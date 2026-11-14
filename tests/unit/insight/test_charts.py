"""Chart data functions against hand-computed tables (WP-2.5)."""
from __future__ import annotations

from types import SimpleNamespace

import pytest

from raptor.api.insight import charts


def _element(name, dtci, deci=0.0, causes=None, red=0.0):
    return {"name": name,
            "dtci": {"mean": dtci, "sem": 0.01},
            "deci": {"mean": deci},
            "red_time": {"mean": red},
            "causes": causes or {}}


def test_a_dot_plot_keeps_the_runs_and_counts_the_met_target():
    per_run = [{"availability": 0.90}, {"availability": 0.95},
               {"availability": 1.00}]
    spec = charts.trial_outcome(per_run, target=0.95)
    assert spec.kind == "dot_plot"
    assert spec.provenance == {"runs": 3, "units": "", "measure": "availability",
                               "met": 2, "target": 0.95}
    assert spec.marks == [{"kind": "target", "value": 0.95}]
    assert spec.series[0]["dots"]
    assert "2 of 3" in spec.caption


def test_the_pareto_ranks_by_share_of_downtime():
    attribution = {"runs": 7, "elements": [
        _element("Valve", 0.10), _element("Pump", 0.30, causes={"repair": 0.7})]}
    spec = charts.downtime_pareto(attribution)
    assert [row["name"] for row in spec.series] == ["Pump", "Valve"]
    assert spec.provenance["runs"] == 7
    assert spec.caption.startswith("Pump causes")


def test_the_waterfall_steps_from_one_to_the_operational_value():
    attribution = {"runs": 5, "causes": {"repair": 0.5, "pm": 0.25}}
    spec = charts.downtime_waterfall(attribution, operational=0.90)
    repair, pm = spec.series
    assert repair["value"] == pytest.approx(0.05)
    assert pm["value"] == pytest.approx(0.025)
    assert repair["to"] == pytest.approx(0.95)
    assert pm["to"] == pytest.approx(0.925)
    assert spec.marks[0]["value"] == pytest.approx(0.925)


def test_the_ladder_drops_a_rung_the_report_did_not_compute():
    ladder = {"inherent": {"value": 0.99}, "achieved": {"value": None},
              "operational": {"value": 0.90}}
    spec = charts.availability_ladder(ladder)
    assert [row["key"] for row in spec.series] == ["inherent", "operational"]
    assert "99 %" in spec.caption and "90 %" in spec.caption


def test_the_histogram_reads_the_log_bins_and_quantiles():
    attribution = {"runs": 10, "outage_durations": {
        "count": 4, "p50": 2.0, "p90": 8.0,
        "hist": [{"lo": 1.0, "hi": 2.0, "count": 2.0},
                 {"lo": 2.0, "hi": 4.0, "count": 2.0}]}}
    spec = charts.outage_histogram(attribution)
    assert len(spec.series) == 2
    assert [mark["label"] for mark in spec.marks] == ["P50", "P90"]
    assert spec.provenance["count"] == 4


def test_the_frequency_duration_map_divides_downtime_by_outages():
    attribution = {"runs": 3, "elements": [
        _element("Pump", 0.2, deci=0.4), _element("Idle", 0.0, deci=0.0)]}
    spec = charts.frequency_duration(attribution)
    assert spec.series == [{"name": "Pump", "x": 0.4, "y": 0.5, "area": 0.2}]


def test_the_quadrant_drops_elements_without_both_importance_measures():
    importance = {"metrics": {"Availability": {"rows": [
        {"block": "Pump", "raw": 2.0, "rrw": 1.5},
        {"block": "Valve", "raw": None, "rrw": 3.0}]}}}
    spec = charts.importance_quadrant(importance)
    assert spec.series == [{"name": "Pump", "x": 2.0, "y": 1.5}]


def test_convergence_widens_then_narrows_the_running_interval():
    per_run = [{"availability": 1.0}, {"availability": 0.0},
               {"availability": 1.0}]
    spec = charts.convergence(per_run)
    first, second, third = spec.series
    assert first == {"n": 1, "mean": 1.0, "sem": None}
    assert second["mean"] == pytest.approx(0.5)
    assert second["sem"] == pytest.approx(0.5)
    assert third["mean"] == pytest.approx(2 / 3)


def test_cost_pareto_sums_the_engine_block_rows():
    cost = SimpleNamespace(blocks={
        1: SimpleNamespace(name="Pump", total=60.0),
        2: SimpleNamespace(name="Valve", total=40.0)})
    report_data = [SimpleNamespace(cost=cost), SimpleNamespace(cost=cost)]
    spec = charts.cost_pareto(report_data)
    assert spec.series[0] == {"name": "Pump", "value": 120.0, "share": 0.6}
    assert spec.provenance["grand_total"] == 200.0


def test_an_unknown_chart_name_lists_the_choices():
    with pytest.raises(ValueError, match="trial-outcome"):
        charts.chart_named("nope")


def test_a_chart_that_needs_attribution_says_so():
    with pytest.raises(ValueError, match="attribution"):
        charts.chart_named("downtime-pareto")


def _capacity_attribution():
    return {
        "runs": 4,
        "causes": {"repair": 0.7, "wait_spare": 0.3},
        "capacity": {
            "rule": "proportional", "generated": 100.0,
            "elements": [
                {"name": "Pump", "share": {"mean": 0.6},
                 "lost_energy": {"mean": 12.0},
                 "through_dependents": {"mean": 2.0}},
                {"name": "Valve", "share": {"mean": 0.2},
                 "lost_energy": {"mean": 4.0},
                 "through_dependents": {"mean": 0.0}}],
            "causes": {"repair": {"mean": 7.0}, "wait_spare": {"mean": 3.0}}},
        "elements": [
            {"name": "Pump", "dtci": {"mean": 0.3},
             "deci": {"mean": 0.5}, "red_time": {"mean": 4.0}, "causes": {}},
            {"name": "Valve", "dtci": {"mean": 0.4},
             "deci": {"mean": 0.2}, "red_time": {"mean": 2.0}, "causes": {}}],
    }


def test_the_capacity_pareto_pairs_each_element_with_its_downtime_share():
    spec = charts.lost_capacity_pareto(_capacity_attribution())
    assert [row["name"] for row in spec.series] == ["Pump", "Valve"]
    assert spec.series[0]["downtime"] == pytest.approx(0.3)
    assert spec.provenance["rule"] == "proportional"


def test_the_capacity_scatter_is_the_256_picture():
    spec = charts.capacity_vs_downtime(_capacity_attribution())
    assert {p["name"]: (p["x"], p["y"]) for p in spec.series} == {
        "Pump": (0.3, 0.6), "Valve": (0.4, 0.2)}
    assert spec.marks == [{"kind": "diagonal"}]


def test_the_capacity_waterfall_splits_lost_energy_by_cause():
    spec = charts.capacity_waterfall(_capacity_attribution())
    repair, spare = spec.series
    assert repair["value"] == pytest.approx(0.7)
    assert spare["value"] == pytest.approx(0.3)
    assert spare["to"] == pytest.approx(0.0)


def _periods():
    return {
        "runs": 2, "periods_per_run": 3, "threshold": 0.95,
        "pooled": {"n": 6, "p5": 0.90, "p25": 0.94, "p50": 0.97,
                   "p75": 0.99, "p95": 1.0, "p_below": 0.5},
        "by_index": [
            {"index": 1, "n": 2, "p5": 0.90, "p25": 0.94, "p50": 0.97,
             "p75": 0.99, "p95": 1.0},
            {"index": 2, "n": 2, "p5": 0.88, "p25": 0.92, "p50": 0.96,
             "p75": 0.98, "p95": 1.0}],
    }


def test_the_annual_fan_keeps_the_bands_and_the_threshold():
    spec = charts.annual_fan(_periods())
    assert spec.kind == "fan" and len(spec.series) == 2
    assert spec.marks == [{"kind": "target", "value": 0.95}]
    assert "median moves" in spec.caption


def test_the_annual_dots_count_below_the_threshold():
    spec = charts.annual_dots(_periods())
    assert spec.kind == "dot_plot"
    assert len(spec.series[0]["dots"]) == 5
    assert "50 %" in spec.caption


def test_an_annual_chart_without_periods_says_what_is_missing():
    with pytest.raises(ValueError, match="period-by-period"):
        charts.chart_named("annual-fan")


def test_the_treemap_shows_every_element_and_pools_the_tail():
    attribution = {"runs": 3, "elements": [
        _element("Pump", 0.5), _element("Valve", 0.3), _element("Fan", 0.2)]}
    spec = charts.downtime_treemap(attribution)
    assert spec.kind == "treemap"
    assert [row["name"] for row in spec.series] == ["Pump", "Valve", "Fan"]
    assert sum(row["w"] * row["h"] for row in spec.series) == pytest.approx(
        1000.0 * 600.0, rel=1e-9)


def test_the_outage_anatomy_builds_one_row_per_path_prefix():
    attribution = {"runs": 2, "sequences": [
        {"steps": [{"element": "Pump", "cause": "repair"},
                   {"element": "Valve", "cause": "wait_spare"}],
         "share": 0.6, "outages": 3},
        {"steps": [{"element": "Pump", "cause": "repair"}],
         "share": 0.4, "outages": 2}]}
    spec = charts.outage_anatomy(attribution)
    assert spec.kind == "icicle"
    first = [row for row in spec.series if row["column"] == 0]
    assert len(first) == 1
    assert first[0]["value"] == pytest.approx(1.0)
    assert [row for row in spec.series if row["column"] == 1][0]["label"] == "Valve"
