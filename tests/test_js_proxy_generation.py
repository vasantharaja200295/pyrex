"""
Tests: _build_server_action_proxies — named JS async functions per @server_action.
"""
from pyrex.parser.pyx_parser import parse_pyx_source
from pyrex.transpiler.transpiler import Transpiler


def _proxies(source: str) -> str:
    """Parse source and return the generated proxy JS string."""
    mod = parse_pyx_source(source)
    t = Transpiler(mod)
    return t._build_server_action_proxies()


# ── No actions → empty string ─────────────────────────────────────────────────

def test_no_actions_returns_empty():
    source = "from pyrex import page\n\n@page\ndef App():\n    return '<div>hi</div>'"
    assert _proxies(source) == ""


# ── Single action ─────────────────────────────────────────────────────────────

def test_proxy_function_is_async():
    source = """
from pyrex import server_action

@server_action
async def greet(name: str):
    return {"msg": f"Hi {name}"}
"""
    js = _proxies(source)
    assert "async function greet" in js


def test_proxy_param_list_matches_action():
    source = """
from pyrex import server_action

@server_action
async def get_greeting(name: str, language: str):
    return {}
"""
    js = _proxies(source)
    assert "async function get_greeting(name, language)" in js


def test_proxy_url_contains_actions_path():
    source = """
from pyrex import server_action

@server_action
async def add_todo(text: str):
    return []
"""
    js = _proxies(source)
    assert "/__pyrex/actions/add_todo" in js


def test_proxy_uses_post_method():
    source = """
from pyrex import server_action

@server_action
async def save(data: str):
    return {}
"""
    js = _proxies(source)
    assert "'POST'" in js or '"POST"' in js


def test_proxy_json_stringifies_params():
    source = """
from pyrex import server_action

@server_action
async def search(query: str, limit: int):
    return []
"""
    js = _proxies(source)
    # The body should JSON.stringify the named params
    assert "JSON.stringify" in js
    assert "query" in js
    assert "limit" in js


def test_proxy_no_params_sends_empty_object():
    source = """
from pyrex import server_action

@server_action
async def clear():
    return []
"""
    js = _proxies(source)
    assert "async function clear()" in js
    assert "{}" in js


def test_proxy_returns_res_json():
    source = """
from pyrex import server_action

@server_action
async def fetch_data():
    return {}
"""
    js = _proxies(source)
    assert "res.json()" in js


# ── Multiple actions ──────────────────────────────────────────────────────────

def test_multiple_proxies_generated():
    source = """
from pyrex import server_action

@server_action
async def action_a(x: str):
    return x

@server_action
async def action_b(y: int):
    return y
"""
    js = _proxies(source)
    assert "async function action_a" in js
    assert "async function action_b" in js


def test_proxies_wrapped_in_script_tag():
    source = """
from pyrex import server_action

@server_action
async def ping():
    return "pong"
"""
    js = _proxies(source)
    assert "<script>" in js
