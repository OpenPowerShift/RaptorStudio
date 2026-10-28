"""Raptor 7's `/dev` hover text: what its status bar shows for an
element under the cursor when launched with `/dev`. The strings are the source's own, joined in field order, and a real capture (2026-10-21,
`tests/golden/TwoBlockRedundancy/legacy/dev_hover_statusbar.txt`) matched a block and two nodes.

    block / event   parentID, Id, parent name, `con out`, totals
    node            parentID, Id, typeNode, `con out`, parent name, totals
    hierarchy       the same walk as a node for the hierarchy's own row
    link            Id and its From → To endpoint names

`con out` is the number of links leaving the element; the totals are `b-` blocks, `n-` nodes,
`h-` hierarchies and `T-` all objects.
"""
from __future__ import annotations

from raptor.core.fileio.rbd import RbdFile

SEPARATOR = "   |   "


def _rows(rbd: RbdFile, name: str) -> list[dict]:
    try:
        return list(rbd.section(name).rows)
    except KeyError:
        return []


def _int(row: dict, key: str) -> int:
    try:
        return int(float(row.get(key, 0) or 0))
    except (TypeError, ValueError):
        return 0


def totals(rbd: RbdFile) -> str:
    """`b-<blocks> n-<nodes> h-<hiers> T-<objects>` (blocks without the ID 0 sentinel; objects
    are blocks + events + nodes + hierarchies)."""
    b = sum(1 for r in _rows(rbd, "BLOCK_DATA") if _int(r, "ID") != 0)
    e = len(_rows(rbd, "EVENT_DATA"))
    n = len(_rows(rbd, "NODE_DATA"))
    h = len(_rows(rbd, "HIERARCHY_DATA"))
    return f"b-{b} n-{n} h-{h} T-{b + e + n + h}"


def _hier_depths(rbd: RbdFile) -> dict[int, int]:
    """Hierarchy id -> depth, walked from each row's parent chain.

    `hierLevel`/`deepestLevel` in Raptor are display levels, not just the
    stored `level` column; a corrupt/cyclic chain stops at the repeat
    instead of hanging the hover.
    """
    rows = {_int(r, "ID"): r for r in _rows(rbd, "HIERARCHY_DATA")}
    cache: dict[int, int] = {}

    def depth(hid: int) -> int:
        if hid in cache:
            return cache[hid]
        seen: set[int] = set()
        cur, d = hid, 0
        while cur in rows and cur not in seen:
            seen.add(cur)
            d += 1
            cur = _int(rows[cur], "Parent")
        cache[hid] = d
        return d

    return {hid: depth(hid) for hid in rows}


def _endpoint_name(rbd: RbdFile, kind: str, ident: int) -> str:
    section, col = {
        "Block": ("BLOCK_DATA", "blockName"),
        "Event": ("EVENT_DATA", "eventName"),
        "Node": ("NODE_DATA", "Name"),
        "Hier": ("HIERARCHY_DATA", "hierarchyName"),
    }.get(str(kind).replace("RBD", ""), (None, None))
    if section is None:
        return f"{kind} {ident}"
    row = next((r for r in _rows(rbd, section)
                if _int(r, "ID") == ident), None)
    return str((row or {}).get(col) or f"{kind} {ident}")


def _hier_text(rbd: RbdFile, row: dict, ident: int, parent_id: int,
               parent_name: str, con_out: int,
               view: object | None) -> str:
    """The `/dev` hierarchy hover: Raptor's display level plus the
    stored hierarchy level, con-out and totals."""
    depths = _hier_depths(rbd)
    deepest = max([*depths.values(),
                   *(_int(r, "level") for r in
                     _rows(rbd, "HIERARCHY_DATA"))], default=0)
    level = _int(row, "level") or depths.get(ident, 0)
    try:
        hier_level = int(view._current_depth()) if view is not None else 0
    except (AttributeError, TypeError, ValueError):
        hier_level = 0
    parts = [f"parentID = {parent_id}     hierLevel = {hier_level}",
             f"deepestLevel = {deepest}",
             f"Id = {ident}",
             f"con out = {con_out}",
             f"level = {level}"]
    if parent_name:
        parts.append(f"parent is {parent_name}")
    parts.append(totals(rbd))
    return SEPARATOR.join(parts)


def inspector_text(rbd: RbdFile, kind: str, ident: int,
                   view: object | None = None) -> str | None:
    """The hover text for `kind` ("block", "event", "node", "hier", "link")
    `ident`, or None.

    `view` is optional and only used for the hierarchy hover's current
    `hierLevel` (a canvas state, not model data). Without it the current
    level is reported as 0, the home level.
    """
    if kind == "link":
        row = next((r for r in _rows(rbd, "LINK_DATA")
                    if _int(r, "ID") == ident), None)
        if row is None:
            return None
        source = _endpoint_name(rbd, row.get("FromType", ""),
                                _int(row, "FromID"))
        target = _endpoint_name(rbd, row.get("ToType", ""),
                                _int(row, "ToID"))
        return SEPARATOR.join((f"Id = {ident}",
                               f"parentID = {_int(row, 'pID')}",
                               f"{source} → {target}",
                               f"L-{len(_rows(rbd, 'LINK_DATA'))}",
                               totals(rbd)))
    table = {"block": "BLOCK_DATA", "event": "EVENT_DATA",
             "node": "NODE_DATA", "hier": "HIERARCHY_DATA"}.get(kind)
    if table is None:
        return None
    row = next((r for r in _rows(rbd, table) if _int(r, "ID") == ident), None)
    if row is None:
        return None
    parent_key = "ParentId" if kind in ("block", "event") else "parent" if kind == "node" else "Parent"
    parent_id = _int(row, parent_key)
    parent_name = ""
    if parent_id > 0:
        hier_row = next((r for r in _rows(rbd, "HIERARCHY_DATA") if _int(r, "ID") == parent_id), None)
        parent_name = (hier_row or {}).get("hierarchyName", "") if hier_row else ""
    ref = {"block": "Block", "event": "Event", "node": "Node", "hier": "Hier"}[kind]
    con_out = sum(1 for r in _rows(rbd, "LINK_DATA")
                  if _int(r, "FromID") == ident and r.get("FromType") == ref)
    if kind == "hier":
        return _hier_text(rbd, row, ident, parent_id, parent_name,
                          con_out, view)
    parts = [f"parentID = {parent_id}", f"Id = {ident}"]
    if kind == "node":
        parts.append(f"typeNode = {_int(row, 'Type')}")
    if parent_name:
        parts.append(f"parent is {parent_name}")
    parts.append(f"con out = {con_out}")
    parts.append(totals(rbd))
    return SEPARATOR.join(parts)
