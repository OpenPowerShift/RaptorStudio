"""Unit tests for `engine/capacity.py`, using hand-computed topologies.

See `tests/unit/test_capacity_e2e.py` for the end-to-end comparison
against the captured engine output for `tests/golden/Capacity1-3`."""

import pytest

from raptor.core.engine.capacity import (
    INT_FLOW_MAX,
    CapacityState,
    analyze_capacity,
    build_capacity_graph,
    determine_max_capacity,
    distribute_flow,
    modsim_round,
    reset_for_new_run,
)
from raptor.core.engine.stats import TimedStat
from raptor.core.model import Link, LinkStatus, RBDBlock, RBDNode, NodeStatus


# --- helpers ---------------------------------------------------------------


def mk_node(nid, ntype, *, full=True, any_path=True, name=None):
    n = RBDNode(node_name=name or f"n{nid}", id=nid, type_node=ntype)
    n.full_flow = full
    n.any_path = any_path
    return n


def mk_link(lid, src, dst, *, nom=-2, max_=-1, pri=50):
    return Link(
        object_num=lid, connect_from_id=src[0], connect_to_id=dst[0],
        connect_f_ref="RBD" + src[1], connect_t_ref="RBD" + dst[1],
        nom_flow=nom, max_flow=max_, cap_priority=pri, status=LinkStatus.UP,
    )


def chain_model():
    """start(1) -l1-> A(block 10) -l2-> end(3): the transparent-chain case.

    Returns (nodes, blocks, events, hiers, links) with NRBD arrays already
    populated (what build_efpa_graph would produce) -- capacity.py builds on
    those."""
    start = mk_node(1, 1)
    end = mk_node(3, 3)
    nodes = {1: start, 3: end}
    links = {
        1: mk_link(1, (1, "Node"), (10, "Block")),
        2: mk_link(2, (10, "Block"), (3, "Node")),
    }
    # NRBD of start walks through block 10 to end via link 2.
    start.next_node_ids = [3]
    start.next_node_link_ids = [2]
    end.in_paths_array = [2]
    blocks = {}
    b = RBDBlock(name="A", id=10)
    b.downstream_link_id = 2
    blocks[10] = b
    return nodes, blocks, {}, {}, links


# --- modsim_round ------------------------------------------------------------


def test_modsim_round_half_away_from_zero():
    assert modsim_round(0.5) == 1
    assert modsim_round(1.5) == 2
    assert modsim_round(2.5) == 3
    assert round(2.5) == 2  # sanity: Python disagrees, that's the point
    assert modsim_round(100.0 / 3.0) == 33
    assert modsim_round(-0.5) == -1


# --- graph build --------------------------------------------------------------


def test_build_capacity_graph_transparent_chain():
    """All-transparent topology: only Start and End are boundaries; Start's
    cap path is the FINAL link into End (the walk skips over the block)."""
    nodes, blocks, events, hiers, links = chain_model()
    group, cap_links = build_capacity_graph(nodes, blocks, events, hiers, links, 100)
    assert [n.id for n in group] == [1, 3]
    assert nodes_by(group, 1).cap_connect_to == [3]
    assert nodes_by(group, 1).cap_connect_path == [2]
    assert cap_links == [2]
    # End is full-flow -> its incoming link gets the unbounded band.
    assert links[2].sim_nom_flow == INT_FLOW_MAX
    assert links[2].sim_max_flow == INT_FLOW_MAX


def test_build_capacity_graph_boundary_with_sentinels():
    """Two parallel block feeds into a fullFlow=0 join. The `-1` sentinel
    means flowGenerated; positional outbound priorities are not assigned to
    a non-anyPath boundary, so the extended raw priorities are kept."""
    start = mk_node(1, 1)
    j = mk_node(5, 2, full=False)
    end = mk_node(3, 3)
    nodes = {1: start, 5: j, 3: end}
    links = {
        1: mk_link(1, (1, "Node"), (10, "Block"), pri=50),
        2: mk_link(2, (10, "Block"), (5, "Node"), nom=-1, max_=30),
        3: mk_link(3, (1, "Node"), (11, "Block"), pri=50),
        4: mk_link(4, (11, "Block"), (5, "Node"), nom=-1, max_=50),
        5: mk_link(5, (5, "Node"), (3, "Node")),
    }
    start.next_node_ids = [5, 5]
    start.next_node_link_ids = [2, 4]
    j.next_node_ids = [3]
    j.next_node_link_ids = [5]
    j.in_paths_array = [2, 4]
    blocks = {}
    for bid, out in ((10, 2), (11, 4)):
        b = RBDBlock(name=f"B{bid}", id=bid)
        b.downstream_link_id = out
        blocks[bid] = b
    group, _cap_links = build_capacity_graph(nodes, blocks, {}, {}, links, 100)
    assert [n.id for n in group] == [1, 5, 3]
    s = nodes_by(group, 1)
    assert sorted(zip(s.cap_connect_to, s.cap_connect_path)) == [(5, 2), (5, 4)]
    # Sentinels resolve against the target node's fan-in (into_num=2); here
    # the maxFlow column is the literal 30/50.
    assert links[2].sim_max_flow == 30
    assert links[4].sim_max_flow == 50
    assert links[2].sim_nom_flow == 30  # -1 -> maximum
    # Positional priorities assigned to Start's cap paths (anyPath=1).
    prio = {lid: links[lid].sim_cap_priority for lid in (2, 4)}
    assert sorted(prio.values()) == [1, 2]


def test_sentinel_minus_two_is_even_share_of_fan_in():
    start = mk_node(1, 1)
    j = mk_node(5, 2, full=False)
    end = mk_node(3, 3)
    nodes = {1: start, 5: j, 3: end}
    links = {
        1: mk_link(1, (1, "Node"), (10, "Block")),
        2: mk_link(2, (10, "Block"), (5, "Node"), nom=-2, max_=-1),
        3: mk_link(3, (1, "Node"), (11, "Block")),
        4: mk_link(4, (11, "Block"), (5, "Node"), nom=-2, max_=-1),
        5: mk_link(5, (5, "Node"), (3, "Node")),
    }
    start.next_node_ids = [5, 5]
    start.next_node_link_ids = [2, 4]
    j.next_node_ids = [3]
    j.next_node_link_ids = [5]
    j.in_paths_array = [2, 4]
    blocks = {}
    for bid, out in ((10, 2), (11, 4)):
        b = RBDBlock(name=f"B{bid}", id=bid)
        b.downstream_link_id = out
        blocks[bid] = b
    build_capacity_graph(nodes, blocks, {}, {}, links, 100)
    # max=-1 -> 100 each; nom=-2 -> ceil(max / into_num=2) = 50 each.
    assert links[2].sim_max_flow == 100 and links[2].sim_nom_flow == 50
    assert links[4].sim_max_flow == 100 and links[4].sim_nom_flow == 50


def nodes_by(group, nid):
    return next(n for n in group if n.id == nid)


# --- DistributeFlow -------------------------------------------------------------


def two_branch_model():
    """The CapacitySplit shape: start -> [A,B] -> join(fullFlow=0) -> end,
    with sim values pre-set (30/30 and 50/50)."""
    start = mk_node(1, 1)
    start.cap_node = True
    start.cap_connect_to = [5, 5]
    start.cap_connect_path = [2, 4]
    start.cap_connect_out = 2
    start.propagation_out_count = 0
    j = mk_node(5, 2, full=False)
    j.status = NodeStatus.ALL_UP
    j.node_flow = 0
    j.cap_node = True
    j.in_paths_array = [2, 4]
    j.cap_connect_to = [3]
    j.cap_connect_path = [5]
    j.cap_connect_out = 1
    j.propagates_to_node_ids = [3]
    j.propagates_to_link_ids = [5]
    j.propagation_out_count = 1
    end = mk_node(3, 3)
    end.status = NodeStatus.ALL_UP
    end.node_flow = 0
    end.cap_node = True
    end.in_paths_array = [5]
    nodes = {1: start, 5: j, 3: end}
    l2 = mk_link(2, (10, "Block"), (5, "Node"))
    l4 = mk_link(4, (11, "Block"), (5, "Node"))
    l5 = mk_link(5, (5, "Node"), (3, "Node"))
    for l, nom, mx in ((l2, 30, 30), (l4, 50, 50), (l5, INT_FLOW_MAX, INT_FLOW_MAX)):
        l.sim_nom_flow, l.sim_max_flow = nom, mx
    links = {2: l2, 4: l4, 5: l5}
    return nodes, links


def test_distribute_common_node_even_split_and_loss():
    nodes, links = two_branch_model()
    rejected = distribute_flow(nodes[1], 100, nodes, links)
    assert rejected == 20  # 80 deliverable
    assert links[2].actual_flow == 30
    assert links[4].actual_flow == 50
    assert nodes[5].node_flow == 80
    # End's own raw node_flow stays 0 (recursion never enters a type-3
    # node); only the NodeFlow array copy carries Start's value, covered by
    # the analyze_capacity tests via state.node_flow[3].


def test_distribute_single_remaining_branch():
    nodes, links = two_branch_model()
    links[2].status = LinkStatus.DOWN  # A down: its feed link drops
    rejected = distribute_flow(nodes[1], 100, nodes, links)
    assert rejected == 50
    assert links[4].actual_flow == 50
    assert nodes[1].node_flow == 50


def test_distribute_all_blocked_rejects_everything():
    nodes, links = two_branch_model()
    links[2].status = LinkStatus.DOWN
    links[4].status = LinkStatus.DOWN
    assert distribute_flow(nodes[1], 100, nodes, links) == 100
    assert nodes[1].node_flow == 0


def test_distribute_priority_groups_fill_in_order():
    """An anyPath boundary with distinct priorities: group 1 fills to both
    nominal and max before group 2 gets anything."""
    start = mk_node(1, 1)
    start.cap_node = True
    start.cap_connect_to = [8, 9]  # m1, m2 (both boundaries)
    start.cap_connect_path = [4, 6]
    start.cap_connect_out = 2
    m1 = mk_node(8, 2, full=False)
    m1.status = NodeStatus.ALL_UP
    m1.node_flow = 0
    m1.cap_node = True
    m1.cap_connect_to = [3]
    m1.cap_connect_path = [7]
    m1.cap_connect_out = 1
    m1.propagates_to_node_ids = [3]
    m1.propagates_to_link_ids = [7]
    m1.propagation_out_count = 1
    m2 = mk_node(9, 2, full=False)
    m2.status = NodeStatus.ALL_UP
    m2.node_flow = 0
    m2.cap_node = True
    m2.cap_connect_to = [3]
    m2.cap_connect_path = [8]
    m2.cap_connect_out = 1
    m2.propagates_to_node_ids = [3]
    m2.propagates_to_link_ids = [8]
    m2.propagation_out_count = 1
    end = mk_node(3, 3)
    end.status = NodeStatus.ALL_UP
    end.node_flow = 0
    end.cap_node = True
    end.in_paths_array = [7, 8]
    nodes = {1: start, 8: m1, 9: m2, 3: end}
    l4 = mk_link(4, (3, "Block"), (8, "Node"))
    l6 = mk_link(6, (4, "Block"), (9, "Node"))
    l7 = mk_link(7, (8, "Node"), (3, "Node"))
    l8 = mk_link(8, (9, "Node"), (3, "Node"))
    for l, nom, mx in ((l4, 40, 40), (l6, 20, 60), (l7, INT_FLOW_MAX, INT_FLOW_MAX), (l8, INT_FLOW_MAX, INT_FLOW_MAX)):
        l.sim_nom_flow, l.sim_max_flow = nom, mx
    links = {4: l4, 6: l6, 7: l7, 8: l8}
    rejected = distribute_flow(start, 100, nodes, links)
    assert rejected == 0
    assert l4.actual_flow == 40  # priority 1 filled first
    assert l6.actual_flow == 60  # then priority 2 surged past its nominal 20


def test_distribute_marks_path_full_on_downstream_rejection():
    """A downstream boundary with NO outlet rejects; its feeders get
    path_full and the loss propagates up."""
    start = mk_node(1, 1)
    start.cap_node = True
    start.cap_connect_to = [8]
    start.cap_connect_path = [4]
    m1 = mk_node(8, 2, full=False)
    m1.status = NodeStatus.ALL_UP
    m1.node_flow = 0
    m1.cap_node = True
    m1.cap_connect_to = []
    m1.cap_connect_path = []  # dead end at the cap level
    m1.cap_connect_out = 0
    m1.propagates_to_node_ids = []
    m1.propagates_to_link_ids = []
    m1.propagation_out_count = 0
    nodes = {1: start, 8: m1}
    l4 = mk_link(4, (3, "Block"), (8, "Node"))
    l4.sim_nom_flow, l4.sim_max_flow = 40, 40
    links = {4: l4}
    rejected = distribute_flow(start, 100, nodes, links)
    assert rejected == 100  # nothing routable anywhere
    assert l4.path_full or l4.actual_flow == 0


def test_distribute_target_node_down_blocks_group():
    nodes, links = two_branch_model()
    nodes[5].status = NodeStatus.DOWN  # join Bad: no branch may send
    assert distribute_flow(nodes[1], 100, nodes, links) == 100


# --- runtime analysis ------------------------------------------------------------


def test_analyze_capacity_green_probe_and_records():
    nodes, links = two_branch_model()
    state = CapacityState()
    reset_for_new_run(state, nodes)
    group = [nodes[i] for i in (1, 5, 3)]
    cap_ids = [2, 4, 5]
    analyze_capacity(
        state, new_sys_status_red=False, sim_time=0.0,
        cap_node_group=group, cap_link_ids=cap_ids, nodes_by_id=nodes,
        links_by_id=links, start_node=nodes[1], end_node=nodes[3],
        flow_generated=100, flowing_at_max_cap=False,
    )
    # Not flowing at max cap: Start's recorded capacity is the probe result
    # (system max = 80), and delivered flow is 80.
    assert state.node_flow[1] == 80
    assert state.node_capacity[1] == 80
    # Join's capacity is the sum of up incoming links' simMax.
    assert state.node_capacity[5] == 80
    # End node's flow mirrors Start's.
    assert state.node_flow[3] == 80
    # CapFile line emitted (flow changed from last_flow=-1), dedup format.
    assert len(state.cap_file_lines) == 1
    assert state.cap_file_lines[0].split() == ["0.000000", "80", "80"]


def test_analyze_capacity_red_zeroes_start():
    nodes, links = two_branch_model()
    state = CapacityState()
    reset_for_new_run(state, nodes)
    group = [nodes[i] for i in (1, 5, 3)]
    analyze_capacity(
        state, new_sys_status_red=True, sim_time=5.0,
        cap_node_group=group, cap_link_ids=[2, 4, 5], nodes_by_id=nodes,
        links_by_id=links, start_node=nodes[1], end_node=nodes[3],
        flow_generated=100, flowing_at_max_cap=False,
    )
    assert state.node_capacity[1] == 0
    assert state.node_flow[1] == 0  # nothing distributed while Red


def test_analyze_capacity_flowing_at_max_skips_probe():
    nodes, links = two_branch_model()
    state = CapacityState()
    reset_for_new_run(state, nodes)
    group = [nodes[i] for i in (1, 5, 3)]
    analyze_capacity(
        state, new_sys_status_red=False, sim_time=0.0,
        cap_node_group=group, cap_link_ids=[2, 4, 5], nodes_by_id=nodes,
        links_by_id=links, start_node=nodes[1], end_node=nodes[3],
        flow_generated=80, flowing_at_max_cap=True,
    )
    # flowing_at_max_cap skips the probe pass: start's capacity comes from
    # the real pass's own delivered amount (80 == flowGenerated).
    assert state.node_capacity[1] == 80
    assert state.node_flow[1] == 80


def test_determine_max_capacity_restores_state():
    nodes, links = two_branch_model()
    group = [nodes[i] for i in (1, 5, 3)]
    max_cap = determine_max_capacity(nodes[1], group, [2, 4, 5], nodes, links)
    assert max_cap == 80
    assert all(l.actual_flow == 0 for l in links.values())
    assert not any(l.path_full for l in links.values())
    assert all(n.node_flow == 0 for n in group)


def test_timed_stat_time_weighted_mean():
    ts = TimedStat()
    ts.reset(0, 0.0)
    ts.update(80, 0.0)   # 80 holds [0,10)
    ts.update(40, 10.0)  # 40 holds [10,20)
    assert ts.mean_at(20.0) == pytest.approx(60.0)
    assert ts.minimum == 40 or ts.minimum == 0  # seeded value counts too
    assert ts.maximum == 80


def test_capacity_run_report_usage_and_capability():
    from raptor.core.engine.capacity import build_capacity_run_report

    nodes, links = two_branch_model()
    state = CapacityState()
    reset_for_new_run(state, nodes)
    group = [nodes[i] for i in (1, 5, 3)]
    analyze_capacity(
        state, new_sys_status_red=False, sim_time=0.0,
        cap_node_group=group, cap_link_ids=[2, 4, 5], nodes_by_id=nodes,
        links_by_id=links, start_node=nodes[1], end_node=nodes[3],
        flow_generated=100, flowing_at_max_cap=False,
    )
    rep = build_capacity_run_report(state, group, 10.0, 100, 0.0)
    assert rep.system_node_id == 1
    assert rep.units_produced == pytest.approx(80 * 10)
    assert rep.ave_lost_flow == pytest.approx(20.0)
    sys_stats = rep.nodes[1]
    assert sys_stats.usage == pytest.approx(1.0)
    assert sys_stats.capability == pytest.approx(1.0)
