"""Property-based tests on generated models.

Real captures pin the engine against real Raptor, but only for the models we
have captured. These tests generate small random models (series, parallel
and k-of-n layouts; varied failure/repair distributions, sparing, PM,
standby stress and logistics delays) and check properties that must hold
for ANY model:

Some models also get a shared spare pool, a repair/PM resource pool, and
block-to-block or system dependencies -- the paths (pooled sparing, PM
prerequisites, deferral into PM_HOLD, dependent blocks) that the captured
corpus covers least.

* the run completes, or stops at one of the documented `NotImplementedError`
  guards -- nothing else may escape;
* the per-step invariants in `raptor.core.engine.invariants` hold;
* the engine is deterministic: the same model gives the same output twice;
* stepping a run with `StepSession` gives the same log as `run_simulation`.

This checks that the engine is sound, not that it is faithful. When a
property fails, Hypothesis shrinks the model to a minimal one, which is a
ready-made probe to capture against real Raptor.

Examples are derandomized so CI is reproducible; set
`HYPOTHESIS_PROFILE=explore` for a longer, randomized local search.
"""
from __future__ import annotations

import os
from typing import Any

import pytest
from hypothesis import HealthCheck, given, settings
from hypothesis import strategies as st

from raptor.api.build import build_from_spec, k_of_n, parallel, series
from raptor.core.fileio.builder import set_fields, sync_counts
from raptor.core.fileio.rbd import RbdFile
from raptor.core.fileio.rbdlib import make_spare_pool_row, make_resource_row
from raptor.core.engine.invariants import InvariantChecker
from raptor.core.engine.step_session import StepSession
from raptor.core.engine.runner import run_simulation
from raptor.core.rng import Distribution

settings.register_profile(
    "ci", max_examples=100, deadline=None, derandomize=True,
    suppress_health_check=[HealthCheck.too_slow])
settings.register_profile(
    "explore", max_examples=500, deadline=None,
    suppress_health_check=[HealthCheck.too_slow])
settings.load_profile(os.environ.get("HYPOTHESIS_PROFILE", "ci"))


def _dist(mean_lo: float, mean_hi: float) -> st.SearchStrategy[tuple[int, tuple[float, float, float]]]:
    """A (distribution id, (p1, p2, p3)) pair with a mean in [mean_lo, mean_hi]."""
    mean = st.floats(mean_lo, mean_hi, allow_nan=False)
    return st.one_of(
        mean.map(lambda m: (int(Distribution.EXPONENTIAL), (m, 0.0, 0.0))),
        mean.map(lambda m: (int(Distribution.NORMAL), (m, m / 5, 0.0))),
        mean.map(lambda m: (int(Distribution.UNIFORM_REAL), (m / 2, 3 * m / 2, 0.0))),
        st.tuples(st.floats(0.5, 3.0), mean).map(
            lambda sm: (int(Distribution.WEIBULL), (sm[0], sm[1], 0.0))),
        mean.map(lambda m: (int(Distribution.FIXED), (m, 0.0, 0.0))),
    )


@st.composite
def _block_fields(draw: Any) -> dict[str, Any]:
    fdist, fparams = draw(_dist(5.0, 200.0))
    rdist, rparams = draw(_dist(0.5, 20.0))
    fields: dict[str, Any] = {"fdist": fdist, "fparams": fparams,
                              "rdist": rdist, "rparams": rparams}
    sparing = draw(st.sampled_from(["Infinite", "Infinite", "Custom", "None"]))
    fields["sp_type"] = sparing
    if sparing == "Custom":
        fields["stock"] = draw(st.integers(0, 3))
    if draw(st.booleans()):
        fields["uses_pm"] = 1
        fields["pm_freq"] = draw(st.floats(5.0, 100.0))
        fields["pm_dist"] = int(Distribution.FIXED)
        fields["pm_params"] = (draw(st.floats(0.1, 5.0)), 0.0, 0.0)
        fields["pm_refresh"] = 1
        fields["pm_fail_reset"] = draw(st.sampled_from([0, 1]))
    if draw(st.booleans()):
        fields["sb_stress"] = draw(st.floats(0.0, 1.0))
    if draw(st.booleans()):
        fields["pre_dist"] = int(Distribution.FIXED)
        fields["pre_params"] = (draw(st.floats(0.0, 2.0)), 0.0, 0.0)
    return fields


#: Replications per generated model. Rule B2: one, because every property
#: below is a per-model property, not an aggregation. `test_generated_models_
#: run_and_hold_invariants` checked `len(logs) == spec["system"]["num_runs"]`,
#: which at 1 is still a real assertion -- it would catch a runner that
#: dropped or duplicated a run -- and nothing else here reads across runs.
#: `test_step_session_matches_batch_run` already forced `num_runs=1` itself.
REPLICATIONS = 1


@st.composite
def models(draw: Any) -> dict[str, Any]:
    """A friendly-spec dict for `build_from_spec` (a fresh `RbdFile` per build)."""
    n = draw(st.integers(1, 4))
    layout = draw(st.sampled_from(["series", "parallel", "k_of_n"]))
    if layout == "series":
        spec = series(n)
    elif layout == "parallel":
        spec = parallel(n)
    else:
        spec = k_of_n(draw(st.integers(1, n)), n)
    for block in spec["blocks"]:
        block.update(draw(_block_fields()))
    spec["system"] = {
        "run_length": draw(st.floats(20.0, 300.0)),
        "num_runs": REPLICATIONS,
        "termination": "TimeSim",
    }
    # A stochastic family is the *subject* of this module, not an accident:
    # the properties below are meant to hold for whatever distribution
    # Hypothesis picks, which is why `_dist` samples five families rather
    # than using `Fixed`. What B2 asks for instead is an explicit seed, so
    # a generated model is reproducible run-to-run rather than only
    # derandomised-by-Hypothesis. 0 is the runner's default
    # (`stream_seed = int(seed or 0)`).
    # Extras applied after the build (the friendly spec has no pools).
    pooled = draw(st.lists(st.booleans(), min_size=n, max_size=n))
    resourced = draw(st.lists(st.booleans(), min_size=n, max_size=n))
    spec["_extras"] = {
        "spare_pool": draw(st.none() | st.fixed_dictionaries({
            "stock": st.integers(0, 3), "rso": st.sampled_from([0, 1]),
            "arrive": st.floats(5.0, 100.0), "eso": st.sampled_from([0, 1]),
            "emertime": st.floats(0.5, 20.0)})),
        "pooled": pooled,
        "resource": draw(st.none() | st.integers(1, 2)),
        "resourced": resourced,
        "pm_req_defer": draw(st.sampled_from([0, 1])),
        "pm_spare": draw(st.sampled_from([0, 1])),
        # (dependent block index, what it depends on): another block's index,
        # or -2 for the system (sysDepend); None for no dependency.
        "dependency": draw(st.none() | st.tuples(
            st.integers(0, n - 1), st.integers(0, n - 1) | st.just(-2))),
    }
    return spec


def build(spec: dict[str, Any]) -> RbdFile:
    """A fresh `RbdFile` for `spec`, with its extras applied."""
    rbd = build_from_spec({k: v for k, v in spec.items() if k != "_extras"}, auto_layout=False)
    extras = spec.get("_extras") or {}
    blocks = rbd.sections["BLOCK_DATA"].rows
    real_blocks = [row for row in blocks if row.get("ID") != "0"]
    pool = extras.get("spare_pool")
    if pool is not None:
        row = make_spare_pool_row("P1", **pool)
        rbd.sections["SPARE_POOL_DATA"].columns = list(row)
        rbd.sections["SPARE_POOL_DATA"].rows.append(row)
        for block, use in zip(real_blocks, extras["pooled"]):
            if use:
                set_fields(block, SpType="Pooled", SparePoolName="P1",
                           PMSpare=extras["pm_spare"])
    capacity = extras.get("resource")
    if capacity is not None:
        row = make_resource_row("R1", capacity)
        rbd.sections["RESOURCE_DATA"].columns = list(row)
        rbd.sections["RESOURCE_DATA"].rows.append(row)
        for block, use in zip(real_blocks, extras["resourced"]):
            if use:
                set_fields(block, NumRes=1, Resource1Name="R1", **{"#Res1Rep": 1, "#Res1pmRep": 1},
                           PMReqDefer=extras["pm_req_defer"])
    dependency = extras.get("dependency")
    if dependency is not None:
        dependent, target = dependency
        if target != dependent:
            fields: dict[str, Any] = {"DepIdle": 100.0, "DepNothing": 0.0, "DepPM": 0.0, "DepFail": 0.0}
            if target == -2:
                fields.update(DepType="", DepNum=-2)
            else:
                fields.update(DepType="RBDBlock", DepNum=real_blocks[target]["ID"])
            set_fields(real_blocks[dependent], **fields)
    sync_counts(rbd)
    return rbd


#: The stream seed handed to every generated model. Named rather than
#: inherited so that a change to the runner's default cannot silently change
#: which models these properties are checked against.
SEED = 0


def _run(spec: dict[str, Any], **kwargs: Any) -> list[list[str]] | None:
    """The model's event logs, or None if it stopped at a documented guard."""
    try:
        return run_simulation(build(spec), seed=SEED, **kwargs)
    except NotImplementedError:
        return None


@given(models())
def test_generated_models_run_and_hold_invariants(spec: dict[str, Any]) -> None:
    checker = InvariantChecker()
    logs = _run(spec, on_event=checker)
    if logs is not None:
        assert len(logs) == spec["system"]["num_runs"]
        assert checker.steps > 0


@given(models())
def test_generated_models_are_deterministic(spec: dict[str, Any]) -> None:
    assert _run(spec) == _run(spec)


@given(models())
def test_step_session_matches_batch_run(spec: dict[str, Any]) -> None:
    one_run = {**spec, "system": {**spec["system"], "num_runs": REPLICATIONS}}
    batch = _run(one_run)
    if batch is None:
        return
    try:
        session = StepSession(build(one_run))
    except NotImplementedError:
        return
    while session.step():
        pass
    stepped, _report = session.finish_run()
    assert stepped == batch[0]


@pytest.mark.parametrize("first,second", [
    ("tests/golden/Capacity1/input.rbd", "tests/golden/CS_Probe/input.rbd"),
    ("tests/golden/ComplexModel/input.rbd", "tests/golden/Events/input.rbd"),
])
def test_no_state_leaks_between_models(first: str, second: str) -> None:
    """Running another model in the same process first must not change a
    model's output: nothing may survive between simulations."""
    import sys
    from pathlib import Path

    sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "tools"))
    import engine_snapshot as snap

    alone = snap.snapshot_entry(first)
    snap.snapshot_entry(second)
    assert snap.snapshot_entry(first) == alone
