"""
Pyrex JSX Runtime

Defines the JSX node intermediate representation and helper types.
pyjsx transforms every JSX expression into jsx(tag, props, children) calls.
This module provides that jsx() function plus the reactive helpers
(__pyrex_handler, __pyrex_list_comp, __pyrex_ternary) that the AST
transformer injects into transformed sources.

Context variables allow components to register state and handlers
during execution so the transpiler can later emit Alpine HTML.
"""
from __future__ import annotations
import contextvars


# ── Per-component execution contexts ─────────────────────────────────────────

# Maps var_name → {"initial": value, "setter": setter_name}
_state_ctx: contextvars.ContextVar[dict | None] = contextvars.ContextVar(
    "pyrex_state", default=None
)

# Maps handler_name → {"source": python_source_str}
_handler_ctx: contextvars.ContextVar[dict | None] = contextvars.ContextVar(
    "pyrex_handlers", default=None
)


# ── Node types ────────────────────────────────────────────────────────────────

class JSXNode:
    """
    Intermediate representation of a JSX element.
    Produced by jsx() at component execution time.
    Consumed by the transpiler to emit Alpine HTML.
    """
    __slots__ = ("tag", "props", "children", "is_component")

    def __init__(self, tag, props: dict, children: list):
        self.tag = tag
        self.props = dict(props) if props else {}
        self.children = children
        self.is_component = callable(tag)

    def __repr__(self) -> str:
        return f"JSXNode({self.tag!r}, props={list(self.props)}, children={len(self.children)})"


class StateVar:
    """
    A reactive state variable returned by useState().

    Behaves like its underlying initial value for Python expressions,
    but carries its Alpine variable name so the transpiler can emit
    x-text="name" when it sees this object as a JSX child.
    """
    __slots__ = ("_name", "_value")

    def __init__(self, name: str, value):
        self._name = name
        self._value = value

    # ── Proxy ops so StateVar is transparent in most Python expressions ───
    def __str__(self):  return str(self._value)
    def __repr__(self): return repr(self._value)
    def __bool__(self): return bool(self._value)
    def __int__(self):  return int(self._value) if self._value else 0
    def __float__(self):return float(self._value) if self._value else 0.0
    def __len__(self):  return len(self._value)
    def __iter__(self): return iter(self._value)
    def __eq__(self, other):
        if isinstance(other, StateVar):
            return self._value == other._value
        return self._value == other
    def __hash__(self): return hash(self._value)


class StateSetter:
    """
    The setter returned by useState(). A no-op at server render time;
    only carries its variable name so event handler source strings can
    reference it correctly.
    """
    __slots__ = ("_var_name", "_setter_name")

    def __init__(self, var_name: str, setter_name: str):
        self._var_name = var_name
        self._setter_name = setter_name

    def __call__(self, *args):
        pass   # Alpine handles reactivity at runtime


class PyrexHandler:
    """Wraps an event handler lambda/function with its Python source string."""
    __slots__ = ("fn", "source")

    def __init__(self, fn, source: str):
        self.fn = fn
        self.source = source


class PyrexListComp:
    """
    Wraps a list comprehension so the transpiler can decide:
      - iterable is a state var → emit Alpine <template x-for>
      - iterable is static      → stamp each item as HTML
    """
    __slots__ = ("iterable_name", "item_name", "template_fn", "items")

    def __init__(self, iterable_name: str, item_name: str, template_fn, items):
        self.iterable_name = iterable_name
        self.item_name = item_name
        self.template_fn = template_fn
        self.items = list(items)


class PyrexTernary:
    """
    Wraps a Python ternary whose condition is a state variable,
    so the transpiler can emit two elements with complementary x-show.
    """
    __slots__ = ("cond_expr", "true_node", "false_node")

    def __init__(self, cond_expr: str, true_node, false_node):
        self.cond_expr = cond_expr
        self.true_node = true_node
        self.false_node = false_node


# ── Core jsx() function ───────────────────────────────────────────────────────

def jsx(tag, props, children=None) -> JSXNode:
    """
    Build a JSX tree node.  Called by pyjsx-transformed .px files.

    pyjsx always generates: jsx(tag, props_dict, [child, child, ...])
    Children may themselves be lists (from list comprehensions) — they are
    flattened so the parent node gets a flat children list.
    """
    flat: list = []
    if children:
        for child in children:
            if isinstance(child, list):
                for item in child:
                    if item is not None and item is not False:
                        flat.append(item)
            elif child is not None and child is not False:
                flat.append(child)
    return JSXNode(tag, props or {}, flat)


# ── Reactive helpers injected by the AST transformer ─────────────────────────

def __pyrex_handler(fn, source: str) -> PyrexHandler:
    """Wrap an event handler with its Python source string."""
    return PyrexHandler(fn, source)


def __pyrex_list_comp(
    iterable_name: str,
    item_name: str,
    template_fn,
    items,
) -> "PyrexListComp | list":
    """
    Wrap a list comprehension. At runtime, check if the iterable is a
    registered state variable.

    Reactive  → return PyrexListComp (transpiler emits <template x-for>)
    Static    → expand immediately, return plain list of JSXNodes
    """
    state_reg = _state_ctx.get()
    if state_reg is not None and iterable_name in state_reg:
        return PyrexListComp(iterable_name, item_name, template_fn, list(items))
    return [template_fn(item) for item in items]


def __pyrex_ternary(
    cond_expr: str,
    true_fn,
    false_fn,
    cond_val,
) -> "PyrexTernary | JSXNode | None":
    """
    Wrap a ternary whose test is a simple name. If that name is a state
    variable, return PyrexTernary (emits two elements with x-show).
    Otherwise evaluate and return the correct branch.
    """
    state_reg = _state_ctx.get()
    if state_reg is not None and cond_expr in state_reg:
        return PyrexTernary(cond_expr, true_fn(), false_fn())
    return true_fn() if cond_val else false_fn()


def __pyrex_register_handler(name: str, fn, source: str) -> None:
    """
    Register an async handler function defined inside a component body.
    Called by the AST transformer immediately after each async def.
    No-ops if not inside a component execution context.
    """
    handler_reg = _handler_ctx.get()
    if handler_reg is not None:
        handler_reg[name] = {"source": source, "fn": fn}
