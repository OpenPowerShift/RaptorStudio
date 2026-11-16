"""`run_simulation_with_reports` options and the worker's disk spool, run
through the engine tier (no GUI import, so the coverage floors see them),
and the conditional-life bisection's iteration caps."""
from __future__ import annotations

import math
import os
from pathlib import Path

from raptor.core.engine.runner import run_simulation_with_reports
from raptor.core.fileio.rbd import load_rbd

GOLD = Path(__file__).resolve().parents[1] / "golden"


def _two_block():
    return load_rbd(os.path.join(GOLD, "TwoBlockRedundancy", "input.rbd"))


def test_count_draws_works_without_a_metadata_sink():
    """The draw count needs somewhere to keep its tally; with no sink the
    runner makes its own instead of failing."""
    _logs, eor, fin = run_simulation_with_reports(
        _two_block(), 2, collect_logs=False, count_draws=True, date="fixed")
    assert eor and fin


def test_end_of_run_text_goes_to_the_sink_and_progress_is_reported():
    """With a sink, the End_Of_Run text goes there (the returned list is
    empty: the GUI keeps it off the heap) and every report step is
    announced, ending with the Final Results report."""
    sink: list[str] = []
    steps: list[tuple[int, int, str]] = []
    _logs, eor, fin = run_simulation_with_reports(
        _two_block(), 3, collect_logs=False, date="fixed",
        end_of_run_sink=sink,
        report_progress=lambda done, total, label: steps.append((done, total, label)))
    assert eor == [] and fin
    assert sink[0].startswith("End_Of_Run Report for")
    assert [s for s in steps if s[2] == "End-of-Run report"] == [
        (1, 3, "End-of-Run report"), (2, 3, "End-of-Run report"), (3, 3, "End-of-Run report")]
    assert steps[-1] == (3, 3, "Final results report")


def test_a_worker_spools_its_availability_rows_to_disk(tmp_path):
    """A chunk whose parent keeps the availability plot on disk writes its
    rows to a file beside it and hands back only the path."""
    from raptor.core.engine import runner
    from raptor.core.fileio.rbds import render_rbds
    payload = (render_rbds(_two_block()), 2, 1000, 10.0, 720.0, False, 0, "",
               str(tmp_path))
    result = runner._run_chunk_worker(payload)
    assert result["ao"] == [] and result["ao_path"]
    spooled = Path(result["ao_path"])
    assert spooled.is_file() and spooled.stat().st_size > 0


def test_bisection_stops_at_its_caps_when_the_survival_function_never_answers(monkeypatch):
    """A survival function that never compares (NaN) must not hang the
    run: the bracket stops growing after its cap and the halving stops
    after its own, still returning a finite time past the age."""
    from raptor.core.analytical import survival
    from raptor.core.engine import conditional_life
    monkeypatch.setattr(survival, "sf", lambda code, p, t: math.nan)
    t = conditional_life._bisect(11, [1.5, 100.0, 0.0], 50.0, 0.25)
    assert math.isfinite(t) and t >= 50.0


def test_halving_stops_at_its_cap_next_to_zero(monkeypatch):
    """At age 0 with the target already met everywhere, the halving walks
    toward 0, where doubles never run out; it stops at 200 steps with a
    time still past the age."""
    from raptor.core.analytical import survival
    from raptor.core.engine import conditional_life
    monkeypatch.setattr(survival, "sf", lambda code, p, t: 0.0)
    t = conditional_life._bisect(11, [1.5, 100.0, 0.0], 0.0, 0.25)
    assert t == 2.0 ** -200


def test_parallel_capacity_lines_keep_one_block_per_run():
    """Workers used to hand back their runs' capacity lines without the
    End_of_Run markers, so every run of a chunk parsed as one: lost
    capacity under -j 4 read 37 % high. The merged lines must be the file
    the sequential path writes, run for run (header, rows, marker)."""
    from raptor.api.series import parse_capacity_runs
    rbd = load_rbd(os.path.join(GOLD, "Capacity1", "input.rbd"))
    sequential: list[str] = []
    run_simulation_with_reports(rbd, 6, collect_logs=False, date="fixed",
                                capacity_sink=sequential)
    parallel: list[str] = []
    run_simulation_with_reports(rbd, 6, collect_logs=False, date="fixed",
                                capacity_sink=parallel, num_workers=2)
    assert len(parse_capacity_runs("\n".join(sequential))) == 6
    assert len(parse_capacity_runs("\n".join(parallel))) == 6
    assert parallel[0] == sequential[0] == "SimulationTime        Flow       Capacity"
    markers = [line for line in parallel if "End_of_Run_" in line]
    assert [m.strip("*") for m in markers] == [f"End_of_Run_{i}" for i in range(1, 7)]
