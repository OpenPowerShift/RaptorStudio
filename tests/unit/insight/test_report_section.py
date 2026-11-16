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
