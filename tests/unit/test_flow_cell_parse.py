"""The Capacity tab's flow cells: each column has its own keyword.

Nominal Flow takes ``MaxFlow`` / ``MaxFlow/N``; Max Flow takes ``FullFlow``
/ ``FullFlow/N``. Both spellings map to the same stored sentinel, so the
editor used to accept the other column's keyword and re-render the cell
with this column's spelling -- which read as "it picked the other one".
"""
from __future__ import annotations

import pytest

from raptor.gui.ui.inspector import parse_flow_cell


@pytest.mark.parametrize("text,label,n,value", [
    ("MaxFlow", "MaxFlow", 2, -1),
    ("maxflow", "MaxFlow", 2, -1),
    ("MaxFlow/2", "MaxFlow", 2, -2),
    (" MaxFlow / 2 ", "MaxFlow", 2, -2),
    ("FullFlow", "FullFlow", 3, -1),
    ("FullFlow/3", "FullFlow", 3, -2),
    ("0", "MaxFlow", 2, 0),
    ("133", "FullFlow", 2, 133),
])
def test_accepts_its_own_keyword_and_numbers(text, label, n, value):
    assert parse_flow_cell(text, label, n) == (value, None)


@pytest.mark.parametrize("text,label", [("FullFlow/2", "MaxFlow"),
                                        ("FullFlow", "MaxFlow"),
                                        ("MaxFlow/2", "FullFlow"),
                                        ("MaxFlow", "FullFlow")])
def test_refuses_the_other_columns_keyword(text, label):
    value, problem = parse_flow_cell(text, label, 2)
    assert value is None
    assert "belongs to the other column" in problem


@pytest.mark.parametrize("text", ["", "abc", "-3", "MaxFlow/3"])
def test_refuses_non_flow_text(text):
    value, problem = parse_flow_cell(text, "MaxFlow", 2)
    assert value is None and problem
