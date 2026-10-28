"""Coverage pass: dialog error/cancel paths, data-table revert paths,
and inspector tab population for every element kind."""

from __future__ import annotations

import json
import os
import pathlib
import tempfile

import pytest
from raptor.gui.ui import filedialogs as _fdg

from PySide6.QtWidgets import (QApplication, QDialog, QMessageBox)

from raptor.core.fileio.rbd import load_rbd
from raptor.gui.ui.data_tables import DataTablesDock
from raptor.gui.ui.diagram import DiagramView
from raptor.gui.ui.inspector import InspectorDock

REPO = pathlib.Path(__file__).resolve().parents[2]
GOLD = os.path.join(REPO, "tests", "golden")
FIX = os.path.join(GOLD, "HierarchyExample", "input.rbd")


@pytest.fixture(scope="module")
def qapp():
    return QApplication.instance() or QApplication([])


@pytest.fixture()
def view(qapp):
    v = DiagramView()
    v.load_rbd(load_rbd(FIX))
    return v


# ------------------------------------------------------------ dialogs --
def test_import_library_no_model(qapp, monkeypatch):
    from raptor.gui.ui.main_window import MainWindow
    w = MainWindow()
    seen = []
    monkeypatch.setattr(
        _fdg, "getOpenFileName",
        staticmethod(lambda *a, **k: seen.append(1) or ("", "")))
    w._import_block_library()          # _rbd None -> silent return
    assert seen == []


def test_import_library_cancelled(qapp, view, monkeypatch):
    from raptor.gui.ui.main_window import MainWindow
    w = MainWindow()
    w._load_into_app(load_rbd(FIX), "x")
    monkeypatch.setattr(_fdg, "getOpenFileName",
                        staticmethod(lambda *a, **k: ("", "")))
    w._import_block_library()          # empty path -> return


def test_import_library_bad_file_warns(qapp, view, monkeypatch):
    from raptor.gui.ui.main_window import MainWindow
    w = MainWindow()
    w._load_into_app(load_rbd(FIX), "x")
    # Write the malformed library to the system temp dir.
    bad = os.path.join(tempfile.gettempdir(), "bad_lib.json")
    with open(bad, "w") as f:
        f.write("{not json")
    monkeypatch.setattr(_fdg, "getOpenFileName",
                        staticmethod(lambda *a, **k: (bad, "")))
    warns = []
    monkeypatch.setattr(QMessageBox, "warning",
                        staticmethod(lambda *a, **k: warns.append(a[2])))
    QDialog.exec = lambda self: QDialog.Accepted
    w._import_block_library()
    assert any("Could not read" in str(t) for t in warns)
    # wrong format id also rejected
    with open(bad, "w") as f:
        json.dump({"format": "other"}, f)
    w._import_block_library()
    assert len(warns) == 2


def test_spof_dialog_exports_file(qapp, view, monkeypatch):
    from raptor.gui.ui.main_window import MainWindow
    w = MainWindow()
    w._load_into_app(view._rbd_ref, "HierarchyExample")
    out = os.path.join(tempfile.gettempdir(), "spof_cov.txt")
    if os.path.exists(out):
        os.remove(out)
    def exec_and_click_export(self):
        from PySide6.QtWidgets import QPushButton
        for btn in self.findChildren(QPushButton):
            if btn.text().startswith("Export"):
                btn.click()
        return QDialog.Accepted
    QDialog.exec = exec_and_click_export
    monkeypatch.setattr(_fdg, "getSaveFileName",
                        staticmethod(lambda *a, **k: (out, "")))
    w._show_spof_dialog()
    txt = open(out).read()
    assert "Single Point Failure File" in txt
    assert "Attitude" in txt and "Thermal" in txt


def test_domains_dialog_renders_per_node_tree(qapp, monkeypatch):
    """Domain Diagram: one tree root per Regular/End node, with a child
    row per source element whose failure propagates to it. The dialog is
    non-modal (`.show()`, not `.exec()`), so it needs no `QDialog.exec`
    monkeypatch, unlike the SPOF dialog."""
    from raptor.gui.ui.main_window import MainWindow
    w = MainWindow()
    w._load_into_app(load_rbd(os.path.join(GOLD, "CS_Probe", "input.rbd")),
                     "x")
    from PySide6.QtWidgets import QTreeWidget
    w._show_domains_dialog()
    dlg = w._domains_dialog
    assert dlg.isVisible()
    tree = dlg.findChild(QTreeWidget)
    assert tree.topLevelItemCount() == len(w._results._domains_data)
    # "end" node's root row names it and lists real upstream sources
    end_row = next(tree.topLevelItem(i)
                   for i in range(tree.topLevelItemCount())
                   if tree.topLevelItem(i).text(0).startswith("end "))
    child_names = {end_row.child(j).text(0)
                  for j in range(end_row.childCount())}
    assert any("Attitude" in name for name in child_names)


# -------------------------------------------------------- data tables --
def test_keyed_cell_invalid_reverts(qapp, view):
    dock = DataTablesDock()
    dock.set_view(view)
    table = dock._spares_table
    if table.rowCount() == 0:
        pytest.skip("fixture has no spare pools")
    stock_col = list(dock._keyed[table][2]).index("Stock")
    item = table.item(0, stock_col)
    orig = item.text()
    item.setText("not-a-number")
    assert table.item(0, stock_col).text() == orig, \
        "invalid int must revert"


def test_keyed_add_remove_row_undo(qapp, view):
    dock = DataTablesDock()
    dock.set_view(view)
    table = dock._spares_table
    if table.rowCount() == 0:
        pytest.skip("no spare-pool rows in fixture")
    n_rows_before = len(view._rbd_ref.sections["SPARE_POOL_DATA"].rows)
    n0 = table.rowCount()
    dock._tabs.setCurrentWidget(table)   # _add_keyed_row acts on the
    dock._add_keyed_row()                # visible keyed tab
    assert table.rowCount() >= n0        # template-only fixtures may
    # not display until refresh; the model row is what matters:
    sec = view._rbd_ref.sections["SPARE_POOL_DATA"]
    assert len(sec.rows) == n_rows_before + 1
    view._stack.undo()
    dock.refresh()
    assert len(sec.rows) == n_rows_before
    assert table.rowCount() == n0


# ---------------------------------------------------------- inspector --
def test_inspector_populates_all_kinds(qapp, view):
    dock = InspectorDock()
    dock.set_rbd(view._rbd_ref)
    bid = sorted(view.blocks)[0]
    blk = view.blocks[bid]
    dock._populate_block(blk)
    nid = next(n for n, nd in view.nodes.items() if nd.type_node == 2)
    dock._populate_node(view.nodes[nid])
    hid = next(iter(view.hiers))
    dock.show_hier(view.hiers[hid])
    QApplication.processEvents()


def test_inspector_rejects_bad_param(qapp, view):
    dock = InspectorDock()
    dock.set_rbd(view._rbd_ref)
    rejected = []
    dock.validation_failed.connect(rejected.append)
    bid = sorted(b for b in view.blocks if b != 0)[0]
    v = view.blocks[bid]
    v.fail_distro = 15            # Weibull: shape must be >= 0.05
    v.num_fail_params = 3
    dock._populate_block(v)
    from PySide6.QtWidgets import QLineEdit, QGroupBox
    groups = [gb for gb in
              dock._scroll_failure.widget().findChildren(QGroupBox)
              if gb.title() == "Failure Distribution"]
    edits = [e for e in groups[0].findChildren(QLineEdit)
             if e.objectName() != "qt_spinbox_lineedit"]
    edits[0].setText("-5")
    edits[0].editingFinished.emit()
    assert rejected, "Weibull shape -5 must be rejected"
