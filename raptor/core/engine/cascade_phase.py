"""The cascade's phase change: `ConductPhaseChange`, applying
a new phase's stress, Cut/Linked state and node paths to every phased
block, event and node, in source's order.

These are `_Cascade` methods, bound in the class body
(`name = cascade_phase.name`); each takes the cascade as `self`.
"""
from __future__ import annotations

from typing import TYPE_CHECKING

from raptor.core.engine.events import change_event_state, conduct_event
from raptor.core.engine.interrupts import InterruptReason
from raptor.core.engine.nodes import reconfigure_node
from raptor.core.engine.phasing import classify_block_phase_transition, classify_event_phase_transition, classify_event_same_value_refire, classify_node_phase_transition
from raptor.core.engine.system_status import MissionStatus, SystemColor, analyze_system, change_system_status
from raptor.core.model import ActiveStatus, BlockStatus, EventStatus, RBDBlock, RBDEvent, RBDNode
from raptor.core.scheduler import Owner

if TYPE_CHECKING:
    from raptor.core.engine.orchestrator import _Cascade


def conduct_phase_change(self: _Cascade, new_phase: int, sim_time: float):
    """Port of `ConductPhaseChange`. A
    generator, called directly from `ControlPhaseChanges`'s own running
    process, so its `WAIT DURATION 0.0` calls run on that same scheduler
    process.

    Classification (`phasing.classify_*`) happens up front for every
    phased block/event/node whose phase value/type/k differs between
    `old_phase` and `new_phase`; application order (Improve->Stress->
    Degrade blocks/events->Degrade nodes for Green/Yellow, reversed for
    Red) reuses the already-ported change functions. Each applied
    block/event gets 5 zero-duration yields, each node 1 -- a real source
    inconsistency, not a typo here.

    PM-deferral release (`pmDeferGroup`) is not ported; raises if a
    phased block would need it."""
    old_phase = self.system_state.current_phase
    phase = self.model.phases[new_phase - 1]
    # Port of `PhaseChangeInProgress:=TRUE` -- spans this
    # generator's whole body including its yields. `_schedule_or_check_
    # system_status` reads it; try/finally so an exception mid-body does
    # not leave the engine permanently suppressed.
    self.phase_change_in_progress = True
    try:
        yield from self._conduct_phase_change_body(new_phase, sim_time, old_phase, phase)
    finally:
        self.phase_change_in_progress = False
    self.release_pm_sync()   # `ASK pmSyncTrigger TO Release`
    # Arm the lazy end-of-instant check (see the combined check's comment
    # in `_conduct_phase_change_body`) -- only when the phase change
    # changed something: a quiet boundary never sets
    # `SystemStateChange`, and Raptor 7 is silent there.
    if self._phase_change_dirtied and self._pending_status_check_time != sim_time:
        self._pending_status_check_time = sim_time
        self._pending_status_check_dirty = True


def phase_apply_node(self: _Cascade, node: RBDNode, new_val: int, sim_time: float,
                     *, events_file: bool = True) -> None:
    """`RBDNodeObj.ChangePhases`: a hierarchy's Out node
    goes Linked / Cut / Active; any other node takes the phase's k and reconfigures.
    Shared by the run's phase change and Failure Effects View's `FEVPhaseChange`."""
    if node.type_node == 5:
        if new_val == 0:
            new_active = ActiveStatus.LINKED
        # `len(node.in_paths_array)`, NOT `node.connect_into_num` (always 0 for
        # nodes): must match the SAME "impossible k" sentinel the caller used to
        # compute `new_val` in the first place.
        elif new_val == len(node.in_paths_array) + 1:
            new_active = ActiveStatus.CUT
        else:
            new_active = ActiveStatus.ACTIVE
        self._cascade_node(node, node.status, sim_time, new_active_status=new_active)
    else:
        node.good_paths = new_val
        result = reconfigure_node(
            node, self.model.links_by_id, 0, 0, 0, 0, 0, 0, sim_time,
            events_file=events_file, hier_name=self._hier_names_by_node_id.get(node.id),
            sb_link_rng=self.sb_link_rng, on_link_status_change=self._mark_cap_link_change,
            # `ReconfigureNode`'s cold branch: a cold node queues
            # itself, and the phase change's closing `CheckColds` runs its survey.
            mark_cold_changed=self._mark_node_cold_changed,
        )
        if result is not None:
            if result.log_line is not None:
                self.logs.append(result.log_line)
            self._cascade_node(node, result.new_status, sim_time)


def _conduct_phase_change_body(self: _Cascade, new_phase: int, sim_time: float, old_phase: int, phase):

    # Raptor 7 -- entering a NonMission phase while
    # currently in a Mission ends that mission immediately, as part of
    # this phase change, and with `deferTrig` = `phaseTrigger` the trigger of that name is released
    # right after, freeing the `maintDefer` blocks held for the mission.
    if not phase.mission and self.system_state.mission_status == MissionStatus.MISSION:
        self.logs.extend(change_system_status(
            self.system_state, self.system_state.color, sim_time, self._all_blocks,
            events_file=True, new_mission_status=MissionStatus.NON_MISSION,
            system_red_cost=self.model.system_red_cost if self.model.cost_analysis else 0.0,
            flagged_blocks=self.flagged_blocks(),
        # Weak-link per-element mission bookkeeping at a phased mission
        # boundary (Raptor 7 at begin at
        # end). Source does this inside `ChangeStatus` on every
        # mission-status change, not only the run-end one.
        block_stats=self.stats_by_block if self.model.weak_analysis else None,
        node_stats=self.node_stats if self.model.weak_analysis else None,
        nodes=list(self.model.nodes_by_id.values()) if self.model.weak_analysis else None,
        event_stats=self.stats_by_event if self.model.weak_analysis else None,
        events=list(self.model.events_by_id.values()) if self.model.weak_analysis else None,
        ))
        if self.model.defer_trigger == "phaseTrigger":
            self.release_trigger("phaseTrigger", sim_time)

    self.system_state.current_phase = new_phase  # phaseNumber:=newPhase 

    block_groups: dict[str, list[tuple]] = {"improve": [], "degrade": [], "stress": []}
    for block in self.model.blocks_by_id.values():
        if not block.uses_phasing or not block.phase_type:
            continue
        old_type, new_type = block.phase_type[old_phase - 1], block.phase_type[new_phase - 1]
        old_value, new_value = block.phase_value[old_phase - 1], block.phase_value[new_phase - 1]
        if old_value == new_value and old_type == new_type:
            continue
        group = classify_block_phase_transition(old_type, new_type)
        if group is not None:
            block_groups[group].append((block, old_type, new_type))

    event_groups: dict[str, list[tuple]] = {"improve": [], "degrade": []}
    for event in self.model.events_by_id.values():
        if not event.uses_phasing or not event.phase_type:
            continue
        old_type, new_type = event.phase_type[old_phase - 1], event.phase_type[new_phase - 1]
        old_value, new_value = event.phase_value[old_phase - 1], event.phase_value[new_phase - 1]
        is_failure = event.op_status == EventStatus.FAILURE
        if old_value != new_value or old_type != new_type:
            group = classify_event_phase_transition(old_type, new_type, is_failure)
        else:
            group = classify_event_same_value_refire(old_type, is_failure)
        if group is not None:
            event_groups[group].append((event, old_type, new_type))

    node_groups: dict[str, list[tuple]] = {"improve": [], "degrade": []}
    for node in self.model.nodes_by_id.values():
        if not node.uses_phasing or not node.phase:
            continue
        # A node's real incoming-path count is derived dynamically from
        # the graph (`len(node.in_paths_array)`), matching this project's
        # convention elsewhere; `node.connect_into_num` is never
        # populated for nodes. The real value matters because the `-1`
        # sentinel remaps to `connect_into_num+1`.
        connect_into_num = len(node.in_paths_array)
        raw_old, raw_new = node.phase[old_phase - 1], node.phase[new_phase - 1]
        group = classify_node_phase_transition(raw_old, raw_new, connect_into_num)
        if group is not None:
            old_val = connect_into_num + 1 if raw_old == -1 else raw_old
            new_val = connect_into_num + 1 if raw_new == -1 else raw_new
            node_groups[group].append((node, old_val, new_val))

    improves = len(block_groups["improve"]) + len(node_groups["improve"])
    degrades = len(block_groups["degrade"]) + len(node_groups["degrade"])

    def apply_block(block: RBDBlock, old_type: str, new_type: str) -> None:
        # Raptor 7 -- old_type==new_type ("A"->"A", the
        # only case routed here) is the "do nothing" case for the
        # state-transition half, but `ChangePhases`'s own tail
        # is unconditional, running regardless of which
        # branch matched, so a stress-only phase change still wakes the
        # block to recompute `combined_stress`.
        if old_type != new_type:
            new_active = {"C": ActiveStatus.CUT, "L": ActiveStatus.LINKED, "A": ActiveStatus.ACTIVE}[new_type]
            # `PhaseStress` is NOT refreshed here: only `Run`'s own top-of-loop
            # and init write it, so a block
            # that is Cut and then recovers logs the stale value from its last
            # Run pass (`LadL6Time`: `Phase_ChangeStress=0.500000` after a
            # Cut/Linked excursion, where this used to print the Cut phase's 1.0).
            # `top_level=False` -- suppresses this transition's own
            # individual system-status check. Multiple blocks/events/nodes
            # can all change in this same phase-change instant; this
            # method's explicit `_check_system_status` call after every
            # group has settled is the single combined one.
            self.transitions[block.id](block.op_status, new_active, "Phase_Change", sim_time, top_level=False)
        # `ChangePhases`'s own tail is unconditional,
        # running after every branch above, including type-changing
        # transitions: the `self.transitions[...]` call only mutates
        # state and logs, it never wakes the block's suspended generator,
        # so `block.run_state.phase_stress` would never refresh for a block that
        # goes Cut and stays Cut until its next recovery. Running/
        # Standby/Idle are the only states safe to interrupt.
        if block.id in self.handles and block.op_status in (BlockStatus.RUNNING, BlockStatus.STANDBY, BlockStatus.IDLE):
            # A block mid-repair/PM/resource-wait is suspended on a
            # specific reason and would crash on an unexpected interrupt.
            # Idle is included: source's `ZeroWait` case lands the same
            # `"Phase_Change"` reason in the block's suspended Run. This
            # activates `block_lifecycle`'s Idle-wait-loop redraw logic.
            self.scheduler.interrupt(self.handles[block.id], InterruptReason.PHASE_CHANGE)

    def apply_event(event: RBDEvent, old_type: str, new_type: str) -> None:
        # Raptor 7's own full dispatch table.
        stats = self.stats_by_event[event.id]
        old_op_status = event.op_status
        if old_type == new_type and old_type not in ("F", "A"):
            return  # C->C, L->L, {F,P,A}->P (identical type, no redraw case): do nothing
        if new_type in ("F", "A"):  # every C/L/F/P/A -> F/A transition fires, unconditionally
            logs, rc, fc = conduct_event(
                event, stats, self.rng_by_event[event.id], sim_time, events_file=True,
                cost_analysis=self.model.cost_analysis,
                weak_analysis=self.model.weak_analysis,
                current_phase=self.system_state.current_phase,
                in_mission=self.system_state.mission_status is MissionStatus.MISSION,
            )
            self.logs.extend(logs)
            self.apply_event(event, old_op_status, rc, fc, sim_time)
            return
        new_active = {
            ("C", "L"): ActiveStatus.LINKED, ("C", "P"): ActiveStatus.ACTIVE,
            ("L", "C"): ActiveStatus.CUT, ("L", "P"): ActiveStatus.ACTIVE,
        }.get((old_type, new_type))
        if new_active is None and old_type in ("F", "P", "A"):
            new_active = {"C": ActiveStatus.CUT, "L": ActiveStatus.LINKED}.get(new_type)
        if new_active is None:
            return  # {F,P,A}->P: do nothing
        logs, rc, fc = change_event_state(
            event, stats, event.op_status, new_active, "Phase_Change", sim_time,
            weak_analysis=self.model.weak_analysis, events_file=True,
            in_mission=self.system_state.mission_status is MissionStatus.MISSION,
        )
        self.logs.extend(logs)
        self.apply_event(event, old_op_status, rc, fc, sim_time)

    def apply_node(node: RBDNode, old_val: int, new_val: int) -> None:
        self.phase_apply_node(node, new_val, sim_time)

    def apply_block_or_event_group(group: list[tuple]):
        for thing, old_type, new_type in group:
            if isinstance(thing, RBDEvent):
                apply_event(thing, old_type, new_type)
            else:
                apply_block(thing, old_type, new_type)
            for _ in range(5):
                yield 0.0

    def apply_node_group(group: list[tuple], hops: int = 1):
        # One `WAIT DURATION 0.0` per node, except the Green/Yellow
        # branch's degrade loop, which has `FOR i:=1 TO 5`
        # (the Red branch's copy, has one).
        for node, old_val, new_val in group:
            apply_node(node, old_val, new_val)
            for _ in range(hops):
                yield 0.0

    if self.system_state.color in (SystemColor.GREEN, SystemColor.YELLOW):
        yield from apply_block_or_event_group(block_groups["improve"] + event_groups["improve"])
        yield from apply_node_group(node_groups["improve"])
        yield from apply_block_or_event_group(block_groups["stress"])
        yield from apply_block_or_event_group(block_groups["degrade"] + event_groups["degrade"])
        yield from apply_node_group(node_groups["degrade"], hops=5)
    else:
        yield from apply_block_or_event_group(block_groups["degrade"] + event_groups["degrade"])
        yield from apply_node_group(node_groups["degrade"])
        yield from apply_block_or_event_group(block_groups["stress"])
        yield from apply_block_or_event_group(block_groups["improve"] + event_groups["improve"])
        yield from apply_node_group(node_groups["improve"])

    # Port of Raptor 7's deferred-PM release. Whenever the
    # system is NonMission at this point in the phase change, every block
    # with a phase-deferred PM outstanding is released: deferPM cleared
    # and a `Start_Deferred_PM` wake delivered to its Run process. The
    # wake routes through the same `DetermineNewState("PM is due")`
    # dispatch, whose defer-gate does not re-defer because the mission
    # status is NonMission.
    _cold_settle = improves + degrades > 0 or event_groups["improve"] or event_groups["degrade"]
    if _cold_settle:
        # Source has no `WAIT DURATION 0.0` here: `pmDeferGroup`'s
        # release loop runs straight into `CheckColds`
        # This extra hop is a HEURISTIC
        # correction, not a port. An induced block needs two scheduler
        # slots to get from its Failed line to its RepHold line (the
        # interrupt-delivery turn, then the separate Repair TELL spawn).
        # Without a hop the phase process reaches CheckColds after
        # granting only one of those slots, so the cold settle lands
        # between those two lines (removing it: corpus 8 -> 21). It sits
        # BEFORE the release, not between the release and CheckColds,
        # so the release and CheckColds share one turn as in source.
        # Otherwise a released block's `PM_is_due` would precede
        # CheckColds' `Too_many_on` (ReleaseProbe31 run 6 t=379.760103;
        # corpus 8 -> 7). Revert it if a source reading contradicts it.
        yield 0.0
    if self.system_state.mission_status is MissionStatus.NON_MISSION:
        # `FOREACH block IN pmDeferGroup` walks the group in the order blocks were added.
        for block in sorted((b for b in self.model.blocks_by_id.values() if b.run_state.defer_pm),
                            key=lambda b: b.run_state.pm_defer_order):
            block.run_state.defer_pm = False
            if block.id in self.handles and block.op_status in (
                    BlockStatus.RUNNING, BlockStatus.STANDBY, BlockStatus.IDLE):
                # `IF block.FailWait: Interrupt(block,"Run") ELSIF block.ZeroWait: BlockTrigger[seqNum].Release`
                #: a block parked at zero stress or Idle is woken through its
                # BlockTrigger, a record of that object's own group, not of the block's.
                gc_owner = Owner.block_trigger(block.id) if (block.run_state.zero_wait and not block.run_state.fail_wait) else None
                self.scheduler.interrupt(self.handles[block.id], InterruptReason.START_DEFERRED_PM, gc_owner=gc_owner)

    if _cold_settle:
        self._flush_cold_changes(sim_time)

    # One combined system-status check for the whole phase change, now
    # that every block/event/node group has settled. A no-op whenever the
    # color did not actually change, so it is safe to call unconditionally.
    #
    # Clear `_induced_wake_pending` before that check: a surviving
    # Induced_Failure/OBPM wake does its own normal un-suppressed check
    # when it actually transitions, and one whose wake is staled by a
    # later same-handle interrupt must not leave the deferral debt
    # latched, or it would swallow the next unrelated top-level check.
    self._induced_wake_pending = False
    # Suppress repeated-Red emission at quiet phase boundaries: when no
    # block/event changed status during this phase change there is nothing
    # new for the repeated-Red comment to describe, and Raptor 7 is silent at
    # such boundaries.
    self._quiet_phase_boundary = (
        not block_groups["improve"] and not block_groups["degrade"]
        and not event_groups["improve"] and not event_groups["degrade"]
    )
    # Source's `ConductPhaseChange` has no end-of-body `AnalyzeSystem`
    # except inside the mission-begin branch (Raptor 7,
    # before `ChangeStatus`). A phase change that changed anything sets
    # `SystemStateChange`, so the lazy TimeAdvance `AnalyzeSystem` runs
    # once at the END of the instant, after every same-instant activity.
    # So the port checks here only when a mission begins; every other
    # status line (color change or repeated-Red) comes from the lazy
    # check that `conduct_phase_change` arms afterwards (2026-09-23/24:
    # 99 -> 75, then 36 -> 31 runs differing, no regressions).
    self._phase_change_dirtied = not self._quiet_phase_boundary
    _mission_begins = phase.mission and self.system_state.mission_status == MissionStatus.NON_MISSION
    if _mission_begins:
        # Source's own pre-mission `AnalyzeSystem`.
        self._check_system_status(sim_time)
    # Otherwise NO check here at all (2026-09-24): color changes too come
    # from the lazy end-of-instant check armed below, after every
    # same-instant activity (PhasingDemo t=388.948360: LowGain's
    # standby-stress hop precedes Raptor 7's State_change Yellow).
    self._quiet_phase_boundary = False

    # Raptor 7 -- entering a Mission phase while currently
    # NonMission begins a new mission.
    if phase.mission and self.system_state.mission_status == MissionStatus.NON_MISSION:
        new_color = analyze_system(self.system_state)
        self.logs.extend(change_system_status(
            self.system_state, new_color, sim_time, self._all_blocks,
            events_file=True, new_mission_status=MissionStatus.MISSION,
            system_red_cost=self.model.system_red_cost if self.model.cost_analysis else 0.0,
            flagged_blocks=self.flagged_blocks(),
        # Weak-link per-element mission bookkeeping at a phased mission
        # boundary. Source does this inside `ChangeStatus` on every
        # mission-status change, not only the run-end one.
        block_stats=self.stats_by_block if self.model.weak_analysis else None,
        node_stats=self.node_stats if self.model.weak_analysis else None,
        nodes=list(self.model.nodes_by_id.values()) if self.model.weak_analysis else None,
        event_stats=self.stats_by_event if self.model.weak_analysis else None,
        events=list(self.model.events_by_id.values()) if self.model.weak_analysis else None,
        ))
