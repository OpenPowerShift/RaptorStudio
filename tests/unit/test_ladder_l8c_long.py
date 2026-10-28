"""LadL8C: 56 blocks, 300 runs, capacity (verification ladder L8, 2026-10-02).

Too long for the fast tiers, so it lives in `tests/ladder/` with its real captures and is checked here under `slow`. Its
event log matches real in all 300 runs (a 87 MB capture, not filed; the first 20 runs are the guarded fixture
`LadL8CEvents`). The End-of-Run and Final Results reports match line for line, every one of the 300 runs.

The last three node-capacity lines (runs 46, 60 and 246: J9's and J1's average capacity) used to be allowed here; they
are the residue of `ChangeLinkStatus` arming the capacity analysis on every link-status change rather than only on a
LinkUp crossing (see "L8" in the internal engine ledger).
"""
from __future__ import annotations

from pathlib import Path

import pytest

from raptor.core.engine.runner import run_simulation_with_reports
from raptor.core.fileio.rbd import load_rbd
from tests.unit.log_compare import read_capture_text

DIR = Path(__file__).resolve().parents[1] / "ladder" / "LadL8C"


def _body(path_or_lines) -> list[str]:
    lines = path_or_lines if isinstance(path_or_lines, list) else read_capture_text(path_or_lines).splitlines()
    return [line.rstrip() for line in lines[3:] if line.strip()]


@pytest.mark.slow
def test_reports_match_exact():
    if not (DIR / "legacy").exists():
        pytest.skip("LadL8C capture not present")
    _, eor, fr = run_simulation_with_reports(load_rbd(str(DIR / "input.rbd")), date="PROBE")
    for name, ours in (("end_of_run", eor), ("final_results", fr)):
        mine, real = _body(ours), _body(DIR / "legacy" / f"rpt_{name}_LadL8C.txt")
        assert len(mine) == len(real)
        stray = [(a, b) for a, b in zip(mine, real) if a != b]
        assert not stray, stray[:3]