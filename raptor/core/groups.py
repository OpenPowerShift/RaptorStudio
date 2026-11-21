"""Component groups and tags: saved views over the model.

A group is **not** a hierarchy. A hierarchy is simulation structure: it owns
links, ports and the scope of an isolated run. A group is presentation and
navigation only, may span hierarchies, and overlaps freely with other
groups. Tags are free-form labels on an element; a group is either an
explicit member list, a tag query, or both.

The storage is deliberately one place, not a column on every element row:

```json
"tags": {"block:3": ["pumps", "rotating"]},
"groups": [{"id": "g_pumps", "name": "Pumps", "colour": "#2F80ED",
            "members": ["block:3", "block:4"]}]
```

That keeps the `.rbds` rule "each value is stored once", works for blocks,
nodes, events and hierarchies alike, and leaves the legacy `.rbd` writer
untouched (groups and tags are `.rbds`-only, like annotations).
"""
from __future__ import annotations

import re
from typing import Any
from collections.abc import Iterable

from raptor.core.fileio.typed import row_int

#: Element kinds a group or tag may name. `hier` is a hierarchy oval.
GROUP_KINDS = ("block", "node", "event", "hier")

#: A member reference: `"block:3"`, `"hier:7"`. The same spelling the
#: per-field formula keys use (`raptor.core.fileio.rbds`).
_REF = re.compile(r"^(block|node|event|hier):([1-9]\d*)$")
#: Limits shared by the reader and the GUI editors.
TAG_MAX = 48
NAME_MAX = 64
ID_MAX = 48
_HEX = re.compile(r"^#[0-9A-Fa-f]{6}$")

#: The row column that names an element's parent hierarchy, by kind.
_PARENT_COLUMNS = (("block", "BLOCK_DATA", "ParentId"),
                   ("node", "NODE_DATA", "parent"),
                   ("event", "EVENT_DATA", "ParentId"),
                   ("hier", "HIERARCHY_DATA", "Parent"))

#: Colours a new group cycles through. They are presentation only.
GROUP_COLOURS = ("#2F80ED", "#27AE60", "#9B51E0", "#F2994A",
                 "#EB5757", "#00A3A3", "#B45309", "#6B7280")


# ---------------------------------------------------------------------------
# references and tags
# ---------------------------------------------------------------------------

def element_ref(kind: str, oid: Any) -> str:
    """`("block", 3)` -> `"block:3"`. Raises ValueError for bad input."""
    kind = str(kind).lower()
    if kind not in GROUP_KINDS:
        raise ValueError(f"unknown element kind {kind!r}")
    try:
        ident = int(oid)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"element id {oid!r} is not an integer") from exc
    if ident <= 0:
        raise ValueError("element ids start at 1")
    return f"{kind}:{ident}"


def parse_ref(text: Any) -> tuple[str, int] | None:
    """`"block:3"` -> `("block", 3)`; None when it is not a member ref."""
    match = _REF.match(str(text or "").strip())
    if match is None:
        return None
    return match.group(1), int(match.group(2))


def clean_tag(raw: Any) -> str:
    """One tag: trimmed, printable, at most `TAG_MAX` characters; "" is
    not a tag."""
    if not isinstance(raw, str):
        return ""
    text = "".join(ch for ch in raw.strip() if ch.isprintable())
    text = " ".join(text.split())
    return text[:TAG_MAX]


def clean_tags(raw: Any) -> dict[str, list[str]]:
    """The tags map, with invalid references/tags dropped and values unique.

    Unknown keys are ignored rather than refused: tags are navigation
    metadata, and a hand-edited file with one stale reference should still
    open the model.
    """
    if not isinstance(raw, dict):
        return {}
    out: dict[str, list[str]] = {}
    for ref, tags in raw.items():
        parsed = parse_ref(ref)
        if parsed is None:
            continue
        values: list[str] = []
        for tag in (tags if isinstance(tags, (list, tuple)) else [tags]):
            clean = clean_tag(tag)
            if clean and clean not in values:
                values.append(clean)
        if values:
            out[element_ref(*parsed)] = values
    return out


def tags_for(rbd: Any, ref: str) -> list[str]:
    """The tags on one element, copied and cleaned."""
    raw = (getattr(rbd, "tags", None) or {}).get(ref)
    if not isinstance(raw, (list, tuple)):
        raw = [raw] if raw is not None else []
    return clean_tags({ref: list(raw)}).get(ref, [])


def set_tags(rbd: Any, ref: str, tags: Iterable[str]) -> bool:
    """Replace an element's tags; True when the model actually changed."""
    parsed = parse_ref(ref)
    if parsed is None:
        raise ValueError(f"not an element reference: {ref!r}")
    key = element_ref(*parsed)
    values: list[str] = []
    for tag in tags:
        clean = clean_tag(tag)
        if clean and clean not in values:
            values.append(clean)
    current = getattr(rbd, "tags", None)
    if not isinstance(current, dict):
        current = {}
        rbd.tags = current
    old = current.get(key)
    if values:
        if old == values:
            return False
        current[key] = values
    else:
        if old is None:
            return False
        current.pop(key, None)
    return True


def add_tag(rbd: Any, ref: str, tag: str) -> bool:
    tags = tags_for(rbd, ref)
    return set_tags(rbd, ref, [*tags, tag])


def remove_tag(rbd: Any, ref: str, tag: str) -> bool:
    return set_tags(rbd, ref, [t for t in tags_for(rbd, ref) if t != tag])


def tag_index(rbd: Any) -> dict[str, set[str]]:
    """`tag -> {element refs}`, built from the cleaned tags map."""
    out: dict[str, set[str]] = {}
    for ref, values in clean_tags(getattr(rbd, "tags", None)).items():
        for tag in values:
            out.setdefault(tag, set()).add(ref)
    return out


def all_tags(rbd: Any) -> list[str]:
    """Every tag in use, sorted case-insensitively."""
    return sorted(tag_index(rbd), key=str.casefold)


# ---------------------------------------------------------------------------
# groups
# ---------------------------------------------------------------------------

def _slug(text: str, prefix: str = "g_", limit: int = ID_MAX) -> str:
    slug = re.sub(r"[^a-z0-9]+", "_", text.strip().lower()).strip("_")
    return (prefix + slug)[:limit].rstrip("_") or prefix.rstrip("_")


def clean_colour(raw: Any) -> str:
    """`"#2f80ed"` -> `"#2F80ED"`; "" when it is not a six-digit hex."""
    text = str(raw or "").strip()
    return text.upper() if _HEX.match(text) else ""


def _clean_members(raw: Iterable[Any]) -> list[str]:
    """Valid element refs, canonical and unique, in the caller's order."""
    out: list[str] = []
    for ref in raw:
        parsed = parse_ref(ref)
        if parsed is not None:
            clean = element_ref(*parsed)
            if clean not in out:
                out.append(clean)
    return out


def _clean_text(raw: Any, limit: int | None = None) -> str:
    """Collapse runs of whitespace, optionally truncating."""
    text = " ".join(str(raw or "").split())
    return text[:limit] if limit is not None else text


def _set_or_drop(group: dict[str, Any], key: str, value: Any) -> None:
    """Store a non-empty value, or remove the key when it is empty."""
    if value:
        group[key] = value
    else:
        group.pop(key, None)


def clean_groups(raw: Any) -> list[dict[str, Any]]:  # noqa: C901, PLR0912
    """The group list, sanitised and with stable unique ids."""
    if not isinstance(raw, list):
        return []
    out: list[dict[str, Any]] = []
    seen: set[str] = set()
    for index, item in enumerate(raw):
        if not isinstance(item, dict):
            continue
        name = " ".join(str(item.get("name") or "").split())[:NAME_MAX]
        if not name:
            continue
        raw_id = str(item.get("id") or "").strip().lower()
        if re.fullmatch(rf"[a-z0-9][a-z0-9_-]{{0,{ID_MAX - 1}}}", raw_id):
            gid = raw_id
        else:
            gid = _slug(raw_id or name)
        if gid in seen:
            base, n = gid, 2
            while f"{base}_{n}" in seen:
                n += 1
            gid = f"{base}_{n}"
        seen.add(gid)
        group: dict[str, Any] = {
            "id": gid,
            "name": name,
            "colour": (clean_colour(item.get("colour"))
                       or GROUP_COLOURS[index % len(GROUP_COLOURS)]),
        }
        description = " ".join(str(item.get("description") or "").split())
        if description:
            group["description"] = description
        tag = clean_tag(item.get("tag") or item.get("tag_query"))
        if tag:
            group["tag"] = tag
        members = _clean_members(item.get("members") or ())
        if members:
            group["members"] = members
        out.append(group)
    return out


def groups_of(rbd: Any) -> list[dict[str, Any]]:
    """The model's groups (a cleaned copy; callers never mutate this)."""
    return clean_groups(getattr(rbd, "groups", None))


def _store(rbd: Any, groups: list[dict[str, Any]]) -> None:
    rbd.groups = groups


def group_by_id(rbd: Any, group_id: str) -> dict[str, Any] | None:
    return next((g for g in groups_of(rbd) if g["id"] == group_id), None)


def group_by_name(rbd: Any, name: str) -> dict[str, Any] | None:
    wanted = name.strip().casefold()
    return next((g for g in groups_of(rbd)
                 if g["name"].casefold() == wanted), None)


def group_members(rbd: Any, group: dict[str, Any]) -> set[str]:
    """The element refs in *group*: explicit members plus its tag query."""
    refs = set(group.get("members") or ())
    tag = clean_tag(group.get("tag"))
    if tag:
        refs |= tag_index(rbd).get(tag, set())
    return refs


def add_group(rbd: Any, name: str,
              members: Iterable[str] | None = None, *,
              colour: str | None = None, description: str | None = None,
              tag: str | None = None) -> dict[str, Any]:
    """Create a group and return it. A same-named group is updated.

    Only the fields that are passed are changed on an existing group:
    `add_group(rbd, "Pumps")` leaves its members, colour and description
    alone, while `members=None` on a new group creates an empty group.
    """
    name = _clean_text(name, NAME_MAX)
    if not name:
        raise ValueError("a group needs a name")
    existing = group_by_name(rbd, name)
    if existing is not None:
        update_group(rbd, existing["id"], name=name, members=members,
                     colour=colour, description=description, tag=tag)
        return group_by_id(rbd, existing["id"]) or existing
    groups = groups_of(rbd)
    gid = _slug(name)
    used = {g["id"] for g in groups}
    if gid in used:
        base, n = gid, 2
        while f"{base}_{n}" in used:
            n += 1
        gid = f"{base}_{n}"
    group: dict[str, Any] = {
        "id": gid,
        "name": name,
        "colour": (clean_colour(colour)
                   or GROUP_COLOURS[len(groups) % len(GROUP_COLOURS)]),
    }
    clean_description = " ".join((description or "").split())
    if clean_description:
        group["description"] = clean_description
    clean_tag_value = clean_tag(tag)
    if clean_tag_value:
        group["tag"] = clean_tag_value
    member_list = _clean_members(members or ())
    if member_list:
        group["members"] = member_list
    groups.append(group)
    _store(rbd, groups)
    return group


def update_group(rbd: Any, group_id: str, *, name: str | None = None,
                 members: Iterable[str] | None = None,
                 colour: str | None = None, description: str | None = None,
                 tag: str | None = None) -> dict[str, Any] | None:
    """Patch one group; None leaves a field alone, "" or [] clears it."""
    groups = groups_of(rbd)
    group = next((g for g in groups if g["id"] == group_id), None)
    if group is None:
        return None
    name = _clean_text(name, NAME_MAX)
    if name:
        group["name"] = name
    colour = clean_colour(colour)
    if colour:
        group["colour"] = colour
    if description is not None:
        _set_or_drop(group, "description", _clean_text(description))
    if tag is not None:
        _set_or_drop(group, "tag", clean_tag(tag))
    if members is not None:
        _set_or_drop(group, "members", _clean_members(members))
    _store(rbd, groups)
    return group


def remove_group(rbd: Any, group_id: str) -> bool:
    groups = groups_of(rbd)
    kept = [g for g in groups if g["id"] != group_id]
    if len(kept) == len(groups):
        return False
    _store(rbd, kept)
    return True


def add_to_group(rbd: Any, group_id: str, refs: Iterable[str]) -> bool:
    group = group_by_id(rbd, group_id)
    if group is None:
        return False
    before = set(group.get("members") or ())
    after = before | set(refs)
    if after == before:
        return False
    update_group(rbd, group_id, members=after)
    return True


def remove_from_group(rbd: Any, group_id: str, refs: Iterable[str]) -> bool:
    group = group_by_id(rbd, group_id)
    if group is None:
        return False
    before = set(group.get("members") or ())
    after = before - set(refs)
    if after == before:
        return False
    update_group(rbd, group_id, members=after)
    return True


def prune(rbd: Any, valid_refs: Iterable[str]) -> bool:
    """Drop members/bindings for elements no longer in the model.

    Called after a load and by the editor before it stores its working
    copy, so a stale reference cannot make a group look larger than the
    model. Empty groups are kept: they are still a named place to collect
    elements. Returns True when anything changed.
    """
    valid = set(valid_refs)
    changed = False
    tags = getattr(rbd, "tags", None)
    if isinstance(tags, dict):
        kept = {ref: values for ref, values in tags.items()
                if ref in valid and parse_ref(ref) is not None}
        if kept != tags:
            rbd.tags = kept
            changed = True
    groups = groups_of(rbd)
    before = getattr(rbd, "groups", None)
    for group in groups:
        members = [ref for ref in group.get("members") or ()
                   if ref in valid]
        if members:
            group["members"] = members
        else:
            group.pop("members", None)
    cleaned = clean_groups(groups)
    if cleaned != before:
        _store(rbd, cleaned)
        changed = True
    return changed


# ---------------------------------------------------------------------------
# view choices
# ---------------------------------------------------------------------------

def view_choices(rbd: Any) -> list[tuple[str, str, str]]:
    """`(label, kind, value)` for a view selector.

    `kind` is `"group"` or `"tag"`; `value` is the group id or the tag
    text. Groups come first, then tags that are not already the query of a
    group, so the selector reads as the two ways to filter.
    """
    groups = groups_of(rbd)
    out = [(f"Group: {g['name']}", "group", g["id"]) for g in groups]
    # Tags are case-sensitive, so only the exact query a group already
    # covers is left out ("Pumps" and "pumps" are two different tags).
    used = {clean_tag(g.get("tag")) for g in groups if g.get("tag")}
    for tag in all_tags(rbd):
        if tag not in used:
            out.append((f"Tag: {tag}", "tag", tag))
    return out


def refs_for_view(rbd: Any, kind: str, value: str) -> set[str]:
    """The element refs a group or tag view selects; unknown kinds are empty."""
    if kind == "tag":
        return set(tag_index(rbd).get(value, set()))
    if kind != "group":
        return set()
    group = group_by_id(rbd, value)
    return group_members(rbd, group) if group else set()


def ancestor_hiers(rbd: Any, refs: Iterable[str]) -> set[str]:
    """The hierarchy refs that contain *refs*, including the refs themselves.

    Used by the canvas so a group member inside a hierarchy does not leave
    its enclosing ovals dimmed: the path to the member stays visible. An
    id that is not in the model contributes nothing.
    """
    parents: dict[str, int] = {}
    for kind, section, column in _PARENT_COLUMNS:
        sec = (getattr(rbd, "sections", None) or {}).get(section)
        for row in (sec.rows if sec is not None else ()):
            oid = row_int(row.get("ID"), 0)
            if oid > 0:
                parents[f"{kind}:{oid}"] = row_int(row.get(column), 0)
    out: set[str] = set()
    for ref in refs:
        parsed = parse_ref(ref)
        if parsed is None:
            continue
        kind, oid = parsed
        if kind == "hier":
            out.add(element_ref("hier", oid))
        parent = parents.get(element_ref(*parsed), 0)
        seen: set[int] = set()
        while parent > 0 and parent not in seen:
            seen.add(parent)
            out.add(element_ref("hier", parent))
            parent = parents.get(element_ref("hier", parent), 0)
    return out


def valid_refs_from_model(rbd: Any) -> set[str]:
    """Every element reference that exists in the model rows."""
    out: set[str] = set()
    for kind, section, _parent in _PARENT_COLUMNS:
        sec = (getattr(rbd, "sections", None) or {}).get(section)
        for row in (sec.rows if sec is not None else ()):
            oid = row_int(row.get("ID"), 0)
            if oid > 0:
                out.add(f"{kind}:{oid}")
    return out
