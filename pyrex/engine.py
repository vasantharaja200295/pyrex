"""
Pyrex Engine

The main entry point for the framework.
- build(filepath) → HTML string
- serve(filepath) → starts a dev HTTP server
"""

import os
import sys
from pathlib import Path

from pyrex.parser.pyx_parser import parse_pyx_file, parse_pyx_source
from pyrex.transpiler.transpiler import Transpiler


def build_file(filepath: str) -> str:
    """
    Parse a .pyx file and transpile it to a complete HTML page.
    Returns the HTML as a string.
    """
    module = parse_pyx_file(filepath)
    transpiler = Transpiler(module)
    return transpiler.transpile()


def build_source(source: str) -> str:
    """
    Parse a .pyx source string and transpile to HTML.
    Useful for testing.
    """
    module = parse_pyx_source(source)
    transpiler = Transpiler(module)
    return transpiler.transpile()


def serve(filepath: str, port: int = 3000, watch: bool = True):
    """
    Start a dev server that serves the transpiled .pyx file.
    Watches for changes and rebuilds automatically.
    """
    from http.server import HTTPServer, BaseHTTPRequestHandler
    import threading
    import time

    filepath = os.path.abspath(filepath)
    cache = {"html": "", "mtime": 0}

    def rebuild():
        try:
            html = build_file(filepath)
            cache["html"] = html
            cache["mtime"] = os.path.getmtime(filepath)
            print(f"  ✓ Built {os.path.basename(filepath)}")
            return True
        except Exception as e:
            cache["html"] = f"<pre style='color:red'>Build error:\n{e}</pre>"
            print(f"  ✗ Build error: {e}")
            return False

    rebuild()

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            if self.path == '/':
                html = cache["html"]
                self.send_response(200)
                self.send_header("Content-Type", "text/html; charset=utf-8")
                self.send_header("Content-Length", str(len(html.encode())))
                self.end_headers()
                self.wfile.write(html.encode())
            else:
                self.send_response(404)
                self.end_headers()

        def log_message(self, format, *args):
            pass  # suppress default logging

    def watcher():
        while True:
            time.sleep(0.5)
            try:
                mtime = os.path.getmtime(filepath)
                if mtime != cache["mtime"]:
                    print(f"\n  File changed, rebuilding...")
                    rebuild()
            except FileNotFoundError:
                pass

    if watch:
        t = threading.Thread(target=watcher, daemon=True)
        t.start()

    server = HTTPServer(("", port), Handler)
    print(f"\n  🔥 Pyrex dev server")
    print(f"  → http://localhost:{port}")
    print(f"  → Watching: {filepath}")
    print(f"  → Press Ctrl+C to stop\n")

    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\n  Server stopped.")
