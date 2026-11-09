"""Issue #198: Alt is the canvas fine-move modifier (step / 10).

The itemChange re-snap is why these tests assert the exact scene distance
rather than just "it moved": the snap state itself has to be subdivided
during the gesture or every Alt move would land back on the coarse grid.
"""
from __future__ import annotations

import pytest
from PySide6.QtCore import QEvent, QPointF, Qt
from PySide6.QtGui import QMouseEvent
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication

from raptor.api.build import build_from_spec
from raptor.core.fileio.rbd import load_rbd
from raptor.gui.ui.diagram import DiagramView

pytestmark = pytest.mark.gui

MODEL = "tests/golden/Phasing3/input.rbd"


@pytest.fixture(scope="module")
def qapp():
    return QApplication.instance() or QApplication([])


def _view():
    rbd = build_from_spec({
        "name": "Fine",
        "blocks": [{"name": "A"}, {"name": "B"}],
        "nodes": [{"id": 1, "name": "Start", "type": "start"},
                  {"id": 2, "name": "End", "type": "end"}],
        "links": [{"from": "Start", "to": "A"}, {"from": "A", "to": "B"},
                  {"from": "B", "to": "End"}],
        "system": {"term_condition": 100, "num_runs": 1},
    }, auto_layout=False)
    view = DiagramView()
    view.load_rbd(rbd)
    view.auto_layout(direction="LR")
    view.resize(800, 500)
    view.show()
    return view


def _select_first_block(view):
    item = next(iter(view._block_items.values()))
    view._scene.clearSelection()
    item.setSelected(True)
    view.setFocus()
    QApplication.processEvents()
    return item


def test_alt_arrow_moves_by_exactly_one_tenth_of_a_snap_step(qapp):
    v = _view()
    item = _select_first_block(v)
    step = v._snap_state["x"]
    assert step > 0
    x0, y0 = item.scenePos().x(), item.scenePos().y()
    v._stack.clear()

    QTest.keyClick(v, Qt.Key.Key_Right, Qt.KeyboardModifier.AltModifier)
    QApplication.processEvents()

    assert item.scenePos().x() == pytest.approx(x0 + step / 10)
    assert item.scenePos().y() == pytest.approx(y0)
    assert v._stack.count() == 1, "the whole nudge is one undo command"
    v._stack.undo()
    assert (item.scenePos().x(), item.scenePos().y()) == pytest.approx(
        (x0, y0))


def test_alt_arrow_is_exact_even_from_an_off_fine_grid_position(qapp):
    """A loaded legacy element need not sit on a tenth-step line; Alt must
    still move by exactly step/10, not catch up to the nearest line."""
    v = DiagramView()
    v.load_rbd(load_rbd("tests/golden/FactoryDemo/input.rbd"))
    v.resize(1000, 700)
    v.show()
    item = next(iter(v._block_items.values()))
    v._scene.clearSelection()
    item.setSelected(True)
    v.setFocus()
    QApplication.processEvents()
    step = v._snap_state["x"]
    x0 = item.scenePos().x()
    v._stack.clear()

    QTest.keyClick(v, Qt.Key.Key_Right, Qt.KeyboardModifier.AltModifier)
    QApplication.processEvents()

    assert item.scenePos().x() == pytest.approx(x0 + step / 10)


def test_alt_drag_uses_the_fine_grid_and_is_one_undo_step(qapp):
    v = _view()
    item = _select_first_block(v)
    step = v._snap_state["x"]
    fine = step / 10
    assert fine > 0
    x0, y0 = item.scenePos().x(), item.scenePos().y()
    v._stack.clear()

    down = QPointF(v.mapFromScene(item.sceneBoundingRect().center()))
    # Six fine steps -> comfortably past the click/drag deadzone even at
    # this view's fit zoom.
    target_scene = item.sceneBoundingRect().center() + QPointF(fine * 6, 0)
    target = QPointF(v.mapFromScene(target_scene))

    v.mousePressEvent(QMouseEvent(
        QEvent.Type.MouseButtonPress, down, down,
        Qt.MouseButton.LeftButton, Qt.MouseButton.LeftButton,
        Qt.KeyboardModifier.AltModifier))
    for i in range(1, 7):
        pos = down + (target - down) * i / 6
        v.mouseMoveEvent(QMouseEvent(
            QEvent.Type.MouseMove, pos, pos,
            Qt.MouseButton.NoButton, Qt.MouseButton.LeftButton,
            Qt.KeyboardModifier.AltModifier))
        QApplication.processEvents()
    v.mouseReleaseEvent(QMouseEvent(
        QEvent.Type.MouseButtonRelease, target, target,
        Qt.MouseButton.LeftButton, Qt.MouseButton.NoButton,
        Qt.KeyboardModifier.AltModifier))
    QApplication.processEvents()

    dx = item.scenePos().x() - x0
    assert dx == pytest.approx(fine * 6), \
        "the dragged item must move by the exact fine distance"
    assert abs(dx) < step, "the drag must be finer than the normal snap"
    assert v._stack.count() == 1
    v._stack.undo()
    assert (item.scenePos().x(), item.scenePos().y()) == pytest.approx(
        (x0, y0))


def test_mixed_block_and_annotation_nudge_is_one_undo_step(qapp):
    v = _view()
    block = next(iter(v._block_items.values()))
    note = v._begin_annotation("text", QPointF(30, 30))
    note.end_edit()
    v._scene.clearSelection()
    block.setSelected(True)
    note.setSelected(True)
    v.setFocus()
    QApplication.processEvents()
    step = v._snap_state["x"]
    block_x = block.scenePos().x()
    note_x = note.scenePos().x()
    v._stack.clear()

    QTest.keyClick(v, Qt.Key.Key_Right, Qt.KeyboardModifier.AltModifier)
    QApplication.processEvents()

    assert block.scenePos().x() == pytest.approx(block_x + step / 10)
    assert note.scenePos().x() == pytest.approx(note_x + step / 10)
    assert v._stack.count() == 1, "mixed selection must be one macro"
    v._stack.undo()
    assert block.scenePos().x() == pytest.approx(block_x)
    assert note.scenePos().x() == pytest.approx(note_x)


def _mouse(kind, pos, buttons, button, mods=Qt.KeyboardModifier.NoModifier):
    return QMouseEvent(kind, pos, pos, button, buttons, mods)


def test_two_annotations_nudge_is_one_undo_step(qapp):
    v = _view()
    one = v._begin_annotation("text", QPointF(30, 30))
    one.end_edit()
    two = v._begin_annotation("text", QPointF(200, 30))
    two.end_edit()
    v._scene.clearSelection()
    one.setSelected(True)
    two.setSelected(True)
    v.setFocus()
    QApplication.processEvents()
    starts = [one.scenePos().x(), two.scenePos().x()]
    v._stack.clear()

    QTest.keyClick(v, Qt.Key.Key_Right, Qt.KeyboardModifier.AltModifier)
    QApplication.processEvents()
    assert v._stack.count() == 1
    v._stack.undo()
    assert [one.scenePos().x(), two.scenePos().x()] == pytest.approx(starts)
    assert starts[0] != starts[1]                     # both really moved


def test_annotation_and_grip_nudge_is_one_undo_step(qapp):
    v = _phasing_view()
    try:
        note = v._begin_annotation("text", QPointF(40, 40))
        note.end_edit()
        link = v._links[0]
        mid = link.nearest_point(QPointF(
            (link._p0.x() + link._p3.x()) / 2,
            (link._p0.y() + link._p3.y()) / 2))
        mx, my = v.scene_to_model(mid)
        grip = v.insert_routing_node(link._link_num, mx, my)
        assert grip is not None
        v._scene.clearSelection()
        note.setSelected(True)
        grip.setSelected(True)
        v.setFocus()
        QApplication.processEvents()
        note_x = note.scenePos().x()
        grip_x = grip.pos().x()
        v._stack.clear()

        QTest.keyClick(v, Qt.Key.Key_Right, Qt.KeyboardModifier.AltModifier)
        QApplication.processEvents()
        assert v._stack.count() == 1
        v._stack.undo()
        assert note.scenePos().x() == pytest.approx(note_x)
        assert grip.pos().x() == pytest.approx(grip_x)
    finally:
        v.close()
        v.deleteLater()
        QApplication.processEvents()


def test_mixed_element_and_annotation_drag_is_one_undo_step(qapp):
    v = _view()
    block = next(iter(v._block_items.values()))
    note = v._begin_annotation("text", QPointF(30, 30))
    note.end_edit()
    v._scene.clearSelection()
    block.setSelected(True)
    note.setSelected(True)
    v.setFocus()
    QApplication.processEvents()
    bx, nx = block.scenePos().x(), note.scenePos().x()
    v._stack.clear()
    v.scale(2.0, 2.0)

    down = QPointF(v.mapFromScene(block.sceneBoundingRect().center()))
    target = down + QPointF(40, 0)
    v.mousePressEvent(_mouse(
        QEvent.Type.MouseButtonPress, down,
        Qt.MouseButton.LeftButton, Qt.MouseButton.LeftButton))
    for i in range(1, 5):
        pos = down + (target - down) * i / 4
        v.mouseMoveEvent(_mouse(
            QEvent.Type.MouseMove, pos,
            Qt.MouseButton.LeftButton, Qt.MouseButton.NoButton,
            Qt.KeyboardModifier.AltModifier))
        QApplication.processEvents()
    v.mouseReleaseEvent(_mouse(
        QEvent.Type.MouseButtonRelease, target,
        Qt.MouseButton.NoButton, Qt.MouseButton.LeftButton,
        Qt.KeyboardModifier.AltModifier))
    QApplication.processEvents()

    assert block.scenePos().x() != bx and note.scenePos().x() != nx
    assert v._stack.count() == 1
    v._stack.undo()
    assert block.scenePos().x() == pytest.approx(bx)
    assert note.scenePos().x() == pytest.approx(nx)


def test_mixed_nudge_does_not_run_the_rule_gate(qapp, monkeypatch):
    import raptor.gui.ui.rule_gate as rule_gate

    calls = []
    monkeypatch.setattr(rule_gate, "check",
                        lambda *a, **k: calls.append(1) or [])
    v = _view()
    block = next(iter(v._block_items.values()))
    note = v._begin_annotation("text", QPointF(30, 30))
    note.end_edit()
    v._scene.clearSelection()
    block.setSelected(True)
    note.setSelected(True)
    v.setFocus()
    QApplication.processEvents()
    v._stack.clear()
    calls.clear()                      # setup/load pushes are not the nudge

    QTest.keyClick(v, Qt.Key.Key_Right, Qt.KeyboardModifier.AltModifier)
    QApplication.processEvents()
    assert v._stack.count() == 1
    assert calls == [], "mixed geometry nudge must stay off the rule gate"


def test_alt_pressed_after_press_is_a_fine_drag_with_undo(qapp):
    """Alt can go down after the mouse press; the fine snap and the
    release epsilon must both follow it for a one-tenth-step drag."""
    v = _view()
    item = _select_first_block(v)
    v._snap_state["x"] = v._snap_state["y"] = 4.0
    x0 = item.scenePos().x()
    x0 = round(x0 / 0.4) * 0.4
    v._snap_state["x"] = v._snap_state["y"] = 0.0
    item.setPos(x0, item.scenePos().y())
    v._snap_state["x"] = v._snap_state["y"] = 4.0
    v._stack.clear()
    v.scale(20.0, 20.0)

    down = QPointF(v.mapFromScene(item.sceneBoundingRect().center()))
    target = QPointF(v.mapFromScene(
        item.sceneBoundingRect().center() + QPointF(0.4, 0)))
    v.mousePressEvent(_mouse(
        QEvent.Type.MouseButtonPress, down,
        Qt.MouseButton.LeftButton, Qt.MouseButton.LeftButton))
    v.mouseMoveEvent(_mouse(
        QEvent.Type.MouseMove, target,
        Qt.MouseButton.LeftButton, Qt.MouseButton.NoButton,
        Qt.KeyboardModifier.AltModifier))
    QApplication.processEvents()
    v.mouseReleaseEvent(_mouse(
        QEvent.Type.MouseButtonRelease, target,
        Qt.MouseButton.NoButton, Qt.MouseButton.LeftButton,
        Qt.KeyboardModifier.AltModifier))
    QApplication.processEvents()

    assert item.scenePos().x() == pytest.approx(x0 + 0.4)
    assert v._stack.count() == 1
    v._stack.undo()
    assert item.scenePos().x() == pytest.approx(x0)


def test_a_one_fine_step_drag_still_gets_an_undo_command(qapp):
    """At a 4-unit grid the fine step is 0.4, below the old 0.5 release
    threshold: the gesture moved on screen but produced no MoveCmd."""
    v = _view()
    item = _select_first_block(v)
    v._snap_state["x"] = v._snap_state["y"] = 4.0
    bid = item._block_id
    x0, y0 = item.scenePos().x(), item.scenePos().y()
    v._stack.clear()
    # Place the one-step move directly (snap silenced), then verify the
    # release maths accepts a delta below the ordinary 0.5 threshold.
    v._snap_state["x"] = v._snap_state["y"] = 0.0
    item.setPos(x0 + 0.4, y0)
    v._snap_state["x"] = v._snap_state["y"] = 4.0
    v._drag_start = [("block", bid, (x0, y0))]
    v._press_positions = {("block", bid): (x0, y0)}
    v._fine_drag = True
    v._drag_primary = None
    v.mouseReleaseEvent(QMouseEvent(
        QEvent.Type.MouseButtonRelease, QPointF(0, 0), QPointF(0, 0),
        Qt.MouseButton.LeftButton, Qt.MouseButton.NoButton,
        Qt.KeyboardModifier.AltModifier))
    QApplication.processEvents()
    assert item.scenePos().x() == pytest.approx(x0 + 0.4)
    assert v._stack.count() == 1
    v._stack.undo()
    assert item.scenePos().x() == pytest.approx(x0)


def _phasing_view():
    v = DiagramView()
    v.load_rbd(load_rbd(MODEL))
    v.resize(1200, 800)
    v.show()
    QApplication.processEvents()
    return v


@pytest.mark.parametrize("kind", ["node", "event", "hier", "annotation",
                                  "arrow", "grip"])
def test_alt_nudge_handles_every_canvas_target(kind, qapp):
    v = _phasing_view()
    try:
        if kind == "node":
            item = v._node_items[14]
        elif kind == "event":
            item = v._event_items[32]
        elif kind == "hier":
            item = v._hier_items[23]
        elif kind == "annotation":
            item = v._begin_annotation("text", QPointF(50, 50))
            item.end_edit()
        elif kind == "arrow":
            item = v._begin_annotation_arrow(
                QPointF(40, 40), QPointF(140, 40))
        else:
            link = v._links[0]
            mid = link.nearest_point(QPointF(
                (link._p0.x() + link._p3.x()) / 2,
                (link._p0.y() + link._p3.y()) / 2))
            mx, my = v.scene_to_model(mid)
            item = v.insert_routing_node(link._link_num, mx, my)
            assert item is not None
        v._scene.clearSelection()
        item.setSelected(True)
        v.setFocus()
        QApplication.processEvents()
        step = v._snap_state["x"]
        x0 = item.scenePos().x()
        before = (v.annotation_entry(item._anno_id)
                  if kind in ("annotation", "arrow") else None)
        v._stack.clear()
        QTest.keyClick(v, Qt.Key.Key_Right, Qt.KeyboardModifier.AltModifier)
        QApplication.processEvents()
        if kind != "arrow":
            assert item.scenePos().x() == pytest.approx(x0 + step / 10)
        assert v._stack.count() == 1
        if before is not None:
            after = v.annotation_entry(item._anno_id)
            if kind == "arrow":
                assert after["x1"] == pytest.approx(
                    before["x1"] + step / 10, abs=0.01)
            else:
                assert after["x"] == pytest.approx(
                    before["x"] + step / 10, abs=0.01)
    finally:
        v.close()
        v.deleteLater()
        QApplication.processEvents()


@pytest.fixture
def win(qapp):
    from raptor.gui.ui import settings
    from raptor.gui.ui.main_window import MainWindow
    settings.set_value("startup/show_start_page", False)
    w = MainWindow()
    w.resize(1500, 950)
    w.show()
    w._load_into_app(load_rbd(MODEL), MODEL)
    for _ in range(6):
        QApplication.processEvents()
    yield w
    w._dirty = False
    w.close()


def _block_id(view, index=0):
    return sorted(view._block_items)[index]


def test_alt_up_fine_nudges_a_focused_selection_instead_of_going_up(win):
    """The Alt+Up chord nudges a focused canvas selection."""
    v = win._diagram_dock.view
    bid = _block_id(v)
    item = v._block_items[bid]
    v._scene.clearSelection()
    item.setSelected(True)
    v.setFocus()
    QApplication.processEvents()
    step = v._snap_state["y"]
    x0, y0 = item.scenePos().x(), item.scenePos().y()
    v._stack.clear()

    win._fine_nudge_up_action.trigger()
    QApplication.processEvents()

    assert v._current_parent == 0, "Alt+Up with a selection moved, not ascended"
    assert item.scenePos().x() == pytest.approx(x0)
    assert item.scenePos().y() == pytest.approx(y0 - step / 10)
    assert v._stack.count() == 1


def test_ribbon_up_still_ascends_with_a_selection(win):
    """The ribbon's Up button is navigation, never a fine nudge."""
    v = win._diagram_dock.view
    v.enter_level(23)
    QApplication.processEvents()
    v._scene.clearSelection()
    bid = _block_id(v)
    v._block_items[bid].setSelected(True)
    v.setFocus()
    QApplication.processEvents()
    assert win._hier_up_action.isEnabled()

    win._hier_up_action.trigger()
    QApplication.processEvents()

    assert v._current_parent == 0


def test_alt_up_still_ascends_when_nothing_is_selected(win):
    """Fallback: with no nudgeable selection Alt+Up remains hierarchy-up."""
    v = win._diagram_dock.view
    v.enter_level(23)
    QApplication.processEvents()
    assert v._current_parent == 23
    v._scene.clearSelection()
    v.setFocus()
    QApplication.processEvents()

    win._fine_nudge_up_action.trigger()
    QApplication.processEvents()

    assert v._current_parent == 0


def test_alt_up_with_only_an_arrow_annotation_nudges_it(win):
    """Arrows are not ItemIsMovable (their ends move independently), but an
    arrow-only selection must still be nudgeable, not silently ascend."""
    v = win._diagram_dock.view
    v.enter_level(23)
    QApplication.processEvents()
    note = v._begin_annotation_arrow(QPointF(50, 50), QPointF(150, 50))
    v._scene.clearSelection()
    note.setSelected(True)
    v.setFocus()
    QApplication.processEvents()
    step = v._snap_state["y"]
    y0 = note.line().y1()
    v._stack.clear()

    win._fine_nudge_up_action.trigger()
    QApplication.processEvents()

    assert v._current_parent == 23, "an arrow-only selection moved, not ascended"
    assert note.line().y1() == pytest.approx(y0 - step / 10)


def test_real_alt_up_chord_in_a_hierarchy_nudges_then_falls_back(win):
    """The key chord itself (not just the QAction) must nudge with a
    selection and still ascend when the selection is empty."""
    v = win._diagram_dock.view
    v.enter_level(23)
    QApplication.processEvents()
    item = v._block_items[_block_id(v)]
    v._scene.clearSelection()
    item.setSelected(True)
    v.setFocus()
    QApplication.processEvents()
    y0 = item.scenePos().y()
    step = v._snap_state["y"]
    v._stack.clear()

    QTest.keyClick(v, Qt.Key.Key_Up, Qt.KeyboardModifier.AltModifier)
    QApplication.processEvents()
    assert v._current_parent == 23
    assert item.scenePos().y() == pytest.approx(y0 - step / 10)

    v._scene.clearSelection()
    QTest.keyClick(v, Qt.Key.Key_Up, Qt.KeyboardModifier.AltModifier)
    QApplication.processEvents()
    assert v._current_parent == 0


def test_alt_arrow_chords_never_arm_key_tips(win):
    v = win._diagram_dock.view
    v._scene.clearSelection()
    v.setFocus()
    QApplication.processEvents()
    for key in (Qt.Key.Key_Left, Qt.Key.Key_Right, Qt.Key.Key_Down,
                Qt.Key.Key_Up):
        win._alt_alone = True
        QTest.keyClick(v, key, Qt.KeyboardModifier.AltModifier)
        QApplication.processEvents()
        assert not win._alt_alone, key
        assert not win._key_tips_active, key
