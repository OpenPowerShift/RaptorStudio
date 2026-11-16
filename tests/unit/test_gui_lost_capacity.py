"""The GUI shows lost capacity after a capacity-analysis run (regression:
the results dock had no model and the capacity sink was not collected
unless the Capacity plot was ticked, so every lost-capacity view was
empty) and exports the year-by-year tables."""
from __future__ import annotations

import os

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

REPO = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
WATER2 = os.path.join(REPO, "new_docs", "models", "Water2.rbds")


@pytest.fixture()
def window(tmp_path):
    from PySide6.QtCore import QSettings
    from PySide6.QtWidgets import QApplication
    QSettings.setPath(QSettings.Format.IniFormat, QSettings.Scope.UserScope, str(tmp_path))
    QSettings.setDefaultFormat(QSettings.Format.IniFormat)
    app = QApplication.instance() or QApplication([])
    from raptor.gui.ui.main_window import MainWindow
    w = MainWindow()
    yield w, app
    w._close_report_streams()
    w._dirty = False
    w.close()


def test_a_capacity_run_shows_lost_capacity_and_yearly_capacity(window):
    from raptor.api.tables import result_tables
    from raptor.core.fileio.rbds import load_rbds
    w, app = window
    w._load_into_app(load_rbds(WATER2), WATER2)
    w._set_exp_field("trials", 4)
    w._set_exp_field("term_condition", 17520.0)        # two years of hours
    w._preflight_issues = lambda *_a, **_k: []
    w._dirty = False
    w._run_simulation()
    for _ in range(20):
        app.processEvents()
    results = w._results
    results._refresh_drivers()
    attribution = results._drivers_page.attribution()
    capacity = attribution["capacity"]
    assert capacity["generated"] == pytest.approx(180.0)
    assert capacity["lost_energy"]["mean"] > 0
    assert capacity["elements"], "the lost flow is split over the elements"
    periods = results._period_summary
    assert periods["capacity"]["pooled"]["n"] == 8       # 4 runs x 2 years
    keys = {t.key for t in result_tables(results.results_bundle())}
    assert {"results.lost_capacity", "results.period_availability",
            "results.period_capacity", "results.period_runs"} <= keys
    results.clear()
    assert results._period_summary is None
