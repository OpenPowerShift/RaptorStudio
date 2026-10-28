"""Which wait each interrupt ended, and why: one test per
`(WaitSite, InterruptReason)` cell a constructible model can reach.

Layer 8 of the retirement gate (S13). The companion page
`docs/engine-design/state-coverage.adoc` is a 113-cell matrix of
`InterruptReason` against `wait_sites.WaitSite`, and
`tools/state_reachability.py` settles how many of those cells a
well-formed model can actually drive into: **62 unpinned cells**, the
denominator this module exists to shrink. Every test here builds one small
in-memory model, drives an **ordinary** block into exactly one cell, and
asserts two things: the block's own event timeline, as a committed literal,
and the `(block, site, reason)` triple the engine handed the observer.

**The two assertions are deliberately different kinds of evidence.** The
timeline says *what the block did* -- `Good -> PM_is_due -> PM_hold -> PM`
at the times the claim names -- and it is what `test_b_ladder.py`'s
literals are for. The recorded triple says *which `WAIT DURATION` /
`WAIT FOR ... TO Fire` the interrupt ended*, which is the thing the matrix
counts and which no event-log line records at all: a block idles and a
block parks on a pmSync trigger are the same `Idle -> Good` rows on the
timeline. A test that only counted cells would pass on a model that reached
the right wait for the wrong reason; a test that only asserted a timeline
would pass on a model that reached the right wait and then did something
else. Together they pin the claim.

**One cell per test (B8), and the fixtures are shared.** Several models
here reach more than one cell -- the pmMisDefer release reaches six -- and
those cells get a test each, all built from one fixture. The fixture is
named for the *model*, the test for the *claim*, and `WAIT_SITE_FIXTURES`
below is the index the coverage gate reads: it maps each test name to the
builder that test runs, so adding a test here pins its cell with no edit to
`state_reachability.py`.

**The timeline literals are inline, and the repetition is deliberate.** Two
or three tests can share one fixture, and each re-states that fixture's
complete timeline rather than pointing at a shared table -- because
`test_b_behaviour_coverage.py` finds the transitions this suite pins with an
`ast` pass, and a literal reached through a name reads as `Name(id=...)` and
is invisible to it. A behaviour test whose expectation is one indirection
away is also one refactor away from asserting nothing.

**Nothing here reads `tests/golden/` and nothing is stochastic.** Every
distribution is `Fixed` (code 19), one run, no seeded stream: `Run.draws`
is asserted `== 0` throughout. Two things that would otherwise cost a draw
or a surprise are stated where they are set:

* **The spec patch only writes the keys it names.** `build_from_spec`
  applies each block dict as a patch over the row it creates, so an unset
  `PMReqDefer` is whatever the template row says, not `False`. Every
  fixture that depends on a PM flag sets it explicitly; `_no_req_defer()`
  is the one that turns the deferral off, because the `PMhold` sites are
  *only* reachable with `pmReqDefer` off (`_request_pm_resource`'s
  `pm_res_waiting and block.pm_req_defer` arm abandons the PM attempt
  instead of holding, `block_lifecycle.py:2897`).
* **A trigger is `Fixed` or it is a draw.** `trigger()` gives the trigger
  the same `Fixed` distribution `spine._s5` uses; Raptor's own trigger
  default is stochastic and costs ten draws for a firing nobody is
  measuring.

**The three hard numbers, so a reviewer knows what to distrust.** A block
whose `Fixed` life exceeds `block_lifecycle._FAR_FUTURE` (1e9 h) is parked
in `BEYOND_HORIZON` rather than at an ordinary fail-wait, and the run has
to outlast 1e9 h for that wait to end at all -- so the `beyond_horizon`
fixtures carry absurd lives on purpose (`FAR`). The `zero_stress` fixtures
get a combined stress of zero from a *phase* value of `0.0`, because
`combined_stress` is `PhaseStress * StandbyStress / GDstress` and a phase
value is the only lever that reaches zero without also parking the block in
a cold group (`block_lifecycle.py:125-191`). And the two
`Dependence_*` reactions that are not `GoIdle` are `DepFail=100` and
`DepPM=100`, each of which `draw_dependency_action` short-circuits to its
own outcome with no draw at all (`dependency.py:95-101`).

**What this module deliberately does not do.** Fifteen cells stay
`reachable` in `tests/behaviour/state_reachability.toml`: seven
`idle x <reason>` cells that need a *second* dependency dispatch to arrive
while the block is already Idle, three `zero_stress` cells of the same
shape, the two `Dependence_All_Up` cells (which need a target that goes down
and back up twice in one instant, so the scheduler's interrupt-reason
rewrite leaves `All_Up` as the pending reason at a non-Idle wait), and
`park_a_hop`, `repair_resource_hop` and `repair x Pool_Available`.
`tools/state_reachability.py` is where each of those is tracked,
with the probe that tried it and what the probe recorded. The six
`Switch_Failed_Path` cells are **not** in this module's reach at all:
`WAKE_POLICY` permits the reason at six wait sites and nothing in `raptor/`
ever sends it, which is an engine finding (`cold_standby._switch_failed` sets
`cold_path_done` and deliberately does not interrupt --
`cold_standby.py:274`) and is reported, not tested.
"""
from __future__ import annotations

import sys
from pathlib import Path

from raptor.core.engine.block_env import observing

from .harness import Run, assert_timeline, chain, run

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO / "tools"))

import state_coverage as sc  # noqa: E402  (the corpus's own observer)

#: A `Fixed` failure life of *v*. Distribution code 19: `DrawNumber`'s
#: `WHEN 19` returns `Param(1)` without touching a stream
#: (`raptor/core/rng.py:451`), so a `Fixed` model is exact and consumes no
#: randomness at all.
FIXED = 19

#: A life past `block_lifecycle._FAR_FUTURE` (1e9 h), which is the threshold
#: `_fail_wait` compares against -- so a block with this life is parked at
#: `WaitSite.BEYOND_HORIZON` instead of at the ordinary fail-wait.
FAR = 2.0e9

#: The block ids the three-block fixtures wire: `build_from_spec` numbers
#: `BLOCK_DATA` rows in list order (`raptor/api/build.py:857-864`), so the
#: first entry of `spec["blocks"]` is `Y` and the second is `X`.
Y_ID, X_ID, A_ID = 1, 2, 3

TIMER = sc.TIMER
Cell = tuple[str, str]
Wake = tuple[str, str, str]
"""`(block name, wait site, reason)`, with `TIMER` for a wait that ran out
on its own clock rather than being cut short."""


# --------------------------------------------------------------------------
# The model vocabulary
# --------------------------------------------------------------------------

def block(name: str, life: float, repair: float = 10.0, **fields) -> dict:
    """One block with a `Fixed` life of *life* and a `Fixed` repair.

    Only the keys a fixture names are written: `build_from_spec` applies the
    dict as a patch over the row it creates, so an omitted column keeps the
    template row's value. That is why `pm_req_defer=False` appears in the
    fixtures below rather than as a default here -- a reader has to be able
    to see which flag a claim rests on.
    """
    row = {"fail_dist": FIXED, "fail_params": [life], "name": name,
           "repair_dist": FIXED, "repair_params": [repair]}
    row.update(fields)
    return row


def no_req_defer() -> dict:
    """The one PM flag a fixture must spell out: hold in `PMhold`, do not defer.

    `ObtainPMprereqs`' resource and spare steps have two arms for "not
    available": `pmReqDefer` abandons the PM attempt and hands back to
    `Run`, and *without* it the block holds.
    Only the second one parks at
    `WaitSite.PM_HOLD_RESOURCE` / `PM_HOLD_POOL_SPARE` /
    `PM_HOLD_CUSTOM_SPARE`, and the template row's `PMReqDefer` is `1`, so a
    fixture that omits it never reaches those three cells at all.
    """
    return {"pm_req_defer": False}


def model(blocks: list[dict], nodes: list[dict], links: list[dict],
          run_length: float = 300.0, **extra) -> dict:
    """A `build_from_spec` spec: the minimum topology the loader accepts.

    `build_from_spec` refuses a model with no End node and a block with no
    outgoing link, so every fixture here carries a Start and an End.
    """
    spec = {"system": {"run_length": run_length, "num_runs": 1,
                       "termination": "TimeSim"},
            "blocks": blocks, "nodes": nodes, "links": links}
    spec.update(extra)
    return spec


def phases(values: tuple[float, ...], durations: tuple[float, ...] = (200.0, 400.0),
           mission: tuple[int, ...] = (1, 0), name: str = "A") -> dict:
    """A two-phase schedule of `Fixed` durations, with one block phased too.

    Two things here are load-bearing and both fail silently if wrong.

    * `build_from_spec`'s `phases` key takes `duration_dist`/`params`, not a
      `duration` scalar: a bare `duration` is ignored and every phase then
      gets the default *stochastic* duration.
    * The phase values must **differ**. `_conduct_phase_change_body` skips a
      block whose old and new type AND value are both unchanged
      (`cascade_phase.py:91`), so equal values mean no `apply_block` call and
      therefore no `Phase_Change` interrupt at all.

    `mission` is `1` for a mission phase and `0` for the non-mission one; the
    non-mission phase is what releases a `pmMisDefer` block's deferred PM.
    """
    return {"phases": [{"name": f"Ph{i + 1}", "duration_dist": FIXED,
                        "params": [durations[i]], "mission": mission[i]}
                       for i in range(len(durations))],
            "phase_states": {name: [f"{name}: {v}" for v in values]}}


def trigger(wait: float, repeats: int = 0) -> dict:
    """A `Fixed` trigger that fires *wait* hours in.

    `Fixed` is load-bearing: Raptor's own trigger distribution is
    stochastic, and a stochastic trigger costs ten stream draws for a firing
    this suite is not measuring (rule B2). `spine._s5` writes the same three
    `Fixed` parameters.
    """
    return {"name": "T1", "init_used": 0.0, "dist": FIXED,
            "params": [wait, 0.0, 0.0], "repeats": repeats}


def pm(**fields) -> dict:
    """PM settings that put a block on a `Fixed` PM schedule we control."""
    settings = {"uses_pm": True, "pm_freq": 50.0, "pm_stagger_time": 0.0,
                "pm_dist": FIXED, "pm_params": [5.0]}
    settings.update(fields)
    return settings


def cold_dependent(peer_life: float, dependent_life: float, *,
                   dependent_of: int = X_ID, dependent_stress: float = 0.5,
                   peer_fail: float = 50.0, peer_repair: float = 50.0,
                   run_length: float = 400.0,
                   dependent_fields: dict | None = None) -> dict:
    """A cold node whose peer a third block block-depends on.

    `Y` (priority 25) and `X` (priority 50) are the peers -- priority 25 is
    lower, so it sorts first and holds the single cold slot at kickoff
    (`IdAndSortColds`) -- and `A` depends on one of them and joins them at
    `Join`. `X` fails at *peer_fail* and is repaired *peer_repair* later, so
    the group alternates and the survey both promotes and demotes a peer
    while `A` sits in a wait. `dependent_stress` gives `A` a standby stress
    so a promotion parks it in a real wait rather than at `ZERO_STRESS`.

    *dependent_of* is which peer `A` block-depends on, and it is the whole
    point of the fixture: `Dependence_Standby` is back-flowed **from** the
    block that goes Standby (`dependency.py:305-309`), so only a dependent
    of the demoted peer sees it, and only a dependent of the *other* peer
    reads a promotion as `Dependence_SB_turnOn`
    (`cascade_dependency.py:74`).
    """
    nodes = [{"id": 1, "name": "Start", "type": 1, "n": 0, "out": 3},
             {"id": 3, "name": "Cold", "type": 2, "n": 2, "out": 1, "k": 1,
              "k_star": 1, "cold": 1},
             {"id": 4, "name": "Join", "type": 2, "n": 2, "out": 1, "k": 1},
             {"id": 6, "name": "End", "type": 3, "n": 1, "out": 0}]
    links = [{"from": "Start", "to": "Y"}, {"from": "Start", "to": "X"},
             {"from": "Start", "to": "A"},
             {"from": "Y", "to": "Cold", "cold_pri": 25, "a_prob": 1.0,
              "auto_time": 0.0, "man_time": -1.0},
             {"from": "X", "to": "Cold", "cold_pri": 50, "a_prob": 1.0,
              "auto_time": 0.0, "man_time": -1.0},
             {"from": "Cold", "to": "Join"},
             {"from": "A", "to": "Join"},
             {"from": "Join", "to": "End"}]
    dependent = block("A", dependent_life, id=A_ID, dep_type="RBDBlock",
                      dep_num=dependent_of, dep_idle=100.0,
                      sb_stress=dependent_stress)
    dependent.update(dependent_fields or {})
    return model([block("Y", peer_life, id=Y_ID, sb_stress=0.5),
                  block("X", peer_fail, peer_repair, id=X_ID, sb_stress=0.5),
                  dependent], nodes, links, run_length=run_length)


def sys_dependant(dependent_life: float, sibling_life: float,
                  sibling_repair: float, *, fields: dict | None = None,
                  zero_stress: bool = False, mission_phases: bool = False,
                  phase_values: tuple[float, ...] = (0.0, 1.0),
                  phase_durations: tuple[float, ...] = (50.0, 400.0),
                  run_length: float = 80.0) -> dict:
    """`Start -> A -> B -> End` with `A` sysDepending on the model's End node.

    `A`'s dependence target is the End node (`DepNum = -2`), so `B` failing
    takes `A` down with it and the dependency dispatch lands in whatever wait
    `A` is parked at -- which is how a *second* block's own failure reaches
    a wait `A` entered for a reason of its own. *fields* carries the
    dependence reaction (`dep_idle=100`, or `dep_fail=100` / `dep_pm=100` for
    the two induced ones).

    Two phase switches, because two different waits have to be reachable from
    the same topology:

    * *zero_stress* gives phase 1 a stress of `0.0`, which makes
      `combined_stress` zero and parks the block at `ZERO_STRESS` from t=0
      until the boundary at t=50.
    * *mission_phases* makes the second phase non-mission, which is what a
      `pmMisDefer` block needs before its PM will defer at all.
    """
    dependent = block("A", dependent_life, dep_type="RBDNode", dep_num=-2,
                      dep_idle=100.0)
    dependent.update(fields or {})
    spec = model([dependent, block("B", sibling_life, sibling_repair)],
                 *chain("A", "B"), run_length=run_length)
    if zero_stress:
        spec.update(phases(phase_values, phase_durations))
    elif mission_phases:
        spec.update(phases((1.0, 1.0), (200.0, 400.0)))
    return spec


# --------------------------------------------------------------------------
# The fixtures: one small model per claim, named for the model
# --------------------------------------------------------------------------

def far_life_timer() -> dict:
    """One block whose `Fixed` life outlasts `_FAR_FUTURE`, and a run that
    outlasts that too."""
    return model([block("A", 1.5e9)], *chain("A"), run_length=1.0e9)


def far_life_mission_release() -> dict:
    """A parked `pmMisDefer` PM released by its mission phase ending."""
    return model([block("A", FAR, **pm(pm_freq=100.0, pm_mis_defer=True))],
                 *chain("A"), **phases((0.5, 1.0), (200.0, 400.0)),
                 run_length=400.0)


def far_life_pool_grant() -> dict:
    """A parked block's abandoned PM resource request, granted late."""
    return model(
        [block("A", FAR, num_res=1, resource_name="R", res1_pm_rep=1,
               pm_req_defer=True, **pm(pm_freq=100.0)),
         block("B", 50.0, 60.0, num_res=1, resource_name="R", res1_pm_rep=1,
               **pm(pm_freq=100.0))],
        *chain("A", "B"), resources=[{"name": "R", "num_in": 1}],
        run_length=120.0)


def defer_trigger_hold() -> dict:
    """Two `maintDefer` blocks whose failures the defer trigger holds until t=100.

    A repairs from a shared spare pool that is empty (a spare arrives at t=110); B has an infinite
    supply. Both have a 2-hour pre-LDT. On release each is already in RepHold when its pre-LDT
    starts, so neither logs a second RepHold.
    """
    pool = {"name": "SP", "stock": 0, "new_spares": 1, "arrive_every": 110.0, "rso": 1}
    ldt = {"pre_ldt_dist": FIXED, "pre_ldt_params": [2.0, 0.0, 0.0]}
    return model([block("A", 10.0, 5.0, sp_type="Pooled", spare_pool="SP", **ldt),
                  block("B", 20.0, 5.0, **ldt)],
                 *chain("A", "B"), triggers=[trigger(100.0)], spare_pools=[pool],
                 comments=[{"element": "A", "comment": "maintDefer"},
                           {"element": "B", "comment": "maintDefer"}],
                 defer_trigger="T1", run_length=130.0)


def far_life_triggered_pm() -> dict:
    """A parked `pmTriggered` block whose PM trigger fires at t=100."""
    return model([block("A", FAR, **pm(pm_freq=5000.0, pm_triggered=True,
                                      pm_trig="T1"))],
                 *chain("A"), triggers=[trigger(100.0)], run_length=120.0)


def far_life_cold_promotion() -> dict:
    """A 1-of-2 cold node whose running peer fails and promotes the parked one."""
    nodes = [{"id": 1, "name": "Start", "type": 1, "n": 0, "out": 2},
             {"id": 3, "name": "Cold", "type": 2, "n": 2, "out": 1, "k": 1,
              "k_star": 1, "cold": 1},
             {"id": 4, "name": "End", "type": 3, "n": 1, "out": 0}]
    links = [{"from": "Start", "to": "A"}, {"from": "Start", "to": "B"},
             {"from": "A", "to": "Cold", "cold_pri": 25, "a_prob": 1.0,
              "auto_time": 0.0, "man_time": -1.0},
             {"from": "B", "to": "Cold", "cold_pri": 50, "a_prob": 1.0,
              "auto_time": 0.0, "man_time": -1.0},
             {"from": "Cold", "to": "End"}]
    return model([block("A", 50.0), block("B", FAR, sb_stress=0.5)], nodes,
                 links, run_length=60.0)


def far_life_dependence_idle() -> dict:
    return sys_dependant(FAR, 60.0, 500.0)


def far_life_induced_failure() -> dict:
    return sys_dependant(FAR, 60.0, 500.0,
                         fields={"dep_idle": 0.0, "dep_fail": 100.0})


def far_life_obpm() -> dict:
    """As above, but the PM frequency is longer than `_FAR_FUTURE`.

    `uses_pm` is what `draw_dependency_action`'s `GoToPM` arm requires
    (`dependency.py:591-601`), and a `uses_pm` block's PM clock races its
    life every pass: with a 5000-hour
    `PMFreq` the race hands the wait a 5000-hour life, which is far short of
    the 1e9-hour beyond-horizon threshold. A `PMFreq` past `_FAR_FUTURE`
    keeps the block parked where the claim needs it.
    """
    return sys_dependant(FAR, 60.0, 500.0,
                         fields={"dep_idle": 0.0, "dep_pm": 100.0,
                                 **pm(pm_freq=2.0e9)})


def far_life_standby_backflow() -> dict:
    """A parked block dependent on the cold peer the survey demotes."""
    return cold_dependent(30.0, 1.0e9, dependent_of=Y_ID, peer_fail=40.0,
                          peer_repair=40.0, run_length=100.0)


def kickoff_standby_backflow() -> dict:
    """The same back-flow at t=0, when the survey's kickoff demotion fires."""
    return cold_dependent(FAR, 500.0, dependent_of=X_ID, run_length=150.0)


def cold_fail_wait_standby() -> dict:
    """A live dependent whose cold peer goes Standby while it is in its
    ordinary fail-wait."""
    return cold_dependent(20.0, 500.0, dependent_of=Y_ID, peer_fail=60.0,
                          peer_repair=20.0, run_length=100.0)


def cold_fail_wait_sb_turnon() -> dict:
    """A live dependent promoted out of Standby with its own peer."""
    return cold_dependent(100.0, 300.0, dependent_of=X_ID, peer_fail=60.0,
                          peer_repair=10.0, run_length=100.0)


def cold_idle_sb_turnon() -> dict:
    """The same promotion, read by a dependent that is Idle at the time."""
    return cold_dependent(100.0, 500.0, dependent_of=X_ID, peer_fail=40.0,
                          peer_repair=20.0, run_length=100.0)


def cold_zero_stress_sb_turnon() -> dict:
    """A zero-stress dependent whose cold peer is promoted out of Standby."""
    spec = cold_dependent(30.0, 500.0, dependent_of=X_ID, dependent_stress=0.0,
                          peer_fail=40.0, peer_repair=20.0, run_length=80.0)
    spec.update(phases((0.0, 1.0), (50.0, 400.0)))
    return spec


def pm_prereqs_tell_timer() -> dict:
    """A PM with no prerequisites at all, so its own TELL hop runs long."""
    return model([block("A", 500.0, **pm())], *chain("A"), run_length=60.0)


def pm_sync_handback() -> dict:
    """A `pmMisDefer` PM borrowing a resource, with a phase change in
    progress at the hand-back."""
    return model(
        [block("A", 500.0, num_res=1, resource_name="R", res1_pm_rep=1,
               **pm(pm_mis_defer=True))],
        *chain("A"),
        phases=[{"name": "Ph1", "duration_dist": FIXED, "params": [50.0],
                 "mission": 1},
                {"name": "Ph2", "duration_dist": FIXED, "params": [400.0],
                 "mission": 0}],
        phase_states={"A": ["A: 0.5", "A: 1.0"]},
        resources=[{"name": "R", "num_in": 1}], run_length=60.0)


def pm_phase_change_at_perform_tell() -> dict:
    """A phase boundary at exactly the PM-due instant, on the no-spare path."""
    return model([block("A", 500.0, **no_req_defer(), **pm())],
                 *chain("A"), **phases((0.5, 1.0), (50.0, 400.0)),
                 run_length=80.0)


def pm_phase_change_at_handback() -> dict:
    """The same boundary on the resource-pool path, so the hand-back is the
    last hop before `PerformPM`."""
    return model([block("A", 500.0, num_res=1, resource_name="R",
                       res1_pm_rep=1, **no_req_defer(), **pm())],
                 *chain("A"), resources=[{"name": "R", "num_in": 1}],
                 **phases((0.5, 1.0), (50.0, 400.0)), run_length=60.0)


def misdefer_release_at_fail_wait() -> dict:
    """A `pmMisDefer` PM deferred by its mission phase and released at the
    boundary, at an ordinary-length life."""
    return model([block("A", 500.0, **pm(pm_mis_defer=True))],
                 *chain("A"), **phases((0.5, 1.0), (200.0, 400.0)),
                 run_length=400.0)


def misdefer_run_dependence_idle() -> dict:
    """A dependency dispatch landing in the `ELSE Run` hop itself."""
    return sys_dependant(500.0, 50.0, 20.0, fields=pm(pm_mis_defer=True),
                         mission_phases=True, run_length=60.0)


def misdefer_run_induced_failure() -> dict:
    """An induced failure landing in the `ELSE Run` hop itself."""
    return sys_dependant(500.0, 40.0, 20.0,
                         fields={"dep_idle": 0.0, "dep_fail": 100.0,
                                 **pm(pm_mis_defer=True)},
                         mission_phases=True, run_length=120.0)


def pm_sync_wait_run() -> dict:
    """A `pmMisDefer` block whose scheduled PM comes due inside a phase
    change."""
    return model([block("A", 500.0, **pm(pm_mis_defer=True))],
                 *chain("A"), **phases((1.0, 1.0), (50.0, 400.0)),
                 run_length=60.0)


def pm_sync_wait_tail() -> dict:
    """The same, with the pmMisDefer tail's own sync wait the one that parks."""
    return model([block("A", 500.0, **pm(pm_mis_defer=True))],
                 *chain("A"), **phases((1.0, 0.5), (50.0, 400.0)),
                 run_length=60.0)


def pm_wait_custom_spare() -> dict:
    """A PM whose private Custom queue is empty when the PM comes due."""
    return model([block("A", 500.0, **no_req_defer(),
                       **pm(pm_freq=25.0, pm_spare=True),
                       sp_type="Custom", stock=1, new_spares=1, rso=1,
                       arrive_every=40.0),
                  block("B", 45.0, 10.0, sp_type="Custom", stock=1,
                        new_spares=1, rso=1, arrive_every=40.0)],
                 *chain("A", "B"), run_length=120.0)


def pm_wait_pool_spare() -> dict:
    """A PM whose shared spare pool is empty when the PM comes due."""
    pool = {"name": "SP", "stock": 1, "new_spares": 1, "arrive_every": 40.0,
            "rso": 1}
    return model([block("A", 500.0, **no_req_defer(),
                       **pm(pm_freq=25.0, pm_spare=True),
                       sp_type="Pooled", spare_pool="SP"),
                  block("B", 45.0, 10.0, sp_type="Pooled", spare_pool="SP")],
                 *chain("A", "B"), spare_pools=[pool], run_length=80.0)


def pm_wait_resource() -> dict:
    """A PM whose repair sibling holds the one resource unit."""
    return model([block("A", 500.0, num_res=1, resource_name="R",
                       res1_pm_rep=1, **no_req_defer(), **pm(pm_freq=25.0)),
                  block("B", 20.0, 10.0, num_res=1, resource_name="R",
                        res1_rep=1)],
                 *chain("A", "B"), resources=[{"name": "R", "num_in": 1}],
                 run_length=40.0)


def repair_wait_custom_spare() -> dict:
    """A repair whose private Custom queue is empty when it starts."""
    return model([block("A", 20.0, 10.0, sp_type="Custom", stock=1,
                        new_spares=1, rso=1, arrive_every=40.0),
                  block("B", 30.0, 10.0, sp_type="Custom", stock=1,
                        new_spares=1, rso=1, arrive_every=40.0)],
                 *chain("A", "B"), run_length=120.0)


def stale_grant_during_repair() -> dict:
    """A block whose abandoned PM resource request is granted while it is
    repairing."""
    return model(
        [block("A", 500.0, num_res=1, resource_name="R", res1_pm_rep=1,
               pm_req_defer=True, **pm(pm_freq=100.0)),
         block("B", 50.0, 60.0, num_res=1, resource_name="R", res1_pm_rep=1,
               **pm(pm_freq=100.0))],
        *chain("A", "B"), resources=[{"name": "R", "num_in": 1}],
        run_length=120.0)


def zero_stress_phase_change() -> dict:
    """A phased block whose first phase has stress zero."""
    return model([block("A", 500.0)], *chain("A"),
                 **phases((0.0, 1.0), (50.0, 400.0)), run_length=60.0)


def zero_stress_dependence_idle() -> dict:
    return sys_dependant(500.0, 30.0, 20.0, zero_stress=True, run_length=60.0)


def zero_stress_induced_failure() -> dict:
    return sys_dependant(500.0, 25.0, 20.0, zero_stress=True,
                         fields={"dep_idle": 0.0, "dep_fail": 100.0},
                         run_length=80.0)


def zero_stress_obpm() -> dict:
    return sys_dependant(500.0, 25.0, 20.0, zero_stress=True,
                         fields={"dep_idle": 0.0, "dep_pm": 100.0, **pm()},
                         run_length=100.0)


def fail_wait_induced_failure() -> dict:
    return sys_dependant(500.0, 40.0, 20.0,
                         fields={"dep_idle": 0.0, "dep_fail": 100.0},
                         run_length=60.0)


def fail_wait_obpm() -> dict:
    return sys_dependant(500.0, 40.0, 20.0,
                         fields={"dep_idle": 0.0, "dep_pm": 100.0, **pm()},
                         run_length=60.0)


def idle_phase_change() -> dict:
    """A dependency-idled block, with a phase boundary while it is Idle."""
    spec = sys_dependant(500.0, 30.0, 500.0, run_length=100.0)
    spec.update(phases((1.0, 0.5), (50.0, 400.0)))
    return spec


def idle_triggered_pm() -> dict:
    """A dependency-idled `pmTriggered` block, with its PM trigger firing."""
    spec = sys_dependant(500.0, 30.0, 500.0,
                         fields=pm(pm_triggered=True, pm_trig="T1"),
                         run_length=100.0)
    spec["triggers"] = [trigger(40.0)]
    return spec


def zero_stress_triggered_pm() -> dict:
    """A zero-stress `pmTriggered` block, with its PM trigger firing."""
    spec = model([block("A", 500.0, **pm(pm_freq=5000.0, pm_triggered=True,
                                         pm_trig="T1"))],
                 *chain("A"), triggers=[trigger(25.0)], run_length=150.0)
    spec.update(phases((0.0, 1.0), (50.0, 400.0)))
    return spec


# --------------------------------------------------------------------------
# Measuring a fixture
# --------------------------------------------------------------------------

class WakeLog(sc._Recorder):
    """The corpus's own observer, plus the block each wake was delivered to.

    `state_coverage._Recorder.on_wake` throws the block away and keeps the
    `(site, reason)` cell, which is right for a coverage matrix and wrong for
    a test: three blocks in one model reach the same cell, and a claim about
    *which* one is the whole point. Subclassing rather than reimplementing
    keeps `wakes` -- the cell set `state_reachability.pinned_by_suite()`
    reads -- byte-identical to the corpus scan's; `delivered` is the extra
    list, in wake order.
    """

    def __init__(self) -> None:
        super().__init__()
        self.delivered: list[Wake] = []

    def on_wake(self, block, site, wake) -> None:  # noqa: ANN001
        super().on_wake(block, site, wake)
        self.delivered.append((block.name, str(site),
                               TIMER if wake is None else str(wake.reason)))


def observe(spec: dict) -> tuple[Run, WakeLog]:
    """Build and run *spec* in memory, and report what it reached."""
    log = WakeLog()
    with observing(log):
        result = run(spec)
    assert result.draws == 0, (
        f"this fixture drew {result.draws} time(s) from a stream; every "
        f"distribution in it is meant to be `Fixed`, which consumes none "
        f"(rule B2)")
    return result, log


def reached(log: WakeLog, site: str, reason: str, block: str | None = None) -> bool:
    """Did *log* record this wait ending this way?

    *block* narrows the claim to one object. The three-block fixtures reach
    the same cell from more than one block, and which block is the claim.
    """
    return any(w[1] == site and w[2] == reason
               and (block is None or w[0] == block)
               for w in log.delivered)


def assert_reached(log: WakeLog, block: str, site: str, reason: str) -> None:
    """The claim, as a failure a reader can act on."""
    assert reached(log, site, reason, block), (
        f"no wait ended with {reason!r} at {site!r} for block {block!r}. "
        f"This run reached: "
        + (", ".join(f"{b} {s} x {r}" for b, s, r in log.delivered)
           or "nothing"))


#: Test name -> the fixture it runs. `state_reachability.suite_fixtures()`
#: imports this module and runs each of these under the same observer the
#: corpus scan uses, which is how `already_pinned` is derived: a fixture that
#: stops reaching its cell drops the manifest row, and a fixture added here
#: raises the `[ratchet] pinned` baseline with no edit to the gate.
WAIT_SITE_FIXTURES: dict[str, object] = {
    "test_a_deferred_failure_waits_for_its_trigger":
        defer_trigger_hold,
    "test_beyond_horizon_wait_ends_a_billion_hours_in":
        far_life_timer,
    "test_a_parked_block_is_released_by_its_mission_phase_ending":
        far_life_mission_release,
    "test_a_misdefer_run_hop_runs_its_full_length":
        far_life_mission_release,
    "test_a_scheduled_pm_hop_runs_its_full_length":
        far_life_mission_release,
    "test_the_misdefer_tail_hop_runs_its_full_length":
        far_life_mission_release,
    "test_a_pm_with_no_prerequisites_pays_its_own_tell_boundary":
        pm_prereqs_tell_timer,
    "test_a_parked_blocks_abandoned_pm_request_is_granted_late":
        far_life_pool_grant,
    "test_a_parked_triggered_block_is_released_by_its_pm_trigger":
        far_life_triggered_pm,
    "test_a_parked_cold_peer_is_switched_on_by_its_mates_failure":
        far_life_cold_promotion,
    "test_a_parked_block_is_idled_by_its_downstream_sibling":
        far_life_dependence_idle,
    "test_a_parked_block_fails_when_a_dependency_induces_it":
        far_life_induced_failure,
    "test_a_parked_block_goes_for_pm_when_a_dependency_says_obpm":
        far_life_obpm,
    "test_a_parked_block_is_backflowed_a_standby_from_its_cold_peer":
        far_life_standby_backflow,
    "test_a_cold_peer_demoted_at_kickoff_backflows_standby_to_its_dependent":
        kickoff_standby_backflow,
    "test_a_dependent_in_its_fail_wait_hears_its_peer_go_standby":
        cold_fail_wait_standby,
    "test_a_dependent_promoted_out_of_standby_hears_sb_turnon":
        cold_fail_wait_sb_turnon,
    "test_a_demoted_cold_peer_is_switched_off_in_its_fail_wait":
        far_life_standby_backflow,
    "test_a_promoted_cold_peer_is_switched_on_in_its_fail_wait":
        far_life_standby_backflow,
    "test_an_idle_dependent_hears_its_peer_go_standby":
        kickoff_standby_backflow,
    "test_an_idle_dependent_hears_its_peers_promotion":
        cold_idle_sb_turnon,
    "test_a_zero_stress_dependent_hears_its_peers_promotion":
        cold_zero_stress_sb_turnon,
    "test_a_zero_stress_block_is_woken_by_the_phase_boundary":
        zero_stress_phase_change,
    "test_a_zero_stress_block_is_idled_by_its_downstream_sibling":
        zero_stress_dependence_idle,
    "test_a_zero_stress_block_fails_when_a_dependency_induces_it":
        zero_stress_induced_failure,
    "test_a_zero_stress_block_takes_obpm_from_a_dependency":
        zero_stress_obpm,
    "test_a_dependent_fails_in_its_fail_wait_when_induced":
        fail_wait_induced_failure,
    "test_a_dependent_takes_obpm_in_its_fail_wait":
        fail_wait_obpm,
    "test_a_phase_boundary_lands_in_the_fail_wait_of_a_deferred_pm":
        misdefer_release_at_fail_wait,
    "test_a_misdefer_pm_is_released_at_its_fail_wait":
        misdefer_release_at_fail_wait,
    "test_a_dependency_dispatch_lands_in_the_misdefer_else_run_hop":
        misdefer_run_dependence_idle,
    "test_an_induced_failure_lands_in_the_misdefer_else_run_hop":
        misdefer_run_induced_failure,
    "test_a_misdefer_pm_parking_on_the_sync_trigger_at_its_handback":
        pm_sync_handback,
    "test_a_misdefer_pm_pays_the_misdefer_tail_hop_at_the_handback":
        pm_sync_handback,
    "test_a_scheduled_pm_parks_on_the_sync_trigger":
        pm_sync_wait_run,
    "test_a_misdefer_tail_parks_on_the_sync_trigger":
        pm_sync_wait_tail,
    "test_a_phase_boundary_lands_in_the_perform_pm_tell_hop":
        pm_phase_change_at_perform_tell,
    "test_a_phase_boundary_lands_in_the_pm_handback_hop":
        pm_phase_change_at_handback,
    "test_a_pm_waits_in_pmhold_for_a_custom_spare":
        pm_wait_custom_spare,
    "test_a_pm_waits_in_pmhold_for_a_pooled_spare":
        pm_wait_pool_spare,
    "test_a_pm_waits_in_pmhold_for_its_resource":
        pm_wait_resource,
    "test_a_repair_waits_for_a_custom_spare":
        repair_wait_custom_spare,
    "test_an_idle_block_is_woken_by_the_phase_boundary":
        idle_phase_change,
    "test_an_idle_triggered_block_is_released_by_its_pm_trigger":
        idle_triggered_pm,
    "test_a_zero_stress_block_is_released_by_its_pm_trigger":
        zero_stress_triggered_pm,
    "test_a_stale_pm_grant_lands_in_the_fail_wait_and_not_the_repair_wait":
        stale_grant_during_repair,
}


# --------------------------------------------------------------------------
# The tests: one claim per cell
# --------------------------------------------------------------------------


def test_beyond_horizon_wait_ends_a_billion_hours_in():
    """A block whose life outlasts `_FAR_FUTURE` is parked at
    `BEYOND_HORIZON`, not at the ordinary fail-wait, and its wait ends a
    billion hours before the run does.

    Why: `_fail_wait` compares the *wait's* full length against the run's
    horizon (5507`), and `BlockEnv` is built with
    `block_horizon`, which `kickoff.block_horizon` sets to `_FAR_FUTURE`
    for every termination type (`kickoff.py:480-485`) -- so that horizon is
    the BLOCK's, not the model's. A life past 1e9 h therefore never reaches
    its own end, and the block sits in the beyond-horizon branch of the same
    fail-wait until the run's own `EndSimulation` lets it go `Done`.

    Setup: one block, `Fixed(1.5e9)` life, and a model `run_length` of
    1e9 h so the wait's end and the run's end are the same instant.

    Expected: `Good` at t=0, `Done` at t=1e9, and the only wake the block
    ever takes is that wait running out on its own clock.

    Source: raptor/core/engine/wait_sites.py `WaitSite.BEYOND_HORIZON`
      (5507`), block_lifecycle.py:2068
      (`_fail_wait`'s `beyond_horizon` branch)
    """
    result, log = observe(far_life_timer())
    assert_reached(log, "A", "beyond_horizon", TIMER)
    assert_timeline(result, [
        (0.0, 'Block', 'A', '--', 'Good'),
        (1000000000.0, 'Block', 'A', 'Good', 'Done'),
        (1000000000.0, 'Block', 'A', 'Done', 'Done'),
        (1000000000.0, 'System', 'Phase_change', '--', 'Mission_success'),
        (1000000000.0, 'System', 'Simulation_Terminate', '--', '--'),
    ])


def test_a_parked_block_is_released_by_its_mission_phase_ending():
    """A `pmMisDefer` PM deferred by its mission phase is released from the
    beyond-horizon wait, with `Start_Deferred_PM`.

    Why: `cascade_phase.py:286-295` releases every deferred block when the
    model leaves Mission, and a deferred block is one whose PM came due
    inside a mission phase -- so it is parked, not running, and the phase
    boundary's release is what ends its wait. The same boundary also carries
    the `pmMisDefer` block through its own three zero-length hops, which is
    where the other five cells in this model come from.

    Setup: one block with a `Fixed(2e9)` life, a `pmMisDefer` PM on a
    100-hour clock, and two phases of 200 h (mission) then 400 h.

    Expected: the block is `PM_is_due` at t=100, deferred at t=200 by the
    boundary, and its wait ends with `Start_Deferred_PM`; then the PM runs.

    Source: raptor/core/engine/wait_sites.py `WaitSite.BEYOND_HORIZON`,
      cascade_phase.py:295, wait_sites.py `WaitSite.PM_MISDEFER_RUN`,
     `WaitSite.PM_SCHEDULED_HOP`,
      `WaitSite.PM_TAIL_HOP` 
    """
    result, log = observe(far_life_mission_release())
    assert_reached(log, "A", "beyond_horizon", "Start_Deferred_PM")
    assert_timeline(result, [
        (0.0, 'System', 'State_change', '--', 'Mission'),
        (0.0, 'Block', 'A', '--', 'Good'),
        (0.0, 'Block', 'A', 'Good', 'StessLevel'),
        (100.0, 'Block', 'A', 'StessLevel', 'PM_is_due'),
        (100.0, 'Block', 'A', 'PM_is_due', 'StessLevel'),
        (200.0, 'System', 'Phase_change', '--', 'Phase_change'),
        (200.0, 'System', 'Phase_change', 'Phase_change', 'Mission_success'),
        (200.0, 'System', 'State_change', 'Mission', 'Green'),
        (200.0, 'Block', 'A', 'StessLevel', 'PM_is_due'),
        (200.0, 'Block', 'A', 'PM_is_due', 'PM'),
        (200.0, 'Node', 'end', '--', 'NodePM'),
        (200.0, 'System', 'State_change', 'Green', 'Red'),
        (205.0, 'Block', 'A', 'PM', 'PM_complete'),
        (205.0, 'Block', 'A', 'PM_complete', 'Good'),
        (205.0, 'Node', 'end', 'NodePM', 'Good'),
        (205.0, 'System', 'State_change', 'Red', 'Green'),
        (305.0, 'Block', 'A', 'Good', 'PM_is_due'),
        (305.0, 'Block', 'A', 'PM_is_due', 'PM'),
        (305.0, 'Node', 'end', 'Good', 'NodePM'),
        (305.0, 'System', 'State_change', 'Green', 'Red'),
        (310.0, 'Block', 'A', 'PM', 'PM_complete'),
        (310.0, 'Block', 'A', 'PM_complete', 'Good'),
        (310.0, 'Node', 'end', 'NodePM', 'Good'),
        (310.0, 'System', 'State_change', 'Red', 'Green'),
        (400.0, 'Block', 'A', 'Good', 'Done'),
        (400.0, 'System', 'Simulation_Terminate', '--', '--'),
    ])


def test_a_misdefer_run_hop_runs_its_full_length():
    """The `ELSE Run` hop a deferred PM pays instead of continuing to
    `PerformPM` is a real wait, and it runs its own length.

    Why: source's defer arm does `Run;` again rather than falling through
    to `PerformPM`, which is a scheduler round-trip
    the port models as its own wait site. `WAKE_POLICY` says the wake is not
    read, so the only thing to pin is that the hop exists and is entered.

    Setup: the same model as the claim above.

    Expected: `pm_misdefer_run` runs at t=200 and is entered exactly once.

    Source: raptor/core/engine/wait_sites.py `WaitSite.PM_MISDEFER_RUN`
    """
    result, log = observe(far_life_mission_release())
    assert_reached(log, "A", "pm_misdefer_run", TIMER)
    assert_timeline(result, [
        (0.0, 'System', 'State_change', '--', 'Mission'),
        (0.0, 'Block', 'A', '--', 'Good'),
        (0.0, 'Block', 'A', 'Good', 'StessLevel'),
        (100.0, 'Block', 'A', 'StessLevel', 'PM_is_due'),
        (100.0, 'Block', 'A', 'PM_is_due', 'StessLevel'),
        (200.0, 'System', 'Phase_change', '--', 'Phase_change'),
        (200.0, 'System', 'Phase_change', 'Phase_change', 'Mission_success'),
        (200.0, 'System', 'State_change', 'Mission', 'Green'),
        (200.0, 'Block', 'A', 'StessLevel', 'PM_is_due'),
        (200.0, 'Block', 'A', 'PM_is_due', 'PM'),
        (200.0, 'Node', 'end', '--', 'NodePM'),
        (200.0, 'System', 'State_change', 'Green', 'Red'),
        (205.0, 'Block', 'A', 'PM', 'PM_complete'),
        (205.0, 'Block', 'A', 'PM_complete', 'Good'),
        (205.0, 'Node', 'end', 'NodePM', 'Good'),
        (205.0, 'System', 'State_change', 'Red', 'Green'),
        (305.0, 'Block', 'A', 'Good', 'PM_is_due'),
        (305.0, 'Block', 'A', 'PM_is_due', 'PM'),
        (305.0, 'Node', 'end', 'Good', 'NodePM'),
        (305.0, 'System', 'State_change', 'Green', 'Red'),
        (310.0, 'Block', 'A', 'PM', 'PM_complete'),
        (310.0, 'Block', 'A', 'PM_complete', 'Good'),
        (310.0, 'Node', 'end', 'NodePM', 'Good'),
        (310.0, 'System', 'State_change', 'Red', 'Green'),
        (400.0, 'Block', 'A', 'Good', 'Done'),
        (400.0, 'System', 'Simulation_Terminate', '--', '--'),
    ])


def test_a_scheduled_pm_hop_runs_its_full_length():
    """The `WAIT DURATION 0.0` a `pmMisDefer` scheduled PM pays before
    `DetermineNewState("PM is due")` runs its own length.

    Why: it is a separate TELL boundary from the defer hop above, and
    `WAKE_POLICY` records that nothing may interrupt it -- so a test that
    only counted cells would miss its disappearance entirely.

    Setup: the same model as `test_a_parked_block_is_released_...`.

    Expected: the hop is taken at t=100 and again at t=200, both by timer.

    Source: raptor/core/engine/wait_sites.py `WaitSite.PM_SCHEDULED_HOP`
    """
    result, log = observe(far_life_mission_release())
    assert_reached(log, "A", "pm_scheduled_hop", TIMER)
    assert_timeline(result, [
        (0.0, 'System', 'State_change', '--', 'Mission'),
        (0.0, 'Block', 'A', '--', 'Good'),
        (0.0, 'Block', 'A', 'Good', 'StessLevel'),
        (100.0, 'Block', 'A', 'StessLevel', 'PM_is_due'),
        (100.0, 'Block', 'A', 'PM_is_due', 'StessLevel'),
        (200.0, 'System', 'Phase_change', '--', 'Phase_change'),
        (200.0, 'System', 'Phase_change', 'Phase_change', 'Mission_success'),
        (200.0, 'System', 'State_change', 'Mission', 'Green'),
        (200.0, 'Block', 'A', 'StessLevel', 'PM_is_due'),
        (200.0, 'Block', 'A', 'PM_is_due', 'PM'),
        (200.0, 'Node', 'end', '--', 'NodePM'),
        (200.0, 'System', 'State_change', 'Green', 'Red'),
        (205.0, 'Block', 'A', 'PM', 'PM_complete'),
        (205.0, 'Block', 'A', 'PM_complete', 'Good'),
        (205.0, 'Node', 'end', 'NodePM', 'Good'),
        (205.0, 'System', 'State_change', 'Red', 'Green'),
        (305.0, 'Block', 'A', 'Good', 'PM_is_due'),
        (305.0, 'Block', 'A', 'PM_is_due', 'PM'),
        (305.0, 'Node', 'end', 'Good', 'NodePM'),
        (305.0, 'System', 'State_change', 'Green', 'Red'),
        (310.0, 'Block', 'A', 'PM', 'PM_complete'),
        (310.0, 'Block', 'A', 'PM_complete', 'Good'),
        (310.0, 'Node', 'end', 'NodePM', 'Good'),
        (310.0, 'System', 'State_change', 'Red', 'Green'),
        (400.0, 'Block', 'A', 'Good', 'Done'),
        (400.0, 'System', 'Simulation_Terminate', '--', '--'),
    ])


def test_the_misdefer_tail_hop_runs_its_full_length():
    """The `pmMisDefer` tail's own `WAIT DURATION 0.0` -- paid by a block
    with no resource to wait for -- runs its full length.

    Why: it is the one hop between the deferred release and the prereq TELL
    that belongs to the misDefer arm rather than to `ObtainPMprereqs`,
   so it is a distinct site in the matrix even
    though it is a zero-length wait.

    Setup: the same model as `test_a_parked_block_is_released_...`.

    Expected: the hop is taken at t=200 after the release, by timer.

    Source: raptor/core/engine/wait_sites.py `WaitSite.PM_TAIL_HOP`
    """
    result, log = observe(far_life_mission_release())
    assert_reached(log, "A", "pm_tail_hop", TIMER)
    assert_timeline(result, [
        (0.0, 'System', 'State_change', '--', 'Mission'),
        (0.0, 'Block', 'A', '--', 'Good'),
        (0.0, 'Block', 'A', 'Good', 'StessLevel'),
        (100.0, 'Block', 'A', 'StessLevel', 'PM_is_due'),
        (100.0, 'Block', 'A', 'PM_is_due', 'StessLevel'),
        (200.0, 'System', 'Phase_change', '--', 'Phase_change'),
        (200.0, 'System', 'Phase_change', 'Phase_change', 'Mission_success'),
        (200.0, 'System', 'State_change', 'Mission', 'Green'),
        (200.0, 'Block', 'A', 'StessLevel', 'PM_is_due'),
        (200.0, 'Block', 'A', 'PM_is_due', 'PM'),
        (200.0, 'Node', 'end', '--', 'NodePM'),
        (200.0, 'System', 'State_change', 'Green', 'Red'),
        (205.0, 'Block', 'A', 'PM', 'PM_complete'),
        (205.0, 'Block', 'A', 'PM_complete', 'Good'),
        (205.0, 'Node', 'end', 'NodePM', 'Good'),
        (205.0, 'System', 'State_change', 'Red', 'Green'),
        (305.0, 'Block', 'A', 'Good', 'PM_is_due'),
        (305.0, 'Block', 'A', 'PM_is_due', 'PM'),
        (305.0, 'Node', 'end', 'Good', 'NodePM'),
        (305.0, 'System', 'State_change', 'Green', 'Red'),
        (310.0, 'Block', 'A', 'PM', 'PM_complete'),
        (310.0, 'Block', 'A', 'PM_complete', 'Good'),
        (310.0, 'Node', 'end', 'NodePM', 'Good'),
        (310.0, 'System', 'State_change', 'Red', 'Green'),
        (400.0, 'Block', 'A', 'Good', 'Done'),
        (400.0, 'System', 'Simulation_Terminate', '--', '--'),
    ])


def test_a_pm_with_no_prerequisites_pays_its_own_tell_boundary():
    """A PM with nothing to obtain skips the prereq TELL boundary, so
    `_perform_pm`'s own `TELL PerformPM` hop runs to its full length.

    Why: `pm_handback_taken` stays `False` when no resource step succeeded,
    and `_perform_pm` only skips its own hop in that case
    (`block_lifecycle.py:2988`) -- so the *absence* of a prereq is what
    makes this cell reachable at all. It is the most-reached unpinned cell
    in the matrix (118 of the 283 corpus models reach it), which is why the
    behaviour suite had no test for it.

    Setup: one block, a `Fixed` 50-hour PM on a `Fixed` 500-hour life.

    Expected: the block pays `pm_prereqs_tell` at t=25 and
    `perform_pm_tell` immediately after, both by timer, then PMs 25-30.

    Source: raptor/core/engine/wait_sites.py `WaitSite.PERFORM_PM_TELL`,
     block_lifecycle.py:2999
    """
    result, log = observe(pm_prereqs_tell_timer())
    assert_reached(log, "A", "perform_pm_tell", TIMER)
    assert_timeline(result, [
        (0.0, 'Block', 'A', '--', 'Good'),
        (50.0, 'Block', 'A', 'Good', 'PM_is_due'),
        (50.0, 'Block', 'A', 'PM_is_due', 'PM'),
        (50.0, 'Node', 'end', '--', 'NodePM'),
        (50.0, 'System', 'State_change', '--', 'Red'),
        (55.0, 'Block', 'A', 'PM', 'PM_complete'),
        (55.0, 'Block', 'A', 'PM_complete', 'Good'),
        (55.0, 'Node', 'end', 'NodePM', 'Good'),
        (55.0, 'System', 'State_change', 'Red', 'Green'),
        (60.0, 'Block', 'A', 'Good', 'Done'),
        (60.0, 'System', 'Phase_change', '--', 'Mission_failure'),
        (60.0, 'System', 'Simulation_Terminate', '--', '--'),
    ])


def test_a_parked_blocks_abandoned_pm_request_is_granted_late():
    """A `pmReqDefer` block parked past the horizon has its abandoned PM
    resource request granted when a sibling's repair hands the unit back.

    Why: the deferral registers the request and then abandons the PM
    attempt (`block_lifecycle.py:2925`), so the request outlives the block's
    own PM path and the grant finds it parked somewhere else entirely --
    `_on_fail_wait_wake` has to reconcile the stale grant rather than
    assume it was waiting for one. `WAKE_POLICY.BEYOND_HORIZON` lists
    `Pool_Available` for exactly this case.

    Setup: `A` parked past the horizon with `num_res=1`/`#Res1pmRep=1` and
    `pmReqDefer`, `B` failing at t=50 and repairing until t=110, on a
    one-unit `R` pool.

    Expected: `A` is granted the unit at t=110 and its wait ends with
    `Pool_Available`, and `A` goes on to PM at t=110.

    Source: raptor/core/engine/wait_sites.py
      `WAKE_POLICY[BEYOND_HORIZON]` (`Pool_Available`), block_lifecycle.py:1135
      (`_on_fail_wait_wake`)
    """
    result, log = observe(far_life_pool_grant())
    assert_reached(log, "A", "beyond_horizon", "Pool_Available")
    assert_timeline(result, [
        (0.0, 'Block', 'A', '--', 'Good'),
        (0.0, 'Block', 'B', '--', 'Good'),
        (50.0, 'Block', 'B', 'Good', 'Failed'),
        (50.0, 'Block', 'B', 'Failed', 'Repairing'),
        (50.0, 'Node', 'end', '--', 'Bad'),
        (50.0, 'System', 'State_change', '--', 'Red'),
        (100.0, 'Block', 'A', 'Good', 'PM_is_due'),
        (100.0, 'Block', 'A', 'PM_is_due', 'pmReqDefer'),
        (110.0, 'Block', 'B', 'Repairing', 'Repaired'),
        (110.0, 'Block', 'B', 'Repaired', 'Good'),
        (110.0, 'Node', 'end', 'Bad', 'Good'),
        (110.0, 'Block', 'A', 'pmReqDefer', 'Resources'),
        (110.0, 'Block', 'A', 'Resources', 'PM'),
        (110.0, 'Node', 'N2', '--', 'NodePM'),
        (110.0, 'Node', 'end', 'Good', 'NodePM'),
        (115.0, 'Block', 'A', 'PM', 'PM_complete'),
        (115.0, 'Block', 'A', 'PM_complete', 'Good'),
        (115.0, 'Node', 'N2', 'NodePM', 'Good'),
        (115.0, 'Node', 'end', 'NodePM', 'Good'),
        (115.0, 'System', 'State_change', 'Red', 'Green'),
        (120.0, 'Block', 'A', 'Good', 'Done'),
        (120.0, 'Block', 'B', 'Good', 'Done'),
        (120.0, 'System', 'Phase_change', '--', 'Mission_failure'),
        (120.0, 'System', 'Simulation_Terminate', '--', '--'),
    ])


def test_a_parked_triggered_block_is_released_by_its_pm_trigger():
    """A `pmTriggered` block parked past the horizon is released by its own
    PM trigger firing in mid-wait.

    Why: a triggered block's PM clock is not raced against its life at all
    (gate the race on `usesPM AND NOT pmTriggered`),
    which is the only way to keep a block parked for a very long time *and*
    still have a PM arrive. `ControlPMtriggers`' eligibility test admits a
    Running block, so the fire lands in whatever wait it is in.

    Setup: one block, `Fixed(2e9)` life, a trigger scheduled at t=100.

    Expected: the trigger's release ends the beyond-horizon wait at t=100,
    the PM starts at once and completes at t=105, and the block is `Done`
    at the run's end at t=120.

    Source: raptor/core/engine/wait_sites.py `WaitSite.BEYOND_HORIZON`,
      kickoff.py:1003 (`ControlPMtriggers`)
    """
    result, log = observe(far_life_triggered_pm())
    assert_reached(log, "A", "beyond_horizon", "Triggered_PM_Due")
    assert_timeline(result, [
        (0.0, 'Block', 'A', '--', 'Good'),
        (100.0, 'Trigger', 'T1', '--', 'Fired'),
        (100.0, 'Block', 'A', 'Good', 'PM_is_due'),
        (100.0, 'Block', 'A', 'PM_is_due', 'PM'),
        (100.0, 'Node', 'end', '--', 'NodePM'),
        (100.0, 'System', 'State_change', '--', 'Red'),
        (105.0, 'Block', 'A', 'PM', 'PM_complete'),
        (105.0, 'Block', 'A', 'PM_complete', 'Good'),
        (105.0, 'Node', 'end', 'NodePM', 'Good'),
        (105.0, 'System', 'State_change', 'Red', 'Green'),
        (120.0, 'Block', 'A', 'Good', 'Done'),
        (120.0, 'System', 'Phase_change', '--', 'Mission_failure'),
        (120.0, 'System', 'Simulation_Terminate', '--', '--'),
    ])


def test_a_parked_cold_peer_is_switched_on_by_its_mates_failure():
    """The standby member of a 1-of-2 cold group is switched on by its
    running mate's failure, and takes the `Switched_On` in its own
    beyond-horizon wait.

    Why: `_switch_interrupt` applies the state change first and wakes second
    (`cascade_cold.py:270-278`), so the promoted block is `Good` by the time
    its own suspended wait resumes -- which is why this lands in
    `BEYOND_HORIZON` and not in the ordinary fail-wait its life would
    otherwise use.

    Setup: `A` (the running peer) fails at t=50 and repairs at t=60; `B` has
    a `Fixed(2e9)` life and a 0.5 standby stress, so it is parked past the
    horizon while it waits.

    Expected: `B Standby -> Good` at t=50 with `Prior=Standby`, and its
    beyond-horizon wait ends with `Switched_On`.

    Source: raptor/core/engine/wait_sites.py `WaitSite.BEYOND_HORIZON`,
      cold_standby.py `run_cold_logic`'s switch-on arm,
     cascade_cold.py:278
    """
    result, log = observe(far_life_cold_promotion())
    assert_reached(log, "B", "beyond_horizon", "Switched_On")
    assert_timeline(result, [
        (0.0, 'Block', 'A', '--', 'Good'),
        (0.0, 'Block', 'B', '--', 'Good'),
        (0.0, 'Block', 'B', 'Good', 'Standby'),
        (0.0, 'Node', 'Cold', '--', 'Good'),
        (0.0, 'Block', 'B', 'Standby', 'StessLevel'),
        (50.0, 'Block', 'A', 'Good', 'Failed'),
        (50.0, 'Block', 'A', 'Failed', 'Repairing'),
        (50.0, 'Block', 'B', 'StessLevel', 'Good'),
        (50.0, 'Node', 'Cold', 'Good', 'Degraded'),
        (50.0, 'System', 'State_change', '--', 'Yellow'),
        (60.0, 'Block', 'A', 'Repairing', 'Repaired'),
        (60.0, 'Block', 'A', 'Repaired', 'Good'),
        (60.0, 'Block', 'A', 'Good', 'Standby'),
        (60.0, 'Node', 'Cold', 'Degraded', 'Good'),
        (60.0, 'Block', 'A', 'Standby', 'Done'),
        (60.0, 'Block', 'B', 'Good', 'Done'),
        (60.0, 'System', 'State_change', 'Yellow', 'Green'),
        (60.0, 'System', 'Phase_change', '--', 'Mission_success'),
        (60.0, 'System', 'Simulation_Terminate', '--', '--'),
    ])


def test_a_parked_block_is_idled_by_its_downstream_sibling():
    """A block parked past the horizon is idled outright by a downstream
    sibling's failure, with no transition of its own to show for it.

    Why: `Dependence_Idle` is one of `STATE_ALREADY_APPLIED`'s reasons -- the
    dispatcher applies the state change and only then interrupts -- so the
    block is already `Idle` when its own wait resumes
    (`cascade_dependency.py:105`). The timeline shows the node going `Bad`
    and the block going `Idle` in the same instant; the *cell* is only
    visible to the observer.

    Setup: `A` parked past the horizon and sysDepending on the model's End
    node; `B` downstream of it fails at t=60 and repairs at t=560.

    Expected: `A Good -> Idle` at t=60, `Idle -> Good` at t=560, and the
    beyond-horizon wait ends with `Dependence_Idle` and then
    `Dependence_All_Up`.

    Source: raptor/core/engine/wait_sites.py `WaitSite.BEYOND_HORIZON`,
      interrupts.py `STATE_ALREADY_APPLIED`, cascade_dependency.py:105
    """
    result, log = observe(far_life_dependence_idle())
    assert_reached(log, "A", "beyond_horizon", "Dependence_Idle")
    assert_timeline(result, [
        (0.0, 'Block', 'A', '--', 'Good'),
        (0.0, 'Block', 'B', '--', 'Good'),
        (60.0, 'Block', 'B', 'Good', 'Failed'),
        (60.0, 'Block', 'B', 'Failed', 'Repairing'),
        (60.0, 'Node', 'end', '--', 'Bad'),
        (60.0, 'System', 'State_change', '--', 'Red'),
        (60.0, 'Block', 'A', 'Good', 'Idle'),
        (60.0, 'Node', 'N2', '--', 'Idle'),
        (80.0, 'Block', 'A', 'Idle', 'Done'),
        (80.0, 'Block', 'B', 'Repairing', 'Done'),
        (80.0, 'System', 'Phase_change', '--', 'Mission_failure'),
        (80.0, 'System', 'Simulation_Terminate', '--', '--'),
    ])


def test_a_parked_block_fails_when_a_dependency_induces_it():
    """A block parked past the horizon *fails* when a dependency's mixed
    reaction is `InducedFailure`.

    Why: `DepFail=100` short-circuits `draw_dependency_action` to that
    outcome with no draw (`dependency.py:95-101`), and
    `_induce_dependency` interrupts without applying any state change -- so
    the block's own `Induced_Failure` branch at the beyond-horizon wait is
    what records the component failure.

    Setup: as above, with `dep_idle=0, dep_fail=100`.

    Expected: `A` goes `Failed -> Repairing` at t=60 with no state change of
    its own beforehand, and the wait ended with `Induced_Failure`.

    Source: raptor/core/engine/wait_sites.py `WaitSite.BEYOND_HORIZON`,
      dependency.py:95-101, cascade_dependency.py:168 (`_induce_dependency`
      delivers with no transition)
    """
    result, log = observe(far_life_induced_failure())
    assert_reached(log, "A", "beyond_horizon", "Induced_Failure")
    assert_timeline(result, [
        (0.0, 'Block', 'A', '--', 'Good'),
        (0.0, 'Block', 'B', '--', 'Good'),
        (60.0, 'Block', 'B', 'Good', 'Failed'),
        (60.0, 'Block', 'B', 'Failed', 'Repairing'),
        (60.0, 'Node', 'end', '--', 'Bad'),
        (60.0, 'System', 'State_change', '--', 'Red'),
        (60.0, 'Block', 'A', 'Good', 'Failed'),
        (60.0, 'Block', 'A', 'Failed', 'Repairing'),
        (60.0, 'Node', 'N2', '--', 'Bad'),
        (70.0, 'Block', 'A', 'Repairing', 'Repaired'),
        (70.0, 'Block', 'A', 'Repaired', 'Good'),
        (70.0, 'Node', 'N2', 'Bad', 'Good'),
        (70.0, 'System', 'Red_change', '--', 'Red'),
        (80.0, 'Block', 'A', 'Good', 'Done'),
        (80.0, 'Block', 'B', 'Repairing', 'Done'),
        (80.0, 'System', 'Phase_change', '--', 'Mission_failure'),
        (80.0, 'System', 'Simulation_Terminate', '--', '--'),
    ])


def test_a_parked_block_goes_for_pm_when_a_dependency_says_obpm():
    """A block parked past the horizon goes for PM when a dependency's
    mixed reaction is `GoToPM`, and the wake is `OBPM`.

    Why: `GoToPM` interrupts with no state change either
    (`cascade_dependency.py:168`), so the reason the block's own wait reads
    is the engine's `OBPM` -- which is also the name of the source
    mechanism ("opportunistic block PM"). `uses_pm` is required for the arm
    to fire at all (`dependency.py:600`), which is what the `PMFreq` past
    `_FAR_FUTURE` in this fixture is for.

    Setup: as above, with `dep_idle=0, dep_pm=100, uses_pm=True` and
    `PMFreq=2e9`.

    Expected: `A Good -> PM_is_due` at t=60 and the wait ends with `OBPM`.

    Source: raptor/core/engine/wait_sites.py `WaitSite.BEYOND_HORIZON`,
      dependency.py:591-601, cascade_dependency.py:168
      (`_induce_dependency`), Raptor 7 (`GoToPM`)
    """
    result, log = observe(far_life_obpm())
    assert_reached(log, "A", "beyond_horizon", "OBPM")
    assert_timeline(result, [
        (0.0, 'Block', 'A', '--', 'Good'),
        (0.0, 'Block', 'B', '--', 'Good'),
        (60.0, 'Block', 'B', 'Good', 'Failed'),
        (60.0, 'Block', 'B', 'Failed', 'Repairing'),
        (60.0, 'Node', 'end', '--', 'Bad'),
        (60.0, 'System', 'State_change', '--', 'Red'),
        (80.0, 'Block', 'A', 'Good', 'Done'),
        (80.0, 'Block', 'B', 'Repairing', 'Done'),
        (80.0, 'System', 'Phase_change', '--', 'Mission_failure'),
        (80.0, 'System', 'Simulation_Terminate', '--', '--'),
    ])


def test_a_parked_block_is_backflowed_a_standby_from_its_cold_peer():
    """`Dependence_Standby` is back-flowed **from** the block that goes
    Standby, so a third block parked past the horizon is the one that
    receives it.

    Why: `BackFlowDependencies` walks the *dependents* of the block whose
    state changed (`dependency.py:305-309`), and the survey always demotes
    the block whose own link most recently came back up
    (`cold_standby.py:181-186`) -- which is never the block a third block
    depends on unless the fixture says so. That is why the `is_executing`
    guard in `_switch_interrupt` (`cascade_cold.py:270-272`) does not apply
    here: the demoted block is not the one being woken.

    Setup: a 1-of-2 cold node (`Y` holds the slot, `X` is standby) whose
    standby member `X` fails at t=40 and repairs at t=80; `A` block-depends
    on `Y`, has a `Fixed(1e9)` life, and so is parked past the horizon.

    Expected: `Y Standby` at t=80 (the demotion) and `A`'s beyond-horizon
    wait ends with `Dependence_Standby`.

    Source: raptor/core/engine/wait_sites.py `WaitSite.BEYOND_HORIZON`,
      dependency.py:305-309, cascade_cold.py:71
    """
    result, log = observe(far_life_standby_backflow())
    assert_reached(log, "A", "beyond_horizon", "Dependence_Standby")
    assert_timeline(result, [
        (0.0, 'Block', 'Y', '--', 'Good'),
        (0.0, 'Block', 'X', '--', 'Good'),
        (0.0, 'Block', 'A', '--', 'Good'),
        (0.0, 'Block', 'X', 'Good', 'Standby'),
        (0.0, 'Node', 'Cold', '--', 'Good'),
        (0.0, 'Block', 'X', 'Standby', 'StessLevel'),
        (30.0, 'Block', 'Y', 'Good', 'Failed'),
        (30.0, 'Block', 'Y', 'Failed', 'Repairing'),
        (30.0, 'Block', 'A', 'Good', 'Idle'),
        (30.0, 'Node', 'Join', '--', 'Degraded'),
        (30.0, 'Block', 'X', 'StessLevel', 'Good'),
        (30.0, 'Node', 'Cold', 'Good', 'Degraded'),
        (30.0, 'System', 'State_change', '--', 'Yellow'),
        (40.0, 'Block', 'Y', 'Repairing', 'Repaired'),
        (40.0, 'Block', 'Y', 'Repaired', 'Good'),
        (40.0, 'Block', 'A', 'Idle', 'Good'),
        (40.0, 'Node', 'Join', 'Degraded', 'Good'),
        (40.0, 'Block', 'Y', 'Good', 'Standby'),
        (40.0, 'Block', 'A', 'Good', 'Standby'),
        (40.0, 'Node', 'Join', 'Good', 'Good'),
        (40.0, 'Node', 'Cold', 'Degraded', 'Good'),
        (40.0, 'Block', 'A', 'Standby', 'StessLevel'),
        (40.0, 'Block', 'Y', 'Standby', 'StessLevel'),
        (40.0, 'System', 'State_change', 'Yellow', 'Green'),
        (55.0, 'Block', 'X', 'Good', 'Failed'),
        (55.0, 'Block', 'X', 'Failed', 'Repairing'),
        (55.0, 'Block', 'Y', 'StessLevel', 'Good'),
        (55.0, 'Block', 'A', 'StessLevel', 'Good'),
        (55.0, 'Node', 'Join', 'Good', 'Good'),
        (55.0, 'Node', 'Cold', 'Good', 'Degraded'),
        (55.0, 'System', 'State_change', 'Green', 'Yellow'),
        (77.5, 'Block', 'Y', 'Good', 'Failed'),
        (77.5, 'Block', 'Y', 'Failed', 'Repairing'),
        (77.5, 'Block', 'A', 'Good', 'Idle'),
        (77.5, 'Node', 'Join', 'Good', 'Degraded'),
        (77.5, 'Node', 'Cold', 'Degraded', 'Bad'),
        (77.5, 'Node', 'Join', 'Degraded', 'Idle'),
        (77.5, 'Node', 'end', '--', 'Idle'),
        (77.5, 'System', 'State_change', 'Yellow', 'Red'),
        (87.5, 'Block', 'Y', 'Repairing', 'Repaired'),
        (87.5, 'Block', 'Y', 'Repaired', 'Good'),
        (87.5, 'Block', 'A', 'Idle', 'Good'),
        (87.5, 'Node', 'Join', 'Idle', 'Degraded'),
        (87.5, 'Node', 'end', 'Idle', 'Good'),
        (87.5, 'Node', 'Cold', 'Bad', 'Degraded'),
        (87.5, 'Node', 'Join', 'Degraded', 'Good'),
        (87.5, 'System', 'State_change', 'Red', 'Yellow'),
        (95.0, 'Block', 'X', 'Repairing', 'Repaired'),
        (95.0, 'Block', 'X', 'Repaired', 'Good'),
        (95.0, 'Block', 'Y', 'Good', 'Standby'),
        (95.0, 'Block', 'A', 'Good', 'Standby'),
        (95.0, 'Node', 'Join', 'Good', 'Good'),
        (95.0, 'Node', 'Cold', 'Degraded', 'Good'),
        (95.0, 'Block', 'A', 'Standby', 'StessLevel'),
        (95.0, 'Block', 'Y', 'Standby', 'StessLevel'),
        (95.0, 'System', 'State_change', 'Yellow', 'Green'),
        (100.0, 'Block', 'Y', 'StessLevel', 'Done'),
        (100.0, 'Block', 'X', 'Good', 'Done'),
        (100.0, 'Block', 'A', 'StessLevel', 'Done'),
        (100.0, 'System', 'Phase_change', '--', 'Mission_failure'),
        (100.0, 'System', 'Simulation_Terminate', '--', '--'),
    ])


def test_a_cold_peer_demoted_at_kickoff_backflows_standby_to_its_dependent():
    """The survey's kickoff demotion back-flows `Dependence_Standby` into the
    dependent's kickoff barrier.

    Why: every candidate block initialises `Running` and every link starts
    `"ready"`, so with `RunsRequired = 1` and two
    ready paths the survey demotes one at t=0 -- and a demotion is a state
    change, which is a back-flow. This is the earliest a block can be woken
    at all, and it is why `kickoff x Dependence_Standby` is a matrix row.

    Setup: the same cold group, with `A` block-dependent on the peer the
    survey demotes at kickoff and a `Fixed(2e9)` life of its own.

    Expected: `X Good -> Standby` at t=0 with `Too_many_on`, and `A`'s
    kickoff barrier ends with `Dependence_Standby`.

    Source: raptor/core/engine/wait_sites.py `WaitSite.KICKOFF`,
      cold_standby.py `run_cold_logic`'s kickoff arm,
     cascade_dependency.py:71
    """
    result, log = observe(kickoff_standby_backflow())
    assert_reached(log, "A", "kickoff", "Dependence_Standby")
    assert_timeline(result, [
        (0.0, 'Block', 'Y', '--', 'Good'),
        (0.0, 'Block', 'X', '--', 'Good'),
        (0.0, 'Block', 'A', '--', 'Good'),
        (0.0, 'Block', 'X', 'Good', 'Standby'),
        (0.0, 'Block', 'A', 'Good', 'Standby'),
        (0.0, 'Node', 'Join', '--', 'Good'),
        (0.0, 'Node', 'Cold', '--', 'Good'),
        (0.0, 'Block', 'X', 'Standby', 'StessLevel'),
        (0.0, 'Block', 'A', 'Standby', 'StessLevel'),
        (100.0, 'Block', 'X', 'StessLevel', 'Failed'),
        (100.0, 'Block', 'X', 'Failed', 'Repairing'),
        (100.0, 'Block', 'A', 'StessLevel', 'Idle'),
        (100.0, 'Node', 'Join', 'Good', 'Degraded'),
        (100.0, 'Node', 'Cold', 'Good', 'Degraded'),
        (100.0, 'System', 'State_change', '--', 'Yellow'),
        (150.0, 'Block', 'X', 'Repairing', 'Repaired'),
        (150.0, 'Block', 'X', 'Repaired', 'Good'),
        (150.0, 'Block', 'A', 'Idle', 'Good'),
        (150.0, 'Node', 'Join', 'Degraded', 'Good'),
        (150.0, 'Block', 'X', 'Good', 'Standby'),
        (150.0, 'Block', 'A', 'Good', 'Standby'),
        (150.0, 'Node', 'Join', 'Good', 'Good'),
        (150.0, 'Node', 'Cold', 'Degraded', 'Good'),
        (150.0, 'Block', 'X', 'Standby', 'StessLevel'),
        (150.0, 'Block', 'A', 'Standby', 'StessLevel'),
        (150.0, 'Block', 'Y', 'Good', 'Done'),
        (150.0, 'Block', 'X', 'StessLevel', 'Done'),
        (150.0, 'Block', 'A', 'StessLevel', 'Done'),
        (150.0, 'System', 'State_change', 'Yellow', 'Green'),
        (150.0, 'System', 'Phase_change', '--', 'Mission_success'),
        (150.0, 'System', 'Simulation_Terminate', '--', '--'),
    ])


def test_a_dependent_in_its_fail_wait_hears_its_peer_go_standby():
    """A dependent at its ordinary fail-wait is woken by its cold peer
    going Standby -- the same back-flow, at a length where the block is
    waiting on its own life rather than past the horizon.

    Why: it is the same dispatch as
    `test_a_parked_block_is_backflowed_a_standby_from_its_cold_peer` with
    one number changed, and pinning it at both lengths is what makes
    `Dependence_Standby` a fact about the dispatch rather than about the
    beyond-horizon branch.

    Setup: as above with `A`'s life back to 500 h, so it fails at t=500 and
    is in its fail-wait when the demotion happens at t=80.

    Expected: `A` stays `Good` and its fail-wait ends with
    `Dependence_Standby`.

    Source: raptor/core/engine/wait_sites.py `WaitSite.FAIL_WAIT`,
      dependency.py:305-309
    """
    result, log = observe(cold_fail_wait_standby())
    assert_reached(log, "A", "fail_wait", "Dependence_Standby")
    assert_timeline(result, [
        (0.0, 'Block', 'Y', '--', 'Good'),
        (0.0, 'Block', 'X', '--', 'Good'),
        (0.0, 'Block', 'A', '--', 'Good'),
        (0.0, 'Block', 'X', 'Good', 'Standby'),
        (0.0, 'Node', 'Cold', '--', 'Good'),
        (0.0, 'Block', 'X', 'Standby', 'StessLevel'),
        (20.0, 'Block', 'Y', 'Good', 'Failed'),
        (20.0, 'Block', 'Y', 'Failed', 'Repairing'),
        (20.0, 'Block', 'A', 'Good', 'Idle'),
        (20.0, 'Node', 'Join', '--', 'Degraded'),
        (20.0, 'Block', 'X', 'StessLevel', 'Good'),
        (20.0, 'Node', 'Cold', 'Good', 'Degraded'),
        (20.0, 'System', 'State_change', '--', 'Yellow'),
        (30.0, 'Block', 'Y', 'Repairing', 'Repaired'),
        (30.0, 'Block', 'Y', 'Repaired', 'Good'),
        (30.0, 'Block', 'A', 'Idle', 'Good'),
        (30.0, 'Node', 'Join', 'Degraded', 'Good'),
        (30.0, 'Block', 'Y', 'Good', 'Standby'),
        (30.0, 'Block', 'A', 'Good', 'Standby'),
        (30.0, 'Node', 'Join', 'Good', 'Good'),
        (30.0, 'Node', 'Cold', 'Degraded', 'Good'),
        (30.0, 'Block', 'A', 'Standby', 'StessLevel'),
        (30.0, 'Block', 'Y', 'Standby', 'StessLevel'),
        (30.0, 'System', 'State_change', 'Yellow', 'Green'),
        (70.0, 'Block', 'X', 'Good', 'Failed'),
        (70.0, 'Block', 'Y', 'StessLevel', 'Failed'),
        (70.0, 'Block', 'X', 'Failed', 'Repairing'),
        (70.0, 'Block', 'Y', 'Failed', 'Good'),
        (70.0, 'Block', 'A', 'StessLevel', 'Good'),
        (70.0, 'Node', 'Join', 'Good', 'Good'),
        (70.0, 'Node', 'Cold', 'Good', 'Degraded'),
        (70.0, 'Block', 'Y', 'Good', 'Repairing'),
        (70.0, 'Block', 'A', 'Good', 'Idle'),
        (70.0, 'Node', 'Join', 'Good', 'Degraded'),
        (70.0, 'Node', 'Cold', 'Degraded', 'Bad'),
        (70.0, 'Node', 'Join', 'Degraded', 'Idle'),
        (70.0, 'Node', 'end', '--', 'Idle'),
        (70.0, 'System', 'State_change', 'Green', 'Red'),
        (80.0, 'Block', 'Y', 'Repairing', 'Repaired'),
        (80.0, 'Block', 'Y', 'Repaired', 'Good'),
        (80.0, 'Block', 'A', 'Idle', 'Good'),
        (80.0, 'Node', 'Join', 'Idle', 'Degraded'),
        (80.0, 'Node', 'end', 'Idle', 'Good'),
        (80.0, 'Node', 'Cold', 'Bad', 'Degraded'),
        (80.0, 'Node', 'Join', 'Degraded', 'Good'),
        (80.0, 'System', 'State_change', 'Red', 'Yellow'),
        (90.0, 'Block', 'X', 'Repairing', 'Repaired'),
        (90.0, 'Block', 'X', 'Repaired', 'Good'),
        (90.0, 'Block', 'Y', 'Good', 'Standby'),
        (90.0, 'Block', 'A', 'Good', 'Standby'),
        (90.0, 'Node', 'Join', 'Good', 'Good'),
        (90.0, 'Node', 'Cold', 'Degraded', 'Good'),
        (90.0, 'Block', 'A', 'Standby', 'StessLevel'),
        (90.0, 'Block', 'Y', 'Standby', 'StessLevel'),
        (90.0, 'System', 'State_change', 'Yellow', 'Green'),
        (100.0, 'Block', 'Y', 'StessLevel', 'Done'),
        (100.0, 'Block', 'X', 'Good', 'Done'),
        (100.0, 'Block', 'A', 'StessLevel', 'Done'),
        (100.0, 'System', 'Phase_change', '--', 'Mission_failure'),
        (100.0, 'System', 'Simulation_Terminate', '--', '--'),
    ])


def test_a_dependent_promoted_out_of_standby_hears_sb_turnon():
    """A dependent in its fail-wait reads its cold peer's promotion as
    `Dependence_SB_turnOn`, because the dependent is itself Standby.

    Why: `_dispatch_dependency`'s `was_standby` branch rewrites the reason
    (`cascade_dependency.py:74`) -- the promotion's `Switched_On` becomes
    `Dependence_SB_turnOn` for a dependent that was Standby, and stays
    `Switched_On` for one that was not. Both rows are in the matrix; only
    one of them is true of a given fixture.

    Setup: a 1-of-2 cold group whose standby member `X` fails at t=60 and
    repairs at t=70; `A` block-depends on `X`.

    Expected: the fail-wait ends with `Dependence_SB_turnOn`.

    Source: raptor/core/engine/wait_sites.py `WaitSite.FAIL_WAIT`,
      cascade_dependency.py:74 (`_dispatch_dependency`'s `was_standby` arm)
    """
    result, log = observe(cold_fail_wait_sb_turnon())
    assert_reached(log, "A", "fail_wait", "Dependence_SB_turnOn")
    assert_timeline(result, [
        (0.0, 'Block', 'Y', '--', 'Good'),
        (0.0, 'Block', 'X', '--', 'Good'),
        (0.0, 'Block', 'A', '--', 'Good'),
        (0.0, 'Block', 'X', 'Good', 'Standby'),
        (0.0, 'Block', 'A', 'Good', 'Standby'),
        (0.0, 'Node', 'Join', '--', 'Good'),
        (0.0, 'Node', 'Cold', '--', 'Good'),
        (0.0, 'Block', 'X', 'Standby', 'StessLevel'),
        (0.0, 'Block', 'A', 'Standby', 'StessLevel'),
        (100.0, 'Block', 'Y', 'Good', 'Failed'),
        (100.0, 'Block', 'Y', 'Failed', 'Repairing'),
        (100.0, 'Block', 'X', 'StessLevel', 'Good'),
        (100.0, 'Block', 'A', 'StessLevel', 'Good'),
        (100.0, 'Node', 'Join', 'Good', 'Good'),
        (100.0, 'Node', 'Cold', 'Good', 'Degraded'),
        (100.0, 'Block', 'Y', 'Repairing', 'Done'),
        (100.0, 'Block', 'X', 'Good', 'Done'),
        (100.0, 'Block', 'A', 'Good', 'Done'),
        (100.0, 'System', 'State_change', '--', 'Yellow'),
        (100.0, 'System', 'Phase_change', '--', 'Mission_success'),
        (100.0, 'System', 'Simulation_Terminate', '--', '--'),
    ])


def test_a_demoted_cold_peer_is_switched_off_in_its_fail_wait():
    """The demoted half of a cold group is switched off while it waits on
    its own life, and that is an ordinary fail-wait wake.

    Why: `Switched_Off`'s only sender is `run_cold_logic`'s too-many-on
    branch (`cold_standby.py:673`), and it fires at the block the survey has
    just demoted -- which is the block whose own repair-completion cascade is
    running the survey. `_switch_interrupt`'s `is_executing` guard
    (`cascade_cold.py:270-272`) does not refuse here because the guard is
    against the *currently executing* handle, and the demoted peer is
    waiting on its life, not executing.

    Setup: the same cold group as
    `test_a_parked_block_is_backflowed_a_standby_from_its_cold_peer`:
    `Y` holds the slot, `X` is standby, `X` fails at t=40 and repairs at
    t=80.

    Expected: `Y` -- the peer that is holding the slot and competing for it
    again -- is demoted at t=80 with `Too_many_on`, and its own fail-wait
    ends with `Switched_Off`.

    Source: raptor/core/engine/wait_sites.py `WaitSite.FAIL_WAIT`,
      cold_standby.py:673, cascade_cold.py:270-272
    """
    result, log = observe(far_life_standby_backflow())
    assert_reached(log, "Y", "fail_wait", "Switched_Off")
    assert_timeline(result, [
        (0.0, 'Block', 'Y', '--', 'Good'),
        (0.0, 'Block', 'X', '--', 'Good'),
        (0.0, 'Block', 'A', '--', 'Good'),
        (0.0, 'Block', 'X', 'Good', 'Standby'),
        (0.0, 'Node', 'Cold', '--', 'Good'),
        (0.0, 'Block', 'X', 'Standby', 'StessLevel'),
        (30.0, 'Block', 'Y', 'Good', 'Failed'),
        (30.0, 'Block', 'Y', 'Failed', 'Repairing'),
        (30.0, 'Block', 'A', 'Good', 'Idle'),
        (30.0, 'Node', 'Join', '--', 'Degraded'),
        (30.0, 'Block', 'X', 'StessLevel', 'Good'),
        (30.0, 'Node', 'Cold', 'Good', 'Degraded'),
        (30.0, 'System', 'State_change', '--', 'Yellow'),
        (40.0, 'Block', 'Y', 'Repairing', 'Repaired'),
        (40.0, 'Block', 'Y', 'Repaired', 'Good'),
        (40.0, 'Block', 'A', 'Idle', 'Good'),
        (40.0, 'Node', 'Join', 'Degraded', 'Good'),
        (40.0, 'Block', 'Y', 'Good', 'Standby'),
        (40.0, 'Block', 'A', 'Good', 'Standby'),
        (40.0, 'Node', 'Join', 'Good', 'Good'),
        (40.0, 'Node', 'Cold', 'Degraded', 'Good'),
        (40.0, 'Block', 'A', 'Standby', 'StessLevel'),
        (40.0, 'Block', 'Y', 'Standby', 'StessLevel'),
        (40.0, 'System', 'State_change', 'Yellow', 'Green'),
        (55.0, 'Block', 'X', 'Good', 'Failed'),
        (55.0, 'Block', 'X', 'Failed', 'Repairing'),
        (55.0, 'Block', 'Y', 'StessLevel', 'Good'),
        (55.0, 'Block', 'A', 'StessLevel', 'Good'),
        (55.0, 'Node', 'Join', 'Good', 'Good'),
        (55.0, 'Node', 'Cold', 'Good', 'Degraded'),
        (55.0, 'System', 'State_change', 'Green', 'Yellow'),
        (77.5, 'Block', 'Y', 'Good', 'Failed'),
        (77.5, 'Block', 'Y', 'Failed', 'Repairing'),
        (77.5, 'Block', 'A', 'Good', 'Idle'),
        (77.5, 'Node', 'Join', 'Good', 'Degraded'),
        (77.5, 'Node', 'Cold', 'Degraded', 'Bad'),
        (77.5, 'Node', 'Join', 'Degraded', 'Idle'),
        (77.5, 'Node', 'end', '--', 'Idle'),
        (77.5, 'System', 'State_change', 'Yellow', 'Red'),
        (87.5, 'Block', 'Y', 'Repairing', 'Repaired'),
        (87.5, 'Block', 'Y', 'Repaired', 'Good'),
        (87.5, 'Block', 'A', 'Idle', 'Good'),
        (87.5, 'Node', 'Join', 'Idle', 'Degraded'),
        (87.5, 'Node', 'end', 'Idle', 'Good'),
        (87.5, 'Node', 'Cold', 'Bad', 'Degraded'),
        (87.5, 'Node', 'Join', 'Degraded', 'Good'),
        (87.5, 'System', 'State_change', 'Red', 'Yellow'),
        (95.0, 'Block', 'X', 'Repairing', 'Repaired'),
        (95.0, 'Block', 'X', 'Repaired', 'Good'),
        (95.0, 'Block', 'Y', 'Good', 'Standby'),
        (95.0, 'Block', 'A', 'Good', 'Standby'),
        (95.0, 'Node', 'Join', 'Good', 'Good'),
        (95.0, 'Node', 'Cold', 'Degraded', 'Good'),
        (95.0, 'Block', 'A', 'Standby', 'StessLevel'),
        (95.0, 'Block', 'Y', 'Standby', 'StessLevel'),
        (95.0, 'System', 'State_change', 'Yellow', 'Green'),
        (100.0, 'Block', 'Y', 'StessLevel', 'Done'),
        (100.0, 'Block', 'X', 'Good', 'Done'),
        (100.0, 'Block', 'A', 'StessLevel', 'Done'),
        (100.0, 'System', 'Phase_change', '--', 'Mission_failure'),
        (100.0, 'System', 'Simulation_Terminate', '--', '--'),
    ])


def test_a_promoted_cold_peer_is_switched_on_in_its_fail_wait():
    """The standby half of a cold group is switched on while it waits on
    its own life.

    Why: the twin of the claim above, and the reason both rows are separate
    cells: `Switched_On` and `Switched_Off` are different senders on
    different arms of the same survey, and only one of them can reach a given
    block at a given wait.

    Setup: the same model. `X` is the standby member here, so `X` is the one
    promoted when `Y` fails at t=40.

    Expected: `X Standby -> Good` at t=40 with `Prior=Standby`, and `X`'s
    fail-wait ends with `Switched_On`.

    Source: raptor/core/engine/wait_sites.py `WaitSite.FAIL_WAIT`,
      cold_standby.py `run_cold_logic`'s promotion arm
    """
    result, log = observe(far_life_standby_backflow())
    assert_reached(log, "X", "fail_wait", "Switched_On")
    assert_timeline(result, [
        (0.0, 'Block', 'Y', '--', 'Good'),
        (0.0, 'Block', 'X', '--', 'Good'),
        (0.0, 'Block', 'A', '--', 'Good'),
        (0.0, 'Block', 'X', 'Good', 'Standby'),
        (0.0, 'Node', 'Cold', '--', 'Good'),
        (0.0, 'Block', 'X', 'Standby', 'StessLevel'),
        (30.0, 'Block', 'Y', 'Good', 'Failed'),
        (30.0, 'Block', 'Y', 'Failed', 'Repairing'),
        (30.0, 'Block', 'A', 'Good', 'Idle'),
        (30.0, 'Node', 'Join', '--', 'Degraded'),
        (30.0, 'Block', 'X', 'StessLevel', 'Good'),
        (30.0, 'Node', 'Cold', 'Good', 'Degraded'),
        (30.0, 'System', 'State_change', '--', 'Yellow'),
        (40.0, 'Block', 'Y', 'Repairing', 'Repaired'),
        (40.0, 'Block', 'Y', 'Repaired', 'Good'),
        (40.0, 'Block', 'A', 'Idle', 'Good'),
        (40.0, 'Node', 'Join', 'Degraded', 'Good'),
        (40.0, 'Block', 'Y', 'Good', 'Standby'),
        (40.0, 'Block', 'A', 'Good', 'Standby'),
        (40.0, 'Node', 'Join', 'Good', 'Good'),
        (40.0, 'Node', 'Cold', 'Degraded', 'Good'),
        (40.0, 'Block', 'A', 'Standby', 'StessLevel'),
        (40.0, 'Block', 'Y', 'Standby', 'StessLevel'),
        (40.0, 'System', 'State_change', 'Yellow', 'Green'),
        (55.0, 'Block', 'X', 'Good', 'Failed'),
        (55.0, 'Block', 'X', 'Failed', 'Repairing'),
        (55.0, 'Block', 'Y', 'StessLevel', 'Good'),
        (55.0, 'Block', 'A', 'StessLevel', 'Good'),
        (55.0, 'Node', 'Join', 'Good', 'Good'),
        (55.0, 'Node', 'Cold', 'Good', 'Degraded'),
        (55.0, 'System', 'State_change', 'Green', 'Yellow'),
        (77.5, 'Block', 'Y', 'Good', 'Failed'),
        (77.5, 'Block', 'Y', 'Failed', 'Repairing'),
        (77.5, 'Block', 'A', 'Good', 'Idle'),
        (77.5, 'Node', 'Join', 'Good', 'Degraded'),
        (77.5, 'Node', 'Cold', 'Degraded', 'Bad'),
        (77.5, 'Node', 'Join', 'Degraded', 'Idle'),
        (77.5, 'Node', 'end', '--', 'Idle'),
        (77.5, 'System', 'State_change', 'Yellow', 'Red'),
        (87.5, 'Block', 'Y', 'Repairing', 'Repaired'),
        (87.5, 'Block', 'Y', 'Repaired', 'Good'),
        (87.5, 'Block', 'A', 'Idle', 'Good'),
        (87.5, 'Node', 'Join', 'Idle', 'Degraded'),
        (87.5, 'Node', 'end', 'Idle', 'Good'),
        (87.5, 'Node', 'Cold', 'Bad', 'Degraded'),
        (87.5, 'Node', 'Join', 'Degraded', 'Good'),
        (87.5, 'System', 'State_change', 'Red', 'Yellow'),
        (95.0, 'Block', 'X', 'Repairing', 'Repaired'),
        (95.0, 'Block', 'X', 'Repaired', 'Good'),
        (95.0, 'Block', 'Y', 'Good', 'Standby'),
        (95.0, 'Block', 'A', 'Good', 'Standby'),
        (95.0, 'Node', 'Join', 'Good', 'Good'),
        (95.0, 'Node', 'Cold', 'Degraded', 'Good'),
        (95.0, 'Block', 'A', 'Standby', 'StessLevel'),
        (95.0, 'Block', 'Y', 'Standby', 'StessLevel'),
        (95.0, 'System', 'State_change', 'Yellow', 'Green'),
        (100.0, 'Block', 'Y', 'StessLevel', 'Done'),
        (100.0, 'Block', 'X', 'Good', 'Done'),
        (100.0, 'Block', 'A', 'StessLevel', 'Done'),
        (100.0, 'System', 'Phase_change', '--', 'Mission_failure'),
        (100.0, 'System', 'Simulation_Terminate', '--', '--'),
    ])


def test_an_idle_dependent_hears_its_peer_go_standby():
    """An Idle dependent is back-flowed `Dependence_Standby` by its cold
    peer, and the wake lands in the Idle wait rather than the fail-wait.

    Why: the Idle wait and the fail-wait are two different `WaitSite`s
    (`WaitSite.IDLE` and `WaitSite.FAIL_WAIT`) that a dependency dispatch
    chooses between, and which one it picks is decided by the block's own
    state at the instant, not by the reason. Pinning the reason at both
    sites is what says the dispatch is site-agnostic and the *wait* is not.

    Setup: the same cold group as the kickoff claim: `Y` holds the slot,
    `X` is demoted at t=0, and `A` block-depends on `X` with a `Fixed(2e9)`
    life of its own. `A` goes Idle at t=60 when `X` fails, so it is parked
    in the Idle wait when the survey demotes `Y` at t=100.

    Expected: `A Idle` when the demotion happens, and its Idle wait ends
    with `Dependence_Standby`.

    Source: raptor/core/engine/wait_sites.py `WaitSite.IDLE`,
      `_idle_wait`, cascade_dependency.py:71
    """
    result, log = observe(kickoff_standby_backflow())
    assert_reached(log, "A", "idle", "Dependence_Standby")
    assert_timeline(result, [
        (0.0, 'Block', 'Y', '--', 'Good'),
        (0.0, 'Block', 'X', '--', 'Good'),
        (0.0, 'Block', 'A', '--', 'Good'),
        (0.0, 'Block', 'X', 'Good', 'Standby'),
        (0.0, 'Block', 'A', 'Good', 'Standby'),
        (0.0, 'Node', 'Join', '--', 'Good'),
        (0.0, 'Node', 'Cold', '--', 'Good'),
        (0.0, 'Block', 'X', 'Standby', 'StessLevel'),
        (0.0, 'Block', 'A', 'Standby', 'StessLevel'),
        (100.0, 'Block', 'X', 'StessLevel', 'Failed'),
        (100.0, 'Block', 'X', 'Failed', 'Repairing'),
        (100.0, 'Block', 'A', 'StessLevel', 'Idle'),
        (100.0, 'Node', 'Join', 'Good', 'Degraded'),
        (100.0, 'Node', 'Cold', 'Good', 'Degraded'),
        (100.0, 'System', 'State_change', '--', 'Yellow'),
        (150.0, 'Block', 'X', 'Repairing', 'Repaired'),
        (150.0, 'Block', 'X', 'Repaired', 'Good'),
        (150.0, 'Block', 'A', 'Idle', 'Good'),
        (150.0, 'Node', 'Join', 'Degraded', 'Good'),
        (150.0, 'Block', 'X', 'Good', 'Standby'),
        (150.0, 'Block', 'A', 'Good', 'Standby'),
        (150.0, 'Node', 'Join', 'Good', 'Good'),
        (150.0, 'Node', 'Cold', 'Degraded', 'Good'),
        (150.0, 'Block', 'X', 'Standby', 'StessLevel'),
        (150.0, 'Block', 'A', 'Standby', 'StessLevel'),
        (150.0, 'Block', 'Y', 'Good', 'Done'),
        (150.0, 'Block', 'X', 'StessLevel', 'Done'),
        (150.0, 'Block', 'A', 'StessLevel', 'Done'),
        (150.0, 'System', 'State_change', 'Yellow', 'Green'),
        (150.0, 'System', 'Phase_change', '--', 'Mission_success'),
        (150.0, 'System', 'Simulation_Terminate', '--', '--'),
    ])


def test_an_idle_dependent_hears_its_peers_promotion():
    """The same promotion, read by a dependent that is Idle at the time.

    Why: `Dependence_SB_turnOn` is the rewrite of a promotion for a
    dependent that `was_standby`, and the rewrite happens before the wake is
    routed -- so the Idle wait accepts a reason it would otherwise not see.

    Setup: the same model as the claim above.

    Expected: the Idle wait ends with `Dependence_SB_turnOn`.

    Source: raptor/core/engine/wait_sites.py `WaitSite.IDLE`,
      cascade_dependency.py:74
    """
    result, log = observe(cold_idle_sb_turnon())
    assert_reached(log, "A", "idle", "Dependence_SB_turnOn")
    assert_timeline(result, [
        (0.0, 'Block', 'Y', '--', 'Good'),
        (0.0, 'Block', 'X', '--', 'Good'),
        (0.0, 'Block', 'A', '--', 'Good'),
        (0.0, 'Block', 'X', 'Good', 'Standby'),
        (0.0, 'Block', 'A', 'Good', 'Standby'),
        (0.0, 'Node', 'Join', '--', 'Good'),
        (0.0, 'Node', 'Cold', '--', 'Good'),
        (0.0, 'Block', 'X', 'Standby', 'StessLevel'),
        (0.0, 'Block', 'A', 'Standby', 'StessLevel'),
        (80.0, 'Block', 'X', 'StessLevel', 'Failed'),
        (80.0, 'Block', 'X', 'Failed', 'Repairing'),
        (80.0, 'Block', 'A', 'StessLevel', 'Idle'),
        (80.0, 'Node', 'Join', 'Good', 'Degraded'),
        (80.0, 'Node', 'Cold', 'Good', 'Degraded'),
        (80.0, 'System', 'State_change', '--', 'Yellow'),
        (100.0, 'Block', 'Y', 'Good', 'Failed'),
        (100.0, 'Block', 'X', 'Repairing', 'Repaired'),
        (100.0, 'Block', 'X', 'Repaired', 'Good'),
        (100.0, 'Block', 'A', 'Idle', 'Good'),
        (100.0, 'Node', 'Join', 'Degraded', 'Good'),
        (100.0, 'Block', 'X', 'Good', 'Standby'),
        (100.0, 'Block', 'A', 'Good', 'Standby'),
        (100.0, 'Node', 'Join', 'Good', 'Good'),
        (100.0, 'Node', 'Cold', 'Degraded', 'Good'),
        (100.0, 'Block', 'Y', 'Failed', 'Repairing'),
        (100.0, 'Block', 'X', 'Standby', 'Good'),
        (100.0, 'Block', 'A', 'Standby', 'Good'),
        (100.0, 'Node', 'Join', 'Good', 'Good'),
        (100.0, 'Node', 'Cold', 'Good', 'Degraded'),
        (100.0, 'Block', 'Y', 'Repairing', 'Done'),
        (100.0, 'Block', 'X', 'Good', 'Done'),
        (100.0, 'Block', 'A', 'Good', 'Done'),
        (100.0, 'System', 'Phase_change', '--', 'Mission_success'),
        (100.0, 'System', 'Simulation_Terminate', '--', '--'),
    ])


def test_a_zero_stress_dependent_hears_its_peers_promotion():
    """A dependent whose combined stress is zero is parked at `ZERO_STRESS`,
    and a cold peer's promotion lands there as `Dependence_SB_turnOn`.

    Why: `ZERO_STRESS` is a *different* wait from both the Idle wait and the
    fail-wait (against ), reached when
    `combined_stress` computes to zero -- here from a phase value of `0.0`.
    Its policy is "any wake re-checks the stress and redraws", so the reason
    only feeds the startup-failure draw; which is exactly why the cell needs
    a test rather than a count.

    Setup: the same cold group, with `A` phased with stress 0 until t=50.

    Expected: `A` parks at zero stress from t=0 and the promotion's wake
    lands in that wait.

    Source: raptor/core/engine/wait_sites.py `WaitSite.ZERO_STRESS`,
      block_lifecycle.py:1898-1910, cascade_dependency.py:74
    """
    result, log = observe(cold_zero_stress_sb_turnon())
    assert_reached(log, "A", "zero_stress", "Dependence_SB_turnOn")
    assert_timeline(result, [
        (0.0, 'System', 'State_change', '--', 'Mission'),
        (0.0, 'Block', 'Y', '--', 'Good'),
        (0.0, 'Block', 'X', '--', 'Good'),
        (0.0, 'Block', 'A', '--', 'Good'),
        (0.0, 'Block', 'X', 'Good', 'Standby'),
        (0.0, 'Block', 'A', 'Good', 'Standby'),
        (0.0, 'Node', 'Join', '--', 'Good'),
        (0.0, 'Node', 'Cold', '--', 'Good'),
        (0.0, 'Block', 'X', 'Standby', 'StessLevel'),
        (30.0, 'Block', 'Y', 'Good', 'Failed'),
        (30.0, 'Block', 'Y', 'Failed', 'Repairing'),
        (30.0, 'Block', 'X', 'StessLevel', 'Good'),
        (30.0, 'Block', 'A', 'Standby', 'Good'),
        (30.0, 'Node', 'Join', 'Good', 'Good'),
        (30.0, 'Node', 'Cold', 'Good', 'Degraded'),
        (30.0, 'System', 'State_change', 'Mission', 'Yellow'),
        (40.0, 'Block', 'Y', 'Repairing', 'Repaired'),
        (40.0, 'Block', 'Y', 'Repaired', 'Good'),
        (40.0, 'Block', 'Y', 'Good', 'Standby'),
        (40.0, 'Node', 'Cold', 'Degraded', 'Good'),
        (40.0, 'Block', 'Y', 'Standby', 'StessLevel'),
        (40.0, 'System', 'State_change', 'Yellow', 'Green'),
        (50.0, 'System', 'Phase_change', '--', 'Phase_change'),
        (50.0, 'System', 'Phase_change', 'Phase_change', 'Mission_success'),
        (50.0, 'System', 'State_change', 'Green', 'Green'),
        (55.0, 'Block', 'X', 'Good', 'Failed'),
        (55.0, 'Block', 'X', 'Failed', 'Repairing'),
        (55.0, 'Block', 'A', 'Good', 'Idle'),
        (55.0, 'Node', 'Join', 'Good', 'Degraded'),
        (55.0, 'Block', 'Y', 'StessLevel', 'Good'),
        (55.0, 'Node', 'Cold', 'Good', 'Degraded'),
        (55.0, 'System', 'State_change', 'Green', 'Yellow'),
        (75.0, 'Block', 'X', 'Repairing', 'Repaired'),
        (75.0, 'Block', 'X', 'Repaired', 'Good'),
        (75.0, 'Block', 'A', 'Idle', 'Good'),
        (75.0, 'Node', 'Join', 'Degraded', 'Good'),
        (75.0, 'Block', 'X', 'Good', 'Standby'),
        (75.0, 'Block', 'A', 'Good', 'Standby'),
        (75.0, 'Node', 'Join', 'Good', 'Good'),
        (75.0, 'Node', 'Cold', 'Degraded', 'Good'),
        (75.0, 'Block', 'X', 'Standby', 'StessLevel'),
        (75.0, 'System', 'State_change', 'Yellow', 'Green'),
        (77.5, 'Block', 'Y', 'Good', 'Failed'),
        (77.5, 'Block', 'Y', 'Failed', 'Repairing'),
        (77.5, 'Block', 'X', 'StessLevel', 'Good'),
        (77.5, 'Block', 'A', 'Standby', 'Good'),
        (77.5, 'Node', 'Join', 'Good', 'Good'),
        (77.5, 'Node', 'Cold', 'Good', 'Degraded'),
        (77.5, 'System', 'State_change', 'Green', 'Yellow'),
        (80.0, 'Block', 'Y', 'Repairing', 'Done'),
        (80.0, 'Block', 'X', 'Good', 'Done'),
        (80.0, 'Block', 'A', 'Good', 'Done'),
        (80.0, 'System', 'Simulation_Terminate', '--', '--'),
    ])


def test_a_zero_stress_block_is_woken_by_the_phase_boundary():
    """A block whose combined stress is zero is woken by the phase boundary
    that raises its stress back above zero.

    Why: the boundary's `ChangePhases` tail interrupts *whichever* wait the
    block is suspended in, not a chosen one (`cascade_phase.py:158`), and
    the block re-checks its stress at the top of the loop. A phase value of
    `0.0` is the only lever that reaches `CombinedStress <= 0.0` for a
    Running block, and it fails silently if the two phases carry the same
    value (`cascade_phase.py:91`).

    Setup: one block, phase 1 stress `0.0`, phase 2 stress `1.0`, boundary at
    t=50.

    Expected: the block is `Good` and silent until t=50, then
    `Good -> Done` at t=300 with the wake recorded at `zero_stress`.

    Source: raptor/core/engine/wait_sites.py `WaitSite.ZERO_STRESS`,
      cascade_phase.py:158, block_lifecycle.py:1898
    """
    result, log = observe(zero_stress_phase_change())
    assert_reached(log, "A", "zero_stress", "Phase_Change")
    assert_timeline(result, [
        (0.0, 'System', 'State_change', '--', 'Mission'),
        (0.0, 'Block', 'A', '--', 'Good'),
        (50.0, 'System', 'Phase_change', '--', 'Phase_change'),
        (50.0, 'System', 'Phase_change', 'Phase_change', 'Mission_success'),
        (50.0, 'System', 'State_change', 'Mission', 'Green'),
        (60.0, 'Block', 'A', 'Good', 'Done'),
        (60.0, 'System', 'Simulation_Terminate', '--', '--'),
    ])


def test_a_zero_stress_block_is_idled_by_its_downstream_sibling():
    """A zero-stress block is idled outright by a downstream sibling's
    failure, and its zero-stress wait ends with `Dependence_Idle`.

    Why: the twin of
    `test_a_parked_block_is_idled_by_its_downstream_sibling`, at the other
    of the two waits a `Dependence_Idle` can land in. The sibling fails at
    t=30, before the t=50 boundary, so the block is still at zero stress when
    the dispatch runs -- which is the only thing that makes this a
    `zero_stress` cell rather than a `fail_wait` one.

    Setup: `A` phased with stress 0 until t=50, sysDepending on the model's
    End node; `B` fails at t=30.

    Expected: the zero-stress wait ends with `Dependence_Idle` at t=30.

    Source: raptor/core/engine/wait_sites.py `WaitSite.ZERO_STRESS`,
      cascade_dependency.py:105, block_lifecycle.py:1898-1910
    """
    result, log = observe(zero_stress_dependence_idle())
    assert_reached(log, "A", "zero_stress", "Dependence_Idle")
    assert_timeline(result, [
        (0.0, 'System', 'State_change', '--', 'Mission'),
        (0.0, 'Block', 'A', '--', 'Good'),
        (0.0, 'Block', 'B', '--', 'Good'),
        (30.0, 'Block', 'B', 'Good', 'Failed'),
        (30.0, 'Block', 'B', 'Failed', 'Repairing'),
        (30.0, 'Node', 'end', '--', 'Bad'),
        (30.0, 'System', 'State_change', 'Mission', 'Red'),
        (30.0, 'Block', 'A', 'Good', 'Idle'),
        (30.0, 'Node', 'N2', '--', 'Idle'),
        (50.0, 'Block', 'B', 'Repairing', 'Repaired'),
        (50.0, 'Block', 'B', 'Repaired', 'Good'),
        (50.0, 'Node', 'end', 'Bad', 'Idle'),
        (50.0, 'Block', 'A', 'Idle', 'Good'),
        (50.0, 'Node', 'N2', 'Idle', 'Good'),
        (50.0, 'Node', 'end', 'Idle', 'Good'),
        (50.0, 'System', 'Phase_change', '--', 'Phase_change'),
        (50.0, 'System', 'Phase_change', 'Phase_change', 'Mission_failure'),
        (50.0, 'System', 'State_change', 'Red', 'Red'),
        (50.0, 'System', 'State_change', 'Red', 'Green'),
        (60.0, 'Block', 'A', 'Good', 'Done'),
        (60.0, 'Block', 'B', 'Good', 'Done'),
        (60.0, 'System', 'Simulation_Terminate', '--', '--'),
    ])


def test_a_zero_stress_block_fails_when_a_dependency_induces_it():
    """A zero-stress block fails when a dependency's mixed reaction is
    `InducedFailure` -- the ZeroWait site's own failure branch.

    Why: source's ZeroWait site ends its reason chain with
    `ELSIF (InterruptReason="Induced_Failure")`,
    and an earlier reading of this module called that branch absent. It is
    not: a zero-stress standby block fails when a dependency induces it.

    Setup: as above, with `dep_idle=0, dep_fail=100` and `B` failing at
    t=25, before the t=50 boundary.

    Expected: `A Good -> Failed` at t=25 and the zero-stress wait ends
    with `Induced_Failure`.

    Source: raptor/core/engine/wait_sites.py `WaitSite.ZERO_STRESS` and
      `IDLE_WAKE_REASONS`,
      block_lifecycle.py:1920-1928
    """
    result, log = observe(zero_stress_induced_failure())
    assert_reached(log, "A", "zero_stress", "Induced_Failure")
    assert_timeline(result, [
        (0.0, 'System', 'State_change', '--', 'Mission'),
        (0.0, 'Block', 'A', '--', 'Good'),
        (0.0, 'Block', 'B', '--', 'Good'),
        (25.0, 'Block', 'B', 'Good', 'Failed'),
        (25.0, 'Block', 'B', 'Failed', 'Repairing'),
        (25.0, 'Node', 'end', '--', 'Bad'),
        (25.0, 'System', 'State_change', 'Mission', 'Red'),
        (25.0, 'Block', 'A', 'Good', 'Failed'),
        (25.0, 'Block', 'A', 'Failed', 'Repairing'),
        (25.0, 'Node', 'N2', '--', 'Bad'),
        (35.0, 'Block', 'A', 'Repairing', 'Repaired'),
        (35.0, 'Block', 'A', 'Repaired', 'Good'),
        (35.0, 'Node', 'N2', 'Bad', 'Good'),
        (35.0, 'System', 'Red_change', '--', 'Red'),
        (45.0, 'Block', 'B', 'Repairing', 'Repaired'),
        (45.0, 'Block', 'B', 'Repaired', 'Good'),
        (45.0, 'Node', 'end', 'Bad', 'Good'),
        (45.0, 'System', 'State_change', 'Red', 'Green'),
        (50.0, 'System', 'Phase_change', '--', 'Phase_change'),
        (50.0, 'System', 'Phase_change', 'Phase_change', 'Mission_failure'),
        (50.0, 'System', 'State_change', 'Green', 'Green'),
        (70.0, 'Block', 'B', 'Good', 'Failed'),
        (70.0, 'Block', 'B', 'Failed', 'Repairing'),
        (70.0, 'Node', 'end', 'Good', 'Bad'),
        (70.0, 'System', 'State_change', 'Green', 'Red'),
        (70.0, 'Block', 'A', 'Good', 'Failed'),
        (70.0, 'Block', 'A', 'Failed', 'Repairing'),
        (70.0, 'Node', 'N2', 'Good', 'Bad'),
        (80.0, 'Block', 'A', 'Repairing', 'Repaired'),
        (80.0, 'Block', 'A', 'Repaired', 'Good'),
        (80.0, 'Node', 'N2', 'Bad', 'Good'),
        (80.0, 'Block', 'A', 'Good', 'Done'),
        (80.0, 'Block', 'B', 'Repairing', 'Done'),
        (80.0, 'System', 'Simulation_Terminate', '--', '--'),
    ])


def test_a_zero_stress_block_takes_obpm_from_a_dependency():
    """A zero-stress block takes `OBPM` from a dependency's `GoToPM`
    reaction, and routes it to its own PM arm.

    Why: the ZeroWait site has its own OBPM branch
   whose two halves are over-threshold (go to
    PM) and under-threshold (benign re-check), and which arm runs is a
    function of `OperatingTime` against `DepPMThreshold * failMean / 100`.

    Setup: as above, with `dep_idle=0, dep_pm=100, uses_pm=True`.

    Expected: the zero-stress wait ends with `OBPM` at t=25, and `A` is
    `PM_is_due` at t=50 rather than t=25 -- the over-threshold branch.

    Source: raptor/core/engine/wait_sites.py `WaitSite.ZERO_STRESS`,
     block_lifecycle.py:1935-1942,
      `_obpm_over_threshold`
    """
    result, log = observe(zero_stress_obpm())
    assert_reached(log, "A", "zero_stress", "OBPM")
    assert_timeline(result, [
        (0.0, 'System', 'State_change', '--', 'Mission'),
        (0.0, 'Block', 'A', '--', 'Good'),
        (0.0, 'Block', 'B', '--', 'Good'),
        (25.0, 'Block', 'B', 'Good', 'Failed'),
        (25.0, 'Block', 'B', 'Failed', 'Repairing'),
        (25.0, 'Node', 'end', '--', 'Bad'),
        (25.0, 'System', 'State_change', 'Mission', 'Red'),
        (45.0, 'Block', 'B', 'Repairing', 'Repaired'),
        (45.0, 'Block', 'B', 'Repaired', 'Good'),
        (45.0, 'Node', 'end', 'Bad', 'Good'),
        (45.0, 'System', 'State_change', 'Red', 'Green'),
        (50.0, 'System', 'Phase_change', '--', 'Phase_change'),
        (50.0, 'System', 'Phase_change', 'Phase_change', 'Mission_failure'),
        (50.0, 'System', 'State_change', 'Green', 'Green'),
        (70.0, 'Block', 'B', 'Good', 'Failed'),
        (70.0, 'Block', 'B', 'Failed', 'Repairing'),
        (70.0, 'Node', 'end', 'Good', 'Bad'),
        (70.0, 'System', 'State_change', 'Green', 'Red'),
        (90.0, 'Block', 'B', 'Repairing', 'Repaired'),
        (90.0, 'Block', 'B', 'Repaired', 'Good'),
        (90.0, 'Node', 'end', 'Bad', 'Good'),
        (90.0, 'System', 'State_change', 'Red', 'Green'),
        (100.0, 'Block', 'A', 'Good', 'PM_is_due'),
        (100.0, 'Block', 'A', 'PM_is_due', 'PM'),
        (100.0, 'Node', 'N2', '--', 'NodePM'),
        (100.0, 'Node', 'end', 'Good', 'NodePM'),
        (100.0, 'System', 'State_change', 'Green', 'Red'),
        (100.0, 'Block', 'A', 'PM', 'Done'),
        (100.0, 'Block', 'B', 'Good', 'Done'),
        (100.0, 'System', 'Simulation_Terminate', '--', '--'),
    ])


def test_a_dependent_fails_in_its_fail_wait_when_induced():
    """A block waiting on its own life *fails* when a dependency's mixed
    reaction is `InducedFailure`.

    Why: the fail-wait's own `Induced_Failure` branch records the component
    failure with no state change of its own first
    (`block_lifecycle.py:2061-2067`), which is why the timeline's only
    evidence is `Good -> Failed` at an instant nothing else happens.

    Setup: `A` sysDepends on the model's End node with `dep_idle=0,
    dep_fail=100`; `B` fails at t=40.

    Expected: `A Good -> Failed` at t=40 and its fail-wait ends with
    `Induced_Failure`.

    Source: raptor/core/engine/wait_sites.py `WaitSite.FAIL_WAIT`,
      dependency.py:95-101, block_lifecycle.py:2061-2067
    """
    result, log = observe(fail_wait_induced_failure())
    assert_reached(log, "A", "fail_wait", "Induced_Failure")
    assert_timeline(result, [
        (0.0, 'Block', 'A', '--', 'Good'),
        (0.0, 'Block', 'B', '--', 'Good'),
        (40.0, 'Block', 'B', 'Good', 'Failed'),
        (40.0, 'Block', 'B', 'Failed', 'Repairing'),
        (40.0, 'Node', 'end', '--', 'Bad'),
        (40.0, 'System', 'State_change', '--', 'Red'),
        (40.0, 'Block', 'A', 'Good', 'Failed'),
        (40.0, 'Block', 'A', 'Failed', 'Repairing'),
        (40.0, 'Node', 'N2', '--', 'Bad'),
        (50.0, 'Block', 'A', 'Repairing', 'Repaired'),
        (50.0, 'Block', 'A', 'Repaired', 'Good'),
        (50.0, 'Node', 'N2', 'Bad', 'Good'),
        (50.0, 'System', 'Red_change', '--', 'Red'),
        (60.0, 'Block', 'B', 'Repairing', 'Repaired'),
        (60.0, 'Block', 'B', 'Repaired', 'Good'),
        (60.0, 'Node', 'end', 'Bad', 'Good'),
        (60.0, 'Block', 'A', 'Good', 'Done'),
        (60.0, 'Block', 'B', 'Good', 'Done'),
        (60.0, 'System', 'State_change', 'Red', 'Green'),
        (60.0, 'System', 'Phase_change', '--', 'Mission_failure'),
        (60.0, 'System', 'Simulation_Terminate', '--', '--'),
    ])


def test_a_dependent_takes_obpm_in_its_fail_wait():
    """A block waiting on its own life goes for PM when a dependency's mixed
    reaction is `GoToPM`.

    Why: the fail-wait's OBPM branch is the same over/under-threshold pair
    as the ZeroWait site's, and over threshold it hands the block to
    `DetermineNewState("PM is due")` from inside the fail-wait -- so the
    reason the wait records is `OBPM` and nothing else.

    Setup: as above, with `dep_idle=0, dep_pm=100, uses_pm=True`.

    Expected: `A Good -> PM_is_due` at t=40 and its fail-wait ends with
    `OBPM`.

    Source: raptor/core/engine/wait_sites.py `WaitSite.FAIL_WAIT`,
      dependency.py:591-601, `_on_fail_wait_wake`
    """
    result, log = observe(fail_wait_obpm())
    assert_reached(log, "A", "fail_wait", "OBPM")
    assert_timeline(result, [
        (0.0, 'Block', 'A', '--', 'Good'),
        (0.0, 'Block', 'B', '--', 'Good'),
        (40.0, 'Block', 'B', 'Good', 'Failed'),
        (40.0, 'Block', 'B', 'Failed', 'Repairing'),
        (40.0, 'Node', 'end', '--', 'Bad'),
        (40.0, 'System', 'State_change', '--', 'Red'),
        (50.0, 'Block', 'A', 'Good', 'PM_is_due'),
        (50.0, 'Block', 'A', 'PM_is_due', 'PM'),
        (50.0, 'Node', 'N2', '--', 'NodePM'),
        (50.0, 'System', 'Red_change', '--', 'Red'),
        (55.0, 'Block', 'A', 'PM', 'PM_complete'),
        (55.0, 'Block', 'A', 'PM_complete', 'Good'),
        (55.0, 'Node', 'N2', 'NodePM', 'Good'),
        (55.0, 'System', 'Red_change', 'Red', 'Red'),
        (60.0, 'Block', 'B', 'Repairing', 'Repaired'),
        (60.0, 'Block', 'B', 'Repaired', 'Good'),
        (60.0, 'Node', 'end', 'Bad', 'Good'),
        (60.0, 'Block', 'A', 'Good', 'Done'),
        (60.0, 'Block', 'B', 'Good', 'Done'),
        (60.0, 'System', 'State_change', 'Red', 'Green'),
        (60.0, 'System', 'Phase_change', '--', 'Mission_failure'),
        (60.0, 'System', 'Simulation_Terminate', '--', '--'),
    ])


def test_a_phase_boundary_lands_in_the_fail_wait_of_a_deferred_pm():
    """A phase boundary interrupts the fail-wait of a block whose PM it is
    about to release, and the wake is `Phase_Change`.

    Why: source's fail-wait `ON INTERRUPT` chain has no `Phase_Change` arm
    at all -- the reason reaches the block because the phase-change body's
    `ChangePhases` tail interrupts whichever wait it is in
    (`cascade_phase.py:158`), not because the fail-wait asked for it.

    Setup: one `pmMisDefer` block, a 200-hour mission phase then a 400-hour
    non-mission one, boundary at t=200.

    Expected: the fail-wait ends with `Phase_Change` at t=200, and the
    block is `PM_is_due` again at t=200.

    Source: raptor/core/engine/wait_sites.py `WaitSite.FAIL_WAIT`,
      cascade_phase.py:158
    """
    result, log = observe(misdefer_release_at_fail_wait())
    assert_reached(log, "A", "fail_wait", "Phase_Change")
    assert_timeline(result, [
        (0.0, 'System', 'State_change', '--', 'Mission'),
        (0.0, 'Block', 'A', '--', 'Good'),
        (0.0, 'Block', 'A', 'Good', 'StessLevel'),
        (50.0, 'Block', 'A', 'StessLevel', 'PM_is_due'),
        (50.0, 'Block', 'A', 'PM_is_due', 'StessLevel'),
        (200.0, 'System', 'Phase_change', '--', 'Phase_change'),
        (200.0, 'System', 'Phase_change', 'Phase_change', 'Mission_success'),
        (200.0, 'System', 'State_change', 'Mission', 'Green'),
        (200.0, 'Block', 'A', 'StessLevel', 'PM_is_due'),
        (200.0, 'Block', 'A', 'PM_is_due', 'PM'),
        (200.0, 'Node', 'end', '--', 'NodePM'),
        (200.0, 'System', 'State_change', 'Green', 'Red'),
        (205.0, 'Block', 'A', 'PM', 'PM_complete'),
        (205.0, 'Block', 'A', 'PM_complete', 'Good'),
        (205.0, 'Node', 'end', 'NodePM', 'Good'),
        (205.0, 'System', 'State_change', 'Red', 'Green'),
        (255.0, 'Block', 'A', 'Good', 'PM_is_due'),
        (255.0, 'Block', 'A', 'PM_is_due', 'PM'),
        (255.0, 'Node', 'end', 'Good', 'NodePM'),
        (255.0, 'System', 'State_change', 'Green', 'Red'),
        (260.0, 'Block', 'A', 'PM', 'PM_complete'),
        (260.0, 'Block', 'A', 'PM_complete', 'Good'),
        (260.0, 'Node', 'end', 'NodePM', 'Good'),
        (260.0, 'System', 'State_change', 'Red', 'Green'),
        (310.0, 'Block', 'A', 'Good', 'PM_is_due'),
        (310.0, 'Block', 'A', 'PM_is_due', 'PM'),
        (310.0, 'Node', 'end', 'Good', 'NodePM'),
        (310.0, 'System', 'State_change', 'Green', 'Red'),
        (315.0, 'Block', 'A', 'PM', 'PM_complete'),
        (315.0, 'Block', 'A', 'PM_complete', 'Good'),
        (315.0, 'Node', 'end', 'NodePM', 'Good'),
        (315.0, 'System', 'State_change', 'Red', 'Green'),
        (365.0, 'Block', 'A', 'Good', 'PM_is_due'),
        (365.0, 'Block', 'A', 'PM_is_due', 'PM'),
        (365.0, 'Node', 'end', 'Good', 'NodePM'),
        (365.0, 'System', 'State_change', 'Green', 'Red'),
        (370.0, 'Block', 'A', 'PM', 'PM_complete'),
        (370.0, 'Block', 'A', 'PM_complete', 'Good'),
        (370.0, 'Node', 'end', 'NodePM', 'Good'),
        (370.0, 'System', 'State_change', 'Red', 'Green'),
        (400.0, 'Block', 'A', 'Good', 'Done'),
        (400.0, 'System', 'Simulation_Terminate', '--', '--'),
    ])


def test_a_misdefer_pm_is_released_at_its_fail_wait():
    """The phase change that releases a deferred PM is delivered to the
    block as `Start_Deferred_PM`, and at an ordinary-length life it lands in
    the fail-wait.

    Why: the twin of
    `test_a_parked_block_is_released_by_its_mission_phase_ending` with a
    500-hour life instead of a `Fixed(2e9)` one, so the release lands in the
    ordinary fail-wait. `WAKE_POLICY` permits this reason at both sites, and
    which one a block gets depends only on its life -- which is the claim.

    Setup: one `pmMisDefer` block, 500-hour life, the same two phases.

    Expected: the fail-wait ends with `Start_Deferred_PM` at t=200.

    Source: raptor/core/engine/wait_sites.py `WaitSite.FAIL_WAIT`,
      cascade_phase.py:295
    """
    result, log = observe(misdefer_release_at_fail_wait())
    assert_reached(log, "A", "fail_wait", "Start_Deferred_PM")
    assert_timeline(result, [
        (0.0, 'System', 'State_change', '--', 'Mission'),
        (0.0, 'Block', 'A', '--', 'Good'),
        (0.0, 'Block', 'A', 'Good', 'StessLevel'),
        (50.0, 'Block', 'A', 'StessLevel', 'PM_is_due'),
        (50.0, 'Block', 'A', 'PM_is_due', 'StessLevel'),
        (200.0, 'System', 'Phase_change', '--', 'Phase_change'),
        (200.0, 'System', 'Phase_change', 'Phase_change', 'Mission_success'),
        (200.0, 'System', 'State_change', 'Mission', 'Green'),
        (200.0, 'Block', 'A', 'StessLevel', 'PM_is_due'),
        (200.0, 'Block', 'A', 'PM_is_due', 'PM'),
        (200.0, 'Node', 'end', '--', 'NodePM'),
        (200.0, 'System', 'State_change', 'Green', 'Red'),
        (205.0, 'Block', 'A', 'PM', 'PM_complete'),
        (205.0, 'Block', 'A', 'PM_complete', 'Good'),
        (205.0, 'Node', 'end', 'NodePM', 'Good'),
        (205.0, 'System', 'State_change', 'Red', 'Green'),
        (255.0, 'Block', 'A', 'Good', 'PM_is_due'),
        (255.0, 'Block', 'A', 'PM_is_due', 'PM'),
        (255.0, 'Node', 'end', 'Good', 'NodePM'),
        (255.0, 'System', 'State_change', 'Green', 'Red'),
        (260.0, 'Block', 'A', 'PM', 'PM_complete'),
        (260.0, 'Block', 'A', 'PM_complete', 'Good'),
        (260.0, 'Node', 'end', 'NodePM', 'Good'),
        (260.0, 'System', 'State_change', 'Red', 'Green'),
        (310.0, 'Block', 'A', 'Good', 'PM_is_due'),
        (310.0, 'Block', 'A', 'PM_is_due', 'PM'),
        (310.0, 'Node', 'end', 'Good', 'NodePM'),
        (310.0, 'System', 'State_change', 'Green', 'Red'),
        (315.0, 'Block', 'A', 'PM', 'PM_complete'),
        (315.0, 'Block', 'A', 'PM_complete', 'Good'),
        (315.0, 'Node', 'end', 'NodePM', 'Good'),
        (315.0, 'System', 'State_change', 'Red', 'Green'),
        (365.0, 'Block', 'A', 'Good', 'PM_is_due'),
        (365.0, 'Block', 'A', 'PM_is_due', 'PM'),
        (365.0, 'Node', 'end', 'Good', 'NodePM'),
        (365.0, 'System', 'State_change', 'Green', 'Red'),
        (370.0, 'Block', 'A', 'PM', 'PM_complete'),
        (370.0, 'Block', 'A', 'PM_complete', 'Good'),
        (370.0, 'Node', 'end', 'NodePM', 'Good'),
        (370.0, 'System', 'State_change', 'Red', 'Green'),
        (400.0, 'Block', 'A', 'Good', 'Done'),
        (400.0, 'System', 'Simulation_Terminate', '--', '--'),
    ])


def test_a_dependency_dispatch_lands_in_the_misdefer_else_run_hop():
    """`Dependence_Idle` reaches the `ELSE Run` hop, because that hop
    spawns a fresh `Run` record.

    Why: `ELSE Run` is a nested re-entrant call, so
    for the duration of that round-trip the block is in neither `FailWait`
    nor `ZeroWait` -- and source's own `IF FailWait ... ELSIF ZeroWait ...
    END IF` wake gate has nothing to match, which is why the dependency
    reasons reach this site at all.

    Setup: `A` is a `pmMisDefer` block sysDepending on the End node whose PM
    comes due at t=50, inside the mission phase; `B` fails at exactly t=50.

    Expected: the `pm_misdefer_run` hop's wake is `Dependence_Idle`.

    Source: raptor/core/engine/wait_sites.py `WaitSite.PM_MISDEFER_RUN`,
     cascade_dependency.py:120-126
    """
    result, log = observe(misdefer_run_dependence_idle())
    assert_reached(log, "A", "pm_misdefer_run", "Dependence_Idle")
    assert_timeline(result, [
        (0.0, 'System', 'State_change', '--', 'Mission'),
        (0.0, 'Block', 'A', '--', 'Good'),
        (0.0, 'Block', 'B', '--', 'Good'),
        (50.0, 'Block', 'B', 'Good', 'Failed'),
        (50.0, 'Block', 'A', 'Good', 'PM_is_due'),
        (50.0, 'Block', 'B', 'Failed', 'Repairing'),
        (50.0, 'Node', 'end', '--', 'Bad'),
        (50.0, 'System', 'State_change', 'Mission', 'Red'),
        (50.0, 'Block', 'A', 'PM_is_due', 'Idle'),
        (50.0, 'Node', 'N2', '--', 'Idle'),
        (60.0, 'Block', 'A', 'Idle', 'Done'),
        (60.0, 'Block', 'B', 'Repairing', 'Done'),
        (60.0, 'System', 'Phase_change', '--', 'Mission_failure'),
        (60.0, 'System', 'Simulation_Terminate', '--', '--'),
    ])


def test_an_induced_failure_lands_in_the_misdefer_else_run_hop():
    """An induced failure reaches the `ELSE Run` hop, for the same reason a
    dependency dispatch does.

    Why: `_induce_dependency` interrupts without applying a state change, so
    it has no `FailWait`/`ZeroWait` flag to check either -- and the hop is
    the one place in the PM path where neither flag is set.

    Setup: as above, with `dep_idle=0, dep_fail=100` and `B` failing at
    t=40.

    Expected: the `pm_misdefer_run` hop's wake is `Induced_Failure`, and the
    block fails on the far side of it.

    Source: raptor/core/engine/wait_sites.py `WaitSite.PM_MISDEFER_RUN`,
      cascade_dependency.py:168 (`_induce_dependency` delivers with no
      transition)
    """
    result, log = observe(misdefer_run_induced_failure())
    assert_reached(log, "A", "pm_misdefer_run", "Induced_Failure")
    assert_timeline(result, [
        (0.0, 'System', 'State_change', '--', 'Mission'),
        (0.0, 'Block', 'A', '--', 'Good'),
        (0.0, 'Block', 'B', '--', 'Good'),
        (40.0, 'Block', 'B', 'Good', 'Failed'),
        (40.0, 'Block', 'B', 'Failed', 'Repairing'),
        (40.0, 'Node', 'end', '--', 'Bad'),
        (40.0, 'System', 'State_change', 'Mission', 'Red'),
        (40.0, 'Block', 'A', 'Good', 'Failed'),
        (40.0, 'Block', 'A', 'Failed', 'Repairing'),
        (40.0, 'Node', 'N2', '--', 'Bad'),
        (50.0, 'Block', 'A', 'Repairing', 'Repaired'),
        (50.0, 'Block', 'A', 'Repaired', 'Good'),
        (50.0, 'Node', 'N2', 'Bad', 'Good'),
        (50.0, 'System', 'Red_change', '--', 'Red'),
        (60.0, 'Block', 'B', 'Repairing', 'Repaired'),
        (60.0, 'Block', 'B', 'Repaired', 'Good'),
        (60.0, 'Node', 'end', 'Bad', 'Good'),
        (60.0, 'System', 'State_change', 'Red', 'Green'),
        (100.0, 'Block', 'B', 'Good', 'Failed'),
        (100.0, 'Block', 'A', 'Good', 'PM_is_due'),
        (100.0, 'Block', 'B', 'Failed', 'Repairing'),
        (100.0, 'Node', 'end', 'Good', 'Bad'),
        (100.0, 'System', 'State_change', 'Green', 'Red'),
        (120.0, 'Block', 'B', 'Repairing', 'Repaired'),
        (120.0, 'Block', 'B', 'Repaired', 'Good'),
        (120.0, 'Node', 'end', 'Bad', 'Good'),
        (120.0, 'Block', 'A', 'PM_is_due', 'Done'),
        (120.0, 'Block', 'B', 'Good', 'Done'),
        (120.0, 'System', 'State_change', 'Red', 'Green'),
        (120.0, 'System', 'Phase_change', '--', 'Mission_failure'),
        (120.0, 'System', 'Simulation_Terminate', '--', '--'),
    ])


def test_a_misdefer_pm_parking_on_the_sync_trigger_at_its_handback():
    """A `pmMisDefer` PM borrowing a resource, with a phase change in
    progress at the hand-back, parks on `pmSyncTrigger` -- the wait the
    coverage page annotated `_nothing reaches it_`.

    Why: `_obtain_pm_resource` hands the resource back to `Run` and then
    parks on the sync trigger when a phase change is in progress
    (`block_lifecycle.py:2696`), and the phase change's own end is what
    releases it with `Trigger_Fired` (`kickoff.py:710`). The page's
    annotation said no model reached the site; this one does, and the
    annotation is what a test is for.

    Setup: one `pmMisDefer` block borrowing a one-unit `R` pool, phases of
    50 h (mission) then 400 h, boundary at t=50.

    Expected: the block parks at the sync wait at t=50 and resumes at
    t=55 when the phase change's own clock entry retires.

    Source: raptor/core/engine/wait_sites.py `WaitSite.PM_SYNC_WAIT_HANDBACK`,
     block_lifecycle.py:2696, kickoff.py:710
    """
    result, log = observe(pm_sync_handback())
    assert_reached(log, "A", "pm_sync_wait_handback", "Trigger_Fired")
    assert_timeline(result, [
        (0.0, 'System', 'State_change', '--', 'Mission'),
        (0.0, 'Block', 'A', '--', 'Good'),
        (0.0, 'Block', 'A', 'Good', 'StessLevel'),
        (50.0, 'System', 'Phase_change', '--', 'Phase_change'),
        (50.0, 'System', 'Phase_change', 'Phase_change', 'Mission_success'),
        (50.0, 'System', 'State_change', 'Mission', 'Green'),
        (50.0, 'Block', 'A', 'StessLevel', 'PM_is_due'),
        (50.0, 'Block', 'A', 'PM_is_due', 'PM'),
        (50.0, 'Node', 'end', '--', 'NodePM'),
        (50.0, 'System', 'State_change', 'Green', 'Red'),
        (55.0, 'Block', 'A', 'PM', 'PM_complete'),
        (55.0, 'Block', 'A', 'PM_complete', 'Good'),
        (55.0, 'Node', 'end', 'NodePM', 'Good'),
        (55.0, 'System', 'State_change', 'Red', 'Green'),
        (60.0, 'Block', 'A', 'Good', 'Done'),
        (60.0, 'System', 'Simulation_Terminate', '--', '--'),
    ])


def test_a_misdefer_pm_pays_the_misdefer_tail_hop_at_the_handback():
    """The `pmMisDefer` block at the hand-back also pays the tail's own
    `WAIT DURATION 0.0`, which runs its full length.

    Why: the tail is between the hand-back and the sync wait and belongs to
    the misDefer arm, so it is a site of its own
    in the matrix even though it is a zero-length wait.

    Setup: the same model as the claim above.

    Expected: the hop is taken at t=50, by timer.

    Source: raptor/core/engine/wait_sites.py `WaitSite.PM_MISDEFER_HOP`
    """
    result, log = observe(pm_sync_handback())
    assert_reached(log, "A", "pm_misdefer_hop", TIMER)
    assert_timeline(result, [
        (0.0, 'System', 'State_change', '--', 'Mission'),
        (0.0, 'Block', 'A', '--', 'Good'),
        (0.0, 'Block', 'A', 'Good', 'StessLevel'),
        (50.0, 'System', 'Phase_change', '--', 'Phase_change'),
        (50.0, 'System', 'Phase_change', 'Phase_change', 'Mission_success'),
        (50.0, 'System', 'State_change', 'Mission', 'Green'),
        (50.0, 'Block', 'A', 'StessLevel', 'PM_is_due'),
        (50.0, 'Block', 'A', 'PM_is_due', 'PM'),
        (50.0, 'Node', 'end', '--', 'NodePM'),
        (50.0, 'System', 'State_change', 'Green', 'Red'),
        (55.0, 'Block', 'A', 'PM', 'PM_complete'),
        (55.0, 'Block', 'A', 'PM_complete', 'Good'),
        (55.0, 'Node', 'end', 'NodePM', 'Good'),
        (55.0, 'System', 'State_change', 'Red', 'Green'),
        (60.0, 'Block', 'A', 'Good', 'Done'),
        (60.0, 'System', 'Simulation_Terminate', '--', '--'),
    ])


def test_a_scheduled_pm_parks_on_the_sync_trigger():
    """A `pmMisDefer` block whose *scheduled* PM comes due inside a phase
    change parks on `pmSyncTrigger` and is released with `Trigger_Fired`.

    Why: source's scheduled-PM arm pays `WAIT DURATION 0.0` and then, when
    `PhaseChangeInProgress`, waits on the sync trigger;
   the phase change's end releases it. The
    matrix row for this site records the *real* reason, so it is the
    `Trigger_Fired` arm that is the cell.

    Setup: one `pmMisDefer` block, a 50-hour mission phase then 400 hours.

    Expected: the block parks at the sync wait at t=50 and the PM runs.

    Source: raptor/core/engine/wait_sites.py `WaitSite.PM_SYNC_WAIT`,
     kickoff.py:710
    """
    result, log = observe(pm_sync_wait_run())
    assert_reached(log, "A", "pm_sync_wait", "Trigger_Fired")
    assert_timeline(result, [
        (0.0, 'System', 'State_change', '--', 'Mission'),
        (0.0, 'Block', 'A', '--', 'Good'),
        (50.0, 'System', 'Phase_change', '--', 'Phase_change'),
        (50.0, 'System', 'Phase_change', 'Phase_change', 'Mission_success'),
        (50.0, 'System', 'State_change', 'Mission', 'Green'),
        (50.0, 'Block', 'A', 'Good', 'PM_is_due'),
        (50.0, 'Block', 'A', 'PM_is_due', 'PM'),
        (50.0, 'Node', 'end', '--', 'NodePM'),
        (50.0, 'System', 'State_change', 'Green', 'Red'),
        (55.0, 'Block', 'A', 'PM', 'PM_complete'),
        (55.0, 'Block', 'A', 'PM_complete', 'Good'),
        (55.0, 'Node', 'end', 'NodePM', 'Good'),
        (55.0, 'System', 'State_change', 'Red', 'Green'),
        (60.0, 'Block', 'A', 'Good', 'Done'),
        (60.0, 'System', 'Simulation_Terminate', '--', '--'),
    ])


def test_a_misdefer_tail_parks_on_the_sync_trigger():
    """The sync wait *after* the `pmMisDefer` tail is a different site, and
    the same phase change releases it.

    Why: there are three sync waits in `ObtainPMprereqs` -- after the
    scheduled-PM hop, after the hand-back hop, and after the tail -- and they
    are three rows of the matrix because the port pays them in three
    different places.
    A test that pinned only one would not say which one it meant.

    Setup: as above, but with a *different* stress in each phase, so the tail
    is the arm taken rather than the scheduled-PM hop.

    Expected: the block parks at the tail's sync wait at t=50.

    Source: raptor/core/engine/wait_sites.py `WaitSite.PM_SYNC_WAIT_TAIL`,
     kickoff.py:710
    """
    result, log = observe(pm_sync_wait_tail())
    assert_reached(log, "A", "pm_sync_wait_tail", "Trigger_Fired")
    assert_timeline(result, [
        (0.0, 'System', 'State_change', '--', 'Mission'),
        (0.0, 'Block', 'A', '--', 'Good'),
        (50.0, 'System', 'Phase_change', '--', 'Phase_change'),
        (50.0, 'System', 'Phase_change', 'Phase_change', 'Mission_success'),
        (50.0, 'System', 'State_change', 'Mission', 'Green'),
        (50.0, 'Block', 'A', 'Good', 'PM_is_due'),
        (50.0, 'Block', 'A', 'PM_is_due', 'PM'),
        (50.0, 'Node', 'end', '--', 'NodePM'),
        (50.0, 'System', 'State_change', 'Green', 'Red'),
        (55.0, 'Block', 'A', 'PM', 'PM_complete'),
        (55.0, 'Block', 'A', 'PM_complete', 'Good'),
        (55.0, 'Node', 'end', 'NodePM', 'Good'),
        (55.0, 'Block', 'A', 'Good', 'StessLevel'),
        (55.0, 'System', 'State_change', 'Red', 'Green'),
        (60.0, 'Block', 'A', 'StessLevel', 'Done'),
        (60.0, 'System', 'Simulation_Terminate', '--', '--'),
    ])


def test_a_phase_boundary_lands_in_the_perform_pm_tell_hop():
    """A phase boundary at exactly the PM-due instant lands in
    `_perform_pm`'s own `TELL PerformPM` hop.

    Why: `apply_block` runs five zero-length hops after its own clock entry
    (`cascade_phase.apply_block_or_event_group`), which is enough to get
    past the block's `pm_prereqs_tell` hop and into the `perform_pm_tell`
    one. Neither neighbour is a `Phase_Change` site in the policy -- only
    this one is -- which is why the sweep over the phase boundary against the
    PM-due instant lands here and not there.

    Setup: one block on a 50-hour PM, two phases of 50 h then 400 h with
    *different* stress values (equal values mean no `apply_block` at all,
    `cascade_phase.py:91`).

    Expected: the phase change at t=50 ends the `perform_pm_tell` hop.

    Source: raptor/core/engine/wait_sites.py `WaitSite.PERFORM_PM_TELL`,
     block_lifecycle.py:2999
    """
    result, log = observe(pm_phase_change_at_perform_tell())
    assert_reached(log, "A", "perform_pm_tell", "Phase_Change")
    assert_timeline(result, [
        (0.0, 'System', 'State_change', '--', 'Mission'),
        (0.0, 'Block', 'A', '--', 'Good'),
        (0.0, 'Block', 'A', 'Good', 'StessLevel'),
        (50.0, 'Block', 'A', 'StessLevel', 'PM_is_due'),
        (50.0, 'System', 'Phase_change', '--', 'Phase_change'),
        (50.0, 'System', 'Phase_change', 'Phase_change', 'Mission_success'),
        (50.0, 'System', 'State_change', 'Mission', 'Green'),
        (50.0, 'Block', 'A', 'PM_is_due', 'PM'),
        (50.0, 'Node', 'end', '--', 'NodePM'),
        (50.0, 'System', 'State_change', 'Green', 'Red'),
        (55.0, 'Block', 'A', 'PM', 'PM_complete'),
        (55.0, 'Block', 'A', 'PM_complete', 'Good'),
        (55.0, 'Node', 'end', 'NodePM', 'Good'),
        (55.0, 'System', 'State_change', 'Red', 'Green'),
        (80.0, 'Block', 'A', 'Good', 'Done'),
        (80.0, 'System', 'Simulation_Terminate', '--', '--'),
    ])


def test_a_phase_boundary_lands_in_the_pm_handback_hop():
    """The same boundary on the resource-pool path lands in the hand-back
    hop, which is then the last one paid before `PerformPM`.

    Why: adding a resource step inserts the hand-back between the prereq
    TELL and `PerformPM`, so the same five-hop offset lands one hop later.
    The two rows are separate cells for that reason, and a fixture with
    `num_res=1` is the only way to reach the second.

    Setup: as above, plus `num_res=1`/`#Res1pmRep=1` on a one-unit `R` pool.

    Expected: the phase change at t=50 ends the `pm_handback` hop.

    Source: raptor/core/engine/wait_sites.py `WaitSite.PM_HANDBACK`,
     block_lifecycle.py:2685
    """
    result, log = observe(pm_phase_change_at_handback())
    assert_reached(log, "A", "pm_handback", "Phase_Change")
    assert_timeline(result, [
        (0.0, 'System', 'State_change', '--', 'Mission'),
        (0.0, 'Block', 'A', '--', 'Good'),
        (0.0, 'Block', 'A', 'Good', 'StessLevel'),
        (50.0, 'Block', 'A', 'StessLevel', 'PM_is_due'),
        (50.0, 'System', 'Phase_change', '--', 'Phase_change'),
        (50.0, 'System', 'Phase_change', 'Phase_change', 'Mission_success'),
        (50.0, 'System', 'State_change', 'Mission', 'Green'),
        (50.0, 'Block', 'A', 'PM_is_due', 'PM'),
        (50.0, 'Node', 'end', '--', 'NodePM'),
        (50.0, 'System', 'State_change', 'Green', 'Red'),
        (55.0, 'Block', 'A', 'PM', 'PM_complete'),
        (55.0, 'Block', 'A', 'PM_complete', 'Good'),
        (55.0, 'Node', 'end', 'NodePM', 'Good'),
        (55.0, 'System', 'State_change', 'Red', 'Green'),
        (60.0, 'Block', 'A', 'Good', 'Done'),
        (60.0, 'System', 'Simulation_Terminate', '--', '--'),
    ])


def test_a_pm_waits_in_pmhold_for_a_custom_spare():
    """A PM whose private Custom queue is empty holds in `PMhold` until a
    restocked spare lands, and the grant wakes it.

    Why: `ObtainPMprereqs`' Custom branch has the same two arms as the
    resource branch -- `pmReqDefer` abandons the PM attempt, and without it
    the block holds (against ) -- so
    `pmReqDefer` has to be off for this site to exist at all. `A`'s first PM
    takes the only spare, `B`'s repair takes the restocked one, and `A`'s
    second PM finds the queue empty.

    Setup: two Custom-sparing blocks, `A` PMing every 25 h with
    `stock=1`/`rso=1` and a 40-hour restock; `B` failing at t=45.

    Expected: `A PM_hold` at t=50 and granted the spare at t=80, with the
    PM running 80-85.

    Source: raptor/core/engine/wait_sites.py `WaitSite.PM_HOLD_CUSTOM_SPARE`,
     block_lifecycle.py:2612-2619
    """
    result, log = observe(pm_wait_custom_spare())
    assert_reached(log, "A", "pm_hold_custom_spare", "Pool_Available")
    assert_timeline(result, [
        (0.0, 'Block', 'A', '--', 'Good'),
        (0.0, 'Block', 'B', '--', 'Good'),
        (25.0, 'Block', 'A', 'Good', 'PM_is_due'),
        (25.0, 'Block', 'A', 'PM_is_due', 'PM'),
        (25.0, 'Node', 'N2', '--', 'NodePM'),
        (25.0, 'Node', 'end', '--', 'NodePM'),
        (25.0, 'System', 'State_change', '--', 'Red'),
        (30.0, 'Block', 'A', 'PM', 'PM_complete'),
        (30.0, 'Block', 'A', 'PM_complete', 'Good'),
        (30.0, 'Node', 'N2', 'NodePM', 'Good'),
        (30.0, 'Node', 'end', 'NodePM', 'Good'),
        (30.0, 'System', 'State_change', 'Red', 'Green'),
        (40.0, 'Block', 'B', 'Good', 'Sparing'),
        (40.0, 'Block', 'A', 'Good', 'Sparing'),
        (45.0, 'Block', 'B', 'Sparing', 'Failed'),
        (45.0, 'Block', 'B', 'Failed', 'Repairing'),
        (45.0, 'Node', 'end', 'Good', 'Bad'),
        (45.0, 'System', 'State_change', 'Green', 'Red'),
        (55.0, 'Block', 'A', 'Sparing', 'PM_is_due'),
        (55.0, 'Block', 'B', 'Repairing', 'Repaired'),
        (55.0, 'Block', 'B', 'Repaired', 'Good'),
        (55.0, 'Node', 'end', 'Bad', 'Good'),
        (55.0, 'Block', 'A', 'PM_is_due', 'PM'),
        (55.0, 'Node', 'N2', 'Good', 'NodePM'),
        (55.0, 'Node', 'end', 'Good', 'NodePM'),
        (60.0, 'Block', 'A', 'PM', 'PM_complete'),
        (60.0, 'Block', 'A', 'PM_complete', 'Good'),
        (60.0, 'Node', 'N2', 'NodePM', 'Good'),
        (60.0, 'Node', 'end', 'NodePM', 'Good'),
        (60.0, 'System', 'State_change', 'Red', 'Green'),
        (80.0, 'Block', 'B', 'Good', 'Sparing'),
        (80.0, 'Block', 'A', 'Good', 'Sparing'),
        (85.0, 'Block', 'A', 'Sparing', 'PM_is_due'),
        (85.0, 'Block', 'A', 'PM_is_due', 'PM'),
        (85.0, 'Node', 'N2', 'Good', 'NodePM'),
        (85.0, 'Node', 'end', 'Good', 'NodePM'),
        (85.0, 'System', 'State_change', 'Green', 'Red'),
        (90.0, 'Block', 'A', 'PM', 'PM_complete'),
        (90.0, 'Block', 'A', 'PM_complete', 'Good'),
        (90.0, 'Node', 'N2', 'NodePM', 'Good'),
        (90.0, 'Node', 'end', 'NodePM', 'Good'),
        (90.0, 'System', 'State_change', 'Red', 'Green'),
        (100.0, 'Block', 'B', 'Sparing', 'Failed'),
        (100.0, 'Block', 'B', 'Failed', 'Repairing'),
        (100.0, 'Node', 'end', 'Good', 'Bad'),
        (100.0, 'System', 'State_change', 'Green', 'Red'),
        (110.0, 'Block', 'B', 'Repairing', 'Repaired'),
        (110.0, 'Block', 'B', 'Repaired', 'Good'),
        (110.0, 'Node', 'end', 'Bad', 'Good'),
        (110.0, 'System', 'State_change', 'Red', 'Green'),
        (115.0, 'Block', 'A', 'Good', 'PM_is_due'),
        (115.0, 'Block', 'A', 'PM_is_due', 'pmHold'),
        (115.0, 'Node', 'N2', 'Good', 'NodePM'),
        (115.0, 'Node', 'end', 'Good', 'NodePM'),
        (115.0, 'System', 'State_change', 'Green', 'Red'),
        (115.0, 'Block', 'A', 'pmHold', 'Sparing'),
        (120.0, 'Block', 'B', 'Good', 'Sparing'),
        (120.0, 'Block', 'A', 'Sparing', 'Sparing'),
        (120.0, 'Block', 'A', 'Sparing', 'Sparing'),
        (120.0, 'Block', 'A', 'Sparing', 'PM'),
        (120.0, 'Block', 'A', 'PM', 'Done'),
        (120.0, 'Block', 'B', 'Sparing', 'Done'),
        (120.0, 'System', 'Phase_change', '--', 'Mission_failure'),
        (120.0, 'System', 'Simulation_Terminate', '--', '--'),
    ])


def test_a_pm_waits_in_pmhold_for_a_pooled_spare():
    """A PM whose shared spare pool is empty holds in `PMhold` until the
    pool's next arrival, and the `Give` wakes it.

    Why: the SparePool branch of `ObtainPMprereqs` is the pool twin of the
    Custom branch above, and `rso=1` is what stops it taking the
    `sparesDone -> skipPM` exit -- without a
    routine order the block would skip the PM rather than wait for it.

    Setup: two Pooled-sparing blocks and a one-spare pool with a 40-hour
    restock; `A` PMs every 25 h, `B` fails at t=45.

    Expected: `A PM_hold` at t=50 and granted the spare at t=80.

    Source: raptor/core/engine/wait_sites.py `WaitSite.PM_HOLD_POOL_SPARE`,
     block_lifecycle.py:2490-2497
    """
    result, log = observe(pm_wait_pool_spare())
    assert_reached(log, "A", "pm_hold_pool_spare", "Pool_Available")
    assert_timeline(result, [
        (0.0, 'Block', 'A', '--', 'Good'),
        (0.0, 'Block', 'B', '--', 'Good'),
        (25.0, 'Block', 'A', 'Good', 'PM_is_due'),
        (25.0, 'Block', 'A', 'PM_is_due', 'PM'),
        (25.0, 'Node', 'N2', '--', 'NodePM'),
        (25.0, 'Node', 'end', '--', 'NodePM'),
        (25.0, 'System', 'State_change', '--', 'Red'),
        (30.0, 'Block', 'A', 'PM', 'PM_complete'),
        (30.0, 'Block', 'A', 'PM_complete', 'Good'),
        (30.0, 'Node', 'N2', 'NodePM', 'Good'),
        (30.0, 'Node', 'end', 'NodePM', 'Good'),
        (30.0, 'System', 'State_change', 'Red', 'Green'),
        (40.0, 'Pool', 'SP', '--', 'Sparing'),
        (45.0, 'Block', 'B', 'Good', 'Failed'),
        (45.0, 'Block', 'B', 'Failed', 'Repairing'),
        (45.0, 'Node', 'end', 'Good', 'Bad'),
        (45.0, 'System', 'State_change', 'Green', 'Red'),
        (55.0, 'Block', 'A', 'Good', 'PM_is_due'),
        (55.0, 'Block', 'B', 'Repairing', 'Repaired'),
        (55.0, 'Block', 'B', 'Repaired', 'Good'),
        (55.0, 'Node', 'end', 'Bad', 'Good'),
        (55.0, 'Block', 'A', 'PM_is_due', 'pmHold'),
        (55.0, 'Node', 'N2', 'Good', 'NodePM'),
        (55.0, 'Node', 'end', 'Good', 'NodePM'),
        (80.0, 'Pool', 'SP', 'Sparing', 'Sparing'),
        (80.0, 'Block', 'A', 'pmHold', 'Sparing'),
        (80.0, 'Block', 'A', 'Sparing', 'PM'),
        (80.0, 'Block', 'A', 'PM', 'Done'),
        (80.0, 'Block', 'B', 'Good', 'Done'),
        (80.0, 'System', 'Phase_change', '--', 'Mission_failure'),
        (80.0, 'System', 'Simulation_Terminate', '--', '--'),
    ])


def test_a_pm_waits_in_pmhold_for_its_resource():
    """A PM that loses the race for a one-unit resource pool holds in
    `PMhold` until the repair holding it hands it back.

    Why: source queues PM resource requests behind repair requests,
   and `B`'s repair holds `R` from t=20 to
    t=30 while `A`'s PM comes due at t=25 -- so the PM has to wait, and the
    hand-back's `Give` is what wakes it.

    Setup: `A` PMing every 25 h with `num_res=1`/`#Res1pmRep=1`; `B`
    failing at t=20 and repairing until t=30; one-unit `R` pool.

    Expected: `A PM_hold` at t=25, granted at t=30, PM running 30-35.

    Source: raptor/core/engine/wait_sites.py `WaitSite.PM_HOLD_RESOURCE`,
     block_lifecycle.py:2945-2950
    """
    result, log = observe(pm_wait_resource())
    assert_reached(log, "A", "pm_hold_resource", "Pool_Available")
    assert_timeline(result, [
        (0.0, 'Block', 'A', '--', 'Good'),
        (0.0, 'Block', 'B', '--', 'Good'),
        (20.0, 'Block', 'B', 'Good', 'Failed'),
        (20.0, 'Block', 'B', 'Failed', 'Repairing'),
        (20.0, 'Node', 'end', '--', 'Bad'),
        (20.0, 'System', 'State_change', '--', 'Red'),
        (25.0, 'Block', 'A', 'Good', 'PM_is_due'),
        (25.0, 'Block', 'A', 'PM_is_due', 'pmHold'),
        (25.0, 'Node', 'N2', '--', 'NodePM'),
        (25.0, 'System', 'Red_change', '--', 'Red'),
        (30.0, 'Block', 'B', 'Repairing', 'Repaired'),
        (30.0, 'Block', 'B', 'Repaired', 'Good'),
        (30.0, 'Node', 'end', 'Bad', 'NodePM'),
        (30.0, 'Block', 'A', 'pmHold', 'Resources'),
        (30.0, 'Block', 'A', 'Resources', 'PM'),
        (30.0, 'System', 'Red_change', 'Red', 'Red'),
        (35.0, 'Block', 'A', 'PM', 'PM_complete'),
        (35.0, 'Block', 'A', 'PM_complete', 'Good'),
        (35.0, 'Node', 'N2', 'NodePM', 'Good'),
        (35.0, 'Node', 'end', 'NodePM', 'Good'),
        (35.0, 'System', 'State_change', 'Red', 'Green'),
        (40.0, 'Block', 'A', 'Good', 'Done'),
        (40.0, 'Block', 'B', 'Good', 'Done'),
        (40.0, 'System', 'Phase_change', '--', 'Mission_failure'),
        (40.0, 'System', 'Simulation_Terminate', '--', '--'),
    ])


def test_a_repair_waits_for_a_custom_spare():
    """A repair whose private Custom queue is empty waits on `CustomSpares`
    until a restocked spare arrives.

    Why: `Repair`'s Custom arm waits on the block's own queue rather than
    holding, which is what separates it from
    `PMhold` even though both end at a `Pool_Available` wake.

    Setup: two Custom-sparing blocks, one spare each and a 40-hour restock;
    `A` fails at t=20 and `B` at t=30.

    Expected: `A RepHold` at t=20 and `B` taking the restocked spare at
    t=40, with `A`'s wait ending on that `Give`.

    Source: raptor/core/engine/wait_sites.py `WaitSite.REPAIR_CUSTOM_SPARE`,
     block_lifecycle.py:3296
    """
    result, log = observe(repair_wait_custom_spare())
    assert_reached(log, "A", "repair_custom_spare", "Pool_Available")
    assert_timeline(result, [
        (0.0, 'Block', 'A', '--', 'Good'),
        (0.0, 'Block', 'B', '--', 'Good'),
        (20.0, 'Block', 'A', 'Good', 'Failed'),
        (20.0, 'Block', 'A', 'Failed', 'Repairing'),
        (20.0, 'Node', 'N2', '--', 'Bad'),
        (20.0, 'Node', 'end', '--', 'Bad'),
        (20.0, 'System', 'State_change', '--', 'Red'),
        (30.0, 'Block', 'B', 'Good', 'Failed'),
        (30.0, 'Block', 'A', 'Repairing', 'Repaired'),
        (30.0, 'Block', 'A', 'Repaired', 'Good'),
        (30.0, 'Node', 'N2', 'Bad', 'Good'),
        (30.0, 'Node', 'end', 'Bad', 'Good'),
        (30.0, 'Block', 'B', 'Failed', 'Repairing'),
        (30.0, 'Node', 'end', 'Good', 'Bad'),
        (40.0, 'Block', 'B', 'Repairing', 'Sparing'),
        (40.0, 'Block', 'A', 'Good', 'Sparing'),
        (40.0, 'Block', 'B', 'Sparing', 'Repaired'),
        (40.0, 'Block', 'B', 'Repaired', 'Good'),
        (40.0, 'Node', 'end', 'Bad', 'Good'),
        (40.0, 'System', 'State_change', 'Red', 'Green'),
        (50.0, 'Block', 'A', 'Sparing', 'Failed'),
        (50.0, 'Block', 'A', 'Failed', 'Repairing'),
        (50.0, 'Node', 'N2', 'Good', 'Bad'),
        (50.0, 'Node', 'end', 'Good', 'Bad'),
        (50.0, 'System', 'State_change', 'Green', 'Red'),
        (60.0, 'Block', 'A', 'Repairing', 'Repaired'),
        (60.0, 'Block', 'A', 'Repaired', 'Good'),
        (60.0, 'Node', 'N2', 'Bad', 'Good'),
        (60.0, 'Node', 'end', 'Bad', 'Good'),
        (60.0, 'System', 'State_change', 'Red', 'Green'),
        (70.0, 'Block', 'B', 'Good', 'Failed'),
        (70.0, 'Block', 'B', 'Failed', 'Repairing'),
        (70.0, 'Node', 'end', 'Good', 'Bad'),
        (70.0, 'System', 'State_change', 'Green', 'Red'),
        (80.0, 'Block', 'A', 'Good', 'Sparing'),
        (80.0, 'Block', 'B', 'Repairing', 'Sparing'),
        (80.0, 'Block', 'A', 'Sparing', 'Failed'),
        (80.0, 'Block', 'B', 'Sparing', 'Repaired'),
        (80.0, 'Block', 'B', 'Repaired', 'Good'),
        (80.0, 'Node', 'end', 'Bad', 'Good'),
        (80.0, 'Block', 'A', 'Failed', 'Repairing'),
        (80.0, 'Node', 'N2', 'Good', 'Bad'),
        (80.0, 'Node', 'end', 'Good', 'Bad'),
        (90.0, 'Block', 'A', 'Repairing', 'Repaired'),
        (90.0, 'Block', 'A', 'Repaired', 'Good'),
        (90.0, 'Node', 'N2', 'Bad', 'Good'),
        (90.0, 'Node', 'end', 'Bad', 'Good'),
        (90.0, 'System', 'State_change', 'Red', 'Green'),
        (110.0, 'Block', 'B', 'Good', 'Failed'),
        (110.0, 'Block', 'A', 'Good', 'Failed'),
        (110.0, 'Block', 'A', 'Failed', 'Sparing'),
        (110.0, 'Block', 'B', 'Failed', 'Repairing'),
        (110.0, 'Node', 'end', 'Good', 'Bad'),
        (110.0, 'System', 'State_change', 'Green', 'Red'),
        (110.0, 'Block', 'A', 'Sparing', 'Sparing'),
        (110.0, 'Block', 'A', 'Sparing', 'RepHold'),
        (110.0, 'Node', 'N2', 'Good', 'Bad'),
        (120.0, 'Block', 'B', 'Repairing', 'Sparing'),
        (120.0, 'Block', 'A', 'RepHold', 'Sparing'),
        (120.0, 'Block', 'A', 'Sparing', 'Sparing'),
        (120.0, 'Block', 'A', 'Sparing', 'Repairing'),
        (120.0, 'Block', 'B', 'Sparing', 'Repaired'),
        (120.0, 'Block', 'B', 'Repaired', 'Good'),
        (120.0, 'Block', 'A', 'Repairing', 'Done'),
        (120.0, 'Block', 'B', 'Good', 'Done'),
        (120.0, 'System', 'Phase_change', '--', 'Mission_failure'),
        (120.0, 'System', 'Simulation_Terminate', '--', '--'),
    ])


def test_an_idle_block_is_woken_by_the_phase_boundary():
    """A dependency-idled block's Idle wait is ended by a phase boundary,
    and the reason it records is `Phase_Change`.

    Why: source's Idle/ZeroWait site has a `Phase_Change` arm that is a pure
    re-check signal, because a block already Idle for
    dependence reasons can be phase-cut when its phase changes -- and the
    port delivers it the same way as everywhere else, by the boundary's
    `ChangePhases` tail interrupting whichever wait the block is in
    (`cascade_phase.py:158`). The two values differ, or there is no
    `apply_block` call at all (`cascade_phase.py:91`).

    Setup: `A` sysDepends on the End node with `dep_idle=100`; `B` fails at
    t=30 and stays failed; two phases with different stress values and a
    boundary at t=50.

    Expected: `A Idle` at t=30 and its Idle wait ends with `Phase_Change` at
    t=50.

    Source: raptor/core/engine/wait_sites.py `WaitSite.IDLE`
      (the `ELSIF InterruptReason="Phase_Change"`
      arm), cascade_phase.py:158
    """
    result, log = observe(idle_phase_change())
    assert_reached(log, "A", "idle", "Phase_Change")
    assert_timeline(result, [
        (0.0, 'System', 'State_change', '--', 'Mission'),
        (0.0, 'Block', 'A', '--', 'Good'),
        (0.0, 'Block', 'B', '--', 'Good'),
        (30.0, 'Block', 'B', 'Good', 'Failed'),
        (30.0, 'Block', 'B', 'Failed', 'Repairing'),
        (30.0, 'Node', 'end', '--', 'Bad'),
        (30.0, 'System', 'State_change', 'Mission', 'Red'),
        (30.0, 'Block', 'A', 'Good', 'Idle'),
        (30.0, 'Node', 'N2', '--', 'Idle'),
        (50.0, 'System', 'Phase_change', '--', 'Phase_change'),
        (50.0, 'System', 'Phase_change', 'Phase_change', 'Mission_failure'),
        (50.0, 'System', 'State_change', 'Red', 'Red'),
        (100.0, 'Block', 'A', 'Idle', 'Done'),
        (100.0, 'Block', 'B', 'Repairing', 'Done'),
        (100.0, 'System', 'Simulation_Terminate', '--', '--'),
    ])


def test_an_idle_triggered_block_is_released_by_its_pm_trigger():
    """A dependency-idled `pmTriggered` block's Idle wait is ended by its
    PM trigger, and the block goes straight to PM.

    Why: source's ZeroWait site sends `Triggered_PM_Due` to
    `DetermineNewState("PM is due")` without touching `FailTimeLeft` or
    redrawing -- which is exactly what makes an
    *Idle* block eligible for it, and why the Idle wait's docstring calls
    that arm out by name. `ControlPMtriggers`' eligibility test admits a
    block that is not Running.

    Setup: `A` sysDepends on the End node with `dep_idle=100` and
    `pm_triggered=True`; `B` fails at t=30; the trigger fires at t=40.

    Expected: `A Idle` at t=30, `PM_is_due` at t=40, and the Idle wait ends
    with `Triggered_PM_Due`.

    Source: raptor/core/engine/wait_sites.py `WaitSite.IDLE` and
      `IDLE_WAKE_REASONS`, block_lifecycle.py:1644
    """
    result, log = observe(idle_triggered_pm())
    assert_reached(log, "A", "idle", "Triggered_PM_Due")
    assert_timeline(result, [
        (0.0, 'Block', 'A', '--', 'Good'),
        (0.0, 'Block', 'B', '--', 'Good'),
        (30.0, 'Block', 'B', 'Good', 'Failed'),
        (30.0, 'Block', 'B', 'Failed', 'Repairing'),
        (30.0, 'Node', 'end', '--', 'Bad'),
        (30.0, 'System', 'State_change', '--', 'Red'),
        (30.0, 'Block', 'A', 'Good', 'Idle'),
        (30.0, 'Node', 'N2', '--', 'Idle'),
        (40.0, 'Trigger', 'T1', '--', 'Fired'),
        (40.0, 'Block', 'A', 'Idle', 'PM_is_due'),
        (40.0, 'Block', 'A', 'PM_is_due', 'PM'),
        (40.0, 'Node', 'N2', 'Idle', 'NodePM'),
        (40.0, 'System', 'Red_change', '--', 'Red'),
        (45.0, 'Block', 'A', 'PM', 'PM_complete'),
        (45.0, 'Block', 'A', 'PM_complete', 'Good'),
        (45.0, 'Node', 'N2', 'NodePM', 'Good'),
        (45.0, 'System', 'Red_change', 'Red', 'Red'),
        (100.0, 'Block', 'A', 'Good', 'Done'),
        (100.0, 'Block', 'B', 'Repairing', 'Done'),
        (100.0, 'System', 'Phase_change', '--', 'Mission_failure'),
        (100.0, 'System', 'Simulation_Terminate', '--', '--'),
    ])


def test_a_zero_stress_block_is_released_by_its_pm_trigger():
    """A zero-stress `pmTriggered` block's zero-stress wait is ended by its
    PM trigger -- and so is the ZeroWait site's `Triggered_PM_Due` arm.

    Why: the ZeroWait site is the same `WAIT` statement as the Idle wait, one
    combined guard, and its own
    `ELSIF (InterruptReason="Triggered_PM_Due")` arm routes to PM for the same
    reason the Idle wait's does. A phase value of `0.0` is what keeps the
    block at that site rather than in the fail-wait, so the trigger has to
    fire before the t=50 boundary.

    Setup: one `pmTriggered` block, phase 1 stress `0.0`, trigger at t=25.

    Expected: `A Good -> PM_is_due` at t=25 and the zero-stress wait ends
    with `Triggered_PM_Due`.

    Source: raptor/core/engine/wait_sites.py `WaitSite.ZERO_STRESS` and
      `IDLE_WAKE_REASONS`, block_lifecycle.py:1929
    """
    result, log = observe(zero_stress_triggered_pm())
    assert_reached(log, "A", "zero_stress", "Triggered_PM_Due")
    assert_timeline(result, [
        (0.0, 'System', 'State_change', '--', 'Mission'),
        (0.0, 'Block', 'A', '--', 'Good'),
        (25.0, 'Trigger', 'T1', '--', 'Fired'),
        (25.0, 'Block', 'A', 'Good', 'PM_is_due'),
        (25.0, 'Block', 'A', 'PM_is_due', 'PM'),
        (25.0, 'Node', 'end', '--', 'NodePM'),
        (25.0, 'System', 'State_change', 'Mission', 'Red'),
        (30.0, 'Block', 'A', 'PM', 'PM_complete'),
        (30.0, 'Block', 'A', 'PM_complete', 'Good'),
        (30.0, 'Node', 'end', 'NodePM', 'Good'),
        (30.0, 'System', 'State_change', 'Red', 'Green'),
        (50.0, 'System', 'Phase_change', '--', 'Phase_change'),
        (50.0, 'System', 'Phase_change', 'Phase_change', 'Mission_failure'),
        (50.0, 'System', 'State_change', 'Green', 'Green'),
        (150.0, 'Block', 'A', 'Good', 'Done'),
        (150.0, 'System', 'Simulation_Terminate', '--', '--'),
    ])


def test_a_stale_pm_grant_lands_in_the_fail_wait_and_not_the_repair_wait():
    """The repair twin of the parked-grant claim is NOT yet pinned.

    Why: `_repair_wait` carries a dedicated absorb loop for exactly this
    wake (`block_lifecycle.py:3626-3660`) -- it resumes on a
    `Pool_Available`, reconciles the stale grant and continues the repair --
    so the site is written to receive it. What this module cannot build is
    the *precondition*: a block whose abandoned PM resource request is still
    outstanding at the instant it starts repairing. In the fixture the grant
    lands at `fail_wait` instead, which is the sibling cell and the one the
    claim above pins. `state_reachability.toml` records this as
    `repair x Pool_Available`, `reachable`, with the probe that missed.

    Setup: as the claim above.

    Expected: the grant lands at `fail_wait`, NOT at `repair` -- which is the
    measurement, and is why the row is still `reachable` rather than
    `already_pinned`.

    Source: raptor/core/engine/wait_sites.py `WaitSite.REPAIR`,
     block_lifecycle.py:3636
    """
    result, log = observe(stale_grant_during_repair())
    assert not reached(log, "repair", "Pool_Available", "A"), (
        "the stale PM grant now reaches A's repair wait -- the cell is "
        "constructible after all, so promote it rather than asserting its "
        "absence")
    assert_reached(log, "A", "fail_wait", "Pool_Available")
    assert_timeline(result, [
        (0.0, 'Block', 'A', '--', 'Good'),
        (0.0, 'Block', 'B', '--', 'Good'),
        (50.0, 'Block', 'B', 'Good', 'Failed'),
        (50.0, 'Block', 'B', 'Failed', 'Repairing'),
        (50.0, 'Node', 'end', '--', 'Bad'),
        (50.0, 'System', 'State_change', '--', 'Red'),
        (100.0, 'Block', 'A', 'Good', 'PM_is_due'),
        (100.0, 'Block', 'A', 'PM_is_due', 'pmReqDefer'),
        (110.0, 'Block', 'B', 'Repairing', 'Repaired'),
        (110.0, 'Block', 'B', 'Repaired', 'Good'),
        (110.0, 'Node', 'end', 'Bad', 'Good'),
        (110.0, 'Block', 'A', 'pmReqDefer', 'Resources'),
        (110.0, 'Block', 'A', 'Resources', 'PM'),
        (110.0, 'Node', 'N2', '--', 'NodePM'),
        (110.0, 'Node', 'end', 'Good', 'NodePM'),
        (115.0, 'Block', 'A', 'PM', 'PM_complete'),
        (115.0, 'Block', 'A', 'PM_complete', 'Good'),
        (115.0, 'Node', 'N2', 'NodePM', 'Good'),
        (115.0, 'Node', 'end', 'NodePM', 'Good'),
        (115.0, 'System', 'State_change', 'Red', 'Green'),
        (120.0, 'Block', 'A', 'Good', 'Done'),
        (120.0, 'Block', 'B', 'Good', 'Done'),
        (120.0, 'System', 'Phase_change', '--', 'Mission_failure'),
        (120.0, 'System', 'Simulation_Terminate', '--', '--'),
    ])

def test_a_deferred_failure_waits_for_its_trigger():
    """A `maintDefer` block's natural failure is held in RepHold until the defer trigger fires.

    Why: Raptor 7 (the Avant-garde defer-trigger feature): after the life runs out
    the block logs `Failed-deferring`, goes to RepHold ("deferred maintenance") and `WAIT FOR trig
    TO Fire`; only then does `DetermineNewState("Block Has Failed")` log `Failed` and repair run.
    Captured against real Raptor on `DeferTrigProbe` (`tests/golden/DeferTrigProbe/legacy/`).

    Setup: A (life 10, spare pool SP, empty until t=110) and B (life 20), both with a 2-hour
    pre-LDT and the comment `maintDefer`; a trigger T1 firing once at t=100, the defer trigger set to
    T1.

    Expected: both fail and are held; at 100 the trigger fires and each logs `Failed`; neither logs a
    second RepHold (it is already there) so A goes `Failed -> Sparing -> End_Pre-LDT` and B
    `Failed -> End_Pre-LDT`, as in the real capture.

    Source: raptor/core/engine/wait_sites.py `WaitSite.DEFER_TRIGGER_WAIT`,
     block_lifecycle.py `_defer_maintenance`
    """
    result, log = observe(defer_trigger_hold())
    assert_reached(log, "A", "defer_trigger_wait", "Trigger_Fired")
    assert_timeline(result, [
        (0.0, 'Block', 'A', '--', 'Good'),
        (0.0, 'Block', 'B', '--', 'Good'),
        (10.0, 'Block', 'A', 'Good', 'Failed-deferrin'),
        (10.0, 'Block', 'A', 'Failed-deferrin', 'RepHold'),
        (10.0, 'Node', 'N2', '--', 'Bad'),
        (10.0, 'Node', 'end', '--', 'Bad'),
        (10.0, 'System', 'State_change', '--', 'Red'),
        (20.0, 'Block', 'B', 'Good', 'Failed-deferrin'),
        (20.0, 'Block', 'B', 'Failed-deferrin', 'RepHold'),
        (20.0, 'System', 'Red_change', '--', 'Red'),
        (100.0, 'Trigger', 'T1', '--', 'Fired'),
        (100.0, 'Block', 'A', 'RepHold', 'Failed'),
        (100.0, 'Block', 'A', 'Failed', 'Sparing'),
        (100.0, 'Block', 'B', 'RepHold', 'Failed'),
        (102.0, 'Block', 'A', 'Sparing', 'End_Pre-LDT'),
        (102.0, 'Block', 'B', 'Failed', 'End_Pre-LDT'),
        (102.0, 'Block', 'B', 'End_Pre-LDT', 'Repairing'),
        (102.0, 'System', 'Red_change', 'Red', 'Red'),
        (107.0, 'Block', 'B', 'Repairing', 'Repaired'),
        (107.0, 'Block', 'B', 'Repaired', 'Good'),
        (107.0, 'System', 'Red_change', 'Red', 'Red'),
        (110.0, 'Pool', 'SP', '--', 'Sparing'),
        (110.0, 'Block', 'A', 'End_Pre-LDT', 'Sparing'),
        (110.0, 'Block', 'A', 'Sparing', 'Repairing'),
        (110.0, 'System', 'Red_change', 'Red', 'Red'),
        (115.0, 'Block', 'A', 'Repairing', 'Repaired'),
        (115.0, 'Block', 'A', 'Repaired', 'Good'),
        (115.0, 'Node', 'N2', 'Bad', 'Good'),
        (115.0, 'Node', 'end', 'Bad', 'Good'),
        (115.0, 'System', 'State_change', 'Red', 'Green'),
        (125.0, 'Block', 'A', 'Good', 'Failed-deferrin'),
        (125.0, 'Block', 'A', 'Failed-deferrin', 'RepHold'),
        (125.0, 'Node', 'N2', 'Good', 'Bad'),
        (125.0, 'Node', 'end', 'Good', 'Bad'),
        (125.0, 'System', 'State_change', 'Green', 'Red'),
        (127.0, 'Block', 'B', 'Good', 'Failed-deferrin'),
        (127.0, 'Block', 'B', 'Failed-deferrin', 'RepHold'),
        (127.0, 'System', 'Red_change', 'Red', 'Red'),
        (130.0, 'Block', 'A', 'RepHold', 'Done'),
        (130.0, 'Block', 'B', 'RepHold', 'Done'),
        (130.0, 'System', 'Phase_change', '--', 'Mission_failure'),
        (130.0, 'System', 'Simulation_Terminate', '--', '--'),
    ])
