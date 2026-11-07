"""Data Tables dock — spreadsheet-style grid view of every block/node.

The Inspector edits one element at a time; this grid shows all of them
with the same validated commit semantics: commit on editingFinished with
the full value, revert invalid input to the last-committed value, and
route every mutation through DiagramView's set_* methods (the single
source of truth) so canvas, explorer, and dirty-state stay in sync.

Distribution cells are combos showing the names from Raptor 7's
ConvertToString; changing one also updates that distribution's `#FParam`
count. Both columns are plain, non-lossy ints in typed.py, so the
population pass can write them back without conversion.
"""
from __future__ import annotations

from bisect import bisect_left
from typing import Any

from raptor.gui.ui.qtcompat import req

from raptor.gui.ui import msgbox

from PySide6.QtCore import Qt, Signal, QTimer, QEvent
from PySide6.QtGui import QDoubleValidator
from PySide6.QtWidgets import (
    QApplication,
    QWidget, QVBoxLayout, QTabWidget, QTableWidget, QTableWidgetItem,
    QCheckBox, QComboBox, QLabel, QHeaderView, QHBoxLayout, QPushButton,
)

from raptor.gui.ui import filedialogs as fdg
from raptor.gui.resources.design_tokens import ThemeManager, SPACING
from raptor.gui.ui.distro_combo import distro_display_name, fill_distro_combo
from raptor.gui.ui.table_filter_bar import FilterBar
from raptor.gui.ui import param_entry
from raptor.core.distro_rules import (
    DISTRO_PARAM_LABELS, validate_distro_params,
)
from raptor.core.distro_rules import DISTRO_NAMES
from raptor.core.fileio.rbdlib import DIST_NPARAMS as DIST_NPARAMS_COUNT
from raptor.core.engine.diststats import compute_stats


def _fmt_stat(val) -> str:
    if val is None:
        return "—"
    if isinstance(val, str):
        return val
    return f"{val:.4g}"


#: A grid with more rows than this builds its cell widgets (combos, entry
#: fields) only for the rows on screen plus a margin, and the rest as they
#: scroll into view (#146): a widget costs ~1 ms, a row has up to 9.
_LAZY_ROWS = 60
_LAZY_MARGIN = 12

#: Columns every grid shares; a grid's own tip for the same name wins.
_COMMON_COL_TIPS = {
    "Name": "The element's name",
    "ID": "The element's id, as the Explorer, the Inspector and exported "
          "tables show it in brackets (read-only)",
}


class _DataTable(QTableWidget):
    """A grid that refuses a sort on a cell-widget column.

    Distribution/parameter cells are widgets, not items. Qt still showed
    a sort arrow and reordered by empty keys, so the arrow lied. Treat
    those headers as non-sortable.
    """

    #: Set by the dock: called with the clipboard's text when Ctrl+V is
    #: pressed on the grid itself (not in a cell's editor). True = handled.
    paste_text = None

    def keyPressEvent(self, event) -> None:
        from PySide6.QtGui import QKeySequence
        if (self.paste_text is not None
                and event.matches(QKeySequence.StandardKey.Paste)):
            if self.paste_text(QApplication.clipboard().text()):
                event.accept()
                return
        super().keyPressEvent(event)

    def sortItems(self, column: int, order: Qt.SortOrder) -> None:
        if any(self.cellWidget(row, column) is not None
               for row in range(self.rowCount())):
            self.horizontalHeader().setSortIndicator(
                -1, Qt.SortOrder.AscendingOrder)
            return
        super().sortItems(column, order)


class _SortItem(QTableWidgetItem):
    """QTableWidgetItem that sorts numerically when both cells parse as
    numbers. The default item compares its text lexically, which puts
    "10" before "9" and "2" after "19" on every ID/param/count column."""

    def __lt__(self, other):
        try:
            return float(self.text()) < float(other.text())
        except (TypeError, ValueError):
            return self.text().casefold() < other.text().casefold()


#: Canonical parameter counts per distribution, from Raptor 7.
_NUM_PARAMS = {1: 2, 2: 1, 3: 2, 4: 2, 5: 2, 6: 3, 7: 2, 8: 2, 9: 2, 10: 2,
               11: 3, 12: 3, 13: 1, 14: 3, 15: 3, 16: 0, 17: 1, 18: 1,
               19: 1, 20: 3, 21: 2, 22: 2}


def _is_time_param(label: str) -> bool:
    """Same exclusion the Inspector's own `_distribution_params_row` uses: Shape,
    N Trials and Probability slots are never time-dimensioned
    regardless of the model's own units."""
    return bool(label) and label not in (
        "Shape", "Shape2", "N Trials", "Probability")


class _WholeModel:
    """Every level's blocks, nodes and events, read from the file's rows.

    The view's own `blocks`/`nodes`/`events` hold the level on screen only.
    Here the level on screen keeps its live objects (they are the ones the
    edit paths change) and every other level is built from its rows. Nothing
    is built on the canvas for another level. `level` names where an element
    lives ("Home", "Pump Bank", "Pump Bank / Stage 2")."""

    def __init__(self, view) -> None:
        from raptor.core.fileio.typed import (
            block_from_row, event_from_row, hier_from_row, node_from_row)
        rbd = view._rbd_ref
        self.live_blocks = set(view.blocks)
        self.live_nodes = set(view.nodes)
        self.live_events = set(view.events)
        self.blocks: dict = {}
        self.nodes: dict = {}
        self.events: dict = {}
        self._parent: dict[tuple[str, int], int] = {}
        hiers: dict[int, tuple[str, int]] = {}
        sec = rbd.sections.get("HIERARCHY_DATA")
        for row in (sec.rows if sec is not None else []):
            try:
                h = hier_from_row(row)
            except (ValueError, KeyError):
                continue
            if h.id:
                hiers[h.id] = (h.name, h.parent_id)
        self.level_count = 1 + len(hiers)
        names: dict[int, str] = {0: "Home"}

        def level_name(pid: int) -> str:
            if pid in names:
                return names[pid]
            chain, seen, cur = [], set(), pid
            while cur and cur in hiers and cur not in seen:
                seen.add(cur)
                chain.append(hiers[cur][0])
                cur = hiers[cur][1]
            names[pid] = " / ".join(reversed(chain)) or "Home"
            return names[pid]
        self._level_name = level_name
        for kind, section, build, live, store in (
                ("block", "BLOCK_DATA", block_from_row, view.blocks,
                 self.blocks),
                ("node", "NODE_DATA", node_from_row, view.nodes,
                 self.nodes),
                ("event", "EVENT_DATA", event_from_row, view.events,
                 self.events)):
            sec = rbd.sections.get(section)
            for row in (sec.rows if sec is not None else []):
                try:
                    obj = build(row)
                except (ValueError, KeyError):
                    continue
                if obj.id == 0 and kind != "node":
                    continue          # the column-template row
                if kind == "node" and obj.id == 0:
                    continue
                store[obj.id] = live.get(obj.id, obj)
                self._parent[(kind, obj.id)] = obj.parent_id

    def level(self, kind: str, oid: int) -> str:
        return self._level_name(self._parent.get((kind, oid), 0))

    def is_live(self, kind: str, oid: int) -> bool:
        return oid in {"block": self.live_blocks, "node": self.live_nodes,
                       "event": self.live_events}[kind]


class DataTablesDock(QWidget):
    #: Emitted after ANY committed edit so MainWindow can mark dirty.
    model_edited = Signal()
    #: Right-click "Go to element" on a Blocks/Nodes row -- (kind, name).
    #: Same shape as results.py's element_clicked; MainWindow wires both
    #: to the one DiagramView.locate_element-backed handler.
    element_locate_requested = Signal(str, str)
    #: The same, by id and kind: used in Whole model, where two levels can
    #: hold elements of one name.
    element_locate_id_requested = Signal(str, int)
    #: A line for the window's status bar ("Set ... on 12 rows").
    status_message = Signal(str)
    #: A committed cell failed distribution-parameter validation and was
    #: reverted. Shares its name with `InspectorDock.validation_failed`,
    #: whose handler MainWindow also connects this one to.
    validation_failed = Signal(str)
    #: The "Export Tables..." button: open the window's Export Tables dialog.
    export_tables_requested = Signal()

    _BLOCK_COLS = ["Name", "ID", "Failure Dist",
                   "Failure Param 1", "Failure Param 2",
                   "Failure Param 3", "# Failure Params",
                   "Repair Dist", "Repair Param 1",
                   "Repair Param 2", "Repair Param 3",
                   "# Repair Params", "PM Frequency",
                   "Failure Mean", "Failure Std Dev",
                   "Repair Mean", "Repair Std Dev", "Level"]

    #: Column header tooltips -- plain-English meaning, since "Failure
    #: Param 1" vs "Param1" vs "Param2" says nothing on its own. Per-cell
    #: tooltips (_apply_param_tips) explain what a specific row's
    #: distribution uses a slot for; these describe the column in general.
    _BLOCK_COL_TIPS = {
        "Failure Dist": "Probability distribution modelling this "
            "block's time to failure",
        "Failure Param 1": "1st parameter of the Failure Dist (meaning "
            "depends on which distribution -- hover the cell itself for "
            "this row's specific meaning)",
        "Failure Param 2": "2nd parameter of the Failure Dist (blank if "
            "that distribution doesn't use one)",
        "Failure Param 3": "3rd parameter of the Failure Dist (blank if "
            "that distribution doesn't use one)",
        "# Failure Params": "How many of the three Failure Param "
            "columns this distribution actually uses (read-only, "
            "follows the Failure Dist choice)",
        "Repair Dist": "Probability distribution modelling this "
            "block's repair time",
        "Repair Param 1": "1st parameter of the Repair Dist (meaning "
            "depends on which distribution -- hover the cell itself for "
            "this row's specific meaning)",
        "Repair Param 2": "2nd parameter of the Repair Dist (blank if "
            "that distribution doesn't use one)",
        "Repair Param 3": "3rd parameter of the Repair Dist (blank if "
            "that distribution doesn't use one)",
        "# Repair Params": "How many of the three Repair Param columns "
            "this distribution actually uses (read-only, follows the "
            "Repair Dist choice)",
        "PM Frequency": "How often (model time units) this block "
            "undergoes Preventive Maintenance",
        "Failure Mean": "Computed mean of the Failure Dist as "
            "currently parameterized (read-only)",
        "Failure Std Dev": "Computed standard deviation of the Failure "
            "Dist as currently parameterized (read-only)",
        "Repair Mean": "Computed mean of the Repair Dist as currently "
            "parameterized (read-only)",
        "Repair Std Dev": "Computed standard deviation of the Repair "
            "Dist as currently parameterized (read-only)",
    }
    _NODE_COL_TIPS = {
        "Type": "Regular (k-out-of-n) / Start / End / Hierarchy In-Out",
        "Required good (k)": "k-out-of-n: how many of this node's "
            "input paths must be up for the node itself to count as up",
        "Out": "Number of outgoing links from this node",
    }
    _EVENT_COL_TIPS = {
        "Distro": "Probability distribution for this event's outcome "
            "(usually Point Estimate -- a fixed success probability)",
        "Param": "The distribution's parameter (for Point Estimate: "
            "probability of success, 0-1)",
        "Stream": "Random-number stream this event draws from",
    }
    #: Raptor 7's "Block Input Tables" -- Sparing and Preventive
    #: Maintenance tabs.
    _SPARING_COLS = ["Name", "ID", "Sparing Type", "Pool Name",
                     "Resource", "New Spares", "Order Interval", "Level"]
    _SPARING_COL_TIPS = {
        "Sparing Type": "How this block gets replacement units: "
            "Infinite/Custom/Pooled/ColdPool/Resource",
        "Pool Name": "Spare pool this block draws from (Pooled sparing "
            "only -- see the Spare Pools tab)",
        "Resource": "Resource pool this block draws from (Resource "
            "sparing only -- see the Resources tab)",
        "New Spares": "Spares acquired per order (Custom sparing only)",
        "Order Interval": "How often a Custom-sparing order arrives",
    }
    #: The manual says "the last five columns... represent the five "
    #: preventive maintenance check boxes" -- Raptor 7's PM tab
    #: "only lists components for which we checked the preventive
    #: maintenance checkbox", matched here by filtering rows to
    #: `block.uses_pm`.
    _PM_COLS = ["Name", "ID", "Frequency", "Needs spare", "Refresh",
               "Defer (non-mission)", "Defer (unavailable)",
               "Failure resets", "Level"]
    _PM_COL_TIPS = {
        "Frequency": "How often (model time units) this block "
            "undergoes Preventive Maintenance",
        "Needs spare": "PM cannot start until a spare is available",
        "Refresh": "After PM completes, this block is treated as new",
        "Defer (non-mission)": "PM waits for a non-mission phase "
            "(only meaningful when this model has phases defined)",
        "Defer (unavailable)": "PM waits until a spare or resources "
            "are available",
        "Failure resets": "A failure resets the time to the next PM "
            "activity",
    }
    _KEYED_COL_TIPS = {
        "RSO": "Routine Spare Ordering: reorder on a fixed schedule, "
            "before stock runs out",
        "ESO": "Emergency Spare Ordering: rush-order when stock hits "
            "zero and a block needs a spare now",
        "SLO": "Stock-Level Ordering: reorder once stock drops to a "
            "threshold",
        "ArriveEvery": "How often a routine spare order arrives",
        "EmerTime": "How long an emergency spare takes to arrive",
        "Level": "Stock level that triggers an SLO reorder",
        "Quan": "How many spares an SLO reorder brings in",
        "SLOTime": "How long an SLO reorder takes to arrive",
        "NumIn": "Number of resources in this pool",
        "SpareCost": "Cost of purchasing one spare",
        "FixedPerUse": "Fixed cost charged each time this resource is "
            "used",
        "PerTimeUnit": "Cost per model-time-unit while this resource "
            "is in use",
        "Phases": "Number of mission phases this resource pool spans",
        "IDnum": "Internal trigger id (read-only)",
        "Repeats": "0 = fires once; otherwise how many times this "
            "trigger repeats",
        "InitUsed": "Offset before this trigger's first firing",
        "Dist": "Distribution for this row's frequency/length "
            "(Triggers) or duration (Phases)",
        "Param1": "1st parameter of Dist (meaning depends on which "
            "distribution -- hover the row's Dist cell)",
        "Param2": "2nd parameter of Dist (blank if unused)",
        "Param3": "3rd parameter of Dist (blank if unused)",
        "Num": "Internal phase id (read-only)",
        "Mission": "Whether this phase counts toward mission time "
            "(vs. a non-mission phase like standby/storage)",
    }

    #: Plain (non-Param) time-dimensioned columns per keyed section --
    #: display-unit override (`_display_unit`) applies to these, same
    #: as it already does for the Blocks table's own Fail/Repair Param
    #: cells (`_param_cell`'s `is_time`). Param1-3 aren't listed here:
    #: their own time-ness is PER-ROW, decided by that row's current
    #: Dist (see `_populate_keyed`'s use of `_is_time_param`).
    _KEYED_TIME_FIELDS = {
        "SPARE_POOL_DATA": {"ArriveEvery", "EmerTime", "SLOTime"},
        "TRIGGERS": {"InitUsed"},
    }

    #: Per-position parameter meaning for the common distributions,
    #: used as header tooltips on the F/R Param columns.
    _PARAM_TIPS = {
        4:  ("Mean (hours)", "", ""),
        7:  ("Mean", "Std Dev", ""),
        15: ("Shape (beta)", "Scale (eta)", "Location"),
        14: ("Min", "Mode", "Max"),
        10: ("Min", "Max", ""),
        8:  ("Mean", "Std Dev", ""),
        6:  ("Shape", "Scale", ""),
        5:  ("Shape", "Scale", ""),
        11: ("Shape", "Rate", "Location"),
        13: ("Mean (events)", "", ""),
        1:  ("Shape a", "Shape b", ""),
    }

    def __init__(self, parent=None):
        super().__init__(parent)
        self._view = None
        #: "Whole model": every level's rows, not just the one on screen.
        #: Kept for the session (it is a view of the model, not part of it).
        self._whole = False
        self._multi_confirmed = False     # "Don't ask again": session only
        self._whole_src: _WholeModel | None = None
        #: A refresh was requested while this dock was hidden. The grids are
        #: expensive for large models (one widget per editable cell), so a
        #: hidden Data Tables tab is filled when it is first shown instead
        #: of during Open.
        self._refresh_pending = False
        #: Tabs left to fill when only the visible grid is built. Opening a
        #: flat 1,500- or 3,800-block model otherwise spends tens of seconds
        #: building cell widgets in tabs nobody is looking at.
        self._dirty_tables: set[QTableWidget] = set()
        self._tabs = QTabWidget()
        self._tabs.setDocumentMode(True)

        self._blocks_table = self._make_table(
            self._BLOCK_COLS, self._BLOCK_COL_TIPS)
        self._nodes_table = self._make_table(
            ["Name", "ID", "Type", "Required good (k)", "Out", "Level"],
            self._NODE_COL_TIPS,
            # wider than default: "Required good (k)" was clipping
            wide_cols={"Required good (k)": 150})
        self._events_table = self._make_table(
            ["Name", "ID", "Distro", "Param", "Stream", "Level"],
            self._EVENT_COL_TIPS)
        # Name-keyed sections (no ID column): pools and triggers.
        self._spares_table = self._make_table(
            ["PoolName", "RSO", "Stock", "NewSp", "ArriveEvery", "ESO",
             "EmerTime", "SLO", "Level", "Quan", "SLOTime"],
            self._KEYED_COL_TIPS)
        self._resources_table = self._make_table(
            ["PoolName", "NumIn", "SpareCost", "FixedPerUse",
             "PerTimeUnit", "Phases"], self._KEYED_COL_TIPS)
        self._triggers_table = self._make_table(
            ["TriggerName", "IDnum", "Repeats", "InitUsed", "Dist",
             "Param1", "Param2", "Param3"], self._KEYED_COL_TIPS)
        # Phase definitions -- same
        # keyed-row shape as pools/triggers.
        self._phases_table = self._make_table(
            ["PhaseName", "Num", "Mission", "Dist", "Param1", "Param2",
             "Param3"], self._KEYED_COL_TIPS)
        self._sparing_table = self._make_table(
            self._SPARING_COLS, self._SPARING_COL_TIPS)
        self._pm_table = self._make_table(
            self._PM_COLS, self._PM_COL_TIPS)
        self._blocks_table.itemChanged.connect(self._on_block_name_changed)
        self._nodes_table.itemChanged.connect(self._on_node_name_changed)
        self._events_table.itemChanged.connect(self._on_event_name_changed)
        self._pm_table.itemChanged.connect(self._on_pm_cell_changed)
        self._apply_level_column()
        from PySide6.QtGui import QKeySequence, QShortcut
        fill = QShortcut(QKeySequence("Ctrl+D"), self)
        fill.setContext(Qt.ShortcutContext.WidgetWithChildrenShortcut)
        fill.activated.connect(self.fill_down)
        for _t, _k in self._level_tables():
            _t.paste_text = self.paste_into_selection
            _t.selectionModel().selectionChanged.connect(self._update_banner)
        self._tabs.currentChanged.connect(self._update_banner)
        self._tabs.addTab(self._blocks_table, "Blocks")
        self._tabs.addTab(self._nodes_table, "Nodes")
        self._tabs.addTab(self._events_table, "Events")
        self._tabs.addTab(self._spares_table, "Spare Pools")
        self._tabs.addTab(self._resources_table, "Resources")
        self._tabs.addTab(self._triggers_table, "Triggers")
        self._tabs.addTab(self._phases_table, "Phases")
        self._tabs.addTab(self._sparing_table, "Sparing")
        self._tabs.addTab(self._pm_table, "Preventive Maintenance")

        # Keyed-row tables: column -> kind for commit validation.
        self._keyed = {
            self._spares_table: ("SPARE_POOL_DATA", "PoolName",
                                 {"PoolName": str, "RSO": int,
                                  "Stock": int, "NewSp": int,
                                  "ArriveEvery": float, "ESO": int,
                                  "EmerTime": float, "SLO": int,
                                  "Level": int, "Quan": int,
                                  "SLOTime": float}),
            self._resources_table: ("RESOURCE_DATA", "PoolName",
                                    {"PoolName": str, "NumIn": int,
                                     "SpareCost": float,
                                     "FixedPerUse": float,
                                     "PerTimeUnit": float,
                                     "Phases": int}),
            self._triggers_table: ("TRIGGERS", "TriggerName",
                                   {"TriggerName": str, "IDnum": int,
                                    "Repeats": int, "InitUsed": float,
                                    "Dist": int, "Param1": float,
                                    "Param2": float, "Param3": float}),
            self._phases_table: ("PHASES", "PhaseName",
                                 {"PhaseName": str, "Num": int,
                                  "Mission": int, "Dist": int,
                                  "Param1": float, "Param2": float,
                                  "Param3": float}),
        }
        for table in self._keyed:
            table.itemChanged.connect(self._on_keyed_cell_changed)
        self._all_tables = [
            self._blocks_table, self._nodes_table, self._events_table,
            self._spares_table, self._resources_table,
            self._triggers_table, self._phases_table,
            self._sparing_table, self._pm_table,
        ]
        # Grids whose cell widgets are built on demand: table -> builder,
        # and the row keys (block ids) still waiting for theirs.
        self._lazy_builders = {
            self._blocks_table: self._build_block_widgets,
            self._sparing_table: self._build_sparing_widgets,
        }
        self._lazy_pending: dict[QTableWidget, set[int]] = {
            t: set() for t in self._lazy_builders}
        for lazy_table in self._lazy_builders:
            lazy_table.verticalScrollBar().valueChanged.connect(
                lambda _v, t=lazy_table: self._realize_visible(t))
            lazy_table.viewport().installEventFilter(self)
            # Anything that reads every row's widgets (CSV and table
            # export) builds the rest first: see `table_export`.
            lazy_table.realize_all = (    # type: ignore[attr-defined]
                lambda t=lazy_table: self._realize_all(t))
        self._sort_choice: dict[QTableWidget, tuple[int, Qt.SortOrder]] = {}
        self._sort_restoring = False
        for table in self._all_tables:
            table.horizontalHeader().sortIndicatorChanged.connect(
                lambda section, order, t=table: self._on_sort_indicator(
                    t, section, order))
            table.horizontalHeader().sortIndicatorChanged.connect(
                lambda *_a, t=table: QTimer.singleShot(
                    0, lambda t=t: self._sync_row_headers(t)))
            table.horizontalHeader().sortIndicatorChanged.connect(
                lambda *_a, t=table: QTimer.singleShot(
                    0, lambda t=t: self._realize_visible(t)))
        self._attach_filters()
        self._tabs.currentChanged.connect(self._on_tab_changed)

        # Click a row -> jump to it on the canvas (entering its hierarchy
        # level first if needed). Left-click covers the common case; a
        # right-click menu is also available for users who expect a click
        # to only select a cell for editing.
        from PySide6.QtCore import Qt as _Qt
        for table, kind in ((self._blocks_table, "block"),
                            (self._nodes_table, "node")):
            table.setContextMenuPolicy(_Qt.ContextMenuPolicy.CustomContextMenu)
            table.customContextMenuRequested.connect(
                lambda pos, t=table, k=kind: self._show_row_context_menu(t, k, pos))
            table.itemClicked.connect(
                lambda item, k=kind: self._goto_clicked_row(item, k))

        bar = QHBoxLayout()
        bar.setContentsMargins(SPACING["sm"], SPACING["xs"],
                               SPACING["sm"], SPACING["xs"])
        bar.setSpacing(SPACING["sm"])      # the buttons used to touch
        hint = QLabel("Edits apply to the model immediately "
                      "(Save to persist). Invalid values revert.")
        hint.setObjectName("ResultsPlaceholder")
        bar.addWidget(hint)
        # Blocks/Nodes/Events are the CURRENT hierarchy level's rows.
        # Without saying so, a model with hierarchy levels looked as if
        # its children were missing.
        self._scope_label = QLabel("")
        self._scope_label.setObjectName("SectionNote")
        bar.addSpacing(12)
        bar.addWidget(self._scope_label)
        refresh = QPushButton("Refresh")
        refresh.clicked.connect(self.refresh)
        self._whole_box = QCheckBox("Whole model")
        self._whole_box.setToolTip(
            "Show the blocks, nodes and events of every hierarchy level, "
            "with a Level column. Rows on the open level stay editable; "
            "rows of other levels are read-only (right-click \u25b8 Go to "
            "level).")
        self._whole_box.toggled.connect(self._on_whole_toggled)
        bar.addSpacing(SPACING["md"])
        bar.addWidget(self._whole_box)
        self._banner = QLabel("")
        self._banner.setObjectName("MultiEditBanner")
        self._banner.setStyleSheet(
            "QLabel#MultiEditBanner { background: #fff3c4; color: #5c4400;"
            " border: 1px solid #e0c060; border-radius: 3px;"
            " padding: 1px 8px; }")
        self._banner.hide()
        bar.addWidget(self._banner)
        bar.addStretch(1)
        self._add_row_btn = QPushButton("Add Row")
        self._add_row_btn.setVisible(False)
        self._add_row_btn.clicked.connect(self._add_keyed_row)
        bar.addWidget(self._add_row_btn)
        self._del_row_btn = QPushButton("Remove Row")
        self._del_row_btn.setVisible(False)
        self._del_row_btn.clicked.connect(self._remove_selected_keyed_row)
        bar.addWidget(self._del_row_btn)
        export_btn = QPushButton("Export CSV…")
        export_btn.setToolTip(
            "Save the currently visible table (the model's own INPUT "
            "data — Blocks/Nodes/Events/Spare Pools/Resources/"
            "Triggers/Phases) as a .csv — this grid had no export "
            "path before")
        export_btn.clicked.connect(self._export_current_table_csv)
        bar.addWidget(export_btn)
        tables_btn = QPushButton("Export Tables…")
        tables_btn.setToolTip(
            "Write all the model's tables (and a run's results) as CSV, "
            "text or one spreadsheet with a tab per table")
        tables_btn.clicked.connect(self.export_tables_requested)
        bar.addWidget(tables_btn)
        bar.addWidget(refresh)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)
        layout.addLayout(bar)
        layout.addWidget(self._tabs, 1)

        ThemeManager.instance().on_changed(self._on_theme_changed)

    @staticmethod
    def _make_table(headers: list[str], tips: dict[str, str] | None = None,
                    wide_cols: dict[str, int] | None = None) -> _DataTable:
        t = _DataTable(0, len(headers))
        t.setHorizontalHeaderLabels(headers)
        t.verticalHeader().setVisible(False)
        t.setAlternatingRowColors(True)
        t.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Interactive)
        # data grid is dense: default wider columns so names/params are
        # readable without dragging; tooltips carry meaning
        t.horizontalHeader().setDefaultSectionSize(110)
        for col, name in enumerate(headers):
            item = t.horizontalHeaderItem(col)
            if item is None:
                continue
            tip = (tips or {}).get(name) or _COMMON_COL_TIPS.get(name)
            if tip:
                item.setToolTip(tip)
            width = (wide_cols or {}).get(name)
            if width:
                t.horizontalHeader().resizeSection(col, width)
        # Click a column header to sort by that value. Rows carry their
        # ID cells with them (every commit handler reads the id from the
        # row, not its position), so reordering is safe; `_SortItem`
        # keeps ID/param/count columns numeric-sorted. Sorting is off
        # while populating (see `refresh`): insertRow with an active sort
        # indicator scatters rows.
        t.setSortingEnabled(True)
        return t

    #: Blocks-table columns that are genuine sim-time quantities. They
    #: get the model's configured unit appended, matching results.py's
    #: MTBDE/MDT/etc. columns.
    _TIME_COLS = {"PM Frequency", "Failure Mean", "Failure Std Dev",
                  "Repair Mean", "Repair Std Dev"}

    @staticmethod
    def _ensure_header_widths(table: QTableWidget) -> None:
        """Widen columns whose header text would otherwise clip mid-word."""
        header = table.horizontalHeader()
        metrics = header.fontMetrics()
        for col in range(table.columnCount()):
            item = table.horizontalHeaderItem(col)
            if item is None:
                continue
            need = metrics.horizontalAdvance(item.text()) + 24
            if table.columnWidth(col) < need:
                table.setColumnWidth(col, need)

    def _apply_time_headers(self) -> None:
        """Label time columns with the unit their values are shown in.

        The native model unit and the optional display-unit override
        must move together; leaving the headers on "hours" while the
        cells were converted to days was the visible half of a mixed-unit
        grid.
        """
        unit = (self._display_unit or self._native_units
                or self._units or "").strip()
        base_by_stripped = {c.split(" (")[0]: c for c in self._BLOCK_COLS}
        for col in range(self._blocks_table.columnCount()):
            item = self._blocks_table.horizontalHeaderItem(col)
            if item is None:
                continue
            base = base_by_stripped.get(item.text().split(" (")[0])
            if base is None or base not in self._TIME_COLS:
                continue
            item.setText(f"{base} ({unit})" if unit else base)
        for table, name in ((self._sparing_table, "Order Interval"),
                            (self._pm_table, "Frequency")):
            for col in range(table.columnCount()):
                item = table.horizontalHeaderItem(col)
                if item is not None and item.text().split(" (")[0] == name:
                    item.setText(f"{name} ({unit})" if unit else name)
        for table, (section, _key, _kinds) in self._keyed.items():
            fields = self._KEYED_TIME_FIELDS.get(section, set())
            for col in range(table.columnCount()):
                item = table.horizontalHeaderItem(col)
                if item is None:
                    continue
                base = item.text().split(" (")[0]
                if base in fields:
                    item.setText(f"{base} ({unit})" if unit else base)
        for table in getattr(self, "_all_tables", ()):
            self._ensure_header_widths(table)

    def set_units(self, unit: str) -> None:
        """Set the model's native time unit and relabel the time columns.

        The model unit is the conversion basis for every time-valued
        cell; a native-unit edit must replace it, not leave the old one
        in place.
        """
        self._units = (unit or "").strip()
        self._native_units = self._units
        self._apply_time_headers()

    def set_view(self, view, *, defer_large: bool = False):
        self._view = view
        # Display-unit override for this grid's own time-dimensioned
        # cells: Failure/Repair Param columns and PM Frequency.
        self._native_units = ""
        self._display_unit = None
        if view is not None and getattr(view, "_rbd_ref", None) is not None:
            try:
                from raptor.core.fileio.typed import system_config_from_row
                self._native_units = system_config_from_row(
                    view._rbd_ref.sections["SYSTEM_DATA"].rows[0]).units
            except (ValueError, KeyError, IndexError, AttributeError):
                pass
            self._display_unit = getattr(view._rbd_ref, "display_unit",
                                         None)
        if defer_large and self._large_model() and self.window().isVisible():
            # The model itself is open and usable; the grids can paint
            # themselves once the event loop reaches this dock. Otherwise a
            # 3,800-block model spent most of its Open time building cell
            # widgets behind the busy indicator. A dock that is already on
            # screen still needs one deferred fill: a hidden/tabbed dock
            # waits for its own show or paint.
            self._refresh_pending = True
            if not self.visibleRegion().isEmpty():
                QTimer.singleShot(0, self._populate_if_pending)
            else:
                self.update()
            return
        self.refresh(force=False)

    def set_display_unit(self, unit: str | None):
        """Live-change the display unit for the currently bound model
        (mirrors `InspectorDock.set_display_unit`) -- redraws every
        time cell immediately."""
        self._display_unit = unit
        if self._view is not None and \
                getattr(self._view, "_rbd_ref", None) is not None:
            self._view._rbd_ref.display_unit = unit
        self._apply_time_headers()
        self.refresh(force=False)

    # ------------------------------------------------------------------

    def _on_theme_changed(self, _dark: bool):
        """ThemeManager callback (weakref-registered): re-read tokens."""
        self.refresh(force=False)

    def _on_sort_indicator(self, table: QTableWidget, section: int,
                           order: Qt.SortOrder) -> None:
        """Remember a USER sort; programmatic restore is ignored."""
        if not self._sort_restoring and section >= 0:
            if any(table.cellWidget(row, section) is not None
                   for row in range(table.rowCount())):
                return
            self._sort_choice[table] = (section, order)

    def _sync_row_headers(self, table: QTableWidget) -> None:
        """Keep the row identity visible while the grid scrolls sideways.

        The Blocks grid is ~1,870 px wide for a ~1,385 px viewport. A
        normal horizontal header scrolls Name/ID away, leaving only the
        tooltip to say which block a parameter belongs to. The vertical
        header repeats the name and never scrolls horizontally.
        """
        try:
            from shiboken6 import isValid
            if not isValid(table):
                return
            names = []
            for row in range(table.rowCount()):
                item = table.item(row, 0)
                names.append(item.text() if item is not None else "")
            table.setVerticalHeaderLabels(names)
            header = table.verticalHeader()
            header.setVisible(bool(names))
            if names:
                header.setFixedWidth(105)
                for row, name in enumerate(names):
                    item = table.verticalHeaderItem(row)
                    if item is not None:
                        item.setToolTip(name)
        except RuntimeError:
            return

    def _defer_population(self) -> bool:
        """True when the grids can wait until this dock is shown.

        Opening a 500-block model used to spend ~23 s building ~7,000 cell
        widgets in a tab the user was not looking at. A hidden tab is only
        filled on `showEvent`; a standalone dock, or one whose window is not
        shown at all (headless tests, exports), still builds eagerly.
        """
        parent = self.parent()
        if parent is None:
            return False
        if not self.window().isVisible():
            return False
        # `isVisible()` stays true for a non-current tabified dock; its
        # visible region is empty until the tab is raised.
        return self.visibleRegion().isEmpty()

    def showEvent(self, event):
        super().showEvent(event)
        self._populate_if_pending()

    def paintEvent(self, event):
        # Raising a tabified dock does not send the child a Show event
        # (its `isVisible()` was already true); the first paint is the
        # reliable "the user can see this now" signal.
        self._populate_if_pending()
        super().paintEvent(event)

    def _populate_if_pending(self):
        if not self._refresh_pending:
            return
        self._refresh_pending = False
        QTimer.singleShot(0, lambda: self.refresh(force=True))

    def refresh(self, force: bool = True):
        if not force and self._defer_population():
            self._refresh_pending = True
            return
        self._refresh_pending = False
        # Preserve the current row/cell: model edits and undo/redo now
        # refresh this grid, and a plain repopulate would otherwise throw
        # the user back to the top after every change.
        current = self._tabs.currentWidget()
        keep_key = None
        keep_col = 0
        keep_scroll = 0
        if isinstance(current, QTableWidget):
            row = current.currentRow()
            keep_col = max(0, current.currentColumn())
            keep_scroll = current.verticalScrollBar().value()
            if row >= 0:
                key_col = (1 if current in
                           (self._blocks_table, self._nodes_table,
                            self._events_table) else 0)
                item = current.item(row, key_col)
                keep_key = item.text() if item is not None else None
            # An edit that makes this row stop matching a filter must not
            # hide it under the cursor: keep it shown until the next time
            # the user types in a filter box.
            bar = getattr(self, "_filter_bars", {}).get(current)
            if bar is not None and bar.is_active() and keep_key is not None:
                try:
                    bar.keep_visible = {int(keep_key)}
                except ValueError:
                    pass
        # Sorting OFF while rows are being inserted (an active sort
        # indicator + insertRow = scattered rows); back on after.
        _all_tables: list[QTableWidget] = [
            self._blocks_table, self._nodes_table, self._events_table,
            self._spares_table, self._resources_table,
            self._triggers_table, self._phases_table,
            self._sparing_table, self._pm_table,
        ]
        # A flat 1,500- or 3,800-block model has nine grids, most of them
        # hidden behind a tab. Filling all of them made Open spend tens of
        # seconds building widgets no one could see. Build the visible grid
        # now and mark the rest for their first tab change.
        fill: list[QTableWidget]
        if self._large_model() and isinstance(current, QTableWidget):
            self._dirty_tables = set(_all_tables) - {current}
            fill = [current]
        else:
            self._dirty_tables.clear()
            fill = list(_all_tables)
        sort_state = {
            t: self._sort_choice.get(
                t, (-1, t.horizontalHeader().sortIndicatorOrder()))
            for t in fill}
        for t in fill:
            t.setSortingEnabled(False)
        for t in fill:
            self._populate_table(t)
        for t in fill:
            self._label_cell_widgets(t)
        self._sort_restoring = True
        try:
            for t in fill:
                # Restore the user's sort, not a fixed ID order.
                # Refreshing after every model edit used to clear the
                # indicator and snap a sorted grid back to populate order.
                section, order = sort_state.get(
                    t, (-1, Qt.SortOrder.AscendingOrder))
                t.horizontalHeader().setSortIndicator(section, order)
                t.setSortingEnabled(True)
        finally:
            self._sort_restoring = False
        if isinstance(current, QTableWidget) and keep_key is not None:
            key_col = (1 if current in
                       (self._blocks_table, self._nodes_table,
                        self._events_table) else 0)
            for row in range(current.rowCount()):
                item = current.item(row, key_col)
                if item is not None and item.text() == keep_key:
                    current.setCurrentCell(row, keep_col)
                    current.selectRow(row)
                    current.verticalScrollBar().setValue(keep_scroll)
                    break
        for table in fill:
            self._realize_visible(table)        # the sort moved rows
            self._ensure_header_widths(table)
            self._sync_row_headers(table)
            self._update_empty_state(table)
        self._update_scope_note()

    def _large_model(self) -> bool:
        """True when filling every grid at once would visibly stall Open."""
        view = self._view
        if view is None:
            return False
        try:
            src = self._src()
            return (len(getattr(src, "blocks", {}) or {})
                    + len(getattr(src, "nodes", {}) or {})
                    + len(getattr(src, "events", {}) or {})) > 400
        except TypeError:
            return False

    def _populate_table(self, table: QTableWidget) -> None:
        """Fill one grid, so a tab can be built on first use."""
        self._dirty_tables.discard(table)
        if table is self._blocks_table:
            self._populate_blocks()
        elif table is self._nodes_table:
            self._populate_nodes()
        elif table is self._events_table:
            self._populate_events()
        elif table is self._sparing_table:
            self._populate_sparing()
        elif table is self._pm_table:
            self._populate_pm()
        elif table in self._keyed:
            self._populate_keyed(table)
        for t, kind in self._level_tables():
            if t is table:
                self._lock_foreign_rows(t, kind)

    def _update_empty_state(self, table: QTableWidget) -> None:
        """Explain an empty grid instead of showing a blank pane."""
        labels = getattr(self, "_empty_labels", None)
        if labels is None:
            labels = self._empty_labels = {}
        label = labels.get(table)
        if table.rowCount():
            if label is not None:
                label.hide()
            return
        if label is None:
            label = QLabel(table.viewport())
            label.setObjectName("SectionNote")
            label.setAlignment(Qt.AlignmentFlag.AlignCenter)
            label.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
            labels[table] = label
            # Geometry set once at creation went stale as soon as the
            # dock/tab was laid out, clipping the note outside the
            # viewport. Follow the viewport's own resizes.
            table.viewport().installEventFilter(self)
        idx = self._tabs.indexOf(table)
        title = self._tabs.tabText(idx) if idx >= 0 else "rows"
        if table is self._blocks_table:
            label.setText("No blocks on this level — open a hierarchy "
                          "or use the model-wide level.")
        else:
            label.setText(f"No {title.lower()} in this model.")
        label.setGeometry(table.viewport().rect())
        label.raise_()
        label.show()

    def eventFilter(self, watched, event):
        """Keep each empty-state note the size of its table viewport, and
        build the widgets of rows a resize or first show uncovers."""
        if event.type() in (QEvent.Type.Resize, QEvent.Type.Show):
            for table in self._lazy_builders:
                if watched is table.viewport():
                    QTimer.singleShot(
                        0, lambda t=table: self._realize_visible(t))
                    break
        if event.type() == QEvent.Type.Resize:
            for table, label in getattr(self, "_empty_labels", {}).items():
                if watched is table.viewport():
                    label.setGeometry(table.viewport().rect())
                    break
        return super().eventFilter(watched, event)

    def _src(self) -> Any:
        """Where the Blocks/Nodes/Events/Sparing/PM grids read their
        elements from: the view's level, or (Whole model) every level."""
        view = self._view
        if view is None or not self._whole:
            return view
        src = self._whole_src
        if src is None:
            src = self._whole_src = _WholeModel(view)
        return src

    def _level_tables(self) -> list[tuple[QTableWidget, str]]:
        return [(self._blocks_table, "block"), (self._nodes_table, "node"),
                (self._events_table, "event"),
                (self._sparing_table, "block"), (self._pm_table, "block")]

    def _apply_level_column(self) -> None:
        """The Level column exists only in Whole model."""
        for t, _kind in self._level_tables():
            t.setColumnHidden(t.columnCount() - 1, not self._whole)

    def _level_item(self, kind: str, oid: int) -> QTableWidgetItem:
        src = self._src()
        text = src.level(kind, oid) if isinstance(src, _WholeModel) else ""
        item = _SortItem(text)
        item.setFlags(item.flags() & ~Qt.ItemFlag.ItemIsEditable)
        return item

    def _is_foreign(self, kind: str, oid: int | None) -> bool:
        """A row of another level (Whole model): shown, not editable."""
        src = self._src()
        return (isinstance(src, _WholeModel) and oid is not None
                and not src.is_live(kind, oid))

    _FOREIGN_TIP = ("On another level: read-only here. Right-click and "
                    "choose Go to level to edit it on the canvas.")

    def _lock_row(self, t: QTableWidget, r: int) -> None:
        """Make one row read-only (it belongs to a level that is not open)."""
        for c in range(t.columnCount()):
            item = t.item(r, c)
            if item is not None:
                item.setFlags(item.flags() & ~(
                    Qt.ItemFlag.ItemIsEditable
                    | Qt.ItemFlag.ItemIsUserCheckable))
            w = t.cellWidget(r, c)
            if w is not None:
                w.setEnabled(False)
        name = t.item(r, 0)
        if name is not None:
            name.setToolTip(self._FOREIGN_TIP)

    def _lock_foreign_rows(self, t: QTableWidget, kind: str) -> None:
        if not self._whole:
            return
        for r in range(t.rowCount()):
            if self._is_foreign(kind, self._row_key(t, r)):
                self._lock_row(t, r)

    # ------------------------------------------------------------------
    # Multi-row column edit: select rows, edit one cell, change them all
    # ------------------------------------------------------------------

    _GRID_KIND = {"_blocks_table": "block", "_nodes_table": "node",
                  "_events_table": "event", "_sparing_table": "block",
                  "_pm_table": "block"}

    def _edit_table(self) -> QTableWidget:
        """The grid an edit came from: the tab on screen."""
        cur = self._tabs.currentWidget()
        if isinstance(cur, QTableWidget) and cur in dict(self._level_tables()):
            return cur
        return self._blocks_table

    def _kind_of(self, table: QTableWidget) -> str:
        return dict(self._level_tables()).get(table, "block")

    def _selected_row_keys(self, table: QTableWidget) -> list[int]:
        """The ids of the selected rows that are shown (a filtered-out row is
        never touched), in row order."""
        sm = table.selectionModel()
        if sm is None:
            return []
        keys: list[int] = []
        for r in sorted({i.row() for i in sm.selectedIndexes()}):
            if table.isRowHidden(r):
                continue
            k = self._row_key(table, r)
            if k is not None and k not in keys:
                keys.append(k)
        return keys

    def _targets(self, table: QTableWidget, oid: int) -> list[int]:
        """The rows an edit of row `oid` applies to: only itself, unless it is
        one of several selected rows."""
        keys = self._selected_row_keys(table)
        if len(keys) < 2 or oid not in keys:
            return [oid]
        return keys

    def _update_banner(self, *_a: object) -> None:
        if getattr(self, "_banner", None) is None:
            return          # the bar is built after the grids
        table = self._tabs.currentWidget()
        n = (len(self._selected_row_keys(table))
             if isinstance(table, QTableWidget)
             and table in dict(self._level_tables()) else 0)
        self._banner.setVisible(n > 1)
        if n > 1:
            self._banner.setText(
                f"{n} rows selected. Editing a cell will change all {n}.")

    def _confirm_multi(self, n: int) -> bool:
        if self._multi_confirmed:
            return True
        from raptor.gui.ui import dialogs
        ok, again = dialogs.ask_apply_to_rows(self, n)
        if ok and again:
            self._multi_confirmed = True      # this session only
        return ok

    def _apply_rows(self, oid: int, what: str, value: str, fn,
                    applicable=None) -> bool:
        """Run the single-row commit `fn(id)` for row `oid`, or for every
        selected row when `oid` is one of several: one undo step and one
        rule-gate pass for the lot (refused as a whole, every row reverts).
        `applicable(id)` says whether a row has the column at all."""
        table = self._edit_table()
        kind = self._kind_of(table)
        keys = self._targets(table, oid)
        if len(keys) == 1:
            return bool(fn(oid))
        todo, read_only, na = [], 0, 0
        for k in keys:
            if self._is_foreign(kind, k):
                read_only += 1
            elif applicable is not None and not applicable(k):
                na += 1
            else:
                todo.append(k)
        if not todo:
            self.status_message.emit(
                f"Nothing to set: {len(keys)} rows selected, none can take "
                f"a {what}.")
            return False
        if not self._confirm_multi(len(todo)):
            self._refresh_soon()                   # put the typed value back
            return False
        stack = req(self._view)._stack
        before = stack.count()
        stack.beginMacro(f"Set {what} on {len(todo)} rows")
        results: list[bool] = []
        try:
            for k in todo:
                results.append(bool(fn(k)))
        finally:
            stack.endMacro()
        changed = any(results)
        if changed and stack.count() == before:
            self._refresh_soon()                   # refused: rows revert
            return False
        if changed:
            note = f"Set {what} to {value} on {sum(results)} rows"
            if read_only:
                note += f"; {read_only} read-only row(s) on other levels skipped"
            if na:
                note += f"; {na} without that parameter skipped"
            self.status_message.emit(note)
        return results[todo.index(oid)] if oid in todo else changed

    def _refresh_soon(self) -> None:
        from shiboken6 import isValid

        def go() -> None:
            if isValid(self):
                self.refresh()
        QTimer.singleShot(0, go)

    def fill_down(self) -> bool:
        """Ctrl+D: the top selected row's value in the current column goes to
        the other selected rows."""
        table = self._edit_table()
        col = table.currentColumn()
        rows = sorted({i.row() for i in table.selectionModel().selectedIndexes()
                       if not table.isRowHidden(i.row())})
        if col < 0 or len(rows) < 2:
            return False
        w = table.cellWidget(rows[0], col)
        from PySide6.QtWidgets import QLineEdit
        edit = w.findChild(QLineEdit) if w is not None else None
        if edit is not None:
            text = edit.text()
        elif isinstance(w, QComboBox):
            return self._fill_combo(table, rows, col, w)
        else:
            item = table.item(rows[0], col)
            text = item.text() if item is not None else ""
        return self._fill_text(table, rows, col, text, start=1)

    def _fill_text(self, table: QTableWidget, rows: list[int], col: int,
                   text: str, start: int = 0) -> bool:
        """Type `text` into column `col` of one of `rows` (the first from
        `start`): its commit fans out to every selected row."""
        from PySide6.QtWidgets import QLineEdit
        done = False
        for r in rows[start:]:
            w = table.cellWidget(r, col)
            if w is None:
                continue
            e = w.findChild(QLineEdit)
            if e is None or not e.isEnabled():
                continue
            e.setText(text)
            e.editingFinished.emit()           # the row's own commit path
            done = True
            break      # the first commit fans out to every selected row
        return done

    def _fill_combo(self, table: QTableWidget, rows: list[int], col: int,
                    combo: QComboBox) -> bool:
        target = table.cellWidget(rows[1], col)
        if not isinstance(target, QComboBox) or not target.isEnabled():
            return False
        target.setCurrentIndex(combo.currentIndex())
        return True

    def paste_into_selection(self, text: str) -> bool:
        """Paste of one plain value with several rows selected: it goes to
        the current column of every selected row."""
        table = self._edit_table()
        sm = table.selectionModel()
        rows = sorted({i.row() for i in sm.selectedIndexes()
                       if not table.isRowHidden(i.row())}) if sm else []
        text = text.strip()
        if len(rows) < 2 or "\n" in text or "\t" in text or not text:
            return False
        return self._fill_text(table, rows, table.currentColumn(), text)

    def _on_whole_toggled(self, on: bool) -> None:
        self._whole = bool(on)
        self._whole_src = None
        self._apply_level_column()
        self.refresh(force=True)

    def _update_scope_note(self) -> None:
        """Say when the Blocks/Nodes/Events grids show one level only."""
        view = self._view
        rbd = getattr(view, "_rbd_ref", None) if view is not None else None
        if rbd is None:
            self._scope_label.setText("")
            return

        def total(section: str, skip_default: bool = False) -> int:
            sec = rbd.sections.get(section)
            if sec is None:
                return 0
            if not skip_default:
                return len(sec.rows)
            return sum(1 for r in sec.rows
                       if str(r.get("ID", "")).strip() not in
                       ("0", "0.0", "0.000000"))

        if self._whole:
            src = self._src()
            self._scope_label.setText(
                f"· Whole model ({len(src.blocks)} blocks across "
                f"{src.level_count} levels)")
            self._scope_label.show()
            return
        cur_b = len(getattr(view, "blocks", {}) or {})
        cur_n = len(getattr(view, "nodes", {}) or {})
        cur_e = len(getattr(view, "events", {}) or {})
        tot_b = total("BLOCK_DATA", True)
        tot_n = total("NODE_DATA")
        tot_e = total("EVENT_DATA")
        if (cur_b, cur_n, cur_e) == (tot_b, tot_n, tot_e):
            self._scope_label.setText("")
            self._scope_label.hide()
            return
        self._scope_label.setText(
            f"· Current level: {cur_b} of {tot_b} blocks, "
            f"{cur_n} of {tot_n} nodes, {cur_e} of {tot_e} events")
        self._scope_label.show()

    def _label_cell_widgets(self, table: QTableWidget) -> None:
        """Give every editor embedded in a cell an accessible name,
        "<column>, <row's name>".

        Cell editors are built as a container holding a bare QLineEdit /
        combo / checkbox, none of which knows which cell it is in, so a
        screen reader announced 91 unlabelled edit fields on a 14-block
        model. Named from CONTENT (column header + the row's first-column
        text), not position, so the name stays right when the user sorts
        and the widgets end up on different rows."""
        tab = self._tabs.tabText(self._tabs.indexOf(table))
        if tab and not table.accessibleName():
            table.setAccessibleName(f"{tab} table")
        for r in range(table.rowCount()):
            self._label_row_widgets(table, r)

    def _label_row_widgets(self, table: QTableWidget, r: int) -> None:
        """`_label_cell_widgets` for one row: its widgets are named from
        the column header and the row's first-column text."""
        from PySide6.QtWidgets import (QCheckBox, QComboBox, QDoubleSpinBox,
                                       QLineEdit, QSpinBox)
        editors = (QLineEdit, QComboBox, QCheckBox, QSpinBox, QDoubleSpinBox)
        inner_of = (QComboBox, QSpinBox, QDoubleSpinBox)
        first = table.item(r, 0)
        who = first.text() if first is not None and first.text() \
            else f"row {r + 1}"
        for c in range(table.columnCount()):
            w = table.cellWidget(r, c)
            if w is None:
                continue
            head = table.horizontalHeaderItem(c)
            col = head.text() if head is not None else f"column {c + 1}"
            found = [w] if isinstance(w, editors) else []
            for kind in editors:
                found += w.findChildren(kind)
            for e in found:
                if isinstance(e.parent(), inner_of):
                    continue          # a combo's / spin box's own edit
                if not e.accessibleName():
                    e.setAccessibleName(f"{col}, {who}")

    def _export_current_table_csv(self) -> None:
        """The currently visible grid tab (Blocks/Nodes/Events/Spare
        Pools/Resources/Triggers/Phases) as CSV, writing the app's own
        input data."""
        import csv
        table = self._tabs.currentWidget()
        if not isinstance(table, QTableWidget):
            return
        tab_name = self._tabs.tabText(self._tabs.currentIndex())
        default = tab_name.replace(" ", "_").lower() + ".csv"
        title = "Export CSV"
        if self._filter_active(table):
            shown, total = self._filter_bars[table].rows_shown_of_total()
            title = (f"Export CSV: only the {shown:,} of {total:,} rows "
                     "the filter shows")
        path, _f = fdg.getSaveFileName(self, title, default, "CSV (*.csv)")
        if not path:
            return

        # DELEGATED (review E-11). This was the ONLY one of six extraction
        # sites that knew distribution combos and parameter fields are CELL
        # WIDGETS rather than items -- so the other five exported those
        # columns blank. `table_to_grid(cell_widgets=True)` is now the
        # default everywhere, making this behaviour the standard rather than
        # the exception.
        from raptor.gui.ui.table_export import table_to_grid
        headers, rows = table_to_grid(table, raw=True, cell_widgets=True)

        try:
            with open(path, "w", newline="", encoding="utf-8") as f:
                writer = csv.writer(f)
                writer.writerow(headers)
                writer.writerows(rows)
        except OSError as e:
            msgbox.critical(self, "Export Failed",
                                 f"Could not write {path}:\n{e}")

    def _apply_param_tips(self, t, row: int, distro: int, which: str):
        """Header-meaning tooltips on the three param cells for this
        block's current distribution ('Mean (hours)' beats 'F Param 1').
        Even when the distribution has no specific label map, the cells
        explain which slot they fill and that the unit follows the
        model's time-units setting."""
        from raptor.core.distro_rules import DISTRO_NAMES as _DN
        base = 3 if which == "F" else 8
        labels = self._PARAM_TIPS.get(distro)
        units = getattr(self, "_display_unit", None) or \
            getattr(self, "_native_units", "") or ""
        base_labels = DISTRO_PARAM_LABELS.get(distro, ("", "", ""))
        entry_labels = tuple(
            f"{lbl} ({param_entry.unit_for(distro, i, units)})"
            if lbl and _is_time_param(base_labels[i])
            and param_entry.unit_for(distro, i, units) else lbl
            for i, lbl in enumerate(param_entry.labels(distro)))
        # In lambda/mu-sigma mode the slots mean something else, so the
        # generic per-distribution tips would name the wrong number.
        transformed = bool(param_entry.is_rate_field(distro, 0)
                           or param_entry.is_mu_sigma(distro))
        labels = entry_labels if transformed else labels
        side = "failure" if which == "F" else "repair"
        tips = ([f"{lbl} ({side} parameter {i + 1})"
                 if lbl else "" for i, lbl in enumerate(labels)]
                if labels else ["", "", ""])
        for i in range(3):
            generic = (f"{which} parameter {i+1} of this block's "
                       f"{_DN.get(distro, '?')} distribution "
                       "(units follow the model's time setting)")
            self._set_cell_tip(t, row, base + i, tips[i] or generic)

    # ------------------------------------------------------------------
    # Cell widgets on demand (#146)
    # ------------------------------------------------------------------

    # ------------------------------------------------------------------
    # Filter row (the grids with many rows)
    # ------------------------------------------------------------------

    #: Sparing-table columns a filter can read without the row's cell
    #: widgets: Name, ID and Sparing Type (from the model).
    _SPARING_FILTER_COLS = {0, 1, 2}

    def _attach_filters(self) -> None:
        """A per-column filter row on the grids that can grow long. It reads
        the model objects the rows were filled from, not the cell widgets, so
        a row the filter hides never gets its widgets built."""
        self._filter_bars: dict[QTableWidget, FilterBar] = {}
        for table, matcher, cols in (
                (self._blocks_table, self._block_matcher, None),
                (self._nodes_table, None, None),
                (self._events_table, None, None),
                (self._sparing_table, self._sparing_matcher,
                 self._SPARING_FILTER_COLS),
                (self._pm_table, None, None)):
            bar = FilterBar.attach(
                table, matcher=matcher, filter_columns=cols,
                row_key=lambda r, t=table: self._row_key(t, r))
            bar.changed.connect(
                lambda *_a, t=table: self._after_filter(t))
            self._filter_bars[table] = bar

    def _after_filter(self, t: QTableWidget) -> None:
        """Rows came and went: build the widgets of the ones now in view."""
        if t in self._lazy_pending:
            self._realize_visible(t)

    def _filter_active(self, t: QTableWidget) -> bool:
        bar = getattr(self, "_filter_bars", {}).get(t)
        return bar is not None and bar.is_active()

    def _block_cell(self, t: QTableWidget, row: int, col: int,
                    bid: int | None) -> tuple[str, float | None]:
        """What the Blocks grid shows in a cell, from the model (the widget
        columns) or the cell's item."""
        b = self._src().blocks.get(bid) if self._view is not None \
            and bid is not None else None
        if b is not None and col in (2, 7):
            code = b.fail_distro if col == 2 else b.repair_distro
            return distro_display_name(code, is_failure=col == 2), None
        if b is not None and col in (3, 4, 5, 8, 9, 10):
            which = "F" if col < 7 else "R"
            code = b.fail_distro if which == "F" else b.repair_distro
            vals = b.fail_values if which == "F" else b.repair_values
            i = col - 3 if which == "F" else col - 8
            shown = self._entry_values(code, list(vals))
            if i < len(shown):
                return f"{shown[i]:.6g}", float(shown[i])
            return "", None
        if b is not None and col == 12:
            from raptor.gui.ui.units_conv import native_to_display
            v = native_to_display(
                b.pm_freq, getattr(self, "_native_units", "") or "",
                getattr(self, "_display_unit", None))
            return f"{v:.6g}", float(v)
        item = t.item(row, col)
        return (item.text() if item is not None else ""), None

    def _block_matcher(self, row: int, filters) -> bool:
        t = self._blocks_table
        bid = self._row_key(t, row)
        for col, f in filters:
            text, raw = self._block_cell(t, row, col, bid)
            if not f(text, raw):
                return False
        return True

    def _sparing_matcher(self, row: int, filters) -> bool:
        from raptor.core.fileio.typed import SPARING_TYPE_TO_FILE_STRING
        t = self._sparing_table
        bid = self._row_key(t, row)
        b = self._src().blocks.get(bid) if self._view is not None \
            and bid is not None else None
        for col, f in filters:
            if col == 2 and b is not None:
                text = SPARING_TYPE_TO_FILE_STRING.get(b.sparing_type, "None")
            else:
                item = t.item(row, col)
                text = item.text() if item is not None else ""
            if not f(text, None):
                return False
        return True

    @staticmethod
    def _row_key(t: QTableWidget, r: int) -> int | None:
        """The block id of a row, read from its ID cell (rows move when the
        grid is sorted; the ID cell moves with them)."""
        item = t.item(r, 1)
        try:
            return int(item.text()) if item is not None else None
        except ValueError:
            return None

    def _realize_initial(self, t: QTableWidget) -> None:
        """After filling a grid: a small one builds everything, as it
        always did; a large one only what is on screen."""
        if t.rowCount() <= _LAZY_ROWS:
            self._realize_rows(t, range(t.rowCount()))
        else:
            self._realize_visible(t)

    def _realize_visible(self, t: QTableWidget) -> None:
        """Build the widgets of the rows in view, plus a margin."""
        from shiboken6 import isValid
        # Deferred calls (sort, resize) can land after the grid is gone.
        if not isValid(t) or not self._lazy_pending.get(t):
            return
        # Only rows a filter leaves showing count. `rowAt` answers -1 when
        # the shown rows do not fill the window (or none match), which used
        # to be read as "the table runs on", so a selective filter built the
        # widgets of every hidden row: ~55 s on a 3,800-block model.
        shown = [r for r in range(t.rowCount()) if not t.isRowHidden(r)]
        if not shown:
            return
        top = t.rowAt(0)
        bottom = t.rowAt(max(t.viewport().height() - 1, 0))
        first = bisect_left(shown, top) if top >= 0 else 0
        # A screenful, at least 30 (not yet laid out: hidden tab, first show).
        fits = max(t.viewport().height() // max(
            t.verticalHeader().defaultSectionSize(), 1) + 1, 30)
        last = (bisect_left(shown, bottom) if bottom >= 0
                else first + fits)
        last = min(last, first + fits, len(shown) - 1)
        self._realize_rows(t, shown[max(first - _LAZY_MARGIN, 0):
                                    last + _LAZY_MARGIN + 1])

    def _realize_all(self, t: QTableWidget) -> None:
        """Every row: before anything that reads all the widgets (export)."""
        if not self._lazy_pending.get(t):
            return
        QApplication.setOverrideCursor(Qt.CursorShape.WaitCursor)
        try:
            # A filtered grid exports (and copies) the rows it shows, so the
            # hidden ones need no widgets.
            rows = [r for r in range(t.rowCount()) if not t.isRowHidden(r)] \
                if self._filter_active(t) else range(t.rowCount())
            self._realize_rows(t, rows)
        finally:
            QApplication.restoreOverrideCursor()

    def _realize_rows(self, t: QTableWidget, rows) -> None:
        from raptor.gui.ui import param_entry
        pending = self._lazy_pending[t]
        build = self._lazy_builders[t]
        todo = []
        for r in rows:
            key = self._row_key(t, r)
            if key in pending:
                todo.append((r, key))
        if not todo:
            return
        t.setUpdatesEnabled(False)
        try:
            with param_entry.cached_modes():
                for r, key in todo:
                    pending.discard(key)
                    build(t, r, key)
                    if self._whole and self._is_foreign("block", key):
                        self._lock_row(t, r)
        finally:
            t.setUpdatesEnabled(True)

    def _populate_blocks(self):
        t = self._blocks_table
        t.setUpdatesEnabled(False)
        t.setRowCount(0)
        self._lazy_pending[t] = set()
        if self._view is None:
            t.setUpdatesEnabled(True)
            return
        from raptor.gui.ui.units_conv import native_to_display
        native_u = getattr(self, "_native_units", "") or ""
        display_u = getattr(self, "_display_unit", None)

        def stat_text(val):
            if val is None or isinstance(val, str):
                return _fmt_stat(val)
            shown = native_to_display(val, native_u, display_u) \
                if display_u else val
            return _fmt_stat(shown)

        src = self._src()
        bids = sorted(src.blocks)
        t.setRowCount(len(bids))
        for r, bid in enumerate(bids):
            b = src.blocks[bid]
            if self._whole:
                t.setItem(r, t.columnCount() - 1,
                          self._level_item("block", bid))
            # ID first (read-only anchor used by commits).
            id_item = _SortItem(str(bid))
            id_item.setFlags(id_item.flags() & ~Qt.ItemFlag.ItemIsEditable)
            t.setItem(r, 1, id_item)
            name_item = _SortItem(b.name)
            t.setItem(r, 0, name_item)
            name_item.setBackground(name_item.background())  # keep default
            t.setItem(r, 6, self._ro(str(_NUM_PARAMS.get(b.fail_distro, "?"))))
            t.setItem(r, 11, self._ro(str(_NUM_PARAMS.get(b.repair_distro, "?"))))
            # Computed Mean/Std Dev, the same maths the Inspector shows
            # under each distribution's param row. `compute_stats`
            # returns a bare None (not a 2-tuple) for an invalid or
            # insufficient parameterization -- e.g. Mass Edit leaves some
            # blocks on default params that a distribution rejects -- so
            # treat None as "no value".
            fail_stats = compute_stats(b.fail_distro, list(b.fail_values))
            repair_stats = compute_stats(b.repair_distro,
                                         list(b.repair_values))
            fmean, fsd = fail_stats if fail_stats is not None else (None, None)
            rmean, rsd = repair_stats if repair_stats is not None \
                else (None, None)
            for col, val in ((13, fmean), (14, fsd), (15, rmean), (16, rsd)):
                t.setItem(r, col, self._ro(stat_text(val)))
        self._lazy_pending[t] = set(bids)
        t.setUpdatesEnabled(True)
        self._realize_initial(t)

    def _build_block_widgets(self, t, r: int, bid: int) -> None:
        """The cell widgets of one Blocks row (distribution combos and
        parameter fields), their hover text and their accessible names."""
        b = req(self._src()).blocks.get(bid)
        if b is None:
            return
        self._distro_cell(t, r, 2, b.fail_distro,
                          lambda code, bid=bid: self._set_dist(bid, "F", code))
        f_labels = DISTRO_PARAM_LABELS.get(b.fail_distro, ("", "", ""))
        f_entry = self._entry_values(b.fail_distro, b.fail_values)
        for i in range(3):
            self._param_cell(
                t, r, 3 + i, b.fail_values[i],
                lambda v, bid=bid, i=i: self._set_param(bid, "F", i, v),
                is_time=_is_time_param(f_labels[i]),
                shown_value=f_entry[i],
                on_commit_entry=lambda v, bid=bid, i=i:
                self._set_param_entry(bid, "F", i, v))
        self._distro_cell(t, r, 7, b.repair_distro,
                          lambda code, bid=bid: self._set_dist(bid, "R", code),
                          is_failure=False)
        r_labels = DISTRO_PARAM_LABELS.get(b.repair_distro, ("", "", ""))
        r_entry = self._entry_values(b.repair_distro, b.repair_values)
        for i in range(3):
            self._param_cell(
                t, r, 8 + i, b.repair_values[i],
                lambda v, bid=bid, i=i: self._set_param(bid, "R", i, v),
                is_time=_is_time_param(r_labels[i]),
                shown_value=r_entry[i],
                on_commit_entry=lambda v, bid=bid, i=i:
                self._set_param_entry(bid, "R", i, v))
        self._param_cell(t, r, 12, b.pm_freq,
                         lambda v, bid=bid: self._set_pm(bid, v))
        # After the cells exist: the parameter cells are widgets, so the
        # per-distribution meaning has to be set on them, not on items.
        self._apply_param_tips(t, r, b.fail_distro, "F")
        self._apply_param_tips(t, r, b.repair_distro, "R")
        self._tag_row_with_name(t, r, b.name)
        self._label_row_widgets(t, r)

    def _populate_nodes(self):
        t = self._nodes_table
        t.setUpdatesEnabled(False)
        t.setRowCount(0)
        if self._view is None:
            t.setUpdatesEnabled(True)
            return
        type_names = {1: "Start", 2: "Regular", 3: "End",
                      4: "Hierarchy In", 5: "Hierarchy Out"}
        src = self._src()
        node_ids = sorted(src.nodes)
        t.setRowCount(len(node_ids))
        for r, nid in enumerate(node_ids):
            n = src.nodes[nid]
            if self._whole:
                t.setItem(r, t.columnCount() - 1,
                          self._level_item("node", nid))
            id_item = _SortItem(str(nid))
            id_item.setFlags(id_item.flags() & ~Qt.ItemFlag.ItemIsEditable)
            t.setItem(r, 1, id_item)
            t.setItem(r, 0, _SortItem(n.node_name))
            t.setItem(r, 2, self._ro(type_names.get(n.type_node, "?")))
            if n.type_node == 2:
                self._k_cell(t, r, n)
                t.setItem(r, 4, self._ro(str(n.connect_out_of_num)))
            else:
                t.setItem(r, 3, self._ro(str(n.good_paths)))
                t.setItem(r, 4, self._ro(str(n.connect_out_of_num)))
            self._tag_row_with_name(t, r, n.node_name)
        t.setUpdatesEnabled(True)

    def _goto_clicked_row(self, item: QTableWidgetItem, kind: str) -> None:
        table = item.tableWidget()
        name_item = table.item(item.row(), 0)
        if self._whole:
            return          # a click selects a cell; Go to level is explicit
        if name_item is not None and name_item.text():
            self.element_locate_requested.emit(kind, name_item.text())

    def _show_row_context_menu(self, table: QTableWidget, kind: str, pos) -> None:
        menu = self._row_menu(table, kind, pos)
        if menu is not None:
            menu.exec(table.viewport().mapToGlobal(pos))

    def _row_menu(self, table: QTableWidget, kind: str, pos):
        """The right-click menu of the row under `pos` (None off the rows)."""
        from PySide6.QtWidgets import QMenu
        row = table.rowAt(pos.y())
        if row < 0:
            return None
        name_item = table.item(row, 0)
        if name_item is None:
            return None
        menu = QMenu(table)
        oid = self._row_key(table, row)
        if self._whole and oid is not None:
            level = table.item(row, table.columnCount() - 1)
            where = level.text() if level is not None else "its level"
            act = menu.addAction(
                f"Go to level: {where} \u25b8 {name_item.text()}")
            act.triggered.connect(
                lambda: self.element_locate_id_requested.emit(kind, oid))
        else:
            act = menu.addAction(f"Go to {name_item.text()} on canvas")
            act.triggered.connect(
                lambda: self.element_locate_requested.emit(
                    kind, name_item.text()))
        return menu

    @staticmethod
    def _set_cell_tip(t: QTableWidget, row: int, col: int, text: str) -> None:
        """Hover text for one cell, whether it is an item or a cell widget.

        A cell widget (distribution combo, parameter and PM fields) sits on
        top of its cell, so the hover reaches the widget and never the item
        beneath: the text has to be on the widget and the field inside it."""
        item = t.item(row, col)
        if item is not None:
            item.setToolTip(text)
        widget = t.cellWidget(row, col)
        if widget is not None:
            for target in (widget, *widget.findChildren(QWidget)):
                target.setToolTip(text)

    @staticmethod
    def _cell_tip(t: QTableWidget, row: int, col: int, *,
                  header: bool = True) -> str:
        """The tip a cell has now: its own, else (`header`) its column's."""
        item = t.item(row, col)
        widget = t.cellWidget(row, col)
        own = item.toolTip() if item is not None else (
            widget.toolTip() if widget is not None else "")
        if own or not header:
            return own
        head = t.horizontalHeaderItem(col)
        return head.toolTip() if head is not None else ""

    @classmethod
    def _tag_row_with_name(cls, t: QTableWidget, row: int, name: str) -> None:
        """Prefix every cell's tooltip in this row with the element's
        name, so hovering any cell tells you which block/node it belongs
        to -- easy to lose track once you have scrolled past the Name
        column -- followed by what the cell means: its own tip (a
        parameter's meaning for this row's distribution) or else its
        column's. Widget cells included."""
        for col in range(t.columnCount()):
            meaning = cls._cell_tip(t, row, col)
            cls._set_cell_tip(t, row, col,
                              f"{name}\n{meaning}" if meaning else name)

    @staticmethod
    def _ro(text: str) -> QTableWidgetItem:
        item = _SortItem(text)
        item.setFlags(item.flags() & ~Qt.ItemFlag.ItemIsEditable)
        item.setForeground(Qt.GlobalColor.gray)
        return item

    def _distro_cell(self, table, row, col, current: int, on_change,
                     *, is_failure: bool = True, kind: str | None = None):
        """One combo cell. `is_failure` picks the display-name rule.

        DELEGATED to `distro_combo.fill_distro_combo` (review E-14). This
        loop applied NO relabel, while the Inspector and two dialogs
        relabelled code 18. So the Repair distribution read "None
        (unrepairable)" in the Inspector and the raw "None" in this grid --
        the same block, on the same screen, under two names.
        """
        combo = fill_distro_combo(QComboBox(), current,
                                  is_failure=is_failure, kind=kind)
        combo.currentIndexChanged.connect(
            lambda _i, cmb=combo, cb=on_change: cb(cmb.currentData()))
        table.setCellWidget(row, col, combo)

    def _param_cell(self, table, row, col, value: float, on_commit,
                    is_time: bool = True, *,
                    shown_value: float | None = None,
                    on_commit_entry=None):
        """`is_time` is False for a Shape/N-Trials/Probability param
        slot, which is never time-dimensioned regardless of the model's
        own units (matching the Inspector's Failure/Repair Param fields).
        Otherwise the cell value is shown in the active display unit.

        `on_commit_entry` is for a slot in Raptor's lambda/mu-sigma entry
        mode: `shown_value` is already in entry space, and the callback
        converts the typed number before validating/storing it."""
        from raptor.gui.ui.units_conv import native_to_display, display_to_native
        native_units = getattr(self, "_native_units", "") or ""
        display_unit = getattr(self, "_display_unit", None) if is_time \
            else None
        w = QWidget()
        from PySide6.QtWidgets import QHBoxLayout, QLineEdit
        lay = QHBoxLayout(w)
        lay.setContentsMargins(2, 0, 2, 0)
        shown0 = shown_value if on_commit_entry is not None else (
            native_to_display(value, native_units, display_unit)
            if is_time else value)
        edit = QLineEdit(f"{shown0:.6g}")
        edit.setValidator(QDoubleValidator())
        edit.setFrame(False)
        committed = {"v": shown0 if on_commit_entry is not None else value}

        def commit():
            try:
                raw = float(edit.text().strip())
            except ValueError:
                edit.setText(f"{committed['v']:.6g}")
                return
            if on_commit_entry is not None:
                if raw == committed["v"]:
                    return
                if on_commit_entry(raw):
                    committed["v"] = raw
                    self.model_edited.emit()
                else:
                    edit.setText(f"{committed['v']:.6g}")
                return
            new_v = display_to_native(raw, native_units, display_unit) \
                if is_time else raw
            if new_v == committed["v"]:
                return
            if on_commit(new_v):
                committed["v"] = new_v
                self.model_edited.emit()
            else:
                shown = native_to_display(committed["v"], native_units,
                                          display_unit) if is_time \
                    else committed["v"]
                edit.setText(f"{shown:.6g}")

        edit.editingFinished.connect(commit)
        lay.addWidget(edit)
        table.setCellWidget(row, col, w)

    def _populate_events(self):
        """Events grid: Name / Distro / failVal / failStream — the same
        fields the Inspector exposes for blocks, routed through the
        view's undoable mutators."""
        t = self._events_table
        t.setUpdatesEnabled(False)
        t.setRowCount(0)
        if self._view is None:
            t.setUpdatesEnabled(True)
            return
        from PySide6.QtWidgets import QHBoxLayout, QLineEdit
        from PySide6.QtGui import QIntValidator
        src = self._src()
        event_ids = sorted(src.events)
        t.setRowCount(len(event_ids))
        for r, eid in enumerate(event_ids):
            event = src.events[eid]
            if self._whole:
                t.setItem(r, t.columnCount() - 1,
                          self._level_item("event", eid))
            id_item = _SortItem(str(eid))
            id_item.setFlags(id_item.flags() & ~Qt.ItemFlag.ItemIsEditable)
            t.setItem(r, 1, id_item)
            t.setItem(r, 0, _SortItem(event.name))
            self._distro_cell(t, r, 2, event.fail_distro,
                              lambda code, eid=eid: self._apply_rows(
                                  eid, "distribution", str(code),
                                  lambda k: self._view.set_event_field(
                                      k, "dist", code)),
                              kind="event")
            # Single param cell (EVENT_DATA.failVal).
            w = QWidget()
            lay = QHBoxLayout(w)
            lay.setContentsMargins(2, 0, 2, 0)
            edit = QLineEdit(f"{event.fail_mean:.6g}")
            edit.setValidator(QDoubleValidator())
            edit.setFrame(False)
            committed = {"v": event.fail_mean}

            # Bind this row's editor, state and event id: the loop variables
            # move on before the user edits (B023).
            def commit_param(edit=edit, committed=committed, eid=eid):
                try:
                    new_v = float(edit.text().strip())
                except ValueError:
                    edit.setText(f"{committed['v']:.6g}")
                    return
                if new_v != committed["v"]:
                    self._apply_rows(
                        eid, "Param", f"{new_v:g}",
                        lambda k: req(self._view).set_event_field(
                            k, "param", new_v) or True)
                    committed["v"] = new_v
                    self.model_edited.emit()

            edit.editingFinished.connect(commit_param)
            lay.addWidget(edit)
            t.setCellWidget(r, 3, w)
            ws = QWidget()
            lays = QHBoxLayout(ws)
            lays.setContentsMargins(2, 0, 2, 0)
            edits = QLineEdit(str(event.fail_stream))
            validator = QIntValidator()
            validator.setBottom(0)
            edits.setValidator(validator)
            edits.setFrame(False)
            committed_s = {"v": event.fail_stream}

            def commit_stream(edits=edits, committed_s=committed_s, eid=eid):
                try:
                    new_v = int(edits.text().strip())
                except ValueError:
                    edits.setText(str(committed_s["v"]))
                    return
                if new_v != committed_s["v"]:
                    self._apply_rows(
                        eid, "Stream", str(new_v),
                        lambda k: req(self._view).set_event_field(
                            k, "stream", new_v) or True)
                    committed_s["v"] = new_v
                    self.model_edited.emit()

            edits.editingFinished.connect(commit_stream)
            lays.addWidget(edits)
            t.setCellWidget(r, 4, ws)
            self._tag_row_with_name(t, r, event.name)
        t.setUpdatesEnabled(True)

    def _text_cell(self, table, row: int, col: int, value: str, on_commit):
        """A plain text cell as a cell-widget (matches `_populate_events`'
        own QLineEdit-in-QWidget pattern) -- used where the value doesn't
        fit a combo/checkbox and a whole-row itemChanged dispatcher would
        be more bookkeeping than it's worth for just two-three columns."""
        from PySide6.QtWidgets import QHBoxLayout, QLineEdit
        w = QWidget()
        lay = QHBoxLayout(w)
        lay.setContentsMargins(2, 0, 2, 0)
        edit = QLineEdit(value)
        edit.setFrame(False)
        committed = {"v": value}

        def commit():
            new_v = edit.text().strip()
            if new_v != committed["v"]:
                if on_commit(new_v):
                    committed["v"] = new_v
                    self.model_edited.emit()
                else:
                    edit.setText(committed["v"])

        edit.editingFinished.connect(commit)
        lay.addWidget(edit)
        table.setCellWidget(row, col, w)

    def _populate_sparing(self):
        """Sparing tab (Raptor 7 "Block Input Tables" -- Sparing
        tab): one row per block, columns for every sparing-related
        field regardless of that block's current type (a block that
        isn't Custom/Pooled/Resource just has blank/inert cells for
        the fields that don't apply to it, matching a plain grid's
        natural shape rather than hiding columns per row)."""
        t = self._sparing_table
        t.setUpdatesEnabled(False)
        t.setRowCount(0)
        self._lazy_pending[t] = set()
        if self._view is None:
            t.setUpdatesEnabled(True)
            return
        src = self._src()
        bids = sorted(src.blocks)
        t.setRowCount(len(bids))
        for r, bid in enumerate(bids):
            b = src.blocks[bid]
            if self._whole:
                t.setItem(r, t.columnCount() - 1,
                          self._level_item("block", bid))
            name_item = _SortItem(b.name)
            name_item.setFlags(name_item.flags() & ~Qt.ItemFlag.ItemIsEditable)
            t.setItem(r, 0, name_item)
            id_item = _SortItem(str(bid))
            id_item.setFlags(id_item.flags() & ~Qt.ItemFlag.ItemIsEditable)
            t.setItem(r, 1, id_item)
        self._lazy_pending[t] = set(bids)
        t.setUpdatesEnabled(True)
        self._realize_initial(t)

    def _build_sparing_widgets(self, t, r: int, bid: int) -> None:
        """The cell widgets of one Sparing row, their hover text and names."""
        b = req(self._src()).blocks.get(bid)
        if b is None:
            return
        from PySide6.QtWidgets import QLineEdit
        from raptor.core.fileio.typed import (
            _SPARING_TYPE_BY_NAME, SPARING_TYPE_TO_FILE_STRING)
        from raptor.gui.ui.units_conv import native_to_display, display_to_native
        native_u = getattr(self, "_native_units", "") or ""
        display_u = getattr(self, "_display_unit", None)
        combo = QComboBox()
        # Only the 4 sparing types a file can carry; ColdPool/Resource
        # strings load as None, so they are not offered.
        current_file_string = SPARING_TYPE_TO_FILE_STRING.get(
            b.sparing_type, "None")
        offered = ["None", "Infinite", "Custom", "Pooled"]
        if current_file_string not in offered:
            # Show an unexpected stored value accurately rather
            # than silently mislabeling the cell as "None".
            offered.append(current_file_string)
        for file_string in offered:
            combo.addItem(file_string, file_string)
        idx = combo.findData(current_file_string)
        combo.setCurrentIndex(max(idx, 0))

        def commit_sparing(file_string, bid=bid, cmb=combo):
            self._apply_rows(
                bid, "Sparing Type", file_string,
                lambda k: req(self._view).set_block_sparing_type(
                    k, _SPARING_TYPE_BY_NAME[file_string].name) or True)
            self.model_edited.emit()
        combo.currentIndexChanged.connect(
            lambda _i, cmb=combo, cb=commit_sparing:
            cb(cmb.currentData()))
        t.setCellWidget(r, 2, combo)

        self._text_cell(
            t, r, 3, b.pool_name,
            lambda text, bid=bid: self._apply_rows(
                bid, "Pool Name", text,
                lambda k: req(self._view).set_block_pool_name(k, text)
                or True))
        self._text_cell(
            t, r, 4, b.resource_pool_name,
            lambda text, bid=bid: self._apply_rows(
                bid, "Resource", text,
                lambda k: req(self._view).set_block_resource_pool(k, text)
                or True))

        def commit_new_spares(bid=bid, row=r):
            cw_c = t.cellWidget(row, 5)
            cw_i = t.cellWidget(row, 6)
            if cw_c is None or cw_i is None:
                return
            c_edit = cw_c.findChild(QLineEdit)
            i_edit = cw_i.findChild(QLineEdit)
            try:
                count = int(req(c_edit).text().strip())
                interval = float(req(i_edit).text().strip())
            except ValueError:
                return
            if display_u:
                interval = display_to_native(
                    interval, native_u, display_u)
            req(self._view).set_block_new_spares(bid, count, interval)
            self.model_edited.emit()

        self._text_cell(t, r, 5, str(int(b.new_spares)),
                        lambda text, cb=commit_new_spares:
                        (cb() or True))
        shown_interval = (native_to_display(
            b.new_spares_arrival, native_u, display_u)
            if display_u else b.new_spares_arrival)
        self._text_cell(t, r, 6, f"{shown_interval:.6g}",
                        lambda text, cb=commit_new_spares:
                        (cb() or True))
        self._tag_row_with_name(t, r, b.name)
        self._label_row_widgets(t, r)

    def _populate_pm(self):
        """Preventive Maintenance tab: "only the
        seven components for which we checked the preventive
        maintenance checkbox are listed on this tab" -- filtered to
        `block.uses_pm`, with the 5 PM option checkboxes as the last
        columns, matching the manual's own description verbatim."""
        t = self._pm_table
        t.setUpdatesEnabled(False)
        t.blockSignals(True)
        t.setRowCount(0)
        if self._view is None:
            t.setUpdatesEnabled(True)
            t.blockSignals(False)
            return
        src = self._src()
        bids = [bid for bid in sorted(src.blocks)
                if src.blocks[bid].uses_pm]
        t.setRowCount(len(bids))
        for r, bid in enumerate(bids):
            b = src.blocks[bid]
            if self._whole:
                t.setItem(r, t.columnCount() - 1,
                          self._level_item("block", bid))
            name_item = _SortItem(b.name)
            name_item.setFlags(name_item.flags() & ~Qt.ItemFlag.ItemIsEditable)
            t.setItem(r, 0, name_item)
            id_item = _SortItem(str(bid))
            id_item.setFlags(id_item.flags() & ~Qt.ItemFlag.ItemIsEditable)
            t.setItem(r, 1, id_item)
            self._param_cell(t, r, 2, b.pm_freq,
                             lambda v, bid=bid:
                             self._set_pm(bid, v), is_time=True)
            for col, (attr, key) in enumerate((
                    ("pm_spare_needed", "spare_needed"),
                    ("pm_refresh", "refresh"),
                    ("pm_mis_defer", "mis_defer"),
                    ("pm_req_defer", "req_defer"),
                    ("pm_fail_reset", "fail_reset")), start=3):
                item = _SortItem()
                item.setFlags(Qt.ItemFlag.ItemIsEnabled | Qt.ItemFlag.ItemIsUserCheckable
                             | Qt.ItemFlag.ItemIsSelectable)
                item.setCheckState(
                    Qt.CheckState.Checked if getattr(b, attr) else Qt.CheckState.Unchecked)
                item.setData(Qt.ItemDataRole.UserRole, (bid, key))
                t.setItem(r, col, item)
            self._tag_row_with_name(t, r, b.name)
        t.blockSignals(False)
        t.setUpdatesEnabled(True)

    def _on_pm_cell_changed(self, item: QTableWidgetItem):
        payload = item.data(Qt.ItemDataRole.UserRole)
        if payload is None or self._view is None:
            return
        bid, key = payload
        on = item.checkState() == Qt.CheckState.Checked
        self._apply_rows(
            bid, req(req(item.tableWidget()).horizontalHeaderItem(
                item.column())).text(),
            "on" if on else "off",
            lambda k: self._view.set_block_pm_option(k, key, on) or True)
        self.model_edited.emit()

    # ------------------------------------------------------------------
    # Keyed rows (pools / triggers)
    # ------------------------------------------------------------------

    def _populate_keyed(self, table):
        section_name, key_col, kinds = self._keyed[table]
        table.setUpdatesEnabled(False)
        table.blockSignals(True)
        table.setRowCount(0)
        time_cols = self._KEYED_TIME_FIELDS.get(section_name, set())
        if self._view is not None and self._view._rbd_ref is not None:
            sec = self._view._rbd_ref.sections.get(section_name)
            rows = sec.rows if sec else []
            table.setRowCount(len(rows))
            for row_idx, r in enumerate(rows):
                has_dist = "Dist" in sec.columns
                try:
                    dist_code = (int(float(r.get("Dist", "0") or 0))
                                if has_dist else None)
                except ValueError:
                    dist_code = 0
                plabels = (DISTRO_PARAM_LABELS.get(dist_code, ("", "", ""))
                          if dist_code is not None else ("", "", ""))

                for ci, col in enumerate(sec.columns):
                    is_dist_param = (
                        col in ("Param1", "Param2", "Param3")
                        and dist_code is not None)
                    if is_dist_param or col in time_cols:
                        # Display-unit override for these keyed tables:
                        # a Trigger/Phase's Param slots are only
                        # time-dimensioned depending on their own row's
                        # current Dist, mirroring the Blocks table's
                        # per-row `_is_time_param` use.
                        is_time = (_is_time_param(plabels[int(col[-1]) - 1])
                                  if is_dist_param else True)
                        try:
                            val = float(r.get(col, 0) or 0)
                        except ValueError:
                            val = 0.0
                        self._param_cell(
                            table, row_idx, ci, val,
                            lambda v, sec_n=section_name, kc=key_col,
                                  key=r.get(key_col, ""), c=col, t=table:  # noqa: B008 -- binds this row's key
                                self._commit_keyed_float(
                                    t, sec_n, kc, key, c, v),
                            is_time=is_time)
                        continue
                    item2: QTableWidgetItem
                    if kinds.get(col) is int and col == "Dist":
                        # Distribution columns show the NAME (manual
                        # screenshots) -- a bare code is unreadable.
                        item2 = _SortItem(
                            DISTRO_NAMES.get(dist_code or 0, f"#{dist_code}"))
                    else:
                        item2 = _SortItem(r.get(col, ""))
                    if col == key_col:
                        item2.setData(Qt.ItemDataRole.UserRole, r.get(key_col, ""))
                        # Spare/Resource pools: a one-sentence plain-
                        # English policy summary as a hover tooltip on
                        # the name cell. Imported locally because
                        # `pool_dialogs` imports this module.
                        if section_name in ("SPARE_POOL_DATA",
                                            "RESOURCE_DATA"):
                            from raptor.gui.ui.pool_dialogs import \
                                plain_english_summary
                            sentence = plain_english_summary(
                                section_name, r,
                                getattr(self, "_native_units", "") or "")
                            if sentence:
                                item2.setToolTip(sentence)
                    table.setItem(row_idx, ci, item2)
                # Hover meaning for the cells that have none yet: a
                # Param's meaning for THIS row's distribution, else the
                # column's own. (Widget cells included.)
                dname = DISTRO_NAMES.get(dist_code or 0, "?")
                for ci, col in enumerate(sec.columns):
                    if self._cell_tip(table, row_idx, ci, header=False):
                        continue            # the pool-name sentence, etc.
                    if col in ("Param1", "Param2", "Param3") \
                            and dist_code is not None:
                        label = plabels[int(col[-1]) - 1]
                        tip = (f"{label} of this row's {dname} distribution"
                               if label else
                               f"Not used by this row's {dname} distribution")
                    else:
                        tip = self._cell_tip(table, row_idx, ci)
                    if tip:
                        self._set_cell_tip(table, row_idx, ci, tip)
        # Readability: generous first column, content-sized rest.
        table.resizeColumnsToContents()
        if table.columnCount() > 0:
            table.setColumnWidth(0, max(140,
                                        table.columnWidth(0) + 24))
        table.blockSignals(False)
        table.setUpdatesEnabled(True)

    def _commit_keyed_float(self, table, section_name: str, key_col: str,
                            key, col: str, value: float) -> bool:
        """`_param_cell`'s `on_commit` for a PHASES/TRIGGERS/SPARE_POOL_
        DATA keyed row's time-dimensioned cell (ArriveEvery/EmerTime/
        SLOTime/InitUsed, or Param1-3) -- same
        validate-then-`set_keyed_cell` path `_on_keyed_cell_changed`
        uses for its own plain-item columns, just entered from a cell
        WIDGET's commit callback instead of `itemChanged` (`_param_cell`
        already emits `model_edited` itself on a truthy return, so this
        doesn't emit again)."""
        if self._view is None or key is None:
            return False
        sec = self._view._rbd_ref.sections.get(section_name)
        if sec is None:
            return False
        row = next((r for r in sec.rows if r.get(key_col) == key), None)
        if row is None:
            return False
        value_str = str(float(value))
        if col in ("Param1", "Param2", "Param3"):
            def cur(c, default="0"):
                return value_str if c == col else row.get(c, default)
            try:
                dist = int(float(cur("Dist")))
                params = [float(cur(f"Param{i}")) for i in (1, 2, 3)]
            except (ValueError, TypeError):
                dist, params = None, None
            if dist is not None and params is not None:
                label = "Phase" if section_name == "PHASES" else "Trigger"
                err = validate_distro_params(dist, params, label)
                if err:
                    self.validation_failed.emit(f"{key}: {err}")
                    return False
        self._view.set_keyed_cell(section_name, key_col, key, col, value_str)
        if section_name in ("SPARE_POOL_DATA", "RESOURCE_DATA"):
            # Keep the key-cell's plain-English tooltip fresh (it is
            # computed from the whole row at populate time). These two
            # sections have no Dist column, so the Dist-triggered
            # repopulate in `_on_keyed_cell_changed` never covers them.
            # Re-entrant but safe: QTableWidget defers cell-widget
            # deletion via `deleteLater`, so rebuilding the table from
            # inside this cell widget's own commit is safe.
            self._populate_keyed_guarded(table)
        return True

    def _populate_keyed_guarded(self, table):
        """Re-populate ONE keyed table mid-session (Dist change, keyed
        float commit): sorting must be off while rows are inserted, then
        restored to the user's choice."""
        header = table.horizontalHeader()
        order = (header.sortIndicatorSection(),
                 header.sortIndicatorOrder())
        table.setSortingEnabled(False)
        self._populate_keyed(table)
        if order[0] >= 0:
            table.setSortingEnabled(True)
            table.sortItems(order[0], order[1])

    def _on_keyed_cell_changed(self, item: QTableWidgetItem):
        table = item.tableWidget()
        section_name, key_col, kinds = self._keyed[table]
        old_key = item.data(Qt.ItemDataRole.UserRole) if item.column() == 0 else None
        row_idx = item.row()
        # The row's live key: from column-0's data role (stable), else
        # read the name cell.
        key = (req(table.item(row_idx, 0)).data(Qt.ItemDataRole.UserRole)
               if table.item(row_idx, 0) is not None else None)
        if self._view is None or key is None:
            return
        sec = self._view._rbd_ref.sections.get(section_name)
        if sec is None:
            return
        col = sec.columns[item.column()]
        kind = kinds.get(col, str)
        raw = item.text().strip()
        if kind is float:
            try:
                value = str(float(raw))
            except ValueError:
                self._reload_keyed_row(table, row_idx, sec, key_col, key)
                return
        elif kind is int:
            if col == "Dist":
                # Distribution cells DISPLAY the name -- accept a name
                # (or a raw code) and store the code.
                name = raw.lower()
                code = next((c for c, n in DISTRO_NAMES.items()
                             if n.lower() == name), None)
                if code is None:
                    try:
                        code = int(float(raw))
                    except ValueError:
                        code = None
                if code is None:
                    self._reload_keyed_row(table, row_idx, sec,
                                           key_col, key)
                    return
                from raptor.core.distro_rules import RAPTOR7_LISTS
                if code not in RAPTOR7_LISTS["delay"]:
                    # Raptor 7's phase and trigger distribution dialog
                    # (`DistBox`) does not list it, so there is no Raptor 7
                    # alert to quote: it cannot be chosen at all. Empirical
                    # in particular could not be kept, as a phase or trigger
                    # row has three parameter cells and no point list.
                    self._reload_keyed_row(table, row_idx, sec, key_col, key)
                    self.validation_failed.emit(
                        f"{key}: {DISTRO_NAMES.get(code, f'#{code}')} is not "
                        "available for a phase or trigger distribution.")
                    return
                value = str(code)
            else:
                try:
                    value = str(int(float(raw)))
                except ValueError:
                    self._reload_keyed_row(table, row_idx, sec,
                                           key_col, key)
                    return
        else:
            value = "_".join(raw.split()) or key
            if value != item.text():
                item.setText(value)
            if col == key_col and value != key and any(
                    r.get(key_col) == value for r in sec.rows if r.get(key_col) != key):
                # Pools, triggers and phases are referred to by name, so two with one name cannot both be used.
                self._reload_keyed_row(table, row_idx, sec, key_col, key)
                self.validation_failed.emit(
                    f"'{value}' is already used by another entry in this table; names must be unique.")
                return
        target_key = old_key if (item.column() == 0 and old_key) else key

        # Distribution-parameter validation: only the PHASES/TRIGGERS
        # keyed tables have a Dist/Param1-3 quadruple (Spare Pools and
        # Resources don't, so `col in (...)` scopes this to those two).
        # Validate the full row -- the candidate value for the committed
        # cell, the file's current values for the other three -- so
        # editing Param3 to 0.0 on a saved Pearson6 row is caught even
        # though Param3 alone looks like "just a float".
        seeded = None
        if col in ("Dist", "Param1", "Param2", "Param3"):
            row = next((r for r in sec.rows if r.get(key_col) == key), None)
            if row is not None:
                def cur(c, default="0"):
                    return value if c == col else row.get(c, default)
                try:
                    dist = int(float(cur("Dist")))
                    params = [float(cur(f"Param{i}")) for i in (1, 2, 3)]
                except (ValueError, TypeError):
                    dist, params = None, None
                if col == "Dist" and dist is not None and params is not None:
                    # A new distribution gets parameters that suit it, with
                    # the old mean: checked against the OLD numbers, Fixed
                    # 0.25 -> Binomial was refused (N Trials 0.25).
                    from raptor.core.distro_rules import params_after_change
                    try:
                        old_dist = int(float(row.get("Dist", "19")))
                    except (TypeError, ValueError):
                        old_dist = dist
                    seeded = params_after_change(
                        old_dist, params, dist,
                        "Phase" if section_name == "PHASES" else "Trigger")
                    if seeded is not None:
                        params = list(seeded)
                if dist is not None and params is not None:
                    label = "Phase" if section_name == "PHASES" else "Trigger"
                    err = validate_distro_params(dist, params, label)
                    if err:
                        self._reload_keyed_row(table, row_idx, sec,
                                               key_col, key)
                        self.validation_failed.emit(f"{key}: {err}")
                        return

        stack = self._view._stack
        stack.beginMacro(f"{key}: {col}")
        try:
            self._view.set_keyed_cell(section_name, key_col, target_key,
                                      col, value)
            if seeded is not None:
                for i, pv in enumerate(seeded, start=1):
                    if row.get(f"Param{i}") != f"{pv:.6f}":
                        self._view.set_keyed_cell(
                            section_name, key_col, target_key,
                            f"Param{i}", f"{pv:.6f}")
        finally:
            stack.endMacro()
        if item.column() == 0:
            item.setData(Qt.ItemDataRole.UserRole, value)
        self.model_edited.emit()
        if col == "Dist":
            # Re-populate so the Dist cell shows the resolved NAME
            # (not whatever the user typed, e.g. a bare code) and so
            # Param1-3 follow the new distribution immediately, not
            # just on the next unrelated refresh.
            self._populate_keyed_guarded(table)
        elif section_name in ("SPARE_POOL_DATA", "RESOURCE_DATA"):
            # Keep the key-cell's plain-English tooltip fresh -- see
            # `_commit_keyed_float`'s identical re-populate for these
            # two sections' time-dimensioned (cell-widget) columns.
            self._populate_keyed_guarded(table)

    def _reload_keyed_row(self, table, row_idx, sec, key_col, key):
        """Revert an invalid edit by re-reading the file row."""
        table.blockSignals(True)
        for r in sec.rows:
            if r.get(key_col) == key:
                for ci, col in enumerate(sec.columns):
                    it = table.item(row_idx, ci)
                    if it is not None and it.text() != r.get(col, ""):
                        it.setText(r.get(col, ""))
                break
        table.blockSignals(False)

    def _on_tab_changed(self, index: int):
        table = self._tabs.widget(index)
        keyed = any(table is t for t in self._keyed)
        self._add_row_btn.setVisible(keyed)
        self._del_row_btn.setVisible(keyed)
        if (isinstance(table, QTableWidget)
                and table in self._dirty_tables and self._view is not None):
            self._dirty_tables.discard(table)
            table.setSortingEnabled(False)
            self._populate_table(table)
            self._label_cell_widgets(table)
            section, order = self._sort_choice.get(
                table, (-1, Qt.SortOrder.AscendingOrder))
            self._sort_restoring = True
            try:
                table.horizontalHeader().setSortIndicator(section, order)
                table.setSortingEnabled(True)
            finally:
                self._sort_restoring = False
            self._ensure_header_widths(table)
            self._sync_row_headers(table)
            self._update_empty_state(table)

    _KEYED_TEMPLATES = {
        "SPARE_POOL_DATA": {"PoolName": "Pool", "RSO": "1", "Stock": "1",
                            "NewSp": "1", "ArriveEvery": "720.000000",
                            "ESO": "0", "EmerTime": "24.000000",
                            "SLO": "0", "Level": "0", "Quan": "1",
                            "SLOTime": "24.000000", "SpCost": "0.000000",
                            "emerShipCost": "0.000000"},
        "RESOURCE_DATA": {"PoolName": "Pool", "NumIn": "1",
                          "SpareCost": "0.000000",
                          "FixedPerUse": "0.000000",
                          "PerTimeUnit": "0.000000", "Phases": "0"},
        "TRIGGERS": {"TriggerName": "Trigger", "IDnum": "0",
                     "Repeats": "1", "InitUsed": "0.000000",
                     "Dist": "8", "Param1": "288.000000",
                     "Param2": "1.000000", "Param3": "0.000000"},
        # Dist 8 = Normal duration.
        "PHASES": {"PhaseName": "Phase", "Num": "1", "Mission": "1",
                   "Dist": "8", "Param1": "5.000000",
                   "Param2": "0.100000", "Param3": "0.000000"},
    }

    def _current_keyed_table(self):
        w = self._tabs.currentWidget()
        return w if w in self._keyed else None

    def _add_keyed_row(self):
        table = self._current_keyed_table()
        if table is None or self._view is None:
            return
        section_name, key_col, _kinds = self._keyed[table]
        base = dict(self._KEYED_TEMPLATES[section_name])
        new_key = self._view.add_keyed_row(section_name, key_col, base)
        self.model_edited.emit()
        self.refresh()
        # Select the fresh row for immediate editing.
        for r in range(table.rowCount()):
            it = table.item(r, 0)
            if it is not None and it.text() == new_key:
                table.setCurrentCell(r, 0)
                return

    def _remove_selected_keyed_row(self):
        table = self._current_keyed_table()
        if table is None or self._view is None:
            return
        row_idx = table.currentRow()
        if row_idx < 0:
            return
        key_item = table.item(row_idx, 0)
        if key_item is None:
            return
        key = key_item.data(Qt.ItemDataRole.UserRole) or key_item.text()
        section_name, key_col, _kinds = self._keyed[table]
        self._view.remove_keyed_row(section_name, key_col, key)
        self.model_edited.emit()
        self.refresh()

    def _on_event_name_changed(self, item: QTableWidgetItem):
        if item.column() != 0 or self._view is None:
            return
        eid = int(req(self._events_table.item(item.row(), 1)).text())
        if eid not in self._view.events:
            return
        new_name = "_".join(item.text().split()) or \
            self._view.events[eid].name
        if new_name != item.text():
            item.setText(new_name)
            return
        if new_name != self._view.events[eid].name:
            self._view.rename_event(eid, new_name)
            self.model_edited.emit()

    def _k_cell(self, table, row, node):
        w = QWidget()
        from PySide6.QtWidgets import QHBoxLayout, QLineEdit
        from PySide6.QtGui import QIntValidator
        lay = QHBoxLayout(w)
        lay.setContentsMargins(2, 0, 2, 0)
        edit = QLineEdit(str(node.good_paths))
        validator = QIntValidator()
        validator.setBottom(1)
        edit.setValidator(validator)
        edit.setFrame(False)
        committed = {"v": node.good_paths}
        nid = node.id

        def commit():
            try:
                new_v = int(edit.text().strip())
            except ValueError:
                edit.setText(str(committed["v"]))
                return
            if new_v == committed["v"]:
                return
            self._apply_rows(
                nid, "Required good (k)", str(new_v),
                lambda k: req(self._view).set_node_good_paths(k, new_v)
                or True)
            committed["v"] = new_v
            self.model_edited.emit()

        edit.editingFinished.connect(commit)
        lay.addWidget(edit)
        table.setCellWidget(row, 3, w)

    # -- mutation adapters (validate -> DiagramView) --------------------

    def _on_block_name_changed(self, item: QTableWidgetItem):
        if item.column() != 0 or self._view is None:
            return
        bid = int(req(self._blocks_table.item(item.row(), 1)).text())
        if bid not in self._view.blocks:
            return          # another level's row (Whole model), read-only
        new_name = "_".join(item.text().split()) or self._view.blocks[bid].name
        if new_name != item.text():
            item.setText(new_name)  # re-enters this handler with the fixed text
            return
        if new_name != self._view.blocks[bid].name:
            self._view.rename_block(bid, new_name)
            self.model_edited.emit()

    def _on_node_name_changed(self, item: QTableWidgetItem):
        if item.column() != 0 or self._view is None:
            return
        nid = int(req(self._nodes_table.item(item.row(), 1)).text())
        if nid not in self._view.nodes:
            return
        new_name = "_".join(item.text().split()) or self._view.nodes[nid].node_name
        if new_name != item.text():
            item.setText(new_name)
            return
        if new_name != self._view.nodes[nid].node_name:
            self._view.rename_node(nid, new_name)
            self.model_edited.emit()

    def _entry_values(self, distro: int, values: list[float]) -> list[float]:
        """Canonical values -> what the entry-mode shows, in shown units."""
        from raptor.gui.ui.units_conv import native_to_display
        native_u = getattr(self, "_native_units", "") or ""
        display_u = getattr(self, "_display_unit", None)
        labels = DISTRO_PARAM_LABELS.get(distro, ("", "", ""))
        # Empirical distributions carry up to 12 data points, while the
        # label table describes the three named parameters. Values past the
        # label list have no time-parameter label, so they are shown as-is;
        # indexing `labels` by the value position crashed the whole model
        # load for `Distribution1_Empirical12`.
        shown = [
            native_to_display(v, native_u, display_u)
            if j < len(labels) and _is_time_param(labels[j]) else v
            for j, v in enumerate(values)]
        return param_entry.to_entry(distro, shown)

    def _has_param(self, bid: int, which: str, index: int) -> bool:
        """Whether block `bid`'s distribution has a `index`-th parameter."""
        b = req(self._src()).blocks.get(bid)
        if b is None:
            return False
        code = b.fail_distro if which == "F" else b.repair_distro
        return index < DIST_NPARAMS_COUNT.get(code, 3)

    def _set_param_entry(self, bid: int, which: str, index: int,
                         raw: float) -> bool:
        use = "Failure" if which == "F" else "Repair"
        return self._apply_rows(
            bid, f"{use} Param {index + 1}", f"{raw:g}",
            lambda k: self._set_param_entry_one(k, which, index, raw),
            applicable=lambda k: self._has_param(k, which, index))

    def _set_param_entry_one(self, bid: int, which: str, index: int,
                             raw: float) -> bool:
        """Commit one cell typed in Raptor's entry mode."""
        block = req(self._view).blocks.get(bid)
        if block is None:
            return False
        distro = block.fail_distro if which == "F" else block.repair_distro
        use = "Failure" if which == "F" else "Repair"
        old = list(block.fail_values if which == "F" else block.repair_values)
        if param_entry.is_rate_field(distro, index) and raw <= 0.0:
            self.validation_failed.emit(f"{use} Rate must be greater than 0")
            return False
        entered = self._entry_values(distro, (old + [0.0] * 3)[:3])
        entered[index] = raw
        from raptor.gui.ui.units_conv import native_to_display, display_to_native
        native_u = getattr(self, "_native_units", "") or ""
        display_u = getattr(self, "_display_unit", None)
        base_labels = DISTRO_PARAM_LABELS.get(distro, ("", "", ""))
        shown_old = [
            native_to_display(v, native_u, display_u)
            if _is_time_param(base_labels[j]) else v
            for j, v in enumerate((old + [0.0] * 3)[:3])]
        converted = param_entry.from_entry(distro, entered, old=shown_old)
        vals = [
            display_to_native(converted[j], native_u, display_u)
            if _is_time_param(base_labels[j]) else converted[j]
            for j in range(3)]
        err = validate_distro_params(distro, vals, use)
        if err is not None:
            return False
        if which == "F":
            req(self._view).set_block_fail_params(bid, vals)
        else:
            req(self._view).set_block_repair_params(bid, vals)
        self.model_edited.emit()
        return True

    def _set_param(self, bid: int, which: str, index: int, value: float) -> bool:
        use = "Failure" if which == "F" else "Repair"
        return self._apply_rows(
            bid, f"{use} Param {index + 1}", f"{value:g}",
            lambda k: self._set_param_one(k, which, index, value),
            applicable=lambda k: self._has_param(k, which, index))

    def _set_param_one(self, bid: int, which: str, index: int,
                       value: float) -> bool:
        block = req(self._view).blocks.get(bid)
        if block is None:
            return False
        distro = block.fail_distro if which == "F" else block.repair_distro
        use = "Failure" if which == "F" else "Repair"
        vals = list(block.fail_values if which == "F" else block.repair_values)
        vals[index] = value
        err = validate_distro_params(distro, vals, use)
        if err is not None:
            return False
        if which == "F":
            req(self._view).set_block_fail_params(bid, vals)
        else:
            req(self._view).set_block_repair_params(bid, vals)
        self.model_edited.emit()
        return True

    def _set_dist(self, bid: int, which: str, code: int) -> bool:
        # Routed through the view so the change lands on the undo stack.
        use = "Failure Dist" if which == "F" else "Repair Dist"
        ok = self._apply_rows(
            bid, use, distro_display_name(code, is_failure=which == "F"),
            lambda k: req(self._view).change_block_dist(k, which, code))
        if ok:
            self.model_edited.emit()
        return ok

    def _set_pm(self, bid: int, value: float) -> bool:
        self._apply_rows(
            bid, "PM Frequency", f"{value:g}",
            lambda k: req(self._view).set_block_pm_freq(k, value) or True)
        self.model_edited.emit()
        return True
