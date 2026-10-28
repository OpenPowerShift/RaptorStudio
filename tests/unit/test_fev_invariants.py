"""Failure Effects View invariants, over every golden model.

* it starts in the all-operating state, system Green (a phased model starts in
  phase 1, whose Cut elements can already take the system out of Green);
* a failure then "Refresh All" returns EXACTLY to that state (every block, event,
  node, link, hierarchy, the counts, the hatching, the criticality);
* no random number is drawn and nothing is scheduled (the null scheduler
  raises if anything tries);
* clicking a failed block restores it, and two clicks on an operating block with
  no dependents are a fail then a restore.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

from raptor.core.engine.errors import UnrunnableModel
from raptor.core.engine.fev import FevSession
from raptor.core.engine.system_status import SystemColor
from raptor.core.fileio.rbd import load_rbd

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO / "tools"))
import golden_fixtures as gf  # noqa: E402

NAMES = sorted(n for n in gf.all_fixture_names() if gf.resolve_rbd_path(n))


def _streams(s):
    ctx = s.ctx
    seen = {}
    for coll in (ctx.sys_streams, ctx.rng_fail_by_block,
                 ctx.rng_repair_by_block, ctx.rng_by_event):
        for g in coll.values():
            seen[id(g)] = g
    return [g.stream._x for g in seen.values()]


@pytest.mark.parametrize("name", NAMES)
def test_every_model_round_trips_and_draws_nothing(name):
    rbd = load_rbd(str(gf.resolve_rbd_path(name)))
    try:
        s = FevSession(rbd)
    except UnrunnableModel as exc:
        assert any(w in str(exc) for w in (
            "cold-standby", "phased", "dependency", "circular", "cannot",
            "no ", "Real Raptor", "capacity", "run is terminated")), str(exc)
        return
    draws_before = _streams(s)
    initial = s.snapshot()
    if not s.phased:
        assert s.system_color == SystemColor.GREEN
        assert all(v[0].value in ("Running", "Standby")
                   for v in initial["blocks"].values())
        assert initial["hatched"] == ()

    for b in s.blocks:
        s.click_block(b.id)
        s.refresh_all()
        assert s.snapshot() == initial, f"{name}: {b.name} did not round trip"
    for e in list(s.model.events_by_id.values()):
        s.click_event(e.id)
        s.refresh_all()
        assert s.snapshot() == initial, f"{name}: event {e.name}"
    if s.phased:
        n = len(s.model.phases)
        for _ in range(n):
            s.next_phase()
        assert s.phase_number == 1, f"{name}: a full lap of phases did not return"
        for _ in range(n):
            s.previous_phase()
        assert s.phase_number == 1
        for target in range(1, n + 1):
            s.goto_phase(target)
            s.refresh_phase()
            assert s.phase_number == target
        s.refresh_all()
        assert s.snapshot() == initial, f"{name}: phase walk did not round trip"
    assert _streams(s) == draws_before, f"{name}: FEV drew a random number"


def test_a_failed_block_is_restored_by_a_second_click():
    rbd = load_rbd(str(REPO / "tests/golden/TwoBlockRedundancy/input.rbd"))
    s = FevSession(rbd)
    a = s.blocks[0]
    s.click_block(a.id)
    assert a.op_status.value == "Repairing"
    s.click_block(a.id)
    assert a.op_status.value == "Running"
    assert s.system_color == SystemColor.GREEN


@pytest.mark.parametrize("termination", ["FailSim", "CycleSim", "TimeSim"])
@pytest.mark.parametrize("name", [n for n in NAMES if n.startswith(
    ("TwoBlock", "MotorDemo", "SPFProbe", "Cascade", "FevHierarchy", "FevPhasing3", "FactoryDemo",
     "PhasingDemo", "ColdStandby", "LadL"))][:40])
def test_fev_never_terminates_whatever_the_models_termination(name, termination):
    """Reported 2026-10-10: restoring a block on a FailSim model raised "Failure
    Effects View has no scheduler" (the run's failure counter tried to end the
    run). FEV has no termination (`NOT FEVmode`)."""
    rbd = load_rbd(str(gf.resolve_rbd_path(name)))
    sec = rbd.sections["SYSTEM_DATA"].rows[0]
    sec["TerminationType"] = termination
    sec["TermCondition"] = "1.000000"
    try:
        s = FevSession(rbd)
    except UnrunnableModel:
        return
    for b in s.blocks:
        s.click_block(b.id)
        s.click_block(b.id)          # fail then restore: the system comes back up
        s.refresh_all()
    for e in list(s.model.events_by_id.values()):
        s.click_event(e.id)
        s.click_event(e.id)
