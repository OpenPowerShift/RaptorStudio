"""Tested recommendations: proposals from causes, paired verdicts, cost, apply."""
from __future__ import annotations

import json

import pytest

from raptor.api import build
from raptor.api.paths import PathError, cell_changes
from raptor.api.scenarios.recommend import (
    Proposal, downtime_words, proposals, recommend, text_lines,
)
from raptor.api.workspace import Workspace
from raptor.core.fileio.rbds import save_rbds


def _spec(repair: float = 20.0) -> dict:
    return {
        "blocks": [
            {"name": "Pump", "fail_dist": "Exponential", "fail_params": [50],
             "repair_dist": "Exponential", "repair_params": [repair]},
            {"name": "Valve", "fail_dist": "Exponential", "fail_params": [400],
             "repair_dist": "Exponential", "repair_params": [2]},
        ],
        "nodes": [{"name": "Start", "type": 1}, {"name": "End", "type": 3}],
        "links": [{"from": "Start", "to": "Pump"}, {"from": "Pump", "to": "Valve"},
                  {"from": "Valve", "to": "End"}],
        "experiment": {"trials": 40, "termination_type": "TimeSim",
                       "term_condition": 2000.0},
    }


def _workspace(tmp_path, spec: dict | None = None):
    rbd = build.build_from_spec(spec or _spec())
    path = tmp_path / "m.rbds"
    save_rbds(rbd, str(path))
    ws = Workspace(tmp_path / "ws")
    return ws, ws.open_model(path)["model_id"]


def _attribution(rbd, causes: dict[str, dict[str, float]]) -> dict:
    elements = []
    for row in rbd.section("BLOCK_DATA").rows:
        name = row.get("blockName")
        if name in causes:
            elements.append({"kind": "block", "id": int(row["ID"]), "name": name,
                             "dtci": {"mean": 0.9 if name == "Pump" else 0.1},
                             "causes": causes[name]})
    return {"elements": elements, "units": "hours", "runs": 10}


def test_the_main_cause_picks_the_lever_and_the_top_driver_gets_a_better_part():
    rbd = build.build_from_spec(_spec())
    found = proposals(rbd, _attribution(rbd, {"Pump": {"repair": 1.0},
                                              "Valve": {"repair": 1.0}}))
    names = [p.name for p in found]
    assert names == ["Pump: halve the repair time",
                     "Pump: a part that lasts twice as long",
                     "Valve: halve the repair time"]
    assert found[0].changes == ("block:Pump.repair_params=10,0",)
    assert found[1].changes == ("block:Pump.fail_params=100,0",)
    assert found[0].unit_cost is None and "not in the model" in found[0].cost_note


def test_a_spare_wait_offers_one_more_spare_with_its_stored_cost():
    spec = _spec()
    spec["blocks"][0].update({"spare_pool": "Shelf", "sp_type": "Pooled"})
    spec["spare_pools"] = [{"name": "Shelf", "stock": 1}]
    rbd = build.build_from_spec(spec)
    rbd.section("SPARE_POOL_DATA").rows[0]["SpCost"] = "250.00"
    found = proposals(rbd, _attribution(rbd, {"Pump": {"wait_spare": 0.7,
                                                       "repair": 0.3}}), top=1)
    spare = next(p for p in found if p.lever == "spare")
    assert spare.changes == ("pool:Shelf.stock=2",)
    assert spare.unit_cost == 250.0
    # the second cause holds 30 % (>= 25 %): it gets its own lever too
    assert any(p.lever == "repair" for p in found)


def test_identical_changes_are_proposed_once():
    rbd = build.build_from_spec(_spec())
    attribution = _attribution(rbd, {"Pump": {"repair": 1.0}})
    found = proposals(rbd, attribution, top=3)
    assert len({p.changes for p in found}) == len(found)


def test_recommend_finds_the_repair_and_proves_it_on_paired_runs(tmp_path):
    ws, mid = _workspace(tmp_path)
    before = json.dumps(ws.get(mid).rbd.section("BLOCK_DATA").rows, sort_keys=True)
    seen: list[str] = []
    result = recommend(ws, mid, top=1, trials=40, seed=2,
                       progress=lambda done, total, label: seen.append(label))
    assert seen and seen[0] == "Finding the downtime drivers"
    best = result.rows[0]
    assert best["name"] == "Pump: halve the repair time"
    assert best["verdict"] == "worth doing"
    assert best["gain"]["availability"] > 0 and best["gain"]["p_better"] >= 0.95
    assert best["gain"]["downtime_avoided"] > 0
    assert best["gain"]["low"] < best["gain"]["availability"] < best["gain"]["high"]
    assert best["cost"]["unit"] is None
    assert result.attribution_run_id and result.base["availability"] is not None
    assert "Pump: halve the repair time" in result.caption
    # the open model is never modified
    assert json.dumps(ws.get(mid).rbd.section("BLOCK_DATA").rows,
                      sort_keys=True) == before
    text = "\n".join(text_lines(result))
    assert "change: block:Pump.repair_params=10,0" in text
    assert "[worth doing]" in text
    assert result.to_dict()["schema"] == "raptor-recommend/1"


def test_an_extra_null_change_is_no_measurable_gain(tmp_path):
    ws, mid = _workspace(tmp_path)
    result = recommend(ws, mid, top=1, trials=10, seed=1, extra=[
        Proposal("Same model", "Pump", "other", None, ("block:Pump.name=Pump",))])
    same = next(row for row in result.rows if row["name"] == "Same model")
    assert same["verdict"] == "no measurable gain"
    assert same["gain"]["availability"] == 0


def test_a_model_without_downtime_says_so(tmp_path):
    spec = _spec()
    for block in spec["blocks"]:
        block.update({"fail_dist": "Fixed", "fail_params": [1e9, 0, 0]})
    ws, mid = _workspace(tmp_path, spec)
    result = recommend(ws, mid, trials=3, seed=1)
    assert result.rows == [] and "No system downtime" in result.caption


def test_downtime_words():
    assert downtime_words(2.194, "hours") == "2.19 hours less a run"
    assert downtime_words(-0.3, "hours") == "0.3 hours more a run"
    assert downtime_words(0.0, "hours") == "unchanged"


def test_cell_changes_names_the_cells_and_leaves_the_model_alone():
    rbd = build.build_from_spec(_spec())
    row = next(r for r in rbd.section("BLOCK_DATA").rows if r.get("blockName") == "Pump")
    before = dict(row)
    cells = cell_changes(rbd, ["block:Pump.repair_params=10"])
    assert [c["column"] for c in cells] == ["RParam1"]
    assert cells[0]["section"] == "BLOCK_DATA" and float(cells[0]["new"]) == 10
    assert row == before
    with pytest.raises(PathError):
        cell_changes(rbd, ["block:Nope.repair_params=1"])


def test_cli_recommend_json(tmp_path, capsys):
    from raptor.cli.main import main
    rbd = build.build_from_spec(_spec())
    path = tmp_path / "m.rbds"
    save_rbds(rbd, str(path))
    code = main(["recommend", str(path), "--top", "1", "-n", "20", "-f", "json"])
    out = json.loads(capsys.readouterr().out)
    assert code == 0 and out["schema"] == "raptor-recommend/1"
    assert ["block:Pump.repair_params=10,0"] in [row["changes"] for row in out["rows"]]
