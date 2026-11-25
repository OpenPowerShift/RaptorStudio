"""Tags in the GUI: Explorer views (with Here only), the canvas dim, the
Tags dialog and its one-step undo command."""
from __future__ import annotations

import pytest
from PySide6.QtCore import Qt
from PySide6.QtWidgets import QApplication, QTreeWidgetItemIterator

from raptor.core import tags as T
from raptor.core.fileio.rbd import load_rbd
from raptor.core.fileio.typed import row_int

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


def _visible(win) -> set[str]:
    return {_role(r) for r in _rows(win)
            if not r.isHidden() and ":" in _role(r)}


def _blocks(rbd) -> list[int]:
    return [row_int(r.get("ID"), 0) for r in rbd.sections["BLOCK_DATA"].rows
            if row_int(r.get("ID"), 0) > 0]


def _select_tag(win, tag: str | None) -> None:
    combo = win._explorer._group_combo
    index = 0 if tag is None else combo.findData(tag)
    assert index >= 0, f"no tag {tag!r} in the selector"
    combo.setCurrentIndex(index)
    QApplication.processEvents()


def _dialog(win, selected=()):
    from raptor.gui.ui.tags_dialog import TagsDialog
    return TagsDialog(win, win._diagram_dock.view._rbd_ref, list(selected))


def test_a_tag_view_shows_only_tagged_elements_and_their_folders(win):
    rbd = win._diagram_dock.view._rbd_ref
    a, b = (f"block:{i}" for i in _blocks(rbd)[:2])
    T.set_tags(rbd, a, ["pumps"])
    win._explorer.refresh()
    _select_tag(win, "pumps")
    assert a in _visible(win) and b not in _visible(win)
    _select_tag(win, None)
    assert {a, b} <= _visible(win)


def test_the_canvas_is_dimmed_to_the_tag_and_restored(win):
    view = win._diagram_dock.view
    member, other = [bid for bid in view.blocks if bid > 0][:2]
    T.set_tags(view._rbd_ref, f"block:{member}", ["pumps"])
    win._explorer.refresh()
    _select_tag(win, "pumps")
    assert view._block_items[member].opacity() == pytest.approx(1.0)
    assert view._block_items[other].opacity() == pytest.approx(0.25)
    _select_tag(win, None)
    assert view._block_items[other].opacity() == pytest.approx(1.0)


def test_a_tagged_element_keeps_its_hierarchy_oval_bright(win):
    view = win._diagram_dock.view
    rbd = view._rbd_ref
    hier_id, other_hier = list(view._hier_items)[:2]
    block = next(row_int(r.get("ID"), 0)
                 for r in rbd.sections["BLOCK_DATA"].rows
                 if row_int(r.get("ParentId"), 0) == hier_id
                 and row_int(r.get("ID"), 0) > 0)
    T.set_tags(rbd, f"block:{block}", ["inside"])
    win._explorer.refresh()
    _select_tag(win, "inside")
    assert view._hier_items[hier_id].opacity() == pytest.approx(1.0)
    assert view._hier_items[other_hier].opacity() == pytest.approx(0.25)


def test_here_only_keeps_the_tag_inside_the_hierarchy_on_screen(win):
    view = win._diagram_dock.view
    rbd = view._rbd_ref
    hier_id = next(iter(view._hier_items))
    inner = next(f"block:{row_int(r.get('ID'), 0)}"
                 for r in rbd.sections["BLOCK_DATA"].rows
                 if row_int(r.get("ParentId"), 0) == hier_id
                 and row_int(r.get("ID"), 0) > 0)
    outer = next(f"block:{b}" for b in _blocks(rbd)
                 if f"block:{b}" not in T.inside(rbd, hier_id))
    T.set_tags(rbd, inner, ["pumps"])
    T.set_tags(rbd, outer, ["pumps"])
    win._explorer.refresh()
    _select_tag(win, "pumps")
    button = win._explorer._within_button
    assert not button.isEnabled()           # at Home: nothing to be inside
    view.enter_level(hier_id)
    QApplication.processEvents()
    assert button.isEnabled()
    button.setChecked(True)
    QApplication.processEvents()
    assert inner in _visible(win) and outer not in _visible(win)
    view.go_home()
    QApplication.processEvents()
    assert not button.isEnabled()
    button.setChecked(False)


def test_a_tag_view_picks_up_a_newly_tagged_element(win):
    rbd = win._diagram_dock.view._rbd_ref
    first, second = (f"block:{i}" for i in _blocks(rbd)[:2])
    T.set_tags(rbd, first, ["inspect"])
    win._explorer.refresh()
    _select_tag(win, "inspect")
    assert second not in _visible(win)
    win._push_tags(({**T.clean_tags(rbd.tags), second: ["inspect"]}, {}),
                   "Tag")
    QApplication.processEvents()
    win._explorer.refresh()
    _select_tag(win, "inspect")
    assert {first, second} <= _visible(win)


def test_the_tags_command_is_one_undoable_step(win):
    from raptor.gui.ui.tags_dialog import TagsCommand
    view = win._diagram_dock.view
    rbd = view._rbd_ref
    ref = f"block:{_blocks(rbd)[0]}"
    after = ({ref: ["pumps"]}, {"pumps": {"description": "Feed"}})
    view._stack.push(TagsCommand(view, ({}, {}), after))
    assert rbd.tags == {ref: ["pumps"]}
    assert T.tag_description(rbd, "pumps") == "Feed"
    view._stack.undo()
    assert rbd.tags == {} and rbd.tag_info == {}
    view._stack.redo()
    assert T.tag_description(rbd, "pumps") == "Feed"


def test_an_empty_tag_says_so_in_the_explorer(win):
    rbd = win._diagram_dock.view._rbd_ref
    T.set_tag_info(rbd, "spare", description="later")
    win._explorer.refresh()
    _select_tag(win, "spare")
    empty = [r for r in _rows(win) if _role(r) == "empty"]
    assert len(empty) == 1 and "No elements tagged" in empty[0].text(0)
    assert not (empty[0].flags() & Qt.ItemFlag.ItemIsSelectable)


def test_the_selector_shows_colour_and_count_in_the_tooltip(win):
    rbd = win._diagram_dock.view._rbd_ref
    T.set_tags(rbd, f"block:{_blocks(rbd)[0]}", ["rotating"])
    T.set_tag_info(rbd, "rotating", description="Moving parts")
    win._explorer.refresh()
    combo = win._explorer._group_combo
    i = combo.findData("rotating")
    tip = combo.itemData(i, Qt.ItemDataRole.ToolTipRole)
    assert "1 element(s)" in tip and "Moving parts" in tip
    assert not combo.itemIcon(i).isNull()


def test_the_dialog_renames_recolours_and_describes(win):
    rbd = win._diagram_dock.view._rbd_ref
    ref = f"block:{_blocks(rbd)[0]}"
    T.set_tags(rbd, ref, ["pump"])
    dlg = _dialog(win)
    assert dlg._current == "pump"
    dlg._name.setText("feed pump")
    dlg._name.editingFinished.emit()
    dlg._colour.setCurrentIndex(dlg._colour.findData("#9B51E0"))
    dlg._description.setText("Feed")
    dlg._description.editingFinished.emit()
    tags, info = dlg.result_data()
    assert tags == {ref: ["feed pump"]}
    assert info == {"feed pump": {"colour": "#9B51E0", "description": "Feed"}}


def test_the_dialog_keeps_a_hand_edited_custom_colour(win):
    rbd = win._diagram_dock.view._rbd_ref
    T.set_tags(rbd, f"block:{_blocks(rbd)[0]}", ["custom"])
    T.set_tag_info(rbd, "custom", colour="#123456")
    dlg = _dialog(win)
    assert dlg._colour.currentData() == "#123456"
    assert dlg.result_data()[1]["custom"]["colour"] == "#123456"


def test_the_dialog_search_narrows_a_long_tag_list(win):
    rbd = win._diagram_dock.view._rbd_ref
    ref = f"block:{_blocks(rbd)[0]}"
    T.set_tags(rbd, ref, [f"tag {i:03d}" for i in range(300)] + ["pumps"])
    dlg = _dialog(win)
    assert dlg._list.count() == 301
    dlg._search.setText("pum")
    assert dlg._list.count() == 1
    assert dlg._current == "pumps"


def test_new_tag_applies_to_the_selection_and_delete_removes_it(
        win, monkeypatch):
    rbd = win._diagram_dock.view._rbd_ref
    a, b = (f"block:{i}" for i in _blocks(rbd)[:2])
    dlg = _dialog(win, [a, b])
    dlg._new_tag()
    assert T.tag_index(dlg._draft)["tag"] == {a, b}
    import raptor.gui.ui.dialogs as dialogs
    monkeypatch.setattr(dialogs, "question_box", lambda *a, **k: True)
    dlg._delete_tag()
    assert dlg.result_data() == ({}, {})


def test_the_dialog_applies_tags_and_drops_deleted_elements(win):
    ref = f"block:{_blocks(win._diagram_dock.view._rbd_ref)[0]}"
    dlg = _dialog(win, [ref])
    dlg._tags_edit.setText(" pumps, rotating, pumps ")
    dlg._apply_tags()
    dlg._draft.tags["block:424242"] = ["gone"]
    tags, _info = dlg.result_data()
    assert tags == {ref: ["pumps", "rotating"]}


def test_the_dialog_caps_a_huge_member_list(win):
    rbd = win._diagram_dock.view._rbd_ref
    dlg = _dialog(win)
    names = {f"block:{i}": f"B{i}" for i in range(1, 206)}
    dlg._name_map = names
    for ref in names:
        T.set_tags(dlg._draft, ref, ["big"])
    dlg._show_members("big")
    assert "… and 5 more" in dlg._members.toPlainText()
    assert dlg._members_label.text() == "Elements (205)"
    assert rbd is not None


def test_typing_tags_then_ok_keeps_them(win):
    view = win._diagram_dock.view
    ref = f"block:{next(i for i in view.blocks if i > 0)}"
    dlg = _dialog(win, [ref])
    dlg._tags_edit.setText("duty, rotating")
    dlg._tags_edit.textEdited.emit("duty, rotating")
    dlg.accept()
    assert dlg.result_data()[0][ref] == ["duty", "rotating"]


def test_tags_are_reachable_from_the_explorer_and_the_canvas(win):
    from PySide6.QtCore import QPointF
    from PySide6.QtGui import QMouseEvent
    combo = win._explorer._group_combo
    assert combo.isEnabled()
    manage = combo.findData(win._explorer._MANAGE)
    assert manage >= 0
    asked: list = []
    win._explorer.manage_tags_requested.disconnect(win._open_tags_dialog)
    win._explorer.manage_tags_requested.connect(lambda: asked.append(1))
    combo.setCurrentIndex(manage)
    QApplication.processEvents()
    assert asked and combo.currentIndex() == 0

    view = win._diagram_dock.view
    requested: list = []
    view.tags_requested.disconnect(win._open_tags_dialog)
    view.tags_requested.connect(lambda: requested.append(1))
    item = next(iter(view._block_items.values()))

    def choose(menu, _event):
        return next(a for a in menu.actions() if a.text() == "Tags…")
    view._exec_menu = choose
    point = view.mapFromScene(item.sceneBoundingRect().center())
    event = QMouseEvent(QMouseEvent.Type.MouseButtonPress, QPointF(point),
                        QPointF(point), Qt.MouseButton.RightButton,
                        Qt.MouseButton.RightButton,
                        Qt.KeyboardModifier.NoModifier)
    view._show_context_menu(event)
    assert requested


def test_import_table_is_in_the_file_views_import_section():
    from raptor.gui.command_table import BACKSTAGE
    assert "data.import_table" in dict(BACKSTAGE)["Import"]
