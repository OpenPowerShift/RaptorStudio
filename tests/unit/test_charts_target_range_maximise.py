"""The target on every availability chart, an axis that fits runs close
to 100 %, and a Results panel that can be seen whole."""
from __future__ import annotations

import os

import pytest

from raptor.api.insight import charts
from raptor.api.insight.axes import nice_range
from raptor.api.insight.periods import summarise

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")


@pytest.mark.parametrize("low, high, floor", [
    (0.9985, 0.99995, 0.998),        # DC2-like runs: was 99 % to 100 %
    (0.998, 0.99995, 0.9975),        # a 99.8 % target in the range
    (0.9999, 0.99999, 0.9998),
])
def test_runs_close_to_100_are_drawn_to_fit(low, high, floor):
    lo, hi = nice_range(low, high, 5, share=True)
    assert floor <= lo <= low and high <= hi <= 1.0


def _target(spec):
    return [m for m in spec.marks if m.get("kind") == "target"]


def test_the_year_charts_use_the_pages_target():
    years = [[0.997, 0.999, 1.0], [0.9985, 0.9995, 0.999]]
    periods = summarise(years)                       # no threshold of its own
    dots = charts.annual_dots(periods, 0.998)
    assert _target(dots) == [{"kind": "target", "value": 0.998, "label": "target"}]
    assert dots.caption.startswith("16.7 % of run-years fall below the target")
    assert "no target was set" in charts.annual_dots(periods).caption
    fan = charts.annual_fan(periods, 0.998)
    assert _target(fan)[0]["value"] == 0.998


def test_the_ladder_and_waterfall_carry_the_target():
    ladder = charts.availability_ladder({"inherent": {"value": 0.9995}}, 0.998)
    assert _target(ladder)[0]["value"] == 0.998
    att = {"runs": 2, "causes": {"repair": 1.0}, "elements": []}
    assert _target(charts.downtime_waterfall(att, 0.999, 0.998))[0]["value"] == 0.998
    # as shares of the loss there is no availability scale for a target
    assert _target(charts.downtime_waterfall(att, None, 0.998)) == []


@pytest.fixture
def window():
    from PySide6.QtTest import QTest
    from PySide6.QtWidgets import QApplication

    from raptor.gui.ui.main_window import MainWindow
    app = QApplication.instance() or QApplication([])
    w = MainWindow()
    w.resize(1400, 800)
    w.show()
    QTest.qWait(200)
    w._results_dock.raise_()
    QTest.qWait(200)
    yield w
    w._persist_layout = False
    w.close()
    w.deleteLater()
    app.processEvents()


def test_the_run_tab_behind_results_does_not_cap_the_strip(window):
    from PySide6.QtCore import Qt
    from PySide6.QtTest import QTest
    window._run_dock.fit_height()
    assert window._run_dock.maximumHeight() == 16777215
    before = window._results_dock.height()
    window.resizeDocks([window._results_dock], [before + 60], Qt.Orientation.Vertical)
    QTest.qWait(200)
    assert window._results_dock.height() > before


def test_maximise_and_restore_the_results(window):
    from PySide6.QtTest import QTest
    dock = window._results_dock
    button = window._dock_chrome_entries[dock]["max_btn"]
    assert button.text() == "Maximise"
    button.click()
    QTest.qWait(200)
    assert dock.isFloating() and dock.isMaximized() and button.text() == "Restore"
    button.click()
    QTest.qWait(200)
    assert not dock.isFloating() and button.text() == "Maximise"


@pytest.mark.parametrize("way", ["restore", "redock"])
def test_back_from_maximised_fits_the_window(window, way):
    """Docked while still maximised, the Results kept their full-screen
    size: the window, its status bar and the chart's foot went off screen,
    and the next Float reopened full screen."""
    from PySide6.QtTest import QTest
    dock = window._results_dock
    window._maximise_results()
    QTest.qWait(200)
    if way == "redock":
        window._redock(dock)
    else:
        window._dock_chrome_entries[dock]["max_btn"].click()
    QTest.qWait(400)
    assert not dock.isFloating() and not dock.isMaximized()
    assert dock.height() <= window._max_bottom_dock_height()
    assert window.statusBar().geometry().bottom() <= window.height()
    remembered = window._results_float_geom
    screen = window.screen().availableGeometry()
    assert remembered is not None and remembered.size() != screen.size()
