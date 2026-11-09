"""Tests for the analyses API.

Covers single-point failures, failure effects, model comparison, and series
parsing / decimation. Uses the checked-in `tests/golden/` models Cascade,
MotorDemo and TwoBlockRedundancy.
"""
from __future__ import annotations

from pathlib import Path

import pytest

from raptor.api.analyse import (
    single_point_failures, failure_effects, compare,
)
from raptor.api.series import parse_ao_text, decimate, parse_and_decimate
from raptor.api.workspace import Workspace

REPO = Path(__file__).resolve().parent.parent.parent
GOLDEN = REPO / "tests" / "golden"


@pytest.fixture
def workspace(tmp_path):
    return Workspace(tmp_path)


@pytest.fixture
def cascade_model(workspace):
    result = workspace.open_model(GOLDEN / "Cascade" / "input.rbd")
    return result["model_id"]


@pytest.fixture
def motor_model(workspace):
    result = workspace.open_model(GOLDEN / "MotorDemo" / "input.rbd")
    return result["model_id"]


@pytest.fixture
def redundant_model(workspace):
    result = workspace.open_model(
        GOLDEN / "TwoBlockRedundancy" / "input.rbd"
    )
    return result["model_id"]


def _cache_run(workspace, run_id, means, *, analyses=None,
               summary_version=None):
    """Write a cached run summary the way `compare` reads one."""
    import json

    path = workspace.root / "runs" / run_id
    path.mkdir(parents=True)
    summary = {
        "run_id": run_id,
        "model_id": run_id,
        "model_name": run_id,
        "num_runs": 1,
        "metrics": {
            name: {"name": name, "mean": value}
            for name, value in means.items()},
    }
    if analyses is not None:
        summary["analyses"] = analyses
    if summary_version is not None:
        summary["summary_version"] = summary_version
    (path / "summary.json").write_text(json.dumps(summary))
    return path


class TestSPF:
    """Single-point-failure results for Cascade and MotorDemo."""

    def test_cascade_spf(self, workspace, cascade_model):
        rows = single_point_failures(workspace, cascade_model)
        assert isinstance(rows, list)
        # Cascade is well-redundant, so it has no single point failures.
        assert len(rows) == 0 or all(
            "kind" in r and "id" in r and "name" in r for r in rows
        )

    def test_spf_returns_dicts(self, workspace, motor_model):
        rows = single_point_failures(workspace, motor_model)
        assert isinstance(rows, list)
        for row in rows:
            assert "kind" in row
            assert "id" in row
            assert "name" in row


class TestFailureEffects:
    """failure_effects produces system colour and log lines."""

    def test_failure_effects_basic(self, workspace, motor_model):
        result = failure_effects(workspace, motor_model, fail=[1])
        assert result["system_status"] in ("Green", "Red", "Yellow")
        assert isinstance(result["log"], list)
        assert result["affected"] == ["1"]

    def test_failure_effects_repair(self, workspace, motor_model):
        result = failure_effects(
            workspace, motor_model, fail=[1], repair=[1],
        )
        assert result["system_status"] in ("Green", "Red", "Yellow")

    def test_redundant_failure_is_yellow_not_red(self, workspace,
                                                 redundant_model):
        """One of two redundant elements failing leaves the system working."""
        result = failure_effects(workspace, redundant_model, fail=[3])
        assert result["system_status"] == "Yellow"

    def test_non_redundant_failure_is_red(self, workspace, motor_model):
        """A failure that takes the End node down is still Red."""
        result = failure_effects(workspace, motor_model, fail=[1])
        assert result["system_status"] == "Red"

    def test_repair_restores_green(self, workspace, redundant_model):
        result = failure_effects(
            workspace, redundant_model, fail=[3], repair=[3],
        )
        assert result["system_status"] == "Green"

    def test_log_is_the_failure_effects_text(self, workspace, redundant_model):
        """The log is what the original's Write To Failure Effects Log writes."""
        result = failure_effects(workspace, redundant_model, fail=[3])
        log = result["log"]
        assert log[0] == "FAILURES"
        assert "     System Status = Yellow" in log
        assert result["effects"]["failed"]
        assert result["phase"] is None

    def test_a_cold_standby_model_runs_in_fev(self, workspace):
        mid = workspace.open_model(GOLDEN / "HierarchyExample" / "input.rbd")["model_id"]
        result = failure_effects(workspace, mid, fail=["HighGain"])
        assert result["effects"]["failed"] == ["HighGain"]

    def test_a_phased_model_can_start_in_another_phase(self, workspace):
        mid = workspace.open_model(GOLDEN / "FevPhasing3" / "input.rbd")["model_id"]
        first = failure_effects(workspace, mid)
        assert first["phase"] == 1 and first["effects"]["cut"]
        second = failure_effects(workspace, mid, phase=2)
        assert second["phase"] == 2 and not second["effects"]["cut"]
        with pytest.raises(Exception):
            failure_effects(workspace, mid, phase=99)


class TestCompare:
    """compare on the same model twice reports zero delta."""

    def test_same_model_zero_delta(self, workspace, motor_model):
        result = compare(workspace, [motor_model, motor_model])
        assert "deltas" in result
        # All deltas should be ~0 (comparing same model)
        for key, val in result["deltas"].items():
            if val is not None:
                assert abs(val) < 0.01, f"Delta {key}={val} should be ~0"

    def test_labels_use_names_not_raw_ids(self, workspace):
        """A regression test for the 2026-09-22 MCP functional test review finding #10: `best`
        and `deltas` used to key on the raw model_id (e.g. `m_f31ac210`)
        instead of the model's name."""
        m1 = workspace.create_model("SeriesDemo", template="series")["model_id"]
        m2 = workspace.create_model("ParallelDemo", template="parallel")["model_id"]
        result = compare(workspace, [m1, m2], num_runs=2)
        assert result["labels"] == ["SeriesDemo", "ParallelDemo"]
        assert all(m1 not in k and m2 not in k for k in result["deltas"]), (
            f"delta keys still carry a raw model_id: {list(result['deltas'])}"
        )
        assert all(v not in (m1, m2) for v in result["best"].values()), (
            f"best values still carry a raw model_id: {result['best']}"
        )
        assert all(v in ("SeriesDemo", "ParallelDemo")
                   for v in result["best"].values())

    def test_duplicate_names_are_disambiguated(self, workspace):
        """Two distinct models that happen to share a name get their id
        appended so delta/best keys stay unambiguous."""
        m1 = workspace.create_model("Copy", template="series")["model_id"]
        m2 = workspace.create_model("Copy", template="parallel")["model_id"]
        result = compare(workspace, [m1, m2], num_runs=2)
        assert result["labels"][0] != result["labels"][1]
        assert all(lbl.startswith("Copy (") for lbl in result["labels"])

    def test_best_direction_for_capacity_and_cost(self, workspace):
        """More flow is better; lost flow and every cost are lower-is-better
        (issue #209)."""
        import json

        def cache_run(run_id, means):
            path = workspace.root / "runs" / run_id
            path.mkdir(parents=True)
            (path / "summary.json").write_text(json.dumps({
                "model_id": run_id,
                "model_name": run_id,
                "num_runs": 1,
                "metrics": {
                    name: {"name": name, "mean": value}
                    for name, value in means.items()},
            }))

        cache_run("worse", {
            "Availability": 0.8,
            "Units_Produced": 100.0,
            "Mean_Flow": 5.0,
            "Mean_Capacity": 6.0,
            "Capacity_Weighted_Availability": 0.5,
            "Lost_Flow": 10.0,
            "Grand_Total": 100.0,
            "System_Down_Cost": 40.0,
            "System_Lost_Flow_Cost": 20.0,
        })
        cache_run("better", {
            "Availability": 0.9,
            "Units_Produced": 200.0,
            "Mean_Flow": 10.0,
            "Mean_Capacity": 12.0,
            "Capacity_Weighted_Availability": 0.8,
            "Lost_Flow": 5.0,
            "Grand_Total": 50.0,
            "System_Down_Cost": 10.0,
            "System_Lost_Flow_Cost": 4.0,
        })
        result = compare(workspace, ["worse", "better"])

        assert result["best"]["Availability"] == "better"
        for name in ("Units_Produced", "Mean_Flow", "Mean_Capacity",
                     "Capacity_Weighted_Availability"):
            assert result["best"][name] == "better", name
        for name in ("Lost_Flow", "Grand_Total", "System_Down_Cost",
                     "System_Lost_Flow_Cost"):
            assert result["best"][name] == "better", name

    def test_a_sole_candidate_is_not_best(self, workspace):
        """A metric present in only one entry has no contest, so naming it
        "best" is misleading (issue #209 review)."""
        _cache_run(workspace, "first", {"Availability": 0.9})
        _cache_run(workspace, "second", {"Reliability": 0.8})
        result = compare(workspace, ["first", "second"])
        assert "Availability" not in result["best"]
        assert "Reliability" not in result["best"]

    def test_equal_candidates_are_not_best(self, workspace):
        _cache_run(workspace, "first", {"Availability": 0.9})
        _cache_run(workspace, "second", {"Availability": 0.9})
        result = compare(workspace, ["first", "second"])
        assert "Availability" not in result["best"]

    def test_grand_total_is_not_ranked_across_capacity_settings(
            self, workspace):
        """Grand Total includes lost-flow cost only when capacity analysis
        is on, so the two figures are not comparable."""
        _cache_run(
            workspace, "with_capacity",
            {"Grand_Total": 100.0, "System_Down_Cost": 10.0},
            analyses={"capacity": True, "cost": True})
        _cache_run(
            workspace, "without_capacity",
            {"Grand_Total": 50.0, "System_Down_Cost": 20.0},
            analyses={"capacity": False, "cost": True})
        result = compare(workspace, ["with_capacity", "without_capacity"])
        assert "Grand_Total" not in result["best"]
        assert result["best"]["System_Down_Cost"] == "with_capacity"
        assert any("Grand_Total" in warning and "capacity" in warning
                   for warning in result["warnings"])

    def test_an_old_cached_summary_says_rerun_for_cost_data(
            self, workspace):
        old = _cache_run(workspace, "old", {"Availability": 0.9})
        (old / "cost.txt").write_text("Cost_Summary\nGrand_Total 10.00\n")
        _cache_run(workspace, "new", {"Availability": 0.9},
                   analyses={"capacity": False, "cost": False},
                   summary_version=2)
        result = compare(workspace, ["old", "new"])
        assert any("cost" in warning.lower()
                   and "re-run" in warning.lower()
                   for warning in result["warnings"])


class TestSeries:
    """Series parsing and decimation."""

    def test_parse_ao_text(self):
        text = "0.0  1.0\n10.0  0.95\n20.0  0.98\n"
        result = parse_ao_text(text)
        assert result["t"] == [0.0, 10.0, 20.0]
        assert result["values"] == [1.0, 0.95, 0.98]

    def test_parse_ao_text_empty(self):
        result = parse_ao_text("")
        assert result["t"] == []
        assert result["values"] == []

    def test_parse_ao_text_with_comments(self):
        text = "# header\n0.0  1.0\n10.0  0.95\n"
        result = parse_ao_text(text)
        assert len(result["t"]) == 2

    def test_decimate_no_change(self):
        data = {"t": [0.0, 1.0], "values": [1.0, 0.9]}
        result = decimate(data, 10)
        assert result["t"] == [0.0, 1.0]

    def test_decimate_reduces(self):
        # 100 points → 10 points
        t = [float(i) for i in range(100)]
        v = [float(i * 0.01) for i in range(100)]
        result = decimate({"t": t, "values": v}, 10)
        assert len(result["t"]) == 10
        assert len(result["values"]) == 10
        # First and last points preserved
        assert result["t"][0] == 0.0
        assert result["t"][-1] == 99.0

    def test_parse_and_decimate(self):
        text = "\n".join(f"{i*0.1}  {1.0 - i*0.001}" for i in range(200))
        result = parse_and_decimate(text, n=50)
        assert len(result["t"]) == 50


def test_criticality_follows_the_developer_option(tmp_path, monkeypatch):
    """Reachability: `show_criticality` (env/--dev-features) adds the SPF column and the FMECA line."""
    from pathlib import Path
    from raptor.api.analyse import spf_listing
    from raptor.api.workspace import Workspace
    ws = Workspace(tmp_path)
    mid = ws.open_model(Path(__file__).resolve().parents[1] / "golden" / "LadL4Block" / "input.rbd")["model_id"]
    monkeypatch.delenv("RAPTOR_DEV_FEATURES", raising=False)
    off = spf_listing(ws, mid)
    monkeypatch.setenv("RAPTOR_DEV_FEATURES", "show_criticality")
    on = spf_listing(ws, mid)
    assert off != on and "Criticality" in on and "Criticality" not in off
    assert spf_listing(ws, mid, criticality=False) == off


class TestSPFParallelAndCache:
    def test_parallel_matches_sequential(self, workspace, cascade_model,
                                         monkeypatch):
        import raptor.core.engine.spf_fev as spf_module
        monkeypatch.setattr(spf_module, "_PARALLEL_MIN_CANDIDATES", 0)
        seq = single_point_failures(workspace, cascade_model, workers=1,
                                    use_cache=False)
        par = single_point_failures(workspace, cascade_model, workers=2,
                                    use_cache=False)
        assert par == seq

    def test_cache_returns_without_recomputing(self, workspace,
                                               cascade_model, monkeypatch):
        single_point_failures(workspace, cascade_model)
        import raptor.core.engine.spf_fev as spf_module

        def _boom(*args, **kwargs):
            raise AssertionError("SPF candidates must not be re-tested")

        monkeypatch.setattr(spf_module, "single_point_failures", _boom)
        assert isinstance(single_point_failures(workspace, cascade_model),
                          list)
