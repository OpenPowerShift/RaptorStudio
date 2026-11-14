"""Applying a finding's fix. Only ever called because a user asked."""
from __future__ import annotations

from typing import Any

from raptor.api.advisor.finding import Fix


class FixNotApplicable(ValueError):
    """The fix is a command for the application, not a change to fields."""


def apply(workspace: Any, model_id: str, fix: Fix) -> list[dict]:
    """Apply `fix`'s field changes to the model and return what changed."""
    if not fix.assignments:
        raise FixNotApplicable(
            f"'{fix.label}' opens a tool in the application; it has no field changes to apply")
    from raptor.api.paths import apply_assignments
    # one save, so one undo step, and all or nothing: a fix of two fields
    # applied one save each was undone halfway, leaving a model that failed
    # validation
    with workspace.batch(model_id):
        return apply_assignments(workspace, model_id, list(fix.assignments))
