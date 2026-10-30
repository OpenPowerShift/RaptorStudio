"""The cascade's system status: the deferred end-of-instant
`AnalyzeSystem` check, the capacity-only re-analysis, and the end of the
simulation (`CallEndSimulation` / `EndSimulation`).

These are `_Cascade` methods, bound in the class body
(`name = cascade_status.name`); each takes the cascade as `self`.
"""
from __future__ import annotations

from typing import TYPE_CHECKING

from raptor.core.engine.blocks import change_block_state
from raptor.core.engine.capacity import analyze_capacity
from raptor.core.engine.errors import StopRun
from raptor.core.engine.errors import require
from raptor.core.engine.events import change_event_state
from raptor.core.engine.nodes import change_node_state
from raptor.core.engine.system_status import SystemColor, analyze_system, change_system_status, red_change_line
from raptor.core.model import BlockStatus, EventStatus, NodeStatus
from raptor.core.scheduler import Process

if TYPE_CHECKING:
    from raptor.core.engine.orchestrator import _Cascade


def _analyze_capacity_only(self: _Cascade, sim_time: float) -> None:
    """The capacity half of `AnalyzeSystem`, for an instant that changed
    state but never got a full status check (its transitions were all
    suppressed or nested). Raptor 7's `TimeAdvance` analyses the flow at the
    end of every such instant; without this a link change under a
    suppressed check would leave `NodeFlow`/`NodeCapacity` stale until
    the next check. Deliberately not a full `_check_system_status`: an
    extra full check re-runs non-idempotent hierarchy/weak bookkeeping."""
    cs = self.capacity_state
    if cs is None or not cs.cap_analysis_required:
        return
    new_color = analyze_system(self.system_state)
    if new_color == SystemColor.RED and self.system_state.color == SystemColor.RED:
        cs.cap_analysis_required = False  # AnalyzeSystem's own quirk: a system staying Red clears it
        return
    analyze_capacity(
        cs,
        new_sys_status_red=(new_color == SystemColor.RED),
        sim_time=sim_time,
        cap_node_group=self.model.cap_node_group,
        cap_link_ids=self.model.cap_link_ids,
        nodes_by_id=self.model.nodes_by_id,
        links_by_id=self.model.links_by_id,
        start_node=self.model.nodes_by_id[require(self.model.start_node_id, "start_node_id")],
        end_node=self.model.nodes_by_id[require(self.model.end_node_id, "end_node_id")],
        flow_generated=self.model.flow_generated,
        flowing_at_max_cap=cs.flowing_at_max_cap,
    )
    cs.cap_analysis_required = False


def _reconcile_hierarchies(self: _Cascade, sim_time: float) -> None:
    """`AnalyzeSystem`'s hierarchy reconciliation: a hierarchy with a failed
    element inside whose Out node still reads AllUp is Degraded, and back to AllUp once nothing inside
    has failed. `ChangeNodeState` only, so no event-log line; its weak-link effect is the hierarchy's
    %Yellow (`HierDegradeWeakProbe`, real 2026-09-30)."""
    for hier in self.model.hiers_by_id.values():
        out_node = self.model.nodes_by_id[hier.out_id]
        if hier.num_currently_failed > 0 and out_node.status == NodeStatus.ALL_UP:
            self._cascade_node(out_node, NodeStatus.DEGRADED, sim_time)
        elif hier.num_currently_failed == 0 and out_node.status == NodeStatus.DEGRADED:
            self._cascade_node(out_node, NodeStatus.ALL_UP, sim_time)


def _check_system_status(self: _Cascade, sim_time: float) -> None:
    self._system_state_changed = False
    self.status_checks += 1       # `AnalyzeSystem` ran (a stepped session pauses after each)
    new_color = analyze_system(self.system_state)
    _reconcile_hierarchies(self, sim_time)
    # Capacity trigger lifecycle (`AnalyzeSystem`,
    # Raptor 7): SimTime=0 always (re)arms the
    # analysis; a system staying Red clears a pending link-change
    # request; the flag is consumed at the end regardless.
    run_capacity_now = False
    if self.capacity_state is not None:
        if sim_time == 0.0:
            self.capacity_state.cap_analysis_required = True
        if new_color == SystemColor.RED and self.system_state.color == SystemColor.RED:
            self.capacity_state.cap_analysis_required = False
        run_capacity_now = self.capacity_state.cap_analysis_required
    old_color = self.system_state.color
    sys_lines = change_system_status(
        self.system_state, new_color, sim_time, self._all_blocks, events_file=True,
        system_red_cost=self.model.system_red_cost if self.model.cost_analysis else 0.0,
        flagged_blocks=self.flagged_blocks(),
    )
    if new_color == SystemColor.RED and old_color != SystemColor.RED:
        # `ChangeStatus`: the system turning Red switches tie-breaking
        # off, so a run-end instant that goes Red after `CallEndSimulation` falls back to
        # plain calendar order (`DepCycleMixedProbe` runs 5 and 8, gdb trace 2026-10-01:
        # `SetTieBreaking(FALSE)` from `ChangeStatus`, then `EndSimulation` ahead of the
        # interrupted Run). Already Red: no colour change, `ChooseNext` stays live.
        self.scheduler.switch_off_tie_breaking()
    if sys_lines:
        self.logs.extend(sys_lines)
    elif not self._quiet_phase_boundary:
        red_line = red_change_line(self.system_state, sim_time, self._all_blocks, events_file=True,
                                   flagged_blocks=self.flagged_blocks())
        if red_line is not None:
            self.logs.append(red_line)
    if self.capacity_state is not None:
        if run_capacity_now:
            analyze_capacity(
                self.capacity_state,
                new_sys_status_red=(new_color == SystemColor.RED),
                sim_time=sim_time,
                cap_node_group=self.model.cap_node_group,
                cap_link_ids=self.model.cap_link_ids,
                nodes_by_id=self.model.nodes_by_id,
                links_by_id=self.model.links_by_id,
                start_node=self.model.nodes_by_id[require(self.model.start_node_id, "start_node_id")],
                end_node=self.model.nodes_by_id[require(self.model.end_node_id, "end_node_id")],
                flow_generated=self.model.flow_generated,
                flowing_at_max_cap=self.capacity_state.flowing_at_max_cap,
            )
        self.capacity_state.cap_analysis_required = False


# `block_env.CascadePort`: what a block's process calls back into.
def check_system_status(self: _Cascade, sim_time: float) -> None:
    """`CascadePort.check_system_status`: `_flush_and_check_system_status`."""
    self._flush_and_check_system_status(sim_time)


def _flush_and_check_system_status(self: _Cascade, sim_time: float) -> None:
    """`_flush_cold_changes` then `_check_system_status`, in that order
    -- the same pair `apply`'s `top_level and not wake_ids` gate runs.
    Used by `block_lifecycle.repair_completion_status`'s `needs_recheck`
    branch, which reaches `_check_system_status` without going through
    `apply`'s gate and so never flushed cold changes on its own."""
    self._flush_cold_changes(sim_time)
    self._schedule_or_check_system_status(sim_time)


def _schedule_or_check_system_status(self: _Cascade, sim_time: float) -> None:
    """The system-status-check half of a top-level completion's "flush
    then check" pair, made same-instant-tie-aware. Defers the check via
    a `_pending_status_check_dirty` flag rather than checking
    immediately, so it fires when the calendar's next entry leaves this
    instant, after every same-instant turn segment. Deduplicated by
    `_pending_status_check_time` so N simultaneous completions schedule
    only one check."""
    if self.phase_change_in_progress:
        # See `phase_change_in_progress`'s own docstring -- source's
        # Run/Repair never AnalyzeSystem, so a top-level completion
        # landing inside an active phase change queues NO check of
        # its own; the phase change's end-of-body pair covers it.
        return
    # Always take the deferred end-of-instant route. An immediate check
    # would reorder output whenever the executing turn still had event
    # lines left to emit after its cascade finished; Raptor 7's `AnalyzeSystem`
    # is a property of `TimeAdvance` itself, i.e. structurally lazy, and
    # runs once at the end of whichever instant went dirty, stamped with
    # that instant's SimTime. The dirty flag plus `run_until`'s per-step
    # hook (kickoff's on_step wiring) replicate that.
    if self._pending_status_check_time == sim_time:
        return  # another same-instant completion already scheduled one
    self._pending_status_check_time = sim_time
    # The old mechanism scheduled the check as a zero-delay calendar
    # entry, a same-instant competitor that could pop inside another
    # block's multi-segment turn and compute the color comment from
    # half-settled states. Source has no such entry: `TimeAdvance` runs
    # `IF SystemStateChange THEN AnalyzeSystem` at its own start, lazily,
    # once at the end of whichever instant went dirty, stamped with that
    # instant's SimTime. The dirty flag plus `run_until`'s per-step hook
    # (kickoff's on_step wiring) replicate that: the check fires when the
    # calendar's next entry leaves this instant, after every same-instant
    # turn segment.
    self._pending_status_check_dirty = True


def _deferred_check_process(self: _Cascade, sim_time: float) -> Process:
    self._pending_status_check_time = None
    self._check_system_status(sim_time)
    return
    yield  # pragma: no cover -- unreachable, see block_run_process's own kickoff idiom


def _end_simulation_now(self: _Cascade) -> None:
    """Port of `EndSimulation`'s body: every
    node silently resets to AllUp, every block logs Done/"End_sim" at
    the current SimTime (with disposal charged), every event logs
    Armed/"End_sim"; then `StopRun` unwinds `run_until`. Used by both
    termination paths: FailSim's queued process and CycleSim's
    synchronous call."""
    end_t = self.scheduler.time
    self.run_ended = True
    self.end_sim_time = end_t
    for n in self.model.nodes_by_id.values():
        # The node's own live stats, not a throwaway: this flush closes
        # each node's final status segment, which weak-link analysis
        # counts (+). A hierarchy Out node still Down
        # or NodePM at run end also gets one more `hierDE`
        # counted here directly.
        if (self.model.weak_analysis and n.type_node == 5
                and n.status in (NodeStatus.DOWN, NodeStatus.PM)):
            _h = self.model.hiers_by_id.get(n.parent_id)
            if _h is not None:
                _h.down_event_count += 1
        change_node_state(n, self.node_stats[n.id], NodeStatus.ALL_UP,
                          n.active_status, end_t,
                          weak_analysis=self.model.weak_analysis,
                          # Same `typeNode<>1` gate the ordinary
                          # cascade uses -- the Start node
                          # accumulates no state time here either.
                          is_start_or_end_node=n.type_node == 1)
    for b in self.model.blocks_by_id.values():
        block_statistics = self.stats_by_block[b.id]
        change_logs, *_ = change_block_state(
            b, block_statistics, BlockStatus.DONE, b.active_status, "End_sim",
            end_t, events_file=True,
            cost_analysis=self.model.cost_analysis,
            weak_analysis=self.model.weak_analysis,
            pool_stats=self.pool_stats_by_name.get(b.resource_pool_name),
            run_completed=True,
        )
        self.logs.extend(change_logs)
    for e in self.model.events_by_id.values():
        end_logs, *_ = change_event_state(
            e, self.stats_by_event[e.id], EventStatus.ARMED, e.active_status,
            "End_sim", end_t, weak_analysis=self.model.weak_analysis, events_file=True,
        )
        self.logs.extend(end_logs)
    # `IF SystemStateChange: AnalyzeSystem; SystemStateChange:=FALSE`
    # after the End_sim lines.
    # `ChangeBlockState` sets `SystemStateChange` unconditionally
    # so with any block the End_sim transitions
    # above guarantee this runs; like every `AnalyzeSystem` it logs
    # only on a color change. Matches the real corpus exactly: the 600+
    # runs that END Red/Yellow re-analyze to the same color (no line);
    # the only trailing `State_change` lines are runs whose final
    # instant held a recovery whose own lazy end-of-instant check was
    # cut off by the run ending -- `EventDepProbe` (11 runs, Red ->
    # Green `_d3_d4`) and `PMGrantVsRepairSpareProbe` (Red -> Yellow
    # `_d3_d4`). The silent node reset above does not touch
    # `system_up`, and the End_sim transitions' deltas are discarded,
    # so the color reflects the true pre-EndSimulation state, as Raptor 7's
    #Yellow shows. An events-only model (no blocks) is left alone: no
    # capture evidences it and its Armed/End_sim lines set the flag via
    # `ChangeEventState` only.
    #
    # NOT the general `_check_system_status`: `EndSimulation` sets
    # `runCompleted:=TRUE` first, and `AnalyzeSystem` gates both
    # its Red->Red `Red_change` line and its capacity analysis on `NOT
    # runCompleted`, so here only a genuine
    # color change (`ChangeStatus`) can log. (Its hierarchy Out-node
    # reconciliation loop is the ER3-14 gap, unported everywhere.)
    if self.model.blocks_by_id:
        self._pending_status_check_dirty = False
        self._pending_status_check_time = None
        self._system_state_changed = False
        self.logs.extend(change_system_status(
            self.system_state, analyze_system(self.system_state), end_t, self._all_blocks,
            events_file=True,
            system_red_cost=self.model.system_red_cost if self.model.cost_analysis else 0.0,
        ))
    raise StopRun()


def _end_sim_process(self: _Cascade) -> Process:
    """FailSim's scheduled `CallEndSimulation` body; delegates to
    `_end_simulation_now`."""
    def process() -> Process:
        self._end_simulation_now()
        yield 0.0  # pragma: no cover -- _end_simulation_now raises
    return process()
