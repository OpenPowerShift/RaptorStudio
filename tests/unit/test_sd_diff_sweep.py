"""Draw-for-draw `/sd` regression sweep across every golden fixture with a
captured `simDetails.TXT`.

Each fixture has a recorded first-divergence index asserted on every run,
so a scheduler/interrupt/engine change that shifts a fixture's RNG-draw
sequence shows up immediately.

The baselines are a snapshot of where the port currently diverges from
Raptor, not a target. Update a baseline only alongside a deliberate fix,
a change to `sd_diff`'s comparison logic, or a change to the fixture's own
`.rbd`/capture; an unexplained move is a regression.
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO / "tools"))

import sd_diff  # noqa: E402

#: fixture -> num_runs to execute. Matches the fixture's configured
#: `SYSTEM_DATA.NumRuns` unless noted otherwise (Phasing1's capture covers
#: 9 full runs despite NumRuns=10).
_SWEEP_FIXTURES = {
    "AntitheticProbe": 10,
    "Capacity1": 10,
    "Capacity2": 10,
    "Capacity3": 10,
    "Ch07Dependent": 5,
    "MixedDepPerc": 20,
    "Phasing1": 9,
    "Phasing3": 10,
    "PhasingDemo": 10,
    "PieFacility2": 5,
    "PmFailResetOff": 10,
    "PreventMxDefer": 10,
    "SpaceStation": 10,
    "TriggerWakeOrder": 100,
    "WakeOrderDependent": 20,
    "WakeOrderInduced": 20,
}

#: fixture -> current first-divergence draw index (None = matches real
#: through the whole overlap).
_BASELINE_FIRST_DIVERGENCE = {
    "AntitheticProbe": None,
    "Capacity1": None,
    "Capacity2": None,
    "Capacity3": None,
    "Ch07Dependent": None,
    "MixedDepPerc": None,
    "Phasing1": None,
    "PhasingDemo": None,
    "Phasing3": None,
    "PieFacility2": None,
    "PmFailResetOff": None,
    "PreventMxDefer": None,
    "SpaceStation": None,
    "TriggerWakeOrder": None,
    "WakeOrderDependent": None,
    "WakeOrderInduced": None,
}

assert set(_SWEEP_FIXTURES) == set(_BASELINE_FIRST_DIVERGENCE)


@pytest.mark.parametrize("fixture", sorted(_SWEEP_FIXTURES))
def test_sd_diff_matches_recorded_baseline(fixture):
    base = REPO / "tests" / "golden" / fixture
    rbd_path = base / "input.rbd"
    real_path = base / "legacy" / "simDetails.TXT"
    real_draws = sd_diff.load_real_draws(real_path)
    assert len(real_draws) > 20, f"{fixture}'s real capture looks empty/truncated"

    out_path = Path("/tmp") / f"sd_diff_sweep_{fixture}.TXT"
    our_draws = sd_diff.run_ours(rbd_path, _SWEEP_FIXTURES[fixture], out_path)
    idx = sd_diff.first_divergence(real_draws, our_draws)
    expected = _BASELINE_FIRST_DIVERGENCE[fixture]
    assert idx == expected, (
        f"{fixture}'s first RNG-draw divergence moved from index "
        f"{expected!r} to {idx!r} (real={len(real_draws)} draws, "
        f"ours={len(our_draws)} draws, num_runs={_SWEEP_FIXTURES[fixture]}) "
        "-- if this is a deliberate fix, update _BASELINE_FIRST_DIVERGENCE "
        "and explain why in the commit; if not, this is a regression, "
        "same class as this file's own docstring describes"
    )


# =========================================================================
# Second sweep: the remaining golden fixtures with a captured
# `simDetails.TXT`. Kept separate from `_SWEEP_FIXTURES` above so its
# baseline history is not disturbed.
#
# `_NEW_NUM_RUNS`: declared `model.num_runs` for each fixture, not
# inferred from the capture's kickoff landmark (that inference gets
# cycle-based fixtures such as CYCLESIM wrong).
#
# A fixture with no `tests/golden/FIDELITY.toml` `[sd_diff.*]` entry must
# match real exactly (draw-for-draw, equal counts). A fixture with an entry
# is allowed the recorded divergence; this test fails if that shape
# changes.
# =========================================================================
import tomllib  # noqa: E402

_NEW_NUM_RUNS = {
    "BlockDepDownPerc": 20,
    "CS_HigherCold": 10,
    "CS_MidSwitchDemote": 10,
    "CS_NoAutosFirst": 10,
    "CS_Probe": 10,
    "CS_Probe_AProb099": 10,
    "CS_Probe_AProb1": 10,
    "CS_Probe_cold": 10,
    "CS_Probe_cold_noLDT": 10,
    "CS_Probe_hot": 10,
    "CS_Probe_pure": 10,
    "CS_SwitchFail": 10,
    "CUST_ALL": 20,
    "CYCLESIM": 2,
    "CYCLESIM_SS": 2,
    "ColdStandbyProbe2": 10,
    "CustExhaust": 20,
    "DEPDOWN": 20,
    "DepPM": 20,
    "DistributionProbe": 1,
    "DistributionProbe2": 1,
    "FactoryDemo": 5,
    "FreqPMProbe": 5,
    "GDTypeProbe": 5,
    "GDTypeProbe2": 5,
    "HierarchyExample": 10,
    "LadL1Busy": 40,
    "LadL1Ldt": 100,
    "LadL1Parallel": 200,
    "LadL1Series": 100,
    "LadL2Both": 100,
    "LadL2Pool": 100,
    "LadL2Res": 100,
    "LadL4Block": 60,
    "LadL4Node": 60,
    "LadL4Web": 60,
    "LadL5Cold": 60,
    "LadL5ColdBusy": 40,
    "LadL5Full": 60,
    "LadL6Cycles": 60,
    "LadL6Full": 60,
    "LadL6Time": 60,
    "LadL7Cap": 40,
    "LadL7CapMix": 40,
    "LadL7Cost": 60,
    "LadL7Gd": 25,
    "LadL7Hier": 60,
    "LadL7HierFull": 60,
    "LadL7Weak": 60,
    "LadL8A": 60,
    "LadL8BEvents": 30,
    "LadL3Mix": 60,
    "LadL3Periodic": 100,
    "LadL3Trig": 60,
    "MotorDemo": 25,
    "MultiOutLink": 20,
    "NodeStandbyProbe": 5,
    "NumRes2": 20,
    "PMSpareCustom": 20,
    "PM_Prereq": 10,
    "Pearson6Shape": 20,
    "PreventMxDemo": 10,
    "ReleaseProbe44": 1,
    "ReleaseProbe45": 1,
    "ReleaseProbe47": 1,
    "ReleaseProbe48": 1,
    "PoolStockProbe": 10,
    "F2_Pearson5Probe": 20,
    "F2_WeibullProbe": 20,
    "ReleaseProbe49": 1,
    "RepairRecheck": 20,
    "SLOProbe": 5,
    "SimStartProbe": 5,
    "SimStartProbe2": 5,
    "SimStartProbe3": 5,
    "TriggerOrderProbe": 3,
}
assert set(_NEW_NUM_RUNS).isdisjoint(_SWEEP_FIXTURES), (
    "a fixture ended up in both the old 15-fixture sweep and the new "
    "39-fixture one -- pick one, not both"
)

with open(REPO / "tests" / "golden" / "FIDELITY.toml", "rb") as _f:
    _SD_FIDELITY = tomllib.load(_f).get("sd_diff", {})

_ORPHAN_SD_ENTRIES = set(_SD_FIDELITY) - set(_NEW_NUM_RUNS)
assert not _ORPHAN_SD_ENTRIES, (
    f"FIDELITY.toml [sd_diff.*] entries for fixtures this sweep doesn't "
    f"cover: {sorted(_ORPHAN_SD_ENTRIES)}"
)


@pytest.mark.parametrize("fixture", sorted(_NEW_NUM_RUNS))
def test_sd_diff_new_fixtures(fixture):
    import golden_fixtures as gf  # local import: tools/ already on sys.path above

    real_path = gf.simdetails_path(fixture)
    rbd_path = gf.resolve_rbd_path(fixture)
    num_runs = _NEW_NUM_RUNS[fixture]
    entry = _SD_FIDELITY.get(fixture)

    real_draws = sd_diff.load_real_draws(real_path)
    out_path = Path("/tmp") / f"sd_diff_new_{fixture}.TXT"

    if entry and entry.get("crashes"):
        try:
            sd_diff.run_ours(rbd_path, num_runs, out_path)
        except Exception as exc:  # noqa: BLE001 -- the expected outcome
            pytest.xfail(f"{fixture}: {entry['reason']}\n(current: {exc!r})")
        else:
            pytest.fail(
                f"{fixture} no longer crashes, but FIDELITY.toml's "
                f"[sd_diff.{fixture}] still has crashes=true -- remove "
                "that entry and give this fixture a real baseline."
            )

    our_draws = sd_diff.run_ours(rbd_path, num_runs, out_path)
    result = sd_diff.compare_draws(real_draws, our_draws)
    current = {
        "count_real": result["count_real"],
        "count_ours": result["count_ours"],
        "first_divergence": result["first_divergence"],
    }
    is_exact = (
        current["count_real"] == current["count_ours"]
        and current["first_divergence"] is None
    )

    if entry is None:
        assert is_exact, (
            f"{fixture}: no tests/golden/FIDELITY.toml [sd_diff.{fixture}] "
            f"entry, but the draws don't match exactly: {current} "
            f"(num_runs={num_runs}). Either this is a regression (fix it), "
            "or a newly-found gap (add an entry with an evidenced reason, "
            "same shape as the other [sd_diff.*] entries)."
        )
        return

    recorded = {
        "count_real": entry.get("count_real"),
        "count_ours": entry.get("count_ours"),
        "first_divergence": entry.get("first_divergence"),
    }
    if current == recorded:
        pytest.xfail(f"{fixture}: {entry['reason']}")
    elif is_exact:
        pytest.fail(
            f"{fixture} now matches real exactly, but FIDELITY.toml still "
            f"has a [sd_diff.{fixture}] entry ({entry['reason']!r}). "
            "Delete that entry -- this fixture is fixed."
        )
    else:
        pytest.fail(
            f"{fixture}'s draw comparison shape moved from the recorded "
            f"baseline {recorded} to {current} (num_runs={num_runs}) -- "
            "update tests/golden/FIDELITY.toml's [sd_diff.{fixture}] entry "
            "with the new numbers and an explanation, or revert if this "
            "wasn't deliberate."
        )
