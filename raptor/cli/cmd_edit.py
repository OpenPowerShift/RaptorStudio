"""`raptor edit`: duplicate elements and copy block data (WP-1.5)."""
from __future__ import annotations

from pathlib import Path
from typing import Any

from raptor.cli import exitcodes as X
from raptor.cli.common import emit_json, open_model, workspace_for
from raptor.cli.exitcodes import CliError


def add_parser(sub: Any) -> None:
    e = sub.add_parser(
        "edit", help="duplicate elements or copy a block's data",
        description="Structural edits shared with the canvas: duplicate a "
                    "selection with fresh ids and names, or copy one block's "
                    "data groups onto others. The model file changes only "
                    "when the in-place flag is given.")
    e.add_argument("model", metavar="MODEL")
    e.set_defaults(func=_missing)
    s = e.add_subparsers(dest="edit_command", metavar="ACTION")

    p = s.add_parser("duplicate", help="copy elements with fresh ids")
    p.add_argument("elements", nargs="+", metavar="ELEMENT")
    p.add_argument("--copies", type=int, default=1, metavar="N")
    p.add_argument("--dx", type=float, default=40.0, metavar="T")
    p.add_argument("--dy", type=float, default=40.0, metavar="T")
    p.add_argument("--in-place", action="store_true",
                   help="write the result back to MODEL")
    p.add_argument("-f", "--format", choices=("text", "json"), default="text")
    p.set_defaults(func=duplicate_command)

    p = s.add_parser("paste-data", help="copy block data groups onto blocks")
    p.add_argument("--source", required=True, metavar="BLOCK")
    p.add_argument("--targets", required=True, metavar="NAMES",
                   help="comma list of blocks")
    p.add_argument("--groups", metavar="NAMES",
                   help="comma list (default Failure,Repair); see "
                        "raptor help set-paths for group names")
    p.add_argument("--in-place", action="store_true",
                   help="write the result back to MODEL")
    p.add_argument("-f", "--format", choices=("text", "json"), default="text")
    p.set_defaults(func=paste_data_command)


def _missing(args: Any) -> int:
    raise CliError("use 'raptor edit duplicate|paste-data'", X.USAGE)


def _save_back(ws: Any, model_id: str, path: str, in_place: bool) -> None:
    if not in_place:
        return
    source = Path(path)
    fmt = "rbd" if source.suffix.lower() == ".rbd" else "rbds"
    temp = source.with_name(source.stem + ".raptor-tmp" + source.suffix)
    result = ws.save_model_as(model_id, str(temp), fmt=fmt)
    Path(result["path"]).replace(source)


def duplicate_command(args: Any) -> int:
    from raptor.api import edit
    if args.copies < 1:
        raise CliError("--copies must be at least 1", X.USAGE)
    with workspace_for() as ws:
        mid = open_model(ws, args.model)
        try:
            result = edit.duplicate_elements(
                ws, mid, args.elements, copies=args.copies,
                offset=(args.dx, args.dy))
        except ValueError as exc:
            raise CliError(str(exc), X.USAGE) from exc
        _save_back(ws, mid, args.model, args.in_place)
    if args.format == "json":
        emit_json(result, None)
        return X.OK
    print(f"Duplicated {result['count']} element(s)"
          + ("" if args.in_place else " in the workspace; pass --in-place"))
    return X.OK


def paste_data_command(args: Any) -> int:
    from raptor.api import edit
    groups = tuple(group.strip() for group in (args.groups or "").split(",")
                   if group.strip()) or ("Failure", "Repair")
    targets = [target.strip() for target in args.targets.split(",")
               if target.strip()]
    with workspace_for() as ws:
        mid = open_model(ws, args.model)
        try:
            result = edit.copy_element_data(ws, mid, args.source, targets,
                                            groups)
        except ValueError as exc:
            raise CliError(str(exc), X.USAGE) from exc
        _save_back(ws, mid, args.model, args.in_place)
    if args.format == "json":
        emit_json(result, None)
        return X.OK
    print(f"Copied {', '.join(groups).lower()} data onto "
          f"{result['blocks']} block(s); {result['count']} cell(s) changed"
          + ("" if args.in_place else " in the workspace; pass --in-place"))
    return X.OK
