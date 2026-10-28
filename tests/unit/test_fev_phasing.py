"""Failure Effects View on a phased model, against a REAL session.

`tests/golden/FevPhasing3/legacy/fevlog_FevPhasing3.txt` is the interactive log
real Raptor wrote while the script in that folder's README was driven by hand
(phase arrows, a click, both erasers). The port replays it byte for byte.
"""
from __future__ import annotations

from pathlib import Path

from raptor.core.engine.fev import FevSession
from raptor.core.engine.fmeca import build_fmeca, format_fmeca_file_block
from raptor.core.fileio.rbd import load_rbd

GOLD = Path(__file__).resolve().parents[1] / "golden" / "FevPhasing3"
REAL = (GOLD / "legacy" / "fevlog_FevPhasing3.txt").read_bytes().decode()


def _write(s):
    return format_fmeca_file_block(build_fmeca(s))


def test_the_phased_session_reproduces_the_real_log_exactly():
    s = FevSession(load_rbd(str(GOLD / "input.rbd")))
    got = [_write(s)]                                          # W1 phase 1, Cut elements
    s.next_phase(); got.append(_write(s))                      # W2 phase 2
    s.click_block(s.block_by_name("Thermal").id); got.append(_write(s))   # W3 Red
    s.refresh_phase(); got.append(_write(s))                   # W4 small eraser
    s.next_phase(); got.append(_write(s))                      # W5 phase 3
    s.next_phase(); got.append(_write(s))                      # W6 phase 4
    s.previous_phase(); got.append(_write(s))                  # W7 phase 3
    s.refresh_all(); got.append(_write(s))                     # W8 large eraser: phase 1
    assert "".join(got) == REAL


def test_the_phase_arrows_wrap():
    s = FevSession(load_rbd(str(GOLD / "input.rbd")))
    n = len(s.model.phases)
    s.previous_phase()
    assert s.phase_number == n
    s.next_phase()
    assert s.phase_number == 1


def test_the_criticality_line_matches_a_real_session_with_the_developer_option():
    """`fevlog_criticality_FevPhasing3.txt`: the same model with the Criticality line on
    (captured 2026-10-10 from a copy launched with the developer month switch). `0.0`
    when the system is not Red, `REALTOSN(critFactor, 15, 6, 0)` when it is."""
    real = (GOLD / "legacy" / "fevlog_criticality_FevPhasing3.txt").read_bytes().decode()
    s = FevSession(load_rbd(str(GOLD / "input.rbd")))

    def write():
        return format_fmeca_file_block(build_fmeca(s, show_criticality=True))
    got = [write()]                                             # phase 1, Yellow
    s.next_phase(); got.append(write())                         # phase 2
    s.click_block(s.block_by_name("Thermal").id); got.append(write())   # Red
    s.refresh_phase(); got.append(write())                      # Green
    s.click_block(s.block_by_name("Attitude").id); got.append(write())  # Red
    assert "".join(got) == real
