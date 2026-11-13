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

from raptor.api.advisor.context import ModelContext
from raptor.api.advisor.finding import Finding, Fix, Target, sort_findings
from raptor.api.advisor.registry import RULES, RuleInfo, list_rules, rule, run_rules

__all__ = ["RULES", "Finding", "Fix", "ModelContext", "RuleInfo", "Target",
           "advise_model", "counts", "list_rules", "rule", "run_rules",
           "sort_findings"]


def advise_model(rbd: Any, *, only: Iterable[str] | None = None,
                 skip: Iterable[str] | None = None) -> list[Finding]:
    """Every model finding for `rbd`, most important first."""
    return run_rules(ModelContext(rbd), phase="model", only=only, skip=skip)


def counts(findings: Iterable[Finding]) -> dict[str, int]:
    out = {"error": 0, "warning": 0, "advice": 0, "info": 0}
    for f in findings:
        out[f.severity] = out.get(f.severity, 0) + 1
    return out
