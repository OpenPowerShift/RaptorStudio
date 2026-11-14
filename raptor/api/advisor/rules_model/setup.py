"""Run set-up rules: analyses and the number of runs."""
from __future__ import annotations

from collections.abc import Iterable

from raptor.api.advisor.context import ModelContext
from raptor.api.advisor.finding import Finding, Fix, Target
from raptor.api.advisor.registry import rule

TOO_FEW_RUNS = 10
ROUGH_RUNS = 30
SUGGESTED_RUNS = 200


@rule("M-CAP-001", title="Capacity analysis with no flow",
      category="capacity", basis="exact", severity="warning")
def capacity_without_flow(ctx: ModelContext) -> Iterable[Finding]:
    if "capacity" not in ctx.analyses:
        return
    flow = float(ctx.system.get("flow_generated") or 0)
    if flow <= 0:
        yield Finding(
            rule="M-CAP-001", severity="warning", category="capacity",
            title="Capacity analysis with no flow",
            message="Capacity analysis is on but no flow enters the Start node.",
            why="Capacity results describe how much of the flow generated at the "
                "Start node reaches the End node. With none generated there is "
                "nothing to report.",
            targets=(Target("system", None, "System", "flow"),),
            evidence={"flow_generated": flow}, basis="exact", impact=0.6,
            doc="advisor.html#M-CAP-001")


@rule("M-EXP-001", title="Too few runs",
      category="experiment", basis="exact", severity="warning")
def few_runs(ctx: ModelContext) -> Iterable[Finding]:
    n = ctx.trials
    if n >= ROUGH_RUNS:
        return
    few = n < TOO_FEW_RUNS
    yield Finding(
        rule="M-EXP-001" if few else "M-EXP-002",
        severity="warning" if few else "advice", category="experiment",
        title="Too few runs" if few else "Rough intervals",
        message=(f"{n} run{'s' if n != 1 else ''} cannot give a confidence interval "
                 f"worth reading. Use {SUGGESTED_RUNS} or more."
                 if few else
                 f"With {n} runs the confidence intervals are rough. "
                 f"{SUGGESTED_RUNS} or more gives steadier results."),
        why="Each run is one possible future. The spread between runs is what "
            "tells you how far to trust the average, and it cannot be judged "
            "from a handful.",
        targets=(Target("system", None, "System", "runs"),),
        evidence={"runs": n}, basis="exact", impact=0.8 if few else 0.3,
        fixes=(Fix(f"Set runs to {SUGGESTED_RUNS}", (f"system.runs={SUGGESTED_RUNS}",)),),
        doc="advisor.html#" + ("M-EXP-001" if few else "M-EXP-002"))


@rule("M-EXP-002", title="Rough intervals",
      category="experiment", basis="exact", severity="advice")
def rough_runs(ctx: ModelContext) -> Iterable[Finding]:
    return ()          # reported by `few_runs`, which shares the thresholds
