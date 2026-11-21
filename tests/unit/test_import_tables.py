"""Planning and applying a spreadsheet data-table import."""
from __future__ import annotations

from pathlib import Path

import pytest

from raptor.core import import_tables as I
from raptor.core.fileio.rbds import load_rbds
from raptor.core.fileio.sheet_reader import Cell, Sheet

MODEL = Path(__file__).resolve().parents[2] / "new_docs" / "models" / "DC1.rbds"


@pytest.fixture()
def rbd():
    return load_rbds(str(MODEL))


def _first_block(rbd):
    from raptor.core.fileio.typed import row_int
    for row in rbd.sections["BLOCK_DATA"].rows:
        oid = row_int(row.get("ID"), 0)
        if oid > 0:
            return oid, row
    raise AssertionError("fixture has no blocks")


def test_headers_map_friendly_names_and_data_table_headings():
    mapping, problems = I.map_headers(
        ["ID", "Block Name", "Failure Distribution", "F Param 1",
         "Failure Param 2 (hours)", "R Dist", "Repair Param 1",
         "PM Frequency", "Preventive Maintenance"])
    assert not problems
    assert set(mapping.values()) == {
        "id", "name", "fail_dist", "fail_param_1", "fail_param_2",
        "repair_dist", "repair_param_1", "pm_freq", "uses_pm"}


def test_an_unrelated_notes_column_is_a_warning_not_a_blocker(rbd):
    oid, _row = _first_block(rbd)
    sheet = Sheet("Data", rows=[
        [Cell(value="id"), Cell(value="PM Freq"), Cell(value="Notes")],
        [Cell(value=oid), Cell(value=25.0), Cell(value="changed by hand")],
    ])
    plan = I.plan_import(rbd, sheet)
    assert not plan.has_errors()
    assert plan.warnings and "not a field" in plan.warnings[0].text()
    assert [c.field for c in plan.changes] == ["pm_freq"]


def test_the_plan_names_each_change_and_keeps_the_formula(rbd):
    oid, _row = _first_block(rbd)
    sheet = Sheet("Data", rows=[
        [Cell(value="id"), Cell(value="Fail Dist"), Cell(value="Fail Param 1"),
         Cell(value="Fail Param 2"), Cell(value="PM Freq")],
        [Cell(value=oid), Cell(value="Weibull"),
         Cell(value=2.0), Cell(value=500.0, formula="=1000/2"),
         Cell(value=50.0, formula="=100/2")],
    ])
    plan = I.plan_import(rbd, sheet, source="plant.xlsx")
    assert not plan.errors
    fields = {c.field for c in plan.changes}
    assert {"fail_dist", "fail_params", "pm_freq"} <= fields
    param = next(c for c in plan.changes if c.field == "fail_params")
    assert param.new == [2.0, 500.0, 0.0]
    assert param.formula == "=1000/2"
    assert plan.summary().startswith("3 change(s) on 1 block(s)")


def test_an_unknown_distribution_or_bad_parameter_blocks_the_whole_row(rbd):
    oid, _row = _first_block(rbd)
    sheet = Sheet("Data", rows=[
        [Cell(value="id"), Cell(value="Fail Dist"), Cell(value="Fail Param 1")],
        [Cell(value=oid), Cell(value="Nonsense"), Cell(value=2.0)],
    ])
    plan = I.plan_import(rbd, sheet)
    assert plan.has_errors()
    assert "not a known failure distribution" in plan.errors[0].text()

    sheet.rows[1][1] = Cell(value="Weibull")
    sheet.rows[1][2] = Cell(value=-2.0)
    plan = I.plan_import(rbd, sheet)
    assert plan.has_errors()
    assert "greater than or equal" in plan.errors[0].text()


def test_an_unknown_block_and_a_missing_key_are_reported_not_silently_skipped(rbd):
    sheet = Sheet("Data", rows=[
        [Cell(value="id"), Cell(value="Fail Param 1")],
        [Cell(value=999999), Cell(value=2.0)],
    ])
    plan = I.plan_import(rbd, sheet)
    assert plan.has_errors() and "not in the model" in plan.errors[0].text()

    sheet = Sheet("Data", rows=[
        [Cell(value="Fail Param 1")], [Cell(value=2.0)]])
    plan = I.plan_import(rbd, sheet)
    assert plan.has_errors() and "needs an 'id' or 'name'" in plan.errors[0].text()


def test_applying_the_plan_changes_only_the_snapshotted_rows(rbd):
    oid, row = _first_block(rbd)
    old_name = row["blockName"]
    sheet = Sheet("Data", rows=[
        [Cell(value="id"), Cell(value="Fail Dist"),
         Cell(value="Fail Param 1"), Cell(value="Fail Param 2")],
        [Cell(value=oid), Cell(value="Weibull"),
         Cell(value=2.0), Cell(value=500.0)],
    ])
    plan = I.plan_import(rbd, sheet)
    before, after = I.apply_to_rbd(rbd, plan)
    assert set(before) == set(after) == {oid}
    row = next(r for r in rbd.sections["BLOCK_DATA"].rows
               if r.get("ID") == str(oid))
    assert row["blockName"] == old_name
    assert row["FDist"] == "15"
    assert row["FParam1"] == "2.000000"
    assert row["FParam2"] == "500.000000"
    assert row["#FParam"] == "3"

    # Undo is the caller's job: writing ``before`` restores the row.
    I.write_rows(rbd, before)
    row = next(r for r in rbd.sections["BLOCK_DATA"].rows
               if r.get("ID") == str(oid))
    assert row["FDist"] == "4"
    assert row["FParam1"] == "1000.000000"


def test_a_name_column_identifies_a_unique_block(rbd):
    _oid, row = _first_block(rbd)
    name = row["blockName"]
    sheet = Sheet("Data", rows=[
        [Cell(value="Block Name"), Cell(value="PM Freq")],
        [Cell(value=name), Cell(value=25.0)],
    ])
    plan = I.plan_import(rbd, sheet)
    assert not plan.errors
    assert [c.field for c in plan.changes] == ["pm_freq"]
