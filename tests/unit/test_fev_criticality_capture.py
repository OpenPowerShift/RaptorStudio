"""Criticality in Failure Effects View against a capture of Raptor 7 (`tests/golden/DEPDOWN/legacy`, 2026-10-11).

The status bar value stays at the last multiplied figure after a repair and is reset to 1 by the next failure; the
Interactive Log prints it only while the system is Red and `0.0` otherwise."""
from __future__ import annotations

import pathlib

from raptor.core.engine.fev import FevSession
from raptor.core.engine.fmeca import build_fmeca
from raptor.core.fileio.rbd import load_rbd

ROOT = pathlib.Path(__file__).resolve().parents[2]
REAL_LOG = (ROOT / "tests/golden/DEPDOWN/legacy/fev_interactive_log_criticality.txt").read_bytes().decode("latin-1")


def _session():
    return FevSession(load_rbd(str(ROOT / "tests/fixtures/DEPDOWN/input.rbd")))


def _snapshot(s) -> str:
    return "\r\n".join(build_fmeca(s, show_criticality=True))


def test_status_bar_criticality_follows_raptor_7_through_the_captured_clicks():
    s = _session()
    assert s.criticality == 1.0
    s.click_block(3)                                  # BlockA fails; BlockB is induced
    assert (s.system_color.value, round(s.criticality, 6)) == ("Red", 0.8)
    for _ in range(6):                                # BlockB cycles Repairing, PM, Idle, Operating, ...
        s.click_block(4)
        assert round(s.criticality, 6) == 0.8
    s.click_block(3)                                  # BlockA repaired: still 0.8 on the status bar
    assert round(s.criticality, 6) == 0.8
    s.click_block(4)                                  # BlockB fails alone: reset to 1
    assert round(s.criticality, 6) == 1.0


def test_interactive_log_text_is_what_raptor_7_wrote():
    s = _session()
    s.click_block(3)
    s.click_block(4)
    s.click_block(4)                                  # A failed, B Idle
    assert _snapshot(s) in REAL_LOG
    s.click_block(4)                                  # B operating
    assert _snapshot(s) in REAL_LOG
    for _ in range(3):
        s.click_block(4)                              # Repairing, PM, Idle
    s.click_block(3)                                  # A repaired
    s.click_block(4)                                  # B failed alone
    assert _snapshot(s) in REAL_LOG
