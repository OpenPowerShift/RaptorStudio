"""`raptor analytic`: the static RBD solver."""
from __future__ import annotations

import csv
import io
import sys
from typing import Any

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


def _fmt(value: float | None, digits: int = 9) -> str:
    if value is None:
        return "N/A"
    if value == 0.0:
        return "0"
    return f"{value:.{digits}g}"


def _summary_text(result: dict[str, Any]) -> str:  # noqa: C901, PLR0912
    lines = [
        f"Analytical analysis: {result['model']} ({result['mode']})",
        f"BDD nodes: {result['bdd_nodes']}",
        "",
    ]
    unit = result.get("units") or ""
    suffix = f" {unit}" if unit else ""
    if result["mode"] == "reliability":
        lines.extend([
            f"time:          {_fmt(result['time'])}{suffix}",
            f"reliability:   {_fmt(result.get('reliability'))}",
            f"pdf:           {_fmt(result.get('pdf'))}",
            f"hazard:        {_fmt(result.get('hazard'))}",
            f"MTTF:          {_fmt(result.get('mttf'))}{suffix}",
        ])
        bx = result.get("bx") or {}
        if bx:
            lines.append("BX life:       " + ", ".join(
                f"B{key}={_fmt(value)}{suffix}" for key, value in bx.items()))
    else:
        lines.extend([
            f"availability:  {_fmt(result.get('availability'))}",
            f"unavailability: {_fmt(result.get('unavailability'))}",
        ])
    if "conditional" in result:
        conditional = result["conditional"]
        lines.append(
            f"conditional:   R({_fmt(conditional['age'])} + "
            f"{_fmt(conditional['tau'])}) = "
            f"{_fmt(conditional['reliability'])}")
    if result.get("equation"):
        lines.extend(["", "Equation:", result["equation"]])
    if result.get("assumptions"):
        lines.extend(["", "Assumptions:"])
        lines.extend(f"  - {item}" for item in result["assumptions"])
    if result.get("warnings"):
        lines.extend(["", "Warnings:"])
        lines.extend(f"  - {warning}" for warning in result["warnings"])
    components = result.get("components") or []
    if result["mode"] == "availability" and components:
        lines.extend(["", "Components:"])
        header = ("name", "availability", "MTTF", "MTTR")
        rows = [[row["name"], _fmt(row.get("availability")),
                 _fmt(row.get("mttf")), _fmt(row.get("mttr"))]
                for row in components]
        widths = [max(len(header[i]), *(len(row[i]) for row in rows))
                  for i in range(len(header))]
        lines.append("  " + "  ".join(
            header[i].ljust(widths[i]) for i in range(len(header))))
        for row in rows:
            lines.append("  " + "  ".join(
                row[i].ljust(widths[i]) for i in range(len(header))))
    importance = result.get("importance") or []
    if importance:
        lines.extend(["", "Importance:"])
        header = ("name", "birnbaum", "raw", "rrw", "VF", "criticality")
        rows = [[row["name"], _fmt(row.get("birnbaum")), _fmt(row.get("raw")),
                 _fmt(row.get("rrw")), _fmt(row.get("vesely_fussell")),
                 _fmt(row.get("criticality"))] for row in importance]
        widths = [max(len(header[i]), *(len(row[i]) for row in rows))
                  for i in range(len(header))]
        lines.append("  " + "  ".join(
            header[i].ljust(widths[i]) for i in range(len(header))))
        for row in rows:
            lines.append("  " + "  ".join(
                row[i].ljust(widths[i]) for i in range(len(header))))
    if result.get("times"):
        lines.extend(["", "Curve:"])
        for point in result["times"]:
            lines.append(f"  t={_fmt(point['time'])}  {_fmt(point['value'])}")
    return "\n".join(lines)


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
    for row in result.get("components") or []:
        writer.writerow(["component", row["name"],
                         row.get("availability", row.get("reliability"))])
    for row in result.get("importance") or []:
        writer.writerow(["importance", f"{row['kind']}:{row['id']}:{row['name']}",
                         row.get("criticality")])
    if result.get("equation"):
        writer.writerow(["equation", "", result["equation"]])
    return out.getvalue()


def analytic_command(args: Any) -> int:
    from raptor.api.analytical import analyse
    from raptor.core.analytical.errors import AnalyticalError
    with workspace_for() as ws:
        mid = open_model(ws, args.model)
        try:
            result = analyse(
                ws, mid,
                mode=args.mode,
                at=args.at,
                times=_floats(args.times),
                bx=_floats(args.bx),
                tau=args.tau,
                importance=args.importance,
                equation=args.equation,
                ignore_repairs=args.ignore_repairs,
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
        print(_summary_text(result))
    if args.format != "text" and result.get("warnings"):
        for warning in result["warnings"]:
            eprint(f"warning: {warning}")
    return X.OK
