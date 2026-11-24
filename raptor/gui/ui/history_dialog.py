"""Version History: browse, preview, open, restore or delete snapshots."""
from __future__ import annotations

import html
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
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
from raptor.core.history import HistoryStore, Snapshot, format_bytes
from raptor.gui.ui.history_description import DescriptionPanel


_CANCELLED: Any = object()


@dataclass
class CurrentState:
    """The working state the Current row stands for.

    `model` is the open model as Save would write it. `run_id` identifies
    the result set an `.rbdz` would carry (None: no results); it matters only
    for archives. `dirty` is the window's unsaved-changes flag.
    """
    model: Any
    dirty: bool = False
    run_id: str | None = None


class HistoryDialog(QDialog):
    """Browse the saved versions of one model file.

    `action` is ``"open"`` (open the snapshot as an unsaved model),
    ``"restore"`` (write it over the file) or ``"delete"`` (remove it and
    stay open). The caller performs the action; the dialog only chooses.

    The first row, Current, is not a version: it is the working state (the
    open model as Save would write it), compared with the newest version.
    Save version now turns it into one. The dialog is modal, so the state
    cannot change under it except through that button.
    """

    def __init__(self, parent: Any, file: str | Path,
                 store: HistoryStore | None = None, *,
                 current_model: Any = None,
                 save_current: Callable[[], bool] | None = None,
                 current_state: Callable[[], CurrentState | None] | None = None):
        super().__init__(parent)
        self._current_state_fn = current_state
        self._state: CurrentState | None = None
        self._current_matches = False
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
        self.action: str | None = None
        #: True while the model is being saved or a version copied in.
        self._working = False
        self._busy = False                      # `run_blocking`'s flag
        self._phase_bar: Any = None
        self.snapshot: Snapshot | None = None
        self.setWindowTitle(f"Version History — {Path(file).name}")
        self.setMinimumSize(860, 520)
        self._build()
        self.reload()

    def _build(self) -> None:
        lay = QVBoxLayout(self)
        lay.setSpacing(8)
        intro = QLabel(
            "Versions are checkpoints you save with Save version now; "
            "saving the model does not add one. They live in your private "
            "application folder, not beside the model.")
        intro.setWordWrap(True)
        lay.addWidget(intro)

        body = QHBoxLayout()
        body.setSpacing(10)
        self._list = QListWidget()
        self._list.setSelectionMode(
            QAbstractItemView.SelectionMode.SingleSelection)
        self._list.setWordWrap(True)       # a long Current line wraps
        self._list.setTextElideMode(Qt.TextElideMode.ElideNone)
        self._list.setHorizontalScrollBarPolicy(
            Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self._list.currentRowChanged.connect(self._show_selected)
        body.addLayout(self._build_list_column(), 2)

        body.addLayout(self._build_right_column(), 3)
        lay.addLayout(body, 1)
        lay.addLayout(self._build_actions())
        # "Saving version… 45 %" appears here while a save or copy runs.
        self._progress_slot = QVBoxLayout()
        lay.addLayout(self._progress_slot)

        box = QDialogButtonBox(QDialogButtonBox.StandardButton.Close)
        box.rejected.connect(self.reject)
        self._close_box = box
        lay.addWidget(box)
        if not Path(self.file).is_file():
            self._snapshot_btn.setEnabled(False)
            self._snapshot_btn.setToolTip(
                "The model file is missing; there is nothing to record")

    def _build_right_column(self) -> QVBoxLayout:
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
        self._current_hint = QLabel(
            "Add a description when you save a version.")
        self._current_hint.setWordWrap(True)
        self._current_hint.setObjectName("SectionNote")
        self._current_hint.hide()
        right.addWidget(self._current_hint)
        self._preview = QPlainTextEdit()
        self._preview.setReadOnly(True)
        self._preview.setAccessibleName("What this version contains")
        mono = QFont("monospace")
        mono.setStyleHint(QFont.StyleHint.Monospace)
        self._preview.setFont(mono)
        self._preview.setLineWrapMode(
            QPlainTextEdit.LineWrapMode.NoWrap)
        right.addWidget(self._preview, 3)
        return right

    def _build_list_column(self) -> QVBoxLayout:
        left = QVBoxLayout()
        left.addWidget(self._list, 1)
        self._total = QLabel("")
        self._total.setWordWrap(True)
        self._total.setAccessibleName("History size")
        left.addWidget(self._total)
        self._warning = QLabel("")
        self._warning.setWordWrap(True)
        self._warning.setAccessibleName("History size warning")
        self._warning.hide()
        left.addWidget(self._warning)
        return left

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
            "Save the current state as a version (saves unsaved edits first)")
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
        self._snapshots = self._store.snapshots(self.file)
        self._refresh_state()
        self._list.blockSignals(True)
        self._list.clear()
        bold = QFont(self._list.font())
        bold.setBold(True)
        head = QListWidgetItem(self._current_text())
        head.setData(Qt.ItemDataRole.UserRole, None)
        head.setFont(bold)
        self._list.addItem(head)
        for shot in self._snapshots:
            label = (f"{shot.time_text()}   {_reason_word(shot.reason)}   "
                     f"{shot.size_text()}")
            if shot.label:
                label += f"   {shot.label}"
            if shot.description:
                label += "   ¶"
            item = QListWidgetItem(label)
            item.setData(Qt.ItemDataRole.UserRole, shot.id)
            tip = f"{shot.size_text()} · {shot.sha256[:16]}"
            if shot.description:
                first = next((ln.strip() for ln in
                              shot.description.splitlines() if ln.strip()), "")
                tip += "\n¶ " + first[:120]
            item.setToolTip(tip)
            self._list.addItem(item)
        self._list.blockSignals(False)
        self._show_usage()
        row = next((i + 1 for i, s in enumerate(self._snapshots)
                    if s.id == selected), 0)
        self._list.setCurrentRow(row)
        self._show_selected(row)

    def _show_usage(self) -> None:
        total = self._store.usage(self.file)
        self._total.setText(
            i18n.tr("History size: %1 for %2 version(s). Identical files "
                    "are stored once.").replace("%1", format_bytes(total))
            .replace("%2", str(len(self._snapshots))))
        over = total > self._store.max_bytes
        self._warning.setVisible(over)
        if over:
            self._warning.setText(
                i18n.tr("This history is over %1. Versions you save are "
                        "never deleted automatically; remove ones you no "
                        "longer need with Delete version.")
                .replace("%1", format_bytes(self._store.max_bytes)))
            from raptor.gui.resources.design_tokens import active_colour
            self._warning.setStyleSheet(
                f"color: {active_colour()['warn']}; font-weight: bold;")

    # -- the Current row -----------------------------------------------
    def _refresh_state(self) -> None:
        """Re-read the working state and compare it with the newest version."""
        state: CurrentState | None = None
        try:
            if self._current_state_fn is not None:
                state = self._current_state_fn()
            elif self._current_model is not None:
                state = CurrentState(self._current_model)
            elif Path(self.file).is_file():
                from raptor.core.fileio.model_file import load_model_file
                state = CurrentState(
                    load_model_file(self.file),
                    run_id=_archive_run_id(Path(self.file)))
        except Exception:  # noqa: BLE001 - the row then says it cannot compare
            state = None
        self._state = state
        self._current_matches = (state is not None and bool(self._snapshots)
                                 and self._matches(self._snapshots[0], state))

    def _matches(self, newest: Snapshot, state: CurrentState) -> bool:
        """True when Save would write what the newest version holds.

        Both sides go through the one writer, so formatting never counts as
        a difference but layout does; the zoom and pan the canvas happens to
        have are not part of the model and are ignored. An archive's results matter
        too: they are compared by run identity (the digest of the run's
        manifest), so no archive is built just to be compared.
        """
        try:
            mine = _comparable(state.model)
            theirs = _comparable(self._load_snapshot(newest))
        except Exception:  # noqa: BLE001
            return False
        if mine != theirs:
            return False
        if newest.path.suffix.lower() == ".rbdz":
            return state.run_id == _archive_run_id(newest.path)
        return True

    def _current_text(self) -> str:
        if self._current_matches:
            newest = self._snapshots[0]
            text = i18n.tr("Current — matches the version saved %1").replace(
                "%1", newest.time_text())
            if newest.label:
                text += f" ({newest.label})"
        else:
            text = i18n.tr("Current — not checkpointed")
        if self._state is not None and self._state.dirty:
            text += " · " + i18n.tr("unsaved changes")
        return text

    def _show_current(self) -> None:
        self._details.setText(f"<b>{html.escape(self._current_text())}</b>")
        self._desc.hide()
        self._current_hint.show()
        self._desc.show_version(None, "")
        state = self._state
        if state is None:
            self._stats.clear()
            self._preview.setPlainText(i18n.tr(
                "The open model could not be read for comparison."))
            return
        self._stats.setText(_contents_line(state.model))
        if not self._snapshots:
            self._preview.setPlainText(i18n.tr(
                "No version to compare with yet. Save version now makes "
                "this the first one."))
            return
        newest = self._snapshots[0]
        lines: list[str] = []
        try:
            from raptor.api.model_diff import diff_models
            diff = diff_models(self._load_snapshot(newest), state.model)
        except Exception as exc:  # noqa: BLE001 - the comparison is an extra
            self._preview.setPlainText(
                i18n.tr("Could not compare with the newest version:")
                + f" {exc}")
            return
        if diff.get("identical"):
            lines.append(i18n.tr(
                "The model content is the same as the newest version "
                "(layout aside)."))
            if not self._current_matches:
                lines.append(i18n.tr(
                    "Only the layout or the saved results differ."))
        else:
            lines.append(i18n.tr("Changes from the newest version to the "
                                 "current model (%1):").replace(
                "%1", str(diff["summary"]["connectivity"])))
            lines += _diff_lines(diff)
        self._preview.setPlainText("\n".join(lines).strip("\n"))

    def current_id(self) -> str | None:
        """The version the Current row matches, if it matches one."""
        return self._snapshots[0].id if self._current_matches else None

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
        if self._working:
            # A save or a copy into History is running: closing now would
            # abandon it half done. The dialog closes once it has finished.
            self._note.setText(
                i18n.tr("Saving — the dialog can be closed when it finishes."))
            return
        self._settle_description(None)
        super().done(result)

    def _show_selected(self, row: int) -> None:
        is_current = row == 0
        index = row - 1
        exists = 0 <= index < len(self._snapshots)
        self._settle_description(
            self._snapshots[index].id if exists else None)
        for button in (self._open_btn, self._restore_btn, self._label_btn,
                       self._delete_btn):
            button.setEnabled(exists)
        self._current_hint.setVisible(is_current)
        self._desc.setVisible(not is_current)
        if is_current:
            self._show_current()
            return
        if not exists:
            self._details.setText(i18n.tr("Select a version."))
            self._preview.clear()
            self._stats.clear()
            self._desc.show_version(None, "")
            return
        shot = self._snapshots[index]
        self._details.setText(
            f"<b>{shot.time_text()}</b> · {html.escape(_reason_word(shot.reason))}"
            f" · {shot.size_text()}"
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
            return "", (i18n.tr("This version could not be read as a model:")
                    + f" {exc}\n\n"
                    + i18n.tr("It is kept unchanged; Open as copy or "
                              "Restore will report the same problem."))
        stats = _contents_line(model)
        lines: list[str] = []
        results = _archive_results_line(shot.path)
        if results:
            lines.append(results)
        current = (self._state.model if self._state is not None
                   else self._current_model)
        if current is None:
            return stats, "\n".join(lines)
        try:
            from raptor.api.model_diff import diff_models
            diff = diff_models(model, current)
        except Exception as exc:  # noqa: BLE001 - the comparison is an extra
            lines += ["", i18n.tr("Could not compare with the open model:")
                  + f" {exc}"]
            return stats, "\n".join(lines)
        if lines:
            lines.append("")
        if diff.get("identical"):
            lines.append(i18n.tr("Same as the model that is open now "
                                 "(layout aside)."))
        else:
            lines.append(i18n.tr("Changes from this version to the model "
                                 "open now (%1):").replace(
                "%1", str(diff["summary"]["connectivity"])))
            lines += _diff_lines(diff)
        return stats, "\n".join(lines).strip("\n")

    # -- progress host (see `save_progress.run_blocking`) -----------------
    def add_progress_widget(self, widget: Any) -> None:
        self._progress_slot.addWidget(widget)

    def remove_progress_widget(self, widget: Any) -> None:
        self._progress_slot.removeWidget(widget)

    def show_hint(self, text: str) -> None:
        self._note.setText(text)

    def _set_working(self, working: bool) -> None:
        """Lock the buttons while a save/copy runs; restore them after."""
        self._working = working
        for button in (self._snapshot_btn, self._open_btn, self._restore_btn,
                       self._label_btn, self._delete_btn):
            button.setEnabled(False)
        self._list.setEnabled(not working)
        self._close_box.setEnabled(not working)
        if not working:
            self._snapshot_btn.setEnabled(Path(self.file).is_file())
            self._show_selected(self._list.currentRow())

    def _save_with_progress(self) -> bool:
        """Run the host's save of unsaved edits, mirroring its progress in
        this dialog (the save itself runs on a worker; see `save_progress`)."""
        from PySide6.QtCore import QTimer
        from raptor.gui.ui.save_progress import SHOW_AFTER_MS, SaveProgressBar
        host = self.parent()
        sinks = getattr(host, "_save_progress_sinks", None)
        latest: list[Any] = [0, None]

        def sink(done: int, total: int | None) -> None:
            latest[:] = [done, total]
            if self._phase_bar is not None:
                self._phase_bar.update_progress(done, total)

        def show() -> None:
            bar = SaveProgressBar(Path(self.file).name, self)
            bar.update_progress(*latest)
            self._progress_slot.addWidget(bar)
            self._phase_bar = bar

        timer = QTimer(self)
        timer.setSingleShot(True)
        timer.timeout.connect(show)
        if sinks is not None:
            sinks.append(sink)
        timer.start(SHOW_AFTER_MS)
        try:
            return bool(self._save_current())  # type: ignore[misc]
        finally:
            timer.stop()
            if sinks is not None and sink in sinks:
                sinks.remove(sink)
            if self._phase_bar is not None:
                self._progress_slot.removeWidget(self._phase_bar)
                self._phase_bar.deleteLater()
                self._phase_bar = None

    def _snapshot_now(self) -> None:
        """Turn the Current state into a version, with an optional label.

        Unsaved edits are saved first (a save writes the file and records
        nothing), then the file is checkpointed. When the newest version
        has the same bytes and no name yet, the label names it; otherwise a
        new entry is added (sharing the stored bytes).
        """
        label, ok = QInputDialog.getText(
            self, i18n.tr("Save Version"),
            i18n.tr("Label for the new version (optional):"))
        if not ok:
            return
        self._set_working(True)
        try:
            shot, newest = self._save_and_record(label)
        except Exception as exc:  # noqa: BLE001 - say so in the dialog
            self._set_working(False)
            self._note.setText(
                i18n.tr("Could not save the version: %1").replace(
                    "%1", str(exc)))
            return
        self._set_working(False)
        if shot is _CANCELLED:
            self._note.setText(
                i18n.tr("Saving was cancelled; no version recorded."))
            return
        if shot is None:
            shot = newest[0] if newest else None
            when = shot.time_text() if shot is not None else ""
            self._note.setText(
                i18n.tr("The model has not changed since the version "
                        "saved %1; that version is selected. Type a label "
                        "to name it.").replace("%1", when) if when else
                i18n.tr("The model has not changed; no version was added."))
        elif newest and newest[0].id == shot.id:
            self._note.setText(
                i18n.tr("Named the newest version saved %1.").replace(
                    "%1", shot.time_text()))
        elif newest and newest[0].sha256 == shot.sha256:
            self._note.setText(
                i18n.tr("Added a version (%1); the model has not changed, "
                        "so the earlier one saved %2 keeps its label.")
                .replace("%1", shot.size_text())
                .replace("%2", newest[0].time_text()))
        else:
            self._note.setText(
                i18n.tr("Saved %1 (%2).").replace("%1", shot.time_text())
                .replace("%2", shot.size_text()))
        self.reload()
        row = next((i + 1 for i, s in enumerate(self._snapshots)
                    if shot is not None and s.id == shot.id), -1)
        if row >= 0:
            self._list.setCurrentRow(row)
            # Offer the description right away: the person who just
            # recorded a version usually has a reason to note why.
            if shot is not None and not shot.description:
                self._desc.begin_edit()

    def _save_and_record(self, label: str) -> tuple[Any, list[Snapshot]]:
        """Save unsaved edits, then copy the file into History (both off the
        GUI thread, with progress shown here)."""
        from raptor.gui.ui.save_progress import run_blocking
        if self._save_current is not None and not self._save_with_progress():
            return _CANCELLED, []
        newest = self._store.snapshots(self.file)[:1]
        shot = run_blocking(
            self, self,
            lambda progress: self._store.record(
                self.file, reason="manual", label=label, progress=progress),
            kind="version", flag="_busy")
        return shot, newest

    def _label_selected(self) -> None:
        shot = self._selected_snapshot()
        if shot is None:
            return
        label, ok = QInputDialog.getText(
            self, i18n.tr("Label Version"),
            i18n.tr("Label (leave empty to clear):"),
            text=shot.label)
        if not ok:
            return
        if self._store.set_label(self.file, shot.id, label):
            self._note.setText(i18n.tr("Label updated."))
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
        row = self._list.currentRow() - 1        # row 0 is Current
        return self._snapshots[row] if 0 <= row < len(self._snapshots) else None


def _reason_word(reason: str) -> str:
    """Why a version exists, in the GUI language."""
    if reason == "manual":
        return i18n.tr("manual")
    if reason == "snapshot":
        return i18n.tr("snapshot")
    if reason == "save":
        return i18n.tr("save")
    if reason == "before restore":
        return i18n.tr("before restore")
    if reason == "restored":
        return i18n.tr("restored")
    if reason == "opened":
        return i18n.tr("opened")
    if reason == "recovered":
        return i18n.tr("recovered")
    return reason


def _contents_line(model: Any) -> str:
    """`13 blocks · 15 nodes · 0 events · 3 hierarchies` for one model."""
    sections = getattr(model, "sections", None) or {}

    def count(name: str) -> int:
        section = sections.get(name)
        return len(section.rows) if section is not None else 0

    # Each word is a literal tr() call so the catalogue scan finds it.
    parts = ((count("BLOCK_DATA"),
              i18n.tr("block"), i18n.tr("blocks")),
             (count("NODE_DATA"),
              i18n.tr("node"), i18n.tr("nodes")),
             (count("EVENT_DATA"),
              i18n.tr("event"), i18n.tr("events")),
             (count("HIERARCHY_DATA"),
              i18n.tr("hierarchy"), i18n.tr("hierarchies")))
    text = " · ".join(f"{n:,} {one if n == 1 else many}"
                      for n, one, many in parts)
    return f"{i18n.tr('Model:')} {text}"


def _comparable(model: Any) -> dict[str, Any]:
    """The model as Save would write it, minus what is not model content."""
    import json
    from raptor.core.fileio.rbds import render_for_save
    doc = json.loads(render_for_save(model))
    for key in ("view", "view_transform", "meta"):
        doc.pop(key, None)
    return doc


def _archive_run_id(path: Path) -> str | None:
    """The run identity an `.rbdz` carries (None: no results, or not one).

    Read from the archive's summary (a few KB), never its result files.
    """
    if Path(path).suffix.lower() != ".rbdz":
        return None
    import json
    import zipfile
    try:
        with zipfile.ZipFile(path) as zf:
            names = [n for n in zf.namelist() if n.endswith("summary.json")]
            if not names:
                return None
            with zf.open(names[0]) as handle:
                summary = json.loads(handle.read(1_000_000))
    except (OSError, ValueError, zipfile.BadZipFile):
        return None
    run_id = summary.get("run_id") if isinstance(summary, dict) else None
    return str(run_id) if run_id else None


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
                _table_text([i18n.tr("Element"), i18n.tr("Field"), i18n.tr("This version"),
                             i18n.tr("Open now")],
                            body)]
    return out
