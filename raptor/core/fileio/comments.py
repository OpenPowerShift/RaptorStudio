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


def _canonical_name(name: str) -> str:
    """Compare names the way the legacy writer stores them.

    `.rbd` save truncates every name to 20 characters and gives colliding
    truncations a ``~n`` distinguisher; COMMENTS and element rows can
    therefore disagree (``LongName...`` vs ``LongName...~3``) while still
    describing the same element. The ID remains the real key.
    """
    from raptor.core.fileio.legacy_layout import LEGACY_NAME_CAP

    text = str(name or "")
    base, sep, suffix = text.rpartition("~")
    if sep and suffix.isdigit():
        text = base
    # Leave room for the longest usual `~n` distinguisher: the base of a
    # truncated twin is shorter than the cap, and both still share this
    # prefix with the element row's untruncated name.
    return text[:LEGACY_NAME_CAP - 4]


def names_agree(first: str, second: str) -> bool:
    return (not first or not second
            or _canonical_name(first) == _canonical_name(second))


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
    for row in rows:
        if str(row.get("Type", "")).lower() != kind:
            continue
        if not is_legacy_comment_row(row):
            continue                    # ID-bearing rows are authoritative
        if row.get("Name", "") == name:
            return row
    return None


def find_comment_row(section, etype: str, oid: int, name: str = "",
                     *, stamp: bool = False, allow_legacy_name: bool = True):
    """Return the COMMENTS row for ``(etype, oid, name)``, or ``None``.

    ``stamp=True`` is for MUTATING callers only (write/rename): it writes
    the ID into a name-matched legacy row so later lookups resolve by ID.
    Reads must never stamp a shared no-ID row, or a later delete would
    treat the survivor's comment as the deleted element's.

    ``allow_legacy_name=False`` disables the no-ID fallback when another
    surviving same-kind element still uses that name.
    """
    if section is None:
        return None
    kind = str(etype).lower()
    oid = int(oid)
    row = _row_by_id(section.rows, kind, oid, name)
    if row is not None:
        return row
    if not allow_legacy_name:
        return None
    row = _legacy_row_by_name(section.rows, kind, name)
    if row is not None and stamp:
        row["ID"] = str(oid)
    return row


def comment_text(section, etype: str, oid: int, name: str = "") -> str:
    """Read-only comment text; never stamps a legacy row."""
    row = find_comment_row(section, etype, oid, name)
    return (row.get("Comment", "") or "") if row is not None else ""
