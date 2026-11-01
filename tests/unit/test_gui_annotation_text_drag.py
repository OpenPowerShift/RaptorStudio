"""Issue #64: a text annotation is a movable item, not a click-to-edit
field.

Dragging the body of a text note must move it (and keep an anchored note's
dx/dy), while editing stays an explicit action -- double-click, F2, or the
context menu's Edit Text… -- so the common case of grabbing a note by its
middle no longer puts the caret in it instead.

The drags here go through the real scene event handlers (not a bare
`setPos`) so the item's mouse press/move/release path is what is being
tested. All Qt tests are offscreen, as the rest of this suite is.
"""
from __future__ import annotations

import os
import pathlib

import pytest

from PySide6.QtCore import QEvent, QPoint, QPointF, Qt
from PySide6.QtGui import QKeyEvent, QMouseEvent
from PySide6.QtWidgets import QGraphicsSceneMouseEvent

from raptor.api.annotations import place_annotation
from raptor.core.fileio.rbd import load_rbd
from raptor.gui.ui.diagram import DiagramView

REPO = pathlib.Path(__file__).resolve().parents[2]
GOLD = os.path.join(REPO, "tests", "golden")

NOTE_TEXT = "Note body"


@pytest.fixture(scope="module")
def qapp():
    from PySide6.QtWidgets import QApplication
    return QApplication.instance() or QApplication([])


def _dispose(qapp, v):
    """Tear the canvas down in Qt's own order.

    A bare `DiagramView` is a top-level widget but not a QMainWindow, so
    the suite's `_reap()` does not own it. Leaving it for the cycle
    collector lets a later worker's `gc.collect()` destroy a scene and its
    undo commands at an arbitrary point -- the same late-teardown crash
    `_clear_orphan_undo_stacks()` exists to avoid.
    """
    v._stack.clear()
    v.close()
    v.deleteLater()
    qapp.sendPostedEvents(None, QEvent.Type.DeferredDelete)
    qapp.processEvents()


@pytest.fixture
def view(qapp):
    v = DiagramView()
    v.load_rbd(load_rbd(os.path.join(
        GOLD, "TwoBlockRedundancy", "input.rbd")))
    v.resize(1200, 800)
    v.show()
    qapp.processEvents()
    yield v
    _dispose(qapp, v)


def _text_item(v, x=200, y=200):
    """A placed, committed text note in Select mode, selected."""
    v.tool_mode = "anno_text"
    item = v._begin_annotation("text", v.mapToScene(QPoint(x, y)))
    assert item is not None
    item.setPlainText(NOTE_TEXT)
    item.end_edit()
    v.tool_mode = "select"
    v._scene.clearSelection()
    item.setSelected(True)
    return item


def _scene_drag(v, item, local_start: QPointF, dx: float, dy: float):
    """A press-move-release gesture on `item` through QGraphicsScene's own
    dispatcher, at `local_start` in the item's coordinates."""
    down = item.mapToScene(local_start)
    target = down + QPointF(dx, dy)

    def event(kind, scene_pos, buttons, button):
        e = QGraphicsSceneMouseEvent(kind)
        e.setScenePos(scene_pos)
        e.setPos(item.mapFromScene(scene_pos))
        e.setLastScenePos(scene_pos)
        e.setButton(button)
        e.setButtons(buttons)
        e.setButtonDownScenePos(Qt.MouseButton.LeftButton, down)
        e.setScreenPos(QPoint(int(scene_pos.x()), int(scene_pos.y())))
        e.setButtonDownScreenPos(
            Qt.MouseButton.LeftButton,
            QPoint(int(down.x()), int(down.y())))
        return e

    v._scene.mousePressEvent(
        event(QEvent.GraphicsSceneMousePress, down,
              Qt.MouseButton.LeftButton, Qt.MouseButton.LeftButton))
    v._scene.mouseMoveEvent(
        event(QEvent.GraphicsSceneMouseMove, target,
              Qt.MouseButton.LeftButton, Qt.MouseButton.NoButton))
    v._scene.mouseReleaseEvent(
        event(QEvent.GraphicsSceneMouseRelease, target,
              Qt.MouseButton.NoButton, Qt.MouseButton.LeftButton))


def _view_drag(v, item, local_start: QPointF, dx: float, dy: float):
    """The same gesture at the view level, so DiagramView's release handler
    commits it (the path anchors and undo depend on)."""
    down = QPointF(v.mapFromScene(item.mapToScene(local_start)))
    target = QPointF(v.mapFromScene(
        item.mapToScene(local_start) + QPointF(dx, dy)))
    v.mousePressEvent(QMouseEvent(
        QEvent.MouseButtonPress, down, down, Qt.MouseButton.LeftButton,
        Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier))
    v.mouseMoveEvent(QMouseEvent(
        QEvent.MouseMove, target, target, Qt.MouseButton.NoButton,
        Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier))
    v.mouseReleaseEvent(QMouseEvent(
        QEvent.MouseButtonRelease, target, target, Qt.MouseButton.LeftButton,
        Qt.MouseButton.NoButton, Qt.KeyboardModifier.NoModifier))


def test_dragging_the_body_moves_a_text_note(qapp, view):
    """The plain case: press in the middle, drag, and the note follows --
    its text is untouched and it does not enter edit mode."""
    from PySide6.QtWidgets import QGraphicsItem

    item = _text_item(view)
    start = item.scenePos()
    _scene_drag(view, item, item.rect().center(), 40, 25)
    qapp.processEvents()

    assert item.scenePos().x() == pytest.approx(start.x() + 40, abs=0.01)
    assert item.scenePos().y() == pytest.approx(start.y() + 25, abs=0.01)
    assert item.toPlainText() == NOTE_TEXT
    assert not (item.textInteractionFlags()
                & Qt.TextInteractionFlag.TextEditorInteraction)
    assert item.flags() & QGraphicsItem.GraphicsItemFlag.ItemIsMovable


def test_stale_edit_flags_do_not_swallow_the_drag(qapp, view):
    """A text item can keep TextEditorInteraction from an edit session that
    no longer owns the focus (a rebuild, or focus moved to another widget
    without a scene focus-out). Its next press must still be an ordinary
    movable-item press, not a caret click."""
    item = _text_item(view)
    item.setTextInteractionFlags(Qt.TextInteractionFlag.TextEditorInteraction)
    start = item.scenePos()

    _scene_drag(view, item, item.rect().center(), 30, 20)
    qapp.processEvents()

    assert item.scenePos() != start
    assert item.toPlainText() == NOTE_TEXT
    assert not (item.textInteractionFlags()
                & Qt.TextInteractionFlag.TextEditorInteraction)


def test_dragging_while_editing_moves_the_note(qapp, view):
    """Even with an edit session genuinely open, a drag that leaves the
    slop distance is a move: text editing is for clicks, not drags."""
    item = _text_item(view)
    item.enter_edit()
    assert item._text_editing
    start = item.scenePos()

    _scene_drag(view, item, item.rect().center(), 45, 30)
    qapp.processEvents()

    assert item.scenePos().x() == pytest.approx(start.x() + 45, abs=0.01)
    assert item.scenePos().y() == pytest.approx(start.y() + 30, abs=0.01)
    assert item.toPlainText() == NOTE_TEXT
    assert not item._text_editing


def test_a_small_click_while_editing_stays_a_text_click(qapp, view):
    """The slop distances drag from caret placement: a press/move/release
    well below the drag threshold must not move the note."""
    item = _text_item(view)
    item.enter_edit()
    start = item.scenePos()
    _scene_drag(view, item, item.rect().center(), 1, 1)
    qapp.processEvents()
    assert item.scenePos() == start


def test_double_click_still_opens_the_text_editor(qapp, view):
    item = _text_item(view)
    item.end_edit()
    item.setSelected(True)
    centre = QPointF(view.mapFromScene(item.mapToScene(item.rect().center())))
    view.mouseDoubleClickEvent(QMouseEvent(
        QEvent.MouseButtonDblClick, centre, centre,
        Qt.MouseButton.LeftButton, Qt.MouseButton.LeftButton,
        Qt.KeyboardModifier.NoModifier))
    qapp.processEvents()

    assert item._text_editing
    assert item.textInteractionFlags() & Qt.TextInteractionFlag.TextEditorInteraction
    assert view._scene.focusItem() is item


def test_f2_opens_the_text_editor(qapp, view):
    item = _text_item(view)
    item.end_edit()
    item.setSelected(True)
    view.keyPressEvent(QKeyEvent(
        QEvent.KeyPress, Qt.Key.Key_F2, Qt.KeyboardModifier.NoModifier))
    qapp.processEvents()

    assert item._text_editing
    assert item.textInteractionFlags() & Qt.TextInteractionFlag.TextEditorInteraction


def test_the_context_menu_route_opens_the_text_editor(qapp, view):
    item = _text_item(view)
    item.end_edit()
    item.setSelected(True)
    view._edit_annotation_text(item)
    qapp.processEvents()

    assert item._text_editing
    assert item.textInteractionFlags() & Qt.TextInteractionFlag.TextEditorInteraction


def test_a_dragged_anchored_note_records_its_offset_and_undo_restores(qapp):
    """The anchor semantics must survive the new drag path: moving a text
    note by its body keeps the link and stores dx/dy, and undo puts both
    the position and the pre-drag anchor back."""
    rbd = load_rbd(os.path.join(GOLD, "FactoryDemo", "input.rbd"))
    note = place_annotation(rbd, kind="text", text="Check",
                            adjacent_to="ArcticAir_10000", location="right")
    v = DiagramView()
    try:
        v.load_rbd(rbd)
        v.resize(1200, 800)
        v.show()
        qapp.processEvents()
        messages: list[str] = []
        v.status_message_sink = messages.append

        aid = note["id"]
        item = v._anno_items[aid]
        # The note starts behind the elements; raise it for the test so a
        # press in its middle lands on the note, not a link beneath it.
        item.setZValue(1000)
        v.tool_mode = "select"
        v._scene.clearSelection()
        item.setSelected(True)
        before = dict(v.annotation_entry(aid))
        base = v._anchored_geometry(
            {**before, "anchor": {k: x for k, x in before["anchor"].items()
                                  if k not in ("dx", "dy")}})

        _view_drag(v, item, item.rect().center(), 60, 30)
        qapp.processEvents()

        after = v.annotation_entry(aid)
        assert (after["x"], after["y"]) != (before["x"], before["y"])
        assert after["text"] == "Check"
        assert after["anchor"]["target"] == "ArcticAir_10000"
        assert after["anchor"]["dx"] == pytest.approx(
            after["x"] - base["x"], abs=0.1)
        assert after["anchor"]["dy"] == pytest.approx(
            after["y"] - base["y"], abs=0.1)
        assert "still anchored" in messages[-1]

        v._stack.undo()
        qapp.processEvents()
        restored = v.annotation_entry(aid)
        assert (restored["x"], restored["y"]) == (before["x"], before["y"])
        assert "dx" not in restored["anchor"] and "dy" not in restored["anchor"]
    finally:
        _dispose(qapp, v)
