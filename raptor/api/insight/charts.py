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


def _values(per_run: list[dict], field_name: str) -> list[float]:
    return [float(row[field_name]) for row in per_run
            if isinstance(row.get(field_name), (int, float))
            and not isinstance(row.get(field_name), bool)]


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
        caption += f", mean {mean:.6g}"
    if target is not None:
        caption += (f"; {met} of {len(values)} "
                    f"({100 * met / len(values):.3g} %) met the target.")
    else:
        caption += "."
    return ChartSpec(
        "dot_plot", f"Run-to-run {label.lower()}",
        "one dot per quantile slice of the runs", label, "runs",
        series=[{"name": "runs", "dots": dots, "values": values}],
        marks=[{"kind": "target", "value": target}] if target is not None else [],
        caption=caption,
        provenance={"runs": len(values), "units": units,
                    "measure": field_name, "met": met, "target": target})


def ecdf(per_run: list[dict], field_name: str = "availability", *,
         label: str = "Availability", target: float | None = None,
         units: str = "") -> ChartSpec:
    """C1b: the chance of reaching each value, same runs as the dot plot."""
    values = sorted(_values(per_run, field_name))
    points = [{"x": v, "y": (i + 1) / len(values)}
              for i, v in enumerate(values)] if values else []
    return ChartSpec(
        "ecdf", f"Chance of at least {label.lower()}", "empirical distribution",
        label, "chance", series=[{"name": label, "points": points}],
        marks=[{"kind": "target", "value": target}] if target is not None else [],
        caption=(f"Half the runs are at or below "
                 f"{points[len(points) // 2]['x']:.6g}." if points
                 else "No runs."),
        provenance={"runs": len(values), "units": units})


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
                 f"({100 * (rows[0]['dtci']['mean'] or 0):.3g} %)."
                 if rows else "No system outage."),
        provenance={"runs": attribution.get("runs", 0)})


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
        provenance={"runs": attribution.get("runs", 0),
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
        caption="; ".join(f"{s['name']} {100 * s['value']:.4g} %"
                          for s in present) or "No availability reported.",
        provenance={"runs": ladder.get("runs")})


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
    """C7: reliability against maintainability, bubble area = DTCI."""
    rows = sorted((r for r in attribution.get("elements") or []
                   if (r.get("deci", {}).get("mean") or 0) > 0),
                  key=lambda r: -(r.get("dtci", {}).get("mean") or 0))[:top]
    points = [{"name": r["name"],
               "x": r["deci"]["mean"] or 0.0,
               "y": ((r["dtci"]["mean"] or 0.0)
                     / (r["deci"]["mean"] or 1.0)),
               "area": r["dtci"]["mean"] or 0.0} for r in rows]
    return ChartSpec(
        "bubble", "Frequency against duration",
        "x = share of outages started, y = share of downtime per outage "
        "started, area = share of downtime", "outages started",
        "downtime per outage", series=points,
        caption=("Reliability is the lever for bubbles high on the left; "
                 "repair and logistics for those low on the right."
                 if points else "No element started an outage."),
        provenance={"runs": attribution.get("runs", 0)})


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
                 "fixing." if points else "No importance measures."),
        provenance={"metric": metric})


def convergence(per_run: list[dict], field_name: str = "availability",
                target: float | None = None) -> ChartSpec:
    """C11: has the running mean settled; is the run long enough?"""
    values = _values(per_run, field_name)
    points = []
    running = 0.0
    for i, value in enumerate(values, start=1):
        running += value
        mean = running / i
        sem = None
        if i > 1:
            var = sum((v - mean) ** 2 for v in values[:i]) / (i - 1)
            sem = (var / i) ** 0.5
        points.append({"n": i, "mean": mean, "sem": sem})
    last = points[-1] if points else None
    return ChartSpec(
        "convergence", "Has the run settled?",
        "the band is +/- 2 standard errors; it should stop moving",
        "runs", field_name, series=points,
        marks=[{"kind": "target", "value": target}] if target is not None else [],
        caption=(f"After {len(values):,} runs the mean is {last['mean']:.6g}"
                 f" with +/-2 SEM {2 * last['sem']:.3g}."
                 if last is not None and last["sem"] is not None
                 else "Not enough runs to estimate the error."),
        provenance={"runs": len(values), "measure": field_name})


def cost_pareto(report_data: list[Any], top: int = 10) -> ChartSpec:
    """C12: where the money goes, from the engine's per-block cost rows."""
    totals: dict[str, float] = {}
    for report in report_data:
        cost = getattr(report, "cost", None)
        if cost is None:
            continue
        for block_id, row in getattr(cost, "blocks", {}).items():
            name = str(getattr(row, "name", block_id))
            totals[name] = totals.get(name, 0.0) + float(row.total or 0.0)
    rows = sorted(totals.items(), key=lambda kv: -kv[1])[:top]
    grand = sum(totals.values())
    return ChartSpec(
        "pareto", "Where the money goes",
        "by element, largest first", "element", "cost",
        series=[{"name": name, "value": value,
                 "share": value / grand if grand else 0.0}
                for name, value in rows],
        caption=(f"{rows[0][0]} carries the largest cost "
                 f"({100 * rows[0][1] / grand:.3g} %)."
                 if rows and grand else "No cost analysis in this run."),
        provenance={"runs": len(report_data), "grand_total": grand})


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
                 f"({100 * (rows[0]['share']['mean'] or 0):.3g} %)."
                 if rows else "No capacity attribution in this run."),
        provenance={"runs": attribution.get("runs", 0),
                    "rule": capacity.get("rule", "")})


def capacity_vs_downtime(attribution: dict[str, Any],
                         top: int = 20) -> ChartSpec:
    """K2: the #256 case in one picture: downtime share against lost flow."""
    capacity = attribution.get("capacity") or {}
    lost = {row["name"]: (row.get("share", {}).get("mean") or 0.0)
            for row in capacity.get("elements") or []}
    points = []
    for row in attribution.get("elements") or []:
        downtime = row.get("dtci", {}).get("mean")
        if downtime is None or row["name"] not in lost:
            continue
        points.append({"name": row["name"], "x": downtime,
                       "y": lost[row["name"]]})
    points.sort(key=lambda p: -(p["y"]))
    points = points[:top]
    return ChartSpec(
        "scatter", "Downtime share against lost capacity",
        "points far above the diagonal remove more capacity than their "
        "downtime suggests", "share of downtime",
        "share of lost capacity", series=points,
        marks=[{"kind": "diagonal"}],
        caption=("Points above the diagonal are understated by availability "
                 "alone." if points else "No capacity attribution in this run."),
        provenance={"runs": attribution.get("runs", 0)})


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
                 f"({100 * steps[0]['value']:.3g} % of lost energy)."
                 if steps else "No capacity attribution in this run."),
        provenance={"runs": attribution.get("runs", 0),
                    "rule": capacity.get("rule", "")})


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
    caption = (f"{len(series)} periods; the median moves from "
               f"{100 * series[0]['p50']:.3g} % to "
               f"{100 * series[-1]['p50']:.3g} %."
               if len(series) > 1 else "Not enough periods for a trend.")
    return ChartSpec(
        "fan", "Availability year by year",
        "bands: P5-P95, P25-P75; line: median", "period", "availability",
        series=series,
        marks=[{"kind": "target", "value": threshold}]
        if threshold is not None else [],
        caption=caption,
        provenance={"runs": periods.get("runs", 0),
                    "periods_per_run": periods.get("periods_per_run", 0)})


def annual_dots(periods: dict[str, Any]) -> ChartSpec:
    """K7: all run-years as a dot plot against the guarantee."""
    pooled = periods.get("pooled") or {}
    threshold = periods.get("threshold")
    quantiles = ("p1", "p5", "p10", "p25", "p50", "p75", "p90", "p95", "p99")
    values = [pooled[key] for key in quantiles
              if isinstance(pooled.get(key), (int, float))]
    dots = [{"value": v} for v in values]
    below = pooled.get("p_below")
    caption = (f"{100 * below:.3g} % of run-years fall below the threshold."
               if isinstance(below, (int, float)) else
               f"{pooled.get('n', 0)} run-years; no threshold was set.")
    return ChartSpec(
        "dot_plot", "A bad year, as a frequency",
        "each dot is one quantile slice of the run-years", "availability",
        "years", series=[{"name": "run-years", "dots": dots}],
        marks=[{"kind": "target", "value": threshold}]
        if threshold is not None else [],
        caption=caption,
        provenance={"runs": periods.get("runs", 0),
                    "periods": pooled.get("n", 0), "below": below})


#: Every chart the application can draw, keyed by the name the CLI and MCP use.
CHARTS = {
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
    "annual-dots": annual_dots,
}


def chart_named(name: str, *, per_run: list[dict] | None = None,
                attribution: dict[str, Any] | None = None,
                importance: dict[str, Any] | None = None,
                report_data: list[Any] | None = None,
                periods: dict[str, Any] | None = None,
                field_name: str = "availability",
                label: str = "Availability",
                target: float | None = None,
                units: str = "") -> ChartSpec:
    """Build one chart by name, from whichever inputs it needs.

    Raises ValueError naming the charts when the name is unknown, and
    ValueError naming the missing input when one is required.
    """
    if name not in CHARTS:
        raise ValueError(
            f"unknown chart {name!r}; known charts: {', '.join(sorted(CHARTS))}")

    def need_attribution() -> dict[str, Any]:
        if attribution is None:
            raise ValueError("this chart needs downtime attribution")
        return attribution

    def _need(value: Any, what: str) -> dict[str, Any]:
        if value is None:
            raise ValueError(f"this chart needs {what}")
        return value

    def need_importance() -> dict[str, Any]:
        if importance is None:
            raise ValueError("this chart needs importance measures")
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
        "cost-pareto": lambda: cost_pareto(report_data or []),
        "downtime-waterfall": lambda: downtime_waterfall(
            need_attribution(),
            measures.availability_ladder(per_run or [])["operational"]["value"]
            if per_run else None),
        "downtime-pareto": lambda: downtime_pareto(need_attribution()),
        "availability-ladder": lambda: availability_ladder(
            measures.availability_ladder(per_run or [])),
        "outage-histogram": lambda: outage_histogram(need_attribution()),
        "frequency-duration": lambda: frequency_duration(need_attribution()),
        "lost-capacity-pareto": lambda: lost_capacity_pareto(
            need_attribution()),
        "capacity-vs-downtime": lambda: capacity_vs_downtime(
            need_attribution()),
        "capacity-waterfall": lambda: capacity_waterfall(need_attribution()),
        "annual-fan": lambda: annual_fan(_need(periods, "period-by-period "
                                                       "availability")),
        "annual-dots": lambda: annual_dots(_need(periods, "period-by-period "
                                                          "availability")),
    }
    return builders[name]()
