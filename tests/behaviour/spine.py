"""The spine: one canonical fixture per rung of the ladder (`README.md`).

The suite is a **ladder, not a matrix**: rung *n* is rung *n-1* plus exactly one
new mechanism, so a failing test localises to one mechanism. This module is
the ladder, written the way the harness requires it -- as a **chain of small pure
functions**, `_s3(_s2(_s1()))`, not one four-branch switch:

    rung  adds                  canonical fixture
    ----  --------------------  -------------------------------------------
    S0    nothing               (no fixture: the RNG and a distribution
                                 statistic are pure functions, and
                                 `tests/unit/test_scheduler.py` /
                                 `test_diststats.py` already pin them)
    S1    a scheduler, a process  one block between a Start and an End node
    S2    topology                S1 + a second block and a k-of-n Join node
    S3    one shared resource     S2 + a RESOURCE pool with a single unit
    S4    scheduled PM            S3 + a periodic PM on A
    S5    a trigger               S4 + a trigger that fires A's PM
    S6    a dependence            S5 + B depending on A
    S7    a spare pool            S6 + a pooled spare pool with RSO restock
    S8    cold standby            S7 + a cold group of 2 (C running, D standby)
    S9    phases                  S8 + two phases, the second non-mission
    S10   events                  S9 + an event whose outcome is certain
    S11   capacity                S10 + a flow limit on the event's out-link

Each `_sN()` returns a fresh spec dict built by calling `_s(N-1)()` and adding
exactly its own mechanism, so reading the file top to bottom reads like the
ladder. `spine(rung, **overrides)` is the one public entry point:

    spine(1)                      # S1's spec
    spine("S2")                   # by name
    spine(3, num_resources=2)     # ... with a two-unit repair pool

**One deviation from the ladder's first sketch, and why.** The sketch described S1 as "one
lone block, no model" and S2 as "S1 + Start/End nodes + links". A lone block
cannot be run through this harness: `build_from_spec` refuses a model with no
End node (`builder.validate`: "ERROR: no End (Type=3) node found") and
`load_model` refuses a block with no outgoing link
(`graph.build_efpa_graph`: "no outgoing link found for RBDBlock id=1"). So S1
carries the minimum topology the loader demands, and **S2's new mechanism is
the k-of-n Join**: the first rung where one failure degrades a node instead of
taking it down. S1's own claim -- "does one thing happen, then another, at the
right sim times?" -- is about the block column of its timeline; see
`test_b_ladder.py`.

Every distribution is `Fixed` (code 19), so each fixture is exact and consumes
zero randomness: `Run.draws == 0` for every rung, which is rule B2 asserted
rather than hoped for. **The event at S10 is the interesting case.** Raptor's
own event distribution is `Bernoulli`, and `bernoulli` draws unconditionally --
`return 1 if self._uniform_real(0.0, 1.0) <= probability else 0`
(`raptor/core/rng.py:302-306`) -- so `Bernoulli(1.0)` costs one stream draw for
an outcome that is not in doubt. S10 therefore writes the event's *outcome*
distribution as `Fixed` with the value `1.0`, which is `p = 1.0` made exact and
free: same `Armed -> Success` transition, zero draws. The measurement (1 draw
vs 0) is recorded in
`test_an_event_at_p_one_succeeds_and_is_rearmed_when_the_run_ends`'s docstring.

**One deviation from the sketch's table, and why.** The sketch
listed S6 as "S4 + a block depending on a block" and S7 as "S3 + a
pool": it branches. This file does not, because the harness requires the spine to
be a *chain* of one-line deltas (`_s3()` calls `_s2()` calls `_s1()`) and a
chain cannot branch. Each rung's added mechanism is exactly the one the
sketch named -- a dependence at S6, a pool at S7 -- so the content matches; only
the parent differs, and the parent's extra mechanisms (S4's PM, S5's trigger)
are inherited rather than dropped. The sketch's S6/S7 rows are therefore
satisfied as subsets of S6/S7 here.

**One forced shape, and why.** The sketch's S8 row read "S7 + a cold group of
2", which suggests the group could be A and B. It cannot: `refuse_unrunnable`
rejects a cold path into a node that depends on another path into the same node
-- real Raptor 7.0.11 crashes on that shape with an access violation -- and
S6's dependence is exactly `B -> A`. So the cold group is a **second stage**
of two new blocks, `C` and `D`, downstream of the k-of-n one; see `_s8`.
"""
from __future__ import annotations

import copy
from typing import Any

from .harness import FIXED, fixed

#: The run length every rung uses, in hours. Long enough for each block to
#: fail, be repaired and run again, short enough that the run ends while a
#: block is still Running (which is what pins the `Done` / `End_sim` line).
RUN_LENGTH = 100.0

#: S1's block: fails at 50, repaired at 60, so it fails twice inside the run
#: and is still Running at 100.
A_FAIL, A_REPAIR = 50.0, 10.0

#: S2's second block: fails at 55, while A is still Repairing, and its repair
#: is short (5) so the run's two blocks are out of step.
B_FAIL, B_REPAIR = 55.0, 5.0

#: S3's repair pool: one unit, so the second failure has to wait for it.
RESOURCE = "R"

#: S4's periodic PM on A: due every `PM_FREQ` hours, lasting `PM_DUR`.
#: `PMFreq=25` against A's `Fixed` 50-hour life is the *boundary* case
#: Raptor 7's race needs to be legible: at t=0 PM wins
#: (25 < 50); the clock is refilled to 25 when the PM completes at t=27, and
#: by then A's life has 25 hours left, so `PMTimeLeft < FailTimeLeft` is
#: 25 < 25 and the *failure* wins that race. `PMRefresh` and `PMFailReset` are
#: both off so that neither restarts the other clock -- they are S4-on-S4
#: options, not part of "a PM fires".
PM_FREQ, PM_DUR = 25.0, 2.0

#: S5's trigger. `repeats=1` fires every `TRIGGER_WAIT` hours for the whole
#: run, so A gets three PMs and the trigger's own `Fired -> Fired` transition
#: is pinned (a `repeats=0` trigger fires exactly once and shows nothing).
TRIGGER, TRIGGER_WAIT = "T1", 30.0

#: S7's pooled spare pool. Stock 1 is deliberately too small: A's PM takes the
#: only spare, so by the time B fails the pool is empty and B has to wait for
#: the `POOL_ARRIVE` restock. `MaxStock=1` in the End-of-Run row is then the
#: stock the pool ever held, not the two it was granted.
POOL, POOL_STOCK, POOL_ARRIVE = "SP", 1, 40.0

#: S8's cold group of 2. `COLD_RUN` is the higher-priority path, so it is the
#: one the group keeps running at kickoff; `COLD_STBY` starts Running like every
#: block and is switched to `Standby` by the first `RunColdLogic`. `COLD_RUN`
#: fails at 50 so the run reaches the other two arms too: its failure promotes
#: `COLD_STBY` (`Prior=Standby`) and its repair demotes it again
#: (`Too_many_on`). `COLD_STBY`'s life is past the run end, so the standby
#: member is never itself the cause of a switch.
COLD_RUN, COLD_STBY = "C", "D"
COLD_FAIL, COLD_REPAIR, COLD_STBY_LIFE = 50.0, 10.0, 200.0

#: S8's link priorities into the cold node. Lower sorts first
#: (`IdAndSortColds`'s selection sort), so `COLD_RUN` keeps the slot.
COLD_PRIO_RUN, COLD_PRIO_STBY = 10, 20

#: S9's two phases: a `Fixed` 60-hour mission phase, then a `Fixed` 40-hour
#: **non-mission** phase, which together are the rung's 100-hour run.
PH_MISSION, PH_NONMISSION = "P1", "P2"
PH_MISSION_LEN, PH_NONMISSION_LEN = 60.0, 40.0

#: S10's event, in series between the cold node and the End node, and the
#: probability column every event carries (`failVal`, which `ConductEvent`
#: draws *as* the Bernoulli parameter -- `1.0` means the event SUCCEEDS,
#: `raptor/core/engine/events.py:70-90`). The distribution is `Fixed` at that
#: same `1.0`, so the outcome is certain and free; see the module docstring.
EVENT, EVENT_P = "E1", 1.0

#: S11's capacity demand at the Start node, and the flow limit on the one link
#: that every unit of delivered flow must cross (`E1 -> End`). The demand is
#: twice the limit on purpose, so the cap is visible as a number the report
#: prints rather than as a number that happens to match.
FLOW_DEMAND, FLOW_LIMIT = 200, 100

#: Rungs this module can build, in ladder order.
RUNGS: tuple[int, ...] = (1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 11)

#: Rung number -> the rung's *name* in the ladder table, for error messages.
RUNG_NAMES: dict[int, str] = {
    1: "a scheduler and a process",
    2: "topology (k-of-n)",
    3: "one shared resource",
    4: "scheduled maintenance",
    5: "triggers",
    6: "dependencies",
    7: "spare pools",
    8: "cold standby",
    9: "phases",
    10: "events",
    11: "capacity",
}


def _block(name: str, fail: float, repair: float) -> dict[str, Any]:
    """One block with a `Fixed` life: fails at *fail*, repaired at
    ``fail + repair``, draws nothing."""
    return {**fixed(fail), "name": name,
            "repair_dist": FIXED, "repair_params": [repair]}


def _block_id(spec: dict[str, Any], name: str) -> int:
    """The `BLOCK_DATA` id `build_from_spec` will give block *name*.

    A spec dict carries no ids, so a field that wants one -- `DepNum`, and
    nothing else in this file does -- has to be given the number the builder
    will assign. `builder.next_id` hands out the lowest unused positive integer
    as the spec is walked, and `build_from_spec` walks `blocks` before `nodes`,
    so the *n*-th entry of `spec["blocks"]` is id *n*.
    """
    for index, block in enumerate(spec["blocks"], start=1):
        if block.get("name") == name:
            return index
    raise KeyError(f"no block named {name!r} in this spec")


def _s1() -> dict[str, Any]:
    """**S1 -- a scheduler and a process.**

    One block, with the least topology the loader will accept: a Start node,
    an End node, and the two links that wire the block between them. The rung
    isolates the block's own timeline: draw a life, fail, repair, run again,
    and go `Done` when the run ends.
    """
    return {
        "system": {"run_length": RUN_LENGTH, "num_runs": 1,
                   "termination": "TimeSim"},
        "blocks": [_block("A", A_FAIL, A_REPAIR)],
        "nodes": [
            {"name": "Start", "type": 1, "n": 0, "out": 1},
            {"name": "End", "type": 3, "n": 1, "out": 0},
        ],
        "links": [{"from": "Start", "to": "A"},
                  {"from": "A", "to": "End"}],
    }


def _s2() -> dict[str, Any]:
    """**S2 -- topology.** `_s1()` plus a second block in parallel with A, and
    a k-of-n `Join` node (k=1 of n=2) between the two and the End node.

    This is the first rung where a block failure does not simply take the
    system down: A's failure leaves the Join `Degraded` and the system
    `Yellow`, and only the second failure makes the End node `Bad` and the
    system `Red`. It is also the first rung where two blocks' timelines
    interleave, so it is where same-instant ordering across blocks is pinned.
    """
    spec = _s1()
    spec["blocks"].append(_block("B", B_FAIL, B_REPAIR))
    spec["nodes"] = [
        {"name": "Start", "type": 1, "n": 0, "out": 2},
        {"name": "Join", "type": 2, "n": 2, "out": 1, "k": 1},
        {"name": "End", "type": 3, "n": 1, "out": 0},
    ]
    spec["links"] = [{"from": "Start", "to": "A"},
                     {"from": "A", "to": "Join"},
                     {"from": "Start", "to": "B"},
                     {"from": "B", "to": "Join"},
                     {"from": "Join", "to": "End"}]
    return spec


def _s3() -> dict[str, Any]:
    """**S3 -- one shared resource.** `_s2()` plus a `RESOURCE` pool of one
    unit, borrowed by both blocks for the duration of a repair (`num_res=1`,
    `resource_name="R"`).

    B now fails while A holds the only unit, so B parks in `RepHold` at t=55
    and is granted the unit at t=60, the instant A's repair hands it back.
    `num_res=1` is load-bearing: source's resource check is `numDiffRes=1`
    exactly, so any other value borrows nothing at all.
    """
    spec = _s2()
    spec["blocks"] = [dict(b, num_res=1, resource_name=RESOURCE)
                      for b in spec["blocks"]]
    spec["resources"] = [{"name": RESOURCE, "num_in": 1}]
    return spec


def _s4() -> dict[str, Any]:
    """**S4 -- scheduled maintenance.** `_s3()` plus a *periodic* PM on A:
    `uses_pm=True` with `PMFreq=25.0` and a `Fixed` 2-hour PM duration.

    **The documented exception, which the ladder anticipates.** "a PM needs
    its repair resource, so S4 legitimately depends on S3" -- this rung's PM
    draws on the *same* one-unit `R` pool its repair does, because a block has
    one `#Res1pmRep` column that covers the PM and one `#Res1Rep` that covers
    the repair, and both default to 1. That is inherited from S3's row, not a
    second mechanism: nothing here configures a resource, a pool or a spare.

    What is new is visible twice over. A PMs at t=25 for two hours, and its
    two hours are **not operating time** -- A's `Fixed` 50-hour life therefore
    ends at t=52, not t=50, and the `Failed` line says
    `Operating_time=50.000000`. Then the refill makes the second race a tie
    that the failure wins (25 is not *less than* 25), so A's single failure
    moves to t=52 while B's stays at t=55: B parks in `RepHold` one hour
    *after* A does and is granted the unit at t=62, five hours later than S3's
    fixture puts it.
    """
    spec = _s3()
    for block in spec["blocks"]:
        if block["name"] == "A":
            block.update(uses_pm=True, pm_freq=PM_FREQ, pm_dist=FIXED,
                         pm_params=[PM_DUR], pm_refresh=False,
                         pm_fail_reset=False)
    return spec


def _s5() -> dict[str, Any]:
    """**S5 -- triggers.** `_s4()` plus a `T1` trigger that fires A's PM.

    One mechanism: the trigger. Its effect is to change *what decides when A's
    PM is due* -- `_pm_race` returns early for a `pmTriggered` block
    (gate both the refill and the race on
    `usesPM AND (NOT pmTriggered)`), so `PMFreq` stops mattering and PM becomes
    due only when `ControlPMtriggers` delivers `"Triggered_PM_Due"`.
   A is therefore configured with
    `pm_triggered=True, pm_trig="T1"` and S4's `PMFreq` becomes inert.

    `T1` is a `Fixed` 30-hour timer with `repeats=1`, so it fires at t=30, 60
    and 90 and A gets three PMs. A trigger that fired once would be a weaker
    fixture: its `Fired -> Fired` row is the transition the corpus actually
    exhibits.
    """
    spec = _s4()
    for block in spec["blocks"]:
        if block["name"] == "A":
            block.update(pm_triggered=True, pm_trig=TRIGGER)
    spec["triggers"] = [{"name": TRIGGER, "init_used": 0.0, "dist": FIXED,
                         "params": [TRIGGER_WAIT, 0.0, 0.0], "repeats": 1}]
    return spec


def _s6() -> dict[str, Any]:
    """**S6 -- dependencies.** `_s5()` plus B *depending on* A
    (`dep_type="RBDBlock"`, `DepNum`=A's id, `DepIdle=100%`).

    One mechanism: the dependence, with the `idle` reaction. `DepIdle=100.0`
    is the deterministic member of the reaction set -- `draw_dependency_action`
    short-circuits it to `"GoIdle"` with no draw at all, which is why this rung
    still consumes zero randomness (rule B2) where a mixed split would not.

    `BackFlowDependencies` dispatches on **Down**, and a block entering PM is
    Down, so the dependence fires twice: A's triggered PM at t=30 idles B, and
    A's failure at t=52 idles it again. Each is undone by the matching `AllUp`.
    A dependent's failure clock is paused while it is Idle, so B's `Fixed`
    55-hour life completes at t=67 -- twelve hours of idle time do not count
    toward it.
    """
    spec = _s5()
    a_id = _block_id(spec, "A")
    for block in spec["blocks"]:
        if block["name"] == "B":
            block.update(dep_type="RBDBlock", dep_num=a_id, dep_idle=100.0,
                         dep_nothing=0.0, dep_pm=0.0, dep_fail=0.0)
    return spec


def _s7() -> dict[str, Any]:
    """**S7 -- spare pools.** `_s6()` plus a `SP` pool with routine restocking
    (RSO), drawn by both blocks (`sp_type="Pooled"`, `spare_pool="SP"`).

    One mechanism: the pool. `sp_type` must be the on-disk string `"Pooled"` --
    `typed._SPARING_TYPE_BY_NAME` maps Raptor's own vocabulary, and the enum
    member name `"SparePool"` is *not* one of them, so writing it yields a
    block with no sparing at all (`pools-spares-resources.adoc`, "Port-only
    devices": a ColdPool or Resource `SpType` string loads as "no sparing").

    Stock 1 is too small on purpose, and each block still keeps S3's repair
    resource: A's PM takes the only spare, the t=40 restock brings it back, A's
    repair at t=52 takes it *again*, and B's repair at t=67 finds the pool
    empty -- so B parks in `RepHold` with `No_spare_in_stock-SP` /
    `Waiting_for_Spare` and is granted the t=80 arrival. That is the pool's
    whole behaviour in one run: grant, restock, run dry, grant again.
    """
    spec = _s6()
    for block in spec["blocks"]:
        block.update(sp_type="Pooled", spare_pool=POOL)
    spec["spare_pools"] = [{"name": POOL, "stock": POOL_STOCK, "new_spares": 1,
                            "arrive_every": POOL_ARRIVE, "rso": 1}]
    return spec


def _s8() -> dict[str, Any]:
    """**S8 -- cold standby.** `_s7()` plus a *cold group of 2* downstream of
    the k-of-n stage: blocks `C` and `D` into a `Cold` node (`cold=1`, `k=1`,
    `k*=1`), with the switching attributes on the two links into it.

    One mechanism: the cold group. `cold=1` is what makes the node's status
    `RunColdLogic`'s rather than `determine_node_status`'s
    aggregation, and the three arms of that routine are all reached in one run:

    * **kickoff demotion.** Every candidate block `Initialize`s `Running` and
      every link starts `"ready"` (`Link.SetToInitValues`), so with `RunsRequired = 1` and two ready paths
      `RunColdLogic` keeps the first and switches the rest to `Standby` with
      `Too_many_on`. The `SimTime>0.0` interrupt guard
      means the demotion logs a line and wakes nothing.
    * **switch-on.** `C` fails at `COLD_FAIL`; `D`'s `Standby` link is next in
      priority order and has room, so it is promoted (`Standby -> Good`,
      `Prior=Standby`).
    * **switch-off.** `C` is repaired at `COLD_FAIL + COLD_REPAIR` and now
      competes with the running `D` for the single slot, so it lands in `Standby`
      again (`Repaired -> Good -> Standby`, `Too_many_on`).

    **Why the group is a *new* pair and not A and B.** It cannot be A and B:
    `refuse_unrunnable` rejects a cold path into a node that depends on another
    path into the same node -- real Raptor 7.0.11 crashes on that shape with an
    access violation -- and S6's dependence is exactly `B -> A`
    (`model_checks.py:118`). That is the one place the ladder's stacking
    forces a shape rather than choosing one, and it is why the cold group is a
    second stage instead of a re-reading of the first.

    **The two blocks carry none of S3-S7's contention.** `C` and `D` borrow no
    repair resource and draw no spare, so the `R` pool and the `SP` pool are
    spent exactly where S7 spent them and **every `A`, `B`, `Pool` and `Trigger`
    row of S7's literal is reproduced unchanged**. What S8 adds is the new
    blocks' own rows, the cold node's line, one new `System:State_change` and
    one whose `from` moved (the group going `Degraded` at t=50 turns the system
    Yellow two hours before A's failure turns it Red), one `Red_change` row at
    t=60, and the two extra `Done` rows.

    **The link attributes are chosen to need no draw.** `AProb = 1.0` short-
    circuits `SetSwitchTime`'s `UniformReal(0,1)` draw
    (`raptor/core/engine/cold_standby.py:set_switch_time`),
    and `AutoTime = 0.0` makes the resolved `switch_status` `"ready"` rather than
    `"auto"`, so no `PrepForSwitch`/`PerformSwitch` timer is ever started.
    `ManTime = -1.0` says "no manual fallback"; it is never consulted, because
    the automatic branch is decided first. `ColdPri` 10 vs 20 is the only thing
    that decides which path runs.
    """
    spec = _s7()
    spec["blocks"].append(_block(COLD_RUN, COLD_FAIL, COLD_REPAIR))
    spec["blocks"].append(_block(COLD_STBY, COLD_STBY_LIFE, COLD_REPAIR))
    for node in spec["nodes"]:
        if node["name"] == "Join":
            node["out"] = 2
    spec["nodes"].append({"name": "Cold", "type": 2, "n": 2, "out": 1, "k": 1,
                          "cold": 1, "k_star": 1, "pr": 0, "autos": 0})
    spec["links"] = [
        {"from": "Start", "to": "A"},
        {"from": "A", "to": "Join"},
        {"from": "Start", "to": "B"},
        {"from": "B", "to": "Join"},
        {"from": "Join", "to": COLD_RUN},
        {"from": "Join", "to": COLD_STBY},
        {"from": COLD_RUN, "to": "Cold", "cold_pri": COLD_PRIO_RUN,
         "a_prob": 1.0, "auto_time": 0.0, "man_time": -1.0},
        {"from": COLD_STBY, "to": "Cold", "cold_pri": COLD_PRIO_STBY,
         "a_prob": 1.0, "auto_time": 0.0, "man_time": -1.0},
        {"from": "Cold", "to": "End"},
    ]
    return spec


def _s9() -> dict[str, Any]:
    """**S9 -- phases.** `_s8()` plus **two phases, the second of them a
    non-mission phase**: `PH_MISSION` (`Mission=1`) for a `Fixed`
    `PH_MISSION_LEN` hours, then `PH_NONMISSION` (`Mission=0`) for a `Fixed`
    `PH_NONMISSION_LEN`, which together are the rung's `RUN_LENGTH`.

    One mechanism: the phase table. It changes three things and nothing else.

    * **The run begins in a mission.** A phased model logs
      `System:State_change -- -> Mission` at kickoff, because phase 1 has
      `Mission=1` (`ChangeStatus`, `raptor/core/engine/system_status.py`).
    * **A phase boundary at `PH_MISSION_LEN`.** The phase clock
      (`ControlPhaseChanges`) logs
      `System:Phase_change -- -> Phase_change` (`Begin_phase_P2`) and TELLs
      `ConductPhaseChange`, whose mission-closing arm
      writes `System:Phase_change -> Mission_failure`. The run is `Red` at that
      instant -- A is `Repairing` and B is `Idle` -- so the mission that just
      ended is recorded as a *failed* one, which is why the word is
      `Mission_failure` and not `Mission_success`.
    * **No more mission-end line at `RUN_LENGTH`.** Unphased (S8), the run's end
      writes `Phase_change -- -> Mission_failure` at t=100 because the system
      ends `Red`. Phased, the mission already closed at the boundary, so t=100
      is only `Simulation_Terminate`.

    Every `Block`, `Node`, `Pool` and `Trigger` row is S8's: the boundary at
    t=60 lands between `C`'s repair and A's, and no element is phased, so
    `ChangePhases` has nothing to Cut, Link or re-stress. That is deliberate --
    per-element phase states are a second mechanism and belong to
    `test_b_phases.py` (S9 of the catalogue), not to this rung's fixture.
    """
    spec = _s8()
    spec["phases"] = [
        {"name": PH_MISSION, "mission": 1, "dist": FIXED,
         "params": [PH_MISSION_LEN]},
        {"name": PH_NONMISSION, "mission": 0, "dist": FIXED,
         "params": [PH_NONMISSION_LEN]},
    ]
    return spec


def _s10() -> dict[str, Any]:
    """**S10 -- events.** `_s9()` plus one event, `EVENT`, in series between
    the cold node and the End node (`Cold -> E1 -> End`).

    One mechanism: the event. It adds exactly three rows and moves nothing else:
    `E1 -- -> Armed` and `E1 Armed -> Success` at t=0 (`ConductEvent`, drawing `failVal`), and `E1 Success -> Armed` at
    `RUN_LENGTH` -- which is `EndSimulation`'s own event sweep putting every
    event back to `Armed` with the `End_sim` detail,
    the same sweep that puts every block to `Done`.

    **`failVal = 1.0` is a certainty, not a bias.** The column is the Bernoulli
    parameter, and `ConductEvent` reads `draw == 1.0` as success, so `p = 1.0`
    is an event that always succeeds. The distribution is written `Fixed` at
    that same `1.0` (`Fixed`'s draw is `Param(1)`, `raptor/core/rng.py:451-452`)
    rather than `Bernoulli`, because `bernoulli` draws a uniform even when the
    outcome is not in doubt (`raptor/core/rng.py:302-306`) and rule B2 wants
    this run to consume no randomness at all. Measured: `Bernoulli(1.0)` costs
    **1** draw and produces the identical event timeline; `Fixed(1.0)` costs
    **0**.

    Because the event succeeds, nothing downstream fails: the `End` node and
    the system behave exactly as S9's, which is the point of p = 1.0 as a rung
    -- it isolates the event's own state machine from its cascade.
    """
    spec = _s9()
    for link in spec["links"]:
        if link["to"] == "End":
            link["to"] = EVENT
    spec["events"] = [{"name": EVENT, "dist": FIXED, "failVal": EVENT_P}]
    spec["links"].append({"from": EVENT, "to": "End"})
    return spec


def _s11() -> dict[str, Any]:
    """**S11 -- capacity.** `_s10()` plus a **flow limit on one link**: a demand
    of `FLOW_DEMAND` at the Start node, and `nom_flow`/`max_flow` =
    `FLOW_LIMIT` on the single link every delivered unit must cross,
    `EVENT -> End`.

    One mechanism: the capacity analysis. **It moves no state.** The rung's
    timeline is S10's, row for row -- capacity is a reporting overlay, and
    `ChangeLinkStatus`'s `LinkUp`-crossing arm only sets
    `CapAnalysisRequired`, which the next
    `AnalyzeSystem` consumes into the report rather than into a state. So this
    rung's claim is a *report* claim, and `test_b_ladder.py`'s committed
    timeline is S10's unchanged. That is the honest shape of the mechanism: the
    rung's own test asserts the capacity rows, not the timeline.

    **The documented coupling, because the limit needs a second flag to bind.**
    `build_capacity_graph` derives a link's `sim_nom_flow`/`sim_max_flow` only
    on the **incoming** links of a *capacity-boundary* node
    (`raptor/core/engine/capacity.py:245-273`), and only when that node's
    `fullFlow` is off: `if node.full_flow: maximum_flow = INT_FLOW_MAX`.
    `make_node_row`'s default is `fullflow=1`
    (`raptor/core/fileio/rbdlib.py:190`), so this rung also sets
    `full_flow = 0` on the End node -- **that flag is part of the capacity
    mechanism, not a second one**, and without it the cap is silently ignored
    and the End row reports `N/A`. `raptor/api/validate.py::_check_capacity_settings`
    exists to warn about exactly that combination, and this fixture is the
    behaviour test for it.

    `FLOW_DEMAND = 200` is twice `FLOW_LIMIT = 100`, so the cap is a visible
    number (`MaxFlow 100`, `MaxCapacity 100`) rather than a coincidence.
    """
    spec = _s10()
    spec["system"].update({"capacity": True, "flow_generated": FLOW_DEMAND})
    for node in spec["nodes"]:
        if node["name"] == "End":
            node["full_flow"] = 0
    for link in spec["links"]:
        if link["from"] == EVENT:
            link.update(nom_flow=FLOW_LIMIT, max_flow=FLOW_LIMIT)
    return spec


#: rung -> its chain function. A lookup table over the chain, not a switch:
#: each rung's behaviour lives in its own `_sN`.
_BUILDERS = {1: _s1, 2: _s2, 3: _s3, 4: _s4, 5: _s5, 6: _s6, 7: _s7,
             8: _s8, 9: _s9, 10: _s10, 11: _s11}


def _rung_number(rung: int | str) -> int:
    """Accept ``1``, ``"1"``, ``"S1"``, ``"s1"``."""
    if isinstance(rung, int):
        number = rung
    else:
        text = str(rung).strip().lower()
        number = int(text[1:]) if text.startswith("s") else int(text)
    if number not in _BUILDERS:
        raise ValueError(
            f"no spine rung {rung!r} -- built so far: "
            + ", ".join(
                f"S{n} ({RUNG_NAMES[n]})" for n in RUNGS)
            + ". A new rung is a new `_sN` in this file, appended to "
              "`_BUILDERS` and `RUNGS`.")
    return number


def spine(rung: int | str = 1, *, run_length: float | None = None,
          num_runs: int | None = None, num_resources: int | None = None,
          ) -> dict[str, Any]:
    """The canonical spec for a rung of the ladder.

    ``rung`` is ``1``..``11`` or ``"S1"``..``"S11"``. Keyword overrides go on the
    result, so a test that wants S3 with a roomier repair pool asks for it
    rather than copying the spec:

    ==========  =====================================================
    override    effect
    ==========  =====================================================
    ``run_length``  ``system.run_length``, in hours
    ``num_runs``    ``system.num_runs`` (B4 says 1 unless the property is
                    about aggregation)
    ``num_resources``  units in the S3 repair pool; raises on a rung
                    that has no pool, rather than ignoring the request
    ==========  =====================================================

    Returns a fresh, mutable spec dict -- nothing is shared between calls, so
    a test may edit it freely.
    """
    number = _rung_number(rung)
    spec = copy.deepcopy(_BUILDERS[number]())
    if run_length is not None:
        spec["system"]["run_length"] = float(run_length)
    if num_runs is not None:
        spec["system"]["num_runs"] = int(num_runs)
    if num_resources is not None:
        pools = spec.get("resources")
        if not pools:
            raise ValueError(
                f"S{number} ({RUNG_NAMES[number]}) has no resource pool, so "
                f"num_resources={num_resources} has nothing to apply to -- "
                "the resource pool is the mechanism rung S3 adds")
        for pool in pools:
            pool["num_in"] = int(num_resources)
    return spec