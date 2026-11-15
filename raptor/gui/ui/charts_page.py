"""The Charts tab: every result chart in one place.

The left side lists the charts by the question they answer (the answer,
over time, why the system goes down, lost capacity, year by year, cost,
what matters most); the right side draws the chosen one.  A chart this
result cannot draw stays in the list, greyed, and choosing it says what
the run needs (a recording to tick, an analysis to turn on) instead of
silently disappearing.

Above the chart sit only the controls the chart uses: the run for a
one-run chart over time, the bottom of the axis for an availability chart
over time, and one Export menu.

Every chart is a `ChartSpec` from the Qt-free modules
(`raptor.api.insight.charts` and `.timecharts`), so the same figure can be
copied, saved as PNG/SVG/CSV, put in a report, or asked for by name from
the command line and the MCP server.
"""
from __future__ import annotations

import logging
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QAction, QActionGroup, QColor, QFont
from PySide6.QtWidgets import (
    QComboBox, QFileDialog, QHBoxLayout, QLabel, QMenu, QPushButton, QSpinBox,
    QSplitter, QStackedWidget, QToolButton, QTreeWidget, QTreeWidgetItem,
    QVBoxLayout, QWidget,
)

from raptor.api.insight import charts, timecharts
from raptor.gui.resources.design_tokens import SPACING, active_colour
from raptor.gui.ui import chart_export
from raptor.gui.ui.chart_widget import ChartWidget

_KEY = Qt.ItemDataRole.UserRole


@dataclass
class _Entry:
    """One chart in the list: its CLI name, its group and how to build it."""
    key: str
    title: str
    group: str
    build: Callable[[], charts.ChartSpec | None] | None
    missing: str = ""          # why it cannot be drawn ("" when it can)
    action: str = ""           # a button that fixes it, when there is one


#: Groups in the order the list shows them.
GROUPS = ("The answer", "Over time", "Why it goes down", "Lost capacity",
          "Year by year", "Cost", "What matters most")

_RECORD_AO = ("Tick Availability plot under Run panel ▸ Recording and run "
              "again: these charts are drawn from that recording.")
_RECORD_CAP = ("Turn on Capacity analysis (System settings) and record the "
               "capacity plot under Run panel ▸ Recording, then run again.")


class ChartsPage(QWidget):
    """The chart list, the chart, and the controls the chart uses."""

    locate_requested = Signal(str, str)
    #: the reader asked for the downtime charts before the drivers were worked out
    drivers_requested = Signal()
    #: the reader changed the bottom of the availability axis (saved with the model)
    y_floor_changed = Signal(float)
    #: "availability" or "capacity": save the recorded plot rows (Raptor's rpt_* file)
    raw_export_requested = Signal(str)

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._entries: dict[str, _Entry] = {}
        #: built charts by title (what the report and the old picker used)
        self._specs: dict[str, charts.ChartSpec] = {}
        self._time_cache: dict[tuple[str, int, float], charts.ChartSpec] = {}
        self._inputs: dict[str, Any] = {}
        self._ao_runs: list[dict[str, Any]] = []
        self._cap_runs: list[dict[str, Any]] = []
        self._y_floor = 0.0
        self._wanted = "Run-to-run outcome"

        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        split = QSplitter(Qt.Orientation.Horizontal)
        split.setChildrenCollapsible(True)
        lay.addWidget(split)

        self.nav = QTreeWidget()
        self.nav.setObjectName("ChartList")
        self.nav.setHeaderHidden(True)
        self.nav.setRootIsDecorated(False)
        self.nav.setIndentation(10)
        self.nav.setUniformRowHeights(True)
        self.nav.setMinimumWidth(150)
        self.nav.currentItemChanged.connect(self._on_current)
        # a heading folds its group away (the list is long on a short dock)
        self.nav.itemClicked.connect(
            lambda item, _col: item.setExpanded(not item.isExpanded())
            if item.childCount() else None)
        split.addWidget(self.nav)

        right = QWidget()
        rlay = QVBoxLayout(right)
        rlay.setContentsMargins(SPACING["md"], SPACING["sm"], SPACING["md"], SPACING["sm"])
        rlay.setSpacing(SPACING["sm"])
        rlay.addLayout(self._build_bar())
        self.stack = QStackedWidget()
        self.chart = ChartWidget()
        self.chart.locate_requested.connect(self.locate_requested)
        self.stack.addWidget(self.chart)
        self.stack.addWidget(self._build_missing_panel())
        rlay.addWidget(self.stack, 1)
        self.note = QLabel("")
        self.note.setObjectName("SectionNote")
        self.note.setWordWrap(True)
        self.note.hide()
        rlay.addWidget(self.note)
        split.addWidget(right)
        split.setStretchFactor(0, 0)
        split.setStretchFactor(1, 1)
        split.setSizes([210, 800])
        self._sync_controls(None)

    def _build_bar(self) -> QHBoxLayout:
        """Run picker, axis floor and Export: shown only where a chart uses them."""
        bar = QHBoxLayout()
        bar.setSpacing(SPACING["sm"])
        self.run_label = QLabel("Run")
        self.run_box = QSpinBox()
        self.run_box.setMinimum(1)
        self.run_box.setAccelerated(True)
        self.run_box.setToolTip("The run this chart shows (arrow keys step through them)")
        self.run_box.valueChanged.connect(lambda _v: self._show_current())
        self.run_of = QLabel("")
        self.run_of.setObjectName("SectionNote")
        self.floor_button = QPushButton("Axis from: auto")
        self.floor_button.setToolTip(
            "Where the availability axis starts (0 to 0.999999). Auto fits the "
            "axis to the data; a value above 0 is a fixed floor used by every "
            "availability chart over time. Saved with the model.")
        self.floor_button.clicked.connect(self._ask_floor)
        self.fold_box = QComboBox()
        for text, share in (("Show every item", 0.0), ("Fold items below 1 %", 0.01),
                            ("Fold items below 2 %", 0.02),
                            ("Fold items below 5 %", 0.05)):
            self.fold_box.addItem(text, share)
        self.fold_box.setToolTip(
            "Gather the items below a share of the whole into one \"N others\" "
            "row or cell, on screen and in what you save; the caption names them")
        self.fold_box.currentIndexChanged.connect(lambda _i: self._show_current())
        for widget in (self.run_label, self.run_box, self.run_of):
            bar.addWidget(widget)
        bar.addStretch(1)
        bar.addWidget(self.fold_box)
        bar.addWidget(self.floor_button)
        self.export_button = QToolButton()
        self.export_button.setText("Export")
        self.export_button.setPopupMode(QToolButton.ToolButtonPopupMode.InstantPopup)
        self.export_button.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonTextOnly)
        self.export_menu = QMenu(self.export_button)
        self.export_menu.addAction("Copy image", self.copy_image)
        self.export_menu.addAction("Save PNG…", self._ask_png)
        self.export_menu.addAction("Save SVG…", self._ask_svg)
        self.export_menu.addAction("Save PDF…", self._ask_pdf)
        self.export_menu.addAction("Save chart data (CSV)…", self._ask_csv)
        self.export_menu.addSeparator()
        self.page_menu = self.export_menu.addMenu("Page size")
        self.page_menu.setToolTipsVisible(True)
        group = QActionGroup(self.page_menu)
        self.page_actions: dict[str, QAction] = {}
        for key, text in chart_export.PAGE_CHOICES:
            action = self.page_menu.addAction(text)
            action.setCheckable(True)
            action.setChecked(key == ChartWidget.export_page)
            action.triggered.connect(lambda _c=False, k=key: self.set_export_page(k))
            group.addAction(action)
            self.page_actions[key] = action
        self.export_menu.addSeparator()
        self.raw_action = self.export_menu.addAction(
            "Save the recorded plot rows…", self._ask_raw)
        self.raw_action.setToolTip("Every recorded row, in Raptor's rpt_*_plot file layout")
        self.export_button.setMenu(self.export_menu)
        bar.addWidget(self.export_button)
        return bar

    def _build_missing_panel(self) -> QWidget:
        """What a chart the run cannot draw needs, with a button when one helps."""
        empty = QWidget()
        elay = QVBoxLayout(empty)
        elay.addStretch(1)
        self.missing_title = QLabel("")
        title_font = QFont(self.font())
        title_font.setPointSizeF(title_font.pointSizeF() + 2)
        title_font.setBold(True)
        self.missing_title.setFont(title_font)
        self.missing_title.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.missing_text = QLabel("")
        self.missing_text.setWordWrap(True)
        self.missing_text.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.missing_action = QPushButton("")
        self.missing_action.clicked.connect(self.drivers_requested)
        elay.addWidget(self.missing_title)
        elay.addWidget(self.missing_text)
        row = QHBoxLayout()
        row.addStretch(1)
        row.addWidget(self.missing_action)
        row.addStretch(1)
        elay.addLayout(row)
        elay.addStretch(2)
        return empty

    # -- input ----------------------------------------------------------

    def set_inputs(self, *, per_run: list[dict] | None = None,
                   attribution: dict[str, Any] | None = None,
                   importance: dict[str, Any] | None = None,
                   report_data: list[Any] | None = None,
                   periods: dict[str, Any] | None = None,
                   units: str = "", run_id: str = "",
                   target: float | None = None,
                   drivers: str = "ready",
                   measure: str = "availability",
                   generated: float | None = None) -> None:
        """Rebuild the list from whatever this result set has.

        `drivers` says where the downtime attribution stands: ``ready``
        (given here, or none possible yet), ``pending`` (it can be worked
        out on request) or ``unavailable`` (the run kept no event log).
        `measure` is the per-run field the convergence chart follows."""
        self._inputs = {"per_run": per_run or [], "attribution": attribution,
                        "importance": importance, "report_data": report_data or [],
                        "periods": periods, "units": units, "run_id": run_id,
                        "target": target, "drivers": drivers, "measure": measure,
                        "generated": generated}
        self._time_cache.clear()
        self._rebuild()

    def set_series(self, ao_runs: list[dict[str, Any]] | None,
                   cap_runs: list[dict[str, Any]] | None) -> None:
        """The recorded availability and capacity plots (parsed sink rows)."""
        self._ao_runs = list(ao_runs or [])
        self._cap_runs = list(cap_runs or [])
        self._time_cache.clear()
        self._rebuild()

    def set_y_floor(self, value: float) -> None:
        """The model's saved axis floor (no signal: loading is not an edit)."""
        self._y_floor = max(0.0, min(0.999999, float(value)))
        self._time_cache.clear()
        self._sync_floor_text()
        self._show_current()

    @property
    def y_floor(self) -> float:
        return self._y_floor

    def set_note(self, text: str) -> None:
        self.note.setText(text)
        self.note.setVisible(bool(text))

    # -- the list -------------------------------------------------------

    def _rebuild(self) -> None:
        current = self.selected_key() or self._wanted
        folded = {item.text(0) for item in self._groups() if not item.isExpanded()}
        self._entries = {e.key: e for e in self._catalogue()}
        self._specs = {}
        for entry in self._entries.values():
            if not entry.missing and entry.key not in timecharts.TIME_CHARTS:
                spec = self._safe(entry)
                if spec is not None:
                    self._specs[entry.title] = spec
        palette = active_colour()
        self.nav.blockSignals(True)
        self.nav.clear()
        heading = QFont(self.nav.font())
        heading.setBold(True)
        muted = QColor(palette["muted"])
        chosen: QTreeWidgetItem | None = None
        for group in GROUPS:
            members = [e for e in self._entries.values() if e.group == group]
            if not members:
                continue
            parent = QTreeWidgetItem([group.upper()])
            parent.setFlags(Qt.ItemFlag.ItemIsEnabled)
            parent.setToolTip(0, "Click to fold or unfold this group")
            parent.setFont(0, heading)
            parent.setForeground(0, muted)
            self.nav.addTopLevelItem(parent)
            for entry in members:
                item = self._item(entry, muted)
                parent.addChild(item)
                if entry.key == current:
                    chosen = item
            parent.setExpanded(parent.text(0) not in folded
                               or (chosen is not None and chosen.parent() is parent))
        if chosen is None:
            chosen = self._first_available()
        self.nav.blockSignals(False)
        if chosen is not None:
            self.nav.setCurrentItem(chosen)
        self._show_current()

    def _item(self, entry: _Entry, muted: QColor) -> QTreeWidgetItem:
        """One chart's row: greyed, with the reason, when it cannot be drawn."""
        item = QTreeWidgetItem([entry.title])
        item.setData(0, _KEY, entry.key)
        if entry.missing:
            italic = QFont(self.nav.font())
            italic.setItalic(True)
            item.setFont(0, italic)
            item.setForeground(0, muted)
            item.setToolTip(0, entry.missing)
        else:
            spec = self._specs.get(entry.title)
            item.setToolTip(0, spec.subtitle if spec is not None else "")
        return item

    def _groups(self) -> list[QTreeWidgetItem]:
        items = (self.nav.topLevelItem(i) for i in range(self.nav.topLevelItemCount()))
        return [item for item in items if item is not None]

    def _chart_items(self) -> list[QTreeWidgetItem]:
        children = (parent.child(j) for parent in self._groups()
                    for j in range(parent.childCount()))
        return [item for item in children if item is not None]

    def _first_available(self) -> QTreeWidgetItem | None:
        return next((item for item in self._chart_items()
                     if not self._entries[item.data(0, _KEY)].missing), None)

    def _catalogue(self) -> list[_Entry]:  # noqa: C901 - one table of charts
        i = self._inputs
        per_run: list[dict] = i.get("per_run") or []
        attribution = i.get("attribution")
        importance = i.get("importance")
        periods = i.get("periods")
        report_data = i.get("report_data") or []
        target, units = i.get("target"), i.get("units") or ""
        entries: list[_Entry] = []

        def add(key: str, title: str, group: str,
                build: Callable[[], charts.ChartSpec | None] | None,
                missing: str = "", action: str = "") -> None:
            entries.append(_Entry(key, title, group, build, missing, action))

        no_runs = "" if per_run else "No runs yet: run the model (F5)."
        add("trial-outcome", "Run-to-run outcome", "The answer",
            lambda: _with_run_id(charts.trial_outcome(per_run, target=target,
                                                      units=units), i), no_runs)
        add("ecdf", "Chance of reaching a value", "The answer",
            lambda: charts.ecdf(per_run, target=target, units=units), no_runs)
        add("availability-ladder", "Availability ladder", "The answer",
            lambda: _with_run_id(charts.availability_ladder(_ladder(per_run)), i),
            no_runs)
        add("convergence", "Has the run settled?", "The answer",
            lambda: charts.convergence(per_run, i.get("measure") or "availability",
                                       target=target),
            "" if len(per_run) >= 2 else "Needs at least 2 runs.")

        ao, cap = self._ao_runs, self._cap_runs
        has_interval = any(v is not None for r in ao for v in r.get("interval", []))
        for key, title, missing in (
                ("availability-through-run", "Availability through a run",
                 "" if ao else _RECORD_AO),
                ("interval-availability", "Availability interval by interval",
                 "" if has_interval else _RECORD_AO),
                ("availability-band", "Availability across all runs",
                 "" if len(ao) >= 2 else (
                     "Needs the availability plot of at least 2 runs." if ao
                     else _RECORD_AO)),
                ("capacity-through-run", "Capacity through a run",
                 "" if cap else _RECORD_CAP),
                ("capacity-band", "Capacity across all runs",
                 "" if len(cap) >= 2 else (
                     "Needs the capacity plot of at least 2 runs." if cap
                     else _RECORD_CAP))):
            add(key, title, "Over time", None, missing)

        if attribution:
            def drivers_missing(_need: str = "") -> str:
                return ""
            action = ""
        else:
            state = i.get("drivers") or "ready"
            action = "Work out the downtime drivers" if state == "pending" else ""
            reason = ("Work out which elements caused each outage first (it "
                      "replays this run's event log)." if state == "pending" else
                      "These charts replay the event log: tick Event log under "
                      "Run panel ▸ Recording and run again.")

            def drivers_missing(_need: str = "") -> str:
                return reason
        a = attribution or {}
        operational = _operational(per_run)
        for key, title, build, extra in (
                ("downtime-pareto", "Who causes the downtime",
                 lambda: charts.downtime_pareto(a), ""),
                ("downtime-waterfall", "Where availability is lost",
                 lambda: charts.downtime_waterfall(a, operational), ""),
                ("downtime-treemap", "Downtime treemap",
                 lambda: charts.downtime_treemap(a), ""),
                ("phase-downtime", "Downtime by phase",
                 lambda: charts.phase_downtime(a),
                 "" if not attribution or a.get("phases")
                 else "The model has no mission phases."),
                ("outage-anatomy", "What an outage looks like",
                 lambda: charts.outage_anatomy(a), ""),
                ("ensemble-ribbon", "Run-to-run state ribbon",
                 lambda: charts.ensemble_ribbon(a),
                 "" if not attribution or a.get("outages")
                 else "No outage timeline was kept for this run."),
                ("outage-histogram", "How long is an outage?",
                 lambda: charts.outage_histogram(a), ""),
                ("frequency-duration", "Frequency against duration",
                 lambda: charts.frequency_duration(a), "")):
            add(key, title, "Why it goes down", build, drivers_missing() or extra,
                action if not attribution else "")

        no_capacity = ("" if a.get("capacity") else
                       drivers_missing() or "Needs Capacity analysis: turn it on in "
                       "the System settings and run again.")
        for key, title, build in (
                ("lost-capacity-pareto", "Who loses the capacity",
                 lambda: charts.lost_capacity_pareto(a)),
                ("capacity-vs-downtime", "Downtime against lost capacity",
                 lambda: charts.capacity_vs_downtime(a)),
                ("capacity-waterfall", "Where the capacity goes",
                 lambda: charts.capacity_waterfall(a)),
                ("lost-capacity-treemap", "Lost capacity treemap",
                 lambda: charts.lost_capacity_treemap(a))):
            add(key, title, "Lost capacity", build, no_capacity,
                action if not attribution else "")

        no_years = ("" if periods else drivers_missing() or
                    "Needs a run at least one whole year long, in hours.")
        p = periods or {}
        add("annual-fan", "Availability year by year", "Year by year",
            lambda: charts.annual_fan(p), no_years, action if not attribution else "")
        add("annual-dots", "A bad year, as a frequency", "Year by year",
            lambda: charts.annual_dots(p), no_years, action if not attribution else "")
        add("annual-capacity-fan", "Capacity delivered year by year", "Year by year",
            lambda: charts.annual_capacity_fan(p),
            no_years or ("" if (p.get("capacity") or {}).get("by_index")
                         else "Needs Capacity analysis as well."),
            action if not attribution else "")

        has_cost = any(getattr(rd, "cost", None) is not None for rd in report_data)
        no_cost = "" if has_cost else ("Needs Cost analysis: turn it on in the "
                                       "System settings and run again.")
        add("cost-pareto", "Where the money goes", "Cost",
            lambda: charts.cost_pareto(charts.element_costs(report_data)), no_cost)
        add("cost-treemap", "Cost treemap", "Cost",
            lambda: charts.cost_treemap(charts.element_costs(report_data)), no_cost)
        add("importance-quadrant", "Importance quadrant", "What matters most",
            lambda: charts.importance_quadrant(importance or {}),
            "" if importance else "Needs importance measures: tick Importance "
            "under Run panel ▸ Analyses and run again.")
        return entries

    # -- showing a chart ------------------------------------------------

    def _safe(self, entry: _Entry) -> charts.ChartSpec | None:
        try:
            return entry.build() if entry.build is not None else None
        except Exception:  # noqa: BLE001 - one bad chart must not break the tab
            logging.getLogger(__name__).exception("could not build %s", entry.key)
            return None

    def _time_spec(self, key: str) -> charts.ChartSpec | None:
        run = self.run_box.value() if key in timecharts.PER_RUN_CHARTS else 0
        cache_key = (key, run, self._y_floor)
        if cache_key not in self._time_cache:
            i = self._inputs
            per_run = i.get("per_run") or []
            ends = [row.get("ending_sim_time") for row in per_run]
            try:
                self._time_cache[cache_key] = timecharts.time_chart(
                    key, ao_runs=self._ao_runs, cap_runs=self._cap_runs,
                    run=max(1, run), units=i.get("units") or "",
                    target=i.get("target"), y_floor=self._y_floor or None,
                    generated=i.get("generated"),
                    ends=ends if any(e is not None for e in ends) else None)
            except ValueError as exc:
                self._show_missing(self._entries[key].title, str(exc), "")
                return None
        return self._time_cache[cache_key]

    def _on_current(self, current: QTreeWidgetItem | None, _previous) -> None:
        if current is not None and current.data(0, _KEY):
            self._wanted = str(current.data(0, _KEY))
        self._show_current()

    def _show_current(self) -> None:
        key = self.selected_key()
        entry = self._entries.get(key or "")
        self._sync_controls(entry)
        if entry is None:
            self.chart.set_spec(None)
            self.stack.setCurrentWidget(self.chart)
            return
        if entry.missing:
            self._show_missing(entry.title, entry.missing, entry.action)
            return
        spec = (self._time_spec(entry.key) if entry.key in timecharts.TIME_CHARTS
                else self._specs.get(entry.title))
        if spec is None:
            if self.stack.currentIndex() == 0:
                self._show_missing(entry.title, "This chart cannot be drawn from "
                                   "this result.", "")
            return
        self.fold_box.setVisible(charts.foldable(spec))
        if self.fold_share > 0:
            spec = charts.fold_small(spec, self.fold_share)
        self.chart.set_spec(spec)
        self.chart.setToolTip(spec.caption)
        self.stack.setCurrentWidget(self.chart)

    def _show_missing(self, title: str, text: str, action: str) -> None:
        self.chart.set_spec(None)
        self.missing_title.setText(title)
        self.missing_text.setText(text)
        self.missing_action.setText(action)
        self.missing_action.setVisible(bool(action))
        self.stack.setCurrentIndex(1)

    def _sync_controls(self, entry: _Entry | None) -> None:
        key = entry.key if entry is not None and not entry.missing else ""
        per_run = key in timecharts.PER_RUN_CHARTS
        runs = len(self._cap_runs if key.startswith("capacity") else self._ao_runs)
        self.run_box.blockSignals(True)
        self.run_box.setMaximum(max(1, runs))
        self.run_box.blockSignals(False)
        self.run_of.setText(f"of {runs:,}" if runs else "")
        for widget in (self.run_label, self.run_box, self.run_of):
            widget.setVisible(per_run)
        floor = key in ("availability-through-run", "interval-availability",
                        "availability-band")
        self.floor_button.setVisible(floor)
        self.fold_box.setVisible(False)      # _show_current shows it when it applies
        self._sync_floor_text()
        self.export_button.setEnabled(bool(key))
        self.raw_action.setVisible(key in timecharts.TIME_CHARTS)

    @property
    def fold_share(self) -> float:
        """The share below which items are folded (0: show every item)."""
        return float(self.fold_box.currentData() or 0.0)

    @property
    def export_page(self) -> str:
        return self.chart.export_page

    def set_export_page(self, page: str) -> None:
        """The page saved and copied charts are drawn for: a bigger page
        gives long names more room (`charts_svg.PAGES`)."""
        self.chart.export_page = page
        if page in self.page_actions:
            self.page_actions[page].setChecked(True)

    def _sync_floor_text(self) -> None:
        self.floor_button.setText(
            "Axis from: auto" if self._y_floor <= 0 else f"Axis from: {self._y_floor:.4g}")

    def _ask_floor(self) -> None:
        from PySide6.QtWidgets import QInputDialog
        value, ok = QInputDialog.getDouble(
            self, "Availability axis", "Start the axis at (0 = fit to the data):",
            self._y_floor, 0.0, 0.999999, 6)
        if ok and 0.0 <= value < 1.0 and value != self._y_floor:
            self.set_y_floor(value)
            self.y_floor_changed.emit(value)

    # -- selection ------------------------------------------------------

    def selected_key(self) -> str | None:
        item = self.nav.currentItem()
        key = item.data(0, _KEY) if item is not None else None
        return str(key) if key else None

    def selected_title(self) -> str:
        entry = self._entries.get(self.selected_key() or "")
        return entry.title if entry is not None else ""

    def selected_spec(self) -> charts.ChartSpec | None:
        return self.chart.spec()

    def select(self, key_or_title: str) -> bool:
        """Show a chart by its CLI name or its title; False when it is not listed."""
        for item in self._chart_items():
            entry = self._entries[item.data(0, _KEY)]
            if key_or_title in (entry.key, entry.title):
                self.nav.setCurrentItem(item)
                return True
        return False

    def titles(self, *, available: bool = True) -> list[str]:
        """The listed chart titles (only the drawable ones by default)."""
        return [e.title for e in self._entries.values()
                if not available or not e.missing]

    def missing_reason(self, key_or_title: str) -> str:
        entry = next((e for e in self._entries.values()
                      if key_or_title in (e.key, e.title)), None)
        return entry.missing if entry is not None else ""

    # -- actions --------------------------------------------------------

    def copy_image(self) -> None:
        self.chart.copy_image()

    def save_svg(self, path: str) -> None:
        self.chart.save_svg(path)

    def save_png(self, path: str) -> None:
        self.chart.save_png(path)

    def save_pdf(self, path: str) -> None:
        self.chart.save_pdf(path)

    def save_csv(self, path: str) -> None:
        self.chart.save_csv(path)

    def _ask(self, caption: str, filters: str) -> str:
        stem = (self.selected_key() or "chart")
        path, _ = QFileDialog.getSaveFileName(self, caption, stem, filters)
        return path

    def _ask_svg(self) -> None:
        if path := self._ask("Save chart", "SVG image (*.svg)"):
            self.save_svg(path)

    def _ask_png(self) -> None:
        if path := self._ask("Save chart", "PNG image (*.png)"):
            self.save_png(path)

    def _ask_pdf(self) -> None:
        if path := self._ask("Save chart", "PDF document (*.pdf)"):
            self.save_pdf(path)

    def _ask_csv(self) -> None:
        if path := self._ask("Save chart data", "CSV (*.csv)"):
            self.save_csv(path)

    def _ask_raw(self) -> None:
        key = self.selected_key() or ""
        self.raw_export_requested.emit("capacity" if key.startswith("capacity")
                                       else "availability")


def _with_run_id(spec: charts.ChartSpec, inputs: dict[str, Any]) -> charts.ChartSpec:
    spec.provenance["run_id"] = inputs.get("run_id") or ""
    return spec


def _ladder(per_run: list[dict]) -> dict[str, Any]:
    from raptor.api.insight.measures import availability_ladder
    return availability_ladder(per_run)


def _operational(per_run: list[dict]) -> float | None:
    if not per_run:
        return None
    return _ladder(per_run)["operational"]["value"]
