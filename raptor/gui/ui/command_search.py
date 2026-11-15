"""Ctrl+Shift+P / Ctrl+K: run any command by name (WP-1.6)."""
from __future__ import annotations

from typing import Any

from PySide6.QtCore import QEvent, QObject, Qt
from PySide6.QtGui import QKeyEvent, QPalette
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
        # U19: the focus stays in the query, so the list is "inactive" and
        # its current row was drawn in the faint inactive colour; draw it
        # as strongly as when the list has focus.
        palette = self.results.palette()
        for role in (QPalette.ColorRole.Highlight, QPalette.ColorRole.HighlightedText):
            palette.setColor(QPalette.ColorGroup.Inactive, role,
                             palette.color(QPalette.ColorGroup.Active, role))
        self.results.setPalette(palette)
        lay.addWidget(self.results, 1)
        # ... and the arrow keys move through the list while typing
        self.query.installEventFilter(self)
        self.query.textChanged.connect(self._fill)
        self.query.returnPressed.connect(self._run)
        self.results.itemActivated.connect(lambda _item: self._run())
        self._fill("")

    _MOVES = {Qt.Key.Key_Up, Qt.Key.Key_Down, Qt.Key.Key_PageUp, Qt.Key.Key_PageDown}

    def eventFilter(self, watched: QObject, event: QEvent) -> bool:
        if (watched is self.query and event.type() == QEvent.Type.KeyPress
                and isinstance(event, QKeyEvent) and event.key() in self._MOVES):
            self.step(Qt.Key(event.key()))
            return True
        return super().eventFilter(watched, event)

    def step(self, key: Qt.Key) -> None:
        """Move the current row as the list itself would, skipping rows
        that cannot run."""
        count = self.results.count()
        if not count:
            return
        step = {Qt.Key.Key_Up: -1, Qt.Key.Key_Down: 1,
                Qt.Key.Key_PageUp: -10, Qt.Key.Key_PageDown: 10}[key]
        row = self.results.currentRow()
        target = max(0, min(count - 1, row + step))
        direction = 1 if step > 0 else -1
        probe = target
        while 0 <= probe < count and not (
                self.results.item(probe).flags() & Qt.ItemFlag.ItemIsEnabled):
            probe += direction
        if 0 <= probe < count:
            target = probe
        self.results.setCurrentRow(target)
        self.results.scrollToItem(self.results.item(target))

    def _recent(self) -> list[str]:
        from PySide6.QtCore import QSettings
        value = QSettings().value("quick_add/recent_commands", [])
        if isinstance(value, str):
            return [value]
        if isinstance(value, (list, tuple)):
            return [str(item) for item in value]
        return []

    def _note_recent(self, command_id: str) -> None:
        from PySide6.QtCore import QSettings
        recent = [command_id] + [value for value in self._recent()
                                 if value != command_id]
        QSettings().setValue("quick_add/recent_commands", recent[:12])

    def _candidates(self) -> list[Candidate]:
        from raptor.gui.command_table import COMMANDS
        recent = {value: index for index, value in
                  enumerate(self._recent())}
        out = []
        for spec in COMMANDS:
            if not spec.handler:
                continue
            out.append(Candidate(
                text=spec.text, category=spec.id.split(".", 1)[0],
                keywords=(spec.id.split(".", 1)[-1],),
                data=spec, recent=recent.get(spec.id, 99)))
        view = getattr(getattr(self._window, "_diagram_dock", None),
                       "view", None)
        if view is not None:
            for kind, items in (("block", getattr(view, "blocks", {})),
                                ("node", getattr(view, "nodes", {})),
                                ("event", getattr(view, "events", {}))):
                for element in items.values():
                    out.append(Candidate(
                        text=element.name, category=f"Go to {kind}",
                        keywords=(kind, "go", "locate"),
                        data=("element", kind, element.name),
                        recent=99))
        return out

    def _fill(self, query: str) -> None:
        self.results.clear()
        for candidate in rank(query, self._candidates()):
            spec = candidate.data
            if isinstance(spec, tuple):
                item = QListWidgetItem(
                    f"{candidate.text}    {candidate.category}")
                item.setData(Qt.ItemDataRole.UserRole, spec)
                self.results.addItem(item)
                continue
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
        if isinstance(command_id, tuple) and command_id[0] == "element":
            _kind, kind, name = command_id
            self.accept()
            view = getattr(getattr(self._window, "_diagram_dock", None),
                           "view", None)
            if view is not None:
                view.locate_element(kind, name)
            return
        spec = next((spec for spec in COMMANDS if spec.id == command_id),
                    None)
        if spec is None:
            return
        action = self._window.__dict__.get(spec.attr)
        self.accept()
        if action is not None and action.isEnabled():
            self._note_recent(command_id)
            action.trigger()
