"""After a run: how far to trust the numbers, and whether a target is met.

These read only the stored per-run rows. Nothing is re-simulated.
"""
from __future__ import annotations

import math
from collections.abc import Iterable
from typing import Any

from raptor.api.advisor import text
from raptor.api.advisor.context import RunContext
from raptor.api.advisor.finding import Finding, Fix, Target
from raptor.api.advisor.registry import rule
from raptor.api.advisor.stats import t_cdf, t_quantile

#: The downtime estimate should be good to this relative half-width.
PRECISION = 0.10
#: Fewer system failures than this over all runs make failure timing unreliable.
FEW_EVENTS = 10
#: The worst 5 % of runs holding more than this share of downtime is worth saying.
TAIL_RUNS = 0.05
TAIL_SHARE = 0.5
MAX_SUGGESTED_RUNS = 100_000

_RUNS = Target("system", None, "System", "runs")


def _runs_fix(n: int) -> tuple[Fix, ...]:
    return (Fix(f"Set runs to {n:,}", (f"system.runs={n}",)),)


@rule("R-QUA-001", title="Downtime is not yet known precisely",
      category="quality", basis="statistical", severity="advice", needs=("model", "run"))
def precision(ctx: RunContext) -> Iterable[Finding]:
    ao = ctx.values("availability")
    n = len(ao)
    if n < 3:
        return
    mean, sem = ctx.mean_sem(ao)
    unavailability = 1.0 - mean
    if unavailability <= 0 or not sem > 0:
        return
    half = t_quantile(0.975, n - 1) * sem
    rel = half / unavailability
    if rel <= PRECISION:
        return
    needed = min(MAX_SUGGESTED_RUNS, math.ceil(n * (rel / PRECISION) ** 2))
    yield Finding(
        rule="R-QUA-001", severity="advice", category="quality",
        title="Downtime is not yet known precisely",
        message=(f"Availability is {text.fmt_pct(mean, 5)} ± {text.fmt_pct(half, 2)} "
                 f"(95 %). The downtime estimate is only good to ± {text.fmt_pct(rel)}. "
                 f"About {needed:,} runs would give ± {text.fmt_pct(PRECISION)}."),
        why="Each run is one possible future. The spread between runs sets how far "
            "the average can be trusted; the interval narrows with the square root "
            "of the number of runs. Precision is judged on downtime because that is "
            "the part of availability a decision changes.",
        targets=(_RUNS,),
        evidence={"availability": mean, "half_width": half, "relative_half_width": rel,
                  "runs": n, "runs_needed": needed},
        basis="statistical", confidence=0.95, impact=min(1.0, rel),
        fixes=_runs_fix(needed), doc="advisor.html#R-QUA-001")


@rule("R-QUA-002", title="Too few system failures to time them",
      category="quality", basis="statistical", severity="warning", needs=("model", "run"))
def few_failures(ctx: RunContext) -> Iterable[Finding]:
    fails = ctx.values("num_system_failures")
    if not fails:
        return
    n, total = len(fails), int(sum(fails))
    if total == 0:
        bound = 1.0 - 0.05 ** (1.0 / n)
        yield Finding(
            rule="R-QUA-003", severity="info", category="quality",
            title="No system failure in any run",
            message=(f"No system failure occurred in {n:,} runs. With 95 % confidence the "
                     f"chance of a failure in one run is below {text.fmt_pct(bound)}."),
            why="Seeing nothing does not prove it cannot happen. With no failures in "
                "n runs the chance per run is, with 95 % confidence, below about 3/n. "
                "Time between failures and time down cannot be estimated at all.",
            targets=(_RUNS,), evidence={"runs": n, "upper_bound": bound, "rule_of_three": 3.0 / n},
            basis="exact", impact=0.3, doc="advisor.html#R-QUA-003")
        return
    if total >= FEW_EVENTS:
        return
    needed = min(MAX_SUGGESTED_RUNS, math.ceil(n * 3 * FEW_EVENTS / total))
    yield Finding(
        rule="R-QUA-002", severity="warning", category="quality",
        title="Too few system failures to time them",
        message=(f"Only {text.plural(total, 'system failure')} occurred in {n:,} runs: "
                 f"time between failures and time down rest on very few events."),
        why="Averages of a handful of events move a lot from one batch to the "
            "next. Availability is less affected, because every run contributes "
            "to it whether or not the system failed.",
        targets=(_RUNS,), evidence={"system_failures": total, "runs": n, "runs_needed": needed},
        basis="statistical", confidence=0.9, impact=1.0 - total / FEW_EVENTS,
        fixes=_runs_fix(needed), doc="advisor.html#R-QUA-002")


@rule("R-QUA-003", title="No system failure in any run",
      category="quality", basis="exact", severity="info", needs=("model", "run"))
def no_failures(ctx: RunContext) -> Iterable[Finding]:
    return ()          # reported by `few_failures`, which already has the count


@rule("R-QUA-005", title="A few bad runs drive the average",
      category="quality", basis="exact", severity="advice", needs=("model", "run"))
def tail_runs(ctx: RunContext) -> Iterable[Finding]:
    ao = ctx.values("availability")
    n = len(ao)
    if n < 20:
        return
    down = sorted((1.0 - a for a in ao), reverse=True)
    total = sum(down)
    if total <= 0:
        return
    k = max(1, math.ceil(TAIL_RUNS * n))
    share = sum(down[:k]) / total
    if share <= TAIL_SHARE:
        return
    ordered = sorted(ao)
    median, p10 = ordered[n // 2], ordered[max(0, math.ceil(0.10 * n) - 1)]
    yield Finding(
        rule="R-QUA-005", severity="advice", category="quality",
        title="A few bad runs drive the average",
        message=(f"{text.fmt_pct(share)} of all downtime came from {k} of {n:,} runs: rare "
                 f"long outages drive the average. A typical run has availability "
                 f"{text.fmt_pct(median, 5)}; one run in ten is below {text.fmt_pct(p10, 5)}."),
        why="When the outcome is this lopsided the average describes few real "
            "runs. Quote the typical run and the bad-case run as well as the mean, "
            "and look at what the worst runs have in common.",
        evidence={"share": share, "worst_runs": k, "runs": n, "median": median, "p10": p10},
        basis="exact", impact=share, doc="advisor.html#R-QUA-005")


def _confident(p: float) -> str:
    return "more than 99 %" if p > 0.99 else text.fmt_pct(p)


def _verdict(ctx: RunContext, req: dict) -> Finding | None:
    metric, op, target = str(req.get("metric", "")), str(req.get("op", ">=")), req.get("value")
    values = ctx.values(ctx.field_for(metric))
    n = len(values)
    if n < 2 or not isinstance(target, (int, float)) or op not in (">=", "<="):
        return None
    mean, sem = ctx.mean_sem(values)
    higher_is_better = op == ">="
    margin = (mean - target) if higher_is_better else (target - mean)
    p_meet = (1.0 if margin >= 0 else 0.0) if not sem > 0 else t_cdf(margin / sem, n - 1)
    runs_meeting = sum((v >= target) if higher_is_better else (v <= target) for v in values)
    half = t_quantile(0.975, n - 1) * sem if sem > 0 else 0.0
    label = str(req.get("label") or metric)
    is_fraction = ctx.field_for(metric) in ("availability", "reliability", "dependability")
    show = (lambda v: text.fmt_pct(v, 5)) if is_fraction else (lambda v: text.fmt_num(v, 4))
    spread = text.fmt_pct(half, 2) if is_fraction else text.fmt_num(half, 2)
    evidence = {"metric": metric, "target": target, "mean": mean, "half_width": half,
                "p_meet": p_meet, "runs": n, "runs_meeting": runs_meeting}
    gap_time = ""
    if ctx.field_for(metric) == "availability" and ctx.run_length and margin < 0:
        hours = -margin * ctx.run_length
        evidence["downtime_gap"] = hours
        gap_time = f" ({text.fmt_time(hours, ctx.units)} more downtime than allowed over the run)"
    common: dict[str, Any] = dict(rule="R-REQ-001", category="requirement", targets=(_RUNS,),
                  evidence=evidence, basis="statistical", doc="advisor.html#R-REQ-001",
                  why="The verdict compares the average over the runs with the target, "
                      "allowing for the spread between runs. The count of runs that "
                      "met the target one by one answers a different question: how "
                      "often a single period would meet it.")
    counted = f"{runs_meeting:,} of {n:,} runs met it individually"
    if p_meet >= 0.95:
        return Finding(severity="info", title="Target met",
                       message=(f"Target met: {label} {op} {show(target)}; the runs give "
                                f"{show(mean)} ± {spread}. {_confident(p_meet)} confident; "
                                f"{counted}."),
                       confidence=p_meet, impact=0.2, **common)
    if p_meet <= 0.05:
        return Finding(severity="warning", title="Target missed",
                       message=(f"Target missed: {label} {op} {show(target)}; the runs give "
                                f"{show(mean)} ± {spread}{gap_time}. "
                                f"{_confident(1 - p_meet)} confident; {counted}."),
                       confidence=1 - p_meet, impact=1.0, **common)
    needed = min(MAX_SUGGESTED_RUNS,
                 math.ceil(n * (t_quantile(0.975, n - 1) * sem / abs(margin)) ** 2)
                 if margin else MAX_SUGGESTED_RUNS)
    evidence["runs_needed"] = needed
    return Finding(severity="advice", title="Too close to call",
                   message=(f"Too close to call: {label} {op} {show(target)}; the runs give "
                            f"{show(mean)} ± {spread}. About {needed:,} runs would decide it."),
                   confidence=max(p_meet, 1 - p_meet), impact=0.7,
                   fixes=_runs_fix(needed) if needed > n else (), **common)


@rule("R-REQ-001", title="Target met, missed or too close to call",
      category="requirement", basis="statistical", severity="warning", needs=("model", "run"))
def requirements(ctx: RunContext) -> Iterable[Finding]:
    for req in ctx.requirements:
        finding = _verdict(ctx, req)
        if finding is not None:
            yield finding
