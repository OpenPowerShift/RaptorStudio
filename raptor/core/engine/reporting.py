"""Per-run report-data snapshot for the `End_Of_Run`/`Final_Results`
reports -- computed once per run from `SystemState`/`RBDBlock`, mirroring
Raptor 7's per-run scalar computation (`StartEngine`, right
after `ChangeStatus(DDone,NonMission)` ends the run). See `reports.py` for
the text builders that consume this.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING

from raptor.core.engine.stats import RunningStat
from raptor.core.engine.system_status import SystemState
from raptor.core.model import RBDBlock

if TYPE_CHECKING:
    from raptor.core.engine.capacity import CapacityRunReport
    from raptor.core.engine.reporting import CostRunReport as _CostRunReport  # noqa: F401


@dataclass
class StockSnapshot:
    """One row of the report's sparing/resource stock tables -- source's
    `InitNumSpares`/`NewSpares`/`MinNumSpares`/`AveNumSpares`/
    `MaxNumSpares`/`EndingSpares`/`NumOfWaits` arrays, sampled at run end.
    `mean` is time-weighted; see
    `pool.ResourceQueue.mean_stock` for the rationale."""

    name: str
    init: int
    arrivals: int
    minimum: int
    mean: float
    maximum: int
    ending: int
    waits: int


@dataclass
class RunReportData:
    run_number: int
    ending_sim_time: float
    availability: float
    mtbde: float | None
    mdt: float | None
    reliability: float
    conditional_reliability: float
    #: Whether the source's EndOfRun actually assigned `RCond` this run
    #: (`IF missionsBegan>0`, Raptor 7 -- no ELSE, so `RCond` keeps
    #: its stale value otherwise). `RCondMon` is a GETMONITOR on the global
    #: variable, so only real assignments become aggregate samples: the final
    #: report's Conditional_Reliability row skips runs with `missionsBegan=0`.
    #: The EOR's own ConditionalReliability line instead displays the stale
    #: global (gated on missionsAttempted), which is `conditional_reliability`
    #: above -- not this flag.
    rcond_assigned: bool
    #: `StatsStartTime` this run (`SimTime` at the in-run
    #: ResetAllStatistics, `0.0` when none -- SystemState.
    #: stats_reset_time). FINAL report Term2 :=
    #: `ending_sim_time - stats_start_time`, printed
    #: as `Active_Stat_Time` when StatsReset.
    stats_start_time: float
    num_system_failures: int
    num_component_failures: int
    mtbm: float | None
    mtbmu: float | None
    mtbms: float | None
    mmt: float | None
    mmtu: float | None
    mmts: float | None
    #: `None` when `MissionGrnMon.Sum+MissionYelMon.Sum+MissionRedMon.Sum <=
    #: 0.0` this run (Raptor 7 -- the source itself has no `ELSE`
    #: here, so `Dependability` keeps whatever value it held from a previous
    #: run; this project computes report data fresh per run with no cross-run
    #: persistence, so `None` is used). Only displayed when `activePhases>0`
    #: (`reports.build_end_of_run_report`'s `has_phases` gate), but always
    #: computed, matching the source's unconditional assignment.
    dependability: float | None
    #: Whether to print `Dependability` at all in the per-run report
    #: (Raptor 7's own `IF (activePhases>0)` gate) -- `model.
    #: phases` non-empty, threaded through from `orchestrator.py`.
    has_phases: bool
    #: `None` when `TotalTime<=0.0` this run (the source's own `GreenPercent
    #: :=100.*GreenMon.Sum/TotalTime` etc. are skipped in that case,
    #: Raptor 7 -- so the underlying `LMONITORED` variable, and
    #: therefore the cross-run `GreenPercentMon` that `Final_Results` reads,
    #: gets no sample this run; the per-run `End_Of_Run` report still shows
    #: `"0.000000"` since it is an unconditional `WriteString` of whatever the
    #: variable holds -- see `reports.build_end_of_run_report`'s fallback).
    green_percent: float | None
    yellow_percent: float | None
    red_percent: float | None
    #: `MissionGrnPer`/`MissionYelPer`/`MissionRedPer`
    #:  -- like `green_percent` etc. but nested inside both
    #: `TotalTime>0.0` and `MissionTotalTime>0.0`, so `None` whenever
    #: `green_percent` is, or whenever mission time alone is exhausted. Only
    #: printed when `has_phases` (Raptor 7's `activePhases>0` gate),
    #: same as `dependability`, but always computed.
    mission_green_percent: float | None
    mission_yellow_percent: float | None
    mission_red_percent: float | None
    sys_fail_stat: RunningStat
    red_stat: RunningStat
    comp_fail_stat: RunningStat
    comp_repair_stat: RunningStat
    #: `block.id -> (FailureCounter-discardedFailures, SparesUsed)`,
    #: snapshotted at run end (source's own `EndSparesUsed[...]:=
    #: SparesUsed[...]`, Raptor 7) since both fields reset for
    #: the NEXT run before this report gets built.
    block_spares: dict[int, tuple[int, int]] = field(default_factory=dict)
    #: Per-run stock statistics for the End_Of_Run report's own
    #: `Custom_Sparing_Information` / `SparePool_Sparing_Information` /
    #: `Resource_Information` tables. Three separate
    #: dicts because the source keys them differently -- Custom by block
    #: (`seqNum`), the other two by pool (`i+totalBlocks`) -- and because each
    #: table has its own column layout. Each value is a `StockSnapshot`; an
    #: empty dict means the model has none of that kind, matching the source's
    #: `IF (SparesTrackingGroup.numberIn>0)` / `IF (totalSpares>0)` /
    #: `IF (totalRes>0)` section guards.
    #: `missionsAttempted` this run -- the weak-link `R` column's denominator
    #: .
    missions_attempted: int = 0
    #: `SysTimeToFail` / `SysTimeToRepair` values this run wrote to the "All system
    #: failure times" / "All system down times" files (only while `StatsStarted`).
    sys_fail_times: list[float] = field(default_factory=list)
    sys_repair_times: list[float] = field(default_factory=list)
    #: `cyclesCount` at EndSimulation (Raptor 7's
    #: `Term3Cond:=cyclesCount`, monitored by `Term3Mon`) -- the Final Results
    #: SUMMARY's `Cycles_Completed` row. Fractional, not a count:
    #: `ControlPhaseChanges` adds 1.0 per completed wrap and
    #: `EndSimulation` then adds the part-cycle the run stopped inside,
    #: `currentPhase/activePhases`. `None` when the model has no
    #: phases, which is also the report's `activePhases>0` section guard.
    cycles_completed: float | None = None
    #: Keyed by block id (names are not unique -- FactoryDemo has two
    #: `CMD_5000` blocks and the source prints a row for each); the pool dicts
    #: below stay name-keyed, where names are unique.
    custom_stock: dict[int, StockSnapshot] = field(default_factory=dict)
    spare_pool_stock: dict[str, StockSnapshot] = field(default_factory=dict)
    resource_stock: dict[str, StockSnapshot] = field(default_factory=dict)
    #: Per-block weak-analysis state-time snapshot (Raptor 7's raw
    #: material), present ONLY when the model enables Weak Link analysis
    #: (otherwise the accumulators stay zero anyway). block id ->
    #: {"name", "run", "repair", "rep_hold", "pm", "pm_hold", "idle",
    #:  "standby", "done"} -- seconds in each state this run.
    weak_stats: dict[int, dict] = field(default_factory=dict)
    #: Same snapshot for NODES (green/yellow/red/blue/brown/orange
    #: seconds), present under Weak Link analysis.
    weak_nodes: dict[int, dict] = field(default_factory=dict)
    #: Weak-link EVENT_RESULTS: event id ->
    #: {"name", "run", "standby", "repair", "m_completed"}.
    weak_events: dict[int, dict] = field(default_factory=dict)
    #: Weak-link HIERARCHY_RESULTS: hier id ->
    #: {"name", "out_node_id", "green", "yellow", "red", "blue", "brown",
    #:  "orange", "down_event_count", "m_completed"}.
    weak_hiers: dict[int, dict] = field(default_factory=dict)
    #: End-of-run Descendant Status Indicator per hierarchy:
    #: hier id -> {"state": "AllUp"|"Degraded"|"Down", "nodes": total,
    #: "down": n, "degraded": n}. Derived from the subtree nodes' final
    #: `status` at finalize (the Raptor 7 engine's DSI counters update live via
    #: `ChangeNodeState`'s descendant rollup, which is not ported here).
    hier_dsi: dict[int, dict] = field(default_factory=dict)
    #: `MTBDEsusp`/`MTBMsusp`/`MTBMususp`/`MTBMssusp` (Raptor 7)
    #: -- true when this run's own `mtbde`/`mtbm`/
    #: `mtbmu`/`mtbms` is the raw (undivided) `GreenMon.Sum+YellowMon.Sum`
    #: fallback, not a genuinely divided rate. The source's flags are sticky
    #: across the whole simulation (reset once at `StartEngine` init,
    #: Raptor 7, never per-run); `build_final_results_report` ORs
    #: these across every run to reproduce that (`any(...)`), since this
    #: project computes report data fresh per run with no persistent
    #: module-level state. Drives the aggregate `Final_Results_Report`'s
    #: `>`-censoring (mean/max prefixed `>`, StDev/SEM forced N/A); the
    #: per-run `End_Of_Run` report is unaffected (plain `REALTOSTR`, no
    #: censoring).
    mtbde_suspected: bool = False
    mtbm_suspected: bool = False
    mtbmu_suspected: bool = False
    mtbms_suspected: bool = False
    #: The value `MTBMsMon` observes for this run. `MTBMsMon` is a MODSIM monitor on the variable `MTBMs`, so EVERY
    #: assignment to it is a sample -- including the "suspected" raw `GreenMon.Sum+YellowMon.Sum` assigned when the
    #: run had failures but no PM, or neither, where `MTBMsupdated:=FALSE` only blanks the
    #: per-run End-of-Run line (`mtbms` stays None there). `LadL4Node` (real, 2026-10-01): 18 of 60 runs had a
    #: PM and the aggregate row still counted all 60, `>`-censored, not `MTBMs(18-runs)`.
    mtbms_agg: float | None = None
    #: `didPM`: sticky, set when any run had both or only PM completions; without it
    #: the aggregate MTBMs row is all N/A, however many suspected samples the monitor holds.
    did_pm: bool = False
    #: Capacity-analysis snapshot (`capacity.CapacityRunReport`) -- `None`
    #: when the model doesn't use `capacityAnalysis`. Consumed by
    #: `reports.build_end_of_run_report`'s ***CAPACITY*** section and the
    #: Final_Results Units_Produced/Mean_Flow/Mean_Capacity rows.
    capacity: CapacityRunReport | None = None
    #: Cost-analysis snapshot (`CostRunReport`) -- `None` when the model
    #: doesn't use `costAnalysis`. Consumed by `reports.build_end_of_run_
    #: report`'s ***COST*** section.
    cost: CostRunReport | None = None
    #: This run ended because a block ran permanently out of spares
    #: (`cascade.out_of_spares`, source's `OutOfSpares` global) -- drives the
    #: FailSim final-results note.
    out_of_spares: bool = False


@dataclass
class BlockCostRow:
    """One block's row across both COST tables (per-run extraction): the Sparing table's InitSpares/NewSpares/
    EmerShip trio and Life_cycle_cost's Purchase/Operating/Sparing/
    Disposal/Total quintuple. `sparing_col`: False for SparePool-sparing
    blocks (source prints N/A and EXCLUDES the row from the Sparing
    table entirely)."""

    name: str
    init_spare_cost: float  # `sic[i]` -- initStock*spareCost, Custom only
    new_spares_cost: float  # BlockEndSpCost
    emer_ship_cost: float   # BlockEndESCost
    purchase: float         # `ic[j]` -- initialCost (ZeroInitCost FALSE)
    operating: float        # OperatingCost[j]
    sparing: float          # SparingCost[j] == sic+sp+es
    disposal: float         # BlockEndDispCost
    total: float            # ic+operating+sparing+disposal
    #: The Operating TABLE's own per-state columns
    #:  -- the raw per-state accumulators, not the aggregate.
    op_cost: float = 0.0
    rep_cost: float = 0.0
    idle_cost: float = 0.0
    hold_cost: float = 0.0
    pm_cost: float = 0.0
    pm_hold_cost: float = 0.0
    sb_cost: float = 0.0
    done_cost: float = 0.0
    #: `block.parentID` -- the Operating table's InHier column
    #: (WriteInt(parentID,4)).
    in_hier: int = 0
    sparing_col: bool = True


@dataclass
class PoolCostRow:
    """One pool's row in the COST section's SparePools/Resources tables.
   For a RESOURCE pool the middle columns
    RELABEL: NewSpares->PerUseCost (es_cost) and EmerShip->PerTimeCost
    (add_cost), per 's swapped arguments."""

    name: str
    initial: float       # initialSpares*spareCost
    new_spares: float    # PoolEndAddCost (spare pools) / PerTimeCost (resources)
    emer_ship: float     # PoolEndESCost (spare pools) / PerUseCost (resources)
    total: float
    is_resource: bool = False


@dataclass
class EventCostRow:
    """One event's row in the COST section's Events table
   : Initial (initialCost), Success
    (EventEndOpCost -- charged per Armed/Success departure),
    Failure (EventEndRepCost -- per Failure recovery)."""

    name: str
    initial: float
    success: float
    failure: float
    total: float
    parent_id: int = 0


@dataclass
class HierCostRow:
    """One hierarchy's row in the COST section's Hierarchy table
   : the HierCosts[seq,1..6] rollup of every
    child block's purchase/operating/sparing/disposal plus child
    events' totals."""

    name: str
    hier_id: int
    purchase: float
    operating: float
    sparing: float
    disposal: float
    events: float
    total: float
    #: The hierarchy's container (0 at the top), its 1-based position in the model's hierarchy order (`seqNum`) and
    #: its OWN costs before the nested rollup: Final Results rebuilds the rollup from the cross-run means.
    parent_id: int = 0
    seq: int = 0
    own: tuple[float, ...] = ()


@dataclass
class CostRunReport:
    """The whole run's cost picture."""

    blocks: dict[int, BlockCostRow]
    spare_pools: dict[str, PoolCostRow]
    resources: dict[str, PoolCostRow]
    events: dict[int, EventCostRow]
    hiers: dict[int, HierCostRow]
    system_down_cost: float
    system_lost_flow_cost: float
    grand_total: float
    #: The model's CONFIGURED `System.redCost`, not the accumulated
    #: total -- source gates its `SystemDownCost` row on
    #: `IF System.redCost>0.0`, which a
    #: zero-accumulator model still fails. Defaults True so callers that
    #: don't set it keep the previous always-print behaviour.
    red_cost_configured: bool = True


def build_run_report_data(
    run_number: int, run_length: float, sim_time: float, state: SystemState, blocks: list[RBDBlock],
    *, has_phases: bool = False, capacity: CapacityRunReport | None = None,
    cost: CostRunReport | None = None, weak_stats: dict | None = None,
    weak_nodes: dict | None = None, hier_dsi: dict | None = None,
    weak_events: dict | None = None, weak_hiers: dict | None = None,
    termination_type: str = "TimeSim",
    auto_stopped: bool = False,
    out_of_spares: bool = False,
    missions_attempted: int = 0,
    cycles_completed: float | None = None,
    custom_stock: dict[int, StockSnapshot] | None = None,
    spare_pool_stock: dict[str, StockSnapshot] | None = None,
    resource_stock: dict[str, StockSnapshot] | None = None,
) -> RunReportData:
    """Port of Raptor 7's per-run scalar computation --
    Availability/MTBDE/MDT/MTBM*/MMT*/Green-Yellow-RedPercent, reading
    `.Sum`/`.Mean`/`.Count` off the within-run monitors
    `system_status.change_system_status`/`block_lifecycle.block_run_process`
    fed via `state` over the course of this run. `Reliability`/
    `ConditionalReliability` use `state.missions_completed`.

    `sim_time`: the scheduler's final clock reading, used for
    `EndingSimTime` only when `blocks` is empty (an events-only model, where
    nothing advances the scheduler past 0.0). Block-based models use
    `run_length` directly, since the source forces `SimTime` to that value by
    run end via each block's "wait for run_length" mechanism.
    """
    green_sum = state.green_stat.sum
    yellow_sum = state.yellow_stat.sum
    red_sum = state.red_stat.sum
    total_time = green_sum + yellow_sum + red_sum
    # CycleSim's run_length is a CYCLE count, not hours -- EndingSimTime
    # must be the actual elapsed clock at the CallEndSimulation instant
    # (tests/golden/CYCLESIM: run lengths ~50,987h for 3,000 cycles).
    # Same reasoning applies to a block-based FailSim (run ends at the
    # terminating failure, not at run_length); TimeSim keeps the
    # forced-to-run_length convention.
    if termination_type in ("CycleSim", "FailSim"):
        # FailSim was only described above, not implemented, until a real
        # capture showed it (`FailSimOutOfSparesProbe`, 2026-09-23: real
        # `EndingSimTime 0.924530`, the permanent-down instant; this port
        # printed `run_length`, which for FailSim is a failure COUNT).
        effective_sim_time = sim_time
    else:
        effective_sim_time = run_length if blocks else sim_time

    availability = (green_sum + yellow_sum) / total_time if total_time > 0.0 else 1.0

    # `SimTime>0.0` gates (930) -- without them, an
    # events-only model (whose SimTime never leaves 0.0, see this function's
    # `EndingSimTime` paragraph) would report a real MTBDE/MDT value instead
    # of "N/A" even when a system failure technically occurred. The else
    # branch is safe for such a model too, since its `green_sum+yellow_sum` is
    # also always 0.
    #
    # MTBDE has a third case: zero system failures this
    # run (`SysFailMon.Count==0`, e.g. `NumberOfSystemFailures=0`) still shows
    # a "suspected" (per-run: plain; aggregate report: `>`-censored) raw sum,
    # not "N/A", as long as the system spent any time up at all.
    mtbde_suspected = False
    if state.sys_fail_stat.count > 0 and effective_sim_time > 0.0:
        mtbde = (green_sum + yellow_sum) / state.sys_fail_stat.count
    elif (green_sum + yellow_sum) > 0.0:
        mtbde = green_sum + yellow_sum
        mtbde_suspected = True
    else:
        mtbde = None
    if auto_stopped:
        # Raptor 7 -- the run could not reach the required failure count, so the last
        # (unfinished) down period is censored: scale the mean by n/(n-1).
        rc = state.red_stat.count
        mdt = state.red_stat.mean * rc / (rc - 1.0) if rc > 1 and effective_sim_time > 0.0 else None
    else:
        mdt = state.red_stat.mean if state.red_stat.count > 0 and effective_sim_time > 0.0 else None

    # MTBM/MTBMu/MTBMs -- a genuine 4-way branch, not
    # 3 independent computations: MTBMu/MTBM/MTBMs interact because MTBM
    # blends unscheduled (CompFailMon, ordinary repair) and scheduled
    # (CompPMMon, PM completion) counts together. `autoStopped` has no MTBM
    # branch in the source (only MDT and MMT, ported above and below).
    comp_fail_count = state.comp_fail_stat.count
    comp_pm_count = state.comp_pm_stat.count
    mtbm_suspected = mtbmu_suspected = mtbms_suspected = False
    mtbm: float | None
    mtbmu: float | None
    mtbms: float | None
    mtbms_agg: float | None = None
    did_pm = False
    if (green_sum + yellow_sum) > 0.0:
        if comp_fail_count > 0 and comp_pm_count > 0:
            mtbmu = (green_sum + yellow_sum) / comp_fail_count
            mtbm = (green_sum + yellow_sum) / (comp_fail_count + comp_pm_count)
            mtbms = mtbms_agg = (green_sum + yellow_sum) / comp_pm_count
            did_pm = True
        elif comp_pm_count > 0:
            # Raptor 7 -- `MTBMu` set to the raw (undivided)
            # sum here, a "suspected"/censored value in source's own
            # aggregate-report notation (`MTBMususp`) -- still a real
            # number in the PER-RUN report though (no `>`-censoring
            # there, see `reports.py`'s own per-run WriteString code).
            mtbm = (green_sum + yellow_sum) / comp_pm_count
            mtbms = mtbms_agg = (green_sum + yellow_sum) / comp_pm_count
            mtbmu = green_sum + yellow_sum
            mtbmu_suspected = True
            did_pm = True
        elif comp_fail_count > 0:
            mtbmu = (green_sum + yellow_sum) / comp_fail_count
            mtbm = (green_sum + yellow_sum) / comp_fail_count
            mtbms = None                      # `MTBMsupdated:=FALSE`: the End-of-Run line reads N/A ...
            mtbms_agg = green_sum + yellow_sum    # ... but the aggregate monitor still observes the raw sum
            mtbms_suspected = True
        else:
            mtbm = mtbmu = green_sum + yellow_sum
            mtbms = None
            mtbms_agg = green_sum + yellow_sum
            mtbm_suspected = mtbmu_suspected = mtbms_suspected = True
    else:
        mtbm = mtbmu = mtbms = None

    # MMT/MMTu/MMTs (Raptor 7, non-`autoStopped` branch only
    # -- and the `autoStopped` branch above).
    comp_repair_count = state.comp_repair_stat.count
    mmt: float | None
    mmtu: float | None
    mmts: float | None
    if auto_stopped:
        # Raptor 7 -- same n/(n-1) censoring on each monitor's mean.
        if comp_repair_count > 1 and comp_pm_count > 1:
            mmts = state.comp_pm_stat.mean * comp_pm_count / (comp_pm_count - 1.0)
            mmtu = state.comp_repair_stat.mean * comp_repair_count / (comp_repair_count - 1.0)
            mmt = (mmtu * (comp_repair_count - 1) + mmts * (comp_pm_count - 1)) / (
                (comp_repair_count - 1) + (comp_pm_count - 1))
        elif comp_repair_count > 1:
            mmtu = state.comp_repair_stat.mean * comp_repair_count / (comp_repair_count - 1.0)
            mmt = mmtu
            mmts = None
        elif comp_pm_count > 1:
            mmts = state.comp_pm_stat.mean * comp_pm_count / (comp_pm_count - 1.0)
            mmt = mmts
            mmtu = None
        else:
            mmt = mmtu = mmts = None
    elif comp_repair_count > 0 and comp_pm_count > 0:
        mmts = state.comp_pm_stat.mean
        mmtu = state.comp_repair_stat.mean
        mmt = (mmtu * comp_repair_count + mmts * comp_pm_count) / (comp_repair_count + comp_pm_count)
    elif comp_repair_count > 0:
        mmtu = state.comp_repair_stat.mean
        mmt = mmtu
        mmts = None
    elif comp_pm_count > 0:
        mmts = state.comp_pm_stat.mean
        mmt = mmts
        mmtu = None
    else:
        mmt = mmtu = mmts = None

    # Dependability -- see `RunReportData.
    # dependability`'s own docstring for the `None`-on-no-data caveat.
    mission_total = state.mission_green_stat.sum + state.mission_yellow_stat.sum + state.mission_red_stat.sum
    dependability = (
        (state.mission_green_stat.sum + state.mission_yellow_stat.sum) / mission_total
        if mission_total > 0.0 else None
    )

    green_percent = 100.0 * green_sum / total_time if total_time > 0.0 else None
    yellow_percent = 100.0 * yellow_sum / total_time if total_time > 0.0 else None
    red_percent = 100.0 * red_sum / total_time if total_time > 0.0 else None

    # MissionGreen/Yellow/RedPercent -- nested
    # inside `TotalTime>0.0` (i.e. `total_time > 0.0`, matching the
    # ordinary percents above) AND `MissionTotalTime>0.0`.
    mission_green_percent: float | None
    mission_yellow_percent: float | None
    mission_red_percent: float | None
    if total_time > 0.0 and mission_total > 0.0:
        mission_green_percent = 100.0 * state.mission_green_stat.sum / mission_total
        mission_yellow_percent = 100.0 * state.mission_yellow_stat.sum / mission_total
        mission_red_percent = 100.0 * state.mission_red_stat.sum / mission_total
    else:
        mission_green_percent = mission_yellow_percent = mission_red_percent = None

    # `IF missionsAttempted>0: Reliability:=FLOAT(missionsCompleted)/
    # FLOAT(missionsAttempted)` and `IF missionsBegan>0: RCond:=FLOAT(
    # missionsCompleted)/FLOAT(missionsBegan)` --
    # two different divisors. They coincide on a non-phased model (one
    # attempt, one begin) but diverge once a model has real mission phases.
    reliability = (float(state.missions_completed) / float(state.missions_attempted)
                   if state.missions_attempted > 0 else float(state.missions_completed))
    conditional_reliability = (float(state.missions_completed) / float(state.missions_began)
                               if state.missions_began > 0 else reliability)

    return RunReportData(
        out_of_spares=out_of_spares,
        run_number=run_number,
        ending_sim_time=effective_sim_time,
        availability=availability,
        mtbde=mtbde,
        mdt=mdt,
        reliability=reliability,
        conditional_reliability=conditional_reliability,
        rcond_assigned=state.missions_began > 0,
        stats_start_time=state.stats_reset_time,
        num_system_failures=state.sys_fail_stat.count,
        num_component_failures=state.comp_fail_stat.count,
        mtbm=mtbm, mtbmu=mtbmu, mtbms=mtbms, mmt=mmt, mmtu=mmtu, mmts=mmts,
        dependability=dependability, has_phases=has_phases,
        green_percent=green_percent, yellow_percent=yellow_percent, red_percent=red_percent,
        mission_green_percent=mission_green_percent, mission_yellow_percent=mission_yellow_percent,
        mission_red_percent=mission_red_percent,
        sys_fail_stat=state.sys_fail_stat, red_stat=state.red_stat,
        comp_fail_stat=state.comp_fail_stat, comp_repair_stat=state.comp_repair_stat,
        block_spares={
            b.id: (b.run_state.failure_counter - b.run_state.discarded_failures, b.run_state.spares_used) for b in blocks
        },
        mtbde_suspected=mtbde_suspected, mtbm_suspected=mtbm_suspected,
        mtbmu_suspected=mtbmu_suspected, mtbms_suspected=mtbms_suspected,
        mtbms_agg=mtbms_agg, did_pm=did_pm,
        capacity=capacity, cost=cost,
        weak_stats=weak_stats or {},
        weak_nodes=weak_nodes or {},
        weak_events=weak_events or {},
        weak_hiers=weak_hiers or {},
        hier_dsi=hier_dsi or {},
        missions_attempted=missions_attempted,
        sys_fail_times=list(state.sys_fail_times),
        sys_repair_times=list(state.sys_repair_times),
        cycles_completed=cycles_completed,
        custom_stock=custom_stock or {},
        spare_pool_stock=spare_pool_stock or {},
        resource_stock=resource_stock or {},
    )
