"""Spreadsheet and CSV export of an analytical result.

The simulation's table export does not cover the analytical solver, so this
writes the same result the window shows: a Summary sheet, one row per
element (with the importance measures), the curve, the equation and the
assumptions. Numbers are real numeric cells.
"""
from __future__ import annotations

from pathlib import Path
from typing import Any

from raptor.api.analytical_text import BDD_NOTE
from raptor.core.fileio.xlsx_writer import Sheet, write_xlsx


def _summary_rows(result: dict[str, Any]) -> list[list[Any]]:
    rows: list[list[Any]] = [["Model", result["model"]],
                             ["Mode", result["mode"]]]
    unit = result.get("units") or ""
    if result["mode"] == "reliability":
        rows += [["Mission time", result.get("time")],
                 ["Time units", unit],
                 ["Reliability", result.get("reliability")],
                 ["Failure density", result.get("pdf")],
                 ["Hazard rate", result.get("hazard")],
                 ["MTTF", result.get("mttf")]]
        rows += [[f"B{key} life ({key}% of systems fail by this time)", value]
                 for key, value in (result.get("bx") or {}).items()]
        conditional = result.get("conditional")
        if conditional:
            rows.append([f"Conditional reliability, next {conditional['tau']:g} "
                         f"from {conditional['age']:g}",
                         conditional["reliability"]])
    else:
        rows += [["Availability", result.get("availability")],
                 ["Unavailability", result.get("unavailability")]]
    scope = result.get("scope") or {}
    if scope.get("phase") is not None:
        rows.append(["Phase analysed",
                     f"{scope['phase']} {scope.get('phase_name') or ''}".strip()])
    if scope.get("ignore"):
        rows.append(["Ignored", ", ".join(scope["ignore"])])
    if scope.get("standby"):
        rows.append(["Standby treated as", scope["standby"]])
    rows.append(["Solver size (BDD nodes)", result.get("bdd_nodes")])
    return rows


def _element_sheet(result: dict[str, Any]) -> Sheet:
    availability = result["mode"] == "availability"
    importance = {(row["kind"], row["id"]): row
                  for row in result.get("importance") or []}
    headers = ["Element", "Kind",
               "Availability" if availability else "Reliability", "MTTF"]
    if availability:
        headers.append("MTTR")
    headers.append("Treatment")
    keys = ("birnbaum", "raw", "rrw", "vesely_fussell", "criticality")
    if importance:
        headers += ["Birnbaum", "RAW", "RRW", "Fussell-Vesely", "Criticality"]
    components = list(result.get("components") or [])
    if importance:
        order = {key: i for i, key in enumerate(importance)}
        components.sort(key=lambda c: order.get((c["kind"], c["id"]),
                                                len(order)))
    rows = []
    for comp in components:
        row = [comp["name"], comp["kind"],
               comp.get("availability" if availability else "reliability"),
               comp.get("mttf")]
        if availability:
            row.append(comp.get("mttr"))
        row.append(comp.get("state") or "")
        if importance:
            measure = importance.get((comp["kind"], comp["id"])) or {}
            row += [measure.get(key) for key in keys]
        rows.append(row)
    return Sheet(name="Elements", headers=headers, rows=rows)


def analytical_sheets(result: dict[str, Any]) -> list[Sheet]:
    """The workbook's sheets for one analytical result."""
    sheets = [Sheet(
        name="Summary", headers=["Item", "Value"],
        rows=_summary_rows(result), notes=[BDD_NOTE])]
    sheets.append(_element_sheet(result))
    if result.get("times"):
        sheets.append(Sheet(
            name="Curve", headers=["Time", "Reliability"],
            rows=[[p["time"], p["value"]] for p in result["times"]]))
    if result.get("equation"):
        sheets.append(Sheet(name="Equation", headers=["Equation"],
                            rows=[[result["equation"]]]))
    notes = ([["Warning", w] for w in result.get("warnings") or []]
             + [["Assumption", a] for a in result.get("assumptions") or []])
    sheets.append(Sheet(name="Assumptions", headers=["Kind", "Text"],
                        rows=notes))
    return sheets


def export_xlsx(result: dict[str, Any], path: str | Path) -> Path:
    """Write ``result`` to ``path`` as an .xlsx workbook."""
    path = Path(path)
    write_xlsx(path, analytical_sheets(result))
    return path
