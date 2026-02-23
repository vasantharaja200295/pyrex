"""
Pyrex public API — stubs used by .pyx files at parse time.

The transpiler parses .pyx files as Python AST and never executes them, so
these decorators and hooks only need to exist so that `from pyrex import ...`
doesn't fail when a user runs a .pyx file directly with Python.
"""


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
    Mark a function as a server action.

    The function runs on the Python server when called from the browser.
    It receives JSON-decoded keyword arguments from the client and must
    return an HTML string that replaces the target element's innerHTML.

    Example:
        @server_action
        def search(query: str):
            results = db.search(query)
            return "".join(f"<li>{r.title}</li>" for r in results)

    Called from JSX event handlers via:
        onclick="pyrex_action('search', {query: this.value}, '#results')"
    """
    return fn
