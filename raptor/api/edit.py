"""Duplicate elements and copy block data (WP-1.5), without Qt.

The planning lives in `raptor.core.edit.clipboard_plan` (Qt-free); this module
applies a plan to a workspace model and saves it, so the CLI and MCP share
exactly the GUI's id allocation, dependency remapping and naming.
"""
from __future__ import annotations

from collections.abc import Sequence
from types import SimpleNamespace
from typing import Any

from raptor.core.edit.clipboard_plan import (
    next_free_name, plan_copy, plan_paste, plan_paste_data,
    remap_dependencies,
)

_SECTIONS = {"block": "BLOCK_DATA", "node": "NODE_DATA",
             "event": "EVENT_DATA", "hier": "HIERARCHY_DATA"}


def _int(value: Any) -> int:
    try:
        return int(float(value))
    except (TypeError, ValueError):
        return 0


def _selection(rbd: Any, elements: Sequence[str]) -> dict[str, Any]:
    from raptor.api.build import resolve_element
    kinds = {"BLOCK_DATA": "block", "NODE_DATA": "node",
             "EVENT_DATA": "event", "HIERARCHY_DATA": "hier"}
    out: dict[str, list[int]] = {"block": [], "node": [], "event": [],
                                 "hier": []}
    for selector in elements:
        section, row = resolve_element(rbd, selector)
        kind = kinds.get(section)
        if kind is None:
            raise ValueError(f"{selector!r} is not a block, node, event or "
                             "hierarchy")
        out[kind].append(_int(row.get("ID")))
    out["node"] = [nid for nid in out["node"]
                   if _int(next((row.get("Type") for row in
                                 rbd.section("NODE_DATA").rows
                                 if _int(row.get("ID")) == nid), 0)) == 2]
    return out


def _hiers(rbd: Any) -> dict[int, Any]:
    out = {}
    for row in rbd.section("HIERARCHY_DATA").rows:
        out[_int(row.get("ID"))] = SimpleNamespace(
            in_id=_int(row.get("in")), out_id=_int(row.get("out")),
            level=_int(row.get("level")), parent=_int(row.get("Parent")))
    return out


def _live(rbd: Any) -> dict[str, set[int]]:
    return {kind: {_int(row.get("ID"))
                   for row in rbd.section(section).rows}
            for kind, section in _SECTIONS.items()}


def _append(rbd: Any, plan: Any) -> None:
    from raptor.api import build
    for kind, section in _SECTIONS.items():
        rows = plan.new_rows[kind]
        if rows:
            rbd.section(section).rows.extend(rows)
    if plan.link_rows:
        rbd.section("LINK_DATA").rows.extend(plan.link_rows)
    build.sync_counts(rbd)


#: The most elements one `duplicate_elements` call may add.
MAX_DUPLICATED = 10_000


def duplicate_elements(workspace: Any, model_id: str,
                       elements: Sequence[str], *, copies: int = 1,
                       offset: tuple[float, float] = (40.0, 40.0)
                       ) -> dict[str, Any]:
    """Copy elements `copies` times with fresh ids and names.

    At most `MAX_DUPLICATED` new elements a call: copies times the
    selection (hierarchy contents included) grows fast, and one call could
    otherwise write a model too large to open."""
    if copies < 1:
        raise ValueError("copies must be at least 1")
    rbd = workspace.get(model_id).rbd
    selection = _selection(rbd, elements)
    if not any(selection.values()):
        raise ValueError("nothing to duplicate: Start, End and the In/Out "
                         "markers cannot be copied, and nothing else was named")
    hiers = _hiers(rbd)
    buffer = plan_copy(rbd, hiers, blocks=selection["block"],
                       nodes=selection["node"], events=selection["event"],
                       sel_hiers=selection["hier"], parent=0)
    made: list[dict[str, Any]] = []
    blocks = {_int(row.get("ID")): row for row in rbd.section("BLOCK_DATA").rows}
    existing = {str(row.get("blockName") or "") for row in blocks.values()}
    hier_names = {str(row.get("hierarchyName") or "")
                  for row in getattr(rbd.sections.get("HIERARCHY_DATA"), "rows", [])}
    created: list[dict[str, Any]] = []
    for index in range(copies):
        plan = plan_paste(
            rbd, buffer, _live(rbd), dx=offset[0] * (index + 1),
            dy=offset[1] * (index + 1), parent=0,
            parent_level=None)
        if index == 0:
            per_copy = sum(len(rows) for rows in plan.new_rows.values())
            if per_copy * copies > MAX_DUPLICATED:
                raise ValueError(
                    f"{copies} copies of this selection would add "
                    f"{per_copy * copies:,} elements; at most "
                    f"{MAX_DUPLICATED:,} can be added at once. Use fewer "
                    "copies, or duplicate in several steps.")
        new_blocks = {_int(row.get("ID")): row for row in plan.new_rows["block"]}
        for old_id, new_id in plan.id_maps.get("block", {}).items():
            source = blocks.get(old_id)
            target = new_blocks.get(new_id)
            if source is not None and target is not None:
                name = next_free_name(existing,
                                      str(source.get("blockName") or "Block"))
                existing.add(name)
                target["blockName"] = name
        # A copied hierarchy kept its name, so the original could no longer
        # be named on its own
        for row in plan.new_rows.get("hier", []):
            name = next_free_name(hier_names, str(row.get("hierarchyName") or "Hier"))
            hier_names.add(name)
            row["hierarchyName"] = name
        remap_dependencies(plan, keep_external=True)
        _append(rbd, plan)
        made.append({kind: [row.get("ID") for row in rows]
                     for kind, rows in plan.new_rows.items()})
        created.extend(
            {"kind": _KIND_TITLE[kind], "id": _int(row.get("ID")),
             "name": str(row.get(_NAME_COLUMN[kind]) or ""),
             "selector": f"{_KIND_TITLE[kind]}:{_int(row.get('ID'))}"}
            for kind, rows in plan.new_rows.items() if kind in _KIND_TITLE
            for row in rows)
    workspace.save(model_id)
    return {"copies": copies, "elements": made, "created": created,
            "count": sum(len(ids) for copy in made for ids in copy.values())}


#: duplicate_elements' `created` entries: the selector kind and name column
_KIND_TITLE = {"block": "Block", "node": "Node", "event": "Event", "hier": "Hier"}
_NAME_COLUMN = {"block": "blockName", "node": "Name", "event": "eventName",
                "hier": "hierarchyName"}


def copy_element_data(workspace: Any, model_id: str, source: str,
                      targets: Sequence[str],
                      groups: Sequence[str] = ("Failure", "Repair")
                      ) -> dict[str, Any]:
    """Copy one block's data groups onto other blocks, one save. An unknown
    group is refused (it used to copy nothing and report success)."""
    from raptor.api.build import resolve_element
    from raptor.core.edit.clipboard_plan import DATA_GROUPS
    unknown = [group for group in groups if group not in DATA_GROUPS]
    if unknown:
        raise ValueError(f"unknown data group(s) {', '.join(map(repr, unknown))}; "
                         f"the groups are {', '.join(DATA_GROUPS)}")
    rbd = workspace.get(model_id).rbd
    section, src = resolve_element(rbd, source)
    if section != "BLOCK_DATA":
        raise ValueError(f"{source!r} is not a block")
    rows = []
    for selector in targets:
        section, row = resolve_element(rbd, selector)
        if section != "BLOCK_DATA":
            raise ValueError(f"{selector!r} is not a block")
        rows.append(row)
    changes = plan_paste_data(src, rows, groups)
    for change in changes:
        change["row"][change["column"]] = change["new"]
    if changes:
        from raptor.api import build
        build.sync_counts(rbd)
        workspace.save(model_id)
    return {"changes": [{key: value for key, value in change.items()
                         if key != "row"} for change in changes],
            "count": len(changes), "blocks": len(rows)}


_OOS_KIND = {"BLOCK_DATA": "block", "EVENT_DATA": "event", "HIERARCHY_DATA": "hier"}


def set_out_of_service(workspace: Any, model_id: str, elements: Sequence[str],
                       state: str | None) -> dict[str, Any]:
    """Take blocks, events or hierarchies out of service for a what-if,
    without changing their data: `state` "cut" (down for the whole run,
    its path broken), "linked" (never fails, as good as a wire) or None
    (back in service). A hierarchy covers everything inside it. Saved in
    .rbds only (see `raptor.core.fileio.out_of_service`).

    Returns {changed: [{kind, id, name, state}], out_of_service: [...]},
    the second the model's whole list after the change."""
    from raptor.api.build import resolve_element
    from raptor.core.fileio import out_of_service as oos
    if state not in (None, "", *oos.STATES):
        raise ValueError(f"state must be cut, linked or in-service, not {state!r}")
    rbd = workspace.get(model_id).rbd
    changed = []
    for selector in elements:
        section, row = resolve_element(rbd, selector)
        if section not in _OOS_KIND and ":" not in str(selector):
            # A hierarchy's In/Out node can share its name: a bare name
            # means the block, event or hierarchy of that name.
            for prefix in ("Hier", "Block", "Event"):
                try:
                    section, row = resolve_element(rbd, f"{prefix}:{selector}")
                    break
                except Exception:   # noqa: BLE001 -- not that kind; try the next
                    continue
        kind = _OOS_KIND.get(section)
        if kind is None:
            raise ValueError(f"{selector!r}: only a block, event or hierarchy "
                             "can be taken out of service")
        oid = int(float(row.get("ID") or 0))
        oos.set_state(rbd, kind, oid, state or None)
        changed.append({"kind": kind, "id": str(oid), "state": state or "in service",
                        "name": str(row.get({"block": "blockName", "event": "eventName",
                                             "hier": "hierarchyName"}[kind]) or oid)})
    workspace.save(model_id)
    return {"changed": changed, "out_of_service": oos.describe(rbd)}


def out_of_service(workspace: Any, model_id: str) -> list[dict[str, str]]:
    """[{kind, id, name, state}] for what the user took out of service."""
    from raptor.core.fileio.out_of_service import describe
    return describe(workspace.get(model_id).rbd)
