"""Tests for `raptor.core.engine.phasing` -- the `ControlPhaseChanges`
loop. Domain-level (no full model needed)."""
from __future__ import annotations

from raptor.core.engine.phasing import control_phase_changes_process
from raptor.core.engine.system_status import SystemState
from raptor.core.model import Phase
from raptor.core.rng import RandomGen, RaptorStream, fetch_raptor_seed
from raptor.core.scheduler import Scheduler


def _run(stop_criteria: float):
    phases = [
        Phase(phase_name="P1", dist=19, params=[10.0]),
        Phase(phase_name="P2", dist=19, params=[10.0]),
    ]
    sched = Scheduler()
    logs: list[str] = []
    conducted: list[int] = []

    def conduct(new_phase: int, t: float):
        conducted.append(new_phase)
        yield 0.0

    proc = control_phase_changes_process(
        phases, SystemState(),
        RandomGen(stream=RaptorStream(seed=fetch_raptor_seed(1))),
        sched, conduct, logs, events_file=True,
        termination_type="TimeSim", stop_criteria=stop_criteria,
    )
    sched.schedule_process(proc)
    sched.run_until(stop_criteria)
    return [l for l in logs if "Begin_phase" in l], conducted


def test_phase_change_skipped_when_boundary_lands_on_run_end() -> None:
    """No phase change is conducted when the phase boundary coincides with
    the run end (`termType=1` and `ABS(StopCriteria-SimTime) < 1e-7`). Phase
    1 lasts 10.0 and the run ends at 10.0, so there is no `Begin_phase_P2`
    and no `ConductPhaseChange`."""
    begins, conducted = _run(10.0)
    assert begins == []
    assert conducted == []


def test_phase_change_conducted_when_boundary_is_before_run_end() -> None:
    begins, conducted = _run(25.0)
    assert any("Begin_phase_P2" in l for l in begins)
    assert conducted == [2, 1]
