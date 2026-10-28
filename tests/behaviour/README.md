# The behaviour suite

Tests that say what the engine **does**.

`tests/unit/` holds the broad regression net: whole outputs compared against
files. That is a strong signal and a useless specification — a failure says
`structural mismatch at line 4217`, and the reader has to go and work out which
state transition it was. Everything in this directory is instead a **claim
about one behaviour**, written out in plain English, with the expected timeline
as a literal list next to it and a citation: the Raptor 7 procedure it
reproduces, the port code that implements it, or a `docs/engine-design/` page. A failure here says which transition moved, at
which sim time, and which docstring explains why it should be there.

This file holds the rules, the harness, the ladder and the docstring template.

```
tests/behaviour/
  harness.py           build in memory, run, extract, assert
  spine.py             one canonical fixture per ladder rung (S1..S11 today)
  test_b_ladder.py     the spine gate: each rung's committed literal
  README.md            this file
```

Run them:

```console
$ pytest tests/behaviour -q
```

---

## The rule set

Nine rules. Each is here because it makes a
test *say* something, not because it is tidy.

| # | Rule | What it buys you |
|---|---|---|
| **B0** | Never execute the old engine. No Wine, no launcher script, no foreign binary. The suite must pass on a laptop that has never had it installed. | Portability you did not have to argue for. |
| **B1** | No captured artefact in this directory — none of the tokens in `test_no_legacy.py`'s list, which is the recorded-output corpus, its snapshot and fidelity TOMLs, and its draw dumps. | The expectation is *derived*, never copied. Reading a capture is inert — but an expectation that came out of a file cannot be argued with. |
| **B2** | `Fixed` distributions by default. They draw `param[0]` and touch no stream, so the run is exact, needs no seed, and consumes **zero** randomness. | Determinism, and a test you can write down. A stochastic family is allowed only when the *distribution* is the subject, and then only with an explicit `seed=` and a stated tolerance in the docstring. |
| **B3** | Built in memory. `build_from_spec(spec, auto_layout=False)`. No `.rbd` on disk, no `Workspace`, no Qt, no `load_rbd`. | The harness has exactly one constructor, so there is one path into the engine and one place it could go wrong. |
| **B4** | Small: ≤ 6 blocks, ≤ 5 nodes, 1 run, **under 100 ms**. | You can run the whole suite and then read it. |
| **B5** | The expectation is *in the test*: a literal list of `(time, object_type, object_name, from, to)`. Never a tolerance against a number the same code recomputed. | The diff is the specification. "Expected 55.0, got 50.0" is readable in review; "expected f(x), got f(y)" is not. |
| **B6** | A named claim and a cited source, in this order: **claim / why / setup / expected / `Source:`**. The `Source:` line is mandatory. | The oracle is the Raptor 7 rule the test names and the port code that implements it, which a reviewer can check; that is stronger and more permanent than a text file captured once. |
| **B7** | No wall-clock, no sleeps, no threads, no filesystem. SimTime is the only clock a test reads. | A test cannot be flaky because the machine was busy. |
| **B8** | One behaviour per test. If the name needs "and", it is two tests. | A red test names one mechanism. |

B0 and B1 are different rules and both are needed. B0 is about *execution* —
never launching the binary — and it binds all of `tests/`. B1 is about *data* —
never reading a recorded oracle — and it binds this directory only. So the
fidelity tier can honour B0 completely while still being a fidelity tier.

---

## The ladder, not a matrix

`spine.py` holds one canonical fixture per rung. **Each rung is the rung below
plus exactly one new mechanism**, so a failing test localises to one mechanism:
everything below it is already green.

| rung | adds | canonical fixture | today |
|---|---|---|---|
| S0 | nothing — pure arithmetic | none (the RNG and a distribution statistic; pinned by `tests/unit/test_scheduler.py` and `test_diststats.py`) | — |
| S1 | a scheduler and a process | one lone block | ✅ |
| S2 | topology | S1 + a second block and a k-of-n `Join` | ✅ |
| S3 | one shared resource | S2 + a one-unit `RESOURCE` pool | ✅ |
| S4 | scheduled maintenance | S3 + a periodic PM on A (`PMFreq=25`, `Fixed` 2 h) | ✅ |
| S5 | triggers | S4 + a `Fixed` 30 h trigger that fires A's PM | ✅ |
| S6 | dependencies | S5 + B depending on A (`DepIdle=100%`) | ✅ |
| S7 | spare pools | S6 + a 1-spare pool with a 40-hour RSO restock | ✅ |
| S8 | cold standby | S7 + a cold group of 2 (`C` running, `D` standby, `ColdPri` 10/20) | ✅ |
| S9 | phases | S8 + two phases, the second **non-mission** (`Fixed` 60 h + `Fixed` 40 h) | ✅ |
| S10 | events | S9 + one event at `p = 1.0`, in series before the End node | ✅ |
| S11 | capacity | S10 + a flow limit on the one link every delivered unit must cross | ✅ |
| S12…S13 | reporting over many runs, cross-cutting properties | | not yet |

`spine(rung)` builds one:

```python
spine(1)                    # S1's spec
spine("S2")                 # by name
spine(3, num_resources=2)   # S3 with a two-unit repair pool
```

Overrides exist so a test asks for the variant instead of copying a spec dict
and letting the copy rot. An override that does not apply **raises** rather
than being ignored (`num_resources` on S1 says so in the message).

Two rules make the ladder real rather than decorative:

1. **One mechanism per rung.** S3 adds a resource and nothing else. A cold-standby
   test that also needs a pool belongs at S7 for the pool and S8 for the
   switching.
2. **The rung below's expected output is a literal, and it does not change.**
   `test_b_ladder.py` runs every rung's fixture and compares against the
   committed literal, so the whole spine is one parametrised gate. A mechanism
   added at S7 that perturbs S1's numbers fails there first — that is a
   *finding*, not a test update.

`spine.py` is a **chain of small pure functions** (`_s7()` calls `_s6()` … calls
`_s1()`), not a switch with a growing `elif` arm. The naive version is the failure mode: a switch grows into unmaintainable code,
whereas the chain reads like the ladder does.

> **Three places the ladder's first sketch could not be followed, and why.**
>
> The first sketch described S1 as "one lone block, no model" and S2 as "S1 + Start/End
> nodes + links". A block with no topology cannot be run: `build_from_spec`
> refuses a model with no End node (`builder.validate` → `ERROR: no End
> (Type=3) node found`) and `load_model` refuses a block with no outgoing link
> (`graph.build_efpa_graph` → `no outgoing link found for RBDBlock id=1`). So
> S1 carries the minimum topology the loader demands, and **S2's new mechanism
> is the k-of-n `Join`** — the first rung where one failure *degrades* a node
> instead of taking it down. S1's own claim is about the block column of its
> timeline.
>
> The sketch also *branched*: it listed S6 as "S4 + a dependence" and S7 as "S3 + a
> pool". The harness requires the spine to be a chain, and a chain cannot branch. Each
> rung's added mechanism is exactly the one the sketch named — a dependence at S6, a
> pool at S7 — so the content matches and the parent merely differs: S6 and S7
> inherit S4's PM and S5's trigger instead of dropping them. That inheritance
> is visible in the literals (S7 has both the `Trigger` rows and the `Pool`
> rows), and it is why the S7 rung is the one worth having.
>
> The sketch said S8 is "S7 + a cold group of 2", which reads as though the group
> could be A and B. It cannot: `refuse_unrunnable` rejects a cold path into a
> node that depends on another path into the same node — real Raptor 7.0.11
> crashes on that shape with an access violation — and S6's dependence is
> exactly `B -> A` (`model_checks.py:118`). So the cold group is a **second
> stage** of two new blocks, `C` and `D`, downstream of the k-of-n one. That is
> the one place the ladder's stacking forces a shape rather than choosing one,
> and it was worth the extra two blocks: because `C` and `D` borrow no repair
> resource and draw no spare, **every `A`, `B`, `Pool` and `Trigger` row of S7's
> literal is reproduced unchanged**, so the rung-difference between S7 and S8 is
> the switching and nothing else.

### What each rung after S3 pins, in one line

| rung | the claim it adds |
|---|---|
| S4 | a PM's duration is **not operating time**, so A's `Fixed` 50-hour life ends at t=52 rather than 50 — and the `Failed` line says `Operating_time=50.000000` |
| S5 | the PM *clock* is exclusive: a `pmTriggered` block's PM happens only when the trigger fires, and a frequency-driven block's PM does not move when one does |
| S6 | a dependence dispatches on `Down`, and **a block in PM is Down** — so a two-hour PM idles the dependent and turns the system `Red`, and an Idle block's failure clock is paused (B fails at 67, not 55) |
| S7 | a one-spare pool runs dry: B parks `RepHold` `Waiting_for_Spare` and is granted the restock in the same instant the arrival is logged, and the PM that finds the pool empty again sits in `pmReqDefer` until the run ends |
| S8 | a cold group reaches **all three** of `RunColdLogic`'s arms in one run — kickoff demotion (`Good -> Standby`, `Too_many_on`, waking nobody because of the `SimTime>0.0` guard), switch-on the instant the running path fails (`Prior=Standby`), and switch-off when the repaired path comes back — and its own node line stays `1/2_up…` throughout, so the group is never `Bad` |
| S9 | the phase table is what makes a mission exist: the run opens in one at t=0 (`Began_a_mission`), and the boundary into a non-mission phase **closes** it — recorded as `Mission_failure`, because the close reads the *system colour* at that instant, not the phase table |
| S10 | an event's `failVal` at `1.0` is a certainty, not a bias: `ConductEvent` reads `draw == 1.0` as success, so it goes `Armed -> Success` in the instant it is armed and its cascade is empty. The `Success -> Armed` at the run end is `EndSimulation`'s element sweep, not the event's |
| S11 | capacity is a **reporting** mechanism and moves no state at all — the rung's committed timeline is S10's, row for row. Its claim is the report: a cap on one link takes 100 off the demand 200 on every hour it applied (`MaxFlow`/`MaxCapacity` 100, `UnitsProduced` 8800 = 88 capable hours × 100) |

---

## The harness

One file, `harness.py`. It **extracts** and **formats**; it never reinterprets
engine logic. If `assert_timeline` needed to know what a status *means*, it
would be a second engine.

### `run(spec, *, runs=1, length=None, seed=None, **system) -> Run`

Builds with `build_from_spec(spec, auto_layout=False)`, runs through
`run_simulation_with_reports`, returns a `Run`:

| field | what it is |
|---|---|
| `events` | every state change, in event-log order, as `Event(time, kind, name, old, new)` |
| `log` | the raw event-log lines, for a test that wants the text |
| `eor`, `final` | End-of-Run and Final-Results report lines |
| `transitions` | per qualified object name, its own `(time, old, new)` trace — used by `assert_timeline`'s failure message |
| `draws` | **total RNG draws consumed.** `0` under B2; asserted, not hoped for |
| `runs` | replications (1 under B4) |
| `rbd` | the built model, if a test wants to inspect the rows |

`length=` shortens the run (the cheapest way to stay inside B4). `**system`
passes anything else in `SYSTEM_DATA` (`termination`, `weak_analysis`, …).

### `events(run, block=None, between=None)`

A filtered view — the thing a test asserts on. `block` matches the qualified
name (`"Block:A"`, `"Node:End"`, `"System:State_change"`) or the bare name
(`"A"`). `between=(t0, t1)` keeps a sim-time window, both ends included.

```python
[str(e) for e in events(result, "Block:B")]
# ['0 Block:B -- -> Good', '55 Block:B Good -> Failed',
#  '55 Block:B Failed -> RepHold', '60 Block:B RepHold -> Sparing', ...]
```

### `assert_timeline(run, expected, *, where=None)`

The workhorse. Compares the run's timeline against a literal list and, on
failure, prints **the symmetric difference in sim-time order with the first
divergence marked** — not two long lists side by side. Unchanged stretches
collapse to `... N unchanged event(s) elided`, and the affected object's own
trace is appended, because a wrong timeline is nearly always one object's
trace that grew a row or lost one.

```
timeline mismatch [S2]: 22 expected, 23 actual
first divergence at event 5, sim time 50
  expected     50.000  System  State_change     -- -> Red
  actual       50.000  System  State_change     -- -> Yellow

symmetric difference (sim-time order, '>>' = first divergence):
  >> expected only     50.000  System  State_change     -- -> Red
  >> actual only       50.000  System  State_change     -- -> Yellow
     actual only       55.000  Node    End              -- -> Bad

actual transitions for System:State_change (3 rows; the rung's literal is above):
     50.000  -- -> Yellow
     55.000  Yellow -> Red
     60.000  Red -> Green
```

Designed to be read at 2am: the first divergence is three lines from the top,
and every column lines up.

A four-element literal `(time, name, old, new)` is accepted as shorthand for a
block row. The five-element form is the plain one and is what the ladder uses,
because the object *type* matters — `Node:End` and `Block:End` are different
objects and the coverage gate keys on the type.

### `state_at(run, t) -> dict[str, str]`

Every object's logged state at sim time `t`, read back from the log: the state
of each object at the last line it logged at or before `t`. An object that has
not logged yet is absent, so a missing key means "not initialised yet".

### `FIXED`, `fixed(v)`, `dist(code, *params)`

`FIXED` is distribution code 19. `fixed(50.0)` is the block patch for "fails at
hour 50". `dist(...)` is for the rare test whose subject *is* a distribution —
those need a `seed=` and a tolerance in the docstring.

### Spec keys the upper rungs use

`build_from_spec` takes friendly names, and the ones rungs S3–S11 need are worth
writing down because three of them will silently do nothing if you get them
wrong. Every numeric column also accepts an expression string.

| where | key | notes |
|---|---|---|
| `system` | `run_length`, `num_runs`, `termination` | `run_length` is `TermCondition`; `termination` is `TerminationType` |
| `blocks[]` | `fail_dist`/`fail_params`, `repair_dist`/`repair_params` | **code and params travel together** on a new block — `_require_dist_params_on_new_block` refuses one without the other |
| | `num_res`, `resource_name` | `num_res=1` exactly. Source's check is `numDiffRes=1`, so any other number borrows nothing and never waits |
| | `uses_pm`, `pm_freq`, `pm_dist`, `pm_params`, `pm_refresh`, `pm_fail_reset`, `pm_triggered`, `pm_trig` | `pm_trig` is a trigger *name*, and only honoured when `pm_triggered` is true |
| | `sp_type`, `spare_pool` | `sp_type` takes Raptor's **on-disk** string, so pooled sparing is `"Pooled"` — see the trap below |
| | `dep_type`, `dep_num`, `dep_idle`, `dep_nothing`, `dep_pm`, `dep_fail` | `dep_num` is an **id**, not a name. `"RBDBlock"`, `"RBDNode"`, `"RBDEvent"`, `"RBDHier"`, `"None"` |
| `resources[]` | `name`, `num_in` | a `RESOURCE` pool has a fixed capacity and is never replenished |
| `spare_pools[]` | `name`, `stock`, `new_spares`, `arrive_every`, `rso`, `level`, `quantity`, `slo`, `slo_time`, `eso`, `emer_time` | `level`/`quantity`/`slo`/`slo_time` are the stock-level-order fields; `rso`/`eso` are the routine and emergency switches |
| `triggers[]` | `name`, `init_used`, `dist`, `params`, `repeats` | `params` must carry **three** values — `make_trigger_row` reads `params[0..2]` unpadded, so a one-element list raises `IndexError` |
| `nodes[]` | `type` (1 Start, 3 End), `n`, `out`, `k` | an **End node ignores `k`** (ladder L2 finding) |
| | `cold`, `k_star`, `pr`, `autos` | cold-standby group. `cold=1` is the switch that hands the node's status to `RunColdLogic` instead of `determine_node_status` |
| | `full_flow`, `div_flow` | capacity boundary. `full_flow=0` ("Unrestricted flow" off) is what makes a link's `nom_flow`/`max_flow` cap bind at all — `make_node_row`'s default is `fullflow=1` |
| `links[]` | `from`, `to` | by name, case-insensitively |
| | `cold_pri`, `a_prob`, `auto_time`, `man_time` | a cold candidate's switching attributes. `a_prob=1.0` short-circuits `SetSwitchTime`'s draw; `auto_time=0.0` resolves the link straight to `"ready"` so no switch timer starts |
| | `nom_flow`, `max_flow` | a flow cap. Sentinel `-1` = "flowGenerated", `-2` = "even share"; only `>= 0` is a real cap |
| `events[]` | `name`, `failVal`, `dist` | `failVal` is the Bernoulli parameter `ConductEvent` draws **and** reads: `1.0` = always succeeds. `dist=19` (`Fixed`) makes that certainty draw-free; the default `Bernoulli` draws a uniform even at `1.0` |
| `phases[]` | `name`, `mission`, `dist`, `params` | one row per phase, in order. `mission=0` marks a non-mission phase, which is what ends a mission at the boundary — as a *failure* if the system is Red there |
| `system` | `capacity`, `flow_generated` | `capacity=True` turns on the flow analysis; `flow_generated` is the demand injected at the Start node |

> **`sp_type` is the one that bites.** `typed._SPARING_TYPE_BY_NAME` maps
> Raptor's own vocabulary: `"Infinite"`, `"Custom"`, `"Pooled"`. The enum
> member name `"SparePool"` is **not** in that map, so writing it produces a
> block whose sparing type is `NONE` — which is worse than an error, because
> the block then runs with no sparing at all and a failure goes straight to
> `Done`. `"ColdPool"` and `"Resource"` are mapped to `NONE` for the same
> reason (a block can never carry those; only pool objects do). This is called
> out in `pools-spares-resources.adoc` under "Port-only devices".

### About `draws`

`draws` is a **measurement**, never an input. The harness subclasses the
engine's own `EngineCtx` for the duration of one `run_simulation` call and asks
the engine's own `driver.count_stream_draws` to wrap that context's streams in
`CountingStream`. It is restored in a `finally`, it changes no drawn value, and
a run with it produces byte-identical logs to a run without it. The harness
never seeds or otherwise owns the RNG: seeding is `run(seed=…)`, and a missing
context raises rather than reporting a confident zero.

---

## The docstring template

Five parts, always in this order, on **every** test function. The `Source:`
line is not decoration — a planned static guard checks for it.

```python
def test_pm_is_granted_the_resource_released_at_the_same_instant():
    """A PM deferred by a busy repair resource is granted that resource the
    instant it is released, before any same-instant switch.

    Why: Raptor 7 queues PM resource requests behind repair requests,
    and a grant that is already in hand when the PM asks must not be
    re-queued.

    Setup: 1 block, PM at t=10, one resource busy until t=10 (Fixed).

    Expected: PM starts at 10.0, not 10.0+eps; block never re-enters `Run`
    between 10.0 and 12.0.

    Source: Raptor 7 (ObtainPMresource), wait_sites.PM_HOLD_RESOURCE
    """
```

* **claim** — one sentence, plain English, present tense. What is true.
* **why** — the Raptor 7 rule, or the capture that settled a question. This is
  what makes the claim checkable by a reviewer instead of by a rerun.
* **setup** — the model in one line, including the distributions. A reviewer
  should be able to rebuild it.
* **expected** — the literal, in prose. The literal itself is the assertion.
* **Source:** — the Raptor 7 procedure the claim reproduces, the port code
  that implements it, or a `docs/engine-design/*.adoc` section. Cite what you
  have read, not what you have inferred.

Sources worth knowing, all under `docs/engine-design/`: `block-lifecycle.adoc`
(what a block's process does), `scheduler-and-ordering.adoc` (same-instant
order, which is where most of this project's subtle bugs live),
`node-status-and-cold-standby.adoc`, `pools-spares-resources.adoc`,
`kickoff-and-runs.adoc`, `state-machine.adoc` (every wait site and what can end
it), `state-coverage.adoc` (which of those cells the corpus reaches).
`raptor/core/engine/wait_sites.py` carries an Raptor 7 on every wait
site, so it is the best index there is.

---

## A worked example

`test_the_second_repair_waits_for_the_only_repair_resource`, the S3 mechanism.

```python
def test_the_second_repair_waits_for_the_only_repair_resource():
    """A block that fails while the single repair resource unit is borrowed
    parks in `RepHold`, and is granted the unit the instant the borrower hands
    it back.

    Why: the mechanism rung S3 adds over S2. Source's request is a two-part
    condition -- a raw stock check *or* an outstanding report number -- so a
    block that cannot be served logs `Waiting_for_resource(s)` and parks, and
    `TakeBack` grants the head of the queue. ...

    Setup: `spine(3)`, one run of 100 sim hours, one-unit pool `R`, A fails at
    50 and holds the unit for its `Fixed` 10, B fails at 55.

    Expected: B is `Failed -> RepHold` at 55, is granted the unit at 60
    (`Sparing`), and is `Good` at 65 after its `Fixed` 5 -- five hours later
    than S2's fixture puts it.

    Source: Raptor 7 (`Repair`'s wait for its resources),
    Raptor 7 (the zero-length hop that lets a same-instant `TakeBack`
    land before the check), Raptor 7 (the per-use cost, charged at the
    grant); docs/engine-design/pools-spares-resources.adoc (Structure),
    block-lifecycle.adoc (Source anchors)
    """
    result = run(spine(3))

    assert [str(e) for e in events(result, "Block:B")] == [
        "0 Block:B -- -> Good",
        "55 Block:B Good -> Failed",
        "55 Block:B Failed -> RepHold",
        "60 Block:B RepHold -> Sparing",
        "60 Block:B Sparing -> Repairing",
        "65 Block:B Repairing -> Repaired",
        "65 Block:B Repaired -> Good",
        "100 Block:B Good -> Done",
    ]
    assert state_at(result, 55.0)["Block:B"] == "RepHold"
    assert state_at(result, 59.0)["Block:B"] == "RepHold"
    assert state_at(result, 60.0)["Block:B"] == "Repairing"
```

Note what it does *not* do. It does not compute when B should be repaired and
compare — `50 + 5` would be recomputing the engine's arithmetic inside the test,
which is B5's trap and B6's failure. The numbers are spelled out, so the
reviewer is comparing the test against the rule it names, not against another
copy of the same arithmetic.

---

## Adding a rung

1. **Write the fixture.** Add `_sN()` to `spine.py`, calling `_(N-1)()` and
   adding exactly one mechanism. Add it to `RUNGS` and `_BUILDERS`, give it an
   entry in `RUNG_NAMES`. Keep every distribution `Fixed` and the run length
   short. **Add `if/elif` to `test_fixed_lives_consume_no_randomness`'s
   parametrisation too** — it is the cheap check that the new mechanism stayed
   draw-free.
2. **Run it once, by hand.** Do not trust a literal you reasoned about:

   ```python
   from tests.behaviour import harness, spine
   result = harness.run(spine.spine(4))
   for e in result.events:
       print((e.time, e.kind, e.name, e.old, e.new))
   print("draws:", result.draws)          # must be 0 (B2)
   ```

   Then check every number against the rule you are about to cite. A
   literal you did not derive is a capture in disguise (B1).
3. **Commit the literal** in `test_b_ladder.py`, as an inline argument to
   `assert_timeline` — the coverage gate `ast`-parses this file, and it can only
   see a literal that is syntactically an argument of the call. The gate sees
   `(time, kind, name, from, to)` in a five-element row, so keep that form.
4. **Add the rung's own claim** as a separate test: one mechanism, one
   behaviour, one docstring (B8). The ladder gate proves the rung did not
   disturb its predecessors; the individual test says what the new mechanism
   *does*.
5. **Promote the manifest rows and raise the ratchet.** Run
   `python tests/behaviour/test_b_behaviour_coverage.py --cov-report`. The
   `asserted by a literal at all` line going up is the signal; every corpus
   transition newly asserted by your literal gets `class = "pinned"` with a
   `reference` naming the test, and `[ratchet] pinned` goes up to the new
   count. **Never lower either number** — that is what makes it a ratchet
   rather than a snapshot.

### Adding a behaviour to an existing rung

No new fixture. Start from `spine(rung)` and change one thing:

```python
spec = spine(3)
spec["blocks"][1]["fail_params"] = [70.0]      # B fails later
result = run(spec, length=150)                 # shorter run, still <100 ms
assert [str(e) for e in events(result, "Block:B")] == [...]
```

### If a behaviour test fails

**Do not edit the literal to make it green.** That is the failure mode this
whole directory exists to prevent: the literal *is* the claim, and changing it
without re-deriving it from the source converts a specification back into a
snapshot. Read the docstring's `Source:` line, read the source, and decide which
of the two is wrong:

* the engine changed → the test was pinning a bug. Check the ladder gate: if an
  earlier rung moved too, that is the finding. Fix the engine, keep the test.
* the test was wrong → say why in the commit message, in terms of the source
  line.

---

## Conventions worth keeping

* One `Run` per test; share nothing between tests. `spine()` returns a fresh
  spec every call, so a test may edit it freely and cannot poison the next one.
* Tests are independent and order-free by construction — there is no shared
  fixture directory and no mutable module state. The fixtures are shared
  through `spine()`, not through mutation.
* Prefer `state_at(run, t)` for "what was true at hour *t*". It reads better
  than slicing a list and it does not depend on how many events precede it.
* Keep the run length as short as the claim allows. Everything here runs in
  single-digit milliseconds; that is what makes the tier worth running on every
  commit.
* If a model needs something `spine()` cannot express, **extend the fixture**.
  A test that builds an `RBDFile` by hand is how the suite grows a second
  dialect, and then there are two ways into the engine and one of them is
  untested (B3 is the rule that keeps it true).