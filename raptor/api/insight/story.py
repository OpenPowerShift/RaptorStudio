"""The Story: answer first, evidence second, raw data third.

One Qt-free object built from a finished batch.  The GUI paints it, the
command line prints it, the MCP server returns it and the report embeds it,
so the four surfaces cannot tell different stories.

The order is deliberate (plan P1, section 7.3): the headline against the
target, the distribution of outcomes, what drives the loss, what to look at,
then the key numbers and provenance.  A section appears only when the model
uses that feature (complexity rule C1).
"""
from __future__ import annotations

import math
from dataclasses import asdict, dataclass
from typing import Any

from raptor.api.insight.attribution import CAUSE_LABELS
from raptor.api.insight.dots import quantile_dots, proportion_text, split_at
from raptor.api.insight.drivers import top as top_drivers

#: Requirement metric -> the per-run field that carries it.
REQUIREMENT_FIELDS = {
    "system.ao": "availability",
    "system.availability": "availability",
    "system.reliability": "reliability",
    "system.dependability": "dependability",
    "system.capacity_weighted_availability": "capacity_weighted_availability",
    "system.mtbde": "mtbde",
    "system.mdt": "mdt",
    "system.mtbm": "mtbm",
    "system.capacity": "capacity_weighted_availability",
}
REQUIREMENT_LABELS = {
    "availability": "Availability",
    "reliability": "Reliability",
    "dependability": "Dependability",
    "capacity_weighted_availability": "Delivered fraction",
    "mtbde": "Time between failures",
    "mdt": "Time down per outage",
    "mtbm": "Time between maintenance",
}

#: Which metrics are fractions (shown as a percentage) and which are durations.
FRACTION_FIELDS = {"availability", "reliability", "dependability",
                   "capacity_weighted_availability"}
TIME_FIELDS = {"mtbde", "mdt", "mtbm", "mttff", "total_time", "uptime",
               "downtime"}


@dataclass(frozen=True)
class StoryHeadline:
    field: str
    label: str
    value: float | None
    target: float | None
    op: str | None
    met: bool | None
    target_text: str
    gap: float | None
    gap_text: str
    value_text: str
    units: str
    sentence: str = ""


@dataclass(frozen=True)
class DriverLine:
    name: str
    kind: str
    id: int
    share: float
    downtime_share: float
    causes: dict[str, float]
    red_time: float


@dataclass(frozen=True)
class NumberChip:
    label: str
    value: float | None
    units: str
    field: str


@dataclass(frozen=True)
class StorySection:
    key: str
    title: str
    line: str
    action: str = ""


@dataclass(frozen=True)
class StoryFinding:
    rule: str
    severity: str
    title: str
    message: str


@dataclass
class Story:
    headline: StoryHeadline | None
    dots: list[dict[str, Any]]
    dot_target: float | None
    met: int
    total: int
    proportion: str
    drivers: list[DriverLine]
    drivers_available: bool
    other_drivers_share: float
    others_time: float
    findings: list[StoryFinding]
    numbers: list[NumberChip]
    sections: list[StorySection]
    provenance: dict[str, Any]
    caption: str
    units: str = ""

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def _mean(values: list[float]) -> float | None:
    present = [v for v in values if v is not None and math.isfinite(v)]
    return sum(present) / len(present) if present else None


def _values(per_run: list[dict], field_name: str) -> list[float]:
    out = []
    for row in per_run:
        value = row.get(field_name)
        if isinstance(value, (int, float)) and not isinstance(value, bool):
            if math.isfinite(float(value)):
                out.append(float(value))
    return out


def _fraction_digits(value: float) -> int:
    """Significant digits for a fraction shown as a percentage: enough to
    keep two digits of the shortfall, so 0.99986 reads 99.986 %, not 99.99 %."""
    import math
    shortfall_pct = 100.0 * (1.0 - value)
    if 0.0 < shortfall_pct < 1.0:
        decimals = 1 - math.floor(math.log10(shortfall_pct))
        return min(9, 2 + decimals)
    return 4


def _fmt(value: float | None, field_name: str, units: str) -> str:
    if value is None:
        return "not available"
    if field_name in FRACTION_FIELDS:
        return f"{100 * value:.{_fraction_digits(value)}g} %"
    if field_name in TIME_FIELDS:
        return f"{value:,.4g}" + (f" {units}" if units else "")
    if field_name == "grand_total":
        # money: whole units with separators, never 1.933e+05
        return f"{value:,.0f}" if abs(value) >= 100 else f"{value:,.2f}"
    return f"{value:,.4g}" + (f" {units}" if units else "")


def _meets(value: float | None, op: str | None, target: float | None) -> bool | None:
    if value is None or target is None or not op:
        return None
    return {
        ">=": value >= target, ">": value > target,
        "<=": value <= target, "<": value < target,
        "==": abs(value - target) < 1e-12, "!=": abs(value - target) >= 1e-12,
    }.get(op)


def _headline(per_run: list[dict], units: str, requirements: list[dict],
              has_capacity: bool, has_phases: bool) -> StoryHeadline | None:
    if per_run:
        if requirements:
            req = requirements[0]
            field_name = REQUIREMENT_FIELDS.get(str(req.get("metric", "")))
            if field_name:
                return _headline_for(
                    per_run, units, field_name, str(req.get("label") or ""),
                    float(req["value"]) if req.get("value") is not None else None,
                    str(req.get("op") or ">="))
        if has_capacity:
            values = _values(per_run, "capacity_weighted_availability")
            if values:
                return _headline_for(per_run, units,
                                     "capacity_weighted_availability", "", None, None)
        if has_phases:
            values = _values(per_run, "reliability")
            if values:
                return _headline_for(per_run, units, "reliability", "", None, None)
        return _headline_for(per_run, units, "availability", "", None, None)
    return None


def _headline_for(per_run: list[dict], units: str, field_name: str,
                  custom_label: str, target: float | None,
                  op: str | None) -> StoryHeadline:
    values = _values(per_run, field_name)
    value = _mean(values)
    label = custom_label or REQUIREMENT_LABELS.get(field_name, field_name)
    met = _meets(value, op, target)
    target_text = ""
    gap_text = ""
    if target is not None:
        target_text = f"{label} {op or '>='} {_fmt(target, field_name, units)}"
        if value is not None:
            if field_name in FRACTION_FIELDS:
                points = 100.0 * abs(value - target)
                gap_text = f"{points:.3g} points"
                # a year is 8,760 hours whatever the run length
                if units.lower().startswith("hour"):
                    gap_text += f" = {points / 100 * 8760:,.3g} h a year"
            else:
                gap_text = _fmt(abs(value - target), field_name, units)
            gap_text = ("met" if met else "missed by") + (
                "" if met else f" {gap_text}")
    gap = (value - target) if value is not None and target is not None else None
    return StoryHeadline(
        field=field_name, label=label, value=value, target=target, op=op,
        met=met, target_text=target_text, gap=gap, gap_text=gap_text,
        value_text=_fmt(value, field_name, units), units=units,
        sentence=(f"{label} is {_fmt(value, field_name, units)}"
                  + (f"; the target is {op or '>='} {_fmt(target, field_name, units)}"
                     f" ({'met' if met else 'missed'})" if target is not None else "")
                  + "."))


def _driver_lines(attribution: dict | None, has_capacity: bool
                  ) -> tuple[list[DriverLine], float, float]:
    if not attribution:
        return [], 0.0, 0.0
    downtime = {(row["kind"], row["id"]): row
                for row in attribution.get("elements") or []}
    if has_capacity and (attribution.get("capacity") or {}).get("elements"):
        ranked = sorted(attribution["capacity"]["elements"],
                        key=lambda r: -(r["share"]["mean"] or 0.0))
        lines = []
        for row in ranked[:3]:
            base = downtime.get((row["kind"], row["id"])) or {}
            lines.append(DriverLine(
                name=row["name"], kind=row["kind"], id=int(row["id"]),
                share=row["share"]["mean"] or 0.0,
                downtime_share=(base.get("dtci") or {}).get("mean") or 0.0,
                causes=base.get("causes") or {},
                red_time=(base.get("red_time") or {}).get("mean") or 0.0))
        return lines, max(0.0, 1.0 - sum(line.share for line in lines)), 0.0
    rows = top_drivers(attribution, 3)
    lines = [DriverLine(
        name=row["name"], kind=row["kind"], id=int(row["id"]),
        share=row["dtci"]["mean"] or 0.0,
        downtime_share=row["dtci"]["mean"] or 0.0,
        causes=row["causes"], red_time=row["red_time"]["mean"] or 0.0)
        for row in rows]
    red = (attribution.get("system") or {}).get("red_time", {}).get("mean") or 0.0
    return (lines, max(0.0, 1.0 - sum(line.share for line in lines)),
            max(0.0, red - sum(line.red_time for line in lines)))


def _finding_list(findings: list[Any]) -> list[StoryFinding]:
    out = []
    for finding in findings[:3]:
        if isinstance(finding, dict):
            out.append(StoryFinding(
                rule=str(finding.get("rule", "")),
                severity=str(finding.get("severity", "")),
                title=str(finding.get("title", "")),
                message=str(finding.get("message", ""))))
        else:
            out.append(StoryFinding(
                rule=str(getattr(finding, "rule", "")),
                severity=str(getattr(finding, "severity", "")),
                title=str(getattr(finding, "title", "")),
                message=str(getattr(finding, "message", ""))))
    return out


_METRIC_CHOICES = (
    ("availability", "Availability"),
    ("reliability", "Reliability"),
    ("mtbde", "Time between failures"),
    ("mdt", "Time down per outage"),
    ("capacity_weighted_availability", "Delivered fraction"),
    ("grand_total", "Grand total cost"),
)


def _chips(per_run: list[dict], units: str, has_capacity: bool,
           cost_on: bool, headline_field: str | None = None) -> list[NumberChip]:
    out = []
    choices = list(_METRIC_CHOICES)
    if headline_field:
        choices.sort(key=lambda item: 0 if item[0] == headline_field else 1)
    for field_name, label in choices:
        if field_name == "capacity_weighted_availability" and not has_capacity:
            continue
        if field_name == "grand_total" and not cost_on:
            continue
        value = _mean(_values(per_run, field_name))
        if value is None:
            continue
        out.append(NumberChip(label=label, value=value, units=units,
                              field=field_name))
    return out[:6]


def _sections(per_run: list[dict], attribution: dict | None, has_capacity: bool,
              cost_on: bool) -> list[StorySection]:
    out: list[StorySection] = []
    causes = (attribution or {}).get("causes") or {}
    if attribution:
        for cause, key, title, action in (
                ("wait_spare", "spares", "Spares",
                 "Stock one more at the pool that waited"),
                ("wait_resource", "resources", "People and equipment",
                 "Add a crew or equipment unit"),
                ("pm", "pm", "Planned maintenance",
                 "Compare a longer PM interval")):
            if causes.get(cause, 0.0) > 0:
                out.append(StorySection(
                    key, title,
                    f"{CAUSE_LABELS[cause]} causes "
                    f"{100 * causes[cause]:.3g} % of system downtime.",
                    action))
    if has_capacity:
        capacity = (attribution or {}).get("capacity") or {}
        rows = capacity.get("elements") or []
        top = rows[0] if rows else None
        out.append(StorySection(
            "capacity", "Capacity",
            "The runs delivered "
            f"{100 * (_mean(_values(per_run, 'capacity_weighted_availability')) or 0):.3g} %"
            " of demand"
            + (f"; {top['name']} loses the most capacity." if top else "."),
            "Show the lost-capacity drivers"))
    if cost_on:
        total = _mean(_values(per_run, "grand_total"))
        out.append(StorySection(
            "cost", "Cost", f"The batch costs {total:,.4g} in total."
            if total is not None else "No cost total was produced.",
            "Show the cost drivers"))
    return out


def _caption(headline: StoryHeadline | None, drivers: list[DriverLine],
             attribution: dict | None) -> str:
    parts = []
    if headline is not None:
        line = f"{headline.label} {headline.value_text}"
        if headline.target_text:
            line += f" against {headline.target_text}"
            if headline.met is False:
                line += f"; missed by {headline.gap_text.removeprefix('missed by ')}"
            elif headline.met:
                line += "; met"
        parts.append(line + ".")
    if drivers:
        parts.append(
            f"{drivers[0].name} causes {100 * drivers[0].share:.3g} % of "
            + ("lost capacity." if (attribution or {}).get("capacity")
               else "the loss."))
    return " ".join(parts) or "No result to summarise."


def build(
    per_run: list[dict],
    *,
    units: str = "",
    attribution: dict | None = None,
    findings: list[Any] | None = None,
    requirements: list[dict] | None = None,
    has_capacity: bool | None = None,
    has_phases: bool | None = None,
    phased: bool | None = None,
    cost_on: bool | None = None,
    model_name: str = "",
    run_id: str = "",
    model_hash: str = "",
) -> Story:
    """Build the Story from a finished batch's per-run dicts.

    `per_run` is `raptor.api.run._rd_to_dict` rows (plain, JSON-safe).  The
    caller decides `has_capacity`/`has_phases`/`cost_on` when it knows the
    model better; left out, they are inferred from the rows.
    """
    requirements = list(requirements or [])
    if has_capacity is None:
        has_capacity = any(row.get("capacity_weighted_availability") is not None
                           for row in per_run)
    if has_phases is None:
        has_phases = (phased if phased is not None else
                      any(bool(row.get("has_phases")) for row in per_run))
    if cost_on is None:
        cost_on = any(row.get("grand_total") is not None for row in per_run)
    headline = _headline(per_run, units, requirements, bool(has_capacity),
                         bool(has_phases))
    values = _values(per_run, headline.field) if headline else []
    dots = quantile_dots(values, 40)
    target = headline.target if headline else None
    met, total = split_at(values, target)
    drivers, others_share, others_time = _driver_lines(attribution,
                                                       bool(has_capacity))
    run_length = _mean(_values(per_run, "total_time"))
    provenance = {
        "runs": len(per_run),
        "run_length": run_length,
        "units": units,
        "model_name": model_name,
        "model_hash": model_hash,
        "run_id": run_id,
        "has_capacity": bool(has_capacity),
        "has_phases": bool(has_phases),
    }
    return Story(
        headline=headline, dots=dots, dot_target=target, met=met, total=total,
        proportion=proportion_text(met, total) if target is not None else "",
        drivers=drivers, drivers_available=attribution is not None,
        other_drivers_share=others_share, others_time=others_time,
        findings=_finding_list(list(findings or [])),
        numbers=_chips(per_run, units, bool(has_capacity), bool(cost_on),
                        headline.field if headline else None),
        sections=_sections(per_run, attribution, bool(has_capacity),
                           bool(cost_on)),
        provenance=provenance,
        caption=_caption(headline, drivers, attribution),
        units=units)


def from_run(workspace: Any, run_id: str) -> Story:
    """The Story for a stored run, from its summary, per-run rows and
    attribution.  One implementation for the CLI, MCP and the report."""
    from raptor.api.advisor import advise_run
    from raptor.api.insight import drivers
    from raptor.api.run import run_dir
    from raptor.api.run_tables import read_per_run, read_summary

    runs_dir = run_dir(workspace, run_id)
    summary = read_summary(runs_dir, run_id, required=True)
    per_run = read_per_run(runs_dir, run_id) or []
    try:
        attribution = drivers.read(workspace, run_id)
    except drivers.NoAttribution:
        attribution = None
    rbd = workspace.get(summary["model_id"]).rbd
    findings = advise_run(rbd, summary, per_run, attribution=attribution)
    return build(
        per_run, units=str(summary.get("units") or ""),
        attribution=attribution, findings=findings,
        has_phases=any(bool(row.get("has_phases")) for row in per_run),
        cost_on=(summary.get("analyses") or {}).get("cost", False),
        model_name=str(summary.get("model_name") or ""),
        model_hash=str(summary.get("model_hash") or ""),
        run_id=run_id)


def text_lines(story: Story) -> list[str]:
    """The Story as plain text (the command line's `explain --story`)."""
    lines: list[str] = []
    head = story.headline
    if head is not None:
        lines.append(f"{head.target_text or head.label}: {head.value_text}"
                     + (f"  ({story.proportion})" if story.proportion else ""))
        if head.target_text and head.met is not None:
            lines.append(head.gap_text)
    if story.drivers:
        lines += ["", "Where the loss comes from:"]
        for line in story.drivers:
            lines.append(f"  {line.name:<22}{100 * line.share:>7.3g} %")
        if story.other_drivers_share > 0:
            lines.append(f"  {'others':<22}{100 * story.other_drivers_share:>7.3g} %")
    if story.findings:
        lines += ["", "What to look at:"]
        for finding in story.findings:
            lines.append(f"  [{finding.severity}] {finding.message}")
    if story.numbers:
        lines += ["", "Key numbers:"]
        for chip in story.numbers:
            lines.append(f"  {chip.label:<26}{_fmt(chip.value, chip.field, chip.units)}")
    prov = story.provenance
    lines += ["", f"{prov['runs']:,} runs"
                  + (f" · {prov['run_length']:,.4g} {prov['units']} each"
                     if prov.get("run_length") else "")
                  + f" · model {str(prov.get('model_hash') or '')[:8]}"
                  + f" · run {prov.get('run_id') or ''}"]
    return lines
