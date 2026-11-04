"""MCP `export_analytical`: the same solve as `analytical_analysis`, written
to one workbook (reliability and availability side by side when both are
asked for), inside the workspace; progress carries the stage."""
from __future__ import annotations

import asyncio
import zipfile
from pathlib import Path

import pytest


@pytest.fixture
def lantern(tmp_path):
    from raptor.mcp.server import mcp
    mcp._raptor_args = type("A", (), {"workspace": str(tmp_path)})()
    mcp._raptor_ws = None
    from raptor.mcp import server  # noqa: F401  (registers the tools)
    from raptor.mcp.tools_model import open_model
    return open_model("Lantern1").structured_content["model_id"], tmp_path


def _sheets(path: Path) -> str:
    return zipfile.ZipFile(path).read("xl/workbook.xml").decode()


def test_both_modes_go_into_one_named_workbook(lantern):
    from raptor.mcp.tools_analytic import export_analytical
    mid, ws = lantern
    r = asyncio.run(export_analytical(mid, modes=["reliability",
                                                  "availability"],
                                      tau=5.0))
    assert not r.is_error, r.content
    data = r.structured_content
    path = Path(data["path"])
    assert path.name == "Lantern1_analytical_reliability_availability.xlsx"
    assert path.parent == ws / "Tables" / "Lantern1"
    assert data["modes"] == ["reliability", "availability"]
    sheets = _sheets(path)
    assert "Reliability Summary" in sheets and "Availability Summary" in sheets


def test_one_mode_by_default_and_a_folder_inside_the_workspace(lantern):
    from raptor.mcp.tools_analytic import export_analytical
    mid, ws = lantern
    r = asyncio.run(export_analytical(mid, folder="out"))
    assert not r.is_error, r.content
    path = Path(r.structured_content["path"])
    assert path == ws / "out" / "Lantern1_analytical_reliability.xlsx"
    assert "Summary" in _sheets(path)
    escape = asyncio.run(export_analytical(mid, folder="../elsewhere"))
    assert escape.is_error
    managed = asyncio.run(export_analytical(mid, folder="models/x"))
    assert managed.is_error


def test_progress_is_one_percentage_with_the_stage(lantern):
    from raptor.mcp import jobs
    from raptor.mcp.tools_analytic import analytical_analysis
    mid, _ws = lantern
    seen: list[tuple] = []

    class Ctx:
        async def report_progress(self, done, total, message):
            seen.append((done, total, message))

    old = jobs.HEARTBEAT_S
    jobs.HEARTBEAT_S = 0.0
    try:
        r = asyncio.run(analytical_analysis(mid, ctx=Ctx()))
    finally:
        jobs.HEARTBEAT_S = old
    assert not r.is_error
    if seen:        # a quick solve may finish before the first heartbeat
        _done, total, message = seen[-1]
        assert total == 1000 and "%" in message
