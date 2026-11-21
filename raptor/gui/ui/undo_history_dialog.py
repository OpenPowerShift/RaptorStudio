"""Undo history: browse named steps and jump to any point in the model."""
from __future__ import annotations

from typing import Any

from collections.abc import Callable

from PySide6.QtCore import Qt
from PySide6.QtGui import QColor, QKeySequence, QShortcut, QUndoStack
from PySide6.QtWidgets import (
    QAbstractItemView, QDialog, QDialogButtonBox, QHBoxLayout, QLabel,
    QListWidget, QListWidgetItem, QPushButton, QVBoxLayout,
)

from raptor.gui.resources.design_tokens import active_colour


class UndoHistoryDialog(QDialog):
    """A modeless view of the model's undo stack.

    Each row is one command. Double-click a row (or press Go to) to move
    the model to just after that command; Undo and Redo move one step.
    """

    def __init__(self, parent: Any, stack: QUndoStack,
                 on_jump: Callable[[int], bool] | None = None):
        super().__init__(parent)
        self._stack = stack
        self._on_jump = on_jump
        self._jumping = False
        self.setWindowTitle("Model History")
        self.setMinimumSize(620, 440)
        self._build()
        stack.indexChanged.connect(self._refresh)
        stack.undoTextChanged.connect(self._refresh)
        stack.redoTextChanged.connect(self._refresh)
        self._refresh()

    def _build(self) -> None:
        lay = QVBoxLayout(self)
        lay.setSpacing(8)
        self._note = QLabel("")
        self._note.setWordWrap(True)
        lay.addWidget(self._note)
        self._list = QListWidget()
        self._list.setAccessibleName("Model history steps")
        self._list.setSelectionMode(
            QAbstractItemView.SelectionMode.SingleSelection)
        self._list.itemDoubleClicked.connect(
            lambda _item: self._go_to_selected())
        self._list.itemActivated.connect(lambda _item: self._go_to_selected())
        lay.addWidget(self._list, 1)
        row = QHBoxLayout()
        self._undo = QPushButton("Undo")
        self._undo.clicked.connect(
            lambda: self._jump_to(self._stack.index() - 1))
        self._redo = QPushButton("Redo")
        self._redo.clicked.connect(
            lambda: self._jump_to(self._stack.index() + 1))
        self._go = QPushButton("Go to step")
        self._go.setToolTip("Move the model to just after the selected step")
        self._go.clicked.connect(self._go_to_selected)
        row.addWidget(self._undo)
        row.addWidget(self._redo)
        row.addWidget(self._go)
        row.addStretch(1)
        lay.addLayout(row)
        box = QDialogButtonBox(QDialogButtonBox.StandardButton.Close)
        box.rejected.connect(self.reject)
        lay.addWidget(box)
        for key, delta in ((QKeySequence.StandardKey.Undo, -1),
                           (QKeySequence.StandardKey.Redo, 1)):
            shortcut = QShortcut(QKeySequence(key), self)
            shortcut.activated.connect(
                lambda d=delta: self._jump_to(self._stack.index() + d))

    def _refresh(self, *_args) -> None:
        index = self._stack.index()
        count = self._stack.count()
        self._note.setText(
            f"{count} step(s) · currently after step {index}."
            + (" Undo is unavailable." if not self._stack.canUndo() else "")
            + (" Redo is available." if self._stack.canRedo() else ""))
        c = active_colour()
        if self._jumping:
            return
        self._list.blockSignals(True)
        self._list.clear()
        done = QColor(c["text"])
        undone = QColor(c["muted"])
        base = QListWidgetItem("0. Before the first retained step")
        base.setData(Qt.ItemDataRole.UserRole, -1)
        base.setForeground(done)
        self._list.addItem(base)
        for i in range(count):
            text = self._stack.text(i) or f"Step {i + 1}"
            item = QListWidgetItem(f"{i + 1}. {text}")
            item.setData(Qt.ItemDataRole.UserRole, i)
            if i < index:
                item.setForeground(done)
            else:
                item.setForeground(undone)
            if i == index - 1:
                item.setSelected(True)
            self._list.addItem(item)
        self._list.blockSignals(False)
        self._list.setCurrentRow(index)
        self._undo.setEnabled(self._stack.canUndo())
        self._redo.setEnabled(self._stack.canRedo())
        self._go.setEnabled(self._list.count() > 0)
        limit = self._stack.undoLimit()
        clean = self._stack.cleanIndex()
        if limit and count >= limit:
            self._note.setText(self._note.text() +
                               "  Earlier steps were discarded at the cap.")
        if clean < 0:
            self._note.setText(self._note.text() +
                               "  The saved state is no longer in history.")
        else:
            self._note.setText(self._note.text() +
                               f"  Saved state: after step {clean}.")


    def _go_to_selected(self) -> None:
        item = self._list.currentItem()
        if item is None:
            return
        try:
            row = int(item.data(Qt.ItemDataRole.UserRole))
        except (TypeError, ValueError):
            return
        self._jump_to(0 if row < 0 else row + 1)

    def _jump_to(self, target: int) -> None:
        target = max(0, min(target, self._stack.count()))
        if target == self._stack.index():
            return
        self._jumping = True
        self._list.setEnabled(False)
        self._go.setEnabled(False)
        try:
            if self._on_jump is not None:
                self._on_jump(target)
            else:
                self._stack.setIndex(target)
        finally:
            self._jumping = False
            self._list.setEnabled(True)
            self._refresh()
