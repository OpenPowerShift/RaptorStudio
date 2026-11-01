"""Network construction and exact metrics on repository models."""
from __future__ import annotations

import math
from pathlib import Path

import pytest

from raptor.core.analytical import build, evaluate, survival
from raptor.core.analytical.errors import NotApplicable
from raptor.core.engine.load import load_model
from raptor.core.fileio.model_file import load_model_file
from raptor.core.rng import Distribution

REPO = Path(__file__).resolve().parents[2]
MODELS = REPO / "new_docs" / "models"
GOLDEN = REPO / "tests" / "golden"


def _model(name: str):
    return load_model(load_model_file(MODELS / f"{name}.rbds"))


def test_lantern_series_matches_closed_form():
    built = build(_model("Lantern1"), mode="reliability")
    assert built.node_count == 8
    t = 48.0
    expected = (
        survival.sf(int(Distribution.WEIBULL), [1.5, 60.0, 0.0], t)
        * survival.sf(int(Distribution.EXPONENTIAL), [200.0, 0.0, 0.0], t)
        * survival.sf(int(Distribution.NORMAL), [100.0, 10.0, 0.0], t)
    )
    assert evaluate.system_probability(built, t) == pytest.approx(expected)
    assert evaluate.system_probability(built, 0.0) == 1.0
    assert evaluate.mttf(built) == pytest.approx(43.142601, rel=1e-6)
    assert evaluate.bx_life(built, 10.0) == pytest.approx(9.153885, rel=1e-6)
    assert evaluate.equation(built) == (
        "(R_Battery_Pack * R_Switch * R_LED_Panel)")


def test_drone_k_of_n_matches_closed_form():
    built = build(_model("Drone1"), mode="reliability")
    t = 10.0
    motor = survival.sf(int(Distribution.EXPONENTIAL), [120.0, 0.0, 0.0], t)
    battery = survival.sf(int(Distribution.NORMAL), [60.0, 6.0, 0.0], t)
    controller = survival.sf(int(Distribution.WEIBULL),
                             [1.2, 300.0, 0.0], t)
    at_least_four = sum(
        math.comb(6, k) * motor ** k * (1.0 - motor) ** (6 - k)
        for k in range(4, 7))
    expected = battery * controller * at_least_four
    assert evaluate.system_probability(built, t) == pytest.approx(expected)
    assert evaluate.system_probability(built, 0.0) == 1.0
    assert evaluate.mttf(built) == pytest.approx(47.395925, rel=1e-5)


def test_hierarchy_is_flattened_to_the_same_answer():
    with_hierarchy = build(_model("Van1"), mode="availability")
    flat = build(_model("VanFlat"), mode="availability")
    assert evaluate.availability(with_hierarchy) == pytest.approx(
        evaluate.availability(flat), abs=1e-12)
    assert evaluate.availability(with_hierarchy) == pytest.approx(
        0.9853554704563443, abs=1e-12)


def test_vendo_availability_and_importance():
    built = build(_model("Vendo1"), mode="availability")
    assert evaluate.availability(built) == pytest.approx(
        0.9706853038245, abs=1e-12)
    rows = {row.name: row for row in evaluate.importance(built)}
    assert rows["Keypad"].criticality > rows["Compressor"].criticality
    assert rows["Keypad"].criticality == pytest.approx(0.6623, abs=1e-3)


def test_events_are_constant_variables():
    model = load_model(load_model_file(GOLDEN / "Events" / "input.rbd"))
    built = build(model, mode="reliability")
    assert evaluate.system_probability(built, 0.0) == pytest.approx(
        0.870737366, abs=1e-9)
    assert evaluate.system_probability(built, 100.0) == pytest.approx(
        evaluate.system_probability(built, 0.0))


def test_phases_are_refused():
    with pytest.raises(NotApplicable, match="phased"):
        build(_model("AirlinerMission"), mode="reliability")


def test_repairs_are_refused_in_reliability_mode():
    with pytest.raises(NotApplicable, match="repair distribution"):
        build(_model("CarCare0"), mode="reliability")
    built = build(_model("CarCare0"), mode="reliability",
                  ignore_repairs=True)
    assert 0.0 < evaluate.system_probability(built, 100.0) < 1.0


def test_dependencies_are_refused():
    model = load_model(load_model_file(GOLDEN / "ComplexExample" / "input.rbd"))
    with pytest.raises(NotApplicable, match="dependency"):
        build(model, mode="reliability")


def test_cold_standby_is_refused():
    with pytest.raises(NotApplicable, match="cold standby"):
        build(_model("Bakery1"), mode="availability")


def test_pm_and_spares_are_refused():
    with pytest.raises(NotApplicable, match="preventive maintenance|spare"):
        build(_model("HospitalPower"), mode="availability")


def test_availability_of_non_repairable_series_is_zero():
    built = build(_model("Lantern1"), mode="availability")
    assert evaluate.availability(built) == 0.0


def test_shared_stream_warning_is_reported():
    built = build(_model("Vendo1"), mode="availability")
    assert any("shared random stream" in warning for warning in built.warnings)


def test_non_repairable_availability_explains_the_zero():
    built = build(_model("Lantern1"), mode="availability")
    assert any("non-repairable" in warning for warning in built.warnings)


def _single(**block_extra):
    from raptor.api.build import build_from_spec
    spec = {
        "system": {"run_length": 1000.0, "num_runs": 1,
                   "termination": "TimeSim"},
        "blocks": [{"name": "A", "fail_dist": 4,
                    "fail_params": [100.0, 0.0, 0.0],
                    "repair_dist": 19, "repair_params": [10.0, 0.0, 0.0],
                    **block_extra}],
        "nodes": [{"name": "Start", "type": 1, "n": 0, "out": 1},
                  {"name": "End", "type": 3, "n": 1, "out": 0}],
        "links": [{"from": "Start", "to": "A"}, {"from": "A", "to": "End"}],
    }
    return load_model(build_from_spec(spec, auto_layout=False))


def test_duty_cycle_scales_analytic_reliability():
    built = build(_single(duty_cycle=0.5), mode="reliability", ignore_repairs=True)
    assert evaluate.system_probability(built, 100.0) == pytest.approx(
        math.exp(-0.5))


def test_start_age_uses_conditional_reliability():
    built = build(_single(start_age=50.0), mode="reliability", ignore_repairs=True)
    assert evaluate.system_probability(built, 100.0) == pytest.approx(
        math.exp(-1.0))


def test_duty_cycle_changes_steady_state_availability():
    built = build(_single(duty_cycle=0.5), mode="availability")
    assert evaluate.availability(built) == pytest.approx(200.0 / 210.0)
