"""Per-server runtime context for the MCP tools.

Holds the parsed CLI arguments and the single :class:`Workspace` the
server operates on, and exposes the small guards the tools use to honour
``--read-only``, ``--max-runs`` and ``--allow-write``.

Keeping this out of ``server.py`` avoids a circular import: tool modules
import :mod:`raptor.mcp.context`, never the server module.
"""
from __future__ import annotations

import re
from pathlib import Path
from typing import Any

from raptor.api.workspace import Workspace


def parsed_args() -> Any:
    """The argparse namespace stored by ``server.main`` (or ``None``)."""
    from raptor.mcp.server import mcp
    return getattr(mcp, "_raptor_args", None)


def workspace() -> Workspace:
    """Return the shared workspace, rebuilding it if the root changed.

    Rebuilding-on-root-change keeps the direct-call unit tests (which swap
    ``mcp._raptor_args`` between temp dirs) isolated from one another.
    """
    from raptor.mcp.server import mcp

    args = parsed_args()
    if args is None:
        raise RuntimeError(
            "MCP server context is not initialised; start the server through "
            "its normal entry point."
        )
    root = Path(args.workspace).resolve()
    ws = getattr(mcp, "_raptor_ws", None)
    if ws is None or ws.root != root:
        ws = Workspace(
            root,
            retain_days=int(getattr(args, "retain_days", 30)),
        )
        # Retention sweep once per workspace.
        try:
            ws.sweep_retention()
        except OSError:
            pass
        mcp._raptor_ws = ws  # type: ignore[attr-defined]
    return ws


def require_relative_path(path: str, what: str = "path") -> str:
    """Reject an absolute/`~` path at the MCP boundary.

    Every file argument names something inside the workspace (or an
    allow-listed directory), never a host path. Refusing absolute paths
    keeps a caller from probing the host filesystem and makes a path mean
    the same thing on every machine.
    """
    text = str(path)
    if (Path(text).is_absolute() or text.startswith("~")
            or re.match(r"^[A-Za-z]:[\\/]", text)):
        raise ValueError(
            f"{what} must be relative to the workspace root; absolute "
            f"paths are not allowed: {text!r}")
    return text


def require_write() -> None:
    """Refuse a mutating tool when the server runs ``--read-only``."""
    args = parsed_args()
    if args is not None and getattr(args, "read_only", False):
        raise PermissionError(
            "server is running in --read-only mode; this tool cannot "
            "modify models or run artefacts."
        )


def max_runs() -> int:
    """The hard cap on ``num_runs`` per call (``--max-runs``)."""
    args = parsed_args()
    return int(getattr(args, "max_runs", 5000))


def allowed_write_dirs() -> list[Path]:
    """Extra directories ``save_model_as`` may write to (``--allow-write``)."""
    args = parsed_args()
    return [Path(p) for p in getattr(args, "allow_write", []) or []]


def allowed_read_dirs() -> list[Path]:
    """Extra directories ``open_model`` may read from (``--allow-read``)."""
    args = parsed_args()
    return [Path(p) for p in getattr(args, "allow_read", []) or []]


def page_args(offset: Any, limit: Any, max_limit: int) -> tuple[int, int]:
    """Validate a paging pair: whole numbers, ``offset >= 0``, ``limit >= 1``.

    A limit above *max_limit* is lowered to it (every paged tool documents
    "at most N"); a negative or non-integer value is an error, not a silent
    clamp, so a caller's bug is not hidden behind a plausible-looking page.
    """
    for name, v in (("offset", offset), ("limit", limit)):
        if isinstance(v, bool) or not isinstance(v, int):
            raise ValueError(f"{name} must be a whole number, got {v!r}")
    if offset < 0:
        raise ValueError(f"offset must be 0 or more, got {offset}")
    if limit < 1:
        raise ValueError(f"limit must be 1 or more, got {limit}")
    return offset, min(limit, max_limit)


def whole_number(value: Any, name: str, *, minimum: int | None = None,
                 maximum: int | None = None) -> int:
    """A whole number argument: not a bool, not a fraction, within bounds."""
    if isinstance(value, bool) or not isinstance(value, int):
        raise ValueError(f"{name} must be a whole number, got {value!r}")
    if minimum is not None and value < minimum:
        raise ValueError(f"{name} must be at least {minimum}, got {value}")
    if maximum is not None and value > maximum:
        raise ValueError(f"{name} must be at most {maximum}, got {value}")
    return value


def check_batch(items: Any, name: str) -> None:
    """Refuse an oversized bulk-edit list before doing any work."""
    from raptor.api.limits import MAX_BATCH_ITEMS
    if len(items) > MAX_BATCH_ITEMS:
        raise ValueError(
            f"{name} has {len(items)} items; one call takes at most "
            f"{MAX_BATCH_ITEMS}. Split it up, or use build_model to create "
            "a whole model at once.")


def public_source(source: str) -> str:
    """A model's origin for display: relative to the workspace when it is
    inside it, else just the file name, so a listing never names the
    server's directories (`template:...` and `build_model` pass through)."""
    if not source or (":" in source[:10] and not source.startswith("/")):
        return source
    p = Path(source)
    if not p.is_absolute():
        return source
    try:
        return str(p.resolve().relative_to(workspace().root))
    except (ValueError, OSError):
        return p.name
