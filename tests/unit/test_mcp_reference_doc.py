"""docs/MCP.md is generated from the live server; a stale copy fails here.

Regenerate with `.venv/bin/python tools/gen_mcp_reference.py`."""
import importlib.util
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def test_mcp_reference_is_current():
    spec = importlib.util.spec_from_file_location("gen_mcp_reference", ROOT / "tools/gen_mcp_reference.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    assert (ROOT / "docs/MCP.md").read_text() == mod.render(), "docs/MCP.md is stale: run tools/gen_mcp_reference.py"
