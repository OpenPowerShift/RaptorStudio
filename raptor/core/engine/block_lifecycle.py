"""A block's lifecycle: `RBDBlockObj.Run`/`Repair`/`PerformPM` as one process.

`BlockProcess` is the scheduler-driven port of an ordinary block's life
(`BlockProcess(...).run()` is a `core.scheduler.Process`;
`block_run_process(...)` builds and runs one): failure draws and the PM race, repair with
pre/post logistics delay, Infinite/Custom/SparePool/None sparing (with
RSO/ESO/SLO replenishment), resource pools, PM prerequisites, and
dependency- and phase-driven Idle/Standby transitions. Blocks that depend on
another block (`dep_type == "RBDBlock"`) run the same process: a dependency
is only a source of `Dependence_*` wakes (Raptor 7 captures: DepLdtSeamProbe,
DepPmSpareProbe and the rest of the Dep* probes).

Source: Raptor 7 (`Run`, `Repair`). Relevant
fixtures: `tests/golden/UniformProbe`, `PrePostLDT`, `FiniteSparePool`,
`SimStartProbe*`, `HierarchyExample`; see `test_block_lifecycle.py`.
"""
from __future__ import annotations

import itertools
import math
from dataclasses import dataclass, field
from enum import Enum, auto
from typing import NamedTuple
from collections.abc import Callable, Generator

from raptor.core.model import (ActiveStatus, BlockStatus, Link, RBDBlock,
                               RBDEvent, RBDNode, SparePool, SparingType)
from raptor.core.rng import Distribution, RandomSource
from raptor.core.scheduler import Owner, Process, Scheduler, Wait, Wake
from raptor.core.engine.blocks import BlockStatistics, StateDeltas, change_block_state, write_block_failed_line
from raptor.core.engine.block_env import BlockEnv, BlockPools
from raptor.core.engine.wait_sites import IDLE_WAKE_REASONS, WaitSite
from raptor.core.engine.dependency import (
    distro_mean, fail_mean, needs_repair_idle_recheck, repair_completion_status,
    resolve_dependency_node_id,
)
from raptor.core.engine.eventlog import format_event_log_line
from raptor.core.engine.system_status import (
    MissionStatus, SystemState, record_component_failure, record_component_pm,
    record_component_repair,
)
from raptor.core.engine.errors import EngineInvariantError, UnexpectedWake, expect_wake, require
from raptor.core.engine.pool import (
    PoolStats, ResourceQueue, SpawnOrderASpareFn, WakeBlockFn, generate_custom_spares_process,
    order_a_custom_spare_process, order_custom_stock_process,
)
from raptor.core.engine.interrupts import InterruptReason, KICKOFF_DEPENDENCE_WAKES, STATE_ALREADY_APPLIED
from raptor.core.engine.sim_details import tag_next_draw


def startup_failure_redraw(
    block: RBDBlock, rng_sys10: RandomSource, fail_time_left: float | None,
    cost_analysis: bool, stats: BlockStatistics,
) -> float | None:
    """One `startUpFailures` redraw (and its two
    twins ).

    When the model defines a TRIGGER named exactly "StartUpFaiLures" and this
    block's `initial_cost < 1.0`, a reactivated block draws Uniform(0,1) from
    SysStream[10]; a draw greater than `initial_cost` forces
    `fail_time_left = 0.000001`, so the block fails almost immediately after
    reactivation (infant mortality on switch-on). Returns the possibly forced
    value. Callers gate on the flag and `initial_cost` before the draw:
    blocks with `initial_cost >= 1.0` skip the branch including the draw."""

    def _do() -> float | None:
        # tempArray[1]=0, [2]=1 -- Uniform(0,1) via dist 10. Source passes
        # the block's `name` as DrawNumber's `/sd` tag.
        tag_next_draw(rng_sys10, block.name)
        draw = rng_sys10.draw_number(Distribution(10), [0.0, 1.0])
        if draw > block.initial_cost:
            return 0.000001
        return fail_time_left

    if rng_sys10 is None:
        return fail_time_left
    return _do()


def _res_fixed_per_use(block: RBDBlock) -> float:
    """`PoolArray[resPoolNum].fixedPerUse` -- resolved once at load time
    (`load_model` sets `block.res_fixed_per_use` from the block's
    RESOURCE_DATA row), the same indirection `blocks.py` uses for
    `_pool_cost_per_time`. Returns 0.0 when never wired."""
    return block.res_fixed_per_use

#: A "wait forever" duration for a block parked in Idle awaiting an
#: external interrupt (`Scheduler.interrupt`) rather than a timed event.
#: Duplicated from orchestrator.py rather than imported to avoid a circular
#: import (orchestrator.py imports from this module).
_FAR_FUTURE = 1.0e9


def _has_ldt(dist: int, params: list[float]) -> bool:
    """Port of the `NOT((preDist=19) AND (preParams[1]=0.0))` guard
    (mirrored for post-LDT).

    `Fixed(0.0)` (distro 19, first param 0.0) means "no delay configured";
    anything else is a real delay to draw and wait out. An empty `params` (an
    `RBDBlock` built without `pre_params`/`post_params`) is also treated as
    `Fixed(0.0)`, avoiding an index into an empty list."""
    return not (dist == 19 and (not params or params[0] == 0.0))


def _custom_spares_replenishable(block: RBDBlock) -> bool:
    """Port of `CheckForSpare`'s Custom-sparing "any due to come in?"
    guard.

    Reports whether any replenishment path is configured (ESO, RSO or SLO).
    `sparesOut` (permanent exhaustion) is set only when none is, and the
    caller then routes the block to Done."""
    return block.emergency_spare_ordering or block.routine_spare_ordering or block.stock_level_ordering


def _obpm_over_threshold(block: RBDBlock, operating_time: float) -> bool:
    """Port of `Run`'s own "OBPM" interrupt-reason threshold check
: `OperatingTime > DepPMThreshold*failMean/100.0`.

    `operating_time` is the caller's running accumulator of real elapsed time
    across the current draw's accumulating-life segments, reset on a fresh
    draw -- not the full original draw."""
    return operating_time > (block.dep_pm_threshold * fail_mean(block) / 100.0)


def combined_stress(block: RBDBlock, current_phase: int = 1) -> float:
    """Port of `Run`'s own `CombinedStress:=PhaseStress*StandbyStress/GDstress`.

    `GDstress` (growth/decay, `gd_type>0`): three formulas keyed on `gd_type`
    (1: linear in `failure_counter`; 2: `gd_rate**failure_counter`; 3:
    exponential approach to `gd_limit/fail_mean`), clamped so it never
    overshoots the `gd_limit/fail_mean` asymptote. Source's IF-chain handles
    only 1/2/3; any other positive `gd_type` falls through with `gdstress`
    still holding its uninitialized local value, modeled here as 0.0 so the
    clamp reproduces the observed fixed point when `0 < gd_limit/fail_mean <
    1`. Outside that window the real value depends on stack garbage and is not
    reproducible, so this raises.

    `PhaseStress` is the block's own per-phase stress value
    (`block.phase_value`, indexed 0-based against the 1-based
    `current_phase`) when `uses_phasing` and phasing is active, else 1.0.
    `opStatus=Standby` overrides `PhaseStress` back to 1.0 and sets
    `StandbyStress:=sb_stress`.

    The stress values are local to `Run`, used only to compute that call's
    wait duration; they are not persisted to any object field.
    """
    if block.gd_type == 0:
        gd_stress = 1.0
    else:
        gd_factor_lim = block.gd_limit / fail_mean(block)
        if block.gd_type == 1:
            gd_stress = block.gd_rate * float(block.run_state.failure_counter) + 1.0
        elif block.gd_type == 2:
            gd_stress = block.gd_rate ** float(block.run_state.failure_counter)
        elif block.gd_type == 3:
            gd_stress = gd_factor_lim + (1.0 - gd_factor_lim) * math.exp(-block.gd_rate * float(block.run_state.failure_counter))
        else:
            # Source's IF-chain handles only GDType 1/2/3; any other
            # GDType>0 leaves GDstress uninitialized. Model that as 0.0 so
            # the clamp below yields the deterministic fixed point observed
            # when 0 < GDFactorLim < 1.
            gd_stress = 0.0
        if gd_factor_lim > 1.0 and gd_stress > gd_factor_lim:
            gd_stress = gd_factor_lim
        elif gd_factor_lim < 1.0 and gd_stress < gd_factor_lim:
            gd_stress = gd_factor_lim
        if block.gd_type not in (1, 2, 3) and not (0.0 < gd_factor_lim < 1.0):
            # The uninitialized-GDstress fall-through is only reproducible
            # when the clamp rescues it (0 < GDFactorLim < 1). Outside that
            # window CombinedStress = 1/(stack garbage) is unportable.
            raise NotImplementedError(
                f"block {block.name!r}: GDType={block.gd_type} leaves GDstress uninitialized in "
                f"source (only 1/2/3 handled) and GDFactorLim={gd_factor_lim!r} is outside the "
                "(0,1) window where the clamp makes the outcome deterministic -- not portable"
            )

    if block.uses_phasing and len(block.phase_value) > 0:
        phase_stress = block.phase_value[current_phase - 1]
    else:
        phase_stress = 1.0

    if block.op_status == BlockStatus.STANDBY:
        standby_stress = block.sb_stress
        phase_stress = 1.0
    else:
        standby_stress = 1.0

    # `PhaseStress`/`CombinedStress`/`GDstress`/`StandbyStress` are locals in
    # `Run`, never persisted to the block.
    # `BlockRunState.phase_stress` is updated only by `ChangePhases` (the
    # orchestrator's kickoff seed and phase-cascade writes); writing it here
    # would clobber that cache.
    return phase_stress * standby_stress / gd_stress


class _Turn(Enum):
    """How a turn of `Run` ends when it does not route to `Repair`."""

    #: Start another turn (source's `Run;` re-entry / `returnToTop`).
    LOOP = auto()
    #: The block's life has ended (`Done`, or the end of the run).
    STOP = auto()


class _Wake(Enum):
    """What a fail-wait's wake handler tells its wait site to do."""

    #: Start another turn of `Run` (source's `returnToTop`).
    LOOP = auto()
    #: Carry on to the turn's `pm.due` / failure handling.
    PROCEED = auto()


class IdleExit(NamedTuple):
    """How `BlockProcess._idle_wait` left source's Idle / ZeroWait wait."""

    #: The block was Idle at least once this turn: the fresh-draw hop after
    #: the wait (Raptor 7's `WAIT DURATION 0.0`) applies.
    was_idle: bool
    #: A wake routed the block to `DetermineNewState("PM is due")`; the
    #: turn skips the redraw and the fail wait.
    pm_due: bool
    #: The `InterruptReason` that routed it there (meaningful when `pm_due`).
    pm_reason: str
    #: An `Induced_Failure` reached the ZeroWait site: the block fails from here.
    induced: bool = False


@dataclass
class SpareRequest:
    """One repair's spare, from `CheckForSpare` to its consumption in
    `Repair`: what the check found and what the early spare-pool grab did.
    Built by `BlockProcess._check_for_spare`; read by
    `BlockProcess._wait_for_spare`.
    """

    #: A spare still held from a PM attempt that pmReqDefer'd on its
    #: resource (source's `spareObtained`): no spare step, nothing counted.
    held: bool
    #: Custom sparing found no stock at the check.
    custom_was_empty: bool = False
    #: An emergency order was placed for this repair's pool spare and not
    #: yet matched by a grant (`DecNumEmerOrdered` pending).
    emer_ordered: bool = False
    #: The spare-pool grab at the start of `Repair` got the spare, and
    #: already counted it.
    obtained_early: bool = False
    #: Stock-level re-order quantity that grab decided (0 for none).
    pool_slo: int = 0


class RepairRequest(NamedTuple):
    """A turn of `Run` routed to `Repair` (`DetermineNewState`'s `Repair;`)."""

    #: A down-start's repair time from `GetStartCond`; `None` for a failure.
    start_val: float | None
    sim_time: float


@dataclass(frozen=True)
class StartCond:
    """Port of `GetStartCond`'s four outputs: the
    block's `start_condition` string plus the `fail_time_left`/`start_val`/
    `first_pre_ldt` values each branch writes.

    `start_condition` uses the source's literal vocabulary -- "Running", "preLDT",
    "repairing", "postLDT" -- because Raptor 7 and `Repair`'s own
    `CASE startCond` dispatch on exactly these strings.
    """
    start_condition: str
    fail_time_left: float = 0.0
    start_val: float = 0.0
    first_pre_ldt: float = 0.0


#: The `simStartType` values `get_start_cond` implements (Raptor 7
#: CASE 1..6). The validator and the GUI pre-flight take their rule from this
#: constant, so the "which start types run" list cannot go stale on its own.
START_TYPES: frozenset[int] = frozenset(range(1, 7))


def get_start_cond(block: RBDBlock, rng_fail: RandomSource, rng_repair: RandomSource) -> StartCond:
    """Port of `GetStartCond`. Called once per run
    for every block, unconditionally; this is where the
    kickoff fail-stream draw for each block comes from.

    The six `sim_start_type` values match the GUI's "This block begins the
    simulation..." options (carries the same
    mapping for the block-table columns):

      1  in a completely random state         -> "Random"  / N/A
      2  up in the following state, random    -> "Up"      / Random
      3  up in the following state, N% used   -> "Up"      / amountExhausted
      4  failed, at random time in rep cycle  -> "Down"    / Random
      5  failed, N% through the repair cycle  -> "Down"    / amountExhausted
      6  failed, at start of hands-on repair  -> "Down"    / At Start

    Draw accounting (every draw moves the shared stream):

    - Types 1/2/4 draw two `Uniform(0,1)` values from the FAIL stream first
      (a temp `[0.0, 1.0]` realArray passed as the distro).
      `rand_draw2` is unused for type 2 but still consumed. Types 3/5/6 draw
      neither.
    - Every type then draws `TimeToFail` from the block's own fail
      distribution, unconditionally, before the CASE -- even types
      5 and 6, which never read the value.
    - Types 1/4/5 additionally draw from the REPAIR stream inside the branch
      they take, for whichever of preLDT/TimeToRepair/postLDT that branch
      reaches. A `Fixed` draw is not logged in `simDetails.TXT`.

    A down-start is not counted as a failure: `block_run_process`'s
    `DownStart.repair_time` entry honours that asymmetry.
    """
    # `ComputeStats(preDist,preParams,preLDT,dummy)` / same for post
    # -- the branch arithmetic uses the DISTRIBUTION MEANS.
    # Source later reassigns `preLDT`/`postLDT` from an actual draw inside
    # several branches; the two uses stay distinct here (`pre_ldt_mean` vs
    # the drawn `pre_ldt`).
    pre_ldt_mean = distro_mean(block.pre_dist, block.pre_params)
    post_ldt_mean = distro_mean(block.post_dist, block.post_params)
    t = block.sim_start_type
    if t not in START_TYPES:
        raise NotImplementedError(
            f"Unknown simStartType={t!r} for block {block.name!r} -- source's own "
            "GetStartCond CASE covers 1..6 and alerts on anything else"
        )

    rand_draw1 = 0.0
    rand_draw2 = 0.0
    if t in (1, 2, 4):
        rand_draw1 = rng_fail.draw_number(Distribution.UNIFORM_REAL, [0.0, 1.0])
        rand_draw2 = rng_fail.draw_number(Distribution.UNIFORM_REAL, [0.0, 1.0])
    time_to_fail = rng_fail.draw_number(Distribution(block.fail_distro), block.fail_values)

    if t == 1:
        # -- "completely random": pick a point uniformly over
        # the whole fail/repair cycle and start there.
        if block.repair_distro == int(Distribution.NONE):
            # `IF repairDistro=18: cycleTime:=failMean` -- with no repair
            # leg the cycle is the operating life, so the first cut
            # (failMean/cycleTime = 1.0) always wins and the block is always
            # Running.
            cycle_time = fail_mean(block)
        else:
            cycle_time = fail_mean(block) + pre_ldt_mean + distro_mean(
                block.repair_distro, block.repair_values) + post_ldt_mean
        fm = fail_mean(block)
        if rand_draw1 < fm / cycle_time:
            return StartCond("Running", fail_time_left=time_to_fail * rand_draw2)
        if rand_draw1 < (fm + pre_ldt_mean) / cycle_time:
            pre_ldt = rng_repair.draw_number(Distribution(block.pre_dist), block.pre_params)
            time_to_repair = rng_repair.draw_number(Distribution(block.repair_distro), block.repair_values)
            return StartCond("preLDT", start_val=time_to_repair,
                             first_pre_ldt=pre_ldt * rand_draw2)
        if rand_draw1 < (fm + pre_ldt_mean + distro_mean(
                block.repair_distro, block.repair_values)) / cycle_time:
            time_to_repair = rng_repair.draw_number(Distribution(block.repair_distro), block.repair_values)
            return StartCond("repairing", start_val=time_to_repair * rand_draw2)
        post_ldt = rng_repair.draw_number(Distribution(block.post_dist), block.post_params)
        return StartCond("postLDT", start_val=post_ldt * rand_draw2)

    if t == 2:
        # "up random" -- starts Running with its first life
        # scaled by rand_draw1. Note type 2 draws one here; type 1's own
        # Running branch draws two (source really differs).
        return StartCond("Running", fail_time_left=time_to_fail * rand_draw1)

    if t == 3:
        # "up, some complete" -- the GUI's universal default.
        # Redraw (up to 100 times total, counting the pre-CASE draw above as
        # attempt 1) until a life exceeds the amount already used, then
        # subtract it.
        for i in range(100):
            if time_to_fail > block.amount_exhausted:
                return StartCond("Running", fail_time_left=time_to_fail - block.amount_exhausted)
            if i < 99:
                time_to_fail = rng_fail.draw_number(Distribution(block.fail_distro), block.fail_values)
        return StartCond("Running", fail_time_left=0.000001)  # 

    if t == 4:
        # "down random" -- same uniform-point pick as type 1
        # but over the repair cycle only (no operating leg).
        if block.repair_distro == int(Distribution.NONE):
            return StartCond("repairing")
        repair_mean = distro_mean(block.repair_distro, block.repair_values)
        cycle_time = pre_ldt_mean + repair_mean + post_ldt_mean
        if rand_draw1 < pre_ldt_mean / cycle_time:
            pre_ldt = rng_repair.draw_number(Distribution(block.pre_dist), block.pre_params)
            time_to_repair = rng_repair.draw_number(Distribution(block.repair_distro), block.repair_values)
            return StartCond("preLDT", start_val=time_to_repair,
                             first_pre_ldt=pre_ldt * rand_draw2)
        if rand_draw1 < (pre_ldt_mean + repair_mean) / cycle_time:
            time_to_repair = rng_repair.draw_number(Distribution(block.repair_distro), block.repair_values)
            return StartCond("repairing", start_val=time_to_repair * rand_draw2)
        post_ldt = rng_repair.draw_number(Distribution(block.post_dist), block.post_params)
        return StartCond("postLDT", start_val=post_ldt * rand_draw2)

    if t == 5:
        # "down, some complete" -- deterministic placement:
        # `amountExhausted` is a percentage here (`downPercent :=
        # amountExhausted/100.0`), applied to the cycle built from three
        # actual draws rather than the means. All three draws happen
        # unconditionally, before any branch is chosen -- unlike types 1/4,
        # which draw only inside the branch they take.
        if block.repair_distro == int(Distribution.NONE):
            return StartCond("repairing")
        pre_ldt = rng_repair.draw_number(Distribution(block.pre_dist), block.pre_params)
        time_to_repair = rng_repair.draw_number(Distribution(block.repair_distro), block.repair_values)
        post_ldt = rng_repair.draw_number(Distribution(block.post_dist), block.post_params)
        cycle_time = pre_ldt + time_to_repair + post_ldt
        down_percent = block.amount_exhausted / 100.0
        if down_percent < pre_ldt / cycle_time:
            return StartCond("preLDT", start_val=time_to_repair,
                             first_pre_ldt=pre_ldt - down_percent * cycle_time)
        if down_percent < (pre_ldt + time_to_repair) / cycle_time:
            return StartCond("repairing",
                             start_val=pre_ldt + time_to_repair - down_percent * cycle_time)
        return StartCond("postLDT",
                         start_val=pre_ldt + time_to_repair + post_ldt - down_percent * cycle_time)

    if t == 6:
        # "down, start of hands-on repair" -- the simplest:
        # one repair draw, taken whole.
        if block.repair_distro == int(Distribution.NONE):
            return StartCond("repairing")
        time_to_repair = rng_repair.draw_number(Distribution(block.repair_distro), block.repair_values)
        return StartCond("repairing", start_val=time_to_repair)
    raise EngineInvariantError(f"start type {t} passed the START_TYPES gate but has no CASE")  # unreachable



def _pm_race(block: RBDBlock, pm_time_left: float, fail_time_left: float,
             stress: float = 1.0) -> tuple[bool, float, float]:
    """Port of `Run`'s `PMTimeLeft`-vs-`FailTimeLeft` race.

    Frequency-based PM (`pm_triggered=False`) competes with the next failure
    for whichever comes first. `pm_time_left` refills to `pm_freq` the moment
    it is due (`<= 0.0`) before the comparison, and `pm_fail_reset` resets it
    to `0.0` on every real failure (see the caller), so PM can only win when
    `pm_freq` is smaller than a typical failure-time draw. Returns
    `(pm_due, new_pm_time_left, wait_time)`.

    Only frequency mode is handled; trigger-based PM raises rather than
    guessing at `RapTriggerObj`'s firing semantics.
    """
    if not block.uses_pm:
        return False, pm_time_left, fail_time_left
    if block.pm_triggered:
        # Raptor 7 gates both the `PMTimeLeft` refill and the
        # race comparison on `usesPM AND (NOT pmTriggered)`. A triggered-mode
        # block's `PMTimeLeft` is not managed here; PM becomes due only via
        # `ControlPMtriggers`'s external wake (`"Triggered_PM_Due"`,
        # dispatched in `block_run_process`).
        return False, pm_time_left, fail_time_left
    # Raptor 7: both the refill and the race are gated on
    # `(NOT deferPM) AND (NOT WaitingForPrereqs)` -- a block whose PM is
    # phase-deferred, or whose deferred PM prereq request is outstanding,
    # just runs toward its failure.
    _deferred = block.run_state.pm_prerequisites_deferred
    if block.run_state.defer_pm or block.run_state.waiting_for_pm_prerequisites or (_deferred is not None and _deferred()):
        return False, pm_time_left, fail_time_left
    if pm_time_left <= 0.0:
        pm_time_left = block.pm_freq
    # Source divides `FailTimeLeft` by `CombinedStress` before the race
    # and compares `PMTimeLeft < FailTimeLeft/stress`;
    # `PMTimeLeft` itself is never stress-scaled. This port keeps
    # `clock.fail_time_left` in RAW units, so the comparison divides here, and the
    # PM-win slot returns the raw-equivalent (`clock.pm_time_left * stress`) so the
    # caller's wait and interrupt decrement stay correct for a PM countdown.
    _stress = stress if stress > 0.0 else 1.0
    if pm_time_left < fail_time_left / _stress:
        # PM wins. Source does not zero `PMTimeLeft` here -- it copies it
        # into a local (`TimeToMaint:=PMTimeLeft; failMaint:=FALSE`)
        # and leaves the field live so the wait's `ON INTERRUPT`
        # arm can decrement it. The reset belongs to PM completion,
        # so the caller sets `clock.pm_time_left = 0.0` once the PM has run.
        # Returning the remaining countdown (rather than 0.0) is required when
        # the wait is interrupted: the caller's `clock.pm_time_left -= elapsed` must
        # decrement a live clock, not an already-zero one.
        return True, pm_time_left, pm_time_left * _stress
    return False, pm_time_left, fail_time_left


#: Stamps `RBDBlock.run_state.pm_defer_order`: the order blocks join `pmDeferGroup`.
_DEFER_SEQ = itertools.count(1)


@dataclass
class FailClock:
    """A block's life and PM clocks, kept across waits and turns."""

    #: Source's `FailTimeLeft`: the rest of the current life, kept across an
    #: interrupted fail-wait. `None` means draw a fresh life.
    fail_time_left: float | None = None
    #: Source's `PMTimeLeft`, set from `pmFreq - pmStagger` at the start.
    pm_time_left: float = 0.0
    # The fields below are always assigned before use, so they have no
    # default: a path that read one unassigned would fail loudly, as the
    # original generator's unbound locals did.
    #: Source's `OperatingTime`, logged with `Failed` and read by OBPM.
    operating_time: float = field(init=False)
    #: The raw life drawn when the PM race was run (`_pm_race`).
    race_raw_life: float | None = field(init=False)
    #: The combined stress of the current wait.
    stress: float = field(init=False)
    #: The length of the current fail-wait.
    wait_duration: float = field(init=False)


@dataclass
class PmRequest:
    """A preventive maintenance request, from the PM race through
    `ObtainPMprereqs` to `PerformPM`."""

    #: The PM race was won this turn (`DetermineNewState("PM is due")`).
    due: bool = field(init=False)
    #: `InterruptReason` logged with `PM_is_due`:
    #: "Scheduled_PM" for the race, "Triggered_PM_Due" from `ControlPMtriggers`.
    reason: InterruptReason | str = field(init=False)
    #: Carry a same-instant PM-race win across one benign-interrupt turn.
    #: Not derived from `clock.fail_time_left is not None`, which stays true
    #: across many later, unrelated benign interrupts.
    benign_continue_pending: bool = False
    #: The PM's prerequisites are in hand.
    prereqs_secured: bool = False
    #: Outstanding asynchronous PM resource requests (source's separate
    #: `ObtainPMprereqs` process, after a pmReqDefer), reconciled by
    #: `BlockProcess._claim_pm_grant` against the pool's queue count. A count,
    #: not a flag: a second deferral can register before the first is picked
    #: up. Kept across turns, so a request can span many.
    resource_pending: int = 0
    #: Outstanding asynchronous PM spare requests, reconciled the same way.
    #: Separate from `resource_pending` so a landed spare is not taken for
    #: "prereqs secured": source's `ObtainPMprereqs` takes the spare, then
    #: still waits for the resource.
    spare_pending: int = 0
    #: How many of those spare requests ordered an emergency spare (`emerSpareOrdered`):
    #: `DecNumEmerOrdered` runs when such a request's grant lands. A request cancelled by an
    #: interrupt (`ON INTERRUPT: Cancel; RETURN`) never decrements, so its order stays counted.
    spare_emer_pending: int = 0
    #: The next PM entry follows an async spare pickup, so it must not re-log
    #: `PM_is_due` (set by `BlockProcess._credit_deferred_pm_spare`).
    spare_pickup: bool = False
    #: This PM pass is the continuation after an async spare pickup, so it
    #: logs no `pmReqDefer` line (source gates it on `NOT FailWait`).
    spare_pickup_pass: bool = False


@dataclass
class HopFlags:
    """Which zero-length boundary (a stand-in for a separate TELL process's
    start) has already been crossed, so it is not crossed twice."""

    #: A `Run;` boundary was crossed this turn (set at each maintenance
    #: completion, consumed by the `Too_many_on` hop).
    run_hop_taken: bool = False
    #: The `PerformPM` hand-back boundary was crossed on this PM attempt.
    pm_handback_taken: bool = False
    #: This PM attempt's resource arrived by an asynchronous grant (a
    #: `Pool_Available` pickup at an ordinary wait), not in the PM's own turn.
    pm_async_pickup: bool = False
    #: A mission-branch pmMisDefer give-back from an async Give resume left
    #: Run's own wait untouched: re-entering it must not re-log it.
    quiet_rewait: bool = False
    #: A lost "Obtained_PM_Prereqs" wake was overwritten by
    #: "Induced_Failure": Run fails at once.
    induced_now: bool = False
    #: The hand-back started from PMhold (source's `IF opStatus=PMhold`).
    handback_from_pmhold: bool = False
    #: The first turn after the kickoff barrier. Its standby-stress hop is
    #: skipped: source's kickoff `CheckColds` demotes before any `Run` starts.
    first_run_pass: bool = False


@dataclass
class DownStart:
    """A block that `GetStartCond` put down at kickoff."""

    #: Source's `startVal`: the repair time already drawn, used by the first
    #: turn's fork in place of a fresh draw.
    repair_time: float | None = None
    #: The "preLDT" down-start's partial pre-LDT wait (source's
    #: `firstPreLDT`), taken once at the pre-LDT gate instead of a draw.
    partial_pre_ldt: float | None = None


class BlockProcess:
    """An ordinary block's whole life. `run()` is a `core.scheduler.Process`
    (`yield duration` where the source has `WAIT DURATION duration; END
    WAIT;`): register it with `scheduler.schedule_process(...)`, then call
    `scheduler.run_until(...)`.

    The constructor's arguments are the block's environment (collaborators,
    pools, callbacks and switches), kept as attributes. State that persists
    between waits lives on the instance, grouped into records (`clock`,
    `pm`, `hops`, `down_start`). The work is done by one sub-generator per
    MODSIM method or step of one (`_run_turn` for `Run`, `_repair` for
    `Repair`, `_obtain_pm_prereqs` and `_perform_pm`, ...); see
    `docs/engine-design/block-lifecycle.adoc` for the map.

    Appends log lines to the caller-supplied `logs` list rather than returning
    them: a generator that both yields and accumulates output across many
    suspensions has no clean single return value, and a shared mutable list
    matches how the source treats `WriteEvents` as a side effect.

    `on_transition`: called as `(old_op_status, deltas, sim_time, wake_ids,
    wake_kinds, top_level)`
    after every `change_block_state` call, supplying the deltas that drive
    `dependency.py`/`cascade.py`'s node/system/dependency propagation.
    `old_op_status` is the block's `op_status` immediately before the call.
    `wake_ids` lists other blocks a resource-pool `release()` just made
    eligible to resume, so the caller can interrupt them at the right point in
    its own cascade pipeline. `top_level` is forwarded to `_Cascade.apply` to
    suppress a system-status check that a follow-up Idle transition will
    cover. `orchestrator.py` is the only caller that passes an
    `on_transition`; leaving it `None` discards the deltas.

    `block.uses_pm`: `_pm_race` decides PM-vs-failure at the top of every
    fresh fail-wait draw, and the PM branch ports `DetermineNewState("PM is
    due")` -> `ObtainPMprereqs` -> `PerformPM`, including PM that needs a
    spare or resource (held or deferred) and trigger-driven PM.

    `resource_pool`: meaningful when `block.resource_type_count==1`, a shared
    RESOURCE_DATA pool needed to perform repair, orthogonal to `sparing_type`
    (see `ResourceQueue`'s module docstring). This is this block's resolved
    pool (looked up by `block.resource_pool_name`); `None` for a block that needs none.
    Waking other blocks after a `release()` is threaded through
    `on_transition`'s `wake_ids` argument so the caller can place the
    interrupt in its own cascade pipeline.

    `spare_pool`/`spare_pool_config`/`spawn_order_a_spare`: meaningful when
    `block.sparing_type==SPARE_POOL`, a shared SPARE_POOL_DATA pool consumed
    permanently on failure (not returned, unlike `resource_pool`). `spare_pool`
    is the runtime `ResourceQueue` for this block's pool (resolved by
    `block.pool_name`); `spare_pool_config` is the pool's static config;
    `spawn_order_a_spare()` schedules a new `order_a_spare_process` (ESO);
    `spawn_order_stock(quantity)` schedules `order_stock_process` (SLO),
    required whenever `spare_pool_config.stock_level_ordering` is set.

    `wake_block`: meaningful when `block.sparing_type==CUSTOM` with RSO and/or
    ESO enabled. A Custom block's `custom_spares` queue is private to it, so
    this function constructs it and spawns its RSO/ESO background processes
    internally; `wake_block` interrupts the block once one of them grants a
    spare (`(block_id, reason) -> None`, same shape as `orchestrator.py`'s own
    closure).

    `all_blocks`/`end_node`/`own_node`: forwarded to
    `dependency.repair_completion_status` when repair finishes, to decide
    Running vs Idle for the final transition. All omitted is fine for a block
    with no covered dependency. `check_system_status`: called only when
    `repair_completion_status` returns `needs_recheck=True` but the
    post-cascade recheck finds no follow-up Idle transition is needed, firing
    the one combined check that was suppressed on the RUNNING transition.
    """

    def __init__(
        self,
        block: RBDBlock,
        stats: BlockStatistics,
        rng_fail: RandomSource,
        rng_repair: RandomSource,
        env: BlockEnv,
        *,
        pools: BlockPools | None = None,
        on_transition: Callable[[BlockStatus, StateDeltas, float, list[int], dict[int, str], bool], None] | None = None,
        own_node: RBDNode | None = None,
        initial_fail_time_left: float | None = None,
        initial_start_cond: StartCond | None = None,
    ) -> None:
        pools = pools if pools is not None else BlockPools()
        self.block = block
        self.stats = stats
        self.rng_fail = rng_fail
        self.rng_repair = rng_repair
        self.on_transition = on_transition
        self.own_node = own_node
        self.initial_fail_time_left = initial_fail_time_left
        self.initial_start_cond = initial_start_cond
        # The run's shared environment (`BlockEnv`), unpacked onto the
        # instance so each step reads `self.scheduler`, `self.logs`, ...
        self.env = env
        self.scheduler = env.scheduler
        self.run_length = env.run_length
        self.logs = env.logs
        self.cost_analysis = env.cost_analysis
        self.weak_analysis = env.weak_analysis
        self.events_file = env.events_file
        self.startup_failures = env.startup_failures
        self.phase_defer_enabled = env.phase_defer_enabled
        self.rng_sys10 = env.rng_sys10
        self.system_state = env.system_state
        self.in_mission_fn = env.in_mission_fn
        self.wake_block = env.wake_block
        self.all_blocks = env.all_blocks
        self.all_events = env.all_events
        self.end_node = env.end_node
        self.nodes_by_id = env.nodes_by_id
        self.links_by_id = env.links_by_id
        self.custom_spares_registry = env.custom_spares_registry
        self.cascade = env.cascade
        self.observer = env.observer
        # This block's pools (`BlockPools`).
        self.resource_pool = pools.resource_pool
        self.pool_stats = pools.pool_stats
        self.spare_pool = pools.spare_pool
        self.spare_pool_config = pools.spare_pool_config
        self.spawn_order_a_spare = pools.spawn_order_a_spare
        self.spawn_order_stock = pools.spawn_order_stock
        # State kept across waits and turns, grouped by concern (see the
        # record classes above).
        self.custom_spares: ResourceQueue | None = None
        self.clock = FailClock()
        self.pm = PmRequest()
        self.hops = HopFlags()
        #: Set by `_fail_wait` when the fail timer itself ran out (not an interrupt).
        self._natural_expiry = False
        self.down_start = DownStart()
        self._refuse_unported_setup()

    def _take_pm_grant(self, kind: str | None) -> None:
        """Take a deferred PM request's grant that landed at a wait (see
        `_claim_pm_grant`). A resource completes the PM's prerequisites:
        the deferred request's background `WAIT FOR ... TO Give` just
        completed, with the same per-use charge as the
        synchronous PM borrow. Anything else is a spare, which is not
        "prereqs secured": source goes on to `WAIT FOR PooledSpares[resPoolNum]
        TO Give` with the spare in hand. It is marked obtained
        (source's `spareObtained:=TRUE`) so the re-entered PM path
        skips the spare step and goes on to the resource request.
        """
        if kind == "resource":
            if self.cost_analysis and self.pool_stats is not None:
                self.pool_stats.es_cost += _res_fixed_per_use(self.block) * float(self.block.pm_resources_needed)
            self.pm.prereqs_secured = True
        else:
            self._credit_deferred_pm_spare(self.scheduler.time)

    def _credit_deferred_pm_spare(self, now: float) -> None:
        """Tail Raptor 7 runs when its PM spare wait returns:
        count the unit, mark it obtained, run the
        SLO re-order, then log `Spare_Arrived`.

        This port abandons a PM attempt on a shortage and retries once the
        spare lands, so the tail is replayed at pickup instead of falling out
        of a resumed wait. Custom sparing replays the same tail against the
        block's private queue.
        """
        self.pm.spare_pickup = True
        self.block.run_state.spares_used += 1
        self.block.run_state.spare_obtained = True
        if self.custom_spares is not None:
            if self.block.stock_level_ordering:
                if self.custom_spares.resources == self.block.slo_order_level:
                    self._spawn_custom_stock_order(self.block.slo_new_spares)
                elif self.custom_spares.resources <= self.block.slo_order_level - self.block.slo_new_spares:
                    self._spawn_custom_stock_order(1)
        elif (self.spare_pool is not None and self.spare_pool_config is not None
                and self.spare_pool_config.stock_level_ordering
                and self.spawn_order_stock is not None):
            if self.spare_pool.resources == self.spare_pool_config.slo_order_level:
                self.spawn_order_stock(self.spare_pool_config.slo_new_spares)
            elif (self.spare_pool.resources
                  <= self.spare_pool_config.slo_order_level - self.spare_pool_config.slo_new_spares):
                self.spawn_order_stock(1)
        if self.events_file:
            self.logs.append(format_event_log_line(
                now, "Block", self.block.name, "Sparing", "Spare_Arrived",
            ))

    def _observe_wake(self, site: WaitSite, resume: object) -> None:
        """Tell the run's `StateObserver`, if any, what ended the wait at
        `site`. Does nothing in an ordinary run."""
        if self.observer is not None:
            self.observer.on_wake(self.block, site, resume if isinstance(resume, Wake) else None)

    def _transition(self, new_status, new_active, comment, sim_time, *, cascade=True, wake_ids=None, wake_kinds=None,
                     top_level=True, **kwargs):
        old_op_status = self.block.op_status
        change_logs, deltas = change_block_state(
            self.block, self.stats, new_status, new_active, comment, sim_time,
            cost_analysis=self.cost_analysis, weak_analysis=self.weak_analysis,
            events_file=self.events_file, pool_stats=self.pool_stats,
            in_mission=self.in_mission_fn() if self.in_mission_fn is not None else False,
            **kwargs,
        )
        self.logs.extend(change_logs)
        if self.observer is not None:
            self.observer.on_transition(self.block, old_op_status, new_status, comment)
        if cascade and self.on_transition is not None:
            self.on_transition(old_op_status, deltas, sim_time, wake_ids or [], wake_kinds or {}, top_level)

    def _apply_maintenance_completed(self, sim_time, wake_ids, wake_kinds=None):
        # `spareObtained:=FALSE` (Raptor 7, before this handler's
        # `DetermineNewState("Maintenance Completed")` call) -- whatever spare
        # this maintenance held is consumed, so the next one must obtain its
        # own. See the PM-side acquisition's `spare_obtained` guard.
        self.block.run_state.spare_obtained = False
        # Port of `DetermineNewState`'s `"Maintenance Completed"` handler
        # (Raptor 7; see `dependency.repair_completion_status`
        # for the full scope) -- both `Repair`'s `"Repaired"` and `PerformPM`'s
        # `"PM_complete"` call this same source method, so it runs identically
        # from both.
        #
        # A block finishing maintenance while what it depends on (block-to-block
        # or sysDepend/the model's End node) is still down goes straight to Idle
        # with an empty comment, not back to Running. For a block with no
        # covered dependency this is always a single RUNNING transition.
        # `wake_ids` applies only to the first transition -- the source's single
        # TakeBack/release point happens once regardless of how many follow.
        # Resolve the block's own dependency target node -- see
        # `dependency.resolve_dependency_node_id` for the three-way mapping.
        # `nodes_by_id` is needed only for the positive-DepNum+RBDNode case;
        # otherwise fall back to `end_node`/`own_node`, already resolved by the
        # caller.
        # Port of every `ChangeBlockState(...,activeStatus,...)` call in the
        # source's handler passing the block's current `activeStatus` field
        # unchanged, never a literal `Active` -- `change_block_state`'s
        # log-line formatting shows this (not the op_status text) whenever it
        # is Cut/Linked. Captured before any transition below mutates it.
        current_active_status = self.block.active_status
        dep_node = self._dependency_node()
        # Port of DetermineNewState's own `returnFromMaint:=TRUE` at the top of
        # "Maintenance Completed" -- cleared again below,
        # either immediately (ordinary/`needs_recheck=False` case) or
        # conditionally (`needs_recheck=True`, where it stays True if the
        # recheck finds the dependency target still down).
        self.block.run_state.returning_from_maintenance = True
        recovery_status, needs_recheck = repair_completion_status(
            self.block, self.all_blocks or [], dep_node, self.own_node, all_events=self.all_events)
        # The final system-status check must be deferred whenever this
        # maintenance completion also queued a resource-pool wake
        # (`wake_ids`) -- the woken block's own resumption does its own
        # top_level check once it runs (matching `_Cascade.apply`'s
        # `top_level and not wake_ids` suppression). Raptor 7 orders the woken
        # block's lines first.
        defer_check = bool(wake_ids)
        # `ChangeBlockState` never runs `CheckColds`/`AnalyzeSystem` itself; the dispatch's own `CheckColds` comes
        # after `returnFromMaint:=FALSE`. A top-level transition here would flush the cold nodes
        # while `returning_from_maintenance` is still set, so a dependent that lands Standby (`Too_many_on`) would be
        # skipped by the resend that follows (`LadL6Full` run 46 t=5161.065367: real idles it, `Dependence`).
        # Where nothing else defers the check, run it after the reset (below) instead.
        late_check = not needs_recheck and not defer_check and self.cascade is not None
        if recovery_status == BlockStatus.STANDBY:
            # Port of Raptor 7's Standby branches: a
            # depended-on BLOCK/NODE in Standby puts the completing block into
            # Standby too. `run_needs_respawn` is not set here -- unlike Idle,
            # Standby keeps `Run`'s process parked on the trigger/ZeroWait
            # site, so wakes have a live receiver.
            self._transition(
                BlockStatus.STANDBY, current_active_status, "", sim_time,
                wake_ids=wake_ids, wake_kinds=wake_kinds, top_level=not (defer_check or late_check),
            )
        elif recovery_status == BlockStatus.RUNNING:
            # Suppress the ordinary top_level system-status check when a
            # recheck or a deferred wake is pending -- one combined check must
            # fire after everything has settled, not early and not twice.
            self._transition(
                BlockStatus.RUNNING, current_active_status, "", sim_time, phase_stress=self.block.run_state.phase_stress,
                wake_ids=wake_ids, wake_kinds=wake_kinds,
                top_level=not (needs_recheck or defer_check or late_check),
            )
        else:
            # See `BlockRunState.run_needs_respawn`. `Repair`/`PerformPM` are
            # separate `TELL METHOD`s in source, so completing into Idle
            # (rather than Running) means `Run`'s process has already returned,
            # with no live receiver for a later `ControlPMtriggers` wake.
            self.block.run_state.run_needs_respawn = True
            self._transition(
                BlockStatus.IDLE, current_active_status, "", sim_time,
                wake_ids=wake_ids, wake_kinds=wake_kinds, top_level=not (defer_check or late_check),
            )

        # Port of Raptor 7's `ChangeBlockState(Running,
        # activeStatus,""); IF coldsAffected: CheckColds; END IF;` -- called
        # unconditionally right after the Running transition in the
        # `down_num<=1` special case (exactly `needs_recheck=True`),
        # regardless of `defer_check`/`wake_ids`, which only gate the separate
        # system-status check. Without this, a cold node this block's recovery
        # just marked affected is not flushed until a later top-level event.
        if needs_recheck and self.cascade is not None:
            self.cascade.flush_cold_changes(sim_time)

        # See repair_completion_status: the RUNNING transition above just ran
        # its own full cascade, so only now can we decide whether a follow-up
        # Idle transition is needed.
        if needs_recheck:
            self._recheck_after_repair(sim_time, dep_node, current_active_status, defer_check)
        else:
            # Port of Raptor 7's unconditional `returnFromMaint
            # :=FALSE`, reached for every other branch of the depType dispatch
            # (down_num>1, no dependency, block-to-block) -- net effect is
            # always False by the time the handler returns.
            self.block.run_state.returning_from_maintenance = False
            # Port of Raptor 7's trailing `IF coldsAffected:
            # CheckColds; END IF;` -- the second CheckColds in the
            # "Maintenance Completed" branch (the first is inside the
            # `downNum<=1` special case, ported above under `needs_recheck`).
            # Source's `Run;` here is synchronous, so the cold survey's
            # `SetSwitchTime` draw lands before the block's own post-repair
            # life redraw.
            if late_check and self.cascade is not None:
                self.cascade.check_system_status(sim_time)  # the flush, then the system-status check
            elif self.cascade is not None:
                self.cascade.flush_cold_changes(sim_time)

    def _dependency_node(self) -> RBDNode | None:
        """The node this block depends on (see
        `dependency.resolve_dependency_node_id` for the three-way mapping), or
        `None` for a block with no covered dependency. `nodes_by_id` is needed
        only for the positive-DepNum+RBDNode case; otherwise `end_node` /
        `own_node`, already resolved by the caller.
        """
        dep_node = None
        if self.own_node is not None:
            end_node_id = self.end_node.id if self.end_node is not None else None
            target_id = resolve_dependency_node_id(self.block, end_node_id, self.own_node.id)
            if target_id is not None:
                if self.nodes_by_id is not None:
                    dep_node = self.nodes_by_id.get(target_id)
                elif target_id == end_node_id:
                    dep_node = self.end_node
                elif target_id == self.own_node.id:
                    dep_node = self.own_node
        return dep_node

    def _recheck_after_repair(self, sim_time: float, dep_node: RBDNode | None,
                              current_active_status: ActiveStatus, defer_check: bool) -> None:
        """`DetermineNewState("Maintenance Completed")`'s `down_num<=1` arm
       : the RUNNING transition has run its own
        cascade, so decide whether the block goes back to Idle because the
        node it depends on is still down, then `ResendBackFlow` and the
        closing cold-standby / system-status check.
        """
        recheck_node = require(dep_node, "dependency node for the repair recheck")
        # Port of Raptor 7's `returnFromMaint:=FALSE`, hoisted
        # here (before the self-correct transition): the self-correct's
        # own ChangeBlockState(Idle) fires the outermost CheckColds flush,
        # and that flush's ResendBackFlow must see this block as ordinary
        # (rfm=False) so its special arm can re-dispatch it.
        #
        # 2026-09-23: only in source's ELSE arm (dep node NOT Down/PM).
        # When the node is still down, `returnFromMaint` stays
        # TRUE through `ResendBackFlow` -- whose re-idle arms require
        # `NOT returnFromMaint` -- and is cleared after it
        # (below). ReleaseProbe21 run 3 t=27.871198: BlockD (defIdle
        # FALSE, its dep node Cpath Down) stays Good in real; the
        # unconditional hoist let the resend idle it.
        if not needs_repair_idle_recheck(recheck_node):
            self.block.run_state.returning_from_maintenance = False
        # Port of Raptor 7's `prevDepState:="GoIdle"` -- set
        # inside both the "still down" and "recovered" arms, but only when
        # `defDepStateIdle` (the assignment sits inside each arm, not
        # gating entry to the `down_num<=1` special case).
        if self.block.def_dep_state_idle:
            self.block.run_state.previous_dependency_state = "GoIdle"
        if self.block.def_dep_state_idle and needs_repair_idle_recheck(recheck_node):
            # Same `run_needs_respawn` reasoning as the first-pass branch:
            # this is the "self-corrects, but still down" second
            # transition, which settles back on Idle, leaving `Run` without
            # a live receiver. Skip when the block is already Idle (a
            # CheckColds resend may have just idled it).
            if self.block.op_status is not BlockStatus.IDLE:
                self.block.run_state.run_needs_respawn = True
                # `top_level=False` (not `not defer_check`): source's
                # `ChangeBlockState` never auto-triggers CheckColds/
                # AnalyzeSystem -- both are separate explicit calls in the
                # surrounding `DetermineNewState` dispatch. This Idle
                # sub-transition has no CheckColds call of its own, so
                # letting its cascade auto-flush fire would resurvey a cold
                # node before the tail's explicit call runs.
                self._transition(BlockStatus.IDLE, current_active_status, "", sim_time, top_level=False)
        else:
            if not defer_check and self.cascade is not None:
                self.cascade.check_system_status(sim_time)

        # Port of ResendBackFlow's call site -- fires
        # unconditionally at the end of the `down_num<=1` branch,
        # regardless of which sub-branch above ran.
        if self.cascade is not None:
            self.cascade.resend_back_flow(sim_time)
        self.block.run_state.returning_from_maintenance = False  # 

        # Port of Raptor 7's second `IF coldsAffected:
        # CheckColds` -- at the end of the whole "Maintenance Completed"
        # dispatch, after ResendBackFlow, so dependence actions the resend
        # just took get their cold-standby node re-marked and drained in
        # the same turn. Unconditional because this port has no
        # `coldsAffected` global; `_flush_cold_changes` no-ops on an empty
        # group. Uses `check_system_status` (flush-then-check) rather than
        # the bare `flush_cold_changes`, since the self-correct-Idle branch
        # above gets no system-status check of its own.
        if self.cascade is not None:
            self.cascade.check_system_status(sim_time)

    def _spawn_custom_stock_order(self, quantity: int) -> None:
        # `RBDBlockObj.OrderStock` is a TELL METHOD on the BLOCK
        # not a PoolObj process -- block-owned.
        # Like every TELL it starts as its own selection at the tail of
        # the instant (`schedule_process`), never inline in the spawning
        # turn. `ObtainPMprereqs` spawns OrderStock then PerformPM, and
        # the two start in creation order, so the OrderStock line precedes
        # `beginning_PM`.
        self.scheduler.schedule_process(order_custom_stock_process(
            self.block, self.custom_spares, self.scheduler, require(self.wake_block, "wake_block"), self.logs, quantity,
            events_file=self.events_file, block_stats=self.stats if self.cost_analysis else None,
        ), block_id=self.block.id)

    def _startup_draw(self, reason: InterruptReason | str | None, ftl: float | None) -> float | None:
        # startUpFailures draws at Run's ZeroWait site (Raptor 7-
        # 5363): a Phase_Change (positive phase
        # value), Switched_On or Dependence_SB_turnOn wake lets a
        # low-initialCost block draw Uniform(0,1) from SysStream[10] and
        # force `FailTimeLeft:=0.000001`. Gated on the model-wide
        # StartUpFaiLures trigger flag; blocks with initialCost>=1.0 skip
        # the branch including the draw. A forced value suppresses the
        # fresh redraw at the loop top (source's non-zero FailTimeLeft).
        if not (self.startup_failures and self.rng_sys10 is not None and self.block.initial_cost < 1.0
                and reason in (InterruptReason.PHASE_CHANGE, InterruptReason.SWITCHED_ON,
                               InterruptReason.DEPENDENCE_SB_TURN_ON)):
            return ftl
        if reason == InterruptReason.PHASE_CHANGE and not (
            self.block.phase_value and self.block.phase_value[
                (self.system_state.current_phase if self.system_state is not None else 1) - 1
            ] > 0.0
        ):
            return ftl
        forced = startup_failure_redraw(self.block, self.rng_sys10, ftl, self.cost_analysis, self.stats)
        return forced if forced is not None else ftl

    def _pm_grant_landed(self) -> str | None:
        """Which async PM-prereq grant has landed -- "resource", "spare", or
        None. PURE: reads the queues, mutates nothing.

        The single owner of this rule. It used to exist twice: here, and
        hand-inlined in `_idle_wait` as `pm_grant_landed`. The inlined copy
        omitted `custom_spares` entirely, so a Custom block whose deferred
        PM spare request had been granted out of its private queue was
        never reconciled -- in exactly the same-instant case the inlined
        comment describes at length, which is the case that most needs it.

        Compares counts, not `is_waiting()`'s binary presence check (see
        `PmRequest.resource_pending`): more than one request can be
        outstanding. A Custom block's deferred PM spare request lives on
        the block's own queue, so `spare_pending` is reconciled against
        `custom_spares` whenever a private queue exists.
        """
        res_q = self.resource_pool.count_waiting(self.block.id) if self.resource_pool is not None else 0
        custom_q = self.custom_spares.count_waiting(self.block.id) if self.custom_spares is not None else 0
        spare_q = self.spare_pool.count_waiting(self.block.id) if self.spare_pool is not None else 0
        if self.pm.resource_pending and self.pm.resource_pending > res_q:
            return "resource"
        if self.pm.spare_pending and self.pm.spare_pending > (custom_q or spare_q):
            return "spare"
        return None

    def _claim_pm_grant(self) -> str | None:
        """Reconcile one landed async PM-prereq grant: the mutating half of
        `_pm_grant_landed`, decrementing the matching counter. Callers that
        only need to know WHETHER one landed must use the pure predicate.
        """
        kind = self._pm_grant_landed()
        if kind == "resource":
            self.pm.resource_pending -= 1
            return "resource"
        if kind == "spare":
            self.pm.spare_pending -= 1
            if self.pm.spare_emer_pending and self.spare_pool is not None:
                self.pm.spare_emer_pending -= 1
                self.spare_pool.num_emer_ordered -= 1
            return "spare"
        return None

    def _on_fail_wait_wake(self, resume: Wake, wait_start: float) -> _Wake:
        """Source's `ON INTERRUPT` arm of `Run`'s fail-wait
        (`WAIT DURATION TimeToMaint ... ON INTERRUPT`):
        account for the time waited, then act on the interrupt reason.

        Shared by the two fail-wait sites. They differ only as the table
        below says; everything else is one copy.

        [cols="1,2,2"]
        |===
        | Difference | ordinary fail-wait | beyond-horizon wait
        | Which wait | the life ends inside the run | the life ends after the run, so the wait is to the end of the run
        | After `_Wake.PROCEED` | falls through to `pm.due` / failure handling | also skips the ordinary wait (the caller sets `skip_ordinary_wait`)
        | `Induced_Failure` | the failure tail records the component failure | the same (confirmed against real, see below)
        |===

        The beyond-horizon copy used to record the component failure here as
        well, so one induced failure counted twice. Raptor 7 counts it once
        (`DepFailBeyondHorizonProbe`: 24 component failures, not 36), so the
        call is gone and both waits leave the count to the failure tail.
        """
        block = self.block
        scheduler = self.scheduler
        elapsed = scheduler.time - wait_start
        # Not source's ON INTERRUPT rescale
        # (`FailTimeLeft:=(FailTimeLeft-(SimTime-TimeHack))*CombinedStress`,
        # Raptor 7): that works only because source mutates
        # `FailTimeLeft` to the divided value right before waiting.
        # This port keeps `clock.fail_time_left` in RAW units and computes
        # `clock.fail_time_left / stress` as a local, so the rate-based
        # decrement below is what is dimensionally consistent. The literal
        # rescale breaks a cold-standby fixture (multiplying the whole
        # remaining countdown by a small stress on each toggle), so it is not
        # used.
        self.clock.fail_time_left = require(self.clock.fail_time_left, "fail_time_left") - elapsed * self.clock.stress
        # Source's `FailTimeLeft` has no separate "unset" sentinel -- literal
        # 0.0 (a countdown interrupted at exactly its expiry, e.g. a PM trigger
        # landing the same moment a fail draw would complete) already means
        # "needs a fresh redraw next time round" (`IF FailTimeLeft=0.0 THEN
        # <redraw>`, Raptor 7), same as this port's `None`. Normalize
        # here so downstream `is None` checks treat the two identically.
        if self.clock.fail_time_left <= 0.0:
            self.clock.fail_time_left = None
        self.clock.operating_time += elapsed
        if block.uses_pm:
            self.clock.pm_time_left -= elapsed
        if self.clock.race_raw_life is not None:
            # The whole life a winning PM race parked keeps burning while the
            # PM countdown is interrupted (source decrements `FailTimeLeft` at
            # every interrupt, Raptor 7); otherwise a PM that
            # is later skipped restores a life that ignores the waits before
            # this one (ReleaseProbe46 run 17: a dependency idle mid-race).
            self.clock.race_raw_life -= elapsed * self.clock.stress
        reason = resume.reason
        # Port of `InterruptReason:=""` (Raptor 7, unconditional at
        # the end of `Run`'s `WHILE returnToTop` loop): every wake-site handler
        # chain must consume and wipe the object field. A stale
        # "Induced_Failure" left by a sibling site's delivery can otherwise
        # suppress `BackFlowDependencies`'s `returnFromMaint` recovery branch
        # later (see `dependency.py`).
        block.run_state.interrupt_reason = ""
        # Ground-truth grant check -- see `_idle_wait`'s copy for the
        # staling-race explanation (`ResourceQueue.is_waiting`, `pool.py`).
        # Checked before the `reason in (...)` catch-all so a staled
        # "Dependence_All_Up" does not swallow a genuine same-instant grant.
        # Compares counts.
        granted_kind = self._claim_pm_grant()
        if granted_kind is not None:
            self._take_pm_grant(granted_kind)
            self.pm.due = True
            return _Wake.PROCEED
        if reason in STATE_ALREADY_APPLIED:
            # "Dependence_All_Up" reaching this non-Idle wait site is a
            # stale/duplicate signal -- it is only meaningful at the Idle-wait
            # loop's own `WHILE opStatus=Idle` check, which already consumed
            # the first such interrupt. A second can still be queued (e.g.
            # cold_standby's prep-phase promotion racing the ordinary sysDepend
            # dispatch for the same block). "Phase_Change" is the same shape:
            # `phasing.conduct_phase_change` already called
            # `change_block_state` before sending this interrupt, so the next
            # turn's `combined_stress` recomputes against the new phase.
            # `pm.benign_continue_pending`: this is the one-shot round trip a
            # same-instant PM-race win must survive.
            self.pm.benign_continue_pending = True
            return _Wake.LOOP
        if reason == InterruptReason.OBPM:
            # Port of Raptor 7's two-branch handler.
            # Over threshold: `returnFromMaint:=TRUE; DetermineNewState("PM is
            # due")` -- which logs "PM_is_due" with the current
            # InterruptReason ("OBPM") and calls ObtainPMprereqs -> PerformPM.
            # Under threshold: `returnFromMaint:=FALSE; IF defDepStateIdle:
            # ChangeBlockState(Idle,...,"Dependence"); returnToTop:=TRUE`.
            if _obpm_over_threshold(block, self.clock.operating_time):
                block.run_state.returning_from_maintenance = True
                self.pm.due = True
                self.pm.reason = InterruptReason.OBPM
                return _Wake.PROCEED
            block.run_state.returning_from_maintenance = False
            if block.def_dep_state_idle:
                self._transition(BlockStatus.IDLE, block.active_status, "Dependence", scheduler.time)
            # Same "just continue" shape as the benign-reason branch above
            # (source's `returnToTop:=TRUE`) -- a currently-winning PM race
            # must survive this bounce too.
            self.pm.benign_continue_pending = True
            return _Wake.LOOP
        if reason == InterruptReason.INDUCED_FAILURE:
            # Port of Raptor 7's unconditional
            # `FailTimeLeft:=0.0; INC(FailureCounter); DetermineNewState("Block
            # Has Failed")` -- a direct, synchronous method call in source,
            # with no `WAIT`, overriding whatever `pm.due` the original draw's
            # PM race decided. Modeling it with a real zero-length `yield`
            # would add a scheduler round-trip source never has, and a
            # same-instant deferred system-status check could then slot in
            # between this block's interrupt delivery and its resumption,
            # seeing a picture that does not yet include this failure.
            # `clock.operating_time` already holds how much has elapsed.
            self.clock.fail_time_left = None
            self.pm.due = False
            if block.uses_pm and not block.pm_fail_reset:
                # Source takes the elapsed time off `PMTimeLeft` TWICE for an induced failure: once in the arm's
                # own bookkeeping (ported above) and again in the `Induced_Failure` branch 
                # unless `pmFailReset` zeroes it. `LadL8B` run 18: PMFreq 400, stagger 75, induced at 310.33 leaves
                # 325 - 2*310.33 < 0, so the next race refills to a full 400 (PM at 324.88+400, not 324.88+14.67).
                self.clock.pm_time_left -= elapsed
            return _Wake.PROCEED
        if reason in (InterruptReason.TRIGGERED_PM_DUE, InterruptReason.START_DEFERRED_PM):
            # Port of `ControlPMtriggers`'s wake (Raptor 7,
            # `InterruptReason:="Triggered_PM_Due"`) -> `DetermineNewState("PM
            # is due")`, called directly with no
            # intermediate wait, and the phase-change machinery's
            # `Start_Deferred_PM` release wake, same dispatch.
            # No zero-length re-wait: it would re-enter the scheduler and could
            # be raced by another same-instant interrupt.
            # `clock.fail_time_left` stays at its paused value, not consumed
            # (source's `FailTimeLeft` stays intact across a PM interrupt,
            # Raptor 7).
            self.pm.due = True
            self.pm.reason = reason
            return _Wake.PROCEED
        # The deferred-PM-resource pickup is handled by the ground-truth
        # grant check ahead of this chain, not by a "Pool_Available" branch.
        # A "Pool_Available" that is not a landed PM grant is unexpected.
        raise UnexpectedWake(resume)

    def _on_pm_boundary_wake(self, resume: Wake, *, handback: bool) -> None:
        """A wake inside one of the PM path's zero-length TELL boundaries,
        while `waiting_for_pm_prerequisites` is set.

        Source's `ObtainPMprereqs` is a separate process from `Run`, so a
        Run-targeted interrupt does not abandon it: most reasons are pure
        notifications here and the PM carries on. (While `waiting_for_pm_prerequisites`
        is set, `orchestrator._Cascade._induce_dependency` refuses
        dependency deliveries, as source does, so several of these arms are
        not reached by any captured model; they are kept as ported.)

        Shared by the two boundaries. They differ only as the table says.

        [cols="1,2,2"]
        |===
        | Difference | TELL boundary (`handback=False`) | hand-back (`handback=True`)
        | Where | `DetermineNewState("PM is due")` -> `ObtainPMprereqs` | after the prerequisites are obtained, before `PerformPM`
        | `Start_Deferred_PM` | a notification | unexpected
        | `Pool_Available` with token `"pm"` | unexpected | an async PM-prerequisite grant landing in the hop: reconciled by `BlockProcess._claim_pm_grant`
        |===
        """
        block = self.block
        reason = resume.reason
        if reason in STATE_ALREADY_APPLIED:
            return  # PM dispatch is independent of Run's own op_status
        if reason == InterruptReason.TRIGGERED_PM_DUE or (
                reason == InterruptReason.START_DEFERRED_PM and not handback):
            return  # already pm.due: the PM proceeds
        if reason == InterruptReason.OBPM:
            # The ordinary fail-wait's "OBPM" handler,
            # landing in this window. The under-threshold case
            # has no observable effect here (no Idle transition, PM proceeds),
            # so only the over-threshold branch acts.
            if _obpm_over_threshold(block, self.clock.operating_time):
                block.run_state.returning_from_maintenance = True
                self.pm.due = True
                self.pm.reason = InterruptReason.OBPM
            else:
                block.run_state.returning_from_maintenance = False
            return
        if reason == InterruptReason.INDUCED_FAILURE:
            # `ObtainPMprereqs` is immune to a Run-targeted interrupt: a pure
            # notification, not a PM-abandoning event.
            return
        if handback and reason == InterruptReason.POOL_AVAILABLE and resume.token == "pm":
            # An async PM-prereq grant landing inside this zero-length
            # hand-back (reachable since the sticky-lateness ordering;
            # crashed ReleaseProbe27/35). Same handling as the repair wait's
            # own "pm" token: reconcile the outstanding PM counter and carry
            # on -- the grant itself is already applied by the pool.
            self._claim_pm_grant()
            return
        raise UnexpectedWake(resume)

    def run(self) -> Process:
        """The block's whole life, one `yield` per MODSIM `WAIT`: the
        kickoff barrier, the start conditions, then turns of `Run` and
        `Repair` until the life ends.

        Note on the `yield run_length - scheduler.time` before each "Done"
        in the sub-generators: they yield until exactly `run_length` before
        logging, so a caller running many processes gets that line in
        chronological order relative to other processes' earlier events.
        """
        block = self.block
        # Port of `ChangeBlockState(Running,Active,"Initialize")`
        #: the caller does this once per block before this
        # process is scheduled, matching source's earlier synchronous
        # `ASK block TO SetToInitValues()`. By the time the
        # first `yield` below runs, `block.op_status`/`active_status` are already
        # Running/Active.
        #
        # Barrier: yield once at SimTime=0.0 before this block computes its first
        # CombinedStress/wait, so cold-standby's kickoff redistribution
        # (`cold_standby.cold_logic_kickoff_process`) runs strictly between every
        # block's Initialize and its first stress computation, matching the real
        # event order. Relies on the insertion-order tie-break documented in
        # `scheduler.py`'s module docstring. A no-op for non-cold-standby
        # fixtures.
        #
        # The barrier is a port-only hop inside Raptor 7's single `Run` TELL start,
        # so its continuation keeps the head of the block's owner group
        # (`Wait.group_head`).
        resume = yield Wait(group_head=True)
        self._observe_wake(WaitSite.KICKOFF, resume)
        if resume is not None and not (
                isinstance(resume, Wake)
                and (resume.reason in KICKOFF_DEPENDENCE_WAKES
                     or resume.reason in (InterruptReason.INDUCED_FAILURE, InterruptReason.OBPM))):
            raise UnexpectedWake(resume)
        if isinstance(resume, Wake) and resume.reason == InterruptReason.OBPM:
            # The same `InitTimeZeroDeps` sends `GoToPM`: at t=0 the block has operated for
            # no time, so `OperatingTime > DepPMThreshold*failMean/100` is false and Run's OBPM arm 
            # only clears `returnFromMaint` and, for `defDepStateIdle`, idles the block.
            block.run_state.interrupt_reason = ""
            block.run_state.returning_from_maintenance = False
            if block.def_dep_state_idle:
                self._transition(BlockStatus.IDLE, block.active_status, "Dependence", self.scheduler.time, top_level=False)
        if isinstance(resume, Wake) and resume.reason == InterruptReason.INDUCED_FAILURE:
            # `InitTimeZeroDeps` (after every block's `Run` TELL) induces a failure in a
            # dependent whose target is down at t=0. That `Run` has by then started and reached its first wait, so
            # the block draws its life, logs its stress and fails at once: the barrier is the port's stand-in for the
            # start of `Run`, so the failure is taken at the first wait site below.
            block.run_state.interrupt_reason = ""
            self.hops.induced_now = True
        self.hops.first_run_pass = True
        self._start_custom_spares()

        start = self.initial_start_cond
        if start is not None and start.start_condition == "postLDT":
            if (yield from self._post_ldt_start(start)):
                return
        elif start is not None and start.start_condition != "Running":
            self._down_start(start)
        # Port of Initialize's `PMTimeLeft:=pmFreq-pmStagger`
        # -- the initial stagger.
        self.clock.pm_time_left = block.pm_freq - block.pm_stagger
        # Source's `WaitingForPrereqs` stays TRUE for as long as a `pmReqDefer`
        # request is outstanding (Raptor 7 ... : set on entry
        # to `ObtainPMprereqs`, cleared only after its `WAIT FOR ... TO Give`),
        # while `Run` carries on. `block.run_state.waiting_for_pm_prerequisites` covers only the
        # short TELL-boundary hops (the dependency-delivery gate relies on that),
        # so the outstanding-deferral half is published separately as a live
        # view of `pm.resource_pending` and `pm.spare_pending`. Read by
        # ControlPMtriggers' eligibility test.
        block.run_state.pm_prerequisites_deferred = lambda: (self.pm.resource_pending + self.pm.spare_pending) > 0
        while True:
            step = yield from self._run_turn()
            if step is _Turn.LOOP:
                continue
            if step is _Turn.STOP:
                return
            if (yield from self._repair(step.start_val, step.sim_time)):
                return

    def _refuse_unported_setup(self) -> None:
        """Refuse, when the process is built, a sparing set-up the port
        cannot simulate faithfully, rather than simulate it wrongly: checked
        once against this block's `BlockPools` and `BlockEnv`, not on the
        way through the run."""
        block = self.block
        if block.sparing_type not in (SparingType.INFINITE, SparingType.CUSTOM, SparingType.SPARE_POOL, SparingType.NONE):
            raise NotImplementedError(
                f"sparing_type={block.sparing_type!r} not ported -- only Infinite, Custom "
                "(no RSO/SLO replenishment), and SparePool are implemented; ColdPool would "
                "otherwise silently be simulated as Infinite, which is wrong, not just incomplete"
            )
        if block.sparing_type == SparingType.SPARE_POOL:
            if self.spare_pool is None or self.spare_pool_config is None:
                raise EngineInvariantError(
                    f"block requires spare pool {block.pool_name!r} but no spare_pool/"
                    "spare_pool_config was provided to block_run_process"
                )
            if self.spare_pool_config.stock_level_ordering and self.spawn_order_stock is None:
                raise EngineInvariantError(
                    f"spare pool {block.pool_name!r} has SLO enabled but no spawn_order_stock "
                    "was provided to block_run_process"
                )

        if block.sparing_type == SparingType.CUSTOM and (block.routine_spare_ordering or block.emergency_spare_ordering):
            if self.wake_block is None:
                raise EngineInvariantError(
                    f"block {block.name!r} has Custom-sparing RSO/ESO enabled but no wake_block "
                    "was provided to block_run_process"
                )

    def _start_custom_spares(self) -> None:
        """Source's `CustomSpares[seqNum]`: a per-block PRIVATE resource
        queue. Nothing outside this block touches its own Custom stock, so it
        is constructed fresh here each run, and RSO's background loop (if
        configured) is spawned bound to it."""
        block = self.block
        scheduler = self.scheduler
        self.custom_spares = None
        if block.sparing_type == SparingType.CUSTOM:
            # `time_fn`/`begin_run`: per-run stock statistics for the report's
            # Custom_Sparing_Information table (see `ResourceQueue.begin_run`).
            self.custom_spares = ResourceQueue(resources=block.init_stock, time_fn=lambda: scheduler.time)
            self.custom_spares.begin_run(block.init_stock, 0.0)
            if self.custom_spares_registry is not None:
                self.custom_spares_registry[block.id] = self.custom_spares
        if self.custom_spares is not None and block.routine_spare_ordering:
            # Source TELLs this block-owned `GenerateSpares` BEFORE the block's
            # Run and ControlPMtriggers (vs );
            # the port spawns it from inside Run, so it takes the head of the
            # block's owner group to keep that order (CUST_ALL: the custom-RSO
            # subscriber's ControlPMtriggers starts, and waits, last).
            scheduler.schedule_process(generate_custom_spares_process(
                block, self.custom_spares, scheduler, require(self.wake_block, "wake_block"), self.logs,
                events_file=self.events_file, block_stats=self.stats if self.cost_analysis else None,
            ), block_id=block.id, at_group_head=True)

    def _post_ldt_start(self, start: StartCond) -> Generator[float | Wait, Wake | None, bool]:
        """A block that `GetStartCond` put in post-LDT at kickoff: source's
        `Repair` `WHEN "postLDT"` arm. Wait the
        remaining post-LDT (`startVal`), log it, then the common tail --
        `DetermineNewState("Maintenance Completed")`.
        Confirmed by SimStartLdtProbe's Raptor 7 capture (2026-09-25).

        Returns True when the run ended during the wait.
        """
        block = self.block
        scheduler = self.scheduler
        # `Repair` opens with the SparePool early grab for
        # every startCond, including this one: PoolPostLdtStartProbe's real
        # capture shows the block taking a spare at t=0 (its second failure
        # finds the pool empty). Only the grab is ported here. Two cases
        # stay refused because no capture decides them: an empty pool at the
        # start, and a grab that triggers stock-level ordering.
        if block.sparing_type == SparingType.SPARE_POOL:
            pool = require(self.spare_pool, "spare_pool")
            if pool.resources <= 0:
                raise NotImplementedError(
                    f"block {block.name!r} starts the run in post-LDT with an empty spare pool "
                    "-- what Raptor 7's Repair does then (its spare-check ELSE arm) is not captured")
            spare = SpareRequest(held=False)
            yield from self._enter_repair(scheduler.time, spare, seam_pending=False)
            if spare.pool_slo:
                raise NotImplementedError(
                    f"block {block.name!r}: a post-LDT start's spare grab triggers stock-level "
                    "ordering, which is not captured")
        wait = start.start_val
        if scheduler.time + wait >= self.run_length:
            yield self.run_length - scheduler.time
            self._transition(BlockStatus.DONE, block.active_status, "End_sim", scheduler.time, cascade=False)
            return True
        yield wait
        if self.events_file:
            self.logs.append(format_event_log_line(
                scheduler.time, "Block", block.name, "End_Partial_post-LDT", f"Time={wait:.6f}"))
        self._apply_maintenance_completed(scheduler.time, [], {})
        block.run_state.defer_pm = False
        self.hops.run_hop_taken = True
        yield 0.0
        self.initial_fail_time_left = None
        return False

    def _down_start(self, start: StartCond) -> None:
        """A block that `GetStartCond` put down (repairing or in pre-LDT) at
        kickoff: its first turn goes straight to `Repair` with the repair
        time already drawn (`down_start.repair_time`)."""
        if start.start_condition == "preLDT":
            # Source's `WHEN "preLDT"` arm is the
            # "repairing" arm with a partial pre-LDT wait in front, so it
            # shares that path: `start_val` is the full repair time and the
            # pre-LDT gate waits `firstPreLDT` instead of drawing.
            self.down_start.partial_pre_ldt = start.first_pre_ldt
        self.down_start.repair_time = start.start_val
        # `GetStartCond` leaves `FailTimeLeft` at its top-of-method 0.0 reset
        # for every down-start arm (Raptor 7 -- only the "Running"
        # arms write it). That 0.0 is not a zero-length first life: the source
        # block reaches `Run` only after `Repair` finishes and draws a fresh
        # life there. Clearing it here keeps the loop's
        # `initial_fail_time_left` hand-off from firing an instant failure on
        # the first post-repair iteration.
        self.initial_fail_time_left = None

    def _draw_life(self) -> float:
        """Draw a new life and race it against the PM clock: source's
        `IF FailTimeLeft=0.0: DrawNumber(...)`
        followed by the `PMTimeLeft` comparison.

        Sets `pm.due`, `clock.pm_time_left` and `clock.fail_time_left` from
        `_pm_race` and returns the raw draw. `_pm_race` is a passthrough
        (`pm.due=False`, the draw unchanged) when `not block.uses_pm`. When
        PM wins, `clock.fail_time_left` holds the PM countdown instead, and
        the returned draw is the only copy of the life.
        `clock.fail_time_left` stays in RAW ("life-consumed") units: the
        wait divides it by the stress lazily, where source mutates
        `FailTimeLeft:=FailTimeLeft/CombinedStress`.

        The first draw of a run may already have been made: GetStartCond's
        kickoff draw (see `get_start_cond`) runs in the same per-block pass
        as every other block's, before this process starts, so it is
        consumed here rather than redrawn, to keep the shared fail stream's
        order. It applies once per run.
        """
        block = self.block
        if self.initial_fail_time_left is not None:
            raw_fail_draw = self.initial_fail_time_left
            self.initial_fail_time_left = None
        else:
            raw_fail_draw = self.rng_fail.draw_number(Distribution(block.fail_distro), block.fail_values)
        system_state = self.system_state
        _draw_stress = combined_stress(block, system_state.current_phase if system_state is not None else 1)
        if _draw_stress > 0.0:
            self.pm.due, self.clock.pm_time_left, self.clock.fail_time_left = _pm_race(
                block, self.clock.pm_time_left, raw_fail_draw, _draw_stress)
        else:
            # A zero-stress block parks in `ZeroWait` before the race, so the draw
            # stays the whole life; the pass that leaves the wait races it.
            self.pm.due, self.clock.fail_time_left = False, raw_fail_draw
        # Source never discards the draw: `FailTimeLeft` keeps the whole life
        # while the PM countdown runs, so a PM that
        # wins must be able to restore it (`_perform_pm`, pmRefresh off; a
        # deferred PM). Every redraw site records it here, not only the main
        # one: DepPmNoRefreshProbe's Raptor 7 capture has the block redrawing on
        # leaving a dependency idle, and the PM after that resumed the SAME life.
        self.clock.race_raw_life = raw_fail_draw if self.pm.due else None
        return raw_fail_draw

    def _idle_wait(self) -> Generator[float | Wait, Wake | None, IdleExit]:
        """Source's Idle / ZeroWait wait at the top of `Run`
        (`IF ((opStatus=Idle) ...): ZeroWait:=TRUE; WAIT FOR BlockTrigger[seqNum]
        TO Fire`, Raptor 7): park while the block
        is Idle, redraw its life when source would, and leave either when
        the block stops being Idle or when a wake routes it to PM.

        Returns an `IdleExit`. A block that is not Idle returns at once with
        `was_idle=False`.
        """
        block = self.block
        scheduler = self.scheduler
        was_idle = False
        idle_pm_due = False
        induced = False
        idle_pm_reason: str = InterruptReason.TRIGGERED_PM_DUE
        # Stage-(A) parking: source's `Run`
        # re-evaluates `IF ((opStatus=Idle) AND (FailTimeLeft=0.0))` at
        # the top of every `returnToTop` iteration, and the wake that
        # completes that pre-wait is benign -- its `ON INTERRUPT` clause
        # is empty. Flow falls through the `WAIT DURATION 0.0` hop into
        # the unconditional redraw, then re-parks at the
        # later ZeroWait site. So a block whose Idle
        # episode began via a Repair/PerformPM completion consumes its
        # first wake with no visible action except one fail-stream draw,
        # and only a second wake reaches the PM/recovery dispatch.
        # Re-evaluated per iteration, exactly like source's gate: after
        # the benign wake's redraw, `clock.fail_time_left` is set.
        #
        # `initial_fail_time_left` pending counts as FailTimeLeft<>0.0:
        # source's `GetStartCond` writes the start-condition life straight
        # into `FailTimeLeft` before `Run` begins, so a block Idle (e.g.
        # dependency-Cut) from t=0 skips this pre-wait entirely and its
        # first wake reaches the real dispatch below -- ReleaseProbe27's
        # `Triggered_PM_Due` at the trigger's t=0.000010 first fire.
        respawn_stage_a = self.clock.fail_time_left is None and self.initial_fail_time_left is None
        while block.op_status == BlockStatus.IDLE:
            was_idle = True
            # Port of Raptor 7's `ZeroWait:=TRUE; WAIT FOR
            # BlockTrigger[seqNum] TO Fire; ...; ZeroWait:=FALSE` -- see
            # `block.run_state.zero_wait` (`model.py`) and
            # `orchestrator._Cascade._induce_dependency`'s gate on it.
            block.run_state.zero_wait = True
            idle_wait_start = scheduler.time          # source's `TimeHack:=SimTime` at the ZeroWait site 
            resume = yield _FAR_FUTURE
            self._observe_wake(WaitSite.IDLE, resume)
            block.run_state.zero_wait = False
            # Normally "Dependence_All_Up" (the ordinary sysDepend/
            # locDepend recovery signal this loop exists for). Also
            # tolerates a stale "Switched_On"/"Switched_Off" queued before
            # the block went Idle by some other mechanism; the `while`
            # re-checks `op_status` each iteration, so a spurious wake is
            # harmless. "Triggered_PM_Due": source's ZeroWait site
            # is the same `WAIT` statement as
            # this Idle wait -- one combined guard -- so
            # `ControlPMtriggers`'s wake lands here whenever a block is
            # Idle-for-dependency when its PM trigger fires. Source
            # dispatches that reason to `DetermineNewState("PM is due")`
            # so break out without touching
            # `FailTimeLeft`/redraw and let the shared `if pm.due:`
            # handling pick it up.
            #
            # "Phase_Change": source's dispatch at this wait site is
            # `ELSIF (InterruptReason="Phase_Change"): ... returnToTop:=TRUE`
            # -- a pure re-check signal, no special
            # action. A phase-driven block/event's "ChangePhases" tail
            # interrupts whichever wait site the block is suspended at
            # when its phase transition fires, so a block already Idle for
            # dependency reasons can be phase-Cut/Active when its phase
            # changes.
            if not isinstance(resume, Wake):
                raise UnexpectedWake(resume)
            idle_reason = resume.reason
            if not (
                idle_reason in IDLE_WAKE_REASONS
                or (idle_reason == InterruptReason.POOL_AVAILABLE
                    and (self.pm.resource_pending or self.pm.spare_pending))
            ):
                raise UnexpectedWake(resume)
            # See the ordinary fail-wait site's copy of this reset for why.
            block.run_state.interrupt_reason = ""
            # Ground-truth grant check, not just `idle_reason ==
            # "Pool_Available"`: `apply()`'s `wake_ids` loop queues this
            # block's "Pool_Available" interrupt before the node cascade
            # that can queue a same-instant "Dependence_All_Up" for the
            # same handle -- generation-staling then discards the earlier
            # one, so `idle_reason` can be "Dependence_All_Up" even though
            # the grant happened. The queue reads the grant directly.
            #
            # This was hand-inlined here and omitted `custom_spares`, so a
            # Custom block granted out of its private queue read as "nothing
            # landed". `_pm_grant_landed` is the single owner and counts
            # requests rather than testing presence, because more than one
            # can be outstanding.
            pm_grant_landed = self._pm_grant_landed() is not None
            if respawn_stage_a:
                # Stage-(A) benign wake (see `respawn_stage_a`): the reason
                # is discarded here (source's empty `ON INTERRUPT`,
                # Raptor 7), then the wait's `WAIT DURATION
                # 0.0` hop pays a scheduler round-trip before the
                # unconditional redraw. Any other same-instant dispatch
                # that already flipped `op_status` out of IDLE still gets
                # its redraw first (source's redraw is unconditional on
                # opStatus), and the loop condition then ends the Idle
                # episode with a drawn `clock.fail_time_left`.
                respawn_stage_a = False
                # In this `WAIT DURATION 0.0` the block is in neither
                # FailWait nor ZeroWait: a same-instant dependency dispatch
                # changes its state but wakes nothing (literal mode).
                block.run_state.pm_tell_pending = True
                yield 0.0
                block.run_state.pm_tell_pending = False
                if self.clock.fail_time_left is None:
                    self._draw_life()
                    self.pm.reason = InterruptReason.SCHEDULED_PM
                    self.clock.operating_time = 0.0
                continue
            if idle_reason == InterruptReason.INDUCED_FAILURE:
                # The ZeroWait site's reason chain ends `ELSIF (InterruptReason="Induced_Failure"):
                # CompTimeToFail...; FailTimeLeft:=0.0; INC(FailureCounter); DetermineNewState("Block Has
                # Failed")`: an Idle (dependency) block, or a zero-stress standby
                # one, FAILS when a dependency induces it (`LadL5Full` run 7 t=4039.48, real 2026-10-01:
                # `S4B1 Failed Operating_time=84.33` from Idle). The earlier reading that this site has no such
                # branch was wrong; only the first site (stage A above) has an empty handler.
                self.clock.fail_time_left = None
                self.pm.due = False
                if block.uses_pm and not block.pm_fail_reset:
                    self.clock.pm_time_left -= scheduler.time - idle_wait_start   # 
                induced = True
                break
            if idle_reason in (InterruptReason.TRIGGERED_PM_DUE, InterruptReason.START_DEFERRED_PM):
                # `Start_Deferred_PM`: the phase-change machinery's
                # deferred-PM release wakes a
                # parked `pmMisDefer` block with this reason -- same
                # dispatch shape as `Triggered_PM_Due`
                # (`DetermineNewState("PM is due")`).
                idle_pm_reason = idle_reason
                idle_pm_due = True
                break
            elif idle_reason == InterruptReason.POOL_AVAILABLE or pm_grant_landed:
                # Same async deferred-PM-resource pickup as the fail-wait
                # sites, reached while this block is genuinely Idle for
                # dependency reasons when its deferred request is granted.
                # Routed like `Triggered_PM_Due` -- skip the redraw+stress
                # section and let the shared `if pm.due:` handling pick it
                # up. Claim exactly one landed grant (`-= 1`), leaving any
                # other outstanding registration tracked.
                self._take_pm_grant(self._claim_pm_grant())
                idle_pm_due = True
                break
            elif idle_reason == InterruptReason.OBPM:
                # Port of the ZeroWait/Idle site's OBPM branch
                #: over the threshold is a
                # dependency-induced PM (`DetermineNewState("PM is due")`);
                # under it is benign -- `returnFromMaint:=FALSE`, an Idle
                # refresh when `defDepStateIdle`, then the ordinary redraw.
                if _obpm_over_threshold(block, self.clock.operating_time):
                    block.run_state.returning_from_maintenance = True
                    idle_pm_reason = InterruptReason.OBPM
                    idle_pm_due = True
                    break
                block.run_state.returning_from_maintenance = False
                if block.def_dep_state_idle:
                    self._transition(BlockStatus.IDLE, block.active_status, "Dependence", scheduler.time)

            # startUpFailures draws (Raptor 7
            # the ZeroWait site's dispatch): a Phase_Change
            # (with positive phase value), Switched_On, or
            # Dependence_SB_turnOn wake lets a low-initialCost block draw
            # Uniform(0,1) from SysStream[10] and force
            # `FailTimeLeft:=0.000001`. Gated on the model-wide
            # StartUpFaiLures trigger flag; blocks with initialCost>=1.0
            # skip the branch including the draw. Setting `clock.fail_time_left`
            # here suppresses the fresh-redraw section below, matching
            # source's non-zero FailTimeLeft.
            self.clock.fail_time_left = self._startup_draw(idle_reason, self.clock.fail_time_left)

            # Port of `Run`'s `IF (FailTimeLeft=0.0): <redraw>`
            # -- positioned between the
            # `opStatus=Idle`-gated ZeroWait (this port's `while` loop
            # above) and the `CombinedStress` computation, and run
            # unconditionally on every `returnToTop` iteration. A block can
            # sit Idle for a long stretch with `FailTimeLeft` at 0.0/None;
            # source redraws it the first time `returnToTop` cycles with
            # `FailTimeLeft=0.0`, even though `opStatus` is still Idle and
            # the block immediately re-suspends afterward.
            #
            # Gated on `block.op_status == BlockStatus.IDLE` -- still
            # genuinely Idle, so the `while` loop above will iterate again.
            # When the same interrupt is also the block's real exit from
            # Idle (`op_status` already flipped to RUNNING synchronously by
            # `_dispatch_dependency`), the redraw belongs to the
            # `was_idle and clock.fail_time_left is None: yield 0.0` hop in
            # `_run_turn` after this wait, which pays a real scheduler round-trip before
            # redrawing so other same-instant same-stream draws go first.
            # `pm.due`/`clock.pm_time_left` decided here persist unconsumed
            # across further Idle-wait iterations (`pm.due` is reset only
            # at the top of the outer loop), so the decision is honoured
            # once the block exits Idle.
            if self.clock.fail_time_left is None and block.op_status == BlockStatus.IDLE:
                self._draw_life()
                self.pm.reason = InterruptReason.SCHEDULED_PM
                self.clock.operating_time = 0.0
        return IdleExit(was_idle, idle_pm_due, idle_pm_reason, induced)

    def _run_turn(self) -> Generator[float | Wait, Wake | None, _Turn | RepairRequest]:
        """One turn of source's `Run`: draw or resume the
        fail clock, wait (Idle, standby, PM race, interrupts), and resolve how
        the turn ends. Returns `_Turn.LOOP` to start another turn,
        `_Turn.STOP` when the block's life has ended, or a `RepairRequest`
        when `DetermineNewState` routes to `Repair`. A down-start's first
        turn goes straight to Repair (the kickoff fork below).
        """
        block = self.block
        scheduler = self.scheduler
        logs = self.logs
        events_file = self.events_file
        system_state = self.system_state
        # Port of Raptor 7's kickoff fork: a "Running" startCond
        # does `ASK block TO TurnOffStartCond; TELL block TO Run IN 0.0`,
        # anything else `TELL block TO Repair IN 0.0`. A block whose
        # `GetStartCond` put it DOWN never enters `Run` on its first pass --
        # source spawns `Repair` directly. This generator merges the two
        # processes, so the literal equivalent is to skip Run's half once.
        # Everything from the TELL boundary below is `Repair`'s body, so the
        # down-start lands there with no "Failed" line and no
        # `failure_counter` increment, matching the real asymmetry.
        #
        # `start_val` carries source's `TimeToRepair:=startVal`
        # (the "repairing" arm) to the
        # repair-duration site, replacing the fresh draw.
        if self.down_start.repair_time is not None:
            start_val = self.down_start.repair_time
            self.down_start.repair_time = None
            sim_time = scheduler.time
        else:
            start_val = None
            # `pm.due`/`pm.reason` are per-iteration decisions in source (a
            # direct `DetermineNewState("PM is due")` call, not a persisted
            # flag), so reset them every time round the loop. Otherwise a block
            # that reached PM via one path and later falls through the
            # redraw/wait section without re-deciding would re-enter PM with a
            # stale True.
            #
            # Except immediately after one of the benign-interrupt
            # `continue`s (`pm.benign_continue_pending`, set just before each):
            # that re-entry is the same resumption episode as the iteration
            # that decided `pm.due`, merely bounced through `continue`.
            if not self.pm.benign_continue_pending:
                self.pm.due = False
                self.pm.reason = InterruptReason.SCHEDULED_PM
                # `clock.race_raw_life` gets the same benign-continue survival as
                # `pm.due`: a benign mid-wait interrupt landing while a
                # PM-race-capped wait is in progress must not wipe the restore
                # point, or a later deferred PM (pmReqDefer) fails the restore
                # step's `clock.race_raw_life is not None` guard and the next
                # iteration burns a fresh fail-stream draw.
                if getattr(self.clock, "race_raw_life", None) is not None and self.clock.fail_time_left is not None:
                    # A PM-race-capped wait that was interrupted and re-enters `Run` for good: `FailTimeLeft`
                    # is the true remaining life (source never overwrites it), not the parked PM
                    # countdown, and the next pass races it afresh (`LadL6Full`: a zero-stress phase between
                    # two stressed ones kept the countdown as the life: `Failed` at exactly `pmFreq`).
                    self.clock.fail_time_left = self.clock.race_raw_life
                self.clock.race_raw_life = None
            self.pm.benign_continue_pending = False
            yield from self._cold_path_redraw()
                # Port of the switch-rolled-"none" permanent link state -- the
                # Run process's countdown is parked: this block's cold link
                # can never carry it again, and internal cascades (its own
                # pair-mate's fail/recover cycle bouncing it Idle/Good via the
                # end node) never re-arm a parked fail-wait. An external-route
                # cascade does wake and consume the remaining life. The
                # discriminator is the cascade origin (the resume tuple's third
                # element, `Scheduler.interrupt`'s `origin` tag): exit the park
                # only for origins outside this block's own cold-standby
                # subsystem.
                # Skip the park below entirely on the same cycle we just
                # redrew (`just_redrew`) -- fall straight through to the
                # ordinary wait/consumption code after this block, as if we
                # had broken out via an external-origin wake. Keyed on
                # `just_redrew` alone, not `needs_redraw`: the wait-until-IDLE
                # route must re-park so its following segment is consumed only
                # if an external-origin cascade arms it.
                #
                # No park here any more: an earlier origin-gated
                # `while True: yield _FAR_FUTURE` was removed once the
                # redraw-wait loop above was labelled with `zero_wait` as
                # source's PARK A, which is what produces the non-consumption
                # Raptor 7 shows for a switch-failed block.
            was_idle, idle_pm_due, idle_pm_reason, idle_induced = yield from self._idle_wait()

            if idle_induced:
                pass    # fails below: `pm.due` is already False, no redraw or wait
            elif not idle_pm_due:
                if was_idle and self.clock.fail_time_left is None:
                    # Port of `Run`'s `IF ((opStatus=Idle) AND (FailTimeLeft=
                    # 0.0)): ZeroWait:=TRUE; WAIT FOR BlockTrigger[seqNum] TO
                    # Fire; ... WAIT DURATION 0.0; {to mimic Raptor6's}` --
                    # Raptor 7. Source's gate is `opStatus=Idle`
                    # (checked before the wait-for-trigger) AND
                    # `FailTimeLeft=0.0`; `was_idle` tracks the first half,
                    # since `block.op_status` has already left IDLE. A block
                    # recovering with a remaining `clock.fail_time_left` does not get
                    # this hop, matching source's `FailTimeLeft=0.0` gate.
                    yield 0.0

                elif self.clock.fail_time_left is None and block.op_status == BlockStatus.STANDBY:
                    # Port of the `Too_many_on` demotion's turn boundary
                    # When this block's transition
                    # back to Running is immediately undone by the cold survey
                    # inside its own cascade -- `ChangeBlockState(Standby,...,
                    # "Too_many_on")` then `SetInterruptReason("Switched_Off");
                    # IF FailWait: Interrupt(block,"Run")` -- that interrupt is
                    # a queued delivery, so the source process parks here and
                    # redraws only when resumed in a later turn. The `was_idle`
                    # hop above ports source's `opStatus=Idle` gate, but a
                    # demoted block is STANDBY, not Idle. Skipped when the
                    # general `Run;` boundary was already crossed after a
                    # maintenance completion.
                    # Also skipped on the Run's FIRST pass after the kickoff
                    # barrier: source's kickoff `CheckColds`
                    # demotes synchronously BEFORE any Run TELL starts, so the
                    # Run starts already Standby and logs its standby stress in
                    # its first turn (PhasingDemo t=0 gdb trace: LowGain's
                    # StessLevel at its own Run start, S00009).
                    if not self.hops.run_hop_taken and not self.hops.first_run_pass:
                        yield 0.0

                self.hops.first_run_pass = False
                self.hops.run_hop_taken = False  # consumed: must not suppress a later, unrelated demotion
                if self.clock.fail_time_left is None:
                    raw_fail_draw = self._draw_life()
                    # Life the race just overwrote: when the PM wins,
                    # `_pm_race` replaces `clock.fail_time_left` with the PM countdown
                    # and the original raw draw survives only here. Source never
                    # discards it -- Raptor 7 waits
                    # `TimeToMaint:=PMTimeLeft` but keeps `FailTimeLeft`
                    # intact, and the wake's bookkeeping subtracts
                    # elapsed real time. If this PM attempt ends up deferred
                    # (pmReqDefer, see the restore below), the block must resume
                    # waiting on its remaining life, not redraw (a redraw
                    # consumes a fail-stream draw the Raptor 7 engine never made).
                    self.clock.race_raw_life = raw_fail_draw if self.pm.due else None
                    self.pm.reason = InterruptReason.SCHEDULED_PM
                    # `clock.operating_time` -- later logged as "Operating_time=" and
                    # used by the "OBPM" threshold check -- is a running
                    # accumulator (port of `OperatingTime:=SimTime-TimeHack+
                    # OperatingTime`, Raptor 7), reset to 0.0
                    # only when a NEW `FailTimeLeft` is drawn
                    # Once a block spends part of its
                    # draw Standby (stress<1.0), real elapsed time diverges from
                    # raw life consumed, so this must be a genuine running total
                    # of real time.
                    self.clock.operating_time = 0.0
                elif (block.uses_pm and not block.pm_triggered and self.clock.pm_time_left <= 0.0
                        and not self.pm.due):
                    # A life carried over (pmRefresh off restores it after a PM) still races the PM
                    # clock every pass: `IF PMTimeLeft<=0.0: PMTimeLeft:=pmFreq` then the comparison
                    # Without this a refresh-off block's second PM never
                    # came due (`PmMisDeferTimerProbe`: Raptor 7's next PM lands at 42, one period after
                    # the deferred one finished).
                    carried = self.clock.fail_time_left
                    self.pm.due, self.clock.pm_time_left, self.clock.fail_time_left = _pm_race(
                        block, self.clock.pm_time_left, carried,
                        combined_stress(block, system_state.current_phase if system_state is not None else 1))
                    self.clock.race_raw_life = carried if self.pm.due else None
                    self.pm.reason = InterruptReason.SCHEDULED_PM

                self.clock.stress = combined_stress(block, system_state.current_phase if system_state is not None else 1)
                raw_life = self.clock.race_raw_life if self.clock.race_raw_life is not None else self.clock.fail_time_left
                if block.uses_pm and not block.pm_triggered and raw_life is not None and self.clock.stress > 0.0:
                    # (Not at zero stress: `ZeroWait` comes before the race, so a zero-stress pass
                    # must leave the life untouched or the PM countdown it would park becomes the life.)
                    # Source races `PMTimeLeft` against `FailTimeLeft/CombinedStress` on EVERY pass of `Run`
                    # with the stress as it is now. The port parks a PM win as the PM
                    # countdown pre-multiplied by the stress it was raced under (`clock.fail_time_left`, the true
                    # life in `clock.race_raw_life`) and a PM loss as the raw life, so a pass after a stress change
                    # (a standby `Switched_On`/`Switched_Off`) must race again from the true remaining life, in
                    # both directions: a PM that won at stress 0.2 may lose at 1.0, and one that lost at 1.0 may
                    # win at 0.2. `LadL5Full` (real 2026-10-01): S1B3's PM at 370.0 (port 344.24) and at 1175.67
                    # (port: never).
                    self.pm.due, self.clock.pm_time_left, self.clock.fail_time_left = _pm_race(
                        block, self.clock.pm_time_left, raw_life, self.clock.stress)
                    self.clock.race_raw_life = raw_life if self.pm.due else None
                if self.clock.stress <= 0.0:
                    # Port of Raptor 7's `IF ((CombinedStress<=
                    # 0.0) OR (opStatus=Idle)): ZeroWait:=TRUE; WAIT FOR Block
                    # Trigger[seqNum] TO Fire; ...` -- a separate wait site from
                    # the `opStatus=Idle` one above (reached after stress is
                    # computed), but the same primitive. A cold-standby-Linked
                    # block can have `sb_stress=0.0`. Any interrupt wakes the
                    # loop back to the top (`continue`), where
                    # `combined_stress` is recomputed against whatever changed.
                    block.run_state.zero_wait = True
                    zero_wait_start = scheduler.time
                    resume = yield _FAR_FUTURE
                    self._observe_wake(WaitSite.ZERO_STRESS, resume)
                    block.run_state.zero_wait = False
                    if not isinstance(resume, Wake):
                        raise UnexpectedWake(resume)
                    block.run_state.interrupt_reason = ""
                    # Same ZeroWait site in source as the Idle wait (`_idle_wait`)
                    # (`IF ((CombinedStress<=0.0) OR (opStatus=Idle))`), so
                    # the same startUpFailures draws apply: a standby-stress-0
                    # cold block switched on draws too (StartupFailuresProbe
                    # real `/sd`: `LowGain 0.123091` at its switch-on).
                    if resume.reason == InterruptReason.INDUCED_FAILURE:
                        # Same site, same `Induced_Failure` branch: a zero-stress
                        # standby block fails when a dependency induces it.
                        self.clock.fail_time_left = None
                        self.pm.due = False
                        if block.uses_pm and not block.pm_fail_reset:
                            self.clock.pm_time_left -= scheduler.time - zero_wait_start   # 
                        self._record_failure(scheduler.time)
                        return RepairRequest(start_val, scheduler.time)
                    if resume.reason in (InterruptReason.START_DEFERRED_PM, InterruptReason.TRIGGERED_PM_DUE):
                        # `ZeroWait`'s own arms: a deferred or triggered PM reaching a block
                        # parked at zero stress goes straight to `DetermineNewState("PM is due")`, as the Idle site does.
                        self.pm.due = True
                        self.pm.reason = resume.reason
                        return (yield from self._preventive_maintenance(scheduler.time))
                    if resume.reason == InterruptReason.OBPM:
                        # `ZeroWait`'s OBPM arm: over the threshold a dependency-induced
                        # PM, under it benign (`LadL8Cab` run 85 t=2314.790508: a standby block at zero stress).
                        if _obpm_over_threshold(block, self.clock.operating_time):
                            block.run_state.returning_from_maintenance = True
                            self.pm.due = True
                            self.pm.reason = InterruptReason.OBPM
                            return (yield from self._preventive_maintenance(scheduler.time))
                        block.run_state.returning_from_maintenance = False
                        if block.def_dep_state_idle:
                            self._transition(BlockStatus.IDLE, block.active_status, "Dependence", scheduler.time)
                        return _Turn.LOOP
                    self.clock.fail_time_left = self._startup_draw(resume.reason, self.clock.fail_time_left)
                    return _Turn.LOOP
                if events_file and self.clock.stress != 1.0 and not self.hops.quiet_rewait:
                    logs.append(format_event_log_line(
                        scheduler.time, "Block", block.name, "StessLevel",
                        f"CombStress={self.clock.stress:.6f}",
                    ))

                turn_end = yield from self._fail_wait()
                if turn_end is not None:
                    return turn_end
            else:
                # `idle_pm_due` skips this whole redraw+stress+wait section:
                # source does not touch `FailTimeLeft`/redraw for a
                # `Triggered_PM_Due` reaching the ZeroWait/Idle wait, it
                # dispatches straight to `DetermineNewState("PM is due")`.
                self.pm.due = True
                self.pm.reason = idle_pm_reason

            sim_time = scheduler.time

            if self.pm.due:
                return (yield from self._preventive_maintenance(sim_time))

            if self._natural_expiry and self._has_defer_trigger():
                yield from self._defer_maintenance(sim_time)
                sim_time = scheduler.time
                self._log_failure(sim_time)
            else:
                self._record_failure(sim_time)
        return RepairRequest(start_val, sim_time)

    def _cold_path_redraw(self) -> Generator[float | Wait, Wake | None, None]:
        """The life redraw of a block whose cold-standby link failed for
        good (`block.run_state.cold_path_done`): source's PARK A for such a block.
        """
        block = self.block
        # A block whose cold-standby link was permanently LinkDone'd
        # (Switch_Failed, `cold_standby._switch_failed`) parks its Run
        # process here. Raptor 7 capture shows a switch-failed block still
        # fails again later once an external-route cascade reaches it, so
        # the park only suppresses consumption from internal-origin
        # cascades.
        if block.run_state.cold_path_done:
            # The park correctly suppresses further consumption from
            # internal-origin cascades, but Raptor 7 still draws a fresh life
            # the instant this block reaches Good after a repair completes
            # -- unconditionally, independent of the park. Without this,
            # the redraw is swallowed by the park until a later
            # external-origin wake breaks it out. One-shot: the
            # `clock.fail_time_left is None` guard only matches right after a
            # redraw is needed.
            needs_redraw = self.clock.fail_time_left is None
            # PARK A is for `opStatus=Idle` only : a repaired block that lands Standby (its cold link is
            # done, `LadL6Full` run 18 t=4157.691860) draws and goes on to log its standby stress at once.
            just_redrew = needs_redraw and block.op_status in (BlockStatus.RUNNING, BlockStatus.STANDBY)
            if just_redrew:
                self._draw_life()
                self.clock.operating_time = 0.0
            elif needs_redraw:
                # Landing on IDLE (dependency still down at repair
                # completion) does not redraw here: Raptor 7 shows no draw
                # across an intervening Idle->Good->Idle round trip driven
                # entirely by its cold-standby pair-mate. The discriminator
                # is whether the wake's resulting `op_status` is IDLE or
                # RUNNING, not cascade origin: a wake that exits to RUNNING
                # waits again; the first wake that leaves the block
                # genuinely IDLE draws right there, then falls through to
                # the ordinary Idle wait (`_idle_wait`) with `clock.fail_time_left`
                # already set, so Stage-A's `respawn_stage_a` check reads
                # False by then.
                while True:
                    # This loop is source's PARK A -- `IF ((opStatus=Idle)
                    # AND (FailTimeLeft=0.0)): ZeroWait:=TRUE; WAIT FOR
                    # BlockTrigger[seqNum] TO Fire`
                    # whose release falls straight into the
                    # `IF FailTimeLeft=0.0: DrawNumber` below exactly as
                    # the `break` here does. Setting `zero_wait` makes the
                    # block visible to source's wake route (`IF FailWait:
                    # Interrupt ELSIF ZeroWait: BlockTrigger.Release`).
                    block.run_state.zero_wait = True
                    yield _FAR_FUTURE
                    block.run_state.zero_wait = False
                    # Release from PARK A draws on ANY wake, whatever
                    # op_status the wake leaves behind. Source's PARK A
                    # entry test is evaluated at the loop top
                    # and once released the flow
                    # falls through `WAIT DURATION 0.0` into the
                    # unconditional `IF (FailTimeLeft=0.0): DrawNumber` at
                    # .
                    break
                # `WAIT DURATION 0.0 {to mimic Raptor6's}`
                #: source's PARK A release falls
                # through this scheduler boundary before the redraw below.
                # `resume` must be None: any interrupt landing in this
                # window is surfaced, not lost.
                resume = yield 0.0
                self._observe_wake(WaitSite.PARK_A_HOP, resume)
                if resume is not None:
                    raise UnexpectedWake(resume)
                self._draw_life()
                self.clock.operating_time = 0.0

    def _fail_wait(self) -> Generator[float | Wait, Wake | None, _Turn | None]:
        """`Run`'s fail-wait (`FailWait:=TRUE; WAIT DURATION TimeToMaint`,
        : wait out the rest of the life (or the PM
        countdown that won the race), or to the end of the run when the life
        overshoots it, and act on an interrupt through
        `_on_fail_wait_wake`.

        Returns `_Turn.LOOP` or `_Turn.STOP` when the turn ends here, or
        `None` to go on to PM (`pm.due`) or the failure.
        """
        block = self.block
        scheduler = self.scheduler
        run_length = self.run_length
        self.hops.quiet_rewait = False
        self._natural_expiry = False
        skip_ordinary_wait = False
        if self.hops.induced_now:
            # Same as the ordinary fail-wait site's `Induced_Failure`
            # branch (the failure tail records the component failure).
            self.hops.induced_now = False
            self.clock.fail_time_left = None
            self.pm.due = False
            skip_ordinary_wait = True
        elif scheduler.time + require(self.clock.fail_time_left, "fail_time_left") / self.clock.stress >= run_length:
            # This wait needs the same interrupt-awareness as the
            # ordinary fail-wait below -- a block whose current draw
            # overshoots run_length can still be interrupted into Idle
            # before reaching it if a sysDepend trigger fires.
            wait_start = scheduler.time
            # Port of Raptor 7's `FailWait:=TRUE; WAIT
            # DURATION TimeToMaint; ...; FailWait:=FALSE` -- see
            # `block.run_state.fail_wait`'s docstring.
            block.run_state.fail_wait = True
            resume = yield run_length - scheduler.time
            self._observe_wake(WaitSite.BEYOND_HORIZON, resume)
            block.run_state.fail_wait = False
            if isinstance(resume, Wake):
                if self._on_fail_wait_wake(resume, wait_start) is _Wake.LOOP:
                    return _Turn.LOOP
                skip_ordinary_wait = True
            else:
                self._transition(BlockStatus.DONE, block.active_status, "End_sim", scheduler.time, cascade=False)
                return _Turn.STOP

        if not skip_ordinary_wait:
            wait_start = scheduler.time
            self.clock.wait_duration = require(self.clock.fail_time_left, "fail_time_left") / self.clock.stress
            # Port of Raptor 7's `FailWait:=TRUE; WAIT
            # DURATION TimeToMaint; ...; FailWait:=FALSE` -- see
            # `block.run_state.fail_wait` (`model.py`) and
            # `orchestrator._Cascade._induce_dependency`'s gate on it:
            # a dependency-induced action only reaches this generator
            # while it is suspended here (or at the sibling `zero_wait`
            # sites). Source's `IF tempBlock.FailWait: Interrupt(...)
            # ELSIF tempBlock.ZeroWait: ASK BlockTrigger...Release END
            # IF` has no ELSE, so a block mid-`ObtainPMprereqs`
            # (neither flag set) receives no wake-up -- the action is
            # decided but never delivered.
            block.run_state.fail_wait = True
            resume = yield self.clock.wait_duration
            self._observe_wake(WaitSite.FAIL_WAIT, resume)
            block.run_state.fail_wait = False
            if isinstance(resume, Wake):
                if self._on_fail_wait_wake(resume, wait_start) is _Wake.LOOP:
                    return _Turn.LOOP
            else:
                # The life ran out by itself (an induced failure arrives as a `Wake` above, and
                # is not deferred: Raptor 7 is the timer's branch only).
                self._natural_expiry = True
                self.clock.fail_time_left = None
                self.clock.operating_time += self.clock.wait_duration
                if block.uses_pm and not self.pm.due:
                    # Port of Raptor 7's ELSE branch -- on a
                    # natural fail-wait expiry, PMTimeLeft decrements by
                    # the elapsed segment (SimTime-TimeHack) unless
                    # pmFailReset will zero it at the failure tail.
                    # Only for FAIL-side race wins: PM-win timeouts
                    # consume PMTimeLeft entirely (it refills via
                    # _pm_race's <=0 gate).
                    self.clock.pm_time_left -= self.clock.wait_duration
        return None

    def _record_failure(self, sim_time: float) -> None:
        """Log `Failed` and count the failure: the part of
        `DetermineNewState("Block Has Failed")` before `Repair`.
        """
        self._log_failure(sim_time)
        self._count_failure(sim_time)

    def _log_failure(self, sim_time: float) -> None:
        if self.events_file:
            self.logs.append(write_block_failed_line(self.block, sim_time, self.clock.operating_time))

    def _has_defer_trigger(self) -> bool:
        """`(comment="maintDefer") AND (deferTrig<>"")` (the Avant-garde
        defer-trigger feature)."""
        return (self.block.comment == "maintDefer" and self.cascade is not None
                and bool(self.cascade.defer_trigger))

    def _defer_maintenance(self, sim_time: float) -> Generator[float | Wait, Wake | None, None]:
        """A `maintDefer` block's natural failure when a defer trigger is set
       : count the failure, log `Failed-deferring`, go to RepHold
        ("deferred maintenance") and wait for the trigger to fire -- except that the special
        trigger name `phaseTrigger` does not hold a block while the system is in a NonMission
        phase. The ordinary `Failed` line and the repair follow the wake."""
        block = self.block
        cascade = self.cascade
        if cascade is None:
            raise EngineInvariantError("a deferred maintenance needs the cascade that owns the trigger")
        trigger = cascade.defer_trigger
        self._count_failure(sim_time)
        if self.events_file:
            self.logs.append(format_event_log_line(
                sim_time, "Block", block.name, "Failed-deferring",
                f"Operating_time={self.clock.operating_time:.6f}"))
        block.run_state.no_cold_check = True
        try:
            self._transition(BlockStatus.REP_HOLD, block.active_status, "deferred maintenance", sim_time)
        finally:
            block.run_state.no_cold_check = False
        if not cascade.has_trigger(trigger):
            return
        if trigger == "phaseTrigger" and self.system_state is not None \
                and self.system_state.mission_status is MissionStatus.NON_MISSION:
            return
        cascade.park_for_trigger(trigger, block.id)
        while True:
            resume = yield _FAR_FUTURE
            self._observe_wake(WaitSite.DEFER_TRIGGER_WAIT, resume)
            if isinstance(resume, Wake) and resume.reason == InterruptReason.TRIGGER_FIRED:
                return

    def _count_failure(self, sim_time: float) -> None:
        block = self.block
        system_state = self.system_state
        # Port of Raptor 7's unconditional (not
        # `EventsFile`-gated) `CompTimeToFail:=SimTime-TimeOfCompFailure;
        # TimeOfCompFailure:=SimTime; INC(FailureCounter)` -- natural and
        # induced failures converge onto this one call site.
        block.run_state.failure_counter += 1
        if system_state is not None:
            record_component_failure(system_state, sim_time)

        if block.uses_pm:
            # Port of Raptor 7's
            # `IF pmFailReset: PMTimeLeft:=0.0 ELSE:
            # PMTimeLeft:=PMTimeLeft-(SimTime-TimeHack)` -- the segment
            # decrement for pm_fail_reset=False happens at the resolution
            # sites, so the non-reset case keeps the raced/decremented
            # value here.
            if block.pm_fail_reset:
                self.clock.pm_time_left = 0.0

    def _preventive_maintenance(self, sim_time: float) -> Generator[float | Wait, Wake | None, _Turn]:
        """Source's PM path, entered when the PM race is won or a PM wake
        arrives: `DetermineNewState("PM is due")` ->
        `ObtainPMprereqs` -> `PerformPM`,
       then "Maintenance Completed". In source the
        last two are separate TELL processes; here they are zero-length
        hops within this sub-generator. Returns `_Turn.LOOP` to start
        another turn of `Run` (after the PM, or after a deferral), or
        `_Turn.STOP` when the block's life has ended.
        """
        step = yield from self._obtain_pm_prereqs(sim_time)
        if isinstance(step, _Turn):
            return step
        return (yield from self._perform_pm(step))

    def _obtain_pm_prereqs(self, sim_time: float) -> Generator[float | Wait, Wake | None, _Turn | float]:
        """Source's `ObtainPMprereqs`, from
        `DetermineNewState("PM is due")`: the pmMisDefer gate, then the PM
        spare and the PM resource, waiting (PMhold) or deferring
        (pmReqDefer) as the block is configured.

        Returns `_Turn.LOOP` when the PM does not start in this turn
        (deferred, or handed back to `Run`), or the time to start
        `PerformPM`.
        """
        block = self.block
        scheduler = self.scheduler
        logs = self.logs
        events_file = self.events_file
        system_state = self.system_state
        phase_defer_enabled = self.phase_defer_enabled
        if (self.pm.reason == InterruptReason.SCHEDULED_PM and block.pm_mis_defer and phase_defer_enabled
                and not self.pm.prereqs_secured and not self.pm.spare_pickup and self.cascade is not None):
            # The scheduled-PM arm of `Run`'s wait, before
            # `DetermineNewState("PM is due")`: a pmMisDefer block pays a `WAIT DURATION 0.0`, and while a
            # phase change is in progress parks on `pmSyncTrigger` until it ends, so the PM is judged
            # against the NEW mission status (`PmMisDeferTimerProbe`, real-traced 2026-09-30: the timer's
            # wake, the trigger, the phase clock, then this block's hop, which finds the change in progress).
            resume = yield 0.0
            self._observe_wake(WaitSite.PM_SCHEDULED_HOP, resume)
            if self.cascade.phase_change_in_progress:
                self.cascade.park_for_pm_sync(block.id)
                resume = yield _FAR_FUTURE
                self._observe_wake(WaitSite.PM_SYNC_WAIT, resume)
            sim_time = scheduler.time
        # Port of `DetermineNewState("PM is due")`'s pmMisDefer
        # defer-gate: a `pmMisDefer` block
        # whose PM comes due while the system is in a mission phase
        # does not run the PM now -- it joins the deferred set
        # (`deferPM:=TRUE` + `pmDeferGroup.Add`) and continues ordinary
        # life (`Run`); the phase-change machinery releases it with a
        # `Start_Deferred_PM` wake the next time the system enters a
        # NonMission phase. Source logs the `PM_is_due` line before the
        # gate, so the deferral is visible in the event log. The
        # race-parked raw life is restored here, so the block
        # resumes its original countdown with no redraw. The gate
        # precedes the `pm.prereqs_secured` fast-path: a resource that
        # lands for an already-deferred PM is impossible (the defer
        # skips prereq acquisition), and the release wakes route
        # through `pm.reason="Start_Deferred_PM"` fresh dispatches.
        # A PM that comes due while `deferPM` is ALREADY set takes the same
        # branch: source's `IF NOT deferPM: ObtainPMprereqs ELSE Run`
        # runs no prerequisites for it, whether or not the
        # mission check above set the flag just now.
        # (Not for the continuation after an asynchronous spare pickup: `DetermineNewState` passed this gate when the
        # request was made, and `ObtainPMprereqs` carries on to the resource step and then the pmMisDefer tail, which
        # postpones in a mission, gives the spare and resource back and logs `Returned_N_Resource(s)` -- `LadL8C`.)
        if (not self.pm.prereqs_secured and not self.pm.spare_pickup
                and (block.run_state.defer_pm
                     or (block.pm_mis_defer and phase_defer_enabled
                         and system_state is not None
                         and system_state.mission_status is MissionStatus.MISSION))):
            if events_file:
                logs.append(format_event_log_line(
                    sim_time, "Block", block.name, "PM_is_due", self.pm.reason,
                ))
            if not block.run_state.defer_pm:
                block.run_state.pm_defer_order = next(_DEFER_SEQ)
            block.run_state.defer_pm = True
            if self.clock.race_raw_life is not None:
                # the PM-due wake takes the elapsed countdown off the life.
                self.clock.fail_time_left = self.clock.race_raw_life - self.clock.wait_duration * self.clock.stress
                self.clock.race_raw_life = None
            self.pm.due = False
            # `Run` is a TELL METHOD: the
            # `ELSE Run` spawns a fresh Run record, so the block
            # sits in neither FailWait nor ZeroWait until it is
            # selected, and a dependency action decided in that
            # gap is never delivered (gdb trace, ReleaseProbe32
            # run 2 t=231.000010: S14233 TELL Run, S14234
            # BatteriesNiCd's PM idles Bus_UR, S14235 the new Run
            # waits 842.676692 -- no induced failure).
            resume = yield 0.0
            self._observe_wake(WaitSite.PM_MISDEFER_RUN, resume)
            # Source's `ELSE Run`. Without this the branch
            # would fall through into the resource request and
            # `beginning_PM` below, logging a deferral and then
            # ignoring it.
            return _Turn.LOOP
        elif not self.pm.prereqs_secured:
        # Port of `DetermineNewState("PM is due")` -> `ObtainPMprereqs`
        # -> `PerformPM`. Beyond the zero-length TELL-boundary
        # yield below, not otherwise interrupt-aware.
            if events_file and not self.pm.spare_pickup:
                logs.append(format_event_log_line(sim_time, "Block", block.name, "PM_is_due", self.pm.reason))
            self.pm.spare_pickup_pass = self.pm.spare_pickup
            self.pm.spare_pickup = False

            # Port of `ObtainPMprereqs;` being a `TELL METHOD`
            # -- a fire-and-forget spawn, so
            # `DetermineNewState` returns before `ObtainPMprereqs`'s
            # body runs. When a PM trigger fires for multiple
            # subscribing blocks, this is why every subscriber's
            # "PM_is_due" line appears before any reaches
            # "beginning_PM". This generator is one long-lived process
            # per block, so it needs an explicit zero-length yield to
            # reproduce that boundary. An interrupt can land in this
            # window (e.g. a sibling entering PM cascading a
            # `Dependence_Idle`), but this site must not abandon for
            # "pure notification" reasons -- source's
            # `ObtainPMprereqs` is a separate process from `Run`, so a
            # `Dependence`-driven state change does not touch it; it
            # keeps running toward `beginning_PM`. Skipped entirely
            # (`pm.prereqs_secured`) when this pass is a deferred PM
            # resource arriving async. `waiting_for_pm_prerequisites` is the
            # port of source's `WaitingForPrereqs`
            #: `orchestrator._Cascade._induce_
            # dependency`'s delivery gate checks it, so a same-instant
            # dependency-cascade interrupt does not reach this
            # generator while it is set.
            if not (self.pm.spare_pickup_pass and block.pm_mis_defer and phase_defer_enabled):
                # (A pmMisDefer block's asynchronous spare pickup is the resumed spare wait itself and pays no TELL
                # hop: real-traced `LadL8Cdiag` t=2600 run 1, S02844 `Spare_Arrived` -> one `ObtainPMprereqs` wake ->
                # `Returned_1_Resource(s)`.)
                block.run_state.waiting_for_pm_prerequisites = True
                resume = yield 0.0
                self._observe_wake(WaitSite.PM_PREREQS_TELL, resume)
                block.run_state.waiting_for_pm_prerequisites = False
                if isinstance(resume, Wake):
                    self._on_pm_boundary_wake(resume, handback=False)
                sim_time = scheduler.time

            # Port of `ObtainPMprereqs` --
            # happy path only: a spare/resource that is immediately
            # available. Source's "not immediately available" branches
            # (PMhold holds, the `WAIT FOR ... TO Give` suspend-resume
            # machinery, `pmMisDefer`'s give-it-back path) are not
            # ported -- raises rather than guessing. `block.run_state.defer_pm` is
            # an entry guard matching source's `IF NOT deferPM:
            # ObtainPMprereqs ELSE: Run` : a phase-driven
            # defer sets it before this point, in which case PM does not
            # happen this cycle.
            if block.run_state.defer_pm:
                # The deferral gate above takes every PM that is due while
                # `deferPM` is set, so reaching here with it set would be a bug.
                raise EngineInvariantError(
                    f"block {block.name!r} reached ObtainPMprereqs with defer_pm set")
            step = yield from self._obtain_pm_spare(sim_time)
            if isinstance(step, _Turn):
                return step
            sim_time = step

        step = yield from self._obtain_pm_resource(sim_time)
        if (isinstance(step, _Turn) or not (block.pm_mis_defer and phase_defer_enabled)
                or self.pm.prereqs_secured or self.cascade is None
                or (block.uses_pm and block.resource_type_count == 1 and block.pm_resources_needed > 0)):
            return step
        # `ObtainPMprereqs`' pmMisDefer tail for a PM with no resource to wait for (the resource branch has its own
        # copy, `_obtain_pm_resource`): `WAIT DURATION 0.0`, the phase-change sync, then postponement during a mission
        # in a non-mission phase the hand-back to `Run` (`Interrupt(SELF,"Run")`) is one more hop.
        resume = yield 0.0
        self._observe_wake(WaitSite.PM_TAIL_HOP, resume)
        if self.cascade.phase_change_in_progress:
            self.cascade.park_for_pm_sync(block.id)
            resume = yield _FAR_FUTURE
            self._observe_wake(WaitSite.PM_SYNC_WAIT_TAIL, resume)
        step = scheduler.time
        if system_state is not None and system_state.mission_status is MissionStatus.MISSION:
            self._misdefer_give_back(step)
            # The postponement has no `Run;` of its own : this PM came clean through, so nothing
            # restarts the `Run` that called `DetermineNewState`, and the block is left with no life process for
            # the rest of the run -- it never fails or PMs again, and the later `Start_Deferred_PM` release finds it
            # in neither wait (`LadL8A` run 22: S5B3, postponed at 1770.355159, is silently released at 2441.342931).
            return _Turn.STOP
        return scheduler.time

    def _abandon_pm_attempt(self) -> None:
        """Give up this PM attempt (deferred, or skipped because no spare
        will ever come) and go back to the life the PM race parked.

        Source re-enters `Run` with `FailTimeLeft` still holding the
        remaining life, so the next turn must not redraw. In raw units
        (see `clock.race_raw_life`), the life consumed while the PM
        countdown ran is the wait times the stress.
        """
        self.pm.due = False
        if self.clock.race_raw_life is not None:
            self.clock.fail_time_left = self.clock.race_raw_life - self.clock.wait_duration * self.clock.stress
            self.clock.race_raw_life = None

    def _obtain_pm_spare(self, sim_time: float) -> Generator[float | Wait, Wake | None, _Turn | float]:
        """`ObtainPMprereqs`'s spare step, when the
        PM needs a spare: take it from Infinite stock, the spare pool or the
        block's Custom queue. An empty stock spawns an emergency order, waits in
        PMhold, or defers (pmReqDefer); a stock that can never refill skips the
        PM (`sparesDone`).

        Returns `_Turn.LOOP` when this PM attempt ends here (skipped or
        deferred), or the time the spare was in hand.
        """
        block = self.block
        stats = self.stats
        cost_analysis = self.cost_analysis
        if block.uses_pm and block.pm_spare_needed:
            if block.sparing_type == SparingType.INFINITE:
                block.run_state.spares_used += 1
                block.run_state.spare_obtained = True  # 
                if cost_analysis:
                    # Raptor 7 -- Infinite pays per
                    # consumption (Custom pays at order time).
                    stats.sp_cost += block.spare_cost
            elif block.sparing_type == SparingType.NONE:
                pass
            elif block.sparing_type == SparingType.SPARE_POOL:
                return (yield from self._obtain_pm_pool_spare(sim_time))
            elif block.sparing_type == SparingType.CUSTOM:
                return (yield from self._obtain_pm_custom_spare(sim_time))
            else:
                raise NotImplementedError(
                    f"block {block.name!r}'s PM spare requirement (sparing_type="
                    f"{block.sparing_type!r}) not ported -- ObtainPMprereqs's spare-"
                    "waiting branch is Infinite/None/"
                    "SparePool/Custom only here"
                )
        return sim_time

    def _obtain_pm_pool_spare(self, sim_time: float) -> Generator[float | Wait, Wake | None, _Turn | float]:
        """`ObtainPMprereqs`'s SparePool branch: take
        the PM's spare from the shared spare pool. An empty pool spawns an
        emergency order, defers (pmReqDefer) or holds in PMhold until the grant
        lands; a pool that can never refill skips the PM (`sparesDone`).

        Returns `_Turn.LOOP` when this PM attempt ends here, or the time the
        spare was in hand.
        """
        block = self.block
        scheduler = self.scheduler
        logs = self.logs
        events_file = self.events_file
        spare_pool = self.spare_pool
        spare_pool_config = self.spare_pool_config
        spawn_order_a_spare = self.spawn_order_a_spare
        spawn_order_stock = self.spawn_order_stock
        spare_pool = require(spare_pool, "spare_pool")
        spare_pool_config = require(spare_pool_config, "spare_pool_config")
        # Port of `spareObtained` (Raptor 7/
        # 5577/5610, reset). Source guards
        # spare acquisition with `IF ((sparingType=
        # SparePool) AND (NOT spareObtained))` and `IF (NOT
        # spareObtained)`, so a block already holding a
        # spare for this maintenance never takes a second
        # one. Without this, a PM whose prereq loop deferred
        # (waiting on the resource after its spare was
        # granted) re-enters here and consumes another
        # spare.
        if not block.run_state.spare_obtained:
            if spare_pool is None or spare_pool_config is None:
                raise EngineInvariantError(
                    f"block {block.name!r} needs a PM spare from pool "
                    f"{block.pool_name!r} but no spare pool was provided "
                    "to block_run_process"
                )
            # Port of `ObtainPMprereqs`'s SparePool branch
            # -- same shape as
            # Repair's SparePool consumption (ESO order /
            # sparesDone / SLO re-order), plus the two "not
            # immediately available" paths:
            # - pmReqDefer : a hand-built
            #   "pmReqDefer"/"No_spare_in_stock-<pool>" line
            #   (literal action-string override), `Run;`
            #   again, no PMhold. The Give-wait stays queued;
            #   its grant lands through the same
            #   `Pool_Available` reconciliation the resource
            #   branch uses (`pm.resource_pending`),
            #   fast-forwarding into PerformPM via
            #   `pm.prereqs_secured`.
            # - otherwise PMhold : hold until the
            #   grant lands, log "Spare_Arrived", proceed.
            emer_ordered_here = False
            if spare_pool.resources == 0:
                if spare_pool_config.emergency_spare_ordering:
                    # `deferred=True` (ER3-29): trace-verified
                    # against a real gdb capture of
                    # `PmCustomESOProbe` (Custom sibling, same
                    # shape) -- Raptor 7 logs `pmReqDefer` BEFORE
                    # `Sparing Spare_ordered`, twice (t=10 and
                    # t=61), the reverse of a synchronous
                    # `OrderASpare` TELL. A deferred start lets
                    # this ObtainPMprereqs turn continue and log
                    # pmReqDefer first; OrderASpare's own
                    # "Spare_ordered" line lands in its own
                    # later selection, same instant.
                    if spawn_order_a_spare is not None:
                        spawn_order_a_spare(deferred=True)
                    spare_pool.num_emer_ordered += 1
                    emer_ordered_here = True
                elif not (spare_pool_config.routine_spare_ordering
                          or spare_pool_config.stock_level_ordering):
                    # `sparesDone:=TRUE` -> `skipPM:=TRUE;
                    # Run; RETURN` : no spare
                    # will ever come -- skip this PM
                    # entirely, resume ordinary running
                    # with the raced life restored.
                    self._abandon_pm_attempt()
                    yield 0.0
                    return _Turn.LOOP
            if not spare_pool.request(block.id, 1, kind="pm"):
                if block.pm_req_defer:
                    if events_file:
                        logs.append(format_event_log_line(
                            sim_time, "Block", block.name, "pmReqDefer",
                            f"No_spare_in_stock-{block.pool_name}",
                        ))
                    # SPARE-pool registration -- tracked on
                    # `pm.spare_pending`, not the resource
                    # counter, so its eventual grant is
                    # reconciled as "spare arrived" and
                    # still has source's resource wait
                    # ahead of it.
                    self.pm.spare_pending += 1
                    if emer_ordered_here:
                        self.pm.spare_emer_pending += 1
                    self._abandon_pm_attempt()
                    # `Run;` again -- the TELL-boundary hop,
                    # same as the resource branch's deferral
                    # .
                    yield 0.0
                    return _Turn.LOOP
                self._transition(BlockStatus.PM_HOLD, block.active_status, "pmReqDefer", sim_time)
                resume = yield _FAR_FUTURE
                self._observe_wake(WaitSite.PM_HOLD_POOL_SPARE, resume)
                if not (
                    isinstance(resume, Wake)
                        and resume.reason == InterruptReason.POOL_AVAILABLE
                ):
                    raise UnexpectedWake(resume)
                sim_time = scheduler.time
                if emer_ordered_here:
                    spare_pool.num_emer_ordered -= 1      # `DecNumEmerOrdered` after the Give 
                if events_file:
                    logs.append(format_event_log_line(
                        sim_time, "Block", block.name, "Sparing", "Spare_Arrived",
                    ))
            block.run_state.spares_used += 1
            # SLO re-order check, same shape as
            # Repair's own SparePool consumption above.
            if spare_pool_config.stock_level_ordering:
                if spare_pool.resources == spare_pool_config.slo_order_level:
                    if spawn_order_stock is not None:
                        spawn_order_stock(spare_pool_config.slo_new_spares)
                elif (spare_pool.resources
                      <= spare_pool_config.slo_order_level - spare_pool_config.slo_new_spares):
                    if spawn_order_stock is not None:
                        spawn_order_stock(1)
            block.run_state.spare_obtained = True
        return sim_time

    def _obtain_pm_custom_spare(self, sim_time: float) -> Generator[float | Wait, Wake | None, _Turn | float]:
        """`ObtainPMprereqs`'s Custom branch: take the
        PM's spare from the block's private Custom queue, with the same empty-
        stock handling as the spare pool (`_obtain_pm_pool_spare`).

        Returns `_Turn.LOOP` when this PM attempt ends here, or the time the
        spare was in hand.
        """
        block = self.block
        stats = self.stats
        scheduler = self.scheduler
        logs = self.logs
        cost_analysis = self.cost_analysis
        events_file = self.events_file
        wake_block = self.wake_block
        self.custom_spares = require(self.custom_spares, "custom_spares")
        # Port of ObtainPMprereqs's Custom branch
        # -- same shape as the
        # SparePool branch above, using the block's private
        # `custom_spares` queue. Source's `pmReqDefer`
        # sub-branch calls `Run;` as a nested re-entrant
        # call and then still falls through to
        # `WAIT FOR CustomSpares TO Give` on the same
        # suspended frame; this port mirrors the SparePool
        # branch's simplification instead (abandon the PM
        # attempt, restore the raced life, retry once the
        # stock arrives via `pm.spare_pending`). With
        # RSO=ESO=SLO=0 the first exhaustion is permanent
        # (`sparesDone`), so only the skipPM path is
        # exercised there.
        if not block.run_state.spare_obtained:
            if self.custom_spares.resources == 0:
                if block.emergency_spare_ordering:
                    # `deferred=True` (ER3-29) -- see the
                    # identical SparePool branch above for the
                    # trace evidence; same call shape, same
                    # ObtainPMprereqs-vs-OrderASpare ordering.
                    scheduler.tell(order_a_custom_spare_process(
                        block, self.custom_spares, scheduler, require(wake_block, "wake_block"), logs,
                        events_file=events_file,
                        block_stats=stats if cost_analysis else None,
                    ), block_id=block.id, deferred=True)
                elif not (block.routine_spare_ordering
                          or block.stock_level_ordering):
                    # `sparesDone:=TRUE` -> the shared
                    # `IF sparesDone: skipPM:=TRUE; Run;
                    # RETURN` -- no spare will
                    # ever come; skip this PM entirely and
                    # resume ordinary running with the raced
                    # life restored. SLO-only counts as "one
                    # will come" per source's
                    # `NOT stockLevelOrdering` conjunct
                    # .
                    self._abandon_pm_attempt()
                    yield 0.0
                    return _Turn.LOOP
            # ER3-04: `kind="pm"` (not the default
            # "repair"), matching `resource_pool`'s own
            # convention ( below) -- so a later
            # abandoned-PM-attempt cleanup can find and
            # withdraw exactly THIS queue entry by kind,
            # the same way the shared resource pool's
            # `pm.resource_pending`/`withdraw(...,"pm")`
            # pairing already does. Without a matching
            # kind tag here, `custom_spares.withdraw`
            # can never find this entry: it stays queued
            # after `pmReqDefer` abandons the PM attempt,
            # letting a later, unrelated failure's own
            # Custom-sparing wait (below) be satisfied out
            # of turn by whichever spare arrives first,
            # and this block wrongly proceeds through a
            # shared RESOURCE-DATA borrow it never
            # actually obtained (gdb-trace-confirmed
            # against `DepCancelConservationProbe`: real
            # makes PmCustom wait for ResHolder's own
            # resource release at t=53; this port let it
            # through at t=34/36 instead).
            if not self.custom_spares.request(block.id, 1, kind="pm"):
                if block.pm_req_defer:
                    if events_file:
                        logs.append(format_event_log_line(
                            sim_time, "Block", block.name, "pmReqDefer",
                            "No_spare_in_stock-Custom",
                        ))
                    # SPARE-pool registration on
                    # `pm.spare_pending` (not the resource
                    # counter): `_claim_pm_grant` must
                    # reconcile this grant against the
                    # private custom queue.
                    self.pm.spare_pending += 1
                    self._abandon_pm_attempt()
                    yield 0.0
                    return _Turn.LOOP
                self._transition(BlockStatus.PM_HOLD, block.active_status, "No_spare_in_stock-Custom", sim_time)
                resume = yield _FAR_FUTURE
                self._observe_wake(WaitSite.PM_HOLD_CUSTOM_SPARE, resume)
                if not (
                    isinstance(resume, Wake)
                        and resume.reason == InterruptReason.POOL_AVAILABLE
                ):
                    raise UnexpectedWake(resume)
                sim_time = scheduler.time
                if events_file:
                    logs.append(format_event_log_line(
                        sim_time, "Block", block.name, "Sparing", "Spare_Arrived",
                    ))
            block.run_state.spares_used += 1
            if block.stock_level_ordering:
                if self.custom_spares.resources == block.slo_order_level:
                    self._spawn_custom_stock_order(block.slo_new_spares)
                elif self.custom_spares.resources <= block.slo_order_level - block.slo_new_spares:
                    self._spawn_custom_stock_order(1)
            block.run_state.spare_obtained = True
        return sim_time

    def _obtain_pm_resource(self, sim_time: float) -> Generator[float | Wait, Wake | None, _Turn | float]:
        """`ObtainPMprereqs`'s resource step, when the
        PM borrows from the shared resource pool: take the resource, or wait in
        PMhold, or defer (pmReqDefer) and hand back to `Run` until the grant
        lands. Also the hand-back to `Run` after an asynchronous grant.

        Returns `_Turn.LOOP` when this PM attempt ends here, or the time to
        start `PerformPM`.
        """
        block = self.block
        scheduler = self.scheduler
        resource_pool = self.resource_pool
        system_state = self.system_state
        phase_defer_enabled = self.phase_defer_enabled
        if block.uses_pm and block.resource_type_count == 1 and block.pm_resources_needed > 0:
            if resource_pool is None:
                raise EngineInvariantError(
                    f"block {block.name!r} needs {block.pm_resources_needed} PM resource unit(s) but no "
                    "resource_pool was provided to block_run_process"
                )
            step = yield from self._request_pm_resource(sim_time)
            if isinstance(step, _Turn):
                return step
            sim_time = step

            # Port of `ObtainPMprereqs`'s post-resource-check hand-back
            # to `Run`. Source's branch looks
            # conditional on `opStatus=PMhold`, but a plain
            # `WAIT FOR PooledSpares[resPoolNum] TO Give(...)` is a
            # scheduled event costing a round-trip even when already
            # satisfiable (same idiom as `Run`'s `WAIT DURATION 0.0`),
            # so the no-wait case pays this boundary too. Making it
            # unconditional (not gated on `pm_res_waiting`) matches the
            # real ordering of "Obtained_N_Resource(s)" lines relative
            # to "beginning_PM". `waiting_for_pm_prerequisites` -- see the
            # sibling copy above.
            # Source's `IF opStatus=PMhold` split, read before
            # the hop: a PMhold block goes straight to PerformPM; only
            # the pmReqDefer / came-clean-through branch has the
            # pmMisDefer mission check below.
            self.hops.handback_from_pmhold = block.op_status == BlockStatus.PM_HOLD
            _handback_status_before = block.op_status

            _async_give_pickup = self.hops.pm_async_pickup and not self.hops.handback_from_pmhold
            _misdefer_path = block.pm_mis_defer and phase_defer_enabled
            _ow0 = block.run_state.reason_overwrites
            handback: float | Wait = 0.0
            if _async_give_pickup and not _misdefer_path:
                handback = self._run_wake_hop()
            block.run_state.waiting_for_pm_prerequisites = True
            self.hops.pm_handback_taken = True
            resume = yield handback
            self._observe_wake(WaitSite.PM_HANDBACK, resume)
            block.run_state.waiting_for_pm_prerequisites = False
            if isinstance(resume, Wake):
                self._on_pm_boundary_wake(resume, handback=True)
            sim_time = scheduler.time
            if (not self.hops.handback_from_pmhold and block.pm_mis_defer and phase_defer_enabled
                    and self.cascade is not None and self.cascade.phase_change_in_progress):
                # `IF PhaseChangeInProgress: WAIT FOR pmSyncTrigger TO Fire`, for every PM path, not
                # only the scheduled timer's own earlier copy: an OBPM that lands mid-phase-change is judged against
                # the NEW mission status (`LadL8A` run 22 t=1770.355159: postponed, no `beginning_PM`).
                self.cascade.park_for_pm_sync(block.id)
                resume = yield _FAR_FUTURE
                self._observe_wake(WaitSite.PM_SYNC_WAIT_HANDBACK, resume)
                sim_time = scheduler.time
            # Port of `ObtainPMprereqs`' pmMisDefer tail
            # with phases active, a pmMisDefer block pays
            # a `WAIT DURATION 0.0` (on the mission branch the hand-back
            # hop above stands for it; the non-mission branch pays one
            # more hop below before its `Interrupt(SELF,"Run")`), and
            # if the system is now in a MISSION phase the PM is
            # postponed: `pmDeferGroup.Add(SELF); deferPM:=TRUE`, the
            # resource goes back (`TakeBack` + `Returned_N_Resource(s)`,
            # cost reversed) and the block carries on running. The
            # phase machinery's `Start_Deferred_PM` release picks it up
            # later. ReleaseProbe31 run 1 t=159.312248: a pmReqDefer
            # request from a non-mission phase granted during a
            # mission. Spare give-back (`pmSpareNeeded`) is not ported.
            if (not self.hops.handback_from_pmhold and block.pm_mis_defer and phase_defer_enabled
                    and system_state is not None
                    and system_state.mission_status is MissionStatus.MISSION):
                self._misdefer_give_back(sim_time)
                # A postponed async spare pickup leaves `Run` in the wait it was already in: no second `StessLevel`
                # (`LadL8Cdiag` run 13 t=1370.988014).
                if self.pm.spare_pickup_pass:
                    self.hops.quiet_rewait = True
                return _Turn.LOOP
            if not self.hops.handback_from_pmhold and block.pm_mis_defer and phase_defer_enabled:
                # Non-mission branch: the pmMisDefer block's
                # `WAIT DURATION 0.0` precedes its `Interrupt(SELF,
                # "Run")`, one hop more than an ordinary block's path
                # to PerformPM.
                misdefer_hop: float | Wait = 0.0
                if _async_give_pickup:
                    # Literal: this second hop is the Run wake (the
                    # first was the pmMisDefer WAIT DURATION 0.0).
                    _ow0 = block.run_state.reason_overwrites
                    misdefer_hop = self._run_wake_hop()
                block.run_state.waiting_for_pm_prerequisites = True
                resume = yield misdefer_hop
                self._observe_wake(WaitSite.PM_MISDEFER_HOP, resume)
                block.run_state.waiting_for_pm_prerequisites = False
                sim_time = scheduler.time
            if _async_give_pickup and block.run_state.interrupt_reason == InterruptReason.OBTAINED_PM_PREREQS:
                block.run_state.interrupt_reason = ""  # Run consumed its wake 
            if (_async_give_pickup and block.run_state.reason_overwrites != _ow0) or (
                    not _async_give_pickup
                    and not self.hops.handback_from_pmhold and block.pm_mis_defer and phase_defer_enabled
                    and _handback_status_before != BlockStatus.IDLE
                    and block.op_status == BlockStatus.IDLE):
                # Literal mode: ANY dispatch that rewrote the shared
                # InterruptReason between the Run wake and Run's
                # resume loses the "Obtained PM Prereqs" wake.
                # LOST WAKE (gdb trace, ReleaseProbe32 run 1
                # t=69.437804): `ObtainPMprereqs` hands back with
                # `InterruptReason:="Obtained_PM_Prereqs";
                # Interrupt(SELF,"Run")`, but a dependency dispatch
                # idles the block before its Run resumes and
                # overwrites the shared `InterruptReason` with
                # `Dependence_Idle`. Run returns to the top and goes
                # Idle: no PM, and the obtained resource stays held.
                # Only the pmMisDefer path: its extra `WAIT DURATION
                # 0.0` is what opens the window (trace S00451 ->
                # S00457 -> S00461). An ordinary block's Run consumes
                # the wake first; applying this to every block broke
                # 10 fixtures. A modelled consequence of the shared
                # field, not a literal port of it.
                self.pm.due = False
                self.hops.pm_handback_taken = False
                if self.clock.race_raw_life is not None:
                    self.clock.fail_time_left = self.clock.race_raw_life
                    self.clock.race_raw_life = None
                if _async_give_pickup and block.run_state.interrupt_reason == InterruptReason.INDUCED_FAILURE:
                    # Run resumes in its FailWait's ON INTERRUPT with
                    # the overwriting reason and fails at once
                    # (ReleaseProbe32 run 5 t=692.116665, S56947:
                    # SolarP_500W Failed right after the lost wake).
                    block.run_state.interrupt_reason = ""
                    self.hops.induced_now = True
                    self.hops.quiet_rewait = True
                return _Turn.LOOP
        return sim_time

    def _run_wake_hop(self) -> Wait:
        """Literal: the hop that IS the Run wake after the Give resume, to
        be yielded. ZeroWait (Idle/Cut) -> `BlockTrigger.Release`, a record
        of the BlockTrigger object (taking over the slot a same-instant
        AllUp reserved, if any); FailWait -> `Interrupt(SELF, "Run")`,
        re-stamping the block's own record (its group goes to the calendar
        tail). Both set `InterruptReason:="Obtained_PM_Prereqs"` first.
        """
        block = self.block
        block.run_state.interrupt_reason = InterruptReason.OBTAINED_PM_PREREQS
        slot = block.run_state.trigger_slot_handle
        if slot is not None:
            block.run_state.trigger_slot_handle = None
            return Wait(owner=Owner.block_trigger(block.id), replaces=slot)
        if block.op_status == BlockStatus.IDLE:
            return Wait(owner=Owner.block_trigger(block.id))
        return Wait(retail=True)

    def _misdefer_give_back(self, sim_time: float) -> None:
        """`ObtainPMprereqs`' pmMisDefer tail on the mission branch
       : postpone the PM (`deferPM:=TRUE`), give
        the resource back (`TakeBack`, `Returned_N_Resource(s)`, cost
        reversed) and let the block carry on running.
        """
        block = self.block
        logs = self.logs
        cost_analysis = self.cost_analysis
        events_file = self.events_file
        resource_pool = self.resource_pool
        pool_stats = self.pool_stats
        wake_block = self.wake_block
        if not block.run_state.defer_pm:
            block.run_state.pm_defer_order = next(_DEFER_SEQ)
        block.run_state.defer_pm = True
        if block.pm_spare_needed:
            self._give_back_pm_spare()
        if resource_pool is not None and block.resource_type_count == 1 and block.pm_resources_needed > 0:
            for granted_id, _kind in resource_pool.release_kinds(block.pm_resources_needed):
                require(wake_block, "wake_block")(granted_id, "Pool_Available", _kind)
        if block.resource_type_count == 1 and block.pm_resources_needed > 0:
            if events_file:
                logs.append(format_event_log_line(
                    sim_time, "Block", block.name, "Resources",
                    f"Returned_{block.pm_resources_needed}_Resource(s)"))
            if cost_analysis and pool_stats is not None:
                pool_stats.es_cost -= _res_fixed_per_use(block) * float(block.pm_resources_needed)
        if self.clock.race_raw_life is not None and not self.hops.pm_async_pickup:
            self.clock.fail_time_left = self.clock.race_raw_life
        self.clock.race_raw_life = None
        self.pm.due = False
        self.hops.pm_handback_taken = False
        self.hops.quiet_rewait = self.hops.pm_async_pickup

    def _give_back_pm_spare(self) -> None:
        """The PM's spare goes back, because the PM is postponed to the next
        NonMission phase (`ObtainPMprereqs`): the
        inverse of the PM-side acquisition, per sparing type.

        * Infinite: the spare cost is reversed (`BlockSpCost -= spareCost`)
          and the use is un-counted (`DEC(SparesUsed)`).
        * Custom: the use is un-counted and the spare returns to the block's
          private queue (`TakeBack`), which may satisfy a waiter.
        * SparePool: the spare returns to the shared pool, then the use is
          un-counted.
        * None: nothing (source's own note: "remnant").

        Then `spareObtained:=FALSE`, so the postponed PM asks for a spare again.
        """
        block = self.block
        if block.sparing_type == SparingType.INFINITE:
            if self.cost_analysis:
                self.stats.sp_cost -= block.spare_cost
            block.run_state.spares_used -= 1
        elif block.sparing_type == SparingType.CUSTOM:
            block.run_state.spares_used -= 1
            self._take_back_spare(require(self.custom_spares, "custom_spares"))
        elif block.sparing_type == SparingType.SPARE_POOL:
            self._take_back_spare(require(self.spare_pool, "spare_pool"))
            block.run_state.spares_used -= 1
        block.run_state.spare_obtained = False

    def _take_back_spare(self, queue: ResourceQueue) -> None:
        """`TakeBack(SELF,1)`: one spare back on `queue`; a block it now
        satisfies is woken the way every other pool release wakes its grantee."""
        for granted_id, kind in queue.release_kinds(1):
            require(self.wake_block, "wake_block")(granted_id, "Pool_Available", kind)

    def _request_pm_resource(self, sim_time: float) -> Generator[float | Wait, Wake | None, _Turn | float]:
        """Request the PM's resources from the shared pool (`ObtainPMprereqs`): take them now, or hold in PMhold until the
        grant lands, or defer (pmReqDefer) and hand back to `Run`. On a pass
        that follows an asynchronous grant (`pm.prereqs_secured`), just log the
        resources obtained.

        Returns `_Turn.LOOP` when the PM attempt was deferred, or the time the
        resources were in hand.
        """
        block = self.block
        scheduler = self.scheduler
        logs = self.logs
        cost_analysis = self.cost_analysis
        events_file = self.events_file
        resource_pool = require(self.resource_pool, "resource_pool")  # checked by the caller
        pool_stats = self.pool_stats
        # Port of `ObtainPMprereqs`'s resource-wait
        # -- the same `resPoolNum`/
        # `PooledSpares` pool as ordinary Repair's resource borrow
        # above, so this mirrors that wait-then-resume pattern:
        # `request()`, and if not immediately granted, hold
        # (`PMhold`, matching source's `ChangeBlockState(PMhold,
        # activeStatus, "pmReqDefer")`) and suspend on a far-future
        # yield until `release()` interrupts with "Pool_Available".
        if not self.pm.prereqs_secured:
            pm_res_granted = resource_pool.request(block.id, block.pm_resources_needed, kind="pm")
            if pm_res_granted and cost_analysis and pool_stats is not None:
                # Raptor 7 -- PM's own per-use borrow
                # charge (numRes1PM units).
                pool_stats.es_cost += _res_fixed_per_use(block) * float(block.pm_resources_needed)
            pm_res_waiting = not pm_res_granted
            if pm_res_waiting and block.pm_req_defer:
                # Port of `ObtainPMprereqs`'s other "resource not
                # available" branch: when
                # `pmReqDefer` is set, source does not hold in
                # `PMhold` -- it logs a hand-built
                # `WriteEvents(...,"pmReqDefer","No_resource_in_
                # stock-"+poolName)` line and calls `Run;` again.
                # `request()` above already registered this block in
                # the pool's FIFO queue, matching source's
                # `WAIT FOR PooledSpares[resPoolNum] TO Give(...)`,
                # which still runs even on the deferred path. This
                # port models the net effect as "abandon this PM
                # attempt, resume ordinary running with
                # `FailTimeLeft` untouched" (`pm.resource_pending`).
                #
                # Source's line is gated `IF ((NOT FailWait) AND
                # (NOT ZeroWait))` : on the async
                # continuation after a deferred PM SPARE arrives
                # (`pm.spare_pickup_pass`), the block's Run is back
                # in a wait, so no line (PM_Prereq runs 4/7).
                if events_file and not self.pm.spare_pickup_pass:
                    logs.append(format_event_log_line(
                        sim_time, "Block", block.name, "pmReqDefer",
                        f"No_resource_in_stock-{block.resource_pool_name}",
                    ))
                # `+= 1`, not `= True`: a second deferral before the
                # first is reconciled must not forget the first is
                # still outstanding.
                self.pm.resource_pending += 1
                self._abandon_pm_attempt()
                # Port of source's "calls `Run;` AGAIN directly" -- a
                # separate scheduling boundary from a plain loop
                # `continue`, matching the TELL-boundary pattern
                # this function's sibling PM-obtain hops pay.
                # Deliberately not setting `block.run_state.fail_wait`/
                # `zero_wait` for this yield: `_induce_dependency`'s
                # gate then treats a block suspended here as not
                # genuinely fail/zero-waiting yet, matching source's
                # `IF FailWait ... ELSIF ZeroWait ...` gate having
                # nothing to match during this extra round-trip.
                # Any dependency-driven state change still applies
                # via `block.op_status`, re-read once the
                # `continue` reaches the loop top.
                if not self.pm.spare_pickup_pass:
                    # (Not after an asynchronous spare pickup: `Run` is back in its wait and there is no extra
                    # round-trip -- real-traced `LadL8Cab` run 63 t=3000, S7B1's spare wake queues nothing.)
                    yield 0.0
                return _Turn.LOOP
            if pm_res_waiting and block.op_status != BlockStatus.PM_HOLD:
                self._transition(BlockStatus.PM_HOLD, block.active_status, "pmReqDefer", sim_time)
            if not pm_res_granted:
                resume = yield _FAR_FUTURE
                self._observe_wake(WaitSite.PM_HOLD_RESOURCE, resume)
                expect_wake(resume, "Pool_Available")
                if cost_analysis and pool_stats is not None:
                    pool_stats.es_cost += _res_fixed_per_use(block) * float(block.pm_resources_needed)
                sim_time = scheduler.time
            self.hops.pm_async_pickup = bool(pm_res_waiting)
            if pm_res_waiting and events_file:
                logs.append(format_event_log_line(
                    sim_time, "Block", block.name, "Resources", f"Obtained_{block.pm_resources_needed}_Resource(s)",
                ))
        else:
            # Port of `ObtainPMprereqs`'s `IF WaitingForResource:
            # ... log Obtained` -- reached via the async
            # "Pool_Available" pickup at whichever ordinary wait
            # site the block was at when its deferred request was
            # granted. `pm.prereqs_secured` was already true, so
            # skip straight to the "resource in hand" tail.
            self.pm.prereqs_secured = False
            self.hops.pm_async_pickup = True
            if events_file:
                logs.append(format_event_log_line(
                    sim_time, "Block", block.name, "Resources", f"Obtained_{block.pm_resources_needed}_Resource(s)",
                ))
        return sim_time

    def _perform_pm(self, sim_time: float) -> Generator[float | Wait, Wake | None, _Turn]:
        """Source's `PerformPM`, started by
        `ObtainPMprereqs`'s `TELL PerformPM`: the TELL boundary, the PM
        itself, then "Maintenance Completed". Returns `_Turn.LOOP`, or
        `_Turn.STOP` when the run ends during the PM.
        """
        block = self.block
        rng_repair = self.rng_repair
        scheduler = self.scheduler
        run_length = self.run_length
        logs = self.logs
        events_file = self.events_file
        resource_pool = self.resource_pool
        system_state = self.system_state
        if not self.hops.pm_handback_taken or (self.hops.pm_async_pickup and not self.hops.handback_from_pmhold):
            # `ObtainPMprereqs` ends in `TELL PerformPM`: the PM body
            # starts as its own selection at the tail, after every entry
            # already queued -- in particular a TELL spawned earlier in
            # the same turn (Custom-sparing `OrderStock`). The
            # resource-pool path crosses this boundary at the
            # unconditional hand-back hop above; the Custom-spare /
            # no-resource path fell straight into `beginning_PM`
            # inline.
            block.run_state.waiting_for_pm_prerequisites = True
            block.run_state.pm_tell_pending = True
            resume = yield 0.0
            self._observe_wake(WaitSite.PERFORM_PM_TELL, resume)
            block.run_state.pm_tell_pending = False
            block.run_state.waiting_for_pm_prerequisites = False
            if isinstance(resume, Wake):
                if (resume.reason not in STATE_ALREADY_APPLIED
                        and resume.reason != InterruptReason.TRIGGERED_PM_DUE):
                    raise UnexpectedWake(resume)  # surface any interrupt this hop newly exposes
            sim_time = scheduler.time
        self.hops.pm_handback_taken = False
        self.hops.pm_async_pickup = False
        self._transition(BlockStatus.PM, block.active_status, "beginning_PM", sim_time)

        maint_time = rng_repair.draw_number(Distribution(block.pm_dist), block.pm_params)
        if scheduler.time + maint_time >= run_length:
            yield run_length - scheduler.time
            self._transition(BlockStatus.DONE, block.active_status, "End_sim", scheduler.time, cascade=False)
            return _Turn.STOP
        yield maint_time
        sim_time = scheduler.time

        # Port of Raptor 7's resource `TakeBack`, right
        # after the PM wait completes (not inside `ObtainPMprereqs`,
        # which only obtains it). Same `wake_ids`-deferred-wake pattern
        # as Repair's resource release.
        pm_pending_wake_ids: list[int] = []
        pm_pending_wake_kinds: dict[int, str] = {}
        if block.uses_pm and block.resource_type_count == 1 and block.pm_resources_needed > 0:
            pm_pending_grants = require(resource_pool, "resource_pool").release_kinds(block.pm_resources_needed)
            pm_pending_wake_ids = [bid for bid, _kind in pm_pending_grants]
            pm_pending_wake_kinds = dict(pm_pending_grants)

        if events_file:
            logs.append(format_event_log_line(sim_time, "Block", block.name, "PM_complete", f"MaintTime={maint_time:.6f}"))
            # `PerformPM` completing restarts the frequency clock --
            # not `_pm_race`. The next race hits the `<= 0` refill and
            # takes a fresh `pm_freq`.
            self.clock.pm_time_left = 0.0
            # `spareObtained:=FALSE` -- this
            # maintenance is over, the next one obtains its own spare.
            block.run_state.spare_obtained = False
        # Port of `PerformPM`'s `CompTimeToDoPM:=maintTime`
        # (`LMONITORED` into `CompPMMon`) --
        # feeds `MTBMs`/`MMTs` (`reports.py`).
        if system_state is not None:
            record_component_pm(system_state, maint_time)

        # Port of Raptor 7's `IF pmRefresh:
        # FailTimeLeft:=0.0` -- ELSE leaves `FailTimeLeft` untouched,
        # so the block resumes with the same remaining
        # time-to-failure it had before PM started. The remaining
        # value reaches this point by one of two routes:
        # - TRIGGERED PM: the `Triggered_PM_Due` interrupt branch only
        #   decrements `clock.fail_time_left` (never sets it to None), so
        #   the paused value is still in the variable.
        # - FREQUENCY-RACED PM: the PM won `_pm_race`, and the
        #   natural-expiry resolution consumed the countdown
        #   (`clock.fail_time_left = None` plus `clock.operating_time +=
        #   wait_duration`) before `if pm.due:` dispatched, so restore
        #   the raced remainder from `clock.race_raw_life - clock.operating_time`
        #   (`PerformPM`'s own duration does not count as operating
        #   time).
        if block.pm_refresh:
            self.clock.fail_time_left = None
            # A still-pending start-condition life IS source's
            # FailTimeLeft (a block Cut/Idle from t=0 that PMs before
            # its first redraw), so the refresh discards it too.
            self.initial_fail_time_left = None
        elif self.clock.fail_time_left is None and self.clock.race_raw_life is not None:
            self.clock.fail_time_left = self.clock.race_raw_life - self.clock.wait_duration * self.clock.stress
        # The parked whole life is spent: left set, the next pass's race would take it, not the remainder.
        self.clock.race_raw_life = None
        # Port of `PerformPM`'s `DetermineNewState("Maintenance
        # Completed")` -- the same dependency-aware
        # dispatch `Repair`'s completion uses below (see
        # `_apply_maintenance_completed`), not an unconditional Running
        # transition.
        self._apply_maintenance_completed(sim_time, pm_pending_wake_ids, pm_pending_wake_kinds)
        # `PerformPM` ends in the same `DetermineNewState("Maintenance
        # Completed")` -> bare `Run;` as `Repair`, so it crosses the
        # same boundary.
        self.hops.run_hop_taken = True
        yield 0.0
        return _Turn.LOOP

    def _check_for_spare(self, sim_time: float) -> Generator[float | Wait, Wake | None, SpareRequest | None]:
        """Source's `CheckForSpare`, as
        `DetermineNewState("Block Has Failed")` calls it before routing to
        `Repair`: log an empty stock, spawn an emergency order, or end the
        block's life when it can never get a spare.

        Returns the repair's `SpareRequest`, or `None` when the block went
        `Done` (`No_spares`) and has waited out the run.
        """
        block = self.block
        scheduler = self.scheduler
        run_length = self.run_length
        # DetermineNewState calls CheckForSpare
        # unconditionally first, then routes Done vs Repair:
        # `repairDistro=18` (Done "No_repair") before `sparesOut` (Done
        # "No_spares"), mutually exclusive. So a No_spare_in_stock line can
        # precede Done No_repair (ER3-05, NoRepairCheckForSpare*Probe).

        if block.sparing_type == SparingType.NONE:
            # Port of CheckForSpare's `sparingType=None` branch
            #: `sparesOut:=TRUE; spareAvail:=FALSE`
            # -- DetermineNewState then routes straight to
            # `ChangeBlockState(Done,...,"No_spares")` with no repair.
            self._transition(BlockStatus.DONE, block.active_status, "No_spares", sim_time)
            yield run_length - scheduler.time
            self._transition(BlockStatus.DONE, block.active_status, "End_sim", scheduler.time, cascade=False)
            return None

        # Port of CheckForSpare's Custom branch --
        # same Part-A/Part-B split as SparePool below (check+log+ESO-spawn
        # here, before Pre-LDT; the actual wait-for-stock happens in Part B,
        # after Pre-LDT). No eager decrement here, matching CheckForSpare's
        # read-only nature; `spare.custom_was_empty` threads to Part B.
        # A spare still held from a PM attempt that pmReqDefer'd on its
        # resource: Repair's `IF (NOT spareObtained)` 
        # skips the spare step, so it is neither consumed nor counted again
        # (ReleaseProbe31 run 3 EOR: Attitude Infinite, `Consumed 1`).
        spare = SpareRequest(held=block.run_state.spare_obtained)
        # `DetermineNewState("Block Has Failed")`'s `IF (NOT spareObtained):
        # CheckForSpare`: a block already holding a
        # spare (taken by a PM attempt that then pmReqDefer'd on its
        # resource) skips the check -- and `Repair` skips its spare step on
        # the same flag and repairs with the held spare
        # (DepCancelConservationProbe t=65).
        if block.sparing_type == SparingType.CUSTOM and not block.run_state.spare_obtained:
            if (yield from self._check_custom_stock(sim_time, spare)):
                return None

        # Port of CheckForSpare's SparePool branch, Part A only
        # -- called from DetermineNewState BEFORE
        # the `repairDistro=18`/`sparesOut` Done check below (source calls
        # CheckForSpare unconditionally, then decides Done vs Repair), so
        # this must run here, same position as Custom's own Part A above --
        # NOT after the NONE check the way this used to be ported (ER3-05).
        # A `NoRepairCheckForSparePooledProbe` Raptor 7 capture (2026-09-23)
        # confirms: `Sparing No_spare_in_stock-<pool>` (this block) logs
        # before `Done No_repair`, exactly like the Custom branch.
        # `resources_was_zero`/`spare.emer_ordered` thread to Part B below
        # (the early opportunistic grab, which IS Repair-body work and
        # correctly stays gated behind the NONE check -- no Repair, no
        # grab). No eager decrement here either, matching CheckForSpare's
        # read-only nature.
        if block.sparing_type == SparingType.SPARE_POOL:
            if (yield from self._check_pool_stock(sim_time, spare)):
                return None
        return spare

    def _check_custom_stock(self, sim_time: float, spare: SpareRequest) -> Generator[float | Wait, Wake | None, bool]:
        """`CheckForSpare`'s Custom arm: log an empty
        private stock, order an emergency spare, or end the block's life when
        the stock can never refill. Records the empty stock on `spare`.

        Returns True when the block went `Done` and has waited out the run.
        """
        block = self.block
        stats = self.stats
        scheduler = self.scheduler
        run_length = self.run_length
        logs = self.logs
        cost_analysis = self.cost_analysis
        events_file = self.events_file
        wake_block = self.wake_block
        self.custom_spares = require(self.custom_spares, "custom_spares")
        spare.custom_was_empty = self.custom_spares.resources == 0
        if spare.custom_was_empty:
            if events_file:
                logs.append(format_event_log_line(
                    sim_time, "Block", block.name, "Sparing", "No_spare_in_stock-Custom",
                ))
            if block.emergency_spare_ordering:
                # `tell`, not `schedule_process`: the real
                # `TELL METHOD OrderASpare`'s startup line must land inside
                # this block's own current turn.
                # Deferred (`deferred=True`) -- a real TELL start. Source's
                # `CheckForSpare` runs before the `Repair;` spawn and this
                # port's `_repair_seam` sits after it, so OrderASpare
                # starts (creation order) before Repair's work:
                # `Spare_ordered` precedes the Pre-LDT row.
                scheduler.tell(order_a_custom_spare_process(
                    block, self.custom_spares, scheduler, require(wake_block, "wake_block"), logs, events_file=events_file,
                    block_stats=stats if cost_analysis else None,
                ), block_id=block.id, deferred=True)
            elif not (block.routine_spare_ordering or block.stock_level_ordering):
                # Permanently out (no ESO, no RSO, no SLO). SLO alone
                # (RSO=ESO=0) is also a valid "don't give up" condition:
                # the reorder was already placed by whichever consumption
                # first brought the pool down to `slo_order_level` (Part
                # B's SLO check below); a later block that finds the pool
                # still empty has nothing new to order and just waits like
                # the RSO case, falling through to the ordinary
                # `custom_spares.request(...)` wait below.
                self._transition(BlockStatus.DONE, block.active_status, "No_spares", sim_time)
                yield run_length - scheduler.time
                self._transition(BlockStatus.DONE, block.active_status, "End_sim", scheduler.time, cascade=False)
                return True
        return False

    def _check_pool_stock(self, sim_time: float, spare: SpareRequest) -> Generator[float | Wait, Wake | None, bool]:
        """`CheckForSpare`'s SparePool arm: log an
        empty pool, order an emergency spare, or end the block's life when the
        pool can never refill. Records an emergency order on `spare`.

        Returns True when the block went `Done` and has waited out the run.
        """
        block = self.block
        scheduler = self.scheduler
        run_length = self.run_length
        logs = self.logs
        events_file = self.events_file
        spare_pool = self.spare_pool
        spare_pool_config = self.spare_pool_config
        spawn_order_a_spare = self.spawn_order_a_spare
        spare_pool = require(spare_pool, "spare_pool")
        spare_pool_config = require(spare_pool_config, "spare_pool_config")
        resources_was_zero = spare_pool.resources == 0
        if resources_was_zero:
            if events_file:
                logs.append(format_event_log_line(
                    sim_time, "Block", block.name, "Sparing", f"No_spare_in_stock-{block.pool_name}",
                ))
            if spare_pool_config.emergency_spare_ordering:
                # Always orders (`CheckForSpare`). An earlier "ESO grace" (no order when a
                # same-instant grant had just emptied the pool) was an unsourced fit to five captures; `LadL4Web`
                # (real 2026-10-01) ordered in two same-instant cases the rule would have silenced (a PM grab
                # and a repair grab), and removing the rule moves no other capture (0 of 3,385 runs differ).
                if spawn_order_a_spare is not None:
                    spawn_order_a_spare(deferred=True)
                spare_pool.num_emer_ordered += 1
                spare.emer_ordered = True
            elif not (spare_pool_config.routine_spare_ordering or spare_pool_config.stock_level_ordering):
                # Permanently out -- same terminal Done/"No_spares" path
                # as Custom-sparing exhaustion above (no ESO, no RSO, no
                # SLO -- Raptor 7).
                self._transition(BlockStatus.DONE, block.active_status, "No_spares", sim_time)
                yield run_length - scheduler.time
                self._transition(BlockStatus.DONE, block.active_status, "End_sim", scheduler.time, cascade=False)
                return True
        else:
            if spare_pool.resources <= spare_pool.num_emer_ordered:
                # Consuming this spare dips into stock already earmarked
                # for an outstanding emergency order -- order another to
                # compensate. Uses the pre-grab
                # count, matching CheckForSpare's own read.
                if events_file:
                    logs.append(format_event_log_line(
                        sim_time, "Block", block.name, "Sparing", f"Used_Ordered_Spare-{block.pool_name}",
                    ))
                if spawn_order_a_spare is not None:
                    spawn_order_a_spare(deferred=True)
        return False

    def _wait_for_spare(self, sim_time: float, spare: SpareRequest) -> Generator[float | Wait, Wake | None, float]:
        """`Repair`'s spare step after Pre-LDT (for Custom for a spare pool): wait for the spare if
        there is none, apply stock-level re-orders, and count the spare
        used (`INC(SparesUsed[seqNum])`).

        Returns the time the spare was in hand.
        """
        block = self.block
        stats = self.stats
        cost_analysis = self.cost_analysis
        # Repair's own Custom-sparing consumption (Raptor 7,
        # `WHEN Custom` under `CASE startCond WHEN "Off"`) -- the same
        # Part-A/Part-B split as SparePool below (ESO order already spawned in
        # Part A if needed; this is the blocking wait). No SLO here -- raised
        # upfront if configured, see the guard near this function's start.
        if block.sparing_type == SparingType.CUSTOM:
            sim_time = yield from self._wait_for_custom_spare(sim_time, spare)

        # Repair's own SparePool consumption --
        # `opStatus != RepHold` is false whenever Pre-LDT just ran (the block
        # is already RepHold), so no second "Waiting_for_Spare" line appears.
        if block.sparing_type == SparingType.SPARE_POOL:
            sim_time = yield from self._wait_for_pool_spare(sim_time, spare)

        # Port of `INC(SparesUsed[seqNum])` -- many near-identical call sites
        # across Raptor 7's Custom/SparePool/Infinite consumption branches
        # converge here: every sparing-type branch above has already either
        # obtained its spare or needs none tracked. `spare.obtained_early`:
        # the SparePool early grab above already counted its unit,
        # so skip it here.
        if not spare.obtained_early and not spare.held:
            block.run_state.spares_used += 1
            if cost_analysis and block.sparing_type == SparingType.INFINITE:
                # Raptor 7 -- the sparingType CASE's
                # Infinite branch charges `spareCost` per consumption; Custom
                # paid at order time (pool.py's custom RSO/ESO processes),
                # pooled pays never (block shows N/A).
                stats.sp_cost += block.spare_cost
        return sim_time

    def _wait_for_custom_spare(self, sim_time: float, spare: SpareRequest) -> Generator[float | Wait, Wake | None, float]:
        """`Repair`'s Custom-sparing consumption:
        wait on the block's private Custom queue if it is empty, then apply
        the stock-level re-order. Returns the time the spare was in hand.
        """
        block = self.block
        scheduler = self.scheduler
        logs = self.logs
        events_file = self.events_file
        self.custom_spares = require(self.custom_spares, "custom_spares")
        if spare.custom_was_empty and block.op_status != BlockStatus.REP_HOLD:
            self._transition(BlockStatus.REP_HOLD, block.active_status, "Waiting_for_Spare", sim_time)

        # ER3-04: withdraw a stale, abandoned PM-side Custom-spare
        # request before this block queues its OWN (unrelated) failure
        # request -- same reasoning and shape as the shared resource
        # pool's identical `pm.resource_pending`/`withdraw(...,"pm")`
        # pairing just below (search this file for "structurally dead").
        # Without this, the PM's own now-dead queue entry (kind="pm",
        # only just made findable by the matching tag added at its
        # request site above) sits in `custom_spares`'s queue and can
        # intercept a later spare arrival that this Repair-path request
        # should get, or vice versa -- see that sibling call site's
        # comment for the full failure mode this caused
        # (`DepCancelConservationProbe`, gdb-trace-confirmed).
        if self.pm.spare_pending and self.custom_spares.withdraw(block.id, "pm"):
            self.pm.spare_pending -= 1
        if block.run_state.spare_obtained:
            pass  # `Repair`'s `IF (NOT spareObtained)` : use the held spare
        elif not self.custom_spares.request(block.id, 1):
            resume = yield _FAR_FUTURE
            self._observe_wake(WaitSite.REPAIR_CUSTOM_SPARE, resume)
            expect_wake(resume, "Pool_Available")
            sim_time = scheduler.time

        if spare.custom_was_empty and events_file:
            logs.append(format_event_log_line(sim_time, "Block", block.name, "Sparing", "Spare_Arrived"))

        # Port of Repair's own Custom-branch SLO re-order check -- mirrors
        # the SparePool one below. The order line lands after this block's
        # Repairing because the spawner defers (`schedule_process`).
        if block.stock_level_ordering:
            if self.custom_spares.resources == block.slo_order_level:
                slo_qty = block.slo_new_spares
            elif self.custom_spares.resources <= block.slo_order_level - block.slo_new_spares:
                slo_qty = 1
            else:
                slo_qty = 0
            if slo_qty > 0:
                # Deferred via schedule_process inside the spawner --
                # order line lands after this block's Repairing.
                self._spawn_custom_stock_order(slo_qty)
        return sim_time

    def _wait_for_pool_spare(self, sim_time: float, spare: SpareRequest) -> Generator[float | Wait, Wake | None, float]:
        """`Repair`'s spare-pool consumption: use
        the spare the early grab took, or wait for one from the shared
        pool, then apply the stock-level re-order. Returns the time the
        spare was in hand.
        """
        block = self.block
        scheduler = self.scheduler
        logs = self.logs
        events_file = self.events_file
        resource_pool = self.resource_pool
        spare_pool = self.spare_pool
        spare_pool_config = self.spare_pool_config
        spawn_order_stock = self.spawn_order_stock
        spare_pool = require(spare_pool, "spare_pool")
        spare_pool_config = require(spare_pool_config, "spare_pool_config")
        if spare.obtained_early:
            # Port of `Repair`'s "ELSE {spare already obtained}" branch
            # -- the early grab above already
            # consumed the unit, so this site just applies whatever SLO
            # re-order that grab decided (`spare.pool_slo`, 0 if none needed).
            # No wait or "Spare_Arrived"/"Waiting_for_Spare" lines, which
            # belong only to the "didn't get it early" path below.
            if spare.pool_slo > 0 and spawn_order_stock is not None:
                spawn_order_stock(spare.pool_slo)
        else:
            # `IF (PooledSpares[..].Resources=0)` re-read here
            # after Pre-LDT -- NOT CheckForSpare's earlier reading:
            # the pool can have gained or lost stock since.
            waiting_for_spare = spare_pool.resources == 0
            if waiting_for_spare and block.op_status != BlockStatus.REP_HOLD:
                self._transition(BlockStatus.REP_HOLD, block.active_status, "Waiting_for_Spare", sim_time)

            # Same stale-`pmReqDefer`-entry withdrawal as the resource
            # pool, applied to the spare pool. A genuinely new failure
            # makes it structurally impossible for this block to still
            # want the PM spare it deferred on, and real discards that
            # request outright rather than leaving it queued. Otherwise
            # `ResourceQueue`'s strict FIFO lets the stale PM entry --
            # earlier in the queue than this block's new repair one -- eat
            # a later arrival that belongs to somebody else.
            if self.pm.spare_pending and self.custom_spares is not None and self.custom_spares.withdraw(block.id, "pm"):
                self.pm.spare_pending -= 1

            if self.pm.spare_pending and spare_pool.withdraw(block.id, "pm"):
                self.pm.spare_pending -= 1
                self.pm.spare_emer_pending = min(self.pm.spare_emer_pending, self.pm.spare_pending)

            if (self.pm.resource_pending and resource_pool is not None
                    and resource_pool.withdraw(block.id, "pm")):
                self.pm.resource_pending -= 1

            if not spare_pool.request(block.id, 1):
                # Source's `WAIT FOR PooledSpares[resPoolNum] TO Give`
                # is bound to this pool object, so a
                # grant on a different pool (e.g. the PM-prereq grant on
                # the resource pool, or a repair resource grant) must not
                # resume this wait even though every pool wake arrives as
                # the same generic "Pool_Available" reason. The interrupt's
                # `token` plus the spare queue's ground truth say whether
                # the wake is actually our repair spare.
                spare_q_before = spare_pool.count_waiting(block.id)
                while True:
                    resume = yield _FAR_FUTURE
                    self._observe_wake(WaitSite.REPAIR_POOL_SPARE, resume)
                    token = expect_wake(resume, "Pool_Available").token
                    if token == "pm":
                        # A PM-prereq grant (source's separate
                        # `ObtainPMprereqs` process) woke this repair wait.
                        # Reconcile the outstanding PM counter and keep
                        # waiting for our own spare.
                        self._claim_pm_grant()
                        continue
                    if spare_pool.count_waiting(block.id) < spare_q_before:
                        break  # one of OUR spare requests was granted
                    # Token was repair/None but our own request is still
                    # queued: this wake belonged to another outstanding
                    # request of this block, not our spare.
                sim_time = scheduler.time

            if spare.emer_ordered:
                spare_pool.num_emer_ordered -= 1

            # Raptor 7 puts this SLO re-order before the
            # "Spare_Arrived" line below, but the real order is
            # "Spare_Arrived" first, then "...Spare(s)_ordered...":
            # `TELL PooledSpares[...] TO OrderStock(...)` (targeting a pool
            # object) does not run its body synchronously up to
            # `OrderStock`'s first `WriteEvents` the way block/link TELLs
            # do.
            if waiting_for_spare and events_file:
                logs.append(format_event_log_line(sim_time, "Block", block.name, "Sparing", "Spare_Arrived"))

            if spare_pool_config.stock_level_ordering:
                if spare_pool.resources == spare_pool_config.slo_order_level:
                    if spawn_order_stock is not None:
                        spawn_order_stock(spare_pool_config.slo_new_spares)
                elif spare_pool.resources <= spare_pool_config.slo_order_level - spare_pool_config.slo_new_spares:
                    if spawn_order_stock is not None:
                        spawn_order_stock(1)
        return sim_time

    def _pre_ldt(self, sim_time: float, start_val: float | None) -> Generator[float | Wait, Wake | None, float | None]:
        """`Repair`'s pre-LDT (logistics delay) wait before the spare
        step: a drawn delay in RepHold, or a preLDT down-start's partial
        wait. A "repairing" down-start has none.

        Returns the time the delay ended, or `None` when the run ended
        during it.
        """
        block = self.block
        rng_repair = self.rng_repair
        scheduler = self.scheduler
        run_length = self.run_length
        logs = self.logs
        events_file = self.events_file
        # `start_val is None` gate: source's down-start `CASE startCond` arms
        # never reach `WHEN "Off"`'s pre-LDT draw+wait -- the "repairing" arm
        # goes straight to the spare/resource step.
        # (The preLDT arm's partial wait is the branch just above; the
        # postLDT arm never gets here -- see `down_start.partial_pre_ldt`.)
        if self.down_start.partial_pre_ldt is not None:
            # The "preLDT" down-start's partial wait.
            # Source logs `startVal` (the repair time about to be spent), not
            # the `firstPreLDT` it waited: a quirk, kept.
            ldt_wait, self.down_start.partial_pre_ldt = self.down_start.partial_pre_ldt, None
            if scheduler.time + ldt_wait >= run_length:
                yield run_length - scheduler.time
                self._transition(BlockStatus.DONE, block.active_status, "End_sim", scheduler.time, cascade=False)
                return None
            yield ldt_wait
            sim_time = scheduler.time
            if events_file:
                logs.append(format_event_log_line(
                    sim_time, "Block", block.name, "End_Partial_pre-LDT", f"Time={require(start_val, 'start_val'):.6f}"))
        elif start_val is None and _has_ldt(block.pre_dist, block.pre_params):
            if block.op_status != BlockStatus.REP_HOLD:
                # `IF opStatus<>RepHold`: a block released from the defer trigger's
                # hold is already in RepHold, and logs nothing here.
                self._transition(BlockStatus.REP_HOLD, block.active_status, "LDT", sim_time)
            ldt_wait = rng_repair.draw_number(Distribution(block.pre_dist), block.pre_params)
            if scheduler.time + ldt_wait >= run_length:
                yield run_length - scheduler.time
                self._transition(BlockStatus.DONE, block.active_status, "End_sim", scheduler.time, cascade=False)
                return None
            yield ldt_wait
            sim_time = scheduler.time
            if events_file:
                logs.append(format_event_log_line(sim_time, "Block", block.name, "End_Pre-LDT", f"Time={ldt_wait:.6f}"))
        return sim_time

    def _obtain_repair_resource(self, sim_time: float) -> Generator[float | Wait, Wake | None, tuple[float, str]]:
        """`Repair`'s resource step: borrow the
        repair's resources from the shared pool, waiting in RepHold when
        they are not free.

        Returns the time the resources were in hand and the comment for the
        `Repairing` line (`No_resources_used`, or the number and pool name).
        """
        block = self.block
        scheduler = self.scheduler
        logs = self.logs
        cost_analysis = self.cost_analysis
        events_file = self.events_file
        resource_pool = self.resource_pool
        pool_stats = self.pool_stats
        # Port of Repair's `IF numDiffRes=1` resource-borrowing branch
        # -- a shared, fixed-capacity pool
        # (RESOURCE_DATA) borrowed for the duration of repair and returned via
        # `release()`, orthogonal to `sparing_type`.
        #
        # Only `numDiffRes=1` borrows: every resource action in source tests
        # `numDiffRes=1` exactly, so `NumRes=2` behaves as no resource use, with
        # or without a configured pool. NumRes2PoolProbe's Raptor 7 capture shows
        # it: two blocks with NumRes=2 on a one-unit pool repair at the same
        # instant, `No_resources_used`, neither waiting.
        repair_comment = "No_resources_used"
        if block.resource_type_count == 1:
            if resource_pool is None:
                raise EngineInvariantError(
                    f"block requires resource pool {block.resource_pool_name!r} (repair_resources_needed="
                    f"{block.repair_resources_needed}) but no resource_pool was provided to block_run_process"
                )
            # Port of Raptor 7's two-part condition
            # (`PooledSpares[resPoolNum].Resources<numRes1`) OR
            # `ReportNumberPending>0`) -- not just a raw resource-count check.
            # A raw count check misses the case where enough units exist now
            # but another block is already queued ahead: `ResourceQueue`'s
            # strict-FIFO `request()` queues this block too, but a raw count
            # would wrongly report "not waiting", suppressing the
            # "Obtained_N_Resource(s)" line. `request()`'s grant test is the
            # logical negation of source's combined condition.
            #
            # A still-queued, stale `pmReqDefer`'d PM request (this block's
            # `pm.resource_pending`) is structurally dead: this block is about
            # to enter REPAIRING for a new, unrelated failure. Withdraw it
            # here: real discards an abandoned PM request permanently the
            # instant a new failure supersedes it. Otherwise `ResourceQueue`'s
            # strict FIFO can let an unrelated release satisfy the stale entry
            # first, wrongly reviving the abandoned PM later.
            if self.pm.resource_pending and resource_pool.withdraw(block.id, "pm"):
                self.pm.resource_pending -= 1
            # Source's `WAIT FOR PooledSpares[resPoolNum] TO Give`
            # yields even when the unit is about to be
            # released by an entry already queued at this instant, so a
            # sibling's same-instant release is observed before this block's
            # own `Resources<numRes1` check runs -- no `RepHold`. Give those
            # entries their turn first; guarded on a pending same-instant entry.
            if (resource_pool.resources < block.repair_resources_needed
                    and resource_pool.release_due_at(scheduler.time)):
                resume = yield 0.0
                self._observe_wake(WaitSite.REPAIR_RESOURCE_HOP, resume)
            granted = resource_pool.request(block.id, block.repair_resources_needed)
            if granted and cost_analysis and pool_stats is not None:
                # Raptor 7 (the early-grab twin charge
                # identically on their own paths) -- RESOURCE pool per-use
                # cost at every successful borrow.
                pool_stats.es_cost += _res_fixed_per_use(block) * float(block.repair_resources_needed)
            waiting_for_resource = not granted
            if waiting_for_resource and block.op_status != BlockStatus.REP_HOLD:
                self._transition(BlockStatus.REP_HOLD, block.active_status, "Waiting_for_resource(s)", sim_time)
            if not granted:
                resume = yield _FAR_FUTURE
                self._observe_wake(WaitSite.REPAIR_RESOURCE, resume)
                expect_wake(resume, "Pool_Available")
                if cost_analysis and pool_stats is not None:
                    # Same per-use charge as the immediate-grant case above --
                    # the borrow succeeded, just later.
                    pool_stats.es_cost += _res_fixed_per_use(block) * float(block.repair_resources_needed)
                # The grant happens synchronously inside another block's event
                # step (that block's `release()` call), at whatever
                # `scheduler.time` is now -- not this block's own pre-wait
                # `sim_time`. Refresh it, or later lines get the wrong
                # timestamp.
                sim_time = scheduler.time
            # "WAIT DURATION 0.0" (Raptor 7, "to get X to jive
            # with previous Raptors") -- a zero-length re-insertion into the
            # scheduler's tie-break ordering, ported literally since
            # simultaneous-event insertion order is significant elsewhere.
            yield 0.0
            if waiting_for_resource and events_file:
                logs.append(format_event_log_line(
                    sim_time, "Block", block.name, "Sparing", f"Obtained_{block.repair_resources_needed}_Resource(s)",
                ))
            repair_comment = f"{block.repair_resources_needed}{block.resource_pool_name}"
        return sim_time, repair_comment

    def _repair_wait(self, repair_time: float) -> Generator[float | Wait, Wake | None, float | None]:
        """`Repair`'s `WAIT DURATION TimeToRepair`,
        with `RepWait` set around it. A stale PM resource grant landing
        during the wait is absorbed and the wait resumed.

        Returns the time the repair finished, or `None` when the run ended
        first.
        """
        block = self.block
        scheduler = self.scheduler
        run_length = self.run_length
        resource_pool = self.resource_pool
        wake_block = self.wake_block
        # Port of `Repair`'s `RepWait:=TRUE`, right
        # before its `WAIT DURATION TimeToRepair` -- `change_block_state`'s
        # `cost_analysis` branch logs a
        # "Partial_Repair" line whenever a transition away from `Repairing`
        # happens while this is still True (the repair never reached its
        # natural completion, the only place it is cleared). See
        # `blocks.change_block_state`.
        block.run_state.rep_wait = True

        # A stale, abandoned PM resource request (pmReqDefer's
        # "abandon this PM attempt" path, `pm.resource_pending`) can be
        # granted by an unrelated sibling's release while this block is
        # genuinely mid-repair. The request was never withdrawn from the
        # pool's queue, so it can land at any later point. Absorb the grant
        # here with the same ground-truth-count check the ordinary fail-wait
        # loop uses. Looped since more than one stale grant could land during
        # one repair.
        remaining_repair = repair_time
        while True:
            if scheduler.time + remaining_repair >= run_length:
                yield run_length - scheduler.time
                self._transition(BlockStatus.DONE, block.active_status, "End_sim", scheduler.time, cascade=False)
                return None
            wait_start = scheduler.time
            resume = yield remaining_repair
            self._observe_wake(WaitSite.REPAIR, resume)
            if resume is None:
                break
            expect_wake(resume, "Pool_Available")  # during the repair wait
            _granted_kind = self._claim_pm_grant()
            if _granted_kind is None:
                raise EngineInvariantError(
                    f"Pool_Available during the repair wait with nothing to reconcile: {resume!r}")
            if _granted_kind == "spare":
                # See `_claim_pm_grant`: the spare is in hand, the resource
                # wait is still ahead. Nothing else to do at this wait -- the
                # PM retry picks it up from `block.run_state.spare_obtained`.
                block.run_state.spare_obtained = True
            # The grant itself (`block.pm_resources_needed` units) was already
            # subtracted from `resource_pool.resources` by whichever
            # `release_kinds()` call satisfied this stale queue entry --
            # discarding it without touching the pool leaves those units
            # permanently unaccounted for. Return them and wake whichever
            # queued block(s) that now completes, same `wake_block` callback
            # the RSO/ESO/SLO background processes use.
            if resource_pool is not None and wake_block is not None:
                for granted_id, _kind in resource_pool.release_kinds(block.pm_resources_needed):
                    # Pass the grant's kind on like every other `wake_block`
                    # caller: an untagged wake reaching a waiter's PM
                    # hand-back hop was unclassifiable there (crashed
                    # ReleaseProbe27/32/35 with a cost sink).
                    wake_block(granted_id, InterruptReason.POOL_AVAILABLE, _kind)
            remaining_repair -= scheduler.time - wait_start
        sim_time = scheduler.time
        # Port of `Repair`'s `RepWait:=FALSE`, as the
        # repair timer completes naturally.
        block.run_state.rep_wait = False
        return sim_time

    def _enter_repair(self, sim_time: float, spare: SpareRequest, seam_pending: bool) -> Generator[float | Wait, Wake | None, float]:
        """Start `Repair` proper: cross the `Repair;` TELL boundary (unless
        this is a kickoff repair, `seam_pending` False), and for a spare-pool
        block make `Repair`'s early opportunistic grab,
        recording the result on `spare`.

        Returns the time `Repair` starts.
        """
        block = self.block
        stats = self.stats
        scheduler = self.scheduler
        cost_analysis = self.cost_analysis
        spare_pool = self.spare_pool
        spare_pool_config = self.spare_pool_config
        # `_repair_seam`: the `Repair;` TELL boundary. Only reached once
        # `repairDistro=18` has already been ruled out above, since a Done
        # route (SparePool's own "permanently out" return included) spawns
        # no Repair at all -- same reasoning as Custom's identically-placed
        # trigger just below for every OTHER sparing type.
        if seam_pending and block.sparing_type != SparingType.SPARE_POOL:
            seam_pending = False          # `_repair_seam`: the `Repair;` TELL starts here
            yield 0.0
            sim_time = scheduler.time
        # Port of `Repair`'s early opportunistic SparePool grab, Part B
        # -- `Repair` is a `TELL METHOD`, so it
        # runs synchronously up to its first `WAIT` when DetermineNewState's
        # "Block Has Failed" case spawns it, and tries to grab the spare
        # immediately, before Pre-LDT and before
        # `ChangeBlockState(RepHold,...,"LDT")` is logged. Separate from and
        # earlier than `Repair`'s later "didn't get it early" fallback
        # (below). No log line of its own.
        # `spare.obtained_early`/`spare.pool_slo` thread the result to the
        # second call site.
        if block.sparing_type == SparingType.SPARE_POOL:
            spare_pool = require(spare_pool, "spare_pool")
            spare_pool_config = require(spare_pool_config, "spare_pool_config")
            if seam_pending:
                seam_pending = False      # `_repair_seam`: the `Repair;` TELL starts here
                yield 0.0
                sim_time = scheduler.time
            # `Repair`'s own `IF (PooledSpares[..].Resources>0)`
            #  -- re-read HERE, after the seam, not CheckForSpare's
            # earlier `resources_was_zero`: another same-instant block can
            # take the last spare in between (the grab used to `assert` and
            # crashed ReleaseProbe27/30/33/35/38/39 once the seam was moved
            # before it), and a spare can equally have arrived.
            if spare_pool.resources > 0:
                # The request must run even under `python -O`, so it is not
                # an `assert` operand.
                if not spare_pool.request(block.id, 1):
                    raise EngineInvariantError(
                        f"block {block.name!r}: spare pool had stock but refused the request")
                spare.obtained_early = True
                if spare.emer_ordered:
                    # -- the early grab's own DecNumEmerOrdered.
                    spare_pool.num_emer_ordered -= 1
                    spare.emer_ordered = False
                # `INC(SparesUsed[seqNum])` happens here, at the grab
                # -- not after Pre-LDT with the other
                # sparing types. The difference is visible when the run ends
                # during that LDT. The shared increment below skips this case.
                block.run_state.spares_used += 1
                if cost_analysis and block.sparing_type == SparingType.INFINITE:
                    stats.sp_cost += block.spare_cost
                if spare_pool_config.stock_level_ordering:
                    if spare_pool.resources == spare_pool_config.slo_order_level:
                        spare.pool_slo = spare_pool_config.slo_new_spares
                    elif spare_pool.resources <= spare_pool_config.slo_order_level - spare_pool_config.slo_new_spares:
                        spare.pool_slo = 1
        return sim_time

    def _repair(self, start_val: float | None, sim_time: float) -> Generator[float | Wait, Wake | None, bool]:
        """Source's `Repair`, reached through
        `DetermineNewState` after a failure or at a down-start: check for a
        spare, pre-LDT, wait for spare and resources, repair, post-LDT, then
        "Maintenance Completed". Returns True when the block's life has ended
        (a `Done` route or the end of the run), False to loop back to `Run`.

        `start_val` is a down-start's repair time from `GetStartCond`, used
        in place of a fresh draw; `None` for an ordinary failure.
        """
        block = self.block
        rng_repair = self.rng_repair
        scheduler = self.scheduler
        run_length = self.run_length
        logs = self.logs
        events_file = self.events_file
        resource_pool = self.resource_pool
        system_state = self.system_state
        # Port of DetermineNewState's `Repair;` call being a TELL METHOD
        # -- a fresh process spawn queued behind every
        # other entry already waiting at this instant, while Run loops to its
        # top and re-suspends at a fresh fail-wait. This generator merges the
        # two, so an explicit zero-length boundary here lets same-instant
        # entries queued before this block's wake resolve first, producing
        # Raptor 7's interleaved [A Failed][B Failed][A RepHold][B RepHold]. The
        # resume is deliberately not dispatched as an interrupt here.
        #
        # The `TELL METHOD Repair` boundary. The requeue takes a fresh seq, so
        # the spawned Repair starts at the tail of the instant, behind every
        # entry already pending. Not for a kickoff repair (`start_val is not
        # None`): that block's kickoff barrier hop already is its `TELL Repair`
        # start. In source, `CheckForSpare` (and the `Done` routes) run in the
        # `Run` turn before the `Repair;` spawn, and a Done route spawns no
        # Repair, so the boundary is taken below via `_repair_seam`.
        seam_pending = start_val is None

        spare = yield from self._check_for_spare(sim_time)
        if spare is None:
            return True

        if block.repair_distro == int(Distribution.NONE):
            # Port of `DetermineNewState("Block Has Failed")`'s
            # `IF ((repairDistro=18) OR (sparesOut))`
            # placed after `CheckForSpare` (source calls CheckForSpare
            # first, both Custom and SparePool -- ER3-05): a
            # `repairDistro=18` block logs `Sparing No_spare_in_stock-<pool>`
            # and, if configured for ESO, `Spare_ordered_(stock=0)` before
            # `Done No_repair`, whatever its sparing type.
            self._transition(BlockStatus.DONE, block.active_status, "No_repair", sim_time)
            yield run_length - scheduler.time
            self._transition(BlockStatus.DONE, block.active_status, "End_sim", scheduler.time, cascade=False)
            return True

        sim_time = yield from self._enter_repair(sim_time, spare, seam_pending)

        ldt_end = yield from self._pre_ldt(sim_time, start_val)
        if ldt_end is None:
            return True
        sim_time = ldt_end

        sim_time = yield from self._wait_for_spare(sim_time, spare)

        sim_time, repair_comment = yield from self._obtain_repair_resource(sim_time)

        # Port of `Repair`'s `TimeToRepair:=startVal` under `CASE startCond
        # WHEN "repairing"` -- a kickoff
        # down-start does not draw here: `GetStartCond` already drew the
        # repair length (and, for types 1/4, scaled it by `rand_draw2` to
        # place the block part-way through that repair). `start_val` is None
        # on every ordinary pass.
        if start_val is not None:
            repair_time = start_val
            start_val = None
        else:
            repair_time = rng_repair.draw_number(Distribution(block.repair_distro), block.repair_values)
        self._transition(BlockStatus.REPAIRING, block.active_status, repair_comment, sim_time)
        # Record when this block's borrowed resource is due back, so a sibling
        # failing at that exact instant can observe the release (see the
        # repair-resource wait's re-yield guard above).
        if block.resource_type_count == 1 and resource_pool is not None:
            resource_pool.note_release_at(scheduler.time + repair_time)
        repaired_at = yield from self._repair_wait(repair_time)
        if repaired_at is None:
            return True
        sim_time = repaired_at

        # TakeBack happens before the "Repaired" line, matching the source's
        # statement order. `release()` itself is
        # synchronous; actually waking the blocks is deferred to `wake_ids=`
        # on the next `_transition` call below, because a woken waiter's
        # lines belong after this block's transition-and-cascade lines but
        # before its system-status check -- a slot only `_Cascade.apply` can
        # hit.
        pending_wake_ids: list[int] = []
        pending_wake_kinds: dict[int, str] = {}
        if block.resource_type_count == 1:
            pending_grants = require(resource_pool, "resource_pool").release_kinds(block.repair_resources_needed)
            pending_wake_ids = [bid for bid, _kind in pending_grants]
            pending_wake_kinds = dict(pending_grants)

        # Port of Raptor 7's `CompTimeToRepair:=TimeToRepair` --
        # fed right before the "Repaired" line.
        if system_state is not None:
            record_component_repair(system_state, repair_time)

        if events_file:
            logs.append(format_event_log_line(sim_time, "Block", block.name, "Repaired", f"MaintTime={repair_time:.6f}"))

        if _has_ldt(block.post_dist, block.post_params):
            self._transition(BlockStatus.REP_HOLD, block.active_status, "postLDT", sim_time,
                        wake_ids=pending_wake_ids, wake_kinds=pending_wake_kinds)
            pending_wake_ids = []
            pending_wake_kinds = {}
            ldt_wait = rng_repair.draw_number(Distribution(block.post_dist), block.post_params)
            if scheduler.time + ldt_wait >= run_length:
                yield run_length - scheduler.time
                self._transition(BlockStatus.DONE, block.active_status, "End_sim", scheduler.time, cascade=False)
                return True
            yield ldt_wait
            sim_time = scheduler.time
            if events_file:
                logs.append(format_event_log_line(sim_time, "Block", block.name, "End_Post-LDT", f"Time={ldt_wait:.6f}"))

        # Port of `Repair`'s `DetermineNewState("Maintenance Completed")`
        # -- see `_apply_maintenance_completed` for the
        # full scope (shared with `PerformPM`'s completion).
        self._apply_maintenance_completed(sim_time, pending_wake_ids, pending_wake_kinds)
        # `Repair`'s tail: a repair cancels a
        # phase-deferred PM -- `IF deferPM: pmDeferGroup.RemoveThis(SELF);
        # deferPM:=FALSE`. Without this a block that deferred a PM during a
        # mission stayed ineligible for triggered PM until a NonMission phase
        # (PmMisDeferProbe: a whole-run mission, SolarP_500W deferred at
        # t=23.4, repaired later, PM_is_due again at t=598.3 in real).
        if block.run_state.defer_pm:
            block.run_state.defer_pm = False
        # The `TELL METHOD Run` re-entry boundary: every
        # `DetermineNewState` re-entry is a bare `Run;`,
        # a fresh process spawn that starts at the tail of the
        # instant, not a synchronous call. This generator merges Repair and Run
        # into one loop, so the literal port is a zero-length boundary here,
        # applied after every repair completion.
        self.hops.run_hop_taken = True
        yield 0.0
        return False


def block_run_process(
    block: RBDBlock,
    stats: BlockStatistics,
    rng_fail: RandomSource,
    rng_repair: RandomSource,
    scheduler: Scheduler,
    run_length: float,
    logs: list[str],
    *,
    cost_analysis: bool = False,
    weak_analysis: bool = False,
    events_file: bool = False,
    startup_failures: bool = False,
    rng_sys10: RandomSource | None = None,
    on_transition: Callable[[BlockStatus, StateDeltas, float, list[int], dict[int, str], bool], None] | None = None,
    resource_pool: ResourceQueue | None = None,
    pool_stats: PoolStats | None = None,
    spare_pool: ResourceQueue | None = None,
    spare_pool_config: SparePool | None = None,
    spawn_order_a_spare: SpawnOrderASpareFn | None = None,
    spawn_order_stock: Callable[[int], None] | None = None,
    #: Per-run registry the driver reads back at run end for the report's
    #: Custom_Sparing_Information table -- this generator owns its own
    #: Custom stock (nothing outside the block touches it), so the queue
    #: exists only here; registering it is cheaper than hoisting the
    #: construction out into the driver.
    custom_spares_registry: dict[int, ResourceQueue] | None = None,
    #: `System.missionStatus=Mission` right now -- a callable because the
    #: status changes over the run. Feeds `change_block_state`'s
    #: `blockMissionBool` clear (weak-link analysis' `R` column); `None`
    #: simply never clears, which is right for callers with no missions.
    in_mission_fn: Callable[[], bool] | None = None,
    wake_block: WakeBlockFn | None = None,
    all_blocks: list[RBDBlock] | None = None,
    #: ER3-08 (`dep_type=="RBDEvent"`): only consulted by
    #: `repair_completion_status` to resolve the depended-upon event by id.
    all_events: list[RBDEvent] | None = None,
    end_node: RBDNode | None = None,
    own_node: RBDNode | None = None,
    nodes_by_id: dict[int, RBDNode] | None = None,
    links_by_id: dict[int, Link] | None = None,
    check_system_status: Callable[[float], None] | None = None,
    resend_back_flow: Callable[[float], None] | None = None,
    flush_cold_changes: Callable[[float], None] | None = None,
    initial_fail_time_left: float | None = None,
    initial_start_cond: StartCond | None = None,
    system_state: SystemState | None = None,
    phase_defer_enabled: bool = False,
) -> Process:
    """An ordinary block's whole life as a `core.scheduler.Process`: packs
    its arguments into a `BlockEnv` and `BlockPools` and runs a
    `BlockProcess`. Kept for callers that build one block at a time (the
    single-block test harness); the orchestrator builds one `BlockEnv` per
    run and constructs `BlockProcess` directly."""
    cascade: _CallbackCascade | None = None
    if check_system_status is not None and resend_back_flow is not None and flush_cold_changes is not None:
        cascade = _CallbackCascade(check_system_status, resend_back_flow, flush_cold_changes)
    elif check_system_status is not None or resend_back_flow is not None or flush_cold_changes is not None:
        raise TypeError("check_system_status, resend_back_flow and flush_cold_changes go together")
    env = BlockEnv(
        scheduler, run_length, logs,
        cost_analysis=cost_analysis, weak_analysis=weak_analysis, events_file=events_file,
        startup_failures=startup_failures, phase_defer_enabled=phase_defer_enabled,
        rng_sys10=rng_sys10, system_state=system_state, in_mission_fn=in_mission_fn,
        wake_block=wake_block, all_blocks=all_blocks, all_events=all_events,
        end_node=end_node, nodes_by_id=nodes_by_id, links_by_id=links_by_id,
        custom_spares_registry=custom_spares_registry, cascade=cascade,
    )
    pools = BlockPools(
        resource_pool=resource_pool, pool_stats=pool_stats,
        spare_pool=spare_pool, spare_pool_config=spare_pool_config,
        spawn_order_a_spare=spawn_order_a_spare, spawn_order_stock=spawn_order_stock,
    )
    return BlockProcess(
        block, stats, rng_fail, rng_repair, env,
        pools=pools, on_transition=on_transition, own_node=own_node,
        initial_fail_time_left=initial_fail_time_left, initial_start_cond=initial_start_cond,
    ).run()


@dataclass(frozen=True)
class _CallbackCascade:
    """A `CascadePort` made of three plain callbacks, for `block_run_process`."""

    check_system_status: Callable[[float], None]
    resend_back_flow: Callable[[float], None]
    flush_cold_changes: Callable[[float], None]
    #: A single-block harness has no phase clock and no pmSyncTrigger.
    phase_change_in_progress: bool = False

    def park_for_pm_sync(self, block_id: int) -> None:
        raise EngineInvariantError("a block parked on pmSyncTrigger in a harness with no phase clock")

    #: A single-block harness has no triggers.
    defer_trigger: str = ""

    def has_trigger(self, name: str) -> bool:
        return False

    def park_for_trigger(self, name: str, block_id: int) -> None:
        raise EngineInvariantError("a block parked on a trigger in a harness with no triggers")
