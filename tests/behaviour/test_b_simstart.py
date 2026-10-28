"""**`sim_start=1` -- "completely random": starting a block part-way through its cycle.**

The one mechanism on this branch that the behaviour suite had no test for at
all, and the largest single block of coverage the fidelity tier uniquely
holds: 25 of the 120 lines in `block_lifecycle.py` that only a whole-output
comparison against a real Raptor capture executes.

Every other rung in `spine.py` starts its blocks *Running*. This one starts
them somewhere in the middle of a fail / pre-LDT / repair / post-LDT cycle,
which is what Raptor calls a "completely random" start condition
(ported at `block_lifecycle.py:324-350`).

The rule
========

`get_start_cond` draws two Uniform(0,1) values from the **failure** stream,
plus the block's failure life, then picks a point uniformly over the whole
cycle by comparing the *first* draw against each leg's cumulative share:

    fm     = mean of the failure distribution
    pre    = mean of the pre-LDT distribution
    repair = mean of the repair distribution
    post   = mean of the post-LDT distribution
    cycle  = fm + pre + repair + post

    draw <  fm/cycle                  -> Running
    draw < (fm + pre)/cycle           -> preLDT
    draw < (fm + pre + repair)/cycle  -> repairing
    otherwise                         -> postLDT

The arithmetic uses the distribution **means** while the value the block
actually starts with is a **draw** scaled by the *second* uniform. Both appear in the same function, and confusing
them is the easiest mistake to make here.

Each leg is identifiable by the state the block **opens** in, which is what
these tests assert:

===========  =====================  =========================================
leg          opening state          distinguishing row
===========  =====================  =========================================
Running      `Good`                 `Failed -> Repairing`
preLDT       `RepHold`              `RepHold -> End_Partial_pre`
repairing    `Repairing`            no LDT row at all
postLDT      `RepHold`              `RepHold -> End_Partial_pos`
===========  =====================  =========================================

`End_Partial_pre` and `End_Partial_pos` are the 15-character truncations the
event log writes for `End_Partial_pre-LDT` and `End_Partial_post-LDT`
(`eventlog.py::_pad`), so the preLDT and postLDT legs are told apart by one
character. That truncation was itself the subject of a bug on this branch --
the harness read the untruncated struct field instead of the log text -- so a
test that depends on the distinction is also a test that the vocabulary is
right.

Why these numbers are checkable by hand
=======================================

Every model here draws `rand_draw1` then `rand_draw2` from the same place in
the same stream, so **`rand_draw2` is 0.18981875 in all six tests** and the
start value in each leg is that leg's mean scaled by it:

    18 x 0.1898187487386167 =  3.4167374772951007  (Running:  scaled life)
    40 x 0.1898187487386167 =  7.592749949544668   (preLDT:    the delay)
    60 x 0.1898187487386167 = 11.389124924317002   (repairing: the repair)
    200 x 0.1898187487386167 = 37.963749747723340  (postLDT:   post delay)

So a reviewer can confirm any literal in this file by multiplying. That is the
property that makes the tests worth having: the expectation is arithmetic, not
a transcript.

The times are written to the last bit because `assert_timeline` compares
tuples exactly. A literal rounded to the log's own six decimals -- 3.416737 --
is *not* equal to the float the engine computed, 3.4167374772951007, and a test
that looked like it was comparing arithmetic was in fact comparing nothing.
The ladder's own fixtures get away with `50.0` and `60.0` because a `Fixed`
life is exact; anything scaled by a uniform is not.

How each leg is reached
=======================

A draw lands in a leg only if that leg's share of the cycle is large enough to
catch a Uniform(0,1). So **each test makes its target leg dominate the
cycle** and lets the draw fall where it may. Every share is stated in its
docstring, and that is what makes the tests robust: they do not depend on a
seed landing in a particular window, they make the window most of `[0, 1)`.

Two arms deserve their own test because they are where the arithmetic is
easiest to get wrong:

* **`repair_distro == NONE` (18).** The source sets `cycleTime := failMean`
  with no repair leg, so `fm/cycle == 1.0` and the *first* cut swallows
  everything. Crucially the other three branches draw from the **repair**
  stream and none is reached -- which is only observable if those
  distributions are stochastic, so the last test uses an `Exponential` LDT and
  asserts on `Run.draws`.
* **A zero-length leg.** `Fixed(0)` is a legal mean, and a zero leg makes its
  cut coincide with the previous one. Used to collapse the cycle to exactly
  the legs under test.

Source: raptor/core/engine/block_lifecycle.py:320-356
(`get_start_cond`, the `t == 1` arm), against Raptor 7.
"""
from __future__ import annotations

from .harness import FIXED, assert_timeline, run

#: Distribution code 4 -- Exponential, mean `p[0]`. Used only where a test
#: needs a *stochastic* leg, so that entering it would be visible in
#: `Run.draws`. Everywhere else every distribution is `Fixed`.
EXPONENTIAL = 4

#: The seed every test here runs at. `sim_start=1` draws Uniform(0,1) twice
#: from the failure stream, so these cannot be all-`Fixed` (rule B2). The seed
#: is pinned so each test is a *regression* test for the arithmetic: with a
#: target share of 0.9 the assertion holds for 90% of all seeds, and the tenth
#: that misses is caught by a wider share, not by luck.
SEED = 0

#: The second uniform at `SEED`, which scales every start value in this file.
#: Quoted so a reader can multiply rather than trust. See the module docstring.
SCALE = 0.1898187487386167

#: Uniform(0,1) is drawn twice before the failure life, and the
#: failure life is `Fixed` in every model here, so a run that enters no
#: repair-stream leg consumes exactly two draws. Asserted rather than assumed:
#: it is the only way to tell "took the Running cut" from "took a different cut
#: that happened to look the same".
TWO_DRAWS = 2


def _one_block(*, fail: float, repair: float,
               pre: tuple[int, float] | None = None,
               post: tuple[int, float] | None = None,
               repair_dist: int = FIXED, run_length: float,
               ) -> dict:
    """A one-block model whose cycle is exactly the four legs given.

    Every leg is `Fixed` unless a test passes an explicit distribution, so the
    block's life and repair are exact and the timeline can be checked against
    arithmetic rather than against a distribution.

    `pre` and `post` are `(distribution_code, mean)`. They are load delay
    times, and they are what a block in the preLDT or postLDT leg waits out
    before its repair clock starts.

    `run_length` is required rather than defaulted: every test in this file
    wants a run that ends just after the rows it asserts on, and a default
    would let a second cycle leak into the literal.
    """
    block: dict = {
        "name": "A",
        "fail_dist": FIXED, "fail_params": (fail, 0.0, 0.0),
        "repair_dist": repair_dist, "repair_params": (repair, 0.0, 0.0),
        "sp_type": "Infinite", "sim_start": 1,
    }
    if pre is not None:
        block["pre_ldt_dist"] = pre[0]
        block["pre_ldt_params"] = (pre[1], 0.0, 0.0)
    if post is not None:
        block["post_ldt_dist"] = post[0]
        block["post_ldt_params"] = (post[1], 0.0, 0.0)
    return {
        "name": "simstart",
        "system": {"run_length": run_length, "num_runs": 1,
                   "termination": "TimeSim"},
        "blocks": [block],
        "nodes": [{"name": "Start", "type": 1, "n": 0, "out": 1},
                  {"name": "End", "type": 3, "n": 1, "out": 0}],
        "links": [{"from": "Start", "to": "A"},
                  {"from": "A", "to": "End"}],
    }


def test_a_missing_repair_leg_collapses_the_cycle_so_the_block_starts_running() -> None:
    """`repairDistro = NONE` makes the Running cut `1.0`, so every seed starts Running.

    Why: Raptor 7 special-cases a missing repair leg --
    `IF repairDistro=18: cycleTime:=failMean`. With no pre- or post-LDT either,
    the cycle *is* the failure mean, so `fm/cycle == 1.0` and the first cut
    swallows the whole of `[0, 1)`. **No seed can reach another leg**, which is
    what makes this the one test in the file with no dominant-leg trick in it.

    Setup: one block, `sim_start=1`, `repair_dist=18` (`NONE`), `Fixed` 20 h
    life, no LDT columns, a 10 h run.

    Expected: opens `Good`, fails at 3.796375 (= 20 x `SCALE`, the scaled
    remaining life), and goes straight to `Done` -- with no repair leg there is
    nothing to repair with, so the system reports `Out_of_spares` and the run
    ends in `Mission_failure` rather than recovering.

    Source: raptor/core/engine/block_lifecycle.py:328-336 -- `if
    block.repair_distro == int(Distribution.NONE): cycle_time =
    fail_mean(block)`, porting Raptor 7.
    """
    result = run(_one_block(fail=20.0, repair=0.0, repair_dist=18,
                            run_length=10.0), seed=SEED)
    assert result.draws == TWO_DRAWS, (
        f"expected {TWO_DRAWS} draws, got {result.draws}")
    assert_timeline(result, [
        (0.0, "A", "--", "Good"),
        (3.796374974772334, "A", "Good", "Failed"),
        (3.796374974772334, "A", "Failed", "Done"),
        (3.796374974772334, "Node", "end", "--", "Bad"),
        (3.796374974772334, "System", "State_change", "--", "Red"),
        (3.796374974772334, "System", "Permanently_Down", "--", "Out_of_spares"),
        (10.0, "A", "Done", "Done"),
        (10.0, "System", "Phase_change", "--", "Mission_failure"),
        (10.0, "System", "Simulation_Terminate", "--", "--"),
    ])


def test_the_running_leg_opens_the_block_good_with_a_life_scaled_by_the_second_uniform() -> None:
    """A failure life that is 90% of the cycle starts the block Running, mid-life.

    Why: the Running cut is at `fm/cycle`. With `fm = 18` and a 2 h repair the
    cycle is 20, so the window is `[0, 0.9)` -- **90% of all draws**, so the
    assertion is about the arithmetic rather than about a lucky seed.

    The block starts with `fail_time_left = time_to_fail * rand_draw2`, a
    *scaled* life rather than the whole 18 h, which is why it fails at 3.4167374772951007
    and not at 18. The scaling and the cut are in the same return statement, so
    a test that checked only "it opened Running" would miss a regression in the
    scaling.

    Setup: one block, `sim_start=1`, `Fixed` 18 h life, `Fixed` 2 h repair, an
    8 h run so exactly one cycle fits.

    Expected: opens `Good`, fails at 3.4167374772951007 (= 18 x `SCALE`), repairs for
    exactly 2 h, and is `Good` again at 5.416737.

    Source: raptor/core/engine/block_lifecycle.py:337-339 -- `if rand_draw1 <
    fm / cycle_time: return StartCond("Running", fail_time_left=time_to_fail
    * rand_draw2)`, porting Raptor 7.
    """
    result = run(_one_block(fail=18.0, repair=2.0, run_length=8.0), seed=SEED)
    assert result.draws == TWO_DRAWS
    assert_timeline(result, [
        (0.0, "A", "--", "Good"),
        (3.4167374772951007, "A", "Good", "Failed"),
        (3.4167374772951007, "A", "Failed", "Repairing"),
        (3.4167374772951007, "Node", "end", "--", "Bad"),
        (3.4167374772951007, "System", "State_change", "--", "Red"),
        (5.416737477295101, "A", "Repairing", "Repaired"),
        (5.416737477295101, "A", "Repaired", "Good"),
        (5.416737477295101, "Node", "end", "Bad", "Good"),
        (5.416737477295101, "System", "State_change", "Red", "Green"),
        (8.0, "A", "Good", "Done"),
        (8.0, "System", "Phase_change", "--", "Mission_failure"),
        (8.0, "System", "Simulation_Terminate", "--", "--"),
    ])


def test_the_pre_ldt_leg_opens_the_block_in_rephold_behind_a_pre_delay() -> None:
    """A 40 h load delay is 96% of the cycle, so the block opens inside the preLDT leg.

    Why: the preLDT cut runs from `fm/cycle` to `(fm + pre)/cycle`. With
    `fm = 2`, `pre = 40`, repair 6, post 0 the cycle is 48 and the window is
    `[2/48, 48/48) = [0.042, 1.0)` -- **96% of all draws**.

    The observable is the load delay itself. A block that opens preLDT logs
    `RepHold -> End_Partial_pre` and does not start its repair clock until the
    delay expires, so the 6 h repair runs from 7.592749949544668 rather than from 0.
    That is `first_pre_ldt` doing its job, and the `End_Partial_pre`
    row is the only thing distinguishing this leg from `postLDT`, which opens
    in the same state.

    Setup: one block, `sim_start=1`, `Fixed` 2 h life, `Fixed` 40 h pre-LDT,
    `Fixed` 6 h repair, a 15 h run.

    Expected: opens `RepHold`, waits out the delay to 7.592749949544668 (= 40 x
    `SCALE`), then repairs for exactly 6 h.

    Source: raptor/core/engine/block_lifecycle.py:340-343 -- the second cut,
    returning `StartCond("preLDT", start_val=time_to_repair,
    first_pre_ldt=pre_ldt * rand_draw2)`, porting Raptor 7.
    """
    result = run(_one_block(fail=2.0, repair=6.0, pre=(FIXED, 40.0),
                            run_length=15.0), seed=SEED)
    assert_timeline(result, [
        (0.0, "A", "--", "RepHold"),
        (0.0, "Node", "end", "--", "Bad"),
        (0.0, "System", "State_change", "--", "Red"),
        (7.592749949544668, "A", "RepHold", "End_Partial_pre"),
        (7.592749949544668, "A", "End_Partial_pre", "Repairing"),
        (7.592749949544668, "System", "Red_change", "--", "Red"),
        (13.592749949544668, "A", "Repairing", "Repaired"),
        (13.592749949544668, "A", "Repaired", "Good"),
        (13.592749949544668, "Node", "end", "Bad", "Good"),
        (13.592749949544668, "System", "State_change", "Red", "Green"),
        (15.0, "A", "Good", "Done"),
        (15.0, "System", "Phase_change", "--", "Mission_failure"),
        (15.0, "System", "Simulation_Terminate", "--", "--"),
    ])


def test_the_repairing_leg_opens_the_block_already_down_and_never_logs_an_ldt() -> None:
    """A 60 h repair is 94% of the cycle, so the block opens mid-repair with no delay row.

    Why: the third cut runs from `(fm + pre)/cycle` to `(fm + pre +
    repair)/cycle`. With `fm = 2`, `pre = 2`, `repair = 60`, post 0 the cycle
    is 64 and the window is `[4/64, 1.0)` = **94% of draws**.

    This leg is the one that is unmistakably not "Running": the block opens
    already down, so it never logs a `Good` state at all. A test that only
    checked "the block eventually failed" would pass on any of the four legs;
    the claim is about the *opening* state, and the absence of any
    `End_Partial_*` row is what separates it from the two delay legs.

    Setup: one block, `sim_start=1`, `Fixed` 2 h life, `Fixed` 2 h pre-LDT,
    `Fixed` 60 h repair, a 13 h run.

    Expected: opens `Repairing` (and re-logs it once at t=0, which is the
    block process starting rather than a state change), completes at 11.389124924317002
    (= 60 x `SCALE`), then `Good`. No `End_Partial_*` row anywhere.

    Source: raptor/core/engine/block_lifecycle.py:345-348 -- the third cut,
    returning `StartCond("repairing", start_val=time_to_repair * rand_draw2)`,
    porting Raptor 7.
    """
    result = run(_one_block(fail=2.0, repair=60.0, pre=(FIXED, 2.0),
                            run_length=13.0), seed=SEED)
    assert result.draws == TWO_DRAWS
    assert result.events[0].new == "Repairing", (
        f"block opened {result.events[0].new!r}, not the repairing state")
    assert not any(str(e.new).startswith("End_Partial")
                   for e in result.events), (
        "an End_Partial_* row means a load-delay leg was taken, not repairing")
    assert_timeline(result, [
        (0.0, "A", "--", "Repairing"),
        (0.0, "Node", "end", "--", "Bad"),
        (0.0, "System", "State_change", "--", "Red"),
        (0.0, "A", "Repairing", "Repairing"),
        (11.389124924317002, "A", "Repairing", "Repaired"),
        (11.389124924317002, "A", "Repaired", "Good"),
        (11.389124924317002, "Node", "end", "Bad", "Good"),
        (11.389124924317002, "System", "State_change", "Red", "Green"),
        (13.0, "A", "Good", "Done"),
        (13.0, "System", "Phase_change", "--", "Mission_failure"),
        (13.0, "System", "Simulation_Terminate", "--", "--"),
    ])


def test_the_post_ldt_leg_is_whatever_is_left_after_three_cuts() -> None:
    """A 200 h post-delay leaves only the last cut, and the block opens already recovered.

    Why: `postLDT` is the `otherwise` -- whatever survives three cuts -- so
    unlike the other three it has no cut of its own and can only be pinned by
    making the other legs negligible. With `fm = 2`, `pre = 2`, `repair = 2`,
    `post = 200` the cycle is 206 and the window is `[6/206, 1.0)` = **97% of
    draws**.

    The block has already survived the whole fail/pre/repair cycle by the time
    it opens, so the row after the opening `RepHold` is `End_Partial_pos ->
    Good` rather than `-> Repairing`. That single difference from the preLDT leg
    is the entire claim, and it is the reason both delay legs need a test.

    Setup: one block, `sim_start=1`, `Fixed` 2 h life, 2 h pre-LDT, 2 h repair,
    200 h post-LDT, and a 39 h run so the delay elapses but nothing follows.

    Expected: opens `RepHold`, the delay runs to 37.963749747723340 (= 200 x `SCALE`),
    and the block comes back `Good` with no repair at all.

    Source: raptor/core/engine/block_lifecycle.py:349-350 -- the `otherwise`
    arm, `post_ldt = rng_repair.draw_number(Distribution(block.post_dist),
    block.post_params)` then `StartCond("postLDT", start_val=post_ldt *
    rand_draw2)`, porting Raptor 7.
    """
    result = run(_one_block(fail=2.0, repair=2.0, pre=(FIXED, 2.0),
                            post=(FIXED, 200.0), run_length=39.0), seed=SEED)
    assert_timeline(result, [
        (0.0, "A", "--", "RepHold"),
        (0.0, "Node", "end", "--", "Bad"),
        (0.0, "System", "State_change", "--", "Red"),
        (37.96374974772334, "A", "RepHold", "End_Partial_pos"),
        (37.96374974772334, "A", "End_Partial_pos", "Good"),
        (37.96374974772334, "Node", "end", "Bad", "Good"),
        (37.96374974772334, "System", "State_change", "Red", "Green"),
        (39.0, "A", "Good", "Done"),
        (39.0, "System", "Phase_change", "--", "Mission_failure"),
        (39.0, "System", "Simulation_Terminate", "--", "--"),
    ])


def test_a_collapsed_cycle_consumes_no_repair_stream_draw() -> None:
    """With a stochastic LDT, exactly two draws proves no delay leg was entered.

    Why: the five models above all consume two draws whether or not a delay
    leg is taken, because a `Fixed` draw touches no stream -- so their draw
    counts cannot actually *see* the claim they appear to make. Giving the
    pre-LDT an `Exponential` distribution fixes that: had the preLDT leg been
    entered, `block_lifecycle.py:341` would have drawn a real load delay and
    the count would be three.

    So this is the one test in the file where `draws` is the assertion rather
    than a cross-check, and it is the direct test of the `repairDistro = NONE`
    arithmetic: the source never reaches the legs that draw from the repair
    stream, and a wasted draw would show it. It is also the only test that
    needs a stochastic distribution, so it is where rule B2's "Fixed unless
    the mechanism *is* stochastic" exemption is spent.

    Setup: one block, `sim_start=1`, `repair_dist=18`, `Fixed` 20 h life, and a
    40 h **Exponential** pre-LDT that must not be drawn.

    Expected: the same timeline as the collapsed-cycle test, and `draws == 2` --
    the two Uniform(0,1) values and nothing else.

    Source: raptor/core/engine/block_lifecycle.py:340-343 (the only draws from
    the repair stream anywhere in the `t == 1` arm) against, which
    returns before any of them.
    """
    result = run(_one_block(fail=20.0, repair=0.0, repair_dist=18,
                            pre=(EXPONENTIAL, 40.0), run_length=10.0),
                 seed=SEED)
    assert result.draws == TWO_DRAWS, (
        f"{result.draws} draws: the pre-LDT stream was drawn, so the cycle "
        f"did not collapse and the Running cut was not 1.0")
    assert_timeline(result, [
        (0.0, "A", "--", "Good"),
        (3.796374974772334, "A", "Good", "Failed"),
        (3.796374974772334, "A", "Failed", "Done"),
        (3.796374974772334, "Node", "end", "--", "Bad"),
        (3.796374974772334, "System", "State_change", "--", "Red"),
        (3.796374974772334, "System", "Permanently_Down", "--", "Out_of_spares"),
        (10.0, "A", "Done", "Done"),
        (10.0, "System", "Phase_change", "--", "Mission_failure"),
        (10.0, "System", "Simulation_Terminate", "--", "--"),
    ])