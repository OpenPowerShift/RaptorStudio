"""The cascade's cold-standby handling: running `ColdLogic` when a
cold-standby node's paths change, interrupting switches in flight, and
flushing the changed-cold group (`CheckColds`).

These are `_Cascade` methods, bound in the class body
(`name = cascade_cold.name`); each takes the cascade as `self`.
"""
from __future__ import annotations

from typing import TYPE_CHECKING

from raptor.core.engine.cold_standby import perform_switch_process, run_cold_logic
from dataclasses import replace

from raptor.core.engine.dependency import back_flow_dependencies_from_node
from raptor.core.engine.errors import require
from raptor.core.engine.interrupts import InterruptReason
from raptor.core.engine.nodes import reconfigure_node
from raptor.core.model import Link, RBDBlock, RBDNode

if TYPE_CHECKING:
    from raptor.core.engine.orchestrator import _Cascade


def apply_cold_logic(self: _Cascade, node: RBDNode, sim_time: float) -> None:
    """Runs `cold_standby.run_cold_logic` for `node` and, if its status
    changed, feeds the result through the same downstream/dependency
    cascade (`_cascade_node`) every other node-status change goes
    through. `run_cold_logic` computes the new status and any block
    transitions it causes, matching `reconfigure_node`'s split between
    computation and cascade application."""
    if node.id in self._cold_logic_in_progress:
        # Re-entrant call from a block transition `run_cold_logic`'s own
        # loop drove through this pipeline; the outer call owns this
        # node's aggregate/log line.
        return
    self._cold_logic_in_progress.add(node.id)
    try:
        def transition(block: RBDBlock, new_op_status, comment: str, t: float) -> None:
            # top_level=False: this transition's own system-status check
            # must not fire yet -- `run_cold_logic` hasn't finished
            # computing this node's aggregate status and downstream nodes
            # haven't been re-cascaded. `apply_cold_logic`'s own
            # `_cascade_node` call eventually triggers the one correct,
            # fully-settled check.
            self.transitions[block.id](new_op_status, block.active_status, comment, t, top_level=False)

        def spawn_switch(link: Link) -> None:
            block = self.model.blocks_by_id[link.connect_from_id]
            # `spawn_status`: the reference point `perform_switch_process`'s
            # ON INTERRUPT branch compares against at delivery time, to
            # decide whether a redraw is warranted.
            def _rerun_cold_logic(n: RBDNode = node) -> None:
                self._run_cold_logic_now(n, self.scheduler.time)
            handle = self.scheduler.tell(perform_switch_process(
                link, block, _rerun_cold_logic,
                require(self.sb_link_rng, "sb_link_rng"), link.status,
            ), deferred=True)
            self._switch_handles[link.object_num] = handle

        # Bundle built ONCE and shared by both back-flow arms below (review
        # E-09) -- they were the same eight expressions written out twice.
        deps = self.backflow_deps()

        cold_result = run_cold_logic(
            node, self.model.links_by_id, self.model.blocks_by_id, transition, sim_time,
            events_file=True, interrupt=self._switch_interrupt,
            end_node_id=self.end_node.id if self.end_node else None, spawn_switch=spawn_switch,
            skip_backflow=self._skip_backflow,
            nodes_by_id=self.model.nodes_by_id,
            system_state=self.system_state,
            hiers_by_id=self.model.hiers_by_id,
            interrupt_link=self._interrupt_switch,
            backflow_all_up=lambda n: back_flow_dependencies_from_node(
                "AllUp", n, deps, sim_time,
            ),
            emit_log=self.logs.append,
            backflow_down=lambda n, special: back_flow_dependencies_from_node(
                "Down", n, replace(deps, special_back_flow=special), sim_time,
            ),
        )
        if cold_result is None:
            # `RunColdLogic`'s own `ELSE ReconfigureNode(0,0,0,0,0,0)`
            # arm, taken whenever `higherCold`
            # is set. All-zero deltas with `inPath=0`, so the node re-reads
            # its links, logs the ordinary `"N-K/M_..."` line, and no
            # switching happens.
            result = reconfigure_node(
                node, self.model.links_by_id, 0, 0, 0, 0, 0, 0, sim_time,
                events_file=True, hier_name=self._hier_names_by_node_id.get(node.id),
                on_link_status_change=self._mark_cap_link_change,
                mark_cold_changed=self._mark_node_cold_changed,
                interrupt_switch=self._interrupt_switch_from_reconfigure,
            )
            if result is not None and result.log_line is not None:
                self.logs.append(result.log_line)
                if result.new_status != node.status:
                    self._cascade_node(node, result.new_status, sim_time)
            return
        cold_logs, new_status, _stressed_blocks = cold_result
        self.logs.extend(cold_logs)
        if new_status != node.status:
            self._cascade_node(node, new_status, sim_time)
            # Does not call `_check_system_status` here: `apply_cold_logic`
            # is reached from `_flush_cold_changes`'s loop, which can call
            # it multiple times for the same node across batches in one
            # flush. Every caller of `_flush_cold_changes` already does its
            # own single check right after the flush returns. The one
            # non-flush caller, `BackFlowDependencies(AllUp)`'s inline
            # `RunColdLogic`, wants the same thing: source's `RunColdLogic`
            # ends at `ChangeNodeState` and checks nothing either.
            #
            # Does not call `resend_back_flow` here either: `_flush_cold_
            # changes` (this project's CheckColds) calls it itself, once,
            # after every affected node has been processed.
    finally:
        self._cold_logic_in_progress.discard(node.id)


def kickoff_cold_logic(self: _Cascade, node: RBDNode, sim_time: float) -> None:
    """Port of the per-run cold-standby kickoff, source's own bare
    `CheckColds`. Marks this node affected and drains
    through `_flush_cold_changes` (which IS `CheckColds`), giving the node
    source's in-group-during-its-own-pass property. No system-status
    check: source's is a bare `CheckColds`."""
    self._cold_change_group.setdefault(node.id)
    self._flush_cold_changes(sim_time)


def _mark_node_cold_changed(self: _Cascade, node: RBDNode) -> None:
    """`nodes.reconfigure_node`'s `mark_cold_changed` callback -- port of
    `ColdChangeGroup.Add(SELF)` for the
    downstream node->node cascade, which has no other cold-standby
    handling. Unguarded, exactly like source; a node can re-queue itself
    from its own pass, which produces Raptor 7's second no-change survey line.
    `_flush_cold_changes`'s remove-after-process drain prevents looping."""
    self._cold_change_group.setdefault(node.id)


def _interrupt_switch(self: _Cascade, link: Link, reason: str = InterruptReason.PERFORM_SWITCH) -> None:
    """Port of `Interrupt(link,"PerformSwitch")` -- queued, not
    synchronous. The link's `perform_switch_process` resumes with
    `("interrupt", "PerformSwitch")`, re-draws a fresh `SetSwitchTime`,
    then runs its tail (marking `switch_completed` and calling
    `run_once`). A no-op if this link has no switch in flight.

    `run_cold_logic`'s own two `interrupt_link` sites call this directly.
    `reconfigure_node`'s newer `interrupt_switch` hook goes through
    `_interrupt_switch_from_reconfigure` instead, which adds a
    re-entrancy guard this direct method must not have."""
    handle = self._switch_handles.pop(link.object_num, None)
    if handle is not None:
        # The `SetSwitchTime` redraw belongs to the link's own `ON INTERRUPT` arm, so it happens when the interrupted
        # record is DELIVERED (`perform_switch_process`), after the rest of the issuing turn. `LadL5Full` (real
        # 2026-10-01, run 44 t=3005.14): link 26's draw (a block demoted later in the issuing turn) precedes the
        # interrupted link 20's. An earlier "draw at issue time" rule (`CS_MidSwitchDemote`) fit a capture whose
        # ordering other, since fixed, differences distorted; with the draw at delivery that capture is exact too.
        self.scheduler.interrupt(handle, reason)


def _interrupt_switch_from_reconfigure(self: _Cascade, link: Link) -> None:
    """`nodes.reconfigure_node`'s `interrupt_switch` callback -- see
    `_interrupt_switch` for the port itself; this wraps it with two
    guards `reconfigure_node`'s calling context needs but `run_cold_logic`'s
    direct calls must not have.

    (1) Suppressed while `link.connect_to_id`'s own `run_cold_logic` pass
    is on the stack (`_cold_logic_in_progress`), for a switch this same
    pass just spawned being reflected back through a nested
    `reconfigure_node` call.

    (2) Suppressed if this link already had an interrupt fire at this
    exact `sim_time` (`_switch_interrupted_at`), preventing extra
    interrupts on a switch cycle that is already being rescheduled and
    keeping the RNG stream correct on fixtures with a fractional AProb.

    The actual single-use guarantee lives in `_switch_handles.pop` inside
    `_interrupt_switch`: only the first interrupt against a switch's
    in-flight lifetime finds a live handle, and `Scheduler.interrupt`'s
    queued delivery lets `perform_switch_process` decide at the fully
    settled moment whether a redraw is warranted."""
    if link.object_num in self._switch_interrupted_at and \
            self._switch_interrupted_at[link.object_num] == self.scheduler.time:
        return
    if link.connect_to_id in self._cold_logic_in_progress:
        return
    self._switch_interrupted_at[link.object_num] = self.scheduler.time
    self._interrupt_switch(link, self._SWITCH_IRQ_RECONFIGURE)


def _run_cold_logic_now(self: _Cascade, node: RBDNode, sim_time: float) -> None:
    """Port of `PerformSwitch`'s own tail: mark
    `node` affected, flush immediately, then check system status once
    after the flush settles. A genuinely top-level, scheduler-driven
    completion, matching source calling `CheckColds` directly rather than
    going through `apply`'s deferred marking."""
    self._cold_change_group.setdefault(node.id)
    self._flush_cold_changes(sim_time)
    # `PerformSwitch` ends in `CheckColds`, which has no `AnalyzeSystem` of its own: the system is analysed at the
    # next `TimeAdvance` only if some `Change*State` marked `SystemStateChange` meanwhile. A switch that merely
    # re-logged a node (`J3 Bad ..._s21`, Bad -> Bad) must not log a `Red_change` (`LadL6Full` run 1 t=5262.696644).
    if self._system_state_changed:
        self._schedule_or_check_system_status(sim_time)


def flush_cold_changes(self: _Cascade, sim_time: float) -> None:
    """`CascadePort.flush_cold_changes`: `_flush_cold_changes`."""
    self._flush_cold_changes(sim_time)


def _flush_cold_changes(self: _Cascade, sim_time: float) -> None:
    """Port of `CheckColds` -- drains
    `_cold_change_group`, running `run_cold_logic` (via `apply_cold_logic`)
    for every node marked since the last flush. If any node's pass
    produced a real change, calls `resend_back_flow` once for the whole
    batch. `resend_back_flow` can mark more nodes, so the outer loop
    repeats until a whole pass produces no new marks.

    A no-op whenever nothing is marked, and re-entrancy-safe: a nested
    call just returns, leaving its marks for the outer call's loop."""
    if self._flushing_cold_changes:
        return
    self._flushing_cold_changes = True
    try:
        completed = False
        while not completed:
            some_colds_changed = False
            while self._cold_change_group:
                some_colds_changed = True
                # Source removes each node after its own `RunColdLogic`
                # returns, not before, and every
                # `Add` is guarded by `IF NOT Includes(SELF)`. A node still
                # in the group while its own pass runs cannot re-queue
                # itself, but the same self-mark from a pass run outside
                # this drain does queue it -- producing Raptor 7's second,
                # no-change survey line.
                for node_id in list(self._cold_change_group):
                    self.apply_cold_logic(self.model.nodes_by_id[node_id], sim_time)
                    self._cold_change_group.pop(node_id, None)
            completed = True
            if some_colds_changed:
                # Port of `skipBackflow:=TRUE`: set
                # for the rest of this flush so a revived-then-re-idled
                # candidate cannot ping-pong. Reset once the whole flush
                # finishes.
                self._skip_backflow = True
                self.resend_back_flow(sim_time)
            if self._cold_change_group:
                completed = False
    finally:
        self._flushing_cold_changes = False
        self._skip_backflow = False


def _switch_interrupt(self: _Cascade, block: RBDBlock, reason: str) -> None:
    # Bare-interrupt shape: the state change already happened via
    # `transition` before this is called; this just wakes the block's
    # suspended process so it recomputes `CombinedStress` against the new
    # stress rate. See `block_lifecycle.block_run_process`'s
    # `"Switched_On"`/`"Switched_Off"` handling.
    #
    # Cooperative variant: when the interrupt targets the block whose own
    # generator turn is executing right now, queuing a scheduler interrupt
    # would deliver to a frame already past its old wait and process the
    # switch state twice. Since the in-flight turn already sees the
    # switched state on its natural continuation, so there is nothing to
    # deliver: the generator's next suspension honors it implicitly.
    handle_id = self.handles[block.id].id
    # Membership, not top-of-stack: the target block's own turn may be an
    # enclosing frame while a process it `tell`ed runs.
    if self.scheduler.is_executing(handle_id):
        return
    # Source's `IF block.FailWait: Interrupt(block,"Run") ELSIF block.ZeroWait: BlockTrigger.Release` with no ELSE
    #: a block in neither wait (a repaired block whose `Run` TELL is still pending) is
    # not interrupted, and its pending record keeps its place (`LadL7HierFull` run 41 t=3197.827021).
    if not (block.run_state.fail_wait or block.run_state.zero_wait):
        return
    self.scheduler.interrupt(self.handles[block.id], reason)
