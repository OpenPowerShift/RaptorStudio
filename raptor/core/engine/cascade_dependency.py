"""The cascade's dependency dispatch: delivering a depended-upon object's
change to its dependent blocks (`BackFlowDependencies`), with source's
`IF FailWait ... ELSIF ZeroWait` delivery gate, and `ResendBackFlow`.

These are `_Cascade` methods, bound in the class body
(`name = cascade_dependency.name`); each takes the cascade as `self`.
"""
from __future__ import annotations

from typing import TYPE_CHECKING

from raptor.core.engine import dependency
from raptor.core.engine.dependency import draw_dependency_action
from raptor.core.engine.interrupts import InterruptReason
from raptor.core.model import BlockStatus, RBDBlock
from raptor.core.engine.system_status import MissionStatus
from raptor.core.scheduler import Owner, Process

if TYPE_CHECKING:
    from raptor.core.engine.orchestrator import _Cascade


def _dispatch_dependency_wait_gated(self: _Cascade, dep_block: RBDBlock, new_status, comment: str,
                                    sim_time: float) -> None:
    """`_dispatch_dependency` with Raptor 7's own wait-site gate applied
    to the wake: wake only if the
    target is in `FailWait` (interrupt) or `ZeroWait` (release), otherwise
    nothing happens. A block can pass `BackFlowDependencies`' status gate
    yet be sitting in neither wait; source leaves those alone. Used by
    `resend_back_flow`."""
    self._dispatch_dependency(dep_block, new_status, comment, sim_time,
                              wait_gated=True)


def _dispatch_dependency(self: _Cascade, dep_block: RBDBlock, new_status, comment: str, sim_time: float,
                         *, wait_gated: bool = False) -> None:
    # ChangeBlockState fires before Interrupt, matching Raptor 7's
    # BackFlowDependencies: apply the state change and its full cascade
    # first, then wake the dependent block's suspended process.
    #
    # `was_standby`: source sets the interrupt reason before its
    # ChangeBlockState ( reads the pre-transition status), so
    # a Standby dependent promoted by the AllUp branch gets
    # "Dependence_SB_turnOn". Captured pre-transition rather than
    # reordering the reason-set itself, which would let nested cascades
    # observe the new reason where source still has the old one.
    was_standby = dep_block.op_status == BlockStatus.STANDBY
    # `ChangeBlockState` logs `Stress=`+the block's own persisted
    # `PhaseStress` FIELD, which only `Run`'s own
    # top-of-loop and init ever write -- NOT a
    # value recomputed for the current phase. At a phase-boundary
    # instant, a block idled by `Phase_Change` and immediately
    # recovered by dependency still holds the PREVIOUS phase's value
    # (its last Run pass ran in that phase): Raptor 7 logs
    # `DependenceStress=0.500000` there, the old phase's stress. This
    # used to recompute `combined_stress(...)` for the current phase
    # instead, printing the new phase's value (1.0) on every such
    # line -- 880/1049 of `Phasing1`'s and 1753/2310 of `Phasing3`'s
    # `DependenceStress=` lines were wrong. Nothing tests event-log
    # text for those fixtures (sd_diff compares draws, fidelity
    # compares reports), so it went unnoticed until the 2026-09-23
    # Phasing1-clone probe captures diffed it line by line.
    _stress = dep_block.run_state.phase_stress
    self.transitions[dep_block.id](
        new_status, dep_block.active_status, comment, sim_time, top_level=False,
        phase_stress=_stress)
    if new_status == BlockStatus.STANDBY:
        # Source's `BackFlowDependencies(NodeStandby)` sets
        # `Dependence_Standby` for a dependent
        # switched off into Standby.
        reason = InterruptReason.DEPENDENCE_STANDBY
    elif new_status == BlockStatus.IDLE:
        reason = InterruptReason.DEPENDENCE_IDLE
    elif was_standby:
        # -- a Standby dependent recovering via AllUp gets
        # its own interrupt reason. The Run dispatch treats every
        # "Dependence*" prefix identically except the startup-failures
        # SB_turnOn draw, so this is observable only through that
        # feature, but the distinction is source-faithful.
        reason = InterruptReason.DEPENDENCE_SB_TURN_ON
    else:
        reason = InterruptReason.DEPENDENCE_ALL_UP
    # Port of `SetInterruptReason` -- a real tracked field, consulted by
    # `dependency.py`'s `returning_from_maintenance` branch.
    dep_block.run_state.interrupt_reason = reason
    dep_block.run_state.reason_overwrites = dep_block.run_state.reason_overwrites + 1
    # `dep_block.id` may not have a scheduler handle yet: a kickoff-time
    # cascade can reach a block whose own process has not been scheduled
    # yet. Nothing is suspended for such a block, so there is nothing to
    # interrupt -- the state transition above already applied the real
    # effect, and the block's generator observes it once it starts.
    if dep_block.id in self.handles:
        # Skip a redundant interrupt for a block this same cascade
        # already queued a `"Pool_Available"` interrupt for. The
        # scheduler's per-handle generation counter stales out an earlier
        # queued interrupt, so sending a second one would discard the
        # earlier (lower-seq) entry and delay this block's resumption
        # behind other wake_ids siblings that do not share this overlap.
        # The transition above still runs either way.
        if dep_block.run_state.no_wake_once:
            pass  # see dependency.py's returnFromMaint arm: no Interrupt(Run)
        elif dep_block.run_state.pm_tell_pending:
            pass  # Run has TELLed PerformPM: neither FailWait nor ZeroWait
        elif dep_block.run_state.waiting_for_pm_prerequisites:
            pass  # mid ObtainPMprereqs: neither FailWait nor ZeroWait (Capacity1 run 4 t=314.058549, events34)
        elif dep_block.id in self._pool_woken_this_turn:
            self._pool_woken_this_turn.discard(dep_block.id)
            if dep_block.run_state.zero_wait and reason == InterruptReason.DEPENDENCE_ALL_UP:
                # Literal: Raptor 7's AllUp creates this block's
                # BlockTrigger.Release record NOW (before later-created
                # records); the port's one generator can't hold it beside
                # the pending Give wake, so reserve its slot in the
                # Owner.block_trigger(id) group with a placeholder the block's own
                # hand-back requeue will take over.
                def _bt_slot() -> Process:
                    yield 1.0e9
                dep_block.run_state.trigger_slot_handle = self.scheduler.schedule_process(
                    _bt_slot(), owner_key=Owner.block_trigger(dep_block.id)).id
        elif wait_gated and not (dep_block.run_state.fail_wait or dep_block.run_state.zero_wait):
            # Source's own `IF FailWait ... ELSIF ZeroWait ... END IF`
            # falls through with NO wake when the target is in neither
            # wait -- see `_dispatch_dependency_wait_gated`'s docstring.
            pass
        else:
            self.scheduler.interrupt(self.handles[dep_block.id], reason)


def _induce_dependency(self: _Cascade, dep_block: RBDBlock, reason: str) -> bool:
    # `GoToPM`'s guard: `usesPM AND NOT(pmMisDefer AND missionStatus=Mission AND
    # activePhases>0)`. A mission-deferring block is not sent for opportunistic PM during a mission phase
    # (`LadL6Full` run 2 t=2476.553210: no `PM_is_due OBPM`).
    if (reason == InterruptReason.OBPM and dep_block.pm_mis_defer and self.model.phases
            and self.system_state.mission_status is MissionStatus.MISSION):
        return False
    # Unlike `_dispatch_dependency`, no transition happens here --
    # matching Raptor 7's bare `SetInterruptReason` +
    # `Interrupt`: the target block's own suspended generator decides what
    # `"OBPM"`/`"Induced_Failure"` means. Sets `_induced_wake_pending` so
    # the dispatching cascade's own top-level check defers to the woken
    # block's later one.
    self._induced_wake_pending = True
    # Port of `SetInterruptReason`, which
    # `dependency.py`'s `returning_from_maintenance` branch consults. Without it a
    # stale flag would be treated as "ready to re-fire" the next time the
    # dependency cycles, instead of no-op'ing because the original
    # interrupt was never consumed.
    dep_block.run_state.interrupt_reason = reason
    dep_block.run_state.reason_overwrites = dep_block.run_state.reason_overwrites + 1
    # See `_dispatch_dependency`'s identical guard: a kickoff-time
    # cascade can reach a block with no scheduler handle yet, and nothing
    # is suspended to interrupt.
    #
    # `fail_wait or zero_wait`: port of source's own `IF tempBlock.
    # FailWait: Interrupt(tempBlock,"Run") ELSIF tempBlock.ZeroWait:
    # Release END IF` (no `ELSE`). A block
    # mid-`ObtainPMprereqs` (neither flag set, suspended elsewhere) never
    # receives this interrupt in source; `interrupt_reason` is still set
    # unconditionally as bookkeeping it never consults.
    #
    # A dependence-idled-then-repromoted block sits back in its ordinary
    # fail-wait in real but can resume through scheduler hops here without
    # the transient fail_wait flag still set, so RUNNING with a handle is
    # accepted too. `dep_block.run_state.waiting_for_pm_prerequisites` is the source-mirrored
    # signal for "genuinely non-deliverable right now": source's own
    # `WaitingForPrereqs` brackets the two ObtainPMprereqs TELL-boundary
    # yields.
    delivered = dep_block.id in self.handles and not dep_block.run_state.waiting_for_pm_prerequisites and (
        dep_block.run_state.fail_wait or dep_block.run_state.zero_wait or dep_block.op_status == BlockStatus.RUNNING)
    if delivered:
        # Plain FIFO interrupt order (no `late` tag): the traces show real
        # serving interrupts in the order sent, with a re-interrupt a
        # no-op (see `Scheduler.interrupt`). The old `late` tag was
        # compensating for that rule and for the extra CheckColds flush
        # (the internal engine ledger, 2026-09-25).
        self.scheduler.interrupt(self.handles[dep_block.id], reason)
    return delivered


def _draw_dependency_action(self: _Cascade, dep_block: RBDBlock, sim_time: float) -> str:
    return draw_dependency_action(dep_block, self.rng_fail_by_block[dep_block.id], sim_time)


def resend_back_flow(self: _Cascade, sim_time: float) -> None:
    # Port of `ResendBackFlow`'s two call sites (`CheckColds`,
    # Raptor 7, and `DetermineNewState`'s "Maintenance
    # Completed" branch). Bound as a plain callback rather than
    # threading `dependency.resend_back_flow`'s many args through every
    # caller.
    # `top_level=False`: this dispatch runs inside `_flush_cold_changes`,
    # itself nested under whatever transition triggered the flush; a
    # resend-driven transition defaulting to top level would re-enter
    # `_flush_cold_changes` before this call returns.
    dependency.resend_back_flow(
        self._all_blocks, self.model.nodes_by_id, self.model.links_by_id,
        self.end_node.id if self.end_node else None,
        # `_dispatch_dependency_wait_gated`, not a bare transition lambda.
        # `ResendBackFlow`'s `specialBackFlow` replay
        # ends both arms with
        # `SetInterruptReason("Dependence_Idle")` plus the FailWait/
        # ZeroWait gate, so a re-idled block is woken; the gate matters as
        # much as the wake, since many resend transitions target a block
        # in neither wait, which source leaves untouched.
        self._dispatch_dependency_wait_gated,
        sim_time, draw_action=self._draw_dependency_action, induce=self._induce_dependency,
        fev=self.fev,
    )
