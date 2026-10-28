"""The MCP server starts as a subprocess over stdio, lists its tools and
answers a basic call.
"""
from __future__ import annotations

import json
import subprocess
import sys

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


def _read_jsonrpc(stream) -> dict:
    """Read one JSON-RPC response from *stream* (newline-delimited)."""
    line = stream.readline()
    if not line:
        raise RuntimeError("Server closed connection without a response")
    return json.loads(line)


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
