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
    assert dx != 0, "Alt+drag must actually move"
    assert abs(dx) < step, "the drag must be finer than the normal snap"
    assert dx / fine == pytest.approx(round(dx / fine)), (
        "the item must land on a tenth-step boundary")
    assert v._stack.count() == 1
    v._stack.undo()
    assert (item.scenePos().x(), item.scenePos().y()) == pytest.approx(
        (x0, y0))


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
    """The action's Alt+Up shortcut is shared with hierarchy-up; a movable
    selection on the focused canvas wins (and the pane does not navigate)."""
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
    assert win._hier_up_action.isEnabled(), \
        "a selection enables Alt+Up at the home level"

    # Trigger the command the shortcut is bound to: QTest does not run
    # Alt-modified QAction shortcuts through the window's shortcut map,
    # but the real ribbon shortcut and this trigger take the same path.
    win._hier_up_action.trigger()
    QApplication.processEvents()

    assert v._current_parent == 0, "Alt+Up with a selection moved, not ascended"
    assert item.scenePos().x() == pytest.approx(x0)
    assert item.scenePos().y() == pytest.approx(y0 - step / 10)
    assert v._stack.count() == 1


def test_alt_up_still_ascends_when_nothing_is_selected(win):
    """Fallback: with no movable selection Alt+Up remains hierarchy-up."""
    v = win._diagram_dock.view
    v.enter_level(23)
    QApplication.processEvents()
    assert v._current_parent == 23
    v._scene.clearSelection()
    v.setFocus()
    QApplication.processEvents()
    assert win._hier_up_action.isEnabled()

    win._hier_up_action.trigger()
    QApplication.processEvents()

    assert v._current_parent == 0
