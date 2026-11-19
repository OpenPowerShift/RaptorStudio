"""Run rules: precision, too few failures, lopsided runs and target verdicts."""
from __future__ import annotations

import math

import pytest

from raptor.api import build
from raptor.api import run as run_api
from raptor.api.advisor import advise_run, advise_stored_run
from raptor.api.advisor.rules_run import quality
from raptor.api.advisor.stats import normal_quantile, t_cdf, t_quantile
from raptor.api.workspace import Workspace

from .helpers import block, series_spec

RBD = build.build_from_spec(series_spec(block("A")))


def rows(ao, fails=None):
    fails = fails if fails is not None else [1] * len(ao)
    return [{"availability": a, "num_system_failures": f, "reliability": float(f == 0)}
            for a, f in zip(ao, fails, strict=True)]


def spread(mean, sd, n):
    """n values with exactly this mean and sample standard deviation."""
    base = [(-1.0) ** i for i in range(n)]
    if n % 2:
        base[-1] = 0.0
    scale = sd / math.sqrt(sum(b * b for b in base) / (n - 1))
    return [mean + b * scale for b in base]


def run_findings(per_run, rule=None, requirements=None):
    out = advise_run(RBD, {}, per_run, requirements=requirements)
    return [f for f in out if rule is None or f.rule == rule]


@pytest.mark.parametrize("df, expected", [(1, 12.7062), (2, 4.3027), (5, 2.5706),
                                           (10, 2.2281), (30, 2.0423), (120, 1.9799)])
def test_t_quantile_matches_tables(df, expected):
    assert t_quantile(0.975, df) == pytest.approx(expected, abs=5e-5)
    assert t_cdf(t_quantile(0.975, df), df) == pytest.approx(0.975, abs=1e-9)
    assert t_quantile(0.025, df) == pytest.approx(-expected, abs=5e-5)


def test_normal_quantile():
    assert normal_quantile(0.975) == pytest.approx(1.959964, abs=1e-6)
    assert normal_quantile(0.5) == pytest.approx(0.0, abs=1e-9)
    assert normal_quantile(0.001) == pytest.approx(-3.090232, abs=1e-6)


def test_R_QUA_001_fires_with_the_runs_needed():
    ao = spread(0.99, 0.008, 100)                 # sem 0.0008, half-width ~0.0016
    f, = run_findings(rows(ao), "R-QUA-001")
    half = t_quantile(0.975, 99) * 0.0008
    assert f.evidence["half_width"] == pytest.approx(half, rel=1e-6)
    assert f.evidence["relative_half_width"] == pytest.approx(half / 0.01, rel=1e-6)
    assert f.evidence["runs_needed"] == math.ceil(100 * (half / 0.01 / 0.10) ** 2)
    assert f.fixes[0].assignments == (f"system.runs={f.evidence['runs_needed']}",)
    assert f.basis == "statistical"


def test_R_QUA_001_silent_when_precise_or_perfect():
    assert not run_findings(rows(spread(0.99, 0.0004, 100)), "R-QUA-001")
    assert not run_findings(rows([1.0] * 50, [0] * 50), "R-QUA-001")
    assert not run_findings(rows([0.9, 0.95]), "R-QUA-001")


def test_R_QUA_002_few_failures():
    f, = run_findings(rows([0.999] * 40, [1] * 4 + [0] * 36), "R-QUA-002")
    assert f.evidence["system_failures"] == 4 and f.severity == "warning"
    assert not run_findings(rows([0.999] * 40, [1] * 10 + [0] * 30), "R-QUA-002")


def test_R_QUA_003_no_failures_gives_the_exact_bound():
    f, = run_findings(rows([1.0] * 300, [0] * 300), "R-QUA-003")
    assert f.evidence["upper_bound"] == pytest.approx(1 - 0.05 ** (1 / 300))
    assert f.evidence["rule_of_three"] == pytest.approx(0.01)
    assert not run_findings(rows([1.0] * 300, [0] * 300), "R-QUA-002")


def test_R_QUA_005_lopsided_runs():
    ao = [0.9999] * 95 + [0.90] * 5              # 5 runs hold almost all the downtime
    f, = run_findings(rows(ao), "R-QUA-005")
    assert f.evidence["worst_runs"] == 5 and f.evidence["share"] > 0.9
    assert not run_findings(rows(spread(0.99, 0.002, 100)), "R-QUA-005")


REQ = {"metric": "system.ao", "op": ">=", "value": 0.995, "label": "Contract availability"}


def test_R_REQ_001_met_missed_and_too_close():
    met, = run_findings(rows(spread(0.997, 0.003, 100)), "R-REQ-001", [REQ])
    assert met.title == "Target met" and met.severity == "info"
    assert met.evidence["runs_meeting"] == 50      # half the runs sit below the mean
    missed, = run_findings(rows(spread(0.993, 0.002, 100)), "R-REQ-001", [REQ])
    assert missed.title == "Target missed" and missed.severity == "warning"
    assert missed.evidence["downtime_gap"] == pytest.approx(0.002 * 8760)
    assert "Contract availability" in missed.message
    close, = run_findings(rows(spread(0.9951, 0.002, 100)), "R-REQ-001", [REQ])
    assert close.title == "Too close to call" and close.fixes
    assert 0.05 < close.evidence["p_meet"] < 0.95


def test_R_REQ_001_upper_limits_and_no_requirement():
    limit = {"metric": "system.mdt", "op": "<=", "value": 10.0}
    per_run = [{"availability": 0.99, "num_system_failures": 1, "mdt": v}
               for v in spread(8.0, 1.0, 50)]
    f, = run_findings(per_run, "R-REQ-001", [limit])
    assert f.title == "Target met"
    assert not run_findings(per_run, "R-REQ-001")
    # an unknown comparison is refused, not judged as ">=" or dropped
    with pytest.raises(ValueError, match="unknown comparison"):
        run_findings(per_run, "R-REQ-001", [{"metric": "system.ao", "op": "!=", "value": 1}])


def test_p_meet_is_the_t_probability():
    ao = spread(0.9951, 0.002, 30)
    f, = run_findings(rows(ao), "R-REQ-001", [REQ])
    sem = 0.002 / math.sqrt(30)
    assert f.evidence["p_meet"] == pytest.approx(t_cdf((0.9951 - 0.995) / sem, 29), rel=1e-6)


def test_model_rules_do_not_run_in_the_run_phase():
    assert all(f.rule.startswith("R-") for f in run_findings(rows(spread(0.99, 0.004, 100))))


def test_a_real_stored_run(tmp_path):
    spec = series_spec(block("A", fail_params=[500, 0, 0], repair_params=[20, 5, 0]),
                       experiment={"trials": 40, "termination_type": "TimeSim",
                                   "term_condition": 2000})
    ws = Workspace(tmp_path)
    mid = ws.register_model(build.build_from_spec(spec), name="m")["model_id"]
    result = run_api.run(ws, mid, seed=3)
    out = advise_stored_run(ws, result.run_id, requirements=[
        {"metric": "system.ao", "op": ">=", "value": 0.5}])
    by_rule = {f.rule: f for f in out}
    assert by_rule["R-REQ-001"].title == "Target met"
    assert by_rule["R-REQ-001"].evidence["runs"] == 40
    assert quality.PRECISION == 0.10
