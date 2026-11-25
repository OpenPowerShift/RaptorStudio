"""`raptor tags`: component tags from the command line."""
from __future__ import annotations

from typing import Any

from raptor.cli import exitcodes as X
from raptor.cli.common import (emit_json, open_model, save_model_back,
                               workspace_for)
from raptor.cli.exitcodes import CliError

_DESCRIPTION = (
    "Tags are free-form labels on elements, with an optional colour and "
    "description per tag. They may span hierarchies and overlap, and never "
    "change what the simulation runs. They are stored in the .rbds file "
    "and are lost by the legacy .rbd format. The model file changes only "
    "with --in-place.")


def add_parser(sub: Any) -> None:
    g = sub.add_parser("tags", help="list, apply, describe and rename tags",
                       description=_DESCRIPTION)
    g.add_argument("model", metavar="MODEL")
    g.set_defaults(func=_missing)
    s = g.add_subparsers(dest="tags_command", metavar="ACTION")

    p = s.add_parser("list", help="list tags with their element counts")
    p.add_argument("--within", type=int, metavar="HIER_ID",
                   help="count only elements inside this hierarchy")
    p.add_argument("-f", "--format", choices=("text", "json"), default="text")
    p.set_defaults(func=list_command)

    p = s.add_parser("apply", help="tag, untag or re-tag elements")
    p.add_argument("elements", nargs="+", metavar="ELEMENT",
                   help="Block:3 / Node:1 / Event:2 / Hier:900 selectors")
    p.add_argument("--tags", required=True, metavar="NAMES",
                   help="comma list of tags")
    p.add_argument("--mode", choices=("replace", "add", "remove"),
                   default="replace",
                   help="replace (default), add or remove")
    _common(p)
    p.set_defaults(func=apply_command)

    p = s.add_parser("describe",
                     help="set a tag's colour or description (creates it)")
    p.add_argument("tag", metavar="TAG")
    p.add_argument("--colour", metavar="HEX", help="#RRGGBB; empty resets")
    p.add_argument("--description", metavar="TEXT", help="empty clears")
    _common(p)
    p.set_defaults(func=describe_command)

    p = s.add_parser("rename", help="rename a tag (merges into an existing one)")
    p.add_argument("tag", metavar="TAG")
    p.add_argument("new", metavar="NEW")
    _common(p)
    p.set_defaults(func=rename_command)

    p = s.add_parser("delete", help="remove a tag from every element")
    p.add_argument("tag", metavar="TAG")
    _common(p)
    p.set_defaults(func=delete_command)

    p = s.add_parser("show", help="show one element's tags")
    p.add_argument("element", metavar="ELEMENT")
    p.add_argument("-f", "--format", choices=("text", "json"), default="text")
    p.set_defaults(func=show_command)


def _common(parser: Any) -> None:
    parser.add_argument("--in-place", action="store_true",
                        help="write the result back to MODEL")
    parser.add_argument("-f", "--format", choices=("text", "json"),
                        default="text")


def _missing(args: Any) -> int:
    raise CliError(
        "use 'raptor tags MODEL list|apply|describe|rename|delete|show'",
        X.USAGE)


def _split(text: str | None) -> list[str]:
    return [part.strip() for part in (text or "").split(",") if part.strip()]


def _save_back(ws: Any, model_id: str, path: str, in_place: bool) -> None:
    if not in_place:
        return
    from pathlib import Path
    if Path(path).suffix.lower() == ".rbd":
        raise CliError(
            "tags are stored in .rbds only; save the model as .rbds (or "
            "omit --in-place) before tagging it", X.USAGE)
    save_model_back(ws, model_id, path, in_place=True)


def _run(args: Any, call) -> tuple[Any, bool]:
    try:
        with workspace_for() as ws:
            mid = open_model(ws, args.model)
            result = call(ws, mid)
            _save_back(ws, mid, args.model, getattr(args, "in_place", False))
    except ValueError as exc:
        raise CliError(str(exc), X.USAGE) from exc
    return result, bool(getattr(args, "in_place", False))


def _done(args: Any, data: Any, in_place: bool, text: str) -> int:
    if args.format == "json":
        emit_json(data, None)
        return X.OK
    print(text + ("" if in_place else "; pass --in-place to write"))
    return X.OK


def list_command(args: Any) -> int:
    from raptor.api import tags
    data, _ = _run(args, lambda ws, mid: tags.list_tags(ws, mid, args.within))
    if args.format == "json":
        emit_json(data, None)
        return X.OK
    if not data["tags"]:
        print("no tags")
    for row in data["tags"]:
        note = f" — {row['description']}" if row["description"] else ""
        print(f"{row['tag']:<24} {row['colour']} {row['elements']:>6} "
              f"element(s){note}")
    return X.OK


def apply_command(args: Any) -> int:
    from raptor.api import tags
    names = _split(args.tags)
    data, in_place = _run(args, lambda ws, mid: tags.set_tags(
        ws, mid, args.elements, names, mode=args.mode))
    verb = "changed" if in_place else "would change"
    return _done(args, data, in_place,
                 f"{data['count']} element(s) {verb} ({data['mode']} mode)")


def describe_command(args: Any) -> int:
    from raptor.api import tags
    data, in_place = _run(args, lambda ws, mid: tags.describe_tag(
        ws, mid, args.tag, colour=args.colour, description=args.description))
    verb = "Tag" if in_place else "Would set tag"
    return _done(args, data, in_place,
                 f"{verb} {data['tag']!r}: {data['colour']}, "
                 f"{data['elements']} element(s)")


def rename_command(args: Any) -> int:
    from raptor.api import tags
    data, in_place = _run(args, lambda ws, mid: tags.rename_tag(
        ws, mid, args.tag, args.new))
    verb = "Renamed" if in_place else "Would rename"
    how = " (merged)" if data["merged"] else ""
    return _done(args, data, in_place,
                 f"{verb} {data['from']!r} to {data['to']!r}{how} on "
                 f"{data['elements']} element(s)")


def delete_command(args: Any) -> int:
    from raptor.api import tags
    data, in_place = _run(args, lambda ws, mid: tags.delete_tag(
        ws, mid, args.tag))
    verb = "Deleted" if in_place else "Would delete"
    return _done(args, data, in_place,
                 f"{verb} tag {data['deleted']!r} from "
                 f"{data['elements']} element(s)")


def show_command(args: Any) -> int:
    from raptor.api import tags
    data, _ = _run(args, lambda ws, mid: tags.tags_of(ws, mid, args.element))
    if args.format == "json":
        emit_json(data, None)
        return X.OK
    print(", ".join(data["tags"]) or "no tags")
    return X.OK
