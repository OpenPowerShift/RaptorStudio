"""End-to-end test of the block -> link -> node cascade.

Replays captured cycles of `tests/golden/UniformProbe` using
`change_block_state` (blocks.py) through `reconfigure_after_block_change`
(cascade.py) into `reconfigure_node` (nodes.py), then diffs the combined
Block+Node log lines against the captured output. System lines are
excluded because that aggregation is not ported.
"""
from __future__ import annotations

import pathlib


from raptor.core.engine.blocks import BlockStatistics, change_block_state, write_block_failed_line
from raptor.core.engine.cascade import reconfigure_after_block_change
from raptor.core.engine.eventlog import format_event_log_line
from raptor.core.model import ActiveStatus, BlockStatus, Link, LinkStatus, RBDBlock, RBDNode
from raptor.core.rng import Distribution, RandomGen, RaptorStream, fetch_raptor_seed
from tests.unit.log_compare import classify as _classify, read_lines

GOLDEN = pathlib.Path(__file__).parent.parent / "golden"


def _golden_block_and_node_lines(max_lines: int) -> list[str]:
    path = GOLDEN / "UniformProbe" / "legacy" / "rpt_uniform_event_log_2.txt.gz"
    lines = read_lines(path, keep=lambda l: ("Block" in l or "Node" in l)
                       and "System" not in l)
    return lines[:max_lines]


def _make_scenario() -> tuple[RBDBlock, RBDNode, dict[int, Link]]:
    # Matches tests/golden/UniformProbe/input.rbd: one block, EFPAlink=2,
    # feeding a single "end" node with one incoming path (link 2), 1-of-1
    # required (good_paths=k=1).
    block = RBDBlock(
        name="unnamed", fail_distro=10, fail_values=[0.0, 1.0],
        repair_distro=10, repair_values=[0.0, 1.0], downstream_link_id=2,
    )
    node = RBDNode(node_name="end", good_paths=1, in_paths_array=[2])
    link = Link(object_num=2, status=LinkStatus.UP)
    return block, node, {2: link}


def test_block_node_cascade_matches_real_captured_log() -> None:
    block, node, links_by_id = _make_scenario()
    stats = BlockStatistics()
    rng_fail = RandomGen(stream=RaptorStream(seed=fetch_raptor_seed(1)))
    rng_repair = RandomGen(stream=RaptorStream(seed=fetch_raptor_seed(2)))

    logs: list[str] = []
    init_logs, *_ = change_block_state(
        block, stats, BlockStatus.RUNNING, ActiveStatus.ACTIVE, "Initialize",
        sim_time=0.0, phase_stress=1.0, events_file=True,
    )
    logs += init_logs
    sim_time = 0.0

    for _ in range(30):
        operating_time = rng_fail.draw_number(Distribution(block.fail_distro), block.fail_values)
        sim_time += operating_time
        logs.append(write_block_failed_line(block, sim_time, operating_time))

        repair_logs, (rc, fc, ic, sc, pc) = change_block_state(
            block, stats, BlockStatus.REPAIRING, ActiveStatus.ACTIVE, "No_resources_used",
            sim_time, events_file=True,
        )
        logs += repair_logs
        result = reconfigure_after_block_change(
            block, node, links_by_id, rc, fc, ic, sc, pc, sim_time, events_file=True,
        )
        if result is not None and result.log_line is not None:
            logs.append(result.log_line)

        repair_time = rng_repair.draw_number(Distribution(block.repair_distro), block.repair_values)
        sim_time += repair_time
        logs.append(format_event_log_line(sim_time, "Block", block.name, "Repaired", f"MaintTime={repair_time:.6f}"))

        running_logs, (rc, fc, ic, sc, pc) = change_block_state(
            block, stats, BlockStatus.RUNNING, ActiveStatus.ACTIVE, "",
            sim_time, phase_stress=1.0, events_file=True,
        )
        logs += running_logs
        result = reconfigure_after_block_change(
            block, node, links_by_id, rc, fc, ic, sc, pc, sim_time, events_file=True,
        )
        if result is not None and result.log_line is not None:
            logs.append(result.log_line)

    golden = _golden_block_and_node_lines(max_lines=len(logs))
    assert len(logs) == len(golden), (
        f"line count differs: mine={len(logs)} golden={len(golden)}"
    )

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
