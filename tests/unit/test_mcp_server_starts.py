"""The MCP server starts as a subprocess over stdio, lists its tools and
answers a basic call.
"""
from __future__ import annotations

import json
import queue
import subprocess
import sys
import threading

import pytest


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _jsonrpc_msg(method: str, *, params: dict | None = None,
                 id: int | None = 1) -> bytes:
    """Build a single JSON-RPC 2.0 message.

    ``id=None`` produces a **notification** (no ``id`` field), which
    expects no response from the server.
    """
    msg: dict = {"jsonrpc": "2.0", "method": method}
    if id is not None:
        msg["id"] = id
    if params is not None:
        msg["params"] = params
    return (json.dumps(msg) + "\n").encode()


def _read_jsonrpc(stream, timeout: float = 20.0) -> dict:
    """Read one JSON-RPC response from *stream* (newline-delimited).

    A broken server must fail the test rather than hang the whole suite,
    so the blocking read runs in a daemon thread with a timeout.
    """
    lines: queue.Queue = queue.Queue(maxsize=1)
    threading.Thread(target=lambda: lines.put(stream.readline()),
                     daemon=True).start()
    try:
        line = lines.get(timeout=timeout)
    except queue.Empty:
        raise RuntimeError(
            f"Server sent no reply within {timeout:g}s") from None
    if not line:
        raise RuntimeError("Server closed connection without a response")
    return json.loads(line)


def _modern_discover_params() -> dict:
    """The 2026-07-28 per-request envelope for `server/discover`."""
    return {
        "_meta": {
            "io.modelcontextprotocol/protocolVersion": "2026-07-28",
            "io.modelcontextprotocol/clientInfo": {
                "name": "test-client", "version": "0.1.0",
            },
            "io.modelcontextprotocol/clientCapabilities": {},
        },
    }


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

class TestMCPServerStarts:
    """Start the server as a subprocess, speak MCP over stdio."""

    @pytest.fixture()
    def server_proc(self):
        """Launch ``python -m raptor.mcp`` and yield (proc, stdin, stdout)."""
        proc = subprocess.Popen(
            [sys.executable, "-m", "raptor.mcp", "--transport", "stdio"],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )
        try:
            yield proc
        finally:
            proc.terminate()
            proc.wait(timeout=5)

    def _send_and_receive(self, proc, method: str,
                          params: dict | None = None,
                          id: int = 1) -> dict:
        """Send a JSON-RPC request and read the response."""
        req = _jsonrpc_msg(method, params=params, id=id)
        proc.stdin.write(req)
        proc.stdin.flush()
        return _read_jsonrpc(proc.stdout)

    def _initialize(self, proc) -> dict:
        """Perform the MCP initialize handshake and return the response."""
        return self._send_and_receive(proc, "initialize", params={
            "protocolVersion": "2025-03-26",
            "capabilities": {},
            "clientInfo": {"name": "test-client", "version": "0.1.0"},
        })

    def test_help_prints_flags(self):
        """``--help`` prints the CLI flags."""
        result = subprocess.run(
            [sys.executable, "-m", "raptor.mcp", "--help"],
            capture_output=True, text=True, timeout=10,
        )
        assert result.returncode == 0
        for flag in ["--workspace", "--transport", "--read-only",
                      "--no-render", "--allow-write", "--retain-days",
                      "--max-runs", "--log-level"]:
            assert flag in result.stdout, f"Flag {flag} not in --help output"

    def test_server_starts_and_lists_tools(self, server_proc):
        """Server starts, responds to initialize, and lists both tools."""
        proc = server_proc

        # 1. Initialize
        init_resp = self._initialize(proc)
        assert "result" in init_resp, f"Initialize failed: {init_resp}"

        # 2. Send initialized notification (required by protocol, no id = no response)
        proc.stdin.write(_jsonrpc_msg("notifications/initialized", id=None))
        proc.stdin.flush()

        # 3. List tools
        tools_resp = self._send_and_receive(proc, "tools/list")
        assert "result" in tools_resp, f"tools/list failed: {tools_resp}"
        tools = tools_resp["result"]["tools"]
        tool_names = [t["name"] for t in tools]

        assert "ping" in tool_names, f"ping not in tool list: {tool_names}"
        assert "list_distributions" in tool_names, (
            f"list_distributions not in tool list: {tool_names}"
        )
        # All tools are registered.
        assert len(tools) >= 30, f"Expected >= 30 tools, got {len(tools)}: {tool_names}"

    def test_ping_returns_pong(self, server_proc):
        """Calling the ping tool returns 'pong'."""
        proc = server_proc

        self._initialize(proc)
        proc.stdin.write(_jsonrpc_msg("notifications/initialized", id=None))
        proc.stdin.flush()

        resp = self._send_and_receive(proc, "tools/call", params={
            "name": "ping",
            "arguments": {},
        })
        assert "result" in resp, f"ping call failed: {resp}"
        content = resp["result"]["content"]
        assert len(content) == 1
        assert content[0]["type"] == "text"
        assert content[0]["text"] == "pong"

    def test_list_distributions_returns_all(self, server_proc):
        """list_distributions returns all 22 distributions with codes."""
        proc = server_proc

        self._initialize(proc)
        proc.stdin.write(_jsonrpc_msg("notifications/initialized", id=None))
        proc.stdin.flush()

        resp = self._send_and_receive(proc, "tools/call", params={
            "name": "list_distributions",
            "arguments": {},
        })
        assert "result" in resp, f"list_distributions failed: {resp}"
        result = resp["result"]
        dists = result["structuredContent"]["distributions"]
        assert isinstance(dists, list)
        assert len(dists) == 22, f"Expected 22 distributions, got {len(dists)}"

        # Spot-check a few known distributions
        codes = {d["code"] for d in dists}
        assert 4 in codes   # Exponential
        assert 15 in codes  # Weibull
        assert 7 in codes   # Lognormal

        # Check that each has the expected keys
        for d in dists:
            assert "code" in d
            assert "name" in d
            assert "num_params" in d
            assert "param_names" in d
            assert "valid_ranges" in d

    def test_server_runs_without_repo_on_path(self, tmp_path):
        """The installed server runs without the repository on
        ``sys.path``/cwd: the row-constructor library lives in
        ``raptor.core.fileio.rbdlib``, which is importable from an
        installed package.
        """
        import os

        env = os.environ.copy()
        env.pop("PYTHONPATH", None)
        proc = subprocess.Popen(
            [sys.executable, "-m", "raptor.mcp", "--transport", "stdio",
             "--workspace", str(tmp_path)],
            stdin=subprocess.PIPE, stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            cwd=str(tmp_path), env=env,
        )
        try:
            self._initialize(proc)
            proc.stdin.write(_jsonrpc_msg("notifications/initialized", id=None))
            proc.stdin.flush()
            resp = self._send_and_receive(proc, "tools/call", params={
                "name": "list_distributions", "arguments": {},
            })
            assert "result" in resp, f"call failed: {resp}"
            result = resp["result"]
            assert not result.get("isError"), result.get("content")
            dists = result["structuredContent"]["distributions"]
            assert len(dists) == 22
        finally:
            proc.terminate()
            proc.wait(timeout=5)


class TestLazyWorkspaceStartup:
    """The transport must not wait for the Workspace to be constructed."""

    def test_main_does_not_build_workspace_before_transport(
            self, tmp_path, monkeypatch):
        from raptor.mcp import server

        seen: dict = {}

        def fake_run(**kwargs):
            seen["workspace"] = getattr(server.mcp, "_raptor_ws", None)
            seen["kwargs"] = kwargs

        monkeypatch.setattr(server, "_import_tools", lambda args: None)
        monkeypatch.setattr(server.mcp, "run", fake_run)
        monkeypatch.setattr(server.mcp, "_raptor_args", None, raising=False)
        monkeypatch.setattr(server.mcp, "_raptor_ws", None, raising=False)
        monkeypatch.setattr(server.mcp, "_raptor_prepared", None,
                            raising=False)

        root = tmp_path / "workspace"
        assert server.main(["--workspace", str(root),
                            "--transport", "stdio"]) == 0

        # The directory tree exists (so a bad path still fails fast), but
        # no Workspace was built before mcp.run().
        assert seen["workspace"] is None
        for name in ("models", "runs", "renders"):
            assert (root / name).is_dir()

    def test_main_reports_unusable_workspace(self, tmp_path, monkeypatch):
        from raptor.mcp import server

        monkeypatch.setattr(server.mcp, "_raptor_args", None, raising=False)
        monkeypatch.setattr(server.mcp, "_raptor_prepared", None,
                            raising=False)
        blocker = tmp_path / "not-a-directory"
        blocker.write_text("")
        assert server.main(["--workspace", str(blocker),
                            "--transport", "stdio"]) == 1

    def test_prepared_root_is_pinned_against_a_symlink_swap(
            self, tmp_path, monkeypatch):
        from raptor.mcp import context, server

        safe = tmp_path / "safe"
        evil = tmp_path / "evil"
        safe.mkdir()
        evil.mkdir()
        link = tmp_path / "workspace"
        link.symlink_to(safe, target_is_directory=True)
        args = type("A", (), {"workspace": str(link), "retain_days": 30})()
        monkeypatch.setattr(server.mcp, "_raptor_args", args, raising=False)
        monkeypatch.setattr(server.mcp, "_raptor_ws", None, raising=False)
        monkeypatch.setattr(server.mcp, "_raptor_prepared", None, raising=False)

        assert context.prepare_workspace() == safe.resolve()
        link.unlink()
        link.symlink_to(evil, target_is_directory=True)

        ws = context.workspace()
        assert ws.root == safe.resolve()
        assert not (evil / "models").exists()

    def test_prepared_root_that_became_a_symlink_is_refused(
            self, tmp_path, monkeypatch):
        from raptor.mcp import context, server

        safe = tmp_path / "safe"
        evil = tmp_path / "evil"
        safe.mkdir()
        evil.mkdir()
        args = type("A", (), {"workspace": str(safe), "retain_days": 30})()
        monkeypatch.setattr(server.mcp, "_raptor_args", args, raising=False)
        monkeypatch.setattr(server.mcp, "_raptor_ws", None, raising=False)
        monkeypatch.setattr(server.mcp, "_raptor_prepared", None, raising=False)
        assert context.prepare_workspace() == safe.resolve()

        safe.rename(tmp_path / "moved")
        safe.symlink_to(evil, target_is_directory=True)

        with pytest.raises(Exception, match="changed since start-up"):
            context.workspace()

    def test_prepared_root_refuses_a_swapped_managed_directory(
            self, tmp_path, monkeypatch):
        from raptor.mcp import context, server

        safe = tmp_path / "safe"
        evil = tmp_path / "evil"
        safe.mkdir()
        evil.mkdir()
        args = type("A", (), {"workspace": str(safe), "retain_days": 30})()
        monkeypatch.setattr(server.mcp, "_raptor_args", args, raising=False)
        monkeypatch.setattr(server.mcp, "_raptor_ws", None, raising=False)
        monkeypatch.setattr(server.mcp, "_raptor_prepared", None, raising=False)
        context.prepare_workspace()

        (safe / "models").rename(tmp_path / "moved-models")
        (safe / "models").symlink_to(evil, target_is_directory=True)

        with pytest.raises(Exception, match="resolves outside"):
            context.workspace()

    def test_modern_discover_and_lazy_first_call(self, tmp_path):
        """A 2026-07-28 first frame is served, then the first call loads."""
        from raptor.api.workspace import Workspace

        root = tmp_path / "workspace"
        writer = Workspace(root)
        writer.create_model("Alpha")
        writer.create_model("Beta")
        proc = subprocess.Popen(
            [sys.executable, "-m", "raptor.mcp", "--transport", "stdio",
             "--workspace", str(root)],
            stdin=subprocess.PIPE, stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )
        try:
            resp = self._send(proc, "server/discover",
                              _modern_discover_params())
            assert "result" in resp, resp
            assert resp["result"]["supportedVersions"] == ["2026-07-28"]

            # The first tool call builds the Workspace on demand.
            params = {**_modern_discover_params(), "name": "list_models",
                      "arguments": {"include_examples": False}}
            call = self._send(proc, "tools/call", params, id=2)
            assert "result" in call, call
            assert not call["result"].get("isError"), call["result"]
            models = call["result"]["structuredContent"]["models"]
            assert {m["name"] for m in models} == {"Alpha", "Beta"}
        finally:
            proc.terminate()
            proc.wait(timeout=5)

    @staticmethod
    def _send(proc, method: str, params: dict | None = None,
              id: int = 1) -> dict:
        proc.stdin.write(_jsonrpc_msg(method, params=params, id=id))
        proc.stdin.flush()
        return _read_jsonrpc(proc.stdout)
