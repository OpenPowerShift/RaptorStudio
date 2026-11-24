"""Version History: browse, preview, open, restore or delete snapshots."""
from __future__ import annotations

import html
from pathlib import Path
from collections.abc import Callable
from typing import Any

from PySide6.QtCore import Qt
from PySide6.QtGui import QFont
from PySide6.QtWidgets import (
    QAbstractItemView, QDialog, QDialogButtonBox, QHBoxLayout, QInputDialog,
    QLabel, QListWidget, QListWidgetItem, QPlainTextEdit, QPushButton,
    QVBoxLayout,
)

from raptor.gui import i18n
from raptor.api.history import store as history_store
from raptor.core.history import HistoryStore, Snapshot, file_digest
from raptor.gui.ui.history_description import DescriptionPanel


class HistoryDialog(QDialog):
    """Browse the saved versions of one model file.

    `action` is ``"open"`` (open the snapshot as an unsaved model),
    ``"restore"`` (write it over the file) or ``"delete"`` (remove it and
    stay open). The caller performs the action; the dialog only chooses.
    """

    def __init__(self, parent: Any, file: str | Path,
                 store: HistoryStore | None = None, *,
                 current_model: Any = None,
                 save_current: Callable[[], bool] | None = None):
        super().__init__(parent)
        self.file = str(file)
        self._store = store or history_store()
        #: The model as it is open now (with any unsaved edits): what a
        #: version is compared with in the preview.
        self._current_model = current_model
        #: Saves unsaved edits before "Save version now" records the file;
        #: returns False when the user cancelled the save.
        self._save_current = save_current
        #: Loaded snapshots by id: a version is read once per dialog.
        self._loaded: dict[str, Any] = {}
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
        # Model statistics, then this version's Markdown description, then
        # the results and the comparison with the open model.
        self._stats = QLabel("")
        self._stats.setWordWrap(True)
        self._stats.setTextInteractionFlags(
            Qt.TextInteractionFlag.TextSelectableByMouse)
        self._stats.setAccessibleName("Model statistics")
        mono_stats = QFont("monospace")
        mono_stats.setStyleHint(QFont.StyleHint.Monospace)
        self._stats.setFont(mono_stats)
        right.addWidget(self._stats)
        self._desc = DescriptionPanel(self._save_description)
        self._desc.saved.connect(lambda _id: self._after_description_saved())
        right.addWidget(self._desc, 2)
        self._preview = QPlainTextEdit()
        self._preview.setReadOnly(True)
        self._preview.setAccessibleName("What this version contains")
        mono = QFont("monospace")
        mono.setStyleHint(QFont.StyleHint.Monospace)
        self._preview.setFont(mono)
        self._preview.setLineWrapMode(
            QPlainTextEdit.LineWrapMode.NoWrap)
        right.addWidget(self._preview, 3)
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
            if shot.description:
                label += "   ¶"
            if current:
                label += "   (current)"
            item = QListWidgetItem(label)
            item.setData(Qt.ItemDataRole.UserRole, shot.id)
            tip = f"{shot.size_text()} · {shot.sha256[:16]}"
            if shot.description:
                first = next((ln.strip() for ln in
                              shot.description.splitlines() if ln.strip()), "")
                tip += "\n¶ " + first[:120]
            item.setToolTip(tip)
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

    def _confirm_save_description(self) -> bool:
        """Ask what to do with an unsaved description edit: True saves it,
        False discards it. A separate method so tests can replace it."""
        from raptor.gui.ui.dialogs import question_box
        return question_box(
            self, "Unsaved Description",
            "The description of this version has unsaved changes.\n\n"
            "Save them?", yes_text="Save description",
            no_text="Discard changes", default_yes=True)

    def _save_description(self, snapshot_id: str, text: str) -> bool:
        ok = self._store.set_description(self.file, snapshot_id, text)
        if not ok:
            self._note.setText("That version no longer exists; the "
                               "description was not saved.")
        return ok

    def _after_description_saved(self) -> None:
        self._note.setText("Description saved.")
        self.reload()

    def _settle_description(self, next_id: str | None) -> None:
        """Before another version is shown, save or drop a pending edit."""
        desc = self._desc
        if not desc.editing or desc.snapshot_id == next_id:
            return
        if desc.is_dirty() and self._confirm_save_description():
            desc.save()

    def done(self, result: int) -> None:
        self._settle_description(None)
        super().done(result)

    def _show_selected(self, row: int) -> None:
        exists = 0 <= row < len(self._snapshots)
        self._settle_description(
            self._snapshots[row].id if exists else None)
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
            self._stats.clear()
            self._desc.show_version(None, "")
            return
        shot = self._snapshots[row]
        current = bool(shot.sha256) and shot.sha256 == self._current_digest
        self._details.setText(
            f"<b>{shot.time_text()}</b> · {shot.reason} · {shot.size_text()}"
            + (" · <i>current version</i>" if current else "")
            + (f"<br>{html.escape(shot.label)}" if shot.label else ""))
        stats, rest = self._describe_parts(shot)
        self._stats.setText(stats)
        self._preview.setPlainText(rest)
        if self._desc.snapshot_id == shot.id:
            self._desc.update_text(shot.description)
        else:
            self._desc.show_version(shot.id, shot.description)

    def _load_snapshot(self, shot: Snapshot) -> Any:
        """The snapshot as a model, read once; raises on a damaged file."""
        if shot.id not in self._loaded:
            from raptor.core.fileio.model_file import load_model_file
            self._loaded[shot.id] = load_model_file(str(shot.path))
        return self._loaded[shot.id]

    def _describe(self, shot: Snapshot) -> str:
        stats, rest = self._describe_parts(shot)
        return stats + ("\n" + rest if rest else "")

    def _describe_parts(self, shot: Snapshot) -> tuple[str, str]:
        """(statistics line, results and comparison) for one version.

        What a version holds and how it differs from the open model.

        Never the raw file: an `.rbdz` is a zip, and its bytes decoded as
        text were shown as noise (and made Qt search every installed font
        for Syriac, Thaana and N'Ko glyphs).
        """
        try:
            model = self._load_snapshot(shot)
        except Exception as exc:  # noqa: BLE001 - a damaged snapshot is shown, not raised
            return "", (f"This version could not be read as a model: {exc}\n\n"
                        "It is kept unchanged; Open as copy or Restore will "
                        "report the same problem.")
        stats = _contents_line(model)
        lines: list[str] = []
        results = _archive_results_line(shot.path)
        if results:
            lines.append(results)
        current = self._current_model
        if current is None:
            return stats, "\n".join(lines)
        try:
            from raptor.api.model_diff import diff_models
            diff = diff_models(model, current)
        except Exception as exc:  # noqa: BLE001 - the comparison is an extra
            lines += ["", f"Could not compare with the open model: {exc}"]
            return stats, "\n".join(lines)
        if lines:
            lines.append("")
        if diff.get("identical"):
            lines.append("Same as the model that is open now (layout "
                         "aside).")
        else:
            lines.append("Changes from this version to the model open now "
                         f"({diff['summary']['connectivity']}):")
            lines += _diff_lines(diff)
        return stats, "\n".join(lines).strip("\n")

    def _snapshot_now(self) -> None:
        """Record the model as it is now, with an optional label.

        Unsaved edits are saved first (saving records a version of its
        own; the label then names it). The file on disk alone was recorded
        before, so with unsaved edits -- or right after a save, which had
        already recorded the file -- the button quietly did nothing.
        """
        label, ok = QInputDialog.getText(
            self, "Save Version",
            "Label for the new version (optional):")
        if not ok:
            return
        if self._save_current is not None and not self._save_current():
            self._note.setText("Saving was cancelled; no version recorded.")
            return
        newest = self._store.snapshots(self.file)[:1]
        shot = self._store.record(
            self.file, reason="manual", label=label)
        if shot is None:
            shot = newest[0] if newest else None
            when = shot.time_text() if shot is not None else ""
            self._note.setText(
                f"The model has not changed since the version recorded "
                f"{when}; that version is selected. Type a label to name "
                "it." if when else
                i18n.tr("The model has not changed; no version was added."))
        elif newest and newest[0].id == shot.id:
            self._note.setText(
                f"Named the newest version recorded {shot.time_text()}.")
        elif newest and newest[0].sha256 == shot.sha256:
            self._note.setText(
                f"Added a version ({shot.size_text()}); the model has not "
                f"changed, so the earlier one recorded "
                f"{newest[0].time_text()} keeps its label.")
        else:
            self._note.setText(
                f"Recorded {shot.time_text()} ({shot.size_text()}).")
        self.reload()
        row = next((i for i, s in enumerate(self._snapshots)
                    if shot is not None and s.id == shot.id), -1)
        if row >= 0:
            self._list.setCurrentRow(row)
            # Offer the description right away: the person who just
            # recorded a version usually has a reason to note why.
            if shot is not None and not shot.description:
                self._desc.begin_edit()

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


def _contents_line(model: Any) -> str:
    """`13 blocks · 15 nodes · 0 events · 3 hierarchies` for one model."""
    sections = getattr(model, "sections", None) or {}

    def count(name: str) -> int:
        section = sections.get(name)
        return len(section.rows) if section is not None else 0

    parts = ((count("BLOCK_DATA"), "block", "blocks"),
             (count("NODE_DATA"), "node", "nodes"),
             (count("EVENT_DATA"), "event", "events"),
             (count("HIERARCHY_DATA"), "hierarchy", "hierarchies"))
    text = " · ".join(f"{n:,} {one if n == 1 else many}"
                      for n, one, many in parts)
    return f"Model: {text}"


def _archive_results_line(path: Path) -> str:
    """For an `.rbdz` version, the saved results it carries; "" otherwise."""
    if Path(path).suffix.lower() != ".rbdz":
        return ""
    import json
    import zipfile
    try:
        with zipfile.ZipFile(path) as zf:
            names = [n for n in zf.namelist() if n.endswith("summary.json")]
            if not names:
                return i18n.tr("Archive without saved results.")
            with zf.open(names[0]) as handle:
                summary = json.loads(handle.read(1_000_000))
    except (OSError, ValueError, zipfile.BadZipFile):
        return i18n.tr("Archive: the saved results could not be read.")
    runs = summary.get("num_runs") if isinstance(summary, dict) else None
    when = summary.get("run_at") if isinstance(summary, dict) else None
    bits = [i18n.tr("Archive with saved results")]
    if isinstance(runs, int):
        bits.append(f"{runs:,} run{'' if runs == 1 else 's'}")
    if isinstance(when, str) and when:
        bits.append(f"run {when.replace('T', ' ')[:16]}")
    return ": ".join([bits[0], ", ".join(bits[1:])]) if len(bits) > 1 \
        else bits[0] + "."


def _diff_lines(diff: dict[str, Any]) -> list[str]:
    """One aligned table per changed group, headed in plain words."""
    from raptor.api.markdown import _DIFF_TITLES
    from raptor.api.model_diff import GROUPS, flatten
    from raptor.cli.cmd_tools import _table_text
    rows = flatten(diff)
    out: list[str] = []
    for group in GROUPS:
        mine = [r for r in rows if r["group"] == group]
        if not mine:
            continue
        body = [[f"{r['kind']} {r['path']}".strip(), r["field"],
                 r["a"] or "—", r["b"] or "—"] for r in mine]
        out += ["", f"{_DIFF_TITLES[group]}:",
                _table_text(["Element", "Field", i18n.tr("This version"), i18n.tr("Open now")],
                            body)]
    return out
