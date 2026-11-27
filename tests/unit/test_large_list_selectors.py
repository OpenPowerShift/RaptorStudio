"""Pickers that can list hundreds of model entries stay short and searchable.

- Every combo popup scrolls after `maxVisibleItems` rows: the global
  stylesheet turns off `combobox-popup`, because styles such as KDE Breeze
  and Fusion otherwise list every entry as tall as the screen.
- "Go to hierarchy" shows each hierarchy's path, filters by typed text and
  jumps on selection.
"""
from __future__ import annotations

import os
from types import SimpleNamespace

import pytest

pytest.importorskip("PySide6")
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QApplication, QComboBox

pytestmark = pytest.mark.gui


@pytest.fixture(scope="module", autouse=True)
def qapp():
    return QApplication.instance() or QApplication([])


def pe():
    for _ in range(5):
        QApplication.processEvents()


def test_global_stylesheet_limits_combo_and_menu_popups():
    from raptor.gui.resources.design_tokens import global_stylesheet
    css = global_stylesheet()
    assert "combobox-popup: 0" in css
    assert "menu-scrollable: 1" in css


def test_long_combo_popup_is_limited_under_the_global_stylesheet(qapp):
    from raptor.gui.resources.design_tokens import global_stylesheet
    old = qapp.styleSheet()
    qapp.setStyleSheet(global_stylesheet())
    try:
        combo = QComboBox()
        combo.addItems([f"item {i}" for i in range(300)])
        combo.show()
        pe()
        combo.showPopup()
        pe()
        popup = combo.view().window()
        row = combo.view().sizeHintForRow(0)
        assert combo.maxVisibleItems() == 10
        # ten rows and a little chrome, nowhere near 300
        assert popup.height() <= (combo.maxVisibleItems() + 2) * row + 20
        combo.hidePopup()
        combo.close()
    finally:
        qapp.setStyleSheet(old)


def test_searchable_limits_rows_and_filters_by_contained_text(qapp):
    from raptor.gui.ui.element_picker import MAX_VISIBLE, searchable
    combo = searchable(QComboBox())
    combo.addItems([f"Pump {i}" for i in range(60)] + ["Valve A", "Big Valve B"])
    assert combo.maxVisibleItems() == MAX_VISIBLE
    comp = combo.completer()
    assert comp.maxVisibleItems() == MAX_VISIBLE
    comp.setCompletionPrefix("valve")
    assert comp.completionCount() == 2          # matches inside the name, any case


def test_hierarchy_paths_show_ancestors():
    from raptor.gui.ui.element_picker import hierarchy_paths_of
    hiers = {1: SimpleNamespace(name="SWBD1", parent_id=0),
             2: SimpleNamespace(name="FDR", parent_id=1),
             3: SimpleNamespace(name="FDR", parent_id=0)}   # same name, top level
    paths = hierarchy_paths_of(hiers)
    assert paths == {1: "SWBD1", 2: "SWBD1 ▸ FDR", 3: "FDR"}


def test_goto_hierarchy_shows_paths_filters_and_jumps(qapp):
    from raptor.core.fileio.rbd import load_rbd
    from raptor.gui.ui.main_window import MainWindow
    w = MainWindow()
    w._load_into_app(load_rbd("tests/golden/NestedHierarchyCost/input.rbd"), "x")
    pe()
    combo = w._goto_combo
    view = w._diagram_dock.view
    assert combo.isEditable()
    assert combo.lineEdit().placeholderText()
    assert combo.currentIndex() == -1
    texts = [combo.itemText(i) for i in range(combo.count())]
    nested = [h for h in view.hiers.values() if h.parent_id]
    assert nested, "the fixture must nest hierarchies"
    for h in nested:
        parent = view.hiers[h.parent_id]
        want = next(t for t in texts if t.endswith(f"▸ {h.name}"))
        assert parent.name in want
    # typing filters (contains, any case) ...
    target = nested[0]
    comp = combo.completer()
    comp.setCompletionPrefix(target.name.lower()[1:])
    assert comp.completionCount() >= 1
    # ... and choosing the entry jumps there
    idx = combo.findData(target.id if hasattr(target, "id") else
                         next(k for k, v in view.hiers.items() if v is target))
    assert idx >= 0
    combo.setCurrentIndex(idx)
    pe()
    assert view._current_parent == combo.itemData(idx)
    assert combo.itemData(idx) in view.hiers
    w._dirty = False
    w.close()


def test_results_hierarchy_filter_is_searchable(qapp):
    from raptor.gui.ui.results import ResultsDock
    dock = ResultsDock()
    dock.set_hierarchy_options({100: "A ▸ Gens", 101: "B"}, 0)
    combo = dock._hier_filter_combo
    assert combo.isEditable()
    assert [combo.itemText(i) for i in range(combo.count())] == ["All", "A ▸ Gens", "B"]
    assert combo.itemData(1) == 100
    assert Qt.MatchFlag.MatchContains == combo.completer().filterMode()
