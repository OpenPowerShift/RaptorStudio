"""Infant mortality on switch-on: the `StartUpFaiLures` trigger and a block's
`initial_cost`.

`startup_failure_redraw` (`raptor/core/engine/block_lifecycle.py:51-77`) is
the whole mechanism, and it is eight lines long because it is a redraw and
nothing else:

    tag_next_draw(rng_sys10, block.name)
    draw = rng_sys10.draw_number(Distribution(10), [0.0, 1.0])
    if draw > block.initial_cost:
        return 0.000001
    return fail_time_left

When the model defines a TRIGGER whose name is exactly `StartUpFaiLures`, a
block whose `initial_cost` is below 1.0 draws `Uniform(0,1)` from
`SysStream[10]` at `Run`'s ZeroWait site, and a draw above `initial_cost`
forces its remaining life to 0.000001 h -- so a block switched back in fails
essentially on the spot instead of running the life it drew. Source's own
three copies of the branch are Raptor 7 and;
the port threads `SysStream[10]` in as `rng_sys10`
(`raptor/core/engine/kickoff.py:491`) and gates the call on the trigger flag,
the stream and `initial_cost` (`block_lifecycle.py:1004-1024`).

**The trigger name is load-bearing and misspelled in the source.**
`load.py:189` reads `any(t.trigger_name == "StartUpFaiLures" for t in
triggers)` -- capital F, capital L, lowercase trailing `ures`. A model that
spells it `StartUpFailures` runs perfectly and never once draws, so
`test_the_correctly_spelled_trigger_name_never_reaches_the_draw` pins that
the flag is *seen* rather than assuming it.

**How reactivation is reached here, and why it needs no second cold group.**
The draw fires on switch-*on*, not at t=0, so a fixture has to get a block
switched back in. The cheapest way is one cold group of two and nothing
else: `C` (priority 10) holds the single slot at kickoff, `D` (priority 20) is
demoted to `Standby`, and `C`'s failure at t=50 promotes `D` with
`Prior=Standby` and an unconditional `Switched_On` interrupt
(`raptor/core/engine/cold_standby.py:729-730`). For that interrupt to land on
the ZeroWait site -- the only site the port wires the redraw into besides the
Idle wait -- `D` must be parked there, which is what `sb_stress = 0.0` does:
`combined_stress` is zero for a Standby block with a zero standby stress
(`block_lifecycle.py:183-187`), so `D` parks at the combined Idle/ZeroWait
site (`block_lifecycle.py:1898-1909`) from t=0 until its promotion, and the
promotion's `Switched_On` is one of the three reasons the redraw reads
(`block_lifecycle.py:1013-1015`). **No dependent, no second cold group, no
node standby trick:** two blocks and three nodes. The third reason, a
`Phase_Change`, is reached from the same fixture with a phase table added --
see `_phased_cold_pair`.

**Why these tests carry a seed when the rest of the suite does not.** Rule B2
asks for `Fixed` distributions wherever the mechanism allows, and it allows
them here for everything *except* the subject: this mechanism is a draw of
`Uniform(0,1)`, distribution code 10, which is stochastic by definition. So
every test below names `seed=SEED` and keeps every life, repair, trigger
schedule and phase duration `Fixed` (code 19, zero draws). `Run.draws` -- the
harness's own measurement of every stream draw in the batch
(`harness.py:290`) -- is then 0, 1 or 2, and it is the instrument for the
distinction the output cannot make: a block with `initial_cost >= 1.0`, or a
model with no such trigger, **skips the draw** (`draws == 0`), which is not the
same claim as drawing and ignoring the result, and only the RNG state can tell
them apart.

**No cost sink.** `run_simulation_with_reports(..., cost_sink=...)` is not an
output switch -- it changes a phased model's trajectory, measured at
14.931518 h against 20.891688 h on one FailSim boundary
(`test_b_costs.py::test_requesting_the_interim_cost_stream_moves_the_phase_draw_behind_the_trigger`).
Two models below *are* phased (the two `Phase_Change` tests, which are the only
way to reach the third of the redraw's three wake reasons), so this is exactly
the trap: no sink is attached to any of them, and both phase durations are
`Fixed`, which is what keeps the only stochastic element in every model the
draw under test.

**The one claim this file cannot pin with a model.**
`startup_failure_redraw` opens with
`if rng_sys10 is None: return fail_time_left`, and no model can reach it: the
flag that calls the function is set only by the presence of the
`StartUpFaiLures` trigger, and a model with a trigger cannot run with fewer
than 11 system streams -- `kickoff._start_triggers` reads `sys_streams[11]`
unconditionally, once per trigger (`kickoff.py:775`), so a 10-stream model
raises `KeyError` there before any block ever reaches the redraw -- and 11
streams are built as keys 1..11, which necessarily includes key 10. Measured,
not assumed: a model with the trigger and `sys_streams` truncated to 10 entries
fails with `KeyError: 11` at kickoff. So the guard is belt-and-braces against a
caller that has already checked the same condition
(`block_lifecycle.py:1013`), and
`test_the_no_stream_path_hands_back_the_life_it_was_given` pins it as the
function's contract rather than pretending a model produced it.
"""
from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from raptor.core.engine import runner as _runner
from raptor.core.engine.block_lifecycle import startup_failure_redraw
from raptor.core.engine.blocks import BlockStatistics
from raptor.core.engine.driver import make_shared_streams
from raptor.core.engine.load import load_model
from raptor.core.rng import Distribution

from . import harness
from .harness import FIXED, assert_timeline, run

#: The seed every test here is salted with. Required (rule B2's exception)
#: because the mechanism under test is a `Uniform(0,1)` draw, the one thing in
#: this file that cannot be `Fixed`. With it, `SysStream[10]`'s first draw is a
#: fixed number, which is what lets the `initial_cost` boundary in the first
#: three tests be a literal rather than a coin flip.
SEED = 1234

#: Run length, in hours. Long enough that both blocks fail and are repaired
#: inside the run, so the `Done` sweep is in the literal too.
RUN_LENGTH = 100.0

#: `C` holds the cold group's single slot at kickoff and fails here, which is
#: the reactivation `D` waits for.
PEER_FAIL, PEER_REPAIR = 50.0, 10.0

#: `D`'s own `Fixed` life: the life it is running when it is switched back in.
#: Long enough that it is spent after the promotion, so the promotion's effect
#: on it is the whole story.
STANDBY_LIFE = 40.0

#: `D`'s standby stress. Zero is what parks it at the combined Idle/ZeroWait
#: site (see the module docstring), which is the site the redraw is wired
#: into. Its peer keeps a non-zero one so the `StessLevel` row exists to show
#: a standby block's stress being logged at all.
STANDBY_STRESS = 0.0

#: The trigger's own schedule: a `Fixed` 100000 h, so it never fires inside a
#: 100 h run and logs no row of its own, and `Fixed` costs no stream draw. The
#: trigger's existence is the whole input here; nothing about its firing is
#: under test.
TRIGGER_NAME = "StartUpFaiLures"
TRIGGER_WAIT = 100000.0

#: `initial_cost` below the drawn value (`DRAW` in the first test's docstring),
#: so the draw is above it and the life is forced; and the same value, so the
#: draw is below it and the life survives. Both are below 1.0, so both take the
#: branch -- the only difference between them is the comparison.
COST_BELOW_DRAW = 0.25
COST_ABOVE_DRAW = 0.5

#: The value at which the branch is not taken at all: the caller's gate is
#: `initial_cost < 1.0` (`block_lifecycle.py:1013`), so 1.0 skips the branch
#: including the draw.
COST_NO_BRANCH = 1.0

#: The phase boundary for the two phase-driven tests. Late enough that the
#: promotion at t=50 and its forced failure have already happened, so the
#: boundary's own roll is the only new draw, and early enough that the run's
#: end still lands inside phase 2.
PHASE_BOUNDARY = 70.0

#: `D`'s phase-2 cell. A positive value means the phase change *is* a stress
#: change the block must absorb, and the boundary's wake rolls; a zero value
#: means `combined_stress` is zero again afterwards, and the boundary's wake
#: does not roll (`block_lifecycle.py:1017-1022`).
PHASE_VALUE_POSITIVE = "A: 0.5"
PHASE_VALUE_ZERO = "A: 0.0"


def _block(name: str, life: float, **fields: Any) -> dict[str, Any]:
    """One block with a `Fixed` life of *life* and a `Fixed` repair.

    Only the columns a fixture names are written; `build_from_spec` applies the
    dict as a patch over the row it creates, so an omitted column keeps the
    template's value (`initial_cost` defaults to 0.0, which is already below
    1.0 -- the fixtures write it out so a reader can see which side of the
    gate each test is on).
    """
    row: dict[str, Any] = {"name": name, "fail_dist": FIXED,
                           "fail_params": [life], "repair_dist": FIXED,
                           "repair_params": [PEER_REPAIR]}
    row.update(fields)
    return row


def _phased_cold_pair(*, initial_cost: float, phase_two_value: str,
                      phase_one_value: str = "A: 1.0") -> dict[str, Any]:
    """The cold pair, plus a two-phase schedule whose boundary lands at
    `PHASE_BOUNDARY`, and `D` phased `"<code>: <value>"` in each phase.

    The phase table is the *only* addition to the fixture, and it is added for
    the one reason the module docstring gives for everything in this file: the
    redraw reads the wake reason, and a `Phase_Change` is the third of the
    three reasons it accepts (`block_lifecycle.py:1013-1015`). Both phase
    durations are `Fixed`, so the phase clock consumes no randomness and the
    only draws in the batch are still the redraw's own.

    The cells are written `"A: <value>"`, not `"<name>: <value>"`, because the
    part before the colon is the block's phase **type** (`A`/`C`/`L`), not its
    name -- `_apply_phase_assignment` partitions on the colon and the loader
    reads the left half as the type.
    """
    spec = _cold_pair(initial_cost=initial_cost)
    spec["phases"] = [
        {"name": "P1", "duration_dist": FIXED, "params": [PHASE_BOUNDARY],
         "mission": 1},
        {"name": "P2", "duration_dist": FIXED,
         "params": [RUN_LENGTH - PHASE_BOUNDARY], "mission": 0},
    ]
    spec["phase_states"] = {"D": [phase_one_value, phase_two_value]}
    return spec


def _cold_pair(*, initial_cost: float, trigger: str | None = TRIGGER_NAME,
               standby_stress: float = STANDBY_STRESS) -> dict[str, Any]:
    """A cold group of two, and nothing else.

    `C` and `D` into a 1-of-2 `cold` node behind a Start node and in front of
    an End node -- the minimum topology the loader accepts
    (`build_from_spec` refuses a model with no End node, and `load_model`
    refuses a block with no outgoing link). `cold_pri` 10 against 20 is the
    only thing that decides which path runs (`IdAndSortColds`'s selection
    sort), and `a_prob=1.0` with `auto_time=0.0` means `SetSwitchTime`'s
    `UniformReal(0,1)` is short-circuited and no switch timer is ever started,
    so the cold machinery itself costs no draw -- see
    `test_b_node_standby.py::_cold` for the same three columns and why.

    *trigger* is the trigger's name, or `None` for a model with no trigger at
    all.
    """
    spec: dict[str, Any] = {
        "system": {"run_length": RUN_LENGTH, "num_runs": 1,
                   "termination": "TimeSim"},
        "blocks": [
            _block("C", PEER_FAIL, sb_stress=0.5),
            _block("D", STANDBY_LIFE, sb_stress=standby_stress,
                   initial_cost=initial_cost),
        ],
        "nodes": [
            {"id": 1, "name": "Start", "type": 1, "n": 0, "out": 2},
            {"id": 2, "name": "Cold", "type": 2, "n": 2, "out": 1, "k": 1,
             "k_star": 1, "cold": 1},
            {"id": 3, "name": "End", "type": 3, "n": 1, "out": 0},
        ],
        "links": [
            {"from": "Start", "to": "C"},
            {"from": "Start", "to": "D"},
            {"from": "C", "to": "Cold", "cold_pri": 10, "a_prob": 1.0,
             "auto_time": 0.0, "man_time": -1.0},
            {"from": "D", "to": "Cold", "cold_pri": 20, "a_prob": 1.0,
             "auto_time": 0.0, "man_time": -1.0},
            {"from": "Cold", "to": "End"},
        ],
    }
    if trigger is not None:
        spec["triggers"] = [{"name": trigger, "init_used": 0.0, "dist": FIXED,
                             "params": [TRIGGER_WAIT, 0.0, 0.0],
                             "repeats": 0}]
    return spec


# ---------------------------------------------------------------------------
# Claim 1: the draw, taken, and above `initial_cost` -> a forced 0.000001 h life
# ---------------------------------------------------------------------------

def test_a_reactivated_block_whose_initial_cost_is_below_the_draw_fails_at_once() -> None:
    """A reactivated block with `initial_cost` below the drawn value fails
    0.000001 h after it is switched back in, not after the life it drew.

    Why: this is the mechanism itself. Source's `Run` ZeroWait site, in three
    identical copies,
    draws `Uniform(0,1)` from `SysStream[10]` for a low-`initialCost` block
    and sets `FailTimeLeft:=0.000001` when the draw beats it; the port is
    `startup_failure_redraw` (`block_lifecycle.py:51-77`), gated by
    `_startup_draw`. Infant mortality on switch-on: the block
    is condemned by its switch-on roll, not by its age.

    Setup: the cold pair of the module docstring, `D` at
    `initial_cost = 0.25` and `sb_stress = 0.0`, the `StartUpFaiLures`
    trigger present, and `seed=SEED`. With that seed `SysStream[10]`'s first
    draw is 0.344364 -- which is why 0.25 and 0.5 straddle it in the two
    tests that share this fixture -- so the draw is *above* the cost here.

    Expected: `D` is promoted at t=50 and fails at t=50.000001, taking the
    cold node and the End node down with it, because 0.000001 h is the whole
    of the life it was reactivated with. Exactly one draw in the batch: the
    `Fixed` lives consume none, so `Run.draws == 1` is this draw and nothing
    else.

    Source: raptor/core/engine/block_lifecycle.py (`startup_failure_redraw`,
      `_startup_draw`); raptor/core/engine/kickoff.py (`rng_sys10`)
    """
    result = run(_cold_pair(initial_cost=COST_BELOW_DRAW), seed=SEED)

    assert result.draws == 1
    assert_timeline(result, [
        (0.0, 'Block', 'C', '--', 'Good'),
        (0.0, 'Block', 'D', '--', 'Good'),
        (0.0, 'Block', 'D', 'Good', 'Standby'),
        (0.0, 'Node', 'Cold', '--', 'Good'),
        (50.0, 'Block', 'C', 'Good', 'Failed'),
        (50.0, 'Block', 'C', 'Failed', 'Repairing'),
        (50.0, 'Block', 'D', 'Standby', 'Good'),
        (50.0, 'Node', 'Cold', 'Good', 'Degraded'),
        (50.0, 'System', 'State_change', '--', 'Yellow'),
        (50.000001, 'Block', 'D', 'Good', 'Failed'),
        (50.000001, 'Block', 'D', 'Failed', 'Repairing'),
        (50.000001, 'Node', 'Cold', 'Degraded', 'Bad'),
        (50.000001, 'Node', 'end', '--', 'Bad'),
        (50.000001, 'System', 'State_change', 'Yellow', 'Red'),
        (60.0, 'Block', 'C', 'Repairing', 'Repaired'),
        (60.0, 'Block', 'C', 'Repaired', 'Good'),
        (60.0, 'Node', 'Cold', 'Bad', 'Degraded'),
        (60.0, 'Node', 'end', 'Bad', 'Good'),
        (60.0, 'System', 'State_change', 'Red', 'Yellow'),
        (60.000001, 'Block', 'D', 'Repairing', 'Repaired'),
        (60.000001, 'Block', 'D', 'Repaired', 'Good'),
        (60.000001, 'Block', 'D', 'Good', 'Standby'),
        (60.000001, 'Node', 'Cold', 'Degraded', 'Good'),
        (60.000001, 'System', 'State_change', 'Yellow', 'Green'),
        (100.0, 'Block', 'C', 'Good', 'Done'),
        (100.0, 'Block', 'D', 'Standby', 'Done'),
        (100.0, 'System', 'Phase_change', '--', 'Mission_failure'),
        (100.0, 'System', 'Simulation_Terminate', '--', '--'),
    ])


# ---------------------------------------------------------------------------
# Claim 2: the same fixture with the draw *below* `initial_cost` -- the draw is
# taken and its result ignored. Same timeline, one more draw.
# ---------------------------------------------------------------------------

def test_a_reactivated_block_whose_initial_cost_is_above_the_draw_still_draws() -> None:
    """`initial_cost` above the drawn value does not stop the draw: the roll
    is taken off `SysStream[10]` and then ignored, and the block keeps the
    life it drew.

    Why: this is the pair that makes the mechanism legible. The output of the
    two tests is *the same timeline* -- a reactivated block that fails at t=90
    rather than t=50.000001 -- because in both cases `D` runs out the life it
    drew before the promotion. Only `Run.draws` separates "drew and the roll
    lost" from "never drew", and it is the draw that shows the comparison is
    `draw > initial_cost` against a real `Uniform(0,1)` number and not
    `draw >= 1.0` or any test of the cost alone: 0.5 is a cost the branch
    admits, and the branch still ran.

    Setup: the same fixture with `D` at `initial_cost = 0.5`, which is above
    the seeded draw of 0.344364 and below the 1.0 the gate admits, and the
    same `seed=SEED`.

    Expected: `D` is promoted at t=50, runs the 40 h life it drew before the
    promotion, and fails at t=90. `Run.draws == 1` -- the draw was taken.

    Source: raptor/core/engine/block_lifecycle.py:69-73 (the tag, the
      Uniform(0,1) draw and the `draw > block.initial_cost` comparison);
    """
    result = run(_cold_pair(initial_cost=COST_ABOVE_DRAW), seed=SEED)

    assert result.draws == 1
    assert_timeline(result, [
        (0.0, 'Block', 'C', '--', 'Good'),
        (0.0, 'Block', 'D', '--', 'Good'),
        (0.0, 'Block', 'D', 'Good', 'Standby'),
        (0.0, 'Node', 'Cold', '--', 'Good'),
        (50.0, 'Block', 'C', 'Good', 'Failed'),
        (50.0, 'Block', 'C', 'Failed', 'Repairing'),
        (50.0, 'Block', 'D', 'Standby', 'Good'),
        (50.0, 'Node', 'Cold', 'Good', 'Degraded'),
        (50.0, 'System', 'State_change', '--', 'Yellow'),
        (60.0, 'Block', 'C', 'Repairing', 'Repaired'),
        (60.0, 'Block', 'C', 'Repaired', 'Good'),
        (60.0, 'Block', 'C', 'Good', 'Standby'),
        (60.0, 'Node', 'Cold', 'Degraded', 'Good'),
        (60.0, 'Block', 'C', 'Standby', 'StessLevel'),
        (60.0, 'System', 'State_change', 'Yellow', 'Green'),
        (90.0, 'Block', 'D', 'Good', 'Failed'),
        (90.0, 'Block', 'D', 'Failed', 'Repairing'),
        (90.0, 'Block', 'C', 'StessLevel', 'Good'),
        (90.0, 'Node', 'Cold', 'Good', 'Degraded'),
        (90.0, 'System', 'State_change', 'Green', 'Yellow'),
        (100.0, 'Block', 'D', 'Repairing', 'Repaired'),
        (100.0, 'Block', 'D', 'Repaired', 'Good'),
        (100.0, 'Block', 'D', 'Good', 'Standby'),
        (100.0, 'Node', 'Cold', 'Degraded', 'Good'),
        (100.0, 'Block', 'C', 'Good', 'Done'),
        (100.0, 'Block', 'D', 'Standby', 'Done'),
        (100.0, 'System', 'State_change', 'Yellow', 'Green'),
        (100.0, 'System', 'Phase_change', '--', 'Mission_success'),
        (100.0, 'System', 'Simulation_Terminate', '--', '--'),
    ])


# ---------------------------------------------------------------------------
# Claim 3: `initial_cost >= 1.0` skips the branch *including the draw*
# ---------------------------------------------------------------------------

def test_a_reactivated_block_at_initial_cost_of_one_skips_the_draw_entirely() -> None:
    """At `initial_cost = 1.0` the branch is not taken at all, and no draw is
    taken with it -- the block's drawn life survives untouched.

    Why: the gate is on the block's `initialCost` *before* the roll, not after
    it (`block_lifecycle.py:1013-1015`, and the docstring's own "blocks with
    `initial_cost >= 1.0` skip the branch including the draw"). That is a
    claim about the RNG's state as much as about the output, and the two are
    only separable by counting draws: the timeline below is byte-for-byte the
    previous test's, which is exactly why the output cannot carry this claim
    on its own.

    Setup: the same fixture with `D` at `initial_cost = 1.0`, the
    `StartUpFaiLures` trigger still present, and the same `seed=SEED`.

    Expected: `D` is promoted at t=50 and fails at t=90 on its own drawn
    life, and `Run.draws == 0` -- the batch consumes no randomness at all.

    Source: raptor/core/engine/block_lifecycle.py:1013 (the gate on the flag,
      the stream and the cost) (_startup_draw);
    """
    result = run(_cold_pair(initial_cost=COST_NO_BRANCH), seed=SEED)

    assert result.draws == 0
    assert_timeline(result, [
        (0.0, 'Block', 'C', '--', 'Good'),
        (0.0, 'Block', 'D', '--', 'Good'),
        (0.0, 'Block', 'D', 'Good', 'Standby'),
        (0.0, 'Node', 'Cold', '--', 'Good'),
        (50.0, 'Block', 'C', 'Good', 'Failed'),
        (50.0, 'Block', 'C', 'Failed', 'Repairing'),
        (50.0, 'Block', 'D', 'Standby', 'Good'),
        (50.0, 'Node', 'Cold', 'Good', 'Degraded'),
        (50.0, 'System', 'State_change', '--', 'Yellow'),
        (60.0, 'Block', 'C', 'Repairing', 'Repaired'),
        (60.0, 'Block', 'C', 'Repaired', 'Good'),
        (60.0, 'Block', 'C', 'Good', 'Standby'),
        (60.0, 'Node', 'Cold', 'Degraded', 'Good'),
        (60.0, 'Block', 'C', 'Standby', 'StessLevel'),
        (60.0, 'System', 'State_change', 'Yellow', 'Green'),
        (90.0, 'Block', 'D', 'Good', 'Failed'),
        (90.0, 'Block', 'D', 'Failed', 'Repairing'),
        (90.0, 'Block', 'C', 'StessLevel', 'Good'),
        (90.0, 'Node', 'Cold', 'Good', 'Degraded'),
        (90.0, 'System', 'State_change', 'Green', 'Yellow'),
        (100.0, 'Block', 'D', 'Repairing', 'Repaired'),
        (100.0, 'Block', 'D', 'Repaired', 'Good'),
        (100.0, 'Block', 'D', 'Good', 'Standby'),
        (100.0, 'Node', 'Cold', 'Degraded', 'Good'),
        (100.0, 'Block', 'C', 'Good', 'Done'),
        (100.0, 'Block', 'D', 'Standby', 'Done'),
        (100.0, 'System', 'State_change', 'Yellow', 'Green'),
        (100.0, 'System', 'Phase_change', '--', 'Mission_success'),
        (100.0, 'System', 'Simulation_Terminate', '--', '--'),
    ])


# ---------------------------------------------------------------------------
# Claim 4: no such trigger defined -> no draw
# ---------------------------------------------------------------------------

def test_a_model_without_the_startup_failures_trigger_never_draws() -> None:
    """Without a trigger named `StartUpFaiLures` the reactivation is silent:
    no draw, and the reactivated block runs its own life out.

    Why: the trigger is a magic name, not a switch. `load.py:189` folds every
    model's trigger list into one boolean at load time
    (`startup_failures=any(t.trigger_name == "StartUpFaiLures" ...)`) and
    `BlockEnv` carries only that boolean into `Run`, so a model without the
    trigger cannot reach the branch at all however many times a block is
    switched back in.

    Setup: the cold pair with no `triggers` key at all, `D` back at
    `initial_cost = 0.25` -- below the 1.0 the gate admits, so the *only*
    thing standing between this run and the roll is the trigger's absence --
    and the same `seed=SEED`.

    Expected: the same timeline as a reactivated block running out its drawn
    life, and `Run.draws == 0`.

    Source: raptor/core/engine/load.py:189 (startup_failures from the
      trigger name), raptor/core/engine/block_env.py:108 (the flag on the
      env), block_lifecycle.py:1013 (the gate reads the flag first)
    """
    result = run(_cold_pair(initial_cost=COST_BELOW_DRAW, trigger=None), seed=SEED)

    assert result.draws == 0
    assert_timeline(result, [
        (0.0, 'Block', 'C', '--', 'Good'),
        (0.0, 'Block', 'D', '--', 'Good'),
        (0.0, 'Block', 'D', 'Good', 'Standby'),
        (0.0, 'Node', 'Cold', '--', 'Good'),
        (50.0, 'Block', 'C', 'Good', 'Failed'),
        (50.0, 'Block', 'C', 'Failed', 'Repairing'),
        (50.0, 'Block', 'D', 'Standby', 'Good'),
        (50.0, 'Node', 'Cold', 'Good', 'Degraded'),
        (50.0, 'System', 'State_change', '--', 'Yellow'),
        (60.0, 'Block', 'C', 'Repairing', 'Repaired'),
        (60.0, 'Block', 'C', 'Repaired', 'Good'),
        (60.0, 'Block', 'C', 'Good', 'Standby'),
        (60.0, 'Node', 'Cold', 'Degraded', 'Good'),
        (60.0, 'Block', 'C', 'Standby', 'StessLevel'),
        (60.0, 'System', 'State_change', 'Yellow', 'Green'),
        (90.0, 'Block', 'D', 'Good', 'Failed'),
        (90.0, 'Block', 'D', 'Failed', 'Repairing'),
        (90.0, 'Block', 'C', 'StessLevel', 'Good'),
        (90.0, 'Node', 'Cold', 'Good', 'Degraded'),
        (90.0, 'System', 'State_change', 'Green', 'Yellow'),
        (100.0, 'Block', 'D', 'Repairing', 'Repaired'),
        (100.0, 'Block', 'D', 'Repaired', 'Good'),
        (100.0, 'Block', 'D', 'Good', 'Standby'),
        (100.0, 'Node', 'Cold', 'Degraded', 'Good'),
        (100.0, 'Block', 'C', 'Good', 'Done'),
        (100.0, 'Block', 'D', 'Standby', 'Done'),
        (100.0, 'System', 'State_change', 'Yellow', 'Green'),
        (100.0, 'System', 'Phase_change', '--', 'Mission_success'),
        (100.0, 'System', 'Simulation_Terminate', '--', '--'),
    ])


# ---------------------------------------------------------------------------
# Claim 5: the trigger name is matched exactly -- the source's spelling
# ---------------------------------------------------------------------------

def test_the_correctly_spelled_trigger_name_never_reaches_the_draw() -> None:
    """A trigger spelled `StartUpFailures` does not switch the mechanism on:
    the run is indistinguishable from a model with no trigger at all.

    Why: the source's own spelling is `StartUpFaiLures` -- capital F, capital
    L, lowercase trailing `ures` -- and `load.py:189` compares the string
    exactly. A fixture that "corrects" the spelling produces a model that
    simulates perfectly and tests nothing, which is the failure mode this
    whole file exists to rule out. The first test's positive result is the
    other half of that pair: between them, the trigger is provably *seen*.

    Setup: the cold pair with the trigger present and correctly spelled, `D`
    at `initial_cost = 0.25`, and the same `seed=SEED` -- so the only
    difference from the first test is six characters of the trigger's name.

    Expected: no draw (`Run.draws == 0`) and the reactivated block runs out
    its own life, failing at t=90.

    Source: raptor/core/engine/load.py:189 (the exact string comparison);
      raptor/api/build.py (TRIGGERS rows, `trigger_name`)
    """
    result = run(_cold_pair(initial_cost=COST_BELOW_DRAW,
                            trigger="StartUpFailures"), seed=SEED)

    assert result.draws == 0
    assert_timeline(result, [
        (0.0, 'Block', 'C', '--', 'Good'),
        (0.0, 'Block', 'D', '--', 'Good'),
        (0.0, 'Block', 'D', 'Good', 'Standby'),
        (0.0, 'Node', 'Cold', '--', 'Good'),
        (50.0, 'Block', 'C', 'Good', 'Failed'),
        (50.0, 'Block', 'C', 'Failed', 'Repairing'),
        (50.0, 'Block', 'D', 'Standby', 'Good'),
        (50.0, 'Node', 'Cold', 'Good', 'Degraded'),
        (50.0, 'System', 'State_change', '--', 'Yellow'),
        (60.0, 'Block', 'C', 'Repairing', 'Repaired'),
        (60.0, 'Block', 'C', 'Repaired', 'Good'),
        (60.0, 'Block', 'C', 'Good', 'Standby'),
        (60.0, 'Node', 'Cold', 'Degraded', 'Good'),
        (60.0, 'Block', 'C', 'Standby', 'StessLevel'),
        (60.0, 'System', 'State_change', 'Yellow', 'Green'),
        (90.0, 'Block', 'D', 'Good', 'Failed'),
        (90.0, 'Block', 'D', 'Failed', 'Repairing'),
        (90.0, 'Block', 'C', 'StessLevel', 'Good'),
        (90.0, 'Node', 'Cold', 'Good', 'Degraded'),
        (90.0, 'System', 'State_change', 'Green', 'Yellow'),
        (100.0, 'Block', 'D', 'Repairing', 'Repaired'),
        (100.0, 'Block', 'D', 'Repaired', 'Good'),
        (100.0, 'Block', 'D', 'Good', 'Standby'),
        (100.0, 'Node', 'Cold', 'Degraded', 'Good'),
        (100.0, 'Block', 'C', 'Good', 'Done'),
        (100.0, 'Block', 'D', 'Standby', 'Done'),
        (100.0, 'System', 'State_change', 'Yellow', 'Green'),
        (100.0, 'System', 'Phase_change', '--', 'Mission_success'),
        (100.0, 'System', 'Simulation_Terminate', '--', '--'),
    ])


# ---------------------------------------------------------------------------
# Claim 6: the roll is a Uniform(0,1) off SysStream[10], tagged with the block
# ---------------------------------------------------------------------------

def test_the_roll_is_a_uniform_off_sysstream_ten_tagged_with_the_block_name(
        tmp_path: Path) -> None:
    """The one draw of the run is `Uniform(0,1)` from `SysStream[10]`, taken at
    the switch-on instant and recorded under the *block's* name.

    Why: `Distribution(10)` with `[0.0, 1.0]` is a `Uniform(0,1)` off a
    shared system stream, and the call site tags it with the block's own name
    the way real's `DrawNumber` `gettingDraw` argument does
    (`tag_next_draw`, `sim_details.py:109-121`). A shared stream is otherwise
    recorded generically as `SysStream10`, so the tag appearing as `D` is what
    shows this is that call site and not some other draw off the same
    generator. The value is checked against the engine's own construction of
    that stream -- `make_shared_streams(sys_streams, seed=SEED)[10]`, whose
    first draw must be it -- so the stream is named rather than inferred, and
    it is inside (0, 1), as a `Uniform(0,1)` sample must be.

    Setup: the first test's fixture, plus the run repeated with the draw log
    requested. The model is still built and run through `harness.run`; the
    second run exists only because the recorder is reachable through
    `run_simulation_with_reports`' own `sim_details_path` argument and not
    through a spec field, the same reason `test_b_costs.py` re-runs for its
    cost sink.

    Expected: one recorded draw -- `50.000000  D  0.344364` -- at the
    promotion, equal to `SysStream[10]`'s first draw and inside (0, 1); and
    the same timeline the first test pins, since this is the very same run.

    Source: raptor/core/engine/block_lifecycle.py:67-70 (tempArray[1]=0,
      [2]=1 -- Uniform(0,1) via dist 10, with the block's name as the tag);
      raptor/core/engine/sim_details.py:109-121 (tag_next_draw);
      raptor/core/engine/driver.py:62-75 (make_shared_streams);
      raptor/core/fileio/rbdlib.py:92 (the default SysStreamsArray)
    """
    spec = _cold_pair(initial_cost=COST_BELOW_DRAW)
    result = run(spec, seed=SEED)
    log = tmp_path / "startup_draws.log"
    _runner.run_simulation_with_reports(
        result.rbd, 1, date=harness.REPORT_DATE, seed=SEED,
        sim_details_path=str(log))

    recorded = [line for line in log.read_text(encoding="latin-1").splitlines()
                if line.strip()]
    assert recorded == ["50.000000  D  0.344364"]

    model = load_model(result.rbd)
    expected = make_shared_streams(model.sys_streams, seed=SEED)[10].draw_number(
        Distribution(10), [0.0, 1.0])
    assert 0.0 < expected < 1.0
    assert float(recorded[0].split()[-1]) == pytest.approx(expected)

    assert result.draws == 1
    assert_timeline(result, [
        (0.0, 'Block', 'C', '--', 'Good'),
        (0.0, 'Block', 'D', '--', 'Good'),
        (0.0, 'Block', 'D', 'Good', 'Standby'),
        (0.0, 'Node', 'Cold', '--', 'Good'),
        (50.0, 'Block', 'C', 'Good', 'Failed'),
        (50.0, 'Block', 'C', 'Failed', 'Repairing'),
        (50.0, 'Block', 'D', 'Standby', 'Good'),
        (50.0, 'Node', 'Cold', 'Good', 'Degraded'),
        (50.0, 'System', 'State_change', '--', 'Yellow'),
        (50.000001, 'Block', 'D', 'Good', 'Failed'),
        (50.000001, 'Block', 'D', 'Failed', 'Repairing'),
        (50.000001, 'Node', 'Cold', 'Degraded', 'Bad'),
        (50.000001, 'Node', 'end', '--', 'Bad'),
        (50.000001, 'System', 'State_change', 'Yellow', 'Red'),
        (60.0, 'Block', 'C', 'Repairing', 'Repaired'),
        (60.0, 'Block', 'C', 'Repaired', 'Good'),
        (60.0, 'Node', 'Cold', 'Bad', 'Degraded'),
        (60.0, 'Node', 'end', 'Bad', 'Good'),
        (60.0, 'System', 'State_change', 'Red', 'Yellow'),
        (60.000001, 'Block', 'D', 'Repairing', 'Repaired'),
        (60.000001, 'Block', 'D', 'Repaired', 'Good'),
        (60.000001, 'Block', 'D', 'Good', 'Standby'),
        (60.000001, 'Node', 'Cold', 'Degraded', 'Good'),
        (60.000001, 'System', 'State_change', 'Yellow', 'Green'),
        (100.0, 'Block', 'C', 'Good', 'Done'),
        (100.0, 'Block', 'D', 'Standby', 'Done'),
        (100.0, 'System', 'Phase_change', '--', 'Mission_failure'),
        (100.0, 'System', 'Simulation_Terminate', '--', '--'),
    ])


# ---------------------------------------------------------------------------
# Claims 6 and 7: the third wake reason, and the phase-value guard on it
# ---------------------------------------------------------------------------

def test_a_phase_boundary_whose_phase_value_is_positive_rolls_again() -> None:
    """A `Phase_Change` wake onto a positive phase value rolls the same
    `Uniform(0,1)` a second time, and the roll is taken.

    Why: the redraw is wired into three wait sites, and a phase boundary that
    changes a phased block's stress is the first of the three reasons the
    caller's gate admits (`block_lifecycle.py:1013-1015`), drawn from one of
    source's two other copies of the branch. It is
    worth its own test because it is a *different* reactivation from the
    switch-on the other tests use: no block is promoted, the cold group does
    nothing at the boundary, and the roll lands on a block that is `Standby`
    because its peer's repair demoted it again at t=60.000001.

    Setup: the cold pair plus a two-phase schedule with the boundary at t=70,
    and `D` phased `"A: 1.0"` then `"A: 0.5"` -- a value change with the same
    type on both sides, which is the only shape that classifies as a bare
    stress change (`phasing.py:179-184`) and therefore the only one whose
    `Phase_Change` interrupt is sent (`cascade_phase.py:158-164`). The same
    `seed=SEED`.

    Expected: the promotion's roll at t=50 and the boundary's roll at t=70,
    two draws in the batch, and the boundary's roll forced against `D` while
    it is `Standby` -- where a forced 0.000001 h life cannot complete, since
    `combined_stress` is zero for a standby block at a zero standby stress, so
    no further row appears. The timeline is the unphased fixture's plus the
    phase rows; nothing else moves.

    Source: raptor/core/engine/block_lifecycle.py:1013-1015 (Phase_Change in
      the gate) (the positive-value arm); Raptor 7;
      raptor/core/engine/phasing.py:179-184 (a value-only change is "stress");
      raptor/core/engine/cascade_phase.py:158-164 (the interrupt)
    """
    result = run(_phased_cold_pair(initial_cost=COST_BELOW_DRAW,
                                  phase_two_value=PHASE_VALUE_POSITIVE),
                 seed=SEED)

    assert result.draws == 2
    assert_timeline(result, [
        (0.0, 'System', 'State_change', '--', 'Mission'),
        (0.0, 'Block', 'C', '--', 'Good'),
        (0.0, 'Block', 'D', '--', 'Good'),
        (0.0, 'Block', 'D', 'Good', 'Standby'),
        (0.0, 'Node', 'Cold', '--', 'Good'),
        (50.0, 'Block', 'C', 'Good', 'Failed'),
        (50.0, 'Block', 'C', 'Failed', 'Repairing'),
        (50.0, 'Block', 'D', 'Standby', 'Good'),
        (50.0, 'Node', 'Cold', 'Good', 'Degraded'),
        (50.0, 'System', 'State_change', 'Mission', 'Yellow'),
        (50.000001, 'Block', 'D', 'Good', 'Failed'),
        (50.000001, 'Block', 'D', 'Failed', 'Repairing'),
        (50.000001, 'Node', 'Cold', 'Degraded', 'Bad'),
        (50.000001, 'Node', 'end', '--', 'Bad'),
        (50.000001, 'System', 'State_change', 'Yellow', 'Red'),
        (60.0, 'Block', 'C', 'Repairing', 'Repaired'),
        (60.0, 'Block', 'C', 'Repaired', 'Good'),
        (60.0, 'Node', 'Cold', 'Bad', 'Degraded'),
        (60.0, 'Node', 'end', 'Bad', 'Good'),
        (60.0, 'System', 'State_change', 'Red', 'Yellow'),
        (60.000001, 'Block', 'D', 'Repairing', 'Repaired'),
        (60.000001, 'Block', 'D', 'Repaired', 'Good'),
        (60.000001, 'Block', 'D', 'Good', 'Standby'),
        (60.000001, 'Node', 'Cold', 'Degraded', 'Good'),
        (60.000001, 'System', 'State_change', 'Yellow', 'Green'),
        (70.0, 'System', 'Phase_change', '--', 'Phase_change'),
        (70.0, 'System', 'Phase_change', 'Phase_change', 'Mission_failure'),
        (70.0, 'System', 'State_change', 'Green', 'Green'),
        (100.0, 'Block', 'C', 'Good', 'Done'),
        (100.0, 'Block', 'D', 'Standby', 'Done'),
        (100.0, 'System', 'Simulation_Terminate', '--', '--'),
    ])


def test_a_phase_boundary_onto_a_zero_phase_value_does_not_roll() -> None:
    """A phase boundary that leaves the block's phase value at zero does not
    roll at all: the same wake, one fewer draw.

    Why: the gate's `Phase_Change` arm carries a second condition the other two
    reasons do not -- the block's own phase value for the new phase must be
    **positive** (`block_lifecycle.py:1017-1022`, source's third copy). A
    boundary onto `0.0` puts `D` back where it already was (zero stress, so
    already parked at the combined Idle/ZeroWait site), so there is no new
    stress to absorb and no roll. It is the same "skip the draw entirely"
    claim as `initial_cost = 1.0`, arrived at from the other side of the gate.

    Setup: the previous test's fixture with `D` phased `"A: 1.0"` then
    `"A: 0.0"`, and the same `seed=SEED`.

    Expected: the promotion's roll at t=50 and nothing at the boundary --
    `Run.draws == 1`, where the positive-value run spent two -- and the same
    timeline, since the forced life at t=50 was already spent.

    Source: raptor/core/engine/block_lifecycle.py:1017-1022 (the phase-value
      condition and its early return); Raptor 7 (the second
      copy of the startUpFailures branch)
    """
    result = run(_phased_cold_pair(initial_cost=COST_BELOW_DRAW,
                                  phase_two_value=PHASE_VALUE_ZERO),
                 seed=SEED)

    assert result.draws == 1
    assert_timeline(result, [
        (0.0, 'System', 'State_change', '--', 'Mission'),
        (0.0, 'Block', 'C', '--', 'Good'),
        (0.0, 'Block', 'D', '--', 'Good'),
        (0.0, 'Block', 'D', 'Good', 'Standby'),
        (0.0, 'Node', 'Cold', '--', 'Good'),
        (50.0, 'Block', 'C', 'Good', 'Failed'),
        (50.0, 'Block', 'C', 'Failed', 'Repairing'),
        (50.0, 'Block', 'D', 'Standby', 'Good'),
        (50.0, 'Node', 'Cold', 'Good', 'Degraded'),
        (50.0, 'System', 'State_change', 'Mission', 'Yellow'),
        (50.000001, 'Block', 'D', 'Good', 'Failed'),
        (50.000001, 'Block', 'D', 'Failed', 'Repairing'),
        (50.000001, 'Node', 'Cold', 'Degraded', 'Bad'),
        (50.000001, 'Node', 'end', '--', 'Bad'),
        (50.000001, 'System', 'State_change', 'Yellow', 'Red'),
        (60.0, 'Block', 'C', 'Repairing', 'Repaired'),
        (60.0, 'Block', 'C', 'Repaired', 'Good'),
        (60.0, 'Node', 'Cold', 'Bad', 'Degraded'),
        (60.0, 'Node', 'end', 'Bad', 'Good'),
        (60.0, 'System', 'State_change', 'Red', 'Yellow'),
        (60.000001, 'Block', 'D', 'Repairing', 'Repaired'),
        (60.000001, 'Block', 'D', 'Repaired', 'Good'),
        (60.000001, 'Block', 'D', 'Good', 'Standby'),
        (60.000001, 'Node', 'Cold', 'Degraded', 'Good'),
        (60.000001, 'System', 'State_change', 'Yellow', 'Green'),
        (70.0, 'System', 'Phase_change', '--', 'Phase_change'),
        (70.0, 'System', 'Phase_change', 'Phase_change', 'Mission_failure'),
        (70.0, 'System', 'State_change', 'Green', 'Green'),
        (100.0, 'Block', 'C', 'Good', 'Done'),
        (100.0, 'Block', 'D', 'Standby', 'Done'),
        (100.0, 'System', 'Simulation_Terminate', '--', '--'),
    ])


# ---------------------------------------------------------------------------
# Claim 8: the no-stream guard, which no model can reach
# ---------------------------------------------------------------------------

def test_the_no_stream_path_hands_back_the_life_it_was_given() -> None:
    """Called without a `SysStream[10]`, the redraw hands back exactly the life
    it was given -- no draw, no forced failure, whatever the cost.

    Why: `startup_failure_redraw` guards its own body with
    `if rng_sys10 is None: return fail_time_left` (`block_lifecycle.py:75-77`),
    belt and braces against the same condition its caller has already checked
    (`block_lifecycle.py:1013`). It is **not reachable from a model**, and
    saying so is the point of this test rather than papering over it: the flag
    that causes the call is set only by the presence of the `StartUpFaiLures`
    trigger, a model with a trigger cannot run with fewer than eleven system
    streams (`kickoff._start_triggers` reads `sys_streams[11]` once per
    trigger, `kickoff.py:775`), and eleven streams are built as keys 1..11, so
    key 10 -- and therefore the stream -- always exists. Pinning the guard's
    contract directly is the honest form of the claim.

    Setup: `D` straight out of a harness-built and harness-run model (so the
    block is the engine's own object, not a hand-made stand-in), a life of
    12.5 h to return, and `cost_analysis` off so the two `stats` arguments
    cannot do anything either.

    Expected: 12.5 comes back unchanged.

    Source: raptor/core/engine/block_lifecycle.py:75-77 (the no-stream guard);
       (the caller's own check of the same condition)
    """
    result = run(_cold_pair(initial_cost=COST_BELOW_DRAW), seed=SEED)
    model = load_model(result.rbd)
    block = model.blocks_by_id[2]
    assert block.name == "D" and block.initial_cost == COST_BELOW_DRAW

    assert startup_failure_redraw(block, None, 12.5, False, BlockStatistics()) == 12.5
