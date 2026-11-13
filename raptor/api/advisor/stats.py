"""Small exact statistics the rules need. No SciPy, no randomness."""
from __future__ import annotations

import math


def poisson_cdf(k: int, mean: float) -> float:
    """P(X <= k) for X ~ Poisson(mean)."""
    if k < 0:
        return 0.0
    if mean <= 0:
        return 1.0
    term = math.exp(-mean)
    total = term
    for i in range(1, k + 1):
        term *= mean / i
        total += term
    return min(1.0, total)


def smallest_stock(mean: float, risk: float, limit: int = 10_000) -> int:
    """The smallest stock s with P(demand > s) <= risk."""
    s = 0
    while s < limit and 1.0 - poisson_cdf(s, mean) > risk:
        s += 1
    return s
