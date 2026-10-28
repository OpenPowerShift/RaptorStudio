"""The last corpus rows: transitions that needed a purpose-built model.

`tests/behaviour/behaviour_manifest.toml` classifies all 318 `(object_type,
from -> to)` rows real Raptor wrote. This module carries the literals for the
twenty-three that were still `planned` once the four cluster files were
finished. Six of them -- `RepHold -> End_Partial_pre`, `Idle -> RepHold`,
`Idle -> pmReqDefer`, `Linked -> Standby`, `Standby -> Resources` and
`Node Switch_Failed -> Degraded` -- had been argued unreachable by the
previous pass, and are not; the models below are what refutes each claim.
Every fixture is built in memory and run through `harness.run` (B3); every
expectation is a literal written inline in the `assert_timeline` call, which
`test_b_behaviour_coverage.py`'s `ast` pass reads without importing this
module (B5).

**Three shapes recur, and between them they are the whole of what was left.**

* **The fifteen-column vocabulary.** `End_Partial_pre-LDT` and
  `End_Partial_post-LDT` are longer than the action column Raptor writes, so
  the log -- and every legacy capture -- records them truncated, as
  `End_Partial_pre` and `End_Partial_pos`. The four rows
  `RepHold -> End_Partial_pre`, `End_Partial_pre -> Repairing`,
  `RepHold -> End_Partial_pos` and `End_Partial_pos -> Good` are one
  mechanism: `sim_start_type=5` puts the block part-way through its repair
  cycle at kickoff (`block_lifecycle.py:388-409`), and the partial wait that
  results writes a row of its own ( for the pre-LDT leg,
   for the post-LDT one). Which leg `AmtExhausted` selects is the
  whole difference between the two fixtures.
* **A `Sparing`, `Resources` or `pmReqDefer` row with no state change of its
  own.** These are raw event rows, so the row that follows one is decided by
  whatever the engine logs next for that object: a stock order arriving, a
  resource being handed straight back, a cold-group demotion. Each of those
  fixtures below arranges exactly one of them and asserts the whole timeline.
* **A row that only exists across a run boundary.** `Done -> RepHold` and
  `Done -> Repairing` are a block run *n* left `Done` and run *n+1* starts
  down: Raptor does not re-announce an object at `Begin_Run_N`, so the
  transition is measured from whatever the previous run last said
  (`behaviour_inventory.py:26-30`). `Run.cross_run` is the harness's own chain
  of exactly that, and rule B4's `runs > 1` exception applies because the run
  boundary *is* the property under test.

Every model is at most three blocks and four nodes, one replication except the
two that are about the boundary, and every distribution `Fixed`, so
`Run.draws == 0` is asserted rather than hoped for (B2).

**What is still open.** `Node Bad -> Switch_Failed` and
`Node NodePM -> Switch_Failed` remain `planned`, and the two `Hier X -> X`
rows are reclassified `unreachable`. The manifest's `reason` for the `Hier`
pair gives the chain of citations; for the two `Node` rows the position is
this. Both need the survey's `Switch_Failed` row to be preceded by a `Bad` /
`NodePM` row written by an *earlier* survey, because `run_cold_logic` emits
every `Switch_Failed` of a pass before that pass's own resurvey aggregate
(`cold_standby.py:443-451`). At a `Switch_Failed` the node cannot itself be
`Bad` or `NodePM`: the two call sites are guarded so that
`ready_paths >= runs_required` (the promotion arm) or
`ready_paths < runs_required >= k_min` (the initiation arms),
and `runs + standbys` is at least `ready_paths` either way, so
`determine_node_status` never picks `Bad` or `NodePM` there
(`nodes.py:281-299`). So the `Bad` has to come from a pass that found no
un-switchable candidate -- which means a candidate that becomes `"none"`
*while* the node is `Bad`. `switch_status` becomes `"none"` only in
`set_switch_time`, called only when a cold link's status changes
to `LinkStandby`, i.e. when some block enters `Standby`. Every
such entry is either a `Too_many_on` (which needs `ready_paths >=
runs_required`, so not while `Bad`), the active-status demotion in
`_demote_running_cold_source` (which leaves a `Cut` / `Linked` link, never a
`LinkStandby` one), or a follow into `Standby` because a dependency target is
already there -- `BackFlowDependencies(NodeStandby)` or
`repair_completion_status`'s `Standby` recovery. The port refuses the
same-node instance of the last one (`model_checks`:
"it is one of the cold-standby paths into node X and depends on Y, another
path into the same node"), and the cross-node instance needs two cold groups,
a `sim_start` down block to force the second group's `Too_many_on` late, and
six blocks -- past what rule B4 calls a small model, and not built here. That
is why these two are left `planned` rather than argued: the route is
identified, not closed.

Source: raptor/core/engine/block_lifecycle.py:277-426 (`get_start_cond`),
 (`_post_ldt_start`) (`_pre_ldt`)
(`_repair`) (`_misdefer_give_back`)
(`_request_pm_resource`), raptor/core/engine/pool.py:499-573
(`order_a_custom_spare_process`, `order_custom_stock_process`),
raptor/core/engine/cold_standby.py:440-573 (`run_cold_logic`),
raptor/core/engine/orchestrator.py:771-783 (`_demote_running_cold_source`),
raptor/core/engine/cascade_phase.py:131-164 (`apply_block`),
raptor/core/engine/blocks.py:169-185 (`change_block_state`'s action choice);
"""
from __future__ import annotations

from typing import Any

from .harness import (
    FIXED, assert_timeline, block, cross_run_view, run)

#: Distribution code 18 -- `Distribution.NONE`. A block with no repair
#: distribution takes `CheckForSpare` and then `Done No_repair`
#: (`block_lifecycle.py:3778-3789`), which is how an emergency order's own
#: `Sparing` row lands *after* the `Done` row.
NONE = 18


def _chain(*names: str) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """`Start -> names -> Join -> End`, or `Start -> name -> End` for one."""
    nodes: list[dict[str, Any]] = [{"name": "Start", "type": 1, "n": 0,
                                   "out": len(names)}]
    links: list[dict[str, Any]] = [{"from": "Start", "to": n} for n in names]
    if len(names) == 1:
        links.append({"from": names[0], "to": "End"})
        nodes.append({"name": "End", "type": 3, "n": 1, "out": 0})
        return nodes, links
    links.extend({"from": n, "to": "Join"} for n in names)
    nodes.append({"name": "Join", "type": 2, "n": len(names), "out": 1,
                  "k": 1})
    links.append({"from": "Join", "to": "End"})
    nodes.append({"name": "End", "type": 3, "n": 1, "out": 0})
    return nodes, links




def _fixed(result: Any) -> None:
    """Assert rule B2: an all-`Fixed` model consumes no randomness."""
    assert result.draws == 0, (
        "every distribution in this fixture is Fixed, so the run must draw "
        f"nothing; it drew {result.draws}")


def _custom_pm_spec(run_length: float, life: float,
                    holder: tuple[float, float], pm_freq: float,
                    phase_len: float) -> dict[str, Any]:
    """`Start -> A, H -> Join -> End`, `A` the deferred PM and `H` holding the
    one PM resource, with four alternating mission / non-mission phases."""
    nodes, links = _chain("A", "H")
    return {
        "system": {"run_length": run_length, "num_runs": 1,
                   "termination": "TimeSim"},
        "blocks": [
            block("A", life, 5.0, id=1, uses_pm=True, pm_freq=pm_freq,
                   pm_dist=FIXED, pm_params=[5.0], num_res=1, res1_pm_rep=1,
                   resource_name="R", pm_refresh=False, pm_fail_reset=False,
                   pm_spare=True, pm_mis_defer=True, pm_req_defer=True,
                   sp_type="Pooled", spare_pool="SP"),
            block("H", holder[0], holder[1], id=2, num_res=1,
                   resource_name="R"),
        ],
        "spare_pools": [{"name": "SP", "stock": 0, "new_spares": 1,
                         "arrive_every": 20.0, "rso": 1}],
        "resources": [{"name": "R", "num_in": 1}],
        "nodes": nodes, "links": links,
        "phases": [{"name": f"P{index + 1}", "mission": 1 - (index % 2),
                    "dist": FIXED, "params": [phase_len]} for index in range(4)],
    }


# ---------------------------------------------------------------------------
# The two partial-logistics-delay down starts
# ---------------------------------------------------------------------------


# ---
# test_a_preldt_down_start_writes_the_partial_pre_row_and_then_repairs


def test_a_preldt_down_start_writes_the_partial_pre_row_and_then_repairs() -> None:
    """`RepHold -> End_Partial_pre` and `End_Partial_pre -> Repairing`: a
    block that begins the simulation part-way through its pre-LDT delay.

    Why: `sim_start_type=5` is "failed, N% through the repair cycle", and
    `get_start_cond` places the block deterministically -- it draws the pre-LDT,
    the repair and the post-LDT lengths, then picks the arm
    `AmtExhausted/100` falls in (`block_lifecycle.py:388-409`). A percentage
    inside the pre-LDT leg returns `StartCond("preLDT", first_pre_ldt=pre_ldt -
    down_percent*cycle)`, and `_pre_ldt`'s partial-wait branch pays exactly
    that remainder and writes `End_Partial_pre-LDT`.
    The action is 19 characters, so the 15-wide log column
    truncates it -- which is why the corpus, and this literal, spell it
    `End_Partial_pre`. `Repair` then goes on to the resource step and
    `Repairing`, which is the row after it.

    Setup: one block, a 20-hour `Fixed` repair and a 4-hour `Fixed` pre-LDT,
    `sim_start=5` with `amt_exhausted=6.25`. Six and a quarter percent of
    the 24-hour cycle is 1.5 hours, so the partial wait is 2.5 and the
    block repairs at 2.5 for the remaining 20. 60 sim hours, one
    replication, every distribution `Fixed`.

    Expected: `A` starts `RepHold` at 0, writes `End_Partial_pre` at 2.5
    and `Repairing` in the same instant, repairs over the remaining 20
    hours and is `Good` at 22.5. The two rows of interest are adjacent
    and neither is a `ChangeBlockState`.

    Source: raptor/core/engine/block_lifecycle.py:388-409 (`get_start_cond`'s
    type-5 preLDT arm) (`_down_start`),
     (`_pre_ldt`'s partial-wait row) (the ordinary
    pre-LDT draw it shares), raptor/core/engine/eventlog.py:65 (the padded,
    truncated action column); Raptor 7
    """
    nodes, links = _chain("A")
    result = run({
        "system": {"run_length": 60.0, "num_runs": 1,
                   "termination": "TimeSim"},
        "blocks": [block("A", 500.0, 20.0, sim_start=5, amt_exhausted=6.25,
                          pre_ldt_dist=FIXED, pre_ldt_params=[4.0])],
        "nodes": nodes, "links": links,
    })

    _fixed(result)

    assert_timeline(result, [
        (0.0, 'Block', 'A', '--', 'RepHold'),
        (0.0, 'Node', 'end', '--', 'Bad'),
        (0.0, 'System', 'State_change', '--', 'Red'),
        (2.5, 'Block', 'A', 'RepHold', 'End_Partial_pre'),
        (2.5, 'Block', 'A', 'End_Partial_pre', 'Repairing'),
        (2.5, 'System', 'Red_change', '--', 'Red'),
        (22.5, 'Block', 'A', 'Repairing', 'Repaired'),
        (22.5, 'Block', 'A', 'Repaired', 'Good'),
        (22.5, 'Node', 'end', 'Bad', 'Good'),
        (22.5, 'System', 'State_change', 'Red', 'Green'),
        (60.0, 'Block', 'A', 'Good', 'Done'),
        (60.0, 'System', 'Phase_change', '--', 'Mission_failure'),
        (60.0, 'System', 'Simulation_Terminate', '--', '--'),
    ])


# ---
# test_a_postldt_down_start_writes_the_partial_post_row_and_then_goes_good


def test_a_postldt_down_start_writes_the_partial_post_row_and_then_goes_good() -> None:
    """`RepHold -> End_Partial_pos` and `End_Partial_pos -> Good`: the same
    down-start mechanism on the *post*-LDT leg.

    Why: the same `get_start_cond` type-5 arm, with `AmtExhausted` past the
    repair rather than inside the pre-LDT, returns `StartCond("postLDT",
    start_val=pre_ldt + repair + post_ldt - down_percent*cycle)`
    (`block_lifecycle.py:408-409`). `_post_ldt_start` then waits that
    remainder, writes `End_Partial_post-LDT` and falls into
    `DetermineNewState("Maintenance Completed")`, which logs
    `Good` -- so the two rows are adjacent. `End_Partial_post-LDT` is 20
    characters and the log column is 15, so the corpus spells it
    `End_Partial_pos`.

    Setup: the same 20-hour repair with 4-hour pre- and post-LDT legs;
    `sim_start=5` with `amt_exhausted=95.0` puts 95% of the 30-hour cycle
    behind the block, leaving 1.5 hours of post-LDT. 60 sim hours, one
    replication, every distribution `Fixed`.

    Expected: `A` starts `RepHold` at 0, writes `End_Partial_pos` at 1.5
    and goes straight `Good` in the same instant -- the whole repair is
    already behind it, so there is no `Repairing` row at all.

    Source: raptor/core/engine/block_lifecycle.py:408-409 (`get_start_cond`'s
    type-5 postLDT arm) (`_post_ldt_start`, its
    `End_Partial_post-LDT` row and its `Maintenance Completed` tail),
     (`_down_start`), raptor/core/engine/eventlog.py:65;
    """
    nodes, links = _chain("A")
    result = run({
        "system": {"run_length": 60.0, "num_runs": 1,
                   "termination": "TimeSim"},
        "blocks": [block("A", 500.0, 20.0, sim_start=5, amt_exhausted=95.0,
                          pre_ldt_dist=FIXED, pre_ldt_params=[4.0],
                          post_ldt_dist=FIXED, post_ldt_params=[6.0])],
        "nodes": nodes, "links": links,
    })

    _fixed(result)

    assert_timeline(result, [
        (0.0, 'Block', 'A', '--', 'RepHold'),
        (0.0, 'Node', 'end', '--', 'Bad'),
        (0.0, 'System', 'State_change', '--', 'Red'),
        (1.5, 'Block', 'A', 'RepHold', 'End_Partial_pos'),
        (1.5, 'Block', 'A', 'End_Partial_pos', 'Good'),
        (1.5, 'Node', 'end', 'Bad', 'Good'),
        (1.5, 'System', 'State_change', 'Red', 'Green'),
        (60.0, 'Block', 'A', 'Good', 'Done'),
        (60.0, 'System', 'Phase_change', '--', 'Mission_failure'),
        (60.0, 'System', 'Simulation_Terminate', '--', '--'),
    ])


# ---
# test_a_postldt_down_start_chains_done_to_rephold_across_a_run_boundary


def test_a_postldt_down_start_chains_done_to_rephold_across_a_run_boundary() -> None:
    """`Done -> RepHold`: the row only exists because run 2 is chained onto
    run 1.

    Why: a run ends with every block flushed `Done End_sim`, and the next run
    does not re-announce the block -- Raptor logs only the down-start's own
    `RepHold Initialize` line. The corpus's chaining rule
    (`behaviour_inventory.py:26-30`) therefore measures the second run's
    `RepHold` from the `Done` the first run left, and that is the row. A single
    run has no previous run, so the fixture is a two-replication batch
    (`harness.Run.cross_run`), which is rule B4's stated exception: the
    property *is* the boundary.

    Setup: one block, `sim_start=5` with `amt_exhausted=95.0`, a 5-hour
    repair and a 5-hour post-LDT, so both runs start in post-LDT and the
    block is `RepHold` at each run's kickoff. 40 sim hours, two replications, every distribution `Fixed`.

    Expected: run 1 is `RepHold -> End_Partial_pos -> Good -> Done`; run 2
    repeats it but its opening row reads `Done -> RepHold`, which is the
    row under test. Asserted against `Run.cross_run`, so both runs are
    in the one literal.

    Source: raptor/core/engine/block_lifecycle.py:265-273 (`init_status` at
    kickoff) (`_post_ldt_start`), raptor/core/engine/kickoff.py
    (the `Initialize` row), tools/behaviour_inventory.py:26-30 (the
    chaining rule), tests/behaviour/harness.py:119-131 (`Run.cross_run`);
    """
    nodes, links = _chain("A")
    batch = run({
        "system": {"run_length": 40.0, "num_runs": 1,
                   "termination": "TimeSim"},
        "blocks": [block("A", 500.0, 5.0, sim_start=5, amt_exhausted=95.0,
                          post_ldt_dist=FIXED, post_ldt_params=[4.0])],
        "nodes": nodes, "links": links,
    }, runs=2)

    _fixed(batch)
    result = cross_run_view(batch)

    assert_timeline(result, [
        (0.0, 'Block', 'A', '--', 'RepHold'),
        (0.0, 'Node', 'end', '--', 'Bad'),
        (0.0, 'System', 'State_change', '--', 'Red'),
        (0.45000000000000107, 'Block', 'A', 'RepHold', 'End_Partial_pos'),
        (0.45000000000000107, 'Block', 'A', 'End_Partial_pos', 'Good'),
        (0.45000000000000107, 'Node', 'end', 'Bad', 'Good'),
        (0.45000000000000107, 'System', 'State_change', 'Red', 'Green'),
        (40.0, 'Block', 'A', 'Good', 'Done'),
        (40.0, 'System', 'Phase_change', '--', 'Mission_failure'),
        (40.0, 'System', 'Simulation_Terminate', '--', '--'),
        (0.0, 'Block', 'A', 'Done', 'RepHold'),
        (0.0, 'Node', 'end', 'Good', 'Bad'),
        (0.0, 'System', 'State_change', 'Green', 'Red'),
        (0.45000000000000107, 'Block', 'A', 'RepHold', 'End_Partial_pos'),
        (0.45000000000000107, 'Block', 'A', 'End_Partial_pos', 'Good'),
        (0.45000000000000107, 'Node', 'end', 'Bad', 'Good'),
        (0.45000000000000107, 'System', 'State_change', 'Red', 'Green'),
        (40.0, 'Block', 'A', 'Good', 'Done'),
        (40.0, 'System', 'Phase_change', 'Mission_failure', 'Mission_failure'),
        (40.0, 'System', 'Simulation_Terminate', '--', '--'),
    ])


# ---
# test_a_repairing_down_start_chains_done_to_repairing_across_a_run_boundary


def test_a_repairing_down_start_chains_done_to_repairing_across_a_run_boundary() -> None:
    """`Done -> Repairing`: the run-boundary sibling of the row above, for
    the *repairing* down-start arm.

    Why: `sim_start_type=6` is "failed, at start of hands-on repair", and its
    `get_start_cond` arm draws one repair length and hands it straight to
    `Repair` (`block_lifecycle.py:411-417`). Kickoff logs
    the block's initial status as `Repairing`, and -- as above -- the run
    boundary is what makes the previous `Done` the measured `from`.

    Setup: one block, `sim_start=6`, a 5-hour `Fixed` repair. 40 sim hours,
    two replications, every distribution `Fixed`. The `Repairing -> Repairing`
    row at t=0 of each run is the `_enter_repair` boundary re-logging the
    status `CheckForSpare` left in place, and is itself a corpus row.

    Expected: each run opens with `Repairing` and repairs over five hours;
    run 2's opening row reads `Done -> Repairing`. The
    `Repairing -> Repairing` row at each kickoff is `CheckForSpare`
    re-logging the status `get_start_cond` left in place.

    Source: raptor/core/engine/block_lifecycle.py:411-417 (`get_start_cond`'s
    type-6 arm) (kickoff's `init_status`)
    (`_down_start`), tools/behaviour_inventory.py:26-30;
    """
    nodes, links = _chain("A")
    batch = run({
        "system": {"run_length": 40.0, "num_runs": 1,
                   "termination": "TimeSim"},
        "blocks": [block("A", 500.0, 5.0, sim_start=6)],
        "nodes": nodes, "links": links,
    }, runs=2)

    _fixed(batch)
    result = cross_run_view(batch)

    assert_timeline(result, [
        (0.0, 'Block', 'A', '--', 'Repairing'),
        (0.0, 'Node', 'end', '--', 'Bad'),
        (0.0, 'System', 'State_change', '--', 'Red'),
        (0.0, 'Block', 'A', 'Repairing', 'Repairing'),
        (5.0, 'Block', 'A', 'Repairing', 'Repaired'),
        (5.0, 'Block', 'A', 'Repaired', 'Good'),
        (5.0, 'Node', 'end', 'Bad', 'Good'),
        (5.0, 'System', 'State_change', 'Red', 'Green'),
        (40.0, 'Block', 'A', 'Good', 'Done'),
        (40.0, 'System', 'Phase_change', '--', 'Mission_failure'),
        (40.0, 'System', 'Simulation_Terminate', '--', '--'),
        (0.0, 'Block', 'A', 'Done', 'Repairing'),
        (0.0, 'Node', 'end', 'Good', 'Bad'),
        (0.0, 'System', 'State_change', 'Green', 'Red'),
        (0.0, 'Block', 'A', 'Repairing', 'Repairing'),
        (5.0, 'Block', 'A', 'Repairing', 'Repaired'),
        (5.0, 'Block', 'A', 'Repaired', 'Good'),
        (5.0, 'Node', 'end', 'Bad', 'Good'),
        (5.0, 'System', 'State_change', 'Red', 'Green'),
        (40.0, 'Block', 'A', 'Good', 'Done'),
        (40.0, 'System', 'Phase_change', 'Mission_failure', 'Mission_failure'),
        (40.0, 'System', 'Simulation_Terminate', '--', '--'),
    ])


# ---
# test_an_unrepairable_block_writes_the_emergency_order_after_done


def test_an_unrepairable_block_writes_the_emergency_order_after_done() -> None:
    """`Done -> Sparing`: the emergency order's own row lands *after* the
    `Done` row that the order provoked.

    Why: with `RDist=18` the block cannot be repaired, so `Repair` logs
    `Sparing No_spare_in_stock-Custom` from `CheckForSpare` and then takes the
    `Done No_repair` route (`block_lifecycle.py:3778-3789`). But
    `CheckForSpare`'s Custom branch has already spawned `OrderASpare` as a
    *deferred* TELL, and the spawned process writes its
    `Sparing Spare_ordered_(stock=...)` line in its own later selection -- so
    it lands after the `Done` row, in the same instant
    (`pool.py:499-537`). `ManTime=-1` with no emergency time set to zero is
    what makes the order's own row the only one; nothing about the order's
    arrival is needed for the row, only that the order was placed.

    Setup: one block, no repair distribution, Custom sparing with zero initial
    stock and emergency ordering enabled with a 30-hour emergency time. 60 sim
    hours, one replication; every distribution is `Fixed`, and the emergency
    time is a plain number, so the run draws nothing.

    Expected: the failure at 10 writes `Sparing No_spare_in_stock-Custom`,
    then `Done No_repair`, then the emergency order's own
    `Sparing Spare_ordered_(stock=0)` in the same instant -- so the
    `Sparing` row is measured from `Done`.

    Source: raptor/core/engine/block_lifecycle.py:3778-3789 (`Repair`'s
    `Done No_repair` route) (`CheckForSpare`'s Custom branch and
    its deferred `OrderASpare`), raptor/core/engine/pool.py:499-537
    (`order_a_custom_spare_process`'s `Spare_ordered` row);
    """
    nodes, links = _chain("A")
    result = run({
        "system": {"run_length": 60.0, "num_runs": 1,
                   "termination": "TimeSim"},
        "blocks": [{"name": "A", "id": 1, "fail_dist": FIXED,
                    "fail_params": [10.0], "repair_dist": NONE,
                    "repair_params": [0.0], "sp_type": "Custom",
                    "Stock": 0, "eso": 1, "emer_time": 30.0}],
        "nodes": nodes, "links": links,
    })

    _fixed(result)

    assert_timeline(result, [
        (0.0, 'Block', 'A', '--', 'Good'),
        (10.0, 'Block', 'A', 'Good', 'Failed'),
        (10.0, 'Block', 'A', 'Failed', 'Sparing'),
        (10.0, 'Block', 'A', 'Sparing', 'Done'),
        (10.0, 'Node', 'end', '--', 'Bad'),
        (10.0, 'System', 'State_change', '--', 'Red'),
        (10.0, 'System', 'Permanently_Down', '--', 'Out_of_spares'),
        (10.0, 'Block', 'A', 'Done', 'Sparing'),
        (40.0, 'Block', 'A', 'Sparing', 'Sparing'),
        (60.0, 'Block', 'A', 'Sparing', 'Done'),
        (60.0, 'System', 'Phase_change', '--', 'Mission_failure'),
        (60.0, 'System', 'Simulation_Terminate', '--', '--'),
    ])


# ---
# test_a_custom_stock_order_lands_after_the_pm_begins_and_before_it_completes


def test_a_custom_stock_order_lands_after_the_pm_begins_and_before_it_completes() -> None:
    """`PM_is_due -> Sparing`, `PM -> Sparing` and
    `Sparing -> PM_complete`, all three from one Custom stock-level order.

    Why: `ObtainPMprereqs` takes the PM's spare from the block's own Custom
    queue, and consuming the last unit drops the stock to the configured
    trigger level, so it spawns `OrderStock` as a deferred TELL
    (`block_lifecycle.py:2605-2631`). That spawn writes its
    `Sparing N_Spare(s)_ordered_(stock=...)` line in its own later selection --
    which for a zero-length `SLOTime` is *before* `ObtainPMprereqs` reaches
    `beginning_PM`, giving `PM_is_due -> Sparing -> PM`, and the batch's
    `Sparing N_Spare(s)_arrived_(stock=...)` line lands `Slotime` later, i.e.
    inside the PM, giving `PM -> Sparing`. The PM then completes, so
    `Sparing -> PM_complete` follows. Three rows, one order, one fixture.

    Setup: one block, Custom sparing with one unit of stock, stock-level
    ordering on with a 2-hour `Slotime`, `pm_spare` so the PM consumes a
    spare, a 20-hour `Fixed` PM frequency and a 10-hour `Fixed` PM. 60 sim
    hours, one replication, every distribution `Fixed`.

    Expected: at 20 the order is placed before `beginning_PM` runs
    (`PM_is_due -> Sparing -> PM`), the batch lands at 22 while the PM is
    still running (`PM -> Sparing`), and the PM completes at 30
    (`Sparing -> PM_complete`). Two PMs, the same three rows twice.

    Source: raptor/core/engine/block_lifecycle.py:2520-2632
    (`_obtain_pm_custom_spare`'s SLO re-order) (the `PM_is_due`
    row and the `ObtainPMprereqs` TELL boundary), raptor/core/engine/pool.py:
    540-573 (`order_custom_stock_process`, both of its rows),
    raptor/core/engine/block_lifecycle.py:2980-3040 (`_perform_pm`'s
    `PM_complete` row); Raptor 7
    """
    nodes, links = _chain("A")
    result = run({
        "system": {"run_length": 60.0, "num_runs": 1,
                   "termination": "TimeSim"},
        "blocks": [block("A", 500.0, 5.0, uses_pm=True, pm_freq=20.0,
                          pm_dist=FIXED, pm_params=[10.0], pm_spare=True,
                          sp_type="Custom", Stock=1, rso=1, slo=1,
                          level=0, quan=1, slotime=2.0, pm_refresh=False,
                          pm_fail_reset=False)],
        "nodes": nodes, "links": links,
    })

    _fixed(result)

    assert_timeline(result, [
        (0.0, 'Block', 'A', '--', 'Good'),
        (20.0, 'Block', 'A', 'Good', 'PM_is_due'),
        (20.0, 'Block', 'A', 'PM_is_due', 'Sparing'),
        (20.0, 'Block', 'A', 'Sparing', 'PM'),
        (20.0, 'Node', 'end', '--', 'NodePM'),
        (20.0, 'System', 'State_change', '--', 'Red'),
        (22.0, 'Block', 'A', 'PM', 'Sparing'),
        (30.0, 'Block', 'A', 'Sparing', 'PM_complete'),
        (30.0, 'Block', 'A', 'PM_complete', 'Good'),
        (30.0, 'Node', 'end', 'NodePM', 'Good'),
        (30.0, 'System', 'State_change', 'Red', 'Green'),
        (50.0, 'Block', 'A', 'Good', 'PM_is_due'),
        (50.0, 'Block', 'A', 'PM_is_due', 'Sparing'),
        (50.0, 'Block', 'A', 'Sparing', 'PM'),
        (50.0, 'Node', 'end', 'Good', 'NodePM'),
        (50.0, 'System', 'State_change', 'Green', 'Red'),
        (52.0, 'Block', 'A', 'PM', 'Sparing'),
        (60.0, 'Block', 'A', 'Sparing', 'PM_complete'),
        (60.0, 'Block', 'A', 'PM_complete', 'Good'),
        (60.0, 'Node', 'end', 'NodePM', 'Good'),
        (60.0, 'Block', 'A', 'Good', 'Done'),
        (60.0, 'System', 'State_change', 'Red', 'Green'),
        (60.0, 'System', 'Phase_change', '--', 'Mission_failure'),
        (60.0, 'System', 'Simulation_Terminate', '--', '--'),
    ])


# ---
# test_a_custom_stock_order_lands_inside_the_post_ldt_wait


def test_a_custom_stock_order_lands_inside_the_post_ldt_wait() -> None:
    """`Sparing -> End_Post-LDT`: the stock order's *arrival* row lands while
    the block is holding in its post-LDT wait.

    Why: `Repair`'s post-LDT arm is `_transition(REP_HOLD, ..., "postLDT")`
    followed by the delay and then the `End_Post-LDT` row
    (`block_lifecycle.py:3846-3859`), so a raw event row written by an
    unrelated process inside that window becomes the block's *last logged
    state*. Here that row is the Custom stock order's arrival, spawned when
    the repair consumed the block's only spare: `Slotime=10` puts the arrival
    at 20, inside the 20-hour post-LDT wait that runs 15 to 35.

    Setup: one block, Custom sparing with one unit and a 10-hour `Slotime`, a
    10-hour life, a 5-hour repair and a 20-hour `Fixed` post-LDT. 40 sim
    hours, one replication, every distribution `Fixed`.

    Expected: the repair consumes the block's only spare at 10 and the
    order is placed (`Repairing -> Sparing`); the batch lands at 20,
    inside the 20-hour post-LDT wait, so `RepHold -> Sparing`; and the
    delay expires at 35 (`Sparing -> End_Post-LDT`).

    Source: raptor/core/engine/block_lifecycle.py:3846-3859 (`Repair`'s
    postLDT arm) (`_enter_repair`'s SLO re-order),
    raptor/core/engine/pool.py:540-573 (`order_custom_stock_process`'s
    arrival row); Raptor 7
    """
    nodes, links = _chain("A")
    result = run({
        "system": {"run_length": 40.0, "num_runs": 1,
                   "termination": "TimeSim"},
        "blocks": [block("A", 10.0, 5.0, sp_type="Custom", Stock=1, rso=1,
                          slo=1, level=0, quan=1, slotime=10.0,
                          post_ldt_dist=FIXED, post_ldt_params=[20.0])],
        "nodes": nodes, "links": links,
    })

    _fixed(result)

    assert_timeline(result, [
        (0.0, 'Block', 'A', '--', 'Good'),
        (10.0, 'Block', 'A', 'Good', 'Failed'),
        (10.0, 'Block', 'A', 'Failed', 'Repairing'),
        (10.0, 'Node', 'end', '--', 'Bad'),
        (10.0, 'System', 'State_change', '--', 'Red'),
        (10.0, 'Block', 'A', 'Repairing', 'Sparing'),
        (15.0, 'Block', 'A', 'Sparing', 'Repaired'),
        (15.0, 'Block', 'A', 'Repaired', 'RepHold'),
        (15.0, 'System', 'Red_change', '--', 'Red'),
        (20.0, 'Block', 'A', 'RepHold', 'Sparing'),
        (35.0, 'Block', 'A', 'Sparing', 'End_Post-LDT'),
        (35.0, 'Block', 'A', 'End_Post-LDT', 'Good'),
        (35.0, 'Node', 'end', 'Bad', 'Good'),
        (35.0, 'System', 'State_change', 'Red', 'Green'),
        (40.0, 'Block', 'A', 'Good', 'Done'),
        (40.0, 'System', 'Phase_change', '--', 'Mission_failure'),
        (40.0, 'System', 'Simulation_Terminate', '--', '--'),
    ])


# ---
# test_the_cost_section_writes_partial_repair_after_a_sparing_row_at_the_run_end


def test_the_cost_section_writes_partial_repair_after_a_sparing_row_at_the_run_end() -> None:
    """`Sparing -> Partial_Repair`: the cost section's own row, written at the
    end of a run that interrupts a repair.

    Why: `Repair` sets `RepWait` immediately before the repair's
    `WAIT DURATION TimeToRepair` and clears it only when that timer completes
    (`block_lifecycle.py:3603-3615`). `ChangeBlockState`'s cost branch writes
    `Partial_Repair RepairTime=...` on any transition away from `Repairing`
    while `RepWait` is still set -- including the run-end flush to `Done`
    (`blocks.py:169-185`, `accounting.py:79-98`). So the block's last row
    before the run ends is whatever raw row it wrote last, and here that is
    the Custom stock order placed when the repair consumed the block's only
    spare. `Partial_Repair` exists only with cost analysis on, so the fixture
    turns it on; every distribution is still `Fixed`.

    Setup: one block, Custom sparing with one unit and a 30-hour `Slotime`
    (so the batch has not arrived when the run ends), a 10-hour life and a
    100-hour repair. 20 sim hours, one replication.

    Expected: the repair starts at 10 and the order is placed in the same
    instant (`Repairing -> Sparing`); the run ends at 20, still inside
    the repair, so the cost section writes `Partial_Repair` and only
    then does the run-end flush write `Done`.

    Source: raptor/core/engine/block_lifecycle.py:3595-3665 (`RepWait` set
    and cleared) (the SLO re-order that writes the `Sparing` row),
    raptor/core/engine/blocks.py:169-185 and
    raptor/core/engine/accounting.py:79-98 (the `Partial_Repair` row);
    Raptor 7
    """
    nodes, links = _chain("A")
    result = run({
        "system": {"run_length": 20.0, "num_runs": 1,
                   "termination": "TimeSim", "cost_analysis": True},
        "blocks": [block("A", 10.0, 100.0, sp_type="Custom", Stock=1,
                          rso=1, slo=1, level=0, quan=1, slotime=30.0)],
        "nodes": nodes, "links": links,
    })

    _fixed(result)

    assert_timeline(result, [
        (0.0, 'Block', 'A', '--', 'Good'),
        (10.0, 'Block', 'A', 'Good', 'Failed'),
        (10.0, 'Block', 'A', 'Failed', 'Repairing'),
        (10.0, 'Node', 'end', '--', 'Bad'),
        (10.0, 'System', 'State_change', '--', 'Red'),
        (10.0, 'Block', 'A', 'Repairing', 'Sparing'),
        (20.0, 'Block', 'A', 'Sparing', 'Partial_Repair'),
        (20.0, 'Block', 'A', 'Partial_Repair', 'Done'),
        (20.0, 'System', 'Phase_change', '--', 'Mission_failure'),
        (20.0, 'System', 'Simulation_Terminate', '--', '--'),
    ])


# ---
# test_a_deferred_pm_on_a_cut_block_is_reactivated_by_the_next_boundary


def test_a_deferred_pm_on_a_cut_block_is_reactivated_by_the_next_boundary() -> None:
    """`PM_is_due -> Good`: a PM that came due while the block was `Cut` and
    was postponed, with the next phase boundary printing the operating status.

    Why: `DetermineNewState("PM is due")`'s `pmMisDefer` gate writes the
    `PM_is_due` row *before* it defers, then sets `deferPM` and hands back to
    `Run` (`block_lifecycle.py:2219-2250`). The row is a raw one, so it is
    logged whatever the block's active status is -- here the block is `Cut`,
    and `Cut -> PM_is_due` is the row before it. The next boundary takes the
    block's cell from `Cut` to `Active`, and `apply_block` calls
    `ChangeBlockState(block.op_status, ACTIVE, "Phase_Change")`
    (`cascade_phase.py:131-150`); because the new active status *is* `Active`,
    `change_block_state` prints the operating status, which is still `Running`
    -- so the row is `PM_is_due -> Good`.

    Setup: one block, a 45-hour `Fixed` PM frequency and `pmMisDefer` set,
    four 30-hour phases with the block's cells `Active, Cut, Active, Active`
    (every phase a mission, so the deferral gate is what defers it). 120 sim
    hours, one replication, every distribution `Fixed`.

    Expected: the block is `Cut` from 30; its PM comes due at 45 and is
    deferred, so its last row there is the raw `PM_is_due`; the boundary
    at 60 re-activates it and prints `Good`. Three rows, one mechanism.

    Source: raptor/core/engine/block_lifecycle.py:2197-2250 (the `pmMisDefer`
    defer gate and its `PM_is_due` row), raptor/core/engine/cascade_phase.py:
    131-164 (`apply_block`), raptor/core/engine/blocks.py:169-185 (the action
    string choice); Raptor 7
    """
    nodes, links = _chain("A")
    result = run({
        "system": {"run_length": 120.0, "num_runs": 1,
                   "termination": "TimeSim"},
        "blocks": [block("A", 500.0, 5.0, uses_pm=True, pm_freq=45.0,
                          pm_dist=FIXED, pm_params=[5.0], pm_mis_defer=True,
                          pm_refresh=False, pm_fail_reset=False)],
        "nodes": nodes, "links": links,
        "phases": [{"name": f"P{index + 1}", "mission": 1, "dist": FIXED,
                    "params": [30.0]} for index in range(4)],
        "phase_states": {"A": ["A: 1.000", "C: 1.000", "A: 1.000",
                               "A: 1.000"]},
    })

    _fixed(result)

    assert_timeline(result, [
        (0.0, 'System', 'State_change', '--', 'Mission'),
        (0.0, 'Block', 'A', '--', 'Good'),
        (30.0, 'System', 'Phase_change', '--', 'Phase_change'),
        (30.0, 'Block', 'A', 'Good', 'Cut'),
        (30.0, 'Node', 'end', '--', 'Bad'),
        (30.0, 'System', 'State_change', 'Mission', 'Red'),
        (45.0, 'Block', 'A', 'Cut', 'PM_is_due'),
        (60.0, 'System', 'Phase_change', 'Phase_change', 'Phase_change'),
        (60.0, 'Block', 'A', 'PM_is_due', 'Good'),
        (60.0, 'Node', 'end', 'Bad', 'Good'),
        (60.0, 'System', 'State_change', 'Red', 'Green'),
        (90.0, 'System', 'Phase_change', 'Phase_change', 'Phase_change'),
        (120.0, 'Block', 'A', 'Good', 'Done'),
        (120.0, 'System', 'Phase_change', 'Phase_change', 'Mission_failure'),
        (120.0, 'System', 'Simulation_Terminate', '--', '--'),
    ])


# ---
# test_a_dependency_idles_a_failed_block_between_its_failure_and_its_pre_ldt


def test_a_dependency_idles_a_failed_block_between_its_failure_and_its_pre_ldt() -> None:
    """`Idle -> RepHold`: the dependency cascade reaches a block that has
    already failed but not yet reached its pre-LDT hold.

    Why: two blocks fail in the same instant, `B` first; `A`'s failure then
    dispatches `BackFlowDependencies`, which puts its dependent `B` into
    `Idle` (`dependency.py:296-320`). `B` is on its own `TELL METHOD Repair`
    boundary by then, so its `CheckForSpare` has run without a state change
    and its next transition is `_pre_ldt`'s
    `_transition(REP_HOLD, ..., "LDT")` (`block_lifecycle.py:3486-3487`),
    which does not test the block's current status. So the block is `Idle` on
    paper and is put into `RepHold` anyway, and the log shows `Idle` then
    `RepHold`.

    Setup: `Start -> A, B -> Join -> End`, both with a 30-hour `Fixed` life and
    a 5-hour repair, `B` dependent on `A` and carrying a 4-hour `Fixed`
    pre-LDT. 40 sim hours, one replication, every distribution `Fixed`.

    Expected: both blocks fail at 30; `A`'s failure dispatches the
    dependence and `B` is `Idle`, and `B`'s own next transition is the
    pre-LDT hold, so the two rows are `Failed -> Idle -> RepHold` and
    `B` repairs at 39.

    Source: raptor/core/engine/block_lifecycle.py:3738-3796 (`_repair`'s
    `CheckForSpare` -> `_enter_repair` -> `_pre_ldt` order, and the TELL
    boundary between them) (`_pre_ldt`'s `RepHold` transition),
    raptor/core/engine/dependency.py:296-320 (`BackFlowDependencies`);
    """
    nodes, links = _chain("A", "B")
    result = run({
        "system": {"run_length": 40.0, "num_runs": 1,
                   "termination": "TimeSim"},
        "blocks": [block("A", 30.0, 5.0, id=1),
                   block("B", 30.0, 5.0, id=2, dep_type="RBDBlock",
                          dep_num=1, pre_ldt_dist=FIXED,
                          pre_ldt_params=[4.0])],
        "nodes": nodes, "links": links,
    })

    _fixed(result)

    assert_timeline(result, [
        (0.0, 'Block', 'A', '--', 'Good'),
        (0.0, 'Block', 'B', '--', 'Good'),
        (30.0, 'Block', 'A', 'Good', 'Failed'),
        (30.0, 'Block', 'B', 'Good', 'Failed'),
        (30.0, 'Block', 'A', 'Failed', 'Repairing'),
        (30.0, 'Block', 'B', 'Failed', 'Idle'),
        (30.0, 'Node', 'Join', '--', 'Degraded'),
        (30.0, 'Node', 'Join', 'Degraded', 'Idle'),
        (30.0, 'Node', 'end', '--', 'Idle'),
        (30.0, 'System', 'State_change', '--', 'Red'),
        (30.0, 'Block', 'B', 'Idle', 'RepHold'),
        (34.0, 'Block', 'B', 'RepHold', 'End_Pre-LDT'),
        (34.0, 'Block', 'B', 'End_Pre-LDT', 'Repairing'),
        (34.0, 'System', 'Red_change', '--', 'Red'),
        (35.0, 'Block', 'A', 'Repairing', 'Repaired'),
        (35.0, 'Block', 'A', 'Repaired', 'Good'),
        (35.0, 'Node', 'Join', 'Idle', 'Degraded'),
        (35.0, 'Node', 'end', 'Idle', 'Good'),
        (35.0, 'System', 'State_change', 'Red', 'Yellow'),
        (39.0, 'Block', 'B', 'Repairing', 'Repaired'),
        (39.0, 'Block', 'B', 'Repaired', 'Good'),
        (39.0, 'System', 'State_change', 'Yellow', 'Green'),
        (40.0, 'Block', 'A', 'Good', 'Done'),
        (40.0, 'Block', 'B', 'Good', 'Done'),
        (40.0, 'System', 'Phase_change', '--', 'Mission_failure'),
        (40.0, 'System', 'Simulation_Terminate', '--', '--'),
    ])


# ---
# test_a_misdefer_hands_the_resource_back_and_the_block_fails_afterwards


def test_a_misdefer_hands_the_resource_back_and_the_block_fails_afterwards() -> None:
    """`Resources -> Failed`: the `Returned_N_Resource(s)` row is the block's
    last row before its life runs out.

    Why: a `pmMisDefer` block whose PM resource request was deferred obtains
    the unit when the holder releases it, and -- if a mission phase has just
    started -- `ObtainPMprereqs`' postponement tail hands the resource
    straight back, writing `Resources Returned_N_Resource(s)`
    (`block_lifecycle.py:2713-2721`). The give-back restores the
    life the PM race had parked, so the block carries on running with only
    `fail_time_left` left; when that expires it logs `Failed` with no row of
    its own in between. The `Returned` row is therefore the measured `from`.

    Setup: `Start -> A, H -> Join -> End`. `A` is the deferred PM: `pmMisDefer`
    and `pmReqDefer` set, a PM spare from a `Pooled` pool that restocks every
    20 hours from empty, one PM resource from a one-unit pool `R`, a 10-hour
    `Fixed` PM frequency and a 95-hour life. `H` borrows `R` from 5 to 20 on a
    five-hour life and a 15-hour repair. Four 40-hour phases alternating
    mission / non-mission, so the grant at 80 lands inside a mission. 100 sim
    hours, one replication, every distribution `Fixed`.

    Expected: the PM at 40 goes ahead (a non-mission phase) and completes
    at 45; the PM at 55 defers on the resource, the grant at 80 lands
    inside the mission phase that starts then, so both `Resources`
    rows are in the same instant; and with the life restored the block
    fails at 95 with nothing of its own in between.

    Source: raptor/core/engine/block_lifecycle.py:2865-2972
    (`_request_pm_resource`'s `Obtained` row)
    (`_misdefer_give_back`'s `Returned` row and its life restore),
     (the mission postponement that calls it),
     (`_abandon_pm_attempt`); Raptor 7
    """
    result = run(_custom_pm_spec(100.0, 95.0, (5.0, 15.0), 20.0, 40.0))

    _fixed(result)

    assert_timeline(result, [
        (0.0, 'System', 'State_change', '--', 'Mission'),
        (0.0, 'Block', 'A', '--', 'Good'),
        (0.0, 'Block', 'H', '--', 'Good'),
        (5.0, 'Block', 'H', 'Good', 'Failed'),
        (5.0, 'Block', 'H', 'Failed', 'Repairing'),
        (5.0, 'Node', 'Join', '--', 'Degraded'),
        (5.0, 'System', 'State_change', 'Mission', 'Yellow'),
        (20.0, 'Pool', 'SP', '--', 'Sparing'),
        (20.0, 'Block', 'H', 'Repairing', 'Repaired'),
        (20.0, 'Block', 'H', 'Repaired', 'Good'),
        (20.0, 'Node', 'Join', 'Degraded', 'Good'),
        (20.0, 'Block', 'A', 'Good', 'PM_is_due'),
        (20.0, 'System', 'State_change', 'Yellow', 'Green'),
        (25.0, 'Block', 'H', 'Good', 'Failed'),
        (25.0, 'Block', 'H', 'Failed', 'Repairing'),
        (25.0, 'Node', 'Join', 'Good', 'Degraded'),
        (25.0, 'System', 'State_change', 'Green', 'Yellow'),
        (40.0, 'Pool', 'SP', 'Sparing', 'Sparing'),
        (40.0, 'Block', 'H', 'Repairing', 'Repaired'),
        (40.0, 'Block', 'H', 'Repaired', 'Good'),
        (40.0, 'Node', 'Join', 'Degraded', 'Good'),
        (40.0, 'System', 'Phase_change', '--', 'Phase_change'),
        (40.0, 'System', 'Phase_change', 'Phase_change', 'Mission_success'),
        (40.0, 'System', 'State_change', 'Yellow', 'Yellow'),
        (40.0, 'Block', 'A', 'PM_is_due', 'PM_is_due'),
        (40.0, 'Block', 'A', 'PM_is_due', 'PM'),
        (40.0, 'Node', 'Join', 'Good', 'Degraded'),
        (45.0, 'Block', 'H', 'Good', 'Failed'),
        (45.0, 'Block', 'A', 'PM', 'PM_complete'),
        (45.0, 'Block', 'A', 'PM_complete', 'Good'),
        (45.0, 'Node', 'Join', 'Degraded', 'Good'),
        (45.0, 'Block', 'H', 'Failed', 'Repairing'),
        (45.0, 'Node', 'Join', 'Good', 'Degraded'),
        (60.0, 'Pool', 'SP', 'Sparing', 'Sparing'),
        (60.0, 'Block', 'H', 'Repairing', 'Repaired'),
        (60.0, 'Block', 'H', 'Repaired', 'Good'),
        (60.0, 'Node', 'Join', 'Degraded', 'Good'),
        (60.0, 'System', 'State_change', 'Yellow', 'Green'),
        (65.0, 'Block', 'H', 'Good', 'Failed'),
        (65.0, 'Block', 'A', 'Good', 'PM_is_due'),
        (65.0, 'Block', 'A', 'PM_is_due', 'pmReqDefer'),
        (65.0, 'Block', 'H', 'Failed', 'Repairing'),
        (65.0, 'Node', 'Join', 'Good', 'Degraded'),
        (65.0, 'System', 'State_change', 'Green', 'Yellow'),
        (80.0, 'Pool', 'SP', 'Sparing', 'Sparing'),
        (80.0, 'Block', 'H', 'Repairing', 'Repaired'),
        (80.0, 'Block', 'H', 'Repaired', 'Good'),
        (80.0, 'Node', 'Join', 'Degraded', 'Good'),
        (80.0, 'System', 'Phase_change', 'Mission_success', 'Phase_change'),
        (80.0, 'Block', 'A', 'pmReqDefer', 'Resources'),
        (80.0, 'System', 'State_change', 'Yellow', 'Green'),
        (80.0, 'System', 'Phase_change', 'Phase_change', 'Mission'),
        (80.0, 'System', 'State_change', 'Green', 'Green'),
        (80.0, 'Block', 'A', 'Resources', 'Resources'),
        (85.0, 'Block', 'H', 'Good', 'Failed'),
        (85.0, 'Block', 'H', 'Failed', 'Repairing'),
        (85.0, 'Node', 'Join', 'Good', 'Degraded'),
        (85.0, 'System', 'State_change', 'Green', 'Yellow'),
        (100.0, 'Pool', 'SP', 'Sparing', 'Sparing'),
        (100.0, 'Block', 'A', 'Resources', 'Failed'),
        (100.0, 'Block', 'H', 'Repairing', 'Repaired'),
        (100.0, 'Block', 'H', 'Repaired', 'Good'),
        (100.0, 'Node', 'Join', 'Degraded', 'Good'),
        (100.0, 'Block', 'A', 'Failed', 'Repairing'),
        (100.0, 'Node', 'Join', 'Good', 'Degraded'),
        (100.0, 'Block', 'A', 'Repairing', 'Done'),
        (100.0, 'Block', 'H', 'Good', 'Done'),
        (100.0, 'System', 'Phase_change', 'Mission', 'Mission_success'),
        (100.0, 'System', 'Simulation_Terminate', '--', '--'),
    ])


# ---
# test_a_deferred_pm_is_released_at_the_next_non_mission_boundary


def test_a_deferred_pm_is_released_at_the_next_non_mission_boundary() -> None:
    """`Resources -> PM_is_due`: the `Returned_N_Resource(s)` row chained
    straight onto the release that restarts the postponed PM.

    Why: the same `pmMisDefer` hand-back as the sibling test, but with the
    postponed block's life long enough that the run reaches the *next*
    non-mission boundary. The phase machinery releases every member of
    `deferPM` there with a `Start_Deferred_PM` wake, the block's `Run` re-runs
    `DetermineNewState("PM is due")`, and the first thing it writes is the raw
    `PM_is_due` row -- measured from the `Returned` row that ended the
    previous attempt (`block_lifecycle.py:2224-2227`).

    Setup: as the sibling, with a 10-hour `Fixed` PM frequency, a 15-hour
    repair on `H`, 30-hour phases and a 95-hour life on `A`. The grant at 80
    is inside the mission phase that starts then, so the resource goes straight
    back, and the release follows at the boundary at 90. 100 sim hours, one
    replication, every distribution `Fixed`.

    Expected: the PM at 40 goes ahead; the one at 55 defers, the grant at
    80 is inside a mission so the resource goes straight back, and the
    release at the boundary at 90 writes `PM_is_due` from that
    `Returned` row. The run ends at 100 with the block still `Good`.

    Source: raptor/core/engine/block_lifecycle.py:2197-2250 (the deferral gate
    and its `PM_is_due` row) (`_misdefer_give_back`),
    raptor/core/engine/cascade_phase.py:280-300 (`Start_Deferred_PM`'s
    release); Raptor 7
    """
    result = run(_custom_pm_spec(100.0, 95.0, (5.0, 15.0), 10.0, 30.0))

    _fixed(result)

    assert_timeline(result, [
        (0.0, 'System', 'State_change', '--', 'Mission'),
        (0.0, 'Block', 'A', '--', 'Good'),
        (0.0, 'Block', 'H', '--', 'Good'),
        (5.0, 'Block', 'H', 'Good', 'Failed'),
        (5.0, 'Block', 'H', 'Failed', 'Repairing'),
        (5.0, 'Node', 'Join', '--', 'Degraded'),
        (5.0, 'System', 'State_change', 'Mission', 'Yellow'),
        (10.0, 'Block', 'A', 'Good', 'PM_is_due'),
        (20.0, 'Pool', 'SP', '--', 'Sparing'),
        (20.0, 'Block', 'H', 'Repairing', 'Repaired'),
        (20.0, 'Block', 'H', 'Repaired', 'Good'),
        (20.0, 'Node', 'Join', 'Degraded', 'Good'),
        (20.0, 'System', 'State_change', 'Yellow', 'Green'),
        (25.0, 'Block', 'H', 'Good', 'Failed'),
        (25.0, 'Block', 'H', 'Failed', 'Repairing'),
        (25.0, 'Node', 'Join', 'Good', 'Degraded'),
        (25.0, 'System', 'State_change', 'Green', 'Yellow'),
        (30.0, 'System', 'Phase_change', '--', 'Phase_change'),
        (30.0, 'System', 'Phase_change', 'Phase_change', 'Mission_success'),
        (30.0, 'System', 'State_change', 'Yellow', 'Yellow'),
        (30.0, 'Block', 'A', 'PM_is_due', 'PM_is_due'),
        (30.0, 'Block', 'A', 'PM_is_due', 'pmReqDefer'),
        (40.0, 'Pool', 'SP', 'Sparing', 'Sparing'),
        (40.0, 'Block', 'H', 'Repairing', 'Repaired'),
        (40.0, 'Block', 'H', 'Repaired', 'Good'),
        (40.0, 'Node', 'Join', 'Degraded', 'Good'),
        (40.0, 'Block', 'A', 'pmReqDefer', 'Resources'),
        (40.0, 'Block', 'A', 'Resources', 'PM'),
        (40.0, 'Node', 'Join', 'Good', 'Degraded'),
        (45.0, 'Block', 'H', 'Good', 'Failed'),
        (45.0, 'Block', 'A', 'PM', 'PM_complete'),
        (45.0, 'Block', 'A', 'PM_complete', 'Good'),
        (45.0, 'Node', 'Join', 'Degraded', 'Good'),
        (45.0, 'Block', 'H', 'Failed', 'Repairing'),
        (45.0, 'Node', 'Join', 'Good', 'Degraded'),
        (55.0, 'Block', 'A', 'Good', 'PM_is_due'),
        (55.0, 'Block', 'A', 'PM_is_due', 'pmReqDefer'),
        (60.0, 'Pool', 'SP', 'Sparing', 'Sparing'),
        (60.0, 'Block', 'H', 'Repairing', 'Repaired'),
        (60.0, 'Block', 'H', 'Repaired', 'Good'),
        (60.0, 'Node', 'Join', 'Degraded', 'Good'),
        (60.0, 'System', 'Phase_change', 'Mission_success', 'Phase_change'),
        (60.0, 'Block', 'A', 'pmReqDefer', 'Resources'),
        (60.0, 'System', 'State_change', 'Yellow', 'Green'),
        (60.0, 'System', 'Phase_change', 'Phase_change', 'Mission'),
        (60.0, 'System', 'State_change', 'Green', 'Green'),
        (60.0, 'Block', 'A', 'Resources', 'Resources'),
        (65.0, 'Block', 'H', 'Good', 'Failed'),
        (65.0, 'Block', 'H', 'Failed', 'Repairing'),
        (65.0, 'Node', 'Join', 'Good', 'Degraded'),
        (65.0, 'System', 'State_change', 'Green', 'Yellow'),
        (80.0, 'Pool', 'SP', 'Sparing', 'Sparing'),
        (80.0, 'Block', 'H', 'Repairing', 'Repaired'),
        (80.0, 'Block', 'H', 'Repaired', 'Good'),
        (80.0, 'Node', 'Join', 'Degraded', 'Good'),
        (80.0, 'System', 'State_change', 'Yellow', 'Green'),
        (85.0, 'Block', 'H', 'Good', 'Failed'),
        (85.0, 'Block', 'H', 'Failed', 'Repairing'),
        (85.0, 'Node', 'Join', 'Good', 'Degraded'),
        (85.0, 'System', 'State_change', 'Green', 'Yellow'),
        (90.0, 'System', 'Phase_change', 'Mission', 'Phase_change'),
        (90.0, 'System', 'Phase_change', 'Phase_change', 'Mission_success'),
        (90.0, 'System', 'State_change', 'Yellow', 'Yellow'),
        (90.0, 'Block', 'A', 'Resources', 'PM_is_due'),
        (90.0, 'Block', 'A', 'PM_is_due', 'pmReqDefer'),
        (100.0, 'Pool', 'SP', 'Sparing', 'Sparing'),
        (100.0, 'Block', 'H', 'Repairing', 'Repaired'),
        (100.0, 'Block', 'H', 'Repaired', 'Good'),
        (100.0, 'Node', 'Join', 'Degraded', 'Good'),
        (100.0, 'Block', 'A', 'pmReqDefer', 'Resources'),
        (100.0, 'Block', 'A', 'Resources', 'PM'),
        (100.0, 'Node', 'Join', 'Good', 'Degraded'),
        (100.0, 'Block', 'A', 'PM', 'Done'),
        (100.0, 'Block', 'H', 'Good', 'Done'),
        (100.0, 'System', 'Simulation_Terminate', '--', '--'),
    ])


# ---
# test_a_pm_holding_for_its_spare_takes_the_resource_when_it_arrives


def test_a_pm_holding_for_its_spare_takes_the_resource_when_it_arrives() -> None:
    """`Sparing -> Resources`: the spare the PM was holding for arrives, and
    the resource step that follows has to wait for its unit.

    Why: with `pmReqDefer` clear, an empty spare pool puts the block into
    `PMhold` and suspends it on `WAIT FOR ... TO Give`
    (`block_lifecycle.py:2489-2504`). The emergency order's arrival resumes
    that wait, and the resume writes `Sparing Spare_Arrived` as a raw row --
    so the block's last logged state becomes `Sparing`. `ObtainPMprereqs` then
    goes on to the resource step, where the one-unit pool is still held, and
    the eventual grant writes `Resources Obtained_N_Resource(s)`. Two raw rows
    and no state change between them.

    Setup: `Start -> A, H -> Join -> End`. `A` PMs every 20 hours, needs a PM
    spare from a `Pooled` pool with no routine or stock-level ordering and a
    40-hour emergency time, and one PM resource from a one-unit pool `R`.
    `H` holds `R` from 5 to 65. The order lands at 60, the resource at 65.
    90 sim hours, one replication, every distribution `Fixed`.

    Expected: the PM at 20 finds the pool empty and holds (`pmHold`); the
    order lands at 60 (`pmHold -> Sparing`); the resource is still held
    until 65, so the grant writes `Resources` from that row and the PM
    runs to 70.

    Source: raptor/core/engine/block_lifecycle.py:2489-2512 (`_obtain_pm_pool_
    spare`'s PMhold wait and its `Spare_Arrived` row)
    (`_request_pm_resource`'s `Obtained` row),
    raptor/core/engine/pool.py:499-537 (`order_a_spare_process`);
    """
    nodes, links = _chain("A", "H")
    result = run({
        "system": {"run_length": 90.0, "num_runs": 1,
                   "termination": "TimeSim"},
        "blocks": [
            block("A", 500.0, 5.0, id=1, uses_pm=True, pm_freq=20.0,
                   pm_dist=FIXED, pm_params=[5.0], pm_spare=True,
                   pm_req_defer=False, sp_type="Pooled", spare_pool="SP",
                   num_res=1, res1_pm_rep=1, resource_name="R",
                   pm_refresh=False, pm_fail_reset=False),
            block("H", 5.0, 60.0, id=2, num_res=1, resource_name="R"),
        ],
        "spare_pools": [{"name": "SP", "stock": 0, "new_spares": 1,
                         "rso": 0, "eso": 1, "emer_time": 40.0}],
        "resources": [{"name": "R", "num_in": 1}],
        "nodes": nodes, "links": links,
    })

    _fixed(result)

    assert_timeline(result, [
        (0.0, 'Block', 'A', '--', 'Good'),
        (0.0, 'Block', 'H', '--', 'Good'),
        (5.0, 'Block', 'H', 'Good', 'Failed'),
        (5.0, 'Block', 'H', 'Failed', 'Repairing'),
        (5.0, 'Node', 'Join', '--', 'Degraded'),
        (5.0, 'System', 'State_change', '--', 'Yellow'),
        (20.0, 'Block', 'A', 'Good', 'PM_is_due'),
        (20.0, 'Block', 'A', 'PM_is_due', 'pmHold'),
        (20.0, 'Node', 'Join', 'Degraded', 'NodePM'),
        (20.0, 'Node', 'end', '--', 'NodePM'),
        (20.0, 'System', 'State_change', 'Yellow', 'Red'),
        (20.0, 'Pool', 'SP', '--', 'Sparing'),
        (60.0, 'Pool', 'SP', 'Sparing', 'Sparing'),
        (60.0, 'Block', 'A', 'pmHold', 'Sparing'),
        (65.0, 'Block', 'H', 'Repairing', 'Repaired'),
        (65.0, 'Block', 'H', 'Repaired', 'Good'),
        (65.0, 'Node', 'Join', 'NodePM', 'Degraded'),
        (65.0, 'Node', 'end', 'NodePM', 'Good'),
        (65.0, 'Block', 'A', 'Sparing', 'Resources'),
        (65.0, 'Block', 'A', 'Resources', 'PM'),
        (65.0, 'System', 'State_change', 'Red', 'Yellow'),
        (70.0, 'Block', 'H', 'Good', 'Failed'),
        (70.0, 'Block', 'A', 'PM', 'PM_complete'),
        (70.0, 'Block', 'A', 'PM_complete', 'Good'),
        (70.0, 'Node', 'Join', 'Degraded', 'Good'),
        (70.0, 'Block', 'H', 'Failed', 'Repairing'),
        (70.0, 'Node', 'Join', 'Good', 'Degraded'),
        (90.0, 'Block', 'A', 'Good', 'PM_is_due'),
        (90.0, 'Block', 'A', 'PM_is_due', 'pmHold'),
        (90.0, 'Node', 'Join', 'Degraded', 'NodePM'),
        (90.0, 'Node', 'end', 'Good', 'NodePM'),
        (90.0, 'System', 'State_change', 'Yellow', 'Red'),
        (90.0, 'Block', 'A', 'pmHold', 'Done'),
        (90.0, 'Block', 'H', 'Repairing', 'Done'),
        (90.0, 'System', 'Phase_change', '--', 'Mission_failure'),
        (90.0, 'System', 'Simulation_Terminate', '--', '--'),
    ])


# ---
# test_a_deferred_pm_spare_is_credited_while_the_block_is_idle


def test_a_deferred_pm_spare_is_credited_while_the_block_is_idle() -> None:
    """`Sparing -> Good`: a deferred PM's spare is credited while the block is
    `Idle`, and the dependency that idled it is what the next row reports.

    Why: a `pmReqDefer` block that found the pool empty leaves its request
    queued (`block_lifecycle.py:2468-2480`), and the pool's routine restock
    settles it asynchronously through `_credit_deferred_pm_spare`, which
    writes `Sparing Spare_Arrived` (`block_lifecycle.py:734-764`). Nothing else
    is logged for the block on that pass: the pickup deliberately skips the
    `PM_is_due` row and the `ObtainPMprereqs` TELL boundary,
   so the block's `Run` stays parked and waits for
    its PM resource. The block's dependency target was down the whole time, so
    its recovery -- `ChangeBlockState(Running, ACTIVE, "")` on a `Dependence`
    dispatch -- is the next row, and the active status is `Active`, so it logs
    `Good`.

    Setup: `Start -> A, X, H -> Join -> End`. `A` PMs every 10 hours with
    `pmReqDefer` and `pmMisDefer`, needs a spare from a `Pooled` pool that
    restocks every 30 hours from empty, one PM resource from a one-unit pool,
    and depends on `X`. `X` has a 10-hour life and a 25-hour repair, so it is
    down from 10 to 35; `H` holds `R` from 5 to 45. The restock lands at 30
    while `A` is `Idle`, and `X` recovers at 35. 40 sim hours, one
    replication, every distribution `Fixed`.

    Expected: `X` fails at 10 in the same instant `A`'s PM defers, so `A`
    is `Idle` from 10; the restock at 30 credits the deferred spare
    (`Idle -> Sparing`); `X` repairs at 35 and `A`'s dependence
    recovers it (`Sparing -> Good`); the PM resource is granted at 45.
    The run ends at 40, so nothing after that row is asserted.

    Source: raptor/core/engine/block_lifecycle.py:734-764
    (`_credit_deferred_pm_spare`'s `Spare_Arrived` row) and
     (the pickup's skipped `PM_is_due` row and TELL hop),
     (the deferred spare request),
    raptor/core/engine/dependency.py:296-320 (the `Idle` dispatch and its
    recovery); Raptor 7
    """
    nodes, links = _chain("A", "X", "H")
    result = run({
        "system": {"run_length": 40.0, "num_runs": 1,
                   "termination": "TimeSim"},
        "blocks": [
            block("A", 500.0, 5.0, id=1, uses_pm=True, pm_freq=10.0,
                   pm_dist=FIXED, pm_params=[5.0], num_res=1, res1_pm_rep=1,
                   resource_name="R", pm_refresh=False, pm_fail_reset=False,
                   pm_spare=True, pm_mis_defer=True, pm_req_defer=True,
                   sp_type="Pooled", spare_pool="SP", dep_type="RBDBlock",
                   dep_num=2),
            block("X", 10.0, 25.0, id=2),
            block("H", 5.0, 40.0, id=3, num_res=1, resource_name="R"),
        ],
        "spare_pools": [{"name": "SP", "stock": 0, "new_spares": 1,
                         "arrive_every": 30.0, "rso": 1}],
        "resources": [{"name": "R", "num_in": 1}],
        "nodes": nodes, "links": links,
    })

    _fixed(result)

    assert_timeline(result, [
        (0.0, 'Block', 'A', '--', 'Good'),
        (0.0, 'Block', 'X', '--', 'Good'),
        (0.0, 'Block', 'H', '--', 'Good'),
        (5.0, 'Block', 'H', 'Good', 'Failed'),
        (5.0, 'Block', 'H', 'Failed', 'Repairing'),
        (5.0, 'Node', 'Join', '--', 'Degraded'),
        (5.0, 'System', 'State_change', '--', 'Yellow'),
        (10.0, 'Block', 'A', 'Good', 'PM_is_due'),
        (10.0, 'Block', 'X', 'Good', 'Failed'),
        (10.0, 'Block', 'A', 'PM_is_due', 'pmReqDefer'),
        (10.0, 'Block', 'X', 'Failed', 'Repairing'),
        (10.0, 'Block', 'A', 'pmReqDefer', 'Idle'),
        (10.0, 'Node', 'Join', 'Degraded', 'Degraded'),
        (10.0, 'Node', 'Join', 'Degraded', 'Idle'),
        (10.0, 'Node', 'end', '--', 'Idle'),
        (10.0, 'System', 'State_change', 'Yellow', 'Red'),
        (30.0, 'Pool', 'SP', '--', 'Sparing'),
        (30.0, 'Block', 'A', 'Idle', 'Sparing'),
        (35.0, 'Block', 'X', 'Repairing', 'Repaired'),
        (35.0, 'Block', 'X', 'Repaired', 'Good'),
        (35.0, 'Block', 'A', 'Sparing', 'Good'),
        (35.0, 'Node', 'Join', 'Idle', 'Degraded'),
        (35.0, 'Node', 'end', 'Idle', 'Good'),
        (35.0, 'Node', 'Join', 'Degraded', 'Degraded'),
        (35.0, 'System', 'State_change', 'Red', 'Yellow'),
        (40.0, 'Block', 'A', 'Good', 'Done'),
        (40.0, 'Block', 'X', 'Good', 'Done'),
        (40.0, 'Block', 'H', 'Repairing', 'Done'),
        (40.0, 'System', 'Phase_change', '--', 'Mission_failure'),
        (40.0, 'System', 'Simulation_Terminate', '--', '--'),
    ])


# ---
# test_a_cold_groups_linked_member_is_standby_when_a_boundary_reactivates_it


def test_a_cold_groups_linked_member_is_standby_when_a_boundary_reactivates_it() -> None:
    """`Linked -> Standby`: the previous agent's "a `Linked` block never has an
    `Active` active status" is true of the *demotion*, and not of the row.

    Why: at the `Cut -> Linked` boundary `apply_block` calls
    `ChangeBlockState(op_status, LINKED, "Phase_Change")`, which leaves
    `op_status` alone (`cascade_phase.py:131-150`). That takes `A`'s cold link
    out of `LinkUp`, and `ReconfigureNode`'s cold arm puts a still-`Running`
    source block into `Standby` with its *unchanged* active status
    (`orchestrator.py:771-783`) -- so the demotion logs `Linked`, and the
    block is now `Standby` with an active status of `Linked`. The next
    boundary takes the cell `Linked -> Active`; `apply_block` again keeps
    `op_status`, and because the new active status *is* `Active`,
    `change_block_state` prints the operating status (`blocks.py:169-185`),
    which is `Standby`. The row is `Linked -> Standby`.

    The `StessLevel` row a switch decision normally writes is what usually
    breaks the chain -- it lands between the two -- so the fixture gives both
    blocks a standby stress of 1.0, matching their running stress, and the
    survey has nothing to report.

    Setup: `Start -> A, B -> Cold -> Join`, `B -> Join`, with `Cold` a 1-of-2
    cold-standby group (`k` and `k*` both 1). Both blocks have a 500-hour life
    and `sb_stress=1.0`; `A` is phased `Cut, Linked, Active` in 50-hour phases.
    150 sim hours, one replication, every distribution `Fixed`.

    Expected: `A` is `Cut` from 0; the boundary at 50 links it and the cold
    arm demotes the still-`Running` block to `Standby` without changing
    its active status, so that row also reads `Linked`; the boundary at
    100 re-activates it and prints the operating status, `Standby`.
    No `StessLevel` row appears, because both blocks' standby stress
    equals their running stress.

    Source: raptor/core/engine/cascade_phase.py:131-164 (`apply_block`),
    raptor/core/engine/nodes.py:448-453 (`on_cold_link_down`),
    raptor/core/engine/orchestrator.py:771-783 (`_demote_running_cold_source`),
    raptor/core/engine/blocks.py:169-185 (the action string choice),
    raptor/core/engine/cold_standby.py:609-674 (`Too_many_on` / `StessLevel`);
    """
    result = run({
        "system": {"run_length": 150.0, "num_runs": 1,
                   "termination": "TimeSim"},
        "blocks": [block("A", 500.0, 5.0, id=1, sb_stress=1.0),
                   block("B", 500.0, 5.0, id=2, sb_stress=1.0)],
        "nodes": [
            {"name": "Start", "type": 1, "n": 0, "out": 2},
            {"name": "Cold", "type": 2, "n": 2, "out": 1, "k": 1,
             "k_star": 1, "cold": 1},
            {"name": "Join", "type": 2, "n": 2, "out": 1, "k": 1},
            {"name": "End", "type": 3, "n": 1, "out": 0},
        ],
        "links": [{"from": "Start", "to": "A"}, {"from": "Start", "to": "B"},
                  {"from": "A", "to": "Cold", "cold_pri": 25, "a_prob": 1.0,
                   "auto_time": 0.0, "man_time": -1.0},
                  {"from": "B", "to": "Cold", "cold_pri": 50, "a_prob": 1.0,
                   "auto_time": 0.0, "man_time": -1.0},
                  {"from": "Cold", "to": "Join"},
                  {"from": "B", "to": "Join"},
                  {"from": "Join", "to": "End"}],
        "phases": [{"name": f"P{index + 1}", "mission": 1, "dist": FIXED,
                    "params": [50.0]} for index in range(3)],
        "phase_states": {"A": ["C: 1.000", "L: 1.000", "A: 1.000"],
                         "B": ["A: 1.000"] * 3},
    })

    _fixed(result)

    assert_timeline(result, [
        (0.0, 'System', 'State_change', '--', 'Mission'),
        (0.0, 'Block', 'A', '--', 'Cut'),
        (0.0, 'Block', 'A', 'Cut', 'Cut'),
        (0.0, 'Block', 'B', '--', 'Good'),
        (0.0, 'Node', 'Cold', '--', 'Degraded'),
        (50.0, 'System', 'Phase_change', '--', 'Phase_change'),
        (50.0, 'Block', 'A', 'Cut', 'Linked'),
        (50.0, 'Node', 'Cold', 'Degraded', 'Good'),
        (100.0, 'System', 'Phase_change', 'Phase_change', 'Phase_change'),
        (100.0, 'Block', 'A', 'Linked', 'Standby'),
        (100.0, 'Node', 'Cold', 'Good', 'Good'),
        (150.0, 'Block', 'A', 'Standby', 'Done'),
        (150.0, 'Block', 'B', 'Good', 'Done'),
        (150.0, 'System', 'Phase_change', 'Phase_change', 'Mission_success'),
        (150.0, 'System', 'Simulation_Terminate', '--', '--'),
    ])


# ---
# test_a_too_many_on_demotion_lands_between_a_deferral_and_its_grant


def test_a_too_many_on_demotion_lands_between_a_deferral_and_its_grant() -> None:
    """`pmReqDefer -> Standby` and `Standby -> Resources`: a cold group's
    `Too_many_on` demotion lands between a PM deferral and the grant that
    answers it.

    Why: `P`'s PM defers at 32 because the one-unit resource pool is held, and
    writes the raw `pmReqDefer No_resource_in_stock-R` row
    (`block_lifecycle.py:2917-2921`). At 35 `A` finishes its repair, its cold
    link returns to `LinkUp`, and the survey now has two ready paths for a
    1-of-1 group, so it demotes `P` -- `TransitionFn` passes the block's own
    active status, which is `Active`, so the row really is `Standby`
    (`cascade_cold.py:37-44`, `cold_standby.py:609-674`). At 40 `H` releases
    `R`; the deferred request is granted and `_request_pm_resource` writes
    `Resources Obtained_1_Resource(s)` from whichever wait `P` is parked on.
    So the `Standby` row sits between the deferral and the
    grant, and both rows the corpus records are there.

    Setup: `Start -> A, P, H -> Cold -> End`, `Cold` a 1-of-1 cold-standby
    group. `A` has a 10-hour life and a 25-hour repair; `P` PMs every 15 hours
    with `pmReqDefer` and one PM resource from `R`; `H` holds `R` from 5 to
    25 and again from 30 to 50. All three have `sb_stress=1.0` so the switch
    decisions write no `StessLevel` row. 60 sim hours, one replication, every
    distribution `Fixed`.

    Expected: `P`'s PM at 32 defers on `R` (`PM_is_due -> pmReqDefer`); `A`
    repairs at 35 and the 1-of-1 survey demotes `P`
    (`pmReqDefer -> Standby`); `H` releases `R` at 40 and the deferred
    request is granted (`Standby -> Resources -> PM`).

    Source: raptor/core/engine/block_lifecycle.py:2865-2944
    (`_request_pm_resource`'s deferral row) (its `Obtained` row
    on the grant), raptor/core/engine/cascade_cold.py:37-44 and
    raptor/core/engine/cold_standby.py:609-674 (the `Too_many_on` demotion and
    the active status it passes); Raptor 7
    """
    result = run({
        "system": {"run_length": 60.0, "num_runs": 1,
                   "termination": "TimeSim"},
        "blocks": [
            block("A", 10.0, 25.0, id=1, sb_stress=1.0),
            block("P", 500.0, 5.0, id=2, sb_stress=1.0, uses_pm=True,
                   pm_freq=15.0, pm_dist=FIXED, pm_params=[2.0],
                   pm_req_defer=True, num_res=1, res1_pm_rep=1,
                   resource_name="R", pm_refresh=False, pm_fail_reset=False),
            block("H", 5.0, 20.0, id=3, num_res=1, resource_name="R"),
        ],
        "resources": [{"name": "R", "num_in": 1}],
        "nodes": [
            {"name": "Start", "type": 1, "n": 0, "out": 3},
            {"name": "Cold", "type": 2, "n": 3, "out": 1, "k": 1,
             "k_star": 1, "cold": 1},
            {"name": "End", "type": 3, "n": 1, "out": 0},
        ],
        "links": [{"from": "Start", "to": "A"}, {"from": "Start", "to": "P"},
                  {"from": "Start", "to": "H"},
                  {"from": "A", "to": "Cold", "cold_pri": 25, "a_prob": 1.0,
                   "auto_time": 0.0, "man_time": -1.0},
                  {"from": "P", "to": "Cold", "cold_pri": 50, "a_prob": 1.0,
                   "auto_time": 0.0, "man_time": -1.0},
                  {"from": "H", "to": "Cold"},
                  {"from": "Cold", "to": "End"}],
    })

    _fixed(result)

    assert_timeline(result, [
        (0.0, 'Block', 'A', '--', 'Good'),
        (0.0, 'Block', 'P', '--', 'Good'),
        (0.0, 'Block', 'H', '--', 'Good'),
        (0.0, 'Block', 'P', 'Good', 'Standby'),
        (0.0, 'Block', 'H', 'Good', 'Standby'),
        (0.0, 'Node', 'Cold', '--', 'Good'),
        (10.0, 'Block', 'A', 'Good', 'Failed'),
        (10.0, 'Block', 'A', 'Failed', 'Repairing'),
        (10.0, 'Block', 'P', 'Standby', 'Good'),
        (10.0, 'Node', 'Cold', 'Good', 'Degraded'),
        (10.0, 'System', 'State_change', '--', 'Yellow'),
        (15.0, 'Block', 'P', 'Good', 'PM_is_due'),
        (15.0, 'Block', 'P', 'PM_is_due', 'PM'),
        (15.0, 'Block', 'H', 'Standby', 'Good'),
        (15.0, 'Node', 'Cold', 'Degraded', 'Degraded'),
        (17.0, 'Block', 'P', 'PM', 'PM_complete'),
        (17.0, 'Block', 'P', 'PM_complete', 'Good'),
        (17.0, 'Block', 'P', 'Good', 'Standby'),
        (17.0, 'Node', 'Cold', 'Degraded', 'Degraded'),
        (20.0, 'Block', 'H', 'Good', 'Failed'),
        (20.0, 'Block', 'H', 'Failed', 'Repairing'),
        (20.0, 'Block', 'P', 'Standby', 'Good'),
        (20.0, 'Node', 'Cold', 'Degraded', 'Degraded'),
        (32.0, 'Block', 'P', 'Good', 'PM_is_due'),
        (32.0, 'Block', 'P', 'PM_is_due', 'pmReqDefer'),
        (35.0, 'Block', 'A', 'Repairing', 'Repaired'),
        (35.0, 'Block', 'A', 'Repaired', 'Good'),
        (35.0, 'Block', 'P', 'pmReqDefer', 'Standby'),
        (35.0, 'Node', 'Cold', 'Degraded', 'Degraded'),
        (40.0, 'Block', 'H', 'Repairing', 'Repaired'),
        (40.0, 'Block', 'H', 'Repaired', 'Good'),
        (40.0, 'Block', 'H', 'Good', 'Standby'),
        (40.0, 'Node', 'Cold', 'Degraded', 'Good'),
        (40.0, 'Block', 'P', 'Standby', 'Resources'),
        (40.0, 'Block', 'P', 'Resources', 'PM'),
        (40.0, 'Node', 'Cold', 'Good', 'Degraded'),
        (42.0, 'Block', 'P', 'PM', 'PM_complete'),
        (42.0, 'Block', 'P', 'PM_complete', 'Good'),
        (42.0, 'Block', 'P', 'Good', 'Standby'),
        (42.0, 'Node', 'Cold', 'Degraded', 'Good'),
        (42.0, 'System', 'State_change', 'Yellow', 'Green'),
        (45.0, 'Block', 'A', 'Good', 'Failed'),
        (45.0, 'Block', 'A', 'Failed', 'Repairing'),
        (45.0, 'Block', 'P', 'Standby', 'Good'),
        (45.0, 'Node', 'Cold', 'Good', 'Degraded'),
        (45.0, 'System', 'State_change', 'Green', 'Yellow'),
        (57.0, 'Block', 'P', 'Good', 'PM_is_due'),
        (57.0, 'Block', 'P', 'PM_is_due', 'PM'),
        (57.0, 'Block', 'H', 'Standby', 'Good'),
        (57.0, 'Node', 'Cold', 'Degraded', 'Degraded'),
        (59.0, 'Block', 'P', 'PM', 'PM_complete'),
        (59.0, 'Block', 'P', 'PM_complete', 'Good'),
        (59.0, 'Block', 'P', 'Good', 'Standby'),
        (59.0, 'Node', 'Cold', 'Degraded', 'Degraded'),
        (60.0, 'Block', 'A', 'Repairing', 'Done'),
        (60.0, 'Block', 'P', 'Standby', 'Done'),
        (60.0, 'Block', 'H', 'Good', 'Done'),
        (60.0, 'System', 'Phase_change', '--', 'Mission_success'),
        (60.0, 'System', 'Simulation_Terminate', '--', '--'),
    ])


# ---
# test_a_dependency_idles_a_block_between_its_pm_due_and_its_deferral


def test_a_dependency_idles_a_block_between_its_pm_due_and_its_deferral() -> None:
    """`Idle -> pmReqDefer`: the dependency cascade reaches a block whose PM
    prerequisites are already being acquired.

    Why: the row needs the block to be `Idle` *between* its raw `PM_is_due`
    row and its resource request, and both events have to land in the same
    sim instant for that window to exist. At 60 `A`'s scheduled PM comes due
    and `X` fails; `A` writes `PM_is_due` and then pays `ObtainPMprereqs`'
    TELL-method boundary (`block_lifecycle.py:2255-2292`). Inside that window
    `X`'s failure dispatches `BackFlowDependencies`, which puts its dependent
    `A` into `Idle` (`dependency.py:296-320`); the dispatch only skips the
    *wake*, not the state change, because `A` is mid-`ObtainPMprereqs` rather
    than in a `FailWait`/`ZeroWait` -- `waiting_for_pm_prerequisites` gates the
    interrupt, not the `ChangeBlockState`. `A` then goes on to the resource
    step, finds the one-unit pool held, and writes the raw `pmReqDefer
    No_resource_in_stock-R` row -- which is therefore measured
    from `Idle`. At 20 the same PM comes due with `X` still up, so that pass
    defers in the other order (`pmReqDefer -> Idle`) and shows the two rows
    the other way round; the contrast is the point.

    Setup: `Start -> A, X, H -> Join -> End`. `A` PMs every 20 hours with
    `pmReqDefer` and one PM resource from a one-unit pool `R`, and depends on
    `X`; `X` has a 25-hour life and a 10-hour repair, so it fails at 25, 60 and
    95; `H` holds `R` from 5 to 35 and again for 30 hours at a time. 120 sim
    hours, one replication, every distribution `Fixed`.

    Expected: the PM at 20 defers from `Running` (`pmReqDefer -> Idle`); the
    one at 60 coincides with `X`'s failure, so `A` is idled mid-prerequisites
    and the deferral row reads `Idle -> pmReqDefer`; the resource is granted
    at 70 and the PM runs to 75. The 95 pass repeats the 60 shape.

    Source: raptor/core/engine/block_lifecycle.py:2251-2292 (the `PM_is_due`
    row and the `ObtainPMprereqs` TELL boundary, and the `waiting_for_pm_prerequisites`
    gate) (`_request_pm_resource`'s deferral row),
    raptor/core/engine/cascade_dependency.py:36-80 (the dispatch that
    applies the state change but skips the wake);
    """
    nodes, links = _chain("A", "X", "H")
    result = run({
        "system": {"run_length": 120.0, "num_runs": 1,
                   "termination": "TimeSim"},
        "blocks": [
            block("A", 500.0, 5.0, id=1, uses_pm=True, pm_freq=20.0,
                   pm_dist=FIXED, pm_params=[5.0], pm_req_defer=True,
                   num_res=1, res1_pm_rep=1, resource_name="R",
                   pm_refresh=False, pm_fail_reset=False,
                   dep_type="RBDBlock", dep_num=2),
            block("X", 25.0, 10.0, id=2),
            block("H", 5.0, 30.0, id=3, num_res=1, resource_name="R"),
        ],
        "resources": [{"name": "R", "num_in": 1}],
        "nodes": nodes, "links": links,
    })

    _fixed(result)

    assert_timeline(result, [
        (0.0, 'Block', 'A', '--', 'Good'),
        (0.0, 'Block', 'X', '--', 'Good'),
        (0.0, 'Block', 'H', '--', 'Good'),
        (5.0, 'Block', 'H', 'Good', 'Failed'),
        (5.0, 'Block', 'H', 'Failed', 'Repairing'),
        (5.0, 'Node', 'Join', '--', 'Degraded'),
        (5.0, 'System', 'State_change', '--', 'Yellow'),
        (20.0, 'Block', 'A', 'Good', 'PM_is_due'),
        (20.0, 'Block', 'A', 'PM_is_due', 'pmReqDefer'),
        (25.0, 'Block', 'X', 'Good', 'Failed'),
        (25.0, 'Block', 'X', 'Failed', 'Repairing'),
        (25.0, 'Block', 'A', 'pmReqDefer', 'Idle'),
        (25.0, 'Node', 'Join', 'Degraded', 'Degraded'),
        (25.0, 'Node', 'Join', 'Degraded', 'Idle'),
        (25.0, 'Node', 'end', '--', 'Idle'),
        (25.0, 'System', 'State_change', 'Yellow', 'Red'),
        (35.0, 'Block', 'H', 'Repairing', 'Repaired'),
        (35.0, 'Block', 'H', 'Repaired', 'Good'),
        (35.0, 'Node', 'Join', 'Idle', 'Degraded'),
        (35.0, 'Node', 'end', 'Idle', 'Good'),
        (35.0, 'Block', 'X', 'Repairing', 'Repaired'),
        (35.0, 'Block', 'X', 'Repaired', 'Good'),
        (35.0, 'Block', 'A', 'Idle', 'Good'),
        (35.0, 'Node', 'Join', 'Degraded', 'Degraded'),
        (35.0, 'Node', 'Join', 'Degraded', 'Good'),
        (35.0, 'Block', 'A', 'Good', 'Resources'),
        (35.0, 'Block', 'A', 'Resources', 'PM'),
        (35.0, 'Node', 'Join', 'Good', 'Degraded'),
        (35.0, 'System', 'State_change', 'Red', 'Yellow'),
        (40.0, 'Block', 'H', 'Good', 'Failed'),
        (40.0, 'Block', 'A', 'PM', 'PM_complete'),
        (40.0, 'Block', 'A', 'PM_complete', 'Good'),
        (40.0, 'Node', 'Join', 'Degraded', 'Good'),
        (40.0, 'Block', 'H', 'Failed', 'Repairing'),
        (40.0, 'Node', 'Join', 'Good', 'Degraded'),
        (60.0, 'Block', 'X', 'Good', 'Failed'),
        (60.0, 'Block', 'A', 'Good', 'PM_is_due'),
        (60.0, 'Block', 'X', 'Failed', 'Repairing'),
        (60.0, 'Block', 'A', 'PM_is_due', 'Idle'),
        (60.0, 'Node', 'Join', 'Degraded', 'Degraded'),
        (60.0, 'Node', 'Join', 'Degraded', 'Idle'),
        (60.0, 'Node', 'end', 'Good', 'Idle'),
        (60.0, 'System', 'State_change', 'Yellow', 'Red'),
        (60.0, 'Block', 'A', 'Idle', 'pmReqDefer'),
        (70.0, 'Block', 'H', 'Repairing', 'Repaired'),
        (70.0, 'Block', 'H', 'Repaired', 'Good'),
        (70.0, 'Node', 'Join', 'Idle', 'Degraded'),
        (70.0, 'Node', 'end', 'Idle', 'Good'),
        (70.0, 'Block', 'X', 'Repairing', 'Repaired'),
        (70.0, 'Block', 'X', 'Repaired', 'Good'),
        (70.0, 'Block', 'A', 'pmReqDefer', 'Good'),
        (70.0, 'Node', 'Join', 'Degraded', 'Degraded'),
        (70.0, 'Node', 'Join', 'Degraded', 'Good'),
        (70.0, 'Block', 'A', 'Good', 'Resources'),
        (70.0, 'Block', 'A', 'Resources', 'PM'),
        (70.0, 'Node', 'Join', 'Good', 'Degraded'),
        (70.0, 'System', 'State_change', 'Red', 'Yellow'),
        (75.0, 'Block', 'H', 'Good', 'Failed'),
        (75.0, 'Block', 'A', 'PM', 'PM_complete'),
        (75.0, 'Block', 'A', 'PM_complete', 'Good'),
        (75.0, 'Node', 'Join', 'Degraded', 'Good'),
        (75.0, 'Block', 'H', 'Failed', 'Repairing'),
        (75.0, 'Node', 'Join', 'Good', 'Degraded'),
        (95.0, 'Block', 'X', 'Good', 'Failed'),
        (95.0, 'Block', 'A', 'Good', 'PM_is_due'),
        (95.0, 'Block', 'X', 'Failed', 'Repairing'),
        (95.0, 'Block', 'A', 'PM_is_due', 'Idle'),
        (95.0, 'Node', 'Join', 'Degraded', 'Degraded'),
        (95.0, 'Node', 'Join', 'Degraded', 'Idle'),
        (95.0, 'Node', 'end', 'Good', 'Idle'),
        (95.0, 'System', 'State_change', 'Yellow', 'Red'),
        (95.0, 'Block', 'A', 'Idle', 'pmReqDefer'),
        (105.0, 'Block', 'H', 'Repairing', 'Repaired'),
        (105.0, 'Block', 'H', 'Repaired', 'Good'),
        (105.0, 'Node', 'Join', 'Idle', 'Degraded'),
        (105.0, 'Node', 'end', 'Idle', 'Good'),
        (105.0, 'Block', 'X', 'Repairing', 'Repaired'),
        (105.0, 'Block', 'X', 'Repaired', 'Good'),
        (105.0, 'Block', 'A', 'pmReqDefer', 'Good'),
        (105.0, 'Node', 'Join', 'Degraded', 'Degraded'),
        (105.0, 'Node', 'Join', 'Degraded', 'Good'),
        (105.0, 'Block', 'A', 'Good', 'Resources'),
        (105.0, 'Block', 'A', 'Resources', 'PM'),
        (105.0, 'Node', 'Join', 'Good', 'Degraded'),
        (105.0, 'System', 'State_change', 'Red', 'Yellow'),
        (110.0, 'Block', 'H', 'Good', 'Failed'),
        (110.0, 'Block', 'A', 'PM', 'PM_complete'),
        (110.0, 'Block', 'A', 'PM_complete', 'Good'),
        (110.0, 'Node', 'Join', 'Degraded', 'Good'),
        (110.0, 'Block', 'H', 'Failed', 'Repairing'),
        (110.0, 'Node', 'Join', 'Good', 'Degraded'),
        (120.0, 'Block', 'A', 'Good', 'Done'),
        (120.0, 'Block', 'X', 'Good', 'Done'),
        (120.0, 'Block', 'H', 'Repairing', 'Done'),
        (120.0, 'System', 'Phase_change', '--', 'Mission_failure'),
        (120.0, 'System', 'Simulation_Terminate', '--', '--'),
    ])


# ---
# test_a_failed_switch_is_followed_by_the_promotion_that_re_aggregates_the_node


def test_a_failed_switch_is_followed_by_the_promotion_that_re_aggregates_the_node() -> None:
    """`Switch_Failed -> Degraded`: the survey's own failure row, then the
    promotion that re-aggregates the node.

    Why: `RunColdLogic`'s "enough paths" pass walks the cold links in priority
    order and, for a `LinkStandby` whose `switch_status` is `"none"` with no
    slot left for it, writes `Node <node> Switch_Failed
    Unable_to_switch_to_<block>` and marks the link `DONE`
    (`cold_standby.py:675-753`). The next candidate in the same
    pass *is* promoted (`Prior=Standby`), and that promotion takes its link
    back to `LinkUp`, which re-aggregates the node: with one path up, one down
    and one `DONE` against `k=1`, `determine_node_status` returns `Degraded`
    (`nodes.py:255-314`). So the node's own row immediately follows the
    `Switch_Failed` row.

    The `"none"` status needs `AProb` below 1 with `ManTime=-1`, which is
    `a_prob=0.0` on `B`'s link -- `set_switch_time` resolves it without a draw
    (`cold_standby.py:90-127`), so the run still consumes no randomness.

    Setup: `Start -> A, B, C -> Cold -> End`, `Cold` a 1-of-1 cold-standby
    group. `B`'s cold link has `a_prob=0.0`; `A` and `C` have `a_prob=1.0`, so
    `B` is the demoted one. `A` has a 30-hour life and a 200-hour repair, `B`
    50 and 200, `C` a 500-hour life. 200 sim hours, one replication, every
    distribution `Fixed`.

    Expected: the survey at 0 demotes `B` (its `AProb` is 0, so its link's
    `switch_status` is `"none"`); `A` fails at 30, which leaves the
    group with no usable path, so the survey writes `Switch_Failed` for
    `B` and then promotes `C` -- and that promotion's link change is
    what re-aggregates the node as `Degraded`.

    Source: raptor/core/engine/cold_standby.py:675-753 (the promotion pass and
    its `Switch_Failed` arm) (`_switch_failed`'s row and its
    `ChangeLinkStatus(LinkDone)`) (`set_switch_time`),
    raptor/core/engine/nodes.py:255-314 (`determine_node_status`);
    """
    result = run({
        "system": {"run_length": 200.0, "num_runs": 1,
                   "termination": "TimeSim"},
        "blocks": [block("A", 30.0, 200.0, id=1, sb_stress=0.5),
                   block("B", 50.0, 200.0, id=2, sb_stress=0.5),
                   block("C", 500.0, 5.0, id=3, sb_stress=0.5)],
        "nodes": [
            {"name": "Start", "type": 1, "n": 0, "out": 3},
            {"name": "Cold", "type": 2, "n": 3, "out": 1, "k": 1,
             "k_star": 1, "cold": 1},
            {"name": "End", "type": 3, "n": 1, "out": 0},
        ],
        "links": [{"from": "Start", "to": name}
                  for name in ("A", "B", "C")] + [
            {"from": "A", "to": "Cold", "cold_pri": 25, "a_prob": 1.0,
             "auto_time": 0.0, "man_time": -1.0},
            {"from": "B", "to": "Cold", "cold_pri": 50, "a_prob": 0.0,
             "auto_time": 0.0, "man_time": -1.0},
            {"from": "C", "to": "Cold", "cold_pri": 75, "a_prob": 1.0,
             "auto_time": 0.0, "man_time": -1.0},
            {"from": "Cold", "to": "End"}],
    })

    _fixed(result)

    assert_timeline(result, [
        (0.0, 'Block', 'A', '--', 'Good'),
        (0.0, 'Block', 'B', '--', 'Good'),
        (0.0, 'Block', 'C', '--', 'Good'),
        (0.0, 'Block', 'B', 'Good', 'Standby'),
        (0.0, 'Block', 'C', 'Good', 'Standby'),
        (0.0, 'Node', 'Cold', '--', 'Good'),
        (0.0, 'Block', 'B', 'Standby', 'StessLevel'),
        (0.0, 'Block', 'C', 'Standby', 'StessLevel'),
        (30.0, 'Block', 'A', 'Good', 'Failed'),
        (30.0, 'Block', 'A', 'Failed', 'Repairing'),
        (30.0, 'Node', 'Cold', 'Good', 'Switch_Failed'),
        (30.0, 'Block', 'C', 'StessLevel', 'Good'),
        (30.0, 'Node', 'Cold', 'Switch_Failed', 'Degraded'),
        (30.0, 'System', 'State_change', '--', 'Yellow'),
        (100.0, 'Block', 'B', 'StessLevel', 'Failed'),
        (100.0, 'Block', 'B', 'Failed', 'Repairing'),
        (100.0, 'Node', 'Cold', 'Degraded', 'Degraded'),
        (200.0, 'Block', 'A', 'Repairing', 'Done'),
        (200.0, 'Block', 'B', 'Repairing', 'Done'),
        (200.0, 'Block', 'C', 'Good', 'Done'),
        (200.0, 'System', 'Phase_change', '--', 'Mission_success'),
        (200.0, 'System', 'Simulation_Terminate', '--', '--'),
    ])


# ---
# test_a_system_that_is_up_at_its_first_mission_end_goes_green


def test_a_system_that_is_up_at_its_first_mission_end_goes_green() -> None:
    """`System State_change Mission -> Green`: the first colour row of a phased run whose system never failed.

    Why: a phased run opens with `State_change Mission` (the mission began), and the system's colour is not
    written until something sets it. The first thing that does is the end of the first mission phase:
    `ConductPhaseChange` closes the mission (`Mission_success`) and the status change that follows is written
    as `Green`, from the `Mission` row that opened the run. `IsolateCold`, a real extract run in Raptor 7
    on 2026-10-10, has this pair at t = 411; before that capture no log in the corpus had it.

    Setup: one block with a life far longer than the run, four 30-hour phases alternating mission / non-mission
    from a mission. Every distribution `Fixed`, 100 hours, one replication.

    Expected: the colour row at 30 is `Mission -> Green`; the later boundaries repeat `Green -> Green`.

    Source: raptor/core/engine/cascade_phase.py (`ConductPhaseChange`), Raptor 7
    """
    nodes, links = _chain("A")
    result = run({
        "system": {"run_length": 100.0, "num_runs": 1, "termination": "TimeSim"},
        "blocks": [block("A", 500.0, 5.0, id=1)],
        "nodes": nodes, "links": links,
        "phases": [{"name": f"P{index + 1}", "mission": 1 - (index % 2), "dist": FIXED, "params": [30.0]}
                   for index in range(4)],
    })

    _fixed(result)

    assert_timeline(result, [
        (0.0, 'System', 'State_change', '--', 'Mission'),
        (0.0, 'Block', 'A', '--', 'Good'),
        (30.0, 'System', 'Phase_change', '--', 'Phase_change'),
        (30.0, 'System', 'Phase_change', 'Phase_change', 'Mission_success'),
        (30.0, 'System', 'State_change', 'Mission', 'Green'),
        (60.0, 'System', 'Phase_change', 'Mission_success', 'Phase_change'),
        (60.0, 'System', 'Phase_change', 'Phase_change', 'Mission'),
        (60.0, 'System', 'State_change', 'Green', 'Green'),
        (90.0, 'System', 'Phase_change', 'Mission', 'Phase_change'),
        (90.0, 'System', 'Phase_change', 'Phase_change', 'Mission_success'),
        (90.0, 'System', 'State_change', 'Green', 'Green'),
        (100.0, 'Block', 'A', 'Good', 'Done'),
        (100.0, 'System', 'Simulation_Terminate', '--', '--'),
    ])
