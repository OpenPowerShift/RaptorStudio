"""The COMMENTS-section lookup shared by the canvas, Inspector and row editor.

COMMENTS rows are keyed by ``Type + ID`` in files this application writes,
but older files may have only ``Type + Name`` (no ID column). Duplicate
element names are legal, so:

* an ID-bearing row is NEVER returned for a different element;
* the name fallback only applies to a row with no ID (a legacy row), and
  only when the caller has a name to compare.
"""
from __future__ import annotations

from raptor.core.fileio.typed import row_int


def is_legacy_comment_row(row) -> bool:
    """True for a COMMENTS row with no real element ID.

    ID 0 is the file format's template/sentinel value and is treated the
    same as a missing ID: both are legacy name-keyed rows.
    """
    return row_int(row.get("ID")) <= 0


def _name_forms(name: str) -> set[str]:
    """The spellings a legacy save may have produced for ``name``.

    Normalization is deliberately conditional: only names beyond the
    20-char cap, or a cap-length ``~n`` truncation twin, get truncated
    forms. Short names ending in ``~2`` are real names and stay exact.
    """
    from raptor.core.fileio.legacy_layout import LEGACY_NAME_CAP

    text = str(name or "")
    forms = {text}
    if len(text) >= LEGACY_NAME_CAP:
        # The candidate truncation lengths the legacy writer can produce
        # (cap-4 through cap, allowing a `~n` distinguisher).
        for cut in range(LEGACY_NAME_CAP - 4, LEGACY_NAME_CAP + 1):
            forms.add(text[:cut])
    base, sep, suffix = text.rpartition("~")
    if (len(text) >= LEGACY_NAME_CAP
            and sep and suffix.isdigit()
            and len(base) >= LEGACY_NAME_CAP - 4):
        # A truncation collision: `base[:cap]` became `base[:cap-4]~n`.
        forms.add(base)
        forms.add(text[:LEGACY_NAME_CAP - 4])
    return forms


def names_agree(first: str, second: str) -> bool:
    if not first or not second:
        return True
    return bool(_name_forms(first) & _name_forms(second))


def _row_by_id(rows, kind: str, oid: int, name: str):
    for row in rows:
        if str(row.get("Type", "")).lower() != kind:
            continue
        if row_int(row.get("ID")) != oid:
            continue
        # An ID match with a genuinely different name is a stale row from
        # a reused ID. Names are compared through the legacy cap because
        # a save can add a `~n` distinguisher to one side only.
        if not names_agree(name, row.get("Name", "")):
            continue
        return row
    return None


def _legacy_row_by_name(rows, kind: str, name: str):
    if not name:
        return None
    candidates = [
        row for row in rows
        if str(row.get("Type", "")).lower() == kind
        and is_legacy_comment_row(row)
    ]
    # Exact spelling wins; only when no exact row exists do normalized
    # truncation twins get considered. If several twins match, the name
    # alone cannot identify one -- return None rather than attach the
    # wrong element's comment.
    for row in candidates:
        if row.get("Name", "") == name:
            return row
    twins = [row for row in candidates
             if names_agree(row.get("Name", ""), name)]
    return twins[0] if len(twins) == 1 else None


def find_comment_row(section, etype: str, oid: int, name: str = "",
                     *, stamp: bool = False):
    """Return the COMMENTS row for ``(etype, oid, name)``, or ``None``.

    ``stamp=True`` is for MUTATING callers only (write/rename): it writes
    the ID into a name-matched legacy row so later lookups resolve by ID.
    Reads must never stamp a shared no-ID row, or a later delete would
    treat the survivor's comment as the deleted element's.
    """
    if section is None:
        return None
    kind = str(etype).lower()
    oid = int(oid)
    row = _row_by_id(section.rows, kind, oid, name)
    if row is not None:
        return row
    row = _legacy_row_by_name(section.rows, kind, name)
    if row is not None and stamp:
        row["ID"] = str(oid)
    return row


def comment_text(section, etype: str, oid: int, name: str = "") -> str:
    """Read-only comment text; never stamps a legacy row."""
    row = find_comment_row(section, etype, oid, name)
    return (row.get("Comment", "") or "") if row is not None else ""
