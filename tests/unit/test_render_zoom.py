"""Render ``zoom_to`` / legibility hint (ZOOM-1).

Skipped when PySide6 is unavailable (the render worker needs Qt).
"""
from __future__ import annotations

import os
import tempfile
from pathlib import Path

import pytest

from raptor.api.render import RenderError, qt_available, render_diagram

pytestmark = pytest.mark.skipif(
    not qt_available(), reason="PySide6 not installed")

REPO = Path(__file__).resolve().parent.parent.parent
GOLDEN = REPO / "tests" / "golden"
MODEL = str(GOLDEN / "MotorDemo" / "input.rbd")


def _tmp_png() -> str:
    return os.path.join(tempfile.mkdtemp(), "render.png")


def test_full_render_reports_legibility():
    _p, meta = render_diagram(MODEL, _tmp_png(), max_px=800)
    assert meta["elements_drawn"] >= meta["elements_in_view"] > 0
    assert "effective_label_px" in meta and "legible" in meta
    assert meta["px_per_unit"] > 0


def test_zoom_to_frames_and_improves_legibility():
    _p, full = render_diagram(MODEL, _tmp_png(), max_px=800)
    _p2, zoom = render_diagram(MODEL, _tmp_png(), zoom_to=["Casing"], max_px=800)
    # One element in view, and text is larger than the whole-model render.
    assert zoom["elements_in_view"] == 1
    assert zoom["effective_label_px"] > full["effective_label_px"]
    assert zoom["zoom_to"] == ["Casing"]


def test_zoom_to_unknown_element_errors_clearly():
    with pytest.raises(RenderError) as exc:
        render_diagram(MODEL, _tmp_png(), zoom_to=["NoSuchBlock"])
    assert "zoom_to" in str(exc.value)


def test_zoom_to_by_qualified_id_and_bad_kind():
    _p, meta = render_diagram(MODEL, _tmp_png(), zoom_to=["Block:1"], max_px=800)
    assert meta["elements_in_view"] == 1
    with pytest.raises(RenderError):
        render_diagram(MODEL, _tmp_png(), zoom_to=["Node:1"])   # no node id 1


def test_zoom_to_ambiguous_bare_id_errors(tmp_path):
    from raptor.api.build import build_from_spec
    from raptor.core.fileio.rbds import save_rbds
    spec = {
        "system": {"term_condition": 10},
        "blocks": [{"id": 1, "name": "A", "fail_dist": 4,
                    "fail_params": [100.0, 0.0]}],
        # Start node deliberately shares id 1 with the block.
        "nodes": [{"id": 1, "name": "Start", "type": "start"},
                  {"id": 2, "name": "End", "type": "end"}],
        "links": [{"from": "Start", "to": "A"}, {"from": "A", "to": "End"}],
    }
    rbd = build_from_spec(spec, auto_layout=True)
    path = tmp_path / "amb.rbds"
    save_rbds(rbd, str(path))
    with pytest.raises(RenderError) as exc:
        render_diagram(str(path), _tmp_png(), zoom_to=["1"], max_px=400)
    assert "ambiguous" in str(exc.value).lower()


def test_multi_selector_union_and_margin():
    _p, one = render_diagram(MODEL, _tmp_png(), zoom_to=["Casing"], max_px=800)
    _p2, two = render_diagram(MODEL, _tmp_png(),
                              zoom_to=["Casing", "Armature"], max_px=800)
    assert two["elements_in_view"] >= 2
    assert two["px_per_unit"] < one["px_per_unit"]
    _p3, wide = render_diagram(MODEL, _tmp_png(), zoom_to=["Casing"],
                               margin=200.0, max_px=800)
    assert wide["px_per_unit"] < one["px_per_unit"]


def test_full_render_can_be_illegible():
    _p, meta = render_diagram(MODEL, _tmp_png(), max_px=800)
    assert meta["legible"] is False


def test_mcp_include_annotations_changes_hash(tmp_path):
    from raptor.mcp.server import mcp
    mcp._raptor_args = type("A", (), {"workspace": str(tmp_path)})()
    mcp._raptor_ws = None
    from raptor.mcp.tools_model import open_model
    from raptor.mcp.tools_view import render_diagram as tool
    mid = open_model("MotorDemo").structured_content["model_id"]
    a = tool(mid, max_px=400, include_annotations=True)
    b = tool(mid, max_px=400, include_annotations=False)
    assert a.structured_content["png_path"] != b.structured_content["png_path"]
    z = tool(mid, zoom_to=["Casing"], max_px=800)
    assert z.structured_content["zoom_to"] == ["Casing"]


def test_negative_or_nonfinite_margin_rejected():
    with pytest.raises(RenderError):
        render_diagram(MODEL, _tmp_png(), zoom_to=["Casing"], margin=-1)
    with pytest.raises(RenderError):
        render_diagram(MODEL, _tmp_png(), zoom_to=["Casing"], margin=float("inf"))


def test_mcp_tool_forwards_zoom_and_reports_fields(tmp_path):
    from raptor.mcp.server import mcp
    mcp._raptor_args = type("A", (), {"workspace": str(tmp_path)})()
    mcp._raptor_ws = None
    from raptor.mcp.tools_model import open_model
    from raptor.mcp.tools_view import render_diagram as tool

    mid = open_model("MotorDemo").structured_content["model_id"]
    r = tool(mid, zoom_to=["Casing"], max_px=800)
    assert not r.is_error, r
    d = r.structured_content
    assert d["elements_in_view"] == 1
    assert d["legible"] is True
    assert "resource_link" in d

    # A different zoom target gets a different content-addressed PNG.
    r2 = tool(mid, zoom_to=["Armature"], max_px=800)
    assert r2.structured_content["png_path"] != d["png_path"]
