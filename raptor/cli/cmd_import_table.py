"""`raptor import-table`: apply failure/repair/PM/stock changes from a sheet."""
from __future__ import annotations

from pathlib import Path
from typing import Any

from raptor.cli import exitcodes as X
from raptor.cli.common import emit_json, open_model, workspace_for
from raptor.cli.exitcodes import CliError


def add_parser(sub: Any) -> None:
    p = sub.add_parser(
        "import-table",
        help="import failure/repair, PM or stock values from a spreadsheet",
        description="Read an .xlsx or .ods sheet and change the failure and "
                    "repair parameters, PM settings or stock of the blocks "
                    "it names. The sheet's formula cells use the workbook's "
                    "calculated value. Every row is validated first and the "
                    "whole import is refused if any row has an error; the "
                    "model file changes only with --in-place.")
    p.add_argument("model", metavar="MODEL")
    p.add_argument("workbook", metavar="SHEET", help=".xlsx or .ods file")
    p.add_argument("--sheet", metavar="NAME",
                   help="worksheet to read (default: active/first)")
    p.add_argument("--in-place", action="store_true",
                   help="write the changes to MODEL")
    p.add_argument("-f", "--format", choices=("text", "json"),
                   default="text")
    p.set_defaults(func=import_table_command)


def _save_back(ws: Any, model_id: str, path: str, in_place: bool) -> bool:
    if not in_place:
        return False
    source = Path(path)
    fmt = "rbd" if source.suffix.lower() == ".rbd" else "rbds"
    temp = source.with_name(source.stem + ".raptor-tmp" + source.suffix)
    result = ws.save_model_as(model_id, str(temp), fmt=fmt)
    Path(result["path"]).replace(source)
    return True


def _print_plan(data: dict[str, Any]) -> None:
    print(f"{data['sheet']}: {data['summary']}")
    for change in data["changes"][:200]:
        formula = f"  [{change['formula']}]" if change["formula"] else ""
        print(f"  row {change['row']}: {change['name']} "
              f"{change['field']}: {change['old']} -> {change['new']}{formula}")
    if len(data["changes"]) > 200:
        print(f"  … {len(data['changes']) - 200} more change(s)")
    for problem in data["problems"]:
        where = f"row {problem['row']}: " if problem["row"] else ""
        print(f"  {problem['level']}: {where}{problem['message']}")


def import_table_command(args: Any) -> int:
    from raptor.api import import_tables
    workbook = Path(args.workbook).expanduser()
    if not workbook.is_file():
        raise CliError(f"no such spreadsheet: {args.workbook}", X.USAGE)
    if workbook.suffix.lower() not in (".xlsx", ".ods"):
        raise CliError(
            f"{workbook.name}: expected an .xlsx or .ods spreadsheet",
            X.USAGE)
    with workspace_for() as ws:
        mid = open_model(ws, args.model)
        try:
            data = (import_tables.apply_file(ws, mid, workbook, args.sheet)
                    if args.in_place
                    else import_tables.plan_file(ws, mid, workbook, args.sheet))
        except ValueError as exc:
            raise CliError(str(exc), X.USAGE) from exc
        _save_back(ws, mid, args.model, args.in_place)
    if args.format == "json":
        emit_json(data, None)
    else:
        _print_plan(data)
        if data["blocked"]:
            return X.USAGE
        if data["changes"] and not data["applied"]:
            print("nothing written; pass --in-place to apply")
    return X.OK if not data["blocked"] else X.USAGE
