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

import copy
import itertools
import logging
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from typing import Any

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QAction, QActionGroup, QColor, QFont, QKeySequence, QShortcut
from PySide6.QtWidgets import (
    QComboBox, QFrame, QHBoxLayout, QLabel, QLineEdit, QMenu, QPushButton,
    QSpinBox, QSplitter, QStackedWidget, QToolButton, QTreeWidget,
    QTreeWidgetItem, QVBoxLayout, QWidget,
)

from raptor.gui import i18n
from raptor.api import run_needs
from raptor.api.insight import charts, hierarchy as hier, timecharts
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
    needs: tuple[str, ...] = ()  # run options the button turns on (run_needs keys)


#: Groups in the order the list shows them.
GROUPS = ("The answer", "By hierarchy", "Over time", "Why it goes down",
          "Lost capacity", "Year by year", "Cost", "What matters most")

#: The smallest height the chart is drawn at; a shorter page scrolls.
CHART_MIN_HEIGHT = 340

#: The charts that draw a target line, where the Target button is shown.
TARGET_CHARTS = ("trial-outcome", "ecdf", "convergence", "availability-through-run",
                 "interval-availability", "availability-band", "availability-ladder",
                 "downtime-waterfall", "annual-fan", "annual-dots")

_RECORD_AO = ("These charts are drawn from the availability plot. "
              + run_needs.gui_hint("availability_plot"))
_RECORD_CAP = ("These charts are drawn from the capacity plot. "
               + run_needs.gui_hint("capacity_plot"))


class ChartsPage(QWidget):
    """The chart list, the chart, and the controls the chart uses."""

    locate_requested = Signal(str, str)
    #: the explicit comparison changed: hierarchy ids in series order
    #: (empty is the automatic top four); the canvas rings follow it
    compare_changed = Signal(list)
    #: the reader asked for the downtime charts before the drivers were worked out
    drivers_requested = Signal()
    #: the reader asked to switch these run options on (run_needs keys) and run
    enable_requested = Signal(list)
    #: the reader changed the bottom of the availability axis (saved with the model)
    y_floor_changed = Signal(float)
    #: the target line, a fraction, or None when cleared
    target_changed = Signal(object)
    #: the capacity-weighted availability target, a fraction or None
    capacity_target_changed = Signal(object)
    #: "availability" or "capacity": save the recorded plot rows (Raptor's rpt_* file)
    raw_export_requested = Signal(str)

    def __init__(self, parent: QWidget | None = None) -> None:  # noqa: PLR0915
        super().__init__(parent)
        self._entries: dict[str, _Entry] = {}
        #: built charts by title (what the report and the old picker used)
        self._specs: dict[str, charts.ChartSpec] = {}
        self._build_errors: dict[str, str] = {}
        self._time_cache: dict[tuple[str, int, float], charts.ChartSpec] = {}
        self._inputs: dict[str, Any] = {}
        self._ao_runs: list[dict[str, Any]] = []
        self._cap_runs: list[dict[str, Any]] = []
        self._cost_runs: list[dict[str, Any]] = []
        self._y_floor = 0.0
        self._wanted = "Run-to-run outcome"
        #: hierarchy id/name for the scope chip, or None for the system
        self._scope: int | str | None = None
        #: follow the canvas level unless the user picked a scope by hand
        self._follow_canvas = True
        #: the canvas level, remembered even while Follow canvas is off
        self._canvas_hierarchy = 0
        #: hierarchy level the ranking and ribbon charts show (0 = all)
        self._depth = 0
        #: hierarchy ids picked for the overlay; empty means the top four
        self._selected: list[int] = []
        #: a previous run's hierarchy rows, kept across a re-run
        self._baseline_rows: list[dict[str, Any]] | None = None
        self._baseline_label: str | None = None
        #: target for capacity-weighted availability (fraction or None)
        self._capacity_target: float | None = None
        #: group expansion kept while the search box filters the list
        self._search_folded: dict[str, bool] | None = None
        #: how the capacity charts' y axis is framed: auto, full, goal,
        #: probability, exceedance or numeric (see `_capacity_axis_menu`)
        self._capacity_axis_mode = "auto"
        self._capacity_axis_value: float | None = None
        self._capacity_axis_range: tuple[float, float] | None = None

        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        split = QSplitter(Qt.Orientation.Horizontal)
        split.setChildrenCollapsible(True)
        lay.addWidget(split)

        left = QWidget()
        left_lay = QVBoxLayout(left)
        left_lay.setContentsMargins(0, 0, 0, 0)
        left_lay.setSpacing(SPACING.get("xs", 4))
        self.search = QLineEdit()
        self.search.setObjectName("ChartSearch")
        self.search.setPlaceholderText("Search charts…")
        self.search.setClearButtonEnabled(True)
        self.search.setAccessibleName("Search charts")
        self.search.setToolTip("Filter the chart list by title or group (Esc clears)")
        self.search.textChanged.connect(self._filter_charts)
        escape = QShortcut(QKeySequence("Escape"), self.search)
        escape.activated.connect(self.search.clear)
        left_lay.addWidget(self.search)
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
        left_lay.addWidget(self.nav, 1)
        split.addWidget(left)

        right = QWidget()
        rlay = QVBoxLayout(right)
        rlay.setContentsMargins(SPACING["md"], SPACING["sm"], SPACING["md"], SPACING["sm"])
        rlay.setSpacing(SPACING["sm"])
        rlay.addLayout(self._build_bar())
        self.chip_row = QWidget()
        self.chip_row.setObjectName("CompareChips")
        self._chip_lay = QHBoxLayout(self.chip_row)
        self._chip_lay.setContentsMargins(0, 0, 0, 0)
        self._chip_lay.setSpacing(SPACING["sm"])
        self.chip_row.hide()
        rlay.addWidget(self.chip_row)
        self.stack = QStackedWidget()
        self.chart = ChartWidget()
        self.chart.locate_requested.connect(self.locate_requested)
        # a short dock (1280 x 720) squashed the plot to about 100 px with
        # its legend taking a third: keep a readable height and scroll
        self.chart_scroll = self._scrolling(self.chart)
        self.stack.addWidget(self.chart_scroll)
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
        from raptor.gui.resources.design_tokens import ThemeManager
        ThemeManager.instance().on_changed(self._on_theme_changed)
        self._sync_controls(None)
        self._update_scope_menu()
        self._update_compare_menu()
        self._update_depth_menu()

    @staticmethod
    def _scrolling(chart: QWidget) -> QWidget:
        """The chart at a readable height, in a scroll area."""
        from PySide6.QtWidgets import QFrame, QScrollArea
        chart.setMinimumHeight(CHART_MIN_HEIGHT)
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        scroll.setWidget(chart)
        return scroll

    def _build_bar(self) -> QHBoxLayout:  # noqa: PLR0915
        """Run picker, axis floor and Export: shown only where a chart uses them."""
        bar = QHBoxLayout()
        bar.setSpacing(SPACING["sm"])
        self.scope_button = QToolButton()
        self.scope_button.setText("Scope: System")
        self.scope_button.setPopupMode(QToolButton.ToolButtonPopupMode.InstantPopup)
        self.scope_button.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonTextOnly)
        self.scope_button.setToolTip(
            "Show the charts for the whole system or for one hierarchy. "
            "It follows the canvas unless you pick a scope here.")
        self.scope_menu = QMenu(self.scope_button)
        self.scope_button.setMenu(self.scope_menu)
        bar.addWidget(self.scope_button)
        self.follow_button = QToolButton()
        self.follow_button.setText("Follow canvas")
        self.follow_button.setCheckable(True)
        self.follow_button.setChecked(True)
        self.follow_button.setToolTip(
            "When on, drilling into a hierarchy on the canvas changes the "
            "chart scope too")
        self.follow_button.toggled.connect(self._on_follow_toggled)
        bar.addWidget(self.follow_button)
        self.depth_box = QComboBox()
        self.depth_box.setAccessibleName("Hierarchy depth")
        self.depth_box.setToolTip(
            "Depth: ranking charts show one level; the treemap and sunburst "
            "grow this many levels.  All levels shows the whole tree.")
        self.depth_box.currentIndexChanged.connect(self._on_depth_changed)
        bar.addWidget(self.depth_box)
        self.layout_box = QComboBox()
        self.layout_box.setAccessibleName("Nested hierarchy layout")
        self.layout_box.setToolTip(
            "How the nested hierarchy is drawn: a treemap favours labels and "
            "exact areas; a sunburst favours depth and structure")
        self.layout_box.addItem("Treemap", "treemap")
        self.layout_box.addItem("Sunburst", "sunburst")
        self.layout_box.currentIndexChanged.connect(
            lambda _i: self._show_current())
        bar.addWidget(self.layout_box)
        self.sankey_box = QComboBox()
        self.sankey_box.setAccessibleName("Lost capacity Sankey grouping")
        self.sankey_box.setToolTip(
            "Group the lost flow by hierarchy and cause, or go straight "
            "from the lost flow to the causes")
        self.sankey_box.addItem("Hierarchy → cause", True)
        self.sankey_box.addItem("Cause only", False)
        self.sankey_box.currentIndexChanged.connect(
            lambda _i: self._show_current())
        bar.addWidget(self.sankey_box)
        self.sankey_delivered = QToolButton()
        self.sankey_delivered.setText("Delivered")
        self.sankey_delivered.setCheckable(True)
        self.sankey_delivered.setChecked(True)
        self.sankey_delivered.setToolTip(
            "Show the delivered branch. Turn it off when the loss is a small "
            "share of the generated flow, so the lost flow fills the figure")
        self.sankey_delivered.toggled.connect(lambda _on: self._show_current())
        bar.addWidget(self.sankey_delivered)
        self.compare_button = QToolButton()
        self.compare_button.setAccessibleName("Compare hierarchies")
        self.compare_button.setText("Compare: top 4")
        self.compare_button.setPopupMode(QToolButton.ToolButtonPopupMode.InstantPopup)
        self.compare_button.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonTextOnly)
        self.compare_button.setToolTip(
            "Pick two to four hierarchies for the overlay. With none picked, "
            "the four with the most downtime are compared.")
        self.compare_menu = QMenu(self.compare_button)
        self.compare_button.setMenu(self.compare_menu)
        bar.addWidget(self.compare_button)
        self.baseline_button = QToolButton()
        self.baseline_button.setAccessibleName("Hierarchy baseline")
        self.baseline_button.setText("Baseline: none")
        self.baseline_button.setPopupMode(QToolButton.ToolButtonPopupMode.InstantPopup)
        self.baseline_button.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonTextOnly)
        self.baseline_button.setToolTip(
            "Keep this run's hierarchy table as the baseline, then run again "
            "to see the change by hierarchy")
        self.baseline_menu = QMenu(self.baseline_button)
        self.set_baseline_action = self.baseline_menu.addAction(
            "Set current as baseline", self._set_baseline)
        self.clear_baseline_action = self.baseline_menu.addAction(
            "Clear baseline", self._clear_baseline)
        self.clear_baseline_action.setEnabled(False)
        self.baseline_button.setMenu(self.baseline_menu)
        bar.addWidget(self.baseline_button)
        self.run_label = QLabel(i18n.tr("Run", "Results"))
        self.run_box = QSpinBox()
        self.run_box.setMinimum(1)
        self.run_box.setAccelerated(True)
        self.run_box.setToolTip("The run this chart shows (arrow keys step through them)")
        self.run_box.valueChanged.connect(lambda _v: self._show_current())
        self.run_of = QLabel("")
        self.run_of.setObjectName("SectionNote")
        self.floor_button = QPushButton("Axis from: auto")
        self.floor_button.setToolTip(
            "Where the availability axis starts (0 to 99.9999 %). Auto fits the "
            "axis to the data; a value above 0 is a fixed floor used by every "
            "availability chart over time. Saved with the model.")
        self.floor_button.clicked.connect(self._ask_floor)
        self.target_button = self._target_control()
        self.capacity_target_button = QPushButton("Capacity target: none")
        self.capacity_target_button.setToolTip(
            "A target capacity-weighted availability drawn on the year-by-year "
            "capacity chart. Saved in the model file; the simulation does not "
            "change.")
        self.capacity_target_button.clicked.connect(self._ask_capacity_target)
        self.capacity_axis_button = self._capacity_axis_control()
        self.fold_box = QComboBox()
        # the same presets `run_chart(fold=...)` and `--chart-fold` take
        for text, mode in charts.FOLD_PRESETS.items():
            self.fold_box.addItem(text, mode)
        self.fold_box.setToolTip(
            "Gather the tail into one \"N others\" row or cell: keep the top "
            "10 or 20, keep a quartile, or take the smallest items until they "
            "are the last 5 % of the total. On screen and in what you save; "
            "the caption names the folded items")
        self.fold_box.currentIndexChanged.connect(lambda _i: self._show_current())
        for widget in (self.run_label, self.run_box, self.run_of):
            bar.addWidget(widget)
        bar.addStretch(1)
        bar.addWidget(self.fold_box)
        bar.addWidget(self.target_button)
        bar.addWidget(self.capacity_target_button)
        bar.addWidget(self.capacity_axis_button)
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
        self.missing_text.setTextFormat(Qt.TextFormat.RichText)
        self.missing_text.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.missing_action = QPushButton("")
        self.missing_action.clicked.connect(self._on_missing_action)
        elay.addWidget(self.missing_title)
        elay.addWidget(self.missing_text)
        row = QHBoxLayout()
        row.addStretch(1)
        row.addWidget(self.missing_action)
        row.addStretch(1)
        elay.addLayout(row)
        elay.addStretch(2)
        return empty

    # -- hierarchy scope -------------------------------------------------

    def _scope_row(self) -> dict[str, Any] | None:
        attribution = self._inputs.get("attribution")
        if self._scope in (None, "", 0, "0"):
            return None
        try:
            return hier.resolve(attribution, self._scope)
        except ValueError:
            return None

    def _scope_meta(self) -> dict[str, Any]:
        attribution = self._inputs.get("attribution")
        return hier.scope_meta(attribution, self._scope_row())

    def _scope_text(self) -> str:
        row = self._scope_row()
        if row is None:
            return "Scope: System"
        return "Scope: " + " > ".join(str(part) for part in row.get("path") or [row["name"]])

    def _update_scope_menu(self) -> None:
        self.scope_menu.clear()
        attribution = self._inputs.get("attribution")
        system = self.scope_menu.addAction("System")
        system.setCheckable(True)
        system.setChecked(self._scope_row() is None)
        system.triggered.connect(lambda _c=False: self.set_scope(None, follow=False))
        for row in hier.rows(attribution):
            text = "  " * max(0, int(row.get("depth") or 1) - 1) + str(row["name"])
            action = self.scope_menu.addAction(text)
            action.setCheckable(True)
            action.setChecked((self._scope_row() or {}).get("id") == row.get("id"))
            ref = int(row.get("id") or 0)
            action.triggered.connect(
                lambda _c=False, ref=ref: self.set_scope(ref, follow=False))
        self.scope_button.setText(self._scope_text())

    def _update_compare_menu(self) -> None:
        self.compare_menu.clear()
        attribution = self._inputs.get("attribution")
        top = self.compare_menu.addAction("Top four by downtime")
        top.setCheckable(True)
        top.setChecked(not self._selected)
        top.triggered.connect(lambda _c=False: self.set_compare([]))
        self.compare_menu.addSeparator()
        for row in hier.rows(attribution):
            indent = "  " * max(0, int(row.get("depth") or 1) - 1)
            action = self.compare_menu.addAction(indent + str(row["name"]))
            action.setCheckable(True)
            action.setChecked(int(row.get("id") or 0) in self._selected)
            ref = int(row.get("id") or 0)
            action.triggered.connect(
                lambda checked=False, ref=ref: self._toggle_compare(ref, checked))
        count = len(self._selected)
        self.compare_button.setText(
            "Compare: top 4" if not count else
            f"Compare: {count} selected")
        self._update_chips()

    def _update_chips(self) -> None:
        """One chip per explicitly compared hierarchy: swatch, name, close."""
        while self._chip_lay.count():
            item = self._chip_lay.takeAt(0)
            widget = item.widget() if item is not None else None
            if widget is not None:
                widget.setParent(None)
                widget.deleteLater()
        names = {int(row.get("id") or 0): str(row.get("name") or "")
                 for row in hier.rows(self._inputs.get("attribution"))}
        shown = [ref for ref in self._selected if ref in names]
        for index, ref in enumerate(shown):
            self._chip_lay.addWidget(self._make_chip(
                ref, names[ref], hier.SERIES_COLOURS[
                    index % len(hier.SERIES_COLOURS)]))
        self._chip_lay.addStretch(1)
        self.chip_row.setVisible(bool(shown))
        self._style_chips()

    def _make_chip(self, ref: int, name: str, colour: str) -> QFrame:
        chip = QFrame()
        chip.setObjectName("CompareChip")
        chip.setProperty("chipColour", colour)
        lay = QHBoxLayout(chip)
        lay.setContentsMargins(SPACING["sm"], 2, 2, 2)
        lay.setSpacing(SPACING["xs"])
        swatch = QLabel()
        swatch.setObjectName("CompareChipSwatch")
        swatch.setFixedSize(12, 12)
        swatch.setStyleSheet(
            f"background: {colour}; border-radius: 6px; border: none;")
        lay.addWidget(swatch)
        label = QLabel(name)
        label.setObjectName("CompareChipName")
        lay.addWidget(label)
        close = QToolButton()
        close.setObjectName("CompareChipClose")
        close.setText("\u00d7")
        close.setAutoRaise(True)
        close.setCursor(Qt.CursorShape.PointingHandCursor)
        close.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        remove = i18n.tr("Remove {name} from comparison", "Results").format(
            name=name)
        close.setAccessibleName(remove)
        close.setToolTip(remove)
        close.clicked.connect(lambda _c=False, ref=ref: self._remove_compare(ref))
        lay.addWidget(close)
        return chip

    def _remove_compare(self, ref: int) -> None:
        self.set_compare([r for r in self._selected if r != ref])

    def _style_chips(self) -> None:
        """Chip colours from the active theme tokens (re-run on a switch)."""
        c = active_colour()
        self.chip_row.setStyleSheet(
            f"QFrame#CompareChip {{ background: {c['group_header']};"
            f" border: 1px solid {c['border']}; border-radius: 10px; }}"
            f"QLabel#CompareChipName {{ color: {c['text']}; background: transparent; }}"
            f"QToolButton#CompareChipClose {{ color: {c['muted']};"
            f" border: none; border-radius: 8px; padding: 0 4px;"
            f" background: transparent; }}"
            f"QToolButton#CompareChipClose:hover {{ color: {c['text']};"
            f" background: {c['border']}; }}"
            f"QToolButton#CompareChipClose:focus {{ border: 2px solid"
            f" {c['accent']}; }}")

    def _toggle_compare(self, ref: int, checked: bool) -> None:
        chosen = list(self._selected)
        if checked and ref not in chosen:
            if len(chosen) >= 4:
                # The comparison charts take four branches; refuse the fifth
                # rather than leave a selection that cannot be drawn.
                self._update_compare_menu()
                return
            chosen.append(ref)
        elif not checked and ref in chosen:
            chosen.remove(ref)
        self.set_compare(chosen)

    def _on_depth_changed(self, _index: int) -> None:
        self._depth = int(self.depth_box.currentData() or 0)
        self._rebuild()

    def _update_depth_menu(self) -> None:
        if not hasattr(self, "depth_box"):
            return
        depth = hier.max_depth(self._inputs.get("attribution"))
        self.depth_box.blockSignals(True)
        self.depth_box.clear()
        self.depth_box.addItem("All levels", 0)
        for level in range(1, max(1, depth) + 1):
            label = "Top level" if level == 1 else f"Level {level}"
            self.depth_box.addItem(label, level)
        index = self.depth_box.findData(self._depth)
        self.depth_box.setCurrentIndex(max(0, index))
        self._depth = int(self.depth_box.currentData() or 0)
        self.depth_box.blockSignals(False)

    def _on_theme_changed(self, _dark: bool) -> None:
        """The list's muted text and the painted chart follow the theme."""
        self._style_chips()
        self._time_cache.clear()
        self._rebuild()

    def _fallback_after_scope(self) -> None:
        """A system-only chart becomes unavailable on drilling in; move to a
        ranking that can answer the same question inside the scope."""
        key = self.selected_key()
        entry = self._entries.get(key or "")
        if (key == "hierarchy-scorecard" and entry is not None
                and entry.missing and self._scope_row() is not None):
            self.select("Hierarchy Pareto")

    def _depth_value(self) -> int:
        return int(self._depth or 0)

    def _ranking_depth(self) -> int:
        """0 is All levels for nested charts; rankings use the top level."""
        return int(self._depth or 1)

    def _compare_refs(self) -> list[int] | None:
        """The comparison set: explicit picks, or the scope's children.

        Without a scope this is None (the charts choose the top four); with a
        scope and no explicit picks the comparison stays inside it, so a
        scoped overlay cannot silently show a sibling.
        """
        if self._selected:
            return list(self._selected)
        row = self._scope_row()
        if row is None:
            return None
        prefix = hier.path_text(row) + " > "
        children = [item for item in hier.rows(self._inputs.get("attribution"))
                    if item is not row and hier.path_text(item).startswith(prefix)
                    and float((item.get("dtci") or {}).get("mean") or 0.0) > 0]
        children.sort(key=lambda item: -(float((item.get("dtci") or {})
                                               .get("mean") or 0.0)))
        return [int(item.get("id") or 0) for item in children[:4]] or [
            int(row.get("id") or 0)]

    def _hierarchy_map_spec(self) -> charts.ChartSpec:
        """The one nested view, drawn as a treemap or a sunburst."""
        attribution = self._inputs.get("attribution") or {}
        nested_depth = None if not self._depth else self._depth
        if self.layout_box.currentData() == "sunburst":
            return charts.hierarchy_sunburst(
                attribution, self._scope, depth=nested_depth)
        return charts.hierarchy_treemap(
            attribution, self._scope, depth=nested_depth)

    def _capacity_sankey_spec(self) -> charts.ChartSpec:
        """The lost-capacity Sankey with the grouping controls applied."""
        attribution = self._inputs.get("attribution") or {}
        by_hierarchy = bool(self.sankey_box.currentData())
        return charts.capacity_sankey(
            attribution, self._scope,
            None if self._scope is not None else (self._selected or None),
            by_hierarchy=by_hierarchy,
            show_delivered=self.sankey_delivered.isChecked())

    def _on_follow_toggled(self, on: bool) -> None:
        self._follow_canvas = bool(on)
        if on:
            self._scope = self._canvas_hierarchy or None
            self._update_scope_menu()
            self._time_cache.clear()
            self._rebuild()

    def follow_hierarchy(self, hid: int | None) -> None:
        """The canvas level changed: use it when Follow canvas is on."""
        self._canvas_hierarchy = int(hid or 0)
        if not self._follow_canvas:
            return
        ref = int(hid or 0) or None
        if ref == self._scope:
            return
        self._scope = ref
        self._update_scope_menu()
        self._time_cache.clear()
        self._rebuild()
        self._fallback_after_scope()

    def set_scope(self, scope: int | str | None, *, follow: bool | None = None) -> None:
        """Set the chart scope; `follow=False` means a deliberate choice."""
        if follow is not None:
            self._follow_canvas = bool(follow)
            self.follow_button.blockSignals(True)
            self.follow_button.setChecked(self._follow_canvas)
            self.follow_button.blockSignals(False)
        self._scope = scope if scope not in (0, "0", "") else None
        self._update_scope_menu()
        self._time_cache.clear()
        self._rebuild()
        self._fallback_after_scope()

    def set_compare(self, refs: Sequence[int | str]) -> None:
        chosen = []
        for ref in refs:
            try:
                chosen.append(int(hier.resolve(self._inputs.get("attribution"), ref)
                                  .get("id") or 0))
            except ValueError:
                continue
        before = list(self._selected)
        self._selected = list(dict.fromkeys(chosen))[:4]
        self._update_compare_menu()
        self._rebuild()
        if before != self._selected:
            self.compare_changed.emit(list(self._selected))

    def selected_hierarchies(self) -> list[int]:
        return list(self._selected)

    def _set_baseline(self) -> None:
        attribution = self._inputs.get("attribution") or {}
        if not attribution.get("hierarchies"):
            return
        self._baseline_rows = list(attribution["hierarchies"])
        run = self._inputs.get("run_id")
        self._baseline_label = f"run {run}" if run else "this run"
        self.baseline_button.setText("Baseline: set")
        self.clear_baseline_action.setEnabled(True)
        self._rebuild()

    def _clear_baseline(self) -> None:
        self._baseline_rows = None
        self._baseline_label = None
        self.baseline_button.setText("Baseline: none")
        self.clear_baseline_action.setEnabled(False)
        self._rebuild()

    def set_baseline(self, rows: list[dict[str, Any]] | None) -> None:
        self._baseline_rows = list(rows) if rows else None
        self.baseline_button.setText("Baseline: set" if rows else "Baseline: none")
        self.clear_baseline_action.setEnabled(bool(rows))

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
                   generated: float | None = None,
                   hierarchy: int | None = None,
                   baseline: list[dict[str, Any]] | None = None,
                   model_hierarchies: bool | None = None,
                   model_phases: bool | None = None,
                   currency: str | None = None) -> None:
        """Rebuild the list from whatever this result set has, money in
        `currency` (the model's; "$" when not given).

        `drivers` says where the downtime attribution stands: ``ready``
        (given here, or none possible yet), ``pending`` (it can be worked
        out on request) or ``unavailable`` (the run kept no event log).
        `measure` is the per-run field the convergence chart follows."""
        self._inputs = {"per_run": per_run or [], "attribution": attribution,
                        "importance": importance, "report_data": report_data or [],
                        "periods": periods, "units": units, "run_id": run_id,
                        "target": target, "drivers": drivers, "measure": measure,
                        "generated": generated,
                        "model_hierarchies": model_hierarchies,
                        "model_phases": model_phases,
                        "currency": currency}
        if self._follow_canvas:
            self._scope = int(hierarchy) if hierarchy else None
        if self._scope is not None and self._scope_row() is None:
            self._scope = None
        valid_ids = {int(row.get("id") or 0)
                     for row in ((attribution or {}).get("hierarchies") or [])}
        before_selected = list(self._selected)
        self._selected = [hid for hid in self._selected if hid in valid_ids]
        if before_selected != self._selected:
            self.compare_changed.emit(list(self._selected))
        if baseline is not None:
            self.set_baseline(baseline)
        self._update_scope_menu()
        self._update_compare_menu()
        self._update_depth_menu()
        self._time_cache.clear()
        self._rebuild()

    def set_series(self, ao_runs: list[dict[str, Any]] | None,
                   cap_runs: list[dict[str, Any]] | None) -> None:
        """The recorded availability and capacity plots (parsed sink rows)."""
        self._ao_runs = list(ao_runs or [])
        self._cap_runs = list(cap_runs or [])
        self._time_cache.clear()
        self._rebuild()

    def set_cost_series(self, cost_runs: list[dict[str, Any]] | None) -> None:
        """The parsed interim cost report (cumulative cost snapshots)."""
        self._cost_runs = list(cost_runs or [])
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

    def set_note(self, text: str, *, time_charts_only: bool = False) -> None:
        """A page note.  `time_charts_only` limits it to the recorded-plot
        charts: the rows it describes are what those charts draw, and the
        run-to-run charts (chance of reaching a value, the ladder) use every
        run regardless."""
        self._note_text = text
        self._note_time_only = bool(time_charts_only)
        self._sync_note()

    def _sync_note(self) -> None:
        key = self.selected_key() or ""
        visible = bool(getattr(self, "_note_text", "")) and (
            not getattr(self, "_note_time_only", False)
            or key in timecharts.TIME_CHARTS)
        self.note.setText(getattr(self, "_note_text", ""))
        self.note.setVisible(visible)

    # -- the list -------------------------------------------------------

    def _rebuild(self) -> None:  # noqa: C901 - one list rebuild
        current = self.selected_key() or self._wanted
        folded = {item.text(0) for item in self._groups() if not item.isExpanded()}
        self._entries = {e.key: e for e in self._catalogue()}
        self._build_errors = {}
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
        if self.search.text():
            self._filter_charts(self.search.text())
        self._show_current()

    def _filter_charts(self, text: str) -> None:
        """Show only charts whose title, key or group matches the search."""
        needle = " ".join(text.lower().split())
        groups = self._groups()
        if needle and self._search_folded is None:
            self._search_folded = {group.text(0): group.isExpanded()
                                   for group in groups}
        if not needle and self._search_folded is not None:
            folded, self._search_folded = self._search_folded, None
            for group in groups:
                group.setExpanded(folded.get(group.text(0), True))
        first: QTreeWidgetItem | None = None
        for group in groups:
            any_visible = False
            for index in range(group.childCount()):
                item = group.child(index)
                entry = self._entries.get(str(item.data(0, _KEY) or ""))
                haystack = " ".join((
                    group.text(0), item.text(0),
                    entry.key if entry else "", entry.group if entry else "",
                    entry.missing if entry else "")).lower()
                visible = not needle or all(part in haystack
                                            for part in needle.split())
                item.setHidden(not visible)
                if visible:
                    any_visible = True
                    if first is None:
                        first = item
            group.setHidden(not any_visible)
            if needle and any_visible:
                group.setExpanded(True)
        current = self.nav.currentItem()
        if needle and (current is None or current.isHidden()) and first is not None:
            self.nav.setCurrentItem(first)

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
            item.setToolTip(0, (f"{entry.title} — {spec.subtitle}"
                                if spec is not None and spec.subtitle
                                else entry.title))
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

    def _catalogue(self) -> list[_Entry]:  # noqa: C901, PLR0912, PLR0915 - one table
        i = self._inputs
        per_run: list[dict] = i.get("per_run") or []
        attribution = i.get("attribution")
        importance = i.get("importance")
        periods = i.get("periods")
        report_data = i.get("report_data") or []
        target, units = i.get("target"), i.get("units") or ""
        if attribution and units and not attribution.get("units"):
            # captions such as the overlay's "Intake caused 42.8 h" read the
            # units from the attribution; the GUI passes them separately
            attribution = {**attribution, "units": units}
        row = self._scope_row()
        source = hier.scoped(attribution, row) if row is not None else (attribution or {})
        scope_reason = ("This chart is system-wide and has no per-hierarchy "
                        "series. Choose System, or use the hierarchy overlay, "
                        "small multiples, heatmap or state ribbon."
                        if row is not None else "")
        measure = ("availability" if row is not None else
                   (i.get("measure") or "availability"))
        scoped_per_run = hier.per_run(attribution, row) if row is not None else per_run
        scoped_label = ("share of system downtime" if row is not None
                        else measure.replace("_", " ").capitalize())
        scoped_target = None if row is not None else target
        # The ladder, the waterfall and the year charts are availability
        # whatever the headline measure is: a dependability target is not
        # drawn on them.
        a_target = (None if row is not None else
                    target if (i.get("measure") or "availability") == "availability"
                    else None)
        entries: list[_Entry] = []

        def add(key: str, title: str, group: str,
                build: Callable[[], charts.ChartSpec | None] | None,
                missing: str = "", action: str = "",
                needs: tuple[str, ...] = ()) -> None:
            if missing and needs and not action:
                action = run_needs.button_text(*needs)
            entries.append(_Entry(key, title, group, build, missing, action,
                                  needs if missing else ()))

        has_runs = bool(per_run) or bool(row is not None and scoped_per_run)
        no_runs = "" if has_runs else "No runs yet: run the model (F5)."
        # the Story's headline measure: the outcome and chance charts drew
        # availability beside a dependability Story and convergence chart
        add("trial-outcome", "Run-to-run outcome", "The answer",
            lambda: _with_run_id(charts.trial_outcome(
                scoped_per_run, measure, label=scoped_label, target=scoped_target,
                units=units), i),
            no_runs)
        add("ecdf", "Chance of reaching a value", "The answer",
            lambda: charts.ecdf(scoped_per_run, measure, label=scoped_label,
                                target=scoped_target, units=units,
                                higher_is_better=row is None), no_runs)
        add("reliability-curve", "Reliability over time", "The answer",
            lambda: charts.reliability_curve(per_run, units=units),
            no_runs or scope_reason)
        add("availability-ladder", "Availability ladder", "The answer",
            lambda: _with_run_id(charts.availability_ladder(_ladder(per_run), a_target), i),
            no_runs or scope_reason)
        add("convergence", "Has the run settled?", "The answer",
            lambda: charts.convergence(scoped_per_run, measure,
                                       target=scoped_target, label=scoped_label),
            "" if len(scoped_per_run) >= 2 else "Needs at least 2 runs.")

        has_hier = bool(attribution and attribution.get("hierarchies"))
        model_hier = self._inputs.get("model_hierarchies")
        model_phases = self._inputs.get("model_phases")
        drivers_state = i.get("drivers") or "ready"
        if has_hier:
            no_hier = ""
        elif not model_hier:
            no_hier = "This model has no hierarchy roll-up."
        elif not attribution:
            no_hier = ("The run kept no event log: turn on Detailed event log "
                       "and run again." if drivers_state == "unavailable" else
                       "Work out the downtime drivers first (the hierarchy "
                       "roll-up comes from the same replay).")
        else:
            no_hier = ("This run has no hierarchy roll-up: rerun it with "
                       "attribution and a hierarchy roll-up.")
        if not attribution:
            phase_missing = ("The run kept no event log: turn on Detailed "
                             "event log and run again."
                             if drivers_state == "unavailable" else
                             "Work out the downtime drivers first.")
        elif (attribution or {}).get("phases"):
            phase_missing = ""
        elif model_phases:
            phase_missing = ("This run has no phase breakdown: rerun it with "
                             "phases and attribution on.")
        else:
            phase_missing = "The model has no mission phases."
        outages = (attribution or {}).get("outages") or {}
        has_hier_timeline = any(run.get("hier") for run in outages.get("runs") or [])
        no_timeline = ("" if has_hier_timeline else
                       "This run kept no per-hierarchy outage timeline: run "
                       "again with downtime attribution on.")
        chosen = self._compare_refs()
        chosen_rows = []
        if chosen:
            try:
                chosen_rows = hier.resolve_many(attribution, chosen)
            except ValueError:
                chosen_rows = []
        overlap = hier.overlaps(chosen_rows)
        no_overlap = ("" if not overlap else
                      "These hierarchies overlap (a parent includes its "
                      "child): choose different branches, or one level.")
        has_comparable = any(float((item.get("dtci") or {}).get("mean") or 0.0) > 0
                             for item in ((attribution or {}).get("hierarchies") or []))
        no_compare = ("" if has_comparable else
                      "No hierarchy has downtime to compare in this run.")
        add("hierarchy-scorecard", "Hierarchy scorecard", "By hierarchy",
            lambda: charts.hierarchy_scorecard(attribution or {}, depth=self._ranking_depth()),
            scope_reason or no_hier)
        add("hierarchy-pareto", "Hierarchy Pareto", "By hierarchy",
            lambda: charts.hierarchy_pareto(attribution or {}, self._scope,
                                                    depth=self._ranking_depth()), no_hier)
        add("hierarchy-map", "Where the downtime sits", "By hierarchy",
            self._hierarchy_map_spec, no_hier)
        add("hierarchy-overlay", "When did each hierarchy's downtime build up?", "By hierarchy",
            lambda: charts.hierarchy_overlay(attribution or {}, chosen,
                                                    depth=self._ranking_depth()),
            no_hier or no_timeline or no_overlap or no_compare)
        add("hierarchy-small-multiples", "Is the hierarchy ranking stable?", "By hierarchy",
            lambda: charts.hierarchy_small_multiples(attribution or {}, chosen,
                                                            depth=self._ranking_depth()),
            no_hier or no_overlap or no_compare)
        add("hierarchy-delta", "Hierarchy change against baseline", "By hierarchy",
            lambda: charts.hierarchy_delta(
                attribution or {}, self._baseline_rows,
                depth=self._ranking_depth(),
                baseline_label=self._baseline_label),
            scope_reason or no_hier or ("" if self._baseline_rows else
                                        "Set a baseline run first, then run again."))
        add("hierarchy-heatmap", "Hierarchy by cause", "By hierarchy",
            lambda: charts.hierarchy_heatmap(attribution or {}, self._scope,
                                                    depth=self._ranking_depth()), no_hier)
        if not (attribution or {}).get("phases"):
            no_phase = "The model has no mission phases."
        elif (any(float((item.get("dtci") or {}).get("mean") or 0.0) > 0
                  for item in (attribution or {}).get("hierarchies") or [])
              and all(not item.get("phase_shares")
                      for item in (attribution or {}).get("hierarchies") or [])):
            no_phase = ("This run has no per-hierarchy phase breakdown: rerun "
                        "it with phases and attribution on.")
        else:
            no_phase = ""
        add("hierarchy-phase-heatmap", "Hierarchy by phase", "By hierarchy",
            lambda: charts.hierarchy_phase_heatmap(
                attribution or {}, self._scope, depth=self._ranking_depth()),
            no_hier or no_phase)
        add("hierarchy-anatomy", "Outage paths across hierarchies", "By hierarchy",
            lambda: charts.hierarchy_anatomy(attribution or {}, self._scope), no_hier)
        add("hierarchy-ribbon", "When was each hierarchy down?", "By hierarchy",
            lambda: charts.hierarchy_ribbon(
                attribution or {}, max(1, self.run_box.value()),
                depth=self._ranking_depth(), hierarchy=self._scope),
            no_hier or no_timeline)


        ao, cap = self._ao_runs, self._cap_runs
        has_interval = any(v is not None for r in ao for v in r.get("interval", []))
        # recorded, but the run ended before one interval did: "tick the
        # Availability plot" was said with the plot ticked
        short_run = ("The run is shorter than one recording interval, so no "
                     "interval was completed. Lower the availability plot's "
                     "interval under Run panel ▸ Recording, then run again.")
        for key, title, missing in (
                ("availability-through-run", "Availability through a run",
                 scope_reason or ("" if ao else _RECORD_AO)),
                ("interval-availability", "Availability interval by interval",
                 scope_reason or ("" if has_interval else short_run if ao else _RECORD_AO)),
                ("availability-band", "Availability across all runs",
                 scope_reason or ("" if len(ao) >= 2 else (
                     "Needs the availability plot of at least 2 runs." if ao
                     else _RECORD_AO))),
                ("capacity-through-run", "Capacity through a run",
                 scope_reason or ("" if cap else _RECORD_CAP)),
                ("capacity-band", "Capacity across all runs",
                 scope_reason or ("" if len(cap) >= 2 else (
                     "Needs the capacity plot of at least 2 runs." if cap
                     else _RECORD_CAP)))):
            add(key, title, "Over time", None, missing,
                needs=(("capacity_plot",) if key.startswith("capacity") and not cap
                       else ("availability_plot",) if not key.startswith("capacity")
                       and not ao else ()))

        if attribution:
            def drivers_missing(_need: str = "") -> str:
                return ""
            action = ""
        else:
            state = i.get("drivers") or "ready"
            action = "Work out the downtime drivers" if state == "pending" else ""
            reason = ("Work out which elements caused each outage first (it "
                      "replays this run's event log)." if state == "pending" else
                      "These charts replay the event log. "
                      + run_needs.gui_hint("attribution"))

            def drivers_missing(_need: str = "") -> str:
                return reason
        has_failure_counts = any(
            isinstance(row.get("num_component_failures"), (int, float))
            for row in per_run)
        add("component-failures", "Which components fail most often?",
            "Why it goes down",
            lambda: charts.component_failure_pareto(per_run),
            no_runs or scope_reason or (
                "" if has_failure_counts else
                "This result has no per-component failure counts."))
        a = source
        operational = None if row is not None else _operational(per_run)
        for key, title, build, extra in (
                ("downtime-pareto", "Who causes the downtime",
                 lambda: charts.downtime_pareto(a), ""),
                ("downtime-waterfall", "Where availability is lost",
                 lambda row=row: charts.downtime_waterfall(
                     a, None if row is not None else operational,
                     scoped_target,
                     ladder=None if row is not None else _ladder(per_run)), ""),
                ("downtime-treemap", "Downtime treemap",
                 lambda: charts.downtime_treemap(a), ""),
                ("phase-downtime", "Downtime by phase",
                 lambda: charts.phase_downtime(a),
                 scope_reason or phase_missing),
                ("phase-cause-heatmap", "Phase by cause",
                 lambda: charts.phase_cause_heatmap(a),
                 scope_reason or phase_missing
                 or ("" if any(row.get("causes") for row in
                              (attribution or {}).get("phases") or [])
                     else "Rerun with phases and attribution on to split each "
                          "phase's downtime by cause.")),
                ("outage-anatomy", "What an outage looks like",
                 lambda: charts.outage_anatomy(a), ""),
                ("ensemble-ribbon", "Run-to-run state ribbon",
                 lambda: charts.ensemble_ribbon(a),
                 scope_reason or ("" if not attribution or a.get("outages")
                                 else "No outage timeline was kept for this run.")),
                ("outage-histogram", "How long is an outage?",
                 lambda: charts.outage_histogram(a), scope_reason),
                ("frequency-duration", "Frequent or long outages?",
                 lambda: charts.frequency_duration(a), "")):
            add(key, title, "Why it goes down", build, drivers_missing() or extra,
                action if not attribution else "",
                needs=() if attribution or action else ("attribution",))

        no_capacity = ("" if a.get("capacity") else
                       drivers_missing() or "Lost capacity needs the capacity "
                       "analysis. " + run_needs.gui_hint("capacity"))
        capacity_needs: tuple[str, ...] = (
            () if a.get("capacity") or action else
            ("capacity",) if attribution else ("attribution", "capacity"))
        for key, title, build in (
                ("lost-capacity-pareto", "Who loses the capacity",
                 lambda: charts.lost_capacity_pareto(a)),
                ("capacity-vs-downtime", "Downtime against lost capacity",
                 lambda: charts.capacity_vs_downtime(a)),
                ("capacity-waterfall", "Lost capacity by cause",
                 lambda: charts.capacity_waterfall(a)),
                ("lost-capacity-treemap", "Lost capacity treemap",
                 lambda: charts.lost_capacity_treemap(a))):
            add(key, title, "Lost capacity", build,
                scope_reason or no_capacity,
                action if not attribution else "", needs=capacity_needs)
        if has_hier:
            add("capacity-sankey", "Lost capacity by hierarchy and cause",
                "Lost capacity", self._capacity_sankey_spec,
                no_capacity or no_overlap)

        ends = [float(row["ending_sim_time"]) for row in per_run
                if isinstance(row.get("ending_sim_time"), (int, float))]
        length = max(ends) if ends else None
        if periods and (periods.get("by_index")
                        or periods.get("periods_per_run")):
            no_years = ""
        elif drivers_missing():
            no_years = drivers_missing()
        elif not str(i.get("units") or "hours").lower().startswith("hour"):
            # "this run is 480 hours" was said of a 480-minute run
            no_years = ("The year charts need the model's time unit to be "
                        f"hours; this model counts {str(i.get('units')).lower()}.")
        elif length is not None and length >= 8760:
            no_years = ("No outage was recorded, so every year was fully "
                        "available: there is nothing to draw year by year.")
        else:
            no_years = ("Needs a run at least one whole year long (8,760 hours)"
                        + (f"; this run is {length:,.0f} hours." if length else "."))
        years_needs: tuple[str, ...] = (
            ("attribution",) if not attribution and not action else ())
        p = periods or {}
        no_years = scope_reason or no_years
        add("annual-fan", "Availability year by year", "Year by year",
            lambda: charts.annual_fan(p, a_target), no_years, action if not attribution else "",
            needs=years_needs)
        add("annual-dots", "How often is a year below target?", "Year by year",
            lambda: charts.annual_dots(p, a_target), no_years, action if not attribution else "",
            needs=years_needs)
        add("annual-capacity-fan", "Capacity delivered year by year", "Year by year",
            lambda: charts.annual_capacity_fan(p, self._capacity_target),
            no_years or ("" if (p.get("capacity") or {}).get("by_index")
                         else "Needs the capacity analysis as well. "
                         + run_needs.gui_hint("capacity")),
            action if not attribution else "",
            needs=years_needs or (() if (p.get("capacity") or {}).get("by_index")
                                  or not periods else ("capacity",)))

        has_cost = any(getattr(rd, "cost", None) is not None for rd in report_data)
        no_cost = "" if has_cost else ("Needs the cost analysis. "
                                       + run_needs.gui_hint("cost"))
        cost_needs = () if has_cost else ("cost",)
        cost_rows = charts.element_costs(report_data)
        if row is not None:
            cost_rows = hier.cost_rows(cost_rows, row, source.get("elements") or [])
        scoped_importance = (hier.importance_rows(importance, row,
                                                  source.get("elements") or [])
                             if row is not None else importance)
        add("cost-pareto", "Where the money goes", "Cost",
            lambda: charts.cost_pareto(cost_rows), no_cost,
            needs=cost_needs)
        add("cost-treemap", "Cost treemap", "Cost",
            lambda: charts.cost_treemap(cost_rows), no_cost,
            needs=cost_needs)
        no_cost_runs = ("" if self._cost_runs else
                        "Needs the interim cost report. "
                        + run_needs.gui_hint("cost_plot"))
        for key, title, build in (
                ("cost-categories", "Cost by category",
                 lambda: charts.cost_categories(self._cost_runs)),
                ("cost-cumulative", "Cumulative operating cost over time",
                 lambda: charts.cost_cumulative(self._cost_runs)),
                ("cost-annual", "Operating cost per year",
                 lambda: charts.cost_annual(self._cost_runs))):
            add(key, title, "Cost", build, no_cost_runs,
                needs=(() if self._cost_runs else
                       ("cost_plot",) if has_cost else ("cost", "cost_plot")))
        # the quadrant plots RAW against RRW: only mode "both" has the two
        quadrant = bool(scoped_importance) and (scoped_importance or {}).get("mode") in (None, "both")
        no_quadrant = "" if quadrant else (
            ("The quadrant needs RAW and RRW, so importance mode Both. "
             if scoped_importance else "Needs importance measures (mode Both). ")
            + run_needs.gui_hint("importance"))
        add("importance-quadrant", "Importance quadrant", "What matters most",
            lambda: charts.importance_quadrant(scoped_importance or {}), no_quadrant,
            needs=() if quadrant else ("importance",))
        if not attribution and (model_hier or model_phases):
            for entry in entries:
                if (entry.group == "By hierarchy" or entry.key.startswith("phase-")
                        or entry.key == "hierarchy-phase-heatmap"):
                    if drivers_state == "pending":
                        entry.action = "Work out the downtime drivers"
                        entry.needs = ()
                    elif drivers_state == "unavailable":
                        entry.action = run_needs.button_text("attribution")
                        entry.needs = ("attribution",)
        if not has_hier and not model_hier:
            # No hierarchy roll-up: the question does not exist, so do not
            # leave a greyed group of controls and rows behind.
            entries = [entry for entry in entries if entry.group != "By hierarchy"]
        if has_hier or model_hier:
            # The nested hierarchy treemap already contains the elements;
            # a flat treemap beside it is the same question twice.
            entries = [entry for entry in entries
                       if entry.key != "downtime-treemap"]
        if not (attribution or {}).get("phases") and not model_phases:
            entries = [entry for entry in entries
                       if not entry.key.startswith("phase-")
                       and entry.key != "hierarchy-phase-heatmap"]
        if row is not None:
            # Scoped, the hierarchy Pareto already ranks the children and
            # elements inside the scope with a stated denominator, and the
            # system outage anatomy already shows the paths inside the scope.
            entries = [entry for entry in entries
                       if entry.key not in ("downtime-pareto",
                                            "hierarchy-anatomy")]
        return entries

    # -- showing a chart ------------------------------------------------

    def _safe(self, entry: _Entry) -> charts.ChartSpec | None:
        from raptor.api.insight.axes import currency
        try:
            if entry.build is None:
                return None
            # captions are written as the chart is built: build in the
            # model's currency, and record it so the drawing and saves match
            with currency(self._inputs.get("currency")):
                spec = entry.build()
                return charts.with_currency_stamp(spec) if spec is not None else None
        except Exception as exc:  # noqa: BLE001 - one bad chart must not break the tab
            logging.getLogger(__name__).exception("could not build %s", entry.key)
            self._build_errors[entry.key] = str(exc)
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

    def _show_current(self) -> None:  # noqa: C901 - one branch per chart shape
        key = self.selected_key()
        entry = self._entries.get(key or "")
        self._sync_controls(entry)
        self._sync_note()
        if entry is None:
            self.chart.set_spec(None)
            self.stack.setCurrentWidget(self.chart_scroll)
            return
        if entry.missing:
            self._show_missing(entry.title, entry.missing, entry.action)
            return
        if entry.key == "hierarchy-map":
            spec = self._hierarchy_map_spec()
        elif entry.key == "capacity-sankey":
            spec = self._capacity_sankey_spec()
        elif entry.key == "hierarchy-ribbon":
            try:
                spec = charts.hierarchy_ribbon(
                    self._inputs.get("attribution") or {},
                    max(1, self.run_box.value()), depth=self._ranking_depth(),
                    hierarchy=self._scope)
            except ValueError:
                spec = None
        else:
            spec = (self._time_spec(entry.key) if entry.key in timecharts.TIME_CHARTS
                    else self._specs.get(entry.title))
        if spec is None:
            self._show_missing(
                entry.title,
                self._build_errors.get(entry.key,
                                       "This chart cannot be drawn from this "
                                       "result."), "")
            return
        self.fold_box.setVisible(charts.foldable(spec))
        folding = charts.parse_fold(self.fold_box.currentData())
        if folding:
            spec = charts.fold_small(spec, **folding)
        if self._scope_row() is not None:
            spec = hier.annotate(spec, self._scope_meta())
        if entry.key in ("capacity-through-run", "capacity-band"):
            spec = self._apply_capacity_axis(spec)
        self.chart.set_spec(spec)
        self.chart.setToolTip(spec.caption)
        self.stack.setCurrentWidget(self.chart_scroll)

    def _on_missing_action(self) -> None:
        entry = self._entries.get(self.selected_key() or "")
        if entry is not None and entry.needs:
            self.enable_requested.emit(list(entry.needs))
        else:
            self.drivers_requested.emit()

    def _show_missing(self, title: str, text: str, action: str) -> None:
        self.chart.set_spec(None)
        self.missing_title.setText(title)
        self.missing_text.setText(text)
        self.missing_action.setText(action)
        self.missing_action.setVisible(bool(action))
        self.stack.setCurrentIndex(1)

    def _has_hierarchies(self) -> bool:
        attribution = self._inputs.get("attribution") or {}
        return bool(attribution.get("hierarchies"))

    def _sync_controls(self, entry: _Entry | None) -> None:
        key = entry.key if entry is not None and not entry.missing else ""
        has_hier = self._has_hierarchies()
        self.scope_button.setVisible(has_hier)
        self.follow_button.setVisible(has_hier)
        self.compare_button.setVisible(
            has_hier and key in charts.MULTI_HIERARCHY_CHARTS
            # Cause only has no hierarchy column to compare
            and not (key == "capacity-sankey"
                     and not self.sankey_box.currentData()))
        self.baseline_button.setVisible(has_hier and key == "hierarchy-delta")
        per_run = key in timecharts.PER_RUN_CHARTS or key == "hierarchy-ribbon"
        runs = (int((self._inputs.get("attribution") or {}).get("runs") or 0)
                if key == "hierarchy-ribbon" else
                len(self._cap_runs if key.startswith("capacity") else self._ao_runs))
        self.run_box.blockSignals(True)
        self.run_box.setMaximum(max(1, runs))
        self.run_box.blockSignals(False)
        self.run_of.setText(f"of {runs:,}" if runs else "")
        for widget in (self.run_label, self.run_box, self.run_of):
            widget.setVisible(per_run)
        floor = key in ("availability-through-run", "interval-availability",
                        "availability-band")
        self.floor_button.setVisible(floor)
        self.capacity_axis_button.setVisible(
            key in ("capacity-through-run", "capacity-band"))
        self.target_button.setVisible(
            key in TARGET_CHARTS and self._scope_row() is None)
        self.capacity_target_button.setVisible(key == "annual-capacity-fan")
        self.fold_box.setVisible(False)      # _show_current shows it when it applies
        depth_charts = {
            "hierarchy-scorecard", "hierarchy-pareto", "hierarchy-heatmap",
            "hierarchy-phase-heatmap", "hierarchy-small-multiples",
            "hierarchy-ribbon", "hierarchy-overlay", "hierarchy-map",
            "hierarchy-delta"}
        self.depth_box.setVisible(
            has_hier and key in depth_charts
            and hier.max_depth(self._inputs.get("attribution")) > 1)
        self.layout_box.setVisible(has_hier and key == "hierarchy-map")
        self.sankey_box.setVisible(has_hier and key == "capacity-sankey")
        self.sankey_delivered.setVisible(has_hier and key == "capacity-sankey")
        self._sync_floor_text()
        self._sync_target_text()
        self.export_button.setEnabled(bool(key))
        self.raw_action.setVisible(key in timecharts.TIME_CHARTS)

    @property
    def fold_mode(self) -> str:
        """The Others control as a `charts.parse_fold` mode ("" = every item)."""
        return str(self.fold_box.currentData() or "")

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
            "Axis from: auto" if self._y_floor <= 0
            else f"Axis from: {100 * self._y_floor:.4g} %")

    def _capacity_axis_control(self) -> QToolButton:
        """The capacity charts' y-axis presets (the axis is visual only)."""
        button = QToolButton()
        button.setText("Y axis: auto")
        button.setPopupMode(QToolButton.ToolButtonPopupMode.InstantPopup)
        button.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonTextOnly)
        button.setToolTip(
            "Frame the capacity y axis: fit the data, show the full-capacity "
            "goal, zoom near it, keep a probability band of recorded values, "
            "start at a capacity exceeded for a share of the time, or type a "
            "numeric range. It changes the drawing only.")
        menu = QMenu(button)
        group = QActionGroup(menu)
        self.capacity_axis_actions: dict[str, QAction] = {}
        for key, text in (("auto", "Fit the data"),
                          ("full", "Full capacity (0 to the goal)"),
                          ("goal", "Proximity to the goal…"),
                          ("probability", "Probability band…"),
                          ("exceedance", "Exceedance…"),
                          ("numeric", "Numeric range…")):
            action = menu.addAction(text)
            action.setCheckable(True)
            action.setChecked(key == "auto")
            action.triggered.connect(
                lambda _checked=False, mode=key: self._ask_capacity_axis(mode))
            group.addAction(action)
            self.capacity_axis_actions[key] = action
        button.setMenu(menu)
        self.capacity_axis_button = button
        return button

    def _ask_capacity_axis(self, mode: str) -> None:  # noqa: C901 - one prompt per mode
        from PySide6.QtWidgets import QInputDialog

        from raptor.gui.ui import msgbox

        value: float | None = None
        low = high = None
        if mode == "goal":
            value, ok = QInputDialog.getDouble(
                self, "Proximity to goal",
                "Show from this share of full capacity, in %:", 95.0,
                0.0, 100.0, 2)
            if not ok:
                return
        elif mode == "probability":
            value, ok = QInputDialog.getDouble(
                self, "Probability band",
                "Show the central share of the recorded capacity, in % "
                "(90 = the middle 90 %):", 90.0, 1.0, 100.0, 2)
            if not ok:
                return
        elif mode == "exceedance":
            value, ok = QInputDialog.getDouble(
                self, "Exceedance",
                "Start at the capacity exceeded for this share of the time, "
                "in % (95 = the level reached 95 % of the time):",
                95.0, 0.0, 100.0, 2)
            if not ok:
                return
        elif mode == "numeric":
            spec = self.selected_spec()
            current = self._capacity_axis_range
            summary = self._capacity_summary(spec)
            if current is None:
                current = (float(summary.get("min") or 0.0),
                           float(summary.get("max") or 1.0))
            low, ok = QInputDialog.getDouble(
                self, "Y axis", "Lowest value on the capacity axis:",
                float(current[0]), -1e15, 1e15, 6)
            if not ok:
                return
            high, ok = QInputDialog.getDouble(
                self, "Y axis", "Highest value on the capacity axis:",
                float(current[1]), -1e15, 1e15, 6)
            if not ok:
                return
            if high <= low:
                msgbox.warning(self, "Y axis",
                               "The highest value must be above the lowest.")
                return
        self.set_capacity_axis(mode, value, low=low, high=high)

    def set_capacity_axis(self, mode: str, value: float | None = None, *,
                          low: float | None = None,
                          high: float | None = None) -> None:
        """Set the capacity y-axis framing (``auto`` clears it)."""
        self._capacity_axis_mode = mode if mode in self.capacity_axis_actions else "auto"
        self._capacity_axis_value = value
        self._capacity_axis_range = ((float(low), float(high))
                                     if mode == "numeric" and low is not None
                                     and high is not None else None)
        for key, action in self.capacity_axis_actions.items():
            action.setChecked(key == self._capacity_axis_mode)
        self._sync_capacity_axis_text()
        self._time_cache.clear()
        self._show_current()

    def _sync_capacity_axis_text(self) -> None:
        if not hasattr(self, "capacity_axis_button"):
            return
        mode = self._capacity_axis_mode
        text = {"auto": "Y axis: auto", "full": "Y axis: full capacity",
                "goal": "Y axis: near the goal",
                "probability": "Y axis: probability band",
                "exceedance": "Y axis: exceedance",
                "numeric": "Y axis: numeric"}.get(mode, "Y axis: auto")
        self.capacity_axis_button.setText(text)

    @staticmethod
    def _capacity_key(share: float) -> str:
        """The `capacity_range` key for a probability (5 % -> ``p05``)."""
        if share <= 0:
            return "p00"
        if share >= 1:
            return "p100"
        # snap an interior share to the nearest whole percent the summary
        # carries (the 0.5 % tail of a 99 % band is p01, not p00)
        return f"p{max(1, min(99, round(100 * share))):02d}"

    @staticmethod
    def _capacity_at(summary: dict[str, float], share: float,
                     fallback: float) -> float:
        """The capacity at probability `share`, interpolated between the
        percentiles the summary carries (p00, p01, p05, ... p100).

        A 70 % band needs p15 and p85, which are not stored: looking the key
        up directly fell back to the min/max and drew the whole range."""
        points = sorted(
            (int(key[1:]) / 100.0, float(value)) for key, value in summary.items()
            if key[:1] == "p" and key[1:].isdigit())
        if not points:
            return fallback
        share = max(0.0, min(1.0, share))
        for (lo_p, lo_v), (hi_p, hi_v) in itertools.pairwise(points):
            if lo_p <= share <= hi_p:
                span = hi_p - lo_p
                return lo_v if span <= 0 else lo_v + (hi_v - lo_v) * (share - lo_p) / span
        return points[0][1] if share < points[0][0] else points[-1][1]

    @classmethod
    def _capacity_summary(cls, spec: charts.ChartSpec | None) -> dict[str, float]:
        """The capacity samples a y-axis preset can frame a spec with."""
        summary = dict((spec.provenance.get("capacity_range") if spec else None) or {})
        if summary:
            return summary
        values: list[float] = []
        for layer in (spec.series if spec else []):
            if layer.get("role") == "area" and layer.get("hi"):
                values += [float(v) for v in layer["hi"]]
            elif layer.get("role") == "line" and layer.get("y"):
                values += [float(v) for v in layer["y"]]
        if not values:
            return {}
        ordered = sorted(values)
        def q(share: float) -> float:
            pos = share * (len(ordered) - 1)
            lo = int(pos)
            hi = min(lo + 1, len(ordered) - 1)
            return ordered[lo] + (ordered[hi] - ordered[lo]) * (pos - lo)
        return {"min": ordered[0], "max": ordered[-1], "mean": sum(ordered) / len(ordered),
                "full": ordered[-1],
                **{cls._capacity_key(share): q(share)
                   for share in (0.0, 0.01, 0.05, 0.10, 0.25, 0.50,
                                 0.75, 0.90, 0.95, 0.99, 1.0)}}

    def _apply_capacity_axis(self, spec: charts.ChartSpec) -> charts.ChartSpec:
        """Frame a capacity chart's y axis without changing its numbers."""
        mode = self._capacity_axis_mode
        if mode == "auto":
            return spec
        summary = self._capacity_summary(spec)
        if not summary:
            return spec
        full = float(summary.get("full") or summary.get("max") or 0.0)
        low = float(summary.get("min") or 0.0)
        high = float(summary.get("max") or full or 1.0)
        value = self._capacity_axis_value
        if mode == "full":
            low, high = 0.0, max(full, high)
        elif mode == "goal":
            share = max(0.0, min(1.0, (value if value is not None else 95.0) / 100.0))
            low = full * share
            high = max(full * 1.02, high) if full > 0 else high
        elif mode == "probability":
            share = max(0.001, min(0.999, (value if value is not None else 90.0) / 100.0))
            tail = (1.0 - share) / 2.0
            low = self._capacity_at(summary, tail, low)
            top = 1.0 - tail
            high = self._capacity_at(summary, top, high)
        elif mode == "exceedance":
            share = max(0.0, min(1.0, (value if value is not None else 95.0) / 100.0))
            below = 1.0 - share
            low = self._capacity_at(summary, below, low)
            high = max(full, high) if share > 0 else high
        elif mode == "numeric" and self._capacity_axis_range is not None:
            low, high = self._capacity_axis_range
        if not (high > low):
            high = low + max(1.0, abs(low) * 0.01)
        framed = copy.copy(spec)
        framed.provenance = dict(spec.provenance)
        framed.provenance["y_range"] = [max(0.0, low), high]
        framed.provenance["y_axis_exact"] = mode == "numeric"
        framed.provenance["capacity_axis"] = {
            "mode": mode, "value": value, "range": [max(0.0, low), high]}
        return framed

    def _target_control(self) -> QPushButton:
        button = QPushButton("Target: none")
        button.setToolTip(
            "A target availability drawn as a dashed line on these charts and "
            "judged in the Overview's Story (met or missed, and by how many "
            "runs). It is saved in the model file; it does not change the "
            "simulation.")
        button.clicked.connect(self._ask_target)
        return button

    @property
    def target(self) -> float | None:
        value = self._inputs.get("target")
        return float(value) if isinstance(value, (int, float)) else None

    def set_target(self, value: float | None) -> None:
        """The target line (a fraction; None or 0 clears it). No signal:
        the dock passes back what the page told it."""
        value = value if value and 0.0 < value <= 1.0 else None
        if value == self.target:
            return
        self._inputs["target"] = value
        self._time_cache.clear()
        self._rebuild()
        self._sync_target_text()

    def _sync_target_text(self) -> None:
        target = self.target
        self.target_button.setText("Target: none" if target is None
                                   else f"Target: {100 * target:.6g} %")

    def set_capacity_target(self, value: float | None) -> None:
        self._capacity_target = value if value and 0 < value <= 1 else None
        self.capacity_target_button.setText(
            "Capacity target: none" if self._capacity_target is None else
            f"Capacity target: {100 * self._capacity_target:.6g} %")
        self._time_cache.clear()
        self._rebuild()

    def _ask_capacity_target(self) -> None:
        from PySide6.QtWidgets import QInputDialog
        value, ok = QInputDialog.getDouble(
            self, "Capacity target",
            "Target capacity-weighted availability, in % (0 = none).\n"
            "Saved in the model; the simulation does not change.",
            round(100 * (self._capacity_target or 0.0), 6), 0.0, 100.0, 6)
        if ok:
            target = value / 100.0 if value > 0 else None
            if target != self._capacity_target:
                self.set_capacity_target(target)
                self.capacity_target_changed.emit(target)

    def _ask_target(self) -> None:
        from PySide6.QtWidgets import QInputDialog
        value, ok = QInputDialog.getDouble(
            self, "Target line",
            f"Target {(self._inputs.get('measure') or 'availability').replace('_', ' ')},"
            " in % (0 = no target).\nSaved in the model; "
            "the simulation does not change.",
            round(100 * (self.target or 0.0), 6), 0.0, 100.0, 6)
        if not ok:
            return
        target = value / 100.0 if value > 0 else None
        if target != self.target:
            self.set_target(target)
            self.target_changed.emit(target)

    def _ask_floor(self) -> None:
        from PySide6.QtWidgets import QInputDialog
        # in percent, as the axis is: it asked for 0.95 under a 95 % axis
        value, ok = QInputDialog.getDouble(
            self, "Availability axis", "Start the axis at, in % (0 = fit to the data):",
            round(100 * self._y_floor, 4), 0.0, 99.9999, 4)
        value /= 100.0
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
        aliases = {"hierarchy-treemap": "treemap", "Hierarchy treemap": "treemap",
                   "hierarchy-sunburst": "sunburst",
                   "Hierarchy sunburst": "sunburst"}
        wanted = key_or_title
        if key_or_title in aliases:
            wanted = "Where the downtime sits"
            index = self.layout_box.findData(aliases[key_or_title])
            if index >= 0:
                self.layout_box.blockSignals(True)
                self.layout_box.setCurrentIndex(index)
                self.layout_box.blockSignals(False)
        for item in self._chart_items():
            entry = self._entries[item.data(0, _KEY)]
            if wanted in (entry.key, entry.title):
                self.nav.setCurrentItem(item)
                self._show_current()
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

    def _ask(self, caption: str, label: str, ext: str) -> str:
        from raptor.gui.ui.chart_widget import ask_chart_path
        return ask_chart_path(self, caption, self.selected_key() or "chart", label, ext)

    def _ask_svg(self) -> None:
        if path := self._ask("Save chart", "SVG image", ".svg"):
            self.save_svg(path)

    def _ask_png(self) -> None:
        if path := self._ask("Save chart", "PNG image", ".png"):
            self.save_png(path)

    def _ask_pdf(self) -> None:
        if path := self._ask("Save chart", "PDF document", ".pdf"):
            self.save_pdf(path)

    def _ask_csv(self) -> None:
        if path := self._ask("Save chart data", "CSV", ".csv"):
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
