"""Reading a stored attribution: the downtime drivers, in words and numbers."""
from __future__ import annotations

import json
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
            partial: str = "drop") -> dict[str, Any]:
    """Availability period by period for a run made with attribution.
    `length` defaults to a year when the model's unit is hours."""
    from raptor.api.insight.periods import HOURS_PER_YEAR, PeriodSpec, from_outages, sentence
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
    summary = from_outages(json.loads(path.read_text()),
                           PeriodSpec(float(length), float(offset), partial),  # type: ignore[arg-type]
                           threshold=below)
    summary["units"] = units
    summary["run_id"] = run_id
    summary["sentence"] = sentence(summary, units)
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
    capacity = attribution.get("capacity")
    if capacity:
        from raptor.api.insight.capacity_attribution import text_lines as capacity_lines
        lines += [""] + capacity_lines(capacity, n)
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
        f"P{p} {_pct(pooled[f'p{p}'])}" for p in (1, 5, 10, 50, 90, 95, 99)))
    lines += ["", f"{'Period':>6}{'Mean':>10}{'P5':>10}{'P50':>10}{'P95':>10}"
              + (f"{'Below':>10}" if summary.get("threshold") is not None else "")]
    for row in summary["by_index"]:
        if not row.get("n"):
            continue
        lines.append(f"{row['index']:>6}{_pct(row['mean']):>10}{_pct(row['p5']):>10}"
                     f"{_pct(row['p50']):>10}{_pct(row['p95']):>10}"
                     + (f"{_pct(row['p_below']):>10}" if "p_below" in row else ""))
    worst = summary["worst"][:3]
    if worst:
        lines += ["", "Worst: " + ", ".join(
            f"{_pct(w['value'])} (run {w['run']}, period {w['index']})" for w in worst)]
    return lines
