"""Public API for component groups and tags.

Groups and tags are saved views over a model, not simulation structure: a
group may span hierarchies and a tag is free-form text on one element.
Neither changes what the engine runs. The store is the `.rbds` file's own
metadata (`rbd.groups`, `rbd.tags`), which the legacy `.rbd` format cannot
carry.
"""
from __future__ import annotations

from typing import Any, Iterable

from raptor.core import groups as _groups


def _handle(ws: Any, model_id: str):
    return ws.get(model_id)


def _normalise(refs: Iterable[str] | None) -> list[str]:
    """`Block:3`/`block:3` -> the store's canonical `block:3`."""
    out: list[str] = []
    for raw in refs or ():
        text = str(raw).strip()
        kind, sep, ident = text.partition(":")
        if sep:
            try:
                out.append(_groups.element_ref(kind, ident))
                continue
            except ValueError:
                pass
        out.append(text)
    return out


def _check_refs(rbd: Any, refs: Iterable[str]) -> list[str]:
    """Canonical refs, refusing ones the model does not have."""
    wanted = _normalise(refs)
    valid = _groups.valid_refs_from_model(rbd)
    unknown = [ref for ref in wanted if ref not in valid]
    if unknown:
        raise ValueError(
            "no such element(s): " + ", ".join(unknown[:10])
            + (" …" if len(unknown) > 10 else ""))
    return wanted


def _resolve(rbd: Any, group: str) -> dict[str, Any]:
    """A group by id or by name (case-insensitive), or ValueError."""
    found = (_groups.group_by_id(rbd, group)
             or _groups.group_by_name(rbd, group))
    if found is None:
        known = ", ".join(g["name"] for g in _groups.groups_of(rbd)) or "none"
        raise ValueError(f"no group {group!r} in this model; groups: {known}")
    return found


def list_groups(ws: Any, model_id: str) -> dict[str, Any]:
    """Every group, with its resolved member count and the model's tags."""
    rbd = _handle(ws, model_id).rbd
    rows = []
    for group in _groups.groups_of(rbd):
        members = _groups.group_members(rbd, group)
        rows.append({
            "id": group["id"],
            "name": group["name"],
            "colour": group.get("colour", ""),
            "description": group.get("description", ""),
            "tag": group.get("tag", ""),
            "explicit_members": len(group.get("members") or ()),
            "members": len(members),
        })
    return {"groups": rows, "count": len(rows),
            "tags": _groups.all_tags(rbd)}


def create_group(ws: Any, model_id: str, name: str,
                 members: Iterable[str] | None = None, *,
                 tag: str | None = None, colour: str | None = None,
                 description: str | None = None) -> dict[str, Any]:
    """Create a group (or update the same-named one) and return it.

    `members` are selectors (`Block:3`, `Node:1`, `Event:2`,
    `Hier:900`); `tag` includes every element carrying that tag, including
    elements tagged later.
    """
    rbd = _handle(ws, model_id).rbd
    group = _groups.add_group(
        rbd, name, None if members is None else _check_refs(rbd, members),
        colour=colour,
                              description=description, tag=tag)
    return {"group": group, "members": len(_groups.group_members(rbd, group))}


def update_group(ws: Any, model_id: str, group: str, *,
                 name: str | None = None, colour: str | None = None,
                 description: str | None = None, tag: str | None = None,
                 members: Iterable[str] | None = None,
                 add: Iterable[str] | None = None,
                 remove: Iterable[str] | None = None) -> dict[str, Any]:
    """Patch one group; `members` replaces, `add`/`remove` edit the list.

    `add`/`remove` are applied with the set operations the editor uses, so
    an element in two lists is added once and removing a non-member is a
    no-op rather than an error.
    """
    rbd = _handle(ws, model_id).rbd
    found = _resolve(rbd, group)
    if add:
        _groups.add_to_group(rbd, found["id"], _check_refs(rbd, add))
    if remove:
        _groups.remove_from_group(rbd, found["id"], _check_refs(rbd, remove))
    if members is not None or any(v is not None for v in
                                  (name, colour, description, tag)):
        updated = _groups.update_group(
            rbd, found["id"], name=name,
            members=None if members is None else _check_refs(rbd, members),
            colour=colour,
            description=description, tag=tag)
        if updated is None:
            raise ValueError(f"group {found['id']!r} disappeared")
        found = updated
    found = _groups.group_by_id(rbd, found["id"]) or found
    return {"group": found,
            "members": len(_groups.group_members(rbd, found))}


def delete_group(ws: Any, model_id: str, group: str) -> dict[str, Any]:
    """Delete a group; the elements and their tags are kept."""
    rbd = _handle(ws, model_id).rbd
    found = _resolve(rbd, group)
    removed = _groups.remove_group(rbd, found["id"])
    if not removed:
        raise ValueError(f"group {found['id']!r} disappeared")
    return {"deleted": found["id"], "name": found["name"]}


def set_tags(ws: Any, model_id: str, refs: Iterable[str],
             tags: Iterable[str], *, mode: str = "replace") -> dict[str, Any]:
    """Tag elements: `replace`, `add` or `remove`.

    Tags are trimmed and deduplicated; case is kept, because a tag is the
    user's own text. An empty list in `replace` mode clears the element's
    tags. A ref the model does not have is refused, not silently dropped.
    """
    if mode not in ("replace", "add", "remove"):
        raise ValueError(f"mode must be replace, add or remove; got {mode!r}")
    rbd = _handle(ws, model_id).rbd
    wanted = _check_refs(rbd, refs)
    clean = []
    for tag in tags:
        cleaned = _groups.clean_tag(tag)
        if cleaned and cleaned not in clean:
            clean.append(cleaned)
    changed = []
    for ref in wanted:
        before = _groups.tags_for(rbd, ref)
        if mode == "replace":
            after = list(clean)
        elif mode == "add":
            after = list(dict.fromkeys([*before, *clean]))
        else:
            after = [t for t in before if t not in set(clean)]
        if after != before:
            _groups.set_tags(rbd, ref, after)
            changed.append({"ref": ref, "tags": after})
    return {"changed": changed, "count": len(changed), "mode": mode}


def tags_of(ws: Any, model_id: str, ref: str) -> dict[str, Any]:
    """One element's tags, and the model's whole tag list."""
    rbd = _handle(ws, model_id).rbd
    if ref not in _groups.valid_refs_from_model(rbd):
        raise ValueError(f"no such element: {ref!r}")
    return {"ref": ref, "tags": _groups.tags_for(rbd, ref),
            "all_tags": _groups.all_tags(rbd)}
