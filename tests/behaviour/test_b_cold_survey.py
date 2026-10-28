"""The cold-standby **switch survey**: which links the group switches back, how
many at once, and what it does about the ones it cannot.

`run_cold_logic`'s second half (`raptor/core/engine/cold_standby.py:452-753`)
is the part of the K-of-N group-switching engine that decides *movement*, as
opposed to the part that only aggregates. It runs one survey per `CheckColds`
drain, in three steps:

1. **Bucket** the group's links by `switch_status` and derive
   `runs_required` from `k`, `k*` and the ready count.
2. **If there are not enough ready paths** (`ready_paths < runs_required`),
   work out how many switches the group still wants
   (`switches_required = k_max - ready_paths`) and either cancel the
   excess in-flight switches or *start* the missing ones
   .
3. **If there are** , keep the first `runs_required` links in
   cold-priority order, demote the rest (`Too_many_on`) and promote a standby
   one that has room (`Prior=Standby`) -- plus the one cancellation the
   enough-paths branch owns of its own: an in-flight switch this survey no
   longer wants.

**Why every fixture here writes `AutoTime` or `ManTime` as a positive
number.** Every existing behaviour fixture carries `AProb = 1.0, AutoTime =
0.0, ManTime = -1.0` (`spine.py:422-425`, `test_b_node_standby.py`'s `_cold`),
which is precisely the combination that resolves `switch_status` to `"ready"`
with no draw and therefore **never starts a switch**: `set_switch_time`'s
`should_auto` branch sets `"auto"` only `if switch_time > 0.0`
(`cold_standby.py:116-117`). So `spine(8)` and everything built on it exercise
the *kickoff redistribution* and the *promote/demote* arms and nothing else --
`switches_required`, `num_switching`, both `spawn_switch` loops, the `"none"`
initiation arm and both `interrupt_link` sites had no behaviour test at all.
Writing `AutoTime = 6.0` is what turns the same two-block group into one with
an asynchronous switch in flight, and it costs nothing: `AProb = 1.0`
short-circuits the `UniformReal(0,1)` roll (`cold_standby.py:103-104`), so
every run here still draws **zero** times (rule B2, asserted in each test).

**The two interruptions are both about a switch that is already under way,
and they are the two hardest lines in the group** -- neither has a real Raptor
capture for a fixture this small, so both were first settled by dedicated
probes (the internal engine ledger `CSExcessSwitchProbe` for the
excess one `CSSwitchRaceProbe` for the `k*` one). `interrupt_link`
is a caller-supplied callback (`cascade_cold.py:73` passes
`self._interrupt_switch`), so for those two the honest expectation is on the
*callback's recorded calls*, not on a logged transition: the log shows the
consequence (the link's `_dsw` tag goes back to `_s` in the node's comment
string) but never names the link that was cancelled. `_recorded_switch_interrupts`
is that record -- a delegating wrapper around the real implementation, so the
run is unchanged and the assertion is a literal list of
`(link number, source block id, cold priority)`.

**Every model here is `Fixed` (distribution code 19)**, one replication, and at
most five blocks and three nodes (rule B4); the slowest is a few milliseconds.
Every cold candidate is a **fresh** block that appears in no other group, which
is what `model_checks.refuse_unrunnable` demands: a candidate that depends on
another path into the same cold node is refused at build time
(`model_checks.py:101-110`).

Source: Raptor 7 (the "not enough ready paths, initiate a new
switch" branch) (its "too many switching" interrupt),
 (`TELL link TO PerformSwitch`) (the manual initiation
loop) (the merged single pass when `checkAutosFirst` is off),
 (the "enough paths" branch) (`Prior=Standby`),
 (`Too_many_on`) (`cmc 6/19/06 Do not interrupt if kMax
is not met`) (`Link.SetSwitchTime`'s `AProb`/`ManTime`
resolution) (`PrepForSwitch`) (`PerformSwitch`);
raptor/core/engine/cold_standby.py:103-127,
 (the ports), raptor/core/engine/cascade_cold.py:48-59
(`spawn_switch` and `_interrupt_switch`),
raptor/core/engine/nodes.py:441-447 (`SetSwitchTime` on any cold link reaching
`LinkStandby`); the internal engine ledger (`SetSwitchTime`'s three-way
resolution) (the two probe captures),
docs/engine-design/node-status-and-cold-standby.adoc ("Structure": the
survey / enough-paths / initiate-switch split, and "State it owns": the
`switch_status` vocabulary)
"""
from __future__ import annotations

import contextlib
from collections.abc import Iterator
from typing import Any

from raptor.core.engine.interrupts import InterruptReason
from raptor.core.engine.orchestrator import _Cascade

from .harness import FIXED, assert_timeline, run

#: How long every switch in this file takes, in hours. `AutoTime = 6.0` on a
#: cold link is what resolves its `switch_status` to `"auto"`
#: (`cold_standby.py:116-117`) instead of `"ready"`, so the survey has
#: something to start; `ManTime = 4.0` does the same for the manual
#: resolution. Both are whole numbers so the switch completes at
#: a literal instant and the timeline needs no epsilon.
SWITCH_TIME = 6.0

#: The manual counterpart of `SWITCH_TIME`, deliberately *shorter*, so the run
#: shows which of the two switches finished first and so the two completions
#: cannot be mistaken for one.
MANUAL_TIME = 4.0

#: When the primary paths fail. Late enough that kickoff's redistribution is
#: unambiguously over, early enough that every switch and every repair lands
#: well inside the run.
FAIL_AT = 20.0


def _block(name: str, fail: float, repair: float) -> dict[str, Any]:
    """One block with a `Fixed` life of *fail* and a `Fixed` repair of
    *repair*: it fails at *fail* and is back at *fail + repair*."""
    return {"name": name, "fail_dist": FIXED, "fail_params": [fail],
            "repair_dist": FIXED, "repair_params": [repair]}


def _cold(prio: int, *, a_prob: float = 1.0, auto_time: float = 0.0,
          man_time: float = -1.0) -> dict[str, Any]:
    """The switching attributes on one link into a cold node.

    `AProb = 1.0` is the deterministic automatic branch of `set_switch_time`
    (`cold_standby.py:103-104`), so no `UniformReal(0,1)` is drawn.
    `AutoTime = 0.0` resolves the status to `"ready"` and means "switch
    instantly, no timer", which is what every pre-existing cold fixture wants;
    a positive `AutoTime` resolves it to `"auto"`
    (`cold_standby.py:116-117`) and is what makes the survey start a switch.
    `ManTime = -1.0` says "no manual fallback" and resolves an unsuccessful
    automatic roll to `"none"` (`cold_standby.py:119-121`); a positive
    `ManTime` resolves it to `"manual"`, and `ManTime = 0.0` is
    the odd third resolution -- `"ready"`, without ever consulting `AutoTime`
    .
    """
    return {"cold_pri": prio, "a_prob": a_prob, "auto_time": auto_time,
            "man_time": man_time}


def _cold_node(name: str, n: int, k: int, *, k_star: int | None = None,
               autos: bool = False) -> dict[str, Any]:
    """A cold-standby k-of-n node.

    `cold = 1` is what makes the node's status `run_cold_logic`'s rather than
    `determine_node_status`'s aggregation, and `k_star` is k*, the upper bound
    `runs_required` and `switches_required` are computed against
    (`cold_standby.py:433`). `pr = 0` is Raptor's "no priority return"
    flag. `autos = True` writes the node's own `checkAutosFirst` flag, which is
    the whole difference between the two initiation loops: off is one merged
    pass, on is automatics first and manual second.
    """
    return {"name": name, "type": 2, "n": n, "out": 1, "k": k, "cold": 1,
            "k_star": k if k_star is None else k_star, "pr": 0,
            "autos": 1 if autos else 0}


def _group(members: list[tuple[str, int, float, dict[str, Any]]],
           *, k: int, k_star: int | None = None, autos: bool = False,
           length: float = 30.0, repair: float = 100.0) -> dict[str, Any]:
    """One cold group of blocks, wired `Start -> block -> Cold -> End`.

    `members` is `(block name, ColdPri, life, link attributes)` in the order
    the links are written, which is also the order the builder numbers them.
    Every member is a **fresh** block that appears in no other group, which is
    what `refuse_unrunnable` requires of a cold candidate
    (`model_checks.py:101-110`). `repair` is the repair time of every member
    that is not given its own, and defaults past the run's end so a member that
    fails stays down unless a test asks otherwise.
    """
    blocks: list[dict[str, Any]] = []
    links: list[dict[str, Any]] = []
    for name, _, life, attrs in members:
        this_repair = attrs.pop("repair", repair)
        blocks.append(_block(name, life, this_repair))
        links.append({"from": "Start", "to": name})
    for name, prio, _, attrs in members:
        links.append({"from": name, "to": "Cold", **_cold(prio, **attrs)})
    links.append({"from": "Cold", "to": "End"})
    return {
        "system": {"run_length": length, "num_runs": 1,
                   "termination": "TimeSim"},
        "blocks": blocks,
        "nodes": [
            {"name": "Start", "type": 1, "n": 0, "out": len(members)},
            {"name": "End", "type": 3, "n": 1, "out": 0},
            _cold_node("Cold", len(members), k, k_star=k_star, autos=autos),
        ],
        "links": links,
    }


@contextlib.contextmanager
def _recorded_switch_interrupts() -> Iterator[list[tuple[int, int, int]]]:
    """Every `interrupt_link` call the run makes, as
    `(link number, source id, cold priority)`.

    `interrupt_link` is an **injected** callback: `run_cold_logic` receives it
    as a keyword (`cold_standby.py:294`) and the cascade supplies
    `self._interrupt_switch` (`cascade_cold.py:73`). Nothing in the event log
    names the link a cancellation was issued against -- the only trace is the
    node's own comment string, where the link's tag goes back from `_dsw<n>`
    to `_s<n>` -- so for a claim about *which* link the survey cancelled, the
    recorded calls are the honest expectation.

    The wrapper **delegates**: it appends and then calls the real
    `_Cascade._interrupt_switch` with the same arguments, so the run's output
    is byte-identical to an unspied one (the `reason` default is the engine's
    own, and the reconfigure route's explicit
    `InterruptReason.PERFORM_SWITCH_RECONFIGURE` is passed straight through).
    Both routes reach this method, which is why each test's expectation is the
    *whole* recorded list: a fixture that also tripped the reconfigure route
    would fail rather than quietly drop an entry.

    The triple identifies a link without the model: the log's own
    `_dsw8`/`_s8` tags are link numbers, and `ColdPri` is unique within a
    group, so `(8, 4, 40)` reads as "link 8, sourced by block 4, `ColdPri = 40`
    -- `D`'s".
    """
    recorded: list[tuple[int, int, int]] = []
    real = _Cascade._interrupt_switch

    def recording(self, link, reason: str = InterruptReason.PERFORM_SWITCH):
        recorded.append((link.object_num, link.connect_from_id,
                         link.cold_priority))
        return real(self, link, reason)

    _Cascade._interrupt_switch = recording
    try:
        yield recorded
    finally:
        _Cascade._interrupt_switch = real


def test_a_cold_group_starts_exactly_the_one_switch_it_needs_and_leaves_the_other_candidate_waiting() -> None:
    """A `1-of-3` cold group whose primary fails starts **one** switch, not the
    two its standby candidates could have supplied, and the third candidate
    never comes back.

    Why: this is the counter. The survey buckets the group's links by
    `switch_status` (`cold_standby.py:452-466`), derives `runs_required` from
    `k`, `k*` and the ready count, and -- because
    `ready_paths = 0 < runs_required = 1` -- works out
    `switches_required = k_max - ready_paths = 1`. The merged
    initiation loop then walks `node.cold_links` in cold-priority order and
    breaks **at the top of each iteration** once `switching_paths` has reached
    `switches_required` : it preps and spawns on the first
    candidate, increments, and the second iteration takes the `break`. The
    second candidate's `switch_status` is still `"auto"` and never reaches
    `"ready"`, so it is not promoted.

    Setup: a `1-of-3` cold node `Cold` (`cold = 1`, `k = k* = 1`) fed by `A`
    (primary, `ColdPri = 10`), `B` (`ColdPri = 20`) and `C` (`ColdPri = 30`).
    Every link carries `AProb = 1.0` and `AutoTime = 6.0`, so at kickoff's
    `Too_many_on` demotion both standbys resolve to `"auto"` rather than
    `"ready"`. `A` fails at 20 and its repair runs past the run's end. Three
    blocks, three nodes, one replication, every distribution `Fixed`.

    Expected: kickoff demotes `B` and `C` (`runs_required = 1` out of two ready
    paths) and logs the group `Good`. At 20 `A` fails and the group goes `Bad`
    with no ready path: exactly one switch starts, so exactly one of the two
    standbys is mid-switch (`_dsw5_s6_df4` for the group -- `B`'s link is in
    flight, `C`'s is still plain standby -- in the group's own comment string,
    which `assert_timeline` does not read).
    At 26 `B`'s six-hour switch completes and the group promotes it
    (`Prior=Standby`, `Degraded`); `C` is still `"auto"` with nobody waiting on
    it and goes to `Done` from `Standby` without ever having run again. That
    asymmetry is the claim: one switch, not two.

    Source: Raptor 7 (the "not enough ready paths, initiate a new
    switch" branch) (`TELL link TO PerformSwitch`)
    (`IF readyPaths >= RunsRequired`) (`Too_many_on`)
    (`Prior=Standby`); raptor/core/engine/cold_standby.py:468-473
    (`runs_required`) (`switches_required`) (the merged pass,
    its `break` and its `switching_paths += 1`),
    raptor/core/engine/cascade_cold.py:48-59 (`spawn_switch`)
    (`kickoff_cold_logic`), raptor/core/engine/nodes.py:441-447
    (`SetSwitchTime` on a link reaching `LinkStandby`);
    docs/engine-design/node-status-and-cold-standby.adoc
    """
    result = run(_group(
        [("A", 10, FAIL_AT, {}),
         ("B", 20, 500.0, {"auto_time": SWITCH_TIME}),
         ("C", 30, 500.0, {"auto_time": SWITCH_TIME})],
        k=1))

    assert result.draws == 0, (
        "every distribution is Fixed and AProb = 1.0 short-circuits "
        f"set_switch_time's roll, so the run must draw nothing; it drew "
        f"{result.draws}")

    assert_timeline(result, [
        (0, 'Block', 'A', '--', 'Good'),
        (0, 'Block', 'B', '--', 'Good'),
        (0, 'Block', 'C', '--', 'Good'),
        # Kickoff: three ready paths, RunsRequired = 1, so two are demoted.
        (0, 'Block', 'B', 'Good', 'Standby'),
        (0, 'Block', 'C', 'Good', 'Standby'),
        (0, 'Node', 'Cold', '--', 'Good'),
        (20, 'Block', 'A', 'Good', 'Failed'),
        (20, 'Block', 'A', 'Failed', 'Repairing'),
        (20, 'Node', 'Cold', 'Good', 'Bad'),
        (20, 'Node', 'end', '--', 'Bad'),
        (20, 'System', 'State_change', '--', 'Red'),
        # The one switch: B completes it at 20 + 6 and is promoted.
        (26, 'Block', 'B', 'Standby', 'Good'),
        (26, 'Node', 'Cold', 'Bad', 'Degraded'),
        (26, 'Node', 'end', 'Bad', 'Good'),
        (26, 'System', 'State_change', 'Red', 'Yellow'),
        (30, 'Block', 'A', 'Repairing', 'Done'),
        (30, 'Block', 'B', 'Good', 'Done'),
        # C never came back: its switch was never started, so it is still
        # `"auto"` and still Standby when the run ends.
        (30, 'Block', 'C', 'Standby', 'Done'),
        (30, 'System', 'Phase_change', '--', 'Mission_failure'),
        (30, 'System', 'Simulation_Terminate', '--', '--'),
    ], where="one switch, not two")


def test_an_excess_switch_already_in_flight_is_interrupted_rather_than_waited_for() -> None:
    """When a `2-of-4` cold group has **two** switches in flight and one
    primary comes back, the survey cancels the one it no longer needs instead
    of waiting for it.

    Why: this is the one line in the group the behaviour suite had never
    reached. The initiation branch's first arm is `IF switchingPaths >
    switchesRequired` (`cold_standby.py:483`): more switches are in flight
    than the group wants, so the survey re-walks `cold_links` and interrupts
    every in-progress link **beyond the first `switches_required`** .
    Source counts in-progress `LinkStandby` links in its own `numSwitching`
    scan and issues `Interrupt(link, "PerformSwitch")` for each one past the
    limit, which the link's own `ON INTERRUPT` handler turns into a fresh
    `SetSwitchTime` (`cold_standby.py:902-913`).

    The arithmetic that makes `2 > 1` reachable is the whole design here. The
    survey only enters the branch when `ready_paths < runs_required`, and
    `runs_required` is `k` unless the ready count is already `>= k*` -- so with
    `k = k* = 2` the branch runs only at `ready_paths = 0`, where
    `switches_required = 2`. Both primaries then failing together buys two
    switches, and the **only** way to shrink `switches_required` to 1 without
    completing either switch is a ready path reappearing: one primary's repair
    lands. That is what `CSSwitchRaceProbe` did in real Raptor with a 0.2-hour
    repair against a 0.5-hour switch.

    Setup: a `2-of-4` cold node `Cold` (`k = k* = 2`) fed by `A` (`ColdPri =
    10`) and `B` (`20`) -- both primaries, both with `AutoTime = 6.0` -- and
    `C` (`30`) and `D` (`40`), the two standbys, also `AutoTime = 6.0`. `A`
    and `B` both fail at 20; `A` repairs in 5 (t=25), `B` repairs past the run
    end. Four blocks, three nodes, one replication, all `Fixed`.

    Expected: kickoff demotes `C` and `D`. At 20 both primaries fail, so
    `ready_paths = 0` and both standbys start switching (`_dsw7_dsw8`). At 25
    `A` repairs: `ready_paths = 1` still leaves the group short of its k, so
    `switches_required` drops to `2 - 1 = 1` and `switching_paths = 2 > 1`.
    The scan keeps the first in-progress link in `cold_links` order (`C`) and
    cancels the second (`D`), which is why the group writes **two** lines at
    25 -- the second with `D`'s tag back at `_s8`. `D` therefore never
    completes its switch and goes to `Done` from `Standby`, while `C` does
    complete at 26 and is promoted. The recorded callback calls pin *which*
    link was cancelled, which the log's state column cannot say.

    Source: Raptor 7 ("too many switching": the `numSwitching`
    scan and `Interrupt(link, "PerformSwitch")`),
    raptor/core/engine/cold_standby.py:482-498 (the port: `switches_required`,
    the `switching_paths > switches_required` gate and the interrupt loop),
     (`PerformSwitch`'s `ON INTERRUPT` redraw),
    raptor/core/engine/cascade_cold.py:73 (`interrupt_link` and
    `_interrupt_switch`);
    the internal engine ledger (`CSExcessSwitchProbe`, the real
    capture of this arm: "k=2, k*=2; both primaries fail, both standbys start
    switching, one primary returns at +0.2, and the excess switch is
    interrupted") (`CSSwitchRaceProbe`);
    docs/engine-design/node-status-and-cold-standby.adoc
    """
    with _recorded_switch_interrupts() as interrupted:
        result = run(_group(
            [("A", 10, FAIL_AT, {"repair": 5.0}),
             ("B", 20, FAIL_AT, {}),
             ("C", 30, 500.0, {"auto_time": SWITCH_TIME}),
             ("D", 40, 500.0, {"auto_time": SWITCH_TIME})],
            k=2))

    assert result.draws == 0, (
        "every distribution is Fixed and AProb = 1.0 short-circuits "
        f"set_switch_time's roll, so the run must draw nothing; it drew "
        f"{result.draws}")

    # One cancellation, and it is D's link: link 8 (the log's own `_dsw8` /
    # `_s8`), sourced by block 4, `ColdPri = 40`. C's link 7 is the first
    # in-progress link in cold_links order, so the scan counts it and stops.
    assert interrupted == [(8, 4, 40)], (
        "the survey should have interrupted exactly one switch -- D's -- and "
        f"left C's alone; it recorded {interrupted}")

    assert_timeline(result, [
        (0, 'Block', 'A', '--', 'Good'),
        (0, 'Block', 'B', '--', 'Good'),
        (0, 'Block', 'C', '--', 'Good'),
        (0, 'Block', 'D', '--', 'Good'),
        # Kickoff: four ready paths, RunsRequired = 2, so two are demoted.
        (0, 'Block', 'C', 'Good', 'Standby'),
        (0, 'Block', 'D', 'Good', 'Standby'),
        (0, 'Node', 'Cold', '--', 'Good'),
        (20, 'Block', 'A', 'Good', 'Failed'),
        (20, 'Block', 'B', 'Good', 'Failed'),
        (20, 'Block', 'A', 'Failed', 'Repairing'),
        (20, 'Node', 'Cold', 'Good', 'Bad'),
        (20, 'Node', 'end', '--', 'Bad'),
        (20, 'System', 'State_change', '--', 'Red'),
        (20, 'Block', 'B', 'Failed', 'Repairing'),
        # Both standbys are now mid-switch: ready = 0, switchesRequired = 2.
        (20, 'Node', 'Cold', 'Bad', 'Bad'),
        # A repairs, so ready = 1 and switchesRequired drops to 1 against
        # switchingPaths = 2. The scan interrupts D's switch ...
        (25, 'Block', 'A', 'Repairing', 'Repaired'),
        (25, 'Block', 'A', 'Repaired', 'Good'),
        (25, 'Node', 'Cold', 'Bad', 'Bad'),
        # ... and re-surveys immediately, which is the group's second line at
        # the same instant (its link tags went `_dsw8` -> `_s8`).
        (25, 'Node', 'Cold', 'Bad', 'Bad'),
        (25, 'System', 'Red_change', '--', 'Red'),
        # C's switch still completes at 20 + 6 and C is promoted.
        (26, 'Block', 'C', 'Standby', 'Good'),
        (26, 'Node', 'Cold', 'Bad', 'Degraded'),
        (26, 'Node', 'end', 'Bad', 'Good'),
        (26, 'System', 'State_change', 'Red', 'Yellow'),
        (30, 'Block', 'A', 'Good', 'Done'),
        (30, 'Block', 'B', 'Repairing', 'Done'),
        (30, 'Block', 'C', 'Good', 'Done'),
        # D's switch was cancelled, so D never came back.
        (30, 'Block', 'D', 'Standby', 'Done'),
        (30, 'System', 'Phase_change', '--', 'Mission_failure'),
        (30, 'System', 'Simulation_Terminate', '--', '--'),
    ], where="excess switch interrupted")


def test_the_survey_switches_the_candidates_earliest_in_cold_priority_order_and_leaves_the_last_one() -> None:
    """A `2-of-5` cold group with three standby candidates switches the **first
    two** of them and leaves the third, so a link's position in
    `node.cold_links` is what decides whether it is switched.

    Why: `IdAndSortColds` (`cold_standby.py:147-176`)
    makes `node.cold_links` the candidate order, and the initiation loop walks
    exactly that list, breaking at the top of each iteration once
    `switching_paths` has reached `switches_required`
    (`cold_standby.py:505-507`). So the *set* of links switched is a
    consequence of the order: with three `"auto"` candidates and
    `switches_required = 2`, the survey preps and spawns on the two earliest
    and never reaches the third. A survey that ignored the order -- or that
    re-counted after each spawn without breaking -- would bring a different
    pair back, which is what makes the pair this test names a statement about
    order rather than about counting.

    This is also the only way to see the counter hold at `k > 1`: every
    existing cold fixture is `1-of-2`, where `switches_required` is 1 and the
    `break` is indistinguishable from running out of candidates.

    Setup: a `2-of-5` cold node `Cold` (`k = k* = 2`) fed by primaries `A`
    (`ColdPri = 10`) and `B` (`20`), which both fail at 20, and standbys `C`
    (`30`), `D` (`40`) and `E` (`50`), all three with `AutoTime = 6.0`. Five
    blocks, three nodes, one replication, all `Fixed`. Five blocks rather than
    four because a third standby is the minimum that makes "the last one" a
    claim about position: with two standbys and two switches the pair is the
    whole list.

    Expected: kickoff demotes `C`, `D` and `E` (four ready paths,
    `RunsRequired = 2`). Both primaries fail at 20, so `ready_paths = 0` and
    `switches_required = 2`; the group's two surveys at that instant show
    `C` mid-switch first and then `C` and `D` both mid-switch, with `E`'s tag
    still plain `_s10` throughout. At 26 `C` and `D` complete together and are
    both promoted, and `E` is still `"auto"` with no switch in flight and goes
    to `Done` from `Standby`.

    Source: Raptor 7 (`IdAndSortColds`: the candidate order),
     (the initiation branch) (`Prior=Standby`);
    raptor/core/engine/cold_standby.py:147-176 (the sorting port),
     (`switches_required`) (the ordered walk and its `break`),
    raptor/core/engine/cascade_cold.py:48-59 (`spawn_switch`);
    docs/engine-design/node-status-and-cold-standby.adoc ("The candidate
    order is the sorted link order, which is why the sort's tie behaviour is
    visible in the log")
    """
    result = run(_group(
        [("A", 10, FAIL_AT, {}),
         ("B", 20, FAIL_AT, {}),
         ("C", 30, 500.0, {"auto_time": SWITCH_TIME}),
         ("D", 40, 500.0, {"auto_time": SWITCH_TIME}),
         ("E", 50, 500.0, {"auto_time": SWITCH_TIME})],
        k=2))

    assert result.draws == 0, (
        "every distribution is Fixed and AProb = 1.0 short-circuits "
        f"set_switch_time's roll, so the run must draw nothing; it drew "
        f"{result.draws}")

    assert_timeline(result, [
        (0, 'Block', 'A', '--', 'Good'),
        (0, 'Block', 'B', '--', 'Good'),
        (0, 'Block', 'C', '--', 'Good'),
        (0, 'Block', 'D', '--', 'Good'),
        (0, 'Block', 'E', '--', 'Good'),
        # Kickoff: five ready paths, RunsRequired = 2, so three are demoted.
        (0, 'Block', 'C', 'Good', 'Standby'),
        (0, 'Block', 'D', 'Good', 'Standby'),
        (0, 'Block', 'E', 'Good', 'Standby'),
        (0, 'Node', 'Cold', '--', 'Good'),
        (20, 'Block', 'A', 'Good', 'Failed'),
        (20, 'Block', 'B', 'Good', 'Failed'),
        (20, 'Block', 'A', 'Failed', 'Repairing'),
        (20, 'Node', 'Cold', 'Good', 'Bad'),
        (20, 'Node', 'end', '--', 'Bad'),
        (20, 'System', 'State_change', '--', 'Red'),
        (20, 'Block', 'B', 'Failed', 'Repairing'),
        # Two switches, two links: `_dsw8` then `_dsw8_dsw9`. E's `_s10`
        # never changes, because the loop breaks before reaching it.
        (20, 'Node', 'Cold', 'Bad', 'Bad'),
        # Both switches complete at 20 + 6 and both links come back.
        (26, 'Node', 'Cold', 'Bad', 'Bad'),
        (26, 'Block', 'C', 'Standby', 'Good'),
        (26, 'Block', 'D', 'Standby', 'Good'),
        (26, 'Node', 'Cold', 'Bad', 'Degraded'),
        (26, 'Node', 'end', 'Bad', 'Good'),
        (26, 'System', 'State_change', 'Red', 'Yellow'),
        (30, 'Block', 'A', 'Repairing', 'Done'),
        (30, 'Block', 'B', 'Repairing', 'Done'),
        (30, 'Block', 'C', 'Good', 'Done'),
        (30, 'Block', 'D', 'Good', 'Done'),
        # E is the candidate the survey never reached.
        (30, 'Block', 'E', 'Standby', 'Done'),
        (30, 'System', 'Phase_change', '--', 'Mission_failure'),
        (30, 'System', 'Simulation_Terminate', '--', '--'),
    ], where="cold_links order decides")


def test_a_candidate_whose_automatic_roll_failed_and_whose_manual_time_is_positive_switches_on_the_manual_arm() -> None:
    """A `checkAutosFirst` cold group takes its `"manual"` candidate on the
    manual loop, after the automatics loop has already spent its slot.

    Why: `SetSwitchTime`'s three-way resolution is what creates the three
    candidate statuses the survey counts separately (`cold_standby.py:103-127`).
    With `AProb = 0.0` there is no automatic roll to win, so the status comes
    from `ManTime`: positive is `"manual"`, and that status is the
    one arm the `checkAutosFirst` loop does **not** touch -- every other test
    in this file runs with the flag off, which is the *merged* single pass at
    `cold_standby.py:505-526`. When a node's own
    `checkAutosFirst` flag is on (`autos = 1`), the survey runs automatics
    first and manual second (`cold_standby.py:527-574`) -- the source's
    auto-then-manual two-phase. The manual loop's own guard is
    `IF switchingPaths < switchesRequired`, so it runs only when the
    automatics loop could not fill the group's demand, which is why this model
    needs `k = 2`: one `"auto"` candidate plus one `"manual"` candidate is the
    smallest shape in which both loops are entered and both spawn.

    Setup: a `2-of-4` cold node `Cold` (`k = k* = 2`, `autos = 1`) fed by
    primaries `A` (`ColdPri = 10`) and `B` (`20`), which both fail at 20, and
    standbys `C` (`30`, `AProb = 1.0`, `AutoTime = 6.0`, so `"auto"`) and `D`
    (`40`, `AProb = 0.0`, `ManTime = 4.0`, so `"manual"`). `MANUAL_TIME` is
    shorter than `SWITCH_TIME` on purpose, so the run shows the manual switch
    finishing first. Four blocks, three nodes, one replication, all `Fixed`.

    Expected: kickoff demotes `C` and `D`, and both links resolve to a
    switchable status. At 20 both primaries fail, `ready_paths = 0` and
    `switches_required = 2`; the automatics loop starts `C`'s six-hour switch
    and the manual loop then starts `D`'s four-hour one, so the group writes
    two lines at 20 with `_dsw7` and then `_dsw7_dsw8`. `D`'s switch completes
    at 24 on its own `ManTime`, which is the group's third line at 24 -- and
    `D` is *not* promoted there, because one ready path still does not meet
    `k = 2`. At 26 `C`'s switch completes, the group has its two ready paths,
    and `C` and `D` are both promoted in the same pass.

    Source: Raptor 7 (`Link.SetSwitchTime`'s
    `AProb`/`ManTime` resolution) (the manual initiation loop,
    ported as "a mirror of the auto loop ... differing only on
    `switchStatus = manual`") (the merged single pass the
    `checkAutosFirst = 0` node takes instead) (`Prior=Standby`);
    raptor/core/engine/cold_standby.py:103-127 (the resolution),
     (`man_switch_paths` in the survey),
     (the automatics loop) (the manual loop),
     (`_prep_for_switch`'s `manual -> manualInProgress`),
     (`perform_switch_process`'s wait);
    the internal engine ledger (`SetSwitchTime`'s three-way resolution);
    docs/engine-design/node-status-and-cold-standby.adoc
    """
    result = run(_group(
        [("A", 10, FAIL_AT, {}),
         ("B", 20, FAIL_AT, {}),
         ("C", 30, 500.0, {"auto_time": SWITCH_TIME}),
         ("D", 40, 500.0, {"a_prob": 0.0, "man_time": MANUAL_TIME})],
        k=2, autos=True))

    assert result.draws == 0, (
        "every distribution is Fixed, and AProb is 1.0 or 0.0 on both "
        "standbys -- each of which short-circuits set_switch_time's roll -- so "
        f"the run must draw nothing; it drew {result.draws}")

    assert_timeline(result, [
        (0, 'Block', 'A', '--', 'Good'),
        (0, 'Block', 'B', '--', 'Good'),
        (0, 'Block', 'C', '--', 'Good'),
        (0, 'Block', 'D', '--', 'Good'),
        (0, 'Block', 'C', 'Good', 'Standby'),
        (0, 'Block', 'D', 'Good', 'Standby'),
        (0, 'Node', 'Cold', '--', 'Good'),
        (20, 'Block', 'A', 'Good', 'Failed'),
        (20, 'Block', 'B', 'Good', 'Failed'),
        (20, 'Block', 'A', 'Failed', 'Repairing'),
        (20, 'Node', 'Cold', 'Good', 'Bad'),
        (20, 'Node', 'end', '--', 'Bad'),
        (20, 'System', 'State_change', '--', 'Red'),
        (20, 'Block', 'B', 'Failed', 'Repairing'),
        # The automatics loop starts C's switch ...
        (20, 'Node', 'Cold', 'Bad', 'Bad'),
        # ... and the manual loop then starts D's, on D's own ManTime.
        (24, 'Node', 'Cold', 'Bad', 'Bad'),
        # D's switch is done at 24 but k = 2 is not met yet, so D waits.
        (26, 'Block', 'C', 'Standby', 'Good'),
        (26, 'Block', 'D', 'Standby', 'Good'),
        (26, 'Node', 'Cold', 'Bad', 'Degraded'),
        (26, 'Node', 'end', 'Bad', 'Good'),
        (26, 'System', 'State_change', 'Red', 'Yellow'),
        (30, 'Block', 'A', 'Repairing', 'Done'),
        (30, 'Block', 'B', 'Repairing', 'Done'),
        (30, 'Block', 'C', 'Good', 'Done'),
        (30, 'Block', 'D', 'Good', 'Done'),
        (30, 'System', 'Phase_change', '--', 'Mission_failure'),
        (30, 'System', 'Simulation_Terminate', '--', '--'),
    ], where="manual arm of the autos-first survey")


def test_a_switch_in_flight_is_cancelled_once_the_group_has_its_full_k_max_paths_again() -> None:
    """A `1-of-2` cold group whose primary is repaired while the standby's
    switch is still running cancels that switch: it is no longer wanted.

    Why: this is the cancellation the *enough paths* branch owns of its own,
    and it is a different judgement from the excess-switch one above. Having
    reached `ready_paths >= runs_required` (`cold_standby.py:609`), the survey
    re-reads every link in `cold_links` order, and a link that is `LinkStandby`
    **and** mid-switch (`autoInProgress` / `manualInProgress`) has its
    own `PerformSwitch` interrupted -- but only
    `IF RunsRequired >= kMax` (source's own comment
    `cmc 6/19/06   Do not interrupt if kMax is not met`). The port keeps that
    gate (`cold_standby.py:739-740`): with k below k*, the group is not at its
    ceiling and the in-flight switch is still worth finishing. `CSSwitchRaceProbe`
    is the real capture: "the primary repaired (0.2) before the standby's 0.5
    switch completes".

    The gate is what makes this model need `k = k* = 1`: the survey computes
    `runs_required = k_max = 1` the moment one ready path reappears
    (`cold_standby.py:468-469`), which is the only configuration in which the
    `>=` holds on the very pass that notices the switch.

    Setup: a `1-of-2` cold node `Cold` (`k = k* = 1`) fed by `A`
    (`ColdPri = 10`, the primary) and `B` (`20`, the standby). `B`'s link
    carries `AutoTime = 6.0`. `A` fails at 20 with a 4-hour repair, so it is
    back at 24 -- two hours *before* `B`'s switch would complete at 26. Two
    blocks, three nodes, one replication, all `Fixed`.

    Expected: kickoff demotes `B`. At 20 `A` fails, the group goes `Bad` with
    no ready path, and `B`'s six-hour switch starts (`_dsw4`). At 24 `A` is
    repaired and the group is back to one ready path, so `runs_required = 1 =
    k_max`: the enough-paths branch keeps `A` and reaches `B`, whose link is
    `LinkStandby` and mid-switch, and cancels the switch. That is the group's
    **second** line at 24, with `B`'s tag back at `_s4`, and it is the
    observable the claim is about. `B` never comes back: the cancelled switch
    left `B`'s status `"auto"` again (`cold_standby.py:912-913`, the
    `ON INTERRUPT` redraw) with nobody waiting on it, so the group settles
    `Good` on `A` alone and `B` goes to `Done` from `Standby`.

    Source: Raptor 7 (`cmc 6/19/06   Do not interrupt if kMax is
    not met`: `Interrupt(link, "PerformSwitch")`) (`ON INTERRUPT`'s
    `SetSwitchTime` redraw) (the "enough paths" branch);
    raptor/core/engine/cold_standby.py:468-469 (`runs_required = k_max`),
     (the enough-paths walk) (the in-progress cancellation
    and its `runs_required >= k_max` gate) (the redraw),
    raptor/core/engine/cascade_cold.py:73 (`interrupt_link` and
    `_interrupt_switch`); the internal engine ledger
    (`CSSwitchRaceProbe`) (the `ON INTERRUPT` arm's two halves);
    docs/engine-design/node-status-and-cold-standby.adoc
    """
    with _recorded_switch_interrupts() as interrupted:
        result = run(_group(
            [("A", 10, FAIL_AT, {"repair": 4.0}),
             ("B", 20, 500.0, {"auto_time": SWITCH_TIME})],
            k=1))

    assert result.draws == 0, (
        "every distribution is Fixed and AProb = 1.0 short-circuits "
        f"set_switch_time's roll, so the run must draw nothing; it drew "
        f"{result.draws}")

    # One cancellation, and it is B's link: link 4 (the log's own `_dsw4` /
    # `_s4`), sourced by block 2, `ColdPri = 20`. Nothing else in this run
    # reaches the interrupt callback, which the whole-list comparison proves.
    assert interrupted == [(4, 2, 20)], (
        "the enough-paths survey should have cancelled exactly one switch -- "
        f"B's, no longer needed -- and issued nothing else; it recorded "
        f"{interrupted}")

    assert_timeline(result, [
        (0, 'Block', 'A', '--', 'Good'),
        (0, 'Block', 'B', '--', 'Good'),
        (0, 'Block', 'B', 'Good', 'Standby'),
        (0, 'Node', 'Cold', '--', 'Good'),
        (20, 'Block', 'A', 'Good', 'Failed'),
        (20, 'Block', 'A', 'Failed', 'Repairing'),
        (20, 'Node', 'Cold', 'Good', 'Bad'),
        (20, 'Node', 'end', '--', 'Bad'),
        (20, 'System', 'State_change', '--', 'Red'),
        # A repairs at 24 and the group is back to one ready path ...
        (24, 'Block', 'A', 'Repairing', 'Repaired'),
        (24, 'Block', 'A', 'Repaired', 'Good'),
        (24, 'Node', 'Cold', 'Bad', 'Degraded'),
        (24, 'Node', 'end', 'Bad', 'Good'),
        # ... and the survey cancels B's still-running switch, which is this
        # second line at the same instant (`_dsw4` -> `_s4`).
        (24, 'Node', 'Cold', 'Degraded', 'Good'),
        (24, 'System', 'State_change', 'Red', 'Green'),
        (30, 'Block', 'A', 'Good', 'Done'),
        # The cancelled switch left B "auto" with nobody waiting on it.
        (30, 'Block', 'B', 'Standby', 'Done'),
        (30, 'System', 'Phase_change', '--', 'Mission_failure'),
        (30, 'System', 'Simulation_Terminate', '--', '--'),
    ], where="in-flight switch no longer wanted")


def test_a_link_that_failed_its_automatic_roll_with_a_zero_manual_time_is_still_switchable() -> None:
    """A standby whose `AProb = 0.0` **and** whose `ManTime = 0.0` resolves to
    `"ready"`, so the survey can switch to it without a timer and without
    failing the path.

    Why: `SetSwitchTime`'s three-way resolution is not "auto unless told
    otherwise". With `AProb = 0.0` the automatic branch loses before any draw
    (`cold_standby.py:103-106`), and the manual fallback then has three
    answers: `ManTime = -1.0` is `"none"` (the permanently
    unusable path that logs `Switch_Failed`, pinned by
    `test_b_cascade_tail.py`), `ManTime > 0.0` is `"manual"` (the
    arm `test_a_candidate_whose_automatic_roll_failed_...` above pins), and
    **`ManTime = 0.0` is `"ready"`** -- the link is switchable at once, with no
    `PrepForSwitch` and no `PerformSwitch` timer, and `AutoTime` is never
    consulted. This is the resolution every existing cold fixture reaches by
    the *other* route (`AProb = 1.0` with `AutoTime = 0.0`), and
    the two routes are indistinguishable from the log alone -- which is exactly
    why the row it produces has to be written down against this input.

    Setup: a `1-of-2` cold node `Cold` (`k = k* = 1`) fed by `A`
    (`ColdPri = 10`) and `B` (`20`, the standby). `B`'s link carries
    `AProb = 0.0` and `ManTime = 0.0`; `A`'s carries the suite's usual
    `AProb = 1.0, ManTime = -1.0`. `A` fails at 20 and repairs past the run
    end. Two blocks, three nodes, one replication, all `Fixed`.

    Expected: kickoff demotes `B` (`Too_many_on`), and because that took `B`'s
    link to `LinkStandby`, `set_switch_time` ran on it and resolved it to
    `"ready"`. At 20 `A` fails: `B`'s link counts as a ready path, so
    `ready_paths = 1 >= runs_required = 1` and the survey takes the
    **enough-paths** branch, where a `LinkStandby` link whose `switch_status`
    is `"ready"` and has room is promoted (`Prior=Standby`). The group goes
    `Good -> Degraded` at 20 -- the instant of the failure, with no
    intermediate `Bad`, because the standby never needed switching back -- and
    no `Switch_Failed` line is written. A `ManTime = -1.0` on the same link
    would have resolved to `"none"` and produced exactly that line instead.

    Source: Raptor 7 (`Link.SetSwitchTime`: `AProb == 0.0 ->
    manual/ManTime`, or `"none"` if `ManTime == -1`, or `"ready"` if
    `ManTime == 0`) (`Prior=Standby`)
    (the `Switch_Failed` arms this input avoids);
    raptor/core/engine/cold_standby.py:103-127 (the resolution
    being this input) (`ready_paths` counting a `LinkStandby` link),
     (the enough-paths branch's `LinkStandby` promotion),
    raptor/core/engine/nodes.py:441-447 (the `SetSwitchTime` call that fired);
    the internal engine ledger (`SetSwitchTime`'s three-way resolution);
    docs/engine-design/node-status-and-cold-standby.adoc
    """
    result = run(_group(
        [("A", 10, FAIL_AT, {}),
         ("B", 20, 500.0, {"a_prob": 0.0, "man_time": 0.0})],
        k=1))

    assert result.draws == 0, (
        "every distribution is Fixed and AProb = 0.0 short-circuits "
        f"set_switch_time's roll, so the run must draw nothing; it drew "
        f"{result.draws}")

    assert_timeline(result, [
        (0, 'Block', 'A', '--', 'Good'),
        (0, 'Block', 'B', '--', 'Good'),
        # Kickoff's Too_many_on takes B's link to LinkStandby, which is where
        # SetSwitchTime ran and resolved AProb = 0.0 / ManTime = 0.0 to "ready".
        (0, 'Block', 'B', 'Good', 'Standby'),
        (0, 'Node', 'Cold', '--', 'Good'),
        (20, 'Block', 'A', 'Good', 'Failed'),
        (20, 'Block', 'A', 'Failed', 'Repairing'),
        # Same instant: B is already a ready path, so it is promoted straight
        # away with no switch in flight -- and no Switch_Failed either.
        (20, 'Block', 'B', 'Standby', 'Good'),
        (20, 'Node', 'Cold', 'Good', 'Degraded'),
        (20, 'System', 'State_change', '--', 'Yellow'),
        (30, 'Block', 'A', 'Repairing', 'Done'),
        (30, 'Block', 'B', 'Good', 'Done'),
        (30, 'System', 'Phase_change', '--', 'Mission_success'),
        (30, 'System', 'Simulation_Terminate', '--', '--'),
    ], where="ManTime = 0 resolves to ready")
