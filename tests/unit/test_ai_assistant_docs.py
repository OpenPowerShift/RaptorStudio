"""AI Assistant is documentation: the reference section the File view opens,
and the client setup the docs promise (including OpenCode)."""
from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def _manual_section() -> str:
    text = (ROOT / "docs" / "manual.adoc").read_text(encoding="utf-8")
    return text.split("[[ai-assistant]]", 1)[1].split(
        "[[files-and-reports]]", 1)[0]


def test_the_file_view_link_opens_a_documentation_section():
    section = _manual_section()
    assert "File ▸ AI Assistant" in section
    assert "docs/MCP.md" in section
    assert "ch-mcp" in section
    assert "Two tabs" not in section        # the old setup dialog
    assert "Print and Export" not in section


def test_the_reference_manual_names_the_supported_clients():
    section = " ".join(_manual_section().lower().split())
    for client in ("claude code", "claude desktop", "opencode"):
        assert client in section


def test_the_generated_reference_has_the_opencode_v2_config():
    text = (ROOT / "docs" / "MCP.md").read_text(encoding="utf-8")
    assert "**OpenCode**" in text
    assert '"type": "local"' in text
    assert '"mcp": {' in text
    assert '"servers": {' in text


def test_the_user_guide_has_the_opencode_v2_config():
    text = (ROOT / "new_docs" / "chapters" / "ch16-mcp.adoc").read_text(
        encoding="utf-8")
    opencode = text.split("==== OpenCode", 1)[1].split("====", 1)[0]
    assert '"type": "local"' in opencode
    assert '"command": [' in opencode
    assert "no\nneed to restart" in opencode
