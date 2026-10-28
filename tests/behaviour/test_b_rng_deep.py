"""What each distribution code does to a block's **failure clock**.

The ladder puts the generator at rung S0,
"pure functions, no scheduler, no model", and its `test_b_draws.py` row asks
for *range* claims ("one draw per family lands in range; monotone in its shape
parameter"). That is the right shape for a family sweep, and it is not what
this file is for. **This file's subject is the engine-visible consequence of
one named distribution code on one named block**, so every claim here is:

> a block whose failure life is drawn from *this* code with *these* parameters
> fails at *this* sim time.

The block starts its clock at `SimTime = 0`, so **the failure time IS the drawn
life** — the number the generator produced is the number in the event log,
with no engine arithmetic in between. That is what makes the claim worth
pinning and what lets the expectation be a literal.

**Two levels of assertion, and why they differ.**

* *Integer-valued codes* (`Binomial`, `UniformInt`, `Poisson`) and the
  clamped `Fixed` case produce **exact** lives, so their whole nine-row
  timeline is asserted through `assert_timeline` with the literal inline. No
  tolerance is involved and none is needed.
* *Real-valued codes* (`Beta`, `Erlang`, `Gamma`, `Pearson6`, `Empirical`,
  `ExtremeValue`, `Laplace`) go through `math.log`/`exp`/`pow`, whose last ulp
  is the platform libm's business, so their times are not bit-reproducible off
  this machine. Those tests assert the drawn life against the full-precision
  literal the port produced, with `abs=1e-9` hours — 3.6 µs on a 0.2-hour
  life, and about 1e-8 of the longest life here. A wrong transform moves the
  value by orders of magnitude more than that, so the tolerance cannot absorb
  a regression; it only buys independence from the libm's last bit.

**Every model here is stochastic, so rule B2's exception applies and it is
named explicitly in each test's docstring**: an explicit `seed=` on
`harness.run`, and a stated tolerance. `seed=0` means "no salt at all", so each
block's fail stream is exactly `fetch_raptor_seed(9)` — the port's own seed
table, i.e. the stream real Raptor would have used. Two tests use `seed=2` and
`seed=1010` because *the property being pinned needs that stream* (the
opposite Laplace arm; a Poisson redraw), not because the default stream was
inconvenient.

**What is already covered elsewhere, so this file does not repeat it.**
`tests/unit/test_rng.py` owns the Lehmer recurrence itself, the seed table and
`jump_ahead`'s composition; the fidelity tier owns the golden draw sequences.
`Normal`, `ChiSquare`, `Exponential`, `LogNormal`, `Triangular`, `Weibull`,
`Bernoulli`, `None` and `ExponentialPower` already reach their dispatch arms
through the corpus models the ladder already runs, so there is no claim for
them here. Every *other* dispatch arm in `RandomGen._dispatch_draw`, and every
primitive it calls that the corpus models never configure, is below.

**One row of `rng.py` that no fixture can reach, and one asymmetry worth
recording, rather than forcing:**

* `rng.py:457-459` — `Laplace`'s fall-through arc and the
  `raise ValueError(f"Unknown distribution: {distro!r}")` behind it. Every
  call site in the engine converts first (`Distribution(trigger.dist)`,
  `Distribution(phase.dist)`, `Distribution(block.fail_distro)`), so
  `draw_number` is only ever handed a member of the 1..22 enum and can never
  fall off the end of the `if` chain. That is also why there is no
  `test_an_unknown_distribution_*` test here: writing one would mean calling
  `draw_number` with something the engine cannot produce.
* `rng.py:151-154` — `_neg_log`'s `else math.inf` arm, reached only because
  `Erlang` is the one primitive that forms a **product** of uniforms rather
  than a sum of their logs. `Gamma` deliberately does the latter
  (`_sum_neg_log`), so a whole-shape `Gamma(1000, ...)` still gives a finite
  life while `Erlang(1000, ...)` gives `+inf`. The `Erlang` tests below are
  that asymmetry seen from both sides.

**Cost.** One block, two nodes, one 100-hour run per test, `sp_type="None"`
so the single failure is also the block's `Done` and the timeline stops
growing. Measured: 0.13–3.4 ms per test, 35 ms for the file (see the report),
well inside B4's 100 ms.
"""
from __future__ import annotations

import pytest

from raptor.core.rng import Distribution, RaptorStream, jump_ahead

from . import harness
from .harness import FIXED, assert_timeline

#: The model every test here runs: `Start -> A -> End`, one block. `sp_type
#: = "None"` means the block has no sparing at all, so its one failure is also
#: its `Done "No_spares"` and the timeline stops there -- which is what keeps
#: the literal nine rows rather than ninety.
def _life_model(code: Distribution, params: tuple[float, ...]) -> dict:
    return {
        "system": {"run_length": 100.0, "num_runs": 1, "termination": "TimeSim"},
        "blocks": [{
            "name": "A", "fail_dist": int(code), "fail_params": list(params),
            "repair_dist": FIXED, "repair_params": [10.0], "sp_type": "None",
        }],
        "nodes": [{"name": "Start", "type": 1, "n": 0, "out": 1},
                  {"name": "End", "type": 3, "n": 1, "out": 0}],
        "links": [{"from": "Start", "to": "A"}, {"from": "A", "to": "End"}],
    }


def _life(run: harness.Run) -> float:
    """The one failure time in *run* -- which is the life that was drawn.

    `sp_type="None"` means there is exactly one: the block has no spare, so
    its first failure is its last.
    """
    failures = [event.time for event in harness.events(run, "Block:A")
                if event.new == "Failed"]
    assert len(failures) == 1, (
        f"expected exactly one failure in this model, got {failures!r}")
    return failures[0]


def test_a_beta_life_is_the_ratio_of_two_whole_shape_gamma_draws() -> None:
    """A block whose failure life is `Beta(2, 5)` fails at 0.200927509851 hours.

    Why: `Beta` is two `Gamma(k, k)` draws -- mean *is* the shape, which is
    why `beta.f`'s own `gamma.f(K1, K1, STREAM)` call passes the shape twice --
    and the draw is `x / (x + y)`, so it is a probability and can never leave
    `(0, 1)`. A whole-shape `Gamma(k, k)` costs `k + 1` uniforms -- the
    burn draw plus `k` summed logs -- so `Beta(2, 5)` costs `(2 + 1) + (5 + 1)
    = 9`, and the second gamma is the more expensive one because `k2` is the
    larger shape.

    Setup: 1 block, `fail_dist=1` (`Beta`) with `fail_params=[2.0, 5.0]`, a
    `Fixed` repair, no spares. `seed=0`, i.e. the port's own seed table with
    no salt (`driver.make_rng`).

    Expected: the failure at 0.200927509851 h, tolerance `abs=1e-9`.

    Source: raptor/core/rng.py:262-270 (_beta) (_gamma's whole-shape
    branch); the `WHEN 1` arm of `DrawNumber`'s `CASE currentDistro`,
   reached at rng.py:413-414
    """
    run = harness.run(_life_model(Distribution.BETA, (2.0, 5.0)), seed=0)
    assert _life(run) == pytest.approx(0.200927509850968, abs=1e-9)


def test_a_binomial_life_counts_the_ten_uniforms_at_or_below_one_half() -> None:
    """A `Binomial(10, 0.5)` life is `6`, and it costs exactly ten uniforms.

    Why: `_binomial` is `sum(1 for _ in range(n) if uniform() <= p)` -- the
    comparison is `<=`, matching the source's `when random.f(STREAM) <= P`, so
    a uniform landing exactly on `p` counts. It draws all `n` uniforms even
    once the outcome cannot change, which is what makes the draw count part of
    the claim rather than an accident.

    Setup: 1 block, `fail_dist=3` (`Binomial`), `fail_params=[10.0, 0.5]`, no
    spares. `seed=0`.

    Expected: ten stream draws, and the whole timeline exactly as below --
    `Binomial` returns an integer, so the life and every sim time derived from
    it are exact and no tolerance is involved.

    Source: raptor/core/rng.py:276-281 (_binomial) (the `WHEN 3`
    dispatch arm)
    """
    run = harness.run(_life_model(Distribution.BINOMIAL, (10.0, 0.5)), seed=0)
    assert run.draws == 10
    assert_timeline(run, [
        (0.0, "Block", "A", "--", "Good"),
        (6.0, "Block", "A", "Good", "Failed"),
        (6.0, "Block", "A", "Failed", "Done"),
        (6.0, "Node", "end", "--", "Bad"),
        (6.0, "System", "State_change", "--", "Red"),
        (6.0, "System", "Permanently_Down", "--", "Out_of_spares"),
        (100.0, "Block", "A", "Done", "Done"),
        (100.0, "System", "Phase_change", "--", "Mission_failure"),
        (100.0, "System", "Simulation_Terminate", "--", "--"),
    ])


def test_an_erlang_life_is_the_negated_log_of_the_product_of_three_uniforms() -> None:
    """An `Erlang(shape=3, scale=2)` life is 5.733948382315 hours, from three draws.

    Why: `Erlang` has its own routine rather than reusing `_gamma`'s integer
    branch, and it forms a **product** of `k` uniforms and takes one `-ln` of
    the product. So it costs exactly `k` draws -- no burn draw, unlike
    `Gamma` -- and that asymmetry is the claim.

    Setup: 1 block, `fail_dist=5` (`Erlang`), `fail_params=[3.0, 2.0]`, no
    spares. `seed=0`.

    Expected: three stream draws and a life of 5.733948382315 h, tolerance
    `abs=1e-9`.

    Source: raptor/core/rng.py:237-246 (_erlang) (the `WHEN 5`
    arm); `SetStats`'s `WHEN 5` mean `p[0]*p[1]`
    fixes `p[0]` as the shape and `p[1]` as the scale
    """
    run = harness.run(_life_model(Distribution.ERLANG, (3.0, 2.0)), seed=0)
    assert run.draws == 3
    assert _life(run) == pytest.approx(5.73394838231508, abs=1e-9)


def test_an_erlang_life_whose_uniform_product_underflows_is_infinite() -> None:
    """An `Erlang(shape=1000, scale=1)` life never expires, so the block never fails.

    Why: a product of 1000 uniforms is around `exp(-1000)`, which is far below
    the smallest subnormal double, so it flushes to `0.0`. `_neg_log` guards
    exactly that and returns `math.inf` rather than letting `math.log(0.0)`
    raise -- this is the `1.#INF00` the real engine records for a large-shape
    Erlang draw. The engine therefore gets an infinite life, schedules no
    failure, and the run closes its mission as a **success**.

    Setup: 1 block, `fail_dist=5` (`Erlang`), `fail_params=[1000.0, 1.0]`, no
    spares. `seed=0`.

    Expected: exactly 1000 stream draws consumed, no `Failed` row anywhere, and
    the four-row timeline below -- `Mission_success`, not `Mission_failure`,
    because the system is still Green when the run ends.

    Source: raptor/core/rng.py:151-154 (_neg_log's underflow arm, which
    `rng.py`'s own module docstring ties to the real `LargeShape1000Probe`)
 (_erlang's product)
    """
    run = harness.run(_life_model(Distribution.ERLANG, (1000.0, 1.0)), seed=0)
    assert run.draws == 1000
    assert_timeline(run, [
        (0.0, "Block", "A", "--", "Good"),
        (100.0, "Block", "A", "Good", "Done"),
        (100.0, "System", "Phase_change", "--", "Mission_success"),
        (100.0, "System", "Simulation_Terminate", "--", "--"),
    ])


def test_a_uniform_integer_life_is_one_uniform_and_no_further_rounding() -> None:
    """A `UniformInt(1, 10)` life is `6`, from a single uniform.

    Why: the transform is `lo + trunc(U * (hi - lo + 1))` -- the span is
    `hi - lo + 1`, so `hi` itself is reachable and `lo` is the floor, and the
    `int()` is a truncation, not a rounding. One uniform, one life.

    Setup: 1 block, `fail_dist=9` (`UniformInt`), `fail_params=[1.0, 10.0]`, no
    spares. `seed=0`.

    Expected: exactly one stream draw and the exact timeline below. No
    tolerance: `UniformInt` returns an integer.

    Source: raptor/core/rng.py:272-274 (_uniform_int) (the
    `WHEN 9` arm)
    """
    run = harness.run(_life_model(Distribution.UNIFORM_INTEGER, (1.0, 10.0)), seed=0)
    assert run.draws == 1
    assert_timeline(run, [
        (0.0, "Block", "A", "--", "Good"),
        (6.0, "Block", "A", "Good", "Failed"),
        (6.0, "Block", "A", "Failed", "Done"),
        (6.0, "Node", "end", "--", "Bad"),
        (6.0, "System", "State_change", "--", "Red"),
        (6.0, "System", "Permanently_Down", "--", "Out_of_spares"),
        (100.0, "Block", "A", "Done", "Done"),
        (100.0, "System", "Phase_change", "--", "Mission_failure"),
        (100.0, "System", "Simulation_Terminate", "--", "--"),
    ])


def test_a_pearson6_life_is_a_scaled_ratio_of_two_whole_shape_gamma_draws() -> None:
    """A `Pearson6(shape=3, shape2=2, scale=1.5)` life is 1.513047576562 hours.

    Why: `Pearson6` is the ratio of two `Gamma(shape, shape)` draws raised to
    the scale, computed as `exp(log(scale) + log(g1) - log(g2))` so the ratio
    is formed in logarithms and cannot overflow. The `.rbd` column order is
    `(shape, shape2, scale)`, which is why `fail_params` reads `[3, 2, 1.5]`
    while the method signature is `(scale, shape, shape2)`.

    Setup: 1 block, `fail_dist=12` (`Pearson6`), `fail_params=[3.0, 2.0, 1.5]`,
    no spares. `seed=0`.

    Expected: seven stream draws -- `Gamma(3, 3)` is `3 + 1` uniforms and
    `Gamma(2, 2)` is `2 + 1` -- and a life of 1.513047576562 h, tolerance
    `abs=1e-9`.

    Source: raptor/core/rng.py:316-320 (pearson6) (the `WHEN 12`
    arm)
    """
    run = harness.run(_life_model(Distribution.PEARSON6, (3.0, 2.0, 1.5)), seed=0)
    assert run.draws == 7
    assert _life(run) == pytest.approx(1.5130475765618292, abs=1e-9)


def test_a_poisson_life_at_or_below_six_counts_uniforms_until_their_product_drops() -> None:
    """A `Poisson(3)` life is `3`, from four uniforms.

    Why: `mu <= 6` is the exact-count branch, not an approximation: it
    multiplies uniforms until their product falls below `exp(-mu)` and returns
    how many were needed. So the draw count is `n + 1` uniforms for a count of
    `n`, which is a much stronger claim than the value alone.

    Setup: 1 block, `fail_dist=13` (`Poisson`), `fail_params=[3.0]`, no
    spares. `seed=0`.

    Expected: exactly four stream draws, and the exact timeline below --
    `Poisson` returns an integer, so nothing here is approximate.

    Source: raptor/core/rng.py:371-378 (the `mu <= 6` counting loop) and
     (the `WHEN 13` arm)
    """
    run = harness.run(_life_model(Distribution.POISSON, (3.0,)), seed=0)
    assert run.draws == 4
    assert_timeline(run, [
        (0.0, "Block", "A", "--", "Good"),
        (3.0, "Block", "A", "Good", "Failed"),
        (3.0, "Block", "A", "Failed", "Done"),
        (3.0, "Node", "end", "--", "Bad"),
        (3.0, "System", "State_change", "--", "Red"),
        (3.0, "System", "Permanently_Down", "--", "Out_of_spares"),
        (100.0, "Block", "A", "Done", "Done"),
        (100.0, "System", "Phase_change", "--", "Mission_failure"),
        (100.0, "System", "Simulation_Terminate", "--", "--"),
    ])


def test_a_poisson_life_above_six_is_the_rounded_normal_approximation() -> None:
    """A `Poisson(9)` life is `9`, and it costs one three-uniform normal draw.

    Why: `mu > 6` switches to the source's normal approximation, `round(Normal(mu,
    sqrt(mu)))`, and keeps drawing only while the rounded value is *negative*.
    `round`, not `int`, and `sqrt(mu)` as the stdev -- so the claim is about
    the branch boundary and its three-draw normal, not about Poisson's mean.

    Setup: 1 block, `fail_dist=13` (`Poisson`), `fail_params=[9.0]`, no
    spares. `seed=0` -- the first normal draw on this stream is already
    non-negative, which is the *other* half of the property and has its own
    test below.

    Expected: exactly three stream draws (one accepted `_normal` iteration: a
    pair for the rejection test plus the fresh uniform for the radius) and a
    life of `9.0`.

    Source: raptor/core/rng.py:365-370 (the `mu > 6` approximation) and
     (the `WHEN 13` arm)
    """
    run = harness.run(_life_model(Distribution.POISSON, (9.0,)), seed=0)
    assert run.draws == 3
    assert _life(run) == 9.0


def test_a_poisson_life_above_six_is_drawn_again_until_it_is_not_negative() -> None:
    """A `Poisson(9)` life on this stream costs six uniforms, because the first
    normal draw rounds negative and is discarded.

    Why: the approximation redraws on `value >= 0`, so a first draw that
    rounds to `-1` or below is thrown away **including its uniforms** -- the
    stream is not rewound, and the discarded draw is not reported. With
    `sigma = sqrt(9) = 3` the chance of a first draw below `-0.5` is about
    0.08 %, so this needs a named stream: `seed=1010` is chosen *because* its
    first normal rounds negative, which is the entire point of the test.

    Setup: 1 block, `fail_dist=13` (`Poisson`), `fail_params=[9.0]`, no
    spares. `seed=1010`.

    Expected: six stream draws and a life of `7.0`. Six and not three is the
    point: the discarded `_normal` and the accepted one cost three uniforms
    each, because neither hit `_normal`'s *own* rejection loop. The sibling
    test above, which costs three, is the same branch without the redraw.

    Source: raptor/core/rng.py:365-370 (the `while True` / `if value >= 0`
    redraw) and raptor/core/rng.py:183-192 (_normal's own rejection loop,
    which is the other way to spend uniforms here)
    """
    run = harness.run(_life_model(Distribution.POISSON, (9.0,)), seed=1010)
    assert run.draws == 6
    assert _life(run) == 7.0


def test_an_empirical_life_interpolates_between_the_two_values_it_lands_between() -> None:
    """An `Empirical` life over the values `(1, 2, 3)` is 2.094064326026, from one uniform.

    Why: `Empirical` maps one uniform onto the piecewise-linear interpolation
    of its value list -- `(n-1) * x` picks the segment, and the fraction within
    it interpolates -- so it is *not* "pick a value at random" and one uniform
    is the whole cost. `fail_params` **is** the value list here, which is why
    there are three parameters and not three slots.

    Setup: 1 block, `fail_dist=16` (`Empirical`), `fail_params=[1.0, 2.0, 3.0]`,
    no spares. `seed=0`.

    Expected: exactly one stream draw, and a life of 2.094064326026 h --
    strictly between `2` and `3`, i.e. in the second segment -- tolerance
    `abs=1e-9`.

    Source: raptor/core/rng.py:322-338 (empirical) (the `WHEN 16`
    arm, which passes the whole parameter list through)
    """
    run = harness.run(_life_model(Distribution.EMPIRICAL, (1.0, 2.0, 3.0)), seed=0)
    assert run.draws == 1
    assert _life(run) == pytest.approx(2.0940643260255456, abs=1e-9)


def test_an_extreme_value_life_is_the_location_scaled_by_the_double_logarithm() -> None:
    """An `ExtremeValue(scale=2, location=5)` life is 6.010854842753 hours.

    Why: the transform is `location - scale * ln(-ln(U))` -- one uniform and
    two logarithms, with the `.rbd` column order `(scale, location)` matching
    the method signature rather than being reversed the way `Weibull` and
    `Triangular` are.

    Setup: 1 block, `fail_dist=21` (`ExtremeValue`), `fail_params=[2.0, 5.0]`,
    no spares. `seed=0`.

    Expected: exactly one stream draw and a life of 6.010854842753 h,
    tolerance `abs=1e-9`. Above the location, as the negative sign on `scale`
    requires when `-ln(U) > 1`.

    Source: raptor/core/rng.py:380-383 (extreme_value) (the
    `WHEN 21` arm)
    """
    run = harness.run(_life_model(Distribution.EXTREME_VALUE, (2.0, 5.0)), seed=0)
    assert run.draws == 1
    assert _life(run) == pytest.approx(6.0108548427531705, abs=1e-9)


def test_a_laplace_life_above_the_location_comes_from_the_upper_arm() -> None:
    """A `Laplace(scale=2, location=5)` life is 5.197573950976 -- above the location.

    Why: `Laplace` is two mirrored arms sharing one uniform, split at
    `x < 0.5`: the lower arm is `location + scale * ln(2x)` and the upper is
    `location - scale * ln(2 - 2x)`. Since `2 - 2x < 1` above the split, its
    logarithm is negative and the life lands **above** the location. Which arm
    ran is decided by that one uniform and nothing else, so the arm is the
    claim.

    Setup: 1 block, `fail_dist=22` (`Laplace`), `fail_params=[2.0, 5.0]`, no
    spares. `seed=0` -- its first uniform is above the split.

    Expected: exactly one stream draw and a life of 5.197573950976 h,
    tolerance `abs=1e-9`.

    Source: raptor/core/rng.py:385-390 (laplace) (the `WHEN 22`
    arm)
    """
    run = harness.run(_life_model(Distribution.LAPLACE, (2.0, 5.0)), seed=0)
    assert run.draws == 1
    assert _life(run) == pytest.approx(5.19757395097595, abs=1e-9)


def test_a_laplace_life_below_the_location_comes_from_the_lower_arm() -> None:
    """The same `Laplace(2, 5)` on a stream whose first uniform is below the
    split gives 2.367965473403 -- below the location.

    Why: the same mirror image as the upper-arm test, and it is a separate
    claim: the branch is `x < 0.5`, so **the arm is a property of the stream,
    not of the parameters**. Nothing about `(scale=2, location=5)` favours
    either arm, which is exactly why a second named stream is needed.

    Setup: 1 block, `fail_dist=22` (`Laplace`), `fail_params=[2.0, 5.0]`, no
    spares. `seed=2` -- chosen because its first uniform falls below the
    split.

    Expected: exactly one stream draw and a life of 2.367965473403 h,
    tolerance `abs=1e-9`.

    Source: raptor/core/rng.py:388-389 (the `x < 0.5` split and the lower
    arm) (the `WHEN 22` arm)
    """
    run = harness.run(_life_model(Distribution.LAPLACE, (2.0, 5.0)), seed=2)
    assert run.draws == 1
    assert _life(run) == pytest.approx(2.367965473402797, abs=1e-9)


def test_a_gamma_life_whose_shape_is_below_one_draws_no_integer_part_sum() -> None:
    """A `Gamma(shape=0.5, scale=2, location=0)` life is 1.074682295678 hours,
    from three uniforms.

    Why: `_gamma` splits the shape into `trunc(k)` and the fraction. With
    `k = 0.5` the integer part is **zero**, so the integer-part sum
    (`_sum_neg_log(kk)`) is skipped entirely and only the Ahrens-Dieter
    rejection for the fractional part runs: two uniforms for the
    `x = u**a`, `y = u**b + x` pair, one more for `y = -ln(U)`. Three draws,
    and a positive finite life -- which is the whole difference between this
    and the underflow case `Erlang` shows.

    Setup: 1 block, `fail_dist=6` (`Gamma`), `fail_params=[0.5, 2.0, 0.0]`
    (`#FParam == 3` is required for `Gamma`, and `p[0]` is the shape), no
    spares. `seed=0`.

    Expected: exactly three stream draws and a life of 1.074682295678 h,
    tolerance `abs=1e-9`.

    Source: raptor/core/rng.py:223-235 (the non-whole-`k` branch, whose
    `if kk != 0` guard is what this test reaches) (the
    `WHEN 6` arm)
    """
    run = harness.run(_life_model(Distribution.GAMMA, (0.5, 2.0, 0.0)), seed=0)
    assert run.draws == 3
    assert _life(run) == pytest.approx(1.0746822956776343, abs=1e-9)


def test_a_pearson5_life_with_a_non_positive_scale_aborts_the_run() -> None:
    """A `Pearson5` whose scale is `0` raises, and the whole run dies with it.

    Why: `pearson5` takes `1.0 / gamma_draw`, so a non-positive scale or shape
    is not merely a bad mean -- it is a guard the source put in front of the
    division, and the port keeps it. The claim that matters here is that the
    guard is **not** swallowed: the failure surfaces out of `run_simulation`
    rather than being logged as a draw and carried on from, because a life of
    `+inf` would silently disable the block forever.

    Setup: 1 block, `fail_dist=11` (`Pearson5`), `fail_params=[2.0, 0.0, 0.0]`
    (shape 2, scale 0, location 0), no spares. `seed=0`.

    Expected: `ValueError` carrying exactly
    `"Shape or scale for Pearson 5 draw <= 0"`.

    Source: raptor/core/rng.py:308-311 (pearson5's guard, whose message is
    verbatim) (the `WHEN 11` arm)
    """
    with pytest.raises(ValueError, match=r"^Shape or scale for Pearson 5 draw <= 0$"):
        harness.run(_life_model(Distribution.PEARSON5, (2.0, 0.0, 0.0)), seed=0)


def test_a_poisson_life_of_zero_aborts_the_run() -> None:
    """A `Poisson` whose `mu` is `0` raises rather than drawing an infinite count.

    Why: `mu <= 0` has no distribution to draw from -- the counting loop's
    threshold would be `exp(0) = 1`, which no product of uniforms ever falls
    below, so it would loop forever. The guard is what stops that, and it is
    checked **before** the `mu > 6` split, so it applies at `mu == 0` too.

    Setup: 1 block, `fail_dist=13` (`Poisson`), `fail_params=[0.0]`, no
    spares. `seed=0`.

    Expected: `ValueError` carrying exactly
    `"Poisson mu less than or equal to zero"`.

    Source: raptor/core/rng.py:356-364 (poisson's `mu` guard, before the
    branch split) (the `WHEN 13` arm)
    """
    with pytest.raises(ValueError, match=r"^Poisson mu less than or equal to zero$"):
        harness.run(_life_model(Distribution.POISSON, (0.0,)), seed=0)


def test_a_fixed_negative_life_is_clamped_to_one_microsecond() -> None:
    """A `Fixed` life of `-5` hours becomes `0.000001`, and the block fails in
    the first microsecond of the run -- for **zero** stream draws.

    Why: `DrawNumber` clamps a negative draw to `0.000001` before anything
    else sees it, and the clamp sits *before* the `/sd` logging, so
    `NamedRandomGen` records the clamped value. `Fixed` is code 19, whose draw
    is `param[0]` and touches no stream, so this is the one negative-life case
    the suite can pin exactly -- and it is what keeps a mis-typed parameter
    from producing a block that can never fail.

    Setup: 1 block, `fail_dist=19` (`Fixed`), `fail_params=[-5.0]`, no spares.
    `seed=0`, and `run.draws` is asserted rather than hoped for.

    Expected: no stream draws at all, and the exact timeline below.

    Source: raptor/core/rng.py:392-406 (the clamp sits, after the
    dispatch and before `/sd` logging) (the `WHEN 19` `Fixed`
    arm)
    """
    run = harness.run(_life_model(Distribution.FIXED, (-5.0,)), seed=0)
    assert run.draws == 0
    assert_timeline(run, [
        (0.0, "Block", "A", "--", "Good"),
        (1e-06, "Block", "A", "Good", "Failed"),
        (1e-06, "Block", "A", "Failed", "Done"),
        (1e-06, "Node", "end", "--", "Bad"),
        (1e-06, "System", "State_change", "--", "Red"),
        (1e-06, "System", "Permanently_Down", "--", "Out_of_spares"),
        (100.0, "Block", "A", "Done", "Done"),
        (100.0, "System", "Phase_change", "--", "Mission_failure"),
        (100.0, "System", "Simulation_Terminate", "--", "--"),
    ])


def test_a_jump_ahead_of_zero_draws_leaves_the_stream_where_it_was() -> None:
    """`jump_ahead(stream, 0)` is a no-op, and so is a negative count.

    Why: `jump_ahead` is how multi-worker batch mode gives each worker a
    disjoint segment of every stream, so its guard is `if steps > 0`: a worker
    whose segment starts at draw zero, or a caller computing an offset that
    came out negative, must land on the *original* stream rather than on
    `A**steps mod M` applied to it. `tests/unit/test_rng.py` pins the
    composition for positive counts; the zero/negative edge is here because it
    is the boundary of the same guard.

    Setup: no model. `RaptorStream(2116429302)`, which is
    `fetch_raptor_seed(1)` -- the first real seed in the table.

    Expected: the stream's next ten draws are bit-for-bit the draws of a
    second, identically-seeded stream that was never jumped.

    Source: raptor/core/rng.py:462-473 (jump_ahead's `if steps > 0` guard);
    `driver.build_engine_ctx`'s `if stream_jump:` caller guard at
    raptor/core/engine/driver.py:685-701, which is why the engine never
    reaches the zero case itself
    """
    jumped = RaptorStream(2116429302)
    untouched = RaptorStream(2116429302)
    jump_ahead(jumped, 0)
    jump_ahead(jumped, -5)
    assert [jumped.uniform() for _ in range(10)] == \
           [untouched.uniform() for _ in range(10)]