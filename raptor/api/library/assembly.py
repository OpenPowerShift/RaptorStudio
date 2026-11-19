"""Assemblies: reusable groups of elements in a part library.

A part type (`raptor.api.library.model.PartType` with kind block, node, ...)
is a set of *values* applied to an element that already exists.  An
assembly is a *structure*: any selection -- a hierarchy with everything
inside it, a node with its blocks, an event, a whole redundant group --
saved with its internal links and inserted later as a fresh copy.

Saving and inserting reuse the copy/paste planner the canvas uses
(`raptor.core.edit.clipboard_plan`, Qt-free), so an inserted assembly gets new ids,
free names, its own hierarchy subtree and its links remapped exactly as a
paste would; spare pools and resources the target model lacks are reset the
way a paste from another model resets them.

An assembly is stored as a `PartType` with ``kind="assembly"`` and
``fields={"items": [[kind, row], ...]}`` (file rows as text), so libraries,
the model's own part list, the CLI and MCP carry it with no new format.
"""
from __future__ import annotations

from collections.abc import Sequence
from typing import Any

from raptor.api.library.model import PartType, _slug

ASSEMBLY = "assembly"


def summary(part: PartType) -> dict[str, int]:
    """How many of each element kind an assembly holds."""
    counts: dict[str, int] = {}
    for kind, _row in part.fields.get("items") or ():
        counts[kind] = counts.get(kind, 0) + 1
    return counts


def describe(part: PartType) -> str:
    """`2 blocks, 1 hierarchy, 3 links` for an assembly."""
    names = {"block": "block", "node": "node", "event": "event",
             "hier": "hierarchy", "link": "link", "annotation": "note"}
    parts = []
    for kind in ("hier", "block", "node", "event", "link", "annotation"):
        n = summary(part).get(kind, 0)
        if n:
            word = names[kind]
            plural = "hierarchies" if kind == "hier" else word + "s"
            parts.append(f"{n} {word if n == 1 else plural}")
    return ", ".join(parts) or "empty"


def assembly_from_elements(rbd: Any, elements: Sequence[str], *, name: str,
                           category: str = "", description: str = "",
                           source: str = "", parent: int = 0) -> PartType:
    """An assembly of the named elements (and, for a hierarchy, all it holds).

    `elements` are selectors as `raptor.api.build.resolve_element` reads
    them (a name, an id, or ``kind:name``).
    """
    from raptor.api.edit import _hiers, _selection
    from raptor.core.edit.clipboard_plan import plan_copy

    selection = _selection(rbd, elements)
    if not any(selection.values()):
        raise ValueError("nothing to save: name at least one element")
    buffer = plan_copy(rbd, _hiers(rbd), blocks=selection["block"],
                       nodes=selection["node"], events=selection["event"],
                       sel_hiers=selection["hier"], parent=parent)
    return assembly_from_buffer(buffer, name=name, category=category,
                                description=description, source=source)


def assembly_from_buffer(buffer: Sequence[tuple[str, dict]], *, name: str,
                         category: str = "", description: str = "",
                         source: str = "") -> PartType:
    """An assembly from a copy buffer (what the canvas copies)."""
    items = [[kind, dict(row)] for kind, row in buffer]
    if not any(kind not in ("link",) for kind, _row in items):
        raise ValueError("an assembly needs at least one element or note")
    return PartType(id=f"asm-{_slug(name)}", name=name,
                    fields={"items": items}, category=category,
                    description=description, source=source, kind=ASSEMBLY)


def buffer_of(part: PartType) -> list[tuple[str, dict]]:
    """The assembly's rows as a copy buffer."""
    if part.kind != ASSEMBLY:
        raise ValueError(f"{part.name!r} is a {part.kind} part type, not an "
                         "assembly")
    return [(str(kind), dict(row)) for kind, row in part.fields.get("items") or ()]


def adapted_buffer(rbd: Any, part: PartType) -> tuple[list[tuple[str, dict]],
                                                      list[str]]:
    """The buffer fitted to `rbd`: references to spare pools, resources or
    other model data it does not have are reset, as a paste from another
    model resets them.  Returns (buffer, notes)."""
    from raptor.core.edit.clipboard_payload import Payload, adapt_for_target
    adapted = adapt_for_target(rbd, Payload(buffer=buffer_of(part)),
                               same_model=False)
    return adapted.buffer, list(adapted.notes)


_NAME_COLUMNS = {"block": ("BLOCK_DATA", "blockName"),
                 "event": ("EVENT_DATA", "eventName"),
                 "hier": ("HIERARCHY_DATA", "hierarchyName")}


def renamed_buffer(rbd: Any, buffer: Sequence[tuple[str, dict]]
                   ) -> list[tuple[str, dict]]:
    """The buffer with each block, event and hierarchy whose name `rbd`
    already uses given a free one (``Pump`` -> ``Pump_2``), so an inserted
    assembly never duplicates a name; a free name is kept. The canvas and the command line both insert through
    this, so they name the copy alike."""
    from raptor.core.edit.clipboard_plan import next_free_name
    existing = {kind: {str(row.get(column) or "")
                       for row in rbd.section(section).rows}
                for kind, (section, column) in _NAME_COLUMNS.items()}
    out: list[tuple[str, dict]] = []
    for kind, row in buffer:
        row = dict(row)
        if kind in _NAME_COLUMNS:
            column = _NAME_COLUMNS[kind][1]
            name = str(row.get(column) or kind.title())
            if name in existing[kind]:
                # only a name already taken changes: renaming every element
                # turned B_HI2_0 into B_HI2_1 in a model with no B_HI2_0
                name = next_free_name(existing[kind], name)
            existing[kind].add(name)
            row[column] = name
        out.append((kind, row))
    return out


def insert_assembly(rbd: Any, part: PartType, *,
                    at: tuple[float, float] | None = None,
                    parent: int = 0) -> dict[str, Any]:
    """Insert a fresh copy of an assembly into `rbd` (no save).

    `at` puts the copy's top-left at that model position; by default it
    goes to the right of everything already in the model.  A name the model
    already uses is changed to a free one (``Pump`` -> ``Pump_2``; listed
    in ``renamed``) and the copy's dependencies on elements
    outside it are dropped (they named elements of another model).
    """
    from raptor.api.edit import _append, _int, _live
    from raptor.core.edit.clipboard_plan import plan_paste, remap_dependencies

    buffer, notes = adapted_buffer(rbd, part)
    named = renamed_buffer(rbd, buffer)
    renamed = {}
    for (kind, before), (_kind, after) in zip(buffer, named):
        if kind in _NAME_COLUMNS:
            column = _NAME_COLUMNS[kind][1]
            if before.get(column) != after.get(column):
                renamed[str(before.get(column) or "")] = str(after.get(column))
    buffer = named
    elements = [(kind, row) for kind, row in buffer
                if kind not in ("link", "annotation")]
    if not elements:
        raise ValueError(f"{part.name!r} holds no element to insert")
    xs = [float(row.get("xPos") or 0) for _kind, row in elements]
    ys = [float(row.get("yPos") or 0) for _kind, row in elements]
    if at is None:
        used_x = [float(row.get("xPos") or 0)
                  for section in ("BLOCK_DATA", "NODE_DATA", "EVENT_DATA",
                                  "HIERARCHY_DATA")
                  for row in rbd.section(section).rows]
        at = ((max(used_x) + 4.0) if used_x else 0.0, min(ys))
    dx, dy = at[0] - min(xs), at[1] - min(ys)
    level = None
    if parent:
        row = next((r for r in rbd.section("HIERARCHY_DATA").rows
                    if _int(r.get("ID")) == parent), None)
        if row is None:
            raise ValueError(f"no hierarchy with id {parent}")
        level = _int(row.get("level"))
    plan = plan_paste(rbd, buffer, _live(rbd), dx=dx, dy=dy, parent=parent,
                      parent_level=level)
    remap_dependencies(plan, keep_external=False)
    _append(rbd, plan)
    made = {kind: [row.get("ID") for row in rows]
            for kind, rows in plan.new_rows.items() if rows}
    annotations = [dict(row) for kind, row in buffer if kind == "annotation"]
    return {"part": part.id, "name": part.name, "elements": made,
            "count": sum(len(ids) for ids in made.values()),
            "renamed": renamed, "annotations": annotations, "notes": notes}


def add_local_part(rbd: Any, part: PartType) -> None:
    """Keep a part (an assembly or a part type) in the model's own list."""
    from raptor.api.library.links import _links, _store
    links = _links(rbd)
    links["parts"][part.id] = part.to_dict()
    _store(rbd, links)


def remove_local_part(rbd: Any, part_id: str) -> bool:
    """Drop a part from the model's own list; links to it are kept."""
    from raptor.api.library.links import _links, _store
    links = _links(rbd)
    removed = links["parts"].pop(part_id, None) is not None
    _store(rbd, links)
    return removed
