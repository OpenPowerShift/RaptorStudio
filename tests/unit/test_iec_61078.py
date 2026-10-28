"""Regression tests against worked examples in IEC 61078:2016.

The standard's numerical examples are analytical; RAPTOR Studio evaluates
the same structure by Monte Carlo simulation.  These tests therefore use
a fixed seed, a large enough sample, and a tolerance that covers sampling
error.  They are cross-checks of the RBD semantics, not a claim of
certified IEC 61078 compliance.

Covered:
* Figure 2 / F.2.1 - three non-repaired blocks in series.
* Figure 3 / F.3.1 - three non-repaired blocks in parallel.
* F.2.2 - three repaired blocks in series (steady-state availability).
* Figure 8 / 9.4 - 2-out-of-3 identical repaired blocks.
* 11.8.2 / Figure 39 - 2-out-of-5 non-identical repaired blocks.
"""
from __future__ import annotations

import math
from pathlib import Path

import pytest

from raptor.api.build import (
    build_from_spec, k_of_n, parallel, series,
)
from raptor.api.run import run
from raptor.api.workspace import Workspace

#: Monte Carlo cross-checks: ~4 minutes of the engine tier. Exact enough to
#: catch a semantics regression, too expensive for every edit -- the CI
#: `long-checks` job runs them on every push.
pytestmark = pytest.mark.long


def _workspace(tmp_path: Path) -> Workspace:
    return Workspace(tmp_path)


def _build(workspace: Workspace, name: str, spec: dict, *,
           rates: list[float], repairs: list[float] | None) -> str:
    """Build a model from ``spec`` with explicit exponential rates.

    ``rates`` and ``repairs`` are in events per hour; the file stores
    means, which is the reciprocal.  ``repairs=None`` selects the
    non-repaired case (Raptor's ``None`` repair distribution).
    """
    for i, block in enumerate(spec["blocks"]):
        block["fdist"] = 4                       # Exponential
        block["fparams"] = (1.0 / rates[i], 0.0, 0.0)
        if repairs is None:
            block["rdist"] = 18                  # None (no repair)
            block["rparams"] = (0.0, 0.0, 0.0)
        else:
            block["rdist"] = 4                   # Exponential repair
            block["rparams"] = (1.0 / repairs[i], 0.0, 0.0)
    model_id = workspace.create_model(name)["model_id"]
    handle = workspace.get(model_id)
    handle.rbd = build_from_spec(spec)
    workspace.save(model_id)
    return model_id


def _mean_metric(workspace: Workspace, model_id: str, metric: str, *,
                 runs: int, horizon: float, seed: int) -> float:
    result = run(workspace, model_id, num_runs=runs,
                 run_length=horizon, confirm_long_run=True, seed=seed)
    value = next((m.mean for m in result.metrics if m.name == metric), None)
    assert value is not None, f"{metric} missing from result metrics"
    return value


def test_figure_2_series_reliability(tmp_path):
    """F.2.1: R_s(T) = exp(-(λ1 + λ2 + λ3) T)."""
    rates = [1.0e-3, 5.0e-4, 2.0e-3]
    workspace = _workspace(tmp_path)
    model_id = _build(workspace, "iec_series", series(3),
                      rates=rates, repairs=None)
    expected = math.exp(-sum(rates) * 1000.0)
    observed = _mean_metric(workspace, model_id, "Reliability",
                            runs=1000, horizon=1000.0, seed=61078)
    assert observed == pytest.approx(expected, abs=0.01)


def test_figure_3_parallel_reliability(tmp_path):
    """F.3.1: R_s(T) = 1 - Π(1 - exp(-λi T))."""
    rates = [1.0e-2, 5.0e-3, 2.0e-2]
    workspace = _workspace(tmp_path)
    model_id = _build(workspace, "iec_parallel", parallel(3),
                      rates=rates, repairs=None)
    expected = 1.0 - math.prod(1.0 - math.exp(-r * 100.0) for r in rates)
    observed = _mean_metric(workspace, model_id, "Reliability",
                            runs=1000, horizon=100.0, seed=61079)
    # Sampling error at R ≈ 0.785 with 1 000 runs is about 0.013.
    assert observed == pytest.approx(expected, abs=0.02)


def test_f2_2_repaired_series_steady_state_availability(tmp_path):
    """F.2.2: A_s = Π μi / (λi + μi)."""
    rates = [1.0e-3, 5.0e-4, 2.0e-3]
    repairs = [0.1, 0.1, 0.1]
    workspace = _workspace(tmp_path)
    model_id = _build(workspace, "iec_series_repaired", series(3),
                      rates=rates, repairs=repairs)
    expected = math.prod(mu / (lam + mu)
                         for lam, mu in zip(rates, repairs))
    observed = _mean_metric(workspace, model_id, "Availability",
                            runs=500, horizon=5000.0, seed=61080)
    assert observed == pytest.approx(expected, abs=0.005)


def test_figure_8_2_of_3_identical_availability(tmp_path):
    """Clause 9.4, Figure 8: A_s = 3A² - 2A³ for identical blocks."""
    rate, repair = 1.0e-3, 1.0e-2
    a = repair / (rate + repair)
    expected = 3.0 * a**2 - 2.0 * a**3
    workspace = _workspace(tmp_path)
    model_id = _build(workspace, "iec_2_of_3", k_of_n(2, 3),
                      rates=[rate] * 3, repairs=[repair] * 3)
    observed = _mean_metric(workspace, model_id, "Availability",
                            runs=500, horizon=5000.0, seed=61081)
    assert observed == pytest.approx(expected, abs=0.005)


def test_11_8_2_2_of_5_non_identical_availability(tmp_path):
    """11.8.2: at least two of five non-identical blocks are up.

    A_s = 1 - [Π(1-Ai) + Σ Ai Π(j≠i)(1-Aj)].
    """
    available = [0.9, 0.7, 0.5, 0.3, 0.1]
    rate = 0.01
    repairs = [rate * a / (1.0 - a) for a in available]
    unavailable = math.prod(1.0 - a for a in available) + sum(
        a * math.prod(1.0 - other for j, other in enumerate(available)
                      if j != i)
        for i, a in enumerate(available))
    expected = 1.0 - unavailable
    workspace = _workspace(tmp_path)
    model_id = _build(workspace, "iec_2_of_5", k_of_n(2, 5),
                      rates=[rate] * 5, repairs=repairs)
    observed = _mean_metric(workspace, model_id, "Availability",
                            runs=300, horizon=2000.0, seed=61082)
    assert observed == pytest.approx(expected, abs=0.01)


@pytest.mark.parametrize("k,n", [(2, 3), (3, 4)])
def test_standard_voting_templates_build_and_validate(tmp_path, k, n):
    """Figures 8 and 9 are expressible as k-of-n voting nodes."""
    from raptor.api.validate import issues
    from raptor.core.fileio.builder import validate

    workspace = _workspace(tmp_path)
    model_id = _build(workspace, f"iec_{k}_of_{n}", k_of_n(k, n),
                      rates=[1.0e-3] * n, repairs=[1.0e-2] * n)
    rbd = workspace.get(model_id).rbd
    assert not [e for e in validate(rbd) if e.startswith("ERROR")]
    assert not [e for e in issues(rbd) if e.severity == "error"]
