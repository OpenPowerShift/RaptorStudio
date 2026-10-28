"""MCP tool result helpers.

Every RAPTOR tool returns a :class:`~mcp.types.CallToolResult` carrying:

* ``structured_content`` — the machine-readable JSON the assistant reads
  and reasons about (full precision, never rounded for display); and
* ``content`` — a human/assistant-readable **Markdown** body (tables,
  headings, units), which is what the user sees and can copy.

Results are data, never screenshots, and every number is available both as
JSON and as a readable table.

Failures are returned as ``is_error=True`` results whose text says what to
do differently, never as a raw traceback, and whose
``structuredContent.error`` is ``{code, message, hint, retryable}`` so a
client can branch on the *kind* of failure (see :data:`ERROR_CODES`).
``@guarded`` converts every exception into such a result; exceptions
outside the anticipated API-layer set are additionally logged with a
traceback and reported with a generic message (no implementation detail
reaches the model).

Tool annotations (``READ_ONLY``, ``ADDITIVE``, ``EDIT``, ``DESTRUCTIVE``)
are the host-facing hints (readOnlyHint / destructiveHint / …) that let
clients auto-approve safe tools and warn on risky ones.
"""
from __future__ import annotations

import base64
import functools
import inspect
import json
import logging
from typing import Any, TypeVar
from collections.abc import Callable

from mcp.types import CallToolResult, ImageContent, TextContent, ToolAnnotations

from raptor.api.analyse import AnalysisError
from raptor.api.annotations import AnnotationNotFound
from raptor.api.build import AmbiguousName, BuildError, ElementNotFound
from raptor.core.input_rules import InputRuleViolation
from raptor.api.estimate import EstimateError
from raptor.api.render import (
    RenderError,
    RenderFailed,
    RenderOutputMissing,
    RenderTimeout,
    RenderUnavailable,
)
from raptor.api.tables import UnknownTable
from raptor.api.run import RunCancelledError, RunError, RunNotFoundError, RunTimeoutError
from raptor.api.workspace import (
    JobLimitExceeded,
    ModelLimitExceeded,
    PathOutsideWorkspace,
    RunArtefactsError,
    UnknownHandle,
    WorkspaceError,
    WorkspaceSizeExceeded,
)

F = TypeVar("F", bound=Callable[..., Any])

_log = logging.getLogger("raptor.mcp")


def _json_preview(data: Any, *, limit: int = 2000) -> str:
    """A short JSON text block, used when a tool has no Markdown body."""
    try:
        text = json.dumps(data, indent=2, default=str)
    except (TypeError, ValueError):
        text = str(data)
    if len(text) > limit:
        text = text[:limit] + "\n… (truncated; see structuredContent)"
    return text


def _as_object(data: Any) -> dict[str, Any]:
    """``structuredContent`` must be a JSON object per the MCP spec.

    Tools that naturally return a list are wrapped under ``"items"``.
    """
    if isinstance(data, dict):
        return data
    return {"items": data}


def ok(data: Any, markdown: str | None = None) -> CallToolResult:
    """Build a successful result from structured *data* and optional Markdown."""
    content: list[Any] = []
    if markdown:
        content.append(TextContent(type="text", text=markdown))
    if not content:
        content.append(TextContent(type="text", text=_json_preview(data)))
    return CallToolResult(content=content, structured_content=_as_object(data))


def image(png: bytes, markdown: str, meta: dict[str, Any]) -> CallToolResult:
    """Build a result carrying a PNG image plus structured metadata.

    Only ``render_diagram`` ever uses this.
    """
    content: list[Any] = [
        ImageContent(
            type="image",
            data=base64.b64encode(png).decode("ascii"),
            mime_type="image/png",
        ),
        TextContent(type="text", text=markdown),
    ]
    return CallToolResult(content=content, structured_content=_as_object(meta))


#: The error taxonomy.  ``code`` is stable and safe to branch on.
ERROR_CODES = (
    "invalid_argument",     # the call's input is wrong -- fix it and retry
    "not_found",            # an id/name/path does not exist
    "permission_denied",    # blocked by --read-only or workspace confinement
    "precondition_failed",  # valid input, but the model/state isn't ready
    "limit_exceeded",       # a server cap (runs, models, disk) was hit
    "cancelled",            # the client cancelled the operation
    "timeout",              # the operation exceeded its time budget
    "unavailable",          # an optional capability is missing (e.g. Qt)
    "internal",             # a server bug -- not the caller's fault
)


def fail(message: str, *, code: str = "invalid_argument",
         hint: str | None = None, retryable: bool = False) -> CallToolResult:
    """Build a tool *execution* error (``isError: true``).

    The text is for the model to read; ``structuredContent.error`` is the
    machine-readable ``{code, message, hint, retryable}`` object.
    """
    err: dict[str, Any] = {"code": code, "message": message,
                           "retryable": retryable}
    if hint:
        err["hint"] = hint
    text = message if not hint else f"{message}\nHint: {hint}"
    return CallToolResult(
        content=[TextContent(type="text", text=text)],
        structured_content={"error": err},
        is_error=True,
    )


#: Anticipated API-layer failures.  These carry an actionable message the
#: model can read and act on, so they are surfaced verbatim.  Anything
#: else is a bug: it is logged with a traceback and reported generically.
_ANTICIPATED = (
    ValueError,
    KeyError,
    TypeError,
    OSError,
    PermissionError,
    FileNotFoundError,
    WorkspaceError,
    BuildError,
    RunError,
    EstimateError,
    AnalysisError,
    RenderError,
    NotImplementedError,
)

# Ordered most-specific first.  (exception type, code, hint, retryable)
_CLASSIFY: tuple[tuple[type, str, str | None, bool], ...] = (
    (RunCancelledError, "cancelled", None, False),
    (RunTimeoutError, "timeout",
     "Use fewer runs, a shorter run_length, or confirm_long_run=true.", False),
    (RunArtefactsError, "precondition_failed",
     "That artefact was not recorded for this run; enable the relevant "
     "analysis/recording option and re-run the simulation.", False),
    (NotImplementedError, "precondition_failed",
     "The model uses an engine feature that is not ported yet; the "
     "message names the element and feature.", False),
    (RunNotFoundError, "not_found",
     "Run ids come from simulate; they may have been purged by retention.", False),
    (RenderUnavailable, "unavailable",
     "Diagram rendering needs PySide6/Qt, which is not importable in this "
     "server; all results remain available as Markdown/data.", False),
    (RenderTimeout, "timeout",
     "The render worker timed out; reduce max_px or complexity.", True),
    (RenderOutputMissing, "precondition_failed",
     "The render worker reported success but produced no image.", True),
    (RenderFailed, "precondition_failed",
     "Check `level` is a hierarchy id (render with level=0 for the whole "
     "model, then read the `Hierarchies` table that describe_model(detail="
     "'full') prints -- each row's id is the level to pass and the "
     "selector to use elsewhere), and that the model is valid.", False),
    (RenderError, "precondition_failed", None, False),
    (UnknownHandle, "not_found",
     "Call list_models for the open model_ids.", False),
    (PathOutsideWorkspace, "permission_denied",
     "Call workspace_info for the writable directories; relative paths "
     "resolve against the workspace root.", False),
    (JobLimitExceeded, "limit_exceeded",
     "Wait for a running job to finish (run_status), or stop one with "
     "cancel_run, then try again.", True),
    (ModelLimitExceeded, "limit_exceeded",
     "Close models you no longer need with close_model.", False),
    (WorkspaceSizeExceeded, "limit_exceeded",
     "Delete old runs or close models to free space.", False),
    (PermissionError, "permission_denied", None, False),
    (FileNotFoundError, "not_found", None, False),
    (UnknownTable, "not_found",
     "Call list_tables for the table keys of this model and run.", False),
    (KeyError, "not_found", None, False),
    (EstimateError, "precondition_failed", None, False),
    (AnalysisError, "precondition_failed", None, False),
    (ElementNotFound, "not_found",
     "Call describe_model(detail='full') to list element names and ids.", False),
    (AnnotationNotFound, "not_found",
     "Call list_annotations for valid annotation ids.", False),
    (AmbiguousName, "invalid_argument",
     "Use the 'Block:N' / 'Node:N' form to pick one.", False),
    (InputRuleViolation, "invalid_argument",
     "RAPTOR Studio refuses this value or link; the model was left unchanged. "
     "The message is RAPTOR Studio's own; change the value and try again.", False),
    (BuildError, "invalid_argument", None, False),
    (ValueError, "invalid_argument", None, False),
    (TypeError, "invalid_argument", None, False),
    (OSError, "precondition_failed", None, False),
    (RunError, "precondition_failed", None, False),
    (WorkspaceError, "precondition_failed", None, False),
)


def _classify(exc: BaseException) -> tuple[str, str | None, bool]:
    for typ, code, hint, retry in _CLASSIFY:
        if isinstance(exc, typ):
            return code, hint, retry
    return "internal", None, False


def _message(exc: BaseException) -> str:
    # KeyError's str() wraps the message in quotes.
    if isinstance(exc, KeyError) and exc.args:
        return str(exc.args[0])
    return str(exc) or type(exc).__name__


def error_result(exc: BaseException, tool: str = "tool") -> CallToolResult:
    """Convert *exc* into a structured ``isError`` result."""
    if not isinstance(exc, _ANTICIPATED):
        _log.exception("tool %s failed unexpectedly", tool)
        return fail(
            f"{tool} failed because of an internal server error. "
            "This is not a problem with your input; details are in the "
            "server log.",
            code="internal", retryable=False,
        )
    code, hint, retry = _classify(exc)
    return fail(_message(exc), code=code, hint=hint, retryable=retry)


def guarded[F: Callable[..., Any]](fn: F) -> F:
    """Wrap a tool so failures become structured ``isError`` results.

    Works for plain and ``async`` tools.  ``functools.wraps`` keeps
    ``__wrapped__`` (and therefore the signature the MCP SDK introspects
    for the input schema, including a ``Context`` parameter) intact.
    """
    if inspect.iscoroutinefunction(fn):
        @functools.wraps(fn)
        async def awrapper(*args: Any, **kwargs: Any) -> CallToolResult:
            try:
                result = await fn(*args, **kwargs)
            except Exception as exc:  # noqa: BLE001 - the tool boundary
                return error_result(exc, fn.__name__)
            return result if isinstance(result, CallToolResult) else ok(result)

        return awrapper  # type: ignore[return-value]

    @functools.wraps(fn)
    def wrapper(*args: Any, **kwargs: Any) -> CallToolResult:
        try:
            result = fn(*args, **kwargs)
        except Exception as exc:  # noqa: BLE001 - the tool boundary
            return error_result(exc, fn.__name__)
        return result if isinstance(result, CallToolResult) else ok(result)

    return wrapper  # type: ignore[return-value]


# ---------------------------------------------------------------------------
# Tool annotations (host-facing hints)
# ---------------------------------------------------------------------------

def _ann(title: str, **kw: Any) -> ToolAnnotations:
    return ToolAnnotations(title=title, open_world_hint=False, **kw)


def READ_ONLY(title: str) -> ToolAnnotations:
    """Never changes models, files or run history (safe to auto-approve)."""
    return _ann(title, read_only_hint=True, idempotent_hint=True)


def ADDITIVE(title: str) -> ToolAnnotations:
    """Creates new things (models, runs); never overwrites or removes."""
    return _ann(title, read_only_hint=False, destructive_hint=False,
                idempotent_hint=False)


def EDIT(title: str, *, idempotent: bool = True) -> ToolAnnotations:
    """Edits a model in place; reversible with ``undo_last_edit``."""
    return _ann(title, read_only_hint=False, destructive_hint=False,
                idempotent_hint=idempotent)


def DESTRUCTIVE(title: str) -> ToolAnnotations:
    """May delete or overwrite data."""
    return _ann(title, read_only_hint=False, destructive_hint=True,
                idempotent_hint=False)


def markdown_table(headers: list[str], rows: list[list[Any]]) -> str:
    """Render a simple Markdown table."""
    lines = [
        "| " + " | ".join(str(h) for h in headers) + " |",
        "| " + " | ".join("---" for _ in headers) + " |",
    ]
    for row in rows:
        lines.append("| " + " | ".join(_cell(v) for v in row) + " |")
    return "\n".join(lines)


def _cell(value: Any) -> str:
    if value is None:
        return "N/A"
    if isinstance(value, float):
        return f"{value:.6g}"
    # Model-controlled text: keep it inside one table cell and don't let a
    # pipe create a fake column.  This is containment, not a substitute for
    # treating the content as untrusted data.
    return (str(value).replace("\\", "\\\\").replace("|", "\\|")
            .replace("\r", " ").replace("\n", " "))
