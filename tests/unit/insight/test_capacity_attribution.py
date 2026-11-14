"""Lost capacity per element (issue #256), checked against hand-built models.

Every test uses all-`Fixed` times and a capacity sink whose flow changes at
known instants, so the expected lost energy can be worked out by hand.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from raptor.api import build
from raptor.api.insight.attribution import aggregate, attribute_logs, attribute_trial
from raptor.api.insight.capacity_attribution import (
    CapacityRun, aggregate_capacity, attribute_capacity, capacity_analysis_on,
    dependencies_of, make_marginal_evaluator, normalise_rule,
    standalone_capacity, text_lines,
)
from raptor.api.series import parse_capacity_runs
from raptor.api.workspace import Workspace
from raptor.core.engine.runner import run_simulation, run_simulation_with_reports
from raptor.core.fileio.rbd import load_rbd
from raptor.core.fileio.rbds import save_rbds

REPO = Path(__file__).resolve().parents[3]


def fixed(name, bid, fail_at, repair, **more):
    return {"name": name, "id": bid, "fail_dist": "Fixed",
            "fail_params": [fail_at, 0, 0], "repair_dist": "Fixed",
            "repair_params": [repair, 0, 0], **more}


def parallel_spec(blocks, caps, generated=100, length=100.0):
    """Start -> each block -> End, one flow cap per branch, End a boundary."""
    links = [{"from": "Start", "to": "S"}]
    for block, cap in zip(blocks, caps, strict=False):
        links += [{"from": "S", "to": block["name"]},
                  {"from": block["name"], "to": "End",
                   "nom_flow": cap, "max_flow": cap}]
    return {"blocks": list(blocks),
            "nodes": [{"name": "Start", "type": 1},
                      {"name": "S", "type": 2, "k": 1},
                      {"name": "End", "type": 3, "full_flow": 0}],
            "links": links,
            "system": {"capacity": True, "flow_generated": generated},
            "experiment": {"trials": 1, "termination_type": "TimeSim",
                           "term_condition": length}}


def run_with_sink(rbd, runs=1):
    cap: list[str] = []
    logs = run_simulation(rbd, runs, _capacity_sink=cap)
    return logs, parse_capacity_runs("\n".join(cap))


def capacity_run(rbd, parsed, rule="proportional", generated=100.0):
    weight = (standalone_capacity(rbd)
              if rule != "first_failed" else {})
    marginal = make_marginal_evaluator(rbd) if rule == "marginal" else None
    return CapacityRun(tuple(zip(parsed["t"], parsed["flow"], strict=False)),
                       generated, weight, dependencies_of(rbd), rule, marginal)


# ---------------------------------------------------------------------------
# Proportional: three parallel units of 50/30/20
# ---------------------------------------------------------------------------

def test_proportional_splits_each_instant_by_standalone_capacity():
    # A down 10..30, B down 15..35, C down 60..70.  Every instant's loss is
    # shared 50:30:20, so the hand total is A 3000, B 1650, C 200.
    spec = parallel_spec([fixed("A", 1, 10, 20), fixed("B", 2, 15, 20),
                          fixed("C", 3, 60, 10)], [50, 30, 20])
    rbd = build.build_from_spec(spec)
    logs, sinks = run_with_sink(rbd)
    trial = attribute_trial(logs[0], 0.0, capacity_run(rbd, sinks[0]))
    capacity = trial.capacity
    assert capacity is not None
    assert capacity.lost_energy == pytest.approx(4850.0)
    assert capacity.by_element[("block", 1)] == pytest.approx(3000.0)
    assert capacity.by_element[("block", 2)] == pytest.approx(1650.0)
    assert capacity.by_element[("block", 3)] == pytest.approx(200.0)
    assert sum(capacity.by_element.values()) == pytest.approx(capacity.lost_energy)
    assert capacity.by_cause == pytest.approx({"repair": 4850.0})


def test_standalone_capacity_is_one_fev_pass_per_element():
    spec = parallel_spec([fixed("A", 1, 10, 20), fixed("B", 2, 15, 20),
                          fixed("C", 3, 60, 10)], [50, 30, 20])
    rbd = build.build_from_spec(spec)
    assert standalone_capacity(rbd) == pytest.approx(
        {("block", 1): 50.0, ("block", 2): 30.0, ("block", 3): 20.0})


def test_a_dependency_charges_the_master_and_shows_the_dependants_part():
    # B is Idle whenever A is down: A's own standalone contribution is 80
    # (50 + B's 30).  All lost energy is A's; 30/80 of it is B's part.
    spec = parallel_spec([fixed("A", 1, 10, 20),
                          fixed("B", 2, 1000, 1, dep_type="RBDBlock",
                                dep_num=1),
                          fixed("C", 3, 1000, 1)], [50, 30, 20], length=40.0)
    rbd = build.build_from_spec(spec)
    logs, sinks = run_with_sink(rbd)
    trial = attribute_trial(logs[0], 0.0, capacity_run(rbd, sinks[0]))
    capacity = trial.capacity
    assert capacity.by_element == {("block", 1): pytest.approx(1600.0)}
    assert capacity.through_dependents[("block", 1)] == pytest.approx(600.0)
    assert capacity.by_cause == pytest.approx({"repair": 1600.0})


# ---------------------------------------------------------------------------
# first_failed and marginal
# ---------------------------------------------------------------------------

def test_first_failed_gives_the_whole_loss_to_whoever_went_down_first():
    # At 12..22 A failed at 10 and B at 12: A is first and takes the whole
    # loss.  At 40..44 B (failed at 34) is first, not A (failed at 40).
    spec = parallel_spec([fixed("A", 1, 10, 20), fixed("B", 2, 12, 10),
                          fixed("C", 3, 1000, 1)], [50, 50, 50],
                         generated=100, length=50.0)
    rbd = build.build_from_spec(spec)
    logs, sinks = run_with_sink(rbd)
    trial = attribute_trial(logs[0], 0.0,
                            capacity_run(rbd, sinks[0], rule="first_failed"))
    capacity = trial.capacity
    assert capacity.lost_energy == pytest.approx(700.0)
    assert capacity.by_element[("block", 1)] == pytest.approx(500.0)
    assert capacity.by_element[("block", 2)] == pytest.approx(200.0)
    assert sum(capacity.by_element.values()) == pytest.approx(capacity.lost_energy)


def test_marginal_counts_an_overlapping_loss_once_per_element():
    # Three 50 units against a demand of 100.  No single failure loses
    # anything; two failures lose 50, and restoring either alone recovers it.
    # So each gets 50 at those instants and the shares deliberately exceed
    # the whole -- the answer to "what would fixing this one buy?".
    spec = parallel_spec([fixed("A", 1, 10, 20), fixed("B", 2, 12, 10),
                          fixed("C", 3, 1000, 1)], [50, 50, 50],
                         generated=100, length=50.0)
    rbd = build.build_from_spec(spec)
    logs, sinks = run_with_sink(rbd)
    trial = attribute_trial(logs[0], 0.0,
                            capacity_run(rbd, sinks[0], rule="marginal"))
    capacity = trial.capacity
    assert capacity.lost_energy == pytest.approx(700.0)
    assert capacity.by_element[("block", 1)] == pytest.approx(700.0)
    assert capacity.by_element[("block", 2)] == pytest.approx(700.0)
    assert sum(capacity.by_element.values()) > capacity.lost_energy
    assert any("need not add up" in note for note in capacity.notes)


def test_unknown_rule_names_the_choices():
    with pytest.raises(ValueError, match="first_failed"):
        normalise_rule("best")


# ---------------------------------------------------------------------------
# Conservation against the engine on the capacity fixtures
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("fixture", ["Capacity1", "Capacity2", "Capacity3"])
def test_lost_energy_reconciles_with_the_engines_own_lost_flow(fixture):
    """The issue names this as the oracle: the elements' lost energy sums to
    `Lost_Flow x span` from the engine, every run."""
    rbd = load_rbd(str(REPO / "tests" / "golden" / fixture / "input.rbd"))
    cap: list[str] = []
    data: list = []
    logs, _eor, _final = run_simulation_with_reports(
        rbd, num_runs=2, capacity_sink=cap, report_sink=data)
    sinks = parse_capacity_runs("\n".join(cap))
    standalone = standalone_capacity(rbd)
    deps = dependencies_of(rbd)
    assert standalone, fixture
    for log, sink, rd in zip(logs, sinks, data, strict=False):
        trial = attribute_trial(
            log, 0.0,
            CapacityRun(tuple(zip(sink["t"], sink["flow"], strict=False)),
                        float(rd.capacity.flow_generated), standalone, deps))
        capacity = trial.capacity
        system = rd.capacity.nodes[rd.capacity.system_node_id]
        engine = (rd.capacity.flow_generated - system.ave_flow) * capacity.span
        assert capacity.lost_energy == pytest.approx(engine, rel=1e-6)
        assert sum(capacity.by_element.values()) == pytest.approx(
            capacity.lost_energy, rel=1e-9)


def test_a_run_without_capacity_sink_has_no_capacity_attribution():
    spec = parallel_spec([fixed("A", 1, 10, 2)], [50], length=20.0)
    spec["system"] = {}
    rbd = build.build_from_spec(spec)
    logs = run_simulation(rbd, 1)
    trial = attribute_trial(logs[0])
    assert trial.capacity is None
    assert "capacity" not in aggregate([trial])
    assert not capacity_analysis_on(rbd)


# ---------------------------------------------------------------------------
# Aggregation, text and run() integration
# ---------------------------------------------------------------------------

def test_aggregate_capacity_keeps_the_element_keys_and_names():
    spec = parallel_spec([fixed("A", 1, 10, 20), fixed("B", 2, 15, 20),
                          fixed("C", 3, 60, 10)], [50, 30, 20])
    rbd = build.build_from_spec(spec)
    logs, sinks = run_with_sink(rbd, runs=2)
    runs = [capacity_run(rbd, sink) for sink in sinks]
    result = attribute_logs(logs, capacity=runs)
    capacity = result["capacity"]
    assert capacity["rule"] == "proportional"
    by_name = {row["name"]: row for row in capacity["elements"]}
    assert by_name["A"]["lost_energy"]["mean"] == pytest.approx(3000.0)
    assert by_name["A"]["lost_energy"]["n"] == 2
    assert by_name["A"]["share"]["mean"] == pytest.approx(3000 / 4850)
    assert by_name["A"]["standalone"] == pytest.approx(50.0)
    # the same figures are on the element rows, beside DTCI and DECI
    element = next(e for e in result["elements"] if e["name"] == "A")
    assert element["lost_capacity"]["mean"] == pytest.approx(3000.0)
    assert sum(c["mean"] for c in capacity["causes"].values()) == pytest.approx(
        capacity["lost_energy"]["mean"])
    text = "\n".join(text_lines(capacity))
    assert "attributed by proportional split" in text
    assert "A" in text and "Through" not in text


def test_aggregate_capacity_is_none_without_capacity():
    assert aggregate_capacity([]) is None


def test_run_writes_the_capacity_block_and_its_rule(tmp_path):
    spec = parallel_spec([fixed("A", 1, 10, 20), fixed("B", 2, 15, 20),
                          fixed("C", 3, 1000, 1)], [50, 30, 20], length=40.0)
    rbd = build.build_from_spec(spec)
    path = tmp_path / "capacity.rbds"
    save_rbds(rbd, str(path))
    workspace = Workspace(tmp_path / "ws")
    model_id = workspace.open_model(path)["model_id"]
    from raptor.api.run import run
    result = run(workspace, model_id, num_runs=2, attribution=True,
                 capacity_rule="proportional", confirm_long_run=True)
    run_dir = tmp_path / "ws" / "runs" / result.run_id
    attribution = json.loads((run_dir / "attribution.json").read_text())
    capacity = attribution["capacity"]
    assert capacity["rule"] == "proportional"
    assert capacity["lost_energy"]["n"] == 2
    names = {row["name"] for row in capacity["elements"]}
    assert {"A", "B"} <= names


def test_attribute_capacity_returns_only_the_capacity_half():
    spec = parallel_spec([fixed("A", 1, 10, 2)], [50], length=20.0,
                         generated=100)
    rbd = build.build_from_spec(spec)
    logs, sinks = run_with_sink(rbd)
    capacity = attribute_capacity(
        logs[0], list(zip(sinks[0]["t"], sinks[0]["flow"], strict=False)),
        50.0, standalone={("block", 1): 50.0})
    assert capacity.lost_energy == pytest.approx(2 * 50.0)
    assert capacity.by_element == {("block", 1): pytest.approx(100.0)}


def test_the_fast_parallel_path_agrees_with_the_failure_effects_view():
    """The one-link fast path must give the same number as a full FEV pass,
    or it is not allowed to be used."""
    from raptor.api.insight.capacity_attribution import _fail_one, _FastCapacity
    from raptor.core.engine.fev import FevSession

    spec = parallel_spec([fixed("A", 1, 10, 20), fixed("B", 2, 15, 20),
                          fixed("C", 3, 60, 10)], [50, 30, 20])
    rbd = build.build_from_spec(spec)
    session = FevSession(rbd)
    session.refresh_all()
    fast = _FastCapacity(session)
    assert fast.ready()
    for block in list(session.model.blocks_by_id.values()):
        key = ("block", int(block.id))
        expected_session = FevSession(rbd)
        expected_session.refresh_all()
        expected = _fail_one(expected_session, "block", int(block.id))
        assert fast.contribution(key) == pytest.approx(expected, abs=1e-9), key


def test_a_series_model_falls_back_to_the_full_pass():
    from raptor.api.insight.capacity_attribution import _FastCapacity
    from raptor.core.engine.fev import FevSession

    spec = series_spec_single()
    rbd = build.build_from_spec(spec)
    session = FevSession(rbd)
    session.refresh_all()
    assert not _FastCapacity(session).ready()
    assert standalone_capacity(rbd) == pytest.approx({("block", 1): 100.0})


def series_spec_single():
    return {"blocks": [fixed("A", 1, 10, 2)],
            "nodes": [{"name": "Start", "type": 1},
                      {"name": "End", "type": 3, "full_flow": 0}],
            "links": [{"from": "Start", "to": "A"},
                      {"from": "A", "to": "End", "nom_flow": 100,
                       "max_flow": 100}],
            "system": {"capacity": True, "flow_generated": 100},
            "experiment": {"trials": 1, "termination_type": "TimeSim",
                           "term_condition": 20.0}}


def test_identical_parallel_units_reuse_one_evaluation_exactly():
    """Four equal 50-unit branches against 100 demand: no single failure
    loses flow.  The grouping cache must give the full pass's answer."""
    from raptor.api.insight.capacity_attribution import _fail_one
    from raptor.core.engine.fev import FevSession

    spec = parallel_spec([fixed("A", 1, 10, 10), fixed("B", 2, 20, 10),
                          fixed("C", 3, 30, 10), fixed("D", 4, 40, 10)],
                         [50, 50, 50, 50], generated=100, length=60.0)
    rbd = build.build_from_spec(spec)
    values = standalone_capacity(rbd)
    assert values == pytest.approx({("block", i): 0.0 for i in (1, 2, 3, 4)})
    for block_id in (1, 2, 3, 4):
        session = FevSession(rbd)
        session.refresh_all()
        assert values[("block", block_id)] == pytest.approx(
            _fail_one(session, "block", block_id))
