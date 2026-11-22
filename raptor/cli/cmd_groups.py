"""`raptor groups`: component groups and tags from the command line."""
from __future__ import annotations

from typing import Any

from raptor.cli import exitcodes as X
from raptor.cli.common import (emit_json, open_model, save_model_back,
                               workspace_for)
from raptor.cli.exitcodes import CliError

_DESCRIPTION = (
    "Groups and tags are saved views over a model: a group may span "
    "hierarchies and a tag is free-form text on one element. Neither "
    "changes what the simulation runs. They are stored in the .rbds file "
    "and are lost by the legacy .rbd format. The model file changes only "
    "with --in-place.")


def add_parser(sub: Any) -> None:
    g = sub.add_parser("groups", help="list, edit and tag component groups",
                       description=_DESCRIPTION)
    g.add_argument("model", metavar="MODEL")
    g.set_defaults(func=_missing)
    s = g.add_subparsers(dest="groups_command", metavar="ACTION")

    p = s.add_parser("list", help="list groups and tags")
    p.add_argument("-f", "--format", choices=("text", "json"), default="text")
    p.set_defaults(func=list_command)

    p = s.add_parser("add", help="create a group (or update the same name)")
    p.add_argument("name", metavar="NAME")
    p.add_argument("--members", metavar="REFS",
                   help="comma list of Block:3 / Node:1 / Event:2 / Hier:900")
    p.add_argument("--tag", metavar="TAG",
                   help="also include every element carrying this tag")
    p.add_argument("--colour", metavar="HEX",
                   help="group colour, #RRGGBB")
    p.add_argument("--description", metavar="TEXT",
                   help="optional note shown in the selector tooltip")
    _common(p)
    p.set_defaults(func=add_command)

    p = s.add_parser("update", help="patch one group")
    p.add_argument("group", metavar="GROUP", help="group name or id")
    p.add_argument("--name", metavar="NAME", help="rename the group")
    edit_members = p.add_mutually_exclusive_group()
    edit_members.add_argument("--members", metavar="REFS",
                              help="replace the member list")
    edit_members.add_argument("--add", metavar="REFS",
                              help="add these members")
    edit_members.add_argument("--remove", metavar="REFS",
                              help="remove these members")
    p.add_argument("--tag", metavar="TAG",
                   help="set the tag query (empty string clears it)")
    p.add_argument("--colour", metavar="HEX",
                   help="group colour, #RRGGBB")
    p.add_argument("--description", metavar="TEXT",
                   help="optional note shown in the selector tooltip")
    _common(p)
    p.set_defaults(func=update_command)

    p = s.add_parser("delete", help="delete a group (elements are kept)")
    p.add_argument("group", metavar="GROUP")
    _common(p)
    p.set_defaults(func=delete_command)

    p = s.add_parser("tag", help="tag, untag or re-tag elements")
    p.add_argument("elements", nargs="+", metavar="ELEMENT",
                   help="Block:3 / Node:1 / Event:2 / Hier:900 selectors")
    p.add_argument("--tags", required=True, metavar="NAMES",
                   help="comma list of tags")
    p.add_argument("--mode", choices=("replace", "add", "remove"),
                   default="replace",
                   help="replace (default), add or remove")
    _common(p)
    p.set_defaults(func=tag_command)

    p = s.add_parser("tags", help="show one element's tags")
    p.add_argument("element", metavar="ELEMENT")
    p.add_argument("-f", "--format", choices=("text", "json"), default="text")
    p.set_defaults(func=tags_command)


def _common(parser: Any) -> None:
    parser.add_argument("--in-place", action="store_true",
                        help="write the result back to MODEL")
    parser.add_argument("-f", "--format", choices=("text", "json"),
                        default="text")


def _missing(args: Any) -> int:
    raise CliError("use 'raptor groups list|add|update|delete|tag|tags'",
                   X.USAGE)


def _refs(text: str | None) -> list[str]:
    return [part.strip() for part in (text or "").split(",") if part.strip()]


def _save_back(ws: Any, model_id: str, path: str, in_place: bool) -> None:
    if not in_place:
        return
    from pathlib import Path
    if Path(path).suffix.lower() == ".rbd":
        raise CliError(
            "groups and tags are stored in .rbds only; save the model as "
            ".rbds (or omit --in-place) before grouping it", X.USAGE)
    save_model_back(ws, model_id, path, in_place=True)


def _run(args: Any, call) -> tuple[Any, bool]:
    with workspace_for() as ws:
        mid = open_model(ws, args.model)
        result = call(ws, mid)
        _save_back(ws, mid, args.model, getattr(args, "in_place", False))
    return result, bool(getattr(args, "in_place", False))


def _fail(exc: ValueError) -> CliError:
    return CliError(str(exc), X.USAGE)


def list_command(args: Any) -> int:
    from raptor.api import groups
    try:
        data, _ = _run(args, lambda ws, mid: groups.list_groups(ws, mid))
    except ValueError as exc:
        raise _fail(exc) from exc
    if args.format == "json":
        emit_json(data, None)
        return X.OK
    if not data["groups"]:
        print("no groups")
    for row in data["groups"]:
        tag = f", tag {row['tag']}" if row["tag"] else ""
        print(f"{row['id']:<16} {row['name']} — {row['members']} member(s)"
              f"{tag}")
    if data["tags"]:
        print("tags: " + ", ".join(data["tags"]))
    return X.OK


def add_command(args: Any) -> int:
    from raptor.api import groups
    try:
        members = None if args.members is None else _refs(args.members)
        data, in_place = _run(args, lambda ws, mid: groups.create_group(
            ws, mid, args.name, members, tag=args.tag,
            colour=args.colour, description=args.description))
    except ValueError as exc:
        raise _fail(exc) from exc
    if args.format == "json":
        emit_json(data, None)
        return X.OK
    group = data["group"]
    done = "Group" if in_place else "Would create or update group"
    print(f"{done} {group['name']!r} ({group['id']}) — "
          f"{data['members']} member(s)"
          + ("" if in_place else "; pass --in-place to write"))
    return X.OK


def update_command(args: Any) -> int:
    from raptor.api import groups
    try:
        data, in_place = _run(args, lambda ws, mid: groups.update_group(
            ws, mid, args.group, name=args.name, colour=args.colour,
            description=args.description, tag=args.tag,
            members=_refs(args.members) if args.members is not None else None,
            add=_refs(args.add) or None, remove=_refs(args.remove) or None))
    except ValueError as exc:
        raise _fail(exc) from exc
    if args.format == "json":
        emit_json(data, None)
        return X.OK
    done = "Group" if in_place else "Would update group"
    print(f"{done} {data['group']['name']!r} — {data['members']} member(s)"
          + ("" if in_place else "; pass --in-place to write"))
    return X.OK


def delete_command(args: Any) -> int:
    from raptor.api import groups
    try:
        data, in_place = _run(
            args, lambda ws, mid: groups.delete_group(ws, mid, args.group))
    except ValueError as exc:
        raise _fail(exc) from exc
    if args.format == "json":
        emit_json(data, None)
        return X.OK
    print(f"{'Deleted' if in_place else 'Would delete'} group "
          f"{data['name']!r}"
          + ("" if in_place else "; pass --in-place to write"))
    return X.OK


def tag_command(args: Any) -> int:
    from raptor.api import groups
    tags = _refs(args.tags)
    try:
        data, in_place = _run(args, lambda ws, mid: groups.set_tags(
            ws, mid, args.elements, tags, mode=args.mode))
    except ValueError as exc:
        raise _fail(exc) from exc
    if args.format == "json":
        emit_json(data, None)
        return X.OK
    verb = "changed" if in_place else "would change"
    print(f"{data['count']} element(s) {verb} ({data['mode']} mode)"
          + ("" if in_place else "; pass --in-place to write"))
    return X.OK


def tags_command(args: Any) -> int:
    from raptor.api import groups
    try:
        data, _ = _run(
            args, lambda ws, mid: groups.tags_of(ws, mid, args.element))
    except ValueError as exc:
        raise _fail(exc) from exc
    if args.format == "json":
        emit_json(data, None)
        return X.OK
    print(", ".join(data["tags"]) or "no tags")
    return X.OK
