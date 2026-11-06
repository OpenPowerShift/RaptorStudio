"""While text is being edited, Cut/Copy/Paste act on the text."""
from __future__ import annotations

import pytest
from PySide6.QtCore import QMimeData, QPointF, Qt
from PySide6.QtGui import QKeyEvent, QTextCursor
from PySide6.QtWidgets import QApplication, QLineEdit

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
    w._load_into_app(load_rbd(MODEL), MODEL)
    w.show()
    w.activateWindow()
    QApplication.processEvents()
    QApplication.clipboard().clear()
    yield w
    w._dirty = False
    w.close()


def _editing_note(win, text="abc"):
    view = win._diagram_dock.view
    view._begin_annotation("text", QPointF(20.0, 20.0))
    item = next(iter(view._anno_items.values()))
    item.setPlainText(text)
    view.setFocus()
    item.enter_edit()
    QApplication.processEvents()
    return view, item


def _set_text(text):
    mime = QMimeData()
    mime.setText(text)
    QApplication.clipboard().setMimeData(mime)
    QApplication.processEvents()


def test_plain_text_pastes_into_an_annotation_being_edited(win):
    view, item = _editing_note(win)
    _set_text("hello")
    assert win._paste_action.isEnabled()          # not greyed: text to paste
    cur = item.textCursor()
    cur.movePosition(QTextCursor.MoveOperation.End)
    item.setTextCursor(cur)
    before = len(view.blocks)
    win._edit_paste()
    assert item.toPlainText() == "abchello" and len(view.blocks) == before


def test_ctrl_v_on_the_canvas_pastes_text_not_elements(win):
    view, item = _editing_note(win, "")
    _set_text("typed")
    before = len(view.blocks)
    ev = QKeyEvent(QKeyEvent.Type.KeyPress, Qt.Key.Key_V,
                   Qt.KeyboardModifier.ControlModifier, "v")
    QApplication.sendEvent(view, ev)
    assert item.toPlainText() == "typed" and len(view.blocks) == before


def test_copying_text_leaves_text_only_on_the_clipboard(win):
    view, item = _editing_note(win, "hello")
    next(iter(view._block_items.values())).setSelected(True)
    view.copy_selection()                          # a Raptor clip is there
    item.setSelected(False)
    cur = item.textCursor()
    cur.select(QTextCursor.SelectionType.Document)
    item.setTextCursor(cur)
    assert win._copy_action.isEnabled() and win._cut_action.isEnabled()
    win._edit_copy()
    mime = QApplication.clipboard().mimeData()
    assert mime.text() == "hello" and not mime.hasFormat(
        "application/x-raptor-studio-clip")
    win._edit_cut()
    assert item.toPlainText() == ""


def test_a_raptor_clip_pastes_as_its_text_summary_into_the_editor(win):
    view, item = _editing_note(win, "")
    view._scene.clearSelection()
    next(iter(view._block_items.values())).setSelected(True)
    view.copy_selection()
    before = len(view.blocks)
    win._edit_paste()
    assert item.toPlainText().startswith("Raptor Studio: 1 block")
    assert len(view.blocks) == before


def test_elements_paste_again_after_leaving_edit_mode(win):
    view, item = _editing_note(win, "x")
    view._scene.clearSelection()
    next(iter(view._block_items.values())).setSelected(True)
    view.copy_selection()
    item._finish_edit()
    view.setFocus()
    QApplication.processEvents()
    assert win._paste_action.isEnabled()
    before = len(view.blocks)
    win._edit_paste()
    assert len(view.blocks) == before + 1
    _set_text("just text")
    assert not win._paste_action.isEnabled()        # unrelated text: greyed


def test_a_line_edit_with_the_focus_gets_cut_copy_paste(win):
    box = QLineEdit(win)
    box.show()
    box.setFocus()
    QApplication.processEvents()
    _set_text("name")
    assert win._paste_action.isEnabled() and not win._copy_action.isEnabled()
    win._edit_paste()
    assert box.text() == "name"
    box.selectAll()
    QApplication.processEvents()
    assert win._copy_action.isEnabled() and win._cut_action.isEnabled()
    win._edit_cut()
    assert box.text() == "" and QApplication.clipboard().text() == "name"
