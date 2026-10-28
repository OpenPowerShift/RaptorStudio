"""Security hardening for MCP inputs: read boundary, finite numbers,
immutable fields, quotas, response caps and CSV formula neutralisation."""
from __future__ import annotations

import asyncio
import shutil
from pathlib import Path
from types import SimpleNamespace

import pytest

pytest.importorskip("mcp")

from raptor.api.limits import MAX_REQUEST_BYTES
from raptor.api.workspace import ModelLimitExceeded, Workspace, WorkspaceSizeExceeded

REPO = Path(__file__).resolve().parents[2]
GOLDEN = REPO / "tests" / "golden"


def _use_workspace(path: Path, **flags):
    from raptor.mcp import context, server
    values = dict(workspace=str(path), retain_days=30, read_only=False,
                  max_runs=5000, allow_write=[], allow_read=[])
    values.update(flags)
    args = SimpleNamespace(**values)
    server.mcp._raptor_args = args
    server.mcp._raptor_ws = None
    context.workspace()
    return server


def _call(name, args):
    from raptor.mcp import server
    return asyncio.run(server.mcp.call_tool(name, args))


def _sc(result):
    return result.structured_content


def test_open_model_refuses_paths_outside_the_read_boundary(tmp_path):
    outside = tmp_path / "outside"
    outside.mkdir()
    src = outside / "private.rbd"
    shutil.copyfile(GOLDEN / "MotorDemo" / "input.rbd", src)
    ws = tmp_path / "ws"
    _use_workspace(ws)
    from raptor.mcp.tools_model import open_model

    refused = open_model(str(src))
    assert refused.is_error
    assert _sc(refused)["error"]["code"] == "invalid_argument"

    # Absolute paths stay refused even with an explicit read opt-in; the
    # opt-in widens WHERE a relative path may resolve, not the path syntax.
    _use_workspace(ws, allow_read=[str(outside)])
    still_absolute = open_model(str(src))
    assert still_absolute.is_error
    allowed = open_model("private.rbd")
    assert not allowed.is_error, allowed.content


def test_build_model_rejects_unknown_and_non_finite_spec_values(tmp_path):
    _use_workspace(tmp_path / "ws")
    from raptor.mcp.tools_model import build_model

    typo = build_model("x", {"blockz": []})
    assert typo.is_error and "unknown spec key" in typo.content[0].text

    bad = build_model("x", {
        "blocks": [{"name": "A", "fail_params": [float("nan")]}],
    })
    assert bad.is_error and "finite" in bad.content[0].text


def test_build_model_refuses_fractional_capacity_flow(tmp_path):
    """A fractional nom_flow/max_flow cannot be stored in a legacy .rbd:
    the reader uses ReadInt, so the decimal desynchronises the token stream
    and crashes real Raptor 7 on open. The entry path refuses it instead."""
    _use_workspace(tmp_path / "ws")
    from raptor.mcp.tools_model import build_model

    spec = {
        "blocks": [{"name": "B1", "id": 1, "fail_dist": 4,
                    "fail_params": [100, 0]},
                   {"name": "B2", "id": 2, "fail_dist": 4,
                    "fail_params": [100, 0]}],
        "nodes": [{"name": "start", "type": "start", "id": 1},
                  {"name": "end", "type": "end", "id": 2}],
        "links": [
            {"from": ["Node", 1], "to": ["Block", 1]},
            {"from": ["Block", 1], "to": ["Block", 2]},
            {"from": ["Block", 2], "to": ["Node", 2],
             "nom_flow": 133.76, "max_flow": 133.76},
        ],
    }
    refused = build_model("fractional", spec)
    assert refused.is_error
    assert "whole number" in refused.content[0].text


def test_update_element_refuses_fractional_capacity_flow(tmp_path):
    _use_workspace(tmp_path / "ws")
    from raptor.mcp.tools_model import (create_model, describe_model,
                                        update_element)

    mid = _sc(create_model("m", template="series", n=2))["model_id"]
    first_link = _sc(describe_model(mid, detail="full"))["links"][0]["id"]
    selector = f"Link:{first_link}"
    result = update_element(mid, selector, {"nom_flow": 133.76})
    assert result.is_error
    assert "whole number" in result.content[0].text

    # A whole number (and the -1/-2 keywords) still commits.
    assert not update_element(mid, selector, {"nom_flow": 133}).is_error
    assert not update_element(mid, selector, {"nom_flow": -2}).is_error


def test_update_element_refuses_identity_and_phase_columns(tmp_path):
    _use_workspace(tmp_path / "ws")
    from raptor.mcp.tools_model import create_model, update_element

    mid = _sc(create_model("m"))["model_id"]
    for patch in ({"ID": 99}, {"ParentId": 2}, {"Phasing": 1},
                  {"P 001": "A: 1.000"}):
        result = update_element(mid, "Block:1", patch)
        assert result.is_error, patch
        if "ID" in patch or "ParentId" in patch or "Phasing" in patch:
            assert "identity, topology or phasing" in result.content[0].text


def test_save_model_as_requires_the_matching_extension(tmp_path):
    _use_workspace(tmp_path / "ws")
    from raptor.mcp.tools_model import create_model, save_model_as

    mid = _sc(create_model("m"))["model_id"]
    mismatch = save_model_as(mid, "bad.rbd", format="rbds")
    assert mismatch.is_error
    assert "needs a .rbds path" in mismatch.content[0].text

    good = save_model_as(mid, "good.rbds", format="rbds")
    assert not good.is_error


def test_workspace_enforces_model_count_and_size(tmp_path):
    ws = Workspace(tmp_path / "one", max_total_models=1)
    ws.create_model("first")
    with pytest.raises(ModelLimitExceeded):
        ws.create_model("second")

    tiny = Workspace(tmp_path / "tiny", max_bytes=1)
    with pytest.raises(WorkspaceSizeExceeded):
        tiny.create_model("too-big")


def test_render_resource_refuses_traversal(tmp_path):
    ws = tmp_path / "ws"
    _use_workspace(ws)
    (ws / "renders").mkdir(parents=True, exist_ok=True)
    (ws.parent / "secret.png").write_bytes(b"PNG")
    (ws / "renders" / "ok.png").write_bytes(b"OK")
    from raptor.mcp.resources import get_render

    with pytest.raises(FileNotFoundError):
        get_render("../../secret.png")
    assert get_render("ok.png") == b"OK"


def test_http_requires_tls_for_non_loopback_and_limits_bodies(tmp_path):
    from raptor.mcp.http_transport import BodyLimitMiddleware, check_args

    token = tmp_path / "token"
    token.write_text("secret-token-0123456789\n")
    base = dict(host="0.0.0.0", port=8765, path="/mcp", token_file=str(token),
                token_env=None, tls_cert=None, tls_key=None,
                allow_insecure_http=False)
    problem = check_args(SimpleNamespace(**base))
    assert problem and "plaintext HTTP" in problem
    assert check_args(SimpleNamespace(**{**base,
                                         "allow_insecure_http": True})) is None

    async def scenario():
        sent = []

        async def app(scope, receive, send):
            sent.append("app")

        async def receive():
            return {"type": "http.request", "body": b""}

        async def send(message):
            sent.append(message)

        middleware = BodyLimitMiddleware(app, MAX_REQUEST_BYTES)
        await middleware(
            {"type": "http", "headers": [(b"content-length", b"999999999")]},
            receive, send)
        return sent

    messages = asyncio.run(scenario())
    assert messages[0]["status"] == 413


def test_csv_export_neutralises_formula_cells(tmp_path):
    from raptor.api.table_export import _write_csv
    from raptor.api.tables import Table

    table = Table(key="inputs.blocks", title="Blocks", group="Inputs",
                  headers=["Name"], rows=[["=cmd|' /C calc'!A0"]],
                  text_cols=[0])
    path = tmp_path / "blocks.csv"
    _write_csv(table, path)
    text = path.read_text(encoding="utf-8-sig")
    assert "'=cmd" in text
