"""Port of `RBDEventObj.ConductEvent`/`ChangeEventState`.

Source: Raptor 7's event object.

Covers the per-event state machine, event-log line formatting and weak-link
time/cost bookkeeping. `conduct_event`/`change_event_state` compute and return
`(run_change, fail_change)` but do not act on them -- the caller
(`orchestrator.py`) wires the cascade, including the `NumCurrentlyFailed`
increment on a failed event (`system_status.event_fail_count_delta`), the
parent hierarchy's failed count, and phase changes (`cascade_phase.py`).
Not ported here: `GraphicsOutput`-gated drawing and `FEVmode` (the Failure
Effects Viewer, a GUI-driven what-if mode).

Checked against `tests/golden/Events` in `tests/unit/test_events_engine.py`.
"""
from __future__ import annotations

from dataclasses import dataclass

from raptor.core.engine.eventlog import format_event_log_line
from raptor.core.model import ActiveStatus, EventStatus, RBDEvent
from raptor.core.rng import Distribution, RandomSource


@dataclass
class EventStatistics:
    """Per-event accumulators that live as module-level `seqNum`-indexed
    arrays in the source (`EventStandbyTime`/`EventRunTime`/`EventRepairTime`/
    `EventOpCost`/`EventRepCost`), kept as a small per-event
    object here."""

    standby_time: float = 0.0
    run_time: float = 0.0
    repair_time: float = 0.0
    op_cost: float = 0.0
    rep_cost: float = 0.0
    #: Weak-link analysis' `eventMissionBool` / `eventMCompleted`
    #: (initialised true/0 per run). The
    #: event-side twin of `BlockStatistics`' own pair: cleared the moment this event
    #: fails mid-mission, credited at every mission
    #: end still holding it, and read by
    #: EVENT_RESULTS' `R` column as `eventMCompleted[i] / missionsAttempted`.
    mission_bool: bool = True
    m_completed: int = 0


def conduct_event(
    event: RBDEvent,
    stats: EventStatistics,
    rng: RandomSource,
    sim_time: float,
    *,
    cost_analysis: bool = False,
    weak_analysis: bool = False,
    events_file: bool = False,
    current_phase: int = 1,
    in_mission: bool = False,
) -> tuple[list[str], int, int]:
    """Port of `ConductEvent` (draws success/failure, dispatches to
    `change_event_state`). Returns `(event_log_lines, run_change,
    fail_change)` -- the log lines produced (empty if `events_file` is False)
    plus `change_event_state`'s own `RunChange`/`FailChange` locals, forwarded
    for a caller-side cascade. Skips `FEVmode` (see module docstring).

    Every `CASE oldOpStatus`/phasing sub-branch in Raptor 7 calls
    `ChangeEventState` with the *same* target status, so the phasing
    conditionals are textually identical and this port does not reproduce
    their conditional structure.
    """
    # Port of ConductEvent's phased branch:
    # `fireVals[1]:=failVals[1]` (the event's own static fail_mean) is the
    # default, overridden by `phaseValue[phaseNumber]` only when the current
    # phase's type is "A". Other phase types carry a zero placeholder in
    # `phase_value`, not a real probability override.
    fire_vals = [event.fail_mean]
    if event.uses_phasing and event.phase_type and event.phase_type[current_phase - 1] == "A":
        fire_vals = [event.phase_value[current_phase - 1]]
    draw = rng.draw_number(Distribution(event.fail_distro), fire_vals)

    if draw == 1.0:  # success
        logs, run_change, fail_change = change_event_state(
            event, stats, EventStatus.SUCCESS, ActiveStatus.ACTIVE, "Fired",
            sim_time, weak_analysis=weak_analysis, events_file=events_file,
            in_mission=in_mission,
        )
        if cost_analysis:
            stats.op_cost += event.operating_cost
    else:  # failure
        logs, run_change, fail_change = change_event_state(
            event, stats, EventStatus.FAILURE, ActiveStatus.ACTIVE, "Fired",
            sim_time, weak_analysis=weak_analysis, events_file=events_file,
            in_mission=in_mission,
        )
        if cost_analysis:
            stats.rep_cost += event.repairing_cost
        # source: `IF AllowReconfigureSystem THEN ReconfigureSystem(0)` when
        # old status was Armed and not phasing — system-level, not acted on
        # here, see module docstring.
    return logs, run_change, fail_change


def change_event_state(
    event: RBDEvent,
    stats: EventStatistics,
    new_op_status: EventStatus,
    new_active_status: ActiveStatus,
    comment: str,
    sim_time: float,
    *,
    weak_analysis: bool = False,
    events_file: bool = False,
    in_mission: bool = False,
) -> tuple[list[str], int, int]:
    """Port of `ChangeEventState`, minus the `GraphicsOutput` block and the
    system-level reconfiguration cascade (see module docstring).

    Returns `(event_log_lines, run_change, fail_change)` -- the last two
    are the source's `RunChange`/`FailChange` locals, returned for a
    caller-side cascade (the caller acts when `abs(run_change) +
    abs(fail_change) == 2`, matching the source's own assertion).
    """
    old_op_status = event.op_status
    old_active_status = event.active_status
    event.op_status = new_op_status
    event.active_status = new_active_status

    state_time = sim_time - event.state_start_time
    if weak_analysis:
        if old_op_status == EventStatus.ARMED:
            stats.standby_time += state_time
        elif old_op_status == EventStatus.SUCCESS:
            stats.run_time += state_time
        elif old_op_status == EventStatus.FAILURE:
            stats.repair_time += state_time
        # Raptor 7 -- failing mid-mission forfeits this
        # event's mission credit, with the same `comment<>"End_sim"`
        # exclusion the block side uses (`blocks.py`), so the run-end flush
        # doesn't retroactively fail every event. The source nests this inside
        # the `weakAnalysis` guard, unlike the node rule, and so does this port.
        if in_mission and comment != "End_sim" and new_op_status == EventStatus.FAILURE:
            stats.mission_bool = False

    event.state_start_time = sim_time

    logs: list[str] = []
    if events_file:
        active_string = {
            ActiveStatus.CUT: "Cut", ActiveStatus.LINKED: "Linked", ActiveStatus.ACTIVE: "Active",
        }[new_active_status]
        status_string = {
            EventStatus.ARMED: "Armed", EventStatus.SUCCESS: "Success", EventStatus.FAILURE: "Failed",
        }[new_op_status]
        action_string = status_string if new_active_status == ActiveStatus.ACTIVE else active_string
        comment_string = comment
        if comment != "Back_To_GUI":
            logs.append(
                format_event_log_line(
                    sim_time, "Event", event.name, action_string,
                    comment_string if comment_string else "change_event_state",
                )
            )

    # RunChange/FailChange computation -- returned for the caller to act on
    # (see module docstring).
    #
    # The five-branch ladder below is DELIBERATELY repeated in
    # `blocks.py` and `nodes.py`. It is not an oversight: the source
    # carries the identical prologue in three separate procedures --
    # Raptor 7 and
    # Raptor 7 -- so the repetition is what makes each
    # function diffable against its Raptor 7 original. Only the tails
    # differ, and `nodes.py` renames every variable, so the shared part is
    # not even textually greppable. Do not "de-duplicate" this: an
    # extraction would make the port less auditable against three
    # procedures for no correctness gain.
    run_change = fail_change = 0
    if old_active_status == ActiveStatus.LINKED and new_active_status == ActiveStatus.LINKED:
        pass
    elif old_active_status == ActiveStatus.CUT and new_active_status == ActiveStatus.CUT:
        pass
    elif old_active_status == ActiveStatus.CUT and new_active_status == ActiveStatus.LINKED:
        run_change, fail_change = 1, -1
    elif old_active_status == ActiveStatus.LINKED and new_active_status == ActiveStatus.CUT:
        run_change, fail_change = -1, 1
    elif old_active_status == ActiveStatus.LINKED:  # now Active
        if new_op_status == EventStatus.FAILURE:
            run_change, fail_change = -1, 1
    elif old_active_status == ActiveStatus.CUT:  # now Active
        if new_op_status in (EventStatus.ARMED, EventStatus.SUCCESS):
            run_change, fail_change = 1, -1
    elif old_op_status == EventStatus.ARMED:
        if new_active_status == ActiveStatus.CUT or new_op_status == EventStatus.FAILURE:
            run_change, fail_change = -1, 1
    elif old_op_status == EventStatus.SUCCESS:
        if new_active_status == ActiveStatus.CUT or new_op_status == EventStatus.FAILURE:
            run_change, fail_change = -1, 1
    elif old_op_status == EventStatus.FAILURE:
        if new_active_status == ActiveStatus.LINKED or new_op_status == EventStatus.SUCCESS:
            run_change, fail_change = 1, -1

    return logs, run_change, fail_change
