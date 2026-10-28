"""The **PM clock** on the paths that do not draw a life: the accounting an
induced failure does to `PMTimeLeft`, the OBPM threshold, and what a block
does when it is asked to fail while it is parked for dependence reasons.

Every fixture here is built in memory, runs one `Fixed`-distribution run, and
asserts a literal timeline written out in the test (rules B2, B3, B5). The
claims are about `raptor/core/engine/block_lifecycle.py` and name the Raptor 7
procedures that file reproduces.
"""
from __future__ import annotations

from typing import Any

from tests.behaviour.harness import FIXED, assert_timeline, chain, run

# --------------------------------------------------------------------------
# The fixtures
# --------------------------------------------------------------------------


def block(name: str, life: float, repair: float = 10.0, **fields) -> dict[str, Any]:
    """One block with a `Fixed` life of *life* and a `Fixed` repair of *repair*.

    Only the keys a fixture names are written: `build_from_spec` applies the
    dict as a patch over the row it creates, so an omitted column keeps the
    template row's value. That is why `pm_req_defer=False` is spelled out in
    the PM fixtures rather than defaulted here -- a reader has to be able to
    see which flag a claim rests on.
    """
    row: dict[str, Any] = {"name": name, "fail_dist": FIXED,
                           "fail_params": [life], "repair_dist": FIXED,
                           "repair_params": [repair]}
    row.update(fields)
    return row


def model(blocks: list[dict], nodes: list[dict], links: list[dict],
          run_length: float = 300.0, **extra) -> dict[str, Any]:
    """A `build_from_spec` spec: the minimum topology the loader accepts.

    `build_from_spec` refuses a model with no End node and a block with no
    outgoing link, so every fixture here carries a Start and an End.
    """
    spec: dict[str, Any] = {"system": {"run_length": run_length, "num_runs": 1,
                                       "termination": "TimeSim"},
                            "blocks": blocks, "nodes": nodes, "links": links}
    spec.update(extra)
    return spec


def frequency_pm(*, freq: float, length: float, stagger: float = 0.0,
                 fail_reset: bool = False, **fields) -> dict[str, Any]:
    """Frequency-mode PM on a `Fixed` schedule this suite owns.

    `pm_freq`/`pm_stagger_time` are the two halves of `Initialize`'s
    `PMTimeLeft:=pmFreq-pmStagger` (`block_lifecycle.py:1329`), so `stagger=0`
    means the clock starts one whole period out and every later refill is a
    whole period too. `pm_refresh=False` is what makes the block resume the
    life the PM race displaced instead of drawing a new one, and
    `pm_req_defer=False` keeps `ObtainPMprereqs` holding rather than handing
    back to `Run` -- only the holding form parks at a `PM_HOLD_*` wait site, so
    a fixture that omitted it would never reach the PM prereq waits at all.
    """
    settings: dict[str, Any] = {"uses_pm": True, "pm_freq": freq,
                                "pm_stagger_time": stagger, "pm_dist": FIXED,
                                "pm_params": [length], "pm_refresh": False,
                                "pm_req_defer": False, "pm_mis_defer": False,
                                "pm_fail_reset": fail_reset}
    settings.update(fields)
    return settings


def induced_failure_dip(*, life: float, repair: float, pm_freq: float,
                        pm_length: float, fail_reset: bool,
                        sibling_life: float = 100.0,
                        sibling_repair: float = 1000.0,
                        run_length: float = 500.0) -> dict[str, Any]:
    """`Start -> A -> N2 -> B -> End`, with `A` sysDepending on the End node
    and `dep_fail = 100%`.

    `A` is the block whose PM clock is under test, `B` is what takes the End
    node -- and so `A` -- down. `A`'s dependence percentages are written out
    longhand rather than left at the template row's `dep_idle=100%`, because
    `draw_dependency_action` short-circuits on the first `100.0` it finds
    (`dependency.py:96-103`) and `DepIdle=100%` would decide `GoIdle` before
    `DepFail` was ever read: the induced branch would be unreachable.

    `def_dep_state_idle=False` matters just as much. With it on, `A` finishing
    its repair while the End node is still `Bad` goes straight back to `Idle`
    (`repair_completion_status`'s `down_num<=1` arm,
    `dependency.py:882-894`, then `block_lifecycle.py:952-967`) and the PM
    clock this fixture exists to measure is never raced again.

    The arithmetic the two tests divide between them: `A`'s clock starts at
    `pm_freq - stagger = 400`, is decremented once for the 100 hours it waits
    before it is induced, and is then decremented a **second** time by the
    induced-failure branch -- landing on 200, not 300. `pm_fail_reset=True`
    zeroes the field instead (`block_lifecycle.py:2149-2150`), which the next
    race refills to a full 400.
    """
    dependent = block("A", life, repair, dep_type="RBDNode", dep_num=-2,
                      dep_idle=0.0, dep_nothing=0.0, dep_pm=0.0,
                      dep_fail=100.0, def_dep_state_idle=False,
                      **frequency_pm(freq=pm_freq, length=pm_length,
                                     fail_reset=fail_reset))
    sibling = block("B", sibling_life, sibling_repair)
    return model([dependent, sibling], *chain("A", "B"),
                 run_length=run_length)


#: The block ids the OBPM fixtures name in `dep_num`. `build_from_spec`
#: numbers blocks independently of nodes and events, so a dependency target
#: has to be the block's own `id` and not its position in the list.
A_ID, B_ID, E_ID = 1, 2, 3


def obpm_threshold(*, threshold: float, run_length: float,
                   a_life: float = 1000.0, a_repair: float = 100.0,
                   b_life: float = 60.0, b_repair: float = 25.0,
                   e_life: float = 40.0, e_repair: float = 5.0) -> dict[str, Any]:
    """`Start -> {A, E} -> Cold -> B -> End`, a `1-of-2` cold node, with `A`
    **block**-depending on `B` at `dep_pm = 100%`.

    Three deliberate choices, each of which is what makes the OBPM wake land at
    the wait site the test is about:

    * `dep_pm=100` short-circuits the action draw to `GoToPM`
      (`dependency.py:102`), which is the only route to an `"OBPM"` wake that
      does not need a PM trigger.
    * `a` block-depends on `B`, not on a node, because
      `repair_completion_status`'s `depType="RBDBlock"` arm
      (`dependency.py:804-825`) returns `(IDLE, False)` on its own -- no
      `GetDownNum` recheck, so no `prevDepState:="GoIdle"` is recorded
      (`block_lifecycle.py:950-951`). That matters because
      `ResendBackFlow`'s replay arm re-idles a promoted dependent whose
      `prevDepState` is `"GoIdle"` (`dependency.py:324-325`), which would undo
      the cold survey's promotion.
    * `a` is the cold candidate holding the single slot (`ColdPri` 10, so it
      sorts first) and `e` is its standby peer, with a standby stress so `e`
      burns its life rather than parking at `ZeroWait`. `e`'s failure is the
      *only* thing that surveys the cold node at a moment `a` is parked in its
      Idle wait.

    `DepPMThresh` is the single knob the two OBPM tests turn: the threshold
    `_obpm_over_threshold` compares against is
    `DepPMThresh * failMean / 100` (`block_lifecycle.py:115-122`), which with
    `a`'s `Fixed` 1000-hour life is `10 * threshold`.
    """
    nodes = [{"id": 1, "name": "Start", "type": 1, "n": 0, "out": 2},
             {"id": 2, "name": "Cold", "type": 2, "n": 2, "out": 1, "k": 1,
              "k_star": 1, "cold": 1, "pr": 0, "autos": 0},
             {"id": 3, "name": "End", "type": 3, "n": 1, "out": 0}]
    links = [{"from": "Start", "to": "A"},
             {"from": "Start", "to": "E"},
             {"from": "A", "to": "Cold", "cold_pri": 10, "a_prob": 1.0,
              "auto_time": 0.0, "man_time": -1.0},
             {"from": "E", "to": "Cold", "cold_pri": 20, "a_prob": 1.0,
              "auto_time": 0.0, "man_time": -1.0},
             {"from": "Cold", "to": "B"},
             {"from": "B", "to": "End"}]
    dependent = block("A", a_life, a_repair, id=A_ID, dep_type="RBDBlock",
                      dep_num=B_ID, dep_idle=0.0, dep_nothing=0.0,
                      dep_pm=100.0, dep_fail=0.0, dep_pm_thresh=threshold,
                      def_dep_state_idle=True, sb_stress=0.5,
                      **frequency_pm(freq=1.0e6, length=5.0))
    return model([dependent,
                  block("B", b_life, b_repair, id=B_ID),
                  block("E", e_life, e_repair, id=E_ID, sb_stress=0.5)],
                 nodes, links, run_length=run_length)


# --------------------------------------------------------------------------
# An induced failure's accounting on `PMTimeLeft`
# --------------------------------------------------------------------------


def test_an_induced_failure_takes_the_elapsed_time_off_the_pm_clock_a_second_time() -> None:
    """A block with `pmFailReset` off has the induced failure's elapsed time
    taken off `PMTimeLeft` **twice**, so the PM that follows the repair comes
    due 200 hours later instead of not at all.

    Why: source's fail wait already decrements `PMTimeLeft` on every
    interrupt -- `PMTimeLeft:=PMTimeLeft-(SimTime-TimeHack)` at Raptor 7 -- and the `Induced_Failure` branch of the same wake
    does it again at Raptor 7. The port keeps both: the first
    at `block_lifecycle.py:1113-1114`, the second inside the induced branch at
    `block_lifecycle.py:1187-1192`. With `PMFreq` 400, `PMStagger` 0 and an
    induced failure 100 hours in, the clock lands on 400 - 100 - 100 = 200
    rather than 300, and 200 < the fresh 300-hour life, so the repair is
    followed by a PM. The internal engine ledger records the same
    double decrement going *negative* and refilling (`LadL8B` run 18: PM at
    324.88 + 400, not + 14.67); here it lands positive and short, which is the
    other half of the same arithmetic.

    Setup: 2 blocks, 3 nodes, 500 sim hours. `A`: `Fixed` life 300, `Fixed`
    repair 10, frequency PM 400 / `Fixed` 5, `PMRefresh` off, `PMReqDefer` off,
    `PMFailReset` off, sysDepending on the End node with `DepFail = 100%` and
    `defDepStateIdle` off. `B`: `Fixed` life 100, `Fixed` repair 1000.

    Expected: `B` fails at 100 and takes the End node down; `A` is induced to
    fail at the same 100; `A` is repaired at 110 and is `Good`; `A` is
    `PM_is_due` at **310** -- 110 + the doubly-decremented 200 -- completes
    the PM at 315, and only then runs its restored 300-hour life out, failing
    at 415.

    Source: Raptor 7 (the two `PMTimeLeft` decrements
    of one induced failure) in the port's `block_lifecycle.py`
    (`pm_fail_reset` zeroing the field instead); the internal engine ledger
    """
    result = run(induced_failure_dip(life=300.0, repair=10.0, pm_freq=400.0,
                                     pm_length=5.0, fail_reset=False))

    assert_timeline(result, [
        (0.0, "Block", "A", "--", "Good"),
        (0.0, "Block", "B", "--", "Good"),
        (100.0, "Block", "B", "Good", "Failed"),
        (100.0, "Block", "B", "Failed", "Repairing"),
        (100.0, "Node", "end", "--", "Bad"),
        (100.0, "System", "State_change", "--", "Red"),
        (100.0, "Block", "A", "Good", "Failed"),
        (100.0, "Block", "A", "Failed", "Repairing"),
        (100.0, "Node", "N2", "--", "Bad"),
        (110.0, "Block", "A", "Repairing", "Repaired"),
        (110.0, "Block", "A", "Repaired", "Good"),
        (110.0, "Node", "N2", "Bad", "Good"),
        (110.0, "System", "Red_change", "--", "Red"),
        (310.0, "Block", "A", "Good", "PM_is_due"),
        (310.0, "Block", "A", "PM_is_due", "PM"),
        (310.0, "Node", "N2", "Good", "NodePM"),
        (310.0, "System", "Red_change", "Red", "Red"),
        (315.0, "Block", "A", "PM", "PM_complete"),
        (315.0, "Block", "A", "PM_complete", "Good"),
        (315.0, "Node", "N2", "NodePM", "Good"),
        (315.0, "System", "Red_change", "Red", "Red"),
        (415.0, "Block", "A", "Good", "Failed"),
        (415.0, "Block", "A", "Failed", "Repairing"),
        (415.0, "Node", "N2", "Good", "Bad"),
        (415.0, "System", "Red_change", "Red", "Red"),
        (425.0, "Block", "A", "Repairing", "Repaired"),
        (425.0, "Block", "A", "Repaired", "Good"),
        (425.0, "Node", "N2", "Bad", "Good"),
        (425.0, "System", "Red_change", "Red", "Red"),
        (500.0, "Block", "A", "Good", "Done"),
        (500.0, "Block", "B", "Repairing", "Done"),
        (500.0, "System", "Phase_change", "--", "Mission_failure"),
        (500.0, "System", "Simulation_Terminate", "--", "--"),
    ], where="pm_fail_reset off")


def test_pm_fail_reset_zeroes_the_pm_clock_so_the_next_pm_is_a_whole_period_out() -> None:
    """With `PMFailReset` on, an induced failure zeroes `PMTimeLeft`, so the
    block's next PM is a full `PMFreq` away and its life expires first.

    Why: source's `"Block Has Failed"` resolution is
    `IF pmFailReset: PMTimeLeft:=0.0 ELSE: PMTimeLeft:=PMTimeLeft-(SimTime-TimeHack)`,
   and the port's `_record_failure` writes
    exactly that choice at `block_lifecycle.py:2142-2150`. A zeroed clock is
    refilled to a whole `PMFreq` by the next race
    (`_pm_race`, `block_lifecycle.py:460-461`), which is what puts the block's
    next PM out of reach of a 300-hour life.

    Setup: the same 2-block model as the test above, with `PMFailReset` on and
    nothing else changed, so the two timelines differ only by that flag.

    Expected: identical up to `A` being `Good` again at 110, then no PM at all
    -- `A` runs the full 300-hour life and fails at **410**, 300 after the
    repair, where the `PMFailReset`-off fixture has `A` in `PM_is_due` at 310.

    Source: Raptor 7 (`pmFailReset` zeroes `PMTimeLeft`),
     (`IF PMTimeLeft<=0.0 THEN PMTimeLeft:=pmFreq`, the refill the
    zero triggers); the internal engine ledger (B.10, `PMTimeLeft`
    is not zeroed when the PM wins the race, which is why the reset has to come
    from the failure and not from the race)
    """
    result = run(induced_failure_dip(life=300.0, repair=10.0, pm_freq=400.0,
                                     pm_length=5.0, fail_reset=True))

    assert_timeline(result, [
        (0.0, "Block", "A", "--", "Good"),
        (0.0, "Block", "B", "--", "Good"),
        (100.0, "Block", "B", "Good", "Failed"),
        (100.0, "Block", "B", "Failed", "Repairing"),
        (100.0, "Node", "end", "--", "Bad"),
        (100.0, "System", "State_change", "--", "Red"),
        (100.0, "Block", "A", "Good", "Failed"),
        (100.0, "Block", "A", "Failed", "Repairing"),
        (100.0, "Node", "N2", "--", "Bad"),
        (110.0, "Block", "A", "Repairing", "Repaired"),
        (110.0, "Block", "A", "Repaired", "Good"),
        (110.0, "Node", "N2", "Bad", "Good"),
        (110.0, "System", "Red_change", "--", "Red"),
        (410.0, "Block", "A", "Good", "Failed"),
        (410.0, "Block", "A", "Failed", "Repairing"),
        (410.0, "Node", "N2", "Good", "Bad"),
        (410.0, "System", "Red_change", "Red", "Red"),
        (420.0, "Block", "A", "Repairing", "Repaired"),
        (420.0, "Block", "A", "Repaired", "Good"),
        (420.0, "Node", "N2", "Bad", "Good"),
        (420.0, "System", "Red_change", "Red", "Red"),
        (500.0, "Block", "A", "Good", "Done"),
        (500.0, "Block", "B", "Repairing", "Done"),
        (500.0, "System", "Phase_change", "--", "Mission_failure"),
        (500.0, "System", "Simulation_Terminate", "--", "--"),
    ], where="pm_fail_reset on")


# --------------------------------------------------------------------------
# The OBPM threshold, at the two wait sites that test it
# --------------------------------------------------------------------------


def test_an_obpm_wake_past_the_threshold_sends_the_block_into_pm() -> None:
    """An `"OBPM"` wake whose `OperatingTime` has passed
    `DepPMThreshold * failMean / 100` puts the block straight into PM, without
    drawing a new life.

    Why: source's fail wait routes the reason two ways
    : over the threshold it sets
    `returnFromMaint:=TRUE` and calls `DetermineNewState("PM is due")`; under
    it, `returnFromMaint:=FALSE` and, for `defDepStateIdle`, an `Idle`
    transition. The port's threshold test is the single shared predicate
    `_obpm_over_threshold` (`block_lifecycle.py:115-122`), called at
     for the fail wait for the Idle wait for the
    zero-stress one.

    Setup: `obpm_threshold(threshold=1.0)`: 3 blocks, 3 nodes, 80 sim hours.
    `A`: `Fixed` life 1000, frequency PM `1000000` / `Fixed` 5 (so the
    scheduled PM can never race), `DepPMThresh = 1%` of a 1000-hour life, i.e.
    a 10-hour threshold, `DepPM = 100%` on a block dependency on `B`,
    `defDepStateIdle` on, `SBStress` 0.5. `B`: `Fixed` life 60, repair 25.
    `E`: the cold node's standby peer, `Fixed` life 40 at standby stress 0.5,
    so it fails at 80.

    Expected: `B` fails at 60 and `A` is `PM_is_due` at that same 60 -- 60 hours
    of `OperatingTime` against a 10-hour threshold -- and completes its `Fixed`
    5-hour PM at 65. No life is drawn in between: the block goes `PM_is_due` to
    `PM` in the same instant.

    Source: Raptor 7 (the fail wait's over-threshold OBPM arm),
     (the same arm in source's own `Run` ordering),
    Raptor 7 (`OperatingTime > DepPMThreshold*failMean/100`);
    docs/engine-design/block-lifecycle.adoc (the fail wait's interrupt
    handlers)
    """
    result = run(obpm_threshold(threshold=1.0, run_length=80.0))

    assert_timeline(result, [
        (0.0, "Block", "A", "--", "Good"),
        (0.0, "Block", "B", "--", "Good"),
        (0.0, "Block", "E", "--", "Good"),
        (0.0, "Block", "E", "Good", "Standby"),
        (0.0, "Node", "Cold", "--", "Good"),
        (0.0, "Block", "E", "Standby", "StessLevel"),
        (60.0, "Block", "B", "Good", "Failed"),
        (60.0, "Block", "B", "Failed", "Repairing"),
        (60.0, "Node", "end", "--", "Bad"),
        (60.0, "System", "State_change", "--", "Red"),
        (60.0, "Block", "A", "Good", "PM_is_due"),
        (60.0, "Block", "A", "PM_is_due", "PM"),
        (60.0, "Block", "E", "StessLevel", "Good"),
        (60.0, "Node", "Cold", "Good", "Degraded"),
        (65.0, "Block", "A", "PM", "PM_complete"),
        (65.0, "Block", "A", "PM_complete", "Idle"),
        (65.0, "Block", "A", "Idle", "Good"),
        (65.0, "Block", "A", "Good", "Standby"),
        (65.0, "Node", "Cold", "Degraded", "Good"),
        (65.0, "Block", "A", "Standby", "StessLevel"),
        (65.0, "System", "Red_change", "--", "Red"),
        (70.0, "Block", "E", "Good", "Failed"),
        (70.0, "Block", "E", "Failed", "Repairing"),
        (70.0, "Block", "A", "StessLevel", "Good"),
        (70.0, "Node", "Cold", "Good", "Degraded"),
        (70.0, "System", "Red_change", "Red", "Red"),
        (75.0, "Block", "E", "Repairing", "Repaired"),
        (75.0, "Block", "E", "Repaired", "Good"),
        (75.0, "Block", "E", "Good", "Standby"),
        (75.0, "Node", "Cold", "Degraded", "Good"),
        (75.0, "Block", "E", "Standby", "StessLevel"),
        (75.0, "System", "Red_change", "Red", "Red"),
        (80.0, "Block", "A", "Good", "Done"),
        (80.0, "Block", "B", "Repairing", "Done"),
        (80.0, "Block", "E", "StessLevel", "Done"),
        (80.0, "System", "Phase_change", "--", "Mission_failure"),
        (80.0, "System", "Simulation_Terminate", "--", "--"),
    ], where="OBPM over the threshold, fail wait")


def test_an_obpm_wake_under_the_threshold_idles_a_block_already_parked_for_dependence() -> None:
    """An `"OBPM"` wake that lands on a block **parked in its Idle wait** and
    finds `OperatingTime` under the threshold clears `returnFromMaint` and --
    because `defDepStateIdle` is set -- puts the block straight back to `Idle`.

    Why: the Idle wait is source's `ZeroWait` site's `opStatus=Idle` half,
    and its OBPM arm is the same
    two-branch handler the fail wait has: over the
    threshold a dependency-induced PM, under it benign --
    `returnFromMaint:=FALSE`, an Idle refresh when `defDepStateIdle`, then the
    ordinary redraw. The port's is `block_lifecycle.py:1668-1677`, and the
    re-idle is what puts the block straight back in the `while
    opStatus=Idle` loop it was already in.

    Setup: `obpm_threshold(threshold=50.0)`: the same 3-block, 3-node model
    with `DepPMThresh = 50%` of a 1000-hour life, a 500-hour threshold no
    fixture run reaches, and 150 sim hours.

    Expected, and the three steps the claim rests on:

    * at 60 `B` fails and the `GoToPM` wake reaches `A` in its **fail** wait.
      60 hours of `OperatingTime` is under 500, so the under-threshold arm
      idles it: `A` logs `Good -> Idle` at 60. That `Running -> Idle`
      transition is plain-to-plain, and `apply`'s cold flush is gated on
      `NOT (old AND new both plain)` (`orchestrator.py:661-666`), so it marks
      the cold node without surveying it.
    * at 80 `E` fails, which is a non-plain turn and so does survey it.
      `run_cold_logic`'s prep phase promotes the Idle candidate `A` to Running
      with a bare `ChangeBlockState` and **no wake**
      (`cold_standby.py:378-392`), so `A` logs `Idle -> Good` and stays parked
      -- `ResendBackFlow` leaves it alone because `prevDepState` is
      `"GoToPM"`, which is neither `"GoIdle"` nor `""`
      (`dependency.py:322-328`).
    * at 145 `B` fails again. The `Down` dispatch finds `A` Running
      (`dependency.py:309`) and the wake is delivered on its `run_state.zero_wait`
      flag (`cascade_dependency.py:168-169`), so it lands **inside** the Idle
      wait rather than at the fail wait. Still 60 hours of `OperatingTime` --
      nothing consumes life while the block is Idle -- so the arm is the
      under-threshold one and `A` is put back to `Idle` at 145.

    Source: Raptor 7 (the ZeroWait/Idle site's OBPM arm),
     (the two halves of the `IF ((CombinedStress<=0.0)
    OR (opStatus=Idle))` wait); the internal engine ledger (the real
    `S4B1 Failed Operating_time=84.33` capture that corrected the earlier
    reading of this site as having no `Induced_Failure` branch, its sibling)
    """
    result = run(obpm_threshold(threshold=50.0, run_length=150.0))

    assert_timeline(result, [
        (0.0, "Block", "A", "--", "Good"),
        (0.0, "Block", "B", "--", "Good"),
        (0.0, "Block", "E", "--", "Good"),
        (0.0, "Block", "E", "Good", "Standby"),
        (0.0, "Node", "Cold", "--", "Good"),
        (0.0, "Block", "E", "Standby", "StessLevel"),
        (60.0, "Block", "B", "Good", "Failed"),
        (60.0, "Block", "B", "Failed", "Repairing"),
        (60.0, "Node", "end", "--", "Bad"),
        (60.0, "System", "State_change", "--", "Red"),
        (60.0, "Block", "A", "Good", "Idle"),
        (80.0, "Block", "E", "StessLevel", "Failed"),
        (80.0, "Block", "E", "Failed", "Repairing"),
        (80.0, "Block", "A", "Idle", "Good"),
        (80.0, "Node", "Cold", "Good", "Degraded"),
        (80.0, "System", "Red_change", "--", "Red"),
        (85.0, "Block", "B", "Repairing", "Repaired"),
        (85.0, "Block", "B", "Repaired", "Good"),
        (85.0, "Node", "end", "Bad", "Good"),
        (85.0, "Block", "E", "Repairing", "Repaired"),
        (85.0, "Block", "E", "Repaired", "Good"),
        (85.0, "Block", "E", "Good", "Standby"),
        (85.0, "Node", "Cold", "Degraded", "Good"),
        (85.0, "Block", "E", "Standby", "StessLevel"),
        (85.0, "System", "State_change", "Red", "Green"),
        (145.0, "Block", "B", "Good", "Failed"),
        (145.0, "Block", "B", "Failed", "Repairing"),
        (145.0, "Node", "end", "Good", "Bad"),
        (145.0, "System", "State_change", "Green", "Red"),
        (145.0, "Block", "A", "Good", "Idle"),
        (150.0, "Block", "A", "Idle", "Done"),
        (150.0, "Block", "B", "Repairing", "Done"),
        (150.0, "Block", "E", "StessLevel", "Done"),
        (150.0, "System", "Phase_change", "--", "Mission_failure"),
        (150.0, "System", "Simulation_Terminate", "--", "--"),
    ], where="OBPM under the threshold, idle wait")
