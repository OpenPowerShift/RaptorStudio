"""The layout refine search has a time budget (#4 item 4).

A parallel model of 12 branches lays out in 10 ms, 24 in about a second and
48 in 40 s: the collision refine's cost grows with links x elements, and the
element cap did not bound it. When the budget runs out the layered layout is
kept and the caller is told.
"""
from __future__ import annotations

import time

from raptor.api.workspace import Workspace
from raptor.core.fileio import legacy_layout
from raptor.core.fileio.legacy_layout import GRID, normalize_legacy_layout


def _model(tmp_path, n=24):
    ws = Workspace(str(tmp_path))
    mid = ws.create_model("p", template="parallel", n=n)["model_id"]
    return ws, mid, ws.get(mid).rbd


def _positions(rbd):
    out = {}
    for sec in ("BLOCK_DATA", "NODE_DATA"):
        for r in rbd.sections[sec].rows:
            if r.get("ID") != "0":
                out[(sec, r["ID"])] = (float(r["xPos"]), float(r["yPos"]))
    return out


def test_an_exhausted_budget_stops_the_search_and_says_so(tmp_path, monkeypatch):
    _ws, _mid, rbd = _model(tmp_path)
    full_report: dict = {}
    t0 = time.perf_counter()
    normalize_legacy_layout(rbd, force=True, report=full_report)
    full = time.perf_counter() - t0
    assert full_report == {}           # it fit in the default budget

    monkeypatch.setattr(legacy_layout, "REFINE_BUDGET_S", 0.0)
    _ws, _mid, rbd2 = _model(tmp_path / "b")
    cut: dict = {}
    t0 = time.perf_counter()
    normalize_legacy_layout(rbd2, force=True, report=cut)
    cut_time = time.perf_counter() - t0
    assert cut["refine_cut_short"] >= 1
    assert cut_time < full / 2, (cut_time, full)


def test_a_cut_short_layout_is_still_complete_and_on_the_grid(tmp_path, monkeypatch):
    monkeypatch.setattr(legacy_layout, "REFINE_BUDGET_S", 0.0)
    _ws, _mid, rbd = _model(tmp_path)
    normalize_legacy_layout(rbd, force=True)
    pos = _positions(rbd)
    assert len(pos) >= 24 + 2
    for x, y in pos.values():
        assert abs(x / GRID - round(x / GRID)) < 1e-6
        assert abs(y / GRID - round(y / GRID)) < 1e-6
    # No two elements share a spot.
    assert len(set(pos.values())) == len(pos)


def test_a_model_that_fits_the_budget_is_laid_out_as_before(tmp_path):
    """The budget must not change the result when it is not reached."""
    _ws, _mid, a = _model(tmp_path / "a", n=10)
    _ws, _mid, b = _model(tmp_path / "b", n=10)
    normalize_legacy_layout(a, force=True)
    report: dict = {}
    normalize_legacy_layout(b, force=True, report=report)
    assert report == {} and _positions(a) == _positions(b)


def test_the_layout_call_needs_no_report(tmp_path):
    _ws, _mid, rbd = _model(tmp_path, n=6)
    assert normalize_legacy_layout(rbd, force=True) in (True, False)


def test_the_tool_reports_a_cut_short_layout(tmp_path, monkeypatch):
    from raptor.mcp.server import mcp
    from raptor.mcp.tools_model import auto_layout, create_model
    mcp._raptor_args = type("A", (), {"workspace": str(tmp_path)})()
    mcp._raptor_ws = None
    mid = create_model("p", template="parallel", n=24).structured_content["model_id"]
    assert auto_layout(mid).structured_content["refine_cut_short"] == 0
    monkeypatch.setattr(legacy_layout, "REFINE_BUDGET_S", 0.0)
    cut = auto_layout(mid)
    data = cut.structured_content
    assert data["refine_cut_short"] >= 1
    text = " ".join(c.text for c in cut.content if getattr(c, "type", "") == "text")
    assert "time budget" in text
