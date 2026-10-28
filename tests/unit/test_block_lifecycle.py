"""End-to-end tests that replay captured simulation runs using the ported
RNG and engine pieces.

Diffs every line, including the run-boundary `Done`/`"End_sim"` line,
against the captured output. Uses `tests/golden/UniformProbe` and the
other block-level fixtures under `tests/golden/`.
"""
from __future__ import annotations

import gzip
import pathlib
import re

import pytest

from raptor.core.engine.block_lifecycle import block_run_process
from tests.unit.single_block import run_single_block
from tests.unit.log_compare import (
    LANDMARK_END_SIM, compare_runs, read_lines, split_runs)
from raptor.core.engine.blocks import BlockStatistics
from raptor.core.engine.pool import ResourceQueue
from raptor.core.model import RBDBlock, SparePool, SparingType
from raptor.core.rng import RandomGen, RaptorStream, fetch_raptor_seed
from raptor.core.scheduler import Scheduler

GOLDEN = pathlib.Path(__file__).parent.parent / "golden"

_RUN_LENGTH = 100.0  # UniformProbe.rbd's SYSTEM_DATA config


def _golden_runs(max_runs: int) -> list[list[str]]:
    """Splits the captured event log into per-run block-line lists (each
    ending with its `Done`/`"End_sim"` line), up to `max_runs` runs."""
    path = GOLDEN / "UniformProbe" / "legacy" / "rpt_uniform_event_log_2.txt.gz"
    block_lines = read_lines(path, keep=lambda l: "Block" in l and "System" not in l)
    return split_runs(block_lines, LANDMARK_END_SIM, max_runs=max_runs)


def _make_block() -> RBDBlock:
    # Matches tests/golden/UniformProbe/input.rbd exactly: Uniform Real(0,1)
    # fail/repair, infinite sparing, independent streams 1 (fail) / 2
    # (repair).
    #
    # `sparing_type` must be set explicitly: `RBDBlock`'s dataclass default
    # is `SparingType.NONE`, not INFINITE, so leaving it out would make the
    # block permanently out of spares (`Done "No_spares"` on the first
    # failure). `block_from_row` reads `SparingType.INFINITE` for
    # UniformProbe's block.
    return RBDBlock(
        name="unnamed", fail_distro=10, fail_values=[0.0, 1.0],
        repair_distro=10, repair_values=[0.0, 1.0],
        sparing_type=SparingType.INFINITE,
    )


def test_single_run_reproduces_real_captured_output() -> None:
    golden_runs = _golden_runs(max_runs=1)
    block = _make_block()
    stats = BlockStatistics()
    rng_fail = RandomGen(stream=RaptorStream(seed=fetch_raptor_seed(1)))
    rng_repair = RandomGen(stream=RaptorStream(seed=fetch_raptor_seed(2)))

    mine = run_single_block(block, stats, rng_fail, rng_repair, _RUN_LENGTH, events_file=True)

    counts, first_structural = compare_runs(mine, golden_runs[0])
    n = min(len(mine), len(golden_runs[0]))
    assert len(mine) == len(golden_runs[0]), (
        f"line count differs: mine={len(mine)} golden={len(golden_runs[0])} "
        "(run-boundary cutoff logic likely off by one cycle)"
    )
    assert counts["structural"] == 0, (
        f"structural mismatch at line {first_structural}: "
        f"mine={mine[first_structural]!r} theirs={golden_runs[0][first_structural]!r}"
    )
    assert counts["fp32-noise"] < n * 0.1


def test_fifty_consecutive_runs_reproduce_real_captured_output() -> None:
    """The real test of the run-boundary/reset mechanics: streams must not
    reset between runs (only the block's own status does); see
    block_lifecycle.py's module docstring. Fifty runs back-to-back (~20K
    lines) is strong evidence this is not a lucky single-run
    coincidence."""
    golden_runs = _golden_runs(max_runs=50)
    block = _make_block()
    stats = BlockStatistics()
    rng_fail = RandomGen(stream=RaptorStream(seed=fetch_raptor_seed(1)))
    rng_repair = RandomGen(stream=RaptorStream(seed=fetch_raptor_seed(2)))

    total_structural = 0
    total_lines = 0
    for run_idx, golden_run in enumerate(golden_runs):
        mine = run_single_block(block, stats, rng_fail, rng_repair, _RUN_LENGTH, events_file=True)
        counts, first_structural = compare_runs(mine, golden_run)
        assert len(mine) == len(golden_run), f"run {run_idx}: line count differs"
        assert counts["structural"] == 0, (
            f"run {run_idx}, line {first_structural}: "
            f"mine={mine[first_structural]!r} theirs={golden_run[first_structural]!r}"
        )
        total_structural += counts["structural"]
        total_lines += min(len(mine), len(golden_run))

    assert total_lines > 3000, "sanity check: should have compared thousands of real lines"
    assert total_structural == 0


def _make_ldt_block() -> RBDBlock:
    # Matches tests/golden/PrePostLDT/input.rbd: same as _make_block()
    # but with a real Fixed(0.05) pre/post logistics delay instead of
    # Fixed(0.0) (see that fixture's README).
    return RBDBlock(
        name="unnamed", fail_distro=10, fail_values=[0.0, 1.0],
        repair_distro=10, repair_values=[0.0, 1.0],
        pre_dist=19, pre_params=[0.05, 0.0, 0.0],
        post_dist=19, post_params=[0.05, 0.0, 0.0],
        sparing_type=SparingType.INFINITE,  # see _make_block()
    )


def _golden_ldt_runs(max_runs: int) -> list[list[str]]:
    path = GOLDEN / "PrePostLDT" / "legacy" / "rpt_pre_post_ldt.txt"
    if not path.exists():
        pytest.skip(f"golden fixture not present: {path}")
    raw_lines = path.read_bytes().split(b"\r\n")
    block_lines = [l.decode("latin-1") for l in raw_lines if b"Block" in l and b"System" not in l]
    runs: list[list[str]] = []
    current: list[str] = []
    for line in block_lines:
        current.append(line)
        if "End_sim" in line:
            runs.append(current)
            current = []
            if len(runs) >= max_runs:
                break
    return runs


def test_pre_post_ldt_reproduces_real_captured_output() -> None:
    """Verifies the LDT branches of the block lifecycle against the 5
    captured runs of tests/golden/PrePostLDT. The expected
    RepHold("LDT")/"End_Pre-LDT"/.../RepHold("postLDT")/"End_Post-LDT"
    sequence is reproduced to fp32 tolerance."""
    golden_runs = _golden_ldt_runs(max_runs=5)
    block = _make_ldt_block()
    stats = BlockStatistics()
    rng_fail = RandomGen(stream=RaptorStream(seed=fetch_raptor_seed(1)))
    rng_repair = RandomGen(stream=RaptorStream(seed=fetch_raptor_seed(2)))

    total_structural = 0
    total_lines = 0
    for run_idx, golden_run in enumerate(golden_runs):
        mine = run_single_block(block, stats, rng_fail, rng_repair, _RUN_LENGTH, events_file=True)
        counts, first_structural = compare_runs(mine, golden_run)
        assert len(mine) == len(golden_run), (
            f"run {run_idx}: line count differs: mine={len(mine)} golden={len(golden_run)}"
        )
        assert counts["structural"] == 0, (
            f"run {run_idx}, line {first_structural}: "
            f"mine={mine[first_structural]!r} theirs={golden_run[first_structural]!r}"
        )
        total_structural += counts["structural"]
        total_lines += min(len(mine), len(golden_run))

    assert total_lines > 1000, "sanity check: should have compared over a thousand real lines"
    assert total_structural == 0


def _make_finite_spare_pool_block() -> RBDBlock:
    # Matches tests/golden/FiniteSparePool/input.rbd: same as _make_block()
    # but Custom sparing with only 3 units of stock and no replenishment
    # (RSO/SLO/ESO all off, matching UniformProbe's untouched defaults).
    return RBDBlock(
        name="unnamed", fail_distro=10, fail_values=[0.0, 1.0],
        repair_distro=10, repair_values=[0.0, 1.0],
        sparing_type=SparingType.CUSTOM, init_stock=3,
    )


def _golden_finite_spare_pool_runs(max_runs: int) -> list[list[str]]:
    path = GOLDEN / "FiniteSparePool" / "legacy" / "rpt_finite_spare_pool.txt.gz"
    if not path.exists():
        pytest.skip(f"golden fixture not present: {path}")
    with gzip.open(path, "rb") as f:
        raw_lines = f.read().split(b"\r\n")
    block_lines = [l.decode("latin-1") for l in raw_lines if b"Block" in l and b"System" not in l]
    runs: list[list[str]] = []
    current: list[str] = []
    for line in block_lines:
        current.append(line)
        if "End_sim" in line:
            runs.append(current)
            current = []
            if len(runs) >= max_runs:
                break
    return runs


def test_finite_spare_pool_reproduces_real_captured_output() -> None:
    """Verifies the Custom-sparing exhaustion branch of the block lifecycle
    against the 5 captured runs of tests/golden/FiniteSparePool. Each run
    exhausts its 3 spares after exactly 3 repairs."""
    golden_runs = _golden_finite_spare_pool_runs(max_runs=5)
    block = _make_finite_spare_pool_block()
    stats = BlockStatistics()
    rng_fail = RandomGen(stream=RaptorStream(seed=fetch_raptor_seed(1)))
    rng_repair = RandomGen(stream=RaptorStream(seed=fetch_raptor_seed(2)))

    total_structural = 0
    total_lines = 0
    for run_idx, golden_run in enumerate(golden_runs):
        mine = run_single_block(block, stats, rng_fail, rng_repair, _RUN_LENGTH, events_file=True)
        counts, first_structural = compare_runs(mine, golden_run)
        assert len(mine) == len(golden_run), (
            f"run {run_idx}: line count differs: mine={len(mine)} golden={len(golden_run)}"
        )
        assert counts["structural"] == 0, (
            f"run {run_idx}, line {first_structural}: "
            f"mine={mine[first_structural]!r} theirs={golden_run[first_structural]!r}"
        )
        total_structural += counts["structural"]
        total_lines += min(len(mine), len(golden_run))

    assert total_lines > 50, "sanity check: should have compared several dozen real lines"
    assert total_structural == 0


def _make_pm_block() -> RBDBlock:
    # Same fail/repair config as _make_block() (Uniform(0,1), mean 0.5),
    # frequency-based PM with PMFreq=0.3 -- small enough to outrace most
    # failure draws. pm_dist=Fixed(0.1) is a small, deterministic
    # maintenance time drawn from the repair stream (see `_pm_race`).
    return RBDBlock(
        name="unnamed", fail_distro=10, fail_values=[0.0, 1.0],
        repair_distro=10, repair_values=[0.0, 1.0],
        uses_pm=True, pm_freq=0.3, pm_dist=19, pm_params=[0.1, 0.0, 0.0],
        pm_refresh=True, pm_fail_reset=True,
        sparing_type=SparingType.INFINITE,  # see _make_block()
    )


def test_spare_pool_slo_orders_stock_when_grant_hits_threshold() -> None:
    """Structural test for SLO (stock-level ordering): a pool with exactly
    1 spare and SLO configured (`SLOOrderLevel=0`, `SLONewSpares=1`) spawns
    a stock order the moment a grant leaves it at 0, and does not take the
    "permanently out of spares" path just because RSO/ESO are both off
    (SLO alone is enough to keep the block waiting instead)."""
    block = RBDBlock(
        name="unnamed", fail_distro=19, fail_values=[0.0, 0.0, 0.0],
        repair_distro=19, repair_values=[0.0, 0.0, 0.0],
        sparing_type=SparingType.SPARE_POOL, pool_name="Cells",
    )
    pool_config = SparePool(
        pool_name="Cells", stock_level_ordering=True, slo_order_level=0, slo_new_spares=1,
        routine_spare_ordering=False, emergency_spare_ordering=False,
    )
    spare_pool = ResourceQueue(resources=1)
    stats = BlockStatistics()
    rng_fail = RandomGen(stream=RaptorStream(seed=fetch_raptor_seed(1)))
    rng_repair = RandomGen(stream=RaptorStream(seed=fetch_raptor_seed(2)))
    scheduler = Scheduler()
    logs: list[str] = []
    stock_orders: list[int] = []

    process = block_run_process(
        block, stats, rng_fail, rng_repair, scheduler, 10.0, logs, events_file=True,
        spare_pool=spare_pool, spare_pool_config=pool_config,
        spawn_order_stock=lambda quantity: stock_orders.append(quantity),
    )
    scheduler.schedule_process(process)
    scheduler.run_until(10.0)

    # Fixed(0.0) fail/repair means the block fails immediately and its
    # one spare is granted immediately too -- the grant leaves the pool
    # at 0 (==slo_order_level), so exactly one order for slo_new_spares
    # (1) must have been spawned, and the block must not have been
    # routed to "No_spares" (it must still be Running/Repairing, not
    # Done, since SLO alone is enough to keep it from being permanently
    # out).
    assert stock_orders == [1]
    assert not any("No_spares" in l for l in logs)
    assert any("No_spare_in_stock-Cells" in l for l in logs)


def test_custom_sparing_rso_eso_replenish_and_unblock_a_waiting_block() -> None:
    """Structural test for Custom-sparing RSO/ESO replenishment, mirroring
    tests/golden/HierarchyExample's `Thermal` block config (`init_stock=1`,
    RSO+ESO both enabled): a block that exhausts its one spare spawns an
    ESO order (arriving after `emer_time`), is woken by it once it arrives,
    and RSO's independent background restocking fires later in the run."""
    block = RBDBlock(
        name="Thermal", fail_distro=19, fail_values=[0.0, 0.0, 0.0],
        repair_distro=19, repair_values=[0.0, 0.0, 0.0],
        sparing_type=SparingType.CUSTOM, init_stock=1,
        routine_spare_ordering=True, new_spares_arrival=5.0, new_spares=1,
        emergency_spare_ordering=True, emer_time=2.0,
    )
    stats = BlockStatistics()
    rng_fail = RandomGen(stream=RaptorStream(seed=fetch_raptor_seed(1)))
    rng_repair = RandomGen(stream=RaptorStream(seed=fetch_raptor_seed(2)))
    scheduler = Scheduler()
    logs: list[str] = []
    handle_box: dict[str, object] = {}

    def wake_block(block_id: int, reason: str,
                   kind: str | None = None) -> None:
        scheduler.interrupt(handle_box["handle"], reason, token=kind)

    process = block_run_process(
        block, stats, rng_fail, rng_repair, scheduler, 10.0, logs, events_file=True,
        wake_block=wake_block,
    )
    handle_box["handle"] = scheduler.schedule_process(process)
    scheduler.run_until(10.0)

    # First failure consumes the one initial spare immediately (no wait).
    # The second failure (also instant, Fixed(0.0) fail/repair) finds the
    # stock at 0: ESO must fire (ordered, then arrives after emer_time),
    # unblocking the waiting block, AND RSO's own independent background
    # loop must also eventually restock (new_spares_arrival=5.0 < 10.0).
    assert any("No_spare_in_stock-Custom" in l for l in logs)
    assert any("Spare_ordered_(stock=0)" in l for l in logs)
    assert any("Ordered_spare_arrives_(stock=1)" in l for l in logs)
    assert any("Spare_Arrived" in l for l in logs)
    assert any("new_spare(s)_arrived" in l for l in logs)
    assert not any("No_spares" in l for l in logs)


@pytest.mark.parametrize("shared_callback", [False, True])
@pytest.mark.parametrize("order_level, quantity", [(0, 2), (2, 1)])
def test_custom_pm_slo_replenishes_private_stock_not_shared_pool(
    shared_callback: bool, order_level: int, quantity: int,
) -> None:
    block = RBDBlock(
        name="PmSlo", fail_distro=19, fail_values=[100.0, 0.0, 0.0],
        repair_distro=19, repair_values=[0.0, 0.0, 0.0],
        sparing_type=SparingType.CUSTOM, init_stock=1,
        uses_pm=True, pm_spare_needed=True, pm_freq=5.0,
        pm_dist=19, pm_params=[1.0, 0.0, 0.0],
        stock_level_ordering=True, slo_order_level=order_level, slo_new_spares=2,
        slo_time=2.0, spare_cost=3.0,
    )
    stats = BlockStatistics()
    rng_fail = RandomGen(stream=RaptorStream(seed=fetch_raptor_seed(1)))
    rng_repair = RandomGen(stream=RaptorStream(seed=fetch_raptor_seed(2)))
    scheduler = Scheduler()
    logs: list[str] = []
    private_pools: dict[int, ResourceQueue] = {}
    shared_orders: list[int] = []

    def wake_block(block_id: int, reason: str,
                   kind: str | None = None) -> None:
        scheduler.interrupt(handle, reason, token=kind)

    process = block_run_process(
        block, stats, rng_fail, rng_repair, scheduler, 10.0, logs,
        events_file=True, cost_analysis=True, wake_block=wake_block,
        custom_spares_registry=private_pools,
        spawn_order_stock=shared_orders.append if shared_callback else None,
    )
    handle = scheduler.schedule_process(process)
    scheduler.run_until(6.0)

    pool = private_pools[block.id]
    assert pool.resources == 0
    assert pool.arrivals == 0
    assert stats.sp_cost == 3.0 * quantity
    assert block.run_state.spares_used == 1
    assert any("PM_complete" in line for line in logs)
    assert shared_orders == []

    scheduler.run_until(8.0)

    assert pool.resources == quantity
    assert pool.arrivals == quantity
    assert block.run_state.spares_used == 1
    assert stats.sp_cost == 3.0 * quantity
    assert shared_orders == []
    assert any(f"{quantity}_Spare(s)_arrived_(stock={quantity})" in line for line in logs)


def test_slotest2_full_engine_matches_real_capture() -> None:
    """SparePool + stock-level ordering (SLO): Stock=2, Level=1, Quan=2,
    SLOTime=24, no RSO/ESO; one block, Exponential MTBF=0.1, Fixed repair
    2h. Full-engine comparison via run_simulation: both captured runs must
    reproduce line-for-line (whitespace-normalized).

    Also pins the deferred OrderStock spawn semantics
    (`_make_spawn_order_stock` uses `schedule_process`, not `tell`): the
    new order line is logged after the spawning block's next yield, not
    synchronously inside its turn."""
    events_path = GOLDEN / "SLOTest2" / "legacy" / "rpt_events_SLOTest2.txt"
    rbd_path = GOLDEN.parent / "fixtures" / "SLOTest2" / "input.rbd"
    if not events_path.exists() or not rbd_path.exists():
        pytest.skip("SLOTest2 golden capture or fixture not present")

    def norm(line: str) -> str:
        return " ".join(line.split())

    real_runs: list[list[str]] = []
    current: list[str] | None = None
    for line in events_path.read_text(errors="replace").splitlines():
        m = re.match(r"^(\S+)\s+System\s+Begin_Run_(\d+)", line)
        if m:
            current = []
            real_runs.append(current)
            continue
        if current is not None and line[:1].isdigit():
            current.append(norm(line))

    from raptor.core.fileio.rbd import load_rbd
    from raptor.core.engine.runner import run_simulation

    logs = run_simulation(load_rbd(str(rbd_path)), num_runs=len(real_runs))
    assert len(logs) == len(real_runs)
    for idx, (real, mine) in enumerate(zip(real_runs, logs), 1):
        mine_norm = [norm(l) for l in mine]
        assert len(mine_norm) == len(real), (
            f"run {idx}: line count differs: mine={len(mine_norm)} real={len(real)}"
        )
        for i, (r, m) in enumerate(zip(real, mine_norm)):
            assert r == m, f"run {idx} line {i}: mine={m!r} real={r!r}"


# ---------------------------------------------------------------------
# GetStartCond: the six simStartType branches
#
# The end-to-end captures are tests/golden/SimStartProbe2 (types 1+4) and
# SimStartProbe3 (types 5+6). Both run LDT-free, so every down-start
# resolves to "repairing" and the preLDT/postLDT cut-points are never
# exercised.
#
# These tests cover that gap, driving get_start_cond with a scripted
# RandomGen so each branch can be selected deliberately. They are a
# port-of-source check, not a fidelity claim; no
# capture backs the LDT arms, which is why block_run_process still raises
# on them.
# ---------------------------------------------------------------------

class _ScriptedRng:
    """A RandomGen stand-in that returns queued values in order, so a
    specific GetStartCond branch can be selected. Records every
    (distro, params) it was asked for, which is how the draw accounting
    (who draws what, in what order, from which stream) is asserted."""

    def __init__(self, values):
        self.values = list(values)
        self.calls = []

    def draw_number(self, distro, params):
        self.calls.append((int(distro), list(params)))
        return self.values.pop(0)


def _start_cond_block(**kw):
    from raptor.core.model import RBDBlock as _B
    b = _B(id=1, name="B")
    # Weibull(1.5, 50) fail / Fixed(10) repair / no LDT -- SimStartProbe2's
    # own config, so the numbers here line up with that capture.
    b.fail_distro, b.fail_values = 15, [1.5, 50.0, 0.0]
    b.repair_distro, b.repair_values = 19, [10.0, 0.0, 0.0]
    b.pre_dist, b.pre_params = 19, [0.0, 0.0, 0.0]
    b.post_dist, b.post_params = 19, [0.0, 0.0, 0.0]
    b.amount_exhausted = 0.0
    for k, v in kw.items():
        setattr(b, k, v)
    return b


@pytest.mark.parametrize("sim_start,draws_fail,expect_uniforms", [
    # Types 1/2/4 take two Uniform(0,1) fail draws before the
    # unconditional TimeToFail draw; 3/5/6 take none.
    (1, 3, 2),
    (2, 3, 2),
    (3, 1, 0),
    (4, 3, 2),
    (5, 1, 0),
    (6, 1, 0),
])
def test_get_start_cond_fail_stream_draw_accounting(sim_start, draws_fail, expect_uniforms):
    from raptor.core.engine.block_lifecycle import get_start_cond
    b = _start_cond_block(sim_start_type=sim_start)
    # A randDraw1 near 1 pushes types 1/4 into their last branch, which
    # for this LDT-free config is still "repairing". The point here is the
    # count and order of fail-stream draws, not which branch is taken.
    rf = _ScriptedRng([0.5, 0.5, 60.0])
    rr = _ScriptedRng([10.0, 10.0, 10.0])
    get_start_cond(b, rf, rr)
    assert len(rf.calls) == draws_fail
    uniform_calls = [c for c in rf.calls[:expect_uniforms] if c == (10, [0.0, 1.0])]
    assert len(uniform_calls) == expect_uniforms
    # The TimeToFail draw is always last of the fail-stream draws and
    # always from the block's own fail distribution.
    assert rf.calls[-1] == (15, [1.5, 50.0, 0.0])


def test_get_start_cond_type6_takes_the_whole_repair_draw():
    """`WHEN 6` ("at start of hands-on repair"):
    one repair draw, taken whole, no scaling."""
    from raptor.core.engine.block_lifecycle import get_start_cond
    b = _start_cond_block(sim_start_type=6)
    sc = get_start_cond(b, _ScriptedRng([42.0]), _ScriptedRng([10.0]))
    assert sc.start_condition == "repairing"
    assert sc.start_val == 10.0
    assert sc.fail_time_left == 0.0  # down-starts never write FailTimeLeft


def test_get_start_cond_type1_running_branch_scales_by_randdraw2():
    """`WHEN 1`'s Running arm uses randDraw**2** , not randDraw1
    the way `WHEN 2` does. The source differs between the two,
    and getting it backwards is invisible in any fixture where only one of
    the two types appears."""
    from raptor.core.engine.block_lifecycle import get_start_cond
    b = _start_cond_block(sim_start_type=1)
    # failMean/cycleTime = 45.137265/55.137265 = 0.8186..., so randDraw1
    # below that lands in the Running arm.
    sc = get_start_cond(b, _ScriptedRng([0.1, 0.25, 80.0]), _ScriptedRng([]))
    assert sc.start_condition == "Running"
    assert sc.fail_time_left == pytest.approx(80.0 * 0.25)


def test_get_start_cond_type1_matches_simstartprobe2_blocka():
    """The exact BlockA numbers from `tests/golden/SimStartProbe2`'s real
    /sd capture, run 1: randDraw1=0.924530 clears the Running cut and
    lands in "repairing" with startVal = TimeToRepair * randDraw2. Real's
    own event log then reads `BlockA Repaired MaintTime=1.782948`."""
    from raptor.core.engine.block_lifecycle import get_start_cond
    b = _start_cond_block(sim_start_type=1)
    sc = get_start_cond(b, _ScriptedRng([0.9245303, 0.1782948, 63.040755]),
                        _ScriptedRng([10.0]))
    assert sc.start_condition == "repairing"
    assert sc.start_val == pytest.approx(1.782948, abs=1e-6)


def test_get_start_cond_type5_is_deterministic_and_draws_all_three():
    """`WHEN 5` draws preLDT, TimeToRepair and postLDT
    unconditionally -- all three, before any branch is chosen -- unlike
    types 1/4, which draw only inside the branch they take. It then places
    the block by `amountExhausted` as a percentage, with no randomness."""
    from raptor.core.engine.block_lifecycle import get_start_cond
    b = _start_cond_block(sim_start_type=5, amount_exhausted=50.0,
                          pre_dist=19, pre_params=[2.0, 0.0, 0.0],
                          post_dist=19, post_params=[4.0, 0.0, 0.0])
    rr = _ScriptedRng([2.0, 10.0, 4.0])  # preLDT, TimeToRepair, postLDT
    sc = get_start_cond(b, _ScriptedRng([60.0]), rr)
    assert len(rr.calls) == 3
    # cycleTime=16, downPercent=0.5 -> 8.0 in. preLDT/cycle=0.125 (no),
    # (preLDT+rep)/cycle=0.75 (yes) -> repairing, 2+10-8 = 4.0 left.
    assert sc.start_condition == "repairing"
    assert sc.start_val == pytest.approx(4.0)


def test_get_start_cond_type5_reaches_postldt_when_far_enough_through():
    from raptor.core.engine.block_lifecycle import get_start_cond
    b = _start_cond_block(sim_start_type=5, amount_exhausted=90.0,
                          pre_dist=19, pre_params=[2.0, 0.0, 0.0],
                          post_dist=19, post_params=[4.0, 0.0, 0.0])
    sc = get_start_cond(b, _ScriptedRng([60.0]), _ScriptedRng([2.0, 10.0, 4.0]))
    # 0.9 > (2+10)/16 = 0.75 -> postLDT, 16 - 0.9*16 = 1.6 left.
    assert sc.start_condition == "postLDT"
    assert sc.start_val == pytest.approx(1.6)


def test_get_start_cond_type4_preldt_branch_uses_repair_stream():
    """`WHEN 4`'s preLDT arm draws preLDT and TimeToRepair
    from the repair stream, and scales only `firstPreLDT` by randDraw2;
    `startVal` keeps the full repair draw."""
    from raptor.core.engine.block_lifecycle import get_start_cond
    b = _start_cond_block(sim_start_type=4,
                          pre_dist=19, pre_params=[10.0, 0.0, 0.0])
    # cycleTime = preLDTmean(10) + repairMean(10) + 0 = 20;
    # randDraw1 = 0.1 < 10/20 = 0.5 -> the preLDT arm.
    rr = _ScriptedRng([8.0, 12.0])  # preLDT draw, TimeToRepair draw
    sc = get_start_cond(b, _ScriptedRng([0.1, 0.25, 60.0]), rr)
    assert sc.start_condition == "preLDT"
    assert sc.start_val == pytest.approx(12.0)          # not scaled
    assert sc.first_pre_ldt == pytest.approx(8.0 * 0.25)  # scaled
    assert len(rr.calls) == 2


def test_get_start_cond_type3_retry_loop_still_intact():
    """`WHEN 3`'s retry loop : redraws until a life exceeds
    amountExhausted, then subtracts it."""
    from raptor.core.engine.block_lifecycle import get_start_cond
    b = _start_cond_block(sim_start_type=3, amount_exhausted=20.0)
    rf = _ScriptedRng([5.0, 10.0, 35.0])  # first two are too short
    sc = get_start_cond(b, rf, _ScriptedRng([]))
    assert sc.start_condition == "Running"
    assert sc.fail_time_left == pytest.approx(15.0)
    assert len(rf.calls) == 3


def test_get_start_cond_rejects_an_out_of_range_type():
    from raptor.core.engine.block_lifecycle import get_start_cond
    b = _start_cond_block(sim_start_type=7)
    with pytest.raises(NotImplementedError, match="Unknown simStartType"):
        get_start_cond(b, _ScriptedRng([1.0] * 5), _ScriptedRng([1.0] * 5))


def test_deferred_pm_resource_withdrawn_at_failure():
    from raptor.core.engine.runner import run_simulation
    from raptor.core.fileio.rbd import load_rbd

    fixture = (pathlib.Path(__file__).resolve().parents[2] / "tests"
               / "golden" / "PMGrantVsRepairSpareProbe" / "input.rbd")
    logs = run_simulation(load_rbd(str(fixture)), num_runs=1)[0]
    lines = ["\t".join(line.split("\t")[i].strip() for i in (0, 2, 3, 4))
             for line in logs]
    assert not any(line.startswith("25.000000\t")
                   and "Spare_Arrived" in line for line in lines)
    assert "30.000000\tResHolder\tRepairing\t1Techs" in lines
    assert "45.000000\tPmDeferRepair\tSparing\tSpare_Arrived" in lines
    assert "50.000000\tPmDeferRepair\tSparing\tObtained_1_Resource(s)" in lines


def _probe_lines(fixture: str) -> list[str]:
    from raptor.core.engine.runner import run_simulation
    from raptor.core.fileio.rbd import load_rbd

    path = (pathlib.Path(__file__).resolve().parents[2] / "tests" / "golden"
            / fixture / "input.rbd")
    logs = run_simulation(load_rbd(str(path)), num_runs=1)[0]
    return ["\t".join(line.split("\t")[i].strip() for i in (0, 2, 3, 4))
            for line in logs]


def test_resource_wait_control_logs_rep_hold() -> None:
    """`ResourceRepairOrderProbe2` (control): the failing block's repair
    release is one full time unit later, so a genuine resource wait logs
    `RepHold Waiting_for_resource(s)`. Capture:
    `tests/golden/ResourceRepairOrderProbe2/legacy/`."""
    lines = _probe_lines("ResourceRepairOrderProbe2")
    assert "25.000000\tResourceWaiter\tRepHold\tWaiting_for_resource(s)" in lines
    assert "26.000000\tResourceWaiter\tSparing\tObtained_1_Resource(s)" in lines


def test_resource_wait_observes_same_instant_release() -> None:
    """`ResourceRepairOrderProbe` (discriminating): the real engine logs no
    `RepHold` at t=25; the failing block observes the sibling's release in
    the same instant and goes straight to `Repairing`. Capture:
    `tests/golden/ResourceRepairOrderProbe/legacy/`."""
    lines = _probe_lines("ResourceRepairOrderProbe")
    assert "25.000000\tResourceWaiter\tRepHold\tWaiting_for_resource(s)" not in lines
    assert "25.000000\tResourceWaiter\tRepairing\t1Techs" in lines



def test_pm_initial_stagger_shifts_first_pm() -> None:
    """Initialize sets `PMTimeLeft:=pmFreq-pmStagger`.
    The PM clock must not start at 0.0, which `_pm_race` would refill to
    the full `pm_freq` and drop the stagger: with `pm_stagger=4`, the
    first PM is due at t=6.0, not t=10.0."""
    from tests.unit.single_block import run_single_block

    def first_pm_time(stagger: float) -> float:
        b = RBDBlock(
            name="PmStagger", fail_distro=19, fail_values=[100.0, 0.0, 0.0],
            repair_distro=19, repair_values=[1.0, 0.0, 0.0],
            sparing_type=SparingType.INFINITE, init_stock=100,
            uses_pm=True, pm_freq=10.0, pm_stagger=stagger,
            pm_dist=19, pm_params=[1.0, 0.0, 0.0],
            pm_refresh=True, pm_fail_reset=True,
        )
        logs = run_single_block(
            b, BlockStatistics(),
            RandomGen(stream=RaptorStream(seed=fetch_raptor_seed(1))),
            RandomGen(stream=RaptorStream(seed=fetch_raptor_seed(2))),
            30.0, events_file=True,
        )
        line = next(l for l in logs if "PM_is_due" in l)
        return float(line.split()[0])

    assert first_pm_time(0.0) == pytest.approx(10.0)
    assert first_pm_time(4.0) == pytest.approx(6.0)


def test_pm_race_uses_stress_scaled_fail_clock() -> None:
    """Source divides `FailTimeLeft` by `CombinedStress` before the PM race
   and never scales `PMTimeLeft`. The port keeps the
    fail clock raw, so the race divides it here, and a PM win returns the
    raw-equivalent (`pm_time_left * stress`) so the caller's
    `fail_time_left / stress` wait is the true PM countdown."""
    from raptor.core.engine.block_lifecycle import _pm_race

    b = RBDBlock(name="R", uses_pm=True, pm_freq=10.0)

    # stress=1: 15 < 20 -> PM wins; raw-equivalent 15 -> wait 15.
    due, _pm, raw = _pm_race(b, 15.0, 20.0, stress=1.0)
    assert due is True and raw == 15.0

    # stress=2: 15 < 20/2=10 is FALSE -> fail wins; wait 20/2 = 10.
    due, _pm, raw = _pm_race(b, 15.0, 20.0, stress=2.0)
    assert due is False and raw == 20.0

    # stress=0.5: PM wins; raw-equivalent 5*0.5=2.5 -> wait 2.5/0.5 = 5.
    due, _pm, raw = _pm_race(b, 5.0, 100.0, stress=0.5)
    assert due is True and raw == pytest.approx(2.5)


def test_none_sparing_exhausts_to_done_no_spares() -> None:
    """`SparingType.NONE` sets `sparesOut:=TRUE` in `CheckForSpare`,
   so `DetermineNewState("Block Has Failed")`
    routes straight to `Done "No_spares"`: the block exhausts and does not
    repair."""
    from raptor.core.engine.block_lifecycle import block_run_process

    block = RBDBlock(
        name="NoneSp", fail_distro=19, fail_values=[5.0, 0.0, 0.0],
        repair_distro=19, repair_values=[1.0, 0.0, 0.0],
        sparing_type=SparingType.NONE,
    )
    stats = BlockStatistics()
    scheduler = Scheduler()
    logs: list[str] = []
    proc = block_run_process(
        block, stats,
        RandomGen(stream=RaptorStream(seed=fetch_raptor_seed(1))),
        RandomGen(stream=RaptorStream(seed=fetch_raptor_seed(2))),
        scheduler, 20.0, logs, events_file=True,
    )
    scheduler.schedule_process(proc)
    scheduler.run_until(20.0)
    joined = "\n".join(logs)
    assert "No_spares" in joined
    assert "Repairing" not in joined
