"""Which `mcp` SDK the server can run on.

`raptor-studio[mcp]` pins `mcp>=2.2,<3` (pyproject.toml), but pip can leave an
older SDK in place (an environment that already has 1.x, or `--no-deps`), and
then `raptor mcp` died with "cannot import name 'MCPServer' from
'mcp.server'" while `raptor doctor` printed `ok mcp SDK: 1.30.0`. This module
is the one place that knows the range; the launcher, the server module and
the doctor all ask it. It must not import `mcp` itself.
"""
from __future__ import annotations

import re
from importlib import metadata

#: The pyproject.toml requirement, spelled out (a test keeps them equal).
REQUIRED = ">=2.2,<3"
_LOW = (2, 2)
_HIGH = (3, 0)


def installed() -> str | None:
    """The installed SDK's version, or None when it is not installed."""
    try:
        return metadata.version("mcp")
    except metadata.PackageNotFoundError:
        return None


def problem(version: str | None = ...) -> str | None:  # type: ignore[assignment]
    """What is wrong with the installed SDK, or None when it will do.

    `version` defaults to the installed one; None means "not installed"."""
    if version is ...:
        version = installed()
    if version is None:
        return ("the mcp SDK is not installed; install the MCP extra: "
                "pip install 'raptor-studio[mcp]'")
    m = re.match(r"(\d+)\.(\d+)", version)
    if m is None:
        return None                     # a version we cannot read: don't block
    major_minor = (int(m.group(1)), int(m.group(2)))
    if _LOW <= major_minor < _HIGH:
        return None
    return (f"mcp {version} is installed, but raptor-studio needs "
            f"mcp{REQUIRED}; upgrade it: pip install -U 'mcp{REQUIRED}'")
