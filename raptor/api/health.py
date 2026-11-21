"""Project health: size, completeness and a readable issue summary.

This is the "is this model ready to send, run or review?" view. It is not a
second validator: `raptor.api.validate` owns correctness. This module adds
the project-level questions a reviewer asks before that: what is in the
file, what has no name, what sits unused, and what still points at nothing.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from raptor.core.fileio.typed import row_int

_SECTIONS = (
    ("blocks", "BLOCK_DATA"), ("nodes", "NODE_DATA"),
    ("events", "EVENT_DATA"), ("hierarchies", "HIERARCHY_DATA"),
    ("links", "LINK_DATA"), ("spare_pools", "SPARE_POOL_DATA"),
    ("resources", "RESOURCE_DATA"), ("triggers", "TRIGGERS"),
    ("phases", "PHASES"),
)
_NAME_COLUMNS = {
    "blocks": "blockName", "nodes": "Name", "events": "eventName",
    "hierarchies": "hierarchyName",
}
#: Singular labels for `summary()`. `rstrip("s")` turned "hierarchies" into
#: "hierarchie", which only a one-hierarchy model exposed.
_SINGULAR = {
    "blocks": "block", "nodes": "node", "events": "event",
    "hierarchies": "hierarchy", "links": "link",
    "spare_pools": "spare pool", "resources": "resource",
    "triggers": "trigger", "phases": "phase",
}
_PLURAL = {
    "blocks": "blocks", "nodes": "nodes", "events": "events",
    "hierarchies": "hierarchies", "links": "links",
    "spare_pools": "spare pools", "resources": "resources",
    "triggers": "triggers", "phases": "phases",
}


@dataclass(frozen=True)
class HealthIssue:
    severity: str          # "error" | "warning" | "info"
    message: str
    element: str = ""      # "block:3" when one can be located

    def text(self) -> str:
        return self.message


@dataclass
class HealthReport:
    counts: dict[str, int] = field(default_factory=dict)
    issues: list[HealthIssue] = field(default_factory=list)
    annotations: int = 0
    text_annotations: int = 0
    comments: int = 0
    formulas: int = 0
    rates: int = 0

    @property
    def errors(self) -> list[HealthIssue]:
        return [i for i in self.issues if i.severity == "error"]

    @property
    def warnings(self) -> list[HealthIssue]:
        return [i for i in self.issues if i.severity == "warning"]

    def summary(self) -> str:
        parts = []
        for key, n in self.counts.items():
            if not n:
                continue
            label = (_SINGULAR if n == 1 else _PLURAL).get(
                key, key.replace("_", " "))
            parts.append(f"{n:,} {label}")
        return " · ".join(parts) or "no elements"


def load(path: str) -> Any:
    """Read a model through the one loader (for CLI callers)."""
    from raptor.core.fileio.model_file import load_model_file
    return load_model_file(path)


def report(rbd: Any) -> HealthReport:  # noqa: C901, PLR0912
    """The health of *rbd*, including the validator's findings."""
    out = HealthReport()
    sections = getattr(rbd, "sections", None) or {}
    rows: dict[str, list[dict[str, str]]] = {}
    for key, section in _SECTIONS:
        sec = sections.get(section)
        found = list(sec.rows) if sec is not None else []
        # Keyed sections (pools/resources/triggers/phases) have no ID column;
        # filtering them by ID>0 reported every one of them as zero.
        if key in ("blocks", "nodes", "events", "hierarchies"):
            found = [r for r in found if row_int(r.get("ID"), 0) > 0]
        rows[key] = found
        out.counts[key] = len(found)

    annotations = list(getattr(rbd, "annotations", None) or [])
    out.annotations = len(annotations)
    out.text_annotations = sum(
        1 for a in annotations if str(a.get("kind") or "") == "text")
    comments_sec = sections.get("COMMENTS")
    # A comment can be a row in the Comments layer or a note drawn on the
    # canvas; both are free prose, so both count.
    out.comments = (len(comments_sec.rows) if comments_sec is not None else 0) + \
        sum(1 for a in annotations if a.get("comment_of") is not None)
    out.formulas = len(getattr(rbd, "formulas", None) or {})
    out.rates = len(getattr(rbd, "rates", None) or [])

    # Validation is the source of truth for correctness.
    try:
        from raptor.api.validate import issues as validate_issues
        for issue in validate_issues(rbd):
            text = f"{issue.element}: {issue.message}" if issue.element \
                else issue.message
            out.issues.append(HealthIssue(issue.severity, text, issue.element))
    except Exception as exc:  # noqa: BLE001 - health must not fail
        out.issues.append(HealthIssue(
            "warning", f"validation could not run: {exc}"))

    # The project-level questions the validator does not ask.
    for key, column in _NAME_COLUMNS.items():
        for row in rows.get(key, []):
            oid = row_int(row.get("ID"), 0)
            if not str(row.get(column) or "").strip():
                singular, kind = {
                    "blocks": ("Block", "block"),
                    "nodes": ("Node", "node"),
                    "events": ("Event", "event"),
                    "hierarchies": ("Hierarchy", "hier"),
                }[key]
                out.issues.append(HealthIssue(
                    "warning", f"{singular} {oid} has no name",
                    f"{kind}:{oid}"))

    seen: dict[str, list[int]] = {}
    for row in rows.get("blocks", []):
        name = str(row.get("blockName") or "").strip().casefold()
        if name:
            seen.setdefault(name, []).append(row_int(row.get("ID"), 0))
    display = {}
    for row in rows.get("blocks", []):
        name = str(row.get("blockName") or "").strip()
        if name:
            display.setdefault(name.casefold(), name)
    for name, ids in seen.items():
        if len(ids) > 1:
            out.issues.append(HealthIssue(
                "warning",
                f"{len(ids)} blocks share the name {display.get(name, name)!r}: "
                + ", ".join(str(i) for i in ids),
                f"block:{ids[0]}"))

    # References are matched case-insensitively, like the dialogs that set
    # them; a trim-only set reported a used pool as unused when the two
    # spellings differed in case.
    used_pools = {str(r.get("SparePoolName") or "").strip().casefold()
                  for r in rows.get("blocks", [])}
    for pool in rows.get("spare_pools", []):
        name = str(pool.get("PoolName") or "").strip()
        if name and name.casefold() not in used_pools:
            out.issues.append(HealthIssue(
                "info", f"spare pool {name!r} is not used by any block"))
    used_resources = {str(r.get("Resource1Name") or "").strip().casefold()
                      for r in rows.get("blocks", [])}
    for resource in rows.get("resources", []):
        name = str(resource.get("PoolName") or "").strip()
        if name and name.casefold() not in used_resources:
            out.issues.append(HealthIssue(
                "info", f"resource pool {name!r} is not used by any block"))

    out.issues.sort(key=lambda i: ({"error": 0, "warning": 1, "info": 2}
                                   .get(i.severity, 3), i.message))
    return out
