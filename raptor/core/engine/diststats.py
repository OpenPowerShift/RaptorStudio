"""Distribution statistics -- port of `ComputeStats` in Raptor 7, used by the 'Update Statistics'
display (Mean / Standard Deviation labels under each distribution's parameter
fields) and by the Empirical Data dialog.

Murphy identifiers: Raptor uses 12345.6789 for out-of-range results (shown as
'Undefined') and 12345.6788 for 'not applicable', preserved here so GUI
wording matches the Raptor 7 engine.
"""
from __future__ import annotations

import math

MURPHY_UNDEFINED = 12345.6789
MURPHY_NA = 12345.6788


def gamma_function(x: float) -> float:
    """Port of `GammaFunction`: the six-term
    Lanczos approximation, not an exact gamma. It agrees with `math.gamma`
    to about 1e-15 relative near 1-3 and drifts to about 1e-11 by x=21
    (a Weibull shape near 0.05), so the engine's Weibull mean
    (`dependency.distro_mean`) uses this too. Only reached with x > 0."""
    y = (1.000000000190015 + (76.18009172947146 / (x + 1.0)) - (86.50532032941677 / (x + 2.0))
         + (24.01409824083091 / (x + 3.0)) - (1.231739572450155 / (x + 4.0))
         + (0.001208650973866179 / (x + 5.0)) - (0.000005395239384953 / (x + 6.0)))
    return (math.pow(x + 5.5, x + 0.5) * math.exp(-1.0 * (x + 5.5)) * _sqrt(2.0 * math.pi) * y) / x


def _sqrt(v: float) -> float:
    """SQRT that yields NaN (shown as 'Undefined') instead of raising for a
    negative argument, e.g. a Chi-square with negative degrees of freedom
    typed into the dialog."""
    return math.sqrt(v) if v >= 0 else float("nan")


def compute_stats(distro: int, params: list[float]) -> tuple[float | str, float | str] | None:
    """Returns (mean, stdev). Values may be:
    - floats (displayable), or
    - 'Undefined' / 'N/A' strings when Raptor's Murphy sentinels fire,
    - None when params are missing/insufficient (caller shows '—').

    `params` is the RAW triple FParam1..3 / RParam1..3 exactly as stored
    in .rbd columns (see each WHEN branch for slot meaning).
    """
    p = list(params) + [0.0] * 3
    # ensure indexing like source's 1-based params[1..3]
    p1, p2, p3 = p[0], p[1], p[2]

    if distro == 1:   # Beta
        if p1 <= 0 or p2 <= 0:
            return None
        mean = p1 / (p1 + p2)
        stdev = _sqrt(p1 * p2 / ((p1 + p2) ** 2 * (p1 + p2 + 1)))
    elif distro == 2:  # Chi-square
        mean = p1
        stdev = _sqrt(2 * p1)
    elif distro == 3:  # Binomial
        mean = p1 * p2
        stdev = _sqrt(max(p1 * p2 * (1 - p2), 0.0))
    elif distro == 4:  # Exponential
        mean = p1 + p2
        stdev = p1
    elif distro == 5:  # Erlang
        mean = p1 * p2
        stdev = _sqrt(p1 * p2 ** 2)
    elif distro == 6:  # Gamma
        mean = p1 * p2 + p3
        stdev = _sqrt(p1 * p2 ** 2)
    elif distro == 7:  # Lognormal
        mean = p1
        stdev = p2
    elif distro == 8:  # Normal
        mean = p1
        stdev = p2
    elif distro == 9:  # Uniform Integer
        mean = (p1 + p2) / 2
        stdev = _sqrt((p2 - p1) ** 2 / 12)
    elif distro == 10:  # Uniform Real
        mean = (p1 + p2) / 2
        stdev = _sqrt((p2 - p1) ** 2 / 12)
    elif distro == 11:  # Pearson 5
        if p1 <= 1:
            mean = stdev = MURPHY_UNDEFINED
        else:
            mean = p2 / (p1 - 1) + p3
            stdev = MURPHY_UNDEFINED if p1 <= 2 else p2 / (p1 - 1) / _sqrt(p1 - 2)
    elif distro == 12:  # Pearson 6
        if p2 <= 1:
            mean = stdev = MURPHY_UNDEFINED
        else:
            mean = p3 * p1 / (p2 - 1)
            stdev = MURPHY_UNDEFINED if p2 <= 2 else _sqrt(
                (p3 ** 2 * p1 * (p1 + p2 - 1)) / ((p2 - 1) ** 2 * (p2 - 2)))
    elif distro == 13:  # Poisson
        mean = p1
        stdev = _sqrt(p1)
    elif distro == 14:  # Triangular
        mean = (p1 + p2 + p3) / 3
        stdev = _sqrt(
            (p1 ** 2 + p2 ** 2 + p3 ** 2 - p1 * p2 - p1 * p3
             - p2 * p3) / 18)
    elif distro == 15:  # Weibull
        if p1 < 0.05:
            mean = stdev = MURPHY_UNDEFINED
        elif p1 == 1:
            mean = p2 + p3
            stdev = p2
        elif p1 > 10_000_000:
            mean = p2 + p3
            stdev = 0.0
        else:
            x = 1 + 1 / p1
            z = gamma_function(x)
            mean = p2 * z + p3
            y = 1 + 2 / p1
            z2 = gamma_function(y)
            stdev = p2 * _sqrt(z2 - z ** 2)
    elif distro == 16:  # Empirical
        vals = [v for v in params if isinstance(v, (int, float))]
        # the .rbd row carries only FParam1..3; a full empirical table
        # arrives via the dialog as a long list — both work here
        if not vals:
            return None
        mean = sum(vals) / len(vals)
        stdev = _sqrt(sum((v - mean) ** 2 for v in vals) / len(vals))
    elif distro == 17:  # Point Estimate
        mean = MURPHY_NA; stdev = MURPHY_NA
    elif distro == 18:  # None
        mean = MURPHY_NA; stdev = MURPHY_NA
    elif distro == 19:  # Fixed
        mean = p1
        stdev = 0.0
    elif distro == 20:  # Exponential Power/Error
        mean = p3
        if p1 <= 0:  # GammaFunction(0) divides by zero
            return mean, "Undefined"
        x = 3 * p1 / 2
        z = gamma_function(x)
        y = p1 / 2
        z2 = gamma_function(y)
        stdev = _sqrt(2 ** p1 * p2 ** 2 * z / z2)
    elif distro == 21:  # Extreme Value
        mean = p2 + 0.57721 * p1
        stdev = _sqrt(p1 ** 2 * math.pi ** 2 / 6)
    elif distro == 22:  # Laplace
        mean = p2
        stdev = _sqrt(2 * p1 ** 2)
    else:
        return None

    def _clean(v):
        if v != v or v in (float("inf"), float("-inf")) or v == MURPHY_UNDEFINED:
            return "Undefined"
        if v == MURPHY_NA:
            return "N/A"
        return v

    return _clean(mean), _clean(stdev)
