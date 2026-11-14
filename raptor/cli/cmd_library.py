"""`raptor library`: share part data between blocks and models (WP-4.3)."""
from __future__ import annotations

from pathlib import Path
from typing import Any

from raptor.cli import exitcodes as X
from raptor.cli.common import emit_json, eprint, open_model, workspace_for
from raptor.cli.exitcodes import CliError


def add_parser(sub: Any) -> None:
    lib = sub.add_parser(
        "library", help="part types: list, inspect, apply and sync",
        description="A part type is a block's data without identity or "
                    "placement; a library is a plain JSON file of them. "
                    "Applying one writes ordinary block columns and records "
                    "a link, so the model stays a normal model. The model "
                    "file changes only for apply/save/detach.")
    lib.set_defaults(func=_missing)
    s = lib.add_subparsers(dest="library_command", metavar="ACTION")

    p = s.add_parser("list", help="list the parts in a library")
    p.add_argument("library", metavar="FILE")
    p.add_argument("-f", "--format", choices=("text", "json"), default="text")
    p.set_defaults(func=list_command)

    p = s.add_parser("show", help="show one part type")
    p.add_argument("library", metavar="FILE")
    p.add_argument("part", metavar="PART")
    p.add_argument("-f", "--format", choices=("text", "json"), default="text")
    p.set_defaults(func=show_command)

    p = s.add_parser("import", help="import a legacy .rbl/.json library")
    p.add_argument("library", metavar="FILE", help="new .rbdlib to write")
    p.add_argument("source", metavar="SOURCE", help=".rbl or legacy JSON")
    p.add_argument("--name", metavar="N")
    p.add_argument("--force", action="store_true",
                   help="replace an existing library file")
    p.set_defaults(func=import_command)

    p = s.add_parser("add", help="save a model block as a new part type")
    p.add_argument("library", metavar="FILE")
    p.add_argument("model", metavar="MODEL")
    p.add_argument("--block", required=True, metavar="NAME|ID")
    p.add_argument("--name", metavar="N")
    p.add_argument("--category", default="", metavar="TEXT")
    p.add_argument("--source", default="", metavar="TEXT")
    p.add_argument("--basis", choices=("data", "handbook", "judgement"),
                   default="judgement")
    p.set_defaults(func=add_command)

    p = s.add_parser("apply", help="apply a part type to blocks and link them")
    p.add_argument("model", metavar="MODEL")
    p.add_argument("--library", required=True, metavar="FILE")
    p.add_argument("--part", required=True, metavar="PART")
    p.add_argument("--blocks", required=True, metavar="NAMES",
                   help="comma list of block names or ids")
    p.add_argument("--override", default="", metavar="FIELDS",
                   help="comma list of fields to leave as they are")
    p.add_argument("--in-place", action="store_true",
                   help="write the changes back to MODEL")
    p.set_defaults(func=apply_command)

    p = s.add_parser("sync", help="show or apply changed part types")
    p.add_argument("model", metavar="MODEL")
    p.add_argument("--library", required=True, metavar="FILE")
    p.add_argument("--apply", action="store_true",
                   help="write the pending changes (default: report only)")
    p.add_argument("--in-place", action="store_true",
                   help="with --apply, write back to MODEL")
    p.add_argument("-f", "--format", choices=("text", "json"), default="text")
    p.set_defaults(func=sync_command)

    p = s.add_parser("detach", help="drop a block's link, keeping its values")
    p.add_argument("model", metavar="MODEL")
    p.add_argument("--block", required=True, metavar="NAME|ID")
    p.add_argument("--in-place", action="store_true",
                   help="write the change back to MODEL")
    p.set_defaults(func=detach_command)


def _missing(args: Any) -> int:
    raise CliError("use 'raptor library list|show|import|add|apply|sync'",
                   X.USAGE)


def _open(path: str, *, create: bool = False):
    from raptor.api.library.model import Library, read_library
    if create and not Path(path).exists():
        return Library(Path(path).stem, {}, path=path)
    try:
        return read_library(path)
    except FileNotFoundError:
        raise CliError(f"no such library: {path}", X.USAGE) from None
    except ValueError as exc:
        raise CliError(str(exc), X.USAGE) from exc


def list_command(args: Any) -> int:
    library = _open(args.library)
    rows = [{"id": part.id, "name": part.name, "category": part.category,
             "source": part.source, "basis": part.basis,
             "version": part.version, "fields": len(part.fields)}
            for part in library.parts.values()]
    if args.format == "json":
        emit_json({"name": library.name, "parts": rows}, None)
        return X.OK
    print(f"{library.name}: {len(rows)} part(s)")
    for row in rows:
        print(f"  {row['id']:<28} {row['name']:<28} "
              f"{row['category']:<12} {row['source']}")
    return X.OK


def show_command(args: Any) -> int:
    library = _open(args.library)
    part = library.get(args.part)
    if part is None:
        raise CliError(f"no part {args.part!r} in {args.library}", X.USAGE)
    if args.format == "json":
        emit_json(part.to_dict(), None)
        return X.OK
    print(f"{part.name} ({part.id}) v{part.version}")
    print(f"category: {part.category or '—'}")
    print(f"basis: {part.basis}; source: {part.source or '—'}")
    for field_name, value in part.fields.items():
        print(f"  {field_name}: {value}")
    return X.OK


def import_command(args: Any) -> int:
    from raptor.api.library.model import import_legacy, write_library
    if Path(args.library).exists() and not args.force:
        raise CliError(f"{args.library} exists; pass --force to replace it",
                       X.USAGE)
    try:
        library = import_legacy(args.source, name=args.name)
    except (OSError, ValueError) as exc:
        raise CliError(str(exc), X.USAGE) from exc
    write_library(library, args.library)
    eprint(f"wrote {len(library.parts)} part(s) to {args.library}")
    return X.OK


def add_command(args: Any) -> int:
    from raptor.api.library.model import part_from_block, write_library
    library = _open(args.library, create=True)
    with workspace_for() as ws:
        mid = open_model(ws, args.model)
        row = _block(ws.get(mid).rbd, args.block)
        part = part_from_block(row, name=args.name, category=args.category,
                               source=args.source, basis=args.basis)
        library.parts[part.id] = part
        write_library(library, args.library)
    eprint(f"added {part.id} to {args.library}")
    return X.OK


def apply_command(args: Any) -> int:
    from raptor.api.library.links import link_block
    library = _open(args.library)
    part = library.get(args.part)
    if part is None:
        raise CliError(f"no part {args.part!r} in {args.library}", X.USAGE)
    overrides = [field.strip() for field in args.override.split(",")
                 if field.strip()]
    blocks = [name.strip() for name in args.blocks.split(",") if name.strip()]
    with workspace_for() as ws:
        mid = open_model(ws, args.model)
        for block in blocks:
            link_block(ws, mid, block, part, library=args.library,
                       overrides=overrides)
        _save_back(ws, mid, args.model, args.in_place)
    eprint(f"linked {len(blocks)} block(s) to {part.id}"
           + ("" if args.in_place else " (workspace only; pass --in-place)"))
    return X.OK


def sync_command(args: Any) -> int:
    from raptor.api.library.links import apply_updates
    library = _open(args.library)
    with workspace_for() as ws:
        mid = open_model(ws, args.model)
        result = apply_updates(ws, mid, library=library,
                               dry_run=not (args.apply and args.in_place))
        if args.apply and not args.in_place:
            eprint("changes not written: pass --in-place with --apply")
        else:
            _save_back(ws, mid, args.model, args.in_place)
    if args.format == "json":
        emit_json(result, None)
        return X.OK
    if not result["pending"]:
        print("Every linked block matches its part type.")
        return X.OK
    for entry in result["pending"]:
        print(f"{entry['name']} ({entry['block']}) follows "
              f"{entry['name_part']} {entry['version']}:")
        for field_name, values in entry["changes"].items():
            print(f"  {field_name}: {values['block']} -> {values['part']}")
    if not args.apply:
        print("\nDry run; pass --apply to write these changes.")
    else:
        print(f"\nApplied {len(result['applied'])} block update(s).")
    return X.OK


def detach_command(args: Any) -> int:
    from raptor.api.library.links import detach
    with workspace_for() as ws:
        mid = open_model(ws, args.model)
        result = detach(ws, mid, args.block)
        _save_back(ws, mid, args.model, args.in_place)
    if not result["detached"]:
        eprint("that block had no part link")
    return X.OK


def _block(rbd: Any, selector: str) -> dict[str, Any]:
    from raptor.api.build import resolve_element
    try:
        section, row = resolve_element(
            rbd, f"Block:{selector}" if selector.isdigit() else selector)
    except Exception as exc:  # noqa: BLE001 - reported as a CLI usage error
        raise CliError(str(exc), X.USAGE) from exc
    if section != "BLOCK_DATA":
        raise CliError(f"{selector!r} is not a block", X.USAGE)
    return row


def _save_back(ws: Any, model_id: str, path: str, in_place: bool) -> None:
    """Write the edited model back over its file, atomically."""
    if not in_place:
        return
    source = Path(path)
    fmt = "rbd" if source.suffix.lower() == ".rbd" else "rbds"
    temp = source.with_name(source.stem + ".raptor-tmp" + source.suffix)
    result = ws.save_model_as(model_id, str(temp), fmt=fmt)
    Path(result["path"]).replace(source)
