"""Console entry point for the MCP server: check the SDK, then start.

The server module imports the SDK when it is imported, so an SDK that is too
old failed with an unrelated-looking `ImportError` before any of our code
could explain it. This module imports nothing from `mcp`, checks first, and
only then imports the server."""
from __future__ import annotations

import sys

from raptor.mcp import sdk


def main(argv: list[str] | None = None) -> int:
    why = sdk.problem()
    if why is not None:
        print(f"raptor-mcp: {why}", file=sys.stderr)
        return 2
    from raptor.mcp.server import main as server_main
    return int(server_main(argv) or 0)
