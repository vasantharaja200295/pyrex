"""
Tests: _handle_event_expr — three event-handler patterns + error case.
"""
import pytest
from pyrex.transpiler.transpiler import _handle_event_expr
from pyrex.parser.pyx_parser import parse_pyx_source, ComponentDef


def _dummy_component() -> ComponentDef:
    """Return a bare ComponentDef for tests that don't need a real component."""
    mod = parse_pyx_source(
        "from pyrex import page\n\n@page\ndef App():\n    return '<div></div>'"
    )
    return mod.components[0]


comp = _dummy_component()


# ── Pattern 1: bare name → name() ────────────────────────────────────────────

def test_pattern1_bare_name():
    result = _handle_event_expr("handle_click", comp)
    assert result == "handle_click()"


def test_pattern1_underscore_name():
    result = _handle_event_expr("on_submit", comp)
    assert result == "on_submit()"


def test_pattern1_camel_name():
    result = _handle_event_expr("handleAdd", comp)
    assert result == "handleAdd()"


# ── Pattern 2: lambda: body → body with scope vars substituted ────────────────

def test_pattern2_lambda_no_arg():
    result = _handle_event_expr("lambda: delete_item()", comp)
    assert "delete_item()" in result
    assert "lambda" not in result


def test_pattern2_scope_var_substituted():
    scope = {"item_id": "abc123"}
    state_names = set()
    result = _handle_event_expr("lambda: delete_item(item_id)", comp, scope, state_names)
    # item_id should be replaced with the JSON-quoted literal "abc123"
    assert "abc123" in result
    assert "item_id" not in result


def test_pattern2_state_var_not_substituted():
    scope = {"count": 5}
    state_names = {"count"}
    result = _handle_event_expr("lambda: increment(count)", comp, scope, state_names)
    # state vars remain as JS references
    assert "count" in result


def test_pattern2_string_scope_var_json_quoted():
    scope = {"status": "active"}
    result = _handle_event_expr("lambda: filter(status)", comp, scope, set())
    assert '"active"' in result


def test_pattern2_bool_scope_var_converted():
    scope = {"enabled": True}
    result = _handle_event_expr("lambda: toggle(enabled)", comp, scope, set())
    assert "true" in result


def test_pattern2_int_scope_var_converted():
    scope = {"page_num": 3}
    result = _handle_event_expr("lambda: go_to(page_num)", comp, scope, set())
    assert "3" in result


# ── Pattern 3: lambda <param>: body → param replaced with 'event' ────────────

def test_pattern3_lambda_with_param():
    result = _handle_event_expr("lambda e: set_name(e.target.value)", comp)
    assert "event" in result
    assert "e.target" not in result
    assert "event.target" in result


def test_pattern3_any_param_name_replaced():
    result = _handle_event_expr("lambda ev: handle(ev.key)", comp)
    assert "event.key" in result
    assert "ev.key" not in result


def test_pattern3_lambda_not_present_in_output():
    result = _handle_event_expr("lambda e: do_thing(e.target.value)", comp)
    assert "lambda" not in result


# ── Error case: direct call raises ValueError ─────────────────────────────────

def test_error_direct_call_raises():
    with pytest.raises(ValueError, match="calls"):
        _handle_event_expr("handle_add()", comp)


def test_error_message_includes_function_name():
    with pytest.raises(ValueError) as exc_info:
        _handle_event_expr("submit_form()", comp)
    assert "submit_form" in str(exc_info.value)


def test_error_direct_call_with_args_raises():
    with pytest.raises(ValueError):
        _handle_event_expr("delete_item(item_id)", comp)
