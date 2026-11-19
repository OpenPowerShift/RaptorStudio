"""Analyse ▸ What If ▸ Recommend: a change for each top downtime driver, tested.

The window runs `raptor.api.scenarios.recommend.recommend` on a copy of the
model in a thread (Base with attribution first, then every proposed change
against Base with paired runs) and lists each change with its measured gain,
its cost and a verdict.  Apply writes the selected change into the open
model as one undo step; nothing else touches the model.
"""
from __future__ import annotations

import tempfile
import threading
from typing import Any

from PySide6.QtCore import Qt, QTimer
from PySide6.QtWidgets import (
    QAbstractItemView, QApplication, QDialog, QDoubleSpinBox, QHBoxLayout, QHeaderView, QLabel,
    QProgressBar, QPushButton, QSpinBox, QTableWidget, QTableWidgetItem,
    QVBoxLayout, QWidget,
)

from raptor.gui import cores
from raptor.gui.ui.analysis_dialog import study_seed

COLUMNS = ("Change", "Measured effect", "Availability gain", "Downtime a run", "Cost")


class RecommendJob:
    """Runs `recommend` in a thread; the dialog polls it. Plain data only."""

    def __init__(self, rbd: Any, *, top: int, trials: int, workers: int,
                 min_gain: float | None = None,
                 seed: int | None = None) -> None:
        self._rbd = rbd
        self.seed = seed
        self.min_gain = min_gain
        self.top, self.trials, self.workers = top, trials, workers
        self.result: Any = None
        self.error: str | None = None
        self.done_count, self.total, self.label = 0, 1, "Finding the downtime drivers"
        self.done = False
        self.abort = threading.Event()
        self._tmp = tempfile.TemporaryDirectory(prefix="raptor-recommend-")
        self._thread = threading.Thread(target=self._work, daemon=True)

    def start(self) -> None:
        self._thread.start()

    def stop(self) -> None:
        self.abort.set()

    def is_running(self) -> bool:
        return self._thread.is_alive()

    def _progress(self, done: int, total: int, label: str) -> None:
        self.done_count, self.total = done, max(1, total)
        if label:
            self.label = label

    def _work(self) -> None:
        from raptor.api.scenarios.recommend import recommend
        from raptor.api.workspace import Workspace
        try:
            ws = Workspace(self._tmp.name)
            mid = ws.register_model(self._rbd, name="recommend")["model_id"]
            self.result = recommend(
                ws, mid, top=self.top, trials=self.trials, seed=self.seed,
                workers=self.workers, timeout_s=24 * 3600.0, min_gain=self.min_gain,
                progress=self._progress, abort=self.abort)
        except Exception as exc:                          # noqa: BLE001
            from raptor.api.batch import plain_error
            self.error = (plain_error(exc) if not self.abort.is_set() else "Stopped.")
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


def _gain_text(row: dict[str, Any]) -> str:
    gain = row["gain"]
    if gain["availability"] is None:
        return "—"
    chance = gain["p_better"]
    return (f"{100 * gain['availability']:+.3g} pts ± {100 * (gain['sem'] or 0):.2g}"
            f"  (P better {100 * (chance if chance is not None else 0.5):.0f} %)")


def _downtime_text(row: dict[str, Any], units: str) -> str:
    from raptor.api.scenarios.recommend import downtime_words
    avoided = row["gain"]["downtime_avoided"]
    return "—" if avoided is None else downtime_words(avoided, units)


def _cost_text(row: dict[str, Any]) -> str:
    cost = row["cost"]
    parts = []
    if cost["unit"] is not None:
        parts.append(f"{cost['unit']:,.6g} a unit")
    if cost["run_total_change"] is not None:
        parts.append(f"run cost {cost['run_total_change']:+,.6g}")
    return "; ".join(parts) or (cost["note"] or "unknown")


class RecommendDialog(QDialog):
    TITLE = "Recommend"

    def __init__(self, win: Any) -> None:
        super().__init__(win)
        self._win = win
        self._job: RecommendJob | None = None
        self._model_key: tuple = ()
        self._applied: set[int] = set()
        self.setWindowTitle("Recommend changes")
        self.setModal(False)
        self.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose, False)
        self.resize(980, 460)
        self._timer = QTimer(self)
        self._timer.setInterval(150)
        self._timer.timeout.connect(self._poll)

        intro = QLabel(
            "Finds the blocks that cause the most system downtime, proposes one "
            "change for each from the cause behind its outages, and tests every "
            "change against this model with paired runs. Runs on a copy: the "
            "model changes only when you press Apply.")
        intro.setWordWrap(True)
        inputs = self._build_inputs(win)

        self.table = self._build_table()
        self.caption = QLabel()
        self.caption.setWordWrap(True)
        self.caption.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        self._progress = QProgressBar()
        self._progress.setVisible(False)
        buttons = self._build_buttons()

        layout = QVBoxLayout(self)
        layout.addWidget(intro)
        layout.addLayout(inputs)
        layout.addWidget(self.table, 1)
        layout.addWidget(self.caption)
        layout.addWidget(self._progress)
        layout.addLayout(buttons)

    def _build_inputs(self, win: Any) -> QHBoxLayout:
        self._top = QSpinBox()
        self._top.setRange(1, 20)
        self._top.setValue(3)
        self._top.setToolTip("How many of the largest downtime drivers get a change")
        from raptor.gui.ui.batch_dialogs import _trials
        self._trials = QSpinBox()
        self._trials.setRange(2, 10_000_000)
        self._trials.setGroupSeparatorShown(True)
        self._trials.setValue(max(2, _trials(win._rbd)))
        self._trials.setToolTip("Trials for Base and for every change (paired)")
        self._min_gain = QDoubleSpinBox()
        self._min_gain.setRange(0.0, 100.0)
        self._min_gain.setDecimals(3)
        self._min_gain.setSingleStep(0.05)
        self._min_gain.setSuffix(" pts")
        self._min_gain.setSpecialValueText("5 % of downtime")
        self._min_gain.setToolTip(
            "Smallest availability gain called 'clearly helps'. The default is "
            "a change that removes 5 % of the model's downtime; a real gain "
            "below it is 'helps a little'. No verdict weighs cost.")
        inputs = QHBoxLayout()
        inputs.addWidget(QLabel("Top drivers"))
        inputs.addWidget(self._top)
        inputs.addSpacing(16)
        inputs.addWidget(QLabel("Trials"))
        inputs.addWidget(self._trials)
        inputs.addSpacing(16)
        inputs.addWidget(QLabel("Material gain"))
        inputs.addWidget(self._min_gain)
        inputs.addStretch(1)
        return inputs

    def _build_table(self) -> QTableWidget:
        table = QTableWidget(0, len(COLUMNS))
        table.setHorizontalHeaderLabels(COLUMNS)
        table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        table.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        table.setWordWrap(True)
        table.verticalHeader().setVisible(False)
        header = table.horizontalHeader()
        header.setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        for column in range(1, len(COLUMNS)):
            header.setSectionResizeMode(column, QHeaderView.ResizeMode.ResizeToContents)
        table.itemSelectionChanged.connect(self._sync_buttons)
        return table

    def _build_buttons(self) -> QHBoxLayout:
        self._run_btn = QPushButton("Run")
        self._run_btn.setDefault(True)
        self._run_btn.clicked.connect(self._start)
        self._stop_btn = QPushButton("Stop")
        self._stop_btn.setEnabled(False)
        self._stop_btn.clicked.connect(self._stop)
        self._apply_btn = QPushButton("Apply")
        self._apply_btn.setToolTip("Write the selected change into the model (one undo step)")
        self._apply_btn.setEnabled(False)
        self._apply_btn.clicked.connect(self.apply_selected)
        self._copy_btn = QPushButton("Copy")
        self._copy_btn.setToolTip("Copy the results as text")
        self._copy_btn.setEnabled(False)
        self._copy_btn.clicked.connect(self._copy)
        close = QPushButton("Close")
        close.clicked.connect(self.close)
        buttons = QHBoxLayout()
        buttons.addWidget(self._apply_btn)
        buttons.addWidget(self._copy_btn)
        buttons.addStretch(1)
        buttons.addWidget(self._run_btn)
        buttons.addWidget(self._stop_btn)
        buttons.addWidget(close)
        return buttons

    # ---- running -------------------------------------------------------------

    @property
    def result(self) -> Any:
        return self._job.result if self._job is not None else None

    def _start(self) -> None:
        if self._job is not None and self._job.is_running():
            return
        if self._win._rbd is None:
            self.caption.setText("Open a model first.")
            return
        from raptor.gui.ui.batch_dialogs import _model_copy
        if self._job is not None:
            self._job.cleanup()
        self._applied.clear()
        self._job = RecommendJob(_model_copy(self._win), top=self._top.value(),
                                 trials=self._trials.value(), workers=cores.chosen(),
                                 min_gain=(self._min_gain.value() / 100
                                           if self._min_gain.value() > 0 else None),
                                 seed=study_seed(self._win))
        self.table.setRowCount(0)
        self.caption.setText("Finding the downtime drivers…")
        self._progress.setRange(0, 0)
        self._progress.setFormat("Finding the downtime drivers…")
        self._progress.setVisible(True)
        self._run_btn.setEnabled(False)
        self._stop_btn.setEnabled(True)
        self._top.setEnabled(False)
        self._trials.setEnabled(False)
        self._min_gain.setEnabled(False)
        self._sync_buttons()
        self._job.start()
        self._timer.start()

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
            if not job.abort.is_set():
                self._progress.setFormat(
                    f"{job.done_count} of {job.total} — {job.label}")
                # a busy bar draws no text: say the step beside it too
                self.caption.setText(f"{job.label}…" if not job.done_count else
                                     f"Testing {job.done_count} of {job.total}: {job.label}")
            return
        self._timer.stop()
        self._run_btn.setEnabled(True)
        self._stop_btn.setEnabled(False)
        self._top.setEnabled(True)
        self._trials.setEnabled(True)
        self._min_gain.setEnabled(True)
        if job.error or job.result is None:
            self._progress.setVisible(False)
            self.caption.setText(job.error or "Nothing ran.")
        else:
            self._progress.setRange(0, 1)
            self._progress.setValue(1)
            self._progress.setFormat(f"Done: {len(job.result.rows)} change(s) tested, "
                                     f"{job.result.trials:,} paired trials each")
            self._show(job.result)
        self._sync_buttons()

    def _show(self, result: Any) -> None:
        units = str(result.base.get("units") or "time units")
        self.table.setRowCount(len(result.rows))
        for index, row in enumerate(result.rows):
            texts = (row["name"], row["verdict"], _gain_text(row),
                     _downtime_text(row, units), _cost_text(row))
            for column, text in enumerate(texts):
                item = QTableWidgetItem(text)
                item.setToolTip(f"{row['name']}\n{'; '.join(row['changes'])}\n\n"
                                f"{row['why']}\n{row.get('weigh') or row['cost']['note']}".strip())
                if column == 1 and row["verdict"] == "clearly helps":
                    font = item.font()
                    font.setBold(True)
                    item.setFont(font)
                self.table.setItem(index, column, item)
        self.table.resizeRowsToContents()
        self.caption.setText(f"{result.caption}\n{result.note}")
        if result.rows:
            self.table.selectRow(0)

    def _sync_buttons(self) -> None:
        result = self.result
        index = self.table.currentRow()
        usable = (result is not None and 0 <= index < len(result.rows)
                  and not result.rows[index].get("error")
                  and index not in self._applied
                  and not (self._job is not None and not self._job.done))
        self._apply_btn.setEnabled(bool(usable))
        self._copy_btn.setEnabled(result is not None and bool(result.rows))

    # ---- acting on a result -----------------------------------------------------

    def apply_selected(self) -> bool:
        """Write the selected change into the open model as one undo step."""
        result = self.result
        index = self.table.currentRow()
        if result is None or not 0 <= index < len(result.rows):
            return False
        row = result.rows[index]
        from raptor.gui.ui.analysis_dialog import apply_changes
        why = apply_changes(self._win, list(row["changes"]), row["name"])
        if why is not None:
            self.caption.setText(f"Cannot apply {row['name']}: {why}.")
            return False
        self._applied.add(index)
        self._sync_buttons()
        self.caption.setText(
            f"Applied {row['name']} (Edit ▸ Undo takes it back). The other "
            "results were measured against the model before this change: run "
            "again to test them against the new one.")
        return True

    def text(self) -> str:
        from raptor.api.scenarios.recommend import text_lines
        return "\n".join(text_lines(self.result)) if self.result is not None else ""

    def _copy(self) -> None:
        QApplication.clipboard().setText(self.text())

    def closeEvent(self, event):                          # noqa: N802
        if self._job is not None and self._job.is_running():
            self._job.stop()
        self._timer.stop()
        super().closeEvent(event)

    def done(self, r):                                    # noqa: D401
        if self._job is not None:
            self._job.cleanup()
            self._poll()
        super().done(r)


def open_recommend(win: Any) -> RecommendDialog:
    """One window, kept with its results until the model changes under it."""
    dlg: QWidget | None = getattr(win, "_recommend_dialog", None)
    key = (id(win._rbd), win._undo_stack.index(), win._current_file)
    if isinstance(dlg, RecommendDialog):
        running = dlg._job is not None and dlg._job.is_running()
        if not running and dlg._model_key != key and not dlg._applied:
            dlg.close()
            dlg.deleteLater()
            dlg = None
    if not isinstance(dlg, RecommendDialog):
        win._model_for_engine()
        dlg = RecommendDialog(win)
        dlg._model_key = key
        win._recommend_dialog = dlg
    dlg.show()
    dlg.raise_()
    dlg.activateWindow()
    return dlg
