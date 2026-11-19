"""Quantile dot plots: a frequency picture of a run-to-run result.

Each dot is one equal-probability slice of the outcome distribution, so a
person reads "7 dots in 100 below the target" rather than comparing two
means.  The method is the one evaluated by Kay, Kola, Hullman and Munson
(2016) and Kale, Kay and Hullman (2020): take `n` evenly spaced quantiles
and stack equal values.

Pure data, no Qt: the GUI paints the dots, the report renders them to SVG
and the API returns the same list.
"""
from __future__ import annotations

import math
from collections.abc import Iterable, Sequence


def quantile_dots(values: Iterable[float], n_dots: int = 40) -> list[dict]:
    """`[{value, count}, ...]` in ascending value, at most `n_dots` dots.

    Fewer values than dots: one dot each, duplicates stacked.  One value:
    one dot (never a made-up spread).  A non-finite value is skipped.
    """
    clean = sorted(float(v) for v in values
                   if v is not None and math.isfinite(float(v)))
    if not clean:
        return []
    if len(clean) <= n_dots:
        picked = clean
    else:
        picked = []
        for i in range(n_dots):
            q = (i + 0.5) / n_dots
            index = min(len(clean) - 1, max(0, math.ceil(q * len(clean)) - 1))
            picked.append(clean[index])
    out: list[dict] = []
    for value in picked:
        if out and out[-1]["value"] == value:
            out[-1]["count"] += 1
        else:
            out.append({"value": value, "count": 1})
    return out


def meets(value: float, target: float, op: str | None = ">=") -> bool:
    """Whether one value meets `op target` (">=" when not given): a
    "<=" target was judged as ">=" and its verdict came out inverted."""
    return {">": value > target, "<=": value <= target, "<": value < target,
            "==": abs(value - target) < 1e-12,
            "!=": abs(value - target) >= 1e-12}.get(op or ">=", value >= target)


def split_at(values: Sequence[float], target: float | None,
             op: str | None = ">=") -> tuple[int, int]:
    """How many values met the target (by `op`, "at least" when not given)
    and the number considered.  `target is None` counts every value as
    met, which is what "no target stated" means on the page."""
    usable = [float(v) for v in values
              if v is not None and math.isfinite(float(v))]
    if target is None:
        return len(usable), len(usable)
    return sum(1 for v in usable if meets(v, target, op)), len(usable)


def proportion_text(met: int, total: int) -> str:
    """`7 runs in 100 met the target` (or "all runs", "no runs"); with
    fewer than 100 runs the real counts, "1 of 20 runs" (it said "5 runs in
    100" for 1 of 20)."""
    if total <= 0:
        return "no runs to compare"
    if met == total:
        return "every run met the target"
    if met == 0:
        return "no run met the target"
    if total < 100:
        return f"{met} of {total} runs met the target"
    per_100 = 100.0 * met / total
    shown = f"{per_100:.0f}" if per_100 >= 1 else f"{per_100:.2g}"
    return f"{shown} runs in 100 met the target"
