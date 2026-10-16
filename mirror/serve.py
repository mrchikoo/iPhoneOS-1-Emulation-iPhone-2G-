#!/usr/bin/env python3
"""Static server with Range, wasm MIME, COOP/COEP. Usage: serve.py [port] [root]"""
import os, re, sys, mimetypes
from http.server import ThreadingHTTPServer, SimpleHTTPRequestHandler
ROOT = os.path.abspath(sys.argv[2]) if len(sys.argv) > 2 else os.path.join(os.path.dirname(os.path.abspath(__file__)))
PORT = int(sys.argv[1]) if len(sys.argv) > 1 else 8007
mimetypes.add_type('application/wasm', '.wasm'); mimetypes.add_type('text/javascript', '.js')
mimetypes.add_type('application/json', '.json'); mimetypes.add_type('image/svg+xml', '.svg')
class H(SimpleHTTPRequestHandler):
    def __init__(self, *a, **k): super().__init__(*a, directory=ROOT, **k)
    def end_headers(self):
        self.send_header('Cross-Origin-Opener-Policy', 'same-origin')
        self.send_header('Cross-Origin-Embedder-Policy', 'require-corp')
        self.send_header('Cross-Origin-Resource-Policy', 'same-origin')
        self.send_header('Cache-Control', 'no-cache')
        self.send_header('Accept-Ranges', 'bytes')
        super().end_headers()
    def send_head(self):
        rng = self.headers.get('Range')
        path = self.translate_path(self.path)
        m = rng and re.match(r'bytes=(\d*)-(\d*)$', rng.strip())
        if not m or not os.path.isfile(path): return super().send_head()
        size = os.path.getsize(path)
        a, b = m.groups()
        if a == '': start, end = max(0, size - int(b)), size - 1
        else: start, end = int(a), int(b) if b else size - 1
        end = min(end, size - 1)
        if start > end or start >= size:
            self.send_response(416); self.send_header('Content-Range', f'bytes */{size}')
            self.send_header('Content-Length', '0'); self.end_headers(); return None
        f = open(path, 'rb'); f.seek(start); self._left = end - start + 1
        self.send_response(206)
        self.send_header('Content-Type', self.guess_type(path))
        self.send_header('Content-Range', f'bytes {start}-{end}/{size}')
        self.send_header('Content-Length', str(self._left)); self.end_headers()
        return f
    def copyfile(self, src, dst):
        left = getattr(self, '_left', None); self._left = None
        try:
            while left is None or left > 0:
                d = src.read(1 << 20 if left is None else min(1 << 20, left))
                if not d: break
                dst.write(d)
                if left is not None: left -= len(d)
        except (BrokenPipeError, ConnectionResetError): pass
    def log_message(self, *a): pass
if __name__ == '__main__':
    ThreadingHTTPServer(('127.0.0.1', PORT), H).serve_forever()
