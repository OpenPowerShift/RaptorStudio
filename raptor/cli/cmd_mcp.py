"""`raptor mcp`: start the MCP server (stdio or HTTP)."""
from __future__ import annotations

from typing import Any


def add_parser(sub: Any) -> None:
    p = sub.add_parser(
        "mcp", help="start the MCP server (stdio or HTTP)",
        description="Start the MCP server so an AI assistant can build, "
                    "validate and simulate models. Options are those of "
                    "raptor-studio-mcp.", add_help=False)
    p.add_argument("rest", nargs="*", help=argparse_remainder())
    p.set_defaults(func=mcp_command, _passthrough=True)


def argparse_remainder() -> str:
    return "options are passed through; try 'raptor mcp --help'"


def mcp_command(args: Any) -> int:
    from raptor.mcp.launch import main as mcp_main
    return int(mcp_main(list(args.rest)) or 0)
