"""The State_change / Red_change comments walk a registry, not every block.

Both comments name only blocks that are cut or repairing/held/done/in
maintenance. They were found by walking every block on every status change,
which on a 3,470-block model was a fifth of a replication (0.75 ms a call).
`blocks.note_block_flags` now keeps a registry of such blocks and the comments
walk just that, in block order. The text must not change.
"""
from __future__ import annotations

import glob
import os
import pathlib
from types import SimpleNamespace

import pytest

from raptor.core.engine import orchestrator
from raptor.core.engine.blocks import note_block_flags
from raptor.core.engine.runner import run_simulation
from raptor.core.engine.system_status import (
    MissionStatus, SystemColor, SystemState, change_system_status, red_change_line,
)
from raptor.core.fileio.rbd import load_rbd
from raptor.core.model import ActiveStatus, BlockStatus

GOLD = pathlib.Path(__file__).resolve().parents[1] / "golden"
FIXTURES = sorted(os.path.basename(os.path.dirname(p))
                  for p in glob.glob(str(GOLD / "*" / "input.rbd")))


def _block(i, op=BlockStatus.RUNNING, active=ActiveStatus.ACTIVE, sink=None):
    return SimpleNamespace(id=i, op_status=op, active_status=active,
                           flag_sink=sink, order=i)


# --- the registry ----------------------------------------------------------

@pytest.mark.parametrize("op", [BlockStatus.REPAIRING, BlockStatus.REP_HOLD,
                                BlockStatus.DONE, BlockStatus.PM_HOLD,
                                BlockStatus.PM])
def test_a_repair_hold_done_or_maintenance_state_is_registered(op):
    sink = {}
    b = _block(1, op, sink=sink)
    note_block_flags(b)
    assert sink == {1: b}


def test_a_cut_block_is_registered_whatever_its_op_status():
    sink = {}
    b = _block(1, BlockStatus.RUNNING, ActiveStatus.CUT, sink)
    note_block_flags(b)
    assert 1 in sink


@pytest.mark.parametrize("op", [BlockStatus.RUNNING, BlockStatus.IDLE,
                                BlockStatus.STANDBY])
def test_an_ordinary_state_is_not_registered_and_clears_an_old_entry(op):
    sink = {}
    b = _block(1, BlockStatus.REPAIRING, sink=sink)
    note_block_flags(b)
    assert 1 in sink
    b.op_status = op
    note_block_flags(b)
    assert sink == {}


def test_a_block_outside_a_run_is_left_alone():
    note_block_flags(_block(1, BlockStatus.REPAIRING, sink=None))   # no error


# --- the two comments ------------------------------------------------------

def _blocks():
    return [
        _block(1), _block(2, BlockStatus.REPAIRING),
        _block(3, BlockStatus.RUNNING, ActiveStatus.CUT),
        _block(4, BlockStatus.REP_HOLD), _block(5, BlockStatus.PM_HOLD),
        _block(6, BlockStatus.DONE), _block(7, BlockStatus.PM),
        _block(8, BlockStatus.IDLE), _block(9, BlockStatus.REPAIRING, ActiveStatus.CUT),
    ]


def _flagged(blocks):
    return [b for b in blocks if (
        b.active_status is ActiveStatus.CUT
        or b.op_status in (BlockStatus.REPAIRING, BlockStatus.REP_HOLD,
                           BlockStatus.DONE, BlockStatus.PM_HOLD, BlockStatus.PM))]


def test_the_state_change_comment_is_the_same_from_the_registry():
    blocks = _blocks()
    walked = change_system_status(
        SystemState(), SystemColor.RED, 5.0, blocks, events_file=True)
    from_registry = change_system_status(
        SystemState(), SystemColor.RED, 5.0, blocks, events_file=True,
        flagged_blocks=_flagged(blocks))
    assert walked == from_registry
    assert "_d2_c3_d4_d6_c9" in walked[-1]


def test_the_red_comment_is_the_same_from_the_registry():
    blocks = _blocks()

    def red(**kw):
        st = SystemState(color=SystemColor.RED, last_up_down_change=0.0)
        return red_change_line(st, 5.0, blocks, events_file=True, **kw), st.red_string

    assert red() == red(flagged_blocks=_flagged(blocks))
    assert red()[1] == "_r2_c3_h4_h5_d6_p7_c9"


def test_mission_changes_still_walk_every_block():
    """The mission snapshot is not a comment: it needs every block."""
    blocks = _blocks()
    stats = {b.id: SimpleNamespace(mission_bool=None) for b in blocks}
    st = SystemState(mission_status=MissionStatus.NON_MISSION)
    change_system_status(st, SystemColor.GREEN, 1.0, blocks, events_file=True,
                         new_mission_status=MissionStatus.MISSION,
                         block_stats=stats, flagged_blocks=_flagged(blocks))
    assert len(_flagged(blocks)) < len(blocks)
    assert all(s.mission_bool is not None for s in stats.values())


# --- the engine ------------------------------------------------------------

def test_failure_effects_view_keeps_the_full_walk():
    from raptor.core.engine.fev import FevSession
    session = FevSession(load_rbd(str(GOLD / "MotorDemo" / "input.rbd")))
    assert session.cascade.flagged_blocks() is None


@pytest.mark.parametrize("fixture", ["MotorDemo", "Cascade", "Phasing1", "LadL7Cap"])
def test_the_registry_holds_exactly_the_flagged_blocks_after_every_step(fixture):
    """The invariant the comments rely on, checked on real runs."""
    from raptor.core.engine.driver import SingleRunDriver, build_engine_ctx
    _model, ctx = build_engine_ctx(load_rbd(str(GOLD / fixture / "input.rbd")), num_runs=1)
    driver = SingleRunDriver(ctx, 0)
    driver.kickoff()
    cascade = driver.cascade
    blocks = list(ctx.model.blocks_by_id.values())
    steps = 0
    while True:
        want = [b for b in blocks if b.active_status is ActiveStatus.CUT or b.op_status in (
            BlockStatus.REPAIRING, BlockStatus.REP_HOLD, BlockStatus.DONE,
            BlockStatus.PM_HOLD, BlockStatus.PM)]
        assert {b.id for b in cascade._flagged.values()} == {b.id for b in want}
        assert [b.id for b in cascade.flagged_blocks()] == [b.id for b in want]
        if steps >= 400 or not driver.step_once():
            break
        steps += 1
    assert steps > 5


@pytest.mark.parametrize("fixture", FIXTURES)
def test_every_golden_fixture_logs_the_same_with_and_without_the_registry(
        fixture, monkeypatch):
    path = str(GOLD / fixture / "input.rbd")
    with_registry = run_simulation(load_rbd(path), num_runs=2)
    monkeypatch.setattr(orchestrator._Cascade, "flagged_blocks", lambda self: None)
    walked = run_simulation(load_rbd(path), num_runs=2)
    assert with_registry == walked
