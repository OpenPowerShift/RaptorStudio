"""Distribution survival functions verified against the engine's own RNG."""
from __future__ import annotations

import math

import pytest

from raptor.core.analytical import survival
from raptor.core.analytical.errors import UnsupportedDistribution
from raptor.core.rng import Distribution, RandomGen, RaptorStream

N = 20_000

#: (name, code, params, three times to probe)
CASES = [
    ("exponential", int(Distribution.EXPONENTIAL), [100.0, 0.0, 0.0],
     [50.0, 100.0, 200.0]),
    ("weibull", int(Distribution.WEIBULL), [1.5, 100.0, 0.0],
     [50.0, 100.0, 200.0]),
    ("normal", int(Distribution.NORMAL), [100.0, 15.0, 0.0],
     [80.0, 100.0, 120.0]),
    ("lognormal", int(Distribution.LOGNORMAL), [100.0, 30.0, 0.0],
     [70.0, 100.0, 150.0]),
    ("gamma", int(Distribution.GAMMA), [2.5, 40.0, 0.0],
     [60.0, 100.0, 160.0]),
    ("erlang", int(Distribution.ERLANG), [3.0, 30.0, 0.0],
     [50.0, 90.0, 150.0]),
    ("beta", int(Distribution.BETA), [2.0, 3.0, 0.0],
     [0.25, 0.5, 0.75]),
    ("chi-square", int(Distribution.CHI_SQUARE), [4.0, 0.0, 0.0],
     [2.0, 4.0, 7.0]),
    ("uniform", int(Distribution.UNIFORM_REAL), [10.0, 20.0, 0.0],
     [12.0, 15.0, 18.0]),
    ("triangular", int(Distribution.TRIANGULAR), [10.0, 15.0, 20.0],
     [12.0, 15.0, 18.0]),
    ("pearson5", int(Distribution.PEARSON5), [3.0, 50.0, 0.0],
     [20.0, 25.0, 40.0]),
    ("pearson6", int(Distribution.PEARSON6), [3.0, 4.0, 50.0],
     [30.0, 50.0, 100.0]),
    ("extreme-value", int(Distribution.EXTREME_VALUE), [20.0, 100.0, 0.0],
     [80.0, 100.0, 130.0]),
    ("laplace", int(Distribution.LAPLACE), [20.0, 100.0, 0.0],
     [80.0, 100.0, 120.0]),
    ("empirical", int(Distribution.EMPIRICAL),
     [10.0, 20.0, 50.0, 100.0], [15.0, 30.0, 80.0]),
]


@pytest.mark.parametrize("name,code,params,times", CASES,
                         ids=[case[0] for case in CASES])
def test_survival_matches_engine_draws(name, code, params, times):
    rng = RandomGen(stream=RaptorStream(seed=1234567))
    draws = [rng.draw_number(Distribution(code), params) for _ in range(N)]
    for t in times:
        empirical = sum(1 for value in draws if value > t) / N
        expected = survival.sf(code, params, t)
        # Four binomial standard errors plus a small allowance for the
        # engine's own floating-point transforms.
        tolerance = 4.0 * math.sqrt(max(expected * (1.0 - expected), 0.0) / N) + 0.01
        assert empirical == pytest.approx(expected, abs=tolerance), (
            f"{name} at t={t}")


@pytest.mark.parametrize("name,code,params,times", CASES,
                         ids=[case[0] for case in CASES])
def test_mean_matches_engine_draws(name, code, params, times):
    rng = RandomGen(stream=RaptorStream(seed=7654321))
    draws = [rng.draw_number(Distribution(code), params) for _ in range(N)]
    empirical = sum(draws) / N
    expected = survival.mean(code, params)
    if expected is None:
        pytest.skip("undefined mean")
    # 5% is loose enough for the heavy-tailed supported laws and tight
    # enough to catch a swapped shape/scale.
    assert empirical == pytest.approx(expected, rel=0.05), name


def test_fixed_and_none():
    assert survival.sf(int(Distribution.FIXED), [10.0], 9.999) == 1.0
    assert survival.sf(int(Distribution.FIXED), [10.0], 10.0) == 0.0
    assert survival.sf(int(Distribution.NONE), [], 1e12) == 1.0
    assert survival.mean(int(Distribution.NONE), []) is None


def test_pdf_integrates_to_one():
    cases = [
        (int(Distribution.EXPONENTIAL), [100.0, 0.0, 0.0], 0.0, 2000.0),
        (int(Distribution.WEIBULL), [1.5, 100.0, 0.0], 0.0, 2000.0),
        (int(Distribution.NORMAL), [100.0, 15.0, 0.0], 0.0, 250.0),
        (int(Distribution.GAMMA), [2.5, 40.0, 0.0], 0.0, 1000.0),
        (int(Distribution.BETA), [2.0, 3.0, 0.0], 0.0, 1.0),
        (int(Distribution.TRIANGULAR), [10.0, 15.0, 20.0], 10.0, 20.0),
    ]
    for code, params, low, high in cases:
        n = 20_000
        step = (high - low) / n
        total = 0.5 * ((survival.pdf(code, params, low) or 0.0)
                       + (survival.pdf(code, params, high) or 0.0))
        for i in range(1, n):
            total += (survival.pdf(code, params, low + i * step) or 0.0)
        total *= step
        assert total == pytest.approx(1.0, abs=0.01), code


def test_unsupported_distributions_are_refused():
    for code in (int(Distribution.BINOMIAL), int(Distribution.POISSON),
                 int(Distribution.UNIFORM_INTEGER),
                 int(Distribution.EXPONENTIAL_POWER)):
        with pytest.raises(UnsupportedDistribution):
            survival.sf(code, [1.0, 2.0, 3.0], 1.0)
