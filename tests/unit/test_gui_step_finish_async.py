"""Debug ▸ Finish must not freeze the window (#386).

`StepSession.finish_all()` and the report construction run on a worker
thread, polled by the same timer as Terminate; the GUI thread keeps turning,
shows the run progress and estimate, and Stop ends the Finish the way it ends
a batch. The results must be the ones the old synchronous Finish gave.
"""
from __future__ import annotations

import copy
import os
import threading
import time

import pytest

pytest.importorskip("PySide6")
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QTimer
from PySide6.QtWidgets import QApplication

from raptor.core.engine.step_session import StepSession
from raptor.core.fileio.rbd import load_rbd
from raptor.gui.ui import msgbox
from raptor.gui.ui.main_window import MainWindow

REPO = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
FIXTURE = os.path.join(REPO, "tests", "golden", "TwoBlockRedundancy", "input.rbd")
RUNS = 3


@pytest.fixture(scope="module")
def qapp():
    return QApplication.instance() or QApplication([])


@pytest.fixture
def window(qapp):
    w = MainWindow()
    rbd = load_rbd(FIXTURE)
    rbd.section("SYSTEM_DATA").rows[0]["TermCondition"] = "200"
    rbd.section("SYSTEM_DATA").rows[0]["NumRuns"] = str(RUNS)
    w._load_into_app(rbd, None)
    w.show()
    qapp.processEvents()
    yield w
    if w._step_finalize_state is not None:
        w._discard_step_finalize()
    assert w._wait_step_finalize(10.0), "finish worker never ended"
    w._invalidate_step_session()
    w._dirty = False
    w.close()
    w.deleteLater()
    qapp.processEvents()


def _wrap_finish_all(monkeypatch, session, *, before=None, on_progress=None,
                     on_stop_check=None):
    """Wrap the session's `finish_all` so a test can watch (and pace) it
    from the worker thread without changing what it computes."""
    original = session.finish_all
    seen = {"thread": None, "progress": []}

    def finish_all(*, progress=None, should_stop=None):
        seen["thread"] = threading.get_ident()
        if before is not None:
            before()

        def progress_hook(done, total):
            seen["progress"].append((done, total))
            if progress is not None:
                progress(done, total)
            if on_progress is not None:
                on_progress(done, total)

        def stop_hook():
            if on_stop_check is not None:
                on_stop_check()
            return should_stop() if should_stop is not None else False

        return original(progress=progress_hook, should_stop=stop_hook)

    monkeypatch.setattr(session, "finish_all", finish_all)
    return seen


def _wait(qapp, cond, timeout=10.0):
    deadline = time.monotonic() + timeout
    while not cond() and time.monotonic() < deadline:
        qapp.processEvents()
        time.sleep(0.005)
    qapp.processEvents()
    return cond()


def test_finish_runs_off_the_gui_thread_with_the_same_results(window, qapp,
                                                             monkeypatch):
    window._on_step()
    session = window._step_session
    assert session is not None
    # The old synchronous Finish on the same model and seed.
    twin_sink: list = []
    twin = StepSession(copy.deepcopy(window._last_run_model),
                       report_sink=twin_sink, num_runs=RUNS)
    twin_logs, _ = twin.finish_all()

    seen = _wrap_finish_all(monkeypatch, session,
                            on_progress=lambda d, t: time.sleep(0.15))
    messages: list[str] = []
    window._statusbar.messageChanged.connect(messages.append)
    ticks: list[float] = []
    heartbeat = QTimer()
    heartbeat.setInterval(10)
    heartbeat.timeout.connect(lambda: ticks.append(time.monotonic()))
    heartbeat.start()

    started = time.monotonic()
    window._on_step_finish()
    assert time.monotonic() - started < 0.25, "Finish blocked the GUI thread"
    assert window._step_session is None
    state = window._step_finalize_state
    assert state is not None and state["kind"] == "finish"
    assert window._run_or_step_active()
    # The step controls are off while it runs; Stop is on.
    assert not window._step_action.isEnabled()
    assert not window._step_finish_action.isEnabled()
    assert window._stop_run_action.isEnabled()
    assert window._run_progress.isVisible()
    thread = window._step_finalize_thread
    window._on_step_finish()                        # a second press is a no-op
    assert window._step_finalize_thread is thread

    assert window._wait_step_finalize(20.0)
    heartbeat.stop()
    assert seen["thread"] is not None
    assert seen["thread"] != threading.get_ident(), "finish_all ran on the GUI thread"
    assert len(ticks) >= 5, "the event loop never turned during Finish"
    assert seen["progress"] == [(1, RUNS), (2, RUNS), (3, RUNS)]
    assert any(m.startswith("Finishing — run") for m in messages)

    assert window._results._report_data == twin_sink
    assert window._results._event_logs == window._dev_filtered_event_logs(twin_logs)
    assert window._results._manifest["trials"] == RUNS
    assert window._step_finalize_state is None
    assert window._run_in_progress is False
    assert not window._run_progress.isVisible()
    assert window._step_action.isEnabled()
    assert not window._stop_run_action.isEnabled()
    assert "Completed 3 run(s)" in window._statusbar.currentMessage()


def test_progress_reaches_the_status_bar(window, qapp, monkeypatch):
    window._on_step()
    session = window._step_session
    release = threading.Event()

    def hold(done, total):
        if done == 1:
            release.wait(5.0)

    _wrap_finish_all(monkeypatch, session, on_progress=hold)
    window._on_step_finish()
    try:
        assert _wait(qapp, lambda: window._run_progress.value() == 100 // RUNS)
        assert window._statusbar.currentMessage().startswith(
            f"Finishing — run 2 of {RUNS}")
    finally:
        release.set()
    assert window._wait_step_finalize(20.0)


def test_stop_between_runs_presents_the_finished_runs(window, qapp,
                                                      monkeypatch):
    window._on_step()
    session = window._step_session

    def hold(done, total):
        if done == 1:
            window_state = window._step_finalize_state
            window_state["stop_ev"].wait(5.0)

    _wrap_finish_all(monkeypatch, session, on_progress=hold)
    window._on_step_finish()
    assert _wait(qapp, lambda: window._run_progress.value() == 100 // RUNS)
    window._stop_bg_run()
    assert "Stop requested" in window._statusbar.currentMessage()
    assert window._wait_step_finalize(20.0)

    assert len(window._results._report_data) == 1
    assert window._results._manifest["trials"] == 1
    assert window._results._requested_runs == (RUNS, "cancelled")
    message = window._statusbar.currentMessage()
    assert message.startswith(f"Stopped after 1 of {RUNS} run(s)")
    assert "session has ended" in message
    assert window._step_session is None
    assert window._run_in_progress is False
    assert not window._run_progress.isVisible()
    assert window._step_action.isEnabled()
    assert not window._step_finish_action.isEnabled()
    assert not window._stop_run_action.isEnabled()


def test_stop_before_any_run_completes_ends_the_session(window, qapp,
                                                        monkeypatch):
    window._on_step()
    session = window._step_session
    presented = []
    monkeypatch.setattr(window, "_present_run_results",
                        lambda *a, **k: presented.append(a))
    gate = {"held": False}

    def hold():
        if not gate["held"]:
            gate["held"] = True
            window._step_finalize_state["stop_ev"].wait(5.0)

    _wrap_finish_all(monkeypatch, session, on_stop_check=hold)
    window._on_step_finish()
    assert _wait(qapp, lambda: gate["held"])
    window._handle_escape()                          # Esc stops it too
    assert window._wait_step_finalize(20.0)
    assert not presented
    assert "no results" in window._statusbar.currentMessage()
    assert window._step_session is None
    assert window._run_in_progress is False
    assert window._step_action.isEnabled()


def test_a_failed_finish_is_reported_and_unlocks(window, qapp, monkeypatch):
    window._on_step()
    session = window._step_session

    def fail(**_hooks):
        raise RuntimeError("fake finish failure")

    monkeypatch.setattr(session, "finish_all", fail)
    errors = []
    monkeypatch.setattr(msgbox, "critical", lambda *a, **k: errors.append(a))
    window._on_step_finish()
    assert window._wait_step_finalize(10.0)
    assert errors and errors[0][1] == "Simulation Failed"
    assert "fake finish failure" in errors[0][2]
    assert window._statusbar.currentMessage() == "Run failed"
    assert window._run_in_progress is False
    assert not window._run_progress.isVisible()
    assert window._step_action.isEnabled()


def test_closing_while_finishing_stops_the_worker(window, qapp, monkeypatch):
    window._on_step()
    session = window._step_session
    gate = {"held": False}

    def hold():
        if not gate["held"]:
            gate["held"] = True
            # Without the close's stop this would hold the worker 30 s.
            window._step_finalize_state["stop_ev"].wait(30.0)

    presented = []
    monkeypatch.setattr(window, "_present_run_results",
                        lambda *a, **k: presented.append(a))
    _wrap_finish_all(monkeypatch, session, on_stop_check=hold)
    window._on_step_finish()
    thread = window._step_finalize_thread
    assert _wait(qapp, lambda: gate["held"])

    window._dirty = False
    started = time.monotonic()
    window.close()
    assert time.monotonic() - started < 10.0
    assert not window.isVisible()
    assert not thread.is_alive()
    assert window._step_finalize_state is None
    assert window._run_in_progress is False
    assert not presented


def test_editing_while_finishing_discards_with_a_message(window, qapp,
                                                         monkeypatch):
    window._on_step()
    session = window._step_session
    gate = {"held": False}

    def hold():
        if not gate["held"]:
            gate["held"] = True
            window._step_finalize_state["stop_ev"].wait(30.0)

    presented = []
    monkeypatch.setattr(window, "_present_run_results",
                        lambda *a, **k: presented.append(a))
    _wrap_finish_all(monkeypatch, session, on_stop_check=hold)
    window._on_step_finish()
    assert _wait(qapp, lambda: gate["held"])
    window._stop_run_for_edit()
    assert window._statusbar.currentMessage() == \
        "Finish results discarded — model edited"
    assert window._wait_step_finalize(10.0)
    assert not presented
    assert window._run_in_progress is False
