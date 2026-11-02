"""`raptor analytic`: the static RBD solver."""
from __future__ import annotations

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
    p.add_argument("--mode", choices=("reliability", "availability"),
                   default="reliability",
                   help="reliability (R(t), non-repairable) or availability "
                        "(steady state, repairable) (default reliability)")
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
    p.add_argument("--phase", type=int, default=None, metavar="N",
                   help="analyse a phased model as if it stayed in phase N "
                        "(1-based) the whole time")
    p.add_argument("--standby", choices=("active", "absent"), default=None,
                   help="cold-standby paths: treat as fully in service, or "
                        "as if they did not exist")
    p.add_argument("--max-variables", type=int, default=None, metavar="N",
                   help="raise the BDD variable limit (default: solver "
                        "setting) when a large model is still tractable")
    p.add_argument("--max-bdd-nodes", type=int, default=None, metavar="N",
                   help="raise the BDD node limit (default: solver setting) "
                        "for a large but structurally simple model")
    p.add_argument("--progress", choices=("auto", "bar", "json", "none"),
                   default="auto",
                   help="progress on stderr: BDD construction and the "
                        "importance pass (auto = bar only on a terminal)")
    p.add_argument("-f", "--format", choices=("text", "json", "csv"),
                   default="text")
    p.set_defaults(func=analytic_command)


def _floats(text: str | None) -> list[float]:
    if not text:
        return []
    try:
        return [float(part) for part in text.split(",") if part.strip()]
    except ValueError as exc:
        raise CliError(f"bad number list {text!r}", X.USAGE) from exc


def _summary_csv(result: dict[str, Any]) -> str:
    out = io.StringIO()
    writer = csv.writer(out)
    writer.writerow(["section", "name", "value"])
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
    if mode == "none" or (mode == "auto" and not sys.stderr.isatty()):
        return None
    import json as _json

    last = {"stage": ""}

    def cb(stage: str, done: int, total: int) -> None:
        if mode == "json":
            eprint(_json.dumps({"event": "progress", "stage": stage,
                                "done": done, "total": total}))
            return
        if stage != last["stage"]:
            if last["stage"]:
                sys.stderr.write("\n")
            last["stage"] = stage
        total_text = f"/{total}" if total else ""
        sys.stderr.write(f"\r{stage} {done}{total_text}")
        if total and done >= total:
            sys.stderr.write("\n")
            last["stage"] = ""
        sys.stderr.flush()
    return cb


def analytic_command(args: Any) -> int:
    from raptor.api.analytical import analyse
    from raptor.core.analytical.errors import AnalyticalError
    progress = _progress_callback(args.progress)
    limits: dict[str, int] = {}
    if args.max_variables is not None:
        limits["max_variables"] = args.max_variables
    if args.max_bdd_nodes is not None:
        limits["max_bdd_nodes"] = args.max_bdd_nodes
    with workspace_for() as ws:
        mid = open_model(ws, args.model)
        try:
            result = analyse(
                ws, mid,
                mode=args.mode,
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
                progress=progress,
                **limits,
            )
        except AnalyticalError as exc:
            raise CliError(str(exc), X.INVALID) from exc
        except ValueError as exc:
            raise CliError(str(exc), X.USAGE) from exc
    if args.format == "json":
        emit_json(result, None)
    elif args.format == "csv":
        sys.stdout.write(_summary_csv(result))
    else:
        print(summary_text(result))
    if args.format != "text" and result.get("warnings"):
        for warning in result["warnings"]:
            eprint(f"warning: {warning}")
    return X.OK
