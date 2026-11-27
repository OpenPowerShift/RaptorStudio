"""The Tags dock: non-modal, follows the canvas selection, every action one
undo step, members in a sortable table."""
from __future__ import annotations

import pytest
from PySide6.QtCore import Qt
from PySide6.QtWidgets import QApplication

from raptor.core import tags as T
from raptor.core.fileio.rbd import load_rbd
from raptor.core.fileio.typed import row_int

pytestmark = pytest.mark.gui

MODEL = "tests/golden/HierarchyExample/input.rbd"


@pytest.fixture(autouse=True, scope="module")
def _qapp():
    return QApplication.instance() or QApplication([])


@pytest.fixture
def win():
    from raptor.gui.ui import settings
    from raptor.gui.ui.main_window import MainWindow
    settings.set_value("startup/show_start_page", False)
    w = MainWindow()
    w.resize(1400, 850)
    w.show()
    w._load_into_app(load_rbd(MODEL), MODEL)
    _settle()
    yield w
    w._dirty = False
    w.close()


def _settle(n: int = 6) -> None:
    for _ in range(n):
        QApplication.processEvents()


def _blocks(rbd) -> list[int]:
    return [row_int(r.get("ID"), 0) for r in rbd.sections["BLOCK_DATA"].rows
            if row_int(r.get("ID"), 0) > 0]


def _shown(win, n: int = 2) -> list[int]:
    """Blocks drawn at the level on screen (the others cannot be selected)."""
    return [b for b in win._diagram_dock.view._block_items if b > 0][:n]


def _select(win, ids) -> None:
    view = win._diagram_dock.view
    for item in view._scene.selectedItems():
        item.setSelected(False)
    for i in ids:
        view._block_items[i].setSelected(True)
    _settle()


def _open(win):
    win._show_tags_dock()
    _settle()
    return win._tags_panel


def test_the_dock_is_hidden_until_asked_for_and_is_not_modal(win):
    assert not win._tags_dock.isVisible()
    assert not win._tags_toggle_action.isChecked()
    panel = _open(win)
    assert win._tags_dock.isVisible() and win._tags_toggle_action.isChecked()
    assert not win.isModal() and not panel.window().isModal()
    win._tags_toggle_action.setChecked(False)
    _settle()
    # a side dock collapses to its stub rather than closing
    assert not win._tags_toggle_action.isChecked()
    assert not panel.isVisible()


def test_the_buttons_follow_the_live_canvas_selection(win):
    a, b = _shown(win)
    T.set_tags(win._rbd, f"block:{a}", ["pumps"])
    panel = _open(win)
    assert panel.current_tag() == "pumps"
    assert not panel._add_sel.isEnabled() and not panel._remove_sel.isEnabled()
    _select(win, [a, b])
    assert panel._add_sel.isEnabled() and panel._remove_sel.isEnabled()
    assert "(1)" in panel._add_sel.text()       # b lacks it
    assert "(1)" in panel._remove_sel.text()    # a has it
    assert "2 elements" in panel._selection_label.text()
    _select(win, [a])
    assert not panel._add_sel.isEnabled() and panel._remove_sel.isEnabled()
    _select(win, [])
    assert not panel._add_sel.isEnabled() and not panel._remove_sel.isEnabled()


def test_add_and_remove_selection_are_one_undo_step_each(win):
    a, b = _shown(win)
    T.set_tag_info(win._rbd, "pumps", description="x")
    panel = _open(win)
    _select(win, [a, b])
    depth = win._undo_stack.count()
    panel._add_sel.click()
    _settle()
    assert win._undo_stack.count() == depth + 1
    assert T.tag_index(win._rbd)["pumps"] == {f"block:{a}", f"block:{b}"}
    assert panel._table.rowCount() == 2
    panel._remove_sel.click()
    _settle()
    assert win._undo_stack.count() == depth + 2
    assert "pumps" not in T.tag_index(win._rbd)
    win._undo_stack.undo()
    _settle()
    assert panel._table.rowCount() == 2
    win._undo_stack.undo()
    _settle()
    assert panel._table.rowCount() == 0


def test_new_tag_takes_the_selection_a_fresh_colour_and_undoes_whole(win):
    a, b = _shown(win)
    panel = _open(win)
    _select(win, [a, b])
    panel._new_button.click()
    _settle()
    panel._new_button.click()
    _settle()
    assert T.all_tags(win._rbd) == ["tag", "tag 2"]
    assert (T.tag_colour(win._rbd, "tag"), T.tag_colour(win._rbd, "tag 2")) \
        == T.TAG_COLOURS[:2]
    assert T.tag_index(win._rbd)["tag"] == {f"block:{a}", f"block:{b}"}
    win._undo_stack.undo()
    _settle()
    assert T.all_tags(win._rbd) == ["tag"]
    win._undo_stack.undo()
    _settle()
    assert T.all_tags(win._rbd) == [] and not T.clean_tag_info(win._rbd.tag_info)


def test_rename_recolour_describe_delete_each_commit_at_once(win, monkeypatch):
    a = _blocks(win._rbd)[0]
    ref = f"block:{a}"
    T.set_tags(win._rbd, ref, ["pump"])
    panel = _open(win)
    depth = win._undo_stack.count()
    panel._name.setText("feed pump")
    panel._name.editingFinished.emit()
    _settle()
    assert T.tags_for(win._rbd, ref) == ["feed pump"]
    assert panel.current_tag() == "feed pump"
    panel._colour.setCurrentIndex(panel._colour.findData("#9B51E0"))
    _settle()
    assert T.tag_colour(win._rbd, "feed pump") == "#9B51E0"
    panel._description.setText("Feed")
    panel._description.editingFinished.emit()
    _settle()
    assert T.tag_description(win._rbd, "feed pump") == "Feed"
    assert win._undo_stack.count() == depth + 3
    import raptor.gui.ui.dialogs as dialogs
    monkeypatch.setattr(dialogs, "question_box", lambda *a, **k: True)
    panel._delete_button.click()
    _settle()
    assert T.all_tags(win._rbd) == []
    assert win._undo_stack.count() == depth + 4
    for _ in range(4):
        win._undo_stack.undo()
    _settle()
    assert T.tags_for(win._rbd, ref) == ["pump"]


def test_search_narrows_a_long_tag_list(win):
    ref = f"block:{_blocks(win._rbd)[0]}"
    T.set_tags(win._rbd, ref, [f"tag {i:03d}" for i in range(300)] + ["pumps"])
    panel = _open(win)
    assert panel._list.count() == 301
    panel._search.setText("pum")
    _settle()
    assert panel._list.count() == 1 and panel.current_tag() == "pumps"


def test_members_table_columns_sort_filter_and_cap(win, monkeypatch):
    rbd = win._rbd
    refs = sorted(T.valid_refs_from_model(rbd))
    for ref in refs:
        T.add_tag(rbd, ref, "all")
    panel = _open(win)
    table = panel._table
    heads = [table.horizontalHeaderItem(i).text() for i in range(4)]
    assert heads == ["Name", "Kind", "ID", "Level"]
    assert table.rowCount() == len(refs)
    kinds = {table.item(r, 1).text() for r in range(table.rowCount())}
    assert kinds <= {"Block", "Node", "Event", "Hierarchy"} and "Block" in kinds
    # sort by ID, ascending then descending
    panel._sort_by(2)
    ids = [int(table.item(r, 2).text()) for r in range(table.rowCount())]
    assert ids == sorted(ids)
    panel._sort_by(2)
    ids = [int(table.item(r, 2).text()) for r in range(table.rowCount())]
    assert ids == sorted(ids, reverse=True)
    # the filter searches name, kind, id and level
    panel._filter.setText("hierarchy")
    assert 0 < table.rowCount() < len(refs)
    assert all("hierarchy" in " ".join(table.item(r, c).text()
                                       for c in range(4)).casefold()
               for r in range(table.rowCount()))
    panel._filter.setText("zzzz")
    assert table.rowCount() == 0 and panel._more_label.isVisible()
    panel._filter.clear()
    # a cap
    import raptor.gui.ui.tags_panel as tp
    monkeypatch.setattr(tp, "_MEMBER_ROWS", 3)
    panel._fill_table()
    assert table.rowCount() == 3
    assert "first 3 of" in panel._more_label.text()


def test_double_clicking_a_row_locates_the_element(win):
    a = _shown(win, 1)[0]
    T.set_tags(win._rbd, f"block:{a}", ["pumps"])
    panel = _open(win)
    _select(win, [])
    asked: list = []
    panel.locate_requested.connect(lambda k, i: asked.append((k, i)))
    panel._table.cellDoubleClicked.emit(0, 0)
    assert asked == [("block", a)]
    _settle()
    assert win._selected_refs() == [f"block:{a}"]
    assert panel._add_sel.isEnabled() is False   # it already has the tag
    assert panel._remove_sel.isEnabled()


def test_enter_in_the_table_locates_the_row(win):
    from PySide6.QtTest import QTest
    a = _shown(win, 1)[0]
    T.set_tags(win._rbd, f"block:{a}", ["pumps"])
    panel = _open(win)
    asked: list = []
    panel.locate_requested.connect(lambda k, i: asked.append((k, i)))
    panel._table.setCurrentCell(0, 0)
    QTest.keyClick(panel._table, Qt.Key.Key_Return)
    assert asked == [("block", a)]


def test_ribbon_and_command_stay_consistent(win):
    assert win._tags_action.isEnabled()
    win._tags_action.trigger()
    _settle()
    assert win._tags_dock.isVisible()
    assert win._tags_toggle_action.isChecked()


def test_dock_state_survives_a_layout_save_and_restore(win):
    from raptor.gui.ui.main_window import MainWindow
    _open(win)
    opened = win._layout_blob()
    win._tags_toggle_action.setChecked(False)
    _settle()
    closed = win._layout_blob()
    # a new session restores the layout the last one saved
    for blob, want in ((opened, True), (closed, False)):
        other = MainWindow()
        other.resize(1400, 850)
        other.show()
        other._load_into_app(load_rbd(MODEL), MODEL)
        _settle()
        assert other._apply_layout_blob(blob)
        _settle(10)
        assert other._tags_toggle_action.isChecked() is want
        assert other._tags_panel.isVisible() is want
        other._dirty = False
        other.close()


def test_reset_layout_hides_the_dock_and_it_can_come_back(win):
    _open(win)
    win.reset_layout()
    _settle(10)
    assert not win._tags_dock.isVisible()
    assert not win._tags_toggle_action.isChecked()
    win._show_tags_dock()
    _settle()
    assert win._tags_dock.isVisible()
    assert win._tags_panel._list is not None
