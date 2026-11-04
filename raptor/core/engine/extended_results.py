"""The ``***EXTENDED_RESULTS***`` section of the Final Results report.

Raptor 7's report ends after its own sections (SUMMARY through
WEAK_LINK_ANALYSIS). Everything Raptor Studio adds goes after them, under
one banner, so the report is complete for the reader and the legacy part
can still be checked against reports captured from real Raptor:
`legacy_lines` cuts the report at the banner and the fidelity tests
compare only what comes before it.

The values are the additive per-run fields of `RunReportData` (the same
ones Results ▸ Availability and Criticality shows), aggregated one sample
per run like every other Final Results statistic.
"""
from __future__ import annotations

import math
from collections.abc import Sequence

from raptor.core.engine.report_format import pop_to_sample
from raptor.core.engine.reporting import RunReportData
from raptor.core.engine.stats import RunningStat

#: First line of the section. Everything before it is the Raptor 7 report.
EXTENDED_RESULTS_BANNER = "***EXTENDED_RESULTS***"

#: (report label, `RunReportData` attribute, decimals) for the availability
#: table. Decimals follow the legacy SUMMARY table: 9 for availabilities,
#: 6 for times and counts' means.
_MEASURES: tuple[tuple[str, str, int], ...] = (
    ("Operational_Availability", "operational_availability", 9),
    ("Inherent_Availability", "inherent_availability", 9),
    ("Achieved_Availability", "achieved_availability", 9),
    ("MTTFF", "mttff", 6),
    ("MTBF_Total_Time", "mtbf_total", 6),
    ("MTBE_Total_Time", "mtbe_total", 6),
    ("Component_CMs", "num_component_cms", 6),
    ("Component_PMs", "num_component_pms", 6),
    ("Red_Time_Corrective", "red_corrective_time", 6),
    ("Red_Time_PM", "red_pm_time", 6),
    ("Red_Time_Logistics", "red_logistics_time", 6),
    ("Red_Time_Done", "red_done_time", 6),
    ("Red_Time_Other", "red_other_time", 6),
)


def legacy_lines(lines: Sequence[str]) -> list[str]:
    """The Raptor 7 part of a Final Results report: every line before
    `EXTENDED_RESULTS_BANNER` (the whole report when there is none).

    Use this wherever a report is compared with a captured real-Raptor
    report."""
    lines = list(lines)
    for i, line in enumerate(lines):
        if line.strip() == EXTENDED_RESULTS_BANNER:
            return lines[:i]
    return lines


def _number(value: float, decimals: int) -> str:
    if not math.isfinite(value):
        return "N/A"
    return f"{value:.{decimals}f}"


def _stat_row(label: str, values: list[float], decimals: int) -> str:
    stat = RunningStat()
    for value in values:
        stat.add(float(value))
    if stat.count == 0:
        cols = ["N/A"] * 5
    else:
        if stat.count > 1:
            sigma = pop_to_sample(stat.population_stdev, stat.count)
            sd = _number(sigma, decimals)
            sem = _number(sigma / math.sqrt(stat.count), decimals)
        else:
            sd = sem = "N/A"
        cols = [_number(stat.minimum, decimals), _number(stat.mean, decimals),
                _number(stat.maximum, decimals), sd, sem]
    return f"{label:<25}" + "".join(f"{col:<16}" for col in cols)


def _numbers(values: Sequence[object]) -> list[float]:
    return [float(v) for v in values
            if isinstance(v, (int, float)) and not isinstance(v, bool)]


def _mean(values: Sequence[object]) -> float | None:
    finite = _numbers(values)
    return sum(finite) / len(finite) if finite else None


def build_extended_results_section(
        all_run_data: Sequence[RunReportData]) -> list[str]:
    """The section's lines, banner first. Empty when there are no runs."""
    runs = list(all_run_data)
    if not runs:
        return []
    lines = [EXTENDED_RESULTS_BANNER, "",
             "Availability_Measures", "",
             f"{'':<25}" + "".join(f"{h:<16}" for h in (
                 "Minimum", "Mean", "Maximum", "St.Dev", "SEM"))]
    for label, attr, decimals in _MEASURES:
        lines.append(_stat_row(
            label, _numbers([getattr(rd, attr, None) for rd in runs]),
            decimals))
    censored = sum(1 for rd in runs if rd.mttff_censored)
    if censored:
        lines.append("")
        lines.append(
            f"MTTFF: {censored} of {len(runs)} run(s) had no system failure; "
            "their run length is used, so the MTTFF is a lower bound.")
    lines.extend(["", "", ""])
    lines.extend(_criticality_table(runs))
    lines.extend(["", "", ""])
    return lines


_SHARES = ("failure", "downtime", "cost")


def _mean_shares(runs: list[RunReportData]
                 ) -> tuple[dict[int, dict[str, float | None]], dict[int, str]]:
    """block id -> {share: mean over runs}, and block id -> name."""
    shares: dict[int, dict[str, list[object]]] = {}
    names: dict[int, str] = {}
    for rd in runs:
        for bid, row in (rd.criticality or {}).items():
            names[bid] = str(row.get("name", bid))
            entry = shares.setdefault(bid, {key: [] for key in _SHARES})
            for key, bucket in entry.items():
                bucket.append(row.get(key))
    means = {bid: {k: _mean(v) for k, v in entry.items()}
             for bid, entry in shares.items()}
    return means, names


def _criticality_table(runs: list[RunReportData]) -> list[str]:
    """Per-block failure / downtime / cost shares, mean over runs, as %."""
    means, names = _mean_shares(runs)
    # A single-token label line, so `api.tables.parse_final_sections` titles
    # the table by it. Values are each block's % share of the system total,
    # mean over runs.
    lines = ["Block_Criticality", ""]
    if not means:
        lines.append("No block criticality data.")
        return lines
    lines.append(f"{'blockName':<22}" + "".join(
        f"{h:>14}" for h in ("%Failures", "%Downtime", "%Cost")))

    def order(bid: int) -> tuple[bool, float, str]:
        failure = means[bid]["failure"]
        return (failure is None, -(failure or 0.0), names[bid].lower())

    for bid in sorted(means, key=order):
        cells = ["N/A" if (v := means[bid][key]) is None else f"{100.0 * v:.2f}"
                 for key in _SHARES]
        lines.append(f"{names[bid]:<21} " + "".join(f"{c:>14}" for c in cells))
    hints = []
    if all(means[bid]["downtime"] is None for bid in means):
        hints.append("Downtime shares need Weak Link analysis.")
    if all(means[bid]["cost"] is None for bid in means):
        hints.append("Cost shares need Cost analysis.")
    if hints:
        lines.append("")
        lines.append(" ".join(hints))
    return lines
