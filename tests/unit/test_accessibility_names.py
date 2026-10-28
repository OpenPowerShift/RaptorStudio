"""Every editor a user can tab into has an accessible name.

Measured through Qt's own accessibility interface (the name a screen
reader would announce), not by counting `setAccessibleName` calls: Qt
derives a name from a form label's buddy, from button text and so on, so
the number of explicit calls says little. What matters is the result.

The gaps this closed, all invisible without a screen reader:
* rate-capable parameter fields sit in a container (edit + rate button),
  so the form's label became the CONTAINER's buddy and the edit was
  unnamed;
* the PM checkbox is a text-less box beside a wrapping label;
* Data Tables cell editors are bare line edits in a container -- 91
  unnamed fields on a 14-block model.
"""
from __future__ import annotations

import pytest
from PySide6.QtGui import QAccessible
from PySide6.QtWidgets import (QApplication, QCheckBox, QComboBox, QDialog,
                               QDoubleSpinBox, QLineEdit, QPlainTextEdit,
                               QPushButton, QRadioButton, QSpinBox,
                               QTableWidget, QToolButton)

pytestmark = pytest.mark.gui

EDITORS = (QLineEdit, QSpinBox, QDoubleSpinBox, QComboBox, QCheckBox,
           QPushButton, QRadioButton, QTableWidget, QToolButton,
           QPlainTextEdit)
#: A spin box's / combo's own inner line edit is not a separate control.
INNER_OF = (QSpinBox, QDoubleSpinBox, QComboBox)


@pytest.fixture
def qapp():
    return QApplication.instance() or QApplication([])


def unnamed(root) -> list[str]:
    out = []
    for kind in EDITORS:
        for c in root.findChildren(kind):
            if isinstance(c.parent(), INNER_OF):
                continue
            if not c.isVisibleTo(root):
                continue
            iface = QAccessible.queryAccessibleInterface(c)
            name = (iface.text(QAccessible.Text.Name) if iface else "") or ""
            if not name.strip():
                out.append(f"{type(c).__name__}"
                           f"[{(c.toolTip() or c.objectName())[:40]}]")
    return out


def _window(qapp, fixture):
    from raptor.core.fileio.rbd import load_rbd
    from raptor.gui.ui.main_window import MainWindow
    w = MainWindow()
    w.resize(1600, 1000)
    w.show()
    w._load_into_app(load_rbd(fixture), fixture)
    for _ in range(5):
        qapp.processEvents()
    return w


def _close(w):
    w._dirty = False
    w.close()


def _audit_inspector(w, qapp, label):
    bad = []
    tabs = w._inspector._tabs
    for i in range(tabs.count()):
        if not tabs.isTabVisible(i):
            continue
        tabs.setCurrentIndex(i)
        qapp.processEvents()
        bad += [f"{label}/{tabs.tabText(i)}: {u}"
                for u in unnamed(tabs.widget(i))]
    return bad


def test_inspector_block_and_node_editors_are_named(qapp):
    w = _window(qapp, "tests/golden/HierarchyExample/input.rbd")
    v = w._diagram_dock.view
    bad = []
    bid = next(b for b in v.blocks if b != 0)
    w._inspector.show_block(bid, v.blocks)
    bad += _audit_inspector(w, qapp, "block")
    nid = next(n for n, x in v.nodes.items() if x.type_node == 2)
    w._inspector.show_node(nid, v.nodes)
    bad += _audit_inspector(w, qapp, "node")
    _close(w)
    assert not bad, bad


def test_inspector_event_and_hierarchy_editors_are_named(qapp):
    w = _window(qapp, "tests/golden/Events/input.rbd")
    v = w._diagram_dock.view
    bad = []
    if v.events:
        w._inspector.show_event(next(iter(v.events.values())))
        bad += _audit_inspector(w, qapp, "event")
    _close(w)
    w = _window(qapp, "tests/golden/HierarchyExample/input.rbd")
    v = w._diagram_dock.view
    if v.hiers:
        w._inspector.show_hier(next(iter(v.hiers.values())))
        bad += _audit_inspector(w, qapp, "hierarchy")
    _close(w)
    assert not bad, bad


def test_data_tables_cell_editors_are_named(qapp):
    w = _window(qapp, "tests/golden/HierarchyExample/input.rbd")
    dt = w._data_tables
    dt.refresh()
    bad = unnamed(dt)
    _close(w)
    assert not bad, bad[:10]


def test_data_table_names_follow_content_not_position(qapp):
    """Sorting moves rows but not cell widgets; the name is built from the
    column header and the ROW'S OWN name so it cannot go stale."""
    w = _window(qapp, "tests/golden/HierarchyExample/input.rbd")
    dt = w._data_tables
    dt.refresh()
    t = dt._blocks_table
    row_name = t.item(0, 0).text()
    names = []
    for c in range(t.columnCount()):
        cw = t.cellWidget(0, c)
        if cw is None:
            continue
        for kind in (QLineEdit, QComboBox, QCheckBox):
            names += [e.accessibleName() for e in cw.findChildren(kind)
                      if not isinstance(e.parent(), INNER_OF)]
    _close(w)
    assert names, "the blocks grid should have editable cells"
    assert all(row_name in n for n in names), (row_name, names)


def test_run_panel_ribbon_and_results_are_named(qapp):
    w = _window(qapp, "tests/golden/TwoBlockRedundancy/input.rbd")
    bad = (unnamed(w._run_panel) + unnamed(w._ribbon)
           + unnamed(w._results))
    _close(w)
    assert not bad, bad


@pytest.mark.parametrize("opener", ["show_system_config", "show_preferences",
                                    "edit_block_defaults"])
def test_main_dialogs_are_named(qapp, monkeypatch, opener):
    from raptor.gui.ui import dialogs
    w = _window(qapp, "tests/golden/TwoBlockRedundancy/input.rbd")
    seen: list[str] = []

    def audit_then_close(self, *a, **k):
        seen.extend(unnamed(self))
        return QDialog.Rejected
    monkeypatch.setattr(QDialog, "exec", audit_then_close)
    getattr(dialogs, opener)(w)
    _close(w)
    assert not seen, seen


def test_mass_edit_dialog_is_named(qapp, monkeypatch):
    from raptor.gui.ui import dialogs
    w = _window(qapp, "tests/golden/Phasing1/input.rbd")
    v = w._diagram_dock.view
    for bid in sorted(b for b in v.blocks if b != 0)[:2]:
        v._block_items[bid].setSelected(True)
    seen: list[str] = []

    def audit_then_close(self, *a, **k):
        seen.extend(unnamed(self))
        return QDialog.Rejected
    monkeypatch.setattr(QDialog, "exec", audit_then_close)
    dialogs.on_mass_edit(w)
    _close(w)
    assert not seen, seen
