"""Failure Effects View reports the flow a state delivers (issue #4, item 13).

`FevSession.capacity()` runs the engine's `analyze_capacity` over FEV's
current statuses. These tests pin that it agrees with a simulation that
reaches the same state, and that asking for it never changes what FEV
decides."""
from __future__ import annotations

import copy
import glob
import os
from pathlib import Path

import pytest

from raptor.core.engine.fev import FevSession
from raptor.core.engine.fmeca import capacity_lines
from raptor.core.engine.runner import run_simulation_with_reports
from raptor.core.fileio.rbd import load_rbd

GOLD = Path(__file__).resolve().parents[1] / "golden"


def _has(path: str, what: str) -> bool:
    rbd = load_rbd(path)
    if what == "phases":
        sec = rbd.sections.get("PHASES")
        return bool(sec and sec.rows)
    return str(rbd.sections["SYSTEM_DATA"].rows[0].get("capacityAnalysis", "0")).strip() in ("1", "1.0")


ALL = sorted(glob.glob(str(GOLD / "*" / "input.rbd")))
CAPACITY = [os.path.basename(os.path.dirname(p)) for p in ALL if _has(p, "capacity")]
UNPHASED_CAPACITY = [f for f in CAPACITY if not _has(str(GOLD / f / "input.rbd"), "phases")]


def _session(fixture: str) -> FevSession:
    return FevSession(load_rbd(str(GOLD / fixture / "input.rbd")))


def _simulated_flow(fixture: str, block_id: int) -> int:
    """Delivered flow after `block_id` fails at t=10 and stays failed, with
    nothing else failing: one deterministic replication's capacity file."""
    rbd = copy.deepcopy(load_rbd(str(GOLD / fixture / "input.rbd")))
    for r in rbd.sections["BLOCK_DATA"].rows:
        if r["ID"] == "0":
            continue
        r.update({"FDist": "19", "FParam1": "900000000.0", "FParam2": "0", "FParam3": "0",
                  "usesPM": "0", "DepType": "None", "DepNum": "0"})
        if int(r["ID"]) == block_id:
            r.update({"FParam1": "10.0", "RDist": "19", "RParam1": "100000.0",
                      "RParam2": "0", "RParam3": "0"})
    system = rbd.sections["SYSTEM_DATA"].rows[0]
    for key in ("SimLength", "termCondition", "TermCondition"):
        if key in system:
            system[key] = "20.000000"
    if getattr(rbd, "experiment", None):
        rbd.experiment["term_condition"] = 20.0
    lines: list[str] = []
    run_simulation_with_reports(rbd, num_runs=1, capacity_sink=lines, collect_logs=False)
    rows = [ln.split() for ln in lines if ln.split() and ln.split()[0].replace(".", "").isdigit()]
    return int(rows[-1][1])


def test_there_are_capacity_fixtures():
    assert len(UNPHASED_CAPACITY) >= 5 and len(CAPACITY) > len(UNPHASED_CAPACITY)


@pytest.mark.parametrize("fixture", UNPHASED_CAPACITY)
def test_each_single_failure_delivers_what_a_simulation_does(fixture):
    for block_id in sorted(_session(fixture).model.blocks_by_id):
        session = _session(fixture)
        session.click_block(block_id)
        assert session.capacity()["delivered"] == _simulated_flow(fixture, block_id), block_id


@pytest.mark.parametrize("fixture", CAPACITY)
def test_asking_for_capacity_changes_nothing_fev_decides(fixture):
    quiet, asked = _session(fixture), _session(fixture)
    asked.capacity()
    assert quiet.snapshot() == asked.snapshot()
    for block_id in sorted(quiet.model.blocks_by_id)[:8]:
        for session in (quiet, asked):
            b = session.model.blocks_by_id[block_id]
            if b.op_status.value in ("Running", "Repairing", "PM"):
                session.click_block(block_id)
        asked.capacity()
        assert quiet.snapshot() == asked.snapshot(), block_id
        assert quiet.logs == asked.logs


def test_a_model_without_capacity_analysis_has_none():
    assert _session("HierarchyExample").capacity() is None


def test_the_everything_up_state_delivers_the_generated_flow():
    flow = _session("Capacity2").capacity()
    assert flow["delivered"] == flow["flow_generated"] and flow["lost"] == 0
    assert capacity_lines(flow)[0] == "CAPACITY"


def test_the_api_cli_and_batch_report_it(tmp_path, capsys):
    from raptor.api.analyse import failure_effects, failure_effects_many
    from raptor.api.workspace import Workspace
    from raptor.cli.main import main
    ws = Workspace(tmp_path / "ws")
    mid = ws.open_model(GOLD / "Capacity2" / "input.rbd")["model_id"]
    one = failure_effects(ws, mid, fail=["SolarP_900W"])
    assert one["capacity"]["delivered"] == 1150
    rows = failure_effects_many(ws, mid, [{"fail": ["SolarP_900W"]}, {}])
    assert [r["capacity"]["delivered"] for r in rows] == [1150, 1500]
    plain = ws.open_model(GOLD / "HierarchyExample" / "input.rbd")["model_id"]
    assert "capacity" not in failure_effects(ws, plain)

    model = str(GOLD / "Capacity2" / "input.rbd")
    assert main(["fev", model, "--fail", "SolarP_900W"]) == 0
    assert "CAPACITY" not in capsys.readouterr().out
    assert main(["fev", model, "--fail", "SolarP_900W", "--capacity"]) == 0
    assert "Delivered Flow:     1150 of 1500" in capsys.readouterr().out
    assert main(["fev", str(GOLD / "HierarchyExample" / "input.rbd"), "--capacity"]) == 0
    assert "--capacity ignored" in capsys.readouterr().err
