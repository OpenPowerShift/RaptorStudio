"""Cores can be given back to the machine while a batch is running (#11).

A multi-core background run keeps `workers_now` in its state; the Run panel's
Cores field stays editable and writes it, and the engine reads it between runs.
"""
from __future__ import annotations

import threading
import time

import pytest
from PySide6.QtWidgets import QApplication

pytestmark = pytest.mark.gui

MODEL = "tests/golden/TwoBlockRedundancy/input.rbd"


@pytest.fixture
def win():
    from raptor.core.fileio.rbd import load_rbd
    from raptor.gui.ui.main_window import MainWindow
    QApplication.instance() or QApplication([])
    w = MainWindow()
    w._load_into_app(load_rbd(MODEL), MODEL)
    w._live_sim_action.setChecked(False)
    yield w
    w._run_in_progress = False
    w._bg_state = None
    w._dirty = False
    w.close()


@pytest.fixture
def keep_setting():
    from raptor.gui import cores
    from raptor.gui.ui import settings
    old = settings.get_int(cores.KEY, 0)
    yield
    settings.set_value(cores.KEY, old)


def _running(win, workers, now=None, done=False):
    win._run_in_progress = True
    win._bg_state = {"workers": workers, "workers_now": now or workers,
                     "done": done, "resume_ev": _set(), "stop_ev": threading.Event(),
                     "progress": (10, 20)}


def _set():
    e = threading.Event()
    e.set()
    return e


def test_the_cores_field_stays_editable_during_a_multi_core_run(win):
    _running(win, 4)
    win._sync_cores_box()
    panel = win._run_panel
    assert panel._cores.isEnabled()
    assert "while the run is going" in panel._cores_note.text()
    assert "give cores back" in panel._cores.toolTip()


@pytest.mark.parametrize("state", [{"workers": 1}, {"workers": 4, "done": True}])
def test_it_stays_locked_when_there_is_nothing_to_adjust(win, state):
    _running(win, state["workers"], done=state.get("done", False))
    win._sync_cores_box()
    assert not win._run_panel._cores.isEnabled()


def test_it_is_editable_between_runs_as_before(win):
    win._run_in_progress = False
    win._bg_state = None
    win._sync_cores_box()
    assert win._run_panel._cores.isEnabled()


def test_changing_cores_during_a_run_lowers_and_restores_the_limit(win, keep_setting):
    _running(win, 4)
    win._on_cores_changed(2)
    assert win._bg_state["workers_now"] == 2
    win._on_cores_changed(1)
    assert win._bg_state["workers_now"] == 1
    win._on_cores_changed(3)
    assert win._bg_state["workers_now"] == 3
    win._on_cores_changed(99)              # cannot exceed what the run started with
    assert win._bg_state["workers_now"] == 4


def test_the_change_is_also_the_setting_for_the_next_run(win, keep_setting):
    from raptor.gui import cores
    _running(win, 4)
    win._on_cores_changed(2)
    assert cores.chosen() == min(2, cores.available())


def test_nothing_is_touched_when_no_run_is_active(win, keep_setting):
    win._bg_state = None
    win._on_cores_changed(2)               # no error, just the setting


def test_the_status_message_says_how_many_of_the_cores_are_busy(win):
    _running(win, 4, now=2)
    win._bg_poll_tick()
    assert "2 of 4 cores" in win._statusbar.currentMessage()
    _running(win, 4, now=4)
    win._bg_last_progress = None
    win._bg_poll_tick()
    assert "4 cores" in win._statusbar.currentMessage()
    assert "of 4 cores" not in win._statusbar.currentMessage()


def test_a_background_run_hands_the_engine_a_live_limit(win, monkeypatch):
    """The wiring: the limit the engine polls is the state's `workers_now`."""
    from raptor.gui import cores
    from raptor.gui.ui import main_window
    seen = {}
    release = threading.Event()

    def fake_run(model, **kwargs):
        seen["limit"] = kwargs["worker_limit"]
        seen["workers"] = kwargs["num_workers"]
        release.wait(10)
        return [[]], [], []

    monkeypatch.setattr(main_window, "run_simulation_with_reports", fake_run)
    monkeypatch.setattr(cores, "chosen", lambda: 3)
    win._start_background_run(4)
    end = time.monotonic() + 15
    while "limit" not in seen and time.monotonic() < end:
        QApplication.instance().processEvents()
        time.sleep(0.02)
    try:
        assert seen["workers"] == 3 and seen["limit"]() == 3
        win._bg_state["workers_now"] = 1
        assert seen["limit"]() == 1
    finally:
        release.set()
        end = time.monotonic() + 15
        while not win._bg_state.get("done") and time.monotonic() < end:
            QApplication.instance().processEvents()
            time.sleep(0.02)
