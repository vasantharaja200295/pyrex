"""
Pyrex public API — stubs used by .pyx files at parse time.

The transpiler parses .pyx files as Python AST and never executes them, so
these decorators and hooks only need to exist so that `from pyrex import ...`
doesn't fail when a user runs a .pyx file directly with Python.
"""
import functools


def page(fn):
    """Mark a component as the root page rendered by the transpiler."""
    return fn


def component(fn):
    """Mark a function as a reusable Pyrex component."""
    return fn


def layout(fn):
    """Mark a component as the layout that wraps every page in the app."""
    return fn


def use_state(initial=None):
    """Declare a reactive state variable (transpiled to JS at build time)."""
    return initial, lambda *_: None


def use_effect(fn, deps=None):  # noqa: ARG001
    """Register a side-effect that runs when deps change (transpiled to JS)."""
    _ = fn, deps


def server_action(fn):
    """
    Mark an async function as a Pyrex server action.

    The function is dispatched through the single POST /__pyrex/ endpoint.
    It receives JSON-decoded keyword arguments from the client and must return
    a JSON-serializable value (dict, list, str, int, float, bool, or None).

    A named JS proxy function is auto-generated in every served page so callers
    can invoke the action by name with exact parameter names:

        const data = await add_todo(text);

    Example:
        @server_action
        async def add_todo(text: str):
            _todos.append(text)
            return list(_todos)
    """
    @functools.wraps(fn)
    async def wrapper(*args, **kwargs):
        return await fn(*args, **kwargs)

    wrapper.__pyrex_server_action__ = True
    return wrapper


# ── Pyrex application class ──────────────────────────────────────────────────

class Pyrex:
    """
    Application entry-point for a Pyrex project.

    Usage (main.py):
        from pyrex import Pyrex

        app = Pyrex()

        @app.on_startup
        async def connect():
            ...

        if __name__ == "__main__":
            app.run(directory="app", port=3000)
    """

    def __init__(self):
        self._startup: list = []
        self._shutdown: list = []

    def on_startup(self, fn):
        """Register an async function to run when the server starts."""
        self._startup.append(fn)
        return fn

    def on_shutdown(self, fn):
        """Register an async function to run when the server shuts down."""
        self._shutdown.append(fn)
        return fn

    def run(self, directory: str = "app", port: int = 3000, watch: bool = True,
            debug: bool = True, secret_key: str = ""):
        """Start the Pyrex dev server."""
        from pyrex.engine import serve
        serve(
            directory,
            port=port,
            watch=watch,
            startup_hooks=self._startup,
            shutdown_hooks=self._shutdown,
            debug=debug,
            secret_key=secret_key,
        )
