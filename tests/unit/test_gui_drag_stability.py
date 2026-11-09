"""Dragging a block across other elements with orthogonal links.

The block must follow the mouse (within one snap step) the whole way, nothing
else may move, and the view must not shift, including when the debounced
checks (stacked-element warning, move settle) fire in the middle of the drag."""
from __future__ import annotations

import itertools

import pytest
from PySide6.QtCore import QPoint, Qt
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication

from raptor.core.fileio.rbds import load_rbds

pytestmark = pytest.mark.gui

MODEL = "new_docs/models/DC2.rbds"


@pytest.fixture
def win():
    from raptor.gui.ui import settings
    from raptor.gui.ui.diagram_items import link_routing, set_link_routing
    from raptor.gui.ui.main_window import MainWindow
    QApplication.instance() or QApplication([])
    settings.set_value("startup/show_start_page", False)
    before = link_routing()
    w = MainWindow()
    w.resize(1400, 900)
    w.show()
    w._load_into_app(load_rbds(MODEL), MODEL)
    view = w._diagram_dock.view
    view.enter_level(20)
    set_link_routing("orthogonal")
    view.reroute_links()
    view.fitInView(view.scene().itemsBoundingRect())
    QApplication.processEvents()
    # Canvas/dock sizing is deferred after a load. If it lands during the
    # drag, the view transform changes under the mouse and the block
    # appears to jump. Let those timers settle first.
    for _ in range(20):
        QApplication.processEvents()
        timer = getattr(w, "_canvas_room_timer", None)
        if timer is None or not timer.isActive():
            break
        QTest.qWait(50)
    QTest.qWait(250)
    QApplication.processEvents()
    yield w
    set_link_routing(before)
    w._dirty = False
    w.close()


def _positions(view, skip):
    return {(k, i): (it.pos().x(), it.pos().y())
            for k, d in (("b", view._block_items), ("n", view._node_items),
                         ("e", view._event_items))
            for i, it in d.items() if (k, i) != skip}


@pytest.mark.parametrize("snap", [True, False])
def test_a_dragged_block_tracks_the_mouse_across_neighbours(win, snap):
    view = win._diagram_dock.view
    view.set_snap_enabled(snap)
    ids = sorted(view._block_items)
    a, targets = ids[0], ids[1:4]
    ia = view._block_items[a]
    step = max(view._snap_state["x"], view._snap_state["y"])
    limit = 2 * step + 4
    others = _positions(view, ("b", a))
    origin = view.mapToScene(QPoint(0, 0))

    path = [view.mapFromScene(ia.sceneBoundingRect().center())]
    path += [view.mapFromScene(view._block_items[t].sceneBoundingRect().center())
             for t in targets]
    QTest.mousePress(view.viewport(), Qt.MouseButton.LeftButton,
                     Qt.KeyboardModifier.NoModifier, path[0])
    last_mouse, last_pos = path[0], ia.pos()
    waited = 0
    for p, q in itertools.pairwise(path):
        n = max(abs(q.x() - p.x()), abs(q.y() - p.y())) // 4 + 1
        for s in range(1, int(n) + 1):
            m = p + (q - p) * s / n
            QTest.mouseMove(view.viewport(), QPoint(int(m.x()), int(m.y())))
            QApplication.processEvents()
            if s % 25 == 0 and waited < 6:
                QTest.qWait(450)        # the debounce timers fire mid-drag
                QApplication.processEvents()
                # Qt may deliver one queued move during qWait. Rebase the
                # reference to where the item and mouse actually are, or
                # the next delta reports that queued move as a jump.
                last_mouse, last_pos = m, ia.pos()
                waited += 1
            dm = view.mapToScene(m) - view.mapToScene(last_mouse)
            dp = ia.pos() - last_pos
            assert max(abs(dp.x() - dm.x()), abs(dp.y() - dm.y())) <= limit
            shift = view.mapToScene(QPoint(0, 0)) - origin
            assert max(abs(shift.x()), abs(shift.y())) < 1.0   # the view stays put
            last_mouse, last_pos = m, ia.pos()
    QTest.mouseRelease(view.viewport(), Qt.MouseButton.LeftButton,
                       Qt.KeyboardModifier.NoModifier, path[-1])
    view.flush_pending_moves()
    QApplication.processEvents()
    assert _positions(view, ("b", a)) == others             # nothing else moved


def test_the_stacked_warning_waits_for_the_drop(win):
    view = win._diagram_dock.view
    ids = sorted(view._block_items)
    a, b = ids[0], ids[2]
    ia, ib = view._block_items[a], view._block_items[b]
    src = view.mapFromScene(ia.sceneBoundingRect().center())
    dst = view.mapFromScene(ib.sceneBoundingRect().center())
    QTest.mousePress(view.viewport(), Qt.MouseButton.LeftButton,
                     Qt.KeyboardModifier.NoModifier, src)
    for s in range(1, 21):
        m = src + (dst - src) * s / 20
        QTest.mouseMove(view.viewport(), QPoint(int(m.x()), int(m.y())))
        QApplication.processEvents()
    QTest.qWait(900)                       # well past the 400 ms debounce
    assert "warning" not in win._validation_badge.text()    # still dragging
    QTest.mouseRelease(view.viewport(), Qt.MouseButton.LeftButton,
                       Qt.KeyboardModifier.NoModifier, dst)
    QTest.qWait(900)
    assert "1 warning" in win._validation_badge.text()      # reported after the drop
