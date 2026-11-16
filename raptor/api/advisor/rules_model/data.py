"""Data plausibility rules."""
from __future__ import annotations

from collections.abc import Iterable

from raptor.api.advisor import text
from raptor.api.advisor.context import ModelContext, on
from raptor.api.advisor.finding import Finding, Fix
from raptor.api.advisor.registry import rule

_DATA_FIELDS = ("fail_dist", "fail_params", "repair_dist", "repair_params")


@rule("M-DAT-001", title="Default failure and repair data",
      category="data", basis="exact", severity="warning")
def default_data(ctx: ModelContext) -> Iterable[Finding]:
    default = ctx.default_block
    if default is None:
        return
    same = [b for b in ctx.blocks
            if all(b.get(f) == default.get(f) for f in _DATA_FIELDS)]
    if not same:
        return
    n, total = len(same), len(ctx.blocks)
    names = text.join_names([str(b["name"]) for b in same])
    yield Finding(
        rule="M-DAT-001", severity="warning", category="data",
        title="Default failure and repair data",
        message=(f"{text.plural(n, 'block')} still {'has' if n == 1 else 'have'} the "
                 f"default failure and repair data: {names}."),
        why="A new block starts with placeholder data. Results that include "
            "placeholder data describe no real equipment.",
        targets=tuple(ctx.block_target(b) for b in same),
        evidence={"blocks": n, "of": total},
        basis="exact", impact=n / total if total else 0.0,
        fixes=(Fix("Open Mass Edit", command="data.mass_edit"),),
        doc="advisor.html#M-DAT-001", group_key="M-DAT-001")


@rule("M-DAT-002", title="Repair takes longer than the life",
      category="data", basis="exact", severity="warning")
def repair_exceeds_life(ctx: ModelContext) -> Iterable[Finding]:
    for b in ctx.blocks:
        life, repair = ctx.mean_life(b), ctx.mean_repair(b)
        if not life or not repair or repair < life:
            continue
        avail = life / (life + repair)
        yield Finding(
            rule="M-DAT-002", severity="warning", category="data",
            title="Repair takes longer than the life",
            message=(f"{b['name']} takes {text.fmt_time(repair, ctx.units)} to repair but "
                     f"lasts only {text.fmt_time(life, ctx.units)} between failures: its own "
                     f"availability is {text.fmt_pct(avail)}."),
            why="This is possible but unusual. It often means the failure and "
                "repair values were swapped or are in different units.",
            targets=(ctx.block_target(b, "repair_params"),),
            evidence={"mean_life": life, "mean_repair": repair, "availability": avail},
            basis="exact", impact=1.0 - avail,
            doc="advisor.html#M-DAT-002")


def _pm_per_run(ctx: ModelContext, b: dict) -> float | None:
    freq, dur = float(b.get("pm_freq") or 0), ctx.mean_pm(b)
    if freq <= 0 or dur is None or not ctx.run_length:
        return None
    return ctx.run_length / freq * dur


@rule("M-DAT-006", title="Maintenance on a falling failure rate",
      category="pm", basis="exact", severity="warning")
def pm_on_decreasing_hazard(ctx: ModelContext) -> Iterable[Finding]:
    for b in ctx.blocks:
        if not on(b.get("uses_pm")) or ctx.hazard_shape(b) != "decreasing":
            continue
        shape = float(b["fail_params"][0])
        yield Finding(
            rule="M-DAT-006", severity="warning", category="pm",
            title="Maintenance on a falling failure rate",
            message=(f"{b['name']} has a falling failure rate (Weibull shape "
                     f"{text.fmt_num(shape)}, below 1): renewing it by preventive "
                     f"maintenance makes failures more likely."),
            why="With a shape below 1 a unit that has survived is less likely to "
                "fail than a new one. Replacing it early brings back the high "
                "early failure rate.",
            targets=(ctx.block_target(b, "uses_pm"),),
            evidence={"weibull_shape": shape},
            basis="exact", impact=0.6,
            fixes=(Fix("Turn off preventive maintenance",
                       (f"block:{int(b['id'])}.uses_pm=false",)),),
            doc="advisor.html#M-DAT-006")


@rule("M-DAT-007", title="Maintenance cannot reduce random failures",
      category="pm", basis="exact", severity="advice")
def pm_on_constant_hazard(ctx: ModelContext) -> Iterable[Finding]:
    for b in ctx.blocks:
        if not on(b.get("uses_pm")) or ctx.hazard_shape(b) != "constant":
            continue
        per_run = _pm_per_run(ctx, b)
        cost = (f" and takes about {text.fmt_time(per_run, ctx.units)} of maintenance "
                f"time over the run" if per_run else "")
        yield Finding(
            rule="M-DAT-007", severity="advice", category="pm",
            title="Maintenance cannot reduce random failures",
            message=(f"{b['name']} fails at a constant rate: scheduled preventive "
                     f"maintenance cannot reduce its failures{cost}."),
            why="With a constant failure rate an old unit is exactly as likely to "
                "fail as a new one, so renewing it changes nothing. Maintenance "
                "only helps equipment that wears out.",
            targets=(ctx.block_target(b, "uses_pm"),),
            evidence={"pm_time_per_run": per_run or 0.0},
            basis="exact", impact=0.4,
            fixes=(Fix("Turn off preventive maintenance",
                       (f"block:{int(b['id'])}.uses_pm=false",)),),
            doc="advisor.html#M-DAT-007")
