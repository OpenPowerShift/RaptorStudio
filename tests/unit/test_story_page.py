"""The Results dock's Story tab."""
from __future__ import annotations

import pytest
from PySide6.QtCore import QPointF, Qt
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication

from raptor.api.insight.story import build
from raptor.gui.ui.story_page import StoryPage


@pytest.fixture
def qapp():
    return QApplication.instance() or QApplication([])


def _story(attribution=True):
    per_run = [{"availability": 0.99, "reliability": 0.8, "mtbde": 100.0,
                "mdt": 5.0, "ending_sim_time": 8760.0} for _ in range(10)]
    attr = None
    if attribution:
        attr = {"runs": 10, "units": "hours", "causes": {"repair": 1.0},
                "system": {"red_time": {"mean": 10.0}, "outages": {"mean": 4.0}},
                "elements": [{"kind": "block", "id": 1, "name": "Pump",
                              "dtci": {"mean": 1.0},
                              "red_time": {"mean": 10.0},
                              "causes": {"repair": 1.0},
                              "main_cause": "repair"}]}
    return build(per_run, units="hours", attribution=attr,
                 requirements=[{"metric": "system.ao", "op": ">=",
                                "value": 0.995, "label": "Contract"}])


def test_empty_state_says_what_to_do(qapp):
    page = StoryPage()
    try:
        assert page.headline.text().startswith("Run the model")
        assert page.story() is None
    finally:
        page.deleteLater()


def test_story_shows_headline_dots_drivers_and_findings(qapp):
    page = StoryPage()
    try:
        page.resize(760, 700)
        page.show()
        page.set_story(_story())
        qapp.processEvents()
        assert "Contract" in page.headline.text()
        assert "missed" in page.target.text().lower()
        assert page.dots._story is not None
        assert page.drivers._story is not None
        assert "Pump" in page.provenance.text() or "10 runs" in page.provenance.text()
        image = page.grab().toImage()
        assert image.width() > 100 and image.height() > 100
        assert len({image.pixel(x, y) for x in range(5, image.width() - 5, 23)
                    for y in range(5, image.height() - 5, 29)}) > 1
    finally:
        page.deleteLater()


def test_story_without_attribution_offers_the_one_action(qapp):
    page = StoryPage()
    try:
        page.set_story(_story(attribution=False))
        assert "Turn on downtime attribution" in page.drivers_caption.text()
    finally:
        page.deleteLater()


def test_clicking_a_driver_names_the_element(qapp):
    page = StoryPage()
    try:
        page.resize(760, 700)
        page.show()
        page.set_story(_story())
        qapp.processEvents()
        page.drivers.grab()
        got = []
        page.element_clicked.connect(lambda kind, oid, name: got.append((kind, oid, name)))
        rect, _driver = page.drivers._hits[0]
        QTest.mouseClick(page.drivers, Qt.MouseButton.LeftButton,
                         pos=QPointF(rect.x() + 20, rect.center().y()).toPoint())
        assert got == [("block", 1, "Pump")]
    finally:
        page.deleteLater()


def test_results_dock_builds_the_story_from_its_own_result_set(qapp):
    from raptor.api import build
    from raptor.core.engine.runner import run_simulation_with_reports
    from raptor.gui.ui.results import ResultsDock

    spec = {"blocks": [{"name": "Pump", "fail_dist": "Fixed",
                        "fail_params": [20, 0, 0], "repair_dist": "Fixed",
                        "repair_params": [2, 0, 0]}],
            "nodes": [{"name": "Start", "type": 1}, {"name": "End", "type": 3}],
            "links": [{"from": "Start", "to": "Pump"},
                      {"from": "Pump", "to": "End"}],
            "experiment": {"trials": 3, "termination_type": "TimeSim",
                           "term_condition": 100.0}}
    rbd = build.build_from_spec(spec)
    data = []
    logs, eor, final = run_simulation_with_reports(
        rbd, num_runs=3, report_sink=data)
    dock = ResultsDock()
    try:
        dock.set_results(logs, eor, final, report_data=data, units="hours")
        qapp.processEvents()
        story = dock._story_page.story()
        assert story is not None
        assert story.headline is not None
        assert story.provenance["runs"] == 3
        assert story.provenance["units"] == "hours"
    finally:
        dock.deleteLater()
