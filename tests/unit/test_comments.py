"""COMMENTS name matching: normalize only where the legacy 20-char cap applies."""
from __future__ import annotations

from raptor.core.fileio.comments import names_agree


def test_short_names_ending_in_a_digit_suffix_are_distinct():
    assert not names_agree("Pump", "Pump~2")
    assert names_agree("Pump~2", "Pump~2")
    assert not names_agree("Pump~2", "Pump~3")


def test_cap_length_truncation_twins_still_match_their_element():
    long_name = "VeryLongComponentNameThatExceedsTheCap"
    assert names_agree(long_name, long_name[:20])
    assert names_agree(long_name[:20], long_name[:18] + "~3")


def test_exact_names_always_agree():
    assert names_agree("Valve", "Valve")
    assert names_agree("", "anything")
    assert names_agree("anything", "")
