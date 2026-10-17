#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
常驻翻译服务：低功耗、单句、NPU 优先。

设计（省电优先）：
  - 模型常驻内存，一次编译长期复用（避免反复 init NPU 的开销）。
  - 单句 batch=1，全静态形状，无 KV 缓存重算 —— 单次调用算力极小。
  - 空闲时线程阻塞在 socket 上，CPU 占用接近 0；NPU 仅在推理瞬间上电。
  - 只依赖标准库 + openvino + transformers（tokenizer），不引入 Web 框架，省内存。
  - 支持 Ctrl+C 优雅退出，释放 NPU 上下文。

协议（TCP，localhost 默认 127.0.0.1:8715，换行分隔 JSON）：
  请求: {"text": "要翻译的中文"}\n
  响应: {"ok": true, "src": "...", "dst": "...", "ms": 12.3}\n
  错误: {"ok": false, "error": "..."}\n
"""
import json
import os
import signal
import socket
import sys
import threading
import time

os.environ.setdefault("HF_HUB_DISABLE_SYMLINKS_WARNING", "1")

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

# 自给自足：不管从哪儿被拉起（GUI / 计划任务 / 注册表 Run），环境变量都要对
os.environ.setdefault("HF_HOME", os.path.join(HERE, ".hf-cache"))
if not os.environ.get("HOME"):
    os.environ["HOME"] = os.environ.get("USERPROFILE", HERE)
os.environ.setdefault("OPENVINO_DISABLE_TELEMETRY", "1")
os.environ.pop("HF_ENDPOINT", None)  # hf-mirror 连不上，只用官方源

# 强制 UTF-8 输出。服务是控制台程序，stdout/stderr 被重定向到 service.log；
# 中文 Windows 下默认按 GBK 写，GUI 按 UTF-8 读就会显示一堆乱码。
# 注意：打包成 exe 后 PYTHONIOENCODING 环境变量不一定生效，必须在进程内改。
for _stream in (sys.stdout, sys.stderr):
    try:
        _stream.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

HOST = "127.0.0.1"
PORT = 8715
MODEL_DIR = os.path.join(HERE, "models", "opus-mt-zh-en")
DEVICE = os.environ.get("NPU_TRANSLATE_DEVICE", "NPU")


def _cfg():
    """读取 config.json（GUI 的设置落在这里），读不到就用默认值。"""
    import nput_core
    return nput_core.load_config()


class TranslateServer:
    def __init__(self, host=None, port=None, model_dir=None, device=None):
        cfg = _cfg()
        self.host = host or cfg.get("host", HOST)
        self.port = int(port or cfg.get("port", PORT))
        self.device = (device or cfg.get("device", DEVICE)).upper()
        self._stop = threading.Event()
        self._lock = threading.Lock()   # NPU 单实例串行推理
        self._t0 = time.time()
        self._nreq = 0
        self.devices = self._detect_devices()
        # config 里的 model_dir 可能是相对路径；找不到就自动退到 models\ 下任一可用模型
        from translate import resolve_model_dir
        raw = model_dir or cfg.get("model_dir", MODEL_DIR)
        self._load(resolve_model_dir(raw), self.device)

    @staticmethod
    def _detect_devices():
        try:
            import openvino as ov
            return list(ov.Core().available_devices)
        except Exception:
            return ["CPU"]

    def request_stop(self):
        """HTTP /shutdown 调用，让主循环优雅退出。"""
        self._stop.set()

    def _load(self, model_dir, device):
        from translate import load_static_multi, _seq_tiers
        print(f"[服务] 加载翻译模型到 {device} ...", flush=True)
        t0 = time.time()
        cfg = _cfg()
        self.model_dir = model_dir
        self.compiled, self.tok = None, None
        self.load_error = None
        # 语言方向：模型目录里 meta.json 决定（不同模型的翻译方向不同）
        try:
            import model_manager as mm
            meta = mm.read_meta(model_dir)
        except Exception:
            meta = {"src": "zh", "dst": "en", "label": os.path.basename(model_dir)}
        self.meta = meta
        print(f"[服务] 语言方向: {meta.get('src')} -> {meta.get('dst')}", flush=True)
        try:
            base = (int(cfg.get("enc_seq", 64)), int(cfg.get("dec_seq", 64)))
            tiers = [base] + [t for t in _seq_tiers() if t != base]  # 更大档兜底长句
            self.compiled, self.tok = load_static_multi(model_dir, device, tiers=tiers)
        except Exception as e:
            import traceback
            self.load_error = f"{type(e).__name__}: {e}"
            print("[服务] 模型加载失败:\n" + traceback.format_exc(), flush=True)
        print(f"[服务] 模型就绪，耗时 {time.time()-t0:.2f}s", flush=True)

    def _ensure_ready(self):
        if self.load_error:
            raise RuntimeError("模型未就绪: " + self.load_error)
        if not self.compiled or self.tok is None:
            raise RuntimeError("模型未就绪")

    def handle_text(self, text):
        from translate import translate_static, _pick_tier
        self._ensure_ready()
        tier = _pick_tier(self.compiled, text, self.tok)
        enc_c, dec_c = self.compiled[tier]
        t0 = time.time()
        with self._lock:
            dst, _ = translate_static(enc_c, dec_c, self.tok, text,
                                      model_dir=self.model_dir)
        return {"ok": True, "src": text, "dst": dst, "ms": round((time.time() - t0) * 1000, 1),
                "tier": tier}

    def translate_http(self, text):
        """给 HTTP 端点用：返回 (译文, 毫秒, 档位)。"""
        r = self.handle_text(text)
        self._nreq += 1
        return r["dst"], r["ms"], r["tier"]

    def selftest(self):
        """诊断端点：确认 tokenizer / 模型 / 推理链路是否正常。"""
        out = {"ok": True,
               "load_error": getattr(self, "load_error", None),
               "tok_type": type(getattr(self, "tok", None)).__name__,
               "tok_callable": callable(getattr(self, "tok", None)),
               "model_dir": getattr(self, "model_dir", None),
               "tiers": sorted(getattr(self, "compiled", {}) or {})}
        try:
            n = len(self.tok("测试")["input_ids"])
            out["tok_ok"] = True
            out["tok_len"] = n
        except Exception as e:
            out["tok_ok"] = False
            out["tok_err"] = f"{type(e).__name__}: {e}"
        try:
            dst, ms, tier = self.translate_http("今天天气真好")
            out["infer_ok"] = True
            out["infer_dst"] = dst
            out["infer_ms"] = ms
            out["infer_tier"] = tier
        except Exception as e:
            import traceback
            out["infer_ok"] = False
            out["infer_err"] = f"{type(e).__name__}: {e}"
            out["infer_trace"] = traceback.format_exc()[-800:]
        return out

    def status_http(self):
        """HTTP 版状态（带 kiss 兼容信息）。"""
        st = self.status()
        st["tok_type"] = type(getattr(self, "tok", None)).__name__
        st["model_dir"] = getattr(self, "model_dir", None)
        st["kiss_compat"] = {
            "nonbatch": {"request": ["text", "from", "to"], "response": ["text", "src"]},
            "batch": {"request": ["texts", "from", "to"],
                      "response": ["[{'text','src'}]", "{'translations':[...]}"]},
            "supported": "zh-CN -> en（其他方向原样返回）",
        }
        return st

    def status(self):
        import os as _os
        return {
            "ok": True,
            "cmd": "status",
            "device": self.device,
            "tiers": sorted(self.compiled.keys()) if self.compiled else [],
            "uptime_s": round(time.time() - self._t0, 1),
            "pid": _os.getpid(),
            "requests": self._nreq,
            "src_lang": (getattr(self, "meta", {}) or {}).get("src", "zh"),
            "dst_lang": (getattr(self, "meta", {}) or {}).get("dst", "en"),
            "model": _os.path.basename(getattr(self, "model_dir", "") or ""),
        }

    def _conn_thread(self, conn):
        try:
            conn.settimeout(30)
            f = conn.makefile("rwb")
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    req = json.loads(line.decode("utf-8"))
                    if req.get("cmd") == "status":
                        resp = self.status()
                    elif req.get("cmd") == "ping":
                        resp = {"ok": True, "cmd": "pong"}
                    else:
                        text = req.get("text", "")
                        if not text:
                            resp = {"ok": False, "error": "missing 'text'"}
                        else:
                            resp = self.handle_text(text)
                            self._nreq += 1
                except Exception as e:
                    resp = {"ok": False, "error": f"{type(e).__name__}: {e}"}
                f.write((json.dumps(resp, ensure_ascii=False) + "\n").encode("utf-8"))
                f.flush()
        except Exception:
            pass
        finally:
            try:
                conn.close()
            except Exception:
                pass

    def serve_forever(self):
        srv = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        srv.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        srv.bind((self.host, self.port))
        srv.listen(8)
        srv.settimeout(1.0)
        print(f"[服务] 监听 {self.host}:{self.port}（设备 {self.device}），Ctrl+C 退出", flush=True)

        def _sig(*_):
            self._stop.set()
        signal.signal(signal.SIGINT, _sig)
        signal.signal(signal.SIGTERM, _sig)

        while not self._stop.is_set():
            try:
                conn, addr = srv.accept()
            except socket.timeout:
                continue
            except OSError:
                break
            threading.Thread(target=self._conn_thread, args=(conn,), daemon=True).start()
        try:
            srv.close()
        except Exception:
            pass
        print("\n[服务] 已退出，NPU 上下文已释放。", flush=True)


if __name__ == "__main__":
    import argparse
    ap = argparse.ArgumentParser(description="NPU 常驻翻译服务（TCP + HTTP 双协议）")
    ap.add_argument("--host", default=None, help="默认读 config.json")
    ap.add_argument("--port", type=int, default=None, help="TCP 端口")
    ap.add_argument("--http-port", type=int, default=None, help="HTTP 端口（kiss-translator 用）")
    ap.add_argument("--device", default=None)
    ap.add_argument("--no-http", action="store_true", help="只起 TCP，不起 HTTP")
    args = ap.parse_args()

    cfg = _cfg()
    tcp_port = args.port or int(cfg.get("port", 8715))
    http_port = args.http_port or int(cfg.get("http_port", 8716))

    srv = TranslateServer(host=args.host, port=tcp_port, device=args.device)

    if not args.no_http:
        import http_server
        threading.Thread(target=http_server.start_http,
                         args=(srv, srv.host, http_port), daemon=True).start()

    print(f"[服务] TCP  {srv.host}:{srv.port}（GUI/客户端）", flush=True)
    if not args.no_http:
        print(f"[服务] HTTP {srv.host}:{http_port}（kiss-translator）", flush=True)
    srv.serve_forever()

