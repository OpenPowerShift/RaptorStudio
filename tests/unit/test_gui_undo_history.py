"""The undo-history dialog and the configurable history cap."""
from __future__ import annotations

import os

import pytest

pytest.importorskip("PySide6")
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtGui import QUndoCommand, QUndoStack
from PySide6.QtWidgets import QApplication

from raptor.gui.ui.undo_history_dialog import UndoHistoryDialog


@pytest.fixture(scope="module")
def qapp():
    return QApplication.instance() or QApplication([])


def _stack(texts):
    stack = QUndoStack()
    for text in texts:
        stack.push(QUndoCommand(text))
    return stack


def test_the_dialog_lists_steps_and_jumps_to_one(qapp):
    stack = _stack(["Add Pump", "Change repair", "Move block"])
    dialog = UndoHistoryDialog(None, stack)
    assert dialog._list.count() == 4          # base row + three commands
    assert "currently after step 3" in dialog._note.text()
    dialog._list.setCurrentRow(0)              # before the first retained step
    dialog._go_to_selected()
    assert stack.index() == 0
    dialog._list.setCurrentRow(1)              # after "Add Pump"
    dialog._go_to_selected()
    assert stack.index() == 1
    assert "currently after step 1" in dialog._note.text()
    assert dialog._undo.isEnabled() and dialog._redo.isEnabled()


def test_the_stack_cap_drops_the_oldest_steps(qapp):
    stack = QUndoStack()
    stack.setUndoLimit(3)
    for text in ("one", "two", "three", "four"):
        stack.push(QUndoCommand(text))
    assert stack.count() == 3
    assert stack.text(0) == "two"
    assert stack.canUndo()


def test_preferences_apply_the_cap_to_the_window_stack(qapp, tmp_path, monkeypatch):
    from raptor.gui.ui import settings
    from raptor.gui.ui.main_window import MainWindow
    settings.set_value("startup/show_start_page", False)
    settings.set_value("editing/undo_limit", 2)
    w = MainWindow()
    try:
        assert w._undo_stack.undoLimit() == 2
        for text in ("a", "b", "c"):
            w._undo_stack.push(QUndoCommand(text))
        assert w._undo_stack.count() == 2
    finally:
        w._dirty = False
        w.close()
        settings.set_value("editing/undo_limit", 200)


def test_a_history_jump_is_refused_while_a_run_is_active(qapp):
    from raptor.gui.ui import settings
    from raptor.gui.ui.main_window import MainWindow
    settings.set_value("startup/show_start_page", False)
    w = MainWindow()
    try:
        w._run_in_progress = True
        assert w._jump_history(0) is False
        assert "Stop the run" in w._statusbar.currentMessage()
    finally:
        w._run_in_progress = False
        w._dirty = False
        w.close()
