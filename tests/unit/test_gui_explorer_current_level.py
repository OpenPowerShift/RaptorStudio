"""The Explorer marks the level on screen and the title bar names it."""
from __future__ import annotations

import pytest
from PySide6.QtCore import Qt
from PySide6.QtWidgets import QApplication

from raptor.core.fileio.rbd import load_rbd

pytestmark = pytest.mark.gui

MODEL = "tests/golden/HierarchyExample/input.rbd"


@pytest.fixture
def win():
    from raptor.gui.ui import settings
    from raptor.gui.ui.main_window import MainWindow
    QApplication.instance() or QApplication([])
    settings.set_value("startup/show_start_page", False)
    w = MainWindow()
    w.resize(1400, 900)
    w.show()
    w._load_into_app(load_rbd(MODEL), MODEL)
    QApplication.processEvents()
    yield w
    w._dirty = False
    w.close()


def _marked(win):
    """Every tree item wearing the current-level background."""
    from PySide6.QtWidgets import QTreeWidgetItemIterator
    out = []
    it = QTreeWidgetItemIterator(win._explorer._tree)
    while it.value() is not None:
        item = it.value()
        if item.data(0, Qt.ItemDataRole.BackgroundRole) is not None:
            out.append(item)
        it += 1
    return out


def test_home_is_marked_and_titled_on_open(win):
    ex = win._explorer
    assert _marked(win) == [ex._tree.topLevelItem(0)]
    assert win.windowTitle().endswith("— Home")


def test_entering_a_hierarchy_marks_it_and_names_it_in_the_title(win):
    view = win._diagram_dock.view
    hid = next(iter(view.hiers))
    view.enter_level(hid)
    QApplication.processEvents()
    item = win._explorer._hier_tree_items[hid]
    assert _marked(win) == [item]
    assert item.font(0).bold()
    assert "current level" in item.toolTip(0)
    name = view.hiers[hid].name
    assert win.windowTitle().endswith(f"— {name} (id {hid})")
    view.go_up()
    QApplication.processEvents()
    assert _marked(win) == [win._explorer._tree.topLevelItem(0)]
    assert win.windowTitle().endswith("— Home")
    assert not item.font(0).bold()
    assert "current level" not in item.toolTip(0)


def test_a_level_change_does_not_rebuild_the_tree(win, monkeypatch):
    ex = win._explorer
    calls = []
    real = ex._refresh
    monkeypatch.setattr(ex, "_refresh", lambda: (calls.append(1), real())[1])
    view = win._diagram_dock.view
    hid = next(iter(view.hiers))
    for _ in range(3):
        view.enter_level(hid)
        view.go_up()
    QApplication.processEvents()
    assert calls == []


def test_the_mark_survives_a_rebuild_and_a_theme_flip(win):
    from raptor.gui.resources.design_tokens import ThemeManager
    view = win._diagram_dock.view
    hid = next(iter(view.hiers))
    view.enter_level(hid)
    win._explorer.refresh()
    assert _marked(win) == [win._explorer._hier_tree_items[hid]]
    tm = ThemeManager.instance()
    for _ in range(2):                 # dark, then back to light
        tm.toggle()
        QApplication.processEvents()
        assert _marked(win) == [win._explorer._hier_tree_items[hid]]


def test_a_long_name_is_elided_in_the_title_with_the_id_kept(win, monkeypatch):
    monkeypatch.setattr(type(win), "_TITLE_LEVEL_CHARS", 3)
    view = win._diagram_dock.view
    hid = next(iter(view.hiers))
    name = view.hiers[hid].name
    assert len(name) > 3
    view.enter_level(hid)
    assert win.windowTitle().endswith(f"— {name[:2]}… (id {hid})")


def test_no_model_has_no_level_in_the_title(win):
    win._close_file() if hasattr(win, "_close_file") else None
    if win._rbd is None:
        assert win.windowTitle() == "RAPTOR Studio"
