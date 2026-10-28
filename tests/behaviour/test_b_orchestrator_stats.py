"""The orchestrator's per-node statistics resets and cold-group registration.

`reset_all_statistics` (`orchestrator.py`, the port of
`IRunsim.ResetAllStatistics`) is the mid-run
statistics reset: a run whose `StartStats` is non-zero fires it once, at that
sim time, and every monitor the report reads is cleared and re-anchored there.
Five of its sites are not reachable from the spine as the suite stands, because
each needs a mechanism the ladder does not have -- a run with capacity analysis
on, a run whose `StartStats` is set, a run with weak-link analysis on *and* a
`StartStats`, a model with a hierarchy in it, and a cold candidate that
recovers while it is still `Cut` or `Linked`. Each test below supplies exactly
the one mechanism its claim is about and pins the literal it produces.

**Why `StartStats` is set on the built model rather than in the spec.**
`harness.run(spec, start_stats=...)` does work -- `build_from_spec` maps the
friendly name onto the `StartStats` column (`raptor/api/build.py:182`) and the
loader reads it back (`raptor/core/fileio/typed.py:405`). The historical
limitation recorded in `test_b_reports.py`'s module docstring was that the
reset writes two lines into the event log which are not `WriteEvents` lines, so
the harness's *reader* used to raise on them; `_fields` now returns `None` for
a line with no tab columns (`harness.py`, `_fields`) and the timeline skips it,
which is why every test here passes `start_stats=` straight through `harness.run`
and no test reaches around it.

**What "the claim is about a statistic, not a transition" means here.** Four
of the five claims are about a *number* the reset moves: a capacity monitor's
post-reset minimum, an event's post-reset state time, an element's mission
credit, a hierarchy's down-event count. A logged transition is not the thing
being claimed in those cases, so the statistic is asserted directly -- from the
End-of-Run report row for the capacity case, and from the engine's own
per-run `weak_events` snapshot for the second and from its weak-link report
rows for the third and the fifth. The `assert_timeline` literal alongside each
is the *other half* of the claim and is asserted for its own sake: **the
statistics reset moves no element state**, so each of these runs has the
timeline its un-reset twin has. That is a real claim about the reset (the
capacity mechanism is a reporting overlay and `ResetAllStatistics` is a
monitor operation), it is written inline so the retirement gate's `ast` pass
can see it, and it is what rules out "the number moved because the run went
differently". The third and fifth tests are the two places the halves are not
identical: the same re-seed also restarts the mission bookkeeping, so their
mission close reads `Mission_success` where the un-reset run's reads
`Mission_failure`, and that difference is asserted rather than glossed.

Source: Raptor 7 (`ResetAllStatistics`, the whole procedure),
Raptor 7 (the `Started Gathering Statistics` marker and
`StatsStartTime := SimTime`), Raptor 7 (`nodeMissionBool[i]`),
Raptor 7 (`hierDE[hier.seqNum]:=0`), Raptor 7
(`ASK NodeFlowMon[i]/NodeCapMon[i] TO Reset` and the
`NodeFlow[i]:=NodeFlow[i]` / `NodeCapacity[i]:=NodeCapacity[i]` self-assignments
that follow), Raptor 7 (`blockMissionBool[i]` /
`blockMCompleted[i]`), Raptor 7 (`eventMissionBool[i]` /
`eventMCompleted[i]`), Raptor 7 (the per-run
`blockMissionBool:=TRUE` / `eventMissionBool:=TRUE` at every run start, which
`reset_all_statistics` deliberately does *not* do -- it seeds from the current
status instead); Raptor 7 (`ChangeBlockState`, whose
`coldsAffected` arm is the cold-change-group registration
`orchestrator.py` ports), Raptor 7 (the per-run
`ASK hier TO SetToInitValues` whose `hierDE:=0` the reset repeats),
Raptor 7 (the only two sites that *increment* `hierDE`,
which is what makes the reset's `:=0` observable);
docs/engine-design/kickoff-and-runs.adoc (where `StartStats` is read and
which reset fires for which termination type);
docs/engine-design/node-status-and-cold-standby.adoc (the `Cut`/`Linked`
active-status vocabulary a cold candidate carries while it recovers)
"""
from __future__ import annotations

from typing import Any

from raptor.core.engine import runner as _runner

from . import harness
from .harness import FIXED, assert_timeline
from .spine import spine

#: The capacity rung's own constants, so the numbers in the literals below can
#: be checked against `spine.py`'s rather than re-derived here. `_s11` puts
#: `flow_generated` on the Start node and caps the one link every delivered
#: unit must cross (`E1 -> End`) at `FLOW_LIMIT`.
FLOW_GENERATED = 200
FLOW_LIMIT = 100

#: When S11's delayed statistics start gathering. Chosen, not arbitrary: S11's
#: A is repaired at hour 62, so at exactly 62 the system is still `Red` and the
#: capacity monitors are still holding **zero** -- the value the reset
#: re-seeds them with. That is what makes the post-reset `MinFlow`/`MinCapacity`
#: columns observable at all; see the first test's docstring.
CAP_RESET_AT = 62.0

#: When the event rung's delayed statistics start gathering. S10's event fires
#: at hour 0 and is re-armed only by `EndSimulation` at 100, so the post-reset
#: window is exactly `100 - EVENT_RESET_AT` hours long.
EVENT_RESET_AT = 25.0
EVENT_RUN_LENGTH = 100.0

#: The weak-link fixture's three phases. `P1`/`P2`/`P3` are 30/20/50 hours, all
#: missions, and the boundary between P1 and P2 is what fires the event's
#: `A: 0.000` cell -- a certain failure -- so the event really does lose its
#: mission credit before the reset and the re-seed is observable.
WEAK_PHASES = (("P1", 30.0), ("P2", 20.0), ("P3", 50.0))

#: When the weak-link fixture's delayed statistics start gathering. At 70 every
#: element of that model is up: A was repaired at 60, B at 62, and the event
#: re-fired into `Success` at 50. That is the point -- the reset's mission-credit
#: seed reads the *current* status, so a run reset while everything is up hands
#: the credit back.
WEAK_RESET_AT = 70.0

#: The cold candidate's `Fixed` repair life in the cold-group fixture. Five
#: hours, so C's repair completes at 55 -- inside phase 1, where C is `Linked` --
#: rather than at 60, where the boundary would have made it `Active` first and
#: the cold-change-group arm would never be entered.
COLD_REPAIR = 5.0
COLD_FAIL = 50.0

#: The hierarchy fixture's one block: it fails at 50 and is repaired at 60, so
#: the hierarchy's Out marker node leaves `Down` exactly once inside the run
#: and `down_event_count` is 1 before the reset.
HIER_FAIL, HIER_REPAIR = 50.0, 10.0

#: When the hierarchy fixture's delayed statistics start gathering. Ten hours
#: after the repair, so the hierarchy is `Good` at the reset and its only down
#: event is behind it -- which is the whole point, because the counter the reset
#: clears is only legible once nothing is left to increment it.
HIER_RESET_AT = 70.0


def _capacity_spec() -> dict[str, Any]:
    """`spine(11)` with delayed statistics switched on.

    S11 already carries the mechanism this claim needs and the trap that goes
    with it: `capacity` on, `flow_generated` at the Start node, a cap of
    `FLOW_LIMIT` on the one link every delivered unit crosses, and
    `full_flow = 0` on the End node so the cap binds at all (`_s11`'s own
    docstring, and `raptor/api/validate.py::_check_capacity_settings`, which
    exists to warn about exactly that combination). Only `StartStats` is added.
    """
    spec = spine(11)
    spec["system"]["start_stats"] = CAP_RESET_AT
    return spec


def _event_spec() -> dict[str, Any]:
    """`spine(10)` with delayed statistics at hour `EVENT_RESET_AT`.

    S10 is the event rung: `E1` sits between the cold node and the End node,
    fires once at t=0 and is put back to `Armed` by `EndSimulation` at the run
    end, so its whole life is one `Success` interval whose length the reset
    shortens.
    """
    spec = spine(10)
    spec["system"]["start_stats"] = EVENT_RESET_AT
    return spec


def _weak_spec() -> dict[str, Any]:
    """Three missions, two blocks into a 2-of-2 Join, and a phased event.

    Written here rather than taken off the ladder because the claim needs three
    mechanisms at once, and `spine(9)`'s two phases (the second of them
    *non*-mission) close the mission at 60 rather than re-opening one: what this
    fixture wants is an event that has genuinely forfeited its mission credit
    and is `Success` again by the time the reset fires.

    * Three phases, `WEAK_PHASES`, so there is a mission to close after the
      reset -- the credit is only *observable* when a mission ends.
    * `E1` phased `A: 1.000` / `A: 0.000` / `A: 1.000`. The P1->P2 boundary at
      30 re-fires the event with phase 2's cell, a `Fixed` 0.0, so it fails;
      the P2->P3 boundary at 50 re-fires it with phase 3's cell and it succeeds
      again. Both draws are free (`Fixed` is distribution code 19), so the run
      still consumes no randomness.
    * `A` fails at 50 and `B` at 52, both repaired by 62, into a `k = 2` Join,
      so the Join goes `Bad` while either is down and is `Good` from 62.
    """
    return {
        "system": {"run_length": 100.0, "num_runs": 1,
                   "termination": "TimeSim",
                   "start_stats": WEAK_RESET_AT},
        "phases": [
            {"name": name, "mission": 1, "dist": FIXED, "params": [length]}
            for name, length in WEAK_PHASES
        ],
        "phase_states": {"E1": ["A: 1.000", "A: 0.000", "A: 1.000"]},
        "blocks": [
            {"name": "A", "fail_dist": FIXED, "fail_params": [50.0],
             "repair_dist": FIXED, "repair_params": [10.0]},
            {"name": "B", "fail_dist": FIXED, "fail_params": [52.0],
             "repair_dist": FIXED, "repair_params": [10.0]},
        ],
        "nodes": [
            {"name": "Start", "type": 1, "n": 0, "out": 2},
            {"name": "Join", "type": 2, "n": 2, "out": 1, "k": 2},
            {"name": "End", "type": 3, "n": 1, "out": 0},
        ],
        "links": [{"from": "Start", "to": "A"},
                  {"from": "Start", "to": "B"},
                  {"from": "A", "to": "Join"},
                  {"from": "B", "to": "Join"},
                  {"from": "Join", "to": "E1"},
                  {"from": "E1", "to": "End"}],
        "events": [{"name": "E1", "dist": FIXED, "failVal": 1.0}],
    }


def _cold_spec() -> dict[str, Any]:
    """`spine(9)` with its cold candidate `Linked` in phase 1.

    S8's cold group is `C` (running, `cold_pri` 10) and `D` (standby,
    `cold_pri` 20) into the `Cold` node, and S9 adds the two-phase table. The
    one change here is `phase_states` putting C in `L: 1.000` for phase 1 and
    `A: 1.000` for phase 2, plus C's repair shortened to `COLD_REPAIR` hours.

    That combination is what puts a block that has just finished a repair
    *back into Running while still `Linked`* at the EFPA link into a cold node,
    which is the only shape that reaches `ChangeBlockState`'s `coldsAffected`
    arm. `COLD_REPAIR = 5` is load-bearing: S9's boundary is at 60 and C's
    default 10-hour repair would complete at 60 too, so the boundary would make
    C `Active` first and the arm would not be entered. At 5 hours C is repaired
    at 55, thirteen hours before the boundary.

    `D` is left alone on purpose -- it is the standby member, and giving it a
    phase cell would move the group rather than the candidate.
    """
    spec = spine(9)
    spec["phase_states"] = {"C": ["L: 1.000", "A: 1.000"]}
    for block in spec["blocks"]:
        if block["name"] == "C":
            block["repair_params"] = [COLD_REPAIR]
    return spec


def _hier_spec() -> dict[str, Any]:
    """`spine(1)`'s topology wrapped in one hierarchy `H1`, with delayed
    statistics at hour `HIER_RESET_AT`.

    Written here rather than taken off the ladder because the ladder has no
    rung for "a hierarchy": none of S1-S11 contains one, so
    `reset_all_statistics`'s `for hier in self.model.hiers_by_id.values()`
    loop has an empty sequence on every rung and the hierarchy's own counters
    are never reset by anything the suite runs. The shape is the smallest one
    that works -- `Start -> in -> A -> J -> out -> End`, with `in` the
    hierarchy's Type=4 In marker, `out` its Type=5 Out marker, and `J` a
    1-into-1 node between `A` and `out`.

    The wiring is the load-bearing part, and it is one decision, not two. A
    hierarchy's *status* is the aggregation of its Out marker node's incoming
    paths (`docs/engine-design/hierarchy-status.adoc`), and `down_event_count` is
    incremented only when that Out marker *leaves* `Down` or `NodePM`
   -- so `out` has to be downstream
    of the block for a failing block to put the hierarchy down at all. Putting
    `J` between `A` and `out` is what gives `out` a path of its own to go down:
    at 50 `J` goes `Bad`, `out` goes `Bad`, and the hierarchy's own first
    logged state is `Bad`. `A` repaired at 60 brings all three back.

    Nothing else is needed, and nothing else is added: no phases (so the
    run-end close is the only mission line), no event, no pool, one block and
    five nodes.
    """
    return {
        "system": {"run_length": 100.0, "num_runs": 1,
                   "termination": "TimeSim",
                   "start_stats": HIER_RESET_AT},
        "hierarchies": [{"id": 1, "name": "H1", "in_id": 2, "out_id": 4,
                         "level": 1}],
        "blocks": [
            {"name": "A", "parent": 1, "fail_dist": FIXED,
             "fail_params": [HIER_FAIL], "repair_dist": FIXED,
             "repair_params": [HIER_REPAIR]},
        ],
        "nodes": [
            {"id": 1, "name": "Start", "type": 1, "n": 0, "out": 1},
            {"id": 2, "name": "in", "type": 4, "n": 1, "out": 1, "parent": 1},
            {"id": 3, "name": "J", "type": 2, "n": 1, "out": 1, "k": 1,
             "parent": 1},
            {"id": 4, "name": "out", "type": 5, "n": 1, "out": 1, "parent": 1},
            {"id": 5, "name": "End", "type": 3, "n": 1, "out": 0},
        ],
        "links": [{"from": "Start", "to": "in"},
                  {"from": "in", "to": "A"},
                  {"from": "A", "to": "J"},
                  {"from": "J", "to": "out"},
                  {"from": "out", "to": "End"}],
    }


def _per_run_snapshot(spec: dict[str, Any], runs: int = 1) -> list[Any]:
    """Every run's own `RunReportData`, captured through the runner's own
    `report_sink`.

    The harness is still the only constructor (rule B3): the model is built by
    `harness.run`, and the batch is then re-run from that built model with
    `report_sink` set, because `harness.run` exposes the report *text* but not
    the report *objects* -- and the claims here are about numbers inside them
    (`weak_stats` / `weak_events`' `m_completed`, an event's `run` state time),
    which no report row prints raw. This is the same two-call shape
    `test_b_reports.py::_report_data` already uses.
    """
    rbd = harness.run(spec, weak_analysis=True).rbd
    data: list[Any] = []
    _runner.run_simulation_with_reports(
        rbd, runs, date=harness.REPORT_DATE, report_sink=data)
    return data


def _row(report: list[str], label: str) -> list[str]:
    """One row of the End-of-Run report, split into its fields.

    The reports are fixed-column text, so `split()` is the honest reading: the
    claim is which number sits in which column, not how many spaces separate
    them. First match wins, and a missing row is an `AssertionError` naming the
    label rather than an `IndexError` three lines later.
    """
    for line in report:
        fields = line.split()
        if fields[:1] == [label]:
            return fields
    raise AssertionError(f"no End-of-Run row labelled {label!r}")


def _weak_row(report: list[str], section: str, label: str) -> list[str]:
    """One row of the weak-link section's table, split into its fields.

    A block's *name* appears in three different tables -- `SPARING`'s
    `blockName Failures SpareSource Consumed`, `BLOCK_RESULTS`' three-column
    `Ao Do R`, and its own eight-column state-percentage table -- so a bare
    first-match lookup reads the wrong one. This takes the section header
    (`BLOCK_RESULTS`, `NODE_RESULTS`, `EVENT_RESULTS`) and reads the first row
    after it whose first field is *label*, which is unambiguous.
    """
    start = next((i for i, line in enumerate(report)
                  if line.split()[:1] == [section]), None)
    if start is None:
        raise AssertionError(f"no {section!r} table in the End-of-Run report")
    # Past the section's own column header (`Block   Ao   Do   R`), the rows
    # run to the first blank line -- which is how `BLOCK_RESULTS`' second,
    # eight-column table stays out of this one's rows.
    seen_header = False
    for line in report[start + 1:]:
        fields = line.split()
        if not fields:
            if seen_header:
                break
            continue
        if "Ao" in fields and "R" in fields:
            seen_header = True
            continue
        if fields[:1] == [label]:
            return fields
    raise AssertionError(
        f"no {label!r} row in {section!r} (table was not found or is empty)")


# ---------------------------------------------------------------------------
# 1. The capacity monitors are reset and re-seeded from the value held at the
#    reset instant -- Raptor 7.
# ---------------------------------------------------------------------------

def test_capacity_flow_and_capacity_monitors_restart_at_the_statistics_reset():
    """A delayed statistics reset clears every node's flow and capacity
    monitors and re-seeds them with the value they hold at that instant, so the
    End-of-Run capacity row reports that instant's zero as the post-reset
    `MinFlow` and `MinCapacity` -- while the averages describe only the hours
    after it.

    Why: `ResetAllStatistics` does not merely `Reset` the two monitors, it
    immediately self-assigns them -- `ASK NodeFlowMon[i]/NodeCapMon[i] TO
    Reset` and then `NodeFlow[i]:=NodeFlow[i]` /
    `NodeCapacity[i]:=NodeCapacity[i]`. The
    self-assignment is the load-bearing half: a `Reset` alone leaves the
    monitor's `Min`/`Max` empty (`TimedStat.reset` sets them to `+inf`/`-inf`,
    `raptor/core/engine/stats.py:94-105`) and the report's
    `observed_minimum` then falls back to whatever the monitor happens to hold
    at the *end* of the run. The source's self-assignment makes the value held
    at the reset instant the post-reset window's first sample, so the minimum of
    the window is the minimum *as of the reset*, not of the run.

    That difference is only visible because hour 62 is chosen: S11's A is
    repaired at exactly 62, so at the instant the reset fires the system is
    still `Red` and the monitors still hold zero -- and zero is not a value the
    post-reset window ever reaches again, because from 62 on the system is
    `Green` and every hour flows the full `FLOW_LIMIT`. So the literal below
    says `MinFlow 0` next to `AveFlow 100.00`, which is a statement no
    post-reset-only window could produce.

    Setup: `spine(11)` -- capacity analysis on, `flow_generated = 200` at the
    Start node, `nom_flow`/`max_flow` = 100 on `E1 -> End`, and
    `full_flow = 0` on the End node so the cap binds (`_s11`'s docstring; that
    flag is part of the capacity mechanism, not a second one). One run of 100
    hours, `StartStats = 62.0`. Every distribution is `Fixed`.

    Expected: on both the `SystemCapacity` and the `End` row, `MinFlow 0`,
    `AveFlow 100.00`, `MaxFlow 100`, `MinCapacity 0`, `AveCapacity 100.00`,
    `MaxCapacity 100` -- and `UnitsProduced 3800.00`, the 38 post-reset hours
    times the 100-unit cap. The timeline is S11's own, unchanged: the reset is
    a monitor operation and moves no state.

    Source: Raptor 7 (`ASK NodeFlowMon[i]/NodeCapMon[i] TO
    Reset` plus the `NodeFlow[i]:=NodeFlow[i]` /
    `NodeCapacity[i]:=NodeCapacity[i]` self-assignments),
    Raptor 7 (`StatsStartTime := SimTime` and the
    `Started Gathering Statistics` marker), Raptor 7 (the per-run
    capacity extraction that reads `AveFlow`/`MinFlow` and multiplies
    `UnitsProduced` by `EndTime - StatsStartTime`);
    raptor/core/engine/stats.py:61-129 (`TimedStat`, whose `reset` empties the
    extrema and whose `observed_minimum` falls back to the held value);
    raptor/core/engine/capacity.py:327-347 (the per-run monitor creation
    whose `reset(0, sim_time)` the statistics reset repeats mid-run);
    docs/engine-design/node-status-and-cold-standby.adoc (Purpose, for the
    k-of-n the flow has to route through)
    """
    run = harness.run(_capacity_spec())

    assert run.draws == 0, (
        "every distribution is Fixed, so B2 says this run must draw nothing; "
        f"it drew {run.draws}")

    # The reset is a monitor operation: S11's timeline, row for row, including
    # the two `Node:Cold` lines and the run-end `Done` sweep.
    assert_timeline(run, [
        (0, 'System', 'State_change', '--', 'Mission'),
        (0, 'Block', 'A', '--', 'Good'),
        (0, 'Block', 'B', '--', 'Good'),
        (0, 'Block', 'C', '--', 'Good'),
        (0, 'Block', 'D', '--', 'Good'),
        (0, 'Event', 'E1', '--', 'Armed'),
        (0, 'Event', 'E1', 'Armed', 'Success'),
        (0, 'Block', 'D', 'Good', 'Standby'),
        (0, 'Node', 'Cold', '--', 'Good'),
        (30, 'Trigger', 'T1', '--', 'Fired'),
        (30, 'Block', 'A', 'Good', 'PM_is_due'),
        (30, 'Block', 'A', 'PM_is_due', 'PM'),
        (30, 'Block', 'B', 'Good', 'Idle'),
        (30, 'Node', 'Join', '--', 'Degraded'),
        (30, 'Node', 'Join', 'Degraded', 'Idle'),
        (30, 'Node', 'end', '--', 'Idle'),
        (30, 'System', 'State_change', 'Mission', 'Red'),
        (32, 'Block', 'A', 'PM', 'PM_complete'),
        (32, 'Block', 'A', 'PM_complete', 'Good'),
        (32, 'Block', 'B', 'Idle', 'Good'),
        (32, 'Node', 'Join', 'Idle', 'Degraded'),
        (32, 'Node', 'end', 'Idle', 'Good'),
        (32, 'Node', 'Join', 'Degraded', 'Good'),
        (32, 'System', 'State_change', 'Red', 'Green'),
        (40, 'Pool', 'SP', '--', 'Sparing'),
        (50, 'Block', 'C', 'Good', 'Failed'),
        (50, 'Block', 'C', 'Failed', 'Repairing'),
        (50, 'Block', 'D', 'Standby', 'Good'),
        (50, 'Node', 'Cold', 'Good', 'Degraded'),
        (50, 'System', 'State_change', 'Green', 'Yellow'),
        (52, 'Block', 'A', 'Good', 'Failed'),
        (52, 'Block', 'A', 'Failed', 'Repairing'),
        (52, 'Block', 'B', 'Good', 'Idle'),
        (52, 'Node', 'Join', 'Good', 'Degraded'),
        (52, 'Node', 'Join', 'Degraded', 'Idle'),
        (52, 'Node', 'end', 'Good', 'Idle'),
        (52, 'System', 'State_change', 'Yellow', 'Red'),
        (60, 'Trigger', 'T1', 'Fired', 'Fired'),
        (60, 'Block', 'C', 'Repairing', 'Repaired'),
        (60, 'Block', 'C', 'Repaired', 'Good'),
        (60, 'Block', 'C', 'Good', 'Standby'),
        (60, 'Node', 'Cold', 'Degraded', 'Good'),
        (60, 'System', 'Phase_change', '--', 'Phase_change'),
        (60, 'System', 'Phase_change', 'Phase_change', 'Mission_failure'),
        (60, 'System', 'State_change', 'Red', 'Red'),
        (60, 'System', 'Red_change', '--', 'Red'),
        (62, 'Block', 'A', 'Repairing', 'Repaired'),
        (62, 'Block', 'A', 'Repaired', 'Good'),
        (62, 'Block', 'B', 'Idle', 'Good'),
        (62, 'Node', 'Join', 'Idle', 'Degraded'),
        (62, 'Node', 'end', 'Idle', 'Good'),
        (62, 'Node', 'Join', 'Degraded', 'Good'),
        (62, 'System', 'State_change', 'Red', 'Green'),
        (67, 'Block', 'B', 'Good', 'Failed'),
        (67, 'Block', 'B', 'Failed', 'Sparing'),
        (67, 'Block', 'B', 'Sparing', 'RepHold'),
        (67, 'Node', 'Join', 'Good', 'Degraded'),
        (67, 'System', 'State_change', 'Green', 'Yellow'),
        (80, 'Pool', 'SP', 'Sparing', 'Sparing'),
        (80, 'Block', 'B', 'RepHold', 'Sparing'),
        (80, 'Block', 'B', 'Sparing', 'Repairing'),
        (85, 'Block', 'B', 'Repairing', 'Repaired'),
        (85, 'Block', 'B', 'Repaired', 'Good'),
        (85, 'Node', 'Join', 'Degraded', 'Good'),
        (85, 'System', 'State_change', 'Yellow', 'Green'),
        (90, 'Trigger', 'T1', 'Fired', 'Fired'),
        (90, 'Block', 'A', 'Good', 'PM_is_due'),
        (90, 'Block', 'A', 'PM_is_due', 'pmReqDefer'),
        (100, 'Block', 'A', 'pmReqDefer', 'Done'),
        (100, 'Block', 'B', 'Good', 'Done'),
        (100, 'Block', 'C', 'Standby', 'Done'),
        (100, 'Block', 'D', 'Good', 'Done'),
        (100, 'Event', 'E1', 'Success', 'Armed'),
        (100, 'System', 'Simulation_Terminate', '--', '--'),
    ])

    # MinFlow AveFlow MaxFlow UsageRate MinCapacity AveCapacity MaxCapacity
    # CapableRate. The two minima are the value the monitors held at hour 62 --
    # zero, because A's repair has not been recorded yet -- and the averages
    # are the 38 hours from 62 to 100, every one of them a full cap.
    for label in ("SystemCapacity", "end"):
        assert _row(run.eor, label)[1:] == [
            "0", "100.00", "100", "1.00", "0", "100.00", "100", "1.00",
        ], label
    assert _row(run.eor, "UnitsProduced") == ["UnitsProduced", "3800.00"]
    assert _row(run.eor, "FlowGenerated") == [
        "FlowGenerated", str(FLOW_GENERATED)]


# ---------------------------------------------------------------------------
# 2. Every float accumulator on an event's statistics is zeroed at the reset,
#    and its state clock re-anchors there -- Raptor 7's float
#    sweep, and the per-run reset it repeats.
# ---------------------------------------------------------------------------

def test_event_statistics_are_zeroed_and_re_anchored_at_the_statistics_reset():
    """A delayed statistics reset zeroes every float accumulator on an event's
    statistics and re-anchors that event's state clock at the reset time, so
    the event's reported state time is the post-reset window and the *second*
    run reports the same window rather than inheriting the first's.

    Why: the per-run reset at every run start is only
    `blockMissionBool:=TRUE; blockMCompleted:=0` and
    `eventMissionBool:=TRUE; eventMCompleted:=0`
    -- the *durations* are not part of
    it on the port's run-start path, which is why `_init_event`
    (`orchestrator.py`, the port of `RBDEventObj.SetToInitValues`, whose
    `SetToInitValues` accumulator zeroing is) zeroes
    `opCost`/`repCost` and `runTime`/`standbyTime`/`repairTime` explicitly and
    *before* the phased early-return. `ResetAllStatistics` is the mid-run twin:
    it walks `vars(es)` and sets every field that is a `float` to `0.0`, then
    `event.state_start_time := SimTime` -- a sweep by type rather than by name,
    because it is the same sweep the source does over its monitor arrays.

    The claim has two halves and they are the same claim: the reset is
    *per-run*, so a second run's event statistics describe the second run's
    post-reset window. That is only expressible across a run boundary -- Raptor
    never re-announces an event's state at `Begin_Run_N`, which is exactly why
    `Run.cross_run` chains every run continuously per object
    (`harness.py`, `Run.cross_run`). So this test runs three replications, says
    so under rule B2, and uses the corpus's own chaining rule to look at them.

    Setup: `spine(10)` -- `E1` between the cold node and the End node,
    `failVal = 1.0` drawn from a `Fixed` so the outcome is certain and free
    (`_s10`'s docstring). `StartStats = 25.0`, three runs. Every distribution
    is `Fixed`; the three runs are identical because nothing is stochastic.

    Expected: `E1`'s `run` state time is exactly `75.0` hours in every run --
    the 75 hours from the reset at 25 to the run end at 100 -- and not the
    whole 100 of an un-reset run, and not 75 + 100 + 100 for an inheriting one.
    Chained across the three runs the event reads
    `Armed -> Armed` at each run's t=0 (Raptor does not re-announce it, so the
    corpus measures the second and third runs from what the previous run last
    said), which is why `run` is 75 three times and not 275.

    Source: Raptor 7 (`eventMissionBool[i]` /
    `eventMCompleted[i]`, the weak half of the same loop), Raptor 7
    (the per-run `eventMissionBool:=TRUE; eventMCompleted:=0` this reset
    deliberately does not repeat -- it seeds from the current status),
    Raptor 7 (`RBDEventObj.SetToInitValues`'s own accumulator
    zeroing, which is where the per-run float zeroing lives),
    Raptor 7 (`EndSimulation`'s event sweep, the `Success ->
    Armed` at 100 that closes the last interval); raptor/core/engine/events.py
    (`EventStatistics`, the four float accumulators the sweep zeroes and the two
    weak-analysis fields it does not -- they are a `bool` and an `int`);
    raptor/core/engine/driver.py (the `weak_events` snapshot the assertion
    reads); harness.py (`Run.cross_run`'s chaining rule);
    docs/engine-design/events-and-phases.adoc (event state vocabulary)
    """
    spec = _event_spec()
    run = harness.run(spec, weak_analysis=True, runs=3)

    assert run.draws == 0, (
        "every distribution is Fixed, so B2 says this run must draw nothing; "
        f"it drew {run.draws}")

    # Rule B2's exception, stated: three replications, because the claim is
    # about what the second run inherits, which no single run can express.
    assert run.runs == 3

    # The statistics reset moves no element state: run 1's timeline is S10's
    # own, unchanged by `StartStats = 25` (the reset's own marker is a
    # `WriteString` line, which `harness._fields` skips as not-an-event-row).
    assert_timeline(run, [
        (0, 'System', 'State_change', '--', 'Mission'),
        (0, 'Block', 'A', '--', 'Good'),
        (0, 'Block', 'B', '--', 'Good'),
        (0, 'Block', 'C', '--', 'Good'),
        (0, 'Block', 'D', '--', 'Good'),
        (0, 'Event', 'E1', '--', 'Armed'),
        (0, 'Event', 'E1', 'Armed', 'Success'),
        (0, 'Block', 'D', 'Good', 'Standby'),
        (0, 'Node', 'Cold', '--', 'Good'),
        (30, 'Trigger', 'T1', '--', 'Fired'),
        (30, 'Block', 'A', 'Good', 'PM_is_due'),
        (30, 'Block', 'A', 'PM_is_due', 'PM'),
        (30, 'Block', 'B', 'Good', 'Idle'),
        (30, 'Node', 'Join', '--', 'Degraded'),
        (30, 'Node', 'Join', 'Degraded', 'Idle'),
        (30, 'Node', 'end', '--', 'Idle'),
        (30, 'System', 'State_change', 'Mission', 'Red'),
        (32, 'Block', 'A', 'PM', 'PM_complete'),
        (32, 'Block', 'A', 'PM_complete', 'Good'),
        (32, 'Block', 'B', 'Idle', 'Good'),
        (32, 'Node', 'Join', 'Idle', 'Degraded'),
        (32, 'Node', 'end', 'Idle', 'Good'),
        (32, 'Node', 'Join', 'Degraded', 'Good'),
        (32, 'System', 'State_change', 'Red', 'Green'),
        (40, 'Pool', 'SP', '--', 'Sparing'),
        (50, 'Block', 'C', 'Good', 'Failed'),
        (50, 'Block', 'C', 'Failed', 'Repairing'),
        (50, 'Block', 'D', 'Standby', 'Good'),
        (50, 'Node', 'Cold', 'Good', 'Degraded'),
        (50, 'System', 'State_change', 'Green', 'Yellow'),
        (52, 'Block', 'A', 'Good', 'Failed'),
        (52, 'Block', 'A', 'Failed', 'Repairing'),
        (52, 'Block', 'B', 'Good', 'Idle'),
        (52, 'Node', 'Join', 'Good', 'Degraded'),
        (52, 'Node', 'Join', 'Degraded', 'Idle'),
        (52, 'Node', 'end', 'Good', 'Idle'),
        (52, 'System', 'State_change', 'Yellow', 'Red'),
        (60, 'Trigger', 'T1', 'Fired', 'Fired'),
        (60, 'Block', 'C', 'Repairing', 'Repaired'),
        (60, 'Block', 'C', 'Repaired', 'Good'),
        (60, 'Block', 'C', 'Good', 'Standby'),
        (60, 'Node', 'Cold', 'Degraded', 'Good'),
        (60, 'System', 'Phase_change', '--', 'Phase_change'),
        (60, 'System', 'Phase_change', 'Phase_change', 'Mission_failure'),
        (60, 'System', 'State_change', 'Red', 'Red'),
        (60, 'System', 'Red_change', '--', 'Red'),
        (62, 'Block', 'A', 'Repairing', 'Repaired'),
        (62, 'Block', 'A', 'Repaired', 'Good'),
        (62, 'Block', 'B', 'Idle', 'Good'),
        (62, 'Node', 'Join', 'Idle', 'Degraded'),
        (62, 'Node', 'end', 'Idle', 'Good'),
        (62, 'Node', 'Join', 'Degraded', 'Good'),
        (62, 'System', 'State_change', 'Red', 'Green'),
        (67, 'Block', 'B', 'Good', 'Failed'),
        (67, 'Block', 'B', 'Failed', 'Sparing'),
        (67, 'Block', 'B', 'Sparing', 'RepHold'),
        (67, 'Node', 'Join', 'Good', 'Degraded'),
        (67, 'System', 'State_change', 'Green', 'Yellow'),
        (80, 'Pool', 'SP', 'Sparing', 'Sparing'),
        (80, 'Block', 'B', 'RepHold', 'Sparing'),
        (80, 'Block', 'B', 'Sparing', 'Repairing'),
        (85, 'Block', 'B', 'Repairing', 'Repaired'),
        (85, 'Block', 'B', 'Repaired', 'Good'),
        (85, 'Node', 'Join', 'Degraded', 'Good'),
        (85, 'System', 'State_change', 'Yellow', 'Green'),
        (90, 'Trigger', 'T1', 'Fired', 'Fired'),
        (90, 'Block', 'A', 'Good', 'PM_is_due'),
        (90, 'Block', 'A', 'PM_is_due', 'pmReqDefer'),
        (100, 'Block', 'A', 'pmReqDefer', 'Done'),
        (100, 'Block', 'B', 'Good', 'Done'),
        (100, 'Block', 'C', 'Standby', 'Done'),
        (100, 'Block', 'D', 'Good', 'Done'),
        (100, 'Event', 'E1', 'Success', 'Armed'),
        (100, 'System', 'Simulation_Terminate', '--', '--'),
    ])

    # The statistic itself. Three identical reports, one per run, each holding
    # `run` = 75.0 for E1: the post-reset window, counted once per run.
    for report in _per_run_snapshot(spec, runs=3):
        assert [(v["name"], v["run"], v["standby"], v["repair"])
                for v in report.weak_events.values()] == [
            ("E1", EVENT_RUN_LENGTH - EVENT_RESET_AT, 0.0, 0.0)], report.run_number

    # And the chained view across the run boundary, which is the corpus's own
    # rule: E1 is `Armed -> Armed` at the start of runs 2 and 3, so nothing
    # about the earlier runs is folded into the later ones.
    assert [str(e) for e in run.cross_run if e.kind == "Event"] == [
        "0 Event:E1 -- -> Armed",
        "0 Event:E1 Armed -> Success",
        "100 Event:E1 Success -> Armed",
        "0 Event:E1 Armed -> Armed",
        "0 Event:E1 Armed -> Success",
        "100 Event:E1 Success -> Armed",
        "0 Event:E1 Armed -> Armed",
        "0 Event:E1 Armed -> Success",
        "100 Event:E1 Success -> Armed",
    ]


# ---------------------------------------------------------------------------
# 3. Under weakAnalysis, the reset seeds each element's mission credit from
# its CURRENT status -- Raptor 7.
# ---------------------------------------------------------------------------

def test_the_weak_link_mission_credit_is_re_seeded_from_each_elements_status():
    """With weak-link analysis on, a delayed statistics reset re-seeds every
    element's mission credit from the status that element holds *at the reset
    instant* -- so a run reset while everything is up hands back the credit that
    the run's earlier failures had forfeited, and the mission that closes
    afterwards reports `R 1.0` where the un-reset run reports `R 0.0`.

    Why: this is the one site in the reset whose rule is deliberately *not* the
    run-start rule. Raptor 7 write
    `blockMissionBool := TRUE` and `eventMissionBool := TRUE` unconditionally at
    every run start, and the port keeps that (`kickoff.py`, the two unconditional
    writes). `ResetAllStatistics` does not: Raptor 7 restarts
    `nodeMissionBool[i]` from `node.Status not in (Down, NodePM)`,
     restarts `blockMissionBool[i]` from
    `block.opStatus not in (Repairing, RepHold, PM, PMhold, Done)`, and
     restarts `eventMissionBool[i]` from
    `event.opStatus <> Failure`. Each also zeroes its `mCompleted` counter. An
    element already down or in maintenance at the reset instant therefore enters
    the post-reset window *without* credit, and one that is up enters *with* it
    -- which is what the `R` column of the weak-link rows prints
    (for events for nodes,
     for blocks, `R = mCompleted / missionsAttempted`).

    `weak_analysis` is a separate `SYSTEM_DATA` flag from `cost_analysis` and
    from the run's own flags, and these three arms are inside
    `if self.model.weak_analysis:` -- so with the flag off the analysis does not
    exist for the run at all and none of this is written. That is the gate, and
    it is why this test turns it on explicitly.

    Setup: the `_weak_spec` fixture -- three 30/20/50-hour missions, blocks `A`
    (fails 50) and `B` (fails 52) into a `k = 2` `Join`, and event `E1` phased
    `A: 1.000` / `A: 0.000` / `A: 1.000` so it certainly fails at the P1->P2
    boundary (30) and certainly succeeds again at P2->P3 (50). One run of 100
    hours, `StartStats = 70.0`, `weak_analysis = True`. Every distribution is
    `Fixed`, so the event's two re-fires draw nothing and the whole run draws
    nothing.

    Expected: at hour 70 `A`, `B`, `Join` and `E1` are all up -- A repaired at
    60, B at 62, the event `Success` since 50, the `Join` `Good` since 62 -- so
    the reset seeds all four with credit, and the mission that closes at 100
    credits all four: `R 1.000000000` on every row. The same model with
    `StartStats = 0` reports `R 0.000000000` for `A`, `B` and `E1` (each
    forfeited credit mid-mission) and `Join` is `0.000000000` too because it
    was `Bad` when its mission closed. The two runs agree on every timeline row
    except the mission close, which the same re-seed moves from
    `Mission_failure` to `Mission_success`: `ResetAllStatistics` restarts "the
    mission/cycle bookkeeping" from the current system state, and the system is
    `Green` at hour 70.

    Source: Raptor 7 (`nodeMissionBool[i]`, seeded from the
    node's status at the reset instant), Raptor 7
    (`blockMissionBool[i]` / `blockMCompleted[i]`, seeded from the block's
    `_MAINT_LIKE` predicate), Raptor 7 (`eventMissionBool[i]` /
    `eventMCompleted[i]`, seeded from `opStatus <> Failure`),
    Raptor 7 (the per-run unconditional writes this
    deliberately differs from), Raptor 7 (the mission close that
    credits every holder of the flag), Raptor 7 (the close itself,
    which is what fires at the run end here);
    raptor/core/engine/system_status.py (`_MAINT_LIKE` and the mission
    begin/close arms), raptor/core/engine/blocks.py (`BlockStatistics.mission_bool`
    / `m_completed`, the two fields the float sweep cannot touch -- one is a
    `bool`, one an `int`), raptor/core/engine/reports.py (the `R` column);
    docs/engine-design/events-and-phases.adoc (the phase table that gives
    this run three missions to close)
    """
    spec = _weak_spec()
    run = harness.run(spec, weak_analysis=True)

    assert run.draws == 0, (
        "every distribution is Fixed, so B2 says this run must draw nothing; "
        f"it drew {run.draws}")

    # The reset moves no *element* state: this is the fixture's own timeline,
    # and the un-reset run of the same model produces exactly these rows (the
    # one difference, the mission close, is asserted at the foot of the test).
    assert_timeline(run, [
        (0, 'System', 'State_change', '--', 'Mission'),
        (0, 'Block', 'A', '--', 'Good'),
        (0, 'Block', 'B', '--', 'Good'),
        (0, 'Event', 'E1', '--', 'Armed'),
        (0, 'Event', 'E1', 'Armed', 'Success'),
        (30, 'System', 'Phase_change', '--', 'Phase_change'),
        (30, 'Event', 'E1', 'Success', 'Failed'),
        (30, 'Node', 'end', '--', 'Bad'),
        (30, 'System', 'State_change', 'Mission', 'Red'),
        (50, 'Block', 'A', 'Good', 'Failed'),
        (50, 'System', 'Phase_change', 'Phase_change', 'Phase_change'),
        (50, 'Block', 'A', 'Failed', 'Repairing'),
        (50, 'Node', 'Join', '--', 'Bad'),
        (50, 'Event', 'E1', 'Failed', 'Success'),
        (50, 'System', 'Red_change', '--', 'Red'),
        (52, 'Block', 'B', 'Good', 'Failed'),
        (52, 'Block', 'B', 'Failed', 'Repairing'),
        (52, 'Node', 'Join', 'Bad', 'Bad'),
        (52, 'System', 'Red_change', 'Red', 'Red'),
        (60, 'Block', 'A', 'Repairing', 'Repaired'),
        (60, 'Block', 'A', 'Repaired', 'Good'),
        (60, 'Node', 'Join', 'Bad', 'Bad'),
        (60, 'System', 'Red_change', 'Red', 'Red'),
        (62, 'Block', 'B', 'Repairing', 'Repaired'),
        (62, 'Block', 'B', 'Repaired', 'Good'),
        (62, 'Node', 'Join', 'Bad', 'Good'),
        (62, 'Node', 'end', 'Bad', 'Good'),
        (62, 'System', 'State_change', 'Red', 'Green'),
        (100, 'Block', 'A', 'Good', 'Done'),
        (100, 'Block', 'B', 'Good', 'Done'),
        (100, 'Event', 'E1', 'Success', 'Armed'),
        (100, 'System', 'Phase_change', 'Phase_change', 'Mission_success'),
        (100, 'System', 'Simulation_Terminate', '--', '--'),
    ])

    # The statistic: every element's `R` column, which is
    # `mCompleted / missionsAttempted`. `Ao` and `Do` are the availability
    # ratios over the same window and are 1.000000000 either way; `R` is the
    # third column and is the whole claim.
    for name in ("A", "B"):
        assert _weak_row(run.eor, "BLOCK_RESULTS", name) == [
            name, "1.000000000", "1.000000000", "1.000000000"], name
    # Ao Do R %Green %Yellow %Red %Orange %Blue %Brown
    assert _weak_row(run.eor, "NODE_RESULTS", "Join") == [
        "Join", "1.000000000", "1.000000000", "1.000000000",
        "100.000000", "0.000000", "0.000000", "0.000000", "0.000000",
        "0.000000"], _weak_row(run.eor, "NODE_RESULTS", "Join")
    # Ao Do R %Success %Armed %Failure
    assert _weak_row(run.eor, "EVENT_RESULTS", "E1") == [
        "E1", "1.000000000", "1.000000000", "1.000000000",
        "100.000000", "0.000000", "0.000000"], _weak_row(
            run.eor, "EVENT_RESULTS", "E1")

    # The same model with no reset: A, B and E1 each forfeited their credit
    # before the mission closed, and the `Join` was `Bad` when it closed, so
    # every `R` is zero. That is the other half of the claim -- the reset is
    # what moved the number, and the timeline below is identical either way.
    unreset_spec = _weak_spec()
    unreset_spec["system"]["start_stats"] = 0.0
    unreset = harness.run(unreset_spec, weak_analysis=True)
    for section, name in (("BLOCK_RESULTS", "A"), ("BLOCK_RESULTS", "B"),
                          ("NODE_RESULTS", "Join"),
                          ("EVENT_RESULTS", "E1")):
        assert _weak_row(unreset.eor, section, name)[3] == "0.000000000", name

    # The two runs agree on every row except the mission close, and that one
    # difference is the same re-seed seen from the other side: the reset also
    # restarts the mission bookkeeping (`ResetAllStatistics` re-anchors
    # "the mission/cycle bookkeeping ... from the current system state",
    # Raptor 7), and the system is `Green` at hour 70, so the
    # mission that closes at 100 is a *success* here and the failure it was
    # before the reset.
    assert [tuple(e) for e in unreset.events[:-2]] == [
        tuple(e) for e in run.events[:-2]]
    assert str(unreset.events[-2]) == (
        "100 System:Phase_change Phase_change -> Mission_failure")
    assert str(run.events[-2]) == (
        "100 System:Phase_change Phase_change -> Mission_success")


# ---------------------------------------------------------------------------
# 4. A cold candidate that returns to Running while still Cut/Linked
#    registers its cold node -- Raptor 7.
# ---------------------------------------------------------------------------

def test_a_cold_candidate_that_recovers_while_still_linked_resurveys_its_cold_node():
    """A cold candidate that finishes a repair while it is still `Linked` is
    registered in its cold node's change group, so the cold survey runs again
    in that same instant and logs the cold node's own status line -- a
    `Node:Cold` row the same model does not produce without the registration.

    Why: `ChangeBlockState`'s `coldsAffected` arm
    marks the node on the candidate's EFPA link when the block "stays Cut/Linked
    but comes back to Running" -- the source's own comment is "may need to go to
    standby". `CheckColds` then drains the group and
    runs `runColdLogic` on every marked node, which is what logs the node line.
    The port guards the mark on the transition being delta-free, the block's
    active status not being `Active`, the active status being unchanged, and the
    new operating status being `Running` -- so the only shape that reaches it is
    a repair completing on a block that is not `Active`.

    Phase 1's `L: 1.000` on `C` is what makes that shape: `C` is `Linked` for
    the first 60 hours, so every state change it makes logs its *active* status
    rather than its operating status, and its repair completing at 55 is a
    transition back to `Running` while `Linked`. The observable is the extra
    `Node:Cold Good -> Good` row at 55: the survey found the node already `Good`
    and logged it anyway, which is the mark's whole effect. Nothing else in this
    run differs -- the same fixture with the mark suppressed produces the same
    rows minus that one, which is what makes it a claim about the mark and not
    about the model.

    Setup: `_cold_spec` -- `spine(9)` with `C` phased `L: 1.000` in phase 1 and
    `A: 1.000` in phase 2, and `C`'s `Fixed` repair life shortened to 5 hours
    so its repair completes at 55, inside phase 1. The cold group is S8's own
    (`C` running at `cold_pri` 10, `D` standby at 20, `k = 1`, `k* = 1`,
    `a_prob = 1.0`, `auto_time = 0.0`, `man_time = -1.0`), so both candidates
    are fresh blocks that appear in no other group -- which is what
    `model_checks.refuse_unrunnable` demands. One run of 100 hours; every
    distribution is `Fixed`, so `D`'s 200-hour life and the trigger and pool
    all draw nothing.

    Expected: `C` runs `Linked -> Failed -> Linked` at 50 and
    `Linked -> Repaired -> Linked` at 55, and the `Cold` node -- which has been
    `Good` since kickoff -- logs `Good -> Good` at 55. The P1->P2 boundary at 60
    then links `C` back to `Good`, and nothing about the run's ending changes.

    Source: Raptor 7 (`ChangeBlockState`'s `coldsAffected` arm
    and its "may need to go to standby" comment), Raptor 7
    (`CheckColds`, which drains the group and runs `runColdLogic`),
    Raptor 7 (`ChangeBlockState`'s active-status log, which is
    why a `Linked` block's rows read `Linked -> Failed`),
    Raptor 7 (kickoff puts every cold node in the change group);
    raptor/core/engine/cascade_cold.py (`_flush_cold_changes`, the drain loop),
    raptor/core/engine/blocks.py:253-263 (the delta ladder, whose `Cut ->
    Cut` and `Linked -> Linked` arms are the delta-free transitions this arm
    requires); docs/engine-design/node-status-and-cold-standby.adoc
    ("Structure": the survey / kickoff-redistribution split, and the `Cut` /
    `Linked` active-status vocabulary)
    """
    run = harness.run(_cold_spec())

    assert run.draws == 0, (
        "every distribution is Fixed, so B2 says this run must draw nothing; "
        f"it drew {run.draws}")

    assert_timeline(run, [
        (0, 'System', 'State_change', '--', 'Mission'),
        (0, 'Block', 'A', '--', 'Good'),
        (0, 'Block', 'B', '--', 'Good'),
        (0, 'Block', 'C', '--', 'Linked'),
        (0, 'Block', 'D', '--', 'Good'),
        (0, 'Block', 'D', 'Good', 'Standby'),
        (0, 'Node', 'Cold', '--', 'Good'),
        (30, 'Trigger', 'T1', '--', 'Fired'),
        (30, 'Block', 'A', 'Good', 'PM_is_due'),
        (30, 'Block', 'A', 'PM_is_due', 'PM'),
        (30, 'Block', 'B', 'Good', 'Idle'),
        (30, 'Node', 'Join', '--', 'Degraded'),
        (30, 'Node', 'Join', 'Degraded', 'Idle'),
        (30, 'Node', 'end', '--', 'Idle'),
        (30, 'System', 'State_change', 'Mission', 'Red'),
        (32, 'Block', 'A', 'PM', 'PM_complete'),
        (32, 'Block', 'A', 'PM_complete', 'Good'),
        (32, 'Block', 'B', 'Idle', 'Good'),
        (32, 'Node', 'Join', 'Idle', 'Degraded'),
        (32, 'Node', 'end', 'Idle', 'Good'),
        (32, 'Node', 'Join', 'Degraded', 'Good'),
        (32, 'System', 'State_change', 'Red', 'Green'),
        (40, 'Pool', 'SP', '--', 'Sparing'),
        (50, 'Block', 'C', 'Linked', 'Failed'),
        (50, 'Block', 'C', 'Failed', 'Linked'),
        (50, 'System', 'State_change', 'Green', 'Yellow'),
        (52, 'Block', 'A', 'Good', 'Failed'),
        (52, 'Block', 'A', 'Failed', 'Repairing'),
        (52, 'Block', 'B', 'Good', 'Idle'),
        (52, 'Node', 'Join', 'Good', 'Degraded'),
        (52, 'Node', 'Join', 'Degraded', 'Idle'),
        (52, 'Node', 'end', 'Good', 'Idle'),
        (52, 'System', 'State_change', 'Yellow', 'Red'),
        # The claim: C's repair completes while it is still Linked, which
        # registers the Cold node and re-surveys it in this same instant.
        (55, 'Block', 'C', 'Linked', 'Repaired'),
        (55, 'Block', 'C', 'Repaired', 'Linked'),
        (55, 'Node', 'Cold', 'Good', 'Good'),
        (55, 'System', 'Red_change', '--', 'Red'),
        (60, 'Trigger', 'T1', 'Fired', 'Fired'),
        (60, 'System', 'Phase_change', '--', 'Phase_change'),
        (60, 'System', 'Phase_change', 'Phase_change', 'Mission_failure'),
        (60, 'System', 'State_change', 'Red', 'Red'),
        (60, 'Block', 'C', 'Linked', 'Good'),
        (60, 'System', 'Red_change', 'Red', 'Red'),
        (62, 'Block', 'A', 'Repairing', 'Repaired'),
        (62, 'Block', 'A', 'Repaired', 'Good'),
        (62, 'Block', 'B', 'Idle', 'Good'),
        (62, 'Node', 'Join', 'Idle', 'Degraded'),
        (62, 'Node', 'end', 'Idle', 'Good'),
        (62, 'Node', 'Join', 'Degraded', 'Good'),
        (62, 'System', 'State_change', 'Red', 'Green'),
        (67, 'Block', 'B', 'Good', 'Failed'),
        (67, 'Block', 'B', 'Failed', 'Sparing'),
        (67, 'Block', 'B', 'Sparing', 'RepHold'),
        (67, 'Node', 'Join', 'Good', 'Degraded'),
        (67, 'System', 'State_change', 'Green', 'Yellow'),
        (80, 'Pool', 'SP', 'Sparing', 'Sparing'),
        (80, 'Block', 'B', 'RepHold', 'Sparing'),
        (80, 'Block', 'B', 'Sparing', 'Repairing'),
        (85, 'Block', 'B', 'Repairing', 'Repaired'),
        (85, 'Block', 'B', 'Repaired', 'Good'),
        (85, 'Node', 'Join', 'Degraded', 'Good'),
        (85, 'System', 'State_change', 'Yellow', 'Green'),
        (90, 'Trigger', 'T1', 'Fired', 'Fired'),
        (90, 'Block', 'A', 'Good', 'PM_is_due'),
        (90, 'Block', 'A', 'PM_is_due', 'pmReqDefer'),
        (100, 'Block', 'A', 'pmReqDefer', 'Done'),
        (100, 'Block', 'B', 'Good', 'Done'),
        (100, 'Block', 'C', 'Good', 'Done'),
        (100, 'Block', 'D', 'Standby', 'Done'),
        (100, 'System', 'Simulation_Terminate', '--', '--'),
    ])


# ---------------------------------------------------------------------------
# 5. The reset zeroes every hierarchy's down-event count, so the hierarchy's
#    mean-time-*between* pair is read over the post-reset window alone --
# ---------------------------------------------------------------------------

def test_the_statistics_reset_zeroes_the_hierarchys_down_event_count():
    """A delayed statistics reset zeroes every hierarchy's down-event count, so
    the hierarchy's `MTBDE`/`MDT` pair is read over the post-reset window alone
    -- and with the count at zero, `MTBDE` falls back to the raw uptime and
    `MDT` has no divisor at all and prints `N/A`.

    Why: `hierDE` is the only *counter* in the reset that belongs to a
    hierarchy rather than to a node, a block or an event, and it is not a
    duration, so no re-anchoring covers it. It is incremented in exactly two
    places -- a hierarchy's Out marker node leaving `Down` or leaving `NodePM`
    (and, ported at
    `raptor/core/engine/nodes.py:692-706`) -- and Raptor 7 clears it
    outright: `hierDE[hier.seqNum]:=0`, with no status test, unlike the three
    `*MissionBool` seeds above it in the same procedure. That unconditional
    `:=0` is the claim. `hiers_by_id` is shared across runs, so without it the
    count would accumulate across a batch and `MTBDE` would be a per-batch
    figure wearing a per-run label.

    The observable is `MDT`, and it is worth being precise about why, because
    `MTBDE` is *not* the discriminator here. `_weak_hier_rows` computes
    `MTBDE = (green+yellow+blue) / hierDE` and `MDT = (red+orange) / hierDE`,
    and falls back to the raw uptime and the string `"N/A"` when `hierDE == 0`
    (`raptor/core/engine/reports.py:746-755`). This fixture's hierarchy goes
    down exactly once, so `hierDE == 1` and `uptime / 1` *is* the uptime --
    `MTBDE` would read `30.00000` whether the reset cleared the counter or not.
    `MDT` cannot be read that way: an un-reset run has ten red hours over one
    down event and prints `10.000`, while a run whose counter was cleared at 70
    has no down event left to divide by, and `"N/A"` is the only way the report
    can say so. So the literal below is a claim about the *fallback branch*,
    reached only because the counter was zeroed.

    One report detail the literal has to accommodate, because it is the
    fallback branch's own arithmetic and not a typo in the test: `MTBDE`'s slot
    is built as `("    " + real_to_str(uptime) + "  ")[:12]`, which for an
    8-character number uses all twelve columns, and the `"N/A"` is then
    appended with no separator. So the row reads `30.00000N/A` -- one field
    where the non-fallback branch gives two -- and the assertion below pins
    five fields for the reset run against the un-reset run's six.

    Setup: `_hier_spec` -- `Start -> in -> A -> J -> out -> End` inside one
    hierarchy `H1`, `A` a `Fixed` 50-hour life repaired at 60. One run of 100
    hours, `StartStats = HIER_RESET_AT = 70.0`, `weak_analysis = True` (the
    `HIERARCHY_RESULTS` table is part of the weak-link section, and
    `down_event_count` is only collected there -- `raptor/core/engine/driver.py:572-583`).
    Every distribution is `Fixed`, so the run draws nothing.

    Expected: `A` fails at 50 and is repaired at 60, taking `J`, the Out marker
    and the hierarchy `Bad -> Good` with it, so the run leaves one down event
    behind the reset; the `H1` row then reads `Ao 1.0`, `Do 1.0`, `R 1.0`,
    `MTBDE 30.00000` (the 30 post-reset hours) and `MDT N/A`. The same model
    with `StartStats = 0` reads `MTBDE 90.00000` and `MDT 10.000` -- the whole
    run's uptime over its one down event, and its ten red hours over the same.
    The two runs agree on every timeline row except the mission close, which
    the mission re-seed moves from `Mission_failure` to `Mission_success`,
    for the reason the third test gives.

    Source: Raptor 7 (`hierDE[hier.seqNum]:=0`, the line under test),
    Raptor 7 (the Out-marker's two leaving-`Down` /
    leaving-`NodePM` increments, the only writers of the counter),
    Raptor 7 (`MTBDE`/`MDT` as `(green+yellow+blue)/hierDE` and
    `(red+orange)/hierDE`), Raptor 7 (the HIERARCHY_RESULTS rows
    themselves), Raptor 7 (the per-run `ASK hier TO SetToInitValues`
    whose `hierDE := 0` this mid-run reset repeats),
    Raptor 7 (the mission close at the run end);
    raptor/core/engine/reports.py:737-759 (`_weak_hier_rows` and its
    `down_event_count == 0` fallback), raptor/core/engine/driver.py:570-583 (where
    `down_event_count` is collected, and only under weak analysis);
    docs/engine-design/hierarchy-status.adoc (a hierarchy's status is the
    aggregation of its Out marker's incoming paths, which is why `out` sits
    downstream of `A`)
    """
    run = harness.run(_hier_spec(), weak_analysis=True)

    assert run.draws == 0, (
        "every distribution is Fixed, so B2 says this run must draw nothing; "
        f"it drew {run.draws}")

    # The reset moves no element state: this is the fixture's own timeline,
    # and the un-reset run of the same model produces exactly these rows (the
    # one difference, the mission close, is asserted at the foot of the test).
    assert_timeline(run, [
        (0, 'Block', 'A', '--', 'Good'),
        (50, 'Block', 'A', 'Good', 'Failed'),
        (50, 'Block', 'A', 'Failed', 'Repairing'),
        (50, 'Node', 'J', '--', 'Bad'),
        (50, 'Hier', 'H1', '--', 'Bad'),
        (50, 'Node', 'end', '--', 'Bad'),
        (50, 'System', 'State_change', '--', 'Red'),
        (60, 'Block', 'A', 'Repairing', 'Repaired'),
        (60, 'Block', 'A', 'Repaired', 'Good'),
        (60, 'Node', 'J', 'Bad', 'Good'),
        (60, 'Hier', 'H1', 'Bad', 'Good'),
        (60, 'Node', 'end', 'Bad', 'Good'),
        (60, 'System', 'State_change', 'Red', 'Green'),
        (100, 'Block', 'A', 'Good', 'Done'),
        (100, 'System', 'Phase_change', '--', 'Mission_success'),
        (100, 'System', 'Simulation_Terminate', '--', '--'),
    ])

    # Hierarchy Ao Do R MTBDE MDT. The first three columns are the Out marker's
    # own figures (the node arm of the reset -- `NodeStatistics()` is replaced, its
    # clock re-anchors at 70, and its mission credit is re-seeded); the last two
    # are the claim. The row is read with `split()`, so it has **five** fields
    # here and six in the un-reset comparison below, and that is the report's
    # own fixed-width arithmetic rather than a slack test: the fallback branch
    # builds its `MTBDE` slot as `("    " + real_to_str(uptime) + "  ")[:12]`,
    # which for an 8-character number fills all 12 columns, and then appends the
    # bare string `"N/A"` with no separator of its own
    # (`raptor/core/engine/reports.py:753-755`). So `N/A` is glued to
    # `MTBDE`, and the field count is the shape of the fallback itself.
    assert _row(run.eor, "H1") == [
        "H1", "1.000000000", "1.000000000", "1.000000000", "30.00000N/A",
    ], _row(run.eor, "H1")

    # The same model with no reset: the counter still holds its one down event,
    # so the pair is a real mean-time-between over the whole 100 hours -- 90
    # hours of uptime and 10 hours of red, one event each -- and neither figure
    # falls back, so both columns get their full width. Note that the *only*
    # thing separating the two rows is `MTBDE`/`MDT`: nothing about the first
    # four columns distinguishes the two runs.
    unreset_spec = _hier_spec()
    unreset_spec["system"]["start_stats"] = 0.0
    unreset = harness.run(unreset_spec, weak_analysis=True)
    assert _row(unreset.eor, "H1") == [
        "H1", "0.900000000", "0.900000000", "0.000000000", "90.00000",
        "10.000",
    ], _row(unreset.eor, "H1")

    # And the two runs agree on every timeline row except the mission close,
    # which the mission re-seed moves -- the third test's own difference, seen
    # here because this model is unphased and the run end is the only mission
    # boundary in it.
    assert [tuple(e) for e in unreset.events[:-2]] == [
        tuple(e) for e in run.events[:-2]]
    assert str(unreset.events[-2]) == (
        "100 System:Phase_change -- -> Mission_failure")
    assert str(run.events[-2]) == (
        "100 System:Phase_change -- -> Mission_success")