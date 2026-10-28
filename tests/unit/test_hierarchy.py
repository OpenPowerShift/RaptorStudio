"""End-to-end test of the hierarchy-internal-exit-node log line for a
single block inside one hierarchy (`start -> Hier(H) -> end`, internally
`in -> block -> out`).

It checks that `nodes.determine_node_status`'s `type_node=5` branch emits
`"Hier"` / the hierarchy name instead of `"Node"` / the node name, and
that the hierarchy's external `out -> end` link cascades exactly like an
ordinary node-to-node connection -- the `out` node's own
`propagates_to_node_ids` / `propagates_to_link_ids` point at `end`, the same mechanism
as any other node.

Golden fixture: `tests/golden/SingleHierarchy`.
"""
from __future__ import annotations

import pathlib
import re


from raptor.core.engine.blocks import BlockStatistics, change_block_state, write_block_failed_line
from raptor.core.engine.cascade import reconfigure_after_block_change, reconfigure_after_node_change
from raptor.core.engine.eventlog import format_event_log_line
from raptor.core.engine.nodes import NodeStatistics, change_node_state
from raptor.core.engine.system_status import (
    SystemState, analyze_system, block_fail_count_delta, change_system_status,
    end_node_system_up_transition,
)
from raptor.core.engine.run_control import reset_node_and_links_for_new_run, reset_system_state_for_new_run
from raptor.core.model import ActiveStatus, BlockStatus, Link, LinkStatus, RBDBlock, RBDNode
from raptor.core.rng import Distribution, RandomGen, RaptorStream, fetch_raptor_seed
from raptor.core.scheduler import Scheduler
from tests.unit.log_compare import (
    LANDMARK_BEGIN_RUN, classify as _classify, read_lines, split_runs)

GOLDEN = pathlib.Path(__file__).parent.parent / "golden"
_NUM_RE = re.compile(r"[\d.]+")
_RUN_LENGTH = 100.0
_NUM_RUNS = 5




def _golden_run_lines() -> list[list[str]]:
    path = GOLDEN / "SingleHierarchy" / "legacy" / "rpt_events_singlehierarchy.txt"
    lines = read_lines(path)
    return split_runs(lines, LANDMARK_BEGIN_RUN, num_runs=_NUM_RUNS)


def _make_scenario():
    block = RBDBlock(
        name="unnamed", id=3, fail_distro=10, fail_values=[0.0, 1.0],
        repair_distro=10, repair_values=[0.0, 1.0], downstream_link_id=4,
    )
    # The hierarchy's internal exit node. Its own downstream connection
    # (`propagates_to_node_ids` / `propagates_to_link_ids`) is the hierarchy's external
    # "Hier -> Node" link (link 2 in the fixture: Hier(10) -> Node(end, 2)),
    # the same shape as any other node's fan-out.
    out_node = RBDNode(
        node_name="out", id=12, type_node=5, good_paths=1, in_paths_array=[4],
        propagates_to_node_ids=[2], propagates_to_link_ids=[2],
    )
    end = RBDNode(node_name="end", id=2, good_paths=1, in_paths_array=[2])
    links_by_id = {
        2: Link(object_num=2, status=LinkStatus.UP),
        4: Link(object_num=4, status=LinkStatus.UP),
    }
    nodes_by_id = {12: out_node, 2: end}
    return block, out_node, end, links_by_id, nodes_by_id


def _run_once(block, out_node, end, links_by_id, nodes_by_id, rng_fail, rng_repair):
    stats = BlockStatistics()
    node_stats = NodeStatistics()
    system_state = SystemState()
    reset_node_and_links_for_new_run(out_node, links_by_id)
    reset_node_and_links_for_new_run(end, links_by_id)
    reset_system_state_for_new_run(system_state)
    block.op_status = BlockStatus.RUNNING
    block.active_status = ActiveStatus.ACTIVE
    block.state_start_time = 0.0

    scheduler = Scheduler()
    logs: list[str] = []

    def apply_cascade(deltas, sim_time):
        rc, fc, ic, sc, pc = deltas
        result = reconfigure_after_block_change(
            block, out_node, links_by_id, rc, fc, ic, sc, pc, sim_time, events_file=True, hier_name="H",
        )
        if result is not None and result.log_line is not None:
            logs.append(result.log_line)
            node_deltas = change_node_state(out_node, node_stats, result.new_status, out_node.active_status, sim_time)
            for r2 in reconfigure_after_node_change(
                out_node, nodes_by_id, links_by_id, *node_deltas, sim_time, events_file=True,
            ):
                if r2.log_line is not None:
                    logs.append(r2.log_line)
                    prior_end_status = end.status
                    change_node_state(end, NodeStatistics(), r2.new_status, end.active_status, sim_time)
                    up_transition = end_node_system_up_transition(prior_end_status, end.status)
                    if up_transition is not None:
                        system_state.system_up = up_transition

        new_color = analyze_system(system_state)
        sys_lines = change_system_status(system_state, new_color, sim_time, [block], events_file=True)
        logs.extend(sys_lines)

    def transition(new_status, new_active, comment, sim_time, *, phase_stress=None, cascade=True):
        old_op = block.op_status
        kwargs = {} if phase_stress is None else {"phase_stress": phase_stress}
        change_logs, (rc, fc, ic, sc, pc) = change_block_state(
            block, stats, new_status, new_active, comment, sim_time, events_file=True, **kwargs,
        )
        logs.extend(change_logs)
        if cascade:
            system_state.num_currently_failed += block_fail_count_delta(old_op, block.op_status)
            apply_cascade((rc, fc, ic, sc, pc), sim_time)

    def process():
        transition(BlockStatus.RUNNING, ActiveStatus.ACTIVE, "Initialize", 0.0, phase_stress=1.0)
        while True:
            operating_time = rng_fail.draw_number(Distribution(block.fail_distro), block.fail_values)
            if scheduler.time + operating_time >= _RUN_LENGTH:
                yield _RUN_LENGTH - scheduler.time
                transition(BlockStatus.DONE, block.active_status, "End_sim", scheduler.time, cascade=False)
                return
            yield operating_time
            sim_time = scheduler.time
            logs.append(write_block_failed_line(block, sim_time, operating_time))

            transition(BlockStatus.REPAIRING, ActiveStatus.ACTIVE, "No_resources_used", sim_time)

            repair_time = rng_repair.draw_number(Distribution(block.repair_distro), block.repair_values)
            if sim_time + repair_time >= _RUN_LENGTH:
                yield _RUN_LENGTH - scheduler.time
                transition(BlockStatus.DONE, block.active_status, "End_sim", scheduler.time, cascade=False)
                return
            yield repair_time
            sim_time = scheduler.time
            logs.append(format_event_log_line(sim_time, "Block", block.name, "Repaired", f"MaintTime={repair_time:.6f}"))

            transition(BlockStatus.RUNNING, ActiveStatus.ACTIVE, "", sim_time, phase_stress=1.0)

    scheduler.schedule_process(process())
    scheduler.run_until(_RUN_LENGTH)
    return logs


def test_hierarchy_exit_node_matches_real_captured_log_across_all_runs() -> None:
    block, out_node, end, links_by_id, nodes_by_id = _make_scenario()
    rng_fail = RandomGen(stream=RaptorStream(seed=fetch_raptor_seed(1)))
    rng_repair = RandomGen(stream=RaptorStream(seed=fetch_raptor_seed(2)))

    golden_runs = _golden_run_lines()

    total = {"match": 0, "fp32-noise": 0, "structural": 0}
    for run_index in range(_NUM_RUNS):
        logs = _run_once(block, out_node, end, links_by_id, nodes_by_id, rng_fail, rng_repair)

        golden = golden_runs[run_index]
        assert "Mission_failure" in golden[-2], f"run {run_index}: expected known trailing lines"
        assert "Simulation_Terminate" in golden[-1], f"run {run_index}: expected known trailing lines"
        golden = golden[:-2]
        assert len(logs) == len(golden), (
            f"run {run_index}: line count differs: mine={len(logs)} golden={len(golden)}"
        )

        first_structural = None
        for i, (mine, theirs) in enumerate(zip(logs, golden)):
            c = _classify(mine, theirs)
            total[c] += 1
            if c == "structural" and first_structural is None:
                first_structural = i
        assert first_structural is None, (
            f"run {run_index}: structural mismatch at line {first_structural}: "
            f"mine={logs[first_structural]!r} theirs={golden[first_structural]!r}"
        )

    assert total["structural"] == 0
    assert total["match"] + total["fp32-noise"] > 250
    hier_count = sum(1 for run in golden_runs for l in run if "\tHier\t" in l or "Hier     " in l)
    assert hier_count > 400
