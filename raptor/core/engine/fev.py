"""Failure Effects View (FEV): a model state you change by hand, with no event loop.

Port of `StartFEVmode` / `RefreshAllFEV`, the FEV click
handler and the `FEVmode` branches of
`BackFlowDependencies`.
Design page: `docs/engine-design/fev-mode.adoc`.

What FEV is (and the run engine is not):

* Every element starts operating, in phase 1. Nothing is scheduled: there is
  no `Scheduler` here (`_NullScheduler` raises if anything tries to wait or wake
  a process), no random number is drawn, and no statistic is kept.
* A user action changes ONE element with `ChangeBlockState` / `ChangeEventState`
  and the model reconfigures through the same cascade a run uses (nodes, links,
  hierarchies, the system colour), then `CheckColds; AnalyzeSystem`.
* A dependency reacts deterministically, never by draw: a dependent block takes
  its worst-case outcome (failure, else PM, else idle, taking the first whose
  percentage is above zero), and is marked `FevDepMode` ("hatched"). Clicking a
  hatched block cycles its alternative outcomes (`click_block`).

Cold-standby nodes work: `SetSwitchTime` is skipped in FEV, so every link keeps
`switchStatus "ready"` and a switch is immediate, with no process (a captured
Raptor 7 session on `HierarchyExample` pins it).

Phased models: the start state is phase 1 (Cut / Linked blocks, events and
hierarchies from `phaseType[1]`), `goto_phase` is `FEVPhaseChange`
(which applies the improve / stress / degrade groups in an
order that depends on whether the system is Red), and the small eraser is
`RefreshPhase`. A Raptor 7 session on `Phasing3` pins it.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from raptor.core.engine.blocks import change_block_state
from raptor.core.engine.cold_standby import NoSwitchTime, sort_cold_links
from raptor.core.engine.driver import build_engine_ctx
from raptor.core.engine.dependency import back_flow_dependencies_from_node
from raptor.core.engine.errors import EngineInvariantError
from raptor.core.engine.events import change_event_state
from raptor.core.engine.nodes import reconfigure_node
from raptor.core.engine.orchestrator import _Cascade
from raptor.core.engine.phasing import (
    classify_block_phase_transition, classify_event_phase_transition,
    classify_event_same_value_refire, classify_node_phase_transition,
)
from raptor.core.engine.system_status import SystemColor
from raptor.core.fileio.rbd import RbdFile
from raptor.core.model import (
    ActiveStatus, BlockRunState, BlockStatus, EventStatus, NodeStatus, RBDBlock, RBDEvent,
)


class _NullScheduler:
    """The scheduler FEV does not have. `time` is 0.0 for the whole session;
    anything that would schedule, wake or wait is an engine error, so a
    divergence from "no process to wake" fails loudly instead of silently
    running a stale process."""

    time = 0.0
    last_active_time = 0.0

    def switch_off_tie_breaking(self) -> None:       # `ChangeStatus`: harmless
        return None

    def is_executing(self, _id: Any) -> bool:
        return False

    def next_time(self) -> None:
        return None

    def _refuse(self, what: str) -> None:
        raise EngineInvariantError(
            f"Failure Effects View has no scheduler: {what} was called")

    def schedule_process(self, *a: Any, **k: Any) -> Any:
        self._refuse("schedule_process")

    def interrupt(self, *a: Any, **k: Any) -> Any:
        self._refuse("interrupt")

    def tell(self, *a: Any, **k: Any) -> Any:
        self._refuse("tell")


@dataclass(frozen=True)
class FevDelta:
    """What one action did, for a UI or a script to show."""
    action: str
    system_color: SystemColor
    hatched: tuple[str, ...]
    criticality: float
    status_text: str = ""


class _FevDown:
    """`FevDependencyHooks`: the FEV `Down` rule for one dependent block."""

    def __init__(self, session: FevSession) -> None:
        self.s = session

    def down(self, dep_block: RBDBlock, sim_time: float) -> None:
        s = self.s
        if dep_block.dep_nothing_percent != 100.0:
            s._mark_hatched(dep_block)
            if s.system_color in (SystemColor.GREEN, SystemColor.YELLOW):
                s.criticality *= (dep_block.dep_fail_percent + dep_block.dep_pm_percent
                                  + dep_block.dep_idle_percent) / 100.0
        # comment "" (not "Dependence").
        if dep_block.dep_fail_percent > 0.0:
            s._set_block(dep_block, BlockStatus.REPAIRING, top_level=False)
        elif dep_block.dep_pm_percent > 0.0:
            s._set_block(dep_block, BlockStatus.PM, top_level=False)
        elif dep_block.dep_idle_percent > 0.0:
            s._set_block(dep_block, BlockStatus.IDLE, top_level=False)


class _FevCascade(_Cascade):
    """The run's cascade with its dependency dispatch made scheduler-free."""

    def _schedule_or_check_system_status(self, sim_time: float) -> None:
        # FEV calls `AnalyzeSystem` itself after every action.
        return None

    def _dispatch_dependency(self, dep_block: RBDBlock, new_status, comment: str,
                             sim_time: float, *, wait_gated: bool = False) -> None:
        # `ChangeBlockState` only: in FEV no block is waiting
        # (`FailWait`/`ZeroWait` are false after `fevReset`), so the
        # `Interrupt` / `BlockTrigger.Release` that follows it in a run is a no-op.
        self.transitions[dep_block.id](
            new_status, dep_block.active_status, comment, sim_time, top_level=False,
            phase_stress=dep_block.run_state.phase_stress)


    def _switch_interrupt(self, block: RBDBlock, reason: str) -> None:
        # `RunColdLogic`'s `Interrupt(block,"Run")` is gated on `FailWait`; no
        # block process exists in FEV, so there is nothing to wake.
        block.run_state.interrupt_reason = reason

    def _draw_dependency_action(self, dep_block, sim_time: float) -> str:
        raise EngineInvariantError(
            "Failure Effects View draws no dependency action (the FEV rule is "
            "deterministic); a dispatch bypassed the FEV hook")


class FevSession:
    """One Failure Effects View session over a model."""

    def __init__(self, rbd_file: RbdFile) -> None:
        self._rbd_file = rbd_file
        self.model, ctx = build_engine_ctx(rbd_file, num_runs=1)
        self.ctx = ctx
        self.logs: list[str] = []
        self.criticality = 1.0
        #: `FevDepGroup`: the hatched blocks, in the order they were marked.
        self.fev_dep_group: list[RBDBlock] = []
        self.phase_number = 1
        self._build()

    # ---- construction ---------------------------------------------------

    def _build(self) -> None:
        model, ctx = self.model, self.ctx
        self.cascade = cascade = _FevCascade(
            model, _NullScheduler(), self.logs, end_node=ctx.end_node,  # type: ignore[arg-type]
            rng_fail_by_block=ctx.rng_fail_by_block, sb_link_rng=NoSwitchTime(),
            stats_by_block=ctx.stats_by_block, stats_by_event=ctx.stats_by_event,
            rng_by_event=ctx.rng_by_event, capacity_state=None, pool_stats_by_name={})
        cascade.fev = _FevDown(self)
        self._start_state()

    def _start_state(self) -> None:
        """`StartFEVmode` / `RefreshAllFEV`: everything operating, phase 1."""
        model, cascade, ctx = self.model, self.cascade, self.ctx
        cascade.reset_for_new_run()
        for node in model.nodes_by_id.values():
            node.good_paths = ctx.original_good_paths[node.id]
        cascade.sim_in_progress = False
        for node in model.nodes_by_id.values():
            if node.cold_standby and not node.priority_return:
                sort_cold_links(node, model.links_by_id)
        for block in model.blocks_by_id.values():
            block.state_start_time = 0.0
            block.run_state = BlockRunState()
            block.ignore_dep = False
            block.op_status = BlockStatus.RUNNING
            block.active_status = ActiveStatus.ACTIVE
            cascade.transitions[block.id] = self._transition_for(block)
        for event in model.events_by_id.values():
            event.op_status = EventStatus.SUCCESS
            event.active_status = ActiveStatus.ACTIVE
            event.state_start_time = 0.0
        self.fev_dep_group.clear()
        self.criticality = 1.0
        self.phase_number = 1
        # `simInProgress:=TRUE` before the dependency pass.
        cascade.sim_in_progress = True
        cascade.system_state.current_phase = 1
        self._phase_1_start_conditions()
        # `IdAndSortColds` + `ColdChangeGroup.Add` for every cold node, then the
        # `CheckColds` of `_analyze`.
        for node in model.nodes_by_id.values():
            if node.cold_standby:
                cascade._cold_change_group.setdefault(node.id)
        self._analyze()

    @property
    def phased(self) -> bool:
        return bool(self.model.phases)

    def _phase_1_start_conditions(self) -> None:
        """`fevStartCond` for blocks and events, then the node pass of
        `StartFEVmode`: phase 1's Cut / Linked."""
        model, cascade = self.model, self.cascade
        if not model.phases:
            return
        for node in model.nodes_by_id.values():
            if node.type_node != 5 and node.uses_phasing and node.phase:
                v = node.phase[0]
                node.good_paths = len(node.in_paths_array) + 1 if v == -1 else v
        for block in model.blocks_by_id.values():
            if block.uses_phasing and block.phase_type:
                block.run_state.phase_stress = block.phase_value[0] if block.phase_value else 1.0
                t = block.phase_type[0]
                if t in ("L", "C"):
                    cascade.transitions[block.id](
                        BlockStatus.RUNNING,
                        ActiveStatus.LINKED if t == "L" else ActiveStatus.CUT,
                        "Initialize", 0.0, top_level=False)
        for event in model.events_by_id.values():
            if event.uses_phasing and event.phase_type and event.phase_type[0] in ("L", "C"):
                t = event.phase_type[0]
                old = event.op_status
                _logs, rc, fc = change_event_state(
                    event, self.ctx.stats_by_event[event.id], EventStatus.SUCCESS,
                    ActiveStatus.LINKED if t == "L" else ActiveStatus.CUT,
                    "Initialize", 0.0, events_file=False)
                cascade.apply_event(event, old, rc, fc, 0.0)
        for node in model.nodes_by_id.values():
            if node.type_node == 5:
                if node.uses_phasing and node.phase:
                    v = node.phase[0]
                    if v in (-1, 0):
                        cascade._cascade_node(
                            node, node.status, 0.0,
                            new_active_status=ActiveStatus.CUT if v == -1 else ActiveStatus.LINKED)
            elif node.type_node != 1 and node.good_paths > len(node.in_paths_array):
                result = reconfigure_node(
                    node, model.links_by_id, 0, 0, 0, 0, 0, 0, 0.0, events_file=False,
                    hier_name=cascade._hier_names_by_node_id.get(node.id),
                    sb_link_rng=cascade.sb_link_rng,
                    on_link_status_change=cascade._mark_cap_link_change,
                    mark_cold_changed=cascade._mark_node_cold_changed)
                if result is not None:
                    cascade._cascade_node(node, result.new_status, 0.0)
            if node.status == NodeStatus.DOWN:
                back_flow_dependencies_from_node(
                    "Down", node, cascade.backflow_deps(), 0.0)

    def _transition_for(self, block: RBDBlock):
        stats = self.ctx.stats_by_block[block.id]

        def transition(new_status, new_active, comment, sim_time, *,
                       phase_stress=None, top_level=True):
            old_op = block.op_status
            old_active = block.active_status
            _logs, deltas = change_block_state(
                block, stats, new_status, new_active, comment, 0.0,
                events_file=False, phase_stress=block.run_state.phase_stress,
                cost_analysis=False, weak_analysis=False)
            self.cascade.apply(block, old_op, deltas, 0.0, top_level=top_level,
                               old_active_status=old_active)
        return transition

    # ---- state ----------------------------------------------------------

    @property
    def system_color(self) -> SystemColor:
        return self.cascade.system_state.color

    @property
    def blocks(self) -> list[RBDBlock]:
        return list(self.model.blocks_by_id.values())

    def block_by_name(self, name: str) -> RBDBlock:
        for b in self.model.blocks_by_id.values():
            if b.name == name:
                return b
        raise KeyError(name)

    def _analyze(self) -> None:
        """`CheckColds; AnalyzeSystem`."""
        self.cascade._flush_cold_changes(0.0)
        self.cascade._check_system_status(0.0)

    # ---- hatching -------------------------------------------------------

    def _mark_hatched(self, block: RBDBlock) -> None:
        block.run_state.fev_induced_by_dependency = True
        if block not in self.fev_dep_group:
            self.fev_dep_group.append(block)

    def _unhatch_all(self) -> None:
        for b in self.fev_dep_group:
            b.run_state.fev_induced_by_dependency = False
        self.fev_dep_group.clear()

    # ---- transitions ----------------------------------------------------

    def _set_block(self, block: RBDBlock, new_status: BlockStatus, *,
                   top_level: bool = True) -> None:
        self.cascade.transitions[block.id](
            new_status, block.active_status, "", 0.0, top_level=top_level)

    def _set_event(self, event: RBDEvent, new_status: EventStatus) -> None:
        old = event.op_status
        _logs, rc, fc = change_event_state(
            event, self.ctx.stats_by_event[event.id], new_status, event.active_status,
            "", 0.0, events_file=False)
        self.cascade.apply_event(event, old, rc, fc, 0.0)

    # ---- actions --------------------------------------------------------

    def click_block(self, block_id: int) -> FevDelta:
        """A left click on a block."""
        b = self.model.blocks_by_id[block_id]
        if b.run_state.fev_induced_by_dependency:
            self._cycle_hatched(b)
        else:
            self._unhatch_all()
            if b.op_status == BlockStatus.RUNNING or (
                    b.op_status == BlockStatus.STANDBY and b.sb_stress > 0.0):
                self.criticality = 1.0
                self._set_block(b, BlockStatus.REPAIRING)
            elif b.op_status in (BlockStatus.REPAIRING, BlockStatus.PM):
                self._set_block(b, BlockStatus.RUNNING)
        self._analyze()
        return self._delta(f"click {b.name}", b)

    def _cycle_hatched(self, b: RBDBlock) -> None:
        """Click on a hatched (induced) block: the next dependency outcome whose
        percentage is above zero, in Raptor 7's fixed order."""
        b.ignore_dep = True
        try:
            st = b.op_status
            run, rep, pm, idle = (BlockStatus.RUNNING, BlockStatus.REPAIRING,
                                  BlockStatus.PM, BlockStatus.IDLE)
            fail_p, pm_p, idle_p, none_p = (b.dep_fail_percent, b.dep_pm_percent,
                                            b.dep_idle_percent, b.dep_nothing_percent)
            if st == run:
                if fail_p > 0.0:
                    self._set_block(b, rep)
                elif pm_p > 0.0:
                    self._set_block(b, pm)
                elif idle_p > 0.0:
                    self._set_block(b, idle)
            elif st == rep:
                if pm_p > 0.0:
                    self._set_block(b, run); self._set_block(b, pm)
                elif idle_p > 0.0:
                    self._set_block(b, idle)
                elif none_p > 0.0:
                    self._set_block(b, run)
            elif st == pm:
                if idle_p > 0.0:
                    self._set_block(b, run); self._set_block(b, idle)
                elif none_p > 0.0:
                    self._set_block(b, run)
                elif fail_p > 0.0:
                    self._set_block(b, run); self._set_block(b, rep)
            elif st == idle:
                if none_p > 0.0:
                    self._set_block(b, run)
                elif fail_p > 0.0:
                    self._set_block(b, run); self._set_block(b, rep)
                elif pm_p > 0.0:
                    self._set_block(b, run); self._set_block(b, pm)
        finally:
            b.ignore_dep = False

    def click_event(self, event_id: int) -> FevDelta:
        e = self.model.events_by_id[event_id]
        self._unhatch_all()
        if e.op_status == EventStatus.SUCCESS:
            self.criticality = 1.0
            self._set_event(e, EventStatus.FAILURE)
        elif e.op_status == EventStatus.FAILURE:
            self._set_event(e, EventStatus.SUCCESS)
        self._analyze()
        return self._delta(f"click {e.name}", None)

    # ---- phases ---------------------------------------------------------

    def goto_phase(self, new_phase: int) -> FevDelta:
        """`FEVPhaseChange` from the toolbar's phase
        arrows. Hatching is NOT cleared: only the eraser does that."""
        if not self.phased:
            raise EngineInvariantError("goto_phase on a model with no phases")
        if not 1 <= new_phase <= len(self.model.phases):
            raise ValueError(f"phase {new_phase} is not in 1..{len(self.model.phases)}")
        self._fev_phase_change(new_phase)
        self._analyze()
        return self._delta(f"phase {new_phase}", None)

    def next_phase(self) -> FevDelta:
        """The forward arrow wraps from the last phase to the first."""
        n = len(self.model.phases)
        return self.goto_phase(1 if self.phase_number >= n else self.phase_number + 1)

    def previous_phase(self) -> FevDelta:
        """The back arrow wraps from the first phase to the last."""
        n = len(self.model.phases)
        return self.goto_phase(n if self.phase_number <= 1 else self.phase_number - 1)

    def refresh_phase(self) -> FevDelta:
        """The small eraser (`RefreshPhase`): clear hatching, then every
        Repairing / PM block runs again and every failed event succeeds. The phase
        stays."""
        self._unhatch_all()
        for b in self.model.blocks_by_id.values():
            if b.op_status in (BlockStatus.REPAIRING, BlockStatus.PM):
                self._set_block(b, BlockStatus.RUNNING, top_level=False)
        for e in self.model.events_by_id.values():
            if e.op_status == EventStatus.FAILURE:
                self._set_event(e, EventStatus.SUCCESS)
        self._analyze()
        return self._delta("refresh phase", None)

    def _fev_phase_change(self, new_phase: int) -> None:
        model, cascade = self.model, self.cascade
        old_phase = self.phase_number
        self.phase_number = new_phase
        cascade.system_state.current_phase = new_phase

        blocks: dict[str, list] = {"improve": [], "degrade": [], "stress": []}
        events: dict[str, list] = {"improve": [], "degrade": []}
        for b in model.blocks_by_id.values():
            if not b.uses_phasing or not b.phase_type:
                continue
            ot, nt = b.phase_type[old_phase - 1], b.phase_type[new_phase - 1]
            if (b.phase_value[old_phase - 1] != b.phase_value[new_phase - 1]) or ot != nt:
                g = classify_block_phase_transition(ot, nt)
                if g is not None:
                    blocks[g].append((b, ot, nt))
        for e in model.events_by_id.values():
            if not e.uses_phasing or not e.phase_type:
                continue
            ot, nt = e.phase_type[old_phase - 1], e.phase_type[new_phase - 1]
            is_failure = e.op_status == EventStatus.FAILURE
            if (e.phase_value[old_phase - 1] != e.phase_value[new_phase - 1]) or ot != nt:
                g = classify_event_phase_transition(ot, nt, is_failure)
            else:
                g = classify_event_same_value_refire(ot, is_failure)
            if g is not None:
                events[g].append((e, ot, nt))
        nodes: dict[str, list] = {"improve": [], "degrade": []}
        for n in model.nodes_by_id.values():
            if not n.uses_phasing or not n.phase:
                continue
            in_path_count = len(n.in_paths_array)
            raw_old, raw_new = n.phase[old_phase - 1], n.phase[new_phase - 1]
            g = classify_node_phase_transition(raw_old, raw_new, in_path_count)
            if g is not None:
                nodes[g].append(
                    (n, in_path_count + 1 if raw_new == -1 else raw_new))

        def apply_things(group: list) -> None:
            for thing, ot, nt in group:
                if isinstance(thing, RBDEvent):
                    self._apply_event_phase(thing, ot, nt)
                else:
                    self._apply_block_phase(thing, ot, nt)

        def apply_nodes(group: list) -> None:
            for n, new_val in group:
                cascade.phase_apply_node(n, new_val, 0.0, events_file=False)

        if self.system_color in (SystemColor.GREEN, SystemColor.YELLOW):
            apply_things(blocks["improve"] + events["improve"])
            apply_nodes(nodes["improve"])
            apply_things(blocks["stress"])
            apply_things(blocks["degrade"] + events["degrade"])
            apply_nodes(nodes["degrade"])
        else:
            apply_things(blocks["degrade"] + events["degrade"])
            apply_nodes(nodes["degrade"])
            apply_things(blocks["stress"])
            apply_things(blocks["improve"] + events["improve"])
            apply_nodes(nodes["improve"])

    def _apply_block_phase(self, b: RBDBlock, old_type: str, new_type: str) -> None:
        """`RBDBlockObj.ChangePhases`."""
        if old_type != new_type:
            active = {"C": ActiveStatus.CUT, "L": ActiveStatus.LINKED,
                      "A": ActiveStatus.ACTIVE}[new_type]
            self.cascade.transitions[b.id](
                b.op_status, active, "Phase_Change", 0.0, top_level=False)

    def _apply_event_phase(self, e: RBDEvent, old_type: str, new_type: str) -> None:
        """`RBDEventObj.ChangePhases`; in FEV
        `ConductEvent` does not draw: a Success event stays Success, anything else
        stays Failure."""
        if old_type == new_type and old_type not in ("F", "A"):
            return
        if new_type in ("F", "A"):
            status = (EventStatus.SUCCESS if e.op_status == EventStatus.SUCCESS
                      else EventStatus.FAILURE)
            active = ActiveStatus.ACTIVE
        else:
            new_active = {"C": ActiveStatus.CUT, "L": ActiveStatus.LINKED}.get(new_type)
            if new_active is None:
                new_active = ActiveStatus.ACTIVE if old_type in ("C", "L") else None
            if new_active is None:
                return
            active = new_active
            status = e.op_status
        old = e.op_status
        _logs, rc, fc = change_event_state(
            e, self.ctx.stats_by_event[e.id], status, active, "Phase_Change", 0.0,
            events_file=False)
        self.cascade.apply_event(e, old, rc, fc, 0.0)

    def refresh_all(self) -> FevDelta:
        """The large eraser: everything operating, phase 1 (`RefreshAllFEV`)."""
        self._start_state()
        return self._delta("refresh all", None)

    def _delta(self, action: str, block: RBDBlock | None) -> FevDelta:
        text = block.op_status.value if block is not None else ""
        return FevDelta(action, self.system_color,
                        tuple(b.name for b in self.fev_dep_group),
                        self.criticality, text)

    # ---- inspection (tests, UI) ----------------------------------------

    def snapshot(self) -> dict[str, Any]:
        """Every piece of state a click can change, for round-trip tests."""
        m = self.model
        return {
            "blocks": {b.id: (b.op_status, b.active_status, b.run_state.fev_induced_by_dependency)
                       for b in m.blocks_by_id.values()},
            "events": {e.id: (e.op_status, e.active_status)
                       for e in m.events_by_id.values()},
            "nodes": {nid: (n.status, n.active_status, n.good_paths)
                      for nid, n in m.nodes_by_id.items()},
            "links": {lid: (l.status, l.active) for lid, l in m.links_by_id.items()},
            "hiers": {hid: (h.status, h.num_currently_failed)
                      for hid, h in m.hiers_by_id.items()},
            "system": (self.system_color, self.cascade.system_state.system_up,
                       self.cascade.system_state.num_currently_failed),
            "hatched": tuple(b.id for b in self.fev_dep_group),
            "criticality": self.criticality,
            "phase": self.phase_number,
        }
