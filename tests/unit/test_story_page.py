"""The Results dock's Story tab."""
from __future__ import annotations

import pytest
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


def test_empty_state_shows_nothing_extra(qapp):
    page = StoryPage()
    try:
        assert page.story() is None
        assert page.headline.isHidden() and page.dots.isHidden()
    finally:
        page.deleteLater()


def test_story_shows_verdict_dots_drivers_and_findings(qapp):
    page = StoryPage()
    try:
        page.resize(760, 400)
        page.show()
        page.set_story(_story())
        qapp.processEvents()
        assert "Contract" in page.headline.text()
        assert "missed" in page.target.text().lower()
        assert page.dots.isVisible()
        assert "Pump" in page.drivers_caption.text()
        assert "Open Drivers" in page.drivers_caption.text()
    finally:
        page.deleteLater()


def test_without_a_target_there_is_no_verdict_and_no_dots(qapp):
    from raptor.api.insight.story import build as build_story
    page = StoryPage()
    try:
        page.show()
        page.set_story(build_story([{"availability": 0.99}] * 5))
        assert page.headline.isHidden() and page.dots.isHidden()
    finally:
        page.deleteLater()


def test_dots_sit_at_their_values_and_stack(qapp):
    from raptor.api.insight.story import build as build_story
    rows = ([{"availability": 1.0}] * 30 + [{"availability": 0.98}] * 10)
    story = build_story(rows, requirements=[
        {"metric": "system.ao", "op": ">=", "value": 0.99}])
    page = StoryPage()
    try:
        page.resize(600, 300)
        page.set_story(story)
        dots, diameter, _range = page.dots.layout_dots(600)
        assert len(dots) == 40
        xs_met = {round(x) for x, _y, met in dots if met}
        xs_missed = {round(x) for x, _y, met in dots if not met}
        assert len(xs_met) == 1 and len(xs_missed) == 1
        assert min(xs_met) > max(xs_missed)          # 1.0 is right of 0.98
        assert min(y for _x, y, _m in dots) >= 0      # the stack fits
        assert diameter >= 1.5
    finally:
        page.deleteLater()


def test_without_attribution_says_how_to_get_it(qapp):
    page = StoryPage()
    try:
        page.set_story(_story(attribution=False))
        assert "Event Log" in page.drivers_caption.text()
    finally:
        page.deleteLater()


def test_a_driver_link_names_the_element_and_the_drivers_link_asks(qapp):
    page = StoryPage()
    try:
        page.set_story(_story())
        got, asked = [], []
        page.element_clicked.connect(lambda kind, oid, name: got.append((kind, oid, name)))
        page.drivers_requested.connect(lambda: asked.append(True))
        page._on_link("el:block:1:Pump")
        page._on_link("drivers")
        assert got == [("block", 1, "Pump")] and asked == [True]
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
