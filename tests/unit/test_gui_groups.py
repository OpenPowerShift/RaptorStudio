"""Groups and tags: Explorer views and the one-step undo command."""
from __future__ import annotations

import pytest
from PySide6.QtCore import Qt
from PySide6.QtWidgets import QApplication, QTreeWidgetItemIterator

from raptor.core import groups as G
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
    w.resize(1200, 800)
    w.show()
    w._load_into_app(load_rbd(MODEL), MODEL)
    QApplication.processEvents()
    yield w
    w._dirty = False
    w.close()


def _rows(win):
    out = []
    it = QTreeWidgetItemIterator(win._explorer._tree)
    while it.value() is not None:
        out.append(it.value())
        it += 1
    return out


def _role(item) -> str:
    return str(item.data(0, Qt.ItemDataRole.UserRole))


def _visible_element_roles(win) -> set[str]:
    return {_role(r) for r in _rows(win)
            if not r.isHidden() and ":" in _role(r)}


def _select_group(win, kind: str, value: str) -> None:
    combo = win._explorer._group_combo
    for i in range(combo.count()):
        if tuple(combo.itemData(i) or ()) == (kind, value):
            combo.setCurrentIndex(i)
            QApplication.processEvents()
            return
    raise AssertionError(f"no {kind} {value!r} in the group selector")


def test_a_group_view_shows_only_its_members_and_their_folders(win):
    rbd = win._diagram_dock.view._rbd_ref
    # Real ids from the model rows, not the Explorer's display tuples.
    from raptor.core.fileio.typed import row_int
    all_blocks = [row_int(r.get("ID"), 0)
                  for r in rbd.sections["BLOCK_DATA"].rows
                  if row_int(r.get("ID"), 0) > 0]
    member = G.element_ref("block", all_blocks[0])
    other = G.element_ref("block", all_blocks[1])
    G.add_group(rbd, "One block", [member])
    win._explorer.refresh()
    _select_group(win, "group", "g_one_block")

    visible = _visible_element_roles(win)
    assert member in visible
    assert other not in visible
    combo = win._explorer._group_combo
    combo.setCurrentIndex(0)
    QApplication.processEvents()
    assert member in _visible_element_roles(win)
    assert other in _visible_element_roles(win)


def test_the_canvas_is_dimmed_to_the_group_and_restored(win):
    view = win._diagram_dock.view
    rbd = view._rbd_ref
    visible = [bid for bid in view.blocks if bid > 0]
    assert len(visible) >= 2
    member, other = visible[0], visible[1]
    G.add_group(rbd, "One visible block",
                [G.element_ref("block", member)])
    win._explorer.refresh()
    _select_group(win, "group", "g_one_visible_block")
    assert view._block_items[member].opacity() == pytest.approx(1.0)
    assert view._block_items[other].opacity() == pytest.approx(0.25)
    win._explorer._group_combo.setCurrentIndex(0)
    QApplication.processEvents()
    assert view._block_items[other].opacity() == pytest.approx(1.0)


def test_a_tag_view_matches_the_tag_and_the_group_view_uses_its_query(win):
    rbd = win._diagram_dock.view._rbd_ref
    from raptor.core.fileio.typed import row_int
    ids = [row_int(r.get("ID"), 0)
           for r in rbd.sections["BLOCK_DATA"].rows
           if row_int(r.get("ID"), 0) > 0]
    ref = G.element_ref("block", ids[2])
    other = G.element_ref("block", ids[3])
    G.set_tags(rbd, ref, ["rotating"])
    G.set_tags(rbd, other, ["inspect"])
    G.add_group(rbd, "Rotating", tag="rotating")
    win._explorer.refresh()
    # The group's own tag query is shown through the group, not repeated
    # as a separate tag entry.
    _select_group(win, "group", "g_rotating")
    assert ref in _visible_element_roles(win)
    assert other not in _visible_element_roles(win)
    # A tag not claimed by a group gets its own view.
    _select_group(win, "tag", "inspect")
    assert other in _visible_element_roles(win)
    assert ref not in _visible_element_roles(win)


def test_groups_command_is_one_undoable_step(win):
    from raptor.gui.ui.groups_dialog import GroupsCommand
    view = win._diagram_dock.view
    rbd = view._rbd_ref
    before = ({}, [])
    ref = G.element_ref("block", 1)
    after = ({ref: ["pumps"]}, [{"id": "g_pumps", "name": "Pumps",
                                 "colour": "#2F80ED", "members": [ref]}])
    stack = view._stack
    stack.push(GroupsCommand(view, before, after))
    assert rbd.tags == {ref: ["pumps"]}
    assert rbd.groups[0]["name"] == "Pumps"
    stack.undo()
    assert rbd.tags == {}
    assert rbd.groups == []
    stack.redo()
    assert rbd.groups[0]["id"] == "g_pumps"
