"""MCP tools for part types and libraries (WP-4.3)."""
from __future__ import annotations

from mcp.types import CallToolResult

from raptor.mcp import context
from raptor.mcp.result import ADDITIVE, READ_ONLY, guarded, ok
from raptor.mcp.server import mcp


def _library(path: str):
    from raptor.api.library.model import read_library
    return read_library(path)


@mcp.tool(annotations=READ_ONLY("Part types"))
@guarded
def list_part_types(model_id: str | None = None,
                    library_path: str | None = None,
                    query: str = "") -> CallToolResult:
    """List part types from a library file or from the open model.

    `library_path` reads a `.rbdlib` file; `model_id` lists the parts stored
    inside that model (the ones its linked blocks follow). With neither,
    lists the parts of the current model made available by `save_part_type`.
    `query` filters on id, name, category and source, case-insensitively.
    """
    from raptor.api.library.links import local_parts

    parts = {}
    source = ""
    if library_path:
        library = _library(library_path)
        parts = dict(library.parts)
        source = library.name
    elif model_id:
        parts = local_parts(context.workspace().get(model_id).rbd)
        source = f"model {model_id}"
    else:
        raise ValueError("give library_path or model_id")
    needle = query.strip().lower()
    rows = []
    for part in parts.values():
        haystack = " ".join((part.id, part.name, part.category,
                             part.source)).lower()
        if needle and needle not in haystack:
            continue
        rows.append({"id": part.id, "name": part.name,
                     "category": part.category, "source": part.source,
                     "basis": part.basis, "version": part.version,
                     "fields": part.fields})
    return ok({"source": source, "parts": rows, "count": len(rows)},
              "\n".join(f"- **{row['name']}** (`{row['id']}`) "
                        f"{row['version']} — {row['source'] or 'no source'}"
                        for row in rows) or "No part type matched.")


@mcp.tool(annotations=ADDITIVE("Apply part type"))
@guarded
def apply_part_type(model_id: str, part: str, elements: list[str],
                    library_path: str | None = None,
                    overrides: list[str] | None = None) -> CallToolResult:
    """Apply a part type to blocks and record the link.

    `part` is an id (or exact name) from `library_path`, or from the parts
    stored inside the model when no path is given. `elements` are block
    names or ids. `overrides` are fields to leave as the block has them.
    The block's columns receive the part's values; the link is bookkeeping,
    so the engine still sees an ordinary block.
    """
    from raptor.api.library.links import link_block, local_parts
    from raptor.api.library.model import PartType

    ws = context.workspace()
    if library_path:
        found = _library(library_path).get(part)
    else:
        found = local_parts(ws.get(model_id).rbd).get(part)
    if found is None:
        raise ValueError(f"no part {part!r}; list them with list_part_types")
    if not isinstance(found, PartType):
        found = PartType.from_dict(found)
    results = [link_block(ws, model_id, element, found,
                          library=library_path or "", overrides=overrides)
               for element in elements]
    return ok({"part": found.id, "version": found.version,
               "elements": results, "count": len(results)},
              f"Applied **{found.name}** to {len(results)} block(s).")


@mcp.tool(annotations=ADDITIVE("Save part type"))
@guarded
def save_part_type(model_id: str, element: str, name: str | None = None,
                   library_path: str | None = None,
                   category: str = "", source: str = "",
                   basis: str = "judgement",
                   link: bool = True) -> CallToolResult:
    """Save a block's data as a part type and optionally link the block.

    With `library_path`, adds the part to that `.rbdlib` file. Without it,
    stores the part inside the model (so the model stays self-contained) and
    links the block by default. The part never includes the block's name,
    position or id.
    """
    from raptor.api.library.links import link_block
    from raptor.api.library.model import (
        Library, part_from_block, read_library, write_library,
    )

    ws = context.workspace()
    rbd = ws.get(model_id).rbd
    from raptor.api.build import resolve_element
    section, row = resolve_element(rbd, f"Block:{element}"
                                   if str(element).isdigit() else element)
    if section != "BLOCK_DATA":
        raise ValueError(f"{element!r} is not a block")
    part = part_from_block(row, name=name, category=category, source=source,
                           basis=basis)
    if library_path:
        try:
            library = read_library(library_path)
        except FileNotFoundError:
            library = Library(library_path.rsplit("/", 1)[-1][:-7] or "Library")
        library.parts[part.id] = part
        write_library(library, library_path)
    if link:
        link_block(ws, model_id, row["ID"], part,
                   library=library_path or "")
    return ok({"part": part.to_dict(), "linked": bool(link)},
              f"Saved **{part.name}** (`{part.id}`) v{part.version}"
              + (f" to `{library_path}`" if library_path else " in the model")
              + ("; the block follows it." if link else "."))


@mcp.tool(annotations=ADDITIVE("Sync part types"))
@guarded
def sync_part_types(model_id: str, library_path: str | None = None,
                    dry_run: bool = True) -> CallToolResult:
    """List or apply the changes a library has made to linked blocks.

    `dry_run=True` (default) only reports the differences, respecting each
    block's overrides. `library_path` reads the current part definitions;
    without it the copies stored inside the model are used.
    """
    from raptor.api.library.links import apply_updates

    library = _library(library_path) if library_path else None
    result = apply_updates(context.workspace(), model_id, library=library,
                           dry_run=dry_run)
    lines = []
    for entry in result["pending"]:
        lines.append(f"- **{entry['name']}** follows {entry['name_part']} "
                     f"{entry['version']}: {', '.join(entry['changes'])}")
    return ok(result, "\n".join(lines) or "Every linked block matches its part.")
