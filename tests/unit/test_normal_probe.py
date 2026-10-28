"""End-to-end test replaying real simulation runs of
`tests/golden/NormalProbe` with the ported RNG and engine pieces, diffing
every line against the captured output.

The fixture exercises `rng.py`'s `_normal` (SIMSCRIPT II.5's `normal.f`).
"""
from __future__ import annotations

import pathlib


from tests.unit.single_block import run_single_block
from raptor.core.engine.blocks import BlockStatistics
from raptor.core.model import RBDBlock, SparingType
from raptor.core.rng import Distribution, RandomGen, RaptorStream, fetch_raptor_seed
from tests.unit.log_compare import (
    LANDMARK_END_SIM, classify as _classify, read_lines, split_runs)

GOLDEN = pathlib.Path(__file__).parent.parent / "golden"
_RUN_LENGTH = 100.0  # NormalProbe.rbd's SYSTEM_DATA config


def _golden_runs(max_runs: int) -> list[list[str]]:
    path = GOLDEN / "NormalProbe" / "legacy_event_log.txt.gz"
    block_lines = read_lines(path, keep=lambda l: "Block" in l and "System" not in l)
    return split_runs(block_lines, LANDMARK_END_SIM, max_runs=max_runs)


def _compare(mine: list[str], golden: list[str]) -> tuple[dict[str, int], int | None]:
    counts = {"match": 0, "fp32-noise": 0, "structural": 0}
    first_structural: int | None = None
    n = min(len(mine), len(golden))
    for i in range(n):
        c = _classify(mine[i], golden[i])
        counts[c] += 1
        if c == "structural" and first_structural is None:
            first_structural = i
    return counts, first_structural


def _make_block() -> RBDBlock:
    # Matches tests/golden/NormalProbe/input.rbd: Normal(10, 1) fail on
    # stream 1, Normal(5, 1) repair on stream 2 -- means chosen large
    # relative to stdev so draws never hit the negative-draw clamp.
    return RBDBlock(
        name="unnamed", fail_distro=int(Distribution.NORMAL), fail_values=[10.0, 1.0],
        repair_distro=int(Distribution.NORMAL), repair_values=[5.0, 1.0],
        # Infinite sparing, as UniformProbe (this probe's template) has: the
        # capture fails and repairs repeatedly. The model default, NONE,
        # would retire the block at its first failure.
        sparing_type=SparingType.INFINITE,
    )


def test_single_run_reproduces_real_captured_output() -> None:
    golden_runs = _golden_runs(max_runs=1)
    block = _make_block()
    stats = BlockStatistics()
    rng_fail = RandomGen(stream=RaptorStream(seed=fetch_raptor_seed(1)))
    rng_repair = RandomGen(stream=RaptorStream(seed=fetch_raptor_seed(2)))

    mine = run_single_block(block, stats, rng_fail, rng_repair, _RUN_LENGTH, events_file=True)

    counts, first_structural = _compare(mine, golden_runs[0])
    assert len(mine) == len(golden_runs[0]), (
        f"line count differs: mine={len(mine)} golden={len(golden_runs[0])}"
    )
    assert counts["structural"] == 0, (
        f"structural mismatch at line {first_structural}: "
        f"mine={mine[first_structural]!r} theirs={golden_runs[0][first_structural]!r}"
    )


def test_fifty_consecutive_runs_reproduce_real_captured_output() -> None:
    """Fifty runs back-to-back through the same persistent streams (streams
    do not reset between runs) exercise `_normal` beyond a single-run
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
        counts, first_structural = _compare(mine, golden_run)
        assert len(mine) == len(golden_run), f"run {run_idx}: line count differs"
        assert counts["structural"] == 0, (
            f"run {run_idx}, line {first_structural}: "
            f"mine={mine[first_structural]!r} theirs={golden_run[first_structural]!r}"
        )
        total_structural += counts["structural"]
        total_lines += min(len(mine), len(golden_run))

    assert total_lines > 500, "sanity check: should have compared hundreds of real lines"
    assert total_structural == 0
