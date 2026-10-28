"""Link endpoint geometry and Connect-tool click targeting.

Links attach at the midpoint of the side they approach from
(`_LinkItem._side` / `_port_point`), computed on each element's real
outline -- rectangle edge, circle cardinal point, hexagon vertex/edge
midpoint or ellipse extreme point -- and are drawn as a straight segment
between the two ports. Ports follow the page flow: an element's incoming
links enter on the side facing their source, its outgoing links leave on
the opposite side. `_anchor_geom` still supplies the box centre/radius
the Connect-click fallback and dependency hover line use.

`_NodeItem.boundingRect()` reserves padding for the capacity-gauge and
dependency-badge unconditionally, extending only left and down from the
node's true circle. That shifts `sceneBoundingRect().center()` off the real
centre, so node geometry measures from `scenePos()` and `_r` rather than
the padded rect. `_BlockItem` is instead top-left anchored, so block
hit-testing measures from its rect.
"""
from __future__ import annotations

import itertools
import os
import pathlib

import pytest
from PySide6.QtCore import QPointF
from PySide6.QtWidgets import QApplication, QGraphicsScene

from raptor.core.fileio.rbd import load_rbd
from raptor.gui.ui import diagram as _diag
from raptor.gui.ui.diagram import (
    DiagramView, _LinkItem, _NodeItem, _BlockItem, _EventItem, _HierItem,
    BLOCK_W,
)

REPO = pathlib.Path(__file__).resolve().parents[2]
GOLD = os.path.join(REPO, "tests", "golden")
FIXTURE = os.path.join(GOLD, "TwoBlockRedundancy", "input.rbd")


def _scene():
    """A fresh scene with grid snapping off, so hand-placed test items keep
    the exact coordinates they are built with (a previous DiagramView in
    the same process leaves `_SNAP_STATE` non-zero, which would snap them
    elsewhere)."""
    _diag._SNAP_STATE["x"] = _diag._SNAP_STATE["y"] = 0.0
    return QGraphicsScene()


@pytest.fixture(scope="module")
def qapp():
    return QApplication.instance() or QApplication([])


def test_node_bounding_rect_is_padded_off_true_centre(qapp):
    """A node's `sceneBoundingRect().center()` is not its true centre when
    badge padding is present."""
    v = DiagramView()
    v.load_rbd(load_rbd(FIXTURE))
    node = next(iter(v._node_items.values()))
    true_centre = node.scenePos()
    padded_centre = node.sceneBoundingRect().center()
    assert (padded_centre - true_centre).manhattanLength() > 1, \
        "if this ever becomes 0, the badge-padding bug this file " \
        "guards against no longer exists and these tests can simplify"


def test_anchor_geom_returns_true_node_centre_and_radius(qapp):
    v = DiagramView()
    v.load_rbd(load_rbd(FIXTURE))
    node = next(iter(v._node_items.values()))
    rect, centre, radius = _LinkItem._anchor_geom(node)
    assert centre == node.scenePos()
    assert radius == node._r
    assert rect.center() == node.scenePos()
    assert rect.width() == pytest.approx(node._r * 2)
    assert rect.height() == pytest.approx(node._r * 2)


def test_anchor_geom_uses_scene_bounding_rect_for_non_nodes(qapp):
    v = DiagramView()
    v.load_rbd(load_rbd(FIXTURE))
    block = next(iter(v._block_items.values()))
    rect, centre, radius = _LinkItem._anchor_geom(block)
    assert radius is None
    assert rect == block.sceneBoundingRect()
    assert centre == block.sceneBoundingRect().center()


def test_link_endpoint_touches_the_true_node_circle(qapp):
    """A line's endpoint at a node sits on the node's real circle
    (distance from the true centre equals the radius), not on the
    badge-padded bounding rect."""
    v = DiagramView()
    v.load_rbd(load_rbd(FIXTURE))
    node_link = next(
        L for L in v._links
        if isinstance(L._from, _NodeItem) or isinstance(L._to, _NodeItem))
    node_item = (node_link._from if isinstance(node_link._from, _NodeItem)
                else node_link._to)
    endpoint = (QPointF(node_link.line().x1(), node_link.line().y1())
               if node_item is node_link._from
               else QPointF(node_link.line().x2(), node_link.line().y2()))
    true_centre = node_item.scenePos()
    dist = ((endpoint.x() - true_centre.x()) ** 2
           + (endpoint.y() - true_centre.y()) ** 2) ** 0.5
    assert dist == pytest.approx(node_item._r, abs=0.5)


def test_kofn_badge_ignores_same_id_collision_with_a_block(qapp):
    """A K-of-N node's `1/2`-style badge counts only links whose target
    is actually a NODE (`connect_t_ref=="RBDNode"`). Block/node/event ids
    are each numbered independently in the file format, so a Block can
    share a node's raw numeric id and the badge must not count an
    unrelated link into `Block2` as a third incoming path to `Join`. The
    builder now pools *auto-assigned* ids across kinds, so the collision
    is built explicitly here (`Join` and `Block2` are both id 2)."""
    from raptor.api.build import build_from_spec, parallel

    spec = parallel(2)
    spec["blocks"][1]["id"] = 2      # Block2
    spec["nodes"][2]["id"] = 2       # Join (after Start and Split)
    rbd = build_from_spec(spec)
    v = DiagramView()
    v.load_rbd(rbd)
    join = next(n for n in v.nodes.values() if n.node_name == "Join")
    block2 = next(b for b in v.blocks.values() if b.name == "Block2")
    assert join.id == block2.id == 2, \
        "fixture assumption broke -- this test needs the id collision to fire"
    item = v._node_items[join.id]
    assert item._inner_label.toPlainText() == "1/2"


def test_element_at_fallback_finds_a_near_miss_block_by_its_edge(qapp):
    """A click a few px outside a block's edge still resolves to that
    block, measured from the block's actual rect rather than its
    top-left `scenePos()`. `_BlockItem`'s local rect is (0,0,w,h),
    anchored at the top-left, unlike a node's centre-anchored
    `scenePos()`."""
    v = DiagramView()
    v.load_rbd(load_rbd(FIXTURE))
    block = next(iter(v._block_items.values()))
    r = block.sceneBoundingRect()
    near_miss = QPointF(r.right() + 5, r.center().y())
    assert v._element_at(near_miss) is block


def test_element_at_fallback_does_not_reach_225px(qapp):
    """A click 30px from a block does not resolve to it: the fallback
    threshold is 15px, and TwoBlockRedundancy has no other element
    within 15px of that point."""
    v = DiagramView()
    v.load_rbd(load_rbd(FIXTURE))
    block = next(iter(v._block_items.values()))
    r = block.sceneBoundingRect()
    far_miss = QPointF(r.right() + 30, r.center().y())
    assert v._element_at(far_miss) is not block


def test_connect_click_near_a_block_does_not_skip_to_a_farther_node(qapp):
    """Arming Connect from Start and then clicking near the block between
    Start and the join node links to that block, rather than skipping
    past it to the far node."""
    v = DiagramView()
    v.load_rbd(load_rbd(FIXTURE))
    start = next(n for n in v._node_items.values() if n._node_id == 1)
    block = next(iter(v._block_items.values()))
    v.tool_mode = "connect"
    v._handle_tool_click(start.scenePos())
    assert v._connect_from == ("Node", 1)

    r = block.sceneBoundingRect()
    near_block = QPointF(r.right() + 5, r.center().y())
    found = v._element_at(near_block)
    assert found is block, \
        f"expected the block near the click, got {found!r}"


def test_connect_ghost_line_starts_at_the_true_node_centre(qapp):
    """The Connect rubber-band preview starts at the node's real centre,
    not the badge-padded `sceneBoundingRect().center()`."""
    v = DiagramView()
    v.load_rbd(load_rbd(FIXTURE))
    start = next(n for n in v._node_items.values() if n._node_id == 1)
    v.tool_mode = "connect"
    v._handle_tool_click(start.scenePos())
    assert v._connect_from == ("Node", 1)

    from PySide6.QtGui import QMouseEvent
    from PySide6.QtCore import Qt as _Qt
    target = QPointF(400, 200)
    viewport_pt = QPointF(v.mapFromScene(target))
    ev = QMouseEvent(QMouseEvent.MouseMove, viewport_pt, viewport_pt,
                     _Qt.NoButton, _Qt.NoButton, _Qt.NoModifier)
    v.mouseMoveEvent(ev)
    assert v._connect_ghost is not None
    line = v._connect_ghost.line()
    assert QPointF(line.x1(), line.y1()) == start.scenePos()


def test_new_model_links_connect_node_to_block_not_node_to_node(qapp):
    """The New Model template wires Start to a default block and that
    block to End, rather than wiring Start straight to End.

    `_resolve_link_endpoint` dispatches on the link's own
    `connect_f_ref`/`connect_t_ref` (RBDBlock vs RBDNode) instead of a
    kind-blind bare-id lookup, so an id shared between a node and a
    block resolves correctly.
"""
    from raptor.gui.new_model import build_new_model

    v = DiagramView()
    v.load_rbd(build_new_model("Test"))

    block = next(iter(v._block_items.values()))
    start = next(n for n in v._node_items.values() if n._node_id == 1)
    end = next(n for n in v._node_items.values() if n._node_id == 2)

    endpoints = {(li._from, li._to) for li in v._links}
    assert (start, block) in endpoints
    assert (block, end) in endpoints
    assert (start, end) not in endpoints


# ---------------------------------------------------------------- ports


def _event_vertices(ev):
    poly = ev.polygon()
    return [ev.mapToScene(poly.at(i)) for i in range(poly.count())]


def _dist_to_polygon(p, verts):
    best = float("inf")
    n = len(verts)
    for i in range(n):
        a, b = verts[i], verts[(i + 1) % n]
        ex, ey = b.x() - a.x(), b.y() - a.y()
        seg = ex * ex + ey * ey
        t = 0.0 if seg == 0 else max(0.0, min(1.0,
            ((p.x() - a.x()) * ex + (p.y() - a.y()) * ey) / seg))
        qx, qy = a.x() + t * ex, a.y() + t * ey
        best = min(best, ((p.x() - qx) ** 2 + (p.y() - qy) ** 2) ** 0.5)
    return best


def test_event_link_endpoint_sits_on_the_hexagon_outline(qapp):
    """A link into an event touches the drawn hexagon. The old
    ray/bounding-box intersection left the endpoint up to ~11px clear of
    the hexagon at diagonal approaches."""
    scene = _scene()
    ev = _EventItem(1, "E", 0, 0)
    block = _BlockItem(1, "B", 200, 130)   # diagonal approach
    scene.addItem(ev)
    scene.addItem(block)
    link = _LinkItem(block, ev)
    scene.addItem(link)

    assert _dist_to_polygon(link._p3, _event_vertices(ev)) \
        == pytest.approx(0, abs=0.5)


def test_hier_link_endpoint_sits_on_the_ellipse_outline(qapp):
    """A link into a hierarchy oval touches the drawn ellipse (the old
    box-exit could sit ~22px clear of it on shallow approaches)."""
    scene = _scene()
    h = _HierItem(1, "H", 0, 0)
    node = _NodeItem(1, "n", 2, 40, 220)   # mostly below the oval
    scene.addItem(h)
    scene.addItem(node)
    link = _LinkItem(node, h)
    scene.addItem(link)

    a, b = _HierItem.W / 2.0, _HierItem.H / 2.0
    p = link._p3
    assert (p.x() / a) ** 2 + (p.y() / b) ** 2 == pytest.approx(1, abs=1e-3)


def test_port_is_the_middle_of_the_approached_side(qapp):
    """Approaching a block from the left attaches at the middle of its
    left side, not wherever the centre-to-centre ray happened to exit."""
    scene = _scene()
    block = _BlockItem(1, "B", 0, 0)
    src = _NodeItem(1, "n", 2, -300, 0)
    scene.addItem(block)
    scene.addItem(src)
    link = _LinkItem(src, block)
    scene.addItem(link)

    c = block.mapToScene(block.rect().center())
    assert link._p3.x() == pytest.approx(c.x() - BLOCK_W / 2)
    assert link._p3.y() == pytest.approx(c.y())
    # the chord exposes exactly the two ports
    assert link.line().p1() == link._p0
    assert link.line().p2() == link._p3


def test_link_is_a_straight_line_between_ports(qapp):
    """A link is a plain straight segment between the two ports -- no
    stubs or bends."""
    scene = _scene()
    block = _BlockItem(1, "B", 0, 0)
    node = _NodeItem(2, "n", 2, 260, 0)
    scene.addItem(block)
    scene.addItem(node)
    link = _LinkItem(block, node)
    scene.addItem(link)

    p0, p3 = link._p0, link._p3
    assert link.line().p1() == p0
    assert link.line().p2() == p3
    # the drawn path is the single straight segment p0 -> p3
    assert link.path().elementCount() == 2


def test_flow_through_ports_are_on_opposite_sides(qapp):
    """An element's incoming and outgoing ports sit on opposite sides in a
    normal straight-through layout: fed from the left, it sends out the
    right."""
    scene = _scene()
    src = _NodeItem(1, "s", 2, -220, 0)    # left of the node
    node = _NodeItem(2, "n", 2, 0, 0)
    dst = _NodeItem(3, "d", 2, 240, 0)     # right of the node
    for it in (src, node, dst):
        scene.addItem(it)
    ln_in = _LinkItem(src, node)
    ln_out = _LinkItem(node, dst)
    scene.addItem(ln_in)
    scene.addItem(ln_out)
    ln_in._update_route()
    ln_out._update_route()

    c, r = node.scenePos(), node._r
    assert ln_in._p3 == QPointF(c.x() - r, c.y())      # enters left
    assert ln_out._p0 == QPointF(c.x() + r, c.y())     # exits right


def test_line_never_cuts_through_its_own_element(qapp):
    """An element's line must not run back across its own body. Here a
    join fed from below and sending to the right takes left/right flow
    ports; the incoming lines reach the left port without crossing."""
    scene = _scene()
    src = _NodeItem(1, "s", 2, 0, 220)     # below the node
    node = _NodeItem(2, "n", 2, 0, 0)
    dst = _NodeItem(3, "d", 2, 120, 220)   # below-right
    for it in (src, node, dst):
        scene.addItem(it)
    ln_in = _LinkItem(src, node)
    ln_out = _LinkItem(node, dst)
    scene.addItem(ln_in)
    scene.addItem(ln_out)
    ln_in._update_route()
    ln_out._update_route()

    c, r = node.scenePos(), node._r
    # horizontal flow (the node also has a right-hand neighbour)
    assert ln_in._p3 == QPointF(c.x() - r, c.y())      # enters left
    assert not _LinkItem._segment_crosses(node, ln_in._p0, ln_in._p3)
    assert not _LinkItem._segment_crosses(node, ln_out._p0, ln_out._p3), \
        "no link may run through the node's body"


def test_vertical_chain_uses_top_bottom(qapp):
    """An element whose neighbours are all directly above/below (no
    horizontal run) flows top-to-bottom: enter top, exit bottom."""
    scene = _scene()
    src = _NodeItem(1, "s", 2, 0, -220)    # above
    node = _NodeItem(2, "n", 2, 0, 0)
    dst = _NodeItem(3, "d", 2, 0, 220)     # below
    for it in (src, node, dst):
        scene.addItem(it)
    ln_in = _LinkItem(src, node)
    ln_out = _LinkItem(node, dst)
    scene.addItem(ln_in)
    scene.addItem(ln_out)
    ln_in._update_route()
    ln_out._update_route()

    c, r = node.scenePos(), node._r
    assert ln_in._p3 == QPointF(c.x(), c.y() - r)      # enters top
    assert ln_out._p0 == QPointF(c.x(), c.y() + r)     # exits bottom


def test_incoming_left_exits_right_even_for_a_lower_target(qapp):
    """Flow passes through: a node fed from the left sends out the right,
    rather than doubling back to the bottom where its target sits."""
    scene = _scene()
    src = _NodeItem(1, "s", 2, -220, 0)    # left of the node
    node = _NodeItem(2, "n", 2, 0, 0)
    dst = _NodeItem(3, "d", 2, 120, 220)   # down-right of the node
    for it in (src, node, dst):
        scene.addItem(it)
    ln_in = _LinkItem(src, node)
    ln_out = _LinkItem(node, dst)
    scene.addItem(ln_in)
    scene.addItem(ln_out)
    ln_in._update_route()
    ln_out._update_route()

    c, r = node.scenePos(), node._r
    assert ln_in._p3 == QPointF(c.x() - r, c.y())      # enters left
    assert ln_out._p0 == QPointF(c.x() + r, c.y()), \
        "incoming from the left must leave to the right"


def test_all_incoming_share_one_entry_port(qapp):
    """Fan-in from different sides all attaches to the element's single
    entry port (the majority side), not a port per link."""
    scene = _scene()
    node = _NodeItem(1, "n", 2, 0, 0)
    a = _NodeItem(2, "a", 2, -240, 0)      # directly left
    b = _NodeItem(3, "b", 2, -200, 120)    # lower-left (still left-dominant)
    for it in (node, a, b):
        scene.addItem(it)
    l1 = _LinkItem(a, node)
    l2 = _LinkItem(b, node)
    scene.addItem(l1)
    scene.addItem(l2)
    l1._update_route()
    l2._update_route()

    c, r = node.scenePos(), node._r
    assert l1._p3 == QPointF(c.x() - r, c.y())
    assert l2._p3 == QPointF(c.x() - r, c.y())


def test_all_outgoing_share_one_exit_port(qapp):
    """Fan-out to different targets all leaves the element's single exit
    port (opposite its entry)."""
    scene = _scene()
    src = _NodeItem(1, "s", 2, -240, 0)    # feeds the node from the left
    node = _NodeItem(2, "n", 2, 0, 0)
    t1 = _NodeItem(3, "t1", 2, 240, 0)     # directly right
    t2 = _NodeItem(4, "t2", 2, 200, 120)   # lower-right
    for it in (src, node, t1, t2):
        scene.addItem(it)
    ln_in = _LinkItem(src, node)
    l1 = _LinkItem(node, t1)
    l2 = _LinkItem(node, t2)
    for li in (ln_in, l1, l2):
        scene.addItem(li)
    for li in (ln_in, l1, l2):
        li._update_route()

    c, r = node.scenePos(), node._r
    assert l1._p0 == QPointF(c.x() + r, c.y())
    assert l2._p0 == QPointF(c.x() + r, c.y())



class TestPerLinkEntrySide:
    """An element fed from more than one direction gets a port per
    direction.

    The reported defect: a block with one input from directly above and
    one from the left drew BOTH connections on its left edge, because
    `_entry_side` collapsed every input onto a single side and forced
    `left` for anything in a left-to-right run. The from-above link then
    had to run round the body -- ugly, and exactly what the crossing
    avoidance in `_update_route` exists to paper over.
    """

    @staticmethod
    def _fed_from(qapp, sources):
        """A node at the origin with one link from each (dx, dy) source.

        Returns `(node, links, scene)`. The SCENE is the important part:
        an unparented `QGraphicsScene` that goes out of scope destroys the
        items it owns, and every wrapper then raises on use -- which
        presents as a confusing "already deleted" deep inside
        `_true_centre` rather than as a missing fixture.
        """
        scene = _scene()
        node = _NodeItem(2, "n", 2, 0, 0)
        scene.addItem(node)
        links = []
        for i, (dx, dy) in enumerate(sources):
            src = _NodeItem(10 + i, f"s{i}", 2, dx, dy)
            scene.addItem(src)
            ln = _LinkItem(src, node)
            scene.addItem(ln)
            links.append(ln)
        for ln in links:
            ln._update_route()
        return node, links, scene

    def _side_of(self, node, link):
        return _LinkItem._side(node, _LinkItem._true_centre(link._from))

    def test_above_and_left_take_different_sides(self, qapp):
        node, (from_above, from_left), _scene = self._fed_from(
            qapp, [(0, -200), (-200, 0)])
        assert self._side_of(node, from_above) == "top"
        assert self._side_of(node, from_left) == "left"
        # The ports are genuinely on different edges, not just labelled.
        c, r = node.scenePos(), node._r
        assert from_above._p3 == QPointF(c.x(), c.y() - r)
        assert from_left._p3 == QPointF(c.x() - r, c.y())

    def test_below_and_right_do_not_fight_the_reading_direction(self, qapp):
        """The two sides a left-to-right element must NOT be entered on.

        `right` is a backwards arrow and `bottom` runs the line UP into the
        block, clipping the body; both fall back to `left`. This is the
        constraint `test_line_never_cuts_through_its_own_element` guards,
        and the change had to keep it.
        """
        node, (from_below, from_right), _scene = self._fed_from(
            qapp, [(0, 200), (200, 0)])
        # Give the node a right-hand neighbour so it reads left-to-right.
        nxt = _NodeItem(30, "x", 2, 200, 0)
        node.scene().addItem(nxt)
        out = _LinkItem(node, nxt)
        node.scene().addItem(out)
        out._update_route()
        for ln in (from_below, from_right):
            ln._update_route()
        # Asserted on the PORT, not on `_side`: the raw geometric answer
        # is "bottom"/"right" and the remap to "left" happens in
        # `_update_route`. The port is what the user actually sees.
        c, r = node.scenePos(), node._r
        left_port = QPointF(c.x() - r, c.y())
        assert from_below._p3 == left_port
        assert from_right._p3 == left_port
        # `top` stays reachable, which is the point of the change.
        assert from_below._p3 != QPointF(c.x(), c.y() + r)

    def test_neither_link_cuts_through_the_body(self, qapp):
        node, links, _scene = self._fed_from(qapp, [(0, -200), (-200, 0)])
        for ln in links:
            assert not _LinkItem._segment_crosses(node, ln._p0, ln._p3)

    def test_a_pure_left_to_right_chain_is_unchanged(self, qapp):
        """The common case must not move: everything still enters left."""
        node, (first, second), _scene = self._fed_from(
            qapp, [(-200, 0), (-400, 0)])
        assert self._side_of(node, first) == "left"
        assert self._side_of(node, second) == "left"

    def test_a_vertical_chain_still_uses_top_and_bottom(self, qapp):
        """An element with no horizontal neighbour keeps flowing
        vertically -- the change only relaxes the horizontal case."""
        node, (from_above, from_below), _scene = self._fed_from(
            qapp, [(0, -200), (0, 200)])
        assert self._side_of(node, from_above) == "top"
        assert self._side_of(node, from_below) == "bottom"


class TestAutoLayoutSpacing:
    """Auto Layout must produce a diagram you can actually read.

    Reported as "it doesn't space adequately even though it gives a
    small separation" on a model whose blocks all share one coordinate.

    The cause is an interaction between two independently-reasonable
    decisions. The view transform is FROZEN at load, so that nudging one
    element cannot rescale the whole canvas and shift every untouched
    one on reopen. A model whose positions are all identical derives a
    closest-spacing of 0, so the scale falls to its floor of 8.0 and
    freezes there. Auto Layout then correctly spaces blocks
    `LAYER_PITCH` (2.5) model units apart -- but at scale 8.0 that
    renders as 20 scene px between 108 px blocks, so every block
    overlaps its neighbour.

    Auto Layout replaces the whole geometry, which is precisely the case
    the freeze does not cover, so it now re-derives the transform.
    """

    @staticmethod
    def _degenerate_model():
        """A model with real topology whose blocks all share a position --
        the shape of a legacy file that never had coordinates written.

        Built on a 16-block model so the layout has several columns to
        space, and with the frozen transform cleared as well: a file
        saved by this app carries a transform, and reusing it would hide
        the degenerate-derivation failure the test is about.

        EVERY positioned row has to be zeroed, not just the blocks. The
        derivation takes the closest spacing across all of them, so a
        single node left at a real coordinate supplies a dx_min and the
        model derives an ordinary scale -- the degenerate case this
        reproduces (`legacy/rbd/Truck2.rbd`: all 27 blocks at (0,0), all
        11 nodes at (0, 91.25)) would never occur.
        """
        rbd = load_rbd(os.path.join(GOLD, "WeakAnalysisProbe", "input.rbd"))
        rbd.view_transform = None
        for name in ("BLOCK_DATA", "NODE_DATA", "EVENT_DATA",
                     "HIERARCHY_DATA"):
            for row in rbd.sections[name].rows:
                if int(float(row.get("ID", 0))) == 0:
                    continue
                row["xPos"] = "0.000000"
                row["yPos"] = "0.000000"
        return rbd

    def test_scale_is_re_derived_not_left_frozen(self, qapp):
        v = DiagramView()
        v.load_rbd(self._degenerate_model())
        degenerate_scale = v._scale
        v.auto_layout()
        assert v._scale > degenerate_scale, (
            f"a full re-layout must not keep the scale derived from "
            f"degenerate input ({v._scale} vs {degenerate_scale})")

    def test_no_two_blocks_overlap(self, qapp):
        from raptor.gui.ui.diagram import BLOCK_W, BLOCK_H
        v = DiagramView()
        v.load_rbd(self._degenerate_model())
        v.auto_layout()
        boxes = [(i.scenePos().x(), i.scenePos().y())
                 for i in v._block_items.values()]
        for (ax, ay), (bx, by) in itertools.pairwise(boxes):
            dx, dy = abs(ax - bx), abs(ay - by)
            assert not (dx < BLOCK_W and dy < BLOCK_H), (
                f"blocks at ({ax:.0f},{ay:.0f}) and ({bx:.0f},{by:.0f}) "
                f"overlap: separation {dx:.0f}x{dy:.0f} vs "
                f"{BLOCK_W}x{BLOCK_H} needed")

    def test_column_gap_is_readable(self, qapp):
        from raptor.gui.ui.diagram import BLOCK_W
        v = DiagramView()
        v.load_rbd(self._degenerate_model())
        v.auto_layout()
        xs = sorted({round(i.scenePos().x(), 1)
                     for i in v._block_items.values()})
        assert len(xs) > 1, "layout produced a single column"
        gap = min(b - a for a, b in itertools.pairwise(xs))
        assert gap >= BLOCK_W, (
            f"closest columns are {gap:.0f}px apart; blocks are "
            f"{BLOCK_W}px wide, so they touch or overlap")

    def test_a_healthy_model_keeps_its_scale(self, qapp):
        """The freeze must still hold where it is meant to: re-deriving
        must converge, not ratchet the scale on every layout."""
        v = DiagramView()
        v.load_rbd(self._degenerate_model())
        v.auto_layout()
        first = v._scale
        v.auto_layout()
        assert v._scale == pytest.approx(first), \
            "re-deriving must converge, not ratchet the scale each layout"


def test_a_long_name_widens_the_block_about_its_centre(qapp):
    """A name too long for BLOCK_W widens the block equally either side,
    so its centre (the stored position) does not move, and a link's port
    sits on the new edge. Wider than BLOCK_MAX_W, the name is cut short
    with an ellipsis and shown whole on hover."""
    from raptor.gui.ui.diagram_items import BLOCK_MAX_W
    scene = _scene()
    a = _BlockItem(1, "A", 0, 0)
    b = _BlockItem(2, "B", 400, 0)
    for it in (a, b):
        scene.addItem(it)
    link = _LinkItem(a, b)
    scene.addItem(link)
    centre = _LinkItem._true_centre(b)
    assert b.rect().width() == BLOCK_W
    b.set_name("Auto_Transfer_Switch")
    link._update_route()
    assert b.rect().width() > BLOCK_W
    assert _LinkItem._true_centre(b) == centre
    port = _LinkItem._port_point(b, "left")
    assert port.x() == pytest.approx(b.mapToScene(b.rect().topLeft()).x())
    b.set_name("W" * 20)
    assert b.rect().width() <= BLOCK_MAX_W
    assert b._label.toPlainText().endswith("…")
    assert b._label.toolTip() == "W" * 20
    b.set_name("B")
    assert b.rect().width() == BLOCK_W and b._label.toolTip() == ""
