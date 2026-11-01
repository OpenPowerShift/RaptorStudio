"""Results ▸ Analytical: exact static reliability/availability.

A thin front end over `raptor.api.analytical.analyse_rbd`, which also backs
the CLI and the MCP tool. It does not sample and it never changes the model.

The solve runs on a worker thread (a large diagram or a heavy-tailed
distribution can take seconds), so the window stays responsive. Results are
shown as headline figures, a sortable per-element table, a reliability curve,
the path-product equation and the assumptions the answer rests on.
"""
from __future__ import annotations

import copy
import os
import queue
import threading
import time
from typing import Any, cast

from PySide6.QtCore import QPointF, QRectF, Qt, QTimer
from PySide6.QtGui import QColor, QFontDatabase, QPainter, QPen, QPolygonF
from PySide6.QtWidgets import (
    QAbstractItemView, QApplication, QCheckBox, QComboBox, QDialog,
    QDoubleSpinBox, QFormLayout, QFrame, QGridLayout, QGroupBox, QHBoxLayout,
    QHeaderView, QLabel, QLineEdit, QPlainTextEdit, QProgressBar,
    QPushButton, QSizePolicy, QStackedWidget, QTableWidget,
    QTableWidgetItem, QTabWidget, QVBoxLayout, QWidget)

from raptor.api.analytical import analyse_rbd
from raptor.api.analytical_text import fmt as _fmt_full
from raptor.api.analytical_text import summary_text
from raptor.core.analytical.errors import AnalyticalError
from raptor.gui.resources.design_tokens import (
    RADIUS, SPACING, TYPE, ThemeManager, active_colour)

#: Curve points drawn when the user leaves "Curve at times" blank.
_AUTO_CURVE_POINTS = 41

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


def _percent(value: float | None) -> str:
    return "" if value is None else f"{100.0 * value:.4g} %"


class _TimeSpin(QDoubleSpinBox):
    """A time field that shows ``48`` rather than ``48.000000``."""

    def textFromValue(self, value: float) -> str:  # noqa: N802 (Qt API)
        return f"{value:.10g}"


class _NumItem(QTableWidgetItem):
    """A table cell that sorts by its number, with N/A always last."""

    def __init__(self, value: float | None, digits: int = 6) -> None:
        super().__init__(_short(value, digits))
        self._value = value
        self.setTextAlignment(
            Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        if value is not None:
            self.setToolTip(_fmt_full(value))

    def __lt__(self, other: QTableWidgetItem) -> bool:
        mine = self._value
        theirs = getattr(other, "_value", None)
        if mine is None or theirs is None:
            return mine is None and theirs is not None
        return mine < theirs


def _solve(token: int, rbd: Any, name: str, options: dict[str, Any],
           inbox: queue.Queue) -> None:
    """Worker-thread body: solve and post ``(token, result, error)``.

    The result goes through a plain thread-safe queue that the dialog polls,
    so no Qt object is ever touched (or destroyed) off the GUI thread, even
    if the dialog is closed while this is still running.
    """
    try:
        inbox.put((token, analyse_rbd(rbd, name, **options), None))
    except AnalyticalError as exc:
        inbox.put((token, None, ("refused", str(exc))))
    except ValueError as exc:
        inbox.put((token, None, ("invalid", str(exc))))
    except Exception as exc:  # noqa: BLE001 - worker boundary
        inbox.put((token, None, ("failed", f"{type(exc).__name__}: {exc}")))


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


class AnalyticDialog(QDialog):
    """Modeless; computes on demand and leaves the model untouched."""

    def __init__(self, win) -> None:
        super().__init__(win)
        self._win = win
        self._token = 0
        self._busy = False
        self._rerun = False     # settings changed while a solve was running
        self._shown_rbd_id: int | None = None
        self._result: dict[str, Any] | None = None
        self._inbox: queue.Queue = queue.Queue()
        self._poll = QTimer(self)
        self._poll.setInterval(30)
        self._poll.timeout.connect(self._drain)
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
        self._intro = QLabel(
            "Exact answers without sampling, for models inside the static "
            "subset. Reliability mode is non-repairable; availability mode "
            "is the steady state of independent, perfectly repaired "
            "components. Components are treated as independent.")
        self._intro.setObjectName("SectionNote")
        self._intro.setWordWrap(True)
        root.addWidget(self._intro)

        root.addWidget(self._build_settings())
        root.addLayout(self._build_actions())

        self._banner = QLabel("")
        self._banner.setObjectName("SectionNote")
        self._banner.setWordWrap(True)
        root.addWidget(self._banner)

        self._pages = QStackedWidget()
        self._pages.addWidget(self._build_message_page())
        self._pages.addWidget(self._build_results_page())
        root.addWidget(self._pages, 1)

        footer = QHBoxLayout()
        footer.addStretch(1)
        self._copy = QPushButton("Copy")
        self._copy.setToolTip("Copy the full result as plain text")
        self._copy.setAutoDefault(False)
        self._copy.setEnabled(False)
        self._copy.clicked.connect(self._copy_result)
        close = QPushButton("Close")
        close.setAutoDefault(False)
        close.clicked.connect(self.close)
        footer.addWidget(self._copy)
        footer.addWidget(close)
        root.addLayout(footer)

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
        self._ignore = QCheckBox("Ignore repairs")
        self._ignore.setToolTip(
            "Reliability mode only: compute the first-failure reliability "
            "of a model that has repair distributions.")
        for check, name in ((self._importance, "Importance measures"),
                            (self._equation, "Show the equation"),
                            (self._ignore, "Ignore repairs")):
            check.setAccessibleName(name)
            options.addWidget(check)
        options.addStretch(1)
        self._options_host = QWidget()
        self._options_host.setLayout(options)
        options.setContentsMargins(0, 0, 0, 0)
        form.addRow("Include:", self._options_host)

        self._form = form
        self._reliability_rows = (self._at, self._times, self._bx)
        self._mode.currentIndexChanged.connect(self._mode_changed)
        for signal in (self._at.valueChanged, self._times.textChanged,
                       self._bx.textChanged, self._importance.toggled,
                       self._equation.toggled, self._ignore.toggled):
            signal.connect(self._settings_changed)
        for line in (self._times, self._bx):
            line.returnPressed.connect(self.compute)
        return box

    def _build_actions(self) -> QHBoxLayout:
        row = QHBoxLayout()
        self._compute = QPushButton("Compute")
        self._compute.setDefault(True)
        self._compute.setToolTip("Solve with the current settings (Enter)")
        self._compute.clicked.connect(self.compute)
        self._progress = QProgressBar()
        self._progress.setRange(0, 0)
        self._progress.setTextVisible(False)
        self._progress.setFixedWidth(120)
        self._progress.setAccessibleName("Computing")
        self._progress.hide()
        row.addWidget(self._compute)
        row.addWidget(self._progress)
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
        lay.addStretch(2)
        return page

    def _build_results_page(self) -> QWidget:
        self._tabs = QTabWidget()
        self._tabs.setDocumentMode(True)

        self._summary_page = QWidget()
        self._cards = QGridLayout(self._summary_page)
        self._cards.setSpacing(SPACING["md"])
        self._cards.setContentsMargins(SPACING["md"], SPACING["md"],
                                       SPACING["md"], SPACING["md"])
        self._tab_summary = self._tabs.addTab(self._summary_page, "Summary")

        self._elements = QTableWidget(0, 0)
        self._elements.setAccessibleName("Per-element results")
        self._elements.setEditTriggers(
            QAbstractItemView.EditTrigger.NoEditTriggers)
        self._elements.setSelectionBehavior(
            QAbstractItemView.SelectionBehavior.SelectRows)
        self._elements.setAlternatingRowColors(True)
        self._elements.setSortingEnabled(True)
        self._elements.verticalHeader().setVisible(False)
        self._elements.horizontalHeader().setSectionResizeMode(
            QHeaderView.ResizeMode.ResizeToContents)
        self._elements.horizontalHeader().setStretchLastSection(True)
        self._tab_elements = self._tabs.addTab(self._elements, "Elements")

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
        self._tab_curve = self._tabs.addTab(curve, "Curve")

        self._equation_view = QPlainTextEdit()
        self._equation_view.setReadOnly(True)
        self._equation_view.setAccessibleName("System equation")
        self._equation_view.setFont(QFontDatabase.systemFont(
            QFontDatabase.SystemFont.FixedFont))
        self._tab_equation = self._tabs.addTab(self._equation_view,
                                               "Equation")

        self._notes = QPlainTextEdit()
        self._notes.setReadOnly(True)
        self._notes.setAccessibleName("Assumptions and warnings")
        self._tab_notes = self._tabs.addTab(self._notes, "Assumptions")
        return self._tabs

    # -- theme ----------------------------------------------------------------

    def _restyle(self) -> None:
        c = active_colour()
        self.setStyleSheet(
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
            f"font-size: {TYPE['caption']}px; background: transparent; }}")
        self._plot.update()

    def _on_theme_changed(self, _dark: bool) -> None:
        self._restyle()

    # -- state ----------------------------------------------------------------

    def _begin_for_model(self) -> None:
        """Start from the open model: its run length, then solve if we can."""
        rbd = self._win._rbd
        self._shown_rbd_id = id(rbd)
        horizon = _run_length(rbd)
        self._at.blockSignals(True)
        self._at.setValue(horizon if horizon is not None else 0.0)
        self._at.blockSignals(False)
        self._set_title(rbd)
        self._mode_changed(solve=False)
        if horizon is None and self._mode.currentData() == "reliability":
            self._show_notice(
                "Enter a mission time",
                "This model is not time-terminated, so it has no default "
                "mission time. Enter the time to evaluate, then press "
                "Compute.")
            self._at.setFocus()
        else:
            self.compute()

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
        if solve and self.isVisible():
            self.compute()

    def _settings_changed(self, *_args) -> None:
        if self._result is not None and not self._busy:
            self._banner.setText(
                "Settings changed — press Compute to update the results.")

    def _values(self) -> dict[str, Any]:
        reliability = self._mode.currentData() == "reliability"
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
            "mode": self._mode.currentData(),
            "at": at,
            "times": times or None,
            "bx": bx,
            "importance": self._importance.isChecked(),
            "equation": self._equation.isChecked(),
            "ignore_repairs": self._ignore.isChecked() and reliability,
        }

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
        self._token += 1
        self._set_busy(True)
        threading.Thread(
            target=_solve, daemon=True,
            args=(self._token, copy.deepcopy(self._win._rbd),
                  self._model_name(), options, self._inbox)).start()

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
        self._progress.setVisible(busy)
        if busy:
            self._banner.setText("Computing…")
            self._poll.start()
        else:
            self._poll.stop()

    def _drain(self) -> None:
        """Deliver finished solves (runs on the GUI thread)."""
        while True:
            try:
                token, result, error = self._inbox.get_nowait()
            except queue.Empty:
                return
            self._on_done(token, result, error)

    def _on_done(self, token: int, result: Any, error: Any) -> None:
        if token != self._token:
            return          # a superseded solve
        self._set_busy(False)
        if self._rerun:
            self._rerun = False
            self.compute()
            return
        if error is not None:
            self._result = None
            self._copy.setEnabled(False)
            kind, message = error
            titles = {
                "refused": "This model is outside the static solver",
                "invalid": "Check the settings",
                "failed": "The analysis could not be completed",
            }
            repairs = ("\n\nIf you only want the first-failure reliability, "
                       "tick “Ignore repairs”."
                       if "repair distribution" in message else "")
            hint = {
                "refused": "\n\nSimulation handles repairs, preventive "
                           "maintenance, spares, resources, dependencies, "
                           "standby and phases." + repairs,
                "invalid": "",
                "failed": "\n\nThe model was not changed.",
            }[kind]
            self._show_notice(titles[kind],
                              message[:1].upper() + message[1:] + hint)
            return
        self._result = result
        self._show_result(result)

    # -- display --------------------------------------------------------------

    def _show_notice(self, title: str, body: str) -> None:
        self._notice_title.setText(title)
        self._notice_body.setText(body)
        self._pages.setCurrentIndex(0)
        self._banner.setText("")
        self._copy.setEnabled(False)

    def _show_result(self, result: dict[str, Any]) -> None:
        self._fill_summary(result)
        self._fill_elements(result)
        self._fill_curve(result)
        equation = result.get("equation")
        self._equation_view.setPlainText(equation or "")
        self._fill_notes(result)
        self._tabs.setTabVisible(self._tab_curve, bool(result.get("times")))
        self._tabs.setTabVisible(self._tab_equation, bool(equation))
        self._pages.setCurrentIndex(1)
        if self._tabs.currentIndex() == -1 or not self._tabs.isTabVisible(
                self._tabs.currentIndex()):
            self._tabs.setCurrentIndex(self._tab_summary)
        self._copy.setEnabled(True)
        warnings = len(result.get("warnings") or [])
        note = (f"  {warnings} warning(s) on the Assumptions tab."
                if warnings else "")
        self._banner.setText(
            f"Exact static result from a {result['bdd_nodes']:,}-node "
            f"diagram. The model was not sampled or changed.{note}")

    def _clear_cards(self) -> None:
        while self._cards.count():
            item = self._cards.takeAt(0)
            widget = item.widget() if item is not None else None
            if widget is not None:
                widget.deleteLater()

    def _card(self, caption: str, value: float | None, tip: str,
              *, sub: str = "", suffix: str = "") -> QFrame:
        card = QFrame()
        card.setObjectName("MetricCard")
        lay = QVBoxLayout(card)
        lay.setContentsMargins(SPACING["md"], SPACING["sm"], SPACING["md"],
                               SPACING["sm"])
        lay.setSpacing(SPACING["2xs"])
        cap = QLabel(caption)
        cap.setObjectName("MetricCaption")
        text = "N/A" if value is None else f"{_short(value)}{suffix}"
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
        cards: list[QFrame] = []
        if result["mode"] == "reliability":
            at = result["time"]
            cards.append(self._card(
                f"Reliability at {_short(at)}{suffix}",
                result.get("reliability"),
                "Probability that the system has not failed by the mission "
                "time.", sub=_percent(result.get("reliability"))))
            cards.append(self._card(
                "MTTF", result.get("mttf"),
                "Mean time to first system failure: the integral of R(t).",
                suffix=suffix))
            cards.append(self._card(
                "Hazard rate", result.get("hazard"),
                "System failure rate at the mission time: density divided by "
                "reliability."))
            cards.append(self._card(
                "Failure density", result.get("pdf"),
                "System failure density f(t) at the mission time."))
            for key, value in (result.get("bx") or {}).items():
                cards.append(self._card(
                    f"B{key} life", value,
                    f"Time by which {key} % of systems have failed.",
                    suffix=suffix))
            conditional = result.get("conditional")
            if conditional:
                cards.append(self._card(
                    f"Next {_short(conditional['tau'])}{suffix} "
                    f"from {_short(conditional['age'])}",
                    conditional["reliability"],
                    "Conditional reliability of a further mission, given "
                    "survival to the mission time.",
                    sub=_percent(conditional["reliability"])))
        else:
            cards.append(self._card(
                "Availability", result.get("availability"),
                "Long-run probability that the system is up.",
                sub=_percent(result.get("availability"))))
            cards.append(self._card(
                "Unavailability", result.get("unavailability"),
                "Long-run probability that the system is down.",
                sub=_percent(result.get("unavailability"))))
        for index, card in enumerate(cards):
            self._cards.addWidget(card, index // 3, index % 3)
        self._cards.setRowStretch(len(cards) // 3 + 1, 1)
        for column in range(3):
            self._cards.setColumnStretch(column, 1)

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

    def _fill_elements(self, result: dict[str, Any]) -> None:
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
                table.setItem(row, offset, _NumItem(value))
        table.setSortingEnabled(True)
        if importance:
            table.sortItems(len(headers) - 1, Qt.SortOrder.DescendingOrder)

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
            self._tab_notes,
            f"Assumptions ({len(warnings)} warning"
            f"{'s' if len(warnings) != 1 else ''})" if warnings
            else "Assumptions")

    def _copy_result(self) -> None:
        if self._result is not None:
            QApplication.clipboard().setText(summary_text(self._result))

    # -- lifecycle ------------------------------------------------------------

    def refresh_for_model(self) -> None:
        """Re-solve when the window's model is a different one."""
        if id(self._win._rbd) != self._shown_rbd_id:
            self._begin_for_model()
        else:
            self.compute()


def open_analytic_dialog(win) -> AnalyticDialog:
    """One modeless dialog per window, like the other analysis dialogs."""
    dialog = getattr(win, "_analytic_dialog", None)
    if dialog is None:
        dialog = AnalyticDialog(win)
        win._analytic_dialog = dialog
    else:
        dialog.refresh_for_model()
    dialog.show()
    dialog.raise_()
    dialog.activateWindow()
    return dialog
