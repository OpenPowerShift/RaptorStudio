"""Clipboard commands while text is being edited.

Cut, Copy and Paste mean two things in the app: elements on the canvas, and
text in whatever editor has the keyboard (an annotation being edited in
place, a rename box, an Inspector or table cell, the Find box). While an
editor has the focus they act on its text, plain text from the system
clipboard pastes, and a Raptor clip pastes as its plain-text summary. Copying
text puts text and nothing else on the clipboard, so it never leaves a
Raptor clip behind.
"""
from __future__ import annotations

from typing import Any

from PySide6.QtCore import Qt
from PySide6.QtGui import QGuiApplication
from PySide6.QtWidgets import (
    QAbstractSpinBox, QApplication, QComboBox, QGraphicsTextItem,
    QGraphicsView, QLineEdit, QPlainTextEdit, QTextEdit)

_EDITING = Qt.TextInteractionFlag.TextEditorInteraction


def _canvas_item(view: QGraphicsView) -> QGraphicsTextItem | None:
    scene = view.scene()
    item = scene.focusItem() if scene is not None else None
    if (isinstance(item, QGraphicsTextItem)
            and item.textInteractionFlags() & _EDITING):
        return item
    return None


def active_editor() -> Any | None:
    """The text editor that has the keyboard, or None: a line edit or text
    edit that is not read-only, or the annotation text item being edited in
    place on a canvas that has the focus."""
    w = QApplication.focusWidget()
    if isinstance(w, QGraphicsView):
        return _canvas_item(w)
    if isinstance(w, (QLineEdit, QTextEdit, QPlainTextEdit)):
        return None if w.isReadOnly() else w
    if isinstance(w, (QAbstractSpinBox, QComboBox)):
        return w.lineEdit()
    return None


def has_selection(editor: Any) -> bool:
    if isinstance(editor, QLineEdit):
        return editor.hasSelectedText()
    if isinstance(editor, (QTextEdit, QPlainTextEdit, QGraphicsTextItem)):
        return editor.textCursor().hasSelection()
    return False


def can_copy(editor: Any) -> bool:
    """Copy/Cut have text to take. An annotation's selection cannot be
    watched cheaply, so while one is being edited they stay available and do
    nothing without a selection."""
    return isinstance(editor, QGraphicsTextItem) or has_selection(editor)


def clipboard_has_text() -> bool:
    return bool(QGuiApplication.clipboard().text())


def _selected(item: QGraphicsTextItem) -> str:
    # Qt marks paragraph breaks in selectedText() with U+2029.
    return item.textCursor().selectedText().replace(" ", "\n")


def copy(editor: Any) -> None:
    if isinstance(editor, QGraphicsTextItem):
        text = _selected(editor)
        if text:
            QGuiApplication.clipboard().setText(text)
    else:
        editor.copy()


def cut(editor: Any) -> None:
    if isinstance(editor, QGraphicsTextItem):
        text = _selected(editor)
        if text:
            QGuiApplication.clipboard().setText(text)
            editor.textCursor().removeSelectedText()
    else:
        editor.cut()


def paste(editor: Any) -> None:
    if isinstance(editor, QGraphicsTextItem):
        text = QGuiApplication.clipboard().text()
        if text:
            editor.textCursor().insertText(text)
    else:
        editor.paste()
