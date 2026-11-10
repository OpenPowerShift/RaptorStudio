"""Module-level helpers, cell roles and table widgets shared by the Results dock and its mixins."""
from __future__ import annotations

import weakref


from PySide6.QtWidgets import QAbstractItemView


from typing import Protocol, cast

from PySide6.QtCore import QEvent, QObject, QPoint, Qt, QTimer
from PySide6.QtGui import QFont
from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QLabel,
    QFrame, QSizePolicy, QScrollArea, QTableWidget, QTableWidgetItem,
)

from raptor.gui.resources.design_tokens import SPACING, active_colour


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
                          "as the Final Results report aggregates it.",
    "Max Cap (avg runs)": "Average of each run's own maximum capacity — "
                          "as the Final Results report aggregates it.",
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


class _ReadOnlyTable(QTableWidget):
    """Base class for every results grid: selectable, sortable, not editable.

    `NoEditTriggers` stops the user opening an editor, and overriding
    `editItem` stops a programmatic call from doing the same. One base
    class means a new results table cannot forget the rule (#218).
    """

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)

    def editItem(self, item: QTableWidgetItem) -> None:  # noqa: N802
        """Never open an editor, even when called directly."""
        return


class _StatefulTable(Protocol):
    """Type-only view of the per-instance state `ResultsDock` keeps on a
    table it owns.

    Qt tables accept extra Python attributes, but `QTableWidget` does not
    declare them; this protocol gives those assignments a checked type
    without changing the widget or the runtime value.
    """

    _hierarchy_sort_hooked: bool
    _sort_state_key: str
    _goto_menu_filter: QObject | None


class _ViewportContextMenuFilter(QObject):
    """Deliver a viewport's ContextMenu event to a callback.

    A QAbstractScrollArea sends the real mouse context event to its
    viewport, not the table widget. Relying only on
    `customContextMenuRequested` made the Weak Link right-click menu
    silent in real use (#211).
    """

    def __init__(self, viewport: QWidget, callback) -> None:
        super().__init__(viewport)
        self._callback = callback
        viewport.installEventFilter(self)

    def eventFilter(self, obj, event) -> bool:  # noqa: N802
        if event.type() == QEvent.Type.ContextMenu:
            self._callback(event.pos())
            return True
        return False


class _TableActionStrip(QWidget):
    """A table's Copy/CSV strip, constrained to that table's right edge.

    `_FitTable` hugs its columns while the page is much wider; a plain
    row with a stretch parked the icons at the page edge, far from a
    narrow table. This strip tracks the table width so its actions stay
    directly above the table's top-right corner (#217).
    """

    def __init__(self, table: QTableWidget, parent=None) -> None:
        super().__init__(parent)
        self.setObjectName("TableActionStrip")
        self.setSizePolicy(QSizePolicy.Policy.Fixed,
                           QSizePolicy.Policy.Fixed)
        self._table_ref = weakref.ref(table)
        self._viewport_ref: weakref.ReferenceType[QWidget] | None = None
        table.installEventFilter(self)
        # Do not size from `sizeHint()` before layout: for a table wider
        # than its scroll viewport that is the page's minimum width and
        # pushes the actions off-screen. Wait for the first show/resize.
        QTimer.singleShot(0, self._sync_width)

    @staticmethod
    def _scroll_viewport(table: QTableWidget) -> QWidget | None:
        parent = table.parentWidget()
        while parent is not None:
            if isinstance(parent, QScrollArea):
                return parent.viewport()
            parent = parent.parentWidget()
        return None

    def _sync_width(self) -> None:
        from shiboken6 import isValid
        if not isValid(self):
            return
        table = self._table_ref()
        if table is None or not isValid(table) or not table.isVisible() \
                or table.width() <= 1:
            return
        width = table.width()
        viewport = self._scroll_viewport(table)
        if viewport is not None and isValid(viewport):
            current = self._viewport_ref() if self._viewport_ref else None
            if current is not viewport:
                self._viewport_ref = weakref.ref(viewport)
                viewport.installEventFilter(self)
        if viewport is not None and isValid(viewport) \
                and viewport.width() > 0:
            # The strip sits inside the page's layout margin; clamp to
            # what is actually visible, not the table's full width.
            left = max(0, self.mapTo(viewport, QPoint(0, 0)).x())
            width = min(width, max(1, viewport.width() - left))
        # Never go below the title + action-button minimum: hiding the
        # heading under the icons is worse than a horizontal scrollbar in
        # an unusually narrow dock (#225).
        width = max(width, self.minimumWidth())
        if width > 0 and width != self.width():
            self.setFixedWidth(width)

    def eventFilter(self, obj, event) -> bool:  # noqa: N802
        if event.type() not in (QEvent.Type.Resize, QEvent.Type.Show):
            return False
        from shiboken6 import isValid
        if not isValid(self):
            return False
        table = self._table_ref()
        viewport = self._viewport_ref() if self._viewport_ref else None
        if obj is table and table is not None and isValid(table):
            self._sync_width()
            # The first pass can run before the page's margins settle;
            # one deferred pass measures the visible left edge too.
            QTimer.singleShot(0, self._sync_width)
        elif viewport is not None and obj is viewport and isValid(viewport):
            self._sync_width()
        return False


class _FitTable(_ReadOnlyTable):
    """A results table as wide as its columns (plus its scrollbar when it
    has one), so it hugs its content and never clips the last column."""

    def sortItems(self, column: int,
                  order: Qt.SortOrder = Qt.SortOrder.AscendingOrder) -> None:  # noqa: N802
        """Keep the header arrow in step with a programmatic sort.

        `_SortItem` reads the header order to keep N/A/— last. Qt's own
        click path updates the indicator before sorting, but a direct
        `sortItems` call would otherwise sort using the previous
        direction; setting it here keeps both paths identical.
        """
        header = self.horizontalHeader()
        if (header.sortIndicatorSection() != column
                or header.sortIndicatorOrder() != order):
            header.setSortIndicator(column, order)
        super().sortItems(column, order)

    def sizeHint(self):  # noqa: N802 (Qt override)
        hint = super().sizeHint()
        width = self.horizontalHeader().length() + 2 * self.frameWidth()
        if self.verticalScrollBarPolicy() != \
                Qt.ScrollBarPolicy.ScrollBarAlwaysOff:
            width += self.verticalScrollBar().sizeHint().width()
        hint.setWidth(width + 2)
        return hint


def _sort_key(item: QTableWidgetItem) -> tuple[str, object]:
    """The value a results cell sorts by: its raw number, not its text.

    Numeric cells keep full precision in `RAW_VALUE_ROLE` while the grid
    shows a rounded string, so text sorting put 9.5 after 10. Column 0
    keeps the un-elided element name in `Qt.UserRole`; two long names can
    share the same visible prefix. N/A and — are marked "bad" so they can
    be kept last in both sort directions.
    """
    raw = item.data(RAW_VALUE_ROLE)
    if isinstance(raw, (int, float)) and not isinstance(raw, bool):
        return "num", float(raw)
    text = item.text()
    if item.column() == 0:
        full = item.data(Qt.ItemDataRole.UserRole)
        return "str", str(full or text).casefold()
    stripped = text.strip()
    if stripped.casefold() in {"", "n/a", "na", "—", "–", "-"}:
        return "bad", ""
    numeric = stripped.replace(",", "")
    if numeric.startswith(">"):
        numeric = numeric[1:].strip()
    if numeric.endswith("%"):
        numeric = numeric[:-1].strip()
    try:
        return "num", float(numeric)
    except ValueError:
        return "str", text.casefold()


class _SortItem(QTableWidgetItem):
    """A results cell whose ordering follows the value behind the display.

    Qt sorts through `operator<`; `N/A`/`—` must stay last whichever
    direction the header arrow points, so the comparison consults the
    header's current order for those two sentinels only.
    """

    def __lt__(self, other):  # noqa: D105 (Qt protocol method)
        if not isinstance(other, QTableWidgetItem):
            return super().__lt__(other)
        a_kind, a_val = _sort_key(self)
        b_kind, b_val = _sort_key(other)
        a_bad, b_bad = a_kind == "bad", b_kind == "bad"
        if a_bad or b_bad:
            if a_bad and b_bad:
                return False
            table = self.tableWidget()
            desc = (table is not None and
                    table.horizontalHeader().sortIndicatorOrder()
                    == Qt.SortOrder.DescendingOrder)
            # Empirically Qt reverses the ascending comparator for
            # descending order (and calls it with the operands swapped),
            # so the sentinel must be "less" only for a descending sort
            # to land at the bottom in both directions.
            return desc if a_bad else not desc
        if a_kind != b_kind:
            return a_kind == "num"
        if a_kind == "num":
            return cast(float, a_val) < cast(float, b_val)
        return cast(str, a_val) < cast(str, b_val)


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
