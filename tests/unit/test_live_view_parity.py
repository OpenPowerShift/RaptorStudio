"""Live-view parity with Raptor 7's own drawing code.

Pins the three things the BESS model exposed: the DSI is Raptor's two
stacked triangles (not a dot), Degraded is Yellow (not the over-stress
green), and a frame no longer rescans every LINK_DATA row once per
hierarchy. The flow label follows `RBDNodeObj.ShowFlow`'s K/M/G
thresholds, and `_live_state_signature` is what lets the GUI skip the
identical t=0 kickoff frames.
"""
from __future__ import annotations

from types import SimpleNamespace

import pytest
from PySide6.QtCore import QPointF
from PySide6.QtWidgets import (
    QApplication, QGraphicsEllipseItem, QGraphicsPathItem,
)

from raptor.core.fileio.rbd import load_rbd
from raptor.core.model import NodeStatus
from raptor.gui.ui.diagram import DiagramView
from raptor.gui.ui.diagram_items import COLOUR
from raptor.gui.ui.diagram_live import _flow_label
from raptor.gui.ui.main_window import _live_state_signature

MODEL = "tests/golden/HierarchyExample/input.rbd"


@pytest.fixture(scope="module")
def qapp():
    return QApplication.instance() or QApplication([])


def test_flow_label_matches_showflow_thresholds():
    # RBDNodeObj.ShowFlow, Raptor 7.
    assert _flow_label(0) == "0"
    assert _flow_label(9999) == "9999"
    assert _flow_label(10000) == "10K"
    assert _flow_label(26880) == "27K"
    assert _flow_label(999499) == "999K"
    assert _flow_label(1234567) == "1.2M"
    assert _flow_label(9950000) == "10M"
    # The source's 1.0B-9.9B branch still appends "M".
    assert _flow_label(999500000) == "1.0M"
    assert _flow_label(9000000000) == "9.0M"
    assert _flow_label(100000000000) == "100000000000"


def test_dsi_is_raptors_two_triangles_not_a_dot(qapp):
    v = DiagramView()
    v.load_rbd(load_rbd(MODEL))
    item = next(iter(v._hier_items.values()))
    item.set_dsi({"state": "Degraded", "nodes": 0, "down": 0,
                  "degraded": 0})
    assert isinstance(item._dsi_dot, QGraphicsPathItem)
    assert not isinstance(item._dsi_dot, QGraphicsEllipseItem)
    # HierMid is two separate closed triangles, centred on the oval.
    assert len(item._dsi_dot.path().toSubpathPolygons()) == 2
    assert item._dsi_dot.pos() == QPointF(0, -1)
    # `WHEN Degraded: SetColor(Yellow)` -- not the over-stress green.
    assert item._dsi_dot.brush().color().name() == "#ffff00"
    assert "Descendant Status Indicator (DSI)" in item._dsi_dot.toolTip()


def test_hierarchy_name_is_inside_at_rest_and_below_while_simulating(qapp):
    """At rest the name is in the oval. A simulation view owns the body's
    centre (DSI triangles) and colour, so the name drops under the oval
    and never overlaps the triangles."""
    v = DiagramView()
    v.load_rbd(load_rbd(MODEL))
    item = next(iter(v._hier_items.values()))
    assert item._label.pos().y() < item.H / 2 - item._label.boundingRect().height()
    assert item._label.pos().y() < 0
    item.set_dsi({"state": "Degraded", "nodes": 0, "down": 0,
                  "degraded": 0})
    assert item._label.pos().y() >= item.H / 2
    assert not item._label.sceneBoundingRect().intersects(
        item._dsi_dot.sceneBoundingRect())
    item.set_anal_label("0.980")
    assert item._anal_label.pos().y() > (
        item._label.pos().y() + item._label.boundingRect().height())
    item.set_dsi(None)
    assert item._label.pos().y() < 0
    assert item._anal_label.pos().y() >= item.H / 2
    # a live state fill (Animate / Step / Failure Effects) does the same
    item.set_live_fill("#00aa00")
    assert item._label.pos().y() >= item.H / 2
    item.retheme(COLOUR)
    assert item._label.pos().y() < 0


def test_boundary_link_map_is_cached_and_invalidated(qapp):
    v = DiagramView()
    v.load_rbd(load_rbd(MODEL))
    first = v._hier_boundary_links()
    assert first, "HierarchyExample must have hierarchy boundary links"
    assert v._hier_boundary_links() is first, \
        "every frame must reuse the map, not rescan LINK_DATA"
    v._invalidate_live_caches()
    assert v._hier_boundary_links() is not first


def _sig(block_status="Running", node_status=NodeStatus.ALL_UP,
         flow=0):
    block = SimpleNamespace(
        op_status=block_status, active_status="Active",
        uses_phasing=False,
        run_state=SimpleNamespace(fev_induced_by_dependency=False, phase_stress=1.0))
    model = SimpleNamespace(
        nodes_by_id={1: SimpleNamespace(status=node_status)},
        hiers_by_id={1: SimpleNamespace(
            status=NodeStatus.ALL_UP, dsi_state=NodeStatus.ALL_UP,
            num_currently_failed=0, dsi_yellows=0)},
        events_by_id={})
    state = SimpleNamespace(color="Green", current_phase=0)
    return _live_state_signature([block], ({1: flow}, {1: flow}),
                                 model, state)


def test_live_signal_ignores_identical_frames_and_sees_changes():
    a = _sig()
    assert a == _sig(), "the t=0 kickoff burst paints the same picture"
    assert a != _sig(block_status="Repairing")
    assert a != _sig(node_status=NodeStatus.DEGRADED)
    assert a != _sig(flow=26880)
