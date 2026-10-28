"""Block-state transitions: the states the repair, sparing, PM, LDT and
dependence mechanisms put a block *into*.

`docs/engine-design/block-lifecycle.adoc` draws the block's life as one
process: draw a life, fail, get a spare and resources, repair, run again, with
preventive maintenance and dependences cutting across it. The states that
process visits -- `RepHold`, `Sparing`, `Repairing`, `Repaired`, `Standby`,
`Idle`, `Done` -- are what this file pins, and it pins them as **timelines**,
because a state's own name says nothing about *when* it is entered or what
follows it.

**Why a file of timelines rather than a table of states.** Two of the claims
here are about the *order* of three lines at one sim instant, and neither is
recoverable from a set membership:

* a logistics-delay column is a *state*, not a comment. The pre-LDT wait holds
  the block in `RepHold` (`ChangeBlockState(RepHold, ..., "LDT")`) and the
  post-LDT wait puts it back there after the repair has finished
  (`"postLDT"`), so one block's ordinary life is
  `Good -> Failed -> RepHold -> End_Pre-LDT -> Repairing -> Repaired ->
  RepHold -> End_Post-LDT -> Good`. `End_Pre-LDT` and `End_Post-LDT` are
  `WriteEvents` rows *between* the states, and the harness chains them per
  object exactly as the corpus does, so each is its own `(from, to)` pair.
* the dependence's reaction is decided by the depended-upon block's *status*,
  and a block **in PM is Down**. That is why a dependent can be idled out of a
  state the repair/PM mechanisms put it in -- `End_Post-LDT -> Idle` below --
  and equally why it cannot: `BackFlowDependencies`'s `Down` arm is gated on
  `tempBlock.opStatus IN (Running, Standby)`, so a dependent that is genuinely
  `Repairing` is left alone and only one whose *last logged* state was
  something else (`Failed`, `PM_is_due`) while its own status was still
  Running can be idled. Those two facts are the same mechanism, and only a
  timeline shows both.

**Every model here is `Fixed` (distribution code 19)**, so each run is exact,
needs no seed and consumes **zero** randomness -- every test below asserts
`Run.draws == 0` from the harness's own counter rather than hoping for it.
Models are small (rule B4): at most four blocks, four nodes, one run.

**What the corpus contains and this file does not reach**, and why, so the
next reader does not re-derive it:

* `Cut -> ...` and `Linked -> ...` for most states are phase-change
  bookkeeping, not any mechanism here (S9, `test_b_trans_phases.py`).
* `Repairing -> Idle`, `RepHold -> Idle` and `PM -> Idle` cannot be produced
  by a dependence at all. `BackFlowDependencies`' `Down` arm is gated on
  `tempBlock.opStatus IN (Running, Standby)`, so a
  dependent that is genuinely repairing, held or in PM is never moved. What
  *is* reachable is any `X -> Idle` whose `X` is a **log row** the block wrote
  while its status was still `Running` -- `Failed`, `PM_is_due`, `Sparing`,
  `Resources`, `End_Post-LDT`, `pmReqDefer` -- and those are what the
  dependence tests here pin.
* `X -> Repaired` for every `X` except `Repairing` cannot be produced either:
  `Repair` takes its `ChangeBlockState(Repairing, ...)` transition
  unconditionally before the repair's wait (ported at
  `block_lifecycle.py:3813`) and writes the `Repaired` row immediately after
  it, so a `Repaired` row is always preceded by a `Repairing` one.
* `Block RepHold -> End_Partial_pre` and `Block RepHold -> End_Partial_pos`
  are **an engine finding, not an unreachable transition**. A kickoff
  down-start (`SimStart = 5`) reaches both rows, but under their *full* names:
  the port writes `End_Partial_pre-LDT` and `End_Partial_post-LDT`. No state
  string anywhere in the 318-row corpus is longer than **15** characters, and
  these two are exactly its 15-character truncations --
  `End_Partial_pre-LDT`[:15] and `End_Partial_post-LDT`[:15].
  `eventlog.format_event_log_line` *pads* the state column to 15 and never
  truncates, so these two corpus rows cannot be asserted by any literal until
  `WriteEvents` truncates the way the corpus shows it doing. Everything else
  the corpus records for a block, this file or another reaches.
"""
from __future__ import annotations

from typing import Any

from . import harness
from .harness import FIXED, assert_timeline

#: One block row for a spec dict: a `Fixed` life (fails at *fail*) and a
#: `Fixed` repair (*repair* hours later). `Fixed`'s draw is `param[0]` and
#: touches no stream, which is what makes every timeline below exact.
def block(name: str, fail: float, repair: float, **extra: Any) -> dict[str, Any]:
    """One block for a spec dict, with a `Fixed` life and repair."""
    return {"name": name, "fail_dist": FIXED, "fail_params": [fail],
            "repair_dist": FIXED, "repair_params": [repair], **extra}


#: A periodic PM on *name*: due every *freq* hours, lasting *dur*, with both
#: cross-resets off. `PMRefresh`/`PMFailReset` are S4-on-S4 options rather
#: than part of "a PM fires", and leaving them on would restart the failure
#: clock and hide the failures these models are built around.
def periodic(name: str, freq: float, dur: float, fail: float,
             repair: float = 10.0, **extra: Any) -> dict[str, Any]:
    """One block with a periodic PM (`PMFreq`, `Fixed` *dur* hours)."""
    return block(name, fail, repair, uses_pm=True, pm_freq=freq,
                 pm_dist=FIXED, pm_params=[dur], pm_refresh=False,
                 pm_fail_reset=False, **extra)


#: `B` depends on `A` with `DepIdle = 100%`: the deterministic member of the
#: reaction set. `draw_dependency_action` short-circuits it to `"GoIdle"` with
#: no draw at all, which is why these models stay draw-free. `DepNum` is an
#: **id**, not a name, and `build_from_spec` walks `blocks` before `nodes`, so
#: the first block in the list is id 1.
DEPENDENT_ON_A = {"dep_type": "RBDBlock", "dep_num": 1, "dep_idle": 100.0,
                  "dep_nothing": 0.0, "dep_pm": 0.0, "dep_fail": 0.0}


def _one_block(blocks: list[dict[str, Any]], length: float,
               **extra: Any) -> dict[str, Any]:
    """`Start -> A -> End`, the least topology the loader accepts."""
    spec = {
        "system": {"run_length": length, "num_runs": 1,
                   "termination": "TimeSim"},
        "blocks": blocks,
        "nodes": [{"name": "Start", "type": 1, "n": 0, "out": 1},
                  {"name": "End", "type": 3, "n": 1, "out": 0}],
        "links": [{"from": "Start", "to": "A"}, {"from": "A", "to": "End"}],
    }
    spec.update(extra)
    return spec


def _parallel_pair(blocks: list[dict[str, Any]], length: float,
                   **extra: Any) -> dict[str, Any]:
    """S2's shape: `Start -> A, B -> Join(k of n) -> End`, one run of *length*.

    Two blocks in parallel with a 1-of-2 `Join` is the smallest topology in
    which one block going down *degrades* rather than fails the system, so a
    dependence between the two is observable without the End node going `Bad`
    first.
    """
    spec = {
        "system": {"run_length": length, "num_runs": 1,
                   "termination": "TimeSim"},
        "blocks": blocks,
        "nodes": [{"name": "Start", "type": 1, "n": 0, "out": 2},
                  {"name": "Join", "type": 2, "n": 2, "out": 1, "k": 1},
                  {"name": "End", "type": 3, "n": 1, "out": 0}],
        "links": [{"from": "Start", "to": "A"}, {"from": "A", "to": "Join"},
                  {"from": "Start", "to": "B"}, {"from": "B", "to": "Join"},
                  {"from": "Join", "to": "End"}],
    }
    spec.update(extra)
    return spec


# ---------------------------------------------------------------------------
# Logistics delay time: a state, not a comment
# ---------------------------------------------------------------------------

def test_a_pre_and_a_post_ldt_column_each_hold_the_block_in_rephold() -> None:
    """Both LDT columns hold the block in `RepHold`: the pre-LDT before the
    spare is taken, the post-LDT after the repair has already finished.

    Why: the two LDT columns are guarded separately by source's
    `NOT((preDist=19) AND (preParams[1]=0.0))` (mirrored
    for the post-LDT), and each armed column costs a real state:
    the pre-LDT wait is `ChangeBlockState(RepHold, activeStatus, "LDT")` plus
    the delay, and the post-LDT wait is the same transition with the comment
    `"postLDT"`, taken *after* the `Repaired` line and the `TakeBack` that
    hands the repair resource back. So one repair
    visits `RepHold` twice and the `Repaired` row is sandwiched between them --
    which is exactly the order a `Repaired -> RepHold` row records, and why
    the two LDT rows (`End_Pre-LDT`, `End_Post-LDT`) are states in their own
    right in the event log rather than detail text.

    Setup: one `Fixed` block `A`, life 50 h, repair 10 h, pre-LDT
    `Fixed(5.0)`, post-LDT `Fixed(3.0)`, 100 sim hours, one run.

    Expected: `A` fails at 50 and holds in `RepHold` for five hours
    (`End_Pre-LDT` at 55, `Repairing` in the same instant), repairs for ten
    (`Repaired` at 65), and holds in `RepHold` again for three
    (`End_Post-LDT`, then `Good`, at 68). The End node is `Bad` across both
    LDT spells, because the block is not `Running` during either.

    Source: Raptor 7 (the pre-LDT guard `_has_ldt` ports),
    Raptor 7 (its post-LDT twin),
    Raptor 7 (TakeBack and the "Repaired" line, in that order);
    docs/engine-design/block-lifecycle.adoc (Structure: `Repair`'s steps)
    """
    spec = _one_block([block("A", 50.0, 10.0,
                              pre_dist=FIXED, pre_params=[5.0],
                              post_dist=FIXED, post_params=[3.0])], 100.0)
    result = harness.run(spec)
    # Rule B2, asserted rather than hoped for: every distribution in
    # this model is `Fixed` (code 19), whose draw is `param[0]` and
    # touches no stream, so the run must consume no randomness.
    assert result.draws == 0, (
        "rule B2: a `Fixed`-only model consumes no randomness; "
        f"this run drew {result.draws} times")
    assert_timeline(result, [
        (0.0, "Block", "A", "--", "Good"),
        (50.0, "Block", "A", "Good", "Failed"),
        # The failure itself is only a log line: the status is still Running
        # until `Repair` makes its first transition, and with the pre-LDT
        # armed that first transition is `RepHold "LDT"`.
        (50.0, "Block", "A", "Failed", "RepHold"),
        (50.0, "Node", "end", "--", "Bad"),
        (50.0, "System", "State_change", "--", "Red"),
        (55.0, "Block", "A", "RepHold", "End_Pre-LDT"),
        # `End_Pre-LDT` is a row, not a status: the block is still `RepHold`
        # and the next transition takes it straight to `Repairing`, in the
        # same instant the row is logged.
        (55.0, "Block", "A", "End_Pre-LDT", "Repairing"),
        (55.0, "System", "Red_change", "--", "Red"),
        (65.0, "Block", "A", "Repairing", "Repaired"),
        # The repair is over and the resource is already back in the pool, but
        # the armed post-LDT column puts the block in `RepHold` again.
        (65.0, "Block", "A", "Repaired", "RepHold"),
        (65.0, "System", "Red_change", "Red", "Red"),
        (68.0, "Block", "A", "RepHold", "End_Post-LDT"),
        (68.0, "Block", "A", "End_Post-LDT", "Good"),
        (68.0, "Node", "end", "Bad", "Good"),
        (68.0, "System", "State_change", "Red", "Green"),
        (100.0, "Block", "A", "Good", "Done"),
        (100.0, "System", "Phase_change", "--", "Mission_failure"),
        (100.0, "System", "Simulation_Terminate", "--", "--"),
    ])


def test_a_spare_that_arrives_during_the_pre_ldt_is_logged_as_sparing_after_it() -> None:
    """With an empty spare pool, the block sits in `RepHold` through its
    pre-LDT and the arrival's `Sparing Spare_Arrived` row lands on the
    `End_Pre-LDT` row, not on a state.

    Why: `Repair`'s spare step runs **after** the pre-LDT
    (for the two sparing types),
    and its `WAIT FOR PooledSpares[..] TO Give` resume writes
    `WriteEvents(..., "Sparing", "Spare_Arrived")`.
    The `Sparing` row is a `WriteEvents` line rather than a state change --
    the block does not move to a `Sparing` status -- so on a pool-backed
    repair it appears as `End_Pre-LDT -> Sparing` whenever the pre-LDT armed
    the wait, and as `RepHold -> Sparing` when it did not. Which of the two
    pairs a run produces is therefore a fact about the LDT column, not about
    the pool.

    Setup: one block `A` (life 50 h, repair 10 h) drawing pooled spares from
    `SP`, which starts **empty** and is restocked every 60 h; pre-LDT
    `Fixed(5.0)`, no post-LDT. 100 sim hours.

    Expected: the pool's own row is `Pool SP -- -> Sparing` at 60, and A's
    `Sparing Spare_Arrived` is at 60 too -- after `End_Pre-LDT` at 55, not
    before it. `A`'s repair then runs 60->70 and it is `Good` again.

    Source: Raptor 7 (`Repair`'s spare-pool consumption, after
    pre-LDT), Raptor 7 (the "Spare_Arrived" WriteEvents),
    Raptor 7 (`CheckForSpare`'s SparePool arm, which logs
    `Sparing No_spare_in_stock-<pool>` before the repair starts);
    docs/engine-design/block-lifecycle.adoc (Structure: `Repair`)
    """
    spec = _one_block([block("A", 50.0, 10.0, pre_dist=FIXED, pre_params=[5.0],
                             sp_type="Pooled", spare_pool="SP")], 100.0,
                      spare_pools=[{"name": "SP", "stock": 0, "new_spares": 1,
                                    "arrive_every": 60.0, "rso": 1}])
    result = harness.run(spec)
    # Rule B2, asserted rather than hoped for: every distribution in
    # this model is `Fixed` (code 19), whose draw is `param[0]` and
    # touches no stream, so the run must consume no randomness.
    assert result.draws == 0, (
        "rule B2: a `Fixed`-only model consumes no randomness; "
        f"this run drew {result.draws} times")
    assert_timeline(result, [
        (0.0, "Block", "A", "--", "Good"),
        (50.0, "Block", "A", "Good", "Failed"),
        # `CheckForSpare` runs before `Repair` and finds the pool empty, so
        # this `Sparing` row precedes the RepHold the pre-LDT transition makes.
        (50.0, "Block", "A", "Failed", "Sparing"),
        (50.0, "Block", "A", "Sparing", "RepHold"),
        (50.0, "Node", "end", "--", "Bad"),
        (50.0, "System", "State_change", "--", "Red"),
        (55.0, "Block", "A", "RepHold", "End_Pre-LDT"),
        # The pool's first restock, and the `Sparing Spare_Arrived` row the
        # block's own `WAIT FOR ... TO Give` resume writes -- five hours
        # *after* the pre-LDT ended, because the spare step comes after it.
        (60.0, "Pool", "SP", "--", "Sparing"),
        (60.0, "Block", "A", "End_Pre-LDT", "Sparing"),
        (60.0, "Block", "A", "Sparing", "Repairing"),
        (60.0, "System", "Red_change", "--", "Red"),
        (70.0, "Block", "A", "Repairing", "Repaired"),
        (70.0, "Block", "A", "Repaired", "Good"),
        (70.0, "Node", "end", "Bad", "Good"),
        (70.0, "System", "State_change", "Red", "Green"),
        (100.0, "Block", "A", "Good", "Done"),
        (100.0, "System", "Phase_change", "--", "Mission_failure"),
        (100.0, "System", "Simulation_Terminate", "--", "--"),
    ])


# ---------------------------------------------------------------------------
# Dependence: the same Down event, over four different dependent states
# ---------------------------------------------------------------------------

def test_a_repair_that_finishes_during_its_dependence_s_pm_completes_into_idle() -> None:
    """A repair that completes while what it depends on is in PM ends
    `Idle`, not `Running` -- and with a post-LDT column the `Idle` follows the
    `End_Post-LDT` row.

    Why: `DetermineNewState("Maintenance Completed")` is reached from `Repair`
   and its first act is to look at the block's
    *dependency* rather than at the block: `depBlock.opStatus` in
    `(Running, Idle)` means `Running`, `Standby` means `Standby`, and anything
    else -- RepHold, Repairing, **PM**, PMhold, Done -- is "down-like",
   which with `DepIdlePerc=100.0` routes the
    block to `Idle`. A block in PM is Down, so A's PM is enough: B's repair
    finishing at 42 while A is mid-PM completes into `Idle`, and stays there
    until A's PM completes at 50. The post-LDT column is what puts the
    `Idle` after an `End_Post-LDT` row rather than straight after `Repaired`.

    Setup: two parallel blocks. `A` never fails; it has a periodic PM every 40
    h lasting 10. `B` depends on `A` with `DepIdle = 100%`, fails at 12 and
    repairs for 30 h, and has a post-LDT column of `Fixed(5.0)`. 60 sim
    hours.

    Expected: `B Repaired` at 42, `Repaired -> RepHold` (the post-LDT wait)
    in the same instant, `End_Post-LDT` at 47 and then `Idle` -- not `Good` --
    because A is still in PM until 50. `B` is released to `Good` at 50 with
    A's `PM_complete`, and A's two hours are not operating time for either
    block.

    Source: Raptor 7 (the "Maintenance Completed" handler),
    Raptor 7 (the `depBlock.opStatus` dispatch),
    Raptor 7 (`Repair`'s call into it);
    docs/engine-design/block-lifecycle.adoc (States: "Maintenance
    Completed" can end in Idle or Standby)
    """
    spec = _parallel_pair(
        [periodic("A", 40.0, 10.0, fail=200.0),
         block("B", 12.0, 30.0, post_dist=FIXED, post_params=[5.0],
               **DEPENDENT_ON_A)], 60.0)
    result = harness.run(spec)
    # Rule B2, asserted rather than hoped for: every distribution in
    # this model is `Fixed` (code 19), whose draw is `param[0]` and
    # touches no stream, so the run must consume no randomness.
    assert result.draws == 0, (
        "rule B2: a `Fixed`-only model consumes no randomness; "
        f"this run drew {result.draws} times")
    assert_timeline(result, [
        (0.0, "Block", "A", "--", "Good"),
        (0.0, "Block", "B", "--", "Good"),
        (12.0, "Block", "B", "Good", "Failed"),
        (12.0, "Block", "B", "Failed", "Repairing"),
        (12.0, "Node", "Join", "--", "Degraded"),
        (12.0, "System", "State_change", "--", "Yellow"),
        (40.0, "Block", "A", "Good", "PM_is_due"),
        (40.0, "Block", "A", "PM_is_due", "PM"),
        (40.0, "Node", "Join", "Degraded", "NodePM"),
        (40.0, "Node", "end", "--", "NodePM"),
        (40.0, "System", "State_change", "Yellow", "Red"),
        (42.0, "Block", "B", "Repairing", "Repaired"),
        (42.0, "Block", "B", "Repaired", "RepHold"),
        (42.0, "System", "Red_change", "--", "Red"),
        # The post-LDT wait ends at 47, and "Maintenance Completed" runs with
        # A still in PM, so the block's next transition is `Idle`.
        (47.0, "Block", "B", "RepHold", "End_Post-LDT"),
        (47.0, "Block", "B", "End_Post-LDT", "Idle"),
        (47.0, "Node", "Join", "NodePM", "Idle"),
        (47.0, "Node", "end", "NodePM", "Idle"),
        (47.0, "System", "Red_change", "Red", "Red"),
        # A comes back at 50 and the AllUp dispatch releases B.
        (50.0, "Block", "A", "PM", "PM_complete"),
        (50.0, "Block", "A", "PM_complete", "Good"),
        (50.0, "Block", "B", "Idle", "Good"),
        (50.0, "Node", "Join", "Idle", "Degraded"),
        (50.0, "Node", "end", "Idle", "Good"),
        (50.0, "Node", "Join", "Degraded", "Good"),
        (50.0, "System", "State_change", "Red", "Green"),
        (60.0, "Block", "A", "Good", "Done"),
        (60.0, "Block", "B", "Good", "Done"),
        (60.0, "System", "Phase_change", "--", "Mission_failure"),
        (60.0, "System", "Simulation_Terminate", "--", "--"),
    ])


def test_a_due_pm_that_is_idled_by_the_dependence_still_runs() -> None:
    """A block whose PM comes due in the same instant its dependence goes
    down is idled first and enters `PM` anyway.

    Why: `DetermineNewState("PM is due")` has already
    written the `PM_is_due` row and committed the block to the PM by the time
    `BackFlowDependencies`'s `Down` arm runs, and
    that arm's own gate is `tempBlock.opStatus IN (Running, Standby)`. The
    dependent is still `Running` at that instant -- its `PM_is_due` row is a
    `WriteEvents` line, not a status -- so it *is* dispatched, and lands in
    `Idle`; but `_perform_pm` is already on the stack
    for that turn, so `Idle -> PM` follows immediately. This is the one place
    a block passes *through* `Idle` without waiting there, and the two rows
    are what show it: no `PM` before the `Idle`, and no `PM_complete` until
    the PM's own length has passed.

    Setup: two parallel blocks. `A` never fails, periodic PM every 20 h
    lasting 2. `B` depends on `A` with `DepIdle = 100%`, never fails, and has
    a periodic PM every 10 h lasting 5. 50 sim hours.

    Expected: the two PMs collide at t=42. Both blocks log `PM_is_due`;
    A goes `PM`; B is dispatched to `Idle` by A's `PM` and then enters `PM`
    in the same instant. A's PM ends at 44 and B's at 47, and B is `Good`
    again at 47 -- its failure clock never ran while it was idled at 20-22.

    Source: Raptor 7 (`DetermineNewState("PM is due")`),
    Raptor 7 (`BackFlowDependencies`' `Down` arm and its
    `opStatus IN (Running, Standby)` gate), Raptor 7
    (`PerformPM`); wait_sites.PM_PREREQS_TELL
    """
    spec = _parallel_pair(
        [periodic("A", 20.0, 2.0, fail=200.0),
         periodic("B", 10.0, 5.0, fail=200.0, **DEPENDENT_ON_A)], 50.0)
    result = harness.run(spec)
    # Rule B2, asserted rather than hoped for: every distribution in
    # this model is `Fixed` (code 19), whose draw is `param[0]` and
    # touches no stream, so the run must consume no randomness.
    assert result.draws == 0, (
        "rule B2: a `Fixed`-only model consumes no randomness; "
        f"this run drew {result.draws} times")
    assert_timeline(result, [
        (0.0, "Block", "A", "--", "Good"),
        (0.0, "Block", "B", "--", "Good"),
        (10.0, "Block", "B", "Good", "PM_is_due"),
        (10.0, "Block", "B", "PM_is_due", "PM"),
        (10.0, "Node", "Join", "--", "Degraded"),
        (10.0, "System", "State_change", "--", "Yellow"),
        (15.0, "Block", "B", "PM", "PM_complete"),
        (15.0, "Block", "B", "PM_complete", "Good"),
        (15.0, "Node", "Join", "Degraded", "Good"),
        (15.0, "System", "State_change", "Yellow", "Green"),
        (20.0, "Block", "A", "Good", "PM_is_due"),
        (20.0, "Block", "A", "PM_is_due", "PM"),
        # A's PM is Down, so B is idled -- and released when it completes.
        (20.0, "Block", "B", "Good", "Idle"),
        (20.0, "Node", "Join", "Good", "Degraded"),
        (20.0, "Node", "Join", "Degraded", "Idle"),
        (20.0, "Node", "end", "--", "Idle"),
        (20.0, "System", "State_change", "Green", "Red"),
        (22.0, "Block", "A", "PM", "PM_complete"),
        (22.0, "Block", "A", "PM_complete", "Good"),
        (22.0, "Block", "B", "Idle", "Good"),
        (22.0, "Node", "Join", "Idle", "Degraded"),
        (22.0, "Node", "end", "Idle", "Good"),
        (22.0, "Node", "Join", "Degraded", "Good"),
        (22.0, "System", "State_change", "Red", "Green"),
        (27.0, "Block", "B", "Good", "PM_is_due"),
        (27.0, "Block", "B", "PM_is_due", "PM"),
        (27.0, "Node", "Join", "Good", "Degraded"),
        (27.0, "System", "State_change", "Green", "Yellow"),
        (32.0, "Block", "B", "PM", "PM_complete"),
        (32.0, "Block", "B", "PM_complete", "Good"),
        (32.0, "Node", "Join", "Degraded", "Good"),
        (32.0, "System", "State_change", "Yellow", "Green"),
        # The collision: both PMs come due at 42.
        (42.0, "Block", "A", "Good", "PM_is_due"),
        (42.0, "Block", "B", "Good", "PM_is_due"),
        (42.0, "Block", "A", "PM_is_due", "PM"),
        (42.0, "Block", "B", "PM_is_due", "Idle"),
        (42.0, "Node", "Join", "Good", "Degraded"),
        (42.0, "Node", "Join", "Degraded", "Idle"),
        (42.0, "Node", "end", "Good", "Idle"),
        (42.0, "System", "State_change", "Green", "Red"),
        # ... and B passes straight through `Idle` into its own PM.
        (42.0, "Block", "B", "Idle", "PM"),
        (42.0, "Node", "Join", "Idle", "NodePM"),
        (42.0, "Node", "end", "Idle", "NodePM"),
        (44.0, "Block", "A", "PM", "PM_complete"),
        (44.0, "Block", "A", "PM_complete", "Good"),
        (44.0, "Node", "Join", "NodePM", "Degraded"),
        (44.0, "Node", "end", "NodePM", "Good"),
        (44.0, "System", "State_change", "Red", "Yellow"),
        (47.0, "Block", "B", "PM", "PM_complete"),
        (47.0, "Block", "B", "PM_complete", "Good"),
        (47.0, "Node", "Join", "Degraded", "Good"),
        (47.0, "System", "State_change", "Yellow", "Green"),
        (50.0, "Block", "A", "Good", "Done"),
        (50.0, "Block", "B", "Good", "Done"),
        (50.0, "System", "Phase_change", "--", "Mission_failure"),
        (50.0, "System", "Simulation_Terminate", "--", "--"),
    ])


def test_a_failure_and_a_dependence_idle_in_the_same_instant_reach_repairing_from_idle() -> None:
    """When a block's own failure and its dependence going down land in the
    same instant, the order is `Failed`, `Idle`, `Repairing`.

    Why: the failure is only a `WriteEvents` row
    (`_record_failure` writes it before `Repair` is spawned,,
    so at the moment A's `PM_is_due` cascade
    dispatches, B is still `Running` and passes
    `BackFlowDependencies`' `opStatus IN (Running, Standby)` gate
   -- it is logged `Idle` in the same instant it
    failed. `Repair`'s own first transition then runs from that `Idle` status
    and takes the block straight to `Repairing`. The `Idle` here is therefore
    a *log row between* `Failed` and `Repairing`, not a spell B spends: B is
    repairing again in the same instant, and the system returns to `Yellow` as
    soon as A's PM is over.

    Setup: two parallel blocks. `A` never fails, periodic PM every 40 h
    lasting 2. `B` depends on `A` with `DepIdle = 100%`, has a periodic PM
    every 20 h lasting 5, a `Fixed` life of 16 h and a 45 h repair, and borrows
    the single unit of a `RESOURCE` pool `R`. 90 sim hours.

    Expected: B's `Fixed` 16-hour life expires at 82, the same instant A's
    third PM comes due. The log order is A `PM_is_due`, B `Failed`, A `PM`,
    B `Idle`, B `Repairing` -- so B never actually sits in `Idle`. B's repair
    would end at 127, past the run, and the run-end sweep takes it from
    `Repairing` to `Done`.

    Source: Raptor 7 (the fail-wait that logs `Failed`),
    Raptor 7 (`BackFlowDependencies`' `Down` arm and its
    `opStatus IN (Running, Standby)` gate), Raptor 7 (`Repair`);
    wait_sites.REPAIR_RESOURCE
    """
    spec = _parallel_pair(
        [periodic("A", 40.0, 2.0, fail=200.0),
         periodic("B", 20.0, 5.0, fail=16.0, repair=45.0, num_res=1,
                  resource_name="R", **DEPENDENT_ON_A)], 90.0,
        resources=[{"name": "R", "num_in": 1}])
    result = harness.run(spec)
    # Rule B2, asserted rather than hoped for: every distribution in
    # this model is `Fixed` (code 19), whose draw is `param[0]` and
    # touches no stream, so the run must consume no randomness.
    assert result.draws == 0, (
        "rule B2: a `Fixed`-only model consumes no randomness; "
        f"this run drew {result.draws} times")
    assert_timeline(result, [
        (0.0, "Block", "A", "--", "Good"),
        (0.0, "Block", "B", "--", "Good"),
        (16.0, "Block", "B", "Good", "Failed"),
        (16.0, "Block", "B", "Failed", "Repairing"),
        (16.0, "Node", "Join", "--", "Degraded"),
        (16.0, "System", "State_change", "--", "Yellow"),
        (40.0, "Block", "A", "Good", "PM_is_due"),
        (40.0, "Block", "A", "PM_is_due", "PM"),
        (40.0, "Node", "Join", "Degraded", "NodePM"),
        (40.0, "Node", "end", "--", "NodePM"),
        (40.0, "System", "State_change", "Yellow", "Red"),
        (42.0, "Block", "A", "PM", "PM_complete"),
        (42.0, "Block", "A", "PM_complete", "Good"),
        (42.0, "Node", "Join", "NodePM", "Degraded"),
        (42.0, "Node", "end", "NodePM", "Good"),
        (42.0, "System", "State_change", "Red", "Yellow"),
        (61.0, "Block", "B", "Repairing", "Repaired"),
        (61.0, "Block", "B", "Repaired", "Good"),
        (61.0, "Node", "Join", "Degraded", "Good"),
        (61.0, "System", "State_change", "Yellow", "Green"),
        (65.0, "Block", "B", "Good", "PM_is_due"),
        (65.0, "Block", "B", "PM_is_due", "PM"),
        (65.0, "Node", "Join", "Good", "Degraded"),
        (65.0, "System", "State_change", "Green", "Yellow"),
        (70.0, "Block", "B", "PM", "PM_complete"),
        (70.0, "Block", "B", "PM_complete", "Good"),
        (70.0, "Node", "Join", "Degraded", "Good"),
        (70.0, "System", "State_change", "Yellow", "Green"),
        # The collision at 82: A's PM comes due and B's life expires in the
        # same instant, and B is logged Idle before its repair starts.
        (82.0, "Block", "A", "Good", "PM_is_due"),
        (82.0, "Block", "B", "Good", "Failed"),
        (82.0, "Block", "A", "PM_is_due", "PM"),
        (82.0, "Block", "B", "Failed", "Idle"),
        (82.0, "Node", "Join", "Good", "Degraded"),
        (82.0, "Node", "Join", "Degraded", "Idle"),
        (82.0, "Node", "end", "Good", "Idle"),
        (82.0, "System", "State_change", "Green", "Red"),
        (82.0, "Block", "B", "Idle", "Repairing"),
        (84.0, "Block", "A", "PM", "PM_complete"),
        (84.0, "Block", "A", "PM_complete", "Good"),
        (84.0, "Node", "Join", "Idle", "Degraded"),
        (84.0, "Node", "end", "Idle", "Good"),
        (84.0, "System", "State_change", "Red", "Yellow"),
        (90.0, "Block", "A", "Good", "Done"),
        (90.0, "Block", "B", "Repairing", "Done"),
        (90.0, "System", "Phase_change", "--", "Mission_failure"),
        (90.0, "System", "Simulation_Terminate", "--", "--"),
    ])


def _three_way(blocks: list[dict[str, Any]], length: float,
               **extra: Any) -> dict[str, Any]:
    """`Start -> H, A, B -> Join(k of n) -> End`: a third path for the
    resource holder.

    The three-block shape exists for one reason: a `RESOURCE` pool's unit has
    to be *held by somebody else* for a PM to be refused, so the deferral
    models below need a block whose repair spans the whole run.
    """
    spec = {
        "system": {"run_length": length, "num_runs": 1,
                   "termination": "TimeSim"},
        "blocks": blocks,
        "nodes": [{"name": "Start", "type": 1, "n": 0, "out": 3},
                  {"name": "Join", "type": 2, "n": 3, "out": 1, "k": 1},
                  {"name": "End", "type": 3, "n": 1, "out": 0}],
        "links": [{"from": "Start", "to": n} for n in ("H", "A", "B")]
                 + [{"from": n, "to": "Join"} for n in ("H", "A", "B")]
                 + [{"from": "Join", "to": "End"}],
    }
    spec.update(extra)
    return spec


def _cold_second_stage(blocks: list[dict[str, Any]], length: float) -> dict[str, Any]:
    """`Start -> A -> J1 -> C, D -> Cold -> End`, a cold group of two whose
    **standby** member `D` depends on `A`.

    The cold group's own node is the third node here, and `D`'s dependence on
    `A` is legal because `A` is not a candidate of that cold node --
    `refuse_unrunnable` only refuses a cold candidate that depends on a *sibling
    candidate of the same node* (`model_checks.py`), which is the shape real
    Raptor 7.0.11 crashes on. So this is the smallest arrangement in which a
    block can be in `Standby` *and* be dispatched by a dependence.
    """
    return {
        "system": {"run_length": length, "num_runs": 1,
                   "termination": "TimeSim"},
        "blocks": blocks,
        "nodes": [
            {"name": "Start", "type": 1, "n": 0, "out": 1},
            {"name": "J1", "type": 2, "n": 1, "out": 2, "k": 1},
            {"name": "Cold", "type": 2, "n": 2, "out": 1, "k": 1, "cold": 1,
             "k_star": 1, "pr": 0, "autos": 0},
            {"name": "End", "type": 3, "n": 1, "out": 0},
        ],
        "links": [
            {"from": "Start", "to": "A"}, {"from": "A", "to": "J1"},
            {"from": "J1", "to": "C"}, {"from": "J1", "to": "D"},
            {"from": "C", "to": "Cold", "cold_pri": 10, "a_prob": 1.0,
             "auto_time": 0.0, "man_time": -1.0},
            {"from": "D", "to": "Cold", "cold_pri": 20, "a_prob": 1.0,
             "auto_time": 0.0, "man_time": -1.0},
            {"from": "Cold", "to": "End"},
        ],
    }


#: `B` depends on `A` in the two-block models above. In the three-block
#: models `A` is the *second* block, so its id is 2 and the dependence needs
#: the id, not the name.
DEPENDENT_ON_SECOND = {"dep_type": "RBDBlock", "dep_num": 2, "dep_idle": 100.0,
                       "dep_nothing": 0.0, "dep_pm": 0.0, "dep_fail": 0.0}

#: One `RESOURCE` unit. Only `num_res=1` borrows at all -- source tests
#: `numDiffRes=1` exactly -- so the model that wants the
#: unit held writes `num_res=1` on every borrowing block.
ONE_RESOURCE = [{"name": "R", "num_in": 1}]


# ---------------------------------------------------------------------------
# PMheld vs deferred: `pmHold`, `pmReqDefer` and the `Resources` row
# ---------------------------------------------------------------------------

def test_a_deferred_pm_spare_survives_its_block_and_a_block_that_runs_out_of_spares_waits_the_run_out() -> None:
    """A block whose PM could not get a pooled spare defers, goes on running,
    and its next failure then parks it in `RepHold` for the rest of the run.

    Why: with `PMReqDefer` set, `ObtainPMprereqs`' spare branch does **not**
    hold in `PMhold`; it writes a hand-built `pmReqDefer` /
    `No_spare_in_stock-<pool>` row and calls `Run` again,
   leaving the `WAIT FOR ... TO Give` queued so
    the eventual grant is not lost. The block then carries on in its ordinary
    fail-wait -- which is why `BackFlowDependencies` can still dispatch it and
    log `pmReqDefer -> Idle`, and why the `Failed -> Sparing` / `Sparing ->
    RepHold` pair of the *next* failure is untouched by the abandoned request.
    The pool's next restock is 200 h away, so the repair waits out the run and
    the run-end sweep takes `RepHold -> Done`.

    Setup: two parallel blocks. `A` never fails and PMs every 20 h for 2. `B`
    depends on `A` (`DepIdle = 100%`), fails at 18, repairs for 30 h, PMs every
    20 h for 5, and draws pooled spares from `SP` (stock 1, restock every 200
    h). 100 sim hours.

    Expected: B's first repair takes the pool's only spare at 18, so its PM at
    50 finds the pool empty and logs `pmReqDefer`. At 64 A's third PM idles
    B -- straight from the `pmReqDefer` row -- and releases it at 66. B fails
    again at 68, parks in `RepHold` on the empty pool at 68, and is `Done` at
    100 with the run.

    Source: Raptor 7 (`ObtainPMprereqs`' SparePool branch),
    Raptor 7 (its pmReqDefer branch),
    Raptor 7 (`BackFlowDependencies`' `Down` arm);
    Raptor 7 (the end-of-run element sweep);
    wait_sites.REPAIR_POOL_SPARE
    """
    spec = _parallel_pair(
        [periodic("A", 20.0, 2.0, fail=200.0),
         periodic("B", 20.0, 5.0, fail=18.0, repair=30.0, sp_type="Pooled",
                  spare_pool="SP", **DEPENDENT_ON_A)], 100.0,
        spare_pools=[{"name": "SP", "stock": 1, "new_spares": 1,
                      "arrive_every": 200.0, "rso": 1}])
    result = harness.run(spec)
    # Rule B2, asserted rather than hoped for: every distribution in
    # this model is `Fixed` (code 19), whose draw is `param[0]` and
    # touches no stream, so the run must consume no randomness.
    assert result.draws == 0, (
        "rule B2: a `Fixed`-only model consumes no randomness; "
        f"this run drew {result.draws} times")
    assert_timeline(result, [
        (0.0, "Block", "A", "--", "Good"),
        (0.0, "Block", "B", "--", "Good"),
        (18.0, "Block", "B", "Good", "Failed"),
        (18.0, "Block", "B", "Failed", "Repairing"),
        (18.0, "Node", "Join", "--", "Degraded"),
        (18.0, "System", "State_change", "--", "Yellow"),
        (20.0, "Block", "A", "Good", "PM_is_due"),
        (20.0, "Block", "A", "PM_is_due", "PM"),
        (20.0, "Node", "Join", "Degraded", "NodePM"),
        (20.0, "Node", "end", "--", "NodePM"),
        (20.0, "System", "State_change", "Yellow", "Red"),
        (22.0, "Block", "A", "PM", "PM_complete"),
        (22.0, "Block", "A", "PM_complete", "Good"),
        (22.0, "Node", "Join", "NodePM", "Degraded"),
        (22.0, "Node", "end", "NodePM", "Good"),
        (22.0, "System", "State_change", "Red", "Yellow"),
        (42.0, "Block", "A", "Good", "PM_is_due"),
        (42.0, "Block", "A", "PM_is_due", "PM"),
        (42.0, "Node", "Join", "Degraded", "NodePM"),
        (42.0, "Node", "end", "Good", "NodePM"),
        (42.0, "System", "State_change", "Yellow", "Red"),
        (44.0, "Block", "A", "PM", "PM_complete"),
        (44.0, "Block", "A", "PM_complete", "Good"),
        (44.0, "Node", "Join", "NodePM", "Degraded"),
        (44.0, "Node", "end", "NodePM", "Good"),
        (44.0, "System", "State_change", "Red", "Yellow"),
        (48.0, "Block", "B", "Repairing", "Repaired"),
        (48.0, "Block", "B", "Repaired", "Good"),
        (48.0, "Node", "Join", "Degraded", "Good"),
        (48.0, "System", "State_change", "Yellow", "Green"),
        # The PM needs a spare, the pool has none: defer and carry on.
        (50.0, "Block", "B", "Good", "PM_is_due"),
        (50.0, "Block", "B", "PM_is_due", "pmReqDefer"),
        # A goes down 14 hours later and B is still Running, so the
        # dependence's `Down` arm takes it out of the deferral row.
        (64.0, "Block", "A", "Good", "PM_is_due"),
        (64.0, "Block", "A", "PM_is_due", "PM"),
        (64.0, "Block", "B", "pmReqDefer", "Idle"),
        (64.0, "Node", "Join", "Good", "Degraded"),
        (64.0, "Node", "Join", "Degraded", "Idle"),
        (64.0, "Node", "end", "Good", "Idle"),
        (64.0, "System", "State_change", "Green", "Red"),
        (66.0, "Block", "A", "PM", "PM_complete"),
        (66.0, "Block", "A", "PM_complete", "Good"),
        (66.0, "Block", "B", "Idle", "Good"),
        (66.0, "Node", "Join", "Idle", "Degraded"),
        (66.0, "Node", "end", "Idle", "Good"),
        (66.0, "Node", "Join", "Degraded", "Good"),
        (66.0, "System", "State_change", "Red", "Green"),
        # The next failure finds the pool empty again and parks in RepHold.
        (68.0, "Block", "B", "Good", "Failed"),
        (68.0, "Block", "B", "Failed", "Sparing"),
        (68.0, "Block", "B", "Sparing", "RepHold"),
        (68.0, "Node", "Join", "Good", "Degraded"),
        (68.0, "System", "State_change", "Green", "Yellow"),
        (86.0, "Block", "A", "Good", "PM_is_due"),
        (86.0, "Block", "A", "PM_is_due", "PM"),
        (86.0, "Node", "Join", "Degraded", "NodePM"),
        (86.0, "Node", "end", "Good", "NodePM"),
        (86.0, "System", "State_change", "Yellow", "Red"),
        (88.0, "Block", "A", "PM", "PM_complete"),
        (88.0, "Block", "A", "PM_complete", "Good"),
        (88.0, "Node", "Join", "NodePM", "Degraded"),
        (88.0, "Node", "end", "NodePM", "Good"),
        (88.0, "System", "State_change", "Red", "Yellow"),
        # The pool's next restock is at 200, so the run ends first.
        (100.0, "Block", "A", "Good", "Done"),
        (100.0, "Block", "B", "RepHold", "Done"),
        (100.0, "System", "Phase_change", "--", "Mission_failure"),
        (100.0, "System", "Simulation_Terminate", "--", "--"),
    ])


def test_a_deferred_pm_spare_that_arrives_while_the_block_is_idled_starts_the_pm_from_idle() -> None:
    """A deferred PM spare that arrives while its block is idled is logged as
    `Sparing` on the `Idle` row and the PM starts from `Idle`.

    Why: the deferred `WAIT FOR PooledSpares[..] TO Give` is still queued, and
    the grant's `Pool_Available` wake is delivered at whatever wait site the
    block is parked at. In this run that site is the dependence's ZeroWait,
    because A's PM covers the arrival; the wake is reconciled by
    `_claim_pm_grant`, which replays the tail of source's PM spare wait
   and writes `Sparing Spare_Arrived`. Since
    `Sparing` is a `WriteEvents` row and not a status, the row lands on
    whatever the block's last logged state was -- `Idle` here -- and the PM's
    own `PM` transition follows it in the same instant. The block never
    returns to `Running` in between, which is why the run's node colour goes
    `Idle -> NodePM` rather than back through `Degraded`.

    Setup: two parallel blocks. `A` never fails and PMs every 20 h **for 10**,
    so its PM window (50-60) contains the pool's restock. `B` depends on `A`,
    fails at 18, repairs for 30 h, PMs every 20 h for 5, and draws pooled
    spares from `SP` (stock 1, restock every 55 h). 140 sim hours.

    Expected: B's repair at 18 takes the pool's only spare. B's PM at 50 finds
    the pool empty and defers (`PM_is_due`, `pmReqDefer`), and A's PM in the
    same instant idles it. The restock at 55 is logged on `B`'s `Idle` row,
    and B runs its PM from `Idle` at 55, completing at 60 -- and stays idled
    afterwards, because A's PM is still running until 60.

    Source: Raptor 7 (the pmReqDefer spare branch),
    Raptor 7 (the tail replayed on pickup, which writes
    `Sparing Spare_Arrived`), Raptor 7 (the `WAIT FOR ... TO Give`
    the deferral leaves queued); docs/engine-design/pools-spares-resources.adoc
    (Structure: two-stage `Give`)
    """
    spec = _parallel_pair(
        [periodic("A", 20.0, 10.0, fail=200.0),
         periodic("B", 20.0, 5.0, fail=18.0, repair=30.0, sp_type="Pooled",
                  spare_pool="SP", **DEPENDENT_ON_A)], 140.0,
        spare_pools=[{"name": "SP", "stock": 1, "new_spares": 1,
                      "arrive_every": 55.0, "rso": 1}])
    result = harness.run(spec)
    # Rule B2, asserted rather than hoped for: every distribution in
    # this model is `Fixed` (code 19), whose draw is `param[0]` and
    # touches no stream, so the run must consume no randomness.
    assert result.draws == 0, (
        "rule B2: a `Fixed`-only model consumes no randomness; "
        f"this run drew {result.draws} times")
    assert_timeline(result, [
        (0.0, "Block", "A", "--", "Good"),
        (0.0, "Block", "B", "--", "Good"),
        (18.0, "Block", "B", "Good", "Failed"),
        (18.0, "Block", "B", "Failed", "Repairing"),
        (18.0, "Node", "Join", "--", "Degraded"),
        (18.0, "System", "State_change", "--", "Yellow"),
        (20.0, "Block", "A", "Good", "PM_is_due"),
        (20.0, "Block", "A", "PM_is_due", "PM"),
        (20.0, "Node", "Join", "Degraded", "NodePM"),
        (20.0, "Node", "end", "--", "NodePM"),
        (20.0, "System", "State_change", "Yellow", "Red"),
        (30.0, "Block", "A", "PM", "PM_complete"),
        (30.0, "Block", "A", "PM_complete", "Good"),
        (30.0, "Node", "Join", "NodePM", "Degraded"),
        (30.0, "Node", "end", "NodePM", "Good"),
        (30.0, "System", "State_change", "Red", "Yellow"),
        (48.0, "Block", "B", "Repairing", "Repaired"),
        (48.0, "Block", "B", "Repaired", "Good"),
        (48.0, "Node", "Join", "Degraded", "Good"),
        (48.0, "System", "State_change", "Yellow", "Green"),
        # Both PMs come due at 50; B's finds the pool empty.
        (50.0, "Block", "A", "Good", "PM_is_due"),
        (50.0, "Block", "B", "Good", "PM_is_due"),
        (50.0, "Block", "B", "PM_is_due", "pmReqDefer"),
        (50.0, "Block", "A", "PM_is_due", "PM"),
        (50.0, "Block", "B", "pmReqDefer", "Idle"),
        (50.0, "Node", "Join", "Good", "Degraded"),
        (50.0, "Node", "Join", "Degraded", "Idle"),
        (50.0, "Node", "end", "Good", "Idle"),
        (50.0, "System", "State_change", "Green", "Red"),
        # The restock lands on the `Idle` row, and the PM starts from there.
        (55.0, "Pool", "SP", "--", "Sparing"),
        (55.0, "Block", "B", "Idle", "Sparing"),
        (55.0, "Block", "B", "Sparing", "PM"),
        (55.0, "Node", "Join", "Idle", "NodePM"),
        (55.0, "Node", "end", "Idle", "NodePM"),
        (55.0, "System", "Red_change", "--", "Red"),
        (60.0, "Block", "A", "PM", "PM_complete"),
        (60.0, "Block", "A", "PM_complete", "Good"),
        (60.0, "Node", "Join", "NodePM", "Degraded"),
        (60.0, "Node", "end", "NodePM", "Good"),
        (60.0, "Block", "B", "PM", "PM_complete"),
        (60.0, "Block", "B", "PM_complete", "Good"),
        (60.0, "Node", "Join", "Degraded", "Good"),
        (60.0, "System", "State_change", "Red", "Green"),
        (76.0, "Block", "B", "Good", "Failed"),
        (76.0, "Block", "B", "Failed", "Sparing"),
        (76.0, "Block", "B", "Sparing", "RepHold"),
        (76.0, "Node", "Join", "Good", "Degraded"),
        (76.0, "System", "State_change", "Green", "Yellow"),
        (80.0, "Block", "A", "Good", "PM_is_due"),
        (80.0, "Block", "A", "PM_is_due", "PM"),
        (80.0, "Node", "Join", "Degraded", "NodePM"),
        (80.0, "Node", "end", "Good", "NodePM"),
        (80.0, "System", "State_change", "Yellow", "Red"),
        (90.0, "Block", "A", "PM", "PM_complete"),
        (90.0, "Block", "A", "PM_complete", "Good"),
        (90.0, "Node", "Join", "NodePM", "Degraded"),
        (90.0, "Node", "end", "NodePM", "Good"),
        (90.0, "System", "State_change", "Red", "Yellow"),
        (110.0, "Pool", "SP", "Sparing", "Sparing"),
        (110.0, "Block", "A", "Good", "PM_is_due"),
        (110.0, "Block", "B", "RepHold", "Sparing"),
        (110.0, "Block", "B", "Sparing", "Repairing"),
        (110.0, "Block", "A", "PM_is_due", "PM"),
        (110.0, "Node", "Join", "Degraded", "NodePM"),
        (110.0, "Node", "end", "Good", "NodePM"),
        (110.0, "System", "State_change", "Yellow", "Red"),
        (120.0, "Block", "A", "PM", "PM_complete"),
        (120.0, "Block", "A", "PM_complete", "Good"),
        (120.0, "Node", "Join", "NodePM", "Degraded"),
        (120.0, "Node", "end", "NodePM", "Good"),
        (120.0, "System", "State_change", "Red", "Yellow"),
        (140.0, "Block", "B", "Repairing", "Repaired"),
        (140.0, "Block", "B", "Repaired", "Good"),
        (140.0, "Node", "Join", "Degraded", "Good"),
        (140.0, "Block", "A", "Good", "PM_is_due"),
        (140.0, "Block", "A", "PM_is_due", "PM"),
        (140.0, "Block", "B", "Good", "Idle"),
        (140.0, "Node", "Join", "Good", "Degraded"),
        (140.0, "Node", "Join", "Degraded", "Idle"),
        (140.0, "Node", "end", "Good", "Idle"),
        (140.0, "System", "State_change", "Yellow", "Red"),
        (140.0, "Block", "A", "PM", "Done"),
        (140.0, "Block", "B", "Idle", "Done"),
        (140.0, "System", "Phase_change", "--", "Mission_failure"),
        (140.0, "System", "Simulation_Terminate", "--", "--"),
    ])


def test_a_deferred_pm_resource_granted_while_the_block_runs_is_logged_as_resources_before_the_pm() -> None:
    """A PM resource deferred at one instant and released forty hours later
    is logged `Good -> Resources`, and the PM it unblocks still completes into
    `Idle` because the dependence is still down.

    Why: `ObtainPMprereqs`' resource step queues a
    `WAIT FOR PooledSpares[resPoolNum] TO Give` even on the `pmReqDefer` path
   and abandons the PM attempt, so the block is
    back in its ordinary fail-wait when the unit comes back. The
    `Pool_Available` wake then reaches the fail-wait instead of the PM wait,
    and the reconciliation logs `WriteEvents(..., "Resources",
    "Obtained_<n>_Resource(s)")` at that ordinary wait
    site -- on whatever state the block is in, here `Running` (`Good`). The PM
    then runs and "Maintenance Completed" applies the *same* dependency rule
    as a repair, so it lands in `Idle`, not `Running`.

    Setup: three parallel blocks. `H` fails at 10 and repairs for 70 h, so it
    holds the single unit of `R` until 80. `A` never fails and PMs every 20 h
    for 10. `B` depends on `A`, borrows `R`, and PMs every 30 h for 5. 110
    sim hours.

    Expected: B's PM at 40 is refused (`pmReqDefer`) and A's PM at 50-60
    idles it. At 80 H hands `R` back and A's PM comes due in the same
    instant: B logs `Resources`, is idled again by A's PM, and runs its PM
    from `Idle` at 80, completing into `Idle` at 85.

    Source: Raptor 7 (the pmReqDefer resource branch),
    Raptor 7 (the "Obtained_n Resource(s)" row),
    Raptor 7 (the hand-back to `Run`),
    Raptor 7 ("Maintenance Completed" and the dependence);
    wait_sites.PM_HOLD_RESOURCE, wait_sites.PM_HANDBACK
    """
    spec = _three_way(
        [block("H", 10.0, 70.0, num_res=1, resource_name="R"),
         periodic("A", 20.0, 10.0, fail=200.0),
         periodic("B", 30.0, 5.0, fail=200.0, num_res=1, resource_name="R",
                  **DEPENDENT_ON_SECOND)], 110.0, resources=ONE_RESOURCE)
    result = harness.run(spec)
    # Rule B2, asserted rather than hoped for: every distribution in
    # this model is `Fixed` (code 19), whose draw is `param[0]` and
    # touches no stream, so the run must consume no randomness.
    assert result.draws == 0, (
        "rule B2: a `Fixed`-only model consumes no randomness; "
        f"this run drew {result.draws} times")
    assert_timeline(result, [
        (0.0, "Block", "H", "--", "Good"),
        (0.0, "Block", "A", "--", "Good"),
        (0.0, "Block", "B", "--", "Good"),
        (10.0, "Block", "H", "Good", "Failed"),
        (10.0, "Block", "H", "Failed", "Repairing"),
        (10.0, "Node", "Join", "--", "Degraded"),
        (10.0, "System", "State_change", "--", "Yellow"),
        (20.0, "Block", "A", "Good", "PM_is_due"),
        (20.0, "Block", "A", "PM_is_due", "PM"),
        (20.0, "Block", "B", "Good", "Idle"),
        (20.0, "Node", "Join", "Degraded", "Degraded"),
        (20.0, "Node", "Join", "Degraded", "Idle"),
        (20.0, "Node", "end", "--", "Idle"),
        (20.0, "System", "State_change", "Yellow", "Red"),
        (30.0, "Block", "A", "PM", "PM_complete"),
        (30.0, "Block", "A", "PM_complete", "Good"),
        (30.0, "Block", "B", "Idle", "Good"),
        (30.0, "Node", "Join", "Idle", "Degraded"),
        (30.0, "Node", "end", "Idle", "Good"),
        (30.0, "Node", "Join", "Degraded", "Degraded"),
        (30.0, "System", "State_change", "Red", "Yellow"),
        # The PM cannot have `R`: defer and go back to the fail-wait.
        (40.0, "Block", "B", "Good", "PM_is_due"),
        (40.0, "Block", "B", "PM_is_due", "pmReqDefer"),
        (50.0, "Block", "A", "Good", "PM_is_due"),
        (50.0, "Block", "A", "PM_is_due", "PM"),
        (50.0, "Block", "B", "pmReqDefer", "Idle"),
        (50.0, "Node", "Join", "Degraded", "Degraded"),
        (50.0, "Node", "Join", "Degraded", "Idle"),
        (50.0, "Node", "end", "Good", "Idle"),
        (50.0, "System", "State_change", "Yellow", "Red"),
        (60.0, "Block", "A", "PM", "PM_complete"),
        (60.0, "Block", "A", "PM_complete", "Good"),
        (60.0, "Block", "B", "Idle", "Good"),
        (60.0, "Node", "Join", "Idle", "Degraded"),
        (60.0, "Node", "end", "Idle", "Good"),
        (60.0, "Node", "Join", "Degraded", "Degraded"),
        (60.0, "System", "State_change", "Red", "Yellow"),
        # H hands `R` back at 80, in the same instant A's next PM comes due.
        # The grant is reconciled at B's fail-wait, hence on a `Good` row.
        (80.0, "Block", "H", "Repairing", "Repaired"),
        (80.0, "Block", "H", "Repaired", "Good"),
        (80.0, "Node", "Join", "Degraded", "Good"),
        (80.0, "Block", "A", "Good", "PM_is_due"),
        (80.0, "Block", "B", "Good", "Resources"),
        (80.0, "Block", "A", "PM_is_due", "PM"),
        (80.0, "Block", "B", "Resources", "Idle"),
        (80.0, "Node", "Join", "Good", "Degraded"),
        (80.0, "Node", "Join", "Degraded", "Degraded"),
        (80.0, "Block", "B", "Idle", "PM"),
        (80.0, "Node", "Join", "Degraded", "Degraded"),
        # The PM completes while A is still in PM, so it completes into Idle.
        (85.0, "Block", "B", "PM", "PM_complete"),
        (85.0, "Block", "B", "PM_complete", "Idle"),
        (85.0, "Node", "Join", "Degraded", "Degraded"),
        (90.0, "Block", "H", "Good", "Failed"),
        (90.0, "Block", "A", "PM", "PM_complete"),
        (90.0, "Block", "A", "PM_complete", "Good"),
        (90.0, "Block", "B", "Idle", "Good"),
        (90.0, "Node", "Join", "Degraded", "Degraded"),
        (90.0, "Node", "Join", "Degraded", "Good"),
        (90.0, "Block", "H", "Failed", "Repairing"),
        (90.0, "Node", "Join", "Good", "Degraded"),
        (110.0, "Block", "A", "Good", "PM_is_due"),
        (110.0, "Block", "A", "PM_is_due", "PM"),
        (110.0, "Block", "B", "Good", "Idle"),
        (110.0, "Node", "Join", "Degraded", "Degraded"),
        (110.0, "Node", "Join", "Degraded", "Idle"),
        (110.0, "Node", "end", "Good", "Idle"),
        (110.0, "System", "State_change", "Yellow", "Red"),
        (110.0, "Block", "H", "Repairing", "Done"),
        (110.0, "Block", "A", "PM", "Done"),
        (110.0, "Block", "B", "Idle", "Done"),
        (110.0, "System", "Phase_change", "--", "Mission_failure"),
        (110.0, "System", "Simulation_Terminate", "--", "--"),
    ])


def test_the_same_deferred_grant_landing_on_an_idled_block_is_logged_on_the_idle_row() -> None:
    """The identical deferred PM resource, but granted while its block is
    `Idle`, is logged `Idle -> Resources`.

    Why: the `Resources` row is written wherever the `Pool_Available` wake is
    *reconciled*, not wherever the request was made,
   and the dependence's ZeroWait is a delivery site
    for that wake. So the same mechanism that produced `Good -> Resources`
    above produces `Idle -> Resources` here, and the PM that follows still
    completes into `Idle`. The only difference
    between the two runs is A's PM length, which decides whether the unit
    comes back inside a PM window.

    Setup: as the previous model but with `H` repairing for 50 h and `A`'s PM
    lasting 15 h, so the unit comes back at 60 -- inside A's PM window
    (55-70) -- and while B is idled.

    Expected: B defers at 20, is idled 20-35, and logs `Resources` on its
    `Idle` row at 60, then runs the PM from `Idle` and completes into `Idle`
    at 65. A second deferral at 90 repeats the first.

    Source: Raptor 7 (the "Obtained_n Resource(s)" row),
    Raptor 7 (the deferral that leaves the request queued),
    Raptor 7 ("Maintenance Completed" applying the dependence);
    wait_sites.IDLE
    """
    spec = _three_way(
        [block("H", 10.0, 50.0, num_res=1, resource_name="R"),
         periodic("A", 20.0, 15.0, fail=200.0),
         periodic("B", 20.0, 5.0, fail=200.0, num_res=1, resource_name="R",
                  **DEPENDENT_ON_SECOND)], 100.0, resources=ONE_RESOURCE)
    result = harness.run(spec)
    # Rule B2, asserted rather than hoped for: every distribution in
    # this model is `Fixed` (code 19), whose draw is `param[0]` and
    # touches no stream, so the run must consume no randomness.
    assert result.draws == 0, (
        "rule B2: a `Fixed`-only model consumes no randomness; "
        f"this run drew {result.draws} times")
    assert_timeline(result, [
        (0.0, "Block", "H", "--", "Good"),
        (0.0, "Block", "A", "--", "Good"),
        (0.0, "Block", "B", "--", "Good"),
        (10.0, "Block", "H", "Good", "Failed"),
        (10.0, "Block", "H", "Failed", "Repairing"),
        (10.0, "Node", "Join", "--", "Degraded"),
        (10.0, "System", "State_change", "--", "Yellow"),
        (20.0, "Block", "A", "Good", "PM_is_due"),
        (20.0, "Block", "B", "Good", "PM_is_due"),
        (20.0, "Block", "B", "PM_is_due", "pmReqDefer"),
        (20.0, "Block", "A", "PM_is_due", "PM"),
        (20.0, "Block", "B", "pmReqDefer", "Idle"),
        (20.0, "Node", "Join", "Degraded", "Degraded"),
        (20.0, "Node", "Join", "Degraded", "Idle"),
        (20.0, "Node", "end", "--", "Idle"),
        (20.0, "System", "State_change", "Yellow", "Red"),
        (35.0, "Block", "A", "PM", "PM_complete"),
        (35.0, "Block", "A", "PM_complete", "Good"),
        (35.0, "Block", "B", "Idle", "Good"),
        (35.0, "Node", "Join", "Idle", "Degraded"),
        (35.0, "Node", "end", "Idle", "Good"),
        (35.0, "Node", "Join", "Degraded", "Degraded"),
        (35.0, "System", "State_change", "Red", "Yellow"),
        (55.0, "Block", "A", "Good", "PM_is_due"),
        (55.0, "Block", "A", "PM_is_due", "PM"),
        (55.0, "Block", "B", "Good", "Idle"),
        (55.0, "Node", "Join", "Degraded", "Degraded"),
        (55.0, "Node", "Join", "Degraded", "Idle"),
        (55.0, "Node", "end", "Good", "Idle"),
        (55.0, "System", "State_change", "Yellow", "Red"),
        # `H` returns the unit at 60, inside A's PM window: the grant is
        # reconciled on B's `Idle` row this time.
        (60.0, "Block", "H", "Repairing", "Repaired"),
        (60.0, "Block", "H", "Repaired", "Good"),
        (60.0, "Node", "Join", "Idle", "Degraded"),
        (60.0, "Node", "end", "Idle", "Good"),
        (60.0, "Block", "B", "Idle", "Resources"),
        (60.0, "Block", "B", "Resources", "PM"),
        (60.0, "Node", "Join", "Degraded", "Degraded"),
        (60.0, "System", "State_change", "Red", "Yellow"),
        (65.0, "Block", "B", "PM", "PM_complete"),
        (65.0, "Block", "B", "PM_complete", "Idle"),
        (65.0, "Node", "Join", "Degraded", "Degraded"),
        (70.0, "Block", "A", "PM", "PM_complete"),
        (70.0, "Block", "A", "PM_complete", "Good"),
        (70.0, "Block", "B", "Idle", "Good"),
        (70.0, "Node", "Join", "Degraded", "Degraded"),
        (70.0, "Node", "Join", "Degraded", "Good"),
        (70.0, "Block", "H", "Good", "Failed"),
        (70.0, "Block", "H", "Failed", "Repairing"),
        (70.0, "Node", "Join", "Good", "Degraded"),
        (90.0, "Block", "B", "Good", "PM_is_due"),
        (90.0, "Block", "A", "Good", "PM_is_due"),
        (90.0, "Block", "B", "PM_is_due", "pmReqDefer"),
        (90.0, "Block", "A", "PM_is_due", "PM"),
        (90.0, "Block", "B", "pmReqDefer", "Idle"),
        (90.0, "Node", "Join", "Degraded", "Degraded"),
        (90.0, "Node", "Join", "Degraded", "Idle"),
        (90.0, "Node", "end", "Good", "Idle"),
        (90.0, "System", "State_change", "Yellow", "Red"),
        (100.0, "Block", "H", "Repairing", "Done"),
        (100.0, "Block", "A", "PM", "Done"),
        (100.0, "Block", "B", "Idle", "Done"),
        (100.0, "System", "Phase_change", "--", "Mission_failure"),
        (100.0, "System", "Simulation_Terminate", "--", "--"),
    ])


# ---------------------------------------------------------------------------
# The cost section's one extra log line: `Partial_Repair`
# ---------------------------------------------------------------------------

def test_a_repair_the_run_ends_in_is_logged_as_a_partial_repair_before_the_done_row() -> None:
    """A repair still running when the run ends produces two rows: the cost
    section's `Partial_Repair` and then `Done`.

    Why: `ChangeBlockState`'s cost-analysis section writes
    `WriteEvents(..., "Partial_Repair", "RepairTime="+REALTOSTR(stateTime))`
    whenever a transition **away from `Repairing`** happens while `RepWait` is
    still set, and `RepWait` is set just before
    the repair's own `WAIT DURATION TimeToRepair` and cleared only when that
    timer completes. A run that ends mid-repair
    therefore clears it never: the row is written *before* the transition's own
    line, which is why the corpus records it as a state of its own between
    `Repairing` and `Done` rather than as detail text on the `Done` row. The
    same section is what charges the repair's per-time and fixed costs, so the
    row only appears with cost analysis on.

    Setup: one block `A`, `Fixed` 50-hour life, `Fixed` 90-hour repair, cost
    analysis on, 100 sim hours.

    Expected: `A` is `Repairing` from 50 and the run ends at 100 with 40 hours
    of its 90-hour repair left, so the only rows are
    `Repairing -> Partial_Repair` and `Partial_Repair -> Done` -- and the
    system never returns to `Green`.

    Source: Raptor 7 (the `Partial_Repair` WriteEvents),
    Raptor 7 (`RepWait` set and cleared),
    Raptor 7 (the cost section it lives in);
    raptor/core/engine/accounting.py `CostAccountant.on_change`
    """
    spec = _one_block([block("A", 50.0, 90.0)], 100.0)
    result = harness.run(spec, cost_analysis=True)
    # Rule B2, asserted rather than hoped for: every distribution in
    # this model is `Fixed` (code 19), whose draw is `param[0]` and
    # touches no stream, so the run must consume no randomness.
    assert result.draws == 0, (
        "rule B2: a `Fixed`-only model consumes no randomness; "
        f"this run drew {result.draws} times")
    assert_timeline(result, [
        (0.0, "Block", "A", "--", "Good"),
        (50.0, "Block", "A", "Good", "Failed"),
        (50.0, "Block", "A", "Failed", "Repairing"),
        (50.0, "Node", "end", "--", "Bad"),
        (50.0, "System", "State_change", "--", "Red"),
        # The cost row comes first, then the transition's own `End_sim` row.
        (100.0, "Block", "A", "Repairing", "Partial_Repair"),
        (100.0, "Block", "A", "Partial_Repair", "Done"),
        (100.0, "System", "Phase_change", "--", "Mission_failure"),
        (100.0, "System", "Simulation_Terminate", "--", "--"),
    ])


# ---------------------------------------------------------------------------
# Standby and the dependence: the one status the `Down` arm also accepts
# ---------------------------------------------------------------------------

def test_a_cold_standby_member_that_depends_on_a_block_is_idled_out_of_standby() -> None:
    """`Standby` is the one non-`Running` status a dependence's `Down` arm
    will move, and a cold group's standby member moves.

    Why: `BackFlowDependencies`' `Down` arm is gated on
    `tempBlock.opStatus IN (Running, Standby)`, and
    `Standby` is in that set because a standby member is not *operating*, so
    it must follow what it depends on. Nothing else is: a dependent that is
    genuinely `Repairing`, `RepHold` or `PM` is left alone, which is why the
    same dependence cannot produce `Repairing -> Idle`. The block is put back
    by the `AllUp` arm, and the cold group puts it straight back to `Standby`
    (`Too_many_on`) in the same instant, because the running member still has
    the group's one slot.

    Setup: `Start -> A -> J1 -> C, D -> Cold -> End`. `A` never fails and PMs
    every 20 h for 8. `C` and `D` are the cold group's two candidates with
    `ColdPri` 10 and 20, so `C` keeps the slot and `D` is demoted at kickoff.
    `D` depends on `A` with `DepIdle = 100%`. `C` fails at 60. 90 sim hours.

    Expected: `D Good -> Standby` at t=0 (`Too_many_on`), then at each of A's
    PMs `D Standby -> Idle`, and back through `Good` to `Standby` in the same
    instant. When `C` fails at 60, `D` is promoted (`Standby -> Good`,
    `Prior=Standby`), and at 76 A's PM idles it from `Good` instead.

    Source: Raptor 7 (`BackFlowDependencies`' `Down` arm and its
    `opStatus IN (Running, Standby)` gate),
    Raptor 7 (`RunColdLogic`'s `Prior=Standby` promotion);
    docs/engine-design/node-status-and-cold-standby.adoc (Structure)
    """
    spec = _cold_second_stage(
        [periodic("A", 20.0, 8.0, fail=200.0),
         block("C", 60.0, 10.0),
         periodic("D", 200.0, 5.0, fail=200.0, **DEPENDENT_ON_A)], 90.0)
    result = harness.run(spec)
    # Rule B2, asserted rather than hoped for: every distribution in
    # this model is `Fixed` (code 19), whose draw is `param[0]` and
    # touches no stream, so the run must consume no randomness.
    assert result.draws == 0, (
        "rule B2: a `Fixed`-only model consumes no randomness; "
        f"this run drew {result.draws} times")
    assert_timeline(result, [
        (0.0, "Block", "A", "--", "Good"),
        (0.0, "Block", "C", "--", "Good"),
        (0.0, "Block", "D", "--", "Good"),
        # Kickoff demotion: two ready paths, one slot, `C` has the priority.
        (0.0, "Block", "D", "Good", "Standby"),
        (0.0, "Node", "Cold", "--", "Good"),
        (20.0, "Block", "A", "Good", "PM_is_due"),
        (20.0, "Block", "A", "PM_is_due", "PM"),
        (20.0, "Block", "D", "Standby", "Idle"),
        (20.0, "Node", "J1", "--", "NodePM"),
        (20.0, "Node", "end", "--", "NodePM"),
        (20.0, "System", "State_change", "--", "Red"),
        (20.0, "Block", "D", "Idle", "Good"),
        (20.0, "Block", "D", "Good", "Standby"),
        (20.0, "Node", "Cold", "Good", "Good"),
        # The cold survey runs again once D is back, and demotes it again.
        (20.0, "Block", "D", "Standby", "Idle"),
        (20.0, "Node", "Cold", "Good", "Degraded"),
        (28.0, "Block", "A", "PM", "PM_complete"),
        (28.0, "Block", "A", "PM_complete", "Good"),
        (28.0, "Block", "D", "Idle", "Good"),
        (28.0, "Node", "J1", "NodePM", "Good"),
        (28.0, "Node", "end", "NodePM", "Good"),
        (28.0, "Block", "D", "Good", "Standby"),
        (28.0, "Node", "Cold", "Degraded", "Good"),
        (28.0, "System", "State_change", "Red", "Green"),
        (48.0, "Block", "A", "Good", "PM_is_due"),
        (48.0, "Block", "A", "PM_is_due", "PM"),
        (48.0, "Block", "D", "Standby", "Idle"),
        (48.0, "Node", "J1", "Good", "NodePM"),
        (48.0, "Node", "end", "Good", "NodePM"),
        (48.0, "System", "State_change", "Green", "Red"),
        (48.0, "Block", "D", "Idle", "Good"),
        (48.0, "Block", "D", "Good", "Standby"),
        (48.0, "Node", "Cold", "Good", "Good"),
        (48.0, "Block", "D", "Standby", "Idle"),
        (48.0, "Node", "Cold", "Good", "Degraded"),
        (56.0, "Block", "A", "PM", "PM_complete"),
        (56.0, "Block", "A", "PM_complete", "Good"),
        (56.0, "Block", "D", "Idle", "Good"),
        (56.0, "Node", "J1", "NodePM", "Good"),
        (56.0, "Node", "end", "NodePM", "Good"),
        (56.0, "Block", "D", "Good", "Standby"),
        (56.0, "Node", "Cold", "Degraded", "Good"),
        (56.0, "System", "State_change", "Red", "Green"),
        # C fails and D takes the slot.
        (60.0, "Block", "C", "Good", "Failed"),
        (60.0, "Block", "C", "Failed", "Repairing"),
        (60.0, "Block", "D", "Standby", "Good"),
        (60.0, "Node", "Cold", "Good", "Degraded"),
        (60.0, "System", "State_change", "Green", "Yellow"),
        (70.0, "Block", "C", "Repairing", "Repaired"),
        (70.0, "Block", "C", "Repaired", "Good"),
        (70.0, "Block", "C", "Good", "Standby"),
        (70.0, "Node", "Cold", "Degraded", "Good"),
        (70.0, "System", "State_change", "Yellow", "Green"),
        # D is Running now, so the same dependence idles it from `Good`.
        (76.0, "Block", "A", "Good", "PM_is_due"),
        (76.0, "Block", "A", "PM_is_due", "PM"),
        (76.0, "Block", "D", "Good", "Idle"),
        (76.0, "Node", "J1", "Good", "NodePM"),
        (76.0, "Node", "end", "Good", "NodePM"),
        (76.0, "System", "State_change", "Green", "Red"),
        (76.0, "Block", "D", "Idle", "Good"),
        (76.0, "Node", "Cold", "Good", "Good"),
        (76.0, "Block", "D", "Good", "Idle"),
        (76.0, "Block", "C", "Standby", "Good"),
        (76.0, "Node", "Cold", "Good", "Degraded"),
        (84.0, "Block", "A", "PM", "PM_complete"),
        (84.0, "Block", "A", "PM_complete", "Good"),
        (84.0, "Block", "D", "Idle", "Good"),
        (84.0, "Node", "J1", "NodePM", "Good"),
        (84.0, "Node", "end", "NodePM", "Good"),
        (84.0, "Block", "C", "Good", "Standby"),
        (84.0, "Node", "Cold", "Degraded", "Good"),
        (84.0, "System", "State_change", "Red", "Green"),
        (90.0, "Block", "A", "Good", "Done"),
        (90.0, "Block", "C", "Standby", "Done"),
        (90.0, "Block", "D", "Good", "Done"),
        (90.0, "System", "Phase_change", "--", "Mission_failure"),
        (90.0, "System", "Simulation_Terminate", "--", "--"),
    ])


def _cold_second_stage_four(blocks: list[dict[str, Any]],
                            length: float) -> dict[str, Any]:
    """The cold group plus a **non**-cold dependent: `Start -> A, B`, then
    `A -> J1 -> C, D`, `C, D -> Cold -> End` and `B -> End`.

    Two arrangements are needed and only one of them can be built at a time.
    A dependent that is itself *Running* when the cold group's kickoff demotion
    fires is switched to `Standby` itself by the group's `NodeStandby`
    back-flow, and a standby block's clock is parked -- so it never fails and
    never repairs. Putting `B` outside the cold group is not enough on its
    own; `B` must also be *out of the run-wait* the back-flow will dispatch
    into, which is why the one test that wants a repair completing into
    `Standby` gives `B` a kickoff down-start repair instead of an ordinary
    life.
    """
    return {
        "system": {"run_length": length, "num_runs": 1,
                   "termination": "TimeSim"},
        "blocks": blocks,
        "nodes": [
            {"name": "Start", "type": 1, "n": 0, "out": 2},
            {"name": "J1", "type": 2, "n": 1, "out": 2, "k": 1},
            {"name": "Cold", "type": 2, "n": 2, "out": 1, "k": 1, "cold": 1,
             "k_star": 1, "pr": 0, "autos": 0},
            {"name": "End", "type": 3, "n": 2, "out": 0},
        ],
        "links": [
            {"from": "Start", "to": "A"}, {"from": "Start", "to": "B"},
            {"from": "A", "to": "J1"},
            {"from": "J1", "to": "C"}, {"from": "J1", "to": "D"},
            {"from": "C", "to": "Cold", "cold_pri": 10, "a_prob": 1.0,
             "auto_time": 0.0, "man_time": -1.0},
            {"from": "D", "to": "Cold", "cold_pri": 20, "a_prob": 1.0,
             "auto_time": 0.0, "man_time": -1.0},
            {"from": "Cold", "to": "End"},
            {"from": "B", "to": "End"},
        ],
    }


# ---------------------------------------------------------------------------
# Two rows that are states only because a *log row* sits between them
# ---------------------------------------------------------------------------

def test_a_pm_spare_that_arrives_in_the_same_instant_the_dependence_goes_down_is_idled_after_the_sparing_row() -> None:
    """A `Sparing Spare_Arrived` row can be followed immediately by `Idle`:
    the block passes through the spare wait and out again.

    Why: the deferred PM spare's grant is reconciled wherever the block
    happens to be parked, and the `Sparing Spare_Arrived` row it writes
   is not a status. In this run the pool's restock
    at 120 lands in the **same instant** A's next PM comes due, so the order
    inside the instant is: A `PM_is_due`, B's `Sparing Spare_Arrived`, A `PM`,
    then B dispatched to `Idle`, and finally B's own `PM` transition. The
    three rows `pmReqDefer -> Sparing -> Idle -> PM` all have the same sim
    time, and only the timeline says so -- a set of states would read as "the
    spare arrived, the block idled, the PM ran", which is right, but would not
    show that nothing happened in between.

    Setup: two parallel blocks. `A` never fails and PMs every 20 h for 5. `B`
    depends on `A` (`DepIdle = 100%`), fails at 18, repairs for 30 h, PMs every
    20 h for 5, and draws pooled spares from `SP` (stock 1, restock every 40
    h, so arrivals fall at 40, 80 and 120). 130 sim hours.

    Expected: the sequence at t=120 is `A PM_is_due`, `Pool SP Sparing`,
    `B pmReqDefer -> Sparing`, `A PM`, `B Sparing -> Idle`, `B Idle -> PM`,
    and B's PM completes at 125. The run ends `Green`, so the two `Red`
    periods (45-50 and 95-100, when A was in PM) are the only ones.

    Source: Raptor 7 (the pickup tail that writes
    `Sparing Spare_Arrived`), Raptor 7
    (`BackFlowDependencies`' `Down` arm);
    docs/engine-design/pools-spares-resources.adoc (Structure)
    """
    spec = _parallel_pair(
        [periodic("A", 20.0, 5.0, fail=200.0),
         periodic("B", 20.0, 5.0, fail=18.0, repair=30.0, sp_type="Pooled",
                  spare_pool="SP", **DEPENDENT_ON_A)], 130.0,
        spare_pools=[{"name": "SP", "stock": 1, "new_spares": 1,
                      "arrive_every": 40.0, "rso": 1}])
    result = harness.run(spec)
    # Rule B2, asserted rather than hoped for: every distribution in
    # this model is `Fixed` (code 19), whose draw is `param[0]` and
    # touches no stream, so the run must consume no randomness.
    assert result.draws == 0, (
        "rule B2: a `Fixed`-only model consumes no randomness; "
        f"this run drew {result.draws} times")
    assert_timeline(result, [
        (0.0, "Block", "A", "--", "Good"),
        (0.0, "Block", "B", "--", "Good"),
        (18.0, "Block", "B", "Good", "Failed"),
        (18.0, "Block", "B", "Failed", "Repairing"),
        (18.0, "Node", "Join", "--", "Degraded"),
        (18.0, "System", "State_change", "--", "Yellow"),
        (20.0, "Block", "A", "Good", "PM_is_due"),
        (20.0, "Block", "A", "PM_is_due", "PM"),
        (20.0, "Node", "Join", "Degraded", "NodePM"),
        (20.0, "Node", "end", "--", "NodePM"),
        (20.0, "System", "State_change", "Yellow", "Red"),
        (25.0, "Block", "A", "PM", "PM_complete"),
        (25.0, "Block", "A", "PM_complete", "Good"),
        (25.0, "Node", "Join", "NodePM", "Degraded"),
        (25.0, "Node", "end", "NodePM", "Good"),
        (25.0, "System", "State_change", "Red", "Yellow"),
        (40.0, "Pool", "SP", "--", "Sparing"),
        (45.0, "Block", "A", "Good", "PM_is_due"),
        (45.0, "Block", "A", "PM_is_due", "PM"),
        (45.0, "Node", "Join", "Degraded", "NodePM"),
        (45.0, "Node", "end", "Good", "NodePM"),
        (45.0, "System", "State_change", "Yellow", "Red"),
        # B's repair finishes inside A's PM, so it completes into Idle.
        (48.0, "Block", "B", "Repairing", "Repaired"),
        (48.0, "Block", "B", "Repaired", "Idle"),
        (48.0, "Node", "Join", "NodePM", "Idle"),
        (48.0, "Node", "end", "NodePM", "Idle"),
        (48.0, "System", "Red_change", "--", "Red"),
        (50.0, "Block", "A", "PM", "PM_complete"),
        (50.0, "Block", "A", "PM_complete", "Good"),
        (50.0, "Block", "B", "Idle", "Good"),
        (50.0, "Node", "Join", "Idle", "Degraded"),
        (50.0, "Node", "end", "Idle", "Good"),
        (50.0, "Node", "Join", "Degraded", "Good"),
        (50.0, "System", "State_change", "Red", "Green"),
        (52.0, "Block", "B", "Good", "PM_is_due"),
        (52.0, "Block", "B", "PM_is_due", "PM"),
        (52.0, "Node", "Join", "Good", "Degraded"),
        (52.0, "System", "State_change", "Green", "Yellow"),
        (57.0, "Block", "B", "PM", "PM_complete"),
        (57.0, "Block", "B", "PM_complete", "Good"),
        (57.0, "Node", "Join", "Degraded", "Good"),
        (57.0, "System", "State_change", "Yellow", "Green"),
        (70.0, "Block", "A", "Good", "PM_is_due"),
        (70.0, "Block", "A", "PM_is_due", "PM"),
        (70.0, "Block", "B", "Good", "Idle"),
        (70.0, "Node", "Join", "Good", "Degraded"),
        (70.0, "Node", "Join", "Degraded", "Idle"),
        (70.0, "Node", "end", "Good", "Idle"),
        (70.0, "System", "State_change", "Green", "Red"),
        (75.0, "Block", "A", "PM", "PM_complete"),
        (75.0, "Block", "A", "PM_complete", "Good"),
        (75.0, "Block", "B", "Idle", "Good"),
        (75.0, "Node", "Join", "Idle", "Degraded"),
        (75.0, "Node", "end", "Idle", "Good"),
        (75.0, "Node", "Join", "Degraded", "Good"),
        (75.0, "System", "State_change", "Red", "Green"),
        (78.0, "Block", "B", "Good", "Failed"),
        (78.0, "Block", "B", "Failed", "Sparing"),
        (78.0, "Block", "B", "Sparing", "RepHold"),
        (78.0, "Node", "Join", "Good", "Degraded"),
        (78.0, "System", "State_change", "Green", "Yellow"),
        (80.0, "Pool", "SP", "Sparing", "Sparing"),
        (80.0, "Block", "B", "RepHold", "Sparing"),
        (80.0, "Block", "B", "Sparing", "Repairing"),
        (95.0, "Block", "A", "Good", "PM_is_due"),
        (95.0, "Block", "A", "PM_is_due", "PM"),
        (95.0, "Node", "Join", "Degraded", "NodePM"),
        (95.0, "Node", "end", "Good", "NodePM"),
        (95.0, "System", "State_change", "Yellow", "Red"),
        (100.0, "Block", "A", "PM", "PM_complete"),
        (100.0, "Block", "A", "PM_complete", "Good"),
        (100.0, "Node", "Join", "NodePM", "Degraded"),
        (100.0, "Node", "end", "NodePM", "Good"),
        (100.0, "System", "State_change", "Red", "Yellow"),
        (110.0, "Block", "B", "Repairing", "Repaired"),
        (110.0, "Block", "B", "Repaired", "Good"),
        (110.0, "Node", "Join", "Degraded", "Good"),
        (110.0, "System", "State_change", "Yellow", "Green"),
        (114.0, "Block", "B", "Good", "PM_is_due"),
        (114.0, "Block", "B", "PM_is_due", "pmReqDefer"),
        # The collision at 120: five rows for two blocks and a pool.
        (120.0, "Pool", "SP", "Sparing", "Sparing"),
        (120.0, "Block", "A", "Good", "PM_is_due"),
        (120.0, "Block", "B", "pmReqDefer", "Sparing"),
        (120.0, "Block", "A", "PM_is_due", "PM"),
        (120.0, "Block", "B", "Sparing", "Idle"),
        (120.0, "Node", "Join", "Good", "Degraded"),
        (120.0, "Node", "Join", "Degraded", "Idle"),
        (120.0, "Node", "end", "Good", "Idle"),
        (120.0, "System", "State_change", "Green", "Red"),
        (120.0, "Block", "B", "Idle", "PM"),
        (120.0, "Node", "Join", "Idle", "NodePM"),
        (120.0, "Node", "end", "Idle", "NodePM"),
        (125.0, "Block", "A", "PM", "PM_complete"),
        (125.0, "Block", "A", "PM_complete", "Good"),
        (125.0, "Node", "Join", "NodePM", "Degraded"),
        (125.0, "Node", "end", "NodePM", "Good"),
        (125.0, "Block", "B", "PM", "PM_complete"),
        (125.0, "Block", "B", "PM_complete", "Good"),
        (125.0, "Node", "Join", "Degraded", "Good"),
        (125.0, "System", "State_change", "Red", "Green"),
        (130.0, "Block", "A", "Good", "Done"),
        (130.0, "Block", "B", "Good", "Done"),
        (130.0, "System", "Phase_change", "--", "Mission_failure"),
        (130.0, "System", "Simulation_Terminate", "--", "--"),
    ])


def test_an_induced_failure_reaches_a_cold_standby_member_that_is_parked() -> None:
    """`DepFail = 100%` fails a standby member where `DepIdle = 100%` would
    only idle it: `Standby -> Failed`.

    Why: the two reactions are arms of the *same* dispatch, and they differ in
    what they do with a block that is not `Running`. With `DepIdle = 100%` the
    `Down` arm takes `ChangeBlockState(Idle, ...)`; with `DepFail = 100%` it
    takes the `"InducedFailure"` outcome, which is `SetInterruptReason` plus
    `Interrupt` and **no** state change of its own
 -- the woken block's own ZeroWait handler then
    fails it. A standby member is parked at that
    same combined Idle/ZeroWait site, which is why
    it can be failed there at all, and why the failure arrives on the
    `Standby` row.

    Setup: `Start -> A -> J1 -> C, D -> Cold -> End`. `A` never fails and PMs
    every 20 h for 8. `C` and `D` are the cold group's candidates (`ColdPri`
    10 and 20), so `D` is the standby member. `D` depends on `A` with
    `DepFail = 100%` and every other reaction at 0. 60 sim hours.

    Expected: `D Good -> Standby` at kickoff, then at each of A's PMs
    `D Standby -> Failed -> Repairing`, repaired ten hours later and demoted
    again (`Good -> Standby`, `Too_many_on`). The `Cold` node is never `Bad`
    -- `C` keeps the group's one slot throughout.

    Source: Raptor 7 (`BackFlowDependencies`' `Down` arm and
    its four reactions), Raptor 7 (the `InducedFailure` arm:
    reason plus interrupt, no `ChangeBlockState`),
    Raptor 7 (the ZeroWait site's `Induced_Failure` branch),
    Raptor 7 (the combined Idle/ZeroWait wait a standby member
    is parked at);
    docs/engine-design/node-status-and-cold-standby.adoc (Structure)
    """
    spec = _cold_second_stage(
        [periodic("A", 20.0, 8.0, fail=200.0),
         block("C", 200.0, 10.0),
         periodic("D", 200.0, 5.0, fail=200.0, dep_type="RBDBlock",
                  dep_num=1, dep_idle=0.0, dep_nothing=0.0, dep_pm=0.0,
                  dep_fail=100.0)], 60.0)
    result = harness.run(spec)
    # Rule B2, asserted rather than hoped for: every distribution in
    # this model is `Fixed` (code 19), whose draw is `param[0]` and
    # touches no stream, so the run must consume no randomness.
    assert result.draws == 0, (
        "rule B2: a `Fixed`-only model consumes no randomness; "
        f"this run drew {result.draws} times")
    assert_timeline(result, [
        (0.0, "Block", "A", "--", "Good"),
        (0.0, "Block", "C", "--", "Good"),
        (0.0, "Block", "D", "--", "Good"),
        (0.0, "Block", "D", "Good", "Standby"),
        (0.0, "Node", "Cold", "--", "Good"),
        (20.0, "Block", "A", "Good", "PM_is_due"),
        (20.0, "Block", "A", "PM_is_due", "PM"),
        (20.0, "Node", "J1", "--", "NodePM"),
        (20.0, "Node", "end", "--", "NodePM"),
        (20.0, "System", "State_change", "--", "Red"),
        # An induced failure, not an idle: the standby member is failed.
        (20.0, "Block", "D", "Standby", "Failed"),
        (20.0, "Block", "D", "Failed", "Repairing"),
        (20.0, "Node", "Cold", "Good", "Degraded"),
        (28.0, "Block", "A", "PM", "PM_complete"),
        (28.0, "Block", "A", "PM_complete", "Good"),
        (28.0, "Node", "J1", "NodePM", "Good"),
        (28.0, "Node", "end", "NodePM", "Good"),
        (28.0, "System", "State_change", "Red", "Yellow"),
        (30.0, "Block", "D", "Repairing", "Repaired"),
        (30.0, "Block", "D", "Repaired", "Good"),
        (30.0, "Block", "D", "Good", "Standby"),
        (30.0, "Node", "Cold", "Degraded", "Good"),
        (30.0, "System", "State_change", "Yellow", "Green"),
        (48.0, "Block", "A", "Good", "PM_is_due"),
        (48.0, "Block", "A", "PM_is_due", "PM"),
        (48.0, "Node", "J1", "Good", "NodePM"),
        (48.0, "Node", "end", "Good", "NodePM"),
        (48.0, "System", "State_change", "Green", "Red"),
        (48.0, "Block", "D", "Standby", "Failed"),
        (48.0, "Block", "D", "Failed", "Repairing"),
        (48.0, "Node", "Cold", "Good", "Degraded"),
        (56.0, "Block", "A", "PM", "PM_complete"),
        (56.0, "Block", "A", "PM_complete", "Good"),
        (56.0, "Node", "J1", "NodePM", "Good"),
        (56.0, "Node", "end", "NodePM", "Good"),
        (56.0, "System", "State_change", "Red", "Yellow"),
        (58.0, "Block", "D", "Repairing", "Repaired"),
        (58.0, "Block", "D", "Repaired", "Good"),
        (58.0, "Block", "D", "Good", "Standby"),
        (58.0, "Node", "Cold", "Degraded", "Good"),
        (58.0, "System", "State_change", "Yellow", "Green"),
        (60.0, "Block", "A", "Good", "Done"),
        (60.0, "Block", "C", "Good", "Done"),
        (60.0, "Block", "D", "Standby", "Done"),
        (60.0, "System", "Phase_change", "--", "Mission_failure"),
        (60.0, "System", "Simulation_Terminate", "--", "--"),
    ])


def test_a_mission_phase_defers_a_due_pm_and_the_deferral_is_the_rows_last_state_before_a_failure() -> None:
    """With `PMMisDefer` on inside a mission phase, the `PM_is_due` row is the
    block's last state before the next failure -- and the deferred PM logs a
    second `PM_is_due` when the phase releases it.

    Why: `DetermineNewState("PM is due")`'s pmMisDefer gate
   writes the `PM_is_due` row and then, in a
    mission, joins the deferred set (`deferPM:=TRUE`) and re-enters `Run`
    **without** running `ObtainPMprereqs` at all. So the block is still
    `Running` with a `PM_is_due` row as its last logged state, and the next
    thing that can happen to it is its ordinary failure. The
    `Start_Deferred_PM` release at the phase boundary re-enters
    `DetermineNewState("PM is due")` and writes the row a second time -- which
    for that block is `PM_is_due -> PM_is_due`, a pair that exists in the log
    only because the deferral logged nothing in between.

    Setup: two parallel blocks, both with a periodic PM every 20 h lasting 5
    and `PMMisDefer` on. `A`'s `Fixed` life is 25 h, so its PM is deferred at
    20 and its life expires 5 hours later. `B` never fails. Two phases: a
    `Fixed` 40 h **mission** phase then a `Fixed` 20 h non-mission phase.
    60 sim hours.

    Expected: both blocks log `PM_is_due` at 20 and neither runs a PM;
    `A PM_is_due -> Failed` at 25. At the t=40 boundary into the non-mission
    phase, B is released and logs `PM_is_due` again before its `PM`, so its
    own pair is `PM_is_due -> PM_is_due -> PM`. The boundary is recorded as
    `Mission_success`, because the system is `Green` at 40.

    Source: Raptor 7 (the pmMisDefer gate and its `Run`
    re-entry), Raptor 7 (the scheduled-PM arm that pays the
    hop and the `pmSyncTrigger` wait before it);
    docs/engine-design/state-machine.adoc
    """
    spec = _parallel_pair(
        [periodic("A", 20.0, 5.0, fail=25.0, pm_mis_defer=True),
         periodic("B", 20.0, 5.0, fail=200.0, pm_mis_defer=True)], 60.0,
        phases=[{"name": "P1", "mission": 1, "dist": FIXED, "params": [40.0]},
                {"name": "P2", "mission": 0, "dist": FIXED,
                 "params": [20.0]}])
    result = harness.run(spec)
    # Rule B2, asserted rather than hoped for: every distribution in
    # this model is `Fixed` (code 19), whose draw is `param[0]` and
    # touches no stream, so the run must consume no randomness.
    assert result.draws == 0, (
        "rule B2: a `Fixed`-only model consumes no randomness; "
        f"this run drew {result.draws} times")
    assert_timeline(result, [
        # A phased model opens a mission at kickoff.
        (0.0, "System", "State_change", "--", "Mission"),
        (0.0, "Block", "A", "--", "Good"),
        (0.0, "Block", "B", "--", "Good"),
        # Both PMs are due at 20 and both are deferred: the row, and nothing.
        (20.0, "Block", "A", "Good", "PM_is_due"),
        (20.0, "Block", "B", "Good", "PM_is_due"),
        (25.0, "Block", "A", "PM_is_due", "Failed"),
        (25.0, "Block", "A", "Failed", "Repairing"),
        (25.0, "Node", "Join", "--", "Degraded"),
        (25.0, "System", "State_change", "Mission", "Yellow"),
        (35.0, "Block", "A", "Repairing", "Repaired"),
        (35.0, "Block", "A", "Repaired", "Good"),
        (35.0, "Node", "Join", "Degraded", "Good"),
        (35.0, "System", "State_change", "Yellow", "Green"),
        # The phase boundary releases B's deferred PM, which logs the row again.
        (40.0, "System", "Phase_change", "--", "Phase_change"),
        (40.0, "System", "Phase_change", "Phase_change", "Mission_success"),
        (40.0, "System", "State_change", "Green", "Green"),
        (40.0, "Block", "B", "PM_is_due", "PM_is_due"),
        (40.0, "Block", "B", "PM_is_due", "PM"),
        (40.0, "Node", "Join", "Good", "Degraded"),
        (40.0, "System", "State_change", "Green", "Yellow"),
        (45.0, "Block", "B", "PM", "PM_complete"),
        (45.0, "Block", "B", "PM_complete", "Good"),
        (45.0, "Node", "Join", "Degraded", "Good"),
        (45.0, "System", "State_change", "Yellow", "Green"),
        # A's PM clock survived the repair and it runs in the non-mission phase.
        (50.0, "Block", "A", "Good", "PM_is_due"),
        (50.0, "Block", "A", "PM_is_due", "PM"),
        (50.0, "Node", "Join", "Good", "Degraded"),
        (50.0, "System", "State_change", "Green", "Yellow"),
        (55.0, "Block", "A", "PM", "PM_complete"),
        (55.0, "Block", "A", "PM_complete", "Good"),
        (55.0, "Node", "Join", "Degraded", "Good"),
        (55.0, "System", "State_change", "Yellow", "Green"),
        (60.0, "Block", "A", "Good", "Done"),
        (60.0, "Block", "B", "Good", "Done"),
        (60.0, "System", "Simulation_Terminate", "--", "--"),
    ])


def test_a_down_start_in_hands_on_repair_logs_repairing_twice_and_its_dependency_can_put_it_in_standby() -> None:
    """A block that starts the run mid-repair logs `Repairing` twice -- once
    from `Initialize`, once when the repair starts -- and the second one can
    be followed by `Standby`.

    Why: `SetToInitValues` picks the initial status from `GetStartCond`'s
    three-way branch -- `"repairing"` gives
    `Repairing` -- and the block's own first turn then runs the repair and
    takes the ordinary `ChangeBlockState(Repairing, ...)` transition.
   Two rows, same status, one at t=0. That is
    harmless for the state machine and is why the corpus contains
    `Repairing -> Repairing` at all. The second row is also where a
    dependence gets its chance: "Maintenance Completed" reads
    `depBlock.opStatus` and `Standby` there means
    `Standby` here, so a block repairing under a
    cold standby member finishes into `Standby` rather than `Running`.

    Setup: `Start -> A, B`, then `A -> J1 -> C, D`, `C, D -> Cold -> End`
    (`ColdPri` 10/20, so `D` is the standby member) and `B -> End`. `B` has
    `SimStart = 6` ("failed, at start of hands-on repair") and a `Fixed` 10 h
    repair, and depends on `D` with `DepIdle = 100%`. 40 sim hours.

    Expected: `B -- -> Repairing` at kickoff and `Repairing -> Repairing` in
    the same instant; `Repaired` at 10 and then `Repaired -> Standby`, because
    `D` has been in `Standby` since kickoff. The system is `Yellow` while `B`
    is down and `Green` from 10.

    Source: Raptor 7 (`SetToInitValues`' three-way startCond
    branch), Raptor 7 (`GetStartCond` type 6),
    Raptor 7 (the repair's own `Repairing` transition),
    Raptor 7 ("Maintenance Completed" following a
    dependency into `Standby`);
    docs/engine-design/block-lifecycle.adoc (Structure: the kickoff barrier)
    """
    spec = _cold_second_stage_four(
        [periodic("A", 200.0, 5.0, fail=200.0),
         block("B", 100.0, 10.0, sim_start=6, dep_type="RBDBlock",
               dep_num=4, dep_idle=100.0, dep_nothing=0.0, dep_pm=0.0,
               dep_fail=0.0),
         block("C", 200.0, 10.0),
         block("D", 200.0, 10.0)], 40.0)
    result = harness.run(spec)
    # Rule B2, asserted rather than hoped for: every distribution in
    # this model is `Fixed` (code 19), whose draw is `param[0]` and
    # touches no stream, so the run must consume no randomness.
    assert result.draws == 0, (
        "rule B2: a `Fixed`-only model consumes no randomness; "
        f"this run drew {result.draws} times")
    assert_timeline(result, [
        (0.0, "Block", "A", "--", "Good"),
        # `SimStart = 6`: Initialize puts B straight into `Repairing`.
        (0.0, "Block", "B", "--", "Repairing"),
        (0.0, "Node", "end", "--", "Degraded"),
        (0.0, "Block", "C", "--", "Good"),
        (0.0, "Block", "D", "--", "Good"),
        (0.0, "Block", "D", "Good", "Standby"),
        (0.0, "Node", "Cold", "--", "Good"),
        # ... and the repair's own transition logs the same status again.
        (0.0, "Block", "B", "Repairing", "Repairing"),
        (0.0, "System", "State_change", "--", "Yellow"),
        (10.0, "Block", "B", "Repairing", "Repaired"),
        # D is in Standby, so "Maintenance Completed" follows it there.
        (10.0, "Block", "B", "Repaired", "Standby"),
        (10.0, "Node", "end", "Degraded", "Good"),
        (10.0, "System", "State_change", "Yellow", "Green"),
        (40.0, "Block", "A", "Good", "Done"),
        (40.0, "Block", "B", "Standby", "Done"),
        (40.0, "Block", "C", "Good", "Done"),
        (40.0, "Block", "D", "Standby", "Done"),
        (40.0, "System", "Phase_change", "--", "Mission_success"),
        (40.0, "System", "Simulation_Terminate", "--", "--"),
    ])


def test_a_run_that_ends_while_the_block_waits_for_its_spare_closes_the_pre_ldt_row_directly_into_done() -> None:
    """When the run ends during the spare wait that follows a pre-LDT, the
    closing row is `End_Pre-LDT -> Done` -- there is no `RepHold` row in
    between.

    Why: the end-of-run sweep puts every block to `Done "End_sim"`
   in whatever status it is in, and it writes a
    row, so the pair the log shows is the block's *last logged row* and `Done`
    -- not the status and `Done`. The pre-LDT row is the last thing logged
    here because the block never moves out of `RepHold`: `Repair`'s spare step
    only writes `Waiting_for_Spare` when `opStatus <> RepHold`,
   and the LDT wait already put it there. The
    run-end path is different in exactly one respect -- `IF (SimTime+ldtWait >=
    TermCondition)` inside the LDT itself routes straight to `Done` **without**
    logging `End_Pre-LDT`, so the row only appears when the delay ends
    strictly before the run does.

    Setup: one block `A`, `Fixed` 50-hour life, `Fixed` 10-hour repair,
    pre-LDT `Fixed(5.0)`, drawing pooled spares from `SP`, which starts
    **empty** and is restocked at 200 h. 70 sim hours.

    Expected: `A` fails at 50, logs `Sparing No_spare_in_stock-SP` and holds
    in `RepHold`, ends its pre-LDT at 55, and then waits on the pool until the
    run ends at 70 -- so the closing row is `End_Pre-LDT -> Done` and there is
    no second `RepHold` row.

    Source: Raptor 7 (the end-of-run element sweep),
    Raptor 7 (`Repair`'s pool wait and its
    `opStatus <> RepHold` guard on the `Waiting_for_Spare` row);
    docs/engine-design/pools-spares-resources.adoc (Structure: `Give` is
    two-stage)
    """
    spec = _one_block([block("A", 50.0, 10.0, pre_dist=FIXED, pre_params=[5.0],
                             sp_type="Pooled", spare_pool="SP")], 70.0,
                      spare_pools=[{"name": "SP", "stock": 0, "new_spares": 1,
                                    "arrive_every": 200.0, "rso": 1}])
    result = harness.run(spec)
    # Rule B2, asserted rather than hoped for: every distribution in
    # this model is `Fixed` (code 19), whose draw is `param[0]` and
    # touches no stream, so the run must consume no randomness.
    assert result.draws == 0, (
        "rule B2: a `Fixed`-only model consumes no randomness; "
        f"this run drew {result.draws} times")
    assert_timeline(result, [
        (0.0, "Block", "A", "--", "Good"),
        (50.0, "Block", "A", "Good", "Failed"),
        (50.0, "Block", "A", "Failed", "Sparing"),
        (50.0, "Block", "A", "Sparing", "RepHold"),
        (50.0, "Node", "end", "--", "Bad"),
        (50.0, "System", "State_change", "--", "Red"),
        (55.0, "Block", "A", "RepHold", "End_Pre-LDT"),
        # No further `RepHold` row: the block was already there and the sweep
        # closes whatever its last logged row was.
        (70.0, "Block", "A", "End_Pre-LDT", "Done"),
        (70.0, "System", "Phase_change", "--", "Mission_failure"),
        (70.0, "System", "Simulation_Terminate", "--", "--"),
    ])
