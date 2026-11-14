"""Downtime attribution, checked on fixed-time models worked out by hand."""
from __future__ import annotations

import glob
from itertools import pairwise

import pytest

from raptor.api import build
from raptor.api.insight.attribution import (
    CAUSES, aggregate, attribute_logs, attribute_trial, block_state,
)
from raptor.core.engine.runner import run_simulation
from raptor.core.fileio.rbd import load_rbd


def fixed(name, fail_at, repair, **more):
    return {"name": name, "fail_dist": "Fixed", "fail_params": [fail_at, 0, 0],
            "repair_dist": "Fixed", "repair_params": [repair, 0, 0], **more}


def series(*blocks, length=40.0, **extra):
    names = [b["name"] for b in blocks]
    chain = ["Start", *names, "End"]
    return {"blocks": list(blocks),
            "nodes": [{"name": "Start", "type": 1}, {"name": "End", "type": 3}],
            "links": [{"from": a, "to": b} for a, b in pairwise(chain)],
            "experiment": {"trials": 1, "termination_type": "TimeSim", "term_condition": length},
            **extra}


def parallel(*blocks, length=40.0):
    links = [{"from": "Start", "to": "S"}, {"from": "J", "to": "End"}]
    for b in blocks:
        links += [{"from": "S", "to": b["name"]}, {"from": b["name"], "to": "J"}]
    return {"blocks": list(blocks),
            "nodes": [{"name": "Start", "type": 1}, {"name": "S", "type": 2, "k": 1},
                      {"name": "J", "type": 2, "k": 1}, {"name": "End", "type": 3}],
            "links": links,
            "experiment": {"trials": 1, "termination_type": "TimeSim", "term_condition": length}}


def trial(spec, start_stats=0.0):
    logs = run_simulation(build.build_from_spec(spec), 1)
    return attribute_trial(logs[0], start_stats)


def by_name(t):
    return {t.names[k]: v for k, v in t.initiated.items()}


def test_two_in_series_each_owns_its_own_outage():
    # A fails at 10 for 2 (then again at 22 for 2, 34 for 2); B fails at 25 for 5
    t = trial(series(fixed("A", 10, 2), fixed("B", 25, 5)))
    got = by_name(t)
    assert t.outages == sum(v.outages for v in got.values())
    assert t.red_time == pytest.approx(sum(v.red_time for v in got.values()))
    assert got["B"].red_time == pytest.approx(5.0)
    assert got["A"].outages >= 2 and got["A"].red_time == pytest.approx(2.0 * got["A"].outages)
    assert t.failure_outages == t.outages
    assert t.cause_time == pytest.approx({"repair": t.red_time})


def test_parallel_pair_the_second_to_fail_starts_the_outage():
    # A down 10..30, B down 20..24: the system is down only 20..24
    t = trial(parallel(fixed("A", 10, 20), fixed("B", 20, 4)))
    got = by_name(t)
    assert t.outages == 1 and t.red_time == pytest.approx(4.0)
    assert list(got) == ["B"] and got["B"].red_time == pytest.approx(4.0)
    assert got["B"].failures == 1


def test_waiting_for_a_spare_is_split_from_the_repair():
    # no spare on the shelf; one arrives every 15: A fails at 10, waits to 15, repairs 3
    a = fixed("A", 10, 3, sparing_type="Custom", stock=0, rso=1, new_spares=1,
              arrive_every=15, eso=0)
    t = trial(series(a, length=20.0))
    assert t.red_time == pytest.approx(8.0)
    assert t.cause_time["wait_spare"] == pytest.approx(5.0)
    assert t.cause_time["repair"] == pytest.approx(3.0)
    assert by_name(t)["A"].red_time == pytest.approx(8.0)


def test_waiting_for_a_crew_is_charged_to_the_second_block():
    # both fail at 10; one crew; repairs take 4 each: down 10..18
    blocks = [fixed(n, 10, 4, num_resources=1, resource_name="Crew") for n in ("A", "B")]
    t = trial(series(*blocks, length=19.0, resources=[{"name": "Crew", "num_in": 1}]))
    assert t.red_time == pytest.approx(8.0) and t.outages == 1
    assert t.cause_time.get("wait_resource", 0.0) + t.cause_time["repair"] == pytest.approx(8.0)
    assert t.cause_time["repair"] >= 4.0


def test_logistics_delay_is_its_own_cause():
    a = fixed("A", 10, 3, pre_ldt_dist=19, pre_ldt_params=[2, 0, 0])
    t = trial(series(a, length=16.0))
    assert t.cause_time["logistics"] == pytest.approx(2.0)
    assert t.cause_time["repair"] == pytest.approx(3.0)


def test_maintenance_outages_count_as_downing_events_not_failures():
    a = {"name": "A", "fail_dist": "Fixed", "fail_params": [1000, 0, 0],
         "repair_dist": "Fixed", "repair_params": [1, 0, 0], "uses_pm": True, "pm_freq": 10,
         "pm_duration_dist": 19, "pm_params": [2, 0, 0]}
    t = trial(series(a, length=30.0))
    assert t.outages >= 2 and t.failure_outages == 0
    assert t.cause_time == pytest.approx({"pm": t.red_time})
    assert by_name(t)["A"].failures == 0


def test_statistics_start_clips_what_is_counted():
    full = trial(series(fixed("A", 10, 2)))
    late = trial(series(fixed("A", 10, 2)), start_stats=11.0)
    assert late.span == pytest.approx(29.0)
    assert late.red_time == pytest.approx(full.red_time - 1.0)
    assert late.outages == full.outages - 1


def test_own_state_time_adds_up_to_the_span():
    t = trial(series(fixed("A", 10, 2), fixed("B", 25, 5)))
    for times in t.own_time.values():
        assert sum(times.values()) == pytest.approx(t.span)


def test_block_state_table():
    assert block_state("RepHold", "Waiting_for_Spare", "other") == "wait_spare"
    assert block_state("RepHold", "Waiting_for_resource(s)", "other") == "wait_resource"
    assert block_state("RepHold", "Phase_Change", "wait_spare") == "wait_spare"
    assert block_state("Done", "End_sim", "repair") == "repair"
    assert block_state("Done", "No_spares", "wait_spare") == "done"
    assert block_state("Sparing", "Spare_Arrived", "up") is None
    assert block_state("Cut", "Phase_Change", "up") == "bypass"


def test_aggregate_shares_and_sequences():
    spec = series(fixed("A", 10, 2), fixed("B", 25, 5))
    result = attribute_logs(run_simulation(build.build_from_spec(spec), 1))
    assert result["runs"] == 1 and result["runs_with_outage"] == 1
    shares = {e["name"]: e["dtci"]["mean"] for e in result["elements"]}
    assert sum(shares.values()) == pytest.approx(1.0)
    assert result["elements"][0]["name"] in ("A", "B")
    assert result["elements"][0]["main_cause"] == "repair"
    assert sum(result["causes"].values()) == pytest.approx(1.0)
    assert sum(s["share"] for s in result["sequences"]) == pytest.approx(1.0)
    assert result["sequences"][0]["steps"][0]["cause"] in CAUSES
    assert aggregate([])["elements"] == []


@pytest.mark.parametrize("path", sorted(glob.glob("tests/golden/*/input.rbd"))[::6],
                         ids=lambda p: p.split("/")[-2])
def test_conservation_on_golden_fixtures(path):
    """Attributed downtime adds up to the system's Red time, by initiator, by
    holder and by cause, in every run; and each element's time adds to the span."""
    for lines in run_simulation(load_rbd(path), 2):
        t = attribute_trial(lines)
        assert sum(v.red_time for v in t.initiated.values()) == pytest.approx(t.red_time, abs=1e-6)
        assert sum(t.holding.values()) == pytest.approx(t.red_time, abs=1e-6)
        assert sum(t.cause_time.values()) == pytest.approx(t.red_time, abs=1e-6)
        assert sum(v.outages for v in t.initiated.values()) == t.outages
        for times in t.own_time.values():
            assert sum(times.values()) == pytest.approx(t.span, rel=1e-9)


# ---- downtime by mission phase ----------------------------------------------

def _line(time, kind, name, action, comment="", element=None):
    from types import SimpleNamespace
    return SimpleNamespace(time=time, type=kind, name=name, action=action,
                           comment=comment, element=element)


def _phased_log():
    """Phase Launch from 0 (not logged, as the engine does), Cruise from 10,
    end at 30.  Pump fails at 5 and is back at 15: down 5 h in Launch and
    5 h in Cruise; Valve fails at 20 for 2 h, all in Cruise."""
    pump, valve = ("Block", 1), ("Block", 2)
    return [
        _line(5, "Block", "Pump", "Failed", element=pump),
        _line(5, "Block", "Pump", "Repairing", element=pump),
        _line(5, "System", "State_change", "Red"),
        _line(10, "System", "Phase_change", "Phase_change", "Begin_phase_Cruise"),
        _line(15, "Block", "Pump", "Good", element=pump),
        _line(15, "System", "State_change", "Green"),
        _line(20, "Block", "Valve", "Failed", element=valve),
        _line(20, "Block", "Valve", "Repairing", element=valve),
        _line(20, "System", "State_change", "Red"),
        _line(22, "Block", "Valve", "Good", element=valve),
        _line(22, "System", "State_change", "Green"),
        _line(30, "System", "Simulation_Terminate", "End"),
    ]


def test_downtime_by_phase_by_hand():
    phases = [{"name": "Launch", "mission": True}, {"name": "Cruise", "mission": True}]
    result = attribute_logs([_phased_log(), _phased_log()], phases=phases)
    rows = {r["name"]: r for r in result["phases"]}
    assert list(rows) == ["Launch", "Cruise"]
    launch, cruise = rows["Launch"], rows["Cruise"]
    assert launch["time"]["mean"] == pytest.approx(10.0)
    assert cruise["time"]["mean"] == pytest.approx(20.0)
    assert launch["red_time"]["mean"] == pytest.approx(5.0)
    assert cruise["red_time"]["mean"] == pytest.approx(7.0)
    assert launch["availability"] == pytest.approx(0.5)          # 1 - 5/10
    assert cruise["availability"] == pytest.approx(1 - 7 / 20)
    assert launch["share_of_downtime"] == pytest.approx(5 / 12)
    # Pump started the outage that ran into Cruise, so it owns that 5 h too
    assert [(d["name"], round(d["share"], 6)) for d in cruise["drivers"]] == [
        ("Pump", round(5 / 7, 6)), ("Valve", round(2 / 7, 6))]
    assert launch["drivers"] == [{"name": "Pump", "kind": "Block", "id": 1, "share": 1.0}]
    # the phases add up to the whole run
    assert launch["red_time"]["mean"] + cruise["red_time"]["mean"] == pytest.approx(
        result["system"]["red_time"]["mean"])


def test_without_the_phase_list_phase_one_is_named_as_such():
    rows = attribute_logs([_phased_log()])["phases"]
    assert [r["name"] for r in rows] == ["(first phase)", "Cruise"]
    assert rows[0]["mission"] is None


def test_an_unphased_run_has_no_phase_section():
    log = [line for line in _phased_log() if line.name != "Phase_change"]
    assert "phases" not in attribute_logs([log])


def test_phases_add_up_on_a_real_phased_run():
    from raptor.api.insight.attribution import model_phases
    rbd = load_rbd(glob.glob("tests/golden/Phasing1/input.rbd")[0])
    logs = run_simulation(rbd, 3)
    phases = model_phases(rbd)
    assert phases and phases[0]["name"] == "D000-075_Orbit"
    result = attribute_logs(logs, phases=phases)
    rows = result["phases"]
    assert [r["name"] for r in rows][:len(phases)] == [p["name"] for p in phases]
    assert sum(r["time"]["mean"] for r in rows) == pytest.approx(
        result["system"]["span"]["mean"])
    assert sum(r["red_time"]["mean"] for r in rows) == pytest.approx(
        result["system"]["red_time"]["mean"])


def test_shares_are_pooled_over_the_runs_not_averaged_per_run():
    """Three runs each with one outage, of 9, 6 and 3 hours, each started by
    a different element: their downtime shares are 1/2, 1/3, 1/6 -- not
    1/3 each, which averaging every run's own 100 % gave."""
    from raptor.api.insight.attribution import _ratio_stat
    red_by_a = [9.0, 0.0, 0.0, 0.0]
    system_red = [9.0, 6.0, 3.0, 0.0]          # the last run had no outage
    stat = _ratio_stat(red_by_a, system_red)
    assert stat["mean"] == pytest.approx(0.5)
    assert stat["n"] == 3 and stat["min"] == 0.0 and stat["max"] == 1.0
    assert stat["sem"] is not None and stat["sem"] > 0
    assert _ratio_stat([0.0], [0.0])["mean"] is None


def test_elements_that_share_a_name_are_told_apart():
    """'unnamed', empty and duplicated names get their kind and id; others
    stay as they are."""
    from raptor.api.insight.attribution import distinct_names
    names = {("block", 1): "unnamed", ("block", 2): "unnamed",
             ("block", 3): "Pump", ("block", 4): "CMD_5000",
             ("node", 5): "CMD_5000", ("block", 6): ""}
    assert distinct_names(names) == {
        ("block", 1): "unnamed (block 1)", ("block", 2): "unnamed (block 2)",
        ("block", 3): "Pump", ("block", 4): "CMD_5000 (block 4)",
        ("node", 5): "CMD_5000 (node 5)", ("block", 6): "block 6"}
