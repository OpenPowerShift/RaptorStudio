"""A run ended by the anti-runaway bound asks whether to keep going with the rest of the batch."""
from __future__ import annotations

import os
from pathlib import Path

import pytest

pytest.importorskip("PySide6")
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication

from raptor.core.engine import driver
from raptor.core.fileio.rbd import load_rbd
from raptor.gui.ui.main_window import MainWindow

pytestmark = pytest.mark.gui
GOLD = Path(__file__).resolve().parents[1] / "golden"


@pytest.fixture(scope="module")
def qapp():
    return QApplication.instance() or QApplication([])


@pytest.fixture
def win(qapp, monkeypatch):
    monkeypatch.setattr(driver, "SAFETY_BOUND", 300.0)
    # A multi-core run's worker processes read the bound from the
    # environment when they start; they never see the monkeypatch.
    monkeypatch.setenv("RAPTOR_SAFETY_BOUND", "300")
    # These tests are about the runaway prompt, not the long-batch guard.
    monkeypatch.setattr(MainWindow, "_confirm_long_batch",
                        lambda self, n: True)
    w = MainWindow()
    rbd = load_rbd(str(GOLD / "TwoBlockRedundancy" / "input.rbd"))
    row = rbd.section("SYSTEM_DATA").rows[0]
    row["TerminationType"], row["TermCondition"], row["NumRuns"] = "FailSim", "1000000", "4"
    w._load_into_app(rbd, None)
    yield w
    w._dirty = False


def _asked(win, monkeypatch, answer):
    seen = []
    monkeypatch.setattr(win, "_ask_keep_going_after_runaway",
                        lambda done, total: (seen.append((done, total)), answer)[1])
    return seen


def test_stopping_after_the_first_runaway_keeps_that_run_only(win, monkeypatch):
    seen = _asked(win, monkeypatch, False)
    win._run_simulation()
    assert seen == [(1, 4)]
    assert len(win._results._event_logs) == 1


def test_keeping_going_runs_every_run_and_asks_once(win, monkeypatch):
    seen = _asked(win, monkeypatch, True)
    win._run_simulation()
    assert seen == [(1, 4)]
    assert len(win._results._report_data) == 4


def _wait_idle(win, qapp, seconds=60):
    import time
    end = time.monotonic() + seconds
    while win._run_in_progress and time.monotonic() < end:
        qapp.processEvents()
        time.sleep(0.02)
    qapp.processEvents()
    assert not win._run_in_progress, "the background run never finished"


@pytest.mark.parametrize("answer,runs", [(False, 1), (True, 4)])
def test_a_background_batch_asks_on_the_gui_thread_and_obeys(win, qapp, monkeypatch, answer, runs):
    from raptor.gui import cores
    monkeypatch.setattr(cores, "chosen", lambda: 1)
    seen = _asked(win, monkeypatch, answer)
    win._live_sim_action.setChecked(False)
    win._on_run()
    _wait_idle(win, qapp)
    assert seen == [(1, 4)]
    assert len(win._results._report_data) == runs


@pytest.mark.parametrize("answer", [False, True])
def test_a_multi_core_batch_asks_too(win, qapp, monkeypatch, answer):
    """On several cores a worker flags the runaway run at once, the GUI
    asks once, and Stop really stops (it used to run every chunk to the
    bound, and Stop waited for all of them)."""
    from raptor.gui import cores
    monkeypatch.setattr(cores, "chosen", lambda: 2)
    seen = _asked(win, monkeypatch, answer)
    win._live_sim_action.setChecked(False)
    win._on_run()
    _wait_idle(win, qapp)
    assert len(seen) == 1 and seen[0][1] == 4
    kept = len(win._results._report_data)
    assert kept == 4 if answer else kept < 4
