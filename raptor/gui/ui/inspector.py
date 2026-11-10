"""Property inspector dock — tabbed view of all element properties.

Shows property groups matching Raptor 7's property dialog tabs, reading
from live RBDBlock/RBDNode model objects on DiagramView. The 7 tab slots are
a shared widget pool relabelled per element kind (see `_TAB_KIND_CONFIG`).

Distribution numbering, parameter labels and validation rules port
Raptor 7 (`ConvertToString`, `GetParameters`,
`CheckValidInput`); the models under `tests/data/models/` exercise the tabs.
"""
from __future__ import annotations

from raptor.gui.ui.qtcompat import req

import os
from typing import Any, cast
from collections.abc import Callable
from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QWidget, QLayout, QVBoxLayout, QScrollArea, QLabel, QFormLayout, QHBoxLayout, QTabWidget, QLineEdit,
    QCheckBox, QSpinBox, QDoubleSpinBox, QComboBox, QPushButton,
    QRadioButton, QTableWidget,
    QFrame,
)
from raptor.core.annotations import ANNO_LOCATIONS

from raptor.core.fileio.comments import comment_text
from raptor.core.fileio.rbd import RbdFile
from raptor.core.fileio.typed import system_config_from_row
# The distribution rules are `raptor.core.distro_rules`'s, not this
# module's (review D-04) -- they are pure model rules and belong in a
# Qt-free home. Imported here because this widget still uses both.
from raptor.core.model import SystemConfig
from raptor.gui.resources.design_tokens import (
    ThemeManager, SPACING, active_colour,
)
from raptor.gui.ui.inspector_rows_mixin import InspectorRowsMixin
from raptor.gui.ui.inspector_block_mixin import InspectorBlockMixin
from raptor.gui.ui.inspector_annotation_mixin import InspectorAnnotationMixin
from raptor.gui.ui.inspector_node_mixin import InspectorNodeMixin

from raptor.gui.ui.inspector_common import (  # noqa: F401  (names tests and callers import from here)
    RAPTOR7_UNITS,
    COMMENT_COMMIT_DELAY_MS,
    _CommentEdit,
    _SIM_START,
    _i_raw,
    _row_float,
    _stream_label,
    parse_flow_cell,
    _WordWrapCheckBox,
    _FormulaCmd,
    _ANNO_KIND_LABELS,
    _ANCHOR_LOCATION_LABELS,
    _vbox,
)


class InspectorDock(InspectorNodeMixin, InspectorAnnotationMixin,
                    InspectorBlockMixin, InspectorRowsMixin, QWidget):
    #: Emitted when the user renames the currently-shown block/node via
    #: the editable Name field (on Enter or focus-loss, only if the text
    #: actually changed). `MainWindow` owns applying this to the live
    #: DiagramView model + canvas label + explorer tree + dirty state --
    #: this dock only knows about the currently displayed element, not the
    #: rest of the app.
    block_renamed = Signal(int, str)
    node_renamed = Signal(int, str)
    hier_renamed = Signal(int, str)
    event_renamed = Signal(int, str)
    block_costs_changed = Signal(int, list)
    #: (event_id, field, value) -- field currently 'param'
    event_field_changed = Signal(int, str, float)
    block_pm_dist_changed = Signal(int, int)
    block_pm_params_changed = Signal(int, list)
    block_pre_ldt_params_changed = Signal(int, list)
    block_post_ldt_params_changed = Signal(int, list)
    block_always_done_changed = Signal(int, bool)
    block_pm_stagger_changed = Signal(int, float)
    #: [nothing, idle, pm, fail] percentages + threshold
    block_dep_changed = Signal(int, list)
    block_init_stock_changed = Signal(int, int)
    #: kind in ("block", "node", "event"); payload = [(code, value)]
    phasing_changed = Signal(str, int, bool, list)
    #: (block_id, [param1, param2, param3]) -- always the full triple, even
    #: when only one field was actually edited, so the receiver never has
    #: to merge partial updates itself.
    block_fail_params_changed = Signal(int, list)
    block_repair_params_changed = Signal(int, list)
    #: (block_id, distro_code) -- Failure/Repair Type combo
    block_fail_type_changed = Signal(int, int)
    block_repair_type_changed = Signal(int, int)
    #: (block_id, stream_int) -- ±1..max independent (negative =
    #: antithetic) or 201..210 system A..J. A SYSTEM pick is emitted for
    #: BOTH sides at once (legacy writes both from one combo).
    block_fail_stream_changed = Signal(int, int)
    block_repair_stream_changed = Signal(int, int)
    block_pm_freq_changed = Signal(int, float)
    #: (block_id, on) -- PM tab's own enable checkbox (Raptor 7:
    #: "engage the checkbox at the top of this tab"). Gates whether the
    #: rest of the tab (frequency/trigger/distribution/options) shows
    #: at all, same as Raptor 7.
    block_pm_enabled_changed = Signal(int, bool)
    #: (block_id, key, on) -- PM tab's 5 option checkboxes, key in
    #: {"spare_needed","refresh","mis_defer","fail_reset","req_defer"}
    #: (`DiagramView._PM_OPTION_ATTRS`'s own keys).
    block_pm_option_changed = Signal(int, str, bool)
    #: (block_id, dependency_num, dep_type) -- Block Dependency tab
    #: master-element selector, same shape/semantics as `node_dep_
    #: changed` (the underlying widget, `_dependency_group`, is shared).
    block_dep_master_changed = Signal(int, int, str)
    #: (block_id, on) -- Dependency tab's "default dependency action"
    #: radio (Operating state / Idle state).
    block_dep_default_idle_changed = Signal(int, bool)
    #: (block_id, pool_name) -- Resources tab pool assignment.
    block_resource_pool_changed = Signal(int, str)
    #: (block_id, [num_needed_for_repair, num_needed_for_pm]).
    block_resource_counts_changed = Signal(int, list)
    #: (block_id, [new_spares_count, arrival_interval]) -- Custom-
    #: sparing's own new-spares fields (same CUSTOM-gated shape as
    #: `block_init_stock_changed`).
    block_new_spares_changed = Signal(int, list)
    #: (block_id, value) -- Raptor 7's Advanced tab "Standby stress"
    #: field, folded into our Failure tab (no free tab slot -- same
    #: precedent as Simulation Theory being folded into Failure/Repair).
    block_standby_stress_changed = Signal(int, float)
    #: (block_id, value) -- duty cycle / start age / restoration.
    block_duty_cycle_changed = Signal(int, float)
    block_start_age_changed = Signal(int, float)
    block_restoration_factor_changed = Signal(int, float)
    block_restoration_type_changed = Signal(int, int)
    #: (block_id, on) -- Growth/Decay enable (real GDType 0 <-> last
    #: non-zero type).
    block_gd_enabled_changed = Signal(int, bool)
    #: (block_id, gd_type) -- Linear(1)/Geometric(2)/Asymptotic(3).
    block_gd_type_changed = Signal(int, int)
    #: (block_id, [rate, limit]).
    block_gd_rate_limit_changed = Signal(int, list)
    node_good_paths_changed = Signal(int, int)
    #: Emitted with a human-readable message when an edit was rejected by
    #: the distribution-domain validation (the field itself reverts).
    #: Status-bar material, deliberately not a modal box.
    validation_failed = Signal(str)
    #: the results-notice banner's "Clear results" button
    clear_results_requested = Signal()
    #: (block_id, comment text) — Raptor 7 comment editing
    block_comment_changed = Signal(int, str)
    #: (etype, id, comment text) — same Raptor 7 comment editing as
    #: `block_comment_changed`, for node/event/hier (whose General tabs
    #: carry the Comment box Raptor 7's Node/Event/Hierarchy
    #: Properties dialogs have). One generic signal rather than three
    #: more per-type ones, matching `DiagramView.set_element_comment`'s
    #: own `(etype, oid, text)` signature.
    element_comment_changed = Signal(str, int, str)
    #: (etype, id) -- General > Identity's "Convert comment to annotation"
    #: button. MainWindow forwards it to `DiagramView.comment_to_annotation`
    #: (same split as `block_comment_changed` -> `set_element_comment`).
    comment_to_annotation_requested = Signal(str, int)
    #: suppress comment echo while populating
    _populating_comment = False
    #: (column, value) — SYSTEM_DATA threshold edits from the General
    #: tab (GYthreshold / YRthreshold, Raptor 7 "Colour Thresholds").
    system_threshold_changed = Signal(str, float)
    #: {SYSTEM_DATA column: new value} from the Inspector's Project
    #: Settings page -- one writer in MainWindow applies them undoably.
    system_config_changed = Signal(dict)
    #: System comment text (COMMENTS section, Type=System).
    system_comment_changed = Signal(str)
    #: Raptor 7 Analysis Type ("Availability"/"Dependability").
    node_anal_type_changed = Signal(str)
    #: (block_id, triggered: bool, trigger-name: str) — PM trigger
    block_pm_trig_changed = Signal(int, bool, str)
    #: (block_id, distro-code) — pre/post-repair logistics delay type
    block_pre_ldt_dist_changed = Signal(int, int)
    block_post_ldt_dist_changed = Signal(int, int)
    #: (block_id, SparingType-name) — sparing replacement strategy
    sparing_type_changed = Signal(int, str)
    #: (block_id, pool-name) — spare pool assignment
    pool_name_changed = Signal(int, str)
    #: pool_dialogs.py section key ("SPARE_POOL_DATA", "RESOURCE_DATA",
    #: "TRIGGERS", "PHASES") — contextual "Manage…" buttons, mirroring
    #: Raptor 7's inline "Create Spare Pool"/"Create Trigger"/"Create
    #: Resources" buttons on the Block Properties tabs.
    manage_pool_requested = Signal(str)
    #: (node_id, on) -- Capacity tab "Unrestricted flow"/"Use any
    #: available path" checkboxes (fullflow/divFlow columns).
    node_full_flow_changed = Signal(int, bool)
    node_any_path_changed = Signal(int, bool)
    #: Capacity flow-assignment cell committed:
    #: (link_id, column("nomFlow"|"MaxFlow"|"CapPri"), old, new) --
    #: routed through DiagramView.set_link_flow_field, one undoable
    #: IdCellCmd per commit.
    flow_cell_changed = Signal(int, str, str, str)
    #: (node_id, on) -- Advanced tab "Include node analysis information
    #: for this node in the output tables and reports" (repNA column).
    node_report_anal_changed = Signal(int, bool)
    #: (node_id, dependency_num, dep_type) -- Dependency tab master-
    #: element selector (0=independent, -1=locally dependent,
    #: -2=system dependent, >0+"RBDBlock"=dependent on that block).
    node_dep_changed = Signal(int, int, str)
    #: (node_id, on) -- Standby tab's three checkboxes (Raptor 7's):
    #: "engage standby logic" (Cold column/cold_standby --
    #: the first checkbox, which the manual says "engages the
    #: remainder of the tab"), "return to primary once repaired" (PR
    #: column/priority_return), "try auto-switchover before manual"
    #: (Autos column/check_autos_first).
    node_cold_standby_changed = Signal(int, bool)
    node_priority_return_changed = Signal(int, bool)
    node_check_autos_first_changed = Signal(int, bool)
    #: (node_id, k_star) -- Standby tab's k* value (k* column).
    node_k_star_changed = Signal(int, int)
    #: (link_id, column, old, new) -- Standby tab's per-incoming-link
    #: switchover table (LINK_DATA ColdPri/AProb/AutoTime/ManTime).
    link_standby_cell_changed = Signal(int, str, str, str)
    #: (event_id, [initial, per_success, per_failure])
    event_costs_changed = Signal(int, list)
    #: (annotation_id, {prop_key: new_value}) -- Colours & Font tab.
    #: A dict (not one signal per field) since the receiver
    #: (`DiagramView.set_annotation_props`) snapshots the full before/
    #: after state for one `AnnoEditCmd`, matching the standalone dialog
    #: this replaced.
    annotation_prop_changed = Signal(int, dict)
    #: (annotation_id) -- the Inspector's "Pick on canvas…" button: the
    #: canvas arms a click-to-anchor mode. Kept as a signal rather than a
    #: direct `anchor_source` call so the dock stays usable without a view.
    annotation_anchor_pick_requested = Signal(int)
    #: (annotation id, "from" | "to"): the canvas pick for one arrow end.
    annotation_arrow_pick_requested = Signal(int, str)
    #: Fired when the Rates library itself changes via a field's
    #: "Use saved rate…" picker (add/edit while browsing) -- the
    #: Inspector never marks the model dirty itself for anything else
    #: (that's always routed through a specific field-changed signal),
    #: but `RatesDialog` doesn't hold a MainWindow reference to call
    #: `_mark_dirty` directly (see rates_dialog.py's own docstring).
    rates_changed = Signal()

    #: Which of the 7 fixed tab slots (General/Failure/Repair/Spares/PM/
    #: Deps/Costs, in that index order) are relevant for each element
    #: kind, and what that slot is relabelled to -- e.g. slot 1
    #: ("Failure") becomes "Capacity" for a node. Matches Raptor 7's
    #:properties dialogs: a Block dialog has all 8 of its own tabs (7
    #: map here 1:1; the 8th, Simulation Theory, is folded into the
    #: Failure/Repair stream editors). A Node dialog has General/Capacity/
    #: Dependency/Advanced always, plus a Standby tab (reuses slot 2, "Repair", otherwise idle for nodes). An
    #: Event dialog has only General/Costs/Advanced, and a Hierarchy
    #: dialog only General (its own Dependency tab isn't ported -- the
    #: engine doesn't simulate hierarchy-level dependency at all, see
    #: `raptor.core.engine.dependency`'s module docstring, so an editable
    #: selector here would silently do nothing).
    _TAB_KIND_CONFIG = {
        "block": {
            0: ("⏺ General", "General — identity, position, and "
                "simulation-start state"),
            1: ("✕ Failure", "Failure distribution and random-number "
                "stream"),
            2: ("↻ Repair", "Repair distribution and random-number "
                "stream"),
            3: ("⛁ Spares", "Sparing strategy, spare pools, and "
                "logistics delay time (LDT)"),
            4: ("⌘ PM", "PM — Preventive Maintenance schedule and "
                "options"),
            5: ("⇢ Deps", "Deps — Dependencies on other elements"),
            6: ("⏣ Costs", "Cost rates"),
        },
        "node": {
            0: ("⏺ General", "Identity, position, connections, and "
                "K-out-of-N logic"),
            1: ("▤ Capacity", "Flow-in / flow-out routing"),
            2: ("⇄ Standby", "Standby switchover — engage cold-standby "
                "logic, priority return, auto-switch order, and the "
                "k* target"),
            5: ("⇢ Dependency", "Dependency on another element"),
            6: ("⚙ Advanced", "Phasing"),
        },
        "event": {
            0: ("⏺ General", "Name, comment, and firing probability"),
            1: ("⏣ Costs", "Cost per initial/success/failure"),
            2: ("⚙ Advanced", "Random-number stream and phasing"),
        },
        "hier": {
            0: ("⏺ General", "Name, nesting, and In/Out markers"),
        },
        "system": {
            0: ("⏺ General", "Project-wide simulation, analysis, and "
                "display settings"),
        },
        "annotation": {
            0: ("⏺ General", "Colours and font for this canvas "
                "annotation"),
        },
    }

    def __init__(self, parent=None):
        super().__init__(parent)
        self._rbd: RbdFile | None = None
        self._stack = None   # adopted from MainWindow (set_undo_stack)
        self._current_block_id: int | None = None
        self._current_node_id: int | None = None
        # Optional callable -> bool, adopted from MainWindow
        # (`set_animate_state_provider`). It reports the LIVE animation
        # state so the Display group can answer "is the run animating?"
        # truthfully. Animation is application state, not model data, so
        # it cannot be read off the model -- `SYSTEM_DATA/graphicsUse` is
        # a legacy column nothing writes, and showing it would put a
        # value on screen that has never governed anything.
        self._animate_state: Callable[[], bool] | None = None
        # What `show_*` was last called with, so a view-refresh (undo/
        # redo, or any other model mutation) can re-populate the same
        # element from the current model instead of leaving the stale
        # populate-time values on screen.
        self._current_target: tuple[str, object] | None = None

        self._header = QLabel("No selection")
        self._header.setObjectName("InspectorHeader")
        self._header.setWordWrap(True)

        # Banner line above the tabs: either "read-only" (padlock) or "your
        # first edit will ask about discarding the displayed results". It
        # says so here instead of just greying the form out, so the user
        # can still view the parameters and see why.
        self._lock_banner = QFrame()
        self._lock_banner.setObjectName("InspectorLockBanner")
        self._lock_banner.setHidden(True)
        bl = QHBoxLayout(self._lock_banner)
        bl.setContentsMargins(SPACING["sm"], SPACING["xs"], SPACING["sm"],
                              SPACING["xs"])
        bl.setSpacing(SPACING["sm"])
        self._lock_icon_lbl = QLabel()
        self._lock_icon_lbl.setFixedWidth(20)
        self._lock_icon_lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
        bl.addWidget(self._lock_icon_lbl)
        self._lock_text = QLabel(self._READ_ONLY_TEXT)
        self._lock_text.setObjectName("InspectorLockText")
        self._lock_text.setWordWrap(True)
        bl.addWidget(self._lock_text, 1)
        self._lock_btn = QPushButton("Clear results to edit")
        self._lock_btn.setToolTip("Discard the results on screen so the model can be edited without a prompt")
        self._lock_btn.clicked.connect(self.clear_results_requested)
        self._lock_btn.setVisible(False)
        bl.addWidget(self._lock_btn)

        # Empty state: what to do, and what this model holds, instead of
        # eight empty property tabs above a blank form.
        self._empty = QLabel()
        self._empty.setObjectName("InspectorEmpty")
        self._empty.setWordWrap(True)
        self._empty.setAlignment(Qt.AlignmentFlag.AlignTop
                                 | Qt.AlignmentFlag.AlignLeft)
        self._empty.setMargin(SPACING["md"])
        self._empty.setAccessibleName("Nothing selected")

        self._tabs = QTabWidget()
        # Tab persistence: keep the same tab open when switching between
        # elements. `_populating` guards against programmatic
        # setCurrentIndex overwriting the user's choice.
        self._user_tab_index = 0
        self._populating = False
        #: Builders for tabs a lazy block selection has not built yet.
        #: They capture the selected BLOCK, so they MUST be dropped as soon
        #: as the Inspector shows anything else -- otherwise opening a
        #: node's Capacity tab later runs the old block's Failure builder
        #: into the same widget slot. (The slots are a shared pool.)
        self._deferred_tabs: dict[int, object] = {}
        self._read_only = False
        #: The live typed object and its two Position labels, so a drag or
        #: undo can update X/Y without rebuilding the whole Inspector.
        self._position_source = None
        self._position_labels = None
        self._tabs.setDocumentMode(True)
        self._tabs.currentChanged.connect(self._on_tab_changed)
        self._tabs.setTabPosition(QTabWidget.TabPosition.North)

        self._scroll_general = self._make_tab("General")
        self._scroll_failure = self._make_tab("Failure")
        self._scroll_repair = self._make_tab("Repair")
        self._scroll_spares = self._make_tab("Spares")
        self._scroll_pm = self._make_tab("PM")
        self._scroll_deps = self._make_tab("Dependencies")
        self._scroll_costs = self._make_tab("Costs")

        self._tabs.addTab(self._scroll_general, "⏺ General")
        self._tabs.addTab(self._scroll_failure, "✕ Failure")
        self._tabs.addTab(self._scroll_repair, "↻ Repair")
        self._tabs.addTab(self._scroll_spares, "⛁ Spares")
        self._tabs.addTab(self._scroll_pm, "⌘ PM")
        self._tabs.addTab(self._scroll_deps, "⇢ Deps")
        self._tabs.addTab(self._scroll_costs, "⏣ Costs")
        self._configure_tabs("block")

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)
        layout.addWidget(self._header)
        layout.addWidget(self._lock_banner)
        layout.addWidget(self._empty, 1)
        layout.addWidget(self._tabs, 1)
        self._show_tabs(False)

        ThemeManager.instance().on_changed(self._refresh_theme)

    def _show_tabs(self, on: bool) -> None:
        """Tabs while something is selected; the empty state otherwise."""
        empty = getattr(self, "_empty", None)
        if empty is None:
            return
        self._tabs.setVisible(on)
        empty.setVisible(not on)
        if not on:
            self._refresh_empty_text()

    def _refresh_empty_text(self) -> None:
        rbd = getattr(self, "_rbd", None)
        if rbd is None:
            self._empty.setText(
                "Open or create a model, then select a block, node, event "
                "or hierarchy to see its properties here.")
            return

        def n(section):
            sec = rbd.sections.get(section)
            if sec is None:
                return 0
            # BLOCK_DATA's ID-0 row is the model's default-block template,
            # not a block; the status bar does not count it either.
            return sum(1 for r in sec.rows
                       if not (section == "BLOCK_DATA"
                               and str(r.get("ID", "")).strip() in ("0", "0.0", "0.000000")))

        units = getattr(self, "_units_str", "") or ""
        self._empty.setText(
            "Select a block, node, event or hierarchy on the canvas to see "
            "and edit its properties.\n\n"
            f"This model: {n('BLOCK_DATA')} blocks · {n('NODE_DATA')} nodes · "
            f"{n('EVENT_DATA')} events · {n('HIERARCHY_DATA')} hierarchies"
            + (f" · time in {units}" if units else ""))

    def _configure_tabs(self, kind: str):
        """Show only the tab slots relevant to `kind` (block/node/event/
        hier/system/annotation), relabelled per `_TAB_KIND_CONFIG` --
        the 7 slots are a shared, reused widget pool (see `_clear_tab`),
        not one fixed set per kind, so every switch must both hide the
        irrelevant ones AND restore the right label on the ones kept.

        A one-slot kind (a Hierarchy, a System, or an Annotation) also
        gets its tab STRIP hidden: a lone "General" tab above a short
        form is chrome with nothing to switch between, and it costs a
        row of height the content can use instead."""
        self._show_tabs(True)
        if kind != "block":
            # A deferred builder belongs to the block it was captured
            # from. Showing a node/event/hierarchy/system must not leave
            # one queued for a shared tab slot (Capacity/Standby reuse the
            # block Failure/Repair widgets).
            self._deferred_tabs.clear()
        cfg = self._TAB_KIND_CONFIG[kind]
        for i in range(self._tabs.count()):
            visible = i in cfg
            if self._tabs.isTabVisible(i) != visible:
                self._tabs.setTabVisible(i, visible)
            if visible:
                text, tip = cfg[i]
                # Each call re-lays out the tab strip: only touch what changed (selection clicks do this often).
                if self._tabs.tabText(i) != text:
                    self._tabs.setTabText(i, text)
                if self._tabs.tabToolTip(i) != tip:
                    self._tabs.setTabToolTip(i, tip)
        if self._tabs.tabBar().isVisible() != (len(cfg) > 1):
            self._tabs.tabBar().setVisible(len(cfg) > 1)

    def _on_tab_changed(self, index: int):
        if not getattr(self, "_populating", False):
            self._user_tab_index = index
        self._run_deferred(index)

    #: Build only the shown tab on a canvas selection (see `_populate_block_inner`). The test suite turns this off
    #: (`RAPTOR_INSPECTOR_EAGER_TABS=1`, set in tests/conftest.py) so tests can read fields on any tab.
    lazy_tabs = os.environ.get("RAPTOR_INSPECTOR_EAGER_TABS") != "1"

    def _run_deferred(self, index: int) -> None:
        """Build a tab that `_defer_tabs` left for later, now that it is about to be shown."""
        build = getattr(self, "_deferred_tabs", {}).pop(index, None)
        if build is None:
            return
        was = getattr(self, "_populating", False)
        self._populating = True            # building must not be taken for the user picking a tab
        try:
            build()
        finally:
            self._populating = was
        self._apply_read_only()

    def ensure_all_tabs_built(self) -> None:
        """Build every tab that is still waiting (tests and automation that read fields on tabs not shown)."""
        for index in sorted(getattr(self, "_deferred_tabs", {})):
            self._run_deferred(index)

    def flush_pending_comment(self) -> bool:
        """Commit an un-debounced Comment draft now, if there is one.

        Save/autosave/close call this so a sentence typed in the last
        few hundred milliseconds is not lost to the debounce. Returns
        whether a draft was actually pending.
        """
        edit = getattr(self, "_comment_edit", None)
        if edit is None:
            return False
        try:
            pending = edit.has_pending()
            edit.commit()
        except RuntimeError:          # widget already deleted by a rebuild
            return False
        return pending

    def set_comment_source(self, etype: str, oid: int, stored: str) -> bool:
        """Update the live Comment editor for a model-side comment change.

        Called by MainWindow from `DiagramView.comment_changed` (undo/redo
        included) when this pane still shows that element. Changing the
        editor's contents in place keeps the widget, caret and pane scroll
        stable instead of rebuilding General and jumping to the bottom.
        """
        if getattr(self, "_current_target", None) != (etype, oid):
            return False
        edit = getattr(self, "_comment_edit", None)
        if edit is None:
            return False
        try:
            edit.set_source(str(stored or "").replace("~", "\n"))
        except RuntimeError:          # widget already deleted by a rebuild
            return False
        return True

    def _begin_populate(self):
        # A draft belongs to the element being replaced; flush it before
        # the old editor is deleted. The commit callbacks are not gated on
        # `_populating`, so a timer/focus-out that lands during a rebuild
        # cannot silently discard a real user edit either.
        self.flush_pending_comment()
        self._populating = True

    def _restore_tab(self):
        """End a populate: re-open the tab the USER had selected, not
        the default first tab. Falls back to the first VISIBLE tab if
        the remembered index is out of range or hidden for this kind
        (e.g. the user was on Node's Capacity tab and then selected a
        Hierarchy, which doesn't have one)."""
        idx = min(getattr(self, "_user_tab_index", 0),
                  max(self._tabs.count() - 1, 0))
        if not self._tabs.isTabVisible(idx):
            idx = next((i for i in range(self._tabs.count())
                       if self._tabs.isTabVisible(i)), 0)
        self._populating = False
        self._tabs.setCurrentIndex(idx)
        # Pages were just rebuilt for this selection: re-apply the
        # read-only lock to the new fields (tab strip itself stays
        # clickable -- see set_read_only).
        self._apply_read_only()

    def _make_tab(self, name: str) -> QScrollArea:
        c = active_colour()
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        scroll.setStyleSheet(f"QScrollArea {{ border: none; background: {c['surface']}; }}")

        content = QWidget()
        content.setObjectName("InspectorContent")
        content.setStyleSheet(f"#InspectorContent {{ background: {c['surface']}; }}")
        self._layout = QVBoxLayout(content)
        self._layout.setContentsMargins(SPACING["sm"], SPACING["xs"], SPACING["sm"], SPACING["sm"])
        self._layout.addStretch()
        scroll.setWidget(content)
        return scroll

    def _system_comment(self) -> str:
        """The COMMENTS row's system comment, if the model has one."""
        if self._rbd is None:
            return ""
        sec = self._rbd.sections.get("COMMENTS")
        if sec is None:
            return ""
        for row in sec.rows:
            if row.get("Type", "") == "System":
                return row.get("Comment", "") or ""
        return ""

    def _comment_for(self, etype: str, oid: int, name: str) -> str:
        """Legacy COMMENTS section entry for this element ('~' separates
        lines, e.g. 'Part # 178830-SP5~Failure data from Skylab').

        Uses the same authoritative Type+ID resolver as the canvas: an
        ID-bearing row belonging to a different same-named element is
        never adopted; only a legacy row with no ID falls back to name.
        """
        if self._rbd is None:
            return ""
        return comment_text(
            self._rbd.sections.get("COMMENTS"), etype, oid, name
        ).replace("~", "\n")

    def set_undo_stack(self, stack):
        """Adopt the application-level undo stack so formula-record
        edits (which the view's own element commands don't carry) can be
        wrapped in a `_FormulaCmd`."""
        self._stack = stack

    def set_animate_state_provider(self, fn: Callable[[], bool] | None):
        """Adopt a callable reporting the live animation state.

        Injected rather than reached for, so the Inspector keeps no
        dependency on MainWindow -- the same shape as `set_undo_stack`.
        Without it the Display group says "(unavailable)" rather than
        inventing a value.
        """
        self._animate_state = fn

    def refresh_animate_row(self) -> None:
        """Re-show the animation row after the toggle changes.

        Called by MainWindow rather than re-populating the whole system
        config: a display setting that writes nothing to the model must
        not repopulate every field just to move one label.
        """
        if self._current_target == ("system", None):
            self.show_system_config()

    def set_rbd(self, rbd: RbdFile | None):
        """Bind the inspector to a loaded model (for units + raw-row reads)."""
        self._rbd = rbd
        # Time units for this model -- shown on time-dimension parameter
        # labels (e.g. 'Mean (hours)').
        self._units_str = ""
        if rbd is not None:          # None: the model was closed
            try:
                from raptor.core.fileio.typed import system_config_from_row
                self._units_str = system_config_from_row(
                    rbd.sections["SYSTEM_DATA"].rows[0]).units
            except (ValueError, KeyError, IndexError, AttributeError):
                pass
        # Display-unit override (view/enter e.g. years on an hours-native
        # model) -- persisted per-model (.rbds-only), read back here same
        # as the native units string above.
        self._display_unit = getattr(rbd, "display_unit", None)
        if getattr(self, "_empty", None) is not None \
                and self._empty.isVisibleTo(self):
            self._refresh_empty_text()

    def set_display_unit(self, unit: str | None):
        """Live-change the display unit for the CURRENTLY open model
        (the ribbon/Preferences control calls this). Does not by
        itself refresh an already-populated field -- the caller must
        re-show the current element for the new unit to take effect,
        same as any other model-level setting change."""
        self._display_unit = unit
        if self._rbd is not None:
            self._rbd.display_unit = unit

    def clear_selection(self):
        """Show the empty-state header and clear every tab."""
        # Centralised here: every "target gone" path (delete, collapse,
        # level change, file close) destroys the Comment editor, and a
        # pending draft must reach the model first.
        self.flush_pending_comment()
        self._header.setText("No selection")
        self._show_tabs(False)
        self._current_block_id = None
        self._current_node_id = None
        self._current_target = None
        for i in range(self._tabs.count()):
            self._clear_tab(cast(QScrollArea, self._tabs.widget(i)))

    def current_target(self) -> tuple[str, object] | None:
        """The `(kind, id)` last shown, for a caller-driven refresh."""
        return getattr(self, "_current_target", None)

    def refresh_position_display(self) -> None:
        """Show the selected block/node's CURRENT X/Y.

        Geometry edits take the light `layout_changed` path (no full
        Inspector rebuild), so without this the Position rows kept the
        coordinates they had when the element was selected -- most visible
        after dragging and then undoing, when the canvas moved back but the
        panel still showed the dragged-to position."""
        target = self.current_target()
        source = getattr(self, "_position_source", None)
        labels = getattr(self, "_position_labels", None)
        if (target is None or target[0] not in ("block", "node")
                or source is None or not labels):
            return
        labels[0].setText(f"{source.x_position:.3f}")
        labels[1].setText(f"{source.y_position:.3f}")

    def show_block(self, block_id: int, blocks: dict | None = None):
        """Populate all block tabs from one RBDBlock."""
        self._current_target = ("block", block_id)
        self._blocks_source = blocks
        self._begin_populate()
        if blocks and block_id in blocks:
            self._populate_block(blocks[block_id])
            return
        if self._rbd is None:
            return
        from raptor.core.fileio.typed import block_from_row
        section = self._rbd.sections.get("BLOCK_DATA")
        if section is None:
            return
        for row in section.rows:
            try:
                b = block_from_row(row)
            except (ValueError, KeyError):
                continue
            if b.id == block_id:
                self._populate_block(b)
                return
        self._header.setText(f"Block {block_id} — not found")

    def show_node(self, node_id: int, nodes: dict | None = None):
        """Populate the node tab."""
        self._current_target = ("node", node_id)
        if nodes and node_id in nodes:
            self._populate_node(nodes[node_id])
            return
        if self._rbd is None:
            return
        from raptor.core.fileio.typed import node_from_row
        section = self._rbd.sections.get("NODE_DATA")
        if section is None:
            return
        for row in section.rows:
            try:
                n = node_from_row(row)
            except (ValueError, KeyError):
                continue
            if n.id == node_id:
                self._populate_node(n)
                return
        self._header.setText(f"Node {node_id} — not found")

    def show_system_config(self):
        """Populate the system-level Analysis group."""
        # Replacing General with the system page deletes the element's
        # Comment editor; flush its draft first.
        self.flush_pending_comment()
        self._current_target = ("system", None)
        if self._rbd is None:
            return
        section = self._rbd.sections.get("SYSTEM_DATA")
        if section is None or not section.rows:
            return
        try:
            cfg = system_config_from_row(section.rows[0])
        except (ValueError, KeyError):
            return
        self._populate_system_config(cfg)

    def _clear_tab(self, scroll: QScrollArea):
        if scroll is getattr(self, "_scroll_general", None):
            # The Position labels live on General and die with it. A block
            # selected while another tab is open builds General only when
            # it is opened, so a kept reference would point at deleted
            # labels (and the previous element) until then.
            self._position_source = None
            self._position_labels = None
        w = scroll.widget()
        if w is None:
            return
        layout = w.layout()
        if layout is None:
            return
        while layout.count() > 1:
            item = layout.takeAt(0)
            widget = req(item).widget()
            if widget is not None:
                # setParent(None) unparents it immediately, so it stops
                # painting at its old geometry right away; deleteLater()
                # alone leaves it a visible (if unmanaged) child until the
                # event loop reaches the deferred delete. Grab the widget
                # into a local first: calling item.widget() a second time
                # after setParent(None) can return None, since an
                # unparented QObject is owned by Python refcounting and a
                # dropped temporary can be GC'd.
                #
                # hide() FIRST. Rebuilding the System Config tab unparented
                # six still-shown group boxes; parentless and not
                # explicitly hidden they were mapped as top-level windows
                # at the screen's corner -- the pile of transient dialogs
                # seen when toggling an analysis on the Run tab.
                widget.hide()
                widget.setParent(None)
                widget.deleteLater()
            elif req(item).layout():
                self._delete_layout(req(item).layout())

    def _delete_layout(self, layout):
        while layout.count():
            item = layout.takeAt(0)
            widget = item.widget()
            if widget is not None:
                widget.hide()
                widget.setParent(None)
                widget.deleteLater()
            elif item.layout():
                self._delete_layout(item.layout())

    def _raw_node_row(self, node_id: int) -> dict | None:
        """The raw NODE_DATA row for a node, if the file section is
        available -- needed because RBDNode does NOT carry every column
        (the K-of-N `n` in particular is unmapped; see typed.py)."""
        if self._rbd is None:
            return None
        section = self._rbd.sections.get("NODE_DATA")
        if section is None:
            return None
        for row in section.rows:
            try:
                if int(float(row.get("ID", "-1"))) == node_id:
                    return row
            except (ValueError, KeyError):
                continue
        return None

    def set_read_only(self, read_only: bool) -> None:
        """Make the property fields read-only while `read_only`, while Results are on display.

        The tab strip itself stays enabled so the user can still switch
        tabs and view the parameters on every tab -- only the input
        widgets inside each page are disabled. The disabled values stay
        visible, and a banner with a padlock icon between the header and
        the tabs explains why."""
        self._read_only = bool(read_only)
        self._sync_lock_banner()
        self._apply_read_only()

    _READ_ONLY_TEXT = "Read-only — editing is locked"
    _RESULTS_NOTICE_TEXT = ("Results are on screen. Clear them to edit, or your first edit will ask "
                            "whether to discard them.")

    def set_results_notice(self, on: bool) -> None:
        """Warn (without disabling anything) that an edit will cost the
        displayed results. Editing is NOT locked while results show; the
        first edit asks, and this line says so before the user gets
        there."""
        self._results_notice = bool(on)
        self._sync_lock_banner()

    def _sync_lock_banner(self) -> None:
        read_only = getattr(self, "_read_only", False)
        notice = getattr(self, "_results_notice", False)
        self._lock_banner.setVisible(read_only or notice)
        self._lock_btn.setVisible(notice and not read_only)
        if read_only:
            from raptor.gui.resources import icons as _icons
            self._lock_text.setText(self._READ_ONLY_TEXT)
            self._lock_icon_lbl.setPixmap(
                _icons.lock_icon(active_colour()["muted"])
                .pixmap(16, 16))
        elif notice:
            self._lock_text.setText(self._RESULTS_NOTICE_TEXT)
            self._lock_icon_lbl.clear()
        # A wrapped label's height depends on its width: make the banner
        # re-measure so a third line is not clipped.
        self._lock_icon_lbl.setVisible(read_only)
        self._lock_text.updateGeometry()
        self._lock_banner.updateGeometry()
        lay = self._lock_banner.layout()
        if lay is not None:
            lay.invalidate()

    _READ_ONLY_WIDGETS = (
        QLineEdit, QSpinBox, QDoubleSpinBox, QComboBox, QCheckBox,
        QPushButton, QRadioButton, QTableWidget,
    )

    def _apply_read_only(self) -> None:
        """(Re)apply the stored read-only flag to every input widget in
        every CURRENTLY POPULATED tab page. Called after each populate
        too (pages are rebuilt per selection), so a locked session
        keeps the fields locked for every newly shown element.

        Widget-level `enabled` state is also how the populate code
        itself gates model-conditional fields (e.g. the spare-cost
        fields on a pool-sparing block) -- so UN-locking must restore
        the recorded PRE-LOCK states instead of blanket re-enabling,
        which would override that gating. Widgets are recorded on
        first lock; pages rebuilt while locked record their own priors
        (populate-time gating included), so the restore is always
        faithful."""
        ro = getattr(self, "_read_only", False)
        prior = getattr(self, "_ro_prior", None)
        if ro:
            if prior is None:
                prior = {}
            for i in range(self._tabs.count()):
                page = self._tabs.widget(i)
                if page is None:
                    continue
                for w in page.findChildren(QWidget):
                    if isinstance(w, self._READ_ONLY_WIDGETS) \
                            and w not in prior:
                        prior[w] = w.isEnabled()
                        w.setEnabled(False)
            self._ro_prior = prior
            return
        if not prior:
            return
        dead = []
        for w, was_enabled in prior.items():
            try:
                w.setEnabled(was_enabled)
            except RuntimeError:
                dead.append(w)   # page rebuilt since: C++ object gone
        for w in dead:
            prior.pop(w, None)
        self._ro_prior = prior if prior else None

    def show_hier(self, hier):
        """Populate the hierarchy tab (name/rename). Only General --
        Raptor 7's own Hierarchy Properties dialog has a Dependency
        tab too, but the engine doesn't simulate hierarchy-level
        dependency at all (see `raptor.core.engine.dependency`'s module
        docstring: "hierarchy dependencies not attempted"), and
        HIERARCHY_DATA has no DepNum/DepType column to persist it in
        even as inert data -- an editable selector here would silently
        do nothing, worse than not offering it."""
        self._current_target = ("hier", hier.id)
        self._begin_populate()
        self._configure_tabs("hier")
        self._header.setText(f"Hierarchy: {hier.name}  (ID {hier.id})")
        self._current_block_id = None
        self._current_node_id = None
        for i in range(self._tabs.count()):
            self._clear_tab(cast(QScrollArea, self._tabs.widget(i)))

        w = self._scroll_general.widget()
        layout = req(w).layout()
        self._clear_tab(self._scroll_general)

        _gb, fl = self._group("Hierarchy", layout)
        self._name_edit(fl, hier.name,
                        lambda new_name, hid=hier.id:
                        self.hier_renamed.emit(hid, new_name))
        self._row(fl, "ID", hier.id)
        self._row(fl, "Parent level", hier.parent_id or "Home")
        self._row(fl, "Level", hier.level)
        self._comment_row(fl, "hier", hier.id, hier.name)
        _gb, fl = self._group("Markers", layout)
        self._row(fl, "In node", hier.in_id)
        self._row(fl, "Out node", hier.out_id)
        self._hier_phasing_group(layout, hier.id, hier.uses_phasing)
        self._restore_tab()

    def _hier_phasing_group(self, layout: QLayout | None, hid: int,
                            phased_now: bool):
        """Raptor 7: a hierarchy can be marked phased too (shown
        hatched on the canvas), but -- unlike block/node/event -- it has
        no per-phase VALUE columns of its own (it's a container, not an
        attribute-bearing element; `tests/data/models/Phasing1.rbd`'s own "500path"
        row is just a bare flag). One checkbox, not the full per-phase
        table `_phasing_group` builds for the other kinds."""
        layout = _vbox(layout)
        _gb, fl = self._group("Phasing", layout)
        cb = QCheckBox("Phased")
        cb.setChecked(phased_now)
        cb.setToolTip(
            "Marks this hierarchy as phased -- shown "
            "hatched on the canvas. A hierarchy has no per-phase values "
            "of its own; this flags that its CONTENTS change between "
            "phases.")
        cb.toggled.connect(
            lambda checked, hid=hid:
                self.phasing_changed.emit("hier", hid, checked, []))
        fl.addRow(cb)

    def show_event(self, event):
        """Event properties across General/Costs/Advanced, matching the
        real Event Properties dialog."""
        self._current_target = ("event", event.id)
        self._begin_populate()
        self._configure_tabs("event")
        from raptor.core.model import RBDEvent  # noqa: F401
        self._header.setText(f"Event: {event.name}  (ID {event.id})")
        self._current_block_id = None
        self._current_node_id = None
        for i in range(self._tabs.count()):
            self._clear_tab(cast(QScrollArea, self._tabs.widget(i)))
        w = self._scroll_general.widget()
        layout = req(w).layout()
        self._clear_tab(self._scroll_general)

        _gb, fl = self._group("Identity", layout)
        self._name_edit(fl, event.name,
                        lambda new_name, eid=event.id:
                        self.event_renamed.emit(eid, new_name))
        self._row(fl, "ID", event.id)
        self._comment_row(fl, "event", event.id, event.name)

        _gb, fl = self._group("Firing", layout)
        param_spin = QDoubleSpinBox()
        param_spin.setRange(0.0, 1.0)
        param_spin.setDecimals(6)
        param_spin.setSingleStep(0.05)
        param_spin.setValue(float(event.fail_mean))
        last = {"v": float(event.fail_mean)}

        def commit_param():
            v = param_spin.value()
            if abs(v - last["v"]) > 1e-12:
                last["v"] = v
                self.event_field_changed.emit(event.id, "param", v)
        param_spin.editingFinished.connect(commit_param)
        fl.addRow("Success probability:", param_spin)

        self._populate_event_costs(event)
        self._populate_event_advanced(event)
        self._restore_tab()

    def _populate_event_costs(self, event):
        """Costs tab (slot 1, reused from the block "Failure" slot) --
        Initial event / Per success / Per failure, matching the real
        Event Properties Costs tab exactly (only 3 fields, no per-time
        rate unlike a block)."""
        w = self._scroll_failure.widget()
        layout = req(w).layout()
        self._clear_tab(self._scroll_failure)
        _gb, fl = self._group("Costs", layout)
        eid = event.id
        fields = (("initial_cost", "Initial event"),
                  ("operating_cost", "Per success"),
                  ("repairing_cost", "Per failure"))
        spins: list[QDoubleSpinBox] = []
        for attr, label in fields:
            sp = QDoubleSpinBox()
            sp.setRange(-1e12, 1e12)
            sp.setDecimals(3)
            sp.setValue(float(getattr(event, attr)))
            spins.append(sp)
            fl.addRow(label + ":", sp)

        def apply_costs():
            self.event_costs_changed.emit(eid, [sp.value() for sp in spins])
        apply_btn = QPushButton("Apply costs")
        apply_btn.clicked.connect(apply_costs)
        fl.addRow(apply_btn)

    def _populate_event_advanced(self, event):
        """Advanced tab (slot 2, reused from the block "Repair" slot) --
        random-number stream + phasing, matching the real Event
        Properties Advanced tab."""
        w = self._scroll_repair.widget()
        layout = req(w).layout()
        self._clear_tab(self._scroll_repair)
        _gb, fl = self._group("Random Stream", layout)
        self._event_stream_row(fl, event.fail_stream, event.id)

        entries = list(zip(event.phase_type, event.phase_value))
        self._phasing_group(layout, "event", event.id,
                            event.uses_phasing, entries)

    def _event_stream_row(self, form: QFormLayout, cur: int, eid: int):
        """Single-sided sibling of `_stream_editor_row` -- an event has
        only ONE stream (`fail_stream`), not a separate failure/repair
        pair, so it needs its own (simpler) widget rather than sharing
        that one directly."""
        from raptor.core.rng import FETCH_RAPTOR_SEED
        max_ind = len(FETCH_RAPTOR_SEED) - 1

        c = QWidget()
        v = QVBoxLayout(c)
        v.setContentsMargins(0, 0, 0, 0)
        v.setSpacing(4)
        row1 = QHBoxLayout(); row1.setContentsMargins(0, 0, 0, 0)
        row1.setSpacing(6)
        row1.addWidget(QLabel("Mode"))
        mode = QComboBox()
        mode.setMinimumWidth(120)
        mode.addItem("System", "sys")
        mode.addItem("Independent", "ind")
        row1.addWidget(mode); row1.addStretch(1)
        v.addLayout(row1)

        row2 = QHBoxLayout(); row2.setContentsMargins(0, 0, 0, 0)
        row2.setSpacing(6)
        row2.addWidget(QLabel("Stream"))
        letter = QComboBox()
        letter.setMinimumWidth(64)
        for i in range(10):
            letter.addItem(chr(ord("A") + i), 201 + i)
        row2.addWidget(letter)
        spin = QSpinBox()
        spin.setRange(1, max_ind)
        spin.setSuffix(f" of {max_ind}")
        spin.setMinimumWidth(96)
        row2.addWidget(spin)
        anti = QCheckBox("Anti")
        row2.addWidget(anti)
        row2.addStretch(1)
        v.addLayout(row2)

        def sync_visibility() -> None:
            """Show/hide the letter vs spin+anti controls for whichever
            mode is currently selected -- reads `mode.currentData()`, not
            `cur` (the event's persisted value at population time).
            Deliberately separate from `populate` below: re-deriving from
            `cur` on every mode change would snap the combo back to the
            mode `cur` is in, making "Independent" unselectable for a
            System-stream event."""
            is_sys = mode.currentData() == "sys"
            letter.setVisible(is_sys)
            spin.setVisible(not is_sys)
            anti.setVisible(not is_sys)

        def populate() -> None:
            """Initial widget setup FROM the event's own persisted
            stream value -- call once, not on every mode change (see
            `sync_visibility`'s own docstring)."""
            if 201 <= cur <= 210:
                mode.setCurrentIndex(0)
                li = letter.findData(cur)
                letter.setCurrentIndex(max(li, 0))
            else:
                mode.setCurrentIndex(1)
                spin.setValue(min(max(abs(cur), 1), max_ind))
                anti.setChecked(cur < 0)
            sync_visibility()

        def send() -> None:
            if mode.currentData() == "sys":
                self.event_field_changed.emit(eid, "stream",
                                              float(letter.currentData()))
            else:
                v2 = spin.value() * (-1 if anti.isChecked() else 1)
                self.event_field_changed.emit(eid, "stream", float(v2))

        populate()
        def _on_event_stream_mode_changed(_i):
            sync_visibility()
            send()
        mode.activated.connect(_on_event_stream_mode_changed)
        letter.activated.connect(
            lambda _i: send() if mode.currentData() == "sys" else None)
        spin.valueChanged.connect(
            lambda _v: send() if mode.currentData() == "ind" else None)
        anti.toggled.connect(
            lambda _on: send() if mode.currentData() == "ind" else None)
        form.addRow("Stream:", c)


    #: The view the anchor controls read their choices from (its
    #: `annotation_entry` and `anchor_targets`); set by the main window.
    anchor_source: Any = None

    _ANCHOR_LOCATIONS = tuple(
        (key, _ANCHOR_LOCATION_LABELS[key]) for key in ANNO_LOCATIONS)

    def _populate_system_config(self, cfg: SystemConfig):
        self._configure_tabs("system")
        self._header.setText("Project Settings")
        self._current_block_id = None
        self._current_node_id = None
        for i in range(self._tabs.count()):
            self._clear_tab(cast(QScrollArea, self._tabs.widget(i)))

        w = self._scroll_general.widget()
        layout = req(w).layout()
        self._clear_tab(self._scroll_general)

        _gb, fl = self._group("Simulation Settings", layout)
        term_words = {"TimeSim": "Time simulation (run length)",
                      "FailSim": "Failure simulation (N system failures)",
                      "CycleSim": "Phase cycles (N phase cycles)"}
        self._row(fl, "Termination",
                  term_words.get(cfg.termination_type,
                                 cfg.termination_type))
        self._row(fl, "Term condition", f"{cfg.term_condition:.3f}")
        self._row(fl, "Start stats", f"{cfg.start_stats:.3f}")
        self._row(fl, "Num runs", cfg.num_runs,
                  "Change trials in the Run panel")
        # Drop-down AND free text, as Raptor 7's own units box was: the
        # common units are one click, and anything else ("cycles", "flight
        # hours") is typed. The vocabulary is the conversion table's, so a
        # picked unit is one the display-unit picker can convert to.
        units_edit = QComboBox()
        units_edit.setEditable(True)
        # Raptor 7's own `UnitsCombo` item list, verbatim
        # (`UnitsCombo`): a drop-down that
        # also accepts typing. Its order and wording are kept so a file
        # written there reads the same here.
        units_edit.addItems(RAPTOR7_UNITS)
        if cfg.units and units_edit.findText(cfg.units) < 0:
            units_edit.addItem(cfg.units)      # keep an exotic unit visible
        units_edit.setCurrentText(cfg.units)
        units_edit.setAccessibleName("Time units")
        units_edit.setToolTip(
            "Time-unit label for this model: pick one of the common units "
            "or type your own (hours, years, cycles…). It relabels values; "
            "it does not convert stored numbers.")

        def commit_units(_index=None):
            text = units_edit.currentText().strip()
            if text and text != cfg.units:
                self.system_config_changed.emit({"units": text})

        # `activated` is the pick from the list; the line edit's own
        # `editingFinished` is a typed value. Both go through one commit,
        # which is a no-op when nothing changed, so the two cannot fight.
        units_edit.activated.connect(commit_units)
        units_line = units_edit.lineEdit()
        if units_line is not None:
            units_line.editingFinished.connect(commit_units)
        fl.addRow("Units:", units_edit)

        # No Display or Analysis group here. Zoom belongs to View ▸ Zoom,
        # the animation speed to Simulate ▸ Animation, and the analysis
        # flags to the Run panel's Analysis section -- the Inspector is a
        # read-only echo of the model, and an echo of a control that lives
        # in a ribbon is just a second place to look (owner decision:
        # "these should be in the appropriate ribbons, not here").

        _gb, fl = self._group("Random Streams", layout)
        # Raptor 7's System Streams tab: a stream number per letter, with
        # an antithetic checkbox stored as a NEGATIVE number. This replaces
        # the old modal's only editor for these values.
        if cfg.sys_streams:
            labels = ["A", "B", "C", "D", "E", "F", "G", "H", "I", "J",
                      "Anc"]
            stream_spins: list[QSpinBox] = []
            stream_anti: list[QCheckBox] = []
            for i, raw in enumerate(cfg.sys_streams):
                label = labels[i] if i < len(labels) else f"S{i}"
                try:
                    raw_i = int(raw)
                except (TypeError, ValueError):
                    raw_i = 0
                sp = QSpinBox()
                sp.setRange(0, 10_000)
                sp.setValue(abs(raw_i))
                anti = QCheckBox("antithetic")
                sp.setAccessibleName(f"System stream {label}")
                anti.setAccessibleName(f"Stream {label} antithetic")
                anti.setChecked(raw_i < 0)
                anti.setToolTip(
                    "Antithetic variates for this stream (paired draws); "
                    "stored as a negative stream number.")
                stream_spins.append(sp)
                stream_anti.append(anti)
                row = QWidget()
                rl = QHBoxLayout(row)
                rl.setContentsMargins(0, 0, 0, 0)
                rl.addWidget(sp, 1)
                rl.addWidget(anti)
                fl.addRow(f"{label}:", row)

                def commit_streams(_=None, spins=stream_spins,
                                   antis=stream_anti) -> None:
                    text = " ".join(
                        str(-sp.value() if cb.isChecked() else sp.value())
                        for sp, cb in zip(spins, antis))
                    self.system_config_changed.emit(
                        {"SysStreamsArray": text})
                for widget in (sp, anti):
                    (widget.editingFinished if isinstance(widget, QSpinBox)
                     else widget.toggled).connect(commit_streams)

        _gb, fl = self._group("Comment", layout)
        comment_edit = QLineEdit(self._system_comment())
        comment_edit.setAccessibleName("System comment")
        comment_edit.setToolTip(
            "System comment stored in the model's COMMENTS section")
        comment_edit.editingFinished.connect(
            lambda: self.system_comment_changed.emit(
                comment_edit.text().strip()))
        fl.addRow("System comment:", comment_edit)

        _gb, fl = self._group("Flags", layout)
        startfail_cb = QCheckBox("Ignore start failures")
        startfail_cb.setAccessibleName("Ignore start failures")
        startfail_cb.setChecked(bool(cfg.ignore_start_fail))
        startfail_cb.toggled.connect(
            lambda on: self.system_config_changed.emit(
                {"IgnoreStartFail": "1" if on else "0"}))
        fl.addRow(startfail_cb)
        neg_cb = QCheckBox("Ignore negative-value warning")
        neg_cb.setAccessibleName("Ignore negative-value warning")
        neg_cb.setChecked(bool(cfg.show_neg_err_msg))
        neg_cb.toggled.connect(
            lambda on: self.system_config_changed.emit(
                {"ShowNegErrMsg": "1" if on else "0"}))
        fl.addRow(neg_cb)

        self._restore_tab()

    def _refresh_theme(self, _dark: bool | None = None):
        c = active_colour()
        # Scoped (InspectorDock {...}) on purpose: an unscoped background
        # declaration applies to this widget and its logical descendants
        # in Qt's stylesheet engine, so the tooltip window shown for any
        # descendant would pick the background up and theme
        # inconsistently. The dock's own background still applies;
        # children keep the themed backgrounds from the global stylesheet
        # or their own rules.
        self.setStyleSheet(f"InspectorDock {{ background: {c['surface']}; }}")
        # Keep the read-only banner on-theme (its icon is repainted from
        # the active tokens; locked state persists across re-themes).
        if self._lock_banner.isVisible():
            from raptor.gui.resources import icons as _icons
            self._lock_icon_lbl.setPixmap(
                _icons.lock_icon(c["muted"]).pixmap(16, 16))
        for i in range(self._tabs.count()):
            sw = self._tabs.widget(i)
            if isinstance(sw, QScrollArea):
                sw.setStyleSheet(f"QScrollArea {{ border: none; background: {c['surface']}; }}")
                cw = sw.widget()
                if cw:
                    cw.setStyleSheet(f"#InspectorContent {{ background: {c['surface']}; }}")
