"""Run blocking engine work with progress and cancellation.

``simulate``, ``sweep_parameter`` and ``compare_models`` can take minutes.
Run as plain synchronous tools they are silent (clients time out at
60-120 s) and cannot be cancelled.  :func:`run_job` fixes both:

* the work runs in a worker thread that is handed an ``abort`` event and a
  cheap ``progress(done, total)`` callback;
* the async side sends an MCP progress notification about once a second
  (which also resets most clients' request timeouts); and
* if the client cancels the request (or the task is torn down) ``abort``
  is set so the engine stops between replications instead of burning CPU
  on a result nobody will read.

``ctx`` may be ``None`` (direct calls in tests): the work still runs, there
is just nobody to notify.
"""
from __future__ import annotations

import threading
from typing import Any, TypeVar
from collections.abc import Callable

import anyio
import anyio.to_thread

from raptor.api.limits import MAX_CONCURRENT_JOBS as _MAX_JOBS

T = TypeVar("T")

#: Seconds between progress notifications.
HEARTBEAT_S = 1.0

#: Long-running tool calls are CPU/memory heavy.  A bounded number may run
#: at once; an MCP client (especially over HTTP) can otherwise start many
#: simulations/sweeps in parallel and exhaust the machine.
_JOB_SLOTS = threading.BoundedSemaphore(max(1, int(_MAX_JOBS)))


async def run_job[T](
    ctx: Any,
    work: Callable[[threading.Event, Callable[[int, int | None], None]], T],
    *,
    label: str = "Working",
) -> T:
    """Run ``work(abort, progress)`` in a thread, reporting progress."""
    return await _run_job_locked(ctx, work, label=label)


async def _run_job_locked[T](  # noqa: C901
    ctx: Any,
    work: Callable[[threading.Event, Callable[[int, int | None], None]], T],
    *,
    label: str = "Working",
) -> T:
    abort = threading.Event()
    state: dict[str, Any] = {"done": 0, "total": None}

    def progress(done: int, total: int | None) -> None:
        state["done"] = done
        state["total"] = total

    box: dict[str, Any] = {}

    def work_in_slot() -> T:
        # Acquire/release inside the worker thread: if the client cancels,
        # the thread is abandoned but still finishes and releases its slot.
        with _JOB_SLOTS:
            return work(abort, progress)

    async def worker() -> None:
        try:
            box["result"] = await anyio.to_thread.run_sync(
                work_in_slot,
                abandon_on_cancel=True,
            )
        except BaseException as exc:  # noqa: BLE001 - re-raised below
            box["error"] = exc
            if not isinstance(exc, Exception):
                raise

    try:
        async with anyio.create_task_group() as tg:
            tg.start_soon(worker)
            waited = 0.0
            while "result" not in box and "error" not in box:
                await anyio.sleep(0.05)
                waited += 0.05
                if ctx is not None and waited >= HEARTBEAT_S:
                    waited = 0.0
                    done, total = state["done"], state["total"]
                    msg = (f"{label}: {done}/{total}" if total
                           else f"{label}…")
                    try:
                        await ctx.report_progress(done, total, msg)
                    except Exception:  # noqa: BLE001 - progress is best-effort
                        ctx = None
    except BaseException:
        abort.set()  # cancelled or failed: stop the engine promptly
        raise
    if "error" in box:
        raise box["error"]
    return box["result"]
