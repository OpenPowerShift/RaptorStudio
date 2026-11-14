"""Attribution through the run API and `raptor explain`."""
from __future__ import annotations

import json

import pytest

from raptor.api import build
from raptor.api import run as run_api
from raptor.api.insight import drivers
from raptor.cli import exitcodes as X
from raptor.cli.main import main
from raptor.core.fileio.rbds import save_rbds
from raptor.api.workspace import Workspace

SPEC = {
    "blocks": [
        {"name": "Pump", "fail_dist": "Exponential", "fail_params": [300, 0, 0],
         "repair_dist": "Lognormal", "repair_params": [10, 3, 0],
         "sparing_type": "Custom", "stock": 0, "rso": 1, "new_spares": 1,
         "arrive_every": 600, "eso": 0},
        {"name": "Valve", "fail_dist": "Exponential", "fail_params": [2000, 0, 0],
         "repair_dist": "Lognormal", "repair_params": [5, 1, 0]}],
    "nodes": [{"name": "Start", "type": 1}, {"name": "End", "type": 3}],
    "links": [{"from": "Start", "to": "Pump"}, {"from": "Pump", "to": "Valve"},
              {"from": "Valve", "to": "End"}],
    "experiment": {"trials": 30, "termination_type": "TimeSim", "term_condition": 3000},
}


@pytest.fixture
def stored(tmp_path):
    ws = Workspace(tmp_path)
    mid = ws.register_model(build.build_from_spec(SPEC), name="m")["model_id"]
    return ws, mid


def test_attribution_does_not_change_the_results(stored):
    ws, mid = stored
    plain = run_api.run(ws, mid, seed=5)
    with_attr = run_api.run(ws, mid, seed=5, attribution=True)
    assert plain.per_run == with_attr.per_run
    assert plain.final_results == with_attr.final_results
    with pytest.raises(drivers.NoAttribution):
        drivers.read(ws, plain.run_id)


def test_stored_attribution_agrees_with_the_engine(stored):
    ws, mid = stored
    result = run_api.run(ws, mid, seed=5, attribution=True)
    attribution = drivers.read(ws, result.run_id)
    assert attribution["runs"] == 30 and attribution["units"] == "hours"
    # the engine's own red time per run = (1 - availability) x run length
    engine_red = sum((1 - row["availability"]) * 3000 for row in result.per_run) / 30
    assert attribution["system"]["red_time"]["mean"] == pytest.approx(engine_red, rel=1e-6)
    engine_outages = sum(row["num_system_failures"] for row in result.per_run) / 30
    assert attribution["system"]["outages"]["mean"] == pytest.approx(engine_outages)
    lead = attribution["elements"][0]
    assert lead["name"] == "Pump" and lead["main_cause"] == "wait_spare"
    assert lead["dtci"]["mean"] > 0.8 and lead["dtci"]["sem"] > 0


def test_caption_and_table(stored):
    ws, mid = stored
    attribution = drivers.read(ws, run_api.run(ws, mid, seed=5, attribution=True).run_id)
    assert drivers.caption(attribution).startswith("2 elements cause 100 % of system downtime: Pump")
    lines = drivers.text_lines(attribution)
    assert any(line.startswith("Pump") and "Waiting for a spare" in line for line in lines)
    assert any("Pump (waiting for a spare) -> Pump (hands-on repair)" in line for line in lines)


def test_explain_command(capsys, tmp_path):
    path = tmp_path / "m.rbds"
    save_rbds(build.build_from_spec(SPEC), str(path))
    assert main(["explain", str(path), "--seed", "5"]) == X.OK
    out = capsys.readouterr().out
    assert "By cause: waiting for a spare" in out and "Pump" in out
    assert main(["explain", str(path), "--seed", "5", "-n", "10", "--top", "1", "-f", "json"]) == X.OK
    doc = json.loads(capsys.readouterr().out)
    assert doc["runs"] == 10 and len(doc["elements"]) == 1


def test_parallel_workers_give_an_attribution_too(stored):
    ws, mid = stored
    result = run_api.run(ws, mid, seed=5, workers=2, attribution=True)
    attribution = drivers.read(ws, result.run_id)
    red = sum((1 - row["availability"]) * 3000 for row in result.per_run) / 30
    assert attribution["system"]["red_time"]["mean"] == pytest.approx(red, rel=1e-6)


def test_explain_yearly(capsys, tmp_path):
    path = tmp_path / "m.rbds"
    save_rbds(build.build_from_spec(SPEC), str(path))
    assert main(["explain", str(path), "--seed", "5", "--year-length", "1000", "--below", "0.9"]) == X.OK
    out = capsys.readouterr().out
    assert "90 periods of 1,000 hours from 30 runs" in out and "Pooled:  P1" in out
    assert main(["explain", str(path), "--seed", "5", "--yearly", "-f", "json"]) == X.OK
    assert json.loads(capsys.readouterr().out)["periods"]["pooled"]["n"] == 0   # runs under a year
