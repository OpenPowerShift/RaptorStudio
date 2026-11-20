"""Run a model several times with different settings, on private copies.

The shared core of `raptor sweep` and `raptor compare` (and anything that
wants "the same model, different parameters"). Each variant is a deep copy
registered in the workspace, with its `PATH=VALUE` assignments applied
through :mod:`raptor.api.paths`, so the source model is never touched.
"""
from __future__ import annotations

import copy
import itertools
import threading
import time
from collections.abc import Callable
from typing import Any

from raptor.api import paths
from raptor.api.results import metrics_to_dict
from raptor.api.run import run as api_run
from raptor.api.workspace import Workspace


def expand_vary(spec: str) -> tuple[str, list[str]]:
    """`PATH=START:STOP:STEP` or `PATH=v1,v2,...` -> (PATH, [value text]).

    A range includes STOP (when STEP lands on it); the values are written
    with repr so no digits are lost.
    """
    path, value = paths.split_assignment(spec)
    if ":" in value and "," not in value:
        parts = value.split(":")
        if len(parts) != 3:
            raise paths.PathError(
                f"{spec!r}: a range is START:STOP:STEP")
        try:
            start, stop, step = (float(p) for p in parts)
        except ValueError:
            raise paths.PathError(f"{spec!r}: range values must be numbers") \
                from None
        if step == 0 or (stop - start) * step < 0:
            raise paths.PathError(f"{spec!r}: STEP must move START toward STOP")
        n = round((stop - start) / step) + 1
        vals = [start + i * step for i in range(max(n, 0))
                if (start + i * step - stop) * step <= 1e-9 * abs(step)]
        return path, [_trim(v) for v in vals]
    if "," in value:
        return path, [v.strip() for v in value.split(",") if v.strip()]
    return path, [value]


def _trim(v: float) -> str:
    v = round(float(v), 12)
    return str(int(v)) if v.is_integer() else repr(v)


def grid(varies: list[tuple[str, list[str]]]) -> list[list[str]]:
    """The cartesian product as lists of `PATH=VALUE` assignments."""
    axes = [[f"{p}={v}" for v in vals] for p, vals in varies]
    return [list(combo) for combo in itertools.product(*axes)]


class Deadline(threading.Event):
    """A stop signal that is also set whenever `parent` is, and, given
    `seconds`, once that long has passed. Runs poll `is_set()` between
    replications and during set-up, so passing one as `abort` holds a whole
    study (many runs) to one time limit, and `expired()` then tells the time
    limit from a cancel. A run's own timer used to set the caller's event,
    so a study could not tell the two apart, and stopped without saying."""

    def __init__(self, parent: Any = None, seconds: float | None = None
                 ) -> None:
        super().__init__()
        self._parent = parent
        self._end = None if seconds is None else time.monotonic() + seconds

    def expired(self) -> bool:
        return self._end is not None and time.monotonic() >= self._end

    def cancelled(self) -> bool:
        """Stopped by the caller, not by a time limit."""
        parent = self._parent
        if parent is None or not parent.is_set():
            return False
        return not (isinstance(parent, Deadline) and parent.expired()
                    and not parent.cancelled())

    def is_set(self) -> bool:
        return (super().is_set() or self.expired()
                or (self._parent is not None and self._parent.is_set()))


def run_variants(
    ws: Workspace, model_id: str, variants: list[dict[str, Any]], *,
    base_assignments: list[str] | None = None,
    num_runs: int | None = None, workers: int = 1, timeout_s: float = 600.0,
    seed: int | None = None,
    progress: Callable[[int, int, str], None] | None = None,
    abort: Any = None, attribution: bool = False,
) -> list[dict[str, Any]]:
    """Run each variant `{"label": str, "assignments": [PATH=VALUE, ...]}`.

    Returns one dict per variant: label, assignments, run_id, num_runs,
    requested_runs and the metric summary. A variant whose assignments or
    run fail is reported with an `error` instead of stopping the batch.

    `timeout_s` is for the whole batch, not for each variant: each run gets
    what is left of it. A variant the deadline cut short is marked
    `truncated`; once it has passed, or once `abort` (a `threading.Event`)
    is set, the variants not yet started come back with `skipped` ("time"
    or "cancelled") and an `error` saying so. `cut_short(results)` words it.
    """
    source = ws.get(model_id).rbd
    deadline = time.monotonic() + timeout_s
    out: list[dict[str, Any]] = []
    stopped: str | None = None
    for i, var in enumerate(variants):
        label = var.get("label", f"variant {i + 1}")
        entry: dict[str, Any] = {"label": label,
                                 "assignments": list(var["assignments"])}
        if stopped is None and abort is not None and abort.is_set():
            stopped = ("time" if isinstance(abort, Deadline)
                       and not abort.cancelled() else "cancelled")
        remaining = deadline - time.monotonic()
        if stopped is None and remaining <= 0:
            stopped = "time"
        if stopped is not None:
            entry["skipped"] = stopped
            entry["error"] = ("the study's time limit (timeout_s="
                              f"{timeout_s:g} s) ran out before this ran"
                              if stopped == "time" else
                              "the study was cancelled before this ran")
            out.append(entry)
            continue
        if progress:
            progress(i, len(variants), label)
        stopped = _run_one(ws, source, i, entry, base_assignments,
                           num_runs=num_runs, workers=workers,
                           timeout_s=remaining, seed=seed, abort=abort,
                           attribution=attribution)
        out.append(entry)
    if progress:
        progress(len(variants), len(variants), "")
    return out


def _run_one(ws: Workspace, source: Any, i: int, entry: dict[str, Any],
             base_assignments: list[str] | None, *, num_runs: int | None,
             workers: int, timeout_s: float, seed: int | None, abort: Any,
             attribution: bool = False
             ) -> str | None:
    """Run one variant into `entry`; return "time" or "cancelled" when the
    study must stop after it."""
    from raptor.api.run import RunTimeoutError, model_num_runs
    vid = None
    own = Deadline(abort)
    stopped: str | None = None
    try:
        clone = copy.deepcopy(source)
        info = ws.register_model(clone, name=f"variant{i + 1}",
                                 source="batch")
        vid = info["model_id"]
        paths.apply_assignments(
            ws, vid, (base_assignments or []) + list(entry["assignments"]))
        requested = num_runs or model_num_runs(ws.get(vid).rbd)
        res = api_run(ws, vid, num_runs=num_runs, workers=workers,
                      timeout_s=timeout_s, confirm_long_run=True,
                      seed=seed, abort=own, attribution=attribution)
        entry.update(run_id=res.run_id, num_runs=res.num_runs,
                     requested_runs=requested,
                     summary=metrics_to_dict(res.metrics),
                     analyses=res.analyses,
                     warnings=res.warnings)
        if requested and res.num_runs < requested:
            entry["truncated"] = True
            stopped = "cancelled" if own.cancelled() else "time"
    except RunTimeoutError as exc:
        entry["error"] = plain_error(exc)
        stopped = "time"
    except Exception as exc:                              # noqa: BLE001
        entry["error"] = plain_error(exc)
        if own.cancelled():
            stopped = "cancelled"
        elif own.is_set():
            stopped = "time"
    finally:
        # the copy has done its job once its run is on disk; kept, every
        # study left one model per variant in the workspace for good
        if vid is not None:
            try:
                ws.close(vid, delete=True)
            except Exception:                             # noqa: BLE001
                pass
    return stopped


def require_measured(short: str | None, measured: bool) -> None:
    """Raise when a study was cut short before it measured anything: a
    table of dashes read as a finished study that found nothing."""
    if short and not measured:
        from raptor.api.run import StudyTimeoutError
        raise StudyTimeoutError("Nothing was measured. " + short)


def plain_error(exc: BaseException) -> str:
    """An error as a user reads it: the message, without the Python class
    ("ElementNotFound: No element named ..."). An error the program did not
    expect keeps its class, which is what a bug report needs."""
    text = str(exc).strip()
    expected = (ValueError, LookupError, OSError, TimeoutError)
    if text and (isinstance(exc, expected)
                 or type(exc).__module__.startswith("raptor.")):
        return text
    return f"{type(exc).__name__}: {text}" if text else type(exc).__name__


def cut_short(results: list[dict[str, Any]], timeout_s: float | None = None
              ) -> str | None:
    """A sentence saying a study from :func:`run_variants` did not finish,
    or None when every variant ran all its trials."""
    skipped = [row for row in results if row.get("skipped")]
    short = [row for row in results if row.get("truncated")]
    if not skipped and not short:
        return None
    why = ("it was cancelled" if any(row.get("skipped") == "cancelled"
                                     for row in skipped) and not any(
               row.get("skipped") == "time" for row in skipped)
           else "its time limit"
           + (f" (timeout_s={timeout_s:.3g} s)" if timeout_s else "")
           + " ran out")
    parts = []
    for row in short:
        parts.append(f"{row['label']} ran {row.get('num_runs')} of "
                     f"{row.get('requested_runs')} trials")
    if skipped:
        parts.append(f"{len(skipped)} of {len(results)} variants did not run")
    return ("Not complete: " + why + "; " + ", ".join(parts)
            + ". Treat these numbers as partial: give a larger timeout_s, "
              "fewer trials or fewer inputs.")


def discard_runs(ws: Workspace, results: list[dict[str, Any]]) -> None:
    """Delete the run folders of `results` (from :func:`run_variants`) once
    their numbers have been read: a study's intermediate runs are not
    results anyone asked to keep, and would crowd out the ones they did."""
    import shutil

    from raptor.api.run import run_dir
    for row in results:
        run_id = row.get("run_id")
        if not run_id:
            continue
        try:
            shutil.rmtree(run_dir(ws, run_id), ignore_errors=True)
        except Exception:                                 # noqa: BLE001
            pass
