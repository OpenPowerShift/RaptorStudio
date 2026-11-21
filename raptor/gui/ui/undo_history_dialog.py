"""Undo history: browse the model's steps and jump to any point in it.

A modeless view of the window's `QUndoStack`. It never runs a command
itself: a jump goes through `MainWindow._jump_history`, which refuses it
while a run is active and asks before the results on screen are discarded.
"""
from __future__ import annotations

from collections.abc import Callable
from typing import Any

from PySide6.QtCore import Qt
from PySide6.QtGui import QBrush, QColor, QKeySequence, QShortcut, QUndoStack
from PySide6.QtWidgets import (
    QAbstractItemView, QApplication, QDialog, QDialogButtonBox, QHBoxLayout,
    QLabel, QListWidget, QListWidgetItem, QPushButton, QVBoxLayout,
)

from raptor.gui.resources.design_tokens import (
    SPACING, ThemeManager, active_colour,
)

#: Row 0's target: the state before the oldest retained step.
BEFORE_FIRST = -1


class UndoHistoryDialog(QDialog):
    """A modeless list of the model's undo steps.

    Row 0 is the state before the oldest retained step and row *n* is the
    model after step *n*. Selecting a row picks the jump target; the note
    above the list says where the model is now, which step matches the
    saved state, and whether the cap is in force.
    """

    def __init__(self, parent: Any, stack: QUndoStack,
                 on_jump: Callable[[int], bool] | None = None):
        super().__init__(parent)
        self._stack = stack
        self._on_jump = on_jump
        #: True while a jump runs, so the stack's own signals cannot
        #: rebuild the list from under the command that is being replayed.
        self._jumping = False
        #: Why the last jump did not happen, shown after the position note.
        self._refusal = ""
        #: Row labels currently shown; text changes only when the stack
        #: gains, loses or replaces commands, so comparing them lets a
        #: plain move update two rows instead of rebuilding every one.
        self._texts: list[str] | None = None
        self._shown_index = -1
        self._shown_clean = -1
        self.setWindowTitle("Model History")
        self.setModal(False)
        self.setMinimumSize(620, 440)
        self._build()
        stack.indexChanged.connect(self._refresh)
        stack.cleanChanged.connect(self._refresh)
        ThemeManager.instance().on_changed(self._on_theme_changed)
        self._refresh()

    # -- construction --------------------------------------------------------

    def _build(self) -> None:
        lay = QVBoxLayout(self)
        lay.setContentsMargins(SPACING["lg"], SPACING["md"],
                               SPACING["lg"], SPACING["md"])
        lay.setSpacing(SPACING["sm"])
        self._note = QLabel("")
        self._note.setWordWrap(True)
        lay.addWidget(self._note)
        self._list = QListWidget()
        self._list.setAccessibleName("Model history steps")
        self._list.setAccessibleDescription(
            "Select a step, then press Go to step to move the model there")
        self._list.setSelectionMode(
            QAbstractItemView.SelectionMode.SingleSelection)
        self._list.itemDoubleClicked.connect(
            lambda _item: self._go_to_selected())
        self._list.itemActivated.connect(lambda _item: self._go_to_selected())
        lay.addWidget(self._list, 1)
        row = QHBoxLayout()
        self._undo = QPushButton("Undo")
        self._undo.setToolTip("Move the model back one step (Ctrl+Z)")
        self._undo.clicked.connect(
            lambda: self._jump_to(self._stack.index() - 1))
        self._redo = QPushButton("Redo")
        self._redo.setToolTip("Move the model forward one step (Ctrl+Y)")
        self._redo.clicked.connect(
            lambda: self._jump_to(self._stack.index() + 1))
        self._go = QPushButton("Go to step")
        self._go.setToolTip(
            "Move the model to just after the selected step (Enter)")
        self._go.clicked.connect(self._go_to_selected)
        row.addWidget(self._undo)
        row.addWidget(self._redo)
        row.addWidget(self._go)
        row.addStretch(1)
        lay.addLayout(row)
        box = QDialogButtonBox(QDialogButtonBox.StandardButton.Close)
        box.rejected.connect(self.reject)
        lay.addWidget(box)
        self._box = box
        self._hint = QLabel(
            "Ctrl+Z / Ctrl+Y step one (Ctrl+Shift+Z redoes); "
            "Ctrl+Home / Ctrl+End jump to an end. "
            "The step list belongs to the model that is open.")
        self._hint.setWordWrap(True)
        lay.addWidget(self._hint)
        self._add_shortcuts()
        self._restyle()

    def _add_shortcuts(self) -> None:
        """Undo/redo and the two ends, scoped to this dialog.

        The ribbon's Ctrl+Z stays the window's Undo; these only fire while
        the history panel has focus, where the same keys must move the
        model without closing the panel.
        """
        steps = ((QKeySequence.StandardKey.Undo, -1),
                 (QKeySequence.StandardKey.Redo, 1))
        for key, delta in steps:
            for binding in QKeySequence.keyBindings(key):
                QShortcut(QKeySequence(binding), self).activated.connect(
                    lambda d=delta: self._jump_to(self._stack.index() + d))
        QShortcut(QKeySequence("Ctrl+Home"), self).activated.connect(
            lambda: self._jump_to(0))
        QShortcut(QKeySequence("Ctrl+End"), self).activated.connect(
            lambda: self._jump_to(self._stack.count()))
        # The panel's own ribbon shortcut closes it when the panel has
        # focus (the window's action cannot fire from another window).
        QShortcut(QKeySequence("Ctrl+Shift+H"), self).activated.connect(
            self.reject)

    def _restyle(self) -> None:
        self._hint.setStyleSheet(
            f"color: {active_colour()['muted']};")

    def _on_theme_changed(self, _dark: bool) -> None:
        self._restyle()
        self._paint(0, self._list.count() - 1)

    # -- rows ----------------------------------------------------------------

    def _base_label(self) -> str:
        stack = self._stack
        limit = stack.undoLimit()
        if limit and stack.count() >= limit:
            label = "Before step 1 — at the history cap"
        else:
            label = "Before step 1 — the model as loaded"
        if stack.cleanIndex() == 0:
            label += "  · saved"
        return label

    def _rebuild_rows(self, texts: list[str]) -> None:
        """Create one row per stack state, cheapest way to add 1,000 items:
        the list is only rebuilt when the set of commands actually changed."""
        clean = self._stack.cleanIndex()
        self._list.blockSignals(True)
        try:
            self._list.clear()
            self._add_row(self._base_label(), BEFORE_FIRST)
            for i, text in enumerate(texts):
                label = f"{i + 1}. {text}"
                if clean == i + 1:
                    label += "  · saved"
                self._add_row(label, i)
        finally:
            self._list.blockSignals(False)
        self._paint(0, self._list.count() - 1)

    def _append_rows(self, first: int, texts: list[str]) -> None:
        """Add the rows for commands pushed since the last refresh, so a
        long session's push does not rebuild the whole list."""
        clean = self._stack.cleanIndex()
        for i in range(first, len(texts)):
            label = f"{i + 1}. {texts[i]}"
            if clean == i + 1:
                label += "  · saved"
            self._add_row(label, i)

    def _add_row(self, label: str, target: int) -> None:
        item = QListWidgetItem(label)
        item.setData(Qt.ItemDataRole.UserRole, target)
        self._list.addItem(item)

    def _paint(self, first: int, last: int) -> None:
        """Colour the states up to the current one as reachable and the
        rest as redo. Only the rows between the old and new positions need
        it, so a step does not repaint a 10,000-row list."""
        if self._list.count() == 0:
            return
        c = active_colour()
        done = QBrush(QColor(c["text"]))
        undone = QBrush(QColor(c["muted"]))
        index = self._stack.index()
        low = max(0, min(first, last))
        high = min(self._list.count() - 1, max(first, last))
        for row in range(low, high + 1):
            item = self._list.item(row)
            if item is not None:
                item.setForeground(done if row <= index else undone)

    # -- state ---------------------------------------------------------------

    def _refresh(self, *_args) -> None:
        if self._jumping:
            return
        stack = self._stack
        index = stack.index()
        count = stack.count()
        clean = stack.cleanIndex()
        texts = [stack.text(i) or f"Step {i + 1}" for i in range(count)]
        previous = self._shown_index
        # A rebuild clears the list, so remember what the user had chosen
        # to jump to (a clean marker update must not move it).
        keep = self._list.currentRow()
        # The saved marker is part of a row's label, so a new clean index
        # needs a rebuild even when no command was added.
        if texts != self._texts or clean != self._shown_clean:
            old = self._texts
            # A push at the top of the stack only adds rows; the base row
            # and the saved marker keep their meaning, so append instead
            # of rebuilding a list that may hold thousands of steps.
            appended = (old is not None and len(texts) > len(old)
                        and texts[:len(old)] == old
                        and clean == self._shown_clean)
            if appended:
                self._append_rows(len(old), texts)
            else:
                self._rebuild_rows(texts)
            self._texts = texts
            self._shown_clean = clean
        else:
            self._paint(previous, index)
        self._shown_index = index
        # Follow the stack when it moves; leave the user's chosen target
        # alone when only the clean marker changed.
        if index != previous:
            self._list.setCurrentRow(index)
        elif 0 <= keep < self._list.count():
            self._list.setCurrentRow(keep)
        else:
            self._list.setCurrentRow(index)
        self._update_note(count, index)
        self._undo.setEnabled(stack.canUndo())
        self._redo.setEnabled(stack.canRedo())
        self._go.setEnabled(count > 0)

    def _update_note(self, count: int, index: int) -> None:
        stack = self._stack
        if count == 0:
            text = ("No edits yet — every change you make to this model "
                    "will be listed here.")
        else:
            steps = "step" if count == 1 else "steps"
            parts = [f"{count} {steps}"]
            if index == 0:
                parts.append("currently before the first retained step")
            else:
                parts.append(f"currently after step {index}")
            clean = stack.cleanIndex()
            if clean < 0:
                parts.append("saved state is no longer in history")
            elif clean == index:
                parts.append("matches the saved state")
            else:
                parts.append(f"saved state is at step {clean}")
            limit = stack.undoLimit()
            if limit and count >= limit:
                parts.append(f"history capped at {limit} steps")
            text = " · ".join(parts) + "."
        if self._refusal:
            text = f"{text} {self._refusal}" if text else self._refusal
        self._note.setText(text)

    # -- jumping -------------------------------------------------------------

    def _go_to_selected(self) -> None:
        item = self._list.currentItem()
        if item is None:
            return
        try:
            target = int(item.data(Qt.ItemDataRole.UserRole))
        except (TypeError, ValueError):
            return
        self._jump_to(0 if target < 0 else target + 1)

    def _jump_to(self, target: int) -> None:
        stack = self._stack
        target = max(0, min(int(target), stack.count()))
        if self._jumping or target == stack.index():
            return
        self._refusal = ""
        self._jumping = True
        self._set_busy(True)
        ok = False
        try:
            if self._on_jump is not None:
                ok = bool(self._on_jump(target))
            else:
                stack.setIndex(target)
                ok = True
        except Exception as exc:  # noqa: BLE001 - a failed replay must not
            # leave the panel disabled or frozen mid-jump.
            self._refusal = f"The jump failed: {exc}"
        finally:
            self._jumping = False
            self._set_busy(False)
        if not ok and not self._refusal:
            self._refusal = "The jump was refused."
        self._shown_index = stack.index()
        self._refresh()

    def _set_busy(self, busy: bool) -> None:
        for widget in (self._list, self._undo, self._redo, self._go,
                       self._box):
            widget.setEnabled(not busy)
        if busy:
            QApplication.setOverrideCursor(Qt.CursorShape.WaitCursor)
        else:
            QApplication.restoreOverrideCursor()
