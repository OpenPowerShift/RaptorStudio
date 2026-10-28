"""`DepFailBeyondHorizonProbe`: a dependency-induced failure that lands in
`Run`'s beyond-horizon wait, the one wake site whose `Induced_Failure` branch
no other fixture reaches.

The probe (built by an internal build script) has BlockDep,
whose life of 1e10 overshoots the run, fail by dependency each time BlockC
fails: 12 times in 150 hours. The port used to count each of those twice
(36 component failures); real Raptor's capture reports 24, one count per
failure, so `BlockProcess._on_fail_wait_wake` no longer records the failure
itself and leaves it to the failure tail. This test keeps that answer.
"""
from __future__ import annotations

import re
from pathlib import Path

import pytest

from raptor.core.engine.runner import run_simulation_with_reports
from raptor.core.fileio.builder import load

PROBE = Path(__file__).resolve().parents[1] / "golden" / "DepFailBeyondHorizonProbe"
_COMPONENT_FAILURES = re.compile(r"^NumberOfComponentFailures\s+(\d+)\s*$")
#: BlockC's own failures (every 12 h from t=10) and BlockDep's induced ones.
_INDUCED_AT = [10.0 + 12.0 * i for i in range(12)]


def _component_failures(eor: list[str]) -> int:
    counts = [int(m.group(1)) for line in eor if (m := _COMPONENT_FAILURES.match(line))]
    assert len(counts) == 1, f"expected one NumberOfComponentFailures line, found {counts}"
    return counts[0]


@pytest.fixture(scope="module")
def port_run() -> tuple[list[str], list[str]]:
    logs, eor, _fr = run_simulation_with_reports(load(str(PROBE / "input.rbd")), date="PROBE")
    return logs[0], eor


def test_probe_reaches_the_beyond_horizon_induced_failure_branch(port_run) -> None:
    events, _eor = port_run
    induced = [float(line.split("\t")[0]) for line in events
               if "BlockDep" in line and "\tFailed" in line]
    assert induced == _INDUCED_AT


def test_each_beyond_horizon_induced_failure_counts_once(port_run) -> None:
    """BlockC's 12 failures, and each one's induced BlockDep failure: 24."""
    _events, eor = port_run
    assert _component_failures(eor) == 12 + 12


def test_component_failures_match_real_capture(port_run) -> None:
    real = _component_failures(
        (PROBE / "legacy" / "rpt_end_of_run_DepFailBeyondHorizonProbe.txt")
        .read_text(errors="replace").splitlines())
    _events, eor = port_run
    assert _component_failures(eor) == real == 24
