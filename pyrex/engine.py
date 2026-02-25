"""
Pyrex Engine

Entry points:
- build_file(filepath) → HTML string   (single-file build, used by `pyrex build`)
- build_source(source) → HTML string   (in-memory build, useful for testing)
- build_route(page_filepath, layout_filepath) → HTML string  (used by serve)
- serve(directory)     → starts a multi-route dev HTTP server (FastAPI + uvicorn)
"""

import asyncio
import inspect
import json
import os
import threading
import time
from pathlib import Path

from pyrex.parser.pyx_parser import parse_pyx_file, parse_pyx_source
from pyrex.transpiler.transpiler import Transpiler


# ── Type coercion helper (exported for tests) ────────────────────────────────

def _coerce_type(value, type_str: str):
    """
    Coerce value to the Python type named by type_str.
    Raises ValueError or TypeError on failure (caller returns 422).
    Unknown type annotations are returned as-is.
    """
    TYPE_MAP = {
        "str":   str,
        "float": float,
        "int":   int,
        "bool":  bool,
        "list":  list,
        "dict":  dict,
    }
    target = TYPE_MAP.get(type_str)
    if target is None:
        return value
    return target(value)


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
              + lt._build_all_component_js() + "\n"
              + pt._build_server_action_proxies())
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


# ── Dev server (FastAPI + uvicorn) ───────────────────────────────────────────

def serve(directory: str = "app", port: int = 3000, watch: bool = True,
          startup_hooks=(), shutdown_hooks=()):
    """
    Start a dev server that serves all page.pyx files found under `directory`.

    Route mapping:  directory/page.pyx          → /
                    directory/about/page.pyx     → /about
                    directory/blog/post/page.pyx → /blog/post

    An optional directory/layout.pyx (must contain a @layout component) wraps
    every page. Changing layout.pyx triggers a rebuild of all routes.

    Server actions are registered as POST /__pyrex/actions/<name> endpoints.
    Named JS proxy functions are auto-generated in each served page.

    Each browser tab connected to /__pyrex_reload receives an SSE message on
    any rebuild and reloads automatically.
    """
    from fastapi import FastAPI, Request
    from fastapi.responses import HTMLResponse, JSONResponse, StreamingResponse
    import uvicorn

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

    # SSE state — asyncio.Queue per connected tab; thread→asyncio bridge
    _sse_queues: list[asyncio.Queue] = []
    _sse_lock = threading.Lock()
    _loop_ref: dict = {}   # {"v": running asyncio event loop}

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
        loop = _loop_ref.get("v")
        if not loop:
            return
        with _sse_lock:
            queues = list(_sse_queues)
        for q in queues:
            try:
                loop.call_soon_threadsafe(q.put_nowait, "data: reload\n\n")
            except Exception:
                pass
        if queues:
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

    # Collect all action names + param type info from every page file
    all_action_names: set[str] = set()
    action_param_types: dict[str, list[tuple[str, str]]] = {}
    for filepath in routes.values():
        try:
            mod = parse_pyx_file(filepath)
            for a in mod.server_actions:
                all_action_names.add(a.name)
                action_param_types[a.name] = a.params
        except Exception:
            pass

    # ── FastAPI app ──────────────────────────────────────────────────────────

    app = FastAPI()

    @app.on_event("startup")
    async def _capture_loop():
        _loop_ref["v"] = asyncio.get_running_loop()

    for hook in startup_hooks:
        app.on_event("startup")(hook)
    for hook in shutdown_hooks:
        app.on_event("shutdown")(hook)

    # SSE endpoint
    @app.get("/__pyrex_reload")
    async def sse_reload():
        q: asyncio.Queue = asyncio.Queue()
        with _sse_lock:
            _sse_queues.append(q)

        async def gen():
            try:
                while True:
                    try:
                        msg = await asyncio.wait_for(q.get(), timeout=15)
                        yield msg
                    except asyncio.TimeoutError:
                        yield ": keepalive\n\n"
            finally:
                with _sse_lock:
                    if q in _sse_queues:
                        _sse_queues.remove(q)

        return StreamingResponse(
            gen(),
            media_type="text/event-stream",
            headers={"Cache-Control": "no-cache", "Connection": "keep-alive"},
        )

    # Page GET routes — registered dynamically so the closure captures the right route
    for route in routes:
        def _make_page_handler(r: str):
            async def handler():
                html = route_cache[r]["html"].replace(
                    "</body>", _SSE_SCRIPT + "\n</body>", 1
                )
                return HTMLResponse(html)
            return handler

        app.add_api_route(route, _make_page_handler(route), methods=["GET"])

    # Server action POST routes — one per action name
    for action_name in all_action_names:
        def _make_action_handler(name: str):
            async def handler(request: Request):
                fn = _action_registry.get(name)
                if fn is None:
                    return JSONResponse(
                        {"error": f"No server action registered: {name!r}"},
                        status_code=404,
                    )

                try:
                    body = await request.json()
                except Exception:
                    body = {}

                # Apply type coercion per declared parameter annotations
                param_types = action_param_types.get(name, [])
                if param_types:
                    kwargs: dict = {}
                    for param_name, type_str in param_types:
                        if param_name not in body:
                            continue
                        try:
                            kwargs[param_name] = _coerce_type(body[param_name], type_str)
                        except (ValueError, TypeError) as e:
                            return JSONResponse(
                                {"error": f"Invalid value for {param_name!r}: {e}"},
                                status_code=422,
                            )
                else:
                    # No annotations — pass all body keys as-is
                    sig = inspect.signature(fn)
                    kwargs = {k: v for k, v in body.items() if k in sig.parameters}

                try:
                    if inspect.iscoroutinefunction(fn):
                        result = await fn(**kwargs)
                    else:
                        result = fn(**kwargs)
                    return JSONResponse(result)
                except Exception as e:
                    return JSONResponse(
                        {"error": f"Action {name!r} raised: {e}"},
                        status_code=500,
                    )

            return handler

        app.add_api_route(
            f"/__pyrex/actions/{action_name}",
            _make_action_handler(action_name),
            methods=["POST"],
        )

    # ── File watcher (daemon thread) ─────────────────────────────────────────

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

    # ── Start server ─────────────────────────────────────────────────────────

    route_list = "  ".join(routes.keys())
    print(f"\n  Pyrex dev server")
    print(f"  http://localhost:{port}")
    print(f"  routes: {route_list}")
    if layout_path:
        print(f"  layout: layout.pyx")
    if all_action_names:
        print(f"  actions: {', '.join(sorted(all_action_names))}")
    print(f"  Ctrl+C to stop\n")

    uvicorn.run(app, host="0.0.0.0", port=port, log_level="warning")
