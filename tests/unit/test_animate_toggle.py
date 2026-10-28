"""Animate is a DISPLAY setting, not a model edit.

The bug this exists to prevent: `_set_animate` used to write
`SYSTEM_DATA/graphicsUse`, which dirtied the model and therefore tripped
MainWindow's edit-during-run guard. With a step session live, clicking
Animate popped a modal "Simulation in progress — editing now will cancel
it" *every single time*. It read as a pile of dialogs on every click,
and it was only visible in real use with a stepped session running —
no headless test of the toggle itself ever saw it.

The rule being pinned: a control that changes how a run is DISPLAYED
must not mutate the model. The original Raptor is on the same side of
this line — its graphics checkbox lives in the Display module and the
setting it gates (the time slice) is a VIEW column, `graphTimeSlice`,
alongside `zoomVal` and `xOrigin`.
"""
from __future__ import annotations

import pytest
from PySide6.QtWidgets import QApplication, QDialog, QMessageBox

FIXTURE = ("tests/golden/TwoBlockRedundancy/input.rbd")


@pytest.fixture
def qapp():
    return QApplication.instance() or QApplication([])


@pytest.fixture(autouse=True)
def _clean_settings():
    from PySide6.QtCore import QSettings
    QSettings().clear()
    yield
    QSettings().clear()


@pytest.fixture
def window(qapp):
    from raptor.gui.ui.main_window import MainWindow
    from raptor.core.fileio.rbd import load_rbd
    w = MainWindow()
    w._load_into_app(load_rbd(FIXTURE), FIXTURE)
    qapp.processEvents()
    return w


@pytest.fixture
def no_dialogs(monkeypatch):
    """Record every modal that would open, and never actually block."""
    seen = []
    for cls in (QDialog, QMessageBox):
        def make(cls):
            def patched(self, *a, **k):
                try:
                    seen.append((cls.__name__, self.windowTitle()))
                except Exception:
                    seen.append((cls.__name__, "?"))
                return QDialog.Accepted
            return patched
        monkeypatch.setattr(cls, "exec", make(cls), raising=False)
    return seen


class TestAnimateDoesNotTouchTheModel:
    def test_toggle_does_not_dirty_the_model(self, window):
        assert window._dirty is False
        window._live_sim_action.setChecked(False)
        assert window._dirty is False, \
            "a display toggle must not make the file unsaved"
        window._live_sim_action.setChecked(True)
        assert window._dirty is False

    def test_toggle_pushes_no_undo_entry(self, window):
        window._live_sim_action.setChecked(False)
        assert not window._undo_stack.canUndo(), \
            "there is nothing to undo about turning animation on or off"

    def test_toggle_leaves_graphicsUse_alone(self, window):
        """The column keeps whatever the FILE said. It is round-tripped
        for the legacy formatter, and nothing offers a control for it."""
        row = window._rbd.sections["SYSTEM_DATA"].rows[0]
        before = row["graphicsUse"]
        window._live_sim_action.setChecked(False)
        assert row["graphicsUse"] == before
        window._live_sim_action.setChecked(True)
        assert row["graphicsUse"] == before


class TestNoDialogsMidStep:
    """The reported symptom, reproduced directly."""

    def test_no_dialog_with_a_step_session_active(self, window, no_dialogs,
                                                  qapp):
        window._step_session = object()      # a live stepped session
        window._live_sim_action.setChecked(False)
        qapp.processEvents()                 # let any singleShot fire
        window._live_sim_action.setChecked(True)
        qapp.processEvents()
        assert no_dialogs == [], (
            "toggling Animate must never interrupt the user; opened: "
            f"{no_dialogs}")

    def test_no_dialog_with_a_run_in_progress(self, window, no_dialogs,
                                              qapp):
        window._run_in_progress = True
        window._live_sim_action.setChecked(False)
        qapp.processEvents()
        assert no_dialogs == [], \
            f"toggling Animate must never interrupt a run: {no_dialogs}"


class TestSpeedGateStillWorks:
    """The reason Animate exists as a toggle at all: speed is only
    meaningful when something is being animated."""

    def test_dial_is_disabled_without_animation(self, window):
        window._live_sim_action.setChecked(False)
        for act in (window._speed_slower, window._speed_reset_1,
                    window._speed_faster, window._speed_reset_max):
            assert not act.isEnabled()
            assert "Animate" in act.toolTip(), \
                "a disabled control must say what would enable it"

    def test_dial_is_enabled_with_animation(self, window):
        window._live_sim_action.setChecked(True)
        for act in (window._speed_slower, window._speed_reset_1,
                    window._speed_faster, window._speed_reset_max):
            assert act.isEnabled()

    def test_delay_is_ignored_when_not_animating(self, window):
        window._live_sim_action.setChecked(True)
        window._set_speed(1.0)
        assert window._effective_event_delay() == 1.0
        window._live_sim_action.setChecked(False)
        assert window._effective_event_delay() == 0.0, \
            "no animation means full speed, whatever the dial says"

    def test_dialled_rate_survives_toggling_off_and_on(self, window):
        """Turning animation off must not silently discard a rate the user
        chose; re-enabling restores it."""
        window._live_sim_action.setChecked(True)
        window._set_speed(0.5)
        window._live_sim_action.setChecked(False)
        window._live_sim_action.setChecked(True)
        assert window._effective_event_delay() == 0.5


class TestPersistence:
    def test_state_is_remembered_between_sessions(self, window, qapp):
        from PySide6.QtCore import QSettings
        from raptor.gui.ui.main_window import MainWindow
        window._live_sim_action.setChecked(True)
        window._live_sim_action.setChecked(False)
        assert QSettings().value("execution/default_animate") is False
        # A fresh window is what a relaunch looks like.
        assert MainWindow()._live_sim_action.isChecked() is False

    def test_speeds_are_not_persisted_with_the_model(self, window):
        """The one thing that must NOT be saved: a rate chosen for this
        session is not engineering data about the reliability model."""
        window._set_speed(0.25)
        import json
        import raptor.core.fileio.rbds as rbds_io
        text = rbds_io.render_rbds(window._rbd)
        assert "0.25" not in json.dumps(json.loads(text).get(
            "experiment", {}), default=str)
