"""The status bar's "approximately N left" estimate.

Pure estimator tests (no Qt) plus the label wiring on the real window.
The wording is a contract: hours or minutes for a long run, seconds for
a short one, no clock time under five minutes, and today/tomorrow spelled
out so an overnight batch cannot claim "12:30pm today" at 2am.
"""
from __future__ import annotations

import os
from datetime import datetime

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from raptor.gui.ui.run_eta import RunEstimate, format_eta  # noqa: E402

NOW = datetime(2026, 10, 26, 12, 0, 0)       # a Monday lunchtime


# -- not enough data ----------------------------------------------------

@pytest.mark.parametrize("done,total,elapsed", [
    (0, 10, 100.0),      # nothing finished
    (1, 10, 100.0),      # one run still includes the start-up cost
    (3, 3, 100.0),       # batch finished: nothing to estimate
    (3, 0, 100.0),       # no trials
    (3, 10, 0.5),        # not enough elapsed time
])
def test_no_estimate_until_the_average_means_something(done, total, elapsed):
    assert format_eta(done, total, elapsed, now=NOW) is None


# -- the wording --------------------------------------------------------

def test_hours_with_a_completion_clock():
    assert format_eta(10, 20, 3600.0, now=NOW) == (
        "Approximately 1 hr left, completion expected around 1:00pm today")


def test_half_hours_keep_one_decimal():
    assert format_eta(4, 16, 1800.0, now=NOW) == (
        "Approximately 1.5 hrs left, completion expected around 1:30pm today")


def test_minutes_with_a_completion_clock():
    # 10 done in 100 s, 90 to go -> 900 s = 15 minutes.
    assert format_eta(10, 100, 100.0, now=NOW) == (
        "Approximately 15 minutes left, completion expected around "
        "12:15pm today")


def test_seconds_are_seconds():
    # 10 done in 100 s, 14 of 24 to go -> 140 s = just over two minutes:
    # minutes, but still no clock time (under five minutes).
    assert format_eta(10, 24, 100.0, now=NOW) == (
        "Approximately 2 minutes left")
    # 2 s per run, 10 to go = 20 s.
    assert format_eta(10, 20, 20.0, now=NOW) == (
        "Approximately 20 seconds left")


def test_under_five_minutes_projects_no_clock():
    """Four minutes is inside the noise of a per-run average, so do not
    promise 12:04pm."""
    text = format_eta(10, 14, 100.0, now=NOW)     # 4 runs * 10 s = 40 s
    assert text == "Approximately 40 seconds left"
    assert "completion" not in text
    text = format_eta(10, 15, 100.0, now=NOW)     # 5 * 10 s -> 50 s
    assert "completion" not in text


def test_five_minutes_is_where_the_clock_starts():
    # 1 s per run, 300 to go = exactly five minutes.
    text = format_eta(10, 310, 10.0, now=NOW)
    assert text == ("Approximately 5 minutes left, completion expected "
                    "around 12:05pm today")


# -- overnight ----------------------------------------------------------

def test_an_overnight_run_says_tomorrow():
    late = datetime(2026, 10, 26, 23, 30, 0)
    # 2 s per run, 2700 to go = 1.5 hours -> 1:00am tomorrow.
    text = format_eta(10, 2710, 20.0, now=late)
    assert text == ("Approximately 1.5 hrs left, completion expected around "
                    "1:00am tomorrow")


def test_more_than_a_day_ahead_names_the_date():
    text = format_eta(1, 50, 1.0, now=NOW)      # treated as too little data
    assert text is None
    # 40 completed, 41 remaining at ~2 days per run?  Use a long batch:
    # 2 done in 2 s, 200000 to go -> ~2 days.
    text = format_eta(2, 200002, 2.0, now=NOW)
    assert "completion expected around" in text
    assert " on 28 Oct" in text


# -- pause bookkeeping --------------------------------------------------

def test_paused_time_does_not_inflate_the_estimate(monkeypatch):
    import raptor.gui.ui.run_eta as mod
    clock = [1000.0]
    monkeypatch.setattr(mod.time, "monotonic", lambda: clock[0])
    est = RunEstimate(started=1000.0)
    clock[0] = 1010.0                     # 10 s of work
    assert est.active_seconds() == 10.0
    est.pause()
    clock[0] = 1060.0                     # 50 s paused
    assert est.active_seconds() == 10.0
    est.resume()
    clock[0] = 1070.0                     # 10 s more work
    assert est.active_seconds() == 20.0


# -- the window's status message ----------------------------------------

@pytest.fixture(scope="module")
def qapp():
    from PySide6.QtWidgets import QApplication
    return QApplication.instance() or QApplication([])


def test_the_run_status_message_carries_the_sentence(qapp):
    from raptor.gui.ui.main_window import MainWindow

    win = MainWindow()

    class _Stub:
        def text(self, done, total):
            return ("Approximately 35 minutes left, completion expected "
                    "around 12:30pm today")

    win._run_eta = _Stub()
    assert win._run_status_text("Running (1 core) — run 3 of 10", 3, 10) == (
        "Running (1 core) — run 3 of 10 · Approximately 35 minutes left, "
        "completion expected around 12:30pm today")

    win._run_eta = None
    assert win._run_status_text("Running (1 core) — run 3 of 10", 3, 10) == (
        "Running (1 core) — run 3 of 10")
    win.close()


def test_the_background_poll_puts_the_estimate_in_the_status_bar(qapp):
    """The progress poll is the background run's only writer; the estimate
    must ride the same message, not a second place the user has to find."""
    import threading
    import time

    from raptor.gui.ui.main_window import MainWindow

    win = MainWindow()
    win._run_eta = RunEstimate(started=time.monotonic() - 100)
    resume = threading.Event()
    resume.set()
    win._bg_state = {
        "resume_ev": resume, "stop_ev": threading.Event(),
        "progress": (10, 20), "done": False, "workers": 1,
    }
    win._bg_poll_tick()
    message = win._statusbar.currentMessage()
    # Depending on whether a worker tick landed between arming the state
    # and the poll, the progress half reads "10 of 20 run(s) complete" or
    # the in-flight "run 11 of 20". Both are the same 10/20 point; the
    # estimate must ride the same message.
    assert "of 20" in message, message
    assert "Approximately 2 minutes left" in message
    win._clear_run_eta()
    win.close()


# -- importance measures (#3) ---------------------------------------------

def test_the_estimate_covers_the_importance_batches_too():
    """The baseline's average per run, times every batch still to come."""
    est = RunEstimate(started=__import__("time").monotonic() - 100, extra_batches=9)
    text = est.text(10, 100, now=datetime(2026, 1, 1, 9, 0))
    plain = RunEstimate(started=__import__("time").monotonic() - 100).text(
        10, 100, now=datetime(2026, 1, 1, 9, 0))
    assert text.endswith("(including importance measures)")
    # 10 s a run: 90 runs left alone, 990 with nine more batches of 100
    assert plain.startswith("Approximately 15 minutes left")
    assert text.startswith("Approximately 2.8 hrs left")


def test_no_importance_batches_leaves_the_sentence_unchanged():
    est = RunEstimate(started=__import__("time").monotonic() - 100)
    assert "importance" not in est.text(10, 100)


def test_the_baseline_estimate_stays_until_the_importance_phase(qapp):
    import time
    from raptor.gui.ui.main_window import MainWindow

    win = MainWindow()
    win._run_eta = RunEstimate(started=time.monotonic() - 100, extra_batches=4)
    assert "including importance measures" in win._run_eta_text(10, 20)
    win._clear_run_eta()
    win.close()


def test_the_importance_phase_keeps_a_time_estimate(qapp):
    """The estimate used to be cleared here and never came back."""
    import time
    from raptor.gui.ui.main_window import MainWindow

    win = MainWindow()
    win._imp_eta = RunEstimate(started=time.monotonic() - 60)
    win._update_importance_progress(11, 101, "perfect:Pump")   # 10 of 100 variants
    message = win._statusbar.currentMessage()
    assert "variant 11 of 101" in message
    assert "Approximately" in message and "left" in message, message
    win._end_importance_progress("done")
    assert win._imp_eta is None
    win.close()


def test_the_importance_estimate_waits_for_two_variants(qapp):
    from raptor.gui.ui.main_window import MainWindow

    win = MainWindow()
    win._update_importance_progress(1, 101, "starting")
    assert "Approximately" not in win._statusbar.currentMessage()
    win._end_importance_progress("done")
    win.close()
