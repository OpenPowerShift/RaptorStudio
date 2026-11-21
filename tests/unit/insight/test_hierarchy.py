"""Hierarchy roll-up, scope and cumulative data used by the new charts."""
from __future__ import annotations

import pytest

from raptor.api.insight import hierarchy as hier
from raptor.api.insight.attribution import (
    Initiated, TrialAttribution, _hierarchy_rows, model_hierarchies,
)


def _trial(red: float = 10.0) -> TrialAttribution:
    trial = TrialAttribution(span=20.0, red_time=red)
    trial.initiated[("block", 1)] = Initiated(outages=1, red_time=red)
    trial.initiator_cause[("block", 1)] = {"repair": red}
    trial.names[("block", 1)] = "Pump"
    trial.names[("block", 2)] = "Filter"
    return trial


def _tree():
    return {
        "names": {10: "Intake", 11: "Filtration"},
        "parent": {10: 0, 11: 0},
        "of": {("block", 1): 10, ("block", 2): 11},
        "paths": {10: ["Intake"], 11: ["Filtration"]},
        "depth": {10: 1, 11: 1},
        "members": {10: {("block", 1)}, 11: {("block", 2)}},
        "children": {10: [], 11: []},
        "direct": {10: {("block", 1)}, 11: {("block", 2)}},
    }


def test_zero_downtime_hierarchy_is_kept_and_members_are_separate():
    rows = _hierarchy_rows([_trial()], _tree())
    by_name = {row["name"]: row for row in rows}
    assert set(by_name) == {"Intake", "Filtration"}
    assert by_name["Filtration"]["dtci"]["mean"] == 0.0
    assert by_name["Filtration"]["member_count"] == 1
    assert by_name["Filtration"]["starter_count"] == 0
    assert by_name["Intake"]["starter_count"] == 1
    assert by_name["Intake"]["trials"][0]["dtci"] == pytest.approx(1.0)


def test_resolve_accepts_id_name_and_path():
    attribution = {"hierarchies": [
        {"id": 10, "name": "Intake", "path": ["Intake"], "parent": 0},
        {"id": 11, "name": "Filtration", "path": ["Plant", "Filtration"],
         "parent": 0},
    ]}
    assert hier.resolve(attribution, 10)["name"] == "Intake"
    assert hier.resolve(attribution, "intake")["id"] == 10
    assert hier.resolve(attribution, "Plant > Filtration")["id"] == 11
    with pytest.raises(ValueError, match="unknown hierarchy"):
        hier.resolve(attribution, "missing")


def test_scope_filters_elements_and_keeps_system_shares():
    attribution = {
        "runs": 2,
        "hierarchies": [{"id": 10, "name": "Intake", "path": ["Intake"],
                         "parent": 0,
                         "members": [{"kind": "block", "id": 1}],
                         "member_count": 1, "starter_count": 1}],
        "elements": [
            {"kind": "block", "id": 1, "name": "Pump",
             "dtci": {"mean": 0.4}, "red_time": {"mean": 4.0},
             "causes": {"repair": 1.0}},
            {"kind": "block", "id": 2, "name": "Filter",
             "dtci": {"mean": 0.6}, "red_time": {"mean": 6.0},
             "causes": {"pm": 1.0}},
        ],
        "sequences": [],
    }
    scoped = hier.scoped(attribution, hier.resolve(attribution, 10))
    assert [row["name"] for row in scoped["elements"]] == ["Pump"]
    assert scoped["elements"][0]["dtci"]["mean"] == 0.4
    assert scoped["causes"] == {"repair": 1.0}
    assert scoped["scope"]["name"] == "Intake"


def test_cumulative_layers_and_ribbon_use_hierarchy_intervals():
    attribution = {
        "hierarchies": [{"id": 10, "name": "Intake", "path": ["Intake"],
                         "parent": 0, "dtci": {"mean": 0.5}}],
        "outages": {"runs": [
            {"start": 0, "end": 10, "red": [2, 4], "hier": {"10": [2, 4]}},
            {"start": 0, "end": 10, "red": [], "hier": {}},
        ]},
    }
    layers, end = hier.cumulative_layers(attribution,
                                         hier.resolve_many(attribution, [10]))
    assert end == 10
    assert layers[0]["name"] == "Intake"
    assert layers[0]["y"][-1] == pytest.approx(1.0)
    rows = hier.ribbon_rows(attribution,
                            hier.resolve_many(attribution, [10]), run=1)
    assert rows[0]["name"] == "Intake"
    assert rows[0]["red"] == [[2.0, 4.0]]


def test_model_hierarchies_members_are_rollups():
    from raptor.core.fileio.rbd import load_rbd
    from pathlib import Path
    path = Path(__file__).resolve().parents[2] / "golden" / "HierarchyExample" / "input.rbd"
    tree = model_hierarchies(load_rbd(str(path)))
    assert tree is not None
    assert tree["names"]
    for hid, members in tree["members"].items():
        assert members
        assert tree["paths"][hid][-1] == tree["names"][hid]


def test_resolve_accepts_numeric_strings_slashes_and_reports_what_exists():
    attribution = {"hierarchies": [
        {"id": 10, "name": "Intake", "path": ["Intake"], "parent": 0},
        {"id": 11, "name": "Filtration", "path": ["Plant", "Filtration"],
         "parent": 0},
    ]}
    assert hier.resolve(attribution, "10")["name"] == "Intake"
    assert hier.resolve(attribution, "Plant / Filtration")["id"] == 11
    assert [row["id"] for row in hier.resolve_many(attribution, [10, 10, 11])] \
        == [10, 11]
    with pytest.raises(ValueError, match="known hierarchies"):
        hier.resolve(attribution, "missing")
    with pytest.raises(ValueError, match="no hierarchy selected"):
        hier.resolve(attribution, None)


def test_scope_labels_and_annotation_notes():
    assert hier.scope_label(None) == "System"
    assert hier.scope_label({"kind": "selection", "path": ["A", "B"]}) == "A + B"
    assert hier.scope_label({"kind": "selection", "path": []}) == "selection"
    from raptor.api.insight.charts import ChartSpec
    spec = ChartSpec("pareto", "T", "", "", "", series=[])
    hier.annotate(spec, {"kind": "hierarchy", "path": ["A", "B"]}, note="Note.")
    assert spec.subtitle == "scope: A > B"
    assert spec.caption == "Note."
    assert spec.scope["path"] == ["A", "B"]
    again = hier.annotate(spec, {"kind": "hierarchy", "path": ["C"]})
    assert again is spec and spec.subtitle == "scope: A > B"


def test_depth_and_child_helpers_cover_empty_cases():
    attribution = {"hierarchies": [
        {"id": 10, "name": "A", "path": ["A"], "parent": 0, "depth": 1},
        {"id": 11, "name": "B", "path": ["A", "B"], "parent": 10, "depth": 2},
    ]}
    assert len(hier.rows_at_depth(attribution, None)) == 2
    assert [row["name"] for row in hier.rows_at_depth(attribution, 2)] == ["B"]
    assert [row["name"] for row in hier.rows_at_depth(attribution, 9)] == ["A"]
    assert hier.max_depth(attribution) == 2
    assert hier.child_parts(attribution, {"dtci": {"mean": 0.0}}) == []
    assert hier.per_run(None, None) == []
    assert hier.per_run({}, {"trials": [{"dtci": 0.5, "red_time": 2.0}]}) == [
        {"availability": 0.5, "hierarchy_red_time": 2.0}]


def test_scoped_cost_and_importance_rows():
    costs = [{"name": "Pump", "total": 2.0}, {"name": "Filter", "total": 1.0}]
    elements = [{"name": "Pump"}]
    row = {"id": 10}
    assert hier.cost_rows(costs, None) == costs
    assert hier.cost_rows(costs, row, elements) == [costs[0]]
    importance = {"metrics": {"Availability": {"rows": [
        {"block": "Pump", "raw": 1.0}, {"block": "Filter", "raw": 2.0}]}}}
    assert hier.importance_rows(None, row) is None
    assert hier.importance_rows(importance, None) is importance
    scoped = hier.importance_rows(importance, row, elements)
    assert [item["block"] for item in scoped["metrics"]["Availability"]["rows"]] \
        == ["Pump"]


def test_cumulative_and_ribbon_empty_records():
    assert hier.cumulative_layers({}, []) == ([], 0.0)
    assert hier.cumulative_layers({"outages": {"runs": [{"end": 0}]}}, []) \
        == ([], 0.0)
    assert hier.ribbon_rows({}, run=1) == []
    assert hier.ribbon_rows(
        {"outages": {"runs": [{"start": 1, "end": 1, "hier": {}}]}}) == []
    assert hier.ribbon_rows(
        {"outages": {"runs": [{"start": 0, "end": 1, "hier": {}}]}}, run=2) == []


def test_sankey_needs_a_hierarchy_roll_up():
    from raptor.api.insight.charts import capacity_sankey
    with pytest.raises(ValueError, match="hierarchy roll-up"):
        capacity_sankey({})


def test_sunburst_layout_handles_empty_and_orphan_nodes():
    assert hier.sunburst_layout([]) == []
    nodes = hier.sunburst_layout([
        {"id": "a", "parent": "", "value": 1},
        {"id": "b", "parent": "", "value": 1},
        {"id": "c", "parent": "missing", "value": 0},
    ])
    assert nodes[0]["start"] < nodes[1]["start"]
    assert hier.overlaps([
        {"name": "Pump house", "path": ["Plant", "Pump house"]},
        {"name": "Plant", "path": ["Plant"]},
    ]) == [("Plant", "Pump house")]


def test_a_duplicate_bare_name_is_ambiguous_but_a_path_is_not():
    attribution = {"hierarchies": [
        {"id": 10, "name": "Pump", "path": ["North", "Pump"], "parent": 0},
        {"id": 11, "name": "Pump", "path": ["South", "Pump"], "parent": 0},
    ]}
    with pytest.raises(ValueError, match="ambiguous hierarchy"):
        hier.resolve(attribution, "Pump")
    assert hier.resolve(attribution, "North > Pump")["id"] == 10
    assert hier.resolve(attribution, "South / Pump")["id"] == 11


def test_legacy_rows_without_direct_members_do_not_double_count():
    attribution = {"elements": [
        {"kind": "block", "id": 1, "name": "P1", "dtci": {"mean": 0.3}},
        {"kind": "block", "id": 2, "name": "P2", "dtci": {"mean": 0.2}},
    ], "hierarchies": [
        {"id": 10, "name": "Power", "parent": 0, "path": ["Power"],
         "members": [{"kind": "block", "id": 1}, {"kind": "block", "id": 2}],
         "dtci": {"mean": 0.5}, "causes": {}},
        {"id": 11, "name": "Pumps", "parent": 10, "path": ["Power", "Pumps"],
         "members": [{"kind": "block", "id": 1}], "dtci": {"mean": 0.3},
         "causes": {}},
    ]}
    parts = hier.child_parts(attribution, attribution["hierarchies"][0])
    assert {part["name"] for part in parts} == {"Pumps", "P2"}
    assert sum(part["share"] for part in parts) == pytest.approx(1.0)


def test_hierarchy_rows_with_no_trials_do_not_divide_by_zero():
    from raptor.api.insight.attribution import _hierarchy_rows
    rows = _hierarchy_rows([], {
        "names": {10: "Power"}, "parent": {10: 0},
        "members": {10: {("block", 1)}}, "paths": {10: ["Power"]},
        "depth": {10: 1}, "children": {10: []}, "direct": {10: {("block", 1)}},
    })
    assert rows[0]["dtci"]["mean"] == 0.0


def test_hierarchy_rows_carry_their_phase_mix():
    trial = TrialAttribution(span=100.0, red_time=10.0)
    trial.phase_time = {"Cruise": 60.0, "Launch": 40.0}
    trial.phase_red = {"Cruise": 6.0, "Launch": 4.0}
    trial.phase_initiated = {"Cruise": {("block", 1): 6.0},
                             "Launch": {("block", 1): 4.0}}
    trial.initiated[("block", 1)] = Initiated(outages=1, red_time=10.0)
    rows = _hierarchy_rows([trial], _tree(), [{"name": "Cruise"},
                                             {"name": "Launch"}])
    power = next(row for row in rows if row["name"] == "Intake")
    assert power["phase_red"] == {"Cruise": 6.0, "Launch": 4.0}
    assert power["phase_shares"] == {"Cruise": 0.6, "Launch": 0.4}


def test_per_run_omits_runs_with_no_system_outage():
    row = {"trials": [
        {"dtci": 0.6, "red_time": 6.0, "system_red_time": 10.0},
        {"dtci": 0.0, "red_time": 0.0, "system_red_time": 0.0}]}
    assert hier.per_run({}, row) == [{"availability": 0.6,
                                      "hierarchy_red_time": 6.0}]
