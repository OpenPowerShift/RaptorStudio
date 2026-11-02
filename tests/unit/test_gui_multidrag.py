"""A multi-selection moves as one item (drag and arrow nudge)."""
from __future__ import annotations

import pytest
from PySide6.QtCore import QPointF, Qt
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication

from raptor.api.build import build_from_spec
from raptor.gui.ui.diagram import DiagramView
from raptor.gui.ui.diagram_items import set_flow_direction


@pytest.fixture(scope="module")
def qapp():
    return QApplication.instance() or QApplication([])


def _view():
    rbd = build_from_spec({
        "name": "Multi",
        "blocks": [{"name": "A"}, {"name": "B"}, {"name": "C"}],
        "nodes": [{"id": 1, "name": "Start", "type": "start"},
                  {"id": 2, "name": "End", "type": "end"}],
        "links": [{"from": "Start", "to": "A"}, {"from": "A", "to": "B"},
                  {"from": "B", "to": "C"}, {"from": "C", "to": "End"}],
        "system": {"term_condition": 100, "num_runs": 1},
    }, auto_layout=False)
    view = DiagramView()
    view.load_rbd(rbd)
    view.resize(700, 400)
    view.show()
    return view


def test_group_drag_carries_every_selected_item(qapp):
    v = _view()
    v.auto_layout(direction="LR")
    items = list(v._block_items.values())
    for item in items:
        item.setSelected(True)
    starts = [(it.scenePos().x(), it.scenePos().y()) for it in items]
    v._drag_start = [("block", iid, p)
                     for (iid, it), p in zip(v._block_items.items(),
                                             starts)
                     if it.isSelected()]
    primary = items[0]
    v._drag_primary = primary
    v._drag_primary_start = QPointF(starts[0][0], starts[0][1])
    v._drag_last_delta = (0.0, 0.0)
    v._drag_armed = True
    dx = v._snap_state["x"]
    dy = v._snap_state["y"]
    primary.setPos(primary.pos() + QPointF(dx, dy))
    v._move_group_with_primary()
    for item, (x, y) in zip(items, starts):
        assert item.scenePos().x() == pytest.approx(x + dx)
        assert item.scenePos().y() == pytest.approx(y + dy)


def test_arrow_nudge_moves_every_selected_item(qapp):
    v = _view()
    items = list(v._block_items.values())
    for item in items:
        item.setSelected(True)
    before = [(it.scenePos().x(), it.scenePos().y()) for it in items]
    v.setFocus()
    QTest.keyClick(v, Qt.Key.Key_Right)
    qapp.processEvents()
    after = [(it.scenePos().x(), it.scenePos().y()) for it in items]
    assert all(a[0] != b[0] for a, b in zip(after, before))
    assert all(a[1] == b[1] for a, b in zip(after, before))
    set_flow_direction("auto")
