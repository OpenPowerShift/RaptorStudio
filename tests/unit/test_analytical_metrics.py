"""Derived analytical metrics: density, hazard, conditional reliability."""
from __future__ import annotations

import math
from pathlib import Path

import pytest

from raptor.core.analytical import build, evaluate
from raptor.core.engine.load import load_model
from raptor.core.fileio.model_file import load_model_file

MODELS = Path(__file__).resolve().parents[2] / "new_docs" / "models"


def _built(name: str):
    return build(load_model(load_model_file(MODELS / f"{name}.rbds")),
                 mode="reliability")


def test_pdf_matches_finite_difference():
    built = _built("Lantern1")
    t = 20.0
    h = 1e-4
    numerical = -(evaluate.system_probability(built, t + h)
                  - evaluate.system_probability(built, t - h)) / (2.0 * h)
    assert evaluate.pdf(built, t) == pytest.approx(numerical, rel=1e-5)


def test_hazard_is_pdf_over_reliability():
    built = _built("Lantern1")
    t = 20.0
    assert evaluate.hazard(built, t) == pytest.approx(
        evaluate.pdf(built, t) / evaluate.system_probability(built, t))


def test_conditional_reliability_is_a_ratio():
    built = _built("Lantern1")
    age, tau = 10.0, 5.0
    expected = (evaluate.system_probability(built, age + tau)
                / evaluate.system_probability(built, age))
    assert evaluate.conditional_reliability(built, age, tau) == pytest.approx(
        expected)


def test_conditional_is_none_after_system_death():
    built = _built("Lantern1")
    assert evaluate.conditional_reliability(built, 1000.0, 10.0) is None


def test_mttf_matches_single_block_mean():
    built = _built("Toaster_Weibull")
    # Weibull(shape=1.5, scale=1100): mean = 1100 * Gamma(1 + 1/1.5).
    expected = 1100.0 * math.gamma(1.0 + 1.0 / 1.5)
    assert evaluate.mttf(built) == pytest.approx(expected, rel=1e-6)
    assert evaluate.bx_life(built, 10.0) == pytest.approx(245.383078, rel=1e-6)


def test_series_importance_is_birnbaum_product_of_the_rest():
    built = _built("Lantern1")
    t = 48.0
    rows = {row.name: row for row in evaluate.importance(built, t)}
    total = evaluate.system_probability(built, t)
    for name, row in rows.items():
        assert row.birnbaum == pytest.approx(total / row.probability, rel=1e-9), name
    criticality = sum(row.criticality for row in rows.values())
    assert 0.0 < criticality <= 1.0 + 1e-12


def test_b100_is_the_finite_support_upper_bound():
    fixed = _built("Toaster_Fixed")
    assert evaluate.bx_life(fixed, 100.0) == pytest.approx(1000.0)
    weibull = _built("Toaster_Weibull")
    assert evaluate.bx_life(weibull, 100.0) is None


def test_huge_finite_time_is_zero_not_a_crash():
    built = _built("Lantern1")
    assert evaluate.system_probability(built, 1e308) == 0.0
    assert evaluate.pdf(built, 1e308) == 0.0
