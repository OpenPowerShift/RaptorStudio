#!/usr/bin/env python3
"""Regenerate `tests/golden/<fixture>/ours/`
report snapshots for every fixture that has a real captured EOR/Final
Results report, so `test_golden_fidelity.py` (V1.2) has something
current to diff against instead of the 4 stale hand-made snapshots that
existed before this script.

For each `tests/golden/<fixture>/` with an `input.rbd` and a `legacy/`
directory containing `rpt_end_of_run*.txt` and/or `rpt_final_results*.txt`,
runs the fixture at its own `SYSTEM_DATA.NumRuns` via
`run_simulation_with_reports(..., date="PROBE")` and writes:

    tests/golden/<fixture>/ours/rpt_end_of_run_ours.txt
    tests/golden/<fixture>/ours/rpt_final_results_ours.txt

`date="PROBE"` is a fixed, recognizable stand-in for the real header's
wall-clock report-generation timestamp (which the simulation itself
never produces) -- `test_golden_fidelity.py` masks that header line on
both sides before comparing, the same way `test_reports.py` already
does with `STUB_DATE`, so the literal string doesn't matter as long as
it's fixed and doesn't collide with anything in a real line.

Idempotent: re-running overwrites the same files with the same content
for an unchanged engine (the whole point -- diff `ours/` before/after an
engine change to see exactly what moved, same spirit as
`test_sd_diff_sweep.py`'s baseline dance but for report TEXT instead of
RNG draws).

Usage (from repo root):
    .venv/bin/python tools/regen_golden_ours.py            # all fixtures
    .venv/bin/python tools/regen_golden_ours.py Capacity2  # one fixture
"""
from __future__ import annotations

import os
import sys
import time
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))
sys.path.insert(0, str(REPO / "tools"))

from raptor.core.engine.runner import run_simulation_with_reports
from raptor.core.fileio.rbd import load_rbd

import golden_fixtures as gf

GOLDEN = REPO / "tests" / "golden"


def fixtures_with_reports() -> list[str]:
    """Every fixture with a real capture, a resolvable .rbd (see
    golden_fixtures.resolve_rbd_path), and at least one of an EOR or
    Final Results real capture file (event-log-only fixtures, e.g.
    Cascade/HierarchyExample, are covered by test_orchestrator.py's
    event-log sweep instead and have nothing for this script to diff)."""
    out = []
    for name in gf.fixtures_with_legacy():
        legacy = GOLDEN / name / "legacy"
        if gf.resolve_rbd_path(name) is None:
            print(f"  ! {name}: no .rbd found (checked tests/golden/ and "
                  "tests/fixtures/), skipping")
            continue
        if (gf.single_match(legacy, "rpt_end_of_run*.txt")
                or gf.single_match(legacy, "rpt_final_results*.txt")):
            out.append(name)
    return out


def regen_one(name: str) -> tuple[bool, str]:
    base = GOLDEN / name
    legacy = base / "legacy"
    eor_real = gf.single_match(legacy, "rpt_end_of_run*.txt")
    fr_real = gf.single_match(legacy, "rpt_final_results*.txt")
    if eor_real is None and fr_real is None:
        return False, "no EOR/Final-Results real capture"

    rbd_path = gf.resolve_rbd_path(name)
    if rbd_path is None:
        return False, "no .rbd found"
    rbd_file = load_rbd(str(rbd_path))
    t0 = time.time()
    _, eor_lines, fr_lines = run_simulation_with_reports(rbd_file, date="PROBE")
    dt = time.time() - t0

    ours = base / "ours"
    ours.mkdir(exist_ok=True)
    if eor_real is not None:
        (ours / "rpt_end_of_run_ours.txt").write_text(
            "\r\n".join(eor_lines), encoding="latin-1")
    if fr_real is not None:
        (ours / "rpt_final_results_ours.txt").write_text(
            "\r\n".join(fr_lines), encoding="latin-1")
    return True, f"{dt:.1f}s"


def main() -> int:
    names = sys.argv[1:] or fixtures_with_reports()
    failures = []
    for name in names:
        try:
            ok, detail = regen_one(name)
        except Exception as exc:  # noqa: BLE001 -- report and keep going
            ok, detail = False, f"EXCEPTION: {exc!r}"
        status = "ok" if ok else "SKIP"
        print(f"{status:5} {name:24} {detail}")
        if not ok and "no EOR/Final-Results" not in detail:
            failures.append(name)
    if failures:
        print(f"\n{len(failures)} fixture(s) failed: {failures}")
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
