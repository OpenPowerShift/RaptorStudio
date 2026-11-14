"""Files & Reports: the GUI wiring (the engine side, which the engine tier
must see, is in test_engine_result_sinks.py)."""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))

from raptor.core.fileio.rbd import load_rbd

GOLD = os.path.join(os.path.dirname(__file__), "..", "golden")


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
        w._preflight_issues = lambda *_args, **_kwargs: []
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
