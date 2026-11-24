"""The hierarchy ChartSpecs: data, captions, SVG and CSV."""
from __future__ import annotations

import itertools

import pytest

from raptor.api.insight import charts
from raptor.render.charts_svg import render


def _attribution():
    return {
        "runs": 3,
        "units": "hours",
        "causes": {"repair": 0.6, "pm": 0.4},
        "elements": [
            {"kind": "block", "id": 1, "name": "Pump",
             "dtci": {"mean": 0.5, "sem": 0.05},
             "red_time": {"mean": 5.0}, "causes": {"repair": 1.0},
             "main_cause": "repair"},
            {"kind": "block", "id": 2, "name": "Filter",
             "dtci": {"mean": 0.3, "sem": 0.03},
             "red_time": {"mean": 3.0}, "causes": {"pm": 1.0},
             "main_cause": "pm"},
        ],
        "hierarchies": [
            {"kind": "hier", "id": 10, "name": "Intake", "path": ["Intake"],
             "depth": 1, "parent": 0,
             "members": [{"kind": "block", "id": 1}],
             "member_count": 1, "starter_count": 1, "elements": 1,
             "dtci": {"mean": 0.5, "sem": 0.05}, "red_time": {"mean": 5.0},
             "causes": {"repair": 1.0}, "main_cause": "repair",
             "trials": [{"red_time": 5.0, "dtci": 0.5},
                        {"red_time": 4.0, "dtci": 0.4},
                        {"red_time": 6.0, "dtci": 0.6}]},
            {"kind": "hier", "id": 20, "name": "Filtration", "path": ["Filtration"],
             "depth": 1, "parent": 0,
             "members": [{"kind": "block", "id": 2}],
             "member_count": 1, "starter_count": 0, "elements": 0,
             "dtci": {"mean": 0.0, "sem": None}, "red_time": {"mean": 0.0},
             "causes": {}, "main_cause": None,
             "trials": [{"red_time": 0.0, "dtci": 0.0}] * 3},
        ],
        "sequences": [
            {"steps": [{"element": "Pump", "cause": "repair",
                        "hierarchy": "Intake", "hierarchy_id": 10}],
             "share": 0.8, "outages": 3},
            {"steps": [{"element": "Filter", "cause": "pm",
                        "hierarchy": "Filtration", "hierarchy_id": 20}],
             "share": 0.2, "outages": 1},
        ],
        "outages": {"runs": [
            {"start": 0, "end": 10, "red": [2, 4], "hier": {"10": [2, 4]}},
            {"start": 0, "end": 10, "red": [], "hier": {}},
            {"start": 0, "end": 10, "red": [1, 3], "hier": {"10": [1, 3]}},
        ]},
        "capacity": {
            "rule": "proportional", "generated": 10,
            "delivered_energy": {"mean": 80},
            "lost_energy": {"mean": 20},
            "elements": [
                {"kind": "block", "id": 1, "name": "Pump",
                 "lost_energy": {"mean": 12}, "share": {"mean": 0.6},
                 "causes": {"repair": 1.0}, "main_cause": "repair"},
                {"kind": "block", "id": 2, "name": "Filter",
                 "lost_energy": {"mean": 8}, "share": {"mean": 0.4},
                 "causes": {"pm": 1.0}, "main_cause": "pm"},
            ],
        },
    }


@pytest.mark.parametrize("name, kind", [
    ("hierarchy-scorecard", "scorecard"),
    ("hierarchy-pareto", "pareto"),
    ("hierarchy-treemap", "treemap"),
    ("hierarchy-overlay", "timeline"),
    ("hierarchy-small-multiples", "small_multiples"),
    ("hierarchy-hops", "hops"),
    ("hierarchy-heatmap", "heatmap"),
    ("hierarchy-anatomy", "icicle"),
    ("hierarchy-ribbon", "ribbon"),
    ("hierarchy-sunburst", "sunburst"),
    ("capacity-sankey", "sankey"),
])
def test_hierarchy_chart_builds_and_renders(name, kind):
    spec = charts.chart_named(name, attribution=_attribution())
    assert spec.kind == kind
    assert spec.caption
    svg = render(spec)
    assert svg.startswith("<svg")
    assert spec.to_dict()["scope"] is not None


def test_hierarchy_delta_needs_a_baseline_and_marks_changes():
    current = _attribution()
    baseline = _attribution()
    baseline["hierarchies"][0]["dtci"] = {"mean": 0.4, "sem": 0.05}
    baseline["hierarchies"][0]["red_time"] = {"mean": 4.0, "sem": 0.5}
    current["hierarchies"][0]["red_time"] = {"mean": 5.0, "sem": 0.5}
    spec = charts.hierarchy_delta(current, baseline["hierarchies"])
    assert spec.kind == "delta"
    assert spec.series[0]["name"] == "Intake"
    assert spec.series[0]["value"] == pytest.approx(1.0)
    assert "1 h a run more" in spec.caption
    assert "±" in spec.caption
    assert spec.baseline is not None
    with pytest.raises(ValueError, match="baseline"):
        charts.hierarchy_delta(current, None)


def test_scope_normalises_the_hierarchy_pareto_but_keeps_system_shares():
    spec = charts.hierarchy_pareto(_attribution(), 10)
    assert spec.x_label == "element"
    assert sum(row["value"] for row in spec.series) == pytest.approx(1.0)
    assert "scope: Intake" in spec.subtitle
    system = charts.hierarchy_pareto(_attribution())
    assert system.x_label == "hierarchy"
    assert system.provenance["row_kind"] == "hierarchy"


def _nested():
    return {
        "runs": 5,
        "phases": [{"name": "Cruise", "mission": True},
                   {"name": "Launch", "mission": True}],
        "elements": [
            {"kind": "block", "id": 1, "name": "P1",
             "dtci": {"mean": 0.3}, "causes": {"repair": 1.0},
             "main_cause": "repair",
             "phase_shares": {"Cruise": 0.25, "Launch": 0.75},
             "phase_red": {"Cruise": 1.0, "Launch": 3.0}},
            {"kind": "block", "id": 2, "name": "P2",
             "dtci": {"mean": 0.2}, "causes": {"pm": 1.0},
             "main_cause": "pm"},
        ],
        "hierarchies": [
            {"id": 10, "name": "Power", "parent": 0, "path": ["Power"],
             "depth": 1, "members": [{"kind": "block", "id": 1},
                                     {"kind": "block", "id": 2}],
             "direct_members": [{"kind": "block", "id": 2}],
             "member_count": 2, "starter_count": 2,
             "dtci": {"mean": 0.5}, "causes": {"repair": 0.6, "pm": 0.4},
             "main_cause": "repair",
             "phase_shares": {"Cruise": 0.75, "Launch": 0.25},
             "phase_red": {"Cruise": 3.0, "Launch": 1.0}},
            {"id": 11, "name": "Pumps", "parent": 10,
             "path": ["Power", "Pumps"], "depth": 2,
             "members": [{"kind": "block", "id": 1}],
             "direct_members": [{"kind": "block", "id": 1}],
             "member_count": 1, "starter_count": 1,
             "dtci": {"mean": 0.3}, "causes": {"repair": 1.0},
             "main_cause": "repair",
             "phase_shares": {"Cruise": 0.25, "Launch": 0.75},
             "phase_red": {"Cruise": 1.0, "Launch": 3.0}},
        ],
    }


def test_nested_treemap_keeps_the_child_hierarchy():
    spec = charts.hierarchy_treemap(_nested())
    names = [row["name"] for row in spec.series]
    assert "Power" in names and "Pumps" in names and "P1" in names
    p1 = next(row for row in spec.series if row["name"] == "P1")
    pumps = next(row for row in spec.series if row["name"] == "Pumps")
    assert p1["x"] >= pumps["x"] and p1["y"] >= pumps["y"]
    assert p1["w"] <= pumps["w"] and p1["h"] <= pumps["h"]


def test_nested_sunburst_has_a_ring_per_level():
    spec = charts.hierarchy_sunburst(_nested())
    depths = {int(row["depth"]) for row in spec.series}
    assert depths == {0, 1, 2}
    assert any(row["name"].endswith("Pumps") for row in spec.series)
    assert charts.hierarchy_sunburst(_nested(), depth=2).series
    assert max(int(row["depth"]) for row in
               charts.hierarchy_sunburst(_nested(), depth=2).series) == 1


def test_scope_offers_child_hierarchies_as_well_as_elements():
    spec = charts.hierarchy_pareto(_nested(), 10)
    assert [row["name"] for row in spec.series] == ["Pumps", "P2"]
    assert sum(row["value"] for row in spec.series) == pytest.approx(1.0)


def test_overlapping_hierarchies_are_refused_for_an_overlay():
    attribution = _nested()
    attribution["outages"] = {"runs": [
        {"start": 0, "end": 10, "red": [1, 3], "hier": {"10": [1, 3]}},
    ]}
    with pytest.raises(ValueError, match="overlap"):
        charts.hierarchy_overlay(attribution, [10, 11])


def test_a_hierarchy_chart_without_a_roll_up_says_how_to_get_one():
    with pytest.raises(ValueError, match="needs the hierarchy roll-up"):
        charts.chart_named("hierarchy-treemap", attribution={})
    with pytest.raises(ValueError, match="needs the hierarchy roll-up"):
        charts.chart_named("hierarchy-sunburst", attribution={"runs": 2})


def test_chart_named_rejects_ambiguous_hierarchy_arguments():
    attribution = _nested()
    with pytest.raises(ValueError, match="not both"):
        charts.chart_named("hierarchy-pareto", attribution=attribution,
                           hierarchy=10, hierarchies=[11])
    with pytest.raises(ValueError, match="does not take several"):
        charts.chart_named("hierarchy-pareto", attribution=attribution,
                           hierarchies=[10])
    with pytest.raises(ValueError, match="does not take one"):
        charts.chart_named("hierarchy-scorecard", attribution=attribution,
                           hierarchy=10)
    with pytest.raises(ValueError, match="at least 1"):
        charts.chart_named("hierarchy-treemap", attribution=attribution,
                           depth=0)
    with pytest.raises(ValueError, match="does not exist"):
        charts.chart_named("hierarchy-treemap", attribution=attribution,
                           depth=9)
    with pytest.raises(ValueError, match="no hierarchy depth"):
        charts.chart_named("downtime-pareto", attribution=attribution,
                           depth=1)


def test_small_multiples_refuses_overlapping_branches():
    with pytest.raises(ValueError, match="overlap"):
        charts.hierarchy_small_multiples(_nested(), ["Power", "Pumps"])


def test_chart_named_rejects_a_depth_off_the_tree():
    attribution = _nested()
    with pytest.raises(ValueError, match="at least 1"):
        charts.chart_named("hierarchy-sunburst", attribution=attribution, depth=0)
    with pytest.raises(ValueError, match="does not exist"):
        charts.chart_named("hierarchy-sunburst", attribution=attribution, depth=9)


def test_chart_named_explains_missing_hierarchy_attribution():
    with pytest.raises(ValueError, match="hierarchy roll-up"):
        charts.chart_named("hierarchy-treemap", attribution={"runs": 2})
    with pytest.raises(ValueError, match="attribution=True"):
        charts.chart_named("hierarchy-sunburst", attribution={})


def test_small_multiples_refuses_a_parent_with_its_child():
    with pytest.raises(ValueError, match="overlap"):
        charts.chart_named("hierarchy-small-multiples",
                           attribution=_nested(), hierarchies=[10, 11])


def test_capacity_sankey_balances_at_every_node():
    spec = charts.capacity_sankey(_attribution())
    links = spec.series
    nodes = {node["id"]: float(node["value"]) for node in
             spec.provenance["nodes"]}
    assert nodes["generated"] == pytest.approx(
        nodes["delivered"] + nodes["lost"])
    for node_id, value in nodes.items():
        if node_id == "delivered" or node_id.startswith("cause-"):
            continue
        out = sum(float(link["value"]) for link in links
                  if link["source"] == node_id)
        assert out == pytest.approx(value, rel=1e-9), node_id


def test_capacity_sankey_labels_each_cause_with_its_share():
    spec = charts.capacity_sankey(_attribution(), by_hierarchy=False,
                                  show_delivered=False)
    svg = render(spec)
    assert "60 %" in svg and "40 %" in svg
    assert spec.title == "Lost capacity by cause"
    assert spec.provenance["by_hierarchy"] is False


def test_capacity_sankey_adds_an_unattributed_remainder_for_a_scope():
    attribution = _attribution()
    spec = charts.capacity_sankey(attribution, "Intake")
    nodes = {node["id"]: node for node in spec.provenance["nodes"]}
    assert "unattributed" in nodes
    assert nodes["unattributed"]["value"] == pytest.approx(8.0)


def test_sunburst_with_no_downtime_is_an_explanation_not_an_error():
    attribution = _nested()
    for row in attribution["hierarchies"]:
        row["dtci"] = {"mean": 0.0}
    spec = charts.hierarchy_sunburst(attribution)
    assert spec.series == []
    assert "No hierarchy has downtime" in spec.caption


def test_unassigned_downtime_is_a_root_slice_not_inflated_shares():
    attribution = _nested()
    attribution["system"] = {"red_time": {"mean": 1.0}}
    attribution["hierarchies"][0]["dtci"] = {"mean": 0.5}
    attribution["hierarchies"][1]["dtci"] = {"mean": 0.2}
    spec = charts.hierarchy_sunburst(attribution)
    roots = {row["name"]: row["value"] for row in spec.series
             if not row.get("parent")}
    assert roots["Not in a hierarchy"] == pytest.approx(0.5)
    assert roots["Power"] == pytest.approx(0.5)
    tree = charts.hierarchy_treemap(attribution)
    assert any(row["name"] == "Not in a hierarchy" for row in tree.series)


def test_small_multiples_defaults_to_four_at_a_busy_level():
    attribution = {"runs": 2, "hierarchies": [
        {"id": i, "name": f"H{i}", "path": [f"H{i}"], "parent": 0, "depth": 1,
         "dtci": {"mean": 0.1}, "causes": {}, "trials": [
             {"dtci": 0.1, "red_time": 1.0}]}
        for i in range(1, 7)
    ]}
    spec = charts.hierarchy_small_multiples(attribution)
    assert len(spec.facets) == 4
    assert [facet.title for facet in spec.facets] == ["H1", "H2", "H3", "H4"]


def test_hierarchy_phase_heatmap_rows_add_to_one_and_explains_the_peak():
    spec = charts.hierarchy_phase_heatmap(_nested())
    assert [row["name"] for row in spec.series] == ["Power"]
    assert set(spec.series[0]["cells"]) == {"Cruise", "Launch"}
    assert sum(spec.series[0]["cells"].values()) == pytest.approx(1.0)
    assert spec.caption == ("Power holds the most hierarchy downtime; most "
                            "of it is in Cruise (75 % of its downtime).")
    assert [item["label"] for item in spec.provenance["columns"]] == [
        "Cruise", "Launch"]
    child = charts.hierarchy_phase_heatmap(_nested(), depth=2)
    assert [row["name"] for row in child.series] == ["Pumps"]
    assert "Launch" in child.caption


def test_hierarchy_phase_heatmap_scopes_and_needs_phases():
    spec = charts.hierarchy_phase_heatmap(_nested(), "Pumps")
    assert spec.title == "Phases inside Pumps"
    assert [row["name"] for row in spec.series] == ["Pumps"]
    assert spec.scope["name"] == "Pumps"
    attribution = _nested()
    attribution.pop("phases")
    with pytest.raises(ValueError, match="needs a phased model"):
        charts.hierarchy_phase_heatmap(attribution)


def test_ranking_captions_disclose_downtime_outside_every_hierarchy():
    attribution = _nested()
    attribution["system"] = {"red_time": {"mean": 1.0}}
    attribution["hierarchies"][0]["dtci"] = {"mean": 0.4}
    attribution["hierarchies"][1]["dtci"] = {"mean": 0.0}
    for spec in (charts.hierarchy_scorecard(attribution),
                 charts.hierarchy_pareto(attribution),
                 charts.hierarchy_heatmap(attribution)):
        assert "60 % of system downtime is not inside a hierarchy" in spec.caption


def test_small_multiples_share_one_axis_and_pluralise_the_caption():
    attribution = _attribution()
    attribution["hierarchies"].append(
        {**attribution["hierarchies"][0], "id": 20, "name": "Cooling",
         "path": ["Cooling"], "dtci": {"mean": 0.2}})
    spec = charts.hierarchy_small_multiples(attribution)
    ranges = {tuple(facet.provenance.get("x_range") or ()) for facet in spec.facets}
    assert len(ranges) == 1
    one = charts.hierarchy_small_multiples(attribution, ["Cooling"])
    assert one.scope["name"] == "1 hierarchy"
    assert one.caption.startswith("1 hierarchy;")


def test_scoped_areas_and_anatomy_use_the_scope_denominator():
    from raptor.api.insight import hierarchy as hier
    attribution = _attribution()
    attribution["sequences"] = [
        {"steps": [{"element": "Pump", "hierarchy_id": 10,
                    "hierarchy_path": "Intake"}],
         "share": 0.25, "outages": 1},
        {"steps": [{"element": "Other", "hierarchy_id": 20,
                    "hierarchy_path": "Filtration"}],
         "share": 0.75, "outages": 1}]
    source = hier.scoped(attribution, attribution["hierarchies"][0])
    assert sum(seq["share"] for seq in source["sequences"]) == pytest.approx(1.0)
    tree = charts.downtime_treemap(source)
    assert sum(row["value"] for row in tree.series) == pytest.approx(1.0)
    assert "Intake downtime" in tree.subtitle


def test_six_hierarchies_say_they_show_the_top_four():
    attribution = {"runs": 2, "hierarchies": [
        {"id": i, "name": f"H{i}", "path": [f"H{i}"], "parent": 0, "depth": 1,
         "dtci": {"mean": 0.1}, "causes": {}, "trials": [
             {"dtci": 0.1, "red_time": 1.0, "system_red_time": 1.0}]}
        for i in range(1, 7)],
        "outages": {"runs": [{"start": 0, "end": 10, "red": [1, 2],
                              "hier": {"1": [1, 2]}}]}}
    small = charts.hierarchy_small_multiples(attribution)
    assert len(small.facets) == 4
    assert "top 4 of 6" in small.caption
    overlay = charts.hierarchy_overlay(attribution)
    assert "top 4 of 6" in overlay.caption


def test_the_cause_only_sankey_drops_float_noise_and_labels_its_causes():
    from raptor.api.insight import hierarchy as hier
    total = 98_397_807.0
    attribution = {"capacity": {
        "lost_energy": {"mean": total}, "delivered": {"mean": 1e9},
        "causes": {"repair": {"mean": total * 0.7},
                   "logistics": {"mean": total * 0.3 - 3e-8}},
        "elements": []}}
    nodes, links = hier.sankey(attribution, by_hierarchy=False, show_delivered=False)
    names = {node["name"] for node in nodes}
    assert "Not attributed" not in names
    assert {"Hands-on repair", "Logistics delay"} <= names
    assert all(link["cause"] in ("repair", "logistics") for link in links)


def test_scorecard_tiles_carry_run_dots_on_one_shared_scale():
    spec = charts.hierarchy_scorecard(_attribution())
    by_name = {row["name"]: row for row in spec.series}
    assert [d["value"] for d in by_name["Intake"]["dots"]] == [0.4, 0.5, 0.6]
    assert spec.provenance["dot_max"] == pytest.approx(0.6)
    svg = render(spec)
    assert "<circle" in svg and "each dot a run" in svg and "60 %" in svg
    assert "members" in svg and "started outages" in svg and "inside" not in svg


def test_scorecard_without_trials_has_no_strip_and_no_error():
    attribution = _attribution()
    for row in attribution["hierarchies"]:
        row["trials"] = []
    spec = charts.hierarchy_scorecard(attribution)
    assert all(row["dots"] == [] for row in spec.series)
    assert "<circle" not in render(spec)


def test_scorecard_badge_names_direction_by_glyph_and_word():
    attribution = _attribution()
    baseline = [dict(row) for row in attribution["hierarchies"]
                if row["id"] == 10]
    baseline[0]["dtci"] = {"mean": 0.8, "sem": 0.05}
    spec = charts.hierarchy_scorecard(attribution, baseline=baseline)
    badges = {row["name"]: charts.scorecard_badge(row) for row in spec.series}
    assert badges["Intake"] == ("good", "▼ 30 pts better")
    assert badges["Filtration"][1].startswith("new")
    assert "▼ 30 pts better" in render(spec)
    worse = dict(baseline[0], dtci={"mean": 0.3, "sem": 0.05})
    row = charts.hierarchy_scorecard(attribution, baseline=[worse]).series[0]
    assert charts.scorecard_badge(row) == ("bad", "▲ 20 pts worse")
    assert charts.scorecard_badge(
        charts.hierarchy_scorecard(attribution).series[0]) is None


def test_spread_labels_never_overlap_and_stay_inside():
    from raptor.api.insight.timecharts import spread_labels
    placed = spread_labels([50.0, 51.0, 52.0, 99.0], 12.0, 10.0, 100.0)
    ordered = sorted(placed)
    assert all(b - a >= 12.0 - 1e-9 for a, b in itertools.pairwise(ordered))
    assert min(placed) >= 10.0 and max(placed) <= 100.0
    assert spread_labels([], 12.0, 0.0, 10.0) == []


def test_overlay_labels_each_line_at_its_end_and_drops_the_key():
    spec = charts.hierarchy_overlay(_attribution())
    assert spec.provenance["end_labels"]
    assert all(layer["key"] is False and layer["end_text"] for layer in spec.series)
    svg = render(spec)
    for layer in spec.series:
        assert layer["end_text"] in svg
