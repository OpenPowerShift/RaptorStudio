"""Planning the row changes for hierarchy operations, with no Qt.

`DiagramView` used to work these out inline while also pushing undo
commands and redrawing. Here they are plain functions of the file rows and
a hierarchy's ids, returning a description of WHAT must change; the view
turns that into an undoable command.
"""
from __future__ import annotations

from collections.abc import Collection
from dataclasses import dataclass, field
from typing import Any
from raptor.core.fileio.typed import row_int

_ID_SECTION = {"Block": "BLOCK_DATA", "Event": "EVENT_DATA",
               "Node": "NODE_DATA"}


def _int(value: Any) -> int:
    """Row cells are text; anything unreadable is -1 (no element has it).

    DELEGATED to `core.fileio.typed.row_int` (review E-12), which
    consolidates five copies of this coercion. The -1 sentinel is kept
    deliberately: these become lookup keys, so a garbage id must not
    collide with a real one.
    """
    return row_int(value, -1)


@dataclass
class DissolvePlan:
    """What deleting one hierarchy oval changes in the file.

    Children are promoted to the enclosing level, the oval and its In/Out
    markers go, links inside the subtree are re-parented, links that
    touched the markers are cut, and links that entered or left the oval
    are rewired to whatever the markers fed / were fed by."""
    hier_row: dict | None
    marker_rows: list[dict]
    #: (link row, its index in LINK_DATA) -- the actual row objects
    removed_links: list[tuple[dict, int]] = field(default_factory=list)
    #: (section, element id, old parent cell, new parent id)
    parent_rewrites: list[tuple[str, int, str, int]] = field(
        default_factory=list)
    #: (link id, old pID, new pID)
    link_pid_rewrites: list[tuple[int, int, int]] = field(
        default_factory=list)
    #: (link id, old field values, new field values)
    rewired_links: list[tuple[int, dict, dict]] = field(default_factory=list)
    n_outside_in: int = 0
    n_outside_out: int = 0


def _subtree_test(rbd: Any, hid: int):
    """`pid -> is that parent hid, or nested (at any depth) inside it`."""
    hier_section = rbd.sections["HIERARCHY_DATA"]
    hier_parent = {_int(r.get("ID")): _int(r.get("Parent", 0))
                   for r in hier_section.rows}

    def in_subtree(pid: int) -> bool:
        seen: set[int] = set()
        while pid and pid not in seen:
            if pid == hid:
                return True
            seen.add(pid)
            pid = hier_parent.get(pid, 0)
        return False
    return in_subtree


def _parent_rewrites(rbd: Any, hid: int, marker_ids: tuple[int, int],
                     in_subtree, target: int) -> list:
    """Every element parented to the oval or inside it moves to `target`.

    The marker-id skip is section-aware: blocks, nodes and hierarchies
    carry independent id namespaces, so a marker id may only skip NODE_DATA
    rows -- applying it to every section could skip same-numbered blocks,
    leaving them parented to the deleted hierarchy."""
    out = []
    for sec_name, pcol in (("BLOCK_DATA", "ParentId"),
                           ("EVENT_DATA", "ParentId"),
                           ("NODE_DATA", "parent"),
                           ("HIERARCHY_DATA", "Parent")):
        sec = rbd.sections.get(sec_name)
        for row in (sec.rows if sec else []):
            oid = _int(row.get("ID"))
            if oid in (0, hid):
                continue
            if sec_name == "NODE_DATA" and oid in marker_ids:
                continue
            pid = _int(row.get(pcol, 0))
            if pid == hid or in_subtree(pid):
                out.append((sec_name, oid, row.get(pcol, "0"), target))
    return out


def _member_ids(rbd: Any, hid: int, marker_ids: tuple[int, int],
                in_subtree) -> set[tuple[str, int]]:
    members: set[tuple[str, int]] = set()
    for sec_name in ("BLOCK_DATA", "EVENT_DATA", "NODE_DATA"):
        parent_column = "parent" if sec_name == "NODE_DATA" else "ParentId"
        for row in rbd.sections[sec_name].rows:
            oid = _int(row.get("ID"))
            if sec_name == "NODE_DATA" and oid in marker_ids:
                continue
            pid = _int(row.get(parent_column, 0))
            if pid == hid or in_subtree(pid):
                members.add((sec_name, oid))
    return members


def _touches_marker(row: dict, marker_ids: tuple[int, int]) -> bool:
    return any(_int(row.get(s + "ID")) in marker_ids
               and row.get(s + "Type") == "Node" for s in ("From", "To"))


def _boundary_feeds(link_section: Any, hier: Any):
    """What the In marker fed, and what fed the Out marker: the reconnect
    targets for rewiring boundary links. Each is (type, id) or None."""
    in_feed = out_source = None
    for row in link_section.rows:
        if _int(row.get("FromID")) == hier.in_id and \
                row.get("FromType") == "Node":
            in_feed = (row.get("ToType", ""), _int(row.get("ToID")))
        if _int(row.get("ToID")) == hier.out_id and \
                row.get("ToType") == "Node":
            out_source = (row.get("FromType", ""), _int(row.get("FromID")))
    return in_feed, out_source


def _rewire_boundary(plan: DissolvePlan, r: dict, hid: int, in_feed,
                     out_source, target: int) -> bool:
    """Rewire a link that enters or leaves the oval to what the markers
    fed / were fed by. True when it did (the link then needs nothing else)."""
    lid = _int(r.get("ID"))
    if r.get("ToType") == "Hier" and _int(r.get("ToID")) == hid \
            and in_feed is not None and in_feed[0]:
        # X -> Hier becomes X -> (whatever the In marker fed)
        plan.rewired_links.append((lid, {
            "ToType": r.get("ToType", ""), "ToID": r.get("ToID", "")}, {
            "ToType": in_feed[0], "ToID": str(in_feed[1]),
            "pID": str(target)}))
        plan.n_outside_in += 1
        return True
    if r.get("FromType") == "Hier" and _int(r.get("FromID")) == hid \
            and out_source is not None and out_source[0]:
        plan.rewired_links.append((lid, {
            "FromType": r.get("FromType", ""),
            "FromID": r.get("FromID", "")}, {
            "FromType": out_source[0], "FromID": str(out_source[1]),
            "pID": str(target)}))
        plan.n_outside_out += 1
        return True
    return False


def _endpoint_inside(r: dict, members: set) -> bool:
    for side in ("From", "To"):
        sec2 = _ID_SECTION.get(r.get(side + "Type", ""))
        if sec2 and (sec2, _int(r.get(side + "ID"))) in members:
            return True
    return False


def _references_oval(r: dict, hid: int) -> bool:
    return ((r.get("ToType") == "Hier" and _int(r.get("ToID")) == hid)
            or (r.get("FromType") == "Hier" and _int(r.get("FromID")) == hid))


def _drop_redundant_halves(plan: DissolvePlan, link_section: Any,
                           marker_ids: tuple[int, int]) -> None:
    """A rewired boundary link makes the marker's own half redundant."""
    rewired = {rl[0] for rl in plan.rewired_links}
    for row in list(link_section.rows):
        if _int(row.get("ID")) in rewired and _touches_marker(row,
                                                              marker_ids):
            plan.removed_links.append((row, link_section.rows.index(row)))


def _plan_links(plan: DissolvePlan, link_section: Any, hid: int,
                marker_ids: tuple[int, int], in_feed, out_source,
                members: set, target: int) -> None:
    for idx, r in enumerate(list(link_section.rows)):
        if _rewire_boundary(plan, r, hid, in_feed, out_source, target):
            continue
        if _touches_marker(r, marker_ids) or _references_oval(r, hid):
            # the marker's own halves, and oval references whose inside
            # half no longer exists
            plan.removed_links.append((r, idx))
        elif _endpoint_inside(r, members):
            old_pid = _int(r.get("pID", 0))
            if old_pid != target:
                plan.link_pid_rewrites.append(
                    (_int(r.get("ID")), old_pid, target))
    _drop_redundant_halves(plan, link_section, marker_ids)


def plan_dissolve(rbd: Any, hier: Any, hid: int) -> DissolvePlan:
    """The row changes for deleting hierarchy `hid` (object `hier`)."""
    target = hier.parent_id or 0
    hier_section = rbd.sections["HIERARCHY_DATA"]
    hier_row = next((r for r in hier_section.rows if _int(r.get("ID")) == hid), None)
    marker_ids = (hier.in_id, hier.out_id)
    plan = DissolvePlan(
        hier_row=hier_row,
        marker_rows=[dict(r) for r in rbd.sections["NODE_DATA"].rows
                     if _int(r.get("ID")) in marker_ids])
    in_subtree = _subtree_test(rbd, hid)
    plan.parent_rewrites = _parent_rewrites(rbd, hid, marker_ids,
                                            in_subtree, target)
    link_section = rbd.sections["LINK_DATA"]
    in_feed, out_source = _boundary_feeds(link_section, hier)
    members = _member_ids(rbd, hid, marker_ids, in_subtree)
    _plan_links(plan, link_section, hid, marker_ids, in_feed, out_source, members,
                target)
    return plan


# -- collapse ----------------------------------------------------------

#: Which column of each section names an element's parent level.
PARENT_COLS = {"BLOCK_DATA": "ParentId", "NODE_DATA": "parent",
               "EVENT_DATA": "ParentId"}


@dataclass
class CollapsePlan:
    """What collapsing a selection into a new hierarchy changes."""
    #: (section, element id, old parent cell): re-parent into the oval
    parent_moves: list[tuple[str, int, str]] = field(default_factory=list)
    #: ("del", row, index) / ("add", new row, -1), in application order
    link_ops: list[tuple[str, dict, int]] = field(default_factory=list)


def _parent_moves(rbd: Any, selected: dict[str, list[int]]) -> list:
    moves = []
    for section in ("BLOCK_DATA", "NODE_DATA", "EVENT_DATA"):
        sec = rbd.sections.get(section)
        if sec is None:
            continue
        for oid in selected[section]:
            row = next((r for r in sec.rows if _int(r.get("ID")) == oid),
                       None)
            old = row.get(PARENT_COLS[section], "0") if row else "0"
            moves.append((section, oid, old))
    return moves


def _collapse_link(row: dict, idx: int, f_in: bool, t_in: bool, hid: int,
                   in_id: int, out_id: int, fresh_id) -> list:
    """Link ops for one link with at least one end in the selection."""
    if f_in and t_in:
        # Fully internal: travels with the selection.
        internal = dict(row)
        internal["pID"] = str(hid)
        return [("del", row, idx), ("add", internal, -1)]
    outside, inside = dict(row), dict(row)
    if t_in:
        # Incoming boundary: X -> S becomes X -> Hier (outside, keeps the
        # original ID) + In-marker -> S (inside).
        outside["ToType"], outside["ToID"] = "Hier", str(hid)
        inside["FromType"], inside["FromID"] = "Node", str(in_id)
    else:
        # Outgoing boundary: S -> X becomes S -> Out-marker (inside) +
        # Hier -> X (outside, keeps the ID).
        outside["FromType"], outside["FromID"] = "Hier", str(hid)
        inside["ToType"], inside["ToID"] = "Node", str(out_id)
    inside["pID"] = str(hid)
    inside["ID"] = fresh_id()
    return [("del", row, idx), ("add", outside, -1), ("add", inside, -1)]


def plan_collapse(rbd: Any, *, hid: int, in_id: int, out_id: int,
                  parent: int, blocks: list[int], nodes: list[int],
                  events: list[int]) -> CollapsePlan:
    """The row changes for collapsing the given elements of level `parent`
    into the new hierarchy `hid` (whose In/Out markers are `in_id` and
    `out_id`). Internal links move into it; boundary links are split into
    an outside half referencing the oval (the file convention: `Node ->
    Hier` / `Hier -> Node` rows on the parent level) and an inside half on
    the marker."""
    selected = {"BLOCK_DATA": blocks, "NODE_DATA": nodes,
                "EVENT_DATA": events}
    by_ref = {"Block": set(blocks), "Node": set(nodes), "Event": set(events)}
    plan = CollapsePlan(parent_moves=_parent_moves(rbd, selected))
    link_section = rbd.sections.get("LINK_DATA")
    if link_section is None:
        return plan
    used_link = {_int(r.get("ID")) for r in link_section.rows}
    counter = [max(used_link) + 1 if used_link else 1]

    def fresh_id() -> str:
        while counter[0] in used_link:
            counter[0] += 1
        used_link.add(counter[0])
        return str(counter[0])

    for idx, row in enumerate(list(link_section.rows)):
        if _int(row.get("pID")) != parent:
            continue
        f_in = _int(row.get("FromID")) in by_ref.get(row.get("FromType"),
                                                     ())
        t_in = _int(row.get("ToID")) in by_ref.get(row.get("ToType"), ())
        if f_in or t_in:
            plan.link_ops.extend(_collapse_link(row, idx, f_in, t_in, hid,
                                                in_id, out_id, fresh_id))
    return plan


# -- import an .rbd as a hierarchy --------------------------------------

@dataclass
class ImportPlan:
    """Rows to add for an imported model, ids remapped into fresh ranges
    and its home level hung directly under the new hierarchy."""
    hier_rows: list[dict]
    node_rows: list[dict]
    block_rows: list[dict]
    event_rows: list[dict]
    link_rows: list[dict]
    #: the imported Start / End, which become the wrapper's In / Out markers
    in_id: int
    out_id: int


def _fresh_map(host: Any, imported: Any, section_name: str) -> dict[int, int]:
    """old id -> new id for one section, avoiding host and imported ids."""
    host_sec = host.sections.get(section_name)
    used = {_int(r.get("ID")) for r in host_sec.rows} if host_sec else set()
    imp = imported.sections.get(section_name)
    mapping: dict[int, int] = {}
    if imp:
        nxt = (max(used) + 1) if used else 1
        for r in imp.rows:
            old = _int(r.get("ID"))
            while nxt in used:
                nxt += 1
            mapping[old] = nxt
            used.add(nxt)
            nxt += 1
    return mapping


def _real_rows(sec: Any) -> list[dict]:
    """Every section's ID=0 row is the defaults template, not an element:
    importing it would create a phantom element with no links, which the
    engine's graph walk dies on."""
    return [r for r in sec.rows if _int(r.get("ID")) != 0] if sec else []


def _start_end(imported: Any, path: str) -> tuple[dict, dict]:
    """Raptor's own Import RBD expects a top-level model: exactly one Start
    and one End. Extra ones would each become an extra in/out marker."""
    node_section = imported.sections.get("NODE_DATA")
    rows = node_section.rows if node_section else []
    starts = [r for r in rows if _int(r.get("Type")) == 1]
    ends = [r for r in rows if _int(r.get("Type")) == 3]
    if len(starts) != 1 or len(ends) != 1:
        raise ValueError(
            f"{path}: the imported model needs EXACTLY one Start "
            f"and one End node to import as a hierarchy "
            f"(found {len(starts)} Start, {len(ends)} End).")
    return starts[0], ends[0]


def _remapped(row: dict, mapping: dict, parent_col: str, hid: int) -> dict:
    r = dict(row)
    r["ID"] = str(mapping[_int(row.get("ID"))])
    old_p = _int(row.get(parent_col, 0))
    r[parent_col] = str(hid) if old_p == 0 else str(mapping.get(old_p, hid))
    return r


def _remap_link(row: dict, maps: dict, hid: int) -> dict:
    r = dict(row)
    r["ID"] = str(maps["LINK"][_int(row.get("ID"))])
    for side in ("From", "To"):
        ref = row.get(side + "Type", "")
        m = {"Block": maps["BLOCK"], "Node": maps["NODE"],
             "Event": maps["EVENT"]}.get(ref)
        oid = _int(row.get(side + "ID"))
        if m and oid in m:
            r[side + "ID"] = str(m[oid])
        elif ref == "Hier" and oid == 0:
            r[side + "ID"] = str(hid)
        else:
            r[side + "ID"] = str(maps["HIER"].get(oid, oid))
    old_pid = _int(row.get("pID"))
    r["pID"] = str(hid) if old_pid == 0 else str(maps["HIER"].get(old_pid,
                                                                  hid))
    return r


def _node_rows(imported: Any, maps: dict, hid: int, start_id: int,
               end_id: int) -> list[dict]:
    """Home-level imported nodes hang under the new hierarchy; the Start
    and End twins turn into its In/Out markers."""
    out = []
    for r in _real_rows(imported.sections.get("NODE_DATA")):
        nr = _remapped(r, maps["NODE"], "parent", hid)
        old_id = _int(r.get("ID"))
        if old_id == start_id:
            nr.update({"Name": "in", "Type": "4", "k": "1", "n": "0",
                       "Out": "1"})
        elif old_id == end_id:
            nr.update({"Name": "out", "Type": "5", "k": "-1", "n": "1",
                       "Out": "0"})
        out.append(nr)
    return out


def plan_import(host: Any, imported: Any, path: str, *, hid: int,
                wrapper_ids: Collection[int]) -> ImportPlan:
    """Rows for importing `imported` into `host` under the new hierarchy
    `hid`. `wrapper_ids` are the ids reserved for the wrapper (its own and
    its markers'), which the remap must avoid. Raises ValueError unless the
    imported model has exactly one Start and one End."""
    wrapper = list(wrapper_ids)
    maps = {"BLOCK": _fresh_map(host, imported, "BLOCK_DATA"),
            "NODE": _fresh_map(host, imported, "NODE_DATA"),
            "EVENT": _fresh_map(host, imported, "EVENT_DATA"),
            "LINK": _fresh_map(host, imported, "LINK_DATA"),
            "HIER": _fresh_map(host, imported, "HIERARCHY_DATA")}
    for m in maps.values():
        for k in list(m):
            if m[k] in wrapper:
                # collision with the wrapper trio: shift beyond max
                m[k] = max(m.values()) + 1
    start_row, end_row = _start_end(imported, path)
    start_id, end_id = _int(start_row["ID"]), _int(end_row["ID"])

    hier_rows = []
    for r in _real_rows(imported.sections.get("HIERARCHY_DATA")):
        hr = _remapped(r, maps["HIER"], "Parent", hid)
        for col in ("in", "out"):
            old = _int(r.get(col))
            hr[col] = str(maps["NODE"].get(old, old))
        hier_rows.append(hr)
    return ImportPlan(
        hier_rows=hier_rows,
        node_rows=_node_rows(imported, maps, hid, start_id, end_id),
        block_rows=[_remapped(r, maps["BLOCK"], "ParentId", hid)
                    for r in _real_rows(imported.sections.get("BLOCK_DATA"))],
        event_rows=[_remapped(r, maps["EVENT"], "ParentId", hid)
                    for r in _real_rows(imported.sections.get("EVENT_DATA"))],
        link_rows=[_remap_link(r, maps, hid)
                   for r in _real_rows(imported.sections.get("LINK_DATA"))],
        in_id=maps["NODE"][start_id], out_id=maps["NODE"][end_id])
