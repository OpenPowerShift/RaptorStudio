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

from PySide6.QtWidgets import QAbstractItemView
import os
from typing import Any, cast
from collections.abc import Callable
from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QDoubleValidator, QIntValidator, QUndoCommand
from PySide6.QtWidgets import (
    QWidget, QLayout, QVBoxLayout, QScrollArea, QLabel, QGroupBox, QFormLayout, QHBoxLayout, QTabWidget, QLineEdit,
    QCheckBox, QSpinBox, QDoubleSpinBox, QComboBox, QPushButton,
    QRadioButton, QButtonGroup, QTableWidget, QTableWidgetItem, QFrame,
    QToolButton, QSizePolicy,
)
from raptor.core.engine.diststats import compute_stats
from raptor.core.annotations import ANNO_DEFAULT_LOCATION, ANNO_LOCATIONS

from raptor.core.fileio.rbd import RbdFile
from raptor.core.fileio.typed import system_config_from_row
# The distribution rules are `raptor.core.distro_rules`'s, not this
# module's (review D-04) -- they are pure model rules and belong in a
# Qt-free home. Imported here because this widget still uses both.
from raptor.core.distro_rules import (
    DISTRO_PARAM_LABELS, validate_distro_params, param_error)
from raptor.core.fileio.typed import row_int
from raptor.core.model import RBDBlock, RBDNode, SystemConfig, SparingType
from raptor.gui.resources.design_tokens import (
    ThemeManager, SPACING, active_colour,
)
from raptor.gui.ui import param_entry

#: Raptor 7's `UnitsCombo` items, in its own order
#: The box was
#: editable, so these are presets rather than a closed list: Raptor 7
#: reads `unitsBox.Text()` and every label is built from it.
RAPTOR7_UNITS = (
    "seconds", "minutes", "hours", "kilo-hrs", "mega-hrs", "giga-hrs",
    "tera-hrs", "days", "months", "years", "cycles", "units", "miles",
    "kilometers", "PPM", "PPB",
)



_SIM_START = {
    # Port of Raptor 7's Input-Tables display code plus
    # the engine semantics in Raptor 7's `CASE simStartType`
    # 1 = completely random state; 2/3 start Up (random life
    # vs a fixed amount exhausted); 4/5/6 start Down (random point in the
    # repair cycle vs % complete vs repair just starting).
    1: "Random",
    2: "Start Up — Random life exhausted",
    3: "Start Up — Some life exhausted",
    4: "Start Failed — Random point in repair",
    5: "Start Failed — % repair complete",
    6: "Start Failed — Repair at start",
}


def _i_raw(row: dict, key: str, default: int = 0) -> int:
    """int-coerce a raw `.rbd` row cell, for columns RBDNode/RBDBlock
    don't carry.

    DELEGATED to `core.fileio.typed.row_int` (review E-12). This was a
    sixth copy of the same coercion -- not in the review's list of five --
    and its own docstring said *"same shape as typed.py's `_i`"* without
    noting that `typed._i` was importable. That is the setup for one copy
    gaining a fix the other misses.

    Note the guard differs slightly from the old code: this used to skip
    the float conversion only for `None`/`""` and fall back to `default`
    for anything else unparseable, which is what `row_int` does.
    """
    return row_int(row.get(key), default)


def _row_float(v) -> float:
    """float-coerce a raw `.rbd` row cell, never raising (blank -> 0)."""
    try:
        return float(v)
    except (TypeError, ValueError):
        return 0.0


def _stream_label(v: int) -> str:
    """Random-stream display: streams 201..210 are the named system
    streams A..J everywhere in the legacy UI;
    anything else shows as its literal number."""
    if 201 <= v <= 210:
        return chr(ord("A") + v - 201)
    return str(v)




def parse_flow_cell(text: str, label: str,
                    n_in: int) -> tuple[int | None, str | None]:
    """Parse one Capacity-tab flow cell into the stored value.

    `label` is the sentinel keyword belonging to THIS column -- "MaxFlow"
    on Nominal Flow, "FullFlow" on Max Flow -- and the two map to the same
    stored sentinels (-1 for "all generated flow", -2 for "an even share
    across the node's n incoming paths"). The other column's keyword used
    to be accepted here as well, so typing it re-rendered as this column's
    keyword and looked as though the editor had picked the other one.

    Returns ``(value, error)``: a value of -2, -1 or >= 0, or None with a
    message to show when the text is not a flow at all (or is the wrong
    column's keyword).
    """
    raw = (text or "").strip()
    key = raw.upper().replace(" ", "")
    own, other = (("MAXFLOW", "FULLFLOW") if label == "MaxFlow"
                  else ("FULLFLOW", "MAXFLOW"))
    if key == own:
        return -1, None
    if key == f"{own}/{n_in}":
        return -2, None
    if key.startswith(own + "/"):
        return None, (f"write {label}/{n_in} for an even share, or a number")
    if key.startswith(other):
        return None, (f"{label} takes {label} or {label}/{n_in}; "
                      f"{other.title()} belongs to the other column")
    try:
        value = int(float(raw))
    except (TypeError, ValueError):
        return None, (f"enter a flow (0 or more), {label}, or "
                      f"{label}/{n_in}")
    if value < 0 and value not in (-1, -2):
        return None, (f"enter a flow (0 or more), {label}, or "
                      f"{label}/{n_in}")
    return value, None


class _WordWrapCheckBox(QWidget):
    """A checkbox whose label word-wraps (QCheckBox has no native
    wrap, and its unwrapped minimum width forces a narrow dock into a
    horizontal scrollbar). Indicator + wrapping QLabel side by side;
    clicking the label toggles like clicking the checkbox text would,
    so the interaction is unchanged."""

    toggled = Signal(bool)

    def __init__(self, text: str, parent=None):
        super().__init__(parent)
        self._cb = QCheckBox()
        # The box has no text of its own (the wrapping label carries it),
        # so without this a screen reader announces an unlabelled checkbox.
        self._cb.setAccessibleName(text)
        self._lbl = QLabel(text)
        self._lbl.setWordWrap(True)
        self._lbl.setTextInteractionFlags(
            Qt.TextInteractionFlag.LinksAccessibleByMouse)
        lay = QHBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(6)
        lay.addWidget(self._cb, 0, Qt.AlignmentFlag.AlignTop)
        lay.addWidget(self._lbl, 1)
        self._cb.toggled.connect(self.toggled)
        self._cb.toggled.connect(
            lambda _on: self._lbl.setStyleSheet(""))
        # clicking the label toggles (QLabel eats clicks otherwise)
        cast(Any, self._lbl).mousePressEvent = self._label_clicked

    def _label_clicked(self, _event) -> None:
        self._cb.toggle()

    def isChecked(self) -> bool:
        return self._cb.isChecked()

    def setChecked(self, on: bool) -> None:
        self._cb.setChecked(on)

    def setToolTip(self, tip: str) -> None:
        super().setToolTip(tip)
        self._cb.setToolTip(tip)
        self._lbl.setToolTip(tip)


class _FormulaCmd(QUndoCommand):
    """Undoable change to one field's stored formula/rate RECORD
    (`RbdFile.formulas[addr]`). The numeric VALUE is restored by the
    editor's own value command; without this, Ctrl+Z restored the number
    but left the (now-lying) basis record behind."""

    def __init__(self, rbd, addr: str, old, new):
        super().__init__("Edit formula basis")
        self._rbd, self.addr = rbd, addr
        self.old, self.new = old, new

    def _apply(self, record):
        if record is None:
            self._rbd.formulas.pop(self.addr, None)
        else:
            self._rbd.formulas[self.addr] = dict(record)

    def redo(self):
        self._apply(self.new)

    def undo(self):
        self._apply(self.old)


#: Annotation kinds as the user sees them, matching the Annotate
#: ribbon's tool buttons rather than the .rbds `kind` strings.
_ANNO_KIND_LABELS = {
    "rect": "Rectangle", "ellipse": "Ellipse",
    "text": "Text", "arrow": "Arrow",
}

#: How the Inspector spells each stored anchor location. The stored values
#: live in `raptor.core.annotations.ANNO_LOCATIONS`; this is only display.
_ANCHOR_LOCATION_LABELS = {
    "right": "Right", "left": "Left", "above": "Above",
    "below": "Below", "center": "Centre",
}


def _vbox(layout: QLayout | None) -> QVBoxLayout:
    """The page layouts the inspector tabs build are all QVBoxLayouts; the stubs return `QLayout | None`."""
    return cast(QVBoxLayout, req(layout))


class InspectorDock(QWidget):
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

    def _begin_populate(self):
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

    def _comment_for(self, etype: str, name: str) -> str:
        """Legacy COMMENTS section entry for this element ('~' separates
        lines, e.g. 'Part # 178830-SP5~Failure data from Skylab')."""
        if self._rbd is None:
            return ""
        sec = self._rbd.sections.get("COMMENTS")
        if sec is None:
            return ""
        for row in sec.rows:
            if row.get("Type", "").lower() == etype.lower() \
                    and row.get("Name", "") == name:
                return (row.get("Comment", "") or "").replace("~", "\n")
        return ""

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

    def _group(self, title: str, layout: QLayout | None) -> tuple[QGroupBox, QFormLayout]:
        layout = _vbox(layout)
        gb = QGroupBox(title)
        gb.setFlat(False)
        fl = QFormLayout(gb)
        fl.setLabelAlignment(Qt.AlignmentFlag.AlignLeft)
        fl.setFormAlignment(Qt.AlignmentFlag.AlignLeft)
        fl.setFieldGrowthPolicy(QFormLayout.FieldGrowthPolicy.AllNonFixedFieldsGrow)
        fl.setHorizontalSpacing(SPACING["md"])
        fl.setVerticalSpacing(2)
        layout.insertWidget(layout.count() - 1, gb)
        return gb, fl

    def _distro_type_row(self, form: QFormLayout, distro: int,
                         is_failure: bool, bid: int, signal) -> None:
        """Editable Type combo (legacy Failure/Repair dialogs offer the
        full distribution list; 's own ConvertToString set
        is DISTRO_NAMES). `activated` fires on USER choice only, so
        re-populating the tab never echoes back."""
        # DELEGATED (review E-14): the code-18 relabel is owned by
        # `distro_combo.distro_display_name`, which decides it once from
        # `is_failure`. This site was the conditional copy; two dialogs
        # applied it unconditionally and data_tables not at all.
        from raptor.gui.ui.distro_combo import fill_distro_combo
        combo = fill_distro_combo(QComboBox(), distro,
                                  is_failure=is_failure)
        ci = combo.findData(distro)
        if ci < 0:
            combo.setToolTip(f"Unknown distribution code #{distro} — "
                             "pick one to fix it")
        combo.activated.connect(
            lambda _i, b=bid, cmb=combo: signal.emit(b, cmb.currentData()))
        form.addRow("Type:", combo)

    def _stream_editor_row(self, form: QFormLayout, cur: int,
                           which: str, bid: int) -> None:
        """Legacy Simulation Theory stream editor ( + ): System radio with a shared A..J combo
        vs an independent stream number with a per-side antithetic
        check (stored NEGATIVE). Picking a SYSTEM letter sets BOTH
        failure and repair streams -- intsArray[11] AND [12] come from
        one sysCombo. Widgets bound to valid ranges = validation by
        construction; the receiver re-checks anyway."""
        from raptor.core.rng import FETCH_RAPTOR_SEED
        max_ind = len(FETCH_RAPTOR_SEED) - 1

        c = QWidget()
        v = QVBoxLayout(c)
        v.setContentsMargins(0, 0, 0, 0)
        v.setSpacing(4)
        # Mode and Stream on separate rows.
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
        anti.setToolTip("Antithetic draws (stored as a negative "
                        "stream number)")
        row2.addWidget(anti)
        row2.addStretch(1)
        v.addLayout(row2)

        def populate() -> None:
            if 201 <= cur <= 210:
                mode.setCurrentIndex(0)
                li = letter.findData(cur)
                letter.setCurrentIndex(max(li, 0))
                letter.setVisible(True)
                spin.setVisible(False)
                anti.setVisible(False)
            else:
                mode.setCurrentIndex(1)
                spin.setValue(min(max(abs(cur), 1), max_ind))
                anti.setChecked(cur < 0)
                letter.setVisible(False)
                spin.setVisible(True)
                anti.setVisible(True)

        def send() -> None:
            if mode.currentData() == "sys":
                code = letter.currentData()
                self.block_fail_stream_changed.emit(bid, code)
                self.block_repair_stream_changed.emit(bid, code)
            else:
                v = spin.value() * (-1 if anti.isChecked() else 1)
                sig = (self.block_fail_stream_changed if which == "F"
                       else self.block_repair_stream_changed)
                sig.emit(bid, v)

        populate()
        def _on_block_stream_mode_changed(_i):
            populate()
            send()
        mode.activated.connect(_on_block_stream_mode_changed)
        letter.activated.connect(
            lambda _i: send() if mode.currentData() == "sys" else None)
        spin.valueChanged.connect(
            lambda _v: send() if mode.currentData() == "ind" else None)
        anti.toggled.connect(
            lambda _on: send() if mode.currentData() == "ind" else None)
        c.setToolTip(
            f"Random number stream. System A..J are shared streams; "
            f"independent streams draw from seeds 1..{max_ind}.")
        form.addRow("Stream:", c)

    @staticmethod
    def _row(form: QFormLayout, label: str, value, tooltip: str = ""):
        text = f"{value:.4g}" if isinstance(value, float) else str(value)
        vlabel = QLabel(text)
        vlabel.setWordWrap(True)
        vlabel.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        form.addRow(label + ":", vlabel)
        if tooltip:
            vlabel.setToolTip(tooltip)
            key_label = form.labelForField(vlabel)
            if key_label is not None:
                key_label.setToolTip(tooltip)
        return vlabel

    def _distribution_params_row(self, form: QFormLayout, distro: int, use: str, values: list[float], on_commit,
                    addr_prefix: str | None = None,
                    lambda_mode: bool | None = None):
        """A distribution's Param1/2/3 row, labelled the way the legacy
        Block Properties dialog labels it (GetParameters):
        one named field per parameter the distribution actually has
        (Weibull -> Shape/Scale/Location, Exponential -> Mean/Location,
        Fixed -> Constant, ...), with hidden slots shown as nothing at
        all (e.g. Empirical/None have no parameter boxes).

        `on_commit(new_values)` fires with the full current triple when
        any one field loses focus/Enter with a changed, valid value, so
        the receiver gets a complete row rather than tracking partial
        edits. Invalid text or a value outside the distribution's domain
        (the CheckValidInput rules -- negative Mean, Triangular
        Min<Mode<Max violated, ...) reverts that one field to its
        last-known-good value; `validation_failed` carries the reason.

        `addr_prefix` (e.g. "block:5:F") turns each field into a
        formula/rate field: typed text can be a plain number, a
        restricted arithmetic expression (optionally with a trailing
        "# basis" comment), or "@RateName" linking to the Rates library --
        see `expr_eval.py`. The original text is stored in
        `RbdFile.formulas[f"{addr_prefix}Param{i+1}"]` and shown again
        next time this field populates, so the basis isn't lost; a plain
        number clears any formula record for that slot. `None` (blocks
        called without a stable element id, e.g. inside a still-unsaved
        template row) falls back to plain-float-only editing."""
        labels_raw = DISTRO_PARAM_LABELS.get(distro, ("", "", ""))
        entry_labels = param_entry.labels(distro, lambda_mode=lambda_mode)
        # Show the model's time units on dimension-full labels: Shape/
        # N Trials/Probability stay bare; Mean/Scale/Location/Min/Max/
        # Mode/Std Dev carry e.g. '(hours)'. The semantic name comes
        # first and the unit sits in brackets, so Weibull reads Shape /
        # Scale / Location top-to-bottom. In lambda/mu-sigma entry modes
        # the unit is the entry space's: per hours, or ln(hours).
        native_units = getattr(self, "_units_str", "") or ""
        display_unit = getattr(self, "_display_unit", None)
        shown_units = display_unit or native_units
        is_time = [bool(lab) and lab not in
                  ("Shape", "Shape2", "N Trials", "Probability")
                  for lab in labels_raw]
        labels = []
        for j, lab in enumerate(entry_labels):
            if not lab:
                labels.append("")
                continue
            unit = param_entry.unit_for(distro, j, shown_units,
                                        lambda_mode=lambda_mode)
            labels.append(f"{lab} ({unit})" if unit and is_time[j] else lab)
        visible = [i for i, lab in enumerate(labels) if lab]
        # Empirical (16) has no per-param labels at all
        # (`DISTRO_PARAM_LABELS[16] == ("", "", "")` -- it's edited as
        # a whole data-point list, not 3 named slots), so `visible` is
        # empty for it. Let it fall through to the "Enter data…" button
        # built below: the per-param loop is a no-op on an empty
        # `visible`, so this only skips the label row, not the button.
        if not visible and distro != 16:
            form.addRow(use + " params:", QLabel("(no parameters)"))
            return
        # Three slots whatever arrives: a list left short by a distribution
        # change (an Empirical's points) crashed every redraw of the block.
        current = (list(values) + [0.0, 0.0, 0.0])[:max(3, len(values))]
        from raptor.gui.ui.expr_eval import (
            resolve, is_plain_number, is_bare_rate_ref, ExpressionError)
        from raptor.gui.ui.units_conv import native_to_display, display_to_native

        def addr_of(idx: int) -> str | None:
            return f"{addr_prefix}Param{idx + 1}" if addr_prefix else None

        def shown_canonical() -> list[float]:
            """Canonical values in the shown unit, before the entry-mode transform."""
            return [native_to_display(v, native_units, display_unit)
                    if is_time[j] else v for j, v in enumerate(current)]

        shown_current = shown_canonical()
        entry_current = param_entry.to_entry(distro, shown_current,
                                             lambda_mode=lambda_mode)

        def display_text(idx: int) -> str:
            addr = addr_of(idx)
            formula = (self._rbd.formulas.get(addr)
                      if (addr and self._rbd is not None) else None)
            if formula:
                if "rate" in formula:
                    return f"@{formula['rate']}"
                if "expr" in formula:
                    return formula["expr"]
            return f"{entry_current[idx]:.6g}"

        current_text = [display_text(i) for i in visible]
        # Fields holding a typed value that breaks only a rule tying the
        # fields together (Triangular Min < Mode < Max): {idx: (value,
        # pos, edit)}. Written with the edit that makes the set valid, so
        # Triangular 5, 10, 15 can become 20, 30, 40 field by field; each
        # field used to be put back as soon as it broke the ordering.
        pending: dict[int, tuple] = {}
        # One parameter per row (not packed side-by-side): the Inspector
        # sidebar is narrow, and a 3-parameter distribution (e.g.
        # Weibull's Shape/Scale/Location) squeezed into one horizontal
        # row would clip the later fields off-screen.
        for pos, i in enumerate(visible):
            edit = QLineEdit(current_text[pos])
            edit.setToolTip(labels[i])
            # A rate-capable field is wrapped in a container (edit + rate
            # button), so the form's label becomes the CONTAINER's buddy,
            # not the edit's, and a screen reader announced an unnamed box.
            edit.setAccessibleName(f"{use} {labels[i]}")
            if addr_prefix is None:
                edit.setValidator(QDoubleValidator())
            else:
                from raptor.gui.ui.rates_dialog import attach_rate_autocomplete
                attach_rate_autocomplete(
                    edit, lambda: [r.get("name", "") for r in
                                  (getattr(self._rbd, "rates", None) or [])])

            def commit(idx=i, pos=pos, edit=edit):
                nonlocal shown_current, entry_current
                text = edit.text().strip()
                addr = addr_of(idx)
                rates = getattr(self._rbd, "rates", None) if self._rbd else None
                is_formula = addr_prefix is not None and \
                    not is_plain_number(text)
                try:
                    if is_formula:
                        new_v = resolve(text, rates)   # native, as typed
                        new_pair = None
                    else:
                        raw = float(text)
                        if param_entry.is_rate_field(
                                distro, idx, lambda_mode=lambda_mode) \
                                and raw <= 0.0:
                            edit.setText(current_text[pos])
                            self.validation_failed.emit(
                                f"{use} {labels[idx].split(' (')[0]} "
                                "must be greater than 0")
                            return
                        entered = list(entry_current)
                        entered[idx] = raw
                        converted = param_entry.from_entry(
                            distro, entered, old=shown_current,
                            lambda_mode=lambda_mode)
                        if param_entry.is_mu_sigma(distro):
                            new_pair = [
                                display_to_native(converted[j], native_units,
                                                  display_unit)
                                if is_time[j] else converted[j]
                                for j in range(2)]
                            new_v = new_pair[idx]
                        else:
                            new_pair = None
                            new_v = display_to_native(converted[idx],
                                                      native_units,
                                                      display_unit) \
                                if is_time[idx] else converted[idx]
                except (ExpressionError, ValueError) as e:
                    edit.setText(current_text[pos])
                    self.validation_failed.emit(
                        str(e) if is_formula else "not a number")
                    return
                if new_v == current[idx] and text == current_text[pos]:
                    return
                own = param_error(distro, idx, new_v, use)
                if own is not None:            # wrong whatever the others say
                    edit.setText(current_text[pos])
                    self.validation_failed.emit(own)
                    return
                trial = list(current)
                for j, (pv, _p, _e) in pending.items():
                    trial[j] = pv
                if new_pair is not None:
                    trial[:2] = new_pair
                else:
                    trial[idx] = new_v
                err = validate_distro_params(distro, trial, use)
                if err is not None:
                    if is_formula:             # only a plain number is held
                        edit.setText(current_text[pos])
                        self.validation_failed.emit(err)
                        return
                    pending[idx] = (new_v, pos, edit)
                    self.validation_failed.emit(
                        f"{err}. Not applied until the other fields make "
                        f"it valid.")
                    return
                held = {j: h for j, h in pending.items() if j != idx}
                pending.clear()
                for j, (pv, ppos, pedit) in held.items():
                    current[j] = pv
                    shown_current = shown_canonical()
                    entry_current = param_entry.to_entry(distro,
                                                         shown_current,
                                                         lambda_mode=lambda_mode)
                    # A number typed over a rate link unlinks it, as a
                    # plain commit does (below).
                    paddr = addr_of(j)
                    if paddr and self._rbd is not None and \
                            paddr in self._rbd.formulas:
                        old_f = self._rbd.formulas.pop(paddr)
                        if self._stack is not None:
                            self._stack.push(_FormulaCmd(
                                self._rbd, paddr, old_f, None))
                    current_text[ppos] = display_text(j)
                    pedit.setText(current_text[ppos])
                if new_pair is not None:
                    current[:2] = new_pair
                else:
                    current[idx] = new_v
                shown_current = shown_canonical()
                entry_current = param_entry.to_entry(distro, shown_current,
                                                     lambda_mode=lambda_mode)
                if addr and self._rbd is not None:
                    old_formula = self._rbd.formulas.get(addr)
                    if is_formula:
                        bare_rate = is_bare_rate_ref(text)
                        if bare_rate is not None:
                            self._rbd.formulas[addr] = {
                                "rate": bare_rate, "value": new_v}
                        else:
                            # Also covers an embedded "@Rate * 1.4" style
                            # reference -- stored as a plain expr so
                            # `display_text` redisplays it verbatim, same
                            # as any other formula.
                            self._rbd.formulas[addr] = {
                                "expr": text, "value": new_v}
                    else:
                        self._rbd.formulas.pop(addr, None)
                        text = display_text(idx)
                    new_formula = self._rbd.formulas.get(addr)
                    # Wrap the basis-record change in its own undo command
                    # (the value command that follows doesn't carry it).
                    if old_formula != new_formula and self._stack is not None:
                        self._stack.push(_FormulaCmd(
                            self._rbd, addr, old_formula, new_formula))
                else:
                    text = display_text(idx)
                current_text[pos] = text
                edit.setText(text)
                on_commit(list(current))

            edit.editingFinished.connect(commit)
            row_widget: QWidget = edit
            if addr_prefix is not None:
                row_widget = QWidget()
                hl = QHBoxLayout(row_widget)
                hl.setContentsMargins(0, 0, 0, 0)
                hl.setSpacing(4)
                hl.addWidget(edit, 1)
                rate_btn = QPushButton("📎")
                rate_btn.setFixedWidth(28)
                rate_btn.setAccessibleName(
                    f"Use a saved rate or formula for {use} {labels[i]}")
                rate_btn.setToolTip(
                    "Use a saved rate, or type a formula (e.g. "
                    "\"0.00015 * 20000 / 8760  # 0.00015 fail/km, "
                    "20000 km/yr\")")
                rate_btn.clicked.connect(
                    lambda _c=False, e=edit, c=commit:
                    self._pick_rate_into(e, c))
                hl.addWidget(rate_btn)
            form.addRow(f"{use} {labels[i]}:", row_widget)

        # --- Update Statistics (legacy parity):
        # mean/stdev of this distribution's committed params, shown as
        # read-only labels and refreshed on every commit. For Empirical
        # (16), an 'Enter data…' button opens the data-entry dialog.
        stats_labels: dict[str, Any] = {}
        u_stats = f" ({shown_units})" if shown_units else ""
        if distro == 16:
            enter_b = QPushButton(f"Enter data… ({use})")
            from raptor.gui.ui.empirical_dialog import open_empirical_dialog
            def open_entry():
                # full table lives on the block (emp_data key) if a
                # previous dialog pass stored it; otherwise seed from the
                # block's own points (all of them: the .rbd keeps an
                # empirical failure or repair law's points on a
                # continuation line)
                prior = getattr(self, "_emp_data", {}).get(id(enter_b))
                vals = prior if prior is not None else \
                    [v for v in current if v]
                shown_vals = [native_to_display(v, native_units, display_unit)
                             for v in vals]
                out = open_empirical_dialog(self, shown_vals,
                                            u_stats.strip(" ()"))
                if out is not None:
                    native_out = [display_to_native(v, native_units,
                                                    display_unit)
                                 for v in out]
                    on_commit(list(native_out))   # engine interpolates N points
                    _refresh()
            enter_b.clicked.connect(open_entry)
            stats_labels["__btn"] = enter_b
        mean_lbl = QLabel("—"); sd_lbl = QLabel("—")
        stats_labels["Mean"] = mean_lbl; stats_labels["Std Dev"] = sd_lbl
        if distro == 16:
            form.addRow("", enter_b)
        form.addRow(f"Mean{u_stats}:", mean_lbl)
        form.addRow(f"Std Dev{u_stats}:", sd_lbl)
        def _refresh():
            # `compute_stats` returns a bare None (not a 2-tuple) for an
            # invalid/insufficient parameterization (its own docstring:
            # "caller shows '-'") -- e.g. Beta with a 2nd shape param of
            # 0 -- so guard the unpack.
            stats = compute_stats(distro, list(current))
            mean_v, sd_v = stats if stats is not None else (None, None)
            for key, val in (("Mean", mean_v), ("Std Dev", sd_v)):
                lbl = stats_labels[key]
                if val is None:
                    lbl.setText("—")
                elif isinstance(val, str):
                    lbl.setText(val)
                else:
                    shown = native_to_display(val, native_units,
                                              display_unit)
                    lbl.setText(f"{shown:.4g}{u_stats}")
        _refresh()
        original_on_commit = on_commit

        def on_commit_with_stats(vals):
            # Plain assignment (not a per-index loop) so Empirical's
            # arbitrary-length `vals` from the data-entry dialog can grow
            # or shrink `current`.
            current[:] = vals
            _refresh()
            original_on_commit(vals)
        on_commit = on_commit_with_stats

    def _pick_rate_into(self, edit: QLineEdit, commit_fn) -> None:
        """"📎" button on a formula-enabled field: open the Rates
        picker, insert "@Name" into the field, and commit it
        immediately (matching what typing "@Name" by hand and pressing
        Enter would do)."""
        from raptor.gui.ui.rates_dialog import pick_rate
        name = pick_rate(self, self._rbd,
                         lambda: self.rates_changed.emit())
        if name:
            edit.setText(f"@{name}")
            commit_fn()

    def _float_row(self, form: QFormLayout, label: str, value: float, on_commit,
                   is_time: bool = False):
        """A single editable numeric field -- same commit/revert shape as
        `_distribution_params_row`'s per-field logic, just for one value instead of
        three (e.g. PM Frequency, not a 3-param distribution row).

        `is_time=True`: the field is a time-dimensioned value (PM
        Frequency, not e.g. a 0-100 threshold percentage) -- it shows and
        accepts the model's display unit (native unless a display-unit
        override is set) instead of the raw stored number, converting on
        the way in and out. Callers with a non-time value (percentages,
        counts) leave this False, which also means no unit suffix is added
        to `label`."""
        from raptor.gui.ui.units_conv import native_to_display, display_to_native
        native_units = getattr(self, "_units_str", "") or ""
        display_unit = getattr(self, "_display_unit", None) if is_time else None
        shown_units = (display_unit or native_units) if is_time else ""
        shown_label = f"{label} ({shown_units})" if shown_units else label

        def to_shown(v: float) -> float:
            return native_to_display(v, native_units, display_unit) \
                if is_time else v

        last_committed = {"value": value}
        edit = QLineEdit(f"{to_shown(value):.6g}")
        edit.setAccessibleName(shown_label)
        edit.setValidator(QDoubleValidator())

        def commit():
            text = edit.text().strip()
            try:
                raw = float(text)
            except ValueError:
                edit.setText(f"{to_shown(last_committed['value']):.6g}")
                return
            new_v = display_to_native(raw, native_units, display_unit) \
                if is_time else raw
            if new_v == last_committed["value"]:
                return
            last_committed["value"] = new_v
            edit.setText(f"{to_shown(new_v):.6g}")
            on_commit(new_v)

        edit.editingFinished.connect(commit)
        form.addRow(shown_label + ":", edit)
        # Long labels must wrap, not force the tab wide: the PM tab's
        # "At start-up, PM cycle has already elapsed (hours):" row would
        # otherwise make the narrow Inspector dock sprout a horizontal
        # scrollbar.
        lbl = form.labelForField(edit)
        if isinstance(lbl, QLabel):
            lbl.setWordWrap(True)
        return edit

    def _int_row(self, form: QFormLayout, label: str, value: int, on_commit, *,
                 minimum: int = 1, maximum: int | None = None):
        """Integer sibling of `_float_row` -- e.g. a node's K-out-of-N
        `good_paths` threshold, not a distribution parameter."""
        last_committed = {"value": value}
        edit = QLineEdit(str(value))
        edit.setAccessibleName(label)
        validator = QIntValidator()
        validator.setBottom(minimum)
        if maximum is not None:
            validator.setTop(maximum)
        edit.setValidator(validator)

        def commit():
            text = edit.text().strip()
            try:
                new_v = int(text)
            except ValueError:
                edit.setText(str(last_committed["value"]))
                return
            # `QIntValidator.setTop`/`setBottom` only reject some out-of-
            # range keystrokes while typing (its own "Intermediate" state
            # is permissive about digit COUNT, not the final value) --
            # clamp explicitly here so a value outside [minimum, maximum]
            # can never actually commit.
            if new_v < minimum:
                new_v = minimum
            elif maximum is not None and new_v > maximum:
                new_v = maximum
            if new_v == last_committed["value"]:
                edit.setText(str(new_v))
                return
            last_committed["value"] = new_v
            edit.setText(str(new_v))
            on_commit(new_v)

        edit.editingFinished.connect(commit)
        form.addRow(label + ":", edit)
        return edit

    def _name_edit(self, form: QFormLayout, current_name: str, on_commit):
        """An editable `Name` row -- `on_commit(new_name)` fires on Enter
        or focus-loss, only when the text actually changed (matching
        `editingFinished`'s "fires once you're done, not per keystroke"
        semantics, so a rename doesn't rebuild the canvas label on every
        keystroke).

        Whitespace is collapsed to underscores, not just stripped at the
        ends: `.rbd` rows are whitespace-delimited (`rbd.py`'s
        `_split_row`, plain `line.split()`), so a name containing an
        embedded space would shift every later column on save. This
        matches the underscore convention used by Raptor 7 names such as
        `SolarP_500W` and `BatteriesNiCd`."""
        edit = QLineEdit(current_name)
        edit.setAccessibleName("Name")
        # A plain closure over `current_name` would go stale after the
        # first successful commit: a second edit to an empty field on the
        # same QLineEdit, with no re-populate in between, would revert to
        # the original name shown when the field was built, not the
        # just-committed one. Track the last-known-good value in this
        # mutable cell instead, updated on every real commit.
        last_committed = {"name": current_name}

        def commit():
            new_name = "_".join(edit.text().split()) or last_committed["name"]
            if new_name != edit.text():
                edit.setText(new_name)
            if new_name != last_committed["name"]:
                last_committed["name"] = new_name
                on_commit(new_name)

        edit.editingFinished.connect(commit)
        # Original enforces names <= 20 chars. This port deliberately
        # keeps long names in the native .rbds format, so instead of
        # blocking, tell the user when a name is past the legacy limit
        # (the legacy .rbd save already truncates, with its own warning).
        def _len_hint(_t: str = ""):
            n = len(edit.text())
            if n > 20:
                edit.setToolTip(
                    f"{n} characters — the .rbd file format truncates "
                    f"names to 20 on save (.rbds keeps the full name).")
            else:
                edit.setToolTip("")
        edit.textChanged.connect(_len_hint)
        _len_hint()
        form.addRow("Name:", edit)
        return edit

    def _comment_row(self, form: QFormLayout, etype: str, oid: int, name: str) -> None:
        """Legacy Raptor 7 comment box (Part #, data provenance…), same
        widget/behaviour as the block General tab's own (which stays on
        its bespoke `block_comment_changed` path rather than being
        rewired through here). Used for node/event/hier, whose Raptor 7
       Node/Event/Hierarchy Properties General tabs all have this
        box too. Emits the generic `element_comment_changed(etype, oid,
        text)`, which `DiagramView.set_element_comment` accepts.
        """
        from PySide6.QtWidgets import QPlainTextEdit as _PTE
        edit = _PTE()
        edit.setFixedHeight(56)
        edit.setPlainText(self._comment_for(etype, name).replace("~", "\n"))
        edit.setToolTip(
            "Free-text comment stored with the model (Part #, data "
            "source, etc.)")
        edit.textChanged.connect(
            lambda: None if getattr(self, "_populating", False)
            else self.element_comment_changed.emit(
                etype, oid, edit.toPlainText()))
        form.addRow("Comment:", edit)

    def _phasing_group(self, layout: QLayout | None, kind: str, oid: int,
                       phased_now: bool,
                       entries_now: list[tuple[str, float]]):
        """Phase assignment UI: only when the model
        defines phases. One Apply button commits the whole table via a
        single undoable command."""
        layout = _vbox(layout)
        sec = self._rbd.sections.get("PHASES") if self._rbd else None
        if not sec or not sec.rows:
            # No mission phases defined yet -- still surface a way in
            # (Raptor 7's Options menu, mirrored here) rather than
            # silently hiding the whole feature.
            _gb, fl = self._group("Phasing", layout)
            self._row(fl, "Status", "No phases defined")
            define_btn = QPushButton("Define Phases…")
            define_btn.setToolTip(
                "Create mission phases")
            define_btn.clicked.connect(
                lambda: self.manage_pool_requested.emit("PHASES"))
            fl.addRow(define_btn)
            return
        phase_names = [r.get("PhaseName", f"Phase {i + 1}")
                       for i, r in enumerate(sec.rows)]
        _gb, fl = self._group("Phasing", layout)
        phased_cb = QCheckBox("Phased")
        phased_cb.setChecked(phased_now)
        phased_cb.setToolTip(
            "A phased element's attributes change from one phase to "
            "another (below) -- shown hatched on the canvas.")
        fl.addRow(phased_cb)
        editors: list[tuple[QComboBox, Any]] = []
        for pi, phase_name in enumerate(phase_names):
            code_now = entries_now[pi][0] if pi < len(entries_now) else (
                "A" if kind != "node" else "L")
            val_now = entries_now[pi][1] if pi < len(entries_now) else 1.0
            combo = QComboBox()
            if kind == "node":
                for code, label in (("K", "k paths"), ("C", "Cut"),
                                    ("L", "Linked")):
                    combo.addItem(label, code)
            else:
                for code, label in (("A", "Active stress"), ("L", "Linked"),
                                    ("C", "Cut")):
                    combo.addItem(label, code)
            ci = combo.findData(code_now)
            combo.setCurrentIndex(max(ci, 0))
            spin: QSpinBox | QDoubleSpinBox
            if kind == "node":
                spin = QSpinBox()
                spin.setRange(0, 999)
                spin.setValue(int(val_now) if code_now == "K" and val_now
                              else max(int(val_now), 0))
            else:
                spin = QDoubleSpinBox()
                spin.setRange(-1e9, 1e9)
                spin.setDecimals(3)
                spin.setValue(float(val_now))
            fl.addRow(f"{phase_name}:", combo)
            fl.addRow("", spin)
            editors.append((combo, spin))

        def apply_phasing():
            entries = []
            for combo, spin in editors:
                code = combo.currentData()
                if kind == "node" and code == "K":
                    entries.append(("K", float(spin.value())))
                else:
                    entries.append((code, float(spin.value())))
            self.phasing_changed.emit(kind, oid,
                                      phased_cb.isChecked(), entries)

        apply_btn = QPushButton("Apply phasing")
        apply_btn.clicked.connect(apply_phasing)
        fl.addRow(apply_btn)
        manage_btn = QPushButton("Manage Phases…")
        manage_btn.setToolTip(
            "Add, rename or delete mission phases")
        manage_btn.clicked.connect(
            lambda: self.manage_pool_requested.emit("PHASES"))
        fl.addRow(manage_btn)

    def _populate_block(self, block: RBDBlock):
        self._populating_comment = True
        try:
            self._populate_block_inner(block)
        finally:
            self._populating_comment = False

    def _populate_block_inner(self, block: RBDBlock):
        self._configure_tabs("block")
        self._header.setText(f"Block: {block.name}  (ID {block.id})")
        self._current_block_id = block.id
        self._current_node_id = None
        # Only the tab that will be shown is built now; the rest are built when the user opens them. Building all
        # seven on every canvas click was most of the lag when selecting elements.
        blocks = getattr(self, "_blocks_source", None) or {}
        bid = block.id

        def live() -> RBDBlock:
            return blocks.get(bid, block)      # the view's current object, not the one captured at click time

        def general() -> None:
            b = live()
            self._populating_comment = True
            try:
                self._populate_general(b)
            finally:
                self._populating_comment = False
            w = self._scroll_general.widget()
            self._phasing_group(
                req(w).layout(), "block", b.id, b.uses_phasing, list(zip(b.phase_type, b.phase_value)))

        steps = [(self._scroll_general, general),
                 (self._scroll_failure, lambda: self._populate_failure(live())),
                 (self._scroll_repair, lambda: self._populate_repair(live())),
                 (self._scroll_spares, lambda: self._populate_spares(live())),
                 (self._scroll_pm, lambda: self._populate_pm(live())),
                 (self._scroll_deps, lambda: self._populate_deps(live())),
                 (self._scroll_costs, lambda: self._populate_costs(live()))]
        self._deferred_tabs = {self._tabs.indexOf(scroll): build for scroll, build in steps}
        self._restore_tab()
        if self.lazy_tabs:
            self._run_deferred(self._tabs.currentIndex())
        else:
            self.ensure_all_tabs_built()

    def _populate_general(self, block: RBDBlock):
        w = self._scroll_general.widget()
        layout = req(w).layout()
        self._clear_tab(self._scroll_general)
        _c = active_colour()

        _gb, fl = self._group("Identity", layout)
        block_id = block.id
        self._name_edit(fl, block.name, lambda new_name: self.block_renamed.emit(block_id, new_name))
        self._row(fl, "ID", block.id)
        self._row(fl, "Parent ID", block.parent_id)
        # Legacy Raptor 7 comment (Part #, data provenance…) — editable,
        # multi-line; '~' separators from the file become line breaks
        from PySide6.QtWidgets import QPlainTextEdit as _PTE
        self._comment_edit = _PTE()
        self._comment_edit.setFixedHeight(56)
        self._comment_edit.setPlainText(
            self._comment_for("block", block.name).replace("~", "\n"))
        self._comment_edit.setToolTip(
            "Free-text comment stored with the model (Part #, data "
            "source, etc.)")
        self._comment_setter = lambda txt: self.block_comment_changed.emit(
            block_id, txt)
        self._comment_edit.textChanged.connect(
            lambda: None if getattr(self, "_populating_comment", False)
            else self._comment_setter(
                self._comment_edit.toPlainText()))
        fl.addRow("Comment:", self._comment_edit)

        _gb, fl = self._group("Position", layout)
        self._position_source = block
        self._position_labels = (
            self._row(fl, "X", f"{block.x_position:.3f}"),
            self._row(fl, "Y", f"{block.y_position:.3f}"))

        _gb, fl = self._group("Connections", layout)
        self._row(fl, "Inputs", block.connect_into_num)
        self._row(fl, "Outputs", block.connect_out_of_num)

        _gb, fl = self._group("Simulation", layout)
        start = _SIM_START.get(block.sim_start_type, f"Unknown ({block.sim_start_type})")
        self._row(fl, "Start state", start)
        self._row(fl, "Uses phasing", "Yes" if block.uses_phasing else "No")
        # For "% repair complete" starts the value is a percentage of the
        # repair cycle; for "some life exhausted" starts it's time units.
        if block.sim_start_type == 5:
            self._row(fl, "% complete", f"{block.amount_exhausted:.3f}")
        elif block.sim_start_type in (3,):
            self._row(fl, "Amount exhausted", f"{block.amount_exhausted:.3f}")

    def _populate_failure(self, block: RBDBlock):
        w = self._scroll_failure.widget()
        layout = req(w).layout()
        self._clear_tab(self._scroll_failure)

        gb, fl = self._group("Failure Distribution", layout)
        self._distro_type_row(fl, block.fail_distro, True, block.id,
                              self.block_fail_type_changed)
        self._stream_editor_row(fl, block.fail_stream, "F", block.id)
        block_id = block.id
        self._distribution_params_row(
            fl, block.fail_distro, "Failure", block.fail_values,
            lambda vals: self.block_fail_params_changed.emit(block_id, vals),
            addr_prefix=f"block:{block_id}:F",
        )

        # Advanced tab fields (Raptor 7 "Mean Life Growth and
        # Decay" plus Raptor 7 "Standby Stress") folded in here: Raptor 7's
        # Block Properties dialog has a separate Advanced tab,
        # but this Inspector is a fixed 7-slot layout with every slot
        # spoken for (same precedent as Simulation Theory being folded
        # into the Failure/Repair stream editors). Growth/decay can be
        # switched on from scratch here.
        gb, fl = self._group("Advanced", layout)
        gb.setToolTip(
            "Standby stress and growth/decay -- how this block's "
            "effective failure rate changes over time or while in "
            "standby.")
        stress_spin = QDoubleSpinBox()
        stress_spin.setRange(0.0, 100.0)
        stress_spin.setDecimals(4)
        stress_spin.setValue(float(block.sb_stress))
        stress_spin.setToolTip(
            "0 = cold standby, between 0 and 1 = warm, >=1 = hot "
            "(fails at the normal rate even while idle).")
        stress_spin.editingFinished.connect(
            lambda bid=block_id, sp=stress_spin:
                self.block_standby_stress_changed.emit(bid, sp.value()))
        fl.addRow("Standby stress:", stress_spin)

        gd_cb = QCheckBox(
            "This block's mean life changes following a failure")
        gd_cb.setChecked(block.gd_type != 0)
        gd_cb.toggled.connect(
            lambda on, bid=block_id:
                self.block_gd_enabled_changed.emit(bid, on))
        fl.addRow(gd_cb)
        if block.gd_type != 0:
            gd_combo = QComboBox()
            gd_combo.addItem("Linear", 1)
            gd_combo.addItem("Geometric", 2)
            gd_combo.addItem("Asymptotic", 3)
            gci = gd_combo.findData(block.gd_type
                                    if block.gd_type in (1, 2) else 3)
            gd_combo.setCurrentIndex(max(gci, 0))
            gd_combo.currentIndexChanged.connect(
                lambda _i, bid=block_id, cmb=gd_combo:
                    self.block_gd_type_changed.emit(bid, cmb.currentData()))
            fl.addRow("Type:", gd_combo)

            rate_spin = QDoubleSpinBox()
            rate_spin.setRange(-1e9, 1e9)
            rate_spin.setDecimals(6)
            rate_spin.setValue(float(block.gd_rate))
            limit_spin = QDoubleSpinBox()
            limit_spin.setRange(-1e9, 1e9)
            limit_spin.setDecimals(6)
            limit_spin.setValue(float(block.gd_limit))

            def _commit_gd(bid=block_id, rs=rate_spin, ls=limit_spin):
                self.block_gd_rate_limit_changed.emit(
                    bid, [rs.value(), ls.value()])
            rate_spin.editingFinished.connect(_commit_gd)
            limit_spin.editingFinished.connect(_commit_gd)
            fl.addRow("Rate:", rate_spin)
            fl.addRow("Limit:", limit_spin)

    def _populate_repair(self, block: RBDBlock):
        w = self._scroll_repair.widget()
        layout = req(w).layout()
        self._clear_tab(self._scroll_repair)

        _gb, fl = self._group("Repair Distribution", layout)
        if block.repair_distro == 0:
            # A repair distro of 0 means "no repair law" -- same meaning as
            # code 18 for a repair, so both read the same way. The label
            # comes from `distro_combo` (review E-14); this used to be a
            # fourth, independent copy of the code-18 rule.
            from raptor.gui.ui.distro_combo import _UNREPAIRABLE
            self._row(fl, "Type", _UNREPAIRABLE)
        else:
            self._distro_type_row(fl, block.repair_distro, False,
                                  block.id, self.block_repair_type_changed)
            if block.repair_distro != 18:
                self._stream_editor_row(fl, block.repair_stream, "R",
                                        block.id)
            block_id = block.id
            self._distribution_params_row(
                fl, block.repair_distro, "Repair", block.repair_values,
                lambda vals: self.block_repair_params_changed.emit(block_id, vals),
                addr_prefix=f"block:{block_id}:R",
            )

    def _populate_spares(self, block: RBDBlock):
        w = self._scroll_spares.widget()
        layout = req(w).layout()
        self._clear_tab(self._scroll_spares)

        st = block.sparing_type
        gb, fl = self._group("Sparing", layout)
        bid2 = block.id
        type_combo = QComboBox()
        # Words a user reads, not the enum's names (NONE, SPARE_POOL).
        for label, type_name in (("None — repair in place", "NONE"),
                             ("Spare pool", "SPARE_POOL"),
                             ("Infinite spares", "INFINITE"),
                             ("Custom stock", "CUSTOM")):
            type_combo.addItem(label, type_name)
        ti = type_combo.findData(st.name)
        type_combo.setCurrentIndex(max(ti, 0))
        type_combo.setToolTip(
            "Replacement strategy: none (repair in place), a spare pool "
            "(draw from a pool), infinite (a spare is always available), "
            "custom (this block's own stock and arrival schedule)")
        type_combo.activated.connect(
            lambda _i, b=bid2, cmb=type_combo:
            self.sparing_type_changed.emit(b, cmb.currentData()))
        fl.addRow("Type:", type_combo)

        pool_combo = QComboBox()
        pool_combo.addItem("(none)", "")
        pools_sec = (self._rbd.sections.get("SPARE_POOL_DATA")
                     if self._rbd else None)
        for r3 in (pools_sec.rows if pools_sec else []):
            param_name = r3.get("PoolName", "")
            if param_name:
                pool_combo.addItem(param_name, param_name)
        pi = pool_combo.findText(block.pool_name or "")
        pool_combo.setCurrentIndex(max(pi, 0))
        pool_combo.setEnabled(st in (SparingType.SPARE_POOL,))
        pool_combo.setToolTip("Spare pool this block draws from")
        pool_combo.activated.connect(
            lambda _i, b=bid2, cmb=pool_combo:
            self.pool_name_changed.emit(b, cmb.currentText()))
        fl.addRow("Pool:", pool_combo)
        manage_pools_btn = QPushButton("Manage Spare Pools…")
        manage_pools_btn.setToolTip(
            "Create, edit or delete spare pools")
        manage_pools_btn.clicked.connect(
            lambda: self.manage_pool_requested.emit("SPARE_POOL_DATA"))
        fl.addRow(manage_pools_btn)

        if st in (SparingType.SPARE_POOL, SparingType.INFINITE, SparingType.CUSTOM):
            gb, fl = self._group("Spare Stock", layout)
            if st == SparingType.CUSTOM:
                # Engine reads block.init_stock only for Custom sparing
                # (block_lifecycle's spares_remaining; cost `sic`) --
                # editing it on a Pooled block would be a silent no-op.
                from PySide6.QtWidgets import QSpinBox
                spin = QSpinBox()
                spin.setRange(0, 999_999)
                spin.setValue(int(block.init_stock))
                last = {"v": int(block.init_stock)}

                def commit_init():
                    if spin.value() != last["v"]:
                        last["v"] = spin.value()
                        self.block_init_stock_changed.emit(block.id,
                                                           spin.value())
                spin.valueChanged.connect(commit_init)
                fl.addRow("Initial stock:", spin)
            else:
                hint = " (pool-managed)" if st == SparingType.SPARE_POOL \
                    else ""
                self._row(fl, "Initial stock", f"{block.init_stock}{hint}")
            if st == SparingType.CUSTOM:
                # Same CUSTOM-gating as Initial stock above -- the engine
                # reads new_spares/new_spares_arrival for its per-block
                # arrival queue only under Custom sparing (`pool.py`'s
                # block-scoped spare-cycle generator); editing
                # these for Pooled/Infinite blocks would be a no-op.
                from PySide6.QtWidgets import QSpinBox
                count_spin = QSpinBox()
                count_spin.setRange(0, 999_999)
                count_spin.setValue(int(block.new_spares))
                interval_spin = QDoubleSpinBox()
                interval_spin.setRange(0.0, 1e9)
                interval_spin.setDecimals(3)
                interval_spin.setValue(float(block.new_spares_arrival))

                def _commit_new_spares(bid=block.id, cs=count_spin,
                                       ivs=interval_spin):
                    self.block_new_spares_changed.emit(
                        bid, [cs.value(), ivs.value()])
                count_spin.editingFinished.connect(_commit_new_spares)
                interval_spin.editingFinished.connect(_commit_new_spares)
                fl.addRow("New spares/order:", count_spin)
                fl.addRow("Arrival interval:", interval_spin)
            else:
                self._row(fl, "New spares/order", block.new_spares)
                self._row(fl, "Arrival interval",
                          f"{block.new_spares_arrival:.3f}")

        if st == SparingType.SPARE_POOL:
            gb, fl = self._group("Spare Ordering", layout)
            self._row(fl, "Routine (RSO)",
                      "Yes" if block.routine_spare_ordering else "No",
                      "Routine Spare Ordering (RSO): reorder a spare on "
                      "a fixed schedule, before stock actually runs out.")
            self._row(fl, "Emergency (ESO)",
                      "Yes" if block.emergency_spare_ordering else "No",
                      "Emergency Spare Ordering (ESO): rush-order a spare "
                      "(usually faster but costlier) when the pool runs "
                      "out and a block needs one now.")
            self._row(fl, "Emergency time", f"{block.emer_time:.3f}",
                      "How long an emergency (ESO) spare takes to arrive.")
            self._row(fl, "Stock level (SLO)",
                      "Yes" if block.stock_level_ordering else "No",
                      "Stock-Level Ordering (SLO): automatically reorder "
                      "once the pool's stock drops to a threshold level.")
            if block.stock_level_ordering:
                self._row(fl, "SLO level", block.slo_order_level,
                          "Stock level that triggers an SLO reorder.")
                self._row(fl, "SLO quantity", block.slo_new_spares,
                          "How many spares an SLO reorder brings in.")
                self._row(fl, "SLO time", f"{block.slo_time:.3f}",
                          "How long an SLO reorder takes to arrive.")

        # Logistical Constraints (legacy Maintenance Information tab):
        # pre/post-repair logistics delay distributions
        gb, fl = self._group("Logistical Constraints", layout)
        ldt_tip = (
            "Logistics Delay Time (LDT): time spent waiting for parts, "
            "technicians, or transport -- separate from the repair work "
            "itself.\nPre-repair delay = LDT before the repair starts.\n"
            "Post-repair delay = LDT after the repair finishes (before "
            "the block goes back up).")
        gb.setToolTip(ldt_tip)
        bid3 = block.id
        # DELEGATED (review E-14); a failure law, so no code-18 relabel.
        from raptor.gui.ui.distro_combo import fill_distro_combo
        pre_combo = fill_distro_combo(QComboBox(), block.pre_dist,
                                  is_failure=True, kind="delay")
        pre_combo.setToolTip(ldt_tip)
        pre_combo.activated.connect(
            lambda _i, b=bid3, cmb=pre_combo:
            self.block_pre_ldt_dist_changed.emit(b, cmb.currentData()))
        fl.addRow("Pre-repair delay (LDT):", pre_combo)
        self._distribution_params_row(
            fl, block.pre_dist, "Pre-LDT", block.pre_params,
            lambda vals, b=bid3:
            self.block_pre_ldt_params_changed.emit(b, vals),
            addr_prefix=f"block:{bid3}:PreLDT",
            lambda_mode=False,       # Raptor's generic "other" box: no lambda
        )
        # DELEGATED (review E-14); a failure law, so no code-18 relabel.
        from raptor.gui.ui.distro_combo import fill_distro_combo
        post_combo = fill_distro_combo(QComboBox(), block.post_dist,
                                  is_failure=True, kind="delay")
        post_combo.setToolTip(ldt_tip)
        post_combo.activated.connect(
            lambda _i, b=bid3, cmb=post_combo:
            self.block_post_ldt_dist_changed.emit(b, cmb.currentData()))
        fl.addRow("Post-repair delay (LDT):", post_combo)
        self._distribution_params_row(
            fl, block.post_dist, "Post-LDT", block.post_params,
            lambda vals, b=bid3:
            self.block_post_ldt_params_changed.emit(b, vals),
            addr_prefix=f"block:{bid3}:PostLDT",
            lambda_mode=False,       # Raptor's generic "other" box: no lambda
        )

        gb, fl = self._group("Resources", layout)
        gb.setToolTip(
            "A resource (e.g. technicians) a block borrows during "
            "repair/PM and returns afterward -- a fixed shared pool, "
            "unlike a spare (consumed permanently). A block uses at "
            "most one resource pool, as in Raptor 7.")
        res_combo = QComboBox()
        res_combo.addItem("(none)", "")
        res_sec = (self._rbd.sections.get("RESOURCE_DATA")
                  if self._rbd else None)
        for r5 in (res_sec.rows if res_sec else []):
            row_name = r5.get("PoolName", "")
            if row_name:
                res_combo.addItem(row_name, row_name)
        rci = res_combo.findText(block.resource_pool_name or "")
        res_combo.setCurrentIndex(max(rci, 0))
        res_combo.activated.connect(
            lambda _i, b=block.id, cmb=res_combo:
            self.block_resource_pool_changed.emit(b, cmb.currentData()))
        fl.addRow("Pool:", res_combo)

        from PySide6.QtWidgets import QSpinBox as _QSpinBox
        repair_spin = _QSpinBox()
        repair_spin.setRange(0, 999)
        repair_spin.setValue(int(block.repair_resources_needed))
        pm_spin = _QSpinBox()
        pm_spin.setRange(0, 999)
        pm_spin.setValue(int(block.pm_resources_needed))
        # The PM count only matters when the block performs PM (the
        # engine gates #Res1pmRep on usesPM) -- inert otherwise, so
        # grey it out with the reason instead of inviting an edit that
        # does nothing. Toggling the PM checkbox re-populates the whole
        # inspector (MainWindow's block_pm_enabled_changed handler),
        # so this gate tracks the checkbox live.
        if not block.uses_pm:
            pm_spin.setEnabled(False)
            pm_spin.setToolTip(
                "Only used when the block performs Preventive "
                "Maintenance -- enable 'This block performs "
                "Preventive Maintenance' on the PM tab first.")

        def _commit_res_counts(bid=block.id, rs=repair_spin, ps=pm_spin):
            self.block_resource_counts_changed.emit(
                bid, [rs.value(), ps.value()])
        repair_spin.editingFinished.connect(_commit_res_counts)
        pm_spin.editingFinished.connect(_commit_res_counts)
        fl.addRow("Needed for repair:", repair_spin)
        fl.addRow("Needed for PM:", pm_spin)
        manage_res_btn = QPushButton("Manage Resources…")
        manage_res_btn.setToolTip(
            "Create, edit or delete resource pools")
        manage_res_btn.clicked.connect(
            lambda: self.manage_pool_requested.emit("RESOURCE_DATA"))
        fl.addRow(manage_res_btn)

    def _populate_pm(self, block: RBDBlock):
        w = self._scroll_pm.widget()
        layout = req(w).layout()
        self._clear_tab(self._scroll_pm)
        block_id = block.id

        # Raptor 7: "engage the checkbox at the top of this tab...
        # Once this checkbox is marked, preventive maintenance
        # activities... can be defined". Re-populates the whole tab on
        # toggle (matching the Node full-flow/cold-standby checkboxes'
        # pattern), since it gates whether the rest of the tab exists at
        # all.
        gb, fl = self._group("Preventive Maintenance", layout)
        pm_cb = _WordWrapCheckBox("This block performs Preventive "
                                  "Maintenance")
        pm_cb.setChecked(bool(block.uses_pm))
        pm_cb.toggled.connect(
            lambda on, bid=block_id: self.block_pm_enabled_changed.emit(
                bid, on))
        fl.addRow(pm_cb)

        if not block.uses_pm:
            return

        gb, fl = self._group("PM Configuration", layout)
        gb.setToolTip(
            "PM = Preventive Maintenance: scheduled servicing performed "
            "on a healthy block, separate from repairing a failure.")
        self._float_row(fl, "Frequency", block.pm_freq,
                         lambda v: self.block_pm_freq_changed.emit(block_id, v),
                         is_time=True)
        # Wording matches Raptor 7's PM tab ("At start-up PM cycle
        # has already elapsed"); "Stagger time" would be accurate but not
        # what a Raptor-trained user would search for.
        self._float_row(
            fl, "At start-up, PM cycle has already elapsed",
            block.pm_stagger,
            lambda v: self.block_pm_stagger_changed.emit(block_id, v),
            is_time=True)
        # Triggered: editable -- checkbox + trigger-name combo fed from
        # the model's TRIGGERS rows; writes pmTriggered/pmTrig
        trig_cb = _WordWrapCheckBox("Triggered by a PM trigger")
        trig_cb.setChecked(bool(block.pm_triggered))
        trig_combo = QComboBox()
        trig_combo.addItem("(none)", "")
        trig_sec = (self._rbd.sections.get("TRIGGERS")
                    if self._rbd else None)
        for r2 in (trig_sec.rows if trig_sec else []):
            type_name = r2.get("TriggerName", "")
            if type_name:
                trig_combo.addItem(type_name, type_name)
        if block.pm_trig:
            ti2 = trig_combo.findText(block.pm_trig)
            trig_combo.setCurrentIndex(max(ti2, 0))
        trig_combo.setEnabled(block.pm_triggered)

        def _trig_toggled(on: bool):
            self.block_pm_trig_changed.emit(
                block_id, on, trig_combo.currentText())
            trig_combo.setEnabled(on)
        def _trig_name(chosen: str):
            if block.pm_triggered:
                self.block_pm_trig_changed.emit(
                    block_id, True, chosen)
        trig_cb.toggled.connect(_trig_toggled)
        trig_combo.activated.connect(lambda _i: _trig_name(
            trig_combo.currentText()))
        fl.addRow(trig_cb)
        fl.addRow("Trigger name:", trig_combo)
        manage_trig_btn = QPushButton("Manage Triggers…")
        manage_trig_btn.setToolTip(
            "Create, edit or delete triggers")
        manage_trig_btn.clicked.connect(
            lambda: self.manage_pool_requested.emit("TRIGGERS"))
        fl.addRow(manage_trig_btn)

        gb, fl = self._group("PM Distribution", layout)
        # DELEGATED (review E-14); a PM law, so no code-18 relabel.
        from raptor.gui.ui.distro_combo import fill_distro_combo
        dist_combo = fill_distro_combo(QComboBox(), block.pm_dist,
                                       is_failure=True, kind="delay")
        dist_combo.currentIndexChanged.connect(
            lambda _i, bid=block.id, cmb=dist_combo:
            self.block_pm_dist_changed.emit(bid, cmb.currentData()))
        fl.addRow("Type:", dist_combo)
        self._distribution_params_row(
            fl, block.pm_dist, "PM", block.pm_params,
            lambda vals, bid=block.id:
            self.block_pm_params_changed.emit(bid, vals),
            addr_prefix=f"block:{block.id}:PM",
            lambda_mode=False,       # Raptor's generic "other" box: no lambda
        )

        gb, fl = self._group("PM Options", layout)
        for label, attr, key, tip in (
                ("Needs a spare to conduct PM", "pm_spare_needed",
                 "spare_needed", None),
                ("After PM is completed, refresh block like new",
                 "pm_refresh", "refresh", None),
                ("Defer PM until a non-mission phase occurs",
                 "pm_mis_defer", "mis_defer",
                 "Only meaningful when this model has phases defined."),
                ("Defer PM until a spare or resources are available",
                 "pm_req_defer", "req_defer", None),
                ("A failure resets the time to next PM activity",
                 "pm_fail_reset", "fail_reset", None)):
            cb = _WordWrapCheckBox(label)
            cb.setChecked(bool(getattr(block, attr)))
            if tip:
                cb.setToolTip(tip)
            cb.toggled.connect(
                lambda on, bid=block_id, k=key:
                    self.block_pm_option_changed.emit(bid, k, on))
            fl.addRow(cb)



    def _populate_deps(self, block: RBDBlock):
        w = self._scroll_deps.widget()
        layout = req(w).layout()
        self._clear_tab(self._scroll_deps)
        block_id = block.id

        # Master-element selector: `block.dependency_num`/`dep_type` use
        # the same encoding `_dependency_group` builds for Nodes
        # (0=independent, -1=locally, -2=system, >0=dependent-on-id), per
        # `dependency.py`'s block handling. The real Block
        # Properties Dependency tab matches the Node Properties
        # Dependency tab, so this exposes the same master-selector value.
        self._dependency_group(layout, block_id, block.dependency_num,
                               block.dep_type,
                               emit=self.block_dep_master_changed.emit)

        gb, fl = self._group("Default Dependency Action", layout)
        gb.setToolTip(
            "What this block does while waiting on its master element, "
            "before any dependency effect is actually applied.")
        r_op = QRadioButton("Operating state")
        r_idle = QRadioButton("Idle state")
        group = QButtonGroup(gb)
        group.addButton(r_op)
        group.addButton(r_idle)
        (r_idle if block.def_dep_state_idle else r_op).setChecked(True)

        def _send_default_dep_state(on, bid=block_id):
            # Both radios route through this one handler, which reads the
            # group's actual current state directly rather than trusting
            # either radio's own toggled `on` argument. Connecting only
            # r_idle.toggled would miss the `on=False` case: selecting
            # "Operating state" unchecks r_idle and would send nothing.
            if r_idle.isChecked() or r_op.isChecked():
                self.block_dep_default_idle_changed.emit(
                    bid, r_idle.isChecked())
        r_op.toggled.connect(_send_default_dep_state)
        r_idle.toggled.connect(_send_default_dep_state)
        fl.addRow(r_op)
        fl.addRow(r_idle)

        if block.dependency_num == 0:
            return

        gb, fl = self._group("Effects (%)", layout)
        dep_id = block.id
        fields: dict[str, QDoubleSpinBox] = {}

        def form_add(w: QWidget, label: str):
            fl.addRow(label + ":", w)

        for label, attr, cur in (
                ("Nothing %", "dep_nothing_percent", block.dep_nothing_percent),
                ("Idle %", "dep_idle_percent", block.dep_idle_percent),
                ("PM %", "dep_pm_percent", block.dep_pm_percent),
                ("Failure %", "dep_fail_percent", block.dep_fail_percent)):
            sp = QDoubleSpinBox()
            sp.setRange(0.0, 100.0)
            sp.setDecimals(2)
            sp.setValue(float(cur))
            fields[attr] = sp
            form_add(sp, label)
        thr = QDoubleSpinBox()
        thr.setRange(0.0, 1e9)
        thr.setDecimals(3)
        thr.setValue(float(block.dep_pm_threshold))
        form_add(thr, "PM threshold")

        apply_btn = QPushButton("Apply dependency")
        def apply_dep():
            percs = [fields[a].value() for a in
                     ("dep_nothing_percent", "dep_idle_percent",
                      "dep_pm_percent", "dep_fail_percent")]
            if abs(sum(percs) - 100.0) > 0.05:
                self.validation_failed.emit(
                    f"Dependency percentages must sum to 100 "
                    f"(got {sum(percs):.1f})")
                return
            self.block_dep_changed.emit(dep_id, percs +
                                        [thr.value()])
        apply_btn.clicked.connect(apply_dep)
        fl.addRow(apply_btn)

    def _populate_costs(self, block: RBDBlock):
        w = self._scroll_costs.widget()
        layout = req(w).layout()
        self._clear_tab(self._scroll_costs)

        from raptor.gui.ui.diagram import DiagramView
        fields = DiagramView.BLOCK_COST_FIELDS
        # Wording matches Raptor 7's own Costs tab rather than generic
        # column-name-derived labels -- e.g. "Rebate or disposal" not
        # "Done Fixed", "Recurring spare" not "Per spare".
        labels = ["Initial acquisition", "Running (/time)",
                  "Standby (/time)", "Idle downtime (/time)",
                  "Hands-on maintenance, fixed cost",
                  "Hands-on maintenance (/time)",
                  "Logistical hold (/time)",
                  "Preventive maintenance, fixed cost",
                  "Preventive maintenance (/time)",
                  "Preventive maintenance hold (/time)",
                  "Rebate or disposal, fixed cost",
                  "Permanently done (/time)",
                  "Recurring spare", "Emergency spare additional cost"]
        cost_id = block.id
        spins: list[QDoubleSpinBox] = []
        _gb, fl = self._group("Costs (edit all, then Apply)", layout)

        def form_add(w2, label):
            fl.addRow(label + ":", w2)

        pool_sparing = block.sparing_type == SparingType.SPARE_POOL
        for label, (attr, _col) in zip(labels, fields):
            sp = QDoubleSpinBox()
            sp.setRange(-1e12, 1e12)
            sp.setDecimals(3)
            sp.setValue(float(getattr(block, attr)))
            if pool_sparing and attr in ("spare_cost", "emergency_shipping_cost"):
                # The engine reads these two only for
                # SparingType.INFINITE (block_lifecycle.py); a Pooled
                # block's spare/emergency cost is the pool's own field
                # instead (Manage Spare Pools...), so editing here would
                # do nothing. Raptor 7 greys these out for the same
                # reason.
                sp.setEnabled(False)
                sp.setToolTip(
                    "This block draws from a spare pool -- its cost "
                    "comes from the POOL's own fields (Spares tab -> "
                    "Manage Spare Pools…), not this one.")
            spins.append(sp)
            form_add(sp, label)
        always_cb = QCheckBox("Always add Done cost at run end")
        always_cb.setChecked(bool(block.always_add_done_cost))

        def commit_always(on: bool, bid=block.id):
            self.block_always_done_changed.emit(bid, on)
        always_cb.toggled.connect(commit_always)
        fl.addRow(always_cb)

        def apply_costs():
            vals = [sp.value() for sp in spins]
            self.block_costs_changed.emit(cost_id, vals)

        apply_btn = QPushButton("Apply costs")
        apply_btn.clicked.connect(apply_costs)
        fl.addRow(apply_btn)

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

    def show_annotation(self, item):
        """Colours & Font for one canvas annotation (rect/ellipse/text
        overlay shape), in the Inspector rather than the separate modal
        "Annotation Properties" dialog it replaced. Each control commits
        its own undo step via `annotation_prop_changed`, which the
        receiver (`DiagramView.set_annotation_props`) turns into one
        `AnnoEditCmd` snapshotting the full before/after state."""
        from raptor.gui.ui.diagram import ANNO_DEFAULTS
        self._begin_populate()
        self._configure_tabs("annotation")
        aid = getattr(item, "_anno_id", None)
        kind = getattr(item, "kind", "annotation")
        # The ribbon's tool buttons say "Rectangle"/"Ellipse"/"Text"/
        # "Arrow"; `kind.title()` said "Rect", so the two disagreed.
        self._header.setText(
            f"Annotation: {_ANNO_KIND_LABELS.get(kind, kind.title())}"
            f"  (ID {aid})")
        # Record what the Inspector is showing. Without this it kept the
        # last element's ("block", id), so a prop commit (e.g. a line
        # width change) fired model_changed -> _refresh_inspector and the
        # panel jumped back to that block instead of staying on the
        # selected annotation.
        self._current_target = ("annotation", aid)
        self._current_block_id = None
        self._current_node_id = None
        for i in range(self._tabs.count()):
            self._clear_tab(cast(QScrollArea, self._tabs.widget(i)))
        w = self._scroll_general.widget()
        layout = req(w).layout()
        self._clear_tab(self._scroll_general)

        props = dict(item.props)

        def emit_changes(changes: dict):
            changed = {k: v for k, v in changes.items()
                      if props.get(k) != v}
            if not changed:
                return
            props.update(changed)
            self.annotation_prop_changed.emit(aid, changed)

        _gb, fl = self._group("Colours", layout)
        # An arrow has no separate border/fill split -- the "border" IS
        # the whole visible annotation, so this reuses the same prop
        # (border_colour/border_width) under a label that matches what
        # the user is actually looking at.
        line_label = "Line" if kind == "arrow" else "Border"
        self._colour_row(fl, line_label, props.get(
            "border_colour", ANNO_DEFAULTS["border_colour"]),
            lambda v: emit_changes({"border_colour": v}),
            allow_none=(kind != "arrow"))
        width_spin = QDoubleSpinBox()
        # 0 == no border (the old separate "draw border"
        # checkbox is gone), so 0 must stay reachable in this spinbox.
        # (Arrow never stores 0 by default -- see _begin_annotation_
        # arrow -- but the field stays usable if the user wants it.)
        width_spin.setRange(0.0, 20.0)
        width_spin.setSingleStep(0.5)
        width_spin.setSuffix(" px")
        width_spin.setValue(float(props.get(
            "border_width", ANNO_DEFAULTS["border_width"])))
        width_spin.valueChanged.connect(
            lambda v: emit_changes({"border_width": v}))
        fl.addRow(f"{line_label} width:", width_spin)

        # Line style dropdown -- one shared prop/control for rect,
        # ellipse and arrow alike. Text's border never had a dash concept
        # and doesn't get the control (the prop still round-trips
        # harmlessly if present).
        if kind in ("rect", "ellipse", "arrow"):
            from raptor.gui.ui.diagram import ANNO_LINE_STYLES
            style_labels = {"solid": "Solid", "dash": "Dash",
                            "dot": "Dot", "dash-dot": "Dash-Dot",
                            "dash-dot-dot": "Dash-Dot-Dot"}
            style_combo = QComboBox()
            for key in ANNO_LINE_STYLES:
                style_combo.addItem(style_labels.get(key, key.title()),
                                    key)
            cur_style = props.get("line_style",
                                  ANNO_DEFAULTS["line_style"])
            idx = style_combo.findData(cur_style)
            style_combo.setCurrentIndex(idx if idx >= 0 else 0)
            style_combo.currentIndexChanged.connect(
                lambda i: emit_changes(
                    {"line_style": style_combo.itemData(i)}))
            fl.addRow("Line style:", style_combo)

        if kind != "arrow":
            self._colour_row(fl, "Fill", props.get("fill_colour"),
                             lambda v: emit_changes({"fill_colour": v}),
                             allow_none=True)
        if kind == "text":
            self._colour_row(fl, "Text", props.get(
                "text_colour", ANNO_DEFAULTS["text_colour"]),
                lambda v: emit_changes({"text_colour": v}))

        if kind != "arrow":
            _gb, fl = self._group("Font", layout)
            self._font_row(fl, props, emit_changes)

        self._anchor_group(layout, aid, kind)
        self._restore_tab()

    #: The view the anchor controls read their choices from (its
    #: `annotation_entry` and `anchor_targets`); set by the main window.
    anchor_source: Any = None

    _ANCHOR_LOCATIONS = tuple(
        (key, _ANCHOR_LOCATION_LABELS[key]) for key in ANNO_LOCATIONS)

    def _anchor_group(self, layout, aid, kind: str) -> None:
        """Anchor: what the annotation is attached to. Anchored, it moves
        with its element (dragging it, Align, Auto Layout); "Not anchored"
        removes the link and leaves it where it is. Each change is one
        undoable edit (`annotation_prop_changed` with an `anchor`)."""
        src = self.anchor_source
        if src is None or aid is None:
            return
        entry = src.annotation_entry(int(aid)) or {}
        anchor = entry.get("anchor") if isinstance(entry.get("anchor"), dict) else None
        targets = src.anchor_targets(exclude=int(aid))
        _gb, fl = self._group("Anchor", layout)

        def combo_for(current_ref, current_name, none_label):
            box = QComboBox()
            box.addItem(none_label, None)
            for label, ref in targets:
                box.addItem(label, ref)
            idx = box.findData(current_ref) if current_ref else -1
            if idx < 0 and current_name:
                idx = next((i for i in range(1, box.count())
                            if box.itemText(i).split(": ", 1)[-1] == current_name), -1)
            box.setCurrentIndex(max(idx, 0))
            return box

        def name_of(box) -> str:
            return box.currentText().split(": ", 1)[-1]

        buttons = QHBoxLayout()
        buttons.setContentsMargins(0, 0, 0, 0)
        break_link = QPushButton("Break link")
        break_link.setToolTip("Stop following the target; the annotation "
                              "stays where it is")
        break_link.setEnabled(anchor is not None)
        break_link.clicked.connect(
            lambda _c=False: self.annotation_prop_changed.emit(
                aid, {"anchor": None}))
        buttons.addWidget(break_link)

        if kind == "arrow":
            tail = combo_for(anchor and anchor.get("from_ref"), anchor and anchor.get("from"),
                             "Free")
            head = combo_for(anchor and anchor.get("to_ref"), anchor and anchor.get("to"),
                             "Free")

            def commit_ends(_i=None):
                new: dict[str, Any] = {}
                for end, box in (("from", tail), ("to", head)):
                    if box.currentData() is not None:
                        new[end] = name_of(box)
                        new[f"{end}_ref"] = box.currentData()
                if not new:
                    if anchor is not None:
                        self.annotation_prop_changed.emit(aid, {"anchor": None})
                    return
                self.annotation_prop_changed.emit(aid, {"anchor": new})

            def end_row(box, end: str):
                pick = QPushButton("Pick…")
                pick.setToolTip(f"Click, then click an element or annotation "
                                f"to attach the arrow's {end} end to it (Esc cancels)")
                pick.setEnabled(hasattr(src, "start_anchor_pick"))
                pick.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Fixed)
                pick.clicked.connect(
                    lambda _c=False: self.annotation_arrow_pick_requested.emit(int(aid), end))
                remove = QPushButton("Remove")
                remove.setToolTip(f"Detach the {end} end: it stays where it is and "
                                  "no longer follows anything")
                remove.setEnabled(box.currentData() is not None)
                remove.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Fixed)
                remove.clicked.connect(lambda _c=False: box.setCurrentIndex(0))
                row = QWidget()
                lay = QHBoxLayout(row)
                lay.setContentsMargins(0, 0, 0, 0)
                lay.setSpacing(4)
                box.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
                box.setMinimumContentsLength(8)
                box.setSizeAdjustPolicy(
                    QComboBox.SizeAdjustPolicy.AdjustToMinimumContentsLengthWithIcon)
                lay.addWidget(box, 1)
                lay.addWidget(pick)
                lay.addWidget(remove)
                return row

            tail.currentIndexChanged.connect(commit_ends)
            head.currentIndexChanged.connect(commit_ends)
            fl.addRow("From:", end_row(tail, "from"))
            fl.addRow("To:", end_row(head, "to"))
            note = ("Each end can be attached to an element or another "
                    "annotation: it then follows it and stops at its edge. "
                    "Pick… chooses the target by clicking it; Remove frees "
                    "that end where it is.")
        else:
            target = combo_for(anchor and anchor.get("ref"),
                               anchor and anchor.get("target"), "Not anchored")
            # Editable, so the list doubles as a search field: type part of
            # an element name or annotation label and Qt's completer narrows
            # it. `NoInsert` stops a half-typed name becoming an option.
            target.setEditable(True)
            target.setInsertPolicy(QComboBox.InsertPolicy.NoInsert)
            # The Inspector is ~380 px wide; an unconstrained combo plus a
            # "Pick on canvas…" button overflowed it and clipped both. Let
            # the combo expand into whatever is left and cap its preferred
            # width instead of sizing to the longest element name.
            target.setSizeAdjustPolicy(
                QComboBox.SizeAdjustPolicy.AdjustToMinimumContentsLengthWithIcon)
            target.setMinimumContentsLength(8)
            target.setSizePolicy(QSizePolicy.Policy.Expanding,
                                 QSizePolicy.Policy.Fixed)
            if target.completer() is not None:
                target.completer().setFilterMode(
                    Qt.MatchFlag.MatchContains)
                target.completer().setCaseSensitivity(
                    Qt.CaseSensitivity.CaseInsensitive)
            pick = QPushButton("Pick on canvas…")
            pick.setToolTip("Click, then click an element or another "
                            "annotation to anchor this annotation to it "
                            "(Esc cancels)")
            pick.setEnabled(hasattr(src, "start_anchor_pick"))
            pick.setSizePolicy(QSizePolicy.Policy.Fixed,
                               QSizePolicy.Policy.Fixed)
            pick.clicked.connect(
                lambda _checked=False: self.annotation_anchor_pick_requested
                .emit(int(aid)))
            target_row = QWidget()
            target_layout = QHBoxLayout(target_row)
            target_layout.setContentsMargins(0, 0, 0, 0)
            target_layout.setSpacing(4)
            target_layout.addWidget(target, 1)
            target_layout.addWidget(pick)

            location = QComboBox()
            for key, label in self._ANCHOR_LOCATIONS:
                location.addItem(label, key)
            location.setCurrentIndex(max(
                location.findData((anchor or {}).get(
                    "location", ANNO_DEFAULT_LOCATION)), 0))
            gap = QDoubleSpinBox()
            gap.setRange(0.0, 500.0)
            gap.setSuffix(" px")
            gap.setValue(float((anchor or {}).get("gap", 12.0)))
            location.setEnabled(anchor is not None)
            gap.setEnabled(anchor is not None)

            def sync_gap(_v=None):
                gap.setEnabled(anchor is not None
                               and location.currentData() != "center")

            sync_gap()

            def commit(_v=None):
                if target.currentData() is None:
                    if anchor is not None:
                        self.annotation_prop_changed.emit(aid, {"anchor": None})
                    return
                self.annotation_prop_changed.emit(aid, {"anchor": {
                    "target": name_of(target), "ref": target.currentData(),
                    "location": location.currentData(),
                    "gap": float(gap.value())}})

            target.currentIndexChanged.connect(commit)
            location.currentIndexChanged.connect(sync_gap)
            location.currentIndexChanged.connect(commit)
            gap.editingFinished.connect(commit)
            fl.addRow("Anchored to:", target_row)
            fl.addRow("Location:", location)
            fl.addRow("Gap:", gap)
            offset = bool(anchor and (anchor.get("dx") or anchor.get("dy")))
            reset = QPushButton("Reset position")
            reset.setToolTip("Put the annotation back at its anchored "
                             "location (undoes where you dragged it)")
            reset.setEnabled(offset)

            def reset_position():
                self.annotation_prop_changed.emit(aid, {"anchor": {
                    k: v for k, v in (anchor or {}).items()
                    if k not in ("dx", "dy")}})

            reset.clicked.connect(lambda _c=False: reset_position())
            buttons.addWidget(reset)
            note = ("Anchored, it moves with its target. Centre ignores the "
                    "gap. Drag the annotation to place it where you like; "
                    "it keeps following its target from there. Reset "
                    "position returns it to its Location, Break link frees "
                    "it. Use Pick on canvas… to choose the target by "
                    "clicking it.")
        buttons.addStretch(1)
        fl.addRow(buttons)
        hint = QLabel(note)
        hint.setWordWrap(True)
        hint.setObjectName("SectionNote")
        fl.addRow(hint)

    def _colour_row(self, form: QFormLayout, label: str, current,
                    on_commit, allow_none: bool = False):
        """Swatch + paste-able hex field + (when `allow_none`) an
        explicit "no colour" toggle.

        `allow_none` is what makes a colour *removable*, and it is the
        point of the third control: until now the only ways to get
        "none" were to clear the hex field (undiscoverable) or to pick a
        fully transparent swatch in the colour dialog (also
        undiscoverable). A visible toggle makes the state obvious and
        one click to set, and it round-trips the SAME stored value --
        `None` -- that `_anno_pen_brush`/`_AnnoText` treat as "don't
        draw this"."""
        from PySide6.QtGui import QColor
        state = {"current": current}
        btn = QPushButton()
        btn.setFixedWidth(28)
        btn.setToolTip(f"Pick a {label.lower()} colour")
        # Paste-able hex field: without it the swatch button would be the
        # only way to change the colour, and being a QPushButton its hex
        # text couldn't be clicked into or pasted over. Typing/pasting
        # here updates the swatch directly, without the QColorDialog.
        hex_edit = QLineEdit()
        hex_edit.setFixedWidth(80)
        hex_edit.setPlaceholderText("#RRGGBB")
        none_btn = None
        if allow_none:
            none_btn = QToolButton()
            none_btn.setText("None")
            none_btn.setCheckable(True)
            none_btn.setFixedWidth(46)
            none_btn.setToolTip(
                f"Draw no {label.lower()} at all (stores null)")

        def restyle():
            c = state["current"]
            # A border that contrasts with the swatch itself, so it stays
            # visible even when the colour matches the panel behind it
            # (a white swatch on a white Inspector otherwise disappears).
            if c:
                col = QColor(c)
                lum = (0.299 * col.red() + 0.587 * col.green()
                       + 0.114 * col.blue())
                border = "#000000" if lum > 140 else "#FFFFFF"
            else:
                border = active_colour()["muted"]
            btn.setStyleSheet(
                f"background: {c or 'transparent'}; "
                f"border: 2px solid {border}; border-radius: 3px;")
            if hex_edit.text() != (c or ""):
                hex_edit.setText(c or "")
            if none_btn is not None:
                was = none_btn.blockSignals(True)
                none_btn.setChecked(c is None)
                none_btn.blockSignals(was)
        restyle()

        # The last real colour committed, so toggling "None" off can put
        # it back instead of leaving the row blank. Kept here rather
        # than captured at construction: the user may pick a colour, hit
        # None, pick another, hit None again, and expect the LATHER one
        # back.
        memory: dict = {"last": current}

        def commit(new_val, *, from_hex: bool):
            state["current"] = new_val
            if new_val:
                memory["last"] = new_val
            restyle()
            if not from_hex:
                hex_edit.setText(new_val or "")
            on_commit(new_val)

        if none_btn is not None:
            def _none_toggled(on: bool):
                commit(None if on else (memory["last"] or "#5D7080"),
                       from_hex=True)
            none_btn.toggled.connect(_none_toggled)

        def pick():
            from PySide6.QtWidgets import QColorDialog
            base = QColor(state["current"] or "#FFFFFF")
            if allow_none:
                c = QColorDialog.getColor(base, self, f"{label} colour",
                                          QColorDialog.ColorDialogOption.ShowAlphaChannel)
            else:
                c = QColorDialog.getColor(base, self, f"{label} colour")
            if not c.isValid():
                return
            new_val = None if (allow_none and c.alpha() == 0) else c.name()
            commit(new_val, from_hex=False)
        btn.clicked.connect(pick)

        def hex_committed():
            text = hex_edit.text().strip()
            if not text:
                if allow_none:
                    commit(None, from_hex=True)
                else:
                    hex_edit.setText(state["current"] or "")
                return
            if not text.startswith("#"):
                text = "#" + text
            c = QColor(text)
            if not c.isValid():
                hex_edit.setText(state["current"] or "")   # reject, restore
                return
            commit(c.name(), from_hex=True)
        hex_edit.editingFinished.connect(hex_committed)

        row = QHBoxLayout()
        row.setContentsMargins(0, 0, 0, 0)
        # The three controls are sized to their content and packed to
        # the left. Previously the row widget carried no stretch, so
        # QFormLayout's AllNonFixedFieldsGrow spread the swatch and the
        # hex field apart with a visible gap between them, while the
        # spin/combo rows above stretched to the full column width.
        row.addWidget(btn)
        row.addSpacing(SPACING["xs"])
        row.addWidget(hex_edit)
        if none_btn is not None:
            row.addSpacing(SPACING["xs"])
            row.addWidget(none_btn)
        row.addStretch(1)
        row_w = QWidget()
        row_w.setLayout(row)
        form.addRow(label + ":", row_w)
        return none_btn

    def _font_row(self, form: QFormLayout, props: dict, emit_changes):
        """Font controls: a "Font..." button for family, plus the two
        adjustments people actually make most (bold/italic and size) as
        direct controls.

        Bold, italic and size were previously reachable ONLY through a
        modal `QFontDialog`, which is three fields and an OK/Cancel to
        get at a single toggle. Each control here commits on its own so
        it is one undo step per change, and the button still commits
        family/size/bold/italic together (matching the old single-button
        behaviour) so picking a family in one dialog is still one step.
        """
        btn = QPushButton()

        def label_text() -> str:
            fam = props.get("font_family", "sans-serif")
            size = props.get("font_size", 10)
            extra = "".join((
                " Bold" if props.get("font_bold") else "",
                " Italic" if props.get("font_italic") else ""))
            return f"{fam}, {size}pt{extra}"
        btn.setText(label_text())
        btn.setToolTip("Pick a font family (and size, bold, italic)")

        def pick():
            from PySide6.QtGui import QFont
            from PySide6.QtWidgets import QFontDialog
            cur = QFont(props.get("font_family", "sans-serif"),
                       int(props.get("font_size", 10)))
            cur.setBold(bool(props.get("font_bold")))
            cur.setItalic(bool(props.get("font_italic")))
            ok, f = QFontDialog.getFont(cur, self, "Font")
            if not ok:
                return
            emit_changes({
                "font_family": f.family(), "font_size": f.pointSize(),
                "font_bold": f.bold(), "font_italic": f.italic()})
            btn.setText(label_text())
            _sync()
        btn.clicked.connect(pick)

        bold = QToolButton()
        bold.setText("B")
        bold.setCheckable(True)
        bold.setFixedWidth(30)
        bold.setToolTip("Bold")
        italic = QToolButton()
        italic.setText("I")
        italic.setCheckable(True)
        italic.setFixedWidth(30)
        italic.setToolTip("Italic")
        size = QSpinBox()
        size.setRange(4, 96)
        size.setSuffix(" pt")
        size.setFixedWidth(64)
        size.setToolTip("Text size in points")

        def _sync():
            bold.blockSignals(True)
            italic.blockSignals(True)
            size.blockSignals(True)
            bold.setChecked(bool(props.get("font_bold")))
            italic.setChecked(bool(props.get("font_italic")))
            size.setValue(int(props.get("font_size", 10)))
            bold.blockSignals(False)
            italic.blockSignals(False)
            size.blockSignals(False)

        def _commit_and_relabel(changes: dict):
            emit_changes(changes)
            btn.setText(label_text())

        bold.toggled.connect(
            lambda on: _commit_and_relabel({"font_bold": on}))
        italic.toggled.connect(
            lambda on: _commit_and_relabel({"font_italic": on}))
        size.valueChanged.connect(
            lambda v: _commit_and_relabel({"font_size": v}))
        _sync()

        row = QHBoxLayout()
        row.setContentsMargins(0, 0, 0, 0)
        row.addWidget(btn)
        row.addSpacing(SPACING["xs"])
        row.addWidget(bold)
        row.addWidget(italic)
        row.addSpacing(SPACING["xs"])
        row.addWidget(size)
        row.addStretch(1)
        row_w = QWidget()
        row_w.setLayout(row)
        form.addRow("Font:", row_w)

    def _populate_node(self, node: RBDNode):
        """Node properties across General/Capacity/Dependency/Advanced,
        matching the real Node Properties dialog."""
        self._begin_populate()
        self._configure_tabs("node")
        self._header.setText(f"Node: {node.node_name}  (ID {node.id})")
        self._current_node_id = node.id
        self._current_block_id = None
        for i in range(self._tabs.count()):
            self._clear_tab(cast(QScrollArea, self._tabs.widget(i)))

        w = self._scroll_general.widget()
        layout = req(w).layout()
        self._clear_tab(self._scroll_general)

        type_names = {1: "Start", 2: "Regular", 3: "End", 4: "Hierarchy In", 5: "Hierarchy Out"}
        gb, fl = self._group("Identity", layout)
        node_id = node.id
        self._name_edit(fl, node.node_name, lambda new_name: self.node_renamed.emit(node_id, new_name))
        self._row(fl, "ID", node.id)
        self._row(fl, "Type", type_names.get(node.type_node, f"Unknown ({node.type_node})"))
        self._row(fl, "Parent ID", node.parent_id)
        self._comment_row(fl, "node", node_id, node.node_name)

        gb, fl = self._group("Position", layout)
        self._position_source = node
        self._position_labels = (
            self._row(fl, "X", f"{node.x_position:.3f}"),
            self._row(fl, "Y", f"{node.y_position:.3f}"))

        gb, fl = self._group("Connections", layout)
        self._row(fl, "Outputs", node.connect_out_of_num)

        gb, fl = self._group("K-out-of-N Logic", layout)
        gb.setToolTip(
            "k-out-of-n: this node needs at least k of its n input paths "
            "working for it to count as up (k=1 is a simple OR, k=n is "
            "a simple AND).")
        # Only Regular (type 2) nodes have a real k-out-of-n threshold;
        # Start/End/Hierarchy nodes store a `k=-1` file-format sentinel
        # that `node_from_row` coerces to `good_paths=1` (see that
        # function's docstring). Editing it here for those types would
        # write back a literal 1, destroying the sentinel. Read-only for
        # anything else.
        if node.type_node == 2:
            node_id = node.id
            self._int_row(fl, "Required good (k)", node.good_paths,
                           lambda v: self.node_good_paths_changed.emit(node_id, v))
        else:
            self._row(fl, "Required good (k)", node.good_paths)
        # `n` is its own NODE_DATA column, distinct from `Out` (which
        # RBDNode.connect_out_of_num maps) and can differ from it.
        # RBDNode doesn't carry `n`, so read the raw row for it.
        raw_row = self._raw_node_row(node.id)
        if raw_row is not None and raw_row.get("n", "") != "":
            self._row(fl, "Total paths (n)", _i_raw(raw_row, "n"))
        else:
            self._row(fl, "Total paths (n)", "(not stored)")

        self._populate_node_capacity(node)
        self._populate_node_standby(node)
        self._populate_node_dependency(node)
        self._populate_node_advanced(node)
        self._restore_tab()

    def _link_rows_for_node(self, node_id: int) -> tuple[list[dict], list[dict]]:
        """(incoming, outgoing) raw LINK_DATA rows touching this node --
        Flow In / Flow Out's own table source. Endpoint TYPE/ID columns
        (FromType/FromID, ToType/ToID) are shared with every other link
        use in the file, not capacity-specific."""
        sec = self._rbd.sections.get("LINK_DATA") if self._rbd else None
        if sec is None:
            return [], []
        into, out_of = [], []
        for row in sec.rows:
            try:
                if row.get("ToType") == "Node" and \
                        int(float(row.get("ToID", -1))) == node_id:
                    into.append(row)
                if row.get("FromType") == "Node" and \
                        int(float(row.get("FromID", -1))) == node_id:
                    out_of.append(row)
            except (TypeError, ValueError):
                continue
        return into, out_of

    def _link_endpoint_name(self, kind: str, oid: int) -> str:
        """Display name for a link endpoint (Block/Node/Hier), by id --
        same raw-row lookup `_raw_node_row` uses for one section, done
        for whichever section this endpoint kind actually lives in."""
        section, col = {"Block": ("BLOCK_DATA", "blockName"),
                        "Node": ("NODE_DATA", "Name"),
                        "Hier": ("HIERARCHY_DATA", "hierarchyName")}.get(
                            kind, (None, None))
        if section is None or self._rbd is None:
            return f"{kind} {oid}"
        sec = self._rbd.sections.get(section)
        if sec is None:
            return f"{kind} {oid}"
        for row in sec.rows:
            try:
                if int(float(row.get("ID", -1))) == oid:
                    return row.get(col or "", "") or f"{kind} {oid}"
            except (TypeError, ValueError):
                continue
        return f"{kind} {oid}"

    @staticmethod
    def _flow_cell_text(raw: str, sentinel_label: str, n_in: int = 1) -> str:
        """nomFlow/MaxFlow store a number or one of two sentinels, which
        Raptor 7's Node dialog shows as keywords:
        -1 is "MaxFlow" (nominal) / "FullFlow" (max) -- all of the flow
        generated at Start -- and -2 is "MaxFlow/N" / "FullFlow/N", an even
        share across the node's N incoming paths. -2 showed as a bare "-2",
        which meant nothing to anyone."""
        try:
            v = int(float(raw))
        except (TypeError, ValueError):
            return str(raw)
        if v == -1:
            return sentinel_label
        if v == -2:
            return f"{sentinel_label}/{n_in}"
        return str(v)

    def _flow_table(self, layout, rows: list[dict], *, incoming: bool):
        headers = (["Link From", "Type", "Nominal Flow", "Max Flow"]
                  if incoming else ["Link To", "Type", "Priority"])
        t = QTableWidget(len(rows), len(headers))
        t.setAccessibleName("Incoming link flows" if incoming
                            else "Outgoing link priorities")
        t.setHorizontalHeaderLabels(headers)
        t.verticalHeader().setVisible(False)
        # Editable value columns (Raptor 7's Node
        # Properties Capacity tab): Nominal Flow + Max Flow on incoming
        # links, Priority (CapPri) on outgoing links -- each commit routed
        # through the view's undoable IdCellCmd. The identity columns
        # stay read-only.
        t.setSelectionMode(QAbstractItemView.SelectionMode.NoSelection)
        value_cols = {2, 3} if incoming else {2}
        for r, row in enumerate(rows):
            kind = row.get("FromType" if incoming else "ToType", "")
            oid = row.get("FromID" if incoming else "ToID", "")
            try:
                oid_i = int(float(oid))
            except (TypeError, ValueError):
                oid_i = 0
            name = self._link_endpoint_name(kind, oid_i)
            cells = [name, kind]
            if incoming:
                cells.append(self._flow_cell_text(
                    row.get("nomFlow", ""), "MaxFlow", len(rows)))
                cells.append(self._flow_cell_text(
                    row.get("MaxFlow", ""), "FullFlow", len(rows)))
            else:
                cells.append(str(row.get("CapPri", "")))
            for c, text in enumerate(cells):
                item = QTableWidgetItem(text)
                if c not in value_cols:
                    item.setFlags(item.flags() & ~Qt.ItemFlag.ItemIsEditable)
                else:
                    n = len(rows)
                    keyword = "MaxFlow" if c == 2 else "FullFlow"
                    flow_tip = (
                        f"{keyword}: all of the flow generated at Start "
                        f"(Project Settings). {keyword}/{n}: an even share "
                        f"across this node's {n} incoming paths. Or a whole "
                        f"number: a fixed flow.")
                    item.setToolTip(
                        flow_tip if incoming else
                        "Capacity priority (CapPri): order this link is "
                        "served when flows compete; 1 first, 99 last.")
                t.setItem(r, c, item)
        t.cellChanged.connect(self._make_flow_commit(t, rows, incoming))
        t.resizeColumnsToContents()
        # Header names themselves must stay readable: content-hug alone
        # can size a narrow numeric column below its own header text
        # ("Nominal Flow" clipped). Width from max(cell content, header),
        # with a floor.
        header = t.horizontalHeader()
        for c, head in enumerate(headers):
            fm = t.fontMetrics()
            min_w = max(fm.horizontalAdvance(head) + 16,
                        t.columnWidth(c), 56)
            if t.columnWidth(c) < min_w:
                t.setColumnWidth(c, min_w)
        # Last section fills the remaining viewport width; otherwise the
        # strip right of the final column paints the table viewport's
        # default, which is black in the light theme.
        header.setStretchLastSection(True)
        t.setMaximumHeight(max(64, 28 * (len(rows) + 1) + 4))
        # insertWidget (not addWidget) -- the tab layout ends with a
        # trailing stretch item (see _group's own convention); addWidget
        # would land AFTER it, pushing the table below everything else
        # including the "Flow generated" note added later.
        layout.insertWidget(layout.count() - 1, t)

    def _make_flow_commit(self, t: QTableWidget, rows: list[dict],
                          incoming: bool):
        """cellChanged handler for an editable flow-assignment table:
        validates the number, routes ONE undoable IdCellCmd per commit
        (LINK_DATA nomFlow/MaxFlow for incoming, CapPri for outgoing),
        and reverts invalid input. `rows` are the LIVE LINK_DATA row
        dicts captured at populate time."""
        cols = (("nomFlow", "MaxFlow") if incoming else ("CapPri",))

        def commit(r: int, c: int):
            if c < 2 or r < 0 or r >= len(rows):
                return
            item = t.item(r, c)
            if item is None:
                return
            text = item.text().strip()
            row = rows[r]
            try:
                link_id = int(float(row.get("ID", "-1")))
            except (TypeError, ValueError):
                return
            col = cols[c - 2]
            if col == "CapPri":
                try:
                    new_v = int(float(text))
                except ValueError:
                    new_v = 0
                if not 1 <= new_v <= 99:
                    self.validation_failed.emit("a priority from 1 to 99")
                    item.setText(str(row.get(col, "")))
                    return
                if row.get(col, "") != str(new_v) and \
                        self.flow_cell_changed is not None:
                    self.flow_cell_changed.emit(link_id, col,
                                                row.get(col, ""), str(new_v))
                return
            label = "MaxFlow" if col == "nomFlow" else "FullFlow"
            # Each column has its own keyword ("MaxFlow" / "FullFlow"); they
            # share the stored sentinels, but accepting the wrong one made
            # the cell re-render with this column's spelling, which read as
            # "it picked the other one".
            new_v, problem = parse_flow_cell(text, label, len(rows))
            if problem is not None:
                self.validation_failed.emit(problem)
                item.setText(self._flow_cell_text(
                    row.get(col, ""), label, len(rows)))
                return
            new = str(new_v)
            if row.get(col, "") == new:
                return
            if self.flow_cell_changed is not None:
                self.flow_cell_changed.emit(link_id, col,
                                            row.get(col, ""), new)
        return commit

    def _populate_node_capacity(self, node: RBDNode):
        """Capacity tab (slot 1, reused from the block "Failure" slot):
        the two flow-routing checkboxes and the link tables the real Node
        Properties Capacity tab shows once a checkbox is unchecked. The
        identity columns are read-only; the flow/priority value columns
        are editable.
        """
        w = self._scroll_failure.widget()
        layout = req(w).layout()
        self._clear_tab(self._scroll_failure)
        node_id = node.id
        into, out_of = self._link_rows_for_node(node_id)

        _gb, fl = self._group("Flow In", layout)
        unrestricted = QCheckBox("Unrestricted flow")
        unrestricted.setChecked(bool(node.full_flow))
        unrestricted.toggled.connect(
            lambda on: self.node_full_flow_changed.emit(node_id, on))
        fl.addRow(unrestricted)
        if not node.full_flow:
            self._flow_table(layout, into, incoming=True)

        _gb, fl = self._group("Flow Out", layout)
        any_path = QCheckBox("Use any available path")
        any_path.setChecked(bool(node.any_path))
        any_path.toggled.connect(
            lambda on: self.node_any_path_changed.emit(node_id, on))
        fl.addRow(any_path)
        if not node.any_path:
            self._flow_table(layout, out_of, incoming=False)

        # System-wide setting (Options/System Settings,
        # "Setting the Flow Generation...") -- echoed read-only
        # here, same as Raptor 7's own Node Properties Capacity tab,
        # so a user configuring flow routing can see it without leaving
        # this dialog. Not a per-node value despite living on this tab.
        sec = self._rbd.sections.get("SYSTEM_DATA") if self._rbd else None
        if sec and sec.rows:
            cfg = system_config_from_row(sec.rows[0])
            note = QLabel(f"Flow generated = {cfg.flow_generated}")
            _vbox(layout).insertWidget(req(layout).count() - 1, note)

    def _populate_node_standby(self, node: RBDNode):
        """Standby tab (slot 2, reused from the block "Repair" slot):
        the three checkboxes and k* value from the real Node Properties
        Standby tab. The underlying fields
        (Cold/PR/Autos/k* columns) are read by `typed.py`."""
        w = self._scroll_repair.widget()
        layout = req(w).layout()
        self._clear_tab(self._scroll_repair)
        node_id = node.id

        gb, fl = self._group("Switchover", layout)
        cold_cb = QCheckBox("Engage standby logic for this node")
        cold_cb.setChecked(bool(node.cold_standby))
        cold_cb.setToolTip(
            "The first checkbox, which engages the remainder of the tab.")
        cold_cb.toggled.connect(
            lambda on: self.node_cold_standby_changed.emit(node_id, on))
        fl.addRow(cold_cb)

        pr_cb = QCheckBox("Return alternates to standby once the "
                          "primary is repaired")
        pr_cb.setChecked(bool(node.priority_return))
        pr_cb.setEnabled(bool(node.cold_standby))
        pr_cb.setToolTip(
            "If unchecked, an alternate that becomes a primary stays "
            "a primary until it itself fails.")
        pr_cb.toggled.connect(
            lambda on: self.node_priority_return_changed.emit(
                node_id, on))
        fl.addRow(pr_cb)

        autos_cb = QCheckBox("Try automatic switchover before manual")
        autos_cb.setChecked(bool(node.check_autos_first))
        autos_cb.setEnabled(bool(node.cold_standby))
        autos_cb.toggled.connect(
            lambda on: self.node_check_autos_first_changed.emit(
                node_id, on))
        fl.addRow(autos_cb)

        gb, fl = self._group("k* Target", layout)
        gb.setToolTip(
            "The number of paths a user would DESIRE operating at any "
            "time (>= k, <= n) -- distinct from k, the minimum "
            "required. Default equals k.")
        # k* is upper-bounded by n: `n` is its own raw NODE_DATA column
        # (see "Total paths (n)" above), not
        # `RBDNode.connect_out_of_num`/"Out", which can differ. Clamp
        # only when the file actually stores one.
        raw_row = self._raw_node_row(node.id)
        n_max = None
        if raw_row is not None and raw_row.get("n", "") != "":
            n_max = _i_raw(raw_row, "n")
        # Lower bound = k (original rule: k* >= k), upper = n when the
        # file stores one.
        min_k = max(0, int(node.good_paths))
        self._int_row(fl, "k*", node.k_star,
                      lambda v: self.node_k_star_changed.emit(node_id, v),
                      minimum=min_k, maximum=n_max)

        # Per-incoming-link switchover table:
        # Priority / Auto Prob / Auto Time / Man Time. The columns exist
        # in LINK_DATA and the engine consumes them.
        gb, fl = self._group("Incoming link switchover", layout)
        gb.setToolTip(
            "Per incoming link: cold-standby switchover priority, "
            "automatic switchover probability and time, and the manual "
            "switchover time.")
        into, _out = self._link_rows_for_node(node.id)
        self._standby_table(layout, into)

    def _standby_table(self, layout, rows: list[dict]):
        headers = ["Link From", "Priority", "Auto Prob", "Auto Time",
                   "Manual Time"]
        t = QTableWidget(len(rows), len(headers))
        t.setAccessibleName("Standby switchover by incoming link")
        t.setHorizontalHeaderLabels(headers)
        t.verticalHeader().setVisible(False)
        t.setSelectionMode(QAbstractItemView.SelectionMode.NoSelection)
        for r, row in enumerate(rows):
            try:
                oid_i = int(float(row.get("FromID", "0")))
            except (TypeError, ValueError):
                oid_i = 0
            name = self._link_endpoint_name(row.get("FromType", ""), oid_i)
            cells = [name, str(row.get("ColdPri", "")),
                     f"{_row_float(row.get('AProb', '')):g}",
                     f"{_row_float(row.get('AutoTime', '')):g}",
                     str(row.get("ManTime", "")) or "N/A"]
            for c, text in enumerate(cells):
                item = QTableWidgetItem(text)
                if c == 0:
                    item.setFlags(item.flags() & ~Qt.ItemFlag.ItemIsEditable)
                t.setItem(r, c, item)
        t.cellChanged.connect(self._make_standby_commit(t, rows))
        t.resizeColumnsToContents()
        header = t.horizontalHeader()
        for c, head in enumerate(headers):
            fm = t.fontMetrics()
            min_w = max(fm.horizontalAdvance(head) + 16,
                        t.columnWidth(c), 56)
            if t.columnWidth(c) < min_w:
                t.setColumnWidth(c, min_w)
        header.setStretchLastSection(True)
        t.setMaximumHeight(max(64, 28 * (len(rows) + 1) + 4))
        layout.insertWidget(layout.count() - 1, t)
        if not rows:
            note = QLabel("No incoming links")
            layout.insertWidget(layout.count() - 1, note)

    def _make_standby_commit(self, t: QTableWidget, rows: list[dict]):
        cols = ("ColdPri", "AProb", "AutoTime", "ManTime")

        def _reset(r: int, c: int, col: str):
            row = rows[r]
            it = t.item(r, c)
            if it is None:
                return
            if col == "ManTime":
                it.setText(str(row.get(col, "")) or "N/A")
            elif col in ("AProb", "AutoTime"):
                it.setText(f"{_row_float(row.get(col, '')):g}")
            else:
                it.setText(str(row.get(col, "")))

        def commit(r: int, c: int):
            if c < 1 or r < 0 or r >= len(rows):
                return
            item = t.item(r, c)
            if item is None:
                return
            row = rows[r]
            try:
                link_id = int(float(row.get("ID", "-1")))
            except (TypeError, ValueError):
                return
            col = cols[c - 1]
            old = row.get(col, "")
            text = item.text().strip()
            if col == "ManTime" and text.upper() in ("N/A", "NA", "NONE"):
                new = "N/A"
            elif col == "ColdPri":
                try:
                    v = int(float(text))
                except ValueError:
                    self.validation_failed.emit("not a number")
                    _reset(r, c, col)
                    return
                if not (1 <= v <= 99):
                    self.validation_failed.emit(
                        "Priority must be between 1 and 99")
                    _reset(r, c, col)
                    return
                new = str(v)
            else:
                try:
                    fv = float(text)
                except ValueError:
                    self.validation_failed.emit("not a number")
                    _reset(r, c, col)
                    return
                if col == "AProb" and not (0.0 < fv <= 1.0):
                    self.validation_failed.emit(
                        "Auto switch probability must be greater than 0 "
                        "and at most 1")
                    _reset(r, c, col)
                    return
                if col in ("AutoTime", "ManTime") and \
                        not (0.0 <= fv <= 999999999.0):
                    self.validation_failed.emit(
                        f"{col} must be between 0 and 999,999,999")
                    _reset(r, c, col)
                    return
                new = f"{fv:g}"
            if new == old:
                return
            self.link_standby_cell_changed.emit(link_id, col, old, new)
        return commit

    def _populate_node_dependency(self, node: RBDNode):
        """Dependency tab (slot 5, reused from the block "Deps" slot):
        independent / locally dependent / system dependent / dependent-
        on-a-block, matching the real Node Properties Dependency tab.
        `node.dependency_num`/`dep_type` ARE read by the engine (e.g.
        `raptor.core.engine.dependency.resolve_dependency_node_id`,
        `cold_standby.py`'s own back-flow checks), unlike a hierarchy's
        -- see `show_hier`'s docstring for why that one stays read-only/
        absent."""
        w = self._scroll_deps.widget()
        layout = req(w).layout()
        self._clear_tab(self._scroll_deps)
        self._dependency_group(layout, node.id, node.dependency_num,
                               node.dep_type)

    def _populate_node_advanced(self, node: RBDNode):
        """Advanced tab (slot 6, reused from the block "Costs" slot):
        phasing, matching the real Node Properties Advanced tab's
        "changes properties during phasing" checkbox, with the per-phase
        K/Cut/Linked editor as the fuller version of that control. Also
        the Raptor 7 dialog's other Advanced checkbox, "Include node analysis
        information..." (`repNA`/`report_node_anal`)."""
        w = self._scroll_costs.widget()
        layout = req(w).layout()
        self._clear_tab(self._scroll_costs)
        node_id = node.id
        _gb, fl = self._group("Reporting", layout)
        report_cb = QCheckBox(
            "Include node analysis information for this node in the "
            "output tables and reports")
        report_cb.setChecked(bool(node.report_node_anal))
        report_cb.toggled.connect(
            lambda on: self.node_report_anal_changed.emit(node_id, on))
        fl.addRow(report_cb)
        if node.type_node == 2:
            entries = []
            for v in (node.phase or []):
                if v == -1:
                    entries.append(("C", float(v)))
                elif v == 0:
                    entries.append(("L", 0.0))
                else:
                    entries.append(("K", float(v)))
            self._phasing_group(layout, "node", node.id,
                                node.uses_phasing, entries)
        else:
            _gb, fl = self._group("Phasing", layout)
            self._row(fl, "Status",
                      "Not applicable to this node type")

    def _dependency_group(self, layout: QLayout | None, oid: int,
                          dependency_num: int, dep_type: str,
                          emit=None):
        """Shared independent/locally-dependent/system-dependent/
        dependent-on-a-block master-element selector (Node/Block
        Properties Dependency tab -- not Hierarchy, see `show_hier`'s
        docstring for why that one stays read-only/absent). Semantics per
        `raptor.core.engine.dependency.resolve_dependency_node_id` and
        `cold_standby.py`: 0=independent, -1=locally dependent,
        -2=system dependent, >0 with dep_type=="RBDBlock"=dependent on
        that block id.

        `emit`: callable(oid, dependency_num, dep_type), defaults to
        `self.node_dep_changed.emit`. Blocks pass
        `self.block_dep_master_changed.emit`, routing the output to the
        right element kind's own signal instead of always Node's."""
        layout = _vbox(layout)
        emit = emit or self.node_dep_changed.emit
        gb, fl = self._group("Dependency", layout)
        r_indep = QRadioButton("Independent")
        r_local = QRadioButton("Locally dependent")
        r_sys = QRadioButton("System dependent")
        r_on = QRadioButton("Dependent on:")
        # Tooltips for the four dependency choices; wording follows the
        # dependency.py engine semantics cited above, plus the manual's
        # dependency chapter.
        r_indep.setToolTip(
            "Independent: answers to nothing but its own clocks. No "
            "other element's state can idle, hold or trigger it.")
        r_local.setToolTip(
            "Locally dependent: tied to this element's own downstream "
            "node. When that node's path goes down, this element is "
            "pulled into Idle (paused, failure clock stops) and resumes "
            "when the path is up again.")
        r_sys.setToolTip(
            "System dependent: tied to the whole model's up/down "
            "status (across every hierarchy). Down system = Idle here; "
            "up system = resume.")
        r_on.setToolTip(
            "Dependent on one named element (master): when THAT element "
            "goes down this element idles -- not failed, just paused, "
            "its own failure clock stops -- and it picks up where it "
            "left off when the master comes back.")
        group = QButtonGroup(gb)
        for r in (r_indep, r_local, r_sys, r_on):
            group.addButton(r)
            fl.addRow(r)

        combo = QComboBox()
        blocks: list[tuple[int, str]] = []
        sec = self._rbd.sections.get("BLOCK_DATA") if self._rbd else None
        if sec is not None:
            for row in sec.rows:
                try:
                    bid = int(float(row.get("ID", "-1")))
                except (ValueError, KeyError):
                    continue
                name = row.get("blockName", "")
                # Whitespace-delimited rows can carry blank names; an
                # unnamed master is invalid input, so skip those entries.
                if not name or not name.strip():
                    continue
                blocks.append((bid, name))
        blocks.sort()

        def fill_combo() -> None:
            """Populate the master list on first use.

            Building ~3,500 QComboBox items is ~20-70 ms and it happened
            for every node/block selection, even though the combo is
            disabled unless "Dependent on:" is chosen (or the element
            already is). Lazy, so an independent element never pays it.
            """
            if combo.count():
                return
            for bid, name in blocks:
                combo.addItem(f"{name} block - {bid:03d}", bid)

        # A saved dependency needs its current entry present immediately
        # (findData below), so only the common independent case is lazy.
        if dependency_num > 0:
            fill_combo()
        fl.addRow("", combo)

        def populate() -> None:
            if dependency_num == -1:
                r_local.setChecked(True)
            elif dependency_num == -2:
                r_sys.setChecked(True)
            elif dependency_num > 0:
                r_on.setChecked(True)
                ci = combo.findData(dependency_num)
                if ci >= 0:
                    combo.setCurrentIndex(ci)
            else:
                r_indep.setChecked(True)
            combo.setEnabled(r_on.isChecked() and combo.count() > 0)

        def send() -> None:
            if r_on.isChecked():
                fill_combo()
            combo.setEnabled(r_on.isChecked() and combo.count() > 0)
            if r_indep.isChecked():
                emit(oid, 0, "")
            elif r_local.isChecked():
                emit(oid, -1, "")
            elif r_sys.isChecked():
                emit(oid, -2, "")
            elif r_on.isChecked() and combo.count():
                emit(oid, combo.currentData(), "RBDBlock")

        populate()
        for r in (r_indep, r_local, r_sys, r_on):
            r.toggled.connect(lambda checked: send() if checked else None)
        combo.activated.connect(
            lambda _i: send() if r_on.isChecked() else None)
        if not blocks:
            r_on.setEnabled(False)
            r_on.setToolTip("No blocks in this model")

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
        units_edit.lineEdit().editingFinished.connect(commit_units)
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
        self._int_row(
            fl, "Flow generated", int(cfg.flow_generated),
            lambda v: self.system_config_changed.emit(
                {"flowGenerated": str(v)}), minimum=0)
        neg_cb = QCheckBox("Ignore negative-value warning")
        neg_cb.setAccessibleName("Ignore negative-value warning")
        neg_cb.setChecked(bool(cfg.show_neg_err_msg))
        neg_cb.toggled.connect(
            lambda on: self.system_config_changed.emit(
                {"ShowNegErrMsg": "1" if on else "0"}))
        fl.addRow(neg_cb)

        _gb, fl = self._group("System Costs", layout)
        self._float_row(
            fl, "System red cost", cfg.system_red_cost,
            lambda v: self.system_config_changed.emit(
                {"systemRedCost": f"{v:.6f}"}))
        self._float_row(
            fl, "Cost of lost flow", cfg.lost_flow_unit_cost,
            lambda v: self.system_config_changed.emit(
                {"sysLostCost": f"{v:.6f}"}))

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
