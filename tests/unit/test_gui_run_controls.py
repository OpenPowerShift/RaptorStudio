"""Run/Pause/Stop control-state tests -- no real background thread
spun up (avoids timing flakiness); `_bg_state` is hand-built with the
same shape `_start_background_run`'s worker produces, and `_run_action`/
`_run_in_progress` are set directly to simulate "a run is in progress"
without actually running one.
"""
from __future__ import annotations

import os
import threading

import pytest

pytest.importorskip("PySide6")

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QEvent, Qt
from PySide6.QtGui import QKeyEvent
from PySide6.QtWidgets import QApplication

from raptor.core.fileio.rbd import load_rbd
from raptor.gui.ui.main_window import MainWindow

REPO = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
FIXTURE = os.path.join(REPO, "tests", "golden", "HierarchyExample", "input.rbd")


@pytest.fixture(scope="module")
def qapp():
    return QApplication.instance() or QApplication([])


@pytest.fixture(autouse=True)
def _restore_qsettings_prefs():
    """QSettings is a process-wide store, not isolated per test;
    `test_save_before_run_preference_modes` writes
    "files/save_before_run" to it. Snapshot and restore that key around
    every test in this file so the value does not leak into later tests
    in the session.

    This fixture exists only because QSettings is process-wide. A
    read-through settings facade lets the whole session share one store
    without per-test save/restore, which is why `ui/settings.py` is
    documented as never caching."""
    from PySide6.QtCore import QSettings
    key = "files/save_before_run"
    settings = QSettings()
    before = settings.value(key, None)
    yield
    if before is None:
        settings.remove(key)
    else:
        settings.setValue(key, before)


@pytest.fixture
def window(qapp):
    w = MainWindow()
    w._load_into_app(load_rbd(FIXTURE), None)
    return w


def _escape(window):
    window.keyPressEvent(QKeyEvent(QEvent.KeyPress, Qt.Key_Escape, Qt.NoModifier))


def test_validation_timer_does_not_clobber_run_state_mid_run(window):
    """_sync_simulate_enabled_state runs off a periodic timer completely
    independent of the run lifecycle -- it must not re-enable Run (or
    disable Pause/Stop) while a run is actually in progress.

    Availability is DERIVED from live objects now, so a background run is
    "the poll timer is running" and a hand-set flag alone is a
    synchronous run, for which Pause is correctly unavailable."""
    window._run_in_progress = True
    window._bg_poll.start()
    try:
        window._sync_simulate_enabled_state()
        assert not window._run_action.isEnabled()
        assert window._pause_action.isEnabled()
        assert window._stop_run_action.isEnabled()
        assert window._jump_action.isEnabled()
    finally:
        window._bg_poll.stop()
    # Once the run genuinely ends, the timer's own refresh must work again.
    window._run_in_progress = False
    window._sync_simulate_enabled_state()
    assert window._run_action.isEnabled()
    assert not window._pause_action.isEnabled()


def test_a_hand_set_run_flag_is_a_synchronous_run(window):
    """No poll timer = a batch on the GUI thread: Stop works, Pause and
    Jump (background-only) do not."""
    window._run_in_progress = True
    window._sync_simulate_enabled_state()
    assert not window._run_action.isEnabled()
    assert window._stop_run_action.isEnabled()
    assert not window._pause_action.isEnabled()
    window._run_in_progress = False


def test_escape_pauses_then_stops_background_run(window):
    """First Esc during a background run pauses it (clears resume_ev,
    "Resume" label); a SECOND Esc while already paused stops it (sets
    stop_ev) -- rather than stopping immediately on the first press."""
    resume_ev = threading.Event()
    resume_ev.set()  # not paused
    stop_ev = threading.Event()
    window._bg_state = {
        "resume_ev": resume_ev, "stop_ev": stop_ev,
        "skip_ev": threading.Event(), "progress": (5, 100),
        "done": False,
    }
    window._run_action.setEnabled(False)
    window._run_in_progress = True

    _escape(window)
    assert not resume_ev.is_set(), "first Esc must pause, not stop"
    assert not stop_ev.is_set()
    assert window._pause_action.text() == "Resume"

    _escape(window)
    assert stop_ev.is_set(), "second Esc (while paused) must stop"


def test_save_before_run_preference_modes(window, tmp_path):
    """The "save before run" preference in both modes: "never" leaves
    unsaved changes, "always" saves silently."""
    from PySide6.QtCore import QSettings

    window._dirty = True

    QSettings().setValue("files/save_before_run", "never")
    assert window._maybe_save_before_run() is True
    assert window._dirty is True  # untouched

    window._current_file = str(tmp_path / "roundtrip.rbds")
    window._dirty = True
    QSettings().setValue("files/save_before_run", "always")
    assert window._maybe_save_before_run() is True
    assert window._dirty is False  # saved silently, no prompt
    assert (tmp_path / "roundtrip.rbds").exists()


def test_run_progress_bar_tracks_background_poll_and_hides_on_done(window):
    """The status-bar run-progress indicator (a QWidget never .show()-n
    in this headless test, so isVisible() would always read False
    regardless -- isHidden() reflects the widget's OWN explicit request
    instead) must be hidden at idle, track percent-complete via
    _bg_poll_tick while a run is active, and hide again once done."""
    assert window._run_progress.isHidden()

    resume_ev = threading.Event()
    resume_ev.set()
    window._bg_state = {
        "resume_ev": resume_ev, "stop_ev": threading.Event(),
        "progress": (42, 100), "done": False,
    }
    window._live_sim_action.setChecked(False)
    window._bg_poll_tick()
    assert not window._run_progress.isHidden()
    assert window._run_progress.value() == 42
    # % moved out of the status-bar text and onto its own label next to
    # the bar, so the run position and the percent are separate.
    assert "%" not in window._statusbar.currentMessage()
    assert window._run_progress_label.text() == "42%"
    assert not window._run_progress_label.isHidden()

    window._bg_state["done"] = True
    window._bg_state["result"] = ([], "", "", [], [], [], [], [], "hours")
    window._bg_poll_tick()
    assert window._run_progress.isHidden()
    assert window._run_progress_label.isHidden()


def test_bg_poll_status_text_stays_consistent_across_snapshot_jitter(window):
    """`_bg_poll_tick`'s status message keeps the same format across ticks
    when Live Sim is on, using the last known snapshot time when no
    fresh snapshot arrived on a particular tick, rather than switching
    between the "run N of M, t = X" and "N of M run(s) complete"
    formats."""
    import threading
    resume_ev = threading.Event()
    resume_ev.set()
    window._live_sim_action.setChecked(True)

    window._bg_state = {
        "resume_ev": resume_ev, "stop_ev": threading.Event(),
        "progress": (1, 10), "done": False,
        "snapshot": (12.5, []),
    }
    window._bg_poll_tick()
    msg1 = window._statusbar.currentMessage()
    assert "t = 12.500" in msg1
    assert "of 10" in msg1

    # No fresh snapshot THIS tick -- must keep showing a time (the last
    # known one), not silently drop to the no-time format.
    window._bg_state = {
        "resume_ev": resume_ev, "stop_ev": threading.Event(),
        "progress": (2, 10), "done": False,
    }
    window._bg_poll_tick()
    msg2 = window._statusbar.currentMessage()
    assert "t = 12.500" in msg2
    assert "of 10" in msg2
    assert "complete" not in msg2


def test_bg_poll_status_text_omits_time_when_live_sim_is_off(window):
    """With Live Sim OFF, the format is consistently the OTHER one (no
    time -- nothing tracks a live in-run time in that mode) on every
    tick, not a mix."""
    import threading
    resume_ev = threading.Event()
    resume_ev.set()
    window._live_sim_action.setChecked(False)
    window._bg_state = {
        "resume_ev": resume_ev, "stop_ev": threading.Event(),
        "progress": (3, 10), "done": False,
        "snapshot": (9.0, []),
    }
    window._bg_poll_tick()
    msg = window._statusbar.currentMessage()
    assert "t =" not in msg
    assert "3 of 10 run(s) complete" in msg


def test_bg_poll_shows_processing_results_before_the_final_summary(window):
    """The status bar shows "Processing results" after the run(s) finish
    but before results are parsed/rendered, then a final summary replaces
    it."""
    import threading
    messages = []
    window._statusbar.showMessage = lambda *a, **k: messages.append(
        a[0] if a else "")

    resume_ev = threading.Event()
    resume_ev.set()
    window._bg_state = {
        "resume_ev": resume_ev, "stop_ev": threading.Event(),
        "progress": (1, 1), "done": True,
        "result": ([], "", "", [], [], [], [], [], "hours"),
    }
    window._bg_poll_tick()

    assert "Processing results…" in messages
    idx = messages.index("Processing results…")
    assert any("Completed" in m or "Stopped" in m
               for m in messages[idx + 1:]), \
        "a final summary must replace the processing message"


def test_status_summary_uses_fixed_decimal_precision(window):
    """MTBDE/MDT display a fixed number of decimals regardless of value, so
    the status text does not shift as the running mean changes
    magnitude."""
    from raptor.core.engine.reporting import RunReportData
    from raptor.core.engine.stats import RunningStat

    def mk(mtbde, mdt):
        return RunReportData(
            rcond_assigned=False, stats_start_time=0.0,
            run_number=1, ending_sim_time=100.0, availability=0.9,
            mtbde=mtbde, mdt=mdt, reliability=0.0,
            conditional_reliability=0.0,
            num_system_failures=1, num_component_failures=2, mtbm=None,
            mtbmu=None, mtbms=None, mmt=None, mmtu=None, mmts=None,
            dependability=None, has_phases=False,
            green_percent=90.0, yellow_percent=5.0, red_percent=5.0,
            mission_green_percent=None, mission_yellow_percent=None,
            mission_red_percent=None, sys_fail_stat=RunningStat(),
            red_stat=RunningStat(), comp_fail_stat=RunningStat(),
            comp_repair_stat=RunningStat(), block_spares={},
        )

    window._update_status_summary([mk(22.358657, 4.846202)], "hours")
    small = window._summary_labels["MTBDE"].text()
    small_mdt = window._summary_labels["MDT"].text()
    window._update_status_summary([mk(1234.5, 4.846202)], "hours")
    big = window._summary_labels["MTBDE"].text()
    big_mdt = window._summary_labels["MDT"].text()
    # same decimal-place COUNT either side of the decimal point moving
    small_decimals = small.split(".")[1].split(" ")[0]
    big_decimals = big.split(".")[1].split(" ")[0]
    assert len(small_decimals) == len(big_decimals), \
        f"decimal precision must not change: {small!r} vs {big!r}"
    assert small_mdt == big_mdt, "MDT unaffected by MTBDE's own magnitude"


def test_escape_stops_synchronous_run_directly(window):
    """A synchronous run has no pause concept -- Esc stops it on the
    first press, same as before this feature existed."""
    window._bg_state = {}
    window._run_action.setEnabled(False)
    window._run_in_progress = True
    window._sync_stop = False

    _escape(window)
    assert window._sync_stop is True


# --- Failure Effects click-order targeting: click Fail then an element,
# or select an element then click Fail (replaces the old block-select
# combo box) -------------------------------------------------------------

@pytest.fixture
def stepping_window(qapp):
    """A separate small fixture (not HierarchyExample, which has known
    stepping-engine gaps unrelated to what these tests exercise) with a
    live StepSession already started."""
    w = MainWindow()
    w._load_into_app(load_rbd(os.path.join(
        REPO, "tests", "golden", "TwoBlockRedundancy", "input.rbd")), None)
    QApplication.instance().processEvents()
    w._on_step()
    QApplication.instance().processEvents()
    assert w._step_session is not None
    return w


def _first_block_item(window):
    v = window._diagram_dock.view
    bid = next(iter(v.blocks))
    return bid, v._block_items[bid]


def test_fev_element_first_applies_immediately(stepping_window):
    """Select a block on the canvas, then click Fail/Repair -- applies
    without arming click-to-target. (A stepped run in progress is replaced by
    Failure Effects View after a question; answer yes.)"""
    from PySide6.QtWidgets import QMessageBox
    QMessageBox.question = staticmethod(lambda *a, **k: QMessageBox.StandardButton.Yes)
    w = stepping_window
    v = w._diagram_dock.view
    _bid, item = _first_block_item(w)
    v._scene.clearSelection()
    item.setSelected(True)
    QApplication.instance().processEvents()

    w._on_fev_fail()
    assert w._fev is not None
    assert v._fev_armed_mode == "click"   # never armed for a target -- applied directly
    assert w._fev.blocks[0].op_status.value == "Repairing"
    w._leave_fev()


def test_fev_action_first_arms_then_applies_on_canvas_click(stepping_window):
    """Nothing selected: clicking Fail/Repair ARMS click-to-target
    (cursor changes -- see set_fev_armed) instead of doing nothing."""
    from PySide6.QtWidgets import QMessageBox
    QMessageBox.question = staticmethod(lambda *a, **k: QMessageBox.StandardButton.Yes)
    w = stepping_window
    v = w._diagram_dock.view
    bid, _item = _first_block_item(w)
    v._scene.clearSelection()
    QApplication.instance().processEvents()

    w._on_fev_fail()
    assert v._fev_armed_mode == "fail"
    assert "armed" in w._statusbar.currentMessage().lower()

    v.fev_target_clicked.emit(bid, "fail")
    QApplication.instance().processEvents()
    assert v._fev_armed_mode == "click"   # single-shot: back to plain clicking
    assert w._fev.model.blocks_by_id[bid].op_status.value == "Repairing"
    w._leave_fev()


def test_fev_armed_click_on_non_block_shows_status_not_crash(stepping_window):
    w = stepping_window
    v = w._diagram_dock.view
    v.set_fev_armed("fail")
    # nothing under an empty scene point -- must not raise
    from PySide6.QtCore import QPointF
    result = v._element_at(QPointF(-99999, -99999))
    assert result is None
    v._fev_armed_mode = None   # cleanup, mirrors what a real click would do


def test_fev_escape_cancels_arming(stepping_window):
    from PySide6.QtWidgets import QMessageBox
    QMessageBox.question = staticmethod(lambda *a, **k: QMessageBox.StandardButton.Yes)
    w = stepping_window
    v = w._diagram_dock.view
    v._scene.clearSelection()
    w._on_fev_fail()
    assert v._fev_armed_mode == "fail"
    v.keyPressEvent(QKeyEvent(QEvent.KeyPress, Qt.Key_Escape, Qt.NoModifier))
    assert v._fev_armed_mode == "click"   # Esc drops the arming, not the view
    w._leave_fev()
    assert v._fev_armed_mode is None


def test_fev_armed_mode_mutually_exclusive_with_delete_armed(stepping_window):
    v = stepping_window._diagram_dock.view
    v.set_fev_armed("fail")
    v.set_delete_armed(True)
    assert v._fev_armed_mode is None
    v.set_fev_armed("repair")
    assert v._delete_armed is False


def test_fev_selection_comes_from_the_canvas_not_a_combo(stepping_window):
    """The target is the canvas selection, not the removed ribbon combo box."""
    w = stepping_window
    assert not hasattr(w, "_fev_combo")
    v = w._diagram_dock.view
    bid, item = _first_block_item(w)
    v._scene.clearSelection()
    assert w._fev_selected() is None   # nothing selected
    item.setSelected(True)
    QApplication.instance().processEvents()
    kind, eid, _name = w._fev_selected()
    assert (kind, eid) == ("block", bid)


# --- "Next Time" step button: advance to the next time step rather than
# showing every event at the same timestamp -------------------------------

def test_on_step_next_time_calls_advance_until_with_flag(window):
    """Thin wiring test (mirrors test_run_to_time_respects_display_unit's
    own mock-_advance_until pattern) -- the ribbon action must request
    the new stopping condition, not silently fall back to single-step
    or step-budget behaviour."""
    captured = {}
    window._advance_until = lambda *a, **kw: captured.update(kw)
    window._on_step_next_time()
    assert captured == {"until_time_changes": True}


def test_next_time_advances_past_a_simultaneous_event_cluster(stepping_window):
    """Real engine/session (not mocked): sim time must strictly advance
    (not just re-land on the same instant), and repeated calls keep
    moving forward monotonically."""
    w = stepping_window
    session = w._step_session
    t0 = session.time
    n0 = len(session.logs)

    w._on_step_next_time()
    QApplication.instance().processEvents()
    t1 = session.time
    assert t1 > t0, "sim time must strictly advance"
    assert len(session.logs) > n0, "at least one event must have run"

    w._on_step_next_time()
    QApplication.instance().processEvents()
    assert session.time >= t1


def test_next_time_action_enabled_after_load_and_gated_with_invalid_model(
        window):
    """Gated identically to Step: enabled once a model is loaded (the
    `window` fixture already did this), disabled when the model becomes
    invalid (`_sync_simulate_enabled_state`'s own loop)."""
    assert window._step_next_time_action.isEnabled()
    assert window._step_next_time_action.isEnabled() == \
        window._step_action.isEnabled()

    window._validation_issues = lambda: ["some blocking error"]
    # Validity is memoised per model revision (`_model_is_valid`), because
    # re-validating on every selection change cost ~100 ms a click. A
    # model really becoming invalid is an EDIT, which goes through
    # `_mark_dirty`; this stand-in has to say the same.
    window._bump_model_revision()
    window._sync_simulate_enabled_state()
    assert not window._step_next_time_action.isEnabled()
    assert not window._step_action.isEnabled()


# --- Sim speed vs. actual visual refresh -----------------------------------

@pytest.mark.parametrize("delay,expected_ms", [
    (None, 30),    # Max -- fastest allowed poll, not throttled at all
    (2.0, 100),    # slowest allowed speed -- default poll is already fine
    (1.0, 100),    # 1 ev/s -- default poll is already fine
    (0.05, 50),    # 20 ev/s -- faster than default poll, must shrink
    (0.01, 30),    # 100 ev/s -- clamped at the floor, not hammered lower
])
def test_bg_poll_interval_tracks_configured_speed(window, delay, expected_ms):
    """`_bg_poll`'s interval tracks the configured simulation speed. A fixed
    100ms poll would drop every event but the last for any speed faster
    than 10 events/sec."""
    window._set_speed(delay)
    assert window._bg_poll.interval() == expected_ms


def test_bg_poll_interval_starts_at_the_documented_default(window):
    assert window._bg_poll.interval() == \
        MainWindow._BG_POLL_INTERVAL_DEFAULT == 100


# --- Ch.10 "Display Failure Effects" -- on-screen Status dialog, not
# just Write to Log -------------------------------------------------------

@pytest.fixture
def fev_window(qapp):
    w = MainWindow()
    w._load_into_app(load_rbd(os.path.join(
        REPO, "tests", "golden", "TwoBlockRedundancy", "input.rbd")), None)
    QApplication.instance().processEvents()
    assert w._enter_fev()
    yield w
    w._leave_fev()
    w._dirty = False
    w.close()


def test_fev_status_without_the_view_shows_statusbar_message_not_crash(window):
    """Before Failure Effects View is open the Status button degrades to a
    status-bar message rather than raising on a None session."""
    window._fev = None
    window._on_fev_show_status()
    assert window._fev_status_dialog is None
    assert "open failure effects view" in window._statusbar.currentMessage().lower()


def test_fev_status_dialog_shows_the_failure_effects_text(fev_window):
    w = fev_window
    w._on_fev_show_status()
    assert w._fev_status_dialog is not None
    assert w._fev_status_dialog.isVisible()
    text = w._fev_status_text.toPlainText()
    assert text == w._fev_text()
    assert text.startswith("FAILURES")


def test_fev_status_dialog_follows_the_session(fev_window):
    """After a click the open dialog shows the new state with no refresh."""
    w = fev_window
    w._on_fev_show_status()
    before = w._fev_status_text.toPlainText()
    b = w._fev.blocks[0]
    w._on_fev_target_clicked(b.id, "click")
    after = w._fev_status_text.toPlainText()
    assert after == w._fev_text() and after != before
    assert b.name in after


def test_fev_status_dialog_reused_not_stacked(fev_window):
    """Calling the Status action twice while the dialog is still open
    must reuse the same QDialog instance rather than stacking a second
    one on top."""
    w = fev_window
    w._on_fev_show_status()
    first = w._fev_status_dialog
    w._on_fev_show_status()
    assert w._fev_status_dialog is first


def test_fev_status_dialog_closes_when_the_view_is_left(fev_window):
    w = fev_window
    w._on_fev_show_status()
    dlg = w._fev_status_dialog
    assert dlg.isVisible()

    w._leave_fev()
    assert not dlg.isVisible()


# --- Ch.16/17 status-bar phase name + mission flag during step sim --------

def test_step_statusbar_shows_phase_name_and_mission_flag(qapp):
    """A phased model's step statusbar message names the current phase and
    says whether it is Mission or Non-Mission time, read from
    `SystemState.current_phase`/`mission_status`."""
    w = MainWindow()
    w._load_into_app(load_rbd(os.path.join(
        REPO, "tests", "golden", "Phasing1", "input.rbd")), None)
    QApplication.instance().processEvents()
    w._on_step()
    QApplication.instance().processEvents()
    msg = w._statusbar.currentMessage()
    assert "Phase 1/3" in msg
    assert "(Mission)" in msg or "(NonMission)" in msg


def test_step_statusbar_omits_phase_suffix_for_non_phased_model(stepping_window):
    """TwoBlockRedundancy has no phases -- the status message must not
    grow a stray "Phase 1/..." suffix for a model that never uses
    phasing at all."""
    w = stepping_window
    msg = w._statusbar.currentMessage()
    assert "Phase" not in msg


def test_fev_status_action_enabled_lifecycle(window):
    """Enabled while Failure Effects View is open, disabled otherwise."""
    assert not window._fev_status_action.isEnabled()
    window._load_into_app(load_rbd(os.path.join(
        REPO, "tests", "golden", "TwoBlockRedundancy", "input.rbd")), None)
    assert not window._fev_status_action.isEnabled()
    assert window._enter_fev()
    assert window._fev_status_action.isEnabled()
    window._leave_fev()
    assert not window._fev_status_action.isEnabled()
