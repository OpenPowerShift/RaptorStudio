"""COMMENTS name matching: normalize only where the legacy 20-char cap applies."""
from __future__ import annotations

from types import SimpleNamespace

from raptor.core.fileio.comments import comment_text, names_agree


def test_short_names_ending_in_a_digit_suffix_are_distinct():
    assert not names_agree("Pump", "Pump~2")
    assert names_agree("Pump~2", "Pump~2")
    assert not names_agree("Pump~2", "Pump~3")


def test_under_cap_stems_with_digit_suffixes_are_distinct():
    assert not names_agree("CompressorStage1", "CompressorStage1~2")
    assert not names_agree("CompressorStage1~2", "CompressorStage1~3")


def test_cap_length_truncation_twins_still_match_their_element():
    long_name = "VeryLongComponentNameThatExceedsTheCap"
    assert names_agree(long_name, long_name[:20])
    assert names_agree(long_name[:20], long_name[:18] + "~3")


def test_two_long_no_id_rows_use_exact_names_not_shared_prefixes():
    stem = "VeryLongComponentNameThatExceedsTheCap"
    section = SimpleNamespace(rows=[
        {"Type": "block", "Name": stem + "Alpha", "Comment": "A"},
        {"Type": "block", "Name": stem + "Beta", "Comment": "B"},
    ])
    # Both names share the legacy truncation prefix; exact spelling must
    # still pick the right row rather than cross-attaching A to B.
    assert comment_text(section, "block", 1, stem + "Alpha") == "A"
    assert comment_text(section, "block", 2, stem + "Beta") == "B"


def test_ambiguous_capped_twins_return_no_comment():
    section = SimpleNamespace(rows=[
        {"Type": "block", "Name": "VeryLongComponentNam", "Comment": "A"},
        {"Type": "block", "Name": "VeryLongComponentN~3", "Comment": "B"},
    ])
    # The query is neither exact spelling but agrees with both capped
    # rows: the name cannot identify one, so attach nothing.
    assert comment_text(section, "block", 1,
                        "VeryLongComponentN~2") == ""


def test_at_cap_digit_suffix_is_ambiguous_by_design():
    # A legal 20-char name ending ~n is indistinguishable from a
    # truncation twin in the legacy format; the twin interpretation wins.
    assert names_agree("CompressorStage123", "CompressorStage123~2")


def test_exact_names_always_agree():
    assert names_agree("Valve", "Valve")
    assert names_agree("", "anything")
    assert names_agree("anything", "")
