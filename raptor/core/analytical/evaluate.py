"""Metrics derived from a built analytical BDD.

All functions are pure: the BDD and the model are read-only. The API layer
turns these values into JSON-safe dictionaries.
"""
from __future__ import annotations

import math
from dataclasses import dataclass

from .bdd import FALSE, TRUE
from .errors import NotApplicable
from .network import BuildResult
from . import survival

#: Below this system reliability the MTTF tail is treated as negligible.
_TAIL = 1e-12
#: Refuse to integrate past this scale (roughly 3e9 years in hours).
_MAX_SCALE = 1e15


@dataclass(frozen=True)
class ImportanceRow:
    """One element's exact importance at a time (or at steady state)."""

    kind: str
    id: int
    name: str
    probability: float
    birnbaum: float
    raw: float | None
    rrw: float | None
    vesely_fussell: float | None
    criticality: float | None


def system_probability(build: BuildResult, t: float) -> float:
    """``R_sys(t)`` in reliability mode, ``A_sys(t)`` in availability mode."""
    return build.bdd.probability(build.root, build.probabilities(t))


def availability(build: BuildResult) -> float:
    """Steady-state system availability (availability-mode build)."""
    if build.mode != "availability":
        raise NotApplicable("availability() needs an availability-mode build")
    return system_probability(build, 0.0)


def pdf(build: BuildResult, t: float) -> float | None:
    """System density ``f_sys(t)`` for a reliability-mode build.

    ``None`` where a block's own density is undefined (an atom of a Fixed or
    Empirical life distribution).
    """
    if build.mode != "reliability":
        raise NotApplicable("pdf() needs a reliability-mode build")
    derivatives = build.derivatives(t)
    if derivatives is None:
        return None
    _, slope = build.bdd.probability_and_derivative(
        build.root, build.probabilities(t), derivatives)
    # ``0.0 - slope`` rather than ``-slope`` so a zero slope is 0, not -0.
    return 0.0 - slope


def hazard(build: BuildResult, t: float) -> float | None:
    """``f_sys(t) / R_sys(t)``; ``None`` where either side is undefined."""
    density = pdf(build, t)
    reliability = system_probability(build, t)
    if density is None or reliability <= 0.0:
        return None
    if not math.isfinite(density):
        return None
    return density / reliability


def _scale(build: BuildResult) -> float:
    """A positive starting scale for tail searches."""
    if build.horizon and build.horizon > 0.0:
        return float(build.horizon)
    means = [info.mean_fail for info in build.variables
             if info.mean_fail is not None and info.mean_fail > 0.0]
    if means:
        return max(means)
    return 1.0


def _upper_bound(build: BuildResult, target: float) -> float | None:
    """Smallest power-of-two scale with R <= target, or ``None``."""
    if target >= 1.0:
        return 0.0
    value = max(_scale(build), 1.0)
    if system_probability(build, 0.0) <= target:
        return 0.0
    for _ in range(60):
        if system_probability(build, value) <= target:
            return value
        value *= 2.0
        if value > _MAX_SCALE:
            return None
    return None


def mttf(build: BuildResult) -> float | None:
    """Mean time to failure by adaptive Simpson; ``None`` if it diverges."""
    if build.mode != "reliability":
        raise NotApplicable("mttf() needs a reliability-mode build")
    if build.root == FALSE:
        return 0.0
    if build.root == TRUE:
        return None
    if system_probability(build, 0.0) <= 0.0:
        return 0.0
    upper = _upper_bound(build, _TAIL)
    if upper is None or upper <= 0.0:
        return None
    return _adaptive_simpson(lambda x: system_probability(build, x),
                             0.0, upper, tol=1e-9, max_depth=24)


def bx_life(build: BuildResult, percent: float) -> float | None:  # noqa: C901, PLR0912
    """``B_X``: time at which the system reliability equals ``1 - X/100``."""
    if build.mode != "reliability":
        raise NotApplicable("bx_life() needs a reliability-mode build")
    if not (0.0 <= percent <= 100.0):
        raise ValueError("BX percentage must be in [0, 100]")
    target = 1.0 - percent / 100.0
    if target >= 1.0:
        return 0.0
    if target <= 0.0:
        return _certain_failure_time(build)
    if system_probability(build, 0.0) <= target:
        return 0.0
    upper = _upper_bound(build, target)
    if upper is None:
        return None
    low = 0.0
    high = float(upper)
    for _ in range(120):
        mid = (low + high) / 2.0
        if system_probability(build, mid) > target:
            low = mid
        else:
            high = mid
        if high - low <= max(1e-12, high * 1e-12):
            break
    return (low + high) / 2.0


def _death_time(info) -> float:
    """Calendar time by which this element has certainly failed (or inf)."""
    if info.perfect:
        return math.inf
    if info.event_success is not None:
        return 0.0 if info.event_success <= 0.0 else math.inf
    _, high = survival.support(info.fail_code, info.fail_params)
    if high is None or info.duty_cycle <= 0.0:
        return math.inf
    return max(0.0, (high - info.start_age) / info.duty_cycle)


def _certain_failure_time(build: BuildResult) -> float | None:
    """The earliest time at which ``R_sys`` is exactly zero, or ``None``.

    ``R_sys(t) = 0`` as soon as the elements that are certainly dead by ``t``
    (their finite support has ended) cut every Start-to-End path, while every
    other element is given the benefit of surviving. A series chain therefore
    reaches zero at its *earliest* finite support end, a parallel group at its
    latest; an unbounded life distribution never forces failure by itself.
    """
    deaths = [_death_time(info) for info in build.variables]
    for moment in sorted({d for d in deaths if math.isfinite(d)}):
        alive = [0.0 if d <= moment else 1.0 for d in deaths]
        if build.bdd.probability(build.root, alive) <= 0.0:
            return moment
    return None


def conditional_reliability(build: BuildResult, t: float,
                            tau: float) -> float | None:
    """``R_sys(t + tau) / R_sys(t)``; ``None`` when the system is already down."""
    if build.mode != "reliability":
        raise NotApplicable(
            "conditional_reliability() needs a reliability-mode build")
    if t < 0.0 or tau < 0.0:
        raise ValueError("at and tau must be non-negative")
    base = system_probability(build, t)
    if base <= 0.0:
        return None
    value = system_probability(build, t + tau) / base
    return min(1.0, max(0.0, value))


def importance(build: BuildResult, at: float = 0.0) -> list[ImportanceRow]:
    """Exact Birnbaum/RAW/RRW/VF/criticality for every variable."""
    probabilities = build.probabilities(at)
    base = build.bdd.probability(build.root, probabilities)
    unavailability = 1.0 - base
    rows: list[ImportanceRow] = []
    for info in build.variables:
        if info.variable is None:
            continue
        variable = info.variable
        p = probabilities[variable]
        upper = list(probabilities)
        upper[variable] = 1.0
        lower = list(probabilities)
        lower[variable] = 0.0
        p1 = build.bdd.probability(build.root, upper)
        p0 = build.bdd.probability(build.root, lower)
        birnbaum = p1 - p0
        raw = ((1.0 - p0) / unavailability
               if unavailability > 0.0 else None)
        rrw = (unavailability / (1.0 - p1)
               if (1.0 - p1) > 0.0 else None)
        vf = ((p1 - base) / unavailability
              if unavailability > 0.0 else None)
        criticality = (birnbaum * (1.0 - p) / unavailability
                       if unavailability > 0.0 else None)
        rows.append(ImportanceRow(
            kind=info.kind,
            id=info.id,
            name=info.name,
            probability=p,
            birnbaum=birnbaum,
            raw=raw,
            rrw=rrw,
            vesely_fussell=vf,
            criticality=criticality,
        ))
    rows.sort(key=lambda row: (row.criticality is None,
                               -(row.criticality or 0.0),
                               row.name.lower()))
    return rows


def equation(build: BuildResult, *, max_terms: int = 5000) -> str | None:
    """A sum-of-path-products equation in block tokens, or ``None``.

    The tokens are ``R_<name>`` (reliability mode) or ``A_<name>``
    (availability mode). Duplicate names get their element id appended. A
    path with several literals is parenthesised; a single literal is not.
    ``None`` means the diagram has more than ``max_terms`` paths.
    """
    paths = build.bdd.paths_to_true(build.root, max_paths=max_terms)
    if paths is None:
        return None
    by_variable = {info.variable: info for info in build.variables
                   if info.variable is not None}
    prefix = "R" if build.mode == "reliability" else "A"
    token_counts: dict[str, int] = {}
    tokens: dict[int, str] = {}
    for variable, info in by_variable.items():
        base = f"{prefix}_{_token(info.name)}"
        token_counts[base] = token_counts.get(base, 0) + 1
        tokens[variable] = (base if token_counts[base] == 1
                            else f"{base}_{info.id}")
    if not paths:
        return "0"
    terms: list[str] = []
    total_chars = 0
    for path in paths:
        literals: list[str] = []
        for variable, high in path:
            token = tokens.get(variable, f"x{variable}")
            literals.append(token if high else f"(1 - {token})")
        if not literals:
            terms.append("1")
        elif len(literals) == 1:
            terms.append(literals[0])
        else:
            terms.append("(" + " * ".join(literals) + ")")
        total_chars += len(terms[-1]) + 3
        if total_chars > 200_000:
            return None
    return " + ".join(terms)


def _token(name: str) -> str:
    cleaned = "".join(char if char.isalnum() else "_"
                      for char in name.strip())
    cleaned = cleaned.strip("_") or "unnamed"
    return cleaned if len(cleaned) <= 64 else cleaned[:61] + "..."


def _adaptive_simpson(f, a: float, b: float, *, tol: float,
                      max_depth: int) -> float | None:
    """Adaptive Simpson with a relative/absolute tolerance."""
    if b <= a:
        return 0.0
    fa = f(a)
    fb = f(b)
    if not (math.isfinite(fa) and math.isfinite(fb)):
        return None
    mid = (a + b) / 2.0
    fm = f(mid)
    if not math.isfinite(fm):
        return None
    whole = (b - a) * (fa + 4.0 * fm + fb) / 6.0

    def recurse(left: float, right: float, f_left: float, f_mid: float,
                f_right: float, estimate: float, depth: int) -> float:
        middle = (left + right) / 2.0
        lm = (left + middle) / 2.0
        rm = (middle + right) / 2.0
        f_lm = f(lm)
        f_rm = f(rm)
        left_est = (middle - left) * (f_left + 4.0 * f_lm + f_mid) / 6.0
        right_est = (right - middle) * (f_mid + 4.0 * f_rm + f_right) / 6.0
        delta = left_est + right_est - estimate
        if depth <= 0 or abs(delta) <= 15.0 * tol:
            return left_est + right_est + delta / 15.0
        return (recurse(left, middle, f_left, f_lm, f_mid, left_est,
                        depth - 1)
                + recurse(middle, right, f_mid, f_rm, f_right, right_est,
                          depth - 1))

    value = recurse(a, b, fa, fm, fb, whole, max_depth)
    if not math.isfinite(value):
        return None
    return value
