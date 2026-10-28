"""Allow ``python -m raptor.mcp`` to start the MCP server."""
from raptor.mcp.server import main

raise SystemExit(main())
