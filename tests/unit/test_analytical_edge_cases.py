"""Edge cases found in the line-by-line solver review."""
from __future__ import annotations

import math

import pytest

from raptor.api.analytical import analyse_rbd
from raptor.api.build import build_from_spec
from raptor.core.analytical import build, evaluate, survival
from raptor.core.engine.load import load_model
from raptor.core.rng import Distribution

EXPONENTIAL = int(Distribution.EXPONENTIAL)
FIXED = int(Distribution.FIXED)
EXTREME = int(Distribution.EXTREME_VALUE)

START = {"name": "Start", "type": 1, "n": 0, "out": 1}
END = {"name": "End", "type": 3, "n": 1, "out": 0}


def _block(name, dist=EXPONENTIAL, params=(100.0, 0.0, 0.0)):
    return {"name": name, "fail_dist": dist, "fail_params": list(params)}


def _node(name):
    return {"name": name, "type": 2, "n": 1, "out": 1}


def _rbd(blocks, nodes, links):
    return build_from_spec(
        {"system": {"run_length": 1000.0, "num_runs": 1,
                    "termination": "TimeSim"},
         "blocks": blocks, "nodes": [START, END, *nodes], "links": links},
        auto_layout=False)


def _series(blocks):
    """Start -> b0 -> n0 -> b1 -> ... -> End, with a node between blocks."""
    nodes = [_node(f"N{i}") for i in range(len(blocks) - 1)]
    links = [{"from": "Start", "to": blocks[0]["name"]}]
    for i in range(len(blocks) - 1):
        links += [{"from": blocks[i]["name"], "to": f"N{i}"},
                  {"from": f"N{i}", "to": blocks[i + 1]["name"]}]
    links.append({"from": blocks[-1]["name"], "to": "End"})
    return _rbd(blocks, nodes, links)


def test_block_fed_only_by_an_unreachable_node_cannot_carry_a_signal():
    rbd = _rbd(
        [_block("A"), _block("C")], [_node("M")],
        [{"from": "Start", "to": "A"}, {"from": "A", "to": "End"},
         {"from": "M", "to": "C"}, {"from": "C", "to": "End"}])
    # The validator rejects an orphan node before the solver sees it, but
    # `build` is public and must not raise a KeyError on such a model.
    built = build(load_model(rbd), mode="reliability", ignore_repairs=True)
    assert evaluate.system_probability(built, 10.0) == pytest.approx(
        math.exp(-0.1))
    assert any("not reachable" in w for w in built.warnings)


def test_long_series_chain_does_not_hit_the_recursion_limit():
    blocks = [_block(f"B{i}", params=(1e6, 0.0, 0.0)) for i in range(700)]
    result = analyse_rbd(_series(blocks), "chain", at=10.0, importance=False,
                         ignore_repairs=True)
    assert result["reliability"] == pytest.approx(math.exp(-700 * 10.0 / 1e6))


def test_b100_of_a_series_is_its_earliest_support_end():
    rbd = _series([_block("A", FIXED, (100.0, 0.0, 0.0)),
                   _block("B", FIXED, (300.0, 0.0, 0.0))])
    built = build(load_model(rbd), mode="reliability", ignore_repairs=True)
    assert evaluate.bx_life(built, 100.0) == pytest.approx(100.0)


def test_b100_needs_only_the_finite_element_of_a_series():
    rbd = _series([_block("A", FIXED, (100.0, 0.0, 0.0)), _block("B")])
    built = build(load_model(rbd), mode="reliability", ignore_repairs=True)
    assert evaluate.bx_life(built, 100.0) == pytest.approx(100.0)


def test_b100_of_a_parallel_pair_is_its_latest_support_end():
    rbd = _rbd(
        [_block("A", FIXED, (100.0, 0.0, 0.0)),
         _block("B", FIXED, (300.0, 0.0, 0.0))], [],
        [{"from": "Start", "to": "A"}, {"from": "Start", "to": "B"},
         {"from": "A", "to": "End"}, {"from": "B", "to": "End"}])
    built = build(load_model(rbd), mode="reliability", ignore_repairs=True)
    assert evaluate.bx_life(built, 100.0) == pytest.approx(300.0)


def test_extreme_value_survival_far_below_its_location_is_one():
    assert survival.sf(EXTREME, [1.0, 1000.0], 0.0) == 1.0


def test_gamma_upper_tail_keeps_relative_precision():
    # Erlang(k=5, scale 1) at t=60: Q = e^-60 * sum_{j<5} 60^j/j!
    expected = math.exp(-60.0) * sum(60.0 ** j / math.factorial(j)
                                     for j in range(5))
    got = survival.sf(int(Distribution.ERLANG), [5.0, 1.0], 60.0)
    assert got == pytest.approx(expected, rel=1e-9)


def test_fixed_density_is_zero_away_from_the_atom_and_undefined_on_it():
    built = build(load_model(_series([_block("A", FIXED, (100.0, 0.0, 0.0))])),
                  mode="reliability", ignore_repairs=True)
    assert evaluate.pdf(built, 50.0) == 0.0
    assert evaluate.pdf(built, 100.0) is None
    assert evaluate.hazard(built, 100.0) is None


def test_curve_times_are_ignored_with_a_warning_in_availability_mode():
    rbd = _series([_block("A")])
    result = analyse_rbd(rbd, "m", mode="availability", times=[1.0, 2.0])
    assert "times" not in result
    assert any("curve times" in w for w in result["warnings"])


def test_missing_mission_time_is_an_input_error_not_a_refusal():
    rbd = _series([_block("A")])
    rbd.sections["SYSTEM_DATA"].rows[0]["TerminationType"] = "FailSim"
    with pytest.raises(ValueError, match="mission time"):
        analyse_rbd(rbd, "m", ignore_repairs=True)
