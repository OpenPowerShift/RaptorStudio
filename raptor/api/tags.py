"""Public API for component tags.

A tag is free-form text on an element (block, node, event or hierarchy),
with an optional colour and description for the tag itself. Tags may span
hierarchies and overlap; they never change what the engine runs. The store
is the `.rbds` file's own metadata (`rbd.tags`, `rbd.tag_info`), which the
legacy `.rbd` format cannot carry.
"""
from __future__ import annotations

from collections.abc import Iterable
from typing import Any

from raptor.core import tags as _tags


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
                out.append(_tags.element_ref(kind, ident))
                continue
            except ValueError:
                pass
        out.append(text)
    return out


def _check_refs(rbd: Any, refs: Iterable[str]) -> list[str]:
    """Canonical refs, refusing ones the model does not have."""
    wanted = _normalise(refs)
    valid = _tags.valid_refs_from_model(rbd)
    unknown = [ref for ref in wanted if ref not in valid]
    if unknown:
        raise ValueError(
            "no such element(s): " + ", ".join(unknown[:10])
            + (" …" if len(unknown) > 10 else ""))
    return wanted


def _prune(rbd: Any) -> None:
    """Drop tags on elements the model no longer has, before editing."""
    _tags.prune(rbd, _tags.valid_refs_from_model(rbd))


def _known(rbd: Any, tag: str) -> str:
    clean = _tags.clean_tag(tag)
    if clean not in _tags.all_tags(rbd):
        known = ", ".join(_tags.all_tags(rbd)[:20]) or "none"
        raise ValueError(f"no tag {tag!r} in this model; tags: {known}")
    return clean


def _row(rbd: Any, tag: str, index: dict[str, set[str]]) -> dict[str, Any]:
    return {"tag": tag, "colour": _tags.tag_colour(rbd, tag),
            "description": _tags.tag_description(rbd, tag),
            "elements": len(index.get(tag, ()))}


def list_tags(ws: Any, model_id: str,
              within: int | None = None) -> dict[str, Any]:
    """Every tag with its colour, description and element count.

    With `within` (a hierarchy id) the counts are of elements inside that
    hierarchy at any depth, and tags with none there are left out.
    """
    rbd = _handle(ws, model_id).rbd
    index = _tags.tag_index(rbd)
    if within:
        inside = _tags.inside(rbd, within)
        index = {t: refs & inside for t, refs in index.items()
                 if refs & inside}
        names = sorted(index, key=str.casefold)
    else:
        names = _tags.all_tags(rbd)
    rows = [_row(rbd, tag, index) for tag in names]
    return {"tags": rows, "count": len(rows), "within": within or None}


def describe_tag(ws: Any, model_id: str, tag: str, *,
                 colour: str | None = None, description: str | None = None,
                 create: bool = True) -> dict[str, Any]:
    """Set a tag's colour and/or description ("" clears). With `create` a
    tag no element carries yet is made, as an empty named tag."""
    rbd = _handle(ws, model_id).rbd
    clean = _tags.clean_tag(tag)
    if not clean:
        raise ValueError("a tag needs a name")
    if not create:
        _known(rbd, clean)
    before = _tags.all_tags(rbd)
    _tags.set_tag_info(rbd, clean, colour=colour, description=description)
    if colour is None:          # an explicit "" asked for the automatic one
        _tags.colour_new_tags(rbd, before)
    return _row(rbd, clean, _tags.tag_index(rbd))


def rename_tag(ws: Any, model_id: str, tag: str, new: str) -> dict[str, Any]:
    """Rename a tag everywhere; renaming onto an existing tag merges them."""
    rbd = _handle(ws, model_id).rbd
    _prune(rbd)
    old = _known(rbd, tag)
    target = _tags.clean_tag(new)
    if not target:
        raise ValueError("the new tag name is empty")
    merged = target in _tags.all_tags(rbd) and target != old
    changed = _tags.rename_tag(rbd, old, target)
    return {"from": old, "to": target, "elements": changed, "merged": merged}


def delete_tag(ws: Any, model_id: str, tag: str) -> dict[str, Any]:
    """Remove a tag from every element; the elements are kept."""
    rbd = _handle(ws, model_id).rbd
    _prune(rbd)
    clean = _known(rbd, tag)
    return {"deleted": clean, "elements": _tags.delete_tag(rbd, clean)}


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
    _prune(rbd)
    wanted = _check_refs(rbd, refs)
    clean = _tags._unique_tags(tags)
    changed = []
    known_before = _tags.all_tags(rbd)
    for ref in wanted:
        before = _tags.tags_for(rbd, ref)
        if mode == "replace":
            after = list(clean)
        elif mode == "add":
            after = list(dict.fromkeys([*before, *clean]))
        else:
            after = [t for t in before if t not in set(clean)]
        if after != before:
            _tags.set_tags(rbd, ref, after)
            changed.append({"ref": ref, "tags": after})
    _tags.colour_new_tags(rbd, known_before)
    return {"changed": changed, "count": len(changed), "mode": mode}


def tags_of(ws: Any, model_id: str, ref: str) -> dict[str, Any]:
    """One element's tags, and the model's whole tag list."""
    rbd = _handle(ws, model_id).rbd
    ref = (_normalise([ref]) or [ref])[0]
    if ref not in _tags.valid_refs_from_model(rbd):
        raise ValueError(f"no such element: {ref!r}")
    return {"ref": ref, "tags": _tags.tags_for(rbd, ref),
            "all_tags": _tags.all_tags(rbd)}


def members(ws: Any, model_id: str, tag: str,
            within: int | None = None) -> dict[str, Any]:
    """The elements carrying a tag, optionally only inside a hierarchy."""
    rbd = _handle(ws, model_id).rbd
    clean = _known(rbd, tag)
    refs = sorted(_tags.refs_for_view(rbd, clean, within))
    return {"tag": clean, "within": within or None, "elements": refs,
            "count": len(refs)}
