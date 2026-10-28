"""The `/C130` Mission File against real
captures: `Phasing3` (5 cycles x 3 runs, phase-driven rows, all outcome 0), `TwoBlockRedundancy`
(20 runs, both outcomes) and `Ch07Dependent` (5 runs, dependencies)."""
from __future__ import annotations

from pathlib import Path

import pytest

from raptor.core.engine.runner import run_simulation
from raptor.core.fileio.rbd import load_rbd

GOLD = Path(__file__).resolve().parents[1] / "golden"

CASES = [("Phasing3", 3, "5.000000"), ("TwoBlockRedundancy", 20, ""), ("Ch07Dependent", 5, "")]


@pytest.mark.parametrize("name,runs,term", CASES)
def test_file_matches_real(name, runs, term, tmp_path):
    rbd = load_rbd(str(GOLD / name / "input.rbd"))
    row = rbd.section("SYSTEM_DATA").rows[0]
    row["NumRuns"] = str(runs)
    if term:
        row["TermCondition"] = term
    path = tmp_path / "mission.txt"
    run_simulation(rbd, num_runs=runs, mission_path=str(path))
    real = (GOLD / name / "legacy" / f"rpt_mission_{name}.txt").read_bytes()
    assert path.read_bytes() == real


def test_markers_and_outcomes(tmp_path):
    rbd = load_rbd(str(GOLD / "TwoBlockRedundancy" / "input.rbd"))
    path = tmp_path / "m.txt"
    run_simulation(rbd, num_runs=3, mission_path=str(path))
    lines = path.read_bytes().decode().split("\r\n")
    assert lines[-2:] == ["-2 -2 -2", ""]
    assert lines.count("-1 -1 -1") == 3          # one per run
    outcomes = {ln.split("  ")[2] for ln in lines if "  " in ln}
    assert outcomes == {"0", "1"}


def test_off_writes_nothing_and_changes_nothing(tmp_path):
    rbd = load_rbd(str(GOLD / "TwoBlockRedundancy" / "input.rbd"))
    a = run_simulation(rbd, num_runs=2)
    b = run_simulation(rbd, num_runs=2, mission_path=str(tmp_path / "m.txt"))
    assert [list(x) for x in a] == [list(x) for x in b]


def test_api_run_honours_the_option(tmp_path, monkeypatch):
    from raptor.api.run import run, section_path
    from raptor.api.workspace import Workspace
    from raptor.core.dev_options import DeveloperOptions

    ws = Workspace(tmp_path)
    mid = ws.open_model(GOLD / "TwoBlockRedundancy" / "input.rbd")["model_id"]
    monkeypatch.delenv("RAPTOR_DEV_FEATURES", raising=False)
    off = run(ws, mid, num_runs=2, timeout_s=60, confirm_long_run=True)
    assert not section_path(ws.root / "runs" / off.run_id, "mission_file").exists()
    on = run(ws, mid, num_runs=2, timeout_s=60, confirm_long_run=True,
             developer=DeveloperOptions(mission_file=True))
    text = section_path(ws.root / "runs" / on.run_id, "mission_file").read_bytes()
    assert text.endswith(b"-2 -2 -2\r\n")
