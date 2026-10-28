"""Port of `RBDNodeObj`'s cold-standby switching group: `IdAndSortColds`
(link priority sort) and `RunColdLogic`
(the K-of-N group-switching engine).

Scope is limited to the configurations exercised by the checked-in fixtures;
`RunColdLogic` is ~470 lines in source and covers phased/hierarchical/
multi-switching-technology setups with no fixture here. Ported:

- Kickoff redistribution: every link starts `"ready"`
  (`Link.SetToInitValues`) and every candidate block
  has already Initialized `Running`. With `readyPaths = EconnectIntoNum >=
  RunsRequired`, `RunColdLogic` walks `coldLinks` in priority order, keeps the
  first `RunsRequired`, and switches every remaining `RBDBlock`-sourced one to
  `Standby`/`"Too_many_on"`. The `SimTime>0.0` interrupt block is skipped,
  matching the source.
- Mid-run switch-off/switch-on: an `RBDBlock`-sourced `"ready"` link can lose
  its slot (Up -> Standby, `"Too_many_on"`, also interrupting the block's own
  suspended `Run` process with `"Switched_Off"` when `SimTime>0.0`) or gain
  one back (Standby -> Running, `"Prior=Standby"`, interrupted with
  `"Switched_On"`) within the same per-link pass, matching the source's
  single-loop `IF (LinkUp) / ELSIF (LinkStandby)` structure. Both interrupt
  reasons are handled in `block_lifecycle.block_run_process` as plain
  `returnToTop`-style continues; the block recomputes `CombinedStress` when it
  is next scheduled.
- The node-status recomputation that follows, a
  separate aggregation from `nodes.determine_node_status` with its own
  `unavailable`/`switching` path bucketing.
- The async switch ("not enough ready paths, initiate a new switch") branch:
  `Link.SBlink`/`SetSwitchTime` (`set_switch_time`, drawing from
  `SysStream[11]`) resolve a link's `switch_status` to `"auto"`/`"manual"`/
  `"none"`; `_prep_for_switch` + `perform_switch_process` port `PrepForSwitch`/
  `TELL link TO PerformSwitch`, an async timer that
  waits `switch_time`, then resolves to `"ready"` and re-triggers
  `RunColdLogic`. Scoped to `checkAutosFirst=True` with a single `"auto"`-
  status candidate.
- `RBDNode`-sourced cold links: a demoted/promoted `RBDNode` candidate gets no
  direct state change here, only `SetHigherCold`+`BackFlowDependencies`
  (`NodeStandby` on switch-off, `AllUp` on switch-on). Those dispatches are
  guarded and raise if the source node genuinely has a dependent.

Also ported since: `"too many switching"` cancellation, manual-only and `"none"`-status
switching (`Switch_Failed`), the phased `kMax` clamp, `IdAndSortColds`'s
non-stable swapping sort, and `RBDEvent`-sourced cold links (their arms are the source's no-ops:
`SetStuckOn` has no reader). A candidate that depends on another candidate of the same cold node
crashes Raptor 7 and is refused (`model_checks.py`).

Still refuses (`NotImplementedError` rather than guessing): `PrepForSwitch` on a node-sourced or
event-sourced link, and a `BackFlowDependencies` on a node candidate that genuinely has a dependent.

Checked byte-for-byte against `tests/golden/HierarchyExample` (kickoff
redistribution, failure and recovery) and `tests/golden/PieFacility2`
(`RBDNode`-sourced candidates).
"""
from __future__ import annotations

from collections.abc import Callable

from raptor.core.engine.dependency import resolve_dependency_node_id
from raptor.core.engine.eventlog import format_event_log_line
from raptor.core.model import (
    BlockStatus,
    Link,
    LinkStatus,
    NodeStatus,
    RBDBlock,
    RBDHier,
    RBDNode,
)
from raptor.core.rng import Distribution, RandomSource
from raptor.core.scheduler import Process, Wake
from raptor.core.engine.sim_details import tag_next_draw
from raptor.core.engine.system_status import SystemState
from raptor.core.engine.errors import EngineInvariantError
from raptor.core.engine.interrupts import InterruptReason


def _src_ref(link) -> str:
    """The type of a link's source as source's cold logic reads it
    (`EconnectFRef`): a hierarchy is its Out NODE. Links built by hand in
    unit tests have no effective fields, so fall back to the raw ones."""
    return link.resolved_from_kind or link.connect_f_ref


def _src_id(link) -> int:
    """The id of the link's source, resolved the same way (`EconnectFromId`)."""
    return link.resolved_from_id if link.resolved_from_kind else link.connect_from_id



class NoSwitchTime:
    """Stands in for `SysStream[11]` in Failure Effects View: `SetSwitchTime` is
    skipped there (`IF (Status=LinkStandby) AND (NOT FEVmode)`),
    so a link keeps `switchStatus "ready"` and a switch is immediate."""

    def draw_number(self, *a, **k):               # pragma: no cover - never reached
        raise RuntimeError("Failure Effects View draws no random numbers")


def set_switch_time(link: Link, rng: RandomSource) -> None:
    """Port of `Link.SetSwitchTime`: draw from
    `SysStream[11]` and resolve the link's `switch_status` to
    `"auto"`/`"manual"`/`"none"` from its `autoSwitchProb`/switch times.
    `rng` must be that same shared stream (`driver.make_shared_streams`'s
    `sys_streams[11]`), not a per-link/per-block one.

    Called whenever a link genuinely transitions to `LinkStandby`: the
    source gates on `Link.SBlink`, and this project narrows that gate to
    "the link is cold-standby-eligible" (`link.sb_link`, set by
    `sort_cold_links`), since `SBlink` and cold-standby membership coincide in
    every checked-in fixture.
    """
    if isinstance(rng, NoSwitchTime):
        return
    if link.auto_switch_probability == 1.0:
        should_auto = True
    elif link.auto_switch_probability == 0.0:
        should_auto = False
    else:
        # The /sd trace names this draw `link <objectNum>` (its `DrawNumber`
        # call site's `gettingDraw` argument), not by the shared stream.
        # Without the tag a phased fixture's stream-11 wrapper reports
        # whichever phase last drew from the same stream.
        tag_next_draw(rng, f"link {link.object_num}")
        draw = rng.draw_number(Distribution.UNIFORM_REAL, [0.0, 1.0])
        should_auto = link.auto_switch_probability > draw
    if should_auto:
        link.switch_time = link.auto_switch_time
        link.switch_status = "auto" if link.switch_time > 0.0 else "ready"
    else:
        if link.manual_switch_time == -1.0:
            link.switch_time = -1.0
            link.switch_status = "none"
        elif link.manual_switch_time > 0.0:
            link.switch_time = link.manual_switch_time
            link.switch_status = "manual"
        else:
            link.switch_time = link.manual_switch_time
            link.switch_status = "ready"

#: Signature for the caller-supplied full-transition callback -- same
#: shape as `dependency.TransitionFn`, reused here so `run_cold_logic`
#: can drive a switched-off block through the SAME `change_block_state`+
#: cascade pipeline any other top-level block transition uses (e.g.
#: `orchestrator._Cascade.make_transition`).
TransitionFn = Callable[[RBDBlock, BlockStatus, str, float], None]

#: Signature for the caller-supplied interrupt callback -- same shape as
#: `dependency.InduceFn`, wakes a block's own suspended `block_run_
#: process` generator with the given reason (`"Switched_On"`/`"Switched_
#: Off"`) AFTER `transition` has already applied the state change, same
#: ordering `_dispatch_dependency`/`_induce_dependency` already use.
InterruptFn = Callable[[RBDBlock, str], None]

_READY_LIKE_STATUS = (LinkStatus.UP, LinkStatus.STANDBY)
_IN_PROGRESS = ("autoInProgress", "manualInProgress")


def sort_cold_links(node: RBDNode, links_by_id: dict[int, Link]) -> None:
    """Port of `IdAndSortColds`: `node.cold_links`
    becomes `node.in_paths_array` ordered by each link's `cold_priority`
    ascending (lower number = higher priority = switched on first), and
    `SetSBlink(TRUE)` marks every cold link so the general
    link-status update calls `SetSwitchTime` on it. The `SetColdDep`
    bookkeeping on each link's source object (`cold_dep`) is skipped, as
    nothing in this port reads it.

    The source's sort is a selection sort that SWAPS position `i` with the
    first strictly-lower priority found after it, so it is NOT stable: equal
    priorities (the default is 50) can come out reordered, e.g. priorities
    `[50, 50, 25]` order as links `[3, 2, 1]`, where a stable sort gives
    `[3, 1, 2]`. Ported literally, swap included (`ER3-10`)."""
    order = list(node.in_paths_array)
    priority = [links_by_id[lid].cold_priority for lid in order]
    for i in range(len(order)):
        top = priority[i]
        swap_index = None
        for j in range(i, len(order)):
            if priority[j] < top:
                top = priority[j]
                swap_index = j
        if swap_index is not None:
            order[i], order[swap_index] = order[swap_index], order[i]
            priority[i], priority[swap_index] = priority[swap_index], priority[i]
    node.cold_links = order
    for lid in node.cold_links:
        links_by_id[lid].sb_link = True
        links_by_id[lid].cold_node = node


def reorder_colds(node: RBDNode, last_link: int) -> None:
    """Port of `ReOrderColds`: move `last_link`
    to the end of `node.cold_links`, keeping the others' relative order.
    Called from `ChangeLinkStatus` when a cold link of a node
    WITHOUT priority return goes Up -> Down/PM/Done, so a failed path drops
    to the back of the switching order (and of the node's comment string)."""
    if last_link in node.cold_links:
        node.cold_links.remove(last_link)
        node.cold_links.append(last_link)


def _switch_failed(
    node: RBDNode,
    link: Link,
    links_by_id: dict[int, Link],
    blocks_by_id: dict[int, RBDBlock],
    nodes_by_id: dict[int, RBDNode] | None,
    hiers_by_id: dict[int, RBDHier] | None,
    system_state: SystemState | None,
    sim_time: float,
    *,
    events_file: bool,
    call_reconfigure: bool,
    interrupt: InterruptFn | None = None,
) -> list[str]:
    """Port of RunColdLogic's three identical `switchStatus="none"`
    branches (auto-loop merged
    not-checkAutosFirst loop the Standby-resurvey case in
    the enough-paths branch): a needed path whose SetSwitchTime draw
    landed on `"none"` (`ManTime=-1`, no manual fallback either) is
    PERMANENTLY unusable:

    - `ReconfigureSystem(link.Id)` — only in the two initiation-branch
      occurrences; the enough-paths branch skips it (source shows no
      such call there).
    - `INC(NumCurrentlyFailed)` (the module-level SYSTEM counter ->
      `SystemState.num_currently_failed`).
    - `IF parentID>0: tempHier TO IncNumFailed` (this node's parent
      hierarchy's own `num_currently_failed`).
    - `ChangeLinkStatus(LinkDone)` -> `link.status = DONE` (shows as
      `_dd` in the resurvey, counts in `downPaths`).
    - `pathFailValue[i]++ / pathStandbyValue[i]--` — the same lazy-
      grown arrays `reconfigure_node` maintains (mirrors the source's
      own `arrayIndex` scan over `inPathsArray`).
    - The log line: `Node <node> Switch_Failed Unable_to_switch_to_<
      block-or-node-name>`, name resolved from the link's
      `EconnectFRef`/`EconnectFromId` (Block or Node source).

    `reconfigure_system` lives in `cascade.py`, which (via `nodes.py`)
    imports this module — so it's imported lazily here to break the
    cycle.
    """
    logs: list[str] = []
    if call_reconfigure and nodes_by_id is not None:
        from raptor.core.engine.cascade import reconfigure_system
        logs += reconfigure_system(
            node, nodes_by_id, links_by_id, link.object_num, sim_time, events_file=events_file,
        )
    if system_state is not None:
        system_state.num_currently_failed += 1
    if node.parent_id > 0 and hiers_by_id is not None and node.parent_id in hiers_by_id:
        hiers_by_id[node.parent_id].num_currently_failed += 1
    from raptor.core.engine.nodes import set_link_status_mirrored
    # Port of ChangeLinkStatus(LinkDone) --
    # INCLUDING its sibling mirror (input-side line follows).
    set_link_status_mirrored(link, LinkStatus.DONE, links_by_id)
    array_index = node.in_paths_array.index(link.object_num)
    while len(node.path_fail_value) <= array_index:
        node.path_fail_value.append(0)
    while len(node.path_standby_value) <= array_index:
        node.path_standby_value.append(0)
    node.path_fail_value[array_index] += 1
    node.path_standby_value[array_index] -= 1
    if _src_ref(link) == "RBDBlock":
        name2 = blocks_by_id[_src_id(link)].name
    elif _src_ref(link) == "RBDNode" and nodes_by_id is not None:
        name2 = nodes_by_id[_src_id(link)].report_name
    else:
        name2 = ""
    if events_file:
        logs.append(format_event_log_line(
            sim_time, "Node", node.report_name, "Switch_Failed",
            f"Unable_to_switch_to_{name2}",
        ))
    # The source block's Run process is dead from here on: with its cold
    # link permanently DONE/`active=False` it can never return to service
    # through its own failure/repair cycle, and its remaining fail life
    # freezes. Mark it and wake its generator so it parks (see
    # block_run_process's loop-top `cold_path_done` check).
    if _src_ref(link) == "RBDBlock":
        source = blocks_by_id.get(_src_id(link))
        if source is not None:
            source.run_state.cold_path_done = True
            # No wake of the source block: Raptor 7 does not re-enter its `Run` here (`LadL5Cold`, real 2026-10-01:
            # a failed switch's target with a standby stress logs no `StessLevel`, which a re-entered `Run` would
            # write -- 33 extra lines in 60 runs). The earlier wake (`SWITCH_FAILED_PATH`) fit captures whose
            # blocks had stress 1.0, where re-entry is silent.
    return logs


def run_cold_logic(
    node: RBDNode,
    links_by_id: dict[int, Link],
    blocks_by_id: dict[int, RBDBlock],
    transition: TransitionFn,
    sim_time: float,
    *,
    events_file: bool = False,
    interrupt: InterruptFn | None = None,
    end_node_id: int | None = None,
    spawn_switch: Callable[[Link], None] | None = None,
    skip_backflow: bool = False,
    nodes_by_id: dict[int, RBDNode] | None = None,
    system_state: SystemState | None = None,
    hiers_by_id: dict[int, RBDHier] | None = None,
    interrupt_link: Callable[[Link], None] | None = None,
    backflow_all_up: Callable[[RBDNode], None] | None = None,
    backflow_down: Callable[[RBDNode, bool], None] | None = None,
    emit_log: Callable[[str], None] | None = None,
) -> tuple[list[str], NodeStatus, list[RBDBlock]] | None:
    """Scoped port of `RunColdLogic` -- see
    this module's own docstring for exactly what's covered. Returns
    `(log_lines, new_status, stress_changed_blocks)` (the third element is
    described below), or `None` when `node.higher_cold` is set
    and source therefore takes `RunColdLogic`'s own `ELSE
    ReconfigureNode(0,0,0,0,0,0)` arm instead (the caller performs that
    aggregation -- see the `higher_cold` block below).
    `log_lines` holds ONLY this function's
    OWN `"Node"` status line (any block-transition lines are already
    appended to the caller's shared log by `transition` itself, matching
    every other cascade entry point in this project). The caller is
    responsible for calling `orchestrator._Cascade._cascade_node`-style
    downstream/dependency cascade logic with `new_status` if it differs
    from `node.status` -- this function does NOT call `ChangeNodeState`
    itself, matching `reconfigure_node`'s own split (status-line
    computation here, state assignment + cascade is the caller's job).

    The third element lists every block whose CombStress changed because
    of a switch decision HERE (`Too_many_on` demotion or `Prior=Standby`
    promotion) -- the caller emits each one's synchronous `StessLevel`
    event-log line at the source's placement (right after the node cascade,
    before the system check) and arms the generator-side suppression for the
    same instant.
    """
    if node.higher_cold:
        # `RunColdLogic`'s whole body is wrapped in `IF NOT higherCold ...
        # ELSE ReconfigureNode(0,0,0,0,0,0); END IF` (Raptor 7
        # opens it closes it), so a higherCold node does not
        # clear the flag (there is no `SetHigherCold(FALSE)` anywhere in
        # `RunColdLogic`; it is owned by whoever set it) but still
        # re-aggregates via `ReconfigureNode(0,...)`. With `inPath=0` and
        # `higherCold` true, `ReconfigureNode`'s gate `((linkStatusChanged)
        # OR (inPath=0)) AND ((NOT coldStandby) OR (higherCold))` passes, so
        # the node gets the ordinary K-of-N aggregation and logs the ordinary
        # `"N-K/M_..."` line rather than `run_cold_logic`'s own `"N/M_..."`.
        #
        # The caller does the aggregation (it owns `reconfigure_node`'s
        # arguments and the cascade); returning None reports that this call
        # took the ELSE arm.
        return None

    # Port of RunColdLogic's "prep" phase, gated
    # by `skip_backflow` (`skipBackflow` in source), which `CheckColds`
    # sets true for the remainder of its own
    # invocation once its first `ResendBackFlow` call has run. That gate stops
    # a loop where the prep phase revives an Idle candidate, `ResendBackFlow`
    # re-idles it, and the next pass revives it again.
    #
    # Three sub-effects when not skipped: (1) `SetIgnoreDep(TRUE)` on a
    # currently-Standby candidate; (2) `BackFlowDependencies
    # (AllUp)` on this node, a no-op whenever nothing depends on this cold
    # node (checked via `resolve_dependency_node_id`, and this raises if the
    # node genuinely has a dependent, since skipping would drop a real
    # effect); (3) the second FOR loop clearing `SetIgnoreDep
    # (FALSE)` on every cold link that is `LinkStandby` and not mid-switch.
    if not skip_backflow:
        has_dependent = any(
            resolve_dependency_node_id(
                b, end_node_id if end_node_id is not None else -1,
                links_by_id[b.downstream_link_id].connect_to_id if b.downstream_link_id in links_by_id else -1,
            ) == node.id
            for b in blocks_by_id.values()
        )
        if has_dependent and backflow_all_up is None:
            raise NotImplementedError(
                "RunColdLogic's own pre-survey 'prep' phase (BackFlowDependencies(AllUp) on this "
                "node) needs the caller's `backflow_all_up` -- this cold node has a dependent"
            )
        for lid in node.cold_links:
            link = links_by_id[lid]
            if _src_ref(link) != "RBDBlock":
                continue
            candidate = blocks_by_id[_src_id(link)]
            if candidate.op_status == BlockStatus.STANDBY:
                # Port of Raptor 7's own `IF block.opStatus=
                # Standby: SetIgnoreDep(TRUE)`. Kept as the literal `IF/
                # ELSIF` source has it (a Standby candidate never also
                # takes the Idle arm below).
                candidate.ignore_dep = True
            elif candidate.op_status == BlockStatus.IDLE:
                # Port of Raptor 7's own `ELIF ((block.opStatus=
                # Idle) AND (block.simDependencyNum<>Id)): ChangeBlockState
                # (Running,...,"Dependence")`. `Id` is this cold node's own id
                # (source's `RunColdLogic` is a node method). Promote straight
                # to Running unless the candidate's own dependency target is
                # this cold node itself (an Idle-because-of-this-node case,
                # where promoting would fight the dependency that put it
                # there), checked via `resolve_dependency_node_id`.
                own_node_id = links_by_id[candidate.downstream_link_id].connect_to_id if candidate.downstream_link_id in links_by_id else -1
                target_id = resolve_dependency_node_id(
                    candidate, end_node_id if end_node_id is not None else -1, own_node_id,
                )
                if target_id != node.id:
                    transition(candidate, BlockStatus.RUNNING, "Dependence", sim_time)
                    # No wake here: the source's line is a bare
                    # `ChangeBlockState(Running,...,"Dependence")`
                    # and `ChangeBlockState` contains no
                    # `Interrupt`. A block promoted here is Running on paper
                    # with its `Run` process still parked until the ordinary
                    # `BackFlowDependencies` tail releases it (`IF FailWait:
                    # Interrupt(tempBlock,"Run") ELSIF ZeroWait: ASK
                    # BlockTrigger[seqNum] TO Release`), reached
                    # in this port via `_dispatch_dependency_wait_gated`.

        # `BackFlowDependencies(AllUp)` on this node ("prepping blocks for link survey"):
        # every Idle or Standby dependent of the cold node goes Running. Between the two prep loops.
        if has_dependent and backflow_all_up is not None:
            backflow_all_up(node)

        # Port of RunColdLogic's second prep FOR loop
        # which runs right after the `BackFlowDependencies(AllUp)`
        # call above. Clears `IgnoreDep` on every cold
        # link's source block whose link has settled into `LinkStandby` with no
        # switch in flight. `PerformSwitch`'s own tail clears it too, but only
        # when that process is actually resumed; a link left frozen
        # `autoInProgress` keeps its source block's `ignore_dep` true until a
        # later survey finds the link genuinely Standby and idle.
        for lid in node.cold_links:
            link = links_by_id[lid]
            if link.status != LinkStatus.STANDBY or link.switch_status in _IN_PROGRESS:
                continue
            if _src_ref(link) != "RBDBlock":
                # Source's own `ELSIF EconnectFRef="RBDNode"` arm
                # (`SetIgnoreDep(FALSE)` on the source NODE) is E.6's
                # still-open RBDNode-sourced-cold-link case -- node-side
                # `IgnoreDep` has no reader in this port yet
                # (are unported), so this is
                # a no-op either way. Same treatment as loop 1 above,
                # which skips the identical arm.
                continue
            blocks_by_id[_src_id(link)].ignore_dep = False

    resolved_in_link_count = len(node.in_paths_array)
    k_min = node.good_paths
    k_max = node.k_star
    if node.uses_phasing and k_max < k_min:
        # Raptor 7 (`IF usesPhasing AND activePhases>0 AND KStar<GoodPathsRequired
        # THEN kMax:=GoodPathsRequired`): a phase may set k* below k. `uses_phasing` is only ever
        # set on a node of a model that has phases.
        k_max = k_min

    stressed_blocks: list[RBDBlock] = []
    extra_logs: list[str] = []

    def note_switch_failed(lines: list[str]) -> None:
        """`Switch_Failed` (and the `ReconfigureSystem` lines before it) are written where the survey makes the
        decision, ahead of any later promotion (`LadL5Cold`, real 2026-10-01: `Switch_Failed` precedes the
        `Prior=Standby` `Good` of the block that takes over). Without an `emit_log` they are held to the end."""
        if emit_log is not None:
            for line in lines:
                emit_log(line)
        else:
            extra_logs.extend(lines)
    ready_paths = switching_paths = auto_switch_paths = man_switch_paths = none_paths = 0
    for lid in node.cold_links:
        link = links_by_id[lid]
        if link.status not in _READY_LIKE_STATUS:
            continue
        if link.switch_status == "ready":
            ready_paths += 1
        elif link.switch_status in _IN_PROGRESS:
            switching_paths += 1
        elif link.switch_status == "auto":
            auto_switch_paths += 1
        elif link.switch_status == "manual":
            man_switch_paths += 1
        elif link.switch_status == "none":
            none_paths += 1

    if ready_paths >= k_max:
        runs_required = k_max
    elif ready_paths > k_min:
        runs_required = ready_paths
    else:
        runs_required = k_min

    if ready_paths < runs_required:
        # "not enough ready paths, initiate a new switch" branch
        # `_prep_for_switch` + spawn moves a
        # candidate to `"autoInProgress"` (shown as `..._dsw<id>` in the
        # resurvey below), then to `"ready"` after `switch_time`, at which
        # point the caller re-runs this function and the "enough paths"
        # branch promotes it back to Running.
        switches_required = k_max - ready_paths
        if switching_paths > switches_required:
            # "too many switching": interrupt the
            # EXCESS in-progress switches -- the source counts in-progress
            # STANDBY links in its own `numSwitching` scan and interrupts
            # every one beyond `switchesRequired` with `Interrupt(link,
            # "PerformSwitch")` (the link's own PerformSwitch process's ON
            # INTERRUPT handler re-draws a fresh SetSwitchTime, ported in
            # `perform_switch_process`).
            num_switching = 0
            for lid in node.cold_links:
                link = links_by_id[lid]
                if link.status == LinkStatus.STANDBY and link.switch_status in _IN_PROGRESS:
                    if num_switching < switches_required:
                        num_switching += 1
                    elif interrupt_link is not None:
                        interrupt_link(link)
        elif switching_paths < switches_required and (auto_switch_paths + man_switch_paths + none_paths) > 0:
            if not node.check_autos_first:
                # Port of Raptor 7's merged single pass: auto and manual
                # candidates prep+spawn in one loop (no auto-then-manual
                # two-phase), with the same Switch_Failed machinery for a
                # "none"-status link.
                for lid in node.cold_links:
                    if switching_paths >= switches_required:
                        break
                    link = links_by_id[lid]
                    if link.status not in _READY_LIKE_STATUS:
                        continue
                    if link.switch_status in ("auto", "manual"):
                        if spawn_switch is None:
                            raise EngineInvariantError(
                                "run_cold_logic needs a spawn_switch callback to initiate a new "
                                "async switch"
                            )
                        _prep_for_switch(link, blocks_by_id)
                        spawn_switch(link)
                        switching_paths += 1
                    elif link.switch_status == "none":
                        note_switch_failed(_switch_failed(
                            node, link, links_by_id, blocks_by_id, nodes_by_id,
                            hiers_by_id, system_state, sim_time,
                            events_file=events_file, call_reconfigure=True,
                                                    interrupt=interrupt,
                        ))
            else:
                if auto_switch_paths + none_paths > 0:
                    for lid in node.cold_links:
                        if switching_paths >= switches_required:
                            break
                        link = links_by_id[lid]
                        if link.status not in _READY_LIKE_STATUS:
                            continue
                        if link.switch_status == "auto":
                            if spawn_switch is None:
                                raise EngineInvariantError(
                                    "run_cold_logic needs a spawn_switch callback to initiate a new "
                                    "async switch"
                                )
                            _prep_for_switch(link, blocks_by_id)
                            spawn_switch(link)
                            switching_paths += 1
                        elif link.switch_status == "none":
                            note_switch_failed(_switch_failed(
                                node, link, links_by_id, blocks_by_id, nodes_by_id,
                                hiers_by_id, system_state, sim_time,
                                events_file=events_file, call_reconfigure=True,
                                interrupt=interrupt,
                            ))
                if switching_paths < switches_required:
                    for lid in node.cold_links:
                        if switching_paths >= switches_required:
                            break
                        link = links_by_id[lid]
                        if link.status not in _READY_LIKE_STATUS:
                            continue
                        if link.switch_status == "manual":
                            # Port of Raptor 7's manual loop -- a mirror of the
                            # auto loop above (`PrepForSwitch` + `TELL
                            # PerformSwitch` + INC(switchingPaths)), differing
                            # only on switchStatus=="manual" (the auto-vs-manual
                            # split happened earlier, probabilistically, in
                            # SetSwitchTime). Reachable when a failed AProb roll
                            # leaves a feeder "manual" while more switches are
                            # still required (ColdStandbyProbe2).
                            if spawn_switch is None:
                                raise EngineInvariantError(
                                    "run_cold_logic needs a spawn_switch callback to initiate a "
                                    "manual switch"
                                )
                            _prep_for_switch(link, blocks_by_id)
                            spawn_switch(link)
                            switching_paths += 1

        # BackFlowDependencies(Down) on THIS node itself (Raptor 7:
        # 3385-3389, unconditional whenever readyPaths<kMax, regardless
        # of the specialBackFlow flag it also sets -- that flag only
        # changes HOW a dependent reacts, not WHETHER this fires) -- a
        # no-op whenever nothing depends on THIS SPECIFIC cold node,
        # checked precisely (not guessed), same reasoning as the prep
        # phase above.
        if any(
            resolve_dependency_node_id(
                b, end_node_id if end_node_id is not None else -1,
                links_by_id[b.downstream_link_id].connect_to_id if b.downstream_link_id in links_by_id else -1,
            ) == node.id
            for b in blocks_by_id.values()
        ):
            if backflow_down is None:
                raise NotImplementedError(
                    "RunColdLogic's own BackFlowDependencies(Down) dispatch "
                    "needs the caller's `backflow_down` -- this cold node has a dependent"
                )
            # `specialBackFlow:=TRUE` when the node was Down or NodePM on entry (the Rap7 error
            # 999 fix): its dependents then replay their earlier decision instead of drawing again.
            backflow_down(node, node.status in (NodeStatus.DOWN, NodeStatus.PM))

    # "enough paths available" branch: keep the
    # first `runs_required` (in cold-priority order) as-is; switch every
    # remaining currently-Up one off to Standby, and switch a currently-Standby
    # one back on if it is next in priority order and has room. One single
    # per-link pass, matching the source's own `IF (LinkUp) / ELSIF
    # (LinkStandby)` structure (both branches share the `onPaths` accumulator,
    # so a higher-priority Standby link can claim a slot before a lower-priority
    # Up link is evaluated). Gated on `ready_paths >= runs_required`
    # (Raptor 7's own `IF readyPaths>=RunsRequired ... END IF;`;
    # the `{WAS ... ELSE ...}` text right after is a commented-out dead ELSE).
    if ready_paths >= runs_required:
        on_paths = 0
        for lid in node.cold_links:
            link = links_by_id[lid]
            if link.status == LinkStatus.UP:
                if link.switch_status == "ready" and on_paths < runs_required:
                    on_paths += 1
                    continue
                # "path is up and switchable" -- this one loses its slot. The
                # source's ELSE branch here is
                # unconditional on `switch_status`'s value: it matters only
                # that the link is UP and not the one being kept.
                if _src_ref(link) == "RBDNode":
                    # Port of Raptor 7: an RBDNode-sourced cold
                    # candidate losing its slot is not itself transitioned (no
                    # ChangeNodeState call here, unlike the RBDBlock branch's
                    # ChangeBlockState); the source node's own aggregate status
                    # still tracks its own inbound links untouched. What does
                    # happen: (1) if the source is itself a cold-standby group
                    # leader, freeze its own independent switching
                    # (`SetHigherCold(TRUE)`); (2) `BackFlowDependencies
                    # (NodeStandby)` on the source node, which propagates to
                    # whatever genuinely depends on that source. Both are no-ops
                    # for the checked-in fixtures' candidates; this raises
                    # rather than guesses where they would not be.
                    if nodes_by_id is None:
                        raise EngineInvariantError(
                            "run_cold_logic needs nodes_by_id to resolve an RBDNode-sourced cold link"
                        )
                    source_node = nodes_by_id[_src_id(link)]
                    if source_node.cold_standby:
                        source_node.higher_cold = True
                    # BackFlowDependencies(NodeStandby) on an RBDNode-sourced cold
                    # candidate's dependents is a no-op dispatch: a Standby
                    # node/block is still counted for availability.
                    continue
                if _src_ref(link) == "RBDEvent":
                    # Port of Raptor 7: `SetStuckOn(TRUE); coldsAffected:=TRUE`.
                    # `stuckOn` has no reader anywhere in the source, so nothing observable follows
                    # (`ColdEventProbe`, real-captured 2026-09-30).
                    continue
                if _src_ref(link) != "RBDBlock":
                    raise NotImplementedError(
                        "RunColdLogic's 'too many on' switch-off only ports RBDBlock/RBDNode/RBDEvent-sourced cold links"
                    )
                block = blocks_by_id[_src_id(link)]
                if block.op_status != BlockStatus.RUNNING:
                    continue  # block could already be link-failed -- source's own IF guard
                transition(block, BlockStatus.STANDBY, "Too_many_on", sim_time)
                # Its CombStress just changed (Running stress -> standby
                # stress); the caller emits this block's synchronous
                # `StessLevel` line (Raptor 7's analog).
                stressed_blocks.append(block)
                # `block.has_dep_objects` is never computed here; what matters is
                # whether any other block has a block-to-block dependency on
                # this one. `BackFlowDependencies(NodeStandby)` is a no-op
                # dispatch, since a Standby block is still counted for
                # availability (see `dependency.py`'s NodeStandby branch).
                pass
                # Interrupt fires only for a REAL mid-run switch (Raptor 7:
                # 3164: `IF SimTime>0.0`) -- at kickoff (SimTime==0.0) the
                # target block hasn't reached its first real wait yet (see
                # `cold_logic_kickoff_process`'s own docstring), so there's
                # nothing to interrupt.
                if sim_time > 0.0 and interrupt is not None:
                    interrupt(block, InterruptReason.SWITCHED_OFF)
            elif link.status == LinkStatus.STANDBY:
                if link.switch_status == "ready" and on_paths < runs_required:
                    on_paths += 1
                    if _src_ref(link) == "RBDNode":
                        # Mirror of the switch-off RBDNode branch above
                        #: un-freeze the source
                        # node's own switching (`SetHigherCold(FALSE)`, only
                        # meaningful if it is itself a cold-standby group
                        # leader) and `BackFlowDependencies(AllUp)` on it.
                        if nodes_by_id is None:
                            raise EngineInvariantError(
                                "run_cold_logic needs nodes_by_id to resolve an RBDNode-sourced cold link"
                            )
                        source_node = nodes_by_id[_src_id(link)]
                        if source_node.cold_standby:
                            source_node.higher_cold = False
                        if any(
                            resolve_dependency_node_id(
                                b, end_node_id if end_node_id is not None else -1,
                                links_by_id[b.downstream_link_id].connect_to_id if b.downstream_link_id in links_by_id else -1,
                            ) == source_node.id
                            for b in blocks_by_id.values()
                        ) or any(
                            n.dependency_num != 0 and resolve_dependency_node_id(
                                n, end_node_id if end_node_id is not None else -1, -1,
                            ) == source_node.id
                            for n in nodes_by_id.values()
                        ):
                            raise NotImplementedError(
                                "RunColdLogic's BackFlowDependencies(AllUp) on an RBDNode-sourced "
                                "cold candidate's own dependents not ported -- this candidate "
                                "genuinely has a dependent"
                            )
                        continue
                    if _src_ref(link) == "RBDEvent":
                        continue   # the source's switch-ON arm has no event case
                    if _src_ref(link) != "RBDBlock":
                        raise NotImplementedError(
                            "RunColdLogic's switch-ON branch only ports RBDBlock/RBDNode/RBDEvent-sourced cold links"
                        )
                    block = blocks_by_id[_src_id(link)]
                    if block.op_status == BlockStatus.STANDBY:  # "block could be linked" -- source's own comment
                        transition(block, BlockStatus.RUNNING, "Prior=Standby", sim_time)
                        stressed_blocks.append(block)
                        # BackFlowDependencies(AllUp) for cold-standby blocks going
                        # Running is a no-op dispatch for dependents: a Standby
                        # block is already counted for the node's k-out-of-n
                        # availability, and the dependents' trigger is based on
                        # the block's up/down status, not its Running/Standby
                        # status. The interrupt is unconditional, unlike the
                        # switch-off one above: the source's switch-on branch has
                        # no `SimTime>0.0` guard, since
                        # a Standby block promoted back to Running always needs
                        # its wait recomputed against the new, higher stress.
                        if interrupt is not None:
                            interrupt(block, InterruptReason.SWITCHED_ON)
                elif link.switch_status in _IN_PROGRESS:
                    # Port of Raptor 7 (`cmc 6/19/06   Do not
                    # interrupt if kMax is not met`): a still-in-progress
                    # switch this survey no longer needs gets its own
                    # PerformSwitch process interrupted -- the ON INTERRUPT
                    # handler (ported in `perform_switch_process`) marks the
                    # link "Interrupted" and re-draws a fresh SetSwitchTime
                    # for its next use.
                    if runs_required >= k_max and interrupt_link is not None:
                        interrupt_link(link)
                elif link.switch_status == "none" and on_paths < runs_required:
                    # Port of Raptor 7: same Switch_Failed
                    # machinery as the initiation branches, but WITHOUT the
                    # ReconfigureSystem call (source shows none here).
                    note_switch_failed(_switch_failed(
                        node, link, links_by_id, blocks_by_id, nodes_by_id,
                        hiers_by_id, system_state, sim_time,
                        events_file=events_file, call_reconfigure=False,
                        interrupt=interrupt,
                    ))
                # else: "ready but enough have been turned on already" or
                # "switchable at SimTime>0.0" -- do nothing, matches source's
                # own trailing ELSE exactly.

    # "Resurvey inbound links" -- a SEPARATE
    # aggregation pass from the one above (post-switching-decisions
    # link statuses, not the pre-decision survey).
    up_paths = down_paths = idle_paths = pm_paths = standby_paths = 0
    link_string = ""
    for lid in node.cold_links:
        link = links_by_id[lid]
        if link.status == LinkStatus.UP:
            if link.switch_status in _IN_PROGRESS:
                down_paths += 1
                link_string += f"_dsw{lid}"
            else:
                up_paths += 1
                link_string += f"_up{lid}"
        elif link.status == LinkStatus.DOWN:
            down_paths += 1
            link_string += f"_df{lid}"
        elif link.status == LinkStatus.IDLE:
            idle_paths += 1
            link_string += f"_di{lid}"
        elif link.status == LinkStatus.DONE:
            down_paths += 1
            link_string += f"_dd{lid}"
        elif link.status == LinkStatus.PM:
            pm_paths += 1
            link_string += f"_dpm{lid}"
        elif link.status == LinkStatus.STANDBY:
            if link.switch_status in _IN_PROGRESS:
                down_paths += 1
                link_string += f"_dsw{lid}"
            else:
                standby_paths += 1
                link_string += f"_s{lid}"

    # "Establish node color" -- a K-of-N formula
    # that looks like determine_node_status's but is a SEPARATE port (the
    # bucket names/semantics differ enough -- e.g. "standbyPaths" here
    # has no equivalent there -- that sharing one function would obscure
    # more than it'd save).
    if k_min > resolved_in_link_count:
        new_status, status_string = NodeStatus.DOWN, "Bad"
    elif up_paths == resolved_in_link_count:
        new_status, status_string = NodeStatus.ALL_UP, "Good"
    elif up_paths >= k_min:
        if up_paths + standby_paths == resolved_in_link_count:
            new_status, status_string = NodeStatus.ALL_UP, "Good"
        else:
            new_status, status_string = NodeStatus.DEGRADED, "Degraded"
    elif idle_paths + up_paths >= k_min:
        new_status, status_string = NodeStatus.IDLE, "Idle"
    elif pm_paths + idle_paths + up_paths >= k_min:
        new_status, status_string = NodeStatus.PM, "NodePM"
    elif down_paths > 0:
        new_status, status_string = NodeStatus.DOWN, "Bad"
    elif switching_paths > 0:
        # Port of Raptor 7's `ELSIF switchPaths > 0:
        # newNodeStatus:=Down`: a cold node with an in-progress switch but
        # no `down` path goes Bad.
        new_status, status_string = NodeStatus.DOWN, "Bad"
    else:
        raise ValueError(f"Error in node status logic (cold logic)! up={up_paths} standby={standby_paths} idle={idle_paths} pm={pm_paths} k={k_min} n={resolved_in_link_count} t={sim_time} {node.node_name} {link_string}")

    logs: list[str] = list(extra_logs)
    if events_file:
        logs.append(format_event_log_line(
            sim_time, "Node", node.node_name, status_string, f"{up_paths}/{resolved_in_link_count}{link_string}",
        ))
    return logs, new_status, stressed_blocks


def cold_logic_kickoff_process(run_once: Callable[[], None]) -> Process:
    """A trivial deferred process whose only job is to run `run_once` --
    letting every block's own `Initialize` turn (also registered via
    `scheduler.schedule_process` at `time=0.0`) run first, since this is
    scheduled after them in the same per-run setup, and `run_until`'s
    tie-break is insertion order (see `scheduler.py`). Calling
    `run_cold_logic` directly from `run_simulation`'s Python code before
    `scheduler.run_until` starts would run it before any block logs anything,
    since `schedule_process` only registers a process.

    Never actually suspends -- `RunColdLogic`'s own kickoff pass is
    synchronous once it's this process's turn. The trailing `yield` after
    `return` is the standard Python idiom for a generator function that
    may legitimately never yield, keeping this callable via `Scheduler`'s
    own `process.send(None)` machinery without a real suspension point.
    """
    run_once()
    return
    yield  # pragma: no cover -- unreachable, see docstring


def _prep_for_switch(link: Link, blocks_by_id: dict[int, RBDBlock]) -> None:
    """Port of `Link.PrepForSwitch`, scoped to the
    `RBDBlock`-sourced case. Marks the switch as actually starting
    (`"auto"`/`"manual"` -> `"...InProgress"`) and sets `ignore_dep` on the
    source block so it is skipped by any dependency dispatch while the switch
    is in flight, cleared again once `perform_switch_process` completes.
    """
    if link.switch_status == "manual":
        link.switch_status = "manualInProgress"
    elif link.switch_status == "auto":
        link.switch_status = "autoInProgress"
    else:
        raise EngineInvariantError(link.switch_status)  # pragma: no cover -- caller already checked
    link.switch_completed = False
    if _src_ref(link) != "RBDBlock":
        raise NotImplementedError(
            "PrepForSwitch for a non-RBDBlock-sourced cold link not ported -- no fixture reaches it"
        )
    blocks_by_id[_src_id(link)].ignore_dep = True


def perform_switch_process(
    link: Link, block: RBDBlock, run_once: Callable[[], None], rng: RandomSource,
    spawn_status: LinkStatus | None = None,
) -> Process:
    """Port of `Link`'s own `TELL METHOD PerformSwitch`,
    including its ON INTERRUPT branch: an `Interrupt(link,
    "PerformSwitch")` (from `run_cold_logic`'s too-many-switching scan or
    enough-paths Standby survey) marks the link `"Interrupted"` then re-runs
    `SetSwitchTime` -- a fresh probabilistic draw from `SysStream[11]` (`rng`)
    that decides the link's next switch attempt's auto/manual/none status. The
    link stays Standby and the switch is retried later. The method's tail
    (`SetIgnoreDep(FALSE)`, `switchCompleted:=TRUE`, `CheckColds`) runs on both
    paths, since Modsim's `WAIT ... ON INTERRUPT` falls through to the code
    after the WAIT either way.

    `run_once` is `orchestrator._Cascade.apply_cold_logic` bound to the
    switch's own target node -- mirrors `PerformSwitch`'s `coldsAffected:=TRUE`
    + `ColdChangeGroup.Add`, this project's equivalent of `CheckColds`'s loop.

    `spawn_status`: `link.status` at the exact moment this switch was spawned
    (`orchestrator.py`'s `spawn_switch` closure); see the ON INTERRUPT branch's
    comment for why it matters.
    """
    resume = yield link.switch_time
    if isinstance(resume, Wake):
        # ON INTERRUPT. `Scheduler.interrupt`
        # delivers only the first interrupt attempt against a given switch's
        # in-flight lifetime; later attempts on the same cycle are no-ops,
        # matching `_switch_handles.pop()`'s single-use semantics. When the
        # interrupt's settled status still matches `spawn_status` and it came
        # through the reconfigure route, the delivery is a complete no-op: the
        # link stays frozen as it was ("up but switching") with no redraw and
        # no `CheckColds`, because an interrupted Modsim WAIT is gone and never
        # re-completes on its own. Otherwise `SetSwitchTime` redraws a fresh
        # status.
        if (spawn_status is not None and link.status == spawn_status
                and resume.reason == InterruptReason.PERFORM_SWITCH_RECONFIGURE):
            # The spawn-status no-op applies only to the reconfigure route
            # (`ReconfigureNode`), where a settled
            # status equal to spawn time means nothing genuinely changed.
            # `RunColdLogic`'s survey site (Raptor 7,
            # `IF RunsRequired>=kMax`) is a deliberate "enough paths, this
            # switch is no longer needed" cancel that the Raptor 7 engine always
            # acts on.
            return
        link.switch_status = "Interrupted"
        set_switch_time(link, rng)
    else:
        link.switch_status = "ready"
    block.ignore_dep = False
    link.switch_completed = True
    run_once()
    return
    yield  # pragma: no cover -- unreachable, keeps this a generator
