"""Dragging a link's routing point moves the point and nothing else."""
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


def _positions(view):
    out = {}
    for kind, items in (("b", view._block_items), ("n", view._node_items),
                        ("e", view._event_items), ("h", view._hier_items)):
        for oid, it in items.items():
            out[(kind, oid)] = (it.scenePos().x(), it.scenePos().y())
    return out


@pytest.mark.parametrize("link_num", [16, 19, 23, 24, 26])
def test_dragging_a_routing_point_moves_no_element(win, link_num):
    view = win._diagram_dock.view
    link = next(i for i in view._links if i._link_num == link_num)
    mid = link.nearest_point(QPointF((link._p0.x() + link._p3.x()) / 2,
                                     (link._p0.y() + link._p3.y()) / 2))
    mx, my = view.scene_to_model(mid)
    grip = view.insert_routing_node(link_num, mx, my)
    assert grip is not None
    before = _positions(view)
    start = view.mapFromScene(grip.scenePos())
    QTest.mousePress(view.viewport(), Qt.LeftButton, Qt.NoModifier, start)
    end = start
    for k in range(1, 41):          # a long, sweeping drag over the diagram
        end = QPoint(start.x() + k * 8, start.y() - k * 5)
        QTest.mouseMove(view.viewport(), end)
        QApplication.processEvents()
        assert _positions(view) == before, f"an element moved at step {k}"
        selected = {type(i).__name__ for i in view._scene.selectedItems()}
        assert selected <= {"_RouteGripItem"}, selected
    QTest.mouseRelease(view.viewport(), Qt.LeftButton, Qt.NoModifier, end)
    QApplication.processEvents()
    assert _positions(view) == before
