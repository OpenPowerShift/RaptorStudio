"""Block library import/export.

Raptor 7's `.rbl` text format is read and written by `raptor.core.fileio.rbl` (byte-identical to
Raptor 7's own export and to its shipped `ch20.rbl`); that is the default here. A JSON format is
kept as well, chosen by a `.json` extension::

    {"format": "raptor-studio-block-library", "version": 1,
     "templates": [{"name": "SolarP_500W", "row": {..BLOCK_DATA..}}]}

Each template row written is a full BLOCK_DATA row (the same dicts the .rbd
parser produces), so the file records the whole block. **Reading** a library
uses only what an `.rbl` can hold -- name, failure and repair distributions
and parameters, the pre/post LDTs and the "dependent" flag -- so importing
the same blocks from either format gives the same result (`rbl_fields`).
Random streams, sparing, PM, costs, position and a dependency on a particular
element stay in the file but are never applied to a model.
"""
from __future__ import annotations

import json

FORMAT_ID = "raptor-studio-block-library"
VERSION = 1


def rbl_stamp(ctime_text: str) -> str:
    """`Sat Oct 10 15:26:13 2026` -> `Sat_Oct_10_15:26:13_2026`, as Raptor 7 builds its header."""
    t = ctime_text
    return f"{t[0:3]}_{t[4:7]}_{t[8:10]}_{t[11:19]}_{t[20:24]}"


def export_library(path: str, rows: list[dict], model_name: str = "Unnamed RBD") -> int:
    """Write BLOCK_DATA rows (id != 0) as a library: `.rbl` text unless the path ends `.json`.
    Returns the block count."""
    if not path.lower().endswith(".json"):
        import time
        from raptor.core.fileio.rbl import blocks_from_rows, write_rbl
        blocks = blocks_from_rows(rows)
        with open(path, "wb") as f:
            f.write(write_rbl(blocks, model_name, rbl_stamp(time.ctime())).encode("latin-1"))
        return len(blocks)
    templates = []
    for r in rows:
        rid = r.get("ID", "0")
        try:
            if int(float(rid)) == 0:
                continue
        except (TypeError, ValueError):
            continue
        templates.append({"name": r.get("blockName", ""), "row": dict(r)})
    doc = {"format": FORMAT_ID, "version": VERSION,
           "templates": templates}
    with open(path, "w", encoding="utf-8") as f:
        json.dump(doc, f, indent=1)
    return len(templates)


def rbl_fields(row: dict) -> dict:
    """`row` (a full BLOCK_DATA row) reduced to the columns an `.rbl` template
    carries, in the form an `.rbl` template has them.

    The `.rbl` format only says whether a block is *dependent* (on the whole
    system, `DepNum` -2) or not; any other dependency (on its own node or a
    particular element) is not part of a library, so it becomes "none", as it
    does for an `.rbl`."""
    from raptor.core.fileio.rbl import LibEntry, template_row
    out = {c: row[c] for c in template_row(LibEntry("")) if c in row}
    try:
        dependent = int(float(row.get("DepNum", "0") or 0)) == -2
    except (TypeError, ValueError):
        dependent = False
    out["DepNum"] = "-2" if dependent else "0"
    out["DepType"] = "None"
    return out


def load_library(path: str) -> list[dict]:
    """Read a library file (`.rbl` text, or the JSON format); returns its template list. A file the
    original would refuse raises `RblError` (a `ValueError`)."""
    with open(path, "rb") as f:
        head = f.read(9)
    if head.startswith(b"Raptor"):
        from raptor.core.fileio.rbl import read_rbl, template_row
        with open(path, "rb") as f:
            entries, _blank = read_rbl(f.read().decode("latin-1"))
        return [{"name": e.name, "row": template_row(e)} for e in entries]
    with open(path, encoding="utf-8") as f:
        doc = json.load(f)
    if doc.get("format") != FORMAT_ID:
        raise ValueError(f"{path}: not a {FORMAT_ID} file")
    return [{"name": t.get("name", ""), "row": rbl_fields(t.get("row", {}))}
            for t in doc.get("templates", [])]
