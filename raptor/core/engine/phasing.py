"""Port of `ControlPhaseChanges`/`ConductPhaseChange` — the phase-change scheduling loop and its per-object dispatcher.

Pure classification logic only (no engine-state mutation) lives here,
independently testable against the Cut/Link/Active/Fire/Prior tables in Raptor 7. The actual state mutation (`ChangePhases`, reusing
the already-ported `change_block_state`/`change_event_state`/
`change_node_state`) and the multi-`WAIT DURATION 0.0` staggered application
order live on `orchestrator._Cascade.conduct_phase_change` — a generator
method, because `ConductPhaseChange` is called directly (no `TELL` prefix)
from `ControlPhaseChanges`'s own already-running process, so its `WAIT
DURATION 0.0` calls all run on the same scheduler process.

`FEVPhaseChange` is GUI-only (no `WriteEvents`/
`ChangeXState` calls in the range), so it is not ported. `termType=3`
(cycle-terminated, `Phasing3.rbd`'s mode) is ported, including the
`StartStats` mid-run statistics reset.
"""
from __future__ import annotations

from typing import Literal
from collections.abc import Callable

from raptor.core.engine.eventlog import format_event_log_line
from raptor.core.engine.system_status import SystemState
from raptor.core.model import Phase
from raptor.core.rng import Distribution, RandomSource
from raptor.core.scheduler import Owner, Process, Scheduler
from raptor.core.engine.errors import EngineInvariantError

PhaseGroup = Literal["improve", "degrade", "stress"]


def control_phase_changes_process(
    phases: list[Phase],
    system_state: SystemState,
    rng: RandomSource,
    scheduler: Scheduler,
    conduct_phase_change: Callable[[int, float], Process],
    logs: list[str],
    *,
    events_file: bool = False,
    termination_type: str = "TimeSim",
    cycle_target: int | None = None,
    stop_criteria: float | None = None,
    end_simulation: Callable[[], None] | None = None,
    start_stats_cycles: int = 0,
    reset_statistics: Callable[[float], None] | None = None,
    set_draw_tag: Callable[[str | None], None] | None = None,
    begin_phase_change: Callable[[], None] | None = None,
) -> Process:
    """Port of `ControlPhaseChanges`. Register with
    `scheduler.schedule_process(...)`: the source's `TELL System TO
    ControlPhaseChanges()` spawns an independent process (a bare
    `TELL` queues a fresh top-level process rather than running inline),
    matching `Scheduler.schedule_process`.

    `conduct_phase_change` is `orchestrator._Cascade.conduct_phase_change`,
    spawned as a deferred TELL (see the call below); `begin_phase_change`
    sets `PhaseChangeInProgress` in this turn, before the spawn.

    `termType=3` (CycleSim): each wrap from the last
    phase back to phase 1 increments `SystemState.cycles_count` and a term
    counter. When the counter reaches `cycle_target` (`trunc(StopCriteria)`),
    the source calls `CallEndSimulation(FALSE,TRUE)` synchronously inside its
    own turn (not queued) and exits the loop — no `Begin_phase` log and no
    `ConductPhaseChange` for the wrapped-to phase 1. This port routes that
    through the caller-provided `end_simulation()`, which raises `StopRun` to
    unwind the scheduler. When the counter reaches `TRUNC(StartStats)`,
    `ResetAllStatistics` runs via the `reset_statistics` callback.
    """
    current_phase = 1
    phase = phases[current_phase - 1]
    if set_draw_tag is not None:
        set_draw_tag(phase.phase_name)
    phase_duration = rng.draw_number(Distribution(phase.dist), phase.params)
    if set_draw_tag is not None:
        set_draw_tag(None)

    while True:
        yield phase_duration
        # Raptor 7 -- TimeSim only: skip the phase change
        # entirely when SimTime is within 1e-7 of StopCriteria (the run
        # length), so the boundary emits no `Begin_phase_*` log and runs no
        # ConductPhaseChange.
        if (termination_type == "TimeSim" and stop_criteria is not None
                and abs(stop_criteria - scheduler.time) < 1e-7):
            return
        if current_phase == len(phases):
            # -- cycle-wrap bookkeeping. `cyclesCount+=1` happens
            # before the termType=3 end check; when the target cycle count is
            # reached, `CallEndSimulation` runs synchronously and the loop
            # exits: no wrapped-phase draw, no `Begin_phase_1` log line, no
            # ConductPhaseChange.
            system_state.cycles_count += 1.0
            if termination_type == "CycleSim":
                if not (end_simulation is not None and cycle_target is not None):
                    raise EngineInvariantError('invariant failed: end_simulation is not None and cycle_target is not None')
                system_state.term_counter += 1
                if system_state.term_counter == cycle_target:
                    # -- synchronous CallEndSimulation + EXIT.
                    end_simulation()
                    return  # unreachable -- end_simulation raises StopRun
                if start_stats_cycles and system_state.term_counter == start_stats_cycles:
                    # -- ResetAllStatistics mid-simulation
                    # (warm-up exclusion): zero every monitor, re-anchor the
                    # sim-time cursors at now, and restart mission/cycle
                    # bookkeeping from the current state
                    # (cascade.reset_all_statistics).
                    #
                    # `ResetAllStatistics` is a `TELL METHOD`
                    # i.e. fire-and-forget, so the blank
                    # line and "Started Gathering Statistics" marker it writes
                    # land after the current turn's own `Begin_phase_1` log.
                    if reset_statistics is None:
                        raise EngineInvariantError(
                            "control_phase_changes_process needs a reset_statistics callback for StartStats>0"
                        )

                    def _reset_statistics_process() -> Process:
                        # The deferred TELL start (`deferred=True` below) is the
                        # boundary; a leading `yield 0.0` here would count the
                        # reset twice.
                        reset_statistics(scheduler.time)
                        return
                        yield  # pragma: no cover -- makes this a generator, see block_run_process's idiom

                    scheduler.tell(_reset_statistics_process(), deferred=True)
            new_phase = 1
        else:
            new_phase = current_phase + 1
        current_phase = new_phase
        phase = phases[new_phase - 1]
        if set_draw_tag is not None:
            set_draw_tag(phase.phase_name)
        phase_duration = rng.draw_number(Distribution(phase.dist), phase.params)
        if set_draw_tag is not None:
            set_draw_tag(None)
        if events_file:
            logs.append(format_event_log_line(
                scheduler.time, "System", "Phase_change", "Phase_change", f"Begin_phase_{phase.phase_name}",
            ))
        if phase_duration > 0.0:
            # `PhaseChangeInProgress:=TRUE; ConductPhaseChange(newPhase)`.
            # `ConductPhaseChange` is a TELL METHOD: Raptor 7's trace (`PhaseFailTieProbe`, 2026-09-30) shows
            # an `I2 tell` here, the phase clock going straight on to its next `WAIT DURATION`, and the
            # body starting as its own selection at the tail of the instant -- after what the same turn
            # queued (a failing block's `Repair` start), not inline.
            if begin_phase_change is not None:
                begin_phase_change()
            # Passive for the capacity monitors: the phase machinery changes no flow by itself; any element it Cuts,
            # Links or re-stresses marks `SystemStateChange` and the instant is analysed (and recorded) anyway.
            scheduler.passive_handles.add(
                scheduler.tell(conduct_phase_change(new_phase, scheduler.time), deferred=True,
                               owner_key=Owner.system()).id)
        else:
            # Raptor 7 -- `ASK pmSyncTrigger TO Release` for a
            # zero-length phase (skips ConductPhaseChange entirely). No
            # fixture models pmSyncTrigger (the GUI's PM sync field is never
            # set in any .rbd), so the Release is a no-op; the phase
            # transition is still logged because the phase did change even
            # though its duration was zero.
            pass


def classify_block_phase_transition(old_type: str, new_type: str) -> PhaseGroup | None:
    """Raptor 7's Cut/Link/Active bucketing for a phased BLOCK
    (also used for a phased NODE's `RBDBlockObj`-shaped
    `BlockPhaseChangeGroup` membership; nodes otherwise use
    `classify_node_phase_transition`, a different k-value rule).

    The caller gates on `oldValue<>newValue OR oldType<>newType`, so this
    function is only reached when something differs.
    `old_type==new_type=="A"` with only the value differing (a value-only
    stress change) is a valid case and classifies as `"stress"`. `None`
    means "no group" and covers the other same-type cases (`"C"->"C"`,
    `"L"->"L"`), which the caller's gate already filters out.
    """
    if old_type == "C":
        return "improve"
    if old_type == "L":
        return "degrade"
    if old_type == "A":
        if new_type == "L":
            return "improve"
        if new_type == "C":
            return "degrade"
        return "stress"  # new_type == "A", value-only change
    return None  # old_type in {"C", "L"} with new_type == old_type -- not reachable via the caller's own gate


def classify_event_phase_transition(old_type: str, new_type: str, op_status_is_failure: bool) -> PhaseGroup | None:
    """Raptor 7's event-specific bucketing -- a superset of the
    block rule (`"C"`/`"L"` cases identical). `"A"`/`"F"` key off the event's
    own `opStatus` instead of a value/stress split: an event whose most recent
    outcome was a failure is an improvement when it fires again, and anything
    else (including a still-Armed event) is a degrade, since firing again
    risks a new failure.

    `op_status_is_failure` is `event.op_status == EventStatus.FAILURE` at the
    moment of classification, before this phase change touches it. `"P"`
    (Prior, no draw) is always `None` when values differ. This implements only
    the `oldValue<>newValue OR oldType<>newType` half; the same-type `"F"`/`"A"`
    refire case is handled by `classify_event_same_value_refire`.
    """
    if old_type == "C":
        return "improve"
    if old_type == "L":
        return "degrade"
    # old_type in {"A", "F", "P"} -- source's own ELSE branch 
    # catches anything not C/L, keyed purely on newType from here.
    if new_type == "L":
        return "improve"
    if new_type == "C":
        return "degrade"
    if new_type in ("A", "F"):
        return "improve" if op_status_is_failure else "degrade"
    return None  # new_type == "P"


def classify_event_same_value_refire(old_type: str, op_status_is_failure: bool) -> PhaseGroup | None:
    """Raptor 7's `ELSE` branch: a phased event whose
    `oldValue==newValue AND oldType==newType` (the outer `IF` is
    false) but whose `oldType` is still `"F"` or `"A"` -- firing in successive
    phases with the same probability both times. It still redraws, classified
    the same failure-status-keyed way as an actual type/value change.
    """
    if old_type not in ("F", "A"):
        return None
    return "improve" if op_status_is_failure else "degrade"


def classify_node_phase_transition(old_val: int, new_val: int, connect_into_num: int) -> PhaseGroup | None:
    """Port of Raptor 7's own node bucketing -- `-1`
    (Cut) remaps to `connectIntoNum+1` (an unsatisfiable k-requirement)
    before comparing, so "improve" always means "k-requirement went
    down" (easier to satisfy) and "degrade" means it went up, matching
    a plain `k`-value comparison once the Cut sentinel is normalized
    this way. `old_val`/`new_val` are the RAW `node.phase[...]` values
    (`-1`/`0`/a bare k-int) -- this function does the `-1` remap
    itself, callers should NOT pre-remap."""
    effective_old = connect_into_num + 1 if old_val == -1 else old_val
    effective_new = connect_into_num + 1 if new_val == -1 else new_val
    if effective_old == effective_new:
        return None
    return "improve" if effective_old > effective_new else "degrade"
