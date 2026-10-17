#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""常驻翻译服务客户端：发一行 JSON，收一行 JSON。
用法:
  python client.py "要翻译的中文"
  python client.py --host 127.0.0.1 --port 8715 --text "..."
"""
import argparse
import json
import socket
import sys


def ask_json(obj, host="127.0.0.1", port=8715, timeout=30.0):
    """发送一个 JSON 对象，返回响应 dict。"""
    s = socket.create_connection((host, port), timeout=timeout)
    try:
        f = s.makefile("rwb")
        f.write((json.dumps(obj, ensure_ascii=False) + "\n").encode("utf-8"))
        f.flush()
        line = f.readline()
        if not line:
            raise RuntimeError("服务端关闭了连接且无响应")
        return json.loads(line.decode("utf-8"))
    finally:
        s.close()


def ask(text, host="127.0.0.1", port=8715, timeout=30.0):
    return ask_json({"text": text}, host, port, timeout)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("text", nargs="?", default=None, help="要翻译的中文（也可用 --text）")
    ap.add_argument("--text", dest="text_opt", default=None, help="要翻译的中文")
    ap.add_argument("--host", default="127.0.0.1")
    ap.add_argument("--port", type=int, default=8715)
    args = ap.parse_args()
    text = args.text_opt or args.text or "今天天气真好，我们一起去公园散步吧。"
    r = ask(text, args.host, args.port)
    if r.get("ok"):
        print(f"{r['src']}\n  -> {r['dst']}  ({r['ms']} ms)")
    else:
        print(f"错误: {r.get('error')}", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
