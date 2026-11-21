"""Version History: browse, preview, open, restore or delete snapshots."""
from __future__ import annotations

import html
from pathlib import Path
from typing import Any

from PySide6.QtCore import Qt
from PySide6.QtGui import QFont
from PySide6.QtWidgets import (
    QAbstractItemView, QDialog, QDialogButtonBox, QHBoxLayout, QInputDialog,
    QLabel, QListWidget, QListWidgetItem, QPlainTextEdit, QPushButton,
    QVBoxLayout,
)

from raptor.api.history import store as history_store
from raptor.core.history import HistoryStore, Snapshot, file_digest


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
        self._current_digest = ""
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
        self._note = QLabel("")
        self._note.setWordWrap(True)
        right.addWidget(self._note)
        self._preview = QPlainTextEdit()
        self._preview.setReadOnly(True)
        self._preview.setLineWrapMode(
            QPlainTextEdit.LineWrapMode.NoWrap)
        right.addWidget(self._preview, 1)
        body.addLayout(right, 3)
        lay.addLayout(body, 1)
        lay.addLayout(self._build_actions())

        box = QDialogButtonBox(QDialogButtonBox.StandardButton.Close)
        box.rejected.connect(self.reject)
        lay.addWidget(box)
        if not Path(self.file).is_file():
            self._snapshot_btn.setEnabled(False)
            self._snapshot_btn.setToolTip(
                "The model file is missing; there is nothing to record")

    def _build_actions(self) -> QHBoxLayout:
        row = QHBoxLayout()
        self._open_btn = QPushButton("Open as copy")
        self._open_btn.setToolTip(
            "Open this version in a new window as an unsaved model")
        self._open_btn.clicked.connect(self._open)
        self._restore_btn = QPushButton("Restore to file")
        self._restore_btn.setToolTip(
            "Write this version over the model file (asks first)")
        self._restore_btn.clicked.connect(self._restore)
        self._label_btn = QPushButton("Label…")
        self._label_btn.setToolTip("Name this version to find it again")
        self._label_btn.clicked.connect(self._label_selected)
        self._snapshot_btn = QPushButton("Save version now…")
        self._snapshot_btn.setToolTip(
            "Record the file as it is now, without waiting for the next save")
        self._snapshot_btn.clicked.connect(self._snapshot_now)
        self._delete_btn = QPushButton("Delete version")
        self._delete_btn.clicked.connect(self._delete)
        row.addWidget(self._open_btn)
        row.addWidget(self._restore_btn)
        row.addWidget(self._label_btn)
        row.addStretch(1)
        row.addWidget(self._snapshot_btn)
        row.addWidget(self._delete_btn)
        for button in (self._open_btn, self._restore_btn, self._label_btn,
                       self._delete_btn):
            button.setEnabled(False)
        return row

    def reload(self) -> None:
        selected = self._selected_id()
        self._current_digest = file_digest(self.file)
        self._snapshots = self._store.snapshots(self.file)
        self._list.blockSignals(True)
        self._list.clear()
        bold = QFont(self._list.font())
        bold.setBold(True)
        for shot in self._snapshots:
            current = bool(shot.sha256) and shot.sha256 == self._current_digest
            label = f"{shot.time_text()}   {shot.reason}"
            if shot.label:
                label += f"   {shot.label}"
            if current:
                label += "   (current)"
            item = QListWidgetItem(label)
            item.setData(Qt.ItemDataRole.UserRole, shot.id)
            item.setToolTip(f"{shot.size_text()} · {shot.sha256[:16]}")
            if current:
                item.setFont(bold)
            self._list.addItem(item)
        self._list.blockSignals(False)
        row = next((i for i, s in enumerate(self._snapshots)
                    if s.id == selected), 0 if self._snapshots else -1)
        if row >= 0:
            self._list.setCurrentRow(row)
        else:
            self._show_selected(-1)

    def current_id(self) -> str | None:
        """The version that matches the file on disk, if one is recorded."""
        if not self._current_digest:
            return None
        return next((s.id for s in self._snapshots
                     if s.sha256 == self._current_digest), None)

    def _selected_id(self) -> str | None:
        item = self._list.currentItem()
        return item.data(Qt.ItemDataRole.UserRole) if item else None

    def _show_selected(self, row: int) -> None:
        exists = 0 <= row < len(self._snapshots)
        for button in (self._open_btn, self._restore_btn, self._label_btn,
                       self._delete_btn):
            button.setEnabled(exists)
        if not exists:
            if self._snapshots:
                self._details.setText("Select a version.")
            elif not Path(self.file).is_file():
                self._details.setText(
                    "No versions and no model file: the file has been moved "
                    "or deleted. History is tied to the file's location.")
            else:
                self._details.setText(
                    "No versions yet. Versions are recorded when you save; "
                    "use <b>Save version now…</b> to record one immediately.")
            self._preview.clear()
            return
        shot = self._snapshots[row]
        current = bool(shot.sha256) and shot.sha256 == self._current_digest
        self._details.setText(
            f"<b>{shot.time_text()}</b> · {shot.reason} · {shot.size_text()}"
            + (" · <i>current version</i>" if current else "")
            + (f"<br>{html.escape(shot.label)}" if shot.label else ""))
        try:
            with open(shot.path, encoding="utf-8", errors="replace") as f:
                text = f.read(200_000)
            size = shot.path.stat().st_size
        except OSError as exc:
            text = f"Could not read this version: {exc}"
            size = 0
        if size > 200_000:
            text += "\n\n… preview truncated; the snapshot itself is complete."
        self._preview.setPlainText(text)

    def _snapshot_now(self) -> None:
        """Record the file immediately, with an optional label."""
        label, ok = QInputDialog.getText(
            self, "Save Version",
            "Label for the new version (optional):")
        if not ok:
            return
        shot = self._store.record(
            self.file, reason="manual", label=label)
        if shot is None:
            self._note.setText(
                "The newest version already matches the file; no duplicate "
                "was added.")
            return
        self._note.setText(f"Recorded {shot.time_text()} ({shot.size_text()}).")
        self.reload()
        row = next((i for i, s in enumerate(self._snapshots)
                    if s.id == shot.id), -1)
        if row >= 0:
            self._list.setCurrentRow(row)

    def _label_selected(self) -> None:
        shot = self._selected_snapshot()
        if shot is None:
            return
        label, ok = QInputDialog.getText(
            self, "Label Version", "Label (leave empty to clear):",
            text=shot.label)
        if not ok:
            return
        if self._store.set_label(self.file, shot.id, label):
            self._note.setText("Label updated.")
            self.reload()

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
