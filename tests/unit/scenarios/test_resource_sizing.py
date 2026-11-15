"""Resource sizing: offered load, paired sizes and a recommendation."""
from __future__ import annotations

from raptor.api import build
from raptor.api.optimise.resources import (
    optimise_resources, resources_from_model,
)
from raptor.api.workspace import Workspace

SPEC = {
    "blocks": [
        {"name": "Pump", "fail_dist": "Fixed", "fail_params": [60, 0, 0],
         "repair_dist": "Fixed", "repair_params": [5, 0, 0],
         "resource1_name": "Crew", "num_res": 1, "res1_rep": 1},
        {"name": "Valve", "fail_dist": "Fixed", "fail_params": [60, 0, 0],
         "repair_dist": "Fixed", "repair_params": [5, 0, 0],
         "resource1_name": "Crew", "num_res": 1, "res1_rep": 1}],
    "resources": [{"name": "Crew", "num_in": 1}],
    "nodes": [{"name": "Start", "type": 1}, {"name": "End", "type": 3}],
    "links": [{"from": "Start", "to": "Pump"},
              {"from": "Pump", "to": "Valve"},
              {"from": "Valve", "to": "End"}],
    "experiment": {"trials": 4, "termination_type": "TimeSim",
                   "term_condition": 1000.0},
}


def test_the_offered_load_is_failures_times_repair_time(tmp_path):
    ws = Workspace(tmp_path)
    mid = ws.register_model(build.build_from_spec(SPEC), name="m")["model_id"]
    specs = resources_from_model(ws.get(mid).rbd)
    assert specs[0].name == "Crew"
    # two blocks, each 1/60 failures per hour x 5 hours repair
    assert specs[0].offered_load == 2 * (1 / 60) * 5


def test_sizes_are_paired_and_a_recommendation_is_made(tmp_path):
    ws = Workspace(tmp_path)
    mid = ws.register_model(build.build_from_spec(SPEC), name="m")["model_id"]
    result = optimise_resources(ws, mid, target=0.90, trials=3, seed=2)
    assert result["recommendations"]
    rec = result["recommendations"][0]
    assert rec["resource"] == "Crew"
    assert rec["units"] >= 1
    assert rec["curve"]
    assert result["batches"] == 1 + len(result["rows"])
    assert result["caption"]


def test_planned_maintenance_adds_to_the_offered_load():
    blocks = [dict(SPEC["blocks"][0], uses_pm=1, pm_freq=100.0,
                   pm_dist="Fixed", pm_params=[2, 0, 0], res1_pm_rep=1),
              SPEC["blocks"][1]]
    (spec,) = resources_from_model(build.build_from_spec(
        {**SPEC, "blocks": blocks}))
    # repairs as before, plus one 2 h PM every 100 h on the pump
    assert abs(spec.offered_load - (2 * (1 / 60) * 5 + 2 / 100)) < 1e-12


def test_the_cli_applies_the_recommended_changes(tmp_path):
    """`raptor optimise resources --apply` writes the size the result names
    in `changes` (the edits MCP and the GUI apply too) into the model file."""
    import contextlib
    import io
    import json

    from raptor.cli.main import main
    from raptor.core.fileio.rbds import save_rbds
    path = tmp_path / "crew.rbds"
    save_rbds(build.build_from_spec(SPEC), str(path))
    buffer = io.StringIO()
    with contextlib.redirect_stdout(buffer):
        assert main(["optimise", "resources", str(path), "--target", "system.ao>=0.9",
                     "-n", "3", "--extra", "1", "--apply", "-f", "json"]) == 0
    (change,) = json.loads(buffer.getvalue())["changes"]
    units = int(change.split("=")[1])
    ws = Workspace(tmp_path / "check")
    mid = ws.open_model(path)["model_id"]
    (spec,) = resources_from_model(ws.get(mid).rbd)
    assert spec.units == units != 1          # the file holds the new size
