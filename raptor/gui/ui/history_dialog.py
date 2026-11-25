"""Version History: browse, preview, open, restore or delete snapshots."""
from __future__ import annotations

import html
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from PySide6.QtCore import QEvent, QRect, QSize, Qt, QTimer
from PySide6.QtGui import QFont, QPainter, QPen
from PySide6.QtWidgets import (
    QAbstractItemView, QApplication, QDialog, QDialogButtonBox, QHBoxLayout,
    QInputDialog, QLabel, QListWidget, QListWidgetItem, QPlainTextEdit,
    QPushButton, QStyle, QStyledItemDelegate, QStyleOptionViewItem,
    QVBoxLayout,
)

from raptor.gui import i18n
from raptor.api.history import store as history_store
from raptor.core.history import (
    HistoryError, HistoryStore, Snapshot, format_bytes)
from raptor.gui.ui.history_description import DescriptionPanel


_CANCELLED: Any = object()
#: Item data for a version row: what the delegate paints.
_ROW_ROLE = Qt.ItemDataRole.UserRole + 1


class _VersionDelegate(QStyledItemDelegate):
    """Paints a version as two lines: label and right-aligned size, then the
    date, an "auto" tag when the program took it and a paragraph mark when it
    has a description. The Current row (no row data) is the stock text row
    with a separator under it."""

    _PAD = 5

    def sizeHint(self, option: QStyleOptionViewItem, index: Any) -> QSize:  # noqa: N802
        data = index.data(_ROW_ROLE)
        if data is None:
            base = super().sizeHint(option, index)
            return QSize(base.width(), base.height() + 2 * self._PAD)
        height = option.fontMetrics.height() * 2 + 2 * self._PAD + 2
        return QSize(option.rect.width(), height)

    def paint(self, painter: QPainter, option: QStyleOptionViewItem,
              index: Any) -> None:
        data = index.data(_ROW_ROLE)
        if data is None:
            super().paint(painter, option, index)
            painter.save()
            rule = option.palette.color(
                option.palette.ColorRole.PlaceholderText)
            rule.setAlpha(110)
            painter.setPen(QPen(rule, 1))
            y = option.rect.bottom()
            painter.drawLine(option.rect.left(), y, option.rect.right(), y)
            painter.restore()
            return
        opt = QStyleOptionViewItem(option)
        self.initStyleOption(opt, index)
        opt.text = ""
        widget = opt.widget
        style = widget.style() if widget is not None else QApplication.style()
        style.drawControl(QStyle.ControlElement.CE_ItemViewItem, opt, painter,
                          widget)
        selected = bool(opt.state & QStyle.StateFlag.State_Selected)
        group = opt.palette.currentColorGroup()
        strong = opt.palette.color(
            group, opt.palette.ColorRole.HighlightedText if selected
            else opt.palette.ColorRole.Text)
        muted = opt.palette.color(
            group, opt.palette.ColorRole.HighlightedText if selected
            else opt.palette.ColorRole.PlaceholderText)
        if selected:
            muted.setAlpha(190)
        self._paint_texts(painter, opt, data, strong, muted)

    def _paint_texts(self, painter: QPainter, opt: QStyleOptionViewItem,
                     data: dict[str, Any], strong: Any, muted: Any) -> None:
        painter.save()
        fm = opt.fontMetrics
        box = opt.rect.adjusted(8, self._PAD, -8, -self._PAD)
        line = fm.height()
        size_w = fm.horizontalAdvance(data["size"])
        painter.setPen(muted)
        painter.drawText(QRect(box.right() - size_w, box.top(), size_w, line),
                         int(Qt.AlignmentFlag.AlignRight
                             | Qt.AlignmentFlag.AlignVCenter), data["size"])
        right = box.right() - size_w - 8
        if data["has_description"]:
            mark_w = fm.horizontalAdvance("¶")
            painter.drawText(QRect(right - mark_w, box.top(), mark_w, line),
                             int(Qt.AlignmentFlag.AlignCenter), "¶")
            right -= mark_w + 6
        font = QFont(opt.font)
        font.setBold(bool(data["label"]))
        font.setItalic(not data["label"])
        painter.setFont(font)
        painter.setPen(strong if data["label"] else muted)
        label = data["label"] or i18n.tr("(no label)")
        painter.drawText(
            QRect(box.left(), box.top(), max(10, right - box.left()), line),
            int(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter),
            fm.elidedText(label, Qt.TextElideMode.ElideRight,
                          max(10, right - box.left())))
        painter.setFont(opt.font)
        painter.setPen(muted)
        top2 = box.top() + line + 2
        when_w = fm.horizontalAdvance(data["when"])
        painter.drawText(QRect(box.left(), top2, when_w + 4, line),
                         int(Qt.AlignmentFlag.AlignLeft
                             | Qt.AlignmentFlag.AlignVCenter), data["when"])
        if data["auto"]:
            tag = i18n.tr("auto")
            tag_w = fm.horizontalAdvance(tag) + 10
            tag_rect = QRect(box.left() + when_w + 12, top2 + 1, tag_w,
                             line - 2)
            painter.setPen(QPen(muted, 1))
            painter.drawRoundedRect(tag_rect, 3, 3)
            painter.drawText(tag_rect, int(Qt.AlignmentFlag.AlignCenter), tag)
        painter.restore()


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
                 current_state: Callable[[], CurrentState | None] | None = None,
                 start_save: bool = False):
        super().__init__(parent)
        #: The error from reading the history index, shown in the dialog.
        self._index_error = ""
        self._start_save = start_save
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
        self.setWindowTitle(i18n.tr("Version History — {name}").format(
            name=Path(file).name))
        self.setMinimumSize(860, 520)
        self._build()
        self.reload()
        if start_save:
            # The "Save Version Now" command: go straight to the save.
            QTimer.singleShot(0, self._snapshot_now)

    def _build(self) -> None:
        lay = QVBoxLayout(self)
        lay.setSpacing(8)
        intro = QLabel(
            "Versions are snapshots you save yourself; saving the model "
            "does not add one.")
        intro.setToolTip(
            "Versions live in your private application folder, not beside "
            "the model.")
        intro.setWordWrap(True)
        lay.addWidget(intro)

        body = QHBoxLayout()
        body.setSpacing(10)
        self._list = QListWidget()
        self._list.setSelectionMode(
            QAbstractItemView.SelectionMode.SingleSelection)
        self._list.setItemDelegate(_VersionDelegate(self._list))
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
        # Enter must only ever close: no action button (and not the
        # description's Edit) may be the default.
        for button in self.findChildren(QPushButton):
            button.setAutoDefault(False)
            button.setDefault(False)
        close = box.button(QDialogButtonBox.StandardButton.Close)
        close.setAutoDefault(True)
        close.setDefault(True)
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
        # Exception text lands here: never interpret it as rich text.
        self._note.setTextFormat(Qt.TextFormat.PlainText)
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
        self._empty_hint = QLabel(self._list.viewport())
        self._empty_hint.setWordWrap(True)
        self._empty_hint.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._empty_hint.setObjectName("SectionNote")
        self._empty_hint.setText(i18n.tr(
            "No versions yet. Save version now keeps the current state."))
        self._empty_hint.hide()
        self._list.viewport().installEventFilter(self)
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

    def eventFilter(self, obj: Any, event: Any) -> bool:  # noqa: N802
        if obj is self._list.viewport() and event.type() == QEvent.Type.Resize:
            self._place_empty_hint()
        return super().eventFilter(obj, event)

    def _place_empty_hint(self) -> None:
        """Centre the "no versions yet" hint in the space under Current."""
        show = not self._snapshots and not self._index_error
        self._empty_hint.setVisible(show)
        if not show or self._list.count() == 0:
            return
        view = self._list.viewport()
        top = self._list.visualItemRect(self._list.item(0)).bottom() + 12
        self._empty_hint.setGeometry(
            16, top, max(40, view.width() - 32), max(40, view.height() - top))

    def showEvent(self, event: Any) -> None:  # noqa: N802
        super().showEvent(event)
        self._place_empty_hint()

    def reload(self) -> None:
        selected = self._selected_id()
        self._index_error = ""
        try:
            self._snapshots = self._store.snapshots(self.file)
        except HistoryError as exc:
            self._snapshots = []
            self._index_error = str(exc)
        self._refresh_state()
        self._list.blockSignals(True)
        self._list.clear()
        bold = QFont(self._list.font())
        bold.setBold(True)
        head = QListWidgetItem(self._current_text())
        head.setData(Qt.ItemDataRole.UserRole, None)
        head.setFont(bold)
        self._list.addItem(head)
        # Seconds only where two versions would otherwise look identical.
        minutes: dict[str, int] = {}
        for shot in self._snapshots:
            key = shot.time_text(seconds=False)
            minutes[key] = minutes.get(key, 0) + 1
        for shot in self._snapshots:
            when = shot.time_text(seconds=minutes[
                shot.time_text(seconds=False)] > 1)
            item = QListWidgetItem(
                f"{shot.label or i18n.tr('(no label)')} · {when} · "
                f"{shot.size_text()}")
            item.setData(Qt.ItemDataRole.UserRole, shot.id)
            item.setData(_ROW_ROLE, {
                "label": shot.label, "when": when, "size": shot.size_text(),
                "auto": shot.is_automatic(),
                "has_description": bool(shot.description)})
            tip = f"{shot.time_text()} · {shot.size_text()} · " \
                  f"{shot.sha256[:16]}"
            if shot.description:
                first = next((ln.strip() for ln in
                              shot.description.splitlines() if ln.strip()), "")
                tip += "\n¶ " + first[:120]
            item.setToolTip(tip)
            self._list.addItem(item)
        self._show_usage()
        row = next((i + 1 for i, s in enumerate(self._snapshots)
                    if s.id == selected), 0)
        # One selection, one refresh: the comparison with the newest
        # version is not cheap, so it must not run for each signal.
        self._list.setCurrentRow(row)
        self._list.blockSignals(False)
        self._place_empty_hint()
        self._show_selected(row)

    def _show_usage(self) -> None:
        try:
            total = self._store.usage(self.file)
        except HistoryError:
            total = 0
        count = len(self._snapshots)
        text = (i18n.tr("History size: %1 for %2 version. Identical files "
                        "are stored once.") if count == 1 else
                i18n.tr("History size: %1 for %2 versions. Identical files "
                        "are stored once."))
        self._total.setText(text.replace("%1", format_bytes(total))
                            .replace("%2", str(count)))
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
        if self._index_error:
            text = i18n.tr("Current — the version list could not be read")
        elif self._current_matches:
            newest = self._snapshots[0]
            text = i18n.tr("Current — same as the version saved %1").replace(
                "%1", newest.time_text())
            if newest.label:
                text += f" ({newest.label})"
        elif not self._snapshots:
            text = i18n.tr("Current — no versions yet. Use Save version now "
                           "to keep this state.")
        else:
            text = i18n.tr("Current — not saved as a version")
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
        if self._index_error:
            self._preview.setPlainText(self._index_error)
            return
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
                "%1", _connectivity_text(diff["summary"]["connectivity"])))
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
            self, i18n.tr("Unsaved Description"),
            i18n.tr("The description of this version has unsaved changes."
                    "\n\nSave them?"), yes_text=i18n.tr("Save description"),
            no_text=i18n.tr("Discard changes"), default_yes=True)

    def _save_description(self, snapshot_id: str, text: str) -> bool:
        ok = self._store.set_description(self.file, snapshot_id, text)
        if not ok:
            self._note.setText(i18n.tr("That version no longer exists; the "
                                      "description was not saved."))
        return ok

    def _after_description_saved(self) -> None:
        self._note.setText(i18n.tr("Description saved."))
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
        auto = f" · {html.escape(i18n.tr('auto'))}" if shot.is_automatic() \
            else ""
        self._details.setText(
            (f"<b>{html.escape(shot.label)}</b><br>" if shot.label else "")
            + f"{shot.time_text()} · {shot.size_text()}{auto}")
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
                "%1", _connectivity_text(diff["summary"]["connectivity"])))
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
        from raptor.gui.ui.save_progress import save_progress_sinks
        host = self.parent()
        sinks = save_progress_sinks(host) if host is not None else None
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
                self, i18n.tr("Delete Version"),
                i18n.tr("Delete the version from %1?\n\nThe model file is "
                        "not changed.").replace("%1", shot.time_text()),
                yes_text=i18n.tr("Delete version"),
                no_text=i18n.tr("Keep")):
            return
        self._store.discard(self.file, shot.id)
        self.reload()

    def _selected_snapshot(self) -> Snapshot | None:
        row = self._list.currentRow() - 1        # row 0 is Current
        return self._snapshots[row] if 0 <= row < len(self._snapshots) else None


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
        bits.append((i18n.tr("{n} run") if runs == 1
                     else i18n.tr("{n} runs")).format(n=f"{runs:,}"))
    if isinstance(when, str) and when:
        bits.append(i18n.tr("run {when}").format(
            when=when.replace("T", " ")[:16]))
    return ": ".join([bits[0], ", ".join(bits[1:])]) if len(bits) > 1 \
        else bits[0] + "."


def _connectivity_text(text: Any) -> str:
    return (i18n.tr("same wiring") if str(text) == "same wiring"
            else str(text))


def _diff_title(group: str) -> str:
    """The comparison's group heading in the GUI language (literal tr()
    calls, so the catalogue scan sees each one)."""
    titles = {
        "elements": i18n.tr("Elements"), "links": i18n.tr("Connections"),
        "structure": i18n.tr("Structure"),
        "system": i18n.tr("System settings"), "rates": i18n.tr("Rates"),
        "spare_pools": i18n.tr("Spare pools"),
        "resources": i18n.tr("Resources"), "triggers": i18n.tr("Triggers"),
        "phases": i18n.tr("Phases"), "project": i18n.tr("Project")}
    return titles.get(group, group)


def _diff_word(text: str) -> str:
    """The markers the comparison puts in a cell, in the GUI language."""
    words = {"(added)": i18n.tr("(added)"), "(removed)": i18n.tr("(removed)"),
             "(renamed)": i18n.tr("(renamed)"),
             "present": i18n.tr("present")}
    return words.get(text, text)


def _diff_lines(diff: dict[str, Any]) -> list[str]:
    """One aligned table per changed group, headed in plain words."""
    from raptor.api.model_diff import GROUPS, flatten
    from raptor.cli.cmd_tools import _table_text
    rows = flatten(diff)
    out: list[str] = []
    for group in GROUPS:
        mine = [r for r in rows if r["group"] == group]
        if not mine:
            continue
        body = [[f"{r['kind']} {r['path']}".strip(), _diff_word(r["field"]),
                 _diff_word(r["a"]) if r["a"] else "—",
                 _diff_word(r["b"]) if r["b"] else "—"] for r in mine]
        out += ["", f"{_diff_title(group)}:",
                _table_text([i18n.tr("Element"), i18n.tr("Field"), i18n.tr("This version"),
                             i18n.tr("Open now")],
                            body)]
    return out
