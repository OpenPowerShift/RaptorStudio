"""Run-by-run hierarchy plot (HOPs): data, SVG, and the Charts page animation."""
from __future__ import annotations

import os

import pytest

from raptor.api.insight import charts
from raptor.render.charts_svg import render

pytest.importorskip("PySide6")
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication  # noqa: E402

from raptor.gui.ui import settings  # noqa: E402
from raptor.gui.ui.charts_page import ChartsPage  # noqa: E402

A = [0.5, 0.2, 0.6, 0.1]
B = [0.3, 0.4, 0.2, 0.3]


def _attribution():
    def row(hid, name, shares):
        return {"kind": "hier", "id": hid, "name": name, "path": [name],
                "depth": 1, "parent": 0, "members": [], "member_count": 1,
                "starter_count": 1, "elements": 1,
                "dtci": {"mean": sum(shares) / len(shares), "sem": 0.01},
                "red_time": {"mean": 1.0}, "causes": {"repair": 1.0},
                "main_cause": "repair",
                "trials": [{"red_time": 1.0, "dtci": v} for v in shares]}
    return {"runs": 4, "units": "hours", "causes": {"repair": 1.0},
            "elements": [], "sequences": [], "outages": {"runs": []},
            "hierarchies": [row(10, "Intake", A), row(20, "Lift", B)]}


@pytest.fixture(scope="module")
def qapp():
    return QApplication.instance() or QApplication([])


def test_static_spec_counts_how_often_the_leader_is_first():
    spec = charts.chart_named("hierarchy-hops", attribution=_attribution())
    assert spec.kind == "hops"
    assert [r["name"] for r in spec.series] == ["Intake", "Lift"]  # mean 0.35 vs 0.3
    # Intake leads in runs 1 and 3 only
    assert spec.series[0]["first"] == 2
    assert spec.caption == "Intake causes the most downtime in 2 of 4 runs."
    assert spec.provenance["frame"] is None
    assert spec.to_dict()["scope"]["path"] == ["Intake", "Lift"]


def test_frame_keeps_axes_fixed_and_names_the_run():
    spec = charts.chart_named("hierarchy-hops", attribution=_attribution())
    frame = charts.hops_frame(spec, 1)
    assert frame.provenance["frame"] == 1
    assert frame.provenance["x_range"] == spec.provenance["x_range"]
    assert frame.caption.startswith("Run 2 of 4: Lift 40 %")
    assert frame.caption.endswith(spec.caption)
    assert charts.hops_frame(frame, None).provenance["frame"] is None
    assert spec.provenance["frame"] is None            # the original is untouched


def test_svg_static_and_frame_render():
    spec = charts.chart_named("hierarchy-hops", attribution=_attribution())
    static = render(spec)
    framed = render(charts.hops_frame(spec, 0))
    assert static.startswith("<svg") and "<circle" in static
    assert "<circle" not in framed and "#1" in framed
    header, rows = charts.csv_table(spec)
    assert header[:3] == ["run", "Intake", "Lift"] and len(rows) == 4


def test_hops_takes_several_hierarchies_and_refuses_one_scope():
    attribution = _attribution()
    charts.chart_named("hierarchy-hops", attribution=attribution,
                       hierarchies=["Intake", "Lift"])
    with pytest.raises(ValueError):
        charts.chart_named("hierarchy-hops", attribution=attribution,
                           hierarchy="Intake")


def _page(qapp):
    page = ChartsPage()
    page.set_inputs(attribution=_attribution(), per_run=[{"availability": 0.9}] * 4)
    page.select("hierarchy-hops") or page.select("Which hierarchy is worse, run by run?")
    return page


def test_animation_is_opt_in_and_steps_deterministically(qapp):
    page = _page(qapp)
    assert page.selected_key() == "hierarchy-hops"
    assert not page._hops_timer.isActive()          # never autoplays
    assert page._hops_frame is None
    assert page.chart.spec().provenance["frame"] is None
    assert page.hops_label.text() == "All 4 runs"
    page._hops_tick()
    assert page.hops_label.text() == "Run 1 of 4"
    assert page.chart.spec().provenance["frame"] == 0
    for _ in range(4):
        page._hops_tick()
    assert page._hops_frame == 0                    # wrapped after 4 runs
    page.step_hops()
    assert page.hops_label.text() == "Run 2 of 4"
    page.stop_hops(reset=True)
    assert page.chart.spec().provenance["frame"] is None


def test_play_starts_and_stops_the_timer_with_no_leak(qapp):
    page = _page(qapp)
    page.toggle_hops_play()
    assert page._hops_timer.isActive() and page.hops_play.text() == "Pause"
    page.toggle_hops_play()
    assert not page._hops_timer.isActive()
    page.toggle_hops_play()
    # another chart, new results and hiding each stop it
    page.select("hierarchy-pareto") or page.select("Hierarchy Pareto")
    assert not page._hops_timer.isActive() and page._hops_frame is None
    page.select("hierarchy-hops")
    page.toggle_hops_play()
    page.set_inputs(attribution=_attribution(), per_run=[{"availability": 0.9}] * 4)
    assert not page._hops_timer.isActive() and page._hops_frame is None
    page.select("hierarchy-hops")
    page.toggle_hops_play()
    page.show()
    page.hide()
    assert not page._hops_timer.isActive()
    page.close()


def test_controls_only_show_for_this_chart(qapp):
    page = _page(qapp)
    page.show()
    assert page.hops_play.isVisible() and page.hops_step.isVisible()
    page.select("hierarchy-pareto") or page.select("Hierarchy Pareto")
    assert not page.hops_play.isVisible()
    page.close()


def test_reduced_motion_disables_play(qapp):
    old = settings.get_bool("appearance/reduce_motion", False)
    settings.set_value("appearance/reduce_motion", True)
    try:
        page = _page(qapp)
        assert not page.hops_play.isEnabled()
        assert "Reduce motion" in page.hops_play.toolTip()
        page.toggle_hops_play()
        assert not page._hops_timer.isActive()
        assert page.hops_step.isEnabled()           # manual stepping still works
        settings.set_value("appearance/reduce_motion", False)
        page.sync_motion_setting()
        assert page.hops_play.isEnabled()
    finally:
        settings.set_value("appearance/reduce_motion", old)
