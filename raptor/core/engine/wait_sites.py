"""The places an ordinary block's process can be woken.

Each `WaitSite` is one `resume = yield ...` in `BlockProcess`: a `WAIT` in
source that an interrupt (`Scheduler.interrupt`, delivered as a `Wake`)
can end early. Yields whose resumption is ignored (the waits to the end of
the run, and timed hops nobody interrupts) are not listed.

`WAKE_POLICY` says, for each site, what may end the wait: its full length
(the timer) and which interrupt reasons. It is the contract a reviewer
checks against Raptor 7, and the rows of the
`WaitSite x InterruptReason` coverage matrix:
`tools/state_coverage.py` records which reasons actually reach
each site across the corpus, writes `docs/engine-design/state-coverage.adoc`,
and fails if a model reaches a cell the policy does not allow.
"""
from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum

from raptor.core.engine.interrupts import KICKOFF_DEPENDENCE_WAKES, STATE_ALREADY_APPLIED, InterruptReason


class WaitSite(StrEnum):
    """One wait in `BlockProcess` that an interrupt can end."""

    #: `run`: the kickoff barrier at t=0, before the first stress
    #: computation (port-only hop inside source's `Run` TELL start).
    KICKOFF = "kickoff"
    #: `_idle_wait`: the Idle / ZeroWait wait at the top of `Run`
    #: (`ZeroWait:=TRUE; WAIT FOR BlockTrigger[seqNum] TO Fire`,
    #: Raptor 7).
    IDLE = "idle"
    #: `_run_turn`: the ZeroWait wait for a block whose combined stress is
    #: zero.
    ZERO_STRESS = "zero_stress"
    #: `_cold_path_redraw`: the `WAIT DURATION 0.0` after PARK A's release
    PARK_A_HOP = "park_a_hop"
    #: `_fail_wait`: the ordinary fail-wait (`FailWait:=TRUE; WAIT DURATION
    #: TimeToMaint`, Raptor 7).
    FAIL_WAIT = "fail_wait"
    #: `_fail_wait`: the same wait when the life overshoots the run, so it
    #: waits to the end of the run.
    BEYOND_HORIZON = "beyond_horizon"
    #: `_obtain_pm_prereqs`: the `ELSE Run` TELL after a pmMisDefer PM is
    #: deferred by mission.
    PM_MISDEFER_RUN = "pm_misdefer_run"
    #: `_obtain_pm_prereqs`: the `ObtainPMprereqs` TELL boundary
    PM_PREREQS_TELL = "pm_prereqs_tell"
    #: `_obtain_pm_pool_spare`: PMhold waiting for a spare-pool spare
    #: (`WAIT FOR PooledSpares TO Give`).
    PM_HOLD_POOL_SPARE = "pm_hold_pool_spare"
    #: `_obtain_pm_custom_spare`: PMhold waiting for a Custom spare
    PM_HOLD_CUSTOM_SPARE = "pm_hold_custom_spare"
    #: `_request_pm_resource`: PMhold waiting for the PM's resources
    PM_HOLD_RESOURCE = "pm_hold_resource"
    #: `_obtain_pm_resource`: the hand-back hop to `Run` after the PM's
    #: resources are in hand.
    PM_HANDBACK = "pm_handback"
    #: `_obtain_pm_resource`: a pmMisDefer block's extra `WAIT DURATION 0.0`
    #: on the non-mission branch.
    PM_MISDEFER_HOP = "pm_misdefer_hop"
    #: `_obtain_pm_prereqs`: a scheduled PM's `WAIT DURATION 0.0` before `DetermineNewState("PM is due")`
    #: for a pmMisDefer block.
    PM_SCHEDULED_HOP = "pm_scheduled_hop"
    #: `_obtain_pm_prereqs`: the `WAIT FOR pmSyncTrigger TO Fire` after it while a phase change is in
    #: progress; the phase change's end releases it.
    PM_SYNC_WAIT = "pm_sync_wait"
    #: `_obtain_pm_resource`: the same `WAIT FOR pmSyncTrigger` after the hand-back hop.
    PM_SYNC_WAIT_HANDBACK = "pm_sync_wait_handback"
    #: `_obtain_pm_prereqs`: a PM with no resource to wait for pays the pmMisDefer tail's `WAIT DURATION 0.0` itself
    #: then the sync wait below.
    PM_TAIL_HOP = "pm_tail_hop"
    PM_SYNC_WAIT_TAIL = "pm_sync_wait_tail"
    #: `_defer_maintenance`: a `maintDefer` block held in RepHold, waiting for the defer trigger to
    #: fire (`WAIT FOR trig TO Fire`).
    DEFER_TRIGGER_WAIT = "defer_trigger_wait"
    #: `_perform_pm`: the `TELL PerformPM` boundary.
    PERFORM_PM_TELL = "perform_pm_tell"
    #: `_wait_for_custom_spare`: `Repair` waiting for a Custom spare
    REPAIR_CUSTOM_SPARE = "repair_custom_spare"
    #: `_wait_for_pool_spare`: `Repair` waiting for a spare-pool spare
    REPAIR_POOL_SPARE = "repair_pool_spare"
    #: `_obtain_repair_resource`: the hop that lets a same-instant release
    #: land before the resource check.
    REPAIR_RESOURCE_HOP = "repair_resource_hop"
    #: `_obtain_repair_resource`: `Repair` waiting in RepHold for its
    #: resources.
    REPAIR_RESOURCE = "repair_resource"
    #: `_repair_wait`: the repair itself (`WAIT DURATION TimeToRepair`,
    #: Raptor 7).
    REPAIR = "repair"


#: The interrupt reasons `BlockProcess._idle_wait` accepts at source's
#: combined Idle / ZeroWait wait; any other
#: raises `UnexpectedWake`. A "Pool_Available" is accepted too, but only
#: while a deferred PM request is outstanding.
IDLE_WAKE_REASONS: frozenset[str] = frozenset({
    InterruptReason.DEPENDENCE_ALL_UP, InterruptReason.DEPENDENCE_SB_TURN_ON, InterruptReason.DEPENDENCE_STANDBY,
    InterruptReason.SWITCHED_ON, InterruptReason.SWITCHED_OFF,
    InterruptReason.TRIGGERED_PM_DUE, InterruptReason.START_DEFERRED_PM, InterruptReason.PHASE_CHANGE, InterruptReason.SWITCH_FAILED_PATH,
    # "Induced_Failure": an InducedFailure dispatch at a block parked at this combined ZeroWait site
    # (`ASK BlockTrigger TO Release` with `InterruptReason:="Induced_Failure"`).
    # The site's reason chain ends with an Induced_Failure branch that FAILS the block
    # `LadL5Full` (real 2026-10-01) confirms it. An earlier note here called it a
    # benign no-op.
    InterruptReason.INDUCED_FAILURE,
    # "Dependence_Idle": a GoIdle dispatch landing on an
    # already-Idle (Site-1-parked) block -- source's Down
    # dispatch does `SetInterruptReason("Dependence_Idle") +
    # ChangeBlockState(Idle,...)` (a no-op state change) + the
    # ZeroWait `BlockTrigger.Release`, and Site-1's wait
    # tolerates it through the same "Dependence" prefix
    # dispatch (`SUBSTR(1,10,...)="Dependence"` ->
    # `returnToTop:=TRUE`, Raptor 7) -> redraw ->
    # re-park.
    InterruptReason.DEPENDENCE_IDLE,
    # "OBPM": source's ZeroWait/Idle ON INTERRUPT chain has an
    # explicit OBPM branch -- over
    # threshold routes to `DetermineNewState("PM is due")`,
    # under threshold is a benign `returnToTop`.
    InterruptReason.OBPM,
})


@dataclass(frozen=True)
class WakePolicy:
    """What may end the wait at one `WaitSite`."""

    #: The wait may run its full length (the timer) without an interrupt.
    timer: bool
    #: The interrupt reasons the site handles; any other raises
    #: `UnexpectedWake`. `None`: the site ignores what woke it.
    reasons: frozenset[str] | None
    #: A condition on a reason, where the code has one.
    note: str = ""


_FAIL_WAIT_REASONS = STATE_ALREADY_APPLIED | {
    InterruptReason.OBPM, InterruptReason.INDUCED_FAILURE,
    InterruptReason.TRIGGERED_PM_DUE, InterruptReason.START_DEFERRED_PM,
    InterruptReason.POOL_AVAILABLE,
}
_POOL_ONLY = frozenset({InterruptReason.POOL_AVAILABLE})

#: What may end each wait, as `BlockProcess` handles it today. A table, not
#: code: each site's own handler still does the checking, and
#: `tests/unit/test_engine_design_docs.py` ties the entries to the sets
#: the handlers use.
WAKE_POLICY: dict[WaitSite, WakePolicy] = {
    WaitSite.KICKOFF: WakePolicy(
        timer=True, reasons=KICKOFF_DEPENDENCE_WAKES | {InterruptReason.INDUCED_FAILURE, InterruptReason.OBPM},
        note="a dependency dispatch has already applied its transition; an induced failure is taken at the first wait"),
    WaitSite.IDLE: WakePolicy(
        timer=False, reasons=IDLE_WAKE_REASONS | {InterruptReason.POOL_AVAILABLE},
        note="Pool_Available only while a deferred PM request is outstanding"),
    WaitSite.ZERO_STRESS: WakePolicy(
        timer=False, reasons=None,
        note="any wake re-checks the stress and redraws; the reason only feeds the startup-failure draw"),
    WaitSite.PARK_A_HOP: WakePolicy(timer=True, reasons=frozenset()),
    WaitSite.FAIL_WAIT: WakePolicy(
        timer=True, reasons=_FAIL_WAIT_REASONS,
        note="Pool_Available only when a deferred PM grant has landed; any reason is taken as that grant then"),
    WaitSite.BEYOND_HORIZON: WakePolicy(
        timer=True, reasons=_FAIL_WAIT_REASONS,
        note="as the fail-wait; the timer ends the run"),
    WaitSite.PM_MISDEFER_RUN: WakePolicy(timer=True, reasons=None, note="the wake is not read"),
    WaitSite.PM_PREREQS_TELL: WakePolicy(
        timer=True, reasons=STATE_ALREADY_APPLIED | {
            InterruptReason.TRIGGERED_PM_DUE, InterruptReason.START_DEFERRED_PM,
            InterruptReason.OBPM, InterruptReason.INDUCED_FAILURE}),
    WaitSite.PM_HOLD_POOL_SPARE: WakePolicy(timer=False, reasons=_POOL_ONLY),
    WaitSite.PM_HOLD_CUSTOM_SPARE: WakePolicy(timer=False, reasons=_POOL_ONLY),
    WaitSite.PM_HOLD_RESOURCE: WakePolicy(timer=False, reasons=_POOL_ONLY),
    WaitSite.PM_HANDBACK: WakePolicy(
        timer=True, reasons=STATE_ALREADY_APPLIED | {
            InterruptReason.TRIGGERED_PM_DUE, InterruptReason.OBPM,
            InterruptReason.INDUCED_FAILURE, InterruptReason.POOL_AVAILABLE},
        note='Pool_Available only with token "pm" (an async PM-prerequisite grant)'),
    WaitSite.PM_MISDEFER_HOP: WakePolicy(timer=True, reasons=None, note="the wake is not read"),
    WaitSite.PM_SCHEDULED_HOP: WakePolicy(timer=True, reasons=None, note="the wake is not read"),
    WaitSite.PM_SYNC_WAIT: WakePolicy(timer=False, reasons=None, note="the wake is not read"),
    WaitSite.PM_SYNC_WAIT_HANDBACK: WakePolicy(timer=False, reasons=None, note="the wake is not read"),
    WaitSite.PM_TAIL_HOP: WakePolicy(timer=True, reasons=None, note="the wake is not read"),
    WaitSite.PM_SYNC_WAIT_TAIL: WakePolicy(timer=False, reasons=None, note="the wake is not read"),
    WaitSite.DEFER_TRIGGER_WAIT: WakePolicy(
        timer=False, reasons=frozenset({InterruptReason.TRIGGER_FIRED}),
        note="only the trigger's fire ends it (no ON INTERRUPT clause in source; FailWait and ZeroWait are clear)"),
    WaitSite.PERFORM_PM_TELL: WakePolicy(
        timer=True, reasons=STATE_ALREADY_APPLIED | {InterruptReason.TRIGGERED_PM_DUE}),
    WaitSite.REPAIR_CUSTOM_SPARE: WakePolicy(timer=False, reasons=_POOL_ONLY),
    WaitSite.REPAIR_POOL_SPARE: WakePolicy(
        timer=False, reasons=_POOL_ONLY,
        note='a "pm" token is a PM grant: reconciled, then the wait resumes'),
    WaitSite.REPAIR_RESOURCE_HOP: WakePolicy(timer=True, reasons=None, note="the wake is not read"),
    WaitSite.REPAIR_RESOURCE: WakePolicy(timer=False, reasons=_POOL_ONLY),
    WaitSite.REPAIR: WakePolicy(
        timer=True, reasons=_POOL_ONLY,
        note="a stale PM grant: reconciled, then the repair resumes"),
}
