"""
Pyrex Transpiler — .px → Alpine HTML

Takes a module registry produced by px_loader.load_px_file() and renders
the page to a complete HTML document with inline Alpine.js directives.

Pipeline for each component execution:
  1. Set _state_ctx / _handler_ctx / _store_ctx context variables
  2. Call the component function (which calls jsx() to build the tree)
  3. Read the state, handler, and store registries populated during execution
  4. Build the inline x-data string and inject onto the root element
  5. Walk the JSX tree → HTML, converting:
       StateVar children      → <span x-text="name">
       StoreAttr children     → <span x-text="$store.name.attr">
       JsRaw children         → <script>…</script>
       PyrexHandler props     → @event="…"
       RefVar ref prop        → x-ref="name"
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
    PyrexListComp, PyrexTernary, JsRaw, RefVar, StoreAttr,
    _state_ctx, _handler_ctx, _store_ctx,
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


# ── Part 1: Client Handler Transpiler ─────────────────────────────────────────

class PyrexClientError(Exception):
    """
    Raised at transpile time when a handler body contains an unsupported or
    forbidden construct.  Message is formatted for developer-friendly output.
    """
    pass


def _client_error(message: str, fn_name: str = "", lineno: int = 0,
                  filepath: str = "") -> PyrexClientError:
    header = "PyrexClientError"
    if fn_name:
        header += f" in {fn_name}()"
    if lineno:
        header += f" at line {lineno}"
    body = f"\n  {message}"
    if filepath and lineno:
        body += f"\n\n  File: {filepath}, line {lineno}"
    return PyrexClientError(header + ":" + body)


class PyrexClientTranspiler:
    """
    Translate an async def Python handler to a minified JavaScript method.

    Only the subset documented in Pyrex's translation table is supported.
    State mutations (direct assignment or in-place mutating methods called
    on a state variable) are detected and raise PyrexClientError.

    Usage:
        t = PyrexClientTranspiler(state_names, setter_names, store_vars, filepath)
        js_method = t.transpile(async_def_source_string)
    """

    # Methods that modify a collection in-place — forbidden on state vars
    _MUTATION_METHODS = frozenset({
        "push", "pop", "shift", "unshift", "splice",
        "sort", "reverse", "fill", "copyWithin",
    })

    # Python builtins that have no meaningful JS equivalent in client handlers
    _UNSUPPORTED_BUILTINS = frozenset({
        "sorted", "enumerate", "zip", "range", "list", "dict",
        "set", "tuple", "map", "filter", "reduce", "vars", "type",
        "isinstance", "hasattr", "getattr", "setattr",
    })

    def __init__(
        self,
        state_names: set[str],
        setter_names: set[str],
        store_vars: dict[str, str] | None = None,
        filepath: str = "",
    ):
        self.state_names = state_names
        self.setter_names = setter_names
        self.store_vars = store_vars or {}
        self.filepath = filepath
        self._fn_name = ""

    # ── Public entry ─────────────────────────────────────────────────────────

    def transpile(self, source: str) -> str:
        """Parse and translate an async def source string to a JS method."""
        tree = ast.parse(source)
        fn = tree.body[0]
        if not isinstance(fn, ast.AsyncFunctionDef):
            raise _client_error(
                f"Expected async def, got {type(fn).__name__}",
                filepath=self.filepath,
            )
        self._fn_name = fn.name
        params = ", ".join(arg.arg for arg in fn.args.args)
        body_js = self._stmts(fn.body)
        return f"async {fn.name}({params}){{{body_js}}}"

    # ── Statement translation ─────────────────────────────────────────────────

    def _stmts(self, stmts: list) -> str:
        return "".join(self._stmt(s) for s in stmts)

    def _stmt(self, node: ast.stmt) -> str:
        lineno = getattr(node, "lineno", 0)

        # js("…") escape hatch — verbatim JS passthrough
        if (
            isinstance(node, ast.Expr)
            and isinstance(node.value, ast.Call)
            and isinstance(node.value.func, ast.Name)
            and node.value.func.id == "js"
        ):
            call = node.value
            if call.args and isinstance(call.args[0], ast.Constant):
                raw = str(call.args[0].value).strip()
                # Ensure it ends with a semicolon
                if raw and not raw.endswith(";"):
                    raw += ";"
                return raw
            return ""

        if isinstance(node, ast.Expr):
            return self._expr(node.value) + ";"

        if isinstance(node, ast.Return):
            if node.value is None:
                return "return;"
            return f"return {self._expr(node.value)};"

        if isinstance(node, ast.Assign):
            return self._assign(node)

        if isinstance(node, ast.AugAssign):
            return self._aug_assign(node)

        if isinstance(node, ast.If):
            cond = self._expr(node.test)
            body = self._stmts(node.body)
            if node.orelse:
                else_body = self._stmts(node.orelse)
                return f"if({cond}){{{body}}}else{{{else_body}}}"
            return f"if({cond}){{{body}}}"

        if isinstance(node, ast.For):
            target = self._target(node.target)
            iter_js = self._expr(node.iter)
            body = self._stmts(node.body)
            return f"for(const {target} of {iter_js}){{{body}}}"

        raise _client_error(
            f"'{ast.unparse(node)}' is not supported in client handlers.\n\n"
            f"  Options:\n"
            f"  → Move this logic to a @server_action (recommended)\n"
            f"  → Use js(\"...\") for raw JavaScript",
            fn_name=self._fn_name,
            lineno=lineno,
            filepath=self.filepath,
        )

    def _assign(self, node: ast.Assign) -> str:
        target = node.targets[0]
        val_js = self._expr(node.value)

        # Store attribute assignment: cart.count = val
        if isinstance(target, ast.Attribute) and isinstance(target.value, ast.Name):
            var_id = target.value.id
            if var_id in self.store_vars:
                sn = self.store_vars[var_id]
                return f"Alpine.store('{sn}').{target.attr}={val_js};"

        # Forbidden: direct assignment to a state variable
        if isinstance(target, ast.Name) and target.id in self.state_names:
            setter = "set" + target.id[0].upper() + target.id[1:]
            raise _client_error(
                f"'{target.id}' is a state variable — use {setter}(...) to update it.\n"
                f"  Direct state mutation is not allowed.",
                fn_name=self._fn_name,
                lineno=getattr(node, "lineno", 0),
                filepath=self.filepath,
            )

        if isinstance(target, ast.Name):
            return f"const {target.id}={val_js};"

        return f"{self._target(target)}={val_js};"

    def _aug_assign(self, node: ast.AugAssign) -> str:
        target_js = self._target(node.target)
        val_js = self._expr(node.value)
        op_map = {ast.Add: "+=", ast.Sub: "-=", ast.Mult: "*=",
                  ast.Div: "/=", ast.Mod: "%="}
        op = op_map.get(type(node.op), "+=")
        return f"{target_js}{op}{val_js};"

    def _target(self, node: ast.expr) -> str:
        if isinstance(node, ast.Name):
            return self._name(node.id)
        if isinstance(node, ast.Attribute):
            return f"{self._expr(node.value)}.{node.attr}"
        if isinstance(node, ast.Subscript):
            return f"{self._expr(node.value)}[{self._expr(node.slice)}]"
        return ast.unparse(node)

    # ── Expression translation ────────────────────────────────────────────────

    def _name(self, name: str) -> str:
        """Prefix state vars and setters with 'this.' (Alpine x-data scope)."""
        if name in self.state_names or name in self.setter_names:
            return f"this.{name}"
        return name

    def _expr(self, node: ast.expr) -> str:
        if isinstance(node, ast.Constant):
            return self._constant(node)

        if isinstance(node, ast.Name):
            n = node.id
            if n == "True":  return "true"
            if n == "False": return "false"
            if n == "None":  return "null"
            return self._name(n)

        if isinstance(node, ast.Await):
            return f"await {self._expr(node.value)}"

        if isinstance(node, ast.JoinedStr):
            return self._fstring(node)

        if isinstance(node, ast.BinOp):
            return self._binop(node)

        if isinstance(node, ast.UnaryOp):
            if isinstance(node.op, ast.Not):
                return f"!{self._expr(node.operand)}"
            if isinstance(node.op, ast.USub):
                return f"-{self._expr(node.operand)}"
            if isinstance(node.op, ast.UAdd):
                return f"+{self._expr(node.operand)}"

        if isinstance(node, ast.BoolOp):
            op = "&&" if isinstance(node.op, ast.And) else "||"
            return f"({op.join(self._expr(v) for v in node.values)})"

        if isinstance(node, ast.Compare):
            return self._compare(node)

        if isinstance(node, ast.Call):
            return self._call(node)

        if isinstance(node, ast.Attribute):
            return self._attribute(node)

        if isinstance(node, ast.Subscript):
            return self._subscript(node)

        if isinstance(node, ast.IfExp):
            cond  = self._expr(node.test)
            true  = self._expr(node.body)
            false = self._expr(node.orelse)
            return f"({cond}?{true}:{false})"

        if isinstance(node, ast.List):
            items = ", ".join(
                f"...{self._expr(e.value)}" if isinstance(e, ast.Starred)
                else self._expr(e)
                for e in node.elts
            )
            return f"[{items}]"

        if isinstance(node, ast.Dict):
            return self._dict(node)

        if isinstance(node, ast.Lambda):
            params = ", ".join(arg.arg for arg in node.args.args)
            body   = self._expr(node.body)
            return f"({params}) => {body}"

        if isinstance(node, ast.Starred):
            return f"...{self._expr(node.value)}"

        raise _client_error(
            f"'{ast.unparse(node)}' is not supported in client handlers.\n\n"
            f"  Options:\n"
            f"  → Move this logic to a @server_action (recommended)\n"
            f"  → Use js(\"...\") for raw JavaScript",
            fn_name=self._fn_name,
            lineno=getattr(node, "lineno", 0),
            filepath=self.filepath,
        )

    def _constant(self, node: ast.Constant) -> str:
        if isinstance(node.value, bool):
            return "true" if node.value else "false"
        if node.value is None:
            return "null"
        if isinstance(node.value, str):
            return json.dumps(node.value)
        return str(node.value)

    def _fstring(self, node: ast.JoinedStr) -> str:
        parts = []
        for v in node.values:
            if isinstance(v, ast.Constant):
                parts.append(str(v.value).replace("`", "\\`").replace("$", "\\$"))
            elif isinstance(v, ast.FormattedValue):
                parts.append(f"${{{self._expr(v.value)}}}")
        return "`" + "".join(parts) + "`"

    def _binop(self, node: ast.BinOp) -> str:
        # list + [item]  →  [...list, item]
        if isinstance(node.op, ast.Add) and isinstance(node.right, ast.List):
            left  = self._expr(node.left)
            items = ", ".join(self._expr(e) for e in node.right.elts)
            return f"[...{left}{', ' + items if items else ''}]"
        # [item] + list  →  [item, ...list]
        if isinstance(node.op, ast.Add) and isinstance(node.left, ast.List):
            right = self._expr(node.right)
            items = ", ".join(self._expr(e) for e in node.left.elts)
            return f"[{items + ', ' if items else ''}...{right}]"

        left  = self._expr(node.left)
        right = self._expr(node.right)

        if isinstance(node.op, ast.FloorDiv):
            return f"Math.floor({left}/{right})"
        if isinstance(node.op, ast.Pow):
            return f"Math.pow({left},{right})"

        op_map = {
            ast.Add: "+", ast.Sub: "-", ast.Mult: "*",
            ast.Div: "/", ast.Mod: "%",
            ast.BitOr: "|", ast.BitAnd: "&", ast.BitXor: "^",
        }
        op = op_map.get(type(node.op))
        if op:
            return f"{left} {op} {right}"

        raise _client_error(
            f"Operator '{ast.unparse(node)}' is not supported in client handlers.",
            fn_name=self._fn_name,
            filepath=self.filepath,
        )

    def _compare(self, node: ast.Compare) -> str:
        if len(node.ops) != 1:
            raise _client_error(
                "Chained comparisons like 'a < b < c' are not supported. "
                "Use 'a < b and b < c' instead.",
                fn_name=self._fn_name,
                filepath=self.filepath,
            )
        left  = self._expr(node.left)
        op    = node.ops[0]
        right = self._expr(node.comparators[0])

        if isinstance(op, ast.In):    return f"{right}.includes({left})"
        if isinstance(op, ast.NotIn): return f"!{right}.includes({left})"

        op_map = {
            ast.Eq: "===", ast.NotEq: "!==",
            ast.Gt: ">",   ast.Lt: "<",
            ast.GtE: ">=", ast.LtE: "<=",
            ast.Is: "===", ast.IsNot: "!==",
        }
        op_str = op_map.get(type(op))
        if not op_str:
            raise _client_error(
                f"Comparison operator '{ast.unparse(node)}' is not supported.",
                fn_name=self._fn_name,
                filepath=self.filepath,
            )
        return f"{left} {op_str} {right}"

    def _attribute(self, node: ast.Attribute) -> str:
        # Store proxy: cart.count  →  Alpine.store('cart').count
        if isinstance(node.value, ast.Name) and node.value.id in self.store_vars:
            sn = self.store_vars[node.value.id]
            return f"Alpine.store('{sn}').{node.attr}"
        return f"{self._expr(node.value)}.{node.attr}"

    def _subscript(self, node: ast.Subscript) -> str:
        obj = self._expr(node.value)
        if isinstance(node.slice, ast.Slice):
            s = node.slice
            lower = self._expr(s.lower) if s.lower else "0"
            if s.upper:
                return f"{obj}.slice({lower},{self._expr(s.upper)})"
            return f"{obj}.slice({lower})"
        return f"{obj}[{self._expr(node.slice)}]"

    def _call(self, node: ast.Call) -> str:
        # js("…") in expression position
        if isinstance(node.func, ast.Name) and node.func.id == "js":
            if node.args and isinstance(node.args[0], ast.Constant):
                return str(node.args[0].value)
            return ""

        # ── Check for forbidden mutation methods on state variables ──────────
        if isinstance(node.func, ast.Attribute) and isinstance(node.func.value, ast.Name):
            var_id = node.func.value.id
            method = node.func.attr
            if var_id in self.state_names and method in self._MUTATION_METHODS:
                setter = "set" + var_id[0].upper() + var_id[1:]
                raise _client_error(
                    f"'{var_id}' is a state variable — use {setter}([...]) to update it.\n"
                    f"  Calling '{method}()' mutates state directly, which is not allowed.\n\n"
                    f"  Example: {setter}([...{var_id}, newItem])  # append\n"
                    f"           {setter}({var_id}.filter(x => x !== item))  # remove",
                    fn_name=self._fn_name,
                    lineno=getattr(node, "lineno", 0),
                    filepath=self.filepath,
                )

        # ── Special Python builtins → JS translations ────────────────────────
        if isinstance(node.func, ast.Name):
            fname = node.func.id

            if fname == "len" and len(node.args) == 1 and not node.keywords:
                return f"{self._expr(node.args[0])}.length"

            if fname == "str" and len(node.args) == 1:
                return f"String({self._expr(node.args[0])})"

            if fname == "bool" and len(node.args) == 1:
                return f"Boolean({self._expr(node.args[0])})"

            if fname == "parseInt" and len(node.args) == 1 and not node.keywords:
                return f"parseInt({self._expr(node.args[0])},10)"

            if fname == "parseFloat" and len(node.args) == 1:
                return f"parseFloat({self._expr(node.args[0])})"

            if fname == "print":
                args = ", ".join(self._expr(a) for a in node.args)
                return f"console.log({args})"

            if fname == "abs" and len(node.args) == 1:
                return f"Math.abs({self._expr(node.args[0])})"

            if fname == "round" and node.args:
                return f"Math.round({self._expr(node.args[0])})"

            if fname == "min" and len(node.args) >= 2:
                args = ", ".join(self._expr(a) for a in node.args)
                return f"Math.min({args})"

            if fname == "max" and len(node.args) >= 2:
                args = ", ".join(self._expr(a) for a in node.args)
                return f"Math.max({args})"

            if fname in self._UNSUPPORTED_BUILTINS:
                raise _client_error(
                    f"'{fname}()' is not supported in client handlers.\n\n"
                    f"  Options:\n"
                    f"  → Move this logic to a @server_action (recommended)\n"
                    f"  → Use js(\"...\") for raw JavaScript",
                    fn_name=self._fn_name,
                    lineno=getattr(node, "lineno", 0),
                    filepath=self.filepath,
                )

        # ── General call ──────────────────────────────────────────────────────
        func_js = self._expr(node.func)
        args_js: list[str] = []
        for arg in node.args:
            if isinstance(arg, ast.Starred):
                args_js.append(f"...{self._expr(arg.value)}")
            else:
                args_js.append(self._expr(arg))
        # Python kwargs → positional JS args (key dropped, value kept in order)
        # e.g. create_task(title=newTask) → create_task(this.newTask)
        # This matches the action proxy signature: async function create_task(title)
        for kw in node.keywords:
            if kw.arg:
                args_js.append(self._expr(kw.value))
        return f"{func_js}({', '.join(args_js)})"

    def _dict(self, node: ast.Dict) -> str:
        parts: list[str] = []
        for k, v in zip(node.keys, node.values):
            if k is None:
                parts.append(f"...{self._expr(v)}")
            elif isinstance(k, ast.Constant) and isinstance(k.value, str):
                # {"key": val} → {key: val}  (identifier-safe keys only)
                key = k.value
                if re.match(r"^[A-Za-z_$][A-Za-z0-9_$]*$", key):
                    parts.append(f"{key}:{self._expr(v)}")
                else:
                    parts.append(f"{json.dumps(key)}:{self._expr(v)}")
            else:
                parts.append(f"[{self._expr(k)}]:{self._expr(v)}")
        return "{" + ", ".join(parts) + "}"


# ── Main transpiler ───────────────────────────────────────────────────────────

class PxTranspiler:
    """
    Renders a .px module registry into a complete HTML page.

    Usage:
        registry = {}
        load_px_file(filepath, registry)
        t = PxTranspiler(registry, action_ids={...},
                         globals_css="...", route_css="...", use_tailwind=False)
        html = t.transpile()
    """

    def __init__(
        self,
        registry: dict,
        action_ids: dict[str, str] | None = None,
        globals_css: str = "",
        route_css: str = "",
        use_tailwind: bool = False,
        tailwind_config: str = "",
    ):
        self.registry = registry
        self.action_ids = action_ids or {}
        self.globals_css = globals_css
        self.route_css = route_css
        self.use_tailwind = use_tailwind
        self.tailwind_config = tailwind_config

    # ── Public entry points ───────────────────────────────────────────────────

    def transpile(self) -> str:
        page_fn = self.registry.get("page_fn")
        if page_fn is None:
            raise ValueError(
                "No @page component found. Decorate a function with @page."
            )
        page_meta = self.registry.get("page_meta", {})
        body_html = self._render_component(page_fn, {})
        proxy_js  = self._build_server_action_proxies()
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

        layout_html = self._render_component(
            layout_fn, {"children": [_RawHTML(page_html)]}
        )
        proxy_js = self._build_server_action_proxies()
        return self._wrap_html_page(layout_html, proxy_js, page_meta)

    # ── Component rendering ───────────────────────────────────────────────────

    def _render_component(self, fn, props: dict) -> str:
        """
        Execute a component function in a fresh state/handler/store context,
        collect the resulting JSX tree, and render it to HTML.
        """
        state_reg:   dict = {}
        handler_reg: dict = {}
        store_reg:   dict = {}   # var_name → store_name

        tok_s  = _state_ctx.set(state_reg)
        tok_h  = _handler_ctx.set(handler_reg)
        tok_st = _store_ctx.set(store_reg)
        try:
            jsx_tree = fn(**props)
        except Exception as exc:
            _state_ctx.reset(tok_s)
            _handler_ctx.reset(tok_h)
            _store_ctx.reset(tok_st)
            fn_name = getattr(fn, "__name__", "?")
            return (
                f'<div style="color:red;padding:1rem;font-family:monospace;">'
                f"Component error in {fn_name}(): {_escape_html(str(exc))}</div>"
            )
        finally:
            _state_ctx.reset(tok_s)
            _handler_ctx.reset(tok_h)
            _store_ctx.reset(tok_st)

        # Inject x-data on the root element if this component has state/handlers
        if (state_reg or handler_reg) and isinstance(jsx_tree, JSXNode):
            jsx_tree.props["x-data"] = self._build_x_data_str(
                state_reg, handler_reg, store_reg
            )

        return self._node_to_html(jsx_tree)

    # ── x-data generation ─────────────────────────────────────────────────────

    def _build_x_data_str(
        self,
        state_reg: dict,
        handler_reg: dict,
        store_reg: dict | None = None,
    ) -> str:
        """Build an inline, minified Alpine x-data object string."""
        parts: list[str] = []

        for var_name, info in state_reg.items():
            initial_js = _py_value_to_js(info["initial"])
            setter = info["setter"]
            parts.append(f"{var_name}:{initial_js}")
            if setter:
                parts.append(f"{setter}(v){{this.{var_name}=v}}")

        state_names  = set(state_reg.keys())
        setter_names = {
            info["setter"] for info in state_reg.values() if info.get("setter")
        }

        for fn_name, info in handler_reg.items():
            try:
                ct = PyrexClientTranspiler(
                    state_names=state_names,
                    setter_names=setter_names,
                    store_vars=store_reg or {},
                    filepath="",
                )
                method_js = ct.transpile(info["source"])
                parts.append(method_js)
            except PyrexClientError:
                raise   # propagate — developer must fix their handler
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

        # Part 2: js() in JSX → <script> tag
        if isinstance(node, JsRaw):
            return f"<script>{node.content}</script>"

        # StateVar → reactive text span
        if isinstance(node, StateVar):
            return f'<span x-text="{node._name}"></span>'

        if isinstance(node, StateSetter):
            return ""

        # Part 5: StoreAttr in JSX children → x-text="$store.name.attr"
        if isinstance(node, StoreAttr):
            return f'<span x-text="{node._alpine_ref}"></span>'

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
            # Part 4: ref={refVar} → x-ref="name"
            if key == "ref" and isinstance(val, RefVar):
                if val._name:
                    parts.append(f'x-ref="{_escape_attr(val._name)}"')
                continue

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
                js_val = self._handler_to_js(val) if isinstance(val, PyrexHandler) else str(val)
                parts.append(f'{key}="{_escape_attr(js_val)}"')
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

            # Part 5: StoreAttr as prop value → Alpine bound prop
            if isinstance(val, StoreAttr):
                parts.append(f':{key}="{_escape_attr(val._alpine_ref)}"')
                continue

            # StateVar as prop value → Alpine bound prop
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

        # Part 3: CSS injection
        css_block = ""
        if self.globals_css:
            css_block += f"  <style>{self.globals_css}</style>\n"
        if self.route_css:
            css_block += f"  <style>{self.route_css}</style>\n"

        # Part 3: Tailwind CDN (v4 Play CDN)
        tailwind_block = ""
        if self.use_tailwind:
            tailwind_block = '  <script src="https://cdn.jsdelivr.net/npm/@tailwindcss/browser@4"></script>\n'
            if self.tailwind_config:
                tailwind_block += f"  <style type=\"text/tailwindcss\">{self.tailwind_config}</style>\n"

        # Part 5: Alpine store initialisation
        store_init_js = _build_store_init_js()
        store_block = ""
        if store_init_js:
            store_block = (
                f"  <script>"
                f"document.addEventListener('alpine:init',()=>{{{store_init_js}}});"
                f"</script>\n"
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
{extra_head}{tailwind_block}{css_block}{store_block}  <style>
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


# ── Store init JS builder ─────────────────────────────────────────────────────

def _build_store_init_js() -> str:
    """Generate Alpine.store(...) init calls for all registered stores."""
    from pyrex.jsx_runtime import _registered_stores
    if not _registered_stores:
        return ""
    parts = []
    for name, store in _registered_stores.items():
        parts.append(
            f"Alpine.store({json.dumps(name)},{json.dumps(store._state)});"
        )
    return "".join(parts)


# ── Standalone helpers ────────────────────────────────────────────────────────

def _lambda_source_to_js(source: str) -> str:
    """Translate a Python lambda/expression source string to JavaScript."""
    source = source.strip()

    m = re.match(r"^lambda\s+(\w+)\s*:\s*(.+)$", source, re.DOTALL)
    if m:
        param = m.group(1)
        body  = m.group(2).strip()
        try:
            js = _py_expr_node_to_js(ast.parse(body, mode="eval").body)
            return re.sub(r"\b" + re.escape(param) + r"\b", "$event", js)
        except Exception:
            body = re.sub(r"\b" + re.escape(param) + r"\b", "$event", body)
            return _py_expr_to_js(body)

    if source.startswith("lambda:"):
        body = source[7:].strip()
        try:
            return _py_expr_node_to_js(ast.parse(body, mode="eval").body)
        except Exception:
            return _py_expr_to_js(body)

    if re.match(r"^\w+$", source):
        return source + "()"

    try:
        return _py_expr_node_to_js(ast.parse(source, mode="eval").body)
    except Exception:
        return _py_expr_to_js(source)


def _py_expr_to_js(expr: str) -> str:
    """String-based fallback Python→JS translator (used for lambda bodies)."""
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


# ── AST-based expression → JS (used by _lambda_source_to_js) ─────────────────

def _py_expr_node_to_js(node: ast.expr) -> str:
    if isinstance(node, ast.Await):
        return f"await {_py_expr_node_to_js(node.value)}"

    if isinstance(node, ast.Call):
        if isinstance(node.func, ast.Name):
            if node.func.id == "len" and len(node.args) == 1:
                return f"{_py_expr_node_to_js(node.args[0])}.length"
            if node.func.id == "print":
                return f"console.log({', '.join(_py_expr_node_to_js(a) for a in node.args)})"
            if node.func.id == "parseInt" and len(node.args) == 1:
                return f"parseInt({_py_expr_node_to_js(node.args[0])},10)"
            if node.func.id == "str" and len(node.args) == 1:
                return f"String({_py_expr_node_to_js(node.args[0])})"
        func = _py_expr_node_to_js(node.func)
        args = [_py_expr_node_to_js(a) for a in node.args]
        # Python kwargs → positional JS args (key dropped, value kept in order)
        for kw in node.keywords:
            if kw.arg:
                args.append(_py_expr_node_to_js(kw.value))
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
        if isinstance(slc, ast.Slice):
            lower = _py_expr_node_to_js(slc.lower) if slc.lower else "0"
            if slc.upper:
                return f"{obj}.slice({lower},{_py_expr_node_to_js(slc.upper)})"
            return f"{obj}.slice({lower})"
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
        # List concatenation
        if isinstance(node.op, ast.Add) and isinstance(node.right, ast.List):
            left  = _py_expr_node_to_js(node.left)
            items = ", ".join(_py_expr_node_to_js(e) for e in node.right.elts)
            return f"[...{left}{', ' + items if items else ''}]"
        if isinstance(node.op, ast.Add) and isinstance(node.left, ast.List):
            right = _py_expr_node_to_js(node.right)
            items = ", ".join(_py_expr_node_to_js(e) for e in node.left.elts)
            return f"[{items + ', ' if items else ''}...{right}]"

        left  = _py_expr_node_to_js(node.left)
        right = _py_expr_node_to_js(node.right)
        if isinstance(node.op, ast.FloorDiv):
            return f"Math.floor({left}/{right})"
        if isinstance(node.op, ast.Pow):
            return f"Math.pow({left},{right})"
        op_map = {ast.Add: "+", ast.Sub: "-", ast.Mult: "*", ast.Div: "/", ast.Mod: "%"}
        return f"{left} {op_map.get(type(node.op), '+')} {right}"

    if isinstance(node, ast.UnaryOp) and isinstance(node.op, ast.Not):
        return f"!{_py_expr_node_to_js(node.operand)}"

    if isinstance(node, ast.BoolOp):
        op = "&&" if isinstance(node.op, ast.And) else "||"
        return f"({op.join(_py_expr_node_to_js(v) for v in node.values)})"

    if isinstance(node, ast.Compare) and len(node.ops) == 1:
        left  = _py_expr_node_to_js(node.left)
        right = _py_expr_node_to_js(node.comparators[0])
        op = node.ops[0]
        if isinstance(op, ast.In):    return f"{right}.includes({left})"
        if isinstance(op, ast.NotIn): return f"!{right}.includes({left})"
        op_map = {ast.Eq: "===", ast.NotEq: "!==", ast.Gt: ">", ast.Lt: "<",
                  ast.GtE: ">=", ast.LtE: "<="}
        return f"{left} {op_map.get(type(op), '==')} {right}"

    if isinstance(node, ast.List):
        return "[" + ", ".join(_py_expr_node_to_js(e) for e in node.elts) + "]"

    if isinstance(node, ast.Dict):
        pairs = ", ".join(
            f"...{_py_expr_node_to_js(v)}" if k is None
            else f"{_py_expr_node_to_js(k)}: {_py_expr_node_to_js(v)}"
            for k, v in zip(node.keys, node.values)
        )
        return "{" + pairs + "}"

    if isinstance(node, ast.IfExp):
        cond  = _py_expr_node_to_js(node.test)
        true  = _py_expr_node_to_js(node.body)
        false = _py_expr_node_to_js(node.orelse)
        return f"({cond}?{true}:{false})"

    if isinstance(node, ast.Lambda):
        params = ", ".join(arg.arg for arg in node.args.args)
        body   = _py_expr_node_to_js(node.body)
        return f"({params}) => {body}"

    return _py_expr_to_js(ast.unparse(node))


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
