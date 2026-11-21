"""The end-of-run attribution pass and its use by the Results dock."""
from __future__ import annotations

import os
import time

import pytest

pytest.importorskip("PySide6")
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication

from raptor.api.insight.attribution import attribute_batch
from raptor.core.engine.runner import run_simulation_with_reports
from raptor.core.fileio.rbd import load_rbd
from raptor.gui.ui.results import ResultsDock

REPO = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
FIXTURE = os.path.join(REPO, "tests/golden/TwoBlockRedundancy/input.rbd")


@pytest.fixture(scope="module")
def qapp():
    return QApplication.instance() or QApplication([])


@pytest.fixture(scope="module")
def batch(qapp):
    rbd = load_rbd(FIXTURE)
    reports: list = []
    logs, eor, fin = run_simulation_with_reports(
        rbd, num_runs=3, date="fixed", report_sink=reports)
    return rbd, reports, logs, eor, fin


def _window(qapp):
    from raptor.gui.ui import settings
    from raptor.gui.ui.main_window import MainWindow
    settings.set_value("startup/show_start_page", False)
    win = MainWindow()
    win._load_into_app(load_rbd(FIXTURE), None)
    win.show()
    return win


def _finish_background(win, qapp, runs=2, timeout=40.0):
    """Start a background batch and pump until its poll has stopped."""
    win._start_background_run(runs)
    deadline = time.monotonic() + timeout
    while win._bg_poll.isActive() and time.monotonic() < deadline:
        qapp.processEvents()
        time.sleep(0.01)
    qapp.processEvents()
    assert win._bg_state.get("done"), "the background run did not finish"
    return win._bg_state


def test_attribute_batch_has_drivers_and_outages(batch):
    rbd, _reports, logs, _eor, _fin = batch
    attribution = attribute_batch(rbd, logs, units="hours")
    assert attribution is not None
    assert attribution["runs"] == 3
    assert attribution["units"] == "hours"
    assert attribution["elements"]
    assert "outages" in attribution


def test_no_event_log_is_not_an_attribution(batch):
    rbd, _reports, _logs, _eor, _fin = batch
    assert attribute_batch(rbd, []) is None
    assert attribute_batch(rbd, [[], []]) is None
    assert attribute_batch(rbd, None) is None


def test_an_empty_log_does_not_take_the_next_runs_capacity(batch, monkeypatch):
    """The capacity sink has one track per run: dropping a run without an
    event log must drop its track too, not shift every later run's flow."""
    import raptor.api.insight.attribution as attr
    batch_counts = [10, 20, 30]
    tracks = [object(), object(), object()]
    seen: dict = {}

    def capture(logs, *args, **kwargs):
        seen["logs"] = list(logs)
        seen["capacity"] = list(kwargs["capacity"])
        seen["phases"] = kwargs["with_outages"]
        return {"runs": len(seen["logs"])}

    monkeypatch.setattr(attr, "attribute_logs", capture)
    result = attribute_batch(object(), [batch_counts[0], [], batch_counts[2]],
                             capacity=tracks, units="hours")
    assert result == {"runs": 2, "units": "hours"}
    assert seen["logs"] == [batch_counts[0], batch_counts[2]]
    assert seen["capacity"] == [tracks[0], tracks[2]]
    assert seen["phases"] is True


def test_a_broken_capacity_split_keeps_the_drivers(batch, monkeypatch, caplog):
    import raptor.api.insight.attribution as attr
    import raptor.api.insight.capacity_attribution as capacity
    rbd, _reports, logs, _eor, _fin = batch

    def explode(*_args, **_kwargs):
        raise ValueError("capacity exploded")

    monkeypatch.setattr(capacity, "capacity_runs", explode)
    with caplog.at_level("ERROR", logger=attr.log.name):
        attribution = attribute_batch(rbd, logs, capacity_lines=["row"],
                                      units="hours")
    assert attribution is not None and attribution["elements"]
    assert not attribution.get("capacity")
    assert "lost-capacity split failed" in caplog.text


def test_the_capacity_split_can_be_stopped(monkeypatch):
    """Stop must land while the split is still being built, not only once
    the replay starts."""
    import raptor.api.insight.capacity_attribution as capacity
    from raptor.api.progress import Cancelled

    def explode(*_args, **kwargs):
        assert "abort" in kwargs
        raise Cancelled()

    monkeypatch.setattr(capacity, "capacity_runs", explode)
    with pytest.raises(Cancelled):
        attribute_batch(object(), [[1]], capacity_lines=["row"])


def test_the_foreground_run_path_computes_drivers_with_progress(batch, qapp):
    win = _window(qapp)
    rbd, _reports, logs, _eor, _fin = batch
    seen = []
    original = win._statusbar.showMessage

    def note(text, *args, **kwargs):
        seen.append(text)
        return original(text, *args, **kwargs)

    win._statusbar.showMessage = note
    try:
        attribution = win._compute_drivers_now(
            rbd, logs, {"manifest": {"experiment": {}}}, "hours")
        assert attribution["runs"] == 3
        assert any("working out what caused the downtime" in text.lower()
                   for text in seen)
        # the bar is back in percent for the stages that follow (results,
        # importance)
        assert win._run_progress.maximum() == 100
    finally:
        win._dirty = False
        win.close()
        win.deleteLater()


def test_the_foreground_pass_stops_with_the_stop_button(batch, qapp):
    win = _window(qapp)
    rbd, _reports, logs, _eor, _fin = batch
    win._sync_stop = True
    try:
        assert win._compute_drivers_now(
            rbd, logs, {"manifest": {"experiment": {}}}, "hours") is None
        assert win._run_progress.maximum() == 100
    finally:
        win._sync_stop = False
        win._dirty = False
        win.close()
        win.deleteLater()


def test_a_failed_foreground_pass_is_a_note_not_a_lost_batch(
        batch, qapp, monkeypatch):
    import raptor.api.insight.attribution as attr
    win = _window(qapp)
    rbd, _reports, logs, _eor, _fin = batch
    monkeypatch.setattr(
        attr, "attribute_batch",
        lambda *_a, **_k: (_ for _ in ()).throw(RuntimeError("boom")))
    notes: list = []
    try:
        assert win._compute_drivers_now(
            rbd, logs, {"manifest": {"experiment": {}}}, "hours",
            notes=notes) is None
        assert any("downtime attribution was not calculated" in note
                   for note in notes)
    finally:
        win._dirty = False
        win.close()
        win.deleteLater()


def test_results_dock_applies_a_precomputed_attribution_without_replay(
        batch, monkeypatch):
    rbd, reports, logs, eor, fin = batch
    attribution = attribute_batch(rbd, logs, units="hours")
    dock = ResultsDock()
    dock.set_results(logs, eor, fin, report_data=reports, units="hours",
                     rbd=rbd, attribution=attribution)
    assert dock.attribution()["runs"] == 3
    assert dock._drivers_page.attribution()["elements"]

    # A second population of the same batch must not replay the log:
    # make the replay explode if the dock falls back to it.
    import raptor.api.insight.attribution as attr
    monkeypatch.setattr(attr, "attribute_logs",
                        lambda *a, **k: (_ for _ in ()).throw(
                            AssertionError("replayed the event log")))
    dock.set_results(logs, eor, fin, report_data=reports, units="hours",
                     rbd=rbd, attribution=attribution)
    assert dock.attribution()["runs"] == 3


def test_a_deferred_pass_drops_the_previous_runs_drivers(batch, monkeypatch):
    """A result set with no attribution of its own (step, stopped, older)
    must not leave the previous run's drivers where the Story reads them."""
    rbd, reports, logs, eor, fin = batch
    attribution = attribute_batch(rbd, logs, units="hours")
    dock = ResultsDock()
    try:
        dock.set_results(logs, eor, fin, report_data=reports, units="hours",
                         rbd=rbd, attribution=attribution)
        assert dock.attribution() is not None
        monkeypatch.setattr(ResultsDock, "DRIVERS_EAGER_LINES", 0)
        dock.set_results(logs, eor, fin, report_data=reports, units="hours",
                         rbd=rbd)
        assert dock.attribution() is None
        assert dock._outages is None and dock._period_summary is None
        assert dock._drivers_stale
    finally:
        dock.deleteLater()


def test_cancelling_the_lazy_pass_says_so(batch, monkeypatch):
    """Cancel must not look like "the run recorded no event log"."""
    from PySide6 import QtWidgets
    from raptor.gui.ui import drivers_page
    rbd, reports, logs, eor, fin = batch

    class _CancelledDialog:
        def __init__(self, *_args, **_kwargs):
            self.cancelled = True

        def __getattr__(self, name):
            return lambda *_a, **_k: None

        def wasCanceled(self):
            return self.cancelled

    monkeypatch.setattr(QtWidgets, "QProgressDialog", _CancelledDialog)
    monkeypatch.setattr(ResultsDock, "DRIVERS_EAGER_LINES", 0)
    dock = ResultsDock()
    try:
        # the Drivers tab is the one on screen: the lazy pass runs for it
        dock._tabs.setCurrentWidget(dock._drivers_page)
        dock.set_results(logs, eor, fin, report_data=reports, units="hours",
                         rbd=rbd)
        assert dock.attribution() is None
        assert dock._drivers_page.caption.text() == drivers_page.CANCELLED_TEXT
        assert dock._drivers_page.turn_on.isHidden()
        # opening the tab again offers to work them out
        assert dock._drivers_stale
    finally:
        dock.deleteLater()


def test_a_background_run_carries_its_attribution(qapp):
    win = _window(qapp)
    try:
        state = _finish_background(win, qapp, runs=2)
        attribution = state.get("attribution")
        assert attribution is not None and attribution["runs"] == 2
        # The dock read the finished pass: it did not replay the log.
        assert win._results.attribution()["runs"] == 2
        assert win._results._precomputed_attribution is None
    finally:
        win._dirty = False
        win.close()
        win.deleteLater()


def test_a_failed_background_pass_is_a_note_not_a_lost_batch(
        qapp, monkeypatch):
    import raptor.api.insight.attribution as attr
    win = _window(qapp)
    monkeypatch.setattr(
        attr, "attribute_batch",
        lambda *_a, **_k: (_ for _ in ()).throw(RuntimeError("boom")))
    try:
        state = _finish_background(win, qapp, runs=1)
        assert state.get("attribution") is None
        assert any("downtime attribution was not calculated" in note
                   for note in state["result"][4])
        assert win._results.has_results(), "the batch was discarded"
    finally:
        win._dirty = False
        win.close()
        win.deleteLater()


def test_a_stopped_background_pass_is_silent(qapp, monkeypatch):
    """Stop during the replay: no attribution, no note, results kept."""
    import raptor.api.insight.attribution as attr
    from raptor.api.progress import Cancelled
    win = _window(qapp)
    monkeypatch.setattr(
        attr, "attribute_batch",
        lambda *_a, **_k: (_ for _ in ()).throw(Cancelled()))
    try:
        state = _finish_background(win, qapp, runs=1)
        assert state.get("attribution") is None
        assert not any("attribution" in note for note in state["result"][4])
        assert win._results.has_results()
    finally:
        win._dirty = False
        win.close()
        win.deleteLater()
