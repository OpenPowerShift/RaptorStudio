"""End-to-end test of genuine multi-object concurrency: two independently
scheduled blocks (via `core.scheduler`'s process machinery) feed a shared
1-of-2 `join` node, which cascades to `end`. This exercises the scheduler
with more than one concurrent process, a two-hop node cascade (block ->
join -> end), and the node-level cascade dispatcher
(`cascade.reconfigure_after_node_change`).

See `tests/golden/TwoBlockRedundancy` for the topology.
"""
from __future__ import annotations

import pathlib


from raptor.core.engine.blocks import BlockStatistics, change_block_state, write_block_failed_line
from raptor.core.engine.cascade import reconfigure_after_block_change, reconfigure_after_node_change
from raptor.core.engine.eventlog import format_event_log_line
from raptor.core.engine.nodes import NodeStatistics, change_node_state
from raptor.core.engine.system_status import (
    SystemState, analyze_system, block_fail_count_delta, change_system_status,
    end_node_system_up_transition,
)
from raptor.core.model import ActiveStatus, BlockStatus, Link, LinkStatus, RBDBlock, RBDNode
from raptor.core.rng import Distribution, RandomGen, RaptorStream, fetch_raptor_seed
from raptor.core.scheduler import Scheduler
from tests.unit.log_compare import (
    LANDMARK_BEGIN_RUN, classify as _classify, read_lines, split_runs)

GOLDEN = pathlib.Path(__file__).parent.parent / "golden"
_RUN_LENGTH = 100.0


def _golden_first_run_lines() -> list[str]:
    path = GOLDEN / "TwoBlockRedundancy" / "legacy" / "rpt_input_two_event.txt.gz"
    lines = read_lines(path)
    # First run only: everything between "Begin_Run_1" (emitted by system
    # orchestration, not by any single object's state machine) and
    # "Begin_Run_2". `max_runs=2` is what makes run 1 the one that ends
    # at the second landmark rather than at EOF.
    runs = split_runs(lines, LANDMARK_BEGIN_RUN, max_runs=2)
    assert len(runs) == 2, (
        f"expected at least Begin_Run_1 and Begin_Run_2 in {path.name}, "
        f"found {len(runs)} run(s)"
    )
    return runs[0]


def _make_scenario():
    block_a = RBDBlock(
        name="unnamed", id=3, fail_distro=10, fail_values=[0.0, 1.0],
        repair_distro=10, repair_values=[0.0, 1.0], downstream_link_id=2,
    )
    block_b = RBDBlock(
        name="unnamed", id=4, fail_distro=10, fail_values=[0.0, 1.0],
        repair_distro=10, repair_values=[0.0, 1.0], downstream_link_id=4,
    )
    join = RBDNode(
        node_name="join", id=5, good_paths=1, in_paths_array=[2, 4],
        propagates_to_node_ids=[2], propagates_to_link_ids=[5],
    )
    end = RBDNode(node_name="end", id=2, good_paths=1, in_paths_array=[5])
    links_by_id = {
        2: Link(object_num=2, status=LinkStatus.UP),
        4: Link(object_num=4, status=LinkStatus.UP),
        5: Link(object_num=5, status=LinkStatus.UP),
    }
    nodes_by_id = {5: join, 2: end}
    return block_a, block_b, join, end, links_by_id, nodes_by_id


def _make_block_process(
    block, block_stats, rng_fail, rng_repair, scheduler, run_length,
    *, join, nodes_by_id, links_by_id, system_state, node_stats, all_blocks, logs,
):
    def apply_cascade(deltas, sim_time):
        rc, fc, ic, sc, pc = deltas
        result = reconfigure_after_block_change(
            block, join, links_by_id, rc, fc, ic, sc, pc, sim_time, events_file=True,
        )
        if result is not None and result.log_line is not None:
            logs.append(result.log_line)
            node_deltas = change_node_state(join, node_stats, result.new_status, join.active_status, sim_time)
            for r2 in reconfigure_after_node_change(
                join, nodes_by_id, links_by_id, *node_deltas, sim_time, events_file=True,
            ):
                if r2.log_line is not None:
                    logs.append(r2.log_line)
                    end_node = nodes_by_id[join.propagates_to_node_ids[0]]
                    prior_end_status = end_node.status
                    change_node_state(end_node, NodeStatistics(), r2.new_status, end_node.active_status, sim_time)
                    up_transition = end_node_system_up_transition(prior_end_status, end_node.status)
                    if up_transition is not None:
                        system_state.system_up = up_transition

        new_color = analyze_system(system_state)
        sys_lines = change_system_status(system_state, new_color, sim_time, all_blocks, events_file=True)
        logs.extend(sys_lines)

    def transition(new_status, new_active, comment, sim_time, *, phase_stress=None, cascade=True):
        old_op = block.op_status
        kwargs = {} if phase_stress is None else {"phase_stress": phase_stress}
        change_logs, (rc, fc, ic, sc, pc) = change_block_state(
            block, block_stats, new_status, new_active, comment, sim_time, events_file=True, **kwargs,
        )
        logs.extend(change_logs)
        if cascade:
            system_state.num_currently_failed += block_fail_count_delta(old_op, block.op_status)
            apply_cascade((rc, fc, ic, sc, pc), sim_time)

    def process():
        transition(BlockStatus.RUNNING, ActiveStatus.ACTIVE, "Initialize", 0.0, phase_stress=1.0)

        while True:
            operating_time = rng_fail.draw_number(Distribution(block.fail_distro), block.fail_values)
            if scheduler.time + operating_time >= run_length:
                # Don't log "Done" immediately: with concurrent processes
                # that would append this line out of chronological order
                # relative to another process's still-pending,
                # earlier-timestamped events. Yield so the scheduler
                # interleaves everything due before `run_length`, then log
                # once resumed.
                yield run_length - scheduler.time
                transition(BlockStatus.DONE, block.active_status, "End_sim", scheduler.time, cascade=False)
                return
            yield operating_time
            sim_time = scheduler.time
            logs.append(write_block_failed_line(block, sim_time, operating_time))

            transition(BlockStatus.REPAIRING, ActiveStatus.ACTIVE, "No_resources_used", sim_time)

            repair_time = rng_repair.draw_number(Distribution(block.repair_distro), block.repair_values)
            if sim_time + repair_time >= run_length:
                yield run_length - scheduler.time
                transition(BlockStatus.DONE, block.active_status, "End_sim", scheduler.time, cascade=False)
                return
            yield repair_time
            sim_time = scheduler.time
            logs.append(format_event_log_line(sim_time, "Block", block.name, "Repaired", f"MaintTime={repair_time:.6f}"))

            transition(BlockStatus.RUNNING, ActiveStatus.ACTIVE, "", sim_time, phase_stress=1.0)

    return process()


def test_two_concurrent_blocks_match_real_captured_log() -> None:
    block_a, block_b, join, _end, links_by_id, nodes_by_id = _make_scenario()
    stats_a, stats_b = BlockStatistics(), BlockStatistics()
    node_stats = NodeStatistics()
    system_state = SystemState()
    rng_fail_a = RandomGen(stream=RaptorStream(seed=fetch_raptor_seed(1)))
    rng_repair_a = RandomGen(stream=RaptorStream(seed=fetch_raptor_seed(2)))
    rng_fail_b = RandomGen(stream=RaptorStream(seed=fetch_raptor_seed(3)))
    rng_repair_b = RandomGen(stream=RaptorStream(seed=fetch_raptor_seed(4)))

    scheduler = Scheduler()
    logs: list[str] = []
    common = dict(
        join=join, nodes_by_id=nodes_by_id, links_by_id=links_by_id,
        system_state=system_state, node_stats=node_stats,
        all_blocks=[block_a, block_b], logs=logs,
    )
    proc_a = _make_block_process(block_a, stats_a, rng_fail_a, rng_repair_a, scheduler, _RUN_LENGTH, **common)
    proc_b = _make_block_process(block_b, stats_b, rng_fail_b, rng_repair_b, scheduler, _RUN_LENGTH, **common)
    scheduler.schedule_process(proc_a)
    scheduler.schedule_process(proc_b)
    scheduler.run_until(_RUN_LENGTH)

    golden = _golden_first_run_lines()
    # golden has 2 trailing lines this test doesn't attempt to reproduce:
    # "System Phase_change Mission_failure Finished_a_mission" and
    # "System Simulation_Terminate" -- mission/termination-summary
    # reporting, out of scope (see cascade.py/system_status.py module
    # docstrings on what's ported).
    assert "Mission_failure" in golden[-2], "expected the known out-of-scope trailing lines -- update this test if the fixture changed"
    assert "Simulation_Terminate" in golden[-1], "expected the known out-of-scope trailing lines -- update this test if the fixture changed"
    golden = golden[:-2]
    assert len(logs) == len(golden), f"line count differs: mine={len(logs)} golden={len(golden)}"

    counts = {"match": 0, "fp32-noise": 0, "structural": 0}
    first_structural = None
    for i, (mine, theirs) in enumerate(zip(logs, golden)):
        c = _classify(mine, theirs)
        counts[c] += 1
        if c == "structural" and first_structural is None:
            first_structural = i

    assert counts["structural"] == 0, (
        f"structural mismatch at line {first_structural}: "
        f"mine={logs[first_structural]!r} theirs={golden[first_structural]!r}"
    )
    assert counts["match"] + counts["fp32-noise"] > 50
