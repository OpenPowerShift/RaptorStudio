"""The `diff_models` MCP tool: what is different between two open models."""
from __future__ import annotations

import asyncio
import json
from pathlib import Path

import jsonschema
import pytest

REPO = Path(__file__).resolve().parent.parent.parent
GOLDEN = REPO / "tests" / "golden"


@pytest.fixture
def pair(tmp_path):
    """Two ids for MotorDemo; the second has the Armature's failure scale changed."""
    from raptor.mcp.server import mcp
    from raptor.mcp.tools_model import open_model, update_element
    mcp._raptor_args = type("A", (), {"workspace": str(tmp_path), "read_only": False})()
    mcp._raptor_ws = None
    ids = [open_model("MotorDemo").structured_content["model_id"] for _ in range(2)]
    r = update_element(ids[1], "Armature", {"fail_params": [1.75, 200.0, 0.0]})
    assert not r.is_error, r.content
    return ids


def _diff(*args, **kw):
    from raptor.mcp.tools_model import diff_models
    r = diff_models(*args, **kw)
    assert not r.is_error, r.content
    return r


def test_a_changed_distribution_is_reported_in_words(pair):
    r = _diff(*pair)
    sc = r.structured_content
    assert not sc["identical"] and sc["truncated"] is False and sc["total_changes"] == 1
    change = sc["elements"]["changed"][0]["changes"][0]
    assert change["field"] == "failure" and "scale 174" in change["a"] and "scale 200" in change["b"]
    text = r.content[0].text
    assert "| Element | Field | A | B |" in text and "Armature" in text


def test_identical_models_and_ignore(pair):
    sc = _diff(pair[0], pair[0]).structured_content
    assert sc["identical"] and sc["ignored"] == ["derived", "layout"]
    assert sc["summary"]["connectivity"] == "same wiring"
    assert _diff(pair[0], pair[1], ignore=["failure"]).structured_content["identical"]
    assert _diff(pair[0], pair[0], ignore=[]).structured_content["ignored"] == []


def test_summary_caps_the_changes_and_full_does_not(pair, monkeypatch):
    from raptor.mcp import tools_model
    from raptor.mcp.tools_model import update_element
    for name in ("Casing", "Magnets"):
        assert not update_element(pair[1], name, {"fail_params": [1.5, 99.0, 0.0]}).is_error
    monkeypatch.setattr(tools_model, "_DIFF_SUMMARY_LIMIT", 2)
    short = _diff(*pair).structured_content
    kept = sum(len(e["changes"]) for e in short["elements"]["changed"])
    assert short["truncated"] is True and kept == 2 and short["total_changes"] == 3
    assert "Showing the first 2 of 3" in _diff(*pair).content[0].text
    full = _diff(*pair, detail="full").structured_content
    assert full["truncated"] is False and sum(len(e["changes"]) for e in full["elements"]["changed"]) == 3


def test_errors_are_results_not_crashes(pair):
    from raptor.mcp.tools_model import diff_models
    assert diff_models(pair[0], "m_nosuch").is_error
    assert diff_models(pair[0], pair[1], ignore=["bogus"]).is_error
    assert diff_models(pair[0], pair[1], match="fuzzy").is_error


def test_tool_is_read_only_and_its_result_satisfies_the_schema(pair):
    from raptor.mcp import server
    tool = next(t for t in asyncio.run(server.mcp.list_tools()) if t.name == "diff_models")
    assert tool.annotations.read_only_hint is True
    schema = json.loads((REPO / "raptor" / "mcp" / "output_schemas.json").read_text())["diff_models"]
    assert tool.output_schema == schema
    jsonschema.validate(_diff(*pair).structured_content, schema)
    jsonschema.validate(_diff(pair[0], pair[0], detail="full", ignore=[]).structured_content, schema)
