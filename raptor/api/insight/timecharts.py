"""Qt-free charts of the system over simulated time.

These replace the old Graphs tab's hand-painted plots (Per Trial Ao,
Interval Ao, Multi-run Ao, Capacity (Flow), Multi-run Capacity) with
`ChartSpec`s, so the application, the report, the command line and the MCP
server draw the same figure from the same numbers.

All five have the kind ``timeline``: a time axis and a list of layers.  A
layer is either an ``area`` (``lo`` and ``hi`` per time) or a ``line``
(``y`` per time), with a ``tone`` the painters map to a theme colour
(``up``, ``degraded``, ``down``, ``accent``, ``muted``, ``ink``).  A step
function is written out as explicit corner points, so a painter only ever
joins points and never has to know which way a step goes.

Inputs are the parsed sink rows from `raptor.api.series`:
``parse_ao_runs`` (``t``, ``values``, ``green``, ``yellow``, ``red``,
``interval``) and ``parse_capacity_runs`` (``t``, ``flow``, ``capacity``).

Large runs: every builder caps what it hands a painter at `MAX_POINTS`
corners per layer.  The caption's numbers always come from the full data.
"""
from __future__ import annotations

import math
from bisect import bisect_right
from typing import Any

from raptor.api.insight.axes import format_value
from raptor.api.insight.charts import ChartSpec
from raptor.api.run_needs import message

#: Corners per layer a chart hands to a painter (a screen cannot show more).
MAX_POINTS = 1500
#: Grid points for the across-runs bands.
BAND_POINTS = 240

TIME_CHARTS = ("availability-through-run", "interval-availability",
               "availability-band", "capacity-through-run", "capacity-band")
#: The charts drawn for one run (they take a run number).
PER_RUN_CHARTS = ("availability-through-run", "interval-availability",
                  "capacity-through-run")


def _pct(value: float, digits: int = 4) -> str:
    return format_value(value, "percent", digits)


def _time(value: float, units: str) -> str:
    text = f"{value:,.0f}" if abs(value) >= 100 else f"{value:.3g}"
    return f"{text} {units}".strip()


def _stride(n: int, limit: int = MAX_POINTS) -> list[int]:
    """At most `limit` evenly spaced indices of `n`, first and last kept."""
    if n <= limit:
        return list(range(n))
    step = (n - 1) / (limit - 1)
    return sorted({0, n - 1, *(round(i * step) for i in range(limit))})


def _pick_run(runs: list[dict[str, Any]], run: int) -> tuple[int, dict[str, Any]]:
    if not runs:
        raise ValueError("this chart needs recorded plot data for at least one run")
    if not 1 <= run <= len(runs):
        raise ValueError(f"run {run} is not in this result (1 to {len(runs)})")
    return run, runs[run - 1]


def _floor_range(lows: list[float], floor: float | None, *, zoom: bool) -> list[float]:
    """The suggested y range for a fraction chart.

    A manual floor (> 0) always wins.  Otherwise a zooming chart starts at a
    round value below its lowest point, so a 99.9 % system is not a flat
    line along the top; a share-of-time chart starts at 0."""
    if floor is not None and 0 < floor < 1:
        return [floor, 1.0]
    if not zoom or not lows:
        return [0.0, 1.0]
    low = min(lows)
    if low <= 0.5:
        return [0.0, 1.0]
    gap = 1.0 - low
    step = 10 ** math.floor(math.log10(gap)) if gap > 0 else 0.001
    return [max(0.0, math.floor(low / step - 1) * step), 1.0]


def _spec(title: str, subtitle: str, units: str, layers: list[dict[str, Any]], *,
          caption: str, fmt: str, y_label: str, y_range: list[float] | None,
          provenance: dict[str, Any], target: float | None = None) -> ChartSpec:
    return ChartSpec(
        "timeline", title, subtitle,
        x_label=f"simulated time ({units})" if units else "simulated time",
        y_label=y_label, series=layers,
        marks=[{"kind": "target", "value": target}] if target is not None else [],
        caption=caption,
        provenance={"format": fmt, "time_units": units, "y_range": y_range,
                    **provenance})


# -- availability ----------------------------------------------------------

def _full_rows(run: dict[str, Any]) -> list[int]:
    return [i for i, (g, y, r) in enumerate(zip(run["green"], run["yellow"], run["red"]))
            if g is not None and y is not None and r is not None]


def availability_through_run(ao_runs: list[dict[str, Any]], run: int = 1, *,
                             units: str = "", target: float | None = None,
                             y_floor: float | None = None) -> ChartSpec:
    """How one run's time split between up, degraded and down, as it went.

    The bands are the cumulative shares of time so far (green fully up,
    yellow up with something failed, red down); their top edge is the
    run's availability to date."""
    number, data = _pick_run(ao_runs, run)
    rows = _full_rows(data)
    if not rows:
        raise ValueError("this run's availability plot has no green/yellow/red rows")
    keep = [rows[i] for i in _stride(len(rows))]
    t = [float(data["t"][i]) for i in keep]
    green = [float(data["green"][i]) for i in keep]
    degraded = [g + float(data["yellow"][i]) for g, i in zip(green, keep, strict=True)]
    total = [d + float(data["red"][i]) for d, i in zip(degraded, keep, strict=True)]
    last = rows[-1]
    g_end, y_end, r_end = (float(data[k][last]) for k in ("green", "yellow", "red"))
    ao_end = float(data["values"][last])
    caption = (f"Run {number}: up {_pct(ao_end)} of the time by "
               f"{_time(float(data['t'][last]), units)} (fully up {_pct(g_end)}, "
               f"degraded {_pct(y_end)}); down {_pct(r_end)}.")
    if target is not None:
        caption += (" It met the target." if ao_end >= target
                    else f" It missed the target {_pct(target)}.")
    layers: list[dict[str, Any]] = [
        {"name": "fully up", "role": "area", "tone": "up", "t": t,
         "lo": [0.0] * len(t), "hi": green},
        {"name": "degraded", "role": "area", "tone": "degraded", "t": t,
         "lo": green, "hi": degraded},
        {"name": "down", "role": "area", "tone": "down", "t": t,
         "lo": degraded, "hi": total},
        {"name": "availability so far", "role": "line", "tone": "ink", "t": t,
         "y": degraded, "width": 1.6},
    ]
    return _spec(f"Availability through run {number}",
                 "the share of time so far spent fully up, degraded and down",
                 units, layers, caption=caption, fmt="percent",
                 y_label="share of time so far",
                 y_range=_floor_range([], y_floor, zoom=False),
                 provenance={"run": number, "runs": len(ao_runs),
                             "samples": len(rows), "y_floor": y_floor,
                             "availability": ao_end},
                 target=target)


def interval_availability(ao_runs: list[dict[str, Any]], run: int = 1, *,
                          units: str = "", target: float | None = None,
                          y_floor: float | None = None) -> ChartSpec:
    """The availability seen in each recording interval of one run.

    Each step is one interval (the value the engine recorded at its end,
    for the time since the previous sample); the line is the run's
    availability to date.  More intervals than a painter can show are
    grouped, keeping the worst interval of each group, so a dip never
    disappears."""
    number, data = _pick_run(ao_runs, run)
    pairs = [(float(t), float(v)) for t, v in zip(data["t"], data["interval"])
             if v is not None]
    if not pairs:
        raise ValueError("this run has no interval samples (the availability "
                         "plot needs a recording interval)")
    starts = [0.0] + [t for t, _ in pairs[:-1]]
    spans = [(s, e, v) for s, (e, v) in zip(starts, pairs, strict=True)]
    worst = min(spans, key=lambda span: span[2])
    group = max(1, math.ceil(len(spans) / MAX_POINTS))
    xs: list[float] = []
    ys: list[float] = []
    for i in range(0, len(spans), group):
        chunk = spans[i:i + group]
        value = min(v for _s, _e, v in chunk)
        xs += [chunk[0][0], chunk[-1][1]]
        ys += [value, value]
    cumulative_t = [float(t) for t in data["t"]]
    keep = _stride(len(cumulative_t))
    line_t = [cumulative_t[i] for i in keep]
    line_y = [float(data["values"][i]) for i in keep]
    caption = (f"Run {number}: {len(spans):,} intervals; the worst was "
               f"{_pct(worst[2])}, ending at {_time(worst[1], units)}.")
    if target is not None:
        below = sum(1 for _s, _e, v in spans if v < target)
        caption += f" {below:,} of them were below the target {_pct(target)}."
    if group > 1:
        caption += f" Each step shows the worst of {group} intervals."
    layers = [
        {"name": "availability in the interval", "role": "line", "tone": "accent",
         "t": xs, "y": ys, "width": 1.6, "fill": True},
        {"name": "availability so far", "role": "line", "tone": "ink",
         "t": line_t, "y": line_y, "width": 1.2, "dash": True},
    ]
    return _spec(f"Availability interval by interval, run {number}",
                 "the share of each recording interval the system was up",
                 units, layers, caption=caption, fmt="percent",
                 y_label="availability in the interval",
                 y_range=_floor_range(ys + line_y, y_floor, zoom=True),
                 provenance={"run": number, "runs": len(ao_runs),
                             "intervals": len(spans), "grouped": group,
                             "y_floor": y_floor, "worst": worst[2]},
                 target=target)


def _quantile(ordered: list[float], share: float) -> float:
    if len(ordered) == 1:
        return ordered[0]
    pos = share * (len(ordered) - 1)
    lo = math.floor(pos)
    hi = min(lo + 1, len(ordered) - 1)
    return ordered[lo] + (pos - lo) * (ordered[hi] - ordered[lo])


def _sample(ts: list[float], vs: list[float], grid: list[float], *, step: bool) -> list[float]:
    """Values of one run at each grid time.

    `step` holds the last value (capacity change points); otherwise the
    value is interpolated (cumulative availability).  Before the first row
    the first value holds; after the last, the last.  One binary search per
    grid time (in C), so a run with many rows costs no more than one with few."""
    out: list[float] = []
    n = len(ts)
    for x in grid:
        j = bisect_right(ts, x) - 1          # last row at or before x
        if j < 0:
            out.append(vs[0])
        elif step or j + 1 >= n:
            out.append(vs[j])
        else:
            span = ts[j + 1] - ts[j]
            frac = 0.0 if span <= 0 else (x - ts[j]) / span
            out.append(vs[j] + frac * (vs[j + 1] - vs[j]))
    return out


def _bands(columns: list[list[float]]) -> dict[str, list[float]]:
    """Across-run mean, min, max and the 10th/90th percentiles per time."""
    out: dict[str, list[float]] = {"mean": [], "min": [], "max": [], "p10": [], "p90": []}
    for values in columns:
        ordered = sorted(values)
        out["mean"].append(sum(ordered) / len(ordered))
        out["min"].append(ordered[0])
        out["max"].append(ordered[-1])
        out["p10"].append(_quantile(ordered, 0.1))
        out["p90"].append(_quantile(ordered, 0.9))
    return out


def _band_layers(grid: list[float], bands: dict[str, list[float]], *,
                 what: str) -> list[dict[str, Any]]:
    return [
        {"name": "every run (lowest to highest)", "role": "area", "tone": "accent",
         "alpha": 0.14, "read": "range", "t": grid, "lo": bands["min"], "hi": bands["max"]},
        {"name": "8 runs in 10", "role": "area", "tone": "accent", "alpha": 0.32,
         "read": "range",
         "t": grid, "lo": bands["p10"], "hi": bands["p90"]},
        {"name": f"mean {what}", "role": "line", "tone": "accent", "t": grid,
         "y": bands["mean"], "width": 2.0},
    ]


def availability_band(ao_runs: list[dict[str, Any]], *, units: str = "",
                      target: float | None = None,
                      y_floor: float | None = None,
                      points: int = BAND_POINTS) -> ChartSpec:
    """Every run's availability to date, as bands across the runs.

    The darker band holds 8 runs in 10, the lighter one every run, and the
    line is the mean.  The bands narrow as the runs settle: where they stop
    narrowing, more simulated time no longer changes the answer."""
    usable = [r for r in ao_runs if r.get("t")]
    if len(usable) < 2:
        raise ValueError("this chart needs the availability plot of at least 2 runs")
    horizon = min(float(r["t"][-1]) for r in usable)
    count = max(2, points)
    grid = [horizon * i / (count - 1) for i in range(count)]
    samples = [_sample([float(x) for x in r["t"]], [float(v) for v in r["values"]],
                       grid, step=False) for r in usable]
    bands = _bands([list(col) for col in zip(*samples, strict=True)])
    early = max(1, count // 10)
    width_early = bands["p90"][early] - bands["p10"][early]
    width_end = bands["p90"][-1] - bands["p10"][-1]
    caption = (f"{len(usable):,} runs end with 8 in 10 between {_pct(bands['p10'][-1])} "
               f"and {_pct(bands['p90'][-1])} (mean {_pct(bands['mean'][-1])}); the "
               f"band is {_pct(width_early, 2)} wide at a tenth of the time and "
               f"{_pct(width_end, 2)} at the end.")
    if target is not None:
        met = sum(1 for column in samples if column[-1] >= target)
        caption += f" {met:,} runs ended at or above the target."
    return _spec("Availability across all runs",
                 "each run's availability so far: where the runs agree",
                 units, _band_layers(grid, bands, what="availability"),
                 caption=caption, fmt="percent", y_label="availability so far",
                 y_range=_floor_range(bands["min"][early:], y_floor, zoom=True),
                 provenance={"runs": len(usable), "horizon": horizon,
                             "y_floor": y_floor,
                             "final": {k: v[-1] for k, v in bands.items()}},
                 target=target)


# -- capacity --------------------------------------------------------------

def _delivered(t: list[float], flow: list[float], full: float) -> tuple[float, float, float]:
    """(flow-time, full-capacity time, time below full) for one run's change points."""
    flow_time = short = 0.0
    for i in range(len(t) - 1):
        span = t[i + 1] - t[i]
        flow_time += flow[i] * span
        if flow[i] < full - 1e-9:
            short += span
    return flow_time, full * (t[-1] - t[0]), short


#: Raptor's capacity plot value for "no limit" (999,999,999), with a margin
UNLIMITED = 999_999_999.0 * 0.999


def _full(runs: list[dict[str, Any]], generated: float | None) -> tuple[float, str]:
    """The flow a fully working system delivers, and what to call it.

    The model's flow generated (the same reference lost capacity uses);
    without the model, the highest capacity any run recorded."""
    if generated is not None and generated > 0:
        return float(generated), "flow generated"
    seen = max((float(c) for r in runs for c in r["capacity"]
                if float(c) < UNLIMITED), default=0.0)
    if seen <= 0:
        # no limit was ever recorded: the most the system ever delivered
        seen = max((float(f) for r in runs for f in r["flow"]), default=0.0)
    return seen, "full capacity (highest recorded)"


def _until(data: dict[str, Any], end: float | None) -> dict[str, Any]:
    """A capacity run held at its last value to the end of the run.

    The capacity plot records a row only when the flow changes, so the last
    row is the last change, not the end of the run."""
    if end is None or not data["t"] or end <= float(data["t"][-1]):
        return data
    return {"t": [*data["t"], end], "flow": [*data["flow"], data["flow"][-1]],
            "capacity": [*data["capacity"], data["capacity"][-1]]}


def _end(ends: list[float | None] | None, index: int) -> float | None:
    if not ends or index >= len(ends):
        return None
    value = ends[index]
    return float(value) if value is not None else None


def capacity_through_run(cap_runs: list[dict[str, Any]], run: int = 1, *,
                         units: str = "", generated: float | None = None,
                         ends: list[float | None] | None = None) -> ChartSpec:
    """One run's flow against what a fully working system delivers.

    The filled area is the flow delivered; the shaded gap above it, up to
    the dashed full-capacity line, is the capacity lost (the same loss the
    lost-capacity charts split by element).  When the system could carry
    more than it delivered at some moment (the capacity column above the
    flow), that capacity is drawn as its own line."""
    number, data = _pick_run(cap_runs, run)
    data = _until(data, _end(ends, number - 1))
    t = [float(x) for x in data["t"]]
    flow = [float(x) for x in data["flow"]]
    cap = [float(x) for x in data["capacity"]]
    if not t:
        raise ValueError("this run's capacity plot is empty")
    full, full_name = _full([data], generated)
    flow_time, full_time, short = _delivered(t, flow, full)
    duration = t[-1] - t[0]
    # corners of the step: each value holds until the next change
    xs, fs, cs = [], [], []
    for i in range(len(t)):
        stop = t[i + 1] if i + 1 < len(t) else t[i]
        xs += [t[i], stop]
        fs += [flow[i], flow[i]]
        cs += [cap[i], cap[i]]
    keep = _stride(len(xs))
    xs, fs, cs = [xs[i] for i in keep], [fs[i] for i in keep], [cs[i] for i in keep]
    if full_time > 0 and duration > 0:
        caption = (f"Run {number} delivered {_pct(flow_time / full_time)} of its "
                   f"{full_name}; it ran below full {_pct(short / duration, 3)} of "
                   f"the time, lowest flow {min(flow):,.4g} of {full:,.4g}.")
    else:
        caption = f"Run {number}: lowest flow {min(flow):,.4g}."
    layers: list[dict[str, Any]] = [
        {"name": "capacity lost", "role": "area", "tone": "down", "alpha": 0.28,
         "t": xs, "lo": fs, "hi": [full] * len(xs)},
        {"name": "flow delivered", "role": "area", "tone": "accent", "alpha": 0.30,
         "read": "none",
         "t": xs, "lo": [0.0] * len(xs), "hi": fs},
        {"name": "flow", "role": "line", "tone": "accent", "t": xs, "y": fs, "width": 1.8},
    ]
    # Raptor writes 999,999,999 for "no limit": with no link or block
    # capacities set the system can carry anything while it is up, which is
    # not a number to draw (it squashed the flow to a line along the floor)
    unlimited = [c >= UNLIMITED for c in cap]
    finite = [c for c, u in zip(cs, (c >= UNLIMITED for c in cs), strict=True) if not u]
    top = max(finite + fs + [full]) or 1.0
    if any(unlimited):
        caption += (" No capacity limit applies while the system is up"
                    if all(u or c <= f + 1e-9 for u, c, f in
                           zip(unlimited, cap, flow, strict=True))
                    else " Where no capacity limit applies the capacity line runs "
                         "along the top edge") + " (Raptor records it as unlimited)."
    if any(not u and c > f + 1e-9 for u, c, f in zip(unlimited, cap, flow, strict=True)):
        layers.append({"name": "capacity available", "role": "line", "tone": "muted",
                       "t": xs, "y": [min(c, top) for c in cs], "width": 1.2})
    layers.append({"name": full_name, "role": "line", "tone": "ink",
                   "t": [xs[0], xs[-1]], "y": [full, full], "width": 1.2, "dash": True})
    return _spec(f"Capacity through run {number}",
                 "flow delivered against a fully working system", units, layers,
                 caption=caption, fmt="", y_label="flow",
                 y_range=[0.0, top],
                 provenance={"run": number, "runs": len(cap_runs),
                             "changes": len(t), "full": full,
                             "delivered": flow_time / full_time if full_time else None})


def capacity_band(cap_runs: list[dict[str, Any]], *, units: str = "",
                  generated: float | None = None,
                  ends: list[float | None] | None = None,
                  points: int = BAND_POINTS) -> ChartSpec:
    """Every run's flow at each time, as bands across the runs."""
    usable = [_until(r, _end(ends, i)) for i, r in enumerate(cap_runs) if r.get("t")]
    if len(usable) < 2:
        raise ValueError("this chart needs the capacity plot of at least 2 runs")
    horizon = max(float(r["t"][-1]) for r in usable)
    count = max(2, points)
    grid = [horizon * i / (count - 1) for i in range(count)]
    samples = [_sample([float(x) for x in r["t"]], [float(v) for v in r["flow"]],
                       grid, step=True) for r in usable]
    bands = _bands([list(col) for col in zip(*samples, strict=True)])
    full, full_name = _full(usable, generated)
    delivered = [_delivered([float(x) for x in r["t"]], [float(v) for v in r["flow"]],
                            full) for r in usable]
    flow_time = sum(d[0] for d in delivered)
    full_time = sum(d[1] for d in delivered)
    caption = f"{len(usable):,} runs"
    if full_time > 0:
        caption += f" delivered {_pct(flow_time / full_time)} of their {full_name}"
    caption += (f"; the mean flow fell as low as {min(bands['mean']):,.4g} "
                f"of {full:,.4g}.")
    layers = _band_layers(grid, bands, what="flow")
    layers.append({"name": full_name, "role": "line", "tone": "ink",
                   "t": [0.0, horizon], "y": [full, full], "width": 1.2,
                   "dash": True})
    return _spec("Capacity across all runs",
                 "flow at each moment: where the runs agree", units, layers,
                 caption=caption, fmt="", y_label="flow",
                 y_range=[0.0, max(full, max(bands["max"])) or 1.0],
                 provenance={"runs": len(usable), "horizon": horizon, "full": full,
                             "delivered": flow_time / full_time if full_time else None})


def time_chart(name: str, *, ao_runs: list[dict[str, Any]] | None = None,
               cap_runs: list[dict[str, Any]] | None = None, run: int = 1,
               units: str = "", target: float | None = None,
               y_floor: float | None = None,
               generated: float | None = None,
               ends: list[float | None] | None = None) -> ChartSpec:
    """One of `TIME_CHARTS` by name (ValueError naming what is missing)."""
    if name.startswith("availability") or name == "interval-availability":
        if not ao_runs:
            raise ValueError(message("availability_plot"))
        if name == "availability-through-run":
            return availability_through_run(ao_runs, run, units=units, target=target,
                                            y_floor=y_floor)
        if name == "interval-availability":
            return interval_availability(ao_runs, run, units=units, target=target,
                                         y_floor=y_floor)
        return availability_band(ao_runs, units=units, target=target, y_floor=y_floor)
    if not cap_runs:
        raise ValueError(message("capacity_plot"))
    if name == "capacity-through-run":
        return capacity_through_run(cap_runs, run, units=units, generated=generated,
                                    ends=ends)
    return capacity_band(cap_runs, units=units, generated=generated, ends=ends)


def stored_inputs(run_dir: Any) -> dict[str, Any]:
    """`chart_named` inputs for the charts over time, from a stored run.

    ``ao_runs`` and ``cap_runs`` are the parsed plot files (empty when the
    run did not record them) and ``units`` the model's time unit."""
    import json
    from pathlib import Path

    from raptor.api.series import parse_ao_runs, parse_capacity_runs
    root = Path(run_dir)
    ao_path, cap_path = root / "availability_plot.txt", root / "capacity.txt"
    units = ""
    summary = root / "summary.json"
    if summary.is_file():
        try:
            units = str(json.loads(summary.read_text()).get("units") or "")
        except (ValueError, OSError):
            units = ""
    return {"ao_runs": parse_ao_runs(ao_path.read_text()) if ao_path.is_file() else [],
            "cap_runs": (parse_capacity_runs(cap_path.read_text())
                         if cap_path.is_file() else []),
            "units": units}
