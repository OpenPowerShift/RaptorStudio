"""What an ordinary block's process needs from the rest of the run.

`BlockProcess` used to take 35 parameters, most of them the same for every
block in a run. They are grouped here:

- `BlockEnv`: the per-run environment every block shares, built once by the
  driver.
- `BlockPools`: the shared pools one block draws on, and the processes it
  can spawn to refill them.
- `CascadePort`: the calls a block's process makes back into the cascade
  (`orchestrator._Cascade`) when its maintenance completes.
- `StateObserver`: an opt-in instrument told about every wake and status
  change, for the state-coverage matrix.
  Install one with `observing(...)`; with none installed nothing is called.
"""
from __future__ import annotations

from collections.abc import Callable, Iterator
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass
from typing import Protocol

from raptor.core.engine.pool import PoolStats, ResourceQueue, SpawnOrderASpareFn, WakeBlockFn
from raptor.core.engine.system_status import SystemState
from raptor.core.engine.wait_sites import WaitSite
from raptor.core.model import BlockStatus, Link, RBDBlock, RBDEvent, RBDNode, SparePool
from raptor.core.rng import RandomSource
from raptor.core.scheduler import Scheduler, Wake


class CascadePort(Protocol):
    """The cascade as a block's process sees it: the three calls
    `DetermineNewState("Maintenance Completed")` makes after the block's own
    transition has run its cascade (`BlockProcess._recheck_after_repair`)."""

    def check_system_status(self, sim_time: float) -> None:
        """Drain the cold-standby change group, then run the system-status
        check (`CheckColds` then `AnalyzeSystem`)."""

    def resend_back_flow(self, sim_time: float) -> None:
        """Source's `ResendBackFlow`."""

    def flush_cold_changes(self, sim_time: float) -> None:
        """Drain the cold-standby change group (`IF coldsAffected:
        CheckColds`)."""

    @property
    def phase_change_in_progress(self) -> bool:
        """`PhaseChangeInProgress`: a `ConductPhaseChange` is queued or mid-way."""

    def park_for_pm_sync(self, block_id: int) -> None:
        """Join `pmSyncTrigger`'s waiters (`WAIT FOR pmSyncTrigger TO Fire`); the phase change's end
        resumes the block's own process."""

    @property
    def defer_trigger(self) -> str:
        """`deferTrig`: the trigger `maintDefer` blocks wait for ("" = off)."""

    def has_trigger(self, name: str) -> bool:
        """Whether the model has a trigger called `name` (`FOREACH trig IN triggerGroup`)."""

    def park_for_trigger(self, name: str, block_id: int) -> None:
        """Join trigger `name`'s waiters (`WAIT FOR trig TO Fire`); its next fire resumes the
        block's own process."""


class StateObserver(Protocol):
    """Told about each wake a block's process receives and each status
    change it makes. Used to build the state-coverage matrix; it must not
    change anything it is told about."""

    def on_wake(self, block: RBDBlock, site: WaitSite, wake: Wake | None) -> None:
        """`wake` is `None` when the wait ran its full length."""

    def on_transition(self, block: RBDBlock, old: BlockStatus, new: BlockStatus, comment: str) -> None:
        """The block's own `ChangeBlockState` from `old` to `new`."""


_observer: ContextVar[StateObserver | None] = ContextVar("block_state_observer", default=None)


@contextmanager
def observing(observer: StateObserver) -> Iterator[StateObserver]:
    """Install `observer` for the simulations run inside the `with` block
    (in this thread or task); the driver copies it into each run's
    `BlockEnv`."""
    token = _observer.set(observer)
    try:
        yield observer
    finally:
        _observer.reset(token)


def current_observer() -> StateObserver | None:
    """The observer installed by `observing(...)`, if any."""
    return _observer.get()


@dataclass(frozen=True)
class BlockEnv:
    """The per-run environment every ordinary block's process shares.

    Built once per run by the driver. A field left at its default suits a
    caller with no such feature (a single-block test harness, say).
    """

    scheduler: Scheduler
    #: The run's horizon: a block still waiting at this time ends `Done`
    #: ("End_sim").
    run_length: float
    #: The run's event-log lines, appended to in order.
    logs: list[str]
    cost_analysis: bool = False
    weak_analysis: bool = False
    events_file: bool = False
    #: The model-wide StartUpFailures switch (`startUpFailures`).
    startup_failures: bool = False
    #: The model has phases, so a pmMisDefer PM can be deferred by mission.
    phase_defer_enabled: bool = False
    #: `SysStream[10]`, the startup-failure stream.
    rng_sys10: RandomSource | None = None
    system_state: SystemState | None = None
    #: `System.missionStatus=Mission` right now -- a callable because the
    #: status changes over the run. Feeds `change_block_state`'s
    #: `blockMissionBool` clear (weak-link analysis' `R` column); `None`
    #: never clears, which is right for callers with no missions.
    in_mission_fn: Callable[[], bool] | None = None
    #: Interrupts a block once a background spare or resource process
    #: grants it (`(block_id, reason, kind) -> None`).
    wake_block: WakeBlockFn | None = None
    all_blocks: list[RBDBlock] | None = None
    #: ER3-08 (`dep_type=="RBDEvent"`): only consulted by
    #: `repair_completion_status` to resolve the depended-upon event by id.
    all_events: list[RBDEvent] | None = None
    end_node: RBDNode | None = None
    nodes_by_id: dict[int, RBDNode] | None = None
    links_by_id: dict[int, Link] | None = None
    #: Per-run registry the driver reads back at run end for the report's
    #: Custom_Sparing_Information table: each block owns its own Custom
    #: stock (nothing outside the block touches it), so the queue exists
    #: only in its process, which registers it here.
    custom_spares_registry: dict[int, ResourceQueue] | None = None
    cascade: CascadePort | None = None
    #: See `StateObserver`; `None` in ordinary runs.
    observer: StateObserver | None = None


@dataclass(frozen=True)
class BlockPools:
    """The shared pools one block draws on, and what it can spawn to refill
    them. All `None` for a block that uses none."""

    #: The RESOURCE_DATA pool the block's repair and PM borrow from
    #: (resolved by `block.resource_pool_name`), returned after use.
    resource_pool: ResourceQueue | None = None
    #: Cost accounting for `resource_pool`.
    pool_stats: PoolStats | None = None
    #: The SPARE_POOL_DATA pool the block's spares come from (resolved by
    #: `block.pool_name`), consumed on use.
    spare_pool: ResourceQueue | None = None
    #: `spare_pool`'s static configuration.
    spare_pool_config: SparePool | None = None
    #: Schedules an emergency order (ESO) for `spare_pool`.
    spawn_order_a_spare: SpawnOrderASpareFn | None = None
    #: Schedules a stock-level order (SLO) for `spare_pool`; required
    #: whenever `spare_pool_config.stock_level_ordering` is set.
    spawn_order_stock: Callable[[int], None] | None = None
