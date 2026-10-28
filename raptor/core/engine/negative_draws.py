"""The negative-time warning (`DrawNumber`).

Real clamps a negative failure or repair time to 0.000001 and, unless the model's "Ignore negative
value warning" box is ticked (`negShutUp`, saved as `SYSTEM_DATA` `ShowNegErrMsg` -- 1 means *ignore*),
shows `NegExpBox` at each draw until the user ticks "Don't show this message again". This port counts
the clamped draws per run and reports them after the batch.
"""
from __future__ import annotations

from collections.abc import Sequence

#: The dialog's own text (Raptor 7, screenshot 2026-10-10).
DIALOG_TITLE = "WARNING:  Negative Repair or Failure Times"
DIALOG_BODY = ("You are using a distribution with parameters such that a negative time was drawn.\n\n"
               "All negative times are rounded up to 0.000001.  This may negate the validity of your "
               "simulation.")
DIALOG_DONT_SHOW = "Don't show this message again."


def ignoring(rbd) -> bool:
    """The model's `negShutUp`: `ShowNegErrMsg` is 1 in `SYSTEM_DATA`."""
    try:
        return str(rbd.section("SYSTEM_DATA").rows[0].get("ShowNegErrMsg", "0")).strip() in ("1", "1.000000")
    except (KeyError, IndexError):
        return False


def note(per_run: Sequence[int]) -> str | None:
    """One line for the results, or None when nothing was clamped."""
    total = sum(per_run)
    if total <= 0:
        return None
    runs = sum(1 for n in per_run if n > 0)
    return (f"{total} negative time(s) were drawn in {runs} of {len(per_run)} run(s) and rounded "
            f"up to 0.000001. This may negate the validity of the simulation.")
