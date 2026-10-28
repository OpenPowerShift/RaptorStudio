"""**Stock-level ordering, and what a post-LDT start does to a spare pool.**

Two mechanisms in `raptor/core/engine/block_lifecycle.py` that no in-memory
model in this suite had been built to provoke, and that until now only the
engine tier's whole-output comparisons against legacy captures executed:

1. **A spare pool's stock-level ordering (SLO) re-order**, at each of the four
   places the port consumes a spare from a `SPARE_POOL_DATA` pool. A pool
   configured with a service-level objective tops itself back up when a grant
   leaves its stock at the configured order level, and tops it up by exactly
   one when the grant leaves it at or below `level - quantity`. The source
   had three near-identical call sites, one per repair-flow variant
   (see the `order_stock_process` docstring in `pool.py`); the port carries
   **four** on the pooled path alone, because it splits `Repair`'s early grab
   -- which *decides* the re-order -- from the later spare step that *applies*
   it. Which of the four fires is decided by where the spare was obtained,
   and that is the claim each test here makes.

2. **A block that starts the run in post-LDT and draws from a spare pool**
   (`BlockProcess._post_ldt_start`). `Repair` opens with an opportunistic
   grab of the pool's spare for *every* start condition, so a post-LDT start
   consumes a spare at t=0 -- before it has waited out any of the delay it is
   nominally in the middle of. Two shapes of that grab are **refused** rather
   than guessed, and both refusals are pinned here as claims, because a
   refusal nobody reads is a refusal nobody maintains.

`docs/engine-design/pools-spares-resources.adoc` is the prose for the
mechanism; `raptor/core/fileio/rbdlib.py`'s `make_spare_pool_row` gives the
friendly spec names (`stock`, `new_spares`, `arrive_every`, `slo`, `level`,
`quantity`, `slo_time`) this file's fixtures are written in.

**One reading of the site that turned out to be wrong, recorded because the
plan's prose is part of the record.** The second mechanism was described to me
as "pool exhausted into a repair: a block whose pool has run dry cannot get a
spare, so it enters repair anyway and the repair competes for the same pool",
with "`Failed -> Repairing` with no intervening spare request" as the
distinguishing observable. That is not what the code does. The site is
`_post_ldt_start`, and its empty-pool arm is a **refusal**
(`block_lifecycle.py:1424-1427`), not a repair:

    if pool.resources <= 0:
        raise NotImplementedError(f"block {block.name!r} starts the run in post-LDT with an empty spare pool "
            "-- what Repair does then (Raptor 7's ELSE arm) is not captured")

What *is* reachable, and what these tests pin, is the stocked-pool grab and the
two refusals. The "`Failed -> Repairing` with no spare request" observable
exists, but it belongs to the ordinary failure path's early grab
(`_enter_repair`), not to a post-LDT start -- and
`test_a_repair_that_finds_stock_at_its_own_repair_seam_takes_it_immediately`
is the test for it, because that grab is the same code the post-LDT start runs.

**Why every fixture is `Fixed` (rule B2).** All three distributions the
mechanism touches are `Fixed`: the failure life, the repair time and the PM
duration. The post-LDT fixture's start condition is `sim_start=5`
("failed, N% through the repair cycle"), which is the *deterministic* start
type -- it draws pre-LDT, repair and post-LDT from the block's own
distributions and reads `amount_exhausted` as a percentage, with no `Uniform`
anywhere (`get_start_cond`, `block_lifecycle.py:388-409`). Every test asserts
`result.draws == 0` rather than hoping for it.

**The three fixture traps, paid once so a reader does not have to.**

* **`sp_type` must be `"Pooled"`.** `typed._SPARING_TYPE_BY_NAME` maps
  Raptor's own vocabulary, and the enum member name `"SparePool"` is not one
  of them, so writing it yields a block with no sparing at all
  (`pools-spares-resources.adoc`, "Port-only devices").
* **A `"Pooled"` block needs its pool named** (`spare_pool="SP"`), and the
  three replenishment switches are not interchangeable. `CheckForSpare` asks
  only whether *anything* is configured (`rso or slo or eso`) before deciding
  a pool can never refill, while what actually refills it is RSO's periodic
  arrival or an emergency or stock order. So `rso=1` with `arrive_every=500`
  inside a 30-hour run is a pool that is *configured* and permanently dry: a
  block that finds it empty neither goes `Done No_spares` nor orders
  anything, it parks in `RepHold`. The per-test docstrings say which switches
  each fixture relies on.
* **`amount_exhausted=80.0` with `sim_start=5` is the post-LTD leg.** The
  three-way cut is on the *cycle*: pre-LDT 0 h, repair 10 h, post-LDT 20 h,
  cycle 30 h, so 80% of the way through is 24 h in -- past the repair's 10 h
  and 6 h into the post-LTD leg. The remaining 6 h is what
  `End_Partial_pos` reports. This is arithmetic, not a transcript: the literal
  6.0 in the timeline is `0 + 10 + 20 - 0.8 * 30`.
"""
from __future__ import annotations

from typing import Any

import pytest

from .harness import FIXED, Run, assert_timeline, events, run

# --------------------------------------------------------------------------
# Fixtures
#
# One block, `Start -> A -> End`, and one `SP` spare pool. Every helper below
# returns a `build_from_spec` spec dict; none of them runs anything, so a
# fixture cannot assert by accident.
# --------------------------------------------------------------------------


def _pool(*, stock: int = 0, new_spares: int = 1, arrive_every: float = 20.0,
          rso: int = 1, slo: int = 0, level: int = 0, quantity: int = 1,
          slo_time: float = 5.0, eso: int = 0,
          emer_time: float = 6.0) -> dict[str, Any]:
    """One `SP` spare pool.

    Defaults are the "nothing configured" pool: no SLO, no ESO, routine
    restocking on. `arrive_every` is short everywhere so the stock actually
    runs down inside the run rather than being frozen for its whole length.
    """
    return {"name": "SP", "stock": stock, "new_spares": new_spares,
            "arrive_every": arrive_every, "rso": rso, "slo": slo,
            "level": level, "quantity": quantity, "slo_time": slo_time,
            "eso": eso, "emer_time": emer_time}


def _chain(block: dict[str, Any], pool: dict[str, Any],
           run_length: float) -> dict[str, Any]:
    """`Start -> A -> End` in series -- the least topology the loader accepts,
    with `A` pooled against `pool` and a `run_length`-hour run."""
    return {
        "system": {"run_length": run_length, "num_runs": 1,
                   "termination": "TimeSim"},
        "blocks": [block],
        "nodes": [{"name": "Start", "type": 1, "n": 0, "out": 1},
                  {"name": "End", "type": 3, "n": 1, "out": 0}],
        "links": [{"from": "Start", "to": "A"}, {"from": "A", "to": "End"}],
        "spare_pools": [pool],
    }


def _pooled(name: str, fail: float, repair: float) -> dict[str, Any]:
    """A `Pooled` block with `Fixed` lives, drawing its spares from `SP`."""
    return {"name": name, "fail_dist": FIXED, "fail_params": [fail],
            "repair_dist": FIXED, "repair_params": [repair],
            "sp_type": "Pooled", "spare_pool": "SP"}


def _periodic_pm(name: str = "A") -> dict[str, Any]:
    """A pooled block with a periodic PM every 10 h lasting 2 h.

    The life is `Fixed(1000)` against a sub-30-hour run, so the block never
    fails: the *only* thing that draws on the pool is its PM, which is what
    makes the pool's stock a clean readout of how many spares the PM took.
    """
    return {**_pooled(name, 1000.0, 10.0), "uses_pm": True, "pm_freq": 10.0,
            "pm_dist": FIXED, "pm_params": [2.0]}


def _post_ldt_start(post_delay: float = 20.0) -> dict[str, Any]:
    """A pooled block that starts the run in the post-LDT leg.

    `sim_start=5` is "failed, N% through the repair cycle" and
    `amount_exhausted=80.0` puts the start 80% of the way through a
    0 + 10 + 20 h cycle, i.e. 6 h into a 20 h post-LDT leg.
    """
    return {**_pooled("A", 1000.0, 10.0),
            "pre_ldt_dist": FIXED, "pre_ldt_params": [0.0, 0.0, 0.0],
            "post_ldt_dist": FIXED, "post_ldt_params": [post_delay, 0.0, 0.0],
            "sim_start": 5, "amount_exhausted": 80.0}


def _pool_comments(result: Run) -> list[str]:
    """Every `Pool` row's comment column, in event-log order.

    The pool's state column is `Sparing` on every one of its rows, so the
    *stock* is the only thing that distinguishes an order of two from an
    order of one, and it lives in the comment. Read back out of the log the
    engine wrote, not recomputed.
    """
    return [line.comment for line in result.log if str(line.type) == "Pool"]


# --------------------------------------------------------------------------
# Mechanism 1: the four SLO re-order sites
# --------------------------------------------------------------------------


def test_a_deferred_pm_pickup_reorders_the_whole_quantity_on_its_order_level():
    """A PM that was deferred for want of a spare re-orders the pool's whole
    `slo_new_spares` quantity when the spare that lands brings the stock to
    exactly the order level.

    Why: a PM whose spare request could not be met abandons the attempt
    (`pmReqDefer`) and leaves the request queued on the pool; when the
    background restock arrives it grants that queued request, and the block
    picks the spare up asynchronously. The pickup replays the tail source
    runs when its PM spare wait returns -- count the unit, mark it obtained,
    re-order, then log `Spare_Arrived` (ported at
    `block_lifecycle.py:734-764`). This is the *first* of the two arms: the
    grant left the stock at `level`, so the whole configured quantity is
    ordered. Raptor 7 is the same two-armed check the
    synchronous PM path runs, which is what makes the quantity and not the
    count of spares consumed the thing being decided.

    Setup: one pooled block, PM every 10 h, `Fixed` 2 h PM, and a pool that
    starts **empty** with routine restocking of 1 every 20 h. The t=10 PM
    finds the pool empty and defers; the t=20 restock grants the queued
    request, leaving the stock at 0 -- configured as `level=0`,
    `slo_new_spares=2`, `slo_time=5`.

    Expected: `PM_is_due -> pmReqDefer` at t=10; at t=20 the pool's arrival,
    the block's `Spare_Arrived`, then `2_Spare(s)_ordered_(stock=0)`, and
    `2_Spare(s)_arrived_(stock=2)` five hours later. Zero draws.

    Source: Raptor 7 (`ObtainPMprereqs`' SparePool tail),
    Raptor 7 (the SLO re-order), block_lifecycle.py:734-764
    (`_credit_deferred_pm_spare`), pool.py:411 (`order_stock_process`);
    docs/engine-design/pools-spares-resources.adoc (Purpose, Source
    anchors)
    """
    result = run(_chain(_periodic_pm(),
                        _pool(stock=0, new_spares=1, arrive_every=20.0, rso=1,
                              slo=1, level=0, quantity=2, slo_time=5.0),
                        28.0))

    assert_timeline(result, [
        (0.0, "Block", "A", "--", "Good"),
        # The t=10 PM finds the pool empty and defers rather than holding.
        (10.0, "Block", "A", "Good", "PM_is_due"),
        (10.0, "Block", "A", "PM_is_due", "pmReqDefer"),
        # The t=20 restock grants the queued request in the same instant:
        # the pool's arrival row precedes the block's grant row, and the
        # SLO order follows both, because the order is *scheduled* rather
        # than run inline.
        (20.0, "Pool", "SP", "--", "Sparing"),
        (20.0, "Block", "A", "pmReqDefer", "Sparing"),
        (20.0, "Pool", "SP", "Sparing", "Sparing"),
        (20.0, "Block", "A", "Sparing", "PM"),
        (20.0, "Node", "end", "--", "NodePM"),
        (20.0, "System", "State_change", "--", "Red"),
        (22.0, "Block", "A", "PM", "PM_complete"),
        (22.0, "Block", "A", "PM_complete", "Good"),
        (22.0, "Node", "end", "NodePM", "Good"),
        (22.0, "System", "State_change", "Red", "Green"),
        (25.0, "Pool", "SP", "Sparing", "Sparing"),
        (28.0, "Block", "A", "Good", "Done"),
        (28.0, "System", "Phase_change", "--", "Mission_failure"),
        (28.0, "System", "Simulation_Terminate", "--", "--"),
    ])
    # The claim is the stock: the grant left the pool empty, which is the
    # configured order level, so the order is for the configured quantity.
    assert _pool_comments(result) == [
        "1_New_spare(s)_arrived_(stock=0)",
        "2_Spare(s)_ordered_(stock=0)",
        "2_Spare(s)_arrived_(stock=2)",
    ]
    assert result.draws == 0


def test_a_deferred_pm_pickup_short_of_the_order_level_reorders_exactly_one():
    """The same deferred-PM pickup, landing *below* `level - quantity`,
    re-orders exactly one spare rather than the configured quantity.

    Why: the second arm of the same two-armed check.
   A pool whose order level is 2 and whose
    `slo_new_spares` is 1 has nothing to top up when a grant leaves it at 1
    -- that is one below `level`, and the rule only orders a single unit once
    the stock is at or below `level - quantity`. The two arms are what make
    the quantity a *configuration* rather than a constant, and this is the
    only fixture in the file where the order is for one.

    Setup: as the previous test, except the restock brings **2** spares and
    the pool is configured `level=2`, `slo_new_spares=1`. The grant takes one
    of the two, leaving the stock at 1: below `level`, and at
    `level - quantity` exactly.

    Expected: the same shape as the `==`-level case -- defer at t=10, grant
    and order at t=20 -- with `1_Spare(s)_ordered_(stock=1)` in place of the
    order of two. Zero draws.

    Source: Raptor 7, block_lifecycle.py:756-760
    (`_credit_deferred_pm_spare`'s `elif`), pool.py:411
    (`order_stock_process`); raptor/core/fileio/rbdlib.py:254-260 (`make_spare_pool_row`'s
    `level`/`quan` columns)
    """
    result = run(_chain(_periodic_pm(),
                        _pool(stock=0, new_spares=2, arrive_every=20.0, rso=1,
                              slo=1, level=2, quantity=1, slo_time=5.0),
                        28.0))

    assert_timeline(result, [
        (0.0, "Block", "A", "--", "Good"),
        (10.0, "Block", "A", "Good", "PM_is_due"),
        (10.0, "Block", "A", "PM_is_due", "pmReqDefer"),
        (20.0, "Pool", "SP", "--", "Sparing"),
        (20.0, "Block", "A", "pmReqDefer", "Sparing"),
        (20.0, "Pool", "SP", "Sparing", "Sparing"),
        (20.0, "Block", "A", "Sparing", "PM"),
        (20.0, "Node", "end", "--", "NodePM"),
        (20.0, "System", "State_change", "--", "Red"),
        (22.0, "Block", "A", "PM", "PM_complete"),
        (22.0, "Block", "A", "PM_complete", "Good"),
        (22.0, "Node", "end", "NodePM", "Good"),
        (22.0, "System", "State_change", "Red", "Green"),
        (25.0, "Pool", "SP", "Sparing", "Sparing"),
        (28.0, "Block", "A", "Good", "Done"),
        (28.0, "System", "Phase_change", "--", "Mission_failure"),
        (28.0, "System", "Simulation_Terminate", "--", "--"),
    ])
    # The claim is the stock: one spare short of the level of two, so the
    # order is for the single unit and not for `slo_new_spares`.
    assert _pool_comments(result) == [
        "2_New_spare(s)_arrived_(stock=1)",
        "1_Spare(s)_ordered_(stock=1)",
        "1_Spare(s)_arrived_(stock=2)",
    ]
    assert result.draws == 0


def test_a_pm_spare_grab_reorders_the_pool_the_instant_the_spare_is_taken():
    """A PM that finds a spare waiting re-orders the pool in the same instant
    it takes it, before the PM itself starts.

    Why: the *synchronous* arm of the same check. When the pool has stock
    there is no wait and no deferred pickup, so the re-order happens inside
    the PM's own prerequisite acquisition -- which is why the pool's
    `2_Spare(s)_ordered` row lands at t=10, the same instant as `PM_is_due`
    and *before* `beginning_PM`, and why the block never logs
    `Spare_Arrived` at all (there was nothing to wait for). This is
    Raptor 7 reached the ordinary way, and it is a different
    copy of the code from the deferred pickup above: one of the three source
    sites the pool docstring counts.

    Setup: one pooled block, PM every 10 h, and a pool holding **2** spares
    with no restocking at all (`rso=0`, `arrive_every=500` so no RSO arrival
    lands inside the run). The t=10 PM takes one immediately, leaving 1,
    configured as `level=1`, `slo_new_spares=2`.

    Expected: `PM_is_due` at t=10, then the pool's
    `2_Spare(s)_ordered_(stock=1)` in the same instant, then `PM`; the
    ordered pair arrives at t=15 (`slo_time=5`) and the stock is 3. Zero
    draws.

    Source: Raptor 7 (`ObtainPMprereqs`, SparePool branch),
    block_lifecycle.py:2505-2511 (`_obtain_pm_pool_spare`'s SLO re-order),
    Raptor 7 (`PoolObj.OrderStock`);
    docs/engine-design/pools-spares-resources.adoc (Structure)
    """
    result = run(_chain(_periodic_pm(),
                        _pool(stock=2, new_spares=1, arrive_every=500.0, rso=0,
                              slo=1, level=1, quantity=2, slo_time=5.0),
                        18.0))

    assert_timeline(result, [
        (0.0, "Block", "A", "--", "Good"),
        (10.0, "Block", "A", "Good", "PM_is_due"),
        # Same instant, and BEFORE the block's own `PM` row: the order is
        # placed inside the prerequisite acquisition, not after the PM.
        (10.0, "Pool", "SP", "--", "Sparing"),
        (10.0, "Block", "A", "PM_is_due", "PM"),
        (10.0, "Node", "end", "--", "NodePM"),
        (10.0, "System", "State_change", "--", "Red"),
        (12.0, "Block", "A", "PM", "PM_complete"),
        (12.0, "Block", "A", "PM_complete", "Good"),
        (12.0, "Node", "end", "NodePM", "Good"),
        (12.0, "System", "State_change", "Red", "Green"),
        (15.0, "Pool", "SP", "Sparing", "Sparing"),
        (18.0, "Block", "A", "Good", "Done"),
        (18.0, "System", "Phase_change", "--", "Mission_failure"),
        (18.0, "System", "Simulation_Terminate", "--", "--"),
    ])
    # The claim is the stock: the grab took the pool from 2 to 1, which is
    # the configured order level, so the order is for two.
    assert _pool_comments(result) == [
        "2_Spare(s)_ordered_(stock=1)",
        "2_Spare(s)_arrived_(stock=3)",
    ]
    assert result.draws == 0


def test_a_repair_that_finds_stock_at_its_own_repair_seam_takes_it_immediately():
    """A repair that finds stock in the pool when `Repair` opens takes it
    there and then, skipping the spare wait entirely, and the SLO re-order it
    decided is applied at the *second* of the two call sites.

    Why: `Repair` is a `TELL METHOD`, so it runs synchronously up to its
    first `WAIT` and tries to grab the spare immediately, before Pre-LDT.
   The re-order that grab decides is *recorded*
    on the spare request and applied by the later spare step,
   which is why the pool's order row lands
    **after** the block's own `Repairing` row here even though the decision
    was taken at t=10. This is the fourth and last of the port's SLO copies,
    and the only one whose order row is not in the same instant as the grab.

    Setup: one pooled block, `Fixed` 10 h life, `Fixed` 4 h repair, no PM.
    The pool holds 2 spares with no restocking, configured `level=1`,
    `slo_new_spares=2`. The t=10 failure finds stock, so the repair takes
    one and leaves 1 -- the order level.

    Expected: `Failed -> Repairing` at t=10 with **no** `Sparing` or
    `RepHold` row (the whole point: the pool had stock, so nothing is logged
    and nothing waits), then the pool's `2_Spare(s)_ordered_(stock=1)` also
    at t=10 but after the block's rows, and `Repaired` at t=14. Zero draws.

    Source: Raptor 7 (`Repair`'s early SparePool grab),
    Raptor 7 (the second call site applying the first's
    decision), block_lifecycle.py:3713-3735 (`_enter_repair`)
    (`_wait_for_pool_spare`'s `obtained_early` branch);
    docs/engine-design/pools-spares-resources.adoc (Purpose)
    """
    result = run(_chain(_pooled("A", 10.0, 4.0),
                        _pool(stock=2, new_spares=1, arrive_every=500.0, rso=0,
                              slo=1, level=1, quantity=2, slo_time=5.0),
                        20.0))

    assert_timeline(result, [
        (0.0, "Block", "A", "--", "Good"),
        (10.0, "Block", "A", "Good", "Failed"),
        (10.0, "Block", "A", "Failed", "Repairing"),
        (10.0, "Node", "end", "--", "Bad"),
        (10.0, "System", "State_change", "--", "Red"),
        # The block's rows come first: the re-order was *decided* by the grab
        # but *applied* by the next step, which is a later selection.
        (10.0, "Pool", "SP", "--", "Sparing"),
        (14.0, "Block", "A", "Repairing", "Repaired"),
        (14.0, "Block", "A", "Repaired", "Good"),
        (14.0, "Node", "end", "Bad", "Good"),
        (14.0, "System", "State_change", "Red", "Green"),
        (15.0, "Pool", "SP", "Sparing", "Sparing"),
        (20.0, "Block", "A", "Good", "Done"),
        (20.0, "System", "Phase_change", "--", "Mission_failure"),
        (20.0, "System", "Simulation_Terminate", "--", "--"),
    ])
    # The claim is the stock: 2 taken down to 1, the order level.
    assert _pool_comments(result) == [
        "2_Spare(s)_ordered_(stock=1)",
        "2_Spare(s)_arrived_(stock=3)",
    ]
    # And the shape of the *absence*: no `Sparing` row, so no spare was
    # logged as missing and no `RepHold Waiting_for_Spare` was entered.
    assert [str(e) for e in events(result, "Block:A",
                                   between=(9.0, 13.0))] == [
        "10 Block:A Good -> Failed",
        "10 Block:A Failed -> Repairing",
    ]
    assert result.draws == 0


def test_a_repair_parked_for_its_spare_reorders_the_pool_once_it_is_granted():
    """A repair that parked in `RepHold Waiting_for_Spare` re-orders the pool
    *after* the `Spare_Arrived` row, once the spare that unblocked it lands.

    Why: the real order here is `Spare_Arrived` first and
    `...Spare(s)_ordered...` second, even though source puts the SLO
    re-order *before* the `Spare_Arrived` line.
   A `TELL PooledSpares[...] TO OrderStock(...)`
    targets a pool object and does not run its body synchronously up to
    `OrderStock`'s first `WriteEvents` the way block and link TELLs do
    (`block_lifecycle.py:3433-3439`), so the order row is necessarily after.
    This is the third source site of the three.

    Setup: one pooled block, `Fixed` 10 h life, `Fixed` 4 h repair. The pool
    starts **empty** and restocks 1 every 12 h, so the t=10 failure finds
    nothing, parks, and is granted the t=12 arrival, which leaves the stock
    at 0 -- configured `level=0`, `slo_new_spares=2`, `slo_time=5`.

    Expected: at t=10 `Failed -> Sparing` (`No_spare_in_stock-SP`) and
    `Sparing -> RepHold` (`Waiting_for_Spare`); at t=12 the pool's arrival,
    the block's `Spare_Arrived`, `Repairing`, and *then*
    `2_Spare(s)_ordered_(stock=0)`, with the pair arriving at t=17. Zero
    draws.

    Source: Raptor 7 (`Repair`'s SparePool branch),
    Raptor 7 (the SLO re-order's position),
    block_lifecycle.py:3440-3449 (`_wait_for_pool_spare`),
    Raptor 7 (`GenerateSpares`: the arrival line is written
    before the grants);
    docs/engine-design/pools-spares-resources.adoc (Structure)
    """
    result = run(_chain(_pooled("A", 10.0, 4.0),
                        _pool(stock=0, new_spares=1, arrive_every=12.0, rso=1,
                              slo=1, level=0, quantity=2, slo_time=5.0),
                        22.0))

    assert_timeline(result, [
        (0.0, "Block", "A", "--", "Good"),
        (10.0, "Block", "A", "Good", "Failed"),
        # The empty-pool path: logged, then parked.
        (10.0, "Block", "A", "Failed", "Sparing"),
        (10.0, "Block", "A", "Sparing", "RepHold"),
        (10.0, "Node", "end", "--", "Bad"),
        (10.0, "System", "State_change", "--", "Red"),
        (12.0, "Pool", "SP", "--", "Sparing"),
        (12.0, "Block", "A", "RepHold", "Sparing"),
        (12.0, "Block", "A", "Sparing", "Repairing"),
        # AFTER the block's `Spare_Arrived` and `Repairing`, not before.
        (12.0, "Pool", "SP", "Sparing", "Sparing"),
        (12.0, "System", "Red_change", "--", "Red"),
        (16.0, "Block", "A", "Repairing", "Repaired"),
        (16.0, "Block", "A", "Repaired", "Good"),
        (16.0, "Node", "end", "Bad", "Good"),
        (16.0, "System", "State_change", "Red", "Green"),
        (17.0, "Pool", "SP", "Sparing", "Sparing"),
        (22.0, "Block", "A", "Good", "Done"),
        (22.0, "System", "Phase_change", "--", "Mission_failure"),
        (22.0, "System", "Simulation_Terminate", "--", "--"),
    ])
    # The claim is the stock: the grant emptied the pool, which is the
    # configured order level, so the order is for the configured quantity.
    assert _pool_comments(result) == [
        "1_New_spare(s)_arrived_(stock=0)",
        "2_Spare(s)_ordered_(stock=0)",
        "2_Spare(s)_arrived_(stock=2)",
    ]
    assert result.draws == 0


def test_a_block_whose_pool_can_never_refill_goes_done_no_spares():
    """A block whose pool has run dry and can never be refilled goes `Done`
    `No_spares` at the instant it fails -- it does **not** enter a repair.

    Why: this is the honest answer to "what does a block whose pool has run
    dry do?", and the answer is not a repair. `CheckForSpare`'s SparePool arm
    asks whether anything is configured that could still deliver -- routine,
    emergency or stock-level ordering -- and when the answer is no, it sets
    `sparesDone` and the block's life ends. With
    RSO and SLO off and the pool empty, the failure at t=10 finds a pool that
    can never refill, so the block is finished: `Sparing No_spare_in_stock-SP`
    is logged and the very next row is `Done No_spares`. A repair here would
    be the engine waiting out the rest of the run for a spare that is never
    coming, and the system is told so (`Permanently_Down Out_of_spares`).

    Setup: one pooled block, `Fixed` 10 h life, `Fixed` 4 h repair, and a
    pool with **no** replenishment at all: `rso=0`, `slo=0`, `eso=0`, stock
    0. A 20-hour run, so the block's `End_sim` is also pinned.

    Expected: `Failed -> Sparing` then `Sparing -> Done` at t=10 with no
    `Repairing` row at all, `Permanently_Down Out_of_spares` in the same
    instant, and the block `Done -> Done` (`End_sim`) at t=20. Zero draws.

    Source: Raptor 7 (`CheckForSpare`, SparePool arm),
    Raptor 7 (the "nothing will ever come" arm),
    block_lifecycle.py:3219-3240 (`_check_pool_stock`);
    docs/engine-design/pools-spares-resources.adoc (Purpose)
    """
    result = run(_chain(_pooled("A", 10.0, 4.0),
                        _pool(stock=0, rso=0, slo=0, eso=0),
                        20.0))

    assert_timeline(result, [
        (0.0, "Block", "A", "--", "Good"),
        (10.0, "Block", "A", "Good", "Failed"),
        (10.0, "Block", "A", "Failed", "Sparing"),
        # The whole claim: `Done`, not `Repairing`, and not `RepHold`.
        (10.0, "Block", "A", "Sparing", "Done"),
        (10.0, "Node", "end", "--", "Bad"),
        (10.0, "System", "State_change", "--", "Red"),
        (10.0, "System", "Permanently_Down", "--", "Out_of_spares"),
        (20.0, "Block", "A", "Done", "Done"),
        (20.0, "System", "Phase_change", "--", "Mission_failure"),
        (20.0, "System", "Simulation_Terminate", "--", "--"),
    ])
    # And the pool never says anything, because nothing was ever ordered
    # into it: the list is empty rather than merely short.
    assert _pool_comments(result) == []
    assert result.draws == 0


def test_an_exhausted_pool_with_emergency_ordering_orders_one_spare():
    """A block that finds its pool empty with emergency ordering configured
    gets an emergency spare ordered in the same instant, and parks until it
    arrives.

    Why: `CheckForSpare`'s SparePool arm always orders when the pool is
    empty and ESO is on -- there is no grace
    period, and the order row is written *before* the block's
    `Waiting_for_Spare` row because the order is a real `TELL` that starts as
    its own selection while the block is still in its own turn. The order is
    for exactly one spare whatever the pool's configured quantity, and it
    lands after `emerTime` hours.

    Setup: the previous test's block, with `eso=1` and `emer_time=6.0` on the
    pool instead of the dead configuration. The pool is still empty and still
    has no RSO and no SLO, so this fixture differs from the previous one in
    one switch.

    Expected: at t=10 `Failed -> Sparing`, the pool's
    `Spare_ordered_(stock=0)`, then `Sparing -> RepHold`
    (`Waiting_for_Spare`); at t=16 the pool's
    `Ordered_spare_arrives_(stock=0)` and the block's `Spare_Arrived` and
    `Repairing`. Zero draws.

    Source: Raptor 7 (`CheckForSpare`, SparePool arm),
    Raptor 7 (the emergency order), Raptor 7
    (`PoolObj.OrderASpare`) (the arrival),
    block_lifecycle.py:3224-3232 (`_check_pool_stock`), pool.py:379
    (`order_a_spare_process`);
    docs/engine-design/pools-spares-resources.adoc (Source anchors)
    """
    result = run(_chain(_pooled("A", 10.0, 4.0),
                        _pool(stock=0, rso=0, slo=0, eso=1, emer_time=6.0),
                        20.0))

    assert_timeline(result, [
        (0.0, "Block", "A", "--", "Good"),
        (10.0, "Block", "A", "Good", "Failed"),
        (10.0, "Block", "A", "Failed", "Sparing"),
        # The order is logged before the park: both are the same instant.
        (10.0, "Pool", "SP", "--", "Sparing"),
        (10.0, "Block", "A", "Sparing", "RepHold"),
        (10.0, "Node", "end", "--", "Bad"),
        (10.0, "System", "State_change", "--", "Red"),
        (16.0, "Pool", "SP", "Sparing", "Sparing"),
        (16.0, "Block", "A", "RepHold", "Sparing"),
        (16.0, "Block", "A", "Sparing", "Repairing"),
        (16.0, "System", "Red_change", "--", "Red"),
        (20.0, "Block", "A", "Repairing", "Repaired"),
        (20.0, "Block", "A", "Repaired", "Good"),
        (20.0, "Node", "end", "Bad", "Good"),
        (20.0, "Block", "A", "Good", "Done"),
        (20.0, "System", "State_change", "Red", "Green"),
        (20.0, "System", "Phase_change", "--", "Mission_failure"),
        (20.0, "System", "Simulation_Terminate", "--", "--"),
    ])
    # The claim is the order itself: one spare, against a pool with no stock
    # and no configured quantity, and it arrives six hours later.
    assert _pool_comments(result) == [
        "Spare_ordered_(stock=0)",
        "Ordered_spare_arrives_(stock=0)",
    ]
    assert result.draws == 0


# --------------------------------------------------------------------------
# Mechanism 2: a post-LDT start drawing on a spare pool
# --------------------------------------------------------------------------


def test_a_post_ldt_start_takes_its_spare_before_it_waits_out_the_post_delay():
    """A block that starts the run in the post-LDT leg takes a spare from the
    pool at t=0, before it has waited out any of the delay, and is Running
    six hours later.

    Why: `Repair` opens with the SparePool early grab for *every* start
    condition, post-LDT included, and the port
    ports only that grab for a down-start
    (`block_lifecycle.py:1416-1429`). So the spare is consumed at kickoff,
    and the post-LDT wait that follows is a plain `WAIT DURATION startVal`
    with an `End_Partial_post-LDT` row and then the common
    `DetermineNewState("Maintenance Completed")` tail.
   A real capture (`PoolPostLdtStartProbe`,
    cited at `block_lifecycle.py:1410`) is what settled that the grab
    happens; the stock dropping is this file's own evidence of it.

    Setup: one pooled block, `sim_start=5`, `amount_exhausted=80.0`,
    pre-LDT 0 h, `Fixed` 10 h repair, `Fixed` 20 h post-LDT -- so the start
    is 6 h into the post-LDT leg. The pool holds **1** spare and restocks 1
    every 20 h. No SLO, no ESO: a grab that tripped either would be refused
    (see the two tests below), and this one trips neither because the stock
    is 2 afterwards.

    Expected: `RepHold` at t=0 (a post-LDT start opens in `RepHold`, not
    `Repairing`), `End_Partial_pos` at t=6 and `Good` immediately after --
    the 6 h being `0 + 10 + 20 - 0.8 * 30`. The pool's t=20 arrival reports
    `stock=1`, which it can only do if the t=0 grab took the one it started
    with. Zero draws.

    Source: Raptor 7 (`Repair`'s `WHEN "postLDT"` arm),
    Raptor 7 (the early SparePool grab),
    Raptor 7 (the maintenance-completed tail),
    block_lifecycle.py:1405-1448 (`_post_ldt_start`),
    kickoff.py:267-275 (a `preLDT`/`postLDT` start opens `RepHold`);
    docs/engine-design/pools-spares-resources.adoc (Purpose)
    """
    result = run(_chain(_post_ldt_start(),
                        _pool(stock=1, new_spares=1, arrive_every=20.0, rso=1,
                              slo=0),
                        30.0))

    assert_timeline(result, [
        (0.0, "Block", "A", "--", "RepHold"),
        (0.0, "Node", "end", "--", "Bad"),
        (0.0, "System", "State_change", "--", "Red"),
        # `End_Partial_pos` is the log's 15-character truncation of
        # `End_Partial_post-LDT`; one character is all that tells this leg
        # from a pre-LDT one.
        (6.0, "Block", "A", "RepHold", "End_Partial_pos"),
        (6.0, "Block", "A", "End_Partial_pos", "Good"),
        (6.0, "Node", "end", "Bad", "Good"),
        (6.0, "System", "State_change", "Red", "Green"),
        (20.0, "Pool", "SP", "--", "Sparing"),
        (30.0, "Block", "A", "Good", "Done"),
        (30.0, "System", "Phase_change", "--", "Mission_failure"),
        (30.0, "System", "Simulation_Terminate", "--", "--"),
    ])
    # The claim is the stock, and it is the only evidence of the t=0 grab:
    # an untouched pool of 1 would report `stock=2` on its restock.
    assert _pool_comments(result) == ["1_New_spare(s)_arrived_(stock=1)"]
    assert result.draws == 0


def test_a_post_ldt_start_into_an_empty_spare_pool_is_refused():
    """A post-LDT start whose pool is empty is **refused**, not repaired: the
    run raises rather than guessing what `Repair` does with no spare.

    Why: this is the one shape of the post-LDT grab the port will not
    simulate, and it is refused at the point of use rather than silently
    treated as infinite spares. `Repair`'s `IF (PooledSpares[..].Resources>0)`
   has an ELSE arm that no capture in the corpus
    exercises, so the port raises and says so, naming the block and the
    source arm. The message is the assertion: a refusal nobody reads is a
    refusal nobody maintains, and an engine that grew a guess here would
    break this test.

    Setup: the previous test's block and start condition, with the pool's
    stock set to **0**. Routine restocking is on but its first arrival is at
    t=20, long after the t=0 grab.

    Expected: `NotImplementedError` out of the run, naming the block, the
    empty pool and Raptor 7's ELSE arm.

    Source: Raptor 7 (`Repair`'s early SparePool grab, ELSE
    arm), block_lifecycle.py:1424-1427 (`_post_ldt_start`'s refusal)
    """
    with pytest.raises(NotImplementedError) as raised:
        run(_chain(_post_ldt_start(),
                   _pool(stock=0, new_spares=1, arrive_every=20.0, rso=1,
                         slo=0),
                   30.0))

    message = str(raised.value)
    assert "block 'A' starts the run in post-LDT with an empty spare pool" in message, (
        f"the refusal must name the block and the shape it refuses; got "
        f"{message!r}")
    assert "spare-check ELSE arm" in message, (
        f"the refusal must name the Raptor 7 branch it declines to guess; got "
        f"{message!r}")


def test_a_post_ldt_start_whose_spare_grab_would_reorder_the_pool_is_refused():
    """A post-LDT start whose grab trips the pool's stock-level ordering is
    also refused: the port ports the grab, not the re-order it decides.

    Why: `_post_ldt_start` calls `_enter_repair`, which *records* the SLO
    quantity the grab decided on `spare.pool_slo` and expects the later spare
    step to apply it. A down-start has no later spare step, so nothing would
    ever place the order, and the port refuses rather than drop it. The
    refusal is the interesting claim: it is what stops the post-LDT grab from
    being a *fourth* copy of the SLO re-order with different behaviour.

    Setup: the post-LDT block of the test above, with the pool holding **1**
    spare, no routine restocking, and SLO configured `level=1`,
    `slo_new_spares=1` -- so the t=0 grab takes the stock to 0, which is at
    or below `level - quantity`, and the grab decides to order one.

    Expected: `NotImplementedError` out of the run, naming the block and
    stock-level ordering as the unported consequence.

    Source: Raptor 7 (`Repair`'s early grab, which decides the
    re-order), Raptor 7 (the second call site that would apply
    it -- absent on a down-start), block_lifecycle.py:1430-1433
    (`_post_ldt_start`'s second refusal)
    """
    with pytest.raises(NotImplementedError) as raised:
        run(_chain(_post_ldt_start(),
                   _pool(stock=1, new_spares=1, arrive_every=500.0, rso=0,
                         slo=1, level=1, quantity=1, slo_time=5.0),
                   30.0))

    message = str(raised.value)
    assert "block 'A': a post-LDT start's spare grab triggers stock-level ordering" in message, (
        f"the refusal must name the block and what it declines to simulate; "
        f"got {message!r}")
    assert "not captured" in message, (
        f"the refusal must say why the port will not guess; got {message!r}")


def test_a_post_ldt_delay_still_outstanding_at_the_end_of_the_run_ends_the_block():
    """A post-LDT start whose remaining delay has not run out by the end of
    the run is finished by the run's own end-of-simulation sweep, with no
    `End_Partial_pos` row at all.

    Why: the post-LDT wait is still outstanding when `EndSimulation` runs, so
    the block is parked in `RepHold` when the run stops and is put `Done` by
    the sweep -- **not** by `_post_ldt_start`'s own "the run ended during the
    wait" guard, which compares against the *block's* horizon rather than the
    model's run length (the test below pins what that guard actually does).
    The grab has already happened at t=0, so the pool is one shorter for the
    whole run either way.

    Setup: the post-LDT block of the test above with a `Fixed` **200 h**
    post-LDT, so the remaining delay is `0 + 10 + 200 - 0.8 * 210 = 42` h
    against a 30-hour run. The pool holds 1 spare and restocks at t=20.

    Expected: `RepHold` at t=0, the pool's t=20 arrival, and `RepHold ->
    Done` at t=30 with **no** `End_Partial_pos` between them -- the delay
    never ended, so the row that ends one was never written. Zero draws.

    Source: Raptor 7 (`Repair`'s `WHEN "postLDT"` arm),
    block_lifecycle.py:1439-1443 (the wait and its log row);
    block_lifecycle.py:1435-1438 is the guard this fixture does *not*
    reach, and its docstring says why
    """
    result = run(_chain(_post_ldt_start(post_delay=200.0),
                        _pool(stock=1, new_spares=1, arrive_every=20.0, rso=1,
                              slo=0),
                        30.0))

    assert_timeline(result, [
        (0.0, "Block", "A", "--", "RepHold"),
        (0.0, "Node", "end", "--", "Bad"),
        (0.0, "System", "State_change", "--", "Red"),
        (20.0, "Pool", "SP", "--", "Sparing"),
        (30.0, "Block", "A", "RepHold", "Done"),
        (30.0, "System", "Phase_change", "--", "Mission_failure"),
        (30.0, "System", "Simulation_Terminate", "--", "--"),
    ])
    # The claim is the stock again: `stock=1` on the t=20 restock is the
    # t=0 grab's work, so the grab happened even though the leg never ended.
    assert _pool_comments(result) == ["1_New_spare(s)_arrived_(stock=1)"]
    assert result.draws == 0


def test_a_post_ldt_delay_past_the_block_horizon_finishes_the_block_a_billion_hours_in():
    """A post-LDT start whose remaining delay exceeds the block's own horizon
    is finished by `_post_ldt_start` itself, a billion hours in -- and that
    billion is the *horizon*, not this model's own 1.5-billion-hour end.

    Why: `_post_ldt_start`'s guard reads `self.run_length`, and on a
    `BlockProcess` that field is the **block horizon**, which
    `kickoff.block_horizon` sets to `_FAR_FUTURE` for every termination type
    (`kickoff.py:483`) -- the same value the `beyond_horizon` fail-wait
    compares against. So the guard cannot fire for any post-LDT delay an
    ordinarily-modelled thousand-year run would configure: it fires only once
    the remaining delay passes 1e9 h, and then it waits out the *horizon*,
    not the run. Worth pinning precisely because the literal is a number a
    reviewer would otherwise have to guess: 20% of a 6,000,000,010 h cycle is
    1,200,000,002 h, which is past 1e9, so the guard fires at t=0 and the
    block goes `Done` at t=1e9 rather than at the model's 1.5e9.

    Setup: the same block with a `Fixed` 6e9 h post-LDT, `amount_exhausted`
    80.0, and a **1.5e9 h** run -- long enough for the horizon-long wait to
    complete inside it. The pool holds 1 spare with no restocking at all, so
    the run schedules nothing else and the two jumps are the only steps the
    scheduler takes. Zero draws, and no pool row at all.

    Expected: `RepHold` at t=0, `RepHold -> Done` at t=1e9 (the horizon, the
    guard's own `Done End_sim`), and the run's own `Done -> Done` at t=1.5e9.

    Source: block_lifecycle.py:1435-1438 (`_post_ldt_start`'s bounded-wait
    guard and its `Done End_sim`), block_lifecycle.py:681 (`self.run_length = env.run_length`),
    kickoff.py:483 (`block_horizon = _FAR_FUTURE`, for every termination type), raptor/core/engine/wait_sites.py
    (`WaitSite.BEYOND_HORIZON`, the other reader of the same value);
    tests/behaviour/test_b_wait_sites.py
    (`test_beyond_horizon_wait_ends_a_billion_hours_in` -- the same 1e9, in
    the ordinary fail-wait)
    """
    result = run(_chain(_post_ldt_start(post_delay=6e9),
                        _pool(stock=1, new_spares=1, arrive_every=500.0, rso=0,
                              slo=0),
                        1.5e9))

    assert_timeline(result, [
        (0.0, "Block", "A", "--", "RepHold"),
        (0.0, "Node", "end", "--", "Bad"),
        (0.0, "System", "State_change", "--", "Red"),
        # A billion hours in, not at the model's own 1.5e9 end.
        (1e9, "Block", "A", "RepHold", "Done"),
        (1.5e9, "Block", "A", "Done", "Done"),
        (1.5e9, "System", "Phase_change", "--", "Mission_failure"),
        (1.5e9, "System", "Simulation_Terminate", "--", "--"),
    ])
    assert _pool_comments(result) == []
    assert result.draws == 0
