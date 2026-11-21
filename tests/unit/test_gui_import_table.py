"""The spreadsheet import dialog and its one undoable command."""
from __future__ import annotations

import pytest
from PySide6.QtWidgets import QApplication, QDialogButtonBox

from raptor.core import import_tables as I
from raptor.core.fileio.rbd import load_rbd
from raptor.core.fileio.sheet_reader import Cell, Sheet

pytestmark = pytest.mark.gui

MODEL = "tests/golden/TwoBlockRedundancy/input.rbd"


@pytest.fixture
def win():
    from raptor.gui.ui import settings
    from raptor.gui.ui.main_window import MainWindow
    QApplication.instance() or QApplication([])
    settings.set_value("startup/show_start_page", False)
    w = MainWindow()
    w.resize(1200, 800)
    w.show()
    w._load_into_app(load_rbd(MODEL), MODEL)
    QApplication.processEvents()
    yield w
    w._dirty = False
    w.close()


def _block(rbd):
    from raptor.core.fileio.typed import row_int
    for row in rbd.sections["BLOCK_DATA"].rows:
        oid = row_int(row.get("ID"), 0)
        if oid > 0:
            return oid, row
    raise AssertionError("no blocks")


def _plan(rbd):
    oid, _row = _block(rbd)
    sheet = Sheet("Data", rows=[
        [Cell(value="id"), Cell(value="Fail Dist"),
         Cell(value="Fail Param 1"), Cell(value="Fail Param 2")],
        [Cell(value=oid), Cell(value="Weibull"),
         Cell(value=2.0), Cell(value=350.0)],
    ])
    return I.plan_import(rbd, sheet, source="import.xlsx")


def test_the_dialog_lists_changes_and_blocks_an_invalid_plan(win):
    from raptor.gui.ui.import_table_dialog import ImportTableDialog
    rbd = win._diagram_dock.view._rbd_ref
    plan = _plan(rbd)
    dialog = ImportTableDialog(win, plan)
    assert dialog._table.rowCount() == len(plan.changes)
    ok = dialog._buttons.button(QDialogButtonBox.StandardButton.Ok)
    assert ok.isEnabled()

    bad = I.plan_import(rbd, Sheet("Data", rows=[
        [Cell(value="id"), Cell(value="Fail Dist")],
        [Cell(value=_block(rbd)[0]), Cell(value="not-a-law")],
    ]))
    dialog = ImportTableDialog(win, bad)
    ok = dialog._buttons.button(QDialogButtonBox.StandardButton.Ok)
    assert not ok.isEnabled()


def test_the_window_handler_reads_a_real_xlsx_and_undoes_it(win, tmp_path,
                                                            monkeypatch):
    from PySide6.QtWidgets import QDialog
    import xlsxwriter
    from raptor.gui.ui import filedialogs as fdg

    rbd = win._diagram_dock.view._rbd_ref
    oid, row = _block(rbd)
    old_dist = row["FDist"]
    path = tmp_path / "import.xlsx"
    book = xlsxwriter.Workbook(str(path))
    sheet = book.add_worksheet("Data")
    sheet.write_row(0, 0, ["id", "Failure Dist", "Failure Param 1",
                           "Failure Param 2"])
    sheet.write_row(1, 0, [oid, "Weibull", 2.0, 400.0])
    book.close()
    monkeypatch.setattr(fdg, "getOpenFileName",
                        lambda *a, **k: (str(path), ""))
    monkeypatch.setattr(
        "raptor.gui.ui.import_table_dialog.ImportTableDialog.exec",
        lambda self: QDialog.DialogCode.Accepted)
    win._import_data_table()
    row = next(r for r in rbd.sections["BLOCK_DATA"].rows
               if r.get("ID") == str(oid))
    assert row["FDist"] == "15"
    win._undo_stack.undo()
    row = next(r for r in rbd.sections["BLOCK_DATA"].rows
               if r.get("ID") == str(oid))
    assert row["FDist"] == old_dist


def test_import_command_changes_the_row_and_undo_restores_it(win):
    from raptor.gui.ui.import_table_dialog import ImportTableCommand
    view = win._diagram_dock.view
    rbd = view._rbd_ref
    oid, row = _block(rbd)
    old = dict(row)
    plan = _plan(rbd)
    before, after = I.row_snapshot(plan, rbd)
    stack = view._stack
    stack.push(ImportTableCommand(view, rbd, before, after, "Import test"))
    row = next(r for r in rbd.sections["BLOCK_DATA"].rows
               if r.get("ID") == str(oid))
    assert row["FDist"] == "15"
    assert row["FParam1"] == "2.000000"
    stack.undo()
    row = next(r for r in rbd.sections["BLOCK_DATA"].rows
               if r.get("ID") == str(oid))
    assert row["FDist"] == old["FDist"]
    assert row["FParam1"] == old["FParam1"]
    stack.redo()
    row = next(r for r in rbd.sections["BLOCK_DATA"].rows
               if r.get("ID") == str(oid))
    assert row["FDist"] == "15"
