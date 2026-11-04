"""Exceptions for the analytical solver.

These are deliberately separate from the engine's `UnrunnableModel`: a model
can be perfectly runnable by simulation and still be outside the analytical
subset. Callers catch `NotApplicable` and show it as a refusal, not a crash.
"""
from __future__ import annotations


class AnalyticalError(Exception):
    """Base class for analytical-solver failures."""


class NotApplicable(AnalyticalError):
    """The model is outside the static solver's assumptions.

    The message names the first offending element and field, so the CLI and
    GUI can tell the user what to change (or that they should use simulation).
    """


class BddTooLarge(AnalyticalError):
    """The reduced ordered BDD exceeded the configured node cap.

    Raised rather than truncated: a silent approximation would be worse than
    a refusal for an "exact" solver. ``limit`` is the cap that was hit,
    ``seconds`` how long the build ran, and ``allowed`` the largest cap this
    machine allows (so a caller can say whether raising it is possible).
    """

    def __init__(self, message: str, *, limit: int = 0, seconds: float = 0.0,
                 allowed: int = 0) -> None:
        super().__init__(message)
        self.limit = limit
        self.seconds = seconds
        self.allowed = allowed


class UnsupportedDistribution(AnalyticalError):
    """A distribution has no analytical survival function here."""
