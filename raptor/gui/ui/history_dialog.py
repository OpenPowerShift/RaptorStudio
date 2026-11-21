"""Version History: browse, preview, open, restore or delete snapshots."""
from __future__ import annotations

from pathlib import Path
from typing import Any

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QAbstractItemView, QDialog, QDialogButtonBox, QHBoxLayout, QLabel,
    QListWidget, QListWidgetItem, QPlainTextEdit, QPushButton, QVBoxLayout,
)

from raptor.api.history import store as history_store
from raptor.core.history import HistoryStore, Snapshot


class HistoryDialog(QDialog):
    """Browse the saved versions of one model file.

    `action` is ``"open"`` (open the snapshot as an unsaved model),
    ``"restore"`` (write it over the file) or ``"delete"`` (remove it and
    stay open). The caller performs the action; the dialog only chooses.
    """

    def __init__(self, parent: Any, file: str | Path,
                 store: HistoryStore | None = None):
        super().__init__(parent)
        self.file = str(file)
        self._store = store or history_store()
        self._snapshots: list[Snapshot] = []
        self.action: str | None = None
        self.snapshot: Snapshot | None = None
        self.setWindowTitle(f"Version History — {Path(file).name}")
        self.setMinimumSize(860, 520)
        self._build()
        self.reload()

    def _build(self) -> None:
        lay = QVBoxLayout(self)
        lay.setSpacing(8)
        intro = QLabel(
            "Versions are written automatically when you save. They live in "
            "your private application folder, not beside the model.")
        intro.setWordWrap(True)
        lay.addWidget(intro)

        body = QHBoxLayout()
        body.setSpacing(10)
        self._list = QListWidget()
        self._list.setSelectionMode(
            QAbstractItemView.SelectionMode.SingleSelection)
        self._list.currentRowChanged.connect(self._show_selected)
        body.addWidget(self._list, 2)

        right = QVBoxLayout()
        self._details = QLabel("Select a version.")
        self._details.setWordWrap(True)
        right.addWidget(self._details)
        self._preview = QPlainTextEdit()
        self._preview.setReadOnly(True)
        self._preview.setLineWrapMode(
            QPlainTextEdit.LineWrapMode.NoWrap)
        right.addWidget(self._preview, 1)
        body.addLayout(right, 3)
        lay.addLayout(body, 1)

        row = QHBoxLayout()
        self._open_btn = QPushButton("Open as copy")
        self._open_btn.setToolTip(
            "Open this version in a new window as an unsaved model")
        self._open_btn.clicked.connect(self._open)
        self._restore_btn = QPushButton("Restore to file")
        self._restore_btn.setToolTip(
            "Write this version over the model file (asks first)")
        self._restore_btn.clicked.connect(self._restore)
        self._delete_btn = QPushButton("Delete version")
        self._delete_btn.clicked.connect(self._delete)
        row.addWidget(self._open_btn)
        row.addWidget(self._restore_btn)
        row.addStretch(1)
        row.addWidget(self._delete_btn)
        lay.addLayout(row)

        box = QDialogButtonBox(QDialogButtonBox.StandardButton.Close)
        box.rejected.connect(self.reject)
        lay.addWidget(box)
        for button in (self._open_btn, self._restore_btn, self._delete_btn):
            button.setEnabled(False)

    def reload(self) -> None:
        selected = self._selected_id()
        self._snapshots = self._store.snapshots(self.file)
        self._list.blockSignals(True)
        self._list.clear()
        for shot in self._snapshots:
            label = f"{shot.time_text()}   {shot.reason}"
            if shot.label:
                label += f"   {shot.label}"
            item = QListWidgetItem(label)
            item.setData(Qt.ItemDataRole.UserRole, shot.id)
            item.setToolTip(f"{shot.size_text()} · {shot.sha256[:16]}")
            self._list.addItem(item)
        self._list.blockSignals(False)
        row = next((i for i, s in enumerate(self._snapshots)
                    if s.id == selected), 0 if self._snapshots else -1)
        if row >= 0:
            self._list.setCurrentRow(row)
        else:
            self._show_selected(-1)

    def _selected_id(self) -> str | None:
        item = self._list.currentItem()
        return item.data(Qt.ItemDataRole.UserRole) if item else None

    def _show_selected(self, row: int) -> None:
        exists = 0 <= row < len(self._snapshots)
        for button in (self._open_btn, self._restore_btn, self._delete_btn):
            button.setEnabled(exists)
        if not exists:
            self._details.setText("Select a version.")
            self._preview.clear()
            return
        shot = self._snapshots[row]
        self._details.setText(
            f"<b>{shot.time_text()}</b> · {shot.reason} · {shot.size_text()}"
            + (f"<br>{shot.label}" if shot.label else ""))
        try:
            with open(shot.path, encoding="utf-8", errors="replace") as f:
                text = f.read(200_000)
        except OSError as exc:
            text = f"Could not read this version: {exc}"
        if shot.path.stat().st_size > 200_000:
            text += "\n\n… preview truncated; the snapshot itself is complete."
        self._preview.setPlainText(text)

    def _open(self) -> None:
        shot = self._selected_snapshot()
        if shot is not None:
            self.action, self.snapshot = "open", shot
            self.accept()

    def _restore(self) -> None:
        shot = self._selected_snapshot()
        if shot is not None:
            self.action, self.snapshot = "restore", shot
            self.accept()

    def _delete(self) -> None:
        shot = self._selected_snapshot()
        if shot is None:
            return
        from raptor.gui.ui.dialogs import question_box
        if not question_box(
                self, "Delete Version",
                f"Delete the version from {shot.time_text()}?\n\nThe model "
                "file is not changed.", yes_text="Delete version",
                no_text="Keep"):
            return
        self._store.discard(self.file, shot.id)
        self.reload()

    def _selected_snapshot(self) -> Snapshot | None:
        row = self._list.currentRow()
        return self._snapshots[row] if 0 <= row < len(self._snapshots) else None
