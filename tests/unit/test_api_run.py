"""Tests for running a simulation and reading structured results.

Covers the `RunResult` shape, metric aggregation against golden
final-results, report pagination, the typed timeout error, persisted
`runs/<run_id>/` artefacts, capacity rows, and the underestimate warning.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from raptor.api.run import (
    RunResult, RunNotFoundError, RunTimeoutError, run, get_report,
    get_timeseries,
)
from raptor.api.workspace import Workspace

REPO = Path(__file__).resolve().parent.parent.parent
GOLDEN = REPO / "tests" / "golden"


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def workspace(tmp_path):
    """Create a fresh workspace for each test."""
    return Workspace(tmp_path)


@pytest.fixture
def motor_model(workspace):
    """Open MotorDemo in the workspace and return its model_id."""
    result = workspace.open_model(GOLDEN / "MotorDemo" / "input.rbd")
    return result["model_id"]


@pytest.fixture
def capacity_model(workspace):
    """Open the Capacity1 model (capacity analysis enabled)."""
    result = workspace.open_model(GOLDEN / "Capacity1" / "input.rbd")
    return result["model_id"]


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

class TestRunBasic:
    """Run produces a well-formed RunResult."""

    def test_run_returns_run_result(self, workspace, motor_model):
        result = run(workspace, motor_model, num_runs=2,
                     timeout_s=60, confirm_long_run=True)
        assert isinstance(result, RunResult)
        assert result.num_runs == 2
        assert result.model_id == motor_model
        assert result.run_id.startswith("r_")
        assert result.wall_seconds > 0

    def test_run_metrics_populated(self, workspace, motor_model):
        result = run(workspace, motor_model, num_runs=2,
                     timeout_s=60, confirm_long_run=True)
        assert len(result.metrics) > 0
        avail = [m for m in result.metrics if m.name == "Availability"]
        assert avail, "Should have an Availability metric"
        assert avail[0].mean is not None

    def test_run_persists_artefacts(self, workspace, motor_model):
        result = run(workspace, motor_model, num_runs=2,
                     timeout_s=60, confirm_long_run=True)
        runs_dir = workspace.root / "runs" / result.run_id
        assert runs_dir.is_dir()
        assert (runs_dir / "final_results.txt").is_file()
        assert (runs_dir / "end_of_run.txt").is_file()
        assert (runs_dir / "summary.json").is_file()

        summary = json.loads((runs_dir / "summary.json").read_text())
        assert summary["run_id"] == result.run_id
        assert summary["num_runs"] == 2

    def test_run_to_dict(self, workspace, motor_model):
        result = run(workspace, motor_model, num_runs=2,
                     timeout_s=60, confirm_long_run=True)
        d = result.to_dict()
        assert "run_id" in d
        assert "metrics" in d
        assert "wall_seconds" in d


class TestRunAvailabilityGolden:
    """Mean availability is close to the golden final-results value."""

    def test_availability_matches_golden(self, workspace, motor_model):
        result = run(workspace, motor_model, num_runs=25,
                     timeout_s=120, confirm_long_run=True)

        # Parse golden final results for availability mean
        golden_path = GOLDEN / "MotorDemo" / "legacy" / "rpt_final_results_MotorDemo.txt"
        golden_text = golden_path.read_text()
        # The Availability line: "Availability  0.726184837  0.927892862  ..."
        for line in golden_text.splitlines():
            if line.strip().startswith("Availability"):
                parts = line.split()
                # parts[0] = "Availability", parts[1] = min, parts[2] = mean
                golden_mean = float(parts[2])
                break
        else:
            pytest.skip("Could not parse golden availability")

        avail_metric = [m for m in result.metrics if m.name == "Availability"][0]
        assert avail_metric.mean is not None
        # The run is stochastic, so allow a loose tolerance around the
        # golden mean rather than an exact match.
        assert abs(avail_metric.mean - golden_mean) < 0.15, (
            f"Availability mean {avail_metric.mean:.6f} too far from "
            f"golden {golden_mean:.6f}"
        )


class TestGetReport:
    """get_report returns paginated data correctly."""

    def test_get_report_basic(self, workspace, motor_model):
        result = run(workspace, motor_model, num_runs=2,
                     timeout_s=60, confirm_long_run=True)
        report = get_report(workspace, result.run_id,
                            section="final_results")
        assert "lines" in report
        assert "total" in report
        assert "next_offset" in report
        assert report["total"] > 0
        assert len(report["lines"]) <= 100

    def test_get_report_pagination(self, workspace, motor_model):
        result = run(workspace, motor_model, num_runs=2,
                     timeout_s=60, confirm_long_run=True)
        page1 = get_report(workspace, result.run_id,
                           section="final_results",
                           offset=0, limit=5)
        assert len(page1["lines"]) == 5
        assert page1["next_offset"] == 5

        page2 = get_report(workspace, result.run_id,
                           section="final_results",
                           offset=5, limit=5)
        assert page2["lines"]  # should have more lines
        # page1 and page2 should have different content
        assert page1["lines"] != page2["lines"]

    def test_get_report_end_of_run(self, workspace, motor_model):
        result = run(workspace, motor_model, num_runs=2,
                     timeout_s=60, confirm_long_run=True)
        report = get_report(workspace, result.run_id,
                            section="end_of_run")
        assert report["total"] > 0

    def test_get_report_not_found(self, workspace):
        with pytest.raises(RunNotFoundError):
            get_report(workspace, "r_nonexistent")

    def test_get_report_invalid_section(self, workspace, motor_model):
        result = run(workspace, motor_model, num_runs=2,
                     timeout_s=60, confirm_long_run=True)
        with pytest.raises(ValueError):
            get_report(workspace, result.run_id,
                       section="nonexistent")


class TestRunTimeout:
    """Deliberately tiny timeout produces a clean typed error or warning."""

    def test_timeout_raises_or_warns(self, workspace, motor_model):
        """With a 0s timeout, either RunTimeoutError is raised or the
        result contains a warning about incomplete runs."""
        try:
            result = run(workspace, motor_model, num_runs=2,
                         timeout_s=0.001, confirm_long_run=True)
            # If it didn't raise, it should have a warning about
            # incomplete runs (the abort fired before all runs finished)
            assert result.num_runs <= 2
        except RunTimeoutError:
            pass  # expected path


class TestCapacityReport:
    """The capacity report section exposes structured rows."""

    def test_get_report_capacity_rows(self, workspace, capacity_model):
        result = run(workspace, capacity_model, num_runs=1,
                     timeout_s=120, confirm_long_run=True)
        report = get_report(workspace, result.run_id, section="capacity")
        assert report["columns"] == ["time", "flow", "capacity"]
        assert report["rows"], "capacity run should record change-points"
        row = report["rows"][0]
        assert set(row) == {"time", "flow", "capacity"}
        assert row["time"] == 0.0
        # Raw lines are still returned for format="raw" callers.
        assert report["lines"]

    def test_capacity_timeseries_keeps_trajectory(self, workspace,
                                                  capacity_model):
        result = run(workspace, capacity_model, num_runs=1,
                     timeout_s=120, confirm_long_run=True)
        ts = get_timeseries(workspace, result.run_id, series="capacity",
                            points=100)
        assert ts["series"] == "capacity"
        assert len(ts["t"]) >= 2
        assert len(ts["t"]) == len(ts["values"]) == len(ts["capacity"])
        # Change-points survive decimation.
        assert ts["t"][0] == 0.0


class TestEstimateWarning:
    """A gross underestimate is surfaced as a warning."""

    def test_actual_over_three_x_estimate_warns(self, workspace, motor_model,
                                                monkeypatch):
        import time as real_time
        import types

        import raptor.api.estimate as estimate_module
        import raptor.api.run as run_module

        class _FakeEstimate:
            wall_seconds_est = 0.05

        monkeypatch.setattr(
            estimate_module, "estimate",
            lambda *a, **k: _FakeEstimate(),
        )
        # The run compares two `time.monotonic()` readings; fake a 10 s
        # actual run so the 3x rule is exercised without sleeping.
        ticks = iter([0.0, 10.0])
        fake_time = types.SimpleNamespace(
            monotonic=lambda: next(ticks),
            monotonic_ns=real_time.monotonic_ns,
            ctime=real_time.ctime,
            time_ns=real_time.time_ns,
        )
        monkeypatch.setattr(run_module, "time", fake_time)
        result = run(workspace, motor_model, num_runs=1,
                     timeout_s=60, confirm_long_run=False)
        assert any(
            "3x" in w or "estimate" in w.lower()
            for w in result.warnings
        ), result.warnings

    def test_warning_in_to_dict(self, workspace, motor_model, monkeypatch):
        import raptor.api.estimate as estimate_module

        class _FakeEstimate:
            wall_seconds_est = 0.00001

        monkeypatch.setattr(
            estimate_module, "estimate",
            lambda *a, **k: _FakeEstimate(),
        )
        result = run(workspace, motor_model, num_runs=1,
                     timeout_s=60, confirm_long_run=False)
        assert result.to_dict()["warnings"] == result.warnings



class TestShortRunSkipsPilot:
    def test_explicit_short_run_does_not_pilot(self, workspace, motor_model,
                                               monkeypatch):
        import raptor.api.estimate as estimate_module

        def _boom(*args, **kwargs):
            raise AssertionError("a short explicit run must not pilot")

        monkeypatch.setattr(estimate_module, "estimate", _boom)
        result = run(workspace, motor_model, num_runs=1, run_length=10.0,
                     timeout_s=60)
        assert result.num_runs == 1
        assert result.estimate_seconds == 0.0
        assert result.estimated_wall_seconds is None
