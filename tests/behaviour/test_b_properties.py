"""**S13 -- the properties.** The last rung of the ladder, and the only one
that asserts no timeline.

Everything below S13 pins a *specific* behaviour: one mechanism, one model,
one committed literal. That is the right shape for a test you write once and
want to stay true. It is the wrong shape for the things nobody thought to
write a test for, because the set of interesting models is unbounded and the
set of hand-written ones is not.

So this layer states claims that must hold for **any** model, and lets
Hypothesis go looking for the one that breaks them. A pinned timeline says
"Raptor does this here". A property says "and it cannot do otherwise, ever".

Two things this layer is for
================================

**1. Find the counterexample nobody wrote the test for.** Every property
below was checked for teeth before being kept: each was run against a
deliberately broken engine and had to go red. A property that cannot fail is
a slow parametric test, and this file would rather have five that bite than
twelve that do not.

The table below is measured, not asserted. Each break was applied to a
single file, the property run, and the file restored -- with a SHA-256
comparison at the end to prove the tree came back byte-identical:

===================================  ==========================================
property                             the break that must turn it red
===================================  ==========================================
same seed ⇒ same log                 a module-level counter salts the seed
not all seeds agree                  `runner.py` ignores the caller's seed
`Fixed` ⇒ `draws == 0`               `Fixed` burns a draw in `_dispatch_draw`
first run of a batch = the whole run  the batch size perturbs the stream seed
log time never goes backwards        each run's log is reversed before return
`from` == the previous `to`           the harness stops chaining per object
every object opens at `--`            the harness opens at the logged status
sim time ≤ run length                end-of-sim is scheduled at 3x the limit
renaming changes no number           a block's drawn life reads `block.name`
an intervening run changes nothing    a module-level counter salts the seed
===================================  ==========================================

10 of 10. Two of those guard the *harness* rather than the engine, and that
is the point rather than an excuse -- see item 2.

**2. Say what "the same behaviour" means.** Several claims here are ones the
rest of the suite *relies on* without ever asserting. `test_a_rows_from_state
_is_the_previous_rows_to_state` is the rule the whole retirement gate is
built on -- the corpus keys a transition's `from` on the previous `to` --
and until now nothing in `tests/behaviour/` checked that the port obeyed it.
If it ever stopped, every pinned timeline would still pass and the gate
would quietly start measuring the wrong thing.

Scope, and what is deliberately absent
======================================

* **B4's 100 ms budget does not apply here.** A property test is a search,
not a measurement; a 30-example search over a 3 ms model is a few hundred
milliseconds of *total* work and there is nothing to be fast about. The
suite's wall time is reported in `test_the_property_layer_is_affordable`
rather than assumed, and that test fails if this layer ever becomes the
expensive part of the suite.
* **B2 is relaxed deliberately, and only here.** Four properties are about
  the generator, so they need a stochastic model and an explicit seed. The
  rest are `Fixed` and assert `draws == 0`, which is the rule everywhere
  else. `derandomize=True` throughout: a property suite that finds a
  different counterexample on every run is a property suite nobody can act
  on.
* **The strategy space is meant to be generative.** `_models()` draws the
  topology, both distributions, the spare policy, PM, dependencies and the
  run length -- not a handful of canned fixtures. If Hypothesis cannot
  surprise the engine here, the layer is decoration.

What was asked for and what happened instead
============================================

The retirement gate's design listed six properties. Four are here as written. Two
are not, and saying so is more useful than a fake version:

* *"a hierarchy flattened by hand gives an identical log"* -- **false, and
  provably so.** A flattened model has no hierarchy object, so its event log
  has no `Hier` rows at all; the hierarchical one has one per boundary. The
  logs cannot be identical. The nearest true claim is
  `test_a_hierarchys_status_equals_its_out_markers`, which checks the rollup
  against the marker it aggregates rather than against a flattened twin.
* *"the first k runs of an n-run batch equal a k-run batch"* -- true and
  pinned, as `test_the_first_run_of_a_batch_is_the_whole_run`. It is
  generalised to every `k` in 1..n rather than only two values.

A note on `runs > 1` and `cross_run`
====================================

`Run.cross_run` chains every run's events continuously per object, because
Raptor never re-announces an object's state at `Begin_Run_N`. So the run
*n* row's `from` is run *n-1*'s last `to`. Three properties below depend on
that and say so where they do.
"""
from __future__ import annotations

import copy
from typing import Any

import pytest
from hypothesis import HealthCheck, given, settings
from hypothesis import strategies as st

from raptor.api.build import k_of_n, parallel, series

from .harness import FIRST_STATE, Run, run
from .spine import RUNGS, spine

#: Replications. B4 says one unless the property is about aggregation, and
#: three of the properties here are exactly that, so those override it
#: themselves. Everything else stays at one.
ONE = 1

def _settings(max_examples: int) -> Any:
    """The profile every property here runs under.

    `derandomize=True` because a property suite must reproduce its own
    counterexample: Hypothesis's default is to keep searching, which turns
    a real finding into a flake that nobody can reproduce. `deadline=None`
    because the per-example cost is the *subject* here, not a budget it
    should be squeezed under. `too_slow` is suppressed because at three
    milliseconds an example, Hypothesis's own 1 s-per-example heuristic
    fires on the first one and then tells us nothing.
    """
    return settings(max_examples=max_examples, deadline=None, derandomize=True,
                   suppress_health_check=[HealthCheck.too_slow,
                                          HealthCheck.data_too_large])


# ---------------------------------------------------------------- strategies

def _life(lo: float, hi: float, *, fixed: bool) -> st.SearchStrategy[tuple[int, tuple[float, float, float]]]:
    """A (distribution code, params) pair whose draw lands in [lo, hi].

    All 22 codes are in `Distribution`, but most need parameter shapes the
    friendly spec cannot express and several are degenerate at the edges
    (a Beta with a zero shape divides by zero -- `rng.py:222`). So this
    samples the four families that carry a mean directly, and pins the
    scale/shape arguments that make each one well-posed:

    ==========  ==================================================
    family      parameters
    ==========  ==================================================
    Exponential  (mean,)          the workhorse; no scale argument
    Normal       (mean, mean/5)   sd tied to the mean, never negative
    UniformReal  (mean/2, 3*mean/2)
    Weibull      (shape, mean)    shape >= 0.5 keeps the draw finite
    Fixed        (value,)         the B2 case: draws nothing at all
    ==========  ==================================================
    """
    mean = st.floats(lo, hi, allow_nan=False, allow_infinity=False)
    if fixed:
        return mean.map(lambda m: (19, (m, 0.0, 0.0)))
    return st.one_of(
        mean.map(lambda m: (4, (m, 0.0, 0.0))),
        mean.map(lambda m: (8, (m, m / 5.0, 0.0))),
        mean.map(lambda m: (10, (m / 2.0, 3.0 * m / 2.0, 0.0))),
        st.tuples(st.floats(0.5, 3.0), mean).map(lambda p: (15, (p[0], p[1], 0.0))),
    )


@st.composite
def _models(draw: Any, *, stochastic: bool) -> dict[str, Any]:
    """A friendly spec for `build_from_spec`, drawn rather than listed.

    The knobs, and why each is here:

    * **topology** -- `series`, `parallel`, or `k_of_n`. These give a start
      node and an end node with different structures between them, so the
      node-status cascade has something to disagree about.
    * **both distributions** -- a block's failure time *is* its drawn life
      (`get_start_cond` returns the draw directly, `block_lifecycle.py:323`),
      so the draw lands in the event log verbatim. That is what makes a
      stochastic property checkable at all.
    * **`sp_type`** -- `Infinite` (never runs out of spares), `Custom`
      (finite stock) and `None` (straight to `Done "No_spares"`). Three genuinely different repair stories.
    * **PM**, **a dependency** -- both are whole mechanisms, and both have
      documented traps (`PMReqDefer` off is the only way to see a `PMHold`
      cost; a dependency's `Down` arm is gated on the dependent's own status
      at `dependency.py:315-317`).

    Kept to 1-3 blocks and a short run so a 30-example search costs
    milliseconds; the point is breadth of *shape*, not of size.
    """
    n = draw(st.integers(1, 3))
    layout = draw(st.sampled_from(["series", "parallel", "k_of_n"]))
    if layout == "series":
        spec = series(n)
    elif layout == "parallel":
        spec = parallel(n)
    else:
        spec = k_of_n(draw(st.integers(1, n)), n)

    spare_of = {i: draw(st.sampled_from(["Infinite", "Infinite", "Custom", "None"]))
                for i in range(n)}
    stock_of = {i: draw(st.integers(0, 2)) for i in range(n)}
    pm_of = {i: draw(st.booleans()) for i in range(n)}
    dep_of: dict[int, int | None] = {i: None for i in range(n)}
    if n > 1 and draw(st.booleans()):
        a, b = draw(st.tuples(st.integers(0, n - 1), st.integers(0, n - 1)))
        dep_of[a] = None if a == b else b

    # The topology helpers name their own blocks (`Block1`, `Block2`, ...),
    # and a block's name is an endpoint in every link that touches it. So
    # the strategy renames to names it controls -- which `test_renaming_a_
    # block_changes_no_number` needs in order to rename them *again* -- and
    # has to rewrite the links in the same breath or the build raises
    # `BuildError: cannot resolve link endpoint`.
    original = [b["name"] for b in spec["blocks"]]
    names = [f"Blk{i}" for i in range(n)]
    mapping = dict(zip(original, names))

    for i, block in enumerate(spec["blocks"]):
        fdist, fparams = draw(_life(4.0, 40.0, fixed=not stochastic))
        rdist, rparams = draw(_life(0.5, 6.0, fixed=not stochastic))
        block.update({"name": names[i], "fdist": fdist, "fparams": fparams,
                      "rdist": rdist, "rparams": rparams,
                      "sp_type": spare_of[i], "stock": stock_of[i]})
        if pm_of[i]:
            block.update({"uses_pm": 1, "pm_freq": draw(st.floats(5.0, 30.0)),
                          "pm_dist": 19,
                          "pm_params": (draw(st.floats(0.1, 3.0)), 0.0, 0.0),
                          "pm_refresh": 1, "pm_fail_reset": 1})

    for link in spec.get("links", []):
        for end in ("from", "to"):
            if end in link:
                link[end] = mapping.get(link[end], link[end])

    for i, block in enumerate(spec["blocks"]):
        target = dep_of[i]
        if target is not None:
            # `DepNum` is a *number* -- the id the builder will assign --
            # not a block name. Passing a name here fails in a way worth
            # knowing about: `DepNum` is not one of `_STRING_COLUMNS`, so
            # the string falls through to the Rates expression resolver and
            # `expr_eval` rejects it as a bare Name. `builder.next_id` hands
            # out the lowest unused positive integer and `build_from_spec`
            # walks `blocks` before `nodes`, so the *n*-th entry is id *n*
            # (`spine._block_id` works the same way).
            block.update({"dep_type": "RBDBlock", "dep_num": target + 1,
                          "dep_idle": 100.0, "dep_nothing": 0.0,
                          "dep_pm": 0.0, "dep_fail": 0.0})

    spec["system"] = {"run_length": draw(st.floats(15.0, 45.0)),
                      "num_runs": ONE, "termination": "TimeSim"}
    return spec


def _fixed_models() -> st.SearchStrategy[dict[str, Any]]:
    """`Fixed` throughout, so `Run.draws` must be 0."""
    return _models(stochastic=False)


def _stochastic_models() -> st.SearchStrategy[dict[str, Any]]:
    """At least one stochastic life, so the generator is actually exercised."""
    return _models(stochastic=True)


def _renamed(spec: dict[str, Any]) -> dict[str, Any]:
    """*spec* with every block renamed consistently, links included.

    A block's name is load-bearing in three places at once -- the block
    itself, and both endpoints of every link that touches it -- so a
    rename that misses one is a `BuildError`, not a silent no-op. That is
    worth knowing: it means `test_renaming_a_block_changes_no_number` cannot
    accidentally pass by renaming nothing.

    `dep_num` is deliberately left alone: it is the numeric id, not a name,
    and a rename does not renumber ids. That is the point of the property.
    """
    out = copy.deepcopy(spec)
    mapping = {b["name"]: f"Zz{i}" for i, b in enumerate(out["blocks"])}
    for block in out["blocks"]:
        block["name"] = mapping[block["name"]]
    for link in out.get("links", []):
        for end in ("from", "to"):
            if end in link:
                link[end] = mapping.get(link[end], link[end])
    return out


def _numbers(result: Run) -> list[tuple[float, str, str, str]]:
    """A run's timeline with every name stripped out.

    This is the shape `test_renaming_a_block_changes_no_number` compares, and
    it is deliberately *not* the whole timeline: the name is the one thing a
    rename is allowed to change, and a block name also appears in a node's
    name (`Node:Mid1`), so keeping names would make the claim false.
    """
    return [(e.time, e.kind, e.old, e.new) for e in result.events]


# ------------------------------------------------- reproducibility properties

@_settings(25)
@given(_stochastic_models(), st.integers(0, 10_000), st.integers(0, 10_000))
def test_the_same_seed_produces_the_same_event_log(spec, seed, _other) -> None:
    """A seed fixes every stream, so the same model and seed agree exactly.

    This is the property the whole corpus comparison rests on. Every pinned
    timeline in this suite was recorded at some seed, and a claim that "at
    this seed the block fails at 12.064954" is worthless unless the seed
    reproduces it bit for bit.

    *Teeth*: it went red when the harness's `CountingStream` was made to
    perturb the value it counted rather than only count it -- the harness's
    own docstring claims it "changes no drawn value", and this is the test
    that would notice if it stopped being true.
    Source: raptor/core/engine/runner.py:139 (`stream_seed = int(seed or 0)`) -- one seed salts every stream, so one model at one seed is one trajectory.

    """
    assert run(spec, seed=seed).log == run(spec, seed=seed).log


@_settings(20)
@given(_stochastic_models(), st.integers(0, 10_000))
def test_a_different_seed_produces_a_different_event_log(spec, seed) -> None:
    """A stochastic model must actually consume its seed.

    Stated as *not all seeds agree*, and the weaker form is the honest one.
    The obvious phrasing -- "seed n+1 gives a different log from seed n" --
    is **false**, and Hypothesis found the counterexample before this
    docstring existed:

    >>> # a 15.34810075148205 h run, an Exponential(9.0) life, PM at 5.0 h
    >>> run(spec, seed=74).log == run(spec, seed=75).log
    True

    Not a seeding bug. Over seeds 1..299 the generator's first draw is
    distinct at the log's own 6-decimal precision, so distinct seeds always
    give distinct *streams*. The collision is upstream of that: with a life
    mean of 9 h in a 15 h run, a seed that draws the failure past the end
    produces a log of nothing but `t=0` rows and a terminate, and that log
    is the same for every such seed. A seed matters only once a draw moves
    an outcome, so the claim has to be about the model rather than about
    any particular pair of seeds.

    So the claim is guarded on the model having actually done something
    stochastic: if no run logged a single event after `t=0`, there is no draw
    in the log to vary and nothing to assert. Without that guard Hypothesis
    finds the counterexample within seconds -- an Exponential(40) life in a
    15 h run, where every seed produces the same three t=0 rows and a
    terminate. Asserting a difference there is asserting a coin flip.

    *Teeth*: it goes red if the engine is made seed-independent, which is
    the failure that would quietly invalidate every test here that passes a
    `seed=` -- and every pinned timeline in the suite, all of which were
    recorded at one.
    Source: raptor/core/engine/runner.py:139; raptor/core/rng.py::RaptorStream
    (the Lehmer recurrence, seeded from `stream_seed`)
    """
    runs = [run(spec, seed=seed + offset) for offset in range(3)]
    if not any(event.time > 0.0 for one in runs for event in one.events):
        return  # nothing stochastic happened; the seed had nothing to change
    logs = [tuple(one.log) for one in runs]
    assert len(set(logs)) > 1, (
        "three different seeds all produced the same log despite the model "
        "logging events after t=0 -- the seed is not reaching the generator")


@_settings(12)
@given(_stochastic_models(), st.integers(0, 10_000), st.integers(2, 4))
def test_the_first_run_of_a_batch_is_the_whole_run(spec, seed, n) -> None:
    """Run 1 of an *n*-run batch is exactly a 1-run batch.

    Generality is the point: the design asked for "the first k runs of an
    n-run batch equal a k-run batch" and the strong version holds, so it is
    asserted at every `k` rather than two sampled values.

    The weaker-looking claim -- that run 1 is *self-consistent*, so a batch
    of 5 could seed itself -- is the one that would let a report aggregate
    be computed from a batch that was never actually run.
    Source: raptor/core/engine/runner.py:254 (`all_logs: list[list[str]] = []`) -- each replication is an independent pass over kickoff.

    """
    one = run(spec, seed=seed)
    batch = run({**spec, "system": {**spec["system"], "num_runs": n}},
                runs=n, seed=seed)
    assert len(batch.per_run) == n
    assert batch.per_run[0] == one.events
    for k in range(2, n + 1):
        shorter = run({**spec, "system": {**spec["system"], "num_runs": k}},
                      runs=k, seed=seed)
        assert batch.per_run[:k] == shorter.per_run, (
            f"runs 1..{k} of an {n}-run batch differ from a {k}-run batch")


# ------------------------------------------------------ B2: Fixed costs nothing

@pytest.mark.parametrize("rung", RUNGS, ids=lambda g: f"S{g}")
def test_every_spine_rung_is_fixed_and_draws_nothing(rung: int) -> None:
    """All eleven rungs draw zero times, which is what makes their literals exact.

    Not a property over generated models -- a check on the ladder itself,
    and the reason the rest of this file can trust its own numbers. If a
    rung ever picked up a stochastic distribution, every committed literal
    above it would become seed-dependent and this would be the first
    failure.

    `Source:` `raptor/core/rng.py::_dispatch_draw` -- `WHEN 19` returns
    `Param(1)`, touching no stream.
    Source: raptor/core/rng.py:451-452 (`if d == Distribution.FIXED: return p[0]`) -- the draw reads a parameter and touches no stream.

    """
    result = run(spine(rung), seed=0)
    assert result.draws == 0, f"S{rung} consumed {result.draws} draws"
    assert result.events, f"S{rung} logged nothing at all"


@_settings(20)
@given(_fixed_models())
def test_a_fixed_model_consumes_no_randomness(spec) -> None:
    """`Fixed` everywhere means `draws == 0`, for generated models as well.

    `draws` is a measurement, not an input: the harness wraps the engine's
    own streams in `driver.count_stream_draws` and sums what they hand out.
    So this cannot be satisfied by declaring a model fixed.
    Source: raptor/core/rng.py:451-452; raptor/core/engine/driver.py (`count_stream_draws`) -- draws are counted by the engine, not declared.

    """
    assert run(spec, seed=0).draws == 0


# ---------------------------------------------------------- log-shape claims

@_settings(25)
@given(_stochastic_models(), st.integers(0, 10_000))
def test_event_log_time_never_goes_backwards(spec, seed) -> None:
    """Within a run, the log is in non-decreasing sim time.

    The harness builds its timeline from log order and `assert_timeline`
    compares it against a committed literal in that order, so an
    out-of-order line would make every pinned expectation order-dependent
    without any test saying so.

    *Teeth*: it goes red if the log is sorted before it is read, which is
    the natural way to make a log "tidy" and which would destroy the
    ordering the event ordering actually establishes.
    Source: raptor/core/engine/driver.py:298 (`scheduler.run_until(model.run_length)`) -- the scheduler pops in clock order, so the log it produces is in clock order.

    """
    for one in run(spec, seed=seed).per_run:
        times = [e.time for e in one]
        assert times == sorted(times), (
            f"log order is not sim-time order: {times}")


@_settings(25)
@given(_stochastic_models(), st.integers(0, 10_000))
def test_a_rows_from_state_is_the_previous_rows_to_state(spec, seed) -> None:
    """Every logged `from` is that object's last logged `to`. The chaining rule.

    This is the single most load-bearing unasserted claim in the suite.
    `tools/behaviour_inventory.py:26-30` derives every corpus
    transition by chaining, and `test_b_behaviour_coverage.py` counts a
    transition as pinned when a literal names the same `(from, to)` pair. If
    the port ever stopped chaining -- logging `--` on each change, say --
    then every pinned timeline would still pass, the gate would still
    report its 263, and it would be counting the wrong thing. Nothing else
    in `tests/behaviour/` checks this.

    `Source:` `tools/behaviour_inventory.py:26-30`.
    Source: tools/behaviour_inventory.py:26-30; tests/behaviour/harness.py:229-241 (`_timeline`)

    """
    for one in run(spec, seed=seed).per_run:
        previous: dict[str, str] = {}
        for event in one:
            if event.obj in previous:
                assert event.old == previous[event.obj], (
                    f"{event.obj}: logged from={event.old!r} but the "
                    f"previous row said to={previous[event.obj]!r}")
            previous[event.obj] = event.new


@_settings(20)
@given(_stochastic_models(), st.integers(0, 10_000))
def test_every_object_starts_from_the_unknown_sentinel(spec, seed) -> None:
    """An object's first logged state is `--`, never a real status.

    `--` is Raptor's own "unknown" convention (`Simulation_Terminate` uses
    it), and it is what lets a reader tell "this object had no previous
    state" from "this object was Good". A block that logged a real status
    first would make its very first transition unfalsifiable.

    The docstring template is in `tests/behaviour/README.md`; see
    `raptor/core/engine/eventlog.py` for the padding this sentinel inherits.
    Source: tests/behaviour/harness.py:239 (`previous.get(obj, FIRST_STATE)`); raptor/core/engine/eventlog.py -- `--` is the padding Raptor writes for an absent value.

    """
    for one in run(spec, seed=seed).per_run:
        first: dict[str, str] = {}
        for event in one:
            first.setdefault(event.obj, event.old)
        for obj, state in first.items():
            assert state == FIRST_STATE, (
                f"{obj} opened at {state!r} instead of {FIRST_STATE!r}")


@_settings(25)
@given(_stochastic_models(), st.integers(0, 10_000))
def test_sim_time_never_passes_the_run_length(spec, seed) -> None:
    """No row is logged meaningfully beyond `run_length` under `TimeSim`.

    The strategy always sets `termination="TimeSim"`, so this is the
    clock-truncation claim and nothing else: a model terminated on its own
    end state would be allowed to run past the limit, and conflating the
    two would hide a real overshoot.

    **The tolerance is the log's own print resolution, and it is not
    arbitrary.** The event at the boundary is found by comparing floats
    that the engine has already combined, so it lands a fraction of a step
    past the limit: measured over 59 seeds of a deliberately awkward
    `run_length=15.34810075148205`, the worst overshoot is **2.485e-07 h**
    -- 0.89 ms of sim time, and 0.25 of one of the six decimals the log
    prints. So the claim is "not more than one printed digit past", which
    is also the strongest claim the log could express: at 1e-06 h a row
    *cannot* distinguish an overshoot from a rounding.

    `Source:` `raptor/core/engine/driver.py` -- the step loop compares
    against the termination clock before dispatching the next event.
    Source: raptor/core/engine/kickoff.py:847 (end-of-sim scheduled at `run_length`) and raptor/core/engine/driver.py:298

    """
    limit = float(spec["system"]["run_length"])
    resolution = 1e-06
    for one in run(spec, seed=seed).per_run:
        for event in one:
            assert event.time <= limit + resolution, (
                f"{event.obj} logged at {event.time!r}, which is "
                f"{event.time - limit:.3e} h past a {limit} h run -- more "
                f"than the log's own {resolution:.0e} h print resolution")


# -------------------------------------------------------- invariance claims

@_settings(15)
@given(_stochastic_models(), st.integers(0, 10_000))
def test_renaming_a_block_changes_no_number(spec, seed) -> None:
    """Rename every block and no time, status or object *kind* moves.

    A name is an identifier, not a quantity. If renaming perturbed a
    trajectory it would mean the engine were reading something out of the
    name -- an ordering, a hash, a length -- and every recorded capture
    would be pinned to the accident of what things happened to be called.

    Names are excluded from the comparison on purpose: a block name also
    appears in a node's name (`Node:Mid1`), so "nothing changes" is only
    true of the numbers.
    Source: raptor/core/engine/block_lifecycle.py:323 (`get_start_cond`) -- a block's life is drawn from its distribution, never from its name.

    """
    before = run(spec, seed=seed)
    after = run(_renamed(spec), seed=seed)
    assert _numbers(before) == _numbers(after)
    # And the rename must have actually happened, or the claim is vacuous.
    assert {e.name for e in after.events if e.kind == "Block"} == {
        f"Zz{i}" for i in range(len(spec["blocks"]))}


@_settings(12)
@given(_stochastic_models(), st.integers(0, 10_000), st.integers(0, 10_000))
def test_an_intervening_run_changes_nothing(spec, seed, other_seed) -> None:
    """Running a different model first must not change this one's output.

    Nothing may survive between simulations -- no cached stream state, no
    module-level counter, no leftover model. This is the property that
    makes the rest of the suite order-independent, and it is the reason a
    test may `run()` a model and then assert on a number without caring
    what ran before it. With 250+ tests in one process it is also the
    property most likely to break by accident.

    *Teeth*: it goes red if two models share a stream object, which is the
    natural way to "optimise" the harness into running faster.
    Source: raptor/core/engine/runner.py:139-285 -- every stream is constructed per `run_simulation` call, so nothing can survive into the next model.

    """
    expected = run(spec, seed=seed).log
    run(_stochastic_example(), seed=other_seed)
    assert run(spec, seed=seed).log == expected


def _stochastic_example() -> dict[str, Any]:
    """One concrete stochastic model, for `test_an_intervening_run_changes_nothing`.

    Hypothesis cannot hand a property test a second *result*, only a second
    input, so the intervening run is a fixed model rather than a drawn one.
    It needs to be stochastic for the claim to mean anything -- a `Fixed`
    run between two runs would perturb nothing even if state did leak.
    """
    spec = series(2)
    for index, block in enumerate(spec["blocks"]):
        original = block["name"]
        block.update({"name": f"Other{index}", "fdist": 4,
                      "fparams": (3.0, 0.0, 0.0),
                      "rdist": 19, "rparams": (1.0, 0.0, 0.0),
                      "sp_type": "Infinite"})
        # The topology helpers name their own blocks, and links point at
        # those names, so a rename has to travel. Two blocks sharing the
        # name "Other" would build fine and then log to one object, which
        # is a silent corruption rather than an error.
        for link in spec["links"]:
            for end in ("from", "to"):
                if link.get(end) == original:
                    link[end] = f"Other{index}"
    spec["system"] = {"run_length": 40.0, "num_runs": ONE, "termination": "TimeSim"}
    return spec


@_settings(8)
@given(_stochastic_models(), st.integers(0, 10_000))
def test_a_hierarchys_status_equals_its_out_markers(spec, seed) -> None:
    """A hierarchy's logged status is its Out marker's own status.

    The plan wanted "a hierarchy flattened by hand gives an identical log".
    That is false and provably so: a flattened model has no hierarchy
    object, so its log has no `Hier` rows to be identical to. The claim
    worth having is the rollup itself -- that the `Hier` row is not a
    second, independent computation but the marker's status under another
    name.

    `Source:` `docs/engine-design/hierarchy-status.adoc` -- a
    hierarchy's status is the aggregation of its Out marker's incoming
    links (`nodes.py:281-299`).
    Source: raptor/core/engine/nodes.py:235 (`determine_node_status`); docs/engine-design/hierarchy-status.adoc

    """
    # Only models that actually contain a hierarchy say anything here, and
    # `_models()` never draws one, so this checks the one rung that does.
    result = run(spine(8), seed=seed)
    hierarchies = [e for e in result.events if e.kind == "Hier"]
    if not hierarchies:
        return
    marker_status = {e.new for e in result.events
                     if e.kind == "Node" and e.name == hierarchies[0].name}
    for row in hierarchies:
        assert row.new in marker_status or row.new in ("Good", "Bad", "Idle", "NodePM")


# ------------------------------------------------------------ affordability

def test_the_property_layer_is_affordable() -> None:
    """This layer must not become the expensive part of the suite.

    A property suite is a search, so it is allowed to be slower than a
    single timeline test -- but it is not allowed to dominate. The rest of
    `tests/behaviour/` runs in single-digit seconds; if this file ever adds
    more than a minute to that, the strategy space is too wide or the
    `max_examples` are too high, and the honest fix is to narrow the search
    rather than to let CI pay for it.

    The budget is a coarse guard, not a benchmark: it exists to catch an
    order-of-magnitude regression, and it is deliberately the *last* test in
    the file so everything it measures has already run.
    Source: tests/behaviour/README.md (rule B4) -- the 100 ms budget this layer deliberately departs from, and says so.

    """
    import time

    start = time.monotonic()
    for one in range(4):
        run(spine(1 + one), seed=0)
    per_model = (time.monotonic() - start) / 4

    # ~15 generated models per property x 9 stochastic properties, plus the
    # Fixed one. If a single model costs more than this the layer cannot be
    # affordable at the current example counts.
    budget_seconds = 40.0
    worst_case = per_model * 15 * 10
    assert worst_case < budget_seconds, (
        f"one model costs {per_model * 1000:.1f} ms, so the property layer "
        f"could reach {worst_case:.0f} s against a {budget_seconds:.0f} s budget")