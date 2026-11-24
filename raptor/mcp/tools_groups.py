"""MCP tools: component groups and tags.

Thin wrappers over `raptor.api.groups`, the same store the Model Explorer
filters by. Groups and tags are saved views, not simulation structure: they
never change what the engine runs. Both live in the `.rbds` file.
"""
from __future__ import annotations

from typing import Any, Literal

from mcp.types import CallToolResult

from raptor.mcp import context
from raptor.mcp.result import (DESTRUCTIVE, EDIT, READ_ONLY, guarded,
                               ok)

from raptor.mcp.server import mcp


def _save(model_id: str) -> None:
    context.require_write()
    context.workspace().save(model_id)


def _rows(groups: list[dict[str, Any]]) -> list[str]:
    out = []
    for row in groups:
        tag = f", tag `{row['tag']}`" if row["tag"] else ""
        out.append(f"- **{row['name']}** (`{row['id']}`, {row['colour']}) — "
                   f"{row['members']} member(s){tag}")
    return out


@mcp.tool(annotations=READ_ONLY("List component groups and tags"))
@guarded
def list_groups(model_id: str) -> CallToolResult:
    """List the model's groups and tags.

    A group is a named, coloured saved view over the model. It may hold an
    explicit member list, every element carrying a tag, or both; it can
    span hierarchies and never changes the simulation. `members` is the
    resolved count (explicit members plus the tag query).
    """
    from raptor.api import groups
    data = groups.list_groups(context.workspace(), model_id)
    lines = [f"{data['count']} group(s)"]
    lines += _rows(data["groups"]) if data["groups"] else ["", "No groups."]
    if data["tags"]:
        lines += ["", "Tags: " + ", ".join(f"`{t}`" for t in data["tags"])]
    return ok(data, markdown="\n".join(lines))


@mcp.tool(annotations=EDIT("Create or update a component group"))
@guarded
def create_group(model_id: str, name: str,
                 members: list[str] | None = None,
                 tag: str | None = None, colour: str | None = None,
                 description: str | None = None) -> CallToolResult:
    """Create a group, or update the group that already has this name.

    `members` are element selectors: `Block:3`, `Node:1`, `Event:2`,
    `Hier:900`. `tag` includes every element carrying that tag, including
    ones tagged later. `colour` is `#RRGGBB`; one is chosen when omitted.
    The model is saved, so the group survives the call.
    """
    context.require_write()         # before the model changes, not at save
    from raptor.api import groups
    ws = context.workspace()
    data = groups.create_group(ws, model_id, name, members, tag=tag,
                               colour=colour, description=description)
    _save(model_id)
    group = data["group"]
    return ok(data, markdown=(
        f"Group **{group['name']}** (`{group['id']}`) with "
        f"{data['members']} member(s)."))


@mcp.tool(annotations=EDIT("Edit a component group"))
@guarded
def update_group(model_id: str, group: str,
                 name: str | None = None, colour: str | None = None,
                 description: str | None = None, tag: str | None = None,
                 members: list[str] | None = None,
                 add: list[str] | None = None,
                 remove: list[str] | None = None) -> CallToolResult:
    """Patch one group (by name or id).

    `members` replaces the explicit list; `add`/`remove` edit it without
    rewriting the rest. `tag=""` clears the tag query; `description=""`
    clears the note. The model is saved.
    """
    context.require_write()         # before the model changes, not at save
    from raptor.api import groups
    ws = context.workspace()
    data = groups.update_group(ws, model_id, group, name=name, colour=colour,
                               description=description, tag=tag,
                               members=members, add=add, remove=remove)
    _save(model_id)
    return ok(data, markdown=(
        f"Group **{data['group']['name']}** now has "
        f"{data['members']} member(s)."))


@mcp.tool(annotations=DESTRUCTIVE("Delete a component group"))
@guarded
def delete_group(model_id: str, group: str) -> CallToolResult:
    """Delete a group; its elements and their tags are kept."""
    context.require_write()         # before the model changes, not at save
    from raptor.api import groups
    ws = context.workspace()
    data = groups.delete_group(ws, model_id, group)
    _save(model_id)
    return ok(data, markdown=f"Deleted group **{data['name']}**.")


@mcp.tool(annotations=EDIT("Tag or untag elements"))
@guarded
def tag_elements(model_id: str, elements: list[str], tags: list[str],
                 mode: Literal["replace", "add", "remove"] = "replace"
                 ) -> CallToolResult:
    """Set, add or remove tags on elements.

    Tags are free-form text (trimmed and deduplicated; case is kept). In
    `replace` mode the list becomes the element's whole tag set, so an
    empty `tags` clears it. An element the model does not have is refused
    rather than skipped.
    """
    context.require_write()         # before the model changes, not at save
    from raptor.api import groups
    ws = context.workspace()
    data = groups.set_tags(ws, model_id, elements, tags, mode=mode)
    _save(model_id)
    change = "; ".join(f"`{c['ref']}`: {', '.join(c['tags']) or '—'}"
                       for c in data["changed"][:10])
    return ok(data, markdown=(
        f"{data['count']} element(s) changed ({mode})"
        + (f": {change}" if change else ".")))
