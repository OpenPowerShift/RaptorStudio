"""Resource (crew and equipment) rules.

The load estimate is offered work divided by capacity: for each block that
uses the resource, failures per unit time x mean repair time x units needed
(plus the same for preventive maintenance), over the number of units.
"""
from __future__ import annotations

import math
from collections.abc import Iterable

from raptor.api.advisor import text
from raptor.api.advisor.context import ModelContext, on
from raptor.api.advisor.finding import Finding, Target
from raptor.api.advisor.registry import rule

BUSY_LOAD = 0.7
TARGET_LOAD = 0.7


def offered_load(ctx: ModelContext, members: list[dict]) -> float:
    """Units of the resource busy on average, if nothing ever waited."""
    work = 0.0
    for b in members:
        rate, repair = ctx.failure_rate(b), ctx.mean_repair(b)
        if rate and repair:
            work += rate * repair * int(b.get("repair_resources_needed") or 1)
        if on(b.get("uses_pm")) and float(b.get("pm_freq") or 0) > 0:
            dur = ctx.mean_pm(b) or 0.0
            work += dur / float(b["pm_freq"]) * int(b.get("pm_resources_needed") or 1)
    return work


@rule("M-RES-001", title="More units needed than exist",
      category="resources", basis="exact", severity="error")
def impossible_repair(ctx: ModelContext) -> Iterable[Finding]:
    for res in ctx.resources:
        name, have = str(res.get("name")), int(res.get("num_in") or 0)
        for b in ctx.resource_members.get(name, []):
            need_rep = int(b.get("repair_resources_needed") or 0)
            need_pm = int(b.get("pm_resources_needed") or 0) if on(b.get("uses_pm")) else 0
            need = max(need_rep, need_pm)
            if need > have:
                what = "a repair" if need_rep >= need_pm else "preventive maintenance"
                yield Finding(
                    rule="M-RES-001", severity="error", category="resources",
                    title="More units needed than exist",
                    message=(f"{b['name']} needs {need} of {name} for {what} but only "
                             f"{have} exist: the work can never start."),
                    why="A task waits until every unit it needs is free at once. "
                        "If it needs more than the total, it waits for ever.",
                    targets=(ctx.block_target(b), Target("resource", None, name, "num_in")),
                    evidence={"needed": need, "available": have},
                    basis="exact", impact=1.0, doc="advisor.html#M-RES-001")


@rule("M-RES-002", title="Crew or equipment overloaded",
      category="resources", basis="heuristic", severity="warning")
def overloaded(ctx: ModelContext) -> Iterable[Finding]:
    for res in ctx.resources:
        name, have = str(res.get("name")), int(res.get("num_in") or 0)
        members = ctx.resource_members.get(name, [])
        if not members or have <= 0:
            continue
        work = offered_load(ctx, members)
        load = work / have
        if load < BUSY_LOAD:
            continue
        suggested = max(have + 1, math.ceil(work / TARGET_LOAD))
        over = load >= 1.0
        yield Finding(
            rule="M-RES-002" if over else "M-RES-003",
            severity="warning" if over else "advice", category="resources",
            title="Crew or equipment overloaded" if over else "Crew or equipment busy",
            message=(f"{name} is overloaded: the work needs {text.fmt_num(load)} times the "
                     f"{have} available, so the repair queue grows without limit."
                     if over else
                     f"{name} will be busy about {text.fmt_pct(load)} of the time: "
                     f"repairs will often wait for it."),
            why="The estimate adds up, for every block that uses this resource, how "
                "often it needs work and for how long. Above about 70 % waiting "
                "becomes common; at 100 % the work can never catch up.",
            targets=(Target("resource", None, name, "num_in"),),
            evidence={"load": load, "units": have, "work": work,
                      "suggested_units": suggested, "blocks": len(members)},
            basis="heuristic", impact=min(1.0, load),
            doc="advisor.html#" + ("M-RES-002" if over else "M-RES-003"))


@rule("M-RES-003", title="Crew or equipment busy",
      category="resources", basis="heuristic", severity="advice")
def busy(ctx: ModelContext) -> Iterable[Finding]:
    return ()          # reported by `overloaded`, which shares the estimate


@rule("M-RES-005", title="Resource not used",
      category="resources", basis="exact", severity="advice")
def unused_resource(ctx: ModelContext) -> Iterable[Finding]:
    for res in ctx.resources:
        name = str(res.get("name"))
        if not ctx.resource_members.get(name):
            yield Finding(
                rule="M-RES-005", severity="advice", category="resources",
                title="Resource not used",
                message=f"{name} is defined but no block uses it.",
                why="A resource only matters when a block's repair or preventive "
                    "maintenance names it.",
                targets=(Target("resource", None, name),), basis="exact", impact=0.1,
                doc="advisor.html#M-RES-005")
