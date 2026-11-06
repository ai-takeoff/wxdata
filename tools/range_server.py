"""Minimal static HTTP server with Range support (mimics S3/NOMADS) for tests.

Usage: python tools/range_server.py DIR PORT
"""
import os
import re
import sys
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer


class H(SimpleHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def send_head(self):
        path = self.translate_path(self.path)
        if not os.path.isfile(path):
            self.send_error(404)
            return None
        rng = self.headers.get("Range")
        size = os.path.getsize(path)
        f = open(path, "rb")
        if rng:
            m = re.match(r"bytes=(\d+)-(\d*)", rng)
            a = int(m.group(1)); b = int(m.group(2)) if m.group(2) else size - 1
            b = min(b, size - 1)
            f.seek(a)
            self._remaining = b - a + 1
            self.send_response(206)
            self.send_header("Content-Range", f"bytes {a}-{b}/{size}")
            self.send_header("Content-Length", str(self._remaining))
        else:
            self._remaining = size
            self.send_response(200)
            self.send_header("Content-Length", str(size))
        self.send_header("Content-Type", "application/octet-stream")
        self.end_headers()
        return f

    def copyfile(self, src, dst):
        n = self._remaining
        while n > 0:
            chunk = src.read(min(1 << 20, n))
            if not chunk:
                break
            dst.write(chunk)
            n -= len(chunk)


if __name__ == "__main__":
    os.chdir(sys.argv[1])
    ThreadingHTTPServer(("127.0.0.1", int(sys.argv[2])), H).serve_forever()
