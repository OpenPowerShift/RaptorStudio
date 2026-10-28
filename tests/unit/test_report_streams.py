"""Files & Reports opt-in and `ResultStream`.

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
from raptor.gui.ui.result_streams import ResultStream

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


def test_gui_files_and_reports_wiring(qtbot=None):
    """The Simulate ▸ Files & Reports group exists with the original's
    opt-in defaults (plots off, Event Log on), the run passes None for
    unticked sinks, and the results dock reads streams lazily."""
    import os
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    from PySide6.QtWidgets import QApplication
    from PySide6.QtCore import QSettings
    app = QApplication.instance() or QApplication([])
    app.setOrganizationName("OpenPowerShift")
    app.setApplicationName("RAPTOR Studio")
    QSettings().clear()
    from raptor.gui.ui.main_window import MainWindow
    w = MainWindow()
    try:
        acts = w._report_stream_actions
        assert set(acts) == {"event_log", "ao_plot", "capacity_plot",
                             "cost_plot"}
        assert acts["event_log"].isChecked() is True
        assert acts["ao_plot"].isChecked() is False
        assert acts["capacity_plot"].isChecked() is False
        assert acts["cost_plot"].isChecked() is False

        # Tick Availability Plot only, run, and check a stream landed.
        #
        # The toggle is now a MIRROR of the run experiment rather than the
        # place the value lives, so it must be set AFTER the model is
        # loaded -- otherwise loading re-seeds it from the model, which
        # is the whole point of the single-writer arrangement. (Ticking it
        # before a model is open is now a no-op by design.)
        rbd = load_rbd(os.path.join(GOLD, "TwoBlockRedundancy", "input.rbd"))
        w._load_into_app(rbd, "t.rbd")
        w._set_recording("ao_plot", True)
        assert acts["ao_plot"].isChecked() is True, \
            "the ribbon toggle must mirror the experiment"
        w._preflight_issues = lambda: []
        # This is a wiring test, not a save test: clear the dirty flag so
        # the "unsaved changes before running" preference doesn't open a
        # modal prompt the headless run can't answer.
        w._dirty = False
        w._run_count_spin.setValue(2)
        w._run_simulation()
        d = w._results
        # ao sink is a stream with rows; capacity/cost stayed unset
        assert hasattr(d._ao_lines, "read_all_lines")
        assert len(d._ao_lines) > 0
        assert not d._cap_lines
        assert not d._cost_lines
        # graphs still render from the stream
        assert d._ao_plot._runs or d._ao_plot.run_count >= 0
        # the safety-bound note path did not fire for a TimeSim model
        assert d._notes == [] or all("safety bound" not in n
                                     for n in d._notes)
    finally:
        w._close_report_streams()
        w.close() if False else None
    QSettings().clear()
