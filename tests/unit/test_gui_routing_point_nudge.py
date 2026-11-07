"""Routing points: arrow-key nudge, undo, and the link ends facing them."""
from __future__ import annotations

import pytest
from PySide6.QtCore import QPoint, QPointF, Qt
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication

from raptor.core.fileio.rbd import load_rbd

pytestmark = pytest.mark.gui

MODEL = "tests/golden/Phasing3/input.rbd"


@pytest.fixture
def win():
    from raptor.gui.ui import settings
    from raptor.gui.ui.main_window import MainWindow
    QApplication.instance() or QApplication([])
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


def _link(view, num):
    return next(i for i in view._links if i._link_num == num)


def _add_grip(view, link_num, dx=0.0, dy=0.0, active=True):
    link = _link(view, link_num)
    mid = link.nearest_point(QPointF((link._p0.x() + link._p3.x()) / 2,
                                     (link._p0.y() + link._p3.y()) / 2))
    mx, my = view.scene_to_model(QPointF(mid.x() + dx, mid.y() + dy))
    grip = view.insert_routing_node(link_num, mx, my)
    assert grip is not None
    if active:
        saved = (view._snap_state["x"], view._snap_state["y"])
        view._snap_state["x"] = view._snap_state["y"] = 0.0
        grip.setPos(grip.pos() + QPointF(0.01, 0))      # drawn, not inactive
        view._snap_state["x"], view._snap_state["y"] = saved
    return grip


def _entry(view, grip):
    return next(e for e in view._rbd_ref.link_grips
                if int(e["id"]) == grip._entry_id)


def _key(view, key, mods=Qt.NoModifier):
    view.setFocus()
    QTest.keyClick(view, key, mods)
    QApplication.processEvents()


def test_arrow_nudge_moves_a_selected_routing_point_one_step_and_undoes(win):
    view = win._diagram_dock.view
    grip = _add_grip(view, 19)
    view._scene.clearSelection()
    grip.setSelected(True)
    x0, y0 = grip.pos().x(), grip.pos().y()
    _key(view, Qt.Key_Right)
    assert grip.pos().x() > x0 + 1 and grip.pos().y() == pytest.approx(y0)
    assert float(_entry(view, grip)["x"]) == pytest.approx(grip.pos().x(), abs=0.01)
    view._stack.undo()
    assert grip.pos().x() == pytest.approx(x0)
    assert float(_entry(view, grip)["x"]) == pytest.approx(x0, abs=0.01)
    view._stack.redo()
    assert grip.pos().x() > x0 + 1


def test_shift_arrow_nudges_a_routing_point_by_one_pixel(win):
    view = win._diagram_dock.view
    grip = _add_grip(view, 19)
    view._scene.clearSelection()
    grip.setSelected(True)
    x0, y0 = grip.pos().x(), grip.pos().y()
    _key(view, Qt.Key_Down, Qt.ShiftModifier)
    assert grip.pos().x() == pytest.approx(x0)
    assert grip.pos().y() == pytest.approx(y0 + 1.0)


def test_nudged_elements_take_the_routing_points_between_them_along(win):
    view = win._diagram_dock.view
    link = _link(view, 19)
    grip = _add_grip(view, 19, dy=-60)
    lone = _add_grip(view, 19, dy=60, active=False)
    ends = [link._from, link._to]
    view._scene.clearSelection()
    for it in ends:
        it.setSelected(True)
    p0 = {id(it): (it.pos().x(), it.pos().y()) for it in ends}
    g0 = (grip.pos().x(), grip.pos().y())
    l0 = (lone.pos().x(), lone.pos().y())
    _key(view, Qt.Key_Left)
    shift = (ends[0].pos().x() - p0[id(ends[0])][0],
             ends[0].pos().y() - p0[id(ends[0])][1])
    assert shift[0] < -1
    assert (grip.pos().x() - g0[0], grip.pos().y() - g0[1]) == pytest.approx(shift)
    assert (lone.pos().x(), lone.pos().y()) == l0            # not drawn: stays
    view._stack.undo()                                      # one step for all
    assert (grip.pos().x(), grip.pos().y()) == pytest.approx(g0)
    assert (ends[0].pos().x(), ends[0].pos().y()) == pytest.approx(p0[id(ends[0])])


def test_a_dragged_routing_point_is_one_undo_step(win):
    view = win._diagram_dock.view
    grip = _add_grip(view, 19, active=False)
    x0, y0 = grip.pos().x(), grip.pos().y()
    start = view.mapFromScene(grip.scenePos())
    QTest.mousePress(view.viewport(), Qt.LeftButton, Qt.NoModifier, start)
    end = start
    for k in range(1, 11):
        end = QPoint(start.x() + k * 6, start.y() - k * 4)
        QTest.mouseMove(view.viewport(), end)
        QApplication.processEvents()
    QTest.mouseRelease(view.viewport(), Qt.LeftButton, Qt.NoModifier, end)
    QApplication.processEvents()
    assert abs(grip.pos().x() - x0) > 5
    view._stack.undo()
    assert (grip.pos().x(), grip.pos().y()) == pytest.approx((x0, y0))
    view._stack.redo()
    assert abs(grip.pos().x() - x0) > 5


@pytest.mark.parametrize("flow", ["LR", "TB"])
@pytest.mark.parametrize("where,expect_exit,expect_entry", [
    ((0, -3000), "top", "top"),          # far above both ends (steeper than the bias)
    ((0, 3000), "bottom", "bottom"),     # far below both ends
])
def test_link_ends_leave_and_enter_by_the_side_facing_the_routing_point(
        win, flow, where, expect_exit, expect_entry):
    from raptor.gui.ui.diagram_items import set_flow_direction
    view = win._diagram_dock.view
    set_flow_direction(flow)
    link = _link(view, 19)
    grip = _add_grip(view, 19)
    mid = QPointF((link._p0.x() + link._p3.x()) / 2,
                  (link._p0.y() + link._p3.y()) / 2)
    saved = (view._snap_state["x"], view._snap_state["y"])
    view._snap_state["x"] = view._snap_state["y"] = 0.0
    grip.setPos(mid + QPointF(*where))
    view._snap_state["x"], view._snap_state["y"] = saved
    assert link._grip_sides == (expect_exit, expect_entry)
    # the end points sit on the box edge of that side, same geometry as others
    assert link._p0 == link._port_point(link._from, expect_exit)
    assert link._p3 == link._port_point(link._to, expect_entry)


def test_sides_do_not_flicker_at_a_boundary():
    from raptor.gui.ui.diagram_items import _LinkItem, set_flow_direction

    class Item:                                  # a stand-in with a centre
        pass

    set_flow_direction("LR")
    orig = _LinkItem._true_centre
    _LinkItem._true_centre = staticmethod(lambda item: QPointF(0, 0))
    try:
        item = Item()
        # horizontal when |dx| >= 0.5 |dy|: the boundary is dy = 2 dx
        right = QPointF(100, 199)               # just on the horizontal side
        below = QPointF(100, 203)               # just on the vertical side
        assert _LinkItem._side(item, right) == "right"
        assert _LinkItem._side(item, below) == "bottom"
        assert _LinkItem._sticky_side(item, below, "right") == "right"
        assert _LinkItem._sticky_side(item, right, "bottom") == "bottom"
        assert _LinkItem._sticky_side(item, QPointF(100, 400), "right") == "bottom"
        assert _LinkItem._sticky_side(item, below, None) == "bottom"
    finally:
        _LinkItem._true_centre = orig
