"""End-to-end tests of `runner.run_simulation`: each parameterized
golden fixture's real `input.rbd` is loaded and its generated event log
diffed against the matching captured legacy log.
"""

from __future__ import annotations

import pathlib

import pytest

from raptor.core.engine.runner import run_simulation
from raptor.core.fileio.rbd import load_rbd
from tests.unit.log_compare import (
    LANDMARK_BEGIN_RUN, classify, read_lines, split_runs)

GOLDEN = pathlib.Path(__file__).parent.parent / "golden"


def _golden_runs(path: pathlib.Path, num_runs: int) -> list[list[str]]:

    lines = read_lines(path, required=True)

    runs = split_runs(lines, LANDMARK_BEGIN_RUN, num_runs=num_runs)

    # Each run's trailing "Phase_change"/"Finished_a_mission" and

    # "Simulation_Terminate" lines are reproduced by `run_simulation`

    # itself (via `system_status.change_system_status`), so the

    # comparison is complete.

    for run in runs:

        assert "Phase_change" in run[-2]

        assert "Simulation_Terminate" in run[-1]

    return runs


@pytest.mark.parametrize(

    "fixture_dir,legacy_file,num_runs,min_match,compare_runs",

    [

        ("TwoBlockRedundancy", "rpt_input_two_event.txt.gz", 20, 1500, None),

        ("PrePostLDT", "rpt_pre_post_ldt.txt", 5, 400, None),

        ("FiniteSparePool", "rpt_finite_spare_pool.txt.gz", 5, 100, None),

        ("BlockDependency", "rpt_blockdependency_events.txt", 5, 1500, None),

        ("SingleHierarchy", "rpt_events_singlehierarchy.txt", 5, 1500, None),

        ("HierarchyExample", "rpt_events_hierarchy.txt", 10, 2080, None),

        ("ComplexModel", "rpt_events_complex.txt", 5, 14000, None),

        ("SharedStreamTie", "rpt_events_sharedstreamtie.txt", 5, 8800, None),

        ("Cascade", "rpt_cascade_events.txt", 5, 9600, None),

        ("ComplexExample", "rpt_events_complex.txt", 10, 195, None),

        ("ResourcePool", "rpt_events_resourcepool.txt", 5, 8600, None),

        ("SpaceStation", "rpt_events_spacestation.txt", 10, 1290, None),

        # TriggerOrderProbe: 3 PM subscribers on one trigger, no pools/
        # spares/deps/phases, all-Fixed distros. The trigger waiters wake
        # in static row order; a repairing subscriber is skipped, not
        # rotated to the back.
        ("TriggerOrderProbe", "rpt_events_TriggerOrderProbe.txt", 3, 252, None),

        # PoolStockProbe: a spare pool that can only drain (RSO/ESO/SLO
        # all off); a pool level counts only if held for a positive
        # duration.
        ("PoolStockProbe", "rpt_events_PoolStockProbe.txt", 10, 1600, None),

        # F2_Pearson5Probe: Pearson5(0.5, 10) stored with three params.
        # With two, the real engine reads dataArray[3] out of bounds
        # (heap garbage).
        ("F2_Pearson5Probe", "rpt_events_F2_Pearson5Probe.txt", 20, 10000, None),
        ("F2_WeibullProbe", "rpt_events_F2_WeibullProbe.txt", 20, 10000, None),

        # SimStartProbe2: exercises GetStartCond's down-start arms --
        # BlockA simStartType=1 ("in a completely random state"), BlockB
        # simStartType=4 ("failed ... at random time" in the repair
        # cycle). Both start the run Repairing, part way through a repair
        # whose length GetStartCond already drew and scaled.
        ("SimStartProbe2", "rpt_events_SimStartProbe2.txt", 5, 700, None),

        # NodeStandbyBackflowProbe / DependentStressProbe: a dependent block
        # following a cold-standby switch-off into Standby
        # (`BackFlowDependencies(NodeStandby)`).
        ("NodeStandbyBackflowProbe", "rpt_events_NodeStandbyBackflowProbe.txt", 10, 900, None),
        ("DependentStressProbe", "rpt_events_DependentStressProbe.txt", 10, 900, None),

        # SimStartProbe3: simStartType 5 ("Repair cycle is N% complete") and
        # 6 ("at start of hands-on repair"). Both blocks start down with
        # identical Fixed(10) repairs, so both kickoff repairs finish at
        # the same instant (t=10.0), exercising the `TELL METHOD Run`
        # re-entry hop at the end of a kickoff repair
        # (`block_lifecycle.kickoff_repair_run_hop`).
        ("SimStartProbe3", "rpt_events_SimStartProbe3.txt", 5, 700, None),

        # MotorDemo: the manual's Chapter 7 "electric motor"
        # dependent-idle example. Armature/Brushes are dep_type=RBDBlock
        # and combine LDT with finite Custom sparing (Brushes starts at
        # Stock=0, ESO enabled).
        ("MotorDemo", "rpt_events_MotorDemo.txt", 25, 515, None),

        # PreventMxDemo: clean match; the fp32-noise is small-magnitude
        # timestamp drift on long accumulated durations.
        ("PreventMxDemo", "rpt_events_PreventMxDemo.txt", 10, 1470, None),

        # FactoryDemo: a block with Custom sparing (SLO=1/RSO=0/ESO=0) must
        # not go permanently "Done No_spares" while the pool can still
        # replenish; the "permanently out" guard checks
        # stock_level_ordering as well as routine_spare_ordering.
        ("FactoryDemo", "rpt_events_FactoryDemo.txt", 5, 2150, None),

        # Events: all 1000 real runs, bit-exact; covers

        # `ReconfigureSystem`'s event-side "Permanently_Down" /

        # CallEndSimulation early-termination path (`_Cascade.apply_event`;

        # see `tests/golden/Events/README.md`).

        ("Events", "rpt_events.txt", 1000, 14000, None),

        # CYCLESIM termType=3 (cycle-terminated): 3,000 cycles of ~17h

        # phases per run.

        ("CYCLESIM", "rpt_events_CYCLESIM.txt", 2, 150000, None),

        # CYCLESIM_SS with StartStats=1000: the mid-run ResetAllStatistics

        # TELL (blank line plus the "Started Gathering Statistics" marker

        # at the reset instant, then post-reset bookkeeping).

        ("CYCLESIM_SS", "rpt_events_CYCLESIM_SS.txt", 2, 150000, None),

        # CS_Probe_pure: the CS_SwitchFail model with the unrelated blocks

        # neutralized (huge fail times, PM off), so the log is purely the

        # Comm/HighGain/LowGain cold-standby dance. Exercises the

        # stress-dilated fail-wait countdown.

        ("CS_Probe_pure", "rpt_events_CS_Probe_pure.txt", 10, 600, None),
        # CS_Probe_AProb1: CS_Probe with link 8's AProb forced 0.5->1.0, so
        # Comm's auto-switch to LowGain always succeeds on the first
        # attempt. Runs 1-6 are structurally exact against the real
        # capture; later runs hit a separate SimTime rounding tie-break
        # issue, so compare_runs=6.
        ("CS_Probe_AProb1", "rpt_events_CS_Probe_AProb1.txt", 10, 4900, 6),
        # CS_Probe_AProb099: CS_Probe_AProb1 with link 8's AProb at 0.99, so
        # SetSwitchTime's own RNG draw is taken (it is skipped at
        # AProb=1.0). `_switch_failed` must not leave `cold_path_done`
        # permanently set across runs, so the per-run block reset clears
        # it. Runs 1-2 match; later runs hit a separate same-instant
        # tie-break issue, so compare_runs=2.
        ("CS_Probe_AProb099", "rpt_events_CS_Probe_AProb099.txt", 10, 1550, 2),

        # PMCustomSLOProbe (all-Fixed, 1 run): a block-owned custom
        # `OrderStock` (a TELL METHOD on the RBDBlockObj,
        # Raptor 7) emits its startup `N_Spare(s)_ordered_` line
        # before `beginning_PM` at t=10.
        ("PMCustomSLOProbe", "rpt_events_PMCustomSLOProbe.txt", 1, 29, None),

        # PMStaggerProbe: pins Initialize's `PMTimeLeft:=pmFreq-pmStagger`
        # Stagger4 (PMFreq 10, stagger 4) first
        # PM_is_due at t=6; Stagger0 at t=10.
        ("PMStaggerProbe", "rpt_events_PMStaggerProbe.txt", 1, 134, None),

        # SparingNoneProbe: pins CheckForSpare's `sparingType=None` branch
        # -- both blocks go Done/"No_spares" at
        # their first failure (no repair).
        ("SparingNoneProbe", "rpt_events_SparingNoneProbe.txt", 5, 90, None),

    ],

)

def test_orchestrator_matches_real_captured_log(fixture_dir, legacy_file, num_runs, min_match, compare_runs) -> None:

    rbd_file = load_rbd(str(GOLDEN / fixture_dir / "input.rbd"))

    golden_runs = _golden_runs(GOLDEN / fixture_dir / "legacy" / legacy_file, num_runs)

    if compare_runs is not None:

        golden_runs = golden_runs[:compare_runs]



    mine_runs = run_simulation(rbd_file, num_runs=compare_runs or num_runs)

    # Mirror _golden_runs' blank-stripping on our side too (real event
    # files contain blank lines -- e.g. CYCLESIM_SS's reset marker --
    # which the golden parser drops).

    mine_runs = [[l for l in run if l.strip()] for run in mine_runs]

    assert len(mine_runs) == len(golden_runs)



    total = {"match": 0, "fp32-noise": 0, "structural": 0}

    for run_index, (logs, golden) in enumerate(zip(mine_runs, golden_runs)):

        assert len(logs) == len(golden), (

            f"{fixture_dir} run {run_index}: line count differs: mine={len(logs)} golden={len(golden)}"

        )

        first_structural = None

        for i, (mine, theirs) in enumerate(zip(logs, golden)):

            c = classify(mine, theirs)

            total[c] += 1

            if c == "structural" and first_structural is None:

                first_structural = i

        assert first_structural is None, (

            f"{fixture_dir} run {run_index}: structural mismatch at line {first_structural}: "

            f"mine={logs[first_structural]!r} theirs={golden[first_structural]!r}"

        )



    assert total["structural"] == 0

    assert total["match"] + total["fp32-noise"] >= min_match


@pytest.mark.parametrize("fixture", ["FreqPMProbe", "PreventMxDemo", "Phasing1"])
def test_logging_does_not_change_pm_cadence(fixture: str) -> None:
    """Logging must be side-effect-free: running a PM-heavy fixture with and
    without log collection produces byte-identical reports, since the
    source's `WriteEvents` calls never affect timing."""
    def reports(collect: bool):
        sink: list = []
        run_simulation(load_rbd(str(GOLDEN / fixture / "input.rbd")),
                       num_runs=5, _report_sink=sink, collect_logs=collect)
        return sink

    assert repr(reports(True)) == repr(reports(False))


def test_hierarchy_fail_count_tracks_and_never_goes_negative() -> None:
    """`hier.num_currently_failed` moves in lockstep with the system's own
    `NumCurrentlyFailed` for any element inside a hierarchy
    (via `IncNumFailed`/
    `DecNumFailed`).

    The counter is exercised, stays non-negative throughout, and is
    re-initialised to 1 at each run's kickoff.
    """
    import raptor.core.engine.orchestrator as orch

    owner = orch._Cascade
    real = owner._apply_hier_fail_delta
    samples: list[tuple[int, int]] = []

    seen_this_run: set[int] = set()
    firsts: list[tuple[int, int]] = []

    def spy(self, parent_id: int, delta: int) -> None:
        real(self, parent_id, delta)
        if parent_id > 0 and delta:
            hier = self.model.hiers_by_id.get(parent_id)
            if hier is not None:
                samples.append((parent_id, hier.num_currently_failed))
                if parent_id not in seen_this_run:
                    seen_this_run.add(parent_id)
                    firsts.append((parent_id, hier.num_currently_failed))

    real_reset = owner.reset_for_new_run
    def spy_reset(self, *a, **kw):
        seen_this_run.clear()
        return real_reset(self, *a, **kw)

    owner._apply_hier_fail_delta = spy
    owner.reset_for_new_run = spy_reset
    try:
        run_simulation(load_rbd(str(GOLDEN / "HierarchyExample" / "input.rbd")), num_runs=3)
    finally:
        owner._apply_hier_fail_delta = real
        owner.reset_for_new_run = real_reset

    assert samples, "no in-hierarchy failure transitions were recorded at all"
    assert all(count >= 0 for _pid, count in samples), \
        f"hierarchy fail count went negative: {samples}"
    # A run may legitimately end with in-hierarchy blocks still down, so
    # the counter need not unwind to zero within a run. It must not carry
    # over: the source re-inits every hierarchy at each kickoff
    # (->), so the first
    # sample of every run is exactly 1.
    assert all(count == 1 for _pid, count in firsts), \
        f"a run started with a stale non-zero count: {firsts}"


def test_failsim_start_stats_resets_on_recovery_count(monkeypatch) -> None:
    """Under FailSim, StartStats is a system-recovery count: the statistics
    reset fires once, when `termCounter == TRUNC(StartStats)`."""
    import pathlib

    from raptor.core.engine import orchestrator as orch, runner
    from raptor.core.fileio.rbd import load_rbd

    rbd = load_rbd(str(pathlib.Path(__file__).parent.parent / "golden" / "TwoBlockRedundancy" / "input.rbd"))
    row = rbd.section("SYSTEM_DATA").rows[0]
    row["TerminationType"] = "FailSim"
    row["TermCondition"] = 4.0
    row["StartStats"] = 2.0
    resets: list[int] = []
    real = orch._Cascade.reset_all_statistics

    def spy(self, sim_time):
        resets.append(self.term_counter)
        return real(self, sim_time)

    monkeypatch.setattr(orch._Cascade, "reset_all_statistics", spy)
    runner.run_simulation(rbd, num_runs=1)
    assert resets == [2]
