"""The Charts tab and its QPainter widget (WP-2.5)."""
from __future__ import annotations

import os

import pytest

pytest.importorskip("PySide6")
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QPoint, Qt
from PySide6.QtTest import QSignalSpy, QTest
from PySide6.QtWidgets import QApplication

from raptor.gui.ui.chart_widget import ChartWidget
from raptor.gui.ui.charts_page import ChartsPage


@pytest.fixture(scope="module")
def qapp():
    return QApplication.instance() or QApplication([])


def _inputs():
    per_run = [{"availability": 0.90, "reliability": 0.8,
                "inherent_availability": 0.95, "achieved_availability": 0.93,
                "operational_availability": 0.91, "total_time": 100.0,
                "downtime": 9.0, "red_corrective_time": 5.0,
                "red_pm_time": 2.0, "red_logistics_time": 2.0,
                "red_done_time": 0.0, "red_other_time": 0.0},
               {"availability": 0.95, "reliability": 0.9,
                "inherent_availability": 0.97, "achieved_availability": 0.95,
                "operational_availability": 0.94, "total_time": 100.0,
                "downtime": 5.0, "red_corrective_time": 3.0,
                "red_pm_time": 1.0, "red_logistics_time": 1.0,
                "red_done_time": 0.0, "red_other_time": 0.0}]
    attribution = {
        "runs": 2, "causes": {"repair": 0.7, "wait_spare": 0.3},
        "elements": [
            {"name": "Pump", "dtci": {"mean": 0.4, "sem": 0.02},
             "deci": {"mean": 0.5}, "red_time": {"mean": 5.0},
             "causes": {"repair": 1.0}},
            {"name": "Valve", "dtci": {"mean": 0.2, "sem": 0.01},
             "deci": {"mean": 0.2}, "red_time": {"mean": 2.0},
             "causes": {"wait_spare": 1.0}},
        ],
        "outage_durations": {"count": 4, "p50": 2.0, "p90": 8.0,
                             "hist": [{"lo": 1.0, "hi": 2.0, "count": 2.0},
                                      {"lo": 2.0, "hi": 4.0, "count": 2.0}]},
        "sequences": [{"steps": [{"element": "Pump", "cause": "repair"}],
                       "share": 1.0, "outages": 2}],
        "outages": {"runs": [{"start": 0, "end": 10,
                              "red": [2, 4]}]},
    }
    importance = {"metrics": {"Availability": {"rows": [
        {"block": "Pump", "raw": 3.0, "rrw": 2.0}]}}}
    return per_run, attribution, importance


def test_the_picker_offers_only_charts_the_data_supports(qapp):
    page = ChartsPage()
    per_run, attribution, importance = _inputs()
    page.set_inputs(per_run=per_run, attribution=attribution,
                    importance=importance, units="hours", run_id="r_1")
    names = [page.selector.itemText(i) for i in range(page.selector.count())]
    assert "Run-to-run outcome" in names
    assert "Who causes the downtime" in names
    assert "Importance quadrant" in names
    assert "Where the money goes" not in names


def test_every_offered_chart_paints_and_exports(qapp, tmp_path):
    page = ChartsPage()
    per_run, attribution, importance = _inputs()
    page.set_inputs(per_run=per_run, attribution=attribution,
                    importance=importance, units="hours", run_id="r_1")
    for index in range(page.selector.count()):
        page.selector.setCurrentIndex(index)
        image = page.chart.grab().toImage()
        assert not image.isNull()
        assert page.selected_spec() is not None
        svg = tmp_path / f"chart-{index}.svg"
        page.save_svg(str(svg))
        assert svg.read_text(encoding="utf-8").startswith("<svg")
        csv = tmp_path / f"chart-{index}.csv"
        page.save_csv(str(csv))
        assert csv.read_text(encoding="utf-8").startswith("series,")


def test_a_named_mark_locates_the_element(qapp):
    widget = ChartWidget()
    widget.resize(700, 380)
    page = ChartsPage()
    per_run, attribution, importance = _inputs()
    page.set_inputs(per_run=per_run, attribution=attribution,
                    importance=importance)
    page.selector.setCurrentText("Who causes the downtime")
    widget.set_spec(page.selected_spec())
    widget.grab()
    spy = QSignalSpy(widget.locate_requested)
    hit = next(rect for rect, kind, name in widget._hits
               if kind == "element" and name == "Pump")
    QTest.mouseClick(widget, Qt.MouseButton.LeftButton,
                     pos=QPoint(int(hit.center().x()), int(hit.center().y())))
    assert spy.count() == 1
    assert spy.at(0) == ["element", "Pump"]


def test_capacity_and_annual_charts_appear_when_their_data_does(qapp):
    page = ChartsPage()
    per_run, attribution, importance = _inputs()
    attribution = dict(attribution)
    attribution["capacity"] = {
        "rule": "proportional", "generated": 100.0,
        "elements": [{"name": "Pump", "share": {"mean": 0.6},
                      "lost_energy": {"mean": 12.0},
                      "through_dependents": {"mean": 0.0}}],
        "causes": {"repair": {"mean": 7.0}}}
    periods = {"runs": 2, "periods_per_run": 2, "threshold": 0.95,
               "pooled": {"n": 4, "p5": 0.9, "p50": 0.97, "p95": 1.0},
               "by_index": [
                   {"index": 1, "n": 2, "p5": 0.90, "p25": 0.94, "p50": 0.97,
                    "p75": 0.99, "p95": 1.0},
                   {"index": 2, "n": 2, "p5": 0.88, "p25": 0.92, "p50": 0.96,
                    "p75": 0.98, "p95": 1.0}]}
    page.set_inputs(per_run=per_run, attribution=attribution,
                    importance=importance, periods=periods)
    names = [page.selector.itemText(i) for i in range(page.selector.count())]
    assert "Who loses the capacity" in names
    assert "Downtime against lost capacity" in names
    assert "Availability year by year" in names
    assert "A bad year, as a frequency" in names
    for name in ("Who loses the capacity", "Downtime against lost capacity",
                 "Availability year by year", "A bad year, as a frequency"):
        page.selector.setCurrentText(name)
        assert not page.chart.grab().toImage().isNull()
