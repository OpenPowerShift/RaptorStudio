"""Prefix ranking shared by Quick Add and command search (WP-1.6)."""
from __future__ import annotations

from raptor.gui.quick_match import Candidate, rank, score


def test_every_token_must_be_a_word_prefix():
    candidates = [
        Candidate("2 pumps in parallel", "Pattern"),
        Candidate("Pump, centrifugal", "Library"),
        Candidate("Duplicate", "home", keywords=("copy",)),
    ]
    assert [c.text for c in rank("pum par", candidates)] == [
        "2 pumps in parallel"]
    assert [c.text for c in rank("pump", candidates)] == [
        "Pump, centrifugal", "2 pumps in parallel"]


def test_name_prefix_beats_word_prefix_beats_keyword():
    candidates = [
        Candidate("Copy command", keywords=()),
        Candidate("Duplicate", keywords=("copy",)),
        Candidate("Copy", keywords=()),
    ]
    assert [c.text for c in rank("copy", candidates)] == [
        "Copy", "Copy command", "Duplicate"]
    assert score("copy", candidates[2]) == 0
    assert score("copy", candidates[0]) == 1
    assert score("copy", candidates[1]) == 3


def test_an_empty_query_keeps_recency_order():
    candidates = [Candidate("A", recent=2), Candidate("B", recent=0)]
    assert [c.text for c in rank("", candidates)] == ["B", "A"]
