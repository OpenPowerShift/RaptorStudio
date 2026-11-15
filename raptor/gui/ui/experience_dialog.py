"""Inspector ▸ Estimate from experience: a life law with no data (plan 10.4).

Asks the questions an engineer can answer ("one in ten have failed by 100
hours, half by 300") and fits a Weibull, lognormal or exponential law
(`raptor.api.distributions.from_quantiles`), refitting as each answer is
typed. The chart draws the fitted law's chance of having failed by each age,
with the stated answers as points, so a law that cannot honour the answers
shows it. *Use for <block>* writes the law and its parameters into the
block's failure or repair distribution as one undo step
(`experience_changes`, the same edits `raptor fit --block` prints).
"""
from __future__ import annotations

import math
from typing import Any

from PySide6.QtCore import QPointF, QRectF, Qt
from PySide6.QtGui import QColor, QPainter, QPainterPath, QPen
from PySide6.QtWidgets import (
    QComboBox, QDialog, QHBoxLayout, QHeaderView, QLabel, QPushButton, QSizePolicy,
    QTableWidget, QTableWidgetItem, QVBoxLayout, QWidget,
)

from raptor.gui.resources.design_tokens import active_colour

LAWS = (("weibull", "Weibull: lives that wear out"),
        ("lognormal", "Lognormal: repair times, a long tail"),
        ("exponential", "Exponential: random failures"))
DEFAULT_SHARES = (10.0, 50.0, 90.0)


def law_cdf(fit: dict[str, Any], age: float) -> float:
    """The fitted law's chance of having failed by `age`."""
    if age <= 0:
        return 0.0
    params = fit["params"]
    if fit["law"] == "weibull":
        return 1.0 - math.exp(-((age / params["scale"]) ** params["shape"]))
    if fit["law"] == "lognormal":
        from statistics import NormalDist
        return NormalDist().cdf((math.log(age) - params["mu"]) / params["sigma"])
    return 1.0 - math.exp(-age / params["mean"])


class FitChart(QWidget):
    """The fitted chance-of-failure curve and the stated answers."""

    LEFT, RIGHT, TOP, BOTTOM = 48, 16, 12, 34

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.fit: dict[str, Any] | None = None
        self.units = ""
        self.setMinimumHeight(200)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)

    def set_fit(self, fit: dict[str, Any] | None, units: str) -> None:
        self.fit, self.units = fit, units
        self.update()

    def paintEvent(self, _event):                          # noqa: N802
        c = active_colour()
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        p.fillRect(self.rect(), QColor(c["surface"]))
        fit = self.fit
        if fit is None:
            p.setPen(QColor(c["muted"]))
            p.drawText(self.rect(), Qt.AlignmentFlag.AlignCenter,
                       "The fitted law appears here once two answers are in")
            p.end()
            return
        w = max(10.0, self.width() - self.LEFT - self.RIGHT)
        h = max(10.0, self.height() - self.TOP - self.BOTTOM)
        top_age = max([fit["fitted"][max(fit["fitted"])], *fit["stated"].values()]) * 1.05

        def pt(age: float, share: float) -> QPointF:
            return QPointF(self.LEFT + w * age / top_age, self.TOP + h * (1 - share))

        p.setPen(QPen(QColor(c["border"]), 1))
        for share in (0.0, 0.5, 1.0):
            p.drawLine(pt(0, share), pt(top_age, share))
        p.setPen(QColor(c["muted"]))
        for share in (0.0, 0.5, 1.0):
            y = self.TOP + h * (1 - share)
            p.drawText(QRectF(0, y - 9, self.LEFT - 6, 18),
                       Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter,
                       f"{100 * share:.0f} %")
        for fraction in (0.0, 0.5, 1.0):
            age = top_age * fraction
            x = self.LEFT + w * fraction
            p.drawText(QRectF(x - 50, self.TOP + h + 4, 100, 16),
                       Qt.AlignmentFlag.AlignHCenter, f"{age:.4g}")
        p.drawText(QRectF(self.LEFT, self.TOP + h + 18, w, 16), Qt.AlignmentFlag.AlignHCenter,
                   f"age{f' ({self.units})' if self.units else ''}: the line is the "
                   "fitted chance of having failed by then, the dots your answers")
        path = QPainterPath(pt(0, 0))
        for step in range(1, 121):
            age = top_age * step / 120
            path.lineTo(pt(age, law_cdf(fit, age)))
        p.setPen(QPen(QColor(c["accent"]), 2))
        p.setBrush(Qt.BrushStyle.NoBrush)
        p.drawPath(path)
        p.setPen(QPen(QColor(c["surface"]), 1.5))
        p.setBrush(QColor(c["result_red"]))
        for share, age in fit["stated"].items():
            p.drawEllipse(pt(age, share), 5, 5)
        p.end()


class ExperienceDialog(QDialog):
    """Fit a block's failure (`use="fail"`) or repair law from experience."""

    def __init__(self, win: Any, block_id: int, use: str = "fail") -> None:
        super().__init__(win)
        self._win = win
        self.block_id = int(block_id)
        self.use = use
        self.fit: dict[str, Any] | None = None
        self.block_name = self._name()
        self.units = (win._current_units() or "") if hasattr(win, "_current_units") else ""
        what = "failure" if use == "fail" else "repair"
        self.setWindowTitle(f"Estimate {what} law from experience")
        self.resize(640, 600)

        intro = QLabel(
            f"No data for {self.block_name}? Say what share "
            + ("has failed" if use == "fail" else "of repairs is finished")
            + " by a given age, for two or more shares. Two answers fix the "
            "law exactly; more are a best fit. The law is only as good as "
            "the answers.")
        intro.setWordWrap(True)
        self.law = QComboBox()
        for key, label in LAWS:
            self.law.addItem(label, key)
        self.law.setCurrentIndex(0 if use == "fail" else 1)
        self.law.currentIndexChanged.connect(self.refit)
        law_row = QHBoxLayout()
        law_row.addWidget(QLabel("Law"))
        law_row.addWidget(self.law, 1)

        self.chart = FitChart()
        self.summary = QLabel()
        self.summary.setWordWrap(True)
        self.summary.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        layout = QVBoxLayout(self)
        layout.addWidget(intro)
        layout.addLayout(law_row)
        layout.addWidget(self._build_table())
        layout.addLayout(self._build_row_buttons())
        layout.addWidget(self.chart, 1)
        layout.addWidget(self.summary)
        layout.addLayout(self._build_buttons(what))
        self.refit()

    def _build_table(self) -> QTableWidget:
        use = self.use
        unit = f" ({self.units})" if self.units else ""
        self.table = QTableWidget(len(DEFAULT_SHARES), 2)
        self.table.setHorizontalHeaderLabels(
            ("Share failed (%)" if use == "fail" else "Share repaired (%)",
             f"By this age{unit}" if use == "fail" else f"Within{unit}"))
        self.table.verticalHeader().setVisible(False)
        self.table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Stretch)
        self.table.setMaximumHeight(150)
        for row, share in enumerate(DEFAULT_SHARES):
            self.table.setItem(row, 0, QTableWidgetItem(f"{share:g}"))
            self.table.setItem(row, 1, QTableWidgetItem(""))
        self.table.itemChanged.connect(self.refit)
        return self.table

    def _build_row_buttons(self) -> QHBoxLayout:
        add = QPushButton("Add answer")
        add.clicked.connect(self._add_row)
        remove = QPushButton("Remove")
        remove.clicked.connect(self._remove_row)
        for button in (add, remove):
            button.setAutoDefault(False)
        rows = QHBoxLayout()
        rows.addWidget(add)
        rows.addWidget(remove)
        rows.addStretch(1)
        return rows

    def _build_buttons(self, what: str) -> QHBoxLayout:
        self.apply_btn = QPushButton(f"Use for {self.block_name}")
        self.apply_btn.setToolTip(f"Write this law into {self.block_name}'s {what} "
                                  "distribution (one undo step)")
        self.apply_btn.clicked.connect(self.apply)
        self.apply_btn.setDefault(True)
        close = QPushButton("Close")
        close.setAutoDefault(False)
        close.clicked.connect(self.close)
        buttons = QHBoxLayout()
        buttons.addWidget(self.apply_btn)
        buttons.addStretch(1)
        buttons.addWidget(close)
        return buttons

    def _name(self) -> str:
        rbd = self._win._rbd
        for row in (rbd.section("BLOCK_DATA").rows if rbd is not None else []):
            if str(row.get("ID")) == str(self.block_id):
                return str(row.get("blockName") or self.block_id)
        return f"block {self.block_id}"

    def _add_row(self) -> None:
        row = self.table.rowCount()
        self.table.insertRow(row)
        self.table.setItem(row, 0, QTableWidgetItem(""))
        self.table.setItem(row, 1, QTableWidgetItem(""))

    def _remove_row(self) -> None:
        row = self.table.currentRow()
        if row >= 0:
            self.table.removeRow(row)
            self.refit()

    def set_answers(self, answers: list[tuple[float, float]]) -> None:
        """Fill the table with (share %, age) pairs (for scripts and tests)."""
        self.table.blockSignals(True)
        self.table.setRowCount(len(answers))
        for row, (share, age) in enumerate(answers):
            self.table.setItem(row, 0, QTableWidgetItem(f"{share:g}"))
            self.table.setItem(row, 1, QTableWidgetItem(f"{age:g}"))
        self.table.blockSignals(False)
        self.refit()

    def _cell(self, row: int, column: int) -> str:
        item = self.table.item(row, column)
        return item.text().strip() if item is not None else ""

    def answers(self) -> tuple[dict[float, float], str | None]:
        """Complete rows as {probability: age}, or why a row cannot be read."""
        out: dict[float, float] = {}
        for row in range(self.table.rowCount()):
            texts = [self._cell(row, col) for col in (0, 1)]
            if not texts[0] or not texts[1]:
                continue
            try:
                share, age = float(texts[0].rstrip("%")), float(texts[1])
            except ValueError:
                return out, f"row {row + 1}: both cells must be numbers"
            if not 0 < share < 100:
                return out, f"row {row + 1}: the share must be between 0 and 100 %"
            out[share / 100.0] = age
        return out, None

    def refit(self) -> None:
        from raptor.api.distributions import from_quantiles
        answers, problem = self.answers()
        self.fit = None
        if problem is None and len(answers) < 2:
            problem = "Give an age for at least two shares."
        if problem is None:
            try:
                self.fit = from_quantiles(self.law.currentData(), answers)
            except ValueError as exc:
                problem = str(exc).capitalize()
        self.chart.set_fit(self.fit, self.units)
        self.apply_btn.setEnabled(self.fit is not None)
        self.summary.setText(problem or self._describe(self.fit))

    def _describe(self, fit: dict[str, Any] | None) -> str:
        if fit is None:
            return ""
        unit = f" {self.units}" if self.units else ""
        params = ", ".join(f"{name} {value:.4g}" for name, value in fit["params"].items())
        median = fit["fitted"].get(0.5)
        note = f" {fit['shape_note'].capitalize()}." if fit["shape_note"] else ""
        return (f"{fit['law'].capitalize()}: {params}. Mean {fit['mean']:.4g}{unit}"
                + (f", median {median:.4g}{unit}" if median is not None else "")
                + "." + note)

    def changes(self) -> list[str]:
        from raptor.api.distributions import experience_changes
        if self.fit is None:
            return []
        return experience_changes(self.fit, str(self.block_id), self.use)

    def apply(self) -> bool:
        from raptor.gui.ui.analysis_dialog import apply_changes
        if self.fit is None:
            return False
        what = "failure" if self.use == "fail" else "repair"
        label = f"{self.block_name} {what} law from experience"
        why = apply_changes(self._win, self.changes(), label)
        if why is not None:
            self.summary.setText(f"Cannot apply: {why}.")
            return False
        self.summary.setText(f"{self._describe(self.fit)} Written into {self.block_name} "
                             "(Edit ▸ Undo takes it back).")
        return True


def open_experience(win: Any, block_id: int, use: str = "fail") -> ExperienceDialog:
    dlg = getattr(win, "_experience_dialog", None)
    if dlg is not None:
        dlg.close()
        dlg.deleteLater()
    dlg = ExperienceDialog(win, block_id, use)
    win._experience_dialog = dlg
    dlg.show()
    dlg.raise_()
    return dlg
