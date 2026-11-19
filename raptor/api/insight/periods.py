"""Availability period by period: what a bad year looks like.

A guarantee is usually judged per calendar year, but a run's availability is
an average over its whole life, so one bad year is invisible in it. Here
each run's system outages (exact start and end times, from the attribution
replay) are cut on period boundaries, giving one availability per (run,
period). The summary pools them, and also reports by period index, because
years within one life are not independent and late life differs from early
life when equipment ages.

Percentiles use the nearest-rank method. The chance of falling below a
threshold is a proportion, so it carries a Wilson interval.
"""
from __future__ import annotations
from raptor.api.insight.axes import pct

import math
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any, Literal

#: P25 and P75 are the year-by-year chart's inner band; before they were
#: here the band was drawn only from hand-made data, never from a run.
PERCENTILES = (1, 5, 10, 25, 50, 75, 90, 95, 99)
#: How many equal-share dots stand for the pooled run-years.
DOTS = 20
#: Hours in a year, used when the model's time unit is hours.
HOURS_PER_YEAR = 8760.0


@dataclass(frozen=True)
class PeriodSpec:
    length: float                                   # model time units per period
    offset: float = 0.0                             # where the first period starts
    partial: Literal["drop", "prorate"] = "drop"    # the incomplete last period

    def __post_init__(self) -> None:
        if not self.length > 0:
            raise ValueError("the period length must be positive")
        if self.offset < 0:
            raise ValueError("the period offset cannot be negative")


def period_availability(red: Sequence[tuple[float, float]], start: float, end: float,
                        spec: PeriodSpec) -> tuple[list[float], int]:
    """One run: availability of each period between `start` and `end`, from
    its system-down intervals. Returns (values, number of partial periods
    dropped). A period is partial when the run does not cover all of it."""
    first = start + spec.offset
    if end <= first:
        return [], 0
    count = math.ceil((end - first) / spec.length - 1e-12)
    down = [0.0] * count
    for a, b in red:
        a, b = max(a, first), min(b, end)
        if b <= a:
            continue
        i = min(count - 1, int((a - first) // spec.length))
        while a < b and i < count:
            edge = first + (i + 1) * spec.length
            piece = min(b, edge) - a
            down[i] += piece
            a += piece
            i += 1
    values, dropped = [], 0
    for i in range(count):
        covered = min(end, first + (i + 1) * spec.length) - (first + i * spec.length)
        if covered < spec.length * (1 - 1e-9):
            if spec.partial == "drop":
                dropped += 1
                continue
        values.append(1.0 - down[i] / covered)
    return values, dropped


def period_capacity(points: Sequence[tuple[float, float]], generated: float,
                    start: float, end: float, spec: PeriodSpec
                    ) -> tuple[list[float], int]:
    """One run: capacity-weighted availability of each period, from its
    capacity sink (`points` = (time, delivered flow), a step function: each
    value holds until the next point).  The value of a period is the energy
    delivered / (flow generated x period length).  Returns (values, number
    of partial periods dropped), cut exactly as `period_availability` cuts."""
    first = start + spec.offset
    if end <= first or generated <= 0:
        return [], 0
    count = math.ceil((end - first) / spec.length - 1e-12)
    delivered = [0.0] * count
    steps = sorted((float(t), float(f)) for t, f in points)
    for i, (t, flow) in enumerate(steps):
        a = max(t, first)
        b = min(steps[i + 1][0] if i + 1 < len(steps) else end, end)
        if b <= a:
            continue
        k = min(count - 1, int((a - first) // spec.length))
        while a < b and k < count:
            edge = first + (k + 1) * spec.length
            piece = min(b, edge) - a
            delivered[k] += max(0.0, flow) * piece
            a += piece
            k += 1
    values, dropped = [], 0
    for k in range(count):
        covered = min(end, first + (k + 1) * spec.length) - (first + k * spec.length)
        if covered < spec.length * (1 - 1e-9) and spec.partial == "drop":
            dropped += 1
            continue
        values.append(delivered[k] / (generated * covered))
    return values, dropped


def percentile(sorted_values: Sequence[float], p: float) -> float:
    """Nearest-rank percentile (p in 0..100) of an ascending sequence."""
    if not sorted_values:
        raise ValueError("no values")
    rank = max(1, math.ceil(p / 100.0 * len(sorted_values)))
    return sorted_values[min(len(sorted_values), rank) - 1]


def wilson(successes: int, n: int, z: float = 1.959964) -> tuple[float, float]:
    """The Wilson 95 % interval for a proportion."""
    if n == 0:
        return 0.0, 1.0
    p = successes / n
    centre = p + z * z / (2 * n)
    spread = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n))
    denom = 1 + z * z / n
    return max(0.0, (centre - spread) / denom), min(1.0, (centre + spread) / denom)


def _describe(values: list[float], threshold: float | None) -> dict[str, Any]:
    n = len(values)
    if n == 0:
        return {"n": 0}
    ordered = sorted(values)
    mean = sum(ordered) / n
    sd = math.sqrt(sum((v - mean) ** 2 for v in ordered) / (n - 1)) if n > 1 else None
    out: dict[str, Any] = {"n": n, "mean": mean, "sd": sd, "min": ordered[0], "max": ordered[-1]}
    for p in PERCENTILES:
        out[f"p{p}"] = percentile(ordered, p)
    if threshold is not None:
        below = sum(v < threshold for v in ordered)
        low, high = wilson(below, n)
        out.update({"below": below, "p_below": below / n, "p_below_low": low,
                    "p_below_high": high})
    return out


def quantile_dots(values: Sequence[float], count: int = DOTS) -> dict[str, Any]:
    """Equal-share dots for a dot plot: every value when there are no more
    than `count`, otherwise the `count` mid-slice quantiles, so that each dot
    stands for the same share of the run-years."""
    ordered = sorted(values)
    n = len(ordered)
    if n == 0:
        return {}
    if n <= count:
        return {"dots": ordered, "dot_share": 1 / n}
    dots = [ordered[min(n - 1, int((k + 0.5) / count * n))] for k in range(count)]
    return {"dots": dots, "dot_share": 1 / count}


def summarise(per_run: list[list[float]], *, threshold: float | None = None,
              dropped: int = 0, spec: PeriodSpec | None = None) -> dict[str, Any]:
    """Pooled and by-index statistics of per-run period availabilities."""
    pooled = [v for run in per_run for v in run]
    longest = max((len(run) for run in per_run), default=0)
    by_index = []
    for i in range(longest):
        column = [run[i] for run in per_run if len(run) > i]
        by_index.append({"index": i + 1, **_describe(column, threshold)})
    worst = sorted(((v, r + 1, i + 1) for r, run in enumerate(per_run)
                    for i, v in enumerate(run)))[:10]
    return {
        "schema": "raptor-periods/1",
        "length": spec.length if spec else None,
        "offset": spec.offset if spec else 0.0,
        "partial": spec.partial if spec else "drop",
        "threshold": threshold,
        "runs": len(per_run),
        "periods_per_run": longest,
        "dropped_partial_periods": dropped,
        "pooled": {**_describe(pooled, threshold), **quantile_dots(pooled)},
        "by_index": by_index,
        "worst": [{"run": r, "index": i, "value": v} for v, r, i in worst],
    }


def from_capacity(capacity_runs: Sequence[dict[str, Any]], generated: float,
                  outages: dict[str, Any], spec: PeriodSpec,
                  threshold: float | None = None) -> dict[str, Any] | None:
    """Capacity-weighted availability period by period: one parsed capacity
    sink per run (`raptor.api.series.parse_capacity_runs`), cut on the same
    run windows as the outage record.  None when the runs do not match."""
    windows = outages.get("runs", [])
    if not capacity_runs or len(capacity_runs) != len(windows):
        return None
    per_run, dropped = [], 0
    for sink, run in zip(capacity_runs, windows, strict=True):
        points = list(zip(sink["t"], sink["flow"], strict=False))
        values, lost = period_capacity(points, generated, float(run["start"]),
                                       float(run["end"]), spec)
        per_run.append(values)
        dropped += lost
    return summarise(per_run, threshold=threshold, dropped=dropped, spec=spec)


def run_rows(availability: list[list[float]],
             capacity: list[list[float]] | None = None) -> list[dict[str, Any]]:
    """One row per (run, period): what issue #257 asks to export."""
    rows = []
    for r, values in enumerate(availability):
        for i, value in enumerate(values):
            row: dict[str, Any] = {"run": r + 1, "period": i + 1,
                                   "availability": value}
            if capacity is not None and r < len(capacity) and i < len(capacity[r]):
                row["capacity_weighted"] = capacity[r][i]
            rows.append(row)
    return rows


def from_outages(outages: dict[str, Any], spec: PeriodSpec,
                 threshold: float | None = None) -> dict[str, Any]:
    """Period statistics from a stored `outages` record
    (`attribution.outage_record`): `{"runs": [{"start", "end", "red": [s, e, s, e...]}]}`."""
    per_run, dropped = [], 0
    for run in outages.get("runs", []):
        flat = run.get("red", [])
        red = list(zip(flat[0::2], flat[1::2], strict=False))
        values, lost = period_availability(red, float(run["start"]), float(run["end"]), spec)
        per_run.append(values)
        dropped += lost
    return summarise(per_run, threshold=threshold, dropped=dropped, spec=spec)


def period_summary(outages: dict[str, Any], spec: PeriodSpec, *,
                   capacity_runs: Sequence[dict[str, Any]] | None = None,
                   generated: float = 0.0, threshold: float | None = None,
                   rows: bool = False, units: str = "") -> dict[str, Any]:
    """Everything the period view shows, from a run's outage record and
    (with capacity analysis) its parsed capacity sink.  The API, the GUI and
    the table export all build it here.

    ``capacity`` holds the capacity-weighted availability statistics when
    the sink matches the runs; `rows` adds one entry per (run, period)."""
    summary = from_outages(outages, spec, threshold=threshold)
    sinks = capacity_runs if generated > 0 else None
    if sinks:
        capacity = from_capacity(sinks, generated, outages, spec, threshold=threshold)
        if capacity is not None:
            summary["capacity"] = capacity
        else:
            sinks = None
    if rows:
        windows = outages.get("runs", [])
        availability = []
        for run in windows:
            flat = run.get("red", [])
            red = list(zip(flat[0::2], flat[1::2], strict=False))
            availability.append(period_availability(
                red, float(run["start"]), float(run["end"]), spec)[0])
        weighted = None
        if sinks:
            weighted = [period_capacity(list(zip(sink["t"], sink["flow"], strict=False)),
                                        generated, float(w["start"]), float(w["end"]),
                                        spec)[0]
                        for sink, w in zip(sinks, windows, strict=True)]
        summary["rows"] = run_rows(availability, weighted)
    summary["units"] = units
    summary["sentence"] = sentence(summary, units)
    return summary


def sentence(summary: dict[str, Any], units: str = "") -> str:
    """One sentence a report can quote."""
    pooled = summary["pooled"]
    if not pooled.get("n"):
        return "The runs are shorter than one period, so there is nothing to slice."
    length = summary["length"]
    period = "year" if units.lower().startswith("hour") and abs(length - HOURS_PER_YEAR) < 1 \
        else f"{length:,.6g}-{units or 'unit'} period"
    lead = (f"A typical {period} has availability {100 * pooled['p50']:.4g} %; "
            f"one in twenty is below {100 * pooled['p5']:.4g} % and the worst seen was "
            f"{100 * pooled['min']:.4g} %")
    threshold = summary.get("threshold")
    if threshold is not None:
        # "61 % of them ... (61 of 100)": no referent, the count said twice,
        # and the years of one run are not independent draws
        periods = f"{period}s" if period == "year" else "periods"
        lead += (f". {pooled['below']:,} of the {pooled['n']:,} {periods} "
                 f"({pct(pooled['p_below'])}) fall below {100 * threshold:.4g} %")
        if summary.get("periods_per_run", 0) > 1:
            lead += (f"; the {periods} of one run follow each other, so they "
                     "are not independent samples")
    capacity = (summary.get("capacity") or {}).get("pooled") or {}
    if capacity.get("n"):
        lead += (f". Weighted by capacity, a typical {period} delivers "
                 f"{100 * capacity['p50']:.4g} % of the flow, one in twenty less than "
                 f"{100 * capacity['p5']:.4g} %")
    return lead + "."
