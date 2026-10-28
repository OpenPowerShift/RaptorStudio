"""The MCP tool list is a contract: a tool, a parameter or a required flag changes only on purpose.

Regenerate after a deliberate change with `UPDATE_MCP_SNAPSHOT=1 .venv/bin/python -m pytest tests/unit/test_mcp_tool_snapshot.py`
and commit `tests/unit/data/mcp_tools_snapshot.json`."""
from __future__ import annotations

import asyncio
import json
import os
from pathlib import Path

SNAPSHOT = Path(__file__).resolve().parent / "data" / "mcp_tools_snapshot.json"


def _current() -> dict:
    from raptor.mcp import server, tools_model, tools_data, tools_run, tools_metrics, tools_tables, tools_view  # noqa: F401
    out = {}
    for t in asyncio.run(server.mcp.list_tools()):
        schema = t.input_schema or {}
        out[t.name] = {"params": sorted(schema.get("properties", {})), "required": sorted(schema.get("required", []))}
    return dict(sorted(out.items()))


def test_the_tool_list_matches_the_snapshot():
    now = _current()
    if os.environ.get("UPDATE_MCP_SNAPSHOT"):
        SNAPSHOT.write_text(json.dumps(now, indent=1) + "\n")
    stored = json.loads(SNAPSHOT.read_text())
    added = sorted(set(now) - set(stored))
    removed = sorted(set(stored) - set(now))
    changed = sorted(n for n in set(now) & set(stored) if now[n] != stored[n])
    assert not (added or removed or changed), (
        f"MCP tools changed -- added {added}, removed {removed}, changed {changed}. If deliberate, regenerate the "
        "snapshot (see this file's docstring).")
