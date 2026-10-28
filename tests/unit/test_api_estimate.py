"""Tests for wall-clock time estimation.

Covers the cumulative pilot, accuracy against a measured batch, caching,
the single-replication bail-out for slow models, the non-mutating
``run_length`` override, and the advice text. Uses the checked-in
`tests/golden/` models MotorDemo and Cascade.
"""
from __future__ import annotations

import time
from pathlib import Path

import pytest

import raptor.api.estimate as estimate_module
from raptor.api.estimate import Estimate, estimate
from raptor.api.workspace import Workspace
from raptor.core.engine.runner import run_simulation_with_reports

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
def motordemo_model(workspace):
    result = workspace.open_model(GOLDEN / "MotorDemo" / "input.rbd")
    return result["model_id"]


def _actual_batch(workspace, model_id, num_runs):
    """Time a real batch the same way the pilot is measured.

    ``collect_logs=False`` (no event log), a ``report_sink`` so the
    per-run reports are built exactly as the pilot builds them.
    """
    rbd = workspace.get(model_id).rbd
    t0 = time.monotonic()
    run_simulation_with_reports(
        rbd, num_runs=num_runs, collect_logs=False, report_sink=[],
    )
    return time.monotonic() - t0


class TestEstimateBasic:
    """estimate produces a well-formed Estimate."""

    def test_estimate_returns_estimate(self, workspace, cascade_model):
        est = estimate(workspace, cascade_model, num_runs=10)
        assert isinstance(est, Estimate)
        assert est.num_runs == 10
        assert len(est.pilot_times) == 5
        assert est.setup >= 0
        assert est.per_run >= 0
        assert est.wall_seconds_est > 0

    def test_pilot_is_cumulative(self, workspace, cascade_model):
        """The pilot is one batch timed after each replication.

        Each recorded time covers the whole batch so far, so the series is
        strictly increasing and the fitted per-run slope is positive.
        """
        est = estimate(workspace, cascade_model, num_runs=10)
        assert len(est.pilot_times) == 5
        assert est.pilot_times[0] < est.pilot_times[1] < est.pilot_times[2]
        # The per-run cost is a real, positive slope, not run-to-run noise.
        assert est.per_run > 0

    def test_estimate_range(self, workspace, cascade_model):
        est = estimate(workspace, cascade_model, num_runs=10)
        assert est.low < est.wall_seconds_est < est.high
        # Range should be approximately ±30%
        assert abs(est.low - est.wall_seconds_est * 0.7) < 0.1
        assert abs(est.high - est.wall_seconds_est * 1.3) < 0.1

    def test_estimate_has_advice(self, workspace, cascade_model):
        est = estimate(workspace, cascade_model, num_runs=10)
        assert isinstance(est.advice, str)
        assert len(est.advice) > 0

    def test_advice_states_caveats(self, workspace, cascade_model):
        """The advice states what the estimate cannot cover."""
        est = estimate(workspace, cascade_model, num_runs=10)
        text = est.advice.lower()
        assert "collect_events" in text
        assert "workers" in text
        assert "phases" in text
        assert "estimate" in text

    def test_estimate_to_dict(self, workspace, cascade_model):
        est = estimate(workspace, cascade_model, num_runs=10)
        d = est.to_dict()
        assert "num_runs" in d
        assert "wall_seconds_est" in d
        assert "advice" in d
        assert isinstance(d["pilot_times"], list)


class TestEstimateAccuracy:
    """Estimated time is within ±30% of an actual measured batch."""

    @pytest.mark.slow
    def test_motordemo_within_30_percent(self, workspace, motordemo_model):
        est = estimate(workspace, motordemo_model, num_runs=30)
        actual = _actual_batch(workspace, motordemo_model, 30)
        assert abs(actual - est.wall_seconds_est) < est.wall_seconds_est * 0.3 + 0.02, (
            f"Estimate {est.wall_seconds_est:.4f}s vs actual {actual:.4f}s "
            f"(difference > 30%)"
        )

    @pytest.mark.slow
    def test_cascade_within_30_percent(self, workspace, cascade_model):
        est = estimate(workspace, cascade_model, num_runs=30)
        actual = _actual_batch(workspace, cascade_model, 30)
        assert abs(actual - est.wall_seconds_est) < est.wall_seconds_est * 0.3, (
            f"Estimate {est.wall_seconds_est:.2f}s vs actual {actual:.2f}s "
            f"(difference > 30%)"
        )


class TestEstimateCache:
    """Cache hit returns same result without re-running the pilot."""

    def test_cache_returns_same_estimate(self, workspace, cascade_model):
        est1 = estimate(workspace, cascade_model, num_runs=10)
        est2 = estimate(workspace, cascade_model, num_runs=10)
        # Should be the same fit from cache
        assert est1.wall_seconds_est == est2.wall_seconds_est
        assert est1.pilot_times == est2.pilot_times

    def test_cache_different_num_runs_reuses_fit(self, workspace,
                                                 cascade_model):
        est1 = estimate(workspace, cascade_model, num_runs=10)
        est2 = estimate(workspace, cascade_model, num_runs=20)
        # Different num_runs should produce different estimates...
        assert est1.num_runs != est2.num_runs
        assert est1.wall_seconds_est != est2.wall_seconds_est
        # ...but the cached fit is reused, so the pilot is identical.
        assert est1.pilot_times == est2.pilot_times
        assert est1.setup == est2.setup
        assert est1.per_run == est2.per_run

    def test_cache_hit_does_not_run_pilot(self, workspace, cascade_model,
                                          monkeypatch):
        estimate(workspace, cascade_model, num_runs=10)

        def _boom(*args, **kwargs):
            raise AssertionError("pilot must not re-run on a cache hit")

        monkeypatch.setattr(
            estimate_module, "run_simulation_with_reports", _boom,
        )
        est = estimate(workspace, cascade_model, num_runs=10)
        assert est.wall_seconds_est > 0


class TestEstimateRunLength:
    """run_length overrides a copy; the live model is never mutated."""

    def test_run_length_does_not_mutate_live_model(self, workspace,
                                                   cascade_model):
        rbd = workspace.get(cascade_model).rbd
        before = rbd.sections["SYSTEM_DATA"].rows[0]["TermCondition"]

        estimate(workspace, cascade_model, num_runs=10, run_length=1234.0)

        after = rbd.sections["SYSTEM_DATA"].rows[0]["TermCondition"]
        assert after == before, "estimate() mutated the live model's TermCondition"

    def test_run_length_separate_cache_keys(self, workspace, cascade_model):
        estimate(workspace, cascade_model, num_runs=10, run_length=100.0)
        estimate(workspace, cascade_model, num_runs=10, run_length=200.0)
        cache_dir = workspace.root / ".estimate_cache"
        assert len(list(cache_dir.glob("*.json"))) == 2


class TestEstimateSlowModel:
    """Single-replication bail-out for very slow models (10 s threshold)."""

    def test_threshold_is_ten_seconds(self):
        assert estimate_module._SINGLE_RUN_BAIL_SECONDS == 10.0

    def test_slow_model_uses_single_replication(self, workspace,
                                                cascade_model, monkeypatch):
        # Force the bail-out: any non-zero first replication is "too slow".
        monkeypatch.setattr(estimate_module, "_SINGLE_RUN_BAIL_SECONDS", 0.0)
        est = estimate(workspace, cascade_model, num_runs=5)
        assert len(est.pilot_times) == 1
        assert est.rough is True
        assert est.setup == 0.0
        assert est.per_run > 0
        assert "rough" in est.advice.lower()

    def test_fast_model_is_not_rough(self, workspace, cascade_model):
        est = estimate(workspace, cascade_model, num_runs=5)
        assert est.rough is False
        assert len(est.pilot_times) == 5

    def test_pilot_scales_with_batch_size(self, workspace, cascade_model):
        """Large batches get a longer pilot (up to the cap)."""
        assert len(estimate(workspace, cascade_model, num_runs=2).pilot_times) == 2
        est = estimate(workspace, cascade_model, num_runs=200)
        assert len(est.pilot_times) == estimate_module._PILOT_MAX

    def test_pilot_count_never_exceeds_request(self):
        assert estimate_module._pilot_count(1) == 1
        assert estimate_module._pilot_count(5) == 5
        assert estimate_module._pilot_count(10) == 5
        assert estimate_module._pilot_count(100) == estimate_module._PILOT_MAX


class TestEstimateCacheWorkers:
    """The cached fit is one-worker; each caller's speed-up is applied."""

    def test_cache_reuses_fit_for_a_different_worker_count(
            self, workspace, cascade_model):
        one = estimate(workspace, cascade_model, num_runs=10, workers=1)
        four = estimate(workspace, cascade_model, num_runs=10, workers=4)
        assert four.cached is True
        assert four.pilot_times == one.pilot_times
        assert four.wall_seconds_est < one.wall_seconds_est

    def test_estimate_reports_its_own_cost(self, workspace, cascade_model):
        first = estimate(workspace, cascade_model, num_runs=10)
        assert first.estimate_seconds > 0
        assert first.cached is False
        second = estimate(workspace, cascade_model, num_runs=10)
        assert second.cached is True


class TestEstimateIncludesImportance:
    def test_total_includes_variant_batches(self, workspace, cascade_model):
        from raptor.api.importance import batch_count, block_rows

        rbd = workspace.get(cascade_model).rbd
        rbd.experiment = dict(rbd.experiment or {})
        rbd.experiment["importance"] = "both"
        blocks = len(block_rows(rbd))
        batches = batch_count(blocks, "both")
        est = estimate(workspace, cascade_model, num_runs=5)
        assert est.importance_mode == "both"
        assert est.importance_blocks == blocks
        assert est.importance_batches == batches - 1
        assert est.total_batches == batches
        assert est.wall_seconds_est == pytest.approx(
            est.baseline_wall_seconds + est.importance_wall_seconds)
        assert est.wall_seconds_est == pytest.approx(
            est.baseline_wall_seconds * batches)

    def test_override_can_turn_importance_on_for_the_estimate_only(
            self, workspace, cascade_model):
        from raptor.api.importance import batch_count, block_rows

        rbd = workspace.get(cascade_model).rbd
        before = dict(rbd.experiment or {})
        est = estimate(workspace, cascade_model, num_runs=3,
                       importance="risk")
        after = dict(workspace.get(cascade_model).rbd.experiment or {})
        assert est.importance_mode == "risk"
        assert est.total_batches == batch_count(len(block_rows(rbd)), "risk")
        assert after == before, "estimate() must not mutate the experiment"
