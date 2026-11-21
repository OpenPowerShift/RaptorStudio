"""The undo-history browser, its cap, and history jumps."""
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


def _window(qapp):
    """A MainWindow with no model loaded (cheap: no canvas rebuild)."""
    from raptor.gui.ui import settings
    from raptor.gui.ui.main_window import MainWindow
    settings.set_value("startup/show_start_page", False)
    return MainWindow()


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


def test_an_empty_history_says_so_and_cannot_jump(qapp):
    dialog = UndoHistoryDialog(None, QUndoStack())
    assert "No edits yet" in dialog._note.text()
    assert not dialog._go.isEnabled()
    assert not dialog._undo.isEnabled()
    assert not dialog._redo.isEnabled()
    assert dialog._list.count() == 1          # only the base row


def test_the_saved_marker_follows_saves_and_undo(qapp):
    stack = _stack(["one", "two"])
    stack.setIndex(1)
    stack.setClean()
    dialog = UndoHistoryDialog(None, stack)
    assert dialog._list.item(1).text().endswith("· saved")
    assert "matches the saved state" in dialog._note.text()
    stack.undo()
    # The saved state is still the one after step 1; only the position
    # moved, so the marker stays and the note points at it.
    assert dialog._list.item(1).text().endswith("· saved")
    assert "saved state is at step 1" in dialog._note.text()
    stack.setClean()                          # saved at the new position
    assert dialog._list.item(0).text().endswith("· saved")
    assert not dialog._list.item(1).text().endswith("· saved")
    assert "matches the saved state" in dialog._note.text()


def test_the_selection_follows_the_stack_but_not_the_clean_marker(qapp):
    stack = _stack(["one", "two", "three"])
    dialog = UndoHistoryDialog(None, stack)
    dialog._list.setCurrentRow(1)              # a jump target to browse
    stack.setClean()                           # not a move
    assert dialog._list.currentRow() == 1
    stack.undo()                               # a move
    assert dialog._list.currentRow() == stack.index()


def test_a_refused_jump_keeps_the_position_and_says_why(qapp):
    stack = _stack(["one", "two", "three"])
    dialog = UndoHistoryDialog(None, stack, on_jump=lambda _i: False)
    dialog._list.setCurrentRow(0)
    dialog._go_to_selected()
    assert stack.index() == 3
    assert "refused" in dialog._note.text()
    assert dialog._go.isEnabled() and dialog._list.isEnabled()


def test_a_jump_goes_back_and_forward_through_the_window(qapp):
    from raptor.core.edit.new_model import build_new_model
    w = _window(qapp)
    try:
        w._rbd = build_new_model()             # jump path needs a model
        dialog = UndoHistoryDialog(w, w._undo_stack, on_jump=w._jump_history)
        for text in ("one", "two", "three"):
            w._undo_stack.push(QUndoCommand(text))
        dialog._list.setCurrentRow(0)
        dialog._go_to_selected()
        assert w._undo_stack.index() == 0
        dialog._list.setCurrentRow(2)
        dialog._go_to_selected()
        assert w._undo_stack.index() == 2
        dialog._undo.click()                   # one step back
        assert w._undo_stack.index() == 1
        dialog._redo.click()                   # and forward again
        assert w._undo_stack.index() == 2
        assert "step 2 of 3" in w._statusbar.currentMessage()
    finally:
        w._dirty = False
        w.close()


def test_loading_another_model_starts_a_new_history(qapp):
    from raptor.core.edit.new_model import build_new_model
    from raptor.gui.ui import settings
    w = _window(qapp)
    try:
        w._load_into_app(build_new_model("First"), None)
        w._undo_stack.push(QUndoCommand("Edit the first model"))
        assert w._undo_stack.count() == 1
        dialog = UndoHistoryDialog(w, w._undo_stack, on_jump=w._jump_history)
        # A cap changed mid-session is applied by the next load.
        settings.set_value("editing/undo_limit", 2)
        w._apply_undo_limit()
        assert w._undo_stack.undoLimit() != 2  # Qt refuses a non-empty stack
        w._load_into_app(build_new_model("Second"), None)
        assert w._undo_stack.count() == 0
        assert w._undo_stack.undoLimit() == 2
        assert "No edits yet" in dialog._note.text()
    finally:
        settings.set_value("editing/undo_limit", 200)
        w._dirty = False
        w.close()


def test_closing_the_model_empties_and_closes_the_history(qapp):
    from raptor.core.edit.new_model import build_new_model
    w = _window(qapp)
    try:
        w._load_into_app(build_new_model("Doomed"), None)
        w._undo_stack.push(QUndoCommand("Edit"))
        dialog = UndoHistoryDialog(w, w._undo_stack, on_jump=w._jump_history)
        dialog.show()
        w._undo_history_dialog = dialog
        w._dirty = False
        w._close_current_file()
        assert w._undo_stack.count() == 0
        assert getattr(w, "_undo_history_dialog", None) is None
    finally:
        w._dirty = False
        w.close()


def test_a_history_jump_is_refused_while_a_run_is_active(qapp):
    from raptor.core.edit.new_model import build_new_model
    w = _window(qapp)
    try:
        w._rbd = build_new_model()
        w._run_in_progress = True
        assert w._jump_history(0) is False
        assert "Stop the run" in w._statusbar.currentMessage()
    finally:
        w._run_in_progress = False
        w._dirty = False
        w.close()


def test_a_jump_needs_a_model(qapp):
    w = _window(qapp)
    try:
        assert w._jump_history(0) is False
        assert "Open or create a model" in w._statusbar.currentMessage()
    finally:
        w._dirty = False
        w.close()


class _StubView:
    """Enough of DiagramView for the jump end: level, hiers, flush."""

    _current_parent = 0
    hiers = {}

    def __init__(self, level, hiers):
        self._history_jump = True
        self._history_jump_level = level
        self.hiers = hiers
        self.levels = []
        self.flushed = 0
        self._stack = self

    def _set_level(self, parent_id):
        self.levels.append(parent_id)

    def flush_signals(self):
        self.flushed += 1


def test_ending_a_jump_rebuilds_one_level_and_flushes_once(qapp):
    from raptor.gui.ui.diagram import DiagramView
    view = _StubView(7, {7: object()})
    DiagramView.end_history_jump(view)
    assert view.levels == [7]
    assert view.flushed == 1
    assert not view._history_jump


def test_ending_a_jump_whose_hierarchy_was_undone_goes_home(qapp):
    from raptor.gui.ui.diagram import DiagramView
    view = _StubView(7, {})                    # the jump removed it
    DiagramView.end_history_jump(view)
    assert view.levels == [0]
    assert view.flushed == 1


def test_a_replayed_command_defers_its_refresh_during_a_jump(qapp):
    from raptor.gui.ui.diagram_commands import _ViewCmd

    class _Signal:
        def __init__(self):
            self.emitted = 0

        def emit(self):
            self.emitted += 1

    class _View:
        _current_parent = 0
        _loading = False
        _history_jump = True
        _pending_signals = []

        def __init__(self):
            self.model_changed = _Signal()

    view = _View()
    cmd = _ViewCmd(view, "one")
    cmd._done()
    assert view.model_changed.emitted == 0
    assert view._pending_signals == ["model_changed"]
    view._history_jump = False
    cmd._done()
    assert view.model_changed.emitted == 1
