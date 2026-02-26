"""
Pyrex Transpiler

Takes a PyxModule (parsed .pyx file) and outputs a complete HTML page.

Pipeline:
  ComponentDef  →  resolve JSX  →  HTML string
  use_state     →  JS __state + __setState runtime
  use_effect    →  JS effect runner
  onClick etc   →  JS event binding
  {expr}        →  interpolated value or data-bind attribute
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
        
        # Collect all JS needed
        js_runtime = self._build_js_runtime()
        component_js = self._build_all_component_js()
        proxy_js = self._build_server_action_proxies()

        # Render root HTML
        body_html = self._render_component(root, props={})

        return self._wrap_html_page(body_html, js_runtime + "\n" + component_js + "\n" + proxy_js)

    def _render_component(self, component: ComponentDef, props: dict) -> str:
        """Render a component to HTML string."""
        if not component.jsx_string:
            return f"<!-- {component.name}: no JSX returned -->"

        # Replace prop references in JSX with their values
        jsx = component.jsx_string
        for prop_name, prop_val in props.items():
            jsx = jsx.replace(f"{{{prop_name}}}", str(prop_val))

        # Build server-side scope: props + inner functions + evaluated local variables
        state_names = {s.var_name for s in component.use_states}
        local_scope: dict = dict(props)

        def _globs():
            # Generator expressions / comprehensions inside eval() look up names in
            # the *globals* dict, not locals.  Merging local_scope in ensures that
            # functions defined via exec() and variables already evaluated are visible
            # to generators, list comps, and other nested scopes.
            return {"__builtins__": __builtins__, **local_scope}

        # Exec inner function defs first so local vars (and inline exprs) can call them
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
                pass  # falls back to <span data-expr> at render time

        # Substitute simple {var_name} references that are not state vars
        for var_name, val in local_scope.items():
            if var_name not in state_names:
                jsx = jsx.replace(f"{{{var_name}}}", str(val))

        # Store scope so _node_to_html can eval complex expressions like {x.upper()}
        self._render_scope = local_scope

        # For state variables, inject placeholder spans that JS will hydrate
        for state in component.use_states:
            initial = _py_value_to_js_literal(state.initial_value)
            # Replace {count} with a span that JS controls
            jsx = re.sub(
                rf'\{{{re.escape(state.var_name)}\}}',
                f'<span data-state="{state.var_name}" data-component="{component.name}">{_strip_quotes(state.initial_value)}</span>',
                jsx
            )

        # Parse the JSX and render to HTML
        try:
            ast_node = parse_jsx(jsx)
            html = self._node_to_html(ast_node, component)
        except Exception as e:
            html = f'<div style="color:red;padding:1rem;">JSX Parse Error in {component.name}: {e}</div>'

        return html

    def _node_to_html(self, node, component: ComponentDef) -> str:
        if isinstance(node, TextNode):
            if node.is_expression:
                # Try to evaluate against the server-side scope (handles {x.upper()} etc.)
                scope = getattr(self, "_render_scope", None)
                if scope is not None:
                    try:
                        result = eval(
                            compile(node.content, "<pyrex>", "eval"),
                            {"__builtins__": __builtins__, **scope},
                            scope,
                        )
                        result_str = str(result)
                        # If the result is already markup, inject raw (list rendering,
                        # conditional HTML blocks, helper functions that return tags).
                        if result_str.lstrip().startswith("<"):
                            return result_str
                        return _escape_html(result_str)
                    except Exception:
                        pass
                return f'<span data-expr="{node.content}"></span>'
            return _escape_html(node.content)

        if isinstance(node, JSXNode):
            # Is this a component reference?
            if node.component and node.tag in self.component_map:
                child_comp = self.component_map[node.tag]
                # Resolve props
                child_props = {}
                for k, v in node.props.items():
                    if isinstance(v, str) and v.startswith('_expr_:'):
                        child_props[k] = v[7:]  # pass expression as-is for now
                    else:
                        child_props[k] = v
                return self._render_component(child_comp, child_props)

            # Regular HTML element
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
        """Convert JSX props to HTML attributes, translating event handlers."""
        parts = []
        state_names = {s.var_name for s in component.use_states}
        setter_names = {s.setter_name: s.var_name for s in component.use_states}

        scope = getattr(self, "_render_scope", {})
        state_names = {s.var_name for s in component.use_states}

        for key, val in props.items():
            # Event handler props (camelCase: onClick, onInput, onKeyDown …)
            if key.startswith('on') and len(key) > 2 and key[2].isupper():
                html_event = _jsx_event_to_html(key)
                if isinstance(val, str) and val.startswith('_expr_:'):
                    expr = val[7:]
                    try:
                        js_expr = _handle_event_expr(expr, component, scope, state_names)
                    except ValueError as e:
                        raise ValueError(f"In component {component.name!r}: {e}") from e
                    parts.append(f'{html_event}="{js_expr}"')
                else:
                    parts.append(f'{html_event}="{val}"')
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
                # Try to resolve as a server-side expression first
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
                # Fall back to JS expression translation
                js_val = _py_expr_to_js(expr, component)
                parts.append(f'{key}="{js_val}"')
            else:
                parts.append(f'{key}="{val}"')

        return (" " + " ".join(parts)) if parts else ""

    def _build_js_runtime(self) -> str:
        """The core Pyrex JS runtime — state management, effects, bindings."""
        return """
<script>
// ── Pyrex Runtime ──────────────────────────────────────────────────────────
const __Pyrex = {
  state: {},
  effects: [],
  listeners: {},

  setState(component, key, value) {
    const stateKey = component + '.' + key;
    this.state[stateKey] = value;

    // Update all DOM nodes bound to this state
    document.querySelectorAll(
      `[data-state="${key}"][data-component="${component}"]`
    ).forEach(el => {
      el.textContent = Array.isArray(value) ? value.join(', ') : value;
    });

    // Update input values
    document.querySelectorAll(
      `[data-bind="${key}"][data-component="${component}"]`
    ).forEach(el => {
      el.value = value;
    });

    // Run effects that depend on this key
    this.runEffects(component, key);
  },

  getState(component, key) {
    return this.state[component + '.' + key];
  },

  registerEffect(component, deps, fn) {
    this.effects.push({ component, deps, fn });
    fn(); // run immediately (like useEffect with deps)
  },

  runEffects(component, changedKey) {
    this.effects.forEach(effect => {
      if (effect.component === component && effect.deps.includes(changedKey)) {
        effect.fn();
      }
    });
  },

  init() {
    // Initialize state from data-state-init attributes
    document.querySelectorAll('[data-state-init]').forEach(el => {
      const component = el.dataset.component;
      const key = el.dataset.stateInit;
      const raw = el.dataset.stateValue;
      let value;
      try { value = JSON.parse(raw); } catch { value = raw; }
      this.state[component + '.' + key] = value;
    });
  }
};

document.addEventListener('DOMContentLoaded', () => __Pyrex.init());

// ───────────────────────────────────────────────────────────────────────────
</script>"""

    def _build_server_action_proxies(self) -> str:
        """
        Generate one named async JS function per @server_action.

        Each proxy calls POST /__pyrex/ with {"i": action_id, "a": params}
        and returns the parsed JSON response. The action ID is the function
        name in dev mode or a sha256 hash in production mode.
        Developers call these functions directly from event handlers:

            onclick="add_todo(input_val).then(d => set_count(d.count))"
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

    def _build_all_component_js(self) -> str:
        """Generate JS for each component: async funcs first, then state/effects."""
        scripts = []
        for comp in self.module.components:
            # Async client-side functions emitted BEFORE state so that setters
            # called inside them are already defined when DOMContentLoaded fires.
            async_js = self._build_component_async_funcs(comp)
            if async_js:
                scripts.append(async_js)
            js = self._build_component_js(comp)
            if js:
                scripts.append(js)
        return "\n".join(scripts)

    def _build_component_async_funcs(self, component: ComponentDef) -> str:
        """
        Transpile async def functions from the component body to JS async functions.

        Each async def inside a @page/@component is a CLIENT-SIDE function —
        it runs in the browser. The transpiler converts it to a JS async function
        and injects it into the page as a <script> block.
        """
        if not component.local_async_funcs:
            return ""
        fns = []
        for laf in component.local_async_funcs:
            try:
                fns.append(_transpile_async_func_to_js(laf.source))
            except Exception as e:
                fns.append(f"/* async func {laf.name} failed to transpile: {e} */")
        return "\n<script>\n" + "\n\n".join(fns) + "\n</script>"

    def _build_component_js(self, component: ComponentDef) -> str:
        if not component.use_states and not component.use_effects:
            return ""

        lines = [f"\n<script>"]
        lines.append(f"// Component: {component.name}")
        lines.append(f"document.addEventListener('DOMContentLoaded', function() {{")

        # Initialize state
        for state in component.use_states:
            initial_js = _py_value_to_js_literal(state.initial_value)
            lines.append(f"  __Pyrex.state['{component.name}.{state.var_name}'] = {initial_js};")
            # Expose setter as a global function components can call
            lines.append(
                f"  window.{state.setter_name} = function(val) {{ "
                f"__Pyrex.setState('{component.name}', '{state.var_name}', val); }};"
            )
            # Expose getter
            lines.append(
                f"  Object.defineProperty(window, '{state.var_name}', {{ "
                f"get() {{ return __Pyrex.getState('{component.name}', '{state.var_name}'); }}, "
                f"configurable: true }});"
            )

        # Register effects
        for effect in component.use_effects:
            deps_js = json.dumps(effect.deps)
            # Translate Python lambda body to JS
            js_body = _py_expr_to_js(effect.callback_body, component)
            lines.append(
                f"  __Pyrex.registerEffect('{component.name}', {deps_js}, function() {{ {js_body}; }});"
            )

        lines.append("});")
        lines.append("</script>")

        return "\n".join(lines)

    def _wrap_html_page(self, body: str, scripts: str) -> str:
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
  {scripts.replace('<script>', '<script>', 1) if '<script>' in scripts else ''}
</head>
<body>
{body}
</body>
</html>"""

    def _next_id(self) -> str:
        self._id_counter += 1
        return f"pyrex-{self._id_counter}"


# ── Helpers ────────────────────────────────────────────────────────────────

def _jsx_event_to_html(jsx_event: str) -> str:
    """onClick → onclick, onChange → oninput, onSubmit → onsubmit"""
    mapping = {
        'onClick': 'onclick',
        'onChange': 'oninput',
        'onSubmit': 'onsubmit',
        'onInput': 'oninput',
        'onKeyDown': 'onkeydown',
        'onKeyUp': 'onkeyup',
        'onFocus': 'onfocus',
        'onBlur': 'onblur',
        'onMouseEnter': 'onmouseenter',
        'onMouseLeave': 'onmouseleave',
    }
    return mapping.get(jsx_event, jsx_event.lower())


def _py_expr_to_js(expr: str, component: ComponentDef) -> str:
    """
    Best-effort Python expression → JavaScript expression.
    Handles the common patterns that appear in event handlers.
    """
    # lambda: body  →  body
    if expr.startswith('lambda:'):
        expr = expr[7:].strip()
    elif re.match(r'^lambda\s+\w+:', expr):
        _, body = expr.split(':', 1)
        expr = body.strip()

    # not x → !x
    expr = re.sub(r'\bnot\s+(\w+)', r'!\1', expr)

    # True/False → true/false
    expr = expr.replace('True', 'true').replace('False', 'false')
    expr = expr.replace('None', 'null')

    # f"...{var}..." → `...${var}...`  (basic f-string)
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
        return py_val  # close enough for basic cases
    if py_val.startswith(("'", '"')):
        # Python string → JS string
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

    Uses the Python AST directly (not string-based regex) for correctness.
    Covers the patterns that appear in async component functions.
    """
    if isinstance(node, ast.Await):
        return f"await {_py_expr_node_to_js(node.value)}"

    if isinstance(node, ast.Call):
        # Python builtins → JS equivalents
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
            return f"{obj}.{slc.value}"   # obj["key"] → obj.key
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

    # Fallback: unparse back to string and run through the regex-based converter
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

    # Unknown statement — emit a comment so the function still parses
    return f"/* {ast.unparse(stmt)} */"


def _transpile_async_func_to_js(source: str) -> str:
    """
    Convert an `async def` Python function to a JS `async function`.

    Used for client-side async functions defined inside @page/@component bodies.
    The body is translated statement-by-statement using the AST.
    """
    tree = ast.parse(source)
    fn = tree.body[0]
    if not isinstance(fn, ast.AsyncFunctionDef):
        raise TypeError(f"Expected AsyncFunctionDef, got {type(fn).__name__}")

    params = ", ".join(arg.arg for arg in fn.args.args)
    body_lines = [_py_stmt_to_js_line(stmt) for stmt in fn.body]
    body = "\n    ".join(body_lines)
    return f"async function {fn.name}({params}) {{\n    {body}\n}}"


# ── Event handler expression helpers ───────────────────────────────────────

def _handle_event_expr(
    expr: str,
    component,
    scope: dict | None = None,
    state_names: set | None = None,
) -> str:
    """
    Translate an event-handler JSX expression to an inline JS string.

    Pattern 1 — bare function reference:
        {handle_add}  →  "handle_add()"

    Pattern 2 — lambda with no event parameter:
        {lambda: delete_item(item_id)}  →  "delete_item('abc')"
        Server-side variables in scope are stamped as literals; state vars
        are left as JS getter references.

    Pattern 3 — lambda with an event parameter:
        {lambda e: set_name(e.target.value)}  →  "set_name(event.target.value)"
        The parameter name is replaced with the standard JS 'event' identifier.

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
        body = re.sub(r'\b' + re.escape(param_name) + r'\b', 'event', body)
        return _py_expr_to_js(body, component)

    # Pattern 2: lambda: <body>  (no parameter — server vars get stamped)
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


def _substitute_scope_vars(expr: str, scope: dict, state_names: set) -> str:
    """
    Replace bare variable references in a JS expression with their
    server-side literal values (evaluated at transpile time).

    State variables are skipped so they remain as live JS getter references.
    """
    for var_name, val in scope.items():
        if var_name in state_names:
            continue
        if not re.search(r'\b' + re.escape(var_name) + r'\b', expr):
            continue
        if isinstance(val, str):
            replacement = json.dumps(val)       # properly quoted JS string
        elif isinstance(val, bool):
            replacement = 'true' if val else 'false'
        elif val is None:
            replacement = 'null'
        elif isinstance(val, (int, float)):
            replacement = str(val)
        else:
            continue                            # skip complex objects
        expr = re.sub(r'\b' + re.escape(var_name) + r'\b', replacement, expr)
    return expr
