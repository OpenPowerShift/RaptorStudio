"""StepSession step debugger: stepped runs must match the batch path.

The interactive stepper shares `SingleRunDriver` with the batch path, so
stepped logs must equal batch logs byte-for-byte on every golden fixture.
This pins that equivalence: an engine change that drifts the two paths
apart fails here rather than silently in the GUI.

`finish_run()`'s report is also pinned: a stepped run must produce the same
`RunReportData` numbers as its batch twin's report-sink entry.
"""
from __future__ import annotations

import pathlib

import pytest

from raptor.core.engine.step_session import StepSession
from raptor.core.engine.runner import run_simulation
from raptor.core.fileio.rbd import load_rbd

GOLDEN = pathlib.Path(__file__).parent.parent / "golden"

# One run per fixture pins kickoff, event order and closing-pass
# equivalence. Phasing1 exercises phasing kickoff; Events the events-only
# model (no blocks); HierarchyExample hierarchies.
@pytest.mark.parametrize(
    "fixture_dir",
    [
        "TwoBlockRedundancy",
        "PrePostLDT",
        "BlockDependency",
        "SingleHierarchy",
        "HierarchyExample",
        "SharedStreamTie",
        "Cascade",
        "Phasing1",
        "Events",
    ],
)
def test_stepped_logs_match_batch(fixture_dir: str) -> None:
    rbd_batch = load_rbd(str(GOLDEN / fixture_dir / "input.rbd"))
    rbd_step = load_rbd(str(GOLDEN / fixture_dir / "input.rbd"))

    batch_logs = run_simulation(rbd_batch, num_runs=1)[0]

    session = StepSession(rbd_step)
    steps = 0
    while session.step():
        steps += 1
        assert steps < 1_000_000, f"{fixture_dir}: runaway step loop"
    assert not session.done or steps > 0  # ran out of events naturally
    logs, _report = session.finish_run()

    assert logs == batch_logs, (
        f"{fixture_dir}: stepped log diverges from batch "
        f"(first diff at line {next(
            (i for i, (a, b) in enumerate(zip(logs, batch_logs))
             if a != b), min(len(logs), len(batch_logs)))})")


def test_finish_run_report_matches_batch_report() -> None:
    """The GUI feeds `finish_run()`'s RunReportData straight into the
    results dock -- it must carry the same availability/MTBDE/MDT the
    batch path's report sink produces."""
    fixture = GOLDEN / "TwoBlockRedundancy" / "input.rbd"
    report_sink_batch: list = []
    run_simulation(load_rbd(str(fixture)), num_runs=1,
                   _report_sink=report_sink_batch)

    sink_step: list = []
    session = StepSession(load_rbd(str(fixture)), report_sink=sink_step)
    while session.step():
        pass
    _, report = session.finish_run()

    assert report is not None and sink_step, \
        "both paths must produce structured reports"
    batch, mine = sink_step[0], report
    assert mine.availability == pytest.approx(batch.availability, rel=1e-12)
    for attr in ("mtbde", "mdt", "reliability"):
        b, m = getattr(batch, attr, None), getattr(mine, attr, None)
        if b is None:
            assert m is None, attr
            continue
        assert m == pytest.approx(b, rel=1e-12), attr


def test_step_session_is_single_use() -> None:
    """A finished session must be inert, not restart from t=0."""
    fixture = GOLDEN / "TwoBlockRedundancy" / "input.rbd"
    session = StepSession(load_rbd(str(fixture)))
    while session.step():
        pass
    first_logs, _ = session.finish_run()
    again_logs, _again_report = session.finish_run()
    assert again_logs == first_logs
    # And stepping past the end does nothing more.
    assert session.step() is False


def test_stop_midway_then_finish_matches_full_run() -> None:
    """Step part-way, then Finish -- the completed log must still match
    the never-stepped batch run (Finish just runs the remaining events)."""
    fixture = GOLDEN / "TwoBlockRedundancy" / "input.rbd"
    batch = run_simulation(load_rbd(str(fixture)), num_runs=1)[0]

    session = StepSession(load_rbd(str(fixture)))
    for _ in range(25):
        if not session.step():
            break
    partial_lines = len(session.logs)
    assert 0 < partial_lines < len(batch), "25 steps should land mid-run"
    logs, _ = session.finish_run()
    assert logs == batch


def test_stop_and_abort_hooks():
    """GUI Pause/Stop plumbing: between-runs stop breaks the batch;
    a mid-run RunAborted discards that run and ends the batch."""
    fixture = GOLDEN / "TwoBlockRedundancy" / "input.rbd"
    rbd = load_rbd(str(fixture))
    assert len(run_simulation(rbd, num_runs=10, stop_check=lambda: True)) == 0

    from raptor.core.engine.errors import RunAborted

    def pc():
        raise RunAborted()
    logs = run_simulation(load_rbd(str(fixture)), num_runs=6,
                          stop_check=lambda: False, pause_check=pc)
    assert logs == []


def test_stop_requested_mid_run_discards_the_run() -> None:
    """A stop that arrives while a run is under way (the block hook's
    `stop_check`, not the between-runs one) raises `RunAborted` inside the
    run: that run is discarded and the batch ends. The API's timed and
    thread-set stops reach this path only by timing, so pin it here."""
    fixture = GOLDEN / "TwoBlockRedundancy" / "input.rbd"
    calls = 0

    def stop_after_first_check() -> bool:
        nonlocal calls
        calls += 1
        return calls > 1  # the first call is the between-runs check

    logs = run_simulation(load_rbd(str(fixture)), num_runs=3,
                          stop_check=stop_after_first_check)
    assert logs == []
    assert calls >= 2

    # The GUI opts in to keeping the interrupted run's log so a Stop does
    # not make the Event Log tab claim collection was off.
    calls2 = 0

    def stop_mid_first_run() -> bool:
        nonlocal calls2
        calls2 += 1
        return calls2 > 1

    partial = run_simulation(load_rbd(str(fixture)), num_runs=3,
                             stop_check=stop_mid_first_run,
                             keep_partial_logs=True)
    assert len(partial) == 1 and partial[0]


def test_a_stepped_capacity_model_banks_each_runs_rows_like_the_batch() -> None:
    """`cap_lines` and `_bank_capacity` are the capacity plot's stepped path:
    the header once, an End_of_Run separator after every run."""
    rbd = load_rbd(str(GOLDEN / "Capacity1" / "input.rbd"))
    session = StepSession(rbd, num_runs=2)
    while session.step():
        pass
    assert session.jump_to_next_run_start()
    banked = session.cap_lines
    assert banked[0].startswith("SimulationTime")
    assert "End_of_Run_1" in "".join(banked)
    while session.step():
        pass
    assert "End_of_Run_2" in "".join(session.cap_lines)


def test_a_model_without_capacity_has_no_capacity_rows() -> None:
    session = StepSession(load_rbd(str(GOLDEN / "TwoBlockRedundancy" / "input.rbd")))
    assert session.cap_lines == []


def test_early_terminate_finalizes_at_the_current_time_and_is_idempotent() -> None:
    session = StepSession(load_rbd(str(GOLDEN / "TwoBlockRedundancy" / "input.rbd")))
    for _ in range(3):
        session.step()
    stopped_at = session.time
    logs, report = session.early_terminate()
    assert logs and session.done
    again_logs, again_report = session.early_terminate()
    assert again_logs is logs and again_report is report
    assert session.time == stopped_at
