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


def test_a_blank_sheet_is_an_error_not_a_silent_no_op(rbd):
    plan = I.plan_import(rbd, Sheet("Data", rows=[]))
    assert plan.has_errors()
    assert "empty" in plan.errors[0].text()


def test_a_header_without_data_rows_warns(rbd):
    plan = I.plan_import(rbd, Sheet("Data", rows=[[Cell(value="id")]]))
    assert not plan.has_errors()
    assert plan.warnings and "no data rows" in plan.warnings[0].text()


def test_fractional_ids_and_stock_are_refused(rbd):
    oid, _row = _first_block(rbd)
    plan = I.plan_import(rbd, Sheet("Data", rows=[
        [Cell(value="id"), Cell(value="PM Freq")],
        [Cell(value=oid + 0.5), Cell(value=10.0)],
    ]))
    assert plan.has_errors()
    assert "whole numbers" in plan.errors[0].text()

    plan = I.plan_import(rbd, Sheet("Data", rows=[
        [Cell(value="id"), Cell(value="Stock")],
        [Cell(value=oid), Cell(value=2.5)],
    ]))
    assert plan.has_errors()
    assert "whole number" in plan.errors[0].text()


def test_a_refused_distribution_change_leaves_the_law_alone(rbd):
    oid, _row = _first_block(rbd)
    sheet = Sheet("Data", rows=[
        [Cell(value="id"), Cell(value="Fail Dist"),
         Cell(value="Fail Param 1"), Cell(value="Fail Param 2")],
        # Triangular needs Min < Mode < Max; -5 is refused
        [Cell(value=oid), Cell(value="Triangular"),
         Cell(value=-5.0), Cell(value=0.0)],
    ])
    plan = I.plan_import(rbd, sheet)
    assert plan.has_errors()
    assert [c.field for c in plan.changes] == []


def test_a_block_repeated_on_two_rows_warns_and_the_later_row_wins(rbd):
    oid, _row = _first_block(rbd)
    sheet = Sheet("Data", rows=[
        [Cell(value="id"), Cell(value="PM Freq")],
        [Cell(value=oid), Cell(value=20.0)],
        [Cell(value=oid), Cell(value=30.0)],
    ])
    plan = I.plan_import(rbd, sheet)
    assert not plan.has_errors()
    assert any("also appears on row 2" in w.text() for w in plan.warnings)
    _before, after = I.row_snapshot(plan, rbd)
    assert after[oid]["PMFreq"] == "30.000000"


def test_applying_a_plan_with_errors_is_refused(rbd):
    sheet = Sheet("Data", rows=[
        [Cell(value="id"), Cell(value="Fail Dist")],
        [Cell(value=999999), Cell(value="Weibull")],
    ])
    plan = I.plan_import(rbd, sheet)
    assert plan.has_errors()
    with pytest.raises(ValueError, match="has errors"):
        I.apply_to_rbd(rbd, plan)


def test_a_unit_suffix_maps_but_an_unrelated_column_does_not(rbd):
    mapping, problems = I.map_headers(
        ["ID", "Name of owner", "Fail Param 1 [hours]",
         "PM Frequency (days)"])
    assert set(mapping.values()) == {"id", "fail_param_1", "pm_freq"}
    assert [p for p in problems if "Name of owner" in p]
    assert not [p for p in problems if "Fail Param 1" in p]


def test_a_parameter_list_with_no_numbers_is_an_error(rbd):
    oid, _row = _first_block(rbd)
    plan = I.plan_import(rbd, Sheet("Data", rows=[
        [Cell(value="id"), Cell(value="Fail Params")],
        [Cell(value=oid), Cell(value="()")],
    ]))
    assert plan.has_errors()
    assert "not a list of numbers" in plan.errors[0].text()


def test_duplicate_rows_cannot_combine_a_law_with_invalid_params(rbd):
    oid, _row = _first_block(rbd)
    sheet = Sheet("Data", rows=[
        [Cell(value="id"), Cell(value="Fail Dist"), Cell(value="Fail Param 1"),
         Cell(value="Fail Param 2")],
        [Cell(value=oid), Cell(value="Weibull"), Cell(value=2.0),
         Cell(value=500.0)],
        [Cell(value=oid), Cell(value=2.0), Cell(value=0.0)],
    ])
    plan = I.plan_import(rbd, sheet)
    assert plan.has_errors()
    assert any("must be" in p.text() for p in plan.errors)


@pytest.mark.parametrize("ext", ["xlsx", "ods"])
def test_the_template_round_trips_and_an_edit_imports(tmp_path, ext):
    """The template is the model's own blocks under columns the importer
    reads: unchanged it plans nothing; one edited cell plans one change."""
    from raptor.core.fileio.rbd import load_rbd
    from raptor.core.fileio.sheet_reader import read_sheet
    from raptor.core.fileio.workbook import Sheet, write_workbook
    from raptor.core.import_tables import (
        plan_import, template_rows, write_template,
    )
    rbd = load_rbd("tests/golden/HierarchyExample/input.rbd")
    path = write_template(rbd, tmp_path / f"template.{ext}")
    plan = plan_import(rbd, read_sheet(str(path)), source=str(path))
    assert not plan.changes and not plan.has_errors()
    headers, rows = template_rows(rbd)
    rows[0][headers.index("Initial stock")] = 7
    edited = write_workbook(tmp_path / f"edited.{ext}",
                            [Sheet(name="Blocks", headers=headers, rows=rows)])
    plan = plan_import(rbd, read_sheet(str(edited)), source=str(edited))
    assert [c.field for c in plan.changes] == ["stock"]
