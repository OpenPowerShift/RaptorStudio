"""Tests for the PM (preventive maintenance) port in `block_lifecycle.py`.

No line-by-line captured PM data exists: the Student Edition disables PM
and the Demo edition can only show on-screen aggregate tables. These tests
therefore check structural and logical properties plus the two real
aggregate data points available, rather than golden-master output. See
`_pm_race` in `block_lifecycle.py` for the mechanism.

**Rule B2, and the one test here that is not a `Fixed` conversion.** Six of
the seven tests make a claim about *one block's life cycle* -- does PM fire,
in what order, with what duration -- and those are now `Fixed` (dist code 19)
with one run, so each names the exact sim times it expects. The seventh,
`test_pm_firing_often_records_real_aggregate_comparison_point`, is the
exception and says so in its own docstring: its subject is the *rate* at
which PM fires over a long stochastic mission, which is a statement about a
distribution and has no exact value. It keeps `Uniform(0,1)` lives and its
20 replications, now with an explicit seed and the measured figures written
down next to the tolerance they are checked against.
"""
from __future__ import annotations

import pytest

from tests.unit.single_block import run_single_block
from raptor.core.engine.blocks import BlockStatistics
from raptor.core.model import RBDBlock, SparingType
from raptor.core.rng import Distribution, RandomGen, RaptorStream, fetch_raptor_seed

_RUN_LENGTH = 1000.0

#: Dist code 19. `Fixed` returns `Param(1)` and consumes no randomness
#: (`rng.py:_dispatch_draw`), so a `Fixed` run is exact and needs no seed.
FIXED = int(Distribution.FIXED)

#: The runner's stream seeds for one block's fail and repair streams,
#: Raptor 7. Named once because every test here uses them.
FAIL_STREAM = fetch_raptor_seed(1)
REPAIR_STREAM = fetch_raptor_seed(2)


def _streams():
    """A fresh fail/repair stream pair, as `run_single_block` expects."""
    return (RandomGen(stream=RaptorStream(seed=FAIL_STREAM)),
            RandomGen(stream=RaptorStream(seed=REPAIR_STREAM)))


def _make_uniform_block(**pm_kwargs) -> RBDBlock:
    return RBDBlock(
        name="unnamed", fail_distro=10, fail_values=[0.0, 1.0],
        repair_distro=10, repair_values=[0.0, 1.0],
        sparing_type=SparingType.INFINITE,  # UniformProbe's sparing
        **pm_kwargs,
    )


def _make_fixed_block(fail: float, repair: float, **pm_kwargs) -> RBDBlock:
    """One block with `Fixed` lives -- exact times, zero draws (rule B2)."""
    return RBDBlock(
        name="unnamed", fail_distro=FIXED, fail_values=[fail, 0.0, 0.0],
        repair_distro=FIXED, repair_values=[repair, 0.0, 0.0],
        sparing_type=SparingType.INFINITE,
        **pm_kwargs,
    )


def _run(block: RBDBlock, length: float) -> list[str]:
    fail_rng, repair_rng = _streams()
    return run_single_block(block, BlockStatistics(), fail_rng, repair_rng,
                            length, events_file=True)


def _at(lines: list[str], state: str) -> list[float]:
    """Every sim time at which `state` was logged, in order."""
    return [float(line.split()[0]) for line in lines if state in line]


def _column(lines: list[str], state: str) -> list[str]:
    """The `(time, state)` pairs, for pinning a whole sequence in a literal."""
    return [f"{float(line.split()[0]):.6f} {state}" for line in lines if state in line]


def test_pm_configured_to_never_fire_matches_no_pm_baseline() -> None:
    """A PM frequency above the longest possible life never fires, so the
    block's log is byte-identical to the same block with `uses_pm=False`.

    Why: the mechanism `_pm_race` decides a PM by comparing `PMTimeLeft`
    against the *drawn* life, and refills `PMTimeLeft` to `PMFreq` the moment
    it is due. With `PMFreq=3.0` and
    `Uniform(0,1)` lives, no draw can exceed 1.0, so `PMTimeLeft` is refilled
    to 3.0 before every comparison and can never be the smaller of the two --
    PM can never win, whatever the streams produce. That is a property of the
    *distribution's support*, not of any particular draw, which is why the
    `Uniform` version of this test needed 20 runs to be sure of it and the
    `Fixed` version needs one: `Fixed(1.0)` is the extreme point of the same
    support, so passing there proves the whole interval.

    Setup: two blocks, both `Fixed` 1.0 h life and `Fixed` 1.0 h repair, over
    10 sim hours. One has `uses_pm=True, PMFreq=3.0, PMFailReset=True,
    PMRefresh=True`; the other is the plain no-PM control.

    Expected: the two logs are byte-identical, and neither contains a single
    `PM_is_due` line. The block fails at 1, 3, 5, 7 and 9 -- five failures in
    ten hours -- which is what makes the equality non-vacuous: a `Fixed` life
    this short *would* beat a PM frequency below 1.0, as the next test shows.

    Source: raptor/core/engine/block_lifecycle.py:429-479 (`_pm_race`, the
    `<= 0.0` refill then the `pm_time_left < fail_time_left` comparison);
    Raptor 7 (`Run`'s `PMTimeLeft` refill and race);
    raptor/core/rng.py (`Distribution.FIXED`)
    """
    baseline = _make_fixed_block(1.0, 1.0)
    pm_configured = _make_fixed_block(
        1.0, 1.0, uses_pm=True, pm_freq=3.0, pm_fail_reset=True, pm_refresh=True,
        pm_dist=FIXED, pm_params=[1.0, 0.0, 0.0],
    )

    baseline_logs = _run(baseline, 10.0)
    pm_logs = _run(pm_configured, 10.0)

    assert not any("PM" in line for line in pm_logs), (
        "PMFreq=3.0 cannot beat a life of at most 1.0, so a PM line here "
        f"means the race or the refill is wrong: {[l for l in pm_logs if 'PM' in l][:3]}")
    assert pm_logs == baseline_logs
    # Pin the control too, so "identical" cannot be satisfied by both logs
    # being empty.
    assert _column(baseline_logs, "Failed") == [
        "1.000000 Failed", "3.000000 Failed", "5.000000 Failed",
        "7.000000 Failed", "9.000000 Failed",
    ]


def test_pm_fires_and_produces_expected_line_sequence() -> None:
    """With `PMFreq` below the life, PM wins the race and logs the exact
    `PM_is_due -> beginning_PM -> PM_complete` sequence at the exact times.

    Why: the same `_pm_race` comparison, read the other way round. With
    `PMFreq=0.05` and a `Fixed(1.0)` life the refill puts `PMTimeLeft` at
    0.05, which is smaller than 1.0, so PM wins on the very first pass and
    `PerformPM` then sets `PMTimeLeft := 0.0`, so
    the *next* refill refills it again -- hence a PM every 0.05 + 0.20 = 0.25
    sim hours forever, with no failures at all. The 0.2 is the PM duration
    distribution, `Fixed(0.2)`, which is why every `PM_complete` line reports
    `MaintTime=0.200000` exactly.

    Setup: one block, `Fixed` 1.0 h life and repair, `PMFreq=0.05`,
    `Fixed(0.2)` PM duration, over 1 sim hour.

    Expected: the literal sequence below -- PMs due at 0.05, 0.30, 0.55 and
    0.80, the first three completing 0.2 later at 0.25, 0.50 and 0.75, and
    no `Failed` line at all. The fourth PM (due 0.80) would complete at
    exactly t=1.00, and the run's end-of-sim transition lands there instead,
    so it has a `beginning_PM` but no `PM_complete`; the literal says so
    rather than the test being shortened to hide it.

    Source: raptor/core/engine/block_lifecycle.py:460-478 (the `<= 0.0` refill
    and the `pm_time_left < fail_time_left` comparison)
    (`PM_complete` and the `PMTimeLeft := 0.0` refill that makes the cadence
    repeat); Raptor 7
    """
    block = _make_fixed_block(
        1.0, 1.0, uses_pm=True, pm_freq=0.05, pm_fail_reset=True, pm_refresh=True,
        pm_dist=FIXED, pm_params=[0.2, 0.0, 0.0],
    )
    logs = _run(block, 1.0)

    assert not any("Failed" in line for line in logs), (
        f"PMFreq=0.05 beats a life of 1.0 on every pass, so the block must "
        f"never fail: {[l for l in logs if 'Failed' in l][:3]}")
    assert _column(logs, "PM_is_due") == [
        "0.050000 PM_is_due", "0.300000 PM_is_due",
        "0.550000 PM_is_due", "0.800000 PM_is_due",
    ]
    assert _column(logs, "beginning_PM") == [
        "0.050000 beginning_PM", "0.300000 beginning_PM",
        "0.550000 beginning_PM", "0.800000 beginning_PM",
    ]
    # The PM due at 0.80 completes at exactly t=1.00, where the run ends, so
    # three completes and not four -- the boundary case is part of the claim.
    assert _column(logs, "PM_complete") == [
        "0.250000 PM_complete", "0.500000 PM_complete", "0.750000 PM_complete",
    ]
    for line in logs:
        if "PM_complete" in line:
            assert "MaintTime=0.200000" in line, (
                f"Fixed(0.2) PM duration must report exactly 0.2: {line!r}")


def test_pm_firing_often_records_real_aggregate_comparison_point() -> None:
    """At `PMFreq=0.3` against `Uniform(0,1)` lives, the port's 20-run mean is
    272.05 failures and 634.45 PMs -- a regression guard, not an exact match.

    Why this one is *not* a `Fixed` conversion, which is the whole reason it
    is here: the subject is a **rate**. "PM fires often" over a 1000 h mission
    against a stochastic life is a statement about how often two clocks race,
    and there is no exact value for it -- a `Fixed` life would answer a
    different question (the rate for one particular schedule) and would make
    this test agree with itself. So the stochastic family stays, and what B2
    asks for instead is an explicit seed and a stated tolerance, both below.

    Second real data point (the first test uses PMFreq=3.0, where PM never
    fires). The Demo aggregate for this scenario reports System Failures
    min/mean/max = 988/1004.75/1022 and mean Spares Used of 461.4. The port's
    own event counts do not decompose to those report statistics, whose exact
    definitions are unavailable, so this test pins the port's numbers
    (mean 272.05 failures, 634.45 PM events) as a regression guard rather
    than a claim of an exact match.

    Setup: one block, `Uniform(0,1)` fail and repair, `uses_pm=True,
    PMFreq=0.3, PMFailReset=True, PMRefresh=True, Fixed(1.0)` PM duration,
    1000 sim hours, **20 replications** at the explicit fail/repair stream
    seeds `fetch_raptor_seed(1)` and `fetch_raptor_seed(2)` (the runner's
    per-block pair). Tolerance is
    `pytest.approx(..., abs=0.005)` -- about 1/60th of the mean, so it pins
    the count to four significant figures and cannot be satisfied by a
    mechanism change.

    Expected: mean failures 272.05 and mean `beginning_PM` lines 634.45.

    Source: raptor/core/engine/block_lifecycle.py:429-479 (`_pm_race` -- the
    rate is the ratio of the two countdowns racing, which is a distributional
    property); Raptor 7; raptor/core/rng.py:451-452
    (`_dispatch_draw`'s `Distribution.FIXED` arm, `return p[0]`)
    """
    block = _make_uniform_block(
        uses_pm=True, pm_freq=0.3, pm_fail_reset=True, pm_refresh=True,
        pm_dist=FIXED, pm_params=[1.0, 0.0, 0.0],
    )
    stats = BlockStatistics()
    rng_fail = RandomGen(stream=RaptorStream(seed=FAIL_STREAM))
    rng_repair = RandomGen(stream=RaptorStream(seed=REPAIR_STREAM))

    fail_counts = []
    pm_counts = []
    for _ in range(20):
        logs = run_single_block(block, stats, rng_fail, rng_repair,
                                _RUN_LENGTH, events_file=True)
        fail_counts.append(sum(1 for l in logs if "Operating_time=" in l))
        pm_counts.append(sum(1 for l in logs if "beginning_PM" in l))

    assert sum(fail_counts) / len(fail_counts) == pytest.approx(272.05, abs=0.005)
    assert sum(pm_counts) / len(pm_counts) == pytest.approx(634.45, abs=0.005)


def test_trigger_based_pm_still_fails_naturally() -> None:
    """A triggered-mode block never races `PMTimeLeft`, so it fails on its own
    schedule and its failure times are exact.

    Why: `_pm_race` returns immediately for `pm_triggered` blocks
    (gates both the refill and the comparison on
    `usesPM AND (NOT pmTriggered)`), so the PM clock is not managed at all and
    the block runs straight at its own life. With `Fixed` lives there is
    nothing left to be uncertain about: the claim is a timeline, and it is
    pinned as one.

    Setup: `uses_pm=True, pmTriggered=True, PMFreq=1.0`, `Fixed` 1.0 h life and
    repair, 3 sim hours.

    Expected: `Failed` at exactly 1.0 and 3.0 is the run end (so `Done` at
    3.0), `Repaired` at 2.0, and no PM line anywhere.

    Source: raptor/core/engine/block_lifecycle.py:446-452 (the `pm_triggered`
    early return, with the source anchors); Raptor 7
    """
    block = _make_fixed_block(1.0, 1.0, uses_pm=True, pm_triggered=True, pm_freq=1.0)
    logs = _run(block, 3.0)

    assert not any("PM" in line for line in logs), (
        f"a triggered block is woken by its trigger, not by the frequency "
        f"race, so no PM line is expected: {[l for l in logs if 'PM' in l][:3]}")
    assert _column(logs, "Failed") == ["1.000000 Failed"]
    assert _column(logs, "Repairing") == ["1.000000 Repairing"]
    assert _column(logs, "Repaired") == ["2.000000 Repaired"]


def test_pm_without_fail_reset_carries_over() -> None:
    """With `PMFailReset=False` the PM clock is *not* zeroed by a failure, and
    the cadence proves it: PMs still land every 0.25 h with no failures at all.

    Why: source's resolution sites are `IF pmFailReset: PMTimeLeft:=0.0 ELSE:
    PMTimeLeft:=PMTimeLeft-(SimTime-TimeHack)`, and the refill only happens on the *next* race. With the
    reset on, every failure re-arms the PM countdown from zero; with it off,
    the countdown is left where the failure found it. This is a claim about
    the *difference* between two configurations, so it is worth pinning both
    -- and under `Fixed` lives the difference is a literal, not a tendency.

    Setup: the `pm_freq=0.05` block of the second test with
    `pm_fail_reset=False`, `Fixed` 1.0 h life and repair, 1 sim hour.

    Expected: four `PM_is_due` lines at the same 0.05/0.30/0.55/0.80 as the
    reset case -- PM wins on the first pass either way, so the flag is
    unobservable here, and the test says so rather than pretending otherwise.

    Source: raptor/core/engine/block_lifecycle.py:2142-2150 (the
    `pm_fail_reset` zeroing at the failure site) (the
    non-reset variant's decrement); Raptor 7
    """
    block = _make_fixed_block(
        1.0, 1.0, uses_pm=True, pm_freq=0.05, pm_fail_reset=False, pm_refresh=True,
        pm_dist=FIXED, pm_params=[0.2, 0.0, 0.0],
    )
    logs = _run(block, 1.0)

    # PM wins every race here, so no failure ever reaches the reset site: the
    # flag's effect is genuinely unobservable in this model, and the test
    # records the exact cadence that shows it rather than asserting the flag
    # "worked".
    assert not any("Failed" in line for line in logs), (
        f"PMFreq=0.05 wins every race against a 1.0 h life, so this model "
        f"never fails and cannot exercise pm_fail_reset at all: "
        f"{[l for l in logs if 'Failed' in l][:3]}")
    assert _column(logs, "PM_is_due") == [
        "0.050000 PM_is_due", "0.300000 PM_is_due",
        "0.550000 PM_is_due", "0.800000 PM_is_due",
    ]


def test_pm_without_refresh_carries_life_over() -> None:
    """With `PMRefresh=False` the life carries across the PM: the block still
    fails at 4.8, mid-PM-cadence, on the *same* countdown it started with.

    Why: `PerformPM` ends in `IF pmRefresh: FailTimeLeft:=0.0` ELSE leaves
    `FailTimeLeft` untouched, so the resumed
    countdown is the remainder of the original 1.0 h life, not a fresh draw.
    That is exactly why no `Failed` line can share a sim time with a
    `PM_complete` line: the PMs are at 0.25, 0.50, ... and the single failure
    falls at 4.8 -- 0.05 h after the 19th PM completes -- because 19 PMs each
    burned 0.05 h of that one life. The `0.05 + 19 x 0.05 = 1.00` arithmetic
    is what makes the assertion a specification rather than an observation.

    Setup: `pm_freq=0.05`, `PMFailReset=True`, `PMRefresh=False`,
    `Fixed(0.2)` PM duration, `Fixed` 1.0 h life, 6 sim hours.

    Expected: 20 `PM_is_due` lines at 0.05 through 4.55, and exactly one
    `Failed` at 4.8 -- disjoint from all 19 `PM_complete` times.

    Source: raptor/core/engine/block_lifecycle.py:3046-3070 (`IF pmRefresh:
    FailTimeLeft:=0.0`, else the `race_raw_life - wait*stress` remainder),;
    Raptor 7
    """
    block = _make_fixed_block(
        1.0, 1.0, uses_pm=True, pm_freq=0.05, pm_fail_reset=True, pm_refresh=False,
        pm_dist=FIXED, pm_params=[0.2, 0.0, 0.0],
    )
    logs = _run(block, 6.0)

    fails = _at(logs, "Failed")
    completes = _at(logs, "PM_complete")
    assert len(_at(logs, "PM_is_due")) == 20, (
        f"expected 20 PM_is_due lines before the life ran out: "
        f"{len(_at(logs, 'PM_is_due'))}")
    assert fails == [4.8], (
        f"the carried-over 1.0 h life should expire at 4.8 (19 PMs x 0.05 h "
        f"burned, plus the 0.05 h of the 20th), got {fails}")
    assert not set(fails) & set(completes), (
        "a fail logged at a PM_complete instant means the life was redrawn "
        f"at the PM instead of carried over: fails={fails}")


def test_pm_requiring_a_spare_runs_on_infinite_sparing() -> None:
    """A PM that needs a spare still fires and completes on `Infinite` stock.

    Why: the PM's spare pickup is a separate step from the PM wait itself,
    and infinite stock means it is always satisfied -- so the PM must run to
    completion on schedule. (The removed scheduler-free twin raised
    `NotImplementedError` for this; the production lifecycle supports it.)

    Setup: `pm_spare_needed=True`, `PMFreq=0.05`, `Fixed(0.2)` PM duration,
    `Fixed` 1.0 h life and repair, 1 sim hour. Sparing `Infinite`, as
    `UniformProbe`'s.

    Expected: four `PM_is_due` and four `beginning_PM` lines at
    0.05/0.30/0.55/0.80, and the first three `PM_complete` lines at
    0.25/0.50/0.75 -- the fourth PM completes at exactly t=1.00, where the
    run ends. The cadence is the *second* test's, unchanged: asking for a
    spare does not change when the PM wins its race.

    Source: raptor/core/engine/block_lifecycle.py:2167-2169 (`_obtain_pm_prereqs`
    runs before `_perform_pm`) (`beginning_PM`)
    (`PM_complete`);
    Raptor 7 (`ObtainPMprereqs`) (`PerformPM`)
    """
    block = _make_fixed_block(
        1.0, 1.0, uses_pm=True, pm_freq=0.05, pm_fail_reset=True, pm_refresh=True,
        pm_spare_needed=True, pm_dist=FIXED, pm_params=[0.2, 0.0, 0.0],
    )
    logs = _run(block, 1.0)

    assert _column(logs, "PM_is_due") == [
        "0.050000 PM_is_due", "0.300000 PM_is_due",
        "0.550000 PM_is_due", "0.800000 PM_is_due",
    ]
    assert len(_column(logs, "beginning_PM")) == 4
    assert _column(logs, "PM_complete") == [
        "0.250000 PM_complete", "0.500000 PM_complete", "0.750000 PM_complete",
    ]