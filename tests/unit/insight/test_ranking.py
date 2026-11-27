"""How many items a ranking shows, and the table of every item behind it."""
from __future__ import annotations

import pytest

from raptor.api.insight import charts, ranking

N = 25      # past the 10 a Pareto draws, so there is an 11th item to lose


def _attribution(count: int = N) -> dict:
    elements = [
        {"kind": "block", "id": i, "name": f"E{i:02d}",
         "dtci": {"mean": 0.9 / (i + 1), "sem": 0.01},
         "deci": {"mean": 0.5 / (i + 1)},
         "outages": {"mean": 2.0}, "red_time": {"mean": 4.0 / (i + 1)},
         "causes": {"repair": 1.0}, "main_cause": "repair"}
        for i in range(1, count + 1)]
    capacity = {"rule": "proportional", "delivered_energy": {"mean": 80},
                "lost_energy": {"mean": 20},
                "elements": [{"kind": "block", "id": i, "name": f"E{i:02d}",
                              "lost_energy": {"mean": 20 / (i + 1)},
                              "share": {"mean": 0.8 / (i + 1)},
                              "causes": {"repair": 1.0}}
                             for i in range(1, count + 1)]}
    hierarchies = [
        {"kind": "hier", "id": 100 + i, "name": f"H{i:02d}",
         "path": [f"H{i:02d}"], "depth": 1, "parent": 0,
         "members": [{"kind": "block", "id": i}], "member_count": 1,
         "starter_count": 1, "dtci": {"mean": 0.9 / (i + 1), "sem": 0.01},
         "red_time": {"mean": 4.0 / (i + 1)}, "causes": {"repair": 1.0},
         "main_cause": "repair",
         "trials": [{"dtci": 0.9 / (i + 1), "red_time": 4.0 / (i + 1),
                     "system_red_time": 4.0}] * 3}
        for i in range(1, count + 1)]
    return {"runs": 3, "units": "hours", "causes": {"repair": 1.0},
            "elements": elements, "hierarchies": hierarchies,
            "capacity": capacity,
            "outages": {"runs": [{"start": 0, "end": 10, "red": [1, 2],
                                  "hier": {"101": [1, 2], "102": [1, 3]}}]}}


def _per_run() -> list[dict]:
    crit = {str(i): {"name": f"C{i:02d}", "failure": 1.0 / (i + 1)}
            for i in range(1, N + 1)}
    return [{"num_component_failures": 10, "criticality": crit}] * 2


def _costs() -> list[dict]:
    return [{"name": f"K{i:02d}", "total": 100.0 / (i + 1)} for i in range(N)]


def _names(table) -> list[str]:
    return [row["name"] for row in table.rows]


@pytest.mark.parametrize("build, drawn_default", [
    (lambda top: charts.downtime_pareto(_attribution(), top), 10),
    (lambda top: charts.lost_capacity_pareto(_attribution(), top), 10),
    (lambda top: charts.component_failure_pareto(_per_run(), top), 10),
    (lambda top: charts.cost_pareto(_costs(), top), 10),
    (lambda top: charts.hierarchy_pareto(_attribution(), None, top), 12),
    (lambda top: charts.hierarchy_scorecard(_attribution(), top), 8),
    (lambda top: charts.frequency_duration(_attribution(), top), 20),
    (lambda top: charts.capacity_vs_downtime(_attribution(), top), 20),
])
def test_a_ranking_draws_top_items_and_lists_all_of_them(build, drawn_default):
    assert len(build(drawn_default).series) == drawn_default
    five = build(5)
    assert len(five.series) == 5
    everything = build(None)       # None is every item
    assert len(everything.series) == N
    # the table behind a short chart lists the same items as the full one
    short, full = ranking.ranked_table(five), ranking.ranked_table(everything)
    assert len(short.rows) == len(full.rows) == N
    assert _names(short) == _names(full)
    assert [row["rank"] for row in short.rows] == list(range(1, N + 1))


def test_the_table_has_value_share_and_a_cumulative_share_that_adds_up():
    table = ranking.ranked_table(charts.cost_pareto(_costs(), 5))
    assert [c.key for c in table.columns][:4] == ["rank", "name", "value", "share"]
    assert table.rows[0]["name"] == "K00"
    total = sum(row["value"] for row in table.rows)
    assert table.rows[0]["share"] == pytest.approx(table.rows[0]["value"] / total)
    cumulative = [row["cumulative"] for row in table.rows]
    assert cumulative == sorted(cumulative)
    assert cumulative[-1] == pytest.approx(1.0)


def test_a_percent_chart_does_not_repeat_its_value_as_a_share():
    table = ranking.ranked_table(charts.downtime_pareto(_attribution(), 5))
    keys = [c.key for c in table.columns]
    assert "share" not in keys and "cumulative" in keys
    assert table.rows[0]["cumulative"] == pytest.approx(table.rows[0]["value"])


def test_a_folded_chart_still_lists_the_items_in_its_others_cell():
    spec = charts.fold_small(charts.cost_pareto(_costs(), None), top=5)
    assert len(spec.series) == 6 and spec.series[-1]["name"] == f"{N - 5} others"
    table = ranking.ranked_table(spec)
    assert len(table.rows) == N
    assert not any("others" in name for name in _names(table))


def test_the_comparison_charts_take_more_than_four_and_list_every_hierarchy():
    attribution = _attribution()
    small = charts.hierarchy_small_multiples(attribution, None, 8)
    assert len(small.facets) == 8
    hops = charts.hierarchy_hops(attribution, None, 12)
    assert len(hops.series) == 12
    overlay = charts.hierarchy_overlay(attribution, None, 6)
    assert len([layer for layer in overlay.series
                if layer.get("name") != "system"]) == 6
    for spec in (small, hops, overlay):
        table = ranking.ranked_table(spec)
        assert len(table.rows) == N
        assert table.columns[-1].key == "shown"
    shown = ranking.ranked_table(small)
    assert sum(1 for row in shown.rows if row["shown"]) == 8


def test_picked_hierarchies_are_capped_per_chart_not_at_four():
    attribution = _attribution()
    refs = [100 + i for i in range(1, 9)]
    assert len(charts.hierarchy_small_multiples(attribution, refs).facets) == 8
    with pytest.raises(ValueError, match="at most six"):
        charts.hierarchy_overlay(attribution, refs)
    with pytest.raises(ValueError, match="at most twelve"):
        charts.hierarchy_small_multiples(
            attribution, [100 + i for i in range(1, 14)])


def test_the_delta_table_lists_every_hierarchy_by_size_of_change():
    attribution = _attribution()
    baseline = [dict(row, red_time={"mean": row["red_time"]["mean"] * 2})
                for row in attribution["hierarchies"]]
    spec = charts.hierarchy_delta(attribution, baseline, top=5)
    assert len(spec.series) == 5
    table = ranking.ranked_table(spec)
    assert len(table.rows) == N
    sizes = [abs(row["value"]) for row in table.rows]
    assert sizes == sorted(sizes, reverse=True)


def test_charts_that_are_not_rankings_have_no_table():
    assert ranking.ranked_table(charts.chart_named(
        "trial-outcome", per_run=[{"availability": 0.9, "total_time": 10.0}] * 3)) is None
    assert not ranking.is_ranked(None)


def test_the_csv_header_says_a_share_is_a_fraction():
    table = ranking.ranked_table(charts.downtime_pareto(_attribution(), 5))
    assert any("fraction: 1 = 100 %" in title for title in table.header())
    assert len(table.grid()) == N and len(table.grid()[0]) == len(table.columns)


def test_every_show_rule_offers_its_default_and_stays_within_its_cap():
    for key, rule in ranking.SHOW_RULES.items():
        options = rule.options()
        assert rule.default in options, key
        assert key in charts.CHARTS, key
    for key in ("hierarchy-overlay", "hierarchy-small-multiples", "hierarchy-hops"):
        most = max(c for c in ranking.SHOW_RULES[key].options())
        assert most == ranking.COMPARE_LIMITS[key]
