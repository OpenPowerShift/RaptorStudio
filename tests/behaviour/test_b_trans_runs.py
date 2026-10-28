"""Transitions that exist only *across* a run boundary.

The corpus's behavioural denominator is 318 distinct
`(object_type, from -> to)` rows real Raptor wrote, and the way they are
read is stated in `tools/behaviour_inventory.py:26-30`: state is
**not** reset at a `Begin_Run_N` boundary, because Raptor does not
re-announce every object at a run start. Resetting would invent 16 `-- ->
...` rows the engine never wrote.

`Run.cross_run` in `harness.py` chains a batch exactly that way, and that
one rule is what makes the nine transitions in this file reachable at all.
Each of them is written once per run by an object whose state the previous
run left behind:

* the three `System:State_change <colour> -> Mission` rows. Kickoff writes
  the `Mission` row once per run and only when phase 1 is a mission
  (`kickoff.py:157-171`), so the `from` is whatever colour the previous
  run *ended* on. A single run has no previous run, so it can only ever
  produce `-- -> Mission`.
* the four `Mission_success` / `Mission_failure` rows on the
  `System:Phase_change` log object. A run's end closes whatever mission is
  open (`EndSimulation`'s `ChangeStatus(DDone, NonMission)`), and the next
  run's kickoff puts `mission_status` back to `Mission` **without writing
  a log row** -- `kickoff.py` sets the field directly and then hardcodes
  the one `State_change` row. So two closes in a row, with no
  `Began_a_mission` row between them, are only visible when run 2 is
  chained onto run 1.
* `System:Permanently_Down Out_of_spares -> Out_of_spares`.
  `reconfigure_system` writes the row on the exact edge where an End
  node's active-path count hits `MinGoodPathsReq-1`
  (`cascade.py:177-191`), and `Link.SetActiveLink(FALSE)` is a one-way
  ratchet *within* a run -- `run_control.py:65-73` is what sets every
  incoming link's `active` back to `True`, and it runs at kickoff. So an
  End node reaches zero active paths once per run and never twice.
* `Event Armed -> Armed`. `EndSimulation`'s event sweep puts every event
  back to `Armed` (`Success -> Armed`, the row the ladder already pins),
  and the next run's `ConductEvent` then writes `Armed -> Armed` from that
  state rather than from `Success`.

**Six of the nine need only `Fixed` distributions**, so they are exact and
consume zero randomness (`Run.draws == 0` is asserted, not hoped for). The
two mixed-outcome rows -- `Mission_failure -> Mission_success` and
`Mission_success -> Mission_failure` -- **cannot** be built from an
all-`Fixed` model, and the reason is the property itself rather than a
missing trick: with every life and every repair `Fixed`, run 2 is a
bit-for-bit replay of run 1, so two consecutive mission closes always have
the same outcome. Those two tests therefore use a single salted
`Exponential` life and a fixed seed; the batch is reproducible to the
digit, so the expectation is still a literal (rule B5), and the measured
draw count is stated in each docstring (rule B2).

**Rule B4 note.** Every model here is two or three blocks and one to two
replications, and every one of these tests measures single-digit
milliseconds. The run length is the one real dial: `runs > 1` is legitimate
here because the property *is* the run boundary (rule B4's stated
exception), and the run length is what keeps the batch small.
"""
from __future__ import annotations

from typing import Any

from . import harness
from .harness import (
    FIXED, assert_timeline, block, cross_run_view)

#: Distribution code 4. `Exponential` is the cheapest stochastic life in the
#: catalogue: one draw per run, and the draw *is* the failure time, so a
#: block with no repair either fails inside the run or never fails at all.
#: That is the whole mechanism the two mixed-outcome tests need.
EXPONENTIAL = 4


def _single(length: float, repair: float | None = None,
            **block_extra: Any) -> dict[str, Any]:
    """`Start -> A -> End`, the least topology the loader accepts."""
    return {
        "system": {"run_length": length, "num_runs": 1,
                   "termination": "TimeSim"},
        "blocks": [block("A", 10.0, repair, **block_extra)],
        "nodes": [{"name": "Start", "type": 1, "n": 0, "out": 1},
                  {"name": "End", "type": 3, "n": 1, "out": 0}],
        "links": [{"from": "Start", "to": "A"},
                  {"from": "A", "to": "End"}],
    }


def _phased_pair(length: float) -> dict[str, Any]:
    """Two `Fixed` blocks into a 1-of-2 `Join`, under two phases.

    One mechanism over three fixtures: **the run length**. The blocks are
    identical in every test below -- `A` fails at 10 and repairs at 15, `B`
    fails at 20 and repairs at 40 -- and phase 1 is a 30-hour mission with
    phase 2 a non-mission, so a run shorter than 30 hours ends *inside* the
    mission. That is what makes the run's end write a mission close, and it
    is the only reason the three `-> Mission` rows below differ at all.

    The run length is what selects the colour the run ends on, and so the
    colour the next run's kickoff is measured from:

    ==========  ==========================  ======================
    length      last colour before the end  the row run 2 writes
    ==========  ==========================  ======================
    16 h        `Green` (A repaired at 15)  `Green -> Mission`
    21 h        `Yellow` (B failed at 20)   `Yellow -> Mission`
    26 h        `Red` (A failed again, so   `Red -> Mission`
                both paths are down at 25)
    ==========  ==========================  ======================

    At 26 h the mission closes as a **failure**, at 16 h and 21 h as a
    **success**, which is the same `missionSuccess := (newColor <> RRed)`
    decision the ladder's own phased fixtures pin -- a mission that went
    `Red` at any point while it was open cannot close as a success
    (`system_status.py:319-320`).
    """
    return {
        "system": {"run_length": length, "num_runs": 1,
                   "termination": "TimeSim"},
        "blocks": [block("A", 10.0, 5.0), block("B", 20.0, 20.0)],
        "nodes": [{"name": "Start", "type": 1, "n": 0, "out": 2},
                  {"name": "Join", "type": 2, "n": 2, "out": 1, "k": 1},
                  {"name": "End", "type": 3, "n": 1, "out": 0}],
        "links": [{"from": "Start", "to": "A"}, {"from": "A", "to": "Join"},
                  {"from": "Start", "to": "B"}, {"from": "B", "to": "Join"},
                  {"from": "Join", "to": "End"}],
        "phases": [
            {"name": "P1", "mission": 1, "dist": FIXED, "params": [30.0]},
            {"name": "P2", "mission": 0, "dist": FIXED, "params": [30.0]},
        ],
    }


def _unphased_pair(length: float) -> dict[str, Any]:
    """Two `Fixed` blocks into a 1-of-2 `Join`, with **no** phase table.

    The same shape as `_phased_pair` and one mechanism fewer: an unphased
    model is in a mission for the whole run and never writes a `Mission` row
    (kickoff's `Began_a_mission` write is gated on `activePhases > 0`,
    `kickoff.py:164-166`), so the only mission rows this fixture produces are
    the closes the run's end writes. That is what makes it the small way to
    pin a *pair* of closes: at a 12-hour run length the run never reaches
    `Red`, so both closes are successes.
    """
    return {
        "system": {"run_length": length, "num_runs": 1,
                   "termination": "TimeSim"},
        "blocks": [block("A", 10.0, 5.0), block("B", 20.0, 5.0)],
        "nodes": [{"name": "Start", "type": 1, "n": 0, "out": 2},
                  {"name": "Join", "type": 2, "n": 2, "out": 1, "k": 1},
                  {"name": "End", "type": 3, "n": 1, "out": 0}],
        "links": [{"from": "Start", "to": "A"}, {"from": "A", "to": "Join"},
                  {"from": "Start", "to": "B"}, {"from": "B", "to": "Join"},
                  {"from": "Join", "to": "End"}],
    }


def _event_run(length: float) -> dict[str, Any]:
    """S1's shape with one event, `E1`, in series between `A` and the End.

    `failVal = 1.0` is the Bernoulli parameter `ConductEvent` draws *as*
    (`raptor/core/engine/events.py:70-90`), so it is an event that always
    succeeds -- and it is written `Fixed` rather than `Bernoulli` so that it
    costs no draw: `Fixed`'s draw is `param[0]` and touches no stream, where
    `bernoulli` draws a uniform even when the outcome is not in doubt
    (`raptor/core/rng.py:302-306`). This is the ladder's own S10 event
    column, unchanged.
    """
    spec = _single(length, repair=5.0)
    spec["links"][1]["to"] = "E1"
    spec["links"].append({"from": "E1", "to": "End"})
    spec["events"] = [{"name": "E1", "dist": FIXED, "failVal": 1.0}]
    return spec


# ---------------------------------------------------------------------------
# System State_change: <colour> -> Mission, measured across the run boundary
# ---------------------------------------------------------------------------

def test_a_run_that_ends_green_is_followed_by_a_mission_opened_from_green() -> None:
    """A second run reopens a mission, and the `from` is the `Green` the
    first run ended on.

    Why: kickoff writes the `Mission` row itself -- `WriteEvents(...
    "Began_a_mission")` inline in the kickoff sequence, gated on
    `activePhases > 0 AND phase[1].mission` (ported
    at `kickoff.py:157-171`) -- and it is written *after* the previous run's
    end sweep has left the system coloured. Raptor never re-announces the
    colour at `Begin_Run_N`, so the row reads from whatever the colour
    actually was. Run 1 ends `Green` (`A` repaired at 15, nothing after it),
    so run 2's row is `Green -> Mission`.

    Setup: `_phased_pair(16.0)` -- `A` `Fixed` 10/`Fixed` 5, `B` `Fixed`
    20/`Fixed` 20, a 1-of-2 `Join`, and two 30-hour phases of which the first
    is a mission; a 16-hour run, so the clock stops inside the mission and
    the run's end closes it.

    Expected: run 1 opens the mission `-- -> Mission`, `A`'s failure at 10
    takes the system `Mission -> Yellow`, its repair at 15 brings it back to
    `Green`, and the run's end closes the mission
    `Phase_change -- -> Mission_success`. Run 2 is the same run again, except
    that its kickoff row is `State_change Green -> Mission` -- the row this
    test exists for -- and its close chains onto run 1's as
    `Mission_success -> Mission_success`.

    Source: Raptor 7 (the kickoff `Began_a_mission` write),
    kickoff.py:157-171 (its port), Raptor 7 (the run-end
    `ChangeStatus(DDone, NonMission)`), Raptor 7
    (`ChangeStatus`'s `State_change` tail);
    docs/engine-design/kickoff-and-runs.adoc
    """
    result = cross_run_view(harness.run(_phased_pair(16.0), runs=2))

    assert result.draws == 0, (
        f"every distribution is Fixed, so this batch must draw nothing; it "
        f"drew {result.draws}")

    assert_timeline(result, [
        (0, 'System', 'State_change', '--', 'Mission'),
        (0, 'Block', 'A', '--', 'Good'),
        (0, 'Block', 'B', '--', 'Good'),
        (10, 'Block', 'A', 'Good', 'Failed'),
        (10, 'Block', 'A', 'Failed', 'Repairing'),
        (10, 'Node', 'Join', '--', 'Degraded'),
        (10, 'System', 'State_change', 'Mission', 'Yellow'),
        (15, 'Block', 'A', 'Repairing', 'Repaired'),
        (15, 'Block', 'A', 'Repaired', 'Good'),
        (15, 'Node', 'Join', 'Degraded', 'Good'),
        (15, 'System', 'State_change', 'Yellow', 'Green'),
        (16, 'Block', 'A', 'Good', 'Done'),
        (16, 'Block', 'B', 'Good', 'Done'),
        (16, 'System', 'Phase_change', '--', 'Mission_success'),
        (16, 'System', 'Simulation_Terminate', '--', '--'),
        (0, 'System', 'State_change', 'Green', 'Mission'),
        (0, 'Block', 'A', 'Done', 'Good'),
        (0, 'Block', 'B', 'Done', 'Good'),
        (10, 'Block', 'A', 'Good', 'Failed'),
        (10, 'Block', 'A', 'Failed', 'Repairing'),
        (10, 'Node', 'Join', 'Good', 'Degraded'),
        (10, 'System', 'State_change', 'Mission', 'Yellow'),
        (15, 'Block', 'A', 'Repairing', 'Repaired'),
        (15, 'Block', 'A', 'Repaired', 'Good'),
        (15, 'Node', 'Join', 'Degraded', 'Good'),
        (15, 'System', 'State_change', 'Yellow', 'Green'),
        (16, 'Block', 'A', 'Good', 'Done'),
        (16, 'Block', 'B', 'Good', 'Done'),
        (16, 'System', 'Phase_change', 'Mission_success', 'Mission_success'),
        (16, 'System', 'Simulation_Terminate', '--', '--'),
    ], where="run 2 opens its mission from Green")


def test_a_run_that_ends_yellow_is_followed_by_a_mission_opened_from_yellow() -> None:
    """The same fixture run for one hour longer, so `B`'s t=20 failure is
    still standing at the end: the mission opens from `Yellow`.

    Why: the kickoff `Mission` row is measured from the colour the previous
    run left, and a `1-of-2` node with one path down is `Degraded`, so the
    system is `Yellow` and not `Red`. The distinction matters because it is
    what keeps the mission's own outcome a **success**: `missionSuccess` is
    set from the colour when the mission *begins* and flipped to false only
    by a change *to* `Red` (`system_status.py:319-320`), and `Yellow` is neither.

    Setup: `_phased_pair(21.0)` -- the same blocks, nodes and phases as the
    `Green` case, run for 21 hours so `B` has failed at 20 and not yet
    repaired.

    Expected: identical to the 16-hour run up to `t=20`, then `B`'s failure
    takes the `Join` `Good -> Degraded` and the system `Green -> Yellow`,
    and the run's end closes the mission `Mission_success` and puts both
    blocks `Done` -- note the run-end sweep writes no node row, so the
    colour the next run reads is `Yellow`. Run 2's kickoff row is therefore
    `State_change Yellow -> Mission`.

    Source: Raptor 7 (kickoff's `Began_a_mission` write),
    Raptor 7 (`missionSuccess := false` only on a change to
    `Red`), kickoff.py:157-171, system_status.py:319-320;
    docs/engine-design/kickoff-and-runs.adoc
    """
    result = cross_run_view(harness.run(_phased_pair(21.0), runs=2))

    assert result.draws == 0, (
        f"every distribution is Fixed, so this batch must draw nothing; it "
        f"drew {result.draws}")

    assert_timeline(result, [
        (0, 'System', 'State_change', '--', 'Mission'),
        (0, 'Block', 'A', '--', 'Good'),
        (0, 'Block', 'B', '--', 'Good'),
        (10, 'Block', 'A', 'Good', 'Failed'),
        (10, 'Block', 'A', 'Failed', 'Repairing'),
        (10, 'Node', 'Join', '--', 'Degraded'),
        (10, 'System', 'State_change', 'Mission', 'Yellow'),
        (15, 'Block', 'A', 'Repairing', 'Repaired'),
        (15, 'Block', 'A', 'Repaired', 'Good'),
        (15, 'Node', 'Join', 'Degraded', 'Good'),
        (15, 'System', 'State_change', 'Yellow', 'Green'),
        (20, 'Block', 'B', 'Good', 'Failed'),
        (20, 'Block', 'B', 'Failed', 'Repairing'),
        (20, 'Node', 'Join', 'Good', 'Degraded'),
        (20, 'System', 'State_change', 'Green', 'Yellow'),
        (21, 'Block', 'A', 'Good', 'Done'),
        (21, 'Block', 'B', 'Repairing', 'Done'),
        (21, 'System', 'Phase_change', '--', 'Mission_success'),
        (21, 'System', 'Simulation_Terminate', '--', '--'),
        (0, 'System', 'State_change', 'Yellow', 'Mission'),
        (0, 'Block', 'A', 'Done', 'Good'),
        (0, 'Block', 'B', 'Done', 'Good'),
        (10, 'Block', 'A', 'Good', 'Failed'),
        (10, 'Block', 'A', 'Failed', 'Repairing'),
        (10, 'Node', 'Join', 'Degraded', 'Degraded'),
        (10, 'System', 'State_change', 'Mission', 'Yellow'),
        (15, 'Block', 'A', 'Repairing', 'Repaired'),
        (15, 'Block', 'A', 'Repaired', 'Good'),
        (15, 'Node', 'Join', 'Degraded', 'Good'),
        (15, 'System', 'State_change', 'Yellow', 'Green'),
        (20, 'Block', 'B', 'Good', 'Failed'),
        (20, 'Block', 'B', 'Failed', 'Repairing'),
        (20, 'Node', 'Join', 'Good', 'Degraded'),
        (20, 'System', 'State_change', 'Green', 'Yellow'),
        (21, 'Block', 'A', 'Good', 'Done'),
        (21, 'Block', 'B', 'Repairing', 'Done'),
        (21, 'System', 'Phase_change', 'Mission_success', 'Mission_success'),
        (21, 'System', 'Simulation_Terminate', '--', '--'),
    ], where="run 2 opens its mission from Yellow")


def test_a_run_that_ends_red_is_followed_by_a_mission_opened_from_red() -> None:
    """The same fixture run for 26 hours, long enough that both paths are
    down when the clock stops: the mission opens from `Red`.

    Why: `Red` is the one colour that changes the *mission* row's own
    history as well as its `from`. `A` fails a second time at 25 while `B` is
    still `Repairing`, so the `Join` goes `Bad` and the system `Red` -- and
    `ChangeStatus`'s arm for a change *to* `Red` clears the mission's own
    success flag. That is why this run's end
    closes the mission as `Mission_failure` where the 16- and 21-hour runs
    closed theirs as `Mission_success`, with nothing else changed.

    Setup: `_phased_pair(26.0)` -- the same blocks, nodes and phases, run for
    26 hours.

    Expected: as before to `t=20`; at 25 `A`'s second failure takes the
    `Join` `Degraded -> Bad`, the `End` node `-- -> Bad` and the system
    `Yellow -> Red`; at 26 the run's end closes the mission
    `Mission -> Mission_failure` and puts both blocks `Done` with no node
    row, so the colour survives as `Red`. Run 2's kickoff row is therefore
    `State_change Red -> Mission` -- and its own close chains onto run 1's
    as `Mission_failure -> Mission_failure`.

    Source: Raptor 7 (`missionSuccess := false` on a change to
    `Red`), system_status.py:313-320 (its port), Raptor 7
    (kickoff's `Began_a_mission` write), kickoff.py:157-171;
    docs/engine-design/kickoff-and-runs.adoc
    """
    result = cross_run_view(harness.run(_phased_pair(26.0), runs=2))

    assert result.draws == 0, (
        f"every distribution is Fixed, so this batch must draw nothing; it "
        f"drew {result.draws}")

    assert_timeline(result, [
        (0, 'System', 'State_change', '--', 'Mission'),
        (0, 'Block', 'A', '--', 'Good'),
        (0, 'Block', 'B', '--', 'Good'),
        (10, 'Block', 'A', 'Good', 'Failed'),
        (10, 'Block', 'A', 'Failed', 'Repairing'),
        (10, 'Node', 'Join', '--', 'Degraded'),
        (10, 'System', 'State_change', 'Mission', 'Yellow'),
        (15, 'Block', 'A', 'Repairing', 'Repaired'),
        (15, 'Block', 'A', 'Repaired', 'Good'),
        (15, 'Node', 'Join', 'Degraded', 'Good'),
        (15, 'System', 'State_change', 'Yellow', 'Green'),
        (20, 'Block', 'B', 'Good', 'Failed'),
        (20, 'Block', 'B', 'Failed', 'Repairing'),
        (20, 'Node', 'Join', 'Good', 'Degraded'),
        (20, 'System', 'State_change', 'Green', 'Yellow'),
        (25, 'Block', 'A', 'Good', 'Failed'),
        (25, 'Block', 'A', 'Failed', 'Repairing'),
        (25, 'Node', 'Join', 'Degraded', 'Bad'),
        (25, 'Node', 'end', '--', 'Bad'),
        (25, 'System', 'State_change', 'Yellow', 'Red'),
        (26, 'Block', 'A', 'Repairing', 'Done'),
        (26, 'Block', 'B', 'Repairing', 'Done'),
        (26, 'System', 'Phase_change', '--', 'Mission_failure'),
        (26, 'System', 'Simulation_Terminate', '--', '--'),
        (0, 'System', 'State_change', 'Red', 'Mission'),
        (0, 'Block', 'A', 'Done', 'Good'),
        (0, 'Block', 'B', 'Done', 'Good'),
        (10, 'Block', 'A', 'Good', 'Failed'),
        (10, 'Block', 'A', 'Failed', 'Repairing'),
        (10, 'Node', 'Join', 'Bad', 'Degraded'),
        (10, 'System', 'State_change', 'Mission', 'Yellow'),
        (15, 'Block', 'A', 'Repairing', 'Repaired'),
        (15, 'Block', 'A', 'Repaired', 'Good'),
        (15, 'Node', 'Join', 'Degraded', 'Good'),
        (15, 'System', 'State_change', 'Yellow', 'Green'),
        (20, 'Block', 'B', 'Good', 'Failed'),
        (20, 'Block', 'B', 'Failed', 'Repairing'),
        (20, 'Node', 'Join', 'Good', 'Degraded'),
        (20, 'System', 'State_change', 'Green', 'Yellow'),
        (25, 'Block', 'A', 'Good', 'Failed'),
        (25, 'Block', 'A', 'Failed', 'Repairing'),
        (25, 'Node', 'Join', 'Degraded', 'Bad'),
        (25, 'Node', 'end', 'Bad', 'Bad'),
        (25, 'System', 'State_change', 'Yellow', 'Red'),
        (26, 'Block', 'A', 'Repairing', 'Done'),
        (26, 'Block', 'B', 'Repairing', 'Done'),
        (26, 'System', 'Phase_change', 'Mission_failure', 'Mission_failure'),
        (26, 'System', 'Simulation_Terminate', '--', '--'),
    ], where="run 2 opens its mission from Red")

# ---------------------------------------------------------------------------
# System Phase_change: a mission close chained onto the previous run's close
# ---------------------------------------------------------------------------

def test_a_mission_that_is_never_red_closes_as_a_success_in_every_run() -> None:
    """Two runs, two successful mission closes, and **no** `Began_a_mission`
    row between them -- so the second close reads `Mission_success ->
    Mission_success`.

    Why: this is the row that proves the chain is the corpus's rule rather
    than a convenience. Within one run the two closes are never adjacent:
    `ConductPhaseChange` closes the open mission and then *begins* the next
    one, and the begin writes `Phase_change Mission` (`system_status.py:349-
    381`). Two closes with nothing between them need the second to be the
    first mission of the next run, and the next run's kickoff does not write
    a begin row at all -- it sets `mission_status` directly and emits only the
    hardcoded `State_change Mission` row (`kickoff.py:157-171`). So the row is
    invisible to any single-run view.

    Setup: `_unphased_pair(12.0)` -- `A` `Fixed` 10/`Fixed` 5, `B` `Fixed`
    20/`Fixed` 5, a 1-of-2 `Join`, no phase table, a 12-hour run. `A` fails at
    10 and is still `Repairing` at 12, so the run ends `Yellow`: never `Red`,
    so both closes succeed.

    Expected: `A`'s failure degrades the `Join` and colours the system
    `Yellow`; the run's end closes the mission `-- -> Mission_success`. Run 2
    replays it exactly (`Yellow -> Yellow` is the only new `State_change`,
    because the colour did not move) and its close chains onto run 1's as
    `Mission_success -> Mission_success`.

    Source: Raptor 7 (kickoff's `Began_a_mission` write, gated on
    `activePhases > 0`), kickoff.py:157-171 (its port),
    Raptor 7 (`ConductPhaseChange`), system_status.py:349-381 and
     (the two mission arms);
    docs/engine-design/events-and-phases.adoc
    """
    result = cross_run_view(harness.run(_unphased_pair(12.0), runs=2))

    assert result.draws == 0, (
        f"every distribution is Fixed, so this batch must draw nothing; it "
        f"drew {result.draws}")

    assert_timeline(result, [
        (0, 'Block', 'A', '--', 'Good'),
        (0, 'Block', 'B', '--', 'Good'),
        (10, 'Block', 'A', 'Good', 'Failed'),
        (10, 'Block', 'A', 'Failed', 'Repairing'),
        (10, 'Node', 'Join', '--', 'Degraded'),
        (10, 'System', 'State_change', '--', 'Yellow'),
        (12, 'Block', 'A', 'Repairing', 'Done'),
        (12, 'Block', 'B', 'Good', 'Done'),
        (12, 'System', 'Phase_change', '--', 'Mission_success'),
        (12, 'System', 'Simulation_Terminate', '--', '--'),
        (0, 'Block', 'A', 'Done', 'Good'),
        (0, 'Block', 'B', 'Done', 'Good'),
        (10, 'Block', 'A', 'Good', 'Failed'),
        (10, 'Block', 'A', 'Failed', 'Repairing'),
        (10, 'Node', 'Join', 'Degraded', 'Degraded'),
        (10, 'System', 'State_change', 'Yellow', 'Yellow'),
        (12, 'Block', 'A', 'Repairing', 'Done'),
        (12, 'Block', 'B', 'Good', 'Done'),
        (12, 'System', 'Phase_change', 'Mission_success', 'Mission_success'),
        (12, 'System', 'Simulation_Terminate', '--', '--'),
    ], where="two runs, two successful closes, no begin row between")


def test_a_mission_that_ends_red_closes_as_a_failure_in_every_run() -> None:
    """The mirror image: three runs, three failed closes, and the second and
    third read `Mission_failure -> Mission_failure`.

    Why: a block with `SpType = None` cannot be repaired, so its first
    failure takes it straight to `Done` (`CheckForSpare`'s arm). That takes the `End` node `Bad`, colours the
    system `Red`, and -- the row this test is about -- leaves the mission's
    success flag false, so the run's end closes it `Mission_failure`. Three
    runs are used rather than two to show the chain is not a one-off: the
    second and third closes are the *same* self-transition, twice.

    Setup: `_single(40.0, sp_type="None")` -- one block, `Fixed` 10-hour life,
    no repair distribution at all, `Start -> A -> End`, a 40-hour run.

    Expected: `A` fails at 10, goes `Failed -> Done`, the `End` node goes
    `Bad`, the system `Red`, and the End node's last active path is marked
    inactive so `Permanently_Down Out_of_spares` is written. Each run's end
    closes the mission `-- -> Mission_failure`, and chained they read
    `Mission_failure -> Mission_failure` twice.

    Source: Raptor 7 (`CheckForSpare`'s `SpType = None` arm),
    Raptor 7 (`ChangeStatus`'s mission bookkeeping),
    Raptor 7 (the run-end `ChangeStatus(DDone, NonMission)`),
    system_status.py:382-396 (the mission-end arm);
    docs/engine-design/block-lifecycle.adoc
    """
    result = cross_run_view(harness.run(_single(40.0, sp_type="None"), runs=3))

    assert result.draws == 0, (
        f"every distribution is Fixed, so this batch must draw nothing; it "
        f"drew {result.draws}")

    assert_timeline(result, [
        (0, 'Block', 'A', '--', 'Good'),
        (10, 'Block', 'A', 'Good', 'Failed'),
        (10, 'Block', 'A', 'Failed', 'Done'),
        (10, 'Node', 'end', '--', 'Bad'),
        (10, 'System', 'State_change', '--', 'Red'),
        (10, 'System', 'Permanently_Down', '--', 'Out_of_spares'),
        (40, 'Block', 'A', 'Done', 'Done'),
        (40, 'System', 'Phase_change', '--', 'Mission_failure'),
        (40, 'System', 'Simulation_Terminate', '--', '--'),
        (0, 'Block', 'A', 'Done', 'Good'),
        (10, 'Block', 'A', 'Good', 'Failed'),
        (10, 'Block', 'A', 'Failed', 'Done'),
        (10, 'Node', 'end', 'Bad', 'Bad'),
        (10, 'System', 'State_change', 'Red', 'Red'),
        (10, 'System', 'Permanently_Down', 'Out_of_spares', 'Out_of_spares'),
        (40, 'Block', 'A', 'Done', 'Done'),
        (40, 'System', 'Phase_change', 'Mission_failure', 'Mission_failure'),
        (40, 'System', 'Simulation_Terminate', '--', '--'),
        (0, 'Block', 'A', 'Done', 'Good'),
        (10, 'Block', 'A', 'Good', 'Failed'),
        (10, 'Block', 'A', 'Failed', 'Done'),
        (10, 'Node', 'end', 'Bad', 'Bad'),
        (10, 'System', 'State_change', 'Red', 'Red'),
        (10, 'System', 'Permanently_Down', 'Out_of_spares', 'Out_of_spares'),
        (40, 'Block', 'A', 'Done', 'Done'),
        (40, 'System', 'Phase_change', 'Mission_failure', 'Mission_failure'),
        (40, 'System', 'Simulation_Terminate', '--', '--'),
    ], where="three runs, three failed closes")


def test_an_end_node_that_runs_out_of_active_paths_does_so_once_per_run() -> None:
    """`Permanently_Down` writes `Out_of_spares` once per run, and the
    second run's row therefore reads `Out_of_spares -> Out_of_spares`.

    Why: `reconfigure_system` does not write the row on every permanent
    down; it writes it on **one edge only** -- the edge where the node's
    active-path count has just dropped to exactly `MinGoodPathsReq - 1`
    (`cascade.py:185-191`). With a `1-of-1` End node that edge is crossed
    once per run, and `Link.SetActiveLink(FALSE)` is a one-way ratchet
    *within* a run, so a second permanent down in the same run would find
    `prior_active == new_active` and write nothing. What makes it reappear at
    all is kickoff: `run_control.py:65-73` is the reset that sets every
    incoming link's `active` back to `True`, and it runs once per run. So the
    row is once-per-run by construction, and the corpus's self-transition is
    the only way to observe it.

    Setup: `_single(40.0, sp_type="None")` -- one block, `Fixed` 10-hour life,
    no repair distribution, `Start -> A -> End`, a 40-hour run. Two runs,
    because two is exactly what the ratchet needs.

    Expected: `A` fails at 10 and goes to `Done`, which is the event that
    drives `reconfigure_system` (`orchestrator.py:651-655`); the `End` node's
    only incoming link is marked inactive and its count reaches
    `1 - 1 = 0`, so `Permanently_Down -- -> Out_of_spares` is written. Run 2
    redoes it and the chained row is
    `Permanently_Down Out_of_spares -> Out_of_spares`.

    Source: Raptor 7 (`ReconfigureSystem`'s
    `MinGoodPathsReq - 1` edge), cascade.py:177-191 (its port),
    orchestrator.py:648-655 (`ChangeBlockState`'s `block.opStatus = Done`
    caller), Raptor 7 (`Link.SetToInitValues`' `active := TRUE`),
    run_control.py:65-73 (the per-run reset);
    docs/engine-design/node-status-and-cold-standby.adoc
    """
    result = cross_run_view(harness.run(_single(40.0, sp_type="None"), runs=2))

    assert result.draws == 0, (
        f"every distribution is Fixed, so this batch must draw nothing; it "
        f"drew {result.draws}")

    assert_timeline(result, [
        (0, 'Block', 'A', '--', 'Good'),
        (10, 'Block', 'A', 'Good', 'Failed'),
        (10, 'Block', 'A', 'Failed', 'Done'),
        (10, 'Node', 'end', '--', 'Bad'),
        (10, 'System', 'State_change', '--', 'Red'),
        (10, 'System', 'Permanently_Down', '--', 'Out_of_spares'),
        (40, 'Block', 'A', 'Done', 'Done'),
        (40, 'System', 'Phase_change', '--', 'Mission_failure'),
        (40, 'System', 'Simulation_Terminate', '--', '--'),
        (0, 'Block', 'A', 'Done', 'Good'),
        (10, 'Block', 'A', 'Good', 'Failed'),
        (10, 'Block', 'A', 'Failed', 'Done'),
        (10, 'Node', 'end', 'Bad', 'Bad'),
        (10, 'System', 'State_change', 'Red', 'Red'),
        (10, 'System', 'Permanently_Down', 'Out_of_spares', 'Out_of_spares'),
        (40, 'Block', 'A', 'Done', 'Done'),
        (40, 'System', 'Phase_change', 'Mission_failure', 'Mission_failure'),
        (40, 'System', 'Simulation_Terminate', '--', '--'),
    ], where="Out_of_spares once per run")


def test_an_event_rearmed_at_one_run_end_is_rearmed_again_at_the_next() -> None:
    """The run-end event sweep puts `E1` back to `Armed`, and the next run's
    `ConductEvent` writes `Armed -> Armed` rather than `Success -> Armed`.

    Why: `EndSimulation` closes the run by sweeping every object, and for
    events that sweep is `ChangeEventState(Armed, ..., "End_sim")`
   -- the ladder already pins its `Success ->
    Armed` row. The next run does **not** re-announce the event at kickoff, so
    the following `ConductEvent` measures its transition from that `Armed`,
    and the corpus therefore contains a self-transition on the event log
    object. No single-run view can produce it: a run that starts `Armed` and
    is swept back to `Armed` needs a *previous* run to have swept it.

    Setup: `_event_run(20.0)` -- S1's `Start -> A -> End` with `A` `Fixed`
    10/`Fixed` 5 and one event `E1` of `Fixed` 1.0 in series
    (`A -> E1 -> End`), a 20-hour run. The event always succeeds, so nothing
    downstream of it ever fails and the only event rows are the two the run
    produces.

    Expected: run 1 arms `E1` and runs it (`-- -> Armed`, `Armed -> Success`),
    `A` fails at 10 and repairs at 15, and the run's end sweeps the event
    back (`Success -> Armed`). Run 2's arming row is then `Armed -> Armed` --
    the row this test exists for -- followed by the same `Armed -> Success`
    and the same end-of-run sweep.

    Source: Raptor 7 (`EndSimulation`'s event sweep),
    Raptor 7 (`ConductEvent`'s initial `Armed -> Success`),
    raptor/core/engine/events.py:70-90 (`failVal` as the Bernoulli
    parameter), raptor/core/rng.py:302-306 (`bernoulli` draws a uniform even
    at `p = 1.0`, which is why the outcome column is `Fixed` here);
    docs/engine-design/events-and-phases.adoc
    """
    result = cross_run_view(harness.run(_event_run(20.0), runs=2))

    assert result.draws == 0, (
        f"every distribution is Fixed, so this batch must draw nothing; it "
        f"drew {result.draws}")

    assert_timeline(result, [
        (0, 'Block', 'A', '--', 'Good'),
        (0, 'Event', 'E1', '--', 'Armed'),
        (0, 'Event', 'E1', 'Armed', 'Success'),
        (10, 'Block', 'A', 'Good', 'Failed'),
        (10, 'Block', 'A', 'Failed', 'Repairing'),
        (10, 'Node', 'end', '--', 'Bad'),
        (10, 'System', 'State_change', '--', 'Red'),
        (15, 'Block', 'A', 'Repairing', 'Repaired'),
        (15, 'Block', 'A', 'Repaired', 'Good'),
        (15, 'Node', 'end', 'Bad', 'Good'),
        (15, 'System', 'State_change', 'Red', 'Green'),
        (20, 'Block', 'A', 'Good', 'Done'),
        (20, 'Event', 'E1', 'Success', 'Armed'),
        (20, 'System', 'Phase_change', '--', 'Mission_failure'),
        (20, 'System', 'Simulation_Terminate', '--', '--'),
        (0, 'Block', 'A', 'Done', 'Good'),
        (0, 'Event', 'E1', 'Armed', 'Armed'),
        (0, 'Event', 'E1', 'Armed', 'Success'),
        (10, 'Block', 'A', 'Good', 'Failed'),
        (10, 'Block', 'A', 'Failed', 'Repairing'),
        (10, 'Node', 'end', 'Good', 'Bad'),
        (10, 'System', 'State_change', 'Green', 'Red'),
        (15, 'Block', 'A', 'Repairing', 'Repaired'),
        (15, 'Block', 'A', 'Repaired', 'Good'),
        (15, 'Node', 'end', 'Bad', 'Good'),
        (15, 'System', 'State_change', 'Red', 'Green'),
        (20, 'Block', 'A', 'Good', 'Done'),
        (20, 'Event', 'E1', 'Success', 'Armed'),
        (20, 'System', 'Phase_change', 'Mission_failure', 'Mission_failure'),
        (20, 'System', 'Simulation_Terminate', '--', '--'),
    ], where="event rearmed by the previous run's end sweep")


# ---------------------------------------------------------------------------
# The two rows an all-Fixed model cannot produce
# ---------------------------------------------------------------------------

def test_a_mission_that_fails_in_one_run_and_succeeds_in_the_next_chains_both() -> None:
    """Run 1's close is a failure and run 2's is a success, so the chained
    row is `Mission_failure -> Mission_success`.

    Why: **this row cannot be built from an all-`Fixed` model, and the reason
    is the property rather than a missing trick.** With every life and every
    repair `Fixed`, run 2 is a bit-for-bit replay of run 1 -- every stream
    draw is `param[0]` and consumes nothing -- so two consecutive mission
    closes always carry the same outcome and the chain can only ever be
    `Mission_success -> Mission_success` or
    `Mission_failure -> Mission_failure`. A run-varying outcome needs one
    stochastic draw, and exactly one is enough: a block with an
    `Exponential` life and no repair either fails inside its run or never
    fails at all, which is precisely the two outcomes being paired.

    Setup: `_single(100.0, fail_dist=EXPONENTIAL, fail_params=[40.0],
    sp_type="None")` -- one block, `Exponential(40)`, no repair,
    `Start -> A -> End`, a 100-hour run, two replications, `seed=4`. Rule B2:
    this is the one stochastic life in the file, so the batch draws
    **2** (one per run), asserted rather than hoped for. The seed is fixed,
    so the batch is reproducible to the digit and the expectation below is
    still a literal.

    Expected: run 1 draws 13.075260 -- `A` fails, cannot be repaired, so it goes
    to `Done`, the `End` node goes `Bad`, the system goes `Red`, and the run's
    end closes the mission `Mission_failure`. Run 2's draw is larger than the
    whole 100-hour run, so `A` never fails, the system stays `Green`, and the
    run's end closes the mission `Mission_success`. Chained: one
    `Phase_change` object, two closes, and the row between them is
    `Mission_failure -> Mission_success`.

    Source: Raptor 7 (`ChangeStatus`'s mission bookkeeping and
    the `missionSuccess := (newColor <> RRed)` decision), Raptor 7
    (`CheckForSpare`'s `SpType = None` arm), Raptor 7 (the run-end
    `ChangeStatus(DDone, NonMission)`), Raptor 7 (the per-run
    `GetStartCond`/`SetToInitValues` loop that re-draws each life per run),
    system_status.py:314-320 (its port);
    docs/engine-design/events-and-phases.adoc
    """
    spec = _single(100.0, sp_type="None")
    spec["blocks"][0]["fail_dist"] = EXPONENTIAL
    spec["blocks"][0]["fail_params"] = [40.0]
    result = cross_run_view(harness.run(spec, runs=2, seed=4))

    assert result.draws == 2, (
        f"one Exponential draw per run, two runs: expected 2, drew "
        f"{result.draws}")

    assert_timeline(result, [
        (0, 'Block', 'A', '--', 'Good'),
        (13.075260386216844, 'Block', 'A', 'Good', 'Failed'),
        (13.075260386216844, 'Block', 'A', 'Failed', 'Done'),
        (13.075260386216844, 'Node', 'end', '--', 'Bad'),
        (13.075260386216844, 'System', 'State_change', '--', 'Red'),
        (13.075260386216844, 'System', 'Permanently_Down', '--', 'Out_of_spares'),
        (100, 'Block', 'A', 'Done', 'Done'),
        (100, 'System', 'Phase_change', '--', 'Mission_failure'),
        (100, 'System', 'Simulation_Terminate', '--', '--'),
        (0, 'Block', 'A', 'Done', 'Good'),
        (100, 'Block', 'A', 'Good', 'Done'),
        (100, 'System', 'Phase_change', 'Mission_failure', 'Mission_success'),
        (100, 'System', 'Simulation_Terminate', '--', '--'),
    ], where="run 1 fails the mission, run 2 succeeds it")


def test_a_mission_that_succeeds_in_one_run_and_fails_in_the_next_chains_both() -> None:
    """The same fixture with the two runs' draws the other way round, so the
    chained row is `Mission_success -> Mission_failure`.

    Why: the same property read in the other direction, and worth pinning on
    its own because the two orderings are *different claims*. The success
    flag is not a colour sample: `ChangeStatus` sets it when the mission
    **begins** from the colour then current and clears it on the first change
    **to** `Red` while the mission is open. So
    "the run ended Green" and "the mission closed as a success" are not the
    same statement, and a chain that goes success-then-failure is the only
    place both orders are exercised against each other.

    Setup: as the previous test -- `_single(100.0, ...)` with `A` on
    `Exponential(40)` and no repair, a 100-hour run, two replications -- with
    `seed=5` instead of `4`. Same 2 draws (rule B2), same reproducibility.

    Expected: run 1's draw is larger than the run, so `A` never fails, the
    system is `Green` throughout and the run's end closes the mission
    `-- -> Mission_success`. Run 2 draws 9.103754, `A` fails and cannot be
    repaired, the system goes `Red`, and the run's end closes the mission
    `Mission_success -> Mission_failure`. Note run 1 writes **no**
    `State_change` row at all: nothing about the system moved, so there is no
    colour row for the chain to walk -- which is exactly why the close row is
    the only evidence that run 2 inherited a mission at all.

    Source: Raptor 7 (the success flag is cleared by a change
    *to* `Red`), Raptor 7 (it is set from the colour at the
    mission's beginning), system_status.py:313-320 (its port),
    Raptor 7 (the run-end `ChangeStatus(DDone, NonMission)`),
    Raptor 7 (the per-run life re-draw);
    docs/engine-design/events-and-phases.adoc
    """
    spec = _single(100.0, sp_type="None")
    spec["blocks"][0]["fail_dist"] = EXPONENTIAL
    spec["blocks"][0]["fail_params"] = [40.0]
    result = cross_run_view(harness.run(spec, runs=2, seed=5))

    assert result.draws == 2, (
        f"one Exponential draw per run, two runs: expected 2, drew "
        f"{result.draws}")

    assert_timeline(result, [
        (0, 'Block', 'A', '--', 'Good'),
        (100, 'Block', 'A', 'Good', 'Done'),
        (100, 'System', 'Phase_change', '--', 'Mission_success'),
        (100, 'System', 'Simulation_Terminate', '--', '--'),
        (0, 'Block', 'A', 'Done', 'Good'),
        (9.103753596985184, 'Block', 'A', 'Good', 'Failed'),
        (9.103753596985184, 'Block', 'A', 'Failed', 'Done'),
        (9.103753596985184, 'Node', 'end', '--', 'Bad'),
        (9.103753596985184, 'System', 'State_change', '--', 'Red'),
        (9.103753596985184, 'System', 'Permanently_Down', '--', 'Out_of_spares'),
        (100, 'Block', 'A', 'Done', 'Done'),
        (100, 'System', 'Phase_change', 'Mission_success', 'Mission_failure'),
        (100, 'System', 'Simulation_Terminate', '--', '--'),
    ], where="run 1 succeeds the mission, run 2 fails it")
