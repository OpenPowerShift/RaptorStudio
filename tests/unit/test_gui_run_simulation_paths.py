"""The branches of the foreground run that a clean run never takes.

`RunMixin._run_simulation` is a long function with several early exits and two
failure paths. A clean batch covers none of them, so before the function was taken
apart these tests pinned what each one leaves behind: the status text, whether the
engine was called, and whether Run and the run settings are usable afterwards.
"""
from __future__ import annotations

import pytest
from PySide6.QtWidgets import QApplication

pytestmark = pytest.mark.gui

MODEL = "tests/golden/TwoBlockRedundancy/input.rbd"


@pytest.fixture
def qapp():
    return QApplication.instance() or QApplication([])


@pytest.fixture
def win(qapp):
    from raptor.core.fileio.rbd import load_rbd
    from raptor.gui.ui.main_window import MainWindow
    w = MainWindow()
    w.resize(1400, 900)
    w.show()
    w._load_into_app(load_rbd(MODEL), MODEL)
    qapp.processEvents()
    w._set_exp_field("trials", 1)
    w._dirty = False
    yield w
    w._run_in_progress = False
    w._dirty = False
    w.close()


@pytest.fixture
def engine_calls(monkeypatch):
    """Replace the engine entry point with a recorder that must not be reached."""
    from raptor.gui.ui import run_mixin
    calls: list = []

    def fake(*a, **k):
        calls.append(k)
        raise AssertionError("the engine must not be called")
    monkeypatch.setattr(run_mixin, "run_simulation_with_reports", fake)
    return calls


def _status(win) -> str:
    return win._statusbar.currentMessage()


def test_a_run_waits_while_the_previous_results_are_still_rendering(win, engine_calls):
    win._results_populating = True
    win._run_simulation()
    assert "Still rendering" in _status(win)
    assert engine_calls == [] and not win._run_in_progress


def test_a_run_waits_while_a_stepped_run_is_finalising(win, engine_calls):
    win._step_finalize_state = {"phase": "working"}
    try:
        win._run_simulation()
    finally:
        win._step_finalize_state = None
    assert "Finalizing" in _status(win)
    assert engine_calls == [] and not win._run_in_progress


def test_a_run_with_no_model_open_does_nothing(win, engine_calls):
    win._rbd = None
    win._run_simulation()
    assert engine_calls == [] and not win._run_in_progress


def test_declining_the_save_prompt_cancels_the_run(win, engine_calls, monkeypatch):
    monkeypatch.setattr(type(win), "_maybe_save_before_run", lambda self: False)
    win._run_simulation()
    assert _status(win) == "Run cancelled"
    assert engine_calls == [] and not win._run_in_progress


def test_an_unready_run_model_cancels_the_run(win, engine_calls, monkeypatch):
    monkeypatch.setattr(type(win), "_prepare_run_model", lambda self: (None, None))
    win._run_simulation()
    assert engine_calls == [] and not win._run_in_progress


def test_a_run_leaves_failure_effects_view_first(win, monkeypatch):
    left = []
    win._fev = object()
    monkeypatch.setattr(type(win), "_leave_fev", lambda self: left.append(True))
    monkeypatch.setattr(type(win), "_maybe_save_before_run", lambda self: False)
    win._run_simulation()
    assert left == [True]


class _Box:
    """A QMessageBox stand-in whose "Run Anyway" button is clicked or not."""
    run_anyway = False

    class Icon:
        Warning = 0

    class ButtonRole:
        AcceptRole = 0
        RejectRole = 1

    def __init__(self, parent=None):
        self._buttons = {}
        self._clicked = None

    def setIcon(self, *_): ...
    def setWindowTitle(self, *_): ...
    def setDetailedText(self, *_): ...
    def setText(self, *_): ...

    def addButton(self, text, role):
        self._buttons[text] = object()
        return self._buttons[text]

    def exec(self):
        self._clicked = self._buttons["Run Anyway" if self.run_anyway else "Cancel"]
        return 0

    def clickedButton(self):
        return self._clicked


def test_cancelling_the_unsupported_features_dialog_stops_the_run(
        win, engine_calls, monkeypatch):
    from raptor.gui.ui import run_mixin
    monkeypatch.setattr(run_mixin, "QMessageBox", type("Cancel", (_Box,), {"run_anyway": False}))
    monkeypatch.setattr(type(win), "_preflight_issues", lambda self, rbd: ["x is unsupported"])
    win._run_simulation()
    assert "unsupported features present" in _status(win)
    assert engine_calls == [] and not win._run_in_progress


def test_run_anyway_goes_ahead_after_the_unsupported_features_dialog(
        win, monkeypatch):
    from raptor.gui.ui import run_mixin
    monkeypatch.setattr(run_mixin, "QMessageBox", type("Anyway", (_Box,), {"run_anyway": True}))
    monkeypatch.setattr(type(win), "_preflight_issues", lambda self, rbd: ["x is unsupported"])
    win._run_simulation()
    assert "successfully" in _status(win)


def test_an_unsupported_feature_error_unlocks_everything_and_says_so(win, monkeypatch):
    from raptor.gui.ui import msgbox, run_mixin
    shown = []

    def boom(*a, **k):
        raise NotImplementedError("a feature")
    monkeypatch.setattr(run_mixin, "run_simulation_with_reports", boom)
    monkeypatch.setattr(msgbox, "warning", lambda *a, **k: shown.append(a))
    win._run_simulation()
    assert shown and "a feature" in shown[0][2]
    assert _status(win) == "Run failed — unsupported feature"
    assert win._run_action.isEnabled() and win._run_panel.is_enabled()
    assert not win._run_in_progress
    assert not win._run_progress.isVisible()


def test_an_engine_error_unlocks_everything_and_says_so(win, monkeypatch):
    from raptor.gui.ui import msgbox, run_mixin
    shown = []

    def boom(*a, **k):
        raise RuntimeError("engine exploded")
    monkeypatch.setattr(run_mixin, "run_simulation_with_reports", boom)
    monkeypatch.setattr(msgbox, "critical", lambda *a, **k: shown.append(a))
    win._run_simulation()
    assert shown and "engine exploded" in shown[0][2]
    assert _status(win) == "Run failed"
    assert win._run_action.isEnabled() and win._run_panel.is_enabled()
    assert not win._run_in_progress
