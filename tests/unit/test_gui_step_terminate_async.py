"""Terminate must not freeze the GUI while a stepped run finalizes.

`StepSession.early_terminate()` and the report/results construction run on
a worker thread; a QTimer on the GUI thread presents the finished results.
The event loop must keep turning during a deliberately slow finalize, and
the terminate path must never navigate hierarchy levels.
"""
from __future__ import annotations

import os
import time

import pytest

pytest.importorskip("PySide6")
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QTimer
from PySide6.QtWidgets import QApplication

from raptor.core.fileio.rbd import load_rbd
from raptor.gui.ui import msgbox
from raptor.gui.ui.main_window import MainWindow

REPO = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
FIXTURE = os.path.join(REPO, "tests", "golden", "TwoBlockRedundancy", "input.rbd")


@pytest.fixture(scope="module")
def qapp():
    return QApplication.instance() or QApplication([])


@pytest.fixture
def window(qapp):
    w = MainWindow()
    rbd = load_rbd(FIXTURE)
    rbd.section("SYSTEM_DATA").rows[0]["TermCondition"] = "200"
    rbd.section("SYSTEM_DATA").rows[0]["NumRuns"] = "1"
    w._load_into_app(rbd, None)
    w.show()
    qapp.processEvents()
    yield w
    w._discard_step_finalize()
    w._invalidate_step_session()
    w._dirty = False
    w.close()
    w.deleteLater()
    qapp.processEvents()


def _wait_for_finalize(window, qapp, timeout: float = 10.0) -> None:
    deadline = time.monotonic() + timeout
    while window._step_finalize_state is not None \
            and time.monotonic() < deadline:
        qapp.processEvents()
        time.sleep(0.005)
    qapp.processEvents()
    assert window._step_finalize_state is None, "finalize worker never finished"


def test_terminate_keeps_the_event_loop_alive(window, qapp, monkeypatch):
    window._on_step()
    session = window._step_session
    assert session is not None

    original = session.early_terminate

    def slow_early_terminate():
        time.sleep(0.4)
        return original()

    monkeypatch.setattr(session, "early_terminate", slow_early_terminate)

    entered = []
    view = window._diagram_dock.view
    monkeypatch.setattr(view, "enter_level",
                        lambda *a, **k: entered.append("enter"))
    monkeypatch.setattr(view, "go_home",
                        lambda *a, **k: entered.append("home"))

    ticks = []
    heartbeat = QTimer()
    heartbeat.setInterval(10)
    heartbeat.timeout.connect(lambda: ticks.append(time.monotonic()))
    heartbeat.start()

    started = time.monotonic()
    window._on_step_terminate()
    returned = time.monotonic() - started
    assert returned < 0.25, "Terminate blocked the GUI thread"
    assert "Finalizing" in window._statusbar.currentMessage()
    state = window._step_finalize_state
    assert state is not None and state["session"] is session, \
        "the session must stay alive until finalize completes"

    _wait_for_finalize(window, qapp)
    heartbeat.stop()

    assert len(ticks) >= 5, "the event loop never turned during finalize"
    assert not entered, "Terminate navigated hierarchy levels"
    assert window._step_session is None
    assert window._step_finalize_state is None
    assert len(window._results._report_data) == 1
    assert "Early Terminate" in window._statusbar.currentMessage()


def test_a_failed_finalize_is_reported_and_unlocks(window, qapp, monkeypatch):
    window._on_step()
    session = window._step_session

    def fail():
        time.sleep(0.05)
        raise RuntimeError("fake finalize failure")

    monkeypatch.setattr(session, "early_terminate", fail)
    errors = []
    monkeypatch.setattr(msgbox, "critical",
                        lambda *a, **k: errors.append(a))

    window._on_step_terminate()
    _wait_for_finalize(window, qapp)

    assert errors and errors[0][1] == "Terminate Failed"
    assert window._step_session is None
    assert window._run_in_progress is False
    assert "failed" in window._statusbar.currentMessage().lower()


def test_closing_while_finalizing_is_safe(window, qapp, monkeypatch):
    window._on_step()
    session = window._step_session
    original = session.early_terminate

    def slow_early_terminate():
        time.sleep(0.25)
        return original()

    monkeypatch.setattr(session, "early_terminate", slow_early_terminate)
    presented = []
    monkeypatch.setattr(window, "_present_run_results",
                        lambda *a, **k: presented.append(a))
    window._on_step_terminate()
    thread = window._step_finalize_thread
    assert thread is not None and thread.is_alive()

    assert window._stop_run_for_close() is True
    assert window._step_finalize_state is None
    assert window._run_in_progress is False
    thread.join(10.0)
    qapp.processEvents()
    assert not presented


def test_discarding_while_finalizing_drops_the_results(window, qapp,
                                                      monkeypatch):
    window._on_step()
    session = window._step_session
    original = session.early_terminate

    def slow_early_terminate():
        time.sleep(0.3)
        return original()

    monkeypatch.setattr(session, "early_terminate", slow_early_terminate)
    presented = []
    monkeypatch.setattr(window, "_present_run_results",
                        lambda *a, **k: presented.append(a))
    window._on_step_terminate()
    thread = window._step_finalize_thread
    assert thread is not None and thread.is_alive()

    # The model is being discarded: the worker may finish, but its result
    # must not be painted over whatever is on screen now.
    window._invalidate_step_session()
    assert window._step_finalize_state is None
    assert window._run_in_progress is False
    deadline = time.monotonic() + 10.0
    while thread.is_alive() and time.monotonic() < deadline:
        qapp.processEvents()
        thread.join(0.02)
    qapp.processEvents()
    assert not presented, "discarded finalize presented its results"
    assert not window._results._report_data
