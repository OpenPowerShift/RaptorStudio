"""How much longer a running batch has left, in words.

A batch can be thousands of replications on a large model, so the only
question a user has while waiting is "how long?".  After a couple of
runs the average time per completed replication is a good enough
estimate: runs vary, but the average absorbs the variation and cannot
oscillate the way a "last run" projection does.

Pure functions, no Qt: `format_eta` is the estimator, `RunEstimate`
carries the start time and the paused time so a Pause does not inflate
the remaining estimate.

Wording is deliberately approximate.  Under five minutes the estimate
is inside the noise of a per-run average, so no clock time is promised
(only "Approximately 4 minutes left"); at five minutes and up the
completion time is worth projecting, and "today"/"tomorrow" is spelled
out because a batch that runs overnight is a real case on a large model.
"""
from __future__ import annotations

import time
from datetime import datetime, timedelta

#: Runs needed before the average means anything.  One completed run
#: still includes the setup/compile time before the first result, which
#: over-states every remaining run.
MIN_RUNS = 2

#: Seconds of activity needed for the same reason.
MIN_SECONDS = 1.0

#: Below this the completion clock is not projected: a few minutes is
#: inside the noise of the per-run average.
NO_CLOCK_BELOW = 5 * 60


def _round_remaining(seconds: float) -> tuple[str, int]:
    """The display text and the number of seconds it stands for.

    The seconds returned are the ROUNDED value the text quotes, so the
    projected clock time advances in step with the words beside it.
    """
    if seconds < 90:
        n = max(1, round(seconds))
        return (f"{n} second{'s' if n != 1 else ''}", n)
    if seconds < 3600:
        n = max(1, round(seconds / 60))
        return (f"{n} minute{'s' if n != 1 else ''}", n * 60)
    hours = round(seconds / 3600, 1)
    # "1.5 hrs" reads better than "1.5 hours"; one hour stays singular.
    if hours >= 10:
        hours = float(round(hours))
    text = f"{hours:g} hr" + ("" if hours == 1 else "s")
    return (text, round(hours * 3600))


def _clock(when: datetime) -> str:
    """`12:30pm`, lower case and without a space, as the user wrote it."""
    return when.strftime("%I:%M%p").lstrip("0").lower()


def _day_suffix(when: datetime, now: datetime) -> str:
    """` today`, ` tomorrow`, or ` on 28 Oct` (with the year when it is
    not this one)."""
    days = (when.date() - now.date()).days
    if days == 0:
        return " today"
    if days == 1:
        return " tomorrow"
    if when.year == now.year:
        return f" on {when.day} {when:%b}"
    return f" on {when.day} {when:%b} {when.year}"


def format_eta(done: int, total: int, elapsed: float, *,
               now: datetime | None = None) -> str | None:
    """The status-bar sentence, or None while there is no useful data.

    `done`/`total` are completed and total replications, `elapsed` the
    ACTIVE seconds the batch has been running (paused time excluded).
    """
    if now is None:
        now = datetime.now()
    if total <= 0 or done <= 0 or done >= total:
        return None
    if done < MIN_RUNS or elapsed < MIN_SECONDS:
        return None
    remaining = (elapsed / done) * (total - done)
    if remaining <= 0:
        return None
    words, seconds = _round_remaining(remaining)
    text = f"Approximately {words} left"
    if seconds >= NO_CLOCK_BELOW:
        when = now + timedelta(seconds=seconds)
        text += (f", completion expected around {_clock(when)}"
                 f"{_day_suffix(when, now)}")
    return text


class RunEstimate:
    """Wall-clock bookkeeping for one running batch.

    `text` reads the clock itself (and `datetime.now()` for the
    projected completion), so the caller only has to pass the run
    counters it already has from the engine's progress callback.
    """

    def __init__(self, *, started: float | None = None) -> None:
        self._started = time.monotonic() if started is None else started
        self._paused_at: float | None = None
        self._paused_total = 0.0

    def pause(self) -> None:
        """A paused batch must not grow its remaining estimate."""
        if self._paused_at is None:
            self._paused_at = time.monotonic()

    def resume(self) -> None:
        if self._paused_at is not None:
            self._paused_total += time.monotonic() - self._paused_at
            self._paused_at = None

    def active_seconds(self) -> float:
        now = time.monotonic()
        return (now - self._started - self._paused_total
                - (now - self._paused_at if self._paused_at is not None
                   else 0.0))

    def text(self, done: int, total: int, *,
             now: datetime | None = None) -> str | None:
        return format_eta(done, total, self.active_seconds(), now=now)
