"""Marginal spare and crew value: discovery, pairing and skipping."""
from __future__ import annotations

from raptor.api.workspace import Workspace
from raptor.api.scenarios.marginal import (
    MarginalItem, candidates, marginal_value,
)
from raptor.core.fileio.rbd import load_rbd

REPO_GOLD = "tests/golden/SpaceStation/input.rbd"


def _workspace(tmp_path):
    ws = Workspace(tmp_path)
    mid = ws.register_model(load_rbd(REPO_GOLD), name="station")["model_id"]
    return ws, mid


def test_candidates_find_pools_and_crews():
    items = candidates(load_rbd(REPO_GOLD))
    kinds = {item.kind for item in items}
    assert "pool" in kinds and "resource" in kinds
    assert any(item.name == "Astronauts crew" for item in items)
    assert all("=" in item.plus for item in items)


def test_one_more_is_paired_and_a_skipped_item_costs_nothing(tmp_path):
    ws, mid = _workspace(tmp_path)
    items = candidates(ws.get(mid).rbd)
    item = next(item for item in items if item.kind == "resource")
    result = marginal_value(
        ws, mid, items=[item], trials=2, seed=3)
    row = result["items"][0]
    assert row["error"] is None
    assert row["item"]["name"] == item.name
    assert result["batches"] == 2          # Base and +1 (crews never go down)
    skipped = marginal_value(
        ws, mid, items=items, trials=2, seed=3,
        skip={i.name for i in items if i.kind != "pool"})
    base_only = 1 + sum(2 if item.minus else 1 for item in items
                        if item.kind == "pool")
    assert skipped["batches"] == base_only
    assert skipped["skipped"]


def test_no_candidates_is_said_not_crashed(tmp_path):
    from raptor.api import build
    ws = Workspace(tmp_path)
    rbd = build.build_from_spec({
        "blocks": [{"name": "P", "fail_dist": "Fixed",
                    "fail_params": [10, 0, 0], "repair_dist": "Fixed",
                    "repair_params": [1, 0, 0]}],
        "nodes": [{"name": "S", "type": 1}, {"name": "E", "type": 3}],
        "links": [{"from": "S", "to": "P"}, {"from": "P", "to": "E"}],
        "experiment": {"trials": 2, "termination_type": "TimeSim",
                       "term_condition": 50.0}})
    mid = ws.register_model(rbd, name="m")["model_id"]
    result = marginal_value(ws, mid, items=[MarginalItem(
        "Same", "pool", "system.length=50", None)], trials=2, seed=1)
    assert result["caption"]
