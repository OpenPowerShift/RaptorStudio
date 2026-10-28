"""End-to-end test of `dependency.py`'s `BackFlowDependencies` port.

The scenario is the two-block join->end topology from
`test_two_block_cascade.py`, but block B (id=4) depends 100% on block A
(id=3): `DepNum=3, DepIdle=100.00, DepType=RBDBlock`. Golden source:
`tests/golden/BlockDependency` (5 runs).
"""
from __future__ import annotations

import pathlib
import re

import pytest

from raptor.core.engine.blocks import BlockStatistics, change_block_state, write_block_failed_line
from raptor.core.engine.cascade import reconfigure_after_block_change, reconfigure_after_node_change
from raptor.core.engine.dependency import (
    BackFlowDeps, back_flow_dependencies, back_flow_dependencies_from_node, back_flow_dependency_trigger,
    draw_dependency_action,
    fail_mean, repair_completion_status, resend_back_flow, resend_back_flow_would_apply, resolve_dependency_node_id,
)
from raptor.core.engine.errors import EngineInvariantError
from raptor.core.engine.eventlog import format_event_log_line
from raptor.core.engine.nodes import NodeStatistics, change_node_state
from raptor.core.engine.run_control import reset_node_and_links_for_new_run, reset_system_state_for_new_run
from raptor.core.engine.system_status import (
    SystemState, analyze_system, block_fail_count_delta, change_system_status,
    end_node_system_up_transition, red_change_line,
)
from raptor.core.model import (
    ActiveStatus, BlockRunState, BlockStatus, EventStatus, Link, LinkStatus, NodeStatus, RBDBlock, RBDEvent, RBDNode,
)
from raptor.core.rng import Distribution, RandomGen, RaptorStream, fetch_raptor_seed
from raptor.core.scheduler import Scheduler, Wake
from tests.unit.log_compare import (
    LANDMARK_BEGIN_RUN, classify as _classify, read_lines, split_runs)

GOLDEN = pathlib.Path(__file__).parent.parent / "golden"
_NUM_RE = re.compile(r"[\d.]+")
_RUN_LENGTH = 100.0
_NUM_RUNS = 5
#: Duration used for a dependency-forced Idle block's "wait indefinitely
#: for the next Dependence_All_Up interrupt" -- the analog of Run's
#: `WAIT FOR BlockTrigger[seqNum] TO Fire` (no natural timeout). Just
#: needs to be safely past `_RUN_LENGTH` so it never fires on its own.
_FAR_FUTURE = _RUN_LENGTH * 10.0




def _golden_run_lines() -> list[list[str]]:
    path = GOLDEN / "BlockDependency" / "legacy" / "rpt_blockdependency_events.txt"
    lines = read_lines(path)
    return split_runs(lines, LANDMARK_BEGIN_RUN, num_runs=_NUM_RUNS)


def _make_scenario():
    block_a = RBDBlock(
        name="unnamed", id=3, fail_distro=10, fail_values=[0.0, 1.0],
        repair_distro=10, repair_values=[0.0, 1.0], downstream_link_id=2,
    )
    block_b = RBDBlock(
        name="unnamed", id=4, fail_distro=10, fail_values=[0.0, 1.0],
        repair_distro=10, repair_values=[0.0, 1.0], downstream_link_id=4,
        dependency_num=3, dep_type="RBDBlock", dep_idle_percent=100.0,
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
    *, join, nodes_by_id, links_by_id, system_state, node_stats, all_blocks, logs, transitions, handles,
):
    def apply_node_cascade(deltas, sim_time):
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

    def transition(new_status, new_active, comment, sim_time, *, phase_stress=None, cascade=True, top_level=True):
        old_op = block.op_status
        kwargs = {} if phase_stress is None else {"phase_stress": phase_stress}
        change_logs, (rc, fc, ic, sc, pc) = change_block_state(
            block, block_stats, new_status, new_active, comment, sim_time, events_file=True, **kwargs,
        )
        logs.extend(change_logs)
        if cascade:
            system_state.num_currently_failed += block_fail_count_delta(old_op, block.op_status)
            # BackFlowDependencies fires before this block's own node
            # cascade (see dependency.py's module docstring). Both this
            # block's and any dependents' node cascades must finish before
            # the single system-status recompute for the whole triggering
            # event: a dependency-triggered nested transition
            # (top_level=False) never computes its own System line, only
            # the outermost caller does, after everything downstream of it
            # (including nested dependency cascades) has run.
            trigger = back_flow_dependency_trigger(rc, fc, ic, sc, pc)
            if trigger is not None:
                back_flow_dependencies(trigger, block, all_blocks, dispatch_transition, sim_time)
            apply_node_cascade((rc, fc, ic, sc, pc), sim_time)
            if top_level:
                new_color = analyze_system(system_state)
                sys_lines = change_system_status(system_state, new_color, sim_time, all_blocks, events_file=True)
                if sys_lines:
                    logs.extend(sys_lines)
                else:
                    # change_system_status is a no-op when the color didn't
                    # actually change -- if it's (still) Red, this is the
                    # repeated-Red comment-tracking line instead.
                    red_line = red_change_line(system_state, sim_time, all_blocks, events_file=True)
                    if red_line is not None:
                        logs.append(red_line)

    def dispatch_transition(dep_block, new_status, comment, sim_time):
        # ChangeBlockState fires BEFORE Interrupt, matching Raptor 7's
        # BackFlowDependencies (e.g. `ASK tempBlock TO ChangeBlockState(
        # Idle,...); IF tempBlock.FailWait Interrupt(tempBlock,"Run")`):
        # apply the state change (and its own full cascade) first, THEN
        # wake the dependent block's own suspended process so it can
        # react to that already-applied new state.
        transitions[dep_block.id](new_status, dep_block.active_status, comment, sim_time, top_level=False)
        reason = "Dependence_Idle" if new_status == BlockStatus.IDLE else "Dependence_All_Up"
        scheduler.interrupt(handles[dep_block.id], reason)

    transitions[block.id] = transition

    def process():
        transition(BlockStatus.RUNNING, ActiveStatus.ACTIVE, "Initialize", 0.0, phase_stress=1.0)
        # `None` means "needs a fresh draw" (true start, or after a repair
        # that found the dependency still down -- FailTimeLeft was fully
        # consumed by that failure/repair cycle, so `Run`'s later `IF
        # FailTimeLeft=0.0` redraws from scratch); a float is a preserved
        # PARTIAL countdown, only ever set by a Dependence_Idle interrupt
        # mid-FailWait -- these are two genuinely different mechanisms
        # (see dependency.py's `repair_completion_status` docstring).
        fail_time_left = rng_fail.draw_number(Distribution(block.fail_distro), block.fail_values)
        original_draw = fail_time_left

        while True:
            # Dependency-forced Idle: wait indefinitely for the matching
            # Dependence_All_Up interrupt (Run's `WAIT FOR BlockTrigger`
            # loop-top branch when opStatus=Idle) before resuming.
            while block.op_status == BlockStatus.IDLE:
                resume = yield _FAR_FUTURE
                assert isinstance(resume, Wake) and resume.reason == "Dependence_All_Up", resume

            if fail_time_left is None:
                fail_time_left = rng_fail.draw_number(Distribution(block.fail_distro), block.fail_values)
                original_draw = fail_time_left

            # No run-length short-circuit here: a wait that lands beyond
            # `run_length` simply never gets resumed by `run_until` (its
            # calendar entry sits past `end_time` and is left alone). Every
            # still-active block is force-`Done`-ed centrally at exactly
            # `run_length` after `run_until` returns (see `_run_once`),
            # matching captures that show both blocks' "Done End_sim"
            # lines at exactly t=100.000000 regardless of where each one's
            # own countdown was.
            wait_start = scheduler.time
            resume = yield fail_time_left
            if isinstance(resume, Wake):
                assert resume.reason == "Dependence_Idle", resume
                # ON INTERRUPT: FailTimeLeft:=(FailTimeLeft-(SimTime-TimeHack))*CombinedStress
                # -- CombinedStress=1.0 always in this fixture (no phasing/
                # standby/growth-decay), so just subtract elapsed time.
                fail_time_left -= scheduler.time - wait_start
                continue  # returnToTop=TRUE -- loop back to the idle-wait check above

            sim_time = scheduler.time
            logs.append(write_block_failed_line(block, sim_time, original_draw))
            fail_time_left = None  # consumed by this real failure

            transition(BlockStatus.REPAIRING, ActiveStatus.ACTIVE, "No_resources_used", sim_time)

            repair_time = rng_repair.draw_number(Distribution(block.repair_distro), block.repair_values)
            yield repair_time
            sim_time = scheduler.time
            logs.append(format_event_log_line(sim_time, "Block", block.name, "Repaired", f"MaintTime={repair_time:.6f}"))

            # DetermineNewState("Maintenance Completed") -- ported as
            # dependency.repair_completion_status: a dependent block that
            # finishes repair while its depended-upon block is still down
            # goes straight to Idle (comment="", the "change_block_state"
            # fallback string), not Running.
            recovery_status, _needs_recheck = repair_completion_status(block, all_blocks)
            if recovery_status == BlockStatus.RUNNING:
                transition(BlockStatus.RUNNING, ActiveStatus.ACTIVE, "", sim_time, phase_stress=1.0)
            else:
                transition(BlockStatus.IDLE, ActiveStatus.ACTIVE, "", sim_time)
                # fail_time_left stays None -- next loop iteration's idle-
                # wait branch engages immediately (op_status is now Idle).

    return process()


def _run_once(block_a, block_b, join, end, links_by_id, nodes_by_id, rngs, run_index):
    stats_a, stats_b = BlockStatistics(), BlockStatistics()
    node_stats = NodeStatistics()
    system_state = SystemState()
    reset_node_and_links_for_new_run(join, links_by_id)
    reset_node_and_links_for_new_run(end, links_by_id)
    reset_system_state_for_new_run(system_state)
    block_a.op_status = BlockStatus.RUNNING
    block_a.active_status = ActiveStatus.ACTIVE
    block_a.state_start_time = 0.0
    block_b.op_status = BlockStatus.RUNNING
    block_b.active_status = ActiveStatus.ACTIVE
    block_b.state_start_time = 0.0

    scheduler = Scheduler()
    logs: list[str] = []
    transitions: dict[int, object] = {}
    handles: dict[int, object] = {}
    common = dict(
        join=join, nodes_by_id=nodes_by_id, links_by_id=links_by_id,
        system_state=system_state, node_stats=node_stats,
        all_blocks=[block_a, block_b], logs=logs, transitions=transitions, handles=handles,
    )
    rng_fail_a, rng_repair_a, rng_fail_b, rng_repair_b = rngs
    proc_a = _make_block_process(block_a, stats_a, rng_fail_a, rng_repair_a, scheduler, _RUN_LENGTH, **common)
    proc_b = _make_block_process(block_b, stats_b, rng_fail_b, rng_repair_b, scheduler, _RUN_LENGTH, **common)
    handles[block_a.id] = scheduler.schedule_process(proc_a)
    handles[block_b.id] = scheduler.schedule_process(proc_b)
    scheduler.run_until(_RUN_LENGTH)

    # Force-end every still-active block at exactly run_length: every
    # BlockDependency run shows both blocks' "Done End_sim" lines at
    # exactly t=100.000000, uncascaded, regardless of each block's own
    # in-progress wait. This is a run-boundary termination step, not each
    # block's countdown landing there by coincidence.
    for b in (block_a, block_b):
        if b.op_status != BlockStatus.DONE:
            transitions[b.id](BlockStatus.DONE, b.active_status, "End_sim", _RUN_LENGTH, cascade=False)
    return logs


def test_dependency_matches_real_captured_log_across_all_runs() -> None:
    block_a, block_b, join, end, links_by_id, nodes_by_id = _make_scenario()
    rng_fail_a = RandomGen(stream=RaptorStream(seed=fetch_raptor_seed(1)))
    rng_repair_a = RandomGen(stream=RaptorStream(seed=fetch_raptor_seed(2)))
    rng_fail_b = RandomGen(stream=RaptorStream(seed=fetch_raptor_seed(3)))
    rng_repair_b = RandomGen(stream=RaptorStream(seed=fetch_raptor_seed(4)))
    rngs = (rng_fail_a, rng_repair_a, rng_fail_b, rng_repair_b)

    golden_runs = _golden_run_lines()

    total = {"match": 0, "fp32-noise": 0, "structural": 0}
    for run_index in range(_NUM_RUNS):
        logs = _run_once(block_a, block_b, join, end, links_by_id, nodes_by_id, rngs, run_index)

        golden = golden_runs[run_index]
        # Every run has the same two known-out-of-scope trailing lines as
        # TwoBlockRedundancy (mission/termination-summary reporting).
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
    # Sanity: the fixture's whole point is exercising the dependency
    # cascade heavily -- real capture has 704 "Dependence" occurrences
    # across all 5 runs (including the 2 out-of-scope trailing lines'
    # runs, negligible), so our own count should be in the same regime.
    dependence_count = sum(1 for run in golden_runs for l in run if "Dependence" in l)
    assert dependence_count > 600


def test_back_flow_dependency_trigger_dispatch() -> None:
    assert back_flow_dependency_trigger(-1, 1, 0, 0, 0) == "Down"  # Running -> Repairing
    assert back_flow_dependency_trigger(1, -1, 0, 0, 0) == "AllUp"  # Repairing -> Running
    assert back_flow_dependency_trigger(0, 0, 0, 0, 0) is None  # Initialize (no-op transition)

    # -> Standby: NodeStandby is a per-dependent no-op, since a Standby
    # block still counts for the node's k-out-of-n availability. The
    # dispatch still fires when dependents exist; `has_dep_objects=False`
    # (nobody depends on this block) never reaches BackFlowDependencies.
    assert back_flow_dependency_trigger(-1, 0, 0, 1, 0) == "NodeStandby"
    assert back_flow_dependency_trigger(-1, 0, 0, 1, 0, has_dep_objects=False) is None


def test_back_flow_dependencies_ignores_non_matching_and_ignore_dep_blocks() -> None:
    depended = RBDBlock(name="a", id=3, op_status=BlockStatus.RUNNING)
    unrelated = RBDBlock(name="b", id=4, op_status=BlockStatus.RUNNING, dependency_num=99, dep_type="RBDBlock")
    ignored = RBDBlock(
        name="c", id=5, op_status=BlockStatus.RUNNING,
        dependency_num=3, dep_type="RBDBlock", dep_idle_percent=100.0, ignore_dep=True,
    )
    calls = []
    back_flow_dependencies(
        "Down", depended, [depended, unrelated, ignored],
        lambda b, s, c, t: calls.append((b.id, s, c, t)), 12.5,
    )
    assert calls == []


def test_back_flow_dependencies_mixed_percentage_needs_a_draw_callback() -> None:
    """Non-100% reactions are drawn, so the caller must supply the draw. A
    caller that does not is built wrong (an invariant error, not a missing feature)."""
    depended = RBDBlock(name="a", id=3, op_status=BlockStatus.RUNNING)
    dependent = RBDBlock(
        name="b", id=4, op_status=BlockStatus.RUNNING,
        dependency_num=3, dep_type="RBDBlock", dep_idle_percent=50.0, dep_nothing_percent=50.0,
    )
    with pytest.raises(EngineInvariantError):
        back_flow_dependencies("Down", depended, [depended, dependent], lambda *a: None, 0.0)


def test_resend_back_flow_would_apply_false_when_dependent_already_idle() -> None:
    # The SpaceStation shape: a sysDepend block whose dependent has
    # already gone Idle from the original edge-triggered dispatch.
    # ResendBackFlow finds nothing Running/Standby to act on, so this is a
    # safe no-op (see that function's docstring for why "nothing down"
    # alone is not the right condition).
    end_node = RBDNode(id=1, status=NodeStatus.DOWN)
    dependent = RBDBlock(name="dep", id=2, op_status=BlockStatus.IDLE, dependency_num=-2)
    assert resend_back_flow_would_apply([dependent], {1: end_node}, {}, end_node.id) is False


def test_resend_back_flow_would_apply_true_when_dependent_still_running() -> None:
    # A sysDepend dependent that is still Running/Standby while the End
    # node is Down is exactly the case the original edge-triggered
    # dispatch would have caught. If it did not, ResendBackFlow is the
    # only thing left to fix it, so this must not be silently skipped.
    end_node = RBDNode(id=1, status=NodeStatus.DOWN)
    dependent = RBDBlock(name="dep", id=2, op_status=BlockStatus.RUNNING, dependency_num=-2)
    assert resend_back_flow_would_apply([dependent], {1: end_node}, {}, end_node.id) is True


def test_resend_back_flow_would_apply_true_for_block_to_block_dependency() -> None:
    depended = RBDBlock(name="a", id=3, op_status=BlockStatus.REPAIRING)
    dependent = RBDBlock(
        name="b", id=4, op_status=BlockStatus.RUNNING, dependency_num=3, dep_type="RBDBlock",
    )
    assert resend_back_flow_would_apply([depended, dependent], {}, {}, None) is True


def test_resend_back_flow_would_apply_false_with_no_dependents() -> None:
    depended = RBDBlock(name="a", id=3, op_status=BlockStatus.REPAIRING)
    assert resend_back_flow_would_apply([depended], {}, {}, None) is False


def test_resolve_dependency_node_id_all_three_kinds() -> None:
    sys_dep = RBDBlock(name="a", dependency_num=-2)
    loc_dep = RBDBlock(name="b", dependency_num=-1)
    node_dep = RBDBlock(name="c", dependency_num=22, dep_type="RBDNode")
    block_dep = RBDBlock(name="d", dependency_num=5, dep_type="RBDBlock")
    no_dep = RBDBlock(name="e")

    assert resolve_dependency_node_id(sys_dep, end_node_id=99, own_node_id=7) == 99
    assert resolve_dependency_node_id(loc_dep, end_node_id=99, own_node_id=7) == 7
    assert resolve_dependency_node_id(node_dep, end_node_id=99, own_node_id=7) == 22
    assert resolve_dependency_node_id(block_dep, end_node_id=99, own_node_id=7) is None
    assert resolve_dependency_node_id(no_dep, end_node_id=99, own_node_id=7) is None


def test_draw_dependency_action_deterministic_shortcuts() -> None:
    rng = RandomGen(stream=RaptorStream(seed=fetch_raptor_seed(1)))
    assert draw_dependency_action(RBDBlock(name="a", dep_idle_percent=100.0), rng, 5.0) == "GoIdle"
    assert draw_dependency_action(RBDBlock(name="b", dep_nothing_percent=100.0), rng, 5.0) == "DoNothing"
    assert draw_dependency_action(RBDBlock(name="c", dep_pm_percent=100.0), rng, 5.0) == "GoToPM"
    assert draw_dependency_action(RBDBlock(name="d", dep_fail_percent=100.0), rng, 5.0) == "InducedFailure"


def test_draw_dependency_action_tzero_persists_across_calls() -> None:
    block = RBDBlock(name="a", dep_nothing_percent=25.0, dep_idle_percent=25.0, dep_pm_percent=25.0, dep_fail_percent=25.0)
    rng = RandomGen(stream=RaptorStream(seed=fetch_raptor_seed(1)))
    first = draw_dependency_action(block, rng, sim_time=0.0)
    assert block.run_state.time_zero_action == first
    # A second draw at SimTime=0.0 reuses the cached action without
    # consuming another RNG draw: the stream's next value matches a fresh,
    # unconsumed stream's.
    fresh_rng = RandomGen(stream=RaptorStream(seed=fetch_raptor_seed(1)))
    fresh_rng.draw_number(Distribution.UNIFORM_REAL, [0.0, 1.0])
    expected_next = fresh_rng.draw_number(Distribution.UNIFORM_REAL, [0.0, 1.0])
    second = draw_dependency_action(block, rng, sim_time=0.0)
    assert second == first
    assert rng.draw_number(Distribution.UNIFORM_REAL, [0.0, 1.0]) == expected_next

    # At a later sim_time, a fresh draw happens every time (no caching).
    later_action = draw_dependency_action(block, rng, sim_time=10.0)
    assert later_action in ("DoNothing", "GoIdle", "GoToPM", "InducedFailure")


def test_fail_mean_gamma_weibull_pearson5() -> None:
    # Gamma: mean = shape*scale + location.
    gamma_block = RBDBlock(name="g", fail_distro=int(Distribution.GAMMA), fail_values=[2.0, 3.0, 1.0])
    assert fail_mean(gamma_block) == 2.0 * 3.0 + 1.0

    # Weibull shape=1 (exponential-equivalent case): mean = scale + location.
    weibull_block = RBDBlock(name="w", fail_distro=int(Distribution.WEIBULL), fail_values=[1.0, 5.0, 2.0])
    assert fail_mean(weibull_block) == 5.0 + 2.0

    # Pearson5: mean = scale/(shape-1) + location, shape>1.
    pearson5_block = RBDBlock(name="p", fail_distro=int(Distribution.PEARSON5), fail_values=[3.0, 4.0, 0.0])
    assert fail_mean(pearson5_block) == 4.0 / (3.0 - 1.0)

    # Pearson5 shape<=1.0: mean is undefined, so the source substitutes
    # the "Murphy identifier" sentinel 12345.6789 rather than raising or
    # skipping the stat (SetStats).
    assert fail_mean(
        RBDBlock(name="bad-pearson5", fail_distro=int(Distribution.PEARSON5), fail_values=[1.0, 4.0, 0.0])
    ) == 12345.6789

    # Pearson6: mean = (location*shape1)/(shape2-1), shape2>1.
    pearson6_block = RBDBlock(name="p6", fail_distro=int(Distribution.PEARSON6), fail_values=[2.0, 3.0, 4.0])
    assert fail_mean(pearson6_block) == (4.0 * 2.0) / (3.0 - 1.0)
    # Pearson6 shape2<=1.0: same Murphy sentinel.
    assert fail_mean(
        RBDBlock(name="bad-pearson6", fail_distro=int(Distribution.PEARSON6), fail_values=[2.0, 1.0, 4.0])
    ) == 12345.6789

    # Weibull shape<0.05: same Murphy sentinel.
    assert fail_mean(
        RBDBlock(name="bad-weibull", fail_distro=int(Distribution.WEIBULL), fail_values=[0.01, 5.0, 2.0])
    ) == 12345.6789

    # Normal is handled by the full SetStats port.
    assert fail_mean(RBDBlock(name="normal", fail_distro=int(Distribution.NORMAL), fail_values=[5.0, 2.0, 0.0])) == 5.0


def test_back_flow_dependencies_from_node_do_nothing_and_induced_failure() -> None:
    node = RBDNode(id=1, status=NodeStatus.DOWN)
    do_nothing_block = RBDBlock(
        name="dn", id=2, op_status=BlockStatus.RUNNING, dependency_num=-2,
        dep_nothing_percent=100.0,
    )
    induced_block = RBDBlock(
        name="ind", id=3, op_status=BlockStatus.RUNNING, dependency_num=-2,
        dep_fail_percent=100.0,
    )
    transitions = []
    induced = []
    back_flow_dependencies_from_node(
        "Down", node,
        BackFlowDeps(
            end_node_id=1, all_blocks=[do_nothing_block, induced_block],
            links_by_id={},
            transition=lambda b, s, c, t: transitions.append((b.id, s, c, t)),
            induce=lambda b, r: induced.append((b.id, r)),
            # Both blocks hit a deterministic 100%-shortcut in
            # draw_dependency_action, so the RNG is never actually consulted
            # here -- None stands in for it.
            draw_action=lambda b, t: draw_dependency_action(b, None, t),
        ),
        3.0,
    )
    assert transitions == []  # DoNothing never calls transition
    assert induced == [(3, "Induced_Failure")]
    assert do_nothing_block.run_state.previous_dependency_state == "DoNothing"
    assert induced_block.run_state.previous_dependency_state == "InducedFailure"


def test_special_back_flow_replays_prev_dep_state_go_idle() -> None:
    # Port of BackFlowDependencies's `IF specialBackFlow` branch
    #: a block whose last ordinary dispatch
    # already decided "GoIdle" is replayed straight to Idle, with no fresh
    # draw and no dispatch of any other action.
    node = RBDNode(id=1, status=NodeStatus.DOWN)
    block = RBDBlock(
        name="dep", id=2, op_status=BlockStatus.RUNNING, dependency_num=-2,
        run_state=BlockRunState(previous_dependency_state="GoIdle"),
    )
    transitions = []
    back_flow_dependencies_from_node(
        "Down", node,
        BackFlowDeps(
            end_node_id=1, all_blocks=[block], links_by_id={},
            transition=lambda b, s, c, t: transitions.append((b.id, s, c, t)),
            special_back_flow=True,
        ),
        5.0,
    )
    assert transitions == [(2, BlockStatus.IDLE, "Dependence", 5.0)]


def test_special_back_flow_empty_prev_dep_state_uses_def_dep_state_idle() -> None:
    node = RBDNode(id=1, status=NodeStatus.DOWN)
    idles = RBDBlock(
        name="idles", id=2, op_status=BlockStatus.RUNNING, dependency_num=-2,
        run_state=BlockRunState(previous_dependency_state=""), def_dep_state_idle=True,
    )
    stays = RBDBlock(
        name="stays", id=3, op_status=BlockStatus.RUNNING, dependency_num=-2,
        run_state=BlockRunState(previous_dependency_state=""), def_dep_state_idle=False,
    )
    transitions = []
    back_flow_dependencies_from_node(
        "Down", node,
        BackFlowDeps(
            end_node_id=1, all_blocks=[idles, stays], links_by_id={},
            transition=lambda b, s, c, t: transitions.append((b.id, s, c, t)),
            special_back_flow=True,
        ),
        5.0,
    )
    assert transitions == [(2, BlockStatus.IDLE, "Dependence", 5.0)]


def test_special_back_flow_skips_return_from_maint_and_other_actions() -> None:
    node = RBDNode(id=1, status=NodeStatus.DOWN)
    mid_maint = RBDBlock(
        name="mid", id=2, op_status=BlockStatus.RUNNING, dependency_num=-2,
        run_state=BlockRunState(previous_dependency_state="GoIdle", returning_from_maintenance=True),
    )
    already_applied = RBDBlock(
        name="applied", id=3, op_status=BlockStatus.RUNNING, dependency_num=-2,
        run_state=BlockRunState(previous_dependency_state="DoNothing"),
    )
    transitions = []
    back_flow_dependencies_from_node(
        "Down", node,
        BackFlowDeps(
            end_node_id=1, all_blocks=[mid_maint, already_applied],
            links_by_id={},
            transition=lambda b, s, c, t: transitions.append((b.id, s, c, t)),
            special_back_flow=True,
        ),
        5.0,
    )
    assert transitions == []


def test_return_from_maint_branch_uses_dep_idle_perc_or_def_dep_state_idle() -> None:
    # Port of BackFlowDependencies's trailing ELSE,
    # reached for the ordinary (non-resend) dispatch when the block is
    # still mid-repair-completion. Empty comment, clears returning_from_maintenance.
    node = RBDNode(id=1, status=NodeStatus.DOWN)
    goes_idle = RBDBlock(
        name="idle", id=2, op_status=BlockStatus.RUNNING, dependency_num=-2,
        run_state=BlockRunState(returning_from_maintenance=True), dep_idle_percent=100.0,
    )
    stays_running = RBDBlock(
        name="run", id=3, op_status=BlockStatus.RUNNING, dependency_num=-2,
        run_state=BlockRunState(returning_from_maintenance=True), dep_idle_percent=0.0, def_dep_state_idle=False,
    )
    transitions = []
    back_flow_dependencies_from_node(
        "Down", node,
        BackFlowDeps(
            end_node_id=1, all_blocks=[goes_idle, stays_running],
            links_by_id={},
            transition=lambda b, s, c, t: transitions.append((b.id, s, c, t)),
        ),
        5.0,
    )
    assert transitions == [(2, BlockStatus.IDLE, "", 5.0), (3, BlockStatus.RUNNING, "", 5.0)]
    assert goes_idle.run_state.returning_from_maintenance is False
    assert stays_running.run_state.returning_from_maintenance is False


def test_induced_failure_sets_return_from_maint_only_when_delivered() -> None:
    # `induce` reports whether `Scheduler.interrupt` was actually called,
    # and `returning_from_maintenance` tracks that rather than firing
    # unconditionally: a block reached while not fail_wait/zero_wait (e.g.
    # a kickoff-time dependency dispatch racing a block whose own
    # `block_run_process` has not started) never receives the interrupt,
    # and marking it `returning_from_maintenance` would poison a later re-check.
    node = RBDNode(id=1, status=NodeStatus.DOWN)
    delivered_block = RBDBlock(name="ind", id=2, op_status=BlockStatus.RUNNING, dependency_num=-2, dep_fail_percent=100.0)
    induced = []
    back_flow_dependencies_from_node(
        "Down", node,
        BackFlowDeps(
            end_node_id=1, all_blocks=[delivered_block], links_by_id={},
            transition=lambda *a: None,
            induce=lambda b, r: induced.append((b.id, r)) or True,
            draw_action=lambda b, t: draw_dependency_action(b, None, t),
        ),
        0.0,
    )
    assert induced == [(2, "Induced_Failure")]
    assert delivered_block.run_state.returning_from_maintenance is True

    undelivered_block = RBDBlock(name="ind2", id=3, op_status=BlockStatus.RUNNING, dependency_num=-2, dep_fail_percent=100.0)
    back_flow_dependencies_from_node(
        "Down", node,
        BackFlowDeps(
            end_node_id=1, all_blocks=[undelivered_block], links_by_id={},
            transition=lambda *a: None, induce=lambda b, r: False,
            draw_action=lambda b, t: draw_dependency_action(b, None, t),
        ),
        0.0,
    )
    assert undelivered_block.run_state.returning_from_maintenance is False


def test_resend_back_flow_dispatches_down_nodes_with_special_back_flow() -> None:
    down_node = RBDNode(id=1, status=NodeStatus.DOWN, node_name="end")
    up_node = RBDNode(id=99, status=NodeStatus.ALL_UP, node_name="other")
    dependent = RBDBlock(
        name="dep", id=2, op_status=BlockStatus.RUNNING, dependency_num=-2, run_state=BlockRunState(previous_dependency_state="GoIdle"),
    )
    transitions = []
    resend_back_flow(
        [dependent], {1: down_node, 99: up_node}, {}, end_node_id=1,
        transition=lambda b, s, c, t: transitions.append((b.id, s, c, t)),
        sim_time=7.0,
    )
    assert transitions == [(2, BlockStatus.IDLE, "Dependence", 7.0)]


def test_resend_back_flow_block_to_block_special_back_flow() -> None:
    # Port of Raptor 7 blockHasDepsGroup FOREACH: a Cut or
    # mid-maintenance block with RBDBlock dependents re-fires its Down
    # dispatch with specialBackFlow semantics -- re-Idle
    # only members whose prevDepState records "GoIdle" (or clean record
    # + defDepStateIdle); others are skipped.
    depended = RBDBlock(name="a", id=3, op_status=BlockStatus.REPAIRING)
    go_idle_dep = RBDBlock(
        name="b", id=4, op_status=BlockStatus.RUNNING, dependency_num=3,
        dep_type="RBDBlock", run_state=BlockRunState(previous_dependency_state="GoIdle"),
    )
    do_nothing_dep = RBDBlock(
        name="c", id=5, op_status=BlockStatus.RUNNING, dependency_num=3,
        dep_type="RBDBlock", run_state=BlockRunState(previous_dependency_state="DoNothing"),
    )
    transitions = []
    resend_back_flow(
        [depended, go_idle_dep, do_nothing_dep], {}, {}, None,
        lambda blk, st, cm, t, **k: transitions.append((blk.name, st)), 0.0,
    )
    assert transitions == [("b", BlockStatus.IDLE)]  # DoNothing member skipped


def test_repair_completion_higher_cold_node_forces_standby() -> None:
    """The source checks `depNode.higherCold` first in the "block is
    active and depends on a node" branch and
    puts the completing block into Standby regardless of the node's own
    status, so a higherCold-but-ALL_UP node returns Standby."""
    from raptor.core.engine.dependency import repair_completion_status
    from raptor.core.model import RBDBlock, RBDNode, NodeStatus, BlockStatus

    block = RBDBlock(name="B", dependency_num=-1, dep_type="RBDNode")
    node = RBDNode(id=1, node_name="end", status=NodeStatus.ALL_UP, higher_cold=True)
    status, recheck = repair_completion_status(block, [], dep_node=node, own_node=node)
    assert status == BlockStatus.STANDBY
    assert recheck is False


def test_distro_mean_codes_16_22_match_source() -> None:
    """Codes 16/17/18/20/21/22 ported from `SetStats`."""
    from raptor.core.engine.dependency import distro_mean
    from raptor.core.rng import Distribution as D

    # 16 Empirical: arithmetic mean of the values.
    assert distro_mean(int(D.EMPIRICAL), [2.0, 4.0, 6.0]) == 4.0
    # 17 "Point Estimate" / 18 None: 12345.6788 Murphy no-mean sentinel.
    assert distro_mean(int(D.BERNOULLI), [0.7, 0.0, 0.0]) == 12345.6788
    assert distro_mean(int(D.NONE), [0.0, 0.0, 0.0]) == 12345.6788
    # 20 Exponential Power: mean = location = p[2].
    assert distro_mean(int(D.EXPONENTIAL_POWER), [2.0, 3.0, 7.0]) == 7.0
    # 21 Extreme Value: location + 0.57721*scale (p[1] + 0.57721*p[0]).
    assert distro_mean(int(D.EXTREME_VALUE), [2.0, 5.0, 0.0]) == pytest.approx(5.0 + 0.57721 * 2.0)
    # 22 Laplace: mean = location = p[1].
    assert distro_mean(int(D.LAPLACE), [3.0, 8.0, 0.0]) == 8.0


# ---------------------------------------------------------------------------
# ER3-08: a block depending on an RBDEvent (dep_type="RBDEvent")
# ---------------------------------------------------------------------------
# NOT yet checked against a real capture (no fixture exercises this
# combination) -- unit-level only, mirroring the already-verified RBDBlock
# branches' shape exactly (source: Raptor 7 FOREACH has no type
# guard on the DEPENDED-upon side; for repair_completion_status).
# ER3-08 (the ER3 label key is the appendix of the internal engine ledger).

def test_back_flow_dependencies_dispatches_rbdevent_dependent_block() -> None:
    """A block with `dep_type="RBDEvent"` reacts to the EVENT it depends on
    exactly like a `dep_type="RBDBlock"` dependent reacts to a block -- this
    used to be silently skipped (`dep_block.dep_type != "RBDBlock"`)."""
    depended_event = RBDEvent(name="e", id=7, op_status=EventStatus.ARMED)
    dependent = RBDBlock(
        name="b", id=4, op_status=BlockStatus.RUNNING,
        dependency_num=7, dep_type="RBDEvent", dep_idle_percent=100.0,
    )
    calls = []
    back_flow_dependencies(
        "Down", depended_event, [dependent],
        lambda b, s, c, t: calls.append((b.id, s, c, t)), 12.5,
    )
    assert calls == [(4, BlockStatus.IDLE, "Dependence", 12.5)]


def test_back_flow_dependencies_ignores_rbdblock_dependent_on_unrelated_event() -> None:
    """An ordinary `dep_type="RBDBlock"` dependent is untouched when an
    EVENT (not its depended block) changes -- ids from different element
    kinds are not implicitly comparable."""
    depended_event = RBDEvent(name="e", id=7, op_status=EventStatus.ARMED)
    dependent = RBDBlock(
        name="b", id=4, op_status=BlockStatus.RUNNING,
        dependency_num=7, dep_type="RBDBlock", dep_idle_percent=100.0,
    )
    calls = []
    back_flow_dependencies(
        "Down", depended_event, [dependent],
        lambda b, s, c, t: calls.append((b.id, s, c, t)), 12.5,
    )
    assert calls == []


def test_repair_completion_status_rbdevent_branch() -> None:
    """Mirrors `test_repair_completion_higher_cold_node_forces_standby`'s
    RBDNode-branch pattern, for the new RBDEvent branch's four cases
   : Linked -> Running; Cut + DepIdle=100 -> Idle;
    Armed/Success -> Running; Failure -> Idle (DepIdle=100)."""
    block = RBDBlock(name="B", dependency_num=9, dep_type="RBDEvent", dep_idle_percent=100.0)

    linked = RBDEvent(id=9, active_status=ActiveStatus.LINKED)
    assert repair_completion_status(block, [], all_events=[linked]) == (BlockStatus.RUNNING, False)

    cut = RBDEvent(id=9, active_status=ActiveStatus.CUT)
    assert repair_completion_status(block, [], all_events=[cut]) == (BlockStatus.IDLE, False)

    armed = RBDEvent(id=9, active_status=ActiveStatus.ACTIVE, op_status=EventStatus.ARMED)
    assert repair_completion_status(block, [], all_events=[armed]) == (BlockStatus.RUNNING, False)

    success = RBDEvent(id=9, active_status=ActiveStatus.ACTIVE, op_status=EventStatus.SUCCESS)
    assert repair_completion_status(block, [], all_events=[success]) == (BlockStatus.RUNNING, False)

    failed = RBDEvent(id=9, active_status=ActiveStatus.ACTIVE, op_status=EventStatus.FAILURE)
    assert repair_completion_status(block, [], all_events=[failed]) == (BlockStatus.IDLE, False)


def test_repair_completion_status_rbdevent_branch_def_dep_state_idle() -> None:
    """Cut + DepIdle!=100 falls to `defDepStateIdle` (source ),
    same as the RBDBlock branch's identical sub-case."""
    failed = RBDEvent(id=9, active_status=ActiveStatus.ACTIVE, op_status=EventStatus.FAILURE)

    block_idle = RBDBlock(
        name="B", dependency_num=9, dep_type="RBDEvent", dep_idle_percent=50.0, def_dep_state_idle=True,
    )
    assert repair_completion_status(block_idle, [], all_events=[failed]) == (BlockStatus.IDLE, False)

    block_running = RBDBlock(
        name="B", dependency_num=9, dep_type="RBDEvent", dep_idle_percent=50.0, def_dep_state_idle=False,
    )
    assert repair_completion_status(block_running, [], all_events=[failed]) == (BlockStatus.RUNNING, False)


def test_orchestrator_raises_for_unported_dep_type() -> None:
    """The setup-time guard still raises for a genuinely unported dep_type
    (e.g. a made-up one) -- only RBDNode/RBDBlock/RBDHier/RBDEvent are
    allowed through, matching ER3-08's own scope note."""
    from raptor.api.build import build_from_spec
    from raptor.core.engine.runner import run_simulation

    spec = {
        "name": "BadDepType",
        "blocks": [
            {"id": 1, "name": "A", "fail_dist": 19, "fail_params": [100, 0, 0],
             "repair_dist": 19, "repair_params": [10, 0, 0]},
        ],
        "nodes": [
            {"id": 1, "name": "Start", "type": 1}, {"id": 2, "name": "End", "type": 3},
        ],
        "links": [
            {"id": 1, "from": ["Node", 1], "to": ["Block", 1]},
            {"id": 2, "from": ["Block", 1], "to": ["Node", 2]},
        ],
        "system": {"term_condition": 10.0, "num_runs": 1},
    }
    rbd = build_from_spec(spec)
    # Force a bogus dep_type directly onto the row -- build_from_spec's own
    # update_element path validates dep_type, so this bypasses it on purpose
    # to exercise the orchestrator's own defense-in-depth guard. rows[0] is
    # the mandatory ID=0 sentinel row (load_model skips it) -- must select
    # the real block's row by ID instead.
    row = next(r for r in rbd.section("BLOCK_DATA").rows if r.get("ID") == "1")
    row["DepType"] = "RBDBogus"
    row["DepNum"] = "1"
    with pytest.raises(NotImplementedError, match="RBDBogus"):
        run_simulation(rbd, num_runs=1)


@pytest.mark.parametrize("active", [ActiveStatus.CUT, ActiveStatus.LINKED])
@pytest.mark.parametrize("node_status, perc, def_idle, expected", [
    (NodeStatus.ALL_UP, 100.0, True, BlockStatus.RUNNING),
    (NodeStatus.DEGRADED, 100.0, True, BlockStatus.RUNNING),
    (NodeStatus.STANDBY, 100.0, True, BlockStatus.STANDBY),
    (NodeStatus.DOWN, 100.0, False, BlockStatus.IDLE),
    (NodeStatus.DOWN, 50.0, True, BlockStatus.IDLE),
    (NodeStatus.DOWN, 50.0, False, BlockStatus.RUNNING),
])
def test_a_cut_or_linked_block_finishing_repair_follows_its_dependency_node(
        active, node_status, perc, def_idle, expected) -> None:
    """Raptor 7 (ER3-09): no `GetDownNum` special case, never a recheck."""
    block = RBDBlock(id=1, dep_type="RBDNode", dependency_num=5, active_status=active,
                     dep_idle_percent=perc, def_dep_state_idle=def_idle)
    node = RBDNode(id=5, status=node_status)
    assert repair_completion_status(block, [block], node) == (expected, False)


def test_a_cut_block_whose_node_is_higher_cold_goes_to_standby() -> None:
    block = RBDBlock(id=1, dep_type="RBDNode", dependency_num=5, active_status=ActiveStatus.CUT)
    node = RBDNode(id=5, status=NodeStatus.DOWN, higher_cold=True)
    assert repair_completion_status(block, [block], node) == (BlockStatus.STANDBY, False)


def test_an_active_block_repairing_into_a_down_node_goes_idle_on_a_100_percent_idle_reaction() -> None:
    """Raptor 7: with more than one path down, `DepIdlePerc=100.0` alone gives Idle,
    whatever `defDepStateIdle` says (the Cut arm reads them the same way)."""
    own = RBDNode(id=6, in_paths_array=[1, 2], path_fail_value=[2, 0], path_pm_value=[0, 0])
    block = RBDBlock(id=1, dep_type="RBDNode", dependency_num=5, downstream_link_id=1, dep_idle_percent=100.0,
                     def_dep_state_idle=False)
    node = RBDNode(id=5, status=NodeStatus.DOWN)
    assert repair_completion_status(block, [block], node, own) == (BlockStatus.IDLE, False)
    block.dep_idle_percent = 50.0
    assert repair_completion_status(block, [block], node, own) == (BlockStatus.RUNNING, False)
