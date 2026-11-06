"""The filter-box expression language (pure Python)."""
from __future__ import annotations

import time

import pytest

from raptor.gui.table_filter import compile_filter, parse_number


def m(expr, text, raw=None):
    f = compile_filter(expr)
    assert f.valid, f.message
    return f(text, raw)


@pytest.mark.parametrize("expr,text,want", [
    ("pump", "Main Pump A", True), ("PUMP", "pump", True), ("pump", "valve", False),
    ('"main pump"', "The Main Pump", True), ('"main pump"', "Main  Pump", False),
    ("Pump*", "Pump_A", True), ("Pump*", "Main Pump", False),
    ("*_B", "Pump_B", True), ("*_B", "Pump_B2", False),
    ("*pump*", "the Pump room", True), ("P?mp", "Pump", True), ("P?mp", "Pomp2", False),
    ("=Pump_A", "pump_a", True), ("=Pump_A", "Pump_AB", False),
    ("!pump", "valve", True), ("!pump", "Pump", False),
    ("blank", "", True), ("empty", "  ", True), ("blank", "x", False),
    ("nonblank", "x", True), ("nonblank", "", False),
])
def test_text_forms(expr, text, want):
    assert m(expr, text) is want


@pytest.mark.parametrize("expr,text,raw,want", [
    (">0.9", "0.95", None, True), (">0.9", "0.9", None, False),
    (">=0.9", "0.9", None, True), ("<5", "4", None, True), ("<=5", "5", None, True),
    ("<5", "7", None, False), ("=3", "3.0", None, True), ("!=3", "4", None, True),
    ("!=3", "3", None, False), ("5..10", "7", None, True), ("5..10", "10", None, True),
    ("10..5", "7", None, True), ("5..10", "11", None, False),
    (">100", "1,234.5", None, True), (">90", "96.2%", None, True),
    (">1000", ">5000", None, True), ("<10", "3 hours", None, True),
    (">5", "abc", None, False), (">5", "", None, False),
    (">5", "shown as text", 9.0, True),            # the raw value wins
    (">5", "9", 2.0, False),
    ("=Pump", "Pump", None, True),
])
def test_number_forms(expr, text, raw, want):
    assert m(expr, text, raw) is want


def test_and_or_precedence():
    f = compile_filter(">5 <10 | blank")
    assert f("7", None) and f("", None)
    assert not f("12", None) and not f("3", None)
    g = compile_filter("pump, valve")
    assert g("Pump", None) and g("valve 2", None) and not g("fan", None)
    assert compile_filter("pump !valve")("Pump A", None)
    assert not compile_filter("pump !valve")("Pump valve", None)


def test_quoted_separators_are_literal():
    f = compile_filter('"a|b"')
    assert f("xa|by", None) and not f("a", None)


@pytest.mark.parametrize("bad", ['"unclosed', ">", ">abc", "5..x", "!", "<="])
def test_bad_input_never_raises_and_matches_everything(bad):
    f = compile_filter(bad)
    assert not f.valid and f.message
    assert f("anything", None) and f("", None)


def test_empty_expression_is_empty_and_matches_all():
    f = compile_filter("   ")
    assert f.empty and f.valid and f("x", None)


def test_parse_number():
    assert parse_number("1,234.5") == 1234.5
    assert parse_number("96.2%") == 96.2
    assert parse_number("-3e2") == -300.0
    assert parse_number("Pump 3") is None
    assert parse_number("") is None


def test_filtering_a_large_column_is_fast():
    cells = [(f"Block_{i}", float(i)) for i in range(30000)]
    f = compile_filter(">100 <20000 Block_1*")
    t0 = time.perf_counter()
    n = sum(1 for t, r in cells if f(t, r))
    assert time.perf_counter() - t0 < 1.0
    assert n > 0
