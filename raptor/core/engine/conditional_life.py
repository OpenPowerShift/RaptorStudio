"""Draw a life conditional on the block having already survived to an age.

Used by the age features (start age, restoration factor). The life returned
is a full life measured from age 0, so the time left is ``life - age``.

Plain rejection (draw until the life exceeds the age) is exact but becomes
hopeless once the age is deep in the tail: at an age where only one life in
a million survives, a thousand rejections almost always fail. The old code
then returned ``age + 0.000001``, an instant failure, which made a worn block
look far worse than it is (an exponential block, which does not age at all,
failed three times as often under a Type I restoration). This module keeps a
few rejection tries, because they are cheap and exact, and then switches to
inverse sampling on the survival function, which is exact at any age.
"""
from __future__ import annotations

import math
from collections.abc import Sequence

from raptor.core.rng import Distribution, RandomSource

#: Cheap exact tries before switching to inverse sampling.
_REJECTION_TRIES = 10
#: Returned past the top of a bounded distribution (a Fixed life the block
#: has already outlived): fail straight away, as the old code did.
_INSTANT = 0.000001


def _uniform(rng: RandomSource) -> float:
    return rng.draw_number(Distribution.UNIFORM_REAL, [0.0, 1.0])


def _closed_form(code: int, p: Sequence[float], age: float, u: float) -> float | None:
    """Exact inverse for the laws that have one, else None."""
    if code == int(Distribution.EXPONENTIAL) and len(p) >= 2:
        mean, loc = float(p[0]), float(p[1])
        # Memoryless: past the location, the time left does not depend on age.
        return max(age, loc) + (-mean * math.log(u))
    if code == int(Distribution.WEIBULL) and len(p) >= 3:
        shape, scale, loc = float(p[0]), float(p[1]), float(p[2])
        x = max(0.0, age - loc)
        return loc + scale * ((x / scale) ** shape - math.log(u)) ** (1.0 / shape)
    return None


def _bisect(code: int, p: Sequence[float], age: float, target: float) -> float:
    """The ``t > age`` where ``sf(t) == target``, by bisection."""
    from raptor.core.analytical.survival import sf

    lo = age
    hi = age + max(1.0, abs(age))
    for _ in range(400):
        if sf(code, p, hi) <= target:
            break
        lo, hi = hi, hi + 2.0 * (hi - age)
    for _ in range(200):
        mid = 0.5 * (lo + hi)
        if mid in (lo, hi):
            break
        if sf(code, p, mid) > target:
            lo = mid
        else:
            hi = mid
    return hi


def conditional_life(rng: RandomSource, code: int, params: Sequence[float],
                     age: float) -> float:
    """A full life drawn from the block's failure law, given it exceeds *age*."""
    # Imported here: `raptor.core.analytical` imports the engine's loader,
    # so a module-level import would be circular.
    from raptor.core.analytical.errors import UnsupportedDistribution
    from raptor.core.analytical.survival import sf, support

    params = list(params)
    for _ in range(_REJECTION_TRIES):
        draw = rng.draw_number(Distribution(code), params)
        if draw > age:
            return draw
    try:
        survived = sf(code, params, age)
        upper = support(code, params)[1]
    except UnsupportedDistribution:
        survived, upper = None, None
    if survived is None:
        # No survival function for this law: keep rejecting, as before.
        for _ in range(1000):
            draw = rng.draw_number(Distribution(code), params)
            if draw > age:
                return draw
        return age + _INSTANT
    if upper is not None and age >= upper:
        return age + _INSTANT
    u = _uniform(rng)
    exact = _closed_form(code, params, age, u)
    if exact is not None:
        return max(exact, age + _INSTANT)
    if survived <= 0.0:
        # The survival function underflowed: the age is beyond any life the
        # law can practically produce.
        return age + _INSTANT
    return max(_bisect(code, params, age, u * survived), age + _INSTANT)
