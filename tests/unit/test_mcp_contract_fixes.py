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


def test_run_results_stay_small_unless_the_detail_is_asked_for(server):
    """Per-replication rows carry only scalar system figures by default; on
    a 500-block model the nested per-element data made the answer 2 MB."""
    from raptor.mcp import tools_run  # noqa: F401
    built = asyncio.run(server.mcp.call_tool(
        "build_model", {"name": "m", "spec": FULL})).structured_content
    ran = asyncio.run(server.mcp.call_tool(
        "simulate", {"model_id": built["model_id"], "num_runs": 3}))
    run_id = ran.structured_content["run_id"]
    import json as _json
    from raptor.api.run import run_dir
    path = run_dir(server.mcp._raptor_ws, run_id) / "per_run.json"
    rows = _json.loads(path.read_text())
    rows[0]["elements"] = [{"id": i, "x": 1.0} for i in range(500)]
    path.write_text(_json.dumps(rows))
    small = asyncio.run(server.mcp.call_tool(
        "get_run_results", {"run_id": run_id})).structured_content
    assert len(small["per_run"]) == 3
    assert all(not isinstance(v, (list, dict))
               for row in small["per_run"] for v in row.values())
    full = asyncio.run(server.mcp.call_tool(
        "get_run_results", {"run_id": run_id, "per_run_detail": True}))
    assert len(full.structured_content["per_run"][0]["elements"]) == 500


def test_a_background_run_says_when_it_is_attributing(server):
    """After the last replication an attributed run replays the event log,
    minutes on a large model; run_status said `simulating` at 100 %."""
    import time

    from raptor.mcp import async_runs, tools_run  # noqa: F401
    built = asyncio.run(server.mcp.call_tool(
        "build_model", {"name": "m", "spec": FULL})).structured_content
    started = asyncio.run(server.mcp.call_tool(
        "start_run", {"model_id": built["model_id"], "num_runs": 4,
                      "attribution": True})).structured_content
    job = async_runs.get(started["job_id"])
    deadline = time.time() + 60
    while job.state in ("queued", "running") and time.time() < deadline:
        time.sleep(0.05)
    assert job.state == "done"
    assert job.insight is not None and job.insight[1] == job.insight[2] == 4
    snap = job.snapshot()
    assert snap["attributing"]["done"] == 4 and snap["phase"] == "done"
    job.state = "running"                    # as a poll mid-replay sees it
    assert job.phase == "attributing"
