"""Port of `RBDNodeObj.ReconfigureNode` (both halves) and `ChangeNodeState`.

Source: Raptor 7's node object.

`ReconfigureNode` does two things: (1) update one incoming link's status from
the `RunChange`/`FailChange`/etc. deltas passed in, then (2) if that changed
anything (or `inPath=0`), re-aggregate all incoming links to determine the
node's overall status (K-out-of-N logic) and log it. This module ports (2) as
`determine_node_status` and (1) as `reconfigure_node`, which drives (2) using
each node's `path_fail_value`/`path_idle_value`/`path_standby_value`/
`path_pm_value` arrays (indexed in parallel with `in_paths_array`).

Also ports the `typeNode=5` branch of step (2)'s `WriteEvents` call: a
hierarchy's internal exit/"out" node logs `"Hier"`/`hier.name` instead of
`"Node"`/`node.node_name`, same aggregation otherwise -- see
`determine_node_status`'s `hier_name` parameter.

The `coldStandby` `ColdChangeGroup` branch is ported
as the caller-supplied `mark_cold_changed` callback: when given, it replaces
the ordinary K-of-N aggregate for a cold-standby node whenever `node.
cold_standby and not node.higher_cold`. `RBDHierObj.ChangeStatus`/
`ChangeDSIStatus`, the hierarchy coloring and `NumCurrentlyFailed` rollup, are
ported in this module's helpers and driven from `change_node_state`.

Checked against `tests/golden/UniformProbe` and `tests/golden/HierarchyExample`
in `tests/unit/test_nodes_engine.py`.
"""
from __future__ import annotations

from dataclasses import dataclass
from collections.abc import Callable

from raptor.core.engine.cold_standby import set_switch_time
from raptor.core.engine.eventlog import format_event_log_line
from raptor.core.model import (
    ActiveStatus, Link, LinkStatus, NodeStatus, RBDHier, RBDNode)
from raptor.core.rng import RandomSource

#: `NodeStatusType=(AllUp,NodeStandby,Degraded,NodeIdle,NodePM,Down)` --
#: source's own enum ORDER is the SEVERITY order the
#: hierarchy rollups compare with (`IF Status > dsiState`).
_NODE_SEVERITY = {
    NodeStatus.ALL_UP: 0,
    NodeStatus.STANDBY: 1,
    NodeStatus.DEGRADED: 2,
    NodeStatus.IDLE: 3,
    NodeStatus.PM: 4,
    NodeStatus.DOWN: 5,
}

#: The DSI recompute chain's worst-first order (Raptor 7:
#: reds>0 -> Down, oranges -> NodePM, siennas -> NodeIdle, yellows ->
#: Degraded, blues -> NodeStandby, else AllUp).



def _change_dsi_status(hier: RBDHier | None,
                       deltas: dict[NodeStatus, int],
                       hiers_by_id: dict[int, RBDHier]) -> None:
    """Port of `RBDHierObj.ChangeDSIStatus`:
    accumulate ±1 descendant-state counters, recompute `dsi_state`
    (worst non-zero wins), and — for a NESTED hierarchy — roll the
    worse-of-own-and-descendants signal up to the parent hierarchy's
    own ChangeDSIStatus."""
    if hier is None:
        return
    counter_for = {
        NodeStatus.DOWN: "dsi_reds",
        NodeStatus.PM: "dsi_oranges",
        NodeStatus.IDLE: "dsi_siennas",
        NodeStatus.DEGRADED: "dsi_yellows",
        NodeStatus.STANDBY: "dsi_blues",
        NodeStatus.ALL_UP: None,
    }
    counter = counter_for
    for status, delta in deltas.items():
        if delta == 0:
            continue
        attr = counter.get(status)
        if attr:
            setattr(hier, attr, getattr(hier, attr) + delta)
    # dsiState := worst non-zero counter (reds > oranges > siennas >
    # yellows > blues, else AllUp)
    if hier.dsi_reds > 0:
        hier.dsi_state = NodeStatus.DOWN
    elif hier.dsi_oranges > 0:
        hier.dsi_state = NodeStatus.PM
    elif hier.dsi_siennas > 0:
        hier.dsi_state = NodeStatus.IDLE
    elif hier.dsi_yellows > 0:
        hier.dsi_state = NodeStatus.DEGRADED
    elif hier.dsi_blues > 0:
        hier.dsi_state = NodeStatus.STANDBY
    else:
        hier.dsi_state = NodeStatus.ALL_UP
    # Nested: the parent's DSIsignal = worse of (own Status, dsiState)
    if hier.parent_id > 0:
        parent = hiers_by_id.get(hier.parent_id)
        if parent is not None:
            old_signal = hier.dsi_signal
            hier.dsi_signal = hier.status if (
                _NODE_SEVERITY[hier.status]
                > _NODE_SEVERITY[hier.dsi_state]) else hier.dsi_state
            if hier.dsi_signal != old_signal:
                _change_dsi_status(
                    parent, {old_signal: -1, hier.dsi_signal: 1},
                    hiers_by_id)


def _hier_change_status(hier: RBDHier | None, new_status: NodeStatus,
                        hiers_by_id: dict[int, RBDHier]) -> None:
    """Port of `RBDHierObj.ChangeStatus`: the
    hierarchy's OWN status := the OUT node's new status (drives the
    oval's live colour); a NESTED hierarchy keeps its parent's
    DSIsignal = worse of (own Status, dsiState) and rolls changes up
    (IncNumFailed/DecNumFailed + ChangeDSIStatus on the parent).

    The hierarchy's boundary links are deliberately not driven here:
    `ReconfigureNode` only recolours a node's own in_paths, and driving them
    engine-side perturbed the cascade ordering. The GUI colours boundary lines
    from `hier.status` at paint time instead (update_live_states's hier loop).
    """
    if hier is None:
        return
    hier.status = new_status
    if hier.parent_id > 0:
        parent = hiers_by_id.get(hier.parent_id)
        if parent is None:
            return
        old_signal = hier.dsi_signal
        hier.dsi_signal = hier.status if (
            _NODE_SEVERITY[hier.status]
            > _NODE_SEVERITY[hier.dsi_state]) else hier.dsi_state
        if hier.dsi_signal != old_signal:
            _change_dsi_status(parent,
                               {old_signal: -1, hier.dsi_signal: 1},
                               hiers_by_id)
_STATUS_STRING = {
    NodeStatus.DOWN: "Bad",
    NodeStatus.ALL_UP: "Good",
    NodeStatus.DEGRADED: "Degraded",
    NodeStatus.STANDBY: "Standby",
    NodeStatus.IDLE: "Idle",
    NodeStatus.PM: "NodePM",
}

#: link.Status -> (bucket incremented, linkString suffix). "switching" links
#: (autoInProgress/manualInProgress while nominally LinkUp) count as a
#: failure instead, per the source's special-case in the LinkUp branch.
_LINK_SUFFIX = {
    LinkStatus.UP: ("runs", "_ur"),
    LinkStatus.DOWN: ("fails", "_df"),
    LinkStatus.IDLE: ("idles", "_di"),
    LinkStatus.PM: ("pms", "_dp"),
    LinkStatus.DONE: ("fails", "_dd"),
}


def set_link_status_mirrored(link: Link, new_status: LinkStatus,
                             links_by_id: dict[int, Link],
                             _seen: set[int] | None = None,
                             on_link_status_change: Callable[[], None] | None = None) -> None:
    """Port of `ChangeLinkStatus`'s own sibling mirror (:
    4323-4326`: `IF hasSiblings: FOREACH sibling IN siblingsGroup: ASK
    sibling TO ChangeLinkStatus(Status)`) together with its capacity hook,
    which the source runs per link -- the sibling's own
    `ChangeLinkStatus` re-evaluates it against the sibling's own prior
    status.

    The sibling groups are the RunEFPA depLink registration (`IAnalyze.
    mod:515-535`, ported in `graph.build_efpa_graph`): every link whose
    EFPA target is a BLOCK/EVENT joins that element's own `downstream_link_id`'s
    sibling group, so when the element's efpa link changes status the
    input-side line -- the link drawn on the block's other side -- shows the
    same colour. Sibling links target block/event objects directly, so they
    are never in any node's `in_paths_array` and no reconfiguration reads
    their status for aggregation; mirroring them is display/flow-mirror state
    only and cannot change any status-line output.

    `on_link_status_change`, when given, is `CapAnalysisRequired`'s setter,
    and is called only for a LinkUp <-> non-LinkUp crossing (Raptor 7:
    4328-4333`: `IF ((priorStatus<>LinkUp) AND (newStatus=LinkUp)) OR
    ((priorStatus=LinkUp) AND (newStatus<>LinkUp))`). A change between two
    non-Up statuses (Standby->PM, PM->Down, ...) leaves the flag alone, so
    real re-analyses the flow one instant later than a naive reading would."""
    if _seen is None:
        _seen = set()
    if link.object_num in _seen:
        return
    _seen.add(link.object_num)
    prior_status = link.status
    link.status = new_status
    # `ChangeLinkStatus`'s `IF SBlink ... IF NOT node.priorityReturn: IF
    # priorStatus=LinkUp AND Status IN (Down, PM, Done): ReOrderColds(Id)`
    cold_node = link.cold_node
    if (link.sb_link and cold_node is not None and not cold_node.priority_return
            and prior_status == LinkStatus.UP
            and new_status in (LinkStatus.DOWN, LinkStatus.PM, LinkStatus.DONE)):
        from raptor.core.engine.cold_standby import reorder_colds
        reorder_colds(cold_node, link.object_num)
    for sibling_id in link.siblings_group:
        sibling = links_by_id.get(sibling_id)
        if sibling is not None and sibling is not link:
            set_link_status_mirrored(
                sibling, new_status, links_by_id, _seen, on_link_status_change,
            )
    if on_link_status_change is not None:
        if ((prior_status != LinkStatus.UP and new_status == LinkStatus.UP)
                or (prior_status == LinkStatus.UP and new_status != LinkStatus.UP)):
            on_link_status_change()


@dataclass
class IncomingLink:
    """One entry of a node's incoming-link set, as `determine_node_status`
    needs it — deliberately not the full `Link` dataclass, since only
    these fields matter for status aggregation."""

    path_id: int  # the link's object_num, used verbatim in the log string
    status: LinkStatus
    switching: bool = False  # link.switchStatus in ("autoInProgress","manualInProgress")


@dataclass
class NodeReconfigureResult:
    new_status: NodeStatus
    status_string: str
    num_good_paths: int
    link_string: str
    log_line: str | None


def determine_node_status(
    node: RBDNode,
    incoming_links: list[IncomingLink],
    good_paths_required: int,
    sim_time: float,
    *,
    events_file: bool = False,
    hier_name: str | None = None,
) -> NodeReconfigureResult:
    """Port of `ReconfigureNode`'s link-aggregation/status-determination
    block. See module docstring for what's
    deliberately not included.

    `hier_name`: pass the PARENT hierarchy's `RBDHier.name` whenever
    `node.type_node == 5` (a hierarchy's internal exit/"out" node) — the
    source's `IF typeNode=5` branch logs `"Hier"`/`hier.name`
    instead of `"Node"`/`node.node_name` for exactly this one node type,
    same aggregation format string otherwise. Leave `None` for every other
    node type (the ordinary `"Node"` line)."""
    runs = fails = idles = standbys = pms = 0
    link_string = ""
    for link in incoming_links:
        if link.status == LinkStatus.UP and link.switching:
            fails += 1
            link_string += f"_dsw{link.path_id}"
        elif link.status == LinkStatus.STANDBY and link.switching:
            fails += 1
            link_string += f"_dsw{link.path_id}"
        elif link.status == LinkStatus.STANDBY:
            standbys += 1
            link_string += f"_us{link.path_id}"
        else:
            bucket, suffix = _LINK_SUFFIX[link.status]
            if bucket == "runs":
                runs += 1
            elif bucket == "fails":
                fails += 1
            elif bucket == "idles":
                idles += 1
            elif bucket == "pms":
                pms += 1
            link_string += f"{suffix}{link.path_id}"

    resolved_in_link_count = len(incoming_links)
    num_good_paths = runs

    if good_paths_required > resolved_in_link_count:
        new_status, status_string = NodeStatus.DOWN, "Bad"
    elif runs == resolved_in_link_count:
        new_status, status_string = NodeStatus.ALL_UP, "Good"
    elif runs >= good_paths_required:
        if runs + standbys == resolved_in_link_count:
            new_status, status_string = NodeStatus.ALL_UP, "Good"
        else:
            new_status, status_string = NodeStatus.DEGRADED, "Degraded"
    elif runs + standbys >= good_paths_required:
        new_status, status_string = NodeStatus.STANDBY, "Standby"
    elif idles + runs + standbys >= good_paths_required:
        new_status, status_string = NodeStatus.IDLE, "Idle"
    elif pms + idles + runs + standbys >= good_paths_required:
        new_status, status_string = NodeStatus.PM, "NodePM"
    elif fails > 0:
        new_status, status_string = NodeStatus.DOWN, "Bad"
    else:
        raise ValueError("Error in node status logic!")

    log_line = None
    if events_file:
        if node.type_node == 5:
            if hier_name is None:
                raise ValueError("determine_node_status: type_node=5 requires hier_name (parent RBDHier.name)")
            kind, label = "Hier", hier_name
        else:
            kind, label = "Node", node.report_name
        log_line = format_event_log_line(
            sim_time, kind, label, status_string,
            f"{num_good_paths}-{good_paths_required}/{resolved_in_link_count}{link_string}",
        )

    return NodeReconfigureResult(new_status, status_string, num_good_paths, link_string, log_line)


def reconfigure_node(
    node: RBDNode,
    links_by_id: dict[int, Link],
    run_change: int,
    fail_change: int,
    idle_change: int,
    standby_change: int,
    pm_change: int,
    in_path: int,
    sim_time: float,
    *,
    events_file: bool = False,
    hier_name: str | None = None,
    sb_link_rng: RandomSource | None = None,
    on_link_status_change: Callable[[], None] | None = None,
    mark_cold_changed: Callable[[RBDNode], None] | None = None,
    interrupt_switch: Callable[[Link], None] | None = None,
    on_cold_link_down: Callable[[Link], None] | None = None,
) -> NodeReconfigureResult | None:
    """Port of `ReconfigureNode`'s link-status-update half,
   driving into `determine_node_status` for
    the aggregation half exactly like the source falls through into its
    own second block. Returns `None` when the source would take neither
    branch of `IF ((linkStatusChanged) OR (inPath=0)) ...` — i.e. `inPath`
    was nonzero and the affected link's status didn't actually change, so
    no re-aggregation happens and nothing is logged.

    `mark_cold_changed`, if given, is called (instead of computing/logging
    the ordinary aggregate) whenever `node.cold_standby and not
    node.higher_cold` on the same `(linkStatusChanged) OR (inPath=0)`
    condition that would otherwise trigger normal aggregation -- port of the
    source's `ELSIF` (`coldsAffected:=TRUE;
    ColdChangeGroup.Add(SELF)`). Callers that omit it (the default) keep the
    unconditional behavior: this function always computes
    `determine_node_status` regardless of `cold_standby`, and the caller
    discards or gates the result (see this module's docstring).

    `hier_name`: forwarded to `determine_node_status` verbatim -- pass the
    parent `RBDHier.name` when `node.type_node == 5`, see that function's
    docstring.

    `interrupt_switch`, if given, is called with the affected link whenever
    its status genuinely changes AND it currently has an in-progress
    cold-standby switch (`link.switch_status in ("autoInProgress",
    "manualInProgress")`) -- port of `ReconfigureNode`'s `IF
    ((link.switchStatus="autoInProgress") OR (...="manualInProgress")):
    Interrupt(link,"PerformSwitch")`. A block
    feeding a cold-standby node can fail while a switch onto that same link
    is still in flight; the Raptor 7 engine resolves that switch immediately, in
    the same instant, where this port would otherwise let the switch's
    original timer run to completion.

    `node.path_fail_value`/`path_idle_value`/`path_standby_value`/
    `path_pm_value` are extended with zeros as needed to stay in step with
    `node.in_paths_array` -- the source declares these arrays with the same
    bounds as `EconnectIntoNum` up front (implicitly zero-initialized);
    nothing populates them from `.rbd` topology data, so this is the lazy
    equivalent.

    `links_by_id` stands in for the source's `ASK root Child("RBDLink",
    path)` lookups -- caller-owned, mutated in place (each `Link.status` is
    the source's `link.Status`).
    """
    link_status_changed = False

    if in_path != 0:
        if in_path not in node.in_paths_array:
            raise ValueError("Error in ReconfigureNode!")
        array_index = node.in_paths_array.index(in_path)
        for values in (
            node.path_fail_value, node.path_idle_value,
            node.path_standby_value, node.path_pm_value,
        ):
            while len(values) <= array_index:
                values.append(0)

        node.path_fail_value[array_index] += fail_change
        fails = node.path_fail_value[array_index]
        node.path_idle_value[array_index] += idle_change
        idles = node.path_idle_value[array_index]
        node.path_standby_value[array_index] += standby_change
        standbys = node.path_standby_value[array_index]
        node.path_pm_value[array_index] += pm_change
        pms = node.path_pm_value[array_index]

        if fails > 0:
            new_link_status = LinkStatus.DOWN
        elif pms > 0:
            new_link_status = LinkStatus.PM
        elif idles > 0:
            new_link_status = LinkStatus.IDLE
        elif standbys > 0:
            new_link_status = LinkStatus.STANDBY
        else:
            new_link_status = LinkStatus.UP

        link = links_by_id[in_path]
        old_link_status = link.status
        if link.status != new_link_status:
            # Port of ReconfigureNode's ChangeLinkStatus call
            # including ChangeLinkStatus's sibling
            # mirror, so the block/event's input-side line follows too, and
            # its own capacity hook, which that
            # method applies per link -- the sibling's prior status decides
            # its own arm, so the hook rides inside the mirror rather than
            # after it.
            set_link_status_mirrored(
                link, new_link_status, links_by_id, None, on_link_status_change,
            )
            link_status_changed = True
            # Port of ReconfigureNode's in-progress-switch interrupt
            # -- see `interrupt_switch`'s own
            # docstring. Read before calling the callback, matching the
            # source's order (`Interrupt` reads `link.switchStatus`
            # immediately after `ChangeLinkStatus`).
            if (interrupt_switch is not None
                    and link.switch_status in ("autoInProgress", "manualInProgress")):
                interrupt_switch(link)
            # Port of ChangeLinkStatus's SBlink-gated SetSwitchTime call
            #: fires for any link transitioning to
            # Standby (block-to-node cascade, not cold-standby-specific);
            # `sb_link` narrows it to cold-standby-eligible links, see
            # `cold_standby.sort_cold_links`. `FEVmode` is never true in this
            # project's scope.
            if new_link_status == LinkStatus.STANDBY and link.sb_link:
                if sb_link_rng is None:
                    raise ValueError(
                        "reconfigure_node: link.sb_link is True but no sb_link_rng (SysStream[11]) "
                        "was provided -- SetSwitchTime cannot be drawn"
                    )
                set_switch_time(link, sb_link_rng)
            # Port of `ReconfigureNode`'s cold-standby block ("Chuck 8/21/04"): a cold
            # node's link leaving `LinkUp` while its source block is still `Running` -- a block Cut by a phase -- puts
            # that block into Standby with an empty comment. A failing block is never `Running` here.
            if (on_cold_link_down is not None and node.cold_standby and old_link_status == LinkStatus.UP
                    and new_link_status != LinkStatus.UP):
                on_cold_link_down(link)

    if not (link_status_changed or in_path == 0):
        return None

    if mark_cold_changed is not None and node.cold_standby and not node.higher_cold:
        mark_cold_changed(node)
        return None

    incoming_links = [
        IncomingLink(
            path_id=path_id,
            status=links_by_id[path_id].status,
            switching=links_by_id[path_id].switch_status in ("autoInProgress", "manualInProgress"),
        )
        for path_id in node.in_paths_array
    ]
    return determine_node_status(
        node, incoming_links, node.good_paths, sim_time, events_file=events_file, hier_name=hier_name,
    )


@dataclass
class NodeStatistics:
    """Per-node accumulators — the `seqNum`-indexed module-level arrays in Raptor 7 (`NodeGreenTime`/`NodeYellowTime`/`NodeRedTime`/etc.),
    same design choice as `EventStatistics`/`BlockStatistics`."""

    green_time: float = 0.0
    yellow_time: float = 0.0
    red_time: float = 0.0
    blue_time: float = 0.0
    brown_time: float = 0.0
    orange_time: float = 0.0
    #: Weak-link analysis' `nodeMissionBool` / `nodeMCompleted`
    #: Cleared the moment this node goes
    #: Down or NodePM. Unlike the block rule, the
    #: source gates this only on `weakAnalysis`, not on being mid-mission.
    mission_bool: bool = True
    m_completed: int = 0


def _node_change_state_deltas(
    prior_status: NodeStatus,
    status: NodeStatus,
    old_active_status: ActiveStatus,
    new_active_status: ActiveStatus,
) -> tuple[int, int, int, int, int]:
    """Port of `ChangeNodeState`'s `RunChange`/`FailChange`/`IdleChange`/
    `StandbyChange`/`pmChange` computation --
    the node-level analog of `blocks.py`'s `change_block_state` CASE, same
    shape: branch first on the `(oldActiveStatus, newActiveStatus)`
    combination, then on `priorStatus` for the "still Active" case.

    Scope note on source nesting: this computation (and the downstream
    cascade it feeds) lives in `IF NOT runCompleted / IF
    typeNode=3 ... ELSE ...` -- the `ELSE` of the same `IF
    typeNode=3` whose `THEN` branch is `system_status.py`'s
    `end_node_system_up_transition` trigger. The two are mutually exclusive:
    an End node (`typeNode=3`) does the `SystemUp` check and never cascades
    further (it has no downstream, `EFPAConnectOut=0` always); every other
    node type does this cascade computation instead. `runCompleted` itself is
    not modeled, so callers should not invoke this at the final DONE
    transition, matching `block_lifecycle.py`'s behavior of not cascading
    there.
    """
    run = fail = idle = standby = pm = 0
    up_like = (NodeStatus.ALL_UP, NodeStatus.DEGRADED)

    if old_active_status == ActiveStatus.LINKED and new_active_status == ActiveStatus.LINKED:
        pass
    elif old_active_status == ActiveStatus.CUT and new_active_status == ActiveStatus.CUT:
        pass
    elif old_active_status == ActiveStatus.CUT and new_active_status == ActiveStatus.LINKED:
        run, fail = 1, -1
    elif old_active_status == ActiveStatus.LINKED and new_active_status == ActiveStatus.CUT:
        run, fail = -1, 1
    elif old_active_status == ActiveStatus.LINKED:  # now Active
        if status in up_like:
            pass
        elif status == NodeStatus.STANDBY:
            run, standby = -1, 1
        elif status == NodeStatus.IDLE:
            run, idle = -1, 1
        elif status == NodeStatus.PM:
            run, pm = -1, 1
        elif status == NodeStatus.DOWN:
            run, fail = -1, 1
    elif old_active_status == ActiveStatus.CUT:  # now Active
        if status in up_like:
            fail, run = -1, 1
        elif status == NodeStatus.STANDBY:
            fail, standby = -1, 1
        elif status == NodeStatus.IDLE:
            fail, idle = -1, 1
        elif status == NodeStatus.PM:
            fail, pm = -1, 1
        elif status == NodeStatus.DOWN:
            pass
    elif prior_status in up_like:  # were Active
        if new_active_status == ActiveStatus.LINKED:
            pass
        elif new_active_status == ActiveStatus.CUT:
            run, fail = -1, 1
        elif status in up_like:
            pass
        elif status == NodeStatus.STANDBY:
            run, standby = -1, 1
        elif status == NodeStatus.IDLE:
            run, idle = -1, 1
        elif status == NodeStatus.PM:
            run, pm = -1, 1
        elif status == NodeStatus.DOWN:
            run, fail = -1, 1
    elif prior_status == NodeStatus.STANDBY:
        if new_active_status == ActiveStatus.LINKED:
            standby, run = -1, 1
        elif new_active_status == ActiveStatus.CUT:
            standby, fail = -1, 1
        elif status in up_like:
            standby, run = -1, 1
        elif status == NodeStatus.STANDBY:
            pass
        elif status == NodeStatus.IDLE:
            standby, idle = -1, 1
        elif status == NodeStatus.PM:
            standby, pm = -1, 1
        elif status == NodeStatus.DOWN:
            standby, fail = -1, 1
    elif prior_status == NodeStatus.IDLE:
        if new_active_status == ActiveStatus.LINKED:
            idle, run = -1, 1
        elif new_active_status == ActiveStatus.CUT:
            idle, fail = -1, 1
        elif status in up_like:
            idle, run = -1, 1
        elif status == NodeStatus.STANDBY:
            idle, standby = -1, 1
        elif status == NodeStatus.IDLE:
            pass
        elif status == NodeStatus.PM:
            idle, pm = -1, 1
        elif status == NodeStatus.DOWN:
            idle, fail = -1, 1
    elif prior_status == NodeStatus.PM:
        if new_active_status == ActiveStatus.LINKED:
            pm, run = -1, 1
        elif new_active_status == ActiveStatus.CUT:
            pm, fail = -1, 1
        elif status in up_like:
            pm, run = -1, 1
        elif status == NodeStatus.STANDBY:
            pm, standby = -1, 1
        elif status == NodeStatus.IDLE:
            pm, idle = -1, 1
        elif status == NodeStatus.PM:
            pass
        elif status == NodeStatus.DOWN:
            pm, fail = -1, 1
    elif prior_status == NodeStatus.DOWN:
        if new_active_status == ActiveStatus.LINKED:
            fail, run = -1, 1
        elif new_active_status == ActiveStatus.CUT:
            pass
        elif status in up_like:
            fail, run = -1, 1
        elif status == NodeStatus.STANDBY:
            fail, standby = -1, 1
        elif status == NodeStatus.IDLE:
            fail, idle = -1, 1
        elif status == NodeStatus.PM:
            fail, pm = -1, 1
        elif status == NodeStatus.DOWN:
            pass

    return run, fail, idle, standby, pm


def change_node_state(
    node: RBDNode,
    stats: NodeStatistics,
    new_status: NodeStatus,
    new_active_status: ActiveStatus,
    sim_time: float,
    *,
    weak_analysis: bool = False,
    is_start_or_end_node: bool = False,
    hiers_by_id: dict[int, RBDHier] | None = None,
) -> tuple[int, int, int, int, int]:
    """Port of `ChangeNodeState`'s status-assignment, weak-link-time
    bookkeeping, and
    downstream-cascade delta computation (see
    `_node_change_state_deltas`'s docstring for the source-nesting note).
    Returns `(run_change, fail_change, idle_change, standby_change,
    pm_change)` -- same shape as `blocks.py`'s `change_block_state`, for
    `cascade.py`'s `reconfigure_after_node_change` to act on exactly like
    `reconfigure_after_block_change` does for blocks.

    `is_start_or_end_node` corresponds to the source's `typeNode<>1` guard
    (`typeNode=1` is the Start node, excluded from weak-link time tracking) --
    named for what it means rather than the raw type code, since the
    type-code table is not reproduced elsewhere in this module. The source's
    guard is `typeNode<>1`, i.e. everything except type 1 is tracked
    (including End nodes), so pass `is_start_or_end_node=True` only for the
    Start node itself, not both ends.

    The five-branch ladder further down (old/new ActiveStatus -> run/fail
    deltas) is DELIBERATELY repeated in `blocks.py` and `events.py`. It is
    not an oversight: the source carries the identical prologue in three
    separate procedures -- Raptor 7 -- so the
    repetition is what makes each function auditable line-by-line against
    its Raptor 7 original. Only the per-element tails differ, and this
    function renames every variable, so the shared part is not even
    textually greppable. Do not "de-duplicate" this into a helper: that
    would trade a verifiable mapping for an abstraction, with no
    correctness gain.
    """
    prior_status = node.status
    prior_active_status = node.active_status
    node.status = new_status
    node.active_status = new_active_status

    if weak_analysis:
        # Raptor 7 -- Down or NodePM forfeits this node's
        # mission credit. Sits OUTSIDE the `typeNode<>1` guard below in
        # source too, and has no `missionStatus` test of its own.
        if new_status in (NodeStatus.DOWN, NodeStatus.PM):
            stats.mission_bool = False
    if weak_analysis and not is_start_or_end_node:
        status_time = sim_time - node.state_start_time
        if prior_status == NodeStatus.ALL_UP:
            stats.green_time += status_time
        elif prior_status == NodeStatus.DEGRADED:
            stats.yellow_time += status_time
        elif prior_status == NodeStatus.DOWN:
            stats.red_time += status_time
            # Raptor 7 -- a hierarchy's Out node (typeNode=5)
            # leaving `Down` increments `hierDE`.
            if node.type_node == 5 and hiers_by_id is not None:
                _h = hiers_by_id.get(node.parent_id)
                if _h is not None:
                    _h.down_event_count += 1
        elif prior_status == NodeStatus.STANDBY:
            stats.blue_time += status_time
        elif prior_status == NodeStatus.IDLE:
            stats.brown_time += status_time
        elif prior_status == NodeStatus.PM:
            stats.orange_time += status_time
            # Raptor 7 -- leaving `NodePM` also counts.
            if node.type_node == 5 and hiers_by_id is not None:
                _h = hiers_by_id.get(node.parent_id)
                if _h is not None:
                    _h.down_event_count += 1

    node.state_start_time = sim_time

    if node.type_node == 5 and hiers_by_id is not None:
        # Live hierarchy rollups (Raptor 7, inside
        # ChangeNodeState's typeNode=5 branch): the OUT node's status change
        # drives the parent hierarchy's oval colour (`RBDHierObj.ChangeStatus`)
        # and its Descendant Status Indicator dot (`ChangeDSIStatus`,
        # Raptor 7).
        _hier_change_status(hiers_by_id.get(node.parent_id), new_status,
                            hiers_by_id)
        if prior_status != new_status:
            _change_dsi_status(hiers_by_id.get(node.parent_id),
                               {prior_status: -1, new_status: 1},
                               hiers_by_id)

    return _node_change_state_deltas(prior_status, new_status, prior_active_status, new_active_status)
