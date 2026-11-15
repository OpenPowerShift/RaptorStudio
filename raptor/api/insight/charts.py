"""Qt-free data for the result charts (WP-2.5).

Each function returns a `ChartSpec`: a kind, a title and subtitle, axis
labels, series, marks, annotations, a one-sentence caption the user can
quote, and provenance.  The GUI paints it with QPainter
(`raptor.gui.ui.chart_widget`), the report renders the same spec to SVG
(`raptor.render.charts_svg`), and the command line prints it.

A chart never computes a measure differently from the tables: every number
comes from `per_run` rows, an `Attribution` dict or the measure registry.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any

from raptor.api.insight.attribution import CAUSES, CAUSE_LABELS
from raptor.api.insight.dots import quantile_dots
from raptor.api.insight import measures
from raptor.api.insight.axes import FAN_LINE_MIN, pct
from raptor.api.run_needs import message


def _needs(key: str) -> str:
    """A caption that says which option to turn on, as a sentence."""
    text = message(key)
    return f"{text[0].upper()}{text[1:]}."


@dataclass
class ChartSpec:
    kind: str
    title: str
    subtitle: str = ""
    x_label: str = ""
    y_label: str = ""
    series: list[dict[str, Any]] = field(default_factory=list)
    marks: list[dict[str, Any]] = field(default_factory=list)
    annotations: list[dict[str, Any]] = field(default_factory=list)
    caption: str = ""
    provenance: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def _mean(values: list[float]) -> float | None:
    return sum(values) / len(values) if values else None


def text_lines(chart: ChartSpec, top: int = 10) -> list[str]:
    """The chart as text: its title and caption, then the numbers it draws.
    A timeline gives each layer's value at the end; any other chart its
    first `top` series rows that carry a value."""
    from raptor.api.insight.axes import format_value
    fmt = str(chart.provenance.get("format") or "")

    def shown(value: float) -> str:            # a timeline layer's end value
        return format_value(value, fmt, 5) if fmt else f"{value:,.4g}"

    def row_value(value: float) -> str:
        return f"{value:,.2f}" if fmt == "money" else format_value(value, fmt, 6)

    lines = [f"{chart.title} — {chart.caption}"]
    if chart.kind == "timeline":
        for layer in chart.series:
            if layer.get("read") == "none" or not layer.get("t"):
                continue
            if layer.get("role") != "area":
                text = shown(float(layer["y"][-1]))
            elif layer.get("read") == "range":
                text = f"{shown(float(layer['lo'][-1]))} to {shown(float(layer['hi'][-1]))}"
            else:
                text = shown(float(layer["hi"][-1]) - float(layer["lo"][-1]))
            lines.append(f"  {layer.get('name', '')}: {text} at the end")
        return lines
    for row in chart.series[:top]:
        name = row.get("name") or row.get("label") or ""
        value = row.get("value", row.get("count", row.get("mean")))
        if isinstance(value, (int, float)) and not isinstance(value, bool):
            lines.append(f"  {name}: {row_value(float(value))}")
    return lines


def _values(per_run: list[dict], field_name: str) -> list[float]:
    return [float(row[field_name]) for row in per_run
            if isinstance(row.get(field_name), (int, float))
            and not isinstance(row.get(field_name), bool)]


def _quartile(values: list[float], q: float) -> float:
    """The `q` quantile of `values` (sorted, not empty), interpolated."""
    pos = q * (len(values) - 1)
    low = int(pos)
    high = min(low + 1, len(values) - 1)
    return values[low] + (values[high] - values[low]) * (pos - low)


def quartile_marks(values: list[float]) -> list[dict[str, Any]]:
    """U16: Q1, median and Q3 of the runs, as quiet reference lines."""
    if len(values) < 4:
        return []
    ordered = sorted(values)
    marks: list[dict[str, Any]] = []
    for label, q in (("Q1", 0.25), ("median", 0.5), ("Q3", 0.75)):
        value = _quartile(ordered, q)
        if marks and marks[-1]["value"] == value:     # one line, one label
            marks[-1]["label"] += f", {label}"
        else:
            marks.append({"kind": "quartile", "label": label, "value": value})
    return marks


def trial_outcome(per_run: list[dict], field_name: str = "availability", *,
                  label: str = "Availability", target: float | None = None,
                  units: str = "") -> ChartSpec:
    """C1: one dot per quantile slice of the per-run values."""
    values = _values(per_run, field_name)
    dots = quantile_dots(values)
    met = sum(1 for v in values if target is not None and v >= target)
    caption = f"{label}: {len(values)} runs"
    mean = _mean(values)
    if mean is not None:
        from raptor.api.insight.axes import format_value
        caption += f", mean {format_value(mean, value_format(field_name, units), 6)}"
    quartiles = quartile_marks(values)
    if len(quartiles) > 1:
        from raptor.api.insight.axes import format_value
        fmt = value_format(field_name, units)
        caption += (f"; the middle half of the runs lie between "
                    f"{format_value(quartiles[0]['value'], fmt, 5)} and "
                    f"{format_value(quartiles[-1]['value'], fmt, 5)} (Q1 to Q3)")
    if target is not None:
        caption += (f"; {met} of {len(values)} "
                    f"({pct(met / len(values))}) met the target.")
    else:
        caption += "."
    return ChartSpec(
        "dot_plot", f"Run-to-run {label.lower()}",
        "one dot per quantile slice of the runs", label, "runs",
        series=[{"name": "runs", "dots": dots, "values": values}],
        marks=([{"kind": "target", "value": target}] if target is not None else [])
        + quartiles,
        caption=caption,
        provenance={"runs": len(values), "units": units,
                    "format": value_format(field_name, units),
                    "measure": field_name, "met": met, "target": target})


#: Per-run fields that are fractions, printed as percentages.
_FRACTIONS = {"availability", "reliability", "dependability",
              "mission_availability", "mission_reliability", "ao", "ai", "aa",
              "green", "yellow", "red"}


def value_format(field_name: str, units: str = "") -> str:
    """How a chart should print this field: ``percent``, ``money`` or ``""``.

    A fraction is a percentage whatever `units` says: callers pass the
    model's time unit there, which is not the unit of a fraction."""
    if field_name in _FRACTIONS:
        return "percent"
    if units in ("cost", "money"):
        return "money"
    return ""


def exceedance_points(values: list[float], *, higher_is_better: bool = True
                      ) -> list[dict[str, float]]:
    """One point per distinct value: the share of runs that reached it.

    With `higher_is_better` a run reaches `v` when its value is at least
    `v`; otherwise when it is at most `v` (downtime, cost).  The points run
    from the share 1/n (the best run) to 1 (every run)."""
    ordered = sorted(values, reverse=higher_is_better)
    n = len(ordered)
    points: list[dict[str, float]] = []
    for i, value in enumerate(ordered):
        share = (i + 1) / n
        if points and points[-1]["value"] == value:
            points[-1]["share"] = share
        else:
            points.append({"value": value, "share": share})
    return points


def _reached(points: list[dict[str, float]], share: float) -> float:
    """The value at least `share` of the runs reached (`points` not empty)."""
    for point in points:
        if point["share"] >= share - 1e-12:
            return point["value"]
    return points[-1]["value"]


def ecdf(per_run: list[dict], field_name: str = "availability", *,
         label: str = "Availability", target: float | None = None,
         units: str = "", higher_is_better: bool = True) -> ChartSpec:
    """C1b: the chance a run reaches each value (an exceedance curve).

    Read across: pick a share of runs on the horizontal axis and the curve
    gives the value that many runs reached.  The runs are the dot plot's."""
    values = _values(per_run, field_name)
    points = exceedance_points(values, higher_is_better=higher_is_better)
    fmt = value_format(field_name, units)
    word = "at least" if higher_is_better else "at most"

    def shown(value: float) -> str:
        from raptor.api.insight.axes import format_value
        return format_value(value, fmt, 5)

    if points:
        nine = _reached(points, 0.9)
        half = _reached(points, 0.5)
        caption = (f"9 runs in 10 reached {word} {shown(nine)}; half reached "
                   f"{word} {shown(half)}.")
        if target is not None:
            met = sum(1 for v in values
                      if (v >= target if higher_is_better else v <= target))
            caption += (f" {pct(met / len(values))} of runs reached the "
                        f"target {shown(target)}.")
    else:
        caption = "No runs."
    return ChartSpec(
        "exceedance", f"Chance of reaching {label.lower()}",
        "read across from a share of runs to the value they reached",
        "share of runs", label,
        series=[{"name": label, "points": points}],
        marks=([{"kind": "target", "value": target}] if target is not None else [])
        # U16: where 1 run in 4, half and 3 in 4 sit on the curve
        + ([{"kind": "quartile", "label": label_q, "share": share,
             "value": _reached(points, share)}
            for label_q, share in (("1 in 4", 0.25), ("half", 0.5), ("3 in 4", 0.75))]
           if len(values) >= 4 else []),
        caption=caption,
        provenance={"runs": len(values), "units": units, "format": fmt,
                    "measure": field_name, "reached": word,
                    "higher_is_better": higher_is_better})


def downtime_pareto(attribution: dict[str, Any], top: int = 10) -> ChartSpec:
    """C2: who causes the downtime, stacked by what the time was spent on."""
    rows = sorted((r for r in attribution.get("elements") or []
                   if (r.get("dtci", {}).get("mean") or 0) > 0),
                  key=lambda r: -(r["dtci"]["mean"] or 0))[:top]
    return ChartSpec(
        "pareto", "Who causes the downtime",
        "stacked by what the time was spent on", "element",
        "share of system downtime",
        series=[{"name": r["name"], "value": r["dtci"]["mean"] or 0.0,
                 "sem": r["dtci"].get("sem"),
                 "causes": r.get("causes") or {},
                 "outages": r.get("deci", {}).get("mean") or 0.0,
                 "red_time": r.get("red_time", {}).get("mean") or 0.0}
                for r in rows],
        caption=(f"{rows[0]['name']} causes the largest share "
                 f"({pct(rows[0]['dtci']['mean'] or 0)})."
                 if rows else "No system outage."),
        provenance={"runs": attribution.get("runs", 0), "format": "percent"})


def downtime_waterfall(attribution: dict[str, Any],
                       operational: float | None = None) -> ChartSpec:
    """C3: from 100 % availability to the achieved one, one step per cause."""
    causes = attribution.get("causes") or {}
    loss = 1.0 - operational if operational is not None else None
    steps: list[dict[str, Any]] = []
    running = 1.0
    for cause in CAUSES:
        share = float(causes.get(cause, 0.0))
        if share <= 0:
            continue
        drop = share * loss if loss is not None else share
        steps.append({"name": CAUSE_LABELS.get(cause, cause), "cause": cause,
                      "value": drop, "from": running, "to": running - drop,
                      "share": share})
        running -= drop
    if not steps:
        caption = "No loss to attribute."
    elif loss is None:
        caption = ("Each step's share of the loss; pass the run's operational "
                   "availability for steps in points.")
    else:
        largest = max(steps, key=lambda s: s["value"])
        caption = (f"The largest single loss is "
                   f"{largest['name'].lower()} "
                   f"({100 * largest['value']:.3g} points).")
    return ChartSpec(
        "waterfall", "From full availability to the achieved one",
        "each step is a cause's share of the loss", "cause",
        "share of loss", series=steps,
        marks=[{"kind": "end", "value": running}] if steps else [],
        caption=caption,
        provenance={"runs": attribution.get("runs", 0), "format": "percent",
                    "operational": operational})


def availability_ladder(ladder: dict[str, Any]) -> ChartSpec:
    """C4: inherent, achieved and operational availability with their gaps."""
    order = (("inherent", "Inherent (repair only)"),
             ("achieved", "Achieved (+ planned maintenance)"),
             ("operational", "Operational (+ logistics, spares, crews)"))
    series = []
    for key, label in order:
        entry = ladder.get(key) or {}
        series.append({"name": label, "key": key, "value": entry.get("value"),
                       "source": entry.get("source", "")})
    present = [s for s in series if s["value"] is not None]
    return ChartSpec(
        "ladder", "What each kind of availability keeps",
        "the gap between rungs is what the next cause takes away",
        "availability", "fraction", series=present,
        caption="; ".join(f"{s['name']} {100 * s['value']:.6g} %"
                          for s in present) or "No availability reported.",
        provenance={"runs": ladder.get("runs"), "format": "percent"})


def outage_histogram(attribution: dict[str, Any]) -> ChartSpec:
    """C6: many short outages or a few long ones (log-x)."""
    durations = attribution.get("outage_durations") or {}
    hist = durations.get("hist") or []
    p50, p90 = durations.get("p50"), durations.get("p90")
    return ChartSpec(
        "histogram", "How long is an outage?",
        "log scale: many short outages or a few long ones", "duration",
        "outages", series=hist,
        marks=[{"kind": "quantile", "label": "P50", "value": p50},
               {"kind": "quantile", "label": "P90", "value": p90}],
        caption=(f"Half of the outages end by {p50:,.4g}; one in ten lasts "
                 f"over {p90:,.4g}." if p50 is not None and p90 is not None
                 else "No outage durations recorded."),
        provenance={"runs": attribution.get("runs", 0),
                    "count": durations.get("count", 0)})


def frequency_duration(attribution: dict[str, Any], top: int = 20) -> ChartSpec:
    """C7: reliability against maintainability, bubble area = DTCI.

    x is how many system outages the element starts a run and y how long
    they last on average, in the model's time unit, so both axes read as
    quantities. An attribution without per-element counts (an older stored
    run) falls back to the two shares, y then being relative (1 = an
    average outage)."""
    rows = sorted((r for r in attribution.get("elements") or []
                   if (r.get("deci", {}).get("mean") or 0) > 0),
                  key=lambda r: -(r.get("dtci", {}).get("mean") or 0))[:top]
    units = str(attribution.get("units") or "")
    absolute = bool(rows) and all(
        (r.get("outages") or {}).get("mean") and (r.get("red_time") or {}).get("mean")
        is not None for r in rows)
    if absolute:
        points = [{"name": r["name"],
                   "x": r["outages"]["mean"],
                   "y": r["red_time"]["mean"] / r["outages"]["mean"],
                   "area": r["dtci"]["mean"] or 0.0} for r in rows]
        subtitle = ("x = system outages it starts a run, y = how long they last, "
                    "area = share of downtime")
        x_label, y_label = "outages started a run", (
            f"mean outage length ({units})" if units else "mean outage length")
    else:
        points = [{"name": r["name"],
                   "x": r["deci"]["mean"] or 0.0,
                   "y": ((r["dtci"]["mean"] or 0.0)
                         / (r["deci"]["mean"] or 1.0)),
                   "area": r["dtci"]["mean"] or 0.0} for r in rows]
        subtitle = ("x = share of outages started, y = outage length relative to "
                    "the average (1 = average), area = share of downtime")
        x_label, y_label = "share of outages started", "relative outage length"
    return ChartSpec(
        "bubble", "Frequency against duration", subtitle, x_label, y_label,
        series=points,
        # frequent short outages sit low on the right; rare long ones high on
        # the left (the caption had the two levers the other way round)
        caption=("Low right, frequent short outages: improve reliability. "
                 "High left, rare long ones: repair and logistics."
                 if points else "No element started an outage."),
        provenance={"runs": attribution.get("runs", 0), "absolute": absolute,
                    # outages a run are a count, not a share: drawn as a
                    # percentage, 2.8 a run read "280 %"
                    "x_format": "" if absolute else "percent",
                    "units": units})


def bubble_tip(spec: ChartSpec, point: dict[str, Any]) -> str:
    """The hover text of one frequency-against-duration bubble."""
    from raptor.api.insight.axes import pct
    share = pct(float(point.get("area") or 0))
    if spec.provenance.get("absolute"):
        units = spec.provenance.get("units") or ""
        return (f"{point['name']}: {float(point['x']):.3g} outages a run, "
                f"{float(point['y']):.3g}{' ' + units if units else ''} each on "
                f"average; {share} of downtime")
    return f"{point['name']}: {pct(float(point['x']))} of outages, {share} of downtime"


def importance_quadrant(importance: dict[str, Any],
                        metric: str = "Availability") -> ChartSpec:
    """C8: what losing an element costs (RAW) against what fixing it buys."""
    entry = (importance.get("metrics") or {}).get(metric) or {}
    points = [{"name": r.get("block"), "x": r.get("raw"), "y": r.get("rrw")}
              for r in entry.get("rows") or []
              if r.get("raw") is not None and r.get("rrw") is not None]
    return ChartSpec(
        "quadrant", f"Importance on {metric.lower()}",
        "x = RAW (what losing it costs), y = RRW (what fixing it buys)",
        "RAW", "RRW", series=points,
        marks=[{"kind": "cross", "x": 1.0, "y": 1.0}],
        caption=("Points far above and right of 1 are both critical and worth "
                 "fixing." if points else "No block has both a RAW and an RRW."),
        provenance={"metric": metric})


def convergence(per_run: list[dict], field_name: str = "availability",
                target: float | None = None) -> ChartSpec:
    """C11: has the running mean settled; is the run long enough?"""
    values = _values(per_run, field_name)
    points: list[dict[str, Any]] = []
    # Welford's running mean and variance: one pass, not one per run
    mean = m2 = 0.0
    for i, value in enumerate(values, start=1):
        delta = value - mean
        mean += delta / i
        m2 += delta * (value - mean)
        sem = (m2 / (i - 1) / i) ** 0.5 if i > 1 else None
        points.append({"n": i, "mean": mean, "sem": sem})
    last = points[-1] if points else None
    fmt = value_format(field_name)

    def shown(value: float, digits: int) -> str:
        from raptor.api.insight.axes import format_value
        return format_value(value, fmt, digits)

    return ChartSpec(
        "convergence", "Has the run settled?",
        "the band is +/- 2 standard errors; it should stop moving",
        "runs", field_name, series=points,
        marks=[{"kind": "target", "value": target}] if target is not None else [],
        caption=(f"After {len(values):,} runs the mean is "
                 f"{shown(float(last['mean']), 6)}, give or take "
                 f"{shown(2 * float(last['sem']), 2)} (2 standard errors)."
                 if last is not None and last["sem"] is not None
                 else "Not enough runs to estimate the error."),
        provenance={"runs": len(values), "measure": field_name,
                    "format": fmt})


_COST_PARTS = ("purchase", "operating", "sparing", "disposal")


def element_costs(report_data: list[Any]) -> list[dict[str, Any]]:
    """Mean cost per run of each block and spare pool, split into
    purchase, operating, sparing and disposal (a pool's cost is all
    `spare_pool`), from the engine's per-run cost reports.  Runs without
    cost analysis are skipped; [] when none has it."""
    totals: dict[str, dict[str, float]] = {}
    runs = 0
    for report in report_data:
        cost = getattr(report, "cost", None)
        if cost is None:
            continue
        runs += 1
        for block_id, row in (getattr(cost, "blocks", {}) or {}).items():
            parts = totals.setdefault(str(getattr(row, "name", block_id)), {})
            split = {k: float(getattr(row, k, 0.0) or 0.0) for k in _COST_PARTS}
            if not any(split.values()):          # only a total is known
                split["other"] = float(getattr(row, "total", 0.0) or 0.0)
            for key, value in split.items():
                parts[key] = parts.get(key, 0.0) + value
        for pool_id, row in (getattr(cost, "spare_pools", {}) or {}).items():
            parts = totals.setdefault(str(getattr(row, "name", pool_id)), {})
            parts["spare_pool"] = (parts.get("spare_pool", 0.0)
                                   + float(getattr(row, "total", 0.0) or 0.0))
    return [{"name": name, **{k: v / runs for k, v in parts.items()},
             "total": sum(parts.values()) / runs}
            for name, parts in totals.items()] if runs else []


def _named_rows(table: Any, columns: dict[str, str]) -> dict[str, dict[str, float]]:
    """{row name: {key: value}} for the columns present (`columns` maps a
    key to its lower-case header); the report's own "Total" row is left out."""
    names = [str(h).strip().lower() for h in table.headers]
    found = {key: names.index(head) for key, head in columns.items() if head in names}
    return {str(row[0]): {key: _float(row[i]) for key, i in found.items()}
            for row in table.rows if str(row[0]).lower() != "total"}


def element_costs_from_tables(tables: list[Any]) -> list[dict[str, Any]]:
    """The same rows as `element_costs` from a stored run's Final Results
    cost tables (`raptor.api.tables.result_tables`), which already hold the
    mean per run.  This is what the command line and MCP use."""
    by_key = {t.key: t for t in tables}
    out: dict[str, dict[str, Any]] = {}
    life = by_key.get("results.cost_life_cycle_cost")
    if life is not None:
        for name, parts in _named_rows(life, {k: k for k in _COST_PARTS}).items():
            out.setdefault(name, {"name": name}).update(parts)
    pools = next((t for k, t in by_key.items() if "sparepool" in k.replace("_", "")), None)
    if pools is not None:
        for name, parts in _named_rows(pools, {"spare_pool": "total"}).items():
            out.setdefault(name, {"name": name}).update(parts)
    for item in out.values():
        item["total"] = sum(float(item.get(k) or 0.0)
                            for k in (*_COST_PARTS, "spare_pool"))
    return list(out.values())


def stored_element_costs(workspace: Any, run_id: str) -> list[dict[str, Any]]:
    """`element_costs_from_tables` for a stored run ([] without cost)."""
    from raptor.api.run_tables import bundle_from_run
    from raptor.api.tables import result_tables
    return element_costs_from_tables(result_tables(bundle_from_run(workspace, run_id)))


COST_CHARTS = ("cost-pareto", "cost-treemap")


def stored_importance(workspace: Any, run_id: str) -> dict[str, Any] | None:
    """The importance measures a stored run made, or None (its summary
    keeps them; `importance-quadrant` reads them)."""
    import json
    from raptor.api.run import run_dir
    path = run_dir(workspace, run_id) / "summary.json"
    try:
        found = json.loads(path.read_text()).get("importance")
    except (OSError, ValueError):
        return None
    return found if isinstance(found, dict) else None


def _float(value: Any) -> float:
    try:
        return float(str(value).replace(",", ""))
    except ValueError:
        return 0.0


def cost_pareto(costs: list[dict[str, Any]], top: int = 10) -> ChartSpec:
    """C12: where the money goes: mean cost per run by element, from
    `element_costs` or `element_costs_from_tables`."""
    totals = {row["name"]: float(row.get("total") or 0.0) for row in costs}
    rows = sorted(((n, v) for n, v in totals.items() if v > 0),
                  key=lambda kv: -kv[1])[:top]
    grand = sum(totals.values())
    return ChartSpec(
        "pareto", "Where the money goes",
        "mean cost per run, by element, largest first", "element", "cost",
        series=[{"name": name, "value": value,
                 "share": value / grand if grand else 0.0}
                for name, value in rows],
        caption=(f"{rows[0][0]} carries the largest cost "
                 f"({pct(rows[0][1] / grand)})."
                 if rows and grand else
                 "No element has a cost in this run." if costs else _needs("cost")),
        provenance={"grand_total": grand, "format": "money"})


def lost_capacity_pareto(attribution: dict[str, Any], top: int = 10) -> ChartSpec:
    """K1: who loses the energy beside who is down the longest."""
    capacity = attribution.get("capacity") or {}
    by_name = {row["name"]: row for row in attribution.get("elements") or []}
    rows = sorted((row for row in capacity.get("elements") or []
                   if (row.get("share", {}).get("mean") or 0) > 0),
                  key=lambda row: -(row["share"]["mean"] or 0))[:top]
    series = []
    for row in rows:
        downtime = (by_name.get(row["name"], {}).get("dtci", {}).get("mean")
                    or 0.0)
        series.append({"name": row["name"],
                       "value": row["share"]["mean"] or 0.0,
                       "downtime": downtime,
                       "lost_energy": row.get("lost_energy", {}).get("mean"),
                       "through_dependents":
                           row.get("through_dependents", {}).get("mean")})
    return ChartSpec(
        "capacity_pareto", "Who loses the capacity",
        "capacity share against downtime share, same element order",
        "element", "share", series=series,
        caption=(f"{rows[0]['name']} loses the largest share of capacity "
                 f"({pct(rows[0]['share']['mean'] or 0)})."
                 if rows else _needs("capacity")),
        provenance={"runs": attribution.get("runs", 0), "format": "percent",
                    "rule": capacity.get("rule", "")})


def capacity_vs_downtime(attribution: dict[str, Any],
                         top: int = 20) -> ChartSpec:
    """K2: the #256 case in one picture: downtime share against lost flow."""
    capacity = attribution.get("capacity") or {}
    lost = {row["name"]: (row.get("share", {}).get("mean") or 0.0)
            for row in capacity.get("elements") or []}
    # an element that loses flow without ever taking the system down has no
    # downtime row: it sits on the left edge, the case this chart is for
    downtime = {row["name"]: (row.get("dtci", {}).get("mean") or 0.0)
                for row in attribution.get("elements") or []}
    points = [{"name": name, "x": downtime.get(name, 0.0), "y": share}
              for name, share in lost.items()
              if name != "(no single element)" and (share or downtime.get(name))]
    points.sort(key=lambda p: -(p["y"]))
    points = points[:top]
    return ChartSpec(
        "scatter", "Downtime share against lost capacity",
        "points far above the diagonal remove more capacity than their "
        "downtime suggests", "share of downtime",
        "share of lost capacity", series=points,
        marks=[{"kind": "diagonal"}],
        caption=("Points above the diagonal are understated by availability "
                 "alone." if points else _needs("capacity")),
        provenance={"runs": attribution.get("runs", 0), "format": "percent"})


def capacity_waterfall(attribution: dict[str, Any]) -> ChartSpec:
    """K5: where the generated flow goes, one step per cause."""
    capacity = attribution.get("capacity") or {}
    causes = capacity.get("causes") or {}
    total = sum((stat.get("mean") or 0.0) for stat in causes.values())
    steps = []
    running = 1.0
    for cause, stat in sorted(causes.items(),
                              key=lambda kv: -(kv[1].get("mean") or 0.0)):
        value = (stat.get("mean") or 0.0) / total if total else 0.0
        if value <= 0:
            continue
        steps.append({"name": CAUSE_LABELS.get(cause, cause), "cause": cause,
                      "value": value, "from": running, "to": running - value,
                      "share": value})
        running -= value
    return ChartSpec(
        "waterfall", "Where the capacity goes",
        "each step is a cause's share of the lost energy", "cause",
        "share of lost energy", series=steps,
        caption=(f"The largest loss is {steps[0]['name'].lower()} "
                 f"({pct(steps[0]['value'])} of lost energy)."
                 if steps else _needs("capacity")),
        provenance={"runs": attribution.get("runs", 0), "format": "percent",
                    "rule": capacity.get("rule", "")})


def annual_capacity_fan(periods: dict[str, Any]) -> ChartSpec:
    """K6b: capacity-weighted availability year by year (energy delivered /
    flow generated), beside the plain availability fan: with many parallel
    units a year can look fully available and still lose output."""
    capacity = periods.get("capacity") or {}
    spec = annual_fan({**periods, "by_index": capacity.get("by_index") or [],
                       "threshold": capacity.get("threshold")})
    spec.title = "Capacity delivered year by year"
    spec.y_label = "capacity-weighted availability"
    rows = spec.series
    if not rows:
        spec.caption = _needs("capacity") + " (or the run is shorter than one period)"
    elif len(rows) >= FAN_LINE_MIN:
        spec.caption = (f"{len(rows)} periods; the median share of capacity "
                        f"delivered moves from {pct(rows[0]['p50'])} to "
                        f"{pct(rows[-1]['p50'])}.")
    return spec


def annual_fan(periods: dict[str, Any]) -> ChartSpec:
    """K6: does a bad year happen, and does it get worse with age?"""
    rows = [row for row in periods.get("by_index") or []
            if row.get("n") and row.get("p50") is not None]
    series = [{"name": f"year {row['index']}", "index": row["index"],
               "p5": row.get("p5"), "p25": row.get("p25"),
               "p50": row.get("p50"), "p75": row.get("p75"),
               "p95": row.get("p95"), "n": row.get("n")}
              for row in rows]
    threshold = periods.get("threshold")
    few = len(series) < FAN_LINE_MIN
    medians = ", ".join(pct(row["p50"]) for row in series)
    if not series:
        caption = "No complete period in this run."
    elif few:
        caption = (f"Only {len(series)} period{'s' if len(series) != 1 else ''}, "
                   f"too few to show a trend; median {medians}.")
    else:
        caption = (f"{len(series)} periods; the median moves from "
                   f"{pct(series[0]['p50'])} to {pct(series[-1]['p50'])}.")
    return ChartSpec(
        "fan", "Availability year by year",
        ("whiskers: P5-P95, box: P25-P75, bar: median" if few else
         "bands: P5-P95, P25-P75; line: median"), "period", "availability",
        series=series,
        marks=[{"kind": "target", "value": threshold}]
        if threshold is not None else [],
        caption=caption,
        provenance={"runs": periods.get("runs", 0), "format": "percent",
                    "periods_per_run": periods.get("periods_per_run", 0)})


def annual_dots(periods: dict[str, Any]) -> ChartSpec:
    """K7: all run-years as a dot plot against the guarantee."""
    pooled = periods.get("pooled") or {}
    threshold = periods.get("threshold")
    n = int(pooled.get("n") or 0)
    if pooled.get("dots"):
        values = [float(v) for v in pooled["dots"]]
        subtitle = ("each dot is one run-year" if len(values) == n else
                    f"each dot is {pct(1 / len(values))} of the {n} run-years")
    else:
        # A run summarised before the equal-share dots: its quantiles are
        # not equal shares, so say what they are.
        quantiles = ("p1", "p5", "p10", "p25", "p50", "p75", "p90", "p95", "p99")
        values = [pooled[key] for key in quantiles
                  if isinstance(pooled.get(key), (int, float))]
        subtitle = "each dot is one of the quantiles P1 to P99 of the run-years"
    dots = [{"value": v} for v in values]
    below = pooled.get("p_below")
    caption = (f"{pct(below)} of run-years fall below the threshold."
               if isinstance(below, (int, float)) else
               f"{pooled.get('n', 0)} run-years; no threshold was set.")
    return ChartSpec(
        "dot_plot", "A bad year, as a frequency",
        subtitle, "availability",
        "years", series=[{"name": "run-years", "dots": dots}],
        marks=[{"kind": "target", "value": threshold}]
        if threshold is not None else [],
        caption=caption,
        provenance={"runs": periods.get("runs", 0), "format": "percent",
                    "periods": pooled.get("n", 0), "below": below})


#: Colour groups a treemap can use beyond the downtime causes: the cost
#: components.  "sparing" shares the waiting-for-a-spare colour on purpose.
COST_GROUPS = {
    "purchase": "Purchase", "operating": "Operating", "sparing": "Spares",
    "disposal": "Disposal", "spare_pool": "Spare pools",
}


def _treemap(items: list[dict[str, Any]], *, title: str, subtitle: str,
             fmt: str, what: str, runs: int, width: float, height: float,
             top: int, group_labels: dict[str, str] | None = None) -> ChartSpec:
    """Lay out `items` ({name, value, cause}) largest first; past `top`
    the rest share one "N others" cell.  `value` is what the area shows;
    `share` (value / total) is added for the labels."""
    from raptor.api.insight.axes import format_value
    from raptor.api.insight.treemap import layout

    rows = sorted((i for i in items if (i.get("value") or 0) > 0),
                  key=lambda i: -float(i["value"]))
    total = sum(float(i["value"]) for i in rows)
    visible = rows[:max(1, top)]
    rest = rows[max(1, top):]
    cells = [dict(i) for i in visible]
    if rest:
        cells.append({"name": f"{len(rest)} others",
                      "value": sum(float(i["value"]) for i in rest), "cause": None})
    for cell in cells:
        cell["share"] = float(cell["value"]) / total if total else 0.0
    rects = layout(cells, width, height)
    series = [{**cell, **rect.to_dict(),
               "aspect": rect.aspect if rect.area > 0 else None}
              for cell, rect in zip(cells, rects, strict=True)]
    caption = "Nothing to show."
    if series:
        first = series[0]
        amount = format_value(float(first["value"]), fmt, 3)
        caption = (f"{first['name']} occupies the largest area ({amount}"
                   + (f", {pct(first['share'])} of {what}" if fmt != "percent" else
                      f" of {what}")
                   + ").")
    provenance: dict[str, Any] = {"runs": runs, "format": fmt, "measure": what,
                                  "elements": len(rows), "shown": len(cells)}
    if group_labels:
        provenance["group_labels"] = group_labels
    return ChartSpec("treemap", title, subtitle, "width", "height",
                     series=series, caption=caption, provenance=provenance)


def phase_downtime(attribution: dict[str, Any]) -> ChartSpec:
    """Which mission phase the downtime falls in, with each phase's
    availability, in the model's phase order."""
    rows = [r for r in attribution.get("phases") or []]
    series = [{"name": r["name"], "value": r["share_of_downtime"],
               "availability": r["availability"],
               "driver": (r["drivers"][0]["name"] if r.get("drivers") else None)}
              for r in rows]
    worst = min((r for r in rows if r["availability"] is not None),
                key=lambda r: r["availability"], default=None)
    caption = "The model has no phases."
    if worst is not None:
        caption = (f"{worst['name']} is the weakest phase: availability "
                   f"{100 * worst['availability']:.4g} %, "
                   f"{pct(worst['share_of_downtime'])} of all downtime")
        if worst.get("drivers"):
            caption += f", mostly started by {worst['drivers'][0]['name']}"
        caption += "."
    return ChartSpec(
        "pareto", "Downtime by phase",
        "share of all system downtime in each phase, in phase order",
        "phase", "share of system downtime", series=series, caption=caption,
        provenance={"runs": attribution.get("runs", 0), "format": "percent"})


def downtime_treemap(attribution: dict[str, Any], *, width: float = 1000.0,
                     height: float = 600.0, top: int = 200) -> ChartSpec:
    """The downtime treemap: area = share of downtime, label = element."""
    items = [{"name": row["name"], "value": row["dtci"]["mean"] or 0.0,
              "cause": row.get("main_cause")}
             for row in attribution.get("elements") or []]
    return _treemap(items, title="Downtime treemap",
                    subtitle="area = share of system downtime; colour = main "
                             "cause; click a cell to locate",
                    fmt="percent", what="system downtime",
                    runs=int(attribution.get("runs", 0) or 0),
                    width=width, height=height, top=top)


def lost_capacity_treemap(attribution: dict[str, Any], *, width: float = 1000.0,
                          height: float = 600.0, top: int = 200) -> ChartSpec:
    """Area = share of the lost flow; colour = what caused the element's lost
    flow (its downtime cause when an older result has no capacity causes)."""
    causes = {row["name"]: row.get("main_cause")
              for row in attribution.get("elements") or []}
    capacity = attribution.get("capacity") or {}
    items = [{"name": row["name"], "value": (row.get("share") or {}).get("mean") or 0.0,
              "cause": row.get("main_cause") or causes.get(row["name"])}
             for row in capacity.get("elements") or []]
    return _treemap(items, title="Lost capacity treemap",
                    subtitle="area = share of the lost flow; colour = main "
                             "cause; click a cell to locate",
                    fmt="percent", what="the lost flow",
                    runs=int(attribution.get("runs", 0) or 0),
                    width=width, height=height, top=top)


def cost_treemap(costs: list[dict[str, Any]], *, width: float = 1000.0,
                 height: float = 600.0, top: int = 200) -> ChartSpec:
    """Area = mean life-cycle cost per run; colour = its largest component
    (purchase, operating, spares, disposal).  Spare pools are cells of
    their own, so the spares picture is complete."""
    items = []
    for row in costs:
        parts = {k: float(row.get(k) or 0.0)
                 for k in (*_COST_PARTS, "spare_pool", "other")}
        group = max(parts, key=lambda k: parts[k]) if any(parts.values()) else None
        items.append({"name": row["name"], "value": float(row.get("total") or 0.0),
                      "cause": group})
    return _treemap(items, title="Cost treemap",
                    subtitle="area = mean life-cycle cost per run; colour = "
                             "largest part of it",
                    fmt="money", what="the cost", runs=0,
                    width=width, height=height, top=top,
                    group_labels=dict(COST_GROUPS))


def outage_anatomy(attribution: dict[str, Any], max_columns: int = 6
                   ) -> ChartSpec:
    """What outages look like: the ordered steps that start and prolong them."""
    tracks: dict[tuple, dict[str, Any]] = {}
    for sequence in attribution.get("sequences") or []:
        steps = (sequence.get("steps") or [])[:max_columns]
        prefix: list[tuple[str, str]] = []
        for column, step in enumerate(steps):
            prefix.append((str(step.get("element")), str(step.get("cause"))))
            key = (column, tuple(prefix))
            entry = tracks.setdefault(key, {
                "column": column, "path": list(prefix),
                "label": str(step.get("element")),
                "cause": str(step.get("cause")),
                "value": 0.0, "outages": 0.0})
            entry["value"] += float(sequence.get("share") or 0.0)
            entry["outages"] += float(sequence.get("outages") or 0.0)
    series = sorted(tracks.values(),
                    key=lambda row: (row["column"], -row["value"]))
    left = series[0]["value"] if series else 0.0
    return ChartSpec(
        "icicle", "What an outage looks like",
        "each column is the next step; width = share of downtime",
        "step", "share of downtime", series=series,
        caption=(f"The commonest start is {series[0]['label']} "
                 f"({pct(left)} of downtime)."
                 if series else "No outage sequences recorded."),
        provenance={"runs": attribution.get("runs", 0),
                    "sequences": len(attribution.get("sequences") or [])})


def ensemble_ribbon(attribution: dict[str, Any], max_runs: int = 200
                    ) -> ChartSpec:
    """All trials as rows: green where up, red where down (C5/7.6.4)."""
    record = attribution.get("outages") or {}
    runs = list(record.get("runs") or ())
    if not runs:
        return ChartSpec(
            "ribbon", "Run-to-run system state", "needs the outage record",
            "time", "runs", series=[],
            caption="No per-run outage record in this attribution.")
    rows: list[dict[str, Any]] = []
    for index, run in enumerate(runs):
        start, end = float(run.get("start", 0.0)), float(run.get("end", 0.0))
        red = [float(value) for value in run.get("red") or ()]
        pairs = list(zip(red[0::2], red[1::2], strict=False))
        rows.append({"run": index + 1, "start": start, "end": end,
                     "red": [list(pair) for pair in pairs],
                     "downtime": sum(b - a for a, b in pairs),
                     "span": max(end - start, 1e-12)})
    rows.sort(key=lambda row: row["downtime"])
    if len(rows) > max_runs:
        step = len(rows) / max_runs
        rows = [rows[min(len(rows) - 1, int(i * step))]
                for i in range(max_runs)]
    worst = rows[-1]["downtime"] / rows[-1]["span"]
    best = rows[0]["downtime"] / rows[0]["span"]
    return ChartSpec(
        "ribbon", "Run-to-run system state",
        "one row per trial, sorted by downtime; red = system down",
        "time", "runs", series=rows,
        caption=(f"{len(runs)} runs; best {pct(1 - best)} up, "
                 f"worst {pct(1 - worst)} up."),
        provenance={"runs": attribution.get("runs", len(runs)),
                    "shown": len(rows)})


#: Chart kinds whose small items `fold_small` can gather into one row or cell.
FOLDABLE_KINDS = ("pareto", "capacity_pareto", "treemap")


def _share_of(spec: ChartSpec, row: dict[str, Any]) -> float | None:
    """A row's share of the whole: its own `share`, or its value when the
    value already is a share (a percent chart)."""
    if isinstance(row.get("share"), (int, float)):
        return float(row["share"])
    if spec.provenance.get("format") == "percent" or spec.kind == "capacity_pareto":
        value = row.get("value")
        return float(value) if isinstance(value, (int, float)) else None
    return None


def foldable(spec: ChartSpec) -> bool:
    """Whether `fold_small` applies: element rankings and treemaps (not a
    chart in a fixed order, such as downtime by phase)."""
    if spec.kind not in FOLDABLE_KINDS:
        return False
    if spec.kind == "pareto" and spec.x_label != "element":
        return False
    return any(_share_of(spec, row) is not None for row in spec.series)


def fold_small(spec: ChartSpec, min_share: float) -> ChartSpec:
    """The same chart with every item below `min_share` (0 to 1) of the
    whole gathered into one "N others below X %" row or cell.

    For a presentation: a ranking or treemap of many slivers reads better
    with them folded, and nothing is hidden (the folded row carries the
    names and their total).  A treemap is laid out again.  Charts that are
    not `foldable`, or a fold that would gather fewer than two items, come
    back unchanged."""
    if min_share <= 0 or not foldable(spec):
        return spec
    keep: list[dict[str, Any]] = []
    small: list[dict[str, Any]] = []
    for row in spec.series:
        share = _share_of(spec, row)
        (small if share is not None and share < min_share else keep).append(row)
    if len(small) < 2:
        return spec
    label = f"{len(small)} others below {pct(min_share)}"
    total = sum(float(r.get("value") or 0.0) for r in small)
    names = [str(r.get("name")) for r in small]
    folded: dict[str, Any] = {"name": label, "value": total, "folded": names}
    if any("share" in r for r in small):
        folded["share"] = sum(_share_of(spec, r) or 0.0 for r in small)
    if spec.kind == "capacity_pareto":
        folded["downtime"] = sum(float(r.get("downtime") or 0.0) for r in small)
    causes: dict[str, float] = {}
    for row in small:
        for cause, part in (row.get("causes") or {}).items():
            causes[cause] = causes.get(cause, 0.0) + float(part) * float(
                row.get("value") or 0.0)
    if causes and total > 0:
        folded["causes"] = {c: v / total for c, v in causes.items()}
    provenance = {**spec.provenance, "folded_below": min_share,
                  "folded": len(small)}
    caption = (f"{spec.caption} " if spec.caption else "") + \
        f"{label}: {', '.join(names)}."
    if spec.kind != "treemap":
        return ChartSpec(spec.kind, spec.title, spec.subtitle, spec.x_label,
                         spec.y_label, series=[*keep, folded], marks=spec.marks,
                         annotations=spec.annotations, caption=caption,
                         provenance=provenance)
    from raptor.api.insight.treemap import layout
    width = max(float(r["x"]) + float(r["w"]) for r in spec.series)
    height = max(float(r["y"]) + float(r["h"]) for r in spec.series)
    cells = [{k: v for k, v in r.items() if k not in ("x", "y", "w", "h", "aspect")}
             for r in keep] + [{**folded, "cause": None}]
    cells.sort(key=lambda cell: -float(cell.get("value") or 0.0))
    rects = layout(cells, width, height)
    series = [{**cell, **rect.to_dict(),
               "aspect": rect.aspect if rect.area > 0 else None}
              for cell, rect in zip(cells, rects, strict=True)]
    return ChartSpec(spec.kind, spec.title, spec.subtitle, spec.x_label,
                     spec.y_label, series=series, marks=spec.marks,
                     annotations=spec.annotations, caption=caption,
                     provenance=provenance)


def _time_chart(name: str, doc: str) -> Any:
    """A `CHARTS` entry for one of the charts over time (`timecharts`).

    They live in their own module, which imports `ChartSpec` from here, so
    the entry imports it only when called."""
    def build(**kwargs: Any) -> ChartSpec:
        from raptor.api.insight.timecharts import time_chart
        return time_chart(name, **kwargs)
    build.__doc__ = doc
    build.__name__ = name.replace("-", "_")
    return build


#: Every chart the application can draw, keyed by the name the CLI and MCP use.
CHARTS = {
    "availability-through-run": _time_chart(
        "availability-through-run",
        "One run's time so far fully up, degraded and down (the availability plot)."),
    "interval-availability": _time_chart(
        "interval-availability",
        "The availability in each recording interval of one run."),
    "availability-band": _time_chart(
        "availability-band",
        "Every run's availability so far, as bands across the runs."),
    "capacity-through-run": _time_chart(
        "capacity-through-run",
        "One run's flow against a fully working system (the capacity plot)."),
    "capacity-band": _time_chart(
        "capacity-band", "Every run's flow at each moment, as bands across the runs."),
    "trial-outcome": trial_outcome,
    "ecdf": ecdf,
    "downtime-pareto": downtime_pareto,
    "downtime-waterfall": downtime_waterfall,
    "availability-ladder": availability_ladder,
    "outage-histogram": outage_histogram,
    "frequency-duration": frequency_duration,
    "importance-quadrant": importance_quadrant,
    "convergence": convergence,
    "cost-pareto": cost_pareto,
    "lost-capacity-pareto": lost_capacity_pareto,
    "capacity-vs-downtime": capacity_vs_downtime,
    "capacity-waterfall": capacity_waterfall,
    "annual-fan": annual_fan,
    "annual-capacity-fan": annual_capacity_fan,
    "annual-dots": annual_dots,
    "downtime-treemap": downtime_treemap,
    "phase-downtime": phase_downtime,
    "lost-capacity-treemap": lost_capacity_treemap,
    "cost-treemap": cost_treemap,
    "outage-anatomy": outage_anatomy,
    "ensemble-ribbon": ensemble_ribbon,
}


def chart_named(name: str, *, per_run: list[dict] | None = None,
                attribution: dict[str, Any] | None = None,
                importance: dict[str, Any] | None = None,
                report_data: list[Any] | None = None,
                costs: list[dict[str, Any]] | None = None,
                periods: dict[str, Any] | None = None,
                field_name: str = "availability",
                label: str = "Availability",
                target: float | None = None,
                units: str = "",
                ao_runs: list[dict[str, Any]] | None = None,
                cap_runs: list[dict[str, Any]] | None = None,
                run: int = 1,
                y_floor: float | None = None) -> ChartSpec:
    """Build one chart by name, from whichever inputs it needs.

    The charts over time (`raptor.api.insight.timecharts.TIME_CHARTS`) take
    the parsed availability plot (`ao_runs`) or capacity plot (`cap_runs`),
    the `run` number for the one-run charts, and `y_floor`, a manual lower
    limit for the availability axis.

    Raises ValueError naming the charts when the name is unknown, and
    ValueError naming the missing input when one is required.
    """
    if name not in CHARTS:
        raise ValueError(
            f"unknown chart {name!r}; known charts: {', '.join(sorted(CHARTS))}")
    from raptor.api.insight.timecharts import TIME_CHARTS, time_chart
    if name in TIME_CHARTS:
        generated = (attribution or {}).get("capacity", {}) or {}
        ends = [row.get("ending_sim_time") for row in per_run or []]
        return time_chart(name, ao_runs=ao_runs, cap_runs=cap_runs, run=run,
                          units=units, target=target, y_floor=y_floor,
                          generated=generated.get("generated"),
                          ends=ends if any(e is not None for e in ends) else None)

    capacity_chart = name in ("lost-capacity-treemap", "lost-capacity-pareto",
                              "capacity-vs-downtime", "capacity-waterfall")

    def need_attribution() -> dict[str, Any]:
        if attribution is None:
            raise ValueError(message("attribution", *(("capacity",) if capacity_chart
                                                      else ())))
        return attribution

    def _need(value: Any, what: str) -> dict[str, Any]:
        if value is None:
            raise ValueError(f"this chart needs {what}")
        return value

    def element_cost_rows() -> list[dict[str, Any]]:
        return costs if costs is not None else element_costs(report_data or [])

    def need_importance() -> dict[str, Any]:
        if importance is None or importance.get("mode") not in (None, "both"):
            # the quadrant plots RAW against RRW: "improvement" has no RAW
            # and "risk" no RRW, so only "both" can draw it
            raise ValueError(message("importance"))
        return importance

    builders = {
        "trial-outcome": lambda: trial_outcome(
            per_run or [], field_name, label=label, target=target,
            units=units),
        "ecdf": lambda: ecdf(
            per_run or [], field_name, label=label, target=target,
            units=units),
        "convergence": lambda: convergence(per_run or [], field_name, target),
        "importance-quadrant": lambda: importance_quadrant(need_importance()),
        "cost-pareto": lambda: cost_pareto(element_cost_rows()),
        "downtime-waterfall": lambda: downtime_waterfall(
            need_attribution(),
            measures.availability_ladder(per_run or [])["operational"]["value"]
            if per_run else None),
        "downtime-pareto": lambda: downtime_pareto(need_attribution()),
        "availability-ladder": lambda: availability_ladder(
            measures.availability_ladder(per_run or [])),
        "outage-histogram": lambda: outage_histogram(need_attribution()),
        "frequency-duration": lambda: frequency_duration(need_attribution()),
        "downtime-treemap": lambda: downtime_treemap(need_attribution()),
        "phase-downtime": lambda: phase_downtime(need_attribution()),
        "lost-capacity-treemap": lambda: lost_capacity_treemap(need_attribution()),
        "cost-treemap": lambda: cost_treemap(element_cost_rows()),
        "outage-anatomy": lambda: outage_anatomy(need_attribution()),
        "ensemble-ribbon": lambda: ensemble_ribbon(need_attribution()),
        "lost-capacity-pareto": lambda: lost_capacity_pareto(
            need_attribution()),
        "capacity-vs-downtime": lambda: capacity_vs_downtime(
            need_attribution()),
        "capacity-waterfall": lambda: capacity_waterfall(need_attribution()),
        "annual-fan": lambda: annual_fan(_need(periods, "period-by-period "
                                                       "availability")),
        "annual-dots": lambda: annual_dots(_need(periods, "period-by-period "
                                                          "availability")),
        "annual-capacity-fan": lambda: annual_capacity_fan(
            _need(periods, "period-by-period availability")),
    }
    return builders[name]()
