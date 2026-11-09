"""Issue #214: typing in General > Identity > Comment must not rebuild the
Inspector (which deleted the editor, reset the caret and jumped the pane to
the bottom) or create one undo command per character."""
from __future__ import annotations

import pytest
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication

from raptor.core.fileio.rbd import load_rbd

pytestmark = pytest.mark.gui

MODEL = "tests/golden/Phasing3/input.rbd"


@pytest.fixture(scope="module")
def qapp():
    return QApplication.instance() or QApplication([])


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


TARGETS = [
    ("block", 5),
    ("node", 14),
    ("event", 32),
    ("hier", 23),
]


def _item(v, kind, oid):
    return {"block": v._block_items, "node": v._node_items,
            "event": v._event_items, "hier": v._hier_items}[kind][oid]


def _show(win, kind, oid):
    v = win._diagram_dock.view
    v._scene.clearSelection()
    _item(v, kind, oid).setSelected(True)
    # Selection populates the Inspector on its own debounce timer (80 ms)
    # so a click does not pay for a heavy rebuild mid-gesture.
    QTest.qWait(120)
    QApplication.processEvents()
    assert win._inspector.current_target() == (kind, oid)


@pytest.mark.parametrize("kind,oid", TARGETS)
def test_typing_comment_keeps_the_editor_caret_and_scroll(win, kind, oid):
    from raptor.gui.ui.inspector import COMMENT_COMMIT_DELAY_MS, _CommentEdit

    v = win._diagram_dock.view
    # Start from an empty comment so "abc" is the whole expected value.
    v.set_element_comment(kind, oid, "")
    v._stack.clear()
    _show(win, kind, oid)
    insp = win._inspector
    edit = insp._comment_edit
    assert isinstance(edit, _CommentEdit)
    assert edit.toPlainText() == ""

    scroll = insp._scroll_general.verticalScrollBar()
    edit.setFocus()
    QApplication.processEvents()
    # Set the scroll AFTER focusing: focus may legitimately scroll the
    # editor into view once, but typing must not move the pane again.
    scroll.setValue(scroll.maximum() // 2)
    QApplication.processEvents()
    before_scroll = scroll.value()
    assert edit.textCursor().position() == 0

    QTest.keyClicks(edit, "abc")
    QApplication.processEvents()

    assert insp._comment_edit is edit, "the editor widget was rebuilt"
    assert edit.textCursor().position() == 3, "the caret was reset"
    assert scroll.value() == before_scroll, "the pane jumped"
    # Nothing is written until the debounce fires: a typing burst is a
    # local draft, not one model write per key.
    assert v.current_comment_stored(kind, oid) == ""

    QTest.qWait(COMMENT_COMMIT_DELAY_MS + 150)
    QApplication.processEvents()

    assert v.current_comment_stored(kind, oid) == "abc"
    assert v._stack.count() == 1, "the burst must be one undo command"
    assert insp._comment_edit is edit, "the debounced commit rebuilt the editor"
    assert edit.textCursor().position() == 3
    assert scroll.value() == before_scroll


@pytest.mark.parametrize("kind,oid", TARGETS)
def test_focus_out_flushes_a_pending_comment(win, kind, oid):
    v = win._diagram_dock.view
    v.set_element_comment(kind, oid, "")
    v._stack.clear()
    _show(win, kind, oid)
    edit = win._inspector._comment_edit
    edit.setFocus()
    QApplication.processEvents()
    QTest.keyClicks(edit, "draft")
    QApplication.processEvents()
    assert v.current_comment_stored(kind, oid) == ""

    # Focus-out is what commits the tail of a sentence immediately. Send
    # the event directly: under the offscreen platform a synthetic
    # `clearFocus()` does not reliably deliver it to the scroll-area
    # widget, and the commit path is the same either way.
    from PySide6.QtCore import QEvent
    from PySide6.QtGui import QFocusEvent
    edit.focusOutEvent(QFocusEvent(QEvent.Type.FocusOut))
    QApplication.processEvents()

    assert v.current_comment_stored(kind, oid) == "draft"
    assert v._stack.count() == 1
