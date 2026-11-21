"""The interim cost report -> cumulative cost charts."""
from __future__ import annotations

from pathlib import Path

import pytest

from raptor.api.insight import charts, cost_series
from raptor.render.charts_svg import render

ROOT = Path(__file__).resolve().parents[3]
CAPACITY = ROOT / "tests" / "golden" / "Capacity1" / "legacy" / (
    "rpt_interim_cost_capacity1.txt")
FACTORY = ROOT / "tests" / "golden" / "ReleaseProbe27" / "legacy" / (
    "rpt_interim_cost_ReleaseProbe27.txt")


def test_the_interim_report_parses_every_run_and_category():
    runs = cost_series.parse_cost_file(str(CAPACITY))
    assert len(runs) == 10
    assert runs[0]["t"] == [720.0]
    assert runs[0]["resources"][-1] == pytest.approx(19_047_285.98)
    assert runs[0]["system_down"][-1] == pytest.approx(565_529.81)
    assert runs[0]["lost_flow"][-1] == pytest.approx(1_368_633.59)
    # The categories add to the report's Grand_Total for each run.
    for run in runs:
        values = cost_series.cumulative(run)
        assert sum(values[key][-1] for key in cost_series.CATEGORIES) == pytest.approx(
            run["grand"][-1], rel=1e-9)


def test_cost_categories_separate_capacity_related_costs():
    runs = cost_series.parse_cost_file(str(FACTORY))
    spec = charts.cost_categories(runs)
    assert spec.kind == "pareto" and spec.provenance["format"] == "money"
    labels = [row["name"] for row in spec.series]
    assert "Lost flow (capacity)" in labels
    assert "System downtime" in labels
    assert "Repair and repair hold" in labels
    assert 0.0 <= spec.provenance["capacity_related"] <= 1.0
    assert "Capacity-related costs" in spec.caption
    assert render(spec).startswith("<svg")


def test_cumulative_and_annual_cost_charts_render():
    runs = cost_series.parse_cost_file(str(FACTORY))
    cumulative = charts.cost_cumulative(runs)
    assert cumulative.kind == "timeline"
    assert {layer["name"] for layer in cumulative.series} >= {
        "Repair and repair hold", "Spares", "Crews and equipment",
        "Planned maintenance", "Lost flow (capacity)"}
    assert render(cumulative).startswith("<svg")
    annual = charts.cost_annual(runs, year=720.0)
    assert annual.kind == "fan"
    assert annual.series and annual.series[0]["n"] == 10
    assert render(annual).startswith("<svg")
    assert "median operating cost" in annual.caption


def test_cost_charts_name_the_missing_cost_plot():
    empty = charts.cost_categories([])
    assert "Cost plot" in empty.caption
    assert charts.cost_cumulative(None).series == []
    assert charts.cost_annual(None).series == []


FACTORY_EOR = ROOT / "tests" / "golden" / "ReleaseProbe27" / "legacy" / (
    "rpt_end_of_run_ReleaseProbe27.txt")


def test_the_end_of_run_report_gives_each_runs_closing_cost_totals():
    # Raptor 7's own End of Run report: ten runs, each ending between two
    # interim snapshots (the last interim one is at 2,160 h)
    finals = cost_series.parse_final_file(str(FACTORY_EOR))
    assert len(finals) == 10
    assert finals[0]["t"] == pytest.approx(2400.499665)
    assert finals[0]["grand"] == pytest.approx(39_222_063.22)
    for final in finals:
        parts = cost_series.cumulative({key: [value] for key, value in final.items()})
        assert sum(parts[key][-1] for key in cost_series.CATEGORIES) == pytest.approx(
            final["grand"], rel=1e-6)


def test_closing_the_runs_counts_the_last_part_interval_of_cost():
    runs = cost_series.parse_cost_file(str(FACTORY))
    finals = cost_series.parse_final_file(str(FACTORY_EOR))
    closed = cost_series.close_runs(runs, finals)
    assert closed[0]["t"][-1] == pytest.approx(2400.499665)
    assert runs[0]["t"][-1] == 2160.0          # the input is not changed
    totals = cost_series.category_totals(closed)
    mean_grand = sum(final["grand"] for final in finals) / len(finals)
    assert sum(totals.values()) == pytest.approx(mean_grand, rel=1e-6)
    # a different number of runs cannot be paired: nothing is guessed
    assert cost_series.close_runs(runs, finals[:3]) is runs
    assert cost_series.close_runs(runs, None) is runs


def test_the_last_year_is_not_understated_when_the_run_ends_between_samples():
    # cost accrues 100 a year; snapshots every half year stop at 1.5 years
    year = 8760.0
    snaps = [0.5 * year, 1.0 * year, 1.5 * year]
    keys = cost_series._snapshot(0.0).keys()
    run = {key: [0.0] * len(snaps) for key in keys}
    run["t"] = list(snaps)
    run["op"] = [50.0, 100.0, 150.0]
    final = {key: 0.0 for key in keys}
    final.update({"t": 2.0 * year, "op": 200.0})
    closed = cost_series.close_runs([run], [final])
    rows = cost_series.annual_rows(closed, year=year)
    assert [row["p50"] for row in rows] == pytest.approx([100.0, 100.0])
    # without the closing totals the unfinished year is left out, not
    # drawn as a year with half its cost
    assert len(cost_series.annual_rows([run], year=year)) == 1


def test_load_runs_parses_a_stored_report_once(tmp_path):
    cost = tmp_path / "cost.txt"
    cost.write_text(FACTORY.read_text())
    eor = tmp_path / "end_of_run.txt"
    eor.write_text(FACTORY_EOR.read_text())
    first = cost_series.load_runs(cost, eor)
    assert cost_series.load_runs(cost, eor) is first
    assert first[0]["t"][-1] == pytest.approx(2400.499665)
    # a missing end-of-run report leaves the interim runs as they are
    assert cost_series.load_runs(cost, tmp_path / "missing.txt")[0]["t"][-1] == 2160.0
