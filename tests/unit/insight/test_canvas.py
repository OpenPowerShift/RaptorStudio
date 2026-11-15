"""Result layers for the diagram: share, cause, certainty (plan 7.6.1)."""
from __future__ import annotations

import pytest

from raptor.api.insight.canvas import CAUSE_GLYPHS, LAYERS, canvas_layer


def _attribution():
    return {
        "runs": 4,
        "elements": [
            {"kind": "block", "id": 1, "name": "Pump",
             "dtci": {"mean": 0.6, "sem": 0.02}, "main_cause": "repair",
             "causes": {"repair": 0.8, "logistics": 0.2}},
            {"kind": "block", "id": 2, "name": "Valve",
             "dtci": {"mean": 0.3, "sem": 0.2}, "main_cause": "wait_spare",
             "causes": {"wait_spare": 1.0}},
            {"kind": "event", "id": 3, "name": "Grid",
             "dtci": {"mean": 0.1, "sem": None}, "main_cause": "other",
             "causes": {}},
            {"kind": "block", "id": 4, "name": "Spare unit",
             "dtci": {"mean": 0.0, "sem": 0.0}, "main_cause": None, "causes": {}},
        ],
        "capacity": {"generated": 100.0, "elements": [
            {"kind": "block", "id": 2, "name": "Valve", "share": {"mean": 0.7, "sem": 0.01}},
            {"kind": "block", "id": 1, "name": "Pump", "share": {"mean": 0.3, "sem": 0.01}},
        ]},
    }


def test_downtime_share_bands_against_the_largest_and_names_the_cause():
    layer = canvas_layer(_attribution(), "downtime-share")
    assert layer["measure"] == "share of system downtime"
    assert [r["name"] for r in layer["elements"]] == ["Pump", "Valve", "Grid"]
    pump, valve, grid = layer["elements"]
    assert (pump["band"], valve["band"], grid["band"]) == ("high", "mid", "low")
    assert pump["glyph"] == CAUSE_GLYPHS["repair"] == "R"
    assert pump["cause_label"] == "Hands-on repair"
    assert pump["low"] == pytest.approx(0.56) and pump["high"] == pytest.approx(0.64)


def test_a_share_the_runs_disagree_on_is_marked_uncertain():
    rows = {r["name"]: r for r in canvas_layer(_attribution(), "downtime-share")["elements"]}
    assert rows["Valve"]["uncertain"]          # ±0.4 on a share of 0.3
    assert rows["Valve"]["low"] == 0.0         # the range stops at 0
    assert not rows["Pump"]["uncertain"]
    assert not rows["Grid"]["uncertain"]       # no spread to judge: not marked


def test_lost_capacity_takes_the_cause_from_the_downtime_row():
    layer = canvas_layer(_attribution(), "lost-capacity")
    assert [r["name"] for r in layer["elements"]] == ["Valve", "Pump"]
    assert layer["elements"][0]["main_cause"] == "wait_spare"
    assert layer["elements"][1]["band"] == "mid"        # 0.3 of 0.7


def test_unknown_layers_and_missing_capacity_say_what_is_wrong():
    assert set(LAYERS) == {"downtime-share", "lost-capacity"}
    with pytest.raises(ValueError, match="unknown layer"):
        canvas_layer(_attribution(), "cost")
    no_capacity = {**_attribution(), "capacity": None}
    with pytest.raises(ValueError, match="capacity analysis"):
        canvas_layer(no_capacity, "lost-capacity")
