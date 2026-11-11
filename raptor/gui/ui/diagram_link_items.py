"""Link and route-grip items of the diagram canvas, with the flow-direction and routing settings they read."""
from __future__ import annotations

import contextlib

from raptor.gui.ui.diagram_item_core import (
    _BlockItem, _NodeItem, _snap_of, _snapped_x, _snapped_y,
)
from raptor.gui.ui.diagram_element_items import _EventItem, _HierItem
from collections.abc import Mapping
from typing import Any
from shiboken6 import isValid
from PySide6.QtCore import (
    Qt, QRectF, QPointF, QLineF,
)
from PySide6.QtGui import (
    QPen, QBrush, QColor, QPainter, QPolygonF,
)
from PySide6.QtWidgets import (
    QGraphicsItem,
    QGraphicsEllipseItem, QGraphicsPathItem,
)
from PySide6.QtGui import QPainterPath
from raptor.core.fileio.band_handover import side_beside
from raptor.core.routing import ROUTING_MODES, Router, angled, fan_hint
from raptor.gui.resources.icons import arrow_head_len, arrow_head_points
from raptor.gui.resources.design_tokens import (
    COLOUR,
)
from raptor.gui.ui.diagram_common import (  # noqa: I001
    BLOCK_H,
)


#: Preferred page-flow direction for port selection. Auto Layout sets it
#: from its chosen direction; LR is the historical default.
_FLOW = {"direction": "auto"}


FLOW_DIRECTIONS = ("auto", "LR", "RL", "TB", "BT")


def set_flow_direction(direction: str) -> None:
    """Set the page-flow axis used by link port selection.

    "auto" keeps the historical neighbour inference, which is what a
    hand-drawn diagram needs. Auto Layout sets an explicit LR/RL/TB/BT."""
    if direction not in FLOW_DIRECTIONS:
        raise ValueError(f"unknown flow direction {direction!r}")
    if _FLOW["direction"] != direction:
        _FLOW["direction"] = direction
        _LINK_GEN[0] += 1


def flow_direction() -> str:
    return _FLOW["direction"]


#: Bumped whenever a link item is created or enters/leaves a scene, so the
#: per-scene link index below knows when it is stale.
_LINK_GEN = [0]


def _links_at(item) -> tuple[list, list]:
    """(incoming, outgoing) link items of `item`.

    Routing needs an element's links constantly (its entry and exit sides
    depend on where its neighbours are). It used to find them by walking
    EVERY item in the scene and testing `isinstance` -- per element, per
    link, on every drag step -- which made moving a block on a 150-block
    model take a second. The index is built once per scene and reused until
    a link is added or removed."""
    scene = item.scene()
    if scene is None:
        return [], []
    idx = getattr(scene, "_link_index", None)
    if idx is None or idx[0] != _LINK_GEN[0]:
        table: dict[int, tuple[list, list]] = {}
        for it in scene.items():
            if isinstance(it, _LinkItem):
                table.setdefault(id(it._to), ([], []))[0].append(it)
                table.setdefault(id(it._from), ([], []))[1].append(it)
        idx = (_LINK_GEN[0], table)
        scene._link_index = idx  # type: ignore[attr-defined]
    pair = idx[1].get(id(item))
    if pair is None or not isValid(item):
        return [], []
    # A level change deletes items without necessarily telling the index;
    # never hand back a link (or an end of one) that Qt has already deleted.
    return ([link for link in pair[0] if isValid(link) and isValid(link._from)],
            [link for link in pair[1] if isValid(link) and isValid(link._to)])


#: How links are drawn: one of `raptor.core.routing.ROUTING_MODES`.
_ROUTING = {"mode": "straight"}


_ROUTER = Router()


def set_link_routing(mode: str) -> None:
    """Choose how every link on the canvas is drawn. The caller re-routes
    the links it has (`DiagramView.reroute_links`)."""
    _ROUTING["mode"] = mode if mode in ROUTING_MODES else "straight"


def link_routing() -> str:
    return _ROUTING["mode"]


def _element_box(item) -> tuple[float, float, float, float]:
    """The item's outline box in scene coordinates."""
    c = _LinkItem._true_centre(item)
    hw, hh = _LinkItem._half_extents(item)
    return (c.x() - hw, c.y() - hh, c.x() + hw, c.y() + hh)



#: Scenes a batch re-route is running over, with one snapshot of every
#: element box for the duration. Without it each link in the batch built
#: the same O(elements) obstacle list, so re-routing a 3,000-link drawing
#: was quadratic. A batch never moves an element, so the snapshot holds.
_ROUTE_BATCH: list[tuple[object, list]] = []


@contextlib.contextmanager
def routing_batch(scene):
    """Route a burst of links against one obstacle snapshot."""
    if scene is None or _ROUTING["mode"] != "orthogonal":
        yield
        return
    items = [it for it in scene.items()
             if isinstance(it, (_BlockItem, _NodeItem, _EventItem, _HierItem))
             and isValid(it)]
    _ROUTE_BATCH.append((scene, [_element_box(it) for it in items]))
    try:
        yield
    finally:
        _ROUTE_BATCH.pop()


def _element_boxes(scene, skip) -> list:
    """Scene boxes (left, top, right, bottom) of every element, minus the
    ones in `skip` -- what an orthogonal link has to keep clear of. The
    element list is cached per scene with the link index; positions are
    read live. The two linked elements need no filtering from a batch
    snapshot: `Router._search` drops any box containing a port."""
    for batch_scene, boxes in reversed(_ROUTE_BATCH):
        if batch_scene is scene:
            return boxes
    cache = getattr(scene, "_element_cache", None)
    if cache is None or cache[0] != _LINK_GEN[0]:
        cache = (_LINK_GEN[0], [it for it in scene.items()
                                if isinstance(it, (_BlockItem, _NodeItem,
                                                   _EventItem, _HierItem))])
        scene._element_cache = cache  # type: ignore[attr-defined]
    out = []
    for it in cache[1]:
        if it in skip or not isValid(it):
            continue
        out.append(_element_box(it))
    return out


def links_affected_by_moving(item) -> list:
    """Every link whose drawn route can change when `item` moves: the links
    touching it, and the links touching its direct neighbours (a
    neighbour's entry/exit side depends on where its neighbours are, and
    `item` is one of them). Nothing else reads `item`'s position, so this
    is exact, not an approximation -- for straight links. A right-angle
    link also steers round other elements, so the links whose drawn path
    now crosses `item` are added. That is a lookup in the scene's spatial
    index, not a re-route of every link: a link nowhere near the moved
    element keeps its route, and so does one that merely detoured round the
    element's old position (a detour that is no longer needed is harmless
    and is tidied the next time the link is touched)."""
    incoming, outgoing = _links_at(item)
    affected: dict[int, object] = {}
    scene = item.scene()
    if _ROUTING["mode"] == "orthogonal" and scene is not None:
        for it in scene.items(item.sceneBoundingRect().adjusted(-6, -6, 6, 6)):
            if isinstance(it, _LinkItem):
                affected[id(it)] = it
    for link in (*incoming, *outgoing):
        affected[id(link)] = link
        for end in (link._from, link._to):
            if end is item:
                continue
            i2, o2 = _links_at(end)
            for other in (*i2, *o2):
                affected[id(other)] = other
    return list(affected.values())


class _LinkItem(QGraphicsPathItem):
    """A link: a straight line between two ports. A port is the
    **midpoint of the side** the link approaches from, computed on the
    item's real outline -- a rectangle edge, a circle's cardinal point, a
    hexagon vertex/edge midpoint or an ellipse's extreme point -- so the
    line always touches the shape (the previous centre-to-centre line
    exited the axis-aligned bounding box and floated off hexagons/ovals).

    Ports follow the page's flow: an element's incoming links enter on
    the side facing their source, and its outgoing links leave on the
    **opposite** side (in from the left -> out to the right; in from the
    top -> out the bottom), so a node never enters and leaves on the same
    side. `line()` is the drawn segment between the two ports."""

    def __init__(self, from_item: QGraphicsItem, to_item: QGraphicsItem,
                 link_num: int | None = None, *, defer_route: bool = False):
        super().__init__()
        self._from = from_item
        self._to = to_item
        self._link_num = link_num
        self._selected = False
        self._p0 = QPointF()
        self._p3 = QPointF()
        self._head = QPolygonF()
        #: GUI-only dog-leg points dragged onto this link. They never enter
        #: the model: the link still connects the same two elements.
        self._grips: list[_RouteGripItem] = []
        #: The (exit, entry) sides last chosen to face the routing points.
        self._grip_sides: tuple[str | None, str | None] = (None, None)
        #: Add-tool hover feedback: None, "allowed" or "blocked". Drawn as
        #: a wide translucent halo under the normal stroke so selection and
        #: live-sim colours keep their own meanings.
        self._insert_highlight: str | None = None
        _LINK_GEN[0] += 1
        self.setFlag(QGraphicsItem.GraphicsItemFlag.ItemIsSelectable)
        self.setAcceptHoverEvents(True)
        self.setZValue(-1)
        self.retheme(COLOUR)
        # A whole-level rebuild creates thousands of links back to back.
        # Routing each one in the constructor made `_links_at` rebuild the
        # scene-wide link index after every single insertion (the generation
        # counter had just changed), so opening a 5,000-link model was
        # quadratic and took minutes. The batch caller builds the index once
        # after the last link and then routes them all.
        if not defer_route:
            self._update_route()

    def line(self) -> QLineF:
        """The segment between the two ports -- the drawn geometry."""
        return QLineF(self._p0, self._p3)

    def nearest_point(self, scene_pos: QPointF) -> QPointF:
        """The closest point on the link's drawn path to `scene_pos`.

        Used to seed a routing point exactly on the line, not wherever the
        right-click landed. Polyline segments are enough for straight,
        angled and right-angle links; curve control points only make the
        answer slightly conservative.
        """
        path = self.path()
        if path.elementCount() == 0:
            return scene_pos
        if path.elementCount() == 1:
            e = path.elementAt(0)
            return QPointF(e.x, e.y)
        best = scene_pos
        best_d = float("inf")
        for i in range(1, path.elementCount()):
            a, b = path.elementAt(i - 1), path.elementAt(i)
            ax, ay, bx, by = a.x, a.y, b.x, b.y
            dx, dy = bx - ax, by - ay
            denom = dx * dx + dy * dy
            if denom <= 1e-12:
                px, py = ax, ay
            else:
                t = ((scene_pos.x() - ax) * dx
                     + (scene_pos.y() - ay) * dy) / denom
                t = max(0.0, min(1.0, t))
                px, py = ax + t * dx, ay + t * dy
            d = (scene_pos.x() - px) ** 2 + (scene_pos.y() - py) ** 2
            if d < best_d:
                best_d, best = d, QPointF(px, py)
        return best

    def set_insert_highlight(self, state: str | None) -> None:
        """Add-tool hover state: None, "allowed" or "blocked"."""
        if state not in (None, "allowed", "blocked"):
            raise ValueError(f"unknown insert highlight {state!r}")
        if self._insert_highlight != state:
            self._insert_highlight = state
            self.update()

    def add_grip(self, grip) -> None:
        """Attach a draggable routing point.

        A newly added point is inactive until dragged, so the link keeps
        its exact existing path -- re-routing here could pick a slightly
        different (but valid) orthogonal route. Only an active point
        changes the drawing.
        """
        if grip not in self._grips:
            self._grips.append(grip)
        if getattr(grip, "_active", True):
            self._update_route()

    def remove_grip(self, grip) -> None:
        if grip in self._grips:
            self._grips.remove(grip)
        self._update_route()

    def grip_items(self) -> list:
        return [g for g in self._grips if isValid(g)]

    def shape(self):
        """Wide (10px) hit area along the line -- the bare 2.5px stroke
        was practically impossible to click for selection."""
        from PySide6.QtGui import QPainterPathStroker
        stroker = QPainterPathStroker()
        stroker.setWidth(10)
        return stroker.createStroke(self.path())

    def boundingRect(self):
        # Pad past the path's own stroke so the arrowhead wings (painted
        # beyond the head endpoint) aren't left un-repainted.
        base = super().boundingRect()
        return base.adjusted(-12, -12, 12, 12)

    def _update_route(self):  # noqa: C901, PLR0912
        from_item, to_item = self._from, self._to
        # Every incoming link to an element shares its one entry port, and
        # every outgoing link shares its one exit port (opposite the
        # entry), so the flow passes straight through and both ports are
        # visible on the outline.
        fc = self._true_centre(from_item)
        tc = self._true_centre(to_item)
        exit_side = self._exit_side(from_item)
        p0 = self._port_point(from_item, exit_side)
        # Per-link direction, NOT the element's single shared entry side.
        #
        # `_entry_side` collapsed every incoming link onto one side and
        # forced "left" for anything in a left-to-right run, so a block fed
        # from directly above AND from the left put both connections on
        # its left edge -- the incoming-from-above link then had to run
        # round the body, which is both ugly and what the crossing
        # avoidance below exists to paper over. `_side` is already
        # direction-aware and already biased towards the page-flow
        # direction (a vertical port needs the vertical component to
        # dominate by more than `_HORIZONTAL_BIAS`), so asking it per
        # link gives top-above / left-from-the-left with no special case.
        entry = self._entry_side_for(to_item, fc)
        p3 = self._port_point(to_item, entry)
        # A link must never cut through the body of an element it connects
        # to. If the shared port would send this particular line across
        # the shape (fan-in from more than one direction), fall back to
        # the facing side. Iterate: flipping one end can change whether
        # the other end is crossed.
        # A routing point placed just outside a side of an end element
        # (Auto Layout puts one at each end of a band hand-over) says which
        # side the link leaves or enters by. The crossing check below would
        # otherwise flip that port to avoid the straight line it assumes.
        active = [g.pos() for g in self._grips
                  if isValid(g) and getattr(g, "_active", True)]
        pinned = False
        if active:
            # Each end leaves/enters by the side that faces the routing
            # point next to it: directly beside the element it pins that
            # edge, otherwise the same facing rule every link uses, with a
            # little stickiness so dragging a point across a boundary does
            # not make the end flicker between two sides.
            prev_out, prev_in = self._grip_sides
            out_side = (self._side_beside(from_item, active[0])
                        or self._sticky_side(from_item, active[0], prev_out))
            in_side = (self._side_beside(to_item, active[-1])
                       or self._sticky_side(to_item, active[-1], prev_in))
            self._grip_sides = (out_side, in_side)
            exit_side = out_side
            p0 = self._port_point(from_item, exit_side)
            entry = in_side
            p3 = self._port_point(to_item, entry)
            pinned = True
        else:
            self._grip_sides = (None, None)
        for _ in range(0 if pinned else 3):
            changed = False
            if self._segment_crosses(to_item, p0, p3):
                entry = self._entry_side_for(to_item, fc)
                p3 = self._port_point(to_item, entry)
                changed = True
            if self._segment_crosses(from_item, p0, p3):
                exit_side = self._exit_fallback_side(from_item, tc)
                p0 = self._port_point(from_item, exit_side)
                changed = True
            if not changed:
                break
        route = None
        grips = active
        mode = _ROUTING["mode"]
        if grips:
            # A dragged routing point is a deliberate dog-leg: draw through
            # it rather than letting the orthogonal router paper over it.
            mode = "straight"
        if mode != "straight" and from_item is not to_item \
                and self.scene() is not None:
            a, b = (p0.x(), p0.y()), (p3.x(), p3.y())
            if mode == "angled":
                route = angled(a, exit_side, b, entry)
            else:
                incoming, outgoing = _links_at(to_item)[0], _links_at(from_item)[1]
                hint = fan_hint(
                    exit_side, entry, a, b,
                    [_element_box(link._to) for link in outgoing],
                    [_element_box(link._from) for link in incoming])
                route = _ROUTER.route(
                    a, exit_side, b, entry,
                    _element_boxes(self.scene(), (from_item, to_item)), hint)
        self.prepareGeometryChange()
        self._p0, self._p3 = p0, p3
        path = QPainterPath(p0)
        if grips:
            tip_from = p0
            for point in grips:
                path.lineTo(point)
                tip_from = point
            path.lineTo(p3)
        elif route is None:             # straight, or the router gave up
            tip_from = p0
            path.lineTo(p3)
        else:
            tip_from = QPointF(*route[-2])
            for x, y in route[1:]:
                path.lineTo(x, y)
        self._head = arrow_head_points(tip_from.x(), tip_from.y(),
                                       p3.x(), p3.y(), arrow_head_len(1.0))
        self.setPath(path)

    @staticmethod
    def _box(item) -> tuple[float, float, float, float]:
        """(cx, cy, half_w, half_h) of an end element, scene pixels."""
        c = _LinkItem._true_centre(item)
        hw, hh = _LinkItem._half_extents(item)
        return c.x(), c.y(), hw, hh

    @staticmethod
    def _side_beside(item, point: QPointF) -> str | None:
        """The side of `item` that `point` sits directly outside (the
        shared rule in `raptor.core.fileio.band_handover`), else None."""
        return side_beside(_LinkItem._box(item), (point.x(), point.y()))

    @staticmethod
    def _true_centre(item) -> QPointF:
        """The item's real centre. A block is top-left anchored (its
        `pos()` is the corner), everything else is centre anchored. Uses
        the shape rect rather than `sceneBoundingRect()` for a node,
        whose capacity-gauge/dependency-badge padding skews the box
        centre off the circle."""
        cached = getattr(item, "_route_centre", None)
        if cached is not None:
            return cached
        if isinstance(item, _BlockItem):
            centre = item.mapToScene(item.rect().center())
        else:
            centre = item.scenePos()
        item._route_centre = centre
        return centre

    @staticmethod
    def _half_extents(item):
        """Half-width/half-height of the item's true shape -- the port
        sits at the middle of a side, so this places it on the outline."""
        if isinstance(item, _BlockItem):
            return item.rect().width() / 2.0, BLOCK_H / 2.0
        if isinstance(item, _NodeItem):
            return item._r, item._r
        if isinstance(item, _EventItem):
            return item._half_w, item._r
        if isinstance(item, _HierItem):
            return _HierItem.W / 2.0, _HierItem.H / 2.0
        r = item.sceneBoundingRect()
        return r.width() / 2.0, r.height() / 2.0

    #: Horizontal component (relative to the vertical) above which a
    #: connection is treated as entering/leaving the left/right side.
    #: The page flows left-to-right, so up to ~63 degrees off horizontal
    #: still reads as a "left/right" port -- keeping entry on the left
    #: and exit on the right for the balanced look, and reserving
    #: top/bottom for genuinely vertical connections.
    _HORIZONTAL_BIAS = 0.5
    #: The same, for round junction nodes (see `_side`).
    _NODE_HORIZONTAL_BIAS = 0.2
    #: An element whose sources are all on one side of it and targets all
    #: on the other reads as a horizontal flow even when the links are
    #: steep, as long as each is at least this far off vertical.
    _MONOTONE_MIN_SLOPE = 0.15

    @staticmethod
    def _side(item, toward: QPointF) -> str:
        """Which side of `item` faces `toward`. Prefers the horizontal
        (page-flow) sides: the vertical component has to dominate by more
        than `_HORIZONTAL_BIAS` before a top/bottom port is chosen. Not
        normalised by the item's half-extents -- doing that made a wide,
        short block "face" straight up/down and take links on its
        top/bottom edge instead of the side the flow arrives from."""
        c = _LinkItem._true_centre(item)
        dx, dy = toward.x() - c.x(), toward.y() - c.y()
        # A round junction node is where a fan of links meets; taking its
        # ports on the top or bottom for anything steeper than ~27 degrees
        # off horizontal sent one link of a fan into the top of the node
        # while the rest arrived on the left. It only uses a vertical port
        # for a link that is almost straight up or down.
        bias = (_LinkItem._NODE_HORIZONTAL_BIAS
                if isinstance(item, _NodeItem)
                else _LinkItem._HORIZONTAL_BIAS)
        if flow_direction() in ("TB", "BT"):
            # A vertical page flow prefers top/bottom ports; only a
            # clearly horizontal connection takes a side port.
            if abs(dy) >= bias * abs(dx):
                return "bottom" if dy >= 0 else "top"
            return "right" if dx >= 0 else "left"
        if abs(dx) >= bias * abs(dy):
            return "right" if dx >= 0 else "left"
        return "bottom" if dy >= 0 else "top"

    #: How far past a side boundary a routing point has to be dragged
    #: before the link end changes side (a band of +-8% of the slope).
    _SIDE_STICKY = 1.08

    @staticmethod
    def _sticky_side(item, toward: QPointF, previous: str | None) -> str:
        """`_side`, but keeping `previous` while `toward` is within a small
        band of the boundary between two sides."""
        side = _LinkItem._side(item, toward)
        if previous is None or previous == side:
            return side
        c = _LinkItem._true_centre(item)
        dx, dy = toward.x() - c.x(), toward.y() - c.y()
        k = _LinkItem._SIDE_STICKY
        for fx, fy in ((k, 1 / k), (1 / k, k)):
            if _LinkItem._side(item, QPointF(c.x() + dx * fx,
                                             c.y() + dy * fy)) == previous:
                return previous
        return side

    _OPPOSITE = {"left": "right", "right": "left",
                 "top": "bottom", "bottom": "top"}

    @staticmethod
    def _port_point(item, side: str) -> QPointF:
        """Scene point at the middle of `side` on the item's outline."""
        c = _LinkItem._true_centre(item)
        hw, hh = _LinkItem._half_extents(item)
        if side == "right":
            return QPointF(c.x() + hw, c.y())
        if side == "left":
            return QPointF(c.x() - hw, c.y())
        if side == "bottom":
            return QPointF(c.x(), c.y() + hh)
        return QPointF(c.x(), c.y() - hh)

    @staticmethod
    def _point_inside(item, p: QPointF) -> bool:
        """True if scene point `p` is strictly inside `item`'s drawn
        shape (a small inset, so a point on the outline doesn't count)."""
        if isinstance(item, _BlockItem):
            return item.rect().adjusted(3, 3, -3, -3) \
                .contains(item.mapFromScene(p))
        if isinstance(item, _NodeItem):
            d = p - item.scenePos()
            return (d.x() ** 2 + d.y() ** 2) ** 0.5 < item._r - 3
        if isinstance(item, _EventItem):
            polygon = item.polygon()
            scene_poly = QPolygonF([item.mapToScene(polygon.at(i))
                                    for i in range(polygon.count())])
            return scene_poly.containsPoint(p, Qt.FillRule.OddEvenFill)
        if isinstance(item, _HierItem):
            c = item.scenePos()
            a = _HierItem.W / 2.0 - 3
            b = _HierItem.H / 2.0 - 3
            return ((p.x() - c.x()) / a) ** 2 + \
                ((p.y() - c.y()) / b) ** 2 < 1.0
        return False

    @staticmethod
    def _segment_crosses(item, a: QPointF, b: QPointF) -> bool:
        """True if the segment a-b runs through `item`'s body. Sampled
        from just past each endpoint (a port sits on the boundary, so the
        crossing that matters happens immediately after it).

        Blocks and nodes -- nearly every link end -- take a fast path: the
        item's shape is read once and the 19 samples are tested with plain
        arithmetic. The general path maps every sample through Qt, which
        was most of the cost of routing a link. Same sample points, same
        result."""
        ax, ay = a.x(), a.y()
        dx, dy = b.x() - ax, b.y() - ay
        if isinstance(item, _BlockItem):
            r = item.mapRectToScene(item.rect().adjusted(3, 3, -3, -3))
            left, top, right, bottom = r.left(), r.top(), r.right(), r.bottom()
            for i in range(1, 20):
                t = i / 20.0
                x, y = ax + dx * t, ay + dy * t
                if left <= x <= right and top <= y <= bottom:
                    return True
            return False
        if isinstance(item, _NodeItem):
            lim = item._r - 3
            if lim <= 0:
                return False
            c = item.scenePos()
            cx, cy, lim2 = c.x(), c.y(), lim * lim
            for i in range(1, 20):
                t = i / 20.0
                ox, oy = ax + dx * t - cx, ay + dy * t - cy
                if ox * ox + oy * oy < lim2:
                    return True
            return False
        for i in range(1, 20):
            t = i / 20.0
            p = QPointF(ax + dx * t, ay + dy * t)
            if _LinkItem._point_inside(item, p):
                return True
        return False

    @staticmethod
    def _majority_side(counts: dict) -> str | None:
        """The most common side in `counts`; ties prefer the horizontal
        (page-flow) sides, left before right."""
        if not counts:
            return None
        order = {"left": 0, "right": 1, "top": 2, "bottom": 3}
        return min(counts, key=lambda s: (-counts[s], order[s]))

    @staticmethod
    def _flow_horizontal(item) -> bool:
        """Whether `item` sits in the page's left-to-right run: true if any
        of its neighbours is reached more horizontally than vertically.
        Such an element takes its ports on the left/right (enter left,
        exit right) even when some inputs arrive from below, which is what
        keeps a join/split visually balanced."""
        direction = flow_direction()
        if direction in ("LR", "RL"):
            return True
        if direction in ("TB", "BT"):
            return False
        # Auto: infer from the item's neighbours. The answer depends on the
        # item's LINKS, so a plain cache went stale whenever a link was
        # constructed after an earlier route (`_LinkItem.__init__` routes
        # immediately): the first link made the element look like a pure
        # vertical chain and every later route reused that answer. Key it
        # on the scene's link-generation counter, which is bumped on every
        # link construction, every link entering/leaving a scene, and a
        # flow-direction change.
        gen = _LINK_GEN[0]
        if getattr(item, "_route_horizontal_gen", None) == gen \
                and getattr(item, "_route_horizontal", None) is not None:
            return item._route_horizontal
        if item.scene() is None:
            return True
        c = _LinkItem._true_centre(item)
        incoming, outgoing = _links_at(item)
        for it in (*incoming, *outgoing):
            other = it._to if it._from is item else it._from
            oc = _LinkItem._true_centre(other)
            dx, dy = oc.x() - c.x(), oc.y() - c.y()
            if abs(dx) >= _LinkItem._HORIZONTAL_BIAS * abs(dy):
                item._route_horizontal = True
                item._route_horizontal_gen = gen
                return True
        result = _LinkItem._runs_across(item, c, incoming, outgoing)
        item._route_horizontal = result
        item._route_horizontal_gen = gen
        return result

    @staticmethod
    def _runs_across(item, c: QPointF, incoming, outgoing) -> bool:
        """Every source on one side of `item` and every target on the
        other (by x), each clearly off vertical: the flow runs across it
        even though the links are steep. A block fed from a node low on its
        left and feeding one low on its right is such an element -- both
        its links used to land on its bottom edge."""
        if not incoming or not outgoing:
            return False
        slope = _LinkItem._MONOTONE_MIN_SLOPE
        sides = set()
        for links, sign in ((incoming, -1), (outgoing, 1)):
            for it in links:
                other = it._from if links is incoming else it._to
                oc = _LinkItem._true_centre(other)
                dx, dy = oc.x() - c.x(), oc.y() - c.y()
                if abs(dx) < slope * abs(dy) or dx == 0:
                    return False
                sides.add(sign * (1 if dx > 0 else -1))
        return len(sides) == 1          # all +1 (L->R) or all -1 (R->L)

    @staticmethod
    def _exit_fallback_side(item, toward: QPointF) -> str:
        """The side to leave `item` from when the flow-direction exit would
        run the line through its own body: the side facing the target --
        unless that is the side the flow ENTERS on, in which case an element
        would take its input and its output on the same edge. Then use the
        perpendicular side facing the target (bottom or top)."""
        side = _LinkItem._side(item, toward)
        if _LinkItem._flow_horizontal(item):
            entry = _LinkItem._entry_side(item)
            if side == entry and side in ("left", "right"):
                c = _LinkItem._true_centre(item)
                return "bottom" if toward.y() >= c.y() else "top"
        elif flow_direction() in ("TB", "BT"):
            entry = _LinkItem._entry_side(item)
            if side == entry and side in ("top", "bottom"):
                c = _LinkItem._true_centre(item)
                return "right" if toward.x() >= c.x() else "left"
        return side

    @staticmethod
    def _flow_sign(item) -> int:
        """Which way the flow runs through `item` along the page: +1 for
        left-to-right, -1 for right-to-left.

        Decided by where its neighbours are: sources to the left and
        targets to the right (or the reverse) vote for their direction, in
        proportion to how far away they are. Everything used to assume
        left-to-right, so an element in a chain that runs the other way
        (after a Flip Horizontal, or a snaking layout) got its input on the
        wrong side and its line cut through its own body."""
        gen = _LINK_GEN[0]
        cached = getattr(item, "_route_flow_sign", None)
        if cached is not None and getattr(item, "_route_flow_sign_gen", None) == gen:
            return cached
        c = _LinkItem._true_centre(item)
        incoming, outgoing = _links_at(item)
        total = 0.0
        if flow_direction() in ("TB", "BT"):
            for it in incoming:
                total += c.y() - _LinkItem._true_centre(it._from).y()
            for it in outgoing:
                total += _LinkItem._true_centre(it._to).y() - c.y()
            # Scene y is down-positive, so TB is +1 and BT is -1.
            if flow_direction() == "BT":
                total = -total
        else:
            for it in incoming:
                total += c.x() - _LinkItem._true_centre(it._from).x()
            for it in outgoing:
                total += _LinkItem._true_centre(it._to).x() - c.x()
        result = -1 if total < 0 else 1
        item._route_flow_sign = result
        item._route_flow_sign_gen = gen
        return result

    @classmethod
    def _entry_side_for(cls, item, toward: QPointF) -> str:  # noqa: C901
        """The side an INCOMING link from `toward` should use.

        `_side` is the pure geometric answer; this adds the one rule that
        keeps the result readable: on an element that flows
        left-to-right, an incoming link may use `left` or `top` only.

        * `right` is a backwards arrow, and
        * `bottom` runs the line UP into the block, which fights the
          reading direction and can clip the body,

        so both fall back to `left`. `top` is deliberately NOT remapped: a
        link dropping in from directly above is an arrow pointing INTO the
        block, which reads cleanly and does not contradict a left-to-right
        flow. That is the case this exists for -- previously every input
        collapsed onto a single shared side, so a block fed from above and
        from the left drew both connections on its left edge.

        Single-sourced on purpose: the crossing-avoidance loop in
        `_update_route` recomputes the entry side when a line would cut
        through the body, and if that path used the raw `_side` it would
        silently undo this rule in exactly the case the rule exists for.
        """
        direction = flow_direction()
        if direction in ("TB", "BT"):
            # A fixed page flow is a constraint, not a hint: every element
            # enters on its upstream edge. Using the item-local sign here
            # made a band hand-over (target placed above its source) enter
            # from the bottom, which reads as a backwards arrow.
            return "top" if direction == "TB" else "bottom"
        if direction in ("LR", "RL"):
            return "left" if direction == "LR" else "right"
        side = cls._side(item, toward)
        if cls._flow_horizontal(item):
            if cls._flow_sign(item) > 0:
                if side in ("right", "bottom"):
                    return "left"
            elif side in ("left", "bottom"):     # right-to-left: mirror
                return "right"
        elif flow_direction() in ("TB", "BT"):
            if cls._flow_sign(item) > 0:         # top-to-bottom
                if side in ("bottom", "right"):
                    return "top"
            elif side in ("top", "left"):        # bottom-to-top: mirror
                return "bottom"
        return side

    @staticmethod
    def _entry_side(item):
        """The single side *all* of `item`'s incoming links enter on, or
        None if it has none. On a left-to-right element that's the left
        side; a vertical-chain element takes the majority side of its
        sources. One port for every input."""
        if item.scene() is None:
            return None
        gen = _LINK_GEN[0]
        cached = getattr(item, "_route_entry_side", None)
        if cached is not None and getattr(item, "_route_entry_side_gen", None) == gen:
            return cached
        counts: dict[str, int] = {}
        for it in _links_at(item)[0]:
            s = _LinkItem._side(item, _LinkItem._true_centre(it._from))
            counts[s] = counts.get(s, 0) + 1
        direction = flow_direction()
        if not counts:
            result = None
        elif direction in ("TB", "BT"):
            result = "top" if direction == "TB" else "bottom"
        elif direction in ("LR", "RL"):
            result = "left" if direction == "LR" else "right"
        elif _LinkItem._flow_horizontal(item):
            result = "left" if _LinkItem._flow_sign(item) > 0 else "right"
        elif flow_direction() in ("TB", "BT"):
            result = "top" if _LinkItem._flow_sign(item) > 0 else "bottom"
        else:
            result = _LinkItem._majority_side(counts)
        item._route_entry_side = result
        item._route_entry_side_gen = gen
        return result

    @staticmethod
    def _exit_side(item) -> str:
        """The single side *all* of `item`'s outgoing links leave from.

        With an explicit page flow this is the downstream edge (bottom for
        TB, right for LR, and their mirrors), so a wrapped band never
        reverses the arrow direction. In auto mode it follows the entry
        side, or the majority target side for a source with no inputs.
        """
        gen = _LINK_GEN[0]
        cached = getattr(item, "_route_exit_side", None)
        if cached is not None and getattr(item, "_route_exit_side_gen", None) == gen:
            return cached
        direction = flow_direction()
        if direction in ("TB", "BT"):
            result = "bottom" if direction == "TB" else "top"
        elif direction in ("LR", "RL"):
            result = "right" if direction == "LR" else "left"
        else:
            entry = _LinkItem._entry_side(item)
            if _LinkItem._flow_horizontal(item):
                result = "right" if _LinkItem._flow_sign(item) > 0 else "left"
            elif entry is not None:
                result = _LinkItem._OPPOSITE[entry]
            else:
                counts: dict[str, int] = {}
                for it in _links_at(item)[1]:
                    s = _LinkItem._side(item, _LinkItem._true_centre(it._to))
                    counts[s] = counts.get(s, 0) + 1
                result = _LinkItem._majority_side(counts) or "right"
        item._route_exit_side = result
        item._route_exit_side_gen = gen
        return result

    @staticmethod
    def _anchor_geom(item):
        """(rect, centre, radius) used by the Connect-click nearest-edge
        fallback and the dependency hover line. For a round node marker,
        `centre`/`radius` come directly from the item's own
        `scenePos()`/`_r`, not `sceneBoundingRect()` -- the capacity-gauge
        and dependency-badge boundingRect() overrides reserve asymmetric
        padding, which shifts sceneBoundingRect().center() off the true
        centre. Everything else (blocks/events/hiers) uses its own box
        rect/centre."""
        if isinstance(item, _NodeItem):
            centre = item.scenePos()
            r = item._r
            return (QRectF(centre.x() - r, centre.y() - r, r * 2, r * 2),
                   centre, r)
        rect = item.sceneBoundingRect()
        return rect, rect.center(), None

    def retheme(self, c: Mapping[str, Any]):
        self._live_active = False
        # Forget the state colour too: `set_live_fill` skips a colour it
        # thinks is already painted, so a stale one left the item plain
        # when the same colour came back (Weak Link off and on again).
        self._live_colour: str | None = None
        self.setPen(QPen(QColor(c["accent"] if self.isSelected()
                                else c["muted"]),
                         3 if self.isSelected() else 2.5))

    def set_live_fill(self, colour_hex: str, tooltip: str | None = None):
        """Solid state colour during the live simulation view -- a link
        has no fill (it's a stroke), only a pen, so "fill" here just
        means the line's own colour. See `_BlockItem.set_live_fill`'s
        own docstring; `DiagramView.retheme` restores the themed pen
        afterwards, this class included."""
        if getattr(self, "_live_colour", None) == QColor(colour_hex).name():
            if tooltip:
                self.setToolTip(tooltip)
            return
        self._live_colour = QColor(colour_hex).name()
        self.setPen(QPen(QColor(colour_hex), 3 if self.isSelected() else 2.5))
        self._live_active = True
        if tooltip:
            self.setToolTip(tooltip)

    def itemChange(self, change, value):
        if change in (QGraphicsItem.GraphicsItemChange.ItemSceneChange,
                      QGraphicsItem.GraphicsItemChange.ItemSceneHasChanged):
            _LINK_GEN[0] += 1          # the scene's link index is stale
        if change == QGraphicsItem.GraphicsItemChange.ItemSelectedHasChanged:
            # Repaint with the accent pen while selected. NB: geometry
            # must not change here -- only the pen. During the live
            # Simulation View the pen is the running state colour, so
            # don't snap it back to the themed one (same guard as
            # `_BlockItem.itemChange`).
            self.setZValue(5 if value else -1)
            if not getattr(self, "_live_active", False):
                self.retheme(COLOUR)
        return super().itemChange(change, value)

    def paint(self, painter: QPainter, option, widget=None):
        if self._insert_highlight:
            # A wide translucent halo under the normal stroke. It follows
            # the whole orthogonal/dog-leg path, not a straight endpoint
            # line, so the hover feedback matches what will actually split.
            colour = (COLOUR["accent"] if self._insert_highlight == "allowed"
                      else COLOUR["failed"])
            painter.save()
            pen = QPen(QColor(colour), max(8.0, self.pen().widthF() + 7.0))
            pen.setCapStyle(Qt.PenCapStyle.RoundCap)
            pen.setJoinStyle(Qt.PenJoinStyle.RoundJoin)
            painter.setPen(pen)
            painter.setOpacity(0.32)
            painter.setBrush(Qt.BrushStyle.NoBrush)
            painter.drawPath(self.path())
            painter.restore()
        pen = self.pen()
        painter.setPen(pen)
        painter.setBrush(QBrush())
        painter.drawPath(self.path())
        # The head is worked out when the route is (`_update_route`), not on
        # every repaint: a pan or a zoom repaints every link.
        # Filled, in the link's own colour: the shaft's no-fill brush was
        # still in force for the polygon, so the head came out as a bare
        # triangle outline -- hard to read at 2.5px against the canvas.
        # `NoPen` for the head so the stroke does not fatten it and eat
        # the point.
        painter.setBrush(QBrush(pen.color()))
        painter.setPen(Qt.PenStyle.NoPen)
        painter.drawPolygon(self._head)


class _RouteGripItem(QGraphicsEllipseItem):
    """A draggable dog-leg point on a link.

    Deliberately *not* a model node: it has no id in `NODE_DATA`, no
    properties dialog and no effect on EFPA, capacity or dependency
    resolution. It is stored only as a `.rbds` annotation so the drawing
    can round-trip; a legacy `.rbd` save drops it with the rest of the
    annotation layer.
    """

    #: Drawn radius in screen pixels. The item ignores the view zoom, so the
    #: handle stays this size however far a large model is zoomed out
    #: (scene units made it 2 px across at 25%).
    RADIUS = 5.0
    #: Hit-test radius, wider than the drawn dot so it is easy to grab.
    HIT_RADIUS = 9.0

    def __init__(self, link: _LinkItem, point: QPointF, *, entry_id: int,
                 active: bool = True):
        r = self.RADIUS
        super().__init__(-r, -r, 2 * r, 2 * r)
        self._link = link
        self._entry_id = int(entry_id)
        #: A freshly inserted point sits on the unchanged link until the
        #: user actually drags it. Saved points start active so reopening
        #: a drawing keeps the dog-leg.
        self._active = bool(active)
        self.setFlag(QGraphicsItem.GraphicsItemFlag.ItemIsMovable)
        self.setFlag(QGraphicsItem.GraphicsItemFlag.ItemIsSelectable)
        self.setFlag(QGraphicsItem.GraphicsItemFlag.ItemSendsGeometryChanges)
        self.setFlag(QGraphicsItem.GraphicsItemFlag.ItemIgnoresTransformations)
        self.setZValue(6)
        self.setCursor(Qt.CursorShape.SizeAllCursor)
        self.setToolTip("Drag to route this link")
        self.setBrush(QBrush(QColor(COLOUR["surface"])))
        self.setPen(QPen(QColor(COLOUR["accent"]), 1.5))
        # Construction places the item; that is not a user drag.
        self._initialising = True
        self.setPos(point)
        self._initialising = False
        if self.parentItem() is None:
            self.setParentItem(link)

    def shape(self):
        path = QPainterPath()
        h = self.HIT_RADIUS
        path.addEllipse(-h, -h, 2 * h, 2 * h)
        return path

    def boundingRect(self):
        h = self.HIT_RADIUS + 1.0
        return QRectF(-h, -h, 2 * h, 2 * h)

    def itemChange(self, change, value):
        if (change == QGraphicsItem.GraphicsItemChange.ItemPositionChange
                and not getattr(self, "_initialising", False)):
            snap = _snap_of(self)
            return QPointF(_snapped_x(value.x(), snap),
                           _snapped_y(value.y(), snap))
        if (change == QGraphicsItem.GraphicsItemChange.ItemPositionHasChanged
                and not getattr(self, "_initialising", False)):
            link = getattr(self, "_link", None)
            if link is not None and isValid(link):
                if not self._active:
                    self._active = True
                link._update_route()
                scene = link.scene()
                view = scene.views()[0] if scene is not None and scene.views() else None
                if view is not None and view._rbd_ref is not None:
                    for entry in getattr(view._rbd_ref, "link_grips", []):
                        if int(entry.get("id", 0)) == self._entry_id:
                            entry["x"] = round(self.pos().x(), 2)
                            entry["y"] = round(self.pos().y(), 2)
                            entry["active"] = True
                            break
                    view._user_arranged = True
                if view is not None and hasattr(view, "_layout_generation"):
                    # The link's route changed: the minimap draws routes.
                    view._layout_generation += 1
        return super().itemChange(change, value)
