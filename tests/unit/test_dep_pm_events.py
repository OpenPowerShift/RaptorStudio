"""Event-log fidelity tests for the DepPM and BlockDepDownPerc fixtures.

Both fixtures are compared cell-by-cell against their captured event logs
over 20 runs. Their `input.rbd` files live under `tests/fixtures`, so the
fixtures are located through the shared `golden_fixtures` resolver rather
than the `GOLDEN/<name>/input.rbd` convention.
"""
from __future__ import annotations

import pathlib
import sys

import pytest

from tests.unit.log_compare import read_capture_text

sys.path.insert(0, str(pathlib.Path(__file__).parent.parent.parent / "tools"))
import golden_fixtures as gf  # noqa: E402

from raptor.core.engine.runner import run_simulation  # noqa: E402
from raptor.core.fileio.rbd import load_rbd  # noqa: E402

GOLDEN = pathlib.Path(__file__).parent.parent / "golden"


def _cells(line: str) -> tuple[str, ...]:
    return tuple(c.strip() for c in line.split("\t"))[:5]


@pytest.mark.parametrize("fixture", ["DepPM", "BlockDepDownPerc"])
def test_dep_events_match_real_all_runs(fixture: str) -> None:
    real = [l.rstrip("\r\n") for l in read_capture_text(GOLDEN / f"{fixture}/legacy/rpt_events_{fixture}.txt").splitlines()
            if "\t" in l][1:]
    runs = sum(1 for l in real if "Begin_Run" in l)
    expected = [_cells(l) for l in real if "Begin_Run" not in l]
    logs = run_simulation(load_rbd(str(gf.resolve_rbd_path(fixture))), num_runs=runs)
    mine = [_cells(l) for run in logs for l in run if "\t" in l]
    assert runs == 20, runs
    assert mine == expected
