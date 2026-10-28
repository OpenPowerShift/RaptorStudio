"""MCP over HTTP: starts, finds a port, and enforces its security rules."""
from __future__ import annotations

import json
import subprocess
import sys
import urllib.error
import urllib.request

import pytest

pytest.importorskip("mcp")

PY = sys.executable


def _start(*extra, env=None):
    proc = subprocess.Popen(
        [PY, "-m", "raptor.cli", "mcp", "--transport", "http", "--port", "0",
         *extra],
        stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, env=env)
    url = proc.stdout.readline().strip()        # the FIRST stdout line
    return proc, url


def _post(url, headers=None, body=None):
    req = urllib.request.Request(
        url, data=json.dumps(body or {"jsonrpc": "2.0", "id": 1,
                                      "method": "ping"}).encode(),
        headers={"Content-Type": "application/json",
                 "Accept": "application/json, text/event-stream",
                 **(headers or {})}, method="POST")
    try:
        with urllib.request.urlopen(req, timeout=10) as r:
            return r.status
    except urllib.error.HTTPError as exc:
        return exc.code


@pytest.fixture
def token_file(tmp_path):
    p = tmp_path / "tok"
    p.write_text("s3cret-token-0123456789\n")
    return str(p)


def test_port_zero_prints_a_usable_url_first(tmp_path):
    proc, url = _start("--workspace", str(tmp_path / "ws"),
                       "--allow-unauthenticated")
    try:
        assert url.startswith("http://127.0.0.1:")
        port = int(url.rsplit(":", 1)[1].split("/")[0])
        assert port > 0 and url.endswith("/mcp")
        assert _post(url) != 0                    # it answers something
    finally:
        proc.terminate(); proc.wait(timeout=10)


def test_bearer_token_is_enforced(tmp_path, token_file):
    proc, url = _start("--workspace", str(tmp_path / "ws"),
                       "--token-file", token_file)
    try:
        assert _post(url) == 401
        assert _post(url, {"Authorization": "Bearer wrong"}) == 401
        assert _post(url, {"Authorization": "Basic czNjcmV0"}) == 401
        assert _post(url, {"Authorization": "Bearer s3cret-token-0123456789"}) != 401
    finally:
        proc.terminate(); proc.wait(timeout=10)


def test_foreign_origin_is_refused_unless_allowed(tmp_path):
    proc, url = _start("--workspace", str(tmp_path / "ws"),
                       "--allow-unauthenticated",
                       "--allow-origin", "http://good.example")
    try:
        assert _post(url, {"Origin": "http://evil.example"}) == 403
        assert _post(url, {"Origin": "http://good.example"}) != 403
    finally:
        proc.terminate(); proc.wait(timeout=10)


def test_non_loopback_without_a_token_refuses_to_start(tmp_path):
    r = subprocess.run(
        [PY, "-m", "raptor.cli", "mcp", "--transport", "http", "--host",
         "0.0.0.0", "--port", "0", "--workspace", str(tmp_path / "ws")],
        capture_output=True, text=True, timeout=30)
    assert r.returncode == 2
    assert "without a token" in r.stderr
    assert r.stdout == ""


def test_bad_option_combinations(tmp_path):
    r = subprocess.run(
        [PY, "-m", "raptor.cli", "mcp", "--transport", "http", "--tls-cert",
         "c.pem", "--workspace", str(tmp_path / "ws")],
        capture_output=True, text=True, timeout=30)
    assert r.returncode == 2 and "--tls-key" in r.stderr
    r = subprocess.run(
        [PY, "-m", "raptor.cli", "mcp", "--transport", "http",
         "--token-file", str(tmp_path / "missing"),
         "--workspace", str(tmp_path / "ws")],
        capture_output=True, text=True, timeout=30)
    assert r.returncode == 2


def test_empty_token_file_is_refused(tmp_path):
    t = tmp_path / "empty"; t.write_text("\n")
    r = subprocess.run(
        [PY, "-m", "raptor.cli", "mcp", "--transport", "http",
         "--token-file", str(t), "--workspace", str(tmp_path / "ws")],
        capture_output=True, text=True, timeout=30)
    assert r.returncode == 2 and "empty" in r.stderr


def test_http_without_a_token_refuses_to_start_even_on_loopback(tmp_path):
    r = subprocess.run(
        [PY, "-m", "raptor.cli", "mcp", "--transport", "http", "--port", "0",
         "--workspace", str(tmp_path / "ws")],
        capture_output=True, text=True, timeout=30)
    assert r.returncode == 2
    assert "without a token" in r.stderr and "--allow-unauthenticated" in r.stderr
    assert r.stdout == ""


def test_a_short_token_is_refused(tmp_path):
    t = tmp_path / "short"; t.write_text("abc123\n")
    r = subprocess.run(
        [PY, "-m", "raptor.cli", "mcp", "--transport", "http",
         "--token-file", str(t), "--workspace", str(tmp_path / "ws")],
        capture_output=True, text=True, timeout=30)
    assert r.returncode == 2 and "too short" in r.stderr
