"""
Pyrex Engine

Entry points:
- build_file(filepath) → HTML string   (single-file build, used by `pyrex build`)
- build_source(source) → HTML string   (in-memory build, useful for testing)
- build_route(page_filepath, layout_filepath) → HTML string  (used by serve)
- serve(directory)     → starts a multi-route dev HTTP server
"""

import json
import os
from pathlib import Path

from pyrex.parser.pyx_parser import parse_pyx_file, parse_pyx_source
from pyrex.transpiler.transpiler import Transpiler


# ── Single-file build helpers (unchanged, used by `pyrex build`) ────────────

def build_file(filepath: str) -> str:
    """Parse a .pyx file and transpile it to a complete HTML page."""
    module = parse_pyx_file(filepath)
    transpiler = Transpiler(module)
    return transpiler.transpile()


def build_source(source: str) -> str:
    """Parse a .pyx source string and transpile to HTML. Useful for testing."""
    module = parse_pyx_source(source)
    transpiler = Transpiler(module)
    return transpiler.transpile()


# ── Route build helper (used by serve) ──────────────────────────────────────

def build_route(page_filepath: str, layout_filepath: str | None = None) -> str:
    """
    Transpile a page.pyx, optionally wrapped in a layout component.

    If layout_filepath is given and contains a @layout component, the page body
    is injected as the {children} prop of the layout before wrapping in HTML.
    """
    page_module = parse_pyx_file(page_filepath)
    pt = Transpiler(page_module)

    if not layout_filepath:
        return pt.transpile()

    layout_module = parse_pyx_file(layout_filepath)
    layout_comp = next((c for c in layout_module.components if c.is_layout), None)
    if not layout_comp:
        return pt.transpile()   # layout.pyx has no @layout component — ignore

    page_root = pt.component_map[page_module.root_component]
    page_body = pt._render_component(page_root, {})

    lt = Transpiler(layout_module)
    layout_body = lt._render_component(layout_comp, {"children": page_body})

    all_js = (pt._build_js_runtime() + "\n"
              + pt._build_all_component_js() + "\n"
              + lt._build_all_component_js())
    return pt._wrap_html_page(layout_body, all_js)


# ── Directory scanning ───────────────────────────────────────────────────────

def _discover_routes(app_dir: str) -> dict[str, str]:
    """
    Recursively find all page.pyx files under app_dir and map them to URL routes.

    app/page.pyx           → /
    app/about/page.pyx     → /about
    app/blog/post/page.pyx → /blog/post
    """
    app_path = Path(app_dir)
    routes: dict[str, str] = {}
    for pyx_file in sorted(app_path.rglob("page.pyx")):
        rel = pyx_file.parent.relative_to(app_path)
        route = "/" + str(rel).replace("\\", "/") if str(rel) != "." else "/"
        routes[route] = str(pyx_file.resolve())
    return routes


def _find_layout(app_dir: str) -> str | None:
    """Return the absolute path to app/layout.pyx, or None if it doesn't exist."""
    p = Path(app_dir) / "layout.pyx"
    return str(p.resolve()) if p.exists() else None


# ── Dev server ───────────────────────────────────────────────────────────────

def serve(directory: str, port: int = 3000, watch: bool = True):
    """
    Start a dev server that serves all page.pyx files found under `directory`.

    Route mapping:  directory/page.pyx          → /
                    directory/about/page.pyx     → /about
                    directory/blog/post/page.pyx → /blog/post

    An optional directory/layout.pyx (must contain a @layout component) wraps
    every page. Changing layout.pyx triggers a rebuild of all routes.

    Each browser tab connected to /__pyrex_reload receives an SSE message on
    any rebuild and reloads automatically.
    """
    from http.server import ThreadingHTTPServer, BaseHTTPRequestHandler
    import threading
    import queue
    import time

    directory = os.path.abspath(directory)
    routes = _discover_routes(directory)
    layout_path = _find_layout(directory)

    if not routes:
        print(f"  No page.pyx files found under: {directory}")
        return

    # Per-route cache
    route_cache: dict[str, dict] = {route: {"html": "", "mtime": 0.0} for route in routes}
    layout_mtime: dict[str, float] = {"v": 0.0}

    # Server action registry: action_name → Python callable
    # Each registered action lives in a per-file persistent namespace so that
    # module-level variables (e.g. in-memory stores) survive across requests.
    _action_registry: dict[str, callable] = {}
    _action_namespaces: dict[str, dict] = {}   # filepath → exec namespace

    def _register_actions(filepath: str) -> None:
        """Parse filepath and exec all @server_action functions into the registry."""
        try:
            module = parse_pyx_file(filepath)
        except Exception:
            return
        if not module.server_actions:
            return

        # Reuse existing namespace so in-memory state survives hot-reloads of
        # OTHER files; reset it only when THIS file changes (new code = fresh state).
        ns = _action_namespaces.get(filepath)
        if ns is None:
            ns = {"__builtins__": __builtins__}
            _action_namespaces[filepath] = ns

        # Exec module imports (skip pyrex itself — stubs already available via __init__)
        from pyrex import page, component, layout, use_state, use_effect, server_action
        ns.update({
            "page": page, "component": component, "layout": layout,
            "use_state": use_state, "use_effect": use_effect,
            "server_action": server_action,
        })
        for imp_src in module.imports:
            if "pyrex" in imp_src:
                continue
            try:
                exec(compile(imp_src, filepath, "exec"), ns)
            except Exception:
                pass

        # Exec module-level variable assignments so actions can reference them
        # (e.g. _todos: list[str] = [], DB_PATH = "app.db")
        for var_src in module.module_vars:
            try:
                exec(compile(var_src, filepath, "exec"), ns)
            except Exception:
                pass

        # Exec each action function
        for action in module.server_actions:
            try:
                exec(compile(action.body, filepath, "exec"), ns)
                fn = ns.get(action.name)
                if callable(fn):
                    _action_registry[action.name] = fn
                    print(f"  [action] {action.name}")
            except Exception as e:
                print(f"  [warn]   could not register action {action.name!r}: {e}")

    # SSE client registry — one Queue per open browser tab
    _sse_clients: list[queue.Queue] = []
    _sse_lock = threading.Lock()

    # Injected into every served page (dev-only, never written to disk)
    _SSE_SCRIPT = (
        "<script>new EventSource('/__pyrex_reload')"
        ".onmessage=function(){location.reload()};</script>"
    )

    def _rebuild_route(route: str) -> bool:
        filepath = routes[route]
        start = time.monotonic()
        try:
            html = build_route(filepath, layout_path)
        except Exception as e:
            route_cache[route]["html"] = (
                f"<pre style='color:red'>Build error in {route}:\n{e}</pre>"
            )
            print(f"  [err] {route}: {e}")
            return False
        route_cache[route]["html"] = html
        route_cache[route]["mtime"] = os.path.getmtime(filepath)
        # Reset this file's action namespace so changed code takes effect,
        # then re-register its server actions.
        _action_namespaces.pop(filepath, None)
        _register_actions(filepath)
        ms = int((time.monotonic() - start) * 1000)
        print(f"  [ok]  {route}  ({ms}ms)")
        return True

    def _broadcast_reload():
        with _sse_lock:
            clients = list(_sse_clients)
        for q in clients:
            try:
                q.put("data: reload\n\n")
            except Exception:
                pass
        if clients:
            print("  browser reloaded")

    # Initial build — all routes
    for route in routes:
        _rebuild_route(route)

    # Seed layout mtime so the watcher doesn't immediately trigger a rebuild
    if layout_path:
        try:
            layout_mtime["v"] = os.path.getmtime(layout_path)
        except FileNotFoundError:
            pass

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            if self.path in route_cache:
                html = route_cache[self.path]["html"].replace(
                    "</body>", _SSE_SCRIPT + "\n</body>", 1
                )
                data = html.encode("utf-8")
                self.send_response(200)
                self.send_header("Content-Type", "text/html; charset=utf-8")
                self.send_header("Content-Length", str(len(data)))
                self.end_headers()
                self.wfile.write(data)

            elif self.path == "/__pyrex_reload":
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

        def do_POST(self):
            if not self.path.startswith("/__pyrex_action/"):
                self.send_response(404)
                self.end_headers()
                return

            action_name = self.path[len("/__pyrex_action/"):]
            # Strip query string if present
            if "?" in action_name:
                action_name = action_name[:action_name.index("?")]

            # Parse JSON body
            content_length = int(self.headers.get("Content-Length", 0))
            raw = self.rfile.read(content_length) if content_length > 0 else b"{}"
            try:
                kwargs = json.loads(raw) if raw else {}
            except json.JSONDecodeError:
                kwargs = {}

            fn = _action_registry.get(action_name)
            if fn is None:
                self.send_response(404)
                self.send_header("Content-Type", "text/plain; charset=utf-8")
                self.end_headers()
                self.wfile.write(f"No server action registered: {action_name!r}".encode())
                return

            try:
                result = fn(**kwargs)
                html = str(result) if result is not None else ""
            except Exception as e:
                html = (
                    f'<div style="color:red;padding:.5rem 1rem;font-family:monospace;'
                    f'border:1px solid #fca5a5;border-radius:4px;background:#fef2f2;">'
                    f'<strong>Action error</strong> in <code>{action_name}()</code>: {e}</div>'
                )

            data = html.encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)

        def log_message(self, format, *args):
            pass  # suppress default request logging

    def watcher():
        while True:
            time.sleep(0.5)
            rebuilt = []

            # Check each page file for changes
            for route, filepath in routes.items():
                try:
                    mtime = os.path.getmtime(filepath)
                    if mtime != route_cache[route]["mtime"]:
                        print(f"\n  File changed ({route}), rebuilding...")
                        if _rebuild_route(route):
                            rebuilt.append(route)
                except FileNotFoundError:
                    pass

            # Check layout — a change requires rebuilding every route
            if layout_path:
                try:
                    mtime = os.path.getmtime(layout_path)
                    if mtime != layout_mtime["v"]:
                        layout_mtime["v"] = mtime
                        print("\n  layout.pyx changed, rebuilding all routes...")
                        for route in routes:
                            if _rebuild_route(route):
                                rebuilt.append(route)
                except FileNotFoundError:
                    pass

            if rebuilt:
                _broadcast_reload()

    if watch:
        t = threading.Thread(target=watcher, daemon=True)
        t.start()

    class _Server(ThreadingHTTPServer):
        def handle_error(self, request, client_address):
            # Silently ignore aborted connections — common on Windows when
            # a browser tab is closed or keep-alive sockets are recycled.
            import sys
            if isinstance(sys.exc_info()[1], ConnectionAbortedError):
                return
            super().handle_error(request, client_address)

    server = _Server(("", port), Handler)

    route_list = "  ".join(routes.keys())
    print(f"\n  Pyrex dev server")
    print(f"  http://localhost:{port}")
    print(f"  routes: {route_list}")
    if layout_path:
        print(f"  layout: layout.pyx")
    print(f"  Ctrl+C to stop\n")

    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\n  Server stopped.")
