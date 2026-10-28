"""Distribution entry, pure layer: the rules every entry route shares.

`params_after_change` must always leave a valid set (or None for the two
distributions that take no typed parameters), keep the mean where the new
distribution has one, and `param_error` must never accept what the whole-set
check would refuse for that field on its own.
"""
from __future__ import annotations

import itertools
import math

import pytest

from raptor.core.distro_rules import (
    DISTRO_PARAM_LABELS, validate_distro_params, param_error,
    params_after_change, params_for_mean)
from raptor.core.engine.dependency import distro_mean
from raptor.core.rng import Distribution

CODES = sorted(int(d) for d in Distribution)
PARAM_CODES = [c for c in CODES if c not in (16, 18)]
MEANS = [0.25, 1.0, 100.0, 8760.0]
#: distributions whose parameters are integers, so the mean can round
ROUNDED = {2, 3, 9}
#: Beta's mean lives in (0, 1) and Point estimate has no mean, so a
#: change to them cannot keep an arbitrary mean
NO_FREE_MEAN = {1, 17}


def test_there_are_22_codes_and_labels_for_each():
    assert len(CODES) == 22
    assert set(CODES) == set(DISTRO_PARAM_LABELS)


@pytest.mark.parametrize("new", CODES)
def test_params_for_mean_is_valid_for_every_distribution(new):
    for m in MEANS:
        vals = params_for_mean(new, m)
        if new in (16, 18):
            assert vals is None
            continue
        assert vals is not None and len(vals) == 3
        assert validate_distro_params(new, vals, "x") is None, (new, m, vals)


@pytest.mark.parametrize("old", PARAM_CODES)
def test_change_leaves_a_valid_set_with_the_same_mean(old):
    for m, new in itertools.product(MEANS, CODES):
        if new == old:
            continue
        old_vals = params_for_mean(old, m)
        out = params_after_change(old, list(old_vals), new, "x")
        if new in (16, 18):
            # no typed parameters: None, or the old values passed through
            assert out is None or validate_distro_params(new, out, "x") is None
            continue
        assert out is not None, (old, new, m)
        assert validate_distro_params(new, out, "x") is None, (old, new, m, out)
        if new in NO_FREE_MEAN:
            continue
        try:
            old_mean = float(distro_mean(old, old_vals))
            new_mean = float(distro_mean(new, out))
        except Exception:
            continue
        if not (math.isfinite(old_mean) and math.isfinite(new_mean)):
            continue
        # undefined-mean sentinel for Weibull/Pearson shapes
        if 12345.6789 in (old_mean, new_mean):
            continue
        if old_mean <= 0:
            continue
        if new in ROUNDED or old in ROUNDED:
            # integer parameters: rounding slack
            ok = abs(new_mean - old_mean) <= max(1.0, 0.5 * old_mean + 1.0)
        else:
            ok = math.isclose(new_mean, old_mean, rel_tol=1e-6)
        assert ok, (old, new, m, old_vals, out, old_mean, new_mean)


@pytest.mark.parametrize("old", CODES)
def test_change_from_empirical_or_none_still_valid(old):
    for new in PARAM_CODES:
        out = params_after_change(old, [], new, "x")
        assert out is not None
        assert validate_distro_params(new, out, "x") is None, (old, new, out)


def test_the_bug_case_fixed_025_to_binomial():
    out = params_after_change(19, [0.25, 0, 0], 3, "Phase")
    assert out[0] == float(round(out[0])) and out[0] >= 1
    assert math.isclose(out[0] * out[1], 0.25)
    assert validate_distro_params(3, out, "Phase") is None


_PROBES = [float("nan"), float("inf"), -float("inf"), -1.0, 0.0, 1e-7, 0.000001,
           0.25, 0.5, 1.0, 1.5, 2.0, 3.0, 100.0, 1e9, 1e10]


@pytest.mark.parametrize("code", CODES)
def test_param_error_never_accepts_what_the_whole_set_check_refuses(code):
    base = params_for_mean(code, 10.0) or [0.0, 0.0, 0.0]
    for idx in range(3):
        for v in _PROBES:
            vals = list(base)
            vals[idx] = v
            alone = param_error(code, idx, v, "x")
            whole = validate_distro_params(code, vals, "x")
            if alone is not None:
                assert whole is not None, (code, idx, v)
            if whole is not None and alone is None:
                # only the rules that tie fields together (or a sign rule on
                # one slot) may be refused by the whole set alone
                assert code in (8, 9, 10, 14, 19), (code, idx, v, whole)


@pytest.mark.parametrize("code", CODES)
def test_non_finite_never_valid(code):
    labels = DISTRO_PARAM_LABELS[code]
    base = params_for_mean(code, 10.0) or [0.0] * 3
    for idx, lab in enumerate(labels):
        if not lab:
            continue
        for bad in (float("nan"), float("inf")):
            vals = list(base)
            vals[idx] = bad
            assert validate_distro_params(code, vals, "x") is not None
            assert param_error(code, idx, bad, "x") is not None


def test_binomial_rules():
    assert param_error(3, 0, 0.25, "x") is not None      # non-integer N
    assert param_error(3, 0, 5.0, "x") is None
    assert param_error(3, 1, 1.5, "x") is not None       # probability > 1
    assert param_error(3, 1, 0.4, "x") is None
    assert param_error(3, 1, -0.1, "x") is not None


@pytest.mark.parametrize("new", [20, 21])
def test_params_for_mean_seed_has_that_mean(new):
    """Exponential power's mean is its Location, Extreme value's is
    Location + 0.57721 * Scale; their seeds had means of 0 and 1.0577 m."""
    for m in MEANS:
        vals = params_for_mean(new, m)
        assert math.isclose(float(distro_mean(new, vals)), m, rel_tol=1e-6), (m, vals)


@pytest.mark.parametrize("new", sorted(set(PARAM_CODES) - NO_FREE_MEAN - {2, 3, 9}))
def test_params_for_mean_seed_has_that_mean_for_the_rest(new):
    for m in MEANS:
        vals = params_for_mean(new, m)
        mean = float(distro_mean(new, vals))
        if mean == 12345.6789:
            continue
        assert math.isclose(mean, m, rel_tol=1e-6), (new, m, vals, mean)
