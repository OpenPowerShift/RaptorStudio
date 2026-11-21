"""The end-of-run attribution pass and its use by the Results dock."""
from __future__ import annotations

import os

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


def test_the_foreground_run_path_computes_drivers_with_progress(batch):
    from raptor.gui.ui import settings
    from raptor.gui.ui.main_window import MainWindow
    QApplication.instance() or QApplication([])
    settings.set_value("startup/show_start_page", False)
    win = MainWindow()
    win.show()
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
        assert any("downtime" in text.lower() for text in seen)
    finally:
        win._dirty = False
        win.close()


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
