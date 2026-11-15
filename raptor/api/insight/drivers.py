"""Reading a stored attribution: the downtime drivers, in words and numbers."""
from __future__ import annotations

import json
import math
from typing import Any

from raptor.api.insight.attribution import CAUSE_LABELS
from raptor.api.workspace import RunArtefactsError


class NoAttribution(RunArtefactsError):
    """The run was made without downtime attribution."""


def read(workspace: Any, run_id: str) -> dict[str, Any]:
    """The run's stored attribution. Raises `NoAttribution` when it has none."""
    from raptor.api.run import run_dir
    path = run_dir(workspace, run_id) / "attribution.json"
    if not path.is_file():
        raise NoAttribution(
            f"run {run_id!r} has no downtime attribution; run it again with attribution on")
    return json.loads(path.read_text())


def periods(workspace: Any, run_id: str, length: float | None = None, *,
            offset: float = 0.0, below: float | None = None,
            partial: str = "drop", rows: bool = False) -> dict[str, Any]:
    """Availability period by period for a run made with attribution.
    `length` defaults to a year when the model's unit is hours.

    With capacity analysis the summary also has ``capacity``: the same
    statistics for capacity-weighted availability (energy delivered /
    flow generated x period), from the run's capacity sink.  `rows` adds
    ``rows``, one entry per (run, period), for an export."""
    from raptor.api.insight.periods import HOURS_PER_YEAR, PeriodSpec, period_summary
    from raptor.api.run import run_dir
    attribution = read(workspace, run_id)
    units = str(attribution.get("units") or "")
    if length is None:
        if not units.lower().startswith("hour"):
            raise ValueError(
                f"the model's time unit is {units or 'not set'}: give the period length in that unit")
        length = HOURS_PER_YEAR
    path = run_dir(workspace, run_id) / "outages.json"
    if not path.is_file():
        raise NoAttribution(
            f"run {run_id!r} kept no outage intervals (too many to store); use fewer runs")
    generated = float((attribution.get("capacity") or {}).get("generated") or 0.0)
    sinks = None
    sink_path = run_dir(workspace, run_id) / "capacity.txt"
    if generated > 0 and sink_path.is_file():
        from raptor.api.series import parse_capacity_runs
        sinks = parse_capacity_runs(sink_path.read_text())
    summary = period_summary(
        json.loads(path.read_text()),
        PeriodSpec(float(length), float(offset), partial),  # type: ignore[arg-type]
        capacity_runs=sinks, generated=generated, threshold=below, rows=rows,
        units=units)
    summary["run_id"] = run_id
    return summary


def _pct(x: float | None) -> str:
    return "n/a" if x is None else f"{100 * x:.3g} %"


def top(attribution: dict[str, Any], n: int = 10) -> list[dict[str, Any]]:
    rows = [e for e in attribution["elements"] if (e["red_time"]["mean"] or 0) > 0]
    rows.sort(key=lambda e: (-(e["dtci"]["mean"] or 0.0), e["name"]))
    return rows[:n]


def caption(attribution: dict[str, Any]) -> str:
    """One sentence a report can quote."""
    drivers = top(attribution, 3)
    if not drivers:
        return f"No system outage occurred in {attribution['runs']:,} runs."
    share = sum(e["dtci"]["mean"] or 0.0 for e in drivers)
    names = ", ".join(f"{e['name']} {_pct(e['dtci']['mean'])}" for e in drivers)
    lead = drivers[0]
    cause = CAUSE_LABELS.get(lead["main_cause"] or "", "").lower()
    tail = (f" {lead['name']}'s outages are mostly {cause} "
            f"({_pct(lead['causes'].get(lead['main_cause'], 0))})." if cause else "")
    return (f"{len(drivers)} element{'s' if len(drivers) != 1 else ''} cause "
            f"{_pct(share)} of system downtime: {names}.{tail}")


def text_lines(attribution: dict[str, Any], n: int = 10) -> list[str]:
    """The drivers as a plain-text table."""
    units = attribution.get("units") or ""
    system = attribution["system"]
    lines = [caption(attribution), ""]
    red, outs = system["red_time"]["mean"], system["outages"]["mean"]
    lines.append(f"System down {red:,.4g} {units} a run in {outs:,.3g} outages "
                 f"({attribution['runs']:,} runs)".replace("  ", " "))
    by_cause = sorted(attribution["causes"].items(), key=lambda kv: -kv[1])
    if by_cause:
        lines.append("By cause: " + ", ".join(
            f"{CAUSE_LABELS[c].lower()} {_pct(v)}" for c, v in by_cause))
    lines.append("")
    lines.append(f"{'Element':<22}{'Downtime':>10}{'±':>8}{'Outages':>9}{'Failures':>10}  Main cause")
    for e in top(attribution, n):
        sem = e["dtci"]["sem"]
        lines.append(
            f"{e['name'][:21]:<22}{_pct(e['dtci']['mean']):>10}"
            f"{(_pct(sem) if sem is not None else ''):>8}{_pct(e['deci']['mean']):>9}"
            f"{_pct(e['fci']['mean']):>10}  "
            f"{CAUSE_LABELS.get(e['main_cause'] or '', '')}")
    sequences = attribution.get("sequences") or []
    if sequences:
        lines += ["", "What the outages look like (share of downtime):"]
        for seq in sequences[:5]:
            path = " -> ".join(f"{s['element']} ({CAUSE_LABELS[s['cause']].lower()})"
                               for s in seq["steps"]) or "(unknown)"
            lines.append(f"  {_pct(seq['share']):>7}  {path}")
    if attribution.get("hierarchies"):
        lines += [""] + hierarchy_lines(attribution, n)
    if attribution.get("phases"):
        lines += [""] + phase_lines(attribution)
    capacity = attribution.get("capacity")
    if capacity:
        from raptor.api.insight.capacity_attribution import text_lines as capacity_lines
        lines += [""] + capacity_lines(capacity, n)
    return lines


#: The keys of a summary statistic kept by `compact`: enough to quote a
#: mean with its interval and say how many runs it rests on.
SUMMARY_STAT = ("mean", "sem", "n")


def _is_stat(value: Any) -> bool:
    return isinstance(value, dict) and "mean" in value and "sem" in value


def _round(value: Any, digits: int = 6) -> Any:
    if isinstance(value, float) and math.isfinite(value) and value != 0.0:
        return float(f"{value:.{digits}g}")
    return value


def compact(value: Any, top: int = 10) -> Any:
    """`value` (an attribution, or part of one) for a reader rather than a
    store: each statistic cut to `SUMMARY_STAT`, floats to six significant
    figures, and the ranked lists (outage sequences, lost-capacity elements)
    to their first `top`. The numbers kept are unchanged apart from rounding."""
    if _is_stat(value):
        return {k: _round(value[k]) for k in SUMMARY_STAT if k in value}
    if isinstance(value, dict):
        out = {}
        for key, item in value.items():
            if key in ("sequences", "elements") and isinstance(item, list):
                item = item[:top]
            out[key] = compact(item, top)
        return out
    if isinstance(value, list):
        return [compact(item, top) for item in value]
    return _round(value)


def hierarchy_lines(attribution: dict[str, Any], n: int = 10) -> list[str]:
    """Each hierarchy's share of downtime: the outages started by anything
    inside it, at any depth, pooled over the runs (so ± is its own)."""
    lines = ["By hierarchy (everything inside it, at any depth):",
             f"{'Hierarchy':<22}{'Downtime':>10}{'±':>8}{'Inside':>8}  Main cause"]
    for row in (attribution.get("hierarchies") or [])[:n]:
        sem = row["dtci"]["sem"]
        indent = "  " * max(0, int(row.get("depth") or 1) - 1)
        lines.append(f"{(indent + row['name'])[:21]:<22}{_pct(row['dtci']['mean']):>10}"
                     f"{(_pct(sem) if sem is not None else ''):>8}{row['elements']:>8}  "
                     f"{CAUSE_LABELS.get(row['main_cause'] or '', '')}")
    return lines


def phase_lines(attribution: dict[str, Any]) -> list[str]:
    """Downtime by mission phase: availability in each phase, its share of
    the downtime and the element that starts most of it."""
    units = attribution.get("units") or ""
    lines = ["Downtime by phase:",
             f"{'Phase':<24}{'Mission':>8}{'Time':>12}{'Down':>10}{'Avail.':>10}"
             f"{'Share':>8}  Main driver"]
    for row in attribution.get("phases") or []:
        mission = {True: "yes", False: "no"}.get(row.get("mission"), "")
        driver = row["drivers"][0] if row.get("drivers") else None
        lead = f"{driver['name']} ({_pct(driver['share'])})" if driver else ""
        lines.append(
            f"{row['name'][:23]:<24}{mission:>8}"
            f"{row['time']['mean'] or 0.0:>12,.4g}{row['red_time']['mean'] or 0.0:>10,.4g}"
            f"{_avail(row['availability']):>10}{_pct(row['share_of_downtime']):>8}  {lead}")
    lines.append(f"(time and down time are means per run, in {units or 'model units'})")
    return lines


def period_lines(summary: dict[str, Any]) -> list[str]:
    """The period (yearly) results as plain text."""
    pooled = summary["pooled"]
    lines = [summary["sentence"], ""]
    if not pooled.get("n"):
        return lines
    lines.append(f"{pooled['n']:,} periods of {summary['length']:,.6g} {summary.get('units') or ''}"
                 f" from {summary['runs']:,} runs"
                 + (f" ({summary['dropped_partial_periods']:,} part periods left out)"
                    if summary["dropped_partial_periods"] else ""))
    lines.append("Pooled:  " + "  ".join(
        f"P{p} {_avail(pooled[f'p{p}'])}" for p in (1, 5, 10, 50, 90, 95, 99)))
    lines += ["", f"{'Period':>6}{'Mean':>10}{'P5':>10}{'P50':>10}{'P95':>10}"
              + (f"{'Below':>10}" if summary.get("threshold") is not None else "")]
    for row in summary["by_index"]:
        if not row.get("n"):
            continue
        lines.append(f"{row['index']:>6}{_avail(row['mean']):>10}{_avail(row['p5']):>10}"
                     f"{_avail(row['p50']):>10}{_avail(row['p95']):>10}"
                     + (f"{_avail(row['p_below']):>10}" if "p_below" in row else ""))
    worst = summary["worst"][:3]
    if worst:
        lines += ["", "Worst: " + ", ".join(
            f"{_avail(w['value'])} (run {w['run']}, period {w['index']})" for w in worst)]
    capacity = summary.get("capacity")
    if capacity and (capacity.get("pooled") or {}).get("n"):
        cp = capacity["pooled"]
        lines += ["", "Capacity-weighted availability (energy delivered / flow "
                      "generated), same periods:",
                  "Pooled:  " + "  ".join(f"P{p} {_avail(cp[f'p{p}'])}"
                                          for p in (1, 5, 10, 50, 90, 95, 99))]
        cw = capacity["worst"][:3]
        if cw:
            lines.append("Worst: " + ", ".join(
                f"{_avail(w['value'])} (run {w['run']}, period {w['index']})"
                for w in cw))
    return lines


def _avail(x: float | None) -> str:
    """An availability to enough digits to tell 99.991 % from 100 %."""
    return "" if x is None else f"{100 * x:.5g} %"
