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

import itertools
import re
import weakref

from raptor.api.presentation import probability_text
from raptor.gui.ui.qtcompat import req

from PySide6.QtWidgets import QAbstractItemView


from typing import Any, cast

from PySide6.QtCore import Qt, QTimer, Signal
from PySide6.QtGui import QColor, QFont, QStandardItemModel
from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QGridLayout, QTabWidget, QLabel,
    QPlainTextEdit, QComboBox, QLineEdit, QTreeView, QTreeWidget,
    QTreeWidgetItem,
    QFrame, QSizePolicy, QScrollArea, QTableWidget, QHeaderView,
)

from raptor.api.tables import parse_final_sections
from raptor.core.engine import weak_metric
from raptor.gui.ui.event_log_model import HEADERS, EventLogModel
from raptor.gui.ui.table_filter_bar import attach_if_large
from raptor.gui.resources.design_tokens import ThemeManager, SPACING, TYPE, active_colour
from raptor.gui.ui.results_pages_mixin import ResultsPagesMixin
from raptor.gui.ui.results_graphs_mixin import ResultsGraphsMixin
from raptor.gui.ui.results_raw_mixin import ResultsRawMixin


from raptor.gui.ui.results_common import (  # noqa: F401  (names tests and callers import from here)
    _mono_font,
    RAW_VALUE_ROLE,
    CENSOR_ROLE,
    _cell_parts,
    GLOSSARY,
    STAT_COLUMN_TOOLTIPS,
    IMPORTANCE_HEADER_TIPS,
    _fmt,
    _SI_PREFIXES,
    _fmt_si,
    _mean,
    _GT_NOTE,
    _GT_EXPLAINED,
    _GT_XRUNS_NOTE,
    _fmt_count,
    _pct,
    _ReadOnlyTable,
    _StatefulTable,
    _ViewportContextMenuFilter,
    _TableActionStrip,
    _FitTable,
    _sort_key,
    _SortItem,
    _MetricCard,
)


class ResultsDock(ResultsRawMixin, ResultsGraphsMixin, ResultsPagesMixin,
                  QWidget):
    #: ("block"|"node", element_NAME) -- the user clicked an event-log row.
    #: The log stores names, not IDs; MainWindow resolves the name against
    #: the loaded model to make the canvas selection.
    element_clicked = Signal(str, str)
    #: A result set arrived or the dock was cleared (`has_results()` may
    #: have changed). The window uses it to tell the Inspector whether an
    #: edit will cost the user their results.
    contents_changed = Signal()
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
        #: (column, order) the user chose per logical results table,
        #: keyed independently of the transient widget so a rebuild keeps
        #: the indicator exactly where the user left it.
        self._sort_states: dict[str, tuple[int, Qt.SortOrder]] = {}
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

        self._scope_banner = QLabel("")
        self._scope_banner.setObjectName("RunScopeBanner")
        self._scope_banner.setWordWrap(True)
        self._scope_banner.setTextFormat(Qt.TextFormat.RichText)
        self._scope_banner.setStyleSheet(
            "QLabel#RunScopeBanner { background: rgba(245, 158, 11, 36); "
            "border: 1px solid #b45309; border-radius: 3px; "
            "padding: 4px 8px; margin: 4px; }")
        self._scope_banner.setVisible(False)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.addWidget(self._scope_banner)
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
        bar.setSpacing(SPACING["sm"])
        bar.setContentsMargins(SPACING["md"], SPACING["xs"], SPACING["md"], 0)
        # Copy / CSV live on each table card now (#205). The old global
        # three-button row spent a full row of height, and its Copy/Export
        # CSV always acted on whichever sub-tab happened to be visible.
        # Export all tables stays on File > Export. Hierarchy selector:
        # filters all result tables by a hierarchy subtree, mirroring
        # Raptor 7's per-dialog hierarchy selectors. Drives the same
        # level-filter path as canvas navigation via a signal MainWindow
        # subscribes to.
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
        bar.addStretch(1)
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

    def _restore_details_view(self, title: str | None,
                              scroll: int = 0) -> None:
        """Re-select a Final Results sub-tab and its scroll offset.

        `_populate_details` rebuilds every page, so the old widgets (and
        their scroll bars) are gone. The tab is selected now; the scroll
        value is applied one event-loop turn later, after the rebuilt page
        has been laid out and its scrollbar range is real.
        """
        if not title:
            return
        for i in range(self._details_tabs.count()):
            if self._details_tabs.tabText(i) != title:
                continue
            self._details_tabs.setCurrentIndex(i)
            page = self._details_tabs.widget(i)
            if scroll and isinstance(page, QScrollArea):
                QTimer.singleShot(
                    0, lambda p=page, v=scroll: self._set_scroll(p, v))
            break

    @staticmethod
    def _set_scroll(widget, value: int) -> None:
        try:
            widget.verticalScrollBar().setValue(value)
        except RuntimeError:
            # A second population replaced the page before the deferred
            # restore ran; there is nothing left to scroll.
            pass

    def _restore_graph_run(self, index: int) -> None:
        """Keep the Graphs run selector on the run the reader was viewing."""
        if 0 <= index < self._graph_run.count():
            self._graph_run.setCurrentIndex(index)

    def _restore_raw_view(self, tab_index: int, scroll: int) -> None:
        """Keep the Report Text tab and its scroll position."""
        if 0 <= tab_index < self._raw_tabs.count():
            self._raw_tabs.setCurrentIndex(tab_index)
            page = self._raw_tabs.widget(tab_index)
            if scroll and isinstance(page, QPlainTextEdit):
                QTimer.singleShot(
                    0, lambda p=page, v=scroll: self._set_scroll(p, v))

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

    def _apply_sort(self, table: QTableWidget, key: str,
                    default: tuple[int, Qt.SortOrder] | None = None) -> None:
        """Turn on header-click sorting and apply this table's remembered
        (or default) order.

        `key` is the table's stable identity across `set_results`
        rebuilds. `default=None` deliberately leaves report order intact
        until the user clicks a header (Summary and Logistics). Filtering
        never calls this: `FilterBar` only hides rows.
        """
        header = table.horizontalHeader()
        table.setSortingEnabled(False)
        state = self._sort_states.get(key, default)
        if state is None or not (0 <= state[0] < table.columnCount()):
            header.setSortIndicator(-1, Qt.SortOrder.AscendingOrder)
        else:
            column, order = state
            # Set the indicator; enabling sorting performs the one sort
            # (an explicit sortItems here would sort the table twice).
            header.setSortIndicator(column, order)
        table.setSortingEnabled(True)
        # A sort moves items but leaves row-hidden flags on the old
        # indexes. Recompute the hierarchy scope when the model settles;
        # FilterBar composes with the same rule for its own passes.
        if not getattr(table, "_hierarchy_sort_hooked", False):
            cast(_StatefulTable, table)._hierarchy_sort_hooked = True
            table.model().layoutChanged.connect(
                lambda *_a: self._apply_level_filter())
        if getattr(table, "_sort_state_key", None) != key:
            cast(_StatefulTable, table)._sort_state_key = key

            def remember(column: int, order: Qt.SortOrder, *,
                         _key: str = key) -> None:
                if column < 0:
                    self._sort_states.pop(_key, None)
                else:
                    self._sort_states[_key] = (column, order)

            header.sortIndicatorChanged.connect(remember)

    def _make_table(self, headers: list[str], rows: list[list[Any]],
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
                item = _SortItem(display)
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
        # A long table (one row per block in a big model) can be filtered
        # by column; a short one is read at a glance and gets no bar. The
        # bar also composes with the hierarchy scope, so its own passes
        # never reveal rows outside the current level. A weak reference
        # keeps the closure from re-creating the table -> bar -> table
        # cycle the filter bar deliberately avoids.
        table_ref = weakref.ref(table)
        attach_if_large(
            table, raw_role=RAW_VALUE_ROLE,
            base_visible=lambda r, tr=table_ref:
            (tr() is not None and self._hierarchy_row_visible(tr(), r)))
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
            # One header per table card: its own Copy/CSV icons. The
            # title is omitted where the tab already names the table
            # (Summary/Logistics), so there is no duplicate heading.
            self._add_table_header(v, name, table,
                                   show_title=name != group)
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
                        rows, *, sort_key: str | None = None,
                        default_sort: tuple[int, Qt.SortOrder] | None = None,
                        keep_report_order: bool | None = None) -> QTableWidget:
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
        # Element-level sections read best alphabetically. Summary and
        # Logistics are ordered by the report itself (measures have a
        # meaningful sequence) and only sort once the user asks.
        report_order = (keep_report_order if keep_report_order is not None
                        else title.split(" - ", 1)[0]
                        in ("SUMMARY", "LOGISTICS"))
        self._apply_sort(
            table, sort_key or f"SECTION::{title}",
            None if report_order else
            (default_sort or (0, Qt.SortOrder.AscendingOrder)))
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


    _WEAK_KEYS = [("run", "Running"), ("repair", "Repairing"),
                  ("rep_hold", "Rep Hold"), ("pm", "PM"),
                  ("pm_hold", "PM Hold"), ("idle", "Idle"),
                  ("standby", "Standby"), ("done", "Done")]

    #: What each Weak Link colour means. Green/Yellow/Red are the
    #: threshold bands; Blue/Brown/Orange are the node-state buckets
    #: (`core.engine.weak_metric`).
    _WEAK_COLOUR_TIPS = {
        "green": "Green — fully working.",
        "yellow": "Yellow — degraded, still partially working.",
        "red": "Red — fully down.",
        "blue": "Blue — standby (available but not operating).",
        "brown": "Brown — dependency wait / idle (blocked by another element).",
        "orange": "Orange — preventive maintenance.",
    }

    #: Weak Link settings: pushed by MainWindow from
    #: the SYSTEM_DATA row. Defaults mirror every fixture's values.
    _weak_anal_type: str = "Availability"
    _weak_gy: float = 0.95
    _weak_yr: float = 0.90
    #: Optional filter: callable -> names of the canvas selection
    #: ("selection options": restrict analysis rows to selected blocks).
    _selected_block_names_fn = None
    _importance_metric: str


    _COST_HEADERS = ["Name", "Purchase", "Operating", "Sparing",
                     "Disposal", "Total"]

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


    #: Plot-file parsing guard: beyond this many rows the interactive
    #: view shows only the first slice and says so — the full data stays
    #: in the stream file for Export Data.
    _MAX_PARSE_ROWS = 200_000
    #: Live per-run refresh guard for the Capacity plot (same rationale,
    #: applied to the per-progress-tick re-parse).
    _LIVE_CAP_REFRESH_ROWS = 50_000
    #: Raw Report preview guards. The End-of-Run report is O(runs x
    #: elements); joining all of it and handing it to QPlainTextEdit is
    #: what crashed the GUI on a large batch. The full report stays in its
    #: ResultStream (or list) for Save Raw Report.
    _RAW_PREVIEW_LINES = 20_000
    _RAW_PREVIEW_BYTES = 4_000_000


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
        self._manifest = None
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
        # The raw tabs' backing sources belong to the cleared result set;
        # leaving them here let Save Raw Report export a previous batch
        # from an empty-looking dock (and kept its stream alive).
        self._raw_sources = {}
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
        self._refresh_scope_banner()
        self.contents_changed.emit()

    def set_results(self, event_logs, end_of_run_lines, final_results_lines,
                    report_data=None, units: str | None = None, notes=None,
                    ao_lines: list[str] | None = None,
                    cap_lines: list[str] | None = None,
                    cost_lines: list[str] | None = None,
                    manifest: dict | None = None,
                    importance: dict | None = None,
                    progress=None):
        # The run manifest travels WITH the results, so a result set can
        # say what it is without trusting a model file that may since
        # have been edited. It also means changing a run setting (trial
        # count, run length, which analyses ran) does not retroactively
        # invalidate results already on screen -- the model hash in the
        # manifest covers engineering content only.
        self._manifest = dict(manifest) if manifest else None
        self._refresh_scope_banner()
        from datetime import datetime
        self._run_at = datetime.now()
        self._event_logs = event_logs
        self._report_data = list(report_data or [])
        # The importance analysis belongs to a run, not to the model, so it
        # travels with the results like the manifest does.
        self._importance = importance
        self._final_lines = final_results_lines
        # Keep the caller's object, not a copy: it may be a disk-backed
        # `ResultStream` holding an O(runs x elements) End-of-Run report.
        # `list(...)` here would pull the whole report back into RAM and
        # undo the streaming this dock is built to accept. Readers that
        # genuinely need every line (Export Tables) materialise it
        # themselves, on demand.
        self._eor_lines = end_of_run_lines if end_of_run_lines is not None else []
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

        def stage(label: str, done: int, total: int) -> None:
            if progress is not None:
                progress(label, done, total)

        # A live refresh (baseline results replaced by the same run plus
        # its Importance data) must not move the reader: remember every
        # selectable view and its scroll offset, then put them back once
        # the rebuilt pages exist.
        previous_tab = None
        previous_scroll = 0
        cur_index = self._details_tabs.currentIndex()
        if cur_index >= 0:
            previous_tab = self._details_tabs.tabText(cur_index)
            current_page = self._details_tabs.widget(cur_index)
            if isinstance(current_page, QScrollArea):
                previous_scroll = current_page.verticalScrollBar().value()
        previous_run = self._run_selector.currentIndex()
        previous_log_scroll = self._log_tree.verticalScrollBar().value()
        previous_graph_run = self._graph_run.currentIndex()
        previous_raw_tab = self._raw_tabs.currentIndex()
        previous_raw_scroll = 0
        raw_page = self._raw_tabs.widget(previous_raw_tab)
        if isinstance(raw_page, QPlainTextEdit):
            previous_raw_scroll = raw_page.verticalScrollBar().value()

        stage("overview", 1, 6)
        self._populate_overview()
        stage("detail tables", 2, 6)
        self._populate_details(final_results_lines)
        stage("level filter", 3, 6)
        self._apply_level_filter()
        self._restore_details_view(previous_tab, previous_scroll)
        stage("graphs", 4, 6)
        self._update_graphs()
        self._restore_graph_run(previous_graph_run)
        stage("raw report", 5, 6)
        self._populate_raw(end_of_run_lines, final_results_lines)
        self._restore_raw_view(previous_raw_tab, previous_raw_scroll)
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
        stage("event log", 6, 6)
        if event_logs:
            wanted = min(max(previous_run, 0),
                         max(self._run_selector.count() - 1, 0))
            self._run_selector.blockSignals(True)
            self._run_selector.setCurrentIndex(wanted)
            self._run_selector.blockSignals(False)
            self._show_selected_run(wanted)
            if previous_log_scroll:
                QTimer.singleShot(
                    0, lambda v=previous_log_scroll: self._set_scroll(
                        self._log_tree, v))
        self._update_log_empty_state()
        self._refresh_theme()
        self.contents_changed.emit()

    def set_importance(self, importance: dict | None) -> None:
        """Replace only the Importance page on an already-populated result set.

        A run with importance measures presents its baseline first, then
        completes the same run with the measures. Rebuilding every table,
        graph and raw view for that second step costs a full report parse
        on a large model and moves the reader. Only the Importance tab can
        have changed, so touch only that tab.
        """
        self._importance = importance
        if not hasattr(self, "_importance_metric"):
            self._importance_metric = "Availability"
        idx = next((i for i in range(self._details_tabs.count())
                    if self._details_tabs.tabText(i) == "Importance"), -1)
        if idx >= 0:
            page = self._details_tabs.widget(idx)
            self._details_tabs.removeTab(idx)
            req(page).deleteLater()
        if importance:
            page = self._build_importance_page()
            self._details_tabs.addTab(self._scroll_wrap(page), "Importance")
        self._update_hier_filter_visibility()
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

    def _refresh_scope_banner(self) -> None:
        """State, in the dock itself, when the numbers are an isolated
        hierarchy run rather than whole-model results."""
        banner = getattr(self, "_scope_banner", None)
        if banner is None:
            return
        iso = (getattr(self, "_manifest", None) or {}).get("isolation")
        if not iso:
            banner.clear()
            banner.setToolTip("")
            banner.setVisible(False)
            return
        from html import escape
        name = escape(str(iso.get("hierarchy") or "hierarchy"))
        text = escape(str(iso.get("banner")
                           or f"Isolated run of {name}"))
        banner.setText(
            f"<b>{text}.</b> These results describe <b>{name}</b> alone; "
            "its input is assumed always available. Summary, tables, Weak "
            "Links, Capacity, Cost and importance are scoped to this "
            "hierarchy.")
        treatments = iso.get("treatments") or {}
        tips = [f"dependencies: {treatments.get('deps', '?')}",
                f"pools/resources: {treatments.get('pools', '?')}",
                f"random streams: {treatments.get('streams', '?')}"]
        for note in iso.get("notes") or []:
            tips.append(str(note))
        for hazard in iso.get("hazards") or []:
            if hazard.get("effect") and hazard.get("effect") != "information":
                tips.append(
                    f"{hazard.get('kind')}: "
                    f"{', '.join(hazard.get('elements') or [])} -> "
                    f"{hazard.get('treatment')} ({hazard.get('effect')})")
        banner.setToolTip("\n".join(tips))
        banner.setVisible(True)

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

        # Availability/Dependability as a decimal with at least four places
        # and up to the report's nine (0.998884325); rounding to a percent
        # would hide the digits reliability work turns on.
        self._cards["Availability"].set_value(
            probability_text(avail_m),
            f"min {avail_lo:.9f} · max {avail_hi:.9f}")
        # Reliability card: same full-precision treatment as the
        # Availability card.
        if rel_m is not None:
            self._cards["Reliability"].set_value(
                probability_text(rel_m),
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

    def _raw_preview_lines(self, src, max_lines: int | None = None,
                           max_bytes: int | None = None
                           ) -> tuple[list[str], int]:
        """(bounded head, total rows) for a raw source.

        `src` is a plain list or a disk-backed ResultStream. Only the head
        is ever read: the full report is O(runs x elements) and joining it
        is what used to crash the dock on a large batch.
        """
        if src is None:
            return [], 0
        max_lines = self._RAW_PREVIEW_LINES if max_lines is None else max_lines
        max_bytes = self._RAW_PREVIEW_BYTES if max_bytes is None else max_bytes
        total = len(src)
        if total == 0 or max_lines <= 0 or max_bytes <= 0:
            return [], total
        if hasattr(src, "read_preview"):
            return src.read_preview(max_lines, max_bytes), total
        shown: list[str] = []
        used = 0
        for line in itertools.islice(src, max_lines):
            shown.append(line)
            used += len(line) + 1
            if used >= max_bytes:
                break
        return shown, total

    def _raw_preview(self, src) -> tuple[str, int, int]:
        """(preview text, total rows, shown rows) for a raw source.

        A truncation note is appended only when something was actually
        left out, so a byte cap that lands exactly on the last line does
        not print "the first N of N".
        """
        shown, total = self._raw_preview_lines(src)
        text = "\n".join(shown)
        if len(shown) < total:
            text += (
                f"\n\n--- Showing the first {len(shown):,} of {total:,} "
                "line(s). Save Raw Report writes the full file. ---")
        return text, total, len(shown)

    def _populate_raw(self, end_of_run_lines, final_results_lines):
        self._raw_sources = {
            "End of Run": end_of_run_lines,
            "Final Results": final_results_lines,
        }
        for name, src in self._raw_sources.items():
            text, _total, _shown = self._raw_preview(src)
            self._raw_views[name].setPlainText(text)

    def raw_report_lines(self, name: str,
                         max_lines: int | None = None) -> tuple[list[str], int]:
        """(bounded lines, total lines) for a printable raw report.

        `collect_raw_report_sections` uses this so a printed report shows
        the real total, not the dock's preview cap, and never materialises
        a multi-million-line End-of-Run report just to lay out the first
        page and a half of it.
        """
        src = self._raw_sources.get(name)
        if src is None:
            view = self._raw_views.get(name)
            text = view.toPlainText() if view is not None else ""
            lines = text.splitlines()
            return (lines, len(lines)) if text.strip() else ([], 0)
        return self._raw_preview_lines(src, max_lines)

    # ------------------------------------------------------------------
    # Theming
    # ------------------------------------------------------------------

    def _refresh_theme(self, _dark: bool | None = None):
        c = active_colour()
        # Per-table action icons are painted from the palette tokens, so
        # they need re-painting when the theme changes (the icons module's
        # own contract). New tables pick up the new colour at build time.
        from PySide6.QtWidgets import QPushButton
        from raptor.gui.resources import icons
        for button in self.findChildren(QPushButton, "TableCopyButton"):
            button.setIcon(icons.copy_icon(c["text"]))
        for button in self.findChildren(QPushButton, "TableCsvButton"):
            button.setIcon(icons.csv_icon(c["text"]))
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
