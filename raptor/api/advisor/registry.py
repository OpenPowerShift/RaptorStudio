"""The rule registry: rules register themselves and `run_rules` runs them.

A rule is a pure function ``rule(ctx) -> iterable of Finding``. It must not
change the model, draw random numbers, or do I/O. A rule that raises never
stops the others: it is reported as one `X-INTERNAL` note.
"""
from __future__ import annotations

import logging
from collections.abc import Callable, Iterable
from dataclasses import dataclass
from typing import Any

from raptor.api.advisor.finding import Finding, sort_findings

log = logging.getLogger(__name__)

DOC_PAGE = "advisor.html"


@dataclass(frozen=True)
class RuleInfo:
    id: str
    title: str
    category: str
    basis: str
    severity: str
    needs: frozenset[str]
    doc: str
    func: Callable[[Any], Iterable[Finding]]

    @property
    def phase(self) -> str:
        return "run" if "run" in self.needs else "model"

    def describe(self) -> dict[str, Any]:
        return {"id": self.id, "title": self.title, "category": self.category,
                "basis": self.basis, "severity": self.severity,
                "phase": self.phase, "doc": self.doc}


RULES: dict[str, RuleInfo] = {}


def rule(id: str, *, title: str, category: str, basis: str, severity: str,
         needs: Iterable[str] = ("model",)) -> Callable:
    """Register a rule function under a stable id (e.g. ``"M-SPR-003"``)."""
    def register(func: Callable[[Any], Iterable[Finding]]) -> Callable:
        if id in RULES:
            raise ValueError(f"duplicate advisor rule id {id!r}")
        RULES[id] = RuleInfo(id=id, title=title, category=category, basis=basis,
                             severity=severity, needs=frozenset(needs),
                             doc=f"{DOC_PAGE}#{id}", func=func)
        return func
    return register


def list_rules(phase: str | None = None) -> list[RuleInfo]:
    _load_rule_modules()
    infos = sorted(RULES.values(), key=lambda r: r.id)
    return [r for r in infos if phase is None or r.phase == phase]


def _selected(rule_id: str, only: Iterable[str] | None,
              skip: Iterable[str] | None) -> bool:
    """`only`/`skip` entries match an id or an id prefix (``"M-SPR"``)."""
    if only is not None and not any(rule_id.startswith(p) for p in only):
        return False
    return not (skip is not None and any(rule_id.startswith(p) for p in skip))


def run_rules(ctx: Any, *, phase: str = "model", only: Iterable[str] | None = None,
              skip: Iterable[str] | None = None) -> list[Finding]:
    """Every finding of the registered rules for `phase`, most important first."""
    only = list(only) if only is not None else None
    skip = list(skip) if skip is not None else None
    out: list[Finding] = []
    for info in list_rules(phase):
        if not _selected(info.id, only, skip):
            continue
        try:
            out.extend(info.func(ctx))
        except Exception as exc:  # noqa: BLE001 - the advisor must never block the user
            log.exception("advisor rule %s failed", info.id)
            out.append(Finding(
                rule="X-INTERNAL", severity="info", category="internal",
                title="A check could not be completed",
                message=f"Check {info.id} ({info.title}) failed: {type(exc).__name__}: {exc}",
                why="This is a fault in the check, not in your model. The other "
                    "checks ran normally.",
                evidence={"rule": info.id}, impact=0.0))
    return sort_findings(out)


_loaded = False


def _load_rule_modules() -> None:
    global _loaded
    if _loaded:
        return
    _loaded = True
    from raptor.api.advisor import rules_model, rules_run  # noqa: F401 - register on import
