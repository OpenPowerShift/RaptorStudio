"""Growth/decay stress: the three `GDstress` formulas, the clamp on both
sides of 1, and the uninitialised-GDstress fall-through.

`combined_stress` (`raptor/core/engine/block_lifecycle.py:125-194`) computes
one number per turn of `Run` -- source's own
`CombinedStress:=PhaseStress*StandbyStress/GDstress` -- and the block's *wait* is
`FailTimeLeft / CombinedStress`, so **the only observable GD stress
leaves is the length of that wait, and therefore the failure times in the
event log**. The values themselves are locals in `Run`: "the stress values are
local to `Run`, used only to compute that call's wait duration; they are not
persisted to any object field". So every test here asserts on
failure times, and the module docstring's table is what a reviewer checks
those times against.

**One model shape, five parameter rows, so the formulas are compared rather
than merely visited.** Every test builds one block (`Fixed` life 10,
`Fixed` repair 1) between a Start and an End node, and the run length is
cut to end the run while the block is still Running. Only `GDType`, `GDRate`
and `GDLimit` move. With `GDRate = 0.5` fixed, the five rows are:

    ================  ========  ===========  ================  ===========================
    row               GDType    GDLimit      GDFactorLim       waits, for c = 0,1,2,3
    ================  ========  ===========  ================  ===========================
    linear            1         1000.0       100.0             10, 15, 20, 25
    geometric         2         0.0          0.0               10, 5, 2.5, 1.25
    saturating        3         20.0         2.0               10, 13.93, 16.32, 17.77
    clamp down        1         12.0         1.2               10, 12, 12, 12  (clamped)
    clamp up          2         4.0          0.4               10, 5, 4, 4    (clamped)
    ================  ========  ===========  ================  ===========================

**The counter is what makes the wait move**, and it only moves when the block
actually fails: `_record_failure` does the ported
`INC(FailureCounter)` (`block_lifecycle.py:2138`),
so a block with a long life and a short run shows a flat row and pins
nothing. Every fixture here fails two or three times inside its run -- enough
for the stress to move twice, which is what both clamp arms need.

**`GDLimit` is written on every row, and that is not tidiness -- it is
load-bearing.** `gd_factor_lim` is `gd_limit / fail_mean(block)`
(`block_lifecycle.py:151`), and the *template row every build starts from*
carries `GDLimit = 1.0` (`rbdlib.py:198-203`, "GDLimit=1.0 in the real
template"), so an omitted limit is `1.0 / life` -- a real clamp, in force
from the first `GDstress` that crosses it. Measured while building these
tests: a `gd_type=2` block with `GDRate=0.5`, a 10-hour life and **no**
`GDLimit` written decays `1, 0.5, 0.25, 0.125` and then sticks at `0.1`
from the fifth life onward, because `1.0/10 = 0.1` is the clamp and
`0.5**4 = 0.0625` has fallen under it -- the run logs `CombStress` 2.0, 4.0,
8.0, 10.0, 10.0. That is correct behaviour, and it is why every row below
names its own limit: the geometric row below needs a limit of `0.0` (or the
run kept short enough never to reach the fourth life), and the clamp rows
need one they intend to bind. A fixture that left the column alone would be
claiming whichever of the two happened to be true for its run length.

**The block must fail repeatedly for the clamp to be reachable at all.**
`GDstress` is a function of the failure counter, and the clamp is a function
of `GDstress`, so a single failure leaves both unexercised.

**Every distribution is `Fixed` (code 19), so every run is exact and consumes
no randomness**: each test asserts `Run.draws == 0` (rule B2). One block and
two nodes apiece, one replication (rule B4).

**The last two rows need a `gd_type` source does not handle, and how one
gets into a runnable model is worth stating rather than discovering.**
`raptor/api/validate.py`'s `_check_gd_types` reports an
out-of-range `GDType` as an error -- the GUI's preflight runs it
(`raptor/gui/ui/preflight.py:44`) -- but `build_from_spec` raises `BuildError`
only on the *structural* validator's findings (`raptor/api/build.py:956-959`,
whose `validate` is `raptor/core/fileio/builder.py`'s), so a
`GDType` of 4 builds, loads and runs. The engine's own guard is therefore the
only thing between such a model and a simulation, and it is the port's
statement that source's chain leaves `GDstress` uninitialised for any
`GDType` it does not handle (`block_lifecycle.py:158-176`). The two tests at
the end pin both halves of that guard: outside the `(0, 1)` window the
outcome depends on stack garbage and it raises; inside it the clamp turns the
uninitialised value into a fixed point and the model runs.
"""
from __future__ import annotations

from typing import Any

import pytest

from .harness import FIXED, assert_timeline, run


def _gd_model(life: float, repair: float, run_length: float,
              **gd: Any) -> dict[str, Any]:
    """`Start -> A -> End`, one block with `Fixed` life and repair, plus the
    GD columns the test names.

    The topology is the least `build_from_spec` accepts: a Start node, an End
    node and the two links that wire the block between them (see
    `spine._s1`'s note on why a lone block cannot be loaded). `gd_type`,
    `gd_rate` and `gd_limit` are the spec-side names for the `GDType`,
    `GDRate` and `GDLimit` columns (`raptor/api/build.py:82-84`).

    `build_from_spec` applies a block dict as a *patch* over the row it
    creates, so a column this helper does not name keeps the template row's
    value -- which for `GDLimit` is `1.0`, not `0.0`. Every fixture that
    cares about the clamp therefore names `gd_limit` explicitly.
    """
    block: dict[str, Any] = {
        "name": "A",
        "fail_dist": FIXED, "fail_params": [life],
        "repair_dist": FIXED, "repair_params": [repair],
    }
    block.update(gd)
    return {
        "system": {"run_length": run_length, "num_runs": 1,
                   "termination": "TimeSim"},
        "blocks": [block],
        "nodes": [{"name": "Start", "type": 1, "n": 0, "out": 1},
                  {"name": "End", "type": 3, "n": 1, "out": 0}],
        "links": [{"from": "Start", "to": "A"}, {"from": "A", "to": "End"}],
    }


def test_gd_type_1_stretches_each_life_by_half_the_rate_per_failure() -> None:
    """`GDType 1` -- linear growth: each successive wait is half a `GDRate`
    longer than the last.

    Why: `GDstress := GDRate*FailureCounter + 1`
    (`block_lifecycle.py:153`, the port of source's linear IF-chain arm in
    `Run`), and the wait is `FailTimeLeft /
    CombinedStress` with `CombinedStress = 1/GDstress` for an
    unphased, non-standby block. So the wait is `life * (1 + rate*counter)`.

    Setup: one block, `Fixed` 10-hour life, `Fixed` 1-hour repair,
    `gd_type=1`, `gd_rate=0.5`, `gd_limit=1000.0` (so `gd_factor_lim` is
    `1000/10 = 100` and the clamp is nowhere near binding). A 60-hour run.

    Expected: waits of 10, 15 and 20 hours -- the block fails at t=10, then
    at `11+15 = 26`, then at `27+20 = 47`, and is still Running when the run
    ends at 60, where it goes `Done`. `GDRate=0.5` is what makes the steps
    10, 15, 20 rather than 10, 20, 30, and the run logs
    `CombStress=0.666667`, `0.500000`, `0.400000` for them.

    Source: Raptor 7 (`Run`'s `GDstress` / `CombinedStress`),
    Raptor 7 (the division), block_lifecycle.py:147-175 (the GD
    block) (the division by `gd_stress`),
    Raptor 7 (`INC(FailureCounter)`, ported at
    block_lifecycle.py:2138); docs/engine-design/block-lifecycle.adoc
    ("compute stress; draw a life ... `_fail_wait(): yield remaining life`").
    """
    result = run(_gd_model(10.0, 1.0, 60.0,
                           gd_type=1, gd_rate=0.5, gd_limit=1000.0))

    assert result.draws == 0, (
        f"every distribution is Fixed, so this run must draw nothing; it "
        f"drew {result.draws}")

    assert_timeline(result, [
        (0.0, 'Block', 'A', '--', 'Good'),
        (10.0, 'Block', 'A', 'Good', 'Failed'),
        (10.0, 'Block', 'A', 'Failed', 'Repairing'),
        (10.0, 'Node', 'end', '--', 'Bad'),
        (10.0, 'System', 'State_change', '--', 'Red'),
        (11.0, 'Block', 'A', 'Repairing', 'Repaired'),
        (11.0, 'Block', 'A', 'Repaired', 'Good'),
        (11.0, 'Node', 'end', 'Bad', 'Good'),
        (11.0, 'Block', 'A', 'Good', 'StessLevel'),
        (11.0, 'System', 'State_change', 'Red', 'Green'),
        (26.0, 'Block', 'A', 'StessLevel', 'Failed'),
        (26.0, 'Block', 'A', 'Failed', 'Repairing'),
        (26.0, 'Node', 'end', 'Good', 'Bad'),
        (26.0, 'System', 'State_change', 'Green', 'Red'),
        (27.0, 'Block', 'A', 'Repairing', 'Repaired'),
        (27.0, 'Block', 'A', 'Repaired', 'Good'),
        (27.0, 'Node', 'end', 'Bad', 'Good'),
        (27.0, 'Block', 'A', 'Good', 'StessLevel'),
        (27.0, 'System', 'State_change', 'Red', 'Green'),
        (47.0, 'Block', 'A', 'StessLevel', 'Failed'),
        (47.0, 'Block', 'A', 'Failed', 'Repairing'),
        (47.0, 'Node', 'end', 'Good', 'Bad'),
        (47.0, 'System', 'State_change', 'Green', 'Red'),
        (48.0, 'Block', 'A', 'Repairing', 'Repaired'),
        (48.0, 'Block', 'A', 'Repaired', 'Good'),
        (48.0, 'Node', 'end', 'Bad', 'Good'),
        (48.0, 'Block', 'A', 'Good', 'StessLevel'),
        (48.0, 'System', 'State_change', 'Red', 'Green'),
        (60.0, 'Block', 'A', 'StessLevel', 'Done'),
        (60.0, 'System', 'Phase_change', '--', 'Mission_failure'),
        (60.0, 'System', 'Simulation_Terminate', '--', '--'),
    ], where="GDType 1 -- linear growth")


def test_gd_type_2_shrinks_each_life_by_the_same_factor_per_failure() -> None:
    """`GDType 2` -- geometric decay: each successive wait is `GDRate` times
    the last, so the failures crowd together.

    Why: `GDstress := GDRate**FailureCounter`
    (`block_lifecycle.py:155`, source's geometric arm). This is the row that must differ from the
    linear one: with the same `GDRate = 0.5` and the same 10-hour life the
    linear row's waits are 10, 15, 20 while this row's are 10, 5, 2.5.

    Setup: one block, `Fixed` 10-hour life, `Fixed` 1-hour repair,
    `gd_type=2`, `gd_rate=0.5`, `gd_limit=0.0` -- so `gd_factor_lim` is
    `0.0/10 = 0.0`, and a limit of zero can never clamp a positive
    `GDstress` (the clamp needs `gd_stress < gd_factor_lim`, and
    `0.5**c > 0` for every counter). A 20-hour run.

    Expected: waits of 10, 5 and 2.5 hours -- failures at t=10, `11+5 = 16`
    and `17+2.5 = 19.5`, with the run ending at 20 while the block is
    `Repairing`, which logs `Repairing -> Done` directly.

    Source: Raptor 7, block_lifecycle.py:147-175 (the GD
    block for this arm) (both clamp directions),;
    docs/engine-design/block-lifecycle.adoc.
    """
    result = run(_gd_model(10.0, 1.0, 20.0,
                           gd_type=2, gd_rate=0.5, gd_limit=0.0))

    assert result.draws == 0, (
        f"every distribution is Fixed, so this run must draw nothing; it "
        f"drew {result.draws}")

    assert_timeline(result, [
        (0.0, 'Block', 'A', '--', 'Good'),
        (10.0, 'Block', 'A', 'Good', 'Failed'),
        (10.0, 'Block', 'A', 'Failed', 'Repairing'),
        (10.0, 'Node', 'end', '--', 'Bad'),
        (10.0, 'System', 'State_change', '--', 'Red'),
        (11.0, 'Block', 'A', 'Repairing', 'Repaired'),
        (11.0, 'Block', 'A', 'Repaired', 'Good'),
        (11.0, 'Node', 'end', 'Bad', 'Good'),
        (11.0, 'Block', 'A', 'Good', 'StessLevel'),
        (11.0, 'System', 'State_change', 'Red', 'Green'),
        (16.0, 'Block', 'A', 'StessLevel', 'Failed'),
        (16.0, 'Block', 'A', 'Failed', 'Repairing'),
        (16.0, 'Node', 'end', 'Good', 'Bad'),
        (16.0, 'System', 'State_change', 'Green', 'Red'),
        (17.0, 'Block', 'A', 'Repairing', 'Repaired'),
        (17.0, 'Block', 'A', 'Repaired', 'Good'),
        (17.0, 'Node', 'end', 'Bad', 'Good'),
        (17.0, 'Block', 'A', 'Good', 'StessLevel'),
        (17.0, 'System', 'State_change', 'Red', 'Green'),
        (19.5, 'Block', 'A', 'StessLevel', 'Failed'),
        (19.5, 'Block', 'A', 'Failed', 'Repairing'),
        (19.5, 'Node', 'end', 'Good', 'Bad'),
        (19.5, 'System', 'State_change', 'Green', 'Red'),
        (20.0, 'Block', 'A', 'Repairing', 'Done'),
        (20.0, 'System', 'Phase_change', '--', 'Mission_failure'),
        (20.0, 'System', 'Simulation_Terminate', '--', '--'),
    ], where="GDType 2 -- geometric decay")


def test_gd_type_3_eases_each_life_towards_the_gd_limit_asymptote() -> None:
    """`GDType 3` -- saturating: the wait lengthens by a shrinking step
    towards `GDLimit / fail_mean` times the life, and never reaches it.

    Why: `GDstress := GDFactorLim + (1 - GDFactorLim)*exp(-GDRate*FailureCounter)`
    (`block_lifecycle.py:157`), so the asymptote is `gd_factor_lim` itself and
    the wait is `life * GDstress`. This is the only one of the three formulas
    that reads `GDLimit` directly rather than through the clamp, and it is
    the row that proves it: `gd_limit=20.0` against a 10-hour life puts the
    asymptote at 20 hours, and the waits approach it from below.

    Setup: one block, `Fixed` 10-hour life, `Fixed` 1-hour repair,
    `gd_type=3`, `gd_rate=0.5`, `gd_limit=20.0` (`gd_factor_lim = 2.0`).
    A 45-hour run.

    Expected: waits of `10*(2 - e**-0.5) = 13.934693` and
    `10*(2 - e**-1) = 16.321206` hours, so failures at t=10,
    `11 + 13.934693 = 24.934693` and `25.934693 + 16.321206 = 42.255899`,
    with the run ending at 45 while the block is Running. The literal carries
    the engine's own last bits -- the run logs `CombStress=0.717633` and
    `0.612700`, the reciprocals of `1.3934693` and `1.6321206` -- which is
    where a reviewer can read the formula back without dividing anything.
    The two steps differ (13.93 then 16.32, never 20), which is what
    separates this arm from a linear one over the same rate.

    Source: Raptor 7, block_lifecycle.py:151 (`gd_factor_lim`),
     (this arm) (neither clamp arm can bind on this row:
    `2 - exp(-0.5c)` is always *below* the 2.0 limit, so the `> 1.0` arm's
    `gd_stress > gd_factor_lim` is never true, and the `< 1.0` arm does not
    apply at all); docs/engine-design/block-lifecycle.adoc.
    """
    result = run(_gd_model(10.0, 1.0, 45.0,
                           gd_type=3, gd_rate=0.5, gd_limit=20.0))

    assert result.draws == 0, (
        f"every distribution is Fixed, so this run must draw nothing; it "
        f"drew {result.draws}")

    assert_timeline(result, [
        (0.0, 'Block', 'A', '--', 'Good'),
        (10.0, 'Block', 'A', 'Good', 'Failed'),
        (10.0, 'Block', 'A', 'Failed', 'Repairing'),
        (10.0, 'Node', 'end', '--', 'Bad'),
        (10.0, 'System', 'State_change', '--', 'Red'),
        (11.0, 'Block', 'A', 'Repairing', 'Repaired'),
        (11.0, 'Block', 'A', 'Repaired', 'Good'),
        (11.0, 'Node', 'end', 'Bad', 'Good'),
        (11.0, 'Block', 'A', 'Good', 'StessLevel'),
        (11.0, 'System', 'State_change', 'Red', 'Green'),
        (24.934693402873663, 'Block', 'A', 'StessLevel', 'Failed'),
        (24.934693402873663, 'Block', 'A', 'Failed', 'Repairing'),
        (24.934693402873663, 'Node', 'end', 'Good', 'Bad'),
        (24.934693402873663, 'System', 'State_change', 'Green', 'Red'),
        (25.934693402873663, 'Block', 'A', 'Repairing', 'Repaired'),
        (25.934693402873663, 'Block', 'A', 'Repaired', 'Good'),
        (25.934693402873663, 'Node', 'end', 'Bad', 'Good'),
        (25.934693402873663, 'Block', 'A', 'Good', 'StessLevel'),
        (25.934693402873663, 'System', 'State_change', 'Red', 'Green'),
        (42.25589899115924, 'Block', 'A', 'StessLevel', 'Failed'),
        (42.25589899115924, 'Block', 'A', 'Failed', 'Repairing'),
        (42.25589899115924, 'Node', 'end', 'Good', 'Bad'),
        (42.25589899115924, 'System', 'State_change', 'Green', 'Red'),
        (43.25589899115924, 'Block', 'A', 'Repairing', 'Repaired'),
        (43.25589899115924, 'Block', 'A', 'Repaired', 'Good'),
        (43.25589899115924, 'Node', 'end', 'Bad', 'Good'),
        (43.25589899115924, 'Block', 'A', 'Good', 'StessLevel'),
        (43.25589899115924, 'System', 'State_change', 'Red', 'Green'),
        (45.0, 'Block', 'A', 'StessLevel', 'Done'),
        (45.0, 'System', 'Phase_change', '--', 'Mission_failure'),
        (45.0, 'System', 'Simulation_Terminate', '--', '--'),
    ], where="GDType 3 -- saturating")


def test_a_limit_above_one_clamps_a_growth_stress_that_climbs_past_it() -> None:
    """A `GDFactorLim` above 1 holds a growing `GDstress` at the limit: the
    waits stop lengthening once the stress crosses it.

    Why: the first clamp arm, `IF GDFactorLim>1.0 AND GDstress>GDFactorLim:
    GDstress:=GDFactorLim` (`block_lifecycle.py:164-165`). The same block,
    life and `GDRate` as the linear row grows its waits without limit
    (10, 15, 20); here the second wait is 12 and every wait after it is 12
    too, because `gd_factor_lim = 12.0/10 = 1.2` and the linear arm's
    `1 + 0.5*counter` passes 1.2 at counter 1 and is then pinned there.

    Setup: one block, `Fixed` 10-hour life, `Fixed` 1-hour repair,
    `gd_type=1`, `gd_rate=0.5`, `gd_limit=12.0`. A 40-hour run.

    Expected: waits of 10, 12 and 12 hours -- failures at t=10,
    `11+12 = 23` and `24+12 = 36`, and the run logs
    `CombStress=0.833333` three times (`1/1.2`), the same value every cycle,
    which is the clamp's own signature: the linear arm alone would have
    logged `0.5` and `0.4` on the second and third cycles.

    Source: Raptor 7, block_lifecycle.py:151
    (`gd_factor_lim = gd_limit / fail_mean`) (the linear arm this
    clamps) (this clamp); docs/engine-design/
    block-lifecycle.adoc.
    """
    result = run(_gd_model(10.0, 1.0, 40.0,
                           gd_type=1, gd_rate=0.5, gd_limit=12.0))

    assert result.draws == 0, (
        f"every distribution is Fixed, so this run must draw nothing; it "
        f"drew {result.draws}")

    assert_timeline(result, [
        (0.0, 'Block', 'A', '--', 'Good'),
        (10.0, 'Block', 'A', 'Good', 'Failed'),
        (10.0, 'Block', 'A', 'Failed', 'Repairing'),
        (10.0, 'Node', 'end', '--', 'Bad'),
        (10.0, 'System', 'State_change', '--', 'Red'),
        (11.0, 'Block', 'A', 'Repairing', 'Repaired'),
        (11.0, 'Block', 'A', 'Repaired', 'Good'),
        (11.0, 'Node', 'end', 'Bad', 'Good'),
        (11.0, 'Block', 'A', 'Good', 'StessLevel'),
        (11.0, 'System', 'State_change', 'Red', 'Green'),
        (23.0, 'Block', 'A', 'StessLevel', 'Failed'),
        (23.0, 'Block', 'A', 'Failed', 'Repairing'),
        (23.0, 'Node', 'end', 'Good', 'Bad'),
        (23.0, 'System', 'State_change', 'Green', 'Red'),
        (24.0, 'Block', 'A', 'Repairing', 'Repaired'),
        (24.0, 'Block', 'A', 'Repaired', 'Good'),
        (24.0, 'Node', 'end', 'Bad', 'Good'),
        (24.0, 'Block', 'A', 'Good', 'StessLevel'),
        (24.0, 'System', 'State_change', 'Red', 'Green'),
        (36.0, 'Block', 'A', 'StessLevel', 'Failed'),
        (36.0, 'Block', 'A', 'Failed', 'Repairing'),
        (36.0, 'Node', 'end', 'Good', 'Bad'),
        (36.0, 'System', 'State_change', 'Green', 'Red'),
        (37.0, 'Block', 'A', 'Repairing', 'Repaired'),
        (37.0, 'Block', 'A', 'Repaired', 'Good'),
        (37.0, 'Node', 'end', 'Bad', 'Good'),
        (37.0, 'Block', 'A', 'Good', 'StessLevel'),
        (37.0, 'System', 'State_change', 'Red', 'Green'),
        (40.0, 'Block', 'A', 'StessLevel', 'Done'),
        (40.0, 'System', 'Phase_change', '--', 'Mission_failure'),
        (40.0, 'System', 'Simulation_Terminate', '--', '--'),
    ], where="GDFactorLim > 1 -- clamp down")


def test_a_limit_below_one_clamps_a_decay_stress_that_falls_under_it() -> None:
    """A `GDFactorLim` below 1 holds a shrinking `GDstress` at the limit: the
    waits stop shortening once the stress crosses under it.

    Why: the second clamp arm, `ELSIF GDFactorLim<1.0 AND GDstress<GDFactorLim:
    GDstress:=GDFactorLim` (`block_lifecycle.py:166-167`) -- a different
    condition and the other direction, so it needs its own test: here the
    geometric arm's `0.5**counter` decays 1, 0.5, 0.25 and 0.25, because
    `gd_factor_lim = 4.0/10 = 0.4` catches it at counter 2 and refuses to
    let it decay further.

    Setup: one block, `Fixed` 10-hour life, `Fixed` 1-hour repair,
    `gd_type=2`, `gd_rate=0.5`, `gd_limit=4.0`. A 25-hour run.

    Expected: waits of 10, 5 and 4 hours -- failures at t=10, `11+5 = 16` and
    `17+4 = 21`. The unclamped row of the same block decays to 2.5 and 1.25;
    here the run logs `CombStress=2.000000` then `2.500000` twice
    (`1/0.4`), so the third cycle's wait is 4 and not 2.5.

    Source: Raptor 7, block_lifecycle.py:151 (the
    geometric arm this clamps) (this clamp);
    docs/engine-design/block-lifecycle.adoc.
    """
    result = run(_gd_model(10.0, 1.0, 25.0,
                           gd_type=2, gd_rate=0.5, gd_limit=4.0))

    assert result.draws == 0, (
        f"every distribution is Fixed, so this run must draw nothing; it "
        f"drew {result.draws}")

    assert_timeline(result, [
        (0.0, 'Block', 'A', '--', 'Good'),
        (10.0, 'Block', 'A', 'Good', 'Failed'),
        (10.0, 'Block', 'A', 'Failed', 'Repairing'),
        (10.0, 'Node', 'end', '--', 'Bad'),
        (10.0, 'System', 'State_change', '--', 'Red'),
        (11.0, 'Block', 'A', 'Repairing', 'Repaired'),
        (11.0, 'Block', 'A', 'Repaired', 'Good'),
        (11.0, 'Node', 'end', 'Bad', 'Good'),
        (11.0, 'Block', 'A', 'Good', 'StessLevel'),
        (11.0, 'System', 'State_change', 'Red', 'Green'),
        (16.0, 'Block', 'A', 'StessLevel', 'Failed'),
        (16.0, 'Block', 'A', 'Failed', 'Repairing'),
        (16.0, 'Node', 'end', 'Good', 'Bad'),
        (16.0, 'System', 'State_change', 'Green', 'Red'),
        (17.0, 'Block', 'A', 'Repairing', 'Repaired'),
        (17.0, 'Block', 'A', 'Repaired', 'Good'),
        (17.0, 'Node', 'end', 'Bad', 'Good'),
        (17.0, 'Block', 'A', 'Good', 'StessLevel'),
        (17.0, 'System', 'State_change', 'Red', 'Green'),
        (21.0, 'Block', 'A', 'StessLevel', 'Failed'),
        (21.0, 'Block', 'A', 'Failed', 'Repairing'),
        (21.0, 'Node', 'end', 'Good', 'Bad'),
        (21.0, 'System', 'State_change', 'Green', 'Red'),
        (22.0, 'Block', 'A', 'Repairing', 'Repaired'),
        (22.0, 'Block', 'A', 'Repaired', 'Good'),
        (22.0, 'Node', 'end', 'Bad', 'Good'),
        (22.0, 'Block', 'A', 'Good', 'StessLevel'),
        (22.0, 'System', 'State_change', 'Red', 'Green'),
        (25.0, 'Block', 'A', 'StessLevel', 'Done'),
        (25.0, 'System', 'Phase_change', '--', 'Mission_failure'),
        (25.0, 'System', 'Simulation_Terminate', '--', '--'),
    ], where="GDFactorLim < 1 -- clamp up")


def test_gd_type_0_leaves_the_wait_at_the_undisturbed_life() -> None:
    """`GDType 0` -- no growth or decay at all: `GDstress` is 1.0, and
    `GDRate` and `GDLimit` are not read at all.

    Why: the first arm of the GD block, `IF GDType=0: GDstress:=1.0`
    (`block_lifecycle.py:148-149`) -- it short-circuits before
    `gd_factor_lim` is even computed, so nothing else in the block can
    bite. The fixture writes deliberately absurd `gd_rate=9.0` and
    `gd_limit=3.0` so that "not read" is the claim: a `GDType 0` block that
    did read either column would not produce these waits.

    Setup: one block, `Fixed` 10-hour life, `Fixed` 1-hour repair,
    `gd_type=0`, `gd_rate=9.0`, `gd_limit=3.0`. A 25-hour run.

    Expected: both waits are exactly the 10-hour life -- failures at t=10 and
    `11+10 = 21` -- and the run logs **no** `StessLevel` line at all, which
    is the engine's own statement that the stress never left 1.0: that line
    is written only `IF ... CombinedStress != 1.0`
    (`block_lifecycle.py:1949`). A `GDRate` of 9.0 read by the linear arm
    would have stretched the second wait to 100 hours and ended the run
    before the second failure.

    Source: Raptor 7, block_lifecycle.py:148-149 (this arm),
     (the `StessLevel` line and its `!= 1.0` gate);
    docs/engine-design/block-lifecycle.adoc.
    """
    result = run(_gd_model(10.0, 1.0, 25.0,
                           gd_type=0, gd_rate=9.0, gd_limit=3.0))

    assert result.draws == 0, (
        f"every distribution is Fixed, so this run must draw nothing; it "
        f"drew {result.draws}")

    assert_timeline(result, [
        (0.0, 'Block', 'A', '--', 'Good'),
        (10.0, 'Block', 'A', 'Good', 'Failed'),
        (10.0, 'Block', 'A', 'Failed', 'Repairing'),
        (10.0, 'Node', 'end', '--', 'Bad'),
        (10.0, 'System', 'State_change', '--', 'Red'),
        (11.0, 'Block', 'A', 'Repairing', 'Repaired'),
        (11.0, 'Block', 'A', 'Repaired', 'Good'),
        (11.0, 'Node', 'end', 'Bad', 'Good'),
        (11.0, 'System', 'State_change', 'Red', 'Green'),
        (21.0, 'Block', 'A', 'Good', 'Failed'),
        (21.0, 'Block', 'A', 'Failed', 'Repairing'),
        (21.0, 'Node', 'end', 'Good', 'Bad'),
        (21.0, 'System', 'State_change', 'Green', 'Red'),
        (22.0, 'Block', 'A', 'Repairing', 'Repaired'),
        (22.0, 'Block', 'A', 'Repaired', 'Good'),
        (22.0, 'Node', 'end', 'Bad', 'Good'),
        (22.0, 'System', 'State_change', 'Red', 'Green'),
        (25.0, 'Block', 'A', 'Good', 'Done'),
        (25.0, 'System', 'Phase_change', '--', 'Mission_failure'),
        (25.0, 'System', 'Simulation_Terminate', '--', '--'),
    ], where="GDType 0 -- stress fixed at 1.0")


def test_a_gd_type_outside_one_two_three_raises_when_the_limit_is_outside_the_window() -> None:
    """A `GDType` source does not handle, with `GDFactorLim` outside `(0, 1)`,
    raises `NotImplementedError` instead of inventing a stress.

    Why: source's IF-chain covers only `GDType` 1, 2 and 3, so any other
    positive value falls through with `GDstress` still holding whatever was
    on the stack ("source leaves GDstress uninitialised"); the
    port models that as `0.0` and then refuses to guess outside the one
    window where the clamp makes the outcome deterministic.
    `GDLimit = 0.0` puts `gd_factor_lim` at `0.0`, outside
    `(0, 1)`, so the clamp cannot rescue it and the guard fires on the
    block's very first wait.

    Setup: one block, `Fixed` 10-hour life, `Fixed` 1-hour repair,
    `gd_type=4`, `gd_limit=0.0`, no `gd_rate` (the unhandled arm never reads
    it). A 30-hour run, which is more than long enough to reach the wait.

    Expected: `NotImplementedError`, out of `run_simulation` with no partial
    timeline -- the block cannot be given a wait at all. The message names
    the block, `GDType=4` and the window, so the finding is legible in a
    failure log rather than being a bare exception type. Asserted on the
    raise rather than on a timeline because there is no timeline to assert:
    the block never reaches a state after `Good`.

    Source: Raptor 7 (`Run`'s GDstress chain, 1/2/3 only),
    block_lifecycle.py:158-163 (the uninitialised fall-through) and
     (this guard);
    raptor/api/validate.py:241-266 (`_check_gd_types`, the API-side error
    for the same value).
    """
    with pytest.raises(NotImplementedError) as raised:
        run(_gd_model(10.0, 1.0, 30.0, gd_type=4, gd_limit=0.0))

    message = str(raised.value)
    assert "GDType=4" in message, (
        f"the message must name the unhandled GDType; got {message!r}")
    assert "GDFactorLim=0.0" in message, (
        f"the message must name the limit that is outside the (0, 1) "
        f"window; got {message!r}")
    assert "not portable" in message, (
        f"the message must say why the port refuses to guess; got "
        f"{message!r}")


def test_a_gd_type_outside_one_two_three_runs_when_the_limit_rescues_it() -> None:
    """The same unhandled `GDType` **runs**, at a fixed stress, when
    `GDFactorLim` is inside `(0, 1)`: the clamp turns the uninitialised
    value into the limit itself.

    Why: `gd_stress = 0.0` for a `GDType` source does not handle, and with
    `gd_factor_lim = 0.5` the second clamp arm's condition
    `GDFactorLim<1.0 AND GDstress<GDFactorLim` holds
    (`block_lifecycle.py:166-167`), so the stress becomes `0.5` before the
    guard looks at it -- and the guard's own comment says this
    is the deterministic fixed point the model is for. `gd_factor_lim` is
    computed from `gd_limit / fail_mean`, so `gd_limit=5.0` against a 10-hour
    life is the window: the block waits `10 * 0.5 = 5` hours between every
    failure, at every counter including the first.

    Setup: one block, `Fixed` 10-hour life, `Fixed` 1-hour repair,
    `gd_type=4`, `gd_limit=5.0`. A 18-hour run.

    Expected: failures at t=5, 11 and 17 -- three equal 5-hour waits, half
    the life, with no clamp-free drift (the counter is irrelevant once the
    clamp has fixed the stress). The first `StessLevel` line is at t=0
    rather than after the first repair, because the stress is already 2.0 at
    kickoff: this is the run that distinguishes "the clamp rescues the
    uninitialised value" from "the value was never used".

    Source: Raptor 7, block_lifecycle.py:151
    (`gd_factor_lim`) (the uninitialised `0.0`) (the
    clamp that rescues it) (the guard it satisfies),
     (the `StessLevel` line this run's first row is);
    docs/engine-design/block-lifecycle.adoc.
    """
    result = run(_gd_model(10.0, 1.0, 18.0, gd_type=4, gd_limit=5.0))

    assert result.draws == 0, (
        f"every distribution is Fixed, so this run must draw nothing; it "
        f"drew {result.draws}")

    assert_timeline(result, [
        (0.0, 'Block', 'A', '--', 'Good'),
        (0.0, 'Block', 'A', 'Good', 'StessLevel'),
        (5.0, 'Block', 'A', 'StessLevel', 'Failed'),
        (5.0, 'Block', 'A', 'Failed', 'Repairing'),
        (5.0, 'Node', 'end', '--', 'Bad'),
        (5.0, 'System', 'State_change', '--', 'Red'),
        (6.0, 'Block', 'A', 'Repairing', 'Repaired'),
        (6.0, 'Block', 'A', 'Repaired', 'Good'),
        (6.0, 'Node', 'end', 'Bad', 'Good'),
        (6.0, 'Block', 'A', 'Good', 'StessLevel'),
        (6.0, 'System', 'State_change', 'Red', 'Green'),
        (11.0, 'Block', 'A', 'StessLevel', 'Failed'),
        (11.0, 'Block', 'A', 'Failed', 'Repairing'),
        (11.0, 'Node', 'end', 'Good', 'Bad'),
        (11.0, 'System', 'State_change', 'Green', 'Red'),
        (12.0, 'Block', 'A', 'Repairing', 'Repaired'),
        (12.0, 'Block', 'A', 'Repaired', 'Good'),
        (12.0, 'Node', 'end', 'Bad', 'Good'),
        (12.0, 'Block', 'A', 'Good', 'StessLevel'),
        (12.0, 'System', 'State_change', 'Red', 'Green'),
        (17.0, 'Block', 'A', 'StessLevel', 'Failed'),
        (17.0, 'Block', 'A', 'Failed', 'Repairing'),
        (17.0, 'Node', 'end', 'Good', 'Bad'),
        (17.0, 'System', 'State_change', 'Green', 'Red'),
        (18.0, 'Block', 'A', 'Repairing', 'Repaired'),
        (18.0, 'Block', 'A', 'Repaired', 'Good'),
        (18.0, 'Node', 'end', 'Bad', 'Good'),
        (18.0, 'Block', 'A', 'Good', 'StessLevel'),
        (18.0, 'Block', 'A', 'StessLevel', 'Done'),
        (18.0, 'System', 'State_change', 'Red', 'Green'),
        (18.0, 'System', 'Phase_change', '--', 'Mission_failure'),
        (18.0, 'System', 'Simulation_Terminate', '--', '--'),
    ], where="unhandled GDType, GDFactorLim in (0, 1) -- clamp rescues it")
