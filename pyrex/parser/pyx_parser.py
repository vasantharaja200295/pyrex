"""
PYX File Parser

Reads a .pyx file, extracts:
- Component functions (def ComponentName():)
- use_state calls
- use_effect calls  
- server_action decorated functions
- The JSX string returned by each component
"""

import ast
import re
import textwrap
from dataclasses import dataclass, field
from pyrex.parser.preprocessor import preprocess, restore_jsx


@dataclass
class UseStateCall:
    var_name: str        # "count"
    setter_name: str     # "set_count"
    initial_value: str   # "0", "'hello'", "[]" etc as string literal


@dataclass
class UseEffectCall:
    callback_body: str   # the function body as string
    deps: list[str]      # dependency variable names


@dataclass
class LocalVar:
    name: str         # "greeting"
    expr_source: str  # Python source of the RHS, e.g. 'f"Hello, {name}"'


@dataclass
class LocalFunc:
    name: str    # "format_price"
    source: str  # full function source via ast.unparse, ready for exec()


@dataclass
class LocalAsyncFunc:
    name: str    # "handle_add"
    source: str  # full async def source via ast.unparse, ready for transpilation


@dataclass
class ComponentDef:
    name: str
    params: list[str]           # prop names
    jsx_string: str             # raw JSX string returned
    use_states: list[UseStateCall] = field(default_factory=list)
    use_effects: list[UseEffectCall] = field(default_factory=list)
    local_vars: list[LocalVar] = field(default_factory=list)
    local_funcs: list[LocalFunc] = field(default_factory=list)
    local_async_funcs: list = field(default_factory=list)  # list[LocalAsyncFunc]
    is_server: bool = False     # decorated with @server_component
    is_root: bool = False       # this is the page root
    is_layout: bool = False     # decorated with @layout
    # Page-level metadata — only meaningful when is_root is True
    page_title: str = ""        # @page(title="...")
    page_favicon: str = ""      # @page(favicon="...")  → <link rel="icon">
    page_meta: dict = field(default_factory=dict)  # @page(meta={"description": "..."})


@dataclass
class ServerAction:
    name: str
    params: list[tuple[str, str]]  # [(param_name, type_annotation), ...]
    body: str  # python source — will be exec()'d and registered as an endpoint


@dataclass
class PyxModule:
    components: list[ComponentDef] = field(default_factory=list)
    server_actions: list[ServerAction] = field(default_factory=list)
    imports: list[str] = field(default_factory=list)
    module_vars: list[str] = field(default_factory=list)  # top-level assignments (e.g. _todos = [])
    root_component: str = ""    # the component to render as the page


def parse_pyx_file(filepath: str) -> PyxModule:
    with open(filepath, encoding="utf-8") as f:
        source = f.read()
    return parse_pyx_source(source)


def parse_pyx_source(source: str) -> PyxModule:
    module = PyxModule()

    # Pre-process: extract JSX triple-quoted strings before ast.parse
    cleaned_source, jsx_store = preprocess(source)

    tree = ast.parse(cleaned_source)

    components = _extract_components(cleaned_source, jsx_store)
    module.components = components

    # Module-level imports — needed to reconstruct the exec context for server actions
    module.imports = _extract_module_imports(tree)

    # Module-level variable assignments (e.g. _todos = [], DB_PATH = "...") —
    # exec'd into the action namespace so actions can reference them
    module.module_vars = _extract_module_vars(tree)

    # @server_action decorated top-level functions
    module.server_actions = _extract_server_actions(tree)

    # The root is either decorated @page or the last component defined
    for comp in components:
        if comp.is_root:
            module.root_component = comp.name
            break
    if not module.root_component and components:
        module.root_component = components[-1].name

    return module


def _extract_components(source: str, jsx_store: dict) -> list[ComponentDef]:
    """
    Walk the source, find component functions.
    A component is any function whose name starts with uppercase
    OR is decorated with @component / @page.
    """
    tree = ast.parse(source)
    components = []


    for node in ast.walk(tree):
        if not isinstance(node, ast.FunctionDef):
            continue

        name = node.name
        decorators = [_get_decorator_name(d) for d in node.decorator_list]

        is_component = name[0].isupper() or 'component' in decorators or 'page' in decorators or 'layout' in decorators
        if not is_component:
            continue

        is_root = 'page' in decorators
        is_server = 'server_component' in decorators
        is_layout = 'layout' in decorators

        # Extract page metadata from @page(title=...) kwargs (only when is_root)
        page_title, page_favicon, page_meta = (
            _extract_page_meta(node.decorator_list) if is_root else ("", "", {})
        )

        params = [arg.arg for arg in node.args.args]

        # Extract the JSX string from the return statement
        jsx_string = _extract_jsx_return(node, source, jsx_store)

        # Extract use_state calls
        use_states = _extract_use_states(node, source)

        # Extract use_effect calls
        use_effects = _extract_use_effects(node, source)

        # Extract local variable assignments (server-side, evaluated at transpile time)
        local_vars = _extract_local_vars(node)

        # Extract inner function definitions (added to eval scope at transpile time)
        local_funcs = _extract_local_funcs(node)

        # Extract async inner functions (transpiled to client-side JS async functions)
        local_async_funcs = _extract_local_async_funcs(node)

        comp = ComponentDef(
            name=name,
            params=params,
            jsx_string=jsx_string,
            use_states=use_states,
            use_effects=use_effects,
            local_vars=local_vars,
            local_funcs=local_funcs,
            local_async_funcs=local_async_funcs,
            is_server=is_server,
            is_root=is_root,
            is_layout=is_layout,
            page_title=page_title,
            page_favicon=page_favicon,
            page_meta=page_meta,
        )
        components.append(comp)

    return components


def _extract_jsx_return(func_node: ast.FunctionDef, source: str, jsx_store: dict) -> str:
    """Find the return statement and extract the JSX string."""
    for node in ast.walk(func_node):
        if not isinstance(node, ast.Return):
            continue
        if node.value is None:
            continue

        val = node.value

        # return "..." or return placeholder
        if isinstance(val, ast.Constant) and isinstance(val.value, str):
            raw = val.value.strip()
            # Restore from jsx_store if it was a placeholder
            return restore_jsx(raw, jsx_store)

        # return ( "..." ) — wrapped in parens
        if isinstance(val, ast.Expr):
            inner = val.value
            if isinstance(inner, ast.Constant) and isinstance(inner.value, str):
                return restore_jsx(inner.value.strip(), jsx_store)

    return ""


def _extract_use_states(func_node: ast.FunctionDef, source: str) -> list[UseStateCall]:
    """
    Find lines like:
        count, set_count = use_state(0)
    """
    states = []

    for node in ast.walk(func_node):
        if not isinstance(node, ast.Assign):
            continue

        # LHS must be a tuple of two names
        if len(node.targets) != 1:
            continue
        target = node.targets[0]
        if not isinstance(target, ast.Tuple) or len(target.elts) != 2:
            continue
        if not all(isinstance(e, ast.Name) for e in target.elts):
            continue

        var_name = target.elts[0].id
        setter_name = target.elts[1].id

        # RHS must be use_state(...)
        rhs = node.value
        if not isinstance(rhs, ast.Call):
            continue
        func = rhs.func
        if isinstance(func, ast.Name) and func.id == 'use_state':
            if rhs.args:
                initial = ast.unparse(rhs.args[0])
            else:
                initial = "None"
            states.append(UseStateCall(
                var_name=var_name,
                setter_name=setter_name,
                initial_value=initial,
            ))

    return states


def _extract_use_effects(func_node: ast.FunctionDef, source: str) -> list[UseEffectCall]:
    """
    Find:
        use_effect(lambda: ..., [dep1, dep2])
    """
    effects = []

    for node in ast.walk(func_node):
        if not isinstance(node, ast.Call):
            continue
        func = node.func
        if not (isinstance(func, ast.Name) and func.id == 'use_effect'):
            continue

        # First arg: lambda or function
        callback_body = ""
        if node.args:
            callback_body = ast.unparse(node.args[0])

        # Second arg: deps list
        deps = []
        if len(node.args) > 1:
            dep_arg = node.args[1]
            if isinstance(dep_arg, ast.List):
                deps = [ast.unparse(e) for e in dep_arg.elts]

        effects.append(UseEffectCall(
            callback_body=callback_body,
            deps=deps,
        ))

    return effects


def _extract_local_vars(func_node: ast.FunctionDef) -> list[LocalVar]:
    """
    Collect simple top-level name = expr assignments that are NOT use_state /
    use_effect calls.  These are evaluated at transpile time and their values
    stamped as static strings in the HTML.

    Only top-level body statements are inspected (not nested ifs, loops, etc.)
    so the set of captured vars is predictable and deterministic.
    """
    skip_calls = {"use_state", "use_effect"}
    result = []

    for stmt in func_node.body:
        if not isinstance(stmt, ast.Assign):
            continue
        # Only single-target assignments (skip tuple unpacking like a, b = ...)
        if len(stmt.targets) != 1:
            continue
        target = stmt.targets[0]
        if not isinstance(target, ast.Name):
            continue
        name = target.id
        # Skip PascalCase (component references) and private/dunder names
        if name[0].isupper() or name.startswith("_"):
            continue
        # Skip use_state / use_effect calls — handled separately
        if isinstance(stmt.value, ast.Call):
            fn = stmt.value.func
            if isinstance(fn, ast.Name) and fn.id in skip_calls:
                continue
        result.append(LocalVar(name=name, expr_source=ast.unparse(stmt.value)))

    return result


def _extract_local_funcs(func_node: ast.FunctionDef) -> list[LocalFunc]:
    """
    Collect inner function definitions from the component body.

    Only top-level defs are captured (e.g. `def row(item): ...`).
    These are exec()'d into the eval scope before local vars are evaluated,
    so local vars and inline JSX expressions can call them freely.

    PascalCase names are skipped (those are component references, not helpers).
    """
    result = []
    for stmt in func_node.body:
        if not isinstance(stmt, ast.FunctionDef):
            continue
        name = stmt.name
        if name[0].isupper() or name.startswith("_"):
            continue
        result.append(LocalFunc(name=name, source=ast.unparse(stmt)))
    return result


def _extract_local_async_funcs(func_node: ast.FunctionDef) -> list[LocalAsyncFunc]:
    """
    Collect top-level async function definitions from the component body.

    These become client-side JS async functions — they are NOT exec()'d on the
    server. Instead, the transpiler converts them to JS `async function` blocks
    and injects them into the page alongside the __Pyrex runtime.

    PascalCase and _-prefixed names are skipped (same rules as local_funcs).
    """
    result = []
    for stmt in func_node.body:
        if not isinstance(stmt, ast.AsyncFunctionDef):
            continue
        name = stmt.name
        if name[0].isupper() or name.startswith("_"):
            continue
        result.append(LocalAsyncFunc(name=name, source=ast.unparse(stmt)))
    return result


def _extract_module_vars(tree: ast.Module) -> list[str]:
    """
    Return top-level variable assignments as source strings.

    Captures plain assignments (`x = []`) and annotated assignments
    (`x: list[str] = []`) so server actions can reference module-level
    state stores, constants, and config variables.

    Function defs, class defs, and imports are excluded — they are handled
    separately or are not relevant to the action exec context.
    """
    result = []
    for node in tree.body:
        if isinstance(node, (ast.Assign, ast.AnnAssign, ast.AugAssign)):
            result.append(ast.unparse(node))
    return result


def _extract_module_imports(tree: ast.Module) -> list[str]:
    """
    Return all top-level import statements as source strings.

    These are exec()'d into the server action namespace so actions can
    use the same libraries the .pyx file imports (sqlite3, os, etc.).
    """
    return [
        ast.unparse(node)
        for node in tree.body
        if isinstance(node, (ast.Import, ast.ImportFrom))
    ]


def _extract_server_actions(tree: ast.Module) -> list[ServerAction]:
    """
    Return all top-level @server_action decorated functions.

    Accepts both `def` and `async def` — server actions are expected to be
    async but sync functions are also registered for backwards compatibility.
    Only top-level functions are considered (not nested definitions).
    """
    actions = []
    for node in tree.body:
        if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        decorators = [_get_decorator_name(d) for d in node.decorator_list]
        if "server_action" not in decorators:
            continue
        params = [
            (arg.arg, ast.unparse(arg.annotation) if arg.annotation else "Any")
            for arg in node.args.args
        ]
        actions.append(ServerAction(name=node.name, params=params, body=ast.unparse(node)))
    return actions


def _extract_page_meta(decorator_list: list) -> tuple[str, str, dict]:
    """
    Parse keyword arguments from a @page(...) decorator.

    Returns (title, favicon, meta_dict).  Only string constant values are
    accepted — dynamic expressions are ignored so the parser stays simple.

    Supported kwargs:
      title   — str, sets the <title> tag
      favicon — str, href for <link rel="icon">
      meta    — dict[str, str], each key/value becomes <meta name="k" content="v">

    Example:
        @page(
            title="Home — My App",
            favicon="/favicon.ico",
            meta={"description": "Welcome", "og:image": "/og.png"},
        )
    """
    for d in decorator_list:
        if not isinstance(d, ast.Call):
            continue
        if _get_decorator_name(d) != 'page':
            continue

        title = ""
        favicon = ""
        meta: dict[str, str] = {}

        for kw in d.keywords:
            if kw.arg == 'title' and isinstance(kw.value, ast.Constant):
                title = str(kw.value.value)
            elif kw.arg == 'favicon' and isinstance(kw.value, ast.Constant):
                favicon = str(kw.value.value)
            elif kw.arg == 'meta' and isinstance(kw.value, ast.Dict):
                for k, v in zip(kw.value.keys, kw.value.values):
                    if isinstance(k, ast.Constant) and isinstance(v, ast.Constant):
                        meta[str(k.value)] = str(v.value)

        return title, favicon, meta

    return "", "", {}


def _get_decorator_name(decorator) -> str:
    if isinstance(decorator, ast.Name):
        return decorator.id
    if isinstance(decorator, ast.Attribute):
        return decorator.attr
    if isinstance(decorator, ast.Call):
        # @page(title="...") — the decorator is a Call whose func is the real name
        return _get_decorator_name(decorator.func)
    return ""
