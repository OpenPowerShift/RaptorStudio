"""Simulate ▸ Trials to Precision: how many trials the answer needs (plan 9.2).

States a precision instead of a run count. Runs the model on a copy in
growing batches with the same seed (`raptor.api.scenarios.run_to_precision`,
Law's sequential relative-precision rule) until the confidence interval's
half-width is within the target fraction of the watched value, and lists
each pass. “Use these trials” writes the trial count it reached into the experiment, as
one undo step, for the next Run.

For availability, reliability and delivered fraction the rule watches the
complement (unavailability): a 10 % precision on 99.9 % availability is
then a tenth of the 0.1 % that is lost, not of the 99.9 %.
"""
from __future__ import annotations
from raptor.api.insight.axes import pct

from typing import Any

from PySide6.QtWidgets import (
    QAbstractItemView, QComboBox, QDoubleSpinBox, QHBoxLayout, QHeaderView, QLabel,
    QLayout, QSpinBox, QTableWidget, QTableWidgetItem, QWidget,
)

from raptor.gui import cores
from raptor.gui.ui.analysis_dialog import AnalysisDialog, Work

#: (metric, label, watch the complement?)
METRICS = (("system.ao", "Availability", True), ("system.r", "Reliability", True),
           ("system.capacity", "Delivered fraction", True),
           ("system.mtbde", "Time between outages", False),
           ("system.mdt", "Mean downtime", False))
COLUMNS = ("Trials", "Estimate", "± half-width", "Relative width")


def _watched(metric: str, complement: bool) -> str:
    label = next(lbl for key, lbl, _c in METRICS if key == metric)
    if not complement:
        return label.lower()
    return "unavailability" if metric == "system.ao" else f"1 − {label.lower()}"


class PrecisionDialog(AnalysisDialog):
    TITLE = "Trials to Precision"
    INTRO = ("How many trials the answer needs. State a precision instead of a "
             "trial count: the model runs on a copy in growing batches with the "
             "same seed until the confidence interval is within the target. "
             "For availability the target is on the part that is lost "
             "(unavailability). “Use these trials” sets the trials for "
             "the next Run.")
    FIRST_LABEL = "Running the first batch"
    SIZE = (760, 520)

    def build_inputs(self) -> QLayout:
        self._metric = QComboBox()
        for key, label, _complement in METRICS:
            self._metric.addItem(label, key)
        self._target = QDoubleSpinBox()
        self._target.setRange(0.5, 50.0)
        self._target.setDecimals(1)
        self._target.setSuffix(" %")
        self._target.setValue(10.0)
        self._target.setToolTip("Half-width of the confidence interval, as a "
                                "share of the watched value")
        self._confidence = QComboBox()
        for level in (0.90, 0.95, 0.99):
            self._confidence.addItem(f"{100 * level:.0f} %", level)
        self._confidence.setCurrentIndex(1)
        self._max = QSpinBox()
        self._max.setRange(10, 10_000_000)
        self._max.setGroupSeparatorShown(True)
        self._max.setValue(20_000)
        self._max.setToolTip("Stop here even if the precision is not reached")
        row = QHBoxLayout()
        for label, widget in (("Watch", self._metric), ("Within", self._target),
                              ("Confidence", self._confidence),
                              ("At most", self._max)):
            row.addWidget(QLabel(label))
            row.addWidget(widget)
            row.addSpacing(10)
        row.addStretch(1)
        return row

    def build_result_area(self) -> QWidget:
        self.table = QTableWidget(0, len(COLUMNS))
        self.table.setHorizontalHeaderLabels(COLUMNS)
        self.table.verticalHeader().setVisible(False)
        self.table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.table.setSelectionMode(QAbstractItemView.SelectionMode.NoSelection)
        self.table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Stretch)
        return self.table

    def _complement(self) -> bool:
        return next(c for key, _l, c in METRICS if key == self._metric.currentData())

    def make_work(self) -> Work | str:
        from raptor.api.scenarios import Precision
        precision = Precision(
            metric=self._metric.currentData(),
            on="unavailability" if self._complement() else "value",
            relative=self._target.value() / 100.0,
            confidence=float(self._confidence.currentData()),
            min_trials=min(50, self._max.value()), max_trials=self._max.value())
        try:
            precision.field()
        except ValueError as exc:
            return str(exc)
        workers = cores.chosen()

        def work(ws, mid, progress, abort):
            from raptor.api.scenarios import run_to_precision
            return run_to_precision(ws, mid, precision, workers=workers,
                                    progress=progress, abort=abort,
                                    timeout_s=24 * 3600.0, confirm_long_run=True)
        return work

    def show_result(self, result: Any) -> None:
        history = result.history
        watched = _watched(result.precision.metric, result.precision.on == "unavailability")
        self.table.setHorizontalHeaderItem(1, QTableWidgetItem(watched.capitalize()))
        self.table.setRowCount(len(history))
        for index, step in enumerate(history):
            texts = (f"{step['trials']:,}",
                     "—" if step["mean"] is None else f"{step['mean']:.5g}",
                     "—" if step["half_width"] is None else f"{step['half_width']:.3g}",
                     "—" if step["relative"] is None else f"{pct(step['relative'])}")
            for column, text in enumerate(texts):
                self.table.setItem(index, column, QTableWidgetItem(text))
        self.caption.setText(self._summary(result))

    def _summary(self, result: Any) -> str:
        watched = _watched(result.precision.metric, result.precision.on == "unavailability")
        reached = {
            "precision": "reached the target",
            "max trials": "did NOT reach the target within the trial limit",
            "cancelled": "was stopped before reaching the target",
            "no data": "had no values to measure",
        }.get(result.stopped, result.stopped)
        estimate = ("" if result.mean is None else
                    f": {watched} {result.mean:.5g}"
                    + (f" ± {result.half_width:.3g}" if result.half_width is not None else ""))
        width = ("" if result.relative_half_width is None else
                 f" (relative width {pct(result.relative_half_width)})")
        return (f"{result.trials:,} trials {reached}{estimate}{width}, "
                f"{100 * result.precision.confidence:.0f} % confidence. "
                "“Use these trials” sets the next Run to this many trials; "
                "that run measures its own interval.")

    def selection(self) -> tuple[Any, str, list[str]] | None:
        result = self.result
        if result is None or self.running() or result.stopped == "no data":
            return None
        return result.trials, f"{result.trials:,} trials", [f"system.runs={result.trials}"]

    def write(self, changes: list[str], label: str) -> str | None:
        # trials have one writer, which keeps the experiment and the legacy
        # SYSTEM_DATA column in step (and is one undo step)
        trials = int(changes[0].split("=", 1)[1])
        win = self._win
        if win._rbd is None:
            return "no model is open"
        if int(win._current_experiment().get("trials", 0)) == trials:
            return "the model already runs this many trials"
        win._set_exp_field("trials", trials)
        return None

    def apply_label(self) -> str:
        return "Use these trials"

    def result_text(self) -> str:
        result = self.result
        if result is None:
            return ""
        lines = ["\t".join(COLUMNS)]
        for step in result.history:
            lines.append("\t".join(str(step[k]) for k in
                                   ("trials", "mean", "half_width", "relative")))
        return "\n".join([self._summary(result), "", *lines])


def open_precision(win: Any) -> PrecisionDialog:
    from raptor.gui.ui.analysis_dialog import open_analysis
    dlg = open_analysis(win, PrecisionDialog, "_precision_dialog")
    assert isinstance(dlg, PrecisionDialog)
    return dlg
