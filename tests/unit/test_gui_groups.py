"""Groups and tags: Explorer views and the one-step undo command."""
from __future__ import annotations

import pytest
from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QApplication, QDialog, QMessageBox, QTreeWidgetItemIterator,
)

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


def test_a_group_view_and_focus_mode_agree_on_opacity(win):
    """Changing the group must re-apply focus mode: a group member that is
    not selected stays dim while focus mode is on."""
    view = win._diagram_dock.view
    ids = [bid for bid in view._block_items if bid > 0]
    member, other = ids[0], ids[1]
    ref = G.element_ref("block", member)
    view._scene.clearSelection()
    view.set_focus_mode(True)                 # nothing selected: all dim
    view.set_group_view({ref})
    assert view._block_items[member].opacity() == pytest.approx(0.25)
    assert view._block_items[other].opacity() == pytest.approx(0.25)

    view._block_items[member].setSelected(True)
    view.set_group_view({ref})
    assert view._block_items[member].opacity() == pytest.approx(1.0)
    assert view._block_items[other].opacity() == pytest.approx(0.25)
    view.set_focus_mode(False)
    view.set_group_view(None)


def test_a_tag_view_picks_up_a_newly_tagged_element(win):
    """The Explorer caches the active view's refs for typing in Find; a
    model edit must invalidate that cache."""
    rbd = win._diagram_dock.view._rbd_ref
    from raptor.core.fileio.typed import row_int
    ids = [row_int(r.get("ID"), 0)
           for r in rbd.sections["BLOCK_DATA"].rows
           if row_int(r.get("ID"), 0) > 0]
    first, second = (G.element_ref("block", ids[0]),
                     G.element_ref("block", ids[1]))
    G.set_tags(rbd, first, ["inspect"])
    win._explorer.refresh()
    _select_group(win, "tag", "inspect")
    assert first in _visible_element_roles(win)
    assert second not in _visible_element_roles(win)

    G.set_tags(rbd, second, ["inspect"])
    win._explorer.refresh()
    _select_group(win, "tag", "inspect")
    assert {first, second} <= _visible_element_roles(win)


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


def test_an_empty_group_says_so_in_the_explorer(win):
    rbd = win._diagram_dock.view._rbd_ref
    G.add_group(rbd, "Spare")
    win._explorer.refresh()
    _select_group(win, "group", "g_spare")

    empty = [r for r in _rows(win) if _role(r) == "empty"]
    assert len(empty) == 1
    assert not empty[0].isHidden()
    assert "no elements" in empty[0].text(0).lower()
    assert not (empty[0].flags() & Qt.ItemFlag.ItemIsSelectable)

    win._explorer._group_combo.setCurrentIndex(0)
    QApplication.processEvents()
    assert not [r for r in _rows(win) if _role(r) == "empty"]


def test_a_group_member_keeps_its_hierarchy_oval_bright(win):
    """Dimming a member's enclosing oval would hide where the member is."""
    view = win._diagram_dock.view
    rbd = view._rbd_ref
    from raptor.core.fileio.typed import row_int
    hier_id, other_hier = list(view._hier_items)[:2]
    block = next(row_int(r.get("ID"), 0)
                 for r in rbd.sections["BLOCK_DATA"].rows
                 if row_int(r.get("ParentId"), 0) == hier_id
                 and row_int(r.get("ID"), 0) > 0)
    G.add_group(rbd, "Inside", [G.element_ref("block", block)])
    win._explorer.refresh()
    _select_group(win, "group", "g_inside")

    assert view._hier_items[hier_id].opacity() == pytest.approx(1.0)
    assert view._hier_items[other_hier].opacity() == pytest.approx(0.25)


def test_the_selector_tooltip_counts_a_groups_elements(win):
    rbd = win._diagram_dock.view._rbd_ref
    ref = G.element_ref("block", 1)
    G.set_tags(rbd, ref, ["rotating"])
    G.add_group(rbd, "Pumps", [ref], tag="rotating")
    G.add_group(rbd, "Spare")
    win._explorer.refresh()

    combo = win._explorer._group_combo
    tips = {combo.itemText(i): combo.itemData(i, Qt.ItemDataRole.ToolTipRole)
            for i in range(combo.count())}
    assert "1 element(s)" in tips["Group: Pumps"]
    assert "element(s)" in tips["Group: Spare"]


def _dialog(win, selected=()):
    from raptor.gui.ui.groups_dialog import GroupsDialog
    return GroupsDialog(win, win._diagram_dock.view._rbd_ref, selected)


def _select_dialog_row(dlg, group_id: str) -> int:
    row = next(i for i, g in enumerate(dlg._groups) if g["id"] == group_id)
    dlg._list.setCurrentRow(row)
    QApplication.processEvents()
    return row


def test_the_dialog_keeps_a_hand_edited_custom_colour(win):
    rbd = win._diagram_dock.view._rbd_ref
    ref = G.element_ref("block", 1)
    G.add_group(rbd, "Custom", [ref], colour="#123456")
    dlg = _dialog(win)
    _select_dialog_row(dlg, "g_custom")

    assert dlg._colour.currentData() == "#123456"
    _tags, groups = dlg.result_data()
    assert groups[0]["colour"] == "#123456"


def test_the_dialog_refuses_duplicate_group_names(win, monkeypatch):
    rbd = win._diagram_dock.view._rbd_ref
    ref = G.element_ref("block", 1)
    G.add_group(rbd, "One", [ref])
    G.add_group(rbd, "Two", [ref])
    dlg = _dialog(win, [ref])
    _select_dialog_row(dlg, "g_two")

    warnings: list[str] = []
    monkeypatch.setattr(
        QMessageBox, "warning",
        lambda _parent, _title, text, *a, **k: warnings.append(text)
        or QMessageBox.StandardButton.Ok)
    dlg._name.setText("one")            # case-insensitive duplicate
    dlg.accept()
    assert warnings and "more than one group called" in warnings[0]
    assert dlg.result() != QDialog.DialogCode.Accepted

    dlg._name.setText("Three")
    dlg.accept()
    assert dlg.result() == QDialog.DialogCode.Accepted


def test_the_dialog_applies_tags_and_drops_deleted_elements(win):
    ref = G.element_ref("block", 1)
    dlg = _dialog(win, [ref])
    dlg._tags_edit.setText(" pumps, rotating, pumps ")
    dlg._apply_tags()
    # A reference the model no longer has must not be written back.
    dlg._tags["block:424242"] = ["gone"]
    tags, _groups = dlg.result_data()
    assert tags == {ref: ["pumps", "rotating"]}


def test_the_dialog_caps_a_huge_member_list(win):
    members = [G.element_ref("block", i) for i in range(1, 206)]
    dlg = _dialog(win)
    dlg._show_members({"id": "g_big", "name": "Big",
                       "colour": "#2F80ED", "members": members})
    text = dlg._members.toPlainText()
    assert "… and 5 more" in text
    assert dlg._members_label.text().startswith("Members (205")


def test_typing_tags_then_ok_keeps_them(win):
    from raptor.core import groups as G
    from raptor.gui.ui.groups_dialog import GroupsDialog
    view = win._diagram_dock.view
    rbd = view._rbd_ref
    bid = next(i for i in view.blocks if i > 0)
    ref = G.element_ref("block", bid)
    dialog = GroupsDialog(win, rbd, [ref])
    dialog._new_group()
    dialog._tags_edit.setText("duty, rotating")
    dialog._tags_edit.textEdited.emit("duty, rotating")
    dialog.accept()
    tags, _groups = dialog.result_data()
    assert tags[ref] == ["duty", "rotating"]
