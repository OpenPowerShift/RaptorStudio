"""Reading a stored attribution: the downtime drivers, in words and numbers."""
from __future__ import annotations

import json
from typing import Any

from raptor.api.insight.attribution import CAUSE_LABELS


class NoAttribution(LookupError):
    """The run was made without downtime attribution."""


def read(workspace: Any, run_id: str) -> dict[str, Any]:
    """The run's stored attribution. Raises `NoAttribution` when it has none."""
    from raptor.api.run import run_dir
    path = run_dir(workspace, run_id) / "attribution.json"
    if not path.is_file():
        raise NoAttribution(
            f"run {run_id!r} has no downtime attribution; run it again with attribution on")
    return json.loads(path.read_text())


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
    return lines
