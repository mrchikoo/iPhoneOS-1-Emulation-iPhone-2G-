#!/usr/bin/env python3
"""Test helper: POST http://127.0.0.1:8201/<name>.png with PNG bytes -> ../shots/<name>.png (CORS enabled).
Used from the page console to save the phone canvas, e.g.
  canvas.toBlob(b => fetch('http://127.0.0.1:8201/springboard.png', {method:'POST', body:b}))
"""
import os, re
from http.server import ThreadingHTTPServer, BaseHTTPRequestHandler

SHOTS = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "shots")


class H(BaseHTTPRequestHandler):
    def cors(self):
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "POST")
        self.send_header("Access-Control-Allow-Headers", "*")
        self.send_header("Cross-Origin-Resource-Policy", "cross-origin")

    def do_OPTIONS(self):
        self.send_response(204); self.cors(); self.end_headers()

    def do_POST(self):
        name = os.path.basename(self.path)
        if not re.fullmatch(r"[\w.-]+\.(png|txt|json)", name):
            self.send_response(400); self.cors(); self.end_headers(); return
        data = self.rfile.read(int(self.headers.get("Content-Length", 0)))
        os.makedirs(SHOTS, exist_ok=True)
        open(os.path.join(SHOTS, name), "wb").write(data)
        self.send_response(200); self.cors(); self.end_headers(); self.wfile.write(b"ok")

    def log_message(self, *a):
        pass


if __name__ == "__main__":
    ThreadingHTTPServer(("127.0.0.1", 8201), H).serve_forever()
