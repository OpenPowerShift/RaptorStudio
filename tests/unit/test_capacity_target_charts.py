"""The capacity target reaches every surface that draws the capacity charts:
the command line, the MCP tool, the Story and the report, falling back to the
model's saved target as the availability target does."""
from __future__ import annotations

import asyncio
from pathlib import Path

import pytest

from raptor.api import run as run_api
from raptor.api.insight import drivers
from raptor.api.insight.story import from_run
from raptor.api.workspace import Workspace
from raptor.cli.cmd_advise import _capacity_target, _chart_for
from raptor.core.fileio.rbd import load_rbd

pytest.importorskip("mcp")

GOLD = Path(__file__).resolve().parents[1] / "golden"


@pytest.fixture(scope="module")
def stored(tmp_path_factory):
    """One capacity run (Capacity2) in a workspace, its model carrying a
    saved capacity target of 90 %."""
    ws = Workspace(tmp_path_factory.mktemp("captarget"))
    rbd = load_rbd(str(GOLD / "Capacity2" / "input.rbd"))
    rbd.targets = {"capacity": 0.9}
    model = ws.register_model(rbd, name="c2")["model_id"]
    result = run_api.run(ws, model, num_runs=6, seed=3, attribution=True)
    return ws, model, result.run_id, drivers.read(ws, result.run_id)


def _targets(spec):
    return [mark for mark in spec.marks if mark.get("kind") == "target"]


def test_the_command_line_falls_back_to_the_saved_capacity_target(stored):
    ws, _model, run_id, attribution = stored
    band = _chart_for(ws, run_id, "capacity-band", attribution)
    assert _targets(band)[0]["share"] == 0.9
    assert "capacity target" in band.caption
    assert "capacity target" in _chart_for(
        ws, run_id, "capacity-waterfall", attribution).caption
    # a --target on the command line wins over the saved one
    explicit = _chart_for(ws, run_id, "capacity-through-run", attribution,
                          capacity_target=0.5)
    assert _targets(explicit)[0]["share"] == 0.5
    assert "It met the capacity target." in explicit.caption


def test_the_target_option_names_the_capacity_target():
    targets = [{"metric": "system.ao", "op": ">=", "value": 0.99},
               {"metric": "system.capacity", "op": ">=", "value": 0.95}]
    assert _capacity_target(targets) == 0.95
    assert _capacity_target(targets[:1]) is None


def test_the_story_and_the_report_carry_the_capacity_verdict(stored):
    ws, _model, run_id, _attribution = stored
    story = from_run(ws, run_id)
    assert story.capacity_target == 0.9
    assert story.capacity_verdict.startswith("Capacity delivered ")
    assert "against the capacity target 90 %" in story.capacity_verdict
    assert from_run(ws, run_id, capacity_target=0.99).capacity_verdict \
        .count("missed by") == 1
    from raptor.api.insight import report_section
    section = report_section.from_run(ws, run_id)
    assert section.story["capacity_verdict"] == story.capacity_verdict
    waterfall = next(spec for spec in section.charts
                     if spec.title == "Lost capacity by cause")
    assert "capacity target 90 %" in waterfall.caption
    from raptor.api.report_html import _story_html

    class _Insight:
        story = section.story
    assert story.capacity_verdict.replace("&", "&amp;") in _story_html(_Insight())


@pytest.fixture
def server(stored):
    from raptor.mcp import server as srv
    from raptor.mcp import tools_advisor, tools_model, tools_run  # noqa: F401
    ws = stored[0]
    srv.mcp._raptor_args = type("A", (), {"workspace": str(ws.root),
                                          "read_only": False})()
    srv.mcp._raptor_ws = None
    yield srv
    srv.mcp._raptor_ws = None


def _chart(server, run_id, chart, **args):
    result = asyncio.run(server.mcp.call_tool(
        "run_chart", {"run_id": run_id, "chart": chart, **args}))
    text = " ".join(c.text for c in result.content if getattr(c, "type", "") == "text")
    assert not result.is_error, text
    return result.structured_content


def test_run_chart_takes_a_capacity_target_and_falls_back_to_the_saved_one(
        server, stored):
    run_id = stored[2]
    saved = _chart(server, run_id, "capacity-band")
    assert [m["share"] for m in saved["marks"] if m["kind"] == "target"] == [0.9]
    explicit = _chart(server, run_id, "capacity-band", capacity_target=0.5)
    assert [m["share"] for m in explicit["marks"] if m["kind"] == "target"] == [0.5]
    # the availability target does not land on a capacity chart
    other = _chart(server, run_id, "capacity-band", target=0.5, capacity_target=0.7)
    assert [m["share"] for m in other["marks"] if m["kind"] == "target"] == [0.7]
    waterfall = _chart(server, run_id, "capacity-waterfall", capacity_target=0.99)
    assert "missed the capacity target 99 %" in waterfall["caption"]
    result = asyncio.run(server.mcp.call_tool(
        "run_chart", {"run_id": run_id, "chart": "capacity-band",
                      "capacity_target": 95.0}))
    assert result.is_error
