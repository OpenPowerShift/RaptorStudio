"""Two `pmMisDefer` paths ported literally from source (no real capture reaches
them; a sweep of `PmMisDeferProbe` never trips either), so unit tests carry them.

* A PM that comes due while `deferPM` is ALREADY set is not run: source's
  `IF NOT deferPM: ObtainPMprereqs ELSE Run`.
* The postponed PM gives its SPARE back (`ObtainPMprereqs`, :
  ), per sparing type, ahead of the resource give-back.
"""
from __future__ import annotations

import pytest

from raptor.core.engine.block_env import BlockEnv, BlockPools
from raptor.core.engine.block_lifecycle import BlockProcess, block_run_process
from raptor.core.engine.blocks import BlockStatistics
from raptor.core.engine.pool import ResourceQueue
from raptor.core.engine.system_status import MissionStatus, SystemState
from raptor.core.model import RBDBlock, SparePool, SparingType
from raptor.core.rng import RandomGen, RaptorStream, fetch_raptor_seed
from raptor.core.scheduler import Scheduler

FIXED = 19


def _block(**fields) -> RBDBlock:
    base = dict(
        name="Pump", fail_distro=FIXED, fail_values=[100.0], repair_distro=FIXED, repair_values=[3.0],
        sparing_type=SparingType.INFINITE, uses_pm=True, pm_freq=30.0, pm_dist=FIXED,
        pm_params=[2.0, 0.0, 0.0], pm_refresh=True, pm_mis_defer=True, spare_cost=50.0,
    )
    base.update(fields)
    return RBDBlock(**base)


def _rngs() -> tuple[RandomGen, RandomGen]:
    return (RandomGen(stream=RaptorStream(seed=fetch_raptor_seed(1))),
            RandomGen(stream=RaptorStream(seed=fetch_raptor_seed(2))))


# --- a PM due while deferPM is already set -------------------------------
#
# A deferred block does not race its PM at all (source's `Run` excludes it), so
# this state is reached only when `deferPM` is set DURING a PM wait that was
# already running. The tests set it mid-wait with a helper process.

def _run(block: RBDBlock, mission: MissionStatus, *, defer_at: float | None, run_length: float = 200.0) -> list[str]:
    logs: list[str] = []
    scheduler = Scheduler()
    state = SystemState()
    state.mission_status = mission
    rng_fail, rng_repair = _rngs()
    scheduler.schedule_process(block_run_process(
        block, BlockStatistics(), rng_fail, rng_repair, scheduler, run_length, logs,
        events_file=True, system_state=state, phase_defer_enabled=True,
    ))
    if defer_at is not None:
        def set_flag():
            yield defer_at
            block.run_state.defer_pm = True
        scheduler.schedule_process(set_flag())
    scheduler.run_until(run_length)
    return [str(line) for line in logs]


def test_a_pm_due_while_already_deferred_is_postponed_not_refused() -> None:
    """Outside a mission the ordinary path would run the PM at t=30; with `deferPM`
    set at t=10 it must not, and it must not raise either."""
    block = _block()
    lines = _run(block, MissionStatus.NON_MISSION, defer_at=10.0)
    first_failure = next(i for i, line in enumerate(lines) if "Failed" in line)
    before_repair = lines[:first_failure]
    assert any("PM_is_due" in line and line.startswith("30.0") for line in before_repair), "the PM came due"
    # A repair cancels a deferred PM, so only the
    # period before the first failure is the deferral's own.
    assert not any("beginning_PM" in line or "PM_complete" in line for line in before_repair)


def test_the_same_block_runs_its_pm_when_it_is_not_deferred() -> None:
    """The control: identical block and phase, `deferPM` never set -> the PM runs."""
    lines = _run(_block(), MissionStatus.NON_MISSION, defer_at=None)
    assert any("beginning_PM" in line and line.startswith("30.0") for line in lines)


# --- the PM's spare goes back --------------------------------------------

def _process(block: RBDBlock, *, spare_pool: ResourceQueue | None = None, cost_analysis: bool = False,
             woken: list | None = None) -> BlockProcess:
    scheduler = Scheduler()
    logs: list[str] = []
    env = BlockEnv(scheduler, 200.0, logs, cost_analysis=cost_analysis, events_file=True,
                   wake_block=(lambda i, reason, kind: woken.append((i, reason, kind))) if woken is not None else None)
    rng_fail, rng_repair = _rngs()
    pools = BlockPools(spare_pool=spare_pool,
                       spare_pool_config=SparePool(pool_name="P1", sparing_type=SparingType.SPARE_POOL)
                       if spare_pool is not None else None)
    proc = BlockProcess(block, BlockStatistics(), rng_fail, rng_repair, env, pools=pools)
    proc.clock.race_raw_life = None     # assigned by the run loop before use; these tests skip the loop
    return proc


def _queue(stock: int) -> ResourceQueue:
    queue = ResourceQueue(resources=stock, time_fn=lambda: 0.0)
    queue.begin_run(stock, 0.0)
    return queue


def test_infinite_spare_cost_and_use_are_reversed() -> None:
    block = _block(pm_spare_needed=True)
    proc = _process(block, cost_analysis=True)
    block.run_state.spares_used = 1
    block.run_state.spare_obtained = True
    proc.stats.sp_cost = 50.0
    proc._give_back_pm_spare()
    assert proc.stats.sp_cost == 0.0
    assert block.run_state.spares_used == 0
    assert block.run_state.spare_obtained is False


def test_infinite_cost_is_untouched_without_cost_analysis() -> None:
    block = _block(pm_spare_needed=True)
    proc = _process(block, cost_analysis=False)
    block.run_state.spares_used = 1
    proc.stats.sp_cost = 7.0
    proc._give_back_pm_spare()
    assert proc.stats.sp_cost == 7.0 and block.run_state.spares_used == 0


def test_pool_spare_returns_to_the_shared_pool() -> None:
    pool = _queue(0)
    block = _block(sparing_type=SparingType.SPARE_POOL, pm_spare_needed=True)
    proc = _process(block, spare_pool=pool)
    block.run_state.spares_used = 1
    block.run_state.spare_obtained = True
    proc._give_back_pm_spare()
    assert pool.resources == 1
    assert block.run_state.spares_used == 0 and block.run_state.spare_obtained is False


def test_custom_spare_returns_to_the_blocks_own_queue() -> None:
    queue = _queue(0)
    block = _block(sparing_type=SparingType.CUSTOM, pm_spare_needed=True)
    proc = _process(block)
    proc.custom_spares = queue
    block.run_state.spares_used = 1
    block.run_state.spare_obtained = True
    proc._give_back_pm_spare()
    assert queue.resources == 1
    assert block.run_state.spares_used == 0 and block.run_state.spare_obtained is False


def test_a_returned_spare_wakes_a_block_that_was_waiting_for_one() -> None:
    pool = _queue(0)
    assert pool.request(99, 1) is False           # block 99 queues for a spare
    woken: list = []
    block = _block(sparing_type=SparingType.SPARE_POOL, pm_spare_needed=True)
    proc = _process(block, spare_pool=pool, woken=woken)
    block.run_state.spares_used = 1
    proc._give_back_pm_spare()
    assert [(i, reason) for i, reason, _kind in woken] == [(99, "Pool_Available")]


def test_none_sparing_gives_nothing_back_but_clears_the_flag() -> None:
    block = _block(sparing_type=SparingType.NONE, pm_spare_needed=True)
    proc = _process(block)
    block.run_state.spares_used = 0
    block.run_state.spare_obtained = True
    proc._give_back_pm_spare()
    assert block.run_state.spares_used == 0 and block.run_state.spare_obtained is False


def test_the_misdefer_tail_no_longer_refuses_a_pm_spare() -> None:
    """The whole tail, as `ObtainPMprereqs` runs it on the mission branch: it used
    to raise NotImplementedError for a block whose PM needs a spare."""
    block = _block(pm_spare_needed=True)
    proc = _process(block, cost_analysis=True)
    block.run_state.spares_used = 1
    proc.stats.sp_cost = 50.0
    proc._misdefer_give_back(12.0)
    assert block.run_state.defer_pm is True
    assert proc.stats.sp_cost == 0.0 and block.run_state.spares_used == 0
    assert proc.pm.due is False


@pytest.mark.parametrize("sparing", [SparingType.INFINITE, SparingType.NONE])
def test_a_block_that_needs_no_pm_spare_is_unchanged(sparing: SparingType) -> None:
    block = _block(sparing_type=sparing, pm_spare_needed=False)
    proc = _process(block, cost_analysis=True)
    block.run_state.spares_used = 3
    proc.stats.sp_cost = 150.0
    proc._misdefer_give_back(12.0)
    assert block.run_state.spares_used == 3 and proc.stats.sp_cost == 150.0 and block.run_state.defer_pm is True
