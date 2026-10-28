"""The dependency dispatch's edges: which follower moves, and which does not.

`docs/engine-design/dependency-backflow.adoc` describes the mechanism --
*when what a follower depends on goes down, the follower reacts according to
its four percentages; when it comes back, the follower resumes* -- and
`tests/unit/test_dependency.py` and the ladder's `DepIdle`/`DepFail` rows pin
the paths a real model walks. **What is left is the edges**: the guard
conditions, the branches that only a specific *arranged* state reaches, and
the three `for`-loop back-edges whose only route is a non-conforming argument.
Most of the guard conditions are not reachable from the engine at all, for the
reason the ladder's own `test_b_trans_block.py` records at length:

> `BackFlowDependencies`' `Down` arm is gated on
> `tempBlock.opStatus IN (Running, Standby)`, so a
> dependent that is genuinely repairing, held or in PM is never moved.

A dependent mid-repair-completion with a *pending* interrupt is the same
shape of gate one level deeper, and so is the `_back_flow_node_dependents`
outer gate `(tempNode.Status<>Down) AND NOT tempNode.IgnoreDep`. Those rows
are real branches with no engine path to them, and the only honest way to hold
them is to **arrange** the state and call the ported function directly -- the
same move `test_b_cascade.py` makes, and for the same stated reason: it found
a ported dispatcher the running engine does not call, and pinned the ported
function's contract rather than pretending the live path covers it.

**So this file has two kinds of test, and every test says which it is.**

* *Through the engine* (one test, the hundred-percent go-to-PM reaction). A
  two-block `Fixed` model, `draws == 0`, asserted with an `assert_timeline`
  literal written inline.
* *Through a direct call* (everything else). Each model is built in memory by
  `harness.run` and turned into the engine's own objects by `load_model` --
  no `.rbd` on disk, no `Workspace`, no Qt -- and the ported function is then
  handed those objects plus a **recording `transition` callback**. The
  callback is the harness's own idea applied to a function that takes one:
  `dependency.py` never calls `ChangeBlockState` itself (see its `TransitionFn`
  docstring), so the only thing to assert on is the call it *asks for*. The
  recorded tuple is `(block name, BlockStatus.value, reason, sim_time,
  no_wake_once)` -- `BlockStatus.IDLE.value` is `"Idle"`, the word the event
  log itself writes, so the expectation is still a literal in the test.

**What "arranged" means here, precisely.** The loader gives a block its
`op_status`, its `run_state.returning_from_maintenance`, its `run_state.previous_dependency_state`, its
`run_state.interrupt_reason` and its `dep_idle_percent` from the `.rbd` file, and gives
a node its `status` and `higher_cold` from whatever the last cascade left
there. A test that wants "the dependent is mid-repair-completion with its
induced-failure wake still pending" sets those two fields on the object it was
given. Nothing else is touched, no function is stubbed, and the expected value
is what `dependency.py` decided -- so a change in the engine's decision still
fails these tests.

* `dependency.py:132` -- `distro_mean`'s `raise NotImplementedError`. Every one
  of `Distribution`'s 22 codes has an arm above it, so the fall-through is
  unreachable by construction.
* **A duplicated `if special_back_flow:` inside the node dispatch's `Down`
  arm**, repeating statement for statement what the block twin had one copy
  of. It could only ever be reached with `special_back_flow = False` and so
  was dead. Reported as a merge artefact. **Now fixed**: main's deduplication
  left one copy,, so there is nothing left here to pin.
* **The holes in the dispatch chains.** Three, reported together, because
  they are one defect wearing three faces: both dispatch functions fell off
  the end of their `change_type` `if`/`elif` chain when handed a trigger
  that is not one of the three declared, and the block one also fell off the
  end of its *reaction* chain while the node twin raised. Two ports of one
  source method, three behaviours for a caller bug.

  **One of the three is now closed and two are not**, which is worth stating
  precisely, because "the dedup fixed it" would be the wrong summary:

  =========================  ============================================
  bad *reaction*, block side  **closed** -- raises `EngineInvariantError`;
                              the two chains are one chain now
  bad *trigger*, block side **still open** -- no `else` after 
  bad *trigger*, node side    **still open** -- pinned by
                              `test_a_node_sourced_trigger_outside_the_
                              declared_set_is_a_silent_no_op`
  =========================  ============================================

  The silent ones are pinned as they are. They change nothing and say
  nothing, which is the property worth a regression test even if a reviewer
  later decides they are defects rather than design.

**And one latent hazard, found while building these fixtures and deliberately
not exercised.** `_recurse_node_backflow` (`dependency.py:725-739`) carries no
visited set and hands the caller's `nodes_by_id` straight back down, so two
nodes whose resolved dependency targets are each other would recurse until the
interpreter's stack ran out. The only thing that stops it is
`_back_flow_node_dependents`' own outer gate,
`if dep_node.status == NodeStatus.DOWN or dep_node.ignore_dep`
(`dependency.py:690`), and that gate reads each **follower's own** status --
so it helps only if the node whose change triggered the dispatch was already
marked `Down` before the walk began. `ChangeNodeState` does mark it first,
which is why a `Down` trigger is safe in the engine; an `AllUp` or
`NodeStandby` trigger over a mutual node-dependency cycle has no stopping
condition at all in this port. The source cannot hit it, because its
`NodeDepGroup` is a membership list and a group does not contain its own
member twice; the port reconstructs membership per call, so it can. Every
fixture here is therefore acyclic in its node dependencies, and the cyclic
model is not written because the only thing it would pin is a
`RecursionError`.
"""
from __future__ import annotations

from typing import Any, NamedTuple

import pytest

from raptor.core.engine import dependency as dep
from raptor.core.engine.errors import EngineInvariantError
from raptor.core.engine.load import load_model
from raptor.core.model import ActiveStatus, BlockStatus, NodeStatus

from . import harness
from .harness import FIXED, assert_timeline

#: The sim time every direct call below dispatches at. SimTime is the only
#: clock a behaviour test reads (rule B7); 12.0 is in no way special except
#: that it is not 0.0, which several of the guards below test for.
AT = 12.0


def _block(name: str, fail: float, **extra: Any) -> dict[str, Any]:
    """One block row: a `Fixed` life at *fail*, a `Fixed` repair, nothing
    stochastic anywhere (rule B2 -- `Fixed` is code 19, whose draw is
    `param[0]` and touches no stream)."""
    return {"name": name, "fail_dist": FIXED, "fail_params": [fail],
            "repair_dist": FIXED, "repair_params": [10.0], **extra}


def _system(**extra: Any) -> dict[str, Any]:
    return {"run_length": 100.0, "num_runs": 1, "termination": "TimeSim", **extra}


def _id_after(earlier: int, section: list[dict[str, Any]], name: str) -> int:
    """The id `build_from_spec` hands the named row of *section*, given that
    *earlier* elements come before it.

    `dep_num` is an **id**, not a name, so a spec that wants a dependence has
    to write the number the builder is about to assign. Ids are the lowest
    unused positive integers, handed out as the spec is walked, and
    `build_from_spec` walks `blocks`, then `nodes`, then `events` -- the same
    rule `spine.py`'s `_block_id` records for blocks. Taking the *count* of the
    earlier sections rather than their contents is what makes this usable while
    the later section's own rows are still being written.
    """
    for index, row in enumerate(section, start=1):
        if row.get("name") == name:
            return earlier + index
    raise KeyError(f"no {name!r} in the section")


def _load(spec: dict[str, Any]):
    """Build *spec* in memory, run it, and hand back the engine's own model.

    Three steps, and the middle one is not optional: `harness.run` is the only
    constructor the suite has (rule B3), it returns the built `.rbd`, and
    `load_model` turns that into the `RBDBlock`/`RBDNode`/`RBDEvent` objects
    the engine runs on. No file, no `Workspace`, no Qt.
    """
    return load_model(harness.run(spec).rbd)


def _block_by_name(model: Any, name: str) -> Any:
    return next(b for b in model.blocks_by_id.values() if b.name == name)


def _node_by_name(model: Any, name: str) -> Any:
    return next(n for n in model.nodes_by_id.values() if n.node_name == name)


class _Log(NamedTuple):
    """One recorded `TransitionFn` call.

    `status` is `BlockStatus.<member>.value`, i.e. the log's own vocabulary
    (`"Idle"`, not `BlockStatus.IDLE`), so a test's expectation is a literal
    in the log's words. `no_wake` is the block's `run_state.no_wake_once` *at the
    moment of the call*, which is the flag that suppresses the wake-up and is
    therefore part of what the `returning_from_maintenance` arm decides.
    """
    block: str
    status: str
    reason: str
    sim_time: float
    no_wake: bool


def _recorder(log: list[_Log]):
    """A `TransitionFn` that appends to *log* instead of changing a block."""
    def record(block: Any, status: Any, reason: str, sim_time: float) -> None:
        log.append(_Log(block.name, status.value, reason, sim_time,
                        block.run_state.no_wake_once))
    return record


def _dispatch(model: Any, trigger: str, log: list[_Log],
              on_cold: Any = None) -> None:
    """Fire the node-sourced back-flow from relay node `T`, into *log*.

    Wraps `back_flow_dependencies_from_node`, whose signature changed under
    this branch: it used to take ten positional parameters plus a
    `nodes_by_id` keyword, and main's review E-09 replaced all of them with a
    `BackFlowDeps` bundle because **seven** call sites were assembling the
    same eight expressions by hand. A test that reaches into the engine has to
    move with it, which is the cost of pinning dispatch branches that no
    in-memory model reaches.

    What did *not* change is what the function does, so the assertions in
    every test below are exactly as they were before the merge -- they are
    the check on whether deduplicating the dispatch preserved behaviour, and
    rewriting them to match new output would have destroyed it.
    """
    deps = dep.BackFlowDeps(
        end_node_id=_node_by_name(model, "End").id,
        all_blocks=list(model.blocks_by_id.values()),
        links_by_id=model.links_by_id,
        transition=_recorder(log),
        nodes_by_id=model.nodes_by_id,
        apply_cold_logic=(None if on_cold is None
                          else lambda node, _sim_time: on_cold(node)),
    )
    dep.back_flow_dependencies_from_node(
        trigger, _node_by_name(model, "T"), deps, AT)


def _arrange(block: Any, **fields: Any) -> None:
    """Put *block* into the state a claim is about, and nothing else.

    `op_status` goes through the same enum the engine uses; the `run_state.*` and
    `def_dep_state_idle` fields are the ones the loader would have read from
    the `.rbd` file had the file said so.
    """
    for name, value in fields.items():
        if name == "op_status":
            block.op_status = value
        elif name.startswith("run_state_"):
            field = name.removeprefix("run_state_")
            # A misspelt or renamed field must fail here, not set a new
            # attribute the engine never reads.
            assert hasattr(block.run_state, field), field
            setattr(block.run_state, field, value)
        else:
            assert hasattr(block, name), name
            setattr(block, name, value)


# ---------------------------------------------------------------------------
# Models. Each is a fresh spec per call, so no test can leak arranged state
# into another.
# ---------------------------------------------------------------------------

def _block_chain_spec() -> dict[str, Any]:
    """A `Fixed` two-block chain: `Start -> A -> J -> B -> C -> End`, with `B`
    depending on block `A`.

    Three blocks, and `C` is a **bystander** -- matched by nothing. Both
    dispatch loops re-examine their `dep_type`/`dependency_num` filter once per
    block, and a bystander in the same pass is the only way to tell "the filter
    skipped this one" apart from "the loop stopped at the first match": every
    test below asserts exactly one recorded transition while `C` is in the
    list, so a filter that returned early would show up. It is also what makes
    the loops' back-edges real -- an arm that falls through returns to the
    `for` line with work still to do.

    `DepIdle = 0%` throughout: the deterministic `100%` short-circuit in
    `draw_dependency_action` would make the mixed-percentage arms
    unreachable, and those arms are the subject. Every call below that reaches
    them supplies its own `draw_action`.
    """
    mixed = {"dep_idle": 0.0, "dep_nothing": 0.0, "dep_pm": 0.0, "dep_fail": 0.0}
    blocks = [_block("A", 50.0),
              _block("B", 200.0, dep_type="RBDBlock",
                     dep_num=_id_after(0, [{"name": "A"}], "A"), **mixed),
              _block("C", 200.0)]
    return {
        "system": _system(),
        "blocks": blocks,
        "nodes": [{"name": "Start", "type": 1, "n": 0, "out": 1},
                  {"name": "J", "type": 2, "n": 1, "out": 1, "k": 1},
                  {"name": "End", "type": 3, "n": 1, "out": 0}],
        "links": [{"from": "Start", "to": "A"}, {"from": "A", "to": "J"},
                  {"from": "J", "to": "B"}, {"from": "B", "to": "C"},
                  {"from": "C", "to": "End"}],
    }


def _cold_followers_spec() -> dict[str, Any]:
    """`B` follows node `T`; nodes `C` and `D` both follow `T` too, and only
    `C` is cold.

    One block dependent on a node, and **two** node dependents on the same
    target, is the minimum shape that shows the difference
    `_back_flow_node_dependents` makes between a cold follower and an ordinary
    one: `C` gets `higher_cold` set (which is what later flips `RunColdLogic`
    to its `ReconfigureNode(0,0,0,0,0)` arm and suppresses all switching), and
    `D` does not. One dependent node would have shown only half of it.

    `C` and `D` depend on `T` rather than on each other, on purpose:
    `_recurse_node_backflow` carries no visited set, so a pair of nodes that
    follow each other would recurse until the interpreter's stack ran out.
    This fixture keeps that graph acyclic; see the module docstring.

    `B` is set to the deterministic `DepIdle = 100%` so that a node-sourced
    `Down` needs no `draw_action` callback: what is under test is the *node*
    half, and a mixed-percentage block in the way would need a second subject.
    """
    nodes = [
        {"name": "Start", "type": 1, "n": 0, "out": 2},
        {"name": "T", "type": 2, "n": 1, "out": 2, "k": 1},
        {"name": "C", "type": 2, "n": 1, "out": 1, "k": 1, "cold": 1,
         "k_star": 1, "pr": 0, "autos": 0, "dep_type": "RBDNode"},
        {"name": "D", "type": 2, "n": 1, "out": 1, "k": 1, "dep_type": "RBDNode"},
        {"name": "End", "type": 3, "n": 1, "out": 0},
    ]
    target = _id_after(2, nodes, "T")
    nodes[2]["dep_num"] = target
    nodes[3]["dep_num"] = target
    return {
        "system": _system(),
        "blocks": [
            _block("A", 50.0),
            _block("B", 200.0, dep_type="RBDNode", dep_num=target,
                   dep_idle=100.0, dep_nothing=0.0, dep_pm=0.0, dep_fail=0.0),
        ],
        "nodes": nodes,
        "links": [{"from": "Start", "to": "A"}, {"from": "Start", "to": "B"},
                  {"from": "A", "to": "T"}, {"from": "B", "to": "D"},
                  {"from": "T", "to": "C"}, {"from": "C", "to": "End"},
                  {"from": "D", "to": "End"}],
    }


def _node_dependent_spec() -> dict[str, Any]:
    """`Start -> A -> T -> End` with `B` also feeding `End`, and `B` following
    node `T`.

    The smallest model whose block can be dispatched by
    `back_flow_dependencies_from_node`, and the one whose `repair_completion_
    status` node branches need: `T` is a real relay node with a real
    `in_paths_array`, and the model's End node is the `end_node_id` every one
    of those calls needs.
    """
    mixed = {"dep_idle": 0.0, "dep_nothing": 0.0, "dep_pm": 0.0, "dep_fail": 0.0}
    nodes = [{"name": "Start", "type": 1, "n": 0, "out": 2},
             {"name": "T", "type": 2, "n": 1, "out": 2, "k": 1},
             {"name": "End", "type": 3, "n": 1, "out": 0}]
    return {
        "system": _system(),
        "blocks": [
            _block("A", 50.0),
            _block("B", 200.0, dep_type="RBDNode",
                   dep_num=_id_after(2, nodes, "T"), **mixed),
        ],
        "nodes": nodes,
        "links": [{"from": "Start", "to": "A"}, {"from": "Start", "to": "B"},
                  {"from": "A", "to": "T"}, {"from": "B", "to": "End"},
                  {"from": "T", "to": "End"}],
    }


def _event_dependent_spec() -> dict[str, Any]:
    """`Start -> A -> E1 -> End` with `B` feeding `End`, and `B` following
    event `E1`.

    `ER3-08`'s shape: a block's dependence may name an *event*, and
    `repair_completion_status` then keys on the event's own `activeStatus` and
    `opStatus` instead of a block's. The event sits in series between `A` and
    End so the loader has somewhere to hang its outgoing link.
    """
    mixed = {"dep_idle": 0.0, "dep_nothing": 0.0, "dep_pm": 0.0, "dep_fail": 0.0}
    nodes = [{"name": "Start", "type": 1, "n": 0, "out": 2},
             {"name": "End", "type": 3, "n": 1, "out": 0}]
    events = [{"name": "E1", "dist": FIXED, "failVal": 1.0}]
    return {
        "system": _system(),
        "blocks": [
            _block("A", 50.0),
            _block("B", 200.0, dep_type="RBDEvent",
                   dep_num=_id_after(4, events, "E1"), **mixed),
        ],
        "nodes": nodes,
        "links": [{"from": "Start", "to": "A"}, {"from": "A", "to": "E1"},
                  {"from": "E1", "to": "End"},
                  {"from": "Start", "to": "B"}, {"from": "B", "to": "End"}],
        "events": events,
    }


def _mixed_followers_spec() -> dict[str, Any]:
    """`A -> T -> End` and `B -> U -> End` and `C -> T -> End`, with `B`
    following node `U` and `C` following block `A`.

    `resend_back_flow_would_apply` is the one function that answers a question
    about the *whole model* -- "would a resend have any effect right now?" --
    so its guards are only visible with a model that has one dependent of each
    kind at once: a block-dependent (`C` on `A`), a node-dependent on an up
    node (`B` on `U`), and a node that could be the down-like one (`T`, which
    nobody follows). Block order matters, because the function returns at the
    *first* pending dependent it finds: `C` sits behind `B` so that `B`'s
    target is read and found wanting first.
    """
    mixed = {"dep_idle": 0.0, "dep_nothing": 0.0, "dep_pm": 0.0, "dep_fail": 0.0}
    nodes = [{"name": "Start", "type": 1, "n": 0, "out": 3},
             {"name": "T", "type": 2, "n": 1, "out": 1, "k": 1},
             {"name": "U", "type": 2, "n": 1, "out": 1, "k": 1},
             {"name": "End", "type": 3, "n": 1, "out": 0}]
    return {
        "system": _system(),
        "blocks": [
            _block("A", 50.0),
            _block("B", 200.0, dep_type="RBDNode",
                   dep_num=_id_after(3, nodes, "U"), **mixed),
            _block("C", 200.0, dep_type="RBDBlock",
                   dep_num=_id_after(0, [{"name": "A"}], "A"), **mixed),
        ],
        "nodes": nodes,
        "links": [{"from": "Start", "to": "A"}, {"from": "Start", "to": "B"},
                  {"from": "Start", "to": "C"},
                  {"from": "A", "to": "T"}, {"from": "B", "to": "U"},
                  {"from": "C", "to": "T"},
                  {"from": "T", "to": "End"}, {"from": "U", "to": "End"}],
    }


# ---------------------------------------------------------------------------
# Through the engine
# ---------------------------------------------------------------------------

def test_a_hundred_percent_go_to_pm_reaction_leaves_a_block_that_does_not_use_pm_running() -> None:
    """A follower set to 100% go-to-PM, which does not use PM, never reacts at all.

    Why: the `GoToPM` arm's inner guard is the source's own
    `usesPM AND NOT(pmMisDefer AND missionStatus=Mission AND activePhases>0)`
    around `SetInterruptReason("OBPM")`. Phasing is
    out of scope, so `activePhases` is always 0, the inner `NOT` collapses, and
    what is left is `usesPM` -- so a block that does not use PM **silently
    consumes the reaction**. The draw is not wasted: `DepPMPerc = 100.0`
    short-circuits `draw_dependency_action` before it draws, which is why this
    run costs zero stream draws.

    Setup: 2 blocks into a 1-of-2 `J`. `A` fails at 50 and is repaired at 60.
    `B` follows `A` with `DepIdle=0%`, `DepPM=100%`, no `usesPM`. `Fixed`
    throughout, so the run is exact.

    Expected: `draws == 0`, and the timeline exactly as below -- `B` has no
    row at all between `Good` and its `Done`, so the reaction was applied to
    nothing.

    Source: raptor/core/engine/dependency.py:377-383 (the `GoToPM` arm's
    `uses_pm` guard) (`draw_dependency_action`'s deterministic
    `100.0` short-circuits, which is why there is no draw); source
    Raptor 7 as quoted in the port's own comment
    """
    spec = {
        "system": _system(),
        "blocks": [
            _block("A", 50.0),
            _block("B", 200.0, dep_type="RBDBlock", dep_num=1, dep_idle=0.0,
                   dep_nothing=0.0, dep_pm=100.0, dep_fail=0.0),
        ],
        "nodes": [{"name": "Start", "type": 1, "n": 0, "out": 2},
                  {"name": "J", "type": 2, "n": 1, "out": 1, "k": 1},
                  {"name": "End", "type": 3, "n": 1, "out": 0}],
        "links": [{"from": "Start", "to": "A"}, {"from": "Start", "to": "B"},
                  {"from": "A", "to": "J"}, {"from": "B", "to": "J"},
                  {"from": "J", "to": "End"}],
    }
    run = harness.run(spec)
    assert run.draws == 0
    assert_timeline(run, [
        (0.0, "Block", "A", "--", "Good"),
        (0.0, "Block", "B", "--", "Good"),
        (50.0, "Block", "A", "Good", "Failed"),
        (50.0, "Block", "A", "Failed", "Repairing"),
        (50.0, "Node", "J", "--", "Degraded"),
        (50.0, "System", "State_change", "--", "Yellow"),
        (60.0, "Block", "A", "Repairing", "Repaired"),
        (60.0, "Block", "A", "Repaired", "Good"),
        (60.0, "Node", "J", "Degraded", "Good"),
        (60.0, "System", "State_change", "Yellow", "Green"),
        (100.0, "Block", "A", "Good", "Done"),
        (100.0, "Block", "B", "Good", "Done"),
        (100.0, "System", "Phase_change", "--", "Mission_success"),
        (100.0, "System", "Simulation_Terminate", "--", "--"),
    ], where="go-to-PM with uses_pm off")


# ---------------------------------------------------------------------------
# The block-sourced dispatch (`back_flow_dependencies`)
# ---------------------------------------------------------------------------

def test_a_resend_goes_idle_on_the_default_reaction_when_the_block_defaults_to_idle() -> None:
    """A resend with nothing recorded replays `defDepStateIdle` and idles the block.

    Why: `ResendBackFlow` does not re-draw -- it replays whichever action the
    *previous* dispatch decided (`prevDepState`). Three cases, and this is the
    third: nothing recorded at all (`prevDepState = ""`) plus
    `defDepStateIdle` set means the same thing the first time round would have
    meant, so the block goes Idle. The two cases either side of it are the
    `GoIdle` replay and the dead end, and they are what makes the third worth
    pinning.

    Setup: `A`/`B`/`C`, `B` follows `A` at 0% idle so the resend really is a
    resend. Arranged: `B.run_state.previous_dependency_state = ""`, `B.def_dep_state_idle = True`.
    `special_back_flow=True`, `change_type="Down"`.

    Expected: exactly one transition, `("B", "Idle", "Dependence", 12.0, False)`.

    Source: raptor/core/engine/dependency.py:334-337 (the `previous_dependency_state == ""
    and def_dep_state_idle` arm), porting Raptor 7's
    `IF specialBackFlow` branch reached from Raptor 7
    """
    model = _load(_block_chain_spec())
    blocks = list(model.blocks_by_id.values())
    log: list[_Log] = []
    _arrange(_block_by_name(model, "B"), def_dep_state_idle=True)
    dep.back_flow_dependencies("Down", _block_by_name(model, "A"), blocks,
                              _recorder(log), AT, special_back_flow=True)
    assert log == [_Log("B", "Idle", "Dependence", AT, False)]


def test_a_resend_is_skipped_while_the_dependent_is_still_mid_repair_completion() -> None:
    """A resend does nothing at all while `returnFromMaint` is still set.

    Why: the source's trailing (implicit) `ELSE` of the `specialBackFlow`
    branch covers `returnFromMaint` *and* every `prevDepState` other than
    `"GoIdle"`/`""`, because those reactions were already fully applied the
    first time. A block that is still mid-repair-completion is in that set, so
    a resend must not re-idle it on top of the transition already applied --
    and it must not touch its recorded state either.

    Setup: same model. Arranged: `B.run_state.returning_from_maintenance = True`. `B`'s
    `previous_dependency_state` is left at `""` and `def_dep_state_idle` at its file
    value (`False`), so the arm is reached for the `returnFromMaint` reason
    and not the other one.

    Expected: no transition at all, and `B.run_state.previous_dependency_state` still `""` --
    nothing was recorded either.

    Source: raptor/core/engine/dependency.py:332-333 (the `returning_from_maintenance`
    skip inside the `special_back_flow` branch), porting Raptor 7
    """
    model = _load(_block_chain_spec())
    log: list[_Log] = []
    follower = _block_by_name(model, "B")
    _arrange(follower, run_state_returning_from_maintenance=True)
    dep.back_flow_dependencies("Down", _block_by_name(model, "A"),
                              list(model.blocks_by_id.values()),
                              _recorder(log), AT, special_back_flow=True)
    assert log == []
    assert follower.run_state.previous_dependency_state == ""


def test_a_repair_completion_recheck_is_abandoned_while_an_induced_failure_is_still_pending() -> None:
    """A pending interrupt from the first dispatch stops the recheck dead.

    Why: this is the `returnFromMaint` arm, and it is gated on
    `tempBlock.InterruptReason = ""` -- if a previous dispatch's own wake
    (`"Induced_Failure"` or `"OBPM"`) has not been consumed by the block's own
    process yet, the source does **nothing at all**: no transition, no
    clearing of `returnFromMaint`, not even a log line. `ReleaseProbe32` run 3
    at t=190.345389 is the real case, where a pending
    `"Obtained_PM_Prereqs"` wake shields the block.

    Setup: same model. Arranged: `B.run_state.returning_from_maintenance = True` **and**
    `B.run_state.interrupt_reason = "Induced_Failure"`.

    Expected: no transition, and `returning_from_maintenance` still `True` afterwards --
    the flag is deliberately *not* cleared on this path.

    Source: raptor/core/engine/dependency.py:340-351 (the guard and its
    comment, porting)
    """
    model = _load(_block_chain_spec())
    log: list[_Log] = []
    follower = _block_by_name(model, "B")
    _arrange(follower, run_state_returning_from_maintenance=True,
             run_state_interrupt_reason="Induced_Failure")
    dep.back_flow_dependencies("Down", _block_by_name(model, "A"),
                              list(model.blocks_by_id.values()),
                              _recorder(log), AT)
    assert log == []
    assert follower.run_state.returning_from_maintenance is True


def test_a_repair_completion_recheck_goes_idle_on_a_hundred_percent_idle_reaction() -> None:
    """A recheck with `DepIdle = 100%` goes Idle, with an empty reason and no wake.

    Why: the source's arm is a state change **only** -- `ChangeBlockState
    (Idle, activeStatus, "")` with no `Interrupt(Run)` beside it, because the
    block's fail-wait keeps running while it is logged Idle. The empty reason
    string is therefore load-bearing (it is what ends up as the log's
    `change_block_state` fallback), and so is `no_wake_once`, which is how the
    port suppresses a wake it must not deliver. Both are in the literal.

    Setup: same model. Arranged: `B.run_state.returning_from_maintenance = True`,
    `B.dep_idle_percent = 100.0`.

    Expected: exactly one transition,
    `("B", "Idle", "", 12.0, True)` -- `no_wake` `True` *inside* the call --
    and the block left clean afterwards: `returning_from_maintenance` `False` and
    `no_wake_once` `False`.

    Source: raptor/core/engine/dependency.py:352-361 (the `no_wake_once` set,
    the `DepIdlePerc=100.0 or defDepStateIdle` test, and the two resets),
    porting Raptor 7
    """
    model = _load(_block_chain_spec())
    log: list[_Log] = []
    follower = _block_by_name(model, "B")
    _arrange(follower, run_state_returning_from_maintenance=True, dep_idle_percent=100.0)
    dep.back_flow_dependencies("Down", _block_by_name(model, "A"),
                              list(model.blocks_by_id.values()),
                              _recorder(log), AT)
    assert log == [_Log("B", "Idle", "", AT, True)]
    assert (follower.run_state.returning_from_maintenance, follower.run_state.no_wake_once) == (False, False)


def test_a_repair_completion_recheck_goes_back_to_running_when_neither_idle_reaction_is_set() -> None:
    """The same recheck with neither idle reaction set goes Running, not Idle.

    Why: the recheck arm has no mixed-percentage draw in it -- unlike the
    ordinary `Down` dispatch, which draws. It reads the same three
    `IF DepIdlePerc=100.0 / ELSE IF defDepStateIdle / ELSE` ladder the source
    uses, and with neither set the answer is `Running`. So a block whose
    dependence is configured to keep it running is re-established where it
    was, still without a wake.

    Setup: same model, with `B` left at the file's `DepIdle = 0%` and
    `defDepStateIdle` off. Arranged: `B.run_state.returning_from_maintenance = True`.

    Expected: exactly one transition, `("B", "Running", "", 12.0, True)`.

    Source: raptor/core/engine/dependency.py:355-358 (the `else` of the
    recheck's idleness ladder), porting Raptor 7
    """
    model = _load(_block_chain_spec())
    log: list[_Log] = []
    follower = _block_by_name(model, "B")
    _arrange(follower, run_state_returning_from_maintenance=True)
    dep.back_flow_dependencies("Down", _block_by_name(model, "A"),
                              list(model.blocks_by_id.values()),
                              _recorder(log), AT)
    assert log == [_Log("B", "Running", "", AT, True)]


def test_an_unrecognised_reaction_is_rejected_rather_than_ignored() -> None:
    """A reaction string outside the four raises instead of being dropped.

    Why: the four `Dep*Perc` fields only ever yield `"GoIdle"`,
    `"DoNothing"`, `"GoToPM"` or `"InducedFailure"`, so this needs a
    caller-supplied `draw_action` that returns something else -- which is what
    a caller bug would look like.

    **This test used to assert the opposite.** When this branch was written,
    the block-sourced dispatch had no `else` on its reaction chain: the value
    went to `prevDepState` and nothing else happened, silently, while the
    node-sourced twin raised -- two ports of one source method disagreeing
    about the same mistake. The test pinned that, with the divergence
    recorded as a finding. Main's deduplication collapsed the two copies into
    one, and in doing so **closed the hole**: there is now a single chain
    and it raises. The finding was right and the fix came from the other
    branch, which is the outcome one hopes for and not the usual one.

    Setup: same model. `draw_action` returns `"Sideways"`; `induce` would raise
    if called, and does not.

    Expected: `EngineInvariantError("Sideways")`, and no transition. The
    exception names the offending value, so the caller's bug is legible from
    the message rather than needing a debugger.

    Source: raptor/core/engine/dependency.py:397 (`else: raise
    EngineInvariantError(action)`), the arm the duplicated chain lacked
    """
    model = _load(_block_chain_spec())
    log: list[_Log] = []
    with pytest.raises(EngineInvariantError, match="Sideways"):
        dep.back_flow_dependencies(
            "Down", _block_by_name(model, "A"),
            list(model.blocks_by_id.values()), _recorder(log), AT,
            draw_action=lambda block, sim_time: "Sideways",
            induce=lambda block, reason: pytest.fail(
                f"induce called with {reason!r}"))
    assert log == []


def test_a_block_sourced_trigger_outside_the_declared_set_is_a_silent_no_op() -> None:
    """A `change_type` that is not one of the three declared triggers does nothing.

    Why: `back_flow_dependencies` is an `if NodeStandby / if Down / elif AllUp`
    chain with **no `else`**, so anything else falls off the end of the loop
    body and the function returns having done nothing -- no transition, no
    error. The engine cannot produce this (`node_dependency_trigger` and
    `back_flow_dependency_trigger` both return only the three, or `None`, and
    every caller tests for `None`), so it is a caller-bug hole rather than a
    behaviour, and it is pinned as-is. The node-sourced twin behaves the same
    way; only the *reaction* chain downgrades a mistake to silence rather than
    raising.

    Setup: same model, `change_type="Sideways"`.

    Expected: no transition, and the follower left with `previous_dependency_state`
    still `""`.

    Source: raptor/core/engine/dependency.py:305-401 (the whole
    `NodeStandby`/`Down`/`AllUp` chain, with no `else` at either level),
    against the `else: raise`
    """
    model = _load(_block_chain_spec())
    log: list[_Log] = []
    follower = _block_by_name(model, "B")
    dep.back_flow_dependencies("Sideways", _block_by_name(model, "A"),
                              list(model.blocks_by_id.values()),
                              _recorder(log), AT)
    assert log == []
    assert follower.run_state.previous_dependency_state == ""


# ---------------------------------------------------------------------------
# The node-sourced dispatch (`back_flow_dependencies_from_node`) and its
# node-sourced half (`_back_flow_node_dependents`)
# ---------------------------------------------------------------------------

def test_a_node_standby_dispatch_skips_a_dependent_that_is_neither_running_nor_idle() -> None:
    """A dependent that is Repairing is left alone by a node entering Standby.

    Why: the same gate as the `Down` arm, and it is the documented reason
    `Repairing -> Standby`, `RepHold -> Standby` and `PM -> Standby` are not
    in the corpus: the dispatch is
    `IF tempBlock.opStatus IN (Running, Idle)`, so a
    follower in any other state is passed over. This is the ladder's own
    recorded finding, pinned from the other side.

    Setup: the `_cold_followers_spec` model -- `B` follows node `T`. Arranged:
    `B.op_status = Repairing`; `T` dispatches `NodeStandby`.

    Expected: no transition on the block loop. The *node* half still runs, and
    cold follower `C` gets its `higher_cold` flag -- which is asserted
    separately, in the cold-follower tests, so that each test owns one claim.

    Source: raptor/core/engine/dependency.py:510-516 (the `NodeStandby` block
    loop and its `op_status` gate), porting Raptor 7
    """
    model = _load(_cold_followers_spec())
    log: list[_Log] = []
    follower = _block_by_name(model, "B")
    _arrange(follower, op_status=BlockStatus.REPAIRING)
    _dispatch(model, "NodeStandby", log)
    assert log == []


def test_a_node_sourced_repair_completion_recheck_is_abandoned_by_a_pending_interrupt() -> None:
    """The node-sourced recheck has the same pending-interrupt guard as the block one.

    Why: the two twins carry the same `IF tempBlock.InterruptReason = ""` guard
    in front of their `returnFromMaint` arms -- `dependency.py:350-351` for the
    block loop for the node loop, both porting
    Raptor 7. Two ports of one statement, and only one of them
    was held.

    Setup: the `_cold_followers_spec` model, `B` following node `T`. Arranged:
    `B.run_state.returning_from_maintenance = True`, `B.run_state.interrupt_reason = "OBPM"`.

    Expected: no transition, and `returning_from_maintenance` still `True`.

    Source: raptor/core/engine/dependency.py:542-554 (the node twin's guard
    and the `PhasedFailSimOrderProbe` / `ReleaseProbe32` cases its comment
    cites), porting Raptor 7
    """
    model = _load(_cold_followers_spec())
    log: list[_Log] = []
    follower = _block_by_name(model, "B")
    _arrange(follower, run_state_returning_from_maintenance=True, run_state_interrupt_reason="OBPM")
    _dispatch(model, "Down", log)
    assert log == []
    assert follower.run_state.returning_from_maintenance is True


def test_an_all_up_dispatch_from_a_node_resumes_its_follower_and_then_returns() -> None:
    """An `AllUp` from a node promotes an Idle follower, then the loop ends cleanly.

    Why: Raptor 7's `AllUp` member loop gives both `Idle` and
    `Standby` followers a direct `ChangeBlockState(Running, activeStatus,
    "Dependence")` -- no draw, no percentage -- and it is the only one of the
    three triggers that promotes a follower without consulting a single
    percentage, because everything that has to come back up comes back up. The
    gate is written `(Idle, Standby)` rather than `Idle`, so a follower the
    `NodeStandby` arm put to sleep is resumed by this arm too.

    Setup: the `_cold_followers_spec` model, `B` following node `T`. Arranged:
    `B.op_status = Idle`; `T` dispatches `AllUp`.

    Expected: exactly one transition,
    `("B", "Running", "Dependence", 12.0, False)`.

    Source: raptor/core/engine/dependency.py:619-628 (the `AllUp` member loop),
    porting Raptor 7
    """
    model = _load(_cold_followers_spec())
    log: list[_Log] = []
    follower = _block_by_name(model, "B")
    _arrange(follower, op_status=BlockStatus.IDLE)
    _dispatch(model, "AllUp", log)
    assert log == [_Log("B", "Running", "Dependence", AT, False)]


def test_a_node_sourced_trigger_outside_the_declared_set_is_a_silent_no_op() -> None:
    """The node-sourced dispatch is as silent as the block one about a bad trigger.

    Why: same finding as the block twin's, on the other function, and it
    matters precisely because they are twins: one source method
    (`RBDBasicObj.BackFlowDependencies`) backs both, so a disagreement between
    them is a porting defect waiting to happen rather than two design
    decisions. Both drop a bad `change_type`; only the node twin's `Down` arm
    has an `else: raise` for a bad *reaction*.

    Setup: the `_cold_followers_spec` model, `change_type="Sideways"` on node
    `T`. `B` is Running, so the block loop's own gate would have matched it if
    the trigger had been one of the three.

    Expected: no transition -- the node half runs but finds no node follower
    of `T` for this trigger, so nothing else moves either.

    Source: raptor/core/engine/dependency.py:510-628 (the three-arm chain with
    no `else`), against the `else: raise EngineInvariantError`
    """
    model = _load(_cold_followers_spec())
    log: list[_Log] = []
    _dispatch(model, "Sideways", log)
    assert log == []


def test_a_down_dispatch_sets_the_cold_flag_on_the_cold_follower_node_only() -> None:
    """A `Down` puts a *cold* follower node into cold logic and leaves an ordinary one out.

    Why: `higherCold` is what flips `RunColdLogic` to its
    `ELSE ReconfigureNode(0,0,0,0,0)` arm -- ordinary K-of-N aggregation, no
    `Too_many_on` demotion, no `SetSwitchTime` draw -- until the target
    recovers. So the flag belongs to the *cold*
    follower alone: an ordinary node following the same target gets the
    dispatch and nothing else, which is the whole reason the model has two
    follower nodes.

    Setup: the `_cold_followers_spec` model. `C` is cold and follows `T`;
    `D` is ordinary and follows `T`. `T` dispatches `Down` with `B` Running.

    Expected: `B` goes Idle, `C.higher_cold` becomes `True`, and
    `D.higher_cold` stays `False`.

    Source: raptor/core/engine/dependency.py:697-704 (the `Down` branch of
    `_back_flow_node_dependents`, whose `if dep_node.cold_standby` is the only
    thing between the two outcomes), porting Raptor 7
    """
    model = _load(_cold_followers_spec())
    log: list[_Log] = []
    cold, ordinary = _node_by_name(model, "C"), _node_by_name(model, "D")
    _dispatch(model, "Down", log)
    assert log == [_Log("B", "Idle", "Dependence", AT, False)]
    assert (cold.higher_cold, ordinary.higher_cold) == (True, False)


def test_a_node_standby_dispatch_also_puts_the_cold_follower_node_into_cold_logic() -> None:
    """`NodeStandby` raises `higherCold` on the cold follower too, not only on `Down`.

    Why: the source has **three** `NodeDepGroup` loops, one per trigger, and
    two of the three set `higherCold` -- `Down` and
    `NodeStandby`. Only `AllUp` clears it. So the flag is not a
    "something went down" marker, it is a "something upstream of me is not
    there" marker, and a standby follower must see it too.

    Setup: the `_cold_followers_spec` model, as above, with `T` dispatching
    `NodeStandby` and `B` Running (so the block loop promotes it to Standby --
    that transition belongs to the guard test, so it is not asserted here).

    Expected: `C.higher_cold` becomes `True` and `D.higher_cold` stays
    `False`.

    Source: raptor/core/engine/dependency.py:715-722 (the `NodeStandby` branch
    of `_back_flow_node_dependents`), porting Raptor 7
    """
    model = _load(_cold_followers_spec())
    log: list[_Log] = []
    cold, ordinary = _node_by_name(model, "C"), _node_by_name(model, "D")
    _dispatch(model, "NodeStandby", log)
    assert (cold.higher_cold, ordinary.higher_cold) == (True, False)


def test_an_all_up_dispatch_leaves_the_cold_flag_alone_when_it_was_never_raised() -> None:
    """`AllUp` clears `higherCold` only if something had set it.

    Why: Raptor 7 is `SetHigherCold(FALSE) if set, recurse,
    then RunColdLogic if cold`. The `if set` is a real test rather than an
    unconditional clear, so a follower that was never in cold logic comes out
    of `AllUp` with nothing written to it -- which is observable only when a
    model has a follower that was never raised, i.e. the ordinary `D` here.

    Setup: the `_cold_followers_spec` model, `T` dispatching `AllUp`, with
    both `C.higher_cold` and `D.higher_cold` at their file values (`False`).
    `B` is Idle, so the block loop promotes it.

    Expected: `B` goes Running and **both** follower nodes' `higher_cold`
    flags are still `False`.

    Source: raptor/core/engine/dependency.py:705-712 (the `AllUp` branch,
    whose `if dep_node.higher_cold` is the test), porting
    """
    model = _load(_cold_followers_spec())
    log: list[_Log] = []
    cold, ordinary = _node_by_name(model, "C"), _node_by_name(model, "D")
    follower = _block_by_name(model, "B")
    _arrange(follower, op_status=BlockStatus.IDLE)
    _dispatch(model, "AllUp", log)
    assert log == [_Log("B", "Running", "Dependence", AT, False)]
    assert (cold.higher_cold, ordinary.higher_cold) == (False, False)


def test_an_all_up_dispatch_offers_the_cold_survey_to_the_cold_follower_node_only() -> None:
    """`AllUp` offers the cold survey to the cold follower and to nobody else.

    Why: the source's `AllUp` node loop ends with `ASK tempNode TO
    RunColdLogic`, which in this port is the caller-supplied
    `run_cold_logic_now` -- the one step the dispatch cannot do itself,
    because only the orchestrator can drive a full cold survey plus its
    cascade. It is offered **per follower node**, and only when that node is
    cold, so the two follower nodes in this model are what separate "offered"
    from "not offered". Omit the callback (the default) and the step is
    skipped entirely rather than raising -- which the previous test shows.

    Setup: the `_cold_followers_spec` model, `C.higher_cold` arranged `True`
    (so the clearing arm is taken), `T` dispatching `AllUp`, `B` Idle.

    Expected: `C.higher_cold` is back to `False` and the survey was offered
    once, to `C`, whose name is the only thing recorded.

    Source: raptor/core/engine/dependency.py:705-714 (the `AllUp` branch's
    `if dep_node.cold_standby and run_cold_logic_now is not None`), porting
    Raptor 7's trailing `ASK tempNode TO RunColdLogic`
    """
    model = _load(_cold_followers_spec())
    log: list[_Log] = []
    surveys: list[str] = []
    cold = _node_by_name(model, "C")
    cold.higher_cold = True
    follower = _block_by_name(model, "B")
    _arrange(follower, op_status=BlockStatus.IDLE)
    _dispatch(model, "AllUp", log,
                     on_cold=lambda node: surveys.append(node.node_name))
    assert log == [_Log("B", "Running", "Dependence", AT, False)]
    assert cold.higher_cold is False
    assert surveys == ["C"]


# ---------------------------------------------------------------------------
# `repair_completion_status` -- the other, independent mechanism
# ---------------------------------------------------------------------------

def test_a_repair_completed_against_a_linked_block_returns_the_block_to_running() -> None:
    """A repair finishing while what it follows is `Linked` gives Running.

    Why: `DetermineNewState`'s "Maintenance Completed" handler keys on the
    depended-upon block's **active status first** (:
    `Linked / Cut / else`), before it looks at any operational state at all.
    `Linked` means the block is out of the diagram but still delivering flow,
    which the source counts as *up*, so the follower runs. `Cut` is the
    opposite and is the next test.

    Setup: the `_block_chain_spec` model -- `B` follows block `A`. Arranged:
    `A.active_status = Linked`; `B` is Active with `DepIdle = 0%` and no
    `defDepStateIdle`, so the `else` arms cannot rescue it.

    Expected: `("Running", False)` -- apply and stop, no re-check.

    Source: raptor/core/engine/dependency.py:802-807 (the `dep_type == "RBDBlock"`
    branch's `Linked` arm), porting Raptor 7
    """
    model = _load(_block_chain_spec())
    followed, follower = _block_by_name(model, "A"), _block_by_name(model, "B")
    followed.active_status = ActiveStatus.LINKED
    status, recheck = dep.repair_completion_status(
        follower, list(model.blocks_by_id.values()))
    assert (status.value, recheck) == ("Running", False)


def test_a_repair_completed_against_a_cut_block_runs_on_when_no_idle_reaction_is_set() -> None:
    """A repair finishing while what it follows is `Cut` reads the two idle flags directly.

    Why: with `Cut`, the source reaches its own three-way ladder
    (`DepIdlePerc=100.0` -> Idle, `ELSE IF defDepStateIdle` -> Idle, `ELSE`
    Running) **without drawing** -- unlike the
    `Down` dispatch, which draws a fresh mixed-percentage action here. With
    neither flag set the answer is `Running`, and that is the half of the
    ladder the corpus models never reach because `Cut` needs per-element phase
    state.

    Setup: the same model. Arranged: `A.active_status = Cut`; `B` Active with
    `DepIdle = 0%` and `defDepStateIdle` off.

    Expected: `("Running", False)`.

    Source: raptor/core/engine/dependency.py:808-813 (the `Cut` arm and its
    `dep_idle_percent == 100.0 or def_dep_state_idle` ternary), porting
    """
    model = _load(_block_chain_spec())
    followed, follower = _block_by_name(model, "A"), _block_by_name(model, "B")
    followed.active_status = ActiveStatus.CUT
    status, recheck = dep.repair_completion_status(
        follower, list(model.blocks_by_id.values()))
    assert (status.value, recheck) == ("Running", False)


def test_a_repair_completed_against_a_cut_event_runs_on_when_no_idle_reaction_is_set() -> None:
    """The same `Cut` ladder, keyed on an event's active status instead of a block's.

    Why: `ER3-08` -- a block may depend on an *event*
    (`dep_type="RBDEvent"`), and the `Maintenance Completed` handler then reads
    the event's `activeStatus`/`opStatus` in place of a block's.
   The port carries it as a separate branch rather
    than sharing the block one, so the `Cut` arm is a second copy of the same
    three-way ladder and needed its own hold.

    Setup: the `_event_dependent_spec` model -- `B` follows event `E1`.
    Arranged: `E1.active_status = Cut`; `B` Active with `DepIdle = 0%` and
    `defDepStateIdle` off.

    Expected: `("Running", False)`.

    Source: raptor/core/engine/dependency.py:825-837 (the `dep_type == "RBDEvent"`
    branch's `Cut` arm), porting Raptor 7
    """
    model = _load(_event_dependent_spec())
    event = next(e for e in model.events_by_id.values() if e.name == "E1")
    event.active_status = ActiveStatus.CUT
    status, recheck = dep.repair_completion_status(
        _block_by_name(model, "B"), list(model.blocks_by_id.values()),
        all_events=list(model.events_by_id.values()))
    assert (status.value, recheck) == ("Running", False)


def test_a_block_that_follows_a_node_and_was_given_no_node_is_a_caller_error() -> None:
    """A node-dependent block handed no `dep_node` raises rather than guessing.

    Why: `repair_completion_status` needs the depended-upon node's live
    status, and there is no correct default for it -- `AllUp` and `Down` give
    opposite answers -- so the port raises instead. The guard is only
    reachable when the caller resolved a node-based dependency and then failed
    to pass the node on, which is a caller bug; this pins that it is loud.

    Setup: the `_node_dependent_spec` model -- `B` follows node `T` by id, so
    `resolve_dependency_node_id` resolves it. `dep_node` is not passed.

    Expected: `ValueError` carrying exactly
    `"block has a node-based dependency but no dep_node was provided"`.

    Source: raptor/core/engine/dependency.py:846-852 (the node-dependent
    test and its `dep_node is None` guard), porting Raptor 7's
    node branch
    """
    model = _load(_node_dependent_spec())
    with pytest.raises(
            ValueError,
            match=r"^block has a node-based dependency but no dep_node was provided$"):
        dep.repair_completion_status(_block_by_name(model, "B"),
                                     list(model.blocks_by_id.values()))


def test_a_repair_completed_against_a_standby_node_puts_the_block_into_standby() -> None:
    """A repair finishing while what it follows is in `NodeStandby` gives Standby.

    Why: Raptor 7 -- a node that has been switched off to
    standby puts the block finishing its repair into standby as well, so the
    follower does not come back up into a path that is not carrying flow. Note
    this test sits **after** the `higherCold` arm in the source's priority
    order, so a node that is *both* standby and `higherCold` is decided by
    `higherCold` first (which gives the same answer here, and is why the two
    arms are separately pinned).

    Setup: the `_node_dependent_spec` model. Arranged: `T.status = NodeStandby`,
    `T.higher_cold` left `False` so the arm under test is the one taken.

    Expected: `("Standby", False)` -- apply and stop, no re-check.

    Source: raptor/core/engine/dependency.py:862-873 (the `higher_cold` arm at
     and the `NodeStandby` arm), porting Raptor 7

    """
    model = _load(_node_dependent_spec())
    target = _node_by_name(model, "T")
    target.status = NodeStatus.STANDBY
    status, recheck = dep.repair_completion_status(
        _block_by_name(model, "B"), list(model.blocks_by_id.values()),
        dep_node=target)
    assert (status.value, recheck) == ("Standby", False)


def test_a_repair_completed_against_a_down_node_with_no_own_node_is_a_caller_error() -> None:
    """A down target node with no `own_node` raises, because the down count needs it.

    Why: the `GetDownNum` special case asks whether this block's own incoming
    path into its *own* node is the only thing keeping that node down
    (`down_num <= 1`). That question is about
    `own_node.in_paths_array`, not about the downstream target, so passing no
    `own_node` leaves the port with nothing to count -- and guessing would pick
    between `Running` and `Running`-then-`Idle`, which is exactly the decision
    the caller exists to make. `tests/unit/test_dependency.py` covers the
    `down_num > 1` and `down_num <= 1` outcomes; this covers the refusal.

    Setup: the `_node_dependent_spec` model. Arranged: `T.status = Down`, so the
    `GetDownNum` path is the one reached; `own_node` is not passed.

    Expected: `ValueError` carrying exactly
    `"block has a node-based dependency with the target node currently down, "
    "but no own_node was provided"`.

    Source: raptor/core/engine/dependency.py:875-877 (the `own_node is None`
    guard), porting Raptor 7
    """
    model = _load(_node_dependent_spec())
    target = _node_by_name(model, "T")
    target.status = NodeStatus.DOWN
    with pytest.raises(
            ValueError,
            match=r"^block has a node-based dependency with the target node "
                  r"currently down, but no own_node was provided$"):
        dep.repair_completion_status(_block_by_name(model, "B"),
                                     list(model.blocks_by_id.values()),
                                     dep_node=target)


# ---------------------------------------------------------------------------
# `resend_back_flow_would_apply` -- "would a resend do anything?"
# ---------------------------------------------------------------------------

def test_a_cut_block_keeps_its_follower_pending_a_back_flow() -> None:
    """A `Cut` block leaves its follower pending, even though a Cut block is `Active`.

    Why: `ResendBackFlow`'s block loop fires for a depended-upon block that is
    either **Cut** or Active-and-mid-maintenance,
   and the port's detector
    (`resend_back_flow_would_apply`) is what `block_lifecycle` calls instead of
    porting the resend itself. A Cut block is caught by the `Cut` test
    *before* the `Active and opStatus in ...` test, so a Cut block that is
    operationally `Running` still counts -- which is the case worth pinning,
    because the two tests together are the whole predicate.

    Setup: the `_mixed_followers_spec` model -- `C` follows block `A`.
    Arranged: `A.active_status = Cut`, `A.op_status` left `Running`, and
    `C.op_status = Running` so it is the kind of follower the final loop acts
    on.

    Expected: `True`.

    Source: raptor/core/engine/dependency.py:952-960 (the two down-like tests)
 (the follower loop that reports the effect), porting
    """
    model = _load(_mixed_followers_spec())
    followed, follower = _block_by_name(model, "A"), _block_by_name(model, "C")
    followed.active_status = ActiveStatus.CUT
    assert dep.resend_back_flow_would_apply(
        list(model.blocks_by_id.values()), model.nodes_by_id,
        model.links_by_id, _node_by_name(model, "End").id) is True
    assert follower.op_status == BlockStatus.RUNNING


def test_a_block_mid_maintenance_keeps_its_follower_pending_a_back_flow() -> None:
    """An `Active` block that is `Repairing` keeps its follower pending.

    Why: the other half of the same predicate -- `Active` **and**
    `opStatus IN (Repairing, PM, RepHold, PMhold)`. A block merely *Running* is
    not down-like and leaves nothing pending, which is the boundary the next
    test's contrast depends on. So the two middle states of a block's life --
    Cut and repairing -- are the two ways a follower becomes pending.

    Setup: the same model. Arranged: `A.active_status = Active` (the file
    value), `A.op_status = Repairing`, `C` Running.

    Expected: `True`.

    Source: raptor/core/engine/dependency.py:920-922
    (`_BLOCK_DOWN_LIKE_FOR_RESEND`) (the `Active and opStatus`
    test), porting Raptor 7
    """
    model = _load(_mixed_followers_spec())
    followed = _block_by_name(model, "A")
    followed.op_status = BlockStatus.REPAIRING
    assert dep.resend_back_flow_would_apply(
        list(model.blocks_by_id.values()), model.nodes_by_id,
        model.links_by_id, _node_by_name(model, "End").id) is True


def test_a_follower_of_an_up_node_is_not_pending_when_a_different_node_is_down() -> None:
    """A down node nobody follows leaves every follower un-pending, and the walk finishes.

    Why: the detector walks *every* dependent and returns at the first one that
    matches, so a follower of a node that is still `AllUp` has to be read and
    dismissed -- and the walk has to reach the end to return `False` at all.
    The model is built so that path is forced: `T` is down, but the only
    node-follower (`B`) follows `U`, which is up. That is the last branch of
    the function's final loop, and it is the one a model whose followers all
    point at the same down node never reaches.

    Setup: the same model, everything at its file values, `T.status` arranged
    to `Down` so `down_like_node_ids` is not empty on the node side.

    Expected: `False`.

    Source: raptor/core/engine/dependency.py:979-986 (the node-side down-like
    set and the early return) (the final follower's
    `target_id in down_like_node_ids` test and the `False` return)
    """
    model = _load(_mixed_followers_spec())
    _node_by_name(model, "T").status = NodeStatus.DOWN
    assert dep.resend_back_flow_would_apply(
        list(model.blocks_by_id.values()), model.nodes_by_id,
        model.links_by_id, _node_by_name(model, "End").id) is False