"""End-to-end acceptance tests for the MCP server.

Runs the MCP server as a subprocess over stdio and speaks JSON-RPC to it.
Marked @slow -- runs outside the watchdog.

  1. TestProtocolAssertions   -- protocol invariants
  2. TestEquivalenceTests     -- engine path vs MCP path
  3. TestScriptedConversation -- the scripted acceptance conversation
"""
from __future__ import annotations

import json
import os
import re
import statistics
import subprocess
import sys
import tempfile
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parent.parent.parent
GOLDEN = REPO / "tests" / "golden"


def _jsonrpc_msg(method: str, params: dict | None = None, *,
                 id: int | None = 1) -> str:
    msg: dict = {"jsonrpc": "2.0", "method": method}
    if id is not None:
        msg["id"] = id
    if params is not None:
        msg["params"] = params
    return json.dumps(msg) + "\n"


def _read_response(proc: subprocess.Popen, timeout: float = 60) -> dict:
    """Read one JSON-RPC response from the server's stdout."""
    import select
    import time
    start = time.monotonic()
    buf = b""
    while time.monotonic() - start < timeout:
        ready, _, _ = select.select([proc.stdout], [], [], 0.5)
        if ready:
            chunk = os.read(proc.stdout.fileno(), 8192)
            if not chunk:
                break
            buf += chunk
            for line in buf.split(b"\n"):
                line = line.strip()
                if not line:
                    continue
                try:
                    obj = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if "jsonrpc" in obj:
                    return obj
    raise TimeoutError(f"No JSON-RPC response within {timeout}s")


class MCPServer:
    """Context manager that starts an MCP server subprocess."""

    def __init__(self, workspace: str | None = None,
                 extra_args: list[str] | None = None):
        self._workspace = workspace or tempfile.mkdtemp()
        self._extra_args = extra_args or []
        self._proc: subprocess.Popen | None = None
        self._msg_id = 0

    def __enter__(self):
        cmd = [
            sys.executable, "-m", "raptor.mcp",
            "--workspace", self._workspace,
            "--no-render",
        ] + self._extra_args
        env = os.environ.copy()
        env["PYTHONPATH"] = str(REPO)
        self._proc = subprocess.Popen(
            cmd, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
            stderr=subprocess.PIPE, env=env, cwd=str(REPO),
        )
        return self

    def __exit__(self, *exc):
        if self._proc and self._proc.poll() is None:
            self._proc.terminate()
            try:
                self._proc.wait(timeout=5)
            except subprocess.TimeoutExpired:
                self._proc.kill()

    def initialize(self):
        init = _jsonrpc_msg("initialize", {
            "protocolVersion": "2024-11-05",
            "capabilities": {},
            "clientInfo": {"name": "test-client", "version": "1.0"},
        })
        self._proc.stdin.write(init.encode())
        self._proc.stdin.flush()
        resp = _read_response(self._proc)
        self._proc.stdin.write(
            _jsonrpc_msg("notifications/initialized", id=None).encode()
        )
        self._proc.stdin.flush()
        return resp.get("result", {})

    def call(self, method: str, params: dict | None = None) -> dict:
        self._msg_id += 1
        msg = _jsonrpc_msg(method, params, id=self._msg_id)
        self._proc.stdin.write(msg.encode())
        self._proc.stdin.flush()
        return _read_response(self._proc)

    def tool(self, name: str, arguments: dict | None = None) -> dict:
        """Call a tool and return the raw CallToolResult."""
        resp = self.call("tools/call", {"name": name,
                                        "arguments": arguments or {}})
        return resp.get("result", {})

    def structured(self, name: str, arguments: dict | None = None) -> dict:
        """Call a tool and return its structuredContent (raising on error)."""
        result = self.tool(name, arguments)
        if result.get("isError"):
            text = " ".join(c.get("text", "") for c in result.get("content", []))
            raise RuntimeError(f"{name} failed: {text[:400]}")
        return result.get("structuredContent", {})

    def content_text(self, name: str, arguments: dict | None = None) -> str:
        result = self.tool(name, arguments)
        return " ".join(c.get("text", "") for c in result.get("content", []))


# ---------------------------------------------------------------------------
# Protocol assertions
# ---------------------------------------------------------------------------

@pytest.mark.slow
class TestProtocolAssertions:

    def test_tool_list_invariants(self):
        with MCPServer() as srv:
            srv.initialize()
            tools1 = srv.call("tools/list")["result"]["tools"]
            tools2 = srv.call("tools/list")["result"]["tools"]
            names1 = [t["name"] for t in tools1]
            names2 = [t["name"] for t in tools2]
            assert names1 == names2, "Tool list not deterministic"
            assert len(tools1) >= 30, f"Expected >= 30 tools, got {len(tools1)}"
            for tool in tools1:
                name = tool["name"]
                assert re.match(r"^[A-Za-z0-9_.-]{1,128}$", name), name
                assert tool.get("description"), f"Empty description: {name}"
                schema = tool.get("inputSchema")
                assert schema, f"Missing inputSchema: {name}"
                assert schema.get("type") == "object", f"Schema not object: {name}"

    def test_ping_works(self):
        with MCPServer() as srv:
            srv.initialize()
            result = srv.tool("ping")
            text = " ".join(c.get("text", "") for c in result.get("content", []))
            assert "pong" in text

    def test_failure_returns_is_error(self):
        with MCPServer() as srv:
            srv.initialize()
            result = srv.tool("open_model", {"path": "/nonexistent/file.rbd"})
            assert result.get("isError") is True
            text = " ".join(c.get("text", "") for c in result.get("content", []))
            assert "Traceback" not in text

    def test_no_image_content_except_render(self):
        """No tool except render_diagram may return image content (§7.7)."""
        with MCPServer() as srv:
            srv.initialize()
            open_result = srv.structured("open_model",
                                         {"path": "MotorDemo"})
            mid = open_result["model_id"]
            calls = [
                ("list_models", {}),
                ("describe_model", {"model_id": mid}),
                ("validate_model", {"model_id": mid}),
                ("single_point_failures", {"model_id": mid}),
                ("simulate", {"model_id": mid, "num_runs": 2,
                              "confirm_long_run": True}),
            ]
            for name, args in calls:
                result = srv.tool(name, args)
                for block in result.get("content", []):
                    assert block.get("type") != "image", (
                        f"{name} returned image content; only render_diagram may"
                    )


# ---------------------------------------------------------------------------
# Equivalence tests -- engine path vs MCP path
# ---------------------------------------------------------------------------

@pytest.mark.slow
class TestEquivalenceTests:

    @pytest.mark.parametrize("model_name", ["MotorDemo"])
    def test_mcp_matches_engine(self, model_name):
        golden_input = GOLDEN / model_name / "input.rbd"
        if not golden_input.is_file():
            pytest.skip(f"Golden fixture {model_name} not found")

        # Engine path
        from raptor.core.fileio.rbd import load_rbd
        from raptor.core.engine.runner import run_simulation_with_reports
        rbd = load_rbd(str(golden_input))
        sink = []
        run_simulation_with_reports(
            rbd, num_runs=10, report_sink=sink, collect_logs=False,
        )
        engine_avail = statistics.fmean(r.availability for r in sink)

        # MCP path
        with MCPServer() as srv:
            srv.initialize()
            mid = srv.structured("open_model",
                                 {"path": model_name})["model_id"]
            data = srv.structured("simulate", {
                "model_id": mid, "num_runs": 10,
                "timeout_s": 120, "confirm_long_run": True,
            })
            mcp_avail = data["summary"]["Availability"]["mean"]

        assert abs(engine_avail - mcp_avail) < 0.05, (
            f"Availability diverged: engine={engine_avail}, mcp={mcp_avail}"
        )


# ---------------------------------------------------------------------------
# Scripted acceptance conversation
# ---------------------------------------------------------------------------

@pytest.mark.slow
class TestScriptedConversation:

    def test_step_01_list_capabilities(self):
        with MCPServer() as srv:
            srv.initialize()
            names = [t["name"] for t in srv.call("tools/list")["result"]["tools"]]
            for expected in ("build_model", "simulate",
                             "single_point_failures", "estimate_runtime"):
                assert expected in names

    def test_step_02_build_pump_station(self):
        with MCPServer() as srv:
            srv.initialize()
            def blk(n):
                return {"name": n, "fail_dist": 4, "fail_params": [1000, 0],
                        "repair_dist": 7, "repair_params": [8, 2]}
            spec = {
                "blocks": [blk("PumpA"), blk("PumpB"), blk("Standby")],
                "nodes": [
                    {"name": "Start", "type": "start"},
                    {"name": "rail", "type": "regular", "k": 1},
                    {"name": "join", "type": "regular", "k": 1},
                    {"name": "End", "type": "end"},
                ],
                "links": [
                    {"from_type": "Node", "from": "Start",
                     "to_type": "Node", "to": "rail"},
                    {"from_type": "Node", "from": "rail",
                     "to_type": "Block", "to": "PumpA"},
                    {"from_type": "Node", "from": "rail",
                     "to_type": "Block", "to": "PumpB"},
                    {"from_type": "Node", "from": "rail",
                     "to_type": "Block", "to": "Standby"},
                    {"from_type": "Block", "from": "PumpA",
                     "to_type": "Node", "to": "join"},
                    {"from_type": "Block", "from": "PumpB",
                     "to_type": "Node", "to": "join"},
                    {"from_type": "Block", "from": "Standby",
                     "to_type": "Node", "to": "join"},
                    {"from_type": "Node", "from": "join",
                     "to_type": "Node", "to": "End"},
                ],
                "system": {"term_condition": 8760, "num_runs": 100},
            }
            built = srv.structured("build_model",
                                   {"name": "PumpStation", "spec": spec})
            mid = built["model_id"]
            val = srv.structured("validate_model", {"model_id": mid})
            assert val["runnable"] is True

            # An unconnected block must be rejected, not silently accepted
            # (the engine crashes at load with "no outgoing link found").
            island = {
                "blocks": [blk("A"), blk("B")],
                "nodes": [{"name": "Start", "type": "start"},
                          {"name": "End", "type": "end"}],
                "links": [
                    {"from_type": "Node", "from": "Start",
                     "to_type": "Block", "to": "A"},
                    {"from_type": "Block", "from": "A",
                     "to_type": "Node", "to": "End"},
                ],
            }
            bad = srv.structured("build_model",
                                 {"name": "Islands", "spec": island})
            assert bad["validation"]["runnable"] is False
            # Raptor 7's own message (ValidateRBD, /3328).
            assert any("To simulate, block 'B' must connect to something!" in str(e)
                       for e in bad["validation"]["errors"])

    def test_step_04_estimate_runtime(self):
        with MCPServer() as srv:
            srv.initialize()
            mid = srv.structured("open_model",
                                 {"path": "MotorDemo"})["model_id"]
            est = srv.structured("estimate_runtime",
                                 {"model_id": mid, "num_runs": 10})
            assert est["seconds_estimate"] > 0
            assert "per_run_ms" in est

    def test_step_05_simulate(self):
        with MCPServer() as srv:
            srv.initialize()
            mid = srv.structured("open_model",
                                 {"path": "MotorDemo"})["model_id"]
            result = srv.tool("simulate", {
                "model_id": mid, "num_runs": 3,
                "timeout_s": 60, "confirm_long_run": True,
            })
            assert not result.get("isError")
            data = result["structuredContent"]
            assert data["run_id"]
            assert "summary" in data
            text = " ".join(c.get("text", "") for c in result["content"])
            assert "| Metric |" in text

    def test_step_06_single_point_failures(self):
        with MCPServer() as srv:
            srv.initialize()
            mid = srv.structured("open_model",
                                 {"path": "MotorDemo"})["model_id"]
            data = srv.structured("single_point_failures", {"model_id": mid})
            assert "spofs" in data and "count" in data

    def test_step_07_sweep_parameter(self):
        with MCPServer() as srv:
            srv.initialize()
            mid = srv.structured("open_model",
                                 {"path": "MotorDemo"})["model_id"]
            data = srv.structured("sweep_parameter", {
                "model_id": mid, "selector": "Casing",
                "field": "RParam1", "values": [4, 8, 24],
                "num_runs": 3, "metrics": ["availability"],
            })
            assert len(data["rows"]) == 3

    def test_step_09_manage_rates(self):
        with MCPServer() as srv:
            srv.initialize()
            mid = srv.structured("open_model",
                                 {"path": "MotorDemo"})["model_id"]
            entry = srv.structured("manage_rates", {
                "model_id": mid, "action": "add",
                "name": "TestRate", "expr": "0.001 * 1000  # test rate",
            })
            assert entry["name"] == "TestRate"
            assert abs(float(entry["value"]) - 1.0) < 1e-9
            rates = srv.structured("manage_rates",
                                   {"model_id": mid, "action": "list"})
            assert any(r["name"] == "TestRate" for r in rates["rates"])

    def test_step_10_save_rbds(self):
        with MCPServer() as srv:
            srv.initialize()
            mid = srv.structured("open_model",
                                 {"path": "MotorDemo"})["model_id"]
            save_path = "saved_model.rbds"
            data = srv.structured("save_model_as", {
                "model_id": mid, "path": save_path, "format": "rbds",
            })
            assert data["path"].endswith(save_path)
            assert data["bytes"] > 0
            assert os.path.isfile(os.path.join(srv._workspace, save_path))

    def test_step_11_get_timeseries(self):
        with MCPServer() as srv:
            srv.initialize()
            mid = srv.structured("open_model",
                                 {"path": "MotorDemo"})["model_id"]
            run_id = srv.structured("simulate", {
                "model_id": mid, "num_runs": 2,
                "timeout_s": 60, "confirm_long_run": True,
            })["run_id"]
            data = srv.structured("get_timeseries",
                                  {"run_id": run_id, "series": "availability"})
            assert "t" in data and "values" in data
            assert len(data["t"]) > 0

    def test_step_12_restart_persistence(self):
        workspace = tempfile.mkdtemp()
        with MCPServer(workspace=workspace) as srv:
            srv.initialize()
            mid = srv.structured("open_model",
                                 {"path": "MotorDemo"})["model_id"]
            srv.structured("save_model_as", {
                "model_id": mid,
                "path": "test.rbds",
                "format": "rbds",
            })
        with MCPServer(workspace=workspace) as srv:
            srv.initialize()
            data = srv.structured("list_models", {})
            assert isinstance(data["models"], list)


# ---------------------------------------------------------------------------
# Additional wire-level checks
# ---------------------------------------------------------------------------

def _call_collect(srv: MCPServer, method: str, params: dict,
                  timeout: float = 120) -> tuple[dict, list[dict]]:
    """Send a request; return (its response, notifications seen before it)."""
    import select
    import time
    srv._msg_id += 1
    rid = srv._msg_id
    srv._proc.stdin.write(_jsonrpc_msg(method, params, id=rid).encode())
    srv._proc.stdin.flush()
    notes: list[dict] = []
    buf = b""
    start = time.monotonic()
    while time.monotonic() - start < timeout:
        ready, _, _ = select.select([srv._proc.stdout], [], [], 0.5)
        if not ready:
            continue
        chunk = os.read(srv._proc.stdout.fileno(), 65536)
        if not chunk:
            break
        buf += chunk
        while b"\n" in buf:
            line, buf = buf.split(b"\n", 1)
            if not line.strip():
                continue
            obj = json.loads(line)
            if obj.get("id") == rid and ("result" in obj or "error" in obj):
                return obj, notes
            if "method" in obj:
                notes.append(obj)
    raise TimeoutError(f"no response to {method}")


@pytest.mark.slow
class TestReviewFixesOverTheWire:

    def test_every_tool_has_annotations(self):
        with MCPServer() as srv:
            srv.initialize()
            tools = srv.call("tools/list")["result"]["tools"]
            for t in tools:
                a = t.get("annotations") or {}
                assert a.get("title"), t["name"]
                assert "readOnlyHint" in a, t["name"]
            by = {t["name"]: t["annotations"] for t in tools}
            assert by["describe_model"]["readOnlyHint"] is True
            assert by["delete_element"]["destructiveHint"] is True

    def test_structured_error_over_wire(self):
        with MCPServer() as srv:
            srv.initialize()
            result = srv.tool("describe_model", {"model_id": "m_missing"})
            assert result["isError"] is True
            err = result["structuredContent"]["error"]
            assert err["code"] == "not_found"
            assert err["hint"]

    def test_invalid_enum_rejected_by_schema(self):
        with MCPServer() as srv:
            srv.initialize()
            result = srv.tool("describe_model",
                              {"model_id": "m_x", "detail": "everything"})
            assert result["isError"] is True

    def test_simulate_sends_progress_notifications(self):
        with MCPServer() as srv:
            srv.initialize()
            mid = srv.structured("open_model",
                                 {"path": "MotorDemo"})["model_id"]
            resp, notes = _call_collect(srv, "tools/call", {
                "name": "simulate",
                "arguments": {"model_id": mid, "num_runs": 2500,
                              "confirm_long_run": True, "timeout_s": 300},
                "_meta": {"progressToken": "tok1"},
            })
            assert not resp["result"].get("isError"), resp
            progress = [n for n in notes
                        if n.get("method") == "notifications/progress"]
            assert progress, "no progress notifications for a multi-second run"
            assert all(n["params"]["progressToken"] == "tok1" for n in progress)
            assert resp["result"]["structuredContent"]["runs"] == 2500

    def test_cancellation_stops_simulate(self):
        import time
        with MCPServer() as srv:
            srv.initialize()
            mid = srv.structured("open_model",
                                 {"path": "MotorDemo"})["model_id"]
            srv._msg_id += 1
            rid = srv._msg_id
            srv._proc.stdin.write(_jsonrpc_msg("tools/call", {
                "name": "simulate",
                "arguments": {"model_id": mid, "num_runs": 5000,
                              "confirm_long_run": True, "timeout_s": 600},
            }, id=rid).encode())
            srv._proc.stdin.flush()
            time.sleep(1.5)
            srv._proc.stdin.write(_jsonrpc_msg(
                "notifications/cancelled",
                {"requestId": rid, "reason": "test"}, id=None).encode())
            srv._proc.stdin.flush()
            # The server must stay responsive right away (engine thread
            # was told to abort, not left burning the request slot).
            t0 = time.monotonic()
            result = srv.tool("ping")
            assert "pong" in " ".join(c.get("text", "") for c in result["content"])
            assert time.monotonic() - t0 < 10


@pytest.mark.slow
class TestServerIdentityOverTheWire:

    def test_server_info_and_instructions(self):
        with MCPServer() as srv:
            init = srv.initialize()
            info = init["serverInfo"]
            assert info["name"] == "raptor-studio"
            assert info.get("title") == "Raptor Studio"
            assert "dependen" in init.get("instructions", "").lower()

    def test_get_element_over_the_wire(self):
        with MCPServer() as srv:
            srv.initialize()
            mid = srv.structured("open_model",
                                 {"path": "MotorDemo"})["model_id"]
            d = srv.structured("get_element",
                               {"model_id": mid, "selector": "Armature"})
            assert d["failure"]["name"] == "Weibull"
            assert d["dependency"]["follows"]["name"] == "PowerSupply"
            tools = srv.call("tools/list")["result"]["tools"]
            ge = next(t for t in tools if t["name"] == "get_element")
            assert ge["annotations"]["readOnlyHint"] is True
