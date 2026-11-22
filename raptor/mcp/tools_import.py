"""MCP tools: import data tables from a spreadsheet.

Thin wrappers over `raptor.api.import_tables` (which reads the workbook
with `raptor.core.fileio.sheet_reader` and plans/applies with
`raptor.core.import_tables`), the same path the desktop dialog reviews.

A review first (`preview_table_import`), then `import_table_data`. A plan
with an error is refused, never partly applied.
"""
from __future__ import annotations

from pathlib import Path
from typing import Any

from mcp.types import CallToolResult

from raptor.mcp import context
from raptor.mcp.result import EDIT, READ_ONLY, guarded, markdown_table, ok

from raptor.mcp.server import mcp

_SUFFIXES = (".xlsx", ".ods")


def _resolve_workbook(path: str) -> Path:
    """A spreadsheet inside the workspace or an --allow-read directory."""
    text = context.require_relative_path(path, "path")
    roots = [context.workspace().root, *context.allowed_read_dirs()]
    for root in roots:
        candidate = (Path(root) / text).resolve()
        if candidate.is_file() and candidate.suffix.lower() in _SUFFIXES:
            return candidate
    raise FileNotFoundError(
        f"no .xlsx or .ods spreadsheet at {path!r} in the workspace or an "
        "--allow-read directory")


def _markdown(data: dict[str, Any]) -> str:
    lines = [f"**{data['sheet']}** — {data['summary']}"]
    rows = [[str(c["row"]), c["name"], c["field"], c["old"], c["new"]]
            for c in data["changes"][:60]]
    if rows:
        lines += ["", markdown_table(["Row", "Block", "Field", "Current",
                                      "Imported"], rows)]
        if len(data["changes"]) > 60:
            lines.append(f"… {len(data['changes']) - 60} more change(s)")
    if data["problems"]:
        lines += ["", "**Problems**"]
        for problem in data["problems"]:
            where = f"row {problem['row']}: " if problem["row"] else ""
            lines.append(f"- {problem['level']}: {where}{problem['message']}")
    if data["blocked"]:
        lines += ["", "The import is blocked until the errors are fixed."]
    elif data["applied"]:
        lines += ["", "Applied and saved."]
    elif data["changes"]:
        lines += ["", "Nothing written; call `import_table_data` to apply."]
    return "\n".join(lines)


@mcp.tool(annotations=READ_ONLY("Preview a spreadsheet import"))
@guarded
def preview_table_import(model_id: str, path: str,
                         sheet: str | None = None) -> CallToolResult:
    """Say exactly what a spreadsheet would change, before changing it.

    Reads an `.xlsx` or `.ods` sheet whose headers name the block and the
    fields (failure/repair distribution and parameters, PM settings, stock).
    Formula cells use the value the spreadsheet application calculated, and
    the formula is reported beside the change. Every row is validated: an
    `error` blocks the import, a `warning` is a judgement. Nothing is
    written.
    """
    from raptor.api import import_tables
    workbook = _resolve_workbook(path)
    ws = context.workspace()
    data = import_tables.plan_file(ws, model_id, workbook, sheet)
    return ok(data, markdown=_markdown(data))


@mcp.tool(annotations=EDIT("Import a spreadsheet table"))
@guarded
def import_table_data(model_id: str, path: str,
                      sheet: str | None = None) -> CallToolResult:
    """Apply a spreadsheet's changes to the model and save it.

    Refuses a plan with any error, so a mistyped row cannot half-edit the
    model. Preview first with `preview_table_import`; the returned changes
    are the ones written. The whole import is one edit step in the
    application, and `undo_last_edit` is the way back here.
    """
    context.require_write()
    from raptor.api import import_tables
    workbook = _resolve_workbook(path)
    ws = context.workspace()
    data = import_tables.apply_file(ws, model_id, workbook, sheet)
    return ok(data, markdown=_markdown(data))
