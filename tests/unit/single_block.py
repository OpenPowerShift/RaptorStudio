"""Run one block through the production lifecycle (`block_run_process`) on its
own scheduler, for block-level tests that compare against real captures.

Replaces the former scheduler-free twin `simulate_block_run`, so these tests
exercise the code the engine actually runs.
"""
from __future__ import annotations

from raptor.core.engine.block_lifecycle import block_run_process
from raptor.core.engine.blocks import BlockStatistics, change_block_state
from raptor.core.model import ActiveStatus, BlockStatus, RBDBlock
from raptor.core.rng import RandomSource
from raptor.core.scheduler import Scheduler


def run_single_block(
    block: RBDBlock, stats: BlockStatistics, rng_fail: RandomSource, rng_repair: RandomSource,
    run_length: float, *, cost_analysis: bool = False, weak_analysis: bool = False,
    events_file: bool = False,
) -> list[str]:
    """One run (SimTime 0 to `run_length`) of a lone block; returns its log lines.

    The block's "Initialize" transition comes first, as the orchestrator's
    kickoff does it. A fresh `Scheduler` per call restarts SimTime at 0
    (`ResetSimTime(0.0)`), while `rng_fail`/`rng_repair` carry over between
    calls, as real streams do across runs.
    """
    logs: list[str] = []
    init_logs, *_ = change_block_state(
        block, stats, BlockStatus.RUNNING, ActiveStatus.ACTIVE, "Initialize",
        sim_time=0.0, phase_stress=1.0, cost_analysis=cost_analysis,
        weak_analysis=weak_analysis, events_file=events_file,
    )
    logs.extend(init_logs)
    scheduler = Scheduler()
    scheduler.schedule_process(block_run_process(
        block, stats, rng_fail, rng_repair, scheduler, run_length, logs,
        cost_analysis=cost_analysis, weak_analysis=weak_analysis, events_file=events_file,
    ))
    scheduler.run_until(run_length)
    return logs
