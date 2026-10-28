"""Run / step / pause / stop availability as a function of state.

These commands used to be enabled and disabled by ~30 scattered
`setEnabled` calls (each run start and end, a sync function on a timer,
the model-load path). They disagreed: during a background run Step and
Next Time were disabled but Finish, Multi and Run-to were not, and during a
synchronous run NOTHING in the step family was disabled, so Step could start
a step session concurrently with the batch.

Availability is now `PREDICATES[...](AppState)`, and `AppState` is derived
from live objects. The predicates are pure, so they are tested as a table;
the live tests then drive real runs and read the real actions.
"""
from __future__ import annotations

import time

import pytest
from PySide6.QtWidgets import QApplication, QMessageBox, QPushButton

from raptor.gui.commands import PREDICATES, AppState, unmet_reason

pytestmark = pytest.mark.gui

MODEL = "tests/golden/TwoBlockRedundancy/input.rbd"

NO_MODEL = AppState()
INVALID = AppState(model_open=True, has_file=True, valid=False)
IDLE = AppState(model_open=True, has_file=True, valid=True)
SYNC = AppState(model_open=True, has_file=True, valid=True, run="sync")
BACKGROUND = AppState(model_open=True, has_file=True, valid=True,
                      run="background")


class TestPredicates:
    @pytest.mark.parametrize("state,expected", [
        (NO_MODEL, False), (INVALID, False), (IDLE, True),
        (SYNC, False), (BACKGROUND, False)])
    def test_can_simulate(self, state, expected):
        assert PREDICATES["can_simulate"](state) is expected

    @pytest.mark.parametrize("state,expected", [
        (NO_MODEL, False), (INVALID, False), (IDLE, False),
        (SYNC, True), (BACKGROUND, True)])
    def test_run_active(self, state, expected):
        assert PREDICATES["run_active"](state) is expected

    @pytest.mark.parametrize("state,expected", [
        (NO_MODEL, False), (INVALID, False), (IDLE, False),
        (SYNC, False), (BACKGROUND, True)])
    def test_background_run(self, state, expected):
        assert PREDICATES["background_run"](state) is expected

    def test_pause_and_jump_never_apply_to_a_synchronous_run(self):
        """Stop works on either kind of run; Pause and Jump exist only for
        a background one."""
        assert PREDICATES["run_active"](SYNC)
        assert not PREDICATES["background_run"](SYNC)

    @pytest.mark.parametrize("state,fragment", [
        (NO_MODEL, "open or create a model"),
        (INVALID, "model has errors"),
        (SYNC, "run is in progress"),
        (BACKGROUND, "run is in progress")])
    def test_the_reason_names_what_is_actually_wrong(self, state, fragment):
        assert fragment in unmet_reason("can_simulate", state)

    def test_no_model_wins_over_other_reasons(self):
        assert "open or create" in unmet_reason(
            "can_simulate", AppState(run="sync"))


# -- the live window ---------------------------------------------------

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
    w._dirty = False          # else Run asks whether to save first
    yield w
    w._run_in_progress = False
    w._dirty = False
    w.close()


STEP_FAMILY = ("_step_action", "_step_next_time_action",
               "_step_finish_action", "_step_multi_action",
               "_run_to_action", "_terminate_action",
               "_restart_step_action", "_fev_fail_action",
               "_fev_repair_action")
#: They end or restart a stepped session, so they need one open.
SESSION_ONLY = ("_step_finish_action", "_terminate_action",
                "_restart_step_action")
#: What can start or advance a session from idle.
STEP_STARTERS = tuple(a for a in STEP_FAMILY if a not in SESSION_ONLY)


def _enabled(win, *attrs):
    return {a: getattr(win, a).isEnabled() for a in attrs}


def test_idle_valid_model(win):
    assert all(_enabled(win, "_run_action",
                        *STEP_STARTERS).values())
    # With no stepped session there is nothing to finish, end or restart.
    assert not any(_enabled(win, *SESSION_ONLY).values())
    assert "press Step" in win._terminate_action.toolTip()
    assert not any(_enabled(win, "_pause_action", "_stop_run_action",
                            "_jump_action").values())
    # Failure Effects View's own commands need the view open.
    assert win._fev_view_action.isEnabled()
    assert not any(_enabled(win, "_fev_status_action", "_fev_erase_all_action",
                            "_fev_phase_next_action").values())
    assert not win._fev_log_btn.isEnabled()


def test_a_disabled_stop_and_pause_say_why(win):
    assert "no run is in progress" in win._stop_run_action.toolTip()
    assert "press Run" in win._pause_action.toolTip()


def test_an_invalid_model_disables_the_simulate_family_and_says_why(win):
    win._validation_issues = lambda: ["No blocks defined"]
    # Validity is memoised per model revision; a stand-in that changes
    # the answer must say the model changed, as an edit would.
    win._bump_model_revision()
    win._sync_simulate_enabled_state()
    assert not any(_enabled(win, "_run_action",
                            *STEP_FAMILY).values())
    assert "model has errors" in win._run_action.toolTip()
    assert not win._fev_view_action.isEnabled()
    assert not win._fev_log_btn.isEnabled()
    assert not win._breakpoint_combo.isEnabled()


def test_fixing_the_model_brings_them_back(win):
    issues = ["broken"]
    win._validation_issues = lambda: issues
    win._bump_model_revision()
    win._sync_simulate_enabled_state()
    assert not win._run_action.isEnabled()
    issues.clear()
    # Validity is memoised per model revision (`_model_is_valid`), because
    # re-validating on every selection change cost ~100 ms a click. In
    # the window the fix IS an edit, which goes through `_mark_dirty`;
    # this stand-in has to say so too, or it is claiming the model
    # changed while the window was never told.
    win._bump_model_revision()
    win._sync_simulate_enabled_state()
    assert win._run_action.isEnabled()
    assert "Unavailable" not in win._run_action.toolTip()


def _during_sync_run(win, monkeypatch, probe):
    """Run a real synchronous batch and call `probe()` from inside it."""
    from raptor.gui.ui import main_window as mw
    real = mw.run_simulation_with_reports
    seen = {}

    def wrapped(*a, **k):
        seen["value"] = probe()
        return real(*a, **k)
    monkeypatch.setattr(mw, "run_simulation_with_reports", wrapped)
    win._run_simulation()
    return seen["value"]


def test_during_a_synchronous_run(win, monkeypatch):
    def probe():
        return (_enabled(win, "_run_action",
                         "_stop_run_action", "_pause_action",
                         "_jump_action", *STEP_FAMILY),
                win._command_state().run)
    states, run = _during_sync_run(win, monkeypatch, probe)
    assert run == "sync"
    assert states["_stop_run_action"], "Stop must work on a batch"
    assert not states["_pause_action"] and not states["_jump_action"]
    assert not states["_run_action"]
    stepping = {k: v for k, v in states.items() if k in STEP_FAMILY}
    assert not any(stepping.values()), \
        f"the step family was enabled during a run: {stepping}"


def test_a_full_refresh_mid_run_does_not_reenable_run(win, monkeypatch):
    """The old rule was 'only refresh when nothing is running'. Run state
    is part of the state now, so a refresh at any time is correct."""
    def probe():
        win._refresh_commands()
        win._sync_simulate_enabled_state()
        return win._run_action.isEnabled(), win._stop_run_action.isEnabled()
    run_on, stop_on = _during_sync_run(win, monkeypatch, probe)
    assert not run_on and stop_on


def test_after_a_synchronous_run_everything_is_back(win, monkeypatch):
    _during_sync_run(win, monkeypatch, lambda: None)
    assert win._command_state().run == "idle"
    assert win._run_action.isEnabled()
    assert not win._stop_run_action.isEnabled()
    assert all(_enabled(win, *STEP_STARTERS).values())


def test_a_failed_run_restores_the_commands_and_unlocks_the_panel(
        win, monkeypatch):
    """Regression: the error paths cleared the run flag but never unlocked
    the Run panel, so the settings stayed greyed out after a failure."""
    from raptor.gui.ui import main_window as mw

    def boom(*a, **k):
        raise RuntimeError("engine exploded")
    monkeypatch.setattr(mw, "run_simulation_with_reports", boom)
    monkeypatch.setattr(QMessageBox, "critical",
                        staticmethod(lambda *a, **k: 0))
    win._run_simulation()
    assert win._run_action.isEnabled()
    assert not win._stop_run_action.isEnabled()
    assert win._run_panel.is_enabled(), "run settings stayed locked"


def _wait_for_idle(win, qapp, seconds=60):
    end = time.monotonic() + seconds
    while win._run_in_progress and time.monotonic() < end:
        qapp.processEvents()
        time.sleep(0.02)
    qapp.processEvents()
    assert not win._run_in_progress, "the background run never finished"


def test_a_background_run_end_to_end(win, qapp):
    win._live_sim_action.setChecked(False)
    win._on_run()
    assert win._command_state().run == "background"
    en = _enabled(win, "_run_action", "_pause_action",
                  "_stop_run_action", "_jump_action", *STEP_FAMILY)
    assert en["_pause_action"] and en["_stop_run_action"] \
        and en["_jump_action"]
    assert not en["_run_action"]
    assert not any(en[k] for k in STEP_FAMILY), en

    _wait_for_idle(win, qapp)
    assert win._command_state().run == "idle"
    assert win._run_action.isEnabled()
    assert not any(_enabled(win, "_pause_action", "_stop_run_action",
                            "_jump_action").values())
    assert all(_enabled(win, *STEP_STARTERS).values())


def test_stopping_a_background_run_restores_the_commands(win, qapp):
    win._live_sim_action.setChecked(False)
    win._on_run()
    win._stop_run_action.trigger()
    _wait_for_idle(win, qapp)
    assert win._run_action.isEnabled()
    assert not win._stop_run_action.isEnabled()


def test_the_validation_timer_does_not_reapply_an_unchanged_state(win,
                                                                   monkeypatch):
    """`_sync_simulate_enabled_state` runs on a timer; a refresh per tick
    would rewrite ~50 tooltips for nothing."""
    calls = []
    real = win._commands.refresh
    monkeypatch.setattr(win._commands, "refresh",
                        lambda *a, **k: (calls.append(1), real(*a, **k))[1])
    for _ in range(5):
        win._sync_simulate_enabled_state()
    assert calls == []


def test_closing_the_model_disables_the_simulate_family(win, qapp):
    win._dirty = False
    win._close_current_file()
    qapp.processEvents()
    assert not any(_enabled(win, "_run_action",
                            *STEP_FAMILY).values())
    assert "open or create a model" in win._run_action.toolTip()


# --- Open / New / Close and the Recent menu follow the run state ---------

class TestFileCommandsDuringARun:
    """They used to stay enabled and raise a modal; now they are disabled,
    with the reason in the tooltip (the modal remains as a safety net)."""

    @pytest.mark.parametrize("state,open_ok,new_ok,close_ok", [
        (AppState(model_open=True, run="idle"), True, True, True),
        (AppState(model_open=True, run="sync"), False, False, False),
        (AppState(model_open=True, run="background"), False, False, False),
        (AppState(model_open=False, run="idle"), True, True, False),
    ])
    def test_rules(self, state, open_ok, new_ok, close_ok):
        assert PREDICATES["not_running"](state) is open_ok
        assert PREDICATES["can_close"](state) is close_ok
        assert PREDICATES["not_running"](state) is new_ok

    def test_reasons(self):
        from raptor.gui.commands import unmet_reason
        assert "stop it first" in unmet_reason(
            "not_running", AppState(model_open=True, run="sync"))
        assert "stop it first" in unmet_reason(
            "can_close", AppState(model_open=True, run="sync"))
        assert "open or create" in unmet_reason(
            "can_close", AppState(model_open=False))

    def test_the_actions_and_file_view_buttons_are_disabled_mid_run(
            self, win, monkeypatch):
        """The File view's buttons are the same commands, so they follow
        them; the recent list opens through `_open_path`, which refuses
        during a run."""
        win._show_backstage()
        nav = {b.text(): b for b in win._backstage.findChildren(QPushButton)}
        blank = next(c for c in win._backstage._cards
                     if c.property("example") is None)
        win._backstage.close_view()

        def probe():
            return (_enabled(win, "_open_action", "_new_action",
                             "_close_file_action"),
                    nav["Open"].isEnabled(), blank.isEnabled())
        states, open_on, new_on = _during_sync_run(win, monkeypatch, probe)
        assert not any(states.values()), states
        assert not open_on and not new_on

    def test_they_come_back_after_the_run(self, win, monkeypatch):
        _during_sync_run(win, monkeypatch, lambda: None)
        assert all(_enabled(win, "_open_action", "_new_action",
                            "_close_file_action").values())


def test_undoing_the_edit_that_broke_the_model_brings_run_back(qapp):
    """REGRESSION: a partial refresh (after the selection changed) was
    recorded as the applied state, so the validation timer saw "nothing
    changed" and never re-enabled Run once an undo had fixed the model."""
    from raptor.core.fileio.rbd import load_rbd
    from raptor.gui.ui.main_window import MainWindow
    w = MainWindow()
    try:
        w._load_into_app(load_rbd(MODEL), MODEL)
        v = w._diagram_dock.view
        run = w._commands.action("sim.run")
        assert run.isEnabled()
        v._links[0].setSelected(True)
        v.delete_selected()
        w._sync_simulate_enabled_state()
        assert not run.isEnabled()
        w._commands.action("home.undo").trigger()
        w._sync_simulate_enabled_state()
        assert run.isEnabled(), run.toolTip()
    finally:
        w._dirty = False
        w.close()


def test_close_leaves_an_empty_workbench(qapp):
    """REGRESSION: Close showed a placeholder model -- a diagram, a full
    Explorer, "Model OK" and "This model: 3 blocks" -- over a ribbon with
    everything disabled."""
    from raptor.core.fileio.rbd import load_rbd
    from raptor.gui.ui.main_window import MainWindow
    from raptor.gui.ui import settings
    settings.set_value("startup/show_start_page", False)
    w = MainWindow()
    try:
        w._load_into_app(load_rbd(MODEL), MODEL)
        w._close_current_file()
        v = w._diagram_dock.view
        assert w._rbd is None
        assert not v._block_items and not v._node_items
        assert w._explorer._tree.topLevelItemCount() == 0
        assert "This model" not in w._inspector._empty.text()
    finally:
        settings.set_value("startup/show_start_page", True)
        w._dirty = False
        w.close()


def test_the_inspector_lets_go_of_a_deleted_block(qapp):
    from raptor.core.fileio.rbd import load_rbd
    from raptor.gui.ui.main_window import MainWindow
    w = MainWindow()
    try:
        w._load_into_app(load_rbd(MODEL), MODEL)
        v = w._diagram_dock.view
        bid = min(v.blocks)
        w._inspector.show_block(bid, v.blocks)
        v._block_items[bid].setSelected(True)
        v.delete_selected()
        w._do_view_refresh()
        assert w._inspector.current_target() is None
    finally:
        w._dirty = False
        w.close()


def test_session_commands_follow_the_stepped_session(win):
    assert not win._terminate_action.isEnabled()
    win._ensure_step_session()
    assert all(_enabled(win, *SESSION_ONLY).values())
    win._step_session = None
    assert not any(_enabled(win, *SESSION_ONLY).values())


def test_clear_results_is_only_enabled_with_a_result_set(win, monkeypatch):
    assert not win._clear_results_action.isEnabled()
    monkeypatch.setattr(win._results, "has_results", lambda: True)
    win._refresh_commands()
    assert win._clear_results_action.isEnabled()
    monkeypatch.setattr(win._results, "has_results", lambda: False)
    win._refresh_commands()
    assert not win._clear_results_action.isEnabled()


def test_run_settings_is_greyed_on_its_own_ribbon_page(win, qapp):
    assert win._run_settings_action.isEnabled()
    win._ribbon.tab_bar.setCurrentIndex(win._simulate_page_index)
    qapp.processEvents()
    assert not win._run_settings_action.isEnabled()
    win._ribbon.tab_bar.setCurrentIndex(win._model_page_index)
    qapp.processEvents()
    assert win._run_settings_action.isEnabled()
