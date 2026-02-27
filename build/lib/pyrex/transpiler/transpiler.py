"""
Pyrex Transpiler — .px → Alpine HTML

Takes a module registry produced by px_loader.load_px_file() and renders
the page to a complete HTML document with inline Alpine.js directives.

Pipeline for each component execution:
  1. Set _state_ctx / _handler_ctx context variables
  2. Call the component function (which calls jsx() to build the tree)
  3. Read the state & handler registries that were populated during execution
  4. Build the inline x-data string and inject onto the root element
  5. Walk the JSX tree → HTML, converting:
       StateVar children      → <span x-text="name">
       PyrexHandler props     → @event="…"
       PyrexListComp children → <template x-for>
       PyrexTernary children  → two elements with complementary x-show
       className / htmlFor    → class / for
"""

from __future__ import annotations
import ast
import inspect
import json
import re
from typing import Any

from pyrex.jsx_runtime import (
    JSXNode, StateVar, StateSetter, PyrexHandler,
    PyrexListComp, PyrexTernary,
    _state_ctx, _handler_ctx,
)


# ── Self-closing HTML void elements ──────────────────────────────────────────

_VOID_ELEMENTS = frozenset({
    "area", "base", "br", "col", "embed", "hr", "img", "input",
    "link", "meta", "param", "source", "track", "wbr",
})


# ── Event prop → Alpine directive mapping ─────────────────────────────────────

_EVENT_MAP: dict[str, str] = {
    "onClick":      "@click",
    "onChange":     "@input",
    "onInput":      "@input",
    "onSubmit":     "@submit",
    "onKeyDown":    "@keydown",
    "onKeyUp":      "@keyup",
    "onFocus":      "@focus",
    "onBlur":       "@blur",
    "onMouseEnter": "@mouseenter",
    "onMouseLeave": "@mouseleave",
}


def _jsx_event_to_alpine(key: str) -> str:
    if key in _EVENT_MAP:
        return _EVENT_MAP[key]
    return "@" + key[2:].lower()


# ── Main transpiler ───────────────────────────────────────────────────────────

class PxTranspiler:
    """
    Renders a .px module registry into a complete HTML page.

    Usage:
        registry = {}
        load_px_file(filepath, registry)
        t = PxTranspiler(registry, action_ids={...})
        html = t.transpile()
    """

    def __init__(self, registry: dict, action_ids: dict[str, str] | None = None):
        self.registry = registry
        self.action_ids = action_ids or {}

    # ── Public entry point ────────────────────────────────────────────────────

    def transpile(self) -> str:
        page_fn = self.registry.get("page_fn")
        if page_fn is None:
            raise ValueError(
                "No @page component found. Decorate a function with @page."
            )
        page_meta = self.registry.get("page_meta", {})
        body_html = self._render_component(page_fn, {})
        proxy_js = self._build_server_action_proxies()
        return self._wrap_html_page(body_html, proxy_js, page_meta)

    def transpile_with_layout(
        self,
        layout_registry: dict,
        action_ids: dict[str, str] | None = None,
    ) -> str:
        """Render the page and wrap it in the layout component."""
        page_fn = self.registry.get("page_fn")
        if page_fn is None:
            raise ValueError("No @page component found.")

        layout_fn = layout_registry.get("layout_fn")
        page_meta = self.registry.get("page_meta", {})

        page_html = self._render_component(page_fn, {})

        if layout_fn is None:
            proxy_js = self._build_server_action_proxies()
            return self._wrap_html_page(page_html, proxy_js, page_meta)

        # Inject page HTML as children of the layout
        # Children here is a pre-rendered HTML string injected via a raw node
        layout_html = self._render_component(layout_fn, {"children": [_RawHTML(page_html)]})
        proxy_js = self._build_server_action_proxies()
        return self._wrap_html_page(layout_html, proxy_js, page_meta)

    # ── Component rendering ───────────────────────────────────────────────────

    def _render_component(self, fn, props: dict) -> str:
        """
        Execute a component function in a fresh state/handler context,
        collect the resulting JSX tree, and render it to HTML.
        """
        state_reg: dict = {}
        handler_reg: dict = {}

        tok_s = _state_ctx.set(state_reg)
        tok_h = _handler_ctx.set(handler_reg)
        try:
            jsx_tree = fn(**props)
        except Exception as exc:
            _state_ctx.reset(tok_s)
            _handler_ctx.reset(tok_h)
            return f'<div style="color:red;padding:1rem;">Component error in {getattr(fn,"__name__","?")}: {exc}</div>'
        finally:
            _state_ctx.reset(tok_s)
            _handler_ctx.reset(tok_h)

        # Inject x-data on the root element if this component has state/handlers
        if (state_reg or handler_reg) and isinstance(jsx_tree, JSXNode):
            jsx_tree.props["x-data"] = self._build_x_data_str(state_reg, handler_reg)

        return self._node_to_html(jsx_tree)

    # ── x-data generation ─────────────────────────────────────────────────────

    def _build_x_data_str(self, state_reg: dict, handler_reg: dict) -> str:
        """Build an inline, minified Alpine x-data object string."""
        parts: list[str] = []

        for var_name, info in state_reg.items():
            initial_js = _py_value_to_js(info["initial"])
            setter = info["setter"]
            parts.append(f"{var_name}:{initial_js}")
            if setter:
                parts.append(f"{setter}(v){{this.{var_name}=v}}")

        all_reactive = (
            set(state_reg.keys())
            | {info["setter"] for info in state_reg.values() if info.get("setter")}
        )

        for fn_name, info in handler_reg.items():
            try:
                method_js = _transpile_async_func_to_method(
                    info["source"], reactive_names=all_reactive
                )
                parts.append(method_js)
            except Exception as exc:
                parts.append(f"/* {fn_name}: {exc} */")

        inner = ",".join(parts)
        return "{" + inner + "}"

    # ── JSX tree → HTML ───────────────────────────────────────────────────────

    def _node_to_html(self, node: Any) -> str:
        if node is None or node is False:
            return ""

        if isinstance(node, _RawHTML):
            return node.html

        # StateVar → reactive text span
        if isinstance(node, StateVar):
            return f'<span x-text="{node._name}"></span>'

        if isinstance(node, StateSetter):
            return ""

        if isinstance(node, (str, int, float)):
            return _escape_html(str(node))

        if isinstance(node, bool):
            return ""

        if isinstance(node, PyrexTernary):
            true_html  = self._render_with_show(node.true_node,  node.cond_expr)
            false_html = self._render_with_show(node.false_node, f"!{node.cond_expr}")
            return true_html + false_html

        if isinstance(node, PyrexListComp):
            return self._render_list_comp(node)

        if isinstance(node, list):
            return "".join(self._node_to_html(child) for child in node)

        if not isinstance(node, JSXNode):
            return _escape_html(str(node))

        # ── JSXNode ───────────────────────────────────────────────────────────
        if node.is_component:
            return self._render_component_node(node)

        tag = node.tag
        attrs_str = self._build_attrs(node.props)

        if tag in _VOID_ELEMENTS:
            return f"<{tag}{attrs_str} />"

        children_html = "".join(self._node_to_html(child) for child in node.children)
        return f"<{tag}{attrs_str}>{children_html}</{tag}>"

    def _render_with_show(self, node: Any, cond_expr: str) -> str:
        """Inject x-show into a JSXNode's root, then render."""
        if isinstance(node, JSXNode):
            node.props["x-show"] = cond_expr
        return self._node_to_html(node)

    def _render_component_node(self, node: JSXNode) -> str:
        fn = node.tag
        props = dict(node.props)
        if node.children:
            props["children"] = node.children
        return self._render_component(fn, props)

    def _render_list_comp(self, comp: PyrexListComp) -> str:
        """Render a reactive list as <template x-for>."""
        item_state = StateVar(comp.item_name, comp.items[0] if comp.items else "")
        try:
            template_node = comp.template_fn(item_state)
        except Exception:
            return ""

        if isinstance(template_node, JSXNode):
            template_node.props.pop("key", None)

        template_html = self._node_to_html(template_node)
        return (
            f'<template x-for="{comp.item_name} in {comp.iterable_name}">'
            f"{template_html}</template>"
        )

    # ── Attribute rendering ───────────────────────────────────────────────────

    def _build_attrs(self, props: dict) -> str:
        parts: list[str] = []

        for key, val in props.items():
            # Pass-through Alpine directives
            if key.startswith("x-") or key.startswith("@"):
                parts.append(f'{key}="{_escape_attr(str(val))}"')
                continue

            # camelCase event props → @event
            if key.startswith("on") and len(key) > 2 and key[2].isupper():
                alpine = _jsx_event_to_alpine(key)
                js = self._handler_to_js(val)
                parts.append(f'{alpine}="{_escape_attr(js)}"')
                continue

            # Lowercase native events
            if key.startswith("on") and len(key) > 2 and not key[2].isupper():
                js = self._handler_to_js(val) if isinstance(val, (PyrexHandler,)) else str(val)
                parts.append(f'{key}="{_escape_attr(js)}"')
                continue

            if key == "className":
                key = "class"
            elif key == "htmlFor":
                key = "for"

            if val is True:
                parts.append(key)
                continue
            if val is False or val is None:
                continue

            if isinstance(val, StateVar):
                parts.append(f':{key}="{val._name}"')
                continue

            parts.append(f'{key}="{_escape_attr(str(val))}"')

        return (" " + " ".join(parts)) if parts else ""

    def _handler_to_js(self, val) -> str:
        if isinstance(val, PyrexHandler):
            return _lambda_source_to_js(val.source)
        if callable(val):
            name = getattr(val, "__name__", None)
            if name and name != "<lambda>":
                return name + "()"
            return ""
        return str(val) if val else ""

    # ── Server action proxies ─────────────────────────────────────────────────

    def _build_server_action_proxies(self) -> str:
        actions = self.registry.get("server_actions", {})
        if not actions:
            return ""

        fns: list[str] = []
        for name, fn in actions.items():
            action_id = self.action_ids.get(name, name)
            orig_fn = getattr(fn, "__pyrex_action_fn__", fn)
            try:
                sig = inspect.signature(orig_fn)
                params = [p for p in sig.parameters if p not in ("self", "cls")]
            except Exception:
                params = []
            param_list = ", ".join(params)
            args_obj = ("{ " + ", ".join(params) + " }") if params else "{}"
            fns.append(
                f"async function {name}({param_list}){{"
                f"return await window.__pyrex.call({json.dumps(action_id)},{args_obj});}}"
            )

        return f"\n<script>{_minify_js(''.join(fns))}</script>"

    # ── HTML page wrapper ─────────────────────────────────────────────────────

    def _wrap_html_page(self, body: str, scripts: str, page_meta: dict) -> str:
        page_title   = page_meta.get("title")   or "Pyrex App"
        page_favicon = page_meta.get("favicon") or ""
        page_meta_d  = page_meta.get("meta")    or {}

        extra_head = ""
        if page_favicon:
            extra_head += f'  <link rel="icon" href="{_escape_attr(page_favicon)}" />\n'
        for mname, mcontent in page_meta_d.items():
            extra_head += (
                f'  <meta name="{_escape_attr(mname)}" '
                f'content="{_escape_attr(mcontent)}" />\n'
            )

        alpine_tag    = '<script defer src="https://cdn.jsdelivr.net/npm/alpinejs@3.x.x/dist/cdn.min.js"></script>'
        idiomorph_tag = '<script defer src="https://cdn.jsdelivr.net/npm/idiomorph@0.3.0/dist/idiomorph.js"></script>'
        pyrexjs_tag   = '<script defer src="/__pyrex_static/pyrex.js"></script>'

        scripts_block = ""
        if scripts.strip():
            scripts_block = f"\n  {scripts.strip()}"

        return f"""<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1.0" />
  <title>{_escape_html(page_title)}</title>
{extra_head}  <style>
    *, *::before, *::after {{ box-sizing: border-box; }}
    body {{ margin: 0; font-family: system-ui, sans-serif; }}
  </style>
  {alpine_tag}
  {idiomorph_tag}{scripts_block}
  {pyrexjs_tag}
</head>
<body>
{body}
</body>
</html>"""


# ── _RawHTML sentinel ─────────────────────────────────────────────────────────

class _RawHTML:
    """Sentinel that the layout injector uses to embed pre-rendered HTML."""
    __slots__ = ("html",)

    def __init__(self, html: str):
        self.html = html


# ── Standalone helpers ────────────────────────────────────────────────────────

def _lambda_source_to_js(source: str) -> str:
    source = source.strip()

    m = re.match(r"^lambda\s+(\w+)\s*:\s*(.+)$", source, re.DOTALL)
    if m:
        param = m.group(1)
        body = m.group(2).strip()
        body = re.sub(r"\b" + re.escape(param) + r"\b", "$event", body)
        return _py_expr_to_js(body)

    if source.startswith("lambda:"):
        return _py_expr_to_js(source[7:].strip())

    if re.match(r"^\w+$", source):
        return source + "()"

    return _py_expr_to_js(source)


def _py_expr_to_js(expr: str) -> str:
    if expr.startswith("lambda:"):
        expr = expr[7:].strip()
    elif re.match(r"^lambda\s+\w+:", expr):
        _, body = expr.split(":", 1)
        expr = body.strip()

    expr = re.sub(r"\bnot\s+(\w+)", r"!\1", expr)
    expr = expr.replace("True", "true").replace("False", "false").replace("None", "null")

    def _fstr(m):
        content = m.group(1)
        content = re.sub(r"\{(\w+)\}", r"${\1}", content)
        return f"`{content}`"
    expr = re.sub(r'f"([^"]*)"', _fstr, expr)
    expr = re.sub(r"f'([^']*)'", _fstr, expr)

    expr = re.sub(r"len\((\w+)\)", r"\1.length", expr)
    expr = expr.replace("print(", "console.log(")
    return expr


def _py_value_to_js(val) -> str:
    if isinstance(val, bool):
        return "true" if val else "false"
    if val is None:
        return "null"
    if isinstance(val, StateVar):
        return _py_value_to_js(val._value)
    if isinstance(val, str):
        return json.dumps(val)
    if isinstance(val, (int, float)):
        return str(val)
    if isinstance(val, (list, dict)):
        return json.dumps(val)
    return json.dumps(str(val))


def _escape_html(text: str) -> str:
    return (
        str(text)
        .replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
        .replace('"', "&quot;")
    )


def _escape_attr(text: str) -> str:
    return str(text).replace("&", "&amp;").replace('"', "&quot;")


# ── Async function → Alpine x-data method ────────────────────────────────────

def _py_expr_node_to_js(node: ast.expr) -> str:
    if isinstance(node, ast.Await):
        return f"await {_py_expr_node_to_js(node.value)}"

    if isinstance(node, ast.Call):
        if isinstance(node.func, ast.Name):
            if node.func.id == "len" and len(node.args) == 1:
                return f"{_py_expr_node_to_js(node.args[0])}.length"
            if node.func.id == "print":
                return f"console.log({', '.join(_py_expr_node_to_js(a) for a in node.args)})"
        func = _py_expr_node_to_js(node.func)
        args = [_py_expr_node_to_js(a) for a in node.args]
        return f"{func}({', '.join(args)})"

    if isinstance(node, ast.Name):
        return {"True": "true", "False": "false", "None": "null"}.get(node.id, node.id)

    if isinstance(node, ast.Constant):
        if isinstance(node.value, bool):
            return "true" if node.value else "false"
        if node.value is None:
            return "null"
        if isinstance(node.value, str):
            return json.dumps(node.value)
        return str(node.value)

    if isinstance(node, ast.Attribute):
        return f"{_py_expr_node_to_js(node.value)}.{node.attr}"

    if isinstance(node, ast.Subscript):
        obj = _py_expr_node_to_js(node.value)
        slc = node.slice
        if isinstance(slc, ast.Constant) and isinstance(slc.value, str):
            return f"{obj}.{slc.value}"
        return f"{obj}[{_py_expr_node_to_js(slc)}]"

    if isinstance(node, ast.JoinedStr):
        parts = []
        for v in node.values:
            if isinstance(v, ast.Constant):
                parts.append(str(v.value))
            elif isinstance(v, ast.FormattedValue):
                parts.append(f"${{{_py_expr_node_to_js(v.value)}}}")
            else:
                parts.append(f"${{{_py_expr_node_to_js(v)}}}")
        return "`" + "".join(parts) + "`"

    if isinstance(node, ast.BinOp):
        left = _py_expr_node_to_js(node.left)
        right = _py_expr_node_to_js(node.right)
        op_map = {ast.Add: "+", ast.Sub: "-", ast.Mult: "*", ast.Div: "/", ast.Mod: "%"}
        return f"{left} {op_map.get(type(node.op), '+')} {right}"

    if isinstance(node, ast.UnaryOp) and isinstance(node.op, ast.Not):
        return f"!{_py_expr_node_to_js(node.operand)}"

    if isinstance(node, ast.List):
        return "[" + ", ".join(_py_expr_node_to_js(e) for e in node.elts) + "]"

    if isinstance(node, ast.Dict):
        pairs = ", ".join(
            f"{_py_expr_node_to_js(k)}: {_py_expr_node_to_js(v)}"
            for k, v in zip(node.keys, node.values)
        )
        return "{" + pairs + "}"

    return _py_expr_to_js(ast.unparse(node))


def _py_stmt_to_js(stmt: ast.stmt) -> str:
    if isinstance(stmt, ast.Expr):
        return f"{_py_expr_node_to_js(stmt.value)};"

    if isinstance(stmt, ast.Assign):
        val = _py_expr_node_to_js(stmt.value)
        target = stmt.targets[0]
        if isinstance(target, ast.Name):
            return f"const {target.id}={val};"
        return f"{_py_expr_node_to_js(target)}={val};"

    if isinstance(stmt, ast.AugAssign):
        target = _py_expr_node_to_js(stmt.target)
        val = _py_expr_node_to_js(stmt.value)
        op_map = {ast.Add: "+=", ast.Sub: "-=", ast.Mult: "*="}
        return f"{target}{op_map.get(type(stmt.op), '+=')}{val};"

    if isinstance(stmt, ast.Return):
        if stmt.value is None:
            return "return;"
        return f"return {_py_expr_node_to_js(stmt.value)};"

    if isinstance(stmt, ast.If):
        cond = _py_expr_node_to_js(stmt.test)
        body = "".join(_py_stmt_to_js(s) for s in stmt.body)
        if stmt.orelse:
            else_body = "".join(_py_stmt_to_js(s) for s in stmt.orelse)
            return f"if({cond}){{{body}}}else{{{else_body}}}"
        return f"if({cond}){{{body}}}"

    return f"/* {ast.unparse(stmt)} */"


def _transpile_async_func_to_method(source: str, reactive_names: set[str]) -> str:
    """Convert async def Python source → minified Alpine x-data method string."""
    tree = ast.parse(source)
    fn = tree.body[0]
    if not isinstance(fn, ast.AsyncFunctionDef):
        raise TypeError(f"Expected AsyncFunctionDef, got {type(fn).__name__}")

    params = ", ".join(arg.arg for arg in fn.args.args)
    body = "".join(_py_stmt_to_js(s) for s in fn.body)

    for name in sorted(reactive_names, key=len, reverse=True):
        body = re.sub(r"\b" + re.escape(name) + r"\b", f"this.{name}", body)

    return f"async {fn.name}({params}){{{body}}}"


# ── JS minifier ───────────────────────────────────────────────────────────────

def _minify_js(src: str) -> str:
    out: list[str] = []
    i = 0
    n = len(src)

    while i < n:
        c = src[i]
        if c in ('"', "'", "`"):
            quote = c
            out.append(c)
            i += 1
            while i < n:
                ch = src[i]
                if ch == "\\" and i + 1 < n:
                    out.append(src[i : i + 2])
                    i += 2
                    continue
                out.append(ch)
                i += 1
                if ch == quote:
                    break
            continue

        if c == "/" and i + 1 < n and src[i + 1] == "/":
            while i < n and src[i] != "\n":
                i += 1
            continue

        if c == "/" and i + 1 < n and src[i + 1] == "*":
            i += 2
            while i < n - 1 and not (src[i] == "*" and src[i + 1] == "/"):
                i += 1
            i += 2
            out.append(" ")
            continue

        if c in (" ", "\t", "\n", "\r"):
            out.append(" ")
            while i < n and src[i] in (" ", "\t", "\n", "\r"):
                i += 1
            continue

        out.append(c)
        i += 1

    joined = "".join(out)
    for op in ("{", "}", ";", ",", "(", ")"):
        joined = joined.replace(" " + op, op).replace(op + " ", op)
    joined = re.sub(r"(?<![=!<>]) = (?!=)", "=", joined)
    joined = re.sub(r" {2,}", " ", joined)
    return joined.strip()
