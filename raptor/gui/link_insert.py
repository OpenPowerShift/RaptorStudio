"""Pure geometry for inserting a new element on an existing link.

The canvas supplies spans and vectors in scene units; this module owns the
rules that can be tested without a QApplication. Keeping it Qt-free matters:
the flow/placement decisions are the part most likely to be wrong in a way
that is hard to see in a GUI test, and they are simple arithmetic.
"""
from __future__ import annotations

from dataclasses import dataclass
from math import hypot

FLOW_DIRECTIONS = ("LR", "RL", "TB", "BT")

#: An element's extent along one axis: (minimum, maximum), scene units.
Span = tuple[float, float]


@dataclass(frozen=True)
class FlowAxis:
    """The downstream direction used by placement and reflow."""

    axis: str       # "x" or "y"
    sign: int       # +1 grows with the axis, -1 grows against it

    def delta(self, distance: float) -> tuple[float, float]:
        if self.axis == "x":
            return (self.sign * float(distance), 0.0)
        return (0.0, self.sign * float(distance))


@dataclass(frozen=True)
class InsertionPlan:
    """Where the new element's centre goes and how far the target moves."""

    center: float
    shift: float
    gap_upstream: float
    gap_downstream: float


@dataclass(frozen=True)
class HoverPick:
    """The result of resolving the pointer against nearby links."""

    index: int | None
    state: str          # "none" | "allowed" | "blocked"
    reason: str = ""


def dominant_flow(vectors: list[tuple[float, float]],
                  configured: str = "auto") -> str:
    """The page flow direction, LR/RL/TB/BT.

    `configured` wins when it is one of the explicit directions (Auto Layout
    sets it). "auto" infers from the visible source->target vectors. The vote
    is weighted by link length, so a long backbone is not outvoted by many
    short vertical connectors. An empty or perfectly balanced model falls
    back to LR, the historical default.
    """
    if configured in FLOW_DIRECTIONS:
        return configured
    total_dx = sum(dx for dx, _dy in vectors)
    total_dy = sum(dy for _dx, dy in vectors)
    weight_x = sum(abs(dx) for dx, _dy in vectors)
    weight_y = sum(abs(dy) for _dx, dy in vectors)
    if weight_x <= 1e-9 and weight_y <= 1e-9:
        return "LR"
    if weight_x >= weight_y:
        return "LR" if total_dx >= 0.0 else "RL"
    return "TB" if total_dy >= 0.0 else "BT"


def choose_flow_axis(dx: float, dy: float, page_flow: str) -> FlowAxis | None:
    """Pick the downstream axis/sign for one link.

    The page axis owns a diagonal link; the link's own arrow owns the sign.
    A link that is mostly perpendicular to the page axis keeps its own axis
    (a vertical hand-over in an LR diagram must push down/up, not sideways).
    """
    length = hypot(dx, dy)
    if length <= 1e-9:
        return None
    page_axis = "x" if page_flow in ("LR", "RL") else "y"
    along = dx if page_axis == "x" else dy
    if abs(along) >= 0.5 * length:
        page_sign = 1 if page_flow in ("LR", "TB") else -1
        sign = page_sign if abs(along) <= 1e-9 else (1 if along > 0.0 else -1)
        return FlowAxis(page_axis, sign)
    if abs(dx) >= abs(dy):
        return FlowAxis("x", 1 if dx >= 0.0 else -1)
    return FlowAxis("y", 1 if dy >= 0.0 else -1)


def span(center: float, half_extent: float) -> Span:
    return (center - half_extent, center + half_extent)


def span_size(value: Span) -> float:
    return value[1] - value[0]


def flow_edges(source: Span, target: Span,
               flow: FlowAxis) -> tuple[float, float]:
    """(source's downstream edge, target's upstream edge)."""
    if flow.sign > 0:
        return source[1], target[0]
    return source[0], target[1]


def available_gap(source: Span, target: Span, flow: FlowAxis) -> float:
    """Free distance from the source's downstream edge to the target's
    upstream edge, positive when the target really is downstream."""
    source_edge, target_edge = flow_edges(source, target, flow)
    return (target_edge - source_edge) * flow.sign


def plan_equal_spacing(source: Span, target: Span, new_size: float,
                       flow: FlowAxis, gap: float) -> InsertionPlan:
    """Centre the new element in the available gap.

    When the gap is too small for the element plus a standard gap on each
    side, the returned `shift` is how far the target (and its downstream
    subgraph) must move. Both side gaps then equal `gap`.
    """
    gap = max(0.0, float(gap))
    new_size = max(0.0, float(new_size))
    source_edge, target_edge = flow_edges(source, target, flow)
    room = available_gap(source, target, flow)
    required = new_size + 2.0 * gap
    if room >= required:
        center = (source_edge + target_edge) / 2.0
        side_gap = (room - new_size) / 2.0
        return InsertionPlan(center, 0.0, side_gap, side_gap)
    shift = required - room
    center = source_edge + flow.sign * (gap + new_size / 2.0)
    return InsertionPlan(center, shift, gap, gap)


def plan_minimum_spacing(source: Span, target: Span, new_span: Span,
                         flow: FlowAxis, gap: float) -> InsertionPlan:
    """Clamp a proposed element position to the standard gap.

    Used for manual dog-legs, where the proposed centre comes from the drawn
    polyline rather than the equal-gap rule. The element is slid downstream
    if it would sit too close to the source; then the target is shifted if
    the downstream gap is too small.
    """
    gap = max(0.0, float(gap))
    center = (new_span[0] + new_span[1]) / 2.0
    half = max(0.0, span_size(new_span)) / 2.0
    source_edge, target_edge = flow_edges(source, target, flow)
    if flow.sign > 0:
        upstream = new_span[0] - source_edge
        downstream = target_edge - new_span[1]
    else:
        upstream = source_edge - new_span[1]
        downstream = new_span[0] - target_edge
    if upstream < gap:
        center += flow.sign * (gap - upstream)
        if flow.sign > 0:
            upstream = gap
            downstream = target_edge - (center + half)
        else:
            upstream = gap
            downstream = (center - half) - target_edge
    shift = max(0.0, gap - downstream)
    return InsertionPlan(center, shift, upstream, max(gap, downstream))


def pick_hover(distances: list[float], pick_radius: float,
               ambiguity_margin: float) -> HoverPick:
    """Resolve the nearest-link decision.

    A single link inside the radius is allowed. If another link is within
    `ambiguity_margin` of the nearest, the hover is blocked: this is the
    orthogonal-overlap case where two routes share the same pixels and any
    pick would be arbitrary.
    """
    within = [(distance, index) for index, distance in enumerate(distances)
              if distance <= pick_radius]
    if not within:
        return HoverPick(None, "none")
    within.sort(key=lambda pair: (pair[0], pair[1]))
    nearest_distance, nearest_index = within[0]
    if len(within) > 1 and within[1][0] - nearest_distance <= ambiguity_margin:
        return HoverPick(
            nearest_index, "blocked",
            "Two or more links overlap here — zoom in or move the pointer")
    return HoverPick(nearest_index, "allowed")
