"""MCP support for large models: bulk failure effects, spec files, core defaults.

Feedback from converting a very large model: one `failure_effects` call per
scenario was impractical, a big spec could not be passed inline, and a
multi-hour batch ran on one core unless the caller knew to ask for more.
"""
from __future__ import annotations

import asyncio
import json
import time
from pathlib import Path
from types import SimpleNamespace

import pytest

from raptor.api.analyse import (
    AnalysisError, MAX_FEV_SCENARIOS, failure_effects, failure_effects_many,
)
from raptor.api.workspace import Workspace

GOLDEN = Path(__file__).resolve().parents[1] / "golden"


@pytest.fixture
def workspace(tmp_path):
    return Workspace(tmp_path)


def _open(workspace, name):
    return workspace.open_model(GOLDEN / name / "input.rbd")["model_id"]


def _use_workspace(tmp_path, **flags):
    from raptor.mcp.server import mcp
    mcp._raptor_args = type("A", (), {"workspace": str(tmp_path), **flags})()
    mcp._raptor_ws = None


# --- bulk failure effects ---------------------------------------------------

SCENARIOS = [{"fail": [3]}, {"fail": [4]}, {"fail": [3, 4]},
             {"fail": [3], "repair": [3]}, {}]


def test_each_scenario_matches_a_separate_call(workspace):
    mid = _open(workspace, "TwoBlockRedundancy")
    many = failure_effects_many(workspace, mid, SCENARIOS, include_log=True)
    for scenario, row in zip(SCENARIOS, many):
        alone = failure_effects(workspace, mid, fail=scenario.get("fail"),
                                repair=scenario.get("repair"))
        assert {k: row[k] for k in alone} == alone


def test_scenarios_do_not_leak_into_each_other(workspace):
    mid = _open(workspace, "TwoBlockRedundancy")
    rows = failure_effects_many(workspace, mid, [{"fail": [3]}, {}, {"fail": [3]}])
    assert rows[0]["system_status"] == rows[2]["system_status"] != rows[1]["system_status"]
    assert rows[1]["system_status"] == "Green"


def test_the_log_text_is_only_returned_on_request(workspace):
    mid = _open(workspace, "TwoBlockRedundancy")
    assert "log" not in failure_effects_many(workspace, mid, [{"fail": [3]}])[0]
    assert "log" in failure_effects_many(workspace, mid, [{"fail": [3]}],
                                         include_log=True)[0]


def test_a_bad_scenario_is_reported_and_the_rest_still_run(workspace):
    mid = _open(workspace, "TwoBlockRedundancy")
    rows = failure_effects_many(workspace, mid, [
        {"fail": [3]}, {"fail": ["no such block"]}, {"phase": 2}, {"fail": [4]}])
    assert "error" not in rows[0] and "error" not in rows[3]
    assert "error" in rows[1] and "no phases" in rows[2]["error"]
    assert rows[3]["system_status"]


def test_too_many_scenarios_are_refused(workspace):
    mid = _open(workspace, "TwoBlockRedundancy")
    with pytest.raises(AnalysisError, match="limit"):
        failure_effects_many(workspace, mid, [{}] * (MAX_FEV_SCENARIOS + 1))


def test_the_tool_returns_the_scenarios(tmp_path):
    from raptor.mcp.tools_model import open_model
    from raptor.mcp.tools_run import failure_effects_batch
    _use_workspace(tmp_path)
    mid = open_model("TwoBlockRedundancy").structured_content["model_id"]
    r = failure_effects_batch(mid, SCENARIOS)
    data = r.structured_content
    assert (data["count"], data["failed"]) == (5, 0)
    assert [s["index"] for s in data["scenarios"]] == list(range(5))


@pytest.mark.parametrize("bad", [[], [3], [{"fail": [3], "extra": 1}]])
def test_the_tool_checks_its_input(tmp_path, bad):
    from raptor.mcp.tools_model import open_model
    from raptor.mcp.tools_run import failure_effects_batch
    _use_workspace(tmp_path)
    mid = open_model("TwoBlockRedundancy").structured_content["model_id"]
    assert failure_effects_batch(mid, bad).is_error


# --- spec files -------------------------------------------------------------

SPEC = {"blocks": [{"id": 1, "name": "A", "fail_dist": 19,
                    "fail_params": [100, 0, 0], "repair_dist": 19,
                    "repair_params": [5, 0, 0]}],
        "nodes": [{"id": 1, "type": "start", "name": "S"},
                  {"id": 2, "type": "end", "name": "E"}],
        "links": [{"from": "S", "to": "A"}, {"from": "A", "to": "E"}]}


def test_build_model_reads_a_spec_file_in_the_workspace(tmp_path):
    from raptor.mcp.tools_model import build_model
    _use_workspace(tmp_path)
    (tmp_path / "big.json").write_text(json.dumps(SPEC))
    r = build_model("from file", spec_path="big.json")
    assert not r.is_error and r.structured_content["model_id"]


@pytest.mark.parametrize("path", ["../outside.json", "/etc/passwd", "~/x.json",
                                  "missing.json"])
def test_build_model_refuses_other_paths(tmp_path, path):
    from raptor.mcp.tools_model import build_model
    _use_workspace(tmp_path)
    (tmp_path.parent / "outside.json").write_text(json.dumps(SPEC))
    assert build_model("x", spec_path=path).is_error


def test_build_model_needs_exactly_one_source(tmp_path):
    from raptor.mcp.tools_model import build_model
    _use_workspace(tmp_path)
    (tmp_path / "s.json").write_text(json.dumps(SPEC))
    assert build_model("x").is_error
    assert build_model("x", spec=SPEC, spec_path="s.json").is_error


def test_build_model_refuses_a_spec_file_that_is_not_an_object(tmp_path):
    from raptor.mcp.tools_model import build_model
    _use_workspace(tmp_path)
    (tmp_path / "s.json").write_text("[1, 2]")
    assert build_model("x", spec_path="s.json").is_error


# --- core defaults ----------------------------------------------------------

def _rbd(blocks):
    rows = lambda n: SimpleNamespace(rows=[{}] * n)  # noqa: E731
    return SimpleNamespace(sections={"BLOCK_DATA": rows(blocks),
                                     "EVENT_DATA": rows(0),
                                     "NODE_DATA": rows(0)})


def test_a_caller_s_workers_are_used_as_given():
    from raptor.mcp.tools_run import resolve_workers
    assert resolve_workers(1, _rbd(5000), 1000) == (1, False)
    assert resolve_workers(3, _rbd(5), 5) == (3, False)


def test_a_large_model_run_many_times_gets_several_cores(monkeypatch):
    from raptor.mcp import tools_run
    monkeypatch.setattr(tools_run.os, "cpu_count", lambda: 16)
    assert tools_run.resolve_workers(None, _rbd(600), 50) == (8, True)
    monkeypatch.setattr(tools_run.os, "cpu_count", lambda: 1)
    assert tools_run.resolve_workers(None, _rbd(600), 50) == (2, True)


@pytest.mark.parametrize("blocks, runs", [(499, 50), (600, 19), (10, 1000)])
def test_small_models_and_short_batches_stay_sequential(blocks, runs):
    from raptor.mcp.tools_run import resolve_workers
    assert resolve_workers(None, _rbd(blocks), runs) == (1, False)


def test_simulate_reports_the_workers_it_used(tmp_path):
    from raptor.mcp.tools_model import open_model
    from raptor.mcp.tools_run import simulate
    _use_workspace(tmp_path)
    mid = open_model("MotorDemo").structured_content["model_id"]
    data = asyncio.run(simulate(mid, num_runs=2, confirm_long_run=True)).structured_content
    assert (data["workers"], data["workers_auto"]) == (1, False)


def test_run_status_shows_the_phase(tmp_path):
    from raptor.mcp.tools_model import open_model
    from raptor.mcp.tools_run import run_status, start_run
    _use_workspace(tmp_path)
    mid = open_model("MotorDemo").structured_content["model_id"]
    started = start_run(mid, num_runs=2).structured_content
    assert started["workers"] == 1 and "phase" in started
    for _ in range(300):
        data = run_status(started["job_id"]).structured_content
        if data["state"] not in ("queued", "running"):
            break
        time.sleep(0.05)
    assert data["state"] == "done" and data["phase"] == "done"
