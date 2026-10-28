"""Tests for the MCP server's tool annotations, structured error
contract, progress/cancellation plumbing, enum schemas, read-only
semantics, resource caps and packaged docs.
"""
from __future__ import annotations

import asyncio
import threading
import time
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parent.parent.parent
GOLDEN = REPO / "tests" / "golden"


def _tools() -> dict:
    from raptor.mcp import server, tools_model, tools_run, tools_metrics, tools_tables  # noqa: F401
    try:
        from raptor.mcp import tools_view  # noqa: F401
    except ImportError:
        pass
    return {t.name: t for t in asyncio.run(server.mcp.list_tools())}


def _use_workspace(tmp_path, **flags):
    from raptor.mcp.server import mcp
    mcp._raptor_args = type("A", (), {"workspace": str(tmp_path), **flags})()
    mcp._raptor_ws = None


def _open_motor() -> str:
    from raptor.mcp.tools_model import open_model
    r = open_model("MotorDemo")
    assert not r.is_error, _text(r)
    return r.structured_content["model_id"]


def _text(result) -> str:
    return " ".join(c.text for c in result.content if getattr(c, "type", "") == "text")


# --------------------------------------------------------------------------
# Annotations
# --------------------------------------------------------------------------

READ_ONLY = {
    "ping", "list_distributions", "list_models", "describe_model",
    "validate_model", "estimate_runtime", "get_report",
    "single_point_failures", "failure_effects", "get_timeseries",
    "search_docs", "read_doc", "workspace_info", "list_annotations",
    "describe_metrics",
}
DESTRUCTIVE = {"save_model_as", "close_model", "delete_element",
               "delete_elements", "delete_link", "undo_last_edit",
               "redo_last_edit", "delete_file", "delete_annotation"}


class TestAnnotations:
    def test_every_tool_is_annotated(self):
        for name, tool in _tools().items():
            a = tool.annotations
            assert a is not None, name
            assert a.title, name
            assert a.read_only_hint is not None, name
            assert a.open_world_hint is False, name

    def test_read_only_tools(self):
        tools = _tools()
        for name in READ_ONLY:
            assert tools[name].annotations.read_only_hint is True, name

    def test_destructive_tools(self):
        tools = _tools()
        for name in DESTRUCTIVE:
            a = tools[name].annotations
            assert a.read_only_hint is False and a.destructive_hint is True, name

    def test_mutating_tools_are_not_read_only(self):
        tools = _tools()
        for name in ("build_model", "update_element", "simulate", "connect"):
            assert tools[name].annotations.read_only_hint is False, name

    def test_only_destructive_set_is_destructive(self):
        for name, tool in _tools().items():
            if tool.annotations.destructive_hint:
                assert name in DESTRUCTIVE, name


# --------------------------------------------------------------------------
# Schemas
# --------------------------------------------------------------------------

class TestEnumSchemas:
    @pytest.mark.parametrize("tool,param,values", [
        ("describe_model", "detail", {"summary", "full"}),
        ("save_model_as", "format", {"rbds", "rbd"}),
        ("get_report", "format", {"markdown", "raw"}),
        ("get_timeseries", "series", {"availability", "capacity", "cost"}),
        ("manage_rates", "action", {"list", "add", "update", "delete"}),
        ("add_node", "type", {"start", "regular", "end", "in", "out"}),
    ])
    def test_enum_in_input_schema(self, tool, param, values):
        prop = _tools()[tool].input_schema["properties"][param]
        found = set(prop.get("enum") or [])
        if not found:
            for alt in prop.get("anyOf", []):
                found |= set(alt.get("enum") or [])
        assert found == values

    def test_long_tools_do_not_expose_ctx_as_input(self):
        for name in ("simulate", "sweep_parameter", "compare_models"):
            props = _tools()[name].input_schema["properties"]
            assert "ctx" not in props, name


# --------------------------------------------------------------------------
# Structured errors
# --------------------------------------------------------------------------

class TestStructuredErrors:
    def test_fail_shape(self):
        from raptor.mcp.result import fail
        r = fail("bad", code="not_found", hint="try list_models")
        assert r.is_error
        err = r.structured_content["error"]
        assert err == {"code": "not_found", "message": "bad",
                       "retryable": False, "hint": "try list_models"}
        assert "try list_models" in _text(r)

    def test_unknown_model_is_not_found(self, tmp_path):
        _use_workspace(tmp_path)
        from raptor.mcp.tools_model import describe_model
        r = describe_model("m_nope")
        assert r.is_error
        err = r.structured_content["error"]
        assert err["code"] == "not_found"
        assert "list_models" in err["hint"]

    def test_bad_value_is_invalid_argument(self, tmp_path):
        _use_workspace(tmp_path)
        from raptor.mcp.tools_model import create_model
        r = create_model("x", template="bogus")
        assert r.is_error
        assert r.structured_content["error"]["code"] == "invalid_argument"

    def test_read_only_is_permission_denied(self, tmp_path):
        _use_workspace(tmp_path, read_only=True)
        from raptor.mcp.tools_model import create_model
        r = create_model("x")
        assert r.structured_content["error"]["code"] == "permission_denied"

    def test_unexpected_exception_is_internal_and_generic(self):
        from raptor.mcp.result import guarded

        @guarded
        def boom():
            raise ZeroDivisionError("secret implementation detail")

        r = boom()
        assert r.is_error
        assert r.structured_content["error"]["code"] == "internal"
        assert "secret implementation detail" not in _text(r)
        assert "ZeroDivisionError" not in _text(r)

    def test_async_guarded(self):
        from raptor.mcp.result import guarded

        @guarded
        async def aboom():
            raise ValueError("nope")

        r = asyncio.run(aboom())
        assert r.structured_content["error"]["code"] == "invalid_argument"
        assert "nope" in _text(r)

    def test_keyerror_message_unquoted(self):
        from raptor.mcp.result import guarded

        @guarded
        def k():
            raise KeyError("missing thing")

        assert _text(k()).startswith("missing thing")

    def test_codes_are_documented(self):
        from raptor.mcp.result import ERROR_CODES, _CLASSIFY
        for _t, code, _h, _r in _CLASSIFY:
            assert code in ERROR_CODES


# --------------------------------------------------------------------------
# Read-only semantics
# --------------------------------------------------------------------------

class TestReadOnly:
    def test_can_open_and_simulate_but_not_edit(self, tmp_path):
        _use_workspace(tmp_path, read_only=True)
        from raptor.mcp.tools_model import open_model, update_element, manage_rates
        from raptor.mcp.tools_run import simulate
        opened = open_model("MotorDemo")
        assert not opened.is_error, _text(opened)
        mid = opened.structured_content["model_id"]
        assert asyncio.run(simulate(mid, num_runs=1, confirm_long_run=True)).is_error is False
        assert update_element(mid, "x", {"a": 1}).is_error
        assert not manage_rates(mid, action="list").is_error
        assert manage_rates(mid, action="add", name="R", expr="1").is_error


# --------------------------------------------------------------------------
# Progress and cancellation
# --------------------------------------------------------------------------

class _FakeCtx:
    def __init__(self):
        self.calls = []

    async def report_progress(self, progress, total=None, message=None):
        self.calls.append((progress, total, message))


class TestRunJob:
    def test_returns_result_without_ctx(self):
        from raptor.mcp.jobs import run_job
        assert asyncio.run(run_job(None, lambda a, p: 42)) == 42

    def test_propagates_exception(self):
        from raptor.mcp.jobs import run_job

        def work(abort, progress):
            raise ValueError("bad input")

        with pytest.raises(ValueError, match="bad input"):
            asyncio.run(run_job(None, work))

    def test_reports_progress(self, monkeypatch):
        from raptor.mcp import jobs
        monkeypatch.setattr(jobs, "HEARTBEAT_S", 0.1)
        ctx = _FakeCtx()

        def work(abort, progress):
            for i in range(1, 6):
                progress(i, 5)
                time.sleep(0.1)
            return "done"

        assert asyncio.run(jobs.run_job(ctx, work, label="Sim")) == "done"
        assert ctx.calls, "no progress notifications sent"
        assert all(m.startswith("Sim") for _p, _t, m in ctx.calls)
        assert ctx.calls[-1][1] == 5

    def test_cancel_sets_abort_and_returns_promptly(self):
        from raptor.mcp.jobs import run_job
        stopped = threading.Event()
        started = threading.Event()

        def work(abort, progress):
            started.set()
            while not abort.is_set():
                time.sleep(0.01)
            stopped.set()

        async def main():
            t0 = time.monotonic()
            task = asyncio.create_task(run_job(None, work))
            while not started.is_set():
                await asyncio.sleep(0.01)
            task.cancel()
            with pytest.raises(asyncio.CancelledError):
                await task
            return time.monotonic() - t0

        elapsed = asyncio.run(main())
        assert stopped.wait(2.0), "engine thread never saw abort"
        assert elapsed < 2.0


class TestEngineAbort:
    def test_api_run_honours_abort_and_progress(self, tmp_path):
        _use_workspace(tmp_path)
        from raptor.mcp import context
        from raptor.api.run import RunCancelledError, run
        ws = context.workspace()
        mid = _open_motor()

        seen = []
        res = run(ws, mid, num_runs=2, confirm_long_run=True,
                  progress=lambda d, t: seen.append((d, t)))
        assert res.num_runs == 2
        assert seen and seen[-1][1] == 2

        aborted = threading.Event()
        aborted.set()
        with pytest.raises(RunCancelledError):
            run(ws, mid, num_runs=50, confirm_long_run=True, abort=aborted)

    def test_sweep_cancelled_between_values(self, tmp_path):
        _use_workspace(tmp_path)
        from raptor.mcp import context
        from raptor.api.analyse import sweep
        from raptor.api.run import RunCancelledError
        ws = context.workspace()
        mid = _open_motor()
        ev = threading.Event()
        ev.set()
        with pytest.raises(RunCancelledError):
            sweep(ws, mid, "Block:1", "fail_params[0]", [1, 2],
                  num_runs=1, abort=ev)
        # nothing left behind
        assert [h for h in ws.list_models() if h["name"].startswith("sweep_")] == []


# --------------------------------------------------------------------------
# Resources
# --------------------------------------------------------------------------

class TestResources:
    def test_unknown_chapter_is_not_found_not_whole_book(self):
        from raptor.mcp.resources import get_chapter
        with pytest.raises(FileNotFoundError) as e:
            get_chapter("no-such-chapter")
        assert "available" in str(e.value)

    def test_chapter_path_traversal_rejected(self):
        from raptor.mcp.resources import get_chapter
        with pytest.raises(FileNotFoundError):
            get_chapter("../../pyproject")

    def test_run_section_traversal_and_unknown_rejected(self, tmp_path):
        _use_workspace(tmp_path)
        from raptor.mcp.resources import get_run_section
        with pytest.raises(FileNotFoundError):
            get_run_section("../..", "final_results")
        with pytest.raises(FileNotFoundError):
            get_run_section("r_x", "../../etc/passwd")

    def test_run_section_is_capped(self, tmp_path, monkeypatch):
        _use_workspace(tmp_path)
        from raptor.mcp import context, resources
        ws = context.workspace()
        d = ws.root / "runs" / "r_big"
        d.mkdir(parents=True)
        (d / "events.txt").write_text("x" * 5000)
        monkeypatch.setattr(resources, "MAX_RESOURCE_CHARS", 100)
        out = resources.get_run_section("r_big", "events")
        assert out.startswith("x" * 100) and "truncated" in out and len(out) < 400

    def test_index_resources_and_mime_types(self, tmp_path):
        _use_workspace(tmp_path)
        from raptor.mcp import server, resources  # noqa: F401
        res = {str(r.uri): r for r in asyncio.run(server.mcp.list_resources())}
        assert "raptor://models" in res and "raptor://runs" in res
        assert res["raptor://models"].mime_type == "application/json"
        templates = {t.uri_template: t for t in asyncio.run(server.mcp.list_resource_templates())}
        assert templates["raptor://renders/{name}"].mime_type == "image/png"

    def test_runs_index_lists_completed_run(self, tmp_path):
        _use_workspace(tmp_path)
        import json
        from raptor.mcp import resources
        from raptor.mcp.tools_model import open_model
        from raptor.mcp.tools_run import simulate
        mid = open_model("MotorDemo").structured_content["model_id"]
        run_id = asyncio.run(simulate(mid, num_runs=1, confirm_long_run=True)
                             ).structured_content["run_id"]
        rows = json.loads(resources.list_runs_resource())
        assert [r["run_id"] for r in rows] == [run_id]
        models = json.loads(resources.list_models_resource())
        assert models[0]["model_id"] == mid


def test_round_two_mcp_guards(tmp_path):
    """Delete-rate, run-count cap, live-file deletion and invalid links."""
    from raptor.mcp import server, tools_model, tools_run, tools_tables, tools_view  # noqa: F401
    _use_workspace(tmp_path)
    call = lambda n, a: asyncio.run(server.mcp.call_tool(n, a))  # noqa: E731
    sc = lambda r: r.structured_content  # noqa: E731

    mid = sc(call("create_model", {"name": "guards"}))["model_id"]
    sc(call("manage_rates", {"model_id": mid, "action": "add",
                             "name": "r1", "expr": "1/1000"}))
    assert sc(call("manage_rates", {"model_id": mid, "action": "delete",
                                    "name": "r1"}))["deleted"] == "r1"

    sc(call("configure_system", {"model_id": mid, "num_runs": 6000}))
    cap = call("simulate", {"model_id": mid, "run_length": 1})
    assert cap.is_error and "exceeds the server cap" in _text(cap)
    zero = call("simulate", {"model_id": mid, "num_runs": 0})
    assert zero.is_error and "at least 1" in _text(zero)

    bad = call("connect", {"model_id": mid,
                           "from_element": ["Widget", 1],
                           "to_element": ["Node", 3]})
    assert bad.is_error and "Unknown element kind" in _text(bad)

    run_id = sc(call("simulate", {"model_id": mid, "num_runs": 2,
                                  "run_length": 10}))["run_id"]
    other = sc(call("create_model", {"name": "other"}))["model_id"]
    crossed = call("get_table", {"model_id": other, "run_id": run_id,
                                 "table": "results.summary"})
    assert crossed.is_error and "belongs to model" in _text(crossed)
    one = sc(call("get_report", {"run_id": run_id,
                                 "section": "end_of_run", "run_number": 1}))
    assert one["lines"][0].startswith("***Run_1")
    bad_run = call("get_report", {"run_id": run_id,
                                  "section": "end_of_run", "run_number": 99})
    assert bad_run.is_error and "out of range" in _text(bad_run)
    ts = sc(call("get_timeseries", {"run_id": run_id, "series": "availability",
                                    "runs": "all", "points": 3}))
    assert all(len(v) == len(ts["t"]) for v in ts["values"].values())

    handle = server.mcp._raptor_ws.get(mid)
    rel = Path(handle.path).relative_to(server.mcp._raptor_ws.root)
    live = call("delete_file", {"path": str(rel)})
    assert live.is_error and "close_model" in _text(live)


def test_round_three_legacy_undo_and_owned_paths(tmp_path):
    from raptor.mcp import server, tools_model, tools_run, tools_tables, tools_view  # noqa: F401
    _use_workspace(tmp_path)
    call = lambda n, a: asyncio.run(server.mcp.call_tool(n, a))  # noqa: E731
    sc = lambda r: r.structured_content  # noqa: E731

    mid = sc(call("open_model", {
        "path": "MotorDemo"}))["model_id"]
    sc(call("update_element", {"model_id": mid, "selector": "Casing",
                               "patch": {"fail_params[0]": 2000}}))
    undo = call("undo_last_edit", {"model_id": mid})
    assert not undo.is_error, _text(undo)
    redo = call("redo_last_edit", {"model_id": mid})
    assert not redo.is_error, _text(redo)

    a = sc(call("create_model", {"name": "a"}))["model_id"]
    b = sc(call("create_model", {"name": "b"}))["model_id"]
    over = call("save_model_as", {"model_id": a,
                                  "path": f"models/{b}/model.rbds"})
    assert over.is_error and "refusing to overwrite" in _text(over)
    run = sc(call("simulate", {"model_id": a, "num_runs": 1,
                               "run_length": 10}))["run_id"]
    bad = call("delete_file", {"path": f"runs/{run}/summary.json"})
    assert bad.is_error and "run artefacts" in _text(bad)


def test_round_four_validation_edges(tmp_path):
    from raptor.mcp import server, tools_model, tools_run, tools_tables, tools_view  # noqa: F401
    _use_workspace(tmp_path)
    call = lambda n, a: asyncio.run(server.mcp.call_tool(n, a))  # noqa: E731
    sc = lambda r: r.structured_content  # noqa: E731

    spec = {
        "blocks": [{"id": 1, "name": "A", "fail_dist": 13,
                    "fail_params": [100]}],
        "nodes": [{"id": 2, "name": "S", "type": "start"},
                  {"id": 3, "name": "E", "type": "end"}],
        "links": [{"from": "S", "to": "A"}, {"from": "A", "to": "E"}],
    }
    mid = sc(call("build_model", {"name": "edges", "spec": spec,
                                  "auto_layout": False}))["model_id"]
    table = sc(call("get_table", {"model_id": mid, "table": "inputs.nodes"}))
    assert table["headers"], "built model input nodes must have headers"

    assert sc(call("add_node", {"model_id": mid, "name": "J",
                                "type": "regular"}))["name"] == "J"
    dup = call("add_node", {"model_id": mid, "name": "J",
                            "type": "regular"})
    assert dup.is_error and "already exists" in _text(dup)

    bad_pm = call("configure_maintenance", {"model_id": mid, "selector": "A",
                                            "trigger_name": "NoTrig"})
    assert bad_pm.is_error and "not defined" in _text(bad_pm)
    no_pool = call("configure_sparing", {"model_id": mid, "selector": "A",
                                         "mode": "pool"})
    assert no_pool.is_error and "requires spare_pool" in _text(no_pool)

    zero_est = call("estimate_runtime", {"model_id": mid, "num_runs": 0})
    assert zero_est.is_error and "at least 1" in _text(zero_est)


def test_round_three_read_only_extract_is_denied(tmp_path):
    from raptor.mcp import server, tools_model, tools_run, tools_tables, tools_view  # noqa: F401
    _use_workspace(tmp_path, read_only=True)
    call = lambda n, a: asyncio.run(server.mcp.call_tool(n, a))  # noqa: E731
    mid = call("open_model", {
        "path": "HierarchyExample"}
    ).structured_content["model_id"]
    out = call("extract_hierarchy", {"model_id": mid, "hierarchy": "500path"})
    assert out.is_error and "read-only" in _text(out)


# --------------------------------------------------------------------------
# Docs shipped with the package
# --------------------------------------------------------------------------

class TestPackagedDocs:
    def test_pyproject_ships_mcp_doc_sources(self):
        text = (REPO / "pyproject.toml").read_text()
        assert "new_docs/chapters/*.adoc" in text
        assert "docs/RBDS_FORMAT.md" in text
        assert "new_docs/book.adoc" in text
