"""End-to-end test of `runner.run_simulation_with_reports`: loads
each fixture's real `input.rbd` and diffs the generated `End_Of_Run` /
`Final_Results_Report` text against the captured report files in
`tests/golden/*/legacy/`, using the shared magnitude-scaled line
comparator in `tests/unit/log_compare.py` (the same one
`test_orchestrator.py` and `test_golden_fidelity.py` use).

The header line embeds the report-generation wall-clock time, which the
simulation does not produce, so both sides get it masked. Trailing
whitespace-only lines carry no structural information and are stripped
from both sides.
"""

from __future__ import annotations

import pathlib

import pytest

from raptor.core.engine.runner import run_simulation_with_reports
from raptor.core.fileio.rbd import load_rbd
from tests.unit.log_compare import (
    classify, read_lines, strip_trailing_blank_ish)

GOLDEN = pathlib.Path(__file__).parent.parent / "golden"


def _mask_header_date(lines: list[str]) -> list[str]:

    if lines and lines[0].startswith(("Final_Results_Report", "End_Of_Run Report")):

        lines = list(lines)

        lines[0] = lines[0].split("  ")[0] + "  STUB_DATE"

    return lines


def _diff(mine: list[str], real: list[str], label: str) -> None:

    mine = _mask_header_date(strip_trailing_blank_ish(mine))

    real = _mask_header_date(strip_trailing_blank_ish(real))

    assert len(mine) == len(real), f"{label}: line count differs: mine={len(mine)} real={len(real)}"

    for i, (m, r) in enumerate(zip(mine, real)):

        c = classify(m, r)

        assert c != "structural", f"{label}: structural mismatch at line {i}: mine={m!r} real={r!r}"


@pytest.mark.parametrize(

    "fixture_dir,end_of_run_file,final_results_file",

    [

        ("TwoBlockRedundancy", None, "rpt_input_two_results.txt"),

        ("UniformProbe", "rpt_uniform_end_of_run_2.txt", "rpt_uniform_final_results_2.txt"),

        ("Events", "rpt_end_of_run_events.txt", "rpt_final_results_events.txt"),

        # termType=3: both summary rows + "... of 3000 cycles:" header +

        # actual elapsed EndingSimTime (~50,987h), 4 blank lines before

        # ***SPARING*** (time-based reports print 5).

        ("CYCLESIM", "rpt_end_of_run_CYCLESIM.txt", "rpt_final_results_CYCLESIM.txt"),

        # StartStats=1000: ResetAllStatistics mid-run -- "Started
        # Gathering Statistics" marker + Failures_After_Reset/
        # Active_Stat_Time rows + footer + discardedFailures Sparing
        # Failures column (all but fp32 last-digit noise).

        ("CYCLESIM_SS", "rpt_end_of_run_CYCLESIM_SS.txt", "rpt_final_results_CYCLESIM_SS.txt"),

    ],

)

def test_reports_match_real_captured_files(fixture_dir, end_of_run_file, final_results_file) -> None:

    rbd_file = load_rbd(str(GOLDEN / fixture_dir / "input.rbd"))

    _, eor_lines, fr_lines = run_simulation_with_reports(rbd_file, date="STUB_DATE")

    if end_of_run_file is not None:

        real_eor = read_lines(GOLDEN / fixture_dir / "legacy" / end_of_run_file)

        _diff(eor_lines, real_eor, f"{fixture_dir} End_Of_Run")

    if final_results_file is not None:

        real_fr = read_lines(GOLDEN / fixture_dir / "legacy" / final_results_file)

        _diff(fr_lines, real_fr, f"{fixture_dir} Final_Results")


def test_weak_analysis_probe_section_matches_real() -> None:
    """The whole WEAK_LINK_ANALYSIS section (NODE/BLOCK/EVENT/
    HIERARCHY_RESULTS) matches the real capture byte-for-byte."""
    _runs, eor, _final = run_simulation_with_reports(
        load_rbd(str(GOLDEN / "WeakAnalysisProbe" / "input.rbd")), num_runs=1)
    real = (GOLDEN / "WeakAnalysisProbe" / "legacy"
            / "rpt_end_of_run_WeakAnalysisProbe.txt").read_text(
                encoding="latin-1").splitlines()

    def weak(lines: list[str]) -> list[str]:
        i = next(k for k, l in enumerate(lines) if "WEAK_LINK_ANALYSIS" in l)
        j = next((k for k in range(i + 1, len(lines))
                  if lines[k].startswith("***")), len(lines))
        return lines[i:j]

    assert weak(eor) == weak(real)
