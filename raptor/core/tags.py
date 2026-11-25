"""Component tags: free-form labels on elements, for navigation and results.

A tag is **not** a hierarchy. A hierarchy is simulation structure: it owns
links, ports and the scope of an isolated run, and its contents never
overlap another hierarchy's. A tag is a label an element carries; tags may
span hierarchies and overlap freely. "Tag within a hierarchy" is the two
combined at view time (`refs_for_view(..., within=...)`), never stored.

The storage is one place, not a column on every element row:

```json
"tags": {"block:3": ["pumps", "rotating"]},
"tag_info": {"pumps": {"colour": "#2F80ED", "description": "Feed pumps"}}
```

`tag_info` is optional per tag. A tag with no stored colour takes one from
its name (`tag_colour`), so the file only stores a colour the user chose --
the `.rbds` rule that an inferable value is never stored. An entry with no
elements is kept: it is a named, described tag waiting to be applied.
Both maps are `.rbds`-only; legacy `.rbd` has no representation.
"""
from __future__ import annotations

import re
import zlib
from types import SimpleNamespace
from typing import Any
from collections.abc import Iterable

from raptor.core.fileio.typed import row_int

#: Element kinds a tag may name. `hier` is a hierarchy oval.
TAG_KINDS = ("block", "node", "event", "hier")

#: A member reference: `"block:3"`, `"hier:7"`. The same spelling the
#: per-field formula keys use (`raptor.core.fileio.rbds`).
_REF = re.compile(r"^(block|node|event|hier):([1-9]\d*)$")
#: Limits shared by the reader and the GUI editors.
TAG_MAX = 48
DESCRIPTION_MAX = 400
_HEX = re.compile(r"^#[0-9A-Fa-f]{6}$")

#: The row column that names an element's parent hierarchy, by kind.
_PARENT_COLUMNS = (("block", "BLOCK_DATA", "ParentId"),
                   ("node", "NODE_DATA", "parent"),
                   ("event", "EVENT_DATA", "ParentId"),
                   ("hier", "HIERARCHY_DATA", "Parent"))

#: Colours a tag without a chosen colour takes, by a hash of its name, so
#: the same tag is the same colour in every model and every session.
TAG_COLOURS = ("#2F80ED", "#27AE60", "#9B51E0", "#F2994A",
               "#EB5757", "#00A3A3", "#B45309", "#6B7280")


# ---------------------------------------------------------------------------
# references
# ---------------------------------------------------------------------------

def element_ref(kind: str, oid: Any) -> str:
    """`("block", 3)` -> `"block:3"`. Raises ValueError for bad input."""
    kind = str(kind).lower()
    if kind not in TAG_KINDS:
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


# ---------------------------------------------------------------------------
# tags on elements
# ---------------------------------------------------------------------------

def clean_tag(raw: Any) -> str:
    """One tag: trimmed, printable, at most `TAG_MAX` characters; "" is
    not a tag."""
    if not isinstance(raw, str):
        return ""
    text = "".join(ch for ch in raw.strip() if ch.isprintable())
    text = " ".join(text.split())
    return text[:TAG_MAX]


def _unique_tags(values: Iterable[Any]) -> list[str]:
    out: list[str] = []
    for tag in values:
        clean = clean_tag(tag)
        if clean and clean not in out:
            out.append(clean)
    return out


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
        values = _unique_tags(tags if isinstance(tags, (list, tuple))
                              else [tags])
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
    values = _unique_tags(tags)
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
    return set_tags(rbd, ref, [*tags_for(rbd, ref), tag])


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
    """Every tag in use or described, sorted case-insensitively."""
    names = set(tag_index(rbd)) | set(clean_tag_info(
        getattr(rbd, "tag_info", None)))
    return sorted(names, key=str.casefold)


# ---------------------------------------------------------------------------
# tag colour and description
# ---------------------------------------------------------------------------

def clean_colour(raw: Any) -> str:
    """`"#2f80ed"` -> `"#2F80ED"`; "" when it is not a six-digit hex."""
    text = str(raw or "").strip()
    return text.upper() if _HEX.match(text) else ""


def default_colour(tag: str) -> str:
    """The colour a tag takes when none was chosen: stable for the name."""
    return TAG_COLOURS[zlib.crc32(tag.encode("utf-8")) % len(TAG_COLOURS)]


def clean_tag_info(raw: Any) -> dict[str, dict[str, str]]:
    """`tag -> {"colour"?, "description"?}`, with empty entries kept.

    An entry with neither field is still a tag the user created on purpose
    (an empty, named tag), so it is kept. A colour equal to the name's
    default is dropped: it is inferable.
    """
    if not isinstance(raw, dict):
        return {}
    out: dict[str, dict[str, str]] = {}
    for key, value in raw.items():
        tag = clean_tag(key)
        if not tag:
            continue
        entry: dict[str, str] = {}
        if isinstance(value, dict):
            colour = clean_colour(value.get("colour"))
            if colour and colour != default_colour(tag):
                entry["colour"] = colour
            description = " ".join(
                str(value.get("description") or "").split())[:DESCRIPTION_MAX]
            if description:
                entry["description"] = description
        out[tag] = entry
    return out


def tag_colour(rbd: Any, tag: str) -> str:
    entry = clean_tag_info(getattr(rbd, "tag_info", None)).get(tag) or {}
    return entry.get("colour") or default_colour(tag)


def tag_description(rbd: Any, tag: str) -> str:
    entry = clean_tag_info(getattr(rbd, "tag_info", None)).get(tag) or {}
    return entry.get("description", "")


def set_tag_info(rbd: Any, tag: str, *, colour: str | None = None,
                 description: str | None = None) -> bool:
    """Create or patch a tag's entry; None leaves a field alone, "" clears
    it. True when the model changed."""
    tag = clean_tag(tag)
    if not tag:
        raise ValueError("a tag needs a name")
    info = clean_tag_info(getattr(rbd, "tag_info", None))
    before = dict(info.get(tag, {})) if tag in info else None
    entry = dict(info.get(tag, {}))
    if colour is not None:
        clean = clean_colour(colour)
        if colour and not clean:
            raise ValueError(f"colour must be #RRGGBB, not {colour!r}")
        if clean and clean != default_colour(tag):
            entry["colour"] = clean
        else:
            entry.pop("colour", None)
    if description is not None:
        text = " ".join(description.split())[:DESCRIPTION_MAX]
        if text:
            entry["description"] = text
        else:
            entry.pop("description", None)
    info[tag] = entry
    rbd.tag_info = info
    return before != entry


def rename_tag(rbd: Any, old: str, new: str) -> int:
    """Rename a tag on every element (merging into *new* if it exists).

    Returns the number of elements changed. The colour and description move
    with the tag unless *new* already has its own.
    """
    old, new = clean_tag(old), clean_tag(new)
    if not old or not new:
        raise ValueError("a tag needs a name")
    if old not in all_tags(rbd):
        raise ValueError(f"no tag {old!r} in this model")
    if old == new:
        return 0
    changed = 0
    for ref in sorted(tag_index(rbd).get(old, set())):
        tags = [new if t == old else t for t in tags_for(rbd, ref)]
        if set_tags(rbd, ref, tags):
            changed += 1
    info = clean_tag_info(getattr(rbd, "tag_info", None))
    moved = info.pop(old, None)
    if moved is not None and not info.get(new):
        info[new] = moved
    rbd.tag_info = info
    return changed


def delete_tag(rbd: Any, tag: str) -> int:
    """Remove a tag from every element and forget its colour/description.

    Returns the number of elements that carried it."""
    tag = clean_tag(tag)
    if tag not in all_tags(rbd):
        raise ValueError(f"no tag {tag!r} in this model")
    refs = sorted(tag_index(rbd).get(tag, set()))
    for ref in refs:
        remove_tag(rbd, ref, tag)
    info = clean_tag_info(getattr(rbd, "tag_info", None))
    info.pop(tag, None)
    rbd.tag_info = info
    return len(refs)


def prune(rbd: Any, valid_refs: Iterable[str]) -> bool:
    """Drop tags on elements no longer in the model.

    Called after a load and before an editor stores its working copy, so a
    stale reference cannot make a tag look larger than the model. Tag
    colours and descriptions are kept. Returns True when anything changed.
    """
    valid = set(valid_refs)
    tags = getattr(rbd, "tags", None)
    if not isinstance(tags, dict):
        return False
    kept = {ref: values for ref, values in tags.items()
            if ref in valid and parse_ref(ref) is not None}
    if kept != tags:
        rbd.tags = kept
        return True
    return False


# ---------------------------------------------------------------------------
# model structure
# ---------------------------------------------------------------------------

def _parents(rbd: Any) -> dict[str, int]:
    """`ref -> parent hierarchy id` (0 at the top level)."""
    out: dict[str, int] = {}
    for kind, section, column in _PARENT_COLUMNS:
        sec = (getattr(rbd, "sections", None) or {}).get(section)
        for row in (sec.rows if sec is not None else ()):
            oid = row_int(row.get("ID"), 0)
            if oid > 0:
                out[f"{kind}:{oid}"] = row_int(row.get(column), 0)
    return out


#: The row column that holds an element's name, by kind.
_NAME_COLUMNS = {"block": ("BLOCK_DATA", "blockName"),
                 "node": ("NODE_DATA", "Name"),
                 "event": ("EVENT_DATA", "eventName"),
                 "hier": ("HIERARCHY_DATA", "hierarchyName")}


def names_of(rbd: Any, refs: Iterable[str]) -> set[str]:
    """The element names of *refs* (what the results tables show)."""
    wanted: dict[str, set[int]] = {}
    for ref in refs:
        parsed = parse_ref(ref)
        if parsed is not None:
            wanted.setdefault(parsed[0], set()).add(parsed[1])
    out: set[str] = set()
    for kind, ids in wanted.items():
        section, column = _NAME_COLUMNS[kind]
        sec = (getattr(rbd, "sections", None) or {}).get(section)
        for row in (sec.rows if sec is not None else ()):
            if row_int(row.get("ID"), 0) in ids:
                name = str(row.get(column) or "").strip()
                if name:
                    out.add(name)
    return out


def valid_refs_from_model(rbd: Any) -> set[str]:
    """Every element reference that exists in the model rows."""
    return set(_parents(rbd))


def ancestor_hiers(rbd: Any, refs: Iterable[str]) -> set[str]:
    """The hierarchy refs that contain *refs*, including the refs themselves.

    Used by the canvas so a tagged element inside a hierarchy does not
    leave its enclosing ovals dimmed: the path to it stays visible. An id
    that is not in the model contributes nothing.
    """
    parents = _parents(rbd)
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


def inside(rbd: Any, hier_id: int) -> set[str]:
    """Every element ref inside hierarchy *hier_id*, at any depth (the
    hierarchy itself is not included)."""
    parents = _parents(rbd)
    children: dict[int, list[str]] = {}
    for ref, parent in parents.items():
        children.setdefault(parent, []).append(ref)
    out: set[str] = set()
    stack = [int(hier_id)]
    seen: set[int] = set()
    while stack:
        hid = stack.pop()
        if hid in seen:
            continue
        seen.add(hid)
        for ref in children.get(hid, ()):
            out.add(ref)
            if ref.startswith("hier:"):
                stack.append(int(ref[5:]))
    return out


def expand(rbd: Any, refs: Iterable[str]) -> set[str]:
    """*refs* plus, for each tagged hierarchy, everything inside it.

    A tag on a hierarchy oval means "this sub-system": its results count
    every element inside, not the oval alone.
    """
    out: set[str] = set()
    for ref in refs:
        out.add(ref)
        parsed = parse_ref(ref)
        if parsed is not None and parsed[0] == "hier":
            out |= inside(rbd, parsed[1])
    return out


def refs_for_view(rbd: Any, tag: str, within: int | None = None) -> set[str]:
    """The element refs a tag selects, optionally only inside hierarchy
    *within* (at any depth). Unknown tags select nothing."""
    refs = set(tag_index(rbd).get(tag, set()))
    if within:
        refs &= inside(rbd, within)
    return refs


# ---------------------------------------------------------------------------
# copy and paste
# ---------------------------------------------------------------------------

#: Copy-buffer item kinds that carry tags (see `clipboard_plan.Buffer`).
#: Rows are text-to-text, like file rows: `{"ref", "tags"}` (a JSON list)
#: and `{"tag", "colour", "description"}`.
BUFFER_KINDS = ("tags", "taginfo")
_BUFFER_ELEMENTS = {"block", "node", "event", "hier"}


def buffer_items(rbd: Any, buffer: Iterable[tuple[str, dict]]
                 ) -> list[tuple[str, dict]]:
    """The tags of the elements in a copy buffer, as buffer items."""
    import json
    out: list[tuple[str, dict]] = []
    used: set[str] = set()
    for kind, row in buffer:
        if kind not in _BUFFER_ELEMENTS:
            continue
        ref = f"{kind}:{row_int(row.get('ID'), 0)}"
        values = tags_for(rbd, ref)
        if values:
            out.append(("tags", {"ref": ref, "tags": json.dumps(values)}))
            used.update(values)
    info = clean_tag_info(getattr(rbd, "tag_info", None))
    for tag in sorted(used, key=str.casefold):
        entry = info.get(tag) or {}
        if entry:
            out.append(("taginfo", {"tag": tag,
                                    "colour": entry.get("colour", ""),
                                    "description":
                                        entry.get("description", "")}))
    return out


def pasted(rbd: Any, buffer: Iterable[tuple[str, dict]],
           id_maps: dict[str, dict[int, int]]
           ) -> tuple[dict[str, list[str]], dict[str, dict[str, str]]] | None:
    """`(tags, tag_info)` once a buffer's tags are on the pasted copies.

    Each copied element's tags go on its copy (`id_maps`: old id -> new id,
    per kind). A pasted tag's colour and description are added only when
    the target model has none for that tag: a paste never recolours the
    target's own tags. None when the buffer carries no tags.
    """
    import json
    tags = clean_tags(getattr(rbd, "tags", None))
    info = clean_tag_info(getattr(rbd, "tag_info", None))
    target = SimpleNamespace(tags=tags, tag_info=info)
    changed = False
    for kind, row in buffer:
        if kind == "tags":
            parsed = parse_ref(row.get("ref"))
            if parsed is None:
                continue
            new = id_maps.get(parsed[0], {}).get(parsed[1])
            if new is None:
                continue
            try:
                values = json.loads(str(row.get("tags") or "[]"))
            except ValueError:
                continue
            if not isinstance(values, list):
                continue
            ref = element_ref(parsed[0], new)
            changed |= set_tags(target, ref, [*tags_for(target, ref), *values])
        elif kind == "taginfo":
            tag = clean_tag(row.get("tag"))
            if tag and not info.get(tag):
                set_tag_info(target, tag,
                             colour=clean_colour(row.get("colour")),
                             description=str(row.get("description") or ""))
                changed = True
    if not changed:
        return None
    return clean_tags(target.tags), clean_tag_info(target.tag_info)


def carried(source: Any, target: Any, id_maps: dict[str, dict[int, int]]
            ) -> tuple[dict[str, list[str]], dict[str, dict[str, str]]] | None:
    """`(tags, tag_info)` for *target* once *source*'s tags are carried over
    to its elements' new ids (an imported model). None when there are none.
    """
    rows = [(parsed[0], {"ID": str(parsed[1])})
            for ref in clean_tags(getattr(source, "tags", None))
            if (parsed := parse_ref(ref)) is not None]
    return pasted(target, buffer_items(source, rows), id_maps)


def keep_only_present(rbd: Any) -> None:
    """Drop tags on elements *rbd* no longer has, and the colour and
    description of tags nothing carries any more (an extracted part)."""
    prune(rbd, valid_refs_from_model(rbd))
    used = set(tag_index(rbd))
    info = clean_tag_info(getattr(rbd, "tag_info", None))
    rbd.tag_info = {t: v for t, v in info.items() if t in used}
