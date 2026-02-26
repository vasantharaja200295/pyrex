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

try:
    from pyrex import tui as _tui
except Exception:
    _tui = None


# ── Action ID helper ─────────────────────────────────────────────────────────

def _make_action_id(fn_name: str, filepath: str, build_id: str) -> str:
    """
    Return an opaque, unique ID for a server action.

    Always hashes  build_id + filepath + fn_name  via SHA-256, returning the
    first 16 hex characters.

    The build_id is generated fresh per server run (secrets.token_hex(16)) so
    action IDs rotate on every restart, making stale browser-cached pages
    unable to call actions after a redeploy.

    Using the filepath ensures that actions with the same name in different
    files receive different IDs, preventing collisions across routes.
    """
    payload = f"{build_id}:{filepath}:{fn_name}"
    return hashlib.sha256(payload.encode()).hexdigest()[:16]


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

    all_scripts = (pt._build_alpine_script() + "\n"
                   + pt._build_all_component_factories() + "\n"
                   + lt._build_all_component_factories() + "\n"
                   + pt._build_server_action_proxies())
    return pt._wrap_html_page(layout_body, all_scripts)


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
          mode: str = "development",
          secret_key: str = "",
          on_ready=None,
          force_rebuild=None,
          env_files=None):
    """
    Start a dev server that serves all page.pyx files found under `directory`.

    Route mapping:  directory/page.pyx          → /
                    directory/about/page.pyx     → /about
                    directory/blog/post/page.pyx → /blog/post

    mode="development" (default): debug-friendly — full error details, no
                                   CSRF/origin enforcement.  Action IDs are
                                   still opaque hashes (same as production).
    mode="production":             CSRF token + Origin header validation,
                                   opaque error responses.

    on_ready is an optional callable invoked after the initial build is complete
    and just before uvicorn starts. Use it to print the "ready" banner and start
    key-binding listeners from the CLI layer.

    force_rebuild is an optional threading.Event; when set by the caller (e.g.
    the 'r' key handler) the watcher triggers a full rebuild on the next tick.
    """
    from fastapi import FastAPI, Request, WebSocket
    from fastapi.responses import HTMLResponse, JSONResponse
    from starlette.middleware.base import BaseHTTPMiddleware
    import uvicorn

    debug = (mode == "development")

    # Resolve secret_key from env if not provided explicitly
    if not secret_key:
        secret_key = os.environ.get("PYREX_SECRET_KEY", "")

    # CSRF token: generated once per server run; empty string in dev mode
    _csrf_token: str = "" if debug else secrets.token_hex(16)

    # Per-run build ID — incorporated into every action ID so they are opaque
    # and rotate on every server restart.  Mirrors Next.js's per-build action
    # ID scheme: IDs are unpredictable, never expose function names, and become
    # invalid after a redeploy (browser reloads get fresh IDs from the new page).
    _build_id: str = secrets.token_hex(16)

    directory = os.path.abspath(directory)
    routes = _discover_routes(directory)
    layout_path = _find_layout(directory)

    if not routes:
        print(f"  No page.pyx files found under: {directory}")
        return

    # Per-route cache
    route_cache: dict[str, dict] = {route: {"html": "", "mtime": 0.0} for route in routes}
    layout_mtime: dict[str, float] = {"v": 0.0}

    # Per-file action ID map: filepath -> {fn_name: id}
    _file_action_ids: dict[str, dict[str, str]] = {}
    _action_registry: dict[str, callable] = {}
    _id_to_name: dict[str, str] = {}                      # action_id → fn_name
    _action_param_types: dict[str, list] = {}             # action_id → [(param, type), …]
    _action_namespaces: dict[str, dict] = {}              # filepath → exec namespace

    def _register_actions(filepath: str) -> None:
        """
        Parse filepath and exec all @server_action functions into the registry.

        Action IDs are derived from (build_id, filepath, fn_name) via SHA-256 so
        they are always opaque — function names are never exposed to the client —
        and rotate with every server restart.

        _action_param_types is keyed by action_id (not by fn_name) so that two
        files with identically-named actions receive distinct type-validation rules.
        """
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

        # Compute the stable ID map for this file (IDs are fixed for the server run)
        fids = _file_action_ids.setdefault(filepath, {})

        # Exec each action function and register under its opaque ID
        for action in module.server_actions:
            # Compute (or reuse) the opaque action ID
            if action.name not in fids:
                fids[action.name] = _make_action_id(action.name, filepath, _build_id)
            action_id = fids[action.name]

            try:
                exec(compile(action.body, filepath, "exec"), ns)
                fn = ns.get(action.name)
                if callable(fn):
                    _action_registry[action_id] = fn
                    _id_to_name[action_id] = action.name
                    # Key param types by action_id to avoid fn-name collisions across files
                    _action_param_types[action_id] = action.params
            except Exception as e:
                if _tui:
                    _tui.print_error(f"Could not register action {action.name!r}: {e}")
                else:
                    print(f"  [warn]   could not register action {action.name!r}: {e}")

    # WebSocket queues — one asyncio.Queue per connected browser tab.
    # The watcher thread pushes "reload" into each queue via loop.call_soon_threadsafe.
    _ws_queues: list = []
    _ws_lock = threading.Lock()
    _loop_ref: dict = {}   # {"v": running asyncio event loop}

    # Injected into every served page in dev mode only (never written to disk)
    _RELOAD_SCRIPT = (
        "<script>(function(){"
        "function connect(){"
        "var ws=new WebSocket((location.protocol==='https:'?'wss:':'ws:')+'//'+"
        "location.host+'/__pyrex_ws');"
        "ws.onmessage=function(e){if(e.data==='reload')location.reload();};"
        "ws.onclose=function(){setTimeout(connect,1000);};"
        "}connect();})();</script>"
    )

    def _rebuild_route(route: str) -> bool:
        filepath = routes[route]
        
        # Reset this file's action namespace so changed code takes effect,
        # then re-register its server actions. This populates _file_action_ids[filepath].
        _action_namespaces.pop(filepath, None)
        _register_actions(filepath)
        
        # Get the action IDs specific to this file
        fids = _file_action_ids.get(filepath, {})
        
        start = time.monotonic()
        try:
            html = build_route(filepath, layout_path, action_ids=fids)
        except Exception as e:
            route_cache[route]["html"] = (
                f"<pre style='color:red'>Build error in {route}:\n{e}</pre>"
            )
            if _tui:
                _tui.print_error(f"Build error in {route}: {e}")
            else:
                print(f"  [err] {route}: {e}")
            return False
        route_cache[route]["html"] = html
        route_cache[route]["mtime"] = os.path.getmtime(filepath)
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

    # _PAGE_INJECT is appended just before </body> at response time (never cached).
    #
    # Production: inject the CSRF token so JS action proxies can read it and echo
    #             it back via the x-pyrex-token header.  The token is a random
    #             32-char hex string generated once per server run.  It MUST be
    #             readable by same-origin JS — that is how CSRF tokens work.
    #             Cross-origin scripts cannot access it due to the browser's
    #             same-origin policy, which is what prevents CSRF attacks.
    #
    # Development: CSRF validation is disabled entirely, so there is nothing to
    #             inject.  The JS proxy uses  window.__PYREX_TOKEN || ''  so it
    #             degrades gracefully when the variable is not defined.
    #             Hot-reload WebSocket script is injected instead.
    if debug:
        _PAGE_INJECT = _RELOAD_SCRIPT
    else:
        _PAGE_INJECT = f"<script>window.__PYREX_TOKEN={json.dumps(_csrf_token)};</script>"

    app = FastAPI()

    # Request logging middleware.
    # Skips Pyrex-internal paths and browser/OS-injected system requests that
    # are unrelated to the application (Chrome DevTools probes, favicons, etc.).
    _LOG_SKIP_PREFIXES = ("/__pyrex", "/.well-known", "/favicon")

    if _tui:
        class _LogMiddleware(BaseHTTPMiddleware):
            async def dispatch(self, request, call_next):
                start = time.monotonic()
                response = await call_next(request)
                path = request.url.path
                if not any(path.startswith(p) for p in _LOG_SKIP_PREFIXES):
                    _tui.print_request(
                        request.method,
                        path,
                        response.status_code,
                        (time.monotonic() - start) * 1000,
                    )
                return response

        app.add_middleware(_LogMiddleware)

    if debug:
        @app.on_event("startup")
        async def _capture_loop():
            _loop_ref["v"] = asyncio.get_running_loop()

    for hook in startup_hooks:
        app.on_event("startup")(hook)
    for hook in shutdown_hooks:
        app.on_event("shutdown")(hook)

    if debug:
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
                    "</body>", _PAGE_INJECT + "\n</body>", 1
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

        # 5. Validate args via Pydantic against declared type annotations.
        #    _action_param_types is keyed by opaque action_id (not fn_name) so two
        #    files with identically-named actions each get the right type rules.
        fn_name = _id_to_name.get(action_id, action_id)
        param_types = _action_param_types.get(action_id, [])
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
        _t0 = time.monotonic()
        try:
            if inspect.iscoroutinefunction(fn):
                result = await fn(**kwargs)
            else:
                result = fn(**kwargs)
            if _tui:
                _tui.print_action_call(fn_name, (time.monotonic() - _t0) * 1000, ok=True)
            return JSONResponse(result)
        except Exception as exc:
            dur_ms = (time.monotonic() - _t0) * 1000
            if _tui:
                _tui.print_action_call(fn_name, dur_ms, ok=False)
            if debug:
                import traceback as _tb
                tb_str = _tb.format_exc()
                if _tui:
                    _tui.print_error(str(exc), tb_str)
                return JSONResponse(
                    {"error": str(exc), "traceback": tb_str},
                    status_code=500,
                )
            return JSONResponse({"error": "request failed"}, status_code=500)

    # ── File watcher (daemon thread) ─────────────────────────────────────────

    def watcher():
        while True:
            time.sleep(0.5)
            rebuilt = []

            # Manual reload triggered by 'r' key (force_rebuild event)
            if force_rebuild and force_rebuild.is_set():
                force_rebuild.clear()
                if _tui:
                    _tui.print_reload_banner()
                for route in routes:
                    if _rebuild_route(route):
                        rebuilt.append(route)

            # Check each page file for changes
            for route, filepath in routes.items():
                try:
                    mtime = os.path.getmtime(filepath)
                    if mtime != route_cache[route]["mtime"]:
                        if _tui:
                            _tui.print_reload_banner()
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
                        if _tui:
                            _tui.print_reload_banner()
                        for route in routes:
                            if _rebuild_route(route):
                                rebuilt.append(route)
                except FileNotFoundError:
                    pass

            if rebuilt:
                _signal_reload()
                if _tui:
                    _tui.print_reload_done(rebuilt)

    if watch:
        t = threading.Thread(target=watcher, daemon=True)
        t.start()

    # ── Start server ─────────────────────────────────────────────────────────

    if on_ready:
        on_ready()
    else:
        # Fallback when called without a CLI on_ready (e.g. via Pyrex.run())
        route_list = "  ".join(routes.keys())
        print(f"\n  Pyrex dev server — {mode}")
        print(f"  http://localhost:{port}")
        print(f"  routes: {route_list}")
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
