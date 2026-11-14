"""PM advice: the age-replacement screen and a small simulated search."""
from __future__ import annotations


from raptor.api import build
from raptor.api.optimise.pm import (
    cost_rate, screen, weibull_optimum,
)
from raptor.api.workspace import Workspace

SPEC = {
    "blocks": [{"name": "Pump", "fail_dist": "Weibull",
                "fail_params": [2.0, 100.0, 0.0],
                "repair_dist": "Fixed", "repair_params": [5, 0, 0],
                "uses_pm": 1, "pm_freq": 400.0,
                "pm_dist": "Fixed", "pm_params": [2, 0, 0],
                "pm_cost": 1.0, "pm_fixed_cost": 10.0,
                "rep_fixed_cost": 100.0}],
    "nodes": [{"name": "Start", "type": 1}, {"name": "End", "type": 3}],
    "links": [{"from": "Start", "to": "Pump"},
              {"from": "Pump", "to": "End"}],
    "experiment": {"trials": 4, "termination_type": "TimeSim",
                   "term_condition": 2000.0},
}


def test_a_wear_out_law_has_a_finite_cost_optimum():
    optimum = weibull_optimum(2.0, 100.0, cp=1.0, cf=10.0)
    assert optimum is not None and 0 < optimum < 300
    assert cost_rate(optimum, 2.0, 100.0, 1.0, 10.0) < cost_rate(
        optimum * 0.4, 2.0, 100.0, 1.0, 10.0)
    assert cost_rate(optimum, 2.0, 100.0, 1.0, 10.0) < cost_rate(
        optimum * 3, 2.0, 100.0, 1.0, 10.0)


def test_a_constant_failure_rate_is_not_worth_preventing():
    assert weibull_optimum(1.0, 100.0, 1.0, 10.0) is None
    assert weibull_optimum(0.7, 100.0, 1.0, 10.0) is None


def test_the_screen_names_the_block_and_the_interval():
    rbd = build.build_from_spec(SPEC)
    advice = screen(rbd)
    assert len(advice) == 1
    entry = advice[0]
    assert entry.block == "Pump" and entry.worthwhile is True
    assert entry.interval is not None and entry.cp >= 1 and entry.cf >= 1


def test_the_simulated_search_reports_a_curve_and_a_recommendation(tmp_path):
    from raptor.api.optimise.pm import optimise_pm
    ws = Workspace(tmp_path)
    mid = ws.register_model(build.build_from_spec(SPEC), name="m")["model_id"]
    result = optimise_pm(ws, mid, trials=3, seed=2)
    assert result["recommendations"]
    rec = result["recommendations"][0]
    assert rec["block"] == "Pump"
    options = {row["option"] for row in rec["curve"]}
    assert "no PM" in options
    assert any("optimum" in option for option in options)
    assert result["batches"] == 1 + 6
    assert result["caption"]
