"""Cores in plain sight, Run vs Animate, and the Sweep and Compare windows.

Animate is the one-core run you watch; Run with Animate off is a full-speed
background run on the cores in Simulate > Run > Cores. The count shows in
the ribbon box, the Run panel's ready line and the status bar.
"""
from __future__ import annotations

import time

import pytest
from PySide6.QtWidgets import QApplication

pytestmark = pytest.mark.gui

MODEL = "tests/golden/TwoBlockRedundancy/input.rbd"


@pytest.fixture
def win(monkeypatch):
    from raptor.core.fileio.rbd import load_rbd
    from raptor.gui.ui.main_window import MainWindow
    QApplication.instance() or QApplication([])
    w = MainWindow()
    w._load_into_app(load_rbd(MODEL), MODEL)
    yield w
    for attr in ("_sweep_dialog", "_compare_dialog"):
        d = getattr(w, attr, None)
        if d is not None:
            d.close()
    w._dirty = False
    w.close()


def _wait(pred, seconds=60):
    app = QApplication.instance()
    end = time.monotonic() + seconds
    while not pred() and time.monotonic() < end:
        app.processEvents()
        time.sleep(0.02)
    app.processEvents()
    assert pred()


def test_cores_choice(monkeypatch):
    from raptor.gui import cores
    monkeypatch.setattr(cores, "available", lambda: 8)
    monkeypatch.setattr(cores.settings, "get_int", lambda k, d=0: 0)
    assert cores.chosen() == 8                  # automatic: every core
    monkeypatch.setattr(cores.settings, "get_int", lambda k, d=0: 99)
    assert cores.chosen() == 8                  # capped at the machine
    assert "1 core" in cores.describe(True)
    assert "8 of 8" in cores.describe(False)


def test_the_core_count_is_shown_and_follows_animate(win):
    from raptor.gui import cores
    win._live_sim_action.setChecked(False)
    n = cores.chosen()
    assert win._cores_spin.isEnabled() and win._cores_spin.value() == n
    assert win._cores_chip.text() == f"{n}/{cores.available()} cores"
    assert f"{n} of {cores.available()}" in win._run_panel._readiness.text()
    win._live_sim_action.setChecked(True)
    assert not win._cores_spin.isEnabled()
    assert "Animate" in win._cores_spin.toolTip()
    assert win._cores_chip.text() == f"1/{cores.available()} cores (Animate)"
    assert "1 core" in win._run_panel._readiness.text()


def test_cores_has_one_editor_and_a_read_only_ribbon_mirror(win):
    from raptor.gui import cores
    from raptor.gui.ui import settings
    old = settings.get_int(cores.KEY, 0)
    try:
        assert win._cores_spin.isReadOnly()
        assert not win._run_panel._cores.isReadOnly()
        win._live_sim_action.setChecked(False)
        settings.set_value(cores.KEY, 1)
        win._sync_cores_box()
        assert win._cores_chip.text() == f"1/{cores.available()} cores"
        assert win._run_panel._cores.value() == 1
    finally:
        settings.set_value(cores.KEY, old)
        win._sync_cores_box()


def test_run_goes_to_the_background_unless_animating(win, monkeypatch):
    calls = []
    monkeypatch.setattr(win, "_start_background_run",
                        lambda n: calls.append(("bg", n)))
    win._live_sim_action.setChecked(False)
    win._on_run()
    assert calls and calls[0][0] == "bg"
    assert not getattr(win, "_run_full_speed", False)


def test_sweep_lists_rates_blocks_and_values():
    from raptor.core.fileio.rbds import load_rbds
    from raptor.gui.ui.batch_dialogs import element_choices, sweep_values
    rbd = load_rbds("new_docs/models/HospitalPower.rbds")
    choices = dict(element_choices(rbd))
    assert choices["Rate  @GenMTBF"][0].path == "rate:GenMTBF"
    grid = choices["Block  Utility_Grid"]
    assert grid[0].path == "block:1.fail_params[0]"
    assert "linked to @UtilityMTBF" in grid[0].note
    assert sweep_values(1, 3, 5, False) == ["1", "1.5", "2", "2.5", "3"]
    assert sweep_values(1, 3, 9, True) == ["1", "2", "3"]


def test_best_is_every_tied_value():
    from raptor.gui.ui.batch_dialogs import best_indices, fmt
    assert best_indices([1.0, 1.0, 0.9], True) == {0, 1}
    assert best_indices([5.0, 5.0], False) == set()
    assert best_indices([None, 2.0, 3.0], False) == {1}
    assert fmt(0.99999) == "0.99999"


class _DoneJob:
    """A finished BatchJob's public shape, without running the engine."""

    done = True

    def __init__(self, results):
        self.results = results

    def is_running(self):
        return False

    def cleanup(self):
        pass


def _compare_summary(**extra):
    base = {"Availability": {"mean": 0.9}}
    base.update(extra)
    return base


def test_compare_shows_optional_capacity_and_cost_rows(win):
    """Capacity/cost rows appear when any compared run has them; a run
    with the analysis off leaves a blank, not a false zero (issue #209)."""
    from raptor.gui.ui.batch_dialogs import (CompareDialog, METRICS,
                                            OPTIONAL_METRICS)
    d = CompareDialog(win)
    try:
        d._files = ["b.rbd", "c.rbd"]
        d._job = _DoneJob([
            {"label": "A", "summary": _compare_summary(
                Units_Produced={"mean": 100.0},
                Mean_Flow={"mean": 10.0},
                Mean_Capacity={"mean": 12.0},
                Capacity_Weighted_Availability={"mean": 0.5},
                Lost_Flow={"mean": 5.0},
                Grand_Total={"mean": 100.0},
                System_Down_Cost={"mean": 20.0},
                System_Lost_Flow_Cost={"mean": 10.0})},
            {"label": "B", "summary": _compare_summary()},
            {"label": "C", "summary": _compare_summary(
                Units_Produced={"mean": 200.0},
                Mean_Flow={"mean": 20.0},
                Mean_Capacity={"mean": 24.0},
                Capacity_Weighted_Availability={"mean": 0.8},
                Lost_Flow={"mean": 3.0},
                Grand_Total={"mean": 50.0},
                System_Down_Cost={"mean": 10.0},
                System_Lost_Flow_Cost={"mean": 4.0})},
        ])
        d._show_results()

        headers = [d._table.verticalHeaderItem(r).text()
                   for r in range(d._table.rowCount())]
        assert headers[:len(METRICS)] == [lbl for _, lbl, _ in METRICS]
        assert headers[len(METRICS):] == [
            lbl for _, lbl, _ in OPTIONAL_METRICS]
        rows = {label: i for i, label in enumerate(headers)}
        for label in ("Mean flow", "Lost flow", "Grand total",
                      "System down cost"):
            assert d._table.item(rows[label], 1).text() == "", label
        # C wins both the higher-is-better flow row and the
        # lower-is-better lost-flow and cost rows.
        for label in ("Mean flow", "Lost flow", "Grand total",
                      "System down cost"):
            assert d._table.item(rows[label], 2).font().bold(), label
            assert not d._table.item(rows[label], 1).font().bold(), label

        csv_headers, csv_rows = d._csv()
        assert csv_headers == ["metric", "A", "B", "C"]
        metric_rows = {row[0]: row[1:] for row in csv_rows}
        assert metric_rows["Mean_Flow"] == ["10.0", "", "20.0"]
        assert metric_rows["Grand_Total"] == ["100.0", "", "50.0"]
    finally:
        d.close()


def test_compare_hides_capacity_and_cost_without_analysis(win):
    from raptor.gui.ui.batch_dialogs import CompareDialog, METRICS
    d = CompareDialog(win)
    try:
        d._files = ["b.rbd"]
        d._job = _DoneJob([
            {"label": "A", "summary": _compare_summary()},
            {"label": "B", "summary": _compare_summary()},
        ])
        d._show_results()
        assert d._table.rowCount() == len(METRICS)
        assert [row[0] for row in d._csv()[1]] == [k for k, _, _ in METRICS]
    finally:
        d.close()


def test_a_sweep_runs_on_a_copy(win, monkeypatch):
    from raptor.gui import cores
    monkeypatch.setattr(cores, "chosen", lambda: 1)
    d = win._open_sweep_dialog()
    assert d.select("Block  Block_1", "Failure") or d._choices
    d._trials.setValue(3)
    d._points.setValue(2)
    before = win._rbd.section("BLOCK_DATA").rows[1].get("FParam1")
    d._start()
    _wait(lambda: d._job.done)
    d._poll()
    assert len(d._job.results) == 2
    assert all(not r.get("error") for r in d._job.results), d._job.results
    assert d._table.rowCount() == 2
    assert d._chart.points
    assert win._rbd.section("BLOCK_DATA").rows[1].get("FParam1") == before


def test_compare_needs_another_model_then_runs(win, monkeypatch, tmp_path):
    import shutil
    from raptor.gui import cores
    monkeypatch.setattr(cores, "chosen", lambda: 1)
    d = win._open_compare_dialog()
    assert "at least one more" in d._plan.text()
    d.add_paths([MODEL])                     # this model: refused
    assert d._files == [] and "already" in d._plan.text()
    copy = tmp_path / "input.rbd"
    shutil.copy(MODEL, copy)
    d.add_paths([str(copy)])
    d._trials.setValue(3)
    d._start()
    _wait(lambda: d._job.done)
    d._poll()
    assert [r["label"] for r in d._job.results] == ["This model",
                                                    "input.rbd"]
    assert d._table.columnCount() == 2
    headers, rows = d._csv()
    assert headers[0] == "metric" and rows[0][0] == "Availability"


def test_a_multi_core_run_can_be_stopped_and_says_why_jump_is_off(
        win, monkeypatch):
    from raptor.gui import cores
    monkeypatch.setattr(cores, "chosen", lambda: 2)
    win._live_sim_action.setChecked(False)
    win._run_count_spin.setValue(400)
    win._run_count_spin.editingFinished.emit()   # a real edit now
    win._dirty = False                           # (no save-first prompt)
    win._on_run()
    try:
        assert win._command_state().run == "background"
        assert win._bg_state["workers"] == 2
        win._refresh_commands()
        assert win._stop_run_action.isEnabled()
        assert not win._jump_action.isEnabled()
        assert "several cores" in win._jump_action.toolTip()
    finally:
        win._stop_run_action.trigger()
        _wait(lambda: not win._run_in_progress)


def test_stopping_a_sweep_does_not_report_an_error(win, monkeypatch):
    """The point in flight when Stop is pressed used to come back as an
    "error" row (RunCancelledError) and count as a failure."""
    from raptor.gui.ui.batch_dialogs import BatchJob
    import threading
    job = BatchJob.__new__(BatchJob)
    job.results, job.done, job.current = [], False, ""
    job.abort = threading.Event()
    job.abort.set()
    import tempfile
    job._tmp = tempfile.TemporaryDirectory()
    job._models = [("1", win._model_for_engine(copy=True), [])]
    job.num_runs, job.workers = 2, 1
    job._work()
    assert job.results == [] and job.done


def test_sweep_values_are_round_and_bad_values_refused():
    from raptor.core.fileio.rbds import load_rbds
    from raptor.gui.ui.batch_dialogs import (element_choices, invalid_value,
                                             sweep_values)
    assert sweep_values(500, 3000, 8, False)[1] == "857.143"
    rbd = load_rbds("new_docs/models/Espresso1.rbds")
    pump = dict(element_choices(rbd))["Block  Pump_A"][0]
    assert invalid_value(rbd, pump, ["100", "200"]) is None
    why = invalid_value(rbd, pump, ["100", "-5"])
    assert why and "-5" in why


def test_reopening_a_window_keeps_its_results_until_the_model_changes(win):
    d = win._open_sweep_dialog()
    d._trials.setValue(7)
    assert win._open_sweep_dialog() is d
    assert d._trials.value() == 7
    view = win._diagram_dock.view
    view.set_block_fail_params(next(iter(view.blocks)), [123, 0, 0])
    assert win._open_sweep_dialog() is not d


def test_compare_names_two_files_called_the_same(tmp_path):
    from raptor.gui.ui.batch_dialogs import model_labels
    a = tmp_path / "v1" / "m.rbds"
    b = tmp_path / "v2" / "m.rbds"
    assert model_labels([str(a), str(b), "x/other.rbds"]) == [
        "v1/m.rbds", "v2/m.rbds", "other.rbds"]


def test_the_trials_box_on_the_ribbon_is_a_read_only_mirror(win):
    spin = win._run_count_spin
    assert spin.isReadOnly()
    win._run_panel._trials.setValue(37)
    win._run_panel._trials.editingFinished.emit()
    assert win._current_experiment()["trials"] == 37
    assert spin.value() == 37


def test_compare_hands_a_model_to_the_differences_window(win, tmp_path):
    import shutil
    copy = tmp_path / "other.rbd"
    shutil.copy(MODEL, copy)
    d = win._open_compare_dialog()
    d.add_paths([str(copy)])
    d._list.setCurrentRow(1)
    assert d._whats_diff.isEnabled()
    d._open_differences()
    diff = win._diff_dialog
    assert diff._other.isChecked() and diff._other_path == str(copy)


def test_closing_a_running_sweep_leaves_it_ready_to_run_again(win, monkeypatch):
    """Closed while running, the window came back with Run greyed out and
    Stop live for good: the poll that resets them had been stopped."""
    from raptor.gui import cores
    monkeypatch.setattr(cores, "chosen", lambda: 1)
    d = win._open_sweep_dialog()
    d._trials.setValue(20_000)
    d._points.setValue(5)
    d._start()
    d.close()
    assert win._open_sweep_dialog() is d
    assert d._run_btn.isEnabled() and not d._stop_btn.isEnabled()
    assert d._progress.format().startswith("Stopped")
