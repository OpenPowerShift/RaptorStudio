"""The "Cumulative By Trial" file (`CalculateInterimRams` )
against real captures: `TwoBlockRedundancy` (20 runs, a row after every run: the N/A branches of
the first rows, then the full ones) and `Phasing3` (captured at 10 runs, every third; nine are enough to reproduce its three rows)."""
from __future__ import annotations

import tempfile
from pathlib import Path

import pytest

from raptor.core.engine.runner import run_simulation
from raptor.core.fileio.rbd import load_rbd

GOLD = Path(__file__).resolve().parents[1] / "golden"

CASES = [("TwoBlockRedundancy", 1, 20, ""), ("Phasing3", 3, 9, "")]


def _ours(name: str, interval: int, runs: int, term: str) -> list[str]:
    rbd = load_rbd(str(GOLD / name / "input.rbd"))
    row = rbd.section("SYSTEM_DATA").rows[0]
    row["NumRuns"] = str(runs)
    if term:
        row["TermCondition"] = term
    with tempfile.TemporaryDirectory() as d:
        path = Path(d) / "c.txt"
        run_simulation(rbd, num_runs=runs, cumulative_trial_path=str(path),
                       cumulative_trial_interval=interval)
        return path.read_bytes().decode("latin-1").replace("\r", "").split("\n")


@pytest.mark.parametrize("name,interval,runs,term", CASES)
def test_file_matches_real(name, interval, runs, term):
    real = (GOLD / name / "legacy" / f"rpt_cumulative_by_trial_{name}.txt").read_bytes()
    assert _ours(name, interval, runs, term) == real.decode("latin-1").replace("\r", "").split("\n")


def test_off_writes_nothing(tmp_path):
    rbd = load_rbd(str(GOLD / "TwoBlockRedundancy" / "input.rbd"))
    run_simulation(rbd, num_runs=2, cumulative_trial_path=str(tmp_path / "c.txt"),
                   cumulative_trial_interval=0)
    assert not (tmp_path / "c.txt").exists()


def test_rows_follow_the_interval():
    lines = _ours("TwoBlockRedundancy", 5, 12, "")
    assert [ln.split()[0] for ln in lines[1:] if ln] == ["5", "10"]


def test_api_run_honours_the_option(tmp_path, monkeypatch):
    from raptor.api.run import run, section_path
    from raptor.api.workspace import Workspace
    from raptor.core.dev_options import DeveloperOptions

    ws = Workspace(tmp_path)
    mid = ws.open_model(GOLD / "TwoBlockRedundancy" / "input.rbd")["model_id"]
    monkeypatch.delenv("RAPTOR_DEV_FEATURES", raising=False)
    off = run(ws, mid, num_runs=4, timeout_s=60, confirm_long_run=True)
    assert not section_path(ws.root / "runs" / off.run_id, "cumulative_by_trial").exists()
    on = run(ws, mid, num_runs=4, timeout_s=60, confirm_long_run=True,
             developer=DeveloperOptions(cumulative_by_trial=2))
    text = section_path(ws.root / "runs" / on.run_id, "cumulative_by_trial").read_bytes()
    assert text.startswith(b"Trials    Ao ") and text.count(b"\r\n") == 3
