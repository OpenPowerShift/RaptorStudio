"""Contract defects an MCP client found by validating every answer against
the advertised outputSchema (the SDK does this on every call)."""
from __future__ import annotations

import asyncio
import json
from pathlib import Path

import pytest

pytest.importorskip("mcp")
jsonschema = pytest.importorskip("jsonschema")

SCHEMAS = json.loads((Path(__file__).resolve().parents[2] / "raptor" / "mcp"
                      / "output_schemas.json").read_text(encoding="utf-8"))

FULL = {  # every block has its failure and repair data: nothing defaulted
    "experiment": {"trials": 2, "termination_type": "TimeSim",
                   "term_condition": 100.0},
    "blocks": [{"name": "Pump", "fail_dist": "Fixed", "fail_params": [20, 0, 0],
                "repair_dist": "Fixed", "repair_params": [2, 0, 0]}],
    "nodes": [{"name": "Start", "type": 1}, {"name": "End", "type": 3}],
    "links": [{"from": "Start", "to": "Pump"}, {"from": "Pump", "to": "End"}],
}


@pytest.fixture
def server(tmp_path):
    from raptor.mcp import server as srv
    from raptor.mcp import tools_model  # noqa: F401
    srv.mcp._raptor_args = type("A", (), {"workspace": str(tmp_path),
                                          "read_only": False})()
    srv.mcp._raptor_ws = None
    yield srv
    srv.mcp._raptor_ws = None


def test_a_clean_build_matches_the_advertised_schema(server):
    result = asyncio.run(server.mcp.call_tool(
        "build_model", {"name": "m", "spec": FULL}))
    assert not result.is_error
    out = result.structured_content
    assert out["notes"] == []
    jsonschema.validate(out, SCHEMAS["build_model"])
