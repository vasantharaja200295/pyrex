"""
Pyrex public API

.px files import from here:
    from pyrex import page, component, layout, useState, useEffect, server_action

All hooks use camelCase to match JSX event prop naming.

Decorators (@page, @component, @layout, @server_action) register the
decorated function into the per-execution module registry maintained by
pyrex.px_loader.  This means the engine never has to walk an AST to find
components — it just reads the registry after exec().
"""
from __future__ import annotations
import asyncio
import functools
import inspect


# ── Registry access (lazy import to avoid circular deps) ─────────────────────

def _get_registry():
    try:
        from pyrex.px_loader import _get_registry as _gr
        return _gr()
    except Exception:
        return None


# ── Decorator API ─────────────────────────────────────────────────────────────

def page(fn=None, *, title: str = "", favicon: str = "", meta: dict | None = None):
    """
    Mark a component as the root page rendered by the engine.

    Usage — bare decorator:
        @page
        def Home(): ...

    Usage — with metadata:
        @page(title="Home", favicon="/favicon.ico", meta={"description": "..."})
        def Home(): ...
    """
    def _decorator(f):
        reg = _get_registry()
        if reg is not None:
            reg["page_fn"] = f
            reg["page_meta"] = {
                "title": title,
                "favicon": favicon,
                "meta": meta or {},
            }
        return f

    if fn is not None:
        # @page with no parentheses
        reg = _get_registry()
        if reg is not None:
            reg["page_fn"] = fn
            reg["page_meta"] = {"title": "", "favicon": "", "meta": {}}
        return fn

    return _decorator


def component(fn):
    """Mark a function as a reusable Pyrex component."""
    reg = _get_registry()
    if reg is not None:
        reg.setdefault("components", {})[fn.__name__] = fn
    return fn


def layout(fn):
    """Mark a component as the layout that wraps every page in the app."""
    reg = _get_registry()
    if reg is not None:
        reg["layout_fn"] = fn
    return fn


def server_action(fn):
    """
    Mark a function as a Pyrex server action.

    The function is dispatched through the single POST /__pyrex/ endpoint.
    A named JS proxy is auto-generated in served pages.

    Both async def and plain def are supported.
    """
    @functools.wraps(fn)
    async def wrapper(*args, **kwargs):
        result = fn(*args, **kwargs)
        if inspect.isawaitable(result):
            return await result
        return result

    wrapper.__pyrex_server_action__ = True
    wrapper.__pyrex_action_fn__ = fn  # keep original for signature inspection

    reg = _get_registry()
    if reg is not None:
        reg.setdefault("server_actions", {})[fn.__name__] = wrapper

    return wrapper


# ── Hooks ─────────────────────────────────────────────────────────────────────

def useState(initial=None, *, _var_name: str = "", _setter_name: str = ""):
    """
    Declare a reactive state variable.

    The AST transformer rewrites every:
        count, setCount = useState(0)
    into:
        count, setCount = useState(0, _var_name="count", _setter_name="setCount")

    At runtime, registers the variable into the per-component context so
    the transpiler can emit the correct x-data attributes.
    """
    from pyrex.jsx_runtime import _state_ctx, StateVar, StateSetter

    state_reg = _state_ctx.get()
    if state_reg is not None and _var_name:
        state_reg[_var_name] = {
            "initial": initial,
            "setter": _setter_name,
        }

    var = StateVar(_var_name, initial) if _var_name else initial
    setter = StateSetter(_var_name, _setter_name) if _var_name else (lambda *_: None)
    return var, setter


def useEffect(fn, deps=None):
    """
    Register a client-side effect.

    At server render time this is a no-op; the transpiler emits an
    x-effect or x-init Alpine directive for the callback.
    (Full useEffect support is roadmap — Phase 2 DX.)
    """
    _ = fn, deps


# ── Application class (unchanged from v0.1) ───────────────────────────────────

class Pyrex:
    """
    Application entry-point.

    Usage (main.py):
        from pyrex import Pyrex

        app = Pyrex()

        @app.on_startup
        async def connect(): ...

        if __name__ == "__main__":
            app.run()
    """

    def __init__(self):
        self._startup: list = []
        self._shutdown: list = []

    def on_startup(self, fn):
        self._startup.append(fn)
        return fn

    def on_shutdown(self, fn):
        self._shutdown.append(fn)
        return fn

    def run(self, directory: str = "app", port: int = 3000, watch: bool = True,
            mode: str = "development", secret_key: str = ""):
        from pyrex.engine import serve
        serve(
            directory,
            port=port,
            watch=watch,
            startup_hooks=self._startup,
            shutdown_hooks=self._shutdown,
            mode=mode,
            secret_key=secret_key,
        )
