"""Fitting a life law from judged quantiles (WP-4.4)."""
from __future__ import annotations

import math
from statistics import NormalDist

import pytest

from raptor.api.distributions import from_quantiles


def test_weibull_closed_form_round_trips():
    shape, scale = 2.0, 100.0
    q = {0.1: scale * (-math.log(0.9)) ** (1 / shape),
         0.5: scale * math.log(2) ** (1 / shape)}
    fit = from_quantiles("weibull", q)
    assert fit["params"]["shape"] == pytest.approx(shape, rel=1e-9)
    assert fit["params"]["scale"] == pytest.approx(scale, rel=1e-9)
    assert fit["mean"] == pytest.approx(scale * math.gamma(1.5), rel=1e-9)
    assert "wears out" in fit["shape_note"]


def test_lognormal_closed_form_round_trips():
    mu, sigma = math.log(100.0), 0.3
    q = {0.1: math.exp(mu + sigma * NormalDist().inv_cdf(0.1)),
         0.5: math.exp(mu)}
    fit = from_quantiles("lognormal", q)
    assert fit["params"]["mu"] == pytest.approx(mu, rel=1e-9)
    assert fit["params"]["sigma"] == pytest.approx(sigma, rel=1e-9)


def test_exponential_uses_the_median_and_says_pm_cannot_help():
    q = {0.1: -100 * math.log(0.9), 0.5: -100 * math.log(0.5)}
    fit = from_quantiles("exponential", q)
    assert fit["params"]["mean"] == pytest.approx(100.0, rel=1e-9)
    assert "cannot" in fit["shape_note"]


def test_three_quantiles_fit_least_squares():
    shape, scale = 1.7, 250.0
    q = {p: scale * (-math.log(1 - p)) ** (1 / shape)
         for p in (0.1, 0.5, 0.9)}
    fit = from_quantiles("weibull", q)
    assert fit["params"]["shape"] == pytest.approx(shape, rel=1e-6)
    assert fit["params"]["scale"] == pytest.approx(scale, rel=1e-6)


def test_inconsistent_judgement_is_refused_by_name():
    with pytest.raises(ValueError, match="higher probability"):
        from_quantiles("weibull", {0.5: 100, 0.1: 300})
    with pytest.raises(ValueError, match="at least two"):
        from_quantiles("weibull", {0.5: 100})
    with pytest.raises(ValueError, match="positive"):
        from_quantiles("weibull", {0.1: -1, 0.5: 100})
    with pytest.raises(ValueError, match="law"):
        from_quantiles("beta", {0.1: 1, 0.5: 2})
