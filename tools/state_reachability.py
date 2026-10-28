#!/usr/bin/env python3
"""Which `WaitSite x InterruptReason` cells a constructible model can reach.

`tools/state_coverage.py` records which of the 113 policy-allowed
cells the 283 snapshot models reach and writes
`docs/engine-design/state-coverage.adoc`. **54 of them nothing reaches**,
and that number is probably
inflated: some cells are defensive branches a well-formed model cannot
construct, so the retirement gate has no denominator until they are
separated out. This tool is what settles it.

**Classification, not counting.** Every one of the 113 cells lands in one of
three classes, and every cell must land in one:

    reachable        a constructible model can drive a block into that
                     `(site, reason)` state; `probe` names the model that
                     does it, and the model is a spec dict another agent can
                     turn into a test verbatim.
    already_pinned   reachable, AND already asserted by a committed
                     `assert_timeline` literal under `tests/behaviour/`.
    unreachable      needs an argument, and the argument is in `reason`.
                     "The engine has no sender for this reason" and "the
                     delivery gate excludes this site" are the two kinds that
                     hold up; "I could not build one" is not.

The reachability evidence is **construction, not reading**. Each probe is a
small `build_from_spec` spec -- every distribution `Fixed` (code 19), so it is
exact and consumes no randomness -- run through the *existing*
`state_coverage._Recorder` observer, so detection is the corpus's own and not
a reimplementation. `--write` regenerates
`tests/behaviour/state_reachability.toml` from the probes' measured results;
`--check` re-runs the probes and fails if the committed classification
disagrees with them, which is what stops the manifest from flattering itself.

Usage (repo root):

    python tools/state_reachability.py            # print the report
    python tools/state_reachability.py --write    # rewrite the TOML
    python tools/state_reachability.py --check    # gate mode

`--check` is what `tests/behaviour/test_b_state_coverage.py` reads its
verdict from; the numbers below are the ones that gate re-derives.
"""
from __future__ import annotations

import argparse
import copy
import re
import sys
from dataclasses import dataclass, field
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))
sys.path.insert(0, str(REPO / "tools"))
sys.path.insert(0, str(REPO / "tools"))

from raptor.core.engine.block_env import observing  # noqa: E402
from raptor.core.engine.wait_sites import WAKE_POLICY, WaitSite  # noqa: E402

import state_coverage as sc  # noqa: E402  (the corpus's own observer)

MANIFEST = REPO / "tests" / "behaviour" / "state_reachability.toml"
PAGE = REPO / "docs" / "engine-design" / "state-coverage.adoc"

#: The "(timer)" pseudo-reason `state_coverage` records when a wait ran its
#: full length rather than being cut short.
TIMER = sc.TIMER

#: A fail life past `_FAR_FUTURE` (1e9 h, `block_lifecycle._FAR_FUTURE`).
#: `_fail_wait`'s beyond-horizon branch compares `SimTime + life/stress`
#: against `BlockEnv.run_length`, which `kickoff.block_horizon` sets to
#: `_FAR_FUTURE` for EVERY termination type -- so a life this long is what
#: parks a block at `WaitSite.BEYOND_HORIZON`. It is a legitimate `Fixed`
#: life, not a trick: the model runs 200 h and the block simply never fails.
FAR = 2.0e9

Cell = tuple[str, str]


#: The page's one placeholder row. It is not a `WaitSite x reason` cell at
#: all -- `state_coverage.render` prints it when a site's policy permits
#: neither a timer nor any reason and no model has reached it yet, so there
#: is no `Ends by` value to put in the column. It is declared here, rather
#: than beside `_placeholder_entry`, because `pinned_by_suite()` needs it:
#: a fixture that reaches the *site* has answered the placeholder, and the
#: measurement has to be able to say so.
PLACEHOLDER: Cell = ("pm_sync_wait_handback", "_nothing reaches it_")


# --------------------------------------------------------------------------
# The 113 cells, re-derived rather than trusted
# --------------------------------------------------------------------------

_ROW = re.compile(r"^\| `(\w+)` \| (.+?) \| (\d+) \|")


def corpus_cells() -> dict[Cell, int]:
    """The committed matrix: every `WaitSite x reason` row and its model count.

    Parsed out of `state-coverage.adoc` with the same regex
    `state_coverage._ROW`, so this tool and the page cannot drift into
    disagreeing about what the denominator is.
    """
    out: dict[Cell, int] = {}
    for line in PAGE.read_text(encoding="utf-8").splitlines():
        match = _ROW.match(line)
        if match:
            out[(match.group(1), match.group(2))] = int(match.group(3))
    return out


def policy_cells() -> set[Cell]:
    """What `wait_sites.WAKE_POLICY` alone permits.

    Sites whose policy says "any reason" (`reasons is None`) contribute
    nothing: the page lists only the reasons that actually arrived, and this
    tool's job is to classify the page's rows, not to invent new ones.
    """
    cells: set[Cell] = set()
    for site, policy in WAKE_POLICY.items():
        if policy.timer:
            cells.add((str(site), TIMER))
        for reason in policy.reasons or ():
            cells.add((str(site), reason))
    return cells


def all_cells() -> list[Cell]:
    """The 113 cells: every row of the committed matrix, sorted."""
    return sorted(corpus_cells())


# --------------------------------------------------------------------------
# Probes: one small constructible model per reachable cell
# --------------------------------------------------------------------------

#: Distribution code 19 is `Fixed`: `DrawNumber`'s `WHEN 19` returns
#: `Param(1)` without touching a stream (`raptor/core/rng.py:451`), so every
#: probe below is exact and consumes no randomness.
FIXED_DIST = 19


def _block(name: str, life: float, repair: float = 10.0, **kw) -> dict:
    """One block with a `Fixed` life of *life* and a `Fixed` repair."""
    block = {"fail_dist": FIXED_DIST, "fail_params": [life], "name": name,
             "repair_dist": FIXED_DIST, "repair_params": [repair]}
    block.update(kw)
    return block


def _model(blocks: list[dict], nodes: list[dict], links: list[dict],
           run_length: float = 300.0, **extra) -> dict:
    """A `build_from_spec` spec: the minimum topology the loader accepts.

    `build_from_spec` refuses a model with no End node and a block with no
    outgoing link (`tests/behaviour/spine.py` documents both), so every probe
    carries a Start and an End.
    """
    spec = {"system": {"run_length": run_length, "num_runs": 1,
                       "termination": "TimeSim"},
            "blocks": blocks, "nodes": nodes, "links": links}
    spec.update(extra)
    return spec


def _chain(*names: str) -> tuple[list[dict], list[dict]]:
    """Start -> names -> End in series, one k=1 node between each pair.

    Node ids are 1 (Start), then 2, 3, ... between blocks, and the End last,
    so a probe can name a node id as a dependency target. With a single name
    there is no intermediate node, so Start and End are ids 1 and 2.
    """
    names = [n for n in names]
    if not names:
        raise ValueError("_chain needs at least one block name")
    nodes = [{"id": 1, "name": "Start", "type": 1, "n": 0, "out": 1}]
    links: list[dict] = [{"from": "Start", "to": names[0]}]
    previous, nid = names[0], 2
    for name in names[1:]:
        nodes.append({"id": nid, "name": f"N{nid}", "type": 2, "n": 1,
                      "out": 1, "k": 1})
        links.append({"from": previous, "to": f"N{nid}"})
        links.append({"from": f"N{nid}", "to": name})
        previous, nid = name, nid + 1
    nodes.append({"id": nid, "name": "End", "type": 3, "n": 1, "out": 0})
    links.append({"from": previous, "to": "End"})
    return nodes, links


def _fork() -> tuple[list[dict], list[dict]]:
    """Start -> A, Start -> B, both into a k=1-of-2 Join, then End."""
    nodes = [{"id": 1, "name": "Start", "type": 1, "n": 0, "out": 2},
             {"id": 2, "name": "Join", "type": 2, "n": 2, "out": 1, "k": 1},
             {"id": 3, "name": "End", "type": 3, "n": 1, "out": 0}]
    links = [{"from": "Start", "to": "A"}, {"from": "Start", "to": "B"},
             {"from": "A", "to": "Join"}, {"from": "B", "to": "Join"},
             {"from": "Join", "to": "End"}]
    return nodes, links


def _pm(**kw) -> dict:
    """PM settings that put a block on a Fixed PM schedule we control."""
    base = {"uses_pm": True, "pm_freq": 50.0, "pm_stagger_time": 0.0,
            "pm_dist": FIXED_DIST, "pm_params": [5.0], "pm_refresh": False}
    base.update(kw)
    return base


def _phases(durations: list[float], *, mission: list[bool] | None = None,
            values: tuple[float, ...] = (0.5, 1.0)) -> dict:
    """A phase schedule of `Fixed` durations, with block `A` phased too.

    Two things here are load-bearing and both fail silently if wrong.

    * `build_from_spec`'s `phases` key takes `duration_dist`/`params`, not a
      `duration` scalar: a bare `duration` is ignored and every phase then
      gets the default *stochastic* duration, so a "Phase_Change at exactly
      t=X" probe lands nowhere and draws randomness.
    * The block's phase values must **differ** between phases.
      `_conduct_phase_change_body` skips a block whose old and new type AND
      value are both unchanged (`cascade_phase.py:91`), so identical values
      mean no `apply_block` call and therefore no `Phase_Change` interrupt
      at all.

    `phase_states` must give EVERY phase a value or the engine's phase parser
    crashes on a blank cell (`_apply_phase_assignment`'s docstring).
    """
    values = list(values)
    if len(values) < len(durations):
        values = values + [values[-1]] * (len(durations) - len(values))
    phases = []
    for index, duration in enumerate(durations):
        phases.append({
            "name": f"Ph{index + 1}", "duration_dist": FIXED_DIST,
            "params": [duration],
            "mission": 1 if mission is None else int(mission[index]),
        })
    return {"phases": phases,
            "phase_states": {"A": [f"A: {values[i]}"
                                   for i in range(len(durations))]}}


def _cold(a_life: float, b_life: float, *, a_kw: dict | None = None,
          b_kw: dict | None = None, a_pri: int = 25, b_pri: int = 50,
          run_length: float = 400.0) -> dict:
    """Start -> A, Start -> B into a 1-of-2 cold node, then End.

    `k_star` is load-bearing twice over: without it `run_cold_logic`'s
    `runs_required` is 0, the survey demotes *both* peers, and the node's
    own status formula raises `Error in node status logic (cold logic)` --
    so a probe that omits it fails to build a runnable model at all.
    """
    nodes = [{"id": 1, "name": "Start", "type": 1, "n": 0, "out": 2},
             {"id": 3, "name": "Cold", "type": 2, "n": 2, "out": 1, "k": 1,
              "k_star": 1, "cold": 1},
             {"id": 4, "name": "End", "type": 3, "n": 1, "out": 0}]
    links = [{"from": "Start", "to": "A"}, {"from": "Start", "to": "B"},
             {"from": "A", "to": "Cold", "cold_pri": a_pri, "a_prob": 1.0,
              "auto_time": 0.0, "man_time": -1.0},
             {"from": "B", "to": "Cold", "cold_pri": b_pri, "a_prob": 1.0,
              "auto_time": 0.0, "man_time": -1.0},
             {"from": "Cold", "to": "End"}]
    return _model([_block("A", a_life, **(a_kw or {})),
                   _block("B", b_life, **(b_kw or {}))], nodes, links,
                  run_length=run_length)


#: The block ids `_cold_dependent` wires, stated rather than inferred.
#: `build_from_spec` numbers `BLOCK_DATA` rows in list order
#: (`raptor/api/build.py:857-864`), so a probe that names `a_dep=1` is
#: naming `Y` -- and a reader should not have to know that to check it.
Y_ID, X_ID, A_ID = 1, 2, 3


def _cold_dependent(peer_life: float, a_life: float, *, a_dep: int = X_ID,
                    a_sb: float = 0.5, peer_fail: float = 50.0,
                    peer_repair: float = 50.0, run_length: float = 400.0,
                    a_kw: dict | None = None) -> dict:
    """A cold node whose peer block a third block `A` block-depends on.

    `Y` (pri 25) and `X` (pri 50) are the peers; `X` fails at *peer_fail* and
    repairs *peer_repair* later, so the node alternates Bad/Good and the
    survey promotes then demotes one peer while `A` sits in a long wait. `A`
    gets a non-zero `sb_stress` so a Standby promotion parks it in a real
    wait rather than at `WaitSite.ZERO_STRESS`.

    *a_dep* is which peer `A` block-depends on: `Y_ID` or `X_ID`. It matters
    -- `Dependence_Standby` is back-flowed from the block that goes Standby,
    so only a dependent of the demoted peer sees it (`dependency.py:305-309`).
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
    a = _block("A", a_life, id=A_ID, dep_type="RBDBlock", dep_num=a_dep,
               dep_idle=100.0, sb_stress=a_sb)
    a.update(a_kw or {})
    return _model([_block("Y", peer_life, id=Y_ID, sb_stress=0.5),
                   _block("X", peer_fail, peer_repair, id=X_ID, sb_stress=0.5),
                   a], nodes, links, run_length=run_length)


def _beyond_horizon_dep(a_life: float, b_life: float, b_repair: float,
                        *, dep_type: str = "RBDNode", dep_num: int = -2,
                        run_length: float = 300.0) -> dict:
    """`A` parked beyond the horizon, `B` failing downstream of it.

    `A` sysDepends on the model's End node (`DepNum=-2`), so `B`'s failure
    idles `A` -- and the idling is what lands the reason in `A`'s
    beyond-horizon wait. `b_repair=0.0` puts `B`'s whole fail/repair cycle
    in one instant, so the recovery's `Dependence_All_Up` is queued while
    `A` is still parked and the reason rewrite puts *that* in the wait.
    """
    nodes, links = _chain("A", "B")
    a = _block("A", a_life, dep_type=dep_type, dep_num=dep_num, dep_idle=100.0)
    return _model([a, _block("B", b_life, b_repair)], nodes, links,
                  run_length=run_length)


@dataclass
class Probe:
    """One constructible model and the cell it is evidence for."""

    name: str
    spec: dict
    cells: tuple[Cell, ...]
    reason: str = ""
    """What shape of model does it. On a `reachable` cell this is the
    sentence a reviewer reads instead of the spec; on an `unreachable` cell
    it is the argument."""
    claims: tuple[Cell, ...] = ()
    """Cells this probe was BUILT for. If a probe does not reach what it
    claims, that is recorded rather than hidden -- see the `observation`
    field and `classify`'s ATTEMPTED branch."""


def probes() -> list[Probe]:
    """Every probe, in the order the manifest lists them.

    Each is a real model that was built and run: the cell list is what the
    `state_coverage` observer actually recorded, not what the spec was
    supposed to produce. `--write` measures them; `--check` re-measures and
    compares.
    """
    out: list[Probe] = []

    def add(name: str, spec: dict, cells: tuple[Cell, ...], reason: str) -> None:
        out.append(Probe(name, spec, cells, reason, claims=cells))

    # --- the three PM TELL boundaries, reached by their own (timer) wake --
    nodes, links = _chain("A")
    add("pm-prereqs-tell-timer",
        _model([_block("A", 500.0, **_pm())], nodes, links),
        (("pm_prereqs_tell", TIMER),),
        "One PM-using block, no prerequisites at all: `DetermineNewState"
        "(\"PM is due\")` -> `ObtainPMprereqs` pays the TELL hop "
        "(`block_lifecycle.py:2287`) and nothing interrupts it.")

    nodes, links = _chain("A")
    add("pm-handback-timer",
        _model([_block("A", 500.0, num_res=1, resource_name="R",
                       res1_pm_rep=1, **_pm())], nodes, links,
               resources=[{"name": "R", "num_in": 1}]),
        (("pm_handback", TIMER),),
        "The same block with `num_res=1`/`#Res1pmRep=1` and a one-unit "
        "RESOURCE pool: the resource step succeeds, so the post-resource "
        "hand-back hop is paid and runs to its full length "
        "(`block_lifecycle.py:2685`).")

    nodes, links = _chain("A")
    add("perform-pm-tell-timer",
        _model([_block("A", 500.0, pm_spare=True, stock=1, rso=0.0,
                       **_pm())], nodes, links),
        (("perform_pm_tell", TIMER),),
        "A Custom-spare PM (`PMSpare`, `stock=1`): the prereq TELL boundary "
        "is skipped (`pm_handback_taken` stays False), so "
        "`_perform_pm`'s own TELL hop runs to its full length "
        "(`block_lifecycle.py:2999`).")

    add("pm-sync-wait-handback",
        _model([_block("A", 500.0, num_res=1, resource_name="R", res1_pm_rep=1,
                       pm_mis_defer=True, **_pm())],
               *_chain("A"), phases=[
                   {"name": "Ph1", "duration_dist": FIXED_DIST, "params": [50.0],
                    "mission": 1},
                   {"name": "Ph2", "duration_dist": FIXED_DIST, "params": [400.0],
                    "mission": 0}],
               phase_states={"A": ["A: 0.5", "A: 1.0"]},
               resources=[{"name": "R", "num_in": 1}]),
        (("pm_sync_wait_handback", "Trigger_Fired"),),
        "A `pmMisDefer` PM borrowing a resource, with a phase change in "
        "progress at the hand-back: the block parks on `pmSyncTrigger` "
        "(`block_lifecycle.py:2696`), which the phase change's end releases "
        "with `Trigger_Fired` (`kickoff.py:710`). The page annotated this "
        "site `_nothing reaches it_`; it is reachable.")

    # --- beyond_horizon: one parked block, one reason --------------------
    add("bh-timer",
        _model([_block("A", 1.5e9)], *_chain("A"), run_length=2.0e9),
        (("beyond_horizon", TIMER),),
        "The beyond-horizon wait's full length is `run_length - SimTime` "
        "against the block's `_FAR_FUTURE` horizon, so it runs out at "
        "t=1e9 h -- and the run has to outlast that, or EndSimulation ends "
        "it first. That horizon is the BLOCK's, not the model's: "
        "`BlockEnv` is built with `block_horizon`, which "
        "`kickoff.block_horizon` sets to `_FAR_FUTURE` for every "
        "termination type (`kickoff.py:480-485`), so `run_length = 2e9` on "
        "the model and `Fixed(1.5e9)` on the block puts the wait's end at "
        "t=1e9 h and the run's end an hour later: the block goes "
        "`Good -> Done` at t=1e9, one billion hours before the run does. "
        "Absurd but well-formed, and the only reason no shipped fixture "
        "reaches the cell.")

    add("bh-dependence-idle",
        _beyond_horizon_dep(FAR, 50.0, 20.0),
        (("beyond_horizon", "Dependence_Idle"),),
        "A block whose fail life exceeds `_FAR_FUTURE` is parked in the "
        "beyond-horizon wait (`block_lifecycle.py:2068`); a second block "
        "failing downstream of A's `sysDepend` on the End node dispatches "
        "`Dependence_Idle` straight into that wait.")

    add("bh-dependence-sb-turnon",
        _cold(50.0, FAR, b_kw={"sb_stress": 0.5}),
        (("beyond_horizon", "Switched_On"),
         ("beyond_horizon", "Dependence_SB_turnOn")),
        "The high-priority cold peer fails, the survey promotes the "
        "low-priority peer, and that peer's own beyond-horizon wait takes "
        "the `Switched_On`. With a second block depending on the cold NODE, "
        "the same promotion reads as `Dependence_SB_turnOn` -- "
        "`_dispatch_dependency`'s `was_standby` branch "
        "(`cascade_dependency.py:74`).")

    add("bh-phase-change",
        _model([_block("A", FAR)], *_chain("A"),
               **_phases([100.0], mission=[1], )),
        (("beyond_horizon", "Phase_Change"),),
        "A phased block parked beyond the horizon: the phase boundary's "
        "`ChangePhases` tail interrupts whichever wait the block is in "
        "(`cascade_phase.py:158`).")

    add("bh-triggered-pm-due",
        _model([_block("A", FAR, uses_pm=True, pm_freq=5000.0,
                       pm_stagger_time=0.0, pm_dist=19, pm_params=[5.0],
                       pm_triggered=True, pm_trig="T1")],
               *_chain("A"),
               triggers=[{"name": "T1", "init_used": 0.0, "dist": 19,
                          "params": [100.0, 0.0, 0.0], "repeats": 1}]),
        (("beyond_horizon", "Triggered_PM_Due"),),
        "A `pmTriggered` block parked beyond the horizon with a PM trigger "
        "scheduled at t=100: `ControlPMtriggers`'s eligibility test admits "
        "a Running block, so the fire lands in the wait "
        "(`kickoff.py:1003`).")

    add("bh-start-deferred-pm",
        _model([_block("A", FAR, uses_pm=True, pm_freq=100.0,
                       pm_stagger_time=0.0, pm_dist=19, pm_params=[5.0],
                       pm_mis_defer=True)],
               *_chain("A"), **_phases([200.0, 400.0], mission=[1, 0])),
        (("beyond_horizon", "Start_Deferred_PM"),),
        "A `pmMisDefer` block parked beyond the horizon while a mission "
        "phase is active: the phase change back to NonMission releases "
        "every deferred block with `Start_Deferred_PM` "
        "(`cascade_phase.py:295`).")

    add("bh-pool-available",
        _model([_block("A", FAR, num_res=1, resource_name="R", res1_pm_rep=1,
                       pm_req_defer=True, uses_pm=True, pm_freq=100.0,
                       pm_stagger_time=0.0, pm_dist=19, pm_params=[5.0]),
                _block("B", 50.0, 60.0, num_res=1, resource_name="R",
                       res1_pm_rep=1, uses_pm=True, pm_freq=100.0,
                       pm_stagger_time=0.0, pm_dist=19, pm_params=[5.0])],
               *_chain("A", "B"), resources=[{"name": "R", "num_in": 1}]),
        (("beyond_horizon", "Pool_Available"),),
        "A `pmReqDefer` block parked beyond the horizon whose abandoned PM "
        "resource request is granted when a sibling's repair hands the unit "
        "back: the pool's `Give` wakes it with `Pool_Available` and "
        "`_on_fail_wait_wake` reconciles the stale grant "
        "(`block_lifecycle.py:1135`).")

    nodes, links = _chain("A")
    add("defer-trigger-hold",
        _model([_block("A", 10.0, 5.0)], nodes, links,
               triggers=[{"name": "T1", "init_used": 0.0, "dist": 19,
                          "params": [100.0, 0.0, 0.0], "repeats": 0}],
               comments=[{"element": "A", "comment": "maintDefer"}],
               defer_trigger="T1", run_length=120.0),
        (("defer_trigger_wait", "Trigger_Fired"),),
        "A block commented `maintDefer` with the defer trigger set to a trigger "
        "that fires once at t=100: the failure at t=10 is held in RepHold "
        "and the fire ends the wait.")

    add("bh-dependence-all-up",
        _beyond_horizon_dep(FAR, 50.0, 0.0),
        (("fail_wait", "Dependence_All_Up"),
         ("beyond_horizon", "Dependence_All_Up")),
        "`Dependence_All_Up` only reaches a *non*-Idle wait as a stale or "
        "duplicate signal, which needs two dispatches queued for one block "
        "in one instant. A downstream block whose repair is `Fixed(0.0)` "
        "goes down and back up inside one instant, so both reasons are "
        "queued and `Scheduler.interrupt` rewrites the pending entry's "
        "reason to the second -- leaving `Dependence_All_Up` in whatever "
        "wait the block is actually parked at.")

    add("bh-dependence-standby",
        _cold_dependent(FAR, 500.0, a_dep=X_ID),
        (("kickoff", "Dependence_Standby"),
         ("beyond_horizon", "Dependence_All_Up")),
        "A block that block-depends on a cold-standby peer: the peer's "
        "`Too_many_on` Standby transition dispatches `Dependence_Standby` "
        "(`cascade_dependency.py:71`). At kickoff that lands in the "
        "dependent's kickoff barrier.")

    add("bh-dependence-standby-live",
        _cold_dependent(50.0, FAR, a_dep=Y_ID),
        (("beyond_horizon", "Dependence_Standby"),),
        "The same back-flow with the DEPENDENT parked past the horizon "
        "instead: `A` has a `Fixed(2e9)` life, so it is waiting in "
        "`BEYOND_HORIZON` while `Y` (which it block-depends on) is "
        "demoted by the survey `X`'s repair completion drives. The demoted "
        "block is `X`, not `A`, so `_switch_interrupt`'s "
        "`Scheduler.is_executing` guard (`cascade_cold.py:270-272`) does not "
        "apply -- the reason is back-flowed to a third block by "
        "`dependency.py:305-309`, and `_dispatch_dependency` interrupts "
        "whatever wait the dependent is in. Measured: the demotion at "
        "t=150 lands `Dependence_Standby` in `A`'s beyond-horizon wait.")

    # --- fail_wait: the same reasons, at an ordinary-length life ---------
    add("fail-wait-dependence-sb-turnon",
        _cold_dependent(500.0, 500.0, a_dep=X_ID),
        (("fail_wait", "Dependence_SB_turnOn"),),
        "The cold-standby promotion of the previous probe with a 500-hour "
        "life on both blocks, so the dependent is at its ordinary "
        "fail-wait rather than beyond the horizon when the promotion "
        "arrives.")

    # --- perform_pm_tell / pm_handback x Phase_Change ---------------------
    nodes, links = _chain("A")
    add("perform-pm-tell-phase-change",
        _model([_block("A", 500.0, **_pm())], nodes, links,
               **_phases([50.0, 400.0], mission=[1, 0], values=(0.5, 1.0))),
        (("perform_pm_tell", "Phase_Change"),),
        "A phase boundary at exactly the PM-due instant: the phase change's "
        "`apply_block` runs five zero-length hops after its own clock entry, "
        "which lands between the block's `pm_prereqs_tell` and "
        "`perform_pm_tell` hops. `Phase_Change` is the one reason "
        "`cascade_phase.py:158` will send to a block in neither `FailWait` "
        "nor `ZeroWait`.")

    nodes, links = _chain("A")
    add("pm-handback-phase-change",
        _model([_block("A", 500.0, num_res=1, resource_name="R", res1_pm_rep=1,
                       **_pm())], nodes, links,
               resources=[{"name": "R", "num_in": 1}],
               **_phases([50.0, 400.0], mission=[1, 0], values=(0.5, 1.0))),
        (("pm_handback", "Phase_Change"),),
        "The same boundary with a resource-pool PM, so the hand-back hop is "
        "the last one paid before `PerformPM` and the phase change lands "
        "there instead.")

    # --- pm_misdefer_run: the `ELSE Run` hop a deferred PM pays ---------
    add("pm-misdefer-run",
        _model([_block("A", 500.0, pm_mis_defer=True, **_pm())],
               *_chain("A"),
               **_phases([200.0, 400.0], mission=[1, 0])),
        (("pm_misdefer_run", TIMER),
         ("pm_misdefer_run", "Dependence_Idle"),
         ("pm_misdefer_run", "Induced_Failure"),
         ("pm_scheduled_hop", TIMER)),
        "A `pmMisDefer` block whose PM comes due inside a mission phase: it "
        "takes the defer gate, joins `pmDeferGroup` and pays the `ELSE Run` "
        "hop (`block_lifecycle.py:2244`) instead of continuing to "
        "`PerformPM`. The dependency reasons arrive at that hop because "
        "`ELSE Run` spawns a fresh Run record, so the block is in neither "
        "`FailWait` nor `ZeroWait` for the instant the dispatch runs.")

    # --- repair: a stale PM grant absorbed mid-repair --------------------
    add("repair-pool-available",
        _model([_block("A", 500.0, num_res=1, resource_name="R", res1_pm_rep=1,
                       pm_req_defer=True, uses_pm=True, pm_freq=100.0,
                       pm_stagger_time=0.0, pm_dist=FIXED_DIST,
                       pm_params=[5.0]),
                _block("B", 50.0, 60.0, num_res=1, resource_name="R",
                       res1_pm_rep=1, uses_pm=True, pm_freq=100.0,
                       pm_stagger_time=0.0, pm_dist=FIXED_DIST,
                       pm_params=[5.0])],
               *_chain("A", "B"), resources=[{"name": "R", "num_in": 1}]),
        (("repair", "Pool_Available"),),
        "The repair twin of the stale-grant probe: a `pmReqDefer` block's "
        "abandoned PM resource request is still queued when the block fails, "
        "so a sibling's release grants it mid-repair and `_repair_wait` "
        "absorbs it (`block_lifecycle.py:3636`).")

    return out


def pinned_by_suite(rungs: tuple[int, ...] | None = None) -> dict[Cell, str]:
    """Cells the committed `tests/behaviour/` fixtures already reach.

    **Measured, not claimed**: every rung of `spine` is run under the same
    `state_coverage._Recorder` observer the corpus scan uses, the cells it
    reaches are recorded, and so are the cells the suite's *own* wait-site
    fixtures reach (`suite_fixtures`). For the same reason
    `test_b_behaviour_coverage.py` parses `assert_timeline` literals with
    `ast` instead of running the suite: a gate that reads its own numerator
    from a file it also writes cannot notice the file rotting.

    Every rung is run, not just the first three. `test_b_ladder.py` asserts
    each rung's COMPLETE event-log timeline as a literal
    (`test_spine_rung_timeline_is_unchanged`), so a cell a rung reaches is
    inside an asserted literal -- which is what `already_pinned` means here.
    S8 added cold standby, S9 phases and S11 capacity on top of S1-S3, and
    they reach cells the first three never touch; measuring only S1-S3
    understated the suite by exactly those cells. `test_b_wait_sites.py`'s
    fixtures do the same job one cell at a time: each of its tests asserts
    the `(block, wait site, reason)` triple the observer recorded *and* the
    model's whole timeline, so a cell one of them reaches is inside an
    asserted literal too.

    *rungs* restricts the ladder half, for a caller that wants one rung. The
    fixtures are always all measured -- there is nothing to restrict.
    """
    rungs_for: dict[Cell, list[int]] = {}
    for rung in (rungs if rungs is not None else _spine_rungs()):
        for cell in _observe_cells(_ladder_spec(rung)):
            rungs_for.setdefault(cell, []).append(rung)
    pinned = {cell: f"tests/behaviour/test_b_ladder.py ({_rungs(rungs)})"
              for cell, rungs in rungs_for.items()}
    for module_name, label, spec in suite_fixtures():
        for cell in _observe_cells(spec):
            pinned.setdefault(cell, f"tests/behaviour/{module_name} ({label})")
    return pinned


def _spine_rungs() -> tuple[int, ...]:
    """The ladder rungs to measure, read from `spine.RUNGS`."""
    from tests.behaviour.spine import RUNGS

    return RUNGS


def _ladder_spec(rung: int) -> dict:
    """One rung's canonical spec. Split out so a rung that cannot be built
    is one failure rather than an import-time crash for the measurement."""
    from tests.behaviour.spine import spine

    return spine(rung)


def suite_fixtures() -> list[tuple[str, str, dict]]:
    """Every wait-site fixture the committed suite declares, as
    `(module file, test name, spec)`.

    **Discovered, not declared here.** Every `tests/behaviour/test_b_*.py`
    module is imported and asked for a module-level
    `WAIT_SITE_FIXTURES` mapping of test name -> a zero-argument builder of
    a `build_from_spec` spec. Adding a fixture to a test module therefore
    pins its cells with no edit to this file, and a module that declares none
    is simply not a wait-site module.

    The attribute is read with `getattr(..., {})` rather than accessed
    directly because the gate imports the suite while `state_reachability`
    is still initialising: a test module that imported *this* module at its
    own module scope would be half-executed at that moment, and its registry
    would read as empty. A test module must therefore reach this one lazily,
    or not at all -- `test_b_wait_sites.py` reaches only `state_coverage`,
    which imports nothing from here.
    """
    import importlib

    out: list[tuple[str, str, dict]] = []
    for path in sorted((REPO / "tests" / "behaviour").glob("test_b_*.py")):
        module = importlib.import_module(f"tests.behaviour.{path.stem}")
        for label, builder in sorted(
                getattr(module, "WAIT_SITE_FIXTURES", {}).items()):
            out.append((path.name, label, builder()))
    return out


def _observe_cells(spec: dict) -> set[Cell]:
    """The `(site, reason)` cells one spec reaches, under the corpus observer.

    Same detector as `run_probe` and the 283-model scan, and the same
    `deepcopy`, because a spec dict is not re-runnable.
    """
    from tests.behaviour.harness import run as harness_run

    recorder = sc._Recorder()
    try:
        with observing(recorder):
            harness_run(copy.deepcopy(spec))
    except Exception:  # pragma: no cover -- a fixture that cannot run fails
        return set()     # its own test; counting what it reached is meaningless
    return {_as_matrix_cell(cell) for cell in recorder.wakes}


def _as_matrix_cell(cell: Cell) -> Cell:
    """The matrix row one observed cell belongs to.

    One adjustment, and it is the page's own convention:
    `pm_sync_wait_handback`'s policy permits neither a timer nor any reason,
    so `state_coverage.render` prints a `_nothing reaches it_` placeholder row
    until some model reaches the SITE. A fixture that drives that site to
    `Trigger_Fired` has answered the row -- which is exactly the reading
    `_placeholder_entry` gives a probe -- so it pins the placeholder cell
    rather than a row that does not exist. Every other observed cell is its
    own matrix row, unchanged.
    """
    return PLACEHOLDER if cell[0] == PLACEHOLDER[0] else cell


def _rungs(rungs: list[int]) -> str:
    """`[1, 2, 3]` -> `"S1-S3"`, `[5, 7]` -> `"S5, S7"`: a short label."""
    ordered = sorted(set(rungs))
    if not ordered:
        return "no rung"
    if ordered == list(range(ordered[0], ordered[-1] + 1)):
        return f"S{ordered[0]}-S{ordered[-1]}"
    return ", ".join("S" + str(rung) for rung in ordered)


#: The measured `already_pinned` set, for `classify`. Recomputed per process
#: run rather than committed as a literal, so it cannot go stale the way a
#: hand-written table does.
PINNED_BY_SUITE: dict[Cell, str] = pinned_by_suite()


# --------------------------------------------------------------------------
# Unreachable cells: the argument, per cell
# --------------------------------------------------------------------------

#: The three shared gate arguments, defined first because the table below
#: references them by name -- a per-cell copy of each would be four copies
#: of the same paragraph, and they would drift apart.
_PM_GATE = (
    "Delivery excluded. `_dispatch_dependency` skips the wake outright "
    "while `dep_block.run_state.waiting_for_pm_prerequisites` is set "
    "(`cascade_dependency.py:105`), and `_induce_dependency` refuses "
    "delivery for the same flag (`cascade_dependency.py:168`). The "
    "`pm_prereqs_tell` hop sets that flag for its whole window "
    "(`block_lifecycle.py:2286-2289`), and the port mirrors source here: "
    "`ObtainPMprereqs` is a separate process from `Run`, so a Run-targeted "
    "interrupt does not abandon it. Measured with a block-dependency "
    "sibling failing at exactly the PM-due instant: the dispatch DOES fire "
    "(`DISPATCH t=50 A -> IDLE (Dependence) wfpr=True pmtell=True`) and the "
    "state change is applied, but no wake is queued -- the block's wait "
    "records `(timer)`.")

_PM_GATE_SWITCH = (
    "Delivery excluded, twice over. `_switch_interrupt` refuses unless the "
    "target is in `FailWait` or `ZeroWait` (`cascade_cold.py:278`) -- and a "
    "block mid-`ObtainPMprereqs` is in neither, which is source's own "
    "`IF FailWait ... ELSIF ZeroWait ... END IF` with no ELSE. The block "
    "would additionally have to be *simultaneously* at this PM boundary and "
    "parked in an ordinary wait.")

_PM_TELL_GATE = (
    "Delivery excluded, as for the `pm_prereqs_tell` dependency cells: "
    "`waiting_for_pm_prerequisites` is set across the `perform_pm_tell` hop "
    "(`block_lifecycle.py:2997`), and both `_dispatch_dependency` and "
    "`_induce_dependency` refuse delivery while it is set.")

_PM_TELL_GATE_SWITCH = (
    "Delivery excluded, as for `pm_prereqs_tell x Switched_Off`: a block "
    "mid-`ObtainPMprereqs` is in neither `FailWait` nor `ZeroWait`, so "
    "`_switch_interrupt` returns without waking it "
    "(`cascade_cold.py:278`).")


#: Why each cell cannot be reached. Keyed by `(site, reason)`.
#:
#: Two kinds of argument hold up, and this table only contains those:
#:
#:   * **no sender** -- no code path in `raptor/` ever sends the reason, so
#:     the cell is in the matrix only because `IDLE_WAKE_REASONS` /
#:     `STATE_ALREADY_APPLIED` list it. That is an engine finding, not a
#:     test-suite gap, and it is reported as such.
#:   * **delivery excluded** -- the sender exists and would fire, but a gate
#:     in the dispatch path excludes this site unconditionally.
UNREACHABLE: dict[Cell, str] = {
    # --- Switch_Failed_Path: the engine has no sender at all -------------
    ("idle", "Switch_Failed_Path"): (
        "ENGINE FINDING -- no sender. `Switch_Failed_Path` is the only "
        "reason `wait_sites.WAKE_POLICY` permits that nothing in `raptor/` "
        "ever sends: `cold_standby._switch_failed` sets `cold_path_done` "
        "and deliberately does NOT interrupt the block, and its own comment "
        "(`cold_standby.py:274`) records that the earlier wake was removed "
        "because real does not re-enter `Run` there. A grep of every "
        "`scheduler.interrupt` / `wake_block` call in the engine finds no "
        "`InterruptReason.SWITCH_FAILED_PATH` anywhere. The cell is "
        "policy-permitted and engine-unreachable."),
    ("fail_wait", "Switch_Failed_Path"): (
        "ENGINE FINDING -- no sender, as for `idle x Switch_Failed_Path`. "
        "The reason is never sent by any code path, so no wait site can "
        "receive it."),
    ("beyond_horizon", "Switch_Failed_Path"): (
        "ENGINE FINDING -- no sender, as for `idle x Switch_Failed_Path`."),
    ("pm_prereqs_tell", "Switch_Failed_Path"): (
        "ENGINE FINDING -- no sender, as for `idle x Switch_Failed_Path`."),
    ("pm_handback", "Switch_Failed_Path"): (
        "ENGINE FINDING -- no sender, as for `idle x Switch_Failed_Path`."),
    ("perform_pm_tell", "Switch_Failed_Path"): (
        "ENGINE FINDING -- no sender, as for `idle x Switch_Failed_Path`."),

    # --- kickoff: the barrier is popped before any dispatch can reach it --
    ("kickoff", "Dependence_All_Up"): (
        "Delivery excluded. The kickoff barrier is the block's FIRST "
        "suspended `yield`, and at t=0 the block is given a scheduler "
        "handle in the same `_start_processes` loop that then schedules the "
        "node-level `InitTimeZeroDeps` re-dispatch as a separate zero-delay "
        "process (`kickoff.py:614-663`). The re-dispatch therefore runs "
        "AFTER every block's barrier has already popped, so by the time a "
        "`Dependence_All_Up` could be dispatched the block is at its first "
        "real wait. Measured: a model whose dependency target is down at "
        "t=0 produces `DISPATCH t=0 A -> IDLE (Dependence)` with "
        "`dep_block.id in self.handles` False -- the state change applies, "
        "the wake is not sent, and the block's kickoff cell records "
        "`(timer)`."),
    ("kickoff", "Dependence_Idle"): (
        "Delivery excluded, exactly as for `kickoff x Dependence_All_Up`: "
        "the `InitTimeZeroDeps` re-dispatch is scheduled as its own "
        "zero-delay process, so it runs after every block's kickoff "
        "barrier has popped. Measured with a down-start sibling: "
        "`induce` reports `inhandles=False -> False` on the first (kickoff) "
        "dispatch and `True` on the deferred second one, by which time the "
        "block is at its `fail_wait`."),
    ("kickoff", "Dependence_SB_turnOn"): (
        "Delivery excluded, as for `kickoff x Dependence_Idle`. This reason "
        "additionally requires `was_standby` -- the dependent must already "
        "be Standby -- and at the kickoff barrier a block is Running/Active "
        "(`block_lifecycle.py:1283`), so the branch cannot be taken at all "
        "before the barrier pops."),
    ("kickoff", "Induced_Failure"): (
        "Delivery excluded, as for `kickoff x Dependence_Idle`. Source's "
        "`InitTimeZeroDeps` is where this reason originates "
        "(`block_lifecycle.py:1312` says so), and the port's copy is the "
        "deferred zero-delay process that runs after the barriers have "
        "popped. Measured: the induce fires with "
        "`dep_block.id in self.handles` False at t=0 and is declined."),
    ("kickoff", "OBPM"): (
        "Delivery excluded, as for `kickoff x Induced_Failure`: "
        "`InitTimeZeroDeps`'s `GoToPM` is the only OBPM sender at t=0 "
        "(`block_lifecycle.py:1303`), and it runs after the kickoff "
        "barriers have popped. Measured: `inhandles=False -> False`."),

    # --- the PM TELL boundaries: waiting_for_pm_prerequisites / pm_tell_pending ----
    # `_dispatch_dependency` skips the wake outright for both flags, and
    # `_induce_dependency` refuses delivery while waiting_for_pm_prerequisites is
    # set. Every cell below is one of those two reasons.
    ("pm_prereqs_tell", "Dependence_All_Up"): _PM_GATE,
    ("pm_prereqs_tell", "Dependence_Idle"): _PM_GATE,
    ("pm_prereqs_tell", "Dependence_SB_turnOn"): _PM_GATE,
    ("pm_prereqs_tell", "Dependence_Standby"): _PM_GATE,
    ("pm_prereqs_tell", "Induced_Failure"): _PM_GATE,
    ("pm_prereqs_tell", "OBPM"): _PM_GATE,
    ("pm_prereqs_tell", "Phase_Change"): (
        "Not excluded by a gate, but not constructible: `Phase_Change` is "
        "sent only by the phase-change body, which runs five zero-length "
        "hops after its own clock entry "
        "(`cascade_phase.apply_block_or_event_group`). Sweeping the phase "
        "boundary against the block's PM-due instant over `Fixed` phase "
        "durations 49.99 / 49.999999 / 50.0 / 50.000001 / 50.01, and over "
        "the plain / resource-pool / Custom-spare PM paths and their "
        "pmMisDefer variants, the phase change lands either in the "
        "preceding `fail_wait` (boundary before the PM-due instant) or in "
        "the FOLLOWING hop -- `perform_pm_tell` or `pm_handback` -- never in "
        "the `pm_prereqs_tell` hop itself. Both neighbours are recorded "
        "reachable; this one is not, and the ordering argument is a fixed "
        "hop count rather than a coincidence."),
    ("pm_prereqs_tell", "Start_Deferred_PM"): (
        "Delivery excluded. `Start_Deferred_PM` is sent only to a block "
        "with `run_state.defer_pm` set (`cascade_phase.py:286-295`), and "
        "`defer_pm` is set only in the branch that RETURNS before the "
        "prereq TELL hop is paid (`block_lifecycle.py:2219-2250`): a "
        "deferred block runs `pm_misdefer_run` and goes back to the top of "
        "`Run`, never reaching `pm_prereqs_tell` while deferred. So the "
        "block is either deferred (and not at this site) or at this site "
        "(and not deferred). Measured across mission/NonMission phase "
        "schedules and four `pm_mis_defer` shapes: the release always "
        "lands at `fail_wait`, never at the prereq hop."),
    ("pm_prereqs_tell", "Switched_Off"): _PM_GATE_SWITCH,
    ("pm_prereqs_tell", "Switched_On"): _PM_GATE_SWITCH,
    ("pm_prereqs_tell", "Triggered_PM_Due"): (
        "Delivery excluded. `ControlPMtriggers`' eligibility test refuses "
        "while `waiting_for_pm_prerequisites` is set (`kickoff.py:958`), which is "
        "exactly the flag the `pm_prereqs_tell` hop holds "
        "(`block_lifecycle.py:2286`). A model with a PM trigger scheduled "
        "at exactly the PM-due instant records `fail_wait | "
        "Triggered_PM_Due` and `pm_prereqs_tell | (timer)` -- the trigger "
        "fires at the fail-wait, one hop earlier."),

    ("pm_handback", "Dependence_All_Up"): _PM_GATE,
    ("pm_handback", "Dependence_Idle"): _PM_GATE,
    ("pm_handback", "Dependence_SB_turnOn"): _PM_GATE,
    ("pm_handback", "Dependence_Standby"): _PM_GATE,
    ("pm_handback", "Induced_Failure"): _PM_GATE,
    ("pm_handback", "OBPM"): _PM_GATE,
    ("pm_handback", "Pool_Available"): (
        "Delivery excluded by construction: the policy note says this "
        "reason is accepted at the hand-back ONLY with token `\"pm\"` (an "
        "async PM-prerequisite grant), and `_on_pm_boundary_wake` "
        "implements exactly that one case (`block_lifecycle.py:1257`). But "
        "the grant that carries the `\"pm\"` token is what RESUMES the "
        "`pm_hold_resource` wait, not something that lands during the hop: "
        "the hand-back is only reached once the resource is already in "
        "hand. Measured with a `pmReqDefer` block on a one-unit pool (the "
        "async-pickup shape): the grant arrives at `fail_wait | "
        "Pool_Available`, the hand-back that follows records `(timer)`, and "
        "no `Pool_Available` lands at the hop."),
    ("pm_handback", "Phase_Change"): (
        "Constructed -- see the reachable rows: a phase boundary at the "
        "PM-due instant lands in the hand-back hop of a resource-pool PM."),
    ("pm_handback", "Start_Deferred_PM"): (
        "Delivery excluded, as for `pm_prereqs_tell x Start_Deferred_PM`: "
        "`Start_Deferred_PM` requires `run_state.defer_pm`, and a deferred block "
        "returns from `ObtainPMprereqs` before the hand-back hop. Measured "
        "over the same phase schedules: the release lands at `fail_wait`."),
    ("pm_handback", "Switched_Off"): _PM_GATE_SWITCH,
    ("pm_handback", "Switched_On"): _PM_GATE_SWITCH,
    ("pm_handback", "Triggered_PM_Due"): (
        "Delivery excluded, as for `pm_prereqs_tell x Triggered_PM_Due`: "
        "`waiting_for_pm_prerequisites` is set across the hand-back hop too "
        "(`block_lifecycle.py:2683`), so `ControlPMtriggers` is not "
        "eligible."),

    ("perform_pm_tell", "Dependence_All_Up"): _PM_TELL_GATE,
    ("perform_pm_tell", "Dependence_Idle"): _PM_TELL_GATE,
    ("perform_pm_tell", "Dependence_SB_turnOn"): _PM_TELL_GATE,
    ("perform_pm_tell", "Dependence_Standby"): _PM_TELL_GATE,
    ("perform_pm_tell", "Switched_Off"): _PM_TELL_GATE_SWITCH,
    ("perform_pm_tell", "Switched_On"): _PM_TELL_GATE_SWITCH,
    ("perform_pm_tell", "Triggered_PM_Due"): (
        "Delivery excluded, as for `pm_prereqs_tell x Triggered_PM_Due`: "
        "`waiting_for_pm_prerequisites` is set across the `perform_pm_tell` hop "
        "(`block_lifecycle.py:2997`), so `ControlPMtriggers` is not "
        "eligible."),

    # --- fail_wait: the two reasons the construction above isolates -------
    ("fail_wait", "Dependence_All_Up"): (
        "Constructed -- see the reachable rows. A downstream block whose "
        "repair is `Fixed(0.0)` fails and recovers inside one instant, so "
        "both `Dependence_Idle` and `Dependence_All_Up` are queued for the "
        "dependent at once and `Scheduler.interrupt` rewrites the pending "
        "entry's reason to the second."),
    ("fail_wait", "Dependence_SB_turnOn"): (
        "Constructed -- see the reachable rows: a cold-standby peer promoted "
        "out of Standby dispatches `Dependence_SB_turnOn` to a block "
        "depending on it, landing in whatever wait that block is parked at."),

    # --- beyond_horizon x Switched_Off ------------------------------------
    ("beyond_horizon", "Switched_Off"): (
        "Delivery gate, and the gate is structural rather than incidental. "
        "`Switched_Off` has exactly one sender: `run_cold_logic`'s too-many-on "
        "branch (`cold_standby.py:673`), which fires it at the block it has "
        "just demoted with `Too_many_on` -- that is, the block that has LOST "
        "its cold slot. `_switch_interrupt` refuses unless the target is in "
        "`FailWait` or `ZeroWait` (`cascade_cold.py:278`) and, first, refuses "
        "outright when the target handle is the one currently executing "
        "(`cascade_cold.py:270-272`). The demoted block is always the "
        "currently-executing one, and always is because of `reorder_colds` "
        "(`cold_standby.py:181-186`): when a cold link goes Up->Down/PM/Done "
        "it is moved to the BACK of `node.cold_links`, and `run_cold_logic`'s "
        "enough-paths pass keeps the FIRST `runs_required` UP links in that "
        "order (`cold_standby.py:610-624`). So the block whose link most "
        "recently came back UP -- the block whose own repair-completion "
        "cascade is running the survey -- is always last in the order and "
        "therefore always the one demoted. Every survey the port runs at "
        "SimTime>0 is reached through `_Cascade.apply_cold_logic` from a "
        "block transition (`cascade_cold.py:23-59`), so the executing handle "
        "is the trigger's, never another block's. Measured over a sweep of "
        "165 cold shapes -- two to four peers, `k` and `k_star` in "
        "{1,2}, eight cold-priority orderings, lives mixing ordinary and "
        "`Fixed(2e9)`, repairs 5/10/25 h -- of 490 `Switched_Off` attempts, "
        "490 named the currently-executing handle and 0 named a block "
        "parked in a wait. The cell would need a demotion driven by "
        "something other than the demoted block's own turn, which no "
        "`reconfigure_node` / `CheckColds` path the port has produces."),
}


# --------------------------------------------------------------------------
# Running the probes
# --------------------------------------------------------------------------

def run_probe(probe_spec: dict) -> tuple[set[Cell], str]:
    """Build, run and observe one probe.

    Returns the cells it reached and a note if the run raised. The observer
    is `state_coverage._Recorder`, the same one the corpus scan installs, so
    "what a cell was reached" means the same thing here as in the page.
    """
    from tests.behaviour.harness import run as harness_run

    recorder = sc._Recorder()
    try:
        with observing(recorder):
            harness_run(copy.deepcopy(probe_spec))
    except Exception as exc:  # a probe that cannot run is evidence too
        return set(), f"{type(exc).__name__}: {exc}"
    return set(recorder.wakes), ""


def measured() -> dict[str, Probe]:
    """Every probe with its *measured* cell list, replacing the claimed one.

    This is the tool's whole point: the committed manifest is a record of
    what was observed, and `--check` re-observes it.
    """
    out: dict[str, Probe] = {}
    for probe in probes():
        cells, note = run_probe(probe.spec)
        out[probe.name] = Probe(probe.name, probe.spec, tuple(sorted(cells)),
                                note or probe.reason,
                                claims=probe.claims)
    return out


# --------------------------------------------------------------------------
# The classification
# --------------------------------------------------------------------------

CLASSES = ("reachable", "already_pinned", "unreachable")
REASON_REQUIRED = ("unreachable",)


#: A per-cell note for every cell a probe MEASURED, because the probe's own
#: reason describes the model, not the one cell it was picked for. A cell
#: with no entry here falls back to its witness probe's reason.
CELL_NOTES: dict[Cell, str] = {
    ("kickoff", TIMER): (
        "Every model reaches it: the kickoff barrier is a zero-length wait "
        "before the block's first stress computation, so it runs out "
        "immediately. The S1 spine alone is witness."),
    ("fail_wait", TIMER): (
        "Every ordinary life ends here: `WAIT DURATION TimeToMaint` runs out "
        "when the life expires (`block_lifecycle.py:2104`)."),
    ("repair", TIMER): (
        "Every ordinary repair ends here: `WAIT DURATION TimeToRepair` runs "
        "out when the repair duration elapses "
        "(`block_lifecycle.py:3632`)."),
    ("repair_resource", "Pool_Available"): (
        "A block whose repair needs a resource parks in `RepHold` and is "
        "granted the unit when a sibling hands it back -- the S3 spine's own "
        "mechanism."),
    ("pm_prereqs_tell", TIMER): (
        "A PM-using block with no prerequisites pays the TELL hop and nothing "
        "interrupts it."),
    ("pm_handback", TIMER): (
        "A PM borrowing from a one-unit resource pool pays the hand-back hop "
        "and nothing interrupts it."),
    ("perform_pm_tell", TIMER): (
        "A Custom-spare PM skips the prereq boundary and pays "
        "`_perform_pm`'s own TELL hop with nothing interrupting it."),
    ("pm_sync_wait_handback", "Trigger_Fired"): (
        "A `pmMisDefer` PM borrowing a resource, with a phase change in "
        "progress at the hand-back, parks on `pmSyncTrigger`; the phase "
        "change's end releases it with `Trigger_Fired`. The page annotated "
        "this site `_nothing reaches it_` -- that annotation was wrong."),
    ("beyond_horizon", "Dependence_Idle"): (
        "A block parked past `_FAR_FUTURE` plus a sibling that fails "
        "downstream of the parked block's `sysDepend` on the End node."),
    ("beyond_horizon", "Dependence_All_Up"): (
        "Same shape, with the node flapping Bad and Good inside one instant "
        "(k=2 of 3, two siblings failing with `Fixed(0.0)` repairs) so both "
        "dependency reasons are queued and the second overwrites the first."),
    ("beyond_horizon", "Switched_On"): (
        "The low-priority peer of a 1-of-2 cold node is promoted when the "
        "high-priority one fails, and that peer's own beyond-horizon wait "
        "takes the `Switched_On`."),
    ("beyond_horizon", "Dependence_SB_turnOn"): (
        "The same cold-standby promotion, read through a dependent: "
        "`_dispatch_dependency`'s `was_standby` branch rewrites it to "
        "`Dependence_SB_turnOn` (`cascade_dependency.py:74`)."),
    ("beyond_horizon", "Phase_Change"): (
        "A phased block parked past `_FAR_FUTURE`: the phase boundary's "
        "`ChangePhases` tail interrupts whichever wait the block is in "
        "(`cascade_phase.py:158`)."),
    ("beyond_horizon", "Triggered_PM_Due"): (
        "A `pmTriggered` block parked past `_FAR_FUTURE` whose PM trigger "
        "fires mid-wait (`kickoff.py:1003`)."),
    ("beyond_horizon", "Start_Deferred_PM"): (
        "A `pmMisDefer` block parked past `_FAR_FUTURE` while a mission "
        "phase is active: the phase change back to NonMission releases every "
        "deferred block (`cascade_phase.py:295`)."),
    ("beyond_horizon", "Pool_Available"): (
        "A `pmReqDefer` block parked past `_FAR_FUTURE` whose abandoned PM "
        "resource request is granted when a sibling's repair hands the unit "
        "back."),
    ("beyond_horizon", "Dependence_Standby"): (
        "A dependent with a `Fixed(2e9)` life, block-dependent on the "
        "cold-standby peer that the survey demotes. The dependent is a THIRD "
        "block, so the `Scheduler.is_executing` guard on the switch "
        "interrupt does not apply to it, and `_dispatch_dependency` "
        "interrupts whatever wait it is parked at."),
    ("beyond_horizon", TIMER): (
        "The wait's full length is `run_length - SimTime` against the "
        "BLOCK's horizon, and `BlockEnv` is built with `block_horizon`, not "
        "the model's `run_length` (`kickoff.py:480-485`): that horizon is "
        "`_FAR_FUTURE` for every termination type, so the wait ends at "
        "t=1e9 h however long the model's own run is. Reaching the cell "
        "therefore needs a run LONGER than 1e9 h, or the model's "
        "`EndSimulation` pops first and the wait is simply abandoned -- "
        "which is why every shorter probe, `Fixed(2e9)` life and all, "
        "records nothing at this site at all."),
    ("fail_wait", "Dependence_All_Up"): (
        "A dependent whose target's node flaps Bad and Good inside one "
        "instant (k=2 of 3, two `Fixed(0.0)`-repair siblings), so both "
        "dependency reasons are queued for the same handle and the second "
        "rewrites the first."),
    ("fail_wait", "Dependence_SB_turnOn"): (
        "A dependent that is itself in Standby when its cold-standby peer is "
        "promoted: `was_standby` rewrites the reason "
        "(`cascade_dependency.py:74`)."),
    ("perform_pm_tell", "Phase_Change"): (
        "A phase boundary at exactly the PM-due instant. `apply_block` runs "
        "five zero-length hops after the phase clock's own entry, which lands "
        "between the block's `pm_prereqs_tell` and `perform_pm_tell` hops."),
    ("pm_handback", "Phase_Change"): (
        "The same boundary with a resource-pool PM, so the hand-back is the "
        "last hop before `PerformPM` and the phase change lands there."),
    ("pm_misdefer_run", TIMER): (
        "A `pmMisDefer` block whose PM comes due inside a mission phase pays "
        "the `ELSE Run` hop instead of continuing to `PerformPM` "
        "(`block_lifecycle.py:2244`)."),
    ("pm_scheduled_hop", TIMER): (
        "The same `pmMisDefer` scheduled-PM arm: a `WAIT DURATION 0.0` "
        "before `DetermineNewState(\"PM is due\")` "
        "(`block_lifecycle.py:2190`)."),
    ("pm_tail_hop", TIMER): (
        "A `pmMisDefer` PM with no resource to wait for pays the "
        "pmMisDefer tail's own `WAIT DURATION 0.0` "
        "(`block_lifecycle.py:2323`)."),
    ("repair", "Pool_Available"): (
        "The engine carries a dedicated absorb loop for exactly this wake "
        "(`_repair_wait`, `block_lifecycle.py:3626-3660`: it resumes on a "
        "`Pool_Available`, reconciles the stale grant and continues the "
        "repair), so the site is written to receive it -- but see the "
        "`observation` field: this tool could not construct the precondition."),
}


@dataclass
class Entry:
    """One classified cell."""

    cell: Cell
    klass: str
    reason: str
    probe: str = ""
    """The probe that MEASURED this cell, when one did."""
    evidence: str = ""
    """Where a reachability claim is backed when no probe measured it:
    `corpus:<n>` for a row of the coverage matrix."""
    attempted: str = ""
    """A probe that was built and run for this cell and did NOT reach it.
    Kept because "we tried this and the engine did X" is what a reviewer
    will accept where a bare claim would not be."""
    observation: str = ""
    """What the attempted probe actually recorded."""
    pinned_by: str = ""
    problems: list[str] = field(default_factory=list)


def classify(results: dict[str, Probe],
             pinned: dict[Cell, str] | None = None) -> dict[Cell, Entry]:
    """Every cell, classified. The manifest is this function's output.

    Two kinds of reachability evidence, both *measured* rather than claimed:

    * a probe in this file that reaches the cell, and
    * a row of `state-coverage.adoc` with a non-zero model count -- the
      corpus IS a set of constructible models, so a cell it reaches is
      reachable by definition; this tool's job is the 54 it does not.

    A cell with neither is `unreachable`, and needs a reason. A cell with
    only an ATTEMPTED probe is `reachable` with the attempt and its
    observation recorded, because a dedicated handler in the engine is
    evidence of intent and "we tried this and the engine did X" is what a
    reviewer will accept where a bare claim would not be.
    """
    pinned = PINNED_BY_SUITE if pinned is None else pinned
    corpus = corpus_cells()
    return {cell: _classify_one(cell, results, pinned, corpus)
            for cell in all_cells()}


#: The note for a cell the snapshot corpus already reaches. One constant
#: because a per-cell copy would be 60 copies of one paragraph.
_CORPUS_NOTE = (
    "Reached by the snapshot corpus; `state-coverage.adoc` has "
    "the model count. Reachability is not in question for a cell "
    "a shipped fixture already exercises -- what is in question "
    "is whether the behaviour suite pins it yet.")


def _classify_one(cell: Cell, results: dict[str, Probe],
                  pinned: dict[Cell, str],
                  corpus: dict[Cell, int]) -> Entry:
    """One cell's classification.

    Split out of `classify` so the order the evidence is weighed in reads as
    a list: a probe that reached it, then the corpus, then a probe that was
    BUILT for it and missed, then an argument. Every branch ends in an
    `Entry`, so a cell cannot fall out of the classification unsaid.
    """
    reached = sorted(name for name, probe in results.items()
                     if cell in probe.cells)
    if reached or cell in pinned:
        # A probe that reaches this CELL, never one that merely reaches the
        # same SITE: citing a probe that did not measure the cell makes the
        # claim look backed when nothing measured it, and `--check` re-runs
        # the named probe, so it fails rather than misleads. A cell no probe
        # here reaches is backed by the corpus count and the pinned fixture.
        witness = reached[0] if reached else ""
        if not witness and not corpus.get(cell):
            witness = _site_witness(results, cell[0]) or ""
        note = (CELL_NOTES.get(cell)
                or (results[witness].reason if witness in results else ""))
        return Entry(cell, "already_pinned" if cell in pinned else "reachable",
                     note, probe=witness,
                     evidence=(f"corpus:{corpus[cell]} model(s)"
                               if corpus.get(cell) else ""),
                     pinned_by=pinned.get(cell, ""))
    if corpus.get(cell):
        return Entry(cell, "reachable", _CORPUS_NOTE,
                     evidence=f"corpus:{corpus[cell]} model(s)")
    attempted = sorted(name for name, probe in results.items()
                       if cell in probe.claims)
    if attempted:
        return Entry(cell, "reachable",
                     CELL_NOTES.get(cell) or UNREACHABLE.get(cell, ""),
                     attempted=attempted[0],
                     observation=render_cells(results[attempted[0]].cells)
                     or "(the model recorded no wait-site wake at all)")
    if cell == PLACEHOLDER:
        return _placeholder_entry(cell, results)
    return Entry(cell, "unreachable", UNREACHABLE.get(cell, ""))


def _placeholder_entry(cell: Cell, results: dict[str, Probe]) -> Entry:
    """The page's `_nothing reaches it_` row.

    Not a cell: it stands for "this site's policy permits neither a timer
    nor any reason and no model has reached it yet", so there is no `Ends by`
    value to print. Once a probe reaches the SITE, the placeholder is
    answered, and the honest class is `reachable` with the probe that
    answers it -- not `unreachable`, which would be an exemption for
    something that is not a state at all.
    """
    witness = _site_witness(results, cell[0])
    return Entry(
        cell, "reachable" if witness else "unreachable",
        "A placeholder row, not a `WaitSite x reason` cell. "
        "`state_coverage.render` prints it when a site's policy "
        "permits neither a timer nor any reason and no model has "
        "reached it -- here because "
        "`WAKE_POLICY.PM_SYNC_WAIT_HANDBACK` is `timer=False, "
        "reasons=None`, so the page has no `Ends by` value to print. "
        + ("The SITE is reachable: the named probe drives it to "
           "`Trigger_Fired`, and that is the same site's real cell."
           if witness else
           "The site is not reachable by any model this tool could "
           "build."),
        probe=witness or "")


def render_cells(cells) -> str:
    """A cell set as readable text, for an `observation` field."""
    return ", ".join(f"{site} x {reason}" for site, reason in sorted(cells))


def _site_witness(results: dict[str, Probe], site: str) -> str | None:
    """The probe that reaches *site* at all, for a placeholder row."""
    names = sorted(name for name, probe in results.items()
                   if any(cell[0] == site for cell in probe.cells))
    return names[0] if names else None


# --------------------------------------------------------------------------
# The manifest
# --------------------------------------------------------------------------

_HEADER = """\
# The `WaitSite x InterruptReason` reachability classification.
#
# Read by tests/behaviour/test_b_state_coverage.py and regenerated by
# tools/state_reachability.py --write. It answers the question
# the 54 unreached cells leave open: how many of them can a well-formed model actually reach?
#
# All 113 cells of docs/engine-design/state-coverage.adoc are listed,
# exactly once, with exactly one class:
#
#   reachable       a constructible model drives a block into that
#                   `(site, reason)` state. `probe` names the model that
#                   does it -- a `build_from_spec` spec in
#                   tools/state_reachability.py, runnable
#                   verbatim by whoever turns it into a test -- and `reason`
#                   says what shape of model it is. Every one of these was
#                   MEASURED, not argued: the tool builds the model, runs it
#                   through the same `state_coverage` observer the 283-model
#                   corpus scan uses, and records the cell.
#   already_pinned  reachable AND already reached by a fixture committed in
#                   tests/behaviour/ (`pinned_by` names it). The ladder's
#                   rungs are run under the same observer the corpus scan
#                   uses, and so is every `WAIT_SITE_FIXTURES` entry the
#                   suite declares -- the set is measured, never read out of
#                   this file. Each of those fixtures asserts the
#                   `(block, wait site, reason)` triple the observer
#                   recorded AND the model's complete event-log timeline, so
#                   a pinned cell is inside an asserted literal either way.
#   unreachable     needs an argument, and `reason` is the argument. Only
#                   two kinds are recorded, because only two kinds survive
#                   review:
#                     * no sender -- nothing in raptor/ ever sends the
#                       reason, so the cell exists only because
#                       wait_sites.WAKE_POLICY lists it. Those are marked
#                       ENGINE FINDING and are reported, not tested.
#                     * delivery excluded -- the sender exists and fires,
#                       but a gate in the dispatch path rules this site
#                       out unconditionally.
#                   "I could not build one" is NOT a reason and does not
#                   appear.
#
# Every unclassified cell fails the gate, and so does every `unreachable`
# cell with an empty reason: a cell that is neither reached nor argued is a
# failure, so the exemption list cannot quietly grow.
#
# HOW TO PROMOTE AN ENTRY: write the test that pins the cell, then change
# `class = "reachable"` to `class = "already_pinned"` and fill in
# `pinned_by`. The gate re-runs every probe on each run and fails if the
# manifest claims a cell the model no longer reaches, so the two changes
# have to be the same commit.
"""

#: How `[ratchet]` is written. Generated, never typed into the header: a
#: baseline written by hand is a baseline that can disagree with the file it
#: heads, and `--write` would then bake the disagreement in.
_RATCHET_NOTE = """
[ratchet]
# Cells classified below. May only rise; falling below it means a
# classification was deleted rather than a cell going away.
classified = {classified}
# Cells reached by a fixture committed in tests/behaviour/ -- the ladder's
# rungs and the suite's own wait-site fixtures, run under the same observer
# the corpus scan uses. May only rise.
pinned = {pinned}
"""


def render(entries: dict[Cell, Entry]) -> str:
    """The manifest as TOML, cell by cell."""
    counts: dict[str, int] = {name: 0 for name in CLASSES}
    for entry in entries.values():
        counts[entry.klass] += 1
    lines = [_HEADER, _RATCHET_NOTE.format(
        classified=len(entries), pinned=counts["already_pinned"])]
    by_site: dict[str, list[str]] = {}
    for site, reason in entries:
        by_site.setdefault(site, []).append(reason)
    for site in WaitSite:
        reasons = sorted(by_site.get(str(site), []))
        if not reasons:
            continue
        lines.append(f"\n# --- {site} ---")
        for reason in reasons:
            lines += _cell_lines(entries[(str(site), reason)])
    total = sum(counts.values())
    lines.append("")
    lines.append(f"# Totals: {total} = " + " + ".join(
        f"{counts[name]} {name}" for name in CLASSES) + ".")
    lines.append("")
    return "\n".join(lines)


def _cell_lines(entry: Entry) -> list[str]:
    """One `[[cell]]` block: the keys that apply, in a fixed order."""
    lines = [
        "",
        "[[cell]]",
        f'site = "{entry.cell[0]}"',
        f'reason = "{entry.cell[1]}"',
        f'class = "{entry.klass}"',
    ]
    for key, value in (("probe", entry.probe), ("evidence", entry.evidence),
                       ("attempted", entry.attempted)):
        if value:
            lines.append(f'{key} = "{value}"')
    if entry.observation:
        lines += ['observation = """', _wrap(entry.observation), '"""']
    if entry.pinned_by:
        lines.append(f'pinned_by = "{entry.pinned_by}"')
    if entry.reason:
        lines += ['note = """', _wrap(entry.reason), '"""']
    return lines


def _wrap(text: str, width: int = 68) -> str:
    """The note text, wrapped for a TOML multi-line string."""
    import textwrap
    return "\n".join(
        "  " + line
        for para in text.split("\n\n")
        for line in textwrap.wrap(" ".join(para.split()), width=width))


def write(entries: dict[Cell, Entry]) -> None:
    MANIFEST.write_text(render(entries), encoding="utf-8")


def load(path: Path = MANIFEST) -> tuple[dict[Cell, Entry], dict[str, int],
                                          list[str]]:
    """Parse the manifest. Malformed rows become problems, not exceptions."""
    import tomllib

    problems: list[str] = []
    entries: dict[Cell, Entry] = {}
    ratchet: dict[str, int] = {}
    try:
        raw = tomllib.loads(path.read_text(encoding="utf-8"))
    except (OSError, tomllib.TOMLDecodeError) as exc:
        return {}, {}, [f"{path.name}: cannot be read as TOML: {exc}"]
    for key, value in (raw.get("ratchet") or {}).items():
        if not isinstance(value, int) or isinstance(value, bool):
            problems.append(f"{path.name}: [ratchet].{key} = {value!r} is "
                            f"not an integer")
        else:
            ratchet[key] = value
    for index, row in enumerate(raw.get("cell") or ()):
        where = f"{path.name}: [[cell]] #{index + 1}"
        if not isinstance(row, dict):
            problems.append(f"{where}: not a table")
            continue
        missing = {"site", "reason", "class"} - set(row)
        if missing:
            problems.append(f"{where}: missing key(s) {sorted(missing)}")
            continue
        cell = (str(row["site"]), str(row["reason"]))
        if row["class"] not in CLASSES:
            problems.append(f"{where}: class = {row['class']!r} is not one "
                            f"of {list(CLASSES)}")
            continue
        if cell in entries:
            problems.append(f"{where}: {cell} is already classified")
            continue
        entries[cell] = Entry(cell, str(row["class"]), str(row.get("note", "")),
                              probe=str(row.get("probe", "")),
                              evidence=str(row.get("evidence", "")),
                              attempted=str(row.get("attempted", "")),
                              observation=str(row.get("observation", "")),
                              pinned_by=str(row.get("pinned_by", "")))
    return entries, ratchet, problems


# --------------------------------------------------------------------------
# Comparing against the manifest
# --------------------------------------------------------------------------

def compare(entries: dict[Cell, Entry], ratchet: dict[str, int],
            results: dict[str, Probe],
            problems: list[str]) -> list[str]:
    """The gate's own verdict, as a list of problem strings."""
    problems = list(problems)
    corpus_counts = corpus_cells()
    measured_cells: dict[Cell, str] = {}
    for name, probe in results.items():
        for cell in probe.cells:
            measured_cells.setdefault(cell, name)

    problems += _matrix_problems(set(all_cells()), set(entries))
    for cell in sorted(set(entries) & set(all_cells())):
        problems += _entry_problems(cell, entries[cell], results,
                                    corpus_counts, measured_cells)
    problems += _ratchet_problems(entries, ratchet)
    return problems


def _matrix_problems(corpus: set[Cell], classified: set[Cell]) -> list[str]:
    """Both directions between the coverage page and the manifest."""
    problems = []
    for cell in sorted(corpus - classified):
        problems.append(f"  [unclassified] {cell} is in the coverage matrix "
                        f"but not in the manifest")
    for cell in sorted(classified - corpus):
        problems.append(f"  [stale] {cell} is classified but is no longer a "
                        f"row of state-coverage.adoc")
    return problems


def _entry_problems(cell: Cell, entry: Entry, results: dict[str, Probe],
                    corpus_counts: dict[Cell, int],
                    measured_cells: dict[Cell, str]) -> list[str]:
    """One classified cell against the evidence that is supposed to back it."""
    problems: list[str] = []
    if entry.klass in REASON_REQUIRED and not entry.reason.strip():
        problems.append(f"  [no reason] {cell} is {entry.klass!r} with an "
                        f"empty note -- an unargued exemption is the thing "
                        f"this gate exists to prevent")
    if entry.klass in ("reachable", "already_pinned"):
        problems += _claim_problems(cell, entry, results, corpus_counts)
    if (entry.klass == "unreachable"
            and cell in measured_cells):
        problems.append(
            f"  [contradicted] {cell} is classified 'unreachable' but "
            f"probe {measured_cells[cell]!r} reaches it")
    return problems


def _claim_problems(cell: Cell, entry: Entry, results: dict[str, Probe],
                    corpus_counts: dict[Cell, int]) -> list[str]:
    """A reachability claim has to point at something that measured it."""
    problems: list[str] = []
    if not (entry.probe or entry.evidence or entry.attempted
            or entry.pinned_by):
        problems.append(
            f"  [unbacked claim] {cell} is {entry.klass!r} but names "
            f"no probe, no corpus evidence, no attempted probe and no "
            f"committed fixture -- a reachability claim has to point "
            f"at something that measured it, or at a measurement that "
            f"already did")
    if entry.probe:
        problems += _probe_problems(cell, entry.probe, results)
    if entry.evidence:
        problems += _evidence_problems(cell, entry.evidence, corpus_counts)
    if entry.klass == "already_pinned" and not entry.pinned_by:
        problems.append(
            f"  [no fixture] {cell} is 'already_pinned' but names no "
            f"committed fixture in tests/behaviour/ -- a pinned claim "
            f"has to point at the test that pins it")
    if entry.attempted:
        problems += _attempt_problems(cell, entry, results)
    return problems


def _probe_problems(cell: Cell, probe: str,
                    results: dict[str, Probe]) -> list[str]:
    """The named probe must exist, and must still reach what it is cited for."""
    if probe not in results:
        return [f"  [unknown probe] {cell} names {probe!r}, which is not in "
                f"state_reachability.probes()"]
    if cell == PLACEHOLDER:
        # The placeholder is answered by the SITE being reached, not by that
        # exact pseudo-reason.
        if any(c[0] == cell[0] for c in results[probe].cells):
            return []
        return [f"  [unbacked claim] the placeholder {cell} is "
                f"{cell!r} on the authority of probe {probe!r}, but "
                f"re-running it does not reach the site at all"]
    if cell in set(results[probe].cells):
        return []
    return [f"  [unbacked claim] {cell} is claimed on the authority of "
            f"probe {probe!r}, but re-running it does not reach the cell "
            f"-- the model changed, or never did"]


def _evidence_problems(cell: Cell, evidence: str,
                       corpus_counts: dict[Cell, int]) -> list[str]:
    """Corpus evidence must be a count read out of the coverage page."""
    if not evidence.startswith("corpus:"):
        return [f"  [bad evidence] {cell} claims {evidence!r}; the only "
                f"accepted form is 'corpus:<n>', read from the coverage "
                f"matrix"]
    if corpus_counts.get(cell):
        return []
    return [f"  [stale evidence] {cell} claims corpus evidence but "
            f"state-coverage.adoc records no model reaching it"]


def _attempt_problems(cell: Cell, entry: Entry,
                      results: dict[str, Probe]) -> list[str]:
    """'We tried this and the engine did X' needs the X, and a real probe."""
    problems: list[str] = []
    if not entry.observation:
        problems.append(
            f"  [no observation] {cell} names attempted probe "
            f"{entry.attempted!r} but records nothing about what it "
            f"did -- 'we tried this and the engine did X' needs the X")
    if entry.attempted not in results:
        problems.append(f"  [unknown probe] {cell} names attempted probe "
                        f"{entry.attempted!r}, which is not in "
                        f"state_reachability.probes()")
    return problems


def _ratchet_problems(entries: dict[Cell, Entry],
                      ratchet: dict[str, int]) -> list[str]:
    """Both baselines. They may only rise."""
    problems: list[str] = []
    classified = len(entries)
    baseline = ratchet.get("classified")
    if baseline is None:
        problems.append("  [no baseline] the manifest has no "
                        "[ratchet].classified")
    elif classified < baseline:
        problems.append(f"  [regression] {classified} cells classified, was "
                        f"{baseline}. A classification may only be added, "
                        f"never deleted to make a run green.")
    pinned = sum(1 for e in entries.values() if e.klass == "already_pinned")
    baseline = ratchet.get("pinned")
    if baseline is None:
        problems.append("  [no baseline] the manifest has no [ratchet].pinned")
    elif pinned < baseline:
        problems.append(
            f"  [regression] {pinned} cells are reached by a committed "
            f"fixture in tests/behaviour/, was {baseline}. Wait-site coverage "
            f"went backwards: either an 'already_pinned' entry was "
            f"reclassified or the fixture that reached it was deleted. "
            f"`git diff HEAD~ -- tests/behaviour/` names which -- a ratchet "
            f"that cannot be lowered is the whole mechanism.")
    return problems


def summary(entries: dict[Cell, Entry], ratchet: dict[str, int],
            results: dict[str, Probe]) -> str:
    """The progress line: a denominator and a numerator."""
    counts = {name: sum(1 for e in entries.values() if e.klass == name)
              for name in CLASSES}
    findings = sum(1 for e in entries.values()
                   if e.klass == "unreachable"
                   and "ENGINE FINDING" in e.reason)
    live = counts["reachable"] + counts["already_pinned"]
    rows = [
        "WaitSite x InterruptReason reachability",
        f"  matrix: {len(all_cells())} cells",
        f"  classified: {len(entries)} = " + " + ".join(
            f"{counts[name]} {name}" for name in CLASSES),
        f"  reachable cells a constructible model drives: {live} "
        f"(ratchet baseline for `classified`: {ratchet.get('classified', 0)})",
        f"  of the unreachables, {findings} are ENGINE FINDINGS "
        f"(the policy permits a reason the engine never sends)",
        f"  probes: {len(results)} models, all `Fixed`, all measured",
    ]
    return "\n".join(rows)


def main() -> int:
    ap = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter)
    mode = ap.add_mutually_exclusive_group()
    mode.add_argument("--write", action="store_true",
                      help="regenerate the manifest from the probes")
    mode.add_argument("--check", action="store_true",
                      help="fail if the manifest disagrees with the probes")
    a = ap.parse_args()

    results = measured()
    if a.write:
        write(classify(results))
        print(f"wrote {MANIFEST.relative_to(REPO)}")
        return 0

    entries, ratchet, problems = load()
    if a.check:
        problems = compare(entries, ratchet, results, problems)
        print(summary(entries, ratchet, results))
        for problem in problems:
            print(problem)
        return 1 if problems else 0

    print(summary(entries, ratchet, results))
    for problem in compare(entries, ratchet, results, problems):
        print(problem)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())