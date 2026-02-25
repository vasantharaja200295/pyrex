"""
Pyrex Engine

Entry points:
- build_file(filepath) → HTML string   (single-file build, used by `pyrex build`)
- build_source(source) → HTML string   (in-memory build, useful for testing)
- build_route(page_filepath, layout_filepath) → HTML string  (used by serve)
- serve(directory)     → starts a multi-route dev HTTP server (FastAPI + uvicorn)
"""

import asyncio
import hashlib
import inspect
import json
import os
import secrets
import threading
import time
from pathlib import Path

from pyrex.parser.pyx_parser import parse_pyx_file, parse_pyx_source
from pyrex.transpiler.transpiler import Transpiler


# ── Action ID helper ─────────────────────────────────────────────────────────

def _make_action_id(fn_name: str, secret_key: str, debug: bool) -> str:
    """
    Return the action ID used as the "i" key in /__pyrex/ requests.

    dev  (debug=True)  → plain function name
    prod (debug=False) → sha256(fn_name + secret_key)[:16]
    """
    if debug:
        return fn_name
    return hashlib.sha256((fn_name + secret_key).encode()).hexdigest()[:16]


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

def build_route(page_filepath: str, layout_filepath: str | None = None,
                action_ids: dict[str, str] | None = None) -> str:
    """
    Transpile a page.pyx, optionally wrapped in a layout component.

    If layout_filepath is given and contains a @layout component, the page body
    is injected as the {children} prop of the layout before wrapping in HTML.

    action_ids maps each @server_action function name to its dispatch ID
    (plain name in dev, sha256 hash in prod). Passed through to the Transpiler
    so generated JS proxies use the correct ID in the /__pyrex/ request body.
    """
    page_module = parse_pyx_file(page_filepath)
    pt = Transpiler(page_module, action_ids=action_ids)

    if not layout_filepath:
        return pt.transpile()

    layout_module = parse_pyx_file(layout_filepath)
    layout_comp = next((c for c in layout_module.components if c.is_layout), None)
    if not layout_comp:
        return pt.transpile()   # layout.pyx has no @layout component — ignore

    page_root = pt.component_map[page_module.root_component]
    page_body = pt._render_component(page_root, {})

    lt = Transpiler(layout_module, action_ids=action_ids)
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
          startup_hooks=(), shutdown_hooks=(),
          debug: bool = True, secret_key: str = ""):
    """
    Start a dev server that serves all page.pyx files found under `directory`.

    Route mapping:  directory/page.pyx          → /
                    directory/about/page.pyx     → /about
                    directory/blog/post/page.pyx → /blog/post

    An optional directory/layout.pyx (must contain a @layout component) wraps
    every page. Changing layout.pyx triggers a rebuild of all routes.

    All server actions are dispatched through a single POST /__pyrex/ endpoint.
    Named JS proxy functions are auto-generated in each served page.

    debug=True  (default): action IDs are plain function names; CSRF/origin
                           checks are skipped; full error details returned.
    debug=False (production): action IDs are sha256 hashes; CSRF token and
                              Origin header are validated; errors are opaque.
    secret_key is used for hashing action IDs in production. Falls back to the
    PYREX_SECRET_KEY environment variable when not supplied.

    Each browser tab connects to /__pyrex_ws (WebSocket) and receives a "reload"
    message on any rebuild, triggering an automatic page reload.
    """
    from fastapi import FastAPI, Request, WebSocket
    from fastapi.responses import HTMLResponse, JSONResponse
    import uvicorn

    # Resolve secret_key from env if not provided explicitly
    if not secret_key:
        secret_key = os.environ.get("PYREX_SECRET_KEY", "")

    # CSRF token: generated once per server run; empty string in dev mode
    _csrf_token: str = "" if debug else secrets.token_hex(16)

    directory = os.path.abspath(directory)
    routes = _discover_routes(directory)
    layout_path = _find_layout(directory)

    if not routes:
        print(f"  No page.pyx files found under: {directory}")
        return

    # Per-route cache
    route_cache: dict[str, dict] = {route: {"html": "", "mtime": 0.0} for route in routes}
    layout_mtime: dict[str, float] = {"v": 0.0}

    # Server action registry: action_id → Python callable
    # In dev mode the ID is the plain function name; in prod it is a sha256 hash.
    # Each registered action lives in a per-file persistent namespace so that
    # module-level variables (e.g. in-memory stores) survive across requests.
    _action_registry: dict[str, callable] = {}
    _id_to_name: dict[str, str] = {}           # action_id → fn_name
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

        # Exec each action function and register under its computed ID
        for action in module.server_actions:
            try:
                exec(compile(action.body, filepath, "exec"), ns)
                fn = ns.get(action.name)
                if callable(fn):
                    action_id = _make_action_id(action.name, secret_key, debug)
                    _action_registry[action_id] = fn
                    _id_to_name[action_id] = action.name
                    print(f"  [action] {action.name}")
            except Exception as e:
                print(f"  [warn]   could not register action {action.name!r}: {e}")

    # WebSocket queues — one asyncio.Queue per connected browser tab.
    # The watcher thread pushes "reload" into each queue via loop.call_soon_threadsafe.
    _ws_queues: list = []
    _ws_lock = threading.Lock()
    _loop_ref: dict = {}   # {"v": running asyncio event loop}

    # Injected into every served page (dev-only, never written to disk)
    _RELOAD_SCRIPT = (
        "<script>(function(){"
        "function connect(){"
        "var ws=new WebSocket((location.protocol==='https:'?'wss:':'ws:')+'//'+"
        "location.host+'/__pyrex_ws');"
        "ws.onmessage=function(e){if(e.data==='reload')location.reload();};"
        "ws.onclose=function(){setTimeout(connect,1000);};"
        "}connect();})();</script>"
    )

    # Collect all action names + param type info from every page file.
    # Done before _rebuild_route so action_ids is ready for initial builds.
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

    # Build action ID map: fn_name → dispatch ID used in /__pyrex/ "i" key
    action_ids: dict[str, str] = {
        name: _make_action_id(name, secret_key, debug)
        for name in all_action_names
    }

    def _rebuild_route(route: str) -> bool:
        filepath = routes[route]
        start = time.monotonic()
        try:
            html = build_route(filepath, layout_path, action_ids=action_ids)
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

    def _signal_reload():
        loop = _loop_ref.get("v")
        if not loop:
            return
        with _ws_lock:
            queues = list(_ws_queues)
        for q in queues:
            try:
                loop.call_soon_threadsafe(q.put_nowait, "reload")
            except Exception:
                pass
        if queues:
            print("  browser reload signalled")

    # Initial build — all routes
    for route in routes:
        _rebuild_route(route)

    # Seed layout mtime so the watcher doesn't immediately trigger a rebuild
    if layout_path:
        try:
            layout_mtime["v"] = os.path.getmtime(layout_path)
        except FileNotFoundError:
            pass

    # ── FastAPI app ──────────────────────────────────────────────────────────

    # Injected into every served page at response time (never written to disk).
    # The CSRF token script runs first so JS proxies can read __PYREX_TOKEN.
    _TOKEN_SCRIPT = f"<script>window.__PYREX_TOKEN={json.dumps(_csrf_token)};</script>"

    app = FastAPI()

    @app.on_event("startup")
    async def _capture_loop():
        _loop_ref["v"] = asyncio.get_running_loop()

    for hook in startup_hooks:
        app.on_event("startup")(hook)
    for hook in shutdown_hooks:
        app.on_event("shutdown")(hook)

    @app.on_event("shutdown")
    async def _close_ws_on_shutdown():
        """Send shutdown sentinel to all open WebSocket connections."""
        with _ws_lock:
            queues = list(_ws_queues)
        for q in queues:
            try:
                q.put_nowait(None)
            except Exception:
                pass

    # WebSocket endpoint — each browser tab connects here for hot-reload signals
    @app.websocket("/__pyrex_ws")
    async def ws_reload(websocket: WebSocket):
        await websocket.accept()
        q: asyncio.Queue = asyncio.Queue()
        with _ws_lock:
            _ws_queues.append(q)
        try:
            while True:
                msg = await q.get()
                if msg is None:   # shutdown sentinel — exit cleanly
                    break
                try:
                    await websocket.send_text(msg)
                except Exception:
                    break   # browser disconnected
        finally:
            with _ws_lock:
                if q in _ws_queues:
                    _ws_queues.remove(q)

    # Page GET routes — registered dynamically so the closure captures the right route
    for route in routes:
        def _make_page_handler(r: str):
            async def handler():
                html = route_cache[r]["html"].replace(
                    "</body>", _TOKEN_SCRIPT + "\n" + _RELOAD_SCRIPT + "\n</body>", 1
                )
                return HTMLResponse(html)
            return handler

        app.add_api_route(route, _make_page_handler(route), methods=["GET"])

    # Single server action endpoint — all actions dispatched through /__pyrex/
    _PYDANTIC_TYPE_MAP = {
        "str": str, "int": int, "float": float,
        "bool": bool, "list": list, "dict": dict,
    }

    @app.post("/__pyrex/")
    async def action_endpoint(request: Request):
        # 1. Origin check (production only)
        if not debug:
            origin = request.headers.get("origin", "")
            host = request.headers.get("host", "")   # keep port, e.g. "example.com:3000"
            if origin not in (f"http://{host}", f"https://{host}"):
                return JSONResponse({"error": "request failed"}, status_code=403)

        # 2. CSRF check (production only)
        if not debug:
            token = request.headers.get("x-pyrex-token", "")
            if not secrets.compare_digest(token, _csrf_token):
                return JSONResponse({"error": "request failed"}, status_code=403)

        # 3. Parse body
        try:
            body = await request.json()
        except Exception:
            detail = "invalid JSON" if debug else "request failed"
            return JSONResponse({"error": detail}, status_code=400)

        action_id = body.get("i")
        args = body.get("a") or {}

        # 4. Look up action by ID
        fn = _action_registry.get(action_id)
        if fn is None:
            detail = f"No action: {action_id!r}" if debug else "request failed"
            return JSONResponse({"error": detail}, status_code=400)

        # 5. Validate args via Pydantic against declared type annotations
        fn_name = _id_to_name.get(action_id, action_id)
        param_types = action_param_types.get(fn_name, [])
        kwargs: dict = {}
        if param_types:
            from pydantic import create_model, ValidationError
            fields = {
                n: (_PYDANTIC_TYPE_MAP.get(t, str), ...)
                for n, t in param_types
            }
            Model = create_model("ActionArgs", **fields)
            try:
                validated = Model(**args)
                kwargs = validated.model_dump()
            except ValidationError as exc:
                detail = str(exc) if debug else "request failed"
                return JSONResponse({"error": detail}, status_code=422)
        else:
            # No annotations — pass matching body keys as-is
            sig = inspect.signature(fn)
            kwargs = {k: v for k, v in args.items() if k in sig.parameters}

        # 6. Call the action function
        try:
            if inspect.iscoroutinefunction(fn):
                result = await fn(**kwargs)
            else:
                result = fn(**kwargs)
            return JSONResponse(result)
        except Exception as exc:
            if debug:
                import traceback
                return JSONResponse(
                    {"error": str(exc), "traceback": traceback.format_exc()},
                    status_code=500,
                )
            return JSONResponse({"error": "request failed"}, status_code=500)

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
                _signal_reload()

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
        mode = "dev" if debug else "prod"
        print(f"  actions: {', '.join(sorted(all_action_names))}  [{mode}] → POST /__pyrex/")
    print(f"  Ctrl+C to stop\n")

    try:
        uvicorn.run(
            app,
            host="0.0.0.0",
            port=port,
            log_level="warning",
            timeout_graceful_shutdown=2,   # safety net if a connection doesn't close in time
        )
    except KeyboardInterrupt:
        pass
