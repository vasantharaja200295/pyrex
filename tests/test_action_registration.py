"""
Tests: @server_action parsing — name, params, and async detection.
"""
import pytest
from pyrex.parser.pyx_parser import parse_pyx_source, ServerAction


SOURCE_SYNC = """
from pyrex import server_action

@server_action
def greet(name: str) -> str:
    return f"Hello, {name}"
"""

SOURCE_ASYNC = """
from pyrex import server_action

@server_action
async def add_todo(text: str):
    return [text]
"""

SOURCE_NO_PARAMS = """
from pyrex import server_action

@server_action
async def clear():
    return []
"""

SOURCE_MULTI_PARAMS = """
from pyrex import server_action

@server_action
async def get_greeting(name: str, language: str):
    return {"greeting": f"Hello, {name}"}
"""

SOURCE_UNTYPED_PARAM = """
from pyrex import server_action

@server_action
async def echo(value):
    return value
"""


def test_sync_action_registered():
    mod = parse_pyx_source(SOURCE_SYNC)
    assert len(mod.server_actions) == 1
    action = mod.server_actions[0]
    assert isinstance(action, ServerAction)
    assert action.name == "greet"


def test_async_action_registered():
    mod = parse_pyx_source(SOURCE_ASYNC)
    assert len(mod.server_actions) == 1
    assert mod.server_actions[0].name == "add_todo"


def test_action_params_extracted():
    mod = parse_pyx_source(SOURCE_MULTI_PARAMS)
    action = mod.server_actions[0]
    assert action.params == [("name", "str"), ("language", "str")]


def test_no_params_action():
    mod = parse_pyx_source(SOURCE_NO_PARAMS)
    action = mod.server_actions[0]
    assert action.name == "clear"
    assert action.params == []


def test_untyped_param_defaults_to_any():
    mod = parse_pyx_source(SOURCE_UNTYPED_PARAM)
    action = mod.server_actions[0]
    assert action.params == [("value", "Any")]


def test_action_body_is_string():
    mod = parse_pyx_source(SOURCE_ASYNC)
    action = mod.server_actions[0]
    assert isinstance(action.body, str)
    assert "add_todo" in action.body


def test_multiple_actions():
    source = """
from pyrex import server_action

@server_action
async def action_a(x: int):
    return x

@server_action
async def action_b(y: str):
    return y
"""
    mod = parse_pyx_source(source)
    names = [a.name for a in mod.server_actions]
    assert "action_a" in names
    assert "action_b" in names
    assert len(mod.server_actions) == 2


def test_non_action_functions_excluded():
    source = """
from pyrex import page, server_action

def helper():
    pass

@server_action
async def real_action():
    return []
"""
    mod = parse_pyx_source(source)
    names = [a.name for a in mod.server_actions]
    assert "helper" not in names
    assert "real_action" in names
