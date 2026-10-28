"""The residue: transitions that needed a purpose-built model, not a rung.

Everything else under `tests/behaviour/` reaches its transitions from the
ladder's canonical fixtures or from a mechanism cluster. The fifty rows the
manifest still calls `planned` are the residue: each one needs a model built
*to provoke* a particular state, and none of the eleven spine rungs reaches
any of them (measured -- `spine(1)`..`spine(11)`, with and without cost
analysis, assert zero of them). So the fixtures here are deliberately small
and deliberately unlike the ladder: one block and a phase table where a phase
is enough, two blocks and a dependence where a dependence is enough, five
blocks and one cold group where a node's status is the subject. **Twenty-three
of the fifty are pinned here**; the module's closing section says why the
other twenty-seven are not, which is the more useful half.

**The one mechanism that carries five of the twenty-three.** `ChangeBlockState` logs
the block's *active* status whenever the new active status is not `Active`,
so a `Cut` or `Linked` block's every subsequent state change reads as a row
into that same `Cut`/`Linked` (`blocks.change_block_state:169-185`). And the
cost section writes one extra row *before* the transition's own line whenever
a change away from `Repairing` happens while `RepWait` is still set
(`RepWait` is set just before the repair's own
`WAIT DURATION TimeToRepair` and cleared only when that timer completes,.
A phase boundary that lands on a block which is
*mid-repair* therefore hits both at once: the cost row writes `Partial_Repair`
and the transition's own line then writes whatever the new active status is.
Four of the twenty-three are exactly that pair read from each end -- `Cut ->
Partial_Repair` / `Partial_Repair -> Linked`, `Linked -> Partial_Repair` /
`Partial_Repair -> Cut`, and `Partial_Repair -> Repairing` for a boundary that
re-activates the block instead of linking it. `Partial_Repair` only exists
with cost analysis on, so the fixtures that need it set it; every distribution
is still `Fixed` (B2), so they consume no randomness either way.

**Why the fixtures are shaped the way they are.** Two constraints are worth
stating because they look like choices and are not:

* *A `Linked` block never logs `Standby`.* `change_block_state` picks
  `status_string` only when the new active status is `Active`, so a demotion
  of a `Linked` block writes `Linked` twice. `Cut -> Standby` is reachable
  (the cold group's switch-off wins the same instant the boundary
  re-activates the block) and `Linked -> Standby` is not, for this reason
  alone. See `test_a_cold_group_member_is_switched_off_to_standby_while_cut`.
* *A node needs a standby **path** before it can leave `Standby`.* The four
  `Node Standby -> X` rows are all the same arithmetic read four ways
  (`nodes.determine_node_status:278-298`: `Standby` is the arm
  `runs < k <= runs + standbys`), and each one is reached by moving exactly
  one of the node's three counters. The cheapest way to give a node a standby
  path is a dependence on a cold group's standby member, which is what the
  four node fixtures do.

Every model here is built in memory and run through `harness.run` (B3), one
replication, at most five blocks and four nodes (B4), every distribution
`Fixed` so `Run.draws == 0` is asserted rather than hoped for (B2), and the
expectations are literals written inline in the `assert_timeline` call --
`test_b_behaviour_coverage.py` finds them with an `ast` pass and cannot see a
literal that is not syntactically an argument of the call.

**Twenty-three of the fifty, and why the other twenty-seven are not here.**
Every fixture below is B4-small -- at most five blocks, four nodes, one
replication -- and the twenty-seven are not a shortfall of effort but three
kinds of boundary. They are listed here so the next reader does not spend the
afternoon again:

1. **Vocabulary, not behaviour (4).** `End_Partial_pos -> Good`,
   `End_Partial_pre -> Repairing`, `RepHold -> End_Partial_pos` and
   `RepHold -> End_Partial_pre` are the corpus's *15-column* reading of
   `End_Partial_post-LDT` and `End_Partial_pre-LDT`: real Raptor pads the
   action column to 15 characters (`eventlog.py:65`) and truncates, which is
   why the corpus has no state name longer than 15 characters at all. The
   port's own log text truncates identically, but `harness._fields` prefers a
   `LogLine`'s structured `action` attribute over its padded text
   (`harness.py:177-203`), so the harness reads the **un**truncated names. The
   behaviour is reproduced -- `sim_start=5` puts a block in pre-LDT at kickoff
   and it logs `End_Partial_pre-LDT -> Repairing` -- but no literal can both
   assert that timeline and carry the corpus's spelling. This is the *fourth*
   claim in the brief that does not hold: the truncation is real, and the
   harness does not go through it. Reported, not worked around.

2. **Excluded by the port's own structure (7).** `Node Bad -> Switch_Failed`,
   `Node NodePM -> Switch_Failed` and `Node Switch_Failed -> Degraded`:
   a cold survey consumes *every* un-switchable candidate in the group, so a
   group is failed at most once per run and can never be `Bad` or `NodePM`
   with a candidate still standing (`cold_standby.py:444-503`; the same
   conclusion is recorded in `test_b_trans_graph.py:75-86`). `Hier Bad -> Bad`
   and `Hier NodePM -> NodePM`: `build_from_spec` forces a hierarchy's Out
   marker to exactly one incoming link, `reconfigure_node` writes the marker's
   row only when that link's status changes, and with one link the marker's
   status is a bijection with it -- so the marker cannot log the same status
   twice (`nodes.py:380-426`; `test_b_trans_graph.py:57-62`). `Linked ->
   Standby` is the third, and it is the constraint stated at the top: a
   demotion prints `status_string` only under an `Active` active status, and a
   `Linked` block never has one. `Idle -> RepHold` and `Idle -> pmReqDefer`
   are the last two: every `REP_HOLD` transition in the engine is inside
   `Repair`'s maintenance path (`block_lifecycle.py:3307/3381/3486/3568/3847`),
   which an `Idle` block never enters, and `DetermineNewState`'s PM arm is
   only reached from a `FailWait`.

3. **Not found in a B4-sized model (16).** `Done -> RepHold`,
   `Done -> Repairing`, `Done -> Sparing`, `Standby -> Resources`,
   `pmReqDefer -> Standby`, `PM -> Sparing`, `PM_is_due -> Good`,
   `PM_is_due -> Sparing`, `Resources -> Failed`, `Resources -> PM_is_due`,
   `Sparing -> End_Post-LDT`, `Sparing -> Good`, `Sparing -> PM_complete`,
   `Sparing -> Partial_Repair`, `Sparing -> Resources`, and the
   `End_Partial_* -> *` pair's *un*truncated spelling. The `Sparing` and
   `Resources` rows are all *raw* event rows rather than states
   (`block_lifecycle.py:2503/2622/2623/3171/3222/3332/3441/3590` for
   `Sparing` for `Resources`), and the port
   writes every one of them between two *states*, so the only way to reach
   `Sparing -> X` is for the next row after a `Sparing` row to be a
   `ChangeBlockState` -- which on both the repair and the PM path it always is.
   `Done -> Repairing` in particular has no route at all in this engine:
   `_transition(BlockStatus.REPAIRING, ...)` has exactly one call site
   (`block_lifecycle.py:3813`), it is preceded unconditionally by a `RepHold`
   and a `Failed` row, and a block that has reached `Done` has no process
   left to reach it. These are the rows the brief's "5 to 60 runs and models
   far larger than B4" note is about; they are the honest boundary of what a
   small-fixture suite can cover.
"""
from __future__ import annotations

from typing import Any

from .harness import FIXED, assert_timeline, run

#: A phased cell. `build_from_spec`'s `_apply_phase_assignment` insists on the
#: `"<code>: <value>"` form for a block, because the loader unconditionally
#: does `float(value.partition(":")[2])` on it (`raptor/api/build.py:2047-2069`).
ACTIVE = "A: 1.000"
CUT = "C: 1.000"
LINKED = "L: 1.000"

#: Every phase in this file is 20 hours, so a five-phase model is a
#: 100-hour run with boundaries at 20, 40, 60 and 80.
PHASE_LEN = 20.0


def _block(name: str, fail: float, repair: float, **extra: Any) -> dict[str, Any]:
    """One block with a `Fixed` life: fails at *fail*, repaired at
    ``fail + repair``, draws nothing."""
    return {**extra, "name": name, "fail_dist": FIXED, "fail_params": [fail],
            "repair_dist": FIXED, "repair_params": [repair]}


def _cold(prio: int) -> dict[str, Any]:
    """The switching attributes every cold link carries.

    `AProb = 1.0` short-circuits `SetSwitchTime`'s `UniformReal(0,1)` draw
    and `AutoTime = 0.0` resolves `switch_status` to `"ready"` rather than
    `"auto"`, so no `PrepForSwitch`/`PerformSwitch` timer is ever started and
    the fixture stays draw-free (`cold_standby.py:103-106`). `ManTime = -1.0`
    says "no manual fallback"; it is never read, because the automatic branch
    is decided first.
    """
    return {"cold_pri": prio, "a_prob": 1.0, "auto_time": 0.0, "man_time": -1.0}


def _phases(cells: list[str]) -> list[dict[str, Any]]:
    """One 20-hour mission phase per entry in *cells*."""
    return [{"name": f"P{index + 1}", "mission": 1, "dist": FIXED,
             "params": [PHASE_LEN]} for index in range(len(cells))]


def _chain(name: str) -> dict[str, Any]:
    """`Start -> name -> End`: the least topology the loader accepts."""
    return {"system": {"run_length": 0.0, "num_runs": 1, "termination": "TimeSim"},
            "blocks": [],
            "nodes": [{"name": "Start", "type": 1, "n": 0, "out": 1},
                      {"name": "End", "type": 3, "n": 1, "out": 0}],
            "links": [{"from": "Start", "to": name}, {"from": name, "to": "End"}]}


def _one_phased_block(cells: list[str], *, fail: float, repair: float,
                      length: float = 100.0, cost_analysis: bool = False,
                      **extra: Any) -> dict[str, Any]:
    """One block, a phase per entry in *cells*, and that block phased.

    The smallest fixture in the file: one block, two nodes. Everything the
    phase-driven rows need is the phase table plus one `Fixed` life.
    """
    spec = _chain("A")
    spec["system"]["run_length"] = length
    spec["system"]["cost_analysis"] = cost_analysis
    spec["blocks"] = [_block("A", fail, repair, **extra)]
    spec["phases"] = _phases(cells)
    spec["phase_states"] = {"A": list(cells)}
    return spec


# ---------------------------------------------------------------------------
# A phase boundary that lands mid-repair: the cost row, then the new status
# ---------------------------------------------------------------------------

def test_a_phase_boundary_that_links_a_block_mid_repair_writes_partial_repair_then_linked() -> None:
    """A boundary landing on a `Cut` block that is mid-repair writes
    `Cut -> Partial_Repair` and then `Partial_Repair -> Linked`; the next
    boundary, on the now-`Linked` block, writes `Linked -> Partial_Repair` and
    then `Partial_Repair -> Repairing`.

    Why: `ConductPhaseChange` classifies every phased block whose phase *type*
    differs and applies the group by calling
    `ChangeBlockState(oldOpStatus, CUT-or-LINKED-or-ACTIVE, "Phase_Change")`
    (`raptor/core/engine/cascade_phase.py:131-150`). Two things then happen in
    that one call. The cost section fires first, because it runs before the
    state-change line is built: `oldOpStatus` is `Repairing` and `RepWait` is
    still set -- the repair's own `WAIT DURATION TimeToRepair` has not
    completed -- so it writes `WriteEvents(..., "Partial_Repair",
    "RepairTime="+REALTOSTR(stateTime))`. Only then
    does the transition choose what to log, and because the new active status
    is not `Active` it logs the *active* status, not `Repairing`
    (`blocks.change_block_state:169-185`). That is the whole of the pair: the
    `Partial_Repair` row is the cost section's, the row after it is the
    boundary's.

    Setup: one block `A` between a Start and an End node, a `Fixed` 30-hour
    life and a `Fixed` 40-hour repair, so the repair spans 30 to 70 and the
    40- and 60-hour boundaries both land inside it. Five 20-hour phases with
    `A`'s cells `Active, Cut, Linked, Active, Active`. Cost analysis on,
    because `Partial_Repair` is a line of the cost section and does not exist
    without it. Every distribution is `Fixed`, so the run draws nothing.

    Expected: `A` is Cut from 20; at 40 the boundary Links it mid-repair and
    the two rows are `Cut -> Partial_Repair` then `Partial_Repair -> Linked`;
    at 60 the boundary re-activates it, and because the new active status *is*
    `Active` the second row is the operating status after all --
    `Linked -> Partial_Repair` then `Partial_Repair -> Repairing`. The repair
    completes at 70 (`Repaired -> Good`, no `Partial_Repair`, because
    `RepWait` is cleared the moment the timer completes). The run ends at 100
    with the repair of a *second* failure still running, which is the
    run-ending `Repairing -> Partial_Repair` / `Partial_Repair -> Done` pair.

    Source: raptor/core/engine/cascade_phase.py:131-150 (`apply_block`'s
    `ChangeBlockState` call), raptor/core/engine/accounting.py:79-98 (the cost
    section's `Partial_Repair` row), raptor/core/engine/block_lifecycle.py:3595-3665
    (`RepWait` set and cleared); Raptor 7,
    """
    result = run(_one_phased_block(
        [ACTIVE, CUT, LINKED, ACTIVE, ACTIVE], fail=30.0, repair=40.0,
        cost_analysis=True))

    assert result.draws == 0, (
        "every distribution is Fixed, so B2 says this run must draw nothing; "
        f"it drew {result.draws}")

    assert_timeline(result, [
        (0.0, 'System', 'State_change', '--', 'Mission'),
        (0.0, 'Block', 'A', '--', 'Good'),
        (20.0, 'System', 'Phase_change', '--', 'Phase_change'),
        (20.0, 'Block', 'A', 'Good', 'Cut'),
        (20.0, 'Node', 'end', '--', 'Bad'),
        (20.0, 'System', 'State_change', 'Mission', 'Red'),
        (30.0, 'Block', 'A', 'Cut', 'Failed'),
        (30.0, 'Block', 'A', 'Failed', 'Cut'),
        (40.0, 'System', 'Phase_change', 'Phase_change', 'Phase_change'),
        # The cost section's row, then the boundary's own row.
        (40.0, 'Block', 'A', 'Cut', 'Partial_Repair'),
        (40.0, 'Block', 'A', 'Partial_Repair', 'Linked'),
        (40.0, 'Node', 'end', 'Bad', 'Good'),
        (40.0, 'System', 'State_change', 'Red', 'Yellow'),
        (60.0, 'System', 'Phase_change', 'Phase_change', 'Phase_change'),
        # Re-activated rather than linked: now the second row *is* Repairing.
        (60.0, 'Block', 'A', 'Linked', 'Partial_Repair'),
        (60.0, 'Block', 'A', 'Partial_Repair', 'Repairing'),
        (60.0, 'Node', 'end', 'Good', 'Bad'),
        (60.0, 'System', 'State_change', 'Yellow', 'Red'),
        (70.0, 'Block', 'A', 'Repairing', 'Repaired'),
        (70.0, 'Block', 'A', 'Repaired', 'Good'),
        (70.0, 'Node', 'end', 'Bad', 'Good'),
        (70.0, 'System', 'State_change', 'Red', 'Green'),
        (80.0, 'System', 'Phase_change', 'Phase_change', 'Phase_change'),
        (100.0, 'Block', 'A', 'Good', 'Failed'),
        (100.0, 'Block', 'A', 'Failed', 'Repairing'),
        (100.0, 'Node', 'end', 'Good', 'Bad'),
        (100.0, 'System', 'State_change', 'Green', 'Red'),
        # The run ends mid-repair: RepWait never cleared, so the cost row again.
        (100.0, 'Block', 'A', 'Repairing', 'Partial_Repair'),
        (100.0, 'Block', 'A', 'Partial_Repair', 'Done'),
        (100.0, 'System', 'Phase_change', 'Phase_change', 'Mission_failure'),
        (100.0, 'System', 'Simulation_Terminate', '--', '--'),
    ])


def test_a_phase_boundary_that_cuts_a_linked_block_mid_repair_writes_partial_repair_then_cut() -> None:
    """The same pair from the other side: `Linked -> Partial_Repair` then
    `Partial_Repair -> Cut`.

    Why: identical code to the sibling test, read with the *destinations*
    swapped -- `ChangeBlockState(Repairing, CUT, "Phase_Change")` fires the
    cost section (still `Repairing`, still `RepWait`) and then logs `CUT`,
    because a non-`Active` new active status is what the log prints
    (`cascade_phase.py:139-150`; `blocks.change_block_state:169-185`). The
    reason this needs its own test and its own phase table is that it is a
    *different* `ChangeBlockState` call with a different second argument, and
    the pair `Partial_Repair -> Cut` cannot be observed in a model whose
    boundary only ever Links.

    Setup: the same one-block model, with `A`'s cells
    `Active, Linked, Cut, Linked, Linked` so the 40-hour boundary is
    `Linked -> Cut` inside a 30-to-70 repair, and the 60-hour boundary brings
    it back to `Linked`. Cost analysis on.

    Expected: `Linked -> Partial_Repair` / `Partial_Repair -> Cut` at 40 and
    `Cut -> Partial_Repair` / `Partial_Repair -> Linked` at 60; the repair
    completes at 70 straight to `Linked`, because `RepWait` was cleared when
    the timer completed and there is no cost row between `Repairing` and
    `Repaired`.

    Source: raptor/core/engine/cascade_phase.py:131-150,
    raptor/core/engine/accounting.py:79-98, raptor/core/engine/blocks.py:169-185;
    """
    result = run(_one_phased_block(
        [ACTIVE, LINKED, CUT, LINKED, LINKED], fail=30.0, repair=40.0,
        cost_analysis=True))

    assert result.draws == 0, (
        "every distribution is Fixed, so B2 says this run must draw nothing; "
        f"it drew {result.draws}")

    assert_timeline(result, [
        (0.0, 'System', 'State_change', '--', 'Mission'),
        (0.0, 'Block', 'A', '--', 'Good'),
        (20.0, 'System', 'Phase_change', '--', 'Phase_change'),
        (20.0, 'Block', 'A', 'Good', 'Linked'),
        (30.0, 'Block', 'A', 'Linked', 'Failed'),
        (30.0, 'Block', 'A', 'Failed', 'Linked'),
        (30.0, 'System', 'State_change', 'Mission', 'Yellow'),
        (40.0, 'System', 'Phase_change', 'Phase_change', 'Phase_change'),
        (40.0, 'Block', 'A', 'Linked', 'Partial_Repair'),
        (40.0, 'Block', 'A', 'Partial_Repair', 'Cut'),
        (40.0, 'Node', 'end', '--', 'Bad'),
        (40.0, 'System', 'State_change', 'Yellow', 'Red'),
        (60.0, 'System', 'Phase_change', 'Phase_change', 'Phase_change'),
        (60.0, 'Block', 'A', 'Cut', 'Partial_Repair'),
        (60.0, 'Block', 'A', 'Partial_Repair', 'Linked'),
        (60.0, 'Node', 'end', 'Bad', 'Good'),
        (60.0, 'System', 'State_change', 'Red', 'Yellow'),
        (70.0, 'Block', 'A', 'Linked', 'Repaired'),
        (70.0, 'Block', 'A', 'Repaired', 'Linked'),
        (70.0, 'System', 'State_change', 'Yellow', 'Green'),
        (80.0, 'System', 'Phase_change', 'Phase_change', 'Phase_change'),
        (100.0, 'Block', 'A', 'Linked', 'Failed'),
        (100.0, 'Block', 'A', 'Failed', 'Linked'),
        (100.0, 'Block', 'A', 'Linked', 'Partial_Repair'),
        (100.0, 'Block', 'A', 'Partial_Repair', 'Linked'),
        (100.0, 'System', 'State_change', 'Green', 'Yellow'),
        (100.0, 'System', 'Phase_change', 'Phase_change', 'Mission_failure'),
        (100.0, 'System', 'Simulation_Terminate', '--', '--'),
    ])


def test_a_block_that_repairs_while_cut_logs_its_repair_as_cut_until_a_boundary_makes_it_active() -> None:
    """A `Cut` block that finishes its repair reads `Cut` for every row of the
    repair and of its own recovery, and the first row after the boundary that
    re-activates it is `Cut -> Good`.

    Why: `change_block_state` builds `action_string` from the *new* active
    status, not from the operating status, whenever that active status is not
    `Active` (`raptor/core/engine/blocks.py:169-185`). So the operating
    lifecycle of a non-Active block is invisible: `Failed`, `Repaired` and the
    return to `Running` all arrive as `Cut`. This is the general form the other
    fixtures in this file specialise, and the corpus's `Cut -> ...` column is
    largely this one fact.

    Setup: one block `A`, a `Fixed` 30-hour life and a `Fixed` 10-hour repair
    (so it repairs at 40 and fails again at 70), five 20-hour phases with `A`'s
    cells `Active, Cut, Cut, Cut, Active`. The boundary at 80 therefore lands
    after the *second* repair completes, so the block is `Cut` and `Running`
    at once.

    Expected: `Cut` at 20; `Cut -> Failed` / `Failed -> Cut` at 30;
    `Cut -> Repaired` / `Repaired -> Cut` at 40; the second failure at 70 and
    its repair at 80, and at that same 80 the boundary re-activates the block,
    so the transition's own line -- which for once is printed as the operating
    status, because the new active status *is* `Active` -- reads `Cut -> Good`
    with no `Repaired` row between it and the boundary.

    Source: raptor/core/engine/blocks.py:169-185 (`change_block_state`'s
    choice of `action_string`), raptor/core/engine/cascade_phase.py:131-150;
    """
    result = run(_one_phased_block(
        [ACTIVE, CUT, CUT, CUT, ACTIVE], fail=30.0, repair=10.0))

    assert result.draws == 0, (
        "every distribution is Fixed, so B2 says this run must draw nothing; "
        f"it drew {result.draws}")

    assert_timeline(result, [
        (0.0, 'System', 'State_change', '--', 'Mission'),
        (0.0, 'Block', 'A', '--', 'Good'),
        (20.0, 'System', 'Phase_change', '--', 'Phase_change'),
        (20.0, 'Block', 'A', 'Good', 'Cut'),
        (20.0, 'Node', 'end', '--', 'Bad'),
        (20.0, 'System', 'State_change', 'Mission', 'Red'),
        (30.0, 'Block', 'A', 'Cut', 'Failed'),
        (30.0, 'Block', 'A', 'Failed', 'Cut'),
        (40.0, 'Block', 'A', 'Cut', 'Repaired'),
        (40.0, 'Block', 'A', 'Repaired', 'Cut'),
        (40.0, 'System', 'Phase_change', 'Phase_change', 'Phase_change'),
        (60.0, 'System', 'Phase_change', 'Phase_change', 'Phase_change'),
        (70.0, 'Block', 'A', 'Cut', 'Failed'),
        (70.0, 'Block', 'A', 'Failed', 'Cut'),
        (80.0, 'Block', 'A', 'Cut', 'Repaired'),
        (80.0, 'Block', 'A', 'Repaired', 'Cut'),
        (80.0, 'System', 'Phase_change', 'Phase_change', 'Phase_change'),
        # Re-activated: the operating status is printed at last.
        (80.0, 'Block', 'A', 'Cut', 'Good'),
        (80.0, 'Node', 'end', 'Bad', 'Good'),
        (80.0, 'System', 'State_change', 'Red', 'Green'),
        (100.0, 'Block', 'A', 'Good', 'Done'),
        (100.0, 'System', 'Phase_change', 'Phase_change', 'Mission_failure'),
        (100.0, 'System', 'Simulation_Terminate', '--', '--'),
    ])

# ---------------------------------------------------------------------------
# Logistics delay time on a block that is not Active
# ---------------------------------------------------------------------------

def test_a_pre_ldt_wait_on_a_cut_block_reads_cut_before_and_after_the_delay() -> None:
    """A `Cut` block's pre-LDT wait is `Cut -> End_Pre-LDT -> Cut`, and the
    same for `End_Post-LDT`; the operating states on either side of the delay
    never appear.

    Why: the two LDT lines are written by `Repair` itself with
    `WriteEvents(..., "End_Pre-LDT", "Time="+REALTOSTR(ldtWait))` and the
    matching `End_Post-LDT`, so
    they are raw event rows rather than state transitions -- but they land
    between two `ChangeBlockState` calls, and the timeline chains them like any
    other row. The transitions either side of them are
    `ChangeBlockState(RepHold, ..., "LDT")` before the wait and
    `ChangeBlockState(Repairing/Good, ..., ...)` after it, and
    `change_block_state` prints the *active* status whenever that is not
    `Active` (`blocks.py:169-185`). So the whole repair of a `Cut` block reads
    as one flat column of `Cut`.

    Setup: one block `A` between a Start and an End node, phased `Cut` in all
    four phases, a `Fixed` 30-hour life, a `Fixed` 10-hour repair, a `Fixed`
    4-hour pre-LDT and a `Fixed` 2-hour post-LDT. Cost analysis on, so the
    run-ending mid-repair pair is visible and the test doubles as the
    `Partial_Repair` regression for a LDT block.

    Expected: `Cut` from kickoff; `Cut -> Failed` at 30; `Cut -> End_Pre-LDT`
    at 34; the repair ends at 44 (`Cut -> Repaired`, then the post-LDT
    `RepHold` reads `Cut` too); `Cut -> End_Post-LDT` at 46. The second
    failure at 76 straddles the 80-hour boundary, which is why it also shows
    the mid-repair pair; the run ends at 100.

    Source: raptor/core/engine/block_lifecycle.py:3485-3495 (the pre-LDT
    `ChangeBlockState` and its `End_Pre-LDT` row) (the
    `Repaired` row, the post-LDT `RepHold` and its `End_Post-LDT` row),
    raptor/core/engine/blocks.py:169-185; Raptor 7
    """
    spec = _one_phased_block(
        [CUT, CUT, CUT, CUT, ACTIVE], fail=30.0, repair=10.0, cost_analysis=True,
        pre_ldt_dist=FIXED, pre_ldt_params=[4.0],
        post_ldt_dist=FIXED, post_ldt_params=[2.0])

    result = run(spec)

    assert result.draws == 0, (
        "every distribution is Fixed, so B2 says this run must draw nothing; "
        f"it drew {result.draws}")

    assert_timeline(result, [
        (0.0, 'System', 'State_change', '--', 'Mission'),
        (0.0, 'Block', 'A', '--', 'Cut'),
        (0.0, 'Node', 'end', '--', 'Bad'),
        (0.0, 'System', 'State_change', 'Mission', 'Red'),
        (20.0, 'System', 'Phase_change', '--', 'Phase_change'),
        (30.0, 'Block', 'A', 'Cut', 'Failed'),
        (30.0, 'Block', 'A', 'Failed', 'Cut'),
        (34.0, 'Block', 'A', 'Cut', 'End_Pre-LDT'),
        (34.0, 'Block', 'A', 'End_Pre-LDT', 'Cut'),
        (40.0, 'System', 'Phase_change', 'Phase_change', 'Phase_change'),
        (44.0, 'Block', 'A', 'Cut', 'Repaired'),
        (44.0, 'Block', 'A', 'Repaired', 'Cut'),
        (46.0, 'Block', 'A', 'Cut', 'End_Post-LDT'),
        (46.0, 'Block', 'A', 'End_Post-LDT', 'Cut'),
        (60.0, 'System', 'Phase_change', 'Phase_change', 'Phase_change'),
        (76.0, 'Block', 'A', 'Cut', 'Failed'),
        (76.0, 'Block', 'A', 'Failed', 'Cut'),
        (80.0, 'System', 'Phase_change', 'Phase_change', 'Phase_change'),
        (80.0, 'Block', 'A', 'Cut', 'End_Pre-LDT'),
        (80.0, 'Block', 'A', 'End_Pre-LDT', 'Cut'),
        (80.0, 'Block', 'A', 'Cut', 'Partial_Repair'),
        (80.0, 'Block', 'A', 'Partial_Repair', 'Repairing'),
        (80.0, 'System', 'Red_change', '--', 'Red'),
        (90.0, 'Block', 'A', 'Repairing', 'Repaired'),
        (90.0, 'Block', 'A', 'Repaired', 'RepHold'),
        (90.0, 'System', 'Red_change', 'Red', 'Red'),
        (92.0, 'Block', 'A', 'RepHold', 'End_Post-LDT'),
        (92.0, 'Block', 'A', 'End_Post-LDT', 'Good'),
        (92.0, 'Node', 'end', 'Bad', 'Good'),
        (92.0, 'System', 'State_change', 'Red', 'Green'),
        (100.0, 'Block', 'A', 'Good', 'Done'),
        (100.0, 'System', 'Phase_change', 'Phase_change', 'Mission_failure'),
        (100.0, 'System', 'Simulation_Terminate', '--', '--'),
    ])


def test_the_post_ldt_rephold_of_a_block_that_is_still_cut_is_read_as_cut_until_a_boundary_reactivates_it() -> None:
    """The `RepHold` a block parks in for its post-LDT delay reads as `Cut`
    while the block is Cut, and reads as `RepHold` the instant a boundary
    re-activates it: `Cut -> RepHold` / `Linked -> RepHold`.

    Why: `Repair`'s post-LDT arm transitions to `RepHold` with the comment
    `"postLDT"` and *then* writes the raw `End_Post-LDT` row when the delay
    expires (`block_lifecycle.py:3846-3859`). Between those two events the block's
    operating status really is `RepHold`, and the log line that says so is the
    one `ChangeBlockState` writes -- except that `change_block_state` prints
    the *active* status whenever the new active status is not `Active`
    (`blocks.py:169-185`), so while the block is `Cut` the row is `Repaired ->
    Cut` and `Cut -> End_Post-LDT`. A phase boundary in that window calls
    `ChangeBlockState(RepHold, Active, "Phase_Change")`
    (`cascade_phase.py:139-150`), and *that* call does have `Active` as its new
    active status, so it prints the operating status and the `RepHold` appears
    as a state of its own. The same is true of a `Linked` block, which is why
    the two rows are one behaviour with two active statuses and not two.

    Setup: one block `A` between a Start and an End node, a `Fixed` 30-hour
    life, a `Fixed` 1-hour repair, a `Fixed` 4-hour pre-LDT and a `Fixed`
    10-hour post-LDT, so the block is `Repaired` at 35 and still waiting out
    its post-LDT when the 40-hour boundary arrives. Four 20-hour phases, `A`'s
    cells `Cut, Cut, Active, Active` for the Cut fixture.

    Expected: `Cut -> Failed` at 30, `Cut -> End_Pre-LDT` at 34,
    `Cut -> Repaired` at 35, then at 40 the boundary's single row `Cut ->
    RepHold`, and at 45 `RepHold -> End_Post-LDT` followed by `Good` -- the
    first `Good` in the run, because only now is the block Active.

    Source: raptor/core/engine/block_lifecycle.py:3843-3859 (the `Repaired`
    row, the post-LDT `RepHold` transition and the `End_Post-LDT` row),
    raptor/core/engine/cascade_phase.py:131-150,
    raptor/core/engine/blocks.py:169-185; Raptor 7
    """
    spec = _one_phased_block(
        [CUT, CUT, ACTIVE, ACTIVE], fail=30.0, repair=1.0, length=60.0,
        pre_ldt_dist=FIXED, pre_ldt_params=[4.0],
        post_ldt_dist=FIXED, post_ldt_params=[10.0])

    result = run(spec)

    assert result.draws == 0, (
        "every distribution is Fixed, so B2 says this run must draw nothing; "
        f"it drew {result.draws}")

    assert_timeline(result, [
        (0.0, 'System', 'State_change', '--', 'Mission'),
        (0.0, 'Block', 'A', '--', 'Cut'),
        (0.0, 'Node', 'end', '--', 'Bad'),
        (0.0, 'System', 'State_change', 'Mission', 'Red'),
        (20.0, 'System', 'Phase_change', '--', 'Phase_change'),
        (30.0, 'Block', 'A', 'Cut', 'Failed'),
        (30.0, 'Block', 'A', 'Failed', 'Cut'),
        (34.0, 'Block', 'A', 'Cut', 'End_Pre-LDT'),
        (34.0, 'Block', 'A', 'End_Pre-LDT', 'Cut'),
        (35.0, 'Block', 'A', 'Cut', 'Repaired'),
        (35.0, 'Block', 'A', 'Repaired', 'Cut'),
        (40.0, 'System', 'Phase_change', 'Phase_change', 'Phase_change'),
        # The boundary lands inside the post-LDT RepHold: Active at last, so
        # the operating status is what gets printed.
        (40.0, 'Block', 'A', 'Cut', 'RepHold'),
        (40.0, 'System', 'Red_change', '--', 'Red'),
        (45.0, 'Block', 'A', 'RepHold', 'End_Post-LDT'),
        (45.0, 'Block', 'A', 'End_Post-LDT', 'Good'),
        (45.0, 'Node', 'end', 'Bad', 'Good'),
        (45.0, 'System', 'State_change', 'Red', 'Green'),
        (60.0, 'Block', 'A', 'Good', 'Done'),
        (60.0, 'System', 'Phase_change', 'Phase_change', 'Mission_failure'),
        (60.0, 'System', 'Simulation_Terminate', '--', '--'),
    ])


def test_the_same_post_ldt_rephold_reads_as_linked_on_a_linked_block() -> None:
    """`Linked -> RepHold`: the same post-LDT `RepHold` on a `Linked` block,
    read from the other active status.

    Why: identical to the sibling test with `Linked` in place of `Cut`. It is
    a separate test because the row it pins is a different corpus transition
    and because it is the only fixture in the file where a `Linked` block is
    ever shown logging something other than `Linked` -- which is possible only
    because `ChangeBlockState` was handed `Active` as the new active status.

    Setup: as the sibling, with `A`'s cells `Linked, Linked, Active, Active`.

    Expected: `Linked -> Failed` at 30, `Linked -> End_Pre-LDT` at 34,
    `Linked -> Repaired` at 35, `Linked -> RepHold` at 40 (the boundary, which
    also takes the End node `Bad` because a `Linked` path counts for
    availability only while it is up), and `RepHold -> End_Post-LDT` then
    `Good` at 45.

    Source: raptor/core/engine/block_lifecycle.py:3843-3859,
    raptor/core/engine/cascade_phase.py:131-150,
    raptor/core/engine/blocks.py:169-185; Raptor 7
    """
    spec = _one_phased_block(
        [LINKED, LINKED, ACTIVE, ACTIVE], fail=30.0, repair=1.0, length=60.0,
        pre_ldt_dist=FIXED, pre_ldt_params=[4.0],
        post_ldt_dist=FIXED, post_ldt_params=[10.0])

    result = run(spec)

    assert result.draws == 0, (
        "every distribution is Fixed, so B2 says this run must draw nothing; "
        f"it drew {result.draws}")

    assert_timeline(result, [
        (0.0, 'System', 'State_change', '--', 'Mission'),
        (0.0, 'Block', 'A', '--', 'Linked'),
        (20.0, 'System', 'Phase_change', '--', 'Phase_change'),
        (30.0, 'Block', 'A', 'Linked', 'Failed'),
        (30.0, 'Block', 'A', 'Failed', 'Linked'),
        (30.0, 'System', 'State_change', 'Mission', 'Yellow'),
        (34.0, 'Block', 'A', 'Linked', 'End_Pre-LDT'),
        (34.0, 'Block', 'A', 'End_Pre-LDT', 'Linked'),
        (35.0, 'Block', 'A', 'Linked', 'Repaired'),
        (35.0, 'Block', 'A', 'Repaired', 'Linked'),
        (40.0, 'System', 'Phase_change', 'Phase_change', 'Phase_change'),
        (40.0, 'Block', 'A', 'Linked', 'RepHold'),
        (40.0, 'Node', 'end', '--', 'Bad'),
        (40.0, 'System', 'State_change', 'Yellow', 'Red'),
        (45.0, 'Block', 'A', 'RepHold', 'End_Post-LDT'),
        (45.0, 'Block', 'A', 'End_Post-LDT', 'Good'),
        (45.0, 'Node', 'end', 'Bad', 'Good'),
        (45.0, 'System', 'State_change', 'Red', 'Green'),
        (60.0, 'Block', 'A', 'Good', 'Done'),
        # The mission closes as a failure: a `Linked` path was `Bad` for the
        # whole of phases 2 and 3, so the mission never had all of its paths.
        (60.0, 'System', 'Phase_change', 'Phase_change', 'Mission_failure'),
        (60.0, 'System', 'Simulation_Terminate', '--', '--'),
    ])


# ---------------------------------------------------------------------------
# A dependence that fires while the dependent is not Active
# ---------------------------------------------------------------------------

def _dependent(cells: list[str], *, length: float = 120.0) -> dict[str, Any]:
    """`B` depends on `A` with a 100 % `DepIdle` reaction, and `B` is phased.

    `A` fails at 20 with a 400-hour repair, so it is down for the whole run
    and `B` is idled from 20 to the end -- long enough for three boundaries to
    land on it while it is `Idle`. Three nodes (Start, a 1-of-2 Join, End),
    two blocks.
    """
    return {
        "system": {"run_length": length, "num_runs": 1, "termination": "TimeSim"},
        "blocks": [
            _block("A", 20.0, 400.0),
            _block("B", 400.0, 5.0, dep_type="RBDBlock", dep_num=1,
                   dep_idle=100.0, dep_nothing=0.0, dep_pm=0.0, dep_fail=0.0),
        ],
        "nodes": [{"name": "Start", "type": 1, "n": 0, "out": 2},
                  {"name": "Join", "type": 2, "n": 2, "out": 1, "k": 1},
                  {"name": "End", "type": 3, "n": 1, "out": 0}],
        "links": [{"from": "Start", "to": "A"}, {"from": "A", "to": "Join"},
                  {"from": "Start", "to": "B"}, {"from": "B", "to": "Join"},
                  {"from": "Join", "to": "End"}],
        "phases": _phases(cells),
        "phase_states": {"B": list(cells)},
    }


def test_a_dependent_that_is_cut_while_it_is_idled_reads_idle_only_once_it_is_active_again() -> None:
    """A block that a dependence has idled while it is `Cut` reads `Cut`
    through the idling; the boundary that re-activates it is the instant
    `Idle` appears: `Cut -> Idle`.

    Why: two independent writers meet here. `BackFlowDependencies`' `Down` arm
    calls `ChangeBlockState(GOOD, IDLE, "Dependence_Idle")`,
   and because `B` is `Cut` at that moment
    `change_block_state` prints the active status, so the row is `Cut -> Cut`
    and `Idle` is *not* visible. Then the 80-hour boundary calls
    `ChangeBlockState(IDLE, ACTIVE, "Phase_Change")`
    (`cascade_phase.py:139-150`) -- here the new active status *is* `Active`,
    so the operating status is printed and the accumulated `Idle` shows up at
    last. This is the general rule behind every `X -> Idle` row on a phased
    dependent: the operating status is always one boundary late.

    Setup: two blocks into a 1-of-2 Join and on to the End node; `B` depends on
    `A` with the reaction split 100 % `DepIdle` (the deterministic member, so
    `draw_dependency_action` short-circuits it and the run draws nothing);
    `A` fails at 20 and repairs at 420. Five 20-hour phases with `B`'s cells
    `Active, Active, Cut, Cut, Active`.

    Expected: `B` is `Idle` from 20; at 40 the boundary Cuts it and it reads
    `Idle -> Cut`; at 80 the boundary re-activates it and reads `Cut -> Idle`
    -- the same `Idle` state, now with an `Active` status to be printed under.

    Source: raptor/core/engine/dependency.py (`BackFlowDependencies`' `Down`
    arm, the `DepIdle` reaction), raptor/core/engine/cascade_phase.py:131-150,
    raptor/core/engine/blocks.py:169-185; Raptor 7
    """
    result = run(_dependent([ACTIVE, ACTIVE, CUT, CUT, ACTIVE]))

    assert result.draws == 0, (
        "every distribution is Fixed, so B2 says this run must draw nothing; "
        f"it drew {result.draws}")

    assert_timeline(result, [
        (0.0, 'System', 'State_change', '--', 'Mission'),
        (0.0, 'Block', 'A', '--', 'Good'),
        (0.0, 'Block', 'B', '--', 'Good'),
        (20.0, 'Block', 'A', 'Good', 'Failed'),
        (20.0, 'System', 'Phase_change', '--', 'Phase_change'),
        (20.0, 'Block', 'A', 'Failed', 'Repairing'),
        (20.0, 'Block', 'B', 'Good', 'Idle'),
        (20.0, 'Node', 'Join', '--', 'Degraded'),
        (20.0, 'Node', 'Join', 'Degraded', 'Idle'),
        (20.0, 'Node', 'end', '--', 'Idle'),
        (20.0, 'System', 'State_change', 'Mission', 'Red'),
        (40.0, 'System', 'Phase_change', 'Phase_change', 'Phase_change'),
        (40.0, 'Block', 'B', 'Idle', 'Cut'),
        (40.0, 'Node', 'Join', 'Idle', 'Bad'),
        (40.0, 'Node', 'end', 'Idle', 'Bad'),
        (40.0, 'System', 'Red_change', '--', 'Red'),
        (60.0, 'System', 'Phase_change', 'Phase_change', 'Phase_change'),
        (80.0, 'System', 'Phase_change', 'Phase_change', 'Phase_change'),
        (80.0, 'Block', 'B', 'Cut', 'Idle'),
        (80.0, 'Node', 'Join', 'Bad', 'Idle'),
        (80.0, 'Node', 'end', 'Bad', 'Idle'),
        (80.0, 'System', 'Red_change', 'Red', 'Red'),
        (100.0, 'System', 'Phase_change', 'Phase_change', 'Phase_change'),
        (120.0, 'Block', 'A', 'Repairing', 'Done'),
        (120.0, 'Block', 'B', 'Idle', 'Done'),
        (120.0, 'System', 'Phase_change', 'Phase_change', 'Mission_failure'),
        (120.0, 'System', 'Simulation_Terminate', '--', '--'),
    ])


def test_a_boundary_that_links_an_idled_dependent_writes_linked_not_good() -> None:
    """`Idle -> Linked`: the boundary's own row wins over the dependent's
    wake, so the transition is `Idle -> Linked` with nothing between.

    Why: `apply_block`'s state-transition half runs before it interrupts the
    block's `Run` (`cascade_phase.py:131-164`), and for a block that is `Idle`
    the transition is `ChangeBlockState(IDLE, LINKED, "Phase_Change")`, which
    prints `LINKED`. `test_b_trans_phases.py` records the divergence it found
    the other way round in the `Linked -> Active` direction -- there the
    `Phase_Change` interrupt reaches the block's Idle wait first and the block
    wakes, redraws and logs `DependenceStress=... ` before the boundary's row
    lands, giving `Idle -> Good` then `Good -> Linked`. Both orders are real
    and both are ordering, not semantics: what decides them is which of the two
    same-instant entries the scheduler reaches first, and here the boundary's
    `ChangeBlockState` is the one that runs first.

    Setup: the same two-block model as the sibling, with `B`'s cells
    `Active, Active, Linked, Linked, Active` so the 40-hour boundary is the
    `Idle -> Linked` one and the 80-hour boundary is the reverse.

    Expected: `B` is `Idle` from 20; `Idle -> Linked` at 40 with the Join going
    `Idle -> Degraded` in the same row group (a `Linked` path counts for
    availability, so the Join is `Degraded` not `Idle`); `Linked -> Idle` at
    80 when `A` is still down.

    Source: raptor/core/engine/cascade_phase.py:131-164 (`apply_block`: the
    `ChangeBlockState` call and the `InterruptReason.PHASE_CHANGE` that
    follows it), raptor/core/engine/blocks.py:169-185;
    tests/behaviour/test_b_trans_phases.py (module docstring, the recorded
    `Idle -> Good` / `Good -> Linked` divergence)
    """
    result = run(_dependent([ACTIVE, ACTIVE, LINKED, LINKED, ACTIVE]))

    assert result.draws == 0, (
        "every distribution is Fixed, so B2 says this run must draw nothing; "
        f"it drew {result.draws}")

    assert_timeline(result, [
        (0.0, 'System', 'State_change', '--', 'Mission'),
        (0.0, 'Block', 'A', '--', 'Good'),
        (0.0, 'Block', 'B', '--', 'Good'),
        (20.0, 'Block', 'A', 'Good', 'Failed'),
        (20.0, 'System', 'Phase_change', '--', 'Phase_change'),
        (20.0, 'Block', 'A', 'Failed', 'Repairing'),
        (20.0, 'Block', 'B', 'Good', 'Idle'),
        (20.0, 'Node', 'Join', '--', 'Degraded'),
        (20.0, 'Node', 'Join', 'Degraded', 'Idle'),
        (20.0, 'Node', 'end', '--', 'Idle'),
        (20.0, 'System', 'State_change', 'Mission', 'Red'),
        (40.0, 'System', 'Phase_change', 'Phase_change', 'Phase_change'),
        (40.0, 'Block', 'B', 'Idle', 'Linked'),
        (40.0, 'Node', 'Join', 'Idle', 'Degraded'),
        (40.0, 'Node', 'end', 'Idle', 'Good'),
        (40.0, 'System', 'State_change', 'Red', 'Yellow'),
        (60.0, 'System', 'Phase_change', 'Phase_change', 'Phase_change'),
        (80.0, 'System', 'Phase_change', 'Phase_change', 'Phase_change'),
        (80.0, 'Block', 'B', 'Linked', 'Idle'),
        (80.0, 'Node', 'Join', 'Degraded', 'Idle'),
        (80.0, 'Node', 'end', 'Good', 'Idle'),
        (80.0, 'System', 'State_change', 'Yellow', 'Red'),
        (100.0, 'System', 'Phase_change', 'Phase_change', 'Phase_change'),
        (120.0, 'Block', 'A', 'Repairing', 'Done'),
        (120.0, 'Block', 'B', 'Idle', 'Done'),
        (120.0, 'System', 'Phase_change', 'Phase_change', 'Mission_failure'),
        (120.0, 'System', 'Simulation_Terminate', '--', '--'),
    ])


# ---------------------------------------------------------------------------
# A cold group whose running member is phased
# ---------------------------------------------------------------------------

def test_a_cold_group_member_is_switched_off_to_standby_while_cut() -> None:
    """`Cut -> Standby`: a cold group's running member, Cut at kickoff and
    re-activated at the same instant the group switches it off, reads
    `Standby`.

    Why: `run_cold_logic`'s `Too_many_on` arm calls
    `transition(block, STANDBY, "Too_many_on")` for every candidate that is
    `Running` but has lost its slot (`cold_standby.py:654-657`), and the
    transition it is handed is the same `ChangeBlockState` the phase
    machinery uses. The phase boundary at 80 is what restores the block to
    `Active`, and because that instant also re-runs the survey the demotion's
    `ChangeBlockState` is the *first* of the two to reach the block, so the
    boundary's own row and the demotion collapse into one -- `Cut -> Standby`,
    not `Cut -> Good` followed by `Good -> Standby`.

    **Why there is no `Linked -> Standby` sibling.** `change_block_state` logs
    `status_string` only when the new active status is `Active`
    (`blocks.py:169-185`), and `run_cold_logic`'s demotion is only ever handed
    `Active`: a demotion of a block that is still `Linked` prints `Linked`
    twice. `Cut -> Standby` is reachable precisely because the boundary and the
    demotion coincide, so there is an `Active` to print under. That is the
    whole constraint, and it is why this row is a fixture and its `Linked`
    counterpart is a reported non-reachability.

    Setup: `C` and `D` into a 1-of-1 `Cold` node (`cold=1`, `k*=1`) and on to
    the End node; `C` at cold priority 10 keeps the slot and `D` at 20 is
    switched to `Standby` at kickoff. `AProb = 1.0` short-circuits
    `SetSwitchTime`'s `UniformReal(0,1)` and `AutoTime = 0.0` resolves
    `switch_status` to `"ready"`, so the fixture draws nothing
    (`cold_standby.py:103-106`). `C` is phased `Active, Cut, Cut, Cut, Active`,
    which is what makes the 80-hour boundary land on a block the group has just
    re-surveyed.

    Expected: `D` is switched off at kickoff (`Good -> Standby`); at 20 `C` is
    Cut and `D` is switched **on** (`Standby -> Good`, `Prior=Standby`,
    `cold_standby.py:3195-3200` in source) which takes the cold node
    `Good -> Degraded` because the group now counts one standby path and one up
    path of two; at 80 the boundary re-activates `C` and the survey demotes it
    in the same instant -- `Cut -> Standby`.

    Source: raptor/core/engine/cold_standby.py:654-657 (the `Too_many_on`
    demotion), raptor/core/engine/cascade_phase.py:131-150,
    raptor/core/engine/blocks.py:169-185; Raptor 7
    """
    spec = {
        "system": {"run_length": 120.0, "num_runs": 1, "termination": "TimeSim"},
        "blocks": [_block("C", 50.0, 10.0), _block("D", 400.0, 5.0)],
        "nodes": [{"name": "Start", "type": 1, "n": 0, "out": 2},
                  {"name": "CD", "type": 2, "n": 2, "out": 1, "k": 1, "cold": 1,
                   "k_star": 1, "pr": 0, "autos": 0},
                  {"name": "End", "type": 3, "n": 1, "out": 0}],
        "links": [{"from": "Start", "to": "C"}, {"from": "Start", "to": "D"},
                  {"from": "C", "to": "CD", **_cold(10)},
                  {"from": "D", "to": "CD", **_cold(20)},
                  {"from": "CD", "to": "End"}],
        "phases": _phases([ACTIVE, CUT, CUT, CUT, ACTIVE]),
        "phase_states": {"C": [ACTIVE, CUT, CUT, CUT, ACTIVE]},
    }

    result = run(spec)

    assert result.draws == 0, (
        "every distribution is Fixed, so B2 says this run must draw nothing; "
        f"it drew {result.draws}")

    assert_timeline(result, [
        (0.0, 'System', 'State_change', '--', 'Mission'),
        (0.0, 'Block', 'C', '--', 'Good'),
        (0.0, 'Block', 'D', '--', 'Good'),
        # Kickoff demotion: two ready paths, one slot.
        (0.0, 'Block', 'D', 'Good', 'Standby'),
        (0.0, 'Node', 'CD', '--', 'Good'),
        (20.0, 'System', 'Phase_change', '--', 'Phase_change'),
        (20.0, 'Block', 'C', 'Good', 'Cut'),
        (20.0, 'Block', 'C', 'Cut', 'Cut'),
        # ... and the switch-on that the Cut frees a slot for.
        (20.0, 'Block', 'D', 'Standby', 'Good'),
        (20.0, 'Node', 'CD', 'Good', 'Degraded'),
        (40.0, 'System', 'Phase_change', 'Phase_change', 'Phase_change'),
        (60.0, 'System', 'Phase_change', 'Phase_change', 'Phase_change'),
        (80.0, 'System', 'Phase_change', 'Phase_change', 'Phase_change'),
        # Boundary and survey in the same instant: one row, and it is Standby.
        (80.0, 'Block', 'C', 'Cut', 'Standby'),
        (80.0, 'Node', 'CD', 'Degraded', 'Good'),
        (100.0, 'System', 'Phase_change', 'Phase_change', 'Phase_change'),
        (120.0, 'Block', 'C', 'Standby', 'Done'),
        (120.0, 'Block', 'D', 'Good', 'Done'),
        (120.0, 'System', 'Phase_change', 'Phase_change', 'Mission_success'),
        (120.0, 'System', 'Simulation_Terminate', '--', '--'),
    ])


# ---------------------------------------------------------------------------
# A node that counts one standby path, and the four ways it can leave
# ---------------------------------------------------------------------------

def _standby_path_model(n1_in: int, n1_k: int, blocks: list[dict[str, Any]],
                        extra_links: list[dict[str, Any]],
                        length: float = 120.0) -> dict[str, Any]:
    """A 1-of-1 cold group that puts one *dependent* path of `N1` on standby.

    `C` and `D` are a cold group; `D` is demoted to `Standby` at kickoff and
    `F`, which depends on `D`, follows it there -- that is the cheapest way to
    give a node a standby path without any manual switching, and it is the
    route `dependency.py:305-313` provides. `N1` is then a
    *k*-of-*n* node whose incoming links are `F` (the standby path) and
    whatever `extra_links` adds. `build_from_spec` walks `blocks` before
    `nodes`, so `DepNum` written as a name is resolved to the block's index.

    `C` never fails except where a fixture needs it to: if it did, `CD` would
    lose its last running path and cascade into `CD`'s own node-sourced cold
    link, which `test_b_node_standby.py`'s companion note records as needing a
    stream the node-to-node cascade does not thread.
    """
    ids = {block["name"]: index for index, block in enumerate(blocks, start=1)}
    ids.update({node["name"]: len(blocks) + index
                for index, node in enumerate(_STANDBY_NODES[:4], start=1)})
    resolved = [
        {**block, "dep_num": ids[block["dep_num"]]}
        if isinstance(block.get("dep_num"), str) else dict(block)
        for block in blocks
    ]
    nodes = [dict(node) for node in _STANDBY_NODES]
    nodes[3]["n"] = n1_in
    nodes[3]["k"] = n1_k
    nodes[0]["out"] = len([b for b in resolved if b["name"] in _FEEDS_START])
    return {"system": {"run_length": length, "num_runs": 1,
                       "termination": "TimeSim"},
            "blocks": resolved, "nodes": nodes,
            "links": _STANDBY_LINKS + extra_links}


_STANDBY_NODES = [
    {"name": "Start", "type": 1, "n": 0, "out": 0},
    {"name": "CD", "type": 2, "n": 2, "out": 1, "k": 1, "cold": 1, "k_star": 1,
     "pr": 0, "autos": 0},
    {"name": "N1", "type": 2, "n": 2, "out": 1, "k": 2},
    {"name": "End", "type": 3, "n": 1, "out": 0},
]

_STANDBY_LINKS = [
    {"from": "Start", "to": "C"}, {"from": "Start", "to": "D"},
    {"from": "Start", "to": "G"},
    {"from": "C", "to": "CD", **_cold(10)}, {"from": "D", "to": "CD", **_cold(20)},
    {"from": "CD", "to": "End"},
    {"from": "D", "to": "F"},
    {"from": "F", "to": "N1"}, {"from": "G", "to": "N1"},
    {"from": "N1", "to": "End"},
]

_FEEDS_START = {"C", "D", "G", "H"}

_ON_D = {"dep_type": "RBDBlock", "dep_idle": 0.0, "dep_nothing": 100.0,
         "dep_pm": 0.0, "dep_fail": 0.0}
_ON_C_IDLE = {"dep_type": "RBDBlock", "dep_idle": 100.0, "dep_nothing": 0.0,
              "dep_pm": 0.0, "dep_fail": 0.0}


def test_a_node_whose_only_up_path_goes_into_pm_leaves_standby_for_nodepm() -> None:
    """`Node Standby -> NodePM`: a 2-of-2 node counting one `Standby` path
    and one running path is `Standby`; when the running path enters PM the node
    is `NodePM`.

    Why: `determine_node_status` counts `runs`, `standbys`, `idles` and `pms`
    over the incoming links and picks `Standby` on the arm
    `runs + standbys >= good_paths_required` (after `runs >= k` has already
    failed) and `NodePM` on `pms + idles + runs + standbys >= k`
    (`nodes.py:278-295`). For `k=2`, `n=2` with one path up and one on
    standby that is `Standby` (1 < 2 <= 2); the running path entering PM moves
    one count out of `runs` and into `pms`, so the `Standby` arm's sum drops
    to 1 and the `NodePM` arm's rises to 2. One counter, one row.

    Setup: `_standby_path_model` -- the cold group `C`/`D` with `F` dependent on
    `D`, and `G` the second path of `N1`. `G` has a `Fixed` 60-hour `PMFreq`
    and a `Fixed` 2-hour PM. Four blocks, four nodes, one replication.

    Expected: `D` and then `F` are put on standby at kickoff and `N1` reads
    `Standby`; at 60 `G` enters PM and `N1` reads `NodePM` (and the End node
    `Degraded`, since `k=2` of 2 no longer has two good paths); at 62 the PM
    completes and `N1` is `Standby` again.

    Source: raptor/core/engine/nodes.py:254-299 (`determine_node_status`'s
    five arms), raptor/core/engine/dependency.py:305-313 (a dependent follows
    its target into `Standby`); Raptor 7
    """
    spec = _standby_path_model(
        2, 2,
        [_block("C", 400.0, 5.0), _block("D", 400.0, 5.0),
         _block("F", 400.0, 5.0, dep_num="D", **_ON_D),
         _block("G", 400.0, 5.0, uses_pm=True, pm_freq=60.0, pm_dist=FIXED,
                pm_params=[2.0], pm_refresh=False, pm_fail_reset=False)],
        [])

    result = run(spec)

    assert result.draws == 0, (
        "every distribution is Fixed, so B2 says this run must draw nothing; "
        f"it drew {result.draws}")

    assert_timeline(result, [
        (0.0, 'Block', 'C', '--', 'Good'),
        (0.0, 'Block', 'D', '--', 'Good'),
        (0.0, 'Block', 'F', '--', 'Good'),
        (0.0, 'Block', 'G', '--', 'Good'),
        (0.0, 'Block', 'D', 'Good', 'Standby'),
        (0.0, 'Block', 'F', 'Good', 'Standby'),
        (0.0, 'Node', 'N1', '--', 'Standby'),
        (0.0, 'Node', 'end', '--', 'Good'),
        (0.0, 'Node', 'CD', '--', 'Good'),
        (60.0, 'Block', 'G', 'Good', 'PM_is_due'),
        (60.0, 'Block', 'G', 'PM_is_due', 'PM'),
        (60.0, 'Node', 'N1', 'Standby', 'NodePM'),
        (60.0, 'Node', 'end', 'Good', 'Degraded'),
        (60.0, 'System', 'State_change', '--', 'Yellow'),
        (62.0, 'Block', 'G', 'PM', 'PM_complete'),
        (62.0, 'Block', 'G', 'PM_complete', 'Good'),
        (62.0, 'Node', 'N1', 'NodePM', 'Standby'),
        (62.0, 'Node', 'end', 'Degraded', 'Good'),
        (62.0, 'System', 'State_change', 'Yellow', 'Green'),
        (120.0, 'Block', 'C', 'Good', 'Done'),
        (120.0, 'Block', 'D', 'Standby', 'Done'),
        (120.0, 'Block', 'F', 'Standby', 'Done'),
        (120.0, 'Block', 'G', 'Good', 'Done'),
        (120.0, 'System', 'Phase_change', '--', 'Mission_success'),
        (120.0, 'System', 'Simulation_Terminate', '--', '--'),
    ])


def test_a_node_whose_only_up_path_is_idled_leaves_standby_for_idle() -> None:
    """`Node Standby -> Idle`: the same 2-of-2 node, with the running path
    idled by a dependence instead of put into PM.

    Why: the fourth arm, `idles + runs + standbys >= k` (`nodes.py:292-293`),
    sits *below* the `Standby` arm, so it is reachable from `Standby` only by
    taking a count out of the standby bucket's competitor -- the running path.
    `BackFlowDependencies`' `Down` arm calls
    `ChangeBlockState(GOOD, IDLE, "Dependence_Idle")` for a `DepIdle` reaction,
   and `determine_node_status` then reads
    `runs=0, standbys=1, idles=1, k=2`, which is `Idle`.

    Setup: the same model, with `G` depending on `C` with the split 100 %
    `DepIdle` (the deterministic member, so no draw) and `C` failing at 50 with
    a 200-hour repair so the idling outlives the rest of the run.

    Expected: `N1` is `Standby` from kickoff; at 50 `G` is idled and `N1` reads
    `Idle` -- and reads it twice, because `CD`'s switch-on in the same instant
    releases both `D` and `F`, which moves a second count out of `standbys`
    and leaves the node on the same arm.

    Source: raptor/core/engine/nodes.py:254-299,
    raptor/core/engine/dependency.py (`DepIdle` reaction, the `Down` arm),
    raptor/core/engine/cold_standby.py:621-644; Raptor 7
    """
    spec = _standby_path_model(
        2, 2,
        [_block("C", 50.0, 200.0), _block("D", 400.0, 5.0),
         _block("F", 400.0, 5.0, dep_num="D", **_ON_D),
         _block("G", 400.0, 5.0, dep_num="C", **_ON_C_IDLE)],
        [])

    result = run(spec)

    assert result.draws == 0, (
        "every distribution is Fixed, so B2 says this run must draw nothing; "
        f"it drew {result.draws}")

    assert_timeline(result, [
        (0.0, 'Block', 'C', '--', 'Good'),
        (0.0, 'Block', 'D', '--', 'Good'),
        (0.0, 'Block', 'F', '--', 'Good'),
        (0.0, 'Block', 'G', '--', 'Good'),
        (0.0, 'Block', 'D', 'Good', 'Standby'),
        (0.0, 'Block', 'F', 'Good', 'Standby'),
        (0.0, 'Node', 'N1', '--', 'Standby'),
        (0.0, 'Node', 'end', '--', 'Good'),
        (0.0, 'Node', 'CD', '--', 'Good'),
        (50.0, 'Block', 'C', 'Good', 'Failed'),
        (50.0, 'Block', 'C', 'Failed', 'Repairing'),
        (50.0, 'Block', 'G', 'Good', 'Idle'),
        (50.0, 'Node', 'N1', 'Standby', 'Idle'),
        (50.0, 'Node', 'end', 'Good', 'Degraded'),
        (50.0, 'Block', 'D', 'Standby', 'Good'),
        (50.0, 'Block', 'F', 'Standby', 'Good'),
        (50.0, 'Node', 'N1', 'Idle', 'Idle'),
        (50.0, 'Node', 'CD', 'Good', 'Degraded'),
        (50.0, 'System', 'State_change', '--', 'Yellow'),
        (120.0, 'Block', 'C', 'Repairing', 'Done'),
        (120.0, 'Block', 'D', 'Good', 'Done'),
        (120.0, 'Block', 'F', 'Good', 'Done'),
        (120.0, 'Block', 'G', 'Idle', 'Done'),
        (120.0, 'System', 'Phase_change', '--', 'Mission_success'),
        (120.0, 'System', 'Simulation_Terminate', '--', '--'),
    ])


def test_a_node_whose_only_up_path_fails_leaves_standby_for_bad() -> None:
    """`Node Standby -> Bad`: the same 2-of-2 node, with the running path
    failed.

    Why: the last arm, `fails > 0` (`nodes.py:296-297`), is the only one
    `Standby` can fall through to, because every arm above it needs a count the
    node no longer has. `runs=0, standbys=1, fails=1, k=2` satisfies none of
    `runs >= k`, `runs + standbys >= k`, `idles + runs + standbys >= k` or
    `pms + idles + runs + standbys >= k`, so `Bad` it is -- which is also why
    this is the row the corpus reaches from a `Standby` node and not from a
    `Good` one.

    Setup: the same model with `G` given a `Fixed` 60-hour life and a 5-hour
    repair, so `N1` comes back to `Standby` at 65 and the row is seen as a pair.

    Expected: `N1` is `Standby` from kickoff; `Standby -> Bad` at 60 and
    `Bad -> Standby` at 65.

    Source: raptor/core/engine/nodes.py:254-299,
    raptor/core/engine/blocks.py:113-122 (the `Failed` row); Raptor 7
    """
    spec = _standby_path_model(
        2, 2,
        [_block("C", 400.0, 5.0), _block("D", 400.0, 5.0),
         _block("F", 400.0, 5.0, dep_num="D", **_ON_D),
         _block("G", 60.0, 5.0)],
        [])

    result = run(spec)

    assert result.draws == 0, (
        "every distribution is Fixed, so B2 says this run must draw nothing; "
        f"it drew {result.draws}")

    assert_timeline(result, [
        (0.0, 'Block', 'C', '--', 'Good'),
        (0.0, 'Block', 'D', '--', 'Good'),
        (0.0, 'Block', 'F', '--', 'Good'),
        (0.0, 'Block', 'G', '--', 'Good'),
        (0.0, 'Block', 'D', 'Good', 'Standby'),
        (0.0, 'Block', 'F', 'Good', 'Standby'),
        (0.0, 'Node', 'N1', '--', 'Standby'),
        (0.0, 'Node', 'end', '--', 'Good'),
        (0.0, 'Node', 'CD', '--', 'Good'),
        (60.0, 'Block', 'G', 'Good', 'Failed'),
        (60.0, 'Block', 'G', 'Failed', 'Repairing'),
        (60.0, 'Node', 'N1', 'Standby', 'Bad'),
        (60.0, 'Node', 'end', 'Good', 'Degraded'),
        (60.0, 'System', 'State_change', '--', 'Yellow'),
        (65.0, 'Block', 'G', 'Repairing', 'Repaired'),
        (65.0, 'Block', 'G', 'Repaired', 'Good'),
        (65.0, 'Node', 'N1', 'Bad', 'Standby'),
        (65.0, 'Node', 'end', 'Degraded', 'Good'),
        (65.0, 'System', 'State_change', 'Yellow', 'Green'),
        (120.0, 'Block', 'C', 'Good', 'Done'),
        (120.0, 'Block', 'D', 'Standby', 'Done'),
        (120.0, 'Block', 'F', 'Standby', 'Done'),
        (120.0, 'Block', 'G', 'Good', 'Done'),
        (120.0, 'System', 'Phase_change', '--', 'Mission_success'),
        (120.0, 'System', 'Simulation_Terminate', '--', '--'),
    ])


def test_a_node_in_standby_becomes_degraded_when_its_standby_path_comes_up() -> None:
    """`Node Standby -> Degraded`: a 2-of-3 node counting one up, one standby
    and one failed path is `Standby`; when the standby path comes up it is
    `Degraded`, because three paths are needed and only two are up.

    Why: the `Degraded` arm is `runs >= k` **and** `runs + standbys <
    resolved_in_link_count` (`nodes.py:285-289`), so reaching it from `Standby` --
    where `runs < k` -- needs the count of runs to *rise*, and the only step
    that raises it is a standby path coming up. That step also takes one out of
    `standbys`, so `runs + standbys` is unchanged; with `k=2, n=3` the pair
    `runs=1, standbys=1` satisfies `runs + standbys >= k` (so `Standby`) and,
    after the promotion, `runs=2, standbys=0` satisfies `runs >= k` while
    `2 < 3` still holds (so `Degraded`). This is the only shape of the row: a
    node with `k == n` cannot be in `Standby` and then `Degraded`, because
    promoting its standby path would take it to `Good`.

    Setup: `_standby_path_model` with `N1` widened to a 2-of-3 and `H` added as
    the third path, with a `Fixed` 20-hour life and a 400-hour repair so `H` is
    down for the rest of the run. `C` fails at 60, which switches `D` on,
    releases `F`, and promotes the standby path. Five blocks, four nodes.

    Expected: `N1` reads `Good` at kickoff and `Standby` at 20 (when `H` fails,
    `runs + standbys = 2 >= k`); at 60 `C` fails, `D` and `F` are released, and
    `N1` reads `Degraded`.

    Source: raptor/core/engine/nodes.py:254-299 (`determine_node_status`, the
    `Degraded` arm and the arm order), raptor/core/engine/cold_standby.py:621-644;
    """
    spec = _standby_path_model(
        3, 2,
        [_block("C", 60.0, 5.0), _block("D", 400.0, 5.0),
         _block("F", 400.0, 5.0, dep_num="D", **_ON_D),
         _block("G", 400.0, 5.0), _block("H", 20.0, 400.0)],
        [{"from": "Start", "to": "H"}, {"from": "H", "to": "N1"}])

    result = run(spec)

    assert result.draws == 0, (
        "every distribution is Fixed, so B2 says this run must draw nothing; "
        f"it drew {result.draws}")

    assert_timeline(result, [
        (0.0, 'Block', 'C', '--', 'Good'),
        (0.0, 'Block', 'D', '--', 'Good'),
        (0.0, 'Block', 'F', '--', 'Good'),
        (0.0, 'Block', 'G', '--', 'Good'),
        (0.0, 'Block', 'H', '--', 'Good'),
        (0.0, 'Block', 'D', 'Good', 'Standby'),
        (0.0, 'Block', 'F', 'Good', 'Standby'),
        (0.0, 'Node', 'N1', '--', 'Good'),
        (0.0, 'Node', 'CD', '--', 'Good'),
        (20.0, 'Block', 'H', 'Good', 'Failed'),
        (20.0, 'Block', 'H', 'Failed', 'Repairing'),
        (20.0, 'Node', 'N1', 'Good', 'Standby'),
        (20.0, 'Node', 'end', '--', 'Good'),
        (20.0, 'System', 'State_change', '--', 'Yellow'),
        (60.0, 'Block', 'C', 'Good', 'Failed'),
        (60.0, 'Block', 'C', 'Failed', 'Repairing'),
        (60.0, 'Block', 'D', 'Standby', 'Good'),
        (60.0, 'Block', 'F', 'Standby', 'Good'),
        (60.0, 'Node', 'N1', 'Standby', 'Degraded'),
        (60.0, 'Node', 'end', 'Good', 'Good'),
        (60.0, 'Node', 'CD', 'Good', 'Degraded'),
        (65.0, 'Block', 'C', 'Repairing', 'Repaired'),
        (65.0, 'Block', 'C', 'Repaired', 'Good'),
        (65.0, 'Block', 'C', 'Good', 'Standby'),
        (65.0, 'Node', 'CD', 'Degraded', 'Good'),
        (120.0, 'Block', 'C', 'Standby', 'Done'),
        (120.0, 'Block', 'D', 'Good', 'Done'),
        (120.0, 'Block', 'F', 'Good', 'Done'),
        (120.0, 'Block', 'G', 'Good', 'Done'),
        (120.0, 'Block', 'H', 'Repairing', 'Done'),
        (120.0, 'System', 'Phase_change', '--', 'Mission_success'),
        (120.0, 'System', 'Simulation_Terminate', '--', '--'),
    ])


# ---------------------------------------------------------------------------
# pmMisDefer: the resource hand-back that logs `Resources` twice
# ---------------------------------------------------------------------------

def test_a_pm_that_mis_defers_gives_its_resource_back_and_logs_resources_twice() -> None:
    """`Resources -> Resources`: a `pmMisDefer` block whose PM resource request
    was deferred writes `Resources Obtained_1_Resource(s)` when the unit
    arrives and `Resources Returned_1_Resource(s)` in the same instant when the
    mission postpones the PM -- and the block then has no life process at all,
    so the run ends with `Resources -> Done`.

    Why: `ObtainPMprereqs`' `pmMisDefer` tail on the mission branch hands the
    resource straight back (ported as
    `_misdefer_give_back`, `block_lifecycle.py:2796-2830`), and each of the
    obtain and the give-back writes its own raw `Resources` row with a
    different detail string. The give-back also leaves the block with no `Run`
    to restart it -- source has no `Run;` of its own on that branch, so the
    block never fails or PMs again and is flushed `Done` at the end of the run
    (`LadL8A` run 22 is the capture that settled this). That is why the
    `Returned` row is the block's *last* row and the `Done` row chains from
    `Resources`.

    Setup: two blocks into a 1-of-2 Join and on to the End node. `A` has a
    `Fixed` 20-hour `PMFreq`, `pmReqDefer` and `pmMisDefer` set, and needs one
    unit of the one-unit `R` pool for its PM; `B` borrows `R` from 10 to 70.
    Three phases: 50 hours mission, 50 non-mission, 100 mission. The first
    deferral is released at 70 while phase 2 is non-mission, so that PM goes
    ahead and completes; the second is released at 140, inside the mission, and
    is the one that gives the resource back. 200 sim hours, one replication.

    Expected: `A` defers at 50, is granted `R` at 70 and PMs to 72; it defers
    again at 92 and is granted `R` at 140 -- in a mission -- so both
    `Resources` rows land in the same instant (`Resources -> Resources`) and
    the block goes quiet; at 200 the run-end flush writes
    `Resources -> Done`.

    Source: raptor/core/engine/block_lifecycle.py:2796-2830
    (`_misdefer_give_back`, the `Returned_N_Resource(s)` row)
    (the `Obtained_N_Resource(s)` row on the deferred request's release),
    raptor/core/engine/block_lifecycle.py:2319-2336 (the mission postponement
    that consumes the `Run`); Raptor 7
    """
    spec = {
        "system": {"run_length": 200.0, "num_runs": 1, "termination": "TimeSim"},
        "blocks": [
            _block("A", 400.0, 5.0, uses_pm=True, pm_freq=20.0, pm_dist=FIXED,
                   pm_params=[2.0], pm_refresh=False, pm_fail_reset=False,
                   pm_req_defer=True, pm_mis_defer=True,
                   num_res=1, res1_pm_rep=1, resource_name="R"),
            _block("B", 10.0, 60.0, num_res=1, resource_name="R"),
        ],
        "resources": [{"name": "R", "num_in": 1}],
        "nodes": [{"name": "Start", "type": 1, "n": 0, "out": 2},
                  {"name": "Join", "type": 2, "n": 2, "out": 1, "k": 1},
                  {"name": "End", "type": 3, "n": 1, "out": 0}],
        "links": [{"from": "Start", "to": "A"}, {"from": "A", "to": "Join"},
                  {"from": "Start", "to": "B"}, {"from": "B", "to": "Join"},
                  {"from": "Join", "to": "End"}],
        "phases": [{"name": "P1", "mission": 1, "dist": FIXED, "params": [50.0]},
                   {"name": "P2", "mission": 0, "dist": FIXED, "params": [50.0]},
                   {"name": "P3", "mission": 1, "dist": FIXED, "params": [100.0]}],
    }

    result = run(spec)

    assert result.draws == 0, (
        "every distribution is Fixed, so B2 says this run must draw nothing; "
        f"it drew {result.draws}")

    assert_timeline(result, [
        (0.0, 'System', 'State_change', '--', 'Mission'),
        (0.0, 'Block', 'A', '--', 'Good'),
        (0.0, 'Block', 'B', '--', 'Good'),
        (10.0, 'Block', 'B', 'Good', 'Failed'),
        (10.0, 'Block', 'B', 'Failed', 'Repairing'),
        (10.0, 'Node', 'Join', '--', 'Degraded'),
        (10.0, 'System', 'State_change', 'Mission', 'Yellow'),
        (20.0, 'Block', 'A', 'Good', 'PM_is_due'),
        (50.0, 'System', 'Phase_change', '--', 'Phase_change'),
        (50.0, 'System', 'Phase_change', 'Phase_change', 'Mission_success'),
        (50.0, 'System', 'State_change', 'Yellow', 'Yellow'),
        (50.0, 'Block', 'A', 'PM_is_due', 'PM_is_due'),
        (50.0, 'Block', 'A', 'PM_is_due', 'pmReqDefer'),
        (70.0, 'Block', 'B', 'Repairing', 'Repaired'),
        (70.0, 'Block', 'B', 'Repaired', 'Good'),
        (70.0, 'Node', 'Join', 'Degraded', 'Good'),
        # Non-mission: the PM goes ahead.
        (70.0, 'Block', 'A', 'pmReqDefer', 'Resources'),
        (70.0, 'Block', 'A', 'Resources', 'PM'),
        (70.0, 'Node', 'Join', 'Good', 'Degraded'),
        (72.0, 'Block', 'A', 'PM', 'PM_complete'),
        (72.0, 'Block', 'A', 'PM_complete', 'Good'),
        (72.0, 'Node', 'Join', 'Degraded', 'Good'),
        (72.0, 'System', 'State_change', 'Yellow', 'Green'),
        (80.0, 'Block', 'B', 'Good', 'Failed'),
        (80.0, 'Block', 'B', 'Failed', 'Repairing'),
        (80.0, 'Node', 'Join', 'Good', 'Degraded'),
        (80.0, 'System', 'State_change', 'Green', 'Yellow'),
        (92.0, 'Block', 'A', 'Good', 'PM_is_due'),
        (92.0, 'Block', 'A', 'PM_is_due', 'pmReqDefer'),
        (100.0, 'System', 'Phase_change', 'Mission_success', 'Phase_change'),
        (100.0, 'System', 'Phase_change', 'Phase_change', 'Mission'),
        (100.0, 'System', 'State_change', 'Yellow', 'Yellow'),
        (140.0, 'Block', 'B', 'Repairing', 'Repaired'),
        (140.0, 'Block', 'B', 'Repaired', 'Good'),
        (140.0, 'Node', 'Join', 'Degraded', 'Good'),
        # Mission: obtained and handed straight back in the same instant.
        (140.0, 'Block', 'A', 'pmReqDefer', 'Resources'),
        (140.0, 'Block', 'A', 'Resources', 'Resources'),
        (140.0, 'System', 'State_change', 'Yellow', 'Green'),
        (150.0, 'Block', 'B', 'Good', 'Failed'),
        (150.0, 'Block', 'B', 'Failed', 'Repairing'),
        (150.0, 'Node', 'Join', 'Good', 'Degraded'),
        (150.0, 'System', 'State_change', 'Green', 'Yellow'),
        # The give-back consumed A's Run, so the run end is its next event.
        (200.0, 'Block', 'A', 'Resources', 'Done'),
        (200.0, 'Block', 'B', 'Repairing', 'Done'),
        (200.0, 'System', 'Phase_change', 'Mission', 'Mission_success'),
        (200.0, 'System', 'Simulation_Terminate', '--', '--'),
    ])


# ---------------------------------------------------------------------------
# The PM's own two holding states, read through the active status
# ---------------------------------------------------------------------------

def _pm_resource(cells: list[str], *, defer: bool, length: float = 120.0,
                 b_fail: float = 10.0, b_repair: float = 60.0) -> dict[str, Any]:
    """`A` has a periodic PM that needs one unit of the one-unit `R` pool;
    `B` holds `R` from *b_fail* to ``b_fail + b_repair``, which is well past
    the boundary these fixtures need.

    `pm_req_defer` is the whole difference between the two behaviours: set, the
    PM attempt is abandoned with a hand-built `pmReqDefer` row and the block
    carries on running; clear, the block holds in `PM_HOLD` until the unit is
    free. Two blocks, three nodes, one replication, every distribution `Fixed`.
    """
    return {
        "system": {"run_length": length, "num_runs": 1, "termination": "TimeSim"},
        "blocks": [
            _block("A", 400.0, 5.0, uses_pm=True, pm_freq=20.0, pm_dist=FIXED,
                   pm_params=[2.0], pm_refresh=False, pm_fail_reset=False,
                   pm_req_defer=defer, num_res=1, res1_pm_rep=1,
                   resource_name="R"),
            _block("B", b_fail, b_repair, num_res=1, resource_name="R"),
        ],
        "resources": [{"name": "R", "num_in": 1}],
        "nodes": [{"name": "Start", "type": 1, "n": 0, "out": 2},
                  {"name": "Join", "type": 2, "n": 2, "out": 1, "k": 1},
                  {"name": "End", "type": 3, "n": 1, "out": 0}],
        "links": [{"from": "Start", "to": "A"}, {"from": "A", "to": "Join"},
                  {"from": "Start", "to": "B"}, {"from": "B", "to": "Join"},
                  {"from": "Join", "to": "End"}],
        "phases": _phases(cells),
        "phase_states": {"A": list(cells)},
    }


def test_a_linked_block_holding_for_its_pm_resource_reads_pmhold_only_once_it_is_active() -> None:
    """`Linked -> pmHold`: a `Linked` block whose PM cannot have its resource
    holds invisibly; the boundary that re-activates it is the instant `pmHold`
    appears.

    Why: `ObtainPMprereqs`' resource step calls
    `ChangeBlockState(RUNNING, PM_HOLD, "Waiting_for_resource(s)")` when the
    request is not granted, and `change_block_state`
    prints the *active* status whenever that status is not `Active`
    (`blocks.py:169-185`) -- so the row is `PM_is_due -> Linked` and the
    `PM_HOLD` is invisible until something hands the block an `Active` status.
    `pmReqDefer` is clear here, which is what makes it a hold rather than a
    deferral (`block_lifecycle.py:2945-2953`).

    Setup: `_pm_resource` with `pm_req_defer` clear, `A`'s cells
    `Linked, Linked, Active, Active, Active` and `B` holding `R` from 10 to 70
    -- past the 40-hour boundary.

    Expected: the PM is due at 20 and holds (`PM_is_due -> Linked`); at 40 the
    boundary re-activates the block and reads `Linked -> pmHold`, which also
    takes the Join and the End node to `NodePM`; at 70 `B` hands `R` back and
    `A` logs `pmHold -> Resources` and goes on to its PM; at 100 the boundary
    takes it back to `Linked`, so the second `pmHold` reads `pmHold -> Linked`.

    Source: raptor/core/engine/block_lifecycle.py:2945-2971
    (`ObtainPMprereqs`' resource step: the `PM_HOLD` transition and the
    `Obtained_N_Resource(s)` row), raptor/core/engine/cascade_phase.py:131-150,
    raptor/core/engine/blocks.py:169-185; Raptor 7
    """
    result = run(_pm_resource([LINKED, LINKED, ACTIVE, ACTIVE, ACTIVE],
                              defer=False))

    assert result.draws == 0, (
        "every distribution is Fixed, so B2 says this run must draw nothing; "
        f"it drew {result.draws}")

    assert_timeline(result, [
        (0.0, 'System', 'State_change', '--', 'Mission'),
        (0.0, 'Block', 'A', '--', 'Linked'),
        (0.0, 'Block', 'B', '--', 'Good'),
        (10.0, 'Block', 'B', 'Good', 'Failed'),
        (10.0, 'Block', 'B', 'Failed', 'Repairing'),
        (10.0, 'Node', 'Join', '--', 'Degraded'),
        (10.0, 'System', 'State_change', 'Mission', 'Yellow'),
        (20.0, 'Block', 'A', 'Linked', 'PM_is_due'),
        (20.0, 'System', 'Phase_change', '--', 'Phase_change'),
        # PM_HOLD entered while Linked: invisible.
        (20.0, 'Block', 'A', 'PM_is_due', 'Linked'),
        (40.0, 'System', 'Phase_change', 'Phase_change', 'Phase_change'),
        (40.0, 'Block', 'A', 'Linked', 'pmHold'),
        (40.0, 'Node', 'Join', 'Degraded', 'NodePM'),
        (40.0, 'Node', 'end', '--', 'NodePM'),
        (40.0, 'System', 'State_change', 'Yellow', 'Red'),
        (60.0, 'System', 'Phase_change', 'Phase_change', 'Phase_change'),
        (70.0, 'Block', 'B', 'Repairing', 'Repaired'),
        (70.0, 'Block', 'B', 'Repaired', 'Good'),
        (70.0, 'Node', 'Join', 'NodePM', 'Degraded'),
        (70.0, 'Node', 'end', 'NodePM', 'Good'),
        (70.0, 'Block', 'A', 'pmHold', 'Resources'),
        (70.0, 'Block', 'A', 'Resources', 'PM'),
        (70.0, 'System', 'State_change', 'Red', 'Yellow'),
        (72.0, 'Block', 'A', 'PM', 'PM_complete'),
        (72.0, 'Block', 'A', 'PM_complete', 'Good'),
        (72.0, 'Node', 'Join', 'Degraded', 'Good'),
        (72.0, 'System', 'State_change', 'Yellow', 'Green'),
        (80.0, 'Block', 'B', 'Good', 'Failed'),
        (80.0, 'System', 'Phase_change', 'Phase_change', 'Phase_change'),
        (80.0, 'Block', 'B', 'Failed', 'Repairing'),
        (80.0, 'Node', 'Join', 'Good', 'Degraded'),
        (80.0, 'System', 'State_change', 'Green', 'Yellow'),
        (92.0, 'Block', 'A', 'Good', 'PM_is_due'),
        (92.0, 'Block', 'A', 'PM_is_due', 'pmHold'),
        (92.0, 'Node', 'Join', 'Degraded', 'NodePM'),
        (92.0, 'Node', 'end', 'Good', 'NodePM'),
        (92.0, 'System', 'State_change', 'Yellow', 'Red'),
        (100.0, 'System', 'Phase_change', 'Phase_change', 'Phase_change'),
        (100.0, 'Block', 'A', 'pmHold', 'Linked'),
        (100.0, 'Node', 'Join', 'NodePM', 'Degraded'),
        (100.0, 'Node', 'end', 'NodePM', 'Good'),
        (100.0, 'System', 'State_change', 'Red', 'Yellow'),
        (120.0, 'Block', 'A', 'Linked', 'Linked'),
        (120.0, 'Block', 'B', 'Repairing', 'Done'),
        (120.0, 'System', 'Phase_change', 'Phase_change', 'Mission_failure'),
        (120.0, 'System', 'Simulation_Terminate', '--', '--'),
    ])


def test_a_deferred_pm_request_reads_good_the_instant_a_boundary_reactivates_the_block() -> None:
    """`pmReqDefer -> Good`: the PM request is abandoned rather than held, and
    the boundary that re-activates the block in the same instant is what makes
    the still-running block read `Good`.

    Why: `pmReqDefer` is not a state change at all -- source writes a
    hand-built `WriteEvents(..., "pmReqDefer", "No_resource_in_stock-<pool>")`
    row and calls `Run;` again with `FailTimeLeft` untouched
    (ported at
    `block_lifecycle.py:2917-2944`), so the block's operating status is
    unchanged by the deferral and no `ChangeBlockState` follows it. The next
    `ChangeBlockState` the block *does* get is the boundary's, and because the
    block is `Cut` at that moment it would print `Cut`; what makes it print
    `Good` is that the boundary's own transition is handed `Active` as the new
    active status (`cascade_phase.py:139-150`). One row, and the row says
    `Good` -- which is also the reason `pmReqDefer` is worth pinning: it is
    the corpus's proof that the deferral left the block running.

    Setup: `_pm_resource` with `pm_req_defer` set, `A`'s cells
    `Cut, Active, Active, Active` so the boundary and the deferral land in the
    same instant, and `B` holding `R` from 10 to 70.

    Expected: `A` is `Cut` from kickoff; at 20 the PM comes due, defers, and is
    re-activated -- `Cut -> PM_is_due`, `PM_is_due -> pmReqDefer`,
    `pmReqDefer -> Good`; at 70 the abandoned request's unit comes back and the
    PM runs after all (`Good -> Resources`, `Resources -> PM`).

    Source: raptor/core/engine/block_lifecycle.py:2912-2944 (the
    `pmReqDefer` row and the abandon), raptor/core/engine/cascade_phase.py:131-150,
    raptor/core/engine/blocks.py:169-185; Raptor 7
    """
    result = run(_pm_resource([CUT, ACTIVE, ACTIVE, ACTIVE], defer=True,
                              length=100.0))

    assert result.draws == 0, (
        "every distribution is Fixed, so B2 says this run must draw nothing; "
        f"it drew {result.draws}")

    assert_timeline(result, [
        (0.0, 'System', 'State_change', '--', 'Mission'),
        (0.0, 'Block', 'A', '--', 'Cut'),
        (0.0, 'Node', 'Join', '--', 'Degraded'),
        (0.0, 'Block', 'B', '--', 'Good'),
        (10.0, 'Block', 'B', 'Good', 'Failed'),
        (10.0, 'Block', 'B', 'Failed', 'Repairing'),
        (10.0, 'Node', 'Join', 'Degraded', 'Bad'),
        (10.0, 'Node', 'end', '--', 'Bad'),
        (10.0, 'System', 'State_change', 'Mission', 'Red'),
        (20.0, 'Block', 'A', 'Cut', 'PM_is_due'),
        (20.0, 'System', 'Phase_change', '--', 'Phase_change'),
        (20.0, 'Block', 'A', 'PM_is_due', 'pmReqDefer'),
        # Re-activated in the same instant: still Running, so `Good`.
        (20.0, 'Block', 'A', 'pmReqDefer', 'Good'),
        (20.0, 'Node', 'Join', 'Bad', 'Degraded'),
        (20.0, 'Node', 'end', 'Bad', 'Good'),
        (20.0, 'System', 'State_change', 'Red', 'Yellow'),
        (40.0, 'System', 'Phase_change', 'Phase_change', 'Phase_change'),
        (60.0, 'System', 'Phase_change', 'Phase_change', 'Phase_change'),
        (70.0, 'Block', 'B', 'Repairing', 'Repaired'),
        (70.0, 'Block', 'B', 'Repaired', 'Good'),
        (70.0, 'Node', 'Join', 'Degraded', 'Good'),
        (70.0, 'Block', 'A', 'Good', 'Resources'),
        (70.0, 'Block', 'A', 'Resources', 'PM'),
        (70.0, 'Node', 'Join', 'Good', 'Degraded'),
        (72.0, 'Block', 'A', 'PM', 'PM_complete'),
        (72.0, 'Block', 'A', 'PM_complete', 'Good'),
        (72.0, 'Node', 'Join', 'Degraded', 'Good'),
        (72.0, 'System', 'State_change', 'Yellow', 'Green'),
        (80.0, 'Block', 'B', 'Good', 'Failed'),
        (80.0, 'System', 'Phase_change', 'Phase_change', 'Phase_change'),
        (80.0, 'Block', 'B', 'Failed', 'Repairing'),
        (80.0, 'Node', 'Join', 'Good', 'Degraded'),
        (80.0, 'Block', 'A', 'Good', 'Cut'),
        (80.0, 'Node', 'Join', 'Degraded', 'Bad'),
        (80.0, 'Node', 'end', 'Good', 'Bad'),
        (80.0, 'System', 'State_change', 'Green', 'Red'),
        (92.0, 'Block', 'A', 'Cut', 'PM_is_due'),
        (92.0, 'Block', 'A', 'PM_is_due', 'pmReqDefer'),
        (100.0, 'Block', 'A', 'pmReqDefer', 'Cut'),
        (100.0, 'Block', 'B', 'Repairing', 'Done'),
        (100.0, 'System', 'Phase_change', 'Phase_change', 'Mission_failure'),
        (100.0, 'System', 'Simulation_Terminate', '--', '--'),
    ])


def test_a_pm_resource_granted_to_a_cut_block_reads_good_when_a_boundary_reactivates_it() -> None:
    """`Resources -> Good`: the PM's `Obtained_1_Resource(s)` row is followed at
    once by the boundary's own `Good`, because the deferred request's unit is
    released in the same instant the boundary re-activates the block.

    Why: the sibling of the `pmReqDefer -> Good` test one step later in the
    same sequence. `ObtainPMprereqs` writes
    `WriteEvents(..., "Resources", "Obtained_1_Resource(s)")` when the unit it
    requested arrives (the row itself is
    `block_lifecycle.py:2955-2958`), and then `PerformPM` transitions to `PM`.
    Here the boundary's `ChangeBlockState` is reached before that, with `Active`
    as the new active status, so the row after `Resources` is the block's
    operating status -- `Good` -- rather than `PM`.

    Setup: `_pm_resource` with `pm_req_defer` set, `A`'s cells
    `Cut, Cut, Active, Active`, so the deferral is at 20 and both the boundary
    and the release of `R` are at 40.

    Expected: `Cut -> PM_is_due` at 20 with `pmReqDefer` and nothing after it;
    at 40 the boundary re-activates the block (`pmReqDefer -> Good`) and the
    unit arrives, so `Good -> Resources` and `Resources -> Good` both land at
    40, the first from the boundary and the second from `PerformPM`.

    Source: raptor/core/engine/block_lifecycle.py:2945-2971 (the
    `Obtained_N_Resource(s)` row), raptor/core/engine/cascade_phase.py:131-150,
    raptor/core/engine/blocks.py:169-185; Raptor 7
    """
    result = run(_pm_resource([CUT, CUT, ACTIVE, ACTIVE], defer=True,
                              length=100.0, b_repair=30.0))

    assert result.draws == 0, (
        "every distribution is Fixed, so B2 says this run must draw nothing; "
        f"it drew {result.draws}")

    assert_timeline(result, [
        (0.0, 'System', 'State_change', '--', 'Mission'),
        (0.0, 'Block', 'A', '--', 'Cut'),
        (0.0, 'Node', 'Join', '--', 'Degraded'),
        (0.0, 'Block', 'B', '--', 'Good'),
        (10.0, 'Block', 'B', 'Good', 'Failed'),
        (10.0, 'Block', 'B', 'Failed', 'Repairing'),
        (10.0, 'Node', 'Join', 'Degraded', 'Bad'),
        (10.0, 'Node', 'end', '--', 'Bad'),
        (10.0, 'System', 'State_change', 'Mission', 'Red'),
        (20.0, 'Block', 'A', 'Cut', 'PM_is_due'),
        (20.0, 'System', 'Phase_change', '--', 'Phase_change'),
        (20.0, 'Block', 'A', 'PM_is_due', 'pmReqDefer'),
        (40.0, 'Block', 'B', 'Repairing', 'Repaired'),
        (40.0, 'Block', 'B', 'Repaired', 'Good'),
        (40.0, 'Node', 'Join', 'Bad', 'Degraded'),
        (40.0, 'Node', 'end', 'Bad', 'Good'),
        (40.0, 'System', 'Phase_change', 'Phase_change', 'Phase_change'),
        # The unit is released in the same instant as the boundary: the raw
        # `Obtained` row lands first, then the boundary's `Good`.
        (40.0, 'Block', 'A', 'pmReqDefer', 'Resources'),
        (40.0, 'Block', 'A', 'Resources', 'Good'),
        (40.0, 'Node', 'Join', 'Degraded', 'Good'),
        (40.0, 'Block', 'A', 'Good', 'PM'),
        (40.0, 'Node', 'Join', 'Good', 'Degraded'),
        (40.0, 'System', 'State_change', 'Red', 'Yellow'),
        (42.0, 'Block', 'A', 'PM', 'PM_complete'),
        (42.0, 'Block', 'A', 'PM_complete', 'Good'),
        (42.0, 'Node', 'Join', 'Degraded', 'Good'),
        (42.0, 'System', 'State_change', 'Yellow', 'Green'),
        (50.0, 'Block', 'B', 'Good', 'Failed'),
        (50.0, 'Block', 'B', 'Failed', 'Repairing'),
        (50.0, 'Node', 'Join', 'Good', 'Degraded'),
        (50.0, 'System', 'State_change', 'Green', 'Yellow'),
        (60.0, 'System', 'Phase_change', 'Phase_change', 'Phase_change'),
        (62.0, 'Block', 'A', 'Good', 'PM_is_due'),
        (62.0, 'Block', 'A', 'PM_is_due', 'pmReqDefer'),
        (80.0, 'Block', 'B', 'Repairing', 'Repaired'),
        (80.0, 'Block', 'B', 'Repaired', 'Good'),
        (80.0, 'Node', 'Join', 'Degraded', 'Good'),
        (80.0, 'System', 'Phase_change', 'Phase_change', 'Phase_change'),
        (80.0, 'Block', 'A', 'pmReqDefer', 'Resources'),
        (80.0, 'Block', 'A', 'Resources', 'Cut'),
        (80.0, 'Node', 'Join', 'Good', 'Degraded'),
        (80.0, 'Block', 'A', 'Cut', 'Cut'),
        (82.0, 'Block', 'A', 'Cut', 'PM_complete'),
        (82.0, 'Block', 'A', 'PM_complete', 'Cut'),
        (82.0, 'System', 'State_change', 'Yellow', 'Green'),
        (90.0, 'Block', 'B', 'Good', 'Failed'),
        (90.0, 'Block', 'B', 'Failed', 'Repairing'),
        (90.0, 'Node', 'Join', 'Degraded', 'Bad'),
        (90.0, 'Node', 'end', 'Good', 'Bad'),
        (90.0, 'System', 'State_change', 'Green', 'Red'),
        (100.0, 'Block', 'A', 'Cut', 'Cut'),
        (100.0, 'Block', 'B', 'Repairing', 'Done'),
        (100.0, 'System', 'Phase_change', 'Phase_change', 'Mission_failure'),
        (100.0, 'System', 'Simulation_Terminate', '--', '--'),
    ])


def test_the_spare_wait_that_follows_a_pre_ldt_reads_rephold_when_a_boundary_lands_in_it() -> None:
    """`End_Pre-LDT -> RepHold`: the pre-LDT wait is followed by a wait for a
    spare, and a boundary landing inside that second wait re-activates the
    block, which prints `RepHold`.

    Why: after `Repair`'s pre-LDT delay expires it waits for a spare, and
    `CheckForSpare`'s SparePool arm has already put the block into `RepHold`
    with `"Waiting_for_Spare"` *before* the pre-LDT (`block_lifecycle.py:3146-3148`), so the block's operating status really is
    `RepHold` across both waits. Only the pre-LDT wait writes a row of its own
    (`End_Pre-LDT`), which is why `End_Pre-LDT` is the last logged state before
    the spare arrives. A boundary in that window calls
    `ChangeBlockState(REP_HOLD, ACTIVE, "Phase_Change")`
    (`cascade_phase.py:139-150`), which prints the operating status because the
    new active status is `Active`. **This is the only shape the row takes**: it
    needs the boundary to land while the block is *waiting*, not while it is
    moving, because a boundary that lands in the same instant as the pre-LDT's
    own expiry is processed after the block has already transitioned to
    `Repairing` and prints `Cut` or `Linked` instead.

    Setup: one block `A` between a Start and an End node, phased `Cut, Active,
    Active` in 34-hour phases so the boundary is at 34; a `Fixed` 30-hour life,
    a `Fixed` 10-hour repair, a `Fixed` 4-hour pre-LDT, `Pooled` sparing from
    an empty one-spare pool that restocks every 60 hours. 100 sim hours.

    Expected: `Cut` from kickoff; the failure at 30 finds the pool empty
    (`Failed -> Sparing`) and the spare wait is invisible while Cut; at 34 the
    pre-LDT expires and the boundary re-activates the block in the same instant,
    so the two rows are `Cut -> End_Pre-LDT` and `End_Pre-LDT -> RepHold`; the
    pool restocks at 60 and the block repairs (`RepHold -> Sparing`,
    `Sparing -> Repairing`) to `Good` at 70.

    Source: raptor/core/engine/block_lifecycle.py:3146-3148 (`CheckForSpare`'s
    `RepHold` before the pre-LDT) (`_pre_ldt`'s `End_Pre-LDT`
    row), raptor/core/engine/cascade_phase.py:131-150,
    raptor/core/engine/blocks.py:169-185; Raptor 7
    """
    spec = {
        "system": {"run_length": 100.0, "num_runs": 1, "termination": "TimeSim"},
        "blocks": [_block("A", 30.0, 10.0, pre_ldt_dist=FIXED,
                          pre_ldt_params=[4.0], sp_type="Pooled",
                          spare_pool="SP")],
        "spare_pools": [{"name": "SP", "stock": 0, "new_spares": 1,
                         "arrive_every": 60.0, "rso": 1}],
        "nodes": [{"name": "Start", "type": 1, "n": 0, "out": 1},
                  {"name": "End", "type": 3, "n": 1, "out": 0}],
        "links": [{"from": "Start", "to": "A"}, {"from": "A", "to": "End"}],
        "phases": [{"name": f"P{index + 1}", "mission": 1, "dist": FIXED,
                    "params": [34.0]} for index in range(3)],
        "phase_states": {"A": [CUT, ACTIVE, ACTIVE]},
    }

    result = run(spec)

    assert result.draws == 0, (
        "every distribution is Fixed, so B2 says this run must draw nothing; "
        f"it drew {result.draws}")

    assert_timeline(result, [
        (0.0, 'System', 'State_change', '--', 'Mission'),
        (0.0, 'Block', 'A', '--', 'Cut'),
        (0.0, 'Node', 'end', '--', 'Bad'),
        (0.0, 'System', 'State_change', 'Mission', 'Red'),
        (30.0, 'Block', 'A', 'Cut', 'Failed'),
        (30.0, 'Block', 'A', 'Failed', 'Sparing'),
        (30.0, 'Block', 'A', 'Sparing', 'Cut'),
        (34.0, 'System', 'Phase_change', '--', 'Phase_change'),
        # Pre-LDT expires and the boundary reactivates in the same instant.
        (34.0, 'Block', 'A', 'Cut', 'End_Pre-LDT'),
        (34.0, 'Block', 'A', 'End_Pre-LDT', 'RepHold'),
        (34.0, 'System', 'Red_change', '--', 'Red'),
        (60.0, 'Pool', 'SP', '--', 'Sparing'),
        (60.0, 'Block', 'A', 'RepHold', 'Sparing'),
        (60.0, 'Block', 'A', 'Sparing', 'Repairing'),
        (60.0, 'System', 'Red_change', 'Red', 'Red'),
        (68.0, 'System', 'Phase_change', 'Phase_change', 'Phase_change'),
        (70.0, 'Block', 'A', 'Repairing', 'Repaired'),
        (70.0, 'Block', 'A', 'Repaired', 'Good'),
        (70.0, 'Node', 'end', 'Bad', 'Good'),
        (70.0, 'System', 'State_change', 'Red', 'Green'),
        (100.0, 'Block', 'A', 'Good', 'Failed'),
        (100.0, 'Block', 'A', 'Failed', 'Sparing'),
        (100.0, 'Block', 'A', 'Sparing', 'RepHold'),
        (100.0, 'Node', 'end', 'Good', 'Bad'),
        (100.0, 'System', 'State_change', 'Green', 'Red'),
        (100.0, 'Block', 'A', 'RepHold', 'Done'),
        (100.0, 'System', 'Phase_change', 'Phase_change', 'Mission_failure'),
        (100.0, 'System', 'Simulation_Terminate', '--', '--'),
    ])
