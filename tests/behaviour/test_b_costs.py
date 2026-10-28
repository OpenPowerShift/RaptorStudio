"""Operating cost: what every hour in every state costs, charged on the way out.

`raptor/core/engine/accounting.py` is the port of the cost-analysis section
of `ChangeBlockState`. It is a set of observers of
one typed event -- a block leaving one `BlockStatus` for another -- and there
is exactly one of them for cost: `CostAccountant.on_change`, which charges
**the state just left** for the hours it was sat in. Two rules follow from
that and are most of what this file pins:

* a state's cost is `rate * hours-in-that-state`, charged once, when the
  block leaves it -- so the last state's hours are charged by the run-end
  flush, and a `Fixed` life makes every one of those hour counts an integer
  a reviewer can add up;
* a repair also charges its **fixed** cost, once per repair, and a PM its own
  fixed cost, once per PM -- Raptor 7 adds the repair
  resource pool's per-time cost on top, does the same for
  the PM's own pool units (`pm_resources_needed`, not `repair_resources_needed`).

**The observable is the report, not the accumulator.** `CostAccountant`
writes into `BlockStatistics`; `costs.build_cost_run_report` reads those numbers
back into the End-of-Run `***COST***` table. These tests assert the printed
row, so one assertion covers the whole chain -- and a reader who wants to
know which module to blame can read the numbers: the printed `Good` column is
`accounting.py`'s `stats.op_cost` and nothing else.

**Rate columns** are the `.rbd` names, spelled with `build.py`'s friendly
keys: `running_cost` -> `Running`, `rep_per_time` -> `repPerTime`,
`rep_fixed` -> `repFixed`, `idle_cost` -> `idle`, `rep_hold_cost` ->
`repHold`, `standby_cost` -> `standby`, `pm_cost` -> `pmCost`,
`pm_fixed_cost` -> `pmFixedCost`, `pm_hold_cost` -> `pmHoldCost`,
`done_per_time` -> `donePerTime`, `done_fixed` -> `doneFixed`, and
`add_done` -> `addDone`. A `RESOURCE_DATA` pool's per-unit rate is
`per_time_unit` -> `PerTimeUnit`. Every model here is `Fixed` (code 19), so
each row is exact and `Run.draws == 0`; the last test in the file is the one
place where a *draw's order* rather than a drawn value is the subject, and
it says so by naming its seed.
"""
from __future__ import annotations

from typing import Any

from raptor.core.engine import runner as _runner

from . import harness
from .spine import FIXED, spine

#: One block's cost rates, in the units the report prints them in. Chosen to
#: be distinguishable from each other (2, 3, 5, 1, 0.25, 4, 7, 0.75) so a
#: column that shows the wrong rate cannot be mistaken for the right one.
RATES: dict[str, Any] = {
    "running_cost": 2.0,
    "rep_per_time": 3.0,
    "rep_fixed": 5.0,
    "idle_cost": 0.5,
    "rep_hold_cost": 1.0,
    "standby_cost": 0.25,
    "pm_cost": 4.0,
    "pm_fixed_cost": 7.0,
    "pm_hold_cost": 0.75,
}


#: The tables the `***COST***` section is made of, each named by the heading
#: line that introduces it. Not every model has every one of them.
_SECTIONS = ("Operating", "Sparing", "Life_cycle_cost", "Resources",
             "SparePools", "Cost_Summary")

#: The `Operating` table's columns, in the order the report prints them.
COLUMNS = ("Block", "InHier", "Good", "Repair", "Idle", "RepHold", "PM",
           "PMHold", "Standby", "Done")


def _section(eor: list[str], section: str) -> list[str]:
    """The lines of one table of the `***COST***` section, from its heading
    up to the next heading (a table's own `Totals` row is part of it)."""
    tail = eor[eor.index("***COST***"):]
    start = next(i for i, line in enumerate(tail)
                 if line.split()[:1] == [section])
    rest = tail[start + 1:]
    for other in _SECTIONS:
        if other == section:
            continue
        ends = [i for i, line in enumerate(rest) if line.split()[:1] == [other]]
        if ends:
            return rest[:ends[0]]
    return rest


def _row(eor: list[str], section: str, name: str) -> list[str]:
    """One row of *section* of the End-of-Run `***COST***` table, split into
    its fields.

    The table is fixed-column text, so `split()` is the honest reading: what
    is asserted is *which number sits in which column*, which is the claim,
    rather than how many spaces separate them. The section argument is a
    heading, because a block name appears in more than one of these tables.
    """
    for line in _section(eor, section):
        if line.split()[:1] == [name]:
            return line.split()
    raise AssertionError(
        f"no {section!r} row for {name!r} in the End-of-Run cost report")


def _costed(spec: dict[str, Any], *, runs: int = 1, **rates: Any):
    """*spec* with cost analysis on and *rates* applied to every block.

    `cost_analysis` is the `SYSTEM_DATA` column the source gates the whole
    cost section on; with it off, `change_block_state` does not call the
    accountant at all, so there is nothing to read. `runs` is the batch size,
    which is 1 everywhere except the one cross-run aggregation below.
    """
    for block in spec["blocks"]:
        block.update({**RATES, **rates})
    return harness.run(spec, runs=runs, cost_analysis=True)


def _operating(eor: list[str], name: str, column: str) -> str:
    """The *column* entry of *name* in the `Operating` table."""
    return _row(eor, "Operating", name)[COLUMNS.index(column)]


# ---------------------------------------------------------------------------
# Per-state accrual
# ---------------------------------------------------------------------------

def test_hours_of_running_accrue_the_operating_rate() -> None:
    """A block's operating cost is its rate times the hours it spent
    `Good`.

    Why: the cost section charges the state a block is *leaving*,
   so `Running` becomes
    `BlockRunningCost * stateTime` at the moment of the change. A's two
    spells are 0->50 (50 h) and 60->100 (40 h) -- the 10 h it spends
    `Repairing` are not operating time, which is why the rate is applied to
    90 hours and not to the 100 the run is long.

    Setup: S1 with `running_cost = 2.0`, `Fixed` 50 h life, 10 h repair,
    100 h run.

    Expected: the `Good` column reads `180.00` -- `2.0 * (50 + 40)`, to the
    cent, with no tolerance.

    Source: Raptor 7 (ChangeBlockState's cost-analysis
    section)
    """
    run = _costed(spine(1))
    assert _operating(run.eor, "A", "Good") == "180.00"


def test_a_repair_charges_its_rate_for_the_hours_and_its_fixed_cost_once() -> None:
    """A repair is `rate * hours + fixed`, the fixed part once per repair.

    Why: the repair branch is `BlockRepairCost*stateTime + BlockRepairFixCost`,
   and the fixed term is *inside* the branch --
    so it is charged when the block leaves `Repairing`, once, however long
    the repair was. A is `Repairing` for exactly 10 h (a `Fixed` 10 h
    repair).

    Setup: S1 with `rep_per_time = 3.0`, `rep_fixed = 5.0`.

    Expected: the `Repair` column reads `35.00` -- `3.0 * 10 + 5.0`. If the
    fixed cost were charged per hour the column would read 80.00; if it were
    charged on entering `Repairing` instead of leaving it, the run would end
    with the same number by accident and this test could not tell.

    Source: Raptor 7 (the repair branch of the cost section)
    """
    run = _costed(spine(1))
    assert _operating(run.eor, "A", "Repair") == "35.00"


def test_waiting_for_a_busy_repair_pool_charges_the_rephold_rate() -> None:
    """Time spent queued for a repair resource is charged at its own rate,
    not at the repair's.

    Why: `RepHold` is a separate branch of the same dispatch
   with its own column, so a block that cannot
    start its repair yet is not "repairing" and is not charged the repair
    rate. In S3, B fails at 55 while A holds the only unit of `R` until 60.

    Setup: S3 with `rep_hold_cost = 1.0` -- one unit, so the second repair
    has to queue.

    Expected: B's `RepHold` column reads `5.00`, the five hours from 55 to
    60, and B's `Repair` column stays `20.00` (`3.0 * 5 + 5.0`) -- the five
    queued hours are in neither rate twice.

    Source: Raptor 7 (the RepHold branch)
    """
    run = _costed(spine(3))
    assert _operating(run.eor, "B", "RepHold") == "5.00"
    assert _operating(run.eor, "B", "Repair") == "20.00"


def test_a_dependent_block_goes_idle_and_charges_the_idle_rate() -> None:
    """A block idled by a dependence is charged the idle rate, and its
    failure clock is stopped while it is.

    Why: `Idle` is its own branch of the cost section.
   In S6, B depends on A with `DepIdle = 100%`,
    so every time A goes Down -- each of its triggered PMs (the 30-hour
    trigger fires at 30 and 90; the one at 60 falls inside A's repair) and
    its failure at 52 -- B is idled until A is back up. The idle spells are
    30->32, 52->62 and 90->92.

    Setup: S6 with `idle_cost = 0.5`.

    Expected: B's `Idle` column reads `7.00` -- `0.5 * (2 + 10 + 2)`, the
    14 hours it spent idle in the run.

    Source: Raptor 7 (the Idle branch);
    docs/engine-design/dependency-backflow.adoc (Structure)
    """
    run = _costed(spine(6))
    assert _operating(run.eor, "B", "Idle") == "7.00"


def test_a_standby_block_charges_the_standby_rate_for_its_whole_spell() -> None:
    """A cold-standby member charges the standby rate from the moment it is
    switched down until it is promoted.

    Why: `Standby` is its own branch. `D` is
    demoted to `Standby` at kickoff (`Too_many_on`) and stays there until C
    fails at 50; being a cold member costs it money exactly like being idle
    costs B money above, and the two columns are distinct.

    Setup: S8 with `standby_cost = 0.25` -- the cold group of two.

    Expected: D's `Standby` column reads `12.50` -- `0.25 * 50`, the hours
    from 0 to 50 -- and its `Good` column reads `100.00` (`2.0 * 50`, the
    second half of the run after its promotion), so the two columns together
    account for all 100 hours.

    Source: Raptor 7 (the Standby branch);
    docs/engine-design/node-status-and-cold-standby.adoc (Source anchors)
    """
    run = _costed(spine(8))
    assert _operating(run.eor, "D", "Standby") == "12.50"
    assert _operating(run.eor, "D", "Good") == "100.00"


# ---------------------------------------------------------------------------
# Maintenance cost
# ---------------------------------------------------------------------------

def test_a_pm_charges_its_rate_for_the_hours_and_its_fixed_cost_per_pm() -> None:
    """A PM is `rate * hours + fixed`, once per PM -- and A gets two.

    Why: the `PM` branch mirrors the repair branch.
    A runs a *periodic* PM (`PMFreq = 25`, a `Fixed` 2 h duration) with
    neither `PMRefresh` nor `PMFailReset`, so the PM clock is refilled to 25
    at each completion: the first PM runs 25->27, the clock is due again at
    52 -- the same instant A's `Fixed` life completes, because the 2 PM hours
    are not operating time, and the failure wins that tie -- and a repair
    defers the clock to 25 h after the repair completes, so the second PM
    runs 87->89. The fixed cost is charged per PM, so the column is twice
    the per-PM figure; the two PMs are 25 h apart and nothing else about
    them differs.

    Setup: S2 with `pm_cost = 4.0`, `pm_fixed_cost = 7.0` and a 2-hour
    periodic PM on A. S2 has no resource pool, so this also pins that the PM
    cost needs no pool: the pool charge is a separate term, added only when
    the block borrows one.

    Expected: A's `PM` column reads `30.00` -- `2 * (4.0 * 2 + 7.0)`.

    Source: Raptor 7 (the PM branch of the cost section)
    """
    spec = spine(2)
    spec["blocks"][0].update(
        uses_pm=True, pm_freq=25.0, pm_dist=FIXED, pm_params=[2.0],
        pm_refresh=False, pm_fail_reset=False)
    run = _costed(spec)
    assert _operating(run.eor, "A", "PM") == "30.00"


def test_a_pm_that_cannot_get_the_repair_pool_waits_and_charges_the_pmhold_rate() -> None:
    """With `PMReqDefer` off, a PM that cannot get the repair resource holds
    in `pmHold` and charges the PM-hold rate for the wait.

    Why: `PMHold` is a state of its own in the cost dispatch,
   distinct from `RepHold` and from `PM`. The
    state is only reachable when the model asks for it: with `PMReqDefer`
    set (the default) the source does *not* hold, it logs a hand-built
    `pmReqDefer` line and hands the block back to `Run`,
   which charges nothing. This model turns the
    flag off, so A holds.

    Setup: S4 with `pm_req_defer = False`, A's PM due at 25, and B changed
    to fail at 20 with a 20 h repair so it holds the only unit of `R` across
    A's PM.

    Expected: A's `PMHold` column reads `11.25` -- `0.75 * 15`, the hours
    from 25 to 40 -- and its `PM` column reads `15.00` for the single 2-hour
    PM that follows.

    Source: Raptor 7 (the pmReqDefer branch of ObtainPMprereqs),
    Raptor 7 (the PMHold branch of the cost section)
    """
    spec = spine(4)
    for block in spec["blocks"]:
        if block["name"] == "A":
            block["pm_req_defer"] = False
        else:
            block["fail_params"] = [20.0]
            block["repair_params"] = [20.0]
    run = _costed(spec)
    assert _operating(run.eor, "A", "PMHold") == "11.25"
    assert _operating(run.eor, "A", "PM") == "15.00"


# ---------------------------------------------------------------------------
# Resource pools
# ---------------------------------------------------------------------------

def test_a_repair_pool_is_charged_per_time_unit_while_a_block_holds_it() -> None:
    """A block borrowing from a `RESOURCE` pool charges that pool its
    per-time-unit rate for the whole borrow, and nothing for the queue.

    Why: Raptor 7 adds `stateTime * costPerTime * numRes1`
    to the *pool's* accumulator while the block is `Repairing`, gated on
    `numDiffRes = 1`. The `RepHold` hours before the grant are not repairing
    hours, so they are not charged to the pool either -- the pool's cost is
    strictly the borrow.

    Setup: S3 with the pool's `per_time_unit = 0.5` and `num_in = 1`, both
    blocks borrowing one unit for their repairs.

    Expected: the pool row reads `[R, 1.00, 2.00, 7.50, 10.50]` -- the
    `PerTimeCost` column is `0.5 * 10` for A's repair and `0.5 * 5` for B's,
    `7.50` in total, and B's five queued hours contribute nothing to it.

    Source: Raptor 7 (the resource-pool time cost)
    """
    spec = spine(3)
    spec["resources"][0]["per_time_unit"] = 0.5
    run = _costed(spec)
    assert _row(run.eor, "Resources", "R") == ["R", "1.00", "2.00", "7.50",
                                              "10.50"]


def test_a_pm_is_charged_the_pools_pm_units_not_the_pools_repair_units() -> None:
    """A PM draws on the same pool as the repair but is charged its own
    `numRes1pm` units, on top of the repairs' charge.

    Why: Raptor 7 is the PM twin of and
    multiplies by `numRes1pm`, not `numRes1` -- a model that borrows two
    units to repair and one to PM pays for two repair-hours and one
    PM-hour. This model borrows one of each, so the pool's total is the two
    repairs plus the two PMs.

    Setup: S4 with the pool's `per_time_unit = 0.5`. The pool is borrowed four
    times: A's PM 25->27 and again 87->89, A's repair 52->62, and B's repair
    62->67 after it queued for the pool from 55.

    Expected: the pool's `PerUseCost` column reads `4.00` -- four borrows at
    1.00 each -- and its `PerTimeCost` column reads `9.50` -- `0.5 * 2` twice
    over for the PMs `+ 0.5 * 10` for A's repair `+ 0.5 * 5` for B's. On S3,
    which has no PM at all, the same pool reads `7.50` and `2.00`: without a
    PM there are two borrows and no PM-hours.

    Source: Raptor 7 (the PM resource-pool time cost)
    """
    spec = spine(4)
    spec["resources"][0]["per_time_unit"] = 0.5
    run = _costed(spec)
    assert _row(run.eor, "Resources", "R") == ["R", "1.00", "4.00", "9.50",
                                              "14.50"]


# ---------------------------------------------------------------------------
# Disposal
# ---------------------------------------------------------------------------

def test_a_block_with_add_done_charges_its_disposal_cost_at_the_run_end_flush() -> None:
    """`addDone` buys one disposal charge, and it is charged at the run-end
    flush rather than when the block reaches `Done`.

    Why: Raptor 7 charges `doneFixedCost` only
    `IF runCompleted`, i.e. only on the end-of-run `End_sim` sweep -- so a
    block that reaches `Done` mid-run (spare exhaustion, say) has not
    charged disposal, and one that never does anything unusual still has
    charged it by the time the report is written.

    Setup: S1 with `add_done = True` and `done_fixed = 3.0`.

    Expected: A's `Disposal` column reads `3.00`, once, and its `Done` column
    reads `0.00` -- the block spends no time in `Done` before the run ends,
    so the per-time done rate has nothing to charge.

Source: raptor/core/engine/accounting.py (the `always_add_done_cost` charge when the run completes)
"""
    run = _costed(spine(1), add_done=True, done_fixed=3.0)
    assert _row(run.eor, "Life_cycle_cost", "A") == [
        "A", "1.00", "215.00", "1.00", "3.00", "220.00"]
    assert _operating(run.eor, "A", "Done") == "0.00"


# ---------------------------------------------------------------------------
# Exactness
# ---------------------------------------------------------------------------

def test_a_fixed_life_makes_every_cost_column_exact() -> None:
    """With `Fixed` lives every cost column is hand arithmetic to the cent,
    and the run draws no randomness at all to produce it.

    Why: rule B2's reason in one assertion. `Fixed`'s draw is `param[0]` and
    touches no stream (`rng.DrawNumber`'s `WHEN 19`), so every hour count in
    the cost section is the integer the spine fixture names, and the printed
    figure is reproducible without a seed or a tolerance. `Run.draws == 0` is
    the measurement that says it: the numbers did not come from a coin.

    Setup: S1 with the full rate set of this module.

    Expected: `draws == 0`, and the whole `***COST***` block -- Good
    `2.0 * 90`, Repair `3.0 * 10 + 5.0`, one spare at 1.00, one purchase at
    1.00, grand total `1 + 215 + 1`.

    Source: Raptor 7 (the cost section);
    docs/engine-design/module-map.adoc (the accounting.py row)
    """
    run = _costed(spine(1))
    assert run.draws == 0
    assert _row(run.eor, "Operating", "A") == [
        "A", "0", "180.00", "35.00", "0.00", "0.00", "0.00", "0.00",
        "0.00", "0.00"]
    assert _row(run.eor, "Life_cycle_cost", "A") == [
        "A", "1.00", "215.00", "1.00", "0.00", "217.00"]
    assert "Grand_Total               217.00" in run.eor


def test_the_run_total_is_carried_into_the_final_results_as_a_cross_run_statistic() -> None:
    """With cost analysis on, the Final Results SUMMARY opens with a
    `Total_Cost` row: the whole run's cost, aggregated over runs like every
    other stat.

    Why: Raptor 7 writes the cross-run statistics of each
    run's own `Grand_Total` immediately *before* the capacity rows, so a
    total-cost figure can be compared across runs without reading the
    per-run End-of-Run sections. It is a five-column row like every other,
    which is why it needs three runs to say anything: with one run the
    spread columns are `N/A`.

    Setup: S1 with the full rate set and `num_runs = 3`. Three runs because
    the claim is about aggregation; nothing is stochastic, so no seed is
    needed and no tolerance applies -- `Run.draws == 0` is asserted, which
    is what makes `0.00` exact rather than merely small.

    Expected: `['Total_Cost', '217.00', '217.00', '217.00', '0.00', '0.00']`
    -- the same 217.00 as the single run's grand total, all three runs alike.

    Source: Raptor 7 (the Final_Results Total Cost row)
    """
    run = _costed(spine(1), runs=3)
    assert run.draws == 0
    assert [line.split() for line in run.final
            if line.startswith("Total_Cost")] == [
        ["Total_Cost", "217.00", "217.00", "217.00", "0.00", "0.00"]]


# ---------------------------------------------------------------------------
# The interim-cost sink: an output option that changes the trajectory
# ---------------------------------------------------------------------------

#: Seed for the one model in this section that has to be stochastic. The claim
#: is about
#: *which* draw happens first, not about how large a draw is, so both runs
#: are salted identically and the two resulting times are compared as
#: literals. No tolerance is needed or wanted: `RaptorStream` is an integer
#: generator, so a seeded value is exact on any machine.
SEED = 1234


def _phased_failsim() -> dict[str, Any]:
    """S1 turned into a phased FailSim model with a trigger, both drawing
    from the shared system stream 11.

    Two things are load-bearing, and both are about *draw order*:

    * **FailSim, not TimeSim.** `kickoff._start_triggers` decides
      `_phase_first = termType <> TimeSim AND NOT costFile`
      (`kickoff.py:755-756`), so a TimeSim model is always
      triggers-first and the option below cannot move anything.
    * **A trigger.** With no trigger there is nothing to draw before the
      phase duration, so the two orderings draw the same numbers in the same
      order and the trajectories agree.

    The phase durations and the trigger are `UniformReal` (distribution code
    10) so that both draws land inside the run: phase 1 in `[10, 30]` and
    the trigger in `[40, 60]`, over a run that ends when the one block fails
    at 50 and is repaired by 60.

    `make_trigger_row` needs three parameter cells, so the trigger's params are
    `[40.0, 60.0, 0.0]` -- the third is unused by `UniformReal`.
    """
    spec = spine(1, run_length=200.0)
    spec["system"].update({"termination": "FailSim", "term_condition": 1.0,
                           "cost_analysis": True})
    spec["phases"] = [
        {"name": "P1", "mission": 1, "dist": 10, "params": [10.0, 30.0]},
        {"name": "P2", "mission": 0, "dist": 10, "params": [30.0, 40.0]},
    ]
    spec["triggers"] = [{"name": "T1", "init_used": 0.0, "dist": 10,
                         "params": [40.0, 60.0, 0.0], "repeats": 1}]
    return spec


def _phase_boundary_time(log: list[str]) -> str:
    """The event-log line where the first phase ends, or a loud failure."""
    for line in log:
        if "Begin_phase_P2" in line:
            return line
    raise AssertionError(
        "the run never reached the P1 -> P2 boundary; its log was:\n"
        + "\n".join(log))


def test_requesting_the_interim_cost_stream_moves_the_phase_draw_behind_the_trigger() -> None:
    """Passing a cost sink changes a phased FailSim run's trajectory, not
    just its output.

    Why: this is real Raptor behaviour, documented and measured at
    `driver.py:8-37` -- ticking *Interim Costs* in the Simulation dialog
    changes the simulated trajectory of a phased CycleSim/FailSim model whose
    triggers share system stream 101 (stream 11 here) with the phase
    schedule. The port reproduces it at one line:
    `_cost_file_on = cost_sink is not None AND costAnalysis`, and
    `_phase_first = termType <> TimeSim AND NOT _cost_file_on`
    (`kickoff.py:755-756`). Without the sink the phase schedule is queued
    first and its duration draw comes off the shared stream first; with the
    sink, `TELL System TO CalculateInterimCost` is queued ahead of
    `TELL System TO ControlPhaseChanges` and
    the trigger draws first, so the phase duration is the *second* number off
    the stream. Every later draw on that stream is shifted too.

    Setup: the phased FailSim model above, both runs salted with `SEED =
    1234`, differing only in whether a cost sink list is passed to
    `run_simulation_with_reports`.

    Expected: the P1 -> P2 boundary lands at 14.931518 h with no sink and at
    20.891688 h with one -- the same two numbers off the stream, swapped.
    (The harness is still the only constructor: `build_from_spec` is reached
    through `harness.run`, and the batch is then re-run from the built model
    because `cost_sink` is an argument of the runner, not a field of a spec,
    so `harness.run` has no way to pass one.)

    Source: driver.py:8-37 (the measured behaviour and its mechanism),
    kickoff.py:755-756 (the port's `_phase_first`);
    Raptor 7 (the two TELLs)
    """
    rbd = harness.run(_phased_failsim(), seed=SEED).rbd
    plain = _runner.run_simulation_with_reports(
        rbd, 1, date=harness.REPORT_DATE, seed=SEED)[0][0]
    costed = _runner.run_simulation_with_reports(
        rbd, 1, date=harness.REPORT_DATE, seed=SEED, cost_sink=[])[0][0]

    assert _phase_boundary_time(plain).split() == [
        "14.931518", "System", "Phase_change", "Phase_change", "Begin_phase_P2"]
    assert _phase_boundary_time(costed).split() == [
        "20.891688", "System", "Phase_change", "Phase_change", "Begin_phase_P2"]
    assert plain != costed


# ---------------------------------------------------------------------------
# The interim snapshot: the same tables, mid-run
# ---------------------------------------------------------------------------

def _interim_snapshots(spec: dict[str, Any], interval: float,
                       **rates: Any) -> list[list[str]]:
    """*spec* with cost analysis on, run with the Interim Costs output
    requested every *interval* hours: the report body of each tick, in order.

    This is the port's stand-in for ticking **Interim Costs** in the
    Simulation dialog (`driver.py:8-37`), which is the only way
    `costs.interim_cost_snapshot` is ever reached: it is the body of a
    scheduled tick, not of the run (`kickoff.py:894-921`). The sink is a list
    the runner appends to, and it is the only way to see the snapshot -- the
    End-of-Run report is built by `build_cost_run_report`, a different
    function with the same columns.

    The harness is still the only constructor: `build_from_spec` is reached
    through `harness.run`, and the batch is then re-run from the built model
    because `cost_sink` is an argument of the runner, not a field of a spec.
    `cost_plot_interval` is the runner's other argument, and it is what makes
    the ticks land where the test wants them.
    """
    for block in spec["blocks"]:
        block.update({**RATES, **rates})
    rbd = harness.run(spec, cost_analysis=True).rbd
    sink: list[str] = []
    _runner.run_simulation_with_reports(
        rbd, 1, date=harness.REPORT_DATE, cost_sink=sink,
        cost_plot_interval=float(interval))
    snapshots: list[list[str]] = []
    current: list[str] | None = None
    for line in sink:
        if line.startswith("SimTime="):
            current = [line]
            snapshots.append(current)
        elif current is not None:
            current.append(line)
    return snapshots


def _interim(snapshots: list[list[str]], t: float) -> list[str]:
    """The snapshot taken at sim time *t*."""
    for body in snapshots:
        if float(body[0].split("=", 1)[1]) == float(t):
            return body
    raise AssertionError(
        f"no interim snapshot at t={t:g}; the ticks were at "
        + ", ".join(f"{float(b[0].split('=', 1)[1]):g}" for b in snapshots))


def _interim_cell(body: list[str], name: str, column: str,
                  *identifying: str) -> str:
    """One cell of *name*'s row in the interim table whose header carries
    every word in *identifying*.

    The interim report prints the same fixed-column tables as the End-of-Run
    `***COST***` section, so which table is meant is named by a few of its own
    header words (`InHier`+`Good`+`Done` for the per-state block table,
    `HierNum`+`Events`+`Total` for the hierarchy table) rather than by
    position. The column index comes from that header, so a test names a
    column instead of counting spaces.
    """
    head = next(i for i, line in enumerate(body)
                if all(word in line.split() for word in identifying))
    header = body[head].split()
    for line in body[head + 1:]:
        if not line.strip():
            break
        row = line.split()
        if row[0] == name:
            return row[header.index(column)]
    raise AssertionError(
        f"no {name!r} row in the interim table with header {' '.join(header)}")


def _unrepairable(*, done_per_time: float, done_fixed: float,
                  add_done: bool, life: float, run_length: float
                  ) -> dict[str, Any]:
    """One block with `SpType = "None"` and a `Done` rate -- the model that
    reaches `Done` **mid-run** and therefore spends the rest of the run with no
    path home.

    `SpType = "None"` is how a block is given no sparing at all
    (`docs/engine-design/pools-spares-resources.adoc`, "Port-only devices
    and deliberate deviations": a ColdPool or Resource `SpType` string loads as
    "no sparing"), so `CheckForSpare` takes `Done "No_spares"` and there is no
    repair. The `Done` rates are arguments because they are the subject; the
    rest of the rate set is applied by the caller (`_interim_snapshots` or
    `_costed`), so every number a test reads off this model is hand
    arithmetic.
    """
    return {
        "system": {"run_length": float(run_length), "num_runs": 1,
                   "termination": "TimeSim"},
        "blocks": [{"name": "A", "fail_dist": FIXED,
                    "fail_params": [float(life)], "repair_dist": FIXED,
                    "repair_params": [5.0], "sp_type": "None",
                    "done_per_time": float(done_per_time),
                    "done_fixed": float(done_fixed),
                    "add_done": bool(add_done)}],
        "nodes": [{"name": "Start", "type": 1, "n": 0, "out": 1},
                  {"name": "End", "type": 3, "n": 1, "out": 0}],
        "links": [{"from": "Start", "to": "A"}, {"from": "A", "to": "End"}],
    }


def test_the_interim_snapshot_charges_the_repair_underway_and_the_queue_in_front_of_it() -> None:
    """Mid-run, the snapshot charges the state each block is *in* -- so the
    block being repaired is in `Repair` and the one waiting for the pool is in
    `RepHold`, at the same tick.

    Why: `interim_cost_snapshot` folds each block's current state's
    `stateTime * rate` into the column of that state
    (`costs.py:198-212`), which is how the snapshot can differ from the
    End-of-Run row built from the accumulators alone: the End-of-Run table
    charges a state only once the block has *left* it, so an unfinished repair
    is in neither table until the run ends. In S3 at t=60, A has been repairing
    since t=50 and B has been queued behind A's single repair unit since t=55.

    Setup: S3 with this module's `RATES` (`rep_per_time = 3.0`, `rep_fixed =
    5.0`, `rep_hold_cost = 1.0`, `running_cost = 2.0`), ticking every 20
    hours.

    Expected: A reads `35.00` Repair (`3.0 * 10 + 5.0`, the fixed cost charged
    once, mid-repair rather than at the end) and `100.00` Good (`2.0 * 50`); B
    reads `5.00` RepHold (`1.0 * 5`) and `0.00` Repair -- its repair has not
    started, so the fixed cost is not charged yet either.

    Source: costs.py:198-212 (the snapshot's per-block dispatch);
    Raptor 7 (the repair branch whose accrual this mirrors)
    """
    snapshots = _interim_snapshots(spine(3), 20.0)
    body = _interim(snapshots, 60.0)
    assert _interim_cell(body, "A", "Repair", "InHier", "Good", "Done") == "35.00"
    assert _interim_cell(body, "A", "Good", "InHier", "Good", "Done") == "100.00"
    assert _interim_cell(body, "B", "RepHold", "InHier", "Good", "Done") == "5.00"
    assert _interim_cell(body, "B", "Repair", "InHier", "Good", "Done") == "0.00"


def test_the_interim_snapshot_charges_a_repair_that_borrows_no_pool() -> None:
    """A repair that borrows no resource pool is charged exactly like one that
    does -- only the pool's own table depends on the borrow.

    Why: the pool term in the snapshot is gated on `numDiffRes = 1`
    (`costs.py:209-212`), and a
    block whose `NumRes` is anything but 1 borrows nothing at all
    (`docs/engine-design/pools-spares-resources.adoc`, "Port-only devices":
    source's check is `numDiffRes=1`, so any other value borrows nothing). S1
    has no resource pool at all, so its repair has no pool to charge -- and
    must still be charged its own rate and its own fixed cost.

    Setup: S1 over 70 hours, ticking every 5, with the same repair rates as
    above. A fails at t=50 and repairs for 10 hours with `Fixed` 10.

    Expected: the t=55 snapshot reads `20.00` Repair -- `3.0 * 5 + 5.0`, five
    hours in and the fixed cost already charged once. A snapshot that skipped
    the whole block because it borrowed nothing would read `0.00`.

    Source: costs.py:209-212 (the interim pool term);
    docs/engine-design/pools-spares-resources.adoc (Port-only devices and
    deliberate deviations: `NumRes` other than 1 uses no resource)
    """
    snapshots = _interim_snapshots(spine(1, run_length=70.0), 5.0)
    body = _interim(snapshots, 55.0)
    assert _interim_cell(body, "A", "Repair", "InHier", "Good", "Done") == "20.00"
    assert _interim_cell(body, "A", "Good", "InHier", "Good", "Done") == "100.00"


def test_the_interim_snapshot_charges_the_hours_a_pm_waits_for_the_pool() -> None:
    """A block in `pmHold` is charged its PM-hold rate by the snapshot, at the
    tick, without the PM itself having started.

    Why: `pmHold` is a state with its own interim column
    (`costs.py:213-214`), reached only with `PMReqDefer` off -- with the flag
    on, the source logs a hand-built `pmReqDefer` line and leaves the block in
    `PM_is_due`, which is not a `BlockStatus` and so is charged by neither
    this snapshot nor the cost section. This is the
    same fixture `test_b_costs.py`'s PMHold cost test uses, read mid-run.

    Setup: S4 with `pm_req_defer = False` on A and B failing at 20 for 20
    hours, so it holds the only unit of `R` across A's PM; ticks every 10.

    Expected: at t=30, A reads `3.75` PMHold (`0.75 * 5`, the hours from 25 to
    30) and `0.00` PM -- the PM has not started, so its own rate and its fixed
    cost are not charged yet, which is exactly the distinction the two columns
    are there to draw.

    Source: costs.py:213-214 (the interim PMHold arm);
    Raptor 7 (the pmReqDefer branch of ObtainPMprereqs)
    """
    spec = spine(4)
    for block in spec["blocks"]:
        if block["name"] == "A":
            block["pm_req_defer"] = False
        else:
            block["fail_params"] = [20.0]
            block["repair_params"] = [20.0]
    body = _interim(_interim_snapshots(spec, 10.0), 30.0)
    assert _interim_cell(body, "A", "PMHold", "InHier", "Good", "Done") == "3.75"
    assert _interim_cell(body, "A", "PM", "InHier", "Good", "Done") == "0.00"


def test_the_interim_snapshot_charges_a_pm_that_borrows_no_pool() -> None:
    """A PM on a block with no resource pool is charged `rate * hours + fixed`
    mid-PM, and the PM's pool term is simply absent.

    Why: the PM arm of the snapshot is `pm_cost * st + pm_fixed_cost` plus a
    pool term gated on `numDiffRes = 1` that multiplies `numRes1pm`
    (`costs.py:215-220`). The
    fixed part is charged as soon as the snapshot sees the block in `PM`, not
    when the PM ends -- so a tick inside a long PM shows the whole fixed cost
    already paid.

    Setup: S2 (no resource pool) with a 10-hour `Fixed` PM on A due at t=25,
    `pm_cost = 4.0`, `pm_fixed_cost = 7.0`, ticking every 5.

    Expected: at t=30, A reads `27.00` PM -- `4.0 * 5 + 7.0` -- with the PM
    still running until t=35. Charging the fixed part at PM completion instead
    would read `20.00` here.

    Source: costs.py:215-220 (the interim PM arm); Raptor 7
    (the PM resource-pool time cost this gates)
    """
    spec = spine(2)
    for block in spec["blocks"]:
        if block["name"] == "A":
            block.update(uses_pm=True, pm_freq=25.0, pm_dist=FIXED,
                         pm_params=[10.0], pm_refresh=False,
                         pm_fail_reset=False)
    body = _interim(_interim_snapshots(spec, 5.0), 30.0)
    assert _interim_cell(body, "A", "PM", "InHier", "Good", "Done") == "27.00"
    assert _interim_cell(body, "A", "Good", "InHier", "Good", "Done") == "50.00"


def test_the_interim_snapshot_charges_the_pool_for_a_pm_in_progress() -> None:
    """Halfway through a PM, the resource pool is already charged for half the
    borrow -- the snapshot charges the pool as the hours pass, not at the end.

    Why: the interim snapshot keeps its own pool accumulator
    (`costs.py:194-195`, `pools_add_delta`) because the End-of-Run pool table
    is built from accumulators the cost section fills on the way *out* of
    `Repairing` and `PM`
    -- so a borrow still in progress is in neither table until the state ends.
    The PM's term multiplies `numRes1pm`, not `numRes1`, which is the
    PM/repair distinction the End-of-Run pool test above pins; what is new here
    is the *timing*. This report also swaps the pool table's middle columns
    against the End-of-Run one, so the borrow prints under `PerUseCost` in the
    interim table and under `PerTimeCost` at the end of the run.

    Setup: S4 over 40 hours -- the run ends just after A's PM, so every tick is
    one that matters -- with the pool's `per_time_unit = 0.5` and one unit,
    ticking every hour so one tick lands inside A's two-hour PM (t=25 to t=27).

    Expected: at t=26, A reads `11.00` PM (`4.0 * 1 + 7.0` -- the PM has not
    ended, and the fixed cost is already paid) and the pool row reads
    `1.00 / 0.50 / 1.00 / 2.50`: `PerUseCost` is `0.5 * 1` for the one PM-hour
    borrowed so far -- half the `1.00` the same column reads at t=27, once the
    PM ends -- and `PerTimeCost` is the pool's own one-unit per-use charge,
    counted at the grant. So the pool is charged for a borrow *in progress*,
    which is the one thing the End-of-Run pool table cannot show.

    Source: costs.py:194-220 (the snapshot's pool delta and its `numRes1pm`
    term); Raptor 7 (the PM resource-pool time cost)
    """
    spec = spine(4, run_length=40.0)
    spec["resources"][0]["per_time_unit"] = 0.5
    snapshots = _interim_snapshots(spec, 1.0)
    body = _interim(snapshots, 26.0)
    assert _interim_cell(body, "A", "PM", "InHier", "Good", "Done") == "11.00"
    assert [_interim_cell(body, "R", column, "Initial", "PerUseCost",
                          "PerTimeCost")
            for column in ("Initial", "PerUseCost", "PerTimeCost",
                           "Total")] == ["1.00", "0.50", "1.00", "2.50"]
    assert [_interim_cell(_interim(snapshots, 27.0), "R", column, "Initial",
                          "PerUseCost", "PerTimeCost")
            for column in ("PerUseCost", "Total")] == ["1.00", "3.00"]


def test_the_interim_snapshot_charges_a_linked_phantom_as_standby() -> None:
    """A block that is `Running` but `Linked` is charged the **standby** rate,
    not the operating rate.

    Why: the snapshot's `Running` arm dispatches on the *active* status
    (`costs.py:221-229`): Active is operating,
    Linked is standby, Cut is idle. That is the whole reason a cold-linked
    phantom is cheaper than a running block, and it is invisible in the
    End-of-Run table unless the block leaves the state -- which this one never
    does.

    Setup: S2 with two phases and A phased `Linked` in both
    (`phase_states`), so it is `Running`+`Linked` from kickoff to the end;
    `running_cost = 2.0`, `standby_cost = 0.25`, ticks every 20.

    Expected: at t=20, A reads `5.00` Standby (`0.25 * 20`) and `0.00` Good.
    A at the operating rate would read `40.00`, and at idle `10.00`; and B,
    which is phased in neither phase, reads `40.00` Good in the same table.

    Source: costs.py:221-229 (the interim Running arm)
    """
    spec = spine(2)
    spec["phases"] = [
        {"name": "P1", "mission": 1, "dist": FIXED, "params": [50.0]},
        {"name": "P2", "mission": 1, "dist": FIXED, "params": [50.0]},
    ]
    spec["phase_states"] = {"A": ["L: 1.000", "L: 1.000"]}
    body = _interim(_interim_snapshots(spec, 20.0), 20.0)
    assert _interim_cell(body, "A", "Standby", "InHier", "Good", "Done") == "5.00"
    assert _interim_cell(body, "A", "Good", "InHier", "Good", "Done") == "0.00"
    assert _interim_cell(body, "B", "Good", "InHier", "Good", "Done") == "40.00"


def test_the_interim_snapshot_charges_a_cut_phantom_as_idle() -> None:
    """A block that is `Running` but `Cut` is charged the **idle** rate -- the
    third arm of the dispatch that charges a `Linked` one the standby rate.

    Why: the snapshot's `Running` arm splits three ways on the *active* status
    (`costs.py:221-229`): Active is operating, Linked
    is standby, Cut is idle. The Cut arm is the one a dispatch is most likely
    to lose, because a cut block has neither failed nor been switched off --
    it is simply not counted by its node this phase, and nothing else in the
    report says it is idle. Asserting `Standby` and `Good` as well is what
    would catch a copy of the Linked arm.

    Setup: S2 with two phases and A phased `Cut` in both, so it is
    `Running`+`Cut` from kickoff; `idle_cost = 0.5`, `standby_cost = 0.25`,
    `running_cost = 2.0`, ticks every 20.

    Expected: at t=20, A reads `10.00` Idle (`0.5 * 20`) with `0.00` Good and
    `0.00` Standby, while B reads `40.00` Good in the same table.

    Source: costs.py:221-229 (the interim Running arm)
    """
    spec = spine(2)
    spec["phases"] = [
        {"name": "P1", "mission": 1, "dist": FIXED, "params": [50.0]},
        {"name": "P2", "mission": 1, "dist": FIXED, "params": [50.0]},
    ]
    spec["phase_states"] = {"A": ["C: 1.000", "C: 1.000"]}
    body = _interim(_interim_snapshots(spec, 20.0), 20.0)
    assert _interim_cell(body, "A", "Idle", "InHier", "Good", "Done") == "10.00"
    assert _interim_cell(body, "A", "Standby", "InHier", "Good", "Done") == "0.00"
    assert _interim_cell(body, "A", "Good", "InHier", "Good", "Done") == "0.00"
    assert _interim_cell(body, "B", "Good", "InHier", "Good", "Done") == "40.00"


def test_the_interim_snapshot_charges_a_demoted_cold_member_as_standby() -> None:
    """A cold-standby member demoted at kickoff is charged the standby rate
    from the first tick, whether or not it ever comes back.

    Why: `Standby` is its own interim column (`costs.py:230-231`) reached by the
    block's *operating* status, which is what distinguishes a cold member (a
    block switched down by `RunColdLogic`) from a `Linked` phantom above: both
    print in the Standby column, for different reasons and from different
    arms. S8's cold group keeps C running, so D is demoted at t=0 with
    `Too_many_on` and stays down until C fails at t=50.

    Setup: S8, ticking every 20, with `standby_cost = 0.25`.

    Expected: at t=20, D reads `5.00` Standby (`0.25 * 20`) and `0.00` Good,
    while the three blocks that are running read `40.00` Good each.

    Source: costs.py:230-231 (the interim Standby arm);
    docs/engine-design/node-status-and-cold-standby.adoc (Source anchors)
    """
    body = _interim(_interim_snapshots(spine(8), 20.0), 20.0)
    assert _interim_cell(body, "D", "Standby", "InHier", "Good", "Done") == "5.00"
    assert _interim_cell(body, "D", "Good", "InHier", "Good", "Done") == "0.00"
    assert _interim_cell(body, "A", "Good", "InHier", "Good", "Done") == "40.00"


def test_the_interim_snapshot_charges_a_block_the_run_has_already_finished() -> None:
    """A block already `Done` is charged its `Done` rate for the hours it has
    been waiting, mid-run.

    Why: the snapshot's `Done` arm (`costs.py:232-233`) is the mirror of the
    End-of-Run `Done -> Done` flush: `Done` is terminal, so a block that
    reached it at t=20 can only ever be charged for those hours by a *later*
    reading of the same state -- which is what the snapshot does, once per
    tick, from `state_start_time`. Without that arm the `Done` column would be
    zero in every interim report and only appear at the end of the run.

    Setup: one block with `SpType = "None"` and `done_per_time = 2.0`, failing
    at t=20 over a 70-hour run, ticking every 10.

    Expected: at t=50, A reads `60.00` Done -- `2.0 * 30`, the hours since it
    went `Done`, and not `2.0 * 50`, which would be measuring from kickoff
    instead of from the state it is in.

    Source: costs.py:232-233 (the interim Done arm);
    docs/engine-design/pools-spares-resources.adoc (Source anchors:
    `CheckForSpare`)
    """
    spec = _unrepairable(done_per_time=2.0, done_fixed=3.0, add_done=True,
                         life=20.0, run_length=70.0)
    body = _interim(_interim_snapshots(spec, 10.0), 50.0)
    assert _interim_cell(body, "A", "Done", "InHier", "Good", "Done") == "60.00"
    assert _interim_cell(body, "A", "Good", "InHier", "Good", "Done") == "40.00"


def test_a_block_that_was_already_done_is_charged_its_wait_once_and_its_disposal() -> None:
    """A block that went `Done` mid-run is charged `Done` for the hours it
    waited -- assigned once, not accumulated -- and its disposal once.

    Why: the run-end flush writes a `Done -> Done` row for every block, even
    one that reached `Done` earlier by its own event (`driver.finalize`), and the cost section's `Done -> Done` arm
    **assigns** `doneCost * stateTime` and `doneFixedCost` rather than adding
    to them (`accounting.py:113-115`, "source: assignment, not +="). The
    distinction is observable: an accumulating implementation would show the
    hours twice, once from the interim ticks that also read the state and once
    from the flush.

    Setup: the same unrepairable block over a 70-hour run, so A goes `Done` at
    t=20 and is still there at the flush; `done_per_time = 2.0`,
    `done_fixed = 3.0`, `add_done = True`.

    Expected: A's `Done` column reads `100.00` -- `2.0 * 50`, the fifty hours
    from t=20 to t=70 -- and its `Disposal` column reads `3.00`, once. The
    `Operating` column reads `40.00` (`2.0 * 20`), which is the other half of
    the run and the reason the two columns together account for all 70 hours.

    Source: accounting.py:113-115 (the `Done -> Done` arm),
    Raptor 7 (the run-completed disposal charge)
    """
    spec = _unrepairable(done_per_time=2.0, done_fixed=3.0, add_done=True,
                         life=20.0, run_length=70.0)
    run = _costed(spec)
    assert run.draws == 0
    assert _operating(run.eor, "A", "Done") == "100.00"
    assert _operating(run.eor, "A", "Good") == "40.00"
    assert _row(run.eor, "Life_cycle_cost", "A") == [
        "A", "1.00", "140.00", "0.00", "3.00", "144.00"]


# ---------------------------------------------------------------------------
# Hierarchy rollup
# ---------------------------------------------------------------------------

def _hierarchy(*, parent: int) -> dict[str, Any]:
    """One hierarchy `H1` holding one block and one event, with its `Parent`
    column set to *parent*.

    The shape is the minimum a hierarchy needs: the `HIERARCHY_DATA` row, its
    two marker nodes (`Type=4` in, `Type=5` out, each with `parent` equal to
    the hierarchy's own id), and links crossing the boundary addressed at the
    hierarchy itself. `A` never fails (its life is past the run end) and `E1`
    succeeds at t=0, so the only thing that accumulates is running time -- at
    the file's default operating rate of 1.0 over 40 hours -- and one event
    success, which is what makes the hierarchy row's arithmetic `1.00` purchase
    + `40.00` operating + `4.00` events.
    """
    return {
        "system": {"run_length": 40.0, "num_runs": 1, "termination": "TimeSim"},
        "hierarchies": [{"id": 1, "name": "H1", "in_id": 2, "out_id": 4,
                         "level": 1, "parent": int(parent)}],
        "blocks": [{"name": "A", "parent": 1, "fail_dist": FIXED,
                    "fail_params": [200.0], "repair_dist": FIXED,
                    "repair_params": [5.0]}],
        "events": [{"name": "E1", "parent": 1, "dist": FIXED, "failVal": 1.0,
                    "init_cost": 1.0, "op_cost": 3.0, "rep_cost": 2.0}],
        "nodes": [
            {"id": 1, "name": "Start", "type": 1, "n": 0, "out": 1},
            {"id": 2, "name": "in", "type": 4, "n": 1, "out": 1, "parent": 1},
            {"id": 4, "name": "out", "type": 5, "n": 1, "out": 1, "parent": 1},
            {"id": 5, "name": "End", "type": 3, "n": 1, "out": 0},
        ],
        "links": [
            {"from": "Start", "to": "in"},
            {"from": "in", "to": "A"},
            {"from": "A", "to": "E1"},
            {"from": "E1", "to": "out"},
            {"from": "out", "to": "End"},
        ],
    }


def test_the_interim_snapshot_rolls_an_event_inside_a_hierarchy_into_its_events_column() -> None:
    """Mid-run, a hierarchy's `Events` column carries the events that belong to
    it, and they are in its `Total` as well.

    Why: the snapshot builds a hierarchy row from its own blocks and events
    and only then folds nested hierarchies into their parents
    (`costs.py:280-299`),
    so an event's cost reaches the hierarchy through two
    additions -- its own `Events` and its `Total` -- and a hierarchy with no
    events must show `0.00` in both. The event's own row is priced once per
    outcome (`events.py:88`, a flat `operating_cost` per success), not per
    hour, which is why `E1`'s total is `1.00` initial + `3.00` success.

    Setup: the hierarchy model above, ticking every 20 hours.

    Expected: at t=20, `H1` reads `4.00` in `Events` and `45.00` in `Total`
    -- `1.00` purchase + `40.00` operating + `0.00` events' purchase, the
    event's own row agreeing at `4.00` Total with `1.00` Initial and `3.00`
    Success.

    Source: costs.py:280-299 (the interim hierarchy rollup);
    Raptor 7 (the source's event rollup into HierCosts)
    """
    body = _interim(_interim_snapshots(_hierarchy(parent=0), 20.0), 20.0)
    assert _interim_cell(body, "H1", "Events", "HierNum", "Events", "Total") == "4.00"
    assert _interim_cell(body, "H1", "Total", "HierNum", "Events", "Total") == "45.00"
    assert _interim_cell(body, "E1", "Total", "InHier", "Success", "Total") == "4.00"


def test_a_hierarchy_whose_parent_is_not_in_the_model_keeps_its_own_row() -> None:
    """A hierarchy naming a `Parent` that is not in the model keeps its own
    costs: the rollup skips it rather than losing the row.

    Why: `rollup_nested_hier_costs` is the `mamaHier` rollup
   and its second guard is `IF (mamaHier=NULL)
    CONTINUE` -- a child hierarchy whose parent row is not there has nowhere to
    roll into, so it keeps what it accumulated. That is a shape the builder
    allows and only warns about for *blocks*
    (`build.check_new_block`: a `parent` naming no hierarchy is a soft
    problem, not a hard error), so the run still completes and the hierarchy
    still prints.

    Setup: the same hierarchy model twice, differing only in the `Parent`
    column of `H1`: `0` (top level) and `5` (no hierarchy 5 in the model).

    Expected: the `Hierarchy` row is identical in both runs --
    `['H1', '1', '1.00', '40.00', '0.00', '0.00', '4.00', '45.00']` -- and so
    is the grand total line, `Grand_Total                45.00`. A rollup that
    invented a parent, or dropped the orphan, would differ between the two.

    Source: Raptor 7 (the mamaHier rollup and its parent guard);
    costs.py:45-58
    """
    top = harness.run(_hierarchy(parent=0), cost_analysis=True)
    orphan = harness.run(_hierarchy(parent=5), cost_analysis=True)
    assert top.draws == 0 and orphan.draws == 0
    assert _row(top.eor, "Hierarchy", "H1") == [
        "H1", "1", "1.00", "40.00", "0.00", "0.00", "4.00", "45.00"]
    assert _row(orphan.eor, "Hierarchy", "H1") == _row(top.eor, "Hierarchy", "H1")
    totals = [[line for line in run.eor if line.startswith("Grand_Total")]
              for run in (top, orphan)]
    assert totals == [["Grand_Total                45.00"]] * 2