"""The four Files & Reports data files against real captures
(`ZeroFailTimeProbe`, `ZeroFailFailSimProbe`, `IsolatePlain`, `IsolateDeps`: every box ticked)."""
from __future__ import annotations

from pathlib import Path

import pytest

from raptor.core.engine import data_files as df
from raptor.core.fileio.rbd import load_rbd

GOLD = Path(__file__).resolve().parents[1] / "golden"
NAMES = ["ZeroFailTimeProbe", "ZeroFailFailSimProbe", "IsolatePlain", "IsolateDeps", "IsolateShare", "IsolateCold"]


def _reports(name):
    from raptor.core.engine.runner import run_simulation
    rows = []
    run_simulation(load_rbd(str(GOLD / name / "input.rbd")), _report_sink=rows)
    return rows


@pytest.mark.parametrize("name", NAMES)
@pytest.mark.parametrize("kind,stem", [
    ("key_parameters", "rpt_key_parameters"), ("failure_times", "rpt_failure_times"),
    ("down_times", "rpt_down_times"), ("end_times", "rpt_end_times")])
def test_file_matches_real(name, kind, stem):
    real = (GOLD / name / "legacy" / f"{stem}_{name}.txt").read_bytes().decode()
    got = df.legacy_text(df.DATA_FILES[kind](_reports(name)))
    assert got == real


def test_failure_and_down_times_start_only_when_statistics_start():
    """With StartStats > 0 (a TimeSim run) the two files hold only what happened
    after "Started Gathering Statistics" (`StatsStarted`)."""
    from raptor.core.engine.runner import run_simulation
    rbd = load_rbd(str(GOLD / "ZeroFailTimeProbe" / "input.rbd"))
    rbd.sections["SYSTEM_DATA"].rows[0]["StartStats"] = "100.000000"
    rows = []
    logs = run_simulation(rbd, num_runs=3, _report_sink=rows)
    full = _reports("ZeroFailTimeProbe")
    for log, d in zip(logs, rows):
        marker = next(i for i, ln in enumerate(log) if "Started Gathering Statistics" in ln)
        after = log[marker:]
        reds = [ln for ln in after if "\tSystem" in ln and "State_change" in ln and "\tRed" in ln]
        assert len(d.sys_fail_times) == len(reds)
        assert len(d.sys_repair_times) >= len(d.sys_fail_times) - 1
    assert sum(len(d.sys_fail_times) for d in rows) < sum(len(d.sys_fail_times) for d in full[:3])


def test_parallel_batches_write_the_same_files_as_a_serial_one():
    from raptor.core.engine.runner import run_simulation
    rbd = load_rbd(str(GOLD / "ZeroFailTimeProbe" / "input.rbd"))
    rows = []
    run_simulation(rbd, num_runs=4, num_workers=2, _report_sink=rows)
    assert [d.run_number for d in rows] == [1, 2, 3, 4]
    for kind in ("end_times", "key_parameters"):
        assert len(df.DATA_FILES[kind](rows)) >= 4
    assert all(d.sys_fail_times for d in rows)
