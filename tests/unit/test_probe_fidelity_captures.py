"""The P1 probe captures (ER3 P1 items; the ER3 label key is the appendix of the internal engine ledger),
captured against Raptor7Student with a Detailed event log (no gdb:
none of these questions was a scheduler-internal one).

- `FailSimOutOfSparesProbe` (ER3-12/ER3-20): a FailSim model whose only block
  runs out of Custom spares. Real ends each run AT the `Permanently_Down
  Out_of_spares` instant; the port logged that line but kept running to
  `TermCondition`, because `_Cascade.apply()` (the block path) never acted on
  `reconfigure_system`'s return the way its sibling `apply_event()` did -- and,
  unlike `apply_event()` (whose only caller is the kickoff loop, which checks
  `run_ended` itself), `apply()` also has to schedule the actual stop
  (`_end_sim_process()`, the same queued `CallEndSimulation` the termCounter
  FailSim path uses). Byte-exact after the fix.
- `ZeroWaitReasonsProbe`: byte-exact with no change -- pinned here so it stays
  that way.
- `EventDepProbe` (ER3-08): a block depending on an event. Two real gaps:
  (1) events initialize BEFORE `simInProgress:=TRUE`,
  so `ReconfigureEvent`'s `IF hasDepObjects AND simInProgress` gate
  skips the dispatch at kickoff and dependents idle later via the queued
  event-level `InitTimeZeroDeps` -- the port dispatched immediately, idling the
  block before the event's own link update reached its node; and the port's
  post-event-init system check then logged a transient color real never shows.
  (2) `EndSimulation` ends with `IF SystemStateChange: AnalyzeSystem`, which
  the port never ran -- a recovery at the run's final instant (whose own lazy
  check the run ending cut off) therefore never got its `State_change` line.
  Only a color change logs there (`runCompleted` gates `Red_change`). Fixing
  (2) also made the pre-existing `PMGrantVsRepairSpareProbe` golden fixture
  byte-exact (Red -> Yellow at t=100), which nothing had been checking.
- `DependenceStress=` (found via `PhaseBarrierProbe`,
  `PhasedWeakAnalysisMultiMissionProbe` and `CycleSimStartStatsCapacityProbe`,
  all `Phasing1`-family clones, then confirmed on `Phasing1`/`Phasing3`
  themselves): `ChangeBlockState` logs the block's persisted `PhaseStress`
  FIELD, which only `Run`'s top-of-loop and init write.
  At a phase boundary a block idled by `Phase_Change` and immediately
  recovered by dependency still holds the previous phase's value. The port
  recomputed the stress for the current phase instead: 880/1049 of
  `Phasing1`'s and 1753/2310 of `Phasing3`'s `DependenceStress=` lines were
  wrong, invisible to every existing test (sd_diff compares draws, fidelity
  compares reports -- neither reads event-log text). Now every one of the
  8023 such rows across these five fixtures matches real, in order, per run.

These fixtures are NOT byte-exact overall -- see ER3-11 and ER3-14 in the appendix of
The internal engine ledger for the remaining (pre-existing, not introduced by
today's changes) residuals.
"""
from __future__ import annotations

import pathlib
import sys

import pytest

from tests.unit.log_compare import read_capture_text

from raptor.core.engine.runner import run_simulation
from raptor.core.fileio.builder import load

GOLDEN = pathlib.Path(__file__).parent.parent / "golden"
sys.path.insert(0, str(GOLDEN.parent.parent / "tools"))
import golden_fixtures as gf  # noqa: E402  (split-layout fixtures: input under tests/fixtures/)


def _rows(lines):
    return [tuple(c.strip() for c in l.split("\t")[:5]) for l in lines if "\t" in l]


def _runs(fixture: str, capture: str | None = None):
    """(ours, real): per-run lists of 5-column row tuples, Begin_Run markers
    dropped (a harness artifact, not an ordering fact)."""
    capture = capture or f"rpt_events_{fixture}.txt"
    real_rows = _rows(read_capture_text(GOLDEN / fixture / "legacy" / capture).splitlines()[1:])
    real, cur = [], None
    for r in real_rows:
        if r[2].startswith("Begin_Run"):
            if cur is not None:
                real.append(cur)
            cur = []
            continue
        cur.append(r)
    real.append(cur)
    ours = [[r for r in _rows(run) if not r[2].startswith("Begin_Run")]
            for run in run_simulation(load(str(gf.resolve_rbd_path(fixture))))]
    return ours, real


@pytest.mark.parametrize("fixture", [
    "FailSimOutOfSparesProbe", "ZeroWaitReasonsProbe", "EventDepProbe", "PMGrantVsRepairSpareProbe",
])
def test_probe_matches_real_byte_exact(fixture: str) -> None:
    ours, real = _runs(fixture)
    assert len(ours) == len(real)
    for i, (o, r) in enumerate(zip(ours, real)):
        assert o == r, f"{fixture} run {i + 1}"


def test_failsim_out_of_spares_stops_at_permanently_down() -> None:
    """The specific ER3-12/ER3-20 fact: the run ends at the same instant as
    `Permanently_Down`, not at `TermCondition`."""
    ours, _real = _runs("FailSimOutOfSparesProbe")
    for run in ours:
        pd_t = next(r[0] for r in run if r[2] == "Permanently_Down")
        assert run[-1][2] == "Simulation_Terminate" and run[-1][0] == pd_t


def test_hier_degrade_probe_no_visible_out_node_reconciliation() -> None:
    """ER3-14: `HierDegradeProbe` (rebuilt through the MCP tools after the
    original crashed real Raptor on open) puts a 1-of-2 redundant pair inside
    a hierarchy. Real logs the internal node `Degraded 1-1/2` while the
    hierarchy's own Out node logs `Good` -- the exact `(numCurrentlyFailed>0)
    AND (Status=AllUp)` state `AnalyzeSystem`'s unported reconciliation loop
   targets -- and never follows with a Degraded
    line for the hierarchy: 261 internal Degraded events, zero `Hier ...
    Degraded` lines across all 10 real runs. The port already matches that;
    runs 1/5/7/10 are byte-exact (the other six diverge later on the
    separate, pre-existing spare-pool order-line class)."""
    ours, real = _runs("HierDegradeProbe")

    def hier_degraded(runs):
        return [r for run in runs for r in run if r[1] == "Hier" and r[3] == "Degraded"]

    def internal_degraded(runs):
        return [r for run in runs for r in run if r[1] == "Node" and r[2] == "500path" and r[3] == "Degraded"]

    assert internal_degraded(real) and not hier_degraded(real)
    assert internal_degraded(ours) and not hier_degraded(ours)
    for i in (0, 4, 6, 9):
        assert ours[i] == real[i], f"run {i + 1}"


_EXPERIMENT = GOLDEN / "CycleSimStartStatsCapacityProbe" / "legacy" / "output_option_experiment"


def _first_phase_times(runs) -> list[str]:
    return [next(r[0] for r in run if r[2] == "Phase_change" and r[3] == "Phase_change") for run in runs]


def _real_runs(path: pathlib.Path):
    runs, cur = [], None
    for r in _rows(read_capture_text(path).splitlines()[1:]):
        if r[2].startswith("Begin_Run"):
            if cur is not None:
                runs.append(cur)
            cur = []
            continue
        if cur is not None:
            cur.append(r)
    runs.append(cur)
    return runs


@pytest.mark.parametrize("capture,cost_file", [
    ("rpt_events_1_eventlog_only.txt", False),
    ("rpt_events_2_plus_final_results.txt", False),
    ("rpt_events_3_plus_end_of_run.txt", False),
    ("rpt_events_4_plus_interim_cost.txt", True),
    ("rpt_events_6_capacity_plot_no_interim_cost.txt", False),
    ("rpt_events_7_everything.txt", True),
])
def test_cyclesim_interim_cost_output_flips_trigger_phase_order(capture: str, cost_file: bool) -> None:
    """ER3-19 spillover: `CycleSimStartStatsCapacityProbe`'s triggers and
    phase schedule share system stream 101, and which draws first at t=0
    depends on the Interim Costs output option -- one fresh file, options
    toggled one at a time: phase first unless Interim Costs is on (source
    queues `TELL System TO CalculateInterimCost` ahead of
    `ControlPhaseChanges`, as TimeSim's `CallEndSimulation` always is). The
    port's stand-in for that option is a requested cost sink. Every run's
    first phase-change time must match (the draw order fixes it)."""
    real = _real_runs(_EXPERIMENT / capture)
    ours = [[r for r in _rows(run) if not r[2].startswith("Begin_Run")]
            for run in run_simulation(load(str(GOLDEN / "CycleSimStartStatsCapacityProbe" / "input.rbd")),
                                      _cost_sink=[] if cost_file else None)]
    assert _first_phase_times(ours) == _first_phase_times(real)
    if capture == "rpt_events_7_everything.txt":
        for i in (2, 4):  # runs 3 and 5: byte-exact; others hit known F.5/pool-order classes
            assert ours[i] == real[i], f"run {i + 1}"


@pytest.mark.parametrize("fixture,capture", [
    ("Phasing1", "rpt_events_phasing1.txt"),
    ("Phasing3", "rpt_events_phasing3.txt"),
    ("PhaseBarrierProbe", None),
    ("PhasedWeakAnalysisMultiMissionProbe", None),
    ("CycleSimStartStatsCapacityProbe", None),
])
def test_dependence_stress_reads_persisted_phase_stress(fixture: str, capture: str | None) -> None:
    ours, real = _runs(fixture, capture)

    def sel(rs):
        return [r for r in rs if r[4].startswith("DependenceStress=")]

    assert any(sel(r) for r in real)
    for i, (o, r) in enumerate(zip(ours, real)):
        assert sel(o) == sel(r), f"{fixture} run {i + 1}"


def test_phased_failsim_puts_phase_draw_first_without_interim_cost() -> None:
    """Batch 1, probe B: FailSim queues no system TELL ahead of
    ControlPhaseChanges (unlike TimeSim's CallEndSimulation), so with the
    event log + reports but no Interim Costs, real draws the phase first
    (first Begin_phase 4.940641 in run 1). Run 1 is then byte-exact up to
    the pool order-line class; later runs drift from that divergence."""
    ours, real = _runs("PhasedFailSimOrderProbe")
    assert _first_phase_times(ours)[:1] == _first_phase_times(real)[:1] == ["4.940641"]


@pytest.mark.parametrize("fixture", ["PooledGraceRepairProbe", "PooledLDTOrderProbe"])
def test_pooled_eso_orders_and_placement_before_run_end(fixture: str) -> None:
    """Batch 1, probes C/D: both blocks fail at t=5 -- the system's first
    failure -- on an ESO pool empty from the start, and real ORDERS both
    spares (revised grace: silent only when a same-instant grant emptied the
    pool). The pool-owned order TELLs land at the end of the instant, the
    second after both blocks' Repair turns (owner-group calendar). Exact up
    to the run-end instant (t=50), where real's ChooseNext tie-break serves
    both orders back to back -- still an open residual."""
    ours, real = _runs(fixture)
    assert [r for r in ours[0] if float(r[0]) < 50.0] == [r for r in real[0] if float(r[0]) < 50.0]
