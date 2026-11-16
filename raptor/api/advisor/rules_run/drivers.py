"""After a run: what drives the downtime, and which lever fits.

These read the downtime attribution (`raptor.api.insight.attribution`), so
they are silent for a run made without it.
"""
from __future__ import annotations

import math
from collections.abc import Iterable

from raptor.api.advisor import text
from raptor.api.advisor.context import RunContext
from raptor.api.advisor.finding import Finding, Target
from raptor.api.advisor.registry import rule
from raptor.api.insight.attribution import CAUSE_LABELS
from raptor.api.insight.drivers import top

#: Name enough elements to cover this share of downtime (at most five).
COVER = 0.80
#: A cause holding at least this share of an element's outages is "the" lever.
MAIN_CAUSE = 0.40
#: A cause holding at least this share of ALL downtime gets its own finding.
SYSTEM_CAUSE = 0.10

_LEVER = {
    "repair": "Shorter repairs help most.",
    "wait_spare": "More stock or faster delivery helps most.",
    "wait_resource": "More people or equipment, or fewer jobs competing for them, helps most.",
    "logistics": "Cutting the delay before and after the repair helps most.",
    "pm": "Less frequent, shorter or better-timed maintenance helps most.",
    "pm_wait": "Having what maintenance needs ready on time helps most.",
    "dependency": "Look at what it depends on: that element holds it down.",
    "done": "It is never repaired: a spare or a repair path is needed.",
}


def _target(row: dict) -> tuple[Target, ...]:
    if row["kind"] in ("block", "event", "node"):
        return (Target(row["kind"], int(row["id"]), row["name"]),)
    return ()


def _share(row: dict) -> float:
    return row["dtci"]["mean"] or 0.0


@rule("R-DRV-001", title="A few elements cause most of the downtime",
      category="drivers", basis="statistical", severity="info", needs=("model", "run"))
def top_contributors(ctx: RunContext) -> Iterable[Finding]:
    if not ctx.attribution:
        return
    rows = top(ctx.attribution, 50)
    if not rows:
        return
    picked, covered = [], 0.0
    for row in rows[:5]:
        picked.append(row)
        covered += _share(row)
        if covered >= COVER:
            break
    listed = ", ".join(f"{r['name']} {text.fmt_pct(_share(r))}" for r in picked)
    total = len(ctx.blocks) + len(ctx.events)
    yield Finding(
        rule="R-DRV-001", severity="info", category="drivers",
        title="A few elements cause most of the downtime",
        message=(f"{len(picked)} of {total} elements cause {text.fmt_pct(covered)} of system "
                 f"downtime: {listed}."),
        why="Each system outage is charged to the element that started it. The "
            "shares are averages over the runs that had an outage. They describe "
            "this design as it is; they do not say what a change would achieve.",
        targets=tuple(t for r in picked for t in _target(r)),
        evidence={"elements": len(picked), "of": total, "share": covered,
                  "runs_with_outage": ctx.attribution["runs_with_outage"]},
        basis="statistical", confidence=0.9, impact=covered,
        doc="advisor.html#R-DRV-001")


@rule("R-DRV-002", title="Where a top contributor's time goes",
      category="drivers", basis="statistical", severity="advice", needs=("model", "run"))
def main_cause(ctx: RunContext) -> Iterable[Finding]:
    if not ctx.attribution:
        return
    units = ctx.attribution.get("units") or ctx.units
    for row in top(ctx.attribution, 3):
        cause = row["main_cause"]
        part = row["causes"].get(cause, 0.0) if cause else 0.0
        if not cause or part < MAIN_CAUSE or cause == "other":
            continue
        hours = row["red_time"]["mean"] or 0.0
        yield Finding(
            rule="R-DRV-002", severity="advice", category="drivers",
            title="Where a top contributor's time goes",
            message=(f"{row['name']}: {text.fmt_pct(part)} of the "
                     f"{text.fmt_time(hours, units)} of system downtime it starts each run is "
                     f"{CAUSE_LABELS[cause].lower()}. {_LEVER[cause]}"),
            why="The time of each outage is split by what the system was waiting "
                "for at each moment: the repair itself, a spare, a crew, a "
                "logistics delay, planned maintenance, or another element.",
            targets=_target(row),
            evidence={"cause": cause, "cause_share": part, "downtime_share": _share(row),
                      "downtime_per_run": hours},
            basis="statistical", confidence=0.9, impact=_share(row) * part,
            doc="advisor.html#R-DRV-002")


@rule("R-DRV-006", title="The top two cannot be told apart",
      category="drivers", basis="statistical", severity="info", needs=("model", "run"))
def not_separable(ctx: RunContext) -> Iterable[Finding]:
    if not ctx.attribution:
        return
    rows = top(ctx.attribution, 2)
    if len(rows) < 2:
        return
    a, b = rows
    sa, sb = a["dtci"]["sem"], b["dtci"]["sem"]
    if not sa or not sb:
        return
    gap = _share(a) - _share(b)
    pooled = math.sqrt(sa * sa + sb * sb)
    if gap >= 2 * pooled:
        return
    yield Finding(
        rule="R-DRV-006", severity="info", category="drivers",
        title="The top two cannot be told apart",
        message=(f"{a['name']} ({text.fmt_pct(_share(a))} ± {text.fmt_pct(sa)}) and "
                 f"{b['name']} ({text.fmt_pct(_share(b))} ± {text.fmt_pct(sb)}) cannot be "
                 f"ranked at this run count."),
        why="The difference between their shares is smaller than twice its "
            "sampling error, so another batch could put them the other way round. "
            "More runs will separate them if they really differ.",
        targets=_target(a) + _target(b),
        evidence={"gap": gap, "pooled_sem": pooled},
        basis="statistical", confidence=0.95, impact=0.2,
        doc="advisor.html#R-DRV-006")


_SYSTEM_CAUSES = {
    "wait_spare": ("R-SPR-101", "spares", "Waiting for spares costs availability",
                   "Waiting for spares causes {p} of system downtime."),
    "wait_resource": ("R-RES-101", "resources", "Waiting for people or equipment costs availability",
                      "Waiting for people or equipment causes {p} of system downtime."),
    "pm": ("R-PM-101", "pm", "Planned maintenance takes the system down",
           "Planned maintenance causes {p} of system downtime."),
    "logistics": ("R-LOG-101", "drivers", "Logistics delay costs availability",
                  "Logistics delay before and after repairs causes {p} of system downtime."),
}


def _system_cause(ctx: RunContext, cause: str) -> Iterable[Finding]:
    if not ctx.attribution:
        return
    share = (ctx.attribution.get("causes") or {}).get(cause, 0.0)
    if share < SYSTEM_CAUSE:
        return
    rule_id, category, title, pattern = _SYSTEM_CAUSES[cause]
    worst = [r for r in top(ctx.attribution, 50) if r["causes"].get(cause, 0.0) > 0]
    worst.sort(key=lambda r: -(r["red_time"]["mean"] or 0.0) * r["causes"].get(cause, 0.0))
    names = text.join_names([r["name"] for r in worst[:3]])
    yield Finding(
        rule=rule_id, severity="warning" if share >= 0.25 else "advice", category=category,
        title=title,
        message=pattern.format(p=text.fmt_pct(share)) + (f" Mostly at {names}." if names else ""),
        why="This is the share of all system Red time during which the element "
            f"holding the system down was in this state. {_LEVER[cause]}",
        targets=tuple(t for r in worst[:3] for t in _target(r)),
        evidence={"share": share, "cause": cause},
        basis="statistical", confidence=0.9, impact=share,
        doc=f"advisor.html#{rule_id}")


@rule("R-SPR-101", title="Waiting for spares costs availability",
      category="spares", basis="statistical", severity="warning", needs=("model", "run"))
def spares_wait(ctx: RunContext) -> Iterable[Finding]:
    return _system_cause(ctx, "wait_spare")


@rule("R-RES-101", title="Waiting for people or equipment costs availability",
      category="resources", basis="statistical", severity="warning", needs=("model", "run"))
def resource_wait(ctx: RunContext) -> Iterable[Finding]:
    return _system_cause(ctx, "wait_resource")


@rule("R-PM-101", title="Planned maintenance takes the system down",
      category="pm", basis="statistical", severity="advice", needs=("model", "run"))
def pm_downtime(ctx: RunContext) -> Iterable[Finding]:
    return _system_cause(ctx, "pm")


@rule("R-LOG-101", title="Logistics delay costs availability",
      category="drivers", basis="statistical", severity="advice", needs=("model", "run"))
def logistics_delay(ctx: RunContext) -> Iterable[Finding]:
    return _system_cause(ctx, "logistics")


# ---- capacity (issue #256) --------------------------------------------------

def _capacity_rows(ctx: RunContext) -> list[dict]:
    """Lost-capacity rows with their downtime share attached.

    The capacity block holds the capacity figures; the element rows hold DTCI.
    They are the same element keys, so join them here once.
    """
    attribution = ctx.attribution or {}
    downtime = {(e["kind"], e["id"]): e
                for e in attribution.get("elements") or []}
    rows = []
    for row in (attribution.get("capacity") or {}).get("elements") or []:
        if (row.get("share", {}).get("mean") or 0.0) <= 0.0:
            continue
        merged = dict(row)
        merged["dtci"] = (downtime.get((row["kind"], row["id"])) or {}).get(
            "dtci") or {"mean": 0.0, "sem": None}
        rows.append(merged)
    rows.sort(key=lambda r: -(r["share"]["mean"] or 0.0))
    return rows


@rule("R-CAP-101", title="Some demand is not delivered",
      category="capacity", basis="statistical", severity="info",
      needs=("model", "run"))
def delivered_fraction(ctx: RunContext) -> Iterable[Finding]:
    rows = _capacity_rows(ctx)
    if not rows:
        return
    delivered = ctx.values("capacity_weighted_availability")
    if not delivered:
        return
    mean = sum(delivered) / len(delivered)
    if mean >= 0.9999:
        return
    top = rows[0]
    yield Finding(
        rule="R-CAP-101", severity="info", category="capacity",
        title="Some demand is not delivered",
        message=(f"The runs delivered {text.fmt_pct(mean)} of the flow asked "
                 f"for; {top['name']} loses the most capacity "
                 f"({text.fmt_pct(top['share']['mean'] or 0.0)} of the loss)."),
        why="Capacity-weighted availability is mean delivered flow divided "
            "by the flow generated. Availability alone only says whether "
            "any flow got through.",
        targets=_target(top),
        evidence={"delivered_fraction": mean, "top_element": top["name"],
                  "top_share": top["share"]["mean"]},
        basis="statistical", confidence=0.9,
        impact=1.0 - mean, doc="advisor.html#R-CAP-101")


@rule("R-CAP-102", title="Downtime share and capacity share disagree",
      category="capacity", basis="statistical", severity="advice",
      needs=("model", "run"))
def capacity_vs_downtime(ctx: RunContext) -> Iterable[Finding]:
    """The many-parallel-units case from issue #256: an element can be down
    for a long time and cost little flow, or the other way round."""
    for row in _capacity_rows(ctx)[:5]:
        cap = row["share"]["mean"] or 0.0
        time = row["dtci"]["mean"] or 0.0
        if cap <= 0 or time <= 0:
            continue
        ratio = cap / time
        if ratio < 2.0 and ratio > 0.5:
            continue
        more = cap > time
        yield Finding(
            rule="R-CAP-102", severity="advice", category="capacity",
            title="Downtime share and capacity share disagree",
            message=(f"{row['name']} causes {text.fmt_pct(cap)} of the lost "
                     f"capacity but {text.fmt_pct(time)} of the downtime: "
                     + ("it removes a large share of the flow when it fails."
                        if more else
                        "it is down often but removes little flow.")),
            why="An hour down counts the same in availability whether the "
                "element is one of a hundred parallel units or the single "
                "point of connection. Ranking by lost capacity answers the "
                "energy question; ranking by downtime answers the up/down "
                "question.",
            targets=_target(row),
            evidence={"capacity_share": cap, "downtime_share": time,
                      "ratio": ratio, "rule": "proportional"},
            basis="statistical", confidence=0.9,
            impact=abs(cap - time), doc="advisor.html#R-CAP-102")


@rule("R-CAP-103", title="Availability overstates the service delivered",
      category="capacity", basis="statistical", severity="warning",
      needs=("model", "run"))
def availability_overstates(ctx: RunContext) -> Iterable[Finding]:
    a = ctx.values("availability")
    c = ctx.values("capacity_weighted_availability")
    if not a or not c:
        return
    mean_a = sum(a) / len(a)
    mean_c = sum(c) / len(c)
    if mean_a - mean_c < 0.01:
        return
    rows = _capacity_rows(ctx)
    top = rows[0] if rows else None
    yield Finding(
        rule="R-CAP-103", severity="warning", category="capacity",
        title="Availability overstates the service delivered",
        message=(f"Availability is {text.fmt_pct(mean_a)} but only "
                 f"{text.fmt_pct(mean_c)} of the capacity is delivered"
                 + (f"; {top['name']} loses the most." if top else ".")),
        why="A system can be 'up' while delivering less than the demand: "
            "capacity analysis measures the flow that actually arrives, not "
            "just whether any path is open.",
        targets=_target(top) if top else (),
        evidence={"availability": mean_a,
                  "capacity_weighted_availability": mean_c},
        basis="statistical", confidence=0.9, impact=mean_a - mean_c,
        doc="advisor.html#R-CAP-103")
