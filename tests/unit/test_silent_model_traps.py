"""Models that build and run but are not what the author meant, and the
messages that now say so."""
from __future__ import annotations

from itertools import pairwise

from raptor.api import build
from raptor.api.validate import issues
from raptor.cli import cmd_info, cmd_tools

NODES = [{"name": "Start", "type": "start"}, {"name": "End", "type": "end"}]


def _spec(*blocks):
    names = [b["name"] for b in blocks]
    chain = ["Start", *names, "End"]
    return {"blocks": list(blocks), "nodes": NODES,
            "links": [{"from": a, "to": b} for a, b in pairwise(chain)]}


GOOD = {"name": "A", "fail_dist": "Exponential", "fail_params": [1000],
        "repair_dist": "Lognormal", "repair_params": [8, 2]}


def test_a_block_with_no_failure_data_is_named():
    spec = _spec(GOOD, {"name": "Bare"})
    notes = build.default_data_notes(spec, build.build_from_spec(spec))
    assert len(notes) == 2
    assert "Bare" in notes[0] and "placeholder" in notes[0]
    assert "Exponential" in notes[0] and "mean 100" in notes[0]
    assert "Bare" in notes[1] and "repair" in notes[1]
    assert all("A," not in n and ": A" not in n for n in notes)


def test_a_fully_specified_model_has_no_notes():
    spec = _spec(GOOD)
    assert build.default_data_notes(spec, build.build_from_spec(spec)) == []
    assert build.default_data_notes({}, None) == []


def test_many_defaulted_blocks_are_counted_not_all_listed():
    spec = _spec(*[{"name": f"B{i}"} for i in range(12)])
    note = build.default_data_notes(spec)[0]
    assert note.startswith("12 block(s)") and "and 4 more" in note


def _life_warnings(spec):
    return [i for i in issues(build.build_from_spec(spec))
            if "shorter than mean repair" in i.message]


def test_a_rate_typed_as_the_mean_life_is_warned_about():
    found = _life_warnings(_spec(dict(GOOD, fail_params=[0.001])))
    assert len(found) == 1 and found[0].severity == "warning"
    assert "0.001" in found[0].message and "not a failure rate" in found[0].message


def test_a_sensible_block_is_not_warned_about():
    assert _life_warnings(_spec(GOOD)) == []


def test_the_warning_is_capped_on_a_model_full_of_them():
    blocks = [dict(GOOD, name=f"B{i}", fail_params=[0.5]) for i in range(15)]
    found = _life_warnings(_spec(*blocks))
    assert len(found) == 10
    more = [i for i in issues(build.build_from_spec(_spec(*blocks)))
            if "5 more block(s)" in i.message]
    assert len(more) == 1


def test_an_unknown_field_suggests_the_nearest_and_lists_friendly_names():
    import pytest
    rbd = build.build_from_spec(_spec(GOOD))
    row = rbd.section("BLOCK_DATA").rows[-1]
    with pytest.raises(build.BuildError) as exc:
        build._column_targets("BLOCK_DATA", row, "fail_parms")
    text = str(exc.value)
    assert "Did you mean 'fail_params'" in text
    assert "FParam1" not in text            # raw columns are not listed


def _rows(values, metrics, error=None):
    return [{"values": {"x": v}, "metrics": m, "error": error}
            for v, m in zip(values, metrics)]


def test_a_sweep_that_changes_nothing_says_so():
    same = _rows(["1", "2", "3"], [{"Availability": 0.9}] * 3)
    note = cmd_tools._no_effect_note(["x"], ["Availability"], same)
    assert note is not None and "no effect" in note
    differ = _rows(["1", "2"], [{"Availability": 0.9}, {"Availability": 0.95}])
    assert cmd_tools._no_effect_note(["x"], ["Availability"], differ) is None
    one = _rows(["1"], [{"Availability": 0.9}])
    assert cmd_tools._no_effect_note(["x"], ["Availability"], one) is None
    failed = _rows(["1", "2"], [{"Availability": None}] * 2, error="boom")
    assert cmd_tools._no_effect_note(["x"], ["Availability"], failed) is None


def test_info_text_lists_nodes_once_with_named_kinds():
    nodes = [{"id": 2, "name": "Start", "type": 1, "k": -1, "n": 0,
              "full_flow": 1, "div_flow": 1}]
    data = {"name": "m", "node_names": nodes, "nodes_detail": list(nodes),
            "links": [{"id": 4, "nom_flow": -2, "max_flow": -1, "cap_pri": 50}],
            "system": {"capacity_analysis": False}}
    shown = cmd_info._for_reading(data)
    assert "nodes_detail" not in shown
    assert shown["node_names"][0] == {"id": 2, "name": "Start",
                                      "type": "start", "k": -1, "n": 0}
    assert shown["links"][0] == {"id": 4}
    assert data["node_names"][0]["type"] == 1      # the record is untouched
    with_capacity = cmd_info._for_reading(
        dict(data, system={"capacity_analysis": True}))
    assert with_capacity["links"][0]["nom_flow"] == -2
