#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""本地监控服务器（只读，纯标准库）：python monitor.py [--port 8000]

- GET /            -> 仪表盘页面（web/dashboard.html）
- GET /api/state   -> engine_status.json（引擎每拍原子写入）
- GET /api/events  -> events.jsonl（最近 300 条事件）
"""
import argparse
import json
import os
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

ROOT = os.path.dirname(os.path.abspath(__file__))


def read_json(path, default):
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return default


def read_events(path, limit=300):
    events = []
    try:
        with open(path, encoding="utf-8", errors="replace") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    events.append(json.loads(line))
                except Exception:
                    pass
    except Exception:
        pass
    return events[-limit:]


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def _send(self, code, body, ctype="application/json; charset=utf-8"):
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Cache-Control", "no-store")
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        path = self.path.split("?", 1)[0]
        if path == "/api/state":
            state = read_json(os.path.join(ROOT, "engine_status.json"), {})
            body = json.dumps(state, ensure_ascii=False).encode("utf-8")
            self._send(200, body)
        elif path == "/api/events":
            events = read_events(os.path.join(ROOT, "events.jsonl"))
            body = json.dumps({"events": events}, ensure_ascii=False).encode("utf-8")
            self._send(200, body)
        elif path in ("/", "/index.html"):
            try:
                with open(os.path.join(ROOT, "web", "dashboard.html"), "rb") as f:
                    body = f.read()
                self._send(200, body, "text/html; charset=utf-8")
            except Exception:
                self._send(404, b"dashboard not found", "text/plain; charset=utf-8")
        else:
            self._send(404, b"not found", "text/plain; charset=utf-8")


def main():
    global ROOT
    ap = argparse.ArgumentParser(description="TSecBench 引擎本地监控")
    ap.add_argument("--port", type=int, default=8000)
    ap.add_argument("--root", default=ROOT, help="项目根目录（读 engine_status.json/events.jsonl）")
    args = ap.parse_args()
    ROOT = args.root
    srv = ThreadingHTTPServer(("127.0.0.1", args.port), Handler)
    print(f"监控面板: http://127.0.0.1:{args.port}  (root={ROOT})")
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
