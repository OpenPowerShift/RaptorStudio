"""Copy and paste of model elements as file rows, with no Qt.

A copy is a snapshot of rows -- the selected elements, everything inside a
selected hierarchy's subtree, and the links wholly inside the copied
cluster. A paste re-inserts them with fresh ids, remapping ParentId, marker
and link references so the copy is self-contained. `DiagramView` supplies
the selection and the anchor, and pushes the result as undoable commands.
"""
from __future__ import annotations

from collections.abc import Collection, Mapping
from dataclasses import dataclass, field
from typing import Any
from raptor.core.fileio.typed import row_int

#: buffer entries are (kind, row): kind is block/node/event/hier/link
#: (annotations are added by the view: they are canvas state, not rows)
Buffer = list[tuple[str, dict]]

_KIND_SECTIONS = (("Block", "BLOCK_DATA", "ParentId"),
                  ("Event", "EVENT_DATA", "ParentId"),
                  ("Node", "NODE_DATA", "parent"))


def _int(value: Any) -> int:
    """DELEGATED to `core.fileio.typed.row_int` (review E-12).

    -1 for unreadable: these become lookup keys, so a garbage id must not
    collide with a real one.
    """
    return row_int(value, -1)


def _rows(rbd: Any, section: str) -> list[dict]:
    sec = rbd.sections.get(section)
    return sec.rows if sec else []


def _chain_test(rbd: Any, targets: set[int]):
    """`pid -> does the hierarchy chain starting at pid reach a target`."""
    hier_parent = {_int(r.get("ID", 0)): _int(r.get("Parent", 0))
                   for r in _rows(rbd, "HIERARCHY_DATA")}

    def chain_contains(pid: int) -> bool:
        seen: set[int] = set()
        while pid and pid not in seen:
            if pid in targets:
                return True
            seen.add(pid)
            pid = hier_parent.get(pid, 0)
        return False
    return chain_contains


def subtree_hiers(rbd: Any, sel_h: list[int]) -> set[int]:
    """The selected hierarchies and every hierarchy nested in them, at any
    depth."""
    cone = set(sel_h)
    hier_parent = {_int(r.get("ID", 0)): _int(r.get("Parent", 0))
                   for r in _rows(rbd, "HIERARCHY_DATA")}
    changed = True
    while changed:
        changed = False
        for hid, par in hier_parent.items():
            if hid and hid not in cone and par in cone:
                cone.add(hid)
                changed = True
    return cone


def _subtree_members(rbd: Any, hiers: Mapping[int, Any],
                     sel_h: list[int]) -> dict[str, set[int]]:
    """Every block, node and event whose ancestor chain lands inside a
    selected hierarchy, plus the selected hierarchies' own In/Out markers."""
    members: dict[str, set[int]] = {"Block": set(), "Node": set(),
                                    "Event": set()}
    for hid in sel_h:
        h = hiers.get(hid)
        if h is not None:
            members["Node"].update((h.in_id, h.out_id))
    chain_contains = _chain_test(rbd, set(sel_h))
    changed = True
    while changed:
        changed = False
        for kind, sec_name, pcol in _KIND_SECTIONS:
            for r in _rows(rbd, sec_name):
                oid = _int(r.get("ID"))
                if oid == 0 or oid in members[kind]:
                    continue
                if chain_contains(_int(r.get(pcol, 0))):
                    members[kind].add(oid)
                    changed = True
    return members


def _rows_of(rbd: Any, section: str, ids: Collection[int]) -> list[dict]:
    return [dict(r) for r in _rows(rbd, section) if _int(r.get("ID")) in ids]


def _internal_links(rbd: Any, moved: dict[str, set[int]],
                    allowed_pids: set[str]) -> list[dict]:
    out = []
    for row in _rows(rbd, "LINK_DATA"):
        if str(_int(row.get("pID"))) not in allowed_pids:
            continue
        f_in = (row.get("FromType") in moved
                and _int(row.get("FromID")) in moved[row["FromType"]])
        t_in = (row.get("ToType") in moved
                and _int(row.get("ToID")) in moved[row["ToType"]])
        if f_in and t_in:
            out.append(dict(row))
    return out


def plan_copy(rbd: Any, hiers: Mapping[int, Any], *, blocks: list[int],
              nodes: list[int], events: list[int], sel_hiers: list[int],
              parent: int) -> Buffer:
    """The paste buffer for the selection made at level `parent`.

    A selected hierarchy drags its markers, everything in its subtree and
    all links internal to that cone; for a hierarchy copy "internal" means
    both endpoints inside the subtree at ANY level, not just the visible
    one."""
    sub = _subtree_members(rbd, hiers, sel_hiers)
    all_b = set(blocks) | sub["Block"]
    all_n = set(nodes) | sub["Node"]
    all_e = set(events) | sub["Event"]
    buffer: Buffer = (
        [("block", r) for r in _rows_of(rbd, "BLOCK_DATA", all_b)]
        + [("node", r) for r in _rows_of(rbd, "NODE_DATA", all_n)]
        + [("event", r) for r in _rows_of(rbd, "EVENT_DATA", all_e)])
    cone = subtree_hiers(rbd, sel_hiers)
    if sel_hiers:
        buffer += [("hier", dict(r)) for r in _rows(rbd, "HIERARCHY_DATA")
                   if _int(r.get("ID")) in cone]
    allowed = {str(parent)} | {str(h) for h in cone}
    moved = {"Block": all_b, "Node": all_n, "Event": all_e}
    buffer += [("link", r) for r in _internal_links(rbd, moved, allowed)]
    return buffer


def count_elements(buffer: Buffer) -> int:
    """What copy reports: everything but links (and annotations)."""
    return sum(1 for k, _ in buffer if k not in ("link", "annotation"))


@dataclass
class PastePlan:
    new_rows: dict[str, list[dict]] = field(default_factory=lambda: {
        "block": [], "node": [], "event": [], "hier": []})
    link_rows: list[dict] = field(default_factory=list)


def _used_ids(rbd: Any, live: Mapping[str, Collection[int]]
              ) -> tuple[dict[str, set[int]], set[int]]:
    """Per-kind ids in use (live elements and file rows), and link ids.
    The sets grow as ids are allocated: two pasted copies must not receive
    the same fresh id."""
    used: dict[str, set[int]] = {}
    for kind, section in (("block", "BLOCK_DATA"), ("node", "NODE_DATA"),
                          ("event", "EVENT_DATA"), ("hier", "HIERARCHY_DATA")):
        used[kind] = set(live[kind]) | {_int(r.get("ID"))
                                        for r in _rows(rbd, section)}
    return used, {_int(r.get("ID")) for r in _rows(rbd, "LINK_DATA")}


def _fresh(used: set[int]) -> int:
    nid = 1
    while nid in used:
        nid += 1
    used.add(nid)
    return nid


def _shifted(row: dict, new_id: int, dx: float, dy: float) -> dict:
    nr = dict(row)
    nr["ID"] = str(new_id)
    nr["xPos"] = f"{float(row.get('xPos', 0)) + dx:.6f}"
    nr["yPos"] = f"{float(row.get('yPos', 0)) + dy:.6f}"
    return nr


def _remap_parent(nr: dict, hier_id_map: dict[int, int],
                  level: int) -> None:
    """A copied element's ParentId/parent follows its hierarchy's new id if
    that hierarchy was copied too; otherwise it was a loose selection from
    the source level and now belongs to the level pasted into."""
    for parent_column in ("ParentId", "parent"):
        if parent_column in nr:
            p_old = _int(nr.get(parent_column, 0))
            nr[parent_column] = str(hier_id_map.get(p_old, level))
            break


def _remap_links(buffer: Buffer, id_maps: dict[str, dict[int, int]],
                 link_ids: set[int], parent: int,
                 hier_id_map: dict[int, int]) -> list[dict]:
    ref_kind = {"Block": "block", "Node": "node", "Event": "event"}
    used = set(link_ids)
    next_num = max(used) + 1 if used else 1
    out = []
    for kind, row in buffer:
        if kind != "link":
            continue
        nr = dict(row)
        while next_num in used:
            next_num += 1
        nr["ID"] = str(next_num)
        used.add(next_num)
        next_num += 1
        for side in ("From", "To"):
            k = ref_kind.get(row.get(side + "Type", ""))
            if k:
                old = _int(row.get(side + "ID"))
                nr[side + "ID"] = str(id_maps.get(k, {}).get(old, old))
        # a link inside a copied hierarchy stays inside its copy
        nr["pID"] = str(hier_id_map.get(_int(row.get("pID")), parent))
        out.append(nr)
    return out


def _hier_levels(elements: list[tuple[str, dict]],
                 parent_level: int | None) -> dict[int, int]:
    """New `level` of each copied hierarchy: pasted under a level of depth
    d, the copy's top hierarchy is d+1 and its nested ones keep their depth
    relative to it."""
    rows = {_int(r.get("ID")): r for k, r in elements if k == "hier"}
    base = (parent_level + 1) if parent_level is not None else 1
    out = {}
    for hid, row in rows.items():
        root = row
        while _int(root.get("Parent")) in rows:
            root = rows[_int(root.get("Parent"))]
        out[hid] = base + _int(row.get("level")) - _int(root.get("level"))
    return out


def plan_paste(rbd: Any, buffer: Buffer, live: Mapping[str, Collection[int]],
               *, dx: float, dy: float, parent: int,
               parent_level: int | None = None) -> PastePlan:
    """Fresh-id rows for pasting `buffer` at level `parent` (whose own depth
    is `parent_level`, None at the top), moved by the model-space delta
    (dx, dy). `live` maps block/node/event/hier to the ids of the live
    elements."""
    used, link_ids = _used_ids(rbd, live)
    elements = [(k, r) for k, r in buffer if k not in ("link", "annotation")]
    plan = PastePlan()
    # Fresh ids for copied hierarchies first: children's ParentId and the
    # markers' parent must map to the new hierarchy id.
    hier_id_map: dict[int, int] = {}
    used_all = (set(live["block"]) | set(live["node"]) | set(live["event"])
                | set(live["hier"]))
    for kind, row in elements:
        if kind == "hier":
            nh = max(used_all | set(hier_id_map.values())) + 1
            hier_id_map[_int(row.get("ID"))] = nh
            hier_id_map[nh] = nh        # reserve
    id_maps: dict[str, dict[int, int]] = {}
    for kind, row in elements:
        if kind == "hier":
            plan.new_rows["hier"].append(
                _shifted(row, hier_id_map[_int(row.get("ID"))], dx, dy))
            continue
        new_id = _fresh(used[kind])
        nr = _shifted(row, new_id, dx, dy)
        _remap_parent(nr, hier_id_map, parent)
        id_maps.setdefault(kind, {})[_int(row.get("ID"))] = new_id
        plan.new_rows[kind].append(nr)
    plan.link_rows = _remap_links(buffer, id_maps, link_ids, parent,
                                  hier_id_map)
    # Hierarchy rows point at their markers via in/out: remap to the fresh
    # marker ids. The copy's top hierarchy goes under the level pasted into;
    # a nested one stays under its own copied parent.
    node_map = id_maps.get("node", {})
    levels = _hier_levels(elements, parent_level)
    for old_row, nr in zip((r for k, r in elements if k == "hier"),
                           plan.new_rows["hier"]):
        for col in ("in", "out"):
            try:
                old_mk = int(float(nr.get(col, 0)))
                nr[col] = str(node_map.get(old_mk, old_mk))
            except (TypeError, ValueError):
                pass
        nr["Parent"] = str(hier_id_map.get(_int(old_row.get("Parent")),
                                           parent))
        nr["level"] = str(levels[_int(old_row.get("ID"))])
    return plan
