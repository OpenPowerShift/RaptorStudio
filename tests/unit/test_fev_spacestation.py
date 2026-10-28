"""Failure Effects View against a REAL session.

`tests/golden/FevSpaceStation/legacy/fevlog_FevSpaceStation.txt` is the
interactive log real Raptor 7 wrote while the click script in that folder's
README was driven by hand: every "Write To Failure Effects Log" is one block
of text. The port replays the same clicks and must write the same bytes.
"""
from __future__ import annotations

from pathlib import Path


from raptor.core.engine.fev import FevSession
from raptor.core.engine.fmeca import build_fmeca, format_fmeca_file_block
from raptor.core.fileio.rbd import load_rbd

GOLD = Path(__file__).resolve().parents[1] / "golden" / "FevSpaceStation"
REAL = (GOLD / "legacy" / "fevlog_FevSpaceStation.txt").read_bytes().decode()


def _session():
    return FevSession(load_rbd(str(GOLD / "input.rbd")))


def _write(s):
    return format_fmeca_file_block(build_fmeca(s))


def _click(s, name):
    s.click_block(s.block_by_name(name).id)


def test_the_captured_script_reproduces_the_real_log_exactly():
    s = _session()
    got = [_write(s)]                                   # W1 baseline
    _click(s, "BatteriesNiCd"); got.append(_write(s))   # W2
    _click(s, "Bus_DET-FR"); got.append(_write(s))      # W3
    _click(s, "SolarP_900W"); got.append(_write(s))     # W4 (hatched, no change)
    _click(s, "BatteriesNiCd"); got.append(_write(s))   # W5 (restore)
    s.refresh_all(); got.append(_write(s))              # W6 large eraser
    _click(s, "Thermal"); _click(s, "SolarP_500W"); got.append(_write(s))  # W7
    got.append(_write(s))                               # W8 small eraser: no-op
    assert "".join(got) == REAL


def test_the_real_log_has_eight_writes():
    assert REAL.count("*" * 65) == 8
    assert REAL.startswith("\r\nFAILURES\r\n")


# --- the second real session: cold standby, node degrade, hatched cycling ---

GOLD2 = Path(__file__).resolve().parents[1] / "golden" / "FevHierarchyExample"
REAL2 = (GOLD2 / "legacy" / "fevlog_FevHierarchyExample.txt").read_bytes().decode()


def test_the_cold_standby_session_reproduces_the_real_log_exactly():
    """HierarchyExample: HighGain primary + LowGain cold standby, a series block
    (Attitude) with percentage dependencies on the system."""
    s = FevSession(load_rbd(str(GOLD2 / "input.rbd")))
    # Entering FEV already put LowGain on standby (the blue block).
    assert s.block_by_name("LowGain").op_status.value == "Standby"
    got = [_write(s)]                                       # W1
    _click(s, "HighGain"); got.append(_write(s))            # W2 fail primary
    assert s.block_by_name("LowGain").op_status.value == "Running"   # took over
    _click(s, "HighGain"); got.append(_write(s))            # W3 restore
    assert s.block_by_name("LowGain").op_status.value == "Standby"
    _click(s, "LowGain"); got.append(_write(s))             # W4 fail the standby
    _click(s, "HighGain"); got.append(_write(s))            # W5 both failed
    _click(s, "Attitude"); got.append(_write(s))            # W6 cycle -> PM
    _click(s, "Attitude"); got.append(_write(s))            # W7 -> Idle
    _click(s, "Attitude"); got.append(_write(s))            # W8 -> operating
    _click(s, "Attitude"); got.append(_write(s))            # W9 -> failed
    s.refresh_all(); got.append(_write(s))                  # W10
    assert "".join(got) == REAL2
