"""The Advisor's findings grouped by the element they are about."""
from __future__ import annotations

from raptor.api.advisor.canvas import advisor_layer
from raptor.api.advisor.finding import Finding, Target


def _finding(rule, severity, *targets, title=None):
    return Finding(rule=rule, severity=severity, category="test", title=title or rule,
                   message="", why="", targets=tuple(targets))


def test_findings_group_by_element_worst_first_and_settings_stay_off_canvas():
    pump, valve = Target("block", 1, "Pump"), Target("block", 2, "Valve")
    layer = advisor_layer([
        _finding("M-A", "advice", pump),
        _finding("M-B", "warning", pump, valve),
        _finding("M-C", "error", valve),
        _finding("M-D", "info", Target("pool", None, "Spares")),
        _finding("M-E", "warning", Target("system", None, "system")),
        _finding("M-B", "warning", pump),          # the same finding twice: once
    ])
    assert layer["findings"] == 6 and layer["off_canvas"] == 2
    rows = {r["name"]: r for r in layer["elements"]}
    assert [r["name"] for r in layer["elements"]] == ["Valve", "Pump"]   # error first
    assert rows["Valve"]["severity"] == "error" and rows["Valve"]["count"] == 2
    assert rows["Pump"]["severity"] == "warning" and rows["Pump"]["count"] == 2
    assert [f["rule"] for f in rows["Pump"]["findings"]] == ["M-B", "M-A"]


def test_no_findings_is_an_empty_layer():
    assert advisor_layer([]) == {"layer": "advisor", "elements": [], "findings": 0,
                                 "off_canvas": 0}
