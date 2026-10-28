"""End-to-end capacity-analysis tests against captured engine output.

Runs the engine with the `_capacity_sink` in-memory hook (the same hook
the GUI capacity plot uses) and compares every
`(SimulationTime, Flow, Capacity)` row, run by run, against
`tests/golden/Capacity1-3/legacy/rpt_capacity_plot_*.txt`.
"""
from __future__ import annotations

from pathlib import Path

import pytest

from raptor.core.fileio.rbd import load_rbd
from raptor.core.engine.runner import run_simulation

REPO = Path(__file__).resolve().parent.parent.parent
FIXTURES = ["Capacity1", "Capacity2", "Capacity3"]
#: Number of leading runs (0-indexed) compared against the real capture.
CLEAN_RUNS = 10
#: Timestamp tolerances for the software `ln` noise floor: timestamps follow
#: the RNG draw chain, so they are compared approximately, while Flow and
#: Capacity are integers and compared exactly.
_TOL_ABS = 3e-6
_TOL_REL = 2.5e-8


def _rows_match(real: list[tuple[float, int, int]], ours: list[tuple[float, int, int]]) -> bool:
    if len(real) != len(ours):
        return False
    for (rt, rf, rc), (ot, of, oc) in zip(real, ours):
        if rf != of or rc != oc:
            return False
        if abs(rt - ot) > max(_TOL_ABS, abs(rt) * _TOL_REL):
            return False
    return True


def _parse_capacity_plot(lines: list[str]) -> list[list[tuple[float, int, int]]]:
    """Real `rpt_capacity_plot_*.txt` lines or our own `_capacity_sink`
    output -> per-run list of (SimulationTime, Flow, Capacity) rows.
    Both use the identical `"*"*30 + "End_of_Run_N" + "*"*30` separator
    (`capacity.py`'s own `analyze_capacity`/`orchestrator.py`'s per-run
    sink extend both write this exact marker)."""
    runs: list[list[tuple[float, int, int]]] = [[]]
    for line in lines:
        line = line.rstrip("\n")
        if line.startswith("*") and "End_of_Run" in line:
            runs.append([])
            continue
        parts = line.split()
        if len(parts) != 3:
            continue
        try:
            t = float(parts[0])
            flow = int(parts[1])
            cap = int(parts[2])
        except ValueError:
            continue
        runs[-1].append((t, flow, cap))
    return [r for r in runs if r]


def _real_runs(fixture: str) -> list[list[tuple[float, int, int]]]:
    path = REPO / "tests" / "golden" / fixture / "legacy" / f"rpt_capacity_plot_{fixture.lower()}.txt"
    return _parse_capacity_plot(path.read_text().splitlines())


def _our_runs(fixture: str) -> list[list[tuple[float, int, int]]]:
    rbd = load_rbd(str(REPO / "tests" / "golden" / fixture / "input.rbd"))
    cap_lines: list[str] = []
    run_simulation(rbd, num_runs=10, _capacity_sink=cap_lines)
    return _parse_capacity_plot(cap_lines)


@pytest.mark.parametrize("fixture", FIXTURES)
def test_capacity_plot_matches_real_capture_for_clean_runs(fixture):
    real_runs = _real_runs(fixture)
    our_runs = _our_runs(fixture)
    assert len(real_runs) == 10 and len(our_runs) == 10

    for run_idx in range(CLEAN_RUNS):
        real, ours = real_runs[run_idx], our_runs[run_idx]
        assert _rows_match(real, ours), (
            f"{fixture} run {run_idx} no longer "
            f"matches the real capacity plot capture: real={real} ours={ours}"
        )
