"""The non-block half of the behavioural corpus: node, hierarchy, event and
system transitions, each pinned from a model that reaches it.

`test_b_behaviour_coverage.py` is the gate that retires the recorded event-log
corpus as an oracle, and that gate counts an `(object_type, from -> to)` row
only when a committed `assert_timeline` literal asserts it. The ladder in
`test_b_ladder.py` owns the block column; this file owns the four object types
whose state machines *are* the cascade:

* a **node**'s status is the k-of-n aggregation of its incoming links, so every
  node state a model reaches is a claim about `determine_node_status`;
* a **hierarchy**'s status is that of its own Out marker node, logged under the
  hierarchy's name rather than the node's -- which is why a hierarchy can only
  be exercised by a model that has one (`test_b_hierarchy_status_is_its_out_markers_k_of_n`);
* an **event**'s state machine has three outcomes and a fourth pseudo-state,
  `Cut`, which only a phased model produces; and
* the **system**'s mission bookkeeping is a second log object (`Phase_change`)
  beside the colour one (`State_change`), so its transitions are chained
  separately from the Green/Yellow/Red rows.

The models here are deliberately small (rule B4: at most six blocks and five
nodes, one run, well under 100 ms) and every distribution is `Fixed` (rule B2),
so each run is exact and consumes no randomness -- asserted with
`result.draws == 0` in every test, not assumed.

**The literals are inline in the `assert_timeline` calls** even where that reads
badly, because the gate parses them with `ast` and can only see a literal that
is syntactically an argument of the call (see `test_b_ladder.py`'s module
docstring for the same decision, and the same cost).

**What this file does not reach, and why.** The corpus chains a log object's
`from` state across run boundaries as well as within one, so several of its rows
need two *runs* to appear, and `harness.run` reports run 1 only. Everything below
is in that category or is structurally out of reach of a model this size; each is
a claim about the engine rather than a gap in the suite, and the reconciler
should not read their absence as "not yet tried":

* `Event Armed -> Armed`, `Event Armed -> Cut`, `Event Armed -> Failed` -- the
  only two writers of an event's `Armed` state are the kickoff initialisation and
  `EndSimulation`'s closing sweep, and an event fires at kickoff, so it is never
  `Armed` at an instant when anything else happens. The same rows need the second
  run to be visible at all: the sweep writes `Armed` last, so run *n+1*'s kickoff
  is what produces `Armed -> Armed`.
* `System {Green,Yellow,Red} -> Mission` -- `State_change` writes `Mission` at
  one place only, the kickoff (`raptor/core/engine/kickoff.py:157-171`), so the
  colour has to be whatever the previous run ended on.
* `System Mission_{success,failure} -> Mission_{success,failure}` and
  `Mission_failure -> Mission_success` -- a mission that ends is always followed by
  a mission that begins, and the begin is written to the same log object, so the
  pairs can only be chained across a run boundary.
* `System Out_of_spares -> Out_of_spares` -- `ReconfigureSystem` writes that line
  only where an End node's active-path count drops to exactly `MinGoodPathsReq - 1`
  (`raptor/core/engine/cascade.py:177-191`), and `link.active` is a one-way
  ratchet within a run (`raptor/core/engine/run_control.py:65-73`), so one run can
  cross that threshold once.
* `Hier Bad -> Bad` and `Hier NodePM -> NodePM` -- a hierarchy's status is its Out
  marker's aggregation, and the marker aggregates the links *into* it; the corpus
  has no `Hier -> Degraded` row at all, which says the Out markers in it had a
  single incoming link, and with one link the link's status is a bijection with
  the marker's (`Up`/`Down`/`PM`/`Idle`/`Standby` -> `Good`/`Bad`/`NodePM`/`Idle`/
  `Standby`), so the marker can only re-log when its status actually moves.
* `Node -> Standby` (eleven rows) -- the only writers of a `LinkStandby` into a
  node's aggregation are a block transitioning to `Standby` and a block being
  put there by a dependency. A block reaches `Standby` from `run_cold_logic`
  (whose candidates' EFPA node *is* the cold node, and whose resurvey has no
  `Standby` arm at all, `raptor/core/engine/cold_standby.py:789-822`) or from a
  `Dependence_Standby` reaction, which needs the depended-on object to be in
  `Standby` or `higherCold`
  (`raptor/core/engine/dependency.py:421-431`). So the route is a
  cold group with an **RBDNode**-sourced candidate -- the one that sets
  `higher_cold` on switch-off (`cold_standby.py:630-643`) -- with a block
  downstream that node-depends on the inner node. That shape is not built here;
  it is the S8-of-a-second-level route, and it is the obvious next thing to try.
* `Node Bad -> Switch_Failed`, `Node NodePM -> Switch_Failed`,
  `Node Switch_Failed -> Degraded` -- a survey consumes **every** `"none"`
  candidate in the group, not just the one switch it asked for: the pass's
  `switchingPaths` counter is advanced only by an automatic or manual switch, so
  a `"none"` link does not stop the loop (t=10 in
  `test_an_unswitchable_cold_candidate_is_failed_permanently_and_the_group_carries_on`
  fails three in one pass, and t=40 in
  `test_a_switch_failure_is_written_from_the_groups_previous_status` fails two).
  So a group can be failed exactly once per run: it cannot be `Bad` or `NodePM`
  with an unswitchable candidate still standing, which is what the first two rows
  need, and the `from` of the one `Switch_Failed` a group does write is always a
  status at or above its `k`, which is what the third row needs.
"""
from __future__ import annotations

from typing import Any

from .harness import FIXED, assert_timeline, run

#: Every model here runs at least this long in hours; the shortest is the
#: 50-hour event fixture, the longest the 200-hour hierarchy one.
_EVENT_PHASE = 10.0

#: The mission fixture's phase table: 20 mission, 20 non-mission, 20 mission,
#: then three 10-hour phases of which the last is the mission the run ends in.
#: Six phases rather than two because every distinct `Phase_change` pairing
#: needs a boundary of its own -- see the two `test_a_mission_*` docstrings.
_MISSION_FLAGS = (1, 0, 1, 0, 0, 1)
_MISSION_LENGTHS = (20.0, 20.0, 20.0, 10.0, 10.0, 10.0)
_MISSION_TOTAL = 90.0


def _event_spec(values: list[str], fail_value: float,
                *, phase_length: float = _EVENT_PHASE) -> dict[str, Any]:
    """One block, one event, and a phase per entry in *values*.

    *values* is the event's per-phase cell: `"<code>: <number>"`, where the code
    is `A` (active -- the event fires and the number is its failure
    probability), `C` (cut -- the event is out of the diagram, and the run-end
    sweep re-arms it as `Cut` rather than `Armed`) or `P` (prior -- active again,
    keeping the outcome it already has, no draw). Every phase is a mission phase
    of *phase_length* hours, and the run ends with the last one, so the closing
    `EndSimulation` sweep and the run's own mission close are both in the
    timeline.

    The event's outcome distribution is `Fixed` (code 19), which is what makes
    the failure probability exact and the run draw-free: `failVal` is the
    Bernoulli parameter `ConductEvent` *draws and reads*, so `Fixed` at 1.0 is
    "always succeeds" and `Fixed` at 0.0 is "always fails", neither of which
    needs a uniform (`raptor/core/rng.py`, `bernoulli` draws unconditionally).
    """
    count = len(values)
    return {
        "system": {"run_length": phase_length * count, "num_runs": 1,
                   "termination": "TimeSim"},
        "blocks": [{"name": "A", "fail_dist": FIXED, "fail_params": [1000.0],
                    "repair_dist": FIXED, "repair_params": [1.0]}],
        "events": [{"name": "E1", "dist": FIXED, "failVal": fail_value}],
        "nodes": [{"id": 1, "name": "Start", "type": 1, "n": 0, "out": 1},
                  {"id": 2, "name": "End", "type": 3, "n": 1, "out": 0}],
        "links": [{"from": "Start", "to": "A"}, {"from": "A", "to": "E1"},
                  {"from": "E1", "to": "End"}],
        "phases": [{"name": f"P{index + 1}", "mission": 1, "dist": FIXED,
                    "params": [phase_length]} for index in range(count)],
        "phase_states": {"E1": values},
    }


def _mission_spec(a_life: float, a_repair: float,
                  b_life: float, b_repair: float) -> dict[str, Any]:
    """Two parallel blocks into a 1-of-2 Join into the End node, phased.

    The two blocks are the only thing that decides the system's colour, so which
    of them is down when the run ends is what separates a mission that closes
    as a success from one that closes as a failure. Every distribution is
    `Fixed`; the six phases are the module-level `_MISSION_FLAGS` and
    `_MISSION_LENGTHS` table, and the run ends inside the last of them.
    """
    return {
        "system": {"run_length": _MISSION_TOTAL, "num_runs": 1,
                   "termination": "TimeSim"},
        "blocks": [
            {"name": "A", "fail_dist": FIXED, "fail_params": [a_life],
             "repair_dist": FIXED, "repair_params": [a_repair]},
            {"name": "B", "fail_dist": FIXED, "fail_params": [b_life],
             "repair_dist": FIXED, "repair_params": [b_repair]},
        ],
        "nodes": [{"id": 1, "name": "Start", "type": 1, "n": 0, "out": 2},
                  {"id": 2, "name": "Join", "type": 2, "n": 2, "out": 1, "k": 1},
                  {"id": 3, "name": "End", "type": 3, "n": 1, "out": 0}],
        "links": [{"from": "Start", "to": "A"}, {"from": "Start", "to": "B"},
                  {"from": "A", "to": "Join"}, {"from": "B", "to": "Join"},
                  {"from": "Join", "to": "End"}],
        "phases": [{"name": f"P{index + 1}", "mission": _MISSION_FLAGS[index],
                    "dist": FIXED, "params": [_MISSION_LENGTHS[index]]}
                   for index in range(len(_MISSION_FLAGS))],
    }


def _hierarchy_spec(*, a_life: float, a_repair: float, a_pm_freq: float,
                    b_life: float, b_repair: float, c_life: float,
                    c_repair: float, d_life: float, d_repair: float,
                    marker_k: int = 3, run_length: float = 200.0
                    ) -> dict[str, Any]:
    """One hierarchy `H1` wrapping three parallel blocks, plus `D` outside it.

    The shape is the minimum a hierarchy needs and nothing more:

        Start -> D -> End                       (the outside world)
        Start -> H1 -> A, B, C -> J -> out -> End

    `H1` is the `HIERARCHY_DATA` row plus its two marker nodes: an In marker
    (`Type=4`) and an Out marker (`Type=5`), each with `parent` equal to the
    hierarchy's own id -- the rule `builder.validate` enforces, and the one
    whose violation crashes graph building rather than being ignored. Links
    crossing the boundary are addressed at the hierarchy itself, not at the
    marker nodes, which is what `_redirect_hierarchy_boundary` exists to
    normalise and what puts the In marker's incoming link into its
    `in_paths_array`.

    `J` is an ordinary 3-into-3 node inside the hierarchy and `out` is its Out
    marker, so the hierarchy's status is `J`'s status with a one-step lag: the
    Out marker aggregates the single link from `J`, so every status `J` reaches
    other than the `Good`/`Degraded` pair (which are both "all links up enough")
    is a status the hierarchy reaches too.

    A carries the PM and a dependence on `D`; B carries a dependence on `D` and
    its own life; C and D carry only lives. Every distribution is `Fixed`.
    """
    return {
        "system": {"run_length": run_length, "num_runs": 1,
                   "termination": "TimeSim"},
        "hierarchies": [{"id": 1, "name": "H1", "in_id": 2, "out_id": 4,
                         "level": 1}],
        "blocks": [
            {"name": "A", "parent": 1, "fail_dist": FIXED,
             "fail_params": [a_life], "repair_dist": FIXED,
             "repair_params": [a_repair], "uses_pm": True, "pm_freq": a_pm_freq,
             "pm_dist": FIXED, "pm_params": [2.0], "pm_mis_defer": False,
             "dep_type": "RBDBlock", "dep_num": 4, "dep_idle": 100.0},
            {"name": "B", "parent": 1, "fail_dist": FIXED,
             "fail_params": [b_life], "repair_dist": FIXED,
             "repair_params": [b_repair], "dep_type": "RBDBlock", "dep_num": 4,
             "dep_idle": 100.0},
            {"name": "C", "parent": 1, "fail_dist": FIXED,
             "fail_params": [c_life], "repair_dist": FIXED,
             "repair_params": [c_repair]},
            {"name": "D", "fail_dist": FIXED, "fail_params": [d_life],
             "repair_dist": FIXED, "repair_params": [d_repair]},
        ],
        "nodes": [
            {"id": 1, "name": "Start", "type": 1, "n": 0, "out": 2},
            {"id": 2, "name": "in", "type": 4, "n": 1, "out": 3, "parent": 1},
            {"id": 3, "name": "J", "type": 2, "n": 3, "out": 1, "k": marker_k,
             "parent": 1},
            {"id": 4, "name": "out", "type": 5, "n": 1, "out": 1, "parent": 1},
            {"id": 5, "name": "End", "type": 3, "n": 2, "out": 0},
        ],
        "links": [
            {"from": "Start", "to": "D"},
            {"from": "D", "to": "End"},
            {"from": "Start", "to": "in"},
            {"from": "in", "to": "A"},
            {"from": "in", "to": "B"},
            {"from": "in", "to": "C"},
            {"from": "A", "to": "J"},
            {"from": "B", "to": "J"},
            {"from": "C", "to": "J"},
            {"from": "J", "to": "out"},
            {"from": "out", "to": "End"},
        ],
    }

def test_hierarchy_status_is_its_out_markers_k_of_n():
    """A hierarchy's own status is the k-of-n aggregation of the links into its
    Out marker node, and every line it writes is logged under the hierarchy's
    name -- so a hierarchy reaches `Bad`, `Idle` and `NodePM` exactly when the
    node behind it does.

    Why: `determine_node_status` has one branch for `typeNode=5` and it differs
    from an ordinary node only in what it prints: `"Hier"`/`hier.name` instead
    of `"Node"`/`node.node_name`, with the same `N-K/M` detail and the same
    aggregation (ported as the `hier_name` argument
    of `raptor/core/engine/nodes.py:301-312`). `ChangeNodeState`'s own
    `typeNode=5` branch then hands that status to `RBDHierObj.ChangeStatus`,
    which is what colours the oval (`nodes.py:698-709`). The marker's `k` is read literally -- only the `-1`
    "the user never opened the K-of-N dialog" sentinel is rewritten to one good
    path (`raptor/core/fileio/typed.py:249-261`) --
    so `k=3` on the Out marker means "all three of J's paths must be up", and
    the hierarchy is then Bad as soon as one of them is not.

    Setup: `Start -> D -> End` and `Start -> H1 -> A,B,C -> J -> out -> End`,
    with `J` a 3-into-3 node and `out` the hierarchy's `Type=5` Out marker.
    A: `Fixed` 500-hour life, `Fixed` 2-hour PM every 30 hours, and a
    dependence on D with `DepIdle=100%`. B: `Fixed` 20/5, dependent on D.
    C: `Fixed` 30/5. D: `Fixed` 35/20. 200 sim hours, one run.

    Expected: every `Hier` row is written in the same instant as the `Node J` row
    that caused it, and its `from` is the marker's own last status -- so the
    pair is `J`'s transition read one step later, and a `J` row with no `Hier`
    row beside it is a transition that did not change the marker's *link*: the
    marker's single link is `Up` for both `Good` and `Degraded` and `Down`,
    `PM`, `Idle` for the rest, so `Bad -> Bad` (t=65), `Idle -> Idle` (t=90) and
    `Good -> Degraded` are invisible to the hierarchy. The PM hand-back is the
    mechanism behind several of the rows that do appear: a PM that completes
    while its dependence target is `Down` returns the block to `Idle`, not
    `Good`, so the marker's link goes `NodePM` -> `Idle` (t=188) and
    `Bad` -> `Idle` (t=105) without the block ever having been `Good` again.

    Source: Raptor 7 (`determine_node_status`'s `typeNode=5`
    branch; raptor/core/engine/nodes.py:301-312), Raptor 7
    (`ChangeNodeState`'s `typeNode=5` branch; nodes.py:698-709),
    Raptor 7 (`k=-1` -> one good path;
    raptor/core/fileio/typed.py:249-261), Raptor 7 (the node
    cascade that carries J's status into the marker;
    raptor/core/engine/cascade.py:87-139), raptor/core/fileio/builder.py:374-404
    (the marker `parent` rule `build_from_spec` validates),
    docs/engine-design/hierarchy-status.adoc (Source anchors, Structure)
    """
    result = run(_hierarchy_spec(
        a_life=500.0, a_repair=5.0, a_pm_freq=30.0, b_life=20.0, b_repair=5.0,
        c_life=30.0, c_repair=5.0, d_life=35.0, d_repair=20.0))
    assert result.draws == 0
    assert_timeline(result, [
            (0, 'Block', 'A', '--', 'Good'),
            (0, 'Block', 'B', '--', 'Good'),
            (0, 'Block', 'C', '--', 'Good'),
            (0, 'Block', 'D', '--', 'Good'),
            (20, 'Block', 'B', 'Good', 'Failed'),
            (20, 'Block', 'B', 'Failed', 'Repairing'),
            (20, 'Node', 'J', '--', 'Bad'),
            (20, 'Hier', 'H1', '--', 'Bad'),
            (20, 'Node', 'end', '--', 'Degraded'),
            (20, 'System', 'State_change', '--', 'Yellow'),
            (25, 'Block', 'B', 'Repairing', 'Repaired'),
            (25, 'Block', 'B', 'Repaired', 'Good'),
            (25, 'Node', 'J', 'Bad', 'Good'),
            (25, 'Hier', 'H1', 'Bad', 'Good'),
            (25, 'Node', 'end', 'Degraded', 'Good'),
            (25, 'System', 'State_change', 'Yellow', 'Green'),
            (30, 'Block', 'A', 'Good', 'PM_is_due'),
            (30, 'Block', 'C', 'Good', 'Failed'),
            (30, 'Block', 'C', 'Failed', 'Repairing'),
            (30, 'Node', 'J', 'Good', 'Bad'),
            (30, 'Hier', 'H1', 'Good', 'Bad'),
            (30, 'Node', 'end', 'Good', 'Degraded'),
            (30, 'Block', 'A', 'PM_is_due', 'PM'),
            (30, 'Node', 'J', 'Bad', 'Bad'),
            (30, 'System', 'State_change', 'Green', 'Yellow'),
            (32, 'Block', 'A', 'PM', 'PM_complete'),
            (32, 'Block', 'A', 'PM_complete', 'Good'),
            (32, 'Node', 'J', 'Bad', 'Bad'),
            (35, 'Block', 'D', 'Good', 'Failed'),
            (35, 'Block', 'C', 'Repairing', 'Repaired'),
            (35, 'Block', 'C', 'Repaired', 'Good'),
            (35, 'Node', 'J', 'Bad', 'Good'),
            (35, 'Hier', 'H1', 'Bad', 'Good'),
            (35, 'Node', 'end', 'Degraded', 'Good'),
            (35, 'Block', 'D', 'Failed', 'Repairing'),
            (35, 'Block', 'A', 'Good', 'Idle'),
            (35, 'Node', 'J', 'Good', 'Idle'),
            (35, 'Hier', 'H1', 'Good', 'Idle'),
            (35, 'Node', 'end', 'Good', 'Degraded'),
            (35, 'Block', 'B', 'Good', 'Idle'),
            (35, 'Node', 'J', 'Idle', 'Idle'),
            (35, 'Node', 'end', 'Degraded', 'Idle'),
            (35, 'System', 'State_change', 'Yellow', 'Red'),
            (55, 'Block', 'D', 'Repairing', 'Repaired'),
            (55, 'Block', 'D', 'Repaired', 'Good'),
            (55, 'Block', 'A', 'Idle', 'Good'),
            (55, 'Node', 'J', 'Idle', 'Idle'),
            (55, 'Block', 'B', 'Idle', 'Good'),
            (55, 'Node', 'J', 'Idle', 'Good'),
            (55, 'Hier', 'H1', 'Idle', 'Good'),
            (55, 'Node', 'end', 'Idle', 'Degraded'),
            (55, 'Node', 'end', 'Degraded', 'Good'),
            (55, 'System', 'State_change', 'Red', 'Green'),
            (65, 'Block', 'C', 'Good', 'Failed'),
            (65, 'Block', 'B', 'Good', 'Failed'),
            (65, 'Block', 'C', 'Failed', 'Repairing'),
            (65, 'Node', 'J', 'Good', 'Bad'),
            (65, 'Hier', 'H1', 'Good', 'Bad'),
            (65, 'Node', 'end', 'Good', 'Degraded'),
            (65, 'Block', 'B', 'Failed', 'Repairing'),
            (65, 'Node', 'J', 'Bad', 'Bad'),
            (65, 'System', 'State_change', 'Green', 'Yellow'),
            (70, 'Block', 'C', 'Repairing', 'Repaired'),
            (70, 'Block', 'C', 'Repaired', 'Good'),
            (70, 'Node', 'J', 'Bad', 'Bad'),
            (70, 'Block', 'B', 'Repairing', 'Repaired'),
            (70, 'Block', 'B', 'Repaired', 'Good'),
            (70, 'Node', 'J', 'Bad', 'Good'),
            (70, 'Hier', 'H1', 'Bad', 'Good'),
            (70, 'Node', 'end', 'Degraded', 'Good'),
            (70, 'System', 'State_change', 'Yellow', 'Green'),
            (82, 'Block', 'A', 'Good', 'PM_is_due'),
            (82, 'Block', 'A', 'PM_is_due', 'PM'),
            (82, 'Node', 'J', 'Good', 'NodePM'),
            (82, 'Hier', 'H1', 'Good', 'NodePM'),
            (82, 'Node', 'end', 'Good', 'Degraded'),
            (82, 'System', 'State_change', 'Green', 'Yellow'),
            (84, 'Block', 'A', 'PM', 'PM_complete'),
            (84, 'Block', 'A', 'PM_complete', 'Good'),
            (84, 'Node', 'J', 'NodePM', 'Good'),
            (84, 'Hier', 'H1', 'NodePM', 'Good'),
            (84, 'Node', 'end', 'Degraded', 'Good'),
            (84, 'System', 'State_change', 'Yellow', 'Green'),
            (90, 'Block', 'D', 'Good', 'Failed'),
            (90, 'Block', 'B', 'Good', 'Failed'),
            (90, 'Block', 'D', 'Failed', 'Repairing'),
            (90, 'Block', 'A', 'Good', 'Idle'),
            (90, 'Node', 'J', 'Good', 'Idle'),
            (90, 'Hier', 'H1', 'Good', 'Idle'),
            (90, 'Node', 'end', 'Good', 'Degraded'),
            (90, 'Block', 'B', 'Failed', 'Idle'),
            (90, 'Node', 'J', 'Idle', 'Idle'),
            (90, 'Node', 'end', 'Degraded', 'Idle'),
            (90, 'System', 'State_change', 'Green', 'Red'),
            (90, 'Block', 'B', 'Idle', 'Repairing'),
            (95, 'Block', 'B', 'Repairing', 'Repaired'),
            (95, 'Block', 'B', 'Repaired', 'Idle'),
            (95, 'System', 'Red_change', '--', 'Red'),
            (100, 'Block', 'C', 'Good', 'Failed'),
            (100, 'Block', 'C', 'Failed', 'Repairing'),
            (100, 'Node', 'J', 'Idle', 'Bad'),
            (100, 'Hier', 'H1', 'Idle', 'Bad'),
            (100, 'Node', 'end', 'Idle', 'Bad'),
            (100, 'System', 'Red_change', 'Red', 'Red'),
            (105, 'Block', 'C', 'Repairing', 'Repaired'),
            (105, 'Block', 'C', 'Repaired', 'Good'),
            (105, 'Node', 'J', 'Bad', 'Idle'),
            (105, 'Hier', 'H1', 'Bad', 'Idle'),
            (105, 'Node', 'end', 'Bad', 'Idle'),
            (105, 'System', 'Red_change', 'Red', 'Red'),
            (110, 'Block', 'D', 'Repairing', 'Repaired'),
            (110, 'Block', 'D', 'Repaired', 'Good'),
            (110, 'Block', 'A', 'Idle', 'Good'),
            (110, 'Node', 'J', 'Idle', 'Idle'),
            (110, 'Block', 'B', 'Idle', 'Good'),
            (110, 'Node', 'end', 'Idle', 'Degraded'),
            (110, 'System', 'State_change', 'Red', 'Green'),
            (130, 'Block', 'B', 'Good', 'Failed'),
            (130, 'Block', 'B', 'Failed', 'Repairing'),
            (130, 'System', 'State_change', 'Green', 'Yellow'),
            (134, 'Block', 'A', 'Good', 'PM_is_due'),
            (134, 'Block', 'A', 'PM_is_due', 'PM'),
            (134, 'Node', 'J', 'Idle', 'NodePM'),
            (134, 'Hier', 'H1', 'Idle', 'NodePM'),
            (134, 'Node', 'end', 'Degraded', 'Degraded'),
            (135, 'Block', 'C', 'Good', 'Failed'),
            (135, 'Block', 'B', 'Repairing', 'Repaired'),
            (135, 'Block', 'B', 'Repaired', 'Good'),
            (135, 'Block', 'C', 'Failed', 'Repairing'),
            (135, 'Node', 'J', 'NodePM', 'Bad'),
            (135, 'Hier', 'H1', 'NodePM', 'Bad'),
            (135, 'Node', 'end', 'Degraded', 'Degraded'),
            (136, 'Block', 'A', 'PM', 'PM_complete'),
            (136, 'Block', 'A', 'PM_complete', 'Good'),
            (136, 'Node', 'J', 'Bad', 'Bad'),
            (140, 'Block', 'C', 'Repairing', 'Repaired'),
            (140, 'Block', 'C', 'Repaired', 'Good'),
            (140, 'Node', 'J', 'Bad', 'Idle'),
            (140, 'Hier', 'H1', 'Bad', 'Idle'),
            (140, 'Node', 'end', 'Degraded', 'Degraded'),
            (140, 'System', 'State_change', 'Yellow', 'Green'),
            (145, 'Block', 'D', 'Good', 'Failed'),
            (145, 'Block', 'D', 'Failed', 'Repairing'),
            (145, 'Block', 'A', 'Good', 'Idle'),
            (145, 'Node', 'J', 'Idle', 'Idle'),
            (145, 'Block', 'B', 'Good', 'Idle'),
            (145, 'Node', 'end', 'Degraded', 'Idle'),
            (145, 'System', 'State_change', 'Green', 'Red'),
            (165, 'Block', 'D', 'Repairing', 'Repaired'),
            (165, 'Block', 'D', 'Repaired', 'Good'),
            (165, 'Block', 'A', 'Idle', 'Good'),
            (165, 'Node', 'J', 'Idle', 'Idle'),
            (165, 'Block', 'B', 'Idle', 'Good'),
            (165, 'Node', 'end', 'Idle', 'Degraded'),
            (165, 'System', 'State_change', 'Red', 'Green'),
            (170, 'Block', 'C', 'Good', 'Failed'),
            (170, 'Block', 'C', 'Failed', 'Repairing'),
            (170, 'Node', 'J', 'Idle', 'Bad'),
            (170, 'Hier', 'H1', 'Idle', 'Bad'),
            (170, 'Node', 'end', 'Degraded', 'Degraded'),
            (170, 'System', 'State_change', 'Green', 'Yellow'),
            (175, 'Block', 'B', 'Good', 'Failed'),
            (175, 'Block', 'C', 'Repairing', 'Repaired'),
            (175, 'Block', 'C', 'Repaired', 'Good'),
            (175, 'Node', 'J', 'Bad', 'Idle'),
            (175, 'Hier', 'H1', 'Bad', 'Idle'),
            (175, 'Node', 'end', 'Degraded', 'Degraded'),
            (175, 'Block', 'B', 'Failed', 'Repairing'),
            (180, 'Block', 'B', 'Repairing', 'Repaired'),
            (180, 'Block', 'B', 'Repaired', 'Good'),
            (180, 'System', 'State_change', 'Yellow', 'Green'),
            (186, 'Block', 'A', 'Good', 'PM_is_due'),
            (186, 'Block', 'A', 'PM_is_due', 'PM'),
            (186, 'Node', 'J', 'Idle', 'NodePM'),
            (186, 'Hier', 'H1', 'Idle', 'NodePM'),
            (186, 'Node', 'end', 'Degraded', 'Degraded'),
            (186, 'System', 'State_change', 'Green', 'Yellow'),
            (188, 'Block', 'A', 'PM', 'PM_complete'),
            (188, 'Block', 'A', 'PM_complete', 'Good'),
            (188, 'Node', 'J', 'NodePM', 'Idle'),
            (188, 'Hier', 'H1', 'NodePM', 'Idle'),
            (188, 'Node', 'end', 'Degraded', 'Degraded'),
            (188, 'System', 'State_change', 'Yellow', 'Green'),
            (200, 'Block', 'D', 'Good', 'Failed'),
            (200, 'Block', 'B', 'Good', 'Failed'),
            (200, 'Block', 'B', 'Failed', 'Repairing'),
            (200, 'Block', 'D', 'Failed', 'Repairing'),
            (200, 'Block', 'A', 'Good', 'Idle'),
            (200, 'Node', 'J', 'Idle', 'Idle'),
            (200, 'Node', 'end', 'Degraded', 'Idle'),
            (200, 'System', 'State_change', 'Green', 'Red'),
            (200, 'Block', 'A', 'Idle', 'Done'),
            (200, 'Block', 'B', 'Repairing', 'Done'),
            (200, 'Block', 'C', 'Good', 'Done'),
            (200, 'Block', 'D', 'Repairing', 'Done'),
            (200, 'System', 'Phase_change', '--', 'Mission_failure'),
            (200, 'System', 'Simulation_Terminate', '--', '--'),
    ], where="hierarchy")


def test_a_phased_event_refires_and_its_outcome_can_repeat():
    """A phased event fires again at every phase boundary, so the same outcome
    can be recorded twice: `Success -> Success` is one `ConductEvent` draw
    landing the same way at two different boundaries.

    Why: a phase change re-applies every phased element, and an event whose
    current and next phase cell are both `A` with the *same* probability is
    still redrawn -- that is `ChangePhases`' own same-value refire arm
    (`raptor/core/engine/phasing.py:217-226`), which
    the caller applies through `apply_event` (`raptor/core/engine/cascade_phase.py:166-197`). `ConductEvent` then draws
    the phase's own value when the phase type is `A`
    (`raptor/core/engine/events.py:70-79`) and
    dispatches to `ChangeEventState`, which logs the event's new status
    whatever it was before. The run's end is the other half: `EndSimulation`'s
    closing sweep re-arms every event, and it passes the event's *own* active
    status, so an event the last phase left `Cut` is re-armed as `Cut` and the
    run writes `Cut -> Cut` (`raptor/core/engine/driver.py:374-381`).

    Setup: `Start -> A -> E1 -> End`, E1's outcome distribution `Fixed` at
    `failVal` 1.0 (an event that always succeeds), and five 10-hour mission
    phases whose E1 cells are `A: 1.0`, `C: 1.0`, `A: 1.0`, `A: 1.0`, `C: 1.0`.
    50 sim hours, one run.

    Expected: E1 is `Armed` and `Success` at kickoff; `Success -> Cut` at t=10
    (the second phase cuts it); `Cut -> Success` at t=20 (an `Active` phase
    fires it again); `Success -> Success` at t=30, where the cell is unchanged
    from t=20's; `Success -> Cut` at t=40; and `Cut -> Cut` at t=50, the run's
    end. Every `Success -> Success` in this literal is that refire arm and
    nothing else: the End node mirrors the event, `Bad` while E1 is cut and
    `Good` when it succeeds, and the system's colour follows.

    Source: Raptor 7 (`classify_event_same_value_refire`;
    raptor/core/engine/phasing.py:217-226), Raptor 7
    (`ChangePhases`' own event dispatch; cascade_phase.py:166-197),
    Raptor 7 (`ConductEvent`'s phased value;
    raptor/core/engine/events.py:70-79), Raptor 7
    (`EndSimulation`'s element sweep; raptor/core/engine/driver.py:374-381),
    docs/engine-design/events-and-phases.adoc (Source anchors)
    """
    result = run(_event_spec(
        ["A: 1.0", "C: 1.0", "A: 1.0", "A: 1.0", "C: 1.0"], 1.0))
    assert result.draws == 0
    assert_timeline(result, [
            (0, 'System', 'State_change', '--', 'Mission'),
            (0, 'Block', 'A', '--', 'Good'),
            (0, 'Event', 'E1', '--', 'Armed'),
            (0, 'Event', 'E1', 'Armed', 'Success'),
            (10, 'System', 'Phase_change', '--', 'Phase_change'),
            (10, 'Event', 'E1', 'Success', 'Cut'),
            (10, 'Node', 'end', '--', 'Bad'),
            (10, 'System', 'State_change', 'Mission', 'Red'),
            (20, 'System', 'Phase_change', 'Phase_change', 'Phase_change'),
            (20, 'Event', 'E1', 'Cut', 'Success'),
            (20, 'Node', 'end', 'Bad', 'Good'),
            (20, 'System', 'State_change', 'Red', 'Green'),
            (30, 'System', 'Phase_change', 'Phase_change', 'Phase_change'),
            (30, 'Event', 'E1', 'Success', 'Success'),
            (40, 'System', 'Phase_change', 'Phase_change', 'Phase_change'),
            (40, 'Event', 'E1', 'Success', 'Cut'),
            (40, 'Node', 'end', 'Good', 'Bad'),
            (40, 'System', 'State_change', 'Green', 'Red'),
            (50, 'Block', 'A', 'Good', 'Done'),
            (50, 'Event', 'E1', 'Cut', 'Cut'),
            (50, 'System', 'Phase_change', 'Phase_change', 'Mission_failure'),
            (50, 'System', 'Simulation_Terminate', '--', '--'),
    ], where="event-success")


def test_a_phased_event_that_fails_can_be_cut_and_can_fail_twice_running():
    """A phased event's failure is not terminal: it can be cut by a phase, come
    back as a success, fail again at two consecutive boundaries, and be
    re-armed at the run's end -- so `Failed -> Failed` is a real transition and
    not a duplicate of the row before it.

    Why: the same `ChangePhases` dispatch as the success case, keyed instead on
    the event's *own* last outcome: an event whose last outcome was a failure is
    an improvement when it fires again, anything else is a degrade
    (`raptor/core/engine/phasing.py:188-214`). Either
    way the redraw happens and `ConductEvent` logs whatever the draw says, so
    the same event writes `Success -> Failed`, `Failed -> Failed` and
    `Failed -> Success` in one run. What the state column holds is the event's
    *new* status whenever the event is Active again, and its new *active* status
    otherwise -- which is why a phase that makes a cut event active logs the
    outcome (`Cut -> Success`, `Cut -> Failed`) and a phase that cuts it logs
    `Cut` (`Failed -> Cut`, `Success -> Cut`)
    (`raptor/core/engine/events.py:148-165`).

    Setup: as the success fixture, with E1's outcome distribution `Fixed` at
    `failVal` 0.0 (an event that always fails) and eight 10-hour mission phases
    whose E1 cells are `A: 0.0`, `C: 1.0`, `A: 1.0`, `A: 0.0`, `A: 0.0`,
    `A: 1.0`, `C: 1.0`, `A: 0.0`. 80 sim hours, one run.

    Expected: `Armed -> Failed` at kickoff; `Failed -> Cut` at t=10;
    `Cut -> Success` at t=20; `Success -> Failed` at t=30; `Failed -> Failed` at
    t=40, where the cell is the same `A: 0.0` as t=30's and that is the
    same-value refire arm; `Failed -> Success` at t=50; `Success -> Cut` at
    t=60; `Cut -> Failed` at t=70; and `Failed -> Armed` at t=80, which is
    `EndSimulation`'s re-arm rather than the event's.

    Source: Raptor 7 (`classify_event_phase_transition`;
    raptor/core/engine/phasing.py:188-214), Raptor 7
    (the same-value refire; phasing.py:217-226), Raptor 7
    (cascade_phase.py:166-197), raptor/core/engine/events.py:148-165
    (`ChangeEventState`'s state column: the active status whenever the new
    status is not `Active`), Raptor 7 (the run's closing re-arm;
    driver.py:374-381)
    """
    result = run(_event_spec(
        ["A: 0.0", "C: 1.0", "A: 1.0", "A: 0.0", "A: 0.0", "A: 1.0",
         "C: 1.0", "A: 0.0"], 0.0))
    assert result.draws == 0
    assert_timeline(result, [
            (0, 'System', 'State_change', '--', 'Mission'),
            (0, 'Block', 'A', '--', 'Good'),
            (0, 'Event', 'E1', '--', 'Armed'),
            (0, 'Event', 'E1', 'Armed', 'Failed'),
            (0, 'Node', 'end', '--', 'Bad'),
            (0, 'System', 'State_change', 'Mission', 'Red'),
            (10, 'System', 'Phase_change', '--', 'Phase_change'),
            (10, 'Event', 'E1', 'Failed', 'Cut'),
            (20, 'System', 'Phase_change', 'Phase_change', 'Phase_change'),
            (20, 'Event', 'E1', 'Cut', 'Success'),
            (20, 'Node', 'end', 'Bad', 'Good'),
            (20, 'System', 'State_change', 'Red', 'Green'),
            (30, 'System', 'Phase_change', 'Phase_change', 'Phase_change'),
            (30, 'Event', 'E1', 'Success', 'Failed'),
            (30, 'Node', 'end', 'Good', 'Bad'),
            (30, 'System', 'State_change', 'Green', 'Red'),
            (40, 'System', 'Phase_change', 'Phase_change', 'Phase_change'),
            (40, 'Event', 'E1', 'Failed', 'Failed'),
            (50, 'System', 'Phase_change', 'Phase_change', 'Phase_change'),
            (50, 'Event', 'E1', 'Failed', 'Success'),
            (50, 'Node', 'end', 'Bad', 'Good'),
            (50, 'System', 'State_change', 'Red', 'Green'),
            (60, 'System', 'Phase_change', 'Phase_change', 'Phase_change'),
            (60, 'Event', 'E1', 'Success', 'Cut'),
            (60, 'Node', 'end', 'Good', 'Bad'),
            (60, 'System', 'State_change', 'Green', 'Red'),
            (70, 'System', 'Phase_change', 'Phase_change', 'Phase_change'),
            (70, 'Event', 'E1', 'Cut', 'Failed'),
            (80, 'Block', 'A', 'Good', 'Done'),
            (80, 'Event', 'E1', 'Failed', 'Armed'),
            (80, 'System', 'Phase_change', 'Phase_change', 'Mission_failure'),
            (80, 'System', 'Simulation_Terminate', '--', '--'),
    ], where="event-failure")


def test_a_mission_boundary_records_the_colour_it_finds_and_re_reports_it():
    """A phase boundary that closes a mission writes the outcome the system is
    *at that instant*, and re-reports the colour even when the colour does not
    move -- so `Red -> Red` and `Green -> Green` are the boundary's own rows and
    a mission that closes while the system is Red is a `Mission_failure`.

    Why: `ConductPhaseChange`'s mission-closing arm calls `ChangeStatus` with the
    colour cached before the boundary did anything
    (`raptor/core/engine/cascade_phase.py:67-81`),
    and `ChangeStatus`'s tail writes a `State_change` line on *every* call that
    got that far -- colour unchanged is not a gate, only a complete no-op is
    (`raptor/core/engine/system_status.py:398-426`).
    The word in the `Finished_a_mission` line is `missionSuccess`, which is
    cleared the moment the system goes Red and set again only when a mission
    begins (and,
    `system_status.py:319-320`).

    Setup: `Start -> A,B -> Join(1 of 2) -> End`, A `Fixed` 10/25 and B
    `Fixed` 20/15, under six phases of 20/20/20/10/10/10 hours whose mission
    flags are mission, non-mission, mission, non-mission, non-mission, mission.
    90 sim hours, one run.

    Expected: the t=60 boundary closes a mission while both blocks are down, so
    it is a `Mission_failure`; the t=90 end closes the last mission with the
    system `Red` again, so that one is `Mission_failure` too; and the boundary
    at t=20, t=40 and t=80 re-reports a colour that has not moved
    (`Yellow -> Yellow` three times, plus the `Red -> Red` at t=60). The
    `Phase_change` object is a *different* log object from `State_change`, so
    its rows chain separately: `Mission_success -> Phase_change` at t=40 is a
    boundary in a non-mission phase, with no mission to close and none to open.

    Source: Raptor 7 (`ConductPhaseChange`;
    raptor/core/engine/cascade_phase.py:24-57), Raptor 7 and
     (`ChangeStatus`'s mission-end line and its tail colour write;
    raptor/core/engine/system_status.py:382-426), Raptor 7 and
     (when a mission stops and starts counting as a success;
    system_status.py:319-320), Raptor 7
    (`AnalyzeSystem`'s colour; system_status.py:239-246),
    Raptor 7 (the run opens in a mission; raptor/core/engine/
    kickoff.py:157-171)
    """
    result = run(_mission_spec(10.0, 25.0, 20.0, 15.0))
    assert result.draws == 0
    assert_timeline(result, [
            (0, 'System', 'State_change', '--', 'Mission'),
            (0, 'Block', 'A', '--', 'Good'),
            (0, 'Block', 'B', '--', 'Good'),
            (10, 'Block', 'A', 'Good', 'Failed'),
            (10, 'Block', 'A', 'Failed', 'Repairing'),
            (10, 'Node', 'Join', '--', 'Degraded'),
            (10, 'System', 'State_change', 'Mission', 'Yellow'),
            (20, 'Block', 'B', 'Good', 'Failed'),
            (20, 'System', 'Phase_change', '--', 'Phase_change'),
            (20, 'Block', 'B', 'Failed', 'Repairing'),
            (20, 'Node', 'Join', 'Degraded', 'Bad'),
            (20, 'Node', 'end', '--', 'Bad'),
            (20, 'System', 'State_change', 'Yellow', 'Red'),
            (20, 'System', 'Phase_change', 'Phase_change', 'Mission_failure'),
            (20, 'System', 'State_change', 'Red', 'Red'),
            (35, 'Block', 'A', 'Repairing', 'Repaired'),
            (35, 'Block', 'A', 'Repaired', 'Good'),
            (35, 'Node', 'Join', 'Bad', 'Degraded'),
            (35, 'Node', 'end', 'Bad', 'Good'),
            (35, 'Block', 'B', 'Repairing', 'Repaired'),
            (35, 'Block', 'B', 'Repaired', 'Good'),
            (35, 'Node', 'Join', 'Degraded', 'Good'),
            (35, 'System', 'State_change', 'Red', 'Green'),
            (40, 'System', 'Phase_change', 'Mission_failure', 'Phase_change'),
            (40, 'System', 'Phase_change', 'Phase_change', 'Mission'),
            (40, 'System', 'State_change', 'Green', 'Green'),
            (45, 'Block', 'A', 'Good', 'Failed'),
            (45, 'Block', 'A', 'Failed', 'Repairing'),
            (45, 'Node', 'Join', 'Good', 'Degraded'),
            (45, 'System', 'State_change', 'Green', 'Yellow'),
            (55, 'Block', 'B', 'Good', 'Failed'),
            (55, 'Block', 'B', 'Failed', 'Repairing'),
            (55, 'Node', 'Join', 'Degraded', 'Bad'),
            (55, 'Node', 'end', 'Good', 'Bad'),
            (55, 'System', 'State_change', 'Yellow', 'Red'),
            (60, 'System', 'Phase_change', 'Mission', 'Phase_change'),
            (60, 'System', 'Phase_change', 'Phase_change', 'Mission_failure'),
            (60, 'System', 'State_change', 'Red', 'Red'),
            (70, 'Block', 'A', 'Repairing', 'Repaired'),
            (70, 'Block', 'A', 'Repaired', 'Good'),
            (70, 'Node', 'Join', 'Bad', 'Degraded'),
            (70, 'Node', 'end', 'Bad', 'Good'),
            (70, 'Block', 'B', 'Repairing', 'Repaired'),
            (70, 'Block', 'B', 'Repaired', 'Good'),
            (70, 'Node', 'Join', 'Degraded', 'Good'),
            (70, 'System', 'Phase_change', 'Mission_failure', 'Phase_change'),
            (70, 'System', 'State_change', 'Red', 'Green'),
            (80, 'Block', 'A', 'Good', 'Failed'),
            (80, 'System', 'Phase_change', 'Phase_change', 'Phase_change'),
            (80, 'Block', 'A', 'Failed', 'Repairing'),
            (80, 'Node', 'Join', 'Good', 'Degraded'),
            (80, 'System', 'State_change', 'Green', 'Yellow'),
            (80, 'System', 'Phase_change', 'Phase_change', 'Mission'),
            (80, 'System', 'State_change', 'Yellow', 'Yellow'),
            (90, 'Block', 'B', 'Good', 'Failed'),
            (90, 'Block', 'B', 'Failed', 'Repairing'),
            (90, 'Node', 'Join', 'Degraded', 'Bad'),
            (90, 'Node', 'end', 'Good', 'Bad'),
            (90, 'System', 'State_change', 'Yellow', 'Red'),
            (90, 'Block', 'A', 'Repairing', 'Done'),
            (90, 'Block', 'B', 'Repairing', 'Done'),
            (90, 'System', 'Phase_change', 'Mission', 'Mission_failure'),
            (90, 'System', 'Simulation_Terminate', '--', '--'),
    ], where="mission-red")


def test_a_mission_that_outlives_the_run_closes_as_a_success():
    """The run's own end closes whatever mission is open, and the word it writes
    is the system's colour at that instant -- so the same closing code writes
    `Mission_success` here and `Mission_failure` in the fixture above.

    Why: `EndSimulation`'s last act is `ASK System TO ChangeStatus(DDone,
    NonMission)` (`raptor/core/engine/driver.py:383-397`),
    which is the same `ChangeStatus` the phase boundary uses, so a mission begun
    at the last boundary and still open when the run ends produces exactly one
    line: `Phase_change Mission -> Mission_success` when the colour is not Red
    and `... -> Mission_failure` when it is
    (`raptor/core/engine/system_status.py:382-396`).
    That is also why the last phase here is a mission phase: a model whose run
    ends in a non-mission phase has already written its closing line at the
    boundary, which is the S9 rung's finding.

    Setup: as the boundary fixture, with A `Fixed` 60/25 and B `Fixed` 20/5, so
    both blocks are `Good` at the run's end. 90 sim hours, one run.

    Expected: `Phase_change Mission -> Mission_success` at t=90 and no mission
    line anywhere else after t=60 -- the t=60 boundary closed the third phase's
    mission as a success while the system was `Green`, and the t=90 line is the
    run's own. The `Green -> Green` at t=40 and t=60 are the two mission
    boundaries whose colour did not move.

    Source: Raptor 7 (`EndSimulation`'s closing `ChangeStatus(DDone,
    NonMission)`; raptor/core/engine/driver.py:383-397), Raptor 7
    (`ChangeStatus`'s mission-end line;
    raptor/core/engine/system_status.py:382-396), Raptor 7
    (`AnalyzeSystem`; system_status.py:239-246),
    docs/engine-design/events-and-phases.adoc (Ordering at a boundary)
    """
    result = run(_mission_spec(60.0, 25.0, 20.0, 5.0))
    assert result.draws == 0
    assert_timeline(result, [
            (0, 'System', 'State_change', '--', 'Mission'),
            (0, 'Block', 'A', '--', 'Good'),
            (0, 'Block', 'B', '--', 'Good'),
            (20, 'Block', 'B', 'Good', 'Failed'),
            (20, 'System', 'Phase_change', '--', 'Phase_change'),
            (20, 'Block', 'B', 'Failed', 'Repairing'),
            (20, 'Node', 'Join', '--', 'Degraded'),
            (20, 'System', 'Phase_change', 'Phase_change', 'Mission_success'),
            (20, 'System', 'State_change', 'Mission', 'Green'),
            (25, 'Block', 'B', 'Repairing', 'Repaired'),
            (25, 'Block', 'B', 'Repaired', 'Good'),
            (25, 'Node', 'Join', 'Degraded', 'Good'),
            (40, 'System', 'Phase_change', 'Mission_success', 'Phase_change'),
            (40, 'System', 'Phase_change', 'Phase_change', 'Mission'),
            (40, 'System', 'State_change', 'Green', 'Green'),
            (45, 'Block', 'B', 'Good', 'Failed'),
            (45, 'Block', 'B', 'Failed', 'Repairing'),
            (45, 'Node', 'Join', 'Good', 'Degraded'),
            (45, 'System', 'State_change', 'Green', 'Yellow'),
            (50, 'Block', 'B', 'Repairing', 'Repaired'),
            (50, 'Block', 'B', 'Repaired', 'Good'),
            (50, 'Node', 'Join', 'Degraded', 'Good'),
            (50, 'System', 'State_change', 'Yellow', 'Green'),
            (60, 'Block', 'A', 'Good', 'Failed'),
            (60, 'System', 'Phase_change', 'Mission', 'Phase_change'),
            (60, 'Block', 'A', 'Failed', 'Repairing'),
            (60, 'Node', 'Join', 'Good', 'Degraded'),
            (60, 'System', 'Phase_change', 'Phase_change', 'Mission_success'),
            (60, 'System', 'State_change', 'Green', 'Green'),
            (70, 'Block', 'B', 'Good', 'Failed'),
            (70, 'System', 'Phase_change', 'Mission_success', 'Phase_change'),
            (70, 'Block', 'B', 'Failed', 'Repairing'),
            (70, 'Node', 'Join', 'Degraded', 'Bad'),
            (70, 'Node', 'end', '--', 'Bad'),
            (70, 'System', 'State_change', 'Green', 'Red'),
            (75, 'Block', 'B', 'Repairing', 'Repaired'),
            (75, 'Block', 'B', 'Repaired', 'Good'),
            (75, 'Node', 'Join', 'Bad', 'Degraded'),
            (75, 'Node', 'end', 'Bad', 'Good'),
            (75, 'System', 'State_change', 'Red', 'Yellow'),
            (80, 'System', 'Phase_change', 'Phase_change', 'Phase_change'),
            (80, 'System', 'Phase_change', 'Phase_change', 'Mission'),
            (80, 'System', 'State_change', 'Yellow', 'Yellow'),
            (85, 'Block', 'A', 'Repairing', 'Repaired'),
            (85, 'Block', 'A', 'Repaired', 'Good'),
            (85, 'Node', 'Join', 'Degraded', 'Good'),
            (85, 'System', 'State_change', 'Yellow', 'Green'),
            (90, 'Block', 'A', 'Good', 'Done'),
            (90, 'Block', 'B', 'Good', 'Done'),
            (90, 'System', 'Phase_change', 'Mission', 'Mission_success'),
            (90, 'System', 'Simulation_Terminate', '--', '--'),
    ], where="mission-success")


def _cold_group_spec(blocks: list[dict[str, Any]], k: int, k_star: int,
                     run_length: float) -> dict[str, Any]:
    """One cold-standby switching group of *blocks* into the End node.

    `k` is the node's good-path requirement and `k_star` its `k*`, which is what
    decides how many paths the group keeps: every candidate starts `Running`
    with its link `"ready"`, so the survey at kickoff keeps the first `k*` in
    `cold_links` order and switches the rest to `Standby`.

    Every link into the group carries `AProb=0.0` and `ManTime=-1.0`, which is
    how a link is made *permanently* unswitchable: `SetSwitchTime` resolves
    `AProb=0` to "not automatic", and with no manual fallback either it leaves
    the link's `switchStatus` at `"none"`
    (`raptor/core/engine/cold_standby.py:90-127`).
    `AProb=1.0` with `AutoTime=0.0` would instead resolve to `"ready"`, which is
    what the S8 rung uses.
    """
    names = [block["name"] for block in blocks]
    return {
        "system": {"run_length": run_length, "num_runs": 1,
                   "termination": "TimeSim"},
        "blocks": blocks,
        "nodes": [
            {"id": 1, "name": "Start", "type": 1, "n": 0, "out": len(names)},
            {"id": 2, "name": "Cold", "type": 2, "n": len(names), "out": 1,
             "k": k, "k_star": k_star, "cold": 1, "pr": 0, "autos": 0},
            {"id": 3, "name": "End", "type": 3, "n": 1, "out": 0},
        ],
        "links": [{"from": "Start", "to": name} for name in names]
                 + [{"from": name, "to": "Cold", "cold_pri": 10 * (index + 1),
                     "a_prob": 0.0, "auto_time": 0.0, "man_time": -1.0}
                    for index, name in enumerate(names)]
                 + [{"from": "Cold", "to": "End"}],
    }


def _cold_block(name: str, life: float, *, pm_freq: float | None = None,
                repair: float = 200.0) -> dict[str, Any]:
    """One cold candidate: a `Fixed` life and repair, optionally with a PM."""
    block: dict[str, Any] = {
        "name": name, "fail_dist": FIXED, "fail_params": [life],
        "repair_dist": FIXED, "repair_params": [repair],
    }
    if pm_freq is not None:
        block.update(uses_pm=True, pm_freq=pm_freq, pm_dist=FIXED,
                     pm_params=[2.0], pm_mis_defer=False)
    return block


def test_an_unswitchable_cold_candidate_is_failed_permanently_and_the_group_carries_on():
    """A cold group whose standby members cannot be switched writes
    `Switch_Failed` for each of them, marks those paths permanently unusable,
    and then reports the group as whatever the surviving paths make it -- which
    is `NodePM` while the running member is in PM.

    Why: `RunColdLogic` counts `"none"`-status links separately from ready ones
    and, when the group needs more paths than it has ready, fails every
    `"none"` candidate it walks (and,
    ported as `_switch_failed` at `raptor/core/engine/cold_standby.py:190-276`):
    the link is set to `LinkDone`, the path's fail counter is incremented, and
    one `Node <name> Switch_Failed Unable_to_switch_to_<block>` line is written.
    The group's own status then comes from the resurvey that follows
    (`cold_standby.py:755-822`), which is a
    *different* aggregation from `determine_node_status` and has no `Standby`
    arm at all -- a cold node cannot be `Standby`, which is why the S8 rung's
    group stays `Degraded` for the whole run.

    Two details of the literal are worth naming. The group's status line for the
    survey is written *after* the `Switch_Failed` lines, so the `from` of the
    first `Switch_Failed` is whatever the group last logged. And every `"none"`
    candidate is failed in the *same* survey: the pass's `switchingPaths`
    counter is advanced only by an automatic or manual switch, so a `"none"`
    link does not consume the one switch the group asked for -- hence three
    `Switch_Failed` rows at t=10 and not one.

    Setup: `Start -> A,B,C,D -> Cold(1 of 1, k*=1) -> End`, every link with
    `AProb=0.0`/`ManTime=-1.0`. A's PM is due every 10 hours; B's, C's and D's
    every 13, 17 and 19 (they never fire, because a block the group has
    switched to `Standby` does not run its own PM clock). A's life is
    `Fixed` 500 and its repair 200, so it never fails inside the run. 30 sim
    hours, one run.

    Expected: kickoff demotes B, C and D (`Too_many_on`) and the group logs
    `Good`; A's first PM at t=10 leaves the group with no ready path, so all
    three standby candidates are failed in one survey and the group is `NodePM`
    because A is in PM; at t=12 A's PM completes and the group is `Degraded`,
    since one of two surviving paths is `LinkDone`. The End node follows the
    group, `NodePM` and then `Good`, because a cold group's link into the End
    node is `LinkUp` for every status the resurvey can report except `Bad`.

    Source: Raptor 7 (`RunColdLogic`'s
    `switchStatus="none"` branches; raptor/core/engine/cold_standby.py:190-276),
    Raptor 7 (`Link.SetSwitchTime`'s `AProb`/`ManTime`
    resolution; cold_standby.py:90-127), Raptor 7 (the resurvey
    that writes the group's own line; cold_standby.py:755-822),
    Raptor 7 (the kickoff `Too_many_on` demotion;
    cold_standby.py:609-674), docs/engine-design/node-status-and-cold-standby
    .adoc (Structure)
    """
    result = run(_cold_group_spec(
        [_cold_block("A", 500.0, pm_freq=10.0),
         _cold_block("B", 500.0, pm_freq=13.0),
         _cold_block("C", 500.0, pm_freq=17.0),
         _cold_block("D", 500.0, pm_freq=19.0)], 1, 1, 30.0), length=30.0)
    assert result.draws == 0
    assert_timeline(result, [
            (0, 'Block', 'A', '--', 'Good'),
            (0, 'Block', 'B', '--', 'Good'),
            (0, 'Block', 'C', '--', 'Good'),
            (0, 'Block', 'D', '--', 'Good'),
            (0, 'Block', 'B', 'Good', 'Standby'),
            (0, 'Block', 'C', 'Good', 'Standby'),
            (0, 'Block', 'D', 'Good', 'Standby'),
            (0, 'Node', 'Cold', '--', 'Good'),
            (10, 'Block', 'A', 'Good', 'PM_is_due'),
            (10, 'Block', 'A', 'PM_is_due', 'PM'),
            (10, 'Node', 'Cold', 'Good', 'Switch_Failed'),
            (10, 'Node', 'Cold', 'Switch_Failed', 'Switch_Failed'),
            (10, 'Node', 'Cold', 'Switch_Failed', 'Switch_Failed'),
            (10, 'Node', 'Cold', 'Switch_Failed', 'NodePM'),
            (10, 'Node', 'end', '--', 'NodePM'),
            (10, 'System', 'State_change', '--', 'Red'),
            (12, 'Block', 'A', 'PM', 'PM_complete'),
            (12, 'Block', 'A', 'PM_complete', 'Good'),
            (12, 'Node', 'Cold', 'NodePM', 'Degraded'),
            (12, 'Node', 'end', 'NodePM', 'Good'),
            (12, 'System', 'State_change', 'Red', 'Yellow'),
            (22, 'Block', 'A', 'Good', 'PM_is_due'),
            (22, 'Block', 'A', 'PM_is_due', 'PM'),
            (22, 'Node', 'Cold', 'Degraded', 'NodePM'),
            (22, 'Node', 'end', 'Good', 'NodePM'),
            (22, 'System', 'State_change', 'Yellow', 'Red'),
            (24, 'Block', 'A', 'PM', 'PM_complete'),
            (24, 'Block', 'A', 'PM_complete', 'Good'),
            (24, 'Node', 'Cold', 'NodePM', 'Degraded'),
            (24, 'Node', 'end', 'NodePM', 'Good'),
            (24, 'System', 'State_change', 'Red', 'Yellow'),
            (30, 'Block', 'A', 'Good', 'Done'),
            (30, 'Block', 'B', 'Standby', 'Done'),
            (30, 'Block', 'C', 'Standby', 'Done'),
            (30, 'Block', 'D', 'Standby', 'Done'),
            (30, 'System', 'Phase_change', '--', 'Mission_failure'),
            (30, 'System', 'Simulation_Terminate', '--', '--'),
    ], where="cold-unswitchable")


def test_a_cold_group_whose_standbys_are_all_unswitchable_ends_up_bad():
    """When the running members of a cold group fail one after another and every
    standby member is unswitchable, the group's own line goes `Good` ->
    `Switch_Failed` -> `Bad` and then stays `Bad`: a failed switch is permanent
    for the rest of the run.

    Why: `_switch_failed` sets the path's link to `LinkDone` and its fail
    counter up, and `LinkDone` is one of the statuses the resurvey counts as a
    down path (`raptor/core/engine/cold_standby.py:769-777`). Nothing ever clears it: the
    ported comment on `reconfigure_system` is explicit that `SetActiveLink` and
    `LinkDone` are the "a path just became PERMANENTLY unusable" ratchet, and
    the source block's own `Run` process is parked for the rest of the run
    (`raptor/core/engine/cascade.py:142-196`;
    `cold_standby.py:266-275`). So a group that has run out of switchable
    standbys degrades to `Bad` on the next failure and can never leave it --
    which is what the `Bad -> Bad` rows at t=40 and t=60 are.

    Setup: `Start -> A,B,C,D,E -> Cold(3 of 3, k*=3) -> End`, every link with
    `AProb=0.0`/`ManTime=-1.0`. A, B and C have `Fixed` lives of 20, 40 and 60
    hours and `Fixed` 200-hour repairs; D and E are the two unswitchable
    standbys and never fail. 60 sim hours, one run.

    Expected: kickoff keeps A, B and C running and switches D and E to
    `Standby`, so the group is `Good` (all five paths counted, three up and two
    standby). A's failure at t=20 leaves two ready paths where the group wants
    three, so D is failed permanently and the group is `Bad` on the resurvey; B's
    and C's failures then only repeat the survey, because there is no switchable
    candidate left to fail.

    Source: Raptor 7 (`_switch_failed`'s
    `ChangeLinkStatus(LinkDone)`; raptor/core/engine/cold_standby.py:243-251),
    Raptor 7 (the resurvey's down-path buckets;
    cold_standby.py:758-787), Raptor 7 (`ReconfigureSystem`'s
    permanent-path ratchet; raptor/core/engine/cascade.py:142-196),
    Raptor 7 (`SetSwitchTime`; cold_standby.py:90-127),
    docs/engine-design/node-status-and-cold-standby.adoc (Structure)
    """
    result = run(_cold_group_spec(
        [_cold_block("A", 20.0), _cold_block("B", 40.0), _cold_block("C", 60.0),
         _cold_block("D", 500.0), _cold_block("E", 500.0)], 3, 3, 60.0),
        length=60.0)
    assert result.draws == 0
    assert_timeline(result, [
            (0, 'Block', 'A', '--', 'Good'),
            (0, 'Block', 'B', '--', 'Good'),
            (0, 'Block', 'C', '--', 'Good'),
            (0, 'Block', 'D', '--', 'Good'),
            (0, 'Block', 'E', '--', 'Good'),
            (0, 'Block', 'D', 'Good', 'Standby'),
            (0, 'Block', 'E', 'Good', 'Standby'),
            (0, 'Node', 'Cold', '--', 'Good'),
            (20, 'Block', 'A', 'Good', 'Failed'),
            (20, 'Block', 'A', 'Failed', 'Repairing'),
            (20, 'Node', 'Cold', 'Good', 'Switch_Failed'),
            (20, 'Node', 'Cold', 'Switch_Failed', 'Switch_Failed'),
            (20, 'Node', 'Cold', 'Switch_Failed', 'Bad'),
            (20, 'Node', 'end', '--', 'Bad'),
            (20, 'System', 'State_change', '--', 'Red'),
            (40, 'Block', 'B', 'Good', 'Failed'),
            (40, 'Block', 'B', 'Failed', 'Repairing'),
            (40, 'Node', 'Cold', 'Bad', 'Bad'),
            (40, 'System', 'Red_change', '--', 'Red'),
            (60, 'Block', 'C', 'Good', 'Failed'),
            (60, 'Block', 'C', 'Failed', 'Repairing'),
            (60, 'Node', 'Cold', 'Bad', 'Bad'),
            (60, 'Block', 'A', 'Repairing', 'Done'),
            (60, 'Block', 'B', 'Repairing', 'Done'),
            (60, 'Block', 'C', 'Repairing', 'Done'),
            (60, 'Block', 'D', 'Standby', 'Done'),
            (60, 'Block', 'E', 'Standby', 'Done'),
            (60, 'System', 'Phase_change', '--', 'Mission_failure'),
            (60, 'System', 'Simulation_Terminate', '--', '--'),
    ], where="cold-exhausted")


def test_a_switch_failure_is_written_from_the_groups_previous_status():
    """A `Switch_Failed` row's `from` is whatever the group last logged, not
    what the survey decided: a group that was `Degraded` writes
    `Degraded -> Switch_Failed`, and the status the resurvey then reports is a
    separate question.

    Why: `_switch_failed` writes its line where the survey makes the decision,
    ahead of the later promotion, and the group's own status line is written
    afterwards by the resurvey (against;
    the ported ordering note is at
    `raptor/core/engine/cold_standby.py:443-451`). So the two rows bracket the
    decision: the `Switch_Failed` line inherits the *previous* status and the
    line after it reports the new one.

    Setup: `Start -> A,B,C,D,E -> Cold(2 of 3, k*=3) -> End`, every link with
    `AProb=0.0`/`ManTime=-1.0`, A/B/C with `Fixed` lives of 20, 40 and 60 hours
    and `Fixed` 200-hour repairs, D and E the unswitchable standbys. 80 sim
    hours, one run.

    Expected: A's failure at t=20 still leaves two ready paths, so nothing is
    failed and the group only degrades; B's failure at t=40 leaves one, and both
    D and E are then failed permanently in that one survey -- two
    `Switch_Failed` rows, each with `from` `Switch_Failed` for the second,
    because the first one has already been written. The group's own line after
    them is `Bad`, because no path of the three that are left is `LinkUp`.

    Source: Raptor 7 (`RunColdLogic`'s
    `switchStatus="none"` branches; raptor/core/engine/cold_standby.py:190-276
    and the ordering note), Raptor 7 (the resurvey;
    cold_standby.py:755-822), docs/engine-design/node-status-and-cold-standby
    .adoc (Structure)
    """
    result = run(_cold_group_spec(
        [_cold_block("A", 20.0), _cold_block("B", 40.0), _cold_block("C", 60.0),
         _cold_block("D", 500.0), _cold_block("E", 500.0)], 2, 3, 80.0),
        length=80.0)
    assert result.draws == 0
    assert_timeline(result, [
            (0, 'Block', 'A', '--', 'Good'),
            (0, 'Block', 'B', '--', 'Good'),
            (0, 'Block', 'C', '--', 'Good'),
            (0, 'Block', 'D', '--', 'Good'),
            (0, 'Block', 'E', '--', 'Good'),
            (0, 'Block', 'D', 'Good', 'Standby'),
            (0, 'Block', 'E', 'Good', 'Standby'),
            (0, 'Node', 'Cold', '--', 'Good'),
            (20, 'Block', 'A', 'Good', 'Failed'),
            (20, 'Block', 'A', 'Failed', 'Repairing'),
            (20, 'Node', 'Cold', 'Good', 'Degraded'),
            (20, 'System', 'State_change', '--', 'Yellow'),
            (40, 'Block', 'B', 'Good', 'Failed'),
            (40, 'Block', 'B', 'Failed', 'Repairing'),
            (40, 'Node', 'Cold', 'Degraded', 'Switch_Failed'),
            (40, 'Node', 'Cold', 'Switch_Failed', 'Switch_Failed'),
            (40, 'Node', 'Cold', 'Switch_Failed', 'Bad'),
            (40, 'Node', 'end', '--', 'Bad'),
            (40, 'System', 'State_change', 'Yellow', 'Red'),
            (60, 'Block', 'C', 'Good', 'Failed'),
            (60, 'Block', 'C', 'Failed', 'Repairing'),
            (60, 'Node', 'Cold', 'Bad', 'Bad'),
            (60, 'System', 'Red_change', '--', 'Red'),
            (80, 'Block', 'A', 'Repairing', 'Done'),
            (80, 'Block', 'B', 'Repairing', 'Done'),
            (80, 'Block', 'C', 'Repairing', 'Done'),
            (80, 'Block', 'D', 'Standby', 'Done'),
            (80, 'Block', 'E', 'Standby', 'Done'),
            (80, 'System', 'Phase_change', '--', 'Mission_failure'),
            (80, 'System', 'Simulation_Terminate', '--', '--'),
    ], where="cold-degraded")


def _pm_cut_hierarchy_spec(run_length: float = 50.0) -> dict[str, Any]:
    """Six blocks in one hierarchy, all six phased `Active`, `Cut`, `Active`.

    A, B and C carry a 20-hour PM that starts at t=25 and would still be running
    at both phase boundaries; D, E and F fail at t=20, 22 and 24 with 200-hour
    repairs, so they never come back. That is the whole point of the shape: the
    second phase cuts all six, which is the only way in this engine to move a
    link *back* from `Down` to `NodePM`, and the third phase restores them --
    the three PM blocks as `PM`, the three repairing blocks as `Repairing`.
    """
    cells = ["A: 1.0", "C: 1.0", "A: 1.0"]
    names = "ABCDEF"
    return {
        "system": {"run_length": run_length, "num_runs": 1,
                   "termination": "TimeSim"},
        "hierarchies": [{"id": 1, "name": "H1", "in_id": 2, "out_id": 4,
                         "level": 1}],
        "blocks": [
            {"name": name, "parent": 1, "fail_dist": FIXED,
             "fail_params": [500.0], "repair_dist": FIXED,
             "repair_params": [200.0], "uses_pm": True, "pm_freq": 25.0,
             "pm_dist": FIXED, "pm_params": [20.0], "pm_mis_defer": False}
            for name in ("A", "B", "C")
        ] + [
            {"name": name, "parent": 1, "fail_dist": FIXED,
             "fail_params": [life], "repair_dist": FIXED,
             "repair_params": [200.0]}
            for name, life in (("D", 20.0), ("E", 22.0), ("F", 24.0))
        ],
        "nodes": [
            {"id": 1, "name": "Start", "type": 1, "n": 0, "out": 1},
            {"id": 2, "name": "in", "type": 4, "n": 1, "out": 6, "parent": 1},
            {"id": 3, "name": "J", "type": 2, "n": 6, "out": 1, "k": 3,
             "parent": 1},
            {"id": 4, "name": "out", "type": 5, "n": 1, "out": 1, "parent": 1},
            {"id": 5, "name": "End", "type": 3, "n": 1, "out": 0},
        ],
        "links": [{"from": "Start", "to": "in"}]
                 + [{"from": "in", "to": name} for name in names]
                 + [{"from": name, "to": "J"} for name in names]
                 + [{"from": "J", "to": "out"}, {"from": "out", "to": "End"}],
        "phases": [{"name": f"P{index + 1}", "mission": 1, "dist": FIXED,
                    "params": [length]}
                   for index, length in enumerate((30.0, 10.0, 10.0))],
        "phase_states": {name: list(cells) for name in names},
    }


def test_a_phase_cut_moves_a_link_out_of_down_and_back_into_node_pm():
    """Cutting a block that is in PM moves its node link from `NodePM` to `Down`,
    and restoring it moves the link back to `NodePM` -- which is the only way in
    this engine to take a link out of `Down` and into the PM bucket, and hence
    the only way a node gets from `Bad` to `NodePM`.

    Why: a phase's `Cut` is an *active* status, and `ChangeBlockState`'s delta
    computation keys the active-status case first: from `Active` to `Cut` it is
    `RunChange=-1, FailChange=+1`, and from `Cut` back to `Active` it is the
    bucket the block's *own* status says -- so a block that was still in PM when
    the phase cut it comes back as `PM`, not `Good`
    (`raptor/core/engine/blocks.py:196-231`).
    `ChangePhases` applies the blocks in one pass and each block's own phase
    transition, so J is re-aggregated once per block: three `Bad -> Bad` rows
    as A, B and C individually come back still in PM, then `Bad -> NodePM` on
    the third, when `pm + idle + up` first reaches J's `k` of 3
    (and,
    `raptor/core/engine/cascade_phase.py:131-165`).

    Setup: `Start -> H1 -> A..F -> J(3 of 6) -> out -> End`, all six blocks
    phased `A: 1.0` / `C: 1.0` / `A: 1.0` over 30/10/10-hour mission phases.
    A, B and C have a `Fixed` 20-hour PM due every 25 hours; D, E and F have
    `Fixed` lives of 20, 22 and 24 hours and `Fixed` 200-hour repairs. 50 sim
    hours, one run.

    Expected: D, E and F's failures leave J `Degraded` (three of six paths up is
    still `k` of 6), then A, B and C's PMs at t=25 make it `NodePM` -- three
    `NodePM -> NodePM` rows, because each PM starts while the node is already
    `NodePM` and the link moves only on the third. The t=30 boundary cuts all
    six, so every link is `Down` and J is `Bad`; the t=40 boundary restores A,
    B and C into `PM` (they are still in their 20-hour PMs) and D, E and F back
    into `Repairing`, so the hierarchy goes `Bad -> NodePM` at the third restore
    and the PMs completing at t=45 take it back to `Good`.

    Source: Raptor 7 (`ChangeBlockState`'s delta computation;
    raptor/core/engine/blocks.py:196-231), Raptor 7
    (`ChangePhases`' own block application;
    raptor/core/engine/cascade_phase.py:131-165), Raptor 7 (the
    node side of the same change; cascade_phase.py:199-227),
    Raptor 7 (`determine_node_status`'s K-of-N;
    raptor/core/engine/nodes.py:235-314), Raptor 7 (the
    `typeNode=5` branch that logs the Out marker as the hierarchy; nodes.py:301
    -312), docs/engine-design/events-and-phases.adoc (Source anchors)
    """
    result = run(_pm_cut_hierarchy_spec())
    assert result.draws == 0
    assert_timeline(result, [
            (0, 'System', 'State_change', '--', 'Mission'),
            (0, 'Block', 'A', '--', 'Good'),
            (0, 'Block', 'B', '--', 'Good'),
            (0, 'Block', 'C', '--', 'Good'),
            (0, 'Block', 'D', '--', 'Good'),
            (0, 'Block', 'E', '--', 'Good'),
            (0, 'Block', 'F', '--', 'Good'),
            (20, 'Block', 'D', 'Good', 'Failed'),
            (20, 'Block', 'D', 'Failed', 'Repairing'),
            (20, 'Node', 'J', '--', 'Degraded'),
            (20, 'System', 'State_change', 'Mission', 'Yellow'),
            (22, 'Block', 'E', 'Good', 'Failed'),
            (22, 'Block', 'E', 'Failed', 'Repairing'),
            (22, 'Node', 'J', 'Degraded', 'Degraded'),
            (24, 'Block', 'F', 'Good', 'Failed'),
            (24, 'Block', 'F', 'Failed', 'Repairing'),
            (24, 'Node', 'J', 'Degraded', 'Degraded'),
            (25, 'Block', 'A', 'Good', 'PM_is_due'),
            (25, 'Block', 'B', 'Good', 'PM_is_due'),
            (25, 'Block', 'C', 'Good', 'PM_is_due'),
            (25, 'Block', 'A', 'PM_is_due', 'PM'),
            (25, 'Node', 'J', 'Degraded', 'NodePM'),
            (25, 'Hier', 'H1', '--', 'NodePM'),
            (25, 'Node', 'end', '--', 'NodePM'),
            (25, 'System', 'State_change', 'Yellow', 'Red'),
            (25, 'Block', 'B', 'PM_is_due', 'PM'),
            (25, 'Node', 'J', 'NodePM', 'NodePM'),
            (25, 'Block', 'C', 'PM_is_due', 'PM'),
            (25, 'Node', 'J', 'NodePM', 'NodePM'),
            (30, 'System', 'Phase_change', '--', 'Phase_change'),
            (30, 'Block', 'A', 'PM', 'Cut'),
            (30, 'Node', 'J', 'NodePM', 'Bad'),
            (30, 'Hier', 'H1', 'NodePM', 'Bad'),
            (30, 'Node', 'end', 'NodePM', 'Bad'),
            (30, 'Block', 'B', 'PM', 'Cut'),
            (30, 'Node', 'J', 'Bad', 'Bad'),
            (30, 'Block', 'C', 'PM', 'Cut'),
            (30, 'Node', 'J', 'Bad', 'Bad'),
            (30, 'Block', 'D', 'Repairing', 'Cut'),
            (30, 'Block', 'E', 'Repairing', 'Cut'),
            (30, 'Block', 'F', 'Repairing', 'Cut'),
            (30, 'System', 'Red_change', '--', 'Red'),
            (40, 'System', 'Phase_change', 'Phase_change', 'Phase_change'),
            (40, 'Block', 'A', 'Cut', 'PM'),
            (40, 'Node', 'J', 'Bad', 'Bad'),
            (40, 'Block', 'B', 'Cut', 'PM'),
            (40, 'Node', 'J', 'Bad', 'Bad'),
            (40, 'Block', 'C', 'Cut', 'PM'),
            (40, 'Node', 'J', 'Bad', 'NodePM'),
            (40, 'Hier', 'H1', 'Bad', 'NodePM'),
            (40, 'Node', 'end', 'Bad', 'NodePM'),
            (40, 'Block', 'D', 'Cut', 'Repairing'),
            (40, 'Block', 'E', 'Cut', 'Repairing'),
            (40, 'Block', 'F', 'Cut', 'Repairing'),
            (40, 'System', 'Red_change', 'Red', 'Red'),
            (45, 'Block', 'A', 'PM', 'PM_complete'),
            (45, 'Block', 'A', 'PM_complete', 'Good'),
            (45, 'Node', 'J', 'NodePM', 'NodePM'),
            (45, 'Block', 'B', 'PM', 'PM_complete'),
            (45, 'Block', 'B', 'PM_complete', 'Good'),
            (45, 'Node', 'J', 'NodePM', 'NodePM'),
            (45, 'Block', 'C', 'PM', 'PM_complete'),
            (45, 'Block', 'C', 'PM_complete', 'Good'),
            (45, 'Node', 'J', 'NodePM', 'Degraded'),
            (45, 'Hier', 'H1', 'NodePM', 'Good'),
            (45, 'Node', 'end', 'NodePM', 'Good'),
            (45, 'System', 'State_change', 'Red', 'Yellow'),
            (50, 'Block', 'A', 'Good', 'Done'),
            (50, 'Block', 'B', 'Good', 'Done'),
            (50, 'Block', 'C', 'Good', 'Done'),
            (50, 'Block', 'D', 'Repairing', 'Done'),
            (50, 'Block', 'E', 'Repairing', 'Done'),
            (50, 'Block', 'F', 'Repairing', 'Done'),
            (50, 'System', 'Phase_change', 'Phase_change', 'Mission_failure'),
            (50, 'System', 'Simulation_Terminate', '--', '--'),
    ], where="phase-cut-pm")
