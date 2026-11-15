"""Findings the user has dismissed, kept with the model.

An Advisor finding is a judgement about a model, and the person who owns
the model may know better: a backup that depends on the unit it backs up
may be deliberate, a placeholder may be the agreed value. Ignoring one
stores the rule, what it is about and the reason in the model (the .rbds
`advisor` key), so it stays dismissed for everyone who opens the file and
the reason travels with it. Restoring removes the entry.

An entry's `target` is the path of the finding's first target without a
field (``block:3``, ``pool:Motors``), or ``""`` for every finding of the rule
in this model.
"""
from __future__ import annotations

from collections.abc import Iterable
from typing import Any

from raptor.api.advisor.finding import Finding


def target_of(finding: Finding) -> str:
    """The key an entry uses for this finding's subject."""
    if not finding.targets:
        return ""
    first = finding.targets[0]
    path = first.path()
    return path.split(".", 1)[0] if first.field else path


def entries(rbd: Any) -> list[dict]:
    """The dismissed findings of `rbd`: `{rule, target, reason}` each."""
    return [dict(e) for e in (getattr(rbd, "advisor", None) or {}).get("ignored") or []]


def matching(finding: Finding, ignored: Iterable[dict]) -> dict | None:
    """The entry that dismisses `finding`, if any."""
    target = target_of(finding)
    for entry in ignored:
        if entry.get("rule") == finding.rule and entry.get("target", "") in ("", target):
            return entry
    return None


def split(findings: Iterable[Finding], rbd: Any) -> tuple[list[Finding], list[Finding]]:
    """(shown, ignored): `findings` with the dismissed ones taken out."""
    ignored = entries(rbd)
    shown: list[Finding] = []
    hidden: list[Finding] = []
    for finding in findings:
        (hidden if ignored and matching(finding, ignored) else shown).append(finding)
    return shown, hidden


def ignore(rbd: Any, rule: str, target: str = "", reason: str = "") -> dict:
    """Dismiss `rule` (for one `target`, or "" for the whole rule) in `rbd`;
    replaces an earlier entry for the same pair. Returns the entry."""
    if not rule:
        raise ValueError("name the rule to ignore, e.g. M-DEP-001")
    entry = {"rule": rule, "target": target or "", "reason": (reason or "").strip()}
    kept = [e for e in entries(rbd)
            if (e["rule"], e.get("target", "")) != (entry["rule"], entry["target"])]
    rbd.advisor = {**(getattr(rbd, "advisor", None) or {}), "ignored": [*kept, entry]}
    return entry


def restore(rbd: Any, rule: str, target: str = "") -> bool:
    """Undo `ignore` for that pair; whether there was one."""
    current = entries(rbd)
    kept = [e for e in current if (e["rule"], e.get("target", "")) != (rule, target or "")]
    if len(kept) == len(current):
        return False
    advisor = dict(getattr(rbd, "advisor", None) or {})
    if kept:
        advisor["ignored"] = kept
    else:
        advisor.pop("ignored", None)
    rbd.advisor = advisor
    return True


def parse(text: str) -> tuple[str, str]:
    """``RULE`` or ``RULE@TARGET`` (the CLI's spelling) -> (rule, target)."""
    rule, _, target = text.partition("@")
    return rule.strip(), target.strip()


#: what each target kind is called in a sentence
_KIND_WORDS = {"block": "Block", "node": "Node", "event": "Event", "hier": "Hierarchy",
               "pool": "Spare pool", "resource": "Resource", "trigger": "Trigger",
               "phase": "Phase", "link": "Connection"}


def describe(rbd: Any, entry: dict) -> dict[str, Any]:
    """An ignored entry for a person: the rule's title, what it is about by
    name ("Node UPS_Ready", not "node:26"), and in `locate` the `Target` to
    go to (None for the whole rule). `target` stays the stored key, which
    restoring the entry needs."""
    from raptor.api.advisor.context import ModelContext
    from raptor.api.advisor.finding import Target
    from raptor.api.advisor.registry import list_rules

    info = next((r for r in list_rules() if r.id == entry.get("rule")), None)
    out: dict[str, Any] = {"rule": entry.get("rule", ""),
                           "title": info.title if info else entry.get("rule", ""),
                           "reason": entry.get("reason", ""),
                           "target": entry.get("target", ""),
                           "subject": "All findings", "locate": None}
    text = entry.get("target", "")
    if not text:
        return out
    kind, _, key = text.partition(":")
    word = _KIND_WORDS.get(kind, kind.capitalize())
    name = key
    target_id: int | None = None
    if key.isdigit():
        target_id = int(key)
        ctx = ModelContext(rbd)
        rows = {"block": ctx.blocks, "node": ctx.nodes, "event": ctx.events}.get(kind, [])
        row = next((r for r in rows if str(r.get("id")) == key), None)
        name = str(row.get("name")) if row and row.get("name") else f"{key} (no longer in the model)"
    out["subject"] = f"{word} {name}"
    out["locate"] = Target(kind, target_id, name)
    return out


def listed(rbd: Any) -> list[dict]:
    """The dismissed entries for output (JSON-safe): `rule`, `target` and
    `reason` as stored, plus the rule's `title` and the `subject` by name."""
    return [{key: value for key, value in describe(rbd, entry).items() if key != "locate"}
            for entry in entries(rbd)]
