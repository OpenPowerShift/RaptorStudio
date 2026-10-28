"""Direct unit tests for `triggers.trigger_process`, isolated from the
rest of the engine. Repetition, one-shot firing and the events-file flag
are exercised here; `tests/golden/HierarchyExample` covers triggers end to
end.
"""
from __future__ import annotations

from raptor.core.engine.triggers import trigger_process
from raptor.core.model import RBDTrigger
from raptor.core.rng import Distribution, RaptorStream, RandomGen
from raptor.core.scheduler import Scheduler


def _rng() -> RandomGen:
    return RandomGen(stream=RaptorStream(seed=12345))


def test_trigger_fires_repeatedly_with_init_used_only_on_first_draw() -> None:
    trigger = RBDTrigger(
        trigger_name="500W-PowerString", repeats=True, init_used=264.0,
        dist=int(Distribution.NORMAL), params=[288.0, 1.0, 0.0],
    )
    rng = _rng()
    scheduler = Scheduler()
    logs: list[str] = []
    scheduler.schedule_process(trigger_process(trigger, rng, scheduler, logs, events_file=True))
    scheduler.run_until(1000.0)
    assert len(logs) >= 2
    for line in logs:
        assert "Trigger" in line and trigger.trigger_name in line and "Fired" in line and "System_event" in line


def test_trigger_does_not_repeat_when_repeats_false() -> None:
    trigger = RBDTrigger(
        trigger_name="one-shot", repeats=False, init_used=0.0,
        dist=int(Distribution.NORMAL), params=[10.0, 1.0, 0.0],
    )
    rng = _rng()
    scheduler = Scheduler()
    logs: list[str] = []
    scheduler.schedule_process(trigger_process(trigger, rng, scheduler, logs, events_file=True))
    scheduler.run_until(1000.0)
    assert len(logs) == 1


def test_trigger_events_file_false_produces_no_logs() -> None:
    trigger = RBDTrigger(
        trigger_name="silent", repeats=True, init_used=0.0,
        dist=int(Distribution.NORMAL), params=[5.0, 1.0, 0.0],
    )
    rng = _rng()
    scheduler = Scheduler()
    logs: list[str] = []
    scheduler.schedule_process(trigger_process(trigger, rng, scheduler, logs, events_file=False))
    scheduler.run_until(100.0)
    assert logs == []


def _subscriber(scheduler: Scheduler, *, in_progress: list[bool], parked: list[int]):
    """A `control_pm_triggers_process` with pmMisDefer phase deferral on, recording its wakes."""
    from raptor.core.engine.triggers import control_pm_triggers_process
    woken: list[float] = []
    process = control_pm_triggers_process(
        None, scheduler, should_wake=lambda: True, wake=lambda: woken.append(scheduler.time),
        phase_defer_wait=lambda: True, phase_change_in_progress=lambda: in_progress[0],
        on_sync_wait=lambda: parked.append(1))
    return scheduler.schedule_process(process), woken


def test_a_pm_subscriber_waits_for_the_phase_change_to_end_before_it_wakes_its_block() -> None:
    """Raptor 7: with a phase change in progress the subscriber parks on `pmSyncTrigger`
    (`PmMisDeferBoundaryProbe`); only the release lets it judge the PM."""
    from raptor.core.engine.interrupts import InterruptReason
    scheduler = Scheduler()
    in_progress, parked = [True], []
    handle, woken = _subscriber(scheduler, in_progress=in_progress, parked=parked)
    scheduler.run_until(1.0)
    scheduler.interrupt(handle, InterruptReason.TRIGGER_FIRED)      # the trigger fires mid phase change
    scheduler.run_until(1.0)
    assert parked == [1] and woken == []
    in_progress[0] = False
    scheduler.interrupt(handle, InterruptReason.TRIGGER_FIRED)      # `pmSyncTrigger` released
    scheduler.run_until(1.0)
    assert woken == [1.0]


def test_a_pm_subscriber_does_not_park_when_no_phase_change_is_in_progress() -> None:
    from raptor.core.engine.interrupts import InterruptReason
    scheduler = Scheduler()
    in_progress, parked = [False], []
    handle, woken = _subscriber(scheduler, in_progress=in_progress, parked=parked)
    scheduler.run_until(1.0)
    scheduler.interrupt(handle, InterruptReason.TRIGGER_FIRED)
    scheduler.run_until(1.0)
    assert parked == [] and woken == [1.0]


def test_a_parked_subscriber_left_at_the_run_end_just_stops() -> None:
    scheduler = Scheduler()
    in_progress, parked = [True], []
    handle, woken = _subscriber(scheduler, in_progress=in_progress, parked=parked)
    from raptor.core.engine.interrupts import InterruptReason
    scheduler.run_until(1.0)
    scheduler.interrupt(handle, InterruptReason.TRIGGER_FIRED)
    scheduler.run_until(2e9)      # past its far-future park: a bare timeout, no wake
    assert woken == []
