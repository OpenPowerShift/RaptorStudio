"""Port of `RBDBlockObj.ChangeBlockState` and the "block has failed" log line
from `DetermineNewState`.

Source: Raptor 7 for `change_block_state`, and Raptor 7's
`DetermineNewState` `WHEN "Block Has Failed"` branch for
`write_block_failed_line`.

Scope: this module ports `ChangeBlockState` only -- the state-transition,
logging and cost-bookkeeping method. The block's `Run`/`Repair`/
`DetermineNewState`/`PerformPM` cooperative processes, which draw failure and
repair times and decide when to call `ChangeBlockState`, live in
`block_lifecycle.py`. Given a transition, `change_block_state` produces
event-log output matching the Raptor 7 engine (checked against
`tests/golden/UniformProbe` and `tests/golden/Phasing1` in
`tests/unit/test_blocks_engine.py`).

The `RunChange`/`FailChange`/`IdleChange`/`StandbyChange`/`pmChange` deltas
are computed and returned for the caller to act on (`cascade.py`), not applied
here. `GraphicsOutput` drawing, cold-standby dependency handling
(`ColdChangeGroup`) and `FEVmode` are out of scope.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, NamedTuple

from raptor.core.engine.accounting import CostAccountant, StateChange, WeakLinkAccountant
from raptor.core.engine.eventlog import format_event_log_line
from raptor.core.model import ActiveStatus, BlockStatus, RBDBlock

if TYPE_CHECKING:
    from raptor.core.engine.pool import PoolStats


_STATUS_STRING = {
    BlockStatus.RUNNING: "Good",
    BlockStatus.REPAIRING: "Repairing",
    BlockStatus.REP_HOLD: "RepHold",
    BlockStatus.PM: "PM",
    BlockStatus.PM_HOLD: "pmHold",
    BlockStatus.IDLE: "Idle",
    BlockStatus.STANDBY: "Standby",
    BlockStatus.DONE: "Done",
}

_ACTIVE_STRING = {
    ActiveStatus.CUT: "Cut",
    ActiveStatus.LINKED: "Linked",
    ActiveStatus.ACTIVE: "Active",
}


class StateDeltas(NamedTuple):
    """`ChangeBlockState`'s per-status count changes (each -1, 0 or +1):
    the node/system cascade adds them to the target node's tallies."""

    run: int
    fail: int
    idle: int
    standby: int
    pm: int


class Transition(NamedTuple):
    """`change_block_state`'s result: the event-log lines it wrote and the
    count changes the caller must cascade."""

    logs: list[str]
    deltas: StateDeltas


@dataclass
class BlockStatistics:
    """Per-block accumulators -- the `seqNum`-indexed module-level arrays in Raptor 7 (`BlockRunTime`, `BlockRepairTime`, `BlockIdleCost`, etc.),
    kept as one small per-block object here (as `EventStatistics` in `events.py`)."""

    idle_cost: float = 0.0
    hold_cost: float = 0.0
    rep_cost: float = 0.0
    op_cost: float = 0.0
    sb_cost: float = 0.0
    pm_hold_cost: float = 0.0
    pm_cost: float = 0.0
    done_cost: float = 0.0
    disp_cost: float = 0.0
    #: `BlockSpCost[seqNum]` -- spare purchase cost:
    #: charged per consumption for Infinite sparing, per manufacture/order
    #: for Custom sparing (RSO arrival, ESO order, SLO order), never for pooled.
    sp_cost: float = 0.0
    #: `BlockESCost[seqNum]` -- emergency shipping,
    #: charged only by Custom sparing's ESO order.
    es_cost: float = 0.0
    run_time: float = 0.0
    repair_time: float = 0.0
    rep_hold_time: float = 0.0
    pm_time: float = 0.0
    pm_hold_time: float = 0.0
    idle_time: float = 0.0
    standby_time: float = 0.0
    done_time: float = 0.0
    #: Weak-link analysis' `blockMissionBool` / `blockMCompleted`
    #: `mission_bool` starts true each run and
    #: is cleared the moment this block enters a maintenance-like state during a
    #: mission; the counter is bumped for every
    #: still-true block when a mission ends. The report's `R` column is
    #: `m_completed / missions_attempted`.
    mission_bool: bool = True
    m_completed: int = 0

    def reset_cost_and_time(self) -> None:
        """Zero every cost and time accumulator, leaving the weak-analysis
        fields alone. One owner (review E-03).

        Port of `SetToInitValues`'s own zeroing (and its event twin's). This 19-field reset was written
        out **twice** — at kickoff (`kickoff.py`) and at the mid-run
        statistics reset (`orchestrator.py`) — and the two call sites are not
        alternatives: an execution trace showed **both fire in one run** on a
        `CycleSim` model with `StartStats > 0`.

        So this is not "two paths that must agree". It is one field list
        applied at two different instants, and the hazard is the obvious one:
        **add a cost or time accumulator and one of the two sites silently
        stops resetting it**, so run N+1's cost table accumulates on top of
        run N's. Neither site could be checked against the other by running
        it, because each is correct in isolation.

        The 19 names are **derived from the dataclass fields**, not listed
        again, so a new accumulator is covered by construction. Two fields are
        deliberately excluded and handled by the callers:

        - `mission_bool` / `m_completed` — not part of this reset. `kickoff`
          sets them unconditionally (which §2 traced as *more* faithful than
          the orchestrator's gated twin); the orchestrator sets them from the
          block's current status under `if self.model.weak_analysis`. Two
          different rules, so folding them in here would pick a winner by
          accident. See `TestMissionFieldsStayWithCallers`.
        """
        for name in cost_and_time_fields():
            setattr(self, name, 0.0)


def cost_and_time_fields() -> tuple[str, ...]:
    """Every `BlockStatistics` field `SetToInitValues` zeroes, derived (review E-03).

    Computed from the dataclass rather than listed, so adding a cost or time
    accumulator cannot leave one of the two reset sites stale. The exclusion is
    by *name and type* -- the two weak-analysis fields -- and anything that is
    not a float is excluded too, so a future bool or int accumulator is
    handled deliberately by its caller rather than zeroed here by accident.
    """
    fields = BlockStatistics.__dataclass_fields__
    return tuple(
        name for name, f in fields.items()
        if f.type in ("float", float)
        and name not in _WEAK_ANALYSIS_FIELDS
    )


#: Excluded from `reset_cost_and_time`: not part of `SetToInitValues`' cost
#: zeroing, and each call site applies its OWN rule to them.
_WEAK_ANALYSIS_FIELDS = frozenset({"mission_bool", "m_completed"})


def write_block_failed_line(block: RBDBlock, sim_time: float, operating_time: float) -> str:
    """Port of the `WriteEvents(SimTime,"Block",name,"Failed","Operating_time="
    +REALTOSTR(OperatingTime))` call in `DetermineNewState`'s `WHEN "Block
    Has Failed"` branch. Emitted before
    `change_block_state` transitions the block to `Repairing` (or `Done`, if
    unrepairable), so it documents the failure at the block's still-running
    moment rather than a `ChangeBlockState`-driven transition."""
    return format_event_log_line(
        sim_time, "Block", block.name, "Failed", f"Operating_time={operating_time:.6f}"
    )


def change_block_state(
    block: RBDBlock,
    stats: BlockStatistics,
    new_op_status: BlockStatus,
    new_active_status: ActiveStatus,
    comment: str,
    sim_time: float,
    *,
    phase_stress: float = 1.0,
    cost_analysis: bool = False,
    weak_analysis: bool = False,
    #: `System.missionStatus=Mission` at the moment of this transition, which
    #: gates the `blockMissionBool` clear. Callers that don't track missions
    #: leave it False, which only skips that clear.
    in_mission: bool = False,
    events_file: bool = False,
    pool_stats: PoolStats | None = None,
    run_completed: bool = False,
) -> Transition:
    """Port of `ChangeBlockState`. Returns a `Transition`: the event-log
    lines plus the `StateDeltas` (the source's run/fail/idle/standby/pm
    change locals) for the caller to cascade (see module docstring)."""
    old_op_status = block.op_status
    old_active_status = block.active_status
    block.op_status = new_op_status
    block.active_status = new_active_status
    if new_op_status == BlockStatus.RUNNING:
        # See `BlockRunState.run_needs_respawn`'s own docstring -- cleared
        # centrally here (any transition TO Running) rather than only at
        # the specific sites that set it, so it can't go stale across a
        # later, genuinely-live Idle episode.
        block.run_state.run_needs_respawn = False

    state_time = sim_time - block.state_start_time

    change = StateChange(block, old_op_status, new_op_status, comment, sim_time, state_time,
                         in_mission=in_mission, run_completed=run_completed)
    logs: list[str] = []
    if cost_analysis:
        logs += CostAccountant(stats, pool_stats, events_file).on_change(change)
    if weak_analysis:
        WeakLinkAccountant(stats).on_change(change)
    block.state_start_time = sim_time

    if events_file:
        active_string = _ACTIVE_STRING[new_active_status]
        status_string = _STATUS_STRING[new_op_status]
        comment_string = comment
        if new_active_status == ActiveStatus.ACTIVE:
            action_string = status_string
            if new_op_status == BlockStatus.RUNNING:
                comment_string = comment_string + f"Stress={phase_stress:.6f}"
        else:
            action_string = active_string
        if comment != "Back_To_GUI":
            logs.append(
                format_event_log_line(
                    sim_time, "Block", block.name, action_string,
                    comment_string if comment_string else "change_block_state",
                )
            )

    # RunChange/FailChange/IdleChange/StandbyChange/pmChange -- computed here
    # for the caller to act on (see module docstring).
    #
    # The five-branch ladder below is DELIBERATELY repeated in
    # `events.py` and `nodes.py`. It is not an oversight: the source
    # carries the identical prologue in three separate procedures --
    # Raptor 7 and
    # Raptor 7 -- so the repetition is what makes each
    # function diffable against its Raptor 7 original. Only the tails
    # differ, and `nodes.py` renames every variable, so the shared part is
    # not even textually greppable. Do not "de-duplicate" this: an
    # extraction would make the port less auditable against three
    # procedures for no correctness gain.
    run_change = fail_change = idle_change = standby_change = pm_change = 0
    if old_active_status == ActiveStatus.LINKED and new_active_status == ActiveStatus.LINKED:
        pass
    elif old_active_status == ActiveStatus.CUT and new_active_status == ActiveStatus.CUT:
        pass
    elif old_active_status == ActiveStatus.CUT and new_active_status == ActiveStatus.LINKED:
        run_change, fail_change = 1, -1
    elif old_active_status == ActiveStatus.LINKED and new_active_status == ActiveStatus.CUT:
        run_change, fail_change = -1, 1
    elif old_active_status == ActiveStatus.LINKED:  # now Active
        if new_op_status == BlockStatus.RUNNING:
            pass
        elif new_op_status in (BlockStatus.REPAIRING, BlockStatus.REP_HOLD, BlockStatus.DONE):
            run_change, fail_change = -1, 1
        elif new_op_status in (BlockStatus.PM, BlockStatus.PM_HOLD):
            run_change, pm_change = -1, 1
        elif new_op_status == BlockStatus.IDLE:
            run_change, idle_change = -1, 1
        elif new_op_status == BlockStatus.STANDBY:
            run_change, standby_change = -1, 1
    elif old_active_status == ActiveStatus.CUT:  # now Active
        if new_op_status == BlockStatus.RUNNING:
            run_change, fail_change = 1, -1
        elif new_op_status in (BlockStatus.REPAIRING, BlockStatus.REP_HOLD, BlockStatus.DONE):
            pass
        elif new_op_status in (BlockStatus.PM, BlockStatus.PM_HOLD):
            fail_change, pm_change = -1, 1
        elif new_op_status == BlockStatus.IDLE:
            fail_change, idle_change = -1, 1
        elif new_op_status == BlockStatus.STANDBY:
            fail_change, standby_change = -1, 1
    elif old_op_status == BlockStatus.RUNNING:
        if new_active_status == ActiveStatus.LINKED:
            pass
        elif new_active_status == ActiveStatus.CUT:
            run_change, fail_change = -1, 1
        elif new_op_status in (BlockStatus.REPAIRING, BlockStatus.REP_HOLD, BlockStatus.DONE):
            run_change, fail_change = -1, 1
        elif new_op_status in (BlockStatus.PM, BlockStatus.PM_HOLD):
            run_change, pm_change = -1, 1
        elif new_op_status == BlockStatus.IDLE:
            run_change, idle_change = -1, 1
        elif new_op_status == BlockStatus.STANDBY:
            run_change, standby_change = -1, 1
    elif old_op_status == BlockStatus.REPAIRING:
        if new_active_status == ActiveStatus.LINKED:
            run_change, fail_change = 1, -1
        elif new_active_status == ActiveStatus.CUT:
            pass
        elif new_op_status == BlockStatus.RUNNING:
            run_change, fail_change = 1, -1
        elif new_op_status == BlockStatus.REP_HOLD:
            pass
        elif new_op_status == BlockStatus.IDLE:
            fail_change, idle_change = -1, 1
        elif new_op_status == BlockStatus.STANDBY:
            fail_change, standby_change = -1, 1
    elif old_op_status == BlockStatus.REP_HOLD:
        if new_active_status == ActiveStatus.LINKED:
            run_change, fail_change = 1, -1
        elif new_active_status == ActiveStatus.CUT:
            pass
        elif new_op_status == BlockStatus.RUNNING:
            run_change, fail_change = 1, -1
        elif new_op_status == BlockStatus.REPAIRING:
            pass
        elif new_op_status == BlockStatus.IDLE:
            fail_change, idle_change = -1, 1
        elif new_op_status == BlockStatus.STANDBY:
            fail_change, standby_change = -1, 1
    elif old_op_status == BlockStatus.PM:
        if new_active_status == ActiveStatus.LINKED:
            run_change, pm_change = 1, -1
        elif new_active_status == ActiveStatus.CUT:
            fail_change, pm_change = 1, -1
        elif new_op_status == BlockStatus.RUNNING:
            run_change, pm_change = 1, -1
        elif new_op_status == BlockStatus.IDLE:
            idle_change, pm_change = 1, -1
        elif new_op_status == BlockStatus.STANDBY:
            standby_change, pm_change = 1, -1
    elif old_op_status == BlockStatus.PM_HOLD:
        if new_active_status == ActiveStatus.LINKED:
            run_change, pm_change = 1, -1
        elif new_active_status == ActiveStatus.CUT:
            fail_change, pm_change = 1, -1
        elif new_op_status == BlockStatus.PM:
            pass
    elif old_op_status == BlockStatus.IDLE:
        if new_active_status == ActiveStatus.LINKED:
            run_change, idle_change = 1, -1
        elif new_active_status == ActiveStatus.CUT:
            fail_change, idle_change = 1, -1
        elif new_op_status == BlockStatus.RUNNING:
            run_change, idle_change = 1, -1
        elif new_op_status in (BlockStatus.PM, BlockStatus.PM_HOLD):
            pm_change, idle_change = 1, -1
        elif new_op_status == BlockStatus.STANDBY:
            idle_change, standby_change = -1, 1
    elif old_op_status == BlockStatus.STANDBY:
        if new_active_status == ActiveStatus.LINKED:
            run_change, standby_change = 1, -1
        elif new_active_status == ActiveStatus.CUT:
            fail_change, standby_change = 1, -1
        elif new_op_status == BlockStatus.RUNNING:
            run_change, standby_change = 1, -1
        elif new_op_status in (BlockStatus.REPAIRING, BlockStatus.REP_HOLD, BlockStatus.DONE):
            fail_change, standby_change = 1, -1
        elif new_op_status in (BlockStatus.PM, BlockStatus.PM_HOLD):
            standby_change, pm_change = -1, 1
        elif new_op_status == BlockStatus.IDLE:
            idle_change, standby_change = 1, -1
    elif old_op_status == BlockStatus.DONE:
        if new_active_status == ActiveStatus.LINKED:
            run_change, fail_change = 1, -1
        elif new_active_status == ActiveStatus.CUT:
            pass

    return Transition(logs, StateDeltas(run_change, fail_change, idle_change, standby_change, pm_change))
