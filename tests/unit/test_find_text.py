"""`raptor.gui.find_text`: what the ribbon Find box means (pure, no Qt)."""
from __future__ import annotations

import pytest

from raptor.gui.find_text import find_entries, parse_find_text, resolve_find_text

NAMES = {
    "Block": {1: "Pump", 2: "valve"},
    "Node": {3: "Pump", 4: "Bus"},
    "Event": {5: "Trip"},
    "Hier": {6: "Plant"},
}


def test_entries_group_by_kind_and_sort_case_insensitively():
    assert find_entries(NAMES) == [
        "Block  Pump (1)", "Block  valve (2)",
        "Node  Bus (4)", "Node  Pump (3)",
        "Event  Trip (5)", "Hier  Plant (6)"]


def test_entries_tolerate_missing_kinds():
    assert find_entries({}) == []


@pytest.mark.parametrize("text,expected", [
    ("", None),
    ("   ", None),
    # The box is stripped first, so a trailing prefix is just the word "Block".
    ("Block  ", (None, "Block", None)),
    ("Block  Pump", ("Block", "Pump", None)),
    ("Block Pump", (None, "Block Pump", None)),   # one space: part of the name
    ("12", (None, "", 12)),
    ("Pump (12)", (None, "Pump", 12)),
    ("Node  Bus (4)", ("Node", "Bus", 4)),
    ("Pump", (None, "Pump", None)),
])
def test_parse(text, expected):
    assert parse_find_text(text) == expected


@pytest.mark.parametrize("text,expected", [
    ("Pump", ("Block", "Pump")),             # blocks before nodes
    ("bus", ("Node", "bus")),                # case-insensitive; keeps what was typed
    ("Trip", ("Event", "Trip")),
    ("Plant", ("Hier", "Plant")),
    ("Node  Pump", ("Node", "Pump")),        # explicit prefix overrides the order
    ("3", ("Node", "Pump")),                 # bare id -> the element's real name
    ("99", None),
    ("Pump (3)", ("Node", "Pump")),          # name+id: the id picks the kind
    ("Pump (4)", None),                      # id names something else -> no match
    ("Node  Bus (4)", ("Node", "Bus")),      # Explorer row form
    ("Missing", None),
    ("", None),
])
def test_resolve(text, expected):
    assert resolve_find_text(text, NAMES) == expected
