"""The report's front matter: Story, charts and checks from one run."""
from __future__ import annotations

from raptor.api import build
from raptor.api import run as run_api
from raptor.api.insight import report_section
from raptor.api.workspace import Workspace

SPEC = {
    "experiment": {"trials": 6, "termination_type": "TimeSim",
                   "term_condition": 8760.0},
    "blocks": [{"name": "Pump", "fail_dist": "Exponential",
                "fail_params": [1200.0, 0.0], "repair_dist": "Exponential",
                "repair_params": [20.0, 0.0]}],
    "nodes": [{"name": "Start", "type": 1}, {"name": "End", "type": 3}],
    "links": [{"from": "Start", "to": "Pump"}, {"from": "Pump", "to": "End"}],
}


def test_the_section_carries_the_story_charts_and_measure_guide(tmp_path):
    ws = Workspace(tmp_path)
    rbd = build.build_from_spec(SPEC)
    mid = ws.register_model(rbd, name="m")["model_id"]
    result = run_api.run(ws, mid, seed=2, attribution=True)
    section = report_section.from_run(ws, result.run_id, rbd=rbd)
    assert section.story is not None
    assert section.story["provenance"]["runs"] == 6
    titles = [spec.title for spec in section.charts]
    assert "Run-to-run availability" in titles
    assert "Who causes the downtime" in titles
    assert "Downtime treemap" in titles
    assert "availability" in section.measure_ids
    assert isinstance(section.findings, list)


def test_suppressed_findings_travel_with_their_reasons(tmp_path):
    ws = Workspace(tmp_path)
    rbd = build.build_from_spec(SPEC)
    mid = ws.register_model(rbd, name="m")["model_id"]
    result = run_api.run(ws, mid, seed=2)
    section = report_section.from_run(
        ws, result.run_id,
        suppressed=[{"rule": "M-SPR-001", "reason": "spares on order"}])
    assert section.suppressed == [{"rule": "M-SPR-001",
                                   "reason": "spares on order"}]


def test_a_nested_model_with_no_hierarchy_downtime_still_reports():
    from raptor.api.insight import report_section
    attribution = {
        "runs": 2, "units": "hours",
        "system": {"red_time": {"mean": 0.0}},
        "hierarchies": [
            {"id": 10, "name": "Power", "path": ["Power"], "parent": 0,
             "depth": 1, "members": [{"kind": "block", "id": 1}],
             "dtci": {"mean": 0.0}, "causes": {}},
            {"id": 11, "name": "Pumps", "parent": 10,
             "path": ["Power", "Pumps"], "depth": 2,
             "members": [{"kind": "block", "id": 1}],
             "dtci": {"mean": 0.0}, "causes": {}},
        ],
    }
    section = report_section.from_parts(
        {"headline": {}, "units": "hours", "findings": []}, [],
        attribution=attribution)
    assert "Hierarchy sunburst" not in [spec.title for spec in section.charts]


def test_a_phased_run_reports_the_phase_chart_and_an_unphased_one_does_not():
    base = {"runs": 2, "system": {"red_time": {"mean": 1.0}},
            "elements": [], "causes": {}, "outage_durations": {},
            "hierarchies": []}

    def titles(phases):
        section = report_section.from_parts(
            {"headline": {}, "units": "hours", "findings": []}, [],
            attribution={**base, "phases": phases})
        return [spec.title for spec in section.charts]

    assert "Downtime by phase" not in titles([])
    rows = [{"name": "Launch", "mission": True, "share_of_downtime": 1.0,
             "availability": 0.9, "drivers": [],
             "causes": {"repair": 1.0}}]
    assert "Downtime by phase" in titles(rows)
    assert "Phase by cause" in titles(rows)

def test_a_cost_run_puts_its_cost_charts_in_the_report():
    from pathlib import Path
    from raptor.api.insight import cost_series
    root = Path(__file__).resolve().parents[3]
    runs = cost_series.parse_cost_file(
        str(root / "tests" / "golden" / "ReleaseProbe27" / "legacy"
            / "rpt_interim_cost_ReleaseProbe27.txt"))
    section = report_section.from_parts(
        {"headline": {}, "units": "hours", "findings": []},
        [{"ending_sim_time": 2160.0}] * len(runs), cost_runs=runs)
    titles = [spec.title for spec in section.charts]
    assert "Cost by category" in titles
    assert "Cumulative operating cost over time" in titles
    assert "Operating cost per year" in titles


def test_the_report_adds_the_reliability_charts_and_the_ladder_waterfall():
    per_run = [{"availability": 0.99, "mttff": 100.0, "mttff_censored": False,
                "num_component_failures": 4,
                "criticality": {"1": {"name": "Pump", "failure": 1.0}},
                "ending_sim_time": 500.0},
               {"availability": 1.0, "mttff": 500.0, "mttff_censored": True,
                "num_component_failures": 0, "ending_sim_time": 500.0}]
    attribution = {"runs": 2, "causes": {"repair": 1.0},
                   "elements": [{"name": "Pump", "kind": "block", "id": 1,
                                 "dtci": {"mean": 1.0}, "causes": {"repair": 1.0}}]}
    section = report_section.from_parts(
        {"headline": {}, "units": "hours", "findings": []}, per_run,
        attribution=attribution)
    by_title = {spec.title: spec for spec in section.charts}
    assert "Reliability over time" in by_title
    assert "Which components fail most often?" in by_title
    waterfall = by_title["From full availability to the achieved one"]
    # the same basis as the Charts tab and run_chart
    assert waterfall.provenance["basis"] == "ladder"
    # no system failure in any run: R(t) is a flat line and is left out
    quiet = report_section.from_parts(
        {"headline": {}, "units": "hours", "findings": []},
        [{**per_run[1]}, {**per_run[1]}])
    assert "Reliability over time" not in {spec.title for spec in quiet.charts}
