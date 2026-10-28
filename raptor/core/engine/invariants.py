"""Runtime invariants of a simulation, checked after every scheduler step.

Used by the test suite through `run_simulation`'s per-step `on_event` hook
(`InvariantChecker` is a drop-in observer). Each check is something the
engine must hold for any model, whether or not it matches Raptor 7:

* simulated time never goes backwards, and no pending calendar entry lies
  in the past;
* the system's count of currently failed components never goes negative;
* every block and event status is a member of its enum;
* a block that reached `Done` stays `Done` for the rest of its run.

A violation raises `EngineInvariantError` at the step that broke it.
"""
from __future__ import annotations

from typing import TYPE_CHECKING, Any

from raptor.core.engine.errors import EngineInvariantError
from raptor.core.model import ActiveStatus, BlockStatus, EventStatus

if TYPE_CHECKING:
    from raptor.core.engine.system_status import SystemState
    from raptor.core.model import RBDBlock
    from raptor.core.scheduler import Scheduler


class InvariantChecker:
    """Per-step invariant checks, with the state needed across steps.

    Pass an instance as `run_simulation(..., on_event=checker)`. A new run
    is recognised by its new `Scheduler` object.
    """

    def __init__(self) -> None:
        self.steps = 0
        #: The current run's scheduler, held (not its id(): a collected
        #: scheduler's id can be reused by the next run's).
        self._scheduler: Scheduler | None = None
        self._last_time = 0.0
        self._done_blocks: set[int] = set()

    def __call__(self, scheduler: Scheduler, system_state: SystemState,
                 blocks: list[RBDBlock], capacity: Any = None, model: Any = None) -> None:
        self.steps += 1
        if scheduler is not self._scheduler:  # a new run
            self._scheduler = scheduler
            self._last_time = 0.0
            self._done_blocks.clear()
        now = scheduler.time
        self._check_clock(scheduler, now)
        if system_state.num_currently_failed < 0:
            self._fail(f"num_currently_failed = {system_state.num_currently_failed} at t={now}")
        for block in blocks:
            self._check_block(block, now)
        if model is not None:
            for event in model.events_by_id.values():
                if not isinstance(event.op_status, EventStatus):
                    self._fail(f"event {event.name!r} op_status {event.op_status!r} is not an EventStatus")

    def _check_clock(self, scheduler: Scheduler, now: float) -> None:
        if now < self._last_time:
            self._fail(f"time went backwards: {self._last_time} -> {now}")
        self._last_time = now
        head = scheduler.next_time()
        if head is not None and head < now:
            self._fail(f"calendar head {head} is earlier than the current time {now}")

    def _check_block(self, block: RBDBlock, now: float) -> None:
        if not isinstance(block.op_status, BlockStatus):
            self._fail(f"block {block.name!r} op_status {block.op_status!r} is not a BlockStatus")
        if not isinstance(block.active_status, ActiveStatus):
            self._fail(f"block {block.name!r} active_status {block.active_status!r} is not an ActiveStatus")
        if block.op_status is BlockStatus.DONE:
            self._done_blocks.add(block.id)
        elif block.id in self._done_blocks:
            self._fail(f"block {block.name!r} left Done for {block.op_status.value} at t={now}")

    def _fail(self, message: str) -> None:
        raise EngineInvariantError(f"invariant violated (step {self.steps}): {message}")
