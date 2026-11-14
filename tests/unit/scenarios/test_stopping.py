"""Run to a precision: the stopping rule and determinism (WP-3.3)."""
from __future__ import annotations

import threading

import pytest

from raptor.api import build
from raptor.api import run as run_api
from raptor.api.scenarios.stopping import (
    Precision, run_to_precision, t_critical,
)
from raptor.api.workspace import Workspace

SPEC = {
    "experiment": {"trials": 200, "termination_type": "TimeSim",
                   "term_condition": 2000.0},
    "blocks": [{"name": "Pump", "fail_dist": "Exponential",
                "fail_params": [400.0, 0.0], "repair_dist": "Exponential",
                "repair_params": [30.0, 0.0]}],
    "nodes": [{"name": "Start", "type": 1}, {"name": "End", "type": 3}],
    "links": [{"from": "Start", "to": "Pump"}, {"from": "Pump", "to": "End"}],
}


def _workspace(tmp_path):
    ws = Workspace(tmp_path)
    mid = ws.register_model(build.build_from_spec(SPEC), name="m")["model_id"]
    return ws, mid


def test_t_critical_matches_the_table_then_the_normal_tail():
    assert t_critical(10, 0.95) == pytest.approx(2.228)
    assert t_critical(120, 0.95) == pytest.approx(1.98, abs=0.01)
    assert t_critical(10_000, 0.95) == pytest.approx(1.960, abs=0.002)


def test_a_bad_precision_is_rejected_before_any_run():
    with pytest.raises(ValueError, match="metric"):
        Precision(metric="nope").field()
    with pytest.raises(ValueError, match="relative"):
        Precision(relative=0).field()
    with pytest.raises(ValueError, match="min_trials"):
        Precision(min_trials=1).field()


def test_the_final_batch_matches_a_fixed_run_with_the_same_seed(tmp_path):
    ws, mid = _workspace(tmp_path)
    precision = Precision(relative=0.0001, min_trials=8, max_trials=16,
                          batch=4)
    outcome = run_to_precision(ws, mid, precision, seed=7)
    fixed = run_api.run(ws, mid, num_runs=outcome.trials, seed=7)
    assert outcome.result.per_run == fixed.per_run
    assert outcome.trials <= 16


def test_a_tight_target_stops_at_the_cap_and_says_so(tmp_path):
    ws, mid = _workspace(tmp_path)
    precision = Precision(relative=1e-6, min_trials=5, max_trials=10, batch=5)
    outcome = run_to_precision(ws, mid, precision, seed=3)
    assert outcome.stopped == "max trials"
    assert outcome.trials <= 10
    assert outcome.history


def test_cancelling_between_passes_keeps_the_completed_batch(tmp_path):
    ws, mid = _workspace(tmp_path)
    abort = threading.Event()

    calls = []

    def progress(done, total, text):
        calls.append(done)
        if len(calls) > 1:
            abort.set()

    outcome = run_to_precision(
        ws, mid, Precision(min_trials=5, max_trials=50, batch=5),
        seed=2, progress=progress, abort=abort)
    assert outcome.stopped == "cancelled"
    assert outcome.trials >= 5
    assert outcome.result.run_id


def test_the_report_states_the_interval_and_the_target(tmp_path):
    ws, mid = _workspace(tmp_path)
    outcome = run_to_precision(
        ws, mid, Precision(relative=0.5, min_trials=10, max_trials=10),
        seed=1)
    data = outcome.to_dict()
    assert data["target"] == pytest.approx(0.5 / 1.5)
    assert data["mean"] is not None and data["sem"] is not None
    assert data["stopped"] in ("precision", "max trials")


def test_a_model_that_never_fails_grows_the_count_geometrically(tmp_path):
    spec = {**SPEC, "blocks": [dict(SPEC["blocks"][0],
                                    fail_params=[1e12, 0.0])]}
    ws = Workspace(tmp_path)
    mid = ws.register_model(build.build_from_spec(spec), name="m")["model_id"]
    result = run_to_precision(ws, mid, Precision(min_trials=4, max_trials=64,
                                                 batch=4))
    counts = [h["trials"] for h in result.history]
    # 4, 8, 16, 32, 64: one pass per doubling, never one trial per pass
    assert counts == [4, 8, 16, 32, 64]
    assert result.stopped == "max trials"
