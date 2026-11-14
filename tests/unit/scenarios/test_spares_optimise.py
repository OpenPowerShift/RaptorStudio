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
