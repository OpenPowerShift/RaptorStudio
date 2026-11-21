"""Undo history: browse named steps and jump to any point in the model."""
from __future__ import annotations

from typing import Any

from PySide6.QtCore import Qt
from PySide6.QtGui import QColor, QUndoStack
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

    def __init__(self, parent: Any, stack: QUndoStack):
        super().__init__(parent)
        self._stack = stack
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
        self._list.setSelectionMode(
            QAbstractItemView.SelectionMode.SingleSelection)
        self._list.itemDoubleClicked.connect(
            lambda _item: self._go_to_selected())
        lay.addWidget(self._list, 1)
        row = QHBoxLayout()
        self._undo = QPushButton("Undo")
        self._undo.clicked.connect(self._stack.undo)
        self._redo = QPushButton("Redo")
        self._redo.clicked.connect(self._stack.redo)
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

    def _refresh(self, *_args) -> None:
        index = self._stack.index()
        count = self._stack.count()
        self._note.setText(
            f"{count} step(s) · currently after step {index}."
            + (" Undo is unavailable." if not self._stack.canUndo() else "")
            + (" Redo is available." if self._stack.canRedo() else ""))
        c = active_colour()
        self._list.blockSignals(True)
        self._list.clear()
        done = QColor(c["text"])
        undone = QColor(c["muted"])
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
        if 0 <= index - 1 < self._list.count():
            self._list.setCurrentRow(index - 1)
        self._undo.setEnabled(self._stack.canUndo())
        self._redo.setEnabled(self._stack.canRedo())
        self._go.setEnabled(self._list.count() > 0)

    def _go_to_selected(self) -> None:
        item = self._list.currentItem()
        if item is None:
            return
        try:
            row = int(item.data(Qt.ItemDataRole.UserRole))
        except (TypeError, ValueError):
            return
        self._stack.setIndex(row + 1)
