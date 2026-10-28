"""The weak-link accountant: hours per state, and the mission credit.

`raptor/core/engine/accounting.py` is the cost-analysis and weak-link half of
`RBDBlockObj.ChangeBlockState`, split out of `blocks.change_block_state` as two
observers of one typed `StateChange`. `test_b_costs.py` owns the first
(`CostAccountant`, the money). This file owns the second
(`WeakLinkAccountant`), which answers a different question about the same
transition: **not what the hours cost, but how many there were, per state.**

That question has exactly two answers in a report, and both are printed by
`***WEAK_LINK_ANALYSIS***`'s `BLOCK_RESULTS` (`reports._build_weak_section`):

* the **percentage row** -- `%Running %Standby %Idle %Repairing %RepHold %PM
  %PMHold %Done`, each one state's hours over the block's whole life. Every
  arm of the `WeakLinkAccountant` dispatch lands in exactly one of those eight
  columns, so the row is the whole dispatch, one number per arm.
* the **`R` column** -- `mCompleted / missionsAttempted`
  (`reports._weak_block_metrics`). Its numerator is
  what Raptor 7 calls the block's *mission bool*: a block that
  enters a maintenance-like state during a mission forfeits the credit for
  that mission, and one that does not keeps it.

**Why the percentage row is the honest observable.** `BlockStatistics` holds eight
accumulators and nothing else prints them individually, so a test that wanted
`repair_time` directly would have to reach into the engine's own state. The
report row is that state, read back through the arithmetic a user reads, and
the arithmetic is the claim: percentages are printed to six decimals, so with
`Fixed` lives every spell is an integer and the row sums to 100.000000 exactly.

**Every model here is `Fixed` (code 19)**, so each row is hand arithmetic and
each run consumes no randomness: `Run.draws == 0` is asserted, not hoped for
(B2). One replication, and at most four blocks and four nodes (B4).
"""
from __future__ import annotations

from typing import Any

from . import harness
from .spine import FIXED, spine

#: `SpType = "None"` -- a block with no sparing of any kind. Its failure has
#: nothing to draw on, so `CheckForSpare` takes `Done "No_spares"` and the
#: block is finished for the rest of the run
#: (`docs/engine-design/pools-spares-resources.adoc`, Source anchors).
NO_SPARES = {"sp_type": "None"}


def _weak_table(eor: list[str], column: str
                ) -> tuple[list[str], dict[str, list[str]]]:
    """`(header, {block name: row})` for one `BLOCK_RESULTS` table.

    `BLOCK_RESULTS` is two tables with the same first column: the metrics one
    (`Ao`, `Do`, `R`) and the per-state percentage one (`%Running` ..
    `%Done`). *column* says which table is wanted -- a name only the wanted
    header carries -- and the row index comes from the header itself, so the
    test names a column rather than counting spaces.

    The rows of a table run until the blank line that ends it; nothing here
    knows what the columns mean, which is what makes this extraction and not a
    second implementation.
    """
    tail = eor[eor.index("***WEAK_LINK_ANALYSIS***"):]
    body = tail[tail.index("BLOCK_RESULTS"):]
    head = next(i for i, line in enumerate(body)
                if line.startswith("Block ") and column in line.split())
    rows: dict[str, list[str]] = {}
    for line in body[head + 1:]:
        if not line.strip():
            break
        fields = line.split()
        rows[fields[0]] = fields
    return body[head].split(), rows


def _weak(eor: list[str], name: str, column: str) -> str:
    """One cell of *name*'s row in the `BLOCK_RESULTS` table that has
    *column*."""
    header, rows = _weak_table(eor, column)
    if name not in rows:
        raise AssertionError(
            f"no BLOCK_RESULTS row for {name!r}; the report has "
            + ", ".join(sorted(rows)))
    return rows[name][header.index(column)]


def _no_spare_series() -> dict[str, Any]:
    """One block with no sparing at all, between a Start and an End node.

    The smallest model that reaches `Done` **mid-run**, which is the only way a
    block is already `Done` when the run-end flush reaches it -- and so the
    only way the weak-link dispatcher sees `old_op_status == Done` at all.
    `A` fails at t=50 with nothing to repair it, so the run is
    `Good -> Failed -> Done` in one instant and the clock does the rest.
    """
    return {
        "system": {"run_length": 100.0, "num_runs": 1, "termination": "TimeSim"},
        "blocks": [{"name": "A", "fail_dist": FIXED, "fail_params": [50.0],
                    "repair_dist": FIXED, "repair_params": [10.0],
                    **NO_SPARES}],
        "nodes": [{"name": "Start", "type": 1, "n": 0, "out": 1},
                  {"name": "End", "type": 3, "n": 1, "out": 0}],
        "links": [{"from": "Start", "to": "A"}, {"from": "A", "to": "End"}],
    }


# ---------------------------------------------------------------------------
# One column per state: the eight arms of the dispatch
# ---------------------------------------------------------------------------

def test_the_hours_of_a_repair_and_of_a_pm_are_the_blocks_own_and_nothing_else() -> None:
    """A block's percentage row is its hours in each state, summed over
    spells -- so two PMs share one `%PM` column.

    Why: `WeakLinkAccountant` adds `stateTime` to the accumulator of the state
    a block is *leaving* (`accounting.py:149-164`), which is the same
    "charge the state just left" rule the cost side uses and for the same
    reason: the hours are only known once the block is out of the state. A
    has two spells in each of three states -- running 0->25 and 27->52 and
    62->87 and 89->100, `PM` 25->27 and 87->89, repairing 52->62 -- so the row
    is a sum of four, two and one spells rather than a reading of any one
    transition. The PM hours are *not* operating time: they are why A's
    `Fixed` 50-hour life completes at t=52 (`spine._s4`).

    Setup: S4 with `weak_analysis` on. Cost analysis stays off, so the only
    thing reading `BlockStatistics` here is the weak-link report.

    Expected: A's row reads `86.000000` running, `10.000000` repairing and
    `4.000000` PM, everything else `0.000000` -- 86 + 10 + 4 = the 100-hour
    run exactly, which is what makes the row an accounting rather than a
    sample. If PM hours were counted as running the row would read 90/10/0.

    Source: accounting.py:149-164 (the weak-link dispatch);
    Raptor 7 (the PM/failure race that fixes where A's two PMs
    and its one repair fall)
    """
    run = harness.run(spine(4), weak_analysis=True)
    assert run.draws == 0
    assert _weak(run.eor, "A", "%Running") == "86.000000"
    assert _weak(run.eor, "A", "%Repairing") == "10.000000"
    assert _weak(run.eor, "A", "%PM") == "4.000000"
    assert _weak(run.eor, "A", "%Idle") == "0.000000"


def test_the_hours_a_block_waits_for_the_repair_pool_are_rephold_hours() -> None:
    """Time queued for the one repair unit is `RepHold`, and it is counted
    once.

    Why: `RepHold` is a state of its own in the weak-link dispatch, not part
    of `Repairing` (`accounting.py:153-154`), so the queue neither inflates the
    repair column nor escapes the row entirely. In S3, B fails at t=55 while A
    holds the only unit until t=60, so B has five `RepHold` hours and five
    `Repairing` hours, and its running hours are 55 before the failure plus 35
    after the repair.

    Setup: S3 with `weak_analysis` on.

    Expected: B reads `5.000000` RepHold and `5.000000` Repairing, and
    `90.000000` Running -- 90 + 5 + 5 = 100. Charging the queue at the repair
    rate instead would put B at 90/0/10.

    Source: accounting.py:153-154 (the RepHold arm);
    docs/engine-design/pools-spares-resources.adoc (Structure: the RepHold
    park and its grant)
    """
    run = harness.run(spine(3), weak_analysis=True)
    assert run.draws == 0
    assert _weak(run.eor, "B", "%RepHold") == "5.000000"
    assert _weak(run.eor, "B", "%Repairing") == "5.000000"
    assert _weak(run.eor, "B", "%Running") == "90.000000"


def test_the_hours_a_dependence_holds_a_block_are_idle_hours() -> None:
    """A block idled by a dependence is charged those hours to `Idle`, and its
    failure clock is stopped while it is.

    Why: `Idle` is its own arm (`accounting.py:159-160`), separate from both
    `Repairing` and the queue. In S6, B depends on A with `DepIdle = 100%`,
    so the backflow dispatcher idles B each time A goes down -- at A's
    triggered PM (t=30) and at its failure (t=52) -- and B's idle spells are
    30->32, 52->62 and 90->92, fourteen hours in all. B's own life is the one
    that moves, because idle time is not operating time: its `Fixed` 55-hour
    life completes at t=67.

    Setup: S6 with `weak_analysis` on.

    Expected: B reads `14.000000` Idle, `5.000000` Repairing and `81.000000`
    Running -- 81 + 14 + 5 = 100, with the fourteen idle hours in the Idle
    column and nowhere else.

    Source: accounting.py:159-160 (the Idle arm); Raptor 7
    (BackFlowDependencies' `Down` arm, which is what idles B)
    """
    run = harness.run(spine(6), weak_analysis=True)
    assert run.draws == 0
    assert _weak(run.eor, "B", "%Idle") == "14.000000"
    assert _weak(run.eor, "B", "%Repairing") == "5.000000"
    assert _weak(run.eor, "B", "%Running") == "81.000000"


def test_the_hours_a_cold_member_is_demoted_are_standby_hours() -> None:
    """A cold-standby member is `Standby` from kickoff until it is promoted,
    and those hours are its own.

    Why: `Standby` is its own arm (`accounting.py:161-162`), which is what
    makes the cold group's availability different from a dependent block's:
    D is not idle and has not failed, it is switched down. S8's cold group
    keeps C running (`COLD_PRIO_RUN` 10 beats D's 20), so D sits in `Standby`
    for the first half of the run; C fails at t=50 and D is promoted, after
    which it runs to the end.

    Setup: S8 with `weak_analysis` on.

    Expected: D reads `50.000000` Standby and `50.000000` Running -- the two
    halves of a 100-hour run, to six decimals, with the promotion instant
    landing exactly on the boundary.

    Source: accounting.py:161-162 (the Standby arm); Raptor 7
    (`RunColdLogic`, which is what demotes D and promotes it back)
    """
    run = harness.run(spine(8), weak_analysis=True)
    assert run.draws == 0
    assert _weak(run.eor, "D", "%Standby") == "50.000000"
    assert _weak(run.eor, "D", "%Running") == "50.000000"


def test_the_hours_a_pm_waits_for_the_repair_pool_are_pm_hold_hours() -> None:
    """With `PMReqDefer` off, a PM that cannot get the pool is `pmHold`, and
    the wait is counted as `PMHold`.

    Why: `PMHold` has its own arm (`accounting.py:157-158`) distinct from
    `RepHold`, and the state is only reachable when the model asks for it:
    with the default `PMReqDefer` the source does not hold at all, it logs a
    hand-built `pmReqDefer` line and hands the block back to `Run`,
   which is not a `BlockStatus` and so is charged
    nothing at all. This model turns the flag off, so A holds.

    Setup: S4 with `pm_req_defer = False` on A, A's PM due at 25, and B
    changed to fail at 20 with a 20-hour repair so it holds the only unit of
    `R` across A's PM. (The same fixture as `test_b_costs.py`'s `PMHold`
    cost test, read here for hours rather than money.)

    Expected: A reads `15.000000` PMHold, `13.000000` RepHold (its own repair
    at t=67 queues behind B's second repair until t=80), `10.000000`
    Repairing (t=80 to t=90) and `2.000000` PM (t=40 to t=42) --
    60 + 13 + 10 + 2 + 15 = 100. The PMHold column exists at all only because
    the flag is off.

    Source: Raptor 7 (the pmReqDefer branch of ObtainPMprereqs),
    accounting.py:157-158 (the PMHold arm)
    """
    spec = spine(4)
    for block in spec["blocks"]:
        if block["name"] == "A":
            block["pm_req_defer"] = False
        else:
            block["fail_params"] = [20.0]
            block["repair_params"] = [20.0]
    run = harness.run(spec, weak_analysis=True)
    assert run.draws == 0
    assert _weak(run.eor, "A", "%PMHold") == "15.000000"
    assert _weak(run.eor, "A", "%RepHold") == "13.000000"
    assert _weak(run.eor, "A", "%Repairing") == "10.000000"
    assert _weak(run.eor, "A", "%PM") == "2.000000"
    assert _weak(run.eor, "A", "%Running") == "60.000000"


def test_a_block_that_can_never_be_repaired_spends_the_rest_of_the_run_in_done() -> None:
    """A block that reaches `Done` mid-run is charged for every hour it then
    sits there, and the run-end flush is what charges them.

    Why: this is the only way the weak-link dispatcher's `Done` arm
    (`accounting.py:163-164`) is reached at all. `Done` is terminal, so the
    hours a block spends in it can only be accrued by a *later* transition out
    of `Done` -- and the only such transition is the end-of-run `End_sim`
    flush, which writes a `Done -> Done` row for every block whether or not it
    was already `Done` (`driver.finalize`). Without that row the hours would
    be lost, and the row would read `%Done = 0.000000` for a block that spent
    half the run with no path home.

    Setup: one block with `SpType = "None"`, so its `Fixed` 50-hour life ends
    with nothing to repair it: `Good -> Failed -> Done` at t=50.

    Expected: A reads `50.000000` Done and `50.000000` Running. The arms this
    model does not exercise stay at `0.000000`: `Standby` because nothing is
    demoted, `Idle` because nothing depends on A.

    Source: accounting.py:163-164 (the Done arm);
    docs/engine-design/pools-spares-resources.adoc (Source anchors:
    `CheckForSpare`, the `Done "No_spares"` that starts the wait)
    """
    run = harness.run(_no_spare_series(), weak_analysis=True)
    assert run.draws == 0
    assert _weak(run.eor, "A", "%Done") == "50.000000"
    assert _weak(run.eor, "A", "%Running") == "50.000000"
    assert _weak(run.eor, "A", "%Standby") == "0.000000"
    assert _weak(run.eor, "A", "%Idle") == "0.000000"


# ---------------------------------------------------------------------------
# The mission credit: Raptor 7
# ---------------------------------------------------------------------------

def test_a_block_that_goes_down_during_the_mission_reports_no_mission_credit() -> None:
    """A block that enters a maintenance-like state during a mission loses
    that mission's credit, and `R` says so.

    Why: Raptor 7 clears the block's mission bool when it
    enters `Repairing`/`RepHold`/`PM`/`PMHold`/`Done` while
    `System.missionStatus = Mission`, and the run-end close credits only the
    blocks that still hold it (`system_status.py:389-394`). The printed `R` is
    that credit over the missions attempted -- one for an unphased run
    (`kickoff.py:168`) -- so a block that failed reads `0.000000` and one that
    did not reads `1.000000`. A fails at t=52, inside the mission, so its
    credit is gone.

    Setup: S4 with `weak_analysis` on and cost analysis off.

    Expected: A's `R` column reads `0.000000000`. `1.000000000` here would mean
    the forfeit never fired.

    Source: Raptor 7 (the mission-credit forfeit);
    accounting.py:165-173 (the port's own statement of the rule, `End_sim`
    excluded)
    """
    run = harness.run(spine(4), weak_analysis=True)
    assert run.draws == 0
    assert _weak(run.eor, "A", "R") == "0.000000000"


def test_a_block_that_never_goes_down_keeps_its_credit_through_the_end_of_run_flush() -> None:
    """The end-of-run `Done` transition does **not** forfeit the credit, so a
    block that never failed reports full credit.

    Why: the source guards the clear with `IF comment <> "End_sim"`
    (restated at `accounting.py:165-168`), because
    the run-end flush moves *every* block to `Done` -- so without the guard
    the flush would retroactively fail every block in the model and `R` would
    read `0.000000000` for all of them, which is why this is a separate claim
    from the one above rather than the same row read twice. D is demoted to
    `Standby` at kickoff and promoted at t=50, and `Standby` is not one of the
    five maintenance-like states, so D holds its bool for the whole mission.

    Setup: S8 with `weak_analysis` on.

    Expected: D's `R` column reads `1.000000000` -- one mission attempted, one
    completed by this block -- while A, which failed, reads `0.000000000` in
    the same table.

    Source: Raptor 7 (`IF comment<>"End_sim"`),
    accounting.py:165-173 (the port's own statement of the rule and of the
    exclusion)
    """
    run = harness.run(spine(8), weak_analysis=True)
    assert run.draws == 0
    assert _weak(run.eor, "D", "R") == "1.000000000"
    assert _weak(run.eor, "A", "R") == "0.000000000"