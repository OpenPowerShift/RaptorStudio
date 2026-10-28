"""Simulation results dock — plain-English, interactive results views.

Presents what `runner.run_simulation_with_reports` produces; no
simulation logic lives here.

Five tabs:
- **Overview**: metric cards, a green/yellow/red system-health bar, and a
  summary paragraph, built from the engine's structured `RunReportData`
  (no text scraping). Acronym tooltips come from the Raptor 7 manual's
  Appendix F definitions.
- **Final Results**: the report's cross-run statistics tables, split into
  sub-tabs (section -> row -> Min/Mean/Max/St.Dev/SEM), with plain-English
  tooltips on row labels and column headers.
- **Event Log**: the per-run event trace as a colour-coded table. Clicking
  a Block/Node row selects that element on the diagram canvas.
- **Graphs**: Ao and capacity plots fed by the engine's plot sinks.
- **Report Text**: the engine's unchanged report text, the fidelity view that
  everything above is presentation layered on top of.

Report text mirrors Raptor 7; the
checked-in models under `tests/data/models/` exercise the result shapes.
"""
from __future__ import annotations

import re

from raptor.gui.ui.qtcompat import req

from PySide6.QtWidgets import QAbstractItemView

from raptor.gui.ui import msgbox

from datetime import datetime
from typing import cast

from PySide6.QtCore import Qt, QTimer, Signal
from PySide6.QtGui import QColor, QFont, QStandardItemModel
from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QGridLayout, QTabWidget, QLabel,
    QPlainTextEdit, QComboBox, QLineEdit, QTreeView, QTreeWidget,
    QTreeWidgetItem,
    QFrame, QSizePolicy, QScrollArea, QTableWidget, QHeaderView,
    QTableWidgetItem, QApplication,
)

from raptor.api.tables import parse_final_sections
from raptor.core.engine import weak_metric
from raptor.core.engine.reports import flow_loss_across_runs
from raptor.gui.ui import filedialogs as fdg
from raptor.gui.ui.event_log_model import HEADERS, EventLogModel
from raptor.gui.resources.design_tokens import ThemeManager, SPACING, TYPE, active_colour


def _mono_font() -> QFont:
    f = QFont("Consolas, Menlo, Monaco, monospace")
    f.setStyleHint(QFont.StyleHint.Monospace)
    f.setPointSize(10)
    return f


#: Per-item storage for a cell's unrounded value alongside its formatted
#: display text, so "Copy Table" gets full precision even though the grid
#: shows fewer digits. Column-0 element names keep their un-elided full
#: string in the plain Qt.UserRole (filtering/click-to-select read that).
RAW_VALUE_ROLE = Qt.ItemDataRole.UserRole + 1
#: Marks a cell whose numeric value is a lower bound (the report's ``>``
#: censor qualifier). Copy/CSV write the number plus a parallel flag.
CENSOR_ROLE = Qt.ItemDataRole.UserRole + 2


def _cell_parts(val):
    """Accept a display string or a (display, raw_value[, censored]) tuple.

    A censored cell keeps its numeric value in the raw slot and marks the
    grid text with ``>``; Copy/CSV need both (a number a spreadsheet can
    use, plus a flag saying it is a lower bound)."""
    if isinstance(val, tuple):
        if len(val) >= 3:
            return val[0], val[1], bool(val[2])
        return val[0], val[1], False
    return val, None, False


def _copy_text(val) -> str:
    """Clipboard text for a cell: the RAW value at full precision when
    one was stashed (str() of a Python float is the shortest string that
    round-trips, i.e. full double precision), else the display text."""
    _, raw, _censored = _cell_parts(val)
    if raw is None:
        return _cell_parts(val)[0]
    return str(raw)


#: Plain-English meanings (Raptor 7 manual, Appendix F "Raptor
#: Definitions"). Keys match the report rows' own labels.
GLOSSARY = {
    "Availability": (
        "Operational Availability (Ao)\n\nFraction of total time the system "
        "was at least PARTIALLY working (green + yellow time).\n\n"
        "Ao = Uptime / Total Time"),
    "Conditional reliability": (
        "Conditional Reliability (RC)\n\nOf the missions that STARTED "
        "with the system up, the fraction completed without a failure."),
    "MTBDE": (
        "Mean Time Between Downing Events\n\nAverage working time between "
        "events that down the system — fully or partially.\n\n"
        "MTBDE = Uptime / Number of Downing Events"),
    "MDT": (
        "Mean Down Time\n\nAverage time the system is FULLY down (red) per "
        "downing event.\n\nMDT = Downtime / Number of Downing Events"),
    "Reliability": (
        "Reliability (R)\n\nFraction of missions completed WITHOUT a system "
        "failure.\n\nR = Successful Missions / Missions Attempted"),
    "Conditional_Reliability": (
        "Conditional Reliability (RC)\n\nOf the missions that STARTED with "
        "the system up, the fraction completed without a failure."),
    "Dependability": (
        "Dependability (Do)\n\nFraction of MISSION time spent at least "
        "partially working."),
    "MTBM": (
        "Mean Time Between Maintenance\n\nAverage working time between ANY "
        "maintenance event — repairs AND preventive maintenance.\n\n"
        "MTBM = Uptime / Number of Maintenance Events"),
    "MTBMu": (
        "Mean Time Between Maintenance, Unscheduled\n\nAverage working time "
        "between failures (unscheduled maintenance only)."),
    "MTBMs": (
        "Mean Time Between Maintenance, Scheduled\n\nAverage working time "
        "between preventive-maintenance events."),
    "MMT": (
        "Mean Maintenance Time\n\nAverage duration of a maintenance event "
        "(includes logistics delays and waiting for spares/resources)."),
    "MMTu": (
        "Mean Maintenance Time, Unscheduled\n\nAverage duration of a repair."),
    "MMTs": (
        "Mean Maintenance Time, Scheduled\n\nAverage duration of preventive "
        "maintenance."),
    "Green_Percent": "Percent of time the system was FULLY working.",
    "Yellow_Percent": "Percent of time the system was DEGRADED (partially working).",
    "Red_Percent": "Percent of time the system was FULLY DOWN.",
    "System_Failures": (
        "Number of System Failures\n\nHow many times the system went fully "
        "down (a red event) per run."),
    "Component_Failures": "How many times individual blocks failed per run.",
    "Total Cost": (
        "Total Cost\n\nThe run's whole life-cycle cost (blocks + events + "
        "sparing + resources + system-down cost + lost-flow cost) — the "
        "Cost tab's Grand Total, one sample per run."),
}

#: Display labels used by the Advanced table that are not the report's
#: own section names.
GLOSSARY.update({
    "System failures": GLOSSARY["System_Failures"],
    "Component failures": GLOSSARY["Component_Failures"],
    "Green %": GLOSSARY["Green_Percent"],
    "Yellow %": GLOSSARY["Yellow_Percent"],
    "Red %": GLOSSARY["Red_Percent"],
})

STAT_COLUMN_TOOLTIPS = {
    "Minimum": "Lowest value observed across the runs.",
    "Min": "Lowest value observed across the runs.",
    "Max": "Highest value observed across the runs.",
    "Std. dev.": "Sample standard deviation — how much the per-run values spread out.",
    "N": "Number of runs that contributed a value to this measure.",
    "Mean": "Average value across the runs.",
    "Maximum": "Highest value observed across the runs.",
    "St.Dev": "Sample standard deviation — how much the per-run values spread out.",
    "SEM": "Standard Error of the Mean — how much this average would wobble "
           "if the simulation were repeated. Smaller = more precise estimate.",
    "Min Flow (worst)": "Lowest flow in the worst run — the envelope across "
                        "runs, useful for capacity planning.",
    "Min Flow (avg runs)": "Average of each run's own minimum flow — Raptor "
                           "7's Final Results aggregation.",
    "Max Flow (avg runs)": "Average of each run's own maximum flow — Raptor "
                           "7's Final Results aggregation.",
    "Max Flow (best)": "Highest flow in the best run — the envelope across "
                       "runs.",
    "Min Cap (worst)": "Lowest capacity in the worst run — the envelope "
                       "across runs.",
    "Min Cap (avg runs)": "Average of each run's own minimum capacity — "
                          "Raptor 7's Final Results aggregation.",
    "Max Cap (avg runs)": "Average of each run's own maximum capacity — "
                          "Raptor 7's Final Results aggregation.",
    "Max Cap (best)": "Highest capacity in the best run — the envelope "
                      "across runs.",
}


#: Header tooltips for the Importance tab, in column order. Each says what
#: the measure ANSWERS, which is the part an acronym hides; the equations
#: and the "why this ranking" argument live in `docs/formulas.adoc` (Help >
#: Formulas), so this is a reading of the column, not a second definition.
IMPORTANCE_HEADER_TIPS = (
    "The block's name. Hover it for this block's own numbers: the system "
    "with the block unable to fail, with it dead, and the standard error "
    "of the difference.",
    "Birnbaum: how far the SYSTEM would move if this block's own "
    "availability moved — system with the block unable to fail, minus "
    "system with it dead. Highest value = the block the system is most "
    "sensitive to. Saturates near 1 in a series model, so it is not what "
    "the list is ranked by.",
    "RAW — Risk Achievement Worth: the system's downtime with this block "
    "dead, divided by its downtime now. 1 means no effect; 10 means ten "
    "times the downtime if it fails. Undefined (—) when the ordinary batch "
    "never failed.",
    "RRW — Risk Reduction Worth: downtime now divided by downtime with this "
    "block unable to fail. 1 means nothing to gain; 2 means half the "
    "downtime. Undefined (—) when the ordinary batch never failed.",
    "Vesely–Fussell: the share of the current downtime this block is "
    "responsible for. This is what the list is ranked by: it separates "
    "blocks that Birnbaum cannot, and RRW agrees with it. Undefined (—) "
    "when the ordinary batch never failed.",
)


def _fmt(v: float | None) -> str:
    if v is None:
        return "N/A"
    return f"{v:,.4g}"


#: Engineering-notation prefixes for large magnitudes (exponent a
#: multiple of 3), largest first -- SI's own standard set. Sub-1 values
#: are deliberately left without the milli/micro/nano treatment: in this
#: domain (reliability/maintainability time quantities) a
#: fractional-hour duration like "0.5 hours" is standard and readable,
#: while "500m hours" would be technically correct SI but reads
#: unnaturally for a plain decimal fraction of the base unit.
_SI_PREFIXES = [(1e12, "T"), (1e9, "G"), (1e6, "M"), (1e3, "k")]


def _fmt_si(v: float | None) -> str:
    """Engineering-notation formatting for a headline number that can
    span a huge range (MTBDE/MDT/MTBM/MMT: anywhere from fractions of an
    hour to hundreds of thousands), where `1.235M` reads more naturally
    than a raw `1,234,567` or `1.235e+06`. Falls back to plain `_fmt`
    (comma-grouped) below 1000."""
    if v is None:
        return "N/A"
    av = abs(v)
    for scale, prefix in _SI_PREFIXES:
        if av >= scale:
            return f"{v / scale:,.4g}{prefix}"
    return _fmt(v)


def _mean(values: list[float]) -> float | None:
    vals = [v for v in values if v is not None]
    return sum(vals) / len(vals) if vals else None


#: Raptor 7 marks likely-poor estimates with a greater-than sign
#: (`gtNote1`/`xRunsNote1`). That marker is kept
#: verbatim and coloured red, with the explanation on hover.
_GT_NOTE = ("Note: Parameters marked with a greater than sign are likely "
            "to be poor estimators.")

#: The card's ">" in plain words (Raptor 7's report says only `_GT_NOTE`).
_GT_EXPLAINED = (
    "\">\" marks a rough estimate: some runs gave no value for this "
    "measure (for example, the system never went fully down in them), so "
    "the average comes from fewer runs than were simulated, or from a "
    "fallback. Run more trials, or a longer run, for a firmer figure.\n\n"
    + _GT_NOTE)

_GT_XRUNS_NOTE = ("Note: Some parameter calculations are based on less "
                  "than the specified number of trials.")


def _fmt_count(v: float | None) -> str:
    """An average count: 0.02 stays 0.02 (a one-decimal "0.0" read as
    none), larger ones to a sensible precision."""
    if v is None:
        return "—"
    if v == 0:
        return "0"
    return f"{v:,.1f}" if abs(v) >= 100 else f"{v:.3g}"


def _pct(v: float) -> str:
    """A share of time: "under 0.1%" rather than a "0.0%" that is not 0."""
    if 0 < v < 0.05:
        return "under 0.1%"
    if 99.95 < v < 100:
        return "over 99.9%"
    return f"{v:.1f}%"


class _FitTable(QTableWidget):
    """A results table as wide as its columns (plus its scrollbar when it
    has one), so it hugs its content and never clips the last column."""

    def sizeHint(self):  # noqa: N802 (Qt override)
        hint = super().sizeHint()
        width = self.horizontalHeader().length() + 2 * self.frameWidth()
        if self.verticalScrollBarPolicy() != \
                Qt.ScrollBarPolicy.ScrollBarAlwaysOff:
            width += self.verticalScrollBar().sizeHint().width()
        hint.setWidth(width + 2)
        return hint


class _MetricCard(QFrame):
    """One headline number: acronym, big value, plain-English caption,
    and a min–max range across runs."""

    def __init__(self, acronym: str, caption: str, parent=None):
        super().__init__(parent)
        self.setObjectName("MetricCard")
        self.setToolTip(GLOSSARY.get(acronym, acronym))
        lay = QVBoxLayout(self)
        lay.setContentsMargins(SPACING["md"], SPACING["sm"], SPACING["md"], SPACING["sm"])
        lay.setSpacing(2)
        self._title = QLabel(acronym.replace("_", " "))
        self._title.setObjectName("MetricTitle")
        lay.addWidget(self._title)
        self._value = QLabel("—")
        self._value.setObjectName("MetricValue")
        lay.addWidget(self._value)
        self._range = QLabel("")
        self._range.setObjectName("MetricRange")
        self._range.setToolTip(
            "Lowest and highest value observed across the individual runs.")
        lay.addWidget(self._range)
        self._caption = QLabel(caption)
        self._caption.setObjectName("MetricCaption")
        self._caption.setWordWrap(True)
        lay.addWidget(self._caption)

    def set_value(self, text: str, range_text: str = "",
                  censored: bool = False):
        """Set the metric value (and optional range) text.

        `censored`: the cross-run estimate was `>`-censored by the report
        builder (some runs never had a downing event), so the card shows
        the mean with the greater-than prefix, coloured red, and the
        explanatory note on hover."""
        if censored:
            self._value.setTextFormat(Qt.TextFormat.RichText)
            self._value.setText(
                f"<span style='color: {active_colour()['result_red']}; "
                f"font-weight: 700;'>&gt;</span>&nbsp; {text}")
            self._value.setToolTip(_GT_EXPLAINED)
            # Said on the card, not only on hover: a red ">" alone read
            # as a mistake.
            range_text = (f"{range_text} · " if range_text else "") + \
                "> rough estimate: not every run gave a value"
        else:
            self._value.setTextFormat(Qt.TextFormat.PlainText)
            self._value.setText(text)
            self._value.setToolTip("")
        self._range.setText(range_text)

    def set_title(self, acronym: str, caption: str | None = None) -> None:
        """Retitle the card (and its tooltip/glossary lookup) -- the
        Availability/Dependability card switches between the two based
        on the model's configured `nodeAnalType`. The two metrics are
        genuinely different numbers."""
        self._title.setText(acronym.replace("_", " "))
        self.setToolTip(GLOSSARY.get(acronym, acronym))
        if caption is not None:
            self._caption.setText(caption)


class ResultsDock(QWidget):
    #: ("block"|"node", element_NAME) -- the user clicked an event-log row.
    #: The log stores names, not IDs; MainWindow resolves the name against
    #: the loaded model to make the canvas selection.
    element_clicked = Signal(str, str)
    #: A result set arrived or the dock was cleared (`has_results()` may
    #: have changed). The window uses it to tell the Inspector whether an
    #: edit will cost the user their results.
    contents_changed = Signal()
    #: The "Export Tables..." button: open the window's Export Tables dialog.
    export_tables_requested = Signal()
    #: User switched Raptor 7 Analysis Type in the Weak Link tab.
    weak_analysis_type_changed = Signal(str)
    #: The metric the Importance tab is measured on changed. The canvas
    #: overlay colours by that metric, so it has to re-apply -- otherwise
    #: the diagram keeps showing Availability while the table says
    #: Reliability and the status line still says "on Availability".
    importance_metric_changed = Signal(str)
    #: The user set the Ao graphs' Y-axis minimum (written to `graphYmin`, as Raptor 7 does).
    y_axis_min_changed = Signal(float)

    def __init__(self, parent=None):
        super().__init__(parent)
        self._event_logs: list[list[str]] = []
        self._report_data = []
        self._final_lines: list[str] = []
        self._eor_lines: list[str] = []
        self._units: str = ""
        self._allowed_names: set[str] | None = None
        self._ao_lines: list[str] = []
        # Whether THIS batch's live capacity view already took over the
        # tab switch (reset at every set_results; see update_live_capacity).
        self._live_cap_autoswitch = False

        self._placeholder = QLabel("No results yet — click Run (F5).")
        self._placeholder.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._placeholder.setObjectName("ResultsPlaceholder")
        self._notes: list[str] = []
        self._source_file_name: str = ""

        self._tabs = QTabWidget()
        self._tabs.setDocumentMode(True)
        self._build_overview_tab()
        self._build_details_tab()
        self._build_event_log_tab()
        self._build_graphs_tab()
        self._build_raw_tab()
        self._tabs.addTab(self._overview_page, "Overview")
        self._tabs.addTab(self._details_holder, "Final Results")
        self._tabs.addTab(self._event_page, "Event Log")
        self._tabs.addTab(self._graphs_page, "Graphs")
        self._tabs.addTab(self._raw_page, "Report Text")
        # The whole results dock is sized by the TALLEST page, because a
        # QTabWidget's stacked layout takes the max over its pages -- so a
        # single tall page (the Graphs plots) floors the bottom splitter
        # and the user cannot drag the Results strip shorter after a run.
        # `SetNoConstraint` is the layout-level way to say "reserve
        # nothing": it makes the layout report a zero minimum regardless
        # of its children. A QTabWidget hides every page but the current
        # one anyway, and a plot or a table repaints at whatever height it
        # is given, so no page needs to reserve space.
        from PySide6.QtWidgets import QLayout as _QLayout
        for i in range(self._tabs.count()):
            page = self._tabs.widget(i)
            page_lay = page.layout() if page is not None else None
            if page_lay is not None:
                page_lay.setSizeConstraint(_QLayout.SizeConstraint.SetNoConstraint)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.addWidget(self._placeholder)
        layout.addWidget(self._tabs, 1)
        self._tabs.hide()

        ThemeManager.instance().on_changed(self._refresh_theme)
        self._refresh_theme()

    # ------------------------------------------------------------------
    # Tab construction
    # ------------------------------------------------------------------

    def _build_overview_tab(self):
        page = QWidget()
        outer = QVBoxLayout(page)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(0)
        # Scroll rather than crush: a scroll area keeps each card at its
        # designed size when the dock strip is short.
        from PySide6.QtWidgets import QScrollArea
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        # A QScrollArea's own minimumSizeHint still propagates its widget's
        # minimum up to the dock, so without this the bottom splitter is
        # floored by the Overview page and cannot be dragged shorter --
        # i.e. "after running, I can't reduce the size of the results
        # pane". The page scrolls instead.
        scroll.setMinimumHeight(0)
        body = QWidget()
        body.setMinimumHeight(0)
        scroll.setWidget(body)
        outer.addWidget(scroll)

        lay = QVBoxLayout(body)
        lay.setContentsMargins(SPACING["lg"], SPACING["md"], SPACING["lg"], SPACING["md"])
        lay.setSpacing(SPACING["md"])

        self._summary_sentence = QLabel("")
        self._summary_sentence.setWordWrap(True)
        self._summary_sentence.setObjectName("SummarySentence")
        self._summary_sentence.setToolTip(
            "A plain-English reading of the averages across all runs.")
        lay.addWidget(self._summary_sentence)

        # What this result set IS -- the run manifest, in one line. Above
        # the numbers, because the numbers are only meaningful with it.
        self._manifest_label = QLabel("")
        self._manifest_label.setWordWrap(True)
        self._manifest_label.setObjectName("SectionNote")
        self._manifest_label.setAccessibleName("Run settings for this result set")
        self._manifest_label.setVisible(False)
        lay.addWidget(self._manifest_label)

        cards = QGridLayout()
        cards.setHorizontalSpacing(SPACING["sm"])
        cards.setVerticalSpacing(SPACING["sm"])
        specs = [
            ("Availability", "fraction of time working (full precision)"),
            ("Reliability", "missions completed without a failure / "
                            "missions attempted"),
            ("MTBDE", "avg. time between downings"),
            ("MDT", "avg. time fully down"),
            ("MTBM", "avg. time between maintenance"),
            ("MMT", "avg. maintenance duration"),
            ("System_Failures", "full failures per run"),
        ]
        self._cards: dict[str, _MetricCard] = {}
        for i, (acronym, caption) in enumerate(specs):
            card = _MetricCard(acronym, caption)
            # Width only. A height minimum here is what actually floored the
            # results dock: seven cards at three per row is three rows of
            # hard minimum, and the page's minimum propagates out through
            # the scroll area to the dock, so the splitter could not go
            # below ~330px. The card's sizeHint still gives it its
            # designed height when there is room; the page scrolls when
            # there is not.
            card.setMinimumWidth(172)
            self._cards[acronym] = card
            cards.addWidget(card, i // 3, i % 3)
        lay.addLayout(cards)

        # Green/Yellow/Red health bar -- DISCRETE segments sized to the
        # simulated percentages (not a decorative gradient), with a legend
        # naming each colour's meaning.
        gyr_row = QHBoxLayout()
        gyr_row.setSpacing(SPACING["sm"])
        self._gyr_label = QLabel("System health")
        self._gyr_label.setObjectName("GYRLabel")
        self._gyr_label.setToolTip(
            "How the simulated time was split between the system states "
            "(averaged across runs):\nGreen = fully working · "
            "Yellow = degraded, still partially working · Red = fully down.")
        gyr_row.addWidget(self._gyr_label)
        bar_holder = QWidget()
        bar_lay = QHBoxLayout(bar_holder)
        bar_lay.setContentsMargins(0, 0, 0, 0)
        bar_lay.setSpacing(2)
        self._gyr_segments: dict[str, QFrame] = {}
        for state in ("operating", "degraded", "failed"):
            seg = QFrame()
            seg.setObjectName(f"GYR_{state}")
            seg.setFixedHeight(16)
            seg.setMinimumWidth(0)
            self._gyr_segments[state] = seg
            # Stretch factor 1 so each starts equal; _style_gyr_bar then
            # sets actual proportions from the simulated percentages.
            bar_lay.addWidget(seg, 1)
        self._gyr_lay = bar_lay
        gyr_row.addWidget(bar_holder, 1)
        lay.addLayout(gyr_row)

        legend = QHBoxLayout()
        legend.setSpacing(SPACING["lg"])
        self._gyr_legend_labels: dict[str, QLabel] = {}
        for key, _colour_key, _meaning in (
            ("green", "operating", "Fully working"),
            ("yellow", "degraded", "Degraded (partially working)"),
            ("red", "failed", "Fully down"),
        ):
            lbl = QLabel()
            lbl.setObjectName(f"Legend_{key}")
            lbl.setToolTip(GLOSSARY.get({
                "green": "Green_Percent", "yellow": "Yellow_Percent",
                "red": "Red_Percent"}[key], ""))
            self._gyr_legend_labels[key] = lbl
            legend.addWidget(lbl)
        legend.addStretch(1)
        lay.addLayout(legend)

        # Mission Green/Yellow/Red (Raptor 7 "Phased Logistics
        # Results" -- MISSION time only, a subset of the all-encompassing
        # bar above, computed only for phased models with mission tracking).
        # Same discrete-segment/legend structure, hidden entirely for a
        # non-phased model (no `mission_green_percent`, nothing to show).
        self._mission_gyr_container = QWidget()
        mission_col = QVBoxLayout(self._mission_gyr_container)
        mission_col.setContentsMargins(0, 0, 0, 0)
        mission_col.setSpacing(SPACING["sm"])

        mission_row = QHBoxLayout()
        mission_row.setSpacing(SPACING["sm"])
        mission_label = QLabel("Mission health")
        mission_label.setObjectName("MissionGYRLabel")
        mission_label.setToolTip(
            "How the MISSION-time portion of the run was split between "
            "the system states (phased logistics -- averaged "
            "across runs):\nGreen = fully working · Yellow = degraded, "
            "still partially working · Red = fully down.")
        mission_row.addWidget(mission_label)
        mission_bar_holder = QWidget()
        mission_bar_lay = QHBoxLayout(mission_bar_holder)
        mission_bar_lay.setContentsMargins(0, 0, 0, 0)
        mission_bar_lay.setSpacing(2)
        self._mission_gyr_segments: dict[str, QFrame] = {}
        for state in ("operating", "degraded", "failed"):
            seg = QFrame()
            seg.setObjectName(f"MissionGYR_{state}")
            seg.setFixedHeight(16)
            seg.setMinimumWidth(0)
            self._mission_gyr_segments[state] = seg
            mission_bar_lay.addWidget(seg, 1)
        self._mission_gyr_lay = mission_bar_lay
        mission_row.addWidget(mission_bar_holder, 1)
        mission_col.addLayout(mission_row)

        mission_legend = QHBoxLayout()
        mission_legend.setSpacing(SPACING["lg"])
        self._mission_gyr_legend_labels: dict[str, QLabel] = {}
        for key in ("green", "yellow", "red"):
            lbl = QLabel()
            lbl.setObjectName(f"MissionLegend_{key}")
            self._mission_gyr_legend_labels[key] = lbl
            mission_legend.addWidget(lbl)
        mission_legend.addStretch(1)
        mission_col.addLayout(mission_legend)

        self._mission_gyr_container.setVisible(False)
        lay.addWidget(self._mission_gyr_container)

        self._runs_table = QTreeWidget()
        self._runs_table.setHeaderLabels(["Run", "Availability", "MTBDE", "MDT",
                                          "System Failures"])
        self._runs_table.setRootIsDecorated(False)
        self._runs_table.setUniformRowHeights(True)
        self._header_tooltips(self._runs_table)
        # No trailing addStretch here: combined with the table's own
        # stretch it makes the scroll-body layout oscillate under
        # re-polish. The table's stretch fills the body.
        lay.addWidget(self._runs_table, 1)
        body.setSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Expanding)
        self._overview_page = page

    @staticmethod
    def _header_tooltips(tree: QTreeWidget):
        # GLOSSARY covers acronym columns (MTBDE, MDT, ...);
        # STAT_COLUMN_TOOLTIPS covers the Minimum/Mean/... stat columns.
        # Checked in this order so a name present in both resolves to the
        # acronym expansion.
        item = tree.headerItem()
        for c in range(item.columnCount()):
            col = item.text(c)
            tip = GLOSSARY.get(col) or STAT_COLUMN_TOOLTIPS.get(col)
            if tip:
                item.setToolTip(c, tip)

    def _build_details_tab(self):
        # Sub-tabs, one per report section; Capacity gets its own tab
        # from the engine's structured capacity snapshot.
        self._details_tabs = QTabWidget()
        self._details_tabs.setDocumentMode(True)
        self._detail_tables: dict[str, QTableWidget] = {}
        self._details_tabs.currentChanged.connect(self._remember_details_tab)

        bar = QHBoxLayout()
        bar.setContentsMargins(SPACING["md"], SPACING["xs"], SPACING["md"], 0)
        from PySide6.QtWidgets import QPushButton
        copy_btn = QPushButton("Copy Table")
        copy_btn.setToolTip("Copy the current section's table as TSV "
                            "(pastes straight into Excel/Sheets)")
        copy_btn.clicked.connect(self._copy_current_table)
        export_btn = QPushButton("Export CSV…")
        export_btn.setToolTip(
            "Save every section (with file/run metadata) as a .csv for "
            "spreadsheet analysis")
        export_btn.clicked.connect(self._export_csv)
        tables_btn = QPushButton("Export Tables…")
        tables_btn.setToolTip(
            "Write every result table (and the model's inputs) as CSV, "
            "text or one spreadsheet with a tab per table")
        tables_btn.clicked.connect(self.export_tables_requested)
        # Stretch first so the buttons sit in the top-right corner rather
        # than trailing the row like stray labels.
        # Hierarchy selector: filters all result tables by a hierarchy
        # subtree, mirroring Raptor 7's per-dialog hierarchy selectors.
        # Drives the same level-filter path as canvas navigation via a
        # signal MainWindow subscribes to.
        self._hier_filter_label = QLabel("Hierarchy:")
        bar.addWidget(self._hier_filter_label)
        from PySide6.QtWidgets import QComboBox
        self._hier_filter_combo = QComboBox()
        self._hier_filter_combo.addItem("All", 0)
        self._hier_filter_combo.setToolTip(
            "Show only results for elements inside the chosen "
            "hierarchy")
        self._hier_filter_combo.setEnabled(False)
        self._hier_filter_combo.currentIndexChanged.connect(
            self._on_hier_filter_changed)
        bar.addWidget(self._hier_filter_combo)
        bar.addSpacing(10)
        bar.addStretch(1)
        bar.addWidget(copy_btn)
        bar.addWidget(export_btn)
        bar.addWidget(tables_btn)
        holder = QWidget()
        hv = QVBoxLayout(holder)
        hv.setContentsMargins(0, 0, 0, 0)
        hv.setSpacing(SPACING["xs"])
        hv.addLayout(bar)
        hv.addWidget(self._details_tabs, 1)
        self._details_holder = holder

    def _remember_details_tab(self, idx: int):
        if getattr(self, "_tab_memory_lock", False):
            return   # a programmatic rebuild must not clobber the user's choice
        if 0 <= idx < self._details_tabs.count():
            title = self._details_tabs.tabText(idx)
            self._last_details_tab = title
            # Summary/Logistics are system-wide; showing the hierarchy
            # selector above them implied they were filtered.
            self._update_hier_filter_visibility()

    #: User picked a hierarchy in the filter combo (id, or 0 = all)
    hierarchy_filter_changed = Signal(int)

    def set_hierarchy_options(self, hiers: dict[int, str],
                              current: int = 0):
        """MainWindow pushes {hier_id: name} for the filter combo."""
        combo = self._hier_filter_combo
        combo.blockSignals(True)
        combo.clear()
        combo.addItem("All", 0)
        for hid in sorted(hiers):
            combo.addItem(hiers[hid], hid)
        ci = combo.findData(current)
        combo.setCurrentIndex(max(ci, 0))
        combo.blockSignals(False)
        self._update_hier_filter_visibility()

    def _update_hier_filter_visibility(self):
        """Show the selector only when it can actually do something:
        the model must have hierarchies AND the results must include at
        least one element-level table (Sparing / Capacity / Cost / Weak
        Link) whose rows a subtree filter can scope. Summary/Logistics
        are system-wide measures with no per-hierarchy view -- Raptor 7's
        own Summary/Logistics tabs carry no hierarchy filter
        either -- so results that stop there hide the selector (mirrors
         `FillResultsTable`'s `hiercount = 0` ->
        Deactivate)."""
        combo = self._hier_filter_combo
        idx = self._details_tabs.currentIndex()
        title = self._details_tabs.tabText(idx) if idx >= 0 else ""
        has = (title not in ("Summary", "Logistics", "Advanced")
               and combo.count() > 1 and self._has_per_hier_results())
        combo.setEnabled(has)
        combo.setVisible(has)
        self._hier_filter_label.setVisible(has)

    def _has_per_hier_results(self) -> bool:
        """True when any ELEMENT-level results table has rows. Measure
        tables (Summary/Logistics) are system-wide and never count --
        they have no per-hierarchy view to filter into."""
        from shiboken6 import isValid
        for title, t in self._detail_tables.items():
            if title in ("SUMMARY", "LOGISTICS"):
                continue
            if t is not None and isValid(t) and t.rowCount() > 0:
                return True
        cap = getattr(self, "_cap_table", None)
        if cap is not None and isValid(cap) and cap.rowCount() > 0:
            return True
        for t in getattr(self, "_cost_tables", []):
            if t is not None and isValid(t) and t.rowCount() > 0:
                return True
        for i in range(self._details_tabs.count()):
            if self._details_tabs.tabText(i) == "Weak Link":
                page = self._details_tabs.widget(i)
                from PySide6.QtWidgets import QTableWidget
                if any(w.rowCount() > 0
                       for w in req(page).findChildren(QTableWidget)):
                    return True
                break
        return False

    def _on_hier_filter_changed(self, index: int):
        hid = self._hier_filter_combo.itemData(index)
        self.hierarchy_filter_changed.emit(int(hid or 0))

    @staticmethod
    def _alive(w) -> bool:
        from shiboken6 import isValid
        return w is not None and isValid(w)

    def set_domains(self, domains: dict[int, dict]) -> None:
        """Store model domains (now shown via the ribbon Domains dialog,
        not a sub-tab here). MainWindow reads them back when opening."""
        self._domains_data = domains or {}



    def _copy_current_table(self) -> None:
        # Tab titles are Title-case ("Summary") but the table registry is
        # keyed by the raw section name ("SUMMARY"), so the lookup
        # upper-cases the title first.
        key = self._details_tabs.tabText(
            self._details_tabs.currentIndex()).upper()
        table = self.section_table(key)
        if table is None:
            # Structured tabs (Capacity/Cost/Weak Link) keep their tables
            # OUTSIDE the section registry -- find the visible one in the
            # current sub-tab instead.
            page = self._details_tabs.currentWidget()
            if page is not None:
                candidates = [t for t in page.findChildren(QTableWidget)
                              if t.rowCount() > 0]
                if candidates:
                    # Prefer the table the user is actually in (a cell
                    # widget's focus chain reaches its table), then one
                    # with a selected row; only then fall back to the
                    # first table on the page. Copying the first Cost
                    # table while looking at another was misleading.
                    focused = None
                    w = QApplication.focusWidget()
                    while w is not None:
                        if isinstance(w, QTableWidget):
                            focused = w
                            break
                        w = w.parent()
                    selected = [t for t in candidates
                                if t.currentRow() >= 0 or t.selectedItems()]
                    table = (focused if focused in candidates else
                             selected[0] if selected else candidates[0])
        if table is None or table.rowCount() == 0:
            return
        # DELEGATED (review E-11). This was the ONLY one of six extraction
        # sites that honoured RAW_VALUE_ROLE, so *Copy Table* gave the exact
        # double while *Export CSV*, *Print/PDF* and the AsciiDoc report gave
        # the rounded grid text -- for the same table. `table_to_grid` now
        # does the extraction once, with `raw=True` for every caller.
        from raptor.gui.ui.table_export import (
            grid_to_html, grid_to_tsv, table_to_grid)
        headers, rows = table_to_grid(table, raw=True)
        tsv = grid_to_tsv(headers, rows)
        # Excel pastes best from an HTML <table> (preserves cells); plain
        # TSV rides along for everything else. Use the one serialiser, so
        # a cell containing &, < or > cannot produce malformed markup.
        from PySide6.QtCore import QMimeData
        mime = QMimeData()
        mime.setText(tsv)
        mime.setHtml(grid_to_html(headers, rows))
        QApplication.clipboard().setMimeData(mime)

    def _export_csv(self) -> None:
        import csv

        path, _ = fdg.getSaveFileName(
            self, "Export Results CSV", "results.csv", "CSV (*.csv)")
        if not path:
            return
        with open(path, "w", newline="", encoding="utf-8") as f:
            writer = csv.writer(f)
            # Provenance block: everything needed to identify this data later.
            writer.writerow(["Model file", self._source_file_name or "?"])
            writer.writerow(["Exported", datetime.now().isoformat(timespec="seconds")])
            writer.writerow(["Runs", len(self._report_data)])
            if self._report_data:
                st = self._report_data[0].ending_sim_time
                writer.writerow([f"Sim time per run{', ' + self._units if self._units else ''}", st])
            writer.writerow([])
            # DELEGATED (review E-11): this read `item(r, c).text()`
            # unconditionally, so Export CSV and Copy Table disagreed about
            # precision for the same table -- one wrote the exact double,
            # the other the rounded display text.
            from raptor.gui.ui.table_export import table_to_grid
            exported: set[int] = set()

            def write_table(label: str, table: QTableWidget) -> None:
                headers, rows = table_to_grid(table, raw=True)
                writer.writerow([f"# {label}"])
                writer.writerow(headers)
                writer.writerows(rows)
                writer.writerow([])

            for title in list(self._detail_tables):
                table = self._detail_tables[title]
                write_table(title.title(), table)
                exported.add(id(table))
            # The dedicated Capacity/Cost/Weak Link pages keep their
            # tables outside `_detail_tables`, so the old export silently
            # dropped the very analyses the user was looking at.
            for i in range(self._details_tabs.count()):
                page = self._details_tabs.widget(i)
                if page is None:
                    continue
                page_title = self._details_tabs.tabText(i)
                tables = [t for t in page.findChildren(QTableWidget)
                          if id(t) not in exported and t.rowCount() > 0]
                for j, table in enumerate(tables, start=1):
                    label = (page_title if len(tables) == 1
                             else f"{page_title} ({j})")
                    write_table(label, table)

    @staticmethod
    def _stat_col_tip(name: str) -> str:
        return STAT_COLUMN_TOOLTIPS.get(
            name, STAT_COLUMN_TOOLTIPS.get(name.rstrip("1234"), ""))

    @staticmethod
    def _scroll_wrap(widget: QWidget) -> QScrollArea:
        """Let `widget` (and any table inside it) grow to its full
        natural height and have this scroll area handle overflow, instead
        of a table's own small internal scrollbar."""
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        # `setMinimumHeight(0)` is load-bearing, not tidiness: a scroll
        # area otherwise propagates its widget's minimum up to the dock,
        # so a long page floors the bottom splitter and the user cannot
        # drag the Results strip shorter. This way the page scrolls.
        scroll.setMinimumHeight(0)
        scroll.setWidget(widget)
        return scroll

    #: Display elision threshold. The full name stays in the item's data
    #: (filtering, click-to-select and tooltips all use it); only the
    #: shown text is elided.
    NAME_ELLIPSIS_AT = 25

    @classmethod
    def _elide(cls, name: str) -> str:
        if len(name) <= cls.NAME_ELLIPSIS_AT:
            return name
        cut = cls.NAME_ELLIPSIS_AT - 1
        return name[:cut] + "…"

    #: Rows a results table shows before it needs its own scrollbar.
    _TABLE_ROWS_SHOWN = 40

    def _make_table(self, headers: list[str], rows: list[list[str]],
                    first_col_label: str = "Measure") -> QTableWidget:
        table = _FitTable(len(rows), len(headers))
        table.setHorizontalHeaderLabels(headers)
        bold = QFont()
        bold.setBold(True)
        for c, _h in enumerate(headers):
            item = table.horizontalHeaderItem(c)
            if item is not None:
                item.setFont(bold)
        for r, row_vals in enumerate(rows):
            for c, val in enumerate(row_vals):
                display, raw, censored = _cell_parts(val)
                item = QTableWidgetItem(display)
                if raw is not None:
                    item.setData(RAW_VALUE_ROLE, raw)
                if censored:
                    item.setData(CENSOR_ROLE, ">")
                if c > 0:
                    item.setTextAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
                else:
                    # Left-hand row labels bold -- they're the row
                    # headers in these tables.
                    item.setFont(bold)
                table.setItem(r, c, item)
        table.verticalHeader().setVisible(False)
        table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        table.setAlternatingRowColors(True)
        table.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        header = table.horizontalHeader()
        # No stretching anywhere: every column hugs its content, so the
        # label column doesn't spread absurdly wide.
        header.setStretchLastSection(False)
        for c in range(len(headers)):
            header.setSectionResizeMode(c, QHeaderView.ResizeMode.ResizeToContents)
        table.resizeColumnsToContents()
        # And the whole table hugs its columns instead of spreading
        # across the full card width (`_FitTable.sizeHint`, from the live
        # column widths: a width fixed now went stale once the style
        # sized the bold label column, and clipped the last column).
        table.setSizePolicy(QSizePolicy.Policy.Maximum,
                            QSizePolicy.Policy.Preferred)
        header.sectionResized.connect(lambda *_a: table.updateGeometry())
        # Tall enough for every row (up to _TABLE_ROWS_SHOWN): the page's
        # scroll area scrolls, not a small box inside it. A longer table
        # (one row per block in a big model) keeps its own scrollbar
        # beyond that.
        row_h = table.verticalHeader().defaultSectionSize()
        full = row_h * (len(rows) + 1) + 4
        table.setMaximumHeight(full)
        fits = len(rows) <= self._TABLE_ROWS_SHOWN
        table.setMinimumHeight(full if fits else
                               row_h * (self._TABLE_ROWS_SHOWN + 1) + 4)
        table.setVerticalScrollBarPolicy(
            Qt.ScrollBarPolicy.ScrollBarAlwaysOff if fits
            else Qt.ScrollBarPolicy.ScrollBarAsNeeded)
        # Acronym tooltips on row labels; stat-column tooltips on headers.
        # The `(k-runs)` label suffix (Raptor 7's `xRunsNote1` layout,
        # Raptor 7) must not break the glossary lookup, so it is
        # stripped before searching: `MDT(8-runs)` still gets MDT's
        # plain-English tooltip.
        for r, row_vals in enumerate(rows):
            item = table.item(r, 0)
            if item is not None:
                # .strip() matters: time-measure labels carry a units
                # suffix ("MTBM (hours)"), so split("(")[0] alone leaves
                # a trailing space that misses the GLOSSARY lookup.
                # Cells may be (display, raw) tuples -- label is the
                # display half.
                label0 = _cell_parts(row_vals[0])[0]
                base = label0.split("(")[0].split("_")[0].strip()
                item.setToolTip(GLOSSARY.get(label0, "")
                                or GLOSSARY.get(base, ""))
                runs_m = re.search(r"(\d+)[ -]runs", label0)
                if runs_m:
                    n = runs_m.group(1)
                    item.setToolTip(item.toolTip() +
                                    f"\n\nBased on only {n} of the runs — "
                                    f"the rest never had a downing event, "
                                    f"so their sample can't be included.")
        for c, h in enumerate(headers):
            h_item = table.horizontalHeaderItem(c)
            if h_item is not None:
                h_item.setToolTip(self._stat_col_tip(h))
        # `>`-censored cells (Raptor 7's gtNote1 layout: the mean/
        # maximum are only the raw-uptime fallback, so they carry a `>`
        # prefix and N/A St.Dev/SEM) get an explanatory hover.
        for r in range(len(rows)):
            for c in range(1, len(headers)):
                item = table.item(r, c)
                if item is not None and item.text().startswith(">"):
                    # Raptor 7's marker, kept verbatim and just
                    # coloured red so it reads as a warning.
                    item.setForeground(QColor(active_colour()["result_red"]))
                    label = _cell_parts(rows[r][0])[0]
                    base = label.split("(")[0].strip()
                    if base == "MTBDE":
                        reason = ("some runs had no downing event — the "
                                  "system stayed up for the whole run — so "
                                  "the true value is at least this large.")
                    elif base.startswith("MTBM"):
                        reason = ("some runs had no maintenance event, so "
                                  "the true value is at least this large.")
                    else:
                        reason = ("some runs produced no value for this "
                                  "measure, so the true value is at least "
                                  "this large.")
                    item.setToolTip(_GT_NOTE + "\n\n" + reason)
        return table

    #: Last details sub-tab the user viewed, by name -- survives
    #: clear()/repopulate so the selection persists across a fresh run.
    _last_details_tab: str | None = None
    #: Optional status-bar sink (MainWindow attaches at wiring time)
    _status_hint_f = None

    def set_status_hint_fn(self, fn):
        self._status_hint_f = fn

    def _populate_details(self, lines: list[str]):
        try:
            self._details_tabs.currentChanged.disconnect(
                self._remember_details_tab)
        except (TypeError, RuntimeError):
            pass
        while self._details_tabs.count():
            w = self._details_tabs.widget(0)
            self._details_tabs.removeTab(0)
            req(w).deleteLater()
        self._detail_tables.clear()
        # The Domains page dies with this wipe; stale refs would let
        # set_level_filter poke a deleted QTreeWidget (shiboken
        # RuntimeError) on the next level change.

        stat_headers = {"minimum", "mean"}
        sections = self._parse_final_sections(lines)
        # Total Cost row: computed GUI-side from the per-run
        # CostRunReport.grand_total and appended to the Summary stats
        # table only when the model produced cost data.
        #
        # Raptor 7 emits its own `Total Cost` row
        # (under `IF costAnalysis`), so the GUI-side row is skipped
        # when the parsed Summary already carries one, rather than showing
        # it twice.
        cost_vals = [d.cost.grand_total for d in self._report_data
                     if d is not None and d.cost is not None]
        if cost_vals and len(cost_vals) == len(self._report_data):
            for t_i, (title, columns, rows) in enumerate(sections):
                if title != "SUMMARY" or not rows:
                    continue
                is_stats_sec = {c.lower() for c in columns[:2]} == \
                    stat_headers
                if not is_stats_sec:
                    continue
                if any(str(r[0]).replace("_", " ").strip().lower() == "total cost"
                       for r in rows):
                    break   # the engine's own row is already present
                mean_v, mn, mx, sd, sem = self._stats(cost_vals)
                # (display, raw) cells: Copy Table gets the full-
                # precision figure, the grid keeps 2dp.
                def _cell(v):
                    return ("N/A" if v is None else f"{v:,.2f}", v)
                cells = [_cell(mn), _cell(mean_v), _cell(mx),
                         _cell(sd), _cell(sem)]
                sections[t_i] = (title, columns,
                                 rows + [("Total Cost", cells)])
                break
        # One tab per section (Summary, Logistics, Sparing, Resources, ...),
        # each holding that section's tables under their own names; the
        # labels and number formats are `raptor.api.presentation`'s, shared
        # with the spreadsheet and the PDF report. The full value stays in
        # each cell (RAW_VALUE_ROLE) for Copy Table.
        from raptor.api.presentation import section_of, table_title

        #: Sections with a dedicated tab built from the per-run snapshots
        #: (not the report text). COST's per-state and per-block sparing
        #: tables, which the dedicated page does not show, are added to it.
        _DEDICATED_TABS = {"CAPACITY", "COST", "WEAK_LINK_ANALYSIS"}
        _COST_EXTRA = ("Operating", "Sparing", "Cost_Summary")
        pages: dict[str, QVBoxLayout] = {}
        order: list[tuple[str, QWidget]] = []
        self._cost_extra = []
        for title, columns, rows in sections:
            head = title.split(" - ", 1)[0]
            if head in _DEDICATED_TABS:
                if head == "COST" and title.split(" - ")[-1] in _COST_EXTRA:
                    self._cost_extra.append((title, columns, rows))
                continue
            table = self._section_widget(title, columns, rows)
            group = section_of(title)
            if group not in pages:
                page = QWidget()
                v = QVBoxLayout(page)
                v.setContentsMargins(SPACING["md"], SPACING["md"],
                                     SPACING["md"], SPACING["md"])
                pages[group] = v
                order.append((group, page))
                if title == "SUMMARY":
                    self._add_runs_line(v)
            v = pages[group]
            name = table_title(title)
            if name != group:
                label = QLabel(name)
                label.setObjectName("SectionTitle")
                v.addWidget(label)
            v.addWidget(table)
            if title == "SUMMARY":
                self._add_summary_notes(v)
            self._detail_tables[title] = table
        for group, page in order:
            pages[group].addStretch(1)
            # The table shows all its rows; this scroll area handles
            # overflow when the dock is short.
            self._details_tabs.addTab(self._scroll_wrap(page), group)

        # The dedicated Capacity tab, built from the engine's structured
        # per-run capacity snapshots. The Final_Results text only puts
        # three headline rows into Summary, so this surfaces the rest.
        cap_page = self._build_capacity_page()
        self._details_tabs.addTab(self._scroll_wrap(cap_page), "Capacity")
        cost_page = self._build_cost_page()
        self._details_tabs.addTab(self._scroll_wrap(cost_page), "Cost")
        weak_page = self._build_weak_page()
        self._details_tabs.addTab(self._scroll_wrap(weak_page), "Weak Link")
        if getattr(self, "_importance", None):
            imp_page = self._build_importance_page()
            self._details_tabs.addTab(self._scroll_wrap(imp_page),
                                      "Importance")
        try:
            self._details_tabs.currentChanged.connect(
                self._remember_details_tab)
        except (TypeError, RuntimeError):
            pass
        # Restore the user's last-selected sub-tab now that the recorder
        # is re-attached.
        wanted = getattr(self, "_last_details_tab", None)
        if wanted:
            for i in range(self._details_tabs.count()):
                if self._details_tabs.tabText(i) == wanted:
                    self._details_tabs.setCurrentIndex(i)
                    break
        # The selector's visibility depends on the results (an
        # element-level table must exist), and set_hierarchy_options may
        # have run at model-load time when there were none yet --
        # re-evaluate now that the tables are built.
        self._update_hier_filter_visibility()
    def _section_widget(self, title: str, columns: list[str],
                        rows) -> QTableWidget:
        """One Final Results table: readable headers, measures as words
        with their unit, values formatted by kind (full value kept for
        Copy Table), element names as the model has them (elided, the
        full name kept for filtering and click-to-select)."""
        from raptor.api.presentation import (
            humanise, kind_of, section_of, table_title, to_number, with_unit)
        from raptor.api.presentation import fmt as _pfmt
        is_stats = {c.lower() for c in columns[:2]} == {"minimum", "mean"}
        own_label = bool(rows) and all(len(v) == len(columns)
                                       for _, v in rows)
        measures = is_stats or columns[:2] == ["Item", "Value"]
        name = table_title(title)
        if own_label:
            first = ("Measure" if is_stats else
                     "Node" if "Node" in name else "")
            headers = [first] + [humanise(c) for c in columns]
            measures = measures or not first
        else:
            headers = [humanise(c) if c else "" for c in columns]
        ctx = f"{section_of(title)} {name}"

        def value(v: str, kind: str):
            num = to_number(v)
            if num is None:
                return v
            shown = _pfmt(v, kind)
            # Keep the numeric value AND the censor flag: the grid shows
            # ">8,759.5", Copy/CSV write 8759.5 in a numeric column and a
            # parallel "censored" column.
            return (shown, num, shown.startswith(">"))

        body_rows, full_names = [], []
        for label, vals in rows:
            if measures:
                kind = kind_of(label, table=ctx)
                shown = with_unit(humanise(label), kind, self._units)
                kinds = [kind] * len(vals)
            else:
                shown = self._elide(label)
                kinds = [kind_of(h, table=ctx)
                         for h in headers[len(headers) - len(vals):]]
            if label == "Totals" or label.endswith("_Totals"):
                shown = "Total"
            body_rows.append([shown] + [value(v, k)
                                        for v, k in zip(vals, kinds)])
            full_names.append(label)
        table = self._make_table(headers, body_rows)
        if not measures:
            for r_i, full in enumerate(full_names):
                it = table.item(r_i, 0)
                if it is not None and full != it.text():
                    it.setData(Qt.ItemDataRole.UserRole, full)
                    it.setToolTip(full)
        return table

    def _add_runs_line(self, v: QVBoxLayout) -> None:
        """The batch header above Summary: "20 runs of 8,760 hours each"
        from the report's own "Results from N runs of sim time T" line."""
        import re
        from raptor.api.presentation import fmt as _pfmt
        line = next((ln for ln in self._final_lines
                     if ln.startswith("Results from")), "").strip()
        if not line:
            return
        m = re.match(r"Results from (\d+) runs? of sim time ([\d.]+)", line)
        if m:
            runs = int(m.group(1))
            unit = f" {self._units}" if self._units else ""
            # The report line quotes the CONFIGURED term condition. A run
            # terminated early (Step ▸ Terminate) simulated less, so use
            # the per-run ending time when the run data carries it.
            times = [d.ending_sim_time for d in self._report_data
                     if getattr(d, "ending_sim_time", None)]
            if times and max(times) - min(times) < 1e-9:
                shown = _pfmt(times[0], "num")
                line = (f"{runs:,} run{'s' if runs != 1 else ''} of "
                        f"{shown}{unit} each")
            elif times:
                shown = _pfmt(sum(times) / len(times), "num")
                line = (f"{runs:,} run{'s' if runs != 1 else ''}, "
                        f"{shown}{unit} each on average")
            else:
                line = (f"{runs:,} run{'s' if runs != 1 else ''} of "
                        f"{_pfmt(m.group(2), 'num')}{unit} each")
        hdr = QLabel(line)
        hdr.setObjectName("SectionTitle")
        hdr.setWordWrap(True)
        v.addWidget(hdr)

    def _add_summary_notes(self, v: QVBoxLayout) -> None:
        """The report builder's censoring/sampling notes (dialog labels in
        Raptor 7), under the Summary table,
        once each (the builder emits one per measure row)."""
        for note_text in dict.fromkeys(self._notes):
            note = QLabel(note_text)
            note.setObjectName("SectionNote")
            note.setWordWrap(True)
            v.addWidget(note)

    #: Both views of the extremes are shown: the envelope (worst/best
    #: run), which is useful for planning, and Raptor 7's Final Results
    #: aggregation (the mean of each run's own min/max).
    _CAP_HEADERS = [
        "Node",
        "Min Flow (worst)", "Min Flow (avg runs)", "Ave Flow",
        "Max Flow (avg runs)", "Max Flow (best)", "Usage",
        "Min Cap (worst)", "Min Cap (avg runs)", "Ave Cap",
        "Max Cap (avg runs)", "Max Cap (best)", "Capability"]

    @staticmethod
    def _capacity_node_ids(caps, sys_nid: int) -> list[int]:
        """Node rows for the Capacity tab, system/Start row first.

        Names are collected across every run: a node present only in a
        later run used to raise KeyError on caps[0] and lose the tab.
        Unknown names fall back to the id so the sort still works.
        """
        node_names = {}
        for cap in caps:
            for nid, stats in cap.nodes.items():
                node_names.setdefault(nid, stats.name)
        ids = sorted({nid for cap in caps for nid in cap.nodes},
                     key=lambda nid: node_names.get(nid, str(nid)).lower())
        if sys_nid in ids:
            ids.remove(sys_nid)
            ids.insert(0, sys_nid)
        return ids

    def _build_capacity_page(self) -> QWidget:
        from raptor.core.engine.capacity import CapacityRunReport  # noqa: F401 (type only)
        page = QWidget()
        v = QVBoxLayout(page)
        v.setContentsMargins(SPACING["md"], SPACING["md"], SPACING["md"], SPACING["md"])
        v.setSpacing(SPACING["sm"])

        caps = [d.capacity for d in self._report_data if d.capacity is not None]
        if not caps:
            msg = QLabel(
                "No capacity analysis in this model.\n\n"
                "Enable Capacity in the Run panel's Analysis section, "
                "give Start a Flow Generated value, and run again.")
            msg.setObjectName("SectionNote")
            msg.setWordWrap(True)
            v.addWidget(msg)
            v.addStretch(1)
            return page

        def mean(getter):
            vals = [v for v in (getter(c) for c in caps) if v is not None]
            return sum(vals) / len(vals) if vals else None

        head = QLabel(
            f"Flow generated: {caps[0].flow_generated:,}   ·   "
            # Units produced is a flow quantity, not a duration: appending
            # the time unit made it read "1,513,838.93 hours".
            f"Units produced: {mean(lambda c: c.units_produced):,.2f}   ·   "
            f"Flow loss (min · avg · max): "
            + " · ".join(f"{x:,.2f}" for x in flow_loss_across_runs(caps)))
        head.setObjectName("SummarySentence")
        head.setWordWrap(True)
        head.setToolTip(
            "Flow Generated: supply fed into the Start node each trial.\n"
            "Units Produced: total flow that made it through the system.\n"
            "Flow loss: Flow Generated less the flow that got through, as the Final Results report prints it\n"
            "(minimum, average and maximum over the runs).")
        v.addWidget(head)


        # Row order per Raptor 7's Capacity tab (Raptor 7 manual Fig. 14.12:
        # the system-level row FIRST, then the capacity nodes). The
        # Start node IS the system row (reports carry its index as
        # `system_node_id`); remaining nodes alphabetical.
        sys_nid = caps[0].system_node_id
        node_ids = self._capacity_node_ids(caps, sys_nid)
        rows = []
        for nid in node_ids:
            # Snapshot the per-run stats once, avoiding late-binding
            # closures over the loop variable.
            per_run = [cap.nodes[nid] for cap in caps if nid in cap.nodes]
            if not per_run:
                continue
            # Raptor prints N/A for the capacity columns of full-flow
            # boundary nodes, but the SystemCapacity row is special: it
            # always carries the Start node's real figures (including
            # capability), even when that node is flagged full-flow.
            is_system = nid == sys_nid
            cap_na = any(s.full_flow for s in per_run) and not is_system

            def avg(attr, runs=per_run):
                vals = [getattr(s, attr) for s in runs]
                return sum(vals) / len(vals) if vals else None

            def avg_nums(vals):
                return sum(vals) / len(vals) if vals else None

            fmt = lambda val, dec=2: (
                "N/A" if val is None else f"{val:,.{dec}f}")
            run_min_flow = [s.min_flow for s in per_run]
            run_max_flow = [s.max_flow for s in per_run]
            run_min_cap = [s.min_cap for s in per_run]
            run_max_cap = [s.max_cap for s in per_run]
            # Numeric cells carry (display, raw) so Copy Table hands the
            # full-precision number, not the rounded grid text. N/A cells
            # have no raw value.
            rows.append([
                "SystemCapacity" if is_system else per_run[0].name,
                (f"{int(min(run_min_flow)):,}", min(run_min_flow)),
                (fmt(avg_nums(run_min_flow), 2), avg_nums(run_min_flow)),
                (fmt(avg("ave_flow"), 4), avg("ave_flow")),
                (fmt(avg_nums(run_max_flow), 2), avg_nums(run_max_flow)),
                (f"{int(max(run_max_flow)):,}", max(run_max_flow)),
                ("N/A", None) if cap_na else
                (fmt(avg("usage"), 4), avg("usage")),
                ("N/A", None) if cap_na else
                (f"{int(min(run_min_cap)):,}", min(run_min_cap)),
                ("N/A", None) if cap_na else
                (fmt(avg_nums(run_min_cap), 2), avg_nums(run_min_cap)),
                ("N/A", None) if cap_na else
                (fmt(avg("ave_cap"), 4), avg("ave_cap")),
                ("N/A", None) if cap_na else
                (fmt(avg_nums(run_max_cap), 2), avg_nums(run_max_cap)),
                ("N/A", None) if cap_na else
                (f"{int(max(run_max_cap)):,}", max(run_max_cap)),
                ("N/A", None) if cap_na else
                (fmt(avg("capability"), 4), avg("capability")),
            ])
        table = self._make_table(self._CAP_HEADERS, rows)
        table.setToolTip(
            "Flow/capacity through each capability node, averaged over the runs.\n"
            "Usage = AveFlow/AveCap · Capability = AveCap/MaxCap.\n"
            "Full-flow boundary nodes show N/A usage/capability.")
        self._cap_table = table
        v.addWidget(table)
        v.addStretch(1)
        return page

    _WEAK_KEYS = [("run", "Running"), ("repair", "Repairing"),
                  ("rep_hold", "Rep Hold"), ("pm", "PM"),
                  ("pm_hold", "PM Hold"), ("idle", "Idle"),
                  ("standby", "Standby"), ("done", "Done")]

    #: Weak Link settings: pushed by MainWindow from
    #: the SYSTEM_DATA row. Defaults mirror every fixture's values.
    _weak_anal_type: str = "Availability"
    _weak_gy: float = 0.95
    _weak_yr: float = 0.90
    #: Optional filter: callable -> names of the canvas selection
    #: ("selection options": restrict analysis rows to selected blocks).
    _selected_block_names_fn = None

    @staticmethod
    def _band(value: float, gy: float, yr: float):
        """(colour-key, band name) per Raptor 7 thresholds (higher=better)."""
        if value >= gy:
            return "result_green", "Green"
        if value >= yr:
            return "result_yellow", "Yellow"
        return "result_red", "Red"

    @staticmethod
    def _stats(vals):
        """(mean, min, max, stdev, sem) — the Weak Link table's columns."""
        n = len(vals)
        mean = sum(vals) / n
        if n < 2:
            return mean, min(vals), max(vals), None, None
        var = sum((v - mean) ** 2 for v in vals) / (n - 1)
        sd = var ** 0.5
        return mean, min(vals), max(vals), sd, sd / n ** 0.5

    @staticmethod
    def _weak_metric_rows(runs, buckets_up, buckets_down, principal=False,
                          missions=None):
        """Per-element metric rows: [(name, [ratio_per_run])].

        Explicit bucket sets, because this is called three ways with three
        DIFFERENT semantics: the block Availability/Dependability split, the node-band
        equivalent, and the per-state "% of time" breakdown, which passes one state against all the others. Only the
        first two correspond to the named analyses, so the analysis name
        cannot be inferred here -- hence the parameters.

        The `up/(up+down)` arithmetic is `core.engine.weak_metric`'s, so
        the ratio is computed in one place even though the bucket CHOICE
        is per-call. `principal` is retained for call compatibility and
        ignored: both branches of the old `if principal:` were
        byte-identical, so it never changed a number.

        `missions` (one `missions_attempted` per run, aligned with `runs`)
        switches the rows to Reliability: `m_completed / missions`, not a
        time ratio, so the bucket sets are ignored.
        """
        up_keys, down_keys = tuple(buckets_up), tuple(buckets_down)
        out = []
        for eid in sorted(runs[0]):
            per_run = [ws[eid] for ws in runs if eid in ws]
            if not per_run:
                continue
            name = per_run[0].get("name", str(eid))
            vals = []
            if missions is not None:
                vals = [weak_metric.reliability(ws[eid], missions[i])
                        for i, ws in enumerate(runs) if eid in ws]
                out.append((name, vals))
                continue
            for x in per_run:
                up = sum(x.get(k, 0.0) for k in up_keys)
                down = sum(x.get(k, 0.0) for k in down_keys)
                denom = up + down
                # Empty denominator is Raptor's 1.0 fallback, not a
                # skipped run. Skipping it made the table mean over the
                # remaining runs (Phasing3 HighGain: 0.700) while the
                # canvas and engine both reported 0.971.
                vals.append(up / denom if denom else 1.0)
            if vals:
                out.append((name, vals))
        return out

    def _goto_selected_row(self, table: QTableWidget, kind: str) -> None:
        row = table.currentRow()
        if row < 0:
            self._status_hint_f and self._status_hint_f(
                "Select a row first")
            return
        item = table.item(row, 0)
        if item is not None:
            # Full name (elision keeps it in UserRole data)
            name = item.data(Qt.ItemDataRole.UserRole) or item.text()
            self.element_clicked.emit(kind, name)

    def _build_weak_page(self) -> QWidget:
        """Weak Link Analysis: a colour chip per element,
        the chosen metric (Availability or Dependability — Raptor 7's
        "Analysis Type"), and across-run min/max/stdev/SEM like Fig
        11.6's Output Tables. A model with no Weak Link data still gets a
        tab with an explanatory message rather than nothing."""
        runs = [d.weak_stats for d in self._report_data if d.weak_stats]
        run_missions = [d.missions_attempted for d in self._report_data
                        if d.weak_stats]
        if not runs:
            page = QWidget()
            v = QVBoxLayout(page)
            v.setContentsMargins(SPACING["md"], SPACING["md"],
                                 SPACING["md"], SPACING["md"])
            v.setSpacing(SPACING["sm"])
            msg = QLabel(
                "No Weak Link analysis in this model.\n\n"
                "Weak Link Analysis is turned on in the Run panel's "
                "Analysis section — enable it and rerun to see "
                "per-element availability/dependability here.")
            msg.setObjectName("SectionNote")
            msg.setWordWrap(True)
            v.addWidget(msg)
            v.addStretch(1)
            return page
        from PySide6.QtWidgets import QCheckBox, QPushButton
        from PySide6.QtGui import QColor
        c = active_colour()
        page = QWidget()
        v = QVBoxLayout(page)
        v.setContentsMargins(SPACING["md"], SPACING["md"],
                             SPACING["md"], SPACING["md"])
        v.setSpacing(SPACING["sm"])

        head = QLabel(
            f"Weak Link Analysis — {len(runs)} run(s). Colour bands "
            f"and metric come from Results ▸ Bands; hover any cell or "
            f"chip for details.")
        head.setObjectName("SummarySentence")
        head.setWordWrap(True)
        v.addWidget(head)


        sel_chk = QCheckBox("Selected elements only")
        sel_chk.setToolTip("Show only elements whose blocks are "
                           "selected on the canvas")
        sel_chk.setChecked(getattr(self, "_weak_sel_only", False))
        def _on_sel_only(on):
            self._weak_sel_only = on
            self._rebuild_weak_page()
        sel_chk.toggled.connect(_on_sel_only)
        v.addWidget(sel_chk)
        sel_note = QLabel(
            "No canvas selection — select one or more blocks, or clear "
            "“Selected elements only”.")
        sel_note.setObjectName("SectionNote")
        sel_note.setWordWrap(True)
        selected = (self._selected_block_names_fn()
                    if self._selected_block_names_fn else [])
        sel_note.setVisible(bool(getattr(self, "_weak_sel_only", False)
                                 and not selected))
        v.addWidget(sel_note)

        kind = weak_metric.canonical(self._weak_anal_type)
        dep = kind == weak_metric.DEPENDABILITY
        rel = kind == weak_metric.RELIABILITY

        def metric_cell_tooltip(name, val, mean_v):
            _, band = self._band(mean_v, self._weak_gy, self._weak_yr)
            return (f"{name}\n{kind} = {val:.6f} "
                    f"→ {band} band\n(Green ≥ {self._weak_gy:.2f}, "
                    f"Yellow ≥ {self._weak_yr:.2f}, Red below)")

        def fill_table(table, rows_data):
            # Raptor 7's Output Tables keep the Min/Max/StdDev/SEM
            # columns even for one run. StdDev/SEM are blank there
            # (undefined with n=1), rather than hiding the columns and
            # changing the table's shape between run counts.
            headers = ["Name", "Colour", kind, "Min", "Max",
                       "StdDev", "SEM"]
            table.setColumnCount(len(headers))
            table.setHorizontalHeaderLabels(headers)
            # This table abbreviates further than STAT_COLUMN_TOOLTIPS's
            # own keys (Min/Max/StdDev vs. Minimum/Maximum/St.Dev).
            _short_stat = {"Min": "Minimum", "Max": "Maximum",
                          "StdDev": "St.Dev", "SEM": "SEM"}
            for col_i, h in enumerate(headers):
                h_item = table.horizontalHeaderItem(col_i)
                if h_item is not None:
                    tip = self._stat_col_tip(_short_stat.get(h, h))
                    if tip:
                        h_item.setToolTip(tip)
            allowed = (self._selected_block_names_fn()
                       if (getattr(self, "_weak_sel_only", False) and
                           self._selected_block_names_fn) else None)
            r = 0
            for name, vals in rows_data:
                if allowed is not None and name not in allowed:
                    continue
                mean_v, mn, mx, sd, sem = self._stats(vals)
                table.insertRow(r)
                n_it = QTableWidgetItem(name)
                table.setItem(r, 0, n_it)
                ck, band = self._band(mean_v, self._weak_gy,
                                      self._weak_yr)
                chip = QTableWidgetItem(band)
                chip.setBackground(QColor(c[ck]))
                chip.setToolTip(
                    f"{band} band — Green ≥ {self._weak_gy:.2f}, "
                    f"Yellow ≥ {self._weak_yr:.2f}, Red below "
                    f"(colour-band thresholds)")
                chip.setFlags(chip.flags() & ~Qt.ItemFlag.ItemIsEditable)
                table.setItem(r, 1, chip)
                v_it = QTableWidgetItem(f"{mean_v:.9f}")
                # Raw value rides along for full-precision Copy Table:
                # the grid shows 9 decimals, the clipboard gets the exact
                # double.
                v_it.setData(RAW_VALUE_ROLE, mean_v)
                v_it.setToolTip(metric_cell_tooltip(
                    name, mean_v, mean_v))
                table.setItem(r, 2, v_it)
                for col, val in zip((3, 4, 5, 6), (mn, mx, sd, sem)):
                    it_s = QTableWidgetItem(
                        "" if val is None else f"{val:.9f}")
                    if val is not None:
                        it_s.setData(RAW_VALUE_ROLE, val)
                    table.setItem(r, col, it_s)
                r += 1

        def size_to_content(table: QTableWidget) -> None:
            # Full height, no cap -- see _make_table's own comment; the
            # page-level scroll area below handles overflow instead of
            # each table's own small internal scrollbar.
            full = (table.horizontalHeader().height()
                    + table.verticalHeader().defaultSectionSize()
                    * table.rowCount() + 4)
            # Both bounds: setting only the maximum left the table at its
            # 192px sizeHint, so it kept an internal scrollbar and never
            # filled the page.
            table.setMinimumHeight(full)
            table.setMaximumHeight(full)

        def goto_row_button(table: QTableWidget, kind: str) -> QPushButton:
            btn = QPushButton("Go to Selected Element")
            btn.setToolTip(
                f"Select {kind}'s canvas element and scroll to it "
                "(navigates into its hierarchy level first if needed)")
            btn.clicked.connect(
                lambda: self._goto_selected_row(table, kind))
            return btn

        # Blocks: full bucket split. From
        # `core.engine.weak_metric`, the same owner `main_window`'s canvas
        # overlay now uses -- the two previously disagreed on whether `idle`
        # is in the Dependability denominator, so the same block could be
        # green in this table and red on the canvas. See that module for why
        # `idle` is excluded.
        bup, bdown = weak_metric.buckets(kind)
        block_rows = self._weak_metric_rows(
            runs, bup, bdown, missions=run_missions if rel else None)
        btable = QTableWidget(0, 3)
        fill_table(btable, block_rows)
        size_to_content(btable)
        # Both double-click and the button navigate, using the same
        # underlying signal the Event Log's row-click already uses
        # (element_clicked).
        btable.cellDoubleClicked.connect(
            lambda r, _c: self._goto_selected_row(btable, "block"))
        blabel = QLabel(
            "Blocks — "
            + ("reliability is the fraction of attempted missions the "
               "block was up through" if rel else
               "dependability excludes standby/PM/dependency time "
               "(principal states only)" if dep else
               "availability counts standby (blue) as uptime"))
        blabel.setObjectName("SectionNote")
        v.addWidget(blabel)
        v.addWidget(btable)
        v.addWidget(goto_row_button(btable, "block"))

        # Nodes.
        # Raptor's NODE_RESULTS lists regular nodes only (`typeNode=2`
        # and reportNodeAnal); Start/End/In/Out are markers, not weak
        # links, and have no bucket time of their own.
        node_runs = [
            {nid: st for nid, st in d.weak_nodes.items()
             if st.get("type_node") == 2 and st.get("report_anal", True)}
            for d in self._report_data if getattr(d, "weak_nodes", None)]
        node_missions = [d.missions_attempted for d in self._report_data
                         if getattr(d, "weak_nodes", None)]
        if node_runs:
            nup = ("green", "yellow", "blue") if not dep else \
                  ("green", "yellow")
            ndown = ("red", "brown", "orange") if not dep else ("red",)
            node_rows = self._weak_metric_rows(
                node_runs, nup, ndown,
                missions=node_missions if rel else None)
            ntable = QTableWidget(0, 3)
            fill_table(ntable, node_rows)
            size_to_content(ntable)
            ntable.cellDoubleClicked.connect(
                lambda r, _c: self._goto_selected_row(ntable, "node"))
            nlabel = QLabel(
                "Nodes — same bands; reliability is the fraction of "
                "attempted missions the node was up through" if rel else
                "Nodes — same bands; dependability "
                "excludes blue/brown/orange")
            nlabel.setObjectName("SectionNote")
            v.addWidget(nlabel)
            v.addWidget(ntable)
            v.addWidget(goto_row_button(ntable, "node"))

            # Per-state % breakdown (Raptor 7 "State_Data" table):
            # the raw numbers behind the single rolled-up Availability/
            # Dependability figure above. Reuses `_weak_metric_rows` per
            # state (state / everything-else) so the mean-across-runs math
            # matches the table above; not gated by the Analysis Type
            # toggle, so every state shows.
            pct_label = QLabel(
                "Node state breakdown — % of total time in each state, "
                "averaged across runs.")
            pct_label.setObjectName("SectionNote")
            pct_label.setWordWrap(True)
            v.addWidget(pct_label)
            states = ("green", "yellow", "red", "blue", "brown", "orange")
            per_state = {s: dict(self._weak_metric_rows(
                node_runs, (s,), tuple(o for o in states if o != s)))
                for s in states}
            allowed = (self._selected_block_names_fn()
                      if (getattr(self, "_weak_sel_only", False) and
                          self._selected_block_names_fn) else None)
            names = sorted({name for rows in per_state.values()
                           for name in rows
                           if allowed is None or name in allowed})
            pct_table = QTableWidget(len(names), 1 + len(states))
            pct_table.setHorizontalHeaderLabels(
                ["Name"] + [f"% {s.capitalize()}" for s in states])
            for r, name in enumerate(names):
                pct_table.setItem(r, 0, QTableWidgetItem(name))
                for ci, s in enumerate(states, start=1):
                    vals = per_state[s].get(name, [0.0])
                    mean_v, *_rest = self._stats(vals)
                    it = QTableWidgetItem(f"{mean_v * 100:.2f}")
                    it.setData(RAW_VALUE_ROLE, mean_v * 100)
                    it.setFlags(it.flags() & ~Qt.ItemFlag.ItemIsEditable)
                    pct_table.setItem(r, ci, it)
            size_to_content(pct_table)
            pct_table.cellDoubleClicked.connect(
                lambda r, _c: self._goto_selected_row(pct_table, "node"))
            v.addWidget(pct_table)

        v.addStretch(1)
        return self._scroll_wrap(page)

    def set_weak_settings(self, anal_type: str, gy: float, yr: float):
        """MainWindow pushes the model's Raptor 7 settings here.

        The Weak tab is built from these values, so an existing tab must
        be rebuilt when the metric or bands change; leaving it alone made
        the table contradict the canvas and status bar.
        """
        old = (self._weak_anal_type, self._weak_gy, self._weak_yr)
        self._weak_anal_type = anal_type or "Availability"
        self._weak_gy, self._weak_yr = float(gy), float(yr)
        if old == (self._weak_anal_type, self._weak_gy, self._weak_yr):
            return
        tabs = getattr(self, "_details_tabs", None)
        if tabs is None or not self._report_data:
            return
        if any(tabs.tabText(i) == "Weak Link"
               for i in range(tabs.count())):
            self._rebuild_weak_page()

    def set_selected_block_names_fn(self, fn):
        """Register the selection-name provider used by the Weak tab filter."""
        self._selected_block_names_fn = fn

    def refresh_weak_selection(self):
        """Re-apply the Weak tab's selection filter after a canvas click."""
        if (getattr(self, "_weak_sel_only", False)
                and getattr(self, "_report_data", None)):
            self._rebuild_weak_page()

    def _rebuild_weak_page(self):
        """Swap the Weak sub-tab for a fresh one (selection filter /
        analysis-type changes). Re-entrancy guarded so a signal echo
        during the rebuild cannot schedule another rebuild."""
        if getattr(self, "_weak_rebuilding", False):
            return
        self._weak_rebuilding = True
        try:
            self._rebuild_weak_page_inner()
        finally:
            self._weak_rebuilding = False

    def _rebuild_weak_page_inner(self):
        was_current = self._details_tabs.currentIndex()
        weak_index = -1
        for i in range(self._details_tabs.count()):
            if self._details_tabs.tabText(i) == "Weak Link":
                weak_index = i
                w = self._details_tabs.widget(i)
                self._details_tabs.removeTab(i)
                req(w).deleteLater()
                break
        page = self._build_weak_page()
        # Advanced is the final tab. Insert Weak Link before it rather
        # than at the end, or a rebuild silently reorders the sub-tabs.
        advanced = next((i for i in range(self._details_tabs.count())
                         if self._details_tabs.tabText(i) == "Advanced"), -1)
        if advanced >= 0:
            index = self._details_tabs.insertTab(advanced, page, "Weak Link")
        else:
            index = self._details_tabs.addTab(page, "Weak Link")
        # Removing the open tab made Qt select its neighbour (usually
        # Cost). Rebuilding because the user ticked "Selected elements
        # only" must leave them on Weak Link, not jump tabs.
        if weak_index >= 0 and was_current == weak_index:
            self._details_tabs.setCurrentIndex(index)

    @staticmethod
    def _table_export_data(table: QTableWidget) -> tuple[list[str], list[list[str]]]:
        """Headers plus full-precision cell text for a built table.

        Used by the Cost page's report-section tables, which are created
        by `_section_widget` rather than the cost page's own builder.
        """
        headers = [(table.horizontalHeaderItem(c).text()
                    if table.horizontalHeaderItem(c) is not None else "")
                   for c in range(table.columnCount())]
        rows: list[list[str]] = []
        for r in range(table.rowCount()):
            row: list[str] = []
            for c in range(table.columnCount()):
                item = table.item(r, c)
                if item is None:
                    row.append("")
                    continue
                raw = item.data(RAW_VALUE_ROLE)
                row.append(str(raw) if raw is not None else item.text())
            rows.append(row)
        return headers, rows

    def _add_table_header(self, v: QVBoxLayout, title: str,
                          table: QTableWidget,
                          headers: list[str] | None = None,
                          copy_rows: list[list[str]] | None = None,
                          tip: str = "") -> QLabel:
        """A table title with its Copy and CSV actions, above the table."""
        from PySide6.QtWidgets import QPushButton
        if headers is None or copy_rows is None:
            headers, copy_rows = self._table_export_data(table)
        hdr_row = QHBoxLayout()
        label = QLabel(title)
        # Bigger than body notes: the title is the table's visual anchor.
        label.setObjectName("TableTitle")
        hdr_row.addWidget(label)
        # Keep the actions with their table title. A trailing stretch put
        # them at the page's far edge, visually detached from short
        # tables.
        hdr_row.addSpacing(12)
        cp = QPushButton("Copy")
        cp.setToolTip(f"Copy the '{title}' table to the clipboard "
                      "as TSV (pastes into Excel/Sheets)")
        sv = QPushButton("CSV…")
        sv.setToolTip(f"Save the '{title}' table as a CSV file")
        cp.setFixedHeight(22)
        sv.setFixedHeight(22)
        hdr_row.addWidget(cp)
        hdr_row.addWidget(sv)
        # Keep the actions compact beside the title; without this stretch
        # Qt expands both buttons across the remaining row.
        hdr_row.addStretch(1)
        v.addLayout(hdr_row)

        def _current_grid():
            # Read the table at click time, not a precomputed list: a
            # hierarchy filter may have hidden rows since it was built.
            return self._table_export_data(table)

        def _copy_this():
            live_headers, live_rows = _current_grid()
            tsv = "\t".join(live_headers) + "\n" + "\n".join(
                "\t".join(r) for r in live_rows)
            from PySide6.QtCore import QMimeData
            from raptor.gui.ui.table_export import grid_to_html
            mime = QMimeData()
            mime.setText(tsv)
            mime.setHtml(grid_to_html(live_headers, live_rows))
            QApplication.clipboard().setMimeData(mime)
            self._status_hint_f and self._status_hint_f(
                f"Copied '{title}'")

        def _save_this():
            import csv
            path, _f = fdg.getSaveFileName(
                self, f"Export {title}", title.replace(" ", "_")
                + ".csv", "CSV (*.csv)")
            if not path:
                return
            live_headers, live_rows = _current_grid()
            with open(path, "w", newline="", encoding="utf-8") as f:
                w2 = csv.writer(f)
                w2.writerow(live_headers)
                w2.writerows(live_rows)
            self._status_hint_f and self._status_hint_f(f"Saved: {path}")

        cp.clicked.connect(_copy_this)
        sv.clicked.connect(_save_this)
        return label

    _COST_HEADERS = ["Name", "Purchase", "Operating", "Sparing",
                     "Disposal", "Total"]

    def _build_cost_page(self) -> QWidget:
        """Cost analysis view: per-run `CostRunReport`s
        averaged across runs -- blocks' life-cycle quintuple, spare/
        resource pools, events, hierarchies, and the system totals."""
        page = QWidget()
        v = QVBoxLayout(page)
        v.setContentsMargins(SPACING["md"], SPACING["md"],
                             SPACING["md"], SPACING["md"])
        v.setSpacing(SPACING["sm"])

        costs = [d.cost for d in self._report_data if d.cost is not None]
        if not costs:
            msg = QLabel(
                "No cost analysis in this model.\n\n"
                "Cost is simulated when Cost is enabled in the Run panel's "
                "Analysis section (and blocks define costs).")
            msg.setObjectName("SectionNote")
            msg.setWordWrap(True)
            v.addWidget(msg)
            v.addStretch(1)
            self._cost_tables: list[QTableWidget] = []
            return page

        def money(x: float) -> str:
            return f"{x:,.2f}"

        def mean_over(getter):
            vals = [getter(cst) for cst in costs]
            return sum(vals) / len(vals)

        down = mean_over(lambda c: c.system_down_cost)
        lost = mean_over(lambda c: c.system_lost_flow_cost)
        total = mean_over(lambda c: c.grand_total)
        head = QLabel(
            f"System-wide totals — Grand total: {money(total)}   \u00b7   "
            f"System down cost: {money(down)}   \u00b7   "
            f"Lost flow cost: {money(lost)}"
            f"   \u00b7   averaged over {len(costs)} run(s)")
        head.setObjectName("SummarySentence")
        head.setWordWrap(True)
        v.addWidget(head)


        self._cost_tables = []
        self._cost_blocks_label = None
        self._cost_blocks_title = ""

        def add_table(title: str, headers: list[str], rows: list[list],
                      tip: str = ""):
            # Cells may be (display, raw) tuples: the grid shows the
            # rounded display text, copy/CSV take the raw value at full
            # precision (same rule as the main Copy Table).
            ui_rows = []
            for row in rows:
                out_row = []
                for cell in row:
                    display, raw, censored = _cell_parts(cell)
                    out_row.append((display, raw, censored)
                                   if raw is not None else display)
                ui_rows.append(out_row)
            copy_rows = [[_copy_text(c) for c in row] for row in rows]
            table = self._make_table(headers, ui_rows)
            if tip:
                table.setToolTip(tip)
            label = self._add_table_header(
                v, title, table, headers, copy_rows)
            if title.startswith("Blocks ("):
                self._cost_blocks_label = label
                self._cost_blocks_title = title
            v.addWidget(table)
            self._cost_tables.append(table)

        # Blocks: life-cycle costs (Raptor 7's Life_cycle_cost table).
        block_ids = sorted({bid for cst in costs for bid in cst.blocks})
        block_rows = []
        for bid in block_ids:
            per_run = [cst.blocks[bid] for cst in costs if bid in cst.blocks]

            def avg(attr, runs=per_run):
                return sum(getattr(r, attr) for r in runs) / len(runs)
            block_rows.append([
                per_run[0].name,
                (money(avg("purchase")), avg("purchase")),
                (money(avg("operating")), avg("operating")),
                (money(avg("sparing")), avg("sparing")),
                (money(avg("disposal")), avg("disposal")),
                (money(avg("total")), avg("total"))])
        add_table(f"Blocks ({len(block_rows)}) -- life-cycle costs",
                  self._COST_HEADERS, block_rows,
                  "Purchase + Operating + Sparing + Disposal = Total")

        # Pools: spare pools keep their column names; RESOURCE pools
        # relabel NewSpares->PerTimeCost / EmerShip->PerUseCost
        # (`PoolCostRow`'s own docstring).
        spare_names = sorted({n for cst in costs for n in cst.spare_pools})
        res_names = sorted({n for cst in costs for n in cst.resources})
        pool_rows = []
        for name in sorted(set(spare_names) | set(res_names)):
            rows_ = ([cst.spare_pools[name] for cst in costs
                      if name in cst.spare_pools]
                     + [cst.resources[name] for cst in costs
                        if name in cst.resources])
            is_res = any(r.is_resource for r in rows_)

            def avg(attr, runs=rows_):
                return sum(getattr(r, attr) for r in runs) / len(runs)
            kind = "Resource" if is_res else "Spare"
            pool_rows.append([
                f"{name} [{kind}]",
                (money(avg("initial")), avg("initial")),
                (money(avg("new_spares")), avg("new_spares")),
                (money(avg("emer_ship")), avg("emer_ship")),
                (money(avg("total")), avg("total"))])
        if pool_rows:
            time_unit = f" ({self._units})" if self._units else ""
            add_table(
                "Pools",
                ["Name", "Initial", f"New Spares / Per-Time{time_unit}",
                 f"Emer. Shipping / Per-Use{time_unit}", "Total"],
                pool_rows)

        # Events (initial/success/failure charges).
        event_ids = sorted({eid for cst in costs for eid in cst.events})
        if event_ids:
            event_rows = []
            for eid in event_ids:
                per_run = [cst.events[eid] for cst in costs
                           if eid in cst.events]

                def avg(attr, runs=per_run):
                    return sum(getattr(r, attr) for r in runs) / len(runs)
                event = per_run[0]
                event_rows.append([
                    event.name,
                    (money(avg("initial")), avg("initial")),
                    (money(avg("success")), avg("success")),
                    (money(avg("failure")), avg("failure"))])
            add_table("Events", ["Name", "Initial", "Success", "Failure"],
                      event_rows)

        # Hierarchies: child-cost rollups.
        hier_ids = sorted({hid for cst in costs for hid in cst.hiers})
        if hier_ids:
            hier_rows = []
            for hid in hier_ids:
                per_run = [cst.hiers[hid] for cst in costs
                           if hid in cst.hiers]

                def avg(attr, runs=per_run):
                    return sum(getattr(r, attr) for r in runs) / len(runs)
                h = per_run[0]
                hier_rows.append([
                    h.name,
                    (money(avg("purchase")), avg("purchase")),
                    (money(avg("operating")), avg("operating")),
                    (money(avg("sparing")), avg("sparing")),
                    (money(avg("disposal")), avg("disposal")),
                    (money(avg("events")), avg("events")),
                    (money(avg("total")), avg("total"))])
            add_table(
                "Hierarchies",
                ["Name", "Purchase", "Operating", "Sparing", "Disposal",
                 "Events", "Total"],
                hier_rows)

        # The report's per-state operating cost and per-block sparing cost
        # (not in the snapshots' life-cycle view above).
        from raptor.api.presentation import table_title
        for title, columns, rows in getattr(self, "_cost_extra", []):
            table = self._section_widget(title, columns, rows)
            self._add_table_header(v, table_title(title), table)
            v.addWidget(table)
            self._cost_tables.append(table)

        v.addStretch(1)
        return page

    @staticmethod
    def _label_header(title: str) -> str:
        return {"SUMMARY": "Measure", "LOGISTICS": "Measure"}.get(title, "Name")

    def set_level_filter(self, allowed_names: set[str] | None):
        """`None` = show everything (home level); a name set hides any
        Sparing/Capacity row whose first-column element is outside it."""
        self._allowed_names = allowed_names
        self._apply_level_filter()


    def _apply_level_filter(self):
        from shiboken6 import isValid
        allowed = getattr(self, "_allowed_names", None)

        def alive(w):
            return w is not None and isValid(w)

        # Summary/Logistics are system-wide measures (Availability, MTBDE,
        # ...). Raptor 7 gives those two tabs no hierarchy filter at
        # all (`FillResultsTable` only wires HierFilter into Sparing/Cost/
        # Analysis/Capacity); filtering them by element name would hide
        # every measure row (a block is never named "MTBDE").
        _MEASURE_TABLES = ("SUMMARY", "LOGISTICS")
        tables: list[QTableWidget] = [
            t for k, t in self._detail_tables.items()
            if alive(t) and k not in _MEASURE_TABLES]
        cap = getattr(self, "_cap_table", None)
        if cap is not None and alive(cap):
            tables.append(cap)
        tables.extend(t for t in getattr(self, "_cost_tables", [])
                      if alive(t))
        # Weak Link tables live in their own page (not the section
        # registry) — include them so hierarchy filtering covers them
        weak_page = None
        for i in range(self._details_tabs.count()):
            if self._details_tabs.tabText(i) == "Weak Link":
                weak_page = self._details_tabs.widget(i)
                break
        if weak_page is not None:
            from PySide6.QtWidgets import QTableWidget as _QT
            tables.extend(t for t in weak_page.findChildren(_QT)
                          if alive(t))
        # Stale entries whose pages were deleteLater'd with an old
        # populate must not crash the filter.
        self._detail_tables = {
            k: t for k, t in self._detail_tables.items() if alive(t)}
        self._cost_tables = [t for t in
                             getattr(self, "_cost_tables", [])
                             if alive(t)]
        for table in tables:
            for r in range(table.rowCount()):
                item = table.item(r, 0)
                shown = (item.text().split("(")[0].strip()
                         if item else "")
                # Prefer the item's full name (kept as UserRole data when
                # the shown label was elided); fall back to a prefix match
                # so a name truncated somewhere in the report chain still
                # matches its one allowed owner.
                name = item.data(Qt.ItemDataRole.UserRole) if item is not None else None
                if not name:
                    name = shown
                if allowed is not None and name and name not in allowed:
                    matches = [a for a in allowed
                               if a.startswith(shown)]
                    table.setRowHidden(r, len(matches) != 1)
                else:
                    table.setRowHidden(r, allowed is not None
                                       and bool(name)
                                       and name not in allowed)
        label = getattr(self, "_cost_blocks_label", None)
        title = getattr(self, "_cost_blocks_title", "")
        cost_tables = getattr(self, "_cost_tables", [])
        if label is not None and alive(label) and cost_tables:
            table = cost_tables[0]
            count = sum(1 for r in range(table.rowCount())
                        if not table.isRowHidden(r))
            suffix = title.split(")", 1)[1] if ")" in title else ""
            label.setText(f"Blocks ({count}){suffix}")

    def _build_importance_page(self) -> QWidget:
        """Importance measures for the blocks, ranked by Birnbaum.

        The numbers are the `raptor.api.importance` result for this run; the
        *definitions* live on the formulas page (Help > Formulas) and are not
        restated here -- that duplication is what this tab replaces.
        """
        from raptor.api.importance import METRICS

        page = QWidget()
        v = QVBoxLayout(page)
        v.setContentsMargins(SPACING["md"], SPACING["md"],
                             SPACING["md"], SPACING["md"])
        v.setSpacing(SPACING["sm"])

        data = self._importance or {}
        metrics = {label: entry for label, entry in
                   (data.get("metrics") or {}).items()}
        if not metrics:
            empty = QLabel(
                "No importance measures for this run.\n\n"
                "Enable Importance measures in the Run panel's Analysis "
                "section and run again. It costs one extra batch per block "
                "(two for Both), so it is off unless asked for.")
            empty.setObjectName("SectionNote")
            empty.setWordWrap(True)
            v.addWidget(empty)
            v.addStretch(1)
            return page

        chosen = self._importance_metric
        if chosen not in metrics:
            chosen = next(iter(metrics))
        entry = metrics[chosen]
        rows = entry.get("rows") or []

        def _num(value, places):
            return "\u2014" if value is None else f"{value:,.{places}f}"

        # One ranking rule for the tab, the report and the overlay. Birnbaum
        # alone is not it: in a series model every block's variant moves the
        # system from ~0 to ~1, so it saturates for all of them and ranks
        # nothing. See `raptor.api.importance.RANKING_ORDER`.
        from raptor.api.importance import RANKING_LABELS, ranked as _ranked
        from raptor.api.importance import ranking_measure

        measure_label = RANKING_LABELS[ranking_measure(rows)]
        top = next(iter(_ranked(rows)), None)
        head = QLabel(
            f"Ranked by {measure_label} importance, measured on "
            f"<b>{chosen}</b>. Baseline {_num(entry.get('baseline'), 4)}. "
            f"{data.get('blocks', len(rows))} blocks, "
            f"{data.get('batches', 0)} batches of {data.get('runs', 0)} runs."
            + (f" Most influential: <b>{top['block']}</b>." if top else ""))
        head.setObjectName("SummarySentence")
        head.setWordWrap(True)
        v.addWidget(head)

        picker = QHBoxLayout()
        picker.addWidget(QLabel("Measured on:"))
        combo = QComboBox()
        for _attr, label in METRICS:
            if label in metrics:
                combo.addItem(label, label)
        combo.setCurrentIndex(combo.findData(chosen))
        combo.currentIndexChanged.connect(
            lambda _i, c=combo: self._set_importance_metric(c.currentData()))
        picker.addWidget(combo)
        picker.addStretch(1)
        v.addLayout(picker)

        # Rank once, by Birnbaum descending, undefined last -- the table and
        # the tooltips must walk the SAME order or a hover explains the
        # wrong block.
        ordered = _ranked(rows)
        table_rows = [[
            r["block"],
            _num(r.get("birnbaum"), 4),
            _num(r.get("raw"), 2),
            _num(r.get("rrw"), 2),
            _num(r.get("vesely_fussell"), 3),
        ] for r in ordered]
        table = self._make_table(
            ["Block", "Birnbaum", "RAW", "RRW", "Vesely\u2013Fussell"],
            table_rows, first_col_label="Block")
        # The columns are bare acronyms. A header tooltip is the only place
        # to say what each one answers without adding a paragraph the
        # reader has to skip; the equations stay on Help > Formulas, which
        # is the single source (`docs/formulas.adoc`) -- these are one-line
        # readings, not a second definition.
        for col, tip in enumerate(IMPORTANCE_HEADER_TIPS):
            h_item = table.horizontalHeaderItem(col)
            if h_item is not None:
                h_item.setToolTip(tip)
        for i, r in enumerate(ordered):
            item = table.item(i, 0)
            if item is None:
                continue
            item.setToolTip(
                f"{r['block']}\n\n"
                f"System {chosen.lower()} with this block unable to fail: "
                f"{_num(r.get('perfect'), 4)}\n"
                f"with this block dead: {_num(r.get('failed'), 4)}\n"
                f"Combined standard error of the difference: "
                f"{_num(r.get('sem'), 4)}")
        v.addWidget(table)

        note = QLabel(
            "Estimated by re-simulation: the model is run once with each "
            "block unable to fail and once with it dead. These are Monte "
            "Carlo estimates, not analytical derivatives -- the standard "
            "error says how much of the ranking is sampling noise. "
            "Definitions and equations: Help \u25b8 Formulas.")
        note.setObjectName("SectionNote")
        note.setWordWrap(True)
        v.addWidget(note)
        for text in data.get("notes", []):
            extra = QLabel(text)
            extra.setObjectName("SectionNote")
            extra.setWordWrap(True)
            v.addWidget(extra)
        v.addStretch(1)
        return page

    def _set_importance_metric(self, label: str) -> None:
        """Swap the measured metric without re-simulating."""
        if label == self._importance_metric:
            return
        self._importance_metric = label
        self._populate_details(getattr(self, "_final_lines", []) or [])
        self.importance_metric_changed.emit(label)

    def section_table(self, title: str) -> QTableWidget | None:
        """The table for a Final Results section, by section name.

        Accepts the plain section name even when the report held several
        tables under that one marker: `parse_final_sections` splits those
        and names each `"<SECTION> - <sub-table>"` (COST is seven tables
        plus a summary tail; one shared header row is what put values in
        unlabelled columns F..J). A bare `"COST"` therefore returns the
        first of them."""
        found = self._detail_tables.get(title)
        if found is not None:
            return found
        prefix = f"{title} - "
        return next((t for name, t in self._detail_tables.items()
                     if name.startswith(prefix)), None)

    def _build_event_log_tab(self):
        page = QWidget()
        lay = QVBoxLayout(page)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(0)

        top = QHBoxLayout()
        top.setContentsMargins(SPACING["sm"], SPACING["xs"], SPACING["sm"], SPACING["xs"])
        top.addWidget(QLabel("Run:"))
        self._run_selector = QComboBox()
        self._run_selector.setMaximumWidth(180)
        self._run_selector.currentIndexChanged.connect(self._show_selected_run)
        top.addWidget(self._run_selector, 0)
        top.addSpacing(SPACING["lg"])
        top.addWidget(QLabel("Filter:"))
        self._filter_edit = QLineEdit()
        self._filter_edit.setPlaceholderText("e.g. Failed, a block name…")
        self._filter_edit.textChanged.connect(self._apply_log_filter)
        self._filter_edit.setMaximumWidth(280)
        top.addWidget(self._filter_edit, 1)
        lay.addLayout(top)

        # A model, not a QTreeWidgetItem per line: see event_log_model.py.
        self._log_model = EventLogModel(
            lambda state: self._state_colour(state, active_colour()), self)
        self._log_tree = QTreeView()
        self._log_tree.setModel(self._log_model)
        self._log_tree.setRootIsDecorated(False)
        self._log_tree.setUniformRowHeights(True)
        self._log_tree.clicked.connect(self._on_log_row_clicked)
        self._log_tree.header().resizeSection(0, 90)
        self._log_tree.header().resizeSection(1, 60)
        self._log_tree.header().resizeSection(2, 160)
        self._log_tree.header().resizeSection(3, 110)
        # Past this many rows a filter pass is worth debouncing.
        self._log_filter_timer = QTimer(self)
        self._log_filter_timer.setSingleShot(True)
        self._log_filter_timer.setInterval(150)
        self._log_filter_timer.timeout.connect(self._run_log_filter)
        # Shown instead of an empty tree when the run was produced
        # without event-log collection, explaining why the tab is empty
        # and what enabling it costs.
        self._log_disabled_label = QLabel(
            "No event log is available for this result set — either "
            "Detailed event log was off, or the run stopped before any "
            "event was recorded.\n\nEnable it in the Run panel ▸ "
            "Recording (Detailed event log). For long simulations the "
            "event log may use significant disk space.")
        self._log_disabled_label.setWordWrap(True)
        self._log_disabled_label.setVisible(False)
        # Filter-specific empty state: a carried-over filter that matches
        # nothing used to leave a blank grid that read as "no events".
        self._log_empty_label = QLabel("")
        self._log_empty_label.setWordWrap(True)
        self._log_empty_label.setObjectName("SectionNote")
        self._log_empty_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._log_empty_label.setVisible(False)
        lay.addWidget(self._log_disabled_label)
        lay.addWidget(self._log_empty_label)
        lay.addWidget(self._log_tree, 1)
        self._event_page = page

    def _build_graphs_tab(self):
        """Ao availability graphs: Per-Trial, Interval, and Multi-run plots fed by the
        engine's `_ao_sink` rows."""
        from raptor.gui.ui.plots import AoPlotWidget
        from PySide6.QtWidgets import QPushButton
        page = QWidget()
        v = QVBoxLayout(page)
        v.setContentsMargins(SPACING["md"], SPACING["md"],
                             SPACING["md"], SPACING["md"])
        v.setSpacing(SPACING["sm"])
        top = QHBoxLayout()
        self._graph_mode = QComboBox()
        self._graph_mode.addItems(["Per Trial Ao", "Interval Ao",
                                   "Multi-run Ao", "Capacity (Flow)",
                                   "Multi-run Capacity (Flow)",
                                   "Availability Convergence"])
        self._graph_mode.currentIndexChanged.connect(self._on_graph_mode)
        top.addWidget(QLabel("Plot:"))
        top.addWidget(self._graph_mode)
        self._graph_run = QComboBox()
        self._graph_run.currentIndexChanged.connect(self._on_graph_run)
        top.addWidget(QLabel("Run:"))
        top.addWidget(self._graph_run)
        top.addStretch(1)
        self._y_axis_btn = QPushButton("Y-axis min…")
        self._y_axis_btn.setToolTip(
            "Set the lowest value on the availability graphs' Y axis "
            "(0 up to 0.999999); Multi-run Ao auto-fits its own range. "
            "Saved with the model.")
        self._y_axis_btn.clicked.connect(self._ask_y_axis_min)
        top.addWidget(self._y_axis_btn)
        save_img_btn = QPushButton("Save Image…")
        save_img_btn.setToolTip(
            "Save the currently displayed plot as a PNG image. "
            "The underlying numbers are available separately via "
            "Export CSV on the Final Results tab.")
        save_img_btn.clicked.connect(self._save_graph_image)
        top.addWidget(save_img_btn)
        # Export the raw plot data: with the Files & Reports plots
        # streamed to disk, this is the "export on demand" path. The
        # default name mirrors Raptor 7's rpt_* file convention.
        export_data_btn = QPushButton("Export Data…")
        export_data_btn.setToolTip(
            "Save the current plot's underlying data rows to a text "
            "file (Availability plot -> rpt_availability_plot_<model>"
            ".txt, Capacity plot -> rpt_capacity_plot_<model>.txt)")
        export_data_btn.clicked.connect(self._export_graph_data)
        top.addWidget(export_data_btn)
        self._graph_note = QLabel("")
        self._graph_note.setObjectName("SectionNote")
        self._graph_note.hide()
        top.addWidget(self._graph_note)
        v.addLayout(top)
        self._ao_plot = AoPlotWidget()
        v.addWidget(self._ao_plot, 1)
        self._graphs_page = page

    def set_y_axis_min(self, value: float) -> None:
        """The model's saved `graphYmin`, as the graphs' lower bound (no signal, no dirty flag)."""
        self._ao_plot.set_y_min(value)

    def _ask_y_axis_min(self) -> None:
        """Raptor 7's "Set Y-Axis" box: the minimum must be at least
        0 and below 1; an out-of-range value is corrected and the box says why."""
        from PySide6.QtWidgets import QInputDialog
        value, ok = QInputDialog.getDouble(
            self, "Set Y-Axis", "Y-axis minimum:", self._ao_plot.y_min, -1.0, 2.0, 6)
        if not ok:
            return
        if value < 0.0:
            msgbox.warning(self, "Set Y-Axis",
                                "Y-axis minimum must be greater than or equal to 0.0.")
            value = 0.0
        elif value > 0.999999:
            msgbox.warning(self, "Set Y-Axis", "Y-axis minimum must be less than 1.0.")
            value = 0.999999
        if value != self._ao_plot.y_min:
            self._ao_plot.set_y_min(value)
            self.y_axis_min_changed.emit(value)

    #: Plot-file parsing guard: beyond this many rows the interactive
    #: view shows only the first slice and says so — the full data stays
    #: in the stream file for Export Data.
    _MAX_PARSE_ROWS = 200_000
    #: Live per-run refresh guard for the Capacity plot (same rationale,
    #: applied to the per-progress-tick re-parse).
    _LIVE_CAP_REFRESH_ROWS = 50_000

    @staticmethod
    def _stream_lines(src) -> list[str]:
        """Rows from a Files & Reports sink: a disk-backed ResultStream
        reads lazily from its temp file (never materialised in RAM by
        the engine); a plain list is returned as-is (tests)."""
        if src is None:
            return []
        if hasattr(src, "read_all_lines"):
            return src.read_all_lines()
        return src

    def _export_graph_data(self) -> None:
        mode = self._graph_mode.currentIndex()
        if mode == 3:   # Capacity (Flow)
            src, stem = getattr(self, "_cap_lines", None), "rpt_capacity_plot"
        else:
            src, stem = getattr(self, "_ao_lines", None), "rpt_availability_plot"
        lines = self._stream_lines(src)
        if not lines:
            self._status_hint_f and self._status_hint_f(
                "No plot data collected — tick the plot under Run panel ▸ "
                "Recording and run again")
            return
        base = (self._source_file_name or "model").rsplit(".", 1)[0]
        safe = "".join(c if c.isalnum() or c in "_-" else "_"
                       for c in base)[:60] or "model"
        path, _f = fdg.getSaveFileName(
            self, "Export Plot Data",
            f"{stem}_{safe}.txt", "Text (*.txt);;All (*)")
        if not path:
            return
        try:
            if hasattr(src, "export_to"):
                req(src).export_to(path)
            else:
                with open(path, "w", encoding="utf-8") as f:
                    f.write("\n".join(lines) + "\n")
            self._status_hint_f and self._status_hint_f(f"Saved: {path}")
        except OSError as e:
            msgbox.critical(self, "Export Failed",
                                 f"Could not write {path}:\n{e}")

    def _save_graph_image(self) -> None:
        if self._ao_plot.width() <= 0 or self._ao_plot.height() <= 0:
            return
        pm = self._ao_plot.grab()
        if pm.isNull():
            return
        mode = self._graph_mode.currentText().replace(" ", "_").lower()
        path, _f = fdg.getSaveFileName(
            self, "Save Graph Image", f"{mode}.png", "PNG Images (*.png)")
        if not path:
            return
        if not pm.save(path, "PNG"):
            msgbox.critical(self, "Export Failed",
                                 f"Could not write {path}")
            return

    def set_capacity_available(self, available: bool) -> None:
        """Grey the Capacity (Flow) plot mode out when the model has no
        Capacity Analysis. Re-enabled on model load / analysis toggle,
        and force-enabled by set_results when real capacity data
        arrives."""
        indexes = [self._graph_mode.findText("Capacity (Flow)"),
                   self._graph_mode.findText("Multi-run Capacity (Flow)")]
        for idx in (i for i in indexes if i >= 0):
            cast(QStandardItemModel,
                 self._graph_mode.model()).item(idx).setEnabled(
                bool(available))
            if not available and self._graph_mode.currentIndex() == idx:
                self._graph_mode.setCurrentIndex(0)

    def _on_graph_mode(self, index: int):
        from raptor.gui.ui.plots import AoPlotWidget
        self._ao_plot.set_mode(index)
        # Multi-run Ao and Convergence both aggregate across ALL runs;
        # the rest follow the run selector.
        self._graph_run.setEnabled(
            index not in (AoPlotWidget.MULTI_RUN,
                          AoPlotWidget.MULTI_CAPACITY,
                          AoPlotWidget.CONVERGENCE))
        # The Y-axis-minimum control applies to availability plots only;
        # capacity paints a data-derived axis and ignored it.
        cap_mode = index in (AoPlotWidget.CAPACITY,
                             AoPlotWidget.MULTI_CAPACITY)
        conv = index == AoPlotWidget.CONVERGENCE
        self._y_axis_btn.setEnabled(not (cap_mode or conv))
        self._y_axis_btn.setToolTip(
            "Not used by capacity plots: their Y axis is derived from the "
            "flow/capacity data." if cap_mode else
            "Not used by the convergence plot: its Y scale is fitted to "
            "the cumulative means." if conv else
            "Set the lowest value on the availability graphs' Y axis "
            "(0 up to 0.999999); Multi-run Ao auto-fits its own range. "
            "Saved with the model.")

    def _on_graph_run(self, index: int):
        self._ao_plot.set_run(index)

    def _update_graphs(self):
        """Re-parse `_ao_lines`/`_cap_lines` and refresh plot + selector.

        The sinks may be plain lists (tests) or disk-backed ResultStreams
        (Files & Reports opt-in): rows are read lazily from the file and
        the interactive view is capped at `_MAX_PARSE_ROWS` with a
        visible note, so the engine never holds them in RAM."""
        from raptor.gui.ui.plots import parse_ao_runs, parse_cap_runs
        ao_all = self._stream_lines(self._ao_lines)
        capped = len(ao_all) > self._MAX_PARSE_ROWS
        runs = parse_ao_runs(ao_all[:self._MAX_PARSE_ROWS]
                             if capped else ao_all)
        self._ao_plot.set_data(runs)
        cap_all = self._stream_lines(getattr(self, "_cap_lines", None) or [])
        cap_runs = parse_cap_runs(
            cap_all[:self._MAX_PARSE_ROWS] if capped else cap_all)
        self._ao_plot.set_cap_data(cap_runs)
        self._update_convergence_data()
        n = max(len(runs), len(cap_runs))
        self._sync_graph_modes(runs, cap_runs, n)
        self._graph_run.blockSignals(True)
        self._graph_run.clear()
        self._graph_run.addItems([f"Run {i + 1}" for i in range(n)])
        if n:
            self._graph_run.setCurrentIndex(0)
        self._graph_run.blockSignals(False)
        self._graph_run.setEnabled(
            n > 0 and self._graph_mode.currentIndex() not in (2, 4, 5))
        if capped:
            self._graph_note.setText(
                f"Showing the first {self._MAX_PARSE_ROWS:,} rows — use "
                f"Export Data… for the full set ({len(ao_all):,} rows)")
            self._graph_note.show()
        else:
            self._graph_note.hide()

    def _sync_graph_modes(self, runs, cap_runs, n_runs) -> None:
        """Enable only plot modes that can actually draw something.

        A disabled mode carries the exact missing prerequisite in its
        tooltip. Capacity used to be enabled whenever the analysis flag was
        on, even when the user had not ticked Capacity plot recording, so
        the tab offered a guaranteed-empty chart.
        """
        has_ao = bool(runs)
        has_interval = any(
            any(v is not None for v in run.get("iv", []))
            for run in runs)
        has_multi = len(runs) >= 2
        has_cap = bool(cap_runs)
        has_multi_cap = len(cap_runs) >= 2
        has_convergence = len(self._report_data or []) >= 2
        reasons = {
            0: ("No availability data — tick Availability plot under "
                "Run panel ▸ Recording and run again." if not has_ao else ""),
            1: ("No interval samples — tick Availability plot under "
                "Run panel ▸ Recording and run again." if not has_interval else ""),
            2: ("Need at least 2 runs with availability plot data." if not has_multi else ""),
            3: ("No capacity plot data — tick Capacity plot under Run "
                "panel ▸ Recording and run again." if not has_cap else ""),
            4: ("Need at least 2 runs with capacity plot data." if not has_multi_cap else ""),
            5: ("Need at least 2 runs to show convergence." if not has_convergence else ""),
        }
        enabled = [has_ao, has_interval, has_multi, has_cap,
                   has_multi_cap, has_convergence]
        for i, ok in enumerate(enabled):
            item = cast(QStandardItemModel, self._graph_mode.model()).item(i)
            if item is not None:
                item.setEnabled(ok)
                item.setToolTip(reasons.get(i, "") if not ok else "")
        if not enabled[self._graph_mode.currentIndex()]:
            first = next((i for i, ok in enumerate(enabled) if ok), 0)
            self._graph_mode.setCurrentIndex(first)
        self._graph_run.setEnabled(
            n_runs > 0 and self._graph_mode.currentIndex() not in (2, 4, 5))

    def _update_convergence_data(self):
        """Cumulative mean Availability/Dependability vs. run count
        (Graphs tab's "Availability Convergence" plot) -- same metric-
        selection logic as the Overview card's own `use_dependability`
        (the model's configured `nodeAnalType`, falling back to
        Availability when Dependability isn't computed for this
        model), so the two views never disagree about which number
        they're both showing."""
        data = self._report_data or []
        use_dependability = (
            weak_metric.canonical(self._weak_anal_type)
            == weak_metric.DEPENDABILITY
            and any(d.dependability is not None for d in data))
        getter = ((lambda d: d.dependability) if use_dependability
                 else (lambda d: d.availability))
        vals = [getter(d) for d in data if getter(d) is not None]
        points: list[tuple[float, float]] = []
        running_sum = 0.0
        for i, v in enumerate(vals, start=1):
            running_sum += v
            points.append((float(i), running_sum / i))
        self._ao_plot.set_convergence_data(
            points, "Dependability" if use_dependability else "Availability")

    def _build_raw_tab(self):
        page = QWidget()
        outer = QVBoxLayout(page)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(0)

        bar = QHBoxLayout()
        bar.setContentsMargins(SPACING["md"], SPACING["xs"],
                               SPACING["md"], 0)
        from PySide6.QtWidgets import QPushButton
        save_btn = QPushButton("Save As…")
        save_btn.setToolTip(
            "Save the currently selected sub-tab's report text "
            "verbatim as a .txt file.")
        save_btn.clicked.connect(self._save_raw_report)
        appc_btn = QPushButton("Export Data Files…")
        appc_btn.setToolTip(
            "Data files: Key Parameters, Ending "
            "Simulation Times, Availability Plot Data (per-trial data "
            "already computed for this run, just never written to "
            "disk in these formats)")
        appc_btn.clicked.connect(self._export_appendix_c_files)
        bar.addStretch(1)
        bar.addWidget(appc_btn)
        bar.addWidget(save_btn)
        outer.addLayout(bar)

        raw_tabs = QTabWidget()
        raw_tabs.setDocumentMode(True)
        self._raw_views: dict[str, QPlainTextEdit] = {}
        for name in ("End of Run", "Final Results"):
            view = QPlainTextEdit()
            view.setReadOnly(True)
            view.setFont(_mono_font())
            view.setLineWrapMode(QPlainTextEdit.LineWrapMode.NoWrap)
            self._raw_views[name] = view
            raw_tabs.addTab(view, name)
        # Interim Cost Report: the engine's periodic
        # cost snapshots (`_cost_sink` rows), byte-exact.
        self._interim_cost_view = QPlainTextEdit()
        self._interim_cost_view.setReadOnly(True)
        self._interim_cost_view.setFont(_mono_font())
        self._interim_cost_view.setLineWrapMode(QPlainTextEdit.LineWrapMode.NoWrap)
        self._interim_cost_view.hide()
        raw_tabs.addTab(self._interim_cost_view, "Interim Cost")
        self._raw_tabs = raw_tabs
        outer.addWidget(raw_tabs, 1)
        self._raw_page = page

    def _export_appendix_c_files(self) -> None:
        """Appendix C "Files and Reports": the data files Raptor 7 writes
        beside its reports -- Key Parameters, All System Failure Times, All
        System Down Times, Ending Simulation Times and Availability Plot Data.
        The first four are built by `raptor.core.engine.data_files` from this
        run's own `RunReportData` and match Raptor 7's files byte for byte (CRLF);
        the CLI and API use the same functions."""
        from PySide6.QtWidgets import (
            QDialog, QVBoxLayout as _VBox, QCheckBox, QLabel as _Label,
            QPushButton as _Btn, QHBoxLayout as _HBox,
        )
        if not self._report_data:
            msgbox.information(
                self, "Export Data Files",
                "Run the simulation first — these files are built "
                "from this run's own per-trial results.")
            return

        dlg = QDialog(self)
        dlg.setWindowTitle("Export Data Files")
        lay = _VBox(dlg)
        note = _Label(
            "One file per dataset, from this run's own results.")
        note.setWordWrap(True)
        lay.addWidget(note)
        cb_key = QCheckBox("Key Parameters (Ao, Do, R, RCond, MTBDE, "
                           "MDT, MTBM, MMT — one row per trial)")
        cb_fail = QCheckBox("All System Failure Times (time to each system "
                            "failure, from when statistics start)")
        cb_down = QCheckBox("All System Down Times (time to each system "
                            "recovery, from when statistics start)")
        cb_end = QCheckBox("Ending Simulation Times (one value per "
                           "trial)")
        for cb in (cb_key, cb_fail, cb_down, cb_end):
            cb.setChecked(True)
        cb_ao = QCheckBox("Availability Plot Data (time-truncated "
                          "runs only)")
        cb_ao.setChecked(bool(getattr(self, "_ao_lines", None)))
        cb_ao.setEnabled(bool(getattr(self, "_ao_lines", None)))
        if not cb_ao.isEnabled():
            cb_ao.setToolTip(
                "Not available for this run — Availability Plot Data "
                "only generates for time-truncated simulations")
        for cb in (cb_key, cb_fail, cb_down, cb_end, cb_ao):
            lay.addWidget(cb)

        buttons = _HBox()
        cancel = _Btn("Cancel")
        export = _Btn("Export…")
        cancel.clicked.connect(dlg.reject)
        export.clicked.connect(dlg.accept)
        buttons.addStretch(1)
        buttons.addWidget(cancel)
        buttons.addWidget(export)
        lay.addLayout(buttons)

        # Non-modal (`.show()`, not `.exec()`): `.exec()` blocks with
        # nothing able to dismiss it under headless/offscreen test
        # automation. `self._appendix_c_dialog` keeps a reference so the
        # dialog isn't garbage-collected before `accepted` fires.
        self._appendix_c_dialog = dlg
        self._appendix_c_checks = (cb_key, cb_fail, cb_down, cb_end, cb_ao)
        dlg.accepted.connect(self._do_export_appendix_c_files)
        dlg.show()

    def _do_export_appendix_c_files(self) -> None:
        from raptor.gui.ui import filedialogs as fdg
        directory = fdg.getExistingDirectory(
            self, "Export Data Files To…")
        if not directory:
            return
        import os
        from raptor.core.engine import data_files
        cb_key, cb_fail, cb_down, cb_end, cb_ao = self._appendix_c_checks
        wanted = [("key_parameters", cb_key), ("failure_times", cb_fail),
                  ("down_times", cb_down), ("end_times", cb_end)]
        written = []
        try:
            for kind, cb in wanted:
                if not cb.isChecked():
                    continue
                name = f"rpt_{kind}.txt"
                lines = data_files.DATA_FILES[kind](self._report_data)
                with open(os.path.join(directory, name), "w",
                          encoding="latin-1", newline="") as f:
                    f.write(data_files.legacy_text(lines))
                written.append(name)
            if cb_ao.isChecked() and getattr(self, "_ao_lines", None):
                name = "rpt_availability_plot.txt"
                with open(os.path.join(directory, name), "w",
                          encoding="utf-8", newline="") as f:
                    f.write("\r\n".join(self._ao_lines) + "\r\n")
                written.append(name)
        except OSError as e:
            msgbox.critical(self, "Export Failed",
                                 f"Could not write to {directory}:\n{e}")
            return
        if written:
            sb = getattr(self.window(), "statusBar", None)
            if sb is not None:
                sb().showMessage(
                    f"Exported {', '.join(written)} to {directory}")

    def _save_raw_report(self) -> None:
        idx = self._raw_tabs.currentIndex()
        if idx < 0:
            return
        name = self._raw_tabs.tabText(idx)
        view = self._raw_tabs.widget(idx)
        text = cast(QPlainTextEdit, view).toPlainText() if view is not None else ""
        if not text.strip():
            return
        default = name.replace(" ", "_").lower() + ".txt"
        path, _f = fdg.getSaveFileName(
            self, "Save Raw Report", default, "Text files (*.txt)")
        if not path:
            return
        try:
            with open(path, "w", encoding="utf-8") as f:
                f.write(text)
        except OSError as e:
            msgbox.critical(self, "Export Failed",
                                 f"Could not write {path}:\n{e}")

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def has_results(self) -> bool:
        """A result set is on screen (as opposed to the placeholder)."""
        return bool(self._report_data or self._event_logs)

    def results_bundle(self, model_name: str = "", units: str = ""):
        """The result set on screen, as the plain bundle the table export
        (and the MCP server) builds its result tables from. None when no
        run has produced results."""
        if not self.has_results():
            return None
        from raptor.api.run import _rd_to_dict
        from raptor.api.tables import ResultsBundle
        return ResultsBundle(
            model_name=model_name, num_runs=len(self._report_data),
            units=units, final_lines=list(self._final_lines),
            end_of_run_lines=list(getattr(self, "_eor_lines", [])),
            per_run=[_rd_to_dict(rd) for rd in self._report_data
                     if rd is not None],
            capacity_lines=list(self._stream_lines(
                getattr(self, "_cap_lines", None))),
            cost_lines=list(self._stream_lines(
                getattr(self, "_cost_lines", None))),
            event_logs=[list(x) if isinstance(x, (list, tuple)) else [str(x)]
                        for x in self._event_logs])

    def run_info(self, rbd, *, model: str = "", file: str = ""):
        """The run information a saved result carries (Run Info sheet,
        report cover): `raptor.api.presentation.run_info` with this result
        set's run count, manifest and time."""
        from raptor.api.presentation import run_info
        return run_info(rbd, model=model, file=file,
                        runs=len(self._report_data),
                        units=getattr(self, "_units", "") or "",
                        run_at=getattr(self, "_run_at", None),
                        manifest=getattr(self, "_manifest", None))

    def clear(self):
        """Reset every result surface to the pre-run placeholder state."""
        self._event_logs = []
        self._report_data = []
        self._importance = None
        self._importance_metric = "Availability"
        self._final_lines = []
        self._eor_lines = []
        # Plot data belongs to the cleared result set. Leaving it behind
        # let Graphs show an old run's curve under the "No results"
        # placeholder, and left the run selector enabled with zero runs.
        self._ao_lines = []
        self._cap_lines = []
        self._cost_lines = []
        self._run_selector.clear()
        self._log_model.clear()
        if hasattr(self, "_log_disabled_label"):
            self._log_disabled_label.setVisible(False)
            self._log_tree.setVisible(True)
        self._runs_table.clear()
        # Removing tabs emits currentChanged; without the lock, teardown
        # records whichever tab happens to be last (Advanced) and every
        # rerun lands there instead of restoring the user's tab.
        self._tab_memory_lock = True
        try:
            while self._details_tabs.count():
                w = self._details_tabs.widget(0)
                self._details_tabs.removeTab(0)
                req(w).deleteLater()
        finally:
            self._tab_memory_lock = False
        self._detail_tables.clear()
        self._summary_sentence.setText("")
        for card in self._cards.values():
            card.set_value("—")
        for view in self._raw_views.values():
            view.clear()
        if hasattr(self, "_ao_plot"):
            self._ao_plot.set_data([])
            self._ao_plot.set_cap_data([])
            self._ao_plot.set_convergence_data([])
            self._graph_run.clear()
            self._graph_run.setEnabled(False)
            self._graph_mode.setCurrentIndex(0)
            self._graph_note.hide()
        self._tabs.hide()
        self._placeholder.show()
        self.contents_changed.emit()

    def set_results(self, event_logs, end_of_run_lines, final_results_lines,
                    report_data=None, units: str | None = None, notes=None,
                    ao_lines: list[str] | None = None,
                    cap_lines: list[str] | None = None,
                    cost_lines: list[str] | None = None,
                    manifest: dict | None = None,
                    importance: dict | None = None):
        # The run manifest travels WITH the results, so a result set can
        # say what it is without trusting a model file that may since
        # have been edited. It also means changing a run setting (trial
        # count, run length, which analyses ran) does not retroactively
        # invalidate results already on screen -- the model hash in the
        # manifest covers engineering content only.
        self._manifest = dict(manifest) if manifest else None
        from datetime import datetime
        self._run_at = datetime.now()
        self._event_logs = event_logs
        self._report_data = list(report_data or [])
        # The importance analysis belongs to a run, not to the model, so it
        # travels with the results like the manifest does.
        self._importance = importance
        self._final_lines = final_results_lines
        self._eor_lines = list(end_of_run_lines or [])
        # Ao-sink rows from the engine (Raptor 7 availability graphs)
        # and capacity-sink rows (Raptor 7 flow plot); empty when the caller
        # didn't request sampling or the model lacks the analysis. May be
        # plain lists (tests) or disk-backed ResultStreams (Files &
        # Reports opt-in) — read lazily, never materialised in RAM here.
        self._ao_lines = ao_lines if ao_lines is not None else []
        # Keep the last chosen metric across re-populations; reset only when
        # the new run has no such metric (handled in `_build_importance_page`).
        if not hasattr(self, "_importance_metric"):
            self._importance_metric = "Availability" 
        self._cap_lines = cap_lines if cap_lines is not None else []
        # Real capacity data arrived: the plot mode is available again
        # even if the model flag lookup missed it (Files & Reports).
        if self._cap_lines and len(self._cap_lines) > 0:
            self.set_capacity_available(True)
        # A fresh results set ends the live-capacity tab ownership (see
        # update_live_capacity) -- the next run may autoswitch again.
        self._live_cap_autoswitch = False
        self._live_ao_autoswitch = False
        # Interim Cost Report rows, present when the
        # caller requested cost sampling and the model has cost analysis.
        # May be a disk-backed ResultStream (Files & Reports opt-in): the
        # Raw view shows at most a bounded slice so a long simulation
        # can't blow up the text widget.
        self._cost_lines = cost_lines if cost_lines is not None else []
        # Censoring/sampling notes from the report builder (Raptor 7
       #shows these in its Final_Results dialog labels), plus any
        # GUI-appended run notes (safety-bound warning).
        self._notes = list(notes or [])
        # The model's own time unit (SYSTEM_DATA "Units" column) -- appended
        # to every TIME quantity shown; percentages and counts never get it.
        self._units = (units or "").strip()

        self._populate_overview()
        self._populate_details(final_results_lines)
        self._apply_level_filter()
        self._update_graphs()
        self._populate_raw(end_of_run_lines, final_results_lines)
        # Interim Cost tab only earns its place when there IS interim
        # cost text (models without cost analysis stay uncluttered).
        has_cost = bool(self._stream_lines(getattr(self, "_cost_lines", None)))
        idx = self._raw_tabs.indexOf(self._interim_cost_view)
        self._raw_tabs.setTabVisible(idx, has_cost)
        if has_cost:
            cost_all = self._stream_lines(self._cost_lines)
            shown = cost_all[:self._MAX_PARSE_ROWS]
            text = "\n".join(shown)
            if len(cost_all) > self._MAX_PARSE_ROWS:
                text += (f"\n\n… ({len(cost_all):,} rows total — use the "
                         f"engine-side rpt_interim_cost export for the "
                         f"full set)")
            self._interim_cost_view.setPlainText(text)

        self._run_selector.blockSignals(True)
        self._run_selector.clear()
        self._run_selector.addItems([f"Run {i + 1}"
                                     for i in range(len(event_logs))])
        self._run_selector.blockSignals(False)

        # Event Log tab: explain an empty tab when the run(s) carried no
        # event log (Files & Reports toggle off).
        has_logs = bool(event_logs) and any(
            len(self._stream_lines(logs)) > 0 for logs in event_logs)
        self._log_disabled_label.setVisible(not has_logs)
        self._log_tree.setVisible(has_logs)

        self._placeholder.hide()
        self._tabs.show()
        if event_logs:
            self._run_selector.setCurrentIndex(0)
            self._show_selected_run(0)
        self._update_log_empty_state()
        self._refresh_theme()
        self.contents_changed.emit()

    # ------------------------------------------------------------------
    # Overview
    # ------------------------------------------------------------------

    def refresh_overview_metric(self) -> None:
        """Re-run the Overview card computation against the already-held
        report data, for a live Analysis Type swap: `set_weak_settings`
        only stores the new `_weak_anal_type` and does not itself repaint
        anything, so this refreshes the Availability/Dependability card
        without waiting for the next simulation run. A no-op before any
        run (`_populate_overview` exits early with no `_report_data`)."""
        self._populate_overview()

    def _refresh_manifest_line(self) -> None:
        """One line saying what this result set IS, from its manifest.

        The design spec's review checklist asks "can you determine
        selection, phase, scenario and result set at a glance?"
        (the internal UI strategy notes) and its principle is that
        "simulation is an experiment, not an animation" -- a
        result that does not say which settings produced it is an
        animation with numbers attached. The model hash is shown because
        it is what lets a reader tell whether these numbers still
        describe the model in front of them."""
        m = getattr(self, "_manifest", None)
        lbl = getattr(self, "_manifest_label", None)
        if lbl is None:
            return
        if not m:
            lbl.setText("No run manifest — settings for this result "
                        "set were not recorded.")
            lbl.setToolTip("")
            lbl.setVisible(True)
            return
        bits = []
        if m.get("trials"):
            bits.append(f"{m['trials']:,} trial(s)")
        if m.get("termination_type"):
            cond = m.get("term_condition")
            bits.append(f"stop: {m['termination_type']}"
                        + (f" at {cond:g}" if isinstance(cond, (int, float))
                           else ""))
        if m.get("analyses"):
            bits.append("analyses: " + ", ".join(m["analyses"]))
        record = m.get("recording") or {}
        if record:
            on = [k for k in ("event_log", "ao_plot", "capacity_plot",
                              "cost_plot") if record.get(k)]
            if on:
                bits.append("recorded: " + ", ".join(on))
        line = "  ·  ".join(bits) if bits else "run settings recorded"
        if m.get("model_hash"):
            line += f"   (model {m['model_hash']}"
            if m.get("digest"):
                line += f", run {m['digest']}"
            line += ")"
        lbl.setText(line)
        lbl.setVisible(True)
        tip = [f"model: {m.get('model_name', '?')}"]
        if m.get("engine_version"):
            tip.append(f"engine: {m['engine_version']}")
        if m.get("seed_strategy"):
            tip.append(f"seeds: {m['seed_strategy']}")
        lbl.setToolTip("\n".join(tip))

    def _populate_overview(self):
        self._runs_table.clear()
        self._refresh_manifest_line()
        data = self._report_data
        if not data:
            self._summary_sentence.setText(
                "Run the simulation to see the summary.")
            return

        u = self._units
        time_u = f" {u}" if u else ""

        def stat(field_getter, fmt=_fmt) -> tuple[float | None, float | None, float | None]:
            """(mean, min, max) of a per-run quantity."""
            vals = [v for v in (field_getter(d) for d in data) if v is not None]
            if not vals:
                return None, None, None
            return sum(vals) / len(vals), min(vals), max(vals)

        def censored(field_getter, suspected_attr: str | None = None) -> bool:
            """Raptor 7's `>` censoring condition for a cross-run
            aggregate: either any run's value was the "suspected"
            raw-uptime fallback (gtNote1), or fewer runs produced a
            usable sample than Availability did (xRunsNote1 -> the
            `(n-runs)` label)."""
            avail_n = sum(1 for d in data if d.availability is not None)
            if suspected_attr and any(
                    getattr(d, suspected_attr, False) for d in data):
                return True
            usable = 0
            for d in data:
                v = field_getter(d)
                if v is None:
                    continue
                if suspected_attr and getattr(d, suspected_attr, False):
                    continue
                usable += 1
            return avail_n > 0 and usable < avail_n

        # The model's configured Raptor 7 metric (nodeAnalType, pushed via
        # `set_weak_settings` -> `self._weak_anal_type` before
        # `set_results` calls this) decides Availability vs
        # Dependability. The two are genuinely different numbers, so the
        # card must show the one the model is configured to report on.
        # Falls back to Availability if Dependability isn't computed for
        # this model (`d.dependability` is `None` -- e.g. no phases).
        # Reliability is mission-based and has its own card below, so for a
        # Reliability model this headline card keeps showing Availability.
        use_dependability = (
            weak_metric.canonical(self._weak_anal_type)
            == weak_metric.DEPENDABILITY
            and any(d.dependability is not None for d in data))
        if use_dependability:
            avail_m, avail_lo, avail_hi = stat(lambda d: d.dependability)
            self._cards["Availability"].set_title(
                "Dependability",
                "fraction of MISSION time working (full precision)")
        else:
            avail_m, avail_lo, avail_hi = stat(lambda d: d.availability)
            self._cards["Availability"].set_title(
                "Availability",
                "fraction of time working (full precision)")
        mtbde_m, mtbde_lo, mtbde_hi = stat(lambda d: d.mtbde)
        mdt_m, mdt_lo, mdt_hi = stat(lambda d: d.mdt)
        mtbm_m, _, _ = stat(lambda d: d.mtbm)
        mmt_m, _, _ = stat(lambda d: d.mmt)
        rel_m, rel_lo, rel_hi = stat(lambda d: d.reliability)
        fails_m, fails_lo, fails_hi = stat(lambda d: float(d.num_system_failures),
                                           fmt=lambda v: f"{v:,.0f}")

        # Availability/Dependability as a full-precision decimal (like
        # the report's own 9-decimal REALTOSTR values, e.g. 0.998884325);
        # rounding to a percent would hide the digits reliability work
        # turns on.
        self._cards["Availability"].set_value(
            f"{avail_m:.9f}".rstrip("0").rstrip(".") or "0",
            f"min {avail_lo:.9f} · max {avail_hi:.9f}")
        # Reliability card: same full-precision treatment as the
        # Availability card.
        if rel_m is not None:
            self._cards["Reliability"].set_value(
                f"{rel_m:.9f}".rstrip("0").rstrip(".") or "0",
                f"min {rel_lo:.9f} · max {rel_hi:.9f}")
        # SI/engineering notation for these headline time values:
        # MTBDE/MDT/MTBM/MMT can span anywhere from fractions of an hour
        # to hundreds of thousands, where "1.235M" reads more naturally
        # than a raw comma-grouped number. The detail tables, Runs table
        # and raw report stay on plain `_fmt` -- those are for precise
        # cross-checking, not a headline glance.
        self._cards["MTBDE"].set_value(
            (_fmt_si(mtbde_m) + time_u) if mtbde_m is not None else "N/A",
            f"min {_fmt_si(mtbde_lo)}{time_u} · "
            f"max {_fmt_si(mtbde_hi)}{time_u}"
            if mtbde_lo is not None else "N/A",
            censored=bool(mtbde_m is not None and censored(
                lambda d: d.mtbde, "mtbde_suspected")))
        self._cards["MDT"].set_value(
            (_fmt_si(mdt_m) + time_u) if mdt_m is not None else "N/A",
            f"min {_fmt_si(mdt_lo)}{time_u} · "
            f"max {_fmt_si(mdt_hi)}{time_u}"
            if mdt_lo is not None else "N/A",
            censored=bool(mdt_m is not None and censored(lambda d: d.mdt)))
        self._cards["MTBM"].set_value(
            (_fmt_si(mtbm_m) + time_u) if mtbm_m is not None else "N/A",
            censored=bool(mtbm_m is not None and censored(
                lambda d: d.mtbm, "mtbm_suspected")))
        self._cards["MMT"].set_value(
            (_fmt_si(mmt_m) + time_u) if mmt_m is not None else "N/A")
        self._cards["System_Failures"].set_value(
            _fmt_count(fails_m),
            f"min {fails_lo:,.0f} · max {fails_hi:,.0f}")

        g = _mean([d.green_percent for d in data]) or 0.0
        y = _mean([d.yellow_percent for d in data]) or 0.0
        r = _mean([d.red_percent for d in data]) or 0.0
        self._gyr_split = (g, y, r)
        self._style_gyr_bar()

        # Only shown for a phased model with mission
        # tracking. Two gates: `has_phases` rules out non-phased models
        # (the engine's `phase_1_mission` defaults to True for those, so
        # the mission percents would just duplicate "System health"); and
        # `mission_*_percent is not None` rules out phased models where
        # no phase is flagged Mission (mission total time stays 0, so the
        # engine returns None).
        has_mission = all(d.has_phases for d in data) and \
            any(d.mission_green_percent is not None for d in data)
        self._mission_gyr_container.setVisible(has_mission)
        if has_mission:
            mg = _mean([d.mission_green_percent for d in data]) or 0.0
            my = _mean([d.mission_yellow_percent for d in data]) or 0.0
            mr = _mean([d.mission_red_percent for d in data]) or 0.0
            self._mission_gyr_split = (mg, my, mr)
            self._style_mission_gyr_bar()

        _unit_note = f" ({u})" if u else ""
        sim_time = _mean([d.ending_sim_time for d in data])
        time_phrase = (f" Each run simulated {_fmt(sim_time)}{time_u}."
                       if sim_time else "")
        parts = [
            f"Across {len(data)} run{'s' if len(data) != 1 else ''}, the system was fully "
            f"working {_pct(g)} of the time, still partially working but "
            f"degraded {_pct(y)}, and completely down {_pct(r)}."
        ]
        if fails_m and fails_m > 0 and mtbde_m:
            # "about 0 times per run" for 1 failure in 50 runs read as
            # "never"; say how rare it is instead.
            how_often = (f"about once every {1 / fails_m:,.0f} runs"
                         if fails_m < 0.95 else
                         f"about {_fmt_count(fails_m)} times per run")
            parts.append(f"It went fully down {how_often} "
                         f"— roughly once every {_fmt(mtbde_m)}{time_u} of "
                         f"working time.")
        elif fails_m == 0:
            parts.append("It never went fully down in any run.")
        parts.append(time_phrase)
        rel = _mean([d.reliability for d in data])
        if rel is not None and rel > 0:
            parts.append(f"Missions completed without a failure (reliability): "
                         f"{rel * 100:.1f}% on average.")
        self._summary_sentence.setText(" ".join(parts))

        for d in data:
            mtbde = _fmt(d.mtbde) + (time_u if d.mtbde is not None else "")
            if d.num_system_failures == 0 and d.mtbde is not None:
                # No downing event: the run length is a lower bound, not
                # a measured MTBDE. Match the Summary table's > marker.
                mtbde = ">" + mtbde
            self._runs_table.addTopLevelItem(QTreeWidgetItem([
                str(d.run_number), f"{d.availability:.9f}",
                mtbde,
                _fmt(d.mdt) + (time_u if d.mdt is not None else ""),
                f"{d.num_system_failures:,}",
            ]))
        # Grow to fit every run (no cap): a QTreeWidget with a stretch
        # factor otherwise just fills the Overview scroll area's viewport
        # and shows its own small internal scrollbar for a large batch,
        # instead of the full-page scroll the surrounding QScrollArea
        # already provides for everything else on this tab.
        row_h = self._runs_table.sizeHintForRow(0) if data else 20
        self._runs_table.setMaximumHeight(
            self._runs_table.header().height() + row_h * len(data) + 4)

    def _style_gyr_bar(self):
        self._style_health_bar(
            "_gyr_split", self._gyr_segments, self._gyr_lay,
            self._gyr_legend_labels, bar_object="GYR", mission=False)

    def _style_mission_gyr_bar(self):
        """Same discrete-segment styling as `_style_gyr_bar`, applied to
        the MISSION-time-only bar."""
        self._style_health_bar(
            "_mission_gyr_split", self._mission_gyr_segments,
            self._mission_gyr_lay, self._mission_gyr_legend_labels,
            bar_object="MissionGYR", mission=True)

    def _style_health_bar(self, split_attr: str, segments, layout,
                          labels, *,
                          bar_object: str, mission: bool) -> None:
        """Paint one green/yellow/red bar from its simulated split.

        The two bars differ only in their object names and in the
        tooltip's reminder that the mission bar covers mission time only;
        the proportions, colours, legend markup and visibility rule are
        shared.
        """
        g, y, r = getattr(self, split_attr, (0.0, 0.0, 0.0))
        total = max(g + y + r, 1e-9)
        c = active_colour()
        # Deliberate traffic-light exception (see design_tokens): the
        # health bar stays red/orange/green rather than the Appendix D
        # block-state colours used elsewhere.
        colours = {"operating": c["result_green"],
                   "degraded": c["result_yellow"],
                   "failed": c["result_red"]}
        widths = {"operating": g / total, "degraded": y / total,
                  "failed": r / total}
        meanings = {"operating": "Fully working", "degraded": "Degraded",
                    "failed": "Fully down"}
        suffix = " (mission time)" if mission else ""
        for key, frac in widths.items():
            seg = segments[key]
            seg.setStyleSheet(
                f"QFrame#{bar_object}_{key} {{ background: {colours[key]};"
                f" border: none; border-radius: 3px; }}")
            seg.setToolTip(
                f"{meanings[key]}{suffix}: {frac * 100:.1f}% of the time")
            layout.setStretchFactor(seg, max(1, round(frac * 200)))
            seg.setVisible(frac > 0.0005)
        legend_texts = {
            "green": f"■ Fully working — {g:.1f}%",
            "yellow": f"■ Degraded — {y:.1f}%",
            "red": f"■ Fully down — {r:.1f}%",
        }
        legend_colours = {"green": colours["operating"],
                          "yellow": colours["degraded"],
                          "red": colours["failed"]}
        text_colour = c["text"]
        for key, text in legend_texts.items():
            lbl = labels[key]
            # Swatch keeps the state colour; the words stay theme-text,
            # since yellow-on-white in the light theme has too little
            # contrast for readable text.
            lbl.setText(
                f"<span style='color:{legend_colours[key]}'>&#9632;</span>"
                f" {text.split(chr(32), 1)[1]}")
            # No `background: transparent` here (see _update_status_
            # summary in main_window.py: an unscoped background
            # declaration on a widget also paints the tooltip window
            # shown for it -- black tip box in light theme).
            lbl.setStyleSheet(f"color: {text_colour}; font-size: 11px;")

    def _parse_final_sections(self, lines: list[str]):
        """-> [(section_title, [column_names], [(label, [values])])] for the
        `***SECTION***`-delimited tables in the Final_Results text.

        A one-line adapter over `raptor.api.tables.parse_final_sections`,
        which owns the parse: the section regex, header detection, the
        first-token-is-the-label row split, and the value grammar
        (`>`-censored, `N/A`, thousands separators, scientific notation).
        See that function's docstring for the documented row semantics --
        it is the single version of them.

        This method used to be a second copy of that loop, and its
        docstring described a different row split from the one it
        actually implemented -- so the rule is now stated in exactly one
        place, above. It stays as an adapter because `_populate_details`
        calls it and then REPLACES an entry in the returned list, so the
        contract that matters is: a mutable list of
        (title, columns, rows) tuples.
        """
        return parse_final_sections(lines)

    # ------------------------------------------------------------------
    # Event Log
    # ------------------------------------------------------------------

    def show_live_logs(self, logs_so_far: list[list[str]], total_runs: int):
        """Live view during a batch: show the event logs of the runs that
        have COMPLETED so far (the engine hands them over run-by-run via
        its progress hook). The final `set_results` call replaces this
        with the full result set."""
        self._placeholder.hide()
        self._tabs.show()
        # Capacity's live update (update_live_capacity) owns the tab
        # switch once it has started for this run batch, so we don't
        # yank the view back to the Event Log on every progress tick.
        if not getattr(self, "_live_cap_autoswitch", False):
            self._tabs.setCurrentWidget(self._event_page)
        self._event_logs = logs_so_far
        count = len(logs_so_far)
        self._run_selector.blockSignals(True)
        self._run_selector.clear()
        self._run_selector.addItems(
            [f"Run {i + 1}" for i in range(count)] +
            ([f"…{total_runs - count} more"] if count < total_runs else []))
        self._run_selector.blockSignals(False)
        self._run_selector.setCurrentIndex(max(0, count - 1))
        self._statusbar_hint = (
            f"Run {count} of {total_runs} complete" if count < total_runs
            else "Complete")
        # Only the newest completed run's log is shown; the selector keeps
        # the earlier ones reachable.
        self._log_model.set_lines(logs_so_far[-1] if logs_so_far else [])

    def _show_selected_run(self, index: int):
        if not (0 <= index < len(self._event_logs)):
            # The trailing "…N more" entry is a placeholder, not a run.
            # Selecting it used to blank the whole log; keep the newest
            # completed run instead.
            if self._event_logs:
                self._run_selector.blockSignals(True)
                self._run_selector.setCurrentIndex(len(self._event_logs) - 1)
                self._run_selector.blockSignals(False)
                self._log_model.set_lines(self._event_logs[-1])
            self._update_log_empty_state()
            return
        self._log_model.set_lines(self._event_logs[index])
        self._update_log_empty_state()

    @staticmethod
    def _state_colour(state: str, c: dict):
        s = state.lower()
        if any(k in s for k in ("good", "repaired", "green")):
            return QColor(c["operating"])
        if any(k in s for k in ("failed", "repairing", "red", "bad")):
            return QColor(c["failed"])
        if any(k in s for k in ("yellow", "degraded", "standby", "idle", "pm")):
            return QColor(c["degraded"])
        return None

    #: Below this many rows a filter pass is instant, so it runs on every
    #: keystroke; above it, it waits for a 150 ms pause in typing.
    _LOG_FILTER_DEBOUNCE_ROWS = 5000

    def _apply_log_filter(self):
        if self._log_model.total > self._LOG_FILTER_DEBOUNCE_ROWS:
            self._log_filter_timer.start()
        else:
            self._run_log_filter()

    def _run_log_filter(self):
        self._log_model.set_filter(self._filter_edit.text())
        self._update_log_empty_state()

    def _update_log_empty_state(self) -> None:
        """Say why the log is blank when a filter matched nothing."""
        lbl = getattr(self, "_log_empty_label", None)
        if lbl is None:
            return
        needle = self._filter_edit.text().strip()
        blank = (self._log_model.total > 0
                 and self._log_model.rowCount() == 0)
        if blank and needle:
            lbl.setText(f"No events match “{needle}”. "
                        "Clear the filter to see the whole log.")
        elif blank:
            lbl.setText("No events to show for this run.")
        # `isVisible()` is False while another Results tab is current, so
        # the label would never appear when the user switches to Event
        # Log. Ask whether the tree is hidden (collection off), not
        # whether its tab happens to be on screen now.
        lbl.setVisible(bool(blank)
                       and not self._log_tree.isHidden())

    def event_log_table(self, visible_only: bool = True
                        ) -> tuple[list[str], list[list[str]]]:
        """(headers, rows) of the Event Log tab as shown -- the filter
        applied when `visible_only`. What Print / Export PDF use, so the
        exporters never reach into the view."""
        if self._log_filter_timer.isActive():
            # A debounced filter not yet applied would export stale rows.
            self._log_filter_timer.stop()
            self._run_log_filter()
        rows = (self._log_model.visible_rows() if visible_only
                else self._log_model.all_rows())
        return list(HEADERS), [list(r) for r in rows]

    def _on_log_row_clicked(self, index):
        kind, name = self._log_model.data(
            index.siblingAtColumn(0), Qt.ItemDataRole.UserRole) or (None, None)
        if kind not in ("block", "node") or not name:
            return
        self.element_clicked.emit(kind, name)

    def update_live_capacity(self, cap_lines) -> None:
        """Capacity snapshots received so far during a batch run. The
        engine appends each completed run's capacity snapshot rows to its
        capacity sink; MainWindow forwards the sink (plain list or
        disk-backed ResultStream) here, so the Graphs tab's Capacity
        (Flow) plot fills in run-by-run rather than only after the whole
        batch. The first update for a batch switches the view to the
        Capacity plot (Raptor 7 shows its capacity display live too);
        later updates only refresh the data. Live refresh pauses past
        `_LIVE_CAP_REFRESH_ROWS` rows, since re-parsing a huge file on
        every progress tick is the memory/CPU blow-up this opt-in design
        avoids; the final `set_results` parse still covers everything."""
        if not cap_lines or len(cap_lines) == 0:
            return
        self._cap_lines = cap_lines
        row_count = getattr(cap_lines, "count", len(cap_lines))
        if row_count > self._LIVE_CAP_REFRESH_ROWS:
            if not self._graph_note.isVisible():
                self._graph_note.setText(
                    "Live plot paused — data large; the full set "
                    "appears when the batch completes")
                self._graph_note.show()
            return
        self._update_graphs()
        self._placeholder.hide()
        self._tabs.show()
        if not getattr(self, "_live_cap_autoswitch", False):
            self._live_cap_autoswitch = True
            item = cast(QStandardItemModel,
                        self._graph_mode.model()).item(3)
            if item is not None and item.isEnabled():
                self._graph_mode.setCurrentIndex(3)  # Capacity (Flow)
                self._tabs.setCurrentWidget(self._graphs_page)

    def update_live_ao(self, ao_lines) -> None:
        """Availability-plot rows received so far during a batch run.
        Mirrors `update_live_capacity`: the engine appends each completed
        run's Ao rows to its sink, and MainWindow forwards them here so
        the Graphs tab's Ao plot fills in run-by-run rather than only at
        batch end."""
        if not ao_lines or len(ao_lines) == 0:
            return
        self._ao_lines = ao_lines
        row_count = getattr(ao_lines, "count", len(ao_lines))
        if row_count > self._LIVE_CAP_REFRESH_ROWS:
            if not self._graph_note.isVisible():
                self._graph_note.setText(
                    "Live plot paused — data large; the full set "
                    "appears when the batch completes")
                self._graph_note.show()
            return
        self._update_graphs()
        self._placeholder.hide()
        self._tabs.show()
        # Capacity's own live update claims the view once it starts; only
        # autoswitch to Ao if neither has claimed it this batch.
        if not getattr(self, "_live_ao_autoswitch", False) and \
                not getattr(self, "_live_cap_autoswitch", False):
            self._live_ao_autoswitch = True
            self._graph_mode.setCurrentIndex(0)   # "Per Trial Ao"
            self._tabs.setCurrentWidget(self._graphs_page)

    # ------------------------------------------------------------------
    # Raw
    # ------------------------------------------------------------------

    def _populate_raw(self, end_of_run_lines, final_results_lines):
        self._raw_views["End of Run"].setPlainText("\n".join(end_of_run_lines))
        self._raw_views["Final Results"].setPlainText("\n".join(final_results_lines))

    # ------------------------------------------------------------------
    # Theming
    # ------------------------------------------------------------------

    def _refresh_theme(self, _dark: bool | None = None):
        c = active_colour()
        self.setStyleSheet(f"""
            QWidget {{ background: {c['surface']}; }}
            QLabel#ResultsPlaceholder {{
                color: {c['muted']}; font-size: {TYPE['body']}px; background: transparent;
            }}
            QLabel#SummarySentence {{
                color: {c['text']}; font-size: {TYPE['body']}px; background: transparent;
            }}
            QLabel#GYRLabel {{
                color: {c['muted']}; font-size: {TYPE['caption']}px; background: transparent;
            }}
            QFrame#MetricCard {{
                background: {c['canvas']};
                border: 1px solid {c['border']};
                border-radius: 8px;
            }}
            QLabel#MetricTitle {{
                color: {c['muted']}; font-size: {TYPE['caption']}px;
                font-weight: 600; background: transparent;
            }}
            QLabel#MetricValue {{
                color: {c['text']}; font-size: 22px; font-weight: 700; background: transparent;
            }}
            QLabel#MetricRange {{
                color: {c['muted']}; font-size: 10px; background: transparent;
            }}
            QLabel#MetricCaption {{
                color: {c['muted']}; font-size: 11px; background: transparent;
            }}
            QTreeWidget {{
                background: {c['surface']}; color: {c['text']};
                border: 1px solid {c['border']}; alternate-background-color: {c['canvas']};
            }}
            QFrame#SectionCard {{
                background: {c['canvas']};
                border: 1px solid {c['border']};
                border-radius: 8px;
            }}
            QLabel#SectionTitle {{
                color: {c['text']}; font-size: {TYPE['label']}px;
                font-weight: 700; background: transparent;
            }}
            QLabel#TableTitle {{
                color: {c['text']}; font-size: {TYPE['body'] + 2}px;
                font-weight: 600; background: transparent;
                padding-top: 6px;
            }}
            QLabel#SectionNote {{
                color: {c['muted']}; font-size: 11px; background: transparent;
            }}
            QTableWidget {{
                background: {c['surface']}; color: {c['text']};
                border: 1px solid {c['border']};
                gridline-color: {c['border']};
                alternate-background-color: {c['canvas']};
            }}
            QLineEdit {{
                background: {c['input_bg']}; color: {c['text']};
                border: 1px solid {c['border']}; border-radius: 4px; padding: 2px 6px;
            }}
            QPushButton {{
                background: {c['canvas']}; color: {c['text']};
                border: 1px solid {c['border']}; border-radius: 4px;
                padding: 3px 10px; font-size: 11px;
            }}
            QPushButton:hover {{
                border-color: {c['accent']}; color: {c['accent']};
            }}
        """)
        self._style_gyr_bar()
        if getattr(self, "_mission_gyr_split", None) is not None:
            self._style_mission_gyr_bar()
