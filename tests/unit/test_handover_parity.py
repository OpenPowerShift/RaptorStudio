"""Band hand-over routing points: one rule, same result from MCP and GUI."""
from __future__ import annotations

import tempfile
from itertools import pairwise

import pytest

from raptor.core.fileio.band_handover import (
    handover_points, handover_sides, side_beside)


def _spec(n=14):
    seq = ["Start"] + [f"B{i}" for i in range(n)] + ["End"]
    return {
        "name": "chain",
        "blocks": [{"name": f"B{i}"} for i in range(n)],
        "nodes": [{"id": 1, "name": "Start", "type": "start"},
                  {"id": 2, "name": "End", "type": "end"}],
        "links": [{"from": a, "to": b} for a, b in pairwise(seq)],
        "system": {"term_condition": 100, "num_runs": 1},
    }


def _call(result):
    assert not result.is_error, result.content
    return result.structured_content


@pytest.fixture
def model_id():
    from raptor.mcp.server import mcp
    with tempfile.TemporaryDirectory() as tmpdir:
        mcp._raptor_args = type("A", (), {"workspace": tmpdir})()
        mcp._raptor_ws = None
        from raptor.mcp.tools_model import build_model
        yield _call(build_model("chain", _spec(), auto_layout=False))["model_id"]


def _rbd(model_id):
    from raptor.mcp import context
    return context.workspace().get(model_id).rbd


def test_points_sit_outside_the_exit_and_entry_sides():
    src = (500.0, 0.0, 54.0, 23.0)
    dst = (0.0, 120.0, 54.0, 23.0)
    first, second = handover_points(src, dst, "LR")
    assert first[1] > 0.0 + 23.0 and second[1] < 120.0 - 23.0
    assert first[0] == 500.0 and second[0] == 0.0
    assert handover_points((0.0, 0.0, 54.0, 23.0), (200.0, 0.0, 54.0, 23.0),
                           "LR") is None
    assert handover_sides((0.0, 0.0), (200.0, 0.0), "auto") is None
    assert side_beside((0.0, 0.0, 54.0, 23.0), (0.0, 40.0)) == "bottom"


def test_mcp_wrapped_layout_saves_handover_points(model_id):
    from raptor.mcp.tools_model import auto_layout
    out = _call(auto_layout(model_id, max_columns=5))
    grips = _rbd(model_id).link_grips
    assert out["handover_points"] == len(grips) > 0
    assert len(grips) % 2 == 0
    ids = [g["id"] for g in grips]
    assert len(set(ids)) == len(ids)
    assert all(g["kind"] == "route_grip" and g["active"] for g in grips)


def test_mcp_layout_replaces_points_and_snake_adds_none(model_id):
    from raptor.mcp.tools_model import auto_layout
    _call(auto_layout(model_id, max_columns=5))
    rbd = _rbd(model_id)
    rbd.link_grips.append({"kind": "route_grip", "id": 999, "parent": 0,
                           "link": 1, "x": 1.0, "y": 1.0, "active": True})
    _call(auto_layout(model_id))                    # snake: no hand-overs
    assert _rbd(model_id).link_grips == []


def test_mcp_and_gui_pin_the_same_links(model_id):
    pytest.importorskip("PySide6")
    from PySide6.QtWidgets import QApplication
    from raptor.api.build import build_from_spec
    from raptor.gui.ui.diagram import DiagramView
    from raptor.mcp.tools_model import auto_layout
    _call(auto_layout(model_id, max_columns=5))
    mcp_links = sorted({g["link"] for g in _rbd(model_id).link_grips})
    QApplication.instance() or QApplication([])
    view = DiagramView()
    view.load_rbd(build_from_spec(_spec(), auto_layout=False))
    view.auto_layout(direction="LR", max_columns=5)
    gui_links = sorted({g["link"] for g in view._rbd_ref.link_grips})
    assert mcp_links == gui_links
