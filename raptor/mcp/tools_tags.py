"""MCP tools: component tags.

Thin wrappers over `raptor.api.tags`, the same store the Model Explorer
filters by. A tag is a free-form label on an element, with an optional
colour and description; tags may span hierarchies and overlap, and never
change what the engine runs. They live in the `.rbds` file.
"""
from __future__ import annotations

from typing import Literal

from mcp.types import CallToolResult

from raptor.mcp import context
from raptor.mcp.result import DESTRUCTIVE, EDIT, READ_ONLY, guarded, ok

from raptor.mcp.server import mcp


def _save(model_id: str) -> None:
    context.workspace().save(model_id)


@mcp.tool(annotations=READ_ONLY("List component tags"))
@guarded
def list_tags(model_id: str, within: int | None = None) -> CallToolResult:
    """List the model's tags with colour, description and element count.

    A tag is a label on blocks, nodes, events or hierarchies; tags may span
    hierarchies and overlap. `within` (a hierarchy id) counts only the
    elements inside that hierarchy, at any depth, and leaves out tags with
    none there.
    """
    from raptor.api import tags
    data = tags.list_tags(context.workspace(), model_id, within)
    lines = [f"{data['count']} tag(s)"
             + (f" inside hierarchy {within}" if within else "")]
    for row in data["tags"]:
        note = f" — {row['description']}" if row["description"] else ""
        lines.append(f"- `{row['tag']}` ({row['colour']}): "
                     f"{row['elements']} element(s){note}")
    if not data["tags"]:
        lines += ["", "No tags."]
    return ok(data, markdown="\n".join(lines))


@mcp.tool(annotations=EDIT("Tag or untag elements"))
@guarded
def tag_elements(model_id: str, elements: list[str], tags: list[str],
                 mode: Literal["replace", "add", "remove"] = "replace"
                 ) -> CallToolResult:
    """Set, add or remove tags on elements.

    Elements are selectors: `Block:3`, `Node:1`, `Event:2`, `Hier:900`.
    Tags are free-form text (trimmed and deduplicated; case is kept). In
    `replace` mode the list becomes the element's whole tag set, so an
    empty `tags` clears it. An element the model does not have is refused
    rather than skipped. The model is saved.
    """
    context.require_write()         # before the model changes, not at save
    from raptor.api import tags as api
    data = api.set_tags(context.workspace(), model_id, elements, tags,
                        mode=mode)
    _save(model_id)
    change = "; ".join(f"`{c['ref']}`: {', '.join(c['tags']) or '—'}"
                       for c in data["changed"][:10])
    return ok(data, markdown=(
        f"{data['count']} element(s) changed ({mode})"
        + (f": {change}" if change else ".")))


@mcp.tool(annotations=EDIT("Set a tag's colour or description"))
@guarded
def describe_tag(model_id: str, tag: str, colour: str | None = None,
                 description: str | None = None) -> CallToolResult:
    """Set a tag's colour (`#RRGGBB`; "" resets to the default) and/or
    description ("" clears). A tag no element carries yet is created as an
    empty named tag. The model is saved."""
    context.require_write()
    from raptor.api import tags as api
    data = api.describe_tag(context.workspace(), model_id, tag,
                            colour=colour, description=description)
    _save(model_id)
    return ok(data, markdown=(
        f"Tag `{data['tag']}`: {data['colour']}, "
        f"{data['elements']} element(s)."))


@mcp.tool(annotations=EDIT("Rename a tag"))
@guarded
def rename_tag(model_id: str, tag: str, new_name: str) -> CallToolResult:
    """Rename a tag on every element. Renaming onto a tag that already
    exists merges the two. The model is saved."""
    context.require_write()
    from raptor.api import tags as api
    data = api.rename_tag(context.workspace(), model_id, tag, new_name)
    _save(model_id)
    how = " (merged into the existing tag)" if data["merged"] else ""
    return ok(data, markdown=(
        f"Renamed `{data['from']}` to `{data['to']}`{how} on "
        f"{data['elements']} element(s)."))


@mcp.tool(annotations=DESTRUCTIVE("Delete a tag"))
@guarded
def delete_tag(model_id: str, tag: str) -> CallToolResult:
    """Remove a tag from every element and forget its colour and
    description; the elements are kept. The model is saved."""
    context.require_write()
    from raptor.api import tags as api
    data = api.delete_tag(context.workspace(), model_id, tag)
    _save(model_id)
    return ok(data, markdown=(
        f"Deleted tag `{data['deleted']}` from {data['elements']} "
        "element(s)."))
