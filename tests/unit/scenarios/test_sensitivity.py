"""One-at-a-time sensitivity: known swings, paired effects, elasticity."""
from __future__ import annotations

import pytest

from raptor.api import build
from raptor.api.scenarios.sensitivity import (
    Input, default_inputs, tornado,
)
from raptor.api.workspace import Workspace

SPEC = {
    "blocks": [{"name": "Pump", "fail_dist": "Fixed",
                "fail_params": [100, 0, 0], "repair_dist": "Fixed",
                "repair_params": [2, 0, 0]}],
    "nodes": [{"name": "Start", "type": 1}, {"name": "End", "type": 3}],
    "links": [{"from": "Start", "to": "Pump"},
              {"from": "Pump", "to": "End"}],
    "experiment": {"trials": 5, "termination_type": "TimeSim",
                   "term_condition": 500.0},
}


def _workspace(tmp_path):
    ws = Workspace(tmp_path)
    rbd = build.build_from_spec(SPEC)
    mid = ws.register_model(rbd, name="m")["model_id"]
    return ws, mid, rbd


def test_default_inputs_offer_life_repair_and_the_stock(tmp_path):
    _ws, _mid, rbd = _workspace(tmp_path)
    inputs = default_inputs(rbd)
    kinds = [item.kind for item in inputs]
    assert "failure" in kinds and "repair" in kinds
    assert all("=" in item.low and "=" in item.high for item in inputs)
    assert [item.name for item in inputs][:2] == ["Pump life", "Pump repair"]


def test_a_longer_repair_swings_availability_down(tmp_path):
    ws, mid, _rbd = _workspace(tmp_path)
    inputs = [
        Input("repair", "repair",
              "block:Pump.repair_params=1,0,0",
              "block:Pump.repair_params=4,0,0",
              base_value=2.0, low_value=1.0, high_value=4.0)]
    result = tornado(ws, mid, inputs, trials=3, seed=2)
    row = result["inputs"][0]
    assert row["error"] is None
    assert row["low"]["effect"] > row["high"]["effect"]
    assert row["swing"] == pytest.approx(
        row["low"]["effect"] - row["high"]["effect"])
    assert row["elasticity"] is not None
    assert result["base"]["mean"] == pytest.approx(0.984)


def test_a_no_op_input_has_exactly_zero_swing(tmp_path):
    ws, mid, _rbd = _workspace(tmp_path)
    result = tornado(ws, mid, [
        Input("same", "repair", "block:Pump.repair_params=2,0,0",
              "block:Pump.repair_params=2,0,0")], trials=3, seed=1)
    row = result["inputs"][0]
    assert row["swing"] == 0
    assert row["distinguishable"] is False


def test_the_same_seed_pairs_every_effect(tmp_path):
    ws, mid, _rbd = _workspace(tmp_path)
    first = tornado(ws, mid, [
        Input("repair", "repair", "block:Pump.repair_params=1,0,0",
              "block:Pump.repair_params=4,0,0")], trials=4, seed=9)
    second = tornado(ws, mid, [
        Input("repair", "repair", "block:Pump.repair_params=1,0,0",
              "block:Pump.repair_params=4,0,0")], trials=4, seed=9)
    assert first["inputs"][0]["swing"] == second["inputs"][0]["swing"]
    assert first["batches"] == 3
