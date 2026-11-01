"""Derived availability classifications, point availability and criticality."""
from __future__ import annotations

from pathlib import Path

import pytest

from raptor.api.availability import (
    availability_at, availability_at_run, availability_summary, criticality_rows)
from raptor.api.run import run
from raptor.api.workspace import Workspace

MODELS = Path(__file__).resolve().parents[2] / "new_docs" / "models"


@pytest.fixture(scope="module")
def vendo_run(tmp_path_factory):
    workspace = Workspace(tmp_path_factory.mktemp("availability"))
    model_id = workspace.open_model(MODELS / "Vendo1.rbds")["model_id"]
    result = run(workspace, model_id, num_runs=10, confirm_long_run=True)
    return workspace, result


def test_availability_classifications_are_present(vendo_run):
    _workspace, result = vendo_run
    row = result.per_run[0]
    assert row["operational_availability"] == pytest.approx(row["availability"])
    for key in ("inherent_availability", "achieved_availability"):
        value = row[key]
        assert value is not None
        assert 0.0 <= value <= 1.0
    assert row["mttff"] is not None
    assert row["mtbf_total"] is not None
    assert row["mtbe_total"] is not None
    assert row["num_component_cms"] is not None
    assert row["num_component_pms"] is not None


def test_point_availability_reads_the_colour_timeline(vendo_run):
    workspace, result = vendo_run
    row = result.per_run[0]
    timeline = row["color_timeline"]
    assert timeline[0] == [0.0, "Green"]
    assert availability_at(row, 0.0) == 1.0
    red = next((entry for entry in timeline if entry[1] == "Red"), None)
    if red is not None:
        assert availability_at(row, float(red[0]) + 1e-9) == 0.0
    point = availability_at_run(workspace, result.run_id, 0.0)
    assert point["availability"] == 1.0
    assert point["runs"] == len(result.per_run)


def test_criticality_failure_shares_sum_to_one(vendo_run):
    workspace, result = vendo_run
    rows = criticality_rows(workspace, result.run_id)
    assert {row["name"] for row in rows} == {"Compressor", "Keypad"}
    assert sum(row["failure"] for row in rows) == pytest.approx(1.0)
    assert all(row["downing_event"] is None for row in rows)


def test_availability_summary_shape(vendo_run):
    _workspace, result = vendo_run
    summary = availability_summary(result.per_run[0])
    assert "availability" in summary
    assert "mttff" in summary


def test_criticality_downtime_and_cost_when_analyses_are_on(tmp_path):
    workspace = Workspace(tmp_path)
    model_id = workspace.open_model(
        Path(__file__).resolve().parents[1] / "golden" / "LadL7Weak"
        / "input.rbd")["model_id"]
    result = run(workspace, model_id, num_runs=3, confirm_long_run=True)
    rows = criticality_rows(workspace, result.run_id)
    assert rows
    assert all(row["downtime"] is not None for row in rows)
    assert all(row["cost"] is not None for row in rows)
    assert sum(row["downtime"] for row in rows) == pytest.approx(1.0)
    assert sum(row["cost"] for row in rows) == pytest.approx(1.0)
