"""LadL8B: 44 blocks, 500 runs, everything together (verification ladder L8, 2026-10-01).

Too long for the fast tiers (about a minute, past the 25 s watchdog), so it lives in `tests/ladder/` with its real
captures and is checked here under `slow`. Its first 30 runs, with the event log, are the ordinary guarded fixture
`LadL8BEvents`. Compared: the End-of-Run and Final Results reports (every `/sd` draw matched when captured, `tools/sd_diff.py --fixture` on a copy under tests/golden).
"""
from __future__ import annotations

from pathlib import Path

import pytest

from raptor.core.engine.runner import run_simulation_with_reports
from raptor.core.fileio.rbd import load_rbd
from tests.unit.log_compare import read_capture_text

DIR = Path(__file__).resolve().parents[1] / "ladder" / "LadL8B"


def _body(path_or_lines) -> list[str]:
    lines = path_or_lines if isinstance(path_or_lines, list) else read_capture_text(path_or_lines).splitlines()
    return [line.rstrip() for line in lines[3:] if line.strip()]


@pytest.mark.slow
def test_reports_and_draws_are_exact_at_500_runs():
    if not (DIR / "legacy").exists():
        pytest.skip("LadL8B capture not present")
    _, eor, fr = run_simulation_with_reports(load_rbd(str(DIR / "input.rbd")), date="PROBE")
    assert _body(eor) == _body(DIR / "legacy" / "rpt_end_of_run_LadL8B.txt")
    assert _body(fr) == _body(DIR / "legacy" / "rpt_final_results_LadL8B.txt")
