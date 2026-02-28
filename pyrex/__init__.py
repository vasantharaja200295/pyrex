"""
Pyrex public API

.px files import from here:
    from pyrex import page, component, layout, useState, useEffect,
                      useRef, js, createStore, useStore, useSelector,
                      server_action

All hooks use camelCase to match JSX convention.

Decorators register into the per-execution module registry maintained by
pyrex.px_loader.  This means the engine never has to walk an AST to find
components — it just reads the registry after exec().
"""
from __future__ import annotations
import asyncio
import functools
import inspect


# ── Module-level config (set by Pyrex.config()) ───────────────────────────────

_pyrex_config: dict = {
    "styling": "css",   # "css" | "tailwind"
    "google_fonts": None,  # list[str] | dict[str, list[int]] | None
}

# Server actions registered from imported files (not page-local).
# When @server_action runs outside of a page load context (i.e. when a
# standalone .py / .px action file is imported), the action is stored here
# so the engine can include it in every page's action registry.
_imported_server_actions: dict[str, callable] = {}


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

    When called outside a page-load context (e.g. from an imported action
    file), the action is stored in the global _imported_server_actions dict
    so the engine can still register and serve it.
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
    else:
        # Imported from a standalone file — register globally
        _imported_server_actions[fn.__name__] = wrapper

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
    Register a client-side effect.  No-op at server render time.
    (Full useEffect support is roadmap — Phase 2 DX.)
    """
    _ = fn, deps


def useRef(_ref_name: str = ""):
    """
    Create a DOM ref.  The AST transformer rewrites:
        inputRef = useRef()
    into:
        inputRef = useRef(_ref_name="inputRef")

    The returned RefVar carries the ref name.  When the transpiler sees
    ref={inputRef} on a JSX element it emits x-ref="inputRef" on that element.
    useRef does NOT add anything to the x-data object.

    Inside js() strings, access the element as $refs.inputRef.
    """
    from pyrex.jsx_runtime import RefVar
    return RefVar(_ref_name)


def js(code: str) -> "JsRaw":
    """
    Raw JavaScript escape hatch.

    Inside a handler body  → emitted verbatim as a JS statement in the
                              generated x-data method.  Alpine magic
                              variables ($refs, $store, $el, etc.) work.

    Inside JSX             → emitted as an inline <script> tag.

    Examples:
        async def handleFocus():
            js("$refs.inputRef.focus()")          # handler → raw JS stmt

        return (
            <div>
                {js("window.scrollTo(0, 0)")}     # JSX → <script> tag
            </div>
        )
    """
    from pyrex.jsx_runtime import JsRaw
    return JsRaw(code)


# ── Store API ─────────────────────────────────────────────────────────────────

def createStore(name: str, initial_state: dict) -> "PyrexStore":
    """
    Create a global reactive store.  Modelled on Zustand.

    The store is shared across all components that import it.  It persists
    for the lifetime of the tab (same as Alpine.store).

    The generated Alpine initialisation code is:
        Alpine.store('name', { ...initial_state })

    and is injected into every page's <head>.

    Example:
        # store/cart.py
        from pyrex import createStore
        cartStore = createStore("cart", {"items": [], "count": 0, "total": 0.0})
    """
    from pyrex.jsx_runtime import PyrexStore, _registered_stores
    store = PyrexStore(name, initial_state)
    _registered_stores[name] = store
    return store


def useStore(store: "PyrexStore", *, _var_name: str = "") -> "StoreProxy":
    """
    Access the full state of a store inside a component.

    The AST transformer rewrites:
        cart = useStore(cartStore)
    into:
        cart = useStore(cartStore, _var_name="cart")

    During component execution, records var_name → store_name in _store_ctx
    so the handler transpiler knows to emit Alpine.store('cart').x for
    handler bodies, and the JSX renderer emits $store.cart.x for templates.

    Example:
        cart = useStore(cartStore)
        return <span>{cart.count} items</span>
        # → <span><span x-text="$store.cart.count"></span> items</span>
    """
    from pyrex.jsx_runtime import StoreProxy, _store_ctx
    store_reg = _store_ctx.get()
    if store_reg is not None and _var_name:
        store_reg[_var_name] = store._name
    return StoreProxy(store._name, store._state)


def useSelector(store: "PyrexStore", selector) -> "StoreAttr":
    """
    Read a specific slice of store state.

    The selector lambda receives a helper whose attribute access records
    the store path.  The result is a StoreAttr whose _alpine_ref property
    is the full $store.name.attr path.

    Example:
        count = useSelector(cartStore, lambda s: s.count)
        return <span>{count}</span>
        # → <span x-text="$store.cart.count"></span>
    """
    from pyrex.jsx_runtime import SelectorHelper
    helper = SelectorHelper(store._name, store._state)
    return selector(helper)


# ── Application class ─────────────────────────────────────────────────────────

class Pyrex:
    """
    Application entry-point.

    Usage (main.py):
        from pyrex import Pyrex

        app = Pyrex()
        app.config(styling="tailwind")

        if __name__ == "__main__":
            app.run()
    """

    def __init__(self):
        self._startup: list = []
        self._shutdown: list = []

    def config(
        self,
        *,
        styling: str = "css",
        google_fonts: "list | dict | None" = None,
    ) -> "Pyrex":
        """
        Configure application-wide settings.

        styling="css"       — default; inject globals.css and route style.css
        styling="tailwind"  — also inject the Tailwind CDN script

        google_fonts        — load fonts from Google Fonts CDN.
            list form:  google_fonts=["Inter", "Roboto Mono"]
                        → uses weights 400 and 700 for each font.
            dict form:  google_fonts={"Inter": [400, 600, 700], "Roboto Mono": [400]}
                        → custom weight list per font.

            For Tailwind (styling="tailwind"), font family CSS variables are
            automatically injected into @theme so you can use font-{name}
            utility classes (e.g. font-inter, font-roboto-mono).
        """
        _pyrex_config["styling"] = styling
        _pyrex_config["google_fonts"] = google_fonts
        return self

    def on_startup(self, fn):
        self._startup.append(fn)
        return fn

    def on_shutdown(self, fn):
        self._shutdown.append(fn)
        return fn

    def run(self, directory: str = "app", port: int | None = None,
            watch: bool = True, mode: str | None = None, secret_key: str = ""):
        import os
        from pyrex.env_loader import load_env_files

        # Determine mode first so the right .env.{mode} file is loaded
        _mode = mode or os.environ.get("PYREX_MODE", "development")

        # Load env files (sets PORT, PYREX_SECRET_KEY, etc. into os.environ)
        load_env_files(root_dir=".", mode=_mode)

        # Read remaining config from env (env files have now been applied)
        _port   = port if port is not None else int(os.environ.get("PORT", "3000"))
        _secret = secret_key or os.environ.get("PYREX_SECRET_KEY", "")

        from pyrex.engine import serve
        serve(
            directory,
            port=_port,
            watch=watch,
            startup_hooks=self._startup,
            shutdown_hooks=self._shutdown,
            mode=_mode,
            secret_key=_secret,
        )
