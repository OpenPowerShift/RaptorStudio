"""Run rules that read the downtime attribution."""
from __future__ import annotations

from raptor.api import build
from raptor.api.advisor import advise_run

from .helpers import block, series_spec

RBD = build.build_from_spec(series_spec(block("Pump"), block("Valve"), block("Fan")))


def stat(mean, sem=0.01):
    return {"mean": mean, "sem": sem, "n": 50}


def element(name, oid, share, causes, sem=0.01, red=10.0):
    return {"kind": "block", "id": oid, "name": name, "dtci": stat(share, sem),
            "deci": stat(share), "fci": stat(share), "red_time": stat(red * share),
            "causes": causes, "main_cause": max(causes, key=causes.get) if causes else None}


def attribution(elements, causes):
    return {"runs": 50, "runs_with_outage": 48, "units": "hours", "elements": elements,
            "causes": causes, "system": {"red_time": stat(10.0), "outages": stat(4.0)}}


def fired(attr, rule=None):
    out = advise_run(RBD, {}, [], attribution=attr)
    return [f for f in out if rule is None or f.rule == rule]


BASE = attribution(
    [element("Pump", 1, 0.7, {"wait_spare": 0.8, "repair": 0.2}),
     element("Valve", 2, 0.2, {"repair": 1.0}),
     element("Fan", 3, 0.1, {"repair": 0.35, "logistics": 0.35, "pm": 0.3})],
    {"wait_spare": 0.56, "repair": 0.375, "logistics": 0.035, "pm": 0.03})


def test_silent_without_an_attribution():
    assert advise_run(RBD, {}, []) == []


def test_R_DRV_001_names_enough_to_cover_80_percent():
    f, = fired(BASE, "R-DRV-001")
    assert f.message.startswith("2 of 3 elements cause 90 % of system downtime: Pump 70 %, Valve 20 %")
    assert [t.name for t in f.targets] == ["Pump", "Valve"]


def test_R_DRV_002_gives_the_lever_for_each_top_contributor():
    found = {f.targets[0].name: f for f in fired(BASE, "R-DRV-002")}
    assert set(found) == {"Pump", "Valve"}             # Fan has no cause at 40 %
    assert "waiting for a spare" in found["Pump"].message
    assert "More stock or faster delivery" in found["Pump"].message
    assert "Shorter repairs" in found["Valve"].message
    assert found["Pump"].evidence["cause_share"] == 0.8


def test_R_DRV_006_only_when_the_gap_is_inside_the_noise():
    assert not fired(BASE, "R-DRV-006")
    close = attribution([element("Pump", 1, 0.45, {"repair": 1.0}, sem=0.04),
                         element("Valve", 2, 0.40, {"repair": 1.0}, sem=0.04)], {"repair": 1.0})
    f, = fired(close, "R-DRV-006")
    assert "Pump" in f.message and "Valve" in f.message


def test_system_cause_rules_and_their_thresholds():
    rules = {f.rule: f for f in fired(BASE)}
    assert rules["R-SPR-101"].severity == "warning" and "56 %" in rules["R-SPR-101"].message
    assert "Mostly at Pump" in rules["R-SPR-101"].message
    assert "R-PM-101" not in rules and "R-LOG-101" not in rules and "R-RES-101" not in rules
    crew = attribution([element("Pump", 1, 1.0, {"wait_resource": 0.15, "repair": 0.85})],
                       {"wait_resource": 0.15, "repair": 0.85})
    assert {f.rule: f.severity for f in fired(crew)}["R-RES-101"] == "advice"


def test_no_outages_gives_no_driver_findings():
    assert fired(attribution([], {})) == []
