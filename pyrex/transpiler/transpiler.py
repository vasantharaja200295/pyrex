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

import re
import json
from pyrex.parser.pyx_parser import PyxModule, ComponentDef, UseStateCall, UseEffectCall
from pyrex.parser.jsx_parser import parse_jsx, JSXNode, TextNode


class Transpiler:
    def __init__(self, module: PyxModule):
        self.module = module
        self.component_map = {c.name: c for c in module.components}
        self._id_counter = 0

    def transpile(self) -> str:
        """Entry point — returns complete HTML page as string."""
        root_name = self.module.root_component
        if not root_name:
            raise ValueError("No root component found. Decorate one with @page or name it with uppercase.")

        root = self.component_map[root_name]
        
        # Collect all JS needed
        js_runtime = self._build_js_runtime()
        component_js = self._build_all_component_js()
        
        # Render root HTML
        body_html = self._render_component(root, props={})

        return self._wrap_html_page(body_html, js_runtime + "\n" + component_js)

    def _render_component(self, component: ComponentDef, props: dict) -> str:
        """Render a component to HTML string."""
        if not component.jsx_string:
            return f"<!-- {component.name}: no JSX returned -->"

        # Replace prop references in JSX with their values
        jsx = component.jsx_string
        for prop_name, prop_val in props.items():
            jsx = jsx.replace(f"{{{prop_name}}}", str(prop_val))

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
                # Unresolved expression at render time — leave as comment for now
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

        for key, val in props.items():
            # Event handler props
            if key.startswith('on') and key[2].isupper():
                html_event = _jsx_event_to_html(key)
                if isinstance(val, str) and val.startswith('_expr_:'):
                    expr = val[7:]
                    js_expr = _py_expr_to_js(expr, component)
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

    def _build_all_component_js(self) -> str:
        """Generate JS for each component's state and effects."""
        scripts = []
        for comp in self.module.components:
            js = self._build_component_js(comp)
            if js:
                scripts.append(js)
        return "\n".join(scripts)

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
