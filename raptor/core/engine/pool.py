"""A FIFO give/take-back resource queue -- standard Modsim/SIMSCRIPT
`RESOURCE` semantics (`WAIT FOR res TO Give(who, amount)` / `ASK res TO
TakeBack(who, amount)`), which Raptor's own `PoolObj` (`PoolObj = OBJECT(ResourceObj)`) is built on. There is no Modsim source to
port for the queueing mechanics themselves -- `ResourceObj` is a
language-level built-in, not user Raptor code -- so this reproduces the
standard semantics of that primitive instead: strict FIFO, head-of-line
blocking (a later waiter whose smaller request could be satisfied right now
still waits behind an earlier waiter whose larger request cannot -- no
reordering, no skipping ahead).

Scope: `RESOURCE_DATA` pools (`SparingType.RESOURCE`) -- fixed capacity for
the whole run, borrowed during repair and returned via `release()` afterward.
`SPARE_POOL_DATA` pools (`SparingType.SPARE_POOL`) reuse this same queue for
their own give-only, never-returned consumption (see `block_lifecycle.py`),
but are grown mid-run by the three background replenishment processes below
(RSO/ESO/SLO; see `order_stock_process`'s docstring for why it is a
near-duplicate of `order_a_spare_process` rather than a shared helper).

`num_emer_ordered` is spare-pool-only bookkeeping (`PoolObj.NumEmerOrdered`);
it stays 0 for `RESOURCE_DATA` pools, which have no emergency ordering.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Protocol
from collections.abc import Callable

from raptor.core.engine.blocks import BlockStatistics
from raptor.core.engine.eventlog import format_event_log_line
from raptor.core.model import RBDBlock, SparePool
from raptor.core.scheduler import Process, Scheduler
from raptor.core.engine.interrupts import InterruptReason


#: Sentinels for "no stock change observed yet this run" -- see
#: `ResourceQueue.begin_run`.
_UNSET_MIN = 1 << 62
_UNSET_MAX = -(1 << 62)


@dataclass
class ResourceQueue:
    """Runtime state for one pool. `resources` starts at the pool's
    static `SparePool.initial_spares` each run -- this class doesn't
    know its own starting capacity, callers reset it explicitly each run
    (same pattern as `NodeStatistics`/`SystemState` elsewhere in this
    project, see `run_control.py`)."""

    resources: int
    num_emer_ordered: int = 0
    _queue: list[tuple[int, int, str]] = field(default_factory=list)
    #: Instants at which a borrowed unit is due back (see
    #: `note_release_at`/`release_due_at`). Used by `block_lifecycle`'s
    #: repair-resource wait to tell whether a same-instant sibling release
    #: is genuinely due -- see `tests/golden/ResourceRepairOrderProbe`.
    _release_times: list[float] = field(default_factory=list)

    #: Per-run stock statistics -- source keeps these in a MODSIM monitor
    #: on the pool's own `Resources` (`CustSpareMon[i]`/`PoolMon[i]`)
    #: plus two hand-kept counters
    #: (`currNumWaits`/`currNumArrivals`, reset per run).
    #: Tracked here because this class is the only place
    #: `resources` is mutated; every other reference in the engine reads it.
    #:
    #: `time_fn` supplies the clock for the TIME-WEIGHTED mean. The class
    #: stays `Scheduler`-agnostic by design (see `release()`'s own note),
    #: so this is a plain callable the caller wires up, and leaving it
    #: `None` disables the mean without affecting anything else.
    time_fn: Callable[[], float] | None = None
    #: True for a RESOURCE pool. Only affects `NumDelays`: the source counts a
    #: PM-driven resource wait twice -- `ObtainPMprereqs` increments
    #: `currNumWaits[resPoolNum+totalBlocks]` once when it finds the pool short
    #: and again at the end of the same `IF (NOT resAvail)`
    #: block, whose own comment mislabels it as a spare-pool index.
    #: The Raptor 7 engine therefore double-counts this wait; ported as observed.
    is_resource_pool: bool = False
    init_stock: int = 0
    arrivals: int = 0
    waits: int = 0
    min_stock: int = 0
    max_stock: int = 0
    _area: float = 0.0
    _last_t: float = 0.0

    def begin_run(self, initial: int, now: float = 0.0) -> None:
        """Reset for a new run -- source's own per-run `currInitNumber[i]
        :=...Resources; currNumWaits[i]:=0; currNumArrivals[i]:=0`
        plus the monitor reset."""
        self.resources = initial
        self.num_emer_ordered = 0
        self._queue.clear()
        self.init_stock = initial
        self.arrivals = 0
        self.waits = 0
        # Min/Max start unset. A stock level counts toward them only once it has
        # been held for a positive duration (see `_accrue`): an initial level
        # replaced within the same instant is held for zero time and MODSIM's
        # timed monitor never records it. The discriminator is timing, not value.
        self.min_stock, self.max_stock = _UNSET_MIN, _UNSET_MAX
        self._area = 0.0
        self._last_t = now

    def reset_statistics(self, now: float = 0.0) -> None:
        """Delayed `ResetAllStatistics` for a pool:
        reset the stock monitor (Min/Max/time-weighted area) and the
        wait/arrival counters, without altering the physical allocation
        (`resources`/`_queue`)."""
        self.arrivals = 0
        self.waits = 0
        self.min_stock, self.max_stock = _UNSET_MIN, _UNSET_MAX
        self._area = 0.0
        self._last_t = now

    def _accrue(self) -> None:
        """Close the current stock level's time slice. Called on EVERY
        mutation, before the level changes."""
        if self.time_fn is None:
            return
        now = self.time_fn()
        if now > self._last_t:
            # The level being closed was actually held -- it counts toward
            # Min/Max. A level set and replaced at the same instant does not
            # (see `begin_run`). The time-weighted mean has always behaved
            # this way implicitly, via the zero-width area slice.
            self._observe()
        self._area += self.resources * (now - self._last_t)
        self._last_t = now

    def stock_extremes(self, end: float) -> tuple[int, int]:
        """(Min, Max) at run end. The level current at `end` counts only if
        it was held for a positive duration before it (same rule as
        `_accrue`); a pool that never recorded anything falls back to its
        current level, matching the `observed_*` properties."""
        lo, hi = self.min_stock, self.max_stock
        # The level current at the run end always counts, even when it was set at that very instant:
        # `LadL2Pool` (real 2026-10-01) reports MaxStock 12 in a run whose restock of 2 landed at exactly
        # t=4000, taking the stock from 10 to 12. A level set and REPLACED within an instant mid-run is still
        # never recorded (see `begin_run`).
        lo, hi = min(lo, self.resources), max(hi, self.resources)
        if lo == _UNSET_MIN:
            lo = self.resources
        if hi == _UNSET_MAX:
            hi = self.resources
        return lo, hi

    @property
    def observed_min_stock(self) -> int:
        return self.resources if self.min_stock == _UNSET_MIN else self.min_stock

    @property
    def observed_max_stock(self) -> int:
        return self.resources if self.max_stock == _UNSET_MAX else self.max_stock

    def _observe(self) -> None:
        if self.resources < self.min_stock:
            self.min_stock = self.resources
        if self.resources > self.max_stock:
            self.max_stock = self.resources

    def mean_stock(self, run_length: float) -> float:
        """Time-weighted mean over the whole run -- `AveNumSpares`. Time-weighted,
        not observation-weighted: `tests/golden/SLOTest2`'s `Cells` pool
        alternates 0<->1 all run and reports `AveStock 0.09`, where an
        observation-based mean of the same history is ~0.5."""
        area = self._area
        if self.time_fn is not None:
            area += self.resources * (run_length - self._last_t)
        return area / run_length if run_length > 0 else 0.0

    def request(self, block_id: int, amount: int, *, kind: str = "repair") -> bool:
        """Returns `True` if `amount` units were granted immediately (the
        caller proceeds without waiting). Returns `False` if queued
        (FIFO, by `block_id`) -- the caller must suspend (a far-future
        `yield`, same pattern as `dependency.py`'s Idle-wait) until
        `release()` elsewhere grants it, signaled via `Scheduler.
        interrupt` with reason `"Pool_Available"` (the caller's own
        responsibility to dispatch -- this class has no `Scheduler`
        reference, see module docstring's `block_id`-not-`ProcessHandle`
        design note in `release()`).

        `kind`: caller-supplied free-form tag (`"repair"`/`"pm"` in practice, see
        `block_lifecycle.py`'s two callers), carried through unchanged to
        `release_kinds()`'s return value -- a pure passthrough label, this
        class has no opinion on what the values mean. An ordinary
        repair-resource grant always sorts ahead of the releasing block's own
        cascade, unconditionally; a PM grant's position depends on a separate
        same-node test the caller applies using this tag -- see
        `orchestrator.py`'s wake_ids loop."""
        if not self._queue and self.resources >= amount:
            self._accrue()
            self.resources -= amount
            if self.time_fn is None:
                self._observe()
            return True
        # `INC(currNumWaits[...])` -- source has ~12 near-identical call
        # sites (one per repair-flow variant x sparing type, plus
        # ObtainPMprereqs'), every one of them immediately before the
        # `WAIT FOR ... TO Give` this `False` return stands for. Counted
        # once, here, where all of them converge.
        self.waits += 2 if (kind == "pm" and self.is_resource_pool) else 1
        self._queue.append((block_id, amount, kind))
        return False

    def is_waiting(self, block_id: int) -> bool:
        """`True` if `block_id` still has an outstanding, ungranted request in
        the FIFO queue. Used by `block_lifecycle.py`'s deferred-PM pickup to
        detect a grant even when the `"Pool_Available"` interrupt announcing it
        was staled out by a later same-instant interrupt for the same block
        handle (`Scheduler.interrupt`'s generation-staling), which would
        otherwise discard the announcement while the grant itself (already
        applied to `resources`/`_queue` by `release()`) stands."""
        return any(bid == block_id for bid, _, _ in self._queue)

    def note_release_at(self, when: float) -> None:
        """Record that a borrowed unit is due back at `when` (a repair's
        natural completion). See `release_due_at`."""
        self._release_times.append(when)

    def release_due_at(self, now: float) -> bool:
        """True if a recorded release is due at exactly `now` (1e-9
        tolerance), pruning stale entries. NON-destructive: several
        waiters at the same instant may all observe the same release."""
        self._release_times = [t for t in self._release_times if t >= now - 1e-9]
        return any(abs(t - now) <= 1e-9 for t in self._release_times)

    def count_waiting(self, block_id: int) -> int:
        """How many of `block_id`'s own requests are still queued (ungranted).
        `is_waiting`'s binary version is correct only when a block can have at
        most one outstanding request; a `pmReqDefer`'d PM request can register
        a second one before the first is ever reconciled (`ObtainPMprereqs`
        is a separate background process in the source that
        `block_run_process` cannot fully model as one generator -- see
        `block.run_state.pm_resource_pending`'s docstring). The caller compares this
        against however many requests it believes it made
        (`pm_resource_pending`) to find "how many have been granted but not
        yet claimed"; `is_waiting()` alone would read as "nothing granted yet"
        whenever any of several requests is still queued, even if an earlier
        one already landed."""
        return sum(1 for bid, _, _ in self._queue if bid == block_id)

    def withdraw(self, block_id: int, kind: str) -> bool:
        """Removes one still-queued `(block_id, kind)` entry (the earliest
        matching one) and returns whether it found one. A `pmReqDefer`'d PM
        request that is superseded by a genuinely new, unrelated failure before
        it is ever granted is discarded outright by the Raptor 7 engine, not left
        queued to be reconciled whenever it eventually lands. Other scenarios
        leave the deferred request live and consume it later; this method is
        for the "a new failure makes it structurally impossible for this block
        to ever want that PM grant again" case. The caller is responsible for
        calling this for the right reason and for reconciling whatever
        bookkeeping (`pm_resource_pending`) tracked the now-withdrawn
        request."""
        for i, (bid, _amt, k) in enumerate(self._queue):
            if bid == block_id and k == kind:
                del self._queue[i]
                return True
        return False

    def release(self, amount: int) -> list[int]:
        """Returns `amount` units, then serves the FIFO queue head-of-
        line, stopping at the first waiter whose request still can't be
        satisfied. Returns the list of `block_id`s just granted, IN
        ORDER -- deliberately `block_id` not `ProcessHandle` (this class
        stays `Scheduler`-agnostic; the caller looks up each id's own
        handle and calls `scheduler.interrupt(handle, "Pool_Available")`
        itself, same indirection `orchestrator._Cascade` already uses
        for dependency-triggered interrupts). Thin wrapper over
        `release_kinds()` for the many callers that don't care about
        request `kind` (spare-pool consumption, RSO/ESO/SLO
        replenishment, ...) -- unchanged signature/behavior."""
        return [block_id for block_id, _kind in self.release_kinds(amount)]

    def release_kinds(self, amount: int) -> list[tuple[int, str]]:
        """Like `release()`, but returns `(block_id, kind)` pairs -- `kind` is
        whatever `request()` was called with for that specific queued request
        (see `request()`'s docstring). Used by `block_lifecycle.py`'s two
        `RESOURCE_DATA` release call sites (ordinary repair completion, PM
        completion), which need to tell an ordinary repair-resource grant
        apart from a PM-prereq grant to route the wake correctly; every other
        caller uses plain `release()` above."""
        self._accrue()
        self.resources += amount
        if self.time_fn is None:
            self._observe()
        granted: list[tuple[int, str]] = []
        while self._queue and self._queue[0][1] <= self.resources:
            block_id, amt, kind = self._queue.pop(0)
            self.resources -= amt
            granted.append((block_id, kind))
        if self.time_fn is None:
            self._observe()
        return granted


#: Signature for "wake this OTHER block once a background replenishment
#: process just granted it a spare" -- unlike `block_run_process`'s own
#: resource-pool `wake_ids` (which has to land at one precise point
#: inside `_Cascade.apply`'s pipeline, see that method's docstring),
#: these two processes have no cascade of their own to interleave with,
#: so a plain callback (heap-pushed `Scheduler.interrupt`, no special
#: synchronous-chaining concerns) is sufficient -- the awakened block's
#: own resumption naturally sorts after THIS process's own remaining
#: code (which is only ever one more log line before it loops/ends).
WakeBlockFn = Callable[[int, str, str], None]


class SpawnOrderASpareFn(Protocol):
    """Schedule one `order_a_spare_process` (ESO) for a spare pool.
    `deferred=True` starts it as its own selection at the tail of the
    instant (the failure path); otherwise it starts on the next step."""

    def __call__(self, *, deferred: bool = False) -> None: ...


@dataclass
class PoolStats:
    """Per-pool cost accumulators (Raptor 7's `PoolAddCost`/
    `PoolESCost` arrays, reset every run). For a RESOURCE pool
    both fill: `es_cost` is charged per use at every borrow
    (`fixedPerUse*numRes1`) and shows as the report's PerUseCost column;
    `add_cost` accrues time-based while a borrowing block sits Repairing/PM
    (`stateTime*costPerTime*numRes1`) and shows as PerTimeCost. For a
    SPARE_POOL both stay 0.0: pooled replenishment purchases are not charged
    anywhere in the source (the only `PoolAddCost`/`PoolESCost` writes are
    `resPoolNum`-indexed resource borrows), kept accumulatable anyway so the
    report writer need not special-case."""

    add_cost: float = 0.0
    es_cost: float = 0.0


def generate_spares_process(
    pool_config: SparePool,
    pool: ResourceQueue,
    scheduler: Scheduler,
    wake_block: WakeBlockFn,
    logs: list[str],
    *,
    events_file: bool = False,
    pool_stats: PoolStats | None = None,
) -> Process:
    """Port of `PoolObj.GenerateSpares` -- RSO's
    periodic background restocking. A `TELL METHOD` in the source (fire-
    and-forget, spawned once and left running): schedule this ONCE per
    RSO-enabled pool at the start of each run via `scheduler.
    schedule_process(...)`, never re-spawn it. Runs an unbounded `LOOP`
    for the rest of the run -- matches the source, which has no exit
    condition of its own (the real process is torn down by `DISPOSE
    (PooledSpares)` at the very end of `StartEngine`, not anything
    internal); this generator simply keeps yielding until `run_until`
    stops advancing time.
    """
    while True:
        yield pool_config.new_spares_arrival
        if pool_stats is not None:
            # Raptor 7 -- RSO arrival charges the POOL's own
            # purchase cost for the batch (PoolAddCost).
            pool_stats.add_cost += float(pool_config.new_spares) * pool_config.spare_cost
        # `currNumArrivals[i+totalBlocks]+:=newSpares`
        pool.arrivals += pool_config.new_spares
        granted_ids = pool.release_kinds(pool_config.new_spares)
        if events_file:
            logs.append(format_event_log_line(
                scheduler.time, "Pool", pool_config.pool_name, "Sparing",
                f"{pool_config.new_spares}_New_spare(s)_arrived_(stock={pool.resources})",
            ))
        for granted_id, granted_kind in granted_ids:
            wake_block(granted_id, InterruptReason.POOL_AVAILABLE, granted_kind)


def order_a_spare_process(
    pool_config: SparePool,
    pool: ResourceQueue,
    scheduler: Scheduler,
    wake_block: WakeBlockFn,
    logs: list[str],
    *,
    events_file: bool = False,
    pool_stats: PoolStats | None = None,
) -> Process:
    """Port of `PoolObj.OrderASpare` -- ESO's
    one-shot emergency order. A `TELL METHOD` in the source: spawn a NEW
    one of these via `scheduler.schedule_process(...)` EVERY TIME a block
    triggers an emergency order (`CheckForSpare`'s `SparePool` branch)
    -- multiple can run concurrently
    for the same pool, one per outstanding emergency order.
    """
    if pool_stats is not None:
        # Raptor 7 -- charged at ORDER time, before the wait.
        pool_stats.add_cost += pool_config.spare_cost
        pool_stats.es_cost += pool_config.emergency_shipping_cost
    if events_file:
        logs.append(format_event_log_line(
            scheduler.time, "Pool", pool_config.pool_name, "Sparing",
            f"Spare_ordered_(stock={pool.resources})",
        ))
    yield pool_config.emergency_time
    # `currNumArrivals[...]+:=1` -- ESO's one-shot.
    pool.arrivals += 1
    granted_ids = pool.release_kinds(1)
    if events_file:
        logs.append(format_event_log_line(
            scheduler.time, "Pool", pool_config.pool_name, "Sparing",
            f"Ordered_spare_arrives_(stock={pool.resources})",
        ))
    for granted_id, granted_kind in granted_ids:
        wake_block(granted_id, InterruptReason.POOL_AVAILABLE, granted_kind)


def order_stock_process(
    pool_config: SparePool,
    pool: ResourceQueue,
    scheduler: Scheduler,
    wake_block: WakeBlockFn,
    logs: list[str],
    quantity: int,
    *,
    events_file: bool = False,
    pool_stats: PoolStats | None = None,
) -> Process:
    """Port of `PoolObj.OrderStock` -- SLO's
    stock-level-triggered order, for a variable `quantity` (unlike ESO's
    always-1 `OrderASpare`) after `pool.SLOTime` (not `emergencyTime`).
    A `TELL METHOD`: spawn a NEW one of these via `scheduler.tell(...)`
    every time a grant leaves the pool's stock at or below its
    configured trigger threshold (`CheckForSpare`'s/`Repair`'s own
    `stockLevelOrdering` checks -- three near-identical call sites, one per repair-flow
    variant).

    Deliberately a near-duplicate of `order_a_spare_process` rather than a
    shared parameterized helper: the two are independently spawnable and run
    concurrently in the source (an emergency order and a stock-level order for
    the same pool can both be in flight at once, as in
    `tests/golden/HierarchyExample`'s `Cells` pool with RSO+ESO+SLO all
    enabled) -- keeping them separate matches that independence directly.
    """
    if pool_stats is not None:
        # Raptor 7 -- charged at ORDER time, before the wait.
        pool_stats.add_cost += pool_config.spare_cost * float(quantity)
    if events_file:
        logs.append(format_event_log_line(
            scheduler.time, "Pool", pool_config.pool_name, "Sparing",
            f"{quantity}_Spare(s)_ordered_(stock={pool.resources})",
        ))
    yield pool_config.slo_time
    # `currNumArrivals[...]+:=quantity` -- SLO's batch.
    pool.arrivals += quantity
    granted_ids = pool.release_kinds(quantity)
    if events_file:
        logs.append(format_event_log_line(
            scheduler.time, "Pool", pool_config.pool_name, "Sparing",
            f"{quantity}_Spare(s)_arrived_(stock={pool.resources})",
        ))
    for granted_id, granted_kind in granted_ids:
        wake_block(granted_id, InterruptReason.POOL_AVAILABLE, granted_kind)


def generate_custom_spares_process(
    block: RBDBlock,
    custom_spares: ResourceQueue,
    scheduler: Scheduler,
    wake_block: WakeBlockFn,
    logs: list[str],
    *,
    events_file: bool = False,
    block_stats: BlockStatistics | None = None,
) -> Process:
    """Port of `RBDBlockObj.GenerateSpares` --
    RSO's periodic background restocking for custom (per-block private)
    sparing. A near-duplicate of `generate_spares_process` (the
    `SPARE_POOL_DATA` version): same unbounded `LOOP` and
    `TELL`-once-per-run-if-`routineSpareOrdering` spawn discipline, but reads
    `block.new_spares_arrival`/`new_spares` instead of a `SparePool` config,
    and logs `"Block"`/`block.name` instead of `"Pool"`/`pool_name`, matching
    the source's `WriteEvents(SimTime,"Block",name,...)`. Kept separate for
    the same reason `order_stock_process` is separate from
    `order_a_spare_process`.
    """
    while True:
        yield block.new_spares_arrival
        # Raptor 7 -- RSO arrival charges the BLOCK's spare
        # purchase cost for the whole batch (Custom sparing pays at
        # MANUFACTURE time, not consumption).
        if block_stats is not None:
            block_stats.sp_cost += block.spare_cost * float(block.new_spares)
        custom_spares.arrivals += block.new_spares
        granted_ids = custom_spares.release_kinds(block.new_spares)
        if events_file:
            logs.append(format_event_log_line(
                scheduler.time, "Block", block.name, "Sparing",
                f"{block.new_spares}_new_spare(s)_arrived_(stock={custom_spares.resources})",
            ))
        for granted_id, granted_kind in granted_ids:
            wake_block(granted_id, InterruptReason.POOL_AVAILABLE, granted_kind)


def order_a_custom_spare_process(
    block: RBDBlock,
    custom_spares: ResourceQueue,
    scheduler: Scheduler,
    wake_block: WakeBlockFn,
    logs: list[str],
    *,
    events_file: bool = False,
    block_stats: BlockStatistics | None = None,
) -> Process:
    """Port of `RBDBlockObj.OrderASpare` --
    ESO's one-shot emergency order for custom (per-block private) sparing.
    Otherwise identical in shape to `order_a_spare_process` (the
    `SPARE_POOL_DATA` version), reading `block.emer_time` instead of
    `pool_config.emergency_time` and logging `"Block"`/`block.name` instead of
    `"Pool"`/`pool_name`. `block_stats`: the block's own `BlockStatistics` when
    `costAnalysis` is on -- the order charges `BlockSpCost += spareCost;
    BlockESCost += emerShippingCost` at order time, before the
    wait, not at arrival.
    """
    if block_stats is not None:
        block_stats.sp_cost += block.spare_cost
        block_stats.es_cost += block.emergency_shipping_cost
    if events_file:
        logs.append(format_event_log_line(
            scheduler.time, "Block", block.name, "Sparing",
            f"Spare_ordered_(stock={custom_spares.resources})",
        ))
    yield block.emer_time
    custom_spares.arrivals += 1
    granted_ids = custom_spares.release_kinds(1)
    if events_file:
        logs.append(format_event_log_line(
            scheduler.time, "Block", block.name, "Sparing",
            f"Ordered_spare_arrives_(stock={custom_spares.resources})",
        ))
    for granted_id, granted_kind in granted_ids:
        wake_block(granted_id, InterruptReason.POOL_AVAILABLE, granted_kind)


def order_custom_stock_process(
    block,
    custom_spares,
    scheduler,
    wake_block,
    logs,
    quantity: int,
    *,
    events_file: bool = False,
    block_stats=None,
) -> Process:
    """Port of `RBDBlockObj.OrderStock` -- SLO's stock-level-triggered order
    for custom (per-block private) sparing. Otherwise identical in shape to
    `order_stock_process` (the `SPARE_POOL_DATA` version), reading
    `block.slo_time` for the wait; messages land under `"Block"`/`block.name`.
    Deferred-spawned (`schedule_process`) like its pool twin, since the order
    lines land after the consuming block's Repairing line."""
    if block_stats is not None:
        block_stats.sp_cost += block.spare_cost * float(quantity)
    if events_file:
        logs.append(format_event_log_line(
            scheduler.time, "Block", block.name, "Sparing",
            f"{quantity}_Spare(s)_ordered_(stock={custom_spares.resources})",
        ))
    yield block.slo_time
    custom_spares.arrivals += quantity
    granted_ids = custom_spares.release_kinds(quantity)
    if events_file:
        logs.append(format_event_log_line(
            scheduler.time, "Block", block.name, "Sparing",
            f"{quantity}_Spare(s)_arrived_(stock={custom_spares.resources})",
        ))
    for granted_id, granted_kind in granted_ids:
        wake_block(granted_id, InterruptReason.POOL_AVAILABLE, granted_kind)
