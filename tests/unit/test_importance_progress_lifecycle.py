"""A run that includes importance measures: the wait, not the arithmetic.

`test_importance.py` pins the numbers and `test_importance_surfaces.py`
pins the tab/overlay wiring. This file pins the lifecycle reported in
#210/#212/#213:

* the baseline's Final Results appear as soon as the baseline is done,
  while importance is still running;
* importance never runs on the GUI thread, and the event loop keeps
  firing (Help must stay usable);
* the status line/bar describe importance, not the baseline's stale
  `Final results report 100/100`;
* repeated polls are deduplicated and cannot advance while paused;
* the final refresh adds the Importance tab without moving the reader.
"""
from __future__ import annotations

import os
import threading
import time

import pytest

pytest.importorskip("PySide6")

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QTimer
from PySide6.QtWidgets import QApplication

from raptor.core.fileio.rbd import load_rbd
from raptor.gui.ui.main_window import MainWindow

REPO = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
TWO_BLOCK = os.path.join(REPO, "tests", "golden", "TwoBlockRedundancy",
                         "input.rbd")

SAMPLE = {
    "mode": "both", "runs": 100, "batches": 5, "blocks": 2,
    "metrics": {"Availability": {
        "baseline": 0.9,
        "rows": [
            {"block": "A", "block_id": 3, "birnbaum": 0.02, "raw": 1.5,
             "rrw": 1.1, "vesely_fussell": 0.2, "perfect": 0.92,
             "failed": 0.90, "sem": 0.001},
            {"block": "B", "block_id": 4, "birnbaum": 0.30, "raw": 4.0,
             "rrw": 1.4, "vesely_fussell": 0.5, "perfect": 0.95,
             "failed": 0.65, "sem": 0.002},
        ]}},
    "notes": [],
}


@pytest.fixture(scope="module")
def qapp():
    return QApplication.instance() or QApplication([])


def _window(qapp) -> MainWindow:
    win = MainWindow()
    win._load_into_app(load_rbd(TWO_BLOCK), None)
    return win


def _state(**over):
    """The shape `_start_background_run`'s worker leaves behind after the
    baseline, while importance is still running."""
    state = {
        "done": False,
        "progress": (2, 2),
        "paused": False,
        "phase": "importance",
        "baseline_ready": True,
        "importance_total": 5,
        "importance_progress": (2, 5, "perfect:Pump A"),
        # The baseline's last report stage: the exact tuple that used to
        # be repainted over the importance status every 100 ms.
        "stage": ("Final results report", 100, 100),
        "result": (["event"], "", ["Final results"], [], [], [], [], [],
                   "hours"),
        "resume_ev": threading.Event(),
        "stop_ev": threading.Event(),
    }
    state["resume_ev"].set()
    state.update(over)
    return state


def test_baseline_results_are_visible_while_importance_is_still_running(qapp):
    win = _window(qapp)
    win._bg_state = _state()

    win._bg_poll_tick()

    assert win._results.has_results(), "the finished baseline stayed hidden"
    assert win._results._importance is None, "importance has not finished"
    message = win.statusBar().currentMessage()
    assert "Importance" in message
    assert "variant 2 of 5" in message
    assert "Final results report" not in message
    # Variant fraction: (2 - 1) / (5 - 1) = 25%.
    assert win._run_progress.value() == 25
    assert win._run_progress.value() < 100
    win.close()


def test_successive_polls_advance_monotonically_and_never_claim_100(qapp):
    win = _window(qapp)
    state = _state(importance_progress=(2, 5, "perfect:Pump A"))
    win._bg_state = state

    win._bg_poll_tick()
    first = win._run_progress.value()
    state["importance_progress"] = (3, 5, "perfect:Pump B")
    win._bg_poll_tick()
    second = win._run_progress.value()
    state["importance_progress"] = (5, 5, "failed:Pump B")
    win._bg_poll_tick()
    last = win._run_progress.value()

    assert first < second < last, (first, second, last)
    assert last < 100, "the bar must stay below 100% until importance returns"
    message = win.statusBar().currentMessage()
    assert "variant 5 of 5" in message and "failed:Pump B" in message
    assert not state["done"]
    win.close()


def test_pause_does_not_advance_or_rewrite_the_importance_line(qapp):
    win = _window(qapp)
    state = _state()
    win._bg_state = state
    win._bg_poll_tick()                 # presents the baseline once
    before = win._run_progress.value()

    state["paused"] = True
    writes: list[str] = []
    win._statusbar.showMessage = lambda *a, **k: writes.append(
        a[0] if a else "")
    for _ in range(4):                  # several 100 ms polls while paused
        win._bg_poll_tick()

    assert win._run_progress.value() == before
    assert len(writes) == 1, writes    # repeated identical writes are deduped
    assert "paused" in writes[0].lower() and "Importance" in writes[0]
    assert not win._results._importance
    assert not state["done"]
    win.close()


def test_final_refresh_adds_importance_without_losing_the_readers_tab(qapp):
    win = _window(qapp)
    state = _state()
    win._bg_state = state
    win._bg_poll_tick()                 # baseline population

    tabs = win._results._details_tabs
    idx = next(i for i in range(tabs.count())
               if tabs.tabText(i) == "Weak Link")
    tabs.setCurrentIndex(idx)

    state["importance_progress"] = (5, 5, "failed:Pump B")
    state["importance"] = SAMPLE
    state["phase"] = "done"
    state["done"] = True
    win._bg_poll_tick()                 # final refresh

    assert win._results._importance == SAMPLE
    names = [tabs.tabText(i) for i in range(tabs.count())]
    assert "Importance" in names
    assert tabs.tabText(tabs.currentIndex()) == "Weak Link", (
        "the refresh moved the user off the tab they were reading")
    win.close()


def test_importance_runs_off_the_gui_thread_and_the_loop_keeps_beating(
        qapp, monkeypatch):
    from raptor.api import importance as imp_mod

    main_thread = threading.get_ident()
    seen = {"thread": None, "beats": 0}

    def slow_analyse(_rbd, _baseline, *, mode="both", runs=None, workers=1,
                     seed=None, progress=None, stop_check=None):
        seen["thread"] = threading.get_ident()
        if progress is not None:
            progress(2, 3, "perfect:A")
        time.sleep(0.35)                # long enough for several heartbeats
        return SAMPLE

    monkeypatch.setattr(imp_mod, "analyse", slow_analyse)
    win = _window(qapp)
    experiment = win._current_experiment()
    experiment["importance"] = "both"
    win._rbd.experiment = experiment

    heartbeat = QTimer()
    heartbeat.setInterval(20)
    heartbeat.timeout.connect(
        lambda: seen.__setitem__("beats", seen["beats"] + 1))
    heartbeat.start()
    try:
        data = win._importance_for(win._rbd, [object()])
    finally:
        heartbeat.stop()

    assert data == SAMPLE
    assert seen["thread"] is not None
    assert seen["thread"] != main_thread, "importance ran on the GUI thread"
    assert seen["beats"] > 0, "the event loop was not pumped while it ran"
    win.close()


def test_a_synchronous_run_shows_the_baseline_before_slow_importance(
        qapp, monkeypatch):
    """End-to-end on the default (synchronous/animated) Run path."""
    from raptor.api import importance as imp_mod

    started = threading.Event()

    def slow_analyse(_rbd, _baseline, *, mode="both", runs=None, workers=1,
                     seed=None, progress=None, stop_check=None):
        started.set()
        time.sleep(0.4)
        return SAMPLE

    monkeypatch.setattr(imp_mod, "analyse", slow_analyse)
    win = _window(qapp)
    win._set_exp_field("trials", 2)
    experiment = win._current_experiment()
    experiment["importance"] = "both"
    win._rbd.experiment = experiment
    win._dirty = False

    checks = {"beats": 0, "baseline": False, "importance": None,
              "status": ""}
    heartbeat = QTimer()
    heartbeat.setInterval(20)

    def look():
        checks["beats"] += 1
        if (started.is_set() and win._results.has_results()
                and not checks["baseline"]):
            checks["baseline"] = True
            checks["importance"] = win._results._importance
            checks["status"] = win.statusBar().currentMessage()

    heartbeat.timeout.connect(look)
    heartbeat.start()
    try:
        win._run_simulation()
    finally:
        heartbeat.stop()

    assert checks["beats"] > 0
    assert checks["baseline"], "Final Results were withheld until importance"
    assert checks["importance"] is None, "importance is still running here"
    assert "Importance" in checks["status"]
    assert win._results._importance == SAMPLE
    win._dirty = False
    win.close()
