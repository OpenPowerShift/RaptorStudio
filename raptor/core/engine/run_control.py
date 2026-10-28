"""Per-run reset performed at each new run's `SimTime=0.0` boundary.

Each run resets, while RNG stream state persists across runs (see
`block_lifecycle.py`):
- a block's own `op_status` -> `Running` (via the "Initialize"
  `ChangeBlockState` call at the top of every captured run);
- a node's `status` -> `AllUp`, its incoming links' `status` -> `Up` and
  `active` -> `True`, and its per-path delta accumulators cleared;
- all of `SystemState` (`num_currently_failed`, `system_up`, `color`,
  `red_string`, `last_up_down_change`, ...).

This is a direct reset, not a delta routed through the usual
`ChangeBlockState`/`incFails` machinery, which would drive
`num_currently_failed` negative off a prior run's `Done` state. Callers
reset block/event timestamps separately in `orchestrator.py`.

Checked against `tests/golden/PrePostLDT` and `tests/golden/FiniteSparePool`
in `tests/unit/test_system_status.py` and `tests/unit/test_reconfigure_system.py`.
"""
from __future__ import annotations

from raptor.core.model import ActiveStatus, Link, NodeStatus, LinkStatus, RBDNode

from raptor.core.engine.stats import RunningStat
from raptor.core.engine.system_status import SystemColor, SystemState


def reset_node_and_links_for_new_run(node: RBDNode, links_by_id: dict[int, Link]) -> None:
    """Resets one node's status and its incoming links' runtime state
    (`LinkStatus`, the separate permanent-reachability `.active` flag,
    and the per-path delta accumulators `reconfigure_node` maintains)
    back to a fresh "everything up" baseline. Only touches links in
    `node.in_paths_array` — call once per node for a multi-node topology,
    not once for the whole `links_by_id` dict (which may hold links
    belonging to other nodes too).
    """
    node.status = NodeStatus.ALL_UP
    # Port of `RBDNodeObj.SetToInitValues`'s unconditional
    # `activeStatus := Active;`. `model.nodes_by_id`
    # is shared across runs, so without this a hierarchy node would keep the
    # prior run's phase-1 Cut/Linked kickoff status, and the next run's
    # kickoff cascade would compute `change_node_state`'s deltas against a
    # stale Cut node instead of a fresh Active one.
    node.active_status = ActiveStatus.ACTIVE
    # `state_start_time` -- the same stale-cross-run issue, for weak-link
    # time bookkeeping. `nodes.change_node_state` computes
    # `status_time = sim_time - node.state_start_time` on every transition;
    # without this reset a node's first transition in run N>1 subtracts the
    # previous run's final timestamp from this run's small `sim_time`,
    # crediting a large negative duration to the state the node started in.
    # Block and event timestamps get the same reset in `orchestrator.py`.
    node.state_start_time = 0.0
    # `higher_cold` (`cold_standby.run_cold_logic`'s `SetHigherCold` port):
    # set while this node is itself a cold-standby group leader whose own
    # switching is frozen because a higher-priority candidate above it is
    # active, cleared once that candidate loses its slot. A run ending while
    # mid-suppression would leave it stuck true, disabling
    # `nodes.reconfigure_node`'s `mark_cold_changed` gate for this node in
    # every later run.
    node.higher_cold = False
    node.path_fail_value = []
    node.path_idle_value = []
    node.path_standby_value = []
    node.path_pm_value = []
    for path_id in node.in_paths_array:
        link = links_by_id[path_id]
        link.status = LinkStatus.UP
        link.active = True
        # Port of `Link.SetToInitValues`: every link
        # resets `switch_status` to `"ready"` and `switch_time` to `0.0` every
        # run, unconditionally. Only cold-standby logic reads these fields.
        link.switch_status = "ready"
        link.switch_time = 0.0


def reset_system_state_for_new_run(system_state: SystemState) -> None:
    """Resets every field of `SystemState` to its fresh-run baseline."""
    system_state.num_currently_failed = 0
    system_state.system_up = True
    system_state.color = SystemColor.GREEN
    system_state.red_string = ""
    system_state.total_red_cost = 0.0
    system_state.last_up_down_change = 0.0
    system_state.last_color_change = 0.0
    system_state.green_stat = RunningStat()
    system_state.yellow_stat = RunningStat()
    system_state.red_stat = RunningStat()
    system_state.sys_fail_stat = RunningStat()
    system_state.stats_started = False
    system_state.sys_fail_times = []
    system_state.sys_repair_times = []
    system_state.count_failure = True
    system_state.time_of_comp_failure = 0.0
    system_state.comp_fail_stat = RunningStat()
    system_state.comp_repair_stat = RunningStat()
    system_state.mission_success = True
    system_state.missions_completed = 0
