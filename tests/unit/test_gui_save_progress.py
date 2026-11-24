"""Saving an .rbdz runs off the GUI thread, with progress, and is safe."""
from __future__ import annotations

import os
import threading
from pathlib import Path

import pytest

pytest.importorskip("PySide6")
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QEvent, QPointF, Qt, QTimer
from PySide6.QtGui import QKeyEvent, QMouseEvent
from PySide6.QtWidgets import QApplication

from raptor.core.fileio import rbdz
from raptor.core.fileio.model_file import load_model_file
from raptor.gui.ui import save_progress

DC1 = Path(__file__).resolve().parents[2] / "new_docs/models/DC1.rbds"


@pytest.fixture(scope="module")
def qapp():
    return QApplication.instance() or QApplication([])


@pytest.fixture
def window(qapp, tmp_path, monkeypatch):
    from raptor.api import history as api_history
    from raptor.core.history import HistoryStore
    from raptor.gui.ui import msgbox, settings
    from raptor.gui.ui.main_window import MainWindow
    store = HistoryStore(tmp_path / "history")
    monkeypatch.setattr(api_history, "default_store", lambda: store)
    settings.set_value("startup/show_start_page", False)
    monkeypatch.setattr(msgbox, "warning", lambda *a, **k: None)
    errors: list[str] = []
    monkeypatch.setattr(msgbox, "critical",
                        lambda _w, _t, text, *a, **k: errors.append(text))
    win = MainWindow()
    win._test_errors = errors
    target = tmp_path / "plant.rbdz"
    win._load_into_app(load_model_file(str(DC1)), str(target))
    win._results.archive_payload = lambda: {"events.txt": ["a line"] * 50}
    win._mark_dirty()
    yield win
    win._dirty = False
    win.close()


def _slow_writer(monkeypatch, probes, hold_ms=700):
    """write_archive stand-in that waits on the GUI thread's probes."""
    real = rbdz.write_archive
    gate = threading.Event()
    seen: dict = {"thread": None}

    def fake(path, text, **kw):
        seen["thread"] = threading.current_thread()
        kw["progress"](50, 100)
        gate.wait(10)
        return real(path, text, **kw)

    monkeypatch.setattr(rbdz, "write_archive", fake)
    for ms, fn in probes:
        QTimer.singleShot(ms, fn)
    QTimer.singleShot(hold_ms, gate.set)
    return seen


def test_save_runs_on_a_worker_and_the_window_keeps_running(
        window, monkeypatch):
    ticks: list[int] = []
    timer = QTimer()
    timer.timeout.connect(lambda: ticks.append(1))
    timer.start(10)
    seen = _slow_writer(monkeypatch, [])
    window._save_file()
    timer.stop()
    assert seen["thread"] is not threading.main_thread()
    assert len(ticks) > 20          # the GUI thread was free throughout
    assert not window._dirty
    assert Path(window._current_file).exists()


def test_input_that_edits_or_runs_is_held_back_until_the_save_ends(
        window, monkeypatch):
    probe: dict = {}

    def during():
        probe["saving"] = window._save_in_progress
        probe["may_run"] = window._run_may_start()
        probe["refuse"] = window._refuse_during_run("open another file")
        key = QKeyEvent(QEvent.Type.KeyPress, Qt.Key.Key_Delete,
                        Qt.KeyboardModifier.NoModifier)
        probe["key_blocked"] = QApplication.sendEvent(window, key)
        press = QMouseEvent(QEvent.Type.MouseButtonPress, QPointF(5, 5),
                            QPointF(5, 5), Qt.MouseButton.LeftButton,
                            Qt.MouseButton.LeftButton,
                            Qt.KeyboardModifier.NoModifier)
        probe["click_blocked"] = QApplication.sendEvent(
            window._diagram_dock.view.viewport(), press)
        before = window._undo_stack.count()
        probe["undo_count_same"] = before == window._undo_stack.count()

    _slow_writer(monkeypatch, [(200, during)])
    window._save_file()
    assert probe["saving"] is True
    assert probe["may_run"] is False
    assert probe["refuse"] is True
    assert probe["key_blocked"] is True
    assert probe["click_blocked"] is True
    assert window._save_in_progress is False
    assert window._run_may_start() is True


def test_progress_bar_shows_only_for_a_slow_save(window, monkeypatch):
    made: list = []
    real = save_progress.SaveProgressBar

    class Spy(real):
        def __init__(self, *a, **k):
            super().__init__(*a, **k)
            made.append(self)

    monkeypatch.setattr(save_progress, "SaveProgressBar", Spy)
    window._save_file()             # small: done well inside 300 ms
    assert made == []
    window._mark_dirty()
    texts: list[str] = []
    _slow_writer(monkeypatch, [
        (500, lambda: texts.append(
            (made[0].label.text(), made[0].bar.value())
            if made else ("no bar", -1)))])
    window._save_file()
    assert len(made) == 1
    label, value = texts[0]
    assert "50 %" in label and "plant.rbdz" in label and value == 50
    assert not made[0].isVisible()          # removed once the save ended


def test_failure_keeps_the_window_dirty_and_shows_the_error(
        window, monkeypatch):
    def boom(*a, **k):
        raise OSError("disk full")
    monkeypatch.setattr(rbdz, "write_archive", boom)
    window._save_file()
    assert window._dirty
    assert any("disk full" in e for e in window._test_errors)
    assert window._save_in_progress is False


def test_closing_during_a_save_waits_then_closes(window, monkeypatch):
    results: dict = {}

    def during():
        from PySide6.QtGui import QCloseEvent
        ev = QCloseEvent()
        window.closeEvent(ev)
        results["ignored"] = not ev.isAccepted()
        results["flag"] = window._close_after_save

    closed: list[int] = []
    monkeypatch.setattr(window, "close", lambda: closed.append(1) or True)
    _slow_writer(monkeypatch, [(100, during)])
    window._save_file()
    assert results == {"ignored": True, "flag": True}
    assert not window._dirty
    QApplication.processEvents()
    QApplication.processEvents()
    assert closed == [1]                    # the deferred close was queued


def test_save_version_now_waits_for_the_save(window, monkeypatch):
    _slow_writer(monkeypatch, [], hold_ms=400)
    assert window._save_for_history() is True
    assert not window._dirty
    assert Path(window._current_file).exists()


def test_save_as_a_second_time_during_a_save_is_ignored(window, monkeypatch):
    calls: list = []
    monkeypatch.setattr(
        window, "_make_file_dialog",
        lambda *a, **k: calls.append(1) or (_ for _ in ()).throw(
            AssertionError("dialog opened while saving")))
    _slow_writer(monkeypatch, [(100, window._save_file_as),
                               (150, window._save_file)])
    window._save_file()
    assert calls == []
