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
    assert block.restoration_type == 0


def test_older_rbds_loads_and_saves_as_current_version(tmp_path):
    source = os.path.join(REPO, "tests", "fixtures",
                          "rbds_before_experiment", "HospitalPower.rbds")
    rbd = load_rbds(source)
    path = os.path.join(tmp_path, "upgraded.rbds")
    save_rbds(rbd, path)
    doc = json.load(open(path, encoding="utf-8"))
    assert doc["version"] == 4


# --- conditional life draws (deep-tail ages) --------------------------------

def _rng(seed: int = 12345) -> RandomGen:
    return RandomGen(stream=RaptorStream(seed=seed))


def test_conditional_life_is_exact_deep_in_a_weibull_tail():
    """At an age only ~e^-27 of lives reach, rejection cannot succeed; the
    old code then returned an instant failure (age + 1e-6). The exact draw
    leaves a time to failure on the scale of 1/hazard (~37 h here)."""
    from raptor.core.engine.conditional_life import conditional_life
    left = [conditional_life(_rng(s), int(Distribution.WEIBULL),
                             [3.0, 1000.0, 0.0], 3000.0) - 3000.0
            for s in range(1, 401)]
    mean_left = sum(left) / len(left)
    assert all(x > 0.001 for x in left)
    assert 25.0 < mean_left < 50.0


def test_conditional_life_of_an_exponential_is_memoryless():
    from raptor.core.engine.conditional_life import conditional_life
    left = [conditional_life(_rng(s), int(Distribution.EXPONENTIAL),
                             [100.0, 0.0], 5000.0) - 5000.0
            for s in range(1, 801)]
    assert 85.0 < sum(left) / len(left) < 115.0


def test_conditional_life_by_bisection_for_a_law_without_closed_form():
    from raptor.core.engine.conditional_life import conditional_life
    from raptor.core.analytical.survival import sf
    # Normal(100, 10) at age 160 (6 sigma): rejection fails, bisection runs.
    life = conditional_life(_rng(), int(Distribution.NORMAL), [100.0, 10.0], 160.0)
    assert 160.0 < life < 200.0
    assert sf(int(Distribution.NORMAL), [100.0, 10.0], life) < sf(
        int(Distribution.NORMAL), [100.0, 10.0], 160.0)


def test_conditional_life_past_a_fixed_life_fails_at_once():
    from raptor.core.engine.conditional_life import conditional_life
    life = conditional_life(_rng(), int(Distribution.FIXED), [100.0], 150.0)
    assert life == pytest.approx(150.000001)


def test_start_age_counts_towards_the_restored_age():
    """Fixed 100 h life, start age 20, Type II 0.5. The first failure is at
    t=80 with the block 100 h old, so the repair leaves it 50 h old and the
    next life has 50 h left: 80 + 10 (repair) + 50 = 140, not 150."""
    run = harness.run(_spec(start_age=20.0, restoration_factor=0.5,
                            restoration_type=2))
    assert _failures(run)[:3] == pytest.approx([80.0, 140.0, 200.0])


def test_pm_restoration_does_not_help_a_block_that_does_not_age():
    """A PM that restores age must not lengthen an exponential life: the
    life is memoryless, so failures per run match the no-PM-restore case."""
    block = harness.block(
        "A", 1.0, 1.0, uses_pm=1, pm_freq=50.0,
        pm_duration_dist=19, pm_params=[0.01], pm_refresh=0,
        restoration_factor=0.9, restoration_type=2)
    block.update(harness.dist(Distribution.EXPONENTIAL, 100.0, 0.0))
    spec = _spec()
    spec["blocks"] = [block]
    spec["system"]["run_length"] = 2000.0
    run = harness.run(spec, runs=20, seed=3)
    per_run = sum(1 for r in run.per_run for e in r
                  if e.obj == "Block:A" and e.new == "Failed") / 20
    # ~2000/101 = 19.8 expected; the old age-shift gave about 1/8 of that.
    assert 15.0 < per_run < 25.0


# --- restoration type 0 = off; factor 0 with a type = as bad as old -------

def _weibull_spec(**extra):
    spec = _spec()
    spec["blocks"] = [{"name": "A", "fail_dist": 15,
                       "fail_params": [3.0, 1000.0, 0.0],
                       "repair_dist": 19, "repair_params": [1.0], **extra}]
    spec["system"]["run_length"] = 5000.0
    return spec


def _failures_per_run(run, runs):
    return sum(1 for r in run.per_run for e in r
               if e.obj == "Block:A" and e.new == "Failed") / runs


def test_factor_zero_with_a_type_is_as_bad_as_old():
    """Weibull(3, 1000) over 5,000 h with a 1 h repair. As bad as old is a
    minimal repair: about (5000/1000)^3 = 125 failures, ~117 allowing for
    repair time (an independent Kijima simulation gives 116.9). As good as
    new gives about 5."""
    old = harness.run(_weibull_spec(restoration_factor=0.0,
                                    restoration_type=2), runs=20, seed=5)
    assert 105.0 < _failures_per_run(old, 20) < 128.0
    new = harness.run(_weibull_spec(), runs=20, seed=5)
    assert _failures_per_run(new, 20) < 8.0


def test_restoration_off_is_the_default_and_raptors_repair():
    block = RBDBlock(name="A")
    assert block.restoration_type == 0
    run = harness.run(_spec())
    assert _failures(run)[:3] == pytest.approx([100.0, 210.0, 320.0])


def test_a_factor_without_a_type_means_type_i():
    from raptor.api.build import build_from_spec
    rbd = build_from_spec(_spec(restoration_factor=0.5), auto_layout=False)
    row = next(r for r in rbd.sections["BLOCK_DATA"].rows
               if str(r.get("ID")) not in ("0", ""))
    assert int(float(row["_RestoreType"])) == 1


def test_a_factor_with_restoration_off_is_refused():
    from raptor.core.input_rules import check
    from raptor.api.build import build_from_spec
    rbd = build_from_spec(_spec(restoration_factor=0.5, restoration_type=0),
                          auto_layout=False)
    rules = [v.rule for v in check(rbd)]
    assert any(rule.endswith("age.restore_off") for rule in rules)


def _v3_doc(**block):
    from raptor.api.build import build_from_spec
    doc = json.loads(render_rbds(build_from_spec(_spec(), auto_layout=False)))
    doc["version"] = 3
    entry = doc["elements"]["blocks"][0]
    entry.pop("restoration_type", None)
    entry.pop("restoration_factor", None)
    entry.update(block)
    return json.dumps(doc)


def test_version_3_factor_without_type_reads_as_type_i():
    rbd = parse_rbds(_v3_doc(restoration_factor=0.5))
    row = next(r for r in rbd.sections["BLOCK_DATA"].rows
               if str(r.get("ID")) not in ("0", ""))
    assert block_from_row(row).restoration_type == 1


def test_version_3_factor_zero_reads_as_off_whatever_the_type():
    rbd = parse_rbds(_v3_doc(restoration_factor=0.0, restoration_type=2))
    row = next(r for r in rbd.sections["BLOCK_DATA"].rows
               if str(r.get("ID")) not in ("0", ""))
    assert block_from_row(row).restoration_type == 0
