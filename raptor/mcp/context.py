"""Per-server runtime context for the MCP tools.

Holds the parsed CLI arguments and the single :class:`Workspace` the
server operates on -- constructed lazily on the first tool or resource
call, so the transport can answer `initialize`/`server/discover` without
reading a model -- and exposes the small guards the tools use to honour
``--read-only``, ``--max-runs`` and ``--allow-write``.

Keeping this out of ``server.py`` avoids a circular import: tool modules
import :mod:`raptor.mcp.context`, never the server module.
"""
from __future__ import annotations

import re
import threading
from pathlib import Path
from typing import Any

from raptor.api.workspace import Workspace, prepare_workspace_root

#: Constructing a Workspace reads the model index; two tool calls racing at
#: start-up must not build two of them (and run the retention sweep twice).
_WORKSPACE_LOCK = threading.Lock()


def parsed_args() -> Any:
    """The argparse namespace stored by ``server.main`` (or ``None``)."""
    from raptor.mcp.server import mcp
    return getattr(mcp, "_raptor_args", None)


def prepare_workspace() -> Path:
    """Create the workspace tree without reading any model.

    ``server.main`` calls this before the transport starts so a bad
    ``--workspace`` still fails fast.  The full :class:`Workspace` is built
    on the first tool call: constructing it here used to parse and
    JSON-schema-check up to 50 models, delaying the first MCP frame by
    several seconds.
    """
    from raptor.mcp.server import mcp

    args = parsed_args()
    if args is None:
        raise RuntimeError(
            "MCP server context is not initialised; start the server through "
            "its normal entry point."
        )
    root = prepare_workspace_root(
        args.workspace,
        probe_write=not bool(getattr(args, "read_only", False)),
    )
    # Pin the path this process checked.  Re-resolving `args.workspace` at
    # first use would follow a symlink swapped in after start-up.
    mcp._raptor_prepared = (str(Path(args.workspace)), root)  # type: ignore[attr-defined]
    return root


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
    prepared = getattr(mcp, "_raptor_prepared", None)
    if prepared is not None and prepared[0] == str(Path(args.workspace)):
        root, resolved = prepared[1], True
    else:
        root, resolved = Path(args.workspace).resolve(), False
    ws = getattr(mcp, "_raptor_ws", None)
    if ws is not None and ws.root == root:
        return ws
    with _WORKSPACE_LOCK:
        ws = getattr(mcp, "_raptor_ws", None)
        if ws is None or ws.root != root:
            ws = Workspace(
                root,
                retain_days=int(getattr(args, "retain_days", 30)),
                _resolved=resolved,
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


def check_study(model_id: str, trials: Any, timeout_s: Any, *,
                workers: Any = None, variants: int = 1) -> None:
    """Refuse a study (what-if, sensitivity, an optimiser) whose trials,
    deadline or size the server would not allow for a single run.

    The effective trial count is `trials`, else the model's own NumRuns, so
    leaving `trials` out cannot slip past ``--max-runs``; `variants` is the
    most variants one call may run (each at that trial count)."""
    import math

    from raptor.api.limits import MAX_TIMEOUT_S, MAX_WORKERS
    from raptor.api.run import model_num_runs
    if isinstance(timeout_s, bool) or not isinstance(timeout_s, (int, float)) \
            or not math.isfinite(timeout_s) or not 0 < timeout_s <= MAX_TIMEOUT_S:
        raise ValueError(f"timeout_s must be a number of seconds, above 0 and at "
                         f"most {MAX_TIMEOUT_S:g}")
    if workers is not None:
        whole_number(workers, "workers", minimum=1, maximum=MAX_WORKERS)
    cap = max_runs()
    if trials is None:
        effective = model_num_runs(workspace().get(model_id).rbd, strict=True)
        if effective is None:
            raise ValueError("the model has no valid NumRuns; pass trials")
        where = f"the model's NumRuns ({effective})"
    else:
        effective = whole_number(trials, "trials", minimum=2)
        where = f"trials={effective}"
    if effective > cap:
        raise ValueError(f"{where} exceeds the server cap of {cap}; lower it or "
                         "raise the cap")
    if variants > MAX_STUDY_VARIANTS:
        raise ValueError(f"this study would run {variants} variants; the most "
                         f"one call may run is {MAX_STUDY_VARIANTS}")


#: Variants (each a full batch of trials) one study call may run.
MAX_STUDY_VARIANTS = 200


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
