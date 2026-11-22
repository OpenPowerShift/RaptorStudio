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


def _result(plan: Any, *, applied: bool,
            changes: list[Any] | None = None) -> dict[str, Any]:
    """One result shape for preview and apply, so the two cannot drift."""
    return {
        "source": plan.source,
        "sheet": plan.sheet,
        "summary": plan.summary(),
        "applied": applied,
        "changes": [_row(c) for c in (changes if changes is not None
                                      else plan.changes)],
        "problems": [_problem(p) for p in plan.problems],
        "blocked": plan.has_errors(),
    }


def plan_file(ws: Any, model_id: str, workbook: str | Path,
              sheet: str | None = None) -> dict[str, Any]:
    """What the workbook would change, without touching the model.

    `sheet` names the worksheet (the workbook's active sheet, the first
    when there is none, is read when omitted). A problem with
    `level == "error"` blocks the apply; warnings do not.
    """
    return _result(_plan(ws, model_id, workbook, sheet), applied=False)


def apply_file(ws: Any, model_id: str, workbook: str | Path,
               sheet: str | None = None, *, save: bool = True
               ) -> dict[str, Any]:
    """Apply the workbook's changes and save the model.

    Refuses a plan with an error (ValueError) so a mistyped row cannot
    half-edit a model. Returns the same shape as :func:`plan_file` with
    `applied` true and only the changes that were written. `save=False` is
    for a caller that owns the file (the CLI writes it itself); the plan is
    then applied in memory and nothing in the workspace is written.
    """
    from raptor.core.import_tables import apply_to_rbd
    plan = _plan(ws, model_id, workbook, sheet)
    if plan.has_errors():
        raise ValueError(
            "the import plan has errors; fix them before applying it: "
            + "; ".join(p.text() for p in plan.errors[:5]))
    if not plan.changes:
        return _result(plan, applied=False, changes=[])
    handle = ws.get(model_id)
    if save:
        with ws.batch(model_id):
            _before, after = apply_to_rbd(handle.rbd, plan)
    else:
        _before, after = apply_to_rbd(handle.rbd, plan)
    # A block planned but deleted meanwhile (a concurrent edit) is skipped
    # by `apply_to_rbd`; report only what the model now holds.
    written = [c for c in plan.changes if c.id in after]
    return _result(plan, applied=True, changes=written)
