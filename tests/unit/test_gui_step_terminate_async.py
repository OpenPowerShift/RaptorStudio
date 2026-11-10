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
from raptor.gui.ui import main_window as main_window_module
from raptor.gui.ui.main_window import MainWindow

REPO = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
FIXTURE = os.path.join(REPO, "tests", "golden", "TwoBlockRedundancy", "input.rbd")


@pytest.fixture(scope="module")
def qapp():
    return QApplication.instance() or QApplication([])


def _drain_finalize(window, qapp, timeout: float = 10.0) -> None:
    """Discard and join any finalize worker before a test tears down."""
    if window._step_finalize_state is not None:
        window._discard_step_finalize()
    thread = window._step_finalize_thread
    deadline = time.monotonic() + timeout
    while thread is not None and thread.is_alive() \
            and time.monotonic() < deadline:
        qapp.processEvents()
        thread.join(0.02)
    if window._step_finalize_state is not None \
            and window._step_finalize_state.get("done"):
        window._step_finalize_poll_tick()
    qapp.processEvents()


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
    _drain_finalize(w, qapp)
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
    view = window._diagram_dock.view
    assert view._live_state_cache is not None

    original = session.early_terminate

    def slow_early_terminate():
        time.sleep(0.4)
        return original()

    monkeypatch.setattr(session, "early_terminate", slow_early_terminate)
    original_final_report = main_window_module.build_final_results_report

    def slow_final_report(*args, **kwargs):
        time.sleep(0.2)
        return original_final_report(*args, **kwargs)

    monkeypatch.setattr(main_window_module, "build_final_results_report",
                        slow_final_report)

    entered = []
    monkeypatch.setattr(view, "enter_level",
                        lambda *a, **k: entered.append("enter"))
    monkeypatch.setattr(view, "go_home",
                        lambda *a, **k: entered.append("home"))
    monkeypatch.setattr(view, "locate_element",
                        lambda *a, **k: entered.append("locate") or False)
    monkeypatch.setattr(view, "_set_level",
                        lambda *a, **k: entered.append("level"))

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
    assert window._run_or_step_active(), \
        "run settings must stay locked while finalize runs"
    assert not window._analysis_actions["weakAnalysis"].isEnabled()
    first_thread = window._step_finalize_thread
    window._on_step_terminate()
    assert window._step_finalize_state is state
    assert window._step_finalize_thread is first_thread, \
        "a second finalize worker was started"

    _wait_for_finalize(window, qapp)
    assert window._analysis_actions["weakAnalysis"].isEnabled()
    heartbeat.stop()

    assert len(ticks) >= 5, "the event loop never turned during finalize"
    assert not entered, "Terminate navigated hierarchy levels"
    assert window._step_session is None
    assert window._step_finalize_state is None
    assert view._live_state_cache is None, \
        "Terminate must clear Animate-off live colours"
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

    window._dirty = False
    window.close()
    assert not window.isVisible()
    assert window._step_finalize_state is None
    assert window._run_in_progress is False
    thread.join(10.0)
    qapp.processEvents()
    assert not presented


def test_editing_while_finalizing_discards_with_a_message(window, qapp,
                                                          monkeypatch):
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
    assert window._run_or_step_active()

    window._stop_run_for_edit()
    assert window._step_finalize_state is not None
    assert window._step_finalize_state["discard"] is True
    assert "discarded" in window._statusbar.currentMessage().lower()
    _wait_for_finalize(window, qapp)
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
    # must not be painted over whatever is on screen now. The finalize
    # state stays alive (and locked) until the worker actually exits.
    window._invalidate_step_session()
    state = window._step_finalize_state
    assert state is not None and state["discard"] is True
    assert window._run_in_progress is True
    deadline = time.monotonic() + 10.0
    while window._step_finalize_state is not None \
            and time.monotonic() < deadline:
        qapp.processEvents()
        thread.join(0.02)
    qapp.processEvents()
    assert window._step_finalize_state is None
    assert window._run_in_progress is False
    assert not presented, "discarded finalize presented its results"
    assert not window._results._report_data
