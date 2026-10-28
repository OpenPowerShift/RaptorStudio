"""Failure Effects View click rules: the hatched-block cycle for every set of non-zero dependency percentages
(checked against the source's tables), the standby-stress boundary (the source, and a real
session on `FevStandbyStressProbe`), and the two erasers' asymmetry."""
from __future__ import annotations

import itertools
from pathlib import Path

import pytest

from raptor.core.engine.fev import FevSession
from raptor.core.fileio.rbd import load_rbd
from raptor.core.model import BlockStatus as S

GOLD = Path(__file__).resolve().parents[1] / "golden"
HIER = GOLD / "FevHierarchyExample" / "input.rbd"


def _reference_next(state, f, p, i, n):
    """The click table, restated independently of the implementation."""
    if state == S.RUNNING:
        return S.REPAIRING if f else S.PM if p else S.IDLE if i else S.RUNNING
    if state == S.REPAIRING:
        return S.PM if p else S.IDLE if i else S.RUNNING if n else S.REPAIRING
    if state == S.PM:
        return S.IDLE if i else S.RUNNING if n else S.REPAIRING if f else S.PM
    return S.RUNNING if n else S.REPAIRING if f else S.PM if p else S.IDLE   # IDLE


def _session_with_attitude_hatched(f, p, i, n):
    s = FevSession(load_rbd(str(HIER)))
    a = s.block_by_name("Attitude")
    a.dep_fail_percent, a.dep_pm_percent, a.dep_idle_percent = 25.0 * f, 25.0 * p, 25.0 * i
    a.dep_nothing_percent = 25.0 * n if (f or p or i) else 100.0
    s.click_block(s.block_by_name("HighGain").id)
    s.click_block(s.block_by_name("LowGain").id)      # both gone: system Red
    return s, a


@pytest.mark.parametrize("flags", [c for c in itertools.product((0, 1), repeat=4)
                                   if c[0] or c[1] or c[2]])
def test_the_hatched_cycle_follows_the_table(flags):
    f, p, i, n = flags
    s, a = _session_with_attitude_hatched(f, p, i, n)
    assert a.run_state.fev_induced_by_dependency
    # Worst case first.
    assert a.op_status == (S.REPAIRING if f else S.PM if p else S.IDLE)
    for _ in range(8):
        expected = _reference_next(a.op_status, f, p, i, n)
        s.click_block(a.id)
        assert a.op_status == expected, (flags, expected)
        assert a.run_state.fev_induced_by_dependency


def test_a_block_with_no_dependency_effect_is_not_hatched():
    _s, a = _session_with_attitude_hatched(0, 0, 0, 1)
    assert not a.run_state.fev_induced_by_dependency and a.op_status == S.RUNNING


@pytest.mark.parametrize("stress,fails", [(0.0, False), (1e-9, True), (1.0, True)])
def test_a_standby_block_fails_by_click_only_when_its_stress_is_positive(stress, fails):
    s = FevSession(load_rbd(str(HIER)))
    low = s.block_by_name("LowGain")
    assert low.op_status == S.STANDBY
    low.sb_stress = stress
    s.click_block(low.id)
    assert (low.op_status == S.REPAIRING) is fails


def test_the_erasers_differ():
    """Large: everything initial, phase 1. Small: failures cleared, phase kept,
    and an Idle block stays Idle (RefreshPhase only touches Repairing / PM)."""
    s = FevSession(load_rbd(str(GOLD / "FevPhasing3" / "input.rbd")))
    s.goto_phase(3)
    thermal = s.block_by_name("Thermal")
    s.click_block(thermal.id)
    assert thermal.op_status == S.REPAIRING
    idle = [b for b in s.blocks if b.op_status == S.IDLE]
    s.refresh_phase()
    assert thermal.op_status == S.RUNNING
    assert s.phase_number == 3
    assert [b for b in s.blocks if b.op_status == S.IDLE] == [
        b for b in idle if b.op_status == S.IDLE]
    s.click_block(thermal.id)
    s.refresh_all()
    assert s.phase_number == 1


def test_phase_round_trip_and_noop():
    s = FevSession(load_rbd(str(GOLD / "FevPhasing3" / "input.rbd")))
    s.goto_phase(3)
    at3 = s.snapshot()
    s.goto_phase(1)
    s.goto_phase(3)
    assert s.snapshot() == at3
    s.goto_phase(3)
    assert s.snapshot() == at3


def test_hatching_survives_a_phase_change_but_not_a_click():
    """Pinned by the real Phasing3 log: W2 (after the arrow) still lists the three
    solar-path blocks as Induced Failures; W3 (after a click) lists them as Failed."""
    s = FevSession(load_rbd(str(GOLD / "FevPhasing3" / "input.rbd")))
    names = lambda: {b.name for b in s.fev_dep_group}      # noqa: E731
    s.next_phase()
    assert {"SolarP_500W", "BatteriesNiCd", "Bus_UR"} <= names()
    s.click_block(s.block_by_name("Thermal").id)
    assert not {"SolarP_500W", "BatteriesNiCd", "Bus_UR"} & names()


def test_the_zero_stress_standby_click_matches_a_real_session():
    """`FevStandbyStressProbe` (captured 2026-10-21): a Standby block with stress 0 ignores a click; once it is
    Running a click fails it."""
    from raptor.core.engine.fmeca import build_fmeca, format_fmeca_file_block
    g = GOLD / "FevStandbyStressProbe"
    real = (g / "legacy" / "fevlog_FevStandbyStressProbe.txt").read_bytes().decode()
    s = FevSession(load_rbd(str(g / "input.rbd")))

    def write():
        return format_fmeca_file_block(build_fmeca(s))
    got = [write()]
    for name in ("LowGain", "HighGain", "LowGain"):
        s.click_block(s.block_by_name(name).id)
        got.append(write())
    s.refresh_all()
    got.append(write())
    assert "".join(got) == real


def test_clicking_an_event_matches_a_real_session():
    """`EventDepProbe` (captured 2026-10-21): a click fails the event (its dependent path goes down, System
    Red), a second click restores it; the large eraser leaves Green. Writes: baseline, click, click, click,
    click + large eraser."""
    from raptor.core.engine.fmeca import build_fmeca, format_fmeca_file_block
    g = GOLD / "EventDepProbe"
    real = (g / "legacy" / "fevlog_EventDepProbe.txt").read_bytes().decode()
    s = FevSession(load_rbd(str(g / "input.rbd")))
    event = next(iter(s.model.events_by_id))

    def write():
        return format_fmeca_file_block(build_fmeca(s))
    got = [write()]
    for _ in range(3):
        s.click_event(event)
        got.append(write())
    s.click_event(event)
    s.refresh_all()
    got.append(write())
    assert "".join(got) == real
