"""A finding: one piece of advice or one observation about a model or a run.

Findings are plain, frozen data so the command line, the MCP server and the
application all show the same thing. Nothing here imports Qt.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any, Literal

Severity = Literal["error", "warning", "advice", "info"]
Basis = Literal["exact", "statistical", "heuristic"]

SEVERITY_RANK: dict[str, int] = {"error": 3, "warning": 2, "advice": 1, "info": 0}

#: How much a finding's impact counts when its method is not exact.
HEURISTIC_CONFIDENCE = 0.7
UNKNOWN_STATISTICAL_CONFIDENCE = 0.5

_KEYED_KINDS = {"pool": "Pool", "resource": "Resource", "trigger": "Trigger",
                "phase": "Phase"}


@dataclass(frozen=True)
class Target:
    """What a finding is about: an element, a keyed row, or the system."""
    kind: str                    # block|node|event|hier|link|pool|resource|trigger|phase|system|experiment
    id: int | None
    name: str
    field: str | None = None     # friendly field name (see `raptor.api.paths`)

    def path(self) -> str:
        """The path text `raptor.api.paths` understands, e.g. `block:12.pm_freq`."""
        if self.kind in ("system", "experiment"):
            return f"system.{self.field}" if self.field else "system"
        if self.kind in _KEYED_KINDS:
            base = f"{self.kind}:{self.name}"
        else:
            base = f"{self.kind}:{self.id if self.id is not None else self.name}"
        return f"{base}.{self.field}" if self.field else base


@dataclass(frozen=True)
class Fix:
    """A change the user may choose to make. Never applied automatically."""
    label: str
    assignments: tuple[str, ...] = ()
    command: str | None = None
    args: dict[str, Any] = field(default_factory=dict)
    changes_results: bool = True


@dataclass(frozen=True)
class Finding:
    rule: str
    severity: Severity
    category: str
    title: str
    message: str
    why: str
    targets: tuple[Target, ...] = ()
    evidence: dict[str, Any] = field(default_factory=dict)
    basis: Basis = "exact"
    confidence: float | None = None
    impact: float = 0.5
    fixes: tuple[Fix, ...] = ()
    doc: str = ""
    group_key: str = ""

    @property
    def score(self) -> float:
        """Impact weighted by how sure the method is (0..1)."""
        if self.basis == "exact":
            conf = 1.0
        elif self.basis == "statistical":
            conf = (self.confidence if self.confidence is not None
                    else UNKNOWN_STATISTICAL_CONFIDENCE)
        else:
            conf = HEURISTIC_CONFIDENCE
        return max(0.0, min(1.0, self.impact)) * conf

    def sort_key(self) -> tuple:
        first = self.targets[0].name if self.targets else ""
        return (-SEVERITY_RANK[self.severity], -self.score, self.rule, first)

    def to_dict(self) -> dict[str, Any]:
        d = asdict(self)
        d["score"] = round(self.score, 6)
        d["targets"] = [dict(asdict(t), path=t.path()) for t in self.targets]
        d["fixes"] = [dict(asdict(f), assignments=list(f.assignments))
                      for f in self.fixes]
        return d

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> Finding:
        targets = tuple(Target(t["kind"], t.get("id"), t["name"], t.get("field"))
                        for t in d.get("targets", ()))
        fixes = tuple(Fix(f["label"], tuple(f.get("assignments", ())),
                          f.get("command"), dict(f.get("args") or {}),
                          f.get("changes_results", True))
                      for f in d.get("fixes", ()))
        return cls(rule=d["rule"], severity=d["severity"], category=d["category"],
                   title=d["title"], message=d["message"], why=d.get("why", ""),
                   targets=targets, evidence=dict(d.get("evidence") or {}),
                   basis=d.get("basis", "exact"), confidence=d.get("confidence"),
                   impact=d.get("impact", 0.5), fixes=fixes, doc=d.get("doc", ""),
                   group_key=d.get("group_key", ""))


def sort_findings(findings: list[Finding]) -> list[Finding]:
    return sorted(findings, key=Finding.sort_key)
