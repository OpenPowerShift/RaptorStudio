"""The Charts tab: the WP-2.5 chart set with one picker and export.

Every chart is a `ChartSpec` from the Qt-free data module, so the same
figure can be copied, saved as PNG/SVG/CSV, or rendered into a report.
"""
from __future__ import annotations

from typing import Any

from PySide6.QtCore import Signal
from PySide6.QtWidgets import (
    QComboBox, QFileDialog, QHBoxLayout, QLabel, QPushButton, QVBoxLayout,
    QWidget,
)

from raptor.api.insight import charts
from raptor.gui.resources.design_tokens import SPACING
from raptor.gui.ui.chart_widget import ChartWidget


class ChartsPage(QWidget):
    """A chart picker, the chart itself, and its export actions."""

    locate_requested = Signal(str, str)

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._specs: dict[str, charts.ChartSpec] = {}
        lay = QVBoxLayout(self)
        lay.setContentsMargins(SPACING["md"], SPACING["md"],
                               SPACING["md"], SPACING["md"])
        lay.setSpacing(SPACING["sm"])

        top = QHBoxLayout()
        top.addWidget(QLabel("Chart:"))
        self.selector = QComboBox()
        self.selector.currentIndexChanged.connect(self._on_selected)
        top.addWidget(self.selector, 1)
        for label, slot in (("Copy image", self.copy_image),
                            ("Save SVG…", self._ask_svg),
                            ("Save PNG…", self._ask_png),
                            ("Save CSV…", self._ask_csv)):
            button = QPushButton(label)
            button.clicked.connect(slot)
            top.addWidget(button)
        lay.addLayout(top)

        self.chart = ChartWidget()
        self.chart.locate_requested.connect(self.locate_requested)
        lay.addWidget(self.chart, 1)
        self.note = QLabel("")
        self.note.setObjectName("SectionNote")
        self.note.setWordWrap(True)
        lay.addWidget(self.note)

    # -- input ----------------------------------------------------------

    def set_inputs(self, *, per_run: list[dict] | None = None,
                   attribution: dict[str, Any] | None = None,
                   importance: dict[str, Any] | None = None,
                   report_data: list[Any] | None = None,
                   periods: dict[str, Any] | None = None,
                   units: str = "", run_id: str = "",
                   target: float | None = None) -> None:
        """Rebuild the picker from whatever this result set has."""
        self._specs = self._build(per_run or [], attribution, importance,
                                  report_data or [], periods, units, run_id,
                                  target)
        keep = self.selector.currentText()
        self.selector.blockSignals(True)
        self.selector.clear()
        self.selector.addItems(list(self._specs))
        self.selector.blockSignals(False)
        if keep in self._specs:
            self.selector.setCurrentText(keep)
        self._on_selected(self.selector.currentIndex())
        self.note.setText(
            "" if attribution is not None else
            "Open the Drivers tab once to add the downtime charts.")

    def _build(self, per_run: list[dict], attribution: dict[str, Any] | None,
               importance: dict[str, Any] | None, report_data: list[Any],
               periods: dict[str, Any] | None,
               units: str, run_id: str,
               target: float | None) -> dict[str, charts.ChartSpec]:
        specs: dict[str, charts.ChartSpec] = {}
        if per_run:
            outcome = charts.trial_outcome(per_run, target=target,
                                           units=units)
            outcome.provenance["run_id"] = run_id
            specs["Run-to-run outcome"] = outcome
            specs["Chance of reaching a value"] = charts.ecdf(
                per_run, target=target, units=units)
            specs["Has the run settled?"] = charts.convergence(
                per_run, target=target)
            ladder = charts.availability_ladder(
                _ladder(per_run))
            ladder.provenance["run_id"] = run_id
            specs["Availability ladder"] = ladder
        if attribution:
            specs["Who causes the downtime"] = charts.downtime_pareto(attribution)
            specs["Where availability is lost"] = charts.downtime_waterfall(
                attribution,
                _operational(per_run))
            specs["How long is an outage?"] = charts.outage_histogram(attribution)
            specs["Frequency against duration"] = charts.frequency_duration(
                attribution)
            if attribution.get("capacity"):
                specs["Who loses the capacity"] = charts.lost_capacity_pareto(
                    attribution)
                specs["Downtime against lost capacity"] = (
                    charts.capacity_vs_downtime(attribution))
                specs["Where the capacity goes"] = charts.capacity_waterfall(
                    attribution)
        if importance:
            specs["Importance quadrant"] = charts.importance_quadrant(importance)
        if periods:
            specs["Availability year by year"] = charts.annual_fan(periods)
            specs["A bad year, as a frequency"] = charts.annual_dots(periods)
        if any(getattr(rd, "cost", None) is not None for rd in report_data):
            specs["Where the money goes"] = charts.cost_pareto(report_data)
        return specs

    def _on_selected(self, index: int) -> None:
        name = self.selector.itemText(index) if index >= 0 else ""
        spec = self._specs.get(name)
        self.chart.set_spec(spec)
        self.chart.setToolTip(spec.caption if spec is not None else "")

    # -- actions --------------------------------------------------------

    def selected_title(self) -> str:
        return self.selector.currentText()

    def selected_spec(self) -> charts.ChartSpec | None:
        return self.chart.spec()

    def copy_image(self) -> None:
        self.chart.copy_image()

    def save_svg(self, path: str) -> None:
        self.chart.save_svg(path)

    def save_png(self, path: str) -> None:
        self.chart.save_png(path)

    def save_csv(self, path: str) -> None:
        self.chart.save_csv(path)

    def _ask_svg(self) -> None:
        path, _ = QFileDialog.getSaveFileName(self, "Save chart", "",
                                              "SVG image (*.svg)")
        if path:
            self.save_svg(path)

    def _ask_png(self) -> None:
        path, _ = QFileDialog.getSaveFileName(self, "Save chart", "",
                                              "PNG image (*.png)")
        if path:
            self.save_png(path)

    def _ask_csv(self) -> None:
        path, _ = QFileDialog.getSaveFileName(self, "Save chart data", "",
                                              "CSV (*.csv)")
        if path:
            self.save_csv(path)


def _ladder(per_run: list[dict]) -> dict[str, Any]:
    from raptor.api.insight.measures import availability_ladder
    return availability_ladder(per_run)


def _operational(per_run: list[dict]) -> float | None:
    if not per_run:
        return None
    return _ladder(per_run)["operational"]["value"]
