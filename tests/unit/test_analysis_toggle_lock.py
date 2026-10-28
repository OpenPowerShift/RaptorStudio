"""The Analyses toggles (Simulate tab) are run settings, and must lock like them.

Reported as: clicking "Cost" or "Capacity" flashed a pile of dialogs --
the same failure mode as the Animate toggle (see
`test_animate_toggle.py`), from a different cause.

The cause: these three flags ARE model data (the engine reads them from
`SYSTEM_DATA` at `core/engine/load.py:176`), so toggling one writes the
model, and every model edit trips `_mark_dirty`'s edit-during-run
guard. With a stepped session open, that guard's modal appeared on every
click. The dialog's *advice* was wrong too: which analyses run changes
what the NEXT run records, not the model being simulated right now, so
there was nothing to invalidate.

Unlike Animate, the fix is NOT to stop writing the model -- the engine
needs it. The fix is to lock these controls during a run, which is the
rule the Run panel already followed and these toggles did not.
"""
from __future__ import annotations

import pytest
from PySide6.QtWidgets import QApplication, QDialog, QMessageBox

FIXTURE = "tests/golden/TwoBlockRedundancy/input.rbd"
ANALYSES = ("weakAnalysis", "costAnalysis", "capacityAnalysis")


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
def no_dialogs(monkeypatch):
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


@pytest.fixture
def window(qapp):
    from raptor.gui.ui.main_window import MainWindow
    from raptor.core.fileio.rbd import load_rbd
    w = MainWindow()
    w._load_into_app(load_rbd(FIXTURE), FIXTURE)
    qapp.processEvents()
    return w


def _row(window):
    return window._rbd.sections["SYSTEM_DATA"].rows[0]


def _experiment(window):
    return window._current_experiment()


class TestTheyWriteTheModel:
    """The engine reads these flags, so they must still be written --
    the fix was to lock them, not to stop writing."""

    def test_enabling_writes_the_model_outside_a_run(self, window, qapp):
        window._analysis_actions["costAnalysis"].setChecked(True)
        qapp.processEvents()
        assert _row(window)["costAnalysis"] in ("1", "T")
        assert "cost" in _experiment(window)["analyses"]

    def test_disabling_writes_the_model(self, window, qapp):
        act = window._analysis_actions["weakAnalysis"]
        act.setChecked(True)
        qapp.processEvents()
        act.setChecked(False)
        qapp.processEvents()
        assert _row(window)["weakAnalysis"] == "0"
        assert "weak" not in _experiment(window)["analyses"]


class TestLockedDuringAStep:
    """The reported symptom."""

    def test_toggles_are_disabled_while_stepping(self, window, qapp):
        window._on_step()
        qapp.processEvents()
        for col in ANALYSES:
            act = window._analysis_actions[col]
            assert not act.isEnabled(), f"{col} must lock during a step"

    def test_disabled_toggle_explains_itself(self, window, qapp):
        window._on_step()
        qapp.processEvents()
        tip = window._analysis_actions["costAnalysis"].toolTip()
        assert "locked" in tip.lower() and "next run" in tip.lower(), \
            f"a disabled control must say why: {tip!r}"

    def test_no_dialog_when_clicked_while_stepping(self, window, no_dialogs,
                                                  qapp):
        window._on_step()
        qapp.processEvents()
        for col in ANALYSES:
            window._analysis_actions[col].setChecked(True)
        qapp.processEvents()
        assert no_dialogs == [], f"opened: {no_dialogs}"

    def test_model_is_untouched_while_stepping(self, window, qapp):
        before = {c: _row(window)[c] for c in ANALYSES}
        window._on_step()
        qapp.processEvents()
        for col in ANALYSES:
            window._analysis_actions[col].setChecked(True)
        qapp.processEvents()
        assert {c: _row(window)[c] for c in ANALYSES} == before
        assert _experiment(window)["analyses"] == []
        assert window._dirty is False

    def test_button_snaps_back_to_the_models_value(self, window, qapp):
        """A refused edit must leave the control agreeing with the model.

        Qt has already set the action to the new value by the time
        `toggled` fires, so an incomplete revert shows True while the
        model reads 0 -- and a later setChecked to that same value then
        emits nothing, leaving the toggle apparently stuck off.
        """
        window._on_step()
        qapp.processEvents()
        act = window._analysis_actions["costAnalysis"]
        act.setChecked(True)
        qapp.processEvents()
        assert act.isChecked() is False, \
            "the button must show the model's value, not the refused one"
        assert _row(window)["costAnalysis"] in ("0", "")


class TestUnlocksAgain:
    @pytest.mark.parametrize("end", ["finish", "terminate"])
    def test_toggles_return_after_the_run_ends(self, window, qapp, end):
        window._on_step()
        qapp.processEvents()
        (window._on_step_finish if end == "finish"
         else window._on_step_terminate)()
        qapp.processEvents()
        for col in ANALYSES:
            assert window._analysis_actions[col].isEnabled(), \
                f"{col} must unlock when the run ends ({end})"

    def test_tooltip_returns_to_its_normal_text(self, window, qapp):
        window._on_step()
        qapp.processEvents()
        window._on_step_finish()
        qapp.processEvents()
        tip = window._analysis_actions["costAnalysis"].toolTip()
        assert "locked" not in tip.lower(), \
            f"a re-enabled control must not still describe being locked: {tip!r}"
        assert "writes this model's settings" in tip

    def test_and_then_it_works(self, window, qapp):
        window._on_step()
        qapp.processEvents()
        window._on_step_finish()
        qapp.processEvents()
        act = window._analysis_actions["capacityAnalysis"]
        act.setChecked(True)
        qapp.processEvents()
        assert act.isChecked() is True
        assert "capacity" in _experiment(window)["analyses"]


class TestLockIsDerivedNotRemembered:
    """The lock is recomputed from live state, so it cannot get stuck.

    Five things can end a step session. A flag that had to be cleared at
    each of them is one forgotten path away from leaving the controls
    greyed out for the rest of the session.
    """

    def test_discarding_a_session_unlocks(self, window, qapp):
        window._on_step()
        qapp.processEvents()
        window._invalidate_step_session()
        qapp.processEvents()
        assert all(window._analysis_actions[c].isEnabled() for c in ANALYSES)

    def test_terminating_a_stepped_session_unlocks(self, window, qapp):
        # (Esc no longer ends a stepped session -- it also cancels a tool,
        # and lost the user's position unasked; Terminate ends it.)
        window._on_step()
        qapp.processEvents()
        window._terminate_action.trigger()
        qapp.processEvents()
        assert all(window._analysis_actions[c].isEnabled() for c in ANALYSES)

    def test_lock_matches_the_run_panel(self, window, qapp):
        """Two places, one rule. A toggle live while the panel is locked
        (or vice versa) is the inconsistency that caused the original
        bug."""
        window._on_step()
        qapp.processEvents()
        assert window._run_panel.is_enabled() is False
        assert all(not window._analysis_actions[c].isEnabled()
                   for c in ANALYSES)
        window._on_step_finish()
        qapp.processEvents()
        assert window._run_panel.is_enabled() is True
        assert all(window._analysis_actions[c].isEnabled() for c in ANALYSES)
