"""The armed tool's cursor must survive an element coming under the
pointer (hover), which is what happens while wheel-zooming."""
from __future__ import annotations

import os

import pytest

pytest.importorskip("PySide6")

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QApplication, QGraphicsSceneHoverEvent

from raptor.api.build import build_from_spec
from raptor.gui.ui.diagram import DiagramView


@pytest.fixture(scope="module")
def qapp():
    return QApplication.instance() or QApplication([])


def _view() -> DiagramView:
    spec = {
        "name": "Cursor",
        "blocks": [{"name": "A"}],
        "nodes": [{"id": 1, "name": "Start", "type": "start"},
                  {"id": 2, "name": "End", "type": "end"}],
        "links": [{"from": "Start", "to": "A"}, {"from": "A", "to": "End"}],
        "system": {"term_condition": 100, "num_runs": 1},
    }
    view = DiagramView()
    view.load_rbd(build_from_spec(spec, auto_layout=True))
    view.resize(800, 600)
    view.show()
    return view


def _hover(item) -> None:
    item.hoverEnterEvent(QGraphicsSceneHoverEvent(
        QGraphicsSceneHoverEvent.Type.GraphicsSceneHoverEnter))


@pytest.mark.parametrize("mode", ["add_block", "add_node", "add_event",
                                  "add_hierarchy", "connect"])
def test_hovering_an_element_keeps_the_tool_cursor(qapp, mode):
    view = _view()
    view.tool_mode = mode
    tool = view.viewport().cursor()
    assert tool.shape() == Qt.CursorShape.BitmapCursor
    for item in (next(iter(view._block_items.values())),
                 next(iter(view._node_items.values()))):
        _hover(item)
        assert item.cursor().shape() == Qt.CursorShape.BitmapCursor
        assert item.cursor().pixmap().cacheKey() == \
            tool.pixmap().cacheKey()


def test_select_mode_hover_still_shows_the_hand(qapp):
    view = _view()
    view.tool_mode = "add_block"
    view.tool_mode = "select"
    block = next(iter(view._block_items.values()))
    _hover(block)
    assert block.cursor().shape() == Qt.CursorShape.PointingHandCursor


def test_a_middle_button_pan_restores_the_tool_cursor(qapp):
    """Releasing a middle-button pan used to set a plain arrow."""
    from PySide6.QtCore import QEvent, QPointF
    from PySide6.QtGui import QMouseEvent
    view = _view()
    view.tool_mode = "add_block"
    point = QPointF(20, 20)
    view.mousePressEvent(QMouseEvent(
        QEvent.Type.MouseButtonPress, point, point,
        Qt.MouseButton.MiddleButton, Qt.MouseButton.MiddleButton,
        Qt.KeyboardModifier.NoModifier))
    assert view.viewport().cursor().shape() == \
        Qt.CursorShape.ClosedHandCursor
    view.mouseReleaseEvent(QMouseEvent(
        QEvent.Type.MouseButtonRelease, point, point,
        Qt.MouseButton.MiddleButton, Qt.MouseButton.NoButton,
        Qt.KeyboardModifier.NoModifier))
    # The tool's painted cursor (a bitmap), not the arrow.
    assert view.viewport().cursor().shape() == Qt.CursorShape.BitmapCursor
