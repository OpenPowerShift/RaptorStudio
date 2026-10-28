"""Smoke-test an installed RAPTOR Studio, the way a user gets it from an index.

Run it with the Python of an environment where `raptor-studio-mcp` (and so
`raptor-studio[mcp]`) has been installed from TestPyPI, PyPI or a local
`dist/`. It checks the things a broken upload would break first:

- `raptor --version` reports the expected version;
- a bundled example model validates and simulates from the command line;
- `raptor-studio-mcp` starts and answers an MCP handshake over stdio.

The release workflow runs it against TestPyPI before PyPI is touched, and
`RELEASING.md` uses it for the local check before tagging.

    python tools/release_smoke.py 0.8.0
"""
from __future__ import annotations

import asyncio
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path


def _run(*args: str) -> str:
    print("$", " ".join(args), flush=True)
    out = subprocess.run(args, capture_output=True, text=True, timeout=600, check=False)
    if out.returncode != 0:
        sys.exit(f"failed ({out.returncode}):\n{out.stdout}\n{out.stderr}")
    return out.stdout


def _tool(name: str) -> str:
    """The console script next to this interpreter, not one elsewhere on PATH."""
    here = Path(sys.executable).parent / name
    found = str(here) if here.exists() else shutil.which(name)
    if not found:
        sys.exit(f"{name} is not installed in this environment")
    return found


async def _mcp_handshake(server: str) -> int:
    from mcp import ClientSession, StdioServerParameters
    from mcp.client.stdio import stdio_client

    params = StdioServerParameters(
        command=server, args=["--workspace", tempfile.mkdtemp(), "--no-render"])
    async with stdio_client(params) as (read, write), ClientSession(read, write) as session:
        await session.initialize()
        names = {tool.name for tool in (await session.list_tools()).tools}
    missing = {"open_model", "simulate", "get_report"} - names
    if missing:
        sys.exit(f"MCP server is missing tools: {sorted(missing)}")
    return len(names)


def main(argv: list[str]) -> int:
    if len(argv) != 1:
        sys.exit(__doc__)
    expected = argv[0]

    version = _run(_tool("raptor"), "--version")
    if not version.startswith(f"raptor {expected} "):
        sys.exit(f"expected version {expected}, got: {version.strip()}")

    from raptor import bundled

    model = Path(bundled.data_root()) / "new_docs" / "models" / "Bakery1.rbds"
    if not model.exists():
        sys.exit(f"bundled example model not installed: {model}")
    _run(_tool("raptor"), "validate", str(model))
    report = _run(_tool("raptor"), "run", str(model), "--runs", "20", "--seed", "1")
    if "Green_Percent" not in report:
        sys.exit(f"simulation printed no results:\n{report}")

    count = asyncio.run(asyncio.wait_for(_mcp_handshake(_tool("raptor-studio-mcp")), 120))
    print(f"ok: raptor {expected}; example model simulated; MCP server answered with {count} tools")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
