"""
Tests: Part 4 — Async Local Function Transpilation.

Covers:
- Parser: async def inside component body → LocalAsyncFunc
- Transpiler: _transpile_async_func_to_js / _py_expr_node_to_js / _py_stmt_to_js_line
- Integration: async funcs appear in built HTML
"""
import ast
import pytest
from pyrex.parser.pyx_parser import parse_pyx_source, LocalAsyncFunc
from pyrex.transpiler.transpiler import (
    _transpile_async_func_to_js,
    _py_expr_node_to_js,
    _py_stmt_to_js_line,
    Transpiler,
)


# ── Parser: detection ─────────────────────────────────────────────────────────

COMPONENT_WITH_ASYNC = """
from pyrex import page

@page
def App():
    async def handle_add():
        data = await add_todo(text)
        set_todos(data)
    return '<div></div>'
"""

COMPONENT_WITH_SYNC_AND_ASYNC = """
from pyrex import page

@page
def App():
    def helper():
        return 1
    async def handle_submit():
        result = await submit()
        set_result(result)
    return '<div></div>'
"""

COMPONENT_NO_ASYNC = """
from pyrex import page

@page
def App():
    return '<div></div>'
"""


def test_async_func_detected():
    mod = parse_pyx_source(COMPONENT_WITH_ASYNC)
    comp = mod.components[0]
    assert len(comp.local_async_funcs) == 1
    assert isinstance(comp.local_async_funcs[0], LocalAsyncFunc)


def test_async_func_name():
    mod = parse_pyx_source(COMPONENT_WITH_ASYNC)
    assert mod.components[0].local_async_funcs[0].name == "handle_add"


def test_async_func_source_is_string():
    mod = parse_pyx_source(COMPONENT_WITH_ASYNC)
    src = mod.components[0].local_async_funcs[0].source
    assert isinstance(src, str)
    assert "handle_add" in src


def test_sync_func_not_in_local_async_funcs():
    mod = parse_pyx_source(COMPONENT_WITH_SYNC_AND_ASYNC)
    comp = mod.components[0]
    async_names = [laf.name for laf in comp.local_async_funcs]
    assert "helper" not in async_names
    assert "handle_submit" in async_names


def test_sync_func_still_in_local_funcs():
    mod = parse_pyx_source(COMPONENT_WITH_SYNC_AND_ASYNC)
    comp = mod.components[0]
    sync_names = [lf.name for lf in comp.local_funcs]
    assert "helper" in sync_names


def test_no_async_funcs_empty_list():
    mod = parse_pyx_source(COMPONENT_NO_ASYNC)
    assert mod.components[0].local_async_funcs == []


def test_pascal_case_async_skipped():
    source = """
from pyrex import page

@page
def App():
    async def HandleClick():
        pass
    return '<div></div>'
"""
    mod = parse_pyx_source(source)
    assert mod.components[0].local_async_funcs == []


def test_underscore_async_skipped():
    source = """
from pyrex import page

@page
def App():
    async def _private():
        pass
    return '<div></div>'
"""
    mod = parse_pyx_source(source)
    assert mod.components[0].local_async_funcs == []


# ── _transpile_async_func_to_js ───────────────────────────────────────────────

def test_basic_async_function():
    src = "async def handle_add():\n    data = await add_todo(text)\n    set_todos(data)"
    js = _transpile_async_func_to_js(src)
    assert "async function handle_add()" in js
    assert "await add_todo(text)" in js
    assert "set_todos(data)" in js


def test_async_function_with_params():
    src = "async def fetch_data(url, token):\n    result = await get(url)\n    return result"
    js = _transpile_async_func_to_js(src)
    assert "async function fetch_data(url, token)" in js


def test_async_function_return():
    src = "async def greet():\n    return 'hello'"
    js = _transpile_async_func_to_js(src)
    assert "return" in js
    assert '"hello"' in js


def test_async_function_raises_on_sync_def():
    src = "def sync_fn():\n    pass"
    with pytest.raises(TypeError):
        _transpile_async_func_to_js(src)


# ── _py_expr_node_to_js — expression mappings ────────────────────────────────

def _expr(src: str) -> ast.expr:
    return ast.parse(src, mode="eval").body


def test_await_mapping():
    node = _expr("await add_todo(text)")
    assert _py_expr_node_to_js(node) == "await add_todo(text)"


def test_name_true():
    assert _py_expr_node_to_js(_expr("True")) == "true"


def test_name_false():
    assert _py_expr_node_to_js(_expr("False")) == "false"


def test_name_none():
    assert _py_expr_node_to_js(_expr("None")) == "null"


def test_name_passthrough():
    assert _py_expr_node_to_js(_expr("my_var")) == "my_var"


def test_constant_string():
    assert _py_expr_node_to_js(_expr('"hello"')) == '"hello"'


def test_constant_int():
    assert _py_expr_node_to_js(_expr("42")) == "42"


def test_attribute():
    assert _py_expr_node_to_js(_expr("obj.prop")) == "obj.prop"


def test_call_simple():
    assert _py_expr_node_to_js(_expr("fn(x)")) == "fn(x)"


def test_len_to_length():
    assert _py_expr_node_to_js(_expr("len(items)")) == "items.length"


def test_print_to_console_log():
    assert _py_expr_node_to_js(_expr("print(x, y)")) == "console.log(x, y)"


def test_not_operator():
    assert _py_expr_node_to_js(_expr("not x")) == "!x"


def test_binop_add():
    result = _py_expr_node_to_js(_expr("a + b"))
    assert result == "a + b"


def test_list_literal():
    result = _py_expr_node_to_js(_expr("[1, 2, 3]"))
    assert result == "[1, 2, 3]"


def test_fstring():
    result = _py_expr_node_to_js(_expr('f"Hello {name}"'))
    assert result == "`Hello ${name}`"


def test_subscript_string_key():
    result = _py_expr_node_to_js(_expr('data["key"]'))
    assert result == "data.key"


def test_subscript_int_key():
    result = _py_expr_node_to_js(_expr("items[0]"))
    assert "items" in result
    assert "0" in result


# ── _py_stmt_to_js_line — statement mappings ─────────────────────────────────

def _stmt(src: str) -> ast.stmt:
    return ast.parse(src).body[0]


def test_expr_stmt():
    result = _py_stmt_to_js_line(_stmt("set_count(1)"))
    assert result == "set_count(1);"


def test_assign_const():
    result = _py_stmt_to_js_line(_stmt("data = await fetch_data()"))
    assert "const data" in result
    assert "await fetch_data()" in result


def test_return_value():
    result = _py_stmt_to_js_line(_stmt("return result"))
    assert result == "return result;"


def test_return_none():
    result = _py_stmt_to_js_line(_stmt("return"))
    assert result == "return;"


def test_if_stmt():
    result = _py_stmt_to_js_line(_stmt("if x:\n    do_thing()"))
    assert "if (x)" in result
    assert "do_thing()" in result


def test_if_else_stmt():
    result = _py_stmt_to_js_line(_stmt("if x:\n    a()\nelse:\n    b()"))
    assert "else" in result
    assert "a()" in result
    assert "b()" in result


# ── Integration: async funcs appear in built HTML ─────────────────────────────

def test_async_func_in_built_html():
    source = """
from pyrex import page, server_action

@server_action
async def add_item(text: str):
    return [text]

@page
def App():
    async def handle_add():
        data = await add_item(text)
        set_items(data)
    return '<div></div>'
"""
    mod = parse_pyx_source(source)
    t = Transpiler(mod)
    html = t.transpile()
    assert "async function handle_add()" in html
    assert "await add_item(text)" in html
    assert "set_items(data)" in html


def test_async_func_is_global_not_inside_domcontentloaded():
    source = """
from pyrex import page, use_state

@page
def App():
    count, set_count = use_state(0)
    async def increment():
        set_count(count + 1)
    return '<div></div>'
"""
    mod = parse_pyx_source(source)
    t = Transpiler(mod)
    html = t.transpile()

    # The async function must appear in the HTML
    assert "async function increment" in html

    # The async function must NOT be defined inside a DOMContentLoaded callback —
    # it is a global function, called by event handlers after page load.
    # Verify by checking that the pattern "DOMContentLoaded" does NOT appear
    # between the opening of the async function and its closing brace.
    import re as _re
    match = _re.search(
        r'async function increment\(\).*?\n\}',
        html, _re.DOTALL
    )
    assert match is not None, "async function increment not found"
    fn_body = match.group(0)
    assert "DOMContentLoaded" not in fn_body
