"""Constants and helpers shared by the diagram modules (no Qt items)."""
from __future__ import annotations
import logging
from raptor.core.fileio import view_transform
from raptor.core.fileio.typed import row_int


#: Silent by default (see raptor/gui/app.py's --debug / RAPTOR_GUI_DEBUG=1,
#: which turns this on for the whole raptor.gui.* namespace). Traces the
#: model-load path -- the last line logged before a native crash (segfault)
#: pinpoints which element/step was being built when it happened.
log = logging.getLogger("raptor.gui.diagram")

def _safe_int(value) -> int:
    """DELEGATED to `core.fileio.typed.row_int` (review E-12).

    Five copies of this coercion existed with THREE different sentinels
    (-1, 0 and None). The choice is not cosmetic, so it is stated here
    rather than inherited: -1 means "no element has this id", which is
    right for building lookup keys -- a garbage id must not collide with a
    real one.
    """
    return row_int(value, -1)

def _properties_offered(kind: str, n_sel: int) -> bool:
    """Whether the right-click "Properties" entry applies: only for a
    single Block/Node/Event. A Link has no Inspector view of its own, and
    "properties of N selected" is ambiguous. Kept as its own function so
    the decision is unit-testable without driving a real `QMenu.exec()`."""
    return n_sel == 1 and kind in ("Block", "Node", "Event")

def _dep_focus_offered(n_sel: int, has_badge: bool) -> bool:
    """Whether the right-click "Focus on Dependency" entry applies: only
    for a single element that carries a dependency badge. Kept separate
    for the same testability reason as `_properties_offered`."""
    return n_sel == 1 and has_badge

BLOCK_W = view_transform.BLOCK_W

BLOCK_H = view_transform.BLOCK_H

NODE_R = view_transform.NODE_R

MIN_ELEMENT_GAP = view_transform.MIN_ELEMENT_GAP

#: LINK_DATA's parent-level column and the per-section parent columns
#: (the .rbd format is not consistent about this naming).
_PARENT_COLS = {"BLOCK_DATA": "ParentId", "NODE_DATA": "parent",
                "EVENT_DATA": "ParentId", "HIERARCHY_DATA": "Parent"}

#: Drag-snap increment shared by every item: DiagramView sets these to
#: the same x/y pitch as the visible background grid whenever the layout
#: changes, so a dragged item lands exactly on a grid line -- which,
#: since that grid is drawn at the model's own authored spacing (see
#: _apply_scene_layout), means it re-aligns with whatever wasn't moved.
#: X and Y are independent, matching the grid itself (a legacy file's
#: row spacing and column spacing routinely differ). 0 disables that
#: axis.
_SNAP_STATE = {"x": 0.0, "y": 0.0}

#: The drawn grid line pitch (x_grid/y_grid) is divided by this many
#: steps for the actual snap increment. A full grid line is still exactly
#: this many snap-steps away in either direction, so existing
#: rows/columns stay reachable -- just with finer control.
SNAP_SUBDIVISIONS = 4
