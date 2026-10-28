"""Interrupt reasons: the values of MODSIM's `InterruptReason` that the engine
sends with `Scheduler.interrupt` and dispatches on when a wait is cut short.

These are distinct from event-log text even where the spelling is close
(`"Phase_Change"` is a reason; `"Phase_change"` is a log column).
"""
from __future__ import annotations

from enum import StrEnum


class InterruptReason(StrEnum):
    """`InterruptReason` values. A `StrEnum`, so each member
    compares, hashes and formats exactly as its string value."""

    # Dependency dispatch (`BackFlowDependencies`).
    DEPENDENCE_IDLE = "Dependence_Idle"
    DEPENDENCE_ALL_UP = "Dependence_All_Up"
    DEPENDENCE_SB_TURN_ON = "Dependence_SB_turnOn"
    DEPENDENCE_STANDBY = "Dependence_Standby"
    INDUCED_FAILURE = "Induced_Failure"
    OBPM = "OBPM"
    # Cold standby switching (`RunColdLogic`, `PerformSwitch`).
    SWITCHED_ON = "Switched_On"
    SWITCHED_OFF = "Switched_Off"
    SWITCH_FAILED_PATH = "Switch_Failed_Path"
    PERFORM_SWITCH = "PerformSwitch"
    PERFORM_SWITCH_RECONFIGURE = "PerformSwitch_reconfigure"
    # Phasing (`ChangePhases`).
    PHASE_CHANGE = "Phase_Change"
    # Preventive maintenance.
    SCHEDULED_PM = "Scheduled_PM"
    TRIGGERED_PM_DUE = "Triggered_PM_Due"
    START_DEFERRED_PM = "Start_Deferred_PM"
    OBTAINED_PM_PREREQS = "Obtained_PM_Prereqs"
    TRIGGER_FIRED = "Trigger_Fired"
    # Resource/spare pools (`Give`).
    POOL_AVAILABLE = "Pool_Available"


#: The dependency dispatches. One delivered while a block sits at its kickoff
#: barrier needs no handling: the dispatch already applied the transition and
#: the block computes its first stress from its current status.
KICKOFF_DEPENDENCE_WAKES = frozenset({
    InterruptReason.DEPENDENCE_IDLE,
    InterruptReason.DEPENDENCE_ALL_UP,
    InterruptReason.DEPENDENCE_SB_TURN_ON,
    InterruptReason.DEPENDENCE_STANDBY,
})

#: Reasons whose state change was already applied by whoever sent the
#: interrupt (the dependency dispatch, a cold-standby switch, a phase
#: change), so the woken wait only has to resume (ER3-32).
STATE_ALREADY_APPLIED = frozenset({
    InterruptReason.DEPENDENCE_IDLE,
    InterruptReason.SWITCHED_ON,
    InterruptReason.SWITCHED_OFF,
    InterruptReason.DEPENDENCE_ALL_UP,
    InterruptReason.DEPENDENCE_SB_TURN_ON,
    InterruptReason.DEPENDENCE_STANDBY,
    InterruptReason.PHASE_CHANGE,
    InterruptReason.SWITCH_FAILED_PATH,
})
