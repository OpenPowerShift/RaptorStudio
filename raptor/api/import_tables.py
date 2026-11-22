"""Public API for spreadsheet data-table imports.

The workbook is read by `raptor.core.fileio.sheet_reader` (no spreadsheet
dependency, no Qt); the plan and the apply come from
`raptor.core.import_tables`, the same code the GUI dialog reviews. Every
row is validated before anything is written, and a plan with an error is
refused rather than partly applied.
"""
from __future__ import annotations

from pathlib import Path
from typing import Any


def _sheet(workbook: str | Path, sheet: str | None):
    from raptor.core.fileio.sheet_reader import read_sheet
    return read_sheet(workbook, sheet)


def _plan(ws: Any, model_id: str, workbook: str | Path,
          sheet: str | None):
    from raptor.core.import_tables import plan_import
    rbd = ws.get(model_id).rbd
    return plan_import(rbd, _sheet(workbook, sheet), source=str(workbook))


def _row(change: Any) -> dict[str, Any]:
    return {
        "row": change.row,
        "ref": change.ref,
        "id": change.id,
        "name": change.name,
        "field": change.field,
        "old": change.old_text(),
        "new": change.new_text(),
        "formula": change.formula,
    }


def _problem(problem: Any) -> dict[str, Any]:
    return {"row": problem.row, "level": problem.level,
            "message": problem.message, "ref": problem.ref}


def plan_file(ws: Any, model_id: str, workbook: str | Path,
              sheet: str | None = None) -> dict[str, Any]:
    """What the workbook would change, without touching the model.

    `sheet` names the worksheet (the active/first one when omitted). A
    problem with `level == "error"` blocks the apply; warnings do not.
    """
    plan = _plan(ws, model_id, workbook, sheet)
    return {
        "source": plan.source,
        "sheet": plan.sheet,
        "summary": plan.summary(),
        "applied": False,
        "changes": [_row(c) for c in plan.changes],
        "problems": [_problem(p) for p in plan.problems],
        "blocked": plan.has_errors(),
    }


def apply_file(ws: Any, model_id: str, workbook: str | Path,
               sheet: str | None = None) -> dict[str, Any]:
    """Apply the workbook's changes and save the model.

    Refuses a plan with an error (ValueError) so a mistyped row cannot
    half-edit a model. Returns the same shape as :func:`plan_file` with
    `applied` true and only the changes that were written.
    """
    from raptor.core.import_tables import apply_to_rbd
    plan = _plan(ws, model_id, workbook, sheet)
    if plan.has_errors():
        raise ValueError(
            "the import plan has errors; fix them before applying it: "
            + "; ".join(p.text() for p in plan.errors[:5]))
    if not plan.changes:
        return {"source": plan.source, "sheet": plan.sheet,
                "summary": plan.summary(), "applied": False,
                "changes": [], "problems": [_problem(p) for p in plan.problems],
                "blocked": False}
    apply_to_rbd(ws.get(model_id).rbd, plan)
    ws.save(model_id)
    return {
        "source": plan.source,
        "sheet": plan.sheet,
        "summary": plan.summary(),
        "applied": True,
        "changes": [_row(c) for c in plan.changes],
        "problems": [_problem(p) for p in plan.problems],
        "blocked": False,
    }
