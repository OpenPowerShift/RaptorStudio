"""Per-block accounting on a state change (split out of `blocks.change_block_state`).

`change_block_state` used to interleave three jobs: charging the state just
left (costs, weak-link times), writing the event-log line, and working out
the node/system count changes. The first is the run's accounting, and it does
not feed back into the other two, so it lives here as two observers of a typed
`StateChange`: `CostAccountant` (cost analysis) and `WeakLinkAccountant`
(weak-link analysis). Both are called synchronously, in that order, from
`change_block_state`, so the arithmetic and the order of every accrual and of
the one log line they can write (`Partial_Repair`) are those of the inline code
they replaced.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

from raptor.core.engine.eventlog import format_event_log_line
from raptor.core.model import BlockStatus, RBDBlock

if TYPE_CHECKING:
    from raptor.core.engine.blocks import BlockStatistics
    from raptor.core.engine.pool import PoolStats


@dataclass(frozen=True, slots=True)
class StateChange:
    """A block leaving one `BlockStatus` for another: what an accountant needs.

    `state_time` is how long the block sat in `old_op_status`. `in_mission`
    is `System.missionStatus=Mission` at this instant; `run_completed` is true
    only for the end-of-run `End_sim` flush.
    """

    block: RBDBlock
    old_op_status: BlockStatus
    new_op_status: BlockStatus
    comment: str
    sim_time: float
    state_time: float
    in_mission: bool = False
    run_completed: bool = False


def _pool_cost_per_time(block: RBDBlock) -> float:
    """`PoolArray[resPoolNum].costPerTime` -- resolved once per model at
    load time (`load_model` sets `block.res_cost_per_time` from the block's
    `RESOURCE_DATA` row), since `RBDBlock` carries the pool by name
    (`resource_pool_name`), not by config reference. Returns 0.0 when never wired."""
    return block.res_cost_per_time


class CostAccountant:
    """Charges the state a block just left to its `BlockStatistics` cost fields
    (port of `ChangeBlockState`'s cost-analysis section)."""

    def __init__(self, stats: BlockStatistics, pool_stats: PoolStats | None, events_file: bool) -> None:
        self.stats = stats
        self.pool_stats = pool_stats
        self.events_file = events_file

    def on_change(self, change: StateChange) -> list[str]:
        """Accrue the cost; returns the event-log lines it wrote (at most the
        `Partial_Repair` line), which come before the transition's own line."""
        stats = self.stats
        pool_stats = self.pool_stats
        events_file = self.events_file
        block = change.block
        old_op_status = change.old_op_status
        new_op_status = change.new_op_status
        sim_time = change.sim_time
        state_time = change.state_time
        run_completed = change.run_completed
        logs: list[str] = []
        if old_op_status == BlockStatus.IDLE:
            stats.idle_cost += block.idle_cost * state_time
        elif old_op_status == BlockStatus.REP_HOLD:
            stats.hold_cost += block.rep_hold_cost * state_time
        elif old_op_status == BlockStatus.REPAIRING:
            stats.rep_cost += block.repairing_cost * state_time + block.rep_fixed_cost
            # Port of ChangeBlockState's resource-pool time cost
            #: a block borrowing from a resource
            # pool (numDiffRes=1) accrues `stateTime*costPerTime*numRes1`
            # against the pool for as long as it sits Repairing.
            if pool_stats is not None and block.resource_type_count == 1:
                pool_stats.add_cost += (
                    state_time * _pool_cost_per_time(block) * float(block.repair_resources_needed)
                )
            # Port of `ChangeBlockState`'s `IF (RepWait): WriteEvents(...,
            # "Partial_Repair","RepairTime="+REALTOSTR(stateTime))`
            #: logged whenever a transition away
            # from `Repairing` happens while `rep_wait` is still set, i.e. the
            # repair never reached its own natural completion (the only place
            # that clears it, in `block_lifecycle.block_run_process`).
            if events_file and block.run_state.rep_wait:
                logs.append(format_event_log_line(
                    sim_time, "Block", block.name, "Partial_Repair", f"RepairTime={state_time:.6f}",
                ))
        elif old_op_status == BlockStatus.RUNNING:
            stats.op_cost += block.operating_cost * state_time
        elif old_op_status == BlockStatus.STANDBY:
            stats.sb_cost += block.standby_cost * state_time
        elif old_op_status == BlockStatus.PM_HOLD:
            stats.pm_hold_cost += block.pm_hold_cost * state_time
        elif old_op_status == BlockStatus.PM:
            stats.pm_cost += block.pm_cost * state_time + block.pm_fixed_cost
            # Raptor 7 -- the PM twin of the Repairing
            # resource-pool time cost above (numRes1PM units).
            if pool_stats is not None and block.resource_type_count == 1:
                pool_stats.add_cost += (
                    state_time * _pool_cost_per_time(block) * float(block.pm_resources_needed)
                )
        elif old_op_status == BlockStatus.DONE and new_op_status == BlockStatus.DONE:
            stats.done_cost = block.done_cost * state_time  # source: assignment, not +=
            stats.disp_cost = block.done_fixed_cost
        elif old_op_status == BlockStatus.DONE and new_op_status != BlockStatus.DONE:
            pass
        # source raises "Error in cost analysis section!" for any other
        # combination — every BlockStatus value is covered above, so no
        # unreachable branch to port here.
        if new_op_status == BlockStatus.DONE and old_op_status != BlockStatus.DONE:
            # Raptor 7 -- `IF ((opStatus=Done) AND
            # (oldOpStatus<>Done)): IF runCompleted AND alwaysAddDoneCost:
            # BlockDispCost:=doneFixedCost`. `run_completed` is true only at
            # the end-of-run "End_sim" flush, so a block reaching Done mid-run
            # (spare exhaustion, etc.) does not charge disposal then; every
            # block with the `.rbd` `addDone` column set charges it at the
            # run-end flush regardless of how it got there.
            if run_completed and block.always_add_done_cost:
                stats.disp_cost = block.done_fixed_cost

        return logs


class WeakLinkAccountant:
    """Accumulates the time in each state and the mission credit for the
    weak-link report (`ChangeBlockState`'s weak-link section)."""

    def __init__(self, stats: BlockStatistics) -> None:
        self.stats = stats

    def on_change(self, change: StateChange) -> None:
        stats = self.stats
        old_op_status = change.old_op_status
        new_op_status = change.new_op_status
        state_time = change.state_time
        comment = change.comment
        in_mission = change.in_mission
        if old_op_status == BlockStatus.RUNNING:
            stats.run_time += state_time
        elif old_op_status == BlockStatus.REPAIRING:
            stats.repair_time += state_time
        elif old_op_status == BlockStatus.REP_HOLD:
            stats.rep_hold_time += state_time
        elif old_op_status == BlockStatus.PM:
            stats.pm_time += state_time
        elif old_op_status == BlockStatus.PM_HOLD:
            stats.pm_hold_time += state_time
        elif old_op_status == BlockStatus.IDLE:
            stats.idle_time += state_time
        elif old_op_status == BlockStatus.STANDBY:
            stats.standby_time += state_time
        elif old_op_status == BlockStatus.DONE:
            stats.done_time += state_time
        # Raptor 7 -- entering a maintenance-like state
        # mid-mission forfeits this block's mission credit. `End_sim` is
        # excluded by the source's `IF comment<>"End_sim"`, so the run-end
        # flush does not retroactively fail every block.
        if (in_mission and comment != "End_sim"
                and new_op_status in (BlockStatus.REPAIRING, BlockStatus.REP_HOLD,
                                      BlockStatus.PM, BlockStatus.PM_HOLD,
                                      BlockStatus.DONE)):
            stats.mission_bool = False

