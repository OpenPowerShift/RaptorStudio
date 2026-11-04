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


# -- overlapping elements -----------------------------------------------------

def _drag(qapp, v, p0, p1, steps=8):
    vp = v.viewport()
    QTest.mousePress(vp, Qt.MouseButton.LeftButton,
                     Qt.KeyboardModifier.NoModifier, p0)
    for s in range(1, steps + 1):
        QTest.mouseMove(vp, p0 + (p1 - p0) * s / steps)
    QTest.mouseRelease(vp, Qt.MouseButton.LeftButton,
                       Qt.KeyboardModifier.NoModifier, p1)
    qapp.processEvents()


def _spots(v):
    return {oid: (round(it.scenePos().x(), 1), round(it.scenePos().y(), 1))
            for oid, it in v._block_items.items()}


def test_grabbing_a_name_moves_only_that_element(qapp):
    """Pressing a block's NAME (a child text item, not movable itself) was
    taken for empty canvas and armed the rubber band under the item drag:
    an element the dragged one crossed was selected and dragged along, and
    that move was left out of the undo step."""
    v = _view()
    v.auto_layout(direction="LR")
    v._stack.clear()
    a, b = (v._block_items[i] for i in sorted(v._block_items)[:2])
    before = _spots(v)
    # Drag B by its name onto A.
    _drag(qapp, v, v.mapFromScene(b._label.sceneBoundingRect().center()),
          v.mapFromScene(a.sceneBoundingRect().center()))
    after = _spots(v)
    assert [oid for oid in before if before[oid] != after[oid]] == \
        [b._block_id]
    assert not a.isSelected()
    v._stack.undo()
    assert _spots(v) == before


def test_every_element_a_gesture_moves_is_in_its_undo(qapp):
    """Whatever else moved during a drag is recorded with it, so one undo
    step holds the whole gesture."""
    v = _view()
    v.auto_layout(direction="LR")
    v._stack.clear()
    ids = sorted(v._block_items)
    a, c = v._block_items[ids[0]], v._block_items[ids[2]]
    p = v.mapFromScene(a.sceneBoundingRect().center())
    vp = v.viewport()
    QTest.mousePress(vp, Qt.MouseButton.LeftButton,
                     Qt.KeyboardModifier.NoModifier, p)
    for s in range(1, 6):
        QTest.mouseMove(vp, p + type(p)(0, 20 * s))
    c.setPos(c.pos() + QPointF(150, 150))    # moved by something else
    QTest.mouseRelease(vp, Qt.MouseButton.LeftButton,
                       Qt.KeyboardModifier.NoModifier, p + type(p)(0, 100))
    qapp.processEvents()
    assert v._stack.count() == 1
    recorded = v._stack.command(0).keys
    assert {("block", ids[0]), ("block", ids[2])} <= recorded
