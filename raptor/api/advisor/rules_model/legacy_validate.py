"""The existing validator's findings, carried into the advisor unchanged.

`raptor.api.validate.issues` stays the owner of the structural and
parameter-range checks; this rule only wraps what it returns so one list
holds everything.
"""
from __future__ import annotations

import re
from collections.abc import Iterable

from raptor.api.advisor.context import ModelContext
from raptor.api.advisor.finding import Finding, Target
from raptor.api.advisor.registry import rule

_BLOCK = re.compile(r"^Block (\d+) '(.*)'$")


@rule("M-VAL-000", title="Model validation", category="structure",
      basis="exact", severity="error")
def validation(ctx: ModelContext) -> Iterable[Finding]:
    found = ctx.validation
    if found is None:
        from raptor.api.validate import issues
        found = issues(ctx.rbd)
    for issue in found:
        targets: tuple[Target, ...] = ()
        m = _BLOCK.match(issue.element or "")
        if m:
            targets = (Target("block", int(m.group(1)), m.group(2)),)
        is_error = issue.severity == "error"
        message = issue.message.split(" [RAPTOR Studio, ", 1)[0].rstrip()
        if issue.element and not message.startswith(issue.element):
            message = f"{issue.element}: {message}"
        yield Finding(
            rule="M-VAL-000", severity="error" if is_error else "warning",
            category="structure",
            title="The model cannot run" if is_error else "Validation warning",
            message=message,
            why="Found by the model validator, which checks structure and "
                "parameter ranges before a run.",
            targets=targets, basis="exact", impact=1.0 if is_error else 0.3,
            doc="advisor.html#M-VAL-000")
