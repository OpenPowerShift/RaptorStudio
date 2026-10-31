"""Annotation defaults and z-order bands — one owner, no Qt.

`ANNO_DEFAULTS` (9 keys) and `ANNO_Z_MIDDLE` were each written twice: once
in `api/annotations.py` and once in `gui/ui/diagram_annotations.py`, with
the same values. The API's copy carried the comment *"kept here rather than
imported so the API layer never needs Qt"* — a sound reason, and the wrong
mechanism. This module is the no-Qt answer to it: a `core/` table satisfies
both the reason and the goal, and the two doors can no longer disagree about
what a fresh annotation looks like.

The API's cross-reference was also stale by the time anyone read it: it
pointed at ``raptor/gui/ui/diagram.py``, but the GUI copy had moved to
``diagram_annotations.py``. That is the failure mode of duplicating a
constant *with* a cross-reference — the reference rots independently of the
value, and nothing notices because the values still agree.

The z-band boundaries are not arbitrary, and the reasoning is worth keeping
with the numbers rather than in a comment that can drift from them:

- `ANNO_Z_BEHIND` has to clear the grid (``-100``) so the background never
  paints over a note, and `ANNO_Z_FRONT` has to clear the rubber-band
  placement ghosts (``15``). Link items and hierarchy ovals sit at ``-1``,
  blocks/nodes/events at ``0`` (``10`` while selected), so anything at or
  below ``-1`` is behind every reliability element.
- `ANNO_Z_MIDDLE` is the floating callout band between the two. It is
  **not** the default for a new annotation any more: a note is drawn
  behind the reliability elements (`ANNO_Z_DEFAULT`) so it cannot hide
  the diagram it annotates. Users who want a callout on top still have
  Bring to Front / Bring Forward.

Note the API's ``ANNO_Z_MIDDLE`` was the int ``20`` and the GUI's the float
``20.0``. Same value, two types — harmless for the float comparisons the
canvas does, but the kind of difference that makes ``is`` fail and ``==``
succeed.
"""
from __future__ import annotations

from typing import Any

#: Default cosmetic properties for a fresh annotation. Also documented in
#: `docs/RBDS_FORMAT.md`.
ANNO_DEFAULTS: dict[str, Any] = {
    "border_colour": "#5D7080",
    # A brand-new rect/ellipse/text annotation starts with a 1px border:
    # 0 is the user's own explicit "off" value (`_anno_pen_brush` treats it
    # as "no pen"), which would make a new note invisible until its fill or
    # text made it noticeable.
    "border_width": 1,
    "fill_colour": None,          # None = transparent
    "text_colour": "#132238",
    "font_family": "sans-serif",
    "font_size": 10,
    "font_bold": False,
    "font_italic": False,
    # Applies to every shape kind's own border/line pen (rect / ellipse /
    # arrow all share this one property and one `_anno_pen_brush` call).
    "line_style": "solid",
}

#: The canvas paints in a fixed set of z bands (every `setZValue` in
#: `diagram_annotations.py` is one of these). Annotations get THREE of their
#: own, so a note can sit behind the reliability elements, in the middle
#: (the default), or in front of everything.
ANNO_Z_BEHIND = -50.0

#: The middle band: a callout that floats over the diagram.
ANNO_Z_MIDDLE = 20.0

ANNO_Z_FRONT = 40.0

#: What a newly placed annotation gets: behind the reliability elements.
#: A note is an overlay on the diagram, and starting it in front meant it
#: could cover the very blocks it describes (and, while an element moved,
#: sit over the gap where that element used to be).
ANNO_Z_DEFAULT = ANNO_Z_BEHIND

#: The three bands, back to front. Send to Back / Send Backward walk this.
ANNO_Z_BANDS = (ANNO_Z_BEHIND, ANNO_Z_MIDDLE, ANNO_Z_FRONT)

#: Where an anchored annotation sits relative to its target. This used to be
#: called "side" and was written into the `.rbds` anchor object as
#: ``"side"``. It is now "location", with ``center`` added for callouts that
#: belong over a circular node, an event hexagon or an oval. The rename is
#: deliberate and has no compatibility shim: no shipped `.rbds` carried
#: annotations when it happened, so an old ``side`` key would be a file
#: from a development build rather than a released one.
ANNO_LOCATIONS = ("right", "left", "above", "below", "center")

#: Default placement for a name-anchored annotation: above the target.
#: That is where a callout label is expected to read, and it keeps the
#: note clear of the links leaving a block's right-hand port.
ANNO_DEFAULT_LOCATION = "above"

#: Human-readable label for an annotation used when it is offered as an
#: anchor target (and stored in the anchor's ``target``). An annotation
#: has no name column, so its text is the only meaningful handle; an empty
#: shape falls back to its id.
def annotation_label(entry: dict[str, Any], limit: int = 40) -> str:
    text = " ".join(str(entry.get("text") or "").split())
    if text:
        return text if len(text) <= limit else text[: limit - 1].rstrip() + "…"
    return f"Annotation {entry.get('id', '?')}"


def annotation_geometry(entry: dict[str, Any]) -> dict[str, Any] | None:
    """Centre and half-extents of an annotation in scene pixels.

    Box kinds use their stored ``x``/``y``/``w``/``h``. An arrow has no
    box of its own, so its bounding box is derived from its two endpoints:
    the midpoint is the centre and each half-extent is half the span. That
    lets an arrow be an anchor target like anything else (and lets the
    ``center`` location put a note over it).
    """
    try:
        if entry.get("kind") == "arrow":
            x1 = float(entry.get("x1", 0) or 0)
            y1 = float(entry.get("y1", 0) or 0)
            x2 = float(entry.get("x2", 0) or 0)
            y2 = float(entry.get("y2", 0) or 0)
            cx, cy = (x1 + x2) / 2.0, (y1 + y2) / 2.0
            hw, hh = abs(x2 - x1) / 2.0, abs(y2 - y1) / 2.0
        else:
            x = float(entry.get("x", 0) or 0)
            y = float(entry.get("y", 0) or 0)
            w = float(entry.get("w", 0) or 0)
            h = float(entry.get("h", 0) or 0)
            cx, cy = x + w / 2.0, y + h / 2.0
            hw, hh = w / 2.0, h / 2.0
    except (TypeError, ValueError):
        return None
    return {
        "kind": "Annotation", "id": entry.get("id"),
        "name": annotation_label(entry),
        "parent": int(entry.get("parent", 0) or 0),
        "cx": cx, "cy": cy, "half_w": hw, "half_h": hh,
    }
