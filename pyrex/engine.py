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
    Browser tabs connected to /__pyrex_reload receive an SSE message
    on each rebuild and reload automatically.
    """
    from http.server import ThreadingHTTPServer, BaseHTTPRequestHandler
    import threading
    import queue
    import time

    filepath = os.path.abspath(filepath)
    cache = {"html": "", "mtime": 0}

    # SSE client registry — one Queue per open browser tab
    _sse_clients: list[queue.Queue] = []
    _sse_lock = threading.Lock()

    # Injected into every served HTML page (dev-only, never written to disk)
    _SSE_SCRIPT = (
        "<script>new EventSource('/__pyrex_reload')"
        ".onmessage=function(){location.reload()};</script>"
    )

    def rebuild():
        start = time.monotonic()
        try:
            html = build_file(filepath)
            cache["html"] = html
            cache["mtime"] = os.path.getmtime(filepath)
            ms = int((time.monotonic() - start) * 1000)
            print(f"  ✓ Built in {ms}ms")
            # Notify every open SSE connection
            with _sse_lock:
                clients = list(_sse_clients)
            for q in clients:
                try:
                    q.put("data: reload\n\n")
                except Exception:
                    pass
            if clients:
                print(f"  ✓ Browser reloaded")
            return True
        except Exception as e:
            cache["html"] = f"<pre style='color:red'>Build error:\n{e}</pre>"
            print(f"  ✗ Build error: {e}")
            return False

    rebuild()

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            if self.path == '/':
                # Inject SSE client script before </body>
                html = cache["html"].replace("</body>", _SSE_SCRIPT + "\n</body>", 1)
                data = html.encode("utf-8")
                self.send_response(200)
                self.send_header("Content-Type", "text/html; charset=utf-8")
                self.send_header("Content-Length", str(len(data)))
                self.end_headers()
                self.wfile.write(data)

            elif self.path == '/__pyrex_reload':
                # Hold the connection open and stream reload events
                self.send_response(200)
                self.send_header("Content-Type", "text/event-stream")
                self.send_header("Cache-Control", "no-cache")
                self.send_header("Connection", "keep-alive")
                self.end_headers()
                q: queue.Queue = queue.Queue()
                with _sse_lock:
                    _sse_clients.append(q)
                try:
                    while True:
                        try:
                            msg = q.get(timeout=15)  # wake on rebuild signal
                            self.wfile.write(msg.encode("utf-8"))
                            self.wfile.flush()
                        except queue.Empty:
                            # SSE comment keeps the connection alive through proxies
                            self.wfile.write(b": keepalive\n\n")
                            self.wfile.flush()
                except (BrokenPipeError, ConnectionResetError, OSError):
                    pass
                finally:
                    with _sse_lock:
                        if q in _sse_clients:
                            _sse_clients.remove(q)

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

    server = ThreadingHTTPServer(("", port), Handler)
    print(f"\n  🔥 Pyrex dev server")
    print(f"  → http://localhost:{port}")
    print(f"  → Watching: {filepath}")
    print(f"  → Press Ctrl+C to stop\n")

    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\n  Server stopped.")
