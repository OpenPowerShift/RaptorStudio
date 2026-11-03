"""Band hand-overs: the one rule the GUI and MCP Auto Layout share.

When Auto Layout wraps a level into bands with an explicit ``max_columns``,
every band keeps the flow direction, so the next band's first element sits
upstream of the previous band's last one. The link between them is a
*hand-over*. It is drawn as one straight run, pinned by two routing points
(stored in the file like any routing point), one just outside the exit side
of the last element and one just outside the entry side of the first.

This module decides which links are hand-overs, which sides they use and
where the two points go. It has no Qt and no model access, so the GUI canvas
(`DiagramView`) and the API (`raptor.api.handover_points`, used by the MCP
``auto_layout`` tool) both call it and cannot drift apart.

A *box* is ``(cx, cy, half_w, half_h)`` in scene pixels.
"""
from __future__ import annotations

Point = tuple[float, float]
Box = tuple[float, float, float, float]

#: Distance beyond a side at which the pinning point sits, in scene pixels.
STUB = 14.0

#: How far beyond a side a routing point may sit and still count as pinning
#: that side (used by the GUI when it draws a link).
BESIDE_REACH = 30.0

#: Outward unit vector of each side.
SIDE_VECTOR: dict[str, Point] = {
    "bottom": (0.0, 1.0), "top": (0.0, -1.0),
    "right": (1.0, 0.0), "left": (-1.0, 0.0)}


def handover_sides(source: Point, target: Point,
                   direction: str) -> tuple[str, str] | None:
    """The (exit, entry) sides for a hand-over from `source` to `target`
    (their centres), or None when the link is not a hand-over.

    A hand-over runs against the flow: the target is upstream of the
    source. It leaves and enters by the sides that face the gap between the
    bands: bottom to top (or top to bottom) in a horizontal flow, right to
    left (or left to right) in a vertical one. From those sides a straight
    line stays clear of both bands' blocks."""
    sx, sy = source
    tx, ty = target
    if direction in ("LR", "RL"):
        behind = tx < sx - 1.0 if direction == "LR" else tx > sx + 1.0
        if behind:
            return ("bottom", "top") if ty >= sy else ("top", "bottom")
    elif direction in ("TB", "BT"):
        behind = ty < sy - 1.0 if direction == "TB" else ty > sy + 1.0
        if behind:
            return ("right", "left") if tx >= sx else ("left", "right")
    return None


def side_port(box: Box, side: str) -> Point:
    """The middle of `side` on the box outline."""
    cx, cy, hw, hh = box
    dx, dy = SIDE_VECTOR[side]
    return cx + dx * hw, cy + dy * hh


def handover_points(source: Box, target: Box, direction: str,
                    stub: float = STUB) -> list[Point] | None:
    """The two pinning points for a link from `source` to `target`, in
    path order (near the source first), or None when it is no hand-over."""
    sides = handover_sides((source[0], source[1]), (target[0], target[1]),
                           direction)
    if sides is None:
        return None
    out: list[Point] = []
    for box, side in ((source, sides[0]), (target, sides[1])):
        px, py = side_port(box, side)
        dx, dy = SIDE_VECTOR[side]
        out.append((round(px + dx * stub, 2), round(py + dy * stub, 2)))
    return out


def side_beside(box: Box, point: Point, reach: float = BESIDE_REACH) -> str | None:
    """The side of `box` that `point` sits directly outside, else None.

    "Directly outside" is within the box's extent across that side and
    within `reach` of it, so a point a little way off an element's edge
    pins the link to that edge."""
    cx, cy, hw, hh = box
    dx, dy = point[0] - cx, point[1] - cy
    if abs(dx) <= hw and hh < dy <= hh + reach:
        return "bottom"
    if abs(dx) <= hw and -hh - reach <= dy < -hh:
        return "top"
    if abs(dy) <= hh and hw < dx <= hw + reach:
        return "right"
    if abs(dy) <= hh and -hw - reach <= dx < -hw:
        return "left"
    return None
