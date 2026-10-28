"""The four per-run data files of Simulation Options > Files & Reports.

"Key parameters", "All system failure times", "All system down times" and
"Ending simulation times" (manual Appendix C). Each is a pure function of the
runs' `RunReportData`, so a parallel batch, the GUI, the CLI and the API write
the same bytes. Layouts are from Raptor 7 (`OpenOutFiles` header,
`DoEndOfRunUpdate`, `ChangeStatus`, `ChangeNodeState` )
and are compared with Raptor 7 captures in
`tests/golden/ZeroFailTimeProbe/legacy/` and `tests/golden/ZeroFailFailSimProbe/legacy/`
(`rpt_{key_parameters,failure_times,down_times,end_times}_*.txt`).

Functions return lines without line ends; `legacy_text` joins them with the
original's CRLF.
"""
from __future__ import annotations

from collections.abc import Sequence

from raptor.core.engine.report_format import format_stats, write_real
from raptor.core.engine.reporting import RunReportData

KEY_PARAMETERS_HEADER = (
    "Trial     Ao           Do           R            RCond"
    "        MTBDE                MDT                MTBM                MMT")

_NA_STAT = "        N/A"            # a missions-attempted-zero R / Do / RCond
_NA_REAL = "                N/A "   # a value that was never updated this run


def _real(val: float) -> str:
    """`WriteReal(val, 19, 9)`: right-aligned in 19 columns, 9 decimals."""
    return write_real(val, 9).rjust(19)


def _real_or_na(val: float | None) -> str:
    return _NA_REAL if val is None else _real(val) + " "


def key_parameters_lines(reports: Sequence[RunReportData]) -> list[str]:
    """One row per trial (`DoEndOfRunUpdate`).

    `Do` is the run's dependability; a model with no phases has every second in
    one mission, so it equals Ao. A value the run never updated (no failure, no
    repair, no maintenance) is `N/A`."""
    lines = [KEY_PARAMETERS_HEADER]
    for d in reports:
        attempted = d.missions_attempted != 0
        do = d.dependability if d.dependability is not None else d.availability
        row = (str(d.run_number) + " " * 10)[:10]
        row += format_stats(d.availability) + "  "
        row += ((format_stats(do) if attempted else _NA_STAT) + "  ")
        row += ((format_stats(d.reliability) if attempted else _NA_STAT) + "  ")
        row += ((format_stats(d.conditional_reliability) if attempted else _NA_STAT) + "  ")
        row += _real_or_na(d.mtbde) + _real_or_na(d.mdt)
        row += _real_or_na(d.mtbm) + _real_or_na(d.mmt)
        lines.append(row)
    return lines


def failure_times_lines(reports: Sequence[RunReportData]) -> list[str]:
    """`SysTimeToFail` of every system failure, all runs in order, from the moment
    statistics started. A failure at time zero with
    "ignore start failures" on has no entry ."""
    return [_real(t) for d in reports for t in d.sys_fail_times]


def down_times_lines(reports: Sequence[RunReportData]) -> list[str]:
    """`SysTimeToRepair` of every system recovery, all runs in order."""
    return [_real(t) for d in reports for t in d.sys_repair_times]


def end_times_lines(reports: Sequence[RunReportData]) -> list[str]:
    """`SimTime` at the end of each run."""
    return [_real(d.ending_sim_time) for d in reports]


DATA_FILES = {
    "key_parameters": key_parameters_lines,
    "failure_times": failure_times_lines,
    "down_times": down_times_lines,
    "end_times": end_times_lines,
}


def legacy_text(lines: Sequence[str]) -> str:
    """The file as Raptor 7 writes it: every line ends CRLF."""
    return "".join(f"{ln}\r\n" for ln in lines)


def to_legacy_eol(text: str) -> str:
    """Stored artefact text (LF) -> Raptor 7's CRLF file."""
    return legacy_text(text.splitlines())
