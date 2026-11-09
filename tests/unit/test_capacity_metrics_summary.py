"""A capacity-analysis run reports capacity-weighted availability (Mean_Flow /
FlowGenerated) and lost flow alongside Availability, which only says some flow got
through (issue #4, item 7)."""
from __future__ import annotations

import json
from dataclasses import replace
from pathlib import Path

import pytest

from raptor.api import batch, metric_definitions
from raptor.api.results import (CAPACITY_FIELDS, COST_FIELDS, aggregate_metrics,
                                analysis_flags, cost_values, metrics_to_dict)
from raptor.api.run import SUMMARY_VERSION, _rd_to_dict
from raptor.api.workspace import Workspace
from raptor.core.engine.runner import run_simulation_with_reports
from raptor.core.fileio.rbd import load_rbd

GOLD = Path(__file__).resolve().parents[1] / "golden"
CAPACITY_MODEL = str(GOLD / "Capacity2" / "input.rbd")
PLAIN_MODEL = str(GOLD / "HierarchyExample" / "input.rbd")
#: Cost analysis on, capacity analysis off (and red cost configured).
COST_ONLY_MODEL = str(GOLD / "MotorDemo" / "input.rbd")


def _report_data(path, runs=4):
    sink: list = []
    run_simulation_with_reports(load_rbd(path), num_runs=runs, report_sink=sink,
                                collect_logs=False)
    return sink


def test_a_capacity_run_summary_has_the_capacity_metrics():
    data = _report_data(CAPACITY_MODEL)
    metrics = metrics_to_dict(aggregate_metrics(data))
    for _key, name in CAPACITY_FIELDS:
        assert metrics[name]["mean"] is not None, name
    cap = data[0].capacity
    generated = float(cap.flow_generated)
    flows = [rd.capacity.nodes[rd.capacity.system_node_id].ave_flow for rd in data]
    assert metrics["Mean_Flow"]["mean"] == pytest.approx(sum(flows) / len(flows))
    assert metrics["Capacity_Weighted_Availability"]["mean"] == pytest.approx(
        metrics["Mean_Flow"]["mean"] / generated)
    assert metrics["Lost_Flow"]["mean"] == pytest.approx(
        generated - metrics["Mean_Flow"]["mean"])


def test_a_run_without_capacity_analysis_has_no_capacity_metrics():
    metrics = metrics_to_dict(aggregate_metrics(_report_data(PLAIN_MODEL)))
    assert not {name for _k, name in CAPACITY_FIELDS} & set(metrics)
    view = metric_definitions.from_summary(metrics, runs=4)
    assert "Mean flow" not in {row["metric"] for row in view["metrics"]}


def test_describe_metrics_lists_and_shows_the_capacity_metrics():
    names = {row["aggregate"] for row in metric_definitions.definitions()}
    assert {name for _k, name in CAPACITY_FIELDS} <= names
    metrics = metrics_to_dict(aggregate_metrics(_report_data(CAPACITY_MODEL)))
    view = metric_definitions.from_summary(metrics, runs=4)
    shown = {row["aggregate"]: row for row in view["metrics"]}
    assert shown["Capacity_Weighted_Availability"]["mean"] is not None


# ---------------------------------------------------------------------------
# Cost analysis (issue #209)
# ---------------------------------------------------------------------------

def test_a_cost_run_summary_has_the_cost_metrics():
    data = _report_data(CAPACITY_MODEL)
    metrics = metrics_to_dict(aggregate_metrics(data))
    for key, name in COST_FIELDS:
        expected = sum(getattr(rd.cost, key) for rd in data) / len(data)
        assert metrics[name]["mean"] == pytest.approx(expected), name


def test_cost_values_mirrors_the_run_cost_report():
    data = _report_data(CAPACITY_MODEL, runs=1)
    values = cost_values(data[0])
    assert values is not None
    for key, _name in COST_FIELDS:
        assert values[key] == pytest.approx(getattr(data[0].cost, key))


def test_a_run_without_cost_analysis_has_no_cost_metrics():
    plain = _report_data(PLAIN_MODEL)
    assert cost_values(plain[0]) is None
    metrics = metrics_to_dict(aggregate_metrics(plain))
    assert not {name for _key, name in COST_FIELDS} & set(metrics)


def test_cost_with_capacity_off_omits_lost_flow_cost():
    """SystemLostFlowCost is written as 0 when capacity analysis is off;
    that is not a real cost and must not enter a comparison."""
    data = _report_data(COST_ONLY_MODEL, runs=1)
    assert data[0].capacity is None and data[0].cost is not None
    values = cost_values(data[0])
    assert values is not None
    assert "grand_total" in values
    assert "system_down_cost" in values
    assert "system_lost_flow_cost" not in values
    metrics = metrics_to_dict(aggregate_metrics(data))
    assert "Grand_Total" in metrics and "System_Down_Cost" in metrics
    assert "System_Lost_Flow_Cost" not in metrics


def test_cost_without_configured_red_cost_omits_system_down_cost():
    data = _report_data(str(GOLD / "DepSpareCostProbe" / "input.rbd"),
                        runs=1)
    values = cost_values(data[0])
    assert values is not None
    assert "grand_total" in values
    assert "system_down_cost" not in values


def test_mixed_cost_data_aggregates_the_samples_that_exist():
    data = _report_data(CAPACITY_MODEL, runs=1)
    mixed = [data[0], replace(data[0], cost=None)]
    metrics = metrics_to_dict(aggregate_metrics(mixed))
    for key, name in COST_FIELDS:
        assert metrics[name]["mean"] == pytest.approx(
            getattr(data[0].cost, key)), name


def test_cost_values_tolerates_a_partial_snapshot():
    data = _report_data(CAPACITY_MODEL, runs=1)
    partial = replace(data[0], cost=replace(
        data[0].cost, grand_total=None, system_down_cost=None,
        system_lost_flow_cost=None))
    values = cost_values(partial)
    assert values == {}


def test_cost_values_reach_the_per_run_row():
    data = _report_data(CAPACITY_MODEL, runs=1)
    row = _rd_to_dict(data[0])
    for key, _name in COST_FIELDS:
        assert row[key] == pytest.approx(getattr(data[0].cost, key))


def test_a_batch_summary_includes_cost_metrics_and_analyses(tmp_path):
    ws = Workspace(tmp_path)
    model_id = ws.open_model(CAPACITY_MODEL)["model_id"]
    (entry,) = batch.run_variants(
        ws, model_id, [{"label": "cost", "assignments": []}],
        num_runs=1, workers=1)
    assert "error" not in entry, entry
    for _key, name in COST_FIELDS:
        assert entry["summary"][name]["mean"] is not None, name
    assert entry["analyses"] == {"capacity": True, "cost": True}
    summary = json.loads(
        (ws.root / "runs" / entry["run_id"] / "summary.json").read_text())
    assert summary["summary_version"] == SUMMARY_VERSION
    assert summary["analyses"] == {"capacity": True, "cost": True}
    assert analysis_flags([_report_data(COST_ONLY_MODEL, runs=1)[0]]) == {
        "capacity": False, "cost": True}
