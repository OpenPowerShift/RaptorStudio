"""A run that includes importance measures: the wait, not the arithmetic.

`test_importance.py` pins the numbers and `test_importance_surfaces.py`
pins the tab/overlay wiring. This file pins the lifecycle reported in
#210/#212/#213:

* the baseline's Final Results appear as soon as the baseline is done,
  while importance is still running;
* importance never runs on the GUI thread, and the event loop keeps
  firing (Help must stay usable);
* the status line/bar describe importance, not the baseline's stale
  `Final results report 100/100`;
* repeated polls are deduplicated and cannot advance while paused;
* the final refresh adds the Importance tab without moving the reader.
"""
from __future__ import annotations

import os
import threading
import time

import pytest

pytest.importorskip("PySide6")

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QTimer
from PySide6.QtWidgets import QApplication

from raptor.core.fileio.rbd import load_rbd
from raptor.gui.ui.main_window import MainWindow

REPO = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
TWO_BLOCK = os.path.join(REPO, "tests", "golden", "TwoBlockRedundancy",
                         "input.rbd")

SAMPLE = {
    "mode": "both", "runs": 100, "batches": 5, "blocks": 2,
    "metrics": {"Availability": {
        "baseline": 0.9,
        "rows": [
            {"block": "A", "block_id": 3, "birnbaum": 0.02, "raw": 1.5,
             "rrw": 1.1, "vesely_fussell": 0.2, "perfect": 0.92,
             "failed": 0.90, "sem": 0.001},
            {"block": "B", "block_id": 4, "birnbaum": 0.30, "raw": 4.0,
             "rrw": 1.4, "vesely_fussell": 0.5, "perfect": 0.95,
             "failed": 0.65, "sem": 0.002},
        ]}},
    "notes": [],
}


@pytest.fixture(scope="module")
def qapp():
    return QApplication.instance() or QApplication([])


def _window(qapp) -> MainWindow:
    win = MainWindow()
    win._load_into_app(load_rbd(TWO_BLOCK), None)
    return win


def _state(**over):
    """The shape `_start_background_run`'s worker leaves behind after the
    baseline, while importance is still running."""
    state = {
        "done": False,
        "progress": (2, 2),
        "paused": False,
        "phase": "importance",
        "baseline_ready": True,
        "importance_total": 5,
        "importance_progress": (2, 5, "perfect:Pump A"),
        # The baseline's last report stage: the exact tuple that used to
        # be repainted over the importance status every 100 ms.
        "stage": ("Final results report", 100, 100),
        "result": (["event"], "", ["Final results"], [], [], [], [], [],
                   "hours"),
        "resume_ev": threading.Event(),
        "stop_ev": threading.Event(),
    }
    state["resume_ev"].set()
    state.update(over)
    return state


def _report():
    """One real-shaped RunReportData, enough for the Overview cards."""
    from raptor.core.engine.reporting import RunReportData
    from raptor.core.engine.stats import RunningStat

    return RunReportData(
        rcond_assigned=False, stats_start_time=0.0, run_number=1,
        ending_sim_time=100.0, availability=0.9, mtbde=20.0, mdt=2.0,
        reliability=0.8, conditional_reliability=0.8,
        num_system_failures=1, num_component_failures=2, mtbm=None,
        mtbmu=None, mtbms=None, mmt=None, mmtu=None, mmts=None,
        dependability=None, has_phases=False, green_percent=90.0,
        yellow_percent=5.0, red_percent=5.0, mission_green_percent=None,
        mission_yellow_percent=None, mission_red_percent=None,
        sys_fail_stat=RunningStat(), red_stat=RunningStat(),
        comp_fail_stat=RunningStat(), comp_repair_stat=RunningStat(),
        block_spares={})


def test_baseline_results_are_visible_while_importance_is_still_running(qapp):
    win = _window(qapp)
    win._bg_state = _state()

    win._bg_poll_tick()

    assert win._results.has_results(), "the finished baseline stayed hidden"
    assert win._results._importance is None, "importance has not finished"
    message = win.statusBar().currentMessage()
    assert "Importance" in message
    assert "variant 1 of 4" in message
    assert "Final results report" not in message
    # Variant fraction: (2 - 1) / (5 - 1) = 25%.
    assert win._run_progress.value() == 25
    assert win._run_progress.value() < 100
    win.close()


def test_successive_polls_advance_monotonically_and_never_claim_100(qapp):
    win = _window(qapp)
    state = _state(importance_progress=(2, 5, "perfect:Pump A"))
    win._bg_state = state

    win._bg_poll_tick()
    first = win._run_progress.value()
    state["importance_progress"] = (3, 5, "perfect:Pump B")
    win._bg_poll_tick()
    second = win._run_progress.value()
    state["importance_progress"] = (5, 5, "failed:Pump B")
    win._bg_poll_tick()
    last = win._run_progress.value()

    assert first < second < last, (first, second, last)
    assert last < 100, "the bar must stay below 100% until importance returns"
    message = win.statusBar().currentMessage()
    assert "variant 4 of 4" in message and "failed:Pump B" in message
    assert not state["done"]
    win.close()


def test_pause_does_not_advance_or_rewrite_the_importance_line(qapp):
    win = _window(qapp)
    state = _state()
    win._bg_state = state
    win._bg_poll_tick()                 # presents the baseline once
    before = win._run_progress.value()

    state["paused"] = True
    writes: list[str] = []
    real_show = win._statusbar.showMessage

    def record(*a, **k):
        writes.append(a[0] if a else "")
        return real_show(*a, **k)

    win._statusbar.showMessage = record
    for _ in range(4):                  # several 100 ms polls while paused
        win._bg_poll_tick()

    assert win._run_progress.value() == before
    assert len(writes) == 1, writes    # repeated identical writes are deduped
    assert "paused" in writes[0].lower() and "Importance" in writes[0]
    assert not win._results._importance
    assert not state["done"]
    win.close()


def test_final_refresh_adds_importance_without_losing_the_readers_tab(qapp):
    win = _window(qapp)
    state = _state()
    win._bg_state = state
    win._bg_poll_tick()                 # baseline population

    tabs = win._results._details_tabs
    idx = next(i for i in range(tabs.count())
               if tabs.tabText(i) == "Weak Link")
    tabs.setCurrentIndex(idx)

    state["importance_progress"] = (5, 5, "failed:Pump B")
    state["importance"] = SAMPLE
    state["phase"] = "done"
    state["done"] = True
    win._bg_poll_tick()                 # final refresh

    assert win._results._importance == SAMPLE
    names = [tabs.tabText(i) for i in range(tabs.count())]
    assert "Importance" in names
    assert tabs.tabText(tabs.currentIndex()) == "Weak Link", (
        "the refresh moved the user off the tab they were reading")
    win.close()


def test_importance_runs_off_the_gui_thread_and_the_loop_keeps_beating(
        qapp, monkeypatch):
    from raptor.api import importance as imp_mod

    main_thread = threading.get_ident()
    seen = {"thread": None, "times": []}

    def slow_analyse(_rbd, _baseline, *, mode="both", runs=None, workers=1,
                     seed=None, progress=None, stop_check=None):
        seen["thread"] = threading.get_ident()
        if progress is not None:
            progress(2, 3, "perfect:A")
        time.sleep(0.45)                # long enough for several heartbeats
        return SAMPLE

    monkeypatch.setattr(imp_mod, "analyse", slow_analyse)
    win = _window(qapp)
    experiment = win._current_experiment()
    experiment["importance"] = "both"
    win._rbd.experiment = experiment

    heartbeat = QTimer()
    heartbeat.setInterval(20)
    heartbeat.timeout.connect(lambda: seen["times"].append(time.monotonic()))
    heartbeat.start()
    try:
        data = win._importance_for(win._rbd, [object()])
    finally:
        heartbeat.stop()

    assert data == SAMPLE
    assert seen["thread"] is not None
    assert seen["thread"] != main_thread, "importance ran on the GUI thread"
    gaps = [b - a for a, b in zip(seen["times"], seen["times"][1:])]
    assert gaps, "the event loop was not pumped while importance ran"
    assert max(gaps) < 0.20, f"event loop stalled for {max(gaps):.2f}s"
    win.close()


def test_a_synchronous_run_shows_the_baseline_before_slow_importance(
        qapp, monkeypatch):
    """End-to-end on the default (synchronous/animated) Run path."""
    from raptor.api import importance as imp_mod

    started = threading.Event()

    def slow_analyse(_rbd, _baseline, *, mode="both", runs=None, workers=1,
                     seed=None, progress=None, stop_check=None):
        started.set()
        time.sleep(0.4)
        return SAMPLE

    monkeypatch.setattr(imp_mod, "analyse", slow_analyse)
    win = _window(qapp)
    win._set_exp_field("trials", 2)
    experiment = win._current_experiment()
    experiment["importance"] = "both"
    win._rbd.experiment = experiment
    win._dirty = False

    checks = {"beats": 0, "baseline": False, "importance": None,
              "status": ""}
    heartbeat = QTimer()
    heartbeat.setInterval(20)

    def look():
        checks["beats"] += 1
        if (started.is_set() and win._results.has_results()
                and not checks["baseline"]):
            checks["baseline"] = True
            checks["importance"] = win._results._importance
            checks["status"] = win.statusBar().currentMessage()

    heartbeat.timeout.connect(look)
    heartbeat.start()
    try:
        win._run_simulation()
    finally:
        heartbeat.stop()

    assert checks["beats"] > 0
    assert checks["baseline"], "Final Results were withheld until importance"
    assert checks["importance"] is None, "importance is still running here"
    assert "Importance" in checks["status"]
    assert win._results._importance == SAMPLE
    win._dirty = False
    win.close()


# --------------------------------------------------- failure / stop paths
def test_an_epilogue_failure_still_unlocks_the_run(qapp, monkeypatch):
    """#1: a raise while presenting the baseline must not strand the app."""
    win = _window(qapp)
    win._set_exp_field("trials", 1)
    experiment = win._current_experiment()
    experiment["importance"] = "both"
    win._rbd.experiment = experiment
    win._dirty = False

    def explode(**_kwargs):
        raise RuntimeError("baseline presentation failed")

    monkeypatch.setattr(win, "_present_baseline_results", explode)
    with pytest.raises(RuntimeError, match="baseline presentation failed"):
        win._run_simulation()

    assert win._run_in_progress is False
    assert win._sync_stop is False
    assert win._importance_running is False
    assert win._imp_eta is None
    assert win._imp_last_key is None
    assert win._pause_action.text() == "Pause"
    win._dirty = False
    win.close()


def test_a_raising_progress_repaint_cannot_hang_the_nested_loop(
        qapp, monkeypatch):
    """#2: loop.quit() is in a `finally`; a UI error still returns."""
    import signal

    from raptor.api import importance as imp_mod

    def slow_analyse(_rbd, _baseline, *, mode="both", runs=None, workers=1,
                     seed=None, progress=None, stop_check=None):
        if progress is not None:
            progress(2, 5, "perfect:A")
        for _ in range(30):
            time.sleep(0.01)
            if stop_check is not None and stop_check():
                raise RuntimeError("cancelled")
        return SAMPLE

    monkeypatch.setattr(imp_mod, "analyse", slow_analyse)
    win = _window(qapp)
    experiment = win._current_experiment()
    experiment["importance"] = "both"
    win._rbd.experiment = experiment

    calls = [0]
    real_update = win._update_importance_progress

    def explode_after_start(*args, **kwargs):
        calls[0] += 1
        if calls[0] >= 2:
            raise RuntimeError("progress repaint failed")
        return real_update(*args, **kwargs)

    win._update_importance_progress = explode_after_start

    def _timeout(_signum, _frame):
        raise TimeoutError("nested importance loop did not exit")

    old = signal.signal(signal.SIGALRM, _timeout)
    signal.setitimer(signal.ITIMER_REAL, 3.0)
    try:
        with pytest.raises(RuntimeError, match="progress repaint failed"):
            win._analyse_importance_off_thread(
                win._rbd, [object()], mode="both", batches=5)
    finally:
        signal.setitimer(signal.ITIMER_REAL, 0)
        signal.signal(signal.SIGALRM, old)
    win.close()


def test_stop_during_the_nested_loop_releases_the_run(qapp, monkeypatch):
    """#1/#6: Stop during importance returns, unlocks and keeps baseline."""
    from raptor.api import importance as imp_mod

    def slow_analyse(_rbd, _baseline, *, mode="both", runs=None, workers=1,
                     seed=None, progress=None, stop_check=None):
        if progress is not None:
            progress(2, 5, "perfect:A")
        for _ in range(50):
            time.sleep(0.01)
            if stop_check is not None and stop_check():
                raise RuntimeError("cancelled")
        return SAMPLE

    monkeypatch.setattr(imp_mod, "analyse", slow_analyse)
    win = _window(qapp)
    win._set_exp_field("trials", 1)
    experiment = win._current_experiment()
    experiment["importance"] = "both"
    win._rbd.experiment = experiment
    win._dirty = False

    QTimer.singleShot(150, win._stop_bg_run)
    started = time.monotonic()
    win._run_simulation()
    elapsed = time.monotonic() - started

    assert elapsed < 5.0, "Stop did not interrupt the nested importance loop"
    assert win._results.has_results()
    assert win._results._importance is None
    assert win._run_in_progress is False
    assert win._sync_stop is False
    assert win._importance_running is False
    assert win._pause_action.text() == "Pause"
    win._dirty = False
    win.close()


def test_bookkeeping_failure_keeps_the_completed_baseline(
        qapp, monkeypatch):
    """#3: block_rows/batch_count failure is note-only."""
    win = _window(qapp)
    win._set_exp_field("trials", 1)
    experiment = win._current_experiment()
    experiment["importance"] = "both"
    win._rbd.experiment = experiment
    win._dirty = False

    from raptor.api import importance as imp_mod

    monkeypatch.setattr(
        imp_mod, "batch_count",
        lambda _n, _mode: (_ for _ in ()).throw(
            ValueError("bookkeeping exploded")))
    win._start_background_run(1)
    deadline = time.monotonic() + 20.0
    while (win._bg_poll.isActive() and time.monotonic() < deadline):
        qapp.processEvents()
        time.sleep(0.01)
    qapp.processEvents()

    state = win._bg_state
    assert state.get("done"), "background run did not finish"
    assert state.get("error") is None
    assert state.get("importance") is None
    assert any("importance measures were not calculated"
               in note for note in state["result"][4])
    assert win._results.has_results(), "the baseline was discarded"
    assert win._results._importance is None
    assert win._run_in_progress is False
    win._dirty = False
    win.close()


# ------------------------------------------------ status / percentage rules
def test_status_reasserts_after_another_writer_overwrites_it(qapp):
    """#8: key dedupe must not leave a stale 'Processing results' up."""
    win = _window(qapp)
    win._update_importance_progress(2, 5, "perfect:A")
    first = win.statusBar().currentMessage()
    assert "Importance" in first

    win.statusBar().showMessage("Processing results — something else")
    win._update_importance_progress(2, 5, "perfect:A")
    assert win.statusBar().currentMessage() == first
    win.close()


def test_percentage_ceil_and_message_numerator_match(qapp):
    """#16: >100 variants must not read 0% after the first one."""
    win = _window(qapp)
    win._update_importance_progress(2, 300, "perfect:A")
    message = win.statusBar().currentMessage()
    assert "variant 1 of 299" in message
    assert "(1%)" in message
    assert win._run_progress.value() == 1

    win._update_importance_progress(300, 300, "failed:Z")
    assert "variant 299 of 299" in win.statusBar().currentMessage()
    assert win._run_progress.value() == 99
    win.close()


def test_importance_progress_ignores_the_baseline_report_stage(qapp):
    """#9/#13: only phase == baseline may paint the baseline's 100/100."""
    win = _window(qapp)
    base = _state(phase="baseline", baseline_ready=False,
                  importance_progress=None)
    win._bg_state = base
    win._bg_last_progress = (2, 2, False)
    win._bg_last_bar = (2, 2)
    win._bg_poll_tick()
    assert win._run_progress.value() == 100

    base.update({"phase": "importance", "baseline_ready": True,
                 "importance_progress": (2, 5, "perfect:A")})
    win._bg_poll_tick()
    message = win.statusBar().currentMessage()
    assert "Final results report" not in message
    assert "Importance" in message
    assert win._run_progress.value() < 100
    win.close()


def test_live_plot_catchup_is_skipped_during_importance(qapp):
    """#5: a baseline arriving late must not re-show Results/Graphs."""
    win = _window(qapp)
    state = _state(
        result=(["event"], "", ["Final results"], [_report()], [],
                ["ao"], ["cap"], [], "hours"))
    win._bg_state = state

    def must_not_run(*_a, **_k):
        raise AssertionError("live Ao/Capacity catch-up ran during importance")

    win._results.update_live_capacity = must_not_run
    win._results.update_live_ao = must_not_run
    win._bg_poll_tick()
    assert win._results.has_results()
    assert win._bg_last_report_count == 1
    win.close()


def test_checked_overlay_survives_a_full_refresh(qapp):
    """#4: a same-run results refresh restores the user's overlay."""
    win = _window(qapp)
    view = win._diagram_dock.view
    win._weak_color_action.setEnabled(True)
    win._weak_color_action.setChecked(True)
    state = win._checked_overlay_state()

    # What _sync_overlay_actions does before re-enabling: clear the canvas.
    view.clear_result_overlays()
    calls: list[str] = []
    win._toggle_weak_colors = lambda: calls.append("weak")
    win._reapply_checked_overlays(state)
    assert calls == ["weak"]
    win.close()


def test_background_done_keeps_the_run_locked_until_results_exist(
        qapp, monkeypatch):
    """#13: Run cannot start while the final result population runs."""
    win = _window(qapp)
    state = _state(done=True, phase="done", baseline_ready=True,
                   importance=SAMPLE)
    win._bg_state = state

    seen: list[bool] = []
    real_populate = win._set_results_with_progress

    def observe(*args, **kwargs):
        seen.append(bool(win._run_in_progress))
        return real_populate(*args, **kwargs)

    win._set_results_with_progress = observe
    win._run_in_progress = True
    win._bg_poll_tick()
    assert seen == [True], "background unlocked before its results were shown"
    assert win._run_in_progress is False
    win.close()


def test_pause_label_resets_at_run_start(qapp):
    """#15: a previous paused run must not leave 'Resume' behind."""
    win = _window(qapp)
    win._pause_action.setText("Resume")
    win._start_run_eta()
    assert win._pause_action.text() == "Pause"
    win._clear_run_eta()
    win.close()


def test_importance_cancels_if_stop_lands_during_the_last_variant(monkeypatch):
    """#6: a partial final batch is not published as a complete ranking."""
    from raptor.api import importance as imp
    import raptor.core.engine.runner as runner

    rbd = load_rbd(TWO_BLOCK)
    fired = [False]

    def fake_run(*_args, **_kwargs):
        fired[0] = True
        return None

    monkeypatch.setattr(runner, "run_simulation", fake_run)
    with pytest.raises(RuntimeError, match="cancelled"):
        imp._run_variants(
            rbd, ("perfect",), [(3, "A")], runs=1, length=100.0,
            workers=1, seed=None, progress=None,
            stop_check=lambda: fired[0], total=2)


def test_baseline_presentation_cannot_be_reentered(qapp):
    """#12: the presentation flag is set before the pumped population."""
    win = _window(qapp)
    state = _state()
    win._bg_state = state
    calls: list[int] = []
    real = win._present_background_baseline

    def present(current):
        calls.append(1)
        win._bg_poll_tick()        # re-entrant poll during the presentation
        return real(current)

    win._present_background_baseline = present
    win._bg_poll_tick()
    assert calls == [1], "baseline results were presented twice"
    win.close()


def test_background_pause_blocks_between_importance_variants(
        qapp, monkeypatch):
    """Pause must stop variants, not merely relabel the button."""
    from raptor.api import importance as imp_mod

    started = threading.Event()

    def slow_analyse(_rbd, _baseline, *, mode="both", runs=None, workers=1,
                     seed=None, progress=None, stop_check=None):
        started.set()
        if progress is not None:
            progress(2, 5, "perfect:A")
        for i in range(20):
            time.sleep(0.02)
            if stop_check is not None and stop_check():
                raise RuntimeError("cancelled")
            if progress is not None:
                progress(3 + i, 5, f"failed:B{i}")
        return SAMPLE

    monkeypatch.setattr(imp_mod, "analyse", slow_analyse)
    win = _window(qapp)
    win._set_exp_field("trials", 1)
    experiment = win._current_experiment()
    experiment["importance"] = "both"
    win._rbd.experiment = experiment
    win._dirty = False

    win._start_background_run(1)
    deadline = time.monotonic() + 20.0
    while time.monotonic() < deadline:
        qapp.processEvents()
        state = win._bg_state or {}
        if started.is_set() and state.get("importance_progress"):
            break
        time.sleep(0.01)
    assert started.is_set(), "importance never started"

    win._toggle_bg_pause()
    deadline = time.monotonic() + 3.0
    while (not (win._bg_state or {}).get("paused")
           and time.monotonic() < deadline):
        qapp.processEvents()
        time.sleep(0.01)
    assert win._bg_state.get("paused"), "worker did not observe the pause"
    frozen = win._bg_state.get("importance_progress")

    end = time.monotonic() + 0.25
    while time.monotonic() < end:
        qapp.processEvents()
        time.sleep(0.01)
    assert win._bg_state.get("importance_progress") == frozen, (
        "a variant completed while paused")
    assert not win._bg_state.get("done"), "paused run completed"

    win._toggle_bg_pause()
    deadline = time.monotonic() + 20.0
    while win._bg_poll.isActive() and time.monotonic() < deadline:
        qapp.processEvents()
        time.sleep(0.01)
    qapp.processEvents()
    assert win._bg_state.get("done")
    assert win._bg_state.get("importance") == SAMPLE
    assert win._results.has_results()
    assert win._pause_action.text() == "Pause"
    win._dirty = False
    win.close()


def test_results_refresh_preserves_tab_scroll_and_selectors(qapp):
    """#7/#11: a same-run refresh must not move the reader."""
    from raptor.gui.ui.results import ResultsDock

    dock = ResultsDock()
    dock.resize(560, 280)
    dock.show()
    qapp.processEvents()
    fin = ["***SUMMARY***",
           "Minimum Mean Maximum St.Dev SEM"]
    for i in range(80):
        fin.append(f"Measure{i} 0.1 0.2 0.3 0.01 0.001")
    logs = [[f"{i * 0.1:.1f}\tBlock\talpha\tGood\tx"
             for i in range(60)],
            [f"{i * 0.1:.1f}\tBlock\tbeta\tFailed\ty"
             for i in range(60)]]
    ao = ["0 0.9 0.9 0.05 0.05 1",
          "**************End_of_Run_1**************",
          "0 0.8 0.8 0.1 0.1 1"]

    def populate():
        dock.set_results(logs, ["eor1", "eor2"], fin, report_data=[],
                         ao_lines=ao)

    populate()
    qapp.processEvents()
    dock._run_selector.setCurrentIndex(1)
    dock._graph_run.setCurrentIndex(1)
    dock._raw_tabs.setCurrentIndex(1)
    raw = dock._raw_tabs.currentWidget()
    raw.verticalScrollBar().setValue(20)
    title = dock._details_tabs.tabText(dock._details_tabs.currentIndex())
    page = dock._details_tabs.currentWidget()
    page.verticalScrollBar().setValue(100)
    qapp.processEvents()
    assert page.verticalScrollBar().maximum() > 0
    assert raw.verticalScrollBar().maximum() > 0
    raw_scroll = raw.verticalScrollBar().value()

    populate()
    qapp.processEvents()
    qapp.processEvents()

    assert dock._run_selector.currentIndex() == 1
    assert dock._graph_run.count() > 1
    assert dock._graph_run.currentIndex() == 1
    assert dock._raw_tabs.currentIndex() == 1
    assert dock._raw_tabs.currentWidget().verticalScrollBar().value() == \
        raw_scroll
    assert dock._details_tabs.tabText(dock._details_tabs.currentIndex()) == \
        title
    assert dock._details_tabs.currentWidget().verticalScrollBar().value() == \
        100
    dock.close()
    dock.deleteLater()
