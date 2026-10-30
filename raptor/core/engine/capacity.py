"""Capacity analysis -- ports of:

- Raptor 7's capacity sections: the per-node
  `CapConnectTo`/`CapConnectPath` boundary-graph build via
  `RBDNodeObj.FindCapPaths`,
  `SortCapacityArrays`, the per-link
  `simNomFlow`/`simMaxFlow` derivation from the `.rbd` `nomFlow`/`MaxFlow`
  columns' `-1`/`-2` sentinels, and the `depLink`/siblings mirror wiring;
- `RBDNodeObj.DistributeFlow` -- the two-phase
  (nominal-then-maximum) priority-grouped flow router;
- `AnalyzeSystem`'s capacity half -- the per-trigger
  flow recomputation feeding the `LMONITORED` `NodeFlow`/`NodeCapacity` arrays,
 plus the CapFile writer;
- Raptor 7's per-run `DetermineMaxCapacity`/`FlowingAtMaxCap`
  setup, and `DoEndOfRunUpdate`'s per-run stat extraction.

Checked in `tests/unit/test_capacity.py` against hand-computed topologies and
in `tests/unit/test_capacity_e2e.py` against `tests/golden/Capacity1`, `Capacity2` and `Capacity3`.

Mechanics summary:

- A node is a capacity boundary (`capNode`) when it is End (type 3), Start
  (type 1), or has `NOT fullFlow` OR `NOT anyPath`. Flow enters at Start,
  recurses through transparent nodes, and is distributed at each boundary
  across its cap-downstream links, grouped by equal `simCapPriority`
  (ascending): every group is filled to nominal first, then spare capacity up
  to each link's maximum. Whatever cannot be routed is lost (`rejected`),
  which is how partial failures reduce system output.
- A link's usable band is `[0, simMaxFlow]` with a soft target of
  `simNomFlow`; both derive from the file columns where `-1` means
  "flowGenerated" and `-2` means "even share of flowGenerated" (max) or
  "even share of max" (nominal).
- Analysis triggers (`CapAnalysisRequired`): SimTime=0
  and a link status change that CROSSES LinkUp in either direction
  (`ChangeLinkStatus`: a change between two non-Up statuses
  leaves the flag alone) -- with a deliberate quirk: the flag is cleared when
  `AnalyzeSystem` finds the system staying Red, so link changes
  while the system remains Red do not
  re-run the analysis.
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field

from raptor.core.engine.report_format import real_to_str
from raptor.core.engine.stats import TimedStat, update_many
from raptor.core.model import Link, LinkStatus, RBDBlock, RBDEvent, RBDHier, RBDNode, NodeStatus

#: The source's own flow infinity.
INT_FLOW_MAX = 999_999_999

_UP_LIKE_NODE = (NodeStatus.ALL_UP, NodeStatus.DEGRADED)


def modsim_round(value: float) -> int:
    """Modsim `ROUND` -- round-half-away-from-zero (Python's built-in `round()`
    is banker's rounding). Flow-share amounts are always non-negative here.

    Non-finite inputs (inf/nan) can appear when a GUI Stop aborts the run
    mid-distribution and a monitor mean divides by ~zero time. The aborted
    run's numbers are discarded, so map them to 0 instead of crashing."""
    if value != value or value in (float("inf"), float("-inf")):
        return 0
    return math.floor(value + 0.5) if value >= 0 else -math.floor(-value + 0.5)


def _pad(value: str, width: int) -> str:
    return (value + " " * width)[:width]


# --- graph build (RunEFPA capacity sections) ------------------------------


def _extend_raw_priorities(
    nodes_by_id: dict[int, RBDNode],
    blocks_by_id: dict[int, RBDBlock],
    events_by_id: dict[int, RBDEvent],
    hiers_by_id: dict[int, RBDHier],
    links_by_id: dict[int, Link],
) -> None:
    """Port of the NRBD pass's capacity hook:
    for every node, each NRBD entry's final link receives the
    corresponding raw out-link's `simCapPriority` (its file CapPri). The j-th
    NRBD entry corresponds 1:1 to the j-th raw out-link, since both enumerate
    the same outgoing list in order. `FindCapPaths` then reads these extended
    values, including through hierarchy-In branches
    where the extended link is the hierarchy-internal one."""
    # Index outgoing links by source once. The old body re-sorted and
    # re-scanned every link for every node -- O(nodes x links), which is
    # seconds on a model with ~900 nodes and ~5,000 links.
    raw_by_source: dict[tuple[str, int], list[Link]] = {}
    for link in sorted(links_by_id.values(), key=lambda x: x.object_num):
        raw_by_source.setdefault(
            (link.connect_f_ref, link.connect_from_id), []).append(link)
    for node in nodes_by_id.values():
        if node.type_node == 3 or not node.next_node_link_ids:
            continue
        source_kind, source_id = ("RBDHier", node.parent_id) if node.type_node == 5 else ("RBDNode", node.id)
        raws = raw_by_source.get((source_kind, source_id), [])
        if len(raws) != len(node.next_node_link_ids):
            # Fan-out count mismatch -- fall back to positional zip over
            # whatever the walk produced; both derive from the same
            # outgoing iteration order.
            raws = raws[: len(node.next_node_link_ids)]
        # Port of `UpdateOutPathsArray`'s priority rule
        # which runs as each out-link is attached:
        #
        #     IF anyPath   {box on capacity db is checked}
        #        priority:=oldSize+1;
        #     ELSE   {user defined priority}
        #        priority:=link.capPriority;
        #     END IF;
        #     ASK link TO SetSimCapPriority(priority);
        #
        # A node with the capacity box ticked prioritises its out-links
        # positionally; a node without it uses each link's own user-configured
        # `CapPri` column. The positional half is also applied later
        # (gated on `anyPath`).
        for pos, raw_l in enumerate(raws, start=1):
            raw_l.sim_cap_priority = pos if node.any_path else raw_l.cap_priority
        for raw_l, final_lid in zip(raws, node.next_node_link_ids):
            links_by_id[final_lid].sim_cap_priority = raw_l.sim_cap_priority


def _find_cap_paths(
    node: RBDNode,
    test_element_id: int,
    priority: int,
    nodes_by_id: dict[int, RBDNode],
    links_by_id: dict[int, Link],
    downstream_group: list[int],
    propagated_group: set[int],
) -> None:
    """Port of `RBDNodeObj.FindCapPaths`:
    recursive walk over the node's NRBD arrays collecting cap-boundary
    OUTBOUND links into `downstream_group` (deduplicated, accumulation
    order preserved), recursing through transparent targets. Sets each
    collected link's `sim_cap_priority` to the walking priority."""
    node.cap_tested = True
    propagated_group.add(node.id)
    for target_id, link_id in zip(node.next_node_ids, node.next_node_link_ids):
        target = nodes_by_id[target_id]
        link = links_by_id[link_id]
        # Raptor 7 -- on the ORIGIN's own links, refresh the
        # walking priority from the link itself (extended there by the
        # NRBD pass from the raw out-link's simCapPriority).
        if node.id == test_element_id:
            priority = link.sim_cap_priority
        if ((not target.full_flow) or (not target.any_path)) or target.type_node == 3:
            if link_id not in downstream_group:
                downstream_group.append(link_id)
                link.sim_cap_priority = priority
        elif not target.cap_tested:
            _find_cap_paths(
                target, test_element_id, priority, nodes_by_id, links_by_id,
                downstream_group, propagated_group,
            )


def _sort_capacity_arrays(node: RBDNode, links_by_id: dict[int, Link]) -> None:
    """Port of `SortCapacityArrays`: selection
    sort of the node's parallel `cap_connect_path`/`cap_connect_to` arrays
    ascending by each path link's current `sim_cap_priority` (stable --
    swaps only on a strictly smaller priority found later)."""
    n = len(node.cap_connect_path)
    priorities = [links_by_id[lid].sim_cap_priority for lid in node.cap_connect_path]
    for i in range(n):
        swap_index = i
        higher_found = False
        for j in range(i, n):
            if priorities[j] < priorities[swap_index]:
                higher_found = True
                swap_index = j
        if higher_found:
            node.cap_connect_path[i], node.cap_connect_path[swap_index] = (
                node.cap_connect_path[swap_index], node.cap_connect_path[i],
            )
            node.cap_connect_to[i], node.cap_connect_to[swap_index] = (
                node.cap_connect_to[swap_index], node.cap_connect_to[i],
            )
            priorities[swap_index] = priorities[i]


def _resolved_cap_target(link: Link, hiers_by_id: dict[int, RBDHier]) -> int:
    """The NODE id a collected cap-path link points at. Source reads
    `link.EconnectToId`; for node-to-node links that
    equals the raw `connect_to_id`, and a link into a hierarchy must
    redirect to its In node (same as `graph._node_forward_targets`)."""
    if link.connect_t_ref == "RBDHier":
        return hiers_by_id[link.connect_to_id].in_id
    return link.connect_to_id


def build_capacity_graph(
    nodes_by_id: dict[int, RBDNode],
    blocks_by_id: dict[int, RBDBlock],
    events_by_id: dict[int, RBDEvent],
    hiers_by_id: dict[int, RBDHier],
    links_by_id: dict[int, Link],
    flow_generated: int,
) -> tuple[list[RBDNode], list[int]]:
    """Port of Raptor 7's three capacity sections. Mutates
    nodes/links in place; returns `(cap_node_group, cap_link_ids)` --
    the boundary nodes in file-row order and the carrying links in
    collection order (`capLinkGroup`). Call AFTER `build_efpa_graph`
    (needs `nrbd_connect_*`)."""
    _extend_raw_priorities(nodes_by_id, blocks_by_id, events_by_id, hiers_by_id, links_by_id)

    for node in nodes_by_id.values():
        node.cap_node = False
        node.cap_tested = False

    cap_node_group: list[RBDNode] = []
    cap_link_ids: list[int] = []
    propagated_group: set[int] = set()
    for node in nodes_by_id.values():
        if node.type_node == 3:
            node.cap_node = True
        elif node.type_node == 1 or not node.full_flow or not node.any_path:
            node.cap_node = True
            downstream_group: list[int] = []
            _find_cap_paths(
                node, node.id, 0, nodes_by_id, links_by_id, downstream_group, propagated_group
            )
            node.cap_connect_out = len(downstream_group)
            node.cap_connect_to = [
                _resolved_cap_target(links_by_id[lid], hiers_by_id) for lid in downstream_group
            ]
            node.cap_connect_path = list(downstream_group)
            # Raptor 7 collects into capLinkGroup (dedup) as a
            # side effect of the same loop that stores the arrays.
            for lid in downstream_group:
                if lid not in cap_link_ids:
                    cap_link_ids.append(lid)
        for temp_id in list(propagated_group):
            # Raptor 7 -- clear the recursion guard between
            # outer origins.
            nodes_by_id[temp_id].cap_tested = False
            propagated_group.discard(temp_id)
        if node.cap_node:
            cap_node_group.append(node)

    # Per-run Initialize sorts each populated boundary's arrays
    # done once here since the arrays never change
    # after the build, and the positional priorities assigned below follow
    # collection order (which this stable sort preserves).
    for node in cap_node_group:
        if node.cap_connect_path:
            _sort_capacity_arrays(node, links_by_id)

    # Section 3: flow values + outbound priorities.
    for node in cap_node_group:
        into_num = len(node.in_paths_array)
        if node.type_node != 1:
            flow_share = math.ceil(flow_generated / into_num) if into_num else flow_generated
            for link_id in node.in_paths_array:
                link = links_by_id[link_id]
                if node.full_flow:
                    maximum_flow = INT_FLOW_MAX
                    nominal_flow = INT_FLOW_MAX
                else:
                    if link.max_flow == -1:
                        maximum_flow = flow_generated
                    elif link.max_flow == -2:
                        maximum_flow = flow_share
                    else:
                        maximum_flow = link.max_flow
                    if link.nom_flow == -1:
                        nominal_flow = maximum_flow
                    elif link.nom_flow == -2:
                        nominal_flow = math.ceil(maximum_flow / into_num) if into_num else maximum_flow
                    else:
                        nominal_flow = min(link.nom_flow, maximum_flow)
                link.sim_nom_flow = nominal_flow
                link.sim_max_flow = maximum_flow
        if node.type_node != 3 and node.any_path:
            for j, link_id in enumerate(node.cap_connect_path, start=1):
                links_by_id[link_id].sim_cap_priority = j

    # Raptor 7 -- depLink/siblings wiring -- is NOT repeated here.
    # It used to be, in a second implementation that guarded on
    # `link.connect_t_ref` where `graph.build_efpa_graph` guards on
    # `link.resolved_to_kind`, and that lacked the self-reference guard
    # (`if sister is link: continue`) the real one carries. Run against all
    # 23 capacity-enabled golden models it changed nothing -- `graph.py`'s
    # pass, which runs first, has already done the work -- and neither way
    # the two could diverge occurs in any model scanned: a link whose
    # `connect_t_ref` says Block/Event while `resolved_to_kind` does not (0
    # cases), and a self-loop (0 cases).
    #
    # So the copy was redundant AND unguarded, which is why it survived: it
    # could not be distinguished from correct by running anything, and it
    # would have started differing the day a model did hit one of those two
    # cases. The source's single `RunEFPA` procedure is not evidence for two
    # copies of its sections; see the review's E-10.


    return cap_node_group, cap_link_ids


# --- runtime state ---------------------------------------------------------


@dataclass
class CapacityState:
    """One RUN's worth of capacity state -- the module-level `NodeFlow`/
    `NodeCapacity` arrays plus their `ITimedStatObj` monitors, the
    `CapAnalysisRequired`/`FlowingAtMaxCap`/`lastFlow` flags, and the
    CapFile line buffer. Rebuilt fresh every run (`lastFlow:=-1`). Keyed by node ID here (Raptor 7 indexes
    by a sequence number this port does not keep; IDs are unique and
    stable, so the order is equivalent)."""

    #: node id -> time-weighted monitor, one per NODE (all nodes get
    #: monitors, Raptor 7; non-capacity nodes stay 0).
    flow_stats: dict[int, TimedStat] = field(default_factory=dict)
    cap_stats: dict[int, TimedStat] = field(default_factory=dict)
    #: Raw monitored array elements (`NodeFlow[seq]`/`NodeCapacity[seq]`).
    node_flow: dict[int, int] = field(default_factory=dict)
    node_capacity: dict[int, int] = field(default_factory=dict)
    cap_analysis_required: bool = False
    flowing_at_max_cap: bool = False
    last_flow: int = -1
    #: `rpt_*_capacity.txt` lines (time / start-node flow / start-node
    #: capacity), deduplicated on flow change.
    cap_file_lines: list[str] = field(default_factory=list)
    #: The nodes whose `NodeFlow`/`NodeCapacity` `analyze_capacity` assigns:
    #: the capacity node group plus the start and end nodes. Set by its first
    #: call. Every other node's values stay 0 for the whole run.
    live_ids: list[int] | None = None
    #: From `reset_for_new_run` until the first `touch_capacity_monitors`:
    #: the first assignment after a reset sets a monitor's Min/Max, so that
    #: touch still covers every node.
    monitors_fresh: bool = True


def reset_for_new_run(
    state: CapacityState,
    nodes_by_id: dict[int, RBDNode],
    sim_time: float = 0.0,
) -> None:
    """Per-run monitor recreation."""
    state.flow_stats.clear()
    state.cap_stats.clear()
    state.node_flow.clear()
    state.node_capacity.clear()
    state.last_flow = -1
    state.cap_analysis_required = False
    state.flowing_at_max_cap = False
    state.cap_file_lines.clear()
    state.monitors_fresh = True
    for node in nodes_by_id.values():
        flow_stat = TimedStat()
        cap_stat = TimedStat()
        flow_stat.reset(0, sim_time)
        cap_stat.reset(0, sim_time)
        state.flow_stats[node.id] = flow_stat
        state.cap_stats[node.id] = cap_stat
        state.node_flow[node.id] = 0
        state.node_capacity[node.id] = 0


# --- DistributeFlow ---------------------------------------------------------


def _set_actual_flow(link: Link, value: int) -> None:
    """Port of `LinkObj.SetActualFlow` -- the
    only mutation path that keeps `nom_reached`/`max_reached` in sync."""
    link.actual_flow = value
    if link.actual_flow < link.sim_nom_flow:
        link.nom_reached = False
        link.max_reached = False
    elif link.actual_flow < link.sim_max_flow:
        link.nom_reached = True
        link.max_reached = False
    else:
        link.nom_reached = True
        link.max_reached = True


def _reset_flows(
    cap_node_group: list[RBDNode],
    cap_link_ids: list[int],
    links_by_id: dict[int, Link],
) -> None:
    """AnalyzeSystem's analysis-head reset:
    zero every cap link's actualFlow (via SetActualFlow, keeping the
    reached flags in sync) and pathFull; zero every cap node's nodeFlow
    (`ResetNodeFlow`)."""
    for lid in cap_link_ids:
        link = links_by_id[lid]
        _set_actual_flow(link, 0)
        link.path_full = False
    for node in cap_node_group:
        node.node_flow = 0


def distribute_flow(
    node: RBDNode,
    sent: int,
    nodes_by_id: dict[int, RBDNode],
    links_by_id: dict[int, Link],
) -> int:
    """Port of `RBDNodeObj.DistributeFlow`.
    Returns `rejected` (the unroutable amount); accumulates
    `node.node_flow += sent - rejected`. Two phases -- fill every
    priority group to NOMINAL, then spill into MAX -- each walking the
    sorted cap arrays group-by-group (equal `sim_cap_priority` = one
    group). Within a group, amounts split evenly via sequential Modsim
    ROUND division. A group whose members share one target node takes
    the source's `commonNode` branch (ONE recursive call for the common
    total, rejections handed back as per-link shares); otherwise each
    link recurses individually. A rejecting target marks its feeders
    `path_full` for the remainder of THIS analysis."""
    amount_left = sent
    all_noms_filled = False
    all_max_filled = False
    next_group_start = 1
    cap_out = node.cap_connect_out

    # Pre-check: a non-Start node whose ENTIRE
    # EFPA out-boundary is blocked takes no flow at all. (Note the source
    # compares counts directly -- an empty EFPA boundary also "matches".)
    if node.type_node != 1:
        downstream_blockage = 0
        for target_id, link_id in zip(node.propagates_to_node_ids, node.propagates_to_link_ids):
            link = links_by_id[link_id]
            target = nodes_by_id[target_id]
            if link.status != LinkStatus.UP or target.status not in _UP_LIKE_NODE:
                downstream_blockage += 1
        if downstream_blockage == len(node.propagates_to_node_ids):
            all_noms_filled = True
            all_max_filled = True

    def _link_available(link: Link, nominal_phase: bool) -> bool:
        return (
            link.status == LinkStatus.UP
            and not (link.nom_reached if nominal_phase else link.max_reached)
            and not link.path_full
        )

    def _mirror(link: Link, value: int) -> None:
        """depLink/siblings actual-flow mirroring. The common branch gates on
        GraphicsOutput (never true here -- skipped, matching the source); the
        non-common branch is unconditional.

        Routes through `_set_actual_flow`, not a bare assignment: the source
        mirrors with `ASK tempLink TO SetActualFlow(linkFlowValue)`,
       and `SetActualFlow` is the only thing that keeps
        `nom_reached`/`max_reached` in step with `actual_flow`.
        Assigning the field directly leaves a mirrored link permanently
        reading `nom_reached=False`, so the nominal fill phase keeps treating
        it as available and pushes it to its maximum.
        """
        if link.dep_link > 0:
            temp_link = links_by_id.get(link.dep_link)
            if temp_link is not None:
                _set_actual_flow(temp_link, value)
                if temp_link.has_siblings:
                    for sid in temp_link.siblings_group:
                        sibling = links_by_id.get(sid)
                        if sibling is not None:
                            _set_actual_flow(sibling, value)

    def _scan_group(group_start: int, nominal_phase: bool):
        """The shared group-finding WHILE ( nom
        max). Returns `(group_end, next_group_start_or_None, num_still_
        avail, available, common_node, temp_node_id)`. `temp_node_id` is
        the LAST status-available member's target -- the source's own
        carryover (`tempNode` assigned before the target-up check),
        load-bearing in the commonNode branch."""
        group_end = group_start
        next_group_start_result = None
        num_still_avail = 0
        available = False
        group_priority_set = False
        group_priority = 0
        first_node = 0
        common_node = True
        temp_node_id = 0
        j = 0
        if group_start <= cap_out:
            while True:
                link = links_by_id[node.cap_connect_path[group_start + j - 1]]
                if group_priority_set and link.sim_cap_priority > group_priority:
                    next_group_start_result = group_start + j
                    break
                group_end = group_start + j
                if _link_available(link, nominal_phase):
                    target = nodes_by_id[node.cap_connect_to[group_start + j - 1]]
                    if target.status in _UP_LIKE_NODE:
                        num_still_avail += 1
                        available = True
                        ceiling = link.sim_nom_flow if nominal_phase else link.sim_max_flow
                        link.avail_flow = ceiling - link.actual_flow
                        if not group_priority_set:
                            group_priority = link.sim_cap_priority
                            group_priority_set = True
                            first_node = target.id
                        elif target.id != first_node:
                            common_node = False
                    elif target.id != first_node:
                        # Port of source's own ELSE: a
                        # status-available link whose target is not up-like
                        # still rejects the common-node hypothesis.
                        common_node = False
                    temp_node_id = target.id
                if (group_start + j) >= cap_out:
                    break
                j += 1
        return group_end, next_group_start_result, num_still_avail, available, common_node, temp_node_id

    def _fill_phase(nominal_phase: bool) -> None:
        nonlocal amount_left, all_noms_filled, all_max_filled, next_group_start
        next_group_start = 1
        while amount_left > 0 and not (all_noms_filled if nominal_phase else all_max_filled):
            group_start = next_group_start
            group_end, next_scan, num_still_avail, avail, common_node, temp_node_id = _scan_group(
                group_start, nominal_phase
            )
            if next_scan is not None:
                next_group_start = next_scan
            reached_end = group_end >= cap_out
            while amount_left > 0 and avail:
                avail = False
                k = num_still_avail
                if common_node:
                    # Raptor 7 (nom) (max).
                    common_total = 0
                    amount_to_distribute = amount_left
                    sent_members = []
                    for j in range(group_start, group_end + 1):
                        link = links_by_id[node.cap_connect_path[j - 1]]
                        if _link_available(link, nominal_phase):
                            share = modsim_round(amount_to_distribute / k) if k > 0 else 0
                            amount_to_distribute -= share
                            k -= 1
                            link.send_amount = min(share, link.avail_flow)
                            common_total += link.send_amount
                            sent_members.append(link)
                    temp_node = nodes_by_id[temp_node_id]
                    node_is_full = False
                    return_amount = 0
                    if temp_node.type_node != 3:
                        return_amount = distribute_flow(temp_node, common_total, nodes_by_id, links_by_id)
                        amount_left = amount_left - common_total + return_amount
                    else:
                        amount_left -= common_total
                    if return_amount > 0:
                        node_is_full = True
                    k = num_still_avail
                    num_still_avail = 0
                    for link in sent_members:
                        if k > 0:
                            return_share = modsim_round(return_amount / k)
                            k -= 1
                            return_amount -= return_share
                        else:
                            # The source would divide by zero here; unreachable
                            # for a genuinely-common group (uniform target =>
                            # uniform up-ness), guarded for safety only.
                            return_share = 0
                        if node_is_full:
                            link.path_full = True
                        # Source's common branch sets the link itself and gates the
                        # depLink/siblings mirror on `GraphicsOutput`
                        # (nominal), which is never true in a report
                        # run, so there is no mirror here in either phase.
                        _set_actual_flow(link, link.send_amount - return_share + link.actual_flow)
                        reached = link.nom_reached if nominal_phase else link.max_reached
                        if not reached and not link.path_full:
                            avail = True
                            num_still_avail += 1
                            ceiling = link.sim_nom_flow if nominal_phase else link.sim_max_flow
                            link.avail_flow = ceiling - link.actual_flow
                else:
                    # Raptor 7 (nom) (max).
                    num_still_avail = 0
                    for j in range(group_start, group_end + 1):
                        if amount_left <= 0:
                            break
                        link = links_by_id[node.cap_connect_path[j - 1]]
                        if not _link_available(link, nominal_phase):
                            continue
                        target = nodes_by_id[node.cap_connect_to[j - 1]]
                        if target.status not in _UP_LIKE_NODE:
                            continue
                        return_amount = 0
                        share = modsim_round(amount_left / k) if k > 0 else 0
                        k -= 1
                        link.send_amount = min(share, link.avail_flow)
                        if target.type_node != 3:
                            return_amount = distribute_flow(target, link.send_amount, nodes_by_id, links_by_id)
                            amount_left = amount_left - link.send_amount + return_amount
                            if return_amount > 0:
                                link.path_full = True
                        else:
                            amount_left -= link.send_amount
                        # `linkFlowValue` ( nominal max).
                        link_flow_value = link.send_amount - return_amount + link.actual_flow
                        if nominal_phase or link.dep_link <= 0:
                            # Nominal sets the link unconditionally and then also mirrors
                            # Max sets the link only in the
                            # ELSE of `IF link.depLink>0` : a
                            # link with a depLink has its flow written to the
                            # depLink and siblings instead of to itself.
                            _set_actual_flow(link, link_flow_value)
                        _mirror(link, link_flow_value)
                        reached = link.nom_reached if nominal_phase else link.max_reached
                        if not reached and not link.path_full:
                            avail = True
                            num_still_avail += 1
                            ceiling = link.sim_nom_flow if nominal_phase else link.sim_max_flow
                            link.avail_flow = ceiling - link.actual_flow
            if reached_end:
                if nominal_phase:
                    all_noms_filled = True
                else:
                    all_max_filled = True

    _fill_phase(nominal_phase=True)
    _fill_phase(nominal_phase=False)

    rejected = amount_left
    node.node_flow += sent - rejected
    return rejected


def determine_max_capacity(
    start_node: RBDNode,
    cap_node_group: list[RBDNode],
    cap_link_ids: list[int],
    nodes_by_id: dict[int, RBDNode],
    links_by_id: dict[int, Link],
) -> int:
    """Port of `DetermineMaxCapacity`: probe the
    (currently all-up) network with an unbounded offer, return what
    survives. Flow state fully restored afterwards. Called once per run
   to set `FlowingAtMaxCap`."""
    _reset_flows(cap_node_group, cap_link_ids, links_by_id)
    lost = distribute_flow(start_node, INT_FLOW_MAX, nodes_by_id, links_by_id)
    max_cap = INT_FLOW_MAX - lost
    _reset_flows(cap_node_group, cap_link_ids, links_by_id)
    return max_cap


def touch_capacity_monitors(state: CapacityState, sim_time: float) -> None:
    """Record the implicit `LMONITORED` values of `NodeFlow`/`NodeCapacity`:
    every assignment splits the timed monitor's integral at `sim_time`, even
    when the value is unchanged. The Raptor 7 engine assigns them at the end of
    every state-changing instant, not only when a new flow analysis was
    required, which matters for the cross-run variance in the aggregate
    report.

    Once every monitor has had its first assignment, only `live_ids` are
    touched. Any other node's monitor holds 0 with Min/Max already 0, and
    assigning 0 to it again changes nothing: the integral gains `0 * dt`,
    exactly +0.0, and the split point is never read while the value is 0.
    On a model with 901 nodes and a 5-node capacity group the full touch
    was about a quarter of a replication's time."""
    live = state.live_ids
    if live is None or state.monitors_fresh:
        update_many(state.flow_stats, state.node_flow, sim_time)
        update_many(state.cap_stats, state.node_capacity, sim_time)
        state.monitors_fresh = False
        return
    update_many(state.flow_stats, state.node_flow, sim_time, live)
    update_many(state.cap_stats, state.node_capacity, sim_time, live)


def analyze_capacity(
    state: CapacityState,
    new_sys_status_red: bool,
    sim_time: float,
    *,
    cap_node_group: list[RBDNode],
    cap_link_ids: list[int],
    nodes_by_id: dict[int, RBDNode],
    links_by_id: dict[int, Link],
    start_node: RBDNode,
    end_node: RBDNode,
    flow_generated: int,
    flowing_at_max_cap: bool,
) -> None:
    """Port of `AnalyzeSystem`'s capacity half,
    minus everything GraphicsOutput/FEVmode-gated. Callers own the
    `CapAnalysisRequired` flag lifecycle (see orchestrator's `_check_
    system_status`); this IS the flag-gated body plus the CapFile writer.
    Updates `state`'s timed monitors exactly where the source assigns the
    `LMONITORED` arrays."""
    if state.live_ids is None:
        state.live_ids = list(dict.fromkeys(
            [node.id for node in cap_node_group] + [start_node.id, end_node.id]))
    _reset_flows(cap_node_group, cap_link_ids, links_by_id)
    if not new_sys_status_red:
        if not flowing_at_max_cap:
            # Probe pass: an unbounded offer records
            # the SYSTEM's max deliverable as the START node's capacity.
            probe_lost = distribute_flow(start_node, INT_FLOW_MAX, nodes_by_id, links_by_id)
            state.node_capacity[start_node.id] = INT_FLOW_MAX - probe_lost
            _reset_flows(cap_node_group, cap_link_ids, links_by_id)
        lost_flow = distribute_flow(start_node, flow_generated, nodes_by_id, links_by_id)
        if flowing_at_max_cap:
            state.node_capacity[start_node.id] = flow_generated - lost_flow
    else:
        state.node_capacity[start_node.id] = 0

    # Recording loop.
    for node in cap_node_group:
        if node.id != end_node.id:
            state.node_flow[node.id] = node.node_flow
        current_cap = 0
        if node.status in _UP_LIKE_NODE:
            if node.full_flow:
                current_cap = INT_FLOW_MAX
            else:
                for link_id in node.in_paths_array:
                    if links_by_id[link_id].status == LinkStatus.UP:
                        current_cap += links_by_id[link_id].sim_max_flow
        else:
            current_cap = 0
        if node.id != start_node.id:
            state.node_capacity[node.id] = current_cap
    state.node_flow[end_node.id] = state.node_flow[start_node.id]

    touch_capacity_monitors(state, sim_time)

    # CapFile writer -- deduplicated on flow change.
    if state.node_flow[start_node.id] != state.last_flow:
        line = (
            _pad(real_to_str(sim_time), 20) + "  "
            + _pad(str(state.node_flow[start_node.id]), 9) + "  "
            + _pad(str(state.node_capacity[start_node.id]), 9)
        )
        state.cap_file_lines.append(line)
        state.last_flow = state.node_flow[start_node.id]


# --- per-run report snapshot (DoEndOfRunUpdate stat extraction) -------------


@dataclass
class CapacityNodeStats:
    name: str
    node_id: int
    min_flow: int
    ave_flow: float
    max_flow: int
    min_cap: int
    ave_cap: float
    max_cap: int
    usage: float
    capability: float
    full_flow: bool


@dataclass
class CapacityRunReport:
    """Raptor 7's per-run extraction, snapshotted at run
    end: per-capNode stats off the timed monitors, `UnitsProduced`,
    `AveLostFlow`, and the type-1 (Start) node's index."""

    units_produced: float
    ave_lost_flow: float
    flow_generated: int
    system_node_id: int
    #: node id -> stats, for every capNode (Start included; the report's
    #: NodeCapacity TABLE skips type 1, but SystemCapacity reads it).
    nodes: dict[int, CapacityNodeStats]


def build_capacity_run_report(
    state: CapacityState,
    cap_node_group: list[RBDNode],
    end_time: float,
    flow_generated: int,
    stats_start_time: float,
) -> CapacityRunReport:
    """Port of Raptor 7. `Usage:=AveFlow/AveCap`,
    `Capability:=AveCap/MaxCap`, both 0.0 when their divisor is 0
    (matching the source's own guards)."""
    system_node = next(n for n in cap_node_group if n.type_node == 1)
    nodes: dict[int, CapacityNodeStats] = {}
    for node in cap_node_group:
        flow_stat = state.flow_stats[node.id]
        cap_stat = state.cap_stats[node.id]
        ave_flow = flow_stat.mean_at(end_time)
        min_flow = int(flow_stat.observed_minimum)
        max_flow = int(flow_stat.observed_maximum)
        ave_cap = cap_stat.mean_at(end_time)
        min_cap = int(cap_stat.observed_minimum)
        max_cap = int(cap_stat.observed_maximum)
        usage = ave_flow / ave_cap if ave_cap != 0.0 else 0.0
        capability = ave_cap / float(max_cap) if max_cap != 0 else 0.0
        nodes[node.id] = CapacityNodeStats(
            name=node.report_name, node_id=node.id,
            min_flow=min_flow, ave_flow=ave_flow, max_flow=max_flow,
            min_cap=min_cap, ave_cap=ave_cap, max_cap=max_cap,
            usage=usage, capability=capability, full_flow=node.full_flow,
        )
    ave_flow_sys = nodes[system_node.id].ave_flow
    return CapacityRunReport(
        units_produced=ave_flow_sys * (end_time - stats_start_time),
        ave_lost_flow=float(flow_generated) - ave_flow_sys,
        flow_generated=flow_generated,
        system_node_id=system_node.id,
        nodes=nodes,
    )
