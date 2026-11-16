"""Find and replace over model table columns (WP-1.7)."""
from __future__ import annotations

import pytest

from raptor.api.tables import replace_in_table
from raptor.core.fileio.rbd import load_rbd

GOLD = "tests/golden/SpaceStation/input.rbd"


def _rbd():
    return load_rbd(GOLD)


def test_a_partial_name_replacement_reports_only_matching_rows():
    rbd = _rbd()
    changes = replace_in_table(rbd, "inputs.blocks_all", "blockName",
                               "Att", "Att2")
    assert changes
    assert all(change["new"] == change["old"].replace("Att", "Att2")
               for change in changes)


def test_case_sensitivity_and_whole_cell():
    rbd = _rbd()
    lower = replace_in_table(rbd, "inputs.blocks_all", "blockName",
                             "attitude", "X", case=False)
    assert lower and lower[0]["new"] == "X"
    whole = replace_in_table(rbd, "inputs.blocks_all", "blockName",
                             "Attitude", "Whole", whole=True)
    assert [change for change in whole if change["old"] == "Attitude"]


def test_a_column_index_is_accepted():
    rbd = _rbd()
    columns = rbd.section("BLOCK_DATA").columns
    index = columns.index("blockName")
    assert replace_in_table(rbd, "inputs.blocks_all", str(index), "Att", "Z")


def test_an_invalid_name_refuses_everything():
    rbd = _rbd()
    before = [row.get("blockName") for row in rbd.section("BLOCK_DATA").rows]
    with pytest.raises(ValueError, match="spaces"):
        replace_in_table(rbd, "inputs.blocks_all", "blockName",
                         "Attitude", "bad name")
    assert [row.get("blockName")
            for row in rbd.section("BLOCK_DATA").rows] == before


def test_a_duplicate_name_refuses_the_whole_set():
    rbd = _rbd()
    with pytest.raises(ValueError, match="duplicate|break names"):
        replace_in_table(rbd, "inputs.blocks_all", "blockName",
                         "SolarP_500W", "Attitude")


def test_a_row_filter_limits_the_change():
    rbd = _rbd()
    changes = replace_in_table(rbd, "inputs.blocks_all", "blockName",
                               "Attitude", "Only", row_filter={0})
    assert all(change["id"] != "" for change in changes)


def test_a_non_name_column_replaces_freely():
    rbd = _rbd()
    changes = replace_in_table(rbd, "inputs.blocks_all", "FParam1",
                               "1.2", "9.9")
    assert all(change["column"] == "FParam1" for change in changes)
