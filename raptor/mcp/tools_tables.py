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
from raptor.mcp.result import ADDITIVE, READ_ONLY, guarded, markdown_table, ok
from raptor.mcp.server import mcp

Format = Literal["csv", "txt", "txt_report", "xlsx"]

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
    'xlsx' ONE workbook with a Run Info tab and a tab per table, real
    numeric cells (the End-of-Run and interim cost reports go beside it as
    .txt files). Any combination.

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
        raise ValueError("formats cannot be empty; use csv, txt, txt_report or xlsx")
    wanted = set(formats or ["xlsx"])
    unknown = wanted - {"csv", "txt", "txt_report", "xlsx"}
    if unknown:
        raise ValueError(f"unknown format(s) {sorted(unknown)}; use csv, "
                         f"txt, txt_report, xlsx")
    options = ExportOptions(csv="csv" in wanted, txt_files="txt" in wanted,
                            txt_report="txt_report" in wanted,
                            xlsx="xlsx" in wanted)
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
