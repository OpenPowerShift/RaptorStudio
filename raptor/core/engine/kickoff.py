"""A run's kickoff (`SingleRunDriver.kickoff`), in source's order: reset the run, initialize every block and event,
build the pools, start every process, then the triggers and the System's
own activities. Also the callbacks those phases hand to processes (PM-trigger
eligibility and wakes, spare-order spawns, `wake_block`, the statistics
reset).

These are `SingleRunDriver` methods, bound in the class body
(`name = kickoff_phases.name`, the module imported under that alias); each takes the driver as `self`.
"""
from __future__ import annotations

from typing import TYPE_CHECKING
import time

from raptor.core.engine.block_env import BlockEnv, BlockPools, current_observer
from raptor.core.engine.block_lifecycle import BlockProcess, get_start_cond
from raptor.core.engine.blocks import change_block_state
from raptor.core.engine.capacity import (
    CapacityState, build_capacity_run_report, determine_max_capacity,
    reset_for_new_run as capacity_reset_for_new_run, touch_capacity_monitors,
)
from raptor.core.engine.cold_standby import cold_logic_kickoff_process, sort_cold_links
from raptor.core.engine.costs import interim_cost_snapshot
from raptor.core.engine.dependency import back_flow_dependencies, back_flow_dependencies_from_node
from raptor.core.engine.eventlog import format_event_log_line
from raptor.core.engine.events import change_event_state
from raptor.core.engine.interrupts import InterruptReason
from raptor.core.engine.nodes import NodeStatistics, change_node_state, reconfigure_node
from raptor.core.engine.orchestrator import _Cascade, _FAR_FUTURE, _init_event
from raptor.core.engine.phasing import control_phase_changes_process
from raptor.core.engine.pool import (
    PoolStats, ResourceQueue, generate_spares_process, order_a_spare_process, order_stock_process,
)
from raptor.core.engine.report_format import ao_time_column, ao_value
from raptor.core.engine.reports import format_interim_cost_report
from raptor.core.engine.sim_details import NamedRandomGen
from raptor.core.engine.system_status import MissionStatus, SystemColor
from raptor.core.engine.triggers import control_pm_triggers_process, trigger_process
from raptor.core.model import (
    ActiveStatus, BlockRunState, BlockStatus, EventStatus, NodeStatus, RBDBlock, RBDNode, SparingType,
)
from raptor.core.scheduler import Owner, Process, Scheduler

if TYPE_CHECKING:
    from raptor.core.engine.driver import SingleRunDriver


def _reset_run(self: SingleRunDriver):
    """Kickoff, first phase: this run's scheduler, capacity state, pool cost
    accumulators and cascade, every node reset to its file values
    (`RBDNodeObj.SetToInitValues`) and the system's mission state
    (`SystemObj.SetToInitValues`)."""
    _current_scheduler = self.ctx.current_scheduler
    end_node = self.ctx.end_node
    model = self.ctx.model
    on_event = self.ctx.on_event
    original_good_paths = self.ctx.original_good_paths
    rng_by_event = self.ctx.rng_by_event
    rng_fail_by_block = self.ctx.rng_fail_by_block
    sim_details_recorder = self.ctx.sim_details_recorder
    stats_by_block = self.ctx.stats_by_block
    stats_by_event = self.ctx.stats_by_event
    sys_streams = self.ctx.sys_streams
    logs = self.logs
    self._scheduler = scheduler = Scheduler()
    self._pm_waiters = {}
    self._pm_sync_waiters = []

    # End-of-instant deferred system-status checks (see
    # `_schedule_or_check_system_status`): composed with any GUI live-view
    # observer so both run per step.
    def _on_step_with_check(s: Scheduler) -> None:
        analysed = False
        if cascade._pending_status_check_dirty:
            nxt = s.next_time()
            if nxt is None or nxt > s.time:
                cascade._pending_status_check_dirty = False
                cascade._pending_status_check_time = None
                cascade._check_system_status(s.time)
                analysed = True
        if cascade._system_state_changed:
            nxt = s.next_time()
            if nxt is None or nxt > s.time:
                cascade._system_state_changed = False
                cascade._analyze_capacity_only(s.time)
                analysed = True
        if cascade.capacity_state is not None:
            nxt = s.next_time()
            if ((nxt is None or nxt > s.time) and s.time > 0.0
                    and (s.last_active_time == s.time or analysed)):
                # Record NodeFlow/NodeCapacity at the end of every
                # instant something ran (see `touch_capacity_monitors`)
                # -- not the System's bookkeeping ticks, which change no
                # state (FactoryDemo's float-noise Units_Produced spread).
                touch_capacity_monitors(cascade.capacity_state, s.time)
        if on_event is not None:
            # With capacity analysis enabled the observer also receives a
            # copy of the live per-node flow/capacity dicts (plain-data
            # copies cross into the GUI thread safely; the engine mutates
            # the originals in place). The fifth argument is the live
            # SimulationModel so the view can paint node/link/event/
            # hierarchy colours from the engine's own live status values.
            if capacity_state is not None:
                on_event(s, cascade.system_state, self.all_blocks_list,
                         (dict(capacity_state.node_flow),
                          dict(capacity_state.node_capacity)),
                         model)
            else:
                on_event(s, cascade.system_state, self.all_blocks_list,
                         None, model)
    scheduler.on_step = _on_step_with_check
    if sim_details_recorder is not None:
        _current_scheduler["s"] = scheduler
    # Per-run capacity state, created after
    # `cascade.reset_for_new_run()` below so `DetermineMaxCapacity`
    # sees the freshly-reset all-up links.
    self.capacity_state = capacity_state = None
    if model.capacity_analysis:
        self.capacity_state = capacity_state = CapacityState()
        capacity_reset_for_new_run(capacity_state, model.nodes_by_id)
    # Per-run pool cost accumulators (Raptor 7 resets them every run).
    self.run_pool_stats = run_pool_stats = {name: PoolStats() for name in model.resource_pools}
    for name in model.spare_pools:
        run_pool_stats.setdefault(name, PoolStats())
    self._cascade = cascade = _Cascade(
        model, scheduler, logs, end_node=end_node, rng_fail_by_block=rng_fail_by_block,
        sb_link_rng=sys_streams.get(11), stats_by_block=stats_by_block, stats_by_event=stats_by_event,
        rng_by_event=rng_by_event, capacity_state=capacity_state,
        pool_stats_by_name=run_pool_stats,
    )
    cascade.mission_lines = self.ctx.mission_lines
    cascade.reset_for_new_run()
    if capacity_state is not None:
        max_cap = determine_max_capacity(
            model.nodes_by_id[model.start_node_id], model.cap_node_group,
            model.cap_link_ids, model.nodes_by_id, model.links_by_id,
        )
        capacity_state.flowing_at_max_cap = model.flow_generated == max_cap
    # Port of `RBDNodeObj.SetToInitValues`'s unconditional
    # `GoodPathsRequired:=goodPaths`: reset every
    # node's runtime `good_paths` to its static file value first (see
    # `original_good_paths`), then override it for a phased, non-type-5
    # node from `phase[1]`. This per-node loop runs before
    # the block loop below, matching the kickoff `FOREACH node ...`
    for node in model.nodes_by_id.values():
        node.good_paths = original_good_paths[node.id]
        # Raptor 7: a cold node without priority return is
        # re-sorted at every run start, undoing the previous run's
        # `ReOrderColds` moves (idempotent for run 1).
        if node.cold_standby and not node.priority_return:
            sort_cold_links(node, model.links_by_id)
        if node.type_node != 5 and node.uses_phasing and model.phases and node.phase:
            phase_1_val = node.phase[0]
            # `len(node.in_paths_array)`, not `node.connect_into_num`
            # -- see the phase-change classification loop's own
            # comment (below) for why that field is always 0.
            node.good_paths = len(node.in_paths_array) + 1 if phase_1_val == -1 else phase_1_val

    # Port of `SystemObj.SetToInitValues`
    # together with the kickoff sequence's inline
    # `WriteEvents(...,"Began_a_mission")` (gated on
    # `activePhases>0 AND phase.mission`). Both key off phase[1].mission,
    # so they are computed once here. Generalizes
    # `reset_system_state_for_new_run`'s always-Mission default to the
    # real conditional.
    phase_1_mission = model.phases[0].mission if model.phases else True
    cascade.system_state.mission_status = MissionStatus.MISSION if phase_1_mission else MissionStatus.NON_MISSION
    cascade.system_state.mission_success = True
    if self.ctx.cum_time is not None:
        cascade.system_state.reliability_record = self.ctx.cum_time.record_reliability
    cascade.system_state.missions_completed = 0
    cascade.system_state.missions_attempted = 1 if phase_1_mission else 0
    cascade.system_state.missions_began = 1 if phase_1_mission else 0
    if model.phases and phase_1_mission:
        logs.append(format_event_log_line(0.0, "System", "State_change", "Mission", "Began_a_mission"))


def _initialize_blocks_and_events(self: SingleRunDriver):
    """Kickoff: every block's `GetStartCond` draw and `SetToInitValues`
   -- its per-run reset, its transition and its
    `Initialize` state change with the cascade -- then every event's
    initialization. Returns each block's start condition and initial fail
    time left."""
    model = self.ctx.model
    rng_by_event = self.ctx.rng_by_event
    rng_fail_by_block = self.ctx.rng_fail_by_block
    rng_repair_by_block = self.ctx.rng_repair_by_block
    stats_by_block = self.ctx.stats_by_block
    stats_by_event = self.ctx.stats_by_event
    cascade = self.cascade
    logs = self.logs
    # Port of Raptor 7's `FOREACH block IN blockGroup: ASK
    # block TO GetStartCond; ASK block TO SetToInitValues` loop --
    # GetStartCond's kickoff draw must happen in this same per-block pass,
    # in this same order, for every block's position in the shared fail
    # stream to land correctly.
    start_cond_by_block = {
        block.id: get_start_cond(
            block, rng_fail_by_block[block.id], rng_repair_by_block[block.id])
        for block in model.blocks_by_id.values()
    }
    initial_fail_time_left_by_block = {
        bid: sc.fail_time_left for bid, sc in start_cond_by_block.items()
    }
    self._pm_trigger_handles = {}

    for block in model.blocks_by_id.values():
        # `op_status`/`active_status` are reset in the Initialize pass below, block by block, as `SetToInitValues` does
        # inside `FOREACH block IN blockGroup`: until a block's turn it keeps the previous
        # run's final status (`Done`), which the system's `State_change` comment lists (`LadL8A` run 2 t=0:
        # `_c5_d6_d7...`).
        block.state_start_time = 0.0
        # A fresh `BlockRunState` is SetToInitValues' per-run reset of the
        # block's process flags: `TzeroAction`,
        # `returnFromMaint`, `spareObtained`, the wait-site gates and the
        # rest, and the run's counters (`SparesUsed[seqNum]:=0`,
        # Raptor 7). Each was once reset by hand here, and each
        # one missed let a run inherit the previous run's state (a stale
        # `fail_wait` woke a kickoff barrier; a stale `cold_path_done`
        # parked a block for every later run).
        block.run_state = BlockRunState()
        # `ignore_dep` -- set by `cold_standby._prep_for_switch` while a
        # cold-standby switch is in flight. A run ending mid-switch skips
        # the clear, so without this reset the next run's kickoff-time
        # dependency dispatch for this block silently no-ops even though
        # nothing is switching.
        block.ignore_dep = False
        # Cost/weak-analysis accumulators: SetToInitValues' own zeroing
        # (plus for events). Without
        # it run N+1's cost tables accumulate on top of run N's. DELEGATED to
        # `BlockStatistics.reset_cost_and_time` (review E-03) -- this 19-field
        # reset was written out here AND in `orchestrator.reset_all_statistics`,
        # and both fire in one run on a `CycleSim` model with `StartStats > 0`,
        # so neither site could have been checked against the other by running
        # it. The field list is derived from the dataclass, so an added
        # accumulator cannot leave this site stale.
        bs = stats_by_block[block.id]
        bs.reset_cost_and_time()
        # `blockMissionBool[i]:=TRUE; blockMCompleted[i]:=0` at every run
        # start. The stats object is shared, so
        # without this a later run inherited the previous run's credit.
        # Deliberately NOT in the shared reset: §2 traced that with
        # `weakAnalysis` off these are written but read zero times, so the
        # unconditional write here is the more faithful of the two rules.
        bs.mission_bool = True
        bs.m_completed = 0
    # `cascade.transitions[block.id]` must exist for every block before
    # any of them runs its kickoff Initialize cascade below -- one block's
    # Initialize can cascade into a dependency that dispatches another
    # block not reached by the loop. Normally populated by the later
    # per-block scheduling loop; pulled forward here, so every block is ready
    # for a kickoff-time cascade.
    for block in model.blocks_by_id.values():
        cascade.transitions[block.id] = cascade.make_transition(block, stats_by_block[block.id])
    # Port of `RBDBlockObj.SetToInitValues`'s own `ChangeBlockState
    # (Running,Active,"Initialize")` call (Raptor 7, reached by
    # the synchronous `ASK block TO SetToInitValues` at Raptor 7,
    # well before the deferred `TELL block TO Run IN 0.0`). Firing it here
    # keeps every block's Initialize line and its node/dependency cascade
    # before any event kickoff work. Every block, dependent or not, fires
    # its Initialize from this same pass, so declaration order logs in
    # declared-ID order.
    for block in model.blocks_by_id.values():
        # Port of SetToInitValues' `PhaseStress:=phaseValue[1]` (else
        # 1.0, Raptor 7) and its Cut/Linked kickoff branch
        # a phased block whose phase-1 type is Linked/Cut
        # starts the run in that active_status, with a cascade.
        #
        # `init_status`: the three-way startCond branch :
        #   startCond="Running"          -> Running
        #   startCond="preLDT"/"postLDT" -> RepHold
        #   otherwise ("repairing")      -> Repairing
        block_start = start_cond_by_block[block.id]
        if block_start.start_condition == "Running":
            init_status = BlockStatus.RUNNING
        elif block_start.start_condition in ("preLDT", "postLDT"):
            init_status = BlockStatus.REP_HOLD
        else:
            init_status = BlockStatus.REPAIRING
        phase_1_active = ActiveStatus.ACTIVE
        phase_1_stress = 1.0
        if block.uses_phasing and model.phases and block.phase_type:
            phase_1_stress = block.phase_value[0] if block.phase_value else 1.0
            if block.phase_type[0] == "L":
                phase_1_active = ActiveStatus.LINKED
            elif block.phase_type[0] == "C":
                phase_1_active = ActiveStatus.CUT
        # Persist onto the block itself, matching `PhaseStress`'s nature
        # as a real per-block field. Without this, a block that stays Idle
        # from kickoff until its first phase change never calls
        # `combined_stress`, leaving the field at its dataclass default
        # instead of phase 1's real value.
        block.run_state.phase_stress = phase_1_stress
        block.op_status = BlockStatus.RUNNING
        block.active_status = ActiveStatus.ACTIVE
        old_op_status = block.op_status
        init_logs, deltas = change_block_state(
            block, stats_by_block[block.id], init_status, phase_1_active, "Initialize", 0.0,
            events_file=True, phase_stress=phase_1_stress,
            cost_analysis=model.cost_analysis, weak_analysis=model.weak_analysis,
        )
        logs.extend(init_logs)
        cascade.apply(block, old_op_status, deltas, 0.0)
    for event in model.events_by_id.values():
        event.op_status = EventStatus.ARMED
        event.active_status = ActiveStatus.ACTIVE
        event.state_start_time = 0.0
    # Events fire once, synchronously, right here -- no scheduler
    # process, see `_init_event`. A model can have both blocks and events;
    # block SetToInitValues/GetStartCond always precedes event
    # SetToInitValues. The system-status check
    # happens once, after every event has initialized -- see
    # `_Cascade.apply_event`.
    if model.events_by_id:
        # Does not break early when `cascade.run_ended` becomes True
        # mid-loop: the real `TELL System TO CallEndSimulation(...)` call
        # is a queued zero-delay event, so every remaining event in kickoff
        # order still fires normally first, and the queued finalization
        # runs only after the whole loop finishes.
        for event in model.events_by_id.values():
            # `eventMissionBool[i]:=TRUE; eventMCompleted[i]:=0` per run.
            stats_by_event[event.id].mission_bool = True
            stats_by_event[event.id].m_completed = 0
            _init_event(event, stats_by_event[event.id], rng_by_event[event.id], cascade)
        if cascade.run_ended:
            # Port of EndSimulation's own finalization pass
            # (Raptor 7, minus the block loop -- this model
            # has none): every node silently resets to AllUp, and every
            # event gets ChangeEventState(Armed,activeStatus,"End_sim"),
            # in event order. No final system-status check.
            for node in model.nodes_by_id.values():
                change_node_state(node, NodeStatistics(), NodeStatus.ALL_UP, node.active_status, 0.0)
            for event in model.events_by_id.values():
                end_logs, *_ = change_event_state(
                    event, stats_by_event[event.id], EventStatus.ARMED, event.active_status,
                    "End_sim", 0.0, events_file=True,
                )
                logs.extend(end_logs)
        elif not model.blocks_by_id:
            cascade._check_system_status(0.0)
        # With blocks present, no check here: source has no
        # AnalyzeSystem after event init -- its lazy TimeAdvance-start
        # check (F.5) sees the t=0 state after every queued t=0 TELL
        # (block Run turns, the event-level `InitTimeZeroDeps`), and
        # `_kickoff_down_node_redispatch_and_check`'s own closing check
        # plays that role here. Checking now logged transient colors
        # Raptor 7 never shows: `EventDepProbe` (a Yellow before the
        # dependent's Idle), `PhasingDemo` 10/10 and `PhaseBarrierProbe`
        # runs 3/7 (a Yellow before t=0 phase-Cut dependency lines).
        # Scored against every Raptor 7 capture (1690 runs): each of those
        # runs' first divergence moved later, nothing else changed. An
        # events-only model keeps the check -- the `Events` fixture's
        # calibration, and nothing queued at t=0 could change its state.
    return start_cond_by_block, initial_fail_time_left_by_block


def _build_pools(self: SingleRunDriver):
    """Kickoff: this run's pools, reset to full --
    one queue per resource and spare pool, the Custom-sparing registry,
    and a routine-ordering (RSO) process per spare pool that has one."""
    model = self.ctx.model
    logs = self.logs
    run_pool_stats = self.run_pool_stats
    scheduler = self.scheduler
    #: block id -> its own Custom-sparing queue, filled in by each
    #: block's generator and read back at run end for the report's
    #: Custom_Sparing_Information table.
    custom_spares_registry: dict[int, ResourceQueue] = {}
    self._custom_spares_registry = custom_spares_registry
    resource_queues = {
        name: self._new_queue(pool.initial_spares, is_resource=True)
        for name, pool in model.resource_pools.items()
    }
    spare_queues = {
        name: self._new_queue(pool.initial_spares)
        for name, pool in model.spare_pools.items()
    }
    # Kept on the driver (not `ctx`) because they are rebuilt per run
    # right here; the run-end report reads them back for its stock tables.
    self._resource_queues = resource_queues
    self._spare_queues = spare_queues

    # Cost-analysis accumulators, one per RESOURCE pool (spare pools
    # keep a zeroed entry too so lookups always succeed -- see
    # `pool.PoolStats`'s own docstring).

    # RSO: one persistent background process per routine-ordering spare
    # pool, spawned once at the start of each run (a TELL/fire-and-forget
    # loop in the source, torn down only at run end).
    for pool_name, pool_config in model.spare_pools.items():
        if pool_config.routine_spare_ordering:
            scheduler.schedule_process(generate_spares_process(
                pool_config, spare_queues[pool_name], scheduler, self._wake_block, logs, events_file=True,
                pool_stats=run_pool_stats.get(pool_name),
            # Owned by the POOL, not `front_tier`: the owner-group calendar
            # alone puts the pool's RSO arrival ahead of a same-instant
            # block-owned custom RSO arrival -- and BEHIND it when a pool
            # ESO order TELLed just before moved the pool's group to the
            # calendar tail (Phasing3/CycleSimStartStatsCapacityProbe run
            # 7 t=1460: Thermal's arrival first, 9 other runs pool first).
            ), owner_key=Owner.pool(pool_name))


def _start_processes(self: SingleRunDriver, start_cond_by_block, initial_fail_time_left_by_block):
    """Kickoff: start every block's, node's and event's process
    (`TELL block TO Run IN 0.0`), the node-level `InitTimeZeroDeps` and
    the cold-standby kickoff with its second wave."""
    end_node = self.ctx.end_node
    model = self.ctx.model
    rng_fail_by_block = self.ctx.rng_fail_by_block
    rng_repair_by_block = self.ctx.rng_repair_by_block
    stats_by_block = self.ctx.stats_by_block
    sys_streams = self.ctx.sys_streams
    cascade = self.cascade
    custom_spares_registry = self._custom_spares_registry
    logs = self.logs
    resource_queues = self._resource_queues
    run_pool_stats = self.run_pool_stats
    scheduler = self.scheduler
    spare_queues = self._spare_queues
    self.all_blocks_list = all_blocks_list = list(model.blocks_by_id.values())
    #: ER3-08 -- `repair_completion_status`'s `dep_type=="RBDEvent"`
    #: branch resolves the depended-upon event from this list.
    all_events_list = list(model.events_by_id.values())

    # Port of `simInProgress:=TRUE` -- see
    # `_Cascade.sim_in_progress` for why it flips here, after the
    # synchronous block/event kickoff passes but before the phased-node
    # phase-1 push below.
    cascade.sim_in_progress = True

    # Port of Raptor 7's phasing kickoff: apply phase 1's
    # Cut/Linked state to every phased type-5 node; an
    # ordinary phased node's `good_paths` is handled earlier alongside the
    # rest of `RBDNodeObj.SetToInitValues`. `ControlPhaseChanges` itself
    # is spawned later in this function.
    #
    # Raptor 7's `IF node.Status=Down: ASK node TO
    # BackFlowDependencies(Down)` is unconditional, re-triggering
    # dependency backflow now that `sim_in_progress` is True for any node
    # that already went Down during the backflow-suppressed kickoff pass.
    # Their dependency reaction was deferred then, not dropped, and must
    # fire now.
    for node in model.nodes_by_id.values():
        if node.type_node == 5 and node.uses_phasing and model.phases and node.phase:
            phase_1_val = node.phase[0]
            if phase_1_val == -1:
                new_active = ActiveStatus.CUT
            elif phase_1_val == 0:
                new_active = ActiveStatus.LINKED
            else:
                new_active = None
            if new_active is not None:
                cascade._cascade_node(node, node.status, 0.0, new_active_status=new_active)
        elif node.type_node != 1 and node.good_paths > len(node.in_paths_array):
            # any other node needing more good paths than it has
            # incoming (a phased node whose phase 1 is Cut: `-1` -> n+1) is
            # reconfigured now, logging its `Bad` line after the events'
            # initialisation and before any block runs.
            result = reconfigure_node(
                node, model.links_by_id, 0, 0, 0, 0, 0, 0, 0.0,
                events_file=True, hier_name=cascade._hier_names_by_node_id.get(node.id),
                sb_link_rng=cascade.sb_link_rng, on_link_status_change=cascade._mark_cap_link_change,
                mark_cold_changed=cascade._mark_node_cold_changed,
            )
            if result is not None and result.log_line is not None:
                logs.append(result.log_line)
                cascade._cascade_node(node, result.new_status, 0.0)
        if node.status == NodeStatus.DOWN:
            back_flow_dependencies_from_node(
                "Down", node, cascade.backflow_deps(), 0.0,
            )
    # `_dispatch_dependency` always uses `top_level=False` (normally
    # reached from inside another block's top-level transition, whose own
    # check covers it), so nothing above triggers a system-status check on
    # its own. This kickoff-time re-check loop is not nested inside any
    # top-level transition, so it needs its own combined check. The check
    # itself is deferred below, because Raptor 7's Yellow line lands after
    # cold-standby's own kickoff redistribution.

    # FailSim and CycleSim: TermCondition is a count (failures/cycles),
    # not a time, so blocks and the scheduler loop get an unbounded
    # horizon; the termination callback ends the run via `StopRun`. The
    # horizon is `_FAR_FUTURE` for every termination type: per-block
    # calendar entries at exactly run_length would otherwise pop before
    # the endsim callee and fight EndSimulation's mass order.
    block_horizon = _FAR_FUTURE
    # What every ordinary block's process shares this run (`BlockEnv`).
    block_env = BlockEnv(
        scheduler, block_horizon, logs,
        events_file=True,
        cost_analysis=model.cost_analysis, weak_analysis=model.weak_analysis,
        startup_failures=model.startup_failures,
        phase_defer_enabled=bool(model.phases),
        rng_sys10=sys_streams.get(10),
        system_state=cascade.system_state,
        in_mission_fn=lambda: (cascade.system_state.mission_status
                               is MissionStatus.MISSION),
        wake_block=self._wake_block,
        all_blocks=all_blocks_list, all_events=all_events_list, end_node=end_node,
        nodes_by_id=model.nodes_by_id, links_by_id=model.links_by_id,
        custom_spares_registry=custom_spares_registry,
        cascade=cascade,
        observer=current_observer(),
    )
    for block in model.blocks_by_id.values():
        stats = stats_by_block[block.id]
        rng_fail = rng_fail_by_block[block.id]
        rng_repair = rng_repair_by_block[block.id]
        if block.dependency_num > 0 and block.dep_type not in ("RBDNode", "RBDBlock", "RBDHier", "RBDEvent"):
            # `RBDHier` (a block depending on a hierarchy) is supported:
            # source `CheckForDeps` resolves it as
            # `Child("RBDNode", simDependencyNum)`,
            # like an `RBDNode` dependency on the hierarchy's Out node --
            # see `dependency.is_node_dependent`. `RBDEvent` (ER3-08,
            # Raptor 7) is a block depending on
            # an event directly: `back_flow_dependencies` and
            # `repair_completion_status` both dispatch on it the same
            # shape as `RBDBlock`, substituting the event's own
            # `op_status`/`active_status` for the depended block's.
            raise NotImplementedError(
                f"block {block.name!r} has dep_type={block.dep_type!r} "
                f"(dependency_num={block.dependency_num}) -- only 'RBDNode', "
                "'RBDHier', 'RBDBlock' and 'RBDEvent' dependencies are ported; "
                "this one would otherwise be silently IGNORED rather than simulated"
            )
        spare_pool_config = model.spare_pools.get(block.pool_name)
        spawn_order_a_spare = None
        spawn_order_stock = None
        if spare_pool_config is not None:
            spawn_order_a_spare = self._make_spawn_order_a_spare(spare_pool_config, spare_queues[block.pool_name])
            spawn_order_stock = self._make_spawn_order_stock(spare_pool_config, spare_queues[block.pool_name])
        process = BlockProcess(
            block, stats, rng_fail, rng_repair, block_env,
            pools=BlockPools(
                resource_pool=resource_queues.get(block.resource_pool_name),
                pool_stats=run_pool_stats.get(block.resource_pool_name),
                spare_pool=spare_queues.get(block.pool_name), spare_pool_config=spare_pool_config,
                spawn_order_a_spare=spawn_order_a_spare, spawn_order_stock=spawn_order_stock,
            ),
            on_transition=cascade.make_on_transition(block),
            own_node=model.nodes_by_id[model.links_by_id[block.downstream_link_id].connect_to_id],
            initial_fail_time_left=initial_fail_time_left_by_block[block.id],
            initial_start_cond=start_cond_by_block[block.id],
        ).run()
        cascade.transitions[block.id] = cascade.make_transition(block, stats)
        if block.sparing_type == SparingType.CUSTOM and block.routine_spare_ordering:
            # Source TELLs this block's `GenerateSpares` BEFORE its Run
            # (vs ), so in the block's
            # owner group it takes the first-round slot and the Run
            # starts a round later (PhasingDemo t=0 gdb trace `events31`:
            # Thermal's GenerateSpares S00001, its Run S00021). The port
            # spawns the real GenerateSpares from inside Run (it needs the
            # per-run Custom queue), so this zero-work placeholder holds
            # its slot. It draws nothing, like GenerateSpares' own start.
            def _gs_slot() -> Process:
                return
                yield  # generator
            scheduler.schedule_process(_gs_slot(), block_id=block.id)
        cascade.handles[block.id] = scheduler.schedule_process(process, block_id=block.id)
        # Port of Raptor 7's own `IF ((block.usesPM) AND
        # (block.pmTriggered)): TELL block TO ControlPMtriggers IN
        # 0.0`, spawned right after this block's own Run/Repair in
        # the SAME per-block loop. It is the block's own activity, so it
        # shares the block's owner group (`owner_key`): its start --
        # and so its place on the trigger's waiter list -- comes a
        # round after the block's Run, and moves with the block's group
        # (Phasing1 run 6 gdb trace, 2026-09-23: SolarP_500W's
        # induced-failure interrupt at t=0 sent its group to the
        # calendar tail, so its ControlPMtriggers started, and waited,
        # behind BatteriesNiCd's for the whole run).
        if block.uses_pm and block.pm_triggered and block.pm_trig:
            self._pm_trigger_handles[block.id] = scheduler.schedule_process(
                control_pm_triggers_process(
                    block, scheduler,
                    should_wake=self._make_pm_trigger_eligible(block),
                    wake=self._make_pm_trigger_wake(block),
                    phase_defer_wait=self._make_pm_phase_defer_wait(block),
                    phase_change_in_progress=lambda: bool(cascade.phase_change_in_progress),
                    on_sync_wait=self._make_pm_sync_wait(block),
                    on_wait=self._make_pm_on_wait(block),
                ), owner_key=Owner.block(block.id))

    # Cold-standby kickoff redistribution, scheduled after every block's
    # own process above, so it runs last among the time=0.0 entries once
    # every block has logged its Initialize line -- see
    # `cold_standby.cold_logic_kickoff_process` for why this cannot be a
    # direct Python call here.
    for node in model.nodes_by_id.values():
        if node.cold_standby:
            # Source's kickoff is `CheckColds`, the
            # drain, not a bare per-node `RunColdLogic`. Running it through
            # the drain puts the node in the group while its own pass runs,
            # so the self-mark that pass makes is a no-op as source's
            # `IF NOT Includes(SELF)` makes it.
            def _kickoff_cold_logic(n: RBDNode = node) -> None:
                cascade.kickoff_cold_logic(n, scheduler.time)
            scheduler.schedule_process(cold_logic_kickoff_process(_kickoff_cold_logic))

    # Port of Raptor 7's node-level `InitTimeZeroDeps`
    # (`FOREACH node IN nodeGroup: IF node.Status=Down: TELL node TO
    # InitTimeZeroDeps IN 0.0`, i.e. `BackFlowDependencies(Down)` again),
    # deferred and positioned after the block Run/Repair TELL loop. The
    # earlier synchronous re-check runs before any block's handle is
    # registered, so `_induce_dependency`'s fail_wait/zero_wait gate can
    # only no-op there; this second call runs once every block has had its
    # first turn. `BlockRunState.time_zero_action` makes this a no-op re-dispatch
    # for a block already resolved and reuses the first call's cached
    # action for one still pending, so there is no second RNG draw.
    # The event-level copy (Raptor 7: `FOREACH event: IF
    # ((opStatus=Failure) OR (activeStatus=Cut)): TELL event TO
    # InitTimeZeroDeps IN 0.0`, i.e. `BackFlowDependencies(Down)`) is
    # ported too, ahead of the node loop as in source's TELL order:
    # `apply_event` suppresses kickoff-time event dispatch (source's
    # `simInProgress` gate), so this is where an event already Failed at
    # kickoff idles its dependents (`EventDepProbe`). The block-level
    # copy is still not ported -- no fixture exercises it.
    def _kickoff_down_node_redispatch_and_check() -> None:
        for event in model.events_by_id.values():
            if event.op_status == EventStatus.FAILURE or event.active_status == ActiveStatus.CUT:
                back_flow_dependencies(
                    "Down", event, all_blocks_list, cascade._dispatch_dependency, 0.0,
                    draw_action=cascade._draw_dependency_action, induce=cascade._induce_dependency,
                )
        for node in model.nodes_by_id.values():
            if node.status == NodeStatus.DOWN:
                back_flow_dependencies_from_node(
                    "Down", node, cascade.backflow_deps(), 0.0,
                )
        # Suppressed whenever this dispatch just queued a genuine
        # Induced_Failure/OBPM interrupt delivery (`_induced_wake_pending`):
        # the delivery is queued, not synchronous, so checking here would
        # compute the system color from the stale pre-failure picture. The
        # woken block's own later resumption does its own un-suppressed
        # check once its transition has happened.
        if cascade._induced_wake_pending:
            cascade._induced_wake_pending = False
        else:
            # t=0's AnalyzeSystem is source's lazy end-of-instant one:
            # arm it instead of checking now, so every t=0 Run turn
            # (including a custom-RSO block's round-later one) is in.
            cascade._schedule_or_check_system_status(0.0)

    # The kickoff-time Down backflow re-check's own combined
    # system-status check, deferred to after cold-standby's kickoff
    # redistribution and the node-level re-dispatch above, reusing
    # `cold_logic_kickoff_process`'s "run this once, deferred" shape. A
    # no-op whenever nothing is Down at kickoff.
    #
    # Two-stage scheduling: `cold_logic_kickoff_process`'s sequence number
    # is fixed at registration time, smaller than any block's round-2 push
    # (assigned when its barrier entry pops), so a single registration
    # would run this before every block reaches its genuine first wait. By
    # the time the first wave's body executes, the scheduler's global
    # sequence counter has advanced past every block's round-2 push, so a
    # second `schedule_process` made here gets a larger sequence number and
    # pops after every block has reached its own wait.
    def _schedule_second_wave() -> None:
        scheduler.schedule_process(cold_logic_kickoff_process(
            _kickoff_down_node_redispatch_and_check,
        ))

    scheduler.schedule_process(cold_logic_kickoff_process(_schedule_second_wave))


def _start_triggers(self: SingleRunDriver):
    """Kickoff: the PM-trigger subscriptions and one `PullRapTrigger` process
    per trigger, with the phase schedule first
    when it precedes the triggers (see `_phase_first`). Returns
    `_phase_first`."""
    _cost_sink = self.ctx.cost_sink
    model = self.ctx.model
    sim_details_recorder = self.ctx.sim_details_recorder
    sys_streams = self.ctx.sys_streams
    cascade = self.cascade
    logs = self.logs
    scheduler = self.scheduler
    # Port of Raptor 7's `CheckColds; FOREACH trig IN
    # triggerGroup: TELL trig TO PullRapTrigger IN 0.0` -- scheduled after
    # the cold-standby kickoff above, in TRIGGERS-section order.
    #
    # Port of `RBDBlockObj.ControlPMtriggers`,
    # collapsed into an `on_fire` callback instead of a literal per-block
    # process. Built once per run (blocks don't change trigger
    # subscriptions mid-run).
    pm_trigger_subscribers: dict[str, list[RBDBlock]] = {}
    pm_waiters = self._pm_waiters
    for block in model.blocks_by_id.values():
        if block.uses_pm and block.pm_triggered and block.pm_trig:
            pm_trigger_subscribers.setdefault(block.pm_trig, []).append(block)

    def _release_pm_sync() -> None:
        """`ASK pmSyncTrigger TO Release` : every process parked on
        `WAIT FOR pmSyncTrigger TO Fire` resumes, in the order it parked, as a record of the trigger's
        owner group like any other trigger's waiters: `ControlPMtriggers` processes
        (`self._pm_sync_waiters`), then blocks parked in their own `Run`'s scheduled-PM arm
        (`cascade.pm_sync_run_waiters`). The same wait in `ObtainPMprereqs`
        is not ported."""
        order = self._pm_sync_waiters
        self._pm_sync_waiters = []
        for bid in order:
            h = self._pm_trigger_handles.get(bid)
            if h is not None:
                scheduler.interrupt(h, InterruptReason.TRIGGER_FIRED, gc_owner=Owner.trigger("pmSyncTrigger"))
        run_order = cascade.pm_sync_run_waiters
        cascade.pm_sync_run_waiters = []
        for bid in run_order:
            h = cascade.handles.get(bid)
            if h is not None:
                scheduler.interrupt(h, InterruptReason.TRIGGER_FIRED, gc_owner=Owner.trigger("pmSyncTrigger"))

    cascade.release_pm_sync = _release_pm_sync

    def _make_on_trigger_fire(trigger_name: str):
        def on_fire(sim_time: float) -> None:
            # `PullRapTrigger`'s fire releases every process waiting on
            # this trigger; each `ControlPMtriggers` then decides for
            # itself, at its own turn, whether its block is eligible.
            # Absorption falls out of each subscriber re-checking
            # `opStatus` after any earlier cascade in the same instant has
            # run.
            #
            # Release order = the waiter list (`pm_waiters`). The wakes
            # are records of the TRIGGER's owner group, not the blocks'
            # (gdb trace, Phasing1: every waiter resumption at a fire
            # carried the trigger object as owner, the same owner as
            # `PullRapTrigger` itself).
            order = pm_waiters.get(trigger_name, [])
            pm_waiters[trigger_name] = []
            for entry in order:
                if isinstance(entry, tuple):
                    # A `maintDefer` block held in its own `Run` (`("run", block_id)`).
                    h = cascade.handles.get(entry[1])
                else:
                    h = self._pm_trigger_handles.get(entry)
                if h is not None:
                    scheduler.interrupt(h, InterruptReason.TRIGGER_FIRED, gc_owner=Owner.trigger(trigger_name))
        return on_fire

    cascade.trigger_waiters = pm_waiters
    fire_by_name: dict = {}

    def _release_trigger(name: str, sim_time: float) -> None:
        """`ASK trig TO Release`: the waiters resume, as at a fire."""
        fire = fire_by_name.get(name)
        if fire is not None:
            fire(sim_time)

    cascade.release_trigger = _release_trigger

    # Whether ControlPhaseChanges' first draw precedes the triggers'
    # PullRapTrigger draws at t=0 (they can share a system stream). Real
    # evidence (2026-09-23, `CycleSimStartStatsCapacityProbe`, one fresh
    # file, output options toggled one at a time): phase first with event
    # log / Final Results / End-of-Run / capacity plot; TRIGGERS first as
    # soon as Interim Costs is on. Source's kickoff queues `TELL System TO
    # CalculateInterimCost` (Raptor 7, `costAnalysis AND
    # CostFile`) ahead of `TELL System TO ControlPhaseChanges()`,
    # just as TimeSim always queues `CallEndSimulation IN StopCriteria`
    # there -- and every TimeSim capture is triggers-first. So the
    # old "by termination type" rule was a proxy for "a system TELL was
    # queued ahead of the phase process". Only the two evidenced cases
    # are encoded, plus FailSim (termType 2 queues nothing there either):
    # `PhasedFailSimOrderProbe`'s Raptor 7 capture (event log + reports, no
    # interim cost) confirmed phase first. `GenerateAoPlotData`/
    # `UpdateCumulativeValues` are untested.
    # `_cost_sink` stands in for CostFile: the API/GUI always request it
    # (like a ticked box), the golden-test harness does not (matching how
    # the CycleSim goldens were captured).
    _cost_file_on = _cost_sink is not None and model.cost_analysis
    _phase_first = model.termination_type != "TimeSim" and not _cost_file_on
    if model.phases and _phase_first:
        # CycleSim/FailSim, no interim-cost file: phase-duration draw first.
        scheduler.passive_handles.add(scheduler.schedule_process(control_phase_changes_process(
            model.phases, cascade.system_state, sys_streams[11], scheduler, cascade.conduct_phase_change, logs,
            events_file=True,
            termination_type=model.termination_type,
            cycle_target=int(model.run_length),
            stop_criteria=model.run_length,
            end_simulation=cascade._end_simulation_now,
            start_stats_cycles=int(model.stats_start_time),
            reset_statistics=self._reset_all_statistics,
            set_draw_tag=self._set_draw_tag
            if sim_details_recorder is not None else None,
            begin_phase_change=cascade.begin_phase_change,
        ), owner_key=Owner.system()).id)

    for trigger in model.triggers:
        # Real tags every trigger schedule draw with the trigger's own
        # name (`gettingDraw` at `TriggerObj`'s DrawNumber call site),
        # even though all triggers share SysStream 11. Wrap per-trigger
        # around the raw base so the shared stream advances once; other
        # stream-11 draws keep their generic SysStream11 recording.
        trigger_rng = sys_streams[11]
        if sim_details_recorder is not None:
            # sys_streams[11] is generically wrapped; unwrap to the raw
            # base so trigger draws record once, under this trigger's
            # name only (NamedRandomGen delegates .draw_number to .rng).
            base = getattr(sys_streams[11], "rng", sys_streams[11])
            trigger_rng = NamedRandomGen(base, sim_details_recorder,
                                         trigger.trigger_name, lambda: scheduler.time)
        _on_fire = _make_on_trigger_fire(trigger.trigger_name)
        fire_by_name[trigger.trigger_name] = _on_fire
        scheduler.passive_handles.add(scheduler.schedule_process(trigger_process(
            trigger, trigger_rng, scheduler, logs, events_file=True,
            on_fire=_on_fire,
        ), owner_key=Owner.trigger(trigger.trigger_name)).id)
    return _phase_first


def _start_system_processes(self: SingleRunDriver, _phase_first):
    """Kickoff, last phase: the System object's own activities
   -- the TimeSim end-of-run and its bookkeeping
    tickers, the Ao-plot and interim-cost interval processes, the phase
    schedule when it follows the triggers, and a TimeSim run's delayed
    statistics reset."""
    _ao_sink = self.ctx.ao_sink
    _cost_sink = self.ctx.cost_sink
    ao_plot_interval = self.ctx.ao_plot_interval
    cost_plot_interval = self.ctx.cost_plot_interval
    model = self.ctx.model
    rbd_file = self.ctx.rbd_file
    sim_details_recorder = self.ctx.sim_details_recorder
    stats_by_block = self.ctx.stats_by_block
    stats_by_event = self.ctx.stats_by_event
    sys_streams = self.ctx.sys_streams
    capacity_state = self.capacity_state
    cascade = self.cascade
    logs = self.logs
    run_pool_stats = self.run_pool_stats
    scheduler = self.scheduler
    # Port of termType=1's own `TELL System TO CallEndSimulation(FALSE,
    # TRUE) IN simLength`: the end-of-run is a real calendar entry
    # competing at its natural instant, not a forced stop.
    # `RBDSimControlObj.ChooseNext` defers it behind any block still
    # active at that instant while non-block entries fire first. Gated on
    # `blocks_by_id`: an events-only model has nothing keeping it alive
    # until run_length.
    _SYS = Owner.system()
    if model.termination_type == "TimeSim" and model.blocks_by_id:
        # The System object's own activities share one owner group, as in
        # the 2026-09-24 gdb traces (PooledGraceRepairProbe t=50):
        # `UpdateSimStatus(StopCriteria)` (19 waits of 0.05*StopCriteria,
        # while the status bar is on), then `CallEndSimulation IN StopCriteria`,
        # whose selection turns ChooseNext on for the rest of that instant
        # and TELLs `EndSimulation`.
        # `UpdateCumulativeValues(kpfTimeMesh)` exists only when a
        # key-parameters file is requested: the 2026-09-30 traces of captures
        # without one show no such activity, and modelling it anyway put a
        # phantom record in the System group ahead of the phase clock at every
        # multiple of 10 (`PhaseFailTieProbe`). It is not modelled.
        def _update_sim_status() -> Process:
            for _ in range(19):
                yield 0.05 * model.run_length

        def _call_end_simulation() -> Process:
            scheduler.cn_active_time = scheduler.time
            scheduler.schedule_process(cascade._end_sim_process(), endsim=True, owner_key=_SYS)
            return
            yield  # generator

        scheduler.passive_handles.add(
            scheduler.schedule_process(_update_sim_status(), owner_key=_SYS).id)
        scheduler.schedule_process(_call_end_simulation(), at=model.run_length, owner_key=_SYS)

    # Port of `TELL System TO GenerateAoPlotData()`
    # and CalculateInterimCost's own spawn -- one interval process each
    # per run. Interval values are GUI-dialog settings, not persisted in
    # the .rbd file.
    if _ao_sink is not None:
        def ao_plot_process() -> Process:
            cum = [0.0, 0.0, 0.0]
            while True:
                yield ao_plot_interval
                t = scheduler.time
                int_g, int_y, int_r = cum
                st = cascade.system_state
                g, y, r = st.green_stat.sum, st.yellow_stat.sum, st.red_stat.sum
                leftover = {
                    SystemColor.GREEN: t - st.last_color_change,
                    SystemColor.YELLOW: t - st.last_color_change,
                    SystemColor.RED: t - st.last_color_change,
                    SystemColor.DONE: 0.0,
                }[st.color]
                if st.color == SystemColor.GREEN: g += leftover
                elif st.color == SystemColor.YELLOW: y += leftover
                elif st.color == SystemColor.RED: r += leftover
                # Source's `cumGreen` etc. persist WITH the leftover
                # added, so the next interval is
                # measured from these totals.
                cum = [g, y, r]
                int_g, int_y, int_r = g - int_g, y - int_y, r - int_r
                total = g + y + r
                if total > 0.0:
                    # `ao_time_column` already ends with its own two-space
                    # column separator.
                    row = (ao_time_column(t)
                           + f"{ao_value((g + y) / total)}  {ao_value(g / total)}  "
                           + f"{ao_value(y / total)}  {ao_value(r / total)}  "
                           + f"{ao_value((int_g + int_y) / (int_g + int_y + int_r))}")
                else:
                    row = ao_time_column(t) + "1.000000  " * 4 + "1.000000"
                _ao_sink.append(row)
        scheduler.schedule_process(ao_plot_process(), owner_key=_SYS)

    cum_time = self.ctx.cum_time
    if cum_time is not None:
        # `TELL System TO UpdateCumulativeValues(kpfTimeMesh)`: one ticker per
        # run, a snapshot of the run's cumulative figures at every multiple of the mesh. Passive: it
        # reads state and changes none.
        def cumulative_time_process() -> Process:
            interval = 0
            while True:
                yield cum_time.mesh
                interval += 1
                cum_time.tick(interval, cascade.system_state, scheduler.time)
        scheduler.passive_handles.add(
            scheduler.schedule_process(cumulative_time_process(), owner_key=_SYS).id)

    if _cost_sink is not None and model.cost_analysis:
        # File header written ONCE.
        if not _cost_sink:
            _cost_sink.append(f"Interim Cost Report for {rbd_file.file_name}  {time.ctime()}")
            _cost_sink.append("")
        def interim_cost_process() -> Process:
            while True:
                yield cost_plot_interval
                t = scheduler.time
                # `run_length` is a TIME only for TimeSim (CycleSim/FailSim
                # store a cycle/failure count there): source's loop has no
                # such guard, and applying it to CycleSim silenced every
                # tick (ReleaseProbe27/31/32/35 interim cost).
                if model.termination_type == "TimeSim" and t >= model.run_length:
                    continue
                cap_rep = None
                if capacity_state is not None:
                    cap_rep = build_capacity_run_report(
                        capacity_state, model.cap_node_group, t,
                        model.flow_generated, model.stats_start_time,
                    )
                snap = interim_cost_snapshot(
                    model, stats_by_block, stats_by_event, run_pool_stats,
                    cascade.system_state, cap_rep, t,
                )
                _cost_sink.extend(format_interim_cost_report(
                    snap, t, capacity_on=capacity_state is not None,
                ))
        # Passive, like the other System tickers: a report snapshot
        # changes no state, so it must not split the capacity monitors
        # (FactoryDemo Final Results Mean_Capacity std with Interim Costs).
        scheduler.passive_handles.add(
            scheduler.schedule_process(interim_cost_process(), owner_key=_SYS).id)


    if model.phases and not _phase_first:
        # TimeSim, or CycleSim/FailSim with the interim-cost file: trigger
        # draws happen before the phase-duration draw (see `_phase_first`).
        scheduler.passive_handles.add(scheduler.schedule_process(control_phase_changes_process(
            model.phases, cascade.system_state, sys_streams[11], scheduler, cascade.conduct_phase_change, logs,
            events_file=True,
            termination_type=model.termination_type,
            cycle_target=int(model.run_length) if model.termination_type == "CycleSim" else None,
            stop_criteria=model.run_length,
            end_simulation=cascade._end_simulation_now,
            start_stats_cycles=int(model.stats_start_time),
            reset_statistics=self._reset_all_statistics,
            set_draw_tag=self._set_draw_tag
            if sim_details_recorder is not None else None,
            begin_phase_change=cascade.begin_phase_change,
        ), owner_key=Owner.system()).id)

    # `StatsStarted:=TRUE` when there is no delayed reset:
    # TimeSim with `StartStats` <= 1e-7, or any other type with `TRUNC(StartStats)=0`.
    if model.termination_type == "TimeSim":
        if model.stats_start_time <= 0.0000001:
            cascade.system_state.stats_started = True
    elif int(model.stats_start_time) == 0:
        cascade.system_state.stats_started = True
    if model.termination_type == "TimeSim" and model.stats_start_time > 0.0:
        # Port of Raptor 7 (termType=1): a TimeSim run
        # schedules `TELL System TO ResetAllStatistics IN startStat`, a
        # plain elapsed-time reset, regardless of phasing. CycleSim uses
        # the phase-cycle-count reset instead (`start_stats_cycles`).
        def _delayed_stats_start() -> Process:
            yield model.stats_start_time
            cascade.reset_all_statistics(scheduler.time)

        scheduler.schedule_process(_delayed_stats_start(), owner_key=_SYS)


def _make_pm_trigger_eligible(self: SingleRunDriver, block: RBDBlock):
    def eligible() -> bool:
        # Raptor 7: not mid-prereq-wait, not
        # phase-deferred, and Running/Idle/Standby.
        _deferred = block.run_state.pm_prerequisites_deferred
        return not (block.run_state.waiting_for_pm_prerequisites or block.run_state.defer_pm
                    or (_deferred is not None and _deferred())) and (
            block.op_status in (
                BlockStatus.RUNNING, BlockStatus.IDLE, BlockStatus.STANDBY))
    return eligible


def _make_pm_phase_defer_wait(self: SingleRunDriver, block: RBDBlock):
    model = self.ctx.model
    def needs_wait() -> bool:
        # Raptor 7's own `(activePhases>0) AND
        # (pmMisDefer)` -- see the hop's comment in
        # `control_pm_triggers_process`.
        # The whole body, hop included, sits inside `IF ((NOT WaitingForPrereqs) AND (NOT deferPM))` : a
        # subscriber that is deferred or mid-prerequisites skips the hop and re-joins the trigger's queue ahead of
        # the ones that pay it (`LadL8B` run 28 t=1100.423228: S3B3 before S2B2).
        return (bool(model.phases) and block.pm_mis_defer
                and not block.run_state.waiting_for_pm_prerequisites and not block.run_state.defer_pm)
    return needs_wait


def _make_pm_sync_wait(self: SingleRunDriver, block: RBDBlock):
    def on_sync_wait() -> None:
        # `WAIT FOR pmSyncTrigger TO Fire`: join the sync trigger's waiters.
        self._pm_sync_waiters.append(block.id)
    return on_sync_wait


def _make_pm_on_wait(self: SingleRunDriver, block: RBDBlock):
    def on_wait() -> None:
        # `ControlPMtriggers` entering `WAIT FOR myTrig TO Fire`
        #: join the back of the trigger's
        # waiter list. A fire releases its waiters in this order.
        self._pm_waiters.setdefault(block.pm_trig, []).append(block.id)
    return on_wait


def _make_pm_trigger_wake(self: SingleRunDriver, block: RBDBlock):
    scheduler = self.scheduler
    cascade = self.cascade
    def wake() -> None:
        # Raptor 7's `SetInterruptReason
        # ("Triggered_PM_Due"); IF FailWait: Interrupt(SELF,"Run")
        # ELSIF ZeroWait: BlockTrigger[seqNum].Release`.
        if block.id in cascade.handles:
            scheduler.interrupt(cascade.handles[block.id], InterruptReason.TRIGGERED_PM_DUE)
    return wake


# Resource/spare pools reset to full capacity each run, consistent
# with Custom sparing's per-run reset.
def _new_queue(self: SingleRunDriver, initial: int, *, is_resource: bool = False) -> ResourceQueue:
    # `time_fn`/`begin_run` wire up the per-run stock statistics the
    # End_Of_Run report's SparePool/Resource_Information tables need.
    scheduler = self.scheduler
    q = ResourceQueue(resources=initial, time_fn=lambda: scheduler.time,
                      is_resource_pool=is_resource)
    q.begin_run(initial, 0.0)
    return q


def _reset_all_statistics(self: SingleRunDriver, t: float) -> None:
    # Reset the cascade's own monitors and every pool's stock monitor
    # plus wait/arrival counters, without
    # altering the physical allocations. Custom-sparing queues are
    # filled in lazily, so read the registry at call time.
    cascade = self.cascade
    resource_queues = self._resource_queues
    spare_queues = self._spare_queues
    custom_spares_registry = self._custom_spares_registry
    cascade.reset_all_statistics(t)
    for q in (*resource_queues.values(), *spare_queues.values(),
              *custom_spares_registry.values()):
        q.reset_statistics(t)


def _wake_block(self: SingleRunDriver, block_id: int, reason: str,
               kind: str | None = None) -> None:
    # `kind` ("pm"/"repair") rides along as the interrupt token so the
    # resumed wait can tell which of its outstanding requests this
    # grant belongs to (see `Scheduler.interrupt`'s `token`).
    scheduler = self.scheduler
    cascade = self.cascade
    # A spare or resource grant (`WAIT FOR Pool TO Give`) resumes its waiter through a delivery record of a
    # per-request owner, not the block's own (gdb trace of LadL2Pool run 27 t=4000, 2026-10-01: both restock
    # wakes have `own=` objects that differ from the blocks'), exactly as the resource pool's own wakes do in
    # `_Cascade.apply`. At the run-end instant `ChooseNext` then sees non-block owners and takes the first.
    cascade._give_seq += 1
    scheduler.interrupt(cascade.handles[block_id], reason,
                        token=kind, gc_owner=Owner.give(cascade._give_seq))


def _make_spawn_order_a_spare(self: SingleRunDriver, pool_config, pool_queue):
    scheduler = self.scheduler
    logs = self.logs
    run_pool_stats = self.run_pool_stats
    def spawn(*, deferred: bool = False) -> None:
        # `deferred=True` (the failure path of `block_run_process`):
        # a real TELL start, queued at the END of the instant like any
        # TELL, OWNED BY THE POOL (`owner_key`): the owner-group
        # calendar then serves the pool's second same-instant order
        # only after every other owner's first activity -- exactly
        # Raptor 7's placement in PooledGraceRepairProbe/
        # PooledLDTOrderProbe/NoRepairCheckForSparePooledProbe
        # (2026-09-23). It used to be `front_tier` (pool processes
        # always first), a stand-in for ChooseNext's "non-block
        # first", which source only enables at the run-end instant.
        # Synchronous callers with no such seam (the PM path and the
        # dependent-block lifecycle) keep `front_tier` -- unverified
        # either way.
        scheduler.tell(order_a_spare_process(
            pool_config, pool_queue, scheduler, self._wake_block, logs, events_file=True,
            pool_stats=run_pool_stats.get(pool_config.pool_name),
        ), front_tier=not deferred, deferred=deferred,
            owner_key=Owner.pool(pool_config.pool_name))
    return spawn


def _make_spawn_order_stock(self: SingleRunDriver, pool_config, pool_queue):
    scheduler = self.scheduler
    logs = self.logs
    run_pool_stats = self.run_pool_stats
    def spawn(quantity: int) -> None:
        # `schedule_process`, not `tell` -- SLO's `TELL PooledSpares
        # TO OrderStock` is deferred to the spawning block's own next
        # yield in the Raptor 7 engine. Owned by the pool, plain end-of-
        # instant order -- NOT `front_tier` any more (2026-09-23):
        # that made `CS_NoAutosFirst`/`DepHierProbe`/
        # `ColdStandbyProbe2`/`CS_SwitchFail` log `N_Spare(s)_ordered`
        # ahead of same-instant block work; with the pool as owner all
        # four are byte-exact.
        scheduler.schedule_process(order_stock_process(
            pool_config, pool_queue, scheduler, self._wake_block, logs, quantity, events_file=True,
            pool_stats=run_pool_stats.get(pool_config.pool_name),
        ), owner_key=Owner.pool(pool_config.pool_name))
    return spawn
