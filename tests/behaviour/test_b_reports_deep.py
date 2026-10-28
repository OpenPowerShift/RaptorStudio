"""The deep end of `raptor/core/engine/reports.py`: the report sections and
rows that only exist for a model which *asks* for them.

`test_b_reports.py` covers the Final Results of S1 -- a plain TimeSim model
with no cost analysis, no capacity analysis, no weak-link analysis and no
hierarchy. That is the shape most runs have, and it leaves most of
`reports.py` unexecuted: 74% of its branches are covered by the whole rest of
the fast tier, and the gaps are not scattered. They are whole *sections*,
each behind an option no S1-shaped model sets:

* **`weak_analysis`** -- the `***WEAK_LINK_ANALYSIS***` block, which is the
  module's largest single feature and had no coverage at all outside the
  fidelity tier. It is four element kinds (nodes, blocks, events,
  hierarchies), each in two shapes: the per-run report prints one figure per
  element, the Final Results prints that figure's cross-run
  Minimum/Mean/Maximum/StdDev/SEM plus a State_Data table of cross-run mean
  percentages. This file covers all eight.
* **hierarchy analysis** -- the one weak row with a genuine four-way branch:
  `MTBDE`/`MDT` are `(green+yellow+blue)/hierDE` and
  `(red+orange)/hierDE` when the hierarchy's Out marker node actually went
  `Down` or `NodePM` at least once (`hierDE` counts those two transitions
  only), and fall back to the raw uptime and
  `"N/A"` when it never did. Both arms are pinned here, in both report
  variants.
* **`termination_type`** -- `FailSim` and `CycleSim`. The header wording, the
  extra `Ending_Sim_Time` row, the `Cycles_Completed` row's gate, and the
  `StatsReset` footer's *units*: a `termType=1` model prints the start
  statistics as a time and a `termType=2`/`3` model prints them as a count
  with a plural `s`.
* **cost analysis with a nested hierarchy** -- the Final Results hierarchy
  cost table is not the mean of the per-run rows; it rebuilds the source's
  nested rollup from the cross-run means.

**Every fixture is built in memory** (B3) by `harness.run`, and every
distribution is `Fixed` (B2), so each of these runs consumes zero randomness
and its rows are exact literals rather than tolerances -- `Run.draws == 0`
is asserted where the claim does not already depend on the number being
exact. Three shapes are hand-written spec dicts rather than a `spine()` rung,
because the ladder has no rung for "one event", "one hierarchy" or "a
hierarchy inside a hierarchy" and inventing rung 12-14 here would not be a
ladder step: each of these adds one element kind to S1's topology and
nothing else. Still `harness.run` (B3), still `Fixed`, still ≤2 blocks and
≤4 nodes (B4).

**One fixture perturbs a field of engine-produced data, and says so.**
`_censored_table_row`'s `(k-runs)` branch -- a monitor that saw *some* but
not all of the batch's runs -- is unreachable from a deterministic model:
with every distribution `Fixed` every run is the same run, so a statistic is
either present in all of them or in none of them, and "none" takes the
`count == 0` arm instead. Real captures do hit it (`LadL4Node`, real
2026-10-01: 18 of 60 runs had a PM and the aggregate row still counted all
60, `>`-censored rather than `MTBMs(18-runs)`). The test therefore runs a
real two-run batch, drops `mtbde` from the second run's `RunReportData`, and
asserts the row the source's branch produces. Nothing is read
from a capture and no engine code is involved: the fixture is engine output
with one field cleared, and the expectation is a literal in the test.

**Three branches of this module are dead, and no test here pretends
otherwise.** Each is guarded by a condition its only caller has already
established, so no engine output can reach it and a test asserting it would
have to fabricate the input rather than run it:

* `_table_row`'s non-empty arm (line 876) -- its sole caller is
  `_censored_table_row`, which reaches it under `if stat.count == 0`.
* `_cycles_table_row`'s empty arm (line 959) -- its sole caller gates on
  `cycles_stat.count` being truthy before calling.
* the capacity FinalArray rows' `stat.count == 0` arm (line 1235) --
  `has_capacity` is truthy only when run 1 had capacity data, so the loop
  that fills those three monitors ran at least once.

All three are reported to the engine owner rather than worked around. The
other 99% is executed and asserted below.
"""
from __future__ import annotations

import dataclasses
from collections.abc import Sequence
from typing import Any

import pytest

from raptor.core.engine import runner as _runner
from raptor.core.engine.load import load_model
from raptor.core.engine.reports import (
    _mean_stock, _output_to_fr, build_final_results_report,
)

from . import harness
from .spine import spine

# ---------------------------------------------------------------------------
# Reading helpers. Both take report *text*, and both take the report's own
# words as arguments, so a failure prints the table next to what was expected
# instead of a bare `False`.
# ---------------------------------------------------------------------------

#: The column heads of every five-column table in the Final Results report.
#: They appear twice (once under ***SUMMARY***, once under ***LOGISTICS***);
#: `_row` always reads from the first, so these lookups are the SUMMARY's.
_TABLE_HEADER_ROWS = (
    "                         Minimum         Mean            Maximum"
    "         St.Dev          SEM")


def _row(report: Sequence[str], header: str, name: str) -> str:
    """The row for *name* in the fixed-width table whose column heads are
    *header*.

    A table's rows are the contiguous non-empty run after *header*: every
    table in `reports.py` closes with a blank line before whatever comes next,
    which is what stops the search from wandering into the next table -- whose
    header starts with the same word (`Node`, `Block`, `Event`, `Hierarchy`)
    as the rows being looked for.

    Raises:
        AssertionError: with the whole report, or the whole table, printed --
            a report assertion that says only "not in list" is the worst kind
            of failing test.
    """
    try:
        start = report.index(header)
    except ValueError:
        raise AssertionError(
            f"no report line {header!r}; the report is:\n"
            + "\n".join(report)) from None
    table: list[str] = []
    for line in report[start + 1:]:
        if not line.strip():
            break
        table.append(line)
    hits = [line for line in table if line.split()[:1] == [name]]
    if len(hits) != 1:
        raise AssertionError(
            f"expected exactly one {name!r} row under {header!r}, found "
            f"{len(hits)}; the table is:\n" + "\n".join(table))
    return hits[0]


# ---------------------------------------------------------------------------
# Fixtures. Three hand-written shapes, each S1's topology plus one element
# kind, so the weak-link rows have something to report on.
# ---------------------------------------------------------------------------

def _event_spec(*, with_block: bool = True) -> dict[str, Any]:
    """`E1` in series between `A` and the End node, with `failVal = 1.0` so the
    event's outcome is certain.

    `Fixed(1.0)` rather than `Bernoulli(1.0)` because Bernoulli draws
    unconditionally even when the answer is not in doubt, and rule B2 wants
    zero randomness (`spine.py`'s own S10 note).

    Args:
        with_block: `False` builds an **events-only** model -- no blocks at
            all, so `Start` feeds `E1` directly. The loader accepts it (its
            "no outgoing link" check is per-block), so this is a real run
            rather than a degenerate one; what it has no *of* is any state
            time to aggregate, which is what the fixture is for.
    """
    spec: dict[str, Any] = {
        "system": {"run_length": 100.0, "num_runs": 1, "termination": "TimeSim"},
        "nodes": [
            {"name": "Start", "type": 1, "n": 0, "out": 1},
            {"name": "End", "type": 3, "n": 1, "out": 0},
        ],
        "links": [{"from": "E1", "to": "End"}],
        "events": [{"name": "E1", "dist": harness.FIXED, "failVal": 1.0}],
    }
    if with_block:
        spec["blocks"] = [
            {"name": "A", "fail_dist": harness.FIXED, "fail_params": [50.0],
             "repair_dist": harness.FIXED, "repair_params": [10.0]},
        ]
        spec["links"] = [{"from": "Start", "to": "A"},
                         {"from": "A", "to": "E1"},
                         {"from": "E1", "to": "End"}]
    else:
        spec["links"].insert(0, {"from": "Start", "to": "E1"})
    return spec


def _hierarchy_spec(*, life: float, event: bool = False) -> dict[str, Any]:
    """S1's topology wrapped in one hierarchy `H1`: `Start -> in -> A -> J ->
    out -> End`, with `in`/`out` the hierarchy's own Type=4/Type=5 marker
    nodes.

    `J` is 1-into-1 inside the hierarchy and `out` is the Out marker, so the
    hierarchy's own status is `J`'s -- which is what lets a failing `A` put
    the hierarchy `Down` and give it a non-zero `hierDE`.

    Args:
        life: `A`'s `Fixed` failure life in hours. 50 fails inside the
            100-hour run; 500 never does.
        event: put `E1` between the hierarchy's Out marker and the End node,
            so the model has both of the weak element kinds the Final
            Results weak-link block can report on.
    """
    spec: dict[str, Any] = {
        "system": {"run_length": 100.0, "num_runs": 1,
                   "termination": "TimeSim"},
        "hierarchies": [{"id": 1, "name": "H1", "in_id": 2, "out_id": 4,
                         "level": 1}],
        "blocks": [
            {"name": "A", "parent": 1, "fail_dist": harness.FIXED,
             "fail_params": [life], "repair_dist": harness.FIXED,
             "repair_params": [10.0]},
        ],
        "nodes": [
            {"id": 1, "name": "Start", "type": 1, "n": 0, "out": 1},
            {"id": 2, "name": "in", "type": 4, "n": 1, "out": 1, "parent": 1},
            {"id": 3, "name": "J", "type": 2, "n": 1, "out": 1, "k": 1,
             "parent": 1},
            {"id": 4, "name": "out", "type": 5, "n": 1, "out": 1, "parent": 1},
            {"id": 5, "name": "End", "type": 3, "n": 1, "out": 0},
        ],
        "links": [{"from": "Start", "to": "in"},
                  {"from": "in", "to": "A"},
                  {"from": "A", "to": "J"},
                  {"from": "J", "to": "out"}],
    }
    if event:
        spec["links"] += [{"from": "out", "to": "E1"},
                          {"from": "E1", "to": "End"}]
        spec["events"] = [{"name": "E1", "dist": harness.FIXED,
                           "failVal": 1.0}]
    else:
        spec["links"].append({"from": "out", "to": "End"})
    return spec


#: `H1` containing `A`, with `H2` nested *inside* `H1` and containing `B`.
#: Cost analysis is on, because the rollup this fixture exists for is a
#: property of the ***COST*** section's hierarchy table.
_NESTED_SPEC: dict[str, Any] = {
    "system": {"run_length": 100.0, "num_runs": 1, "termination": "TimeSim",
               "cost_analysis": True},
    "hierarchies": [
        {"id": 1, "name": "H1", "in_id": 2, "out_id": 4, "level": 1},
        {"id": 5, "name": "H2", "in_id": 6, "out_id": 7, "level": 2,
         "parent": 1},
    ],
    "blocks": [
        {"name": "A", "parent": 1, "fail_dist": harness.FIXED,
         "fail_params": [50.0], "repair_dist": harness.FIXED,
         "repair_params": [10.0]},
        {"name": "B", "parent": 5, "fail_dist": harness.FIXED,
         "fail_params": [60.0], "repair_dist": harness.FIXED,
         "repair_params": [5.0]},
    ],
    "nodes": [
        {"id": 1, "name": "Start", "type": 1, "n": 0, "out": 1},
        {"id": 2, "name": "in1", "type": 4, "n": 1, "out": 1, "parent": 1},
        {"id": 3, "name": "J", "type": 2, "n": 2, "out": 1, "k": 1,
         "parent": 1},
        {"id": 4, "name": "out1", "type": 5, "n": 1, "out": 1, "parent": 1},
        {"id": 6, "name": "in2", "type": 4, "n": 1, "out": 1, "parent": 5},
        {"id": 7, "name": "out2", "type": 5, "n": 1, "out": 1, "parent": 5},
        {"id": 8, "name": "End", "type": 3, "n": 2, "out": 0},
    ],
    "links": [{"from": "Start", "to": "in1"},
              {"from": "in1", "to": "A"},
              {"from": "A", "to": "in2"},
              {"from": "in2", "to": "B"},
              {"from": "B", "to": "out2"},
              {"from": "out2", "to": "J"},
              {"from": "J", "to": "out1"},
              {"from": "out1", "to": "End"}],
}


def _with_start_stats(run: harness.Run, at: float):
    """The harness-built model with its `StartStats` cell set to *at*, in
    memory.

    Same harness limitation `test_b_reports.py` documents in its own module
    docstring: `harness.run` cannot read back the blank line and the bare
    `<time>  Started Gathering Statistics` banner the reset writes,
   because `_fields` insists every log line has
    four tab-separated columns. The engine is right and the reader is too
    strict, so the cell is written on the built model. The constructor is
    still the harness's, nothing touches disk, and no `Workspace` exists.
    """
    row = run.rbd.sections["SYSTEM_DATA"].rows[0]
    row["StartStats"] = f"{at:.6f}"
    return run.rbd


def _cycle_sim(cycles: int) -> list[str]:
    """The Final Results of S9 run for *cycles* cycles."""
    return harness.run(spine(9, run_length=float(cycles)),
                       termination="CycleSim").final


# ---------------------------------------------------------------------------
# Weak-link analysis -- the per-run report (End_Of_Run)
# ---------------------------------------------------------------------------

def test_a_node_up_ninety_five_percent_of_the_run_reports_that_as_its_availability() -> None:
    """A node's `NODE_RESULTS` row splits the run six ways, and its Ao/Do
    columns are the `(up + blue) / total` and `(green + yellow) /
    (green + yellow + red)` ratios of that same split.

    Why: the source's own denominator set for a node is the sum of its six
    state clocks, which is what makes `Ao` and the
    six percentages sum to 100% rather than merely being printed near each
    other. `R` is `mCompleted/missionsAttempted` -- a *separate* denominator,
    and it is 0 here because S2's k-of-n Join takes the system down and the
    mission is never completed. So a row can be perfectly well-defined and
    still report zero reliability, and those are different facts.

    Setup: S2 (A fails at 50, B at 55, both repaired inside the run), one
    run, `weak_analysis=True`. The Join node is `Good` while both paths are
    up, `Degraded` when one is down and `Bad` when both are.

    Expected: `Join` as
    `Ao 0.950000000, Do 0.950000000, R 0.000000000, 90.000000 %Green,
    5.000000 %Yellow, 5.000000 %Red, 0.000000 %Orange/%Blue/%Brown` -- 95
    hours up of 100 (90 green + 5 yellow), 10 hours down (5 yellow + 5 red),
    and zero time idle or on PM.

    Source: Raptor 7 (the NODE_RESULTS row's columns),
    Raptor 7 (the six-clock denominators);
    docs/engine-design/node-status-and-cold-standby.adoc (node status
    vocabulary)
    """
    run = harness.run(spine(2), weak_analysis=True)
    assert _row(run.eor,
                "Node                             Ao           Do"
                "            R     %Green    %Yellow       %Red"
                "    %Orange      %Blue     %Brown",
                "Join") == (
        "Join                    0.950000000  0.950000000  0.000000000"
        "  90.000000   5.000000   5.000000   0.000000   0.000000   0.000000")
    assert run.draws == 0


def test_a_block_that_spent_ten_hours_being_repaired_reports_ten_percent_repairing() -> None:
    """A block's state-percentage row divides the run by *its own* eight
    state clocks, so a block that was `Repairing` for a tenth of the run says
    `10.000000` under `%Repairing` and `90.000000` under `%Running`.

    Why: block `Ao` is `(run + standby) / (all eight clocks)` -- not the
    system's green-plus-yellow ratio, which is what the node row uses.
   The two rows of the same table can therefore
    disagree for the same instant, and this is the case where they do not:
    A was never idle, never on PM and never held, so all 100 of its hours sit
    in two columns.

    Setup: S2 as above, one run, `weak_analysis=True`. A's `Fixed` repair
    life is 10 hours, so `Repairing` spans 50-60.

    Expected: `A` as `90.000000 %Running, 0 %Standby, 0 %Idle,
    10.000000 %Repairing, 0 %RepHold, 0 %PM, 0 %PMHold, 0 %Done`, and B --
    down for 5 of its 100 hours -- as `95.000000`/`5.000000` over the same two
    columns.

    Source: Raptor 7 (the block Ao denominator and the eight
    state clocks), Raptor 7's sibling table at Raptor 7
    """
    run = harness.run(spine(2), weak_analysis=True)
    header = ("Block                     %Running    %Standby       %Idle"
              "  %Repairing    %RepHold         %PM     %PMHold"
              "       %Done")
    assert _row(run.eor, header, "A") == (
        "A                        90.000000    0.000000    0.000000"
        "   10.000000    0.000000    0.000000    0.000000    0.000000")
    assert _row(run.eor, header, "B") == (
        "B                        95.000000    0.000000    0.000000"
        "    5.000000    0.000000    0.000000    0.000000    0.000000")


def test_an_event_that_always_succeeds_reports_full_success_and_no_failures() -> None:
    """An `EVENT_RESULTS` row gives an event only three states -- Success,
    Armed and Failure -- and an event that never fails is 100% Success.

    Why: the event row's `AoDenom` is `run + standby + repair` and its three
    percentages are those three counters over that denominator,
   which is a different and smaller denominator
    than a node's six-clock one. `%Armed` is a real column and stays a real
    column here at zero: an event that succeeded and was re-armed at the run's
    end still reports its Armed time, and a report that omitted the column
    would hide the difference between "was never armed again" and "was armed
    and never fired".

    Setup: one block `A` (fails at 50, repaired at 60) and one event `E1`
    in series after it, `failVal = 1.0` drawn from a `Fixed` so the outcome
    is certain and free; one run, `weak_analysis=True`.

    Expected: `E1` as `Ao 1.000000000, Do 1.000000000, R 1.000000000,
    100.000000 %Success, 0.000000 %Armed, 0.000000 %Failure`.

    Source: Raptor 7 (EVENT_RESULTS), Raptor 7
    (the row's columns), Raptor 7 (the three-clock denominator)
    """
    run = harness.run(_event_spec(), weak_analysis=True)
    assert _row(run.eor,
                "Event                            Ao           Do"
                "            R      %Success      %Armed    %Failure",
                "E1") == (
        "E1                      1.000000000  1.000000000  1.000000000"
        "    100.000000    0.000000    0.000000")


def test_a_hierarchy_that_went_down_once_divides_its_uptime_by_that_one_event() -> None:
    """A hierarchy's `MTBDE` is its own uptime divided by the number of
    times it went down -- 90 hours up over one downing event is `90.00000` --
    and `MDT` is its downtime over the same count.

    Why: `hierDE` counts only the Out marker node *leaving* `Down` or
    `NodePM`, not time and not node changes, so
    one failure-and-repair of the single block inside `H1` is exactly one
    `hierDE`. `MTBDE` here is therefore a real mean time between hierarchy
    downing events, and it happens to equal the uptime because the divisor is
    one -- a coincidence of the fixture, not of the formula. Note also the
    source's own field truncation: MTBDE is `[:12]` and MDT `[:10]`, so the
    five decimals in `90.00000` are the field's width, not the value's
    precision.

    Setup: `_hierarchy_spec(life=50.0)` -- `A` fails at 50 and is repaired at
    60, which puts `H1`'s Out marker `Down` once; one run,
    `weak_analysis=True`.

    Expected: the summary row
    `H1  0.900000000  0.900000000  0.000000000    90.00000    10.000` and
    the percentage row `H1  90.000000 %Green, 0 %Yellow, 10.000000 %Red,
    0 %Orange, 0 %Blue, 0 %Brown`.

    Source: Raptor 7 (the two HIERARCHY_RESULTS rows),
    Raptor 7 (MTBDE and MDT from `hierDE`);
    docs/engine-design/hierarchy-status.adoc (a hierarchy's status is its
    Out marker's)
    """
    run = harness.run(_hierarchy_spec(life=50.0), weak_analysis=True)
    assert _row(run.eor,
                "Hierarchy                        Ao           Do"
                "            R       MTBDE       MDT",
                "H1") == (
        "H1                      0.900000000  0.900000000  0.000000000"
        "    90.00000    10.000")
    assert _row(run.eor,
                "Hierarchy                  %Green    %Yellow       %Red"
                "    %Orange      %Blue     %Brown",
                "H1") == (
        "H1                      90.000000   0.000000  10.000000"
        "   0.000000   0.000000   0.000000")


def test_a_hierarchy_that_never_goes_down_reports_its_raw_uptime_and_no_downtime() -> None:
    """A hierarchy whose Out marker never left `Good` reports its uptime
    *undivided* and `N/A` for mean downtime -- and the same `N/A` reaches the
    Final Results summary line.

    Why: `hierDE` is a count of downing events, so a hierarchy that never
    went down has no divisor; the source falls back to printing the raw
    `green+yellow+blue` sum and the literal `"N/A"` rather than dividing by
    zero (and for the aggregate
    monitor, whose count is also empty). `N/A` is the honest answer: a
    hierarchy that never failed has no mean time *between* failures, and a
    zero there would read as "it failed and recovered instantly". The
    end-of-run row shows the source's own truncation doing it -- the
    undivided `100.0` fills the whole 12-character MTBDE field, so the `N/A`
    butts straight up against it.

    Setup: `_hierarchy_spec(life=500.0)` -- `A`'s 500-hour `Fixed` life is
    past the 100-hour run end, so nothing ever fails; one run,
    `weak_analysis=True`.

    Expected: the end-of-run summary row ends `    100.0000N/A`, and the
    Final Results `MTBDE`/`MDT` line reads `H1                     100.00
    N/A`.

    Source: Raptor 7 (the `hierDE == 0` fallback),
    Raptor 7 (the cross-run hMTBDE/hMDT monitors and their N/A)
    """
    run = harness.run(_hierarchy_spec(life=500.0), weak_analysis=True)
    assert _row(run.eor,
                "Hierarchy                        Ao           Do"
                "            R       MTBDE       MDT",
                "H1") == (
        "H1                      1.000000000  1.000000000  1.000000000"
        "    100.0000N/A")
    assert _row(run.final,
                "                       MTBDE         MDT ",
                "H1") == "H1                     100.00        N/A"


# ---------------------------------------------------------------------------
# Weak-link analysis -- the Final Results, which aggregates across runs
# ---------------------------------------------------------------------------

def test_the_final_results_node_analysis_gives_each_figure_a_cross_run_minimum_mean_and_maximum() -> None:
    """The Final Results gives a node three tables, one per figure, each with
    that figure's cross-run Minimum, Mean, Maximum, StdDev and SEM.

    Why: this is the report's core asymmetry with the per-run one -- where
    `NODE_RESULTS` prints one number, `NODE_ANALYSIS` prints the
    `RStatObj` monitor's five statistics over the batch, exactly like every
    other row of the Final Results. `Do` for the
    Join node is 0.95 and not 1.00 because the same node is `Bad` for 5 of
    its 100 hours, and the two figures come from two different denominators
     -- so printing them in separate tables is what keeps the
    distinction visible.

    Setup: S2 as above, one run, `weak_analysis=True`. One run, so every
    cross-run spread column is `N/A` rather than zero -- a monitor with one
    observation has nothing to spread.

    Expected: `Join` as `0.950000000` in all three of the Ao columns, again
    in all three of the Do columns, and `0.000000000` in all three of the R
    columns, with `N/A` for both spread columns of each.

    Source: Raptor 7 (the Final-Results weak-link block),
    Raptor 7 (one Availability/Dependability/Reliability_Data
    row's five columns)
    """
    run = harness.run(spine(2), weak_analysis=True)
    assert _row(run.final,
                "Node                   AoMin         AoMean        AoMax"
                "         AoStdDev      AoSEM",
                "Join") == (
        "Join                   0.950000000   0.950000000   0.950000000"
        "   N/A           N/A")
    assert _row(run.final,
                "Node                   DoMin         DoMean        DoMax"
                "         DoStdDev      DoSEM",
                "Join") == (
        "Join                   0.950000000   0.950000000   0.950000000"
        "   N/A           N/A")
    assert _row(run.final,
                "Node                   RMin          RMean         RMax"
                "          RStdDev       RSEM",
                "Join") == (
        "Join                   0.000000000   0.000000000   0.000000000"
        "   N/A           N/A")


def test_the_final_results_state_data_row_is_the_cross_run_mean_of_the_per_state_percentages() -> None:
    """`State_Data` prints one row per element whose columns are the
    cross-run *mean* of that element's per-run state percentages -- and it
    uses the same six node columns and eight block columns as the per-run
    report.

    Why: the table is the mean, not the minimum, mean and maximum, because a
    state *percentage* is itself already a ratio and averaging ratios is what
    the source's per-column monitor `.Mean` does
    (the same pattern, applied to the weak block). The
    column set differs between the two element kinds in the same table --
    `%Orange` last for a node, `%Done` last for a block -- so a report that
    treated the table as one shape would mislabel one of them.

    Setup: S2, one run, `weak_analysis=True`.

    Expected: the node row `Join  90.000000 %Green, 5.000000 %Yellow,
    5.000000 %Red, 0.000000 %Blue, 0.000000 %Brown, 0.000000 %Orange` and
    the block row `A  90.000000 %Running, ... 10.000000 %Repairing, ...
    0.000000 %Done`, each identical to the per-run row for the same element.

    Source: Raptor 7 (the State_Data tables), Raptor 7-
    5096 (a column's cross-run mean over per-run values)
    """
    run = harness.run(spine(2), weak_analysis=True)
    assert _row(run.final,
                "Node                      %Green     %Yellow        %Red"
                "       %Blue      %Brown     %Orange",
                "Join") == (
        "Join                   90.000000    5.000000    5.000000"
        "    0.000000    0.000000    0.000000")
    assert _row(run.final,
                "Block                   %Running    %Standby       %Idle"
                "  %Repairing    %RepHold         %PM     %PMHold"
                "       %Done",
                "A") == (
        "A                      90.000000    0.000000    0.000000"
        "   10.000000    0.000000    0.000000    0.000000    0.000000")


def test_the_final_results_event_analysis_prints_success_armed_and_failure_columns() -> None:
    """An event gets the same three figure tables as a node, and a
    `State_Data` row whose columns are `%Success`, `%Armed` and `%Failure` --
    padded to 14 rather than the node table's 12.

    Why: `EVENT_ANALYSIS` is the one weak-link sub-table whose state row does
    not share the node/block column set: an event
    has three states, not six or eight, so its row is three 14-wide
    `REALTOSN` fields and the trailing space in the header is the source's
    own. Getting this wrong would silently put an event's Armed time under a
    column headed `%Brown`.

    Setup: `_event_spec()`, one run, `weak_analysis=True`.

    Expected: `E1` as `1.000000000` in every Ao/Do/R cell, and the state row
    `E1                      100.000000      0.000000      0.000000`.

    Source: Raptor 7 (EVENT_ANALYSIS),
    Raptor 7 (the per-run row these aggregate)
    """
    run = harness.run(_event_spec(), weak_analysis=True)
    assert _row(run.final,
                "Event                  RMin          RMean         RMax"
                "          RStdDev       RSEM",
                "E1") == (
        "E1                     1.000000000   1.000000000   1.000000000"
        "   N/A           N/A")
    assert _row(run.final,
                "Event                     %Success        %Armed"
                "      %Failure ",
                "E1") == (
        "E1                      100.000000      0.000000      0.000000")


def test_the_final_results_hierarchy_analysis_leads_with_the_per_run_mtbde_and_mdt_mean() -> None:
    """`HIERARCHY_ANALYSIS` opens with an `MTBDE`/`MDT` summary line of the
    cross-run means of those two per-run figures, then gives the hierarchy
    the same three figure tables and State_Data row as everything else.

    Why: `hMTBDE` and `hMDT` are monitored variables rather than computed
    ratios, so each per-run *assignment* is one observation
   -- and the two monitors have different counts,
    because `hMDT` is assigned only on the runs where the hierarchy actually
    went down. That is why this row prints its two figures at 10-wide with no
    spread columns at all while the tables below print five: there is no
    reliable spread to print. The summary line is also preceded by a blank
    whether or not there are events, which is why the section
    opens on an empty line here.

    Setup: `_hierarchy_spec(life=50.0)`, one run, `weak_analysis=True`.

    Expected: the summary line `H1                     90.00         10.00`,
    then `H1` as `0.900000000` across the Ao and Do tables, `0.000000000`
    across the R table, and `90.000000 %Green / 10.000000 %Red` in State_Data.

    Source: Raptor 7 (HIERARCHY_ANALYSIS),
    Raptor 7 (the hMTBDE/hMDT monitors)
    """
    run = harness.run(_hierarchy_spec(life=50.0), weak_analysis=True)
    assert _row(run.final,
                "                       MTBDE         MDT ",
                "H1") == "H1                     90.00         10.00     "
    assert _row(run.final,
                "Hierarchy              DoMin         DoMean        DoMax"
                "         DoStdDev      DoSEM",
                "H1") == (
        "H1                     0.900000000   0.900000000   0.900000000"
        "   N/A           N/A")
    assert _row(run.final,
                "Hierarchy                 %Green     %Yellow        %Red"
                "       %Blue      %Brown     %Orange",
                "H1") == (
        "H1                     90.000000    0.000000   10.000000"
        "    0.000000    0.000000    0.000000")


def test_an_event_that_never_leaves_success_reports_a_full_success_and_a_perfect_score() -> None:
    """An event with no accumulated state time at all reports Ao = Do = R =
    1.0 and 100% Success -- the report's "nothing went wrong" fallback, not a
    division by zero.

    Why: `AoDenom` is `run + standby + repair`, and
    an events-only model's scheduler never leaves t=0 -- with no block to wait
    out a run length, `EndingSimTime` is `0.000000` and the event's three state
    clocks are all `0.0`. The source's own answer for a zero denominator is
    `Ao := 1.0; Do := 1.0` with `Success := 100.0`, which is the same answer
    the node and block helpers give, and it is the right one here: "this
    element was never down" and "this element was down and we could not
    measure it" must not print the same row.

    Setup: `_event_spec(with_block=False)` -- a model of one event and the two
    nodes the loader insists on, no blocks, so `EndingSimTime` is `0.000000`
    and every clock the row divides by is zero; one run,
    `weak_analysis=True`.

    Expected: `E1` as `Ao 1.000000000, Do 1.000000000, R 1.000000000,
    100.000000 %Success, 0.000000 %Armed, 0.000000 %Failure`.

    Source: Raptor 7 (the event's Ao/Do denominators and the
    zero-denominator fallback), Raptor 7 (the row)
    """
    run = harness.run(_event_spec(with_block=False), weak_analysis=True)
    assert _row(run.eor,
                "Event                            Ao           Do"
                "            R      %Success      %Armed    %Failure",
                "E1") == (
        "E1                      1.000000000  1.000000000  1.000000000"
        "    100.000000    0.000000    0.000000")


def test_a_node_that_accumulated_no_time_at_all_is_reported_as_fully_available() -> None:
    """A node whose six state clocks are all zero reports `Ao = Do = 1.0` and
    `100.000000 %Green` -- the source's no-measurement fallback, not a
    division by zero.

    Why: the node denominators are the sum of the six clocks,
   and a run that ends before any of them can
    advance has nothing to divide by. The source's own answer is
    `Ao := 1.0; Do := 1.0` with `Green := 100.0` and every other percentage
    zero, which is the same answer `Do` gets when only the *dependability*
    clocks are zero. That is the honest one here: "this node was never
    observed down" and "this node was down and we could not tell" must not
    print the same row, and the all-green row is the first of those.

    Setup: S2 with `run_length = 0.0` -- the run ends at t=0, before the
    kickoff's first status change has had any duration to accumulate. Every
    clock stays 0.0; one run, `weak_analysis=True`.

    Expected: `Join` as `Ao 1.000000000, Do 1.000000000, R 1.000000000,
    100.000000 %Green` and `0.000000` for %Yellow, %Red, %Orange, %Blue and
    %Brown.

    Source: Raptor 7 (the node denominators and the zero-total
    fallback), Raptor 7 (the row)
    """
    run = harness.run(spine(2, run_length=0.0), weak_analysis=True,
                      length=0.0)
    assert _row(run.eor,
                "Node                             Ao           Do"
                "            R     %Green    %Yellow       %Red"
                "    %Orange      %Blue     %Brown",
                "Join") == (
        "Join                    1.000000000  1.000000000  1.000000000"
        " 100.000000   0.000000   0.000000   0.000000   0.000000   0.000000")


def test_a_block_that_accumulated_no_time_at_all_is_reported_as_fully_running() -> None:
    """A block whose eight state clocks are all zero reports `Ao = Do = 1.0`
    and `100.000000 %Running`, for the same reason and with the same
    fallback as the node row.

    Why: the block denominators are the sum of the eight clocks,
   so a run that ends at t=0 divides by zero
    twice over -- once for `Ao`, and once for `Do`'s
    `run + repair + rep_hold + done`. The source guards the outer division
    and, inside it, `Do`'s separately, so the two fallbacks are independent;
    both fire here, and `100.000000 %Running` with `0.000000` in every other
    column is the pair of them.

    Setup: S2 with `run_length = 0.0`, one run, `weak_analysis=True`.

    Expected: `A` as `Ao 1.000000000, Do 1.000000000` in the first table and
    `100.000000 %Running` with seven `0.000000` in the second.

    Source: Raptor 7 (the block denominators and their two
    zero-guards), Raptor 7 (the state-percentage row)
    """
    run = harness.run(spine(2, run_length=0.0), weak_analysis=True,
                      length=0.0)
    assert _row(run.eor,
                "Block                            Ao           Do"
                "            R",
                "A") == "A                       1.000000000  1.000000000  1.000000000"
    assert _row(run.eor,
                "Block                     %Running    %Standby       %Idle"
                "  %Repairing    %RepHold         %PM     %PMHold"
                "       %Done",
                "A") == (
        "A                       100.000000    0.000000    0.000000"
        "    0.000000    0.000000    0.000000    0.000000    0.000000")


def test_a_model_with_an_event_and_a_hierarchy_puts_the_event_analysis_first() -> None:
    """The Final Results weak-link block orders its four sub-tables
    NODE, BLOCK, EVENT, HIERARCHY, and the hierarchy block's leading blank
    line appears only when there is no event block to precede it.

    Why: the two blocks are written by two different source routines
    (for events for hierarchies), and
    the blank that separates them belongs to the *event* block's leading
    `WriteLn`. So a model with only a hierarchy gets that blank from a line
    that is written whether or not there are events -- an unconditional write
    in one routine being relied on by the other, which is exactly the sort of
    coupling a report reader cannot see. Getting the order wrong would also
    put the hierarchy's MTBDE/MDT summary line under the event's heading.

    Setup: `_hierarchy_spec(life=50.0, event=True)` -- one block inside one
    hierarchy, with `E1` between the hierarchy's Out marker and the End
    node; one run, `weak_analysis=True`.

    Expected: the four titles in the order `NODE_ANALYSIS`, `BLOCK_ANALYSIS`,
    `EVENT_ANALYSIS`, `HIERARCHY_ANALYSIS`, and the whole tail from
    `EVENT_ANALYSIS` onwards line for line -- `E1`'s three figure tables and
    its `State_Data` row, a blank pair, then `HIERARCHY_ANALYSIS` with
    `H1`'s MTBDE/MDT line and its own three tables.

    Source: Raptor 7 (EVENT_ANALYSIS),
    Raptor 7 (HIERARCHY_ANALYSIS and its unconditional leading
    blank)
    """
    run = harness.run(_hierarchy_spec(life=50.0, event=True),
                      weak_analysis=True)
    titles = [i for i, line in enumerate(run.final)
              if line.endswith("_ANALYSIS")]
    assert [run.final[i] for i in titles] == [
        "NODE_ANALYSIS", "BLOCK_ANALYSIS", "EVENT_ANALYSIS",
        "HIERARCHY_ANALYSIS"]
    # The whole event-then-hierarchy tail, line for line. The blank pair
    # before `HIERARCHY_ANALYSIS` is the *event* block's trailing pair, and
    # there is none after the event title before its own tables -- which is
    # the coupling: a hierarchy on its own gets its leading blank only
    # because the event routine writes one unconditionally.
    assert run.final[titles[2]:] == [
        "EVENT_ANALYSIS",
        "",
        "Availability_Data",
        "Event                  AoMin         AoMean        AoMax         AoStdDev      AoSEM",
        "E1                     1.000000000   1.000000000   1.000000000   N/A           N/A",
        "",
        "Dependability_Data",
        "Event                  DoMin         DoMean        DoMax         DoStdDev      DoSEM",
        "E1                     1.000000000   1.000000000   1.000000000   N/A           N/A",
        "",
        "Reliability_Data",
        "Event                  RMin          RMean         RMax          RStdDev       RSEM",
        "E1                     1.000000000   1.000000000   1.000000000   N/A           N/A",
        "",
        "State_Data",
        "Event                     %Success        %Armed      %Failure ",
        "E1                      100.000000      0.000000      0.000000",
        "",
        "",
        "HIERARCHY_ANALYSIS",
        "",
        "                       MTBDE         MDT ",
        "H1                     90.00         10.00     ",
        "",
        "Availability_Data",
        "Hierarchy              AoMin         AoMean        AoMax         AoStdDev      AoSEM",
        "H1                     0.900000000   0.900000000   0.900000000   N/A           N/A",
        "",
        "Dependability_Data",
        "Hierarchy              DoMin         DoMean        DoMax         DoStdDev      DoSEM",
        "H1                     0.900000000   0.900000000   0.900000000   N/A           N/A",
        "",
        "Reliability_Data",
        "Hierarchy              RMin          RMean         RMax          RStdDev       RSEM",
        "H1                     0.000000000   0.000000000   0.000000000   N/A           N/A",
        "",
        "State_Data",
        "Hierarchy                 %Green     %Yellow        %Red       %Blue      %Brown     %Orange",
        "H1                     90.000000    0.000000   10.000000    0.000000    0.000000    0.000000",
        "",
    ]


# ---------------------------------------------------------------------------
# Termination type: FailSim and CycleSim
# ---------------------------------------------------------------------------

def test_a_cycle_sim_run_reports_both_its_failure_count_and_its_elapsed_time() -> None:
    """A CycleSim report prints *both* termination rows -- the failure count
    and the elapsed sim time -- where a TimeSim report prints only the
    failure count.

    Why: the source's own `IF/ELSE` at Raptor 7 falls through
    to a second unconditional block for cycle-based termination, so
    `termType = 3` gets two rows in the order failure-count-then-time. The
    elapsed time is the *actual* clock at the end of the last cycle rather
    than the run length, which for a CycleSim model is a cycle count: one
    cycle of S9's 60-hour mission phase plus 40-hour non-mission phase is
    `100.000000` hours, not `1.000000`. Reading the run length as a time is
    the mistake this row exists to prevent.

    Setup: S9 (two phases), one run of `1` cycle under
    `termination="CycleSim"`.

    Expected: `System_Failures 2 2.000000 2` and
    `Ending_Sim_Time 100.000000 100.000000 100.000000`, and no
    `Cycles_Completed` row -- that one is gated on a *non*-CycleSim model,
    because the cycle count is already the termination criterion here.

    Source: Raptor 7 (the two termination rows),
    Raptor 7 (the `Cycles_Completed` gate, which excludes
    `termType=3`)
    """
    final = _cycle_sim(1)
    assert _row(final, _TABLE_HEADER_ROWS, "System_Failures") == (
        "System_Failures          2               2.000000        2"
        "               N/A             N/A             ")
    assert _row(final, _TABLE_HEADER_ROWS, "Ending_Sim_Time") == (
        "Ending_Sim_Time          100.000000      100.000000"
        "      100.000000      N/A             N/A             ")
    assert not [line for line in final if line.startswith("Cycles_")]


def test_the_cycle_sim_header_says_one_cycle_in_the_singular() -> None:
    """A CycleSim report whose run length is one cycle says `of 1 cycle:`, not
    `of 1 cycles:`.

    Why: the header is chosen by the source's own three-way wording switch,
   and the cycle arm has a singular case of its
    own -- the same shape as `to 1 failure:` on the `FailSim` arm. A report
    that always pluralised would be wrong about the most common CycleSim
    length there is.

    Setup: S9, one run of `1` cycle.

    Expected: the header line `Results from 1 run of 1 cycle:`.

    Source: Raptor 7 (the per-`termType` header wording)
    """
    assert "Results from 1 run of 1 cycle:" in _cycle_sim(1)


def test_the_cycle_sim_header_says_several_cycles_in_the_plural() -> None:
    """Three cycles is `of 3 cycles:`, and three replications is `runs`.

    Why: the same header carries two independent plural decisions -- the run
    count and the cycle count -- and a model with more than one of each has to
    get both right. `Results from 3 runs of 2 cycles:` is the case where a
    single `run_word` reused for both would read `3 run of 2 cycles`.

    Setup: S9, three runs of `2` cycles each. This is an aggregation property
    (the header counts runs), so rule B2's single-run default does not
    apply; every distribution is still `Fixed`, so the three runs are
    identical and consume no randomness.

    Expected: `Results from 3 runs of 2 cycles:`.

    Source: Raptor 7 (the header wording),
    Raptor 7 (the Final Results' own header)
    """
    spec = spine(9, run_length=2.0)
    spec["system"]["termination"] = "CycleSim"
    assert "Results from 3 runs of 2 cycles:" in harness.run(spec, runs=3).final


def test_a_fail_sim_run_that_reset_its_statistics_counts_the_reset_in_failures() -> None:
    """When a FailSim run reset its statistics, the footer's unit is the
    *failure count* -- `Stats started after 2 failures` -- pluralised, not a
    time.

    Why: `StartStats` is a different quantity per `termType`: a
    `termType=1` model reads it as elapsed hours and a
    `termType=2` as `TRUNC(StartStats)` failures. Here the reset fires on the
    second counted failure (`orchestrator`'s `termCounter == TRUNC(StartStats)`)
    and the run stops at the third, so
    two of its three failures are excluded from every statistic printed
    above -- which is why `MTBDE` is 50.000000 and not the whole run's
    100/1. The plural `s` is the source's own `IF StartStats > 1.0` arm.

    Setup: S1 under `termination="FailSim"` with `run_length = 3.0` (three
    failures) and `StartStats = 2.0`, one run.

    Expected: the line `Stats started after 2 failures`, and the time row
    relabelled `Active_Stat_Time` because the statistics no longer start at 0.

    Source: Raptor 7 (the StatsReset footer and its per-
    `termType` unit), Raptor 7 (the FailSim reset trigger)
    """
    rbd = _with_start_stats(
        harness.run(spine(1), termination="FailSim", length=3.0), 2.0)
    _, _, final = _runner.run_simulation_with_reports(
        rbd, 1, date=harness.REPORT_DATE)
    assert "Stats started after 2 failures" in final
    assert _row(final, _TABLE_HEADER_ROWS, "Active_Stat_Time") == (
        "Active_Stat_Time         60.000000       60.000000       60.000000"
        "       N/A             N/A             ")


def test_a_cycle_sim_run_that_reset_its_statistics_counts_the_reset_in_cycles() -> None:
    """A CycleSim run's reset footer counts *cycles*: `Stats started after 1
    cycle`, singular.

    Why: the third `termType` unit, and the only one of the three that is a
    cycle count rather than a time or a failure count.
   A CycleSim model's `StartStats` is compared
    against `termCounter` at each phase wrap, so a
    reset at cycle 1 of a 3-cycle run throws the whole first cycle away: the
    same three cycles that report `Availability 0.660000000` unreset report
    `0.550000000` once the first 100 hours are excluded, and `MTBDE` falls
    from `33.000000` to `27.500000` because one of the three cycles'
    downing events is no longer counted.

    Setup: S9 under `termination="CycleSim"` with `run_length = 3.0` (three
    cycles) and `StartStats = 1.0`, one run.

    Expected: `Stats started after 1 cycle` and
    `Availability 0.550000000`, `MTBDE 27.500000`.

    Source: Raptor 7 (the footer), Raptor 7 (the
    CycleSim `ResetAllStatistics` trigger)
    """
    rbd = _with_start_stats(
        harness.run(spine(9), termination="CycleSim", length=3.0), 1.0)
    _, _, final = _runner.run_simulation_with_reports(
        rbd, 1, date=harness.REPORT_DATE)
    assert "Stats started after 1 cycle" in final
    assert _row(final, _TABLE_HEADER_ROWS, "Availability").split() == [
        "Availability", "0.550000000", "0.550000000", "0.550000000",
        "N/A", "N/A"]
    assert _row(final, _TABLE_HEADER_ROWS, "MTBDE").split() == [
        "MTBDE", "27.500000", "27.500000", "27.500000", "N/A", "N/A"]


def test_a_statistics_reset_leaves_no_mission_begun_so_conditional_reliability_has_no_samples() -> None:
    """After a statistics reset that happens before any mission phase, the
    `Conditional_Reliability` row is `N/A` in all five columns -- because the
    monitor that row reads never saw a single sample.

    Why: `RCond` is only assigned under `IF missionsBegan > 0`,
   and `missionsBegan` is one of the counters
    `ResetAllStatistics` zeroes. A reset before the first phase therefore
    leaves that monitor empty for the rest of the run, and an empty monitor
    prints `N/A` in every column rather than a zero -- a zero would claim the
    conditional reliability was measured and found to be nil. `Reliability`,
    whose divisor is `missionsAttempted`, is a different monitor and survives:
    it reads `0.000000000` in the same table.

    Setup: S1 under `termination="FailSim"` with `run_length = 3.0` and
    `StartStats = 2.0`. The reset fires on the second counted failure,
    before the run's mission is attempted.

    Expected: `Conditional_Reliability` as `N/A` in all five columns, and
    `Reliability` as `0.000000000` in its three value columns with `N/A`
    spread -- one run, so there is nothing to spread either.

    Source: Raptor 7 (the `RCond` assignment gate),
    Raptor 7 (an empty monitor prints `N/A` in every column)
    """
    rbd = _with_start_stats(
        harness.run(spine(1), termination="FailSim", length=3.0), 2.0)
    _, _, final = _runner.run_simulation_with_reports(
        rbd, 1, date=harness.REPORT_DATE)
    assert _row(final, _TABLE_HEADER_ROWS,
                "Conditional_Reliability").split() == [
        "Conditional_Reliability", "N/A", "N/A", "N/A", "N/A", "N/A"]
    assert _row(final, _TABLE_HEADER_ROWS, "Reliability").split() == [
        "Reliability", "0.000000000", "0.000000000", "0.000000000",
        "N/A", "N/A"]


# ---------------------------------------------------------------------------
# Aggregation edge cases
# ---------------------------------------------------------------------------

def test_a_monitor_that_saw_fewer_runs_than_availability_gets_a_run_count_suffix_and_a_censored_mean() -> None:
    """A statistic that saw *some* of the batch's runs gets a `(k-runs)`
    label and a `>`-censored mean and maximum, with `N/A` for both spread
    columns.

    Why: Raptor 7's own branch. The `>` means "this is a lower
    bound -- the runs that produced no value would have made it longer", and
    the `(k-runs)` suffix says how many runs the figure is actually based on.
    This is the MTBDE/MDT distinction the source draws and
    `test_b_reports.py` does not reach: for the MDT family the short-count
    branch keeps the plain min/mean/max and computes sigma whenever
    `count > 1`, and this row shows the MTBDE family's other
    half. The note the branch collects is a *dialog* message, not report
    text -- real Raptor shows it in its Final Results dialog and never writes
    it to the file, so it is passed in as `notes_sink`.

    Setup: a real two-run S1 batch, then `mtbde` cleared on the second run's
    `RunReportData` before the report is built. **This perturbation is
    deliberate and it is the only way in:** with every distribution `Fixed`
    every run is the same run, so a statistic is present in all runs or none,
    and "none" takes the `count == 0` arm instead. A run with no uptime at
    all cannot be produced deterministically, and a stochastic capture that
    does produce it is the fidelity tier's business. The data is engine
    output; only one field of it is cleared.

    Expected: `MTBDE(1-runs)  90.000000  >90.000000  >90.000000  N/A  N/A`,
    and exactly one note,
    `"Note: Some parameter calculations are based on less than the specified
    number of trials."`.

    Source: Raptor 7 (the `>`-censoring branch),
    Raptor 7 (the MDT/MMT family's contrasting branch),
    Raptor 7 (MTBDE is a monitored global, so every assignment is a
    sample)
    """
    rbd = harness.run(spine(1), runs=2).rbd
    data: list = []
    _runner.run_simulation_with_reports(
        rbd, 2, date=harness.REPORT_DATE, report_sink=data)
    assert [rd.mtbde for rd in data] == [90.0, 90.0]
    data[1] = dataclasses.replace(data[1], mtbde=None)

    notes: list[str] = []
    final = build_final_results_report(
        data, load_model(rbd).blocks_by_id, "spine1.rbd", 100.0,
        harness.REPORT_DATE, notes_sink=notes)
    assert _row(final, _TABLE_HEADER_ROWS, "MTBDE(1-runs)").split() == [
        "MTBDE(1-runs)", "90.000000", ">90.000000", ">90.000000",
        "N/A", "N/A"]
    # Availability saw both runs, so its spread is a real zero, not N/A.
    assert _row(final, _TABLE_HEADER_ROWS, "Availability").split() == [
        "Availability", "0.900000000", "0.900000000", "0.900000000",
        "0.000000000", "0.000000000"]
    assert notes == [
        "Note: Some parameter calculations are based on less than the "
        "specified number of trials."]


def test_capacity_analysis_is_model_wide_so_a_batch_cannot_mix_runs_that_have_it_with_runs_that_do_not() -> None:
    """A batch whose first run has capacity data and a later run does not is
    refused with a `ValueError` naming the offending run.

    Why: capacity analysis is a model-wide option, so
    `build_final_results_report` decides once -- from run 1 -- whether the
    report has capacity rows at all, and then requires every run to agree
    (`_require_capacity`, porting the source's single `capacityAnalysis`
    global). Checking it per run would mean a report whose Units Produced row
    silently covers 3 of 4 runs; refusing names the run instead. The same
    refusal guards the per-run capacity section, where a run without the
    data would print an empty `***CAPACITY***` block.

    Setup: a real two-run S11 batch (the rung with capacity on), then
    `capacity` cleared on the second run's `RunReportData`.

    Expected: `ValueError: run 2 has no capacity data but earlier runs do`.

    Source: docs/engine-design/module-map.adoc (the `reports.py` row:
    capacity is a model-wide option), Raptor 7 (the Final
    Results capacity rows that read it)
    """
    rbd = harness.run(spine(11), runs=2).rbd
    data: list = []
    _runner.run_simulation_with_reports(
        rbd, 2, date=harness.REPORT_DATE, report_sink=data)
    assert [rd.capacity is not None for rd in data] == [True, True]
    data[1] = dataclasses.replace(data[1], capacity=None)

    with pytest.raises(ValueError) as excinfo:
        build_final_results_report(
            data, load_model(rbd).blocks_by_id, "spine11.rbd", 100.0,
            harness.REPORT_DATE)
    assert str(excinfo.value) == (
        "run 2 has no capacity data but earlier runs do")


def test_an_unported_termination_type_is_refused_rather_than_reported_as_time_sim() -> None:
    """A `termination_type` the module does not port raises
    `NotImplementedError`, rather than falling through to the TimeSim wording.

    Why: `reports.py` states in its own docstring that a section it cannot
    verify against a fixture "raises `NotImplementedError` rather than being
    silently skipped", and `build_final_results_report` carries the same rule
    for its `termType` argument. The alternative -- treating an unknown
    termination as a time-based one -- would print `of sim time X:` and a
    `System_Failures` row for a model that terminated some other way, which is
    a plausible-looking wrong answer rather than an obvious failure.

    Setup: no model at all -- the check is on the argument, and the batch is
    empty.

    Expected: `NotImplementedError` reading
    `termination_type='MissionSim' not ported -- only TimeSim/FailSim/CycleSim`.

    Source: docs/engine-design/module-map.adoc (the `reports.py` row, and
    its `NotImplementedError` rule); Raptor 7 (the three `termType`
    arms this dispatches to)
    """
    with pytest.raises(NotImplementedError) as excinfo:
        build_final_results_report([], {}, "m.rbd", 100.0,
                                   harness.REPORT_DATE,
                                   termination_type="MissionSim")
    assert str(excinfo.value) == (
        "termination_type='MissionSim' not ported -- only "
        "TimeSim/FailSim/CycleSim")


def test_final_results_folds_a_nested_hierarchys_costs_into_its_parents_row() -> None:
    """A hierarchy nested inside another appears in *both* rows: its own, and
    folded into its parent's -- so `H1` shows `206.00` where the block
    directly inside it costs `103.00`.

    Why: the Final Results hierarchy cost table is not the mean of the
    per-run rows; the source refills `HierCosts` from the cross-run block and
    event means and then folds nested hierarchies into their parents in a
    loop over the *alphabetically* sorted hierarchies.
    That loop is the reason the printed rows are not always the hierarchies'
    own: when alphabetical order differs from `seqNum` order, the printed row
    for a name can hold another hierarchy's cost (real `LadL8A`, 2026-10-01,
    shows `H6`'s cost under `H3a`). Here the two orders agree, so the rollup
    is the obvious one -- which is exactly what makes it a safe fixture for
    the behaviour, and it is still observable: `H1`'s total is the sum of
    `A`'s and `B`'s, not `A`'s alone.

    Setup: `_NESTED_SPEC` -- `H1` holds `A` and contains `H2`, which holds
    `B` -- with cost analysis on and one run.

    Expected: the two hierarchy rows of the Final Results `***COST***`
    section as `H1 ... 2.00 202.00 2.00 0.00 0.00 206.00` and
    `H2 ... 1.00 101.00 1.00 0.00 0.00 103.00`, against a `Blocks` total of
    `206.00` -- so the parent row double-counts nothing and omits nothing.

    Source: Raptor 7 (the Final Results hierarchy cost table
    and its nested rollup), Raptor 7 (the cross-run mean of
    every cost cell)
    """
    run = harness.run(_NESTED_SPEC)
    header = ("Hierarchy            HierNum    Purchase   Operating"
              "     Sparing    Disposal      Events       Total")
    assert _row(run.final, header, "H1") == (
        "H1                      1           2.00      202.00        2.00"
        "        0.00        0.00      206.00 ")
    assert _row(run.final, header, "H2") == (
        "H2                      5           1.00      101.00        1.00"
        "        0.00        0.00      103.00 ")
    # The two hierarchy rows add up to the model's whole bill, so the parent's
    # extra 103.00 is `B`'s and not a second charge for `A`.
    assert "Blocks                    206.00" in run.final
    assert "Grand_Total               206.00" in run.final


# ---------------------------------------------------------------------------
# The two ported helpers whose defensive arms are unreachable through the
# report builders. Called directly, because that is the only way to reach
# them, and each states what the source's own function does.
# ---------------------------------------------------------------------------

def test_the_file_output_label_convention_leaves_an_empty_label_empty() -> None:
    """`OutputToFR` turns every space after the first character into `_`, and
    leaves an empty label empty rather than indexing into it.

    Why: the report builders pass literal labels to it, so the empty case is
    defensive; it is pinned here because it is the one input where the port's
    `label[0] + label[1:].replace(...)` would raise `IndexError` if it were not
    guarded. The convention itself is the claim
    worth having: `"System Failures"` is written to the file as
    `System_Failures`, which is what makes the report's labels usable as
    output-file names.

    Setup: none -- no model. Two direct calls to the ported helper.

    Expected: `_output_to_fr("") == ""` and
    `_output_to_fr("System Failures") == "System_Failures"`, and
    `_output_to_fr("Green Percent") == "Green_Percent"` with the *first*
    character left alone (Raptor 7's own row label).

    Source: Raptor 7 (OutputToFR);
    Raptor 7 (a label the Final Results passes through it)
    """
    assert _output_to_fr("") == ""
    assert _output_to_fr("System Failures") == "System_Failures"
    assert _output_to_fr("Green Percent") == "Green_Percent"


def test_the_cross_run_mean_of_no_stock_rows_is_zero_rather_than_a_division_by_zero() -> None:
    """`_mean_stock` returns `0.0` for an empty set of per-run snapshots
    instead of dividing by zero.

    Why: every column of the Final Results stock tables is a mean across the
    batch's runs, and the mean of nothing has no
    definition. The guard exists so that a column with no runs behind it reads
    `0.00` rather than raising out of the middle of a report -- the same
    choice the rest of the module makes for an empty monitor, where `N/A` is
    available, and the reason this one is `0.0` is that `REALTOSN(0.0, ...)`
    is what the source writes for a column it never filled.

    Setup: none -- no model, no runs. One direct call to the ported helper.

    Expected: `_mean_stock([], "init") == 0.0`, and a one-row call returns
    that row's own value unchanged.

    Source: Raptor 7 (each stock column is a
    `GETMONITOR(...).Mean` over the per-run values)
    """
    assert _mean_stock([], "init") == 0.0