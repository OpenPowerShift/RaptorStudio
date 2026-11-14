"""Availability period by period (issue 257)."""
from __future__ import annotations

import pytest

from raptor.api import build
from raptor.api import run as run_api
from raptor.api.insight import drivers
from raptor.api.insight.attribution import attribute_trial
from raptor.api.insight.periods import (
    PeriodSpec, from_outages, percentile, period_availability, sentence, summarise, wilson,
)
from raptor.api.workspace import Workspace
from raptor.core.engine.runner import run_simulation


def test_an_outage_across_a_boundary_is_split():
    # periods of 100; down 90..130 and 250..260
    values, dropped = period_availability([(90, 130), (250, 260)], 0, 300, PeriodSpec(100))
    assert values == pytest.approx([0.90, 0.70, 0.90]) and dropped == 0


def test_partial_last_period_is_dropped_or_prorated():
    red = [(310, 330)]
    values, dropped = period_availability(red, 0, 350, PeriodSpec(100))
    assert values == pytest.approx([1.0, 1.0, 1.0]) and dropped == 1
    values, dropped = period_availability(red, 0, 350, PeriodSpec(100, partial="prorate"))
    assert values == pytest.approx([1.0, 1.0, 1.0, 1 - 20 / 50]) and dropped == 0


def test_offset_moves_the_boundaries_and_skips_what_comes_before():
    values, _ = period_availability([(0, 40), (140, 160)], 0, 250, PeriodSpec(100, offset=50))
    assert values == pytest.approx([1 - 10 / 100, 1 - 10 / 100])      # 50..150 and 150..250
    assert period_availability([], 0, 40, PeriodSpec(100, offset=50)) == ([], 0)


def test_time_weighted_mean_equals_the_runs_availability():
    red = [(12.5, 60.0), (95.0, 205.0), (290.0, 291.0)]
    values, _ = period_availability(red, 0, 300, PeriodSpec(100))
    assert sum(values) / 3 == pytest.approx(1 - sum(b - a for a, b in red) / 300)


def test_percentiles_are_nearest_rank():
    values = [0.1 * i for i in range(1, 11)]
    assert percentile(values, 50) == pytest.approx(0.5)
    assert percentile(values, 5) == pytest.approx(0.1)
    assert percentile(values, 95) == pytest.approx(1.0)
    assert percentile([0.7], 1) == 0.7 and percentile([0.2, 0.9], 50) == 0.2
    with pytest.raises(ValueError):
        percentile([], 50)


def test_wilson_interval():
    low, high = wilson(5, 100)
    assert low == pytest.approx(0.0215, abs=5e-4) and high == pytest.approx(0.1118, abs=5e-4)
    assert wilson(0, 0) == (0.0, 1.0)


def test_summary_pools_and_goes_by_index():
    per_run = [[0.99, 0.95, 0.90], [0.98, 0.97, 0.80], [1.0, 0.96]]
    s = summarise(per_run, threshold=0.93, dropped=1, spec=PeriodSpec(8760))
    assert s["runs"] == 3 and s["periods_per_run"] == 3 and s["dropped_partial_periods"] == 1
    assert s["pooled"]["n"] == 8 and s["pooled"]["below"] == 2
    assert s["pooled"]["p_below"] == pytest.approx(0.25)
    assert [row["n"] for row in s["by_index"]] == [3, 3, 2]
    assert s["by_index"][2]["mean"] == pytest.approx(0.85)
    assert s["worst"][0] == {"run": 2, "index": 3, "value": 0.80}
    text = sentence(s, "hours")
    assert "A typical year" in text and "25 % of them fall below 93 %" in text
    assert "nothing to slice" in sentence(summarise([[]], spec=PeriodSpec(10)))


def test_bad_spec_is_refused():
    with pytest.raises(ValueError):
        PeriodSpec(0)
    with pytest.raises(ValueError):
        PeriodSpec(10, offset=-1)


SPEC = {
    "blocks": [{"name": "Pump", "fail_dist": "Exponential", "fail_params": [700, 0, 0],
                "repair_dist": "Lognormal", "repair_params": [30, 10, 0]}],
    "nodes": [{"name": "Start", "type": 1}, {"name": "End", "type": 3}],
    "links": [{"from": "Start", "to": "Pump"}, {"from": "Pump", "to": "End"}],
    "experiment": {"trials": 25, "termination_type": "TimeSim", "term_condition": 8760 * 4},
}


def test_replay_intervals_reconcile_with_the_engine():
    rbd = build.build_from_spec(SPEC)
    reports = []
    logs = run_simulation(rbd, 5, _report_sink=reports)
    for lines, report in zip(logs, reports, strict=True):
        trial = attribute_trial(lines)
        red = list(zip(trial.red[0::2], trial.red[1::2], strict=True))
        values, dropped = period_availability(red, trial.start, trial.end, PeriodSpec(8760))
        assert len(values) == 4 and dropped == 0
        assert sum(values) / 4 == pytest.approx(report.availability, rel=1e-9)


def test_stored_run_gives_yearly_results_for_any_year_length(tmp_path):
    ws = Workspace(tmp_path)
    mid = ws.register_model(build.build_from_spec(SPEC), name="m")["model_id"]
    result = run_api.run(ws, mid, seed=2, attribution=True)
    yearly = drivers.periods(ws, result.run_id, below=0.95)
    assert yearly["pooled"]["n"] == 100 and yearly["periods_per_run"] == 4
    mean_run = sum(r["availability"] for r in result.per_run) / 25
    assert yearly["pooled"]["mean"] == pytest.approx(mean_run, rel=1e-9)
    assert yearly["pooled"]["min"] <= yearly["pooled"]["p5"] <= yearly["pooled"]["p50"]
    assert "A typical year" in yearly["sentence"]
    halves = drivers.periods(ws, result.run_id, length=4380)
    assert halves["pooled"]["n"] == 200
    plain = run_api.run(ws, mid, seed=2)
    assert plain.per_run == result.per_run
    with pytest.raises(drivers.NoAttribution):
        drivers.periods(ws, plain.run_id)


def test_from_outages_record():
    record = {"runs": [{"start": 0, "end": 200, "red": [90, 130]},
                       {"start": 0, "end": 200, "red": []}]}
    s = from_outages(record, PeriodSpec(100))
    assert s["pooled"]["n"] == 4 and s["pooled"]["min"] == pytest.approx(0.70)
