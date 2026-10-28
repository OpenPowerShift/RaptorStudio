"""Plain data model for Raptor 7's RBD objects.

Ported from the OBJECT type definitions in Raptor 7.
Deliberately behavior-free: every class here is a plain dataclass mirroring
the Modsim `OBJECT`'s fields only. The corresponding `ASK METHOD`/
`TELL METHOD` bodies (state machines, simulation logic) live in
`raptor.core.engine`.

Field naming matches the Modsim source field names (not idiomatic Python
naming) on purpose: it keeps this file directly diffable against Raptor 7 while porting, and against the `.rbd` file format's own column
headers, which use the same names.

Numbering/typing notes:
    - Modsim `STRING` -> `str`
    - Modsim `INTEGER`/`CARDINAL` -> `int`
    - Modsim `REAL` -> `float`
    - Modsim `BOOLEAN` -> `bool`
    - Modsim `intArray`/`realArray`/`strArray`/`boolArray` (all declared as
      `ARRAY INTEGER OF ...`, i.e. dynamically-sized,
      1-indexed in the source) -> plain Python `list`, 0-indexed. Any code
      translating between a `.rbd` file's 1-indexed columns and these lists
      must do the index shift explicitly at the boundary (file I/O layer),
      not here.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from collections.abc import Callable


# --- enums (ported from Raptor 7's TYPE section) -------------

class Direction(Enum):
    INTO = "INTO"
    OUTOF = "OUTOF"
    MINUSINTO = "MINUSINTO"
    MINUSOUTOF = "MINUSOUTOF"


class BlockStatus(Enum):
    RUNNING = "Running"
    REPAIRING = "Repairing"
    DONE = "Done"
    IDLE = "Idle"
    STANDBY = "Standby"
    WORKSPACE = "Workspace"
    PM = "PM"
    PM_HOLD = "PMhold"
    REP_HOLD = "RepHold"


class EventStatus(Enum):
    ARMED = "Armed"
    SUCCESS = "Success"
    FAILURE = "Failure"


class NodeStatus(Enum):
    ALL_UP = "AllUp"
    STANDBY = "NodeStandby"
    DEGRADED = "Degraded"
    IDLE = "NodeIdle"
    PM = "NodePM"
    DOWN = "Down"


class LinkStatus(Enum):
    UP = "LinkUp"
    DOWN = "LinkDown"
    IDLE = "LinkIdle"
    STANDBY = "LinkStandby"
    PM = "LinkPM"
    DONE = "LinkDone"


class ActiveStatus(Enum):
    ACTIVE = "Active"
    CUT = "Cut"
    LINKED = "Linked"


class SparingType(Enum):
    INFINITE = "Infinite"
    CUSTOM = "Custom"
    SPARE_POOL = "SparePool"
    COLD_POOL = "ColdPool"
    RESOURCE = "Resource"
    NONE = "None"


# --- support objects ------------------------------------------------------

@dataclass
class SparePool:
    """Port of `SparePoolObj`."""

    pool_name: str = ""
    sparing_type: SparingType = SparingType.NONE
    initial_spares: int = 0
    new_spares: int = 0
    slo_order_level: int = 0
    slo_new_spares: int = 0
    new_spares_arrival: float = 0.0
    emergency_time: float = 0.0
    fixed_per_use: float = 0.0
    spare_cost: float = 0.0
    cost_per_time: float = 0.0
    slo_time: float = 0.0
    emergency_shipping_cost: float = 0.0
    uses_phasing: bool = False
    stock_level_ordering: bool = False
    routine_spare_ordering: bool = False
    emergency_spare_ordering: bool = False
    phase: list[int] = field(default_factory=list)


@dataclass
class RBDTrigger:
    """Port of `RapTriggerObj` -- a self-
    contained periodic timer.

    Not directly tied to any block/node in this project's own ported scope:
    it is only consumed by triggered-mode PM (`RBDBlock.pm_triggered`,
    which stays unported and raises), while `triggers.trigger_process`
    otherwise fires and logs it."""

    trigger_name: str = ""
    repeats: bool = False
    init_used: float = 0.0
    dist: int = 19
    params: list[float] = field(default_factory=list)


@dataclass
class Phase:
    """Port of `PhaseObj`."""

    phase_name: str = ""
    id: int = 0
    dist: int = 0
    params: list[float] = field(default_factory=list)
    mission: bool = False


@dataclass
class SystemConfig:
    """Port of the `.rbd` file's `SYSTEM_DATA` row (top-level simulation
    settings — not a Modsim `OBJECT` like the others here,
    but the module-level `VAR`s in / that get
    persisted per-file: `NumRuns`, `sysStreams`, etc.)."""

    image_string: str = ""
    termination_type: str = "FailSim"
    term_condition: float = 0.0
    start_stats: float = 0.0
    num_runs: int = 1
    graph_time_slice: float = 0.0
    graph_ymin: float = 0.0
    graph_ymax: float = 1.0
    zoom_val: float = 24.0
    units: str = "hours"
    graphics_use: str = "GraphicsOff"
    #: 11 entries: streams A-J then Ancillary (indices 0-10 here map to
    #: Modsim's `sysStreams[1..11]`). Values <70 mean "own dedicated stream,
    #: seeded via FetchRaptorSeed(value)"; >=200 in a block/event's
    #: FStream/RStream means "use sys_streams[value-200]" (see `GetStream`
    #: in Raptor 7).
    sys_streams: list[int] = field(default_factory=lambda: [0] * 11)
    x_origin: float = 0.0
    y_origin: float = 0.0
    #: `ShowNegErrMsg` in `SYSTEM_DATA`, which Raptor 7 reads as `negShutUp`: 1 means IGNORE the
    #: negative-time warning (its dialog says "Ignore negative value warning"). The name is the file's.
    show_neg_err_msg: bool = False
    ignore_start_fail: bool = False
    #: `flowGenerated` -- an INTEGER in the
    #: engine (units of flow per time unit): `RunEFPA` divides it
    #: (`CEIL(FLOAT(flowGenerated)/...)`, Raptor 7) and
    #: `AnalyzeSystem`'s capacity half distributes exactly this amount
    #: the `.rbd` column is written with
    #: `WriteInt`.
    flow_generated: int = 0
    system_red_cost: float = 0.0
    lost_flow_unit_cost: float = 0.0
    weak_analysis: bool = False
    cost_analysis: bool = False
    capacity_analysis: bool = False
    node_anal_type: str = "Availability"
    gy_threshold: float = 0.95
    yr_threshold: float = 0.90



# --- RBD graph objects -----------------------------------------------------

@dataclass
class RBDBasic:
    """Port of `RBDBasicObj` — common fields shared by every canvas object
    (blocks, nodes, events, hierarchies all inherit this in the source)."""

    name: str = ""
    id: int = 0
    parent_id: int = 0
    comment: str = ""
    active_status: ActiveStatus = ActiveStatus.ACTIVE
    connect_into_num: int = 0
    connect_out_of_num: int = 0
    good_paths_required: int = 0
    min_good_paths_required: int = 0
    num_good_paths: int = 0
    dependency_num: int = 0
    resolved_in_link_count: int = 0
    x_position: float = 0.0
    y_position: float = 0.0
    state_start_time: float = 0.0
    selected: bool = False
    uses_phasing: bool = False
    has_dep_objects: bool = False
    cold_dep: bool = False
    ignore_dep: bool = False
    dep_type: str = ""
    # Ao/Do/R are display-formatted result strings in the source
    # (computed post-analysis), not simulation inputs — kept as str to
    # match; the actual numeric values live in the analysis/reporting layer.
    ao: str = ""
    do: str = ""
    r: str = ""


@dataclass
class RBDHier(RBDBasic):
    """Port of `RBDHierObj`."""

    is_connected_node: bool = False
    in_id: int = 0
    out_id: int = 0
    level: int = 0
    num_currently_failed: int = 0
    #: `hierDE[hier.seqNum]`: the number of
    #: times this hierarchy's Out node (typeNode=5) has LEFT a `Down` or
    #: `NodePM` state -- the denominator of the weak-link
    #: `HIERARCHY_RESULTS` `MTBDE`/`MDT` columns. Reset by
    #: `ResetAllStatistics`.
    down_event_count: int = 0
    dsi_blues: int = 0
    dsi_yellows: int = 0
    dsi_siennas: int = 0
    dsi_oranges: int = 0
    dsi_reds: int = 0
    old_id: int = 0
    my_depth: int = 0
    status: NodeStatus = NodeStatus.ALL_UP
    dsi_state: NodeStatus = NodeStatus.ALL_UP
    dsi_signal: NodeStatus = NodeStatus.ALL_UP
    zoom: float = 1.0
    x_center: float = 0.0
    y_center: float = 0.0
    x_origin: float = 0.0
    y_origin: float = 0.0


@dataclass
class BlockRunState:
    """What one run changes on an ordinary block, as opposed to what the
    `.rbd` file configures.

    The driver gives every block a fresh `BlockRunState` at the start of each
    run (`RBDBlock.run_state`), so nothing here leaks from one run into the next.
    Most are the flags the block's own process and the cascade use to
    coordinate: where the process is waiting (and so whether a dependency
    change may wake it), and what the last interrupt said. The rest are the
    run's counters and the block's current stress.
    """

    #: `FevDepMode`: in Failure Effects View, this block's
    #: state is an INDUCED effect of a dependency (drawn hatched; clicking it
    #: cycles its alternative outcomes). Always False in an ordinary run.
    fev_induced_by_dependency: bool = False
    interrupt_reason: str = ""
    previous_dependency_state: str = ""
    returning_from_maintenance: bool = False
    time_zero_action: str = ""
    #: True while `Idle` specifically because a `Repair`/`PerformPM`
    #: completion (`block_lifecycle._apply_maintenance_completed`, source's
    #: `DetermineNewState("Maintenance Completed")`) decided NOT Running.
    #: In source, `Repair`/`PerformPM` are both separate `TELL METHOD`s from
    #: `Run`, so once one of them completes
    #: into this Idle state, `Run`'s own process -- the one with the `WHILE
    #: opStatus=Idle: ZeroWait:=TRUE; WAIT FOR BlockTrigger[seqNum] TO Fire`
    #: loop `ControlPMtriggers` relies on to deliver `"Triggered_PM_Due"` --
    #: has already returned, so there is no live process left to wake. This
    #: port's `block_run_process` collapses `Run`/`Repair`/`PerformPM` into
    #: ONE generator that never truly terminates, so it always has a
    #: live-looking `while op_status == IDLE` wait ready regardless; this
    #: flag reproduces the Raptor 7 engine's "nothing is listening" gap instead.
    #: Cleared centrally in `blocks.change_block_state` whenever
    #: `new_op_status==RUNNING` (Run is live/re-engaged again for every
    #: transition path), rather than narrowly mirrored at each RUNNING call
    #: site, so it cannot go stale across a later, genuinely-live Idle
    #: episode.
    run_needs_respawn: bool = False
    #: set while a `maintDefer` block enters RepHold ("deferred maintenance"): that `ChangeBlockState` is not a
    #: `CheckColds` site (goes straight to `WAIT FOR trig TO Fire`)
    no_cold_check: bool = False
    rep_wait: bool = False
    #: `True` only while this block's own process is genuinely suspended at
    #: its ordinary `WAIT DURATION TimeToMaint` fail/PM-timer wait
    #: (Raptor 7's own `FailWait`), `False` everywhere else
    #: (including mid-`ObtainPMprereqs`/PM-obtain, mid-repair, mid-LDT,
    #: mid-resource-wait -- ANY other suspension point). `orchestrator.
    #: _Cascade._induce_dependency` gates interrupt delivery on
    #: `fail_wait or zero_wait`, matching source's own `IF FailWait:
    #: Interrupt(...) ELSIF ZeroWait: ...Release END IF` dispatch pattern
    #: (repeated at several call sites, e.g. `BackFlowDependencies`'s own
    #: "InducedFailure" branch, Raptor 7) having NO `ELSE`
    #: -- a block mid-ANY-other-wait gets its own bookkeeping fields
    #: (`interrupt_reason`/`returning_from_maintenance`) updated but is never
    #: actually woken.
    fail_wait: bool = False
    #: Sibling to `fail_wait`: `True` only while genuinely
    #: suspended at one of the TWO `WAIT FOR BlockTrigger[seqNum] TO Fire`
    #: sites (Raptor 7's own Idle-wait's own
    #: `CombinedStress<=0.0` wait -- `block_lifecycle.block_run_process`'s
    #: own Idle-wait `while` loop and its `stress<=0.0` sibling).
    zero_wait: bool = False
    defer_pm: bool = False
    #: Order this block joined source's `pmDeferGroup` (`ASK pmDeferGroup TO Add(SELF)`): the phase change's
    #: release loop walks the group in insertion order, not block-id order.
    pm_defer_order: int = 0
    #: Port of source's own `WaitingForPrereqs`, `True`
    #: only while this block's own `block_run_process` generator is
    #: genuinely suspended at one of the two `ObtainPMprereqs` TELL-
    #: boundary zero-length yields (`block_lifecycle.py`). Used by
    #: `orchestrator._Cascade._induce_dependency` as a delivery gate
    #: (sibling to `fail_wait`/`zero_wait`): `ObtainPMprereqs` is a
    #: separate, independently-scheduled process in source, immune to an
    #: `Interrupt(tempBlock,"Run")` the whole time it runs (even though
    #: `op_status` stays `RUNNING` throughout, since only `PerformPM`
    #: finally calls `ChangeBlockState(PM,...)`).
    waiting_for_pm_prerequisites: bool = False
    #: Mirrors `block_lifecycle.py`'s own local `pm_resource_pending`
    #: generator variable so `orchestrator.py`'s `apply()` can also see
    #: it. A local-only version cannot survive a `Pool_Available` interrupt
    #: getting staled out by a later same-instant interrupt for the same
    #: block handle (see `ResourceQueue.is_waiting`'s own docstring in
    #: `pool.py`); this field lets the three deferred-PM pickup sites
    #: detect the grant via direct queue membership instead of relying on
    #: interrupt-reason survival.
    pm_resource_pending: bool = False
    spare_obtained: bool = False
    #: `cold_standby._switch_failed` permanently LinkDone'd this block's
    #: cold-standby link; `block_run_process` parks on it (reset per run).
    cold_path_done: bool = False
    #: Set around a dependency state change that must not also wake the
    #: block's `Run` (`dependency.back_flow_dependencies*`).
    no_wake_once: bool = False
    #: `Run` is between its PerformPM TELL and that TELL's start, so a
    #: dependency wake must be treated as landing on the TELL (literal Give).
    pm_tell_pending: bool = False
    #: Count of `InterruptReason` overwrites by the dependency dispatch; the
    #: block's own process compares before/after values across a wait.
    reason_overwrites: int = 0
    #: Handle id of the placeholder scheduler slot for a `BlockTrigger.Release`
    #: record, retired when the block's own continuation takes its place.
    trigger_slot_handle: int | None = None
    #: Live view of whether a `pmReqDefer` PM-prerequisite request is still
    #: outstanding (`WaitingForPrereqs`), installed by `block_run_process`.
    pm_prerequisites_deferred: Callable[[], bool] | None = None

    #: `PhaseStress` (Raptor 7's own `combined_stress`
    #: formula target) -- source stores this as a real per-block OBJECT
    #: FIELD, last set whenever the block's own `Run` loop most recently
    #: computed it, NOT recomputed on every reference. `BlockProcess`
    #: updates this every time it calls `combined_stress`; a caller elsewhere in the SAME instant (a phase-change's
    #: own dependency-triggered recovery, `orchestrator._dispatch_
    #: dependency`) reads whatever this was last set to -- e.g. a
    #: "Dependence" recovery line firing in the same instant a phase change
    #: already advanced `current_phase` still shows the OLD phase's stress,
    #: because the block's own `Run` loop hasn't re-entered and recomputed
    #: it yet.
    phase_stress: float = 1.0
    #: `SparesUsed[seqNum]` (a Raptor 7 `VAR`, incremented at ~15 spare-
    #: draw sites in Raptor 7 -- RSO/ESO/Custom/SLO consumption,
    #: all implemented in `block_lifecycle.py`/`pool.py`). Reset to 0 each
    #: run. Feeds the `Spare_Utilization`/
    #: `Spares_Utilization` report tables.
    spares_used: int = 0
    #: `FailureCounter`: the block's failures this run. Also drives the
    #: growth/degradation stress.
    failure_counter: int = 0
    #: `failure_counter` when the statistics were last reset
    #: (`ResetAllStatistics`); the report counts only the failures after it.
    discarded_failures: int = 0


@dataclass
class RBDBlock(RBDBasic):
    """Port of `RBDBlockObj` — by far the largest object in the source
    (~145 lines just for this one's field list)."""

    # Modsim enums default to their first declared literal — Raptor 7:
    # `BlockStatusType=(Running,Repairing,Done,Idle,Standby,Workspace,PM,
    # PMhold,RepHold)` — so a freshly-constructed block's `opStatus` is
    # `Running`, not `Idle`. ChangeBlockState's very first call therefore
    # sees `oldOpStatus=Running`: a Running->Running "Initialize"
    # transition produces RunChange=0, no cascade.
    op_status: BlockStatus = BlockStatus.RUNNING
    pool_name: str = ""
    resource_pool_name: str = ""
    start_condition: str = ""
    pm_trig: str = ""

    fail_distro: int = 19  # default Fixed
    num_fail_params: int = 0
    fail_stream: int = 0
    repair_distro: int = 19
    num_repair_params: int = 0
    repair_stream: int = 0
    init_stock: int = 0
    new_spares: int = 0
    downstream_node_id: int = 0
    downstream_link_id: int = 0
    slo_order_level: int = 0
    slo_new_spares: int = 0
    gd_type: int = 0
    sim_start_type: int = 0
    resource_type_count: int = 0
    repair_resources_needed: int = 0
    pm_resources_needed: int = 0
    pm_dist: int = 19
    pre_dist: int = 19
    post_dist: int = 19

    sparing_type: SparingType = SparingType.NONE

    fail_mean: float = 0.0
    repair_mean: float = 0.0
    fail_stdev: float = 0.0
    emer_time: float = 0.0
    initial_cost: float = 0.0
    operating_cost: float = 0.0
    pm_cost: float = 0.0
    pm_hold_cost: float = 0.0
    rep_hold_cost: float = 0.0
    repairing_cost: float = 0.0
    spare_cost: float = 0.0
    idle_cost: float = 0.0
    standby_cost: float = 0.0
    done_cost: float = 0.0
    rep_fixed_cost: float = 0.0
    done_fixed_cost: float = 0.0
    slo_time: float = 0.0
    gd_rate: float = 0.0
    gd_limit: float = 0.0
    emergency_shipping_cost: float = 0.0
    amount_exhausted: float = 0.0
    start_val: float = 0.0
    first_pre_ldt: float = 0.0
    sb_stress: float = 0.0
    pm_stagger: float = 0.0
    pm_freq: float = 0.0
    pm_fixed_cost: float = 0.0
    dep_nothing_percent: float = 0.0
    dep_idle_percent: float = 0.0
    dep_pm_percent: float = 0.0
    dep_fail_percent: float = 0.0
    dep_pm_threshold: float = 0.0
    new_spares_arrival: float = 0.0  # `.rbd` BLOCK_DATA column "ArriveEvery"

    is_connected_node: bool = False
    allow_reconfigure_system: bool = False
    stock_level_ordering: bool = False
    routine_spare_ordering: bool = False
    emergency_spare_ordering: bool = False
    always_add_done_cost: bool = False
    uses_pm: bool = False
    pm_spare_needed: bool = False
    pm_refresh: bool = False
    pm_mis_defer: bool = False
    pm_fail_reset: bool = False
    pm_triggered: bool = False
    pm_req_defer: bool = False
    def_dep_state_idle: bool = False

    fail_values: list[float] = field(default_factory=list)
    repair_values: list[float] = field(default_factory=list)
    pm_params: list[float] = field(default_factory=list)
    pre_params: list[float] = field(default_factory=list)
    post_params: list[float] = field(default_factory=list)
    phase_value: list[float] = field(default_factory=list)
    phase_type: list[str] = field(default_factory=list)

    #: RESOURCE_DATA pool's `costPerTime`/`fixedPerUse` for this block's
    #: `resource_pool_name`, resolved once by `orchestrator.load_model`.
    res_cost_per_time: float = 0.0
    res_fixed_per_use: float = 0.0

    #: This run's state (`BlockRunState`); the driver replaces it at the
    #: start of every run.
    run_state: BlockRunState = field(default_factory=BlockRunState, repr=False, compare=False)


@dataclass
class RBDEvent(RBDBasic):
    """Port of `RBDEventObj`."""

    op_status: EventStatus = EventStatus.ARMED
    old_status: EventStatus = EventStatus.ARMED
    fail_distro: int = 17  # default Bernoulli, matches typical event usage
    fail_stream: int = 0
    failure_counter: int = 0
    downstream_node_id: int = 0
    downstream_link_id: int = 0
    discarded_failures: int = 0
    fail_mean: float = 0.0  # the "probability" param for Bernoulli events
    fail_stdev: float = 0.0
    initial_cost: float = 0.0
    operating_cost: float = 0.0
    repairing_cost: float = 0.0
    is_connected_node: bool = False
    allow_reconfigure_system: bool = False
    fail_values: list[float] = field(default_factory=list)
    phase_value: list[float] = field(default_factory=list)
    phase_type: list[str] = field(default_factory=list)


@dataclass
class RBDNode(RBDBasic):
    """Port of `RBDNodeObj`."""

    node_name: str = ""
    type_node: int = 0

    @property
    def report_name(self) -> str:
        """Name as Raptor 7 prints it in the event log and Final Results.

        A type-3 node (`End`) is always logged as lowercase `end`, whatever
        the `.rbd` row carries; every other node keeps its own name.  The
        model itself keeps the authored spelling so the canvas and the file
        rows stay in step.
        """
        if self.type_node == 3 and self.node_name.lower() == "end":
            return "end"
        return self.node_name
    good_paths: int = 0
    propagation_out_count: int = 0
    cap_connect_out: int = 0
    node_flow: int = 0
    k_star: int = 0
    propagates_to_node_ids: list[int] = field(default_factory=list)
    propagates_to_link_ids: list[int] = field(default_factory=list)
    in_paths_array: list[int] = field(default_factory=list)
    cap_connect_to: list[int] = field(default_factory=list)
    cap_connect_path: list[int] = field(default_factory=list)
    path_fail_value: list[int] = field(default_factory=list)
    path_idle_value: list[int] = field(default_factory=list)
    path_standby_value: list[int] = field(default_factory=list)
    path_pm_value: list[int] = field(default_factory=list)
    cold_links: list[int] = field(default_factory=list)
    phase: list[int] = field(default_factory=list)
    next_node_ids: list[int] = field(default_factory=list)
    next_node_link_ids: list[int] = field(default_factory=list)
    #: `capNode` -- TRUE for End (type 3), Start (type 1),
    #: and every node that is a capacity-analysis BOUNDARY (`NOT fullFlow`
    #: OR `NOT anyPath`). Set once by `engine/capacity.py`'s graph build
    #: read at runtime by flow analysis/reporting.
    cap_node: bool = False
    #: `capTested` -- per-`FindCapPaths` recursion guard,
    #: reset across the outer node loop. Runtime-only.
    cap_tested: bool = False
    cold_standby: bool = False
    priority_return: bool = False
    check_autos_first: bool = False
    report_node_anal: bool = False
    full_flow: bool = False
    any_path: bool = False
    higher_cold: bool = False
    status: NodeStatus = NodeStatus.ALL_UP


@dataclass
class Link:
    """Port of `LinkObj` — note this does NOT inherit `RBDBasic` in the
    source (it inherits `PolygonObj` from the Modsim graphics library
    instead), so it's a separate dataclass rather than a subclass here."""

    connect_f_ref: str = ""
    connect_t_ref: str = ""
    resolved_from_kind: str = ""
    resolved_to_kind: str = ""
    switch_status: str = ""
    connect_from_id: int = 0
    connect_to_id: int = 0
    object_num: int = 0
    cold_priority: int = 0
    cap_priority: int = 0
    max_flow: int = 0
    nom_flow: int = 0
    actual_flow: int = 0
    dep_link: int = 0
    avail_flow: int = 0
    sim_nom_flow: int = 0
    sim_max_flow: int = 0
    sim_cap_priority: int = 0
    send_amount: int = 0
    resolved_to_id: int = 0
    resolved_from_id: int = 0
    parent_id: int = 0
    selected: bool = False
    has_siblings: bool = False
    #: `siblingsGroup` -- link ids mirrored by this
    #: link's own status changes (`ChangeLinkStatus`)
    #: and by `depLink`'s flow updates in `DistributeFlow`
    #: Populated by `graph.build_efpa_graph`'s
    #: RunEFPA depLink registration for EVERY
    #: model -- NOT capacity-gated (the same registration inside
    #: `capacity.analyze_capacity` is idempotent). A link feeding a
    #: block/event joins that element's `downstream_link_id`'s group.
    siblings_group: list[int] = field(default_factory=list)
    flowing: bool = False
    switch_completed: bool = False
    nom_reached: bool = False
    max_reached: bool = False
    path_full: bool = False
    active: bool = False
    sb_link: bool = False
    #: Engine-run state: the cold-standby node this link feeds (set by
    #: `cold_standby.sort_cold_links`), for `ReOrderColds`.
    cold_node: RBDNode | None = None
    status: LinkStatus = LinkStatus.IDLE
    auto_switch_probability: float = 0.0
    auto_switch_time: float = 0.0
    manual_switch_time: float = 0.0
    switch_time: float = 0.0
