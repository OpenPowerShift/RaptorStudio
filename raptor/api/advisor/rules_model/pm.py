"""Preventive maintenance rules."""
from __future__ import annotations

from collections.abc import Iterable

from raptor.api.advisor import text
from raptor.api.advisor.context import ModelContext, on
from raptor.api.advisor.finding import Finding, Fix
from raptor.api.advisor.registry import rule

#: A PM interval this many times the mean life is rarely reached.
RARELY_REACHED = 3.0


def _interval_pm(b: dict) -> bool:
    return on(b.get("uses_pm")) and not on(b.get("pm_triggered"))


@rule("M-PM-001", title="Maintenance never happens",
      category="pm", basis="exact", severity="warning")
def pm_beyond_run(ctx: ModelContext) -> Iterable[Finding]:
    run = ctx.run_length
    if not run:
        return
    for b in ctx.blocks:
        if not _interval_pm(b):
            continue
        first = float(b.get("pm_freq") or 0) - float(b.get("pm_stagger_time") or 0)
        if first > run:
            yield Finding(
                rule="M-PM-001", severity="warning", category="pm",
                title="Maintenance never happens",
                message=(f"{b['name']}'s first preventive maintenance is due after "
                         f"{text.fmt_time(first, ctx.units)} of operation, beyond the run "
                         f"length ({text.fmt_time(run, ctx.units)}): it never happens."),
                why="The maintenance clock counts operating time. If the first one "
                    "is due after the run ends, the setting has no effect on the "
                    "results.",
                targets=(ctx.block_target(b, "pm_freq"),),
                evidence={"first_pm": first, "run_length": run},
                basis="exact", impact=0.4, doc="advisor.html#M-PM-001")


@rule("M-PM-003", title="Redundant blocks maintained together",
      category="pm", basis="heuristic", severity="advice")
def synchronised_pm(ctx: ModelContext) -> Iterable[Finding]:
    for group in ctx.groups:
        if group.k >= group.n:
            continue
        by_schedule: dict[tuple[float, float], list[dict]] = {}
        for kind, bid in group.members:
            b = ctx.block_by_id.get(bid) if kind == "block" else None
            if b is not None and _interval_pm(b):
                key = (float(b.get("pm_freq") or 0), float(b.get("pm_stagger_time") or 0))
                by_schedule.setdefault(key, []).append(b)
        for (freq, _stagger), same in by_schedule.items():
            if len(same) < 2 or freq <= 0:
                continue
            spare_paths = group.n - group.k
            loses = len(same) > spare_paths
            fixes = tuple(
                f"block:{int(b['id'])}.pm_stagger_time={text.fmt_num(i * freq / len(same), 6).replace(',', '')}"
                for i, b in enumerate(same) if i > 0)
            yield Finding(
                rule="M-PM-003", severity="advice", category="pm",
                title="Redundant blocks maintained together",
                message=(f"{text.plural(len(same), 'redundant block')} at "
                         f"{group.node['name']} ({text.join_names([str(b['name']) for b in same])}) "
                         f"are all due for maintenance at the same time"
                         + (": the node goes down during maintenance."
                            if loses else ": the redundancy is reduced during maintenance.")),
                why="Blocks with the same interval and the same stagger are maintained "
                    "together. Spreading the first maintenance of each across the "
                    "interval keeps the others in service.",
                targets=tuple(ctx.block_target(b, "pm_stagger_time") for b in same),
                evidence={"blocks": len(same), "pm_freq": freq, "k": group.k, "n": group.n},
                basis="heuristic", impact=0.6 if loses else 0.3,
                fixes=(Fix("Stagger them evenly", fixes),),
                doc="advisor.html#M-PM-003")


@rule("M-PM-004", title="Maintenance interval longer than the life",
      category="pm", basis="exact", severity="advice")
def pm_rarely_reached(ctx: ModelContext) -> Iterable[Finding]:
    for b in ctx.blocks:
        if not _interval_pm(b) or ctx.hazard_shape(b) != "increasing":
            continue
        life, freq = ctx.mean_life(b), float(b.get("pm_freq") or 0)
        if life and freq > RARELY_REACHED * life:
            yield Finding(
                rule="M-PM-004", severity="advice", category="pm",
                title="Maintenance interval longer than the life",
                message=(f"{b['name']} is maintained every {text.fmt_time(freq, ctx.units)} but "
                         f"lasts {text.fmt_time(life, ctx.units)} on average: most units "
                         f"fail before their maintenance is due."),
                why="Preventive maintenance helps a wearing-out item only if it "
                    "comes before most failures. An interval several times the mean "
                    "life is rarely reached.",
                targets=(ctx.block_target(b, "pm_freq"),),
                evidence={"pm_freq": freq, "mean_life": life},
                basis="exact", impact=0.3, doc="advisor.html#M-PM-004")
