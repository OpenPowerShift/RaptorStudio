"""`raptor edit`: duplicate elements and copy block data (WP-1.5)."""
from __future__ import annotations

from pathlib import Path
from typing import Any

from raptor.cli import exitcodes as X
from raptor.cli.common import (emit_json, open_model, save_model_back,
                               workspace_for)
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
    p.add_argument("--copies", type=int, default=1, metavar="N", help="how many copies (default 1)")
    p.add_argument("--dx", type=float, default=40.0, metavar="T",
                   help="each copy's offset to the right, in diagram units (default 40)")
    p.add_argument("--dy", type=float, default=40.0, metavar="T",
                   help="each copy's offset downward (default 40)")
    p.add_argument("--in-place", action="store_true",
                   help="write the result back to MODEL")
    p.add_argument("-f", "--format", choices=("text", "json"), default="text")
    p.set_defaults(func=duplicate_command)

    p = s.add_parser("paste-data", help="copy block data groups onto blocks")
    p.add_argument("--source", required=True, metavar="BLOCK", help="the block to copy from")
    p.add_argument("--targets", required=True, metavar="NAMES",
                   help="comma list of blocks")
    p.add_argument("--groups", metavar="NAMES",
                   help="comma list (default Failure,Repair); see "
                        "raptor help set-paths for group names")
    p.add_argument("--in-place", action="store_true",
                   help="write the result back to MODEL")
    p.add_argument("-f", "--format", choices=("text", "json"), default="text")
    p.set_defaults(func=paste_data_command)

    p = s.add_parser("out-of-service",
                     help="take blocks, events or hierarchies out of service",
                     description="A what-if switch that leaves the element's data "
                                 "alone: cut (down for the whole run), linked "
                                 "(never fails) or in-service (back to normal). A "
                                 "hierarchy covers everything inside it. Saved in "
                                 ".rbds only.")
    p.add_argument("elements", nargs="*", metavar="ELEMENT",
                   help="names or Kind:id selectors; none lists what is out of service")
    p.add_argument("--state", choices=("cut", "linked", "in-service"), default="cut",
                   help="cut (default), linked or in-service")
    p.add_argument("--in-place", action="store_true",
                   help="write the result back to MODEL")
    p.add_argument("-f", "--format", choices=("text", "json"), default="text")
    p.set_defaults(func=out_of_service_command)


def _missing(args: Any) -> int:
    raise CliError("use 'raptor edit duplicate|paste-data|out-of-service'", X.USAGE)


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
        save_model_back(ws, mid, args.model, in_place=args.in_place)
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
        save_model_back(ws, mid, args.model, in_place=args.in_place)
    if args.format == "json":
        emit_json(result, None)
        return X.OK
    print(f"Copied {', '.join(groups).lower()} data onto "
          f"{result['blocks']} block(s); {result['count']} cell(s) changed"
          + ("" if args.in_place else " in the workspace; pass --in-place"))
    return X.OK


def out_of_service_command(args: Any) -> int:
    from raptor.api import edit
    from raptor.api.build import BuildError
    with workspace_for() as ws:
        mid = open_model(ws, args.model)
        if args.elements:
            if args.in_place and Path(args.model).suffix.lower() == ".rbd":
                raise CliError("a legacy .rbd cannot store elements out of service; "
                               "save the model as .rbds first", X.USAGE)
            try:
                result = edit.set_out_of_service(
                    ws, mid, args.elements,
                    None if args.state == "in-service" else args.state)
            except (ValueError, KeyError, BuildError) as exc:
                raise CliError(str(exc), X.USAGE) from exc
            save_model_back(ws, mid, args.model, in_place=args.in_place)
        else:
            result = {"changed": [], "out_of_service": edit.out_of_service(ws, mid)}
    if args.format == "json":
        emit_json(result, None)
        return X.OK
    for c in result["changed"]:
        print(f"{c['kind']} {c['name']}: {c['state']}")
    outs = result["out_of_service"]
    print("Out of service: " + (", ".join(f"{o['name']} ({o['state']})" for o in outs)
                                if outs else "nothing")
          + ("" if args.in_place or not args.elements else " (in the workspace; pass --in-place)"))
    return X.OK
