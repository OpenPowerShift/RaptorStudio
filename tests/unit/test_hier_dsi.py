"""End-of-run Descendant Status Indicator (manual Ch.16) rollup."""

from __future__ import annotations

import pathlib
import os

import pytest

from raptor.core.engine.driver import _hier_dsi_rollup
from raptor.core.engine.load import load_model
from raptor.core.engine.runner import run_simulation_with_reports
from raptor.core.fileio.rbd import load_rbd
from raptor.core.model import NodeStatus

REPO = pathlib.Path(__file__).resolve().parents[2]
GOLD = os.path.join(REPO, "tests", "golden")


def test_rollup_all_up_on_clean_model():
    m = load_model(load_rbd(os.path.join(
        GOLD, "HierarchyExample", "input.rbd")))
    dsi = _hier_dsi_rollup(m, 1000.0)
    # fresh model objects: every node ALL_UP -> all three ovals green
    assert set(dsi) == {23, 26, 29}
    assert all(d["state"] == "AllUp" and d["down"] == 0
               for d in dsi.values())
    assert all(d["nodes"] >= 1 for d in dsi.values())


def test_rollup_down_and_degraded_precedence():
    m = load_model(load_rbd(os.path.join(
        GOLD, "HierarchyExample", "input.rbd")))
    nodes = m.nodes_by_id
    # inside hier 23 (node 20) DOWN, inside 26 (node 21) DEGRADED
    nodes[20].status = NodeStatus.DOWN
    nodes[21].status = NodeStatus.DEGRADED
    dsi = _hier_dsi_rollup(m, 1000.0)
    assert dsi[23]["state"] == "Down" and dsi[23]["down"] == 1
    assert dsi[26]["state"] == "Degraded"
    assert dsi[29]["state"] == "AllUp"
    # markers never counted as descendants
    assert all("in" not in d for d in dsi.values())


def test_report_pipeline_carries_hier_dsi():
    sink = []
    run_simulation_with_reports(
        load_rbd(os.path.join(GOLD, "HierarchyExample", "input.rbd")),
        num_runs=1, date="fixed", report_sink=sink)
    assert sink and sink[0].hier_dsi
    states = {d["state"] for d in sink[0].hier_dsi.values()}
    assert states <= {"AllUp", "Degraded", "Down"}


def test_gui_ovals_get_dsi_dot(qapp):
    from PySide6.QtWidgets import QApplication
    _app = QApplication.instance() or qapp
    from raptor.gui.ui.diagram import DiagramView
    v = DiagramView()
    v.load_rbd(load_rbd(os.path.join(
        GOLD, "HierarchyExample", "input.rbd")))
    v.apply_hier_dsi({23: {"state": "Down", "nodes": 1, "down": 1,
                           "degraded": 0}})
    item = v._hier_items[23]
    assert item._dsi_dot.isVisible()
    assert item._dsi_info["state"] == "Down"
    # clearing hides the dot again
    v.apply_hier_dsi({})
    assert not item._dsi_dot.isVisible()


@pytest.fixture(scope="module")
def qapp():
    from PySide6.QtWidgets import QApplication
    return QApplication.instance() or QApplication([])


def test_live_dsi_dot_shows_all_six_states_and_linked_cut(qapp):
    """The original's dot (`ChangeDSIStatus`): green, blue, yellow, sienna, orange, red; white or
    black when the hierarchy's out node is Linked or Cut."""
    from raptor.gui.ui.diagram import DiagramView
    v = DiagramView()
    v.load_rbd(load_rbd(os.path.join(GOLD, "HierarchyExample", "input.rbd")))
    item = next(iter(v._hier_items.values()))
    seen = {}
    for state in ("AllUp", "NodeStandby", "Degraded", "NodeIdle", "NodePM", "Down", "Linked", "Cut"):
        item.set_dsi({"state": state, "nodes": 0, "down": 0, "degraded": 0})
        seen[state] = item._dsi_dot.brush().color().name()
    assert len(set(seen.values())) == 8, seen
    assert seen["Linked"] == "#ffffff" and seen["Cut"] == "#000000"
