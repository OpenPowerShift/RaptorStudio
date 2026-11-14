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


# ---- capacity (issue #256) -------------------------------------------------

def capacity_element(name, oid, share, dtci, through=0.0):
    return {"kind": "block", "id": oid, "name": name,
            "share": stat(share), "lost_energy": stat(100.0 * share),
            "through_dependents": stat(through), "standalone": 1.0,
            "dtci": stat(dtci)}


def capacity_attribution(rows, delivered=0.9, availability=0.99):
    attr = attribution([element(r["name"], r["id"], r["dtci"]["mean"],
                                {"repair": 1.0}) for r in rows], {"repair": 1.0})
    attr["capacity"] = {
        "rule": "proportional", "generated": 100.0,
        "lost_energy": stat(250.0), "elements": rows, "causes": {},
        "notes": [],
    }
    per_run = [{"availability": availability,
                "capacity_weighted_availability": delivered}] * 5
    return attr, per_run


def test_R_CAP_101_says_how_much_demand_was_not_delivered():
    attr, per_run = capacity_attribution(
        [capacity_element("Pump", 1, 0.6, 0.2)], delivered=0.82)
    out = advise_run(RBD, {}, per_run, attribution=attr)
    found = {f.rule: f for f in out}
    assert "82 %" in found["R-CAP-101"].message
    assert "Pump loses the most capacity (60 % of the loss)" in found["R-CAP-101"].message


def test_R_CAP_102_when_the_two_shares_differ_by_a_factor_of_two():
    attr, per_run = capacity_attribution(
        [capacity_element("Pump", 1, 0.3, 0.7),     # down long, loses little
         capacity_element("Grid", 2, 0.7, 0.3)],    # down little, loses much
        delivered=0.95)
    out = {f.targets[0].name: f for f in advise_run(RBD, {}, per_run,
                                                    attribution=attr)
           if f.rule == "R-CAP-102"}
    assert set(out) == {"Pump", "Grid"}
    assert "removes a large share of the flow" in out["Grid"].message
    assert "removes little flow" in out["Pump"].message


def test_R_CAP_103_when_availability_overstates_the_flow():
    attr, per_run = capacity_attribution(
        [capacity_element("Pump", 1, 1.0, 1.0)], delivered=0.90,
        availability=0.99)
    found = {f.rule: f for f in advise_run(RBD, {}, per_run, attribution=attr)}
    assert found["R-CAP-103"].severity == "warning"
    assert "99 %" in found["R-CAP-103"].message and "90 %" in found["R-CAP-103"].message


def test_capacity_rules_are_silent_without_capacity_data():
    assert not [f for f in fired(BASE) if f.rule.startswith("R-CAP")]
