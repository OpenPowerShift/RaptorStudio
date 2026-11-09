"""The COMMENTS-section lookup shared by the canvas, Inspector and row editor.

COMMENTS rows are keyed by ``Type + ID`` in files this application writes,
but older files may have only ``Type + Name`` (no ID column). Duplicate
element names are legal, so:

* an ID-bearing row is NEVER returned for a different element;
* the name fallback only applies to a row with no ID (a legacy row), and
  only when the caller has a name to compare.
"""
from __future__ import annotations

from typing import Any


def _int(value: Any) -> int:
    try:
        return int(float(value))
    except (TypeError, ValueError, OverflowError):
        return -1


def is_legacy_comment_row(row) -> bool:
    """True for a COMMENTS row with no real element ID.

    ID 0 is the file format's template/sentinel value and is treated the
    same as a missing ID: both are legacy name-keyed rows.
    """
    return _int(row.get("ID")) <= 0


def _row_by_id(rows, kind: str, oid: int, name: str):
    for row in rows:
        if str(row.get("Type", "")).lower() != kind:
            continue
        if _int(row.get("ID")) != oid:
            continue
        row_name = row.get("Name", "")
        # An ID match with a different non-empty name is a stale row from
        # a reused ID, not this element's comment.
        if name and row_name and row_name != name:
            continue
        return row
    return None


def _legacy_row_by_name(rows, kind: str, name: str):
    if not name:
        return None
    for row in rows:
        if str(row.get("Type", "")).lower() != kind:
            continue
        if not is_legacy_comment_row(row):
            continue                    # ID-bearing rows are authoritative
        if row.get("Name", "") == name:
            return row
    return None


def find_comment_row(section, etype: str, oid: int, name: str = "",
                     *, stamp: bool = False):
    """Return the COMMENTS row for ``(etype, oid, name)``, or ``None``.

    ``stamp=True`` writes the ID into a name-matched legacy row, so later
    lookups (including undo/redo after a level change) resolve by ID.
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
    row = find_comment_row(section, etype, oid, name)
    return (row.get("Comment", "") or "") if row is not None else ""
