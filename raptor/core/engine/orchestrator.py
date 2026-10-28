"""The cascade: how one object's state change reaches everything else.

Ports `ReconfigureBlock` / `ReconfigureEvent`'s downstream cascade and
`CheckColds`. `_Cascade` applies each block, event
and node transition and fans the result out to nodes, dependents, pools, cold
standby, phases and the system status. Four method groups live
in their own modules: `cascade_phase` (phase change), `cascade_cold` (cold
standby), `cascade_dependency` (dependency dispatch) and `cascade_status`
(system status and end of simulation). The rest of a run lives beside it: `load.py` builds the model, `driver.py` runs one replication over a
fresh `_Cascade`, `runner.py` loops over runs, `costs.py` builds the cost
tables and `step_session.py` is the GUI's step-through session.

The golden fixtures under `tests/golden/` exercise this path; see
`tests/unit/test_orchestrator.py`.
"""
from __future__ import annotations

from typing import TYPE_CHECKING
from collections.abc import Callable

from raptor.core.engine.blocks import BlockStatistics, StateDeltas, change_block_state
from raptor.core.engine.capacity import (
    CapacityState,
)
if TYPE_CHECKING:
    pass
from raptor.core.engine.cascade import reconfigure_after_block_change, reconfigure_system
from raptor.core.engine.dependency import (
    BackFlowDeps, FevDependencyHooks,
    back_flow_dependencies, back_flow_dependencies_from_node, back_flow_dependency_trigger,
    node_dependency_trigger,
)
from raptor.core.engine.events import EventStatistics, change_event_state, conduct_event
from raptor.core.engine.nodes import NodeStatistics, change_node_state, reconfigure_node
from raptor.core.engine.pool import (
    PoolStats,
)
from raptor.core.engine.run_control import reset_node_and_links_for_new_run, reset_system_state_for_new_run
from raptor.core.engine.system_status import (
    _MAINT_LIKE, MissionStatus, SystemColor, SystemState, block_fail_count_delta,
    end_node_system_up_transition, event_fail_count_delta,
)
from raptor.core.model import (
    ActiveStatus, BlockStatus, EventStatus, LinkStatus, NodeStatus, RBDBlock, RBDEvent, RBDNode,
)
from raptor.core.rng import RandomSource
from raptor.core.scheduler import Owner, Process, ProcessHandle, Scheduler
from raptor.core.engine.interrupts import InterruptReason
from raptor.core.engine import cascade_phase, cascade_cold, cascade_dependency, cascade_status
from raptor.core.engine.load import SimulationModel

#: Duration used for a dependency-forced Idle block's "wait for the next
#: Dependence_All_Up interrupt" -- just needs to exceed any real run length.
_FAR_FUTURE = 1.0e9


class _Cascade:
    """Per-run cascade wiring. Owns one run's mutable simulation state
    (node/system stats, dependent-block scheduler handles) and exposes
    `apply`, the single entry point every block transition funnels through.
    Reconstructed fresh for every run."""

    def __init__(
        self, model: SimulationModel, scheduler: Scheduler, logs: list[str],
        *, end_node: RBDNode | None = None, rng_fail_by_block: dict[int, RandomSource] | None = None,
        sb_link_rng: RandomSource | None = None, stats_by_block: dict[int, BlockStatistics] | None = None,
        stats_by_event: dict[int, EventStatistics] | None = None, rng_by_event: dict[int, RandomSource] | None = None,
        capacity_state: CapacityState | None = None,
        pool_stats_by_name: dict[str, PoolStats] | None = None,
    ) -> None:
        self.model = model
        self.scheduler = scheduler
        self.logs = logs
        #: Needed by `conduct_phase_change` to drive a phased block/event's
        #: own "Phase_Change" transition without a pre-registered per-object
        #: closure.
        self.stats_by_block = stats_by_block or {}
        self.stats_by_event = stats_by_event or {}
        self.rng_by_event = rng_by_event or {}
        self.node_stats: dict[int, NodeStatistics] = {nid: NodeStatistics() for nid in model.nodes_by_id}
        self.system_state = SystemState(
            ignore_zero_time_failures=model.ignore_start_fail)
        self.transitions: dict[int, Callable] = {}
        self.handles: dict[int, ProcessHandle] = {}
        #: `link.id -> ProcessHandle` for each in-flight
        #: `perform_switch_process` spawn. `Interrupt(link, "PerformSwitch")`
        #: needs the handle to queue the ON INTERRUPT redraw. The latest
        #: spawn per link wins.
        self._switch_handles: dict[int, ProcessHandle] = {}
        #: `link.id -> SimTime` of the last switch interrupt that actually
        #: fired. Suppresses a second same-instant interrupt on the
        #: same link when it targets a freshly re-spawned switch.
        self._switch_interrupted_at: dict[int, float] = {}
        #: `simInProgress`. While false (the
        #: synchronous per-block kickoff `SetToInitValues` pass), a node or
        #: block status change updates its own status line but must not
        #: cascade into dependent blocks yet. `_Cascade` is rebuilt every
        #: run, so the `False` default is the per-run reset.
        self.sim_in_progress = False
        #: The `/C130` mission file's rows (shared across the batch), or None.
        self.mission_lines: list[str] | None = None
        #: How many times the system status was analysed this run (`AnalyzeSystem`).
        self.status_checks = 0
        #: Failure Effects View's dependency rule (`engine/fev.py`); `None`
        #: in every ordinary run, so the run engine is unchanged.
        self.fev: FevDependencyHooks | None = None
        self._all_blocks = list(model.blocks_by_id.values())
        #: Raptor 7's `hasDepObjects` is precomputed by `CheckForDeps`
        #: and gates the whole
        #: `BackFlowDependencies` dispatch.
        #: Rebuilding that flag by scanning every block on every transition
        #: is O(blocks) per event, and `back_flow_dependencies` would then
        #: scan them again. These indexes are the one owner of that
        #: precomputation; order follows `_all_blocks` so multi-dependent
        #: dispatch still matches the source's group order.
        self._block_dependents: dict[int, list[RBDBlock]] = {}
        self._event_dependents: dict[int, list[RBDBlock]] = {}
        for _dep_block in self._all_blocks:
            if _dep_block.dep_type == "RBDBlock":
                self._block_dependents.setdefault(
                    _dep_block.dependency_num, []).append(_dep_block)
            elif _dep_block.dep_type == "RBDEvent":
                self._event_dependents.setdefault(
                    _dep_block.dependency_num, []).append(_dep_block)
        self._hier_names_by_node_id = {
            n.id: model.hiers_by_id[n.parent_id].name
            for n in model.nodes_by_id.values() if n.type_node == 5
        }
        #: The model's End node and per-block fail streams, used by
        #: `_cascade_node`'s `back_flow_dependencies_from_node` dispatch.
        #: `rng_fail_by_block` persists across runs; only `_Cascade` itself
        #: is rebuilt each run.
        self.end_node = end_node
        self.rng_fail_by_block = rng_fail_by_block or {}
        #: `SysStream[11]`, `Link.SetSwitchTime`'s dedicated shared stream
        #: Read only for a cold-standby link
        #: transitioning to Standby.
        self.sb_link_rng = sb_link_rng
        #: Set by `apply_event` when a FailSim model's End node hits
        #: `Permanently_Down` (the real `CallEndSimulation` trigger,
        #: Raptor 7). `run_simulation`'s event-init loop
        #: checks it after every event and stops early if set.
        self.run_ended = False
        #: Source's `autoStopped`: the End node went
        #: Permanently_Down under FailSim. Drives the MDT/MMT censoring
        #: corrections in `reporting.build_run_report_data` and the
        #: final-results `OutOfSpares` note.
        self.auto_stopped = False
        self.out_of_spares = False
        #: FailSim machinery: `termCounter`/`CountFailure`
        #: counts End-node down->up recoveries;
        #: `run_simulation` ends the run when it reaches
        #: `trunc(TermCondition)`. `end_sim_time` records the SimTime the
        #: early `EndSimulation` fired at.
        self.term_counter = 0
        self.end_sim_scheduled = False
        self.end_sim_time: float | None = None
        #: Re-entrancy guard for `apply_cold_logic`. The cold-logic switch
        #: loop drives a block transition through the same `apply` pipeline;
        #: with cold reconfiguration deferred to `_flush_cold_changes` this
        #: should not re-enter, but the guard protects against a nested
        #: `resend_back_flow` re-dispatching the same node while its own
        #: `run_cold_logic` call is still on the stack.
        self._cold_logic_in_progress: set[int] = set()
        #: Port of `ColdChangeGroup`/`coldsAffected` (Raptor 7
        #: `CheckColds`). `ReconfigureNode`'s cold-standby branch marks the
        #: node here rather than running `RunColdLogic` immediately;
        #: `_flush_cold_changes` drains the group later, at the end of a
        #: genuinely top-level block transition's cascade. Source's group is
        #: a `QueueObj`, drained FIFO, so this is an
        #: insertion-ordered dict used as an ordered set, not a `set`.
        self._cold_change_group: dict[int, None] = {}
        #: Port of `skipBackflow`. Set for the
        #: rest of a flush so a `resend_back_flow`-triggered dispatch that
        #: lands back on a cold node's candidate cannot bounce between
        #: revival and re-idling forever.
        self._skip_backflow = False
        #: Re-entrancy guard for `_flush_cold_changes` (see that method).
        self._flushing_cold_changes = False
        #: Set by `_induce_dependency`, consumed by `apply`'s
        #: `top_level and not wake_ids` gate. `Induced_Failure`/`OBPM` has
        #: no synchronous `transition()` step of its own: the woken block's
        #: transition and log lines happen later, when its queued interrupt
        #: resumes as a separate scheduler turn at the same SimTime. If the
        #: dispatching block's own top-level check ran immediately it would
        #: see a system-status change reflecting only itself, not the block
        #: it just induced-failed.
        self._induced_wake_pending = False
        #: Block ids whose next top-level transition is forced
        #: `top_level=False` (see `apply`): a multi-block resource-pool
        #: grant checks system status only once, after the last is granted.
        self._suppress_top_level_for: set[int] = set()
        #: Block ids given a `"Pool_Available"` interrupt by this `apply`
        #: call's `wake_ids` handling, not consumed by a same-turn
        #: dependency dispatch. A block can be both a resource-pool waiter
        #: and dependency-recovering in the same cascade; `_dispatch_dependency`
        #: checks this to skip a redundant second interrupt. Only ever added
        #: to and discarded from, never reassigned.
        self._pool_woken_this_turn: set[int] = set()
        #: The `SimTime` a deferred system-status check is currently
        #: scheduled for, or `None` if none is pending -- see
        #: `_schedule_or_check_system_status`'s own docstring.
        self._pending_status_check_time: float | None = None
        #: True once a deferred system-status check is owed at the END of
        #: the current instant (drained by run_simulation's on_step hook).
        self._pending_status_check_dirty: bool = False
        #: Source's `SystemStateChange`: any block
        #: transition this instant. Cleared by a full `_check_system_status`.
        self._system_state_changed: bool = False
        #: True when the most recent phase-change boundary had zero
        #: improve/degrade transitions -- suppresses repeated-Red emission.
        self._quiet_phase_boundary: bool = False
        #: True while a `conduct_phase_change` body is in progress (spans
        #: its internal yields), mirroring source's `PhaseChangeInProgress`.
        #: While set, `_schedule_or_check_system_status` must not queue a
        #: deferred check: the phase change's own end-of-body check is the
        #: only one.
        self.phase_change_in_progress = False
        #: Set by the driver each run: `ASK pmSyncTrigger TO Release`.
        self.release_pm_sync = lambda: None
        #: Blocks parked on `pmSyncTrigger` inside their own `Run` (the scheduled-PM arm,
        #: Raptor 7), in the order they parked.
        self.pm_sync_run_waiters: list[int] = []
        #: Per-trigger waiter queues, shared with the driver's `ControlPMtriggers` waiters: ints are
        #: those processes, `("run", block_id)` a block held by the defer trigger.
        self.trigger_waiters: dict[str, list] = {}
        #: `ASK trig TO Release` by name (the driver fills it in each run).
        self.release_trigger = lambda name, sim_time: None
        #: Set by `_conduct_phase_change_body` when the phase change changed
        #: any block/event (source's `SystemStateChange`); arms the lazy
        #: end-of-instant status check once the body is done.
        self._phase_change_dirtied = False
        self._give_seq = 0
        #: This run's capacity state (`CapacityState`, None when capacity
        #: analysis is off). `_mark_cap_link_change` sets its
        #: `cap_analysis_required` flag whenever a link status changes.
        #: Owned and recreated by `run_simulation` per run.
        self.capacity_state = capacity_state
        #: Per-resource-pool cost accumulators (cost analysis), read by
        #: `make_transition` for a block's own time-based borrow charges.
        self.pool_stats_by_name = pool_stats_by_name or {}

    # Method groups kept in their own modules as functions taking the
    # cascade as `self`, bound here so they are ordinary methods.
    # cascade_phase.py
    conduct_phase_change = cascade_phase.conduct_phase_change
    _conduct_phase_change_body = cascade_phase._conduct_phase_change_body
    phase_apply_node = cascade_phase.phase_apply_node

    # cascade_cold.py
    apply_cold_logic = cascade_cold.apply_cold_logic

    def park_for_pm_sync(self, block_id: int) -> None:
        self.pm_sync_run_waiters.append(block_id)

    @property
    def defer_trigger(self) -> str:
        return self.model.defer_trigger

    def has_trigger(self, name: str) -> bool:
        return any(t.trigger_name == name for t in self.model.triggers)

    def park_for_trigger(self, name: str, block_id: int) -> None:
        """`WAIT FOR trig TO Fire` from a block's own `Run` (the `maintDefer` hold): queued with the
        trigger's other waiters, in the order they parked."""
        self.trigger_waiters.setdefault(name, []).append(("run", block_id))

    def begin_phase_change(self) -> None:
        """`PhaseChangeInProgress:=TRUE`, set by the phase clock before it TELLs the body."""
        self.phase_change_in_progress = True
    kickoff_cold_logic = cascade_cold.kickoff_cold_logic
    _mark_node_cold_changed = cascade_cold._mark_node_cold_changed
    _interrupt_switch = cascade_cold._interrupt_switch
    _interrupt_switch_from_reconfigure = cascade_cold._interrupt_switch_from_reconfigure
    _run_cold_logic_now = cascade_cold._run_cold_logic_now
    flush_cold_changes = cascade_cold.flush_cold_changes
    _flush_cold_changes = cascade_cold._flush_cold_changes
    _switch_interrupt = cascade_cold._switch_interrupt

    def backflow_deps(self, *, special_back_flow: bool = False) -> BackFlowDeps:
        """Bind the node-sourced back-flow bundle once (review E-09).

        Four call sites — two in `cascade_cold`, one here, two in `kickoff`
        — each assembled the same eight expressions from this same object:
        `end_node.id if end_node else -1`, `_all_blocks`,
        `model.links_by_id`, `_dispatch_dependency`,
        `_draw_dependency_action`, `_induce_dependency`, `model.nodes_by_id`,
        and `apply_cold_logic`.

        The first seven are time-invariant, so the bundle is built once per
        run. The eighth is a closure over `sim_time`, which is why the bundle
        stores the raw two-argument `apply_cold_logic` and
        `BackFlowDeps.cold_logic_now(sim_time)` builds the closure.

        `nodes_by_id` is what enables the NodeDepGroup half
        (`dependency._back_flow_node_dependents`): a cold-standby node that
        is itself a dependent gets `higherCold` toggled there. A bundle built
        with `nodes_by_id=None` runs the block half only, which is what
        `resend_back_flow` does today — flagged there rather than changed.

        `special_back_flow` is the one per-call difference, and it is
        defaulted rather than part of the cached bundle because only
        `resend_back_flow` sets it, through its own explicit bundle.
        """
        return BackFlowDeps(
            end_node_id=self.end_node.id if self.end_node else -1,
            all_blocks=self._all_blocks,
            links_by_id=self.model.links_by_id,
            transition=self._dispatch_dependency,
            nodes_by_id=self.model.nodes_by_id,
            draw_action=self._draw_dependency_action,
            induce=self._induce_dependency,
            special_back_flow=special_back_flow,
            apply_cold_logic=self.apply_cold_logic,
            fev=self.fev,
        )

    # cascade_dependency.py
    _dispatch_dependency_wait_gated = cascade_dependency._dispatch_dependency_wait_gated
    _dispatch_dependency = cascade_dependency._dispatch_dependency
    _induce_dependency = cascade_dependency._induce_dependency
    _draw_dependency_action = cascade_dependency._draw_dependency_action
    resend_back_flow = cascade_dependency.resend_back_flow

    # cascade_status.py
    _analyze_capacity_only = cascade_status._analyze_capacity_only
    _check_system_status = cascade_status._check_system_status
    check_system_status = cascade_status.check_system_status
    _flush_and_check_system_status = cascade_status._flush_and_check_system_status
    _schedule_or_check_system_status = cascade_status._schedule_or_check_system_status
    _deferred_check_process = cascade_status._deferred_check_process
    _end_simulation_now = cascade_status._end_simulation_now
    _end_sim_process = cascade_status._end_sim_process

    def _mark_cap_link_change(self) -> None:
        """`CapAnalysisRequired:=TRUE`. The
        caller is `nodes.set_link_status_mirrored`, which applies the
        source's LinkUp-crossing test itself before calling this -- real
        reads each link's own prior status, so the decision belongs with
        the status write, not here."""
        if self.capacity_state is not None and not self.capacity_state.cap_analysis_required:
            self.capacity_state.cap_analysis_required = True

    def reset_for_new_run(self) -> None:
        for node in self.model.nodes_by_id.values():
            reset_node_and_links_for_new_run(node, self.model.links_by_id)
        # Port of `Link.SetToInitValues`'s own status reset
        # a link feeding straight into a BLOCK/EVENT (direct
        # block->block connections, node->block lines) is in no node's
        # `in_paths_array`, so without this it kept the previous run's final
        # status for the whole next run. Raptor 7 resets every link to LinkUp at
        # every kickoff.
        for link in self.model.links_by_id.values():
            if link.status != LinkStatus.UP:
                link.status = LinkStatus.UP
        reset_system_state_for_new_run(self.system_state)
        # `FOREACH hier IN hierGroup: ASK hier TO SetToInitValues`
        # whose body is `Status:=AllUp;
        # numCurrentlyFailed:=0`. `model.hiers_by_id`
        # is shared across runs, so without this the count carried over from
        # the previous run.
        for hier in self.model.hiers_by_id.values():
            hier.num_currently_failed = 0
            hier.status = NodeStatus.ALL_UP
            # `hierDE[hier.seqNum]:=0` in the Out node's `SetToInitValues`
            # (gated on weakAnalysis).
            # `hiers_by_id` is shared across runs, so the down-event count
            # would otherwise accumulate.
            hier.down_event_count = 0
        self.node_stats = {nid: NodeStatistics() for nid in self.model.nodes_by_id}

    def reset_all_statistics(self, sim_time: float) -> None:
        """Port of `IRunsim.ResetAllStatistics` -- CycleSim's
        mid-run statistics reset. Every monitored duration/count/cost is
        zeroed, the sim-time cursors are re-anchored at `sim_time`, and the
        mission/cycle bookkeeping restarts from the current system state.

        Pool level monitors and wait/arrival counters are not tracked here;
        this engine derives every reported pool quantity from live queue
        state and carries only the two cost accumulators."""
        # Source's own event-file marker : a blank line then
        # "<time>  Started Gathering Statistics" (two spaces, direct
        # WriteString -- not a WriteEvents line, no tab columns).
        self.logs.append("")
        self.logs.append(f"{sim_time:.6f}  Started Gathering Statistics")
        ss = self.system_state
        ss.stats_started = True                # `StatsStarted:=TRUE` 
        # `StatsStartTime:=SimTime` -- feeds EndOfRun's Term2
        # (`SimTime-StatsStartTime`) and hence the final report's
        # `Active_Stat_Time` row.
        ss.stats_reset_time = sim_time
        ss.last_color_change = sim_time
        ss.last_up_down_change = sim_time
        ss.time_of_comp_failure = sim_time
        ss.last_mission_change = sim_time
        for stat in (
            ss.sys_fail_stat, ss.comp_fail_stat, ss.comp_repair_stat,
            ss.green_stat, ss.yellow_stat, ss.red_stat,
            ss.mission_green_stat, ss.mission_yellow_stat, ss.mission_red_stat,
        ):
            stat.reset()
        # CompPMMon is deliberately NOT reset -- source's own
        # ResetAllStatistics monitor list covers the other
        # monitors but skips CompPMMon, so post-reset MTBM/MTBMs keep
        # counting PMs from the whole run.
        ss.total_red_cost = 0.0
        # `ZeroInitCost:=TRUE` -- suppress the initial
        # (purchase) cost in the post-reset cost report.
        ss.zero_init_cost = True
        # `cyclesCount:=-1.0` (tony 4-22 comment: ControlPhaseChange
        # occurs AFTER this procedure and re-increments it to 0).
        ss.cycles_count = -1.0
        ss.missions_completed = 0
        ss.missions_began = 0
        ss.mission_success = True
        ss.last_mission_change = sim_time
        ss.missions_attempted = 1
        if ss.color != SystemColor.RED:
            ss.missions_began = 1
        if self.model.phases and ss.mission_status == MissionStatus.NON_MISSION:
            ss.missions_attempted = 0
            ss.missions_began = 0
        for node in self.model.nodes_by_id.values():
            self.node_stats[node.id] = NodeStatistics()
            # `nodeMissionBool[i]` restarts from
            # the node's current status, not unconditionally TRUE: a node
            # already Down or in NodePM at the reset instant enters the
            # post-reset window without mission credit. `NodeStatistics()`'s
            # default is True, so this only ever clears it.
            if self.model.weak_analysis:
                self.node_stats[node.id].mission_bool = node.status not in (
                    NodeStatus.DOWN, NodeStatus.PM)
            node.state_start_time = sim_time
            # `ASK NodeFlowMon[i]/NodeCapMon[i] TO Reset` then the
            # self-assignments `NodeFlow[i]:=NodeFlow[i]` /
            # `NodeCapacity[i]:=NodeCapacity[i]`:
            # clear the statistics, then re-seed with the value held right
            # now, which establishes the post-reset window's Min/Max.
            if self.capacity_state is not None and self.model.capacity_analysis:
                flow_stat = self.capacity_state.flow_stats.get(node.id)
                if flow_stat is not None:
                    flow_stat.reset(0, sim_time)
                    flow_stat.update(self.capacity_state.node_flow.get(node.id, 0), sim_time)
                cap_stat = self.capacity_state.cap_stats.get(node.id)
                if cap_stat is not None:
                    cap_stat.reset(0, sim_time)
                    cap_stat.update(self.capacity_state.node_capacity.get(node.id, 0), sim_time)
        for hier in self.model.hiers_by_id.values():
            # Raptor 7 -- `hierDE[hier.seqNum]:=0`.
            hier.down_event_count = 0
        for block in self.model.blocks_by_id.values():
            block.run_state.spares_used = 0
            # Source's ResetAllStatistics calls each block's own
            # `ResetStats`, which does exactly
            # `discardedFailures:=FailureCounter` -- FailureCounter is not
            # zeroed, and the EOR Sparing table's Failures column prints
            # `FailureCounter-discardedFailures`.
            block.run_state.discarded_failures = block.run_state.failure_counter
            block.state_start_time = sim_time
            bs = self.stats_by_block[block.id]
            # The same 19-field zeroing `kickoff` applies at the run
            # boundary, one owner since review E-03. Both sites fire in one
            # run on a `CycleSim` model with `StartStats > 0`, so they are two
            # instants of ONE reset rather than two competing copies of it.
            bs.reset_cost_and_time()
            # `blockMissionBool[i]` / `blockMCompleted[i]`
            # -- the block-side twin of the node
            # reset above, gated on `weakAnalysis`. Source's predicate is
            # exactly `_MAINT_LIKE`. The stats object is reused here, so
            # both fields are written.
            #
            # Deliberately NOT in the shared reset, and deliberately NOT the
            # same rule as `kickoff`'s: this one seeds from the block's
            # CURRENT status, because a block already in maintenance enters
            # the post-reset window without mission credit. Two different
            # rules, so neither belongs in the shared helper.
            if self.model.weak_analysis:
                bs.mission_bool = block.op_status not in _MAINT_LIKE
                bs.m_completed = 0
        for event in self.model.events_by_id.values():
            event.state_start_time = sim_time
            es = self.stats_by_event[event.id]
            for field_name, fval in vars(es).items():
                if isinstance(fval, float):
                    setattr(es, field_name, 0.0)
            # `eventMissionBool[i]` / `eventMCompleted[i]`
            # -- an event already in `Failure`
            # restarts without mission credit. The float sweep above cannot
            # cover these (one bool, one int).
            if self.model.weak_analysis:
                es.mission_bool = event.op_status is not EventStatus.FAILURE
                es.m_completed = 0
        for pool_stats in self.pool_stats_by_name.values():
            pool_stats.add_cost = 0.0
            pool_stats.es_cost = 0.0


    def _apply_hier_fail_delta(self, parent_id: int, delta: int) -> None:
        """Raptor 7 -- wherever the system's
        `NumCurrentlyFailed` moves, the owning hierarchy's own
        `numCurrentlyFailed` moves with it, gated on `parentID>0`. Pure
        bookkeeping; it exists so `AnalyzeSystem`'s Out-node reconciliation
        has a usable input. Two other source sites move the same counter and
        are deliberately not mirrored here: `cold_standby.py` already ports
        the node switch-failure sites, and `RBDHierObj.ChangeStatus`'s
        parent-hierarchy bump is gated on graphics output and never runs in
        a headless text run."""
        if delta == 0 or parent_id <= 0:
            return
        hier = self.model.hiers_by_id.get(parent_id)
        if hier is not None:
            hier.num_currently_failed += delta

    def make_on_transition(self, block: RBDBlock) -> Callable:
        def on_transition(
            old_op_status: BlockStatus, deltas: StateDeltas, sim_time: float, wake_ids: list[int],
            wake_kinds: dict[int, str], top_level: bool,
        ) -> None:
            self.apply(
                block, old_op_status, deltas, sim_time,
                wake_ids=wake_ids, wake_kinds=wake_kinds, top_level=top_level,
            )
        return on_transition

    def make_transition(self, block: RBDBlock, stats: BlockStatistics) -> Callable:
        """An externally-callable force-transition for an ordinary
        (`block_run_process`-driven) block, registered into
        `self.transitions[block.id]` so `_dispatch_dependency` can drive
        this block like a `dep_type=="RBDBlock"` one."""
        def transition(new_status, new_active, comment, sim_time, *, phase_stress=None, top_level=True):
            old_op_status = block.op_status
            old_active_status = block.active_status
            # `phase_stress` is the block's own last-computed stress value
            # (see that field), matching source's `ChangeBlockState` reading
            # the object's real `PhaseStress` field rather than recomputing
            # it. An explicit `phase_stress` overrides the field.
            change_logs, deltas = change_block_state(
                block, stats, new_status, new_active, comment, sim_time, events_file=True,
                phase_stress=phase_stress if phase_stress is not None else block.run_state.phase_stress,
                cost_analysis=self.model.cost_analysis, weak_analysis=self.model.weak_analysis,
                in_mission=self.system_state.mission_status is MissionStatus.MISSION,
                pool_stats=self.pool_stats_by_name.get(block.resource_pool_name),
            )
            self.logs.extend(change_logs)
            self.apply(block, old_op_status, deltas, sim_time, top_level=top_level,
                       old_active_status=old_active_status)
        return transition

    def apply(
        self, block: RBDBlock, old_op_status: BlockStatus, deltas: StateDeltas,
        sim_time: float, *, top_level: bool = True, wake_ids: list[int] | None = None,
        wake_kinds: dict[int, str] | None = None, old_active_status: ActiveStatus | None = None,
    ) -> None:
        """Port of `ReconfigureBlock`'s dispatch: `BackFlowDependencies`
        fires before this block's own node cascade.

        `wake_ids`: other blocks a resource-pool `release()` just made
        eligible to resume. `Scheduler.interrupt` queues their resumption
        rather than running it synchronously, so this block's own generator
        runs to its next `yield` before any woken block gets a turn. This
        call's own top-level system-status check is therefore skipped when
        `wake_ids` is non-empty -- the woken block's own later transition
        does its own check, which is the one that appears in Raptor 7 captures.

        Two independent triggers feed the same `_check_system_status`: (1)
        the ordinary `top_level`-gated check here, once per outermost
        transition, and (2) `_cascade_node`'s immediate check when a cascade
        actually reaches and changes the End node. Both funnel into the same
        idempotent check, so firing both is safe."""
        # Override a caller-supplied `top_level=True` when this block is one
        # of several blocks a single resource-pool `release()` just granted
        # together (`_suppress_top_level_for`). Each granted block's own
        # resumption is a separate top-level turn, so without this the first
        # to resume would check system status before the others are part of
        # the picture; only the last granted block should check.
        if block.id in self._suppress_top_level_for and top_level:
            self._suppress_top_level_for.discard(block.id)
            # The suppression exists for the system-status check only. Cold
            # changes flush in every one of these turns, since each granted
            # block's resumption is its own process and its PerformPM/Repair
            # entry calls CheckColds synchronously.
            #
            # The override fires only on a genuine scheduler-driven
            # resumption (`top_level=True`), leaving the entry available for
            # the block's own real resumption when the current transition is
            # a nested dependency dispatch.
            outermost_turn = True
            top_level = False
        else:
            outermost_turn = top_level

        # `ChangeBlockState` sets `SystemStateChange:=TRUE`
        # `TimeAdvance` runs `AnalyzeSystem` at the end
        # of any instant so marked. See `_analyze_capacity_only`.
        self._system_state_changed = True
        rc, fc, ic, sc, pc = deltas
        if (self.sim_in_progress and not (rc or fc or ic or sc or pc) and block.active_status != ActiveStatus.ACTIVE
                and (old_active_status is None or old_active_status == block.active_status)
                and block.op_status == BlockStatus.RUNNING):
            # `ChangeBlockState`: a block that stays Cut/Linked but comes back to Running
            # (a repair finished while Cut) marks its cold node for `CheckColds` -- "may need to go to standby".
            _cold_node = self.model.nodes_by_id[self.model.links_by_id[block.downstream_link_id].connect_to_id]
            if _cold_node.cold_standby:
                self._cold_change_group.setdefault(_cold_node.id)
        _fail_delta = block_fail_count_delta(old_op_status, block.op_status)
        self.system_state.num_currently_failed += _fail_delta
        self._apply_hier_fail_delta(getattr(block, "parent_id", 0), _fail_delta)

        # Raptor 7's `hasDepObjects` gate, using
        # the index built once per run instead of scanning every block here.
        dependents = self._block_dependents.get(block.id)
        has_dep_objects = bool(dependents)
        trigger = back_flow_dependency_trigger(rc, fc, ic, sc, pc, has_dep_objects=has_dep_objects)
        block_backflow_pending = trigger is not None and self.sim_in_progress and has_dep_objects

        # Queue resource-pool wakes before this block's own node cascade.
        # `release()` itself already ran synchronously in
        # `block_run_process`, so `wake_ids` here is just the queuing of
        # already-decided wakes. Mark all but the last of a multi-block
        # grant for suppression (see `_suppress_top_level_for`).
        if wake_ids and len(wake_ids) > 1:
            self._suppress_top_level_for.update(wake_ids[:-1])
        if wake_ids:
            self._pool_woken_this_turn.update(wake_ids)
        # The pool's `Give` resumes each waiter as a record of its own
        # (per-request) owner, strictly FIFO in grant order (`wake_ids`).
        # Raptor 7 then logs each PM grant's "Obtained_N_Resource(s)" at pickup;
        # a waiter that is also an Idle/Standby dependent of this block's
        # node is handled by `_dispatch_dependency` (placeholder slot below).
        for woken_id in wake_ids or []:
            self._give_seq += 1
            self.scheduler.interrupt(self.handles[woken_id], InterruptReason.POOL_AVAILABLE,
                                     token=(wake_kinds or {}).get(woken_id),
                                     gc_owner=Owner.give(self._give_seq))

        # Block-to-block backflow, after the resource-pool wake queuing
        # above. Source's repair-completion order puts TakeBack's Give-
        # resumes before the recovered block's own
        # `BackFlowDependencies(AllUp)`. The node-level AllUp
        # from the cascade below was already after the wake loop.
        if block_backflow_pending and trigger is not None:
            back_flow_dependencies(trigger, block, self._all_blocks, self._dispatch_dependency, sim_time,
                                  draw_action=self._draw_dependency_action, induce=self._induce_dependency,
                                  fev=self.fev, dependents=dependents)

        link = self.model.links_by_id[block.downstream_link_id]
        node = self.model.nodes_by_id[link.connect_to_id]
        # `ReconfigureNode`'s first arm is gated `(NOT coldStandby) OR
        # (higherCold)`: a cold node whose `higherCold`
        # is set aggregates and logs inline like an ordinary node; only a
        # cold node without `higherCold` defers to `ColdChangeGroup`.
        if node.cold_standby and not node.higher_cold:
            # A cold-standby node computes its own aggregate status via
            # `run_cold_logic` (called separately through `apply_cold_logic`).
            # Still call `reconfigure_after_block_change` with
            # `events_file=False` for its side effect of updating this
            # block's own incoming link status, which the later survey reads.
            # `reconfigure_node` only computes the aggregate result, so
            # discarding the return value is safe.
            reconfigure_result = reconfigure_after_block_change(
                block, node, self.model.links_by_id, rc, fc, ic, sc, pc, sim_time, events_file=False,
                sb_link_rng=self.sb_link_rng, on_link_status_change=self._mark_cap_link_change,
                interrupt_switch=self._interrupt_switch_from_reconfigure,
                on_cold_link_down=self._demote_running_cold_source,
            )
            # At kickoff every cold node is already in `ColdChangeGroup` (in node order) and the one
            # `CheckColds` drains it at the end; the port runs each node from its own kickoff process in node order, so a
            # kickoff-time transition must not put its node in the group ahead of the others (`LadL8A`: J8 before J4).
            # This mark is unguarded, exactly like source's own `Add`
            # Only mark the node when the
            # block's own incoming link's derived status actually changed:
            # source's `ColdChangeGroup.Add` sits inside the
            # `((linkStatusChanged) OR (inPath=0)) AND (coldStandby)` branch,
            # not behind a plain delta-sum test. `reconfigure_after_block_change`
            # returns None exactly when there was no link-status change, or
            # for a sum-0 no-op transition -- both must not mark.
            if reconfigure_result is not None and self.sim_in_progress:
                self._cold_change_group.setdefault(node.id)
        else:
            result = reconfigure_after_block_change(
                block, node, self.model.links_by_id, rc, fc, ic, sc, pc, sim_time,
                events_file=True, hier_name=self._hier_names_by_node_id.get(node.id),
                on_link_status_change=self._mark_cap_link_change,
            )
            if result is not None and result.log_line is not None:
                self.logs.append(result.log_line)
                self._cascade_node(node, result.new_status, sim_time)

        if outermost_turn:
            # Port of `CheckColds`'s call sites (kickoff, `PerformSwitch`
            # completion, repair completion), all reached from a block's own
            # outermost scheduler-driven turn. Runs unconditionally at top
            # level: Raptor 7's `CheckColds` fires synchronously in the repairing
            # block's own turn even when that transition also released
            # resource-pool grants, whose continuations are separate queued
            # processes and run after the whole cascade. Gated on
            # `outermost_turn`, not `top_level`, since a suppressed
            # multi-grant resumption is still a genuine outermost turn.
            # Source calls CheckColds only at failure, repair/maintenance
            # completion, beginning_PM, switch completion, kickoff and the
            # end of a phase change -- never after Run's own dependency-driven
            # Running/Idle/Standby change (gdb trace `events36` run 3
            # t=72.300109: Raptor 7's cold promotion comes from the phase change,
            # five hops after the Idle dispatches, not from Attitude's turn).
            _plain = {BlockStatus.RUNNING, BlockStatus.IDLE, BlockStatus.STANDBY}
            # Entering `PMhold` is not a `CheckColds` site either (`ObtainPMprereqs` calls `ChangeBlockState(PMhold,
            # )` and goes straight to its `WAIT FOR ... TO Give`, Raptor 7):
            # `LadL5Full` (real 2026-10-01) logs the cold node's PM line only when the PM begins, a second later.
            # A `maintDefer` block entering RepHold goes straight to `WAIT FOR trig TO
            # Fire`: the nodes it marked stay in the group until the next real call site (the DeferPhaseTrigProbe
            # trace: BatteriesNiH's `Repair` start, not Bus_QR's own turn, writes the cold-survey rows).
            if (not (old_op_status in _plain and block.op_status in _plain) and block.op_status != BlockStatus.PM_HOLD
                    and not block.run_state.no_cold_check):
                self._flush_cold_changes(sim_time)
        if self.mission_lines is not None and _fail_delta == 1:
            # `ChangeBlockState`'s tail: a block going down writes
            # `<id>  <SimTime>  <0 while the system is Green/Yellow, else 1>`, read AFTER the
            # reconfigure above.
            self.mission_lines.append(
                f"{block.id}  {sim_time:.6f}  "
                f"{0 if self.system_state.color in (SystemColor.GREEN, SystemColor.YELLOW) else 1}")
        if top_level and not wake_ids:
        # Suppressed whenever this cascade just queued an `Induced_Failure`/
        # `OBPM` interrupt (`_induced_wake_pending`): the woken block's own
        # later resumption does its own un-suppressed check once its
        # transition has happened.
            if self._induced_wake_pending:
                self._induced_wake_pending = False
            # Armed even with an induced/OBPM wake pending: the check is
            # the lazy, de-duplicated end-of-instant one, so arming it
            # cannot reorder mid-instant lines, and an induced wake can be
            # absorbed with no transition of its own (e.g. landing in a PM
            # hand-back window), which used to lose the instant's check
            # (PmMisDeferProbe run 7 t=100.524177: Raptor 7's Red_change
            # _r5_h4_p9_p13 after BatteriesNaS's beginning_PM).
            self._schedule_or_check_system_status(sim_time)
        else:
            # Literal: every `ChangeBlockState` sets `SystemStateChange:=TRUE`
            # so a nested or grant-releasing
            # transition dirties the instant too; the lazy de-duplicated
            # check makes arming it here harmless when another arm already
            # did. ReleaseProbe32 run 2 t=145.907225: Raptor 7's Red_change
            # _r5_c1_c2_c3 after a pool-woken Attitude went Repairing.
            self._schedule_or_check_system_status(sim_time)

        # Permanently_Down fires after the ordinary system State_change
        # line. Multi-hop cascades recurse through reconfigure_system itself
        # via node.propagates_to_node_ids/path -- see cascade.py.
        if block.op_status == BlockStatus.DONE:
            pd_lines = reconfigure_system(
                node, self.model.nodes_by_id, self.model.links_by_id, block.downstream_link_id, sim_time, events_file=True,
            )
            self.logs.extend(pd_lines)
            # ER3-12/ER3-20: mirrors apply_event's identical check just
            # below (Raptor 7's `CallEndSimulation`) -- a
            # non-empty return means the End node itself just hit
            # Permanently_Down. This block path had the log line but never
            # acted on it, so a FailSim model kept running to
            # `TermCondition` (a time-bound fallback) instead of stopping
            # at the actual permanent-down instant, and `out_of_spares`
            # stayed False the whole run. `run_length > 1` mirrors
            # apply_event's own guard (a `run_length<=1` FailSim model is
            # presumably meant to end after exactly one failure regardless
            # of cause, not specifically "out of spares").
            #
            # Unlike apply_event -- whose ONLY caller is run_simulation's
            # own kickoff/event-init loop, which checks `self.run_ended`
            # itself right after calling it (see that loop's own comment)
            # -- `apply()` is called from many places throughout a running
            # simulation via block_run_process's scheduler-driven turns,
            # none of which check `run_ended` after an ordinary call. Just
            # setting the flags here is therefore not enough to actually
            # stop the run; this also has to schedule the same queued
            # zero-delay `_end_sim_process()` the OTHER working FailSim
            # stop path (this function's `termCounter`-reaches-
            # `TermCondition` branch, above) uses -- Raptor 7's
            # `TELL System TO CallEndSimulation(...) IN 0.0`,
            # queued so the current cascade finishes first.
            if (pd_lines and self.model.termination_type == "FailSim" and self.fev is None
                    and not self.end_sim_scheduled and not self.run_ended):
                self.run_ended = True
                self.auto_stopped = True
                if int(self.model.run_length) > 1:
                    self.out_of_spares = True
                self.end_sim_scheduled = True
                self.scheduler.schedule_process(self._end_sim_process(), endsim=True)

        # Drop whatever this call's own `wake_ids` added to
        # `_pool_woken_this_turn`, whether or not `_dispatch_dependency`
        # consumed them: a wake_ids member with no same-turn dependency
        # overlap would otherwise linger and risk a skip for a later
        # unrelated dispatch targeting the same block id.
        if wake_ids:
            self._pool_woken_this_turn.difference_update(wake_ids)

    def apply_event(
        self, event: RBDEvent, old_op_status: EventStatus, run_change: int, fail_change: int, sim_time: float,
    ) -> None:
        """Port of `ReconfigureEvent` -- the
        event-side analog of `apply`, called after every `change_event_state`
        call (both the "Initialize" and the "Fired" one).

        Narrower than `apply`: no `Permanently_Down` call, and only the
        `RunChange`/`FailChange` deltas (events have no idle/standby/pm
        buckets). Deliberately does not call `_check_system_status`: the
        caller (`run_simulation`'s event-init loop) calls it exactly once,
        after all events, since real defers the system-status line until
        every event has been initialized."""
        _fail_delta = event_fail_count_delta(old_op_status, event.op_status)
        self.system_state.num_currently_failed += _fail_delta
        self._apply_hier_fail_delta(getattr(event, "parent_id", 0), _fail_delta)

        # `ReconfigureEvent`'s own gate is `IF ((hasDepObjects) AND
        # (simInProgress))`, exactly like the block and
        # node paths' (`block_backflow_pending`, `_cascade_node`). Events
        # initialize BEFORE `simInProgress:=TRUE`
        # so a kickoff-time Fired event does NOT dispatch its
        # dependents here: it only reconfigures its node, and the dependents
        # idle later via the queued event-level `InitTimeZeroDeps` (ported
        # in `_kickoff_down_node_redispatch_and_check`). Without this gate
        # the dependent idled BEFORE the event's own link update reached
        # the node, logging the node with the event's link still up
        # (`EventDepProbe`, every Failure-outcome run).
        dependents = self._event_dependents.get(event.id)
        trigger = back_flow_dependency_trigger(
            run_change, fail_change, 0, 0, 0, has_dep_objects=bool(dependents))
        if trigger is not None and self.sim_in_progress and dependents:
            back_flow_dependencies(trigger, event, self._all_blocks, self._dispatch_dependency, sim_time,
                                  draw_action=self._draw_dependency_action, induce=self._induce_dependency,
                                  fev=self.fev, dependents=dependents)

        if abs(run_change) + abs(fail_change) == 2:
            link = self.model.links_by_id[event.downstream_link_id]
            node = self.model.nodes_by_id[link.connect_to_id]
            result = reconfigure_node(
                node, self.model.links_by_id, run_change, fail_change, 0, 0, 0,
                event.downstream_link_id, sim_time, events_file=True, hier_name=self._hier_names_by_node_id.get(node.id),
                on_link_status_change=self._mark_cap_link_change,
                mark_cold_changed=self._mark_node_cold_changed,   # a cold node defers to `ColdChangeGroup`
            )
            if result is not None and result.log_line is not None:
                self.logs.append(result.log_line)
                self._cascade_node(node, result.new_status, sim_time)

        # `RBDEventObj.ReconfigureSystem`'s forwarder and its
        # `CallEndSimulation` trigger -- only on
        # a fresh Armed->Failure fire, i.e. `fail_change==1`, and only for
        # an UNPHASED event: the phased Armed arm never calls
        # it, since a phased event can re-fire in a later phase and its
        # failure is not permanent (ER3-12's "too broad" half; the flag
        # `AllowReconfigureSystem`, is TRUE whenever the
        # unphased arm is reached).
        # Failure Effects View changes an event with `ChangeEventState`, never
        # `ConductEvent`, so it has no `ReconfigureSystem` forwarder.
        if (fail_change == 1 and self.fev is None
                and not (event.uses_phasing and self.model.phases)):
            link = self.model.links_by_id[event.downstream_link_id]
            node = self.model.nodes_by_id[link.connect_to_id]
            pd_lines = reconfigure_system(
                node, self.model.nodes_by_id, self.model.links_by_id, event.downstream_link_id, sim_time, events_file=True,
            )
            self.logs.extend(pd_lines)
            # A non-empty return means the End node itself just hit
            # Permanently_Down (`reconfigure_system`'s only case that
            # produces a line). Raptor 7 then calls
            # `CallEndSimulation`, which ends the run with no further
            # system-status line; `run_simulation`'s event-init loop checks
            # `self.run_ended` and skips its own deferred check.
            if pd_lines and self.model.termination_type == "FailSim":
                self.run_ended = True
                self.auto_stopped = True
                if int(self.model.run_length) > 1:
                    self.out_of_spares = True

    def _demote_running_cold_source(self, link) -> None:
        """`ReconfigureNode`'s cold block: the source block of a cold link that just left
        `LinkUp` goes Standby if it is still `Running`, and is interrupted `Switched_Off` after t=0."""
        ref = link.resolved_from_kind or link.connect_f_ref
        if ref != "RBDBlock":
            return
        block = self.model.blocks_by_id[link.resolved_from_id if link.resolved_from_kind else link.connect_from_id]
        if block.op_status != BlockStatus.RUNNING:
            return
        sim_time = self.scheduler.time
        self.transitions[block.id](BlockStatus.STANDBY, block.active_status, "", sim_time, top_level=False)
        if sim_time > 0.0:
            self._switch_interrupt(block, InterruptReason.SWITCHED_OFF)

    def _cascade_node(
        self, node: RBDNode, new_status, sim_time: float, *, new_active_status: ActiveStatus | None = None,
    ) -> None:
        """Recursive node-to-node cascade tail (Raptor 7's
        downstream fan-out), generalizing a 1-hop to arbitrary depth. Checks
        "am I the End node" on entry so it also covers a block connecting
        directly into End.

        `new_active_status`: None keeps `node.active_status` unchanged;
        `conduct_phase_change` passes the status unchanged with a real new
        active status, matching `ChangeNodeState`'s calls for a type-5 node."""
        node_stats = self.node_stats[node.id]
        prior_status = node.status
        # `ChangeNodeState` sets `SystemStateChange:=TRUE` unconditionally.
        self._system_state_changed = True
        if new_active_status is None:
            new_active_status = node.active_status
        # `weak_analysis`/`is_start_or_end_node` must be threaded through
        # here, the only call site of `change_node_state` in the whole
        # orchestrator, or node weak stats never accumulate for any fixture.
        # `type_node==1` is the Start node -- see `change_node_state`'s own
        # docstring for the parameter name.
        node_deltas = change_node_state(
            node, node_stats, new_status, new_active_status, sim_time,
            weak_analysis=self.model.weak_analysis,
            is_start_or_end_node=node.type_node == 1,
            hiers_by_id=self.model.hiers_by_id)

        if node.type_node == 3:
            up_transition = end_node_system_up_transition(prior_status, node.status)
            if up_transition is not None:
                self.system_state.system_up = up_transition
                if up_transition:
                    # `SysTimeToRepair:=SimTime-LastUpDownChange`, written to the
                    # down-times file when `StatsStarted`.
                    if self.system_state.stats_started:
                        self.system_state.sys_repair_times.append(
                            sim_time - self.system_state.last_up_down_change)
                    # Port of Raptor 7's "was down, now up"
                    # branch: `LastUpDownChange:=SimTime`. That reset is what
                    # `SysFailMon`'s next `SysTimeToFail` sample needs, so it
                    # measures the up-segment since this recovery rather than
                    # the full fail-to-fail cycle.
                    self.system_state.last_up_down_change = sim_time
                    # FailSim failure counting:
                    # every End-node down->up recovery increments
                    # `termCounter` (CountFailure is TRUE throughout the
                    # normal flow; only the dZeroFail first-recovery edge
                    # clears it: `change_system_status`, ported 2026-10-09
                    # from `ZeroFail*Probe`). When the count
                    # reaches trunc(TermCondition), source queues `TELL
                    # System TO CallEndSimulation(FALSE,TRUE) IN 0.0`
                    # -- a QUEUED zero-delay entry, so the
                    # current cascade finishes first; `_end_sim_process`
                    # (scheduled below) reproduces EndSimulation's body.
                    # `(termType=2) AND (NOT FEVmode)`: Failure
                    # Effects View has no termination, so a restore there ends nothing.
                    if (self.model.termination_type == "FailSim" and self.fev is None
                            and not self.end_sim_scheduled and not self.run_ended):
                        if self.system_state.count_failure:
                            self.term_counter += 1
                        else:
                            self.system_state.count_failure = True
                        # -- `IF ((termCounter>0) AND (termCounter=
                        # TRUNC(StartStats))): TELL System ResetAllStatistics`.
                        # A deferred reset queued before the end-of-run TELL
                        # below, so a StartStats equal to the stop count resets
                        # and then ends. No fixture has FailSim with
                        # StartStats>0.
                        start_count = int(self.model.stats_start_time)
                        if self.term_counter > 0 and self.term_counter == start_count:
                            def _reset_statistics_process() -> Process:
                                self.reset_all_statistics(self.scheduler.time)
                                return
                                yield  # pragma: no cover
                            self.scheduler.tell(_reset_statistics_process(), deferred=True)
                        reached = self.term_counter == int(self.model.run_length)
                        if reached:
                            self.end_sim_scheduled = True
                            # -- `TELL System TO CallEndSimulation(
                            # FALSE,TRUE) IN 0.0`: QUEUED zero-delay entry, so
                            # the current cascade finishes first; the shared
                            # `_end_simulation_now` body (below) reproduces
                            # EndSimulation and raises StopRun.
                            #
                            # `endsim=True`, as for TimeSim's stop: at the
                            # run-end instant Raptor 7's ChooseNext serves block
                            # activities first, so the recovering block's
                            # post-repair `Run` TELL still starts and draws its
                            # next life before the run ends (ReleaseProbe37:
                            # without this, every run from 2 on was one
                            # fail-stream draw behind real -- a regression
                            # from 146a953's `Run;` boundary, 2026-09-20).
                            self.scheduler.schedule_process(self._end_sim_process(), endsim=True)
                # Asymmetry: going down checks and logs immediately, even
                # mid-cascade; going up waits for the outer top-level check
                # once the whole triggering cascade has finished. Report
                # trouble as soon as anything indicates it, and declare "all
                # clear" only once the recovery's cascade has fully settled.
                if up_transition is False:
                    self._check_system_status(sim_time)

        # Downstream recursive cascade must run and
        # fully finish before this node's own `BackFlowDependencies` dispatch
        # below. A node's dependents only go Idle/Dependence after the full
        # downstream chain has logged its own status lines; firing the
        # dependency dispatch first would consume/produce RNG draws in the
        # wrong order, and the shared streams persist across runs.
        if node.type_node != 3:
            delta_sum = sum(abs(d) for d in node_deltas)
            if delta_sum not in (0, 2):
                raise ValueError("Error in ChangeNodeState!")
            if delta_sum == 2:
                for downstream_id, path_link in zip(node.propagates_to_node_ids, node.propagates_to_link_ids):
                    downstream_node = self.model.nodes_by_id[downstream_id]
                    result = reconfigure_node(
                        downstream_node, self.model.links_by_id, *node_deltas, path_link, sim_time,
                        events_file=True, hier_name=self._hier_names_by_node_id.get(downstream_node.id),
                        on_link_status_change=self._mark_cap_link_change,
                        mark_cold_changed=self._mark_node_cold_changed,
                        interrupt_switch=self._interrupt_switch_from_reconfigure,
                    )
                    if result is None or result.log_line is None:
                        continue
                    self.logs.append(result.log_line)
                    self._cascade_node(downstream_node, result.new_status, sim_time)

        # Port of ChangeNodeState's own BackFlowDependencies dispatch
        # -- a different trigger classification from
        # `end_node_system_up_transition`'s, computed independently.
        # Unconditional on node type, generalized to fire for any node's
        # status change, covering `locDepend`/positive-`DepNum`+`RBDNode`
        # dependencies alongside `sysDepend`.
        #
        # Full source gate: `simInProgress AND
        # hasDepObjects AND (priorStatus<>Status) AND (NOT coldStandby)`.
        # `priorStatus<>Status` is `node_dependency_trigger`'s job; the
        # `NOT coldStandby` half is applied here (a cold node never
        # dispatches its dependents from its own state change -- its
        # RunColdLogic/CheckColds pass owns that propagation). `hasDepObjects`
        # is deliberately not ported: adding it changes many sweep fixtures.
        dep_trigger = node_dependency_trigger(prior_status, node.status, node.higher_cold)
        # Source's own full gate: `IF ((simInProgress)
        # AND (hasDepObjects) AND (priorStatus<>Status) AND (NOT coldStandby))`.
        # `priorStatus<>Status` is `node_dependency_trigger`'s own job; the
        # `NOT coldStandby` half (a cold-standby node never dispatches its
        # dependents from its own state change -- its RunColdLogic/CheckColds
        # pass owns that propagation) is applied here. `hasDepObjects` is
        # deliberately not ported: adding it changes many sweep fixtures.
        if (dep_trigger is not None and self.sim_in_progress
                and not node.cold_standby):
            back_flow_dependencies_from_node(
                dep_trigger, node, self.backflow_deps(), sim_time,
            )

    # Two `Interrupt(link,"PerformSwitch")` call sites with different
    # intent: `ReconfigureNode`'s fires whenever
    # a switching link's status changes and does nothing when the settled
    # status matches spawn time; `RunColdLogic`'s survey site
    # decides the switch is no longer needed and
    # acts unconditionally. They use distinct reason strings here.
    _SWITCH_IRQ_SURVEY = InterruptReason.PERFORM_SWITCH
    _SWITCH_IRQ_RECONFIGURE = InterruptReason.PERFORM_SWITCH_RECONFIGURE

def _init_event(event: RBDEvent, stats: EventStatistics, rng: RandomSource, cascade: _Cascade) -> None:
    """Port of `RBDEventObj.SetToInitValues`'s event-firing tail,
   both the non-phasing `ELSE` branch
    (`ChangeEventState(Armed,Active,"Initialize")` then `ConductEvent`,
    unconditionally) and the phased branch keyed on `phaseType[1]`:
    `"L"`/`"C"` move to Linked/Cut with no draw; `"P"` moves to Active with
    no draw; `"F"`/`"A"` moves to Active and fires, using phase 1's own
    `phaseValue` as the Bernoulli probability. Always `sim_time=0.0` --
    events fire once, synchronously, during kickoff."""
    if event.uses_phasing and event.phase_type:
        phase_1_type = event.phase_type[0]
    else:
        phase_1_type = None

    # SetToInitValues' own accumulator zeroing:
    # event cost and weak-analysis accumulators reset per run, before the
    # phased early-return below.
    stats.op_cost = stats.rep_cost = 0.0
    stats.run_time = stats.standby_time = stats.repair_time = 0.0

    if phase_1_type in ("L", "C"):
        new_active = ActiveStatus.LINKED if phase_1_type == "L" else ActiveStatus.CUT
        old_op_status = event.op_status
        logs, rc, fc = change_event_state(
            event, stats, EventStatus.ARMED, new_active, "Initialize", 0.0, events_file=True,
        )
        cascade.logs.extend(logs)
        cascade.apply_event(event, old_op_status, rc, fc, 0.0)
        return

    old_op_status = event.op_status
    armed_logs, rc, fc = change_event_state(
        event, stats, EventStatus.ARMED, ActiveStatus.ACTIVE, "Initialize", 0.0, events_file=True,
    )
    cascade.logs.extend(armed_logs)
    cascade.apply_event(event, old_op_status, rc, fc, 0.0)

    if phase_1_type == "P":
        return  # armed and Active, but doesn't fire until a later phase says to

    old_op_status = event.op_status
    fired_logs, rc, fc = conduct_event(
        event, stats, rng, sim_time=0.0, events_file=True,
        cost_analysis=cascade.model.cost_analysis,
        weak_analysis=cascade.model.weak_analysis,
        current_phase=cascade.system_state.current_phase,
        in_mission=cascade.system_state.mission_status is MissionStatus.MISSION,
    )
    cascade.logs.extend(fired_logs)
    cascade.apply_event(event, old_op_status, rc, fc, 0.0)


