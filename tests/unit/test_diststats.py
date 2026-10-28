"""`diststats.compute_stats` -- every `ComputeStats` arm
against values worked by hand from the source formulas, plus the Lanczos
`GammaFunction` the Weibull and Exponential Power arms use."""
from __future__ import annotations

import math

import pytest

from raptor.core.engine.diststats import compute_stats, gamma_function

U, NA = "Undefined", "N/A"


@pytest.mark.parametrize(("distro", "params", "mean", "stdev"), [
    (1, [2, 3, 0], 0.4, math.sqrt(6 / (25 * 6))),             # Beta
    (2, [8, 0, 0], 8.0, 4.0),                                  # Chi-square
    (3, [10, 0.2, 0], 2.0, math.sqrt(1.6)),                    # Binomial
    (4, [100, 5, 0], 105.0, 100.0),                            # Exponential (scale, location)
    (5, [3, 4, 0], 12.0, math.sqrt(48)),                       # Erlang
    (6, [2, 5, 1], 11.0, math.sqrt(50)),                       # Gamma
    (7, [10, 2, 0], 10.0, 2.0),                                # Lognormal: stored mean/sd
    (8, [288, 1, 0], 288.0, 1.0),                              # Normal
    (9, [1, 7, 0], 4.0, math.sqrt(3)),                         # Uniform Integer
    (10, [2, 8, 0], 5.0, math.sqrt(3)),                        # Uniform Real
    (11, [3, 4, 1], 3.0, 2.0),                                 # Pearson 5
    (11, [1.5, 4, 1], 9.0, U),                                 # Pearson 5, shape <= 2
    (11, [1, 4, 1], U, U),                                     # Pearson 5, shape <= 1
    (12, [2, 4, 3], 2.0, math.sqrt(9 * 2 * 5 / (9 * 2))),      # Pearson 6
    (12, [2, 1.5, 3], 12.0, U),                                # Pearson 6, shape2 <= 2
    (12, [2, 1, 3], U, U),                                     # Pearson 6, shape2 <= 1
    (13, [9, 0, 0], 9.0, 3.0),                                 # Poisson
    (14, [1, 4, 7], 4.0, math.sqrt(27 / 18)),                  # Triangular
    (15, [1, 50, 2], 52.0, 50.0),                              # Weibull, shape 1
    (15, [2e7, 50, 2], 52.0, 0.0),                             # Weibull, shape > 1e7
    (15, [0.04, 50, 2], U, U),                                 # Weibull, shape < 0.05
    (16, [1, 2, 3, 4], 2.5, math.sqrt(1.25)),                  # Empirical (population sd)
    (17, [0.7, 0, 0], NA, NA),                                 # Point Estimate
    (18, [0, 0, 0], NA, NA),                                   # None
    (19, [42, 0, 0], 42.0, 0.0),                               # Fixed
    (21, [2, 10, 0], 10 + 0.57721 * 2, math.sqrt(4 * math.pi ** 2 / 6)),  # Extreme Value
    (22, [3, 7, 0], 7.0, math.sqrt(18)),                       # Laplace
])
def test_compute_stats_matches_source_formula(distro, params, mean, stdev):
    got = compute_stats(distro, params)
    for g, want in zip(got, (mean, stdev)):
        if isinstance(want, str):
            assert g == want
        else:
            assert g == pytest.approx(want, rel=1e-12)


def test_weibull_general_shape_uses_gamma_function():
    m, s = compute_stats(15, [2, 100, 5])
    z, z2 = gamma_function(1.5), gamma_function(2.0)
    assert m == 100 * z + 5
    assert s == 100 * math.sqrt(z2 - z * z)
    assert m == pytest.approx(100 * math.sqrt(math.pi) / 2 + 5, rel=1e-13)


def test_exponential_power():
    m, s = compute_stats(20, [2, 3, 4])
    assert m == 4
    assert s == pytest.approx(math.sqrt(4 * 9 * math.gamma(3) / math.gamma(1)), rel=1e-13)
    assert compute_stats(20, [0, 3, 4]) == (4, U)   # GammaFunction(0) would divide by 0


@pytest.mark.parametrize(("distro", "params"), [
    (1, [0, 3, 0]),    # Beta with a non-positive shape: caller shows a dash
    (16, []),          # Empirical with no data
    (99, [1, 2, 3]),   # OTHERWISE
])
def test_compute_stats_none(distro, params):
    assert compute_stats(distro, params) is None


@pytest.mark.parametrize(("distro", "params"), [
    (2, [-4, 0, 0]),   # SQRT of a negative argument
    (5, [-1, 2, 0]),
    (13, [-9, 0, 0]),
])
def test_negative_sqrt_is_undefined_not_an_exception(distro, params):
    assert compute_stats(distro, params)[1] == U


def test_infinite_result_is_undefined():
    # Uniform with overflowing bounds: the mean is inf
    assert compute_stats(10, [1e308, 1e308, 0])[0] == U


@pytest.mark.parametrize("x", [1.05, 1.5, 2.0, 1 + 1 / 0.7, 3.0, 11.0, 21.0])
def test_gamma_function_is_source_lanczos(x):
    """Close to math.gamma but not identical: this is what real Raptor
    computes, so the port uses it rather than math.gamma."""
    assert gamma_function(x) == pytest.approx(math.gamma(x), rel=1e-10)


def test_gamma_function_known_digits():
    # Raptor 7's formula evaluated by hand at x=11: 10! plus the
    # approximation's own error, which math.gamma does not have
    assert gamma_function(11.0) != math.gamma(11.0)
    assert abs(gamma_function(11.0) - 3628800.0) < 1e-5


def test_display_stats_and_engine_means_agree_including_the_murphy_sentinels():
    """`ComputeStats` (the dialog's labels) and `SetStats` (the block's stored mean) are separate
    source procedures with the same formulas; the two ports must not drift apart, sentinels
    included (12345.6789 out of range -> "Undefined", 12345.6788 no mean -> "N/A")."""
    import itertools

    from raptor.core.engine.dependency import distro_mean
    from raptor.core.engine.diststats import compute_stats

    shown = {"Undefined": 12345.6789, "N/A": 12345.6788}
    grid = [0.01, 0.04, 0.5, 1.0, 1.5, 2.5, 7.0, 2e7]
    for distro in (d for d in range(1, 23) if d != 16):   # 16 (empirical) takes a table, not three numbers
        for params in itertools.product(grid, repeat=3):
            if distro == 20 and params[0] > 1e6:   # POWER overflows; real's behaviour there is unknown
                continue
            stats = compute_stats(distro, list(params))
            if stats is None:
                continue
            assert shown.get(stats[0], stats[0]) == distro_mean(distro, list(params)), (distro, params)
