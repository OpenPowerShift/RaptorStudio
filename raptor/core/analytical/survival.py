"""Analytical survival functions for the engine's distributions.

The parameter order is the engine's, not the textbook's. Every function here
is checked against `raptor.core.rng.RandomGen` draws; see
`tests/unit/test_analytical_survival.py`. Getting a shape/scale swap wrong
would produce a plausible-looking wrong answer, which is the one failure mode
this module must not have.

Only continuous life distributions are supported. Discrete distributions
(Binomial, Poisson, Uniform integer) and the engine's Exponential-power stub
are refused. `Distribution.NONE` is a perfect block: ``R(t) = 1``.
"""
from __future__ import annotations

import math
from collections.abc import Sequence
from itertools import pairwise

from raptor.core.rng import Distribution

from .errors import UnsupportedDistribution

_INF = float("inf")
_GAMMA = 0.5772156649015329
_SQRT2 = math.sqrt(2.0)
_SQRT2PI = math.sqrt(2.0 * math.pi)

#: The codes with an analytical survival function in this module.
SUPPORTED: frozenset[int] = frozenset({
    int(Distribution.BETA),
    int(Distribution.CHI_SQUARE),
    int(Distribution.ERLANG),
    int(Distribution.EXPONENTIAL),
    int(Distribution.FIXED),
    int(Distribution.GAMMA),
    int(Distribution.LAPLACE),
    int(Distribution.LOGNORMAL),
    int(Distribution.NORMAL),
    int(Distribution.PEARSON5),
    int(Distribution.PEARSON6),
    int(Distribution.TRIANGULAR),
    int(Distribution.UNIFORM_REAL),
    int(Distribution.WEIBULL),
    int(Distribution.EMPIRICAL),
    int(Distribution.EXTREME_VALUE),
    int(Distribution.NONE),
})


def _need(params: Sequence[float], count: int, code: int) -> list[float]:
    values = [float(v) for v in params]
    if len(values) < count:
        raise UnsupportedDistribution(
            f"distribution code {code} needs {count} parameters, "
            f"got {len(values)}")
    return values


def _positive(value: float, name: str) -> float:
    if not (value > 0.0) or not math.isfinite(value):
        raise UnsupportedDistribution(f"{name} must be positive, got {value!r}")
    return value


def _unit(value: float, name: str) -> float:
    if not (0.0 <= value <= 1.0) or not math.isfinite(value):
        raise UnsupportedDistribution(f"{name} must be in [0, 1], got {value!r}")
    return value


# ---------------------------------------------------------------------------
# Incomplete gamma and beta (Numerical Recipes series / continued fraction)
# ---------------------------------------------------------------------------

def _gamma_series(a: float, x: float) -> float:
    """Regularized lower incomplete gamma P(a, x), series branch."""
    if x <= 0.0:
        return 0.0
    ap = a
    total = 1.0 / a
    delta = total
    for _ in range(10_000):
        ap += 1.0
        delta *= x / ap
        total += delta
        if abs(delta) < abs(total) * 1e-15:
            break
    return total * math.exp(-x + a * math.log(x) - math.lgamma(a))


def _gamma_cf(a: float, x: float) -> float:
    """Regularized upper incomplete gamma Q(a, x), continued fraction."""
    if x <= 0.0:
        return 1.0
    tiny = 1e-300
    b = x + 1.0 - a
    c = 1.0 / tiny
    d = 1.0 / b if b != 0.0 else 1.0 / tiny
    h = d
    for i in range(1, 10_000):
        an = -i * (i - a)
        b += 2.0
        d = an * d + b
        if abs(d) < tiny:
            d = tiny
        c = b + an / c
        if abs(c) < tiny:
            c = tiny
        d = 1.0 / d
        delta = d * c
        h *= delta
        if abs(delta - 1.0) < 1e-15:
            break
    return math.exp(-x + a * math.log(x) - math.lgamma(a)) * h


def _gammp(a: float, x: float) -> float:
    """Regularized lower incomplete gamma P(a, x)."""
    if a <= 0.0 or not math.isfinite(a):
        raise UnsupportedDistribution(f"gamma shape must be positive, got {a!r}")
    if x <= 0.0:
        return 0.0
    if x < a + 1.0:
        return min(1.0, max(0.0, _gamma_series(a, x)))
    return min(1.0, max(0.0, 1.0 - _gamma_cf(a, x)))


def _gammq(a: float, x: float) -> float:
    """Regularized upper incomplete gamma Q(a, x) = 1 - P(a, x)."""
    return min(1.0, max(0.0, 1.0 - _gammp(a, x)))


def _betacf(a: float, b: float, x: float) -> float:
    tiny = 1e-300
    qab = a + b
    qap = a + 1.0
    qam = a - 1.0
    c = 1.0
    d = 1.0 - qab * x / qap
    if abs(d) < tiny:
        d = tiny
    d = 1.0 / d
    h = d
    for m in range(1, 10_000):
        m2 = 2 * m
        aa = m * (b - m) * x / ((qam + m2) * (a + m2))
        d = 1.0 + aa * d
        if abs(d) < tiny:
            d = tiny
        c = 1.0 + aa / c
        if abs(c) < tiny:
            c = tiny
        d = 1.0 / d
        h *= d * c
        aa = -(a + m) * (qab + m) * x / ((a + m2) * (qap + m2))
        d = 1.0 + aa * d
        if abs(d) < tiny:
            d = tiny
        c = 1.0 + aa / c
        if abs(c) < tiny:
            c = tiny
        d = 1.0 / d
        delta = d * c
        h *= delta
        if abs(delta - 1.0) < 1e-15:
            break
    return h


def _betai(a: float, b: float, x: float) -> float:
    """Regularized incomplete beta I_x(a, b)."""
    if a <= 0.0 or b <= 0.0:
        raise UnsupportedDistribution("beta shapes must be positive")
    if x <= 0.0:
        return 0.0
    if x >= 1.0:
        return 1.0
    log_beta = (math.lgamma(a + b) - math.lgamma(a) - math.lgamma(b)
                + a * math.log(x) + b * math.log1p(-x))
    if x < (a + 1.0) / (a + b + 2.0):
        value = math.exp(log_beta) * _betacf(a, b, x) / a
    else:
        value = 1.0 - math.exp(log_beta) * _betacf(b, a, 1.0 - x) / b
    return min(1.0, max(0.0, value))


# ---------------------------------------------------------------------------
# Survival functions
# ---------------------------------------------------------------------------

def sf(code: int, params: Sequence[float], t: float) -> float:
    """P(T > t), with an overflow guard for very large finite times."""
    try:
        value = _sf(code, params, t)
    except (OverflowError, ZeroDivisionError, ValueError):
        return 0.0
    return value if math.isfinite(value) else 0.0


def _sf(code: int, params: Sequence[float], t: float) -> float:  # noqa: C901, PLR0912, PLR0915
    """P(T > t) for the engine's distribution ``code`` and parameters."""
    if not math.isfinite(t):
        return 0.0 if t == _INF else 1.0
    code = int(code)
    p = [float(v) for v in params]

    if code == int(Distribution.NONE):
        return 1.0
    if code == int(Distribution.FIXED):
        value = _need(p, 1, code)[0]
        return 1.0 if t < value else 0.0
    if code == int(Distribution.EXPONENTIAL):
        q = _need(p, 2, code)
        mean = _positive(q[0], "Exponential mean")
        loc = q[1]
        return 1.0 if t <= loc else math.exp(-(t - loc) / mean)
    if code == int(Distribution.WEIBULL):
        q = _need(p, 3, code)
        shape = _positive(q[0], "Weibull shape")
        scale = _positive(q[1], "Weibull scale")
        loc = q[2]
        if t <= loc:
            return 1.0
        return math.exp(-((t - loc) / scale) ** shape)
    if code == int(Distribution.NORMAL):
        q = _need(p, 2, code)
        mean = q[0]
        sd = _positive(q[1], "Normal standard deviation")
        z = (t - mean) / (sd * _SQRT2)
        return 0.5 * math.erfc(z)
    if code == int(Distribution.LOGNORMAL):
        q = _need(p, 2, code)
        mean = _positive(q[0], "Lognormal mean")
        sd = _positive(q[1], "Lognormal standard deviation")
        if t <= 0.0:
            return 1.0
        mean_sq = mean * mean
        var = sd * sd
        normal_mean = math.log(mean_sq / math.sqrt(var + mean_sq))
        normal_sd = math.sqrt(math.log((var + mean_sq) / mean_sq))
        z = (math.log(t) - normal_mean) / (normal_sd * _SQRT2)
        return 0.5 * math.erfc(z)
    if code == int(Distribution.GAMMA):
        q = _need(p, 3, code)
        shape = _positive(q[0], "Gamma shape")
        scale = _positive(q[1], "Gamma scale")
        loc = q[2]
        if t <= loc:
            return 1.0
        return _gammq(shape, (t - loc) / scale)
    if code == int(Distribution.ERLANG):
        q = _need(p, 2, code)
        shape = q[0]
        if shape <= 0.0 or abs(shape - round(shape)) > 1e-9:
            raise UnsupportedDistribution(
                f"Erlang shape must be a positive integer, got {shape!r}")
        scale = _positive(q[1], "Erlang scale")
        if t <= 0.0:
            return 1.0
        return _gammq(shape, t / scale)
    if code == int(Distribution.CHI_SQUARE):
        q = _need(p, 1, code)
        df = _positive(q[0], "Chi-square degrees of freedom")
        if t <= 0.0:
            return 1.0
        return _gammq(df / 2.0, t / 2.0)
    if code == int(Distribution.BETA):
        q = _need(p, 2, code)
        a = _positive(q[0], "Beta shape 1")
        b = _positive(q[1], "Beta shape 2")
        if t <= 0.0:
            return 1.0
        if t >= 1.0:
            return 0.0
        return 1.0 - _betai(a, b, t)
    if code == int(Distribution.UNIFORM_REAL):
        q = _need(p, 2, code)
        low, high = q[0], q[1]
        if high < low:
            raise UnsupportedDistribution(
                f"Uniform real high {high!r} is below low {low!r}")
        if high == low:
            return 1.0 if t < low else 0.0
        if t <= low:
            return 1.0
        if t >= high:
            return 0.0
        return (high - t) / (high - low)
    if code == int(Distribution.TRIANGULAR):
        q = _need(p, 3, code)
        low, mode, high = q[0], q[1], q[2]
        if not (low <= mode <= high):
            raise UnsupportedDistribution(
                "Triangular parameters must satisfy min <= mode <= max")
        if high == low:
            return 1.0 if t < low else 0.0
        if t <= low:
            return 1.0
        if t >= high:
            return 0.0
        width = high - low
        if mode == low:
            return ((high - t) / width) ** 2
        if mode == high:
            return 1.0 - ((t - low) / width) ** 2
        if t <= mode:
            return 1.0 - ((t - low) ** 2
                          / (width * (mode - low)))
        return ((high - t) ** 2) / (width * (high - mode))
    if code == int(Distribution.PEARSON5):
        q = _need(p, 3, code)
        shape = _positive(q[0], "Pearson 5 shape")
        scale = _positive(q[1], "Pearson 5 scale")
        loc = q[2]
        if t <= loc:
            return 1.0
        return _gammp(shape, scale / (t - loc))
    if code == int(Distribution.PEARSON6):
        q = _need(p, 3, code)
        shape1 = _positive(q[0], "Pearson 6 shape 1")
        shape2 = _positive(q[1], "Pearson 6 shape 2")
        scale = _positive(q[2], "Pearson 6 scale")
        if t <= 0.0:
            return 1.0
        ratio = t / (scale + t)
        return 1.0 - _betai(shape1, shape2, ratio)
    if code == int(Distribution.EXTREME_VALUE):
        q = _need(p, 2, code)
        scale = _positive(q[0], "Extreme value scale")
        loc = q[1]
        z = (loc - t) / scale
        # X = loc - scale*ln(E), E~Exp(1): P(X > t) = P(E < e^z)
        # = 1 - exp(-e^z). This is the Gumbel-minimum survival, not the
        # Gumbel-maximum exp(-e^z) form.
        return 1.0 - math.exp(-math.exp(z))
    if code == int(Distribution.LAPLACE):
        q = _need(p, 2, code)
        scale = _positive(q[0], "Laplace scale")
        loc = q[1]
        if t < loc:
            return 1.0 - 0.5 * math.exp((t - loc) / scale)
        return 0.5 * math.exp(-(t - loc) / scale)
    if code == int(Distribution.EMPIRICAL):
        return _empirical_sf(p, t)
    if code == int(Distribution.BERNOULLI):
        # An event's success probability, not a life distribution. The
        # network treats it as a constant variable.
        return _unit(p[0], "Bernoulli probability") if p else 0.0
    raise UnsupportedDistribution(
        f"distribution code {code} has no analytical survival function")


def _empirical_sf(data: Sequence[float], t: float) -> float:
    values = [float(v) for v in data]
    if not values:
        raise UnsupportedDistribution("Empirical distribution has no values")
    if any(not math.isfinite(v) for v in values):
        raise UnsupportedDistribution("Empirical values must be finite")
    if any(b < a for a, b in pairwise(values)):
        raise UnsupportedDistribution(
            "Empirical values must be non-decreasing (the engine uses file "
            "order, not a sort)")
    if len(values) == 1:
        return 1.0 if t < values[0] else 0.0
    if t <= values[0]:
        return 1.0
    if t >= values[-1]:
        return 0.0
    # The engine draws position=(n-1)U, picks floor(position), and linearly
    # interpolates between adjacent values. The CDF is therefore linear
    # between knots with probability 1/(n-1) per interval.
    for i in range(len(values) - 1):
        lo, hi = values[i], values[i + 1]
        if hi == lo:
            continue
        if lo <= t < hi:
            fraction = (t - lo) / (hi - lo)
            cdf = (i + fraction) / (len(values) - 1)
            return max(0.0, min(1.0, 1.0 - cdf))
    return 0.0


# ---------------------------------------------------------------------------
# Densities
# ---------------------------------------------------------------------------

def pdf(code: int, params: Sequence[float], t: float) -> float | None:
    """Density at ``t``; ``None`` for a point mass or a discrete law."""
    try:
        value = _pdf(code, params, t)
    except (OverflowError, ZeroDivisionError, ValueError):
        return 0.0
    if value is None:
        return None
    return value if math.isfinite(value) else 0.0


def _pdf(code: int, params: Sequence[float], t: float) -> float | None:  # noqa: C901, PLR0912, PLR0915
    """Density at ``t``; ``None`` for a point mass or a discrete law."""
    code = int(code)
    p = [float(v) for v in params]
    if code in (int(Distribution.FIXED), int(Distribution.BERNOULLI),
                int(Distribution.NONE)):
        return None
    if code == int(Distribution.EXPONENTIAL):
        q = _need(p, 2, code)
        mean = _positive(q[0], "Exponential mean")
        loc = q[1]
        if t <= loc:
            return 0.0
        return math.exp(-(t - loc) / mean) / mean
    if code == int(Distribution.WEIBULL):
        q = _need(p, 3, code)
        shape = _positive(q[0], "Weibull shape")
        scale = _positive(q[1], "Weibull scale")
        loc = q[2]
        if t <= loc:
            return 0.0
        z = (t - loc) / scale
        return (shape / scale) * z ** (shape - 1.0) * math.exp(-z ** shape)
    if code == int(Distribution.NORMAL):
        q = _need(p, 2, code)
        mean = q[0]
        sd = _positive(q[1], "Normal standard deviation")
        z = (t - mean) / sd
        return math.exp(-0.5 * z * z) / (sd * _SQRT2PI)
    if code == int(Distribution.LOGNORMAL):
        q = _need(p, 2, code)
        mean = _positive(q[0], "Lognormal mean")
        sd = _positive(q[1], "Lognormal standard deviation")
        if t <= 0.0:
            return 0.0
        mean_sq = mean * mean
        var = sd * sd
        normal_mean = math.log(mean_sq / math.sqrt(var + mean_sq))
        normal_sd = math.sqrt(math.log((var + mean_sq) / mean_sq))
        z = (math.log(t) - normal_mean) / normal_sd
        return (math.exp(-0.5 * z * z)
                / (t * normal_sd * _SQRT2PI))
    if code in (int(Distribution.GAMMA), int(Distribution.ERLANG),
                int(Distribution.CHI_SQUARE)):
        if code == int(Distribution.GAMMA):
            q = _need(p, 3, code)
            shape = _positive(q[0], "Gamma shape")
            scale = _positive(q[1], "Gamma scale")
            loc = q[2]
        elif code == int(Distribution.ERLANG):
            q = _need(p, 2, code)
            shape = _positive(q[0], "Erlang shape")
            scale = _positive(q[1], "Erlang scale")
            loc = 0.0
        else:
            q = _need(p, 1, code)
            shape = _positive(q[0], "Chi-square degrees of freedom") / 2.0
            scale = 2.0
            loc = 0.0
        if t <= loc:
            return 0.0
        x = (t - loc) / scale
        return math.exp((shape - 1.0) * math.log(x) - x
                        - math.lgamma(shape)) / scale
    if code == int(Distribution.BETA):
        q = _need(p, 2, code)
        a = _positive(q[0], "Beta shape 1")
        b = _positive(q[1], "Beta shape 2")
        if t <= 0.0 or t >= 1.0:
            return 0.0
        log_pdf = ((a - 1.0) * math.log(t) + (b - 1.0) * math.log1p(-t)
                   + math.lgamma(a + b) - math.lgamma(a) - math.lgamma(b))
        return math.exp(log_pdf)
    if code == int(Distribution.UNIFORM_REAL):
        q = _need(p, 2, code)
        low, high = q[0], q[1]
        if high <= low:
            return None
        return 1.0 / (high - low) if low < t < high else 0.0
    if code == int(Distribution.TRIANGULAR):
        q = _need(p, 3, code)
        low, mode, high = q[0], q[1], q[2]
        if high <= low:
            return None
        if t < low or t > high:
            return 0.0
        if mode == low:
            return 2.0 * (high - t) / ((high - low) ** 2) if t < high else 0.0
        if mode == high:
            return 2.0 * (t - low) / ((high - low) ** 2) if t > low else 0.0
        if t < mode:
            return 2.0 * (t - low) / ((high - low) * (mode - low))
        if t > mode:
            return 2.0 * (high - t) / ((high - low) * (high - mode))
        return 2.0 / (high - low)
    if code == int(Distribution.PEARSON5):
        q = _need(p, 3, code)
        shape = _positive(q[0], "Pearson 5 shape")
        scale = _positive(q[1], "Pearson 5 scale")
        loc = q[2]
        if t <= loc:
            return 0.0
        y = 1.0 / (t - loc)
        return (scale ** shape / math.gamma(shape)) * y ** (shape + 1.0) * math.exp(-scale * y)
    if code == int(Distribution.PEARSON6):
        q = _need(p, 3, code)
        shape1 = _positive(q[0], "Pearson 6 shape 1")
        shape2 = _positive(q[1], "Pearson 6 shape 2")
        scale = _positive(q[2], "Pearson 6 scale")
        if t <= 0.0:
            return 0.0
        log_beta = math.lgamma(shape1 + shape2) - math.lgamma(shape1) - math.lgamma(shape2)
        return math.exp((shape1 - 1.0) * math.log(t)
                        + shape2 * math.log(scale)
                        - (shape1 + shape2) * math.log(scale + t)
                        + log_beta)
    if code == int(Distribution.EXTREME_VALUE):
        q = _need(p, 2, code)
        scale = _positive(q[0], "Extreme value scale")
        loc = q[1]
        z = (loc - t) / scale
        return (1.0 / scale) * math.exp(z - math.exp(z))
    if code == int(Distribution.LAPLACE):
        q = _need(p, 2, code)
        scale = _positive(q[0], "Laplace scale")
        loc = q[1]
        return math.exp(-abs(t - loc) / scale) / (2.0 * scale)
    if code == int(Distribution.EMPIRICAL):
        values = [float(v) for v in p]
        if len(values) < 2:
            return None
        if t <= values[0] or t >= values[-1]:
            return 0.0
        for i in range(len(values) - 1):
            lo, hi = values[i], values[i + 1]
            if hi == lo:
                continue
            if lo < t < hi:
                return 1.0 / ((len(values) - 1) * (hi - lo))
        return None
    raise UnsupportedDistribution(
        f"distribution code {code} has no analytical density")


# ---------------------------------------------------------------------------
# Means and support
# ---------------------------------------------------------------------------

def _mean(code: int, params: Sequence[float]) -> float | None:  # noqa: C901, PLR0912
    """E[T], or ``None`` when the mean is undefined or infinite."""
    code = int(code)
    p = [float(v) for v in params]
    if code == int(Distribution.NONE):
        return None
    if code == int(Distribution.FIXED):
        return _need(p, 1, code)[0]
    if code == int(Distribution.EXPONENTIAL):
        q = _need(p, 2, code)
        return q[1] + _positive(q[0], "Exponential mean")
    if code == int(Distribution.WEIBULL):
        q = _need(p, 3, code)
        shape = _positive(q[0], "Weibull shape")
        scale = _positive(q[1], "Weibull scale")
        return q[2] + scale * math.gamma(1.0 + 1.0 / shape)
    if code == int(Distribution.NORMAL):
        return _need(p, 2, code)[0]
    if code == int(Distribution.LOGNORMAL):
        return _positive(_need(p, 2, code)[0], "Lognormal mean")
    if code == int(Distribution.GAMMA):
        q = _need(p, 3, code)
        return q[2] + _positive(q[0], "Gamma shape") * _positive(q[1], "Gamma scale")
    if code == int(Distribution.ERLANG):
        q = _need(p, 2, code)
        return _positive(q[0], "Erlang shape") * _positive(q[1], "Erlang scale")
    if code == int(Distribution.CHI_SQUARE):
        return _positive(_need(p, 1, code)[0], "Chi-square degrees of freedom")
    if code == int(Distribution.BETA):
        q = _need(p, 2, code)
        a = _positive(q[0], "Beta shape 1")
        b = _positive(q[1], "Beta shape 2")
        return a / (a + b)
    if code == int(Distribution.UNIFORM_REAL):
        q = _need(p, 2, code)
        return (q[0] + q[1]) / 2.0
    if code == int(Distribution.TRIANGULAR):
        q = _need(p, 3, code)
        return (q[0] + q[1] + q[2]) / 3.0
    if code == int(Distribution.PEARSON5):
        q = _need(p, 3, code)
        shape = _positive(q[0], "Pearson 5 shape")
        scale = _positive(q[1], "Pearson 5 scale")
        if shape <= 1.0:
            return None
        return q[2] + scale / (shape - 1.0)
    if code == int(Distribution.PEARSON6):
        q = _need(p, 3, code)
        shape1 = _positive(q[0], "Pearson 6 shape 1")
        shape2 = _positive(q[1], "Pearson 6 shape 2")
        scale = _positive(q[2], "Pearson 6 scale")
        if shape2 <= 1.0:
            return None
        return scale * shape1 / (shape2 - 1.0)
    if code == int(Distribution.EXTREME_VALUE):
        q = _need(p, 2, code)
        return q[1] + _GAMMA * _positive(q[0], "Extreme value scale")
    if code == int(Distribution.LAPLACE):
        return _need(p, 2, code)[1]
    if code == int(Distribution.EMPIRICAL):
        values = [float(v) for v in p]
        if not values:
            return None
        if len(values) == 1:
            return values[0]
        return sum((a + b) / 2.0 for a, b in pairwise(values)) / (len(values) - 1)
    if code == int(Distribution.BERNOULLI):
        return None
    raise UnsupportedDistribution(
        f"distribution code {code} has no analytical mean")


def mean(code: int, params: Sequence[float]) -> float | None:
    """E[T], or ``None`` when undefined/infinite/not numerically evaluable."""
    try:
        value = _mean(code, params)
    except (OverflowError, ZeroDivisionError, ValueError):
        return None
    return value if value is None or math.isfinite(value) else None


def support(code: int, params: Sequence[float]) -> tuple[float, float | None]:
    """The support of the distribution, as ``(lower, upper|None)``."""
    code = int(code)
    p = [float(v) for v in params]
    if code == int(Distribution.NONE):
        return 0.0, None
    if code == int(Distribution.FIXED):
        value = _need(p, 1, code)[0]
        return value, value
    if code in (int(Distribution.BETA),):
        return 0.0, 1.0
    if code == int(Distribution.UNIFORM_REAL):
        q = _need(p, 2, code)
        return q[0], q[1]
    if code == int(Distribution.TRIANGULAR):
        q = _need(p, 3, code)
        return q[0], q[2]
    if code == int(Distribution.EMPIRICAL):
        values = [float(v) for v in p]
        if not values:
            raise UnsupportedDistribution("Empirical distribution has no values")
        return values[0], values[-1]
    if code in (int(Distribution.EXPONENTIAL), int(Distribution.WEIBULL),
                int(Distribution.GAMMA), int(Distribution.ERLANG),
                int(Distribution.LOGNORMAL), int(Distribution.PEARSON5)):
        return 0.0, None
    return 0.0, None
