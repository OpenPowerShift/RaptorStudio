"""The "Cumulative By Time" file (ticker writer
reliability counts) against real captures, each with the event log of the same run:

* `TwoBlockRedundancy`: mesh 10 over 100 hours, so the last tick coincides with the run's end;
* `FreqPMProbe`: mesh 70 over 300, which does not divide the run (four ticks, PM in the model);
* `MixedDepPerc`: mesh 500 over 3007 with dependencies, 20 runs.

The ticker is a passive record in the System group: the captured event logs are identical to the
port's with or without it."""
from __future__ import annotations

import tempfile
from pathlib import Path

import pytest

from tests.unit.log_compare import read_capture_text

from raptor.core.engine.eventlog import format_event_log_file
from raptor.core.engine.runner import run_simulation
from raptor.core.fileio.rbd import load_rbd

GOLD = Path(__file__).resolve().parents[1] / "golden"

CASES = [("TwoBlockRedundancy", 10.0, 20), ("FreqPMProbe", 70.0, 5), ("MixedDepPerc", 500.0, 20)]


def _run(name: str, mesh: float, runs: int):
    rbd = load_rbd(str(GOLD / name / "input.rbd"))
    with tempfile.TemporaryDirectory() as d:
        path = Path(d) / "t.txt"
        logs = run_simulation(rbd, num_runs=runs, cumulative_time_path=str(path),
                              cumulative_time_mesh=mesh)
        return logs, path.read_bytes().decode("latin-1").replace("\r", "").split("\n")


@pytest.mark.parametrize("name,mesh,runs", CASES)
def test_file_matches_real(name, mesh, runs):
    _, ours = _run(name, mesh, runs)
    real = (GOLD / name / "legacy" / f"rpt_cumulative_by_time_{name}.txt").read_bytes()
    assert ours == real.decode("latin-1").replace("\r", "").split("\n")


@pytest.mark.parametrize("name,mesh,runs", CASES[:2])
def test_the_ticker_changes_nothing_in_the_event_log(name, mesh, runs):
    logs, _ = _run(name, mesh, runs)
    cap = {"TwoBlockRedundancy": "cumtime_events_TwoBlockRedundancy.txt",
           "FreqPMProbe": "cumtime_events_FreqPMProbe.txt"}[name]
    real = read_capture_text(GOLD / name / "legacy" / cap).replace("\r", "").split("\n")
    ours = [str(x) for x in format_event_log_file(logs, "input.rbd", "DATE")]
    assert ours[1:] == real[1:len(ours)]


def test_a_non_time_run_is_refused(tmp_path):
    rbd = load_rbd(str(GOLD / "Phasing3" / "input.rbd"))
    with pytest.raises(ValueError, match="time-terminated"):
        run_simulation(rbd, num_runs=1, cumulative_time_path=str(tmp_path / "t.txt"),
                       cumulative_time_mesh=10.0)


def test_off_writes_nothing(tmp_path):
    rbd = load_rbd(str(GOLD / "TwoBlockRedundancy" / "input.rbd"))
    run_simulation(rbd, num_runs=2, cumulative_time_path=str(tmp_path / "t.txt"),
                   cumulative_time_mesh=0.0)
    assert not (tmp_path / "t.txt").exists()


def test_api_run_honours_the_option(tmp_path, monkeypatch):
    from raptor.api.run import run, section_path
    from raptor.api.workspace import Workspace
    from raptor.core.dev_options import DeveloperOptions

    ws = Workspace(tmp_path)
    mid = ws.open_model(GOLD / "TwoBlockRedundancy" / "input.rbd")["model_id"]
    monkeypatch.delenv("RAPTOR_DEV_FEATURES", raising=False)
    off = run(ws, mid, num_runs=3, timeout_s=60, confirm_long_run=True)
    assert not section_path(ws.root / "runs" / off.run_id, "cumulative_by_time").exists()
    on = run(ws, mid, num_runs=3, timeout_s=60, confirm_long_run=True,
             developer=DeveloperOptions(cumulative_by_time=25.0))
    text = section_path(ws.root / "runs" / on.run_id, "cumulative_by_time").read_bytes()
    assert text.startswith(b"SimTime ") and text.count(b"\r\n") == 5   # header + 4 mesh points
