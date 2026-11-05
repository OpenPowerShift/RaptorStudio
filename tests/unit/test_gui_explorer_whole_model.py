"""The Explorer lists the whole model, not just the level on screen (#140)."""
from __future__ import annotations

import pytest
from PySide6.QtCore import Qt
from PySide6.QtWidgets import QApplication, QTreeWidgetItemIterator

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


def _rows(win) -> list:
    out = []
    it = QTreeWidgetItemIterator(win._explorer._tree)
    while it.value() is not None:
        out.append(it.value())
        it += 1
    return out


def _role(item) -> str:
    return str(item.data(0, Qt.ItemDataRole.UserRole))


def _ids(win, kind: str) -> set[int]:
    return {int(_role(r).split(":")[1]) for r in _rows(win)
            if _role(r).startswith(f"{kind}:")}


def _model_ids(rbd, section: str) -> set[int]:
    return {int(float(r["ID"])) for r in rbd.sections[section].rows
            if int(float(r["ID"])) > 0}


def test_home_lists_every_element_of_every_level(win):
    rbd = load_rbd(MODEL)
    view = win._diagram_dock.view
    assert view._current_parent == 0
    # The canvas holds one level; the Explorer holds them all.
    assert len(view.blocks) < len(_model_ids(rbd, "BLOCK_DATA"))
    assert _ids(win, "block") == _model_ids(rbd, "BLOCK_DATA")
    assert _ids(win, "node") == _model_ids(rbd, "NODE_DATA")
    assert _ids(win, "hier") == set(view.hiers)


def test_elements_sit_under_their_own_hierarchy(win):
    view = win._diagram_dock.view
    rbd = load_rbd(MODEL)
    parent_of = {int(float(r["ID"])): int(float(r["ParentId"]))
                 for r in rbd.sections["BLOCK_DATA"].rows}
    for row in _rows(win):
        if not _role(row).startswith("block:"):
            continue
        bid = int(_role(row).split(":")[1])
        folder = _role(row.parent())
        expected = (f"hier:{parent_of[bid]}" if parent_of[bid]
                    else "root")
        assert folder == expected, row.text(0)
    assert view._current_parent == 0


def test_expand_all_opens_every_folder(win):
    win._explorer.expand_all()
    while win._explorer._expand_timer.isActive():
        QApplication.processEvents()
    assert all(r.isExpanded() for r in _rows(win) if r.childCount())


def test_find_reaches_inside_hierarchies(win):
    view = win._diagram_dock.view
    sub = next(r for r in _rows(win) if _role(r).startswith("block:")
               and int(_role(r).split(":")[1]) not in view.blocks)
    name = sub.text(0).split(" (")[0]
    win._explorer.filter_items(name)
    visible = [r for r in _rows(win) if not r.isHidden()]
    assert sub in visible
    assert not sub.parent().isHidden() and sub.parent().isExpanded()
    win._explorer.filter_items("")
    assert all(not r.isHidden() for r in _rows(win))


def test_clicking_an_element_on_another_level_opens_it(win):
    view = win._diagram_dock.view
    sub = next(r for r in _rows(win) if _role(r).startswith("block:")
               and int(_role(r).split(":")[1]) not in view.blocks)
    bid = int(_role(sub).split(":")[1])
    rows_before = len(_rows(win))

    win._explorer._tree.itemClicked.emit(sub, 0)
    QApplication.processEvents()

    assert view._current_parent != 0
    assert view._block_items[bid].isSelected()
    assert win._inspector.current_target() == ("block", bid)
    # A level change does not rebuild the tree (the row clicked survives).
    assert len(_rows(win)) == rows_before
    assert sub.parent().isExpanded()


def test_clicking_a_hierarchy_row_selects_its_oval(win):
    view = win._diagram_dock.view
    row = next(r for r in _rows(win) if _role(r).startswith("hier:"))
    hid = int(_role(row).split(":")[1])
    win._explorer._tree.itemClicked.emit(row, 0)
    QApplication.processEvents()
    assert view._hier_items[hid].isSelected()


def test_expand_and_collapse_are_icon_buttons(win):
    for button in (win._explorer._expand_button,
                   win._explorer._collapse_button):
        assert not button.icon().isNull()
        assert button.text() == ""
        assert button.toolTip()
        assert button.accessibleName()


def test_double_clicking_an_out_marker_leaves_its_own_hierarchy(win):
    """The tree lists every level, so an Out marker it shows may belong to
    a hierarchy that is not on screen; double-clicking it opens the level
    that holds that hierarchy, as the marker does on the canvas."""
    view = win._diagram_dock.view
    rbd = load_rbd(MODEL)
    outs = {int(float(r["ID"])): int(float(r["parent"]))
            for r in rbd.sections["NODE_DATA"].rows
            if int(float(r["ID"])) > 0 and int(float(r["Type"])) == 5}
    nid, hid = next(iter(sorted(outs.items())))
    row = next(r for r in _rows(win) if _role(r) == f"node:{nid}")
    assert view._current_parent == 0          # the marker is not on screen
    win._explorer._on_item_double_clicked(row, 0)
    QApplication.processEvents()
    assert view._current_parent == view.hiers[hid].parent_id

    # On screen too: enter the hierarchy, then its own marker goes up.
    view.enter_level(hid)
    QApplication.processEvents()
    win._explorer._on_item_double_clicked(row, 0)
    QApplication.processEvents()
    assert view._current_parent == view.hiers[hid].parent_id


def test_switching_theme_repaints_the_icon_buttons(win):
    """ThemeManager passes the new theme to its callbacks."""
    from raptor.gui.resources.design_tokens import ThemeManager
    tm = ThemeManager.instance()
    was_dark = tm.is_dark
    before = win._explorer._expand_button.icon().cacheKey()
    try:
        tm.set_dark(not was_dark)
        assert win._explorer._expand_button.icon().cacheKey() != before
    finally:
        tm.set_dark(was_dark)
