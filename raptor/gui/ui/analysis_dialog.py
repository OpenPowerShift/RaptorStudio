"""The frame every Analyse window shares (plan sections 8 and 9).

What If, Sensitivity and the three optimisers all run a long API call on a
copy of the model, report progress, can be stopped, show a result, and can
write a recommended change into the open model as one undo step. This module
holds that once:

- `AnalysisJob` runs `work(workspace, model_id, progress, abort)` in a thread
  on a private workspace holding a copy of the model; the dialog polls it.
- `AnalysisDialog` is the window: an intro, the subclass's inputs, its result
  area, a caption, a progress bar and Run / Stop / Apply / Copy / Close.
- `apply_changes` writes PATH=VALUE changes into the open model as one undo
  step (the same path Recommend's Apply takes).
"""
from __future__ import annotations

import logging
import tempfile
import threading
from collections.abc import Callable
from typing import Any

from PySide6.QtCore import Qt, QTimer
from PySide6.QtGui import QFont, QFontMetrics
from PySide6.QtWidgets import (
    QApplication, QDialog, QHBoxLayout, QLabel, QLayout, QProgressBar, QPushButton,
    QSpinBox, QVBoxLayout, QWidget,
)

log = logging.getLogger(__name__)

#: work(workspace, model_id, progress, abort) -> result
Work = Callable[[Any, str, Callable[[int, int, str], None], threading.Event], Any]


class AnalysisJob:
    """Runs one analysis in a thread on a copy of the model. Plain data only:
    the dialog reads `done_count`, `total`, `label`, `result` and `error`."""

    def __init__(self, rbd: Any, work: Work, *, first_label: str = "Starting") -> None:
        self._rbd = rbd
        self._work_fn = work
        self.result: Any = None
        self.error: str | None = None
        self.done_count, self.total, self.label = 0, 1, first_label
        self.done = False
        self.abort = threading.Event()
        self._tmp = tempfile.TemporaryDirectory(prefix="raptor-analysis-")
        self._thread = threading.Thread(target=self._work, daemon=True)

    def start(self) -> None:
        self._thread.start()

    def stop(self) -> None:
        self.abort.set()

    def is_running(self) -> bool:
        return self._thread.is_alive()

    def wait(self, timeout: float | None = None) -> None:
        self._thread.join(timeout)

    def _progress(self, done: int, total: int, label: str) -> None:
        self.done_count, self.total = done, max(1, total)
        if label:
            self.label = label

    def _work(self) -> None:
        from raptor.api.workspace import Workspace
        try:
            ws = Workspace(self._tmp.name)
            mid = ws.register_model(self._rbd, name="analysis")["model_id"]
            self.result = self._work_fn(ws, mid, self._progress, self.abort)
        except Exception as exc:                          # noqa: BLE001
            log.warning("analysis failed", exc_info=True)
            # the message is for the user; the exception's class is in the log
            self.error = ((str(exc) or type(exc).__name__)
                          if not self.abort.is_set() else "Stopped.")
        finally:
            self.done = True

    def cleanup(self) -> None:
        if self.is_running():
            self.abort.set()
            self._thread.join(timeout=30)
        try:
            self._tmp.cleanup()
        except OSError:
            pass


def apply_changes(win: Any, changes: list[str], label: str) -> str | None:
    """Write PATH=VALUE `changes` into the open model as one undo step.

    Returns None when applied, else why not (to show the user)."""
    from raptor.api.paths import PathError, cell_changes
    from raptor.gui.ui.diagram_commands import BlockCellsCmd
    view = win._diagram_dock.view
    win._model_for_engine()          # canvas edits into the rows first
    rbd = view._rbd_ref
    if rbd is None:
        return "no model is open"
    try:
        cells = cell_changes(rbd, list(changes))
    except (PathError, ValueError) as exc:
        return str(exc)
    edits = [{"row": rbd.sections[c["section"]].rows[c["index"]],
              "column": c["column"], "old": c["old"], "new": c["new"]}
             for c in cells]
    if not edits:
        return "it is already in the model"
    view._stack.push(BlockCellsCmd(view, edits, f"Apply: {label}"))
    win._statusbar.showMessage(f"Applied: {label}", 5000)
    return None


def trials_box(win: Any, tooltip: str) -> QSpinBox:
    """A trials spin box starting at the model's own run count."""
    from raptor.gui.ui.batch_dialogs import _trials
    box = QSpinBox()
    box.setRange(2, 10_000_000)
    box.setGroupSeparatorShown(True)
    box.setValue(max(2, _trials(win._rbd)) if win._rbd is not None else 100)
    box.setToolTip(tooltip)
    return box


def study_seed(win) -> int | None:
    """The seed a study (What If, Sensitivity, Recommend, the optimisers)
    uses, from Run panel ▸ Seed (#292): None for Raptor 7's fixed table.
    Drawn ONCE per study, so the base and every variant share it and the
    comparison stays paired."""
    from raptor.api.run import resolve_seed
    try:
        experiment = win._current_experiment()
    except Exception:  # noqa: BLE001 - no model: the fixed table
        return None
    return resolve_seed(experiment) or None


class AnalysisDialog(QDialog):
    """Base window. A subclass sets TITLE and INTRO and implements
    `build_inputs`, `build_result_area`, `make_work`, `show_result`,
    `selection` and `result_text`."""

    TITLE = "Analysis"
    INTRO = ""
    FIRST_LABEL = "Starting"
    SIZE = (980, 520)
    #: Whether a stopped job's partial result may be applied (the precision
    #: search keeps its last whole pass, which is a sound answer).
    apply_partial = False

    def __init__(self, win: Any) -> None:
        super().__init__(win)
        self._win = win
        self._job: AnalysisJob | None = None
        self._model_key: tuple = ()
        self._applied: set[Any] = set()
        self.setWindowTitle(self.TITLE)
        self.setModal(False)
        from raptor.gui.ui.modality import fit_dialog
        fit_dialog(self, *self.SIZE)
        self._timer = QTimer(self)
        self._timer.setInterval(150)
        self._timer.timeout.connect(self._poll)

        intro = QLabel(self.INTRO)
        intro.setWordWrap(True)
        self._inputs = self.build_inputs()
        self.caption = QLabel()
        self.caption.setWordWrap(True)
        self.caption.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        self._progress = QProgressBar()
        self._progress.setVisible(False)
        layout = QVBoxLayout(self)
        layout.addWidget(intro)
        layout.addLayout(self._inputs)
        layout.addWidget(self.build_result_area(), 1)
        layout.addWidget(self.caption)
        layout.addWidget(self._progress)
        # U20: the model is not frozen while this runs; say so, and say
        # when it changed since, so a result is never read as current.
        self._copy_note = QLabel(
            "This runs on a copy of the model taken when you pressed Run. You can "
            "keep editing; changes made now are not in this result.")
        self._copy_note.setObjectName("SectionNote")
        self._copy_note.setWordWrap(True)
        self._copy_note.setVisible(False)
        layout.addWidget(self._copy_note)
        self._revision: int | None = None
        self._eta: Any = None
        layout.addLayout(self._build_buttons())

    # ---- for subclasses ----------------------------------------------------------

    def build_inputs(self) -> QLayout:
        return QHBoxLayout()

    def build_result_area(self) -> QWidget:
        raise NotImplementedError

    def make_work(self) -> Work | str:
        """The work to run, or a message saying why it cannot run."""
        raise NotImplementedError

    def show_result(self, result: Any) -> None:
        raise NotImplementedError

    def selection(self) -> tuple[Any, str, list[str]] | None:
        """(key, label, changes) of what Apply would write, if anything."""
        return None

    def result_text(self) -> str:
        return ""

    def apply_label(self) -> str:
        return "Apply"

    def write(self, changes: list[str], label: str) -> str | None:
        """Write `changes` into the open model; None, or why not."""
        return apply_changes(self._win, changes, label)

    # ---- frame -------------------------------------------------------------------

    def _build_buttons(self) -> QHBoxLayout:
        self._run_btn = QPushButton("Run")
        self._run_btn.setDefault(True)
        self._run_btn.clicked.connect(self.start)
        self._stop_btn = QPushButton("Stop")
        self._stop_btn.setEnabled(False)
        self._stop_btn.clicked.connect(self._stop)
        self._apply_btn = QPushButton(self.apply_label())
        self._apply_btn.setToolTip("Write the selected change into the model (one undo step)")
        self._apply_btn.setEnabled(False)
        self._apply_btn.clicked.connect(self.apply_selected)
        self._copy_btn = QPushButton("Copy")
        self._copy_btn.setToolTip("Copy the results as text")
        self._copy_btn.setEnabled(False)
        self._copy_btn.clicked.connect(self._copy)
        close = QPushButton("Close")
        close.clicked.connect(self.close)
        # the default button is drawn bold: size both for that, or the
        # label is clipped when the default moves to it
        bold = QFont(self.font())
        bold.setBold(True)
        metrics = QFontMetrics(bold)
        for button in (self._run_btn, self._apply_btn):
            button.setMinimumWidth(metrics.horizontalAdvance(button.text()) + 32)
        # Copy on its own at the left; on the right the order of the work:
        # Run (Stop), then the button that writes the answer into the model,
        # then Close. The write button becomes the default once it can act.
        buttons = QHBoxLayout()
        buttons.addWidget(self._copy_btn)
        buttons.addStretch(1)
        buttons.addWidget(self._run_btn)
        buttons.addWidget(self._stop_btn)
        buttons.addSpacing(12)
        buttons.addWidget(self._apply_btn)
        buttons.addWidget(close)
        return buttons

    @property
    def result(self) -> Any:
        return self._job.result if self._job is not None else None

    def running(self) -> bool:
        # The job's own flag, not its thread: the thread is still alive for
        # a moment after it sets `done`, and a check in that moment left
        # Apply greyed out after a finished run (seen on a slow CI runner).
        return self._job is not None and not self._job.done

    def _set_inputs_enabled(self, on: bool) -> None:
        for index in range(self._inputs.count()):
            item = self._inputs.itemAt(index)
            widget = item.widget() if item is not None else None
            if widget is not None:
                widget.setEnabled(on)

    def start(self) -> bool:
        if self.running():
            return False
        if self._win._rbd is None:
            self.caption.setText("Open a model first.")
            return False
        work = self.make_work()
        if isinstance(work, str):
            self.caption.setText(work)
            return False
        from raptor.gui.ui.batch_dialogs import _model_copy
        if self._job is not None:
            self._job.cleanup()
        self._applied.clear()
        self._job = AnalysisJob(_model_copy(self._win), work, first_label=self.FIRST_LABEL)
        self._revision = getattr(self._win, "_model_revision", 0)
        from raptor.gui.ui.run_eta import RunEstimate
        self._eta = RunEstimate()       # U18: the main run's "about N left"
        self._copy_note.setText(
            "This runs on a copy of the model taken when you pressed Run. You can "
            "keep editing; changes made now are not in this result.")
        self._copy_note.setVisible(True)
        self.caption.setText(f"{self.FIRST_LABEL}…")
        self._progress.setRange(0, 0)
        self._progress.setFormat(f"{self.FIRST_LABEL}…")
        self._progress.setVisible(True)
        self._run_btn.setEnabled(False)
        self._stop_btn.setEnabled(True)
        self._set_inputs_enabled(False)
        self.sync_buttons()
        self._job.start()
        self._timer.start()
        return True

    def _stop(self) -> None:
        if self._job is not None:
            self._job.stop()
            self._stop_btn.setEnabled(False)
            self._progress.setFormat("Stopping…")

    def _poll(self) -> None:
        job = self._job
        if job is None:
            self._timer.stop()
            return
        if not job.done:
            if job.done_count:
                self._progress.setRange(0, job.total)
                self._progress.setValue(job.done_count)
            else:
                self._progress.setRange(0, 0)      # busy until the first trial ends
            if not job.abort.is_set():
                # The step's name is in the caption just below; the bar
                # says only how far, so the label is not read twice.
                self._progress.setFormat(f"{job.done_count:,} of {job.total:,}"
                                         if job.done_count else "")
                step = f"{job.label[:1].upper()}{job.label[1:]}…"
                eta = self._eta.text(job.done_count, job.total) if self._eta else None
                self.caption.setText(f"{step}  ·  {eta}" if eta else step)
            return
        self._timer.stop()
        self._run_btn.setEnabled(True)
        self._stop_btn.setEnabled(False)
        self._set_inputs_enabled(True)
        self._partial = False
        if job.error or job.result is None:
            self._progress.setVisible(False)
            self.caption.setText(job.error or ("Stopped before anything finished."
                                               if job.abort.is_set() else "Nothing ran."))
        else:
            self._partial = job.abort.is_set()
            self._progress.setRange(0, 1)
            self._progress.setValue(1)
            self._progress.setFormat("Stopped: partial results" if self._partial else "Done")
            self.show_result(job.result)
            if self.model_changed_since_run():
                self._copy_note.setText(
                    "The model was edited while this ran, so these results are for "
                    "the model as it was when you pressed Run. Run again for the "
                    "current model.")
            else:
                self._copy_note.setVisible(False)
            if self._partial:
                self.caption.setText(
                    "Stopped before the end: what is shown is only what finished"
                    + ("." if self.apply_partial else
                       ", so nothing here can be applied. Run again for a full answer.")
                    + "\n" + self.caption.text())
        self.sync_buttons()

    def model_changed_since_run(self) -> bool:
        """Whether the open model was edited after the last Run (applying a
        result from this window counts only after it is shown)."""
        return (self._revision is not None
                and getattr(self._win, "_model_revision", 0) != self._revision)

    def finish(self, timeout: float = 600.0) -> None:
        """Wait for the running job and show its result (for tests and scripts)."""
        if self._job is not None:
            self._job.wait(timeout)
            self._poll()

    def sync_buttons(self) -> None:
        chosen = self.selection()
        self._apply_btn.setEnabled(
            chosen is not None and bool(chosen[2]) and chosen[0] not in self._applied
            and not self.running()
            and (self.apply_partial or not getattr(self, "_partial", False)))
        self._copy_btn.setEnabled(self.result is not None and not self.running())
        # Enter does the next useful thing: run, or write the answer in
        can_apply = self._apply_btn.isEnabled()
        self._apply_btn.setDefault(can_apply)
        self._run_btn.setDefault(not can_apply)

    def apply_selected(self) -> bool:
        chosen = self.selection()
        if chosen is None:
            return False
        key, label, changes = chosen
        why = self.write(changes, label)
        if why is not None:
            self.caption.setText(f"Cannot apply {label}: {why}.")
            return False
        self._applied.add(key)
        self.sync_buttons()
        self.caption.setText(
            f"Applied {label} (Edit ▸ Undo takes it back). The results were "
            "measured on the model before this change: run again to measure "
            "from the new one.")
        return True

    def _copy(self) -> None:
        QApplication.clipboard().setText(self.result_text())

    def closeEvent(self, event):                          # noqa: N802
        if self.running() and self._job is not None:
            self._job.stop()
        self._timer.stop()
        super().closeEvent(event)

    def done(self, r):                                    # noqa: D401
        if self._job is not None:
            self._job.cleanup()
            self._poll()
        super().done(r)


def open_analysis(win: Any, cls: type[AnalysisDialog], attr: str) -> AnalysisDialog:
    """One window of `cls`, kept with its results until the model changes."""
    dlg = getattr(win, attr, None)
    key = (id(win._rbd), win._undo_stack.index(), win._current_file)
    if isinstance(dlg, cls):
        if not dlg.running() and dlg._model_key != key and not dlg._applied:
            dlg.close()
            dlg.deleteLater()
            dlg = None
    if not isinstance(dlg, cls):
        win._model_for_engine()
        dlg = cls(win)
        dlg._model_key = key
        setattr(win, attr, dlg)
    dlg.show()
    dlg.raise_()
    dlg.activateWindow()
    return dlg
