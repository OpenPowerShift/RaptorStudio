"""MCP tools, part 4: tables.

``list_tables`` / ``get_table`` read the model's input tables and a run's
result tables; ``export_tables`` writes them as CSV, text or one spreadsheet
with a tab per table. These are the same tables, from the same code
(:mod:`raptor.api.tables`), as the GUI's *Export Tables* dialog.
"""
from __future__ import annotations

from typing import Any, Literal

from mcp.types import CallToolResult

from raptor.mcp import context
from raptor.mcp.result import (
    ADDITIVE, EDIT, READ_ONLY, guarded, markdown_table, ok,
)
from raptor.mcp.server import mcp

Format = Literal["csv", "txt", "txt_report", "xlsx", "ods"]

_ROW_CAP = 200


def _rbd_for(handle, run_id: str | None):
    """The model an artefact belongs to.

    An isolated run stores the extract it actually ran; its input tables
    must come from that extract, not from the source model.
    """
    if run_id:
        from raptor.api.run import run_dir
        path = run_dir(context.workspace(), run_id) / "isolated_model.rbds"
        if path.is_file():
            from raptor.core.fileio.rbds import load_rbds
            return load_rbds(str(path))
    return handle.rbd


def _bundle(model_id: str, run_id: str | None):
    if not run_id:
        return None
    from raptor.api.run_tables import bundle_from_run
    return bundle_from_run(context.workspace(), run_id, model_id=model_id)


def _isolation(run_id: str | None) -> dict | None:
    """The isolation record for a run, or None (shared by every table tool)."""
    if not run_id:
        return None
    from raptor.api.run import isolation_metadata
    return isolation_metadata(context.workspace(), run_id)


@mcp.tool(annotations=READ_ONLY("List tables"))
@guarded
def list_tables(model_id: str, run_id: str | None = None) -> CallToolResult:
    """List the tables that can be read or exported.

    Two groups: **Inputs** (the model's data: blocks, nodes, events,
    hierarchies, links, spare pools, resources, triggers, phases, system
    settings) and **Results** (a run's report sections, per-run figures and
    capacity samples). Pass `run_id` (from `simulate`) to include a run's
    result tables; without it only the inputs are listed and the result
    tables are shown as unavailable.

    Returns {tables: [{key, title, group, rows, available, reason,
    default}]}. `key` is what `get_table` and `export_tables` take, e.g.
    'inputs.blocks' or 'results.summary'. `default` is false for the long
    ones (event log, end-of-run report, interim cost, all block columns).
    """
    from raptor.api.tables import catalogue
    handle = context.workspace().get(model_id)
    infos = catalogue(_rbd_for(handle, run_id), _bundle(model_id, run_id))
    isolation = _isolation(run_id)
    md = markdown_table(
        ["Key", "Table", "Group", "Rows", "Available"],
        [[i.key, i.title, i.group, i.rows,
          "yes" if i.available else f"no - {i.reason}"] for i in infos])
    data: dict[str, Any] = {"model_id": model_id, "run_id": run_id,
                            "tables": [i.to_dict() for i in infos]}
    if isolation:
        data["isolation"] = isolation
    return ok(data, markdown=md)


@mcp.tool(annotations=READ_ONLY("Get table"))
@guarded
def get_table(model_id: str, table: str, run_id: str | None = None,
              offset: int = 0, limit: int = 100) -> CallToolResult:
    """Read one table: its column headers and a page of rows.

    `table` is a key from `list_tables` (e.g. 'inputs.blocks',
    'results.summary'; result tables need `run_id`). Rows are returned as
    lists in header order, as the model file holds them (text such as
    "12.500000") or as numbers for computed tables. `limit` is at most 200;
    use `offset` and `next_offset` to page. Returns {key, title, group,
    headers, rows, total_rows, next_offset, notes}.
    """
    from raptor.api.tables import collect
    handle = context.workspace().get(model_id)
    offset, limit = context.page_args(offset, limit, _ROW_CAP)
    run_rbd = _rbd_for(handle, run_id)
    (t,) = collect(run_rbd, _bundle(model_id, run_id), [table])
    data = t.to_dict(limit=limit, offset=offset)
    # Result tables are in the model's native units; say so, and say
    # whether a display unit is configured (they are not converted).
    sys_sec = run_rbd.sections.get("SYSTEM_DATA")
    data["units"] = (str(sys_sec.rows[0].get("units", "")).strip()
                     if sys_sec and sys_sec.rows else "")
    data["display_unit"] = getattr(run_rbd, "display_unit", None) or ""
    end = offset + limit
    data["next_offset"] = end if end < len(t.rows) else None
    md = f"**{t.title}** ({t.group}) - {len(t.rows)} rows\n\n" + \
        markdown_table(t.headers, data["rows"])
    if t.notes:
        md += "\n\n" + "\n".join(f"_{n}_" for n in t.notes)
    if data["next_offset"] is not None:
        md += f"\n\n_More rows: call again with offset={end}._"
    isolation = _isolation(run_id)
    if isolation:
        data["isolation"] = isolation
        md = ("**Isolated hierarchy** — " + isolation.get("hierarchy", "")
              + ". Not a whole-model result.\n\n" + md)
    return ok(data, markdown=md)


@mcp.tool(annotations=ADDITIVE("Export tables"))
@guarded
def export_tables(model_id: str, run_id: str | None = None,
                  tables: list[str] | None = None,
                  formats: list[Format] | None = None,
                  folder: str | None = None) -> CallToolResult:
    """Write tables to files: CSV, text, or one spreadsheet.

    `tables`: keys from `list_tables`; omit for the default set (all input
    tables except the all-columns block table, plus every result table
    except the long ones). Result tables need `run_id`.

    `formats` (default ['xlsx']): 'csv' one .csv per table; 'txt' one
    fixed-width .txt per table; 'txt_report' ONE combined text report;
    'xlsx' ONE Excel workbook and 'ods' the same as an OpenDocument
    spreadsheet: a Contents tab, a Run Info tab, a tab per table and a
    Glossary, real numeric cells, filters, frozen headers and print set-up
    (the End-of-Run and interim cost reports go beside it as .txt files).
    Any combination.

    `folder` is inside the workspace (relative paths resolve against its
    root); default `Tables/<model>` (or `Tables/<model>_<run_id>`).
    Existing files of the same names are replaced.

    Returns {folder, tables, files: [path], resource_links}.
    """
    context.require_write()
    from raptor.api.table_export import (
        TABLES_DIRNAME, ExportOptions, export_tables as _export, safe_stem)
    from raptor.api.tables import collect
    from raptor.api.presentation import run_info
    ws = context.workspace()
    handle = ws.get(model_id)
    bundle = _bundle(model_id, run_id)
    run_rbd = _rbd_for(handle, run_id)
    selected = collect(run_rbd, bundle, tables)
    if formats is not None and not formats:
        raise ValueError("formats cannot be empty; use csv, txt, "
                         "txt_report, xlsx or ods")
    wanted = set(formats or ["xlsx"])
    unknown = wanted - {"csv", "txt", "txt_report", "xlsx", "ods"}
    if unknown:
        raise ValueError(f"unknown format(s) {sorted(unknown)}; use csv, "
                         f"txt, txt_report, xlsx, ods")
    options = ExportOptions(csv="csv" in wanted, txt_files="txt" in wanted,
                            txt_report="txt_report" in wanted,
                            xlsx="xlsx" in wanted, ods="ods" in wanted)
    stem = safe_stem(handle.name)
    if folder is not None:
        folder = context.require_relative_path(folder, "folder")
    dest = ws.resolve_output_path(
        folder or f"{TABLES_DIRNAME}/{stem}" + (f"_{run_id}" if run_id
                                                 else ""))
    target = dest.resolve()
    # The model tree is managed state (including closed models that must
    # survive a restart), not an export destination.
    managed = {
        "models": (ws.root / "models").resolve(),
        "runs": (ws.root / "runs").resolve(),
        "renders": (ws.root / "renders").resolve(),
    }
    for label, root in managed.items():
        if target == root or root in target.parents:
            raise ValueError(
                f"export folder cannot be inside the {label} tree; "
                "choose a folder under Tables/")
    info = (run_info(run_rbd, model=handle.name, runs=bundle.num_runs,
                     units=bundle.units) if bundle is not None else None)
    result = _export(selected, dest, options, model_name=handle.name,
                     stem=stem, info=info)
    data = result.to_dict()
    isolation = _isolation(run_id)
    if isolation:
        data["isolation"] = isolation
    data["resource_links"] = [f"file://{f}" for f in result.files]
    md = (f"Wrote {len(result.files)} file(s) for {len(selected)} table(s) "
          f"to `{result.folder}`:\n\n" +
          "\n".join(f"- `{f.name}`" for f in result.files))
    if isolation:
        md = ("**Isolated hierarchy** — " + isolation.get("hierarchy", "")
              + ". Not a whole-model result.\n\n" + md)
    return ok(data, markdown=md)


@mcp.tool(annotations=ADDITIVE("Export report"))
@guarded
def export_report(run_id: str, path: str | None = None,
                  sections: list[Literal["diagram", "story", "results",
                                         "data_tables"]] | None = None,
                  title: str | None = None) -> CallToolResult:
    """Write a run's report as one PDF or one self-contained HTML file: the
    same document as the GUI's File > Export PDF, for a reader who wants the
    answer, not the tables.

    `sections` (default ['diagram', 'story']): 'story' is the headline, what
    drives the downtime, the findings, the charts and how to read each
    measure; 'results' adds every result table (dozens of pages on a large
    model); 'data_tables' the model's input tables.  The run needs
    `attribution=True` for the drivers part of the story.

    `path` is inside the workspace and ends in .pdf or .html; default
    `Reports/<model>_<run_id>.pdf`.  Returns {path, sections, bytes}.
    """
    context.require_write()
    from raptor.api.report import ReportError, write_report
    from raptor.api.run import run_dir
    from raptor.api.run_tables import read_summary
    from raptor.api.table_export import safe_stem
    ws = context.workspace()
    summary = read_summary(run_dir(ws, run_id), run_id, required=True)
    chosen = tuple(sections or ("diagram", "story"))
    if path is not None:
        path = context.require_relative_path(path, "path")
    stem = safe_stem(str(summary.get("model_name") or "model"))
    dest = ws.resolve_output_path(path or f"Reports/{stem}_{run_id}.pdf")
    for label in ("models", "runs", "renders"):
        root = (ws.root / label).resolve()
        if root == dest.resolve() or root in dest.resolve().parents:
            raise ValueError(f"a report cannot go inside the {label} tree; "
                             "choose a path under Reports/")
    dest.parent.mkdir(parents=True, exist_ok=True)
    try:
        written = write_report(ws, run_id, dest, title=title, sections=chosen)
    except ReportError as exc:
        raise ValueError(str(exc)) from exc
    size = written.stat().st_size
    return ok({"path": str(written), "sections": list(chosen), "bytes": size,
               "resource_links": [f"file://{written}"]},
              markdown=f"Wrote the report to `{written}` ({size:,} bytes).")


@mcp.tool(annotations=EDIT("Replace in table"))
@guarded
def replace_in_table(model_id: str, table: str, column: str, find: str,
                     replace: str, whole: bool = False,
                     case: bool = True) -> CallToolResult:
    """Find and replace in one model table column.

    `table` is an input table key that edits model rows:
    `inputs.blocks_all`, `inputs.nodes`, `inputs.events`,
    `inputs.hierarchies`, `inputs.spare_pools`, `inputs.resources`,
    `inputs.triggers`, `inputs.phases` or `inputs.comments`. `column` is a
    file column name or index. `whole` matches the whole cell; `case` is
    case-sensitive. Name columns are validated as a manual rename is, and a
    refused set changes nothing. Returns the old and new value of every
    changed cell.
    """
    from raptor.api import build, tables

    ws = context.workspace()
    rbd = ws.get(model_id).rbd
    try:
        changes = tables.replace_in_table(
            rbd, table, column, find, replace, whole=whole, case=case)
    except ValueError as exc:
        raise ValueError(str(exc)) from exc
    for change in changes:
        change["row"][change["column"]] = change["new"]
    if changes:
        build.sync_counts(rbd)
        ws.save(model_id)
    data: dict[str, Any] = {
        "changes": [{key: value for key, value in change.items()
                     if key != "row"} for change in changes],
            "count": len(changes), "table": table, "column": column}
    lines = ["| Row | Column | Old | New |", "|---|---|---|---|"]
    for change in data["changes"][:50]:
        lines.append(f"| {change['id']} | {change['column']} | "
                     f"{change['old']} | {change['new']} |")
    return ok(data, "\n".join(lines) if changes else "No cell matched.")
