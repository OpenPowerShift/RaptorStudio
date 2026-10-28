"""The tail of the cascade: the arms only a second cold group, a later phase
and a `pm_mis_defer` flag can reach.

Every fixture here exists because the mechanism it pins is a *second*
mechanism reacting to the first. That is why the spine cannot reach any of
them, and why `test_b_cascade.py` and `test_b_node_standby.py` -- which hold
the first mechanism's own rows -- do not hold these:

* **A cold node that lost its slot to a lower group does no switching of its
  own.** `run_cold_logic` is wrapped in `IF NOT higherCold ... ELSE
  ReconfigureNode(0,0,0,0,0,0)`, and the `ELSE` arm lives in the *caller*
  (`cascade_cold.py:84-101`) because the caller owns `reconfigure_node`'s
  arguments and the cascade. The observable is the shape of the log line
  itself: a frozen group writes the ordinary k-of-n comment
  (`2-1/2_ur4_ur5`), never the cold group's own (`2/2_up7_up6`), because the
  two aggregations are separate ports with separate comment builders.
* **A mission that begins in a later phase snapshots who is eligible.**
  `change_system_status`'s mission-BEGIN arm (`system_status.py:349-381`)
  asks every block, node and event whether it starts the mission already in a
  maintenance-like state, and only the weak-link report's `R` column
  (`mCompleted / missionsAttempted`) reads that answer back.
* **A `pm_mis_defer` block is not woken for opportunistic PM during a
  mission.** `_induce_dependency` swallows the `"OBPM"` reason outright
  (`cascade_dependency.py:133-135`), so the block's PM gate is never opened
  and it never writes a `PM_is_due` row.
* **The last two rows of the corpus.** `Node Bad -> Switch_Failed` and
  `Node NodePM -> Switch_Failed` were `planned` because a `Switch_Failed` row
  is written at the *front* of a survey that then logs the node's own new
  status, and neither `Bad` nor `NodePM` can be that status while the survey
  still has a candidate it wants to switch to (`runs + standbys >= k`).
  `tests/behaviour/test_b_final_rows.py`'s module docstring carries the full
  derivation. It closes here with **two cold groups and five blocks**, and the
  step that was missing is not a shape at all: the "none" status does not
  need the survey to fail, it needs a *link* to reach `LinkStandby` while the
  node is already down, and `set_switch_time` is called from
  `reconfigure_node` for **any** `LinkStandby` transition on a cold link
  (`nodes.py:441-447`), not only from a survey decision. So the route is a
  block *following* a demoted candidate into `Standby`
  (`BackFlowDependencies(NodeStandby)`, `dependency.py:242-248`),
  which is what `F` does below.

**Why five blocks and not six.** The obvious way to get a block into
`Standby` while its own node is down is `repair_completion_status`'s
`Standby` recovery, and that needs a dependency target *node* already in
`Standby` or `higherCold` -- two cold groups deep plus a 2-of-2 node, which is
the six-block shape `test_b_final_rows.py` describes. The block-level sibling
is free: `repair_completion_status`'s `depType="RBDBlock"` arm answers
`Standby` for a target block that is itself `Standby`
(`dependency.py:817-819`), and `F` below reaches it with a plain block
dependence on `D`. `model_checks.refuse_unrunnable` is not in the way and
never was: its one rule is that a candidate must not depend on *another path
into the same node* (`model_checks.py:101-110`), and `F` depends on `D`,
which is a candidate of a different cold group.

**Every model here is `Fixed` (distribution code 19)**, so each run is exact
and consumes zero randomness; every test asserts `Run.draws == 0` (B2). One
replication and at most five blocks and four nodes (B4); the slowest fixture
here is a few milliseconds. Every expectation is a literal written inline in
the `assert_timeline` call, which `test_b_behaviour_coverage.py`'s `ast` pass
reads without importing this module (B5).

Source: raptor/core/engine/cascade_cold.py:84-101 (the `higherCold` `ELSE`
arm), raptor/core/engine/cold_standby.py:323-338 (`run_cold_logic`'s own
`higherCold` guard) (the node-sourced switch-off that sets the
flag), raptor/core/engine/system_status.py:349-381 (the mission-begin
snapshot), raptor/core/engine/cascade_phase.py:340-353 (its only caller with
weak-link bookkeeping threaded), raptor/core/engine/cascade_dependency.py:
129-135 (the `"OBPM"` guard), raptor/core/engine/dependency.py:95-102 (the
deterministic `DepPM = 100 %` short-circuit) (the
`NodeStandby` dispatch and the follow into `Standby`),
raptor/core/engine/nodes.py:441-447 (`SetSwitchTime` on any cold link
reaching `LinkStandby`), raptor/core/engine/model_checks.py:101-110 (the
same-node refusal that is *not* a problem here);
docs/engine-design/node-status-and-cold-standby.adoc ("Structure": the
`higherCold` split and the `Switch_Failed` arm),
docs/engine-design/dependency-backflow.adoc ("Structure": the
`NodeStandby` follow); Raptor 7 (`RunColdLogic`'s `ELSE
ReconfigureNode`) (`GoToPM`'s `PMMisDefer` guard),
 (`BackFlowDependencies(NodeStandby)`) (a completing
block following a `Standby` target into `Standby`)
"""
from __future__ import annotations

from typing import Any

from .harness import (
    FIXED, assert_timeline, block, details, run)


def _cold(prio: int, a_prob: float = 1.0) -> dict[str, Any]:
    """The switching attributes every cold link carries.

    `AutoTime = 0.0` resolves `switch_status` to `"ready"` rather than
    `"auto"`, so no `PrepForSwitch`/`PerformSwitch` timer is ever started and
    the fixture stays draw-free. `a_prob = 1.0` is the deterministic automatic
    branch of `set_switch_time` (`cold_standby.py:103-104`), so no
    `UniformReal(0,1)` is drawn.

    **`a_prob = 0.0` is the whole of the `Switch_Failed` mechanism.** It takes
    `set_switch_time`'s manual branch instead, and with `ManTime = -1.0` there
    is no manual fallback either, so the link resolves to `"none"`
    (`cold_standby.py:118-121`) -- still with no draw, because `AProb` is
    compared against `1.0`/`0.0` before the draw is reached.
    """
    return {"cold_pri": prio, "a_prob": a_prob, "auto_time": 0.0,
            "man_time": -1.0}


def _cold_node(name: str, n: int, k: int) -> dict[str, Any]:
    """A cold-standby k-of-n node: `cold = 1` is what makes its status
    `run_cold_logic`'s rather than `determine_node_status`'s, and `k_star` is
    k* (`RunsRequired`'s upper bound). `pr = 0` / `autos = 0` are Raptor's own
    "no priority return" and "do not check automatics first" switches, so the
    survey keeps the source's single merged pass."""
    return {"name": name, "type": 2, "n": n, "out": 1, "k": k, "cold": 1,
            "k_star": k, "pr": 0, "autos": 0}


def _weak_row(result: Any, table: str, name: str) -> list[str]:
    """One row of the weak-link report's `BLOCK_RESULTS` / `EVENT_RESULTS`
    table, split on whitespace.

    The `R` column of that row is `mCompleted / missionsAttempted`
    (`reports._weak_block_metrics`), and its numerator is the mission bool a
    mission-BEGIN snapshot writes -- so the row is the only place the snapshot
    is readable from outside the engine.
    """
    tail = result.eor[result.eor.index(table):]
    header = next(i for i, line in enumerate(tail)
                  if line.startswith(("Block ", "Event "))
                  and "R" in line.split())
    columns = tail[header].split()
    for line in tail[header + 1:]:
        if not line.strip():
            break
        if line.split()[0] == name:
            return line.split()[columns.index("R"):columns.index("R") + 1]
    raise AssertionError(f"no {table} row for {name!r}")


# ---------------------------------------------------------------------------
# A frozen cold group
# ---------------------------------------------------------------------------


def test_a_cold_group_whose_slot_was_taken_aggregates_instead_of_switching() -> None:
    """A cold node frozen by a lower group runs no switching of its own: it
    aggregates k-of-n inline and parks nobody.

    Why: `run_cold_logic`'s whole body is wrapped in `IF NOT higherCold ...
    ELSE ReconfigureNode(0,0,0,0,0,0)` (opens it,
     closes it), so a node with `higherCold` set does not bucket
    its links, does not demote anybody and does not draw: the *caller*
    aggregates it instead, with all-zero deltas and `inPath = 0`, which is
    what makes it log the ordinary K-of-N line
    (`cascade_cold.py:84-101`). `higherCold` is written by exactly one thing
    in this file -- a **node**-sourced cold link losing its slot in a higher
    group (`cold_standby.py:621-644`) -- so the freeze has to come from a
    second cold group, and the two groups have to be listed lower-group-first
    for the freeze to land before the frozen group is surveyed.

    Setup: `Start -> {A, P, G}`; a cold 1-of-1 `Low` fed by `G` (prio 10) and
    by the node `High` (prio 20); a cold 1-of-1 `High` fed by `A` (prio 10) and
    `P` (prio 20); `Low -> End`. Node order is `Start, End, Low, High`, so
    `Low`'s kickoff survey runs first and freezes `High`. No block ever
    fails, 30 sim hours.

    Expected: `Low` keeps `G` and takes `High`'s slot, which sets `High`'s
    `higher_cold` flag; `High` then logs its kickoff line from the k-of-n
    aggregation (`2-1/2_ur4_ur5`) rather than from its own survey
    (`2/2_up10_up11`), and **`P` is never demoted** -- there is no
    `P -> Standby` row anywhere in the run, which is the whole difference
    between the two aggregations. The same three blocks with `High` listed
    *before* `Low` demote `P` at kickoff instead, so the order is the
    mechanism and not decoration.

    Source: raptor/core/engine/cold_standby.py:323-338 (`run_cold_logic`'s
    guard) (the node-sourced switch-off that sets `higher_cold`),
     (the cold group's own `N/M_...` comment),
    raptor/core/engine/cascade_cold.py:84-101 (the `ELSE` arm),
    raptor/core/engine/nodes.py:301-314 (the k-of-n `N-K/M_...` comment),
    raptor/core/engine/kickoff.py:580-594 (the per-cold-node kickoff drain,
    in node order); Raptor 7
    """
    result = run({
        "system": {"run_length": 30.0, "num_runs": 1,
                   "termination": "TimeSim"},
        "blocks": [block("A", 500.0, 10.0), block("P", 500.0, 10.0),
                   block("G", 500.0, 10.0)],
        "nodes": [
            {"name": "Start", "type": 1, "n": 0, "out": 3},
            {"name": "End", "type": 3, "n": 1, "out": 0},
            _cold_node("Low", 2, 1),
            _cold_node("High", 2, 1),
        ],
        "links": [
            {"from": "Start", "to": "A"},
            {"from": "Start", "to": "P"},
            {"from": "Start", "to": "G"},
            {"from": "A", "to": "High", **_cold(10)},
            {"from": "P", "to": "High", **_cold(20)},
            {"from": "High", "to": "Low", **_cold(20)},
            {"from": "G", "to": "Low", **_cold(10)},
            {"from": "Low", "to": "End"},
        ],
    })

    assert result.draws == 0, (
        "every distribution is Fixed and every AProb is exactly 1.0, so the "
        f"run must draw nothing; it drew {result.draws}")

    assert_timeline(result, [
        (0.0, "Block", "A", "--", "Good"),
        (0.0, "Block", "P", "--", "Good"),
        (0.0, "Block", "G", "--", "Good"),
        (0.0, "Node", "Low", "--", "Good"),
        (0.0, "Node", "High", "--", "Good"),
        (30.0, "Block", "A", "Good", "Done"),
        (30.0, "Block", "P", "Good", "Done"),
        (30.0, "Block", "G", "Good", "Done"),
        (30.0, "System", "Phase_change", "--", "Mission_success"),
        (30.0, "System", "Simulation_Terminate", "--", "--"),
    ])

    # The two aggregations are separate ports with separate comment builders,
    # so the *shape* of the line is the observable, not just its state.
    assert details(result, "Node", "Low") == ["2/2_up7_up6"], (
        "an unfrozen cold group logs its own N/M comment")
    assert details(result, "Node", "High") == ["2-1/2_ur4_ur5"], (
        "a higherCold node is aggregated by reconfigure_node, so it logs the "
        "ordinary N-K/M comment and never runs its own survey")
    assert [e.new for e in result.events if e.obj == "Block:P"] == [
        "Good", "Done"], "a frozen group demotes nobody, so P never parks"


# ---------------------------------------------------------------------------
# A mission that begins in a later phase
# ---------------------------------------------------------------------------


def test_a_mission_beginning_in_a_later_phase_snapshots_who_is_eligible() -> None:
    """A mission that begins at a phase boundary records, per element, whether
    that element was already in a maintenance-like state when it began.

    Why: `change_system_status`'s mission-BEGIN arm does not merely increment
    a counter. Before counting the mission it walks every live `BlockStatistics`,
    `NodeStatistics` and `EventStatistics` and sets each element's mission bool to
    "am I *not* already in maintenance?" (ported at
    `system_status.py:349-381`). An element that is already repairing when the
    mission starts therefore begins it **without credit**, and the only place
    that answer is readable is the weak-link report's `R` column
    (`mCompleted / missionsAttempted`). The same call also writes a
    `State_change` line even when the colour did not move, because the
    mission *status* changed (`system_status.py:398-426`) -- which is the row
    that makes the boundary visible in the timeline at all.

    Setup: `Start -> A -> E1 -> End`; one event `E1` at `p = 1.0` written
    `Fixed` so the run stays draw-free; `A` starts mid-repair (`sim_start = 6`,
    a 100-hour `Fixed` repair) so it is still `Repairing` at the boundary; two
    phases, `P1` **non**-mission for 20 hours and `P2` mission for 20.
    `weak_analysis` on, which is what threads the three stat stores into the
    call (`cascade_phase.py:346-353`). 40 sim hours.

    Expected: the mission opens at t=20 with the system `Red` throughout, and
    writes `Phase_change -> Mission` plus a `Red -> Red` `State_change` row.
    `A`, repairing at that instant, is credited nothing: `R` reads
    `0.000000000`. `E1`, armed and successful, is credited the mission:
    `R` reads `1.000000000`. The same three phases with `A` starting `Good`
    instead read `1.000000000` for the block, so the zero is the snapshot and
    not the mission's own success.

    Source: raptor/core/engine/system_status.py:349-381 (the mission-begin
    snapshot) (the `State_change` line the mission status alone
    earns), raptor/core/engine/cascade_phase.py:340-353 (the only caller that
    passes the three stat stores),
    raptor/core/engine/reports.py:`_weak_block_metrics`'s `R` column,
    docs/engine-design/events-and-phases.adoc ("Structure": a phase change
    "can begin or end a *mission*"); Raptor 7
    """
    result = run({
        "system": {"run_length": 40.0, "num_runs": 1,
                   "termination": "TimeSim"},
        "blocks": [block("A", 500.0, 100.0, sim_start=6)],
        "nodes": [{"name": "Start", "type": 1, "n": 0, "out": 1},
                  {"name": "End", "type": 3, "n": 1, "out": 0}],
        "links": [{"from": "Start", "to": "A"}, {"from": "A", "to": "E1"},
                  {"from": "E1", "to": "End"}],
        "events": [{"name": "E1", "dist": FIXED, "failVal": 1.0}],
        "phases": [{"name": "P1", "mission": 0, "dist": FIXED,
                    "params": [20.0]},
                   {"name": "P2", "mission": 1, "dist": FIXED,
                    "params": [20.0]}],
    }, weak_analysis=True)

    assert result.draws == 0, (
        "every distribution is Fixed and the event's outcome is Fixed(1.0), "
        f"so the run must draw nothing; it drew {result.draws}")

    assert_timeline(result, [
        (0.0, "Block", "A", "--", "Repairing"),
        (0.0, "Node", "end", "--", "Bad"),
        (0.0, "System", "State_change", "--", "Red"),
        (0.0, "Event", "E1", "--", "Armed"),
        (0.0, "Event", "E1", "Armed", "Success"),
        (0.0, "Block", "A", "Repairing", "Repairing"),
        (20.0, "System", "Phase_change", "--", "Phase_change"),
        (20.0, "System", "Phase_change", "Phase_change", "Mission"),
        (20.0, "System", "State_change", "Red", "Red"),
        (40.0, "Block", "A", "Repairing", "Done"),
        (40.0, "Event", "E1", "Success", "Armed"),
        (40.0, "System", "Phase_change", "Mission", "Mission_failure"),
        (40.0, "System", "Simulation_Terminate", "--", "--"),
    ])

    # A block repairing when the mission opens begins it without credit; the
    # event, armed and successful, is credited. The control run (A starting
    # `Good`) reads 1.000000000 for the block, so the zero is the snapshot.
    assert _weak_row(result, "BLOCK_RESULTS", "A") == ["0.000000000"]
    assert _weak_row(result, "EVENT_RESULTS", "E1") == ["1.000000000"]


# ---------------------------------------------------------------------------
# A pm_mis_defer block and an opportunistic PM
# ---------------------------------------------------------------------------


def test_a_pm_mis_deferring_block_is_not_sent_for_opportunistic_pm_in_a_mission() -> None:
    """A block that defers a PM past a mission boundary is not woken for
    opportunistic PM while the system is inside a mission phase.

    Why: `GoToPM` is a *draw*, not a transition: the block-level dispatch
    hands `_induce_dependency` an `"OBPM"` reason and the block's own gate
    decides whether to act on it. That gate is
    `usesPM AND NOT(pmMisDefer AND missionStatus = Mission AND activePhases >
    0)`, and it is the only reason this row cannot be written. So the three
    conditions are all load-bearing and all visible here: the draw really
    happened (`DepPM = 100 %` short-circuits to `"GoToPM"` with no
    randomness, `dependency.py:99-100`, and `Run.draws == 0` proves it), the
    block does use PM, the block does set `PMMisDefer`, the model has phases
    and the mission is open. Remove any one and `B` writes a `PM_is_due` row.

    Setup: `Start -> A -> J -> B -> End`; `J` a 1-of-1 node; `B` depends on `A`
    with `DepPM = 100 %` (so `A` going down draws `"GoToPM"` deterministically)
    and sets `pm_mis_defer`; `B`'s own `PMFreq` is 500, far past the run, so
    the *only* thing that could put `B` in PM is the opportunistic wake. One
    mission phase covering the whole 30-hour run.

    Expected: `A` fails at t=10, `J` and the system go `Bad`/`Red`, and `B`
    never writes `PM_is_due` and never leaves `Good` -- its row is
    `Good -> Done` at the end and nothing else. The dependence's `Down`
    dispatch reached `B` (it is the dispatch that produced the draw); what it
    did not do is open the gate.

    Source: raptor/core/engine/cascade_dependency.py:129-135 (`_induce_dependency`'s
    `"OBPM"` guard), raptor/core/engine/dependency.py:95-102 (the
    `DepPM = 100 %` short-circuit) (the `GoToPM` arm),
    raptor/core/engine/cascade_dependency.py:133-135's caller
    `dependency.py:600`'s own `usesPM` check;
    docs/engine-design/dependency-backflow.adoc ("Structure": `PM` ->
    "OBPM wake: block decides against its threshold"),
    docs/engine-design/events-and-phases.adoc ("Structure": `pmMisDefer`
    judged against the new mission status); Raptor 7
    """
    result = run({
        "system": {"run_length": 30.0, "num_runs": 1,
                   "termination": "TimeSim"},
        "blocks": [
            block("A", 10.0, 5.0),
            block("B", 500.0, 5.0, uses_pm=True, pm_freq=500.0,
                   pm_dist=FIXED, pm_params=[2.0], pm_mis_defer=True,
                   pm_refresh=False, pm_fail_reset=False,
                   dep_type="RBDBlock", dep_num=1, dep_idle=0.0,
                   dep_nothing=0.0, dep_pm=100.0, dep_fail=0.0),
        ],
        "nodes": [{"name": "Start", "type": 1, "n": 0, "out": 1},
                  {"name": "J", "type": 2, "n": 1, "out": 1, "k": 1},
                  {"name": "End", "type": 3, "n": 1, "out": 0}],
        "links": [{"from": "Start", "to": "A"}, {"from": "A", "to": "J"},
                  {"from": "J", "to": "B"}, {"from": "B", "to": "End"}],
        "phases": [{"name": "P1", "mission": 1, "dist": FIXED,
                    "params": [30.0]}],
    })

    assert result.draws == 0, (
        "DepPM = 100% resolves the dependence's action without a draw and "
        "B's PMFreq is past the run, so nothing stochastic is reached; "
        f"the run drew {result.draws}")

    assert_timeline(result, [
        (0.0, "System", "State_change", "--", "Mission"),
        (0.0, "Block", "A", "--", "Good"),
        (0.0, "Block", "B", "--", "Good"),
        (10.0, "Block", "A", "Good", "Failed"),
        (10.0, "Block", "A", "Failed", "Repairing"),
        (10.0, "Node", "J", "--", "Bad"),
        (10.0, "Node", "end", "--", "Bad"),
        (10.0, "System", "State_change", "Mission", "Red"),
        (15.0, "Block", "A", "Repairing", "Repaired"),
        (15.0, "Block", "A", "Repaired", "Good"),
        (15.0, "Node", "J", "Bad", "Good"),
        (15.0, "Node", "end", "Bad", "Good"),
        (15.0, "System", "State_change", "Red", "Green"),
        (25.0, "Block", "A", "Good", "Failed"),
        (25.0, "Block", "A", "Failed", "Repairing"),
        (25.0, "Node", "J", "Good", "Bad"),
        (25.0, "Node", "end", "Good", "Bad"),
        (25.0, "System", "State_change", "Green", "Red"),
        (30.0, "Block", "A", "Repairing", "Repaired"),
        (30.0, "Block", "A", "Repaired", "Good"),
        (30.0, "Node", "J", "Bad", "Good"),
        (30.0, "Node", "end", "Bad", "Good"),
        (30.0, "Block", "A", "Good", "Done"),
        (30.0, "Block", "B", "Good", "Done"),
        (30.0, "System", "State_change", "Red", "Green"),
        (30.0, "System", "Phase_change", "--", "Mission_failure"),
        (30.0, "System", "Simulation_Terminate", "--", "--"),
    ])

    assert details(result, "Block", "B") == ["InitializeStress=1.000000",
                                              "End_sim"], (
        "B is never sent for PM: an OBPM wake that reached it would have "
        "written a PM_is_due row before End_sim")


# ---------------------------------------------------------------------------
# The two Switch_Failed rows that follow a down node
# ---------------------------------------------------------------------------


def _switch_failed_spec(g: dict[str, Any]) -> dict[str, Any]:
    """The five-block model both `Switch_Failed` rows need.

    Two *disjoint* cold groups hang off the same Start node, which is what
    makes the step possible at all:

    * `C` is a cold 2-of-3 fed by `B1`, `B2` and `D`. `D` starts mid-repair
      (`sim_start = 6`, a 25-hour `Fixed` repair), so at kickoff only `B1` and
      `B2` are ready and there is **nothing to demote** -- `readyPaths = 2` is
      exactly `kMax`. When `D` repairs at t=25 the survey finds three ready
      paths for two slots and demotes `D` with `Too_many_on`
      (`cold_standby.py:657`). That is the late demotion the whole shape turns
      on, and it is late for a reason inside `run_cold_logic`: a `"none"`
      candidate that already exists when `N` goes down is consumed by *that*
      same survey, because the `"none"` arm sits in the initiation branch and
      the initiation branch is the branch a down node takes
      (`cold_standby.py:499-526`). The `"none"` status therefore has to be
      created **after** `N` is already down, which is why the demotion is a
      mid-run repair and not a kickoff redistribution.
    * `N` is a cold 2-of-2 fed by `F` and `G`. `F` depends on `D` with
      `DepNothing`, so `D`'s demotion -- which is a `Running -> Standby`
      transition, i.e. `StandbyChange = +1` -- fires
      `BackFlowDependencies(NodeStandby)` and `F`, a `Running` follower,
      follows it into `Standby` (`dependency.py:242-248`).
    * `F`'s link carries `AProb = 0.0` and `ManTime = -1.0`, so when it reaches
      `LinkStandby` `set_switch_time` resolves it to `"none"` **from the
      reconfiguration that carried `F` there** (`nodes.py:441-447`) -- not from
      a survey decision. That is the step `test_b_final_rows.py` could not
      find: it is not a switching decision at all.
    * `G` is the only difference between the two rows: it fails (leaving `N`
      `Bad`) or takes a 30-hour PM (leaving `N` `NodePM`).
    """
    return {
        "system": {"run_length": 30.0, "num_runs": 1,
                   "termination": "TimeSim"},
        "blocks": [
            block("B1", 500.0, 10.0),
            block("B2", 500.0, 10.0),
            block("D", 500.0, 25.0, sim_start=6),
            block("F", 500.0, 10.0, dep_type="RBDBlock", dep_num=3,
                   dep_idle=0.0, dep_nothing=100.0, dep_pm=0.0, dep_fail=0.0),
            g,
        ],
        "nodes": [
            {"name": "Start", "type": 1, "n": 0, "out": 5},
            {"name": "End", "type": 3, "n": 1, "out": 0},
            _cold_node("C", 3, 2),
            _cold_node("N", 2, 2),
        ],
        "links": [
            {"from": "Start", "to": "B1"},
            {"from": "Start", "to": "B2"},
            {"from": "Start", "to": "D"},
            {"from": "Start", "to": "F"},
            {"from": "Start", "to": "G"},
            {"from": "B1", "to": "C", **_cold(10)},
            {"from": "B2", "to": "C", **_cold(20)},
            {"from": "D", "to": "C", **_cold(30)},
            {"from": "C", "to": "End"},
            {"from": "F", "to": "N", **_cold(10, a_prob=0.0)},
            {"from": "G", "to": "N", **_cold(20)},
            {"from": "N", "to": "End"},
        ],
    }


def test_a_bad_node_reports_a_switch_failure_to_a_candidate_that_followed_into_standby() -> None:
    """`Node Bad -> Switch_Failed`: a cold node that has already gone `Bad`
    tries to switch to a candidate whose own link says it cannot be switched
    to, and says so.

    Why: `Switch_Failed` is written at the **front** of the survey that decides
    it (`cold_standby.py:443-451`), ahead of that survey's own aggregate line.
    So `Bad -> Switch_Failed` needs a node that was *already* `Bad` when a
    *later* survey ran, which the two `Switch_Failed` call sites cannot produce
    on their own: both are guarded on `readyPaths >= RunsRequired` (the
    promotion arm) or `readyPaths < RunsRequired >= kMin` (the
    initiation arms), and in either case
    `runs + standbys >= kMin`, so `determine_node_status`'s `Bad` /
    `NodePM` arms are unreachable from inside the same pass. What has to
    happen instead is a *link* reaching `LinkStandby` while the node is
    already down, because `ReconfigureNode` calls `SetSwitchTime` for any cold
    link that does (`nodes.py:441-447`) -- a reconfiguration, not a switching
    decision. `F` reaching `Standby` by following `D` is exactly that.

    Setup: `_switch_failed_spec` with `G` failing at t=15 (`Fixed` 15-hour life,
    12-hour repair), so `N` has `up = 1 < k = 2` and one dead path at t=15.
    Five blocks, four nodes, one replication, every distribution `Fixed`.

    Expected: `N` is `Good` at kickoff (nothing to demote: two ready paths for
    two slots) and goes `Bad` at t=15 when `G` fails. Nothing is switched at
    that instant -- there is no `"none"` candidate to consume yet. At t=25 `D`
    repairs, `C` demotes it, `F` follows into `Standby` and its link resolves
    to `"none"`; the survey of `N` that this marks now has `readyPaths = 0 <
    RunsRequired = 2`, reaches `F`, and writes `Node N Switch_Failed
    Unable_to_switch_to_F` while `N` is still `Bad`. The row after it is the
    same survey's aggregate, `Switch_Failed -> Bad`.

    Source: raptor/core/engine/cold_standby.py:443-451 (`note_switch_failed`'s
    placement) (the merged pass's `"none"` arm),
     (the `Too_many_on` demotion) (`_switch_failed`),
    raptor/core/engine/nodes.py:441-447 (`SetSwitchTime` on any cold link
    reaching `LinkStandby`) (the K-of-N arms a down node cannot
    reach mid-survey), raptor/core/engine/dependency.py:242-248
    (the `NodeStandby` dispatch and the follow into `Standby`);
    Raptor 7 (the merged initiation loop)
    """
    result = run(_switch_failed_spec(block("G", 15.0, 12.0)))

    assert result.draws == 0, (
        "every distribution is Fixed and F's AProb = 0.0 resolves without a "
        f"draw, so the run must draw nothing; it drew {result.draws}")

    assert_timeline(result, [
        (0.0, "Block", "B1", "--", "Good"),
        (0.0, "Block", "B2", "--", "Good"),
        (0.0, "Block", "D", "--", "Repairing"),
        (0.0, "Block", "F", "--", "Good"),
        (0.0, "Block", "G", "--", "Good"),
        (0.0, "Node", "C", "--", "Degraded"),
        (0.0, "Node", "N", "--", "Good"),
        (0.0, "Block", "D", "Repairing", "Repairing"),
        (0.0, "System", "State_change", "--", "Yellow"),
        (15.0, "Block", "G", "Good", "Failed"),
        (15.0, "Block", "G", "Failed", "Repairing"),
        (15.0, "Node", "N", "Good", "Bad"),
        (15.0, "Node", "end", "--", "Degraded"),
        (25.0, "Block", "D", "Repairing", "Repaired"),
        (25.0, "Block", "D", "Repaired", "Good"),
        (25.0, "Block", "D", "Good", "Standby"),
        (25.0, "Block", "F", "Good", "Standby"),
        (25.0, "Node", "C", "Degraded", "Good"),
        (25.0, "Node", "N", "Bad", "Switch_Failed"),
        (25.0, "Node", "N", "Switch_Failed", "Bad"),
        (27.0, "Block", "G", "Repairing", "Repaired"),
        (27.0, "Block", "G", "Repaired", "Good"),
        (27.0, "Node", "N", "Bad", "Bad"),
        (30.0, "Block", "B1", "Good", "Done"),
        (30.0, "Block", "B2", "Good", "Done"),
        (30.0, "Block", "D", "Standby", "Done"),
        (30.0, "Block", "F", "Standby", "Done"),
        (30.0, "Block", "G", "Good", "Done"),
        (30.0, "System", "Phase_change", "--", "Mission_success"),
        (30.0, "System", "Simulation_Terminate", "--", "--"),
    ])

    assert details(result, "Node", "N") == [
        "2/2_up10_up11", "1/2_up10_df11", "Unable_to_switch_to_F",
        "0/2_dd10_df11", "1/2_dd10_up11"], (
        "the Switch_Failed line names the candidate it could not switch to, "
        "and F's path is the `_dd` one from then on")
    assert details(result, "Block", "F") == [
        "InitializeStress=1.000000", "Dependence", "End_sim"], (
        "F follows D into Standby with the Dependence reason, which is the "
        "standby_change = +1 the NodeStandby dispatch keys on")


def test_a_node_pm_node_reports_a_switch_failure_to_a_candidate_that_followed_into_standby() -> None:
    """`Node NodePM -> Switch_Failed`: the same step, on a node that is
    `NodePM` rather than `Bad` when it happens.

    Why: identical mechanism to the `Bad` row, and identical reason why the
    step has to come from a reconfiguration: `NodePM` is
    `determine_node_status`'s **fourth** arm -- `pmPaths + idlePaths + upPaths
    >= k` -- which sits *below* `Idle` and *above* the `fails > 0` arm
    (`nodes.py:292-297`), so a node can be `NodePM` with a dead path and still
    reach it. That is the only difference between the two rows and it is worth
    a fixture of its own: the `Bad` row needs `up < k` with no idle and no PM
    path, and the `NodePM` row needs `up + idle < k <= pm + idle + up`, which
    a single failing block cannot produce. A PM path can.

    Setup: `_switch_failed_spec` with `G` taking a 30-hour `Fixed` PM due at
    t=15 instead of failing, so `N` has one running path, one PM path and
    `k = 2`: `up + idle = 1 < 2` but `pm + idle + up = 2 >= 2`. Five blocks,
    four nodes, one replication, every distribution `Fixed`.

    Expected: identical to the `Bad` row except for `G`'s own rows and `N`'s
    first downward move, which is `Good -> NodePM` at t=15. At t=25 `D`
    repairs, `C` demotes it, `F` follows into `Standby`, its link resolves to
    `"none"`, and the survey of `N` writes `Node N Switch_Failed
    Unable_to_switch_to_F` while `N` is `NodePM`. The same survey's aggregate
    then finds `F`'s path `_dd` and logs `Switch_Failed -> Bad`: a failed
    switch is a dead path, and one PM path plus one dead path is not
    `pm + idle + up >= k`, so the `fails > 0` arm takes it.

    Source: raptor/core/engine/nodes.py:292-297 (the `NodePM` arm and the
    `Bad` arm below it), raptor/core/engine/cold_standby.py:190-276
    (`_switch_failed`) (`note_switch_failed`'s placement),
     (the merged pass's `"none"` arm),
    raptor/core/engine/dependency.py:242-248;
    Raptor 7 (the merged initiation loop)
    """
    result = run(_switch_failed_spec(
        block("G", 500.0, 10.0, uses_pm=True, pm_freq=15.0, pm_dist=FIXED,
               pm_params=[30.0], pm_refresh=False, pm_fail_reset=False)))

    assert result.draws == 0, (
        "every distribution is Fixed, G's PM is a Fixed 30 hours and F's "
        f"AProb = 0.0, so the run must draw nothing; it drew {result.draws}")

    assert_timeline(result, [
        (0.0, "Block", "B1", "--", "Good"),
        (0.0, "Block", "B2", "--", "Good"),
        (0.0, "Block", "D", "--", "Repairing"),
        (0.0, "Block", "F", "--", "Good"),
        (0.0, "Block", "G", "--", "Good"),
        (0.0, "Node", "C", "--", "Degraded"),
        (0.0, "Node", "N", "--", "Good"),
        (0.0, "Block", "D", "Repairing", "Repairing"),
        (0.0, "System", "State_change", "--", "Yellow"),
        (15.0, "Block", "G", "Good", "PM_is_due"),
        (15.0, "Block", "G", "PM_is_due", "PM"),
        (15.0, "Node", "N", "Good", "NodePM"),
        (15.0, "Node", "end", "--", "Degraded"),
        (25.0, "Block", "D", "Repairing", "Repaired"),
        (25.0, "Block", "D", "Repaired", "Good"),
        (25.0, "Block", "D", "Good", "Standby"),
        (25.0, "Block", "F", "Good", "Standby"),
        (25.0, "Node", "C", "Degraded", "Good"),
        (25.0, "Node", "N", "NodePM", "Switch_Failed"),
        (25.0, "Node", "N", "Switch_Failed", "Bad"),
        (25.0, "Node", "end", "Degraded", "Degraded"),
        (30.0, "Block", "B1", "Good", "Done"),
        (30.0, "Block", "B2", "Good", "Done"),
        (30.0, "Block", "D", "Standby", "Done"),
        (30.0, "Block", "F", "Standby", "Done"),
        (30.0, "Block", "G", "PM", "Done"),
        (30.0, "System", "Phase_change", "--", "Mission_success"),
        (30.0, "System", "Simulation_Terminate", "--", "--"),
    ])

    assert details(result, "Node", "N") == [
        "2/2_up10_up11", "1/2_up10_dpm11", "Unable_to_switch_to_F",
        "0/2_dd10_dpm11"], (
        "the NodePM row's `_dpm` is the PM path, and F's path becomes the "
        "`_dd` one the Switch_Failed left behind")
