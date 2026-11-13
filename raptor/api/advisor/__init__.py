"""The Advisor: rules that read a model (and later a run) and say what looks
wrong or unwise, as typed findings with stable ids.

    from raptor.api.advisor import advise_model
    for f in advise_model(rbd):
        print(f.severity, f.rule, f.message)

Nothing here imports Qt, draws random numbers or changes the model.
"""
from __future__ import annotations

from collections.abc import Iterable
from typing import Any

from raptor.api.advisor.context import ModelContext, RunContext
from raptor.api.advisor.finding import Finding, Fix, Target, sort_findings
from raptor.api.advisor.registry import RULES, RuleInfo, list_rules, rule, run_rules

__all__ = ["RULES", "Finding", "Fix", "ModelContext", "RuleInfo", "RunContext", "Target",
           "advise_model", "advise_run", "advise_stored_run", "counts", "list_rules",
           "rule", "run_rules", "sort_findings"]


def advise_model(rbd: Any, *, only: Iterable[str] | None = None,
                 skip: Iterable[str] | None = None) -> list[Finding]:
    """Every model finding for `rbd`, most important first."""
    return run_rules(ModelContext(rbd), phase="model", only=only, skip=skip)


def advise_run(rbd: Any, summary: dict, per_run: list[dict], *,
               requirements: list[dict] | None = None,
               only: Iterable[str] | None = None,
               skip: Iterable[str] | None = None) -> list[Finding]:
    """Every run finding for one finished batch of `rbd`, most important first."""
    ctx = RunContext(rbd, summary, per_run, requirements)
    return run_rules(ctx, phase="run", only=only, skip=skip)


def advise_stored_run(workspace: Any, run_id: str, *,
                      requirements: list[dict] | None = None) -> list[Finding]:
    """`advise_run` for a run kept in a workspace (reads its stored artefacts)."""
    from raptor.api.run import run_dir
    from raptor.api.run_tables import read_per_run, read_summary
    runs_dir = run_dir(workspace, run_id)
    summary = read_summary(runs_dir, run_id, required=True)
    per_run = read_per_run(runs_dir, run_id) or []
    rbd = workspace.get(summary["model_id"]).rbd
    return advise_run(rbd, summary, per_run, requirements=requirements)


def counts(findings: Iterable[Finding]) -> dict[str, int]:
    out = {"error": 0, "warning": 0, "advice": 0, "info": 0}
    for f in findings:
        out[f.severity] = out.get(f.severity, 0) + 1
    return out
