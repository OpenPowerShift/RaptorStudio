"""Link routing styles: the Qt-free router, the SVG renderer and the canvas."""
from __future__ import annotations

import itertools

import pytest
from PySide6.QtWidgets import QApplication

from raptor.core import routing
from raptor.core.fileio.model_file import load_model_file
from raptor.render.svg import SvgRenderError, render_svg
import itertools


@pytest.fixture
def qapp():
    return QApplication.instance() or QApplication([])


MODEL = "tests/golden/Hierarchy1-7/input.rbd"


def _axis_aligned(pts):
    return all(abs(a[0] - b[0]) < 1e-6 or abs(a[1] - b[1]) < 1e-6
               for a, b in itertools.pairwise(pts))


def test_orthogonal_route_has_only_right_angles():
    pts = routing.Router().route((0, 0), "right", (200, 100), "left", [])
    assert pts is not None and _axis_aligned(pts)
    assert pts[0] == (0, 0) and pts[-1] == (200, 100)


def test_orthogonal_route_steers_round_an_obstacle():
    wall = (80, -50, 120, 150)          # straddles the direct Z lane
    pts = routing.Router().route((0, 0), "right", (200, 0), "left", [wall])
    assert pts is not None and _axis_aligned(pts)
    for a, b in itertools.pairwise(pts):
        assert not routing._hits(a, b, [wall])


def test_unroutable_link_falls_back_to_none():
    # an element sitting right on the exit stub: no candidate can leave the port
    wall = (8, -1000, 60, 1000)
    assert routing.Router().route((0, 0), "right", (200, 50), "left", [wall]) is None


def test_budget_exhaustion_falls_back():
    r = routing.Router(per_route_s=0.02, total_s=0.0)
    assert r.route((0, 0), "right", (200, 100), "left", []) is None
    assert r.fallbacks == 1


def test_angled_has_stubs_and_a_diagonal():
    pts = routing.angled((0, 0), "right", (200, 100), "left")
    assert pts is not None and len(pts) == 4 and not _axis_aligned(pts)


@pytest.mark.parametrize("mode", routing.ROUTING_MODES)
def test_svg_renders_every_mode(mode):
    svg, meta = render_svg(load_model_file(MODEL), routing=mode)
    assert meta["routing"] == mode
    assert (mode == "straight") == (meta["links_routed"] == 0)
    assert svg.count('fill="none" stroke=') >= meta["links_routed"] and svg.count('<path d="M') - 1 == meta["links_routed"]


def test_svg_rejects_unknown_mode():
    with pytest.raises(SvgRenderError):
        render_svg(load_model_file(MODEL), routing="curvy")


def test_canvas_moving_a_block_reroutes_only_nearby_links(qapp):
    from raptor.gui.ui import diagram_items as di
    from raptor.gui.ui.diagram import DiagramView
    di.set_link_routing("orthogonal")
    try:
        view = DiagramView()
        view.set_lod_enabled(False)
        view.load_rbd(load_model_file(MODEL))
        item = next(iter(view._block_items.values()))
        affected = di.links_affected_by_moving(item)
        assert 0 < len(affected) < len(view._links) / 4
    finally:
        di.set_link_routing("straight")


def test_fan_in_shares_one_lane_whatever_the_block_widths():
    # three sources of different widths feeding one target at x=300
    sources = [(0, 0, 40, 20), (0, 50, 90, 70), (0, 100, 60, 120)]
    r = routing.Router()
    lanes = set()
    for box in sources:
        p0 = (box[2], (box[1] + box[3]) / 2)
        hint = routing.fan_hint("right", "left", p0, (300, 60), [], sources)
        pts = r.route(p0, "right", (300, 60), "left", [], hint)
        assert pts is not None and _axis_aligned(pts)
        # the vertical run of the Z: the x where the route changes y
        lanes.update(a[0] for a, b in itertools.pairwise(pts) if a[0] == b[0] and a[1] != b[1])
    assert lanes == {195.0}          # midway between the widest source edge (90) and 300
