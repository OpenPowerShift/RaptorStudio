"""Spreadsheet and CSV export of an analytical result.

The simulation's table export does not cover the analytical solver, so this
writes the same result the window shows: a Summary sheet, one row per
element (with the importance measures), the curve, the equation and the
assumptions, as an Excel (``.xlsx``) or OpenDocument (``.ods``) workbook.
Numbers are real numeric cells; layout, colour and print set-up are
`raptor.core.fileio.workbook`'s, the same as every other spreadsheet export.
"""
from __future__ import annotations

from collections.abc import Sequence
from dataclasses import replace
from pathlib import Path
from typing import Any

from raptor.api.analytical_text import BDD_NOTE
from raptor.api.glossary import TERMS
from raptor.core.fileio.workbook import (
    BAR, Sheet, WorkbookInfo, write_workbook,
)

#: Summary rows whose value is a probability or a time ("B10 life ...").
_PROB_ITEMS = ("Reliability", "Availability", "Unavailability",
               "Conditional reliability")


def _summary_kind(item: str) -> str:
    if item.startswith(_PROB_ITEMS):
        return "prob"
    if item in ("Failure density", "Hazard rate"):
        return "rate"
    if item.startswith("B") and " life " in item:
        return "time"
    return "time" if item in ("Mission time", "MTTF") else ""


def _summary_rows(result: dict[str, Any]) -> list[list[Any]]:
    rows: list[list[Any]] = [["Model", result["model"]],
                             ["Mode", result["mode"]]]
    if result.get("hierarchy"):
        rows.append(["Hierarchy", result["hierarchy"]])
        rows.append(["Scope", "this hierarchy on its own: its In node is "
                              "treated as Start and its Out node as End, "
                              "so its input is always available"])
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
    if scope.get("dependencies"):
        rows.append(["Dependencies treated as", scope["dependencies"]])
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
    formats = ["", "", "prob", "time"] + (["time"] if availability else [])
    formats.append("")                                      # Treatment
    if importance:
        # Birnbaum, Fussell-Vesely and criticality are fractions; RAW and
        # RRW are ratios to the system as it is.
        formats += ["prob", "ratio", "ratio", "prob", "prob"]
    first_measure = headers.index("Birnbaum") if importance else None
    bars = ({first_measure + k: BAR for k in (0, 3, 4)}
            if first_measure is not None else {})
    return Sheet(
        name="Elements", headers=headers, rows=rows, formats=formats,
        text_cols={0, 1, headers.index("Treatment")}, title="Elements",
        description="Every element: its own " + headers[2].lower()
                    + (", MTTF and MTTR" if availability else " and MTTF")
                    + (", and its importance" if importance else ""),
        # Ranked by importance when there is one; else weakest first.
        sort=None if importance else (2, False), bars=bars,
        scales={2: True},              # the weakest element stands out
        notes=(["Ranked by importance, most important first."]
               if importance else []))


def analytical_sheets(result: dict[str, Any]) -> list[Sheet]:
    """The workbook's sheets for one analytical result."""
    summary = _summary_rows(result)
    sheets = [Sheet(
        name="Summary", headers=["Item", "Value"], rows=summary,
        notes=[BDD_NOTE], row_formats=[_summary_kind(str(r[0]))
                                       for r in summary],
        description=f"The system's {result['mode']} and how it was solved")]
    sheets.append(_element_sheet(result))
    if result.get("times"):
        sheets.append(Sheet(
            name="Curve", headers=["Time", "Reliability"],
            rows=[[p["time"], p["value"]] for p in result["times"]],
            formats=["time", "prob"], highlight=False,
            description="System reliability against time, for a chart"))
    if result.get("equation"):
        sheets.append(Sheet(name="Equation", headers=["Equation"],
                            rows=[[result["equation"]]], text_cols={0},
                            description="The structure function solved"))
    notes = ([["Warning", w] for w in result.get("warnings") or []]
             + [["Assumption", a] for a in result.get("assumptions") or []])
    sheets.append(Sheet(name="Assumptions", headers=["Kind", "Text"],
                        rows=notes, text_cols={0, 1},
                        description="Warnings and the assumptions the "
                                    "solver made"))
    return sheets


def _phase(result: dict[str, Any]) -> Any:
    return (result.get("scope") or {}).get("phase")


def _labels(results: Sequence[dict[str, Any]]) -> list[str]:
    """Each result's sheet prefix: its mode when the modes differ, its
    phase when the phases differ ("Phase 2", "Availability phase 2"), and
    a number if two would still share a name (several treatments)."""
    modes = {r["mode"] for r in results}
    phases = {_phase(r) for r in results}
    labels = []
    for r in results:
        parts = []
        if len(modes) > 1:
            parts.append(str(r["mode"]).capitalize())
        if len(phases) > 1:
            parts.append(f"phase {_phase(r)}" if parts else f"Phase {_phase(r)}")
        labels.append(" ".join(parts) or str(r["mode"]).capitalize())
    if len(set(labels)) != len(labels):
        labels = [f"{i} {label}" for i, label in enumerate(labels, 1)]
    return labels


def export_workbook(results: dict[str, Any] | Sequence[dict[str, Any]],
                    path: str | Path) -> Path:
    """Write one result, or several (reliability and availability, say), to
    ``path`` as one workbook: ``.xlsx`` or ``.ods`` by its extension
    (``.xlsx`` when it has neither). Several results get a set of sheets
    each, named after the mode: "Reliability Summary", "Availability
    Elements", ..."""
    path = Path(path)
    if path.suffix.lower() not in (".xlsx", ".ods"):
        path = path.with_name(path.name + ".xlsx")
    batch = [results] if isinstance(results, dict) else list(results)
    if not batch:
        raise ValueError("no analytical results to export")
    if len(batch) == 1:
        sheets = analytical_sheets(batch[0])
    else:
        sheets = [replace(sheet, name=f"{label} {sheet.name}",
                          title=f"{label} {sheet.title or sheet.name}")
                  for label, result in zip(_labels(batch), batch)
                  for sheet in analytical_sheets(result)]
    modes = " and ".join(dict.fromkeys(str(r["mode"]) for r in batch))
    model = str(batch[0].get("model") or "Model")
    info = WorkbookInfo(title=f"{model} - analytical {modes}",
                        subtitle=[f"Model: {model}"], source=model,
                        glossary=TERMS)
    write_workbook(path, sheets, info)
    return path


#: The name it had when the only format was Excel.
export_xlsx = export_workbook


def export_file_name(model: str, results: dict[str, Any]
                     | Sequence[dict[str, Any]], ext: str = "xlsx") -> str:
    """The workbook's default name: the model, the hierarchy if one was
    solved on its own, and what is in it, e.g.
    ``Plant_analytical_reliability.xlsx``,
    ``Plant_h12_analytical_reliability_availability.xlsx``,
    ``Plant_analytical_reliability_phase2.xlsx``,
    ``Plant_analytical_reliability_all_phases.xlsx``."""
    from raptor.api.table_export import safe_stem
    batch = [results] if isinstance(results, dict) else list(results)
    stem = safe_stem(model)
    hierarchies = {r.get("hierarchy") for r in batch}
    if len(hierarchies) == 1 and None not in hierarchies:
        stem += "_h" + safe_stem(str(next(iter(hierarchies))))
    modes = list(dict.fromkeys(str(r["mode"]) for r in batch))
    phases = {_phase(r) for r in batch}
    kind = "_".join(sorted(modes, key=("reliability", "availability").index))
    if len(phases) > 1:
        kind += "_all_phases"
    elif len(modes) * len(phases) != len(batch):
        kind = f"{len(batch)}_results"
    elif None not in phases:
        kind += f"_phase{next(iter(phases))}"
    return f"{stem}_analytical_{kind}.{ext.lstrip('.')}"
