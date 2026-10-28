"""Tests for the Markdown presentation layer.

Covers summary tables (units in headers, human rounding, `display_unit`),
parsing final-results text into real tables (preserving `>`, `N/A` and sample
counts), worst-first element sorting, and the comparison and sweep tables.
"""
from __future__ import annotations

from pathlib import Path

from raptor.api.markdown import (
    summary_table, element_table, report_markdown,
    comparison_table, sweep_table,
)
from raptor.api.results import Metric

REPO = Path(__file__).resolve().parent.parent.parent
GOLDEN = REPO / "tests" / "golden"


class TestSummaryTable:
    """summary_table produces valid Markdown."""

    def test_with_metrics(self):
        metrics = [
            Metric(name="Availability", min=0.8, mean=0.9, max=1.0,
                   stdev=0.05, sem=0.01),
            Metric(name="Reliability", min=0.0, mean=0.3, max=1.0,
                   stdev=0.4, sem=0.08),
        ]
        table = summary_table(type("R", (), {"metrics": metrics})())
        assert "| Metric |" in table
        assert "Availability" in table
        assert "Reliability" in table
        assert "|---" in table

    def test_with_none_values(self):
        metrics = [Metric(name="MTBDE")]  # all None
        table = summary_table(type("R", (), {"metrics": metrics})())
        assert "N/A" in table

    def test_empty(self):
        table = summary_table(type("R", (), {"metrics": []})())
        assert "Metric" in table  # header only

    def test_units_in_time_headers(self):
        metrics = [Metric(name="MTBDE", min=26.142654, mean=33.404143,
                          max=36.0, stdev=None, sem=None)]
        table = summary_table(type("R", (), {"metrics": metrics})())
        assert "MTBDE (hours)" in table
        # Times get 2 decimals, not 6.
        assert "26.14" in table
        assert "26.142654" not in table

    def test_display_unit_converts_and_relabels(self):
        metrics = [Metric(name="MTBDE", min=24.0, mean=48.0, max=72.0,
                          stdev=None, sem=None)]
        result = type("R", (), {
            "metrics": metrics,
            "display_unit": "days",
            "native_unit": "hours",
        })()
        table = summary_table(result)
        assert "MTBDE (days)" in table
        assert "1.00" in table   # 24 h -> 1 day
        assert "3.00" in table   # 72 h -> 3 days

    def test_probabilities_four_significant_figures(self):
        metrics = [Metric(name="Availability", min=0.726184837,
                          mean=0.927892862, max=1.0,
                          stdev=0.078862552, sem=0.015772510)]
        table = summary_table(type("R", (), {"metrics": metrics})())
        assert "0.7262" in table
        assert "0.9279" in table
        assert "0.726184837" not in table  # full precision stays in JSON

    def test_dict_with_metrics_mapping(self):
        payload = {
            "metrics": {
                "Availability": {
                    "min": 0.8, "mean": 0.9, "max": 1.0,
                    "stdev": 0.05, "sem": 0.01,
                },
                "MTBDE": {
                    "min": None, "mean": None, "max": None,
                    "stdev": None, "sem": None,
                },
            },
        }
        table = summary_table(payload)
        assert "Availability" in table
        assert "MTBDE (hours)" in table
        assert "N/A" in table


class TestElementTable:
    """element_table renders SPF or describe rows."""

    def test_basic(self):
        rows = [
            {"kind": "Block", "id": 1, "name": "PumpA"},
            {"kind": "Block", "id": 2, "name": "PumpB"},
        ]
        table = element_table(rows, sort_by="name")
        assert "PumpA" in table
        assert "PumpB" in table
        assert "| kind |" in table

    def test_empty(self):
        table = element_table([])
        assert "No elements" in table

    def test_worst_first_by_default(self):
        rows = [
            {"Node": "good", "AoMean": 0.99},
            {"Node": "bad", "AoMean": 0.38},
            {"Node": "mid", "AoMean": 0.75},
        ]
        table = element_table(rows)
        lines = [ln for ln in table.splitlines() if ln.startswith("| ")]
        # header, then rows: worst (lowest AoMean) first.
        assert "bad" in lines[1]
        assert "mid" in lines[2]
        assert "good" in lines[3]


class TestReportMarkdown:
    """report_markdown parses golden final-results text."""

    def _golden(self) -> str:
        return (
            GOLDEN / "MotorDemo" / "legacy"
            / "rpt_final_results_MotorDemo.txt"
        ).read_text()

    def test_parse_motor_demo_results(self):
        md = report_markdown(self._golden())
        assert "Availability" in md
        assert "MTBDE" in md
        # Real Markdown tables, not a code fence.
        assert "```" not in md
        assert "| Metric | Minimum | Mean | Maximum | St.Dev | SEM |" in md
        # Mean availability preserved at the report's printed precision.
        assert "0.927892862" in md
        # Section headers as Markdown headings.
        assert "#### Summary" in md
        assert "#### Logistics" in md
        assert "#### Sparing" in md
        assert "#### Cost" in md

    def test_preserves_censored_marker_and_footnotes_it(self):
        md = report_markdown(self._golden())
        assert ">33.404143" in md
        assert "at least" in md.lower()
        assert "censored" in md.lower()

    def test_keeps_na_and_explains_it(self):
        md = report_markdown(self._golden())
        assert "N/A" in md
        assert "not available" in md.lower()

    def test_sample_counts_become_footnotes(self):
        md = report_markdown(self._golden())
        assert "MDT" in md
        assert "18 of 25 runs" in md
        assert "(18-runs)" not in md

    def test_empty_report(self):
        md = report_markdown("")
        assert "Empty report" in md

    def test_section_headers(self):
        text = (
            "***SUMMARY***\nAvail  0.9  1.0\n\n"
            "***LOGISTICS***\nMTBM  10.0\n"
        )
        md = report_markdown(text)
        assert "#### Summary" in md
        assert "#### Logistics" in md


class TestCapacityReportMarkdown:
    """report_markdown renders the capacity sink as a real table."""

    CAP = (
        "SimulationTime        Flow       Capacity\n"
        "0.000000              2050       2050\n"
        "13.901797             1550       1550\n"
        "******************************End_of_Run_1******************************\n"
        "0.000000              4          999999999\n"
    )

    def test_table_with_columns(self):
        md = report_markdown(self.CAP, section="capacity")
        assert "```" not in md
        assert "| Time | Flow | Capacity |" in md
        assert "2050" in md
        assert "999999999" in md

    def test_per_run_headings(self):
        md = report_markdown(self.CAP, section="capacity")
        assert "#### Run 1" in md
        assert "#### Run 2" in md

    def test_empty_capacity(self):
        md = report_markdown("***End_of_Run_1***\n", section="capacity")
        assert "No capacity data" in md


class TestComparisonTable:
    """comparison_table renders compare output."""

    def test_basic(self):
        comparison = {
            "models_or_runs": ["m1", "m2"],
            "metrics": {
                "Availability": [0.9, 0.85],
                "Reliability": [0.5, 0.4],
            },
            "deltas": {},
        }
        table = comparison_table(comparison)
        assert "Availability" in table
        assert "0.9" in table
        assert "0.85" in table

    def test_units_in_metric_labels(self):
        comparison = {
            "models_or_runs": ["m1", "m2"],
            "metrics": {"MTBDE": [33.404143, 26.142654]},
        }
        table = comparison_table(comparison)
        assert "MTBDE (hours)" in table
        assert "33.40" in table


class TestSweepTable:
    """sweep_table renders sweep output."""

    def test_basic(self):
        sweep_result = {
            "field": "repair_mean",
            "rows": [
                {"repair_mean": 10.0, "Availability": 0.95},
                {"repair_mean": 20.0, "Availability": 0.90},
                {"repair_mean": 30.0, "Availability": 0.85},
            ],
            "monotonic": True,
        }
        table = sweep_table(sweep_result)
        assert "repair_mean" in table
        assert "Availability" in table
        assert "0.95" in table

    def test_units_in_metric_headers(self):
        sweep_result = {
            "field": "repair_mean",
            "rows": [
                {"repair_mean": 10.0, "MTBDE": 33.404143},
                {"repair_mean": 20.0, "MTBDE": 26.142654},
            ],
            "monotonic": True,
        }
        table = sweep_table(sweep_result)
        assert "MTBDE (hours)" in table
        assert "33.40" in table

    def test_non_monotonic(self):
        sweep_result = {
            "field": "x",
            "rows": [
                {"x": 1.0, "Availability": 0.9},
                {"x": 2.0, "Availability": 0.95},
                {"x": 3.0, "Availability": 0.85},
            ],
            "monotonic": False,
        }
        table = sweep_table(sweep_result)
        assert "not monotonic" in table.lower()

    def test_empty(self):
        table = sweep_table({"rows": []})
        assert "No sweep data" in table