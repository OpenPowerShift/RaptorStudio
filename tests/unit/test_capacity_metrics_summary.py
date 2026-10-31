"""A capacity-analysis run reports capacity-weighted availability (Mean_Flow /
FlowGenerated) and lost flow alongside Availability, which only says some flow got
through (issue #4, item 7)."""
from __future__ import annotations

from pathlib import Path

import pytest

from raptor.api import metric_definitions
from raptor.api.results import CAPACITY_FIELDS, aggregate_metrics, metrics_to_dict
from raptor.core.engine.runner import run_simulation_with_reports
from raptor.core.fileio.rbd import load_rbd

GOLD = Path(__file__).resolve().parents[1] / "golden"
CAPACITY_MODEL = str(GOLD / "Capacity2" / "input.rbd")
PLAIN_MODEL = str(GOLD / "HierarchyExample" / "input.rbd")


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
