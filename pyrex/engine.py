"""
Pyrex Engine

Entry points:
- build_file(filepath) → HTML string   (single-file build, used by `pyrex build`)
- build_source(source) → HTML string   (in-memory build, useful for testing)
- serve(directory)     → starts a multi-route dev HTTP server (FastAPI + uvicorn)

Migration note: this engine drives .px files (pyjsx + Alpine).
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

from pyrex.px_loader import load_px_file, register_import_hook
from pyrex.transpiler.transpiler import PxTranspiler, _minify_js

# Register the .px import hook so cross-file component/action imports work
register_import_hook()

try:
    from pyrex import tui as _tui
except Exception:
    _tui = None


# ── Action ID helper ──────────────────────────────────────────────────────────

def _annotation_type_name(param: inspect.Parameter) -> str:
    """Return the string type name for a pydantic field from a parameter annotation."""
    ann = param.annotation
    if ann is inspect.Parameter.empty:
        return "str"
    if hasattr(ann, "__name__"):
        return ann.__name__
    return "str"


def _make_action_id(fn_name: str, filepath: str, build_id: str) -> str:
    payload = f"{build_id}:{filepath}:{fn_name}"
    return hashlib.sha256(payload.encode()).hexdigest()[:16]


# ── Registry helpers ──────────────────────────────────────────────────────────

def _new_registry() -> dict:
    return {
        "page_fn":        None,
        "page_meta":      {"title": "", "favicon": "", "meta": {}},
        "layout_fn":      None,
        "components":     {},
        "server_actions": {},
    }


def _load_px(filepath: str) -> dict:
    """Load a .px (or .pyx) file and return its populated registry."""
    registry = _new_registry()
    load_px_file(filepath, registry)
    # Merge in any server actions registered from imported files
    from pyrex import _imported_server_actions
    for name, fn in _imported_server_actions.items():
        registry["server_actions"].setdefault(name, fn)
    return registry


# ── Part 3: CSS / Tailwind helpers ────────────────────────────────────────────

def _read_css(path: str | None) -> str:
    """Read a CSS file and return its content, or '' if not found."""
    if path and Path(path).exists():
        return Path(path).read_text(encoding="utf-8")
    return ""


def _read_tailwind_config(project_root: str) -> str:
    """
    Try to read tailwind.config.json or tailwind.config.js from project root.
    Returns a JS expression string (the config object) or ''.
    """
    # Prefer JSON — easy to parse
    json_cfg = Path(project_root) / "tailwind.config.json"
    if json_cfg.exists():
        return json_cfg.read_text(encoding="utf-8").strip()

    # Fall back to .js — try to extract the exported object
    js_cfg = Path(project_root) / "tailwind.config.js"
    if js_cfg.exists():
        import re as _re
        content = js_cfg.read_text(encoding="utf-8").strip()
        # Handle: module.exports = { ... }
        m = _re.search(r"module\.exports\s*=\s*(\{[\s\S]*\})", content)
        if m:
            return m.group(1)
        # Handle: tailwind.config = { ... }  (CDN-style config file)
        m = _re.search(r"tailwind\.config\s*=\s*(\{[\s\S]*\})", content)
        if m:
            return m.group(1)

    return ""


def _make_transpiler(
    registry: dict,
    action_ids: dict[str, str] | None,
    app_dir: str,
    route_dir: str | None = None,
) -> PxTranspiler:
    """Build a PxTranspiler with CSS / Tailwind info for this route."""
    from pyrex import _pyrex_config

    globals_css = _read_css(str(Path(app_dir) / "globals.css"))
    route_css   = _read_css(str(Path(route_dir) / "style.css") if route_dir else None)

    use_tailwind   = _pyrex_config.get("styling") == "tailwind"
    tailwind_cfg   = _read_tailwind_config(str(Path(app_dir).parent)) if use_tailwind else ""

    return PxTranspiler(
        registry,
        action_ids=action_ids,
        globals_css=globals_css,
        route_css=route_css,
        use_tailwind=use_tailwind,
        tailwind_config=tailwind_cfg,
    )


# ── Single-file build (used by `pyrex build`) ─────────────────────────────────

def build_file(filepath: str) -> str:
    """Load a .px file and transpile it to a complete HTML page."""
    registry  = _load_px(filepath)
    app_dir   = str(Path(filepath).parent.parent)   # best-effort guess
    route_dir = str(Path(filepath).parent)
    t = _make_transpiler(registry, None, app_dir, route_dir)
    return t.transpile()


def build_source(source: str) -> str:
    """Transpile a .px source string to HTML. Useful for testing."""
    import tempfile
    with tempfile.NamedTemporaryFile(
        suffix=".px", mode="w", encoding="utf-8", delete=False
    ) as f:
        f.write(source)
        tmp = f.name
    try:
        return build_file(tmp)
    finally:
        os.unlink(tmp)


# ── Route build (used by serve) ───────────────────────────────────────────────

def build_route(
    page_filepath: str,
    layout_filepath: str | None = None,
    action_ids: dict[str, str] | None = None,
    app_dir: str = "",
) -> str:
    """
    Transpile a page.px, optionally wrapped in a layout component.
    action_ids maps each @server_action name to its opaque dispatch ID.
    """
    page_registry = _load_px(page_filepath)
    _app_dir   = app_dir or str(Path(page_filepath).parent.parent)
    _route_dir = str(Path(page_filepath).parent)
    t = _make_transpiler(page_registry, action_ids, _app_dir, _route_dir)

    if not layout_filepath or not Path(layout_filepath).exists():
        return t.transpile()

    layout_registry = _load_px(layout_filepath)
    return t.transpile_with_layout(layout_registry)


# ── Directory scanning ────────────────────────────────────────────────────────

def _discover_routes(app_dir: str) -> dict[str, str]:
    """
    Recursively find all page.px files and map them to URL routes.
    Falls back to page.pyx for backward compatibility.
    """
    app_path = Path(app_dir)
    routes: dict[str, str] = {}

    for pattern in ("page.px", "page.pyx"):
        for px_file in sorted(app_path.rglob(pattern)):
            rel = px_file.parent.relative_to(app_path)
            route = "/" + str(rel).replace("\\", "/") if str(rel) != "." else "/"
            if route not in routes:   # prefer .px over .pyx
                routes[route] = str(px_file.resolve())

    return routes


def _find_layout(app_dir: str) -> str | None:
    """Return the absolute path to app/layout.px (or layout.pyx), or None."""
    for name in ("layout.px", "layout.pyx"):
        p = Path(app_dir) / name
        if p.exists():
            return str(p.resolve())
    return None


# ── Nav fragment extraction ───────────────────────────────────────────────────

def _extract_nav_fragment(html: str) -> dict:
    import re as _re

    title_m = _re.search(r"<title[^>]*>(.*?)</title>", html, _re.IGNORECASE | _re.DOTALL)
    title = title_m.group(1) if title_m else "Pyrex App"

    main_m = _re.search(r"<main[^>]*>(.*?)</main>", html, _re.IGNORECASE | _re.DOTALL)
    if main_m:
        main_html = main_m.group(1)
    else:
        body_m = _re.search(r"<body[^>]*>(.*?)</body>", html, _re.IGNORECASE | _re.DOTALL)
        main_html = body_m.group(1) if body_m else ""

    scripts = []
    for m in _re.finditer(r"<script(?:\s[^>]*)?>([^<]+)</script>", html, _re.DOTALL):
        content = m.group(1).strip()
        if content:
            scripts.append(content)

    return {"html": main_html, "title": title, "scripts": scripts}


# ── Dev server (FastAPI + uvicorn) ────────────────────────────────────────────

def serve(
    directory: str = "app",
    port: int = 3000,
    watch: bool = True,
    startup_hooks=(),
    shutdown_hooks=(),
    mode: str = "development",
    secret_key: str = "",
    on_ready=None,
    force_rebuild=None,
    env_files=None,
):
    from fastapi import FastAPI, Request, WebSocket
    from fastapi.responses import HTMLResponse, JSONResponse
    from starlette.middleware.base import BaseHTTPMiddleware
    import uvicorn

    debug = mode == "development"

    if not secret_key:
        secret_key = os.environ.get("PYREX_SECRET_KEY", "")

    _csrf_token: str = "" if debug else secrets.token_hex(16)
    _build_id: str = secrets.token_hex(16)

    directory = os.path.abspath(directory)
    routes = _discover_routes(directory)
    layout_path = _find_layout(directory)

    if not routes:
        print(f"  No page.px files found under: {directory}")
        return

    route_cache: dict[str, dict] = {r: {"html": "", "mtime": 0.0} for r in routes}
    layout_mtime: dict[str, float] = {"v": 0.0}

    _file_action_ids: dict[str, dict[str, str]] = {}
    _action_registry: dict[str, callable] = {}
    _id_to_name: dict[str, str] = {}
    _action_param_types: dict[str, list] = {}

    def _register_actions(filepath: str) -> None:
        try:
            reg = _load_px(filepath)
        except Exception:
            return
        actions = reg.get("server_actions", {})
        if not actions:
            return

        fids = _file_action_ids.setdefault(filepath, {})
        for name, fn in actions.items():
            if name not in fids:
                fids[name] = _make_action_id(name, filepath, _build_id)
            action_id = fids[name]
            _action_registry[action_id] = fn
            _id_to_name[action_id] = name
            orig = getattr(fn, "__pyrex_action_fn__", fn)
            try:
                sig = inspect.signature(orig)
                _action_param_types[action_id] = [
                    (p, _annotation_type_name(sig.parameters[p]))
                    for p in sig.parameters
                    if p not in ("self", "cls")
                ]
            except Exception:
                _action_param_types[action_id] = []

    _ws_queues: list = []
    _ws_lock = threading.Lock()
    _loop_ref: dict = {}

    def _rebuild_route(route: str) -> bool:
        filepath = routes[route]
        _register_actions(filepath)
        fids = _file_action_ids.get(filepath, {})

        try:
            html = build_route(filepath, layout_path, action_ids=fids, app_dir=directory)
        except Exception as e:
            route_cache[route]["html"] = (
                f"<pre style='color:red'>Build error in {route}:\n{e}</pre>"
            )
            # Record mtime even on failure so the watcher won't retry until
            # the file is actually saved again.
            try:
                route_cache[route]["mtime"] = os.path.getmtime(filepath)
            except FileNotFoundError:
                pass
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

    for route in routes:
        _rebuild_route(route)

    if layout_path:
        try:
            layout_mtime["v"] = os.path.getmtime(layout_path)
        except FileNotFoundError:
            pass

    # ── FastAPI app ───────────────────────────────────────────────────────────

    _PAGE_INJECT = (
        '<meta name="pyrex-dev" content="1">' if debug
        else f'<meta name="pyrex-csrf" content="{_csrf_token}">'
    )

    app = FastAPI()

    from fastapi.responses import Response as _Response
    _pyrexjs_path = Path(__file__).parent / "static" / "pyrex.js"
    _pyrexjs_minified = (
        _minify_js(_pyrexjs_path.read_text(encoding="utf-8"))
        if _pyrexjs_path.exists() else ""
    )

    @app.get("/__pyrex_static/pyrex.js")
    def _serve_pyrexjs():
        if _pyrexjs_minified:
            return _Response(content=_pyrexjs_minified, media_type="application/javascript")
        return _Response(
            content="/* pyrex.js not found */",
            media_type="application/javascript",
            status_code=404,
        )

    # ── Static file directories ───────────────────────────────────────────────
    # app/static/ → /static   (images, fonts, per-app assets)
    # public/     → /public   (project-root assets, favicons, robots.txt, etc.)
    from fastapi.staticfiles import StaticFiles as _StaticFiles
    _static_app = Path(directory) / "static"
    if _static_app.is_dir():
        app.mount("/static", _StaticFiles(directory=str(_static_app)), name="static")
    _public = Path(directory).parent / "public"
    if _public.is_dir():
        app.mount("/public", _StaticFiles(directory=str(_public)), name="public")

    _LOG_SKIP_PREFIXES = ("/__pyrex", "/.well-known", "/favicon", "/static", "/public")

    if _tui:
        class _LogMiddleware(BaseHTTPMiddleware):
            async def dispatch(self, request, call_next):
                start = time.monotonic()
                response = await call_next(request)
                path = request.url.path
                if not any(path.startswith(p) for p in _LOG_SKIP_PREFIXES):
                    _tui.print_request(
                        request.method, path, response.status_code,
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
            with _ws_lock:
                queues = list(_ws_queues)
            for q in queues:
                try:
                    q.put_nowait(None)
                except Exception:
                    pass

        @app.websocket("/__pyrex_ws")
        async def ws_reload(websocket: WebSocket):
            await websocket.accept()
            q: asyncio.Queue = asyncio.Queue()
            with _ws_lock:
                _ws_queues.append(q)
            try:
                while True:
                    msg = await q.get()
                    if msg is None:
                        break
                    try:
                        await websocket.send_text(msg)
                    except Exception:
                        break
            finally:
                with _ws_lock:
                    if q in _ws_queues:
                        _ws_queues.remove(q)

    for route in routes:
        def _make_handler(r: str):
            async def handler(request: Request):
                if request.headers.get("x-pyrex-nav") == "1":
                    return JSONResponse(_extract_nav_fragment(route_cache[r]["html"]))
                html = route_cache[r]["html"].replace(
                    "</body>", _PAGE_INJECT + "\n</body>", 1
                )
                return HTMLResponse(html)
            return handler
        app.add_api_route(route, _make_handler(route), methods=["GET"])

    _PYDANTIC_TYPE_MAP = {
        "str": str, "int": int, "float": float,
        "bool": bool, "list": list, "dict": dict,
    }

    @app.post("/__pyrex/")
    async def action_endpoint(request: Request):
        if not debug:
            origin = request.headers.get("origin", "")
            host = request.headers.get("host", "")
            if origin not in (f"http://{host}", f"https://{host}"):
                return JSONResponse({"error": "request failed"}, status_code=403)

        if not debug:
            token = request.headers.get("x-pyrex-token", "")
            if not secrets.compare_digest(token, _csrf_token):
                return JSONResponse({"error": "request failed"}, status_code=403)

        try:
            body = await request.json()
        except Exception:
            detail = "invalid JSON" if debug else "request failed"
            return JSONResponse({"error": detail}, status_code=400)

        action_id = body.get("i")
        args = body.get("a") or {}

        fn = _action_registry.get(action_id)
        if fn is None:
            detail = f"No action: {action_id!r}" if debug else "request failed"
            return JSONResponse({"error": detail}, status_code=400)

        fn_name = _id_to_name.get(action_id, action_id)
        param_types = _action_param_types.get(action_id, [])
        kwargs: dict = {}
        if param_types:
            from pydantic import create_model, ValidationError
            fields = {n: (_PYDANTIC_TYPE_MAP.get(t, str), ...) for n, t in param_types}
            Model = create_model("ActionArgs", **fields)
            try:
                validated = Model(**args)
                kwargs = validated.model_dump()
            except ValidationError as exc:
                detail = str(exc) if debug else "request failed"
                return JSONResponse({"error": detail}, status_code=422)
        else:
            orig = getattr(fn, "__pyrex_action_fn__", fn)
            sig = inspect.signature(orig)
            kwargs = {k: v for k, v in args.items() if k in sig.parameters}

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
                    {"error": str(exc), "traceback": tb_str}, status_code=500
                )
            return JSONResponse({"error": "request failed"}, status_code=500)

    # ── File watcher ──────────────────────────────────────────────────────────

    def watcher():
        while True:
            time.sleep(0.5)
            rebuilt = []

            if force_rebuild and force_rebuild.is_set():
                force_rebuild.clear()
                if _tui:
                    _tui.print_reload_banner()
                for route in routes:
                    if _rebuild_route(route):
                        rebuilt.append(route)

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

    if on_ready:
        on_ready()
    else:
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
            timeout_graceful_shutdown=2,
        )
    except KeyboardInterrupt:
        pass
