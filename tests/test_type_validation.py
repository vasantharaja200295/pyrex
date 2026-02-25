"""
Tests: _coerce_type — type coercion for server action parameters.
"""
import pytest
from pyrex.engine import _coerce_type


# ── Successful coercions ──────────────────────────────────────────────────────

def test_str_passthrough():
    assert _coerce_type("hello", "str") == "hello"


def test_int_from_int():
    assert _coerce_type(42, "int") == 42


def test_int_from_float():
    # float → int is valid Python: int(3.9) == 3
    assert _coerce_type(3.9, "int") == 3


def test_float_from_int():
    assert _coerce_type(5, "float") == 5.0
    assert isinstance(_coerce_type(5, "float"), float)


def test_float_from_string():
    assert _coerce_type("3.14", "float") == pytest.approx(3.14)


def test_int_from_string():
    assert _coerce_type("7", "int") == 7


def test_bool_from_bool():
    assert _coerce_type(True, "bool") is True
    assert _coerce_type(False, "bool") is False


def test_list_from_list():
    assert _coerce_type([1, 2, 3], "list") == [1, 2, 3]


def test_dict_from_dict():
    assert _coerce_type({"a": 1}, "dict") == {"a": 1}


def test_str_from_number():
    assert _coerce_type(42, "str") == "42"


# ── Unknown type — value passed through unchanged ─────────────────────────────

def test_unknown_type_passthrough():
    assert _coerce_type("anything", "unknown_type") == "anything"
    assert _coerce_type(99, "MyModel") == 99


# ── Failed coercions raise ValueError or TypeError ───────────────────────────

def test_int_from_non_numeric_string_raises():
    with pytest.raises((ValueError, TypeError)):
        _coerce_type("not-a-number", "int")


def test_float_from_non_numeric_string_raises():
    with pytest.raises((ValueError, TypeError)):
        _coerce_type("hello", "float")


def test_list_from_non_iterable_raises():
    with pytest.raises(TypeError):
        _coerce_type(42, "list")


def test_dict_from_list_raises():
    with pytest.raises((ValueError, TypeError)):
        _coerce_type([1, 2], "dict")
