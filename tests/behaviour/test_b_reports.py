"""Reports: what the Final Results say about a batch of runs, and in what shape.

`raptor/core/engine/reports.py` is the port of the two text builders behind
the Simulation dialog's report tabs -- `DoEndOfRunUpdate`
(one block per run) and `DoEndOfSimUpdate`
(once after all of them). Everything here is about
the *second* one, because that is where the interesting arithmetic lives: it
feeds one sample per run into the same recursive `RStatObj` mechanism the
source uses, and then prints each monitor's minimum, mean, maximum, standard
deviation and SEM in five fixed columns.

Three claims live here, and they are the ones a comparison test cannot make:

* **Exactness.** Every distribution in the spine is `Fixed`, so N runs
  produce N *identical* numbers and the cross-run spread is exactly zero --
  not small, not 1e-16, but `0.000000000`. That is what makes a report
  assertion a literal rather than a tolerance.
* **The sample count is part of the row.** A monitor that saw fewer samples
  than Availability either gets `N/A` for its spread columns or a
  `(k-runs)` label; one run sees nothing to spread, so both spread columns
  are `N/A`.
* **Delayed statistics change what the report is about.** A run whose
  statistics started at hour 25 reports `Failures_After_Reset` and says so in
  a footer, and the reset itself is announced in
  the event log at the start-statistics time.

The last group of tests is about `raptor.api.results`, which is where a
caller -- the MCP `simulate` tool, a JSON consumer -- reads the same numbers
without parsing text. `aggregate_metrics` and `metrics_to_dict` are pure
functions over the engine's `RunReportData` objects, so they are asserted
here on the shapes themselves: one `Metric` per named stat, in a fixed order,
and `None` for every statistic of a stat no run supplied.

**One harness limitation, recorded here because these tests route around it.**
A model with `StartStats > 0` cannot be built through `harness.run(spec,
start_stats=...)`: the statistics reset writes two lines into the event log
that are not `WriteEvents` lines -- a blank line, then a bare
`<time>  Started Gathering Statistics` -- and `harness._fields` raises
`ValueError: cannot read an event-log line` on both, because it insists every
line has four tab-separated columns. The engine is right (it is reproducing
 exactly); the harness's log reader is stricter than
the format it is reading. So the two delayed-statistics tests build the model
through the harness as usual and then write the `StartStats` cell on the
built, in-memory `SYSTEM_DATA` row -- the constructor is still the harness's,
nothing touches disk, and no `Workspace` exists (rule B3).
"""
from __future__ import annotations

from typing import Any

from raptor.api.results import aggregate_metrics, metrics_to_dict
from raptor.core.engine import runner as _runner
from raptor.core.engine.reporting import RunReportData

from . import harness
from .spine import spine

#: The column heads of every five-column table in the Final Results report.
_TABLE_HEADER = ["Minimum", "Mean", "Maximum", "St.Dev", "SEM"]

#: When the delayed-statistics model's statistics start gathering.
START_STATS = 25.0


def _final_row(final: list[str], label: str) -> list[str]:
    """One `Minimum/Mean/Maximum/St.Dev/SEM` row of the Final Results
    report, split into its fields.

    The report is fixed-column text, so `split()` is the honest reading:
    the claim is which number sits in which column, not how many spaces
    separate them.
    """
    for line in final:
        fields = line.split()
        if fields[:1] == [label]:
            return fields
    raise AssertionError(f"no Final Results row labelled {label!r}")


def _report_data(spec: dict[str, Any], runs: int) -> list[RunReportData]:
    """The *runs* runs' `RunReportData`, captured through the runner's own
    `report_sink` hook.

    The harness is still the only constructor (rule B3): the model is built
    by `harness.run`, and the batch is then re-run from the built model with
    `report_sink` set, because `harness.run` exposes the report *text* but
    not the report *objects* these tests need.
    """
    rbd = harness.run(spec).rbd
    data: list[RunReportData] = []
    _runner.run_simulation_with_reports(
        rbd, runs, date=harness.REPORT_DATE, report_sink=data)
    return data


def _delayed_stats_rbd(spec: dict[str, Any]):
    """The harness-built model with its `StartStats` cell set to
    `START_STATS`, in memory.

    See the module docstring: `harness.run` cannot read back the two log
    lines the reset itself writes, so the delayed-statistics column is set on
    the built model rather than in the spec. Nothing is written to disk.
    """
    rbd = harness.run(spec).rbd
    row = rbd.sections["SYSTEM_DATA"].rows[0]
    row["StartStats"] = f"{START_STATS:.6f}"
    return rbd


# ---------------------------------------------------------------------------
# Cross-run aggregation, with no capture anywhere
# ---------------------------------------------------------------------------

def test_identical_runs_aggregate_to_one_value_with_no_spread() -> None:
    """Three `Fixed` runs report the same minimum, mean and maximum, and a
    standard deviation and SEM of exactly zero.

    Why: every distribution in the model is `Fixed` (code 19), which draws
    `param[0]` and touches no stream, so all three runs are the same run.
    The Final Results SUMMARY takes one sample per run into a `RunningStat`
    and prints its five statistics; with three
    identical samples the spread is not merely small but identically zero --
    `pop_to_sample` of a zero-variance population stdev, divided by
    `sqrt(3)`. Note the two column widths: the 9-decimal rows print
    `0.000000000` and the 6-decimal ones `0.000000`.

    Setup: S1, `num_runs = 3`. No seed: nothing is stochastic.

    Expected: the Availability row as
    `['Availability', '0.900000000', '0.900000000', '0.900000000',
    '0.000000000', '0.000000000']`, under the head
    `['Minimum', 'Mean', 'Maximum', 'St.Dev', 'SEM']`, and the MTBDE row as
    `['MTBDE', '90.000000', '90.000000', '90.000000', '0.000000',
    '0.000000']`.

    Source: Raptor 7 (DoEndOfSimUpdate's SUMMARY),
    Raptor 7 (the per-run scalars fed to the cross-run
    monitors)
    """
    run = harness.run(spine(1), runs=3)
    assert _final_row(run.final, "Minimum") == _TABLE_HEADER
    assert _final_row(run.final, "Availability") == [
        "Availability", "0.900000000", "0.900000000", "0.900000000",
        "0.000000000", "0.000000000"]
    assert _final_row(run.final, "MTBDE") == [
        "MTBDE", "90.000000", "90.000000", "90.000000", "0.000000",
        "0.000000"]
    assert run.draws == 0


def test_one_run_has_no_spread_to_report_and_says_so() -> None:
    """A monitor that saw a single sample prints `N/A` for both spread
    columns, not zero.

    Why: the source guards the whole spread block on the sample count --
    `IF (count > 1.0)` before computing sigma, `ELSE` writing `"N/A"` for
    St.Dev and SEM. Zero would be a lie about a
    distribution estimated from one observation, and `N/A` is what the
    column is for. This is the same code path as the three-run case above,
    one count lower.

    Setup: S1, `num_runs = 1`.

    Expected: `['Availability', '0.900000000', '0.900000000', '0.900000000',
    'N/A', 'N/A']`, and `System_Failures` as
    `['System_Failures', '1', '1.000000', '1', 'N/A', 'N/A']` -- minimum
    and maximum are printed as integers, the mean as a real.

    Source: Raptor 7 (the SUMMARY rows' `IF count > 1` guard)
    """
    run = harness.run(spine(1), runs=1)
    assert _final_row(run.final, "Availability") == [
        "Availability", "0.900000000", "0.900000000", "0.900000000",
        "N/A", "N/A"]
    assert _final_row(run.final, "System_Failures") == [
        "System_Failures", "1", "1.000000", "1", "N/A", "N/A"]


# ---------------------------------------------------------------------------
# Delayed statistics
# ---------------------------------------------------------------------------

def test_a_run_whose_statistics_started_late_reports_failures_after_reset() -> None:
    """When any run reset its statistics mid-run, the SUMMARY's failure row
    is relabelled `Failures_After_Reset` and a footer names the start time.

    Why: the label is chosen by the source's own `IF StatsReset`,
   and `StatsReset` is sticky for the whole
    batch -- `runner.run_simulation_with_reports` sets it from
    `any(rd.stats_start_time > 0.0)`, so one reset among N runs relabels all
    of them. The footer at Raptor 7 is the only place the
    report says *when* the statistics started, and for a TimeSim model
    (`termType = 1`) it is printed as a time rather than a count.

    Setup: S1 with `StartStats = 25.0`, one run: the reset fires at t=25 and
    the run's own availability drops from 0.90 to 0.866666667, because the
    25 hours before the reset are no longer counted -- 65 h of up time in
    the 75 h from the reset to the run's end.

    Expected: a `Failures_After_Reset` row where a plain run has
    `System_Failures`, the line `Stats started at time 25.000000` below it,
    and the post-reset availability.

    Source: Raptor 7 (the row label), Raptor 7
    (the StatsReset footer)
    """
    _, _, final = _runner.run_simulation_with_reports(
        _delayed_stats_rbd(spine(1)), 1, date=harness.REPORT_DATE)
    assert _final_row(final, "Failures_After_Reset") == [
        "Failures_After_Reset", "1", "1.000000", "1", "N/A", "N/A"]
    assert "Stats started at time 25.000000" in final
    assert _final_row(final, "Availability")[1] == "0.866666667"


def test_the_statistics_reset_announces_itself_at_the_start_stats_time() -> None:
    """The reset writes a blank line and then
    `<time>  Started Gathering Statistics` -- at the start-statistics time,
    not at the run's end.

    Why: Raptor 7 writes that marker directly with
    `WriteString`, so it has no tab columns and no status field: it is a
    banner, not an event, and the blank line before it is the source's own.
    The time on it is the moment `StatsStartTime` is re-anchored, which is
    what every post-reset duration in the report is measured from -- here
    hour 25, twenty-five hours into a hundred-hour run.

    Setup: S1 with `StartStats = 25.0`, one run.

    Expected: the third line of the event log is exactly
    `'25.000000  Started Gathering Statistics'`, preceded by an empty line
    and followed by nothing until A fails at 50.

    Source: Raptor 7 (the marker), Raptor 7
    (`StatsStartTime := SimTime`)
    """
    logs, _, _ = _runner.run_simulation_with_reports(
        _delayed_stats_rbd(spine(1)), 1, date=harness.REPORT_DATE)
    assert logs[0][1] == ""
    assert logs[0][2] == "25.000000  Started Gathering Statistics"
    assert logs[0][3].split()[:4] == ["50.000000", "Block", "A", "Failed"]


# ---------------------------------------------------------------------------
# The aggregate shapes raptor.api.results hands to a caller
# ---------------------------------------------------------------------------

def test_aggregate_metrics_returns_one_metric_per_named_stat_in_a_fixed_order() -> None:
    """`aggregate_metrics` returns seventeen metrics, named by the report's
    own field names, in that order.

    Why: it is a table lookup over `_METRIC_FIELDS`, not a scan of what a
    particular run happened to produce -- so the list is the same length and
    in the same order whatever the model was, including the stats this run
    could not compute. `MTBDE` comes before `MDT` because that is the order
    Raptor 7 prints the SUMMARY rows in.

    Setup: S1, three runs, so every metric has samples to aggregate.

    Expected: exactly these seventeen names, in this order, and the
    Availability metric's five statistics as `(0.9, 0.9, 0.9, 0.0, 0.0)`.

    Source: Raptor 7 (the SUMMARY row order the names follow);
    docs/engine-design/module-map.adoc (the reporting.py row)
    """
    metrics = aggregate_metrics(_report_data(spine(1), 3))
    assert [metric.name for metric in metrics] == [
        "Availability", "Reliability", "Conditional_Reliability",
        "Dependability", "MTBDE", "MDT", "MTBM", "MTBMu", "MTBMs", "MMT",
        "MMTu", "MMTs", "System_Failures", "Component_Failures",
        "Green_Percent", "Yellow_Percent", "Red_Percent",
    ]
    availability = metrics[0]
    assert (availability.min, availability.mean, availability.max,
            availability.stdev, availability.sem) == (0.9, 0.9, 0.9, 0.0, 0.0)


def test_metrics_to_dict_keys_each_metric_by_name_with_its_five_statistics() -> None:
    """`metrics_to_dict` is the JSON shape: one dict per metric, keyed by
    name, each carrying `name`, `min`, `mean`, `max`, `stdev`, `sem`, and
    the censoring pair `censored`/`censored_at` (a lower bound, rendered
    with Raptor 7's `>` qualifier; `censored_at` is the raw value the
    bound came from, so it is `None` on an uncensored metric).

    Setup: S1, three runs.

    Expected: `Availability` as
    `{'name': 'Availability', 'min': 0.9, 'mean': 0.9, 'max': 0.9,
    'stdev': 0.0, 'sem': 0.0}` and `System_Failures` as
    `{'name': 'System_Failures', 'min': 1, 'mean': 1.0, 'max': 1,
    'stdev': 0.0, 'sem': 0.0}` -- the count statistics keep their integer
    minimum and maximum and take a float mean.

    Source: Raptor 7 (SysFailMon fed `SysFailMon.Count` per
    run, so its minimum and maximum are integers)
    """
    as_dict = metrics_to_dict(aggregate_metrics(_report_data(spine(1), 3)))
    assert as_dict["Availability"] == {
        "name": "Availability", "min": 0.9, "mean": 0.9, "max": 0.9,
        "stdev": 0.0, "sem": 0.0, "censored": False, "censored_at": None}
    assert as_dict["System_Failures"] == {
        "name": "System_Failures", "min": 1, "mean": 1.0, "max": 1,
        "stdev": 0.0, "sem": 0.0, "censored": False, "censored_at": None}


def test_a_stat_no_run_supplied_is_none_for_every_one_of_its_statistics() -> None:
    """A stat that no run could compute is `None` for *all five*, so a
    consumer renders `N/A` rather than printing a zero.

    Why: `MTBMs` (mean time between maintenance suspensions) needs a PM to
    have happened, and S1 has none, so the engine leaves the per-run value
    `None` and the aggregate filters `None`s out. Filtering rather than
    substituting zero is the whole point: a zero there would read as "the
    PM interval was zero", which is a different and wrong statement.

    Setup: S1, three runs.

    Expected: `MTBMs` and `MMTs` are `{'name': ..., 'min': None,
    'mean': None, 'max': None, 'stdev': None, 'sem': None}`, while
    `MTBM` -- which needs no PM -- is fully populated.

    Source: Raptor 7 (the MTBMs family, and the `IF didPM=TRUE`
    guard the report applies); docs/engine-design/module-map.adoc (the
    reporting.py row)
    """
    as_dict = metrics_to_dict(aggregate_metrics(_report_data(spine(1), 3)))
    for name in ("MTBMs", "MMTs"):
        assert as_dict[name] == {
            "name": name, "min": None, "mean": None, "max": None,
            "stdev": None, "sem": None, "censored": False,
            "censored_at": None}
    assert as_dict["MTBM"]["mean"] == 90.0