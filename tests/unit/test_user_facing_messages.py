"""User-facing text must not carry developer source citations.

Errors used to be shown as "… [RAPTOR Studio, Raptor 7]". Those
locations are useful in the rule definitions, not in a dialog, an MCP
response or the published docs.
"""
from __future__ import annotations

import asyncio
import re
from pathlib import Path

from raptor.api.validate import issues
from raptor.api.workspace import Workspace

REPO = Path(__file__).resolve().parent.parent.parent
GOLDEN = REPO / "tests" / "golden"
CITATION = re.compile(r"[A-Za-z0-9_]+\.(?:mod|py|cpp|h):\d+")

USER_DOCS = [
    REPO / "docs" / "MCP.md",
    REPO / "docs" / "RBDS_FORMAT.md",
    REPO / "docs" / "IEC61078.md",
    *sorted((REPO / "new_docs" / "chapters").glob("*.adoc")),
]


def test_validation_messages_have_no_source_citation():
    ws = Workspace(Path("/tmp"))
    model_id = ws.open_model(GOLDEN / "MotorDemo" / "input.rbd")["model_id"]
    rbd = ws.get(model_id).rbd
    # Break the graph so the connectivity rule fires.
    rbd.section("LINK_DATA").rows.clear()
    found = issues(rbd)
    assert found, "expected at least one issue from the broken graph"
    for issue in found:
        assert "[RAPTOR Studio," not in issue.message
        assert not CITATION.search(issue.message), issue.message


def test_mcp_tool_descriptions_have_no_source_citation():
    from raptor.mcp import server, tools_model, tools_data  # noqa: F401
    for tool in asyncio.run(server.mcp.list_tools()):
        text = tool.description or ""
        assert not CITATION.search(text), f"{tool.name}: {text}"


def test_user_docs_have_no_source_citation():
    for path in USER_DOCS:
        if not path.exists():
            continue
        for number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
            assert not CITATION.search(line), f"{path.name}:{number}: {line.strip()}"
