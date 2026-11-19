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
                               "format": "percent", "met": 2, "target": 0.95}
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
    assert [mark["label"] for mark in spec.marks] == ["median", "1 in 10 longer"]
    assert spec.provenance["count"] == 4


def test_the_frequency_duration_map_divides_downtime_by_outages():
    attribution = {"runs": 3, "elements": [
        _element("Pump", 0.2, deci=0.4), _element("Idle", 0.0, deci=0.0)]}
    spec = charts.frequency_duration(attribution)
    assert spec.series == [{"name": "Pump", "x": 0.4, "y": 0.5, "area": 0.2}]
    assert spec.y_label == "relative outage length"


def test_the_frequency_duration_map_reads_in_counts_and_time_when_it_can():
    pump = {**_element("Pump", 0.2, deci=0.4), "outages": {"mean": 4.0},
            "red_time": {"mean": 10.0}}
    spec = charts.frequency_duration({"runs": 3, "units": "hours", "elements": [pump]})
    assert spec.series == [{"name": "Pump", "x": 4.0, "y": 2.5, "area": 0.2}]
    assert spec.y_label == "mean outage length (hours)"
    assert spec.caption.startswith("Low right, frequent short outages: improve reliability")
    # U12: a count of outages, not a share: 4 a run is not "400 %"
    assert spec.provenance["x_format"] == ""
    assert charts.bubble_tip(spec, spec.series[0]) == (
        "Pump: 4 outages a run, 2.5 hours each on average; 20 % of downtime")
    from raptor.render.charts_svg import render
    assert " %" not in render(spec).split("Frequency against duration")[1].split("share of downtime")[0]


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


def test_cost_pareto_is_the_mean_per_run():
    # Pump costs 60 in each of two runs: 60 a run, not 120 (the Final
    # Results figure), and its share of 100 a run is 60 %.
    cost = SimpleNamespace(blocks={
        1: SimpleNamespace(name="Pump", total=60.0),
        2: SimpleNamespace(name="Valve", total=40.0)})
    report_data = [SimpleNamespace(cost=cost), SimpleNamespace(cost=cost)]
    spec = charts.cost_pareto(charts.element_costs(report_data))
    assert spec.series[0] == {"name": "Pump", "value": 60.0, "share": 0.6}
    assert spec.provenance["grand_total"] == 100.0


def test_element_costs_from_the_stored_tables_match_the_run_reports():
    from raptor.api.tables import Table
    life = Table("results.cost_life_cycle_cost", "Life-cycle cost", "results",
                 ["Block", "ID", "Purchase", "Operating", "Sparing", "Disposal", "Total"],
                 [["Pump", 1, "100.00", "0.00", "300.00", "0.00", "400.00"],
                  ["Total", "", "100.00", "0.00", "300.00", "0.00", "400.00"]])
    pools = Table("results.cost_sparepools", "Spare pools", "results",
                  ["SparePools", "Initial", "NewSpares", "EmerShip", "Total"],
                  [["Seals", "50.00", "50.00", "0.00", "100.00"]])
    rows = {r["name"]: r for r in charts.element_costs_from_tables([life, pools])}
    assert rows["Pump"]["total"] == pytest.approx(400.0)
    assert rows["Pump"]["sparing"] == pytest.approx(300.0)
    assert rows["Seals"] == {"name": "Seals", "spare_pool": 100.0, "total": 100.0}
    assert "Total" not in rows


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
    periods = _periods()
    periods["by_index"] = periods["by_index"] * 2
    spec = charts.annual_fan(periods)
    assert spec.kind == "fan" and len(spec.series) == 4
    assert spec.marks == [{"kind": "target", "value": 0.95}]
    assert "median moves" in spec.caption and "band" in spec.subtitle


def test_two_periods_are_boxes_not_a_trend():
    """G9: a line through two medians read as a trend; two or three periods
    are drawn and worded as separate boxes."""
    spec = charts.annual_fan(_periods())
    assert "too few to show a trend" in spec.caption
    assert "median 97 %, 96 %" in spec.caption
    assert "box" in spec.subtitle and "moves" not in spec.caption


def test_the_annual_dots_count_below_the_threshold():
    spec = charts.annual_dots(_periods())
    assert spec.kind == "dot_plot"
    assert len(spec.series[0]["dots"]) == 5
    assert "50 %" in spec.caption
    assert "quantiles" in spec.subtitle


def test_the_annual_dots_are_equal_shares_when_the_run_has_them():
    periods = _periods()
    periods["pooled"]["dots"] = [0.9, 0.93, 0.95, 0.97, 0.99, 1.0]
    spec = charts.annual_dots(periods)
    assert [d["value"] for d in spec.series[0]["dots"]] == periods["pooled"]["dots"]
    assert spec.subtitle == "each dot is one run-year"


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


def test_the_ensemble_ribbon_sorts_runs_and_keeps_red_intervals():
    attribution = {"runs": 3, "outages": {"runs": [
        {"start": 0, "end": 100, "red": [10, 20, 50, 60]},
        {"start": 0, "end": 100, "red": []},
        {"start": 0, "end": 100, "red": [5, 15]}]}}
    spec = charts.ensemble_ribbon(attribution)
    assert spec.kind == "ribbon"
    assert [row["downtime"] for row in spec.series] == [0, 10, 20]
    assert spec.series[1]["red"] == [[5, 15]]
    assert "best" in spec.caption and "worst" in spec.caption


def test_the_exceedance_curve_counts_runs_at_least_each_value():
    # By hand: of four runs, 1 reached 1.0, 3 reached at least 0.95 and all
    # 4 reached at least 0.90.
    per_run = [{"availability": v} for v in (0.95, 0.90, 1.00, 0.95)]
    spec = charts.ecdf(per_run, target=0.95)
    assert spec.kind == "exceedance"
    assert spec.series[0]["points"] == [
        {"value": 1.00, "share": 0.25}, {"value": 0.95, "share": 0.75},
        {"value": 0.90, "share": 1.0}]
    assert spec.provenance["format"] == "percent"
    assert "75 % of runs reached the target 95 %" in spec.caption
    assert "half reached at least 95 %" in spec.caption


def test_a_lower_is_better_measure_reads_at_most():
    per_run = [{"downtime": v} for v in (5.0, 1.0, 3.0)]
    spec = charts.ecdf(per_run, "downtime", label="Downtime",
                       higher_is_better=False)
    assert [p["value"] for p in spec.series[0]["points"]] == [1.0, 3.0, 5.0]
    assert spec.provenance["reached"] == "at most"
    assert "reached at most" in spec.caption


def test_a_time_unit_does_not_stop_a_fraction_printing_as_a_percentage():
    assert charts.value_format("availability", "hours") == "percent"
    assert charts.value_format("cost", "money") == "money"
    assert charts.value_format("downtime", "hours") == ""


def test_cost_treemap_by_hand():
    # Two runs.  Pump: purchase 100 + sparing 300 per run -> 400 a run,
    # mostly spares.  Pool "Seals": 200 in run 1 only -> 100 a run.
    from types import SimpleNamespace as NS
    from raptor.api.insight.charts import cost_treemap

    def report(pool):
        block = NS(name="Pump", purchase=100.0, operating=0.0, sparing=300.0,
                   disposal=0.0)
        return NS(cost=NS(blocks={1: block},
                          spare_pools={"Seals": NS(name="Seals", total=pool)}))

    from raptor.api.insight.charts import element_costs
    spec = cost_treemap(element_costs([report(200.0), report(0.0)]))
    cells = {c["name"]: c for c in spec.series}
    assert cells["Pump"]["value"] == pytest.approx(400.0)
    assert cells["Pump"]["cause"] == "sparing"
    assert cells["Pump"]["share"] == pytest.approx(0.8)
    assert cells["Seals"]["value"] == pytest.approx(100.0)
    assert cells["Seals"]["cause"] == "spare_pool"
    assert spec.provenance["format"] == "money"
    assert spec.provenance["group_labels"]["sparing"] == "Spares"
    # areas are proportional to the values
    area = {n: c["w"] * c["h"] for n, c in cells.items()}
    assert area["Pump"] / area["Seals"] == pytest.approx(4.0)
    assert "Pump occupies the largest area (400, 80 % of the cost)" in spec.caption


def test_lost_capacity_treemap_uses_the_capacity_shares():
    from raptor.api.insight.charts import lost_capacity_treemap
    attribution = {
        "runs": 3,
        "elements": [{"name": "A", "main_cause": "repair"},
                     {"name": "B", "main_cause": "wait_spare"}],
        "capacity": {"elements": [{"name": "A", "share": {"mean": 0.25}},
                                  {"name": "B", "share": {"mean": 0.75}}]},
    }
    spec = lost_capacity_treemap(attribution)
    assert [c["name"] for c in spec.series] == ["B", "A"]
    assert spec.series[0]["cause"] == "wait_spare"
    assert spec.caption == "B occupies the largest area (75 % of the lost flow)."


def test_phase_downtime_keeps_phase_order_and_names_the_weakest():
    attribution = {"runs": 2, "phases": [
        {"name": "Launch", "share_of_downtime": 0.25, "availability": 0.5,
         "drivers": [{"name": "Pump", "share": 1.0}]},
        {"name": "Cruise", "share_of_downtime": 0.75, "availability": 0.9,
         "drivers": [{"name": "Valve", "share": 0.6}]}]}
    spec = charts.phase_downtime(attribution)
    assert [r["name"] for r in spec.series] == ["Launch", "Cruise"]
    assert spec.caption == ("Launch is the weakest phase: availability 50 %, "
                            "25 % of all downtime, mostly started by Pump.")
    from raptor.render.charts_svg import render
    assert "availability 50 %" in render(spec)


# -- folding the small items (presentation setting) ------------------------

def _many(n: int = 8) -> dict:
    shares = [0.6, 0.3] + [0.1 / (n - 2)] * (n - 2)
    return {"runs": 2, "causes": {"repair": 1.0},
            "elements": [{"name": f"E{i}", "dtci": {"mean": s},
                          "deci": {"mean": 0.1}, "red_time": {"mean": 1.0},
                          "causes": {"repair": 0.5, "wait_spare": 0.5},
                          "main_cause": "repair"}
                         for i, s in enumerate(shares)]}


def test_fold_small_gathers_a_ranking_tail_and_names_it():
    from raptor.api.insight import charts
    spec = charts.fold_small(charts.downtime_pareto(_many()), 0.05)
    assert [r["name"] for r in spec.series][:2] == ["E0", "E1"]
    folded = spec.series[-1]
    assert folded["name"] == "6 others below 5 %"
    assert folded["value"] == pytest.approx(0.1)
    assert folded["folded"] == [f"E{i}" for i in range(2, 8)]
    assert folded["causes"] == pytest.approx({"repair": 0.5, "wait_spare": 0.5})
    assert "E7" in spec.caption
    assert spec.provenance["folded"] == 6


def test_fold_small_relays_a_treemap_and_keeps_the_total():
    from raptor.api.insight import charts
    base = charts.downtime_treemap(_many())
    spec = charts.fold_small(base, 0.05)
    assert len(spec.series) == 3
    area = sum(r["w"] * r["h"] for r in spec.series)
    assert area == pytest.approx(sum(r["w"] * r["h"] for r in base.series))
    assert sum(r["value"] for r in spec.series) == pytest.approx(1.0)


def test_fold_small_leaves_other_charts_and_single_items_alone():
    from raptor.api.insight import charts
    phases = charts.ChartSpec("pareto", "Downtime by phase", x_label="phase",
                              series=[{"name": "a", "value": 0.001},
                                      {"name": "b", "value": 0.001}],
                              provenance={"format": "percent"})
    assert charts.fold_small(phases, 0.05) is phases
    one_small = charts.downtime_pareto(_many(3))
    assert charts.fold_small(one_small, 0.05) is one_small
    assert charts.fold_small(charts.downtime_pareto(_many()), 0.0).series[-1][
        "name"] == "E7"


def test_the_capacity_scatter_keeps_an_element_with_no_system_downtime():
    # in a 2-of-3 a pump loses flow without ever taking the system down:
    # exactly the point the chart is for, at x = 0
    attribution = {"runs": 2,
                   "elements": [{"name": "Valve", "dtci": {"mean": 1.0}}],
                   "capacity": {"elements": [
                       {"name": "Pump", "share": {"mean": 0.7}},
                       {"name": "Valve", "share": {"mean": 0.3}},
                       {"name": "(no single element)", "share": {"mean": 0.0}}]}}
    spec = charts.capacity_vs_downtime(attribution)
    assert {p["name"]: (p["x"], p["y"]) for p in spec.series} == {
        "Pump": (0.0, 0.7), "Valve": (1.0, 0.3)}


def test_lost_capacity_treemap_prefers_the_capacity_cause():
    from raptor.api.insight.charts import lost_capacity_treemap
    attribution = {"runs": 1, "elements": [],
                   "capacity": {"elements": [
                       {"name": "Pump", "share": {"mean": 1.0}, "main_cause": "pm"}]}}
    assert lost_capacity_treemap(attribution).series[0]["cause"] == "pm"


def test_quartile_lines_on_run_to_run_and_chance_of_reaching():
    """U16: both charts show where a quarter, half and three quarters of
    the runs fall; equal quartiles share one line."""
    from raptor.api.insight.charts import ecdf, quartile_marks, trial_outcome
    from raptor.render.charts_svg import render

    per_run = [{"availability": v} for v in (0.90, 0.92, 0.94, 0.96, 0.98)]
    dots = trial_outcome(per_run)
    assert [(m["label"], round(m["value"], 3)) for m in dots.marks
            if m["kind"] == "quartile"] == [("Q1", 0.92), ("median", 0.94), ("Q3", 0.96)]
    assert "between 92 % and 96 %" in dots.caption
    curve = ecdf(per_run)
    assert [(m["label"], m["share"]) for m in curve.marks if m["kind"] == "quartile"] == [
        ("1 in 4", 0.25), ("half", 0.5), ("3 in 4", 0.75)]
    assert "median 94 %" in render(dots) and "half: 94 %" in render(curve)
    same = quartile_marks([1.0] * 8)
    assert [m["label"] for m in same] == ["Q1, median, Q3"]
    assert quartile_marks([0.9, 1.0]) == []      # too few runs for quartiles
