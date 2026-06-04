#!/usr/bin/env python3
"""Bare-minimum test server for Kindle 4 browser debugging.

Try these URLs on your Kindle in order:
  1. http://<ip>:8080/           → simplest possible HTML
  2. http://<ip>:8080/tiny       → even simpler, almost no HTML
  3. http://<ip>:8080/plain      → plain text, no HTML at all
  4. http://<ip>:8080/test.mobi  → direct file download (put a .mobi in same dir)

This helps us figure out WHERE the Kindle 4 browser breaks.
"""

import socket
import sys
from http.server import HTTPServer, BaseHTTPRequestHandler
from pathlib import Path


def get_local_ip() -> str:
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(("8.8.8.8", 80))
        ip = s.getsockname()[0]
        s.close()
        return ip
    except Exception:
        return "127.0.0.1"


class TestHandler(BaseHTTPRequestHandler):
    server_version = "SimpleHTTP/1.0"

    def do_GET(self):
        print(f"\n  >>> REQUEST: {self.path}")
        print(f"      From: {self.address_string()}")
        print(f"      Headers: {dict(self.headers)}")

        if self.path == "/":
            # Test 1: Simple HTML
            body = b"<html><body><h1>It works!</h1><p>Your Kindle can load HTML.</p><p><a href='/plain'>Test plain text</a></p></body></html>"
            self.send_response(200)
            self.send_header("Content-Type", "text/html")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        elif self.path == "/tiny":
            # Test 2: Absolute minimum
            body = b"<h1>Hello Kindle</h1>"
            self.send_response(200)
            self.send_header("Content-Type", "text/html")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        elif self.path == "/plain":
            # Test 3: Not even HTML
            body = b"If you can read this, plain text works."
            self.send_response(200)
            self.send_header("Content-Type", "text/plain")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        elif self.path.endswith(".mobi"):
            # Test 4: Serve any .mobi in current dir
            filename = self.path.lstrip("/")
            filepath = Path(filename)
            if filepath.exists() and filepath.is_file():
                data = filepath.read_bytes()
                self.send_response(200)
                self.send_header("Content-Type", "application/x-mobipocket-ebook")
                self.send_header("Content-Length", str(len(data)))
                self.send_header("Content-Disposition", f'attachment; filename="{filepath.name}"')
                self.end_headers()
                self.wfile.write(data)
                print(f"      Served: {filepath.name} ({len(data)} bytes)")
            else:
                self.send_response(404)
                body = b"File not found"
                self.send_header("Content-Type", "text/plain")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)
        else:
            self.send_response(200)
            body = b"OK"
            self.send_header("Content-Type", "text/plain")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

    def log_message(self, format, *args):
        pass  # we handle logging in do_GET


def main():
    port = int(sys.argv[1]) if len(sys.argv) > 1 else 8080
    ip = get_local_ip()

    server = HTTPServer(("0.0.0.0", port), TestHandler)

    print(f"")
    print(f"  Kindle Browser Test Server")
    print(f"  ──────────────────────────────────────")
    print(f"  Try these on your Kindle 4 browser:")
    print(f"")
    print(f"    1. http://{ip}:{port}/")
    print(f"    2. http://{ip}:{port}/tiny")
    print(f"    3. http://{ip}:{port}/plain")
    print(f"    4. http://{ip}:{port}/yourbook.mobi")
    print(f"  ──────────────────────────────────────")
    print(f"  Watching for requests...\n")

    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\n  Stopped.")
        server.server_close()


if __name__ == "__main__":
    main()
