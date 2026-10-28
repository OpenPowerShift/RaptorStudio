"""Ports follow the direction the flow actually runs, not always left-to-right.

An element in a chain that runs right-to-left (after Flip Horizontal, or a
snaking layout) used to take its input on the left and its output on the
right regardless, so a link from the right cut through the block and, when
that fallback fired, the output ended up on the same edge as the input.
"""
from __future__ import annotations

import pytest
from PySide6.QtWidgets import QApplication, QGraphicsScene

from raptor.gui.ui.diagram import _BlockItem, _LinkItem, _NodeItem

pytestmark = pytest.mark.gui


@pytest.fixture
def qapp():
    return QApplication.instance() or QApplication([])


def _side(item, p):
    c = _LinkItem._true_centre(item)
    hw, hh = _LinkItem._half_extents(item)
    dx, dy = p.x() - c.x(), p.y() - c.y()
    if abs(abs(dx) - hw) < 1.5:
        return "right" if dx > 0 else "left"
    if abs(abs(dy) - hh) < 1.5:
        return "bottom" if dy > 0 else "top"
    return "?"


def _chain(source_x, target):
    """source (block) -> element (block at the origin) -> target (node)."""
    sc = QGraphicsScene()
    src = _BlockItem(1, "src", source_x, 0)
    el = _BlockItem(2, "el", 0, 0)
    tgt = _NodeItem(9, "t", 2, *target)
    for i in (src, el, tgt):
        sc.addItem(i)
    into, out = _LinkItem(src, el), _LinkItem(el, tgt)
    for link in (into, out):
        sc.addItem(link)
    for _ in range(2):
        into._update_route()
        out._update_route()
    return el, into, out, sc


def test_left_to_right_is_unchanged(qapp):
    el, into, out, _sc = _chain(-300, (300, 0))
    assert _side(el, into._p3) == "left"
    assert _side(el, out._p0) == "right"


def test_right_to_left_enters_on_the_right_and_leaves_on_the_left(qapp):
    el, into, out, _sc = _chain(300, (-300, 0))
    assert _LinkItem._flow_sign(el) == -1
    assert _side(el, into._p3) == "right"
    assert _side(el, out._p0) == "left"


def test_a_turn_back_never_uses_the_same_edge_for_input_and_output(qapp):
    """The reported case: fed from the right, output down and slightly to
    the right. Leaving on the left would run the line through the block, so
    the output goes out the bottom -- not back out the input's edge."""
    el, into, out, _sc = _chain(300, (110, 185))
    assert _side(el, into._p3) == "right"
    assert _side(el, out._p0) == "bottom"


def test_a_turn_back_upwards_leaves_from_the_top(qapp):
    el, into, out, _sc = _chain(300, (110, -185))
    assert _side(el, into._p3) == "right"
    assert _side(el, out._p0) == "top"


@pytest.mark.parametrize("target", [(110, 185), (110, -185), (-110, 185),
                                    (-300, 0), (0, 185)])
def test_no_link_cuts_through_the_element_it_leaves_or_enters(qapp, target):
    el, into, out, _sc = _chain(300, target)
    assert not _LinkItem._segment_crosses(el, into._p0, into._p3)
    assert not _LinkItem._segment_crosses(el, out._p0, out._p3)


# -- a fan into a join node, with one steep source ------------------------

def _fan(top_y):
    """Spines at x=0 feed a join node; the top one is `top_y` above it.
    The node also feeds two uplinks to its right (the reported diagram)."""
    sc = QGraphicsScene()
    join = _NodeItem(50, "join", 2, 400, 0)
    sc.addItem(join)
    spines, links = [], []
    for i, y in enumerate((top_y, -300, 0, 300)):
        b = _BlockItem(10 + i, f"spine{i}", -300, y - 23)
        sc.addItem(b)
        spines.append(b)
        links.append(_LinkItem(b, join))
    outs = []
    for i, y in enumerate((-150, 150)):
        u = _BlockItem(20 + i, f"up{i}", 800, y - 23)
        sc.addItem(u)
        outs.append(_LinkItem(join, u))
    for link in (*links, *outs):
        sc.addItem(link)
    for _ in range(2):
        for link in (*links, *outs):
            link._update_route()
    return join, links, outs, sc


@pytest.mark.parametrize("top_y", [-300, -600, -900, -1200])
def test_every_input_of_a_join_node_arrives_on_its_left(qapp, top_y):
    """One source is far above the others -- steeper than 27 degrees off
    horizontal -- and used to go into the TOP of the node while its
    siblings arrived on the left."""
    join, links, outs, _sc = _fan(top_y)
    assert [_side(join, l._p3) for l in links] == ["left"] * 4
    assert [_side(join, l._p0) for l in outs] == ["right"] * 2


def test_a_source_directly_above_a_node_still_drops_in_from_the_top(qapp):
    """The exception that stays: a link that is (nearly) vertical is an
    arrow pointing into the node, which reads cleanly."""
    sc = QGraphicsScene()
    join = _NodeItem(50, "join", 2, 0, 0)
    above = _BlockItem(1, "above", -54, -600)
    left = _BlockItem(2, "left", -500, -23)
    for i in (join, above, left):
        sc.addItem(i)
    a, b = _LinkItem(above, join), _LinkItem(left, join)
    for link in (a, b):
        sc.addItem(link)
    for _ in range(2):
        a._update_route()
        b._update_route()
    assert _side(join, a._p3) == "top"
    assert _side(join, b._p3) == "left"


def test_a_block_across_a_fan_takes_input_left_and_output_right(qapp):
    """Fed from a node low on its left, feeding one low on its right: the
    flow runs across it, so neither link belongs on its bottom edge."""
    sc = QGraphicsScene()
    blk = _BlockItem(1, "spine7", 0, 0)
    src = _NodeItem(2, "tor", 2, -700, 900)
    dst = _NodeItem(3, "join", 2, 800, 900)
    for i in (blk, src, dst):
        sc.addItem(i)
    into, out = _LinkItem(src, blk), _LinkItem(blk, dst)
    for link in (into, out):
        sc.addItem(link)
    for _ in range(2):
        into._update_route()
        out._update_route()
    assert _side(blk, into._p3) == "left"
    assert _side(blk, out._p0) == "right"


def test_a_vertical_stack_with_a_little_jitter_stays_vertical(qapp):
    """The monotone rule must not turn an almost-straight vertical chain
    into a horizontal one because its blocks are a few pixels off."""
    sc = QGraphicsScene()
    mid = _BlockItem(1, "mid", 0, 0)
    up = _NodeItem(2, "up", 2, -6, -500)
    down = _NodeItem(3, "down", 2, 6, 500)
    for i in (mid, up, down):
        sc.addItem(i)
    into, out = _LinkItem(up, mid), _LinkItem(mid, down)
    for link in (into, out):
        sc.addItem(link)
    for _ in range(2):
        into._update_route()
        out._update_route()
    assert _side(mid, into._p3) in ("top", "bottom")
    assert _side(mid, out._p0) in ("top", "bottom")
