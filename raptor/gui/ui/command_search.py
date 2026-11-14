"""Ctrl+Shift+P / Ctrl+K: run any command by name (WP-1.6)."""
from __future__ import annotations

from typing import Any

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QDialog, QLineEdit, QListWidget, QListWidgetItem, QVBoxLayout,
)

from raptor.gui.quick_match import Candidate, rank


class CommandSearch(QDialog):
    """A filtered command list; Enter triggers the same QAction as the ribbon."""

    def __init__(self, window: Any, parent: Any = None) -> None:
        super().__init__(parent or window)
        self._window = window
        self.setWindowTitle("Run a command")
        self.resize(560, 420)
        lay = QVBoxLayout(self)
        self.query = QLineEdit()
        self.query.setPlaceholderText("Type a command, e.g. duplicate, check, "
                                      "export…")
        lay.addWidget(self.query)
        self.results = QListWidget()
        lay.addWidget(self.results, 1)
        self.query.textChanged.connect(self._fill)
        self.query.returnPressed.connect(self._run)
        self.results.itemActivated.connect(lambda _item: self._run())
        self._fill("")

    def _candidates(self) -> list[Candidate]:
        from raptor.gui.command_table import COMMANDS
        out = []
        for spec in COMMANDS:
            if not spec.handler:
                continue
            out.append(Candidate(
                text=spec.text, category=spec.id.split(".", 1)[0],
                keywords=(spec.id.split(".", 1)[-1],),
                data=spec, recent=0))
        return out

    def _fill(self, query: str) -> None:
        self.results.clear()
        for candidate in rank(query, self._candidates()):
            spec = candidate.data
            action = self._window.__dict__.get(spec.attr)
            shortcut = ""
            if action is not None:
                shortcut = action.shortcut().toString()
            item = QListWidgetItem(
                f"{spec.text}" + (f"    {shortcut}" if shortcut else ""))
            item.setData(Qt.ItemDataRole.UserRole, spec.id)
            if action is not None and not action.isEnabled():
                item.setFlags(item.flags() & ~Qt.ItemFlag.ItemIsEnabled)
                tip = action.toolTip()
                reason = (tip.split("Unavailable:", 1)[-1].strip()
                          if "Unavailable:" in tip else "unavailable")
                item.setToolTip(reason)
                item.setText(f"{item.text()}    ({reason})")
            self.results.addItem(item)
        if self.results.count():
            self.results.setCurrentRow(0)

    def _run(self) -> None:
        item = self.results.currentItem()
        if item is None:
            return
        from raptor.gui.command_table import COMMANDS
        command_id = item.data(Qt.ItemDataRole.UserRole)
        spec = next((spec for spec in COMMANDS if spec.id == command_id),
                    None)
        if spec is None:
            return
        action = self._window.__dict__.get(spec.attr)
        self.accept()
        if action is not None and action.isEnabled():
            action.trigger()
