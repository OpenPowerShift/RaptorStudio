"""Which element a report row is about, carried with the row's text.

A report row names its element (`Armature   Infinite   3 ...`), and names
are not unique: two blocks may share one. The report text must stay exactly
as the legacy format writes it, so the element is attached to the line
instead -- `ElementLine` is a `str` (equal to, and written exactly as, its
text) that also carries `(kind, id)`, the same idea as the event log's
`LogLine`. Tables built from a report read it back and give each row an ID.

A saved run stores the tags beside the report (`tags_of` / `apply_tags`),
since a file holds only the text.
"""
from __future__ import annotations

from collections.abc import Iterable, Sequence

#: Element kinds a report row can be about.
KINDS = ("block", "node", "event", "hier")


class ElementLine(str):
    """A report line about one element: `element` is `(kind, id)`."""

    __slots__ = ("element",)
    element: tuple[str, int]

    def __new__(cls, text: str, kind: str, element_id: int) -> ElementLine:
        line = super().__new__(cls, text)
        line.element = (kind, int(element_id))
        return line

    def __reduce__(self):
        # Rows cross process boundaries with the run's results.
        return (ElementLine, (str(self), *self.element))


def tag(text: str, kind: str, element_id: int) -> ElementLine:
    return ElementLine(text, kind, element_id)


def element_of(text: object) -> tuple[str, int] | None:
    """`(kind, id)` of a tagged line (a report row or an event-log
    `LogLine`), or None."""
    return getattr(text, "element", None)


def tags_of(lines: Sequence[str]) -> list[list]:
    """`[[line index, kind, id], ...]` for saving beside the text."""
    return [[i, *e] for i, line in enumerate(lines)
            if (e := element_of(line)) is not None]


def apply_tags(lines: Sequence[str], tags: Iterable[Sequence]) -> list[str]:
    """`lines` with the saved tags put back (entries out of range or with a
    kind not in `KINDS` are ignored)."""
    out = list(lines)
    for entry in tags:
        try:
            i, kind, oid = int(entry[0]), str(entry[1]), int(entry[2])
        except (TypeError, ValueError, IndexError):
            continue
        if 0 <= i < len(out) and kind in KINDS:
            out[i] = ElementLine(out[i], kind, oid)
    return out
