"""MCP tools for the Advisor and downtime drivers, and their parity with the API."""
from __future__ import annotations

import asyncio

import pytest

pytest.importorskip("mcp")

SPEC = {
    "experiment": {"trials": 30, "termination_type": "TimeSim", "term_condition": 3000},
    "blocks": [{"name": "Pump", "fail_dist": 4, "fail_params": [300.0, 0.0],
                "repair_dist": 7, "repair_params": [10.0, 3.0]},
               {"name": "Spare", "fail_dist": 4, "fail_params": [300.0, 0.0],
                "repair_dist": 7, "repair_params": [10.0, 3.0],
                "dep_type": "RBDBlock", "dep_num": 1, "dep_idle": 100}],
    "nodes": [{"name": "Start", "type": 1}, {"name": "S", "type": 2, "k": 1},
              {"name": "J", "type": 2, "k": 1}, {"name": "End", "type": 3}],
    "links": [{"from": "Start", "to": "S"}, {"from": "S", "to": "Pump"},
              {"from": "S", "to": "Spare"}, {"from": "Pump", "to": "J"},
              {"from": "Spare", "to": "J"}, {"from": "J", "to": "End"}],
}


@pytest.fixture
def server(tmp_path):
    from raptor.mcp import server as srv
    from raptor.mcp import tools_advisor, tools_model, tools_run  # noqa: F401
    srv.mcp._raptor_args = type("A", (), {"workspace": str(tmp_path), "read_only": False})()
    srv.mcp._raptor_ws = None
    yield srv
    srv.mcp._raptor_ws = None


def call(server, tool, /, **args):
    result = asyncio.run(server.mcp.call_tool(tool, args))
    return result


def ok(result):
    text = " ".join(c.text for c in result.content if getattr(c, "type", "") == "text")
    assert not result.is_error, text
    return result.structured_content


@pytest.fixture
def model(server):
    return ok(call(server, "build_model", name="m", spec=SPEC, validate_model=False))["model_id"]


def test_advise_matches_the_api(server, model):
    from raptor.api.advisor import advise_model
    from raptor.mcp import context
    data = ok(call(server, "advise", model_id=model, min_severity="info", limit=200))
    api = [f.to_dict() for f in advise_model(context.workspace().get(model).rbd)]
    assert data["findings"] == api
    assert "M-STR-002" in {f["rule"] for f in data["findings"]}
    assert data["counts"]["warning"] >= 1 and data["truncated"] is False


def test_advise_filters_and_truncates(server, model):
    data = ok(call(server, "advise", model_id=model, only=["M-STR"], min_severity="info"))
    assert {f["rule"] for f in data["findings"]} == {"M-STR-002"}
    data = ok(call(server, "advise", model_id=model, min_severity="info", limit=1))
    assert data["shown"] == 1 and data["truncated"] is True


def test_apply_fix_then_undo(server, model):
    done = ok(call(server, "apply_fix", model_id=model, rule="M-STR-002"))
    assert done["applied"] == ["block:2.dep_type=None"] and done["remaining"] == 0
    assert "M-STR-002" not in {f["rule"] for f in ok(call(server, "advise", model_id=model))["findings"]}
    ok(call(server, "undo_last_edit", model_id=model))
    assert "M-STR-002" in {f["rule"] for f in ok(call(server, "advise", model_id=model))["findings"]}


def test_apply_fix_says_what_to_do_when_it_cannot(server, model):
    missing = call(server, "apply_fix", model_id=model, rule="M-RES-001")
    assert missing.is_error and "call advise" in missing.content[0].text
    tool_only = call(server, "apply_fix", model_id=model, rule="M-SPR-001")
    assert tool_only.is_error


def test_drivers_need_attribution_and_match_the_api(server, model):
    plain = ok(call(server, "simulate", model_id=model, seed=4))
    refused = call(server, "downtime_drivers", run_id=plain["run_id"])
    assert refused.is_error and "attribution" in refused.content[0].text

    run = ok(call(server, "simulate", model_id=model, seed=4, attribution=True))
    assert run["summary"] == plain["summary"]            # attribution changes no result
    data = ok(call(server, "downtime_drivers", run_id=run["run_id"], top=1))
    from raptor.api.insight import drivers
    from raptor.mcp import context
    api = drivers.read(context.workspace(), run["run_id"])
    assert data["system"] == api["system"] and data["causes"] == api["causes"]
    assert len(data["elements"]) == 1 and data["caption"] == drivers.caption(api)

    advised = ok(call(server, "advise", model_id=model, run_id=run["run_id"], min_severity="info",
                      limit=200, targets=[{"metric": "system.ao", "op": ">=", "value": 0.2}]))
    rules = {f["rule"] for f in advised["findings"]}
    assert {"R-DRV-001", "R-REQ-001", "M-STR-002"} <= rules


def test_rules_listing(server):
    rules = ok(call(server, "list_advisor_rules"))["rules"]
    assert {"model", "run"} == {r["phase"] for r in rules}
    assert all(r["id"] and r["title"] for r in rules)


def test_annual_availability_matches_the_api(server, model):
    from raptor.api.insight import drivers
    from raptor.mcp import context
    run = ok(call(server, "simulate", model_id=model, seed=4, attribution=True))
    data = ok(call(server, "annual_availability", run_id=run["run_id"], year_length=500.0,
                   below=0.5))
    api = drivers.periods(context.workspace(), run["run_id"], 500.0, below=0.5)
    assert data == api
    assert data["pooled"]["n"] == 30 * 6 and "p_below" in data["pooled"]


def test_run_story_returns_the_same_numbers_as_the_api(server, model):
    from raptor.api.insight.story import from_run
    from raptor.mcp import context
    run = ok(call(server, "simulate", model_id=model, seed=4, attribution=True))
    data = ok(call(server, "run_story", run_id=run["run_id"]))
    api = from_run(context.workspace(), run["run_id"]).to_dict()
    assert data["headline"] == api["headline"]
    assert data["drivers"] == api["drivers"]
    assert data["numbers"] == api["numbers"]
    assert data["provenance"]["run_id"] == run["run_id"]


def test_run_story_matches_the_api_and_needs_no_attribution(server, model):
    from raptor.api.insight.story import from_run
    from raptor.mcp import context
    plain = ok(call(server, "simulate", model_id=model, seed=4))
    data = ok(call(server, "run_story", run_id=plain["run_id"]))
    api = from_run(context.workspace(), plain["run_id"])
    assert data["headline"]["field"] == "availability"
    assert data["headline"]["value"] == pytest.approx(api.headline.value)
    assert data["dots"] == api.dots
    assert data["provenance"]["runs"] == api.provenance["runs"]
    with_attr = ok(call(server, "simulate", model_id=model, seed=4, attribution=True))
    story = ok(call(server, "run_story", run_id=with_attr["run_id"]))
    assert story["drivers_available"] is True and story["drivers"]
