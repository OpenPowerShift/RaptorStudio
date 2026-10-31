"""Annotation (drawing-overlay) API.

Annotations are the text boxes, rectangles, ellipses and arrows drawn on
top of the diagram.  They exist only in ``.rbds`` (the legacy ``.rbd``
format has no representation and drops them).  The full parameter set is
documented in ``docs/RBDS_FORMAT.md``.

This module is the missing write path: before it, annotations could only
be created in the GUI.  It offers two placement modes:

* **By name** (``adjacent_to="Srv_3_Disk", location="right"``) -- no
  coordinates, stable across ``auto_layout`` re-runs.  For arrows,
  ``from_``/``to`` anchor the tail and head to two elements.  The target
  may be another annotation (``Annotation:7``, or its label when unique):
  placement then uses that annotation's bounding box.
* **By coordinates** (``x``/``y``/``w``/``h`` or ``x1``/``y1``/``x2``/
  ``y2``) -- in the same *scene* space the GUI writes (see below).

Coordinate contract
-------------------
Elements are stored in *model* coordinates and mapped to the canvas by a
frozen transform persisted as ``RbdFile.view_transform``
(``{scale, origin_x, flip_base, dx_min, dy_min}``); the GUI's ``_tx``/
``_ty`` are ``(x - origin_x) * scale`` and ``(flip_base - y) * scale``.
Annotations are stored in that resulting *scene* space (the GUI writes
``item.scenePos()``).  Writing model coordinates straight into an
annotation is what makes it land in the corner.  :func:`place_annotation`
does the mapping, reusing a frozen transform when present and otherwise
deriving the same one the GUI derives on load.
"""
from __future__ import annotations

import math
from typing import Any

from raptor.api.build import (
    BuildError,
    ElementNotFound,
    resolve_element,
)
from raptor.api.workspace import Workspace
from raptor.core.annotations import (  # noqa: E402,F401
    ANNO_DEFAULT_LOCATION,
    ANNO_DEFAULTS,
    ANNO_LOCATIONS,
    ANNO_Z_DEFAULT,
    ANNO_Z_MIDDLE,
    annotation_geometry,
    annotation_label,
)
from raptor.core.fileio import view_transform

#: Annotation kinds (mirrors ``docs/RBDS_FORMAT.md``).
ANNO_KINDS = ("text", "rect", "ellipse", "arrow")

# `ANNO_DEFAULTS` and `ANNO_Z_MIDDLE` are imported at the top of this module
# from `raptor.core.annotations` (review E-07). Both used to be spelled out
# here as well, under the comment "kept here rather than imported so the API
# layer never needs Qt" -- a sound reason and the wrong mechanism, since
# `core/` needs no Qt.
#
# Its cross-reference was stale by the time anyone could act on it: it named
# `gui/ui/diagram.py`, but the GUI copy had moved to `diagram_annotations.py`.
# That is the specific hazard of duplicating a constant *together with* a
# cross-reference -- the reference rots independently of the value, and
# nothing notices while the two still agree.

_ANNO_STYLE_KEYS = frozenset(ANNO_DEFAULTS) | {"z"}
_LINE_STYLES = ("solid", "dash", "dot", "dash-dot", "dash-dot-dot")

#: On-canvas element sizes in *scene* pixels.  The GUI draws every block at
#: a constant ``BLOCK_W x BLOCK_H`` regardless of the model transform, and a
#: node as a circle of radius ``NODE_R``; the event hexagon and hierarchy
#: oval sizes are likewise constants.  Duplicated from
#: ``raptor/gui/ui/diagram.py`` (``BLOCK_W``/``BLOCK_H``/``NODE_R`` and
#: ``_HierItem.W/H``) for the same no-Qt reason.
_BLOCK_HALF = (view_transform.BLOCK_W / 2, view_transform.BLOCK_H / 2)
_NODE_HALF = (float(view_transform.NODE_R),) * 2
_EVENT_HALF = (view_transform.NODE_R + 2.0,) * 2
_HIER_HALF = (75.0, 28.0)       # 150 x 56


class AnnotationNotFound(BuildError):
    """Referenced annotation id does not exist."""


# ---------------------------------------------------------------------------
# Coordinate mapping (shared with the GUI: core.fileio.view_transform)
# ---------------------------------------------------------------------------

def derive_view_transform(rbd) -> dict[str, float]:
    """The transform the GUI would derive for this model on first load."""
    return view_transform.derive(rbd).as_dict()


def ensure_view_transform(rbd) -> dict[str, float]:
    """Return the model's frozen transform, deriving and freezing one if
    the model has none (matching what the GUI would do on open)."""
    return view_transform.ensure(rbd).as_dict()


def _model_to_scene(rbd, x: float, y: float) -> tuple[float, float]:
    """Model coordinates to the scene space annotations are stored in.

    The arithmetic lives on :class:`~raptor.core.fileio.view_transform.
    ViewTransform`, which the canvas also uses, so the API and the GUI
    cannot disagree about the origin, flip or scale.
    """
    return view_transform.ensure(rbd).to_scene(x, y)


# ---------------------------------------------------------------------------
# Anchors
# ---------------------------------------------------------------------------

def _annotation_in(rbd, ref: str | int) -> dict[str, Any] | None:
    """The annotation an anchor selector names, if it names one.

    Accepts ``Annotation:7`` and a label (the annotation's text, or
    ``Annotation 7`` for an empty one). A label that matches more than one
    annotation is ambiguous and rejected; ids are never ambiguous.
    """
    entries = list(getattr(rbd, "annotations", None) or [])
    text = str(ref).strip()
    prefix, _, ident = text.partition(":")
    if prefix.strip().lower() == "annotation":
        if not ident.strip().isdigit():
            raise BuildError(
                f"{text!r} is not an annotation id; use Annotation:<id>")
        for entry in entries:
            if int(entry.get("id", 0) or 0) == int(ident):
                return entry
        raise ElementNotFound(f"No annotation with ID {ident}")
    matches = [e for e in entries if annotation_label(e) == text]
    if len(matches) > 1:
        raise BuildError(
            f"annotation label {text!r} is ambiguous; use Annotation:<id>")
    return matches[0] if matches else None


def _annotation_geometry_for(rbd, ref) -> dict[str, Any] | None:
    entry = _annotation_in(rbd, ref)
    return annotation_geometry(entry) if entry is not None else None


def _annotation_refs(entry: dict[str, Any]) -> list[str]:
    """Annotation references an entry's anchor holds (box target, or an
    arrow's two ends)."""
    anchor = entry.get("anchor")
    if not isinstance(anchor, dict):
        return []
    refs = [anchor.get("ref") or anchor.get("target")]
    for end in ("from", "to"):
        if anchor.get(end) is not None:
            refs.append(anchor.get(f"{end}_ref") or anchor.get(end))
    return [str(r) for r in refs if r]


def _would_cycle(rbd, target_entry: dict[str, Any],
                 new_id: int) -> bool:
    """True if anchoring at `target_entry` would loop back to `new_id`.

    Anchors to annotations can chain (and an arrow can join two of them),
    and a cycle would make following a layout chase its own tail: the
    placement is bounded, so it would not hang, but it could never settle.
    Ids break ties: labels need not be unique, so the stored ``ref`` is
    preferred.
    """
    seen: set[int] = set()
    stack: list[dict[str, Any]] = [target_entry]
    while stack:
        current = stack.pop()
        cid = int(current.get("id", 0) or 0)
        if cid == int(new_id):
            return True
        if cid in seen:                 # a pre-existing cycle, not ours
            continue
        seen.add(cid)
        for ref in _annotation_refs(current):
            prefix, _, ident = ref.partition(":")
            if prefix.strip().lower() == "annotation" \
                    and ident.strip().isdigit() \
                    and int(ident) == int(new_id):
                return True
            try:
                found = _annotation_in(rbd, ref)
            except BuildError:
                continue                # the target is gone; no cycle
            if found is not None:
                stack.append(found)
    return False


def _anchor_for(rbd, ref: str | int) -> dict[str, Any]:
    """Resolve an element name/id or an annotation to its scene centre,
    half-extents and hierarchy level."""
    text = str(ref).strip()
    explicit_annotation = text.partition(":")[0].strip().lower() == "annotation"
    if explicit_annotation:
        geometry = _annotation_geometry_for(rbd, ref)
        if geometry is None:
            raise BuildError(f"annotation {ref!r} has no usable geometry")
        return geometry
    try:
        section, row = resolve_element(rbd, ref)
    except ElementNotFound:
        # Plain names resolve to elements first (a block called "B1" wins
        # over an annotation whose text happens to be "B1"); only when no
        # element matches is the name treated as an annotation label.
        annotation = _annotation_in(rbd, ref)
        if annotation is None:
            raise
        geometry = annotation_geometry(annotation)
        if geometry is None:        # pragma: no cover - entries are numeric
            raise BuildError(
                f"annotation {ref!r} has no usable geometry") from None
        return geometry
    try:
        x = float(row.get("xPos", 0) or 0)
        y = float(row.get("yPos", 0) or 0)
    except (TypeError, ValueError):
        x = y = 0.0
    if section == "BLOCK_DATA":
        kind, half, pkey = "Block", _BLOCK_HALF, "ParentId"
    elif section == "NODE_DATA":
        kind, half, pkey = "Node", _NODE_HALF, "parent"
    elif section == "EVENT_DATA":
        kind, half, pkey = "Event", _EVENT_HALF, "ParentId"
    elif section == "HIERARCHY_DATA":
        kind, half, pkey = "Hierarchy", _HIER_HALF, "Parent"
    else:  # pragma: no cover - resolve_element only returns these sections
        raise BuildError(f"cannot anchor an annotation to {ref!r}")
    try:
        parent = int(float(row.get(pkey, 0) or 0))
    except (TypeError, ValueError):
        parent = 0
    cx, cy = _model_to_scene(rbd, x, y)
    return {
        "kind": kind, "id": int(float(row["ID"])),
        "name": row.get("blockName") or row.get("Name")
        or row.get("eventName") or row.get("hierarchyName")
        or f"{kind} {row.get('ID')}",
        "parent": parent, "cx": cx, "cy": cy,
        "half_w": half[0], "half_h": half[1],
    }


_SELECTOR_KIND = {"Block": "Block", "Node": "Node", "Event": "Event",
                  "Hierarchy": "Hier", "Annotation": "Annotation"}


def _ref(anchor: dict) -> str:
    """`Kind:id` for an anchor: names need not be unique, ids are."""
    return f"{_SELECTOR_KIND[anchor['kind']]}:{anchor['id']}"


def _to_scene_units(rbd, units: str, *coords: float | None) -> tuple[float | None, ...]:
    """Coordinates given in `units` ("scene" or "model") as scene pixels;
    a coordinate pair converts together, a missing one stays None."""
    if units == "scene":
        return coords
    if units != "model":
        raise BuildError(f"units must be 'scene' or 'model', got {units!r}")
    out: list[float | None] = []
    for i in range(0, len(coords), 2):
        cx, cy = coords[i], coords[i + 1]
        if cx is None or cy is None:
            out += [cx, cy]
        else:
            sx, sy = _model_to_scene(rbd, float(cx), float(cy))
            out += [round(sx, 2), round(sy, 2)]
    return tuple(out)


_GEOMETRY = ("x", "y", "w", "h", "x1", "y1", "x2", "y2")


def refresh_anchor_names(rbd) -> int:
    """Rewrite each anchor's stored names from the elements its ``ref``s
    point at, so a rename (of a block, node, event, hierarchy or an
    annotation's text) cannot leave a stale ``target``/``from``/``to``.

    ``ref`` is the authority (ids are unique and survive a rename); the
    names are kept for readers of the file and as a fallback. A ref that no
    longer resolves is left alone. Returns how many anchors changed."""
    changed = 0
    for entry in getattr(rbd, "annotations", None) or []:
        anchor = entry.get("anchor")
        if not isinstance(anchor, dict):
            continue
        touched = False
        for name_key, ref_key in (("target", "ref"), ("from", "from_ref"),
                                  ("to", "to_ref")):
            ref = anchor.get(ref_key)
            if not ref or name_key not in anchor:
                continue
            try:
                name = _anchor_for(rbd, ref)["name"]
            except BuildError:
                continue
            if anchor[name_key] != name:
                anchor[name_key] = name
                touched = True
        changed += touched
    return changed


def follow_layout(rbd, old_transform: dict | None = None) -> int:
    """Keep annotations with the diagram after Auto Layout moved elements.

    An annotation placed next to an element or another annotation
    (``anchor``) is placed next to it again, at the same location and gap;
    an arrow between two targets is redrawn between them. One placed by
    coordinates keeps its position in the model when the canvas transform
    changed (`old_transform` is the transform before the layout). An
    anchor whose target no longer exists, or whose name is ambiguous,
    leaves the annotation where it is. Returns how many annotations moved.

    Anchored notes that would land on an element, a free-standing note or
    a note placed before them are *nudged* to the nearest clear spot (see
    `_nudge`). This only happens here, after a layout: dragging an element
    in the GUI puts its notes back at their stored location, because a
    note that hops sideways while you drag is more surprising than one
    that briefly overlaps. A note the user dragged away from its anchored
    spot (the anchor's ``dx``/``dy``) keeps that offset and is not nudged.

    Anchors can chain (a note on a note on a block, an arrow between
    notes), so entries are processed in dependency order: every target is
    final before what is anchored to it. A cycle (which the API refuses to
    create, but a hand-edited file could hold) is processed once in file
    order rather than looped on.
    """
    new = view_transform.ensure(rbd)
    old = view_transform.ViewTransform.from_dict(old_transform) if old_transform else None
    refresh_anchor_names(rbd)
    entries = list(getattr(rbd, "annotations", None) or [])
    moved: set[int] = set()
    before = {id(e): {k: e.get(k) for k in _GEOMETRY} for e in entries}

    # Coordinate-placed annotations follow the transform once.
    for entry in entries:
        if isinstance(entry.get("anchor"), dict):
            continue
        if old is None or old == new:
            continue
        for kx, ky in (("x", "y"), ("x1", "y1"), ("x2", "y2")):
            if isinstance(entry.get(kx), (int, float)) and isinstance(entry.get(ky), (int, float)):
                mx, my = old.to_model(float(entry[kx]), float(entry[ky]))
                sx, sy = new.to_scene(mx, my)
                entry[kx], entry[ky] = round(sx, 2), round(sy, 2)

    anchored = sorted((e for e in entries if isinstance(e.get("anchor"), dict)),
                      key=lambda e: _anchor_depth(rbd, e))
    free = [e for e in entries
            if not isinstance(e.get("anchor"), dict) and e.get("kind") != "arrow"]
    placed: list[dict[str, Any]] = []        # anchored boxes already settled
    obstacles = _element_boxes(rbd)
    for entry in anchored:
        anchor = entry["anchor"]
        try:
            if entry.get("kind") == "arrow" and anchor.get("from") is not None:
                tail = _anchor_for(rbd, anchor.get("from_ref") or anchor["from"])
                head = _anchor_for(rbd, anchor.get("to_ref") or anchor["to"])
                entry.update({"x1": round(tail["cx"], 2), "y1": round(tail["cy"], 2),
                              "x2": round(head["cx"], 2), "y2": round(head["cy"], 2)})
            elif anchor.get("target") is not None:
                target = _anchor_for(rbd, anchor.get("ref") or anchor["target"])
                w, h = float(entry.get("w") or 0), float(entry.get("h") or 0)
                location = str(anchor.get("location", ANNO_DEFAULT_LOCATION))
                gap = float(anchor.get("gap", 12.0))
                dx, dy = anchor_offset(anchor)
                if dx or dy:
                    # Dragged by hand: the user chose this spot, so it is
                    # the anchored place plus their offset, never nudged.
                    px, py = _place_at_location(target, location, gap, w, h)
                    px, py = px + dx, py + dy
                else:
                    px, py = _nudge(
                        target, location, gap, w, h,
                        int(entry.get("parent", 0) or 0),
                        obstacles + [b for b in _note_boxes(free + placed, entry)])
                entry["x"], entry["y"] = round(px, 2), round(py, 2)
                placed.append(entry)
        except BuildError:
            continue                        # its target is gone: leave it

    for entry in entries:
        if any(entry.get(k) != before[id(entry)][k] for k in _GEOMETRY):
            moved.add(int(entry.get("id", 0) or 0))
    return len(moved)


def _anchor_depth(rbd, entry: dict[str, Any]) -> int:
    """How many annotations sit between `entry` and an element: 0 for one
    anchored to an element, 1 to a note on an element, and so on. Bounded,
    so a cycle yields a finite number instead of recursing."""
    depth, current, seen = 0, entry, set()
    while depth < 64:
        cid = int(current.get("id", 0) or 0)
        if cid in seen:
            break
        seen.add(cid)
        nxt = None
        for ref in _annotation_refs(current):
            try:
                found = _annotation_in(rbd, ref)
            except BuildError:
                continue
            if found is not None:
                nxt = found
                break
        if nxt is None:
            break
        depth, current = depth + 1, nxt
    return depth


_SECTION_REF = (("BLOCK_DATA", "Block"), ("NODE_DATA", "Node"),
                ("EVENT_DATA", "Event"), ("HIERARCHY_DATA", "Hier"))


def _element_records(rbd) -> list[dict[str, Any]]:
    """Every drawn element: its parent level, ``ref``, section, row and
    scene box ``(left, top, right, bottom)``."""
    out = []
    for section, prefix in _SECTION_REF:
        sec = rbd.sections.get(section)
        for row in (sec.rows if sec else []):
            if str(row.get("ID", "0")).strip() in ("", "0"):
                continue
            ref = f"{prefix}:{int(float(row['ID']))}"
            try:
                a = _anchor_for(rbd, ref)
            except BuildError:
                continue
            out.append({"parent": a["parent"], "ref": ref, "section": section,
                        "row": row,
                        "box": (a["cx"] - a["half_w"], a["cy"] - a["half_h"],
                                a["cx"] + a["half_w"], a["cy"] + a["half_h"])})
    return out


def _element_boxes(rbd) -> list[tuple[int, str, float, float, float, float]]:
    """(parent, ref, left, top, right, bottom) of every drawn element, in
    scene pixels. The ``ref`` lets a note ignore its own target."""
    return [(r["parent"], r["ref"], *r["box"]) for r in _element_records(rbd)]


def _stack_needs(rbd) -> dict[tuple[str, str], float]:
    """Vertical room, in scene pixels, the anchored notes want above and
    below each element: ``{(element ref, "above"|"below"): px}``.

    Notes anchored the same way on one element stack, and a note on a note
    on an element (all "above", say) stacks on top of it, so each adds its
    height plus its gap. Only ``above``/``below`` need vertical room; a
    note a user dragged away (``dx``/``dy``) is where they put it and asks
    for none."""
    entries = [e for e in (getattr(rbd, "annotations", None) or [])
               if isinstance(e.get("anchor"), dict) and e.get("kind") != "arrow"]
    by_id = {int(e.get("id", 0) or 0): e for e in entries}
    needs: dict[tuple[str, str], float] = {}
    for entry in entries:
        anchor = entry["anchor"]
        direction = str(anchor.get("location", ANNO_DEFAULT_LOCATION))
        if direction not in ("above", "below") or any(anchor_offset(anchor)):
            continue
        cost = float(entry.get("h") or 0) + float(anchor.get("gap", 12.0))
        current, depth = entry, 0
        while depth < 64:
            ref = str((current["anchor"].get("ref") or current["anchor"].get("target")) or "")
            prefix, _, ident = ref.partition(":")
            if prefix.strip().lower() == "annotation" and ident.strip().isdigit():
                parent = by_id.get(int(ident))
                if parent is None or str(parent["anchor"].get("location",
                                                              ANNO_DEFAULT_LOCATION)) != direction:
                    break
                current, depth = parent, depth + 1
                continue
            if ":" in ref:
                needs[(ref, direction)] = needs.get((ref, direction), 0.0) + cost
            break
    return needs


#: Extra clearance kept around an obstacle so a nudged note does not touch.
_NUDGE_MARGIN = 2.0

#: Slack added to the room a stack of notes asks for, so the outermost note
#: is not hard against the next element (drawn blocks are a little larger
#: than the nominal size notes are placed against).
_ROOM_SLACK = 12.0 + 2 * _NUDGE_MARGIN


def make_room_for_annotations(rbd) -> dict[tuple[str, int], tuple[float, float]]:
    """Stretch the space between vertically stacked elements so the notes
    anchored above or below them fit, instead of leaving them on top of a
    neighbour.

    For a pair of neighbours in one column that the notes between them
    would not fit, the elements above the gap move up and those below move
    down, by the same (grid-multiple) amount, so a split's joining nodes
    stay centred. Nothing ever moves closer; elements between the pair
    (the middle of a split) stay put; other hierarchy levels are separate.
    Horizontal room (``left``/``right`` notes) is not made.

    Positions are model units; the frozen view transform is not changed, so
    the stretch shows at the same scale. Returns the elements it moved as
    ``{(section, id): (x, y)}`` so a caller that keeps its own copy of
    positions (the GUI) can update it. Deterministic, and a model that
    already has room is left untouched.
    """
    from raptor.core.fileio.legacy_layout import GRID

    needs = _stack_needs(rbd)
    if not needs:
        return {}
    scale = view_transform.ensure(rbd).scale
    moved: dict[tuple[str, int], tuple[float, float]] = {}
    for _round in range(200):
        records = _element_records(rbd)
        violation = None
        for upper in sorted(records, key=lambda r: r["box"][1]):
            below_need = needs.get((upper["ref"], "below"), 0.0)
            for lower in records:
                if lower is upper or lower["parent"] != upper["parent"]:
                    continue
                if lower["box"][1] < upper["box"][3] - 1e-6:
                    continue                      # not below it
                if not (lower["box"][0] < upper["box"][2]
                        and lower["box"][2] > upper["box"][0]):
                    continue                      # a different column
                if any(m is not upper and m is not lower
                       and m["parent"] == upper["parent"]
                       and m["box"][1] >= upper["box"][3] - 1e-6
                       and m["box"][3] <= lower["box"][1] + 1e-6
                       and m["box"][0] < min(upper["box"][2], lower["box"][2])
                       and m["box"][2] > max(upper["box"][0], lower["box"][0])
                       for m in records):
                    continue                      # not neighbours
                want = below_need + needs.get((lower["ref"], "above"), 0.0)
                if want <= 0:
                    continue
                short = want + _ROOM_SLACK - (lower["box"][1] - upper["box"][3])
                if short > 1e-6:
                    violation = (upper, lower, short)
                    break
            if violation:
                break
        if violation is None:
            break
        upper, lower, short = violation
        step = 2 * GRID
        grow = math.ceil(short / scale / step - 1e-9) * step     # model units
        up_y = float(upper["row"]["yPos"])
        low_y = float(lower["row"]["yPos"])
        for rec in records:
            if rec["parent"] != upper["parent"]:
                continue
            y = float(rec["row"]["yPos"])
            if y >= up_y - 1e-9:
                y += grow / 2.0
            elif y <= low_y + 1e-9:
                y -= grow / 2.0
            else:
                continue
            rec["row"]["yPos"] = f"{round(y, 6) + 0.0:.6f}"
            moved[(rec["section"], int(float(rec["row"]["ID"])))] = (
                float(rec["row"]["xPos"]), y)
    return moved


def _note_boxes(notes: list[dict[str, Any]], skip: dict[str, Any]):
    for n in notes:
        if n is skip:
            continue
        try:
            x, y = float(n["x"]), float(n["y"])
            w, h = float(n.get("w") or 0), float(n.get("h") or 0)
        except (KeyError, TypeError, ValueError):
            continue
        yield (int(n.get("parent", 0) or 0), f"Annotation:{n.get('id')}",
               x, y, x + w, y + h)


def anchor_offset(anchor: dict[str, Any]) -> tuple[float, float]:
    """The manual offset (``dx``, ``dy`` scene px) a user dragged a note to
    from where its anchor puts it. (0, 0) when it was never dragged or the
    stored values are not numbers."""
    try:
        return float(anchor.get("dx", 0) or 0), float(anchor.get("dy", 0) or 0)
    except (TypeError, ValueError):
        return 0.0, 0.0


#: How many times the gap a note may be pushed out along one location.
_NUDGE_STEPS = 6


def _nudge(target: dict, location: str, gap: float, w: float, h: float,
           parent: int, boxes) -> tuple[float, float]:
    """The first clear top-left for a w x h note around `target`.

    Candidates, best first: the stored `location` at its gap, then the same
    location pushed out in gap-sized steps, then the other locations in a
    fixed order. The first that overlaps nothing wins; if none is clear the
    stored location is used unchanged. ``center`` is never nudged: it is
    meant to cover its target. The target itself is not an obstacle (the
    gap already keeps clear of it), and neither is anything on another
    hierarchy level. Deterministic in its inputs and independent of where
    the note is now, so a second layout gives the same answer.
    """
    preferred = _place_at_location(target, location, gap, w, h)
    if location == "center":
        return preferred
    own = (f"{target['kind'] if target['kind'] != 'Hierarchy' else 'Hier'}:"
           f"{target['id']}")
    relevant = [b for b in boxes if b[0] == parent and b[1] != own]

    def clear(pos: tuple[float, float]) -> bool:
        left, top = pos[0] - _NUDGE_MARGIN, pos[1] - _NUDGE_MARGIN
        right, bottom = pos[0] + w + _NUDGE_MARGIN, pos[1] + h + _NUDGE_MARGIN
        return not any(left < b[4] and right > b[2] and top < b[5] and bottom > b[3]
                       for b in relevant)

    if clear(preferred):
        return preferred
    others = [loc for loc in ("above", "right", "below", "left") if loc != location]
    for loc in [location] + others:
        for step in range(1 if loc != location else 2, _NUDGE_STEPS + 1):
            candidate = _place_at_location(target, loc, gap * step, w, h)
            if clear(candidate):
                return candidate
    return preferred


def _auto_size(text: str) -> tuple[float, float]:
    lines = (text or "").splitlines() or [""]
    longest = max((len(ln) for ln in lines), default=0)
    w = max(120.0, 7.5 * longest + 16.0)
    h = max(40.0, 18.0 * len(lines) + 14.0)
    return round(w, 2), round(h, 2)


def _place_at_location(anchor: dict, location: str, gap: float,
                       w: float, h: float) -> tuple[float, float]:
    """Top-left scene position for a w x h box at *location* around the
    anchor. ``center`` ignores ``gap``: a callout belongs over the target,
    not offset from it."""
    cx, cy = anchor["cx"], anchor["cy"]
    hw, hh = anchor["half_w"], anchor["half_h"]
    if location == "right":
        return (cx + hw + gap, cy - h / 2.0)
    if location == "left":
        return (cx - hw - gap - w, cy - h / 2.0)
    if location == "above":
        return (cx - w / 2.0, cy - hh - gap - h)
    if location == "below":
        return (cx - w / 2.0, cy + hh + gap)
    if location == "center":
        return (cx - w / 2.0, cy - h / 2.0)
    raise BuildError(
        f"unknown location {location!r}; use one of {ANNO_LOCATIONS}")


# ---------------------------------------------------------------------------
# Core placement (works on an in-memory RbdFile)
# ---------------------------------------------------------------------------

def _next_anno_id(rbd) -> int:
    used = {int(a.get("id", 0)) for a in getattr(rbd, "annotations", [])
            if str(a.get("id", "")).lstrip("-").isdigit()}
    i = 1
    while i in used:
        i += 1
    return i


def _require_finite_geometry(**values: Any) -> None:
    for key, value in values.items():
        if value is None:
            continue
        try:
            finite = math.isfinite(float(value))
        except (TypeError, ValueError, OverflowError):
            finite = False
        if not finite:
            raise BuildError(f"{key} must be a finite number; got {value!r}")


def place_annotation(
    rbd,
    *,
    kind: str = "text",
    text: str = "",
    adjacent_to: str | int | None = None,
    location: str = ANNO_DEFAULT_LOCATION,
    gap: float = 12.0,
    x: float | None = None,
    y: float | None = None,
    w: float | None = None,
    h: float | None = None,
    x1: float | None = None,
    y1: float | None = None,
    x2: float | None = None,
    y2: float | None = None,
    from_: str | int | None = None,
    to: str | int | None = None,
    parent: int | None = None,
    units: str = "scene",
    **style: Any,
) -> dict[str, Any]:
    """Add one annotation to *rbd* and return its entry (no save).

    Exactly one placement mode is required: ``adjacent_to`` (or
    ``from_``/``to`` for an arrow) **or** explicit coordinates.
    ``location`` defaults to ``"above"``; ``center`` ignores ``gap``.
    ``units="model"`` gives ``x``/``y`` (and an arrow's ``x1``..``y2``) in
    the model's own coordinates, the ones element ``x``/``y`` use; the
    default ``"scene"`` is the canvas pixels annotations are stored in.
    ``w``/``h`` are always scene pixels (a block is 108 x 46).

    Every rejected argument raises ``BuildError`` with a message naming
    what to supply. That is deliberate and is the reason the checks below
    are explicit rather than deferred to ``float()``: a caller -- or an
    MCP client on the other end of a JSON request -- cannot act on
    ``TypeError: float() argument must be... not 'NoneType'``, and an
    exception that escapes as the wrong type bypasses the handler that
    would have turned it into a useful response.
    """
    if kind not in ANNO_KINDS:
        raise BuildError(f"unknown annotation kind {kind!r}; use {ANNO_KINDS}")
    from raptor.api.limits import MAX_ANNOTATION_TEXT, MAX_ANNOTATIONS
    if len(str(text or "")) > MAX_ANNOTATION_TEXT:
        raise BuildError(
            f"annotation text is too long ({len(str(text))} characters); "
            f"the limit is {MAX_ANNOTATION_TEXT}")
    if len(getattr(rbd, "annotations", None) or []) >= MAX_ANNOTATIONS:
        raise BuildError(
            f"annotation limit reached ({MAX_ANNOTATIONS})")
    _require_finite_geometry(
        gap=gap, x=x, y=y, w=w, h=h, x1=x1, y1=y1, x2=x2, y2=y2)
    x, y, x1, y1, x2, y2 = _to_scene_units(rbd, units, x, y, x1, y1, x2, y2)
    if parent is not None:
        hiers = rbd.sections.get("HIERARCHY_DATA")
        known = {str(h.get("ID")) for h in (hiers.rows if hiers else [])}
        if str(parent) not in ("0", "None") and str(parent) not in known:
            raise BuildError(
                f"no hierarchy {parent!r}; annotations would be invisible "
                "at every level")
    if location not in ANNO_LOCATIONS:
        raise BuildError(
            f"unknown location {location!r}; use {ANNO_LOCATIONS}")
    unknown = set(style) - _ANNO_STYLE_KEYS
    if unknown:
        raise BuildError(
            f"unknown annotation property/properties {sorted(unknown)}; "
            f"allowed: {sorted(_ANNO_STYLE_KEYS)}")
    if style.get("line_style") is not None \
            and style["line_style"] not in _LINE_STYLES:
        raise BuildError(
            f"unknown line_style {style['line_style']!r}; use {_LINE_STYLES}")

    has_coords = x is not None or y is not None
    has_arrow_coords = any(v is not None for v in (x1, y1, x2, y2))
    anchor_ref = adjacent_to
    arrow_refs = from_ is not None or to is not None

    # Resolve the explicit coordinates to a definite pair, ONCE, here --
    # before the placement-mode checks rather than at the point of use.
    # Two reasons, both learned the hard way:
    #
    #  * x and y must come in pairs. The old check only asked whether
    #    EITHER was given, so `x=100` alone passed validation and went on
    #    to reach `float(None)`: a bare TypeError from inside the
    #    placement code, several frames from the call, with nothing in
    #    the message naming the argument that was missing. A caller --
    #    or an MCP client -- can act on "give both x and y"; it cannot
    #    act on a TypeError from float().
    #  * A type checker cannot carry that guarantee from the validation
    #    block to the use site, because the two live in separate
    #    `if kind ==` branches and negating
    #    `anchor_ref is None and (x is None or y is None)` yields a
    #    disjunction, not a statement that both are present. Resolving
    #    once here makes the guarantee a value, not an inference.
    explicit_xy: tuple[float, float] | None = None
    if has_coords:
        if x is None or y is None:
            raise BuildError("give both x and y (or neither); see "
                             "docs/RBDS_FORMAT.md#annotations")
        explicit_xy = (float(x), float(y))

    if kind == "arrow":
        if arrow_refs and anchor_ref is not None:
            raise BuildError("an arrow takes from_/to or adjacent_to, not both")
        if not arrow_refs and not has_arrow_coords:
            raise BuildError(
                "an arrow needs from_ and to (element names) or all four of "
                "x1/y1/x2/y2")
        if arrow_refs and (from_ is None or to is None):
            raise BuildError("an arrow needs both from_ and to")
    else:
        if anchor_ref is None and not has_coords:
            raise BuildError(
                "give adjacent_to (a name) or x and y; see "
                "docs/RBDS_FORMAT.md#annotations")

    entry: dict[str, Any] = {"kind": kind, "id": _next_anno_id(rbd)}
    for k, v in ANNO_DEFAULTS.items():
        entry[k] = v
    entry.update(style)

    # Anchors may point at another annotation; refuse a chain that loops
    # back to this one. Checked here, once the new id is known, and before
    # any geometry is written.
    for ref in (anchor_ref, from_, to):
        if ref is None:
            continue
        prefix, _, ident = str(ref).strip().partition(":")
        if prefix.strip().lower() == "annotation" and ident.strip().isdigit() \
                and int(ident) == int(entry["id"]):
            raise BuildError(
                "an annotation cannot be anchored to itself")
        target = _anchor_for(rbd, ref)
        if target.get("kind") != "Annotation":
            continue
        target_entry = _find(rbd, int(target["id"]))
        if target_entry is not None and _would_cycle(
                rbd, target_entry, int(entry["id"])):
            raise BuildError(
                "that anchor would create a cycle of annotations; pick a "
                "target that does not lead back to this annotation")

    if kind == "arrow":
        if arrow_refs:
            # `arrow_refs` is true when AT LEAST ONE endpoint was given,
            # and the check above already established that both are
            # present. A type checker cannot follow that through a bool,
            # so the invariant is restated here rather than assumed --
            # marked unreachable because it is, and because duplicating
            # the message is cheaper than a cast.
            if from_ is None or to is None:      # pragma: no cover
                raise BuildError("an arrow needs both from_ and to")
            tail = _anchor_for(rbd, from_)
            head = _anchor_for(rbd, to)
            entry.update({
                "x1": round(tail["cx"], 2), "y1": round(tail["cy"], 2),
                "x2": round(head["cx"], 2), "y2": round(head["cy"], 2),
                "parent": tail["parent"] if parent is None else parent,
                "x": 0.0, "y": 0.0,
                "anchor": {"from": tail["name"], "to": head["name"],
                           "from_ref": _ref(tail), "to_ref": _ref(head)},
            })
        else:
            # An explicit `or`-chain rather than a `for v in (...)` loop:
            # the loop catches the same cases at runtime, but it cannot
            # narrow the four individual variables, so every `float()`
            # below stayed Optional as far as a type checker was
            # concerned. Same message, same behaviour, statically sound.
            if x1 is None or y1 is None or x2 is None or y2 is None:
                raise BuildError("an arrow needs all four of x1/y1/x2/y2")
            entry.update({"x1": round(float(x1), 2), "y1": round(float(y1), 2),
                          "x2": round(float(x2), 2), "y2": round(float(y2), 2),
                          "parent": parent or 0, "x": 0.0, "y": 0.0})
        entry.setdefault("border_width", 2)
    else:
        aw, ah = w, h
        if aw is None or ah is None:
            auto_w, auto_h = _auto_size(text)
            aw = aw if aw is not None else auto_w
            ah = ah if ah is not None else auto_h
        aw, ah = float(aw), float(ah)
        if anchor_ref is not None:
            anchor = _anchor_for(rbd, anchor_ref)
            px, py = _place_at_location(anchor, location, gap, aw, ah)
            entry.update({
                "x": round(px, 2), "y": round(py, 2), "w": aw, "h": ah,
                "parent": anchor["parent"] if parent is None else parent,
                "anchor": {"target": anchor["name"], "ref": _ref(anchor),
                           "location": location, "gap": float(gap)},
            })
        else:
            # `explicit_xy` is a value, not an inference: it is a definite
            # pair whenever no anchor was given, because validation
            # rejected a lone x or y above. The fallback is unreachable
            # and says so, rather than being a silent zero-coordinate
            # annotation if that ever changed.
            px, py = explicit_xy if explicit_xy is not None else (0.0, 0.0)
            entry.update({"x": round(px, 2), "y": round(py, 2),
                          "w": aw, "h": ah, "parent": parent or 0})
    entry["text"] = text
    entry.setdefault("z", ANNO_Z_DEFAULT)

    if not hasattr(rbd, "annotations") or rbd.annotations is None:
        rbd.annotations = []
    rbd.annotations.append(entry)
    return entry


# ---------------------------------------------------------------------------
# Public API (Workspace-backed)
# ---------------------------------------------------------------------------

def add_annotation(
    workspace: Workspace,
    model_id: str,
    **kwargs: Any,
) -> dict[str, Any]:
    """Add an annotation and save the model.  See :func:`place_annotation`."""
    handle = workspace.get(model_id)
    entry = place_annotation(handle.rbd, **kwargs)
    workspace.save(model_id)
    return {"annotation": entry, "annotation_id": entry["id"],
            "summary": _summary(entry, handle.rbd)}


def list_annotations(workspace: Workspace, model_id: str,
                     parent: int | None = None) -> dict[str, Any]:
    """List annotations, optionally filtered to one hierarchy level."""
    handle = workspace.get(model_id)
    items = []
    for a in getattr(handle.rbd, "annotations", []) or []:
        if parent is not None and int(a.get("parent", 0) or 0) != int(parent):
            continue
        items.append(_summary(a, handle.rbd))
    return {"annotations": items, "count": len(items)}


def update_annotation(
    workspace: Workspace,
    model_id: str,
    annotation_id: int,
    patch: dict[str, Any],
) -> dict[str, Any]:
    """Patch one annotation.  Re-anchor with
    ``adjacent_to``/``location``/``gap`` (text/rect/ellipse) or
    ``from_``/``to`` (arrow); any style, ``text`` or explicit geometry
    field is applied directly.

    Supplying ``location``/``gap`` alone keeps the annotation's existing
    anchor (so ``{"location": "left"}`` re-places a name-anchored
    annotation without needing the target again).  ``kind`` may be
    changed, but changing it between ``arrow`` and the box kinds also
    needs the matching placement fields."""
    handle = workspace.get(model_id)
    rbd = handle.rbd
    entry = _find(rbd, annotation_id)
    if entry is None:
        raise AnnotationNotFound(
            f"no annotation with id {annotation_id}; call list_annotations")

    # Read the anchor dict ONCE and narrow that same value. The previous
    # form called `entry.get("anchor")` twice -- once in the isinstance
    # test, once to return it -- so the checker could not carry the
    # narrowing across the two calls and every use below was
    # "possibly None" even though the expression could only ever yield a
    # dict. An annotation that is not a dict means there is no anchor to
    # inherit from, which is a dict of nothing rather than a None.
    existing_anchor = entry.get("anchor")
    if not isinstance(existing_anchor, dict):
        existing_anchor = {}
    reanchor = any(k in patch for k in ("adjacent_to", "from_", "to",
                                        "location", "gap"))
    # Geometry/style fields applied directly; explicit coordinates may be
    # given in model units (`units: "model"`).
    patch = dict(patch)
    units = str(patch.pop("units", "scene"))
    drop_anchor = "anchor" in patch and patch.pop("anchor") is None
    direct = {k: v for k, v in patch.items()
              if k not in ("adjacent_to", "from_", "to", "location", "gap",
                           "kind")}
    unknown = set(direct) - _ANNO_STYLE_KEYS - {
        "text", "x", "y", "w", "h", "x1", "y1", "x2", "y2", "parent"}
    if unknown:
        raise BuildError(f"unknown annotation field(s) {sorted(unknown)}")
    if "line_style" in direct and direct["line_style"] not in _LINE_STYLES:
        raise BuildError(
            f"unknown line_style {direct['line_style']!r}; use {_LINE_STYLES}")
    if "text" in direct:
        from raptor.api.limits import MAX_ANNOTATION_TEXT
        if len(str(direct["text"] or "")) > MAX_ANNOTATION_TEXT:
            raise BuildError(
                f"annotation text is too long; the limit is "
                f"{MAX_ANNOTATION_TEXT} characters")
    _require_finite_geometry(**{
        k: direct[k] for k in ("gap", "x", "y", "w", "h",
                               "x1", "y1", "x2", "y2") if k in direct})
    if units != "scene":
        for kx, ky in (("x", "y"), ("x1", "y1"), ("x2", "y2")):
            if (kx in direct) != (ky in direct):
                raise BuildError(f"with units='model' give {kx} and {ky} together")
            if kx in direct:
                direct[kx], direct[ky] = _to_scene_units(rbd, units, direct[kx], direct[ky])

    if reanchor:
        # Rebuild geometry in place: remove, re-place with the merged fields.
        # Fall back to the existing anchor's target/from/to when only
        # location/gap is supplied, so a lone {"location": "left"} re-places
        # instead of silently de-anchoring.
        kind = str(patch.get("kind", entry.get("kind", "text")))
        adjacent_to = patch.get("adjacent_to")
        from_ = patch.get("from_")
        to = patch.get("to")
        if adjacent_to is None and from_ is None and to is None:
            # Only location/gap was supplied: keep the existing anchor.
            # Prefer the stored `ref` (ids are unique; annotation labels
            # and element names need not be).
            adjacent_to = existing_anchor.get("ref") or existing_anchor.get("target")
            from_ = existing_anchor.get("from_ref") or existing_anchor.get("from")
            to = existing_anchor.get("to_ref") or existing_anchor.get("to")
            if from_ is not None or to is not None:
                adjacent_to = None
        elif adjacent_to is not None:
            # An explicit box anchor replaces any arrow endpoints.
            from_ = to = None
        # Check against the id being kept, not the temporary one
        # `place_annotation` may allocate after removing this entry: a
        # gap in the id sequence can otherwise hide a cycle. `place_annotation`
        # checks again with its own id; this one is authoritative for an
        # update.
        for ref in (adjacent_to, from_, to):
            if ref is None:
                continue
            try:
                target = _anchor_for(rbd, ref)
            except BuildError:
                continue            # placement will report the bad target
            if target.get("kind") != "Annotation":
                continue
            target_entry = _find(rbd, int(target["id"]))
            if target_entry is not None and _would_cycle(
                    rbd, target_entry, annotation_id):
                raise BuildError(
                    "that anchor would create a cycle of annotations; pick a "
                    "target that does not lead back to this annotation")
        merged = {
            "kind": kind,
            "text": direct.pop("text", entry.get("text", "")),
            "adjacent_to": adjacent_to,
            "from_": from_,
            "to": to,
            "location": patch.get("location",
                                  existing_anchor.get("location", ANNO_DEFAULT_LOCATION)),
            "gap": patch.get("gap", existing_anchor.get("gap", 12.0)),
            "parent": direct.pop("parent", entry.get("parent")),
        }
        merged.update(direct)
        for k in _ANNO_STYLE_KEYS:
            merged.setdefault(k, entry.get(k, ANNO_DEFAULTS.get(k)))
        if merged["adjacent_to"] is None and merged["from_"] is None:
            # No anchor and no target given: keep existing explicit coords.
            merged["x"] = entry.get("x")
            merged["y"] = entry.get("y")
            merged["w"] = entry.get("w")
            merged["h"] = entry.get("h")
            merged["x1"] = entry.get("x1")
            merged["y1"] = entry.get("y1")
            merged["x2"] = entry.get("x2")
            merged["y2"] = entry.get("y2")
        rbd.annotations.remove(entry)
        try:
            new_entry = place_annotation(rbd, **merged)
        except Exception:
            rbd.annotations.append(entry)   # restore on failure
            raise
        new_entry["id"] = annotation_id
        workspace.save(model_id)
        return {"annotation": new_entry, "annotation_id": annotation_id,
                "summary": _summary(new_entry, rbd)}

    if "kind" in patch and patch["kind"] != entry.get("kind"):
        raise BuildError(
            "changing an annotation's kind needs its placement fields too "
            "(e.g. a new arrow needs from_/to); pass kind together with "
            "adjacent_to or from_/to")
    entry.update(direct)
    # Placed by hand now: it no longer follows its element.
    if drop_anchor or any(k in direct for k in ("x", "y", "x1", "y1", "x2", "y2")):
        entry.pop("anchor", None)
    workspace.save(model_id)
    return {"annotation": entry, "annotation_id": annotation_id,
            "summary": _summary(entry, rbd)}


def delete_annotation(workspace: Workspace, model_id: str,
                      annotation_id: int) -> dict[str, Any]:
    """Delete one annotation (no-op error if it does not exist)."""
    handle = workspace.get(model_id)
    rbd = handle.rbd
    entry = _find(rbd, annotation_id)
    if entry is None:
        raise AnnotationNotFound(
            f"no annotation with id {annotation_id}; call list_annotations")
    rbd.annotations.remove(entry)
    workspace.save(model_id)
    return {"deleted": annotation_id, "kind": entry.get("kind")}


def _find(rbd, annotation_id: int) -> dict[str, Any] | None:
    for a in getattr(rbd, "annotations", []) or []:
        if int(a.get("id", 0) or 0) == int(annotation_id):
            return a
    return None


def _summary(a: dict[str, Any], rbd) -> dict[str, Any]:
    out = {
        "annotation_id": a.get("id"),
        "kind": a.get("kind"),
        "parent": a.get("parent", 0),
        "text": a.get("text", ""),
    }
    if a.get("kind") == "arrow":
        out.update({"x1": a.get("x1"), "y1": a.get("y1"),
                    "x2": a.get("x2"), "y2": a.get("y2")})
    else:
        out.update({"x": a.get("x"), "y": a.get("y"),
                    "w": a.get("w"), "h": a.get("h")})
    if isinstance(a.get("anchor"), dict):
        out["anchor"] = a["anchor"]
    return out
