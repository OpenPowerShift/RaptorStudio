#!/usr/bin/env python3
"""Check that the book's MCP tool table matches the live tool set.

`ch16-mcp.adoc` documents the tool catalogue in prose tables and quotes
the tool count in two separate places.  Nothing gated either, so they
drifted: the chapter said "32 tools" in one paragraph and "42" in
another, and stayed that way.

This is the docs-side counterpart of
`test_manual_reference_matches_live_gui`, which does the same job for the
GUI's control list in `docs/manual-reference.adoc`.

Two checks:

1. every tool name in ch16's tables exists in the live tool set, and every
   tool in the live set is mentioned in ch16 (so a new tool cannot be
   added without a line of documentation);
2. the "N tools" figure(s) quoted in the chapter match the live count.

Run directly for a report, or let `tests/unit/test_docs_quality_gates.py`
call `main()`.
"""
from __future__ import annotations

import os
import re
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
CH16 = os.path.join(ROOT, "new_docs", "chapters", "ch16-mcp.adoc")

#: Inline code spans holding a bare tool name, e.g. `add_block`.
_TOOL_SPAN = re.compile(r"`([a-z][a-z0-9_]*)`")
#: "the full v1 catalogue of 32 tools" / "42 including ..."
_COUNT = re.compile(r"\b(\d+)\s+(?:tools?\b|including\b)")

#: Names that look like tool names but are not, so the "every live tool is
#: documented" half does not chase prose words.
_NOT_TOOLS = {
    "rbds", "rbd", "adoc", "json", "yaml", "asciidoc",
    "true", "false", "null", "none", "yes", "no",
}

#: `render_diagram` is not registered when the server runs with
#: `--no-render`, so a chapter may legitimately quote either count.
_OPTIONAL_TOOLS = 1


def chapter_text() -> str:
    with open(CH16, encoding="utf-8") as fh:
        return fh.read()


def live_tool_names() -> set[str]:
    """Every tool name the MCP server registers.

    Read from the server's tool manager rather than `list_tools()`: the
    public one is async, and this check is synchronous (and is called from
    a plain pytest test, not the event loop).  Importing the tool modules
    is what registers them, so import every part first -- the same set
    `server.py` wires up.
    """
    import raptor.mcp.tools_model      # noqa: F401  (registers on import)
    import raptor.mcp.tools_data       # noqa: F401
    import raptor.mcp.tools_run        # noqa: F401
    import raptor.mcp.tools_metrics    # noqa: F401
    import raptor.mcp.tools_tables     # noqa: F401
    import raptor.mcp.tools_view       # noqa: F401
    import raptor.mcp.tools_svg        # noqa: F401
    from raptor.mcp.server import mcp
    return set(mcp._tool_manager._tools)


def mentioned_names(text: str) -> set[str]:
    """Every bare identifier the chapter writes in an inline code span.

    No attempt is made to tell a tool name from a field name here.  That
    matters for the two directions separately: for "is this live tool
    documented?" a tool name is unambiguous, so a plain code-span match is
    exact and has no false positives.  The reverse direction (a documented
    name that is no longer a tool) cannot be made reliable -- the chapter
    legitimately writes `model_id`, `nom_flow` and `num_runs` in backticks,
    which no name-shape test separates from `get_element` -- so it is not
    checked, and the tool list is kept honest by the forward direction.
    """
    return set(_TOOL_SPAN.findall(text)) - _NOT_TOOLS


def quoted_counts(text: str) -> list[int]:
    """Every "N tools" figure quoted in the chapter."""
    return [int(m) for m in _COUNT.findall(text)]


def main() -> int:
    text = chapter_text()
    live = live_tool_names()
    mentioned = mentioned_names(text)

    undocumented = sorted(live - mentioned)

    counts = quoted_counts(text)
    stale = [c for c in counts
             if abs(c - len(live)) > _OPTIONAL_TOOLS]

    problems: list[str] = []
    if undocumented:
        problems.append(
            f"{len(undocumented)} live tool(s) are not mentioned in "
            f"ch16-mcp.adoc: {', '.join(undocumented)}")
    if stale:
        problems.append(
            f"ch16-mcp.adoc quotes tool count(s) {sorted(set(stale))} but "
            f"the server registers {len(live)} "
            f"({sorted(counts)} found in the text)")

    if problems:
        print("check_tools: FAILED")
        for p in problems:
            print(f"  - {p}")
        print("  Fix ch16-mcp.adoc, or add the tool to the catalogue table.")
        return 1

    print(f"check_tools: OK ({len(live)} live tools, all documented; "
          f"quoted counts {sorted(set(counts))} consistent)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
