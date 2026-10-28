"""Unit tests for `run_control.py`'s per-run reset functions. These are
domain-independent structural checks; `test_system_status.py` and
`test_reconfigure_system.py` cover the same functions against captured
multi-run fixtures."""
from __future__ import annotations

from raptor.core.engine.run_control import reset_node_and_links_for_new_run, reset_system_state_for_new_run
from raptor.core.engine.system_status import SystemColor, SystemState
from raptor.core.model import Link, LinkStatus, NodeStatus, RBDNode


def test_reset_node_and_links_restores_fresh_baseline() -> None:
    node = RBDNode(node_name="end", good_paths=1, in_paths_array=[2, 4])
    node.status = NodeStatus.DOWN
    node.path_fail_value = [1, 1]
    node.path_idle_value = [0, 1]
    node.path_standby_value = [1, 0]
    node.path_pm_value = [0, 0]
    links_by_id = {
        2: Link(object_num=2, status=LinkStatus.DOWN, active=False),
        4: Link(object_num=4, status=LinkStatus.IDLE, active=False),
    }

    reset_node_and_links_for_new_run(node, links_by_id)

    assert node.status == NodeStatus.ALL_UP
    assert node.path_fail_value == []
    assert node.path_idle_value == []
    assert node.path_standby_value == []
    assert node.path_pm_value == []
    assert links_by_id[2].status == LinkStatus.UP
    assert links_by_id[2].active is True
    assert links_by_id[4].status == LinkStatus.UP
    assert links_by_id[4].active is True


def test_reset_node_and_links_only_touches_this_nodes_links() -> None:
    node = RBDNode(node_name="join", good_paths=1, in_paths_array=[2])
    other_link = Link(object_num=99, status=LinkStatus.DOWN, active=False)
    links_by_id = {2: Link(object_num=2, status=LinkStatus.DOWN, active=False), 99: other_link}

    reset_node_and_links_for_new_run(node, links_by_id)

    assert links_by_id[2].status == LinkStatus.UP
    assert other_link.status == LinkStatus.DOWN, "link belonging to another node must not be touched"


def test_reset_system_state_restores_fresh_baseline() -> None:
    state = SystemState()
    state.num_currently_failed = 3
    state.system_up = False
    state.color = SystemColor.RED
    state.red_string = "_r3_h4"
    state.last_up_down_change = 42.5

    reset_system_state_for_new_run(state)

    assert state.num_currently_failed == 0
    assert state.system_up is True
    assert state.color == SystemColor.GREEN
    assert state.red_string == ""
    assert state.last_up_down_change == 0.0
