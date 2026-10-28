"""Ports the two dispatchers that turn a state-change's deltas into a
downstream update: `RBDBlockObj.ReconfigureBlock` (block -> node) and the
cascade tail of `ChangeNodeState` (node -> node, for multi-hop topologies like
`join -> end`).

Sources: Raptor 7 and Raptor 7's `ChangeNodeState`.

Also ports `ReconfigureSystem`'s recursive "permanently down" trigger
(`reconfigure_system`), which tracks path
reachability (`NumActivePaths`/`link.active`). This is a different mechanism
from `AnalyzeSystem`'s Green/Yellow/Red color aggregation in
`system_status.py`: it is a one-way ratchet within a run (once a path goes
permanently inactive, e.g. a block runs permanently out of spares, it never
comes back for the rest of that run), and it resets at the next run boundary.

The `hasDepObjects`/`BackFlowDependencies` branches are ported separately in
`dependency.py`, which callers run alongside this module. Node-level
dependency propagation and hierarchy propagation remain out of scope, as does
the part of `ReconfigureSystem` narrowed out in `reconfigure_system`'s own
docstring.

Checked against `tests/golden/FiniteSparePool` (single-hop) and
`tests/golden/Cascade` (multi-hop) in `tests/unit/test_reconfigure_system.py`.
"""
from __future__ import annotations

from collections.abc import Callable

from raptor.core.engine.eventlog import format_event_log_line
from raptor.core.engine.nodes import NodeReconfigureResult, reconfigure_node
from raptor.core.model import Link, RBDBlock, RBDNode
from raptor.core.rng import RandomSource


def reconfigure_after_block_change(
    block: RBDBlock,
    node: RBDNode,
    links_by_id: dict[int, Link],
    run_change: int,
    fail_change: int,
    idle_change: int,
    standby_change: int,
    pm_change: int,
    sim_time: float,
    *,
    events_file: bool = False,
    hier_name: str | None = None,
    sb_link_rng: RandomSource | None = None,
    on_link_status_change: Callable[[], None] | None = None,
    interrupt_switch: Callable[[Link], None] | None = None,
    on_cold_link_down: Callable[[Link], None] | None = None,
) -> NodeReconfigureResult | None:
    """Port of `ChangeBlockState`'s dispatch guard
     plus `ReconfigureBlock` itself, minus the deferred dependency-
    propagation branches (see module docstring): a sum of 2 means exactly
    one "from" bucket and one "to" bucket changed (the normal single-
    transition case) and triggers the cascade; a sum of 0 means no real
    transition (e.g. Cut/Linked bookkeeping) and is a silent no-op; any
    other sum is the source's `"Error2 in ChangeBlockState!"` case (should
    not happen with well-formed transitions). Returns `None` for the sum-0
    no-op case, same as `reconfigure_node` returning `None` when nothing
    changed.

    `node`/`links_by_id` stand in for the source's `ASK window
    Descendant("RBDNode", EFPAnode)` and `ASK root Child("RBDLink", ...)`
    lookups — caller-owned. `block.downstream_link_id` is passed as `reconfigure_
    node`'s `in_path`, matching `ReconfigureBlock`'s call with `EFPAlink`.
    `hier_name`: forwarded to `reconfigure_node` verbatim -- pass this
    when `node.type_node == 5` (the block connects directly into a
    hierarchy's internal exit node), see `nodes.determine_node_status`.
    """
    delta_sum = abs(run_change) + abs(fail_change) + abs(idle_change) + abs(standby_change) + abs(pm_change)
    if delta_sum == 0:
        return None
    if delta_sum != 2:
        raise ValueError("Error2 in ChangeBlockState!")

    return reconfigure_node(
        node, links_by_id, run_change, fail_change, idle_change, standby_change, pm_change,
        block.downstream_link_id, sim_time, events_file=events_file, hier_name=hier_name, sb_link_rng=sb_link_rng,
        on_link_status_change=on_link_status_change, interrupt_switch=interrupt_switch,
        on_cold_link_down=on_cold_link_down,
    )


def reconfigure_after_node_change(
    node: RBDNode,
    nodes_by_id: dict[int, RBDNode],
    links_by_id: dict[int, Link],
    run_change: int,
    fail_change: int,
    idle_change: int,
    standby_change: int,
    pm_change: int,
    sim_time: float,
    *,
    events_file: bool = False,
    hier_names_by_node_id: dict[int, str] | None = None,
    on_link_status_change: Callable[[], None] | None = None,
    interrupt_switch: Callable[[Link], None] | None = None,
) -> list[NodeReconfigureResult]:
    """Port of `ChangeNodeState`'s downstream-cascade tail,
    the node-level analog of `reconfigure_after_block_change`.
    Unlike a block (one `EFPAnode`/`EFPAlink`), a node may fan out to
    *several* downstream nodes (`node.propagates_to_node_ids`/`propagates_to_link_ids`,
    parallel lists — `EFPAConnectTo`/`EFPAConnectPath` in the source),
    so this calls `reconfigure_node` once per downstream connection and
    returns every non-`None` result, in `propagates_to_node_ids` order.

    Same delta-sum contract as `reconfigure_after_block_change`: sum of 2
    triggers the cascade (looping all downstream connections), sum of 0
    is a silent no-op (matches the source: there is no `ELSIF` for the
    sum-0 case, only the sum-2 cascade and a sum-elsewhere error), any
    other sum is the source's `"Error in ChangeNodeState!"` case.

    `hier_names_by_node_id`: `{downstream node id: parent RBDHier.name}`
    for any downstream node with `type_node == 5` in this fan-out (see
    `nodes.determine_node_status`) -- omit entirely if none of them are a
    hierarchy's internal exit node.
    """
    delta_sum = abs(run_change) + abs(fail_change) + abs(idle_change) + abs(standby_change) + abs(pm_change)
    if delta_sum == 0:
        return []
    if delta_sum != 2:
        raise ValueError("Error in ChangeNodeState!")

    results = []
    for downstream_id, path_link in zip(node.propagates_to_node_ids, node.propagates_to_link_ids):
        downstream_node = nodes_by_id[downstream_id]
        hier_name = None if hier_names_by_node_id is None else hier_names_by_node_id.get(downstream_id)
        result = reconfigure_node(
            downstream_node, links_by_id, run_change, fail_change, idle_change,
            standby_change, pm_change, path_link, sim_time, events_file=events_file, hier_name=hier_name,
            on_link_status_change=on_link_status_change, interrupt_switch=interrupt_switch,
        )
        if result is not None:
            results.append(result)
    return results


def reconfigure_system(
    node: RBDNode,
    nodes_by_id: dict[int, RBDNode],
    links_by_id: dict[int, Link],
    in_path: int,
    sim_time: float,
    *,
    events_file: bool = False,
) -> list[str]:
    """Port of `RBDNodeObj.ReconfigureSystem`
    -- the "a path just became PERMANENTLY unusable" reachability check,
    distinct from (and layered on top of) the ordinary link/node status
    cascade in `nodes.py`. Marks `links_by_id[in_path].active = False`
    (`SetActiveLink(FALSE)`, permanent — unlike `LinkStatus`, this never
    flips back), recomputes the node's active-path count from `node.
    in_paths_array`, and — only on the specific edge where that count
    just dropped to exactly `node.min_good_paths_required - 1` (source's
    `MinGoodPathsReq` -- the minimum per-phase k when phased, else
    `goodPaths`):

    - `typeNode=3` (End node): returns the `"Permanently_Down"` log line.
    - anything else: recurses into every `(node.propagates_to_node_ids[j],
      node.propagates_to_link_ids[j])` pair (`EFPAConnectTo`/`EFPAConnectPath`,
      populated for multi-hop fan-out topologies by `graph.build_efpa_graph`'s
      `TestFail` pass), collecting every `"Permanently_Down"` line the
      recursion produces further downstream.

    Narrower than the source in two ways: (1) `finished`/`coldStandby`/
    `BackFlowDependencies` bookkeeping (the `typeNode=2` branch's own separate
    side effects, alongside its recursion) and `termType=2`'s
    `CallEndSimulation` are not modeled; (2) the calling block's own
    `ReconfigureSystem` forwarder (which decrements
    the block's own separate `NumActivePaths` before calling this) is skipped,
    so callers call this directly with the block's `downstream_link_id` as `in_path`.
    """
    prior_active = sum(1 for lid in node.in_paths_array if links_by_id[lid].active)
    links_by_id[in_path].active = False
    new_active = sum(1 for lid in node.in_paths_array if links_by_id[lid].active)

    # `min_good_paths_required` defaults to 0 on a hand-built `RBDNode` (only
    # `typed.node_from_row` populates it); fall back to `good_paths`, which
    # is its own value for an unphased node.
    min_req = node.min_good_paths_required if node.min_good_paths_required > 0 else node.good_paths
    if new_active != min_req - 1 or prior_active == new_active:
        return []

    if node.type_node == 3:
        if not events_file:
            return []
        return [format_event_log_line(sim_time, "System", "Permanently_Down", "Out_of_spares", "System_change")]

    logs: list[str] = []
    for downstream_id, path_link in zip(node.propagates_to_node_ids, node.propagates_to_link_ids):
        downstream_node = nodes_by_id[downstream_id]
        logs += reconfigure_system(
            downstream_node, nodes_by_id, links_by_id, path_link, sim_time, events_file=events_file,
        )
    return logs
