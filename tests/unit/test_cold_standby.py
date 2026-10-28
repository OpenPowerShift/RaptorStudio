"""Direct unit tests for `cold_standby.py`'s `RunColdLogic` port.

Each test builds a small scenario and asserts the source-cited behaviour
directly rather than comparing a full golden fixture. Link priorities and
block names mirror `tests/golden/HierarchyExample`.
"""
from __future__ import annotations

import pytest

from raptor.core.engine.cold_standby import perform_switch_process, run_cold_logic, sort_cold_links
from raptor.core.model import BlockStatus, Link, LinkStatus, NodeStatus, RBDBlock, RBDNode
from raptor.core.engine.cold_standby import set_switch_time
from raptor.core.engine.sim_details import NamedRandomGen, SimDetailsRecorder
from raptor.core.rng import Distribution, RandomGen, RaptorStream


def _node(**kwargs) -> RBDNode:
    defaults = dict(id=1, node_name="Comm", good_paths=1, k_star=1)
    defaults.update(kwargs)
    return RBDNode(**defaults)


def _link(**kwargs) -> Link:
    defaults = dict(connect_f_ref="RBDBlock", switch_status="ready", status=LinkStatus.UP)
    defaults.update(kwargs)
    return Link(**defaults)


def test_sort_cold_links_orders_by_cold_priority() -> None:
    node = _node(in_paths_array=[10, 20, 30])
    links_by_id = {
        10: _link(cold_priority=3),
        20: _link(cold_priority=1),
        30: _link(cold_priority=2),
    }
    sort_cold_links(node, links_by_id)
    assert node.cold_links == [20, 30, 10]


def test_kickoff_switches_excess_running_block_to_standby_no_interrupt() -> None:
    # HierarchyExample's Comm node shape: k=k*=1, two RBDBlock-sourced
    # links both starting Up/"ready". Link 15 (HighGain) stays on, link 8
    # (LowGain) loses its slot. At SimTime==0.0 no interrupt fires.
    high = RBDBlock(name="HighGain", id=7, op_status=BlockStatus.RUNNING)
    low = RBDBlock(name="LowGain", id=6, op_status=BlockStatus.RUNNING)
    # cold_priority values from HierarchyExample's input.rbd: link 15
    # (HighGain) = 25, link 8 (LowGain) = 50. A lower number means higher
    # priority, so link 15 sorts first and stays on.
    node = _node(in_paths_array=[15, 8], cold_links=[15, 8])
    links_by_id = {
        15: _link(cold_priority=25, connect_from_id=7),
        8: _link(cold_priority=50, connect_from_id=6),
    }
    block_to_link = {7: 15, 6: 8}
    transitions = []
    interrupts = []

    def transition(block, new_status, comment, t):
        block.op_status = new_status
        # A real caller's `transition` (orchestrator._Cascade.make_
        # transition) drives the block's link status too, via the same
        # cascade. Mimic that side effect here so the "Resurvey inbound
        # links" section sees a consistent Standby link.
        links_by_id[block_to_link[block.id]].status = (
            LinkStatus.STANDBY if new_status == BlockStatus.STANDBY else LinkStatus.UP
        )
        transitions.append((block.name, new_status, comment, t))

    logs, new_status, _stressed = run_cold_logic(
        node, links_by_id, {7: high, 6: low}, transition, 0.0,
        events_file=True, interrupt=lambda b, r: interrupts.append((b.name, r)),
    )
    assert transitions == [("LowGain", BlockStatus.STANDBY, "Too_many_on", 0.0)]
    assert interrupts == []  # SimTime==0.0 -- no interrupt, matches real capture
    assert new_status == NodeStatus.ALL_UP
    assert logs == ["0.000000          \tNode     \tComm                  \tGood             \t1/2_up15_s8"]


def test_midrun_switch_off_interrupts_the_block() -> None:
    # Same shape, but SimTime>0.0, so the source's `IF SimTime>0.0` guard
    # fires and the demoted block's suspended Run
    # process is woken with "Switched_Off".
    high = RBDBlock(name="HighGain", id=7, op_status=BlockStatus.RUNNING)
    low = RBDBlock(name="LowGain", id=6, op_status=BlockStatus.RUNNING)
    node = _node(in_paths_array=[15, 8], cold_links=[15, 8])
    links_by_id = {
        15: _link(cold_priority=25, connect_from_id=7),
        8: _link(cold_priority=50, connect_from_id=6),
    }
    interrupts = []

    def transition(block, new_status, comment, t):
        block.op_status = new_status

    run_cold_logic(
        node, links_by_id, {7: high, 6: low}, transition, 100.0,
        interrupt=lambda b, r: interrupts.append((b.name, r)),
    )
    assert interrupts == [("LowGain", "Switched_Off")]


def test_switch_on_promotes_standby_block_and_always_interrupts() -> None:
    # HighGain's link has gone Down (e.g. HighGain just failed); LowGain's
    # Standby link is next in priority order and has room, so it is
    # promoted back to Running. The interrupt is unconditional on the
    # switch-ON side, even at SimTime==0.0, unlike
    # the switch-OFF side's SimTime>0.0 guard.
    high = RBDBlock(name="HighGain", id=7, op_status=BlockStatus.REPAIRING)
    low = RBDBlock(name="LowGain", id=6, op_status=BlockStatus.STANDBY)
    node = _node(in_paths_array=[15, 8], cold_links=[15, 8])
    links_by_id = {
        15: _link(cold_priority=25, connect_from_id=7, status=LinkStatus.DOWN),
        8: _link(cold_priority=50, connect_from_id=6, status=LinkStatus.STANDBY),
    }
    transitions = []
    interrupts = []

    def transition(block, new_status, comment, t):
        block.op_status = new_status
        transitions.append((block.name, new_status, comment))

    run_cold_logic(
        node, links_by_id, {7: high, 6: low}, transition, 0.0,
        interrupt=lambda b, r: interrupts.append((b.name, r)),
    )
    assert transitions == [("LowGain", BlockStatus.RUNNING, "Prior=Standby")]
    assert interrupts == [("LowGain", "Switched_On")]


def test_higher_cold_suppression_does_not_clear_the_flag() -> None:
    # RunColdLogic's suppressed arm is `IF NOT higherCold ... ELSE
    # ReconfigureNode(0,0,0,0,0,0); END IF`:
    # it does no switching and returns None so the caller
    # performs that all-zero-delta aggregation, and it does not clear the
    # flag. Ownership belongs to whoever set it (the BackFlowDependencies
    # NodeDepGroup loops, or an outer cold node's survey).
    node = _node(higher_cold=True)
    assert run_cold_logic(node, {}, {}, lambda *a: None, 0.0) is None
    assert node.higher_cold is True  # persists until its owner clears it
    # ...and stays suppressed on every subsequent call, not just the first
    assert run_cold_logic(node, {}, {}, lambda *a: None, 1.0) is None
    assert node.higher_cold is True


def test_node_dep_group_toggles_higher_cold_on_a_cold_dependent() -> None:
    # Port of BackFlowDependencies' NodeDepGroup loops (Raptor 7:
    # 1424-1431 Down / 1452-1462 AllUp). Comm (cold, dep_type="RBDNode",
    # dependency_num -> Power) goes higherCold when Power goes Down and
    # comes back when it recovers, with RunColdLogic driven on the
    # recovery.
    from raptor.core.engine.dependency import BackFlowDeps, back_flow_dependencies_from_node

    power = RBDNode(node_name="Power", id=19)
    comm = _node(cold_standby=True, dependency_num=19, dep_type="RBDNode")
    comm.id = 18
    nodes = {19: power, 18: comm}

    back_flow_dependencies_from_node(
        "Down", power,
        BackFlowDeps(end_node_id=-1, all_blocks=[], links_by_id={},
                     transition=lambda *a: None, nodes_by_id=nodes),
        0.0,
    )
    assert comm.higher_cold is True

    ran: list[int] = []
    back_flow_dependencies_from_node(
        "AllUp", power,
        BackFlowDeps(end_node_id=-1, all_blocks=[], links_by_id={},
                     transition=lambda *a: None, nodes_by_id=nodes,
                     apply_cold_logic=lambda n, t: ran.append(n.id)),
        1.0,
    )
    assert comm.higher_cold is False
    assert ran == [18]  # the AllUp branch's own `ASK tempNode TO RunColdLogic`


def test_node_dep_group_skips_a_non_dependent_node() -> None:
    # The membership gate: a cold node with no dependency on the source is
    # left alone entirely.
    from raptor.core.engine.dependency import BackFlowDeps, back_flow_dependencies_from_node

    power = RBDNode(node_name="Power", id=19)
    comm = _node(cold_standby=True, dependency_num=0)
    comm.id = 18
    back_flow_dependencies_from_node(
        "Down", power,
        BackFlowDeps(end_node_id=-1, all_blocks=[], links_by_id={},
                     transition=lambda *a: None,
                     nodes_by_id={19: power, 18: comm}),
        0.0,
    )
    assert comm.higher_cold is False


def test_not_enough_ready_paths_with_no_switchable_candidates_goes_down() -> None:
    # Both links Down: readyPaths=0 < runsRequired, and there is nothing
    # auto/manual/none-switchable to initiate a switch on, so the node goes
    # Bad.
    high = RBDBlock(name="HighGain", id=7, op_status=BlockStatus.RUNNING)
    low = RBDBlock(name="LowGain", id=6, op_status=BlockStatus.RUNNING)
    node = _node(in_paths_array=[15, 8], cold_links=[8, 15])
    links_by_id = {
        15: _link(connect_from_id=7, status=LinkStatus.DOWN),
        8: _link(connect_from_id=6, status=LinkStatus.DOWN),
    }
    _logs, new_status, _stressed = run_cold_logic(node, links_by_id, {7: high, 6: low}, lambda *a: None, 0.0, events_file=True)
    assert new_status == NodeStatus.DOWN


def test_not_enough_ready_paths_initiates_auto_switch() -> None:
    # HighGain's link goes Down; LowGain's link is Standby with
    # switch_status="auto" (already resolved by an earlier SetSwitchTime
    # draw) -- readyPaths=0 < kMax=1 and there is an auto-switchable
    # candidate, so this preps it and calls spawn_switch. The
    # HierarchyExample capture shows Comm going Bad then recovering 0.5
    # time units later.
    high = RBDBlock(name="HighGain", id=7, op_status=BlockStatus.RUNNING)
    low = RBDBlock(name="LowGain", id=6, op_status=BlockStatus.RUNNING)
    node = _node(in_paths_array=[15, 8], cold_links=[15, 8], check_autos_first=True)
    links_by_id = {
        15: _link(cold_priority=25, connect_from_id=7, status=LinkStatus.DOWN),
        8: _link(cold_priority=50, connect_from_id=6, status=LinkStatus.STANDBY, switch_status="auto"),
    }
    spawned = []
    logs, new_status, _stressed = run_cold_logic(
        node, links_by_id, {7: high, 6: low}, lambda *a: None, 10.7,
        events_file=True, spawn_switch=lambda link: spawned.append(link),
    )
    assert spawned == [links_by_id[8]]
    assert links_by_id[8].switch_status == "autoInProgress"
    assert low.ignore_dep is True
    assert new_status == NodeStatus.DOWN
    assert logs == ["10.700000         \tNode     \tComm                  \tBad              \t0/2_df15_dsw8"]


def test_perform_switch_process_completes_after_switch_time() -> None:
    link = _link(switch_time=0.5, switch_status="autoInProgress")
    block = RBDBlock(name="LowGain", id=6, ignore_dep=True)
    ran = []
    gen = perform_switch_process(link, block, lambda: ran.append(True), RandomGen(1))
    assert next(gen) == 0.5
    with pytest.raises(StopIteration):
        gen.send(None)
    assert link.switch_status == "ready"
    assert link.switch_completed is True
    assert block.ignore_dep is False
    assert ran == [True]


def test_switch_off_is_unconditional_on_switch_status() -> None:
    # The source's switch-off branch does not
    # check switch_status at all: a link that is Up and not the one being
    # kept switches off regardless of whether its switch_status is
    # "ready", "auto", "manual", or already in progress.
    high = RBDBlock(name="HighGain", id=7, op_status=BlockStatus.RUNNING)
    low = RBDBlock(name="LowGain", id=6, op_status=BlockStatus.RUNNING)
    node = _node(in_paths_array=[15, 8], cold_links=[15, 8])
    links_by_id = {
        15: _link(cold_priority=25, connect_from_id=7, switch_status="ready"),
        8: _link(cold_priority=50, connect_from_id=6, switch_status="autoInProgress"),
    }
    transitions = []
    run_cold_logic(
        node, links_by_id, {7: high, 6: low}, lambda b, s, c, t: transitions.append((b.name, s, c)), 0.0,
    )
    assert transitions == [("LowGain", BlockStatus.STANDBY, "Too_many_on")]


def test_set_switch_time_names_its_own_sd_draw_after_the_link() -> None:
    # The `/sd` record names this draw by the link (`gettingDraw` is an
    # argument of the `DrawNumber` call, so it identifies the call site,
    # not the stream); HierarchyExample's capture writes
    # `0.000000  link 8  0.025303`. SysStream[11] is shared with
    # ControlPhaseChanges' phase-duration draws, which tag themselves by
    # phase name, so the switch draw must set its own name rather than
    # inherit the last phase's.
    recorder = SimDetailsRecorder()
    shared = NamedRandomGen(
        RandomGen(RaptorStream(1)), recorder, lambda: "D061-120_Orbit", lambda: 0.0,
    )
    link = _link(object_num=8, auto_switch_probability=0.98, auto_switch_time=2.0)
    set_switch_time(link, shared)
    # The phase tag is not consumed by the switch draw...
    assert [ln.split("  ")[1] for ln in recorder.lines] == ["link 8"]
    # ...and the one-shot does not survive into the next draw, which falls
    # back to the stream's own live lookup.
    shared.draw_number(Distribution.UNIFORM_REAL, [0.0, 1.0])
    assert [ln.split("  ")[1] for ln in recorder.lines] == ["link 8", "D061-120_Orbit"]


def test_set_switch_time_tags_nothing_when_no_draw_is_taken() -> None:
    # AProb of exactly 1.0/0.0 short-circuits without a draw (source's
    # own `IF AutoSwitchProb=1.0 ... ELSIF =0.0` pair), so nothing is
    # recorded and no stale one-shot tag is left behind for the next
    # caller of the shared stream.
    recorder = SimDetailsRecorder()
    shared = NamedRandomGen(RandomGen(RaptorStream(1)), recorder, "SysStream11", lambda: 0.0)
    set_switch_time(_link(object_num=8, auto_switch_probability=1.0, auto_switch_time=2.0), shared)
    assert recorder.lines == []
    assert shared.next_name is None


def test_equal_cold_priorities_are_reordered_by_the_sources_selection_sort() -> None:
    """`IdAndSortColds` swaps position i with the first strictly-lower priority after it, so it
    is not stable: priorities [50, 50, 25] order the links [3, 2, 1] (a stable sort: [3, 1, 2])."""
    node = _node(in_paths_array=[1, 2, 3])
    links_by_id = {1: _link(cold_priority=50), 2: _link(cold_priority=50), 3: _link(cold_priority=25)}
    sort_cold_links(node, links_by_id)
    assert node.cold_links == [3, 2, 1]


@pytest.mark.parametrize("priorities, expected", [
    ([1, 2, 3], [1, 2, 3]),
    ([3, 2, 1], [3, 2, 1]),
    ([50, 50, 50], [1, 2, 3]),
    ([2, 1, 1], [2, 3, 1]),      # the swaps move link 1 to the back
    ([1, 1, 2, 1], [1, 2, 4, 3]),
], ids=str)
def test_selection_sort_examples(priorities, expected) -> None:
    ids = list(range(1, len(priorities) + 1))
    node = _node(in_paths_array=ids)
    sort_cold_links(node, {i: _link(cold_priority=p) for i, p in zip(ids, priorities)})
    assert node.cold_links == expected


def _three_running_blocks_into_a_cold_node(**node_fields):
    blocks = {i: RBDBlock(name=f"B{i}", id=i, op_status=BlockStatus.RUNNING) for i in (1, 2, 3)}
    node = _node(in_paths_array=[11, 12, 13], cold_links=[11, 12, 13], **node_fields)
    links = {10 + i: _link(cold_priority=i, connect_from_id=i) for i in (1, 2, 3)}
    demoted = []

    def transition(block, new_status, comment, t):
        block.op_status = new_status
        links[10 + block.id].status = LinkStatus.STANDBY if new_status == BlockStatus.STANDBY else LinkStatus.UP
        demoted.append(block.name)

    run_cold_logic(node, links, blocks, transition, 0.0, events_file=True)
    return demoted


def test_a_phased_node_never_runs_fewer_than_k_paths_even_if_k_star_is_lower() -> None:
    """Raptor 7: with phasing and k* < k, kMax is k. k=2, k*=1, three ready paths:
    one is switched off, not two."""
    assert _three_running_blocks_into_a_cold_node(good_paths=2, k_star=1, uses_phasing=True) == ["B3"]
