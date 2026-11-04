"""The Final Results ``***EXTENDED_RESULTS***`` section and `legacy_lines`.

The section follows every Raptor 7 section of the report; the fidelity tests
cut the report at its banner (`legacy_lines`) and compare only the part
before it against the real captures.
"""
from __future__ import annotations

from pathlib import Path

import pytest

from raptor.api.tables import parse_final_sections
from raptor.core.engine.extended_results import (
    EXTENDED_RESULTS_BANNER, build_extended_results_section, legacy_lines)
from raptor.core.engine.runner import run_simulation_with_reports
from raptor.core.fileio.builder import load

REPO = Path(__file__).resolve().parents[2]
MODEL = REPO / "tests" / "fixtures" / "ReleaseProbe46" / "input.rbd"


@pytest.fixture(scope="module")
def final_lines() -> list[str]:
    _, _, fr = run_simulation_with_reports(load(str(MODEL)), date="X")
    return fr


def test_the_section_closes_the_report(final_lines):
    i = final_lines.index(EXTENDED_RESULTS_BANNER)
    before = final_lines[:i]
    assert before[0].startswith("Final_Results_Report")
    assert not any(EXTENDED_RESULTS_BANNER in line for line in final_lines[i + 1:])
    # Every Raptor 7 section comes first.
    assert any(line == "***SUMMARY***" for line in before)


def test_legacy_lines_is_everything_before_the_banner(final_lines):
    i = final_lines.index(EXTENDED_RESULTS_BANNER)
    assert legacy_lines(final_lines) == final_lines[:i]
    assert legacy_lines(final_lines[:i]) == final_lines[:i]


def test_the_tables_parse_into_named_result_tables(final_lines):
    tables = {title: (cols, rows)
              for title, cols, rows in parse_final_sections(final_lines)}
    cols, rows = tables["EXTENDED_RESULTS - Availability_Measures"]
    assert cols == ["Minimum", "Mean", "Maximum", "St.Dev", "SEM"]
    labels = [label for label, _ in rows]
    assert labels[:4] == ["Operational_Availability", "Inherent_Availability",
                          "Achieved_Availability", "MTTFF"]
    assert all(len(values) == 5 for _, values in rows)

    cols, rows = tables["EXTENDED_RESULTS - Block_Criticality"]
    assert cols == ["blockName", "%Failures", "%Downtime", "%Cost"]
    failure = {label: float(values[0]) for label, values in rows}
    assert set(failure) == {"BlockA", "BlockB"}
    assert sum(failure.values()) == pytest.approx(100.0, abs=0.02)
    # Sorted by failure share, largest first.
    assert [label for label, _ in rows] == sorted(
        failure, key=lambda name: -failure[name])


def test_operational_availability_mean_matches_the_summary_availability(
        final_lines):
    tables = {title: rows for title, _, rows in parse_final_sections(final_lines)}
    summary = dict(tables["SUMMARY"])
    extended = dict(tables["EXTENDED_RESULTS - Availability_Measures"])
    assert float(extended["Operational_Availability"][1]) == pytest.approx(
        float(summary["Availability"][1]), abs=1e-9)


def test_no_runs_no_section():
    assert build_extended_results_section([]) == []
