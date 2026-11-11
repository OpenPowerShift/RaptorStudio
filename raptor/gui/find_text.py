"""Resolving what the ribbon's Find box holds (pure: no Qt, no model objects).

The window hands in plain ``{kind: {element id: name}}`` tables, so every rule below
can be tested without a diagram.
"""
from __future__ import annotations

import re
from collections.abc import Mapping

#: Kinds in lookup order. A bare name matches blocks before nodes: a name typed into
#: Find is far more often a block's. This is also the order the completer lists them.
KINDS = ("Block", "Node", "Event", "Hier")

Names = Mapping[str, Mapping[int, str]]


def find_entries(names: Names) -> list[str]:
    """Backing list for the Find box: ``"Block  Pump (12)"`` per element, by kind then
    case-insensitive name, so Enter can resolve either a name or an id."""
    out: list[str] = []
    for kind in KINDS:
        for eid, name in sorted(names.get(kind, {}).items(),
                                key=lambda t: t[1].lower()):
            out.append(f"{kind}  {name} ({eid})")
    return out


def parse_find_text(text: str) -> tuple[str | None, str, int | None] | None:
    """Split the box into ``(kind, name, id)``; each may be absent (None / "" / None).

    ``None`` when nothing usable is left (empty, or only a kind prefix).
    """
    text = text.strip()
    kind = None
    for p in KINDS:
        # Two spaces, not one: ``Block Pump`` is a name that merely starts with "Block".
        if text.startswith(p + "  "):
            kind, text = p, text[len(p) + 2:].strip()
            break
    if not text:
        return None
    if text.isdigit():
        return kind, "", int(text)
    m = re.fullmatch(r"(.*?)\s*\((\d+)\)\s*", text)
    if m:
        return kind, m.group(1).strip(), int(m.group(2))
    return kind, text, None


def resolve_find_text(text: str, names: Names) -> tuple[str, str] | None:
    """What the Find box holds -> ``(kind, name)``, or None.

    Accepts the prefixed form (``Block  Pump (12)``), a bare name (``Pump``), a bare
    id (``12``), and a name carrying its id (``Pump (12)``) -- which is what the
    Explorer rows show, so a row the user clicked through from is a valid thing to
    press Enter on.
    """
    parsed = parse_find_text(text)
    if parsed is None:
        return None
    kind, name, eid = parsed
    if eid is not None and not name:
        # An id alone identifies exactly one element, if it exists.
        return next(((k, names[k][eid]) for k in KINDS if eid in names.get(k, {})),
                    None)
    if kind is not None:
        return kind, name
    if eid is not None:
        # Name AND id: the id is authoritative when it names something.
        return next(((k, name) for k in KINDS
                     if (have := names.get(k, {}).get(eid)) is not None
                     and have.lower() == name.lower()), None)
    return next(((k, name) for k in KINDS
                 if any(n.lower() == name.lower() for n in names.get(k, {}).values())),
                None)
