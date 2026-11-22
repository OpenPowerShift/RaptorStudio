"""Analyse ▸ Optimise: spare stock, maintenance intervals and crew sizes
(plan 9.1.3, 9.2 and 9.5).

Each window runs one `raptor.api.optimise` search on a copy of the model,
with paired simulation, and lists what it measured. Apply writes the selected
recommendation's PATH=VALUE changes into the model as one undo step: the same
changes `raptor optimise ... --apply` and the MCP tools return.
"""
from __future__ import annotations

from collections.abc import Sequence
from typing import Any

from PySide6.QtCore import QPointF, QRectF, Qt
from PySide6.QtGui import QColor, QFont, QPainter, QPen
from PySide6.QtWidgets import (
    QAbstractItemView, QComboBox, QDoubleSpinBox, QHBoxLayout, QHeaderView, QLabel,
    QLayout, QSpinBox, QSplitter, QTableWidget, QTableWidgetItem, QWidget,
)

from raptor.gui import i18n
from raptor.gui import cores
from raptor.gui.resources.design_tokens import active_colour
from raptor.gui.ui.analysis_dialog import study_seed, AnalysisDialog, Work, trials_box


def _pct(value: float | None) -> str:
    return "—" if value is None else f"{100 * value:.4g} %"


def _table(columns: tuple[str, ...]) -> QTableWidget:
    table = QTableWidget(0, len(columns))
    table.setHorizontalHeaderLabels(i18n.header_labels(columns))
    table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
    table.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
    table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
    table.verticalHeader().setVisible(False)
    header = table.horizontalHeader()
    header.setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
    for column in range(1, len(columns)):
        header.setSectionResizeMode(column, QHeaderView.ResizeMode.ResizeToContents)
    return table


def _fill(table: QTableWidget, rows: Sequence[Sequence[Any]], bold: set[int],
          tips: list[str] | None = None) -> None:
    table.setRowCount(len(rows))
    for index, texts in enumerate(rows):
        for column, text in enumerate(texts):
            item = QTableWidgetItem(str(text))
            if index in bold:
                font = QFont(item.font())
                font.setBold(True)
                item.setFont(font)
            if tips:
                item.setToolTip(tips[index])
            table.setItem(index, column, item)


def _target_box(value: float = 99.5) -> QDoubleSpinBox:
    box = QDoubleSpinBox()
    box.setRange(0.0, 100.0)
    box.setDecimals(3)
    box.setSuffix(" %")
    box.setValue(value)
    box.setToolTip("Availability the recommendation must meet with 95 % confidence "
                   "(its lower bound must reach it)")
    return box


# ---- spares --------------------------------------------------------------------

class TradeOffChart(QWidget):
    """Availability against holding cost: each evaluated point with its
    confidence whisker, the target as a dashed line, the recommended point
    as a star and the current stock as a ring."""

    LEFT, RIGHT, TOP, BOTTOM = 70, 20, 14, 36

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.result: dict[str, Any] | None = None
        self.setMinimumSize(360, 200)

    def set_result(self, result: dict[str, Any]) -> None:
        self.result = result
        self.update()

    def paintEvent(self, _event):                         # noqa: N802
        c = active_colour()
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        p.fillRect(self.rect(), QColor(c["surface"]))
        plot = QRectF(self.LEFT, self.TOP, max(20, self.width() - self.LEFT - self.RIGHT),
                      max(20, self.height() - self.TOP - self.BOTTOM))
        p.setPen(QPen(QColor(c["border"]), 1))
        p.drawRect(plot)
        points = [pt for pt in (self.result or {}).get("points") or [] if pt.get("mean") is not None]
        if not points:
            p.setPen(QColor(c["muted"]))
            p.drawText(plot, Qt.AlignmentFlag.AlignCenter, "The trade-off appears here")
            p.end()
            return
        result = self.result or {}
        base = result.get("base") or {}
        target = result.get("target")
        xs = [pt["cost"] for pt in points] + [base.get("cost") or 0.0]
        ys = ([pt["lower"] for pt in points if pt.get("lower") is not None]
              + [pt["upper"] for pt in points if pt.get("upper") is not None]
              + [pt["mean"] for pt in points]
              + ([target] if isinstance(target, (int, float)) else []))
        x0, x1 = min(xs), max(xs)
        y0, y1 = min(ys), max(ys)
        x1 = x1 if x1 > x0 else x0 + 1
        pad = (y1 - y0) * 0.1 or 0.001
        y0, y1 = y0 - pad, y1 + pad

        def X(v: float) -> float:  # noqa: N802
            return plot.left() + (v - x0) / (x1 - x0) * plot.width()

        def Y(v: float) -> float:  # noqa: N802
            return plot.bottom() - (v - y0) / (y1 - y0) * plot.height()

        p.setPen(QColor(c["muted"]))
        for v in (y0 + pad, (y0 + y1) / 2, y1 - pad):
            p.drawText(QRectF(0, Y(v) - 8, self.LEFT - 6, 16),
                       Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter, _pct(v))
        for v in (x0, x1):
            p.drawText(QRectF(X(v) - 50, plot.bottom() + 2, 100, 16),
                       Qt.AlignmentFlag.AlignHCenter, f"{v:,.4g}")
        p.drawText(QRectF(plot.left(), plot.bottom() + 16, plot.width(), 16),
                   Qt.AlignmentFlag.AlignHCenter, "spares holding cost")
        if isinstance(target, (int, float)):
            p.setPen(QPen(QColor(c["result_yellow"]), 1.2, Qt.PenStyle.DashLine))
            p.drawLine(QPointF(plot.left(), Y(target)), QPointF(plot.right(), Y(target)))
        chosen = result.get("recommended")
        for pt in points:
            x, y = X(pt["cost"]), Y(pt["mean"])
            p.setPen(QPen(QColor(c["muted"]), 1))
            if pt.get("lower") is not None and pt.get("upper") is not None:
                p.drawLine(QPointF(x, Y(pt["lower"])), QPointF(x, Y(pt["upper"])))
            colour = QColor(c["accent"])
            p.setPen(QPen(colour, 1))
            p.setBrush(colour)
            radius = 6.0 if pt is chosen or pt == chosen else 3.5
            p.drawEllipse(QPointF(x, y), radius, radius)
        if base.get("mean") is not None:
            p.setBrush(Qt.BrushStyle.NoBrush)
            p.setPen(QPen(QColor(c["text"]), 1.5))
            p.drawEllipse(QPointF(X(base.get("cost") or 0.0), Y(base["mean"])), 8, 8)
        p.end()


class SparesDialog(AnalysisDialog):
    TITLE = "Optimise spares"
    INTRO = ("The cheapest spare stock that meets an availability target. A "
             "backorder model proposes stock levels one unit at a time (where "
             "one more unit removes the most expected shortage per unit of "
             "cost); each is simulated with paired runs, and a level meets the "
             "target only when its lower confidence bound does. The ring is "
             "today's stock; the large dot the recommendation.")
    FIRST_LABEL = "Running Base"
    SIZE = (1040, 680)

    def build_inputs(self) -> QLayout:
        self._mode = QComboBox()
        self._mode.addItem("Cheapest that meets the target", "meet")
        self._mode.addItem("Show the trade-off curve", "curve")
        self._target = _target_box()
        self._budget = QSpinBox()
        self._budget.setRange(1, 500)
        self._budget.setValue(40)
        self._budget.setToolTip("Most stock levels to simulate")
        self._trials = trials_box(self._win, "Trials for every stock level (paired)")
        row = QHBoxLayout()
        for label, widget in (("Goal", self._mode), ("Availability", self._target),
                              ("Up to", self._budget), ("Trials", self._trials)):
            row.addWidget(QLabel(label))
            row.addWidget(widget)
            row.addSpacing(10)
        row.addStretch(1)
        return row

    def build_result_area(self) -> QWidget:
        self.chart = TradeOffChart()
        self.table = _table(("Stock", "Holding cost", "Availability", "Lower bound"))
        self.table.itemSelectionChanged.connect(self.sync_buttons)
        split = QSplitter(Qt.Orientation.Horizontal)
        split.addWidget(self.chart)
        split.addWidget(self.table)
        split.setSizes([520, 480])
        return split

    def make_work(self) -> Work | str:
        from raptor.api.optimise.spares import pools_from_model
        if not pools_from_model(self._win._rbd):
            return "This model has no finite spare pool with demand to optimise."
        mode, target = self._mode.currentData(), self._target.value() / 100
        budget, trials, workers = self._budget.value(), self._trials.value(), cores.chosen()

        seed = study_seed(self._win)
        def work(ws, mid, progress, abort):
            from raptor.api.optimise.spares import optimise_spares
            return optimise_spares(ws, mid, mode=mode, target=target,
                                   max_evaluations=budget, trials=trials, seed=seed,
                                   workers=workers, timeout_s=24 * 3600.0,
                                   progress=progress, abort=abort)
        return work

    def show_result(self, result: Any) -> None:
        self.chart.set_result(result)
        points = result["points"]
        chosen = result.get("recommended")
        base = result.get("base") or {}

        def stock(pt: dict[str, Any]) -> str:
            return ", ".join(f"{name} {n}" for name, n in pt["stocks"].items())
        # Row 0 is today's stock, so the recommendation reads against it
        # (the chart's ring was the only place it showed).
        rows = [(f"Today: {stock(base)}" if base.get("stocks") else "Today",
                 f"{base.get('cost') or 0:,.6g}", _pct(base.get("mean")),
                 _pct(base.get("lower")))]
        rows += [(stock(pt), f"{pt['cost']:,.6g}", _pct(pt["mean"]), _pct(pt["lower"]))
                 for pt in points]
        bold = {i + 1 for i, pt in enumerate(points) if pt == chosen}
        _fill(self.table, rows, bold,
              ["The stock the model holds now"]
              + ["; ".join(pt.get("changes") or []) for pt in points])
        self.caption.setText(f"{result['caption']} {result['note']}")
        if bold:
            self.table.selectRow(min(bold))

    def selection(self) -> tuple[Any, str, list[str]] | None:
        result = self.result
        index = self.table.currentRow() - 1          # row 0 is today's stock
        if result is None or not 0 <= index < len(result["points"]):
            return None
        point = result["points"][index]
        stock = ", ".join(f"{name} {n}" for name, n in point["stocks"].items())
        return index, f"spare stock {stock}", list(point.get("changes") or [])

    def result_text(self) -> str:
        result = self.result
        if result is None:
            return ""
        base = result.get("base") or {}
        lines = [f"today {', '.join(f'{k} {v}' for k, v in (base.get('stocks') or {}).items())}\t"
                 f"cost {base.get('cost') or 0:,.6g}\t{_pct(base.get('mean'))}\t"
                 f"lower {_pct(base.get('lower'))}"]
        lines += [f"{', '.join(f'{k} {v}' for k, v in pt['stocks'].items())}\t"
                  f"cost {pt['cost']:,.6g}\t{_pct(pt['mean'])}\tlower {_pct(pt['lower'])}"
                  for pt in result["points"]]
        return "\n".join([*lines, "", result["caption"], result["note"]])


# ---- preventive maintenance ------------------------------------------------------

class PmDialog(AnalysisDialog):
    TITLE = "Optimise maintenance"
    INTRO = ("Is planned maintenance worthwhile on each block, and how often? "
             "A formula screens each block first (PM pays only for a part that "
             "wears out and whose planned job is cheaper than a failure), then "
             "no PM and 0.5 to 2 times the formula's interval are simulated "
             "with paired runs. The recommendation comes from the simulation.")
    FIRST_LABEL = "Running Base"

    def build_inputs(self) -> QLayout:
        self._objective = QComboBox()
        self._objective.addItem("Availability", "availability")
        self._objective.addItem("Cost", "cost")
        self._trials = trials_box(self._win, "Trials for every option (paired)")
        row = QHBoxLayout()
        for label, widget in (("Best for", self._objective), ("Trials", self._trials)):
            row.addWidget(QLabel(label))
            row.addWidget(widget)
            row.addSpacing(10)
        row.addStretch(1)
        return row

    def build_result_area(self) -> QWidget:
        # The formula's screen and the simulation's verdict are two different
        # answers: named apart, "worthwhile" beside "Worth changing: no" read
        # as a contradiction.
        self.table = _table(("Block", "Formula screen", "Best simulated option",
                             "Its availability", "Recommendation"))
        self.table.itemSelectionChanged.connect(self.sync_buttons)
        return self.table

    def make_work(self) -> Work | str:
        objective, trials, workers = (self._objective.currentData(), self._trials.value(),
                                      cores.chosen())

        seed = study_seed(self._win)
        def work(ws, mid, progress, abort):
            from raptor.api.optimise.pm import optimise_pm
            return optimise_pm(ws, mid, objective=objective, trials=trials, seed=seed,
                               workers=workers, timeout_s=24 * 3600.0,
                               progress=progress, abort=abort)
        return work

    def _rows(self) -> list[dict[str, Any]]:
        result = self.result or {}
        best = {r["block"]: r for r in result.get("recommendations") or []}
        return [{"advice": a, "rec": best.get(a["block"])} for a in result.get("advice") or []]

    def show_result(self, result: Any) -> None:
        if not result["advice"]:
            self.caption.setText("No block has a life law PM could act on.")
            return
        rows, tips, bold = [], [], set()
        fraction = result["objective"] == "availability"
        header = self.table.horizontalHeaderItem(3)
        if header is not None:
            header.setText("Its availability" if fraction else "Its mean cost")
        for index, entry in enumerate(self._rows()):
            advice, rec = entry["advice"], entry["rec"] or {}
            screen = ("PM may pay" if advice["worthwhile"]
                      else "PM cannot pay" if advice["worthwhile"] is False
                      else "not enough data")
            mean = rec.get("mean")
            verdict = (f"change to {rec.get('option')}" if rec.get("worthwhile")
                       else "keep as is (formula)" if rec and not rec.get("options_simulated")
                       else "keep as is" if rec else "not simulated")
            rows.append((advice["block"], screen, rec.get("option", "—"),
                         _pct(mean) if fraction else ("—" if mean is None else f"{mean:,.6g}"),
                         verdict))
            tips.append(advice.get("reason") or "")
            if rec.get("worthwhile"):
                bold.add(index)
        _fill(self.table, rows, bold, tips)
        self.caption.setText(f"{result['caption']} {result['note']}")
        if rows:
            self.table.selectRow(min(bold) if bold else 0)

    def selection(self) -> tuple[Any, str, list[str]] | None:
        rows = self._rows()
        index = self.table.currentRow()
        if not 0 <= index < len(rows) or rows[index]["rec"] is None:
            return None
        rec = rows[index]["rec"]
        return rec["block"], f"{rec['block']}: {rec['option']}", list(rec.get("changes") or [])

    def result_text(self) -> str:
        if self.result is None:
            return ""
        lines = [f"{e['advice']['block']}\t{(e['rec'] or {}).get('option', '—')}\t"
                 f"{'worth changing' if (e['rec'] or {}).get('worthwhile') else 'keep as is'}"
                 f"\t{e['advice'].get('reason') or ''}" for e in self._rows()]
        return "\n".join([*lines, "", self.result["caption"], self.result["note"]])


# ---- crews -------------------------------------------------------------------------

class CrewsDialog(AnalysisDialog):
    TITLE = "Optimise crews"
    INTRO = ("How many units of each repair crew or tool are needed. Sizes from "
             "one below the offered load (failures per hour × repair hours) to a "
             "few above it are simulated with paired runs; the recommendation "
             "is the smallest size whose lower confidence bound meets the "
             "target, or the best measured size when none does.")
    FIRST_LABEL = "Running Base"

    def build_inputs(self) -> QLayout:
        self._target = _target_box()
        self._extra = QSpinBox()
        self._extra.setRange(0, 20)
        self._extra.setValue(3)
        self._extra.setToolTip("How many sizes above the offered load to try")
        self._trials = trials_box(self._win, "Trials for every size (paired)")
        row = QHBoxLayout()
        for label, widget in (("Availability", self._target), ("Sizes above the load", self._extra),
                              ("Trials", self._trials)):
            row.addWidget(QLabel(label))
            row.addWidget(widget)
            row.addSpacing(10)
        row.addStretch(1)
        return row

    def build_result_area(self) -> QWidget:
        self.table = _table(("Resource", "Recommended units", "Availability", "Lower bound",
                             "Mean cost", "Why"))
        self.table.itemSelectionChanged.connect(self.sync_buttons)
        return self.table

    def make_work(self) -> Work | str:
        from raptor.api.optimise.resources import resources_from_model
        if not resources_from_model(self._win._rbd):
            return "This model has no repair resource to size."
        target, extra = self._target.value() / 100, self._extra.value()
        trials, workers = self._trials.value(), cores.chosen()

        seed = study_seed(self._win)
        def work(ws, mid, progress, abort):
            from raptor.api.optimise.resources import optimise_resources
            return optimise_resources(ws, mid, target=target, extra=extra, trials=trials,
                                      seed=seed, workers=workers, timeout_s=24 * 3600.0,
                                      progress=progress, abort=abort)
        return work

    def show_result(self, result: Any) -> None:
        recs = result["recommendations"]
        def cost(value: float | None) -> str:
            return "—" if value is None else f"{value:,.6g}"
        rows = [(r["resource"], str(r["units"]), _pct(r["mean"]), _pct(r["lower"]),
                 cost(r.get("cost")), r["why"]) for r in recs]
        tips = ["\n".join(f"{row['units']} units: {_pct(row['mean'])} (lower {_pct(row['lower'])})"
                          + (f", cost {cost(row.get('cost'))}" if row.get("cost") is not None else "")
                          for row in r["curve"]) for r in recs]
        _fill(self.table, rows, set(), tips)
        self.caption.setText(f"{result['caption']} {result['note']}")
        if rows:
            self.table.selectRow(0)

    def selection(self) -> tuple[Any, str, list[str]] | None:
        result = self.result
        index = self.table.currentRow()
        if result is None or not 0 <= index < len(result["recommendations"]):
            return None
        rec = result["recommendations"][index]
        return rec["resource"], f"{rec['resource']}: {rec['units']} units", list(rec["changes"])

    def result_text(self) -> str:
        if self.result is None:
            return ""
        lines = [f"{r['resource']}\t{r['units']} units\t{_pct(r['mean'])}\t"
                 f"lower {_pct(r['lower'])}\t"
                 f"cost {'—' if r.get('cost') is None else format(r['cost'], ',.6g')}\t"
                 f"{r['why']}" for r in self.result["recommendations"]]
        return "\n".join([*lines, "", self.result["caption"], self.result["note"]])


def open_optimiser(win: Any, kind: str) -> AnalysisDialog:
    from raptor.gui.ui.analysis_dialog import open_analysis
    cls: type[AnalysisDialog] = {"spares": SparesDialog, "pm": PmDialog,
                                 "crews": CrewsDialog}[kind]
    return open_analysis(win, cls, f"_optimise_{kind}_dialog")
