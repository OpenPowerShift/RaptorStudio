"""What changed since the run before: the headline numbers, side by side.

After an edit and a re-run, the Overview cards say what each number was
before, so the effect of the change can be read off without remembering
it. Whether the difference is bigger than the run-to-run spread is said
too: with 100 runs the availability moves in the fourth decimal place
from the random draws alone, and a new user otherwise reads that noise as
the effect of the edit.

Shown only on the Overview cards (not every table) and switched off in
Preferences (`SETTING`).
"""
from __future__ import annotations

import math
from collections.abc import Callable, Iterable
from dataclasses import dataclass

#: Preferences key: show the change from the run before on the cards.
SETTING = "results/show_change"

#: The cards compared, and which way is better (+1 higher, -1 lower).
DIRECTION: dict[str, int] = {
    "Availability": +1,
    "Dependability": +1,
    "Reliability": +1,
    "MTBDE": +1,
    "MDT": -1,
    "System_Failures": -1,
}

#: Two-sided 95 % normal quantile: a difference inside this many standard
#: errors of the two means is not told apart from the random draws.
Z95 = 1.96


@dataclass(frozen=True)
class Figure:
    """One headline quantity over a batch of runs."""
    mean: float
    sem: float           # standard error of the mean
    runs: int


@dataclass(frozen=True)
class Change:
    """What a card says about the run before."""
    text: str            # "was 0.9121 · up 0.0210"
    verdict: str         # "better" | "worse" | "same" | "unknown"
    tooltip: str


def figure(values: Iterable[float | None]) -> Figure | None:
    """Mean and standard error of the per-run values that exist."""
    vals = [float(v) for v in values if v is not None]
    if not vals:
        return None
    n = len(vals)
    mean = sum(vals) / n
    if n < 2:
        return Figure(mean, 0.0, n)
    var = sum((v - mean) ** 2 for v in vals) / (n - 1)
    return Figure(mean, math.sqrt(var / n), n)


def short_probability(value: float) -> str:
    """A difference in a probability, to four significant figures."""
    return f"{value:.4g}"


def compare(key: str, before: Figure | None, now: Figure | None,
            fmt: Callable[[float], str],
            diff_fmt: Callable[[float], str] | None = None) -> Change | None:
    """The change line for card `key`, or None when there is nothing to
    compare (no run before, or this run has no value). `fmt` writes the
    old value as the card writes the new one; `diff_fmt` the difference
    (default `fmt`), which needs fewer digits than a nine-place value."""
    dfmt = diff_fmt or fmt
    if before is None or now is None or key not in DIRECTION:
        return None
    was = f"was {fmt(before.mean)}"
    diff = now.mean - before.mean
    if before.runs < 2 or now.runs < 2:
        return Change(was, "unknown",
                      "One run cannot say whether a difference is more than "
                      "chance. Run more trials to tell.")
    noise = Z95 * math.hypot(before.sem, now.sem)
    if diff == 0 or abs(diff) <= noise:
        return Change(
            f"{was} · no clear change", "same",
            f"The difference is within the run-to-run spread (± "
            f"{dfmt(noise)} at 95 %), so it could be chance alone. More "
            f"trials narrow the spread.")
    direction = "up" if diff > 0 else "down"
    better = (diff > 0) == (DIRECTION[key] > 0)
    return Change(
        f"{was} · {direction} {dfmt(abs(diff))}",
        "better" if better else "worse",
        f"Bigger than the run-to-run spread (± {dfmt(noise)} at 95 %): "
        f"the change between the runs, not chance.")
