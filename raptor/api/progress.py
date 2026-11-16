"""One way to report progress and to cancel, for everything that can be long.

Anything that may take more than about half a second accepts
``progress: ProgressFn | None`` and ``abort: threading.Event | None``. The
command line prints it, the MCP server relays it and the application shows
it; none of them invents its own.
"""
from __future__ import annotations

import threading
from collections.abc import Callable
from dataclasses import dataclass


@dataclass(frozen=True)
class Progress:
    stage: str                    # stable id: "setup", "simulate", "attribute", "variant", ...
    label: str                    # words for a person
    done: int
    total: int | None = None      # None = unknown: show a busy indicator, never a made-up share
    detail: str = ""
    eta_seconds: float | None = None

    @property
    def fraction(self) -> float | None:
        if not self.total:
            return None
        return max(0.0, min(1.0, self.done / self.total))

    def text(self) -> str:
        """`Working out what caused the downtime: 120 of 1,000`."""
        count = (f"{self.done:,} of {self.total:,}" if self.total
                 else f"{self.done:,}" if self.done else "")
        parts = [self.label + (f": {count}" if count else "")]
        if self.detail:
            parts.append(self.detail)
        return " · ".join(parts)


ProgressFn = Callable[[Progress], None]


class Cancelled(Exception):
    """The caller asked for the work to stop."""


def check(abort: threading.Event | None) -> None:
    """Raise `Cancelled` if the caller has asked to stop."""
    if abort is not None and abort.is_set():
        raise Cancelled()


def report(progress: ProgressFn | None, stage: str, label: str, done: int,
           total: int | None = None, detail: str = "") -> None:
    if progress is not None:
        progress(Progress(stage, label, done, total, detail))
