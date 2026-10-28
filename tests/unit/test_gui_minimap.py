"""Navigation minimap (`_MinimapView` in `raptor/gui/ui/diagram.py`): a
DiagramDock overview widget sharing the main scene. It auto-hides when the
current content already fits the viewport; once it does not, it shows and
lets click/drag pan the main view.
"""
from __future__ import annotations

import itertools
import os

import pytest

pytest.importorskip("PySide6")

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QPoint
from PySide6.QtWidgets import QApplication

from raptor.core.fileio.rbd import load_rbd
from raptor.gui.ui.diagram import DiagramDock

REPO = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
GOLD = os.path.join(REPO, "tests", "golden")


@pytest.fixture(scope="module")
def qapp():
    return QApplication.instance() or QApplication([])


@pytest.fixture
def dock(qapp):
    d = DiagramDock()
    d.resize(800, 600)
    d.show()
    d.view.load_rbd(load_rbd(os.path.join(GOLD, "HierarchyExample",
                                          "input.rbd")))
    QApplication.instance().processEvents()
    return d


def test_minimap_exists_and_shares_the_main_scene(dock):
    mm = dock._minimap
    assert mm is not None
    assert mm.scene() is dock.view._scene


def test_minimap_hidden_when_content_already_fits(dock):
    dock._minimap._refresh()
    assert not dock._minimap.isVisible()


def test_minimap_shown_once_content_overflows_the_viewport(dock):
    v = dock.view
    v.scale(20, 20)
    v._user_zoomed = True
    QApplication.instance().processEvents()
    dock._minimap._refresh()
    assert dock._minimap.isVisible()


def test_minimap_click_pans_the_main_view(dock):
    v = dock.view
    v.scale(20, 20)
    v._user_zoomed = True
    QApplication.instance().processEvents()
    dock._minimap._refresh()
    assert dock._minimap.isVisible()

    before = v.mapToScene(v.viewport().rect().center())
    dock._minimap._pan_to(QPoint(5, 5))
    QApplication.instance().processEvents()
    after = v.mapToScene(v.viewport().rect().center())
    assert before != after


def test_minimap_has_a_visible_border(dock):
    """The minimap has a visible frame border and a border style rule, rather
    than a transparent-background stylesheet that suppressed it."""
    from PySide6.QtWidgets import QFrame
    mm = dock._minimap
    assert mm.frameShape() == QFrame.Box
    assert mm.lineWidth() > 0
    assert "border" in mm.styleSheet()


def test_minimap_does_not_refit_mid_drag(dock):
    """While `_dragging` is True, `_refresh()` must not touch the transform,
    which would drift the pan mid-drag."""
    v = dock.view
    v.scale(20, 20)
    v._user_zoomed = True
    QApplication.instance().processEvents()
    mm = dock._minimap
    mm._refresh()
    assert mm.isVisible()

    mm._dragging = True
    transform_before = mm.transform()
    mm._refresh()
    assert mm.transform() == transform_before


def test_minimap_press_and_move_updates_viewport_rect_immediately(dock):
    """The viewport-rect overlay must move on every mouse event during
    a drag, not wait for the next timer tick."""
    v = dock.view
    v.scale(20, 20)
    v._user_zoomed = True
    QApplication.instance().processEvents()
    mm = dock._minimap
    mm._refresh()
    assert mm.isVisible()

    mm.mousePressEvent(_FakeMouseEvent(QPoint(10, 10)))
    assert mm._dragging
    rect_after_press = v.mapToScene(v.viewport().rect()).boundingRect()
    mm.mouseMoveEvent(_FakeMouseEvent(QPoint(150, 100)))
    rect_after_move = v.mapToScene(v.viewport().rect()).boundingRect()
    assert rect_after_press != rect_after_move   # main view actually panned

    mm.mouseReleaseEvent(_FakeMouseEvent(QPoint(150, 100)))
    assert not mm._dragging


class _FakeMouseEvent:
    """Minimal stand-in for QMouseEvent -- `_MinimapView`'s handlers
    only ever read `.pos()` (PySide6 < 6.6-style API, still supported
    alongside `.position()`)."""

    def __init__(self, pos: QPoint):
        self._pos = pos

    def pos(self):
        return self._pos


def test_minimap_repositions_to_viewport_bottom_right(dock):
    mm = dock._minimap
    mm._reposition()
    g = dock.view.viewport().geometry()      # in the view's own space
    assert mm.x() == g.left() + g.width() - mm.SIZE.width() - mm._MARGIN
    assert mm.y() == g.top() + g.height() - mm.SIZE.height() - mm._MARGIN


def test_minimap_refresh_is_a_no_op_when_main_view_not_visible(qapp):
    """Headless tests routinely build a DiagramDock without ever
    calling .show() -- the poll must stay cheap/inert then, not throw
    or spin work on an invisible view."""
    d = DiagramDock()
    d.view.load_rbd(load_rbd(os.path.join(GOLD, "HierarchyExample",
                                          "input.rbd")))
    d._minimap._refresh()   # must not raise
    assert not d._minimap.isVisible()


# -- dragging, wheel, caching, shape ------------------------------------

def _zoomed(dock, factor=20):
    v = dock.view
    v.scale(factor, factor)
    v._user_zoomed = True
    QApplication.instance().processEvents()
    dock._minimap._refresh()
    assert dock._minimap.isVisible()
    return v, dock._minimap


def _centre(v):
    return v.mapToScene(v.viewport().rect().center())


def test_a_real_mouse_drag_pans_the_canvas_continuously(dock):
    """Delivered as genuine Qt mouse events to the minimap's viewport, not
    through the private handlers."""
    from PySide6.QtCore import Qt
    from PySide6.QtTest import QTest
    v, mm = _zoomed(dock)
    vp = mm.viewport()
    QTest.mousePress(vp, Qt.LeftButton, Qt.NoModifier, QPoint(20, 60))
    seen = []
    for x in range(20, 160, 10):
        QTest.mouseMove(vp, QPoint(x, 60))
        QApplication.instance().processEvents()
        seen.append(_centre(v).x())
    QTest.mouseRelease(vp, Qt.LeftButton, Qt.NoModifier, QPoint(160, 60))
    assert len(set(round(x, 3) for x in seen)) > 5, "the canvas must follow"
    assert seen == sorted(seen), "and move steadily one way"


def test_grabbing_the_frame_does_not_make_the_canvas_jump(dock):
    from PySide6.QtCore import QPointF
    v, mm = _zoomed(dock)
    frame = mm._viewport_rect_here()
    inside = (frame.center() + QPointF(frame.width() / 4, 0)).toPoint()
    before = _centre(v)
    mm.mousePressEvent(_FakeMouseEvent(inside))
    after_press = _centre(v)
    mm.mouseReleaseEvent(_FakeMouseEvent(inside))
    assert (after_press - before).manhattanLength() < 1.0, \
        "pressing on the frame must not re-centre the canvas on the cursor"


def test_pressing_outside_the_frame_jumps_there(dock):
    v, mm = _zoomed(dock)
    frame = mm._viewport_rect_here()
    outside = QPoint(int(frame.right()) + 15, int(frame.center().y()))
    before = _centre(v)
    mm.mousePressEvent(_FakeMouseEvent(outside))
    mm.mouseReleaseEvent(_FakeMouseEvent(outside))
    assert _centre(v) != before


def test_the_wheel_over_the_minimap_scrolls_the_canvas(dock):
    from PySide6.QtCore import QPointF, Qt
    from PySide6.QtGui import QWheelEvent
    v, mm = _zoomed(dock)
    bar = v.verticalScrollBar()
    bar.setValue((bar.minimum() + bar.maximum()) // 2)
    start = bar.value()
    ev = QWheelEvent(QPointF(50, 50), QPointF(50, 50), QPoint(0, 0),
                     QPoint(0, -120), Qt.NoButton, Qt.NoModifier,
                     Qt.NoScrollPhase, False)
    mm.wheelEvent(ev)
    assert bar.value() != start


def test_panning_does_not_redraw_the_overview_shapes(dock, monkeypatch):
    """The overview is cached: moving the viewport frame over it must not
    re-render every element (that was the cost of a drag)."""
    _v, mm = _zoomed(dock)
    mm.repaint()                                # build the cache
    calls = []
    real = type(mm)._paint_shapes
    monkeypatch.setattr(type(mm), "_paint_shapes",
                        lambda self, qp, scale: (calls.append(1),
                                                 real(self, qp, scale))[1])
    for x in range(10, 120, 10):
        mm._pan_to(QPoint(x, 50))
        mm.repaint()
    assert calls == [], "panning must reuse the cached overview"


def test_the_overview_is_redrawn_when_the_model_changes(dock, monkeypatch):
    v, mm = _zoomed(dock)
    mm.repaint()
    calls = []
    real = type(mm)._paint_shapes
    monkeypatch.setattr(type(mm), "_paint_shapes",
                        lambda self, qp, scale: (calls.append(1),
                                                 real(self, qp, scale))[1])
    item = next(iter(v._block_items.values()))
    item.setPos(item.pos().x() + 40, item.pos().y())
    QApplication.instance().processEvents()
    mm._refresh()                      # the poll notices the move
    assert mm._settle.isActive() or mm._overview_dirty
    mm._settle.stop()
    mm._on_model_settled()
    QApplication.instance().processEvents()
    mm.repaint()
    assert calls, "a moved element must reach the overview"


def test_a_drag_does_not_rebuild_the_overview_at_every_step(dock,
                                                            monkeypatch):
    v, mm = _zoomed(dock)
    mm.repaint()
    calls = []
    real = type(mm)._paint_shapes
    monkeypatch.setattr(type(mm), "_paint_shapes",
                        lambda self, qp, scale: (calls.append(1),
                                                 real(self, qp, scale))[1])
    item = next(iter(v._block_items.values()))
    for _ in range(15):
        item.setPos(item.pos().x() + 3, item.pos().y())
        QApplication.instance().processEvents()
        mm._refresh()                  # a poll tick, then a repaint
        mm.repaint()
    assert len(calls) <= 1, calls


def test_the_box_takes_the_diagram_s_shape(dock):
    from PySide6.QtCore import QRectF
    mm = dock._minimap
    mm._adapt_size(QRectF(0, 0, 4000, 300))          # a long thin chain
    wide = mm.size()
    mm._adapt_size(QRectF(0, 0, 400, 400))
    square = mm.size()
    mm._adapt_size(QRectF(0, 0, 200, 900))           # a tall one
    tall = mm.size()
    assert wide.width() > square.width() >= tall.width()
    assert wide.height() < square.height() <= tall.height()
    assert mm.size() == mm.SIZE                      # _reposition uses it


# -- stability: the minimap stays put while the canvas moves ---------------

def test_the_minimap_is_not_carried_along_when_the_canvas_scrolls(dock):
    """Scrolling shifts the viewport's pixels and its children. A minimap
    parented to the viewport slid with every pan and snapped back on the next
    poll; it is a child of the view itself, so it does not move at all."""
    v, mm = _zoomed(dock)
    assert mm.parent() is v, "must not be inside the scrolling viewport"
    before = mm.geometry()
    bar = v.horizontalScrollBar()
    for _ in range(10):
        bar.setValue(bar.value() + 40)
        v.viewport().scroll(-40, 0)          # what Qt does when it scrolls
        QApplication.instance().processEvents()
        assert mm.geometry() == before
    vbar = v.verticalScrollBar()
    vbar.setValue(vbar.value() + 120)
    QApplication.instance().processEvents()
    assert mm.geometry() == before


def test_the_picture_does_not_rescale_while_things_are_dragged(dock):
    v, mm = _zoomed(dock)
    mm._update_fit_target()
    mm._refresh()
    t0 = mm.transform()
    item = next(iter(v._block_items.values()))
    for i in range(30):
        item.setPos(item.pos().x() + 1.5, item.pos().y() + 0.5)
        QApplication.instance().processEvents()
        mm._refresh()
        assert mm.transform() == t0, f"rescaled at step {i}"
    mm._settle.stop()
    mm._on_model_settled()
    assert mm.transform() == t0              # still inside what it fits


def test_the_picture_refits_only_when_the_content_outgrows_it(dock):
    from PySide6.QtCore import QPointF
    v, mm = _zoomed(dock)
    mm._update_fit_target()
    mm._refresh()
    t0 = mm.transform()
    item = next(iter(v._block_items.values()))
    item.setPos(item.pos() + QPointF(20000, 0))     # far outside the fit
    QApplication.instance().processEvents()
    mm._settle.stop()
    mm._on_model_settled()
    assert mm.transform() != t0                     # now it must refit


def test_the_view_s_world_changing_does_not_shift_the_picture(dock):
    """The minimap has its own scene rect; the shared scene's (a huge,
    changing 'world') must not move it."""
    v, mm = _zoomed(dock)
    mm._refresh()
    t0, keys = mm.transform(), mm.mapToScene(QPoint(20, 20))
    world = v._scene.sceneRect()
    v._scene.setSceneRect(world.adjusted(-900, -300, 1500, 700))
    QApplication.instance().processEvents()
    mm._refresh()
    assert mm.transform() == t0
    assert mm.mapToScene(QPoint(20, 20)) == keys


def test_dragging_eases_the_canvas_instead_of_hopping(dock):
    """One minimap pixel is many canvas pixels, so following the cursor
    exactly moves the canvas in hops. The canvas eases toward the target."""
    from PySide6.QtCore import Qt
    from PySide6.QtTest import QTest
    v, mm = _zoomed(dock)
    bar = v.horizontalScrollBar()
    bar.setValue(bar.minimum())
    QApplication.instance().processEvents()
    vp = mm.viewport()
    QTest.mousePress(vp, Qt.LeftButton, Qt.NoModifier, QPoint(20, 60))
    QTest.mouseMove(vp, QPoint(60, 60))         # one big cursor move
    positions = [bar.value()]
    # Drive the 60 Hz easing tick directly instead of waiting 16 ms for it:
    # under a parallel test run the event loop is starved, several frames
    # collapse into one, and the first step then looks like the single hop
    # this test exists to rule out. The easing LAW is what is under test,
    # and calling `_glide_step` measures exactly that, deterministically.
    for _ in range(12):
        mm._glide_step()
        positions.append(bar.value())
    QTest.mouseRelease(vp, Qt.LeftButton, Qt.NoModifier, QPoint(60, 60))
    steps = [b - a for a, b in itertools.pairwise(positions)]
    travelled = positions[-1] - positions[0]
    assert travelled > 0
    assert sum(1 for s in steps if s > 0) >= 4, "it must arrive over several frames"
    assert max(steps) < travelled * 0.75, "not in a single hop"
    moving = [s for s in steps if s]
    assert moving[0] >= moving[-1], "eases out: the big steps come first"


def test_the_poll_notices_a_moved_element_but_not_an_idle_scene(dock):
    v, mm = _zoomed(dock)
    mm._refresh()
    sig = mm._signature
    mm._refresh()
    assert mm._signature == sig                       # idle: no change
    item = next(iter(v._block_items.values()))
    item.setPos(item.pos().x() + 25, item.pos().y())
    mm._refresh()
    assert mm._signature != sig
    assert mm._overview_dirty


def test_the_minimap_does_not_listen_to_the_scenes_changed_signal(dock):
    """A Python slot on `changed` runs on every repaint request and alters
    event ordering (it changed how the docks sized themselves in a UI
    sweep). Changes are found by the poll instead."""
    v = dock.view
    receivers = v._scene.receivers("2changed(QList<QRectF>)")
    # Only Qt's own view connections (one per QGraphicsView); none of ours.
    assert not hasattr(dock._minimap, "_on_scene_changed")
    assert receivers <= 2
