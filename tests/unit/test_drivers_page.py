"""The Results dock's Drivers tab."""
from __future__ import annotations

import pytest
from PySide6.QtCore import QPointF, Qt
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication

from raptor.api import build
from raptor.api.insight.attribution import attribute_logs
from raptor.core.engine.runner import run_simulation
from raptor.gui.ui import drivers_page
from raptor.gui.ui.drivers_page import DriversPage

SPEC = {
    "blocks": [
        {"name": "Pump", "fail_dist": "Exponential", "fail_params": [300, 0, 0],
         "repair_dist": "Lognormal", "repair_params": [10, 3, 0],
         "sparing_type": "Custom", "stock": 0, "rso": 1, "new_spares": 1,
         "arrive_every": 600, "eso": 0},
        {"name": "Valve", "fail_dist": "Exponential", "fail_params": [2000, 0, 0],
         "repair_dist": "Lognormal", "repair_params": [5, 1, 0]}],
    "nodes": [{"name": "Start", "type": 1}, {"name": "End", "type": 3}],
    "links": [{"from": "Start", "to": "Pump"}, {"from": "Pump", "to": "Valve"},
              {"from": "Valve", "to": "End"}],
    "experiment": {"trials": 20, "termination_type": "TimeSim", "term_condition": 3000},
}


@pytest.fixture
def qapp():
    return QApplication.instance() or QApplication([])


@pytest.fixture(scope="module")
def logs():
    return run_simulation(build.build_from_spec(SPEC), 20)


@pytest.fixture
def page(qapp):
    p = DriversPage()
    p.resize(800, 600)
    p.show()
    yield p
    p.close()
    p.deleteLater()


def test_empty_state_says_how_to_get_the_view(page):
    assert page.caption.text() == drivers_page.EMPTY_TEXT
    assert not page.chart.isVisible() and page.footer.text() == ""


def test_shows_the_drivers_and_the_anatomy(page, logs, qapp):
    attribution = attribute_logs(logs)
    attribution["units"] = "hours"
    page.set_attribution(attribution)
    qapp.processEvents()
    assert page.chart.isVisible()
    assert page.caption.text().startswith("2 elements cause 100 % of system downtime: Pump")
    assert [r["name"] for r in page.chart.rows()] == ["Pump", "Valve"]
    assert page.chart.cells() and page.chart.cells()[0].element == "Pump"
    assert "20 runs" in page.footer.text() and "hours" in page.footer.text()
    image = page.chart.grab().toImage()
    assert len({image.pixel(x, 40) for x in range(10, image.width() - 10, 7)}) > 1


def test_clicking_a_row_names_the_element(page, logs, qapp):
    page.set_attribution(attribute_logs(logs))
    qapp.processEvents()
    page.chart.grab()                                   # paint, so the rows have places
    got = []
    page.element_clicked.connect(lambda kind, oid, name: got.append((kind, oid, name)))
    rect, row = page.chart._hit[0]
    QTest.mouseClick(page.chart, Qt.MouseButton.LeftButton,
                     pos=QPointF(rect.x() + 20, rect.center().y()).toPoint())
    assert got == [("block", row["id"], "Pump")]


def test_no_outage_is_said_plainly(page, qapp):
    page.set_attribution({"runs": 50, "elements": [], "causes": {}, "sequences": [],
                          "system": {"red_time": {"mean": 0.0}, "outages": {"mean": 0.0}}})
    assert "No system outage occurred in 50 runs" in page.caption.text()
    assert not page.chart.isVisible()


def test_results_dock_fills_the_tab_from_its_event_logs(logs, qapp):
    from raptor.gui.ui.results import ResultsDock
    dock = ResultsDock()
    try:
        titles = [dock._tabs.tabText(i) for i in range(dock._tabs.count())]
        assert titles[:2] == ["Overview", "Drivers"]
        dock._event_logs, dock._units, dock._manifest = logs, "hours", None
        dock._schedule_drivers()
        assert dock._drivers_page.attribution()["runs"] == 20
        dock._event_logs = [["plain text line"]]
        dock._refresh_drivers()
        assert dock._drivers_page.attribution() is None
        dock.clear()
        assert dock._drivers_page.caption.text() == drivers_page.EMPTY_TEXT
    finally:
        dock.deleteLater()


def test_year_by_year_chart_needs_hours_and_more_than_one_year(qapp):
    from raptor.gui.ui.results import ResultsDock
    spec = dict(SPEC, experiment={"trials": 10, "termination_type": "TimeSim",
                                  "term_condition": 8760 * 3})
    long_logs = run_simulation(build.build_from_spec(spec), 10)
    dock = ResultsDock()
    try:
        dock._event_logs, dock._units, dock._manifest = long_logs, "hours", None
        dock._schedule_drivers()
        page = dock._drivers_page
        assert page.chart.periods()["pooled"]["n"] == 30
        assert page.years.text().startswith("A typical year has availability")
        dock._units = "miles"
        dock._refresh_drivers()
        assert page.chart.periods() is None and page.years.text() == ""
    finally:
        dock.deleteLater()


def test_short_runs_show_no_year_chart(page, logs, qapp):
    page.set_attribution(attribute_logs(logs))
    page.set_periods({"periods_per_run": 1, "by_index": [], "pooled": {"n": 20}, "sentence": "x"})
    assert page.chart.periods() is None and not page.years.isVisible()


def test_percent_text_never_uses_an_exponent():
    assert drivers_page._pct(1.0, 2) == "100 %" and drivers_page._pct(0.4712, 2) == "47 %"


def test_a_capacity_only_run_still_shows_the_lost_capacity(page, qapp):
    attribution = {
        "runs": 10, "elements": [], "causes": {}, "sequences": [],
        "system": {"red_time": {"mean": 0.0}, "outages": {"mean": 0.0}},
        "capacity": {
            "rule": "proportional",
            "elements": [
                {"kind": "block", "id": 1, "name": "A",
                 "share": {"mean": 0.6}, "lost_energy": {"mean": 300.0, "sem": 1.0},
                 "through_dependents": {"mean": 0.0}, "standalone": 50.0},
                {"kind": "block", "id": 2, "name": "B",
                 "share": {"mean": 0.4}, "lost_energy": {"mean": 200.0, "sem": 1.0},
                 "through_dependents": {"mean": 100.0}, "standalone": 30.0},
            ],
            "notes": [],
        },
    }
    page.set_attribution(attribution)
    qapp.processEvents()
    assert page.chart.isVisible() and page.chart._capacity is not None
    assert page.caption.text().startswith("The system never went Red")
    image = page.chart.grab().toImage()
    assert image.width() > 0 and page.chart._capacity_hit
    assert [r["name"] for r in page.chart._capacity["elements"]] == ["A", "B"]


def test_small_cause_segments_explain_their_name_on_hover(page, logs, qapp):
    attribution = attribute_logs(logs)
    attribution["units"] = "hours"
    page.set_attribution(attribution)
    qapp.processEvents()
    chart = page.chart
    chart.grab()                    # a paint pass fills the hit boxes
    assert chart._cause_hit
    rect, cause = chart._cause_hit[0]
    QTest.mouseMove(chart, rect.center().toPoint())
    assert drivers_page.CAUSE_LABELS[cause] in chart.toolTip()


def test_a_canvas_selection_marks_the_matching_driver_row(page, logs, qapp):
    attribution = attribute_logs(logs)
    page.set_attribution(attribution)
    qapp.processEvents()
    row = page.chart.rows()[0]
    page.highlight(row["kind"], int(row["id"]))
    assert page.chart._highlight == (row["kind"], int(row["id"]))
    page.highlight("", -1)
    assert page.chart._highlight == ("", -1)
