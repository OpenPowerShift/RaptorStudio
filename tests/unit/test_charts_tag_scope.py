"""Tags in the chart Scope and Compare pickers (fixed at run time)."""
from __future__ import annotations

import os

import pytest

pytest.importorskip("PySide6")
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication

from raptor.api.insight import hierarchy as hier
from raptor.api.insight.attribution import attribute_logs
from raptor.gui.ui.charts_page import ChartsPage
from tests.unit.insight.test_attribution import _two_block_log


@pytest.fixture(scope="module")
def qapp():
    return QApplication.instance() or QApplication([])


def _attribution():
    hierarchies = {"names": {10: "Outer", 11: "Inner"}, "parent": {10: 0, 11: 10},
                   "of": {("block", 1): 10, ("block", 2): 11}}
    tags = {"members": {"wet": {("block", 1), ("block", 2)},
                        "valves": {("block", 2)}},
            "colours": {"wet": "#2F80ED", "valves": "#EB5757"},
            "descriptions": {}, "signature": "x"}
    result = attribute_logs([_two_block_log() for _ in range(4)],
                            hierarchies=hierarchies, tags=tags)
    result["units"] = "hours"
    return result


def _per_run():
    return [{"availability": 0.6, "total_time": 30.0} for _ in range(4)]


def test_tag_ids_are_stable_and_resolve_by_name():
    attribution = _attribution()
    wet = hier.resolve(attribution, "#wet")
    assert wet["id"] < 0 and hier.resolve(attribution, wet["id"]) is wet
    assert hier.resolve(attribution, "Tag: wet") is wet
    # another tag added does not move this one's id
    hierarchies = {"names": {10: "Outer"}, "parent": {10: 0},
                   "of": {("block", 1): 10, ("block", 2): 10}}
    tags = {"members": {"wet": {("block", 1)}, "aaa": {("block", 2)}},
            "colours": {}, "descriptions": {}, "signature": "y"}
    again = attribute_logs([_two_block_log()], hierarchies=hierarchies, tags=tags)
    assert hier.resolve(again, "#wet")["id"] == wet["id"]
    with pytest.raises(ValueError, match="tags in this run"):
        hier.resolve(attribution, "#dry")


def test_a_tag_scope_offers_only_contribution_charts_and_they_draw(qapp):
    page = ChartsPage()
    page.resize(900, 500)
    attribution = _attribution()
    page.set_inputs(per_run=_per_run(), attribution=attribution, units="hours")
    wet = hier.resolve(attribution, "#wet")
    page.set_scope(wet["id"], follow=False)
    assert page.scope_button.text() == "Scope: #wet"
    drawable = page.titles()
    keys = {e.key for e in page._entries.values() if not e.missing}
    assert keys <= ChartsPage.TAG_SCOPE_CHARTS, keys
    assert "hierarchy-map" not in keys and "hierarchy-pareto" in keys
    for title in drawable:
        assert page.select(title)
        assert page.selected_spec() is not None, title
        assert not page.chart.grab().toImage().isNull()
    assert page.selected_spec().scope["kind"] == "tag"


def test_a_tag_within_a_hierarchy_has_no_run_by_run_charts(qapp):
    page = ChartsPage()
    attribution = _attribution()
    page.set_inputs(per_run=_per_run(), attribution=attribution, units="hours")
    inner = next(r for r in hier.tag_within_rows(attribution)
                 if r["tag"] == "wet" and r["within"] == 11)
    page.set_scope(inner["id"], follow=False)
    assert page.scope_button.text() == "Scope: #wet in Outer > Inner"
    entry = page._entries["trial-outcome"]
    assert entry.missing.startswith("Run-by-run shares are kept")
    assert not page._entries["hierarchy-pareto"].missing


def test_tags_compare_in_small_multiples_and_not_in_the_overlay(qapp):
    page = ChartsPage()
    attribution = _attribution()
    page.set_inputs(per_run=_per_run(), attribution=attribution, units="hours")
    wet, valves = hier.resolve(attribution, "#wet"), hier.resolve(attribution, "#valves")
    page.set_compare([wet["id"], valves["id"]])
    assert page.selected_hierarchies() == [wet["id"], valves["id"]]
    assert not page._entries["hierarchy-small-multiples"].missing
    assert page._entries["hierarchy-overlay"].missing
    page.select("Is the hierarchy ranking stable?")
    assert [f.title for f in page.selected_spec().facets] == ["wet", "valves"]


def test_the_picker_lists_tags_under_their_own_heading(qapp):
    page = ChartsPage()
    attribution = _attribution()
    page.set_inputs(per_run=_per_run(), attribution=attribution, units="hours")
    scope = page.picker("scope")
    assert scope.apply_filter("wet") >= 1
    names = scope.visible_names()
    assert "wet" in names and "in Outer > Inner" in names
    compare = page.picker("compare")
    compare.apply_filter("")
    assert "in Outer > Inner" not in compare.visible_names()   # no per-run list


def test_charts_say_when_the_tags_changed_after_the_run(qapp):
    page = ChartsPage()
    attribution = _attribution()
    page.set_inputs(per_run=_per_run(), attribution=attribution, units="hours",
                    tags_changed=True)
    page.set_scope(hier.resolve(attribution, "#wet")["id"], follow=False)
    page.select("Hierarchy Pareto")
    assert "run again to update" in page.selected_spec().caption
