"""MCP tools for part types and libraries (WP-4.3)."""
from __future__ import annotations

from typing import Literal

from mcp.types import CallToolResult

from raptor.mcp import context
from raptor.mcp.result import DESTRUCTIVE, EDIT, READ_ONLY, guarded, ok
from raptor.mcp.server import mcp


def _library_file(path: str) -> str:
    """An existing library inside the workspace or an `--allow-read`
    directory.  Same boundary as `open_model`: a relative path, and a file
    outside the allowed roots answers like one that does not exist."""
    from pathlib import Path
    path = context.require_relative_path(path, "library_path")
    for root in (context.workspace().root, *context.allowed_read_dirs()):
        try:
            base = Path(root).resolve()
            found = (base / path).resolve()
        except OSError:
            continue
        if base in found.parents and found.is_file():
            return str(found)
    raise FileNotFoundError(f"No library at {path!r} in the workspace or an "
                            "--allow-read directory.")


def _library(path: str):
    from raptor.api.library.model import read_library
    found = _library_file(path)
    try:
        return read_library(found)
    except (ValueError, KeyError, TypeError, UnicodeDecodeError):
        # never echo the file: a parse error quoting it would read host files
        raise ValueError(f"{path!r} is not a part library (.rbdlib)") from None


def _library_for_writing(path: str, name: str):
    """(library, resolved path): the existing library at `path`, or a new
    one, at a `.rbdlib` path inside the workspace (or an `--allow-write`
    directory), never inside the trees the workspace manages."""
    from pathlib import PurePosixPath

    from raptor.api.library.model import Library
    path = context.require_relative_path(path, "library_path")
    suffix = PurePosixPath(path).suffix.lower()
    if suffix == "":
        path += ".rbdlib"
    elif suffix != ".rbdlib":
        raise ValueError(f"a part library is a .rbdlib file; got {suffix!r}")
    ws = context.workspace()
    dest = ws.resolve_output_path(path, allow_extra=context.allowed_write_dirs())
    ws.refuse_managed(dest)
    if dest.is_file():
        return _library(path), str(dest)
    dest.parent.mkdir(parents=True, exist_ok=True)
    return Library(name, path=str(dest)), str(dest)


def _write_library(library, dest: str) -> None:
    """Write `library`, inside the workspace's size cap."""
    import json
    from pathlib import Path

    from raptor.api.library.model import write_library
    ws = context.workspace()
    if ws.root.resolve() in Path(dest).resolve().parents:
        ws.check_quota(len(json.dumps(library.to_dict(), indent=1)) + 1)
    write_library(library, dest)


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
        rows.append({"id": part.id, "name": part.name, "kind": part.kind,
                     "category": part.category, "source": part.source,
                     "basis": part.basis, "version": part.version,
                     "fields": part.fields})
    return ok({"source": source, "parts": rows, "count": len(rows)},
              "\n".join(f"- **{row['name']}** (`{row['id']}`) "
                        f"{row['version']} — {row['source'] or 'no source'}"
                        for row in rows) or "No part type matched.")


@mcp.tool(annotations=EDIT("Apply part type"))
@guarded
def apply_part_type(model_id: str, part: str, elements: list[str],
                    library_path: str | None = None,
                    overrides: list[str] | None = None) -> CallToolResult:
    """Apply a part type to blocks and record the link.

    `part` is an id (or exact name) from `library_path`, or from the parts
    stored inside the model when no path is given. `elements` are names or
    ids of elements of the part's kind (block, node, event, hierarchy or
    annotation). `overrides` are fields to leave as the element has them.
    The element's columns receive the part's values; the link is
    bookkeeping, so the engine still sees an ordinary element.
    """
    from raptor.api.library.links import link_element, local_parts
    from raptor.api.library.model import PartType

    context.require_write()
    ws = context.workspace()
    if library_path:
        found = _library(library_path).get(part)
    else:
        found = local_parts(ws.get(model_id).rbd).get(part)
    if found is None:
        raise ValueError(f"no part {part!r}; list them with list_part_types")
    if not isinstance(found, PartType):
        found = PartType.from_dict(found)
    context.check_batch(elements, "elements")
    # one save and one undo step for the lot, all or nothing: per element
    # it saved the whole model each time (22 s for 240 blocks) and one
    # undo took back only the last element
    with ws.batch(model_id):
        results = [link_element(ws, model_id, found.kind, element, found,
                                library=library_path or "",
                                overrides=overrides)
                   for element in elements]
    return ok({"part": found.id, "version": found.version,
               "elements": results, "count": len(results)},
              f"Applied **{found.name}** to {len(results)} block(s).")


@mcp.tool(annotations=DESTRUCTIVE("Save part type"))
@guarded
def save_part_type(model_id: str, element: str, name: str | None = None,
                   library_path: str | None = None, kind: str = "block",
                   category: str = "", source: str = "",
                   basis: Literal["data", "handbook", "experience", "judgement"] = "experience",
                   link: bool = True) -> CallToolResult:
    """Save an element's data as a part type and optionally link it.

    `kind` is block, node, event, hierarchy or annotation; `element` is the
    name/id (or annotation id). With `library_path`, adds the part to that
    `.rbdlib` file. Without it, stores the part inside the model and links
    the element by default. `basis` says where the numbers came from:
    data, handbook or experience ("judgement" is read as experience). The
    part never includes the element's name, position or id.
    """
    from raptor.api.library.links import apply_part_to_rbd
    from raptor.api.library.model import part_from_element

    context.require_write()
    ws = context.workspace()
    rbd = ws.get(model_id).rbd
    if kind == "annotation":
        row = next((entry for entry in getattr(rbd, "annotations", []) or []
                    if str(entry.get("id")) == str(element)), None)
        if row is None:
            raise ValueError(f"annotation {element!r} not found")
    else:
        from raptor.api.build import resolve_element
        label = {"block": "Block", "node": "Node", "event": "Event",
                 "hierarchy": "Hier"}[kind]
        section, row = resolve_element(
            rbd, f"{label}:{element}" if str(element).isdigit() else element)
        expected = {"block": "BLOCK_DATA", "node": "NODE_DATA",
                    "event": "EVENT_DATA", "hierarchy": "HIERARCHY_DATA"}[kind]
        if section != expected:
            raise ValueError(f"{element!r} is not a {kind}")
    part = part_from_element(kind, row, name=name, category=category,
                             source=source, basis=basis)
    if library_path:
        stem = library_path.rsplit("/", 1)[-1].rsplit(".", 1)[0]
        library, dest = _library_for_writing(library_path, stem or "Library")
        library.parts[part.id] = part
        _write_library(library, dest)
    if link:
        apply_part_to_rbd(rbd, f"{kind}:{row.get('ID')}", part,
                          library=library_path or "")
        ws.save(model_id)
    return ok({"part": part.to_dict(), "linked": bool(link)},
              f"Saved **{part.name}** (`{part.id}`) v{part.version}"
              + (f" to `{library_path}`" if library_path else " in the model")
              + ("; the element follows it." if link else "."))


@mcp.tool(annotations=EDIT("Sync part types"))
@guarded
def sync_part_types(model_id: str, library_path: str | None = None,
                    dry_run: bool = True) -> CallToolResult:
    """List or apply the changes a library has made to linked blocks.

    `dry_run=True` (default) only reports the differences, respecting each
    block's overrides. `library_path` reads the current part definitions;
    without it the copies stored inside the model are used.
    """
    from raptor.api.library.links import apply_updates

    if not dry_run:
        context.require_write()
    library = _library(library_path) if library_path else None
    result = apply_updates(context.workspace(), model_id, library=library,
                           dry_run=dry_run)
    lines = []
    for entry in result["pending"]:
        lines.append(f"- **{entry['name']}** follows {entry['name_part']} "
                     f"{entry['version']}: {', '.join(entry['changes'])}")
    return ok(result, "\n".join(lines) or "Every linked block matches its part.")


@mcp.tool(annotations=DESTRUCTIVE("Save assembly"))
@guarded
def save_assembly(model_id: str, elements: list[str], name: str,
                  library_path: str | None = None, category: str = "",
                  source: str = "") -> CallToolResult:
    """Save a group of elements as a reusable assembly.

    An assembly is structure, not values: the named `elements` (blocks,
    nodes, events, hierarchies), everything inside a named hierarchy, and
    the links between them.  `insert_assembly` puts a fresh copy into any
    model.  With `library_path` the assembly is added to that `.rbdlib`
    file; without it, it is kept in the model's own part list.  The model
    itself is not changed.
    """
    from raptor.api.library.assembly import (
        add_local_part, assembly_from_elements, describe,
    )
    context.require_write()
    ws = context.workspace()
    rbd = ws.get(model_id).rbd
    part = assembly_from_elements(rbd, elements, name=name,
                                  category=category, source=source)
    if library_path:
        stem = library_path.rsplit("/", 1)[-1].rsplit(".", 1)[0]
        library, dest = _library_for_writing(library_path, stem or "Library")
        library.parts[part.id] = part
        _write_library(library, dest)
    else:
        add_local_part(rbd, part)
        ws.save(model_id)
    return ok({"part": part.id, "name": part.name, "contents": describe(part),
               "library_path": library_path},
              f"Saved assembly **{part.name}** (`{part.id}`): {describe(part)}"
              + (f" to `{library_path}`." if library_path else " in the model."))


@mcp.tool(annotations=EDIT("Insert assembly", idempotent=False))
@guarded
def insert_assembly(model_id: str, part: str,
                    library_path: str | None = None,
                    x: float | None = None, y: float | None = None
                    ) -> CallToolResult:
    """Insert a fresh copy of an assembly into a model.

    `part` is the assembly's id or name, from `library_path` or the model's
    own part list.  The copy gets new ids; a name the model already uses
    becomes a free one (Pump -> Pump_2), listed in `renamed` {old: new}; a
    hierarchy keeps everything inside it.  It is placed at (`x`, `y`) or,
    by default, to the right of the model, and is NOT connected: link it in
    with `connect`.  Spare pools or resources the model lacks are reset and
    listed in `notes`.
    """
    from raptor.api.library.assembly import insert_assembly as insert
    from raptor.api.library.links import local_parts

    context.require_write()
    ws = context.workspace()
    rbd = ws.get(model_id).rbd
    found = (_library(library_path).get(part) if library_path
             else local_parts(rbd).get(part)
             or next((p for p in local_parts(rbd).values() if p.name == part),
                     None))
    if found is None:
        raise ValueError(f"no assembly {part!r}; list them with list_part_types")
    if (x is None) != (y is None):
        # one of them alone was ignored without a word
        raise ValueError("give both x and y, or neither")
    at = (x, y) if x is not None and y is not None else None
    result = insert(rbd, found, at=at)
    ws.save(model_id)
    return ok(result, f"Inserted **{found.name}**: {result['count']} "
                      "element(s), not yet connected.")
