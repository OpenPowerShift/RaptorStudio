"""The edit-during-run guard, and the run trial count.

Two unrelated defects, both of which had been reported as symptoms rather
than causes.

1. The guard is modal and is raised from inside `QUndoStack.push` (a
   command's `_done()` -> `model_changed`). Anything that re-enters on
   that path -- a dialog button handler, an operation that pumps the
   event loop between commands -- could arm it again while the first
   dialog was still open. The user saw "transiently a pile of dialogs"
   over copy/paste, Animate and the analysis toggles. The latch coalesced
   the common case but nothing bounded the pathological one, so these
   tests pin two hard rules: the prompt body is non-re-entrant, and the
   number of prompts is capped.

2. The legacy `.rbd` format writes its numbers with `%f`, so a trial
   count is the string `"20.000000"`. `int()` on that raises, the
   exception was swallowed, and the trial count fell back to 1 -- so
   opening ANY legacy model silently ran a single trial instead of the
   configured count. No message, and the results were reported for one
   trial.
"""
from __future__ import annotations

import time

import pytest
from PySide6.QtWidgets import QApplication, QDialog, QMessageBox

FIXTURE = "tests/golden/TwoBlockRedundancy/input.rbd"


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
def count_dialogs(monkeypatch):
    """Count modals, answer them instantly, and never block."""
    seen = []
    for cls in (QDialog, QMessageBox):
        def make(cls):
            def patched(self, *a, **k):
                seen.append(cls.__name__)
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
    # Closed explicitly, and CLEAN. These tests open step sessions and
    # leave the model dirty; a leaked MainWindow keeps its StepSession,
    # tooltip poller and validation timer alive for the rest of the
    # session. The dirty flag is cleared first because `closeEvent`
    # otherwise opens a real "discard changes?" dialog: this fixture is
    # set up before `count_dialogs`, so monkeypatch has already undone the
    # dialog patch by the time this teardown runs, and the test hangs
    # rather than fails.
    yield w
    w._step_session = None
    w._run_in_progress = False
    w._dirty = False
    w.close()


class _GuardHarness:
    """One window and one dialog counter for the whole guard class.

    Deliberately shared rather than per-test. Each test here is about an
    INVARIANT of the same state machine, and they mutate shared counters
    (`_edit_guard_prompt_times`, the latch) -- so a per-test window buys
    nothing but a fresh MainWindow, a fresh StepSession and a fresh
    `closeEvent` prompt per assertion, and that ceremony is what made the
    file hang rather than fail.
    """

    def __init__(self, w, qapp, seen):
        self.w, self.qapp, self.seen = w, qapp, seen

    def open_step(self):
        """A fresh stepped session, with the prompt budget reset."""
        self.w._step_session = None
        self.w._edit_guard_prompt_times = []
        self.w._edit_during_run_guard = False
        self.w._edit_guard_reentrancy = False
        self.w._edit_run_abandoned = False
        self.w._on_step()
        self.qapp.processEvents()
        return self.w._step_session is not None

    def edits(self, n=1):
        for _ in range(n):
            self.w._mark_dirty()
            self.qapp.processEvents()

    def reset_counter(self):
        self.seen.clear()


@pytest.fixture
def guard(window, count_dialogs, qapp):
    return _GuardHarness(window, qapp, count_dialogs)


class TestGuardCannotStack:
    def test_many_edits_produce_at_most_the_cap(self, guard):
        """The reported symptom: an operation that fires `_mark_dirty`
        repeatedly must not produce a matching number of dialogs."""
        assert guard.open_step()
        guard.reset_counter()
        guard.edits(25)
        assert len(guard.seen) <= guard.w._EDIT_GUARD_MAX_PROMPTS, \
            f"{len(guard.seen)} dialogs for 25 edits -- it stacked"

    def test_cap_is_enforced(self, guard):
        """Past the cap the guard stops the run instead of asking. A
        bounded promise the UI can actually keep."""
        assert guard.open_step()
        guard.w._edit_guard_prompt_times = [time.monotonic()] * 100
        assert guard.w._edit_guard_prompt_budget() <= 0
        guard.reset_counter()
        guard.edits(1)
        assert guard.w._step_session is None, \
            "out of budget the run must still be stopped"
        assert guard.seen == [], "past the cap it must not ask at all"

    def test_no_prompt_once_the_run_has_ended(self, guard):
        """The prompt is queued with a zero-timer; if the run finishes in
        between, there is nothing to ask about."""
        assert guard.open_step()
        guard.w._step_session = None      # run ends before the timer fires
        guard.reset_counter()
        guard.w._confirm_edit_during_run()
        assert guard.seen == []

    def test_reentrancy_latch_is_released(self, guard):
        """A latch that is never released would silently disable the
        guard for the rest of the session."""
        assert guard.open_step()
        guard.edits(1)
        assert guard.w._edit_guard_reentrancy is False
        assert guard.w._edit_during_run_guard is False

    def test_the_run_is_actually_stopped(self, guard):
        """The guard is not a suggestion. Whichever path it takes, the
        run must not keep executing against stale engine objects."""
        assert guard.open_step()
        guard.edits(10)
        assert guard.w._step_session is None

    def test_no_guard_without_a_run(self, guard):
        """The common case must be completely silent."""
        guard.w._step_session = None
        guard.reset_counter()
        guard.edits(10)
        assert guard.seen == []


class TestTrialCountFromALegacyFile:
    """`.rbd` stores numbers as `%f`, so a count arrives as a string with
    a decimal point."""

    def test_legacy_float_text_is_not_silently_dropped(self):
        from raptor.core.run_experiment import from_system_row
        exp = from_system_row({"NumRuns": "20.000000"})
        assert exp["trials"] == 20, \
            "a model configured for 20 trials must not run 1"

    @pytest.mark.parametrize("text,want", [
        ("1.000000", 1), ("20.000000", 20), ("1000.000000", 1000),
        ("20", 20), (" 20.000000 ", 20),
    ])
    def test_accepted_forms(self, text, want):
        from raptor.core.run_experiment import from_system_row
        assert from_system_row({"NumRuns": text})["trials"] == want

    def test_the_whole_row_round_trips(self):
        from raptor.core.run_experiment import from_system_row
        row = {"NumRuns": "250.000000", "TerminationType": "FailSim",
               "TermCondition": "12.500000", "StartStats": "3.000000",
               "weakAnalysis": "1", "costAnalysis": "0",
               "capacityAnalysis": "1"}
        exp = from_system_row(row)
        assert exp["trials"] == 250
        assert exp["term_condition"] == 12.5
        assert exp["start_stats"] == 3.0
        assert exp["analyses"] == ["weak", "capacity"]

    def test_normalise_accepts_a_numeric_string(self):
        """Same coercion on the other path: a `.rbds` experiment block or
        a hand-edited JSON file can carry a string."""
        from raptor.core.run_experiment import normalise
        assert normalise({"trials": "500.0"})["trials"] == 500

    @pytest.mark.parametrize("bad", ["", None, "many", [1], {"a": 1}])
    def test_genuine_garbage_still_falls_back(self, bad):
        from raptor.core.run_experiment import from_system_row
        assert from_system_row({"NumRuns": bad})["trials"] == 1

    def test_a_bool_is_not_a_count(self):
        """`int(True)` is 1, which would be a plausible-looking wrong
        answer rather than an obviously-bad one."""
        from raptor.core.run_experiment import normalise
        assert normalise({"trials": True})["trials"] == 1
        assert normalise({"trials": False})["trials"] == 1

    def test_a_loaded_legacy_model_reports_its_real_trial_count(self, qapp):
        """End to end: the model row says 20, so the app must say 20."""
        from raptor.gui.ui.main_window import MainWindow
        from raptor.core.fileio.rbd import load_rbd
        w = MainWindow()
        try:
            w._load_into_app(load_rbd(FIXTURE), FIXTURE)
            qapp.processEvents()
            row = w._rbd.sections["SYSTEM_DATA"].rows[0]["NumRuns"]
            assert int(float(row)) == 20
            assert w._current_experiment()["trials"] == 20, \
                "the run experiment must agree with the model's own row"
            assert w._run_panel._trials.value() == 20
        finally:
            w._step_session = None
            w.close()


def test_a_run_does_not_prompt_about_its_own_plot_untick(window, qapp,
                                                         count_dialogs):
    """Ticking the Capacity plot on a model without Capacity Analysis makes
    the run untick it (and say so in the status bar). That untick happens
    with the run already active, so it used to read as a user edit: the
    guard prompted "Simulation in progress ... will cancel it" and the run
    it belonged to was cancelled, leaving no results."""
    w = window
    w._maybe_save_before_run = lambda: True     # not what is under test
    w._report_stream_actions["capacity_plot"].setChecked(True)
    w._run_count_spin.setValue(1)
    count_dialogs.clear()
    w._run_simulation()
    qapp.processEvents()
    assert w._results._report_data, "the run was cancelled by its own untick"
    assert not w._report_stream_actions["capacity_plot"].isChecked()
    assert "QMessageBox" not in count_dialogs
