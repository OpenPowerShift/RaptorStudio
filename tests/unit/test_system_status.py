"""End-to-end test of the full block -> link -> node -> system cascade.

Replays captured cycles of `tests/golden/UniformProbe` and diffs the
combined Block+Node+System log lines against the captured output -- the
entire event log for the scenario, not a subset of line types.

The pieces are composed here rather than by the orchestrator:
`blocks.change_block_state` -> `system_status.block_fail_count_delta` ->
`cascade.reconfigure_after_block_change` -> `nodes.change_node_state` ->
`system_status.end_node_system_up_transition` ->
`system_status.analyze_system` -> `system_status.change_system_status`.
"""
from __future__ import annotations

import pathlib
import re


from raptor.core.engine.blocks import BlockStatistics, change_block_state, write_block_failed_line
from raptor.core.engine.cascade import reconfigure_after_block_change
from raptor.core.engine.eventlog import format_event_log_line
from raptor.core.engine.nodes import NodeStatistics, change_node_state
from raptor.core.engine.run_control import reset_node_and_links_for_new_run, reset_system_state_for_new_run
from raptor.core.engine.system_status import (
    SystemState, analyze_system, block_fail_count_delta, change_system_status,
    end_node_system_up_transition, red_change_line,
)
from raptor.core.model import ActiveStatus, BlockStatus, Link, LinkStatus, RBDBlock, RBDNode
from raptor.core.rng import Distribution, RandomGen, RaptorStream, fetch_raptor_seed
from tests.unit.log_compare import classify as _classify, read_lines

GOLDEN = pathlib.Path(__file__).parent.parent / "golden"
_NUM_RE = re.compile(r"[\d.]+")




def _golden_lines(max_lines: int) -> list[str]:
    path = GOLDEN / "UniformProbe" / "legacy" / "rpt_uniform_event_log_2.txt.gz"
    lines = read_lines(path, keep=lambda l: any(
        k in l for k in ("Block", "Node", "State_change")))
    return lines[:max_lines]


def _make_scenario() -> tuple[RBDBlock, RBDNode, dict[int, Link]]:
    block = RBDBlock(
        name="unnamed", id=3, fail_distro=10, fail_values=[0.0, 1.0],
        repair_distro=10, repair_values=[0.0, 1.0], downstream_link_id=2,
    )
    node = RBDNode(node_name="end", good_paths=1, in_paths_array=[2])
    link = Link(object_num=2, status=LinkStatus.UP)
    return block, node, {2: link}


def _do_block_transition(
    block, block_stats, node, node_stats, links_by_id, system_state, logs,
    new_op_status, new_active_status, comment, sim_time, *, phase_stress=None,
) -> None:
    old_op_status = block.op_status
    kwargs = {} if phase_stress is None else {"phase_stress": phase_stress}
    change_logs, (rc, fc, ic, sc, pc) = change_block_state(
        block, block_stats, new_op_status, new_active_status, comment,
        sim_time, events_file=True, **kwargs,
    )
    logs += change_logs
    system_state.num_currently_failed += block_fail_count_delta(old_op_status, block.op_status)

    result = reconfigure_after_block_change(
        block, node, links_by_id, rc, fc, ic, sc, pc, sim_time, events_file=True,
    )
    if result is not None and result.log_line is not None:
        logs.append(result.log_line)
        prior_node_status = node.status
        change_node_state(node, node_stats, result.new_status, node.active_status, sim_time)
        up_transition = end_node_system_up_transition(prior_node_status, node.status)
        if up_transition is not None:
            system_state.system_up = up_transition

    new_color = analyze_system(system_state)
    if new_color != system_state.color:
        sys_lines = change_system_status(system_state, new_color, sim_time, [block], events_file=True)
        logs.extend(sys_lines)
    else:
        red_line = red_change_line(system_state, sim_time, [block], events_file=True)
        if red_line is not None:
            logs.append(red_line)


def test_full_block_node_system_cascade_matches_real_captured_log() -> None:
    block, node, links_by_id = _make_scenario()
    block_stats = BlockStatistics()
    node_stats = NodeStatistics()
    system_state = SystemState()
    rng_fail = RandomGen(stream=RaptorStream(seed=fetch_raptor_seed(1)))
    rng_repair = RandomGen(stream=RaptorStream(seed=fetch_raptor_seed(2)))

    logs: list[str] = []
    _do_block_transition(
        block, block_stats, node, node_stats, links_by_id, system_state, logs,
        BlockStatus.RUNNING, ActiveStatus.ACTIVE, "Initialize", 0.0, phase_stress=1.0,
    )
    sim_time = 0.0

    for _ in range(30):
        operating_time = rng_fail.draw_number(Distribution(block.fail_distro), block.fail_values)
        sim_time += operating_time
        logs.append(write_block_failed_line(block, sim_time, operating_time))

        _do_block_transition(
            block, block_stats, node, node_stats, links_by_id, system_state, logs,
            BlockStatus.REPAIRING, ActiveStatus.ACTIVE, "No_resources_used", sim_time,
        )

        repair_time = rng_repair.draw_number(Distribution(block.repair_distro), block.repair_values)
        sim_time += repair_time
        logs.append(format_event_log_line(sim_time, "Block", block.name, "Repaired", f"MaintTime={repair_time:.6f}"))

        _do_block_transition(
            block, block_stats, node, node_stats, links_by_id, system_state, logs,
            BlockStatus.RUNNING, ActiveStatus.ACTIVE, "", sim_time, phase_stress=1.0,
        )

    golden = _golden_lines(max_lines=len(logs))
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
    assert counts["match"] + counts["fp32-noise"] > 80


def _golden_ldt_full_lines(max_lines: int) -> list[str]:
    path = GOLDEN / "PrePostLDT" / "legacy" / "rpt_pre_post_ldt.txt"
    lines = read_lines(path, keep=lambda l: any(
        k in l for k in ("Block", "Node", "State_change", "Red_change")))
    return lines[:max_lines]


def test_ldt_full_cascade_matches_real_captured_log() -> None:
    """The same shape as the full cascade test, for the LDT-configured
    block (`tests/golden/PrePostLDT`) across all 5 captured runs. This
    exercises `red_change_line`: PrePostLDT is the only fixture where the
    system stays Red across multiple block-state changes within one down
    period. It also covers the run-boundary "no cascade after End_sim"
    rule.
    """
    from raptor.core.engine.block_lifecycle import _has_ldt

    block = RBDBlock(
        name="unnamed", id=3, fail_distro=10, fail_values=[0.0, 1.0],
        repair_distro=10, repair_values=[0.0, 1.0], downstream_link_id=2,
        pre_dist=19, pre_params=[0.05, 0.0, 0.0],
        post_dist=19, post_params=[0.05, 0.0, 0.0],
    )
    node = RBDNode(node_name="end", good_paths=1, in_paths_array=[2])
    links_by_id = {2: Link(object_num=2, status=LinkStatus.UP)}
    block_stats = BlockStatistics()
    node_stats = NodeStatistics()
    system_state = SystemState()
    rng_fail = RandomGen(stream=RaptorStream(seed=fetch_raptor_seed(1)))
    rng_repair = RandomGen(stream=RaptorStream(seed=fetch_raptor_seed(2)))
    run_length = 100.0

    logs: list[str] = []
    _do_block_transition(
        block, block_stats, node, node_stats, links_by_id, system_state, logs,
        BlockStatus.RUNNING, ActiveStatus.ACTIVE, "Initialize", 0.0, phase_stress=1.0,
    )
    sim_time = 0.0

    def _end_sim() -> None:
        end_logs, *_ = change_block_state(
            block, block_stats, BlockStatus.DONE, block.active_status, "End_sim",
            run_length, events_file=True,
        )
        logs.extend(end_logs)

    for _ in range(5):
        while True:
            operating_time = rng_fail.draw_number(Distribution(block.fail_distro), block.fail_values)
            if sim_time + operating_time >= run_length:
                _end_sim()
                sim_time = run_length
                break
            sim_time += operating_time
            logs.append(write_block_failed_line(block, sim_time, operating_time))

            if _has_ldt(block.pre_dist, block.pre_params):
                _do_block_transition(
                    block, block_stats, node, node_stats, links_by_id, system_state, logs,
                    BlockStatus.REP_HOLD, ActiveStatus.ACTIVE, "LDT", sim_time,
                )
                ldt_wait = rng_repair.draw_number(Distribution(block.pre_dist), block.pre_params)
                if sim_time + ldt_wait >= run_length:
                    _end_sim()
                    sim_time = run_length
                    break
                sim_time += ldt_wait
                logs.append(format_event_log_line(sim_time, "Block", block.name, "End_Pre-LDT", f"Time={ldt_wait:.6f}"))

            _do_block_transition(
                block, block_stats, node, node_stats, links_by_id, system_state, logs,
                BlockStatus.REPAIRING, ActiveStatus.ACTIVE, "No_resources_used", sim_time,
            )

            repair_time = rng_repair.draw_number(Distribution(block.repair_distro), block.repair_values)
            if sim_time + repair_time >= run_length:
                _end_sim()
                sim_time = run_length
                break
            sim_time += repair_time
            logs.append(format_event_log_line(sim_time, "Block", block.name, "Repaired", f"MaintTime={repair_time:.6f}"))

            if _has_ldt(block.post_dist, block.post_params):
                _do_block_transition(
                    block, block_stats, node, node_stats, links_by_id, system_state, logs,
                    BlockStatus.REP_HOLD, ActiveStatus.ACTIVE, "postLDT", sim_time,
                )
                ldt_wait = rng_repair.draw_number(Distribution(block.post_dist), block.post_params)
                if sim_time + ldt_wait >= run_length:
                    _end_sim()
                    sim_time = run_length
                    break
                sim_time += ldt_wait
                logs.append(format_event_log_line(sim_time, "Block", block.name, "End_Post-LDT", f"Time={ldt_wait:.6f}"))

            _do_block_transition(
                block, block_stats, node, node_stats, links_by_id, system_state, logs,
                BlockStatus.RUNNING, ActiveStatus.ACTIVE, "", sim_time, phase_stress=1.0,
            )

        # A new run starts fresh at SimTime=0.0: the block's status plus
        # node/link/system state are reset directly rather than through
        # _do_block_transition's delta tracking (block_fail_count_delta
        # (Done, Running) would decrement num_currently_failed below zero).
        # This matches a run-start procedure resetting these globals.
        init_logs, *_ = change_block_state(
            block, block_stats, BlockStatus.RUNNING, ActiveStatus.ACTIVE, "Initialize",
            0.0, phase_stress=1.0, events_file=True,
        )
        logs.extend(init_logs)
        reset_node_and_links_for_new_run(node, links_by_id)
        reset_system_state_for_new_run(system_state)
        sim_time = 0.0

    # The final "Initialize" above starts a 6th run outside the capture
    # (only 5 captured runs exist); drop its lone log line before comparing.
    logs = logs[:-1]

    golden = _golden_ldt_full_lines(max_lines=len(logs))
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
    assert "Red_change" in " ".join(logs), "sanity check: should have exercised red_change_line at least once"
    assert counts["match"] + counts["fp32-noise"] > 1000
