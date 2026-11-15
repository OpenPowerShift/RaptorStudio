"""Analyse ▸ Sensitivity: the tornado (plan 8.3.4).

Varies each input the model offers (block life and repair means, PM
intervals, finite spare stock, crew counts) to a low and a high value, one at
a time, with paired runs (`raptor.api.scenarios.sensitivity.tornado`), and
draws each input's effect on the chosen metric as a bar either side of the
base value, widest swing first. A bar too close to zero to tell from no
effect is drawn hollow. Selecting an input and pressing Apply writes its
better end into the model as one undo step.
"""
from __future__ import annotations

from typing import Any

from PySide6.QtCore import QPointF, QRectF, Qt
from PySide6.QtGui import QColor, QPainter, QPen
from PySide6.QtWidgets import (
    QComboBox, QHBoxLayout, QLabel, QLayout, QSizePolicy, QSpinBox, QWidget,
)

from raptor.gui import cores
from raptor.gui.resources.design_tokens import active_colour
from raptor.gui.ui.analysis_dialog import AnalysisDialog, Work, trials_box

METRICS = (("availability", "Availability", True), ("mdt", "Mean downtime", False),
           ("mtbde", "Time between outages", False),
           ("capacity_weighted_availability", "Delivered fraction", True))
LOWER_IS_BETTER = {"mdt", "lost_flow", "grand_total"}


class TornadoChart(QWidget):
    """Horizontal bars of each input's low and high effect around zero."""

    ROW_H, LEFT, RIGHT, TOP, BOTTOM = 30, 210, 150, 28, 30

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.rows: list[dict[str, Any]] = []
        self.fraction = True
        self.selected = -1
        self.on_select: Any = None
        self.setMinimumHeight(180)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        self.setMouseTracking(True)

    def set_rows(self, rows: list[dict[str, Any]], fraction: bool) -> None:
        self.rows = [r for r in rows if r.get("swing") is not None]
        self.fraction = fraction
        self.selected = 0 if self.rows else -1
        self.setMinimumHeight(self.TOP + self.BOTTOM + self.ROW_H * max(3, len(self.rows)))
        self.update()

    def _fmt(self, value: float) -> str:
        return f"{100 * value:+.3g} pts" if self.fraction else f"{value:+.4g}"

    def _scale(self) -> float:
        widest = max((abs((r[end]["effect"] or 0.0)) + 2 * (r[end]["sem"] or 0.0)
                      for r in self.rows for end in ("low", "high")), default=0.0)
        return widest or 1.0

    def mousePressEvent(self, event):                     # noqa: N802
        index = int((event.position().y() - self.TOP) // self.ROW_H)
        if 0 <= index < len(self.rows):
            self.selected = index
            self.update()
            if self.on_select is not None:
                self.on_select(index)

    def paintEvent(self, _event):                         # noqa: N802
        c = active_colour()
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        p.fillRect(self.rect(), QColor(c["surface"]))
        if not self.rows:
            p.setPen(QColor(c["muted"]))
            p.drawText(self.rect(), Qt.AlignmentFlag.AlignCenter,
                       "Each input's effect appears here, widest first")
            p.end()
            return
        plot_w = max(40.0, self.width() - self.LEFT - self.RIGHT)
        mid = self.LEFT + plot_w / 2
        scale = (plot_w / 2) / self._scale()
        p.setPen(QColor(c["muted"]))
        p.drawText(QRectF(self.LEFT, 4, plot_w / 2 - 6, 18),
                   Qt.AlignmentFlag.AlignRight, "◄ worse")
        p.drawText(QRectF(mid + 6, 4, plot_w / 2, 18), Qt.AlignmentFlag.AlignLeft, "better ►")
        for index, row in enumerate(self.rows):
            y = self.TOP + index * self.ROW_H
            if index == self.selected:
                band = QColor(c["accent"])
                band.setAlpha(40)
                p.fillRect(QRectF(0, y, self.width(), self.ROW_H), band)
            p.setPen(QColor(c["text"]))
            p.drawText(QRectF(8, y, self.LEFT - 14, self.ROW_H),
                       Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter,
                       str(row["input"]["name"]))
            solid = bool(row.get("distinguishable"))
            for end, tone in (("low", c["result_red"]), ("high", c["accent"])):
                effect = row[end]["effect"]
                if effect is None:
                    continue
                better = -effect if row.get("_lower_better") else effect
                width = better * scale
                rect = QRectF(min(mid, mid + width), y + 6, abs(width), self.ROW_H - 12)
                colour = QColor(tone)
                p.setPen(QPen(colour, 1.2))
                if solid:
                    p.setBrush(colour)
                else:
                    p.setBrush(Qt.BrushStyle.NoBrush)
                p.drawRect(rect)
            p.setPen(QColor(c["muted"]))
            swing = self._fmt(row["swing"]).lstrip("+")
            p.drawText(QRectF(self.width() - self.RIGHT + 8, y, self.RIGHT - 14, self.ROW_H),
                       Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter,
                       f"swing {swing}")
        p.setPen(QPen(QColor(c["border"]), 1))
        bottom = self.TOP + len(self.rows) * self.ROW_H
        p.drawLine(QPointF(mid, self.TOP - 2), QPointF(mid, bottom))
        p.setPen(QColor(c["muted"]))
        p.drawText(QRectF(self.LEFT, bottom + 6, plot_w, 18), Qt.AlignmentFlag.AlignLeft,
                   "Red: input at its low value · blue: at its high value · hollow: "
                   "not distinguishable from no effect at this many runs")
        p.end()


class TornadoDialog(AnalysisDialog):
    TITLE = "Sensitivity"
    INTRO = ("Which inputs the result is most sensitive to. Each input (block "
             "life and repair, PM interval, spare stock, crew size) is moved to "
             "a low and a high value, one at a time, and run against this model "
             "with paired runs. One-at-a-time shows each input alone: it cannot "
             "show inputs that only matter together.")
    FIRST_LABEL = "Running Base"
    SIZE = (1040, 640)

    def build_inputs(self) -> QLayout:
        self._metric = QComboBox()
        for key, label, _fraction in METRICS:
            self._metric.addItem(label, key)
        self._top = QSpinBox()
        self._top.setRange(1, 60)
        self._top.setValue(10)
        self._top.setToolTip("How many inputs to vary (the largest downtime causes first)")
        self._trials = trials_box(self._win, "Trials for Base and for each end of every input")
        row = QHBoxLayout()
        for label, widget in (("Metric", self._metric), ("Inputs", self._top),
                              ("Trials", self._trials)):
            row.addWidget(QLabel(label))
            row.addWidget(widget)
            row.addSpacing(12)
        row.addStretch(1)
        return row

    def build_result_area(self) -> QWidget:
        from PySide6.QtWidgets import QScrollArea
        self.chart = TornadoChart()
        self.chart.on_select = lambda _i: self.sync_buttons()
        area = QScrollArea()
        area.setWidgetResizable(True)
        area.setWidget(self.chart)
        return area

    def make_work(self) -> Work | str:
        from raptor.api.scenarios.sensitivity import default_inputs
        inputs = default_inputs(self._win._rbd, limit=self._top.value())
        if not inputs:
            return "This model has no input that can be varied."
        metric, trials, workers = self._metric.currentData(), self._trials.value(), cores.chosen()

        def work(ws, mid, progress, abort):
            from raptor.api.scenarios.sensitivity import tornado
            return tornado(ws, mid, inputs, trials=trials, metric=metric, seed=1,
                           workers=workers, timeout_s=24 * 3600.0,
                           progress=progress, abort=abort)
        return work

    def show_result(self, result: Any) -> None:
        fraction = next((f for key, _l, f in METRICS if key == result["metric"]), False)
        lower = result["metric"] in LOWER_IS_BETTER
        rows = [{**row, "_lower_better": lower} for row in result["inputs"]]
        self.chart.set_rows(rows, fraction)
        self.caption.setText(f"{result['caption']} {result['note']}")

    def _chosen_row(self) -> dict[str, Any] | None:
        rows = self.chart.rows
        return rows[self.chart.selected] if 0 <= self.chart.selected < len(rows) else None

    def selection(self) -> tuple[Any, str, list[str]] | None:
        row = self._chosen_row()
        if row is None or self.running():
            return None
        low, high = row["low"]["effect"], row["high"]["effect"]
        if low is None or high is None:
            return None
        sign = -1 if row.get("_lower_better") else 1
        end = "high" if sign * high >= sign * low else "low"
        if sign * row[end]["effect"] <= 0:
            return None                     # neither end is an improvement
        change = row["input"][end]
        return (row["input"]["name"], end), f"{row['input']['name']} at its {end} value", [change]

    def apply_label(self) -> str:
        return "Apply the better end"

    def result_text(self) -> str:
        from raptor.api.scenarios.sensitivity import sensitivity_lines
        return "\n".join(sensitivity_lines(self.result)) if self.result is not None else ""


def open_tornado(win: Any) -> TornadoDialog:
    from raptor.gui.ui.analysis_dialog import open_analysis
    dlg = open_analysis(win, TornadoDialog, "_tornado_dialog")
    assert isinstance(dlg, TornadoDialog)
    return dlg
