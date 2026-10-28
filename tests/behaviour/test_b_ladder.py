"""The spine itself: every rung's canonical fixture produces exactly its
committed timeline.

The suite is built as a ladder rather than a
matrix, and this file is what makes that real rather than decorative. Each
rung is the rung below plus exactly one new mechanism, and each rung's
expected timeline is a literal committed here. So a mechanism introduced at
S7 that perturbs S1's numbers fails in `test_spine_rung_timeline_is_unchanged`
before any individual test has to be read -- which is the whole point of
building upwards.

**The literals are written inline in the `assert_timeline` call, not in a
module-level table**, even though that reads worse. `test_b_behaviour_coverage.py`
(the retirement gate) finds the transitions this suite pins with an `ast` pass over the test
modules, so it can only see a literal that is *syntactically* an argument of
the call; a name like `S2_TIMELINE` reads as `Name(id=...)` and the gate
reports the call as unreadable rather than silently ignoring it. One explicit
`elif` per rung, seven literals in the open, is the price of that gate being
able to see the suite. The literals are also the machine-readable index of
which transitions are pinned, so they are kept in a plain, regular
literal-of-tuples form: `(sim_time, object_type, object_name, from, to)`.
"""
from __future__ import annotations

import pytest

from .harness import (
    FIXED, assert_timeline, details, events, run, state_at)
from .spine import spine


@pytest.mark.parametrize("rung", (1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 11),
                         ids=("S1", "S2", "S3", "S4", "S5", "S6", "S7",
                              "S8", "S9", "S10", "S11"))
def test_spine_rung_timeline_is_unchanged(rung):
    """Each rung's canonical fixture produces exactly the committed timeline
    below, and nothing else.

    Why: the ladder is only additive if a lower rung's numbers cannot move.
    S1 pins one block's life end to end (draw a `Fixed` life, fail, repair,
    run again, `Done` at the run's end); S2 adds k-of-n topology, so A's
    failure degrades the `Join` and turns the system `Yellow` instead of
    taking it `Red`; S3 adds one shared repair resource, so B parks in
    `RepHold` while A holds the only unit; S4 adds a periodic PM on A, whose
    two hours are not operating time (so A's `Fixed` 50-hour life ends at 52,
    not 50); S5 replaces the PM *clock* with a trigger, which fires at 30/60/90;
    S6 makes B depend on A, so B idles on each of A's two `Down` dispatches
    and its paused failure clock pushes its failure from 55 to 67; S7 points
    both blocks at a one-spare pool, which runs dry and restocks. S8 adds a
    cold group of 2, whose three arms all appear in one run (kickoff demotion
    at t=0, switch-on at t=50, switch-off at t=60) and which costs the A and B
    rows nothing at all; S9 adds two phases whose second is a non-mission
    phase, which opens a mission at t=0 and closes it at the t=60 boundary;
    S10 adds one event whose outcome is certain (`Armed -> Success`, and
    `End_sim` re-arms it at the run end); S11 adds a flow limit, which is a
    *reporting* mechanism and moves no state at all -- so S11's literal is
    S10's, row for row. Each rung's added mechanism is the only difference
    between its literal and the one below, so a change to a lower rung fails
    here first.

    Setup: `spine(rung)`, one run of 100 sim hours, every distribution `Fixed`.

    Expected: the timeline in the literal for that rung, exactly. S0 is absent
    on purpose -- it is the pure-function layer (the RNG and a distribution
    statistic), pinned by `tests/unit/test_scheduler.py` and
    `tests/unit/test_diststats.py`, with no model to run.

    Source: Raptor 7 (`Run`'s fail-wait, `WAIT DURATION
    TimeToMaint`), Raptor 7 (`Repair`'s `WAIT DURATION
    TimeToRepair`), Raptor 7 (the `Failed` line, logged before
    the state change), Raptor 7 (`ChangeNodeState`), Raptor 7:
    231-251 (`AnalyzeSystem`'s colour), Raptor 7 (`Repair`
    waiting in `RepHold` for its resources), Raptor 7 (`Run`'s
    `PMTimeLeft`-vs-`FailTimeLeft` race), Raptor 7
    (`PullRapTrigger`), Raptor 7 (`ControlPMtriggers`),
    Raptor 7 (`BackFlowDependencies`), Raptor 7
    (`Repair` waiting on `PooledSpares`), Raptor 7
    (`GenerateSpares`); Raptor 7 (`IdAndSortColds`),
    Raptor 7 with (`RunColdLogic`: the kickoff
    demotion, its `SimTime>0.0` interrupt guard, and the switch-on promotion),
    Raptor 7 (`Link.SetToInitValues`: every link starts
    `"ready"`), Raptor 7 (`Link.SetSwitchTime`);
    Raptor 7 (`ControlPhaseChanges`), Raptor 7
    (`ConductPhaseChange`); Raptor 7 (`ConductEvent`),
    Raptor 7 (`ChangeEventState`), Raptor 7
    (`EndSimulation`'s event sweep);
    docs/engine-design/block-lifecycle.adoc (Source anchors, States),
    node-status-and-cold-standby.adoc (Purpose, Source anchors, Structure),
    pools-spares-resources.adoc (Structure),
    dependency-backflow.adoc (Structure), events-and-phases.adoc (Structure)
    """
    result = run(spine(rung))

    if rung == 1:
        # S1: one block. Every row below is either the block's own lifecycle
        # or the cascade of its one failure, in the order the cascade runs it.
        assert_timeline(result, [
            # A starts Running; its `Fixed` life then runs out at t=50.
            (0.0, "Block", "A", "--", "Good"),
            # `Failed` is logged while A is *still* Running -- `DetermineNewState`
            # writes it before it changes the state -- so it is its own row.
            (50.0, "Block", "A", "Good", "Failed"),
            (50.0, "Block", "A", "Failed", "Repairing"),
            # One block in series with the End node: its failure IS the
            # system's, so End goes Bad and the system Red in the same instant.
            (50.0, "Node", "end", "--", "Bad"),
            (50.0, "System", "State_change", "--", "Red"),
            # The `Fixed` 10-hour repair ends at t=60: "Repaired", then straight
            # back to "Good", then the cascade takes End and the system up.
            (60.0, "Block", "A", "Repairing", "Repaired"),
            (60.0, "Block", "A", "Repaired", "Good"),
            (60.0, "Node", "end", "Bad", "Good"),
            (60.0, "System", "State_change", "Red", "Green"),
            # The run ends at 100 with A still Running, so it goes Done.
            (100.0, "Block", "A", "Good", "Done"),
            (100.0, "System", "Phase_change", "--", "Mission_failure"),
            (100.0, "System", "Simulation_Terminate", "--", "--"),
        ], where="S1")

    elif rung == 2:
        # S2 adds a second block and a k-of-n Join (k=1 of n=2). The only rows
        # that differ from S1's are the Join's, B's, and the system's: one
        # failure now degrades rather than fails.
        assert_timeline(result, [
            (0.0, "Block", "A", "--", "Good"),
            (0.0, "Block", "B", "--", "Good"),
            (50.0, "Block", "A", "Good", "Failed"),
            (50.0, "Block", "A", "Failed", "Repairing"),
            # Still one good path of two, so `Join` is `Degraded`, not Bad.
            (50.0, "Node", "Join", "--", "Degraded"),
            (50.0, "System", "State_change", "--", "Yellow"),
            # B fails while A is still Repairing. Now 0 of 2, so the failure
            # reaches the End node and the system.
            (55.0, "Block", "B", "Good", "Failed"),
            (55.0, "Block", "B", "Failed", "Repairing"),
            (55.0, "Node", "Join", "Degraded", "Bad"),
            (55.0, "Node", "end", "--", "Bad"),
            (55.0, "System", "State_change", "Yellow", "Red"),
            # A's repair ends first (its `Fixed` 10 from t=50). Its cascade
            # runs before B's own repair completes at the same instant.
            (60.0, "Block", "A", "Repairing", "Repaired"),
            (60.0, "Block", "A", "Repaired", "Good"),
            (60.0, "Node", "Join", "Bad", "Degraded"),
            (60.0, "Node", "end", "Bad", "Good"),
            (60.0, "Block", "B", "Repairing", "Repaired"),
            (60.0, "Block", "B", "Repaired", "Good"),
            (60.0, "Node", "Join", "Degraded", "Good"),
            (60.0, "System", "State_change", "Red", "Green"),
            (100.0, "Block", "A", "Good", "Done"),
            (100.0, "Block", "B", "Good", "Done"),
            (100.0, "System", "Phase_change", "--", "Mission_failure"),
            (100.0, "System", "Simulation_Terminate", "--", "--"),
        ], where="S2")

    elif rung == 3:
        # S3 adds one `RESOURCE` unit shared by both repairs. Against S2's
        # literal the differences are B's `RepHold` at 55, the `Sparing` grant
        # at 60, and the last repair ending at 65 instead of 60.
        assert_timeline(result, [
            (0.0, "Block", "A", "--", "Good"),
            (0.0, "Block", "B", "--", "Good"),
            # A fails at 50 and takes the pool's only unit ("1R" = 1 of R).
            (50.0, "Block", "A", "Good", "Failed"),
            (50.0, "Block", "A", "Failed", "Repairing"),
            (50.0, "Node", "Join", "--", "Degraded"),
            (50.0, "System", "State_change", "--", "Yellow"),
            # B fails at 55 with the unit held: `RepHold`, not `Repairing`.
            (55.0, "Block", "B", "Good", "Failed"),
            (55.0, "Block", "B", "Failed", "RepHold"),
            (55.0, "Node", "Join", "Degraded", "Bad"),
            (55.0, "Node", "end", "--", "Bad"),
            (55.0, "System", "State_change", "Yellow", "Red"),
            # A's repair ends at 60 and hands the unit back; B is granted it at
            # that same instant, and its `Fixed` 5 puts it back in service at 65.
            (60.0, "Block", "A", "Repairing", "Repaired"),
            (60.0, "Block", "A", "Repaired", "Good"),
            (60.0, "Node", "Join", "Bad", "Degraded"),
            (60.0, "Node", "end", "Bad", "Good"),
            (60.0, "Block", "B", "RepHold", "Sparing"),
            (60.0, "Block", "B", "Sparing", "Repairing"),
            (60.0, "System", "State_change", "Red", "Yellow"),
            (65.0, "Block", "B", "Repairing", "Repaired"),
            (65.0, "Block", "B", "Repaired", "Good"),
            (65.0, "Node", "Join", "Degraded", "Good"),
            (65.0, "System", "State_change", "Yellow", "Green"),
            (100.0, "Block", "A", "Good", "Done"),
            (100.0, "Block", "B", "Good", "Done"),
            (100.0, "System", "Phase_change", "--", "Mission_failure"),
            (100.0, "System", "Simulation_Terminate", "--", "--"),
        ], where="S3")

    elif rung == 4:
        # S4 adds a periodic PM on A (`PMFreq=25`, `Fixed` 2 h, `PMRefresh` and
        # `PMFailReset` both off). Against S3's literal the differences are
        # exactly three, and all three follow from the PM:
        #   * A PMs at 25-27 and 87-89 (`PM_is_due -> PM -> PM_complete -> Good`),
        #   * A's failure moves from 50 to **52**, because the two PM hours are
        #     not operating time -- the `Failed` line says Operating_time=50.000000,
        #   * so B's repair wait moves from 60/65 to 62/67.
        # The PM clock is refilled to 25 when the PM completes, and by then A's
        # life has 25 hours left, so `PMTimeLeft < FailTimeLeft` is 25 < 25 and
        # the *failure* wins that race -- hence one PM before the failure, not
        # a PM every 25 hours.
        assert_timeline(result, [
            (0.0, "Block", "A", "--", "Good"),
            (0.0, "Block", "B", "--", "Good"),
            # First PM. A's node degrades and the system turns Yellow for the
            # two hours the block is in PM: a PM is a `Down` for the node.
            (25.0, "Block", "A", "Good", "PM_is_due"),
            (25.0, "Block", "A", "PM_is_due", "PM"),
            (25.0, "Node", "Join", "--", "Degraded"),
            (25.0, "System", "State_change", "--", "Yellow"),
            (27.0, "Block", "A", "PM", "PM_complete"),
            (27.0, "Block", "A", "PM_complete", "Good"),
            (27.0, "Node", "Join", "Degraded", "Good"),
            (27.0, "System", "State_change", "Yellow", "Green"),
            # A's `Fixed` 50-hour life now completes: 25 running hours before
            # the PM, 2 PM hours that do not count, 25 more after it.
            (52.0, "Block", "A", "Good", "Failed"),
            (52.0, "Block", "A", "Failed", "Repairing"),
            (52.0, "Node", "Join", "Good", "Degraded"),
            (52.0, "System", "State_change", "Green", "Yellow"),
            (55.0, "Block", "B", "Good", "Failed"),
            (55.0, "Block", "B", "Failed", "RepHold"),
            (55.0, "Node", "Join", "Degraded", "Bad"),
            (55.0, "Node", "end", "--", "Bad"),
            (55.0, "System", "State_change", "Yellow", "Red"),
            # A's repair ends at 52+10 and hands the unit back; B is granted it
            # at that same instant and its `Fixed` 5 puts it back in service.
            (62.0, "Block", "A", "Repairing", "Repaired"),
            (62.0, "Block", "A", "Repaired", "Good"),
            (62.0, "Node", "Join", "Bad", "Degraded"),
            (62.0, "Node", "end", "Bad", "Good"),
            (62.0, "Block", "B", "RepHold", "Sparing"),
            (62.0, "Block", "B", "Sparing", "Repairing"),
            (62.0, "System", "State_change", "Red", "Yellow"),
            (67.0, "Block", "B", "Repairing", "Repaired"),
            (67.0, "Block", "B", "Repaired", "Good"),
            (67.0, "Node", "Join", "Degraded", "Good"),
            (67.0, "System", "State_change", "Yellow", "Green"),
            # Second PM: the refilled 25-hour clock runs out 25 hours after A
            # is Good again at 62, so 62+25=87.
            (87.0, "Block", "A", "Good", "PM_is_due"),
            (87.0, "Block", "A", "PM_is_due", "PM"),
            (87.0, "Node", "Join", "Good", "Degraded"),
            (87.0, "System", "State_change", "Green", "Yellow"),
            (89.0, "Block", "A", "PM", "PM_complete"),
            (89.0, "Block", "A", "PM_complete", "Good"),
            (89.0, "Node", "Join", "Degraded", "Good"),
            (89.0, "System", "State_change", "Yellow", "Green"),
            (100.0, "Block", "A", "Good", "Done"),
            (100.0, "Block", "B", "Good", "Done"),
            (100.0, "System", "Phase_change", "--", "Mission_failure"),
            (100.0, "System", "Simulation_Terminate", "--", "--"),
        ], where="S4")

    elif rung == 5:
        # S5 adds a trigger, `T1`, that fires A's PM. Against S4's literal the
        # differences are the three `Trigger` rows and the PM times they
        # cause: the first PM moves from 25 to 30 (the trigger's own `Fixed`
        # 30-hour wait), and a *second* PM at 90 replaces S4's 87 -- there is
        # no PM clock left to run out, so PM happens exactly when T1 fires.
        # T1's middle firing at t=60 lands while B is in `RepHold` and changes
        # nothing, because the block PM would be due is not the one waiting.
        assert_timeline(result, [
            (0.0, "Block", "A", "--", "Good"),
            (0.0, "Block", "B", "--", "Good"),
            # T1's first firing, and the PM it causes in the same instant.
            (30.0, "Trigger", "T1", "--", "Fired"),
            (30.0, "Block", "A", "Good", "PM_is_due"),
            (30.0, "Block", "A", "PM_is_due", "PM"),
            (30.0, "Node", "Join", "--", "Degraded"),
            (30.0, "System", "State_change", "--", "Yellow"),
            (32.0, "Block", "A", "PM", "PM_complete"),
            (32.0, "Block", "A", "PM_complete", "Good"),
            (32.0, "Node", "Join", "Degraded", "Good"),
            (32.0, "System", "State_change", "Yellow", "Green"),
            (52.0, "Block", "A", "Good", "Failed"),
            (52.0, "Block", "A", "Failed", "Repairing"),
            (52.0, "Node", "Join", "Good", "Degraded"),
            (52.0, "System", "State_change", "Green", "Yellow"),
            (55.0, "Block", "B", "Good", "Failed"),
            (55.0, "Block", "B", "Failed", "RepHold"),
            (55.0, "Node", "Join", "Degraded", "Bad"),
            (55.0, "Node", "end", "--", "Bad"),
            (55.0, "System", "State_change", "Yellow", "Red"),
            # T1 fires again at 60. A is already due to fail (its life has 2
            # hours left) and B is waiting for the repair unit, so the wake
            # finds nothing to do: the row is logged, no block changes.
            (60.0, "Trigger", "T1", "Fired", "Fired"),
            (62.0, "Block", "A", "Repairing", "Repaired"),
            (62.0, "Block", "A", "Repaired", "Good"),
            (62.0, "Node", "Join", "Bad", "Degraded"),
            (62.0, "Node", "end", "Bad", "Good"),
            (62.0, "Block", "B", "RepHold", "Sparing"),
            (62.0, "Block", "B", "Sparing", "Repairing"),
            (62.0, "System", "State_change", "Red", "Yellow"),
            (67.0, "Block", "B", "Repairing", "Repaired"),
            (67.0, "Block", "B", "Repaired", "Good"),
            (67.0, "Node", "Join", "Degraded", "Good"),
            (67.0, "System", "State_change", "Yellow", "Green"),
            # Third firing at 90: the second PM, 30 hours on from the first
            # rather than S4's 25-hour refill.
            (90.0, "Trigger", "T1", "Fired", "Fired"),
            (90.0, "Block", "A", "Good", "PM_is_due"),
            (90.0, "Block", "A", "PM_is_due", "PM"),
            (90.0, "Node", "Join", "Good", "Degraded"),
            (90.0, "System", "State_change", "Green", "Yellow"),
            (92.0, "Block", "A", "PM", "PM_complete"),
            (92.0, "Block", "A", "PM_complete", "Good"),
            (92.0, "Node", "Join", "Degraded", "Good"),
            (92.0, "System", "State_change", "Yellow", "Green"),
            (100.0, "Block", "A", "Good", "Done"),
            (100.0, "Block", "B", "Good", "Done"),
            (100.0, "System", "Phase_change", "--", "Mission_failure"),
            (100.0, "System", "Simulation_Terminate", "--", "--"),
        ], where="S5")

    elif rung == 6:
        # S6 adds one dependence: B follows A (`DepType=RBDBlock`,
        # `DepNum`=A, `DepIdle=100%`). Against S5's literal the differences are
        # B's `Idle` rows at 30 and 52 (each undone by the matching `AllUp` at
        # 32 and 62), the `Join`/`End` rollups that follow them, and B's
        # failure moving from 55 to **67** -- twelve hours of Idle time do not
        # count toward B's `Fixed` 55-hour life.
        #
        # The dependence also fires on the PM at t=30, not just the failure:
        # `BackFlowDependencies` dispatches on `Down`, and a block in PM is
        # Down. That is why the system is `Red` from 30 to 32 in a run whose
        # k-of-n node could still have carried the mission.
        assert_timeline(result, [
            (0.0, "Block", "A", "--", "Good"),
            (0.0, "Block", "B", "--", "Good"),
            # T1 fires, A's PM starts, and B is idled in the same instant --
            # logged after A's own PM row, because the dependent's transition
            # and its cascade complete before the depended-on block's link
            # status is updated.
            (30.0, "Trigger", "T1", "--", "Fired"),
            (30.0, "Block", "A", "Good", "PM_is_due"),
            (30.0, "Block", "A", "PM_is_due", "PM"),
            (30.0, "Block", "B", "Good", "Idle"),
            (30.0, "Node", "Join", "--", "Degraded"),
            (30.0, "Node", "Join", "Degraded", "Idle"),
            (30.0, "Node", "end", "--", "Idle"),
            (30.0, "System", "State_change", "--", "Red"),
            # A's PM completes at 32 and the `AllUp` dispatch resumes B: the
            # End node is Good again while the Join is still counting A as
            # Down, so the Join's own `Good` row comes last.
            (32.0, "Block", "A", "PM", "PM_complete"),
            (32.0, "Block", "A", "PM_complete", "Good"),
            (32.0, "Block", "B", "Idle", "Good"),
            (32.0, "Node", "Join", "Idle", "Degraded"),
            (32.0, "Node", "end", "Idle", "Good"),
            (32.0, "Node", "Join", "Degraded", "Good"),
            (32.0, "System", "State_change", "Red", "Green"),
            (52.0, "Block", "A", "Good", "Failed"),
            (52.0, "Block", "A", "Failed", "Repairing"),
            (52.0, "Block", "B", "Good", "Idle"),
            (52.0, "Node", "Join", "Good", "Degraded"),
            (52.0, "Node", "Join", "Degraded", "Idle"),
            (52.0, "Node", "end", "Good", "Idle"),
            (52.0, "System", "State_change", "Green", "Red"),
            (60.0, "Trigger", "T1", "Fired", "Fired"),
            (62.0, "Block", "A", "Repairing", "Repaired"),
            (62.0, "Block", "A", "Repaired", "Good"),
            (62.0, "Block", "B", "Idle", "Good"),
            (62.0, "Node", "Join", "Idle", "Degraded"),
            (62.0, "Node", "end", "Idle", "Good"),
            (62.0, "Node", "Join", "Degraded", "Good"),
            (62.0, "System", "State_change", "Red", "Green"),
            # B's failure: 55 hours of life measured only while Running, plus
            # the 12 hours it spent Idle (30-32 and 52-62).
            (67.0, "Block", "B", "Good", "Failed"),
            (67.0, "Block", "B", "Failed", "Repairing"),
            (67.0, "Node", "Join", "Good", "Degraded"),
            (67.0, "System", "State_change", "Green", "Yellow"),
            (72.0, "Block", "B", "Repairing", "Repaired"),
            (72.0, "Block", "B", "Repaired", "Good"),
            (72.0, "Node", "Join", "Degraded", "Good"),
            (72.0, "System", "State_change", "Yellow", "Green"),
            # Third T1 firing: A's PM starts and B is idled again, so the
            # system is Red for the two hours of a PM it could survive.
            (90.0, "Trigger", "T1", "Fired", "Fired"),
            (90.0, "Block", "A", "Good", "PM_is_due"),
            (90.0, "Block", "A", "PM_is_due", "PM"),
            (90.0, "Block", "B", "Good", "Idle"),
            (90.0, "Node", "Join", "Good", "Degraded"),
            (90.0, "Node", "Join", "Degraded", "Idle"),
            (90.0, "Node", "end", "Good", "Idle"),
            (90.0, "System", "State_change", "Green", "Red"),
            (92.0, "Block", "A", "PM", "PM_complete"),
            (92.0, "Block", "A", "PM_complete", "Good"),
            (92.0, "Block", "B", "Idle", "Good"),
            (92.0, "Node", "Join", "Idle", "Degraded"),
            (92.0, "Node", "end", "Idle", "Good"),
            (92.0, "Node", "Join", "Degraded", "Good"),
            (92.0, "System", "State_change", "Red", "Green"),
            (100.0, "Block", "A", "Good", "Done"),
            (100.0, "Block", "B", "Good", "Done"),
            (100.0, "System", "Phase_change", "--", "Mission_failure"),
            (100.0, "System", "Simulation_Terminate", "--", "--"),
        ], where="S6")

    elif rung == 7:
        # S7 adds one spare pool, `SP`: stock 1, one spare per 40 hours (RSO),
        # drawn by both blocks. Against S6's literal the differences are the
        # two `Pool` rows and B's failure, which now *waits for a spare*.
        #
        # The pool is the whole rung: A's PM at t=30 takes the only spare
        # (`PMSpare=1` by default), the t=40 restock brings the stock to 1,
        # A's repair at t=52 takes that one too, and B's repair at t=67 finds
        # the pool empty. So B parks in `RepHold` -- not for the repair unit,
        # which A handed back at 62, but `Waiting_for_Spare` -- and is granted
        # the t=80 arrival in the same instant that arrival is logged.
        #
        # T1's third firing at t=90 leaves A in `pmReqDefer`: the PM is due but
        # the pool is empty again, so the PM's spare request is deferred rather
        # than granted. A is still `pmReqDefer` at t=100 when the run ends and
        # goes `Done` from there -- a block waiting for a spare at the end of
        # the run is `Done`, not `RepHold`.
        assert_timeline(result, [
            (0.0, "Block", "A", "--", "Good"),
            (0.0, "Block", "B", "--", "Good"),
            (30.0, "Trigger", "T1", "--", "Fired"),
            (30.0, "Block", "A", "Good", "PM_is_due"),
            (30.0, "Block", "A", "PM_is_due", "PM"),
            # A's PM drew the pool's only spare; B is idled by the dependence
            # as at S6.
            (30.0, "Block", "B", "Good", "Idle"),
            (30.0, "Node", "Join", "--", "Degraded"),
            (30.0, "Node", "Join", "Degraded", "Idle"),
            (30.0, "Node", "end", "--", "Idle"),
            (30.0, "System", "State_change", "--", "Red"),
            (32.0, "Block", "A", "PM", "PM_complete"),
            (32.0, "Block", "A", "PM_complete", "Good"),
            (32.0, "Block", "B", "Idle", "Good"),
            (32.0, "Node", "Join", "Idle", "Degraded"),
            (32.0, "Node", "end", "Idle", "Good"),
            (32.0, "Node", "Join", "Degraded", "Good"),
            (32.0, "System", "State_change", "Red", "Green"),
            # First RSO restock: one new spare, stock back to 1.
            (40.0, "Pool", "SP", "--", "Sparing"),
            (52.0, "Block", "A", "Good", "Failed"),
            (52.0, "Block", "A", "Failed", "Repairing"),
            (52.0, "Block", "B", "Good", "Idle"),
            (52.0, "Node", "Join", "Good", "Degraded"),
            (52.0, "Node", "Join", "Degraded", "Idle"),
            (52.0, "Node", "end", "Good", "Idle"),
            (52.0, "System", "State_change", "Green", "Red"),
            (60.0, "Trigger", "T1", "Fired", "Fired"),
            (62.0, "Block", "A", "Repairing", "Repaired"),
            (62.0, "Block", "A", "Repaired", "Good"),
            (62.0, "Block", "B", "Idle", "Good"),
            (62.0, "Node", "Join", "Idle", "Degraded"),
            (62.0, "Node", "end", "Idle", "Good"),
            (62.0, "Node", "Join", "Degraded", "Good"),
            (62.0, "System", "State_change", "Red", "Green"),
            # B fails 12 hours late (the dependence's paused clock) and finds
            # the pool empty: `Sparing` is the "no spare in stock" line, then
            # `RepHold` is `Waiting_for_Spare`. A's repair unit is free, so
            # this wait is entirely the pool's.
            (67.0, "Block", "B", "Good", "Failed"),
            (67.0, "Block", "B", "Failed", "Sparing"),
            (67.0, "Block", "B", "Sparing", "RepHold"),
            (67.0, "Node", "Join", "Good", "Degraded"),
            (67.0, "System", "State_change", "Green", "Yellow"),
            # The t=80 arrival is both logged and granted to B in the same
            # instant, so `Pool Sparing` precedes B's grant row.
            (80.0, "Pool", "SP", "Sparing", "Sparing"),
            (80.0, "Block", "B", "RepHold", "Sparing"),
            (80.0, "Block", "B", "Sparing", "Repairing"),
            (85.0, "Block", "B", "Repairing", "Repaired"),
            (85.0, "Block", "B", "Repaired", "Good"),
            (85.0, "Node", "Join", "Degraded", "Good"),
            (85.0, "System", "State_change", "Yellow", "Green"),
            # T1's last firing: A's PM is due, the pool is empty again, so the
            # PM's spare request defers instead of being granted.
            (90.0, "Trigger", "T1", "Fired", "Fired"),
            (90.0, "Block", "A", "Good", "PM_is_due"),
            (90.0, "Block", "A", "PM_is_due", "pmReqDefer"),
            (100.0, "Block", "A", "pmReqDefer", "Done"),
            (100.0, "Block", "B", "Good", "Done"),
            (100.0, "System", "Phase_change", "--", "Mission_failure"),
            (100.0, "System", "Simulation_Terminate", "--", "--"),
        ], where="S7")

    elif rung == 8:
        # S8 adds one cold group of 2: blocks C and D into a `Cold` node
        # (`Cold=1`, `k=1`, `k*=1`) with `ColdPri` 10 and 20, `AProb=1.0`,
        # `AutoTime=0.0`, `ManTime=-1.0` on the two links into it.
        #
        # Against S7's literal the differences are *only* the new blocks' rows,
        # the cold node's own line, one new `State_change` and one whose `from`
        # moved (both because the cold group goes `Degraded` at t=50), and the
        # extra `Done` rows. Every `A`, `B`, `Pool` and `Trigger` row is
        # byte-identical to S7 -- the new blocks borrow no repair resource and
        # draw no spare, so the `R` and `SP` pools are spent exactly where S7
        # spent them. That is what makes this rung legible: the switching is
        # the *only* variable.
        #
        # All three arms of `RunColdLogic` are in the run:
        #   * t=0  kickoff demotion. Every link starts `"ready"`
        #     (`Link.SetToInitValues`) and two ready
        #     paths into a 1-of-2 group means one is switched to `Standby` with
        #     `Too_many_on`. The `SimTime>0.0` guard means
        #     the demotion wakes nothing -- hence D's `Run` is not interrupted.
        #   * t=50 switch-on. C fails; D's `Standby` link has room and is
        #     promoted (`Prior=Standby`), and the node
        #     logs `Degraded` -- one good path of two, exactly like S2's Join.
        #   * t=60 switch-off. C is repaired and now competes with the running
        #     D for the single slot, so it lands in `Standby` again.
        assert_timeline(result, [
            (0.0, "Block", "A", "--", "Good"),
            (0.0, "Block", "B", "--", "Good"),
            (0.0, "Block", "C", "--", "Good"),
            (0.0, "Block", "D", "--", "Good"),
            # D has the lower `ColdPri`, so C keeps the slot and D waits. The
            # cold node's own first line is `1/2_up7_s8`: 1 of 2 up, link 7 up,
            # link 8 in standby.
            (0.0, "Block", "D", "Good", "Standby"),
            (0.0, "Node", "Cold", "--", "Good"),
            (30.0, "Trigger", "T1", "--", "Fired"),
            (30.0, "Block", "A", "Good", "PM_is_due"),
            (30.0, "Block", "A", "PM_is_due", "PM"),
            (30.0, "Block", "B", "Good", "Idle"),
            (30.0, "Node", "Join", "--", "Degraded"),
            (30.0, "Node", "Join", "Degraded", "Idle"),
            (30.0, "Node", "end", "--", "Idle"),
            (30.0, "System", "State_change", "--", "Red"),
            (32.0, "Block", "A", "PM", "PM_complete"),
            (32.0, "Block", "A", "PM_complete", "Good"),
            (32.0, "Block", "B", "Idle", "Good"),
            (32.0, "Node", "Join", "Idle", "Degraded"),
            (32.0, "Node", "end", "Idle", "Good"),
            (32.0, "Node", "Join", "Degraded", "Good"),
            (32.0, "System", "State_change", "Red", "Green"),
            (40.0, "Pool", "SP", "--", "Sparing"),
            # Switch-on: C fails on the same schedule as A did at S3, D takes
            # over, and the group degrades the system to Yellow an instant
            # before A's own failure takes it Red. Without the cold group the
            # system would have gone Green -> Red in one step at t=50.
            (50.0, "Block", "C", "Good", "Failed"),
            (50.0, "Block", "C", "Failed", "Repairing"),
            (50.0, "Block", "D", "Standby", "Good"),
            (50.0, "Node", "Cold", "Good", "Degraded"),
            (50.0, "System", "State_change", "Green", "Yellow"),
            (52.0, "Block", "A", "Good", "Failed"),
            (52.0, "Block", "A", "Failed", "Repairing"),
            (52.0, "Block", "B", "Good", "Idle"),
            (52.0, "Node", "Join", "Good", "Degraded"),
            (52.0, "Node", "Join", "Degraded", "Idle"),
            (52.0, "Node", "end", "Good", "Idle"),
            (52.0, "System", "State_change", "Yellow", "Red"),
            (60.0, "Trigger", "T1", "Fired", "Fired"),
            # Switch-off: C is repaired, finds D holding the slot, and is
            # demoted again. `Repaired -> Good -> Standby`, so the repair
            # completes into a state change rather than into `Good`.
            (60.0, "Block", "C", "Repairing", "Repaired"),
            (60.0, "Block", "C", "Repaired", "Good"),
            (60.0, "Block", "C", "Good", "Standby"),
            (60.0, "Node", "Cold", "Degraded", "Good"),
            # A `Red_change` row for the instant itself: the cold group's line
            # above was the only re-report the run makes, so the deferred
            # end-of-instant system check runs against it. S7 had none.
            (60.0, "System", "Red_change", "--", "Red"),
            (62.0, "Block", "A", "Repairing", "Repaired"),
            (62.0, "Block", "A", "Repaired", "Good"),
            (62.0, "Block", "B", "Idle", "Good"),
            (62.0, "Node", "Join", "Idle", "Degraded"),
            (62.0, "Node", "end", "Idle", "Good"),
            (62.0, "Node", "Join", "Degraded", "Good"),
            (62.0, "System", "State_change", "Red", "Green"),
            (67.0, "Block", "B", "Good", "Failed"),
            (67.0, "Block", "B", "Failed", "Sparing"),
            (67.0, "Block", "B", "Sparing", "RepHold"),
            (67.0, "Node", "Join", "Good", "Degraded"),
            (67.0, "System", "State_change", "Green", "Yellow"),
            (80.0, "Pool", "SP", "Sparing", "Sparing"),
            (80.0, "Block", "B", "RepHold", "Sparing"),
            (80.0, "Block", "B", "Sparing", "Repairing"),
            (85.0, "Block", "B", "Repairing", "Repaired"),
            (85.0, "Block", "B", "Repaired", "Good"),
            (85.0, "Node", "Join", "Degraded", "Good"),
            (85.0, "System", "State_change", "Yellow", "Green"),
            (90.0, "Trigger", "T1", "Fired", "Fired"),
            (90.0, "Block", "A", "Good", "PM_is_due"),
            (90.0, "Block", "A", "PM_is_due", "pmReqDefer"),
            (100.0, "Block", "A", "pmReqDefer", "Done"),
            (100.0, "Block", "B", "Good", "Done"),
            # The standby block goes to `Done` from `Standby`, not from `Good`.
            (100.0, "Block", "C", "Standby", "Done"),
            (100.0, "Block", "D", "Good", "Done"),
            (100.0, "System", "Phase_change", "--", "Mission_failure"),
            (100.0, "System", "Simulation_Terminate", "--", "--"),
        ], where="S8")

    elif rung == 9:
        # S9 adds two phases: P1 is a mission phase of a `Fixed` 60 hours, P2
        # is a **non-mission** phase of a `Fixed` 40, together the 100-hour run.
        #
        # Against S8's literal the differences are five rows and no other: the
        # mission the run opens at t=0 (`Began_a_mission`), the phase boundary's
        # `Begin_phase_P2`, the mission *closing* as a failure because the
        # system is Red there, and -- the one that is easy to miss -- the
        # `Phase_change -- -> Mission_failure` row at t=100 that S8 wrote is
        # **gone**, because the mission already ended at the boundary and an
        # unphased run has only that one place to report a failed mission.
        # Every Block, Node, Pool and Trigger row is S8's: no element is
        # phased, so `ChangePhases` has nothing to Cut, Link or re-stress.
        assert_timeline(result, [
            # A phased model opens in a mission; an unphased one cannot.
            (0.0, "System", "State_change", "--", "Mission"),
            (0.0, "Block", "A", "--", "Good"),
            (0.0, "Block", "B", "--", "Good"),
            (0.0, "Block", "C", "--", "Good"),
            (0.0, "Block", "D", "--", "Good"),
            (0.0, "Block", "D", "Good", "Standby"),
            (0.0, "Node", "Cold", "--", "Good"),
            (30.0, "Trigger", "T1", "--", "Fired"),
            (30.0, "Block", "A", "Good", "PM_is_due"),
            (30.0, "Block", "A", "PM_is_due", "PM"),
            (30.0, "Block", "B", "Good", "Idle"),
            (30.0, "Node", "Join", "--", "Degraded"),
            (30.0, "Node", "Join", "Degraded", "Idle"),
            (30.0, "Node", "end", "--", "Idle"),
            # `from` is `Mission`, not `--`: the system was already in a mission
            # before it went Red.
            (30.0, "System", "State_change", "Mission", "Red"),
            (32.0, "Block", "A", "PM", "PM_complete"),
            (32.0, "Block", "A", "PM_complete", "Good"),
            (32.0, "Block", "B", "Idle", "Good"),
            (32.0, "Node", "Join", "Idle", "Degraded"),
            (32.0, "Node", "end", "Idle", "Good"),
            (32.0, "Node", "Join", "Degraded", "Good"),
            (32.0, "System", "State_change", "Red", "Green"),
            (40.0, "Pool", "SP", "--", "Sparing"),
            (50.0, "Block", "C", "Good", "Failed"),
            (50.0, "Block", "C", "Failed", "Repairing"),
            (50.0, "Block", "D", "Standby", "Good"),
            (50.0, "Node", "Cold", "Good", "Degraded"),
            (50.0, "System", "State_change", "Green", "Yellow"),
            (52.0, "Block", "A", "Good", "Failed"),
            (52.0, "Block", "A", "Failed", "Repairing"),
            (52.0, "Block", "B", "Good", "Idle"),
            (52.0, "Node", "Join", "Good", "Degraded"),
            (52.0, "Node", "Join", "Degraded", "Idle"),
            (52.0, "Node", "end", "Good", "Idle"),
            (52.0, "System", "State_change", "Yellow", "Red"),
            (60.0, "Trigger", "T1", "Fired", "Fired"),
            (60.0, "Block", "C", "Repairing", "Repaired"),
            (60.0, "Block", "C", "Repaired", "Good"),
            (60.0, "Block", "C", "Good", "Standby"),
            (60.0, "Node", "Cold", "Degraded", "Good"),
            # P1 ends at 60. `Begin_phase_P2` then the mission close, and the
            # close is a FAILURE because the system is Red at this instant
            # (A Repairing, B Idle) -- `ConductPhaseChange`'s mission-closing
            # arm reads the colour, not the phase table.
            (60.0, "System", "Phase_change", "--", "Phase_change"),
            (60.0, "System", "Phase_change", "Phase_change", "Mission_failure"),
            # The status is re-reported at the boundary and does not move.
            (60.0, "System", "State_change", "Red", "Red"),
            (60.0, "System", "Red_change", "--", "Red"),
            (62.0, "Block", "A", "Repairing", "Repaired"),
            (62.0, "Block", "A", "Repaired", "Good"),
            (62.0, "Block", "B", "Idle", "Good"),
            (62.0, "Node", "Join", "Idle", "Degraded"),
            (62.0, "Node", "end", "Idle", "Good"),
            (62.0, "Node", "Join", "Degraded", "Good"),
            (62.0, "System", "State_change", "Red", "Green"),
            (67.0, "Block", "B", "Good", "Failed"),
            (67.0, "Block", "B", "Failed", "Sparing"),
            (67.0, "Block", "B", "Sparing", "RepHold"),
            (67.0, "Node", "Join", "Good", "Degraded"),
            (67.0, "System", "State_change", "Green", "Yellow"),
            (80.0, "Pool", "SP", "Sparing", "Sparing"),
            (80.0, "Block", "B", "RepHold", "Sparing"),
            (80.0, "Block", "B", "Sparing", "Repairing"),
            (85.0, "Block", "B", "Repairing", "Repaired"),
            (85.0, "Block", "B", "Repaired", "Good"),
            (85.0, "Node", "Join", "Degraded", "Good"),
            (85.0, "System", "State_change", "Yellow", "Green"),
            (90.0, "Trigger", "T1", "Fired", "Fired"),
            (90.0, "Block", "A", "Good", "PM_is_due"),
            (90.0, "Block", "A", "PM_is_due", "pmReqDefer"),
            (100.0, "Block", "A", "pmReqDefer", "Done"),
            (100.0, "Block", "B", "Good", "Done"),
            (100.0, "Block", "C", "Standby", "Done"),
            (100.0, "Block", "D", "Good", "Done"),
            # ...and no `Phase_change` line at all: the mission is already over.
            (100.0, "System", "Simulation_Terminate", "--", "--"),
        ], where="S9")

    elif rung == 10:
        # S10 adds one event, `E1`, in series between the cold node and End,
        # with its outcome probability `failVal` = 1.0 and its distribution
        # `Fixed` at that same 1.0.
        #
        # Against S9's literal the differences are three rows and nothing else.
        # The event succeeds, so its cascade is empty and every downstream
        # status is exactly S9's -- which is what p = 1.0 buys: the event's own
        # state machine, isolated from what an event's failure would do.
        assert_timeline(result, [
            (0.0, "System", "State_change", "--", "Mission"),
            (0.0, "Block", "A", "--", "Good"),
            (0.0, "Block", "B", "--", "Good"),
            (0.0, "Block", "C", "--", "Good"),
            (0.0, "Block", "D", "--", "Good"),
            # `ConductEvent` reads `draw == 1.0` as SUCCESS, so `failVal = 1.0`
            # is an event that always succeeds -- armed at t=0, resolved in the
            # same instant.
            (0.0, "Event", "E1", "--", "Armed"),
            (0.0, "Event", "E1", "Armed", "Success"),
            (0.0, "Block", "D", "Good", "Standby"),
            (0.0, "Node", "Cold", "--", "Good"),
            (30.0, "Trigger", "T1", "--", "Fired"),
            (30.0, "Block", "A", "Good", "PM_is_due"),
            (30.0, "Block", "A", "PM_is_due", "PM"),
            (30.0, "Block", "B", "Good", "Idle"),
            (30.0, "Node", "Join", "--", "Degraded"),
            (30.0, "Node", "Join", "Degraded", "Idle"),
            (30.0, "Node", "end", "--", "Idle"),
            (30.0, "System", "State_change", "Mission", "Red"),
            (32.0, "Block", "A", "PM", "PM_complete"),
            (32.0, "Block", "A", "PM_complete", "Good"),
            (32.0, "Block", "B", "Idle", "Good"),
            (32.0, "Node", "Join", "Idle", "Degraded"),
            (32.0, "Node", "end", "Idle", "Good"),
            (32.0, "Node", "Join", "Degraded", "Good"),
            (32.0, "System", "State_change", "Red", "Green"),
            (40.0, "Pool", "SP", "--", "Sparing"),
            (50.0, "Block", "C", "Good", "Failed"),
            (50.0, "Block", "C", "Failed", "Repairing"),
            (50.0, "Block", "D", "Standby", "Good"),
            (50.0, "Node", "Cold", "Good", "Degraded"),
            (50.0, "System", "State_change", "Green", "Yellow"),
            (52.0, "Block", "A", "Good", "Failed"),
            (52.0, "Block", "A", "Failed", "Repairing"),
            (52.0, "Block", "B", "Good", "Idle"),
            (52.0, "Node", "Join", "Good", "Degraded"),
            (52.0, "Node", "Join", "Degraded", "Idle"),
            (52.0, "Node", "end", "Good", "Idle"),
            (52.0, "System", "State_change", "Yellow", "Red"),
            (60.0, "Trigger", "T1", "Fired", "Fired"),
            (60.0, "Block", "C", "Repairing", "Repaired"),
            (60.0, "Block", "C", "Repaired", "Good"),
            (60.0, "Block", "C", "Good", "Standby"),
            (60.0, "Node", "Cold", "Degraded", "Good"),
            (60.0, "System", "Phase_change", "--", "Phase_change"),
            (60.0, "System", "Phase_change", "Phase_change", "Mission_failure"),
            (60.0, "System", "State_change", "Red", "Red"),
            (60.0, "System", "Red_change", "--", "Red"),
            (62.0, "Block", "A", "Repairing", "Repaired"),
            (62.0, "Block", "A", "Repaired", "Good"),
            (62.0, "Block", "B", "Idle", "Good"),
            (62.0, "Node", "Join", "Idle", "Degraded"),
            (62.0, "Node", "end", "Idle", "Good"),
            (62.0, "Node", "Join", "Degraded", "Good"),
            (62.0, "System", "State_change", "Red", "Green"),
            (67.0, "Block", "B", "Good", "Failed"),
            (67.0, "Block", "B", "Failed", "Sparing"),
            (67.0, "Block", "B", "Sparing", "RepHold"),
            (67.0, "Node", "Join", "Good", "Degraded"),
            (67.0, "System", "State_change", "Green", "Yellow"),
            (80.0, "Pool", "SP", "Sparing", "Sparing"),
            (80.0, "Block", "B", "RepHold", "Sparing"),
            (80.0, "Block", "B", "Sparing", "Repairing"),
            (85.0, "Block", "B", "Repairing", "Repaired"),
            (85.0, "Block", "B", "Repaired", "Good"),
            (85.0, "Node", "Join", "Degraded", "Good"),
            (85.0, "System", "State_change", "Yellow", "Green"),
            (90.0, "Trigger", "T1", "Fired", "Fired"),
            (90.0, "Block", "A", "Good", "PM_is_due"),
            (90.0, "Block", "A", "PM_is_due", "pmReqDefer"),
            (100.0, "Block", "A", "pmReqDefer", "Done"),
            (100.0, "Block", "B", "Good", "Done"),
            (100.0, "Block", "C", "Standby", "Done"),
            (100.0, "Block", "D", "Good", "Done"),
            # `EndSimulation` puts every event back to `Armed` with an `End_sim`
            # detail, in the same sweep that puts every block to `Done`.
            (100.0, "Event", "E1", "Success", "Armed"),
            (100.0, "System", "Simulation_Terminate", "--", "--"),
        ], where="S10")

    else:
        # S11 adds a flow limit on one link: a demand of 200 at the Start node
        # and `nomFlow`/`MaxFlow` = 100 on `E1 -> End`, the only link every
        # delivered unit has to cross. (`End` also needs `fullFlow` off for the
        # cap to bind at all -- see `_s11`'s docstring and
        # `validate._check_capacity_settings`.)
        #
        # **This literal is S10's, row for row, and that is the finding.** The
        # capacity analysis moves no state: `ChangeLinkStatus`'s LinkUp arm
        # only raises `CapAnalysisRequired`, which the
        # next `AnalyzeSystem` spends on the report. So the rung's real claim is
        # a report claim, and it lives in
        # `test_a_flow_limit_on_one_link_caps_the_flow_the_system_can_produce`
        # below. The literal is committed anyway: if a future change made the
        # capacity analysis *move* a state, this rung would have to gain a row,
        # and that should be a deliberate, visible change rather than a silent
        # one.
        assert_timeline(result, [
            (0.0, "System", "State_change", "--", "Mission"),
            (0.0, "Block", "A", "--", "Good"),
            (0.0, "Block", "B", "--", "Good"),
            (0.0, "Block", "C", "--", "Good"),
            (0.0, "Block", "D", "--", "Good"),
            (0.0, "Event", "E1", "--", "Armed"),
            (0.0, "Event", "E1", "Armed", "Success"),
            (0.0, "Block", "D", "Good", "Standby"),
            (0.0, "Node", "Cold", "--", "Good"),
            (30.0, "Trigger", "T1", "--", "Fired"),
            (30.0, "Block", "A", "Good", "PM_is_due"),
            (30.0, "Block", "A", "PM_is_due", "PM"),
            (30.0, "Block", "B", "Good", "Idle"),
            (30.0, "Node", "Join", "--", "Degraded"),
            (30.0, "Node", "Join", "Degraded", "Idle"),
            (30.0, "Node", "end", "--", "Idle"),
            (30.0, "System", "State_change", "Mission", "Red"),
            (32.0, "Block", "A", "PM", "PM_complete"),
            (32.0, "Block", "A", "PM_complete", "Good"),
            (32.0, "Block", "B", "Idle", "Good"),
            (32.0, "Node", "Join", "Idle", "Degraded"),
            (32.0, "Node", "end", "Idle", "Good"),
            (32.0, "Node", "Join", "Degraded", "Good"),
            (32.0, "System", "State_change", "Red", "Green"),
            (40.0, "Pool", "SP", "--", "Sparing"),
            (50.0, "Block", "C", "Good", "Failed"),
            (50.0, "Block", "C", "Failed", "Repairing"),
            (50.0, "Block", "D", "Standby", "Good"),
            (50.0, "Node", "Cold", "Good", "Degraded"),
            (50.0, "System", "State_change", "Green", "Yellow"),
            (52.0, "Block", "A", "Good", "Failed"),
            (52.0, "Block", "A", "Failed", "Repairing"),
            (52.0, "Block", "B", "Good", "Idle"),
            (52.0, "Node", "Join", "Good", "Degraded"),
            (52.0, "Node", "Join", "Degraded", "Idle"),
            (52.0, "Node", "end", "Good", "Idle"),
            (52.0, "System", "State_change", "Yellow", "Red"),
            (60.0, "Trigger", "T1", "Fired", "Fired"),
            (60.0, "Block", "C", "Repairing", "Repaired"),
            (60.0, "Block", "C", "Repaired", "Good"),
            (60.0, "Block", "C", "Good", "Standby"),
            (60.0, "Node", "Cold", "Degraded", "Good"),
            (60.0, "System", "Phase_change", "--", "Phase_change"),
            (60.0, "System", "Phase_change", "Phase_change", "Mission_failure"),
            (60.0, "System", "State_change", "Red", "Red"),
            (60.0, "System", "Red_change", "--", "Red"),
            (62.0, "Block", "A", "Repairing", "Repaired"),
            (62.0, "Block", "A", "Repaired", "Good"),
            (62.0, "Block", "B", "Idle", "Good"),
            (62.0, "Node", "Join", "Idle", "Degraded"),
            (62.0, "Node", "end", "Idle", "Good"),
            (62.0, "Node", "Join", "Degraded", "Good"),
            (62.0, "System", "State_change", "Red", "Green"),
            (67.0, "Block", "B", "Good", "Failed"),
            (67.0, "Block", "B", "Failed", "Sparing"),
            (67.0, "Block", "B", "Sparing", "RepHold"),
            (67.0, "Node", "Join", "Good", "Degraded"),
            (67.0, "System", "State_change", "Green", "Yellow"),
            (80.0, "Pool", "SP", "Sparing", "Sparing"),
            (80.0, "Block", "B", "RepHold", "Sparing"),
            (80.0, "Block", "B", "Sparing", "Repairing"),
            (85.0, "Block", "B", "Repairing", "Repaired"),
            (85.0, "Block", "B", "Repaired", "Good"),
            (85.0, "Node", "Join", "Degraded", "Good"),
            (85.0, "System", "State_change", "Yellow", "Green"),
            (90.0, "Trigger", "T1", "Fired", "Fired"),
            (90.0, "Block", "A", "Good", "PM_is_due"),
            (90.0, "Block", "A", "PM_is_due", "pmReqDefer"),
            (100.0, "Block", "A", "pmReqDefer", "Done"),
            (100.0, "Block", "B", "Good", "Done"),
            (100.0, "Block", "C", "Standby", "Done"),
            (100.0, "Block", "D", "Good", "Done"),
            (100.0, "Event", "E1", "Success", "Armed"),
            (100.0, "System", "Simulation_Terminate", "--", "--"),
        ], where="S11")


@pytest.mark.parametrize("rung", (1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 11),
                         ids=("S1", "S2", "S3", "S4", "S5", "S6", "S7",
                              "S8", "S9", "S10", "S11"))
def test_fixed_lives_consume_no_randomness(rung):
    """A model whose lives are all `Fixed` draws nothing at all.

    Why: rule B2 exists so a behaviour test is exact and needs no seed. It is
    only worth anything if it is checked rather than assumed, and the check is
    free: `DrawNumber`'s `WHEN 19` returns `Param(1)` without touching a
    stream, so a run's total draw count must be exactly zero. A rung that
    grew a stochastic distribution without saying so fails here, in one
    assertion, instead of as an unexplained number change somewhere above.

    It is also a check on the mechanisms themselves, not only on the
    distributions: a *mixed* dependence would draw (`DepNothing`/`DepIdle`/
    `DepPM` below 100 each fall through to `UniformReal(0,1)*100`), and a
    stochastic PM duration would too. S6 keeps `DepIdle=100%` and every rung
    keeps a `Fixed` PM duration precisely so this assertion stays at zero.
    Two of the newer rungs had to be built with that in mind rather than
    merely checked for it: S8's links carry `AProb = 1.0`, which
    short-circuits `SetSwitchTime`'s `UniformReal(0,1)`, and S10's event
    takes its outcome from a `Fixed(1.0)` rather than Raptor's own
    `Bernoulli`, which draws a uniform even at `p = 1.0`. Both are the rung's
    own mechanism configured deterministically, not a mechanism hidden.

    Setup: `spine(rung)`, one run, every distribution `Fixed`.

    Expected: `Run.draws == 0`.

    Source: raptor/core/rng.py:451-452 (`_dispatch_draw`'s `Distribution.FIXED`
    arm, `return p[0]`); raptor/core/rng.py:302-306 (`bernoulli`'s
    unconditional uniform, the draw S10's `Fixed` avoids);
    raptor/core/rng.py:63-76 (`CountingStream`, the measurement the harness
    reads); Raptor 7 (`BackFlowDependencies`' mixed-percentage
    action draw, skipped at exactly 100%); Raptor 7
    (`Link.SetSwitchTime`, S8's `AProb = 1.0` short-circuit);
    docs/engine-design/kickoff-and-runs.adoc (Structure)
    """
    result = run(spine(rung))
    assert result.draws == 0, (
        f"S{rung} must consume zero randomness -- a rung grew a stochastic "
        f"distribution without saying so (rule B2)")


def test_one_failure_degrades_the_join_node_but_does_not_fail_the_system():
    """With k=1 of n=2, a single block's failure leaves the `Join` node
    `Degraded` and the system `Yellow`, and only the second failure takes the
    End node down.

    Why: this is the mechanism rung S2 adds over S1. `ReconfigureNode`
    re-aggregates a node's incoming links into a status when one of them
    changed, and `k` is how many of `n` must be good -- so the first failure
    is a degradation, and the system colour follows the node's rather than the
    block's. A node that has never logged a state has nothing to degrade
    *from*, which is why the first `Join` row is `-- -> Degraded` and not
    `Good -> Degraded`.

    Setup: `spine(2)`, one run of 100 sim hours, A fails at 50 and B at 55,
    both `Fixed`, no resources.

    Expected: the `Join` node is `-- -> Degraded` at 50, `Bad` at 55,
    `Degraded` then `Good` at 60; the system is `Yellow` at 50, `Red` at 55 and
    `Green` at 60.

    Source: Raptor 7 (`ChangeNodeState`), Raptor 7
    (`AnalyzeSystem`), Raptor 7 (the delta computation
    `ChangeNodeState` returns downstream);
    docs/engine-design/node-status-and-cold-standby.adoc (Purpose,
    Structure)
    """
    result = run(spine(2))

    assert [str(e) for e in events(result, "Node:Join")] == [
        "50 Node:Join -- -> Degraded",
        "55 Node:Join Degraded -> Bad",
        "60 Node:Join Bad -> Degraded",
        "60 Node:Join Degraded -> Good",
    ]
    assert state_at(result, 50.0)["System:State_change"] == "Yellow"
    assert state_at(result, 55.0)["System:State_change"] == "Red"
    assert state_at(result, 60.0)["System:State_change"] == "Green"


def test_the_second_repair_waits_for_the_only_repair_resource():
    """A block that fails while the single repair resource unit is borrowed
    parks in `RepHold`, and is granted the unit the instant the borrower hands
    it back.

    Why: the mechanism rung S3 adds over S2. Source's request is a two-part
    condition -- a raw stock check *or* an outstanding report number -- so a
    block that cannot be served logs `Waiting_for_resource(s)` and parks, and
    `TakeBack` grants the head of the queue. The `Sparing` /
    `Obtained_1_Resource(s)` line is written at the grant, which lands on the
    same instant as the borrower's repair completing; that coincidence is the
    part worth pinning, and `state_at` is used to show B really is parked for
    the whole five hours in between rather than merely late.

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


def test_a_second_resource_unit_removes_the_contention():
    """With two units in the repair pool, both repairs start at the instant
    their block fails, and the `RepHold` row disappears.

    Why: this is the check that rung S3's fixture is pinned by *the resource
    and not by the schedule*. Same blocks, same `Fixed` lives, one number
    changed -- so the only thing that can move B from `RepHold` at t=55 to
    `Repairing` at t=55 is the unit count. It is also the shape a later rung's
    test wants (`spine(3, num_resources=n)` rather than a copied spec).

    Setup: `spine(3, num_resources=2)`, one run of 100 sim hours.

    Expected: B is `Failed -> Repairing` at t=55 with no `RepHold` between, and
    both blocks are `Good` at t=60.

    Source: Raptor 7 (`Repair`'s two-part resource condition),
    Raptor 7 (the zero-length re-insertion after the grant);
    docs/engine-design/pools-spares-resources.adoc (Structure, and
    Port-only devices -- the check is `numDiffRes=1` exactly, so a block asking
    for any other number of units borrows nothing and never waits)
    """
    result = run(spine(3, num_resources=2))

    assert [str(e) for e in events(result, "Block:B",
                                    between=(50.0, 70.0))] == [
        "55 Block:B Good -> Failed",
        "55 Block:B Failed -> Repairing",
        "60 Block:B Repairing -> Repaired",
        "60 Block:B Repaired -> Good",
    ]
    assert state_at(result, 60.0)["Block:A"] == "Good"
    assert state_at(result, 60.0)["Block:B"] == "Good"


def test_a_pm_is_not_operating_time_so_a_fixed_life_lands_late():
    """A block's scheduled PM does not count toward its failure life, so a
    `Fixed` 50-hour life that has already been through a 2-hour PM ends at
    t=52 rather than t=50.

    Why: this is the mechanism rung S4 adds over S3, and the number it moves
    is the one that cannot be read off the spec. Source accumulates
    `OperatingTime` only in `Run`'s running branch; the
    PM's own `WAIT DURATION` belongs to `PerformPM`, and the PM clock is zeroed
    by the PM completing, so a PM costs wall-clock
    time and buys nothing in life. The `Failed` line says so out loud --
    `Operating_time=50.000000` at `SimTime=52` -- and pinning *that* is the
    real claim: a test that only checked "A fails around 50" would pass
    whether or not the PM clock were counted.

    Setup: `spine(4)`, one run of 100 sim hours. A has `uses_pm=True,
    PMFreq=25`, a `Fixed` 2-hour PM, and `PMRefresh`/`PMFailReset` both off;
    its `Fixed` life is 50 and its `Fixed` repair is 10.

    Expected: A is `PM_is_due`/`PM` at t=25 and `PM_complete`/`Good` at 27;
    A fails at t=52, not t=50, with `Operating_time=50.000000` on the
    `Failed` line; and the run's second PM is at t=87, i.e. 25 hours after A
    is Good again at 62.

    Source: Raptor 7 (`Run`'s `PMTimeLeft`-vs-`FailTimeLeft`
    race, and the refill that precedes it), Raptor 7
    (`PerformPM` zeroing `PMTimeLeft` on completion), Raptor 7
    (`OperatingTime` accumulated in the running branch only);
    docs/engine-design/block-lifecycle.adoc (Source anchors: `Run`,
    `PerformPM`)
    """
    result = run(spine(4))

    assert [str(e) for e in events(result, "Block:A")] == [
        "0 Block:A -- -> Good",
        "25 Block:A Good -> PM_is_due",
        "25 Block:A PM_is_due -> PM",
        "27 Block:A PM -> PM_complete",
        "27 Block:A PM_complete -> Good",
        "52 Block:A Good -> Failed",
        "52 Block:A Failed -> Repairing",
        "62 Block:A Repairing -> Repaired",
        "62 Block:A Repaired -> Good",
        "87 Block:A Good -> PM_is_due",
        "87 Block:A PM_is_due -> PM",
        "89 Block:A PM -> PM_complete",
        "89 Block:A PM_complete -> Good",
        "100 Block:A Good -> Done",
    ]
    # `result.log` is a flat tab-separated table, so the block's name is a
    # column and not a substring of the line -- select A's own `Failed` row by
    # column, not by search, or B's failure comes with it.
    failed = [line for line in result.log
              if "Failed" in line and line.split("\t")[2].strip() == "A"]
    assert len(failed) == 1, failed
    assert "Operating_time=50.000000" in failed[0], (
        "A's Fixed life is 50 hours, so the Failed line must report 50 hours of "
        f"operating time -- the 2 PM hours are not operating time: {failed[0]}")
    assert float(failed[0].split()[0]) == 52.0


def test_a_triggered_pm_is_not_perturbed_by_the_frequency_and_vice_versa():
    """PM is due when a trigger fires and not at a `PMFreq` countdown, and a
    frequency-driven block's PM is not moved by a trigger that fires.

    Why: this is the mechanism rung S5 adds over S4, and it is the only place
    in the ladder where a rung *replaces* a clock rather than adding a second
    one. Source gates both the `PMTimeLeft` refill and the race comparison on
    `usesPM AND (NOT pmTriggered)`, so for a
    triggered block PM is due only via `ControlPMtriggers`' external
    `"Triggered_PM_Due"` wake, and that wake only
    reaches a block that is Running, Idle or Standby. So the two arms are
    genuinely exclusive and neither one can quietly take the other's timing --
    which is what makes "the rung below's numbers did not change" a claim
    rather than a coincidence.

    Setup: `spine(5)` for the trigger-driven arm, and `spine(4)` with a
    trigger attached to A's frequency-driven PM for the other arm. The trigger
    is `Fixed` 30 hours, `repeats=1`, so it fires at 30, 60 and 90.

    Expected: on `spine(5)` A is in `PM` at t=30, 90 (exactly the trigger's
    firings) and never at t=25 or t=55 -- S4's PM frequency; and the t=60
    firing logs the trigger but changes no block, because A is due to fail
    within the hour rather than to PM. On the frequency-driven arm the t=30
    firing changes nothing and the PMs stay at 25 and 87.

    Source: Raptor 7 (the refill and the race both gated on
    `NOT pmTriggered`), Raptor 7 (`ControlPMtriggers`: the
    eligibility test, and the `FailWait`/`ZeroWait` interrupt),
    Raptor 7 (`PullRapTrigger`: the `Fixed` wait, the `Fired`
    line, and the repeat);
    docs/engine-design/block-lifecycle.adoc (Source anchors: `Run`,
    `PerformPM`, `ObtainPMprereqs`)
    """
    triggered = run(spine(5))
    assert [str(e) for e in events(triggered, "Block:A",
                                    between=(0.0, 99.0))] == [
        "0 Block:A -- -> Good",
        "30 Block:A Good -> PM_is_due",
        "30 Block:A PM_is_due -> PM",
        "32 Block:A PM -> PM_complete",
        "32 Block:A PM_complete -> Good",
        "52 Block:A Good -> Failed",
        "52 Block:A Failed -> Repairing",
        "62 Block:A Repairing -> Repaired",
        "62 Block:A Repaired -> Good",
        "90 Block:A Good -> PM_is_due",
        "90 Block:A PM_is_due -> PM",
        "92 Block:A PM -> PM_complete",
        "92 Block:A PM_complete -> Good",
    ]
    assert [str(e) for e in events(triggered, "Trigger:T1")] == [
        "30 Trigger:T1 -- -> Fired",
        "60 Trigger:T1 Fired -> Fired",
        "90 Trigger:T1 Fired -> Fired",
    ]

    # The other arm: the same trigger attached to a *frequency* PM. It fires
    # at 30 and 60 and moves nothing; the PMs are still S4's.
    frequency = spine(4)
    frequency["triggers"] = [{"name": "T1", "init_used": 0.0, "dist": FIXED,
                              "params": [30.0, 0.0, 0.0], "repeats": 1}]
    other = run(frequency)
    assert [str(e) for e in events(other, "Trigger:T1")] == [
        "30 Trigger:T1 -- -> Fired",
        "60 Trigger:T1 Fired -> Fired",
        "90 Trigger:T1 Fired -> Fired",
    ]
    assert [str(e) for e in events(other, "Block:A",
                                    between=(0.0, 99.0))] == [
        "0 Block:A -- -> Good",
        "25 Block:A Good -> PM_is_due",
        "25 Block:A PM_is_due -> PM",
        "27 Block:A PM -> PM_complete",
        "27 Block:A PM_complete -> Good",
        "52 Block:A Good -> Failed",
        "52 Block:A Failed -> Repairing",
        "62 Block:A Repairing -> Repaired",
        "62 Block:A Repaired -> Good",
        "87 Block:A Good -> PM_is_due",
        "87 Block:A PM_is_due -> PM",
        "89 Block:A PM -> PM_complete",
        "89 Block:A PM_complete -> Good",
    ]


def test_a_dependent_goes_idle_when_what_it_follows_goes_down_and_back():
    """A block with `DepIdle=100%` on block A is idled whenever A goes Down --
    including when A enters PM -- and resumes on the matching `AllUp`.

    Why: this is the mechanism rung S6 adds over S5, and the part worth
    pinning is that `Down` covers PM. `BackFlowDependencies` is dispatched from
    `ReconfigureBlock` on a block-state change, and PM is a state change to a
    Down state, so a two-hour PM idles a dependent for two hours and turns the
    system `Red` for that instant even though the k-of-n node could still carry
    the mission. That is a surprising enough consequence to be worth its own
    literal rather than being inferred from the rung's.

    Setup: `spine(6)`, one run of 100 sim hours. B depends on A
    (`DepType=RBDBlock`, `DepNum`=A, `DepIdle=100%`, the other three
    reactions 0%).

    Expected: B is `Good -> Idle` at t=30 (A's PM starts) and again at t=52
    (A fails), and `Idle -> Good` at t=32 and t=62 as each comes back up; B's
    own failure is at t=67, twelve hours later than S5's, because an Idle
    block's failure clock is paused.

    Source: Raptor 7 (`BackFlowDependencies`, its `Down`/`AllUp`
    dispatch), Raptor 7 (`ReconfigureBlock`'s call into it),
    Raptor 7 (the action draw, short-circuited to `GoIdle` at
    `DepIdlePerc=100.0`), Raptor 7 (`Run`'s Idle wait, whose
    wake pauses the failure clock);
    docs/engine-design/dependency-backflow.adoc (Purpose, Structure);
    block-lifecycle.adoc (States)
    """
    result = run(spine(6))

    assert [str(e) for e in events(result, "Block:B")] == [
        "0 Block:B -- -> Good",
        "30 Block:B Good -> Idle",
        "32 Block:B Idle -> Good",
        "52 Block:B Good -> Idle",
        "62 Block:B Idle -> Good",
        "67 Block:B Good -> Failed",
        "67 Block:B Failed -> Repairing",
        "72 Block:B Repairing -> Repaired",
        "72 Block:B Repaired -> Good",
        # The last T1 firing idles B once more, for A's second PM.
        "90 Block:B Good -> Idle",
        "92 Block:B Idle -> Good",
        "100 Block:B Good -> Done",
    ]
    # The idle period really is the two hours, not a single instant: a state
    # read at the midpoint is the only thing that can tell the difference.
    assert state_at(result, 30.0)["Block:B"] == "Idle"
    assert state_at(result, 31.0)["Block:B"] == "Idle"
    assert state_at(result, 32.0)["Block:B"] == "Good"
    # And the system is Red for the PM, because the dependent took the whole
    # End node Idle with it.
    assert state_at(result, 31.0)["System:State_change"] == "Red"


def test_an_empty_spare_pool_parks_the_repair_until_the_restock_lands():
    """A block that fails with the pool empty parks in `RepHold`
    `Waiting_for_Spare` and is granted the spare in the same instant the
    routine restock is logged.

    Why: this is the mechanism rung S7 adds over S6, and the claim is the
    *ordering* rather than the timing. `GenerateSpares` is a background
    `TELL METHOD` that adds the new spares and then calls `Give`, and `Give`
    grants the FIFO head and interrupts it with `Pool_Available`; the event log
    records the arrival before the grant because the arrival line is written
    first. So at t=80 the
    pool's row precedes B's `RepHold -> Sparing`, and both are at 80.0.

    Setup: `spine(7)`, one run of 100 sim hours. The pool `SP` has stock 1,
    one new spare every 40 hours (RSO on), and both blocks draw from it; A's
    PM at 30 and A's repair at 52 take the only two spares the pool ever has
    before t=67.

    Expected: `Pool:SP` is `-- -> Sparing` at t=40 and `Sparing -> Sparing`
    at t=80; B is `Failed -> Sparing` (`No_spare_in_stock-SP`) then
    `Sparing -> RepHold` (`Waiting_for_Spare`) at t=67, and `RepHold ->
    Sparing` (`Spare_Arrived`) then `Sparing -> Repairing` at t=80, thirteen
    hours after it failed.

    Source: Raptor 7 (`Repair`'s `PooledSpares` request, the
    `Resources=0` re-read and the two log lines), Raptor 7
    (`CheckForSpare`'s SparePool arm), Raptor 7
    (`GenerateSpares`: the arrival line written before the grants);
    docs/engine-design/pools-spares-resources.adoc (Purpose, Source
    anchors, Structure)
    """
    result = run(spine(7))

    assert [str(e) for e in events(result, "Pool:SP")] == [
        "40 Pool:SP -- -> Sparing",
        "80 Pool:SP Sparing -> Sparing",
    ]
    assert [str(e) for e in events(result, "Block:B",
                                    between=(65.0, 89.0))] == [
        "67 Block:B Good -> Failed",
        "67 Block:B Failed -> Sparing",
        "67 Block:B Sparing -> RepHold",
        "80 Block:B RepHold -> Sparing",
        "80 Block:B Sparing -> Repairing",
        "85 Block:B Repairing -> Repaired",
        "85 Block:B Repaired -> Good",
    ]
    assert state_at(result, 67.0)["Block:B"] == "RepHold"
    assert state_at(result, 79.0)["Block:B"] == "RepHold"
    assert state_at(result, 80.0)["Block:B"] == "Repairing"

    # The two `Pool` lines carry the stock, and that is where "run dry" is
    # visible: t=80's arrival reports stock=0 *after* granting to B.
    pool_lines = [line for line in result.log if "Pool" in line and "SP" in line]
    assert len(pool_lines) == 2, pool_lines
    assert "arrived_(stock=1)" in pool_lines[0], pool_lines[0]
    assert "arrived_(stock=0)" in pool_lines[1], pool_lines[1]


def test_the_spare_pool_reports_the_stock_it_ever_held():
    """The pool's End-of-Run row reports stock 1 as both its minimum and its
    maximum, because stock 1 is all it ever held after a grant.

    Why: `MaxStock` is a *monitor* on the pool's stock, and MODSIM's timed
    monitor records a level only once it has been held for a positive
    duration. This pool is drained to 0 by A's PM and refilled by a restock,
    so the level 1 is replaced within the instant it is set and never
    accumulates any time at all -- and the port keeps that rule, with one
    deliberate exception: the level current at the run end always counts
    (`pool.ResourceQueue.stock_extremes`, and the `LadL2Pool` capture that
    settled the exception). Here the run ends with the pool empty, so the
    run-end level is 0 and the reported maximum is the 1 from the grant.

    Setup: `spine(7)`, one run of 100 sim hours, pool `SP` with stock 1 and
    one new spare every 40 hours.

    Expected: the `SparePool_Sparing_Information` row reads
    `InitNumSpares=1, NewSpares=2, MinStock=0, MaxStock=1, EndStock=0,
    NumDelays=2` -- one new spare at the start, two arrivals (t=40, t=80), a
    minimum of 0 (the pool was drained), a maximum of 1, nothing left at the
    run end, and two delays: B's repair spare at t=67 and A's *PM* spare at
    t=90, which is why the rung's last PM never leaves `pmReqDefer`.

    Source: Raptor 7 (`GenerateSpares`, the arrival counter),
    Raptor 7 (`CheckForSpare`, the stock check and the delay count),
    Raptor 7 (`ObtainPMresource`'s own request, which is the
    second delay and is counted once, not twice),
    raptor/core/engine/pool.py (`ResourceQueue.begin_run`,
    `ResourceQueue.stock_extremes`);
    docs/engine-design/pools-spares-resources.adoc (Purpose, Source
    anchors)
    """
    result = run(spine(7))

    header = next(i for i, line in enumerate(result.eor)
                  if line.strip().startswith("poolName"))
    row = next(line for line in result.eor[header + 1:]
               if line.strip().startswith("SP"))
    fields = row.split()
    assert fields[1:8] == ["1", "2", "0", "0.42", "1", "0", "2"], row


def test_a_cold_group_parks_the_lower_priority_path_and_switches_it_in():
    """A cold group of 2 parks its lower-priority path in `Standby` at
    kickoff, promotes it the instant the running path fails, and demotes the
    repaired path again.

    Why: this is the mechanism rung S8 adds over S7, and all three of
    `RunColdLogic`'s arms land in one 100-hour run. A cold node does not
    aggregate inline: it queues itself in `ColdChangeGroup` and `CheckColds`
    drains the group into `RunColdLogic`, which sorts the
    candidate links by `ColdPri` (`IdAndSortColds`),
    keeps the first `RunsRequired` and switches the rest to `Standby` with the
    detail word `Too_many_on`. Because every link starts `"ready"`
    (`Link.SetToInitValues`) and every block starts
    `Running`, that demotion happens *at kickoff* -- and Raptor 7's
    `IF SimTime>0.0` guard means it wakes nobody, which is why D's suspended
    `Run` is never interrupted and D's life simply runs at full stress while
    parked. The other two arms are the mid-run switch-on (`Prior=Standby`, whose interrupt is *unconditional*, even at
    `SimTime==0.0`) and the mid-run switch-off back into `Standby`.

    Setup: `spine(8)`, one run of 100 sim hours. Blocks `C` and `D` feed a
    `Cold` node (`Cold=1`, `k=1`, `k*=1`) with `ColdPri` 10 (C) and 20 (D),
    `AProb=1.0`, `AutoTime=0.0`, `ManTime=-1.0` on the two links into it. C
    fails at 50 and is repaired at 60; D's life (200) is past the run end.

    Expected: D is `Good -> Standby` at t=0 with detail `Too_many_on` and stays
    there; at t=50 C fails and D is `Standby -> Good` with detail
    `Prior=Standby`; at t=60 C is repaired and goes `Repaired -> Good ->
    Standby`, `Too_many_on` again. The node's own line reads `1/2_up7_s8`, then
    `1/2_up8_df7`, then `1/2_up8_s7` -- always one up path of two, so the
    group never once goes Bad.

    Source: Raptor 7 (`RunColdLogic`), Raptor 7
    (`IdAndSortColds`), Raptor 7 (the switch-OFF `SimTime>0.0`
    interrupt guard), Raptor 7 (the switch-ON interrupt, which
    has no such guard), Raptor 7 (`Link.SetToInitValues`),
    Raptor 7 (`Link.SetSwitchTime`, whose `AProb=1.0` arm skips the
    draw and whose `AutoTime=0.0` resolves the link straight to `"ready"`);
    raptor/core/engine/cold_standby.py (`run_cold_logic`, `sort_cold_links`,
    `set_switch_time`), raptor/core/engine/cascade_cold.py
    (`_Cascade._flush_cold_changes`, i.e. `CheckColds`);
    docs/engine-design/node-status-and-cold-standby.adoc (Purpose, Source
    anchors, Structure, State it owns)
    """
    result = run(spine(8))

    assert [str(e) for e in events(result, "Block:D")] == [
        "0 Block:D -- -> Good",
        # Kickoff demotion.
        "0 Block:D Good -> Standby",
        # Switch-on, in the instant C's link goes down.
        "50 Block:D Standby -> Good",
        "100 Block:D Good -> Done",
    ]
    assert [str(e) for e in events(result, "Block:C")] == [
        "0 Block:C -- -> Good",
        "50 Block:C Good -> Failed",
        "50 Block:C Failed -> Repairing",
        "60 Block:C Repairing -> Repaired",
        # The repair completes into `Good` and is demoted in the same instant,
        # so there is no `Good` interval at t=60 to read back.
        "60 Block:C Repaired -> Good",
        "60 Block:C Good -> Standby",
        "100 Block:C Standby -> Done",
    ]
    assert details(result, "Block", "D") == [
        "InitializeStress=1.000000",
        "Too_many_on",
        "Prior=StandbyStress=1.000000",
        "End_sim",
    ]
    assert details(result, "Block", "C") == [
        "InitializeStress=1.000000",
        "Operating_time=50.000000",
        "No_resources_used",
        "MaintTime=10.000000",
        "Stress=1.000000",
        "Too_many_on",
        "End_sim",
    ]

    # The node's own line names which link is up and which is not, and the
    # group is `Degraded` rather than `Bad` for the whole run.
    assert [str(e) for e in events(result, "Node:Cold")] == [
        "0 Node:Cold -- -> Good",
        "50 Node:Cold Good -> Degraded",
        "60 Node:Cold Degraded -> Good",
    ]
    assert details(result, "Node", "Cold") == [
        "1/2_up7_s8", "1/2_up8_df7", "1/2_up8_s7"]

    # D is genuinely parked for fifty hours rather than flickering through
    # Standby: only a read-back strictly between the transitions can tell. And
    # after the switch-on D stays `Good` -- the demotion at t=60 is C's, not
    # D's, because C is the path that came back.
    assert state_at(result, 0.0)["Block:D"] == "Standby"
    assert state_at(result, 25.0)["Block:D"] == "Standby"
    assert state_at(result, 49.0)["Block:D"] == "Standby"
    assert state_at(result, 50.0)["Block:D"] == "Good"
    assert state_at(result, 59.0)["Block:D"] == "Good"
    assert state_at(result, 95.0)["Block:D"] == "Good"
    # C's own track through the group: Running, Repairing, then parked.
    assert state_at(result, 25.0)["Block:C"] == "Good"
    assert state_at(result, 55.0)["Block:C"] == "Repairing"
    assert state_at(result, 61.0)["Block:C"] == "Standby"
    assert state_at(result, 95.0)["Block:C"] == "Standby"


def test_a_non_mission_phase_closes_the_mission_at_the_phase_boundary():
    """A phased model opens a mission at t=0, and the boundary into a
    non-mission phase closes it there -- recording the close as a *failure*,
    because the system is Red at that instant.

    Why: this is the mechanism rung S9 adds over S8. The phase table is what
    makes a mission exist at all: kickoff writes `State_change ->
    Began_a_mission` when phase 1 has `Mission=1`, and
    the phase clock's mission-closing arm writes `Finished_a_mission` with
    `Mission_success` or `Mission_failure` chosen by the **system colour at
    that instant**, not by the phase table (`ConductPhaseChange`). This fixture lands the boundary at t=60 with A
    `Repairing` and B `Idle`, so the system is Red and the mission that just
    ended is a failed one. That is the part worth pinning: "non-mission phase"
    does not mean "the mission succeeded".

    Setup: `spine(9)`, one run of 100 sim hours, phases `P1` (mission, `Fixed`
    60 h) and `P2` (non-mission, `Fixed` 40 h). No element is phased, so
    `ChangePhases` has nothing to Cut, Link or re-stress and every block and
    node row is S8's.

    Expected: `System:State_change` is `-- -> Mission` at t=0 with detail
    `Began_a_mission`; `System:Phase_change` is `-- -> Phase_change`
    (`Begin_phase_P2`) then `Phase_change -> Mission_failure`
    (`Finished_a_mission`) at t=60; and at t=100 there is **no** `Phase_change`
    row at all, because the mission closed at the boundary -- where S8,
    unphased, had exactly one such row, at t=100.

    Source: Raptor 7 (the kickoff `Began_a_mission`, gated on the
    first phase being a mission phase), Raptor 7
    (`ControlPhaseChanges`: the clock and the `Begin_phase` log),
    Raptor 7 (`ConductPhaseChange`), Raptor 7
    (entering a non-mission phase);
    raptor/core/engine/system_status.py (`ChangeStatus`, the mission
    transition lines), raptor/core/engine/cascade_phase.py
    (`_Cascade.conduct_phase_change`);
    docs/engine-design/events-and-phases.adoc (Purpose, Source anchors,
    Structure, Ordering at a boundary)
    """
    result = run(spine(9))

    assert [str(e) for e in events(result, "System:Phase_change")] == [
        "60 System:Phase_change -- -> Phase_change",
        "60 System:Phase_change Phase_change -> Mission_failure",
    ]
    assert details(result, "System", "Phase_change") == [
        "Begin_phase_P2", "Finished_a_mission"]
    # The mission opened at t=0, and it is what the system was *from* when it
    # went Red: this fixture's first Red reads `Mission -> Red`, where S8's
    # unphased one reads `-- -> Red`.
    assert str(events(result, "System:State_change")[0]) == (
        "0 System:State_change -- -> Mission")
    assert details(result, "System", "State_change")[0] == "Began_a_mission"
    assert str(events(result, "System:State_change")[1]) == (
        "30 System:State_change Mission -> Red")
    # And the Red is current, not a leftover: read back at the boundary.
    assert state_at(result, 60.0)["System:State_change"] == "Red"


def test_an_event_at_p_one_succeeds_and_is_rearmed_when_the_run_ends():
    """An event whose probability is `1.0` goes `Armed -> Success` in the
    instant it is armed, and `End_sim` puts it back to `Armed` at the run's
    end.

    Why: this is the mechanism rung S10 adds over S9. `ConductEvent`
   draws the event's `failVal` and reads `draw == 1.0`
    as SUCCESS, so the probability column at `1.0` is an event that always
    succeeds -- and its success cascade is empty, which is why every downstream
    status is exactly S9's. The re-arm at the end is *not* the event's own
    doing: `EndSimulation` sweeps every element once, putting blocks to `Done`
    and events to `Armed` with an `End_sim` detail,
    so besides its own firing an event has exactly one other place it can
    change state.

    Setup: `spine(10)`, one run of 100 sim hours, event `E1` in series between
    the cold node and the End node with `failVal = 1.0`. The outcome
    distribution is `Fixed` at that same `1.0` rather than Raptor's default
    `Bernoulli`, because `bernoulli` draws a uniform unconditionally
    (`raptor/core/rng.py:302-306`) and rule B2 wants the run to consume no
    randomness at all. Measured: `Bernoulli(1.0)` gives an identical event
    timeline and costs **1** draw; `Fixed(1.0)` gives the same timeline and
    costs **0**.

    Expected: `E1` is `-- -> Armed` (`Initialize`) then `Armed -> Success`
    (`Fired`) at t=0, and `Success -> Armed` (`End_sim`) at t=100; the `End`
    node's first logged state is `-- -> Idle` at t=30, exactly S9's, which is
    what "this event succeeded" means for everything downstream.

    Source: Raptor 7 (`ConductEvent`, the `draw == 1.0` success
    test), Raptor 7 (`ChangeEventState`), Raptor 7
    (`EndSimulation`: blocks to `Done`, events to `Armed`);
    raptor/core/engine/events.py (`conduct_event`, `change_event_state`),
    raptor/core/engine/cascade_status.py (`EndSimulation`'s event sweep),
    raptor/core/rng.py:302-306 (`bernoulli`'s unconditional draw),
    raptor/core/rng.py:451-452 (`Fixed`'s `return p[0]`);
    docs/engine-design/events-and-phases.adoc (Purpose, Structure)
    """
    result = run(spine(10))

    assert [str(e) for e in events(result, "Event:E1")] == [
        "0 Event:E1 -- -> Armed",
        "0 Event:E1 Armed -> Success",
        "100 Event:E1 Success -> Armed",
    ]
    assert details(result, "Event", "E1") == ["Initialize", "Fired", "End_sim"]
    # The success cascade is empty, so the End node behaves exactly as S9's.
    assert str(events(result, "Node:End")[0]) == "30 Node:end -- -> Idle"
    assert result.draws == 0, (
        f"the event's Fixed outcome must stay draw-free: {result.draws}")


def test_a_flow_limit_on_one_link_caps_the_flow_the_system_can_produce():
    """A flow limit on the one link every unit of demand must cross caps the
    flow the End-of-Run capacity report calls producible, by 100 units on
    every hour it applied.

    Why: this is the mechanism rung S11 adds over S10, and it is a *reporting*
    mechanism -- `ChangeLinkStatus`'s `LinkUp`-crossing arm only raises
    `CapAnalysisRequired`, which the next
    `AnalyzeSystem` spends on the report and on nothing else. That is why S11's
    committed timeline is S10's, row for row. The cap binds only at a
    **capacity-boundary node**: `build_capacity_graph` derives a link's
    `simNomFlow`/`simMaxFlow` on the *incoming* links of a node whose
    `fullFlow` is off, and substitutes the flow infinity otherwise
    (`raptor/core/engine/capacity.py:245-273`). `make_node_row`'s default is
    `fullflow=1`, so this fixture also sets `fullFlow=0` on the End node --
    which is part of the capacity mechanism rather than a second one, and
    `raptor/api/validate.py::_check_capacity_settings` exists precisely to warn
    that the cap is otherwise silently ignored.

    Setup: `spine(11)`, one run of 100 sim hours, `flow_generated = 200` at the
    Start node, `nomFlow`/`MaxFlow` = 100 on the one link every delivered unit
    must cross (`E1 -> End`), `fullFlow = 0` on the End node.

    Expected: `FlowGenerated 200`, and on the `SystemCapacity` row `MaxFlow`
    and `MaxCapacity` both 100 -- the cap, never the demand -- with
    `CapableRate 0.88` because the model has a good path for 88 of its 100
    hours (it is Red from 30 to 32 and from 52 to 62), `AveFlow`/`AveCapacity`
    88.00, and `UnitsProduced 8800.00` = 88 capable hours x the 100-unit cap.

    Source: Raptor 7 (`ChangeLinkStatus`, the LinkUp-crossing
    arm that arms the analysis), Raptor 7
    (`DetermineMaxCapacity`), Raptor 7 (`DistributeFlow`, the
    nominal-then-maximum priority router), Raptor 7
    (`AnalyzeSystem`'s capacity half), Raptor 7 (the per-run
    capacity extraction); raptor/core/engine/capacity.py:245-273
    (`build_capacity_graph`'s `simNomFlow`/`simMaxFlow` derivation and its
    `fullFlow` gate); raptor/api/validate.py (`_check_capacity_settings`);
    docs/engine-design/node-status-and-cold-standby.adoc (Purpose, for the
    k-of-n the flow has to route through)
    """
    result = run(spine(11))

    start = next(i for i, line in enumerate(result.eor)
                 if "***CAPACITY***" in line)
    tail = result.eor[start:]
    generated = next(line for line in tail if line.startswith("FlowGenerated"))
    produced = next(line for line in tail if line.startswith("UnitsProduced"))
    system = next(line for line in tail if line.startswith("SystemCapacity"))
    end_node = next(line for line in tail if line.split()[:1] == ["end"])

    assert generated.split() == ["FlowGenerated", "200"], generated
    assert produced.split() == ["UnitsProduced", "8800.00"], produced
    # MinFlow AveFlow MaxFlow UsageRate MinCapacity AveCapacity MaxCapacity
    # CapableRate: the cap is what MaxFlow and MaxCapacity report, and the
    # demand 200 appears nowhere in them.
    assert system.split()[1:] == ["0", "88.00", "100", "1.00",
                                  "0", "88.00", "100", "0.88"], system
    assert end_node.split()[1:] == ["0", "88.00", "100", "1.00",
                                    "0", "88.00", "100", "0.88"], end_node
