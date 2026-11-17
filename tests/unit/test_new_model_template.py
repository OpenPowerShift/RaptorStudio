"""The Blank model's own file header must agree with its rows.

It said `Links=0` over two links and spelled the hierarchy count
`Hierarchies=` where the format (and the checker) says `Hiers=`, so a
new user's first Blank model showed two "Validation warning" cards in
the Advisor, worded for a developer ("call sync_counts(rbd) ...").
"""
from __future__ import annotations

from raptor.core.edit.new_model import build_new_model
from raptor.core.fileio import builder


def test_a_blank_model_has_no_file_format_warnings():
    assert builder.validate(build_new_model("NewModel")) == []


def test_the_header_counts_are_the_rows():
    rbd = build_new_model("NewModel")
    assert rbd.counts.get("Links") == len(rbd.section("LINK_DATA").rows) == 2
    assert rbd.counts.get("Hiers") == 0
