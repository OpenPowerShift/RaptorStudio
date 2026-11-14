"""Spare optimisation: the backorder seed and a small end-to-end case."""
from __future__ import annotations

import math

import pytest

from raptor.api import build
from raptor.api.optimise.spares import (
    PoolSpec, analytic_path, optimise_spares, poisson_ebo,
)
from raptor.api.workspace import Workspace

SPEC = {
    "blocks": [{"name": "Pump", "fail_dist": "Fixed",
                "fail_params": [100, 0, 0], "repair_dist": "Fixed",
                "repair_params": [1, 0, 0],
                "spare_pool": "Seals", "sp_type": "Pooled"}],
    "spare_pools": [{"name": "Seals", "stock": 0, "rso": 1,
                     "new_spares": 1, "arrive_every": 200.0}],
    "nodes": [{"name": "Start", "type": 1}, {"name": "End", "type": 3}],
    "links": [{"from": "Start", "to": "Pump"},
              {"from": "Pump", "to": "End"}],
    "experiment": {"trials": 4, "termination_type": "TimeSim",
                   "term_condition": 1000.0},
}


def test_ebo_matches_the_direct_poisson_sum():
    mean = 2.0
    for stock in range(5):
        direct = sum((k - stock) * math.exp(-mean) * mean ** k
                     / math.factorial(k) for k in range(stock + 1, 60))
        assert poisson_ebo(mean, stock) == pytest.approx(direct, rel=1e-9)


def test_the_analytic_path_never_lowers_a_stock_and_is_efficient():
    pools = [PoolSpec("A", 0, 10.0, 0.02, 20.0),
             PoolSpec("B", 0, 1.0, 0.02, 20.0)]
    path = analytic_path(pools)
    assert path
    stocks = path[-1]["stocks"]
    assert stocks["A"] <= stocks["B"]        # the cheaper pool gains first
    for i in range(1, len(path)):
        assert sum(path[i]["stocks"].values()) == i + 1


def test_the_optimiser_finds_the_stock_that_meets_the_bound(tmp_path):
    ws = Workspace(tmp_path)
    mid = ws.register_model(build.build_from_spec(SPEC), name="m")["model_id"]
    result = optimise_spares(
        ws, mid, mode="meet", target=0.85, max_evaluations=8,
        trials=4, seed=2)
    recommended = result["recommended"]
    assert recommended is not None
    assert recommended["stocks"]["Seals"] >= 1
    assert recommended["lower"] >= 0.85
    assert result["evaluations"] <= 8
    assert result["base"]["stocks"]["Seals"] == 0
    assert result["caption"].startswith("Recommended")


def test_curve_mode_returns_the_points_it_paid_for(tmp_path):
    ws = Workspace(tmp_path)
    mid = ws.register_model(build.build_from_spec(SPEC), name="m")["model_id"]
    result = optimise_spares(
        ws, mid, mode="curve", max_evaluations=6, trials=3, seed=1)
    assert result["points"]
    assert result["recommended"] is not None
    assert result["evaluations"] <= 6
    assert result["costs_default"] is True


def test_the_lead_time_follows_the_pool_resupply_rule():
    from raptor.api.optimise.spares import lead_time
    assert lead_time({"slo": 1, "slo_time": 48, "eso": 1, "emer_time": 5},
                     1000) == (48.0, "stock-level order")
    assert lead_time({"eso": "1", "emer_time": 5}, 1000)[0] == 5.0
    assert lead_time({"rso": 1, "arrive_every": 200}, 1000)[0] == 200.0
    assert lead_time({}, 1000) == (1000, "no resupply: the whole run")


def test_the_seed_demand_is_the_sum_of_member_failure_rates():
    from raptor.api.optimise.spares import pools_from_model
    spec = {**SPEC, "blocks": [
        dict(SPEC["blocks"][0], name="P1"),
        dict(SPEC["blocks"][0], name="P2", fail_params=[50, 0, 0])],
        "links": [{"from": "Start", "to": "P1"}, {"from": "P1", "to": "P2"},
                  {"from": "P2", "to": "End"}]}
    (pool,) = pools_from_model(build.build_from_spec(spec))
    assert pool.demand == pytest.approx(1 / 100 + 1 / 50)
    assert pool.lead_time == 200.0


def test_mdt_is_met_by_the_upper_bound_not_the_lower():
    from raptor.api.optimise.spares import _meets
    point = {"mean": 10.0, "lower": 8.0, "upper": 12.0}
    assert _meets(point, 13.0, "mdt") is True
    assert _meets(point, 11.0, "mdt") is False
    assert _meets(point, 9.0, "availability") is False
    assert _meets(point, 7.0, "availability") is True


def test_an_unmet_target_is_not_captioned_as_a_recommendation(tmp_path):
    ws = Workspace(tmp_path)
    mid = ws.register_model(build.build_from_spec(SPEC), name="m")["model_id"]
    result = optimise_spares(ws, mid, mode="meet", target=1.5,
                             max_evaluations=3, trials=4, seed=2)
    assert result["target_met"] is False
    assert result["caption"].startswith("No evaluated stock met the target")
