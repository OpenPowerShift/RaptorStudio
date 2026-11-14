"""Files & Reports opt-in and `ResultStream`: the engine side.

No GUI import here on purpose: a module that imports PySide6 or raptor.gui
is marked `gui` and left out of the engine tier and its coverage floors
(the GUI wiring tests are in test_report_streams.py).

Raptor collects plot/log files only when ticked in Simulation Options ▸
Files & Reports, and writes them incrementally. These tests pin the port
of that design: sinks are disk-backed streams created only for the ticked
boxes, the engine skips unticked sinks entirely, log collection is
optional, and a FailSim run that never meets its condition is reported
(safety bound) instead of growing memory without limit."""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))


from raptor.core.fileio.rbd import load_rbd
from raptor.core.engine.runner import run_simulation_with_reports
from raptor.core.result_streams import ResultStream

GOLD = os.path.join(os.path.dirname(__file__), "..", "golden")
REPO = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))


def test_result_stream_list_interface(tmp_path):
    s = ResultStream("ao", directory=str(tmp_path))
    s.append("row1")
    s.extend(["row2", "row3"])
    assert len(s) == 3
    assert s.read_all_lines() == ["row1", "row2", "row3"]
    assert list(s) == ["row1", "row2", "row3"]
    # incremental tail read
    assert s.read_new_lines() == ["row1", "row2", "row3"]
    assert s.read_new_lines() == []
    s.append("row4")
    assert s.read_new_lines() == ["row4"]
    assert s  # truthy like a non-empty list
    s.close()
    assert not os.path.exists(s.path)
    s.append("dropped")          # closed stream: silent no-op
    assert len(s) == 4           # row4 was the last counted row


def test_result_stream_reads_closed_stream_as_empty(tmp_path):
    """A closed stream's temp file is deleted, but the Results dock can
    still hold the reference while the next batch starts. Reads must
    report an empty stream, not raise FileNotFoundError — otherwise a
    live Ao update's `_update_graphs` (which reads both plots) fails the
    whole run on the previous batch's stale capacity stream."""
    s = ResultStream("capacity", directory=str(tmp_path))
    s.extend(["0.0  10  10", "1.0  5  10"])
    assert s.read_all_lines() == ["0.0  10  10", "1.0  5  10"]
    s.close()
    assert not os.path.exists(s.path)
    # The exact failure mode: open() on the deleted temp path.
    assert s.read_all_lines() == []
    assert s.read_new_lines() == []
    assert list(s) == []


def test_result_stream_export(tmp_path):
    s = ResultStream("capacity", directory=str(tmp_path))
    s.extend(["0.0  10  10", "1.0  5  10"])
    out = tmp_path / "rpt_capacity_plot_model.txt"
    s.export_to(str(out))
    assert out.read_text().splitlines() == ["0.0  10  10", "1.0  5  10"]
    s.close()


def test_engine_accepts_stream_sinks():
    """The engine's `if not sink:` header-once checks and appends work
    against a ResultStream (list duck-typing)."""
    rbd = load_rbd(os.path.join(GOLD, "TwoBlockRedundancy", "input.rbd"))
    ao = ResultStream("ao")
    _logs, _eor, _fin = run_simulation_with_reports(rbd, 2, ao_sink=ao)
    assert len(ao) > 0
    lines = ao.read_all_lines()
    assert lines[0].startswith("SimulationTime")   # engine's own header
    assert any("End_of_Run_1" in ln for ln in lines)
    ao.close()


def test_failsim_header_wording():
    """FailSim header reads 'Results from N runs to 1 failure:'."""
    rbd = load_rbd(os.path.join(GOLD, "Events", "input.rbd"))
    _logs, _eor, fin = run_simulation_with_reports(
        rbd, 2, collect_logs=False)
    header = next(ln for ln in fin if ln.startswith("Results from"))
    assert header == "Results from 2 runs to 1 failure:", header


def test_safety_bound_reported_not_silent(monkeypatch):
    """A FailSim model whose condition is never met, with events still firing, ends on the
    anti-runaway bound; `metadata_sink` records that so the GUI can warn
    instead of grinding silently. (The bound is shrunk so the test is quick.)"""
    from raptor.core.engine import driver
    monkeypatch.setattr(driver, "SAFETY_BOUND", 500.0)
    rbd = load_rbd(os.path.join(GOLD, "TwoBlockRedundancy", "input.rbd"))
    row = rbd.section("SYSTEM_DATA").rows[0]
    row["TerminationType"], row["TermCondition"] = "FailSim", "1000000"
    meta: dict = {}
    _logs, _eor, _fin = run_simulation_with_reports(
        rbd, 1, collect_logs=False, metadata_sink=meta)
    assert meta.get("hit_safety_bound") is True
    assert meta.get("runs_completed") == 1


def test_a_run_that_ends_because_nothing_is_scheduled_is_not_a_runaway():
    """`Events` ends at t = 0 (nothing is ever scheduled, as in the real capture); that is a natural end,
    so no safety-bound warning."""
    rbd = load_rbd(os.path.join(GOLD, "Events", "input.rbd"))
    meta: dict = {}
    run_simulation_with_reports(rbd, 3, collect_logs=False, metadata_sink=meta)
    assert meta.get("hit_safety_bound") is False


def test_collect_logs_off_frees_run_logs():
    """Event Log checkbox OFF: returned logs are empty (run-index
    alignment kept) and no log rows accumulate."""
    rbd = load_rbd(os.path.join(GOLD, "TwoBlockRedundancy", "input.rbd"))
    logs, eor, fin = run_simulation_with_reports(rbd, 3, collect_logs=False)
    assert len(logs) == 3
    assert all(l == [] for l in logs)
    # reports still collected
    assert eor and fin


def test_time_sim_unaffected_by_metadata(monkeypatch):
    """TimeSim termination is the scheduled end — never flagged as a
    bound hit."""
    rbd = load_rbd(os.path.join(GOLD, "TwoBlockRedundancy", "input.rbd"))
    meta: dict = {}
    run_simulation_with_reports(rbd, 2, metadata_sink=meta)
    assert meta.get("hit_safety_bound") is False
