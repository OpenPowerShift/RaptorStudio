"""Model ▸ Analytical: exact static reliability/availability.

A thin front end over `raptor.api.analytical.analyse_rbd`, which also backs
the CLI and the MCP tool. It does not sample and it never changes the model.

The solve runs on a worker thread (a large diagram or a heavy-tailed
distribution can take seconds), so the window stays responsive. Results are
shown as headline figures, a sortable per-element table, a reliability curve,
the path-product equation and the assumptions the answer rests on.
"""
from __future__ import annotations

import copy
import json
import math
import os
from collections import OrderedDict
import queue
import re
import threading
import time
import weakref
from collections.abc import Sequence
from typing import Any, cast

from PySide6.QtCore import QEvent, QPointF, QRectF, Qt, QTimer
from PySide6.QtGui import QColor, QFontDatabase, QPainter, QPen, QPolygonF
from PySide6.QtWidgets import (
    QAbstractItemView, QApplication, QCheckBox, QComboBox, QDialog,
    QDoubleSpinBox, QFormLayout, QFrame, QGridLayout, QGroupBox, QHBoxLayout,
    QHeaderView, QLabel, QLineEdit, QMenu, QPlainTextEdit, QProgressBar,
    QPushButton, QScrollArea, QSizePolicy, QSpinBox, QStackedWidget,
    QTableWidget,
    QTableWidgetItem, QTabWidget, QVBoxLayout, QWidget)

from raptor.api.presentation import probability_text
from raptor.api.analytical import analyse_rbd_set, scope_options
from raptor.api.analytical_text import fmt as _fmt_full
from raptor.api.analytical_text import scope_sentence, summary_text
from raptor.core.analytical.bdd import MAX_BDD_NODES
from raptor.core.analytical.errors import BddTooLarge
from raptor.core.analytical.limits import max_bdd_nodes_allowed, memory_text
from raptor.core.analytical.errors import AnalyticalError
from raptor.core.analytical.network import MAX_VARIABLES
from raptor.gui.ui.table_filter_bar import FilterBar
from raptor.gui.ui import msgbox
from raptor.gui.resources.design_tokens import (
    RADIUS, SPACING, TYPE, ThemeManager, active_colour)

#: Curve points drawn when the user leaves "Curve at times" blank.
_AUTO_CURVE_POINTS = 41
#: Solved results kept per dialog, so going back to earlier settings shows
#: their result at once. Each is a few kB to a few hundred kB.
_CACHE_SIZE = 16
_READY_TITLE = "Ready to compute"
_READY_BODY = ("Check the settings above, then press Compute. A small model "
               "solves in a moment; a large one can take minutes, with "
               "progress and the time left shown beside the button.")

_MEASURE_HELP = {
    "Birnbaum": "How far the system moves if this element's probability "
                "moves (the element's structural importance).",
    "RAW": "Risk achievement worth: how much worse the system is with this "
           "element failed for certain.",
    "RRW": "Risk reduction worth: how much better the system could be if "
           "this element never failed.",
    "Fussell–Vesely": "The share of the system's unavailability that "
                      "disappears if this element is made perfect.",
    "Criticality": "The probability that this element is failed and "
                   "critical, given that the system has failed.",
}


def _px(key: str) -> int:
    """A type-scale size in pixels (the token table also holds the font family)."""
    return cast(int, TYPE[key])


def _run_length(rbd: Any) -> float | None:
    """The model's run length, or None when it is not time-terminated."""
    try:
        row = rbd.sections["SYSTEM_DATA"].rows[0]
        if str(row.get("TerminationType", "TimeSim")) != "TimeSim":
            return None
        value = float(row.get("TermCondition", 0.0))
    except (KeyError, IndexError, AttributeError, TypeError, ValueError):
        return None
    return value if value > 0.0 else None


def _units(rbd: Any) -> str:
    try:
        return str(rbd.sections["SYSTEM_DATA"].rows[0].get("units", "")).strip()
    except (KeyError, IndexError, AttributeError):
        return ""


def _floats(text: str) -> list[float]:
    return [float(part) for part in text.split(",") if part.strip()]


def _short(value: float | None, digits: int = 6) -> str:
    if value is None:
        return "N/A"
    if value == 0.0:
        return "0"
    return f"{value:.{digits}g}"


def _readable(value: float | None) -> str:
    """A table number at a glance; the full value is in its tooltip.

    Four significant figures, except near 1 (reliabilities, availabilities,
    RAW, RRW), where the digits that differ from 1 are what matters:
    0.9999873 stays 0.9999873, not "1". Large values are whole numbers
    with thousands separators rather than 2.395e+04.
    """
    if value is None:
        return "N/A"
    if value == 0.0 or value == 1.0 or not math.isfinite(value):
        return f"{value:g}"
    gap = abs(value - 1.0)
    if gap < 0.01:
        decimals = min(12, max(4, 2 - math.floor(math.log10(gap))))
        return f"{value:.{decimals}f}"
    if 1_000.0 <= abs(value) < 1e9:
        return f"{value:,.0f}"
    return f"{value:.4g}"


def _percent(value: float | None) -> str:
    return "" if value is None else f"{100.0 * value:.4g} %"


class _TimeSpin(QDoubleSpinBox):
    """A time field that shows ``48`` rather than ``48.000000``."""

    def textFromValue(self, value: float) -> str:  # noqa: N802 (Qt API)
        return f"{value:.10g}"


class _NumItem(QTableWidgetItem):
    """A table cell that sorts by its number, with N/A always last."""

    def __init__(self, value: float | None, digits: int = 6, *,
                 readable: bool = False) -> None:
        super().__init__(_readable(value) if readable
                         else _short(value, digits))
        self._value = value
        self.setTextAlignment(
            Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        if value is not None:
            self.setToolTip(_fmt_full(value))
            # The exact number, for the table's filter row (>0.99 compares
            # this, not the rounded text).
            self.setData(Qt.ItemDataRole.UserRole + 1, float(value))

    def __lt__(self, other: QTableWidgetItem) -> bool:
        mine = self._value
        theirs = getattr(other, "_value", None)
        if mine is None or theirs is None:
            return mine is None and theirs is not None
        return mine < theirs


def _solve(token: int, rbd: Any, name: str, options: dict[str, Any],
           inbox: queue.Queue, progress_inbox: queue.Queue,
           cancel_ev: threading.Event) -> None:
    """Worker-thread body: solve and post ``(token, result, error)``.

    The result goes through a plain thread-safe queue that the dialog polls,
    so no Qt object is ever touched (or destroyed) off the GUI thread, even
    if the dialog is closed while this is still running. Progress uses a
    second queue so a result and a progress update can never be confused.
    """
    def progress(stage: str, done: int, total: int) -> None:
        if cancel_ev.is_set():
            raise _AnalyticCancelled()
        progress_inbox.put((token, stage, done, total))

    try:
        chosen = dict(options)
        results = analyse_rbd_set(rbd, name, [chosen.pop("mode", "reliability")],
                                  progress=progress, **chosen)
        # One result, or {"phases": [...]} for "All phases".
        inbox.put((token, results[0] if len(results) == 1
                   else {"phases": results}, None))
    except _AnalyticCancelled:
        inbox.put((token, None, ("cancelled", "Cancelled")))
    except BddTooLarge as exc:
        inbox.put((token, None, ("refused", str(exc), exc.limit, exc.allowed)))
    except AnalyticalError as exc:
        inbox.put((token, None, ("refused", str(exc))))
    except ValueError as exc:
        inbox.put((token, None, ("invalid", str(exc))))
    except Exception as exc:  # noqa: BLE001 - worker boundary
        inbox.put((token, None, ("failed", f"{type(exc).__name__}: {exc}")))


def _without_pass_count(stage: str) -> str:
    """The solver's line without its pass count, which the percentage
    replaces: "Computing importance — pass 988 of about 3,082, about 15 s
    left" becomes "Computing importance — about 15 s left"."""
    return re.sub(r" — pass [\d,]+ of about [\d,]+(?:, (.*))?$",
                  lambda m: f" — {m.group(1)}" if m.group(1) else "", stage)


class _AnalyticCancelled(Exception):
    """Raised from the progress callback when the user presses Cancel."""


class _CurvePlot(QWidget):
    """R(t) against time: a plain line plot in the active theme."""

    def __init__(self) -> None:
        super().__init__()
        self._points: list[tuple[float, float]] = []
        self._unit = ""
        self.setMinimumHeight(190)
        self.setSizePolicy(QSizePolicy.Policy.Expanding,
                           QSizePolicy.Policy.Expanding)
        self.setAccessibleName("Reliability curve")

    def set_points(self, points: list[tuple[float, float]], unit: str) -> None:
        self._points = sorted(points)
        self._unit = unit
        if self._points:
            low, high = self._points[0], self._points[-1]
            self.setAccessibleDescription(
                f"Reliability falls from {low[1]:.4g} at {low[0]:g} to "
                f"{high[1]:.4g} at {high[0]:g} {unit}".strip())
        self.update()

    def paintEvent(self, _event) -> None:  # noqa: N802 (Qt API)
        c = active_colour()
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.fillRect(self.rect(), QColor(c["surface"]))
        left, right, top, bottom = 46.0, 18.0, 12.0, 42.0
        area = QRectF(left, top, self.width() - left - right,
                      self.height() - top - bottom)
        if area.width() < 20 or area.height() < 20 or len(self._points) < 2:
            return
        text_pen = QPen(QColor(c["muted"]))
        grid_pen = QPen(QColor(c["border"]))
        t0, t1 = self._points[0][0], self._points[-1][0]
        span = (t1 - t0) or 1.0

        def to_x(t: float) -> float:
            return area.left() + (t - t0) / span * area.width()

        def to_y(v: float) -> float:
            return area.bottom() - max(0.0, min(1.0, v)) * area.height()

        small = painter.font()
        small.setPixelSize(_px("caption"))
        painter.setFont(small)
        for tick in (0.0, 0.25, 0.5, 0.75, 1.0):
            y = to_y(tick)
            painter.setPen(grid_pen)
            painter.drawLine(QPointF(area.left(), y), QPointF(area.right(), y))
            painter.setPen(text_pen)
            painter.drawText(QRectF(0, y - 8, left - 6, 16),
                             int(Qt.AlignmentFlag.AlignRight
                                 | Qt.AlignmentFlag.AlignVCenter),
                             f"{tick:g}")
        for index in range(5):
            t = t0 + span * index / 4.0
            painter.setPen(text_pen)
            painter.drawText(
                QRectF(to_x(t) - 40, area.bottom() + 4, 80, 16),
                int(Qt.AlignmentFlag.AlignHCenter), f"{t:.4g}")
        if self._unit:
            painter.drawText(
                QRectF(area.left(), area.bottom() + 22, area.width(), 16),
                int(Qt.AlignmentFlag.AlignHCenter), f"Time ({self._unit})")
        painter.setPen(QPen(QColor(c["accent"]), 2.0))
        painter.drawPolyline(QPolygonF(
            [QPointF(to_x(t), to_y(v)) for t, v in self._points]))
        if len(self._points) <= 24:
            painter.setBrush(QColor(c["accent"]))
            for t, v in self._points:
                painter.drawEllipse(QPointF(to_x(t), to_y(v)), 3.0, 3.0)


class _CardGrid(QWidget):
    """The Summary cards: as many per row (up to 3) as fit at their natural
    width, each at its natural height."""

    MAX_COLUMNS = 3

    def __init__(self) -> None:
        super().__init__()
        self.grid = QGridLayout(self)
        self.grid.setSpacing(SPACING["md"])
        self.grid.setContentsMargins(SPACING["md"], SPACING["md"],
                                     SPACING["md"], SPACING["md"])
        self._cards: list[QWidget] = []
        self._columns = 0

    def set_cards(self, cards: Sequence[QWidget]) -> None:
        self._cards = list(cards)
        self._columns = 0
        self._reflow()

    def _fitting_columns(self) -> int:
        if not self._cards:
            return 1
        widest = max(card.sizeHint().width() for card in self._cards)
        spacing = self.grid.spacing()
        margins = self.grid.contentsMargins()
        room = self.width() - margins.left() - margins.right()
        fit = (room + spacing) // max(1, widest + spacing)
        return max(1, min(self.MAX_COLUMNS, fit, len(self._cards)))

    def _reflow(self) -> None:
        columns = self._fitting_columns()
        if columns == self._columns:
            return
        self._columns = columns
        for card in self._cards:
            self.grid.removeWidget(card)
        for column in range(self.MAX_COLUMNS):
            self.grid.setColumnStretch(column, 1 if column < columns else 0)
        for index, card in enumerate(self._cards):
            self.grid.addWidget(card, index // columns, index % columns)
        rows = (len(self._cards) + columns - 1) // columns
        for row in range(rows + 4):
            self.grid.setRowStretch(row, 1 if row == rows else 0)

    def resizeEvent(self, event) -> None:  # noqa: N802 (Qt API)
        super().resizeEvent(event)
        self._reflow()


class AnalyticDialog(QDialog):
    """Modeless; computes on demand and leaves the model untouched."""

    def __init__(self, win) -> None:
        super().__init__(win)
        self._win = win
        self._token = 0
        self._busy = False
        self._rerun = False     # settings changed while a solve was running
        self._cancel_ev = threading.Event()
        self._worker: threading.Thread | None = None
        self._has_standby = False   # the open model has cold-standby nodes
        self._has_dependencies = False
        #: The model the page shows: a weak reference (an id() can be
        #: reused by the next model opened, which then showed the old
        #: model's results) and its file path, plus a serial for the cache.
        self._shown_rbd: weakref.ref | None = None
        self._shown_path: Any = None
        self._model_serial = 0
        self._result: dict[str, Any] | None = None
        self._inbox: queue.Queue = queue.Queue()
        self._progress_inbox: queue.Queue = queue.Queue()
        self._poll = QTimer(self)
        self._poll.setInterval(30)
        self._poll.timeout.connect(self._drain)
        #: The window's model revision the shown result was solved from,
        #: so an edit made while the dialog stays open is noticed.
        self._solved_revision: int | None = None
        self._stale_shown = False
        self._changed_while_busy = False
        self._solve_on_pick: Any = None
        #: Results by `_cache_key`: the settings and the model revision.
        self._cache: OrderedDict[str, dict[str, Any]] = OrderedDict()
        self._running_key: str | None = None
        self._running_options: dict[str, Any] | None = None
        #: (path, shown result) while the other mode is worked out for a
        #: workbook with both; the page keeps showing the shown result.
        self._export_pending: tuple[str, list[dict[str, Any]]] | None = None
        #: Every phase's result for an "All phases" solve, else None.
        self._result_set: list[dict[str, Any]] | None = None
        self._watch = QTimer(self)
        self._watch.setInterval(400)
        self._watch.timeout.connect(self._check_model)
        self.setWindowTitle("Analytical Analysis")
        self.setModal(False)
        self.resize(900, 720)
        self._build()
        ThemeManager.instance().on_changed(self._on_theme_changed)
        self._restyle()
        self._begin_for_model()

    # -- construction -------------------------------------------------------

    def _build(self) -> None:  # noqa: PLR0915
        root = QVBoxLayout(self)
        root.setContentsMargins(SPACING["lg"], SPACING["md"], SPACING["lg"],
                                SPACING["md"])
        root.setSpacing(SPACING["md"])

        self._heading = QLabel("Analytical Analysis")
        self._heading.setObjectName("DialogHeading")
        root.addWidget(self._heading)
        self._intro_prefix = (
            "Exact results worked out from the diagram, without simulation, "
            "with every element failing independently. Reliability mode "
            "assumes no repairs; availability mode gives the long-run "
            "fraction of time up when every element is repaired on its "
            "own.\n"
        )
        self._intro = QLabel(self._intro_prefix.rstrip())
        self._intro.setObjectName("SectionNote")
        self._intro.setWordWrap(True)
        root.addWidget(self._intro)

        root.addWidget(self._build_settings())
        root.addLayout(self._build_actions())

        self._banner = QLabel("")
        self._banner.setObjectName("SectionNote")
        self._banner.setWordWrap(True)
        root.addWidget(self._banner)

        self._outlook_note = QLabel("")
        self._outlook_note.setObjectName("SectionNote")
        self._outlook_note.setWordWrap(True)
        self._outlook_note.setTextFormat(Qt.TextFormat.PlainText)
        self._outlook_note.setAccessibleName("Diagram size outlook")
        self._outlook_note.setToolTip(
            "A check of the model's structure made before solving. A diagram "
            "only grows large where a junction's inputs share blocks (other "
            "than through one common element, which the solver factors "
            "out). The exact size is known only once it is built.")
        self._outlook_note.hide()
        root.addWidget(self._outlook_note)

        self._scope_note = QLabel("")
        self._scope_note.setObjectName("ScopeNote")
        self._scope_note.setWordWrap(True)
        self._scope_note.setTextFormat(Qt.TextFormat.PlainText)
        self._scope_note.hide()
        root.addWidget(self._scope_note)

        self._pages = QStackedWidget()
        self._pages.addWidget(self._build_message_page())
        self._pages.addWidget(self._wrap_with_phase_picker(
            self._build_results_page()))
        root.addWidget(self._pages, 1)

        footer = QHBoxLayout()
        footer.addStretch(1)
        self._copy = QPushButton("Copy")
        self._copy.setToolTip("Copy the full result as plain text")
        self._copy.setAutoDefault(False)
        self._copy.setEnabled(False)
        self._copy.clicked.connect(self._copy_result)
        self._export = QPushButton("Export")
        self._export.setToolTip(
            "Save as a spreadsheet (Excel .xlsx or OpenDocument .ods): "
            "summary, elements with "
            "importance measures, curve, equation and assumptions; this "
            "result, or reliability and availability with the same "
            "settings in one workbook")
        self._export.setAutoDefault(False)
        self._export.setEnabled(False)
        menu = QMenu(self._export)
        self._export_this = menu.addAction("This result…")
        self._export_this.triggered.connect(lambda: self.export_result())
        self._export_both = menu.addAction("Reliability and availability…")
        self._export_both.setToolTip(
            "Both modes with the current settings in one workbook; the "
            "other mode is worked out first if it has not been")
        menu.setToolTipsVisible(True)
        self._export_both.triggered.connect(lambda: self.export_both())
        self._export.setMenu(menu)
        close = QPushButton("Close")
        close.setAutoDefault(False)
        close.clicked.connect(self.close)
        footer.addWidget(self._copy)
        footer.addWidget(self._export)
        footer.addWidget(close)
        root.addLayout(footer)

    def _limits_sentence(self, elements: int | None = None,
                         nodes: int | None = None) -> str:
        if elements is None:
            elements = self._max_variables.value()
        if nodes is None:
            nodes = self._max_bdd_nodes.value()
        return (
            f"Size limits: {elements:,} elements and {nodes:,} BDD nodes "
            "(the BDD is the solver's internal map of which failures bring "
            "the system down). Higher limits can make a solve slow.")

    def _refresh_bdd_memory(self) -> None:
        if hasattr(self, "_bdd_memory"):
            nodes = self._max_bdd_nodes.value()
            self._bdd_memory.setText(
                f"(needs {memory_text(nodes)} at most; this machine allows "
                f"up to {self._max_bdd_nodes.maximum():,})")

    def _refresh_limits_text(self) -> None:
        # A tooltip, not a wrapped line: in a short dialog a second wrapped
        # line under the spin boxes was clipped.
        if hasattr(self, "_limits_host"):
            self._limits_host.setToolTip(self._limits_sentence())

    def _build_settings(self) -> QGroupBox:  # noqa: PLR0915
        box = QGroupBox("Settings")
        form = QFormLayout(box)
        form.setFieldGrowthPolicy(
            QFormLayout.FieldGrowthPolicy.AllNonFixedFieldsGrow)
        form.setHorizontalSpacing(SPACING["md"])

        self._mode = QComboBox()
        self._mode.addItem("Reliability — no repairs, R(t)", "reliability")
        self._mode.addItem("Availability — steady state", "availability")
        self._mode.setAccessibleName("Analysis mode")
        self._mode.setToolTip(
            "Reliability: probability of no system failure by the mission "
            "time. Availability: long-run fraction of time the system is up.")
        form.addRow("Mode:", self._mode)

        units = _units(self._win._rbd)
        self._at = _TimeSpin()
        self._at.setRange(0.0, 1e12)
        self._at.setDecimals(6)
        self._at.setStepType(
            QDoubleSpinBox.StepType.AdaptiveDecimalStepType)
        self._at.setKeyboardTracking(False)
        if units:
            self._at.setSuffix(f" {units}")
        self._at.setAccessibleName("Mission time")
        self._at.setToolTip("The time at which R(t), density and hazard are "
                            "evaluated. Defaults to the model's run length.")
        form.addRow("Mission time:", self._at)

        self._times = QLineEdit()
        self._times.setPlaceholderText(
            "blank = 0 to the mission time; or e.g. 0, 24, 48")
        self._times.setAccessibleName("Curve times")
        self._times.setToolTip("Comma-separated times for the reliability "
                               "curve. Leave blank for an even curve from 0 "
                               "to the mission time.")
        form.addRow("Curve at times:", self._times)

        self._bx = QLineEdit("10")
        self._bx.setPlaceholderText("e.g. 10, 5")
        self._bx.setAccessibleName("BX life percentages")
        self._bx.setToolTip("Comma-separated percentages. B10 is the time by "
                            "which 10 % of systems have failed.")
        form.addRow("BX life (%):", self._bx)

        options = QHBoxLayout()
        self._importance = QCheckBox("Importance measures")
        self._importance.setChecked(True)
        self._importance.setToolTip(
            "Exact Birnbaum, RAW, RRW, Fussell–Vesely and criticality for "
            "every element.")
        self._equation = QCheckBox("Equation")
        self._equation.setToolTip(
            "The exact system equation as a sum of path products.")
        for check, name in ((self._importance, "Importance measures"),
                            (self._equation, "Show the equation")):
            check.setAccessibleName(name)
            options.addWidget(check)
        options.addStretch(1)
        self._options_host = QWidget()
        self._options_host.setLayout(options)
        options.setContentsMargins(0, 0, 0, 0)
        form.addRow("Include:", self._options_host)

        ignore = QHBoxLayout()
        ignore.setContentsMargins(0, 0, 0, 0)
        self._ignore = QCheckBox("Repairs")
        self._ignore.setToolTip(
            "Reliability mode only: compute the first-failure reliability "
            "of a model that has repair distributions.")
        self._ignore_pm = QCheckBox("Preventive maintenance")
        self._ignore_pm.setToolTip(
            "Leave out PM: no PM downtime and no age renewal.")
        self._ignore_spares = QCheckBox("Spares")
        self._ignore_spares.setToolTip(
            "Assume a replacement part is always available at once.")
        self._ignore_resources = QCheckBox("Resources")
        self._ignore_resources.setToolTip(
            "Assume crews and tools are always available.")
        for check in (self._ignore, self._ignore_pm, self._ignore_spares,
                      self._ignore_resources):
            check.setAccessibleName(f"Ignore {check.text().lower()}")
            ignore.addWidget(check)
        ignore.addStretch(1)
        self._ignore_host = QWidget()
        self._ignore_host.setLayout(ignore)
        form.addRow("Ignore:", self._ignore_host)

        self._phase = QComboBox()
        self._phase.setAccessibleName("Phase to analyse")
        self._phase.setToolTip(
            "Analyse the system as if it stayed in this phase for the whole "
            "time. Phase changes during the mission are not modelled.")
        form.addRow("Phase:", self._phase)

        self._standby = QComboBox()
        self._standby.setAccessibleName("Standby treatment")
        self._standby.setToolTip(
            "Cold-standby paths are normally refused. Choose to treat them "
            "as fully in service, or as if they did not exist. Switching "
            "time and switch failures are ignored either way.")
        self._standby.addItem("Not chosen (the model is refused)", None)
        self._standby.addItem("Treat as fully in service", "active")
        self._standby.addItem("Treat as absent", "absent")
        form.addRow("Standby:", self._standby)

        self._dependencies = QComboBox()
        self._dependencies.setAccessibleName("Dependency treatment")
        self._dependencies.setToolTip(
            "A dependent element is normally refused. Choose whether every "
            "dependency is up (analyse the dependent element's own law as "
            "independent) or down (remove it from the success path).")
        self._dependencies.addItem("Not chosen (the model is refused)", None)
        self._dependencies.addItem("Treat as up", "up")
        self._dependencies.addItem("Treat as down", "down")
        form.addRow("Dependencies:", self._dependencies)

        from raptor.gui.widgets.hierarchy_picker import HierarchyPicker
        self._hierarchy = HierarchyPicker(home_data=None)
        self._hierarchy.setAccessibleName("Hierarchy to analyse")
        self._hierarchy.setToolTip(
            "Analyse the whole model, or one hierarchy on its own. The "
            "hierarchy's In node is treated as the system Start and its Out "
            "node as the End, so its input is assumed always available; "
            "everything outside the hierarchy is left out.")
        from raptor.gui.widgets.hierarchy_picker import home_button
        self._hierarchy_home = home_button(self._hierarchy)
        self._hierarchy_row = QWidget()
        hierarchy_row = QHBoxLayout(self._hierarchy_row)
        hierarchy_row.setContentsMargins(0, 0, 0, 0)
        hierarchy_row.addWidget(self._hierarchy, 1)
        hierarchy_row.addWidget(self._hierarchy_home)
        form.addRow("Hierarchy:", self._hierarchy_row)

        limits = QHBoxLayout()
        limits.setSpacing(SPACING["sm"])
        limits.setContentsMargins(0, 0, 0, 0)
        self._max_variables = QSpinBox()
        self._max_variables.setRange(1, 1_000_000)
        self._max_variables.setValue(MAX_VARIABLES)
        self._max_variables.setSingleStep(250)
        self._max_variables.setGroupSeparatorShown(True)
        self._max_variables.setAccessibleName("Maximum elements")
        self._max_variables.setToolTip(
            "Largest number of blocks and events the solver will take on.")
        self._max_bdd_nodes = QSpinBox()
        self._max_bdd_nodes.setRange(2, max_bdd_nodes_allowed())
        self._max_bdd_nodes.setValue(MAX_BDD_NODES)
        self._max_bdd_nodes.setSingleStep(100_000)
        self._max_bdd_nodes.setGroupSeparatorShown(True)
        self._max_bdd_nodes.setAccessibleName("Maximum BDD nodes")
        self._max_bdd_nodes.setToolTip(
            "The most nodes the solver's diagram may grow to before it gives "
            "up. The default suits almost every model. A higher limit needs "
            "more memory (shown alongside) and makes the solve, importance "
            "measures and curve slower. The largest limit offered is half "
            "of the memory free on this machine.")
        limits.addWidget(self._max_variables)
        limits.addWidget(QLabel("elements"))
        limits.addSpacing(SPACING["md"])
        limits.addWidget(self._max_bdd_nodes)
        limits.addWidget(QLabel("BDD nodes"))
        self._bdd_memory = QLabel("")
        self._bdd_memory.setObjectName("SectionNote")
        limits.addWidget(self._bdd_memory)
        limits.addStretch(1)
        limits_host = QWidget()
        limits_host.setLayout(limits)
        # Size limits are rarely needed, so they sit behind "Advanced".
        self._advanced = QCheckBox("Show advanced settings")
        self._advanced.setAccessibleName("Show advanced settings")
        form.addRow("", self._advanced)
        form.addRow("Limits:", limits_host)
        self._limits_host = limits_host
        self._advanced.toggled.connect(self._show_advanced)

        self._form = form
        self._reliability_rows = (self._at, self._times, self._bx)
        self._mode.currentIndexChanged.connect(self._mode_changed)
        # Typed values (times, limits) wait for Compute or Enter: solving on
        # every keystroke would start a solve per digit.
        for signal in (self._at.valueChanged, self._times.textChanged,
                       self._bx.textChanged,
                       self._max_variables.valueChanged,
                       self._max_bdd_nodes.valueChanged):
            signal.connect(self._settings_changed)
        # A pick from a list or a tick is a finished choice, but nothing
        # solves until Compute is pressed (a large model takes minutes).
        # The page below says so at once: ticking the very option a refusal
        # suggested must not leave the refusal on screen.
        for signal in (self._importance.toggled, self._equation.toggled,
                       self._ignore.toggled, self._ignore_pm.toggled,
                       self._ignore_spares.toggled,
                       self._ignore_resources.toggled,
                       self._phase.currentIndexChanged,
                       self._standby.currentIndexChanged,
                       self._dependencies.currentIndexChanged,
                       self._hierarchy.currentIndexChanged):
            signal.connect(self._choice_changed)
        self._max_variables.valueChanged.connect(self._refresh_limits_text)
        self._max_bdd_nodes.valueChanged.connect(self._refresh_limits_text)
        self._max_bdd_nodes.valueChanged.connect(self._refresh_bdd_memory)
        self._refresh_bdd_memory()
        self._refresh_limits_text()
        self._show_advanced(False)
        for line in (self._times, self._bx):
            line.returnPressed.connect(self.compute)
        return box

    def _build_actions(self) -> QHBoxLayout:
        row = QHBoxLayout()
        self._compute = QPushButton("Compute")
        self._compute.setDefault(True)
        self._compute.setToolTip("Solve with the current settings (Enter)")
        self._compute.clicked.connect(self.compute)
        # A thin bar in the theme's colours, like the main window's run bar
        # (styled in `_restyle`; unstyled, the platform drew it black on a
        # light theme). The percentage leads the line beside it.
        self._progress = QProgressBar()
        self._progress.setRange(0, 0)
        self._progress.setTextVisible(False)
        self._progress.setFixedSize(160, 6)
        self._progress.setAccessibleName("Computing")
        self._progress.hide()
        self._progress_label = QLabel("")
        self._progress_label.setObjectName("SectionNote")
        self._progress_label.setMinimumWidth(220)
        self._progress_label.hide()
        self._cancel = QPushButton("Cancel")
        self._cancel.setToolTip("Stop the current analytical solve")
        self._cancel.clicked.connect(self._cancel_solve)
        self._cancel.hide()
        row.addWidget(self._compute)
        row.addWidget(self._cancel)
        row.addWidget(self._progress)
        row.addWidget(self._progress_label)
        row.addStretch(1)
        return row

    def _build_message_page(self) -> QWidget:
        page = QFrame()
        page.setObjectName("NoticeCard")
        lay = QVBoxLayout(page)
        lay.setContentsMargins(SPACING["lg"], SPACING["lg"], SPACING["lg"],
                               SPACING["lg"])
        lay.addStretch(1)
        self._notice_title = QLabel("")
        self._notice_title.setObjectName("NoticeHeading")
        self._notice_title.setWordWrap(True)
        self._notice_body = QLabel("")
        self._notice_body.setWordWrap(True)
        self._notice_body.setTextInteractionFlags(
            Qt.TextInteractionFlag.TextSelectableByMouse)
        self._notice_body.setAccessibleName("Analysis message")
        lay.addWidget(self._notice_title)
        lay.addWidget(self._notice_body)
        # One-click versions of the advice in the message: most real models
        # have repairs, so reliability mode refuses them on first open.
        fixes = QHBoxLayout()
        fixes.setContentsMargins(0, SPACING["sm"], 0, 0)
        self._fix = QPushButton("")
        self._fix.setAccessibleName("Apply the suggested setting")
        self._fix.clicked.connect(self._apply_fix)
        self._alt_fix = QPushButton("Use availability mode instead")
        self._alt_fix.setToolTip(
            "Steady-state availability keeps the repairs in the answer.")
        self._alt_fix.clicked.connect(self._use_availability)
        for button in (self._fix, self._alt_fix):
            button.hide()
            fixes.addWidget(button)
        fixes.addStretch(1)
        lay.addLayout(fixes)
        self._fix_action: Any = None
        lay.addStretch(2)
        return page

    def _show_advanced(self, shown: bool) -> None:
        self._form.setRowVisible(self._limits_host, shown)

    def _offer_fix(self, key: str | None) -> None:
        """Show the button that applies the refusal's suggestion, if any."""
        checks = {
            "repair distribution": (self._ignore, "Ignore repairs and compute"),
            "preventive maintenance": (self._ignore_pm,
                                       "Ignore preventive maintenance and "
                                       "compute"),
            "spare": (self._ignore_spares, "Ignore spares and compute"),
            "resource": (self._ignore_resources,
                         "Ignore resources and compute"),
        }
        combos = {
            "has phases": (self._phase, "Choose a phase…"),
            "cold standby": (self._standby, "Choose a standby treatment…"),
            "dependency": (self._dependencies,
                           "Choose a dependency treatment…"),
        }
        self._fix_action = None
        # The buttons are one click: they apply the fix and solve. A list
        # opened by one solves once a pick is made from it.
        if key in checks:
            box, label = checks[key]

            def tick(box=box) -> None:
                box.setChecked(True)
                self.compute()
            self._fix_action = tick
            self._fix.setText(label)
        elif key in combos:
            combo, label = combos[key]

            def pick(combo=combo) -> None:
                self._solve_on_pick = combo
                combo.setFocus()
                combo.showPopup()
            self._fix_action = pick
            self._fix.setText(label)
        self._fix.setVisible(self._fix_action is not None)
        self._alt_fix.setVisible(
            key == "repair distribution"
            and self._mode.currentData() == "reliability")

    def _offer_larger_limit(self, allowed: int) -> None:
        """A size refusal that a larger limit might fix: offer ``allowed``
        nodes, saying what it costs."""
        def raise_limit() -> None:
            self._max_bdd_nodes.setValue(
                min(allowed, self._max_bdd_nodes.maximum()))
            self.compute()
        self._fix_action = raise_limit
        self._fix.setText(f"Try a limit of {allowed:,} nodes "
                          f"({memory_text(allowed)} at most)")
        self._fix.setVisible(True)

    def _use_availability(self) -> None:
        self._mode.setCurrentIndex(max(0, self._mode.findData("availability")))
        self.compute()

    def _apply_fix(self) -> None:
        if self._fix_action is not None:
            self._fix_action()

    def _build_elements_table(self) -> QTableWidget:
        table = QTableWidget(0, 0)
        table.setAccessibleName("Per-element results")
        table.setEditTriggers(
            QAbstractItemView.EditTrigger.NoEditTriggers)
        table.setSelectionBehavior(
            QAbstractItemView.SelectionBehavior.SelectRows)
        table.setAlternatingRowColors(True)
        table.setSortingEnabled(True)
        table.verticalHeader().setVisible(False)
        # Widths are set per fill and on resize (`_spread_columns`): each
        # column's natural width, spare room shared by the measures. Not
        # ResizeToContents, which measured every row on each repaint (slow
        # for thousands of elements); only the first 200 rows are measured.
        table.horizontalHeader().setResizeContentsPrecision(200)
        table.setObjectName("ElementTable")
        table.setHorizontalScrollMode(
            QAbstractItemView.ScrollMode.ScrollPerPixel)
        table.viewport().installEventFilter(self)
        # Thousands of elements: a per-column filter row (shown on request,
        # and only when there are more than a handful of rows).
        FilterBar.attach(table, raw_role=Qt.ItemDataRole.UserRole + 1)
        return table

    def _wrap_with_phase_picker(self, tabs: QWidget) -> QWidget:
        """The result tabs, with a "Showing phase" list above them for an
        "All phases" result (one result per phase, shown one at a time)."""
        page = QWidget()
        lay = QVBoxLayout(page)
        lay.setContentsMargins(0, 0, 0, 0)
        row = QHBoxLayout()
        self._phase_picker_label = QLabel("Showing phase:")
        self._phase_picker = QComboBox()
        self._phase_picker.setAccessibleName("Phase shown")
        self._phase_picker.setToolTip(
            "Each phase was solved on its own. Pick one to see its result; "
            "Export saves them all")
        self._phase_picker.currentIndexChanged.connect(self._phase_picked)
        row.addWidget(self._phase_picker_label)
        row.addWidget(self._phase_picker, 1)
        row.addStretch(1)
        self._phase_picker_row = QWidget()
        self._phase_picker_row.setLayout(row)
        row.setContentsMargins(0, 0, 0, SPACING["sm"])
        self._phase_picker_row.hide()
        lay.addWidget(self._phase_picker_row)
        lay.addWidget(tabs, 1)
        return page

    def _phase_picked(self, index: int) -> None:
        results = self._result_set
        if not results or not 0 <= index < len(results):
            return
        self._result = results[index]
        self._show_result(self._result)

    def _display(self, result: dict[str, Any]) -> None:
        """Show a result, or a set with one result per phase."""
        phases = result.get("phases")
        self._result_set = list(phases) if phases else None
        self._phase_picker.blockSignals(True)
        self._phase_picker.clear()
        if phases:
            for item in phases:
                scope = item.get("scope") or {}
                self._phase_picker.addItem(
                    f"{scope.get('phase')}. {scope.get('phase_name') or ''}"
                    .rstrip())
        self._phase_picker.blockSignals(False)
        self._phase_picker_row.setVisible(bool(phases))
        self._result = phases[0] if phases else result
        self._show_result(self._result)

    def _results_to_save(self) -> list[dict[str, Any]]:
        """What This result saves: every phase of an "All phases" set."""
        if self._result_set:
            return list(self._result_set)
        return [self._result] if self._result is not None else []

    def _build_results_page(self) -> QWidget:
        self._tabs = QTabWidget()
        self._tabs.setObjectName("AnalyticTabs")
        self._tabs.setDocumentMode(True)
        # The tab bar's own base line is drawn in the palette's shadow
        # shade, a black line on the light theme; `_restyle` draws it in
        # the theme's border colour instead.
        self._tabs.tabBar().setDrawBase(False)

        # The cards keep their natural size: in a short window the page
        # scrolls, and in a narrow one they reflow to fewer per row, rather
        # than squashing (which cut the big numbers in half).
        self._summary_page = _CardGrid()
        self._cards = self._summary_page.grid
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        scroll.setWidget(self._summary_page)
        self._tab_summary = self._add_page(scroll, "Summary")

        self._elements = self._build_elements_table()
        self._tab_elements = self._add_page(self._elements, "Elements")

        curve = QWidget()
        curve_lay = QHBoxLayout(curve)
        curve_lay.setContentsMargins(0, SPACING["sm"], 0, 0)
        self._plot = _CurvePlot()
        self._curve_table = QTableWidget(0, 2)
        self._curve_table.setAccessibleName("Reliability curve values")
        self._curve_table.setEditTriggers(
            QAbstractItemView.EditTrigger.NoEditTriggers)
        self._curve_table.setAlternatingRowColors(True)
        self._curve_table.verticalHeader().setVisible(False)
        self._curve_table.horizontalHeader().setStretchLastSection(True)
        self._curve_table.setMinimumWidth(220)
        self._curve_table.setMaximumWidth(300)
        curve_lay.addWidget(self._plot, 3)
        curve_lay.addWidget(self._curve_table, 1)
        self._tab_curve = self._add_page(curve, "Curve")

        self._equation_view = QPlainTextEdit()
        self._equation_view.setReadOnly(True)
        self._equation_view.setAccessibleName("System equation")
        self._equation_view.setFont(QFontDatabase.systemFont(
            QFontDatabase.SystemFont.FixedFont))
        equation_page = QWidget()
        equation_lay = QVBoxLayout(equation_page)
        equation_lay.setContentsMargins(0, SPACING["sm"], 0, 0)
        self._equation_note = QLabel("")
        self._equation_note.setObjectName("SectionNote")
        self._equation_note.setWordWrap(True)
        equation_lay.addWidget(self._equation_note)
        equation_lay.addWidget(self._equation_view, 1)
        self._tab_equation = self._add_page(equation_page, "Equation")

        self._notes = QPlainTextEdit()
        self._notes.setReadOnly(True)
        self._notes.setAccessibleName("Assumptions and warnings")
        self._tab_notes = self._add_page(self._notes, "Assumptions")
        return self._tabs

    # The result tabs. A tab that does not apply (Curve in availability
    # mode, Equation when not asked for) is taken out and put back, not
    # hidden: a hidden tab under the styled tab bar left its width as a
    # gap ("Summary, Elements, ____, Assumptions"). So a tab is named by
    # its page widget, never by an index, which moves.

    def _add_page(self, page: QWidget, label: str) -> QWidget:
        self._tabs.addTab(page, label)
        self._tab_order = [*getattr(self, "_tab_order", []), (page, label)]
        return page

    def tab_shown(self, page: QWidget) -> bool:
        return self._tabs.indexOf(page) != -1

    def show_tab(self, page: QWidget) -> None:
        """Bring ``page``'s tab to the front."""
        if self.tab_shown(page):
            self._tabs.setCurrentWidget(page)

    def _set_tab_shown(self, page: QWidget, shown: bool) -> None:
        if shown == self.tab_shown(page):
            return
        if not shown:
            self._tabs.removeTab(self._tabs.indexOf(page))
            return
        position = 0
        for other, label in self._tab_order:
            if other is page:
                self._tabs.insertTab(position, page, label)
                return
            if self.tab_shown(other):
                position += 1

    # -- theme ----------------------------------------------------------------

    def _restyle(self) -> None:
        c = active_colour()
        self.setStyleSheet(
            f"QLabel#ScopeNote {{ color: {c['text']}; "
            f"border: 1px solid {c['accent']}; "
            f"border-left: {SPACING['xs']}px solid {c['accent']}; "
            f"border-radius: {RADIUS['control']}px; "
            f"padding: {SPACING['sm']}px {SPACING['md']}px; }}"
            f"QLabel#DialogHeading {{ font-size: {TYPE['title']}px; "
            f"font-weight: 600; color: {c['text']}; }}"
            f"QLabel#SectionNote {{ color: {c['muted']}; "
            f"font-size: {TYPE['label']}px; }}"
            f"QFrame#NoticeCard {{ background: {c['canvas']}; "
            f"border: 1px solid {c['border']}; "
            f"border-radius: {RADIUS['card']}px; }}"
            f"QLabel#NoticeHeading {{ font-size: {_px('body') + 3}px; "
            f"font-weight: 600; color: {c['text']}; }}"
            f"QFrame#MetricCard {{ background: {c['canvas']}; "
            f"border: 1px solid {c['border']}; "
            f"border-radius: {RADIUS['card']}px; }}"
            f"QLabel#MetricCaption {{ color: {c['muted']}; "
            f"font-size: {TYPE['caption']}px; background: transparent; }}"
            f"QLabel#MetricValue {{ color: {c['text']}; font-size: 22px; "
            f"font-weight: 600; background: transparent; }}"
            f"QLabel#MetricSub {{ color: {c['muted']}; "
            f"font-size: {TYPE['caption']}px; background: transparent; }}"
            f"QTabWidget#AnalyticTabs::pane {{ border: none; "
            f"border-top: 1px solid {c['border']}; }}"
            f"QTableWidget#ElementTable::item {{ padding: 0 {SPACING['sm']}px; }}"
            # Room on the right of each heading for the sort arrow, which
            # otherwise sits on a right-aligned heading's last letters.
            f"QTableWidget#ElementTable QHeaderView::section {{ "
            f"padding-left: {SPACING['sm']}px; padding-right: 18px; }}"
            f"QProgressBar {{ border: none; border-radius: 3px; "
            f"background: {c['border']}; }}"
            f"QProgressBar::chunk {{ border-radius: 3px; "
            f"background: {c['accent']}; }}")
        self._plot.update()

    def _on_theme_changed(self, _dark: bool) -> None:
        self._restyle()

    # -- state ----------------------------------------------------------------

    def _begin_for_model(self) -> None:
        """Start from the open model: its run length, then solve if we can."""
        # Free memory changes; read the ceiling afresh for each model.
        self._max_bdd_nodes.setMaximum(max_bdd_nodes_allowed())
        self._refresh_bdd_memory()
        rbd = self._win._rbd
        self._shown_rbd = weakref.ref(rbd)
        self._shown_path = getattr(self._win, "_current_file", None)
        self._model_serial += 1
        self._cache.clear()
        self._result = None
        # The last solve was of the old model: an "edited since" note
        # compares against it, and said "The model has changed since this
        # result" over a fresh page for a newly opened model.
        self._solved_revision = None
        self._stale_shown = False
        horizon = _run_length(rbd)
        self._at.blockSignals(True)
        self._at.setValue(horizon if horizon is not None else 0.0)
        self._at.blockSignals(False)
        self._set_title(rbd)
        self._fill_scope_controls(rbd)
        self._mode_changed(solve=False)
        # Nothing solves on opening: a large model takes minutes, and the
        # settings (mode, mission time, phase, dependencies, ...) usually
        # want a look first. Compute is the default button, so Enter runs it.
        if horizon is None and self._mode.currentData() == "reliability":
            self._show_notice(
                "Enter a mission time",
                "This model is not time-terminated, so it has no default "
                "mission time. Enter the time to evaluate, then press "
                "Compute.")
            self._at.setFocus()
        else:
            self._show_ready()
            self._compute.setFocus()

    def _fill_scope_controls(self, rbd: Any) -> None:
        """Show the phase, standby and dependency choices only when the
        model has them."""
        try:
            offered = scope_options(rbd)
        except Exception:  # noqa: BLE001 - the solve will explain a bad model
            offered = {"phases": [], "has_standby": False,
                       "has_dependencies": False}
        phases = offered["phases"]
        for widget in (self._phase, self._standby, self._dependencies,
                       self._hierarchy):
            widget.blockSignals(True)
        self._phase.clear()
        if phases:
            self._phase.addItem("Choose a phase…", None)
            for number, name in enumerate(phases, 1):
                self._phase.addItem(f"{number}. {name}", number)
            if len(phases) > 1:
                self._phase.addItem("All phases, one at a time", "all")
        self._standby.setCurrentIndex(0)
        self._hierarchy.set_hierarchies(
            [(h["id"], f"{h['name']} ({h['id']})")
             for h in offered.get("hierarchies") or []])
        # A choice made for the previous model must not leak into this one.
        for widget in (self._phase, self._standby, self._dependencies,
                       self._hierarchy):
            widget.setCurrentIndex(0)
        for widget in (self._phase, self._standby, self._dependencies,
                       self._hierarchy):
            widget.blockSignals(False)
        self._form.setRowVisible(self._phase, bool(phases))
        self._has_standby = bool(offered["has_standby"])
        self._form.setRowVisible(self._standby, self._has_standby)
        self._has_dependencies = bool(offered.get("has_dependencies"))
        self._form.setRowVisible(self._dependencies, self._has_dependencies)
        self._has_hierarchies = bool(offered.get("hierarchies"))
        self._form.setRowVisible(self._hierarchy_row, self._has_hierarchies)
        self._hierarchy_home.setEnabled(not self._hierarchy.is_home())

    def _model_name(self) -> str:
        path = getattr(self._win, "_current_file", None)
        if path:
            return os.path.splitext(os.path.basename(str(path)))[0]
        name = getattr(self._win._rbd, "file_name", None)
        return os.path.splitext(str(name))[0] if name else "Untitled model"

    def _set_title(self, rbd: Any) -> None:
        name = self._model_name()
        shown = name if len(name) <= 48 else name[:45] + "…"
        self._heading.setText(f"Analytical Analysis — {shown}")
        self._heading.setToolTip(name)
        self.setWindowTitle(f"Analytical Analysis — {shown}")

    def _mode_changed(self, *_args, solve: bool = True) -> None:
        reliability = self._mode.currentData() == "reliability"
        for widget in self._reliability_rows:
            self._form.setRowVisible(widget, reliability)
        self._ignore.setVisible(reliability)
        if solve:
            self._choice_changed()

    def _settings_changed(self, *_args) -> None:
        """A setting changed: say what Compute will do, without solving."""
        if self._busy:
            # Shown once the running solve is delivered.
            self._changed_while_busy = True
        elif self._pages.currentIndex() == 0:
            # A notice (a refusal, "cancelled", ...) is about the old
            # settings; the new ones have not been tried yet.
            self._show_ready()
        else:
            self._mark_stale(
                "Settings changed — press Compute to update the results.")

    def _choice_changed(self, *_args) -> None:
        picked = self._solve_on_pick is not None \
            and self.sender() is self._solve_on_pick
        self._solve_on_pick = None
        if picked:
            self.compute()       # the pick a fix button asked for
        elif self._busy:
            self._changed_while_busy = True
        elif not self._show_cached():
            # A different treatment is a different question: the old
            # answer must not stay on screen looking like this one's.
            self._show_ready()

    def _same_model(self) -> bool:
        """Is the window's model still the one this page was set up for?"""
        shown = self._shown_rbd() if self._shown_rbd is not None else None
        return (shown is not None and shown is self._win._rbd
                and getattr(self._win, "_current_file", None)
                == self._shown_path)

    def _cache_key(self, options: dict[str, Any] | None = None) -> str | None:
        """The settings (the current ones by default) and model revision,
        or None if a setting does not parse. The limits are left out: they
        decide whether a solve finishes, never its answer."""
        if options is None:
            try:
                options = self._values()
            except ValueError:
                return None
        options = dict(options)
        options.pop("max_variables", None)
        options.pop("max_bdd_nodes", None)
        return json.dumps([self._model_serial,
                           getattr(self._win, "_model_revision", 0), options],
                          sort_keys=True, default=str)

    def _show_cached(self) -> bool:
        """Show the result already worked out for these settings, if any."""
        key = self._cache_key()
        result = self._cache.get(key) if key is not None else None
        if key is None or result is None:
            return False
        self._cache.move_to_end(key)
        self._stale_shown = False
        self._solved_revision = getattr(self._win, "_model_revision", 0)
        self._display(result)
        self._banner.setText(self._banner.text()
                             + "  Shown from earlier: these settings were "
                               "already solved for this model.")
        return True

    def _show_ready(self) -> None:
        """Nothing for these settings yet: say so, and that earlier results
        are kept (no need to export before trying another treatment)."""
        self._show_notice(_READY_TITLE, _READY_BODY)
        if self._cache:
            self._notice_body.setText(
                _READY_BODY + "\n\nEarlier results are kept while this "
                "window is open: change the settings back to see one "
                "again at once.")

    def _mark_stale(self, text: str) -> None:
        """Say the panel below no longer matches the settings or model."""
        self._stale_shown = True
        self._banner.setText(text)

    def _check_model(self) -> None:
        """Notice a model edit or a different model while the dialog is open.

        A different model starts over, as reopening the dialog does. An edit
        to the same model only marks the result out of date: re-solving a
        large model on every edit would be far too slow.
        """
        if self._win._rbd is None:
            # The model was closed: its results must not stay up as if
            # they were still about an open model.
            if self._shown_rbd is not None:
                self._shown_rbd = None
                self._cache.clear()
                self._result = None
                self._solved_revision = None
                if self._busy:
                    self._cancel_solve()
                self._show_notice("No model open",
                                  "Open a model to analyse it.")
            return
        if not self._same_model():
            self._begin_for_model()
            return
        revision = getattr(self._win, "_model_revision", 0)
        if (self._solved_revision is not None and not self._busy
                and self._result is not None
                and self._pages.currentIndex() == 1
                and revision != self._solved_revision
                and not self._stale_shown):
            self._mark_stale("The model has changed since this result — "
                             "press Compute to update it.")

    def showEvent(self, event) -> None:  # noqa: N802 (Qt API)
        super().showEvent(event)
        self._watch.start()

    def hideEvent(self, event) -> None:  # noqa: N802 (Qt API)
        self._watch.stop()
        # Closing the window stops the solve: nobody is left to see it, and
        # a large one would otherwise keep a core busy for minutes.
        if self._busy:
            self._cancel_solve()
        super().hideEvent(event)

    def shut_down(self, wait_s: float = 3.0) -> None:
        """Close for good (the application is exiting): cancel any solve and
        give the worker a moment to stop at its next progress check."""
        self._cancel_ev.set()
        self.close()
        worker = self._worker
        if worker is not None and worker.is_alive():
            worker.join(wait_s)

    def _values(self, mode: str | None = None) -> dict[str, Any]:
        """The solve's options: the current settings, in ``mode`` if given
        (the other mode, for a workbook with both)."""
        mode = mode or self._mode.currentData()
        reliability = mode == "reliability"
        times: list[float] = []
        bx: list[float] = []
        if reliability:
            try:
                times = _floats(self._times.text())
                bx = _floats(self._bx.text())
            except ValueError:
                raise ValueError(
                    "Curve times and BX percentages must be numbers "
                    "separated by commas.") from None
        at = float(self._at.value()) if reliability else None
        if reliability and not times and at and at > 0.0:
            times = [at * i / (_AUTO_CURVE_POINTS - 1)
                     for i in range(_AUTO_CURVE_POINTS)]
        return {
            "mode": mode,
            "at": at,
            "times": times or None,
            "bx": bx,
            "importance": self._importance.isChecked(),
            "equation": self._equation.isChecked(),
            "ignore": self._ignored(reliability),
            "phase": self._phase.currentData(),
            "standby": (self._standby.currentData()
                        if self._has_standby else None),
            "dependencies": (self._dependencies.currentData()
                             if self._has_dependencies else None),
            "hierarchy": (self._hierarchy.currentData()
                          if getattr(self, "_has_hierarchies", False)
                          else None),
            "max_variables": self._max_variables.value(),
            "max_bdd_nodes": self._max_bdd_nodes.value(),
        }

    def _ignored(self, reliability: bool) -> list[str]:
        chosen = []
        for check, key in ((self._ignore, "repairs"),
                           (self._ignore_pm, "pm"),
                           (self._ignore_spares, "spares"),
                           (self._ignore_resources, "resources")):
            if check.isChecked() and (reliability or key != "repairs"):
                chosen.append(key)
        return chosen

    # -- running --------------------------------------------------------------

    def compute(self) -> None:
        """Solve with the current settings (asynchronously)."""
        if self._busy:
            self._rerun = True   # solve again with the newer settings after
            return
        try:
            options = self._values()
        except ValueError as exc:
            self._show_notice("Check the settings", str(exc))
            return
        if self._show_cached():
            return
        self._start_solve(options)

    def _start_solve(self, options: dict[str, Any]) -> None:
        self._running_key = self._cache_key(options)
        self._running_options = dict(options)
        self._token += 1
        self._cancel_ev.clear()
        self._outlook_note.hide()
        self._solved_revision = getattr(self._win, "_model_revision", 0)
        self._stale_shown = False
        self._changed_while_busy = False
        if self._pages.currentIndex() == 0:
            # Replace the old notice (a refusal, "ready", ...) at once, so
            # the choice just made is not shown as still refused for the
            # whole of a long solve.
            self._show_notice(
                "Working out the results…",
                "Progress and the time left are shown beside the Compute "
                "button. Cancel, or closing this window, stops the solve.")
        self._set_busy(True)
        self._worker = threading.Thread(
            target=_solve, daemon=True,
            args=(self._token, copy.deepcopy(self._win._rbd),
                  self._model_name(), options, self._inbox,
                  self._progress_inbox, self._cancel_ev))
        self._worker.start()

    def _cancel_solve(self) -> None:
        if not self._busy:
            return
        self._cancel_ev.set()
        self._cancel.setEnabled(False)
        self._banner.setText("Cancelling…")

    def wait_for_result(self) -> None:
        """Block until the running solve has been delivered (for scripts
        and tests; the application never calls this)."""
        deadline = time.monotonic() + 120.0
        while self._busy and time.monotonic() < deadline:
            self._drain()
            time.sleep(0.005)
        QApplication.processEvents()

    def _set_busy(self, busy: bool) -> None:
        self._busy = busy
        self._compute.setEnabled(not busy)
        self._cancel.setVisible(busy)
        self._cancel.setEnabled(busy)
        self._progress.setVisible(busy)
        self._progress_label.setVisible(busy)
        if busy:
            self._progress.setRange(0, 0)
            self._progress.setValue(0)
            self._progress_label.setText("Starting…")
            self._banner.setText("Computing…")
            self._poll.start()
        else:
            self._poll.stop()
            self._progress_label.setText("")

    def _drain(self) -> None:
        """Deliver progress and finished solves (runs on the GUI thread)."""
        while True:
            try:
                token, stage, done, total = \
                    self._progress_inbox.get_nowait()
            except queue.Empty:
                break
            self._on_progress(token, stage, done, total)
        while True:
            try:
                token, result, error = self._inbox.get_nowait()
            except queue.Empty:
                return
            self._on_done(token, result, error)

    def _on_progress(self, token: int, stage: str, done: int,
                     total: int) -> None:
        if token != self._token or not self._busy:
            return
        if stage.startswith("Outlook: "):
            text = stage.removeprefix("Outlook: ")
            self._outlook_note.setText("Diagram size: " + text[:1].upper()
                                       + text[1:])
            self._outlook_note.show()
            return
        total = max(0, int(total))
        if total:
            # One figure for the whole solve: the solver gives the build its
            # expected share and the results the rest (`_Overall`), so the
            # bar fills once rather than filling and starting again.
            permille = min(1000, max(0, int(done)) * 1000 // total)
            permille = max(permille, self._progress.value())
            self._progress.setRange(0, 1000)
            self._progress.setValue(permille)
            self._progress_label.setText(
                f"{permille // 10}% · {_without_pass_count(stage)}")
        else:
            self._progress.setRange(0, 0)
            self._progress_label.setText(
                stage if not done else f"{stage} — {done:,}")

    def _on_done(self, token: int, result: Any, error: Any) -> None:
        if token != self._token:
            return          # a superseded solve
        if error is None and self._cancel_ev.is_set():
            result, error = None, ("cancelled", "Cancelled")
        self._set_busy(False)
        if self._export_pending is not None:
            self._finish_export_both(result, error)
            return
        if self._rerun:
            self._rerun = False
            self.compute()
            return
        if error is not None:
            self._show_error(error)
            return
        self._deliver(result)

    def _keep(self, result: dict[str, Any]) -> None:
        """Cache a finished solve; each phase of an "All phases" set is
        also kept on its own, so choosing that phase later shows it."""
        entries = [(self._running_key, result)]
        options = self._running_options
        for item in result.get("phases") or ():
            if options is not None:
                phase = (item.get("scope") or {}).get("phase")
                entries.append((self._cache_key(dict(options, phase=phase)),
                                item))
        for key, value in entries:
            if key is not None:
                self._cache[key] = value
        while len(self._cache) > _CACHE_SIZE:
            self._cache.popitem(last=False)

    def _show_error(self, error: Any) -> None:
        """A solve that did not give a result: say why, offer a fix."""
        self._result = None
        self._set_actions(False)
        kind, message, *size = error
        if kind == "cancelled":
            self._banner.setText("Cancelled")
            self._show_notice(
                "Analysis cancelled",
                "The solve was stopped. The model was not changed.")
            return
        titles = {
            "refused": "This model is outside the static solver",
            "invalid": "Check the settings",
            "failed": "The analysis could not be completed",
        }
        tips = {
            "repair distribution": "tick Ignore ▸ Repairs",
            "preventive maintenance": "tick Ignore ▸ Preventive "
                                      "maintenance",
            "spare": "tick Ignore ▸ Spares",
            "resource": "tick Ignore ▸ Resources",
            "has phases": "choose a Phase",
            "cold standby": "choose a Standby treatment",
            "dependency": "choose a Dependencies treatment",
        }
        lowered = message.lower()
        advice = [tip for key, tip in tips.items() if key in lowered]
        fix_key = next((key for key in tips if key in lowered), None)
        suggestion = (f"\n\nTo analyse the model anyway, "
                      f"{advice[0]}." if advice else "")
        hint = {
            "refused": "\n\nSimulation handles repairs, preventive "
                       "maintenance, spares, resources, dependencies, "
                       "standby and phases." + suggestion,
            "invalid": "",
            "failed": "\n\nThe model was not changed.",
        }[kind]
        message = message.rstrip()
        if message and message[-1] not in ".!?":
            message += "."
        self._show_notice(titles[kind],
                          message[:1].upper() + message[1:] + hint)
        if kind == "refused":
            self._offer_fix(fix_key)
            if len(size) == 2 and size[1] > size[0]:
                # A size refusal: a step up first (4x, at least the
                # default), not straight to the most this machine takes.
                self._notice_title.setText(
                    "The solver's diagram grew too large")
                self._offer_larger_limit(
                    min(size[1], max(MAX_BDD_NODES, 4 * size[0])))
            if "limit" in lowered:
                # A size refusal: show where the limits are set.
                self._advanced.setChecked(True)

    def _deliver(self, result: dict[str, Any]) -> None:
        """Show a finished solve, and keep it for these settings."""
        self._keep(result)
        self._display(result)
        if self._changed_while_busy:
            self._mark_stale(
                "Settings changed — press Compute to update the results.")

    # -- display --------------------------------------------------------------

    def _show_notice(self, title: str, body: str) -> None:
        self._notice_title.setText(title)
        self._notice_body.setText(body)
        self._pages.setCurrentIndex(0)
        self._banner.setText("")
        # The "This result assumes" line belongs to a result; with a notice
        # in its place it would describe something no longer shown.
        self._scope_note.hide()
        self._fix.hide()
        self._alt_fix.hide()
        self._set_actions(False)

    def _show_result(self, result: dict[str, Any]) -> None:
        # The size outlook matters while solving, and afterwards only as a
        # warning; a reassuring one would just take room from the results.
        if "may grow large" not in self._outlook_note.text():
            self._outlook_note.hide()
        self._fill_summary(result)
        self._fill_elements(result)
        self._fill_curve(result)
        equation = result.get("equation")
        self._equation_view.setPlainText(equation or "")
        quantity = ("reliability" if result["mode"] == "reliability"
                    else "availability")
        letter = "R" if result["mode"] == "reliability" else "A"
        self._equation_note.setText(
            f"The system {quantity} written out exactly. {letter}_name is "
            f"that element's {quantity}"
            + (" at the mission time" if letter == "R" else "")
            + f"; (1 - {letter}_name) means it has failed. Each bracketed "
            "term is one distinct way the system can work, and the terms "
            "never overlap, so adding them gives the system "
            f"{quantity}.")
        self._fill_notes(result)
        self._set_tab_shown(self._tab_curve, bool(result.get("times")))
        self._set_tab_shown(self._tab_equation, bool(equation))
        self._show_scope(result)
        self._pages.setCurrentIndex(1)
        if self._tabs.currentIndex() == -1:
            self._tabs.setCurrentWidget(self._tab_summary)
        self._set_actions(True)
        warnings = len(result.get("warnings") or [])
        note = (f"  {warnings} warning(s) on the Assumptions tab."
                if warnings else "")
        hierarchy = result.get("hierarchy")
        scope = ("Exact result for hierarchy "
                 f"{hierarchy} on its own (its input assumed always "
                 "available)."
                 if hierarchy else
                 "Exact result worked out from the diagram, with no "
                 "simulation. The model was not changed.")
        self._banner.setText(f"{scope}{note}")

    def _show_scope(self, result: dict[str, Any]) -> None:
        """Say plainly, above every tab, what this answer assumed."""
        text = scope_sentence(result.get("scope"))
        self._scope_note.setText(text)
        self._scope_note.setVisible(bool(text))

    def _clear_cards(self) -> None:
        while self._cards.count():
            item = self._cards.takeAt(0)
            widget = item.widget() if item is not None else None
            if widget is not None:
                widget.deleteLater()

    def _card(self, caption: str, value: float | None, tip: str,
              *, sub: str = "", suffix: str = "",
              probability: bool = False) -> QFrame:
        card = QFrame()
        card.setObjectName("MetricCard")
        card.setSizePolicy(QSizePolicy.Policy.Preferred,
                           QSizePolicy.Policy.Fixed)
        lay = QVBoxLayout(card)
        lay.setContentsMargins(SPACING["md"], SPACING["sm"], SPACING["md"],
                               SPACING["sm"])
        lay.setSpacing(SPACING["2xs"])
        cap = QLabel(caption)
        cap.setObjectName("MetricCaption")
        if value is None:
            text = "N/A"
        elif probability:
            text = probability_text(value)    # at least four places
        else:
            text = f"{_short(value)}{suffix}"
        val = QLabel(text)
        val.setObjectName("MetricValue")
        val.setTextInteractionFlags(
            Qt.TextInteractionFlag.TextSelectableByMouse)
        val.setAccessibleName(f"{caption}: {text}")
        extra = QLabel(sub)
        extra.setObjectName("MetricSub")
        card.setToolTip(tip if value is not None else
                        f"{tip}\n\nNot defined for this model at this time "
                        "(for example an infinite mean or a point mass).")
        for widget in (cap, val, extra):
            lay.addWidget(widget)
        return card

    def _fill_summary(self, result: dict[str, Any]) -> None:
        self._clear_cards()
        suffix = f" {result['units']}" if result.get("units") else ""
        # Rates are per one time unit: "per hour", not "per hours".
        unit = str(result.get("units") or "")
        one = unit[:-1] if unit.endswith("s") and len(unit) > 1 else unit
        per = f" per {one}" if one else ""
        cards: list[QFrame] = []
        if result["mode"] == "reliability":
            at = result["time"]
            cards.append(self._card(
                f"Reliability at {_short(at)}{suffix}",
                result.get("reliability"),
                "Probability that the system has not failed by the mission "
                "time.", sub=_percent(result.get("reliability")),
                probability=True))
            cards.append(self._card(
                "MTTF", result.get("mttf"),
                "Mean time to first system failure: the integral of R(t).",
                suffix=suffix))
            cards.append(self._card(
                "Hazard rate", result.get("hazard"),
                "System failure rate at the mission time: density divided by "
                "reliability.", sub=per.strip()))
            cards.append(self._card(
                "Failure density", result.get("pdf"),
                "System failure density f(t) at the mission time.",
                sub=per.strip()))
            for key, value in (result.get("bx") or {}).items():
                cards.append(self._card(
                    f"B{key} life", value,
                    f"Time by which {key} % of systems have failed.",
                    sub=f"{key}% of systems fail by this time",
                    suffix=suffix))
            conditional = result.get("conditional")
            if conditional:
                cards.append(self._card(
                    f"Next {_short(conditional['tau'])}{suffix} "
                    f"from {_short(conditional['age'])}",
                    conditional["reliability"],
                    "Conditional reliability of a further mission, given "
                    "survival to the mission time.",
                    sub=_percent(conditional["reliability"]),
                    probability=True))
        else:
            cards.append(self._card(
                "Availability", result.get("availability"),
                "Long-run probability that the system is up.",
                sub=_percent(result.get("availability")), probability=True))
            cards.append(self._card(
                "Unavailability", result.get("unavailability"),
                "Long-run probability that the system is down.",
                sub=_percent(result.get("unavailability"))))
        self._summary_page.set_cards(cards)

    @staticmethod
    def _element_row(comp: dict[str, Any], availability: bool,
                     measures: dict[str, Any] | None,
                     with_importance: bool) -> list[float | None]:
        """The numeric cells of one element's row, after its name."""
        values = [comp.get("availability" if availability else "reliability"),
                  comp.get("mttf")]
        if availability:
            values.append(comp.get("mttr"))
        if with_importance:
            keys = ("birnbaum", "raw", "rrw", "vesely_fussell", "criticality")
            values += [None if measures is None else measures.get(k)
                       for k in keys]
        return values

    def _fill_elements(self, result: dict[str, Any]) -> None:  # noqa: C901
        availability = result["mode"] == "availability"
        importance = {(row["kind"], row["id"]): row
                      for row in result.get("importance") or []}
        headers = ["Element", "Availability" if availability else
                   "Reliability", "MTTF"]
        if availability:
            headers.append("MTTR")
        if importance:
            headers += ["Birnbaum", "RAW", "RRW", "Fussell–Vesely",
                        "Criticality"]
        components = list(result.get("components") or [])
        treated = any(c.get("state") for c in components)
        if treated:
            headers.append("Treatment")
        if importance:
            order = {key: i for i, key in enumerate(importance)}
            components.sort(key=lambda row: order.get(
                (row["kind"], row["id"]), len(order)))
        table = self._elements
        table.setSortingEnabled(False)
        table.clear()
        table.setColumnCount(len(headers))
        table.setRowCount(len(components))
        table.setHorizontalHeaderLabels(headers)
        for column, header in enumerate(headers):
            head = table.horizontalHeaderItem(column)
            if head is not None and header in _MEASURE_HELP:
                head.setToolTip(_MEASURE_HELP[header])
        for row, comp in enumerate(components):
            name = QTableWidgetItem(comp["name"])
            name.setToolTip(f"{comp['kind'].capitalize()} {comp['id']}")
            table.setItem(row, 0, name)
            cells = self._element_row(
                comp, availability,
                importance.get((comp["kind"], comp["id"])), bool(importance))
            for offset, value in enumerate(cells, 1):
                table.setItem(row, offset, _NumItem(value, readable=True))
            if treated:
                table.setItem(row, len(headers) - 1,
                              QTableWidgetItem(comp.get("state") or ""))
        table.setSortingEnabled(True)
        if importance:
            table.sortItems(len(headers) - 1 - int(treated),
                            Qt.SortOrder.DescendingOrder)
        self._spread_columns(len(headers), treated)

    def _spread_columns(self, count: int, treated: bool) -> None:
        """Every column gets the width its header and numbers need (names
        capped at 360 px); room to spare goes to the measures, and a window
        too narrow for them all scrolls sideways rather than squeezing the
        numbers into ellipses. Redone when the table is resized."""
        table = self._elements
        header = table.horizontalHeader()
        header.setStretchLastSection(False)
        text_columns = {0} | ({count - 1} if treated else set())
        for column in range(count):
            header.setSectionResizeMode(
                column, QHeaderView.ResizeMode.Interactive)
            item = table.horizontalHeaderItem(column)
            if item is not None:
                item.setTextAlignment(
                    (Qt.AlignmentFlag.AlignLeft if column in text_columns
                     else Qt.AlignmentFlag.AlignRight)
                    | Qt.AlignmentFlag.AlignVCenter)
        table.resizeColumnsToContents()
        gap = 0     # the header and cell padding already space them
        self._natural_widths = [
            min(header.sectionSize(column) + gap, 360)
            if column in text_columns else header.sectionSize(column) + gap
            for column in range(count)]
        self._text_columns = text_columns
        self._share_spare_width()

    def _share_spare_width(self) -> None:
        widths = list(getattr(self, "_natural_widths", ()))
        table = self._elements
        if not widths or len(widths) != table.columnCount():
            return
        measures = [c for c in range(len(widths))
                    if c not in self._text_columns]
        spare = table.viewport().width() - sum(widths)
        if spare > 0 and measures:
            for column in measures:
                widths[column] += spare // len(measures)
        header = table.horizontalHeader()
        for column, width in enumerate(widths):
            header.resizeSection(column, width)

    def eventFilter(self, watched, event) -> bool:  # noqa: N802 (Qt API)
        table = getattr(self, "_elements", None)
        if table is not None and watched is table.viewport() \
                and event.type() == QEvent.Type.Resize:
            self._share_spare_width()
        return super().eventFilter(watched, event)

    def _fill_curve(self, result: dict[str, Any]) -> None:
        points = result.get("times") or []
        self._plot.set_points(
            [(p["time"], p["value"]) for p in points
             if p["value"] is not None], result.get("units", ""))
        table = self._curve_table
        table.setColumnCount(2)
        table.setHorizontalHeaderLabels(["Time", "Reliability"])
        table.setRowCount(len(points))
        for row, point in enumerate(points):
            table.setItem(row, 0, _NumItem(point["time"]))
            table.setItem(row, 1, _NumItem(point["value"]))

    def _fill_notes(self, result: dict[str, Any]) -> None:
        lines: list[str] = []
        warnings = result.get("warnings") or []
        if warnings:
            lines.append("Warnings")
            lines += [f"  • {w}" for w in warnings]
            lines.append("")
        lines.append("This result assumes")
        lines += [f"  • {a}" for a in result.get("assumptions") or []]
        self._notes.setPlainText("\n".join(lines))
        self._tabs.setTabText(
            self._tabs.indexOf(self._tab_notes),
            f"Assumptions ({len(warnings)} warning"
            f"{'s' if len(warnings) != 1 else ''})" if warnings
            else "Assumptions")

    def _set_actions(self, enabled: bool) -> None:
        self._copy.setEnabled(enabled)
        self._export.setEnabled(enabled)

    def export_both(self, path: str | None = None) -> str | None:
        """This result and the other mode with the same settings, in one
        workbook (the same as ``raptor analytic --mode both``). The other
        mode is worked out first if it has not been; the page keeps
        showing this result. Returns the path once written, or None if a
        solve was started (it is written when the solve finishes)."""
        shown = self._result
        if shown is None or self._busy:
            return None
        saved = self._results_to_save()
        other = ("availability" if shown["mode"] == "reliability"
                 else "reliability")
        try:
            options = self._values(other)
        except ValueError as exc:
            self._show_notice("Check the settings", str(exc))
            return None
        if not path:
            from raptor.api.analytical_export import export_file_name
            names = [{"mode": "reliability", "hierarchy": shown.get("hierarchy")},
                     {"mode": "availability", "hierarchy": shown.get("hierarchy")}]
            path = self._ask_path(export_file_name(self._model_name(), names))
            if not path:
                return None
        key = self._cache_key(options)
        cached = self._cache.get(key) if key is not None else None
        if cached is not None:
            return self._write_workbook(self._in_mode_order(saved, cached),
                                        path)
        self._export_pending = (path, saved)
        self._start_solve(options)
        self._banner.setText(f"Working out {other} for the workbook…")
        return None

    @staticmethod
    def _in_mode_order(shown: list[dict[str, Any]], other: dict[str, Any]
                       ) -> list[dict[str, Any]]:
        """Reliability first, then availability; each a result or every
        phase of an "All phases" set."""
        others = list(other.get("phases") or [other])
        return (shown + others if shown[0]["mode"] == "reliability"
                else others + shown)

    def _finish_export_both(self, result: Any, error: Any) -> None:
        path, shown = self._export_pending or ("", [])
        self._export_pending = None
        if error is not None:
            # The page still shows its own result; say why the workbook
            # was not written rather than replace that result.
            kind, message, *_size = error
            self._banner.setText("Workbook not written.")
            if kind != "cancelled":
                msgbox.warning(
                    self, "Export failed",
                    f"The other mode could not be worked out with these "
                    f"settings, so the workbook was not written:\n\n"
                    f"{message}")
            return
        self._keep(result)
        self._write_workbook(self._in_mode_order(shown, result), path)

    def _ask_path(self, name: str) -> str | None:
        from raptor.gui.ui.filedialogs import ask_spreadsheet_path
        start = os.path.dirname(
            str(getattr(self._win, "_current_file", "") or "")
        ) or os.getcwd()
        return ask_spreadsheet_path(self, "Export analytical results",
                                    os.path.join(start, name))

    def export_result(self, path: str | None = None) -> str | None:
        """Write the current result to a workbook (asks for a file if none)."""
        if self._result is None:
            return None
        return self._write_workbook(self._results_to_save(), path)

    def _write_workbook(self, results: list[dict[str, Any]],
                path: str | None) -> str | None:
        if not results:
            return None
        from raptor.api.analytical_export import (
            export_file_name, export_workbook)
        if not path:
            path = self._ask_path(export_file_name(self._model_name(),
                                                   results))
            if not path:
                return None
        try:
            path = str(export_workbook(results, path))
        except OSError as exc:
            msgbox.warning(self, "Export failed", str(exc))
            return None
        self._banner.setText(f"Saved {os.path.basename(path)}")
        return path

    def _copy_result(self) -> None:
        results = self._results_to_save()
        if results:
            QApplication.clipboard().setText(
                "\n\n".join(summary_text(r) for r in results))

    # -- lifecycle ------------------------------------------------------------

    def follow_run_scope(self, hierarchy_id: int) -> None:
        """Preselect the hierarchy the Run panel is scoped to, so Run, Step
        and the exact solver agree on what "this hierarchy" means. Whole
        model (0) leaves the dialog's own choice alone."""
        if not hierarchy_id or not getattr(self, "_has_hierarchies", False):
            return
        index = self._hierarchy.findData(hierarchy_id)
        if index >= 0 and index != self._hierarchy.currentIndex():
            self._hierarchy.setCurrentIndex(index)

    def refresh_for_model(self) -> None:
        """Start over when the window's model is a different one; for the
        same model keep what is shown (an edit since is flagged by the model
        watch, and Compute solves again)."""
        if not self._same_model():
            self._begin_for_model()
        else:
            self._check_model()


def open_analytic_dialog(win) -> AnalyticDialog:
    """One modeless dialog per window, like the other analysis dialogs."""
    dialog = getattr(win, "_analytic_dialog", None)
    if dialog is None:
        dialog = AnalyticDialog(win)
        win._analytic_dialog = dialog
    else:
        dialog.refresh_for_model()
    panel = getattr(win, "_run_panel", None)
    if panel is not None:
        dialog.follow_run_scope(panel.hierarchy_scope())
    dialog.show()
    dialog.raise_()
    dialog.activateWindow()
    return dialog
