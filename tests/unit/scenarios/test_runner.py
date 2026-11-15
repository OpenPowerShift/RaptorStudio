"""What-if scenarios: paired comparisons, null changes and the base model."""
from __future__ import annotations

from raptor.api import build
from raptor.api.run import model_hash
from raptor.api.scenarios import Scenario, compare
from raptor.api.workspace import Workspace
from raptor.core.fileio.rbds import save_rbds


def _workspace(tmp_path):
    spec = {
        "blocks": [{"name": "Pump", "fail_dist": "Fixed",
                    "fail_params": [20, 0, 0], "repair_dist": "Fixed",
                    "repair_params": [2, 0, 0]}],
        "nodes": [{"name": "Start", "type": 1}, {"name": "End", "type": 3}],
        "links": [{"from": "Start", "to": "Pump"},
                  {"from": "Pump", "to": "End"}],
        "experiment": {"trials": 10, "termination_type": "TimeSim",
                       "term_condition": 200.0},
    }
    rbd = build.build_from_spec(spec)
    path = tmp_path / "m.rbds"
    save_rbds(rbd, str(path))
    ws = Workspace(tmp_path / "ws")
    return ws, ws.open_model(path)["model_id"]


def test_a_slower_repair_is_worse_and_paired(tmp_path):
    ws, mid = _workspace(tmp_path)
    before = model_hash(ws.get(mid).rbd)
    comparison = compare(
        ws, mid, [Scenario("Slow repair",
                           ("block:Pump.repair_params=8,0,0",))],
        trials=10, seed=3)
    row = comparison.rows[0]
    availability = row["metrics"]["availability"]
    assert row["error"] is None and availability["paired"] is True
    assert availability["delta"]["mean"] < 0
    assert availability["p_better"] < 0.5
    assert comparison.variance_reduction["availability"] is not None
    assert model_hash(ws.get(mid).rbd) == before


def test_a_null_change_differs_by_exactly_zero(tmp_path):
    ws, mid = _workspace(tmp_path)
    comparison = compare(ws, mid, [Scenario("Same model")], trials=5, seed=2)
    delta = comparison.rows[0]["metrics"]["availability"]["delta"]
    assert delta["mean"] == 0 and delta["sem"] == 0
    assert comparison.rows[0]["metrics"]["availability"]["p_better"] == 0.5


def test_a_bad_change_is_reported_and_the_rest_still_run(tmp_path):
    ws, mid = _workspace(tmp_path)
    comparison = compare(
        ws, mid, [Scenario("Bad", ("no_such:path=1",)),
                  Scenario("Good", ("block:Pump.repair_params=1,0,0",))],
        trials=3, seed=1)
    assert comparison.rows[0]["error"]
    assert comparison.rows[1]["error"] is None


def test_requirements_are_checked_for_base_and_each_scenario(tmp_path):
    ws, mid = _workspace(tmp_path)
    comparison = compare(
        ws, mid, [Scenario("Same")], trials=3, seed=1,
        requirements=[{"metric": "system.ao", "op": ">=", "value": 0.0,
                       "label": "Any availability"}])
    assert comparison.requirements[0]["base"] == "met"
    assert comparison.requirements[0]["scenarios"]["Same"] == "met"


def test_the_caption_says_when_several_scenarios_were_tried(tmp_path):
    ws, mid = _workspace(tmp_path)
    comparison = compare(
        ws, mid, [Scenario("One"), Scenario("Two")], trials=3, seed=1)
    assert "With several scenarios" in comparison.caption
    assert comparison.to_dict()["trials"] == 3


def test_a_fall_in_time_down_is_counted_as_better():
    from raptor.api.scenarios.runner import _paired
    base = [10.0, 12.0, 11.0, 9.0]
    shorter = [8.0, 10.5, 9.0, 8.0]
    _stat, p_up = _paired(base, shorter)
    _stat, p_better = _paired(base, shorter, lower_better=True)
    assert p_up < 0.05 and p_better > 0.95


def test_the_caption_follows_the_direction_of_the_change():
    from raptor.api.scenarios.runner import _caption

    def row(name, delta):
        return {"name": name, "metrics": {"availability": {"delta": {"mean": delta}}}}
    assert "does not change" in _caption([row("S", 0.0)])
    assert "largest gain" in _caption([row("S", 0.01), row("T", -0.01)])
    text = _caption([row("S", -0.02), row("T", -0.01)])
    assert text.startswith("No scenario improves") and "T" in text


def test_p_better_uses_students_t_at_small_counts():
    from raptor.api.scenarios.runner import _paired
    base = [0.0, 0.0, 0.0]
    other = [1.0, 2.0, 3.0]            # mean 2, sem 0.577, t = 3.46 on 2 df
    _stat, p = _paired(base, other)
    assert abs(p - 0.9629) < 1e-3     # the normal said 0.9997
