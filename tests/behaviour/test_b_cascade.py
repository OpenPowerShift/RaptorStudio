"""The cascade dispatchers: what a state change does to everything downstream.

`raptor/core/engine/cascade.py` holds the three functions that turn one
element's state change into an update further down the graph:

* `reconfigure_after_block_change` -- the block -> node hop, guarded by the
  source's `ChangeBlockState` dispatch: a delta
  sum of 0 is a silent no-op, a sum of 2 cascades, anything else is the
  source's `"Error2 in ChangeBlockState!"` case.
* `reconfigure_after_node_change` -- the node -> node hop,
 the same contract with a fan-out over
  `node.propagates_to_node_ids`.
* `reconfigure_system` -- the *permanent* path-reachability ratchet
 : when a path goes permanently inactive
  (`SetActiveLink(FALSE)`, which never flips back within a run) and the
  node's active-path count drops to exactly `MinGoodPathsReq - 1`, an End
  node reports `"Permanently_Down"` and any other node recurses downstream.

**Why this file exists.** At the start of this work `cascade.py` stood at
**27.0%** statement coverage from `tests/behaviour/` alone (against a
recorded floor of 80.0% held by the legacy fidelity tier, which executes
the module only as a by-product of a whole-report comparison) and had not
moved through two sessions of ladder work, because **no rung of the ladder
reaches it**: S1-S11 all give every block infinite spares, so nothing in
`spine.py` ever runs a path out. This file is the behaviour test for the
module, in the same position `test_b_spf.py` took for `spf.py` (0.0% ->
87%).

**The mechanism is "the block ran out of spares", and that is the first
thing to get right.** A block with `sp_type="None"` has no sparing at all,
so `CheckForSpare`'s `sparingType=None` arm sets
`sparesOut:=TRUE` and `DetermineNewState` routes straight to
`ChangeBlockState(Done, ..., "No_spares")` with no repair. That is the
cheapest constructible route to `Done`, and every run-driven test here uses
it. `sp_type` must be the on-disk string `"None"` -- `_SPARING_TYPE_BY_NAME`
(`raptor/core/fileio/typed.py`) maps Raptor's own vocabulary and any other
spelling, `"ColdPool"` and `"Resource"` included, becomes no sparing.

**Why some tests call the module directly.** Two of the three dispatchers'
branches cannot be reached from a *run*, and a behaviour test that cannot
reach the branch it claims to pin is not a claim:

* The delta sums the guards reject (`"Error2 in ChangeBlockState!"`,
  `"Error in ChangeNodeState!"`). `blocks.change_block_state`
  (and the CASE table) sets its deltas in **pairs**, one
  "+1" and one "-1", so its sum is always 0 (a `pass` arm, e.g. Cut ->
  Cut) or 2. `nodes._node_change_state_deltas` is the same shape. The
  guards exist because the source has them; no well-formed transition can
  trip them. These two tests build a model through the harness, then call
  the dispatcher with a delta the state machine cannot produce, and pin
  the source's error text.
* The `"Permanently_Down"` line with `events_file=False`, and the node
  fan-out over more than one downstream connection. Both are reachable by
  calling the function on a model the harness built, which is what
  `test_b_spf.py` does for `spf.py` and what these do.

Every model here is `Fixed` (distribution code 19), so each is exact and
consumes **zero** randomness: `Run.draws == 0` for all of them. The
harness-built models are small on purpose (rule B4): at most 3 blocks and
4 nodes, 1 run each.

**A note on the node-level dispatcher.** `reconfigure_after_node_change` is
the port of `ChangeNodeState`'s cascade tail, but the running engine does
not call it: `_Cascade._cascade_node`
(`raptor/core/engine/orchestrator.py:886-903`) inlines the same loop, with
its own `delta_sum not in (0, 2)` guard raising the same
`"Error in ChangeNodeState!"`. So this module's node-level dispatcher is
reachable only from `tests/unit/test_two_block_cascade.py` and friends --
**an engine finding**, reported rather than worked around: the ported
function and the live code path are two implementations of one mechanism
that can drift. What the tests below pin is the ported function's
documented contract (the delta-sum guard and the fan-out in
`propagates_to_node_ids` order), because that is what `cascade.py` says it does.
"""
from __future__ import annotations

from typing import Any

import pytest

from raptor.core.engine.cascade import (
    reconfigure_after_block_change,
    reconfigure_after_node_change,
    reconfigure_system,
)
from raptor.core.engine.load import load_model
from raptor.core.engine.run_control import reset_node_and_links_for_new_run

from . import harness
from .harness import FIXED, assert_timeline
from .spine import RUN_LENGTH, spine

#: A `Fixed` life: fails at *fail*, repaired *repair* hours later. `Fixed` is
#: distribution code 19, whose draw is `param[0]` and touches no stream
#: (`raptor/core/rng.py`), so a model built from these is exact and free.
def block(name: str, fail: float, repair: float, **extra: Any) -> dict[str, Any]:
    """One block row for a spec dict, with a `Fixed` life and repair."""
    return {"name": name, "fail_dist": FIXED, "fail_params": [fail],
            "repair_dist": FIXED, "repair_params": [repair], **extra}


#: A block that can never be repaired: no sparing at all, so its one failure
#: ends its life. `CheckForSpare`'s `sparingType=None` arm
#: takes `Done "No_spares"` with no repair.
NO_SPARES = {"sp_type": "None"}


def _system(**extra: Any) -> dict[str, Any]:
    """The `system` block every spec here shares: one run of 100 sim hours."""
    return {"run_length": RUN_LENGTH, "num_runs": 1, "termination": "TimeSim",
            **extra}


def _no_spare_series() -> dict[str, Any]:
    """`Start -> A -> End`, where `A` runs out of spares at t=50.

    The smallest model that reaches `reconfigure_system` at all: `A`'s
    failure is also its `Done "No_spares"`, and `Done` is what the
    orchestrator's block path tests before calling it
    (`raptor/core/engine/orchestrator.py:651-654`).
    """
    return {
        "system": _system(),
        "blocks": [block("A", 50.0, 10.0, **NO_SPARES)],
        "nodes": [{"name": "Start", "type": 1, "n": 0, "out": 1},
                  {"name": "End", "type": 3, "n": 1, "out": 0}],
        "links": [{"from": "Start", "to": "A"}, {"from": "A", "to": "End"}],
    }


def _no_spare_behind_a_relay() -> dict[str, Any]:
    """`Start -> A -> J -> End`: the same dead block, one relay node further
    from the End node.

    `J` is a plain k-of-1 node (1 in, 1 out), which is what makes it a
    *relay* rather than a real stage: `RunEFPA`'s `TestFail` pass
   prunes a node whose every incoming path is
    starved, so `J.propagates_to_node_ids` is `[End]` and the cascade reaches the
    End node through it.
    """
    return {
        "system": _system(),
        "blocks": [block("A", 50.0, 10.0, **NO_SPARES)],
        "nodes": [{"name": "Start", "type": 1, "n": 0, "out": 1},
                  {"name": "J", "type": 2, "n": 1, "out": 1, "k": 1},
                  {"name": "End", "type": 3, "n": 1, "out": 0}],
        "links": [{"from": "Start", "to": "A"}, {"from": "A", "to": "J"},
                  {"from": "J", "to": "End"}],
    }


def _two_of_two_join(k: int) -> dict[str, Any]:
    """S2's parallel stage (`Start -> A, B -> Join -> End`) with both blocks
    unable to get a spare, and the `Join`'s k column set to *k*.

    Two no-spare blocks into a 1-of-2 `Join` is the shape that makes the
    k-of-N threshold of `reconfigure_system` legible: the first `Done`
    leaves one good path (still above k-1 for k=1... and at k-1 for k=2),
    the second leaves none.
    """
    spec = spine(2)
    spec["system"] = _system()
    for row in spec["blocks"]:
        row.update(NO_SPARES)
    for node in spec["nodes"]:
        if node["name"] == "Join":
            node["k"] = k
    return spec


def _phased_join(phase_k: list[int]) -> dict[str, Any]:
    """The same two-no-spare-blocks stage, with a **per-phase** k column on
    the `Join`.

    `phase_states` is the spec key that writes the `P001`/`P002` cells and
    sets the node's `Phases` flag; a node's cell is the k it demands in
    that phase. `typed.node_from_row` then sets the loaded node's
    `min_good_paths_required` to the **minimum** of those cells,
   which is the value
    `reconfigure_system` triggers on -- not the node's own `good_paths`.
    """
    spec = _two_of_two_join(k=2)
    spec["phases"] = [
        {"name": "P1", "mission": 1, "dist": FIXED, "params": [60.0]},
        {"name": "P2", "mission": 0, "dist": FIXED, "params": [40.0]},
    ]
    spec["phase_states"] = {"Join": phase_k}
    return spec


def _fresh(spec: dict[str, Any]):
    """The *loaded* model for *spec*, built and run through the harness and
    then reset to a fresh-run baseline.

    Three steps, and each is load-bearing. `harness.run(spec)` is the
    suite's only constructor (rule B3) and hands back the built `.rbd`;
    `load_model` turns it into the engine's own model objects with the EFPA
    graph built (`in_paths_array`, `propagates_to_node_ids`, each element's
    `downstream_link_id`) -- which is what `reconfigure_system` and
    `reconfigure_after_node_change` read and what a hand-built `RBDNode`
    would have to fake. `reset_node_and_links_for_new_run` then puts every
    node back to `AllUp` with its incoming links `Up`/`active`, because a
    loaded link starts at its dataclass default (`LinkStatus.IDLE`,
    `active=False`) and the dispatchers count *active* paths.

    The harness runs the model because it has no other constructor; a few
    milliseconds per model is the price, the same one `test_b_spf.py` pays.
    """
    model = load_model(harness.run(spec).rbd)
    for node in model.nodes_by_id.values():
        reset_node_and_links_for_new_run(node, model.links_by_id)
    return model


def _node(model: Any, name: str):
    """The one loaded node called *name*."""
    found = [n for n in model.nodes_by_id.values() if n.node_name == name]
    assert len(found) == 1, f"expected exactly one node named {name!r}, got {found}"
    return found[0]


# ---------------------------------------------------------------------------
# reconfigure_system: the permanent-down ratchet, driven by a real run
# ---------------------------------------------------------------------------

def test_a_block_that_runs_out_of_spares_reports_the_end_node_permanently_down():
    """A block with no spares goes `Done` at its failure instant and the End
    node's own path is reported permanently down at the same instant.

    Why: `Done` is the only block status the orchestrator's block path
    tests before calling `ReconfigureSystem`
    (`raptor/core/engine/orchestrator.py:651-654`), and the End node is the
    `typeNode=3` case that *returns* the line instead of recursing.
   Without a spare the block can never be
    repaired, so `SetActiveLink(FALSE)` is not a guess: the path really is
    gone for the rest of the run.

    Setup: one `Fixed` 50 h block `A` with `SpType = "None"`, between a
    Start and an End node; 100 sim hours, one run.

    Expected: `A` `Good -> Failed -> Done` all at t=50, the End node
    `-- -> Bad` at t=50, the system `Red` at t=50, and one
    `System:Permanently_Down -> Out_of_spares` line at t=50 -- the same
    instant, after the ordinary `State_change` line rather than instead of
    it. Nothing else changes state: the run ends `Mission_failure` at 100
    because the system is `Red` when the clock does.

    Source: Raptor 7 (CheckForSpare's sparingType=None arm),
    Raptor 7 (RBDNodeObj.ReconfigureSystem),
    Raptor 7 (the CallEndSimulation trigger this line feeds)
    """
    assert_timeline(harness.run(_no_spare_series()), [
        # `A` starts Running, and its `Fixed` life runs out at t=50.
        (0.0, "Block", "A", "--", "Good"),
        # `Failed` is logged before the state change,
        # and with no sparing to draw on there is no repair: `Done "No_spares"`
        # follows in the same instant, which is the transition the cascade
        # hangs off.
        (50.0, "Block", "A", "Good", "Failed"),
        (50.0, "Block", "A", "Failed", "Done"),
        # The ordinary link/node cascade first: the End node's only input
        # link goes down and it aggregates to `Bad`.
        (50.0, "Node", "end", "--", "Bad"),
        (50.0, "System", "State_change", "--", "Red"),
        # Then the permanent ratchet: the path is `SetActiveLink(FALSE)`, the
        # End node's active count is 0 == MinGoodPathsReq(1) - 1, so it
        # returns the `Permanently_Down` line.
        (50.0, "System", "Permanently_Down", "--", "Out_of_spares"),
        (100.0, "Block", "A", "Done", "Done"),
        (100.0, "System", "Phase_change", "--", "Mission_failure"),
        (100.0, "System", "Simulation_Terminate", "--", "--"),
    ])


def test_a_relay_node_does_not_stop_the_permanently_down_report() -> None:
    """A dead path one relay node upstream still reports the End node.

    Why: `reconfigure_system`'s non-End arm recurses through
    `node.propagates_to_node_ids`/`propagates_to_link_ids`,
   and those arrays are `RunEFPA`'s
    *pruned* fan-out: a node whose every incoming path is starved is
    skipped (`TestFail`), so `J`'s own single
    downstream connection is the End node's. That pruning is what lets a
    multi-hop topology report at all -- without it the report would stop at
    the relay and the End node would never hear about it.

    Setup: `Start -> A -> J -> End`, `A` with no spares and a `Fixed` 50 h
    life, `J` a plain k-of-1 relay.

    Expected: identical to the direct case -- `A` `Failed` then `Done` at
    t=50, `J` and `End` both `-- -> Bad`, the system `Red`, and exactly one
    `Permanently_Down` at t=50. `J`'s own line is not `Permanently_Down`:
    only a `typeNode=3` node returns the line.

    Source: Raptor 7 (ReconfigureSystem's recursion),
    Raptor 7 (RBDNodeObj.TestFail, the relay pruning)
    """
    assert_timeline(harness.run(_no_spare_behind_a_relay()), [
        (0.0, "Block", "A", "--", "Good"),
        (50.0, "Block", "A", "Good", "Failed"),
        (50.0, "Block", "A", "Failed", "Done"),
        # Both nodes log, in cascade order: `J` first (the block's own
        # downstream node), then the End node it fans out to.
        (50.0, "Node", "J", "--", "Bad"),
        (50.0, "Node", "end", "--", "Bad"),
        (50.0, "System", "State_change", "--", "Red"),
        # One line, from the End node the recursion reached through `J`.
        (50.0, "System", "Permanently_Down", "--", "Out_of_spares"),
        (100.0, "Block", "A", "Done", "Done"),
        (100.0, "System", "Phase_change", "--", "Mission_failure"),
        (100.0, "System", "Simulation_Terminate", "--", "--"),
    ])


def test_a_stage_that_has_not_lost_its_last_good_path_reports_nothing() -> None:
    """A 3-of-2 stage reports nothing when one of its two paths dies.

    Why: the ratchet fires only on the specific edge where the node's
    active-path count drops to exactly `MinGoodPathsReq - 1`
   -- a *threshold*, not "any path died". Here
    `MinGoodPathsReq` is 3 and the count goes 2 -> 1, which is not 2, so
    the function returns no line even though a path went permanently
    inactive. This is the branch that separates the mechanism from
    `AnalyzeSystem`'s colour aggregation: the system is already `Red` at
    t=0, and no `Permanently_Down` line is ever written.

    Setup: S2's `Start -> A, B -> Join -> End` with both blocks
    `SpType = "None"` and the `Join` written `k = 3` of `n = 2`.

    Expected: no `Permanently_Down` line anywhere in the run. `A` goes
    `Done` at t=50 and `B` at t=55, and the `Join` is `Bad -> Bad` both
    times -- it was already `Bad` at t=0, because a 3-of-2 node can never
    be satisfied.

    Source: Raptor 7 (the MinGoodPathsReq - 1 trigger),
    Raptor 7 (MinGoodPathsReq's definition)
    """
    run = harness.run(_two_of_two_join(k=3))
    assert_timeline(run, [
        (0.0, "Block", "A", "--", "Good"),
        (0.0, "Block", "B", "--", "Good"),
        # k=3 of n=2 can never be met, so the node is Bad from kickoff.
        (0.0, "Node", "Join", "--", "Bad"),
        (0.0, "Node", "end", "--", "Bad"),
        (0.0, "System", "State_change", "--", "Red"),
        (50.0, "Block", "A", "Good", "Failed"),
        (50.0, "Block", "A", "Failed", "Done"),
        (50.0, "Node", "Join", "Bad", "Bad"),
        (50.0, "System", "Red_change", "--", "Red"),
        (55.0, "Block", "B", "Good", "Failed"),
        (55.0, "Block", "B", "Failed", "Done"),
        (55.0, "Node", "Join", "Bad", "Bad"),
        (55.0, "System", "Red_change", "Red", "Red"),
        (100.0, "Block", "A", "Done", "Done"),
        (100.0, "Block", "B", "Done", "Done"),
        (100.0, "System", "Phase_change", "--", "Mission_failure"),
        (100.0, "System", "Simulation_Terminate", "--", "--"),
    ])
    assert not [e for e in run.events if e.name == "Permanently_Down"], (
        "a 3-of-2 node whose active count fell 2 -> 1 never reached "
        "MinGoodPathsReq - 1 == 2, so reconfigure_system must return no "
        f"line; got {[e.new for e in run.events if e.name == 'Permanently_Down']}"
    )


def test_a_k_of_two_stage_reports_on_its_first_dead_path() -> None:
    """The same two dead blocks, with the stage's k at 2, report at t=50 --
    one path short of the requirement, not at zero.

    Why: this is the threshold edge the previous test shows the other side
    of. `MinGoodPathsReq` is 2, so the count going 2 -> 1 *is*
    `MinGoodPathsReq - 1` and the function fires immediately -- while a
    good path is still up and the system is still Yellow-able. The line
    says "this stage can no longer meet its requirement", which is not the
    same claim as "the system is Red".

    Setup: S2's parallel stage, both blocks `SpType = "None"`, `Join`
    written `k = 2` of `n = 2`.

    Expected: `A` `Done` at t=50, `Join` and `End` `-- -> Bad`, the system
    `Red`, and `Permanently_Down` at t=50. `B`'s own `Done` at t=55 changes
    no node status and writes no second line: the count has already passed
    the edge.

    Source: Raptor 7 (the MinGoodPathsReq - 1 trigger),
    Raptor 7 (MinGoodPathsReq)
    """
    assert_timeline(harness.run(_two_of_two_join(k=2)), [
        (0.0, "Block", "A", "--", "Good"),
        (0.0, "Block", "B", "--", "Good"),
        # The first path dies: active 2 -> 1, which IS k-1 for k=2.
        (50.0, "Block", "A", "Good", "Failed"),
        (50.0, "Block", "A", "Failed", "Done"),
        (50.0, "Node", "Join", "--", "Bad"),
        (50.0, "Node", "end", "--", "Bad"),
        (50.0, "System", "State_change", "--", "Red"),
        (50.0, "System", "Permanently_Down", "--", "Out_of_spares"),
        # The second path dies and nothing new is reported: the threshold
        # edge was crossed once, at t=50.
        (55.0, "Block", "B", "Good", "Failed"),
        (55.0, "Block", "B", "Failed", "Done"),
        (55.0, "Node", "Join", "Bad", "Bad"),
        (55.0, "System", "Red_change", "--", "Red"),
        (100.0, "Block", "A", "Done", "Done"),
        (100.0, "Block", "B", "Done", "Done"),
        (100.0, "System", "Phase_change", "--", "Mission_failure"),
        (100.0, "System", "Simulation_Terminate", "--", "--"),
    ])


def test_a_phased_node_reports_on_its_smallest_per_phase_k() -> None:
    """A `Join` written k=2 but phased `[2, 1]` waits for *both* paths to
    die before it reports -- the trigger is the smallest per-phase k.

    Why: `MinGoodPathsReq` is "the MINIMUM per-phase k when the node is
    phased, else `goodPaths`" (ported at
    `raptor/core/fileio/typed.py:271-274`). This node's `good_paths` is 2
    and its per-phase cells are `[2, 1]`, so `min_good_paths_required` is 1 and
    the trigger is `1 - 1 == 0`: the phase-2 requirement of one good path
    governs, and a run that only ever reached phase 1 would never fire it.
    Comparing this against the unphased k=2 fixture is the whole claim --
    same topology, same two dead blocks, one extra spec key, and the report
    moves from t=50 to t=55.

    Setup: the k=2 parallel stage, plus two `Fixed` phases (60 h mission,
    40 h non-mission) and `phase_states = {"Join": [2, 1]}`.

    Expected: no `Permanently_Down` at t=50 (active count 1, not 0), and
    one at t=55 when `B`'s own `Done` takes the count to 0. The phase
    boundary at t=60 lands after the report, so it cannot be what moved it.

    Source: Raptor 7 (MinGoodPathsReq = min per-phase k),
    Raptor 7 (the trigger that reads it)
    """
    run = harness.run(_phased_join([2, 1]))
    assert_timeline(run, [
        # A phased model opens a mission at kickoff (`ChangeStatus`,
        # raptor/core/engine/system_status.py), which is the one row the
        # unphased fixture does not have.
        (0.0, "System", "State_change", "--", "Mission"),
        (0.0, "Block", "A", "--", "Good"),
        (0.0, "Block", "B", "--", "Good"),
        # Phase 1 demands k=2, so losing one path takes the Join Bad --
        # but min_good_paths_required is 1, so nothing is reported yet.
        (50.0, "Block", "A", "Good", "Failed"),
        (50.0, "Block", "A", "Failed", "Done"),
        (50.0, "Node", "Join", "--", "Bad"),
        (50.0, "Node", "end", "--", "Bad"),
        (50.0, "System", "State_change", "Mission", "Red"),
        # Second path dead: active count 0 == min_good_paths_required(1) - 1.
        (55.0, "Block", "B", "Good", "Failed"),
        (55.0, "Block", "B", "Failed", "Done"),
        (55.0, "Node", "Join", "Bad", "Bad"),
        (55.0, "System", "Permanently_Down", "--", "Out_of_spares"),
        (55.0, "System", "Red_change", "--", "Red"),
        (60.0, "System", "Phase_change", "--", "Phase_change"),
        (60.0, "System", "Phase_change", "Phase_change", "Mission_failure"),
        (60.0, "System", "State_change", "Red", "Red"),
        (60.0, "Node", "Join", "Bad", "Bad"),
        (100.0, "Block", "A", "Done", "Done"),
        (100.0, "Block", "B", "Done", "Done"),
        (100.0, "System", "Simulation_Terminate", "--", "--"),
    ])


def test_an_event_that_fails_permanently_reports_the_end_node_down() -> None:
    """An event whose outcome is a certain failure reports the End node
    permanently down at t=0, through the event-side `ReconfigureSystem`.

    Why: the event path is a second, separate call site
    (`raptor/core/engine/orchestrator.py:750-757`, port of
    `RBDEventObj.ReconfigureSystem`'s unphased `Armed -> Failure` arm), and it is gated on `fail_change == 1` and on
    the event being **unphased** -- a phased event's failure is not
    permanent, since it can re-fire in a later phase. The event here fires
    once at kickoff and fails, so the End node loses its only input at
    t=0.

    Setup: `Start -> A -> J -> E1 -> End`, `A` with a `Fixed` 300 h life
    (it never fails inside the run), and `E1` written `failVal = 0.0` as a
    `Fixed` distribution -- `failVal` is the Bernoulli parameter
    `ConductEvent` draws (`raptor/core/engine/events.py:70-90`), so 0.0 is a
    certain failure and costs no draws.

    Expected: `E1` `Armed -> Failed` at t=0, the End node `-- -> Bad`,
    the system `Red`, and one `Permanently_Down` at t=0. `A` never enters
    the log again: it is still `Good` when the run ends.

    Source: Raptor 7 (RBDEventObj.ReconfigureSystem's
    unphased Armed -> Failure arm), Raptor 7 (AllowReconfigureSystem, TRUE on that arm)
    """
    spec = {
        "system": _system(),
        "blocks": [block("A", 300.0, 10.0)],
        "nodes": [{"name": "Start", "type": 1, "n": 0, "out": 1},
                  {"name": "J", "type": 2, "n": 1, "out": 1, "k": 1},
                  {"name": "End", "type": 3, "n": 1, "out": 0}],
        "links": [{"from": "Start", "to": "A"}, {"from": "A", "to": "J"},
                  {"from": "J", "to": "E1"}, {"from": "E1", "to": "End"}],
        "events": [{"name": "E1", "dist": FIXED, "failVal": 0.0}],
    }
    assert_timeline(harness.run(spec), [
        (0.0, "Block", "A", "--", "Good"),
        # Events fire once, synchronously, during kickoff
        # (`RBDEventObj.SetToInitValues`).
        (0.0, "Event", "E1", "--", "Armed"),
        (0.0, "Event", "E1", "Armed", "Failed"),
        (0.0, "Node", "end", "--", "Bad"),
        (0.0, "System", "State_change", "--", "Red"),
        (0.0, "System", "Permanently_Down", "--", "Out_of_spares"),
        # The end of the run sweeps events back to `Armed`
        # (`EndSimulation`) and blocks to `Done`.
        (100.0, "Block", "A", "Good", "Done"),
        (100.0, "Event", "E1", "Failed", "Armed"),
        (100.0, "System", "Phase_change", "--", "Mission_failure"),
        (100.0, "System", "Simulation_Terminate", "--", "--"),
    ])


def test_a_failsim_run_ends_at_the_instant_the_end_node_goes_permanently_down() -> None:
    """`FailSim` termination stops the run on the permanently-down report,
    and the run is recorded as having run out of spares.

    Why: this is what the report is *for*. A `FailSim` model is meant to
    stop when the system can no longer be recovered, and
    Raptor 7's `CallEndSimulation` is triggered by a
    non-empty `ReconfigureSystem` return -- the orchestrator's port at
    `raptor/core/engine/orchestrator.py:657-688` queues a zero-delay
    `_end_sim_process` so the current cascade finishes first, which is why
    `A`'s closing `Done` row lands at t=50 rather than at t=100. The
    `out_of_spares` flag it sets is what the final results report prints as
    `Unable to complete 100 failure/repair cycles`.

    Setup: the one-block no-spare series model, `termination = "FailSim"`,
    `run_length = 100` (the `run_length > 1` guard the port applies).

    Expected: the run stops at t=50 -- `Simulation_Terminate` at 50, not
    100, and no `A Done -> Done` sweep row at 100 -- and the final results
    report carries the out-of-spares note with `Ending_Sim_Time 50.000000`.

    Source: Raptor 7 (CallEndSimulation),
    Raptor 7 (the return that triggers it)
    """
    spec = _no_spare_series()
    spec["system"] = _system(termination="FailSim")
    run = harness.run(spec)

    assert_timeline(run, [
        (0.0, "Block", "A", "--", "Good"),
        (50.0, "Block", "A", "Good", "Failed"),
        (50.0, "Block", "A", "Failed", "Done"),
        (50.0, "Node", "end", "--", "Bad"),
        (50.0, "System", "State_change", "--", "Red"),
        (50.0, "System", "Permanently_Down", "--", "Out_of_spares"),
        # The queued `_end_sim_process` runs at the end of this instant, so
        # the closing `Done` and the mission row are stamped t=50 too.
        (50.0, "Block", "A", "Done", "Done"),
        (50.0, "System", "Phase_change", "--", "Mission_failure"),
        (50.0, "System", "Simulation_Terminate", "--", "--"),
    ])
    assert any("out of spares" in line for line in run.final), (
        "a FailSim run stopped by the permanently-down report must be "
        "reported as out of spares; final results were: "
        f"{[l for l in run.final if l.strip()][:8]}"
    )
    assert any("Ending_Sim_Time" in line and "50.000000" in line
               for line in run.final), (
        "the run must be recorded as ending at t=50, not at its t=100 "
        f"run_length; got {[l for l in run.final if 'Ending' in l]}"
    )


# ---------------------------------------------------------------------------
# reconfigure_system called directly: the line, and its absence
# ---------------------------------------------------------------------------

def test_the_permanently_down_line_is_suppressed_without_an_event_log() -> None:
    """With the event log off, the path is still marked permanently
    inactive and no line is returned.

    Why: `events_file` gates only the *return value*, never the state
    change -- `reconfigure_system` sets `SetActiveLink(FALSE)` and
    recomputes the count before it looks at `events_file` at all.
   That ordering is load-bearing: the cold
    standby `Switch_Failed` path calls the function to do the state change
    and logs its own `Node ... Switch_Failed` line separately
    (`raptor/core/engine/cold_standby.py:233-235`), so the reachability
    ratchet and the log line are independent.

    Setup: the one-block model, loaded through the harness; the End node's
    single incoming link deactivated with `events_file=False`.

    Expected: an empty list, and the End node's incoming link
    `active == False` afterwards -- the permanent mark is taken either way.

    Source: Raptor 7 (SetActiveLink before the log guard),
    Raptor 7 (RunColdLogic's switchStatus="none" branch, the
    caller that passes events_file through)
    """
    model = _fresh(_no_spare_series())
    end = _node(model, "End")
    in_path = end.in_paths_array[0]

    assert reconfigure_system(end, model.nodes_by_id, model.links_by_id,
                              in_path, 10.0, events_file=False) == []
    assert model.links_by_id[in_path].active is False, (
        "the permanent mark is `SetActiveLink(FALSE)` and happens before "
        "events_file is consulted; the flag must be False even with the log off"
    )


def test_the_permanently_down_line_names_the_reason_it_fired() -> None:
    """The line's `from` is the system's own vocabulary, not a block's: it
    is logged on `System`, `System_change`, `Out_of_spares`.

    Why: the line is written by `format_event_log_line(sim_time, "System",
    "Permanently_Down", "Out_of_spares", "System_change")` in the
    `typeNode=3` arm, so the object it is
    attributed to is the *system*, not the node or the block that ran out
    of spares. That is what makes the log row readable: a reader scanning
    for `Permanently_Down` finds a system-level verdict, and the block's
    own `Done No_spares` row is the cause, elsewhere in the same instant.

    Setup: the one-block model, loaded through the harness, the End node's
    incoming link deactivated with the log on.

    Expected: exactly one line, splitting into the five event-log columns
    `(time, type, name, action, comment)` as
    `10.000000 / System / Permanently_Down / Out_of_spares / System_change`.

    Source: Raptor 7 (the typeNode=3 WriteLn),
    raptor/core/engine/eventlog.py format_event_log_line (the five columns)
    """
    model = _fresh(_no_spare_series())
    end = _node(model, "End")
    in_path = end.in_paths_array[0]

    logs = reconfigure_system(end, model.nodes_by_id, model.links_by_id,
                              in_path, 10.0, events_file=True)
    assert len(logs) == 1, f"expected one line, got {logs}"
    assert logs[0].split() == [
        "10.000000", "System", "Permanently_Down", "Out_of_spares",
        "System_change",
    ], f"unexpected permanently-down line: {logs[0]!r}"


# ---------------------------------------------------------------------------
# reconfigure_after_node_change: the node -> node fan-out
# ---------------------------------------------------------------------------

def _fan_out_join() -> dict[str, Any]:
    """A node whose fan-out reaches the End node over **two** connections.

    `Start -> A -> J`, then `J` out to `C` and `D`, each of which feeds
    `End`. `J` therefore has two downstream connections and `End` two
    incoming paths, so one delta on `J` reconfigures the End node twice --
    once per connection, in `propagates_to_node_ids` order, and the second call
    sees the first call's link status. This is the only shape in which the
    fan-out loop is visible at all: everywhere else in this file `J` has a
    single downstream connection and the loop runs once.

    `A` is long-lived so nothing else moves: the claim is about the
    dispatcher, not about a failure.
    """
    return {
        "system": _system(),
        "blocks": [block("A", 300.0, 10.0), block("C", 300.0, 10.0),
                   block("D", 300.0, 10.0)],
        "nodes": [{"name": "Start", "type": 1, "n": 0, "out": 1},
                  {"name": "J", "type": 2, "n": 1, "out": 2, "k": 1},
                  {"name": "End", "type": 3, "n": 2, "out": 0}],
        "links": [{"from": "Start", "to": "A"}, {"from": "A", "to": "J"},
                  {"from": "J", "to": "C"}, {"from": "J", "to": "D"},
                  {"from": "C", "to": "End"}, {"from": "D", "to": "End"}],
    }


def test_a_node_transition_cascades_to_every_downstream_connection_in_order() -> None:
    """One delta on a node cascades once per downstream connection, in
    `propagates_to_node_ids` order, and returns one result per connection that
    changed.

    Why: a node may fan out to several downstream nodes -- the source's
    parallel `EFPAConnectTo`/`EFPAConnectPath` lists
   -- so the port loops the connections and
    calls `reconfigure_node` on each, keeping the results in order. The
    order is observable here because the two connections land on the *same*
    End node: the first call's link update is what the second call
    aggregates, so the two results are `Degraded` and then `Bad`, not `Bad`
    twice. A loop that ran the connections in the other order, or that
    aggregated once for the whole fan-out, could not produce that pair.

    Setup: the two-connection fan-out model above, loaded through the
    harness, with `J` taking a `Run -> Fail` delta (`-1, +1, 0, 0, 0`,
    's pair).

    Expected: two results -- `Degraded` with `1-1/2_df5_ur6`, then `Bad`
    with `0-1/2_df5_df6` -- i.e. path 5 down and path 6 still up after the
    first, both down after the second.

    Source: Raptor 7 (ChangeNodeState's cascade tail),
    Raptor 7 (ReconfigureNode's aggregation, which is what
    produces the `N-M_df5_ur6` comment)
    """
    model = _fresh(_fan_out_join())
    join = _node(model, "J")

    results = reconfigure_after_node_change(
        join, model.nodes_by_id, model.links_by_id, -1, 1, 0, 0, 0, 10.0,
        events_file=True)

    assert [(r.status_string, r.num_good_paths, r.link_string) for r in results] == [
        ("Degraded", 1, "_df5_ur6"),
        ("Bad", 0, "_df5_df6"),
    ], (
        "the fan-out must reconfigure the End node once per downstream "
        "connection, in order, each seeing the previous one's link status; "
        f"got {[(r.status_string, r.num_good_paths, r.link_string) for r in results]}"
    )
    assert [r.log_line.split()[3] for r in results] == ["Degraded", "Bad"], (
        "each result carries its own event-log row, in cascade order; got "
        f"{[r.log_line for r in results]}"
    )


def test_a_repeated_node_transition_changes_no_link_and_returns_nothing() -> None:
    """The same delta twice: the second call is a silent no-op.

    Why: `reconfigure_node` returns `None` when the affected link's status
    did not actually change (Raptor 7's
    `IF ((linkStatusChanged) OR (inPath=0))` guard), and
    `reconfigure_after_node_change` drops those `None`s rather than
    returning them. Re-applying a delta the node has already absorbed is
    therefore invisible -- which is what stops a re-cascade from writing a
    duplicate node line, and it is the same `None` contract the block-side
    dispatcher has.

    Setup: the two-connection fan-out model, loaded through the harness,
    with `J` taking the same `Run -> Fail` delta twice.

    Expected: the first call returns the `Degraded` then `Bad` pair; the
    second returns an empty list, having written no line.

    Source: Raptor 7 (ReconfigureNode's linkStatusChanged
    guard), Raptor 7 (the fan-out that drops the Nones)
    """
    model = _fresh(_fan_out_join())
    join = _node(model, "J")

    first = reconfigure_after_node_change(
        join, model.nodes_by_id, model.links_by_id, -1, 1, 0, 0, 0, 10.0,
        events_file=True)
    second = reconfigure_after_node_change(
        join, model.nodes_by_id, model.links_by_id, -1, 1, 0, 0, 0, 10.0,
        events_file=True)

    assert [r.status_string for r in first] == ["Degraded", "Bad"], (
        f"the first cascade must reconfigure both connections, got {first}")
    assert second == [], (
        "re-applying a delta whose links are already down changes no link "
        "status, so reconfigure_node returns None for both connections and "
        f"the fan-out must return nothing; got {second}"
    )


def test_a_node_transition_with_no_net_change_cascades_to_nothing() -> None:
    """All-zero deltas on a node are a silent no-op -- no line, no result.

    Why: the source has no `ELSIF` for the sum-0 case on the node side
    either: `ChangeNodeState`'s cascade tail runs only on a real
    transition, and a delta set that sums to zero is the Cut/Linked
    bookkeeping case, where no link status
    changes. This is the node-level twin of the block-side sum-0 no-op and
    it is what the guard exists for.

    Setup: the two-connection fan-out model, loaded through the harness,
    with `J` taking `(0, 0, 0, 0, 0)`.

    Expected: an empty list.

    Source: Raptor 7 (ChangeNodeState's delta pairs, where a
    sum of 0 is the Cut/Linked bookkeeping case),
    Raptor 7 (the cascade tail it guards)
    """
    model = _fresh(_fan_out_join())
    join = _node(model, "J")

    assert reconfigure_after_node_change(
        join, model.nodes_by_id, model.links_by_id, 0, 0, 0, 0, 0, 10.0,
        events_file=True) == []


# ---------------------------------------------------------------------------
# The delta-sum guards: the source's own error cases
# ---------------------------------------------------------------------------
#
# Both guards are unreachable from a run, and that is the finding rather
# than an excuse. `blocks.change_block_state` sets its five deltas in
# **pairs** -- every arm of its CASE assigns one "+1" and one "-1", or
# `pass` -- so the sum is always 0 or 2 and never anything else.
# `nodes._node_change_state_deltas` has the same
# shape. The guards exist because the source has them
# so they are pinned by calling
# the dispatcher with a delta the state machine cannot produce, on a model
# the harness built.

def test_a_block_delta_the_state_machine_cannot_produce_is_the_error2_case() -> None:
    """A delta sum that is neither 0 nor 2 raises the source's
    `"Error2 in ChangeBlockState!"`.

    Why: `ChangeBlockState`'s dispatch accepts
    only a sum of 2 (cascade) and a sum of 0 (no real transition, e.g.
    Cut/Linked bookkeeping) and treats anything else as a bug in the
    caller, reporting it as `"Error2 in ChangeBlockState!"`. A sum of 1 --
    one bucket changed with nothing to offset it -- is exactly what the
    port's `blocks.change_block_state` cannot produce, which is why this
    needs a direct call rather than a model.

    Setup: the one-block model loaded through the harness; the End node
    given `run_change = -1` and nothing else.

    Expected: `ValueError("Error2 in ChangeBlockState!")` -- the source's
    own text, verbatim, because that string is what a reader of the MODSIM
    source would be grepping for.

    Source: Raptor 7 (ChangeBlockState's dispatch guard)
    """
    model = _fresh(_no_spare_series())
    block_a = next(b for b in model.blocks_by_id.values() if b.name == "A")
    end = _node(model, "End")

    with pytest.raises(ValueError, match=r"^Error2 in ChangeBlockState!$"):
        reconfigure_after_block_change(block_a, end, model.links_by_id,
                                       -1, 0, 0, 0, 0, 10.0, events_file=True)


def test_a_node_delta_the_state_machine_cannot_produce_is_the_change_node_state_error() -> None:
    """A node delta sum of 3 raises the source's
    `"Error in ChangeNodeState!"`.

    Why: the node-level guard is the same contract one level down, with the
    source's own wording -- note it is *not* the block side's `"Error2"`.
   A sum of 3 is three buckets changed at
    once, which `_node_change_state_deltas` cannot emit: like the block
    CASE it assigns deltas in pairs, one "+1" and one "-1".

    Setup: the two-connection fan-out model loaded through the harness,
    with `J` given `run_change = 1, fail_change = 1, idle_change = 1`.

    Expected: `ValueError("Error in ChangeNodeState!")`.

    Source: Raptor 7 (ChangeNodeState's delta computation and
    its cascade guard)
    """
    model = _fresh(_fan_out_join())
    join = _node(model, "J")

    with pytest.raises(ValueError, match=r"^Error in ChangeNodeState!$"):
        reconfigure_after_node_change(join, model.nodes_by_id, model.links_by_id,
                                      1, 1, 1, 0, 0, 10.0, events_file=True)


def test_a_cut_bookkeeping_transition_is_a_silent_block_no_op() -> None:
    """A sum-0 block delta returns `None` -- the no-op the source has no
    arm for.

    Why: `ChangeBlockState`'s dispatch has an
    `ELSIF` for the sum-2 cascade but nothing for a sum of 0, because a
    delta set that nets to zero is not a transition at all: it is the
    Cut/Linked bookkeeping case (`pass` in the block CASE,
    `raptor/core/engine/blocks.py`), where the block's op status is
    unchanged and only its active status moved. Returning `None` -- rather
    than a `NodeReconfigureResult` -- is what tells the caller there is no
    node line to write and no cold node to mark.

    Setup: the one-block model loaded through the harness; all five block
    deltas zero.

    Expected: `None`, not an empty list and not a result object.

    Source: Raptor 7 (the dispatch guard with no sum-0 arm)
    """
    model = _fresh(_no_spare_series())
    block_a = next(b for b in model.blocks_by_id.values() if b.name == "A")
    end = _node(model, "End")

    assert reconfigure_after_block_change(
        block_a, end, model.links_by_id, 0, 0, 0, 0, 0, 10.0,
        events_file=True) is None
