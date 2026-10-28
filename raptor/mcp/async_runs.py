"""Background simulation jobs for the MCP server.

`simulate` blocks for the whole batch: a 100-replication, 20-year batch runs
for tens of minutes, longer than many clients keep one call open, and a
client that gives up loses every replication that had already finished.

`start_run` registers a job and returns its id immediately; `run_status`
reports progress and, once finished, the run id; `cancel_run` sets the
abort flag the engine checks between replications.  Jobs live in this
process only -- they are not persisted, so a restart forgets them, while the
*run* they produced stays in the workspace like any other.

The registry is small and bounded: finished jobs are evicted oldest-first so
a client that polls rarely cannot grow it without limit.
"""
from __future__ import annotations

import threading
import time
import uuid
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

#: Finished jobs kept for `run_status` before the oldest is evicted.
MAX_JOBS = 32

_LOCK = threading.Lock()
_JOBS: dict[str, Job] = {}


@dataclass
class Job:
    """One background simulation."""

    job_id: str
    model_id: str
    total: int | None
    state: str = "queued"          # queued | running | done | failed | cancelled
    done: int = 0
    started: float = field(default_factory=time.time)
    finished: float | None = None
    run_id: str | None = None
    wall_seconds: float | None = None
    summary: dict[str, Any] | None = None
    error: str | None = None
    abort: threading.Event = field(default_factory=threading.Event)

    def snapshot(self) -> dict[str, Any]:
        elapsed = (self.finished or time.time()) - self.started
        percent = (100.0 * self.done / self.total
                   if self.total and self.total > 0 else None)
        return {
            "job_id": self.job_id,
            "model_id": self.model_id,
            "state": self.state,
            "completed_runs": self.done,
            "requested_runs": self.total,
            "percent": None if percent is None else round(percent, 1),
            "elapsed_s": round(elapsed, 1),
            "wall_seconds": None if self.wall_seconds is None
                            else round(self.wall_seconds, 1),
            "run_id": self.run_id,
            "summary": self.summary,
            "error": self.error,
        }


def _evict_locked() -> None:
    """Drop the oldest finished job once the registry is full."""
    if len(_JOBS) < MAX_JOBS:
        return
    finished = [(job.finished or 0.0, key) for key, job in _JOBS.items()
                if job.state in ("done", "failed", "cancelled")]
    for _, key in sorted(finished)[: max(1, len(_JOBS) - MAX_JOBS + 1)]:
        _JOBS.pop(key, None)


def start(
    work: Callable[[threading.Event, Callable[[int, int | None], None]], Any],
    *,
    model_id: str,
    total: int | None,
    label: str = "run",
) -> Job:
    """Register a job and run *work(abort, progress)* on a daemon thread."""
    from raptor.api.limits import MAX_BACKGROUND_JOBS
    from raptor.api.workspace import JobLimitExceeded
    job = Job(job_id=f"job_{uuid.uuid4().hex[:10]}", model_id=model_id,
              total=total)
    with _LOCK:
        busy = sum(1 for j in _JOBS.values()
                   if j.state in ("queued", "running"))
        if busy >= MAX_BACKGROUND_JOBS:
            raise JobLimitExceeded(
                f"{busy} background jobs are already queued or running (the "
                f"limit is {MAX_BACKGROUND_JOBS}); wait for one to finish or "
                "cancel it with cancel_run")
        _evict_locked()
        _JOBS[job.job_id] = job

    def progress(done: int, total_hint: int | None) -> None:
        job.done = int(done)
        if total_hint:
            job.total = int(total_hint)

    def runner() -> None:
        # Take one of the shared CPU slots (the same two that simulate, sweeps
        # and comparisons use), so background jobs cannot multiply the load.
        # A job waits here in the "queued" state.
        from raptor.mcp.jobs import _JOB_SLOTS
        try:
            with _JOB_SLOTS:
                if job.abort.is_set():
                    job.state = "cancelled"
                    return
                job.state = "running"
                result = work(job.abort, progress)
                job.run_id = getattr(result, "run_id", None)
                job.wall_seconds = getattr(result, "wall_seconds", None)
                job.done = int(getattr(result, "num_runs", job.done) or job.done)
                metrics = getattr(result, "to_dict", None)
                if callable(metrics):
                    try:
                        job.summary = {
                            name: value.get("mean")
                            for name, value in (metrics().get("metrics") or {}).items()
                            if isinstance(value, dict) and "mean" in value
                        }
                    except Exception:  # noqa: BLE001 - summary is best-effort
                        job.summary = None
                job.state = "cancelled" if job.abort.is_set() else "done"
        except BaseException as exc:  # noqa: BLE001 - reported, not raised
            job.state = "cancelled" if job.abort.is_set() else "failed"
            job.error = f"{type(exc).__name__}: {exc}"
        finally:
            job.finished = time.time()

    threading.Thread(target=runner, name=f"raptor-{job.job_id}",
                     daemon=True).start()
    return job


def get(job_id: str) -> Job | None:
    with _LOCK:
        return _JOBS.get(job_id)


def cancel(job_id: str) -> Job | None:
    job = get(job_id)
    if job is None:
        return None
    job.abort.set()
    return job


def active() -> list[Job]:
    with _LOCK:
        return [j for j in _JOBS.values()
                if j.state in ("queued", "running")]
