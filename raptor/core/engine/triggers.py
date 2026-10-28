"""Port of `RapTriggerObj.PullRapTrigger`.

A trigger is a self-contained periodic timer: draw a wait time (from
`SysStream[11]`, the same shared stream `cold_standby.set_switch_time` uses),
subtract `init_used` (retried up to 100 times if the result isn't positive,
matching `block_lifecycle.get_start_cond`'s retry loop), wait that long, log
`"Trigger"`/`name`/`"Fired"`/`"System_event"`, draw a fresh wait time (no
`init_used` subtraction this time), and repeat if `repeats` -- otherwise stop
after firing once.

Covers the fire-and-log behavior plus `Release`'s wake-up semantics for
triggered-mode PM (`RBDBlockObj.ControlPMtriggers`),
which wakes a subscribed block with `InterruptReason:="Triggered_PM_Due"` if it
is currently `Running`/`Idle`/`Standby` and not mid-PM-prereq-wait or
phase-deferred. `control_pm_triggers_process` ports the source's one-process-
per-block form; `trigger_process`'s optional `on_fire` callback offers the
collapsed form for callers for which wake ordering does not matter.

Checked against `tests/golden/HierarchyExample` and `tests/golden/Phasing1`.
"""
from __future__ import annotations

from collections.abc import Callable

from raptor.core.engine.eventlog import format_event_log_line
from raptor.core.model import RBDTrigger
from raptor.core.rng import Distribution, RandomSource
from raptor.core.scheduler import Process, Scheduler


def trigger_process(
    trigger: RBDTrigger, rng: RandomSource, scheduler: Scheduler, logs: list[str], *, events_file: bool = False,
    on_fire: Callable[[float], None] | None = None,
) -> Process:
    """Scheduler-driven port of `PullRapTrigger`'s `WHILE`+`LOOP` body.
    Register with `scheduler.schedule_process(...)`, matching the source's
    `TELL trig TO PullRapTrigger IN 0.0` (a deferred spawn). Schedule it
    AFTER `cold_logic_kickoff_process` (Raptor 7: `CheckColds;`
    runs, then `FOREACH trig IN triggerGroup: TELL trig TO PullRapTrigger
    IN 0.0`).

    No explicit `run_length` boundary handling, unlike `block_run_process` --
    a trigger has no end-of-run bookkeeping of its own, so this keeps yielding
    an unbounded `LOOP` and relies on `Scheduler.run_until` never popping an
    entry past `run_length`.
    """
    wait_time = None
    for _ in range(100):
        draw = rng.draw_number(Distribution(trigger.dist), trigger.params)
        wait_time = draw - trigger.init_used
        if wait_time > 0.0:
            break
    else:
        wait_time = 0.00001  # Raptor 7 -- exhausted all 100 retries

    while True:
        yield wait_time
        if events_file:
            logs.append(format_event_log_line(
                scheduler.time, "Trigger", trigger.trigger_name, "Fired", "System_event",
            ))
        if on_fire is not None:
            on_fire(scheduler.time)
        wait_time = rng.draw_number(Distribution(trigger.dist), trigger.params)
        if wait_time <= 0.0:
            wait_time = 0.000001
        if not trigger.repeats:
            return


#: Far-future park for a subscriber waiting on its trigger -- the same
#: idiom `block_lifecycle`'s own Idle parks use (there is no "wait for a
#: named event" scheduler primitive; a park plus an `interrupt` IS that
#: primitive here).
_FAR_FUTURE = 1_000_000_000.0


def control_pm_triggers_process(
    block, scheduler: Scheduler, *,
    should_wake: Callable[[], bool],
    wake: Callable[[], None],
    phase_defer_wait: Callable[[], bool] | None = None,
    phase_change_in_progress: Callable[[], bool] | None = None,
    on_sync_wait: Callable[[], None] | None = None,
    on_wait: Callable[[], None] | None = None,
) -> Process:
    """Literal port of `RBDBlockObj.ControlPMtriggers`
   -- one process per subscribing block, spawned
    alongside that block's own `Run`/`Repair`, looping on
    `WAIT FOR myTrig TO Fire`.

    Source's shape, which this keeps:

        LOOP
           WAIT FOR myTrig TO Fire;
           IF ((NOT WaitingForPrereqs) AND (NOT deferPM))
              IF ((opStatus=Running) OR (opStatus=Idle) OR (opStatus=Standby))
                 InterruptReason:="Triggered_PM_Due";
                 IF FailWait: Interrupt(SELF,"Run")
                 ELSIF ZeroWait: BlockTrigger[seqNum].Release
              END IF
           END IF
        END LOOP

    One process per block rather than one loop over subscribers, because two
    things are observable only with separate processes:

    * Order: each subscriber holds its own scheduler queue position, so the
      wake order is the scheduler's, not a subscriber list's.
    * Absorption: each subscriber re-checks `opStatus` at its own turn, by
      which time an earlier cascade in the same instant may already have idled
      its block.

    `should_wake`/`wake` are supplied by the caller so this module stays free
    of orchestrator internals: `should_wake` is the source's eligibility test,
    `wake` its `Interrupt`/`Release`.

    `on_wait` is called each time the loop enters `WAIT FOR myTrig TO Fire`:
    the caller keeps the trigger's waiter list from it, and a fire releases
    its waiters in that order (first entry at this process's start, then
    re-entry after each fire it handled -- so the `pmMisDefer` hop below
    re-queues a subscriber behind the others).
    """
    while True:
        if on_wait is not None:
            on_wait()
        resume = yield _FAR_FUTURE
        if resume is None:
            # A bare timeout: the far-future park elapsed, which only
            # happens past run end. Nothing to do.
            return
        if phase_defer_wait is not None and phase_defer_wait():
            # Raptor 7's `IF ((activePhases>0) AND
            # (pmMisDefer)): WAIT DURATION 0.0`. This extra hop is why a
            # phase-deferrable subscriber ends up behind the others for the
            # rest of the run: it re-joins the queue one slot later than the
            # subscribers that skipped the branch.
            yield 0.0
            if phase_change_in_progress is not None and phase_change_in_progress():
                # `IF PhaseChangeInProgress: WAIT FOR pmSyncTrigger TO Fire` : park until
                # the phase change ends (`ConductPhaseChange` releases it), so the PM
                # is judged against the NEW mission status (`PmMisDeferBoundaryProbe`).
                if on_sync_wait is not None:
                    on_sync_wait()
                if (yield _FAR_FUTURE) is None:
                    return
        if should_wake():
            wake()
