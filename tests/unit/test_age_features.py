"""Age features: duty cycle, start age and restoration factor."""
from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

from raptor.core.engine.block_lifecycle import combined_stress, get_start_cond
from raptor.core.fileio.rbds import load_rbds, parse_rbds, render_rbds, save_rbds
from raptor.core.fileio.typed import block_from_row
from raptor.core.model import RBDBlock
from raptor.core.rng import Distribution, RandomGen, RaptorStream
from tests.behaviour import harness

REPO = Path(__file__).resolve().parents[2]


def _spec(**extra):
    block = harness.block("A", 100.0, 10.0, **extra)
    return {
        "system": {"run_length": 1000.0, "num_runs": 1,
                   "termination": "TimeSim"},
        "blocks": [block],
        "nodes": [{"name": "Start", "type": 1, "n": 0, "out": 1},
                  {"name": "End", "type": 3, "n": 1, "out": 0}],
        "links": [{"from": "Start", "to": "A"}, {"from": "A", "to": "End"}],
    }


def _failures(run) -> list[float]:
    return [event.time for event in run.events
            if event.obj == "Block:A" and event.new == "Failed"]


def test_duty_cycle_scales_combined_stress():
    block = RBDBlock(name="A", duty_cycle=0.5)
    assert combined_stress(block) == pytest.approx(0.5)
    block.duty_cycle = 1.0
    assert combined_stress(block) == pytest.approx(1.0)
    block.uses_phasing = True
    block.phase_value = [0.5]
    block.duty_cycle = 0.5
    assert combined_stress(block, 1) == pytest.approx(0.25)


def test_start_age_uses_the_conditional_first_draw():
    block = RBDBlock(
        name="A", id=1, fail_distro=int(Distribution.FIXED),
        fail_values=[100.0], repair_distro=int(Distribution.NONE),
        pre_dist=int(Distribution.FIXED), pre_params=[0.0],
        post_dist=int(Distribution.FIXED), post_params=[0.0],
        sim_start_type=3, amount_exhausted=0.0, start_age=20.0)
    rng = RandomGen(stream=RaptorStream(seed=12345))
    start = get_start_cond(block, rng, rng)
    assert start.start_condition == "Running"
    assert start.fail_time_left == pytest.approx(80.0)


def test_start_age_zero_matches_the_existing_first_draw():
    block = RBDBlock(
        name="A", id=1, fail_distro=int(Distribution.FIXED),
        fail_values=[100.0], repair_distro=int(Distribution.NONE),
        pre_dist=int(Distribution.FIXED), pre_params=[0.0],
        post_dist=int(Distribution.FIXED), post_params=[0.0],
        sim_start_type=3, amount_exhausted=0.0, start_age=0.0)
    rng = RandomGen(stream=RaptorStream(seed=12345))
    assert get_start_cond(block, rng, rng).fail_time_left == pytest.approx(100.0)


def test_duty_cycle_doubles_the_first_life():
    run = harness.run(_spec(duty_cycle=0.5))
    assert _failures(run)[:3] == pytest.approx([200.0, 410.0, 620.0])


def test_start_age_subtracts_from_the_first_life():
    run = harness.run(_spec(start_age=20.0))
    assert _failures(run)[:3] == pytest.approx([80.0, 190.0, 300.0])


def test_restoration_type_ii_restores_a_fraction_of_total_age():
    run = harness.run(_spec(restoration_factor=0.5, restoration_type=2))
    assert _failures(run)[:4] == pytest.approx([100.0, 160.0, 220.0, 280.0])


def test_restoration_type_i_restores_only_since_the_last_action():
    run = harness.run(_spec(restoration_factor=0.5, restoration_type=1))
    failures = _failures(run)
    assert failures[:3] == pytest.approx([100.0, 160.0, 195.0])


def test_restoration_factor_one_is_as_good_as_new():
    run = harness.run(_spec(restoration_factor=1.0, restoration_type=2))
    assert _failures(run)[:3] == pytest.approx([100.0, 210.0, 320.0])


def test_pm_restoration_delays_the_next_failure():
    block = harness.block(
        "A", 300.0, 10.0, uses_pm=1, pm_freq=200.0,
        pm_duration_dist=19, pm_params=[10.0], pm_refresh=0,
        restoration_factor=0.5, restoration_type=2)
    spec = _spec()
    spec["blocks"] = [block]
    run = harness.run(spec)
    failures = _failures(run)
    assert failures[0] == pytest.approx(410.0)


def test_age_fields_round_trip_through_rbds():
    spec = _spec(duty_cycle=0.4, start_age=12.5,
                 restoration_factor=0.75, restoration_type=2)
    from raptor.api.build import build_from_spec
    rbd = build_from_spec(spec, auto_layout=False)
    text = render_rbds(rbd)
    assert '"duty_cycle": 0.4' in text
    assert '"start_age": 12.5' in text
    assert '"restoration_factor": 0.75' in text
    assert '"restoration_type": 2' in text
    again = parse_rbds(text)
    row = again.sections["BLOCK_DATA"].rows[1]
    assert float(row["_DutyCycle"]) == pytest.approx(0.4)
    assert float(row["_StartAge"]) == pytest.approx(12.5)
    assert float(row["_RestoreFactor"]) == pytest.approx(0.75)
    assert int(float(row["_RestoreType"])) == 2


def test_legacy_rbd_does_not_emit_rbds_only_columns():
    from raptor.api.build import build_from_spec
    from raptor.core.fileio.rbd import render_rbd
    rbd = build_from_spec(
        _spec(duty_cycle=0.4, start_age=12.5, restoration_factor=0.75),
        auto_layout=False)
    text = render_rbd(rbd)
    assert "_DutyCycle" not in text
    assert "_StartAge" not in text
    assert "_RestoreFactor" not in text


def test_legacy_rbd_loads_with_age_defaults():
    from raptor.core.fileio.rbd import load_rbd
    rbd = load_rbd(os.path.join(REPO, "tests", "golden",
                                "Hierarchy1-7", "input.rbd"))
    row = next(r for r in rbd.sections["BLOCK_DATA"].rows
               if str(r.get("ID", "")) not in ("0", ""))
    block = block_from_row(row)
    assert block.duty_cycle == 1.0
    assert block.start_age == 0.0
    assert block.restoration_factor == 0.0
    assert block.restoration_type == 1


def test_older_rbds_loads_and_saves_as_current_version(tmp_path):
    source = os.path.join(REPO, "tests", "fixtures",
                          "rbds_before_experiment", "HospitalPower.rbds")
    rbd = load_rbds(source)
    path = os.path.join(tmp_path, "upgraded.rbds")
    save_rbds(rbd, path)
    doc = json.load(open(path, encoding="utf-8"))
    assert doc["version"] == 3
