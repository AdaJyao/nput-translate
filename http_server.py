#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
HTTP 端点：kiss-translator 走 HTTP，必须提供。
设计要点：
  - 只用标准库 http.server，不引 Flask/FastAPI（省内存，契合省电目标）。
  - 支持 POST /translate（非聚合）、/translate/batch（聚合）、/status、/ping。
  - 支持 OPTIONS 预检与 CORS 头（浏览器插件跨域需要）。
  - GET / 返回说明页，便于人工确认服务活着。

与裸 TCP 协议（client.py / GUI 用）并存，互不影响。
"""
import json
import os
import sys
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

import kiss_api

CORS_HEADERS = {
    "Access-Control-Allow-Origin": "*",
    "Access-Control-Allow-Methods": "POST, GET, OPTIONS",
    "Access-Control-Allow-Headers": "Content-Type, Authorization",
    "Access-Control-Max-Age": "86400",
}


def make_handler(server_ref):
    """工厂：把 TranslateServer 实例注入 handler。"""

    class Handler(BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.1"
        server_version = "NPUTranslate/1.0"

        # ---------- 工具
        def _send_json(self, obj, code=200):
            body = json.dumps(obj, ensure_ascii=False).encode("utf-8")
            self.send_response(code)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            for k, v in CORS_HEADERS.items():
                self.send_header(k, v)
            self.end_headers()
            try:
                self.wfile.write(body)
            except Exception:
                pass

        def _read_body(self):
            try:
                n = int(self.headers.get("Content-Length") or 0)
            except ValueError:
                n = 0
            if n <= 0:
                return {}
            raw = self.rfile.read(n)
            try:
                return json.loads(raw.decode("utf-8"))
            except Exception:
                return {}

        def _query_params(self):
            """解析查询串，重复的 texts 自动收成列表。"""
            from urllib.parse import urlparse, parse_qs
            try:
                qs = urlparse(self.path).query
                parsed = parse_qs(qs, keep_blank_values=True)
            except Exception:
                return {}
            out = {}
            for k, v in parsed.items():
                out[k] = v[0] if len(v) == 1 else v
            return out

        def log_message(self, fmt, *args):
            # 关掉默认的逐请求 stderr 日志（常驻省电、少写盘）
            pass

        # ---------- 路由
        def do_OPTIONS(self):
            self.send_response(204)
            for k, v in CORS_HEADERS.items():
                self.send_header(k, v)
            self.end_headers()

        def do_GET(self):
            path = self.path.split("?")[0].rstrip("/")
            if path in ("", "/status"):
                self._send_json(server_ref.status_http())
            elif path == "/ping":
                self._send_json({"ok": True, "cmd": "pong"})
            elif path == "/devices":
                self._send_json({"ok": True, "devices": server_ref.devices})
            elif path == "/selftest":
                self._send_json(server_ref.selftest())
            elif path == "/langs":
                self._send_json({"langs": kiss_api.KISS_LANGS})
            elif path in ("/translate", "/api/translate"):
                # 支持 GET + 查询参数，便于浏览器地址栏直接测：
                #   /translate?text=今天天气真好&to=en
                q = self._query_params()
                if not q.get("text"):
                    self._send_json(
                        {"ok": False,
                         "error": "GET /translate 需要查询参数，例：/translate?text=今天天气真好&to=en"},
                        400)
                    return
                self._handle_nobatch(q)
            elif path in ("/translate/batch", "/api/translate/batch"):
                q = self._query_params()
                if not q.get("texts"):
                    self._send_json(
                        {"ok": False,
                         "error": "GET /translate/batch 需要 texts，例：/translate/batch?texts=你好&texts=世界&to=en"},
                        400)
                    return
                self._handle_batch(q)
            else:
                self._send_json({"ok": False, "error": f"unknown path: {path}"}, 404)

        def do_POST(self):
            path = self.path.split("?")[0].rstrip("/")
            body = self._read_body()

            if path in ("/translate", "/api/translate"):
                self._handle_nobatch(body)
            elif path in ("/translate/batch", "/api/translate/batch"):
                self._handle_batch(body)
            elif path == "/shutdown":
                self._send_json({"ok": True, "message": "正在退出"})
                # 交给主线程置停止标志
                try:
                    server_ref.request_stop()
                except Exception:
                    pass
            else:
                self._send_json({"ok": False, "error": f"unknown path: {path}"}, 404)

        # ---------- 业务
        def _handle_nobatch(self, body):
            text = body.get("text", "")
            if not text:
                self._send_json({"ok": False, "error": "missing 'text'"}, 400)
                return
            to = body.get("to", "en")
            src_dir, dst_dir = _model_dirs(server_ref)
            src = kiss_api.detect_lang(text)
            if not kiss_api.should_translate(text, to, src_dir, dst_dir):
                # 不支持的方向：原样返回，不报错
                self._send_json(kiss_api.build_response_nobatch(
                    text, text, kiss_api.lang_code(src)))
                return
            try:
                dst, ms, tier = server_ref.translate_http(text)
            except Exception as e:
                import traceback
                tb = traceback.format_exc()
                print("[HTTP] 翻译异常:\n" + tb, flush=True)
                self._send_json({"ok": False,
                                 "error": f"{type(e).__name__}: {e}",
                                 "_trace": tb[-1200:]}, 500)
                return
            resp = kiss_api.build_response_nobatch(text, dst, kiss_api.lang_code(src))
            resp["_ms"] = round(ms, 1)
            resp["_tier"] = tier
            self._send_json(resp)

        def _handle_batch(self, body):
            texts = body.get("texts") or []
            # GET 传来的单个 texts 是字符串，包成列表；POST 规范本身是数组
            if isinstance(texts, str):
                texts = [texts]
            if not isinstance(texts, list) or not texts:
                self._send_json({"ok": False, "error": "missing 'texts'"}, 400)
                return
            to = body.get("to", "en")
            src_dir, dst_dir = _model_dirs(server_ref)
            pairs = []
            for t in texts:
                src = kiss_api.detect_lang(t)
                if not t or not kiss_api.should_translate(t, to, src_dir, dst_dir):
                    pairs.append((t, kiss_api.lang_code(src)))
                    continue
                try:
                    dst, _, _ = server_ref.translate_http(t)
                    pairs.append((dst, kiss_api.lang_code(src)))
                except Exception:
                    pairs.append((t, kiss_api.lang_code(src)))
            self._send_json(kiss_api.build_response_batch(pairs))

    return Handler


def _model_dirs(server_ref):
    """当前模型的语言方向（来自服务加载时的 meta.json）。"""
    meta = getattr(server_ref, "meta", None) or {}
    return meta.get("src", "zh"), meta.get("dst", "en")


def start_http(server_ref, host, port):
    """启动 HTTP 服务（阻塞）。"""
    srv = ThreadingHTTPServer((host, port), make_handler(server_ref))
    srv.daemon_threads = True
    print(f"[HTTP] 监听 http://{host}:{port}（kiss-translator 兼容）", flush=True)
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        srv.server_close()
