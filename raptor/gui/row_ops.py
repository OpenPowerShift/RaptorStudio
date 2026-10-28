"""Creating and cloning file rows, with no Qt.

Every element the app adds is a row cloned from a real row of the loaded
file (the mandatory ID=0 defaults row), inserted into the same `RbdFile`
that Save writes -- so the write-back covers new rows with no extra
bookkeeping. These are the helpers for that, formerly methods on
`DiagramView`.
"""
from __future__ import annotations

from collections.abc import Collection
from typing import Any

from raptor.core.fileio.rbd import RbdSection

HIER_COLUMNS = ["hierarchyName", "ID", "xPos", "yPos", "Parent",
                "In", "Out", "isCNd", "zoom", "xCenter", "yCenter",
                "in", "out", "level", "Phs", "xOrigin", "yOrigin",
                "myDepth"]

#: Columns of sections a model may lack until the user first uses them.
_SECTION_COLUMNS = {
    "SPARE_POOL_DATA": ["PoolName", "RSO", "Stock", "NewSp", "ArriveEvery",
                        "ESO", "EmerTime", "SLO", "Level", "Quan",
                        "SLOTime"],
    "RESOURCE_DATA": ["PoolName", "NumIn", "SpareCost", "FixedPerUse",
                      "PerTimeUnit", "Phases"],
    "TRIGGERS": ["TriggerName", "IDnum", "Repeats", "InitUsed", "Dist",
                 "Param1", "Param2", "Param3"],
    # Header per the Phasing fixtures.
    "PHASES": ["PhaseName", "Num", "Mission", "Dist", "Param1", "Param2",
               "Param3"],
    "HIERARCHY_DATA": HIER_COLUMNS,
}


def ensure_section(rbd: Any, name: str) -> RbdSection:
    """The section, creating it empty if the model has none. New sections
    go before COMMENTS so the rendered file keeps a sane order for the
    engine's parser."""
    sec = rbd.sections.get(name)
    if sec is not None:
        return sec
    section = RbdSection(name=name, columns=list(_SECTION_COLUMNS[name]),
                         rows=[])
    rebuilt: dict = {}
    placed = False
    for key, existing in rbd.sections.items():
        if key == "COMMENTS" and not placed:
            rebuilt[name] = section
            placed = True
        rebuilt[key] = existing
    if not placed:
        rebuilt[name] = section
    rbd.sections = rebuilt
    return section


def template_row(rbd: Any, section_name: str, id_col: str) -> dict | None:
    """A copy of the section's ID=0 defaults row (or its first row), for
    cloning a new element from. None if there is no section or it is
    empty."""
    if rbd is None:
        return None
    section = rbd.sections.get(section_name)
    if section is None or not section.rows:
        return None
    for row in section.rows:
        try:
            if int(float(row.get(id_col, "-1"))) == 0:
                return dict(row)
        except (ValueError, KeyError):
            continue
    return dict(section.rows[0])


def sanitize_name(name: str) -> str:
    """Names are whitespace-delimited tokens in the file."""
    return "_".join(name.split()) or "unnamed"


def next_id(rbd: Any, section_name: str, id_col: str,
            existing: Collection[int]) -> int:
    """The lowest id used neither by a live element nor by any row of the
    section."""
    used = set(existing)
    if rbd is not None:
        section = rbd.sections.get(section_name)
        if section is not None:
            for row in section.rows:
                try:
                    used.add(int(float(row.get(id_col, "-1"))))
                except (ValueError, KeyError):
                    continue
    nid = 1
    while nid in used:
        nid += 1
    return nid


def build_hierarchy_rows(rbd: Any, *, name: str, x: float, y: float,
                         parent: int, parent_level: int | None,
                         used_ids: Collection[int], node_ids: Collection[int]
                         ) -> tuple[dict, dict, dict]:
    """Fresh (hier_row, in_row, out_row) with disjoint cross-section ids,
    `Parent` = the current level (`parent_level` is that level's own depth,
    None at the top). Rows clone the ID=0 templates so every positional
    column of the .rbd format is present."""
    ensure_section(rbd, "HIERARCHY_DATA")
    used = set(used_ids)     # one shared id namespace across sections
    hid = 1
    while hid in used:
        hid += 1
    in_id = next_id(rbd, "NODE_DATA", "ID", node_ids)
    while in_id in used or in_id == hid:
        in_id += 1
    out_id = next_id(rbd, "NODE_DATA", "ID", node_ids)
    while out_id in used or out_id in (hid, in_id):
        out_id += 1

    level = (parent_level + 1) if parent_level is not None else 1
    # No template row (freshly ensured empty section): seed numeric columns
    # with "0", not "". An empty value emits no token in the
    # whitespace-delimited format, misaligning the reader's positional
    # sequence on this row and every row after it; hierarchyName is the
    # section's only string column.
    hbase = template_row(rbd, "HIERARCHY_DATA", "ID") or {
        c: ("none" if c == "hierarchyName" else "0") for c in HIER_COLUMNS}
    hier_row = {**dict(hbase),
            "hierarchyName": sanitize_name(name),
            "ID": str(hid),
            "xPos": f"{x:.6f}", "yPos": f"{y:.6f}",
            "Parent": str(parent),
            "In": "1", "Out": "1", "isCNd": "1",
            "zoom": "14.00", "xCenter": "0.000000", "yCenter": "0.000000",
            "in": str(in_id), "out": str(out_id), "level": str(level)}
    nbase = template_row(rbd, "NODE_DATA", "ID") or {}

    def marker_row(mid: int, mtype: int, k: str, n: str, out: str,
                   mx: float) -> dict:
        r = dict(nbase)
        r.update({
            "Name": "in" if mtype == 4 else "out",
            "ID": str(mid),
            "parent": str(hid), "Type": str(mtype),
            "k": k, "n": n, "Out": out,
            "xPos": f"{mx:.6f}", "yPos": f"{y + 1.25:.6f}",
        })
        return r
    return (hier_row,
            marker_row(in_id, 4, "1", "0", "1", x - 10),
            marker_row(out_id, 5, "-1", "1", "0", x + 10))
