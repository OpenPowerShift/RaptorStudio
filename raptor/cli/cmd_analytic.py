"""`raptor analytic`: the static RBD solver."""
from __future__ import annotations

import argparse
import csv
import io
import sys
from typing import Any

from raptor.api.analytical_text import summary_text
from raptor.cli import exitcodes as X
from raptor.cli.common import (
    eprint, emit_json, open_model, workspace_for)
from raptor.cli.exitcodes import CliError


def add_parser(sub: Any) -> None:
    p = sub.add_parser(
        "analytic",
        help="exact reliability/availability of a coherent RBD",
        description="Solve the static subset of an RBD exactly: series, "
                    "parallel, mixed, k-out-of-n and hierarchies. "
                    "Reliability mode is non-repairable. Availability mode "
                    "is steady state, independent components, perfect "
                    "repair. Models outside that subset are refused with the "
                    "first offending element.")
    p.add_argument("model", metavar="MODEL")
    p.add_argument("--mode", choices=("reliability", "availability", "both"),
                   default="reliability",
                   help="reliability (R(t), non-repairable), availability "
                        "(steady state, repairable), or both with the same "
                        "settings (default reliability)")
    p.add_argument("--hierarchy", default=None, metavar="NAME|ID",
                   help="analyse one hierarchy as a standalone model; its In "
                        "node becomes Start and its Out node End")
    p.add_argument("--at", type=float, metavar="T",
                   help="mission time for reliability mode (default: the "
                        "model's run length); ignored in availability mode")
    p.add_argument("--times", metavar="T1,T2,...",
                   help="also evaluate a curve at these times")
    p.add_argument("--bx", metavar="X1,X2,...", default="10",
                   help="BX life percentages for reliability mode (default "
                        "10); ignored in availability mode")
    p.add_argument("--tau", type=float, metavar="T",
                   help="conditional reliability for another mission of T "
                        "after --at")
    p.add_argument("--importance", action="store_true", default=True,
                   help="exact Birnbaum/RAW/RRW/VF/criticality (default on)")
    p.add_argument("--no-importance", action="store_false", dest="importance")
    p.add_argument("--equation", action="store_true",
                   help="print the path-product equation")
    p.add_argument("--ignore-repairs", action="store_true",
                   help="reliability mode only: compute first-failure "
                        "reliability even when the model has repair "
                        "distributions")
    p.add_argument("--ignore", default=None, metavar="LIST",
                   help="comma-separated simulation-only features to leave "
                        "out instead of refusing: repairs (reliability "
                        "mode), pm, spares, resources")
    p.add_argument("--phase", type=_phase_arg, default=None, metavar="N|all",
                   help="analyse a phased model as if it stayed in phase N "
                        "(1-based) the whole time; all solves each phase on "
                        "its own, one result per phase")
    p.add_argument("--standby", choices=("active", "absent"), default=None,
                   help="cold-standby paths: treat as fully in service, or "
                        "as if they did not exist")
    p.add_argument("--dependencies", choices=("up", "down"), default=None,
                   help="treat every dependency as up (analyse the dependent "
                        "element's own law) or down (remove it from the "
                        "success path)")
    p.add_argument("--max-variables", type=int, default=None, metavar="N",
                   help="raise the BDD variable limit (default: solver "
                        "setting) when a large model is still tractable")
    p.add_argument("--max-bdd-nodes", type=int, default=None, metavar="N",
                   help="raise the BDD node limit (default 2,000,000, about "
                        "0.7 GB); at most half of the free memory at 350 "
                        "bytes a node")
    p.add_argument("--progress", choices=("auto", "bar", "json", "none"),
                   default="auto",
                   help="progress on stderr: one percentage for the whole "
                        "solve (building the diagram, then the results), "
                        "with the stage and the time left (auto = bar only "
                        "on a terminal)")
    p.add_argument("--xlsx", default=None, metavar="PATH",
                   help="also write the result as a spreadsheet (one set of "
                        "sheets per mode and phase with --mode both or "
                        "--phase all); a folder gets "
                        "the default name, e.g. "
                        "MODEL_analytical_reliability.xlsx")
    p.add_argument("--ods", default=None, metavar="PATH",
                   help="the same as --xlsx, as an OpenDocument "
                        "spreadsheet (.ods)")
    p.add_argument("-f", "--format", choices=("text", "json", "csv"),
                   default="text",
                   help="stdout format; with several results (--mode "
                        "both, --phase all) json is "
                        '{"results": [...]} and csv has mode and phase '
                        "columns")
    p.set_defaults(func=analytic_command)


def _phase_arg(text: str) -> int | str:
    if text.strip().lower() == "all":
        return "all"
    try:
        return int(text)
    except ValueError:
        raise argparse.ArgumentTypeError(
            f"phase must be a number or 'all', not {text!r}") from None


def _floats(text: str | None) -> list[float]:
    if not text:
        return []
    try:
        return [float(part) for part in text.split(",") if part.strip()]
    except ValueError as exc:
        raise CliError(f"bad number list {text!r}", X.USAGE) from exc


def _summary_csv(result: dict[str, Any], *,  # noqa: C901
                 mode_column: bool = False, phase_column: bool = False,
                 header: bool = True) -> str:
    out = io.StringIO()
    plain = csv.writer(out)
    lead = (([result["mode"]] if mode_column else [])
            + ([(result.get("scope") or {}).get("phase")]
               if phase_column else []))

    class _Writer:
        @staticmethod
        def writerow(row: list[Any]) -> None:
            plain.writerow([*lead, *row])

    writer = _Writer()
    if header:
        plain.writerow((["mode"] if mode_column else [])
                       + (["phase"] if phase_column else [])
                       + ["section", "name", "value"])
    writer.writerow(["summary", "model", result.get("model")])
    if result.get("hierarchy"):
        writer.writerow(["summary", "hierarchy", result["hierarchy"]])
    for key in ("time", "reliability", "pdf", "hazard", "mttf",
                "availability", "unavailability", "bdd_nodes"):
        if key in result and result[key] is not None:
            writer.writerow(["summary", key, result[key]])
    for key, value in (result.get("bx") or {}).items():
        writer.writerow(["bx", key, value])
    for item in result.get("assumptions") or []:
        writer.writerow(["assumption", "", item])
    if "conditional" in result:
        conditional = result["conditional"]
        writer.writerow(["conditional", f"tau={conditional['tau']}",
                         conditional["reliability"]])
    for point in result.get("times") or []:
        writer.writerow(["curve", point["time"], point["value"]])
    for row in result.get("components") or []:
        writer.writerow(["component", row["name"],
                         row.get("availability", row.get("reliability"))])
    for row in result.get("importance") or []:
        writer.writerow(["importance", f"{row['kind']}:{row['id']}:{row['name']}",
                         row.get("criticality")])
    if result.get("equation"):
        writer.writerow(["equation", "", result["equation"]])
    return out.getvalue()


def _progress_callback(mode: str):
    """stderr progress. ``done``/``total`` is one figure for the whole solve
    (`raptor.api.analytical._Overall`); the bar is a single line rewritten
    in place, cut to the terminal's width. It once started a new line
    whenever the stage text changed, which is every report."""
    if mode == "none" or (mode == "auto" and not sys.stderr.isatty()):
        return None
    import json as _json
    import shutil

    shown = {"line": False}

    def cb(stage: str, done: int, total: int) -> None:
        if mode == "json":
            eprint(_json.dumps({"event": "progress", "stage": stage,
                                "done": done, "total": total}))
            return
        if stage.startswith("Outlook: "):
            if shown["line"]:
                sys.stderr.write("\n")
            sys.stderr.write(stage.removeprefix("Outlook: ") + "\n")
            shown["line"] = False
            sys.stderr.flush()
            return
        percent = f"{100 * done // total:3d}% " if total else ""
        width = max(20, shutil.get_terminal_size((100, 20)).columns - 1)
        sys.stderr.write("\r" + f"{percent}{stage}"[:width].ljust(width))
        shown["line"] = True
        sys.stderr.flush()

    def finish() -> None:
        if shown["line"]:
            sys.stderr.write("\n")
            sys.stderr.flush()
    cb.finish = finish  # type: ignore[attr-defined]
    return cb


def analytic_command(args: Any) -> int:
    from raptor.api.analytical import analyse_modes
    from raptor.core.analytical.errors import AnalyticalError
    progress = _progress_callback(args.progress)
    limits: dict[str, int] = {}
    if args.max_variables is not None:
        limits["max_variables"] = args.max_variables
    if args.max_bdd_nodes is not None:
        limits["max_bdd_nodes"] = args.max_bdd_nodes
    modes = (["reliability", "availability"] if args.mode == "both"
             else [args.mode])
    with workspace_for() as ws:
        mid = open_model(ws, args.model)
        try:
            results = analyse_modes(
                ws, mid, modes,
                hierarchy=args.hierarchy,
                at=args.at,
                times=_floats(args.times),
                bx=_floats(args.bx),
                tau=args.tau,
                importance=args.importance,
                equation=args.equation,
                ignore_repairs=args.ignore_repairs,
                ignore=[x.strip() for x in (args.ignore or "").split(",")
                        if x.strip()],
                phase=args.phase,
                standby=args.standby,
                dependencies=args.dependencies,
                progress=progress,
                **limits,
            )
        except AnalyticalError as exc:
            raise CliError(str(exc), X.INVALID) from exc
        except ValueError as exc:
            raise CliError(str(exc), X.USAGE) from exc
        finally:
            if progress is not None and hasattr(progress, "finish"):
                progress.finish()
        name = ws.get(mid).name
    if args.xlsx:
        _write_workbook(args.xlsx, name, results, "xlsx")
    if args.ods:
        _write_workbook(args.ods, name, results, "ods")
    _emit(args.format, results)
    return X.OK


def _emit(format_: str, results: list[dict[str, Any]]) -> None:
    both = len(results) > 1
    modes = len({r["mode"] for r in results}) > 1
    phases = len({(r.get("scope") or {}).get("phase") for r in results}) > 1
    if format_ == "json":
        emit_json({"results": results} if both else results[0], None)
    elif format_ == "csv":
        sys.stdout.write("".join(
            _summary_csv(result, mode_column=modes, phase_column=phases,
                         header=index == 0)
            for index, result in enumerate(results)))
    else:
        print("\n\n".join(summary_text(result) for result in results))
    if format_ != "text":
        for result in results:
            for warning in result.get("warnings") or []:
                eprint(f"warning: {warning}")


def _write_workbook(target: str, name: str, results: list[dict[str, Any]],
                    ext: str) -> None:
    from pathlib import Path

    from raptor.api.analytical_export import export_file_name, export_workbook
    path = Path(target)
    if path.is_dir():
        path = path / export_file_name(name, results, ext)
    elif path.suffix.lower() != f".{ext}":
        path = path.with_name(path.name + f".{ext}")
    try:
        path = export_workbook(results, path)
    except OSError as exc:
        raise CliError(f"cannot write {path}: {exc}", X.OUTPUT) from exc
    eprint(f"wrote {path}")
