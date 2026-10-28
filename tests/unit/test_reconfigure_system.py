"""End-to-end test of `cascade.reconfigure_system` -- the "permanently
down" / path-reachability mechanism for a block that runs out of spares,
layered on the block -> link -> node -> system cascade covered by
`test_system_status.py`.

Only the single-hop (`typeNode=3`) case is exercised here; the `Cascade`
fixture in `test_orchestrator.py` covers the multi-hop recursive case
through the real orchestrator. See `tests/golden/FiniteSparePool` and
`cascade.py`'s module docstring.
"""
from __future__ import annotations

import pathlib
import re


from raptor.core.engine.blocks import BlockStatistics, change_block_state, write_block_failed_line
from raptor.core.engine.cascade import reconfigure_after_block_change, reconfigure_system
from raptor.core.engine.eventlog import format_event_log_line
from raptor.core.engine.nodes import NodeStatistics, change_node_state
from raptor.core.engine.run_control import reset_node_and_links_for_new_run, reset_system_state_for_new_run
from raptor.core.engine.system_status import (
    SystemState, analyze_system, block_fail_count_delta, change_system_status,
    end_node_system_up_transition,
)
from raptor.core.model import ActiveStatus, BlockStatus, Link, LinkStatus, RBDBlock, RBDNode, SparingType
from raptor.core.rng import Distribution, RandomGen, RaptorStream, fetch_raptor_seed
from tests.unit.log_compare import classify as _classify, read_lines

GOLDEN = pathlib.Path(__file__).parent.parent / "golden"
_NUM_RE = re.compile(r"[\d.]+")
_RUN_LENGTH = 100.0




def _golden_lines(max_lines: int) -> list[str]:
    path = GOLDEN / "FiniteSparePool" / "legacy" / "rpt_finite_spare_pool.txt.gz"
    lines = read_lines(path, keep=lambda l: any(
        k in l for k in ("Block", "Node", "State_change", "Permanently_Down")))
    return lines[:max_lines]


def test_finite_spare_pool_permanently_down_matches_real_captured_log() -> None:
    block = RBDBlock(
        name="unnamed", id=3, fail_distro=10, fail_values=[0.0, 1.0],
        repair_distro=10, repair_values=[0.0, 1.0], downstream_link_id=2,
        sparing_type=SparingType.CUSTOM, init_stock=3,
    )
    node = RBDNode(node_name="end", type_node=3, good_paths=1, in_paths_array=[2])
    link = Link(object_num=2, status=LinkStatus.UP, active=True)
    links_by_id = {2: link}
    block_stats = BlockStatistics()
    node_stats = NodeStatistics()
    system_state = SystemState()
    rng_fail = RandomGen(stream=RaptorStream(seed=fetch_raptor_seed(1)))
    rng_repair = RandomGen(stream=RaptorStream(seed=fetch_raptor_seed(2)))

    def transition(new_status, new_active, comment, sim_time, *, phase_stress=None) -> None:
        old_op_status = block.op_status
        kwargs = {} if phase_stress is None else {"phase_stress": phase_stress}
        change_logs, (rc, fc, ic, sc, pc) = change_block_state(
            block, block_stats, new_status, new_active, comment, sim_time, events_file=True, **kwargs,
        )
        logs.extend(change_logs)
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

    def reset_for_new_run() -> None:
        reset_node_and_links_for_new_run(node, links_by_id)
        reset_system_state_for_new_run(system_state)

    logs: list[str] = []
    transition(BlockStatus.RUNNING, ActiveStatus.ACTIVE, "Initialize", 0.0, phase_stress=1.0)
    sim_time = 0.0

    for _ in range(5):
        spares_remaining = block.init_stock
        while True:
            operating_time = rng_fail.draw_number(Distribution(block.fail_distro), block.fail_values)
            if sim_time + operating_time >= _RUN_LENGTH:
                end_logs, *_ = change_block_state(
                    block, block_stats, BlockStatus.DONE, block.active_status, "End_sim",
                    _RUN_LENGTH, events_file=True,
                )
                logs.extend(end_logs)
                sim_time = _RUN_LENGTH
                break
            sim_time += operating_time
            logs.append(write_block_failed_line(block, sim_time, operating_time))

            if spares_remaining <= 0:
                logs.append(format_event_log_line(
                    sim_time, "Block", block.name, "Sparing", "No_spare_in_stock-Custom",
                ))
                # Running -> Done produces a RunChange=-1/FailChange=1
                # cascade (ChangeBlockState's "oldActiveStatus=Linked
                # {now Active}" branch), routed through the same
                # transition() wrapper as every other state change; the
                # real log places Node/System State_change lines before
                # Permanently_Down.
                transition(BlockStatus.DONE, block.active_status, "No_spares", sim_time)
                pd_lines = reconfigure_system(
                    node, {node.id: node}, links_by_id, block.downstream_link_id, sim_time, events_file=True,
                )
                logs.extend(pd_lines)
                end_logs, *_ = change_block_state(
                    block, block_stats, BlockStatus.DONE, block.active_status, "End_sim",
                    _RUN_LENGTH, events_file=True,
                )
                logs.extend(end_logs)
                sim_time = _RUN_LENGTH
                break
            spares_remaining -= 1

            transition(BlockStatus.REPAIRING, ActiveStatus.ACTIVE, "No_resources_used", sim_time)

            repair_time = rng_repair.draw_number(Distribution(block.repair_distro), block.repair_values)
            if sim_time + repair_time >= _RUN_LENGTH:
                end_logs, *_ = change_block_state(
                    block, block_stats, BlockStatus.DONE, block.active_status, "End_sim",
                    _RUN_LENGTH, events_file=True,
                )
                logs.extend(end_logs)
                sim_time = _RUN_LENGTH
                break
            sim_time += repair_time
            logs.append(format_event_log_line(sim_time, "Block", block.name, "Repaired", f"MaintTime={repair_time:.6f}"))

            transition(BlockStatus.RUNNING, ActiveStatus.ACTIVE, "", sim_time, phase_stress=1.0)

        init_logs, *_ = change_block_state(
            block, block_stats, BlockStatus.RUNNING, ActiveStatus.ACTIVE, "Initialize",
            0.0, phase_stress=1.0, events_file=True,
        )
        logs.extend(init_logs)
        reset_for_new_run()
        sim_time = 0.0

    # The final "Initialize" starts a 6th run outside the capture -- drop it.
    logs = logs[:-1]

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
    assert "Permanently_Down" in " ".join(logs), "sanity check: should have exercised reconfigure_system"
    assert counts["match"] + counts["fp32-noise"] > 50
def test_reconfigure_system_uses_min_good_paths_req() -> None:
    """`ReconfigureSystem` triggers on `NumActivePaths == MinGoodPathsReq
    - 1` (the minimum per-phase k when phased), not the current
    `goodPaths`. Here `good_paths=3` but `min_good_paths_required=2`, so
    deactivating the second of three paths (active 3 -> 2 -> 1) fires
    `Permanently_Down`; using `good_paths` would need active==2."""
    from raptor.core.engine.cascade import reconfigure_system
    from raptor.core.model import RBDNode, Link

    node = RBDNode(id=2, node_name="end", type_node=3, good_paths=3,
                   min_good_paths_required=2, in_paths_array=[10, 11, 12])
    links = {i: Link(active=True) for i in (10, 11, 12)}
    nodes = {2: node}

    assert reconfigure_system(node, nodes, links, 10, 1.0, events_file=True) == []
    logs = reconfigure_system(node, nodes, links, 11, 2.0, events_file=True)
    assert any("Permanently_Down" in l for l in logs)

