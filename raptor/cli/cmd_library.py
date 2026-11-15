"""`raptor library`: share part data between blocks and models (WP-4.3)."""
from __future__ import annotations

from pathlib import Path
from typing import Any

from raptor.api.library.model import BASES, normalise_basis
from raptor.cli import exitcodes as X
from raptor.cli.common import emit_json, eprint, open_model, workspace_for
from raptor.cli.exitcodes import CliError


def add_parser(sub: Any) -> None:
    lib = sub.add_parser(
        "library", help="part types and assemblies: list, apply, insert, sync",
        description="A part type is an element's data without identity or "
                    "placement; an assembly is a saved group of elements (a "
                    "hierarchy with its contents, a node with its blocks, "
                    "events) with its internal links. A library is a plain "
                    "JSON file of both. Applying a part type writes ordinary "
                    "columns and records a link; inserting an assembly adds "
                    "a fresh copy with new ids and free names, so the model "
                    "stays a normal model. The model file changes only for "
                    "apply/save/detach/insert.")
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

    _add_save_and_insert(s)

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



def _add_save_and_insert(s: Any) -> None:
    """`library add` (a part type or an assembly) and `library insert`."""
    p = s.add_parser("add", help="save a model block as a new part type")
    p.add_argument("library", metavar="FILE")
    p.add_argument("model", metavar="MODEL")
    target = p.add_mutually_exclusive_group(required=True)
    target.add_argument("--block", metavar="NAME|ID",
                        help="the element whose values become a part type")
    target.add_argument("--assembly", metavar="NAMES",
                        help="comma list of elements saved as an assembly "
                             "(a hierarchy brings everything inside it)")
    p.add_argument("--kind", default="block",
                   choices=("block", "node", "event", "hierarchy",
                            "annotation"))
    p.add_argument("--name", metavar="N")
    p.add_argument("--category", default="", metavar="TEXT")
    p.add_argument("--source", default="", metavar="TEXT")
    p.add_argument("--basis", choices=BASES, default="experience",
                   type=normalise_basis,
                   help="where the numbers came from (judgement is read as "
                        "experience)")
    p.set_defaults(func=add_command)

    p = s.add_parser("insert", help="insert a copy of an assembly into a model")
    p.add_argument("model", metavar="MODEL")
    p.add_argument("--library", required=True, metavar="FILE")
    p.add_argument("--part", required=True, metavar="PART")
    p.add_argument("--at", metavar="X,Y",
                   help="model position of the copy's top-left "
                        "(default: right of the model)")
    p.add_argument("--in-place", action="store_true",
                   help="write the change back to MODEL")
    p.add_argument("-f", "--format", choices=("text", "json"), default="text")
    p.set_defaults(func=insert_command)

def _missing(args: Any) -> int:
    raise CliError("use 'raptor library list|show|import|add|insert|apply|sync|detach'",
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
    rows = [{"id": part.id, "name": part.name, "kind": part.kind,
             "category": part.category,
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
    print(f"{part.name} ({part.id}) v{part.version} [{part.kind}]")
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
    from raptor.api.library.model import part_from_element, write_library
    library = _open(args.library, create=True)
    if args.assembly:
        return _add_assembly(args, library)
    with workspace_for() as ws:
        mid = open_model(ws, args.model)
        rbd = ws.get(mid).rbd
        row = _element(rbd, args.kind, args.block)
        part = part_from_element(args.kind, row, name=args.name,
                                 category=args.category, source=args.source,
                                 basis=args.basis)
        library.parts[part.id] = part
        write_library(library, args.library)
    eprint(f"added {part.id} ({part.kind}) to {args.library}")
    return X.OK


def _add_assembly(args: Any, library: Any) -> int:
    from raptor.api.library.assembly import assembly_from_elements, describe
    from raptor.api.library.model import write_library
    names = [name.strip() for name in args.assembly.split(",") if name.strip()]
    with workspace_for() as ws:
        mid = open_model(ws, args.model)
        try:
            part = assembly_from_elements(
                ws.get(mid).rbd, names, name=args.name or names[0],
                category=args.category, source=args.source)
        except (KeyError, ValueError) as exc:
            raise CliError(str(exc), X.USAGE) from exc
    library.parts[part.id] = part
    write_library(library, args.library)
    eprint(f"added assembly {part.id} ({describe(part)}) to {args.library}")
    return X.OK


def insert_command(args: Any) -> int:
    from raptor.api.library.assembly import insert_assembly
    library = _open(args.library)
    part = library.get(args.part)
    if part is None:
        raise CliError(f"no part {args.part!r} in {args.library}", X.USAGE)
    at = None
    if args.at:
        try:
            x, y = (float(v) for v in args.at.split(","))
        except ValueError:
            raise CliError("--at takes X,Y", X.USAGE) from None
        at = (x, y)
    with workspace_for() as ws:
        mid = open_model(ws, args.model)
        try:
            result = insert_assembly(ws.get(mid).rbd, part, at=at)
        except ValueError as exc:
            raise CliError(str(exc), X.USAGE) from exc
        ws.save(mid)
        _save_back(ws, mid, args.model, args.in_place)
    if args.format == "json":
        emit_json(result, None)
        return X.OK
    print(f"inserted {part.name}: {result['count']} element(s)")
    for note in result["notes"]:
        print(f"  note: {note}")
    if not args.in_place:
        eprint("workspace only; pass --in-place to write MODEL")
    return X.OK


def apply_command(args: Any) -> int:
    from raptor.api.library.links import link_element
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
            link_element(ws, mid, part.kind, block, part,
                         library=args.library, overrides=overrides)
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


def _element(rbd: Any, kind: str, selector: str) -> dict[str, Any]:
    if kind == "annotation":
        entry = next((entry for entry in getattr(rbd, "annotations", []) or []
                      if str(entry.get("id")) == str(selector)), None)
        if entry is None:
            raise CliError(f"annotation {selector!r} not found", X.USAGE)
        return entry
    from raptor.api.build import resolve_element
    label = {"block": "Block", "node": "Node", "event": "Event",
             "hierarchy": "Hier"}[kind]
    try:
        section, row = resolve_element(
            rbd, f"{label}:{selector}" if selector.isdigit() else selector)
    except Exception as exc:  # noqa: BLE001
        raise CliError(str(exc), X.USAGE) from exc
    expected = {"block": "BLOCK_DATA", "node": "NODE_DATA",
                "event": "EVENT_DATA", "hierarchy": "HIERARCHY_DATA"}[kind]
    if section != expected:
        raise CliError(f"{selector!r} is not a {kind}", X.USAGE)
    return row


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
