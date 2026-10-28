"""Port of the system-level Green/Yellow/Red status aggregation:
`AnalyzeSystem`'s color determination (the
non-hierarchy/non-capacity-analysis subset) and `ChangeStatus` in full -- the `"System"`/`"State_change"` color line and the
`"System"`/`"Phase_change"`/`"Began_a_mission"`/`"Finished_a_mission"`
mission-transition lines.

Also ports the two pieces of bookkeeping that feed into it:
- `NumCurrentlyFailed`'s block-transition delta (the `incFails`/`decFails` booleans inside `ChangeBlockState`).
- `SystemUp`'s End-node-transition trigger (inside
  `ChangeNodeState`'s `typeNode=3` branch).

Also ports the repeated-Red `"Red_change"` line (`red_change_line`) -- distinct from `"State_change"`'s comment builder (`_r`/
`_h`/`_d`/`_p`/`_c` prefixes here vs `_d`-for-everything-non-Cut there), it
fires on every block-state change that happens while the system is already Red
and stays Red, deduplicated against the last comment used
(`SystemState.red_string`).

Hierarchy `NumCurrentlyFailed` rollup, capacity
analysis and mission/phase tracking are out of scope. `ReconfigureSystem`'s
"permanently down" reachability mechanism is ported
separately, in `cascade.py`.

Checked against `tests/golden/PrePostLDT` and `tests/golden/Phasing1` in
`tests/unit/test_system_status.py`.
"""
from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from enum import Enum

from raptor.core.engine.eventlog import format_event_log_line
from raptor.core.engine.stats import RunningStat
from raptor.core.model import ActiveStatus, BlockStatus, EventStatus, NodeStatus, RBDBlock


class SystemColor(Enum):
    GREEN = "Green"
    YELLOW = "Yellow"
    RED = "Red"
    DONE = "Done"


class MissionStatus(Enum):
    """Port of `MissionStatusType=(Mission,NonMission)`."""

    MISSION = "Mission"
    NON_MISSION = "NonMission"


_UP_LIKE_BLOCK = {BlockStatus.RUNNING, BlockStatus.STANDBY}
_FAIL_LIKE_BLOCK = {
    BlockStatus.REPAIRING, BlockStatus.DONE, BlockStatus.IDLE,
    BlockStatus.PM, BlockStatus.PM_HOLD, BlockStatus.REP_HOLD,
}
#: The maintenance-like block states that forfeit mission credit
#: (the same set Raptor 7 uses).
_MAINT_LIKE = {BlockStatus.REPAIRING, BlockStatus.REP_HOLD, BlockStatus.PM,
               BlockStatus.PM_HOLD, BlockStatus.DONE}

_UP_LIKE_NODE = {NodeStatus.ALL_UP, NodeStatus.DEGRADED, NodeStatus.STANDBY}
_DOWN_LIKE_NODE = {NodeStatus.DOWN, NodeStatus.IDLE, NodeStatus.PM}


@dataclass
class SystemState:
    """Module-level globals in the source (`NumCurrentlyFailed`,
    `SystemUp`, `System.Status`), gathered into one object here."""

    num_currently_failed: int = 0
    system_up: bool = True
    color: SystemColor = SystemColor.GREEN
    #: The last comment string logged on a "Red_change" line -- module-level
    #: `redString` in the source, which doubles as `change_system_status`'s
    #: "State_change" comment scratch buffer. That makes
    #: `change_system_status` overwrite this on every actual color change, not
    #: just `red_change_line`'s dedup path. Resets to "" at the start of every
    #: run along with the rest of the per-run state (`run_control.py`).
    red_string: str = ""
    #: `LastUpDownChange` in the source -- the sim time the system last
    #: transitioned into Red. Set by `change_system_status`; also resets
    #: to 0.0 each run, per the same evidence as `red_string` above.
    last_up_down_change: float = 0.0

    # --- End_Of_Run / Final_Results report state ---
    #: `LastColorChange` in the source -- the
    #: sim time the system last changed color (ANY color, unlike `last_
    #: up_down_change` which only tracks into-Red transitions). Reset to
    #: 0.0 each run, same as `last_up_down_change`.
    last_color_change: float = 0.0
    #: `GreenMon`/`YellowMon`/`RedMon` (Raptor 7, fed by
    #: assigning to the `LMONITORED` `GreenTime`/`YellowTime`/`RedTime`
    #: variables) -- each sample is the duration just spent in that
    #: color, fed on every color CHANGE (tagged to the color being LEFT).
    #: Reset every run.
    green_stat: RunningStat = field(default_factory=RunningStat)
    yellow_stat: RunningStat = field(default_factory=RunningStat)
    red_stat: RunningStat = field(default_factory=RunningStat)
    #: `SysFailMon` (Raptor 7, fed by assigning to
    #: `SysTimeToFail:=SimTime-LastUpDownChange` on every transition into Red).
    #: The source also has a `SimTime=0.0 AND dZeroFail` suppression
    #: `dZeroFail` is not tracked here, so this always feeds
    #: the sample.
    sys_fail_stat: RunningStat = field(default_factory=RunningStat)
    #: `dZeroFail` (the "Do not count failures which occur at time zero"
    #: option, `IgnoreStartFail` in SYSTEM_DATA). Set from the model when the
    #: cascade is built; never reset per run.
    ignore_zero_time_failures: bool = False
    #: `CountFailure` : cleared when the system
    #: turns Red at SimTime 0 with `dZeroFail` on, and set again by the next
    #: down->up recovery INSTEAD of counting a FailSim failure
    #: Reset to True each run in this port (the
    #: source sets it once per simulation, which differs only when a run ends
    #: while the t=0 failure is still unrecovered; a FailSim run cannot).
    count_failure: bool = True
    #: `TimeOfCompFailure`/`CompFailMon` (Raptor 7:
    #: 5426/5469/5542`) -- system-WIDE (not per-block) "time of the last
    #: ANY-block failure" cursor and the resulting inter-failure-duration
    #: monitor, fed at every block's own failure ("Failed" line) site.
    #: `CompRepairMon` (5884/6036`) is simpler -- each
    #: sample is just that block's own repair duration, no cursor needed.
    time_of_comp_failure: float = 0.0
    comp_fail_stat: RunningStat = field(default_factory=RunningStat)
    comp_repair_stat: RunningStat = field(default_factory=RunningStat)
    #: Sim-time of the system's own first component failure this run (`None`
    #: until then, set once by `record_component_failure` and never touched
    #: again). Unlike `time_of_comp_failure` above, which updates on every
    #: failure, this answers "is this the very first one".
    #: `block_lifecycle.py`'s ESO-order branch consults it for a one-time
    #: grace on whichever failures land at that exact instant.
    first_failure_time: float | None = None
    #: `CompPMMon` (`LMONITORED CompTimeToDoPM`) -- each
    #: sample is one block's own PM duration, fed at `PerformPM`'s completion
    #: (`block_lifecycle.py`'s PM-due handling). Reset every run
    #: Feeds `MTBMs`/`MMTs` (`reports.py`).
    comp_pm_stat: RunningStat = field(default_factory=RunningStat)
    #: `LastMissionChange` -- a separate cursor from
    #: `last_color_change`, reset on either a color or a mission-status change
    #: (see `change_system_status`'s mission-time feeding logic for why this
    #: can't just reuse `last_color_change`). Reset to 0.0 each run.
    last_mission_change: float = 0.0
    #: `MissionGrnMon`/`MissionYelMon`/`MissionRedMon`
    #: (fed by assigning to `MissionGrnTime`/`MissionYelTime`/
    #: `MissionRedTime`) -- like `green_stat`/`yellow_stat`/`red_stat`, but only
    #: accumulate time spent during a mission and are fed on a broader trigger
    #: (color or mission-status change, not just color change). Feeds
    #: `Dependability` (`reporting.py`). Reset every run
    mission_green_stat: RunningStat = field(default_factory=RunningStat)
    mission_yellow_stat: RunningStat = field(default_factory=RunningStat)
    mission_red_stat: RunningStat = field(default_factory=RunningStat)
    #: `TotalRedCost` (reset) -- the SYSTEM
    #: down-cost accumulator: `redCost*colorTime` charged when a color
    #: change LEAVES Red (inside ChangeStatus's own color-
    #: duration bookkeeping). Fed only when `costAnalysis` is on AND
    #: `system.redCost>0`; this port accumulates unconditionally (a 0.0
    #: red_cost contributes nothing) and the report writer gates display.
    total_red_cost: float = 0.0
    #: `missionStatus`. The dataclass default (`Mission`) is the
    #: non-phased baseline; `runner.run_simulation`'s per-run kickoff
    #: overrides it from `phases[0].mission` whenever the model uses phasing,
    #: matching `SystemObj.SetToInitValues`.
    #: `ConductPhaseChange` is the only thing that changes it after kickoff,
    #: via `change_system_status`'s `new_mission_status` param.
    mission_status: MissionStatus = MissionStatus.MISSION
    #: `missionSuccess`/`missionsAttempted`/`missionsBegan`/`missionsCompleted`
    #: `missions_attempted` and `missions_began`
    #: differ only when a mission begins while the system is already Red
    #: `Reliability := missionsCompleted/missionsAttempted`,
    #: `ConditionalReliability := missionsCompleted/missionsBegan`
    #: (`reporting.py`). Without phasing there is exactly one implicit mission
    #: per run (kickoff to the final `ChangeStatus(DDone,NonMission)` call).
    mission_success: bool = True
    #: `RecordReliabilityFileData`: called with `(sim_time, at_end)` when a
    #: mission first fails and when an unfailed run ends. Set by the driver only when the
    #: cumulative-by-time file is on.
    reliability_record: Callable[..., None] | None = None
    missions_attempted: int = 0
    missions_began: int = 0
    missions_completed: int = 0
    #: `cyclesCount` (reset per run-ish) --
    #: incremented each time ControlPhaseChanges wraps from the LAST
    #: phase back to phase 1; termType=3's termination count.
    cycles_count: float = 0.0
    #: `termCounter` (reset per run) -- CycleSim's
    #: completed-cycle counter; the run ends when it reaches
    #: `trunc(TermCondition)`.
    term_counter: int = 0
    #: `StatsStartTime` (region) -- the sim time of
    #: the in-run `ResetAllStatistics`, `0.0` when no reset has
    #: happened yet this run (per-run init ). Feeds the FINAL
    #: report's Term2 (`SimTime-StatsStartTime`) -- i.e. the
    #: `Active_Stat_Time` row when StatsReset. Not the same thing as the
    #: orchestrator config's `stats_start_time` (that one is `StartStats`,
    #: the trigger THRESHOLD).
    stats_reset_time: float = 0.0
    #: `StatsStarted` : false at run start,
    #: true once statistics are being gathered. Gates the system failure-time and
    #: down-time data files.
    stats_started: bool = False
    #: Values the "All system failure times" / "All system down times" files get
    #: this run (`SysTimeToFail`, `SysTimeToRepair`). Not reset by
    #: `ResetAllStatistics`: a file keeps what it has already written.
    sys_fail_times: list[float] = field(default_factory=list)
    sys_repair_times: list[float] = field(default_factory=list)
    #: `ZeroInitCost` (set true by `ResetAllStatistics`):
    #: suppresses the per-block/event initial (purchase) cost in the cost
    #: report after a delayed reset.
    zero_init_cost: bool = False
    #: `phaseNumber` (Raptor 7 VAR, a single run-wide current-phase
    #: index, 1-based -- not per-object) -- read by `block_lifecycle
    #: .combined_stress` (`phaseValue[phaseNumber]`) and advanced by
    #: `phasing.control_phase_changes_process`. `1` (phase 1) whenever
    #: phasing isn't configured, matching the implicit "always phase 1"
    #: behavior of the real models.
    current_phase: int = 1


def block_fail_count_delta(old_status: BlockStatus, new_status: BlockStatus) -> int:
    """Port of the `incFails`/`decFails` booleans
    : `+1` when a block goes from an up-like status (Running/
    Standby) to a fail-like one, `-1` for the reverse, `0` for same-bucket
    transitions (e.g. Repairing -> PM). Feed the result into
    `SystemState.num_currently_failed`."""
    if old_status in _UP_LIKE_BLOCK and new_status in _FAIL_LIKE_BLOCK:
        return 1
    if old_status in _FAIL_LIKE_BLOCK and new_status in _UP_LIKE_BLOCK:
        return -1
    return 0


def event_fail_count_delta(old_status: EventStatus, new_status: EventStatus) -> int:
    """Port of `ChangeEventState`'s own `incFails`/`decFails` booleans
   -- a separate computation from
    `ChangeEventState`'s `RunChange`/`FailChange` (`events.change_event_state`),
    even though both derive from the same old/new status pair: `+1` when an
    event goes from Armed/Success to Failure, `-1` for the reverse, `0`
    otherwise. Unlike the block version, active-status Cut/Linked plays no
    part; the source's condition only looks at `oldOpStatus`/`newOpStatus`.
    """
    if old_status in (EventStatus.ARMED, EventStatus.SUCCESS) and new_status == EventStatus.FAILURE:
        return 1
    if new_status in (EventStatus.ARMED, EventStatus.SUCCESS) and old_status == EventStatus.FAILURE:
        return -1
    return 0


def end_node_system_up_transition(prior_status: NodeStatus, new_status: NodeStatus) -> bool | None:
    """Port of `ChangeNodeState`'s `typeNode=3` (End node) up/down trigger.
   Returns the new `SystemUp` value if this
    transition should flip it (and the caller should re-run
    `analyze_system`), or `None` if neither edge fired. Only call this for
    the End node's own transitions."""
    if prior_status in _UP_LIKE_NODE and new_status in _DOWN_LIKE_NODE:
        return False
    if prior_status in _DOWN_LIKE_NODE and new_status in _UP_LIKE_NODE:
        return True
    return None


def analyze_system(state: SystemState) -> SystemColor:
    """Port of `AnalyzeSystem`'s color determination
    ."""
    if state.num_currently_failed == 0 and state.system_up:
        return SystemColor.GREEN
    if state.num_currently_failed > 0 and state.system_up:
        return SystemColor.YELLOW
    return SystemColor.RED


def change_system_status(
    state: SystemState,
    new_color: SystemColor,
    sim_time: float,
    blocks: list[RBDBlock],
    *,
    events_file: bool = False,
    new_mission_status: MissionStatus | None = None,
    system_red_cost: float = 0.0,
    #: Weak-link analysis' per-element mission bookkeeping
    #: (at mission BEGIN and at mission END). Both need the live `BlockStatistics`/`NodeStatistics`, which this
    #: function otherwise has no reason to see -- passed only when weak
    #: analysis is on, and a no-op when either is None.
    block_stats: dict | None = None,
    node_stats: dict | None = None,
    nodes: list | None = None,
    event_stats: dict | None = None,
    events: list | None = None,
) -> list[str]:
    """Port of `ChangeStatus` in full: color-
    duration/failure-count bookkeeping, mission begin/end bookkeeping, and the
    `"State_change"`/`"Phase_change"` log lines both can produce.
    `new_mission_status` defaults to `state.mission_status` (no change).
    Returns the formatted log lines in the source's order (mission lines, if
    any, before the color `"State_change"` line), or `[]` if neither actually
    changed. The source only calls `ChangeStatus` when at least one of color or
    mission status changes -- calling it otherwise hits `"Error in system
    ChangeStatus!"` -- so this treats a no-op call as a no-op.
    """
    if new_mission_status is None:
        new_mission_status = state.mission_status
    if new_color == state.color and new_mission_status == state.mission_status:
        return []

    lines: list[str] = []
    color_changed = new_color != state.color
    # Captured BEFORE the color block below mutates `state.color` -- the
    # NEW mission-time feeding logic further down needs the color the
    # system is ABOUT to leave, same as source's own `oldColor:=Status`
    # captured at the very top of `ChangeStatus` (Raptor 7,
    # before either color or mission-status gets overwritten).
    old_color = state.color

    if color_changed:
        # Port of Raptor 7's own color-duration/failure-
        # count bookkeeping. Keyed on the OLD color (the one just being
        # left).
        color_time = sim_time - state.last_color_change
        old_stat = {
            SystemColor.GREEN: state.green_stat,
            SystemColor.YELLOW: state.yellow_stat,
            SystemColor.RED: state.red_stat,
        }.get(state.color)
        if old_stat is not None:
            old_stat.add(color_time)
        # Raptor 7 -- `TotalRedCost:=TotalRedCost+redCost*
        # colorTime`, charged when the color change LEAVES Red (keyed on
        # the OLD color, same as the duration feeding above).
        if old_color == SystemColor.RED:
            state.total_red_cost += system_red_cost * color_time
        state.last_color_change = sim_time

        state.color = new_color
        if new_color == SystemColor.RED:
            # Raptor 7 -- unconditional on the OLD color,
            # only keyed on the color actually just becoming Red.
            # Raptor 7: `IF ((SimTime=0.0) AND dZeroFail)
            # CountFailure:=FALSE ELSE SysTimeToFail:=SimTime-
            # LastUpDownChange (the SysFailMon sample) END IF`.
            if sim_time == 0.0 and state.ignore_zero_time_failures:
                state.count_failure = False
            else:
                state.sys_fail_stat.add(sim_time - state.last_up_down_change)
                if state.stats_started:
                    state.sys_fail_times.append(sim_time - state.last_up_down_change)
            state.last_up_down_change = sim_time
            if state.mission_status == MissionStatus.MISSION and state.mission_success:
                state.mission_success = False
                if state.reliability_record is not None:
                    state.reliability_record(sim_time, at_end=False)

    if color_changed or new_mission_status != state.mission_status:
        # Port of Raptor 7's own mission begin/end
        # bookkeeping. The trigger is the source's own `(oldStatus<>
        # newStatus) OR (oldColor<>newColor)`, not just a mission-status
        # change, so the MissionGrnTime/YelTime/RedTime feeding below also
        # fires on a color change (a mid-mission color change starts a fresh
        # mission-time segment, same as `last_color_change` does for the
        # ordinary Green/Yellow/RedMon stats).
        old_status = state.mission_status
        # Port of Raptor 7's own `missionTime:=SimTime-
        # LastMissionChange; LastMissionChange:=SimTime; IF oldStatus=
        # Mission: CASE oldColor ...` -- a separate cursor/CASE dispatch
        # from the ordinary Green/Yellow/RedMon feeding above (keyed on
        # `old_color`, captured before this function's color mutation), and
        # gated on having just come from a mission (a NonMission segment's
        # duration is discarded, matching the source's missing `ELSE` here).
        mission_time = sim_time - state.last_mission_change
        state.last_mission_change = sim_time
        if old_status == MissionStatus.MISSION:
            mission_old_stat = {
                SystemColor.GREEN: state.mission_green_stat,
                SystemColor.YELLOW: state.mission_yellow_stat,
                SystemColor.RED: state.mission_red_stat,
            }.get(old_color)
            if mission_old_stat is not None:
                mission_old_stat.add(mission_time)
        state.mission_status = new_mission_status
        if old_status == MissionStatus.NON_MISSION and new_mission_status == MissionStatus.MISSION:
            # Raptor 7 -- "beginning a mission".
            state.missions_attempted += 1
            if new_color != SystemColor.RED:
                state.mission_success = True
                state.missions_began += 1
            else:
                state.mission_success = False
            # -- a mission BEGINS by snapshotting who is
            # currently eligible: a block already in a maintenance-like
            # state, or a node already Down/NodePM, starts the mission
            # without credit.
            if block_stats is not None:
                for mission_block in blocks:
                    st = block_stats.get(mission_block.id)
                    if st is not None:
                        st.mission_bool = mission_block.op_status not in _MAINT_LIKE
            if node_stats is not None and nodes is not None:
                for node in nodes:
                    st = node_stats.get(node.id)
                    if st is not None:
                        st.mission_bool = node.status not in (
                            NodeStatus.DOWN, NodeStatus.PM)
            # -- the event arm of the same snapshot, between the
            # block and node loops in the source. An event already in
            # `Failure` starts the mission without credit.
            if event_stats is not None and events is not None:
                for event in events:
                    st = event_stats.get(event.id)
                    if st is not None:
                        st.mission_bool = event.op_status is not EventStatus.FAILURE
            if events_file:
                lines.append(format_event_log_line(sim_time, "System", "Phase_change", "Mission", "Began_a_mission"))
        elif old_status == MissionStatus.MISSION and new_mission_status == MissionStatus.NON_MISSION:
            # Raptor 7 -- "finished a mission".
            status_string = "Mission_success" if state.mission_success else "Mission_failure"
            if state.mission_success:
                state.missions_completed += 1
            # -- every element still holding its mission bool
            # is credited with one completed mission.
            for store in (block_stats, event_stats, node_stats):
                if store is None:
                    continue
                for st in store.values():
                    if st.mission_bool:
                        st.m_completed += 1
            if events_file:
                lines.append(format_event_log_line(sim_time, "System", "Phase_change", status_string, "Finished_a_mission"))

    # Raptor 7 -- the tail State_change write is gated only on
    # EventsFile and `System.Status<>DDone`; it fires on every ChangeStatus
    # call that reached here (i.e. color or mission status actually changed),
    # not just on color changes. The mission-boundary calls inside
    # ConductPhaseChanges (both passing the current cached
    # color as newColor) therefore emit a State_change line even when the
    # color did not move. The early-return no-op guard above stands in for the
    # source's "Error in system ChangeStatus!" alert on a complete no-op call.
    if new_color == SystemColor.DONE and state.mission_success and state.reliability_record is not None:
        # `IF (newColor=DDone) AND missionSuccess: RecordReliabilityFileData`.
        state.reliability_record(sim_time, at_end=True)
    if not events_file or new_color == SystemColor.DONE:
        return lines

    state_change_string = ""
    for block in blocks:
        if block.active_status == ActiveStatus.CUT:
            state_change_string += f"_c{block.id}"
        elif block.op_status in (BlockStatus.REPAIRING, BlockStatus.REP_HOLD, BlockStatus.DONE):
            state_change_string += f"_d{block.id}"

    # Raptor 7 reuses the same `redString` global as both this
    # comment's scratch buffer and `red_change_line`'s dedup memory, so every
    # ordinary color-change event overwrites `red_string` as a side effect,
    # regardless of what color it changes to. Without this, a `red_change_line`
    # comment computed before the system last left and re-entered Red could
    # compare equal to a freshly recomputed identical-looking comment and get
    # deduplicated away.
    state.red_string = state_change_string

    comment = state_change_string if state_change_string else "system_change_status"
    lines.append(format_event_log_line(sim_time, "System", "State_change", new_color.value, comment))
    return lines


def record_component_failure(state: SystemState, sim_time: float) -> None:
    """Port of `CompTimeToFail:=SimTime-TimeOfCompFailure; TimeOfComp
    Failure:=SimTime` -- call at every
    block's own "Failed" line site (natural or induced failure alike;
    both converge onto ONE call site in `block_lifecycle.block_run_
    process`, see that module)."""
    state.comp_fail_stat.add(sim_time - state.time_of_comp_failure)
    state.time_of_comp_failure = sim_time
    if state.first_failure_time is None:
        state.first_failure_time = sim_time


def record_component_repair(state: SystemState, repair_time: float) -> None:
    """Port of `CompTimeToRepair:=TimeToRepair` -- call at every block's own "Repaired" line site."""
    state.comp_repair_stat.add(repair_time)


def record_component_pm(state: SystemState, maint_time: float) -> None:
    """Port of `CompTimeToDoPM:=maintTime` -- call
    at every block's own "PM_complete" line site (`PerformPM`'s own
    completion, `block_lifecycle.py`)."""
    state.comp_pm_stat.add(maint_time)


def red_change_line(
    state: SystemState,
    sim_time: float,
    blocks: list[RBDBlock],
    *,
    events_file: bool = False,
) -> str | None:
    """Port of `AnalyzeSystem`'s repeated-Red comment-tracking
   -- the `ELSIF ((currentSysStatus=RRed) AND
    (newSysStatus=RRed))` branch, i.e. call this INSTEAD of `change_
    system_status` whenever `analyze_system` returns `RED` again but the
    color hasn't actually changed (`change_system_status` itself is a
    no-op in that case, matching the source's mutually-exclusive `IF
    currentSysStatus<>newSysStatus ... ELSIF (both Red) ...`).

    Returns `None` when: the system isn't Red, `sim_time` hasn't advanced
    past the last up/down transition (Raptor 7's `SimTime>
    LastUpDownChange` guard -- true immediately after entering Red, since
    `change_system_status` just set `last_up_down_change` to that same
    `sim_time`), or the built comment is identical to the last one logged
    (`state.red_string`, Raptor 7's deduplication).
    """
    if state.color != SystemColor.RED:
        return None
    if sim_time <= state.last_up_down_change:
        return None
    if not events_file:
        return None

    comment = ""
    for block in blocks:
        if block.active_status == ActiveStatus.CUT:
            comment += f"_c{block.id}"
        elif block.op_status == BlockStatus.REPAIRING:
            comment += f"_r{block.id}"
        elif block.op_status in (BlockStatus.REP_HOLD, BlockStatus.PM_HOLD):
            comment += f"_h{block.id}"
        elif block.op_status == BlockStatus.DONE:
            comment += f"_d{block.id}"
        elif block.op_status == BlockStatus.PM:
            comment += f"_p{block.id}"

    if comment == state.red_string:
        return None
    state.red_string = comment

    final_comment = comment if comment else "System_status_change"
    return format_event_log_line(sim_time, "System", "Red_change", "Red", final_comment)
