"""Simulate > Batch > Recommend: run in the background, list, apply, undo."""
from __future__ import annotations

import time

import pytest
from PySide6.QtWidgets import QApplication

pytestmark = pytest.mark.gui


def _rbd():
    from raptor.api import build
    return build.build_from_spec({
        "blocks": [
            {"name": "Pump", "fail_dist": "Exponential", "fail_params": [50],
             "repair_dist": "Exponential", "repair_params": [20]},
            {"name": "Valve", "fail_dist": "Exponential", "fail_params": [400],
             "repair_dist": "Exponential", "repair_params": [2]},
        ],
        "nodes": [{"name": "Start", "type": 1}, {"name": "End", "type": 3}],
        "links": [{"from": "Start", "to": "Pump"}, {"from": "Pump", "to": "Valve"},
                  {"from": "Valve", "to": "End"}],
        "experiment": {"trials": 30, "termination_type": "TimeSim",
                       "term_condition": 2000.0}})


@pytest.fixture
def win(tmp_path):
    from raptor.gui.ui import settings
    from raptor.gui.ui.main_window import MainWindow
    QApplication.instance() or QApplication([])
    settings.set_value("files/save_before_run", "never")
    w = MainWindow()
    w._load_into_app(_rbd(), str(tmp_path / "m.rbd"))
    yield w
    w._dirty = False
    w.close()


def _wait(dlg, seconds: float = 120.0) -> None:
    end = time.monotonic() + seconds
    while dlg._job is not None and not dlg._job.done and time.monotonic() < end:
        QApplication.processEvents()
        time.sleep(0.02)
    dlg._poll()


def _repair_param(w) -> str:
    rows = w._diagram_dock.view._rbd_ref.section("BLOCK_DATA").rows
    return next(r for r in rows if r.get("blockName") == "Pump")["RParam1"]


def test_the_command_opens_one_window_that_tests_and_applies(win):
    action = win._recommend_action
    assert action.text() == "Recommend…"
    dlg = win._open_recommend_dialog()
    assert win._open_recommend_dialog() is dlg
    dlg._top.setValue(1)
    dlg._start()
    assert not dlg._run_btn.isEnabled() and dlg._stop_btn.isEnabled()
    _wait(dlg)
    assert dlg._job.error is None, dlg._job.error
    assert dlg.table.rowCount() == len(dlg.result.rows) >= 1
    names = [dlg.table.item(i, 0).text() for i in range(dlg.table.rowCount())]
    assert "Pump: halve the repair time" in names
    assert dlg._run_btn.isEnabled() and not dlg._stop_btn.isEnabled()
    assert dlg.caption.text()
    assert "change: block:Pump.repair_params=10,0" in dlg.text()
    assert "No verdict weighs cost" in dlg.caption.text()
    assert dlg._job.min_gain is None          # the default bar

    before = float(_repair_param(win))
    dlg.table.selectRow(names.index("Pump: halve the repair time"))
    assert dlg._apply_btn.isEnabled()
    assert dlg.apply_selected()
    assert float(_repair_param(win)) == pytest.approx(before / 2)
    assert not dlg._apply_btn.isEnabled()          # applied once
    assert "Applied" in dlg.caption.text()
    win._undo_stack.undo()
    assert float(_repair_param(win)) == pytest.approx(before)
    dlg.close()


def test_stop_ends_the_job_and_frees_the_buttons(win):
    dlg = win._open_recommend_dialog()
    dlg._trials.setValue(2000)
    dlg._start()
    dlg._stop()
    _wait(dlg)
    assert dlg._job.done
    assert dlg._run_btn.isEnabled() and not dlg._stop_btn.isEnabled()
    dlg.close()
