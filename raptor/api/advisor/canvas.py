"""The Advisor's findings as a diagram layer (plan 7.6.1).

Each finding names its targets; this groups them by the element they are
about, so the diagram can mark every block, node, event and hierarchy with
how many findings concern it and the worst of their severities. A finding
about a setting (a pool, a resource, the experiment, the system) has no
element to mark: it is counted in ``off_canvas`` and stays in the Advisor
panel.
"""
from __future__ import annotations

from collections.abc import Iterable
from typing import Any

from raptor.api.advisor.finding import SEVERITY_RANK, Finding

#: Target kinds drawn on the diagram.
ELEMENT_KINDS = ("block", "node", "event", "hier")


def advisor_layer(findings: Iterable[Finding]) -> dict[str, Any]:
    """``{layer, elements, findings, off_canvas}``. Each element row is
    ``{kind, id, name, count, severity, findings}``, where ``severity`` is
    the worst one and ``findings`` lists ``{rule, severity, title}`` worst
    first; rows are ordered worst severity first, then most findings."""
    rows: dict[tuple[str, int], dict[str, Any]] = {}
    total = off_canvas = 0
    for finding in findings:
        total += 1
        marked = False
        for target in finding.targets:
            if target.kind not in ELEMENT_KINDS or not target.id:
                continue
            key = (target.kind, int(target.id))
            row = rows.setdefault(key, {"kind": target.kind, "id": int(target.id),
                                        "name": target.name, "findings": []})
            if all(f["rule"] != finding.rule or f["title"] != finding.title
                   for f in row["findings"]):
                row["findings"].append({"rule": finding.rule, "severity": finding.severity,
                                        "title": finding.title})
            marked = True
        off_canvas += not marked
    out = []
    for row in rows.values():
        row["findings"].sort(key=lambda f: (-SEVERITY_RANK[f["severity"]], f["rule"]))
        row["count"] = len(row["findings"])
        row["severity"] = row["findings"][0]["severity"]
        out.append(row)
    out.sort(key=lambda r: (-SEVERITY_RANK[r["severity"]], -r["count"], r["name"]))
    return {"layer": "advisor", "elements": out, "findings": total, "off_canvas": off_canvas}
