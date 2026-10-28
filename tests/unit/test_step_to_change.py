"""The original's Step (`TimeAdvance`): pause at the end of an instant in
which something changed state. `StepSession.step_to_change` against the event log of the same run:
its stops are exactly the times at which a block really changed state (an `End_Pre-LDT` or a
stress re-report is not a change)."""
from __future__ import annotations

from pathlib import Path

import pytest

from raptor.core.engine.step_session import StepSession
from raptor.core.fileio.rbd import load_rbd

GOLD = Path(__file__).resolve().parents[1] / "golden"
NOT_A_CHANGE = ("StessLevel", "End_Pre-LDT", "End_Post-LDT", "End_Partial", "PM_is_due", "Sparing",
                "InitializeStress", "Time=", "Trigger")


@pytest.mark.parametrize("name,length", [("TwoBlockRedundancy", "20"), ("MotorDemo", "300"),
                                         ("FreqPMProbe", "120"), ("MixedDepPerc", "200")])
def test_stops_are_the_times_blocks_change_state(name, length):
    rbd = load_rbd(str(GOLD / name / "input.rbd"))
    rbd.section("SYSTEM_DATA").rows[0]["TermCondition"] = length
    session = StepSession(rbd)
    stops = []
    while session.step_to_change():
        stops.append(round(session.time, 6))
    logs, _ = session.finish_run()
    changes = set()
    for line in logs:
        f = str(line).split("\t")
        if len(f) >= 5 and f[1].strip() == "Block" and not any(k in f[3] or k in f[4] for k in NOT_A_CHANGE):
            changes.add(round(float(f[0]), 6))
    assert set(stops) - {0.0} == changes - {float(length)}
    assert stops == sorted(stops) and len(stops) == len(set(stops))   # one stop per instant


def test_a_finer_step_still_advances_one_entry():
    rbd = load_rbd(str(GOLD / "TwoBlockRedundancy" / "input.rbd"))
    session = StepSession(rbd)
    assert session.step() is True


def _short(name, length="30"):
    rbd = load_rbd(str(GOLD / name / "input.rbd"))
    rbd.section("SYSTEM_DATA").rows[0]["TermCondition"] = length
    return rbd


@pytest.mark.parametrize("name", ["TwoBlockRedundancy", "MotorDemo"])
def test_jumping_through_every_run_is_the_batch(name):
    """Jump: the rest of a run goes by, the next starts on the same random streams.
    Stepping some, jumping, and finishing must give the batch's logs and report figures run for run."""
    from raptor.core.engine.runner import run_simulation
    runs = 3
    batch_reports: list = []
    batch_logs = run_simulation(_short(name), runs, _report_sink=batch_reports)
    sink: list = []
    session = StepSession(_short(name), num_runs=runs, report_sink=sink)
    session.step_to_change()
    session.step_to_change()
    assert session.run_index == 0
    assert session.jump_to_next_run_start() is True
    assert session.run_index == 1
    assert session.has_next_run
    logs, reports = session.finish_all()
    assert [list(x) for x in logs] == [list(x) for x in batch_logs]
    assert len(sink) == runs == len(reports)
    assert sink == batch_reports


def test_jump_from_the_last_run_ends_the_session_and_stops_at_the_first_change():
    session = StepSession(_short("TwoBlockRedundancy"), num_runs=2)
    assert session.jump_to_next_run_start() is True
    # the new run is built and not run on: its first stop is the t = 0 analysis, as for a fresh session
    assert session.time == 0.0 and not session.done
    assert session.step_to_change() and session.time == 0.0
    assert session.jump_to_next_run_start() is False
    assert session.done and len(session.completed) == 1


def test_step_and_jump_stops_match_a_real_session():
    """Captured 2026-10-21 in Raptor 7 Student (TwoBlockRedundancy, 100 hours, 202 runs, graphics on): paused
    in run 9, Step, then Jump -> Run 10 at Sim Time 0.000000, then Step -> 0.444507, Step -> 0.686393."""
    rbd = load_rbd(str(GOLD / "TwoBlockRedundancy" / "input.rbd"))
    session = StepSession(rbd, num_runs=202, report_sink=[])
    session.step_to_change()
    for _ in range(9):
        session.jump_to_next_run_start()
    assert session.run_index == 9
    stops = []
    for _ in range(3):
        session.step_to_change()
        stops.append(round(session.time, 6))
    assert stops == [0.0, 0.444507, 0.686393]
