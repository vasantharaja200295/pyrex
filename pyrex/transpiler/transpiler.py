"""
Pyrex Transpiler

Takes a PyxModule (parsed .pyx file) and outputs a complete HTML page.

Pipeline:
  ComponentDef  →  resolve JSX  →  HTML string
  use_state     →  Alpine x-data reactive property + setter method
  use_effect    →  Alpine x-effect directive
  onClick etc   →  Alpine @click / @input / etc.
  {expr}        →  static stamped value or x-text binding (for state vars)

Runtime: Alpine.js (CDN, defer) — no __Pyrex global, no window.setter globals.
"""

import ast
import re
import json
from pyrex.parser.pyx_parser import PyxModule, ComponentDef, UseStateCall, UseEffectCall, LocalFunc, LocalAsyncFunc
from pyrex.parser.jsx_parser import parse_jsx, JSXNode, TextNode


class Transpiler:
    def __init__(self, module: PyxModule, action_ids: dict[str, str] | None = None):
        self.module = module
        self.component_map = {c.name: c for c in module.components}
        self._id_counter = 0
        self._action_ids = action_ids or {}

    def transpile(self) -> str:
        """Entry point — returns complete HTML page as string."""
        root_name = self.module.root_component
        if not root_name:
            raise ValueError("No root component found. Decorate one with @page or name it with uppercase.")

        root = self.component_map[root_name]

        alpine_script = self._build_alpine_script()
        component_factories = self._build_all_component_factories()
        proxy_js = self._build_server_action_proxies()

        body_html = self._render_component(root, props={})

        return self._wrap_html_page(body_html, alpine_script + "\n" + component_factories + "\n" + proxy_js)

    def _render_component(self, component: ComponentDef, props: dict) -> str:
        """Render a component to HTML string."""
        if not component.jsx_string:
            return f"<!-- {component.name}: no JSX returned -->"

        jsx = component.jsx_string
        for prop_name, prop_val in props.items():
            prop_str = str(prop_val)
            if not prop_str.lstrip().startswith("<"):
                jsx = jsx.replace(f"{{{prop_name}}}", prop_str)

        # Build server-side scope: props + inner functions + evaluated local variables
        state_names = {s.var_name for s in component.use_states}
        local_scope: dict = dict(props)

        def _globs():
            return {"__builtins__": __builtins__, **local_scope}

        for lf in component.local_funcs:
            try:
                exec(compile(lf.source, "<pyrex>", "exec"), _globs(), local_scope)
            except Exception:
                pass

        for lv in component.local_vars:
            try:
                local_scope[lv.name] = eval(
                    compile(lv.expr_source, "<pyrex>", "eval"),
                    _globs(),
                    local_scope,
                )
            except Exception:
                pass

        # Substitute server-side variables that are NOT state vars.
        # Skip any value whose string form is HTML (starts with '<') — those are
        # safely injected as raw HTML by _node_to_html's expression evaluator.
        # Inlining raw HTML into the JSX string before parsing breaks the JSX
        # parser when the HTML contains Alpine @event attributes.
        for var_name, val in local_scope.items():
            if var_name not in state_names:
                val_str = str(val)
                if not val_str.lstrip().startswith("<"):
                    jsx = jsx.replace(f"{{{var_name}}}", val_str)

        self._render_scope = local_scope

        # State variable references → x-text spans (Alpine keeps them updated)
        for state in component.use_states:
            jsx = re.sub(
                rf'\{{{re.escape(state.var_name)}\}}',
                f'<span x-text="{state.var_name}"></span>',
                jsx
            )

        try:
            ast_node = parse_jsx(jsx)
        except Exception as e:
            return f'<div style="color:red;padding:1rem;">JSX Parse Error in {component.name}: {e}</div>'

        # Attach Alpine directives to the root element if the component has reactive state
        needs_alpine = bool(component.use_states or component.local_async_funcs)
        if needs_alpine and isinstance(ast_node, JSXNode):
            ast_node.props['x-data'] = f"__pyrex_{component.name}()"

        if component.use_effects and isinstance(ast_node, JSXNode):
            effect_bodies = []
            for effect in component.use_effects:
                js_body = _py_expr_to_js(effect.callback_body, component)
                effect_bodies.append(js_body)
            ast_node.props['x-effect'] = "; ".join(effect_bodies)

        try:
            html = self._node_to_html(ast_node, component)
        except Exception as e:
            html = f'<div style="color:red;padding:1rem;">Render Error in {component.name}: {e}</div>'

        return html

    def _node_to_html(self, node, component: ComponentDef) -> str:
        if isinstance(node, TextNode):
            if node.is_expression:
                scope = getattr(self, "_render_scope", None)
                if scope is not None:
                    try:
                        result = eval(
                            compile(node.content, "<pyrex>", "eval"),
                            {"__builtins__": __builtins__, **scope},
                            scope,
                        )
                        result_str = str(result)
                        if result_str.lstrip().startswith("<"):
                            return result_str
                        return _escape_html(result_str)
                    except Exception:
                        pass
                return f'<span data-expr="{node.content}"></span>'
            return _escape_html(node.content)

        if isinstance(node, JSXNode):
            if node.component and node.tag in self.component_map:
                child_comp = self.component_map[node.tag]
                child_props = {}
                for k, v in node.props.items():
                    if isinstance(v, str) and v.startswith('_expr_:'):
                        child_props[k] = v[7:]
                    else:
                        child_props[k] = v
                # Save and restore _render_scope: child renders overwrite it,
                # which would corrupt expression evaluation in the parent scope
                # (e.g. {children} evaluated after <Header/> would lose its value).
                saved_scope = getattr(self, "_render_scope", None)
                result = self._render_component(child_comp, child_props)
                self._render_scope = saved_scope
                return result

            tag = node.tag
            attrs = self._build_attrs(node.props, component)

            if node.self_closing:
                return f'<{tag}{attrs} />'

            children_html = ""
            for child in node.children:
                children_html += self._node_to_html(child, component)

            return f'<{tag}{attrs}>{children_html}</{tag}>'

        return ""

    def _build_attrs(self, props: dict, component: ComponentDef) -> str:
        """Convert JSX props to HTML attributes, translating event handlers to Alpine directives."""
        parts = []
        state_names = {s.var_name for s in component.use_states}
        scope = getattr(self, "_render_scope", {})

        for key, val in props.items():
            # Alpine pass-through directives (x-data, x-text, x-effect already set directly)
            if key.startswith('x-') or key.startswith('@'):
                parts.append(f'{key}="{val}"')
                continue

            # camelCase JSX event props: onClick, onChange, onSubmit, etc.
            if key.startswith('on') and len(key) > 2 and key[2].isupper():
                alpine_attr = _jsx_event_to_alpine(key)
                if isinstance(val, str) and val.startswith('_expr_:'):
                    expr = val[7:]
                    try:
                        js_expr = _handle_event_expr_alpine(expr, component, scope, state_names)
                    except ValueError as e:
                        raise ValueError(f"In component {component.name!r}: {e}") from e
                    parts.append(f'{alpine_attr}="{js_expr}"')
                else:
                    parts.append(f'{alpine_attr}="{val}"')
                continue

            # Lowercase HTML event attrs: onclick, oninput, onkeydown, etc.
            #
            # Alpine only processes @event directives for elements inside an x-data
            # scope.  Stateful components always have x-data on their root element, so
            # @event works there.  Stateless components (no use_state, no async funcs)
            # have NO x-data, so Alpine never attaches the listener — the button does
            # nothing.  For those, emit the plain native on* attribute instead.
            if key.startswith('on') and len(key) > 2 and not key[2].isupper():
                event_name = key[2:]   # "click", "input", "keydown", …
                if isinstance(val, str) and val.startswith('_expr_:'):
                    js_expr = _py_expr_to_js(val[7:], component)
                else:
                    js_expr = val if isinstance(val, str) else str(val)
                if component.use_states or component.local_async_funcs:
                    # Stateful component — Alpine @event directive.
                    # Replace 'event' with Alpine's '$event' magic identifier.
                    js_expr = re.sub(r'\bevent\b', lambda _: '$event', js_expr)
                    parts.append(f'@{event_name}="{js_expr}"')
                else:
                    # Stateless component — keep as a native on* attribute.
                    # 'event' and 'this' behave exactly as in plain HTML here.
                    parts.append(f'{key}="{js_expr}"')
                continue

            # className → class
            if key == 'className':
                key = 'class'

            # htmlFor → for
            if key == 'htmlFor':
                key = 'for'

            if isinstance(val, bool):
                if val:
                    parts.append(key)
            elif isinstance(val, str) and val.startswith('_expr_:'):
                expr = val[7:]
                scope = getattr(self, "_render_scope", None)
                if scope is not None:
                    try:
                        result = eval(
                            compile(expr, "<pyrex>", "eval"),
                            {"__builtins__": __builtins__},
                            scope,
                        )
                        parts.append(f'{key}="{_escape_html(str(result))}"')
                        continue
                    except Exception:
                        pass
                js_val = _py_expr_to_js(expr, component)
                parts.append(f'{key}="{js_val}"')
            else:
                parts.append(f'{key}="{val}"')

        return (" " + " ".join(parts)) if parts else ""

    # ── Alpine script tag ───────────────────────────────────────────────────

    def _build_alpine_script(self) -> str:
        """Return a <script defer> tag that loads Alpine.js from CDN."""
        return '<script defer src="https://cdn.jsdelivr.net/npm/alpinejs@3.x.x/dist/cdn.min.js"></script>'

    # ── Component factory functions for x-data ──────────────────────────────

    def _build_all_component_factories(self) -> str:
        """Generate Alpine x-data factory functions for all stateful components."""
        scripts = []
        for comp in self.module.components:
            factory = self._build_component_factory(comp)
            if factory:
                scripts.append(factory)
        if not scripts:
            return ""
        return "\n<script>\n" + "\n\n".join(scripts) + "\n</script>"

    def _build_component_factory(self, component: ComponentDef) -> str:
        """
        Generate a JS factory function for a component's Alpine x-data object.

        The factory is called once per component instance via x-data="__pyrex_Name()".
        Each call returns a fresh reactive object, so multiple instances of the
        same component are fully isolated.

        The object contains:
          1. State variables as properties (initial values)
          2. Setter methods named exactly as declared in use_state
          3. Async client-side handler methods (translated from Python)
        """
        if not component.use_states and not component.local_async_funcs:
            return ""

        state_names = {s.var_name for s in component.use_states}
        setter_names = {s.setter_name for s in component.use_states}
        reactive_names = state_names | setter_names

        lines = [f"function __pyrex_{component.name}() {{", "  return {"]

        for state in component.use_states:
            initial = _py_value_to_js_literal(state.initial_value)
            lines.append(f"    {state.var_name}: {initial},")
            lines.append(f"    {state.setter_name}(val) {{ this.{state.var_name} = val; }},")

        for laf in component.local_async_funcs:
            try:
                method_js = _transpile_async_func_to_js_method(laf.source, reactive_names)
                lines.append(f"    {method_js},")
            except Exception as e:
                lines.append(f"    /* {laf.name}: transpile error: {e} */")

        lines.append("  };")
        lines.append("}")
        return "\n".join(lines)

    # ── Server action proxies (unchanged from original) ─────────────────────

    def _build_server_action_proxies(self) -> str:
        """
        Generate one named async JS function per @server_action.

        Each proxy calls POST /__pyrex/ with {"i": action_id, "a": params}
        and returns the parsed JSON response.
        """
        if not self.module.server_actions:
            return ""
        fns = []
        for action in self.module.server_actions:
            action_id = self._action_ids.get(action.name, action.name)
            param_list = ", ".join(p[0] for p in action.params)
            args_obj = (
                "{ " + ", ".join(p[0] for p in action.params) + " }"
                if action.params else "{}"
            )
            fns.append(f"""
async function {action.name}({param_list}) {{
    const res = await fetch('/__pyrex/', {{
        method: 'POST',
        headers: {{
            'Content-Type': 'application/json',
            'x-pyrex-token': window.__PYREX_TOKEN || '',
        }},
        body: JSON.stringify({{ i: {json.dumps(action_id)}, a: {args_obj} }}),
    }});
    return await res.json();
}}""")
        return "\n<script>" + "".join(fns) + "\n</script>"

    def _wrap_html_page(self, body: str, scripts: str) -> str:
        # Split out the Alpine <script defer> tag so it appears first in <head>,
        # before the factory / proxy scripts that it depends on.
        alpine_tag = '<script defer src="https://cdn.jsdelivr.net/npm/alpinejs@3.x.x/dist/cdn.min.js"></script>'
        rest = scripts.replace(alpine_tag, "").strip()
        head_content = f"  {alpine_tag}"
        if rest:
            head_content += f"\n  {rest}"
        return f"""<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1.0" />
  <title>Pyrex App</title>
  <style>
    *, *::before, *::after {{ box-sizing: border-box; }}
    body {{ margin: 0; font-family: system-ui, sans-serif; }}
  </style>
{head_content}
</head>
<body>
{body}
</body>
</html>"""

    def _next_id(self) -> str:
        self._id_counter += 1
        return f"pyrex-{self._id_counter}"


# ── Helpers ────────────────────────────────────────────────────────────────

def _jsx_event_to_alpine(jsx_event: str) -> str:
    """
    Translate a camelCase JSX event prop to an Alpine @event directive name.

    onClick → @click, onChange → @change, onSubmit → @submit, etc.
    """
    mapping = {
        'onClick':      '@click',
        'onChange':     '@change',
        'onSubmit':     '@submit',
        'onInput':      '@input',
        'onKeyDown':    '@keydown',
        'onKeyUp':      '@keyup',
        'onFocus':      '@focus',
        'onBlur':       '@blur',
        'onMouseEnter': '@mouseenter',
        'onMouseLeave': '@mouseleave',
    }
    if jsx_event in mapping:
        return mapping[jsx_event]
    # Generic fallback: onFooBar → @foobar
    return '@' + jsx_event[2:].lower()


def _handle_event_expr_alpine(
    expr: str,
    component,
    scope: dict | None = None,
    state_names: set | None = None,
) -> str:
    """
    Translate an event-handler JSX expression to an Alpine-compatible JS string.

    Pattern 1 — bare function reference:
        {handle_submit}  →  "handle_submit()"

    Pattern 2 — lambda with no event parameter:
        {lambda: set_count(count + 1)}  →  "set_count(count + 1)"
        Server-side scope variables are stamped as literals.

    Pattern 3 — lambda with an event parameter:
        {lambda e: set_name(e.target.value)}  →  "set_name($event.target.value)"
        The parameter name is replaced with Alpine's $event identifier.

    Error — direct function call (not wrapped in a lambda):
        {handle_add()}  →  raises ValueError with a clear message
    """
    import ast as _ast

    expr = expr.strip()
    scope = scope or {}
    state_names = state_names or set()

    # Pattern 3: lambda <param>: <body>
    m = re.match(r'^lambda\s+(\w+)\s*:\s*(.+)$', expr, re.DOTALL)
    if m:
        param_name = m.group(1)
        body = m.group(2).strip()
        # Replace the param name with Alpine's $event ($ needs escaping in re.sub)
        body = re.sub(r'\b' + re.escape(param_name) + r'\b', lambda _: '$event', body)
        return _py_expr_to_js(body, component)

    # Pattern 2: lambda: <body>  (no parameter)
    if expr.startswith('lambda:'):
        body = expr[7:].strip()
        body = _substitute_scope_vars(body, scope, state_names)
        return _py_expr_to_js(body, component)

    # Error case: detect a plain function call (not wrapped in a lambda)
    try:
        tree = _ast.parse(expr, mode='eval')
        if (isinstance(tree.body, _ast.Call)
                and isinstance(tree.body.func, _ast.Name)):
            name = tree.body.func.id
            raise ValueError(
                f"`{{{expr}}}` calls `{name}` at render time — the return value becomes "
                f"the handler, not the function itself. "
                f"Use a bare reference `{{{name}}}` to call it with no args, "
                f"or a lambda `{{lambda: {expr}}}` to call it with args."
            )
    except SyntaxError:
        pass

    # Pattern 1: bare name  →  name()
    if re.match(r'^\w+$', expr):
        return expr + '()'

    # Fallback: generic expression translation
    return _py_expr_to_js(expr, component)


def _py_expr_to_js(expr: str, component) -> str:
    """
    Best-effort Python expression → JavaScript expression.
    Handles the common patterns that appear in event handlers and effects.
    """
    # lambda: body  →  body
    if expr.startswith('lambda:'):
        expr = expr[7:].strip()
    elif re.match(r'^lambda\s+\w+:', expr):
        _, body = expr.split(':', 1)
        expr = body.strip()

    # not x → !x
    expr = re.sub(r'\bnot\s+(\w+)', r'!\1', expr)

    # True/False/None → JS equivalents
    expr = expr.replace('True', 'true').replace('False', 'false')
    expr = expr.replace('None', 'null')

    # f"...{var}..." → `...${var}...`
    def fstring_to_template(m):
        content = m.group(1)
        content = re.sub(r'\{(\w+)\}', r'${$1}', content)
        return f'`{content}`'
    expr = re.sub(r'f"([^"]*)"', fstring_to_template, expr)
    expr = re.sub(r"f'([^']*)'", fstring_to_template, expr)

    # len(x) → x.length
    expr = re.sub(r'len\((\w+)\)', r'\1.length', expr)

    # print(...) → console.log(...)
    expr = expr.replace('print(', 'console.log(')

    return expr


def _py_value_to_js_literal(py_val: str) -> str:
    """Convert Python literal string to JS literal."""
    py_val = py_val.strip()
    if py_val in ('True', 'False'):
        return py_val.lower()
    if py_val == 'None':
        return 'null'
    if py_val.startswith(('[', '{')):
        return py_val
    if py_val.startswith(("'", '"')):
        return py_val
    return py_val  # numbers pass through as-is


def _strip_quotes(val: str) -> str:
    val = val.strip()
    if (val.startswith('"') and val.endswith('"')) or \
       (val.startswith("'") and val.endswith("'")):
        return val[1:-1]
    return val


def _escape_html(text: str) -> str:
    return (text
        .replace('&', '&amp;')
        .replace('<', '&lt;')
        .replace('>', '&gt;')
        .replace('"', '&quot;'))


# ── Async function transpilation ────────────────────────────────────────────

def _py_expr_node_to_js(node: ast.expr) -> str:
    """
    Translate an AST expression node to a JavaScript expression string.
    Uses the Python AST directly for correctness.
    """
    if isinstance(node, ast.Await):
        return f"await {_py_expr_node_to_js(node.value)}"

    if isinstance(node, ast.Call):
        if isinstance(node.func, ast.Name):
            if node.func.id == "len" and len(node.args) == 1:
                return f"{_py_expr_node_to_js(node.args[0])}.length"
            if node.func.id == "print":
                args = ", ".join(_py_expr_node_to_js(a) for a in node.args)
                return f"console.log({args})"
        func = _py_expr_node_to_js(node.func)
        args = [_py_expr_node_to_js(a) for a in node.args]
        args += [f"{kw.arg}={_py_expr_node_to_js(kw.value)}" for kw in node.keywords]
        return f"{func}({', '.join(args)})"

    if isinstance(node, ast.Name):
        mapping = {"True": "true", "False": "false", "None": "null"}
        return mapping.get(node.id, node.id)

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
                inner = _py_expr_node_to_js(v.value)
                parts.append(f"${{{inner}}}")
            else:
                parts.append(f"${{{_py_expr_node_to_js(v)}}}")
        return "`" + "".join(parts) + "`"

    if isinstance(node, ast.BinOp):
        left = _py_expr_node_to_js(node.left)
        right = _py_expr_node_to_js(node.right)
        op_map = {
            ast.Add: "+", ast.Sub: "-", ast.Mult: "*",
            ast.Div: "/", ast.Mod: "%",
        }
        op = op_map.get(type(node.op), "?")
        return f"{left} {op} {right}"

    if isinstance(node, ast.UnaryOp) and isinstance(node.op, ast.Not):
        return f"!{_py_expr_node_to_js(node.operand)}"

    if isinstance(node, ast.List):
        items = ", ".join(_py_expr_node_to_js(e) for e in node.elts)
        return f"[{items}]"

    if isinstance(node, ast.Dict):
        pairs = ", ".join(
            f"{_py_expr_node_to_js(k)}: {_py_expr_node_to_js(v)}"
            for k, v in zip(node.keys, node.values)
        )
        return "{" + pairs + "}"

    return _py_expr_to_js(ast.unparse(node), None)


def _py_stmt_to_js_line(stmt: ast.stmt) -> str:
    """Translate a single Python AST statement to a JS statement string."""
    if isinstance(stmt, ast.Expr):
        return f"{_py_expr_node_to_js(stmt.value)};"

    if isinstance(stmt, ast.Assign):
        val = _py_expr_node_to_js(stmt.value)
        target = stmt.targets[0]
        if isinstance(target, ast.Name):
            return f"const {target.id} = {val};"
        return f"{_py_expr_node_to_js(target)} = {val};"

    if isinstance(stmt, ast.AugAssign):
        target = _py_expr_node_to_js(stmt.target)
        val = _py_expr_node_to_js(stmt.value)
        op_map = {ast.Add: "+=", ast.Sub: "-=", ast.Mult: "*="}
        op = op_map.get(type(stmt.op), "+=")
        return f"{target} {op} {val};"

    if isinstance(stmt, ast.Return):
        if stmt.value is None:
            return "return;"
        return f"return {_py_expr_node_to_js(stmt.value)};"

    if isinstance(stmt, ast.If):
        cond = _py_expr_node_to_js(stmt.test)
        body = " ".join(_py_stmt_to_js_line(s) for s in stmt.body)
        if stmt.orelse:
            else_body = " ".join(_py_stmt_to_js_line(s) for s in stmt.orelse)
            return f"if ({cond}) {{ {body} }} else {{ {else_body} }}"
        return f"if ({cond}) {{ {body} }}"

    return f"/* {ast.unparse(stmt)} */"


def _transpile_async_func_to_js(source: str) -> str:
    """
    Convert an `async def` Python function to a standalone JS `async function`.
    Kept for backwards compatibility; new code should use
    _transpile_async_func_to_js_method for Alpine x-data methods.
    """
    tree = ast.parse(source)
    fn = tree.body[0]
    if not isinstance(fn, ast.AsyncFunctionDef):
        raise TypeError(f"Expected AsyncFunctionDef, got {type(fn).__name__}")

    params = ", ".join(arg.arg for arg in fn.args.args)
    body_lines = [_py_stmt_to_js_line(stmt) for stmt in fn.body]
    body = "\n    ".join(body_lines)
    return f"async function {fn.name}({params}) {{\n    {body}\n}}"


def _transpile_async_func_to_js_method(source: str, reactive_names: set[str]) -> str:
    """
    Convert an `async def` Python function to an Alpine x-data method.

    The result is a method definition (no `function` keyword) suitable for
    placement inside the object returned by a factory function:

        async handle_submit(arg) {
            const result = await some_action(this.count);
            this.set_count(result.value);
        }

    References to reactive_names (state vars and their setters) are prefixed
    with `this.` so they access the Alpine reactive object correctly.
    """
    tree = ast.parse(source)
    fn = tree.body[0]
    if not isinstance(fn, ast.AsyncFunctionDef):
        raise TypeError(f"Expected AsyncFunctionDef, got {type(fn).__name__}")

    params = ", ".join(arg.arg for arg in fn.args.args)
    body_lines = [_py_stmt_to_js_line(stmt) for stmt in fn.body]
    body = "\n    ".join(body_lines)

    # Prefix reactive names with this. — longest names first to avoid
    # partial-match issues (e.g. "count" before "set_count").
    for name in sorted(reactive_names, key=len, reverse=True):
        body = re.sub(r'\b' + re.escape(name) + r'\b', f'this.{name}', body)

    return f"async {fn.name}({params}) {{\n    {body}\n  }}"


# ── Event handler expression helpers ───────────────────────────────────────

def _substitute_scope_vars(expr: str, scope: dict, state_names: set) -> str:
    """
    Replace bare variable references in a JS expression with their
    server-side literal values (evaluated at transpile time).

    State variables are skipped so they remain as live Alpine reactive references.
    """
    for var_name, val in scope.items():
        if var_name in state_names:
            continue
        if not re.search(r'\b' + re.escape(var_name) + r'\b', expr):
            continue
        if isinstance(val, str):
            replacement = json.dumps(val)
        elif isinstance(val, bool):
            replacement = 'true' if val else 'false'
        elif val is None:
            replacement = 'null'
        elif isinstance(val, (int, float)):
            replacement = str(val)
        else:
            continue
        expr = re.sub(r'\b' + re.escape(var_name) + r'\b', replacement, expr)
    return expr
