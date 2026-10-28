"""Security review follow-ups: root input validation and MCP hardening.

Each test pins one finding of the MCP security review: control characters in
names and text, out-of-range or wrongly typed values, the unbounded
background-job registry, unauthenticated loopback HTTP and unlimited token
guessing, error messages and listings that expose server paths, and
unvalidated paging and batch sizes.
"""
from __future__ import annotations

import asyncio
import threading
import time
from types import SimpleNamespace

import pytest

pytest.importorskip("mcp")

from raptor.api.build import BuildError, _check_scalar, _require_dist_code  # noqa: E402
from raptor.core.fileio.rbd import load_rbd  # noqa: E402
from raptor.core.input_rules import check, has_blank, new_violations  # noqa: E402
from raptor.gui.ui.expr_eval import ExpressionError, eval_expression  # noqa: E402
from raptor.mcp.server import mcp  # noqa: E402


# ----------------------------------------------------------------- fixtures

@pytest.fixture
def ws(tmp_path):
    mcp._raptor_args = SimpleNamespace(
        workspace=str(tmp_path / "ws"), read_only=False, max_runs=5000,
        allow_write=[], allow_read=[], retain_days=30)
    mcp._raptor_ws = None
    from raptor.mcp import (tools_data, tools_metrics, tools_model,  # noqa: F401
                            tools_run, tools_tables)
    return tmp_path


def call(tool, **args):
    return asyncio.run(mcp.call_tool(tool, args))


def err(result):
    assert result.structured_content.get("error"), result.structured_content
    return result.structured_content["error"]


def new_model(**kw):
    return call("create_model", name="t", template="series", n=3,
                **kw).structured_content["model_id"]


MOTOR = "tests/golden/MotorDemo/input.rbd"


# --------------------------------------------- root input rules (all fronts)

@pytest.mark.parametrize("name", ["a b", "a\nb", "a\tb", "a b", "a‮b",
                                  "a​b", "a\x00b"])
def test_names_with_any_blank_or_invisible_character_are_blank(name):
    assert has_blank(name)


@pytest.mark.parametrize("name", ["Pump_A", "é", "<b>", "a|b"])
def test_ordinary_names_are_not_blank(name):
    assert not has_blank(name)


def test_a_block_name_with_a_newline_is_a_rule_violation():
    rbd = load_rbd(MOTOR)
    before = rbd
    import copy
    after = copy.deepcopy(rbd)
    next(r for r in after.sections["BLOCK_DATA"].rows
         if r.get("ID") != "0")["blockName"] = "a\nb"
    rules = {v.rule for v in new_violations(before, after)}
    assert "block.name" in rules or any(r.startswith("value.control_char")
                                        for r in rules)


def test_unknown_distribution_code_is_a_value_error_and_a_refused_edit():
    import copy
    rbd = load_rbd(MOTOR)
    bad = copy.deepcopy(rbd)
    row = next(r for r in bad.sections["BLOCK_DATA"].rows if r.get("ID") != "0")
    row["FDist"] = "-5"
    found = [v for v in check(bad) if v.rule.startswith("value.dist_code")]
    assert found and found[0].severity == "error"
    assert new_violations(rbd, bad), "an edit that introduces it is refused"


def test_non_finite_parameter_and_non_numeric_coordinate_are_flagged():
    import copy
    bad = copy.deepcopy(load_rbd(MOTOR))
    row = next(r for r in bad.sections["BLOCK_DATA"].rows if r.get("ID") != "0")
    row["FParam1"] = "nan"
    row["xPos"] = "{'a': 1}"
    rules = {v.rule for v in check(bad)}
    assert "value.param.FParam1" in rules and "value.coord.xPos" in rules


def test_real_models_have_no_value_violations():
    for path in ("tests/golden/MotorDemo/input.rbd",
                 "tests/golden/Cascade/input.rbd",
                 "tests/golden/Phasing3/input.rbd"):
        assert [v for v in check(load_rbd(path)) if v.kind == "value"] == []


# ------------------------------------------------------ root field editing

@pytest.mark.parametrize("value", [{"a": 1}, [1, 2], None, b"x", {1}])
def test_a_cell_holds_one_number_or_one_piece_of_text(value):
    with pytest.raises(BuildError):
        _check_scalar("xPos", value)


@pytest.mark.parametrize("text", ["a\nb", "a\tb", "a\x00", "a‮", "x" * 10_001])
def test_text_with_control_characters_or_too_long_is_refused(text):
    with pytest.raises(BuildError):
        _check_scalar("blockName", text)


@pytest.mark.parametrize("code", [-5, 0, 23, 10 ** 30, 1.5, "abc", float("nan")])
def test_distribution_codes_outside_the_engine_are_refused(code):
    with pytest.raises(BuildError):
        _require_dist_code("FDist", code)


def test_a_delay_may_be_zero_but_a_failure_law_may_not():
    _require_dist_code("Pre-LDTDist", 0)
    with pytest.raises(BuildError):
        _require_dist_code("FDist", 0)


@pytest.mark.parametrize("expr", ["(-8)**0.5", "0**-1", "0.0**-1"])
def test_expression_edge_cases_are_expression_errors(expr):
    with pytest.raises(ExpressionError):
        eval_expression(expr)


# --------------------------------------------------- MCP: edits are checked

def test_update_element_refuses_bad_values(ws):
    mid = new_model()
    for patch in ({"x": {"a": 1}}, {"fail_dist": 10 ** 30}, {"fail_dist": -5},
                  {"name": "a\nb"}, {"fail_params": [1, 2, 3, 4, 5]}):
        assert err(call("update_element", model_id=mid, selector="Block:1",
                        patch=patch))["code"] == "invalid_argument", patch
    assert call("validate_model", model_id=mid).structured_content["runnable"]


def test_add_block_refuses_control_characters_in_a_name(ws):
    mid = new_model()
    for name in ("a\nb", "a\tb", "a‮b"):
        assert err(call("add_block", model_id=mid, name=name))["code"] == "invalid_argument"


def test_units_and_comments_refuse_a_newline(ws):
    mid = new_model()
    assert err(call("configure_system", model_id=mid, units="h\nours"))["code"] \
        == "invalid_argument"


def test_a_rbd_round_trip_never_gains_a_block_from_a_name(ws):
    """The original finding: a newline in a name became an extra block."""
    mid = new_model()
    call("update_element", model_id=mid, selector="Block:3",
         patch={"name": "ok"})
    saved = call("save_model_as", model_id=mid, path="p.rbd",
                 format="rbd").structured_content["path"]
    assert len([r for r in load_rbd(saved).sections["BLOCK_DATA"].rows
                if r.get("ID") != "0"]) == 3


def test_model_names_are_validated(ws):
    for name in ("a\nb", "x" * 101, "  "):
        assert err(call("create_model", name=name))["code"] in (
            "invalid_argument", "precondition_failed")
    assert call("create_model", name="Good name 1").structured_content["model_id"]


def test_expression_rates_report_clean_errors(ws):
    mid = new_model()
    for expr in ("0**-1", "(-8)**0.5"):
        e = err(call("manage_rates", model_id=mid, action="add", name="r1",
                     expr=expr))
        assert e["code"] == "invalid_argument", expr


# ------------------------------------------------ MCP: paging and counts

def test_paging_arguments_are_validated(ws):
    mid = new_model()
    for tool, args in (("list_models", {"offset": -1}),
                       ("list_models", {"limit": 0}),
                       ("list_models", {"limit": -5}),
                       ("get_table", {"model_id": mid, "table": "inputs.blocks", "offset": -1}),
                       ("read_doc", {"name": "rbds", "offset": -3})):
        assert err(call(tool, **args))["code"] == "invalid_argument", (tool, args)
    assert call("list_models", limit=10 ** 6).structured_content["models"] is not None


def test_bulk_edits_have_a_size_limit(ws):
    from raptor.api.limits import MAX_BATCH_ITEMS
    mid = new_model()
    big = [{"kind": "block", "name": f"b{i}"} for i in range(MAX_BATCH_ITEMS + 1)]
    e = err(call("add_elements", model_id=mid, elements=big))
    assert e["code"] == "invalid_argument" and "build_model" in e["message"]
    e = err(call("delete_elements", model_id=mid,
                 selectors=["Block:1"] * (MAX_BATCH_ITEMS + 1)))
    assert e["code"] == "invalid_argument"


# -------------------------------------------- MCP: no server paths leaked

def test_open_model_answers_the_same_for_outside_and_missing_files(ws):
    outside = call("open_model", path="../../../../etc/passwd")
    missing = call("open_model", path="../../../../etc/no_such_file_xyz")
    a, b = err(outside), err(missing)
    assert a["code"] == b["code"] == "not_found"
    assert "/etc" not in a["message"].replace("../../../../etc/passwd", "")
    assert "refusing" not in a["message"]


def test_path_errors_do_not_name_the_workspace(ws):
    root = str((ws / "ws").resolve())
    mid = new_model()
    for tool, args in (("save_model_as", {"model_id": mid, "path": "../x.rbds"}),
                       ("delete_file", {"path": "../x.rbds"}),
                       ("delete_file", {"path": "nope.rbds"})):
        assert root not in err(call(tool, **args))["message"], tool


def test_listings_do_not_expose_install_paths(ws):
    import json
    ex = call("list_models").structured_content["examples"]
    assert ex and all(set(e) == {"name"} for e in ex)
    from raptor.mcp.resources import list_examples_resource
    assert all(set(e) == {"name"} for e in json.loads(list_examples_resource()))


def test_model_listings_show_a_source_without_server_directories(ws):
    import json
    from pathlib import Path
    call("open_model", path="MotorDemo")
    new_model()
    rows = call("list_models").structured_content["models"]
    assert rows and not any(Path(r["source"]).is_absolute() for r in rows)
    from raptor.mcp.resources import list_models_resource
    assert not any(Path(r["source"]).is_absolute()
                   for r in json.loads(list_models_resource()))


def test_a_new_workspace_is_private(ws):
    import os
    import stat
    from raptor.mcp import context
    root = context.workspace().root
    assert stat.S_IMODE(os.stat(root).st_mode) & 0o077 == 0


# --------------------------------------------------- background job limits

def _reset_jobs():
    from raptor.mcp import async_runs
    with async_runs._LOCK:
        async_runs._JOBS.clear()


def test_background_jobs_are_capped_and_share_the_cpu_slots():
    from raptor.api.limits import MAX_BACKGROUND_JOBS, MAX_CONCURRENT_JOBS
    from raptor.api.workspace import JobLimitExceeded
    from raptor.mcp import async_runs
    _reset_jobs()
    release = threading.Event()
    running = {"now": 0, "peak": 0}
    lock = threading.Lock()

    def work(abort, progress):
        with lock:
            running["now"] += 1
            running["peak"] = max(running["peak"], running["now"])
        release.wait(10)
        with lock:
            running["now"] -= 1
        return SimpleNamespace(run_id="r", wall_seconds=0.0, num_runs=1)

    jobs = [async_runs.start(work, model_id="m", total=1)
            for _ in range(MAX_BACKGROUND_JOBS)]
    time.sleep(0.5)
    try:
        assert running["peak"] <= MAX_CONCURRENT_JOBS
        states = sorted(j.state for j in jobs)
        assert states.count("running") == MAX_CONCURRENT_JOBS
        assert states.count("queued") == MAX_BACKGROUND_JOBS - MAX_CONCURRENT_JOBS
        with pytest.raises(JobLimitExceeded):
            async_runs.start(work, model_id="m", total=1)
    finally:
        release.set()
        for j in jobs:
            j.abort.set()
        deadline = time.time() + 10
        while any(j.state in ("queued", "running") for j in jobs) \
                and time.time() < deadline:
            time.sleep(0.05)
        _reset_jobs()
    assert all(j.state in ("done", "cancelled") for j in jobs)


def test_a_queued_job_cancelled_before_it_starts_finishes(ws):
    from raptor.mcp import async_runs
    _reset_jobs()
    release = threading.Event()

    def blocker(abort, progress):
        release.wait(10)
        return SimpleNamespace(run_id=None, wall_seconds=0, num_runs=0)

    first = [async_runs.start(blocker, model_id="m", total=1) for _ in range(2)]
    queued = async_runs.start(blocker, model_id="m", total=1)
    time.sleep(0.3)
    try:
        assert queued.state == "queued"
        queued.abort.set()
    finally:
        release.set()
    deadline = time.time() + 10
    while queued.finished is None and time.time() < deadline:
        time.sleep(0.05)
    assert queued.state == "cancelled" and queued.finished is not None
    for j in first:
        j.abort.set()
    _reset_jobs()


def test_start_run_reports_the_limit_as_an_error(ws, monkeypatch):
    from raptor.mcp import async_runs
    release = threading.Event()
    _reset_jobs()
    for _ in range(4):
        async_runs.start(lambda a, p: release.wait(10) or SimpleNamespace(
            run_id=None, wall_seconds=0, num_runs=0), model_id="m", total=1)
    mid = new_model()
    try:
        e = err(call("start_run", model_id=mid, num_runs=1))
        assert e["code"] == "limit_exceeded" and e["retryable"] is True
    finally:
        release.set()
        for j in list(async_runs._JOBS.values()):
            j.abort.set()
        time.sleep(0.5)
        _reset_jobs()


# --------------------------------------------------------------- HTTP auth

def _args(**kw):
    base = dict(host="127.0.0.1", port=8765, path="/mcp", token_file=None,
                token_env=None, tls_cert=None, tls_key=None,
                allow_insecure_http=False, allow_unauthenticated=False,
                allow_origin=[])
    base.update(kw)
    return SimpleNamespace(**base)


def test_http_needs_a_token_unless_explicitly_waived():
    from raptor.mcp.http_transport import check_args
    assert "without a token" in check_args(_args())
    assert check_args(_args(allow_unauthenticated=True)) is None


def test_waiver_does_not_cover_a_network_bind():
    from raptor.mcp.http_transport import check_args
    assert "without a token" in check_args(
        _args(host="0.0.0.0", allow_unauthenticated=True))


def test_short_tokens_are_refused(tmp_path):
    from raptor.mcp.http_transport import MIN_TOKEN_CHARS, check_args
    t = tmp_path / "t"
    t.write_text("x" * (MIN_TOKEN_CHARS - 1))
    assert "too short" in check_args(_args(token_file=str(t)))
    t.write_text("x" * MIN_TOKEN_CHARS)
    assert check_args(_args(token_file=str(t))) is None


def _asgi(app, headers, client="10.0.0.9"):
    sent: list[dict] = []

    async def receive():
        return {"type": "http.request", "body": b"", "more_body": False}

    async def send(message):
        sent.append(message)

    scope = {"type": "http", "headers": headers, "client": (client, 1234)}
    asyncio.run(app(scope, receive, send))
    return next(m["status"] for m in sent if m["type"] == "http.response.start")


def test_repeated_wrong_tokens_lock_the_client_out(monkeypatch):
    from raptor.mcp import http_transport as ht

    async def ok_app(scope, receive, send):
        await send({"type": "http.response.start", "status": 200, "headers": []})
        await send({"type": "http.response.body", "body": b"ok"})

    token = "right-token-0123456789"
    app = ht.BearerTokenMiddleware(ok_app, token)
    good = [(b"authorization", f"Bearer {token}".encode())]
    bad = [(b"authorization", b"Bearer wrong")]
    assert _asgi(app, good) == 200
    for _ in range(ht.MAX_AUTH_FAILURES):
        assert _asgi(app, bad) == 401
    assert _asgi(app, bad) == 429
    assert _asgi(app, good) == 429            # even the right token, for now
    assert _asgi(app, good, client="10.0.0.10") == 200   # others unaffected
    real = time.monotonic
    monkeypatch.setattr(ht.time, "monotonic",
                        lambda: real() + ht.AUTH_WINDOW_S + 1)
    assert _asgi(app, good) == 200            # the window has passed


def test_deeply_nested_arguments_are_refused_without_hanging():
    """A ~200-deep argument tree used to hang the SDK's pydantic JSON
    parser: the request got no response at all while the server stayed
    alive. The transport guard rewrites the over-deep tree to a marker
    that keeps the JSON-RPC envelope, and `call_tool` turns it into the
    normal tool error (with the caller's own request id)."""
    import json

    import mcp_types
    from mcp.server.mcpserver.exceptions import ToolError

    from raptor.mcp.server import _DEPTH_MARKER

    def nest(n):
        root: dict = {}
        cur = root
        for _ in range(n):
            cur["a"] = {}
            cur = cur["a"]
        return root

    msg = mcp_types.jsonrpc_message_adapter.validate_json(json.dumps({
        "jsonrpc": "2.0", "id": 7, "method": "tools/call",
        "params": {"name": "ping", "arguments": {"junk": nest(200)}},
    }), by_name=False)
    assert msg.id == 7
    assert msg.params["arguments"] == {_DEPTH_MARKER: True}

    # Streamable HTTP parses the body itself and calls validate_python;
    # the same guard must apply there.
    body = {"jsonrpc": "2.0", "id": 8, "method": "tools/call",
            "params": {"name": "ping", "arguments": {"junk": nest(200)}}}
    msg2 = mcp_types.jsonrpc_message_adapter.validate_python(body, by_name=False)
    assert msg2.params["arguments"] == {_DEPTH_MARKER: True}

    with pytest.raises(ToolError, match="nested too deeply"):
        asyncio.run(mcp.call_tool("ping", msg.params["arguments"]))
