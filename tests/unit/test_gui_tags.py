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
    from raptor.gui.ui.tags_panel import TagsCommand
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


def test_tags_are_reachable_from_the_explorer_and_the_canvas(win):
    from PySide6.QtCore import QPointF
    from PySide6.QtGui import QMouseEvent
    combo = win._explorer._group_combo
    assert combo.isEnabled()
    manage = combo.findData(win._explorer._MANAGE)
    assert manage >= 0
    asked: list = []
    win._explorer.manage_tags_requested.disconnect(win._show_tags_dock)
    win._explorer.manage_tags_requested.connect(lambda: asked.append(1))
    combo.setCurrentIndex(manage)
    QApplication.processEvents()
    assert asked and combo.currentIndex() == 0

    view = win._diagram_dock.view
    requested: list = []
    view.tags_requested.disconnect(win._show_tags_dock)
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


def test_inspector_chips_add_and_remove_a_tag_with_one_undo_each(win, tmp_path):
    from raptor.gui.ui.tag_chips import TagChips
    rbd = win._rbd
    bid = _blocks(rbd)[0]
    T.set_tags(rbd, f"block:{bid}", ["pumps"])
    win._inspector.show_block(bid)
    QApplication.processEvents()
    chips = win._inspector.findChild(TagChips)
    assert chips is not None and chips.tags() == ["pumps"]
    chips.add_edit.setText("  wet  area ")
    chips.add_edit.returnPressed.emit()
    QApplication.processEvents()
    assert T.tags_for(rbd, f"block:{bid}") == ["pumps", "wet area"]
    # the Inspector shows the new list
    win._inspector.show_block(bid)
    chips = win._inspector.findChild(TagChips)
    assert chips.tags() == ["pumps", "wet area"]
    chips._remove("pumps")
    QApplication.processEvents()
    assert T.tags_for(rbd, f"block:{bid}") == ["wet area"]
    win._undo_stack.undo()
    assert T.tags_for(rbd, f"block:{bid}") == ["pumps", "wet area"]
    win._undo_stack.undo()
    assert T.tags_for(rbd, f"block:{bid}") == ["pumps"]
    win._inspector.show_block(bid)
    win._inspector.resize(360, 700)
    win._inspector.grab().save(str(tmp_path / "chips.png"))


def test_inspector_chips_are_disabled_while_read_only(win):
    from raptor.gui.ui.tag_chips import TagChips
    bid = _blocks(win._rbd)[0]
    win._inspector.show_block(bid)
    win._inspector.set_read_only(True)
    chips = win._inspector.findChild(TagChips)
    assert not chips.add_edit.isEnabled()
    win._inspector.set_read_only(False)
    assert chips.add_edit.isEnabled()


def test_tag_colours_stripe_tagged_elements_and_list_them(win, tmp_path):
    from raptor.gui.ui.diagram_tag_stripes import MAX_STRIPES
    rbd, view = win._rbd, win._diagram_dock.view
    shown = [bid for bid in view._block_items]
    assert len(shown) >= 2
    T.set_tags(rbd, f"block:{shown[0]}", ["a", "b", "c", "d"])
    T.set_tags(rbd, f"block:{shown[1]}", ["a"])
    win._tag_colours_action.setChecked(True)
    QApplication.processEvents()
    first = view._block_items[shown[0]]._tag_stripe
    assert first is not None and len(first.childItems()) == MAX_STRIPES
    assert "1 more than the stripe shows" in first.toolTip()
    assert win._tag_legend.isVisible()
    assert win._tag_legend.visible_tags() == ["a", "b", "c", "d"]
    # a viewport child would scroll away with the scene
    assert win._tag_legend.parent() is view
    # an undoable tag edit restripes without a rebuild
    win._on_element_tags_changed(f"block:{shown[1]}", [])
    QApplication.processEvents()
    assert view._block_items[shown[1]]._tag_stripe is None
    # the legend's click is the Explorer's tag view
    win._tag_legend.tag_clicked.emit("b")
    QApplication.processEvents()
    assert _visible(win) >= {f"block:{shown[0]}"}
    view.grab().save(str(tmp_path / "tag_colours.png"))
    win._tag_colours_action.setChecked(False)
    assert view._block_items[shown[0]]._tag_stripe is None
    assert not win._tag_legend.isVisible()


def test_the_explorer_tag_filters_the_results_tables_too(win):
    rbd = win._rbd
    hid = next(row_int(r.get("ID"), 0) for r in rbd.sections["HIERARCHY_DATA"].rows
               if row_int(r.get("ID"), 0) > 0)
    T.set_tags(rbd, f"hier:{hid}", ["area"])
    win._explorer.refresh_groups()
    win._push_tag_options()
    combo = win._results._tag_filter_combo
    assert combo.findData("area") > 0
    seen = []
    original = win._results.set_tag_filter
    win._results.set_tag_filter = lambda names: (seen.append(names), original(names))
    _select_tag(win, "area")
    QApplication.processEvents()
    inside = T.names_of(rbd, T.expand(rbd, {f"hier:{hid}"}))
    assert seen[-1] == inside and len(inside) > 1     # what the oval holds
    assert combo.currentData() == "area"
    # choosing All in the results box clears the Explorer too
    win._results._tag_filter_combo.activated.emit(0)
    QApplication.processEvents()
    assert win._explorer.current_tag() == "" and seen[-1] is None


def _focused(widget) -> bool:
    """Is *widget* its window's focus widget? (An offscreen window is never
    the active one, so `hasFocus()` is false even for the right widget.)"""
    return widget.window().focusWidget() is widget


def _scroll_area_of(widget):
    from PySide6.QtWidgets import QScrollArea
    parent = widget.parentWidget()
    while parent is not None and not isinstance(parent, QScrollArea):
        parent = parent.parentWidget()
    return parent


def test_adding_a_tag_keeps_the_inspector_scroll_and_the_add_box_focus(win):
    from raptor.gui.ui.tag_chips import TagChips
    bid = _blocks(win._rbd)[0]
    insp = win._inspector
    insp.resize(300, 260)
    insp.show_block(bid)
    for _ in range(4):
        QApplication.processEvents()
    chips = insp.findChild(TagChips)
    area = _scroll_area_of(chips)
    insp._tabs.setCurrentWidget(area)   # the Tags row lives on one tab
    for _ in range(4):
        QApplication.processEvents()
    assert chips.isVisible()
    bar = area.verticalScrollBar()
    assert bar.maximum() > 0, "the pane must scroll for this test to mean anything"
    area.ensureWidgetVisible(chips)
    bar.setValue(bar.maximum())
    win.activateWindow()
    chips.add_edit.setFocus()
    QApplication.processEvents()
    before = bar.value()
    assert before > 0
    assert _focused(chips.add_edit), "precondition: the box starts focused"
    chips.add_edit.setText("first")
    chips.add_edit.returnPressed.emit()
    for _ in range(4):
        QApplication.processEvents()
    chips2 = insp.findChild(TagChips)
    assert chips2 is chips, "the chips were rebuilt, not updated in place"
    assert chips.tags() == ["first"]
    assert T.tags_for(win._rbd, f"block:{bid}") == ["first"]
    assert _scroll_area_of(chips).verticalScrollBar().value() == before
    assert _focused(chips.add_edit)
    # and a second tag straight away, then a removal
    chips.add_edit.setText("second")
    chips.add_edit.returnPressed.emit()
    for _ in range(4):
        QApplication.processEvents()
    assert chips.tags() == ["first", "second"]
    chips._remove("first")
    for _ in range(4):
        QApplication.processEvents()
    assert chips.tags() == ["second"]
    assert _scroll_area_of(chips).verticalScrollBar().value() == before
    # the other tags are listed in the picker, with their own colours
    assert insp.findChild(TagChips) is chips


def test_new_tags_from_the_inspector_get_distinct_colours_and_one_undo(win):
    rbd = win._rbd
    bid = _blocks(rbd)[0]
    ref = f"block:{bid}"
    win._inspector.show_block(bid)
    for name in ("one", "two", "three"):
        win._on_element_tags_changed(ref, [*T.tags_for(rbd, ref), name])
        QApplication.processEvents()
    colours = [T.tag_colour(rbd, t) for t in ("one", "two", "three")]
    assert colours == list(T.TAG_COLOURS[:3])
    win._undo_stack.undo()
    assert T.tags_for(rbd, ref) == ["one", "two"]
    assert "three" not in T.all_tags(rbd)
    assert "three" not in T.clean_tag_info(rbd.tag_info)
    win._undo_stack.undo()
    win._undo_stack.undo()
    assert T.all_tags(rbd) == [] and T.clean_tag_info(rbd.tag_info) == {}
    win._undo_stack.redo()
    assert T.tag_colour(rbd, "one") == T.TAG_COLOURS[0]
