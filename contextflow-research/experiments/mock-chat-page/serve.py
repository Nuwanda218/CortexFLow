#!/usr/bin/env python3
"""Static file server for the mock chat page, with SPA fallback.

Serves experiments/mock-chat-page on http://127.0.0.1:8787 .
Any path under /mock/chat/ returns index.html so a hard reload restores the
session route (same behaviour a real SPA needs from its host).

Usage:  python serve.py [--port 8787]
"""
from __future__ import annotations

import argparse
import functools
import http.server
import os
import socketserver
import sys

ROOT = os.path.dirname(os.path.abspath(__file__))


class Handler(http.server.SimpleHTTPRequestHandler):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, directory=ROOT, **kwargs)

    def do_GET(self):  # noqa: N802
        path = self.path.split("?", 1)[0]
        if path == "/" or path.startswith("/mock/chat"):
            self.path = "/index.html"
        return super().do_GET()

    def guess_type(self, path):
        ctype = super().guess_type(path)
        if path.endswith(".js"):
            return "text/javascript"
        return ctype

    def log_message(self, fmt, *args):
        sys.stderr.write("[mock-chat] %s - %s\n" % (self.address_string(), fmt % args))


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--port", type=int, default=8787)
    args = ap.parse_args()
    socketserver.TCPServer.allow_reuse_address = True
    with socketserver.ThreadingTCPServer(("127.0.0.1", args.port), Handler) as httpd:
        print(f"[mock-chat] serving {ROOT} at http://127.0.0.1:{args.port}/mock/chat/session-a", flush=True)
        try:
            httpd.serve_forever()
        except KeyboardInterrupt:
            pass
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
