"""Engine exceptions and invariant helpers.

These replace `assert` for checks the engine relies on at runtime: Python
strips `assert` statements under `-O`, so a check written that way silently
disappears from an optimised build.
"""
from __future__ import annotations

from typing import TYPE_CHECKING, TypeVar

if TYPE_CHECKING:
    from raptor.core.scheduler import Wake

T = TypeVar("T")


class EngineInvariantError(RuntimeError):
    """An internal engine invariant does not hold (a bug, not a user error)."""


class UnrunnableModel(NotImplementedError):
    """The model has a shape this simulator refuses to run (`model_checks`):
    a dependency cycle, or one Raptor 7 itself crashes on. A
    `NotImplementedError` so every caller that already treats those as a
    documented refusal still does; `problems` lists each reason."""

    def __init__(self, problems) -> None:
        self.problems = list(problems)
        super().__init__("; ".join(str(problem) for problem in self.problems))


class StreamBudgetExceeded(RuntimeError):
    """A chunk of a multi-worker batch drew more from one random stream than
    its segment holds, so it ran into the next chunk's draws and the runs are
    no longer independent. Rerun with fewer runs or sequentially."""


class UnexpectedWake(EngineInvariantError):
    """A suspended process was resumed with a value its wait cannot handle."""


def require[T](value: T | None, what: str) -> T:
    """`value`, which the caller's configuration guarantees is present."""
    if value is None:
        raise EngineInvariantError(f"{what} is required here but was not provided")
    return value


def expect_wake(resume: Wake | None, *reasons: str) -> Wake:
    """`resume` as a `Wake` whose reason is one of `reasons` (any reason when
    none are given); anything else is an `UnexpectedWake`."""
    from raptor.core.scheduler import Wake

    if not isinstance(resume, Wake) or (reasons and resume.reason not in reasons):
        raise UnexpectedWake(f"expected a wake with reason in {reasons!r}, got {resume!r}")
    return resume


class SkipRun(Exception):
    """GUI Jump (manual B-11): abandon the current run (no closing passes,
    results discarded) but continue with the next replication -- unlike
    `RunAborted`, which ends the whole batch."""
    pass


class RunAborted(Exception):
    """GUI Stop pressed mid-run: unwind without closing passes. This run's
    results are discarded; completed runs and sinks are kept."""
    pass


class StopRun(Exception):
    """Port of Modsim's `StopSimulation`. Raised by the
    FailSim `EndSimulation` process to unwind `scheduler.run_until`
    immediately; every other scheduled process is abandoned."""
