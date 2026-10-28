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
- `ANNO_Z_MIDDLE` is what a newly placed annotation gets: a callout
  floating over the diagram.

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

#: The middle band, and what a newly placed annotation gets.
ANNO_Z_MIDDLE = 20.0

ANNO_Z_FRONT = 40.0

#: The three bands, back to front. Send to Back / Send Backward walk this.
ANNO_Z_BANDS = (ANNO_Z_BEHIND, ANNO_Z_MIDDLE, ANNO_Z_FRONT)
