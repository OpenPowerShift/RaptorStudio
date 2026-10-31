"""`raptor validate | info | get | set | convert`: look at and change models."""
from __future__ import annotations

import json
import shutil
import sys
from pathlib import Path
from typing import Any

from raptor.cli import exitcodes as X
from raptor.cli.common import (
    eprint, emit_json, load_set_file, open_model, workspace_for, write_text,
)
from raptor.cli.exitcodes import CliError

VALIDATE_SCHEMA = "raptor-validate/1"
INFO_SCHEMA = "raptor-info/1"


def add_parser(sub: Any) -> None:
    v = sub.add_parser(
        "validate", help="check models and report errors and warnings",
        description="Check each MODEL the way a run would, without running; "
                    "an .rbds file is also checked against the format's JSON "
                    "Schema (see `raptor schema`). Exit 3 if any model has "
                    "errors (or warnings, with --strict).")
    v.add_argument("models", nargs="+", metavar="MODEL")
    v.add_argument("-f", "--format", choices=("text", "json"), default="text")
    v.add_argument("--strict", action="store_true",
                   help="warnings are failures too")
    v.set_defaults(func=validate_command)

    sc = sub.add_parser(
        "schema", help="print the .rbds JSON Schema",
        description="Print the JSON Schema of the .rbds format (the one "
                    "`validate` and opening a file check against), for an "
                    "editor or another validator.")
    sc.add_argument("-o", "--output", metavar="FILE",
                    help="write it to FILE instead of stdout")
    sc.set_defaults(func=schema_command)

    i = sub.add_parser(
        "info", aliases=["describe"],
        help="describe a model: elements, settings, pools",
        description="Describe MODEL, or one element with --element.")
    i.add_argument("model", metavar="MODEL")
    i.add_argument("-f", "--format", choices=("text", "json", "markdown"),
                   default="text")
    i.add_argument("--element", metavar="SELECTOR",
                   help="one element, by name or Kind:N, in full")
    i.set_defaults(func=info_command)

    g = sub.add_parser(
        "get", help="print model fields",
        description="Print the current value of each PATH. 'raptor help "
                    "set-paths' lists the grammar.")
    g.add_argument("model", metavar="MODEL")
    g.add_argument("paths", nargs="+", metavar="PATH")
    g.add_argument("-f", "--format", choices=("text", "json"), default="text")
    g.set_defaults(func=get_command)

    s = sub.add_parser(
        "set", help="change model fields and save the model",
        description="Apply each PATH=VALUE to MODEL and save. Writes to -o "
                    "OUT, or with --in-place to MODEL itself (a .bak copy is "
                    "kept with --backup). Without either, nothing is "
                    "written.")
    s.add_argument("model", metavar="MODEL")
    s.add_argument("assignments", nargs="*", metavar="PATH=VALUE")
    s.add_argument("--set-file", metavar="FILE")
    out = s.add_mutually_exclusive_group()
    out.add_argument("-o", "--output", metavar="OUT")
    out.add_argument("--in-place", action="store_true")
    s.add_argument("--backup", action="store_true",
                   help="with --in-place, keep MODEL.bak")
    s.set_defaults(func=set_command)

    c = sub.add_parser(
        "convert", help="convert between .rbd and .rbds",
        description="Write IN as OUT in the format OUT's extension names "
                    "(or --to). Refuses a lossy conversion unless "
                    "--allow-lossy.")
    c.add_argument("input", metavar="IN")
    c.add_argument("output", metavar="OUT")
    c.add_argument("--to", choices=("rbd", "rbds"))
    c.add_argument("--allow-lossy", action="store_true",
                   help=".rbds-only content (annotations, experiment "
                        "settings, rate definitions) is dropped in .rbd")
    c.set_defaults(func=convert_command)


def schema_command(args: Any) -> int:
    from raptor.api.validate import rbds_schema
    text = json.dumps(rbds_schema(), indent=1) + "\n"
    if args.output:
        write_text(Path(args.output), text)
        eprint(f"wrote {args.output}")
    else:
        sys.stdout.write(text)
    return X.OK


def validate_command(args: Any) -> int:
    worst = X.OK
    docs: list[dict] = []
    with workspace_for() as ws:
        for path in args.models:
            try:
                mid = open_model(ws, path)
            except CliError as exc:
                docs.append({"model": path, "error": str(exc)})
                worst = max(worst, exc.code)
                continue
            from raptor.api.validate import issues
            found = issues(ws.get(mid).rbd)
            errs = [i for i in found if i.severity == "error"]
            warns = [i for i in found if i.severity == "warning"]
            docs.append({
                "model": path,
                "errors": [{"element": i.element, "message": i.message}
                           for i in errs],
                "warnings": [{"element": i.element, "message": i.message}
                             for i in warns],
            })
            if errs:
                worst = max(worst, X.INVALID)
            elif warns and args.strict:
                worst = max(worst, X.INVALID)
    if args.format == "json":
        emit_json({"schema": VALIDATE_SCHEMA, "results": docs}, None)
    else:
        for d in docs:
            if "error" in d:
                print(f"{d['model']}: {d['error']}")
                continue
            ne, nw = len(d["errors"]), len(d["warnings"])
            print(f"{d['model']}: {ne} error(s), {nw} warning(s)")
            for e in d["errors"]:
                print(f"  error: {e['element']}: {e['message']}")
            for w in d["warnings"]:
                print(f"  warning: {w['element']}: {w['message']}")
    return worst


def info_command(args: Any) -> int:
    from raptor.api import build, inspect as api_inspect
    with workspace_for() as ws:
        mid = open_model(ws, args.model)
        if args.element:
            try:
                data = api_inspect.get_element(ws, mid, args.element, "full")
            except Exception as exc:
                raise CliError(f"{args.element}: {exc}", X.USAGE) from exc
        else:
            data = build.describe(ws, mid, detail="full")
            data.pop("model_id", None)
    if args.format == "json":
        emit_json({"schema": INFO_SCHEMA, **data}, None)
    else:
        _print_info(data, args.format == "markdown")
    return X.OK


def _print_info(data: dict, markdown: bool) -> None:
    def show(k: str, v: Any, indent: int = 0) -> None:
        pad = "  " * indent
        if isinstance(v, dict):
            print(f"{pad}{k}:")
            for kk, vv in v.items():
                show(kk, vv, indent + 1)
        elif isinstance(v, list) and v and isinstance(v[0], dict):
            print(f"{pad}{k}: ({len(v)})")
            for item in v:
                print(pad + "  - " + ", ".join(
                    f"{a}={b}" for a, b in item.items()))
        else:
            print(f"{pad}{k}: {v}")
    if markdown:
        print(f"# {data.get('name', 'model')}\n")
    for k, v in data.items():
        show(k, v)


def get_command(args: Any) -> int:
    from raptor.api import paths
    values: dict[str, Any] = {}
    with workspace_for() as ws:
        mid = open_model(ws, args.model)
        for p in args.paths:
            try:
                values[p] = paths.get_value(ws, mid, p)
            except paths.PathError as exc:
                raise CliError(f"{p}: {exc}", X.USAGE) from exc
            except Exception as exc:
                raise CliError(f"{p}: {exc}", X.USAGE) from exc
    if args.format == "json":
        emit_json(values, None)
    elif len(values) == 1:
        v = next(iter(values.values()))
        print(json.dumps(v) if isinstance(v, (list, dict)) else v)
    else:
        for k, v in values.items():
            print(f"{k}={json.dumps(v) if isinstance(v, (list, dict)) else v}")
    return X.OK


def set_command(args: Any) -> int:
    from raptor.api import paths
    texts = list(args.assignments)
    if args.set_file:
        texts = load_set_file(args.set_file) + texts
    if not texts:
        raise CliError("nothing to set: give PATH=VALUE arguments or "
                       "--set-file", X.USAGE)
    if not args.output and not args.in_place:
        raise CliError("refusing to guess where to write: give -o OUT or "
                       "--in-place", X.USAGE)
    src = Path(args.model)
    with workspace_for() as ws:
        mid = open_model(ws, args.model)
        for t in texts:
            try:
                paths.apply_assignment(ws, mid, t)
            except paths.PathError as exc:
                raise CliError(f"{t!r}: {exc}", X.USAGE) from exc
            except Exception as exc:
                raise CliError(f"{t!r}: {exc}", X.USAGE) from exc
        dest = Path(args.output) if args.output else src
        fmt = "rbd" if dest.suffix.lower() == ".rbd" else "rbds"
        if args.in_place and args.backup:
            shutil.copy2(src, src.with_name(src.name + ".bak"))
        # Written to a temp name first so a failed save never truncates
        # the model.
        try:
            res = ws.save_model_as(mid, str(dest.with_name(
                dest.stem + ".raptor-tmp" + dest.suffix)), fmt=fmt)
            Path(res["path"]).replace(dest)
        except OSError as exc:
            raise CliError(f"cannot write {dest}: {exc}", X.OUTPUT) from exc
    eprint(f"wrote {dest} ({len(texts)} change(s))")
    return X.OK


def convert_command(args: Any) -> int:
    src, dest = Path(args.input), Path(args.output)
    fmt = args.to or ("rbd" if dest.suffix.lower() == ".rbd" else "rbds")
    if fmt == "rbd" and src.suffix.lower() == ".rbds" and not args.allow_lossy:
        with workspace_for() as ws:
            mid = open_model(ws, args.input)
            rbd = ws.get(mid).rbd
            from raptor.api.workspace import rbds_only_content
            lost = rbds_only_content(rbd)
        if lost:
            raise CliError(
                "converting to .rbd would drop: " + ", ".join(lost) +
                "; use --allow-lossy to proceed", X.USAGE)
    with workspace_for() as ws:
        mid = open_model(ws, args.input)
        try:
            ws.save_model_as(mid, str(dest), fmt=fmt)
        except Exception as exc:
            raise CliError(f"cannot write {dest}: {exc}", X.OUTPUT) from exc
    eprint(f"wrote {dest}")
    return X.OK
