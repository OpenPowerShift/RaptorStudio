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


#: Two runs of each recorded plot, in the engine's sink layout.
_MARK = ["", "*" * 30 + "End_of_Run_{n}" + "*" * 30, ""]
AO = (["SimulationTime Ao Green Yellow Red Interval",
       "10 0.9 0.8 0.1 0.1 0.9", "20 0.85 0.7 0.15 0.15 0.8", "20 0.85 0.7 0.15 0.15"]
      + [m.format(n=1) for m in _MARK]
      + ["10 0.95 0.9 0.05 0.05 0.95", "20 0.9 0.85 0.05 0.1 0.85", "20 0.9 0.85 0.05 0.1"]
      + [m.format(n=2) for m in _MARK])
CAP = (["SimulationTime Flow Capacity", "0 100 100", "5 40 40", "8 100 100"]
       + [m.format(n=1) for m in _MARK]
       + ["0 100 100", "12 0 0", "15 100 100"] + [m.format(n=2) for m in _MARK])


def _series():
    from raptor.api.series import parse_ao_runs, parse_capacity_runs
    return parse_ao_runs("\n".join(AO)), parse_capacity_runs("\n".join(CAP))


def _all_titles(page):
    return page.titles(available=False)


def test_the_list_offers_every_chart_and_greys_what_the_run_lacks(qapp):
    page = ChartsPage()
    per_run, attribution, importance = _inputs()
    page.set_inputs(per_run=per_run, attribution=attribution,
                    importance=importance, units="hours", run_id="r_1")
    drawable = page.titles()
    assert "Run-to-run outcome" in drawable
    assert "Who causes the downtime" in drawable
    assert "Importance quadrant" in drawable
    # listed, but greyed with what the run needs
    assert "Where the money goes" in _all_titles(page)
    assert "Where the money goes" not in drawable
    # the control's own label and where it is
    assert "<b>Cost</b> under Run panel ▸ Results to calculate" in page.missing_reason("cost-pareto")
    assert "<b>Availability plot</b> under Run panel ▸ Recording" in page.missing_reason(
        "availability-band")
    page.select("cost-pareto")
    assert page.stack.currentIndex() == 1
    assert "Cost</b> under Run panel" in page.missing_text.text()


def test_the_capacity_axis_presets_frame_the_drawn_range(qapp):
    page = ChartsPage()
    page.set_inputs(per_run=_inputs()[0], units="hours")
    page.set_series(*_series())
    page.select("capacity-band")
    auto = list(page.selected_spec().provenance["y_range"])
    assert auto[0] == pytest.approx(0.0)
    page.set_capacity_axis("full")
    full = page.selected_spec().provenance["y_range"]
    assert full[0] == pytest.approx(0.0) and full[1] >= 100
    page.set_capacity_axis("goal", 95)
    goal = page.selected_spec().provenance["y_range"]
    assert goal[0] == pytest.approx(95.0) and goal[1] >= 100
    page.set_capacity_axis("probability", 90)
    band = page.selected_spec().provenance["y_range"]
    assert band[0] <= band[1]
    page.set_capacity_axis("numeric", low=40.0, high=110.0)
    assert page.selected_spec().provenance["y_range"] == pytest.approx([40.0, 110.0])
    page.set_capacity_axis("auto")
    assert page.selected_spec().provenance["y_range"] == pytest.approx(auto)
    page.select("availability-band")
    assert not page.capacity_axis_button.isVisibleTo(page)


def test_a_probability_band_between_stored_percentiles_is_interpolated():
    # p15/p85 are not stored; the band used to fall back to the whole range
    summary = {"min": 0.0, "max": 100.0, "p00": 0.0, "p10": 10.0, "p25": 40.0,
               "p75": 60.0, "p90": 90.0, "p100": 100.0}
    assert ChartsPage._capacity_at(summary, 0.15, -1.0) == pytest.approx(20.0)
    assert ChartsPage._capacity_at(summary, 0.85, -1.0) == pytest.approx(80.0)
    assert ChartsPage._capacity_at(summary, 0.25, -1.0) == pytest.approx(40.0)
    assert ChartsPage._capacity_at({}, 0.5, -1.0) == -1.0


def test_the_gui_closes_cost_runs_with_the_end_of_run_totals():
    from pathlib import Path
    from raptor.gui.ui.results_graphs_mixin import ResultsGraphsMixin
    legacy = Path(__file__).resolve().parents[2] / "tests" / "golden" / "ReleaseProbe27" / "legacy"
    cost = (legacy / "rpt_interim_cost_ReleaseProbe27.txt").read_text().splitlines()
    eor = (legacy / "rpt_end_of_run_ReleaseProbe27.txt").read_text().splitlines()
    runs = ResultsGraphsMixin._parse_cost(cost, eor)
    assert runs[0]["t"][-1] == pytest.approx(2400.499665)
    assert ResultsGraphsMixin._parse_cost(cost)[0]["t"][-1] == 2160.0


def test_cost_series_charts_are_listed_and_paint(qapp):
    from pathlib import Path
    from raptor.api.insight.cost_series import parse_cost_file
    root = Path(__file__).resolve().parents[2]
    cost = parse_cost_file(str(root / "tests" / "golden" / "ReleaseProbe27"
                                  / "legacy" / "rpt_interim_cost_ReleaseProbe27.txt"))
    page = ChartsPage()
    page.resize(900, 500)
    page.set_inputs(per_run=_inputs()[0], units="hours")
    page.set_cost_series(cost)
    titles = page.titles()
    for title in ("Cost by category", "Cumulative operating cost over time",
                  "Operating cost per year"):
        assert title in titles
        assert page.select(title)
        assert page.stack.currentIndex() == 0
        assert not page.chart.grab().toImage().isNull()


def test_cost_charts_are_built_in_the_models_currency(qapp):
    from pathlib import Path
    from raptor.api.insight.cost_series import parse_cost_file
    root = Path(__file__).resolve().parents[2]
    cost = parse_cost_file(str(root / "tests" / "golden" / "ReleaseProbe27"
                                  / "legacy" / "rpt_interim_cost_ReleaseProbe27.txt"))
    page = ChartsPage()
    page.set_inputs(per_run=_inputs()[0], units="hours", currency="€")
    page.set_cost_series(cost)
    assert page.select("Cumulative operating cost over time")
    spec = page.selected_spec()
    assert spec is not None and spec.provenance["currency"] == "€"
    assert "€" in spec.caption and "$" not in spec.caption
    assert not page.chart.grab().toImage().isNull()      # paints in it too


def test_the_row_note_only_applies_to_charts_over_time(qapp):
    page = ChartsPage()
    page.set_inputs(per_run=_inputs()[0], units="hours")
    page.set_series(*_series())
    page.set_note("all recorded rows", time_charts_only=True)
    page.select("ecdf")
    assert not page.note.isVisibleTo(page)
    page.select("capacity-band")
    assert page.note.isVisibleTo(page)
    assert "all recorded rows" in page.note.text()


def test_the_lost_capacity_sankey_can_drop_the_hierarchy_and_delivered(qapp):
    page = ChartsPage()
    per_run, attribution, importance = _hierarchy_inputs()
    attribution = dict(attribution)
    attribution["capacity"] = {
        "rule": "proportional", "generated": 100.0,
        "delivered_energy": {"mean": 80.0}, "lost_energy": {"mean": 20.0},
        "elements": [
            {"kind": "block", "id": 1, "name": "Pump",
             "lost_energy": {"mean": 12.0}, "share": {"mean": 0.6},
             "causes": {"repair": 1.0}, "main_cause": "repair"},
            {"kind": "block", "id": 2, "name": "Filter",
             "lost_energy": {"mean": 8.0}, "share": {"mean": 0.4},
             "causes": {"pm": 1.0}, "main_cause": "pm"}],
        "causes": {"repair": {"mean": 12.0}, "pm": {"mean": 8.0}}}
    page.set_inputs(per_run=per_run, attribution=attribution,
                    importance=importance, units="hours")
    assert page.select("Lost capacity by hierarchy and cause")
    assert page.sankey_box.isVisibleTo(page)
    assert page.sankey_delivered.isVisibleTo(page)
    nodes = page.selected_spec().provenance["nodes"]
    assert any(node["id"] == "delivered" for node in nodes)
    assert any(str(node["id"]).startswith("hier-") for node in nodes)
    page.sankey_delivered.setChecked(False)
    nodes = page.selected_spec().provenance["nodes"]
    assert not any(node["id"] == "delivered" for node in nodes)
    assert nodes[0]["id"] == "lost"
    page.sankey_box.setCurrentIndex(page.sankey_box.findData(False))
    spec = page.selected_spec()
    assert not any(str(node["id"]).startswith("hier-")
                   for node in spec.provenance["nodes"])
    assert any(str(node["id"]).startswith("cause-")
               for node in spec.provenance["nodes"])


def _sankey_page(lost: float, delivered: float):
    page = ChartsPage()
    per_run, attribution, importance = _hierarchy_inputs()
    attribution = dict(attribution)
    attribution["capacity"] = {
        "rule": "proportional", "generated": lost + delivered,
        "delivered_energy": {"mean": delivered}, "lost_energy": {"mean": lost},
        "elements": [
            {"kind": "block", "id": 1, "name": "Pump",
             "lost_energy": {"mean": lost * 0.6}, "share": {"mean": 0.6},
             "causes": {"repair": 1.0}, "main_cause": "repair"},
            {"kind": "block", "id": 2, "name": "Filter",
             "lost_energy": {"mean": lost * 0.4}, "share": {"mean": 0.4},
             "causes": {"pm": 1.0}, "main_cause": "pm"}],
        "causes": {"repair": {"mean": lost * 0.6}, "pm": {"mean": lost * 0.4}}}
    page.set_inputs(per_run=per_run, attribution=attribution,
                    importance=importance, units="hours")
    page.select("Lost capacity by hierarchy and cause")
    return page


def test_a_small_loss_starts_the_sankey_at_the_loss_and_says_so(qapp):
    page = _sankey_page(lost=1.0, delivered=999.0)       # 0.1 % lost
    spec = page.selected_spec()
    assert not page.sankey_delivered.isChecked()
    assert spec.provenance["nodes"][0]["id"] == "lost"
    assert "lost flow only" in spec.caption
    page.sankey_delivered.setChecked(True)               # the reader's choice sticks
    assert any(n["id"] == "delivered" for n in page.selected_spec().provenance["nodes"])
    assert "lost flow only" not in page.selected_spec().caption


def test_a_large_loss_keeps_the_delivered_branch(qapp):
    page = _sankey_page(lost=200.0, delivered=800.0)     # 20 % lost
    assert page.sankey_delivered.isChecked()
    assert "lost flow only" not in page.selected_spec().caption


def test_the_toolbar_buttons_are_framed_and_the_pickers_have_a_caret(qapp):
    page = ChartsPage()
    for button in (page.scope_button, page.compare_button, page.follow_button,
                   page.sankey_delivered, page.hops_play):
        assert "border: 1px solid" in button.styleSheet()
    assert "padding-right" in page.scope_button.styleSheet()
    assert "padding-right" in page.compare_button.styleSheet()


def test_enter_opens_the_scope_picker(qapp):
    from PySide6.QtCore import QEvent
    from PySide6.QtGui import QKeyEvent
    page = ChartsPage()
    per_run, attribution, importance = _hierarchy_inputs()
    page.set_inputs(per_run=per_run, attribution=attribution,
                    importance=importance, units="hours")
    event = QKeyEvent(QEvent.Type.KeyPress, Qt.Key.Key_Return,
                      Qt.KeyboardModifier.NoModifier)
    assert page.eventFilter(page.scope_button, event)
    assert page.picker("scope").isVisible()
    page.picker("scope").hide()


def test_a_narrow_page_swaps_the_chart_list_for_a_grouped_drop_down(qapp):
    page = ChartsPage()
    per_run, attribution, importance = _hierarchy_inputs()
    page.set_inputs(per_run=per_run, attribution=attribution,
                    importance=importance, units="hours")
    page.resize(1000, 700)
    page.show()
    assert not page.nav_combo.isVisible() and page._left.isVisible()
    page.resize(420, 700)
    assert page.nav_combo.isVisible() and not page._left.isVisible()
    # a heading row per group, the charts indented beneath it
    texts = [page.nav_combo.itemText(i) for i in range(page.nav_combo.count())]
    assert any(t.startswith("    ") for t in texts)
    assert any(heading and not t.startswith(" ")
               for (heading, _name), t in zip(page._combo_rows, texts, strict=True))
    row = next(i for i, (heading, name) in enumerate(page._combo_rows)
               if not heading and name == "hierarchy-pareto")
    page.nav_combo.activated.emit(row)
    assert page.selected_key() == "hierarchy-pareto"
    assert page.nav_combo.currentIndex() == row
    page.hide()


def test_a_gathered_treemap_cell_carries_its_members_for_details(qapp):
    page = ChartsPage()
    per_run, attribution, _importance = _inputs()
    attribution = dict(attribution)
    attribution["capacity"] = {
        "rule": "proportional",
        "elements": [{"name": f"E{i}", "share": {"mean": 0.2 if i == 0 else 0.8 / 204},
                      "lost_energy": {"mean": 1.0}}
                     for i in range(205)]}
    page.set_inputs(per_run=per_run, attribution=attribution)
    page.select("Lost capacity treemap")
    spec = page.selected_spec()
    group = next(row for row in spec.series if row["name"].endswith("others"))
    assert len(group["items"]) == 5
    widget = ChartWidget()
    widget.resize(900, 500)
    widget.set_spec(spec)
    widget.grab()
    assert any(row.get("items") for _rect, row in widget._detail_hits)
    from PySide6.QtWidgets import QDialog, QTableWidget
    seen = {}
    original = QDialog.exec
    QDialog.exec = lambda dialog: seen.setdefault(
        "rows", dialog.findChild(QTableWidget).rowCount())
    try:
        widget.show_details(group)
    finally:
        QDialog.exec = original
    assert seen["rows"] == 5


def test_the_search_filter_narrows_the_chart_tree_and_clears(qapp):
    page = ChartsPage()
    per_run, attribution, importance = _inputs()
    page.set_inputs(per_run=per_run, attribution=attribution,
                    importance=importance, units="hours", run_id="r_1")
    page.set_series(*_series())

    def shown():
        return [page.nav.topLevelItem(i).child(j).text(0)
                for i in range(page.nav.topLevelItemCount())
                if not page.nav.topLevelItem(i).isHidden()
                for j in range(page.nav.topLevelItem(i).childCount())
                if not page.nav.topLevelItem(i).child(j).isHidden()]

    page.search.setText("capacity")
    assert "Capacity through a run" in shown()
    assert "Availability ladder" not in shown()
    page.search.setText("no such chart")
    assert shown() == []
    page.search.clear()
    assert "Availability ladder" in shown()
    assert "Capacity through a run" in shown()


def test_the_list_is_grouped_by_question(qapp):
    page = ChartsPage()
    per_run, attribution, importance = _deep_hierarchy_inputs()
    page.set_inputs(per_run=per_run, attribution=attribution,
                    importance=importance, units="hours", run_id="r_1")
    groups = [page.nav.topLevelItem(i).text(0)
              for i in range(page.nav.topLevelItemCount())]
    # All charts first: every group on one page
    assert groups[:5] == ["ALL CHARTS", "THE ANSWER", "BY HIERARCHY", "OVER TIME",
                          "WHY IT GOES DOWN"]
    # a heading can be chosen: it shows the group's overview
    assert page.nav.topLevelItem(0).flags() & Qt.ItemFlag.ItemIsSelectable


def test_every_drawable_chart_paints_and_exports(qapp, tmp_path):
    page = ChartsPage()
    page.resize(900, 500)
    per_run, attribution, importance = _inputs()
    page.set_inputs(per_run=per_run, attribution=attribution,
                    importance=importance, units="hours", run_id="r_1")
    page.set_series(*_series())
    titles = page.titles()
    assert "Availability across all runs" in titles
    assert "Capacity through a run" in titles
    for index, title in enumerate(titles):
        assert page.select(title)
        assert page.stack.currentIndex() == 0, title
        image = page.chart.grab().toImage()
        assert not image.isNull()
        spec = page.selected_spec()
        assert spec is not None, title
        svg = tmp_path / f"chart-{index}.svg"
        page.save_svg(str(svg))
        assert svg.read_text(encoding="utf-8").startswith("<svg")
        csv = tmp_path / f"chart-{index}.csv"
        page.save_csv(str(csv))
        header = csv.read_text(encoding="utf-8").splitlines()[0]
        if spec.kind == "timeline":
            assert header.startswith("layer,")
        else:                                  # named for what the rows hold
            assert header and "note" not in header.split(","), (title, header)


def test_only_the_controls_a_chart_uses_are_shown(qapp):
    page = ChartsPage()
    page.set_inputs(per_run=_inputs()[0])
    page.set_series(*_series())
    page.select("availability-through-run")
    assert page.run_box.isVisibleTo(page) and page.floor_button.isVisibleTo(page)
    assert page.run_of.text() == "of 2"
    assert page.raw_action.isVisible()
    page.select("capacity-band")
    assert not page.run_box.isVisibleTo(page)
    assert not page.floor_button.isVisibleTo(page)
    page.select("trial-outcome")
    assert not page.run_box.isVisibleTo(page)
    assert not page.raw_action.isVisible()


def test_the_run_picker_redraws_the_one_run_chart(qapp):
    page = ChartsPage()
    page.set_inputs(per_run=_inputs()[0], units="hours")
    page.set_series(*_series())
    page.select("availability-through-run")
    assert page.selected_spec().provenance["run"] == 1
    page.run_box.setValue(2)
    assert page.selected_spec().provenance["run"] == 2
    assert page.selected_spec().title == "Availability through run 2"


def test_the_downtime_charts_offer_to_work_out_the_drivers(qapp):
    page = ChartsPage()
    page.set_inputs(per_run=_inputs()[0], drivers="pending")
    spy = QSignalSpy(page.drivers_requested)
    page.select("downtime-pareto")
    assert page.stack.currentIndex() == 1
    assert page.missing_action.isVisibleTo(page)
    page.missing_action.click()
    assert spy.count() == 1
    page.set_inputs(per_run=_inputs()[0], drivers="unavailable")
    assert "<b>Detailed event log</b> under Run panel ▸ Recording" in page.missing_reason(
        "downtime-pareto")


@pytest.mark.parametrize("chart, needs, label", [
    ("downtime-pareto", ["attribution"], "Turn on Detailed event log and run"),
    ("lost-capacity-pareto", ["attribution", "capacity"],
     "Turn on Detailed event log and Capacity and run"),
    ("capacity-band", ["capacity_plot"], "Turn on Capacity and Capacity plot and run"),
    ("availability-band", ["availability_plot"], "Turn on Availability plot and run"),
    ("cost-pareto", ["cost"], "Turn on Cost and run"),
    ("importance-quadrant", ["importance"], "Turn on Importance measures and run"),
])
def test_a_greyed_chart_offers_to_turn_its_option_on_and_run(qapp, chart, needs, label):
    page = ChartsPage()
    page.set_inputs(per_run=_inputs()[0], drivers="unavailable")
    spy = QSignalSpy(page.enable_requested)
    page.select(chart)
    assert page.stack.currentIndex() == 1
    assert page.missing_action.isVisibleTo(page)
    assert page.missing_action.text() == label
    page.missing_action.click()
    assert spy.count() == 1 and spy.at(0)[0] == needs


def test_an_importance_run_without_raw_says_to_choose_both(qapp):
    page = ChartsPage()
    page.set_inputs(per_run=_inputs()[0], importance={
        "mode": "improvement", "metrics": {"Availability": {"rows": []}}})
    assert "mode Both" in page.missing_reason("importance-quadrant")


def test_the_selection_survives_new_results(qapp):
    page = ChartsPage()
    per_run, attribution, _importance = _inputs()
    page.set_inputs(per_run=per_run, attribution=attribution)
    page.select("Who causes the downtime")
    page.set_inputs(per_run=per_run, attribution=attribution)
    assert page.selected_key() == "downtime-pareto"


def test_a_named_mark_locates_the_element(qapp):
    widget = ChartWidget()
    widget.resize(700, 380)
    page = ChartsPage()
    per_run, attribution, importance = _inputs()
    page.set_inputs(per_run=per_run, attribution=attribution,
                    importance=importance)
    page.select("Who causes the downtime")
    widget.set_spec(page.selected_spec())
    widget.grab()
    spy = QSignalSpy(widget.locate_requested)
    hit = next(rect for rect, kind, name in widget._hits
               if kind == "element" and name == "Pump")
    QTest.mouseClick(widget, Qt.MouseButton.LeftButton,
                     pos=QPoint(int(hit.center().x()), int(hit.center().y())))
    assert spy.count() == 1
    assert spy.at(0) == ["element", "Pump"]


def test_the_time_cursor_reads_every_layer(qapp):
    widget = ChartWidget()
    widget.resize(800, 420)
    page = ChartsPage()
    page.set_inputs(per_run=_inputs()[0], units="hours")
    page.set_series(*_series())
    page.select("capacity-through-run")
    widget.set_spec(page.selected_spec())
    widget.grab()
    frame, read = widget._probe
    marks = read(frame.x(6.0))
    text = marks[0][1]
    assert text.splitlines()[0] == "at 6 hours"
    assert "flow: 40" in text and "capacity lost: 60" in text


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
    names = page.titles()
    for name in ("Who loses the capacity", "Downtime against lost capacity",
                 "Availability year by year", "How often is a year below target?"):
        assert name in names
        page.select(name)
        assert not page.chart.grab().toImage().isNull()


def _many_elements(attribution, n=8):
    shares = [0.6, 0.3] + [0.1 / (n - 2)] * (n - 2)
    attribution = dict(attribution)
    attribution["elements"] = [
        {"name": f"Element_{i}", "dtci": {"mean": s, "sem": 0.01},
         "deci": {"mean": 0.1}, "red_time": {"mean": 1.0},
         "causes": {"repair": 1.0}, "main_cause": "repair"}
        for i, s in enumerate(shares)]
    return attribution


def test_the_fold_setting_folds_rankings_on_screen_and_in_saved_files(qapp, tmp_path):
    page = ChartsPage()
    per_run, attribution, _importance = _inputs()
    page.set_inputs(per_run=per_run, attribution=_many_elements(attribution))
    page.select("downtime-pareto")
    assert page.fold_box.isVisibleTo(page)
    assert len(page.chart.spec().series) == 8
    page.fold_box.setCurrentIndex(page.fold_box.findData("tail:0.05"))
    names = [row["name"] for row in page.chart.spec().series]
    assert names == ["Element_0", "Element_1", "Element_2", "Element_3",
                     "Element_4", "3 others (last 5 % of the total)"]
    path = tmp_path / "folded.svg"
    page.save_svg(str(path))
    assert "last 5 % of the total" in path.read_text(encoding="utf-8")
    page.select("downtime-pareto")
    page.fold_box.setCurrentIndex(page.fold_box.findData("fraction:0.25"))
    assert [row["name"] for row in page.chart.spec().series] == [
        "Element_0", "Element_1", "6 others"]
    page.fold_box.setCurrentIndex(page.fold_box.findData("tail:0.05"))
    assert "others (last 5 %" in page.chart.spec().series[-1]["name"]
    page.select("trial-outcome")           # not a ranking: no fold control
    assert not page.fold_box.isVisibleTo(page)


def test_saved_charts_use_the_chosen_page_in_every_format(qapp, tmp_path):
    from PySide6.QtGui import QImage

    page = ChartsPage()
    per_run, attribution, _importance = _inputs()
    page.set_inputs(per_run=per_run, attribution=attribution)
    page.select("downtime-pareto")
    assert page.export_page == "a4" and page.page_actions["a4"].isChecked()
    page.set_export_page("slide")
    assert page.page_actions["slide"].isChecked()
    page.save_svg(str(tmp_path / "c.svg"))
    assert "338.67mm" in (tmp_path / "c.svg").read_text(encoding="utf-8")
    page.save_png(str(tmp_path / "c.png"))
    picture = QImage(str(tmp_path / "c.png"))
    assert picture.width() == 4000 and picture.height() == 2250   # 300 dpi
    page.save_pdf(str(tmp_path / "c.pdf"))
    pdf = (tmp_path / "c.pdf").read_bytes()
    assert pdf.startswith(b"%PDF") and len(pdf) > 2000
    titles = [a.text() for a in page.export_menu.actions()]
    assert "Save PDF…" in titles and "Page size" in titles


def _hierarchy_inputs():
    """A small attribution with two top-level hierarchies and a timeline."""
    per_run, attribution, importance = _inputs()
    attribution = dict(attribution)
    attribution["hierarchies"] = [
        {"kind": "hier", "id": 100, "name": "Intake", "path": ["Intake"],
         "depth": 1, "parent": 0,
         "members": [{"kind": "block", "id": 1}, {"kind": "block", "id": 2}],
         "member_count": 2, "starter_count": 1, "elements": 1,
         "dtci": {"mean": 0.8, "sem": 0.02}, "red_time": {"mean": 5.0},
         "causes": {"repair": 1.0}, "main_cause": "repair",
         "phase_shares": {"Cruise": 0.75, "Launch": 0.25},
         "trials": [{"red_time": 5.0, "dtci": 0.8},
                    {"red_time": 4.0, "dtci": 0.7}]},
        {"kind": "hier", "id": 200, "name": "Filtration", "path": ["Filtration"],
         "depth": 1, "parent": 0,
         "members": [{"kind": "block", "id": 3}],
         "member_count": 1, "starter_count": 0, "elements": 0,
         "dtci": {"mean": 0.0, "sem": None}, "red_time": {"mean": 0.0},
         "causes": {}, "main_cause": None,
         "trials": [{"red_time": 0.0, "dtci": 0.0},
                    {"red_time": 0.0, "dtci": 0.0}]},
    ]
    attribution["phases"] = [
        {"name": "Cruise", "mission": True, "share_of_downtime": 0.6,
         "availability": 0.95, "drivers": [{"name": "Pump", "share": 1.0}]},
        {"name": "Launch", "mission": True, "share_of_downtime": 0.4,
         "availability": 0.9, "drivers": [{"name": "Cooler", "share": 1.0}]},
    ]
    attribution["outages"] = {"runs": [
        {"start": 0, "end": 10, "red": [2, 4],
         "hier": {"100": [2, 4]}},
        {"start": 0, "end": 10, "red": [],
         "hier": {}},
    ]}
    return per_run, attribution, importance


def test_hierarchy_charts_paint_export_and_follow_the_canvas(qapp, tmp_path):
    page = ChartsPage()
    page.resize(900, 500)
    per_run, attribution, importance = _hierarchy_inputs()
    page.set_inputs(per_run=per_run, attribution=attribution,
                    importance=importance, units="hours", run_id="r_1")
    assert "Hierarchy scorecard" in page.titles(available=False)
    assert "Where the downtime sits" in page.titles(available=False)
    titles = [title for title in page.titles()
              if "hierarchy" in title.lower()]
    assert "When did each hierarchy's downtime build up?" in titles
    for index, title in enumerate(titles):
        assert page.select(title)
        assert page.stack.currentIndex() == 0, title
        assert not page.chart.grab().toImage().isNull()
        spec = page.selected_spec()
        assert spec is not None
        svg = tmp_path / f"h-{index}.svg"
        page.save_svg(str(svg))
        assert svg.read_text(encoding="utf-8").startswith("<svg")
        csv = tmp_path / f"h-{index}.csv"
        page.save_csv(str(csv))
        assert csv.read_text(encoding="utf-8").splitlines()[0]
    page.follow_hierarchy(100)
    assert page.scope_button.text() == "Scope: Intake"
    assert "system-wide" in page.missing_reason("availability-band")
    assert page.select("Hierarchy Pareto")
    assert page.selected_spec().scope["id"] == 100
    assert "scope: Intake" in page.selected_spec().subtitle


def test_hierarchy_overlay_baseline_and_compare(qapp):
    page = ChartsPage()
    per_run, attribution, importance = _hierarchy_inputs()
    page.set_inputs(per_run=per_run, attribution=attribution,
                    importance=importance, units="hours", run_id="r_1")
    page.set_compare([100, 200])
    assert page.select("When did each hierarchy's downtime build up?")
    assert page.selected_spec().scope["kind"] == "selection"
    page._set_baseline()
    assert page.baseline_button.text() == "Baseline: set"
    assert page.select("Hierarchy change against baseline")
    assert page.selected_spec().kind == "delta"
    assert page.baseline_button.text() == "Baseline: set"


def test_depth_control_limits_nested_charts_and_rankings(qapp):
    page = ChartsPage()
    per_run, attribution, importance = _deep_hierarchy_inputs()
    page.set_inputs(per_run=per_run, attribution=attribution,
                    importance=importance, units="hours", run_id="r_1")
    assert [page.depth_box.itemText(i) for i in range(page.depth_box.count())] == [
        "All levels", "Top level", "Level 2"]
    assert page.select("Hierarchy sunburst")
    assert max(row["depth"] for row in page.selected_spec().series) == 2
    assert any("Pump house" in row["name"]
               for row in page.selected_spec().series)
    assert page.depth_box.findData(1) > 0
    page.depth_box.setCurrentIndex(page.depth_box.findData(1))
    assert max(row["depth"] for row in page.selected_spec().series) == 0
    page.depth_box.setCurrentIndex(page.depth_box.findData(2))
    sun = page.selected_spec()
    assert max(row["depth"] for row in sun.series) == 1
    assert page.select("Hierarchy by cause")
    assert {row["name"] for row in page.selected_spec().series} == {"Pump house"}
    assert page.select("Hierarchy treemap")
    assert any(row["name"] == "Pump house" for row in page.selected_spec().series)


def test_scope_inside_a_parent_hierarchy_uses_its_children(qapp):
    page = ChartsPage()
    per_run, attribution, importance = _deep_hierarchy_inputs()
    page.set_inputs(per_run=per_run, attribution=attribution,
                    importance=importance, units="hours", run_id="r_1")
    page.set_scope(100)
    assert page.scope_button.text() == "Scope: Intake"
    assert "Who causes the downtime" not in page.titles(available=False)
    assert page.select("Hierarchy sunburst")
    spec = page.selected_spec()
    assert spec.title == "Inside Intake"
    assert spec.scope["name"] == "Intake"
    # Depth starts fully nested; a level removes detail (#351)
    assert page.depth_box.currentText() == "All levels"
    assert {int(row["depth"]) for row in spec.series} == {0, 1, 2}
    page.depth_box.setCurrentIndex(page.depth_box.findData(1))   # 1 level down
    spec = page.selected_spec()
    assert {int(row["depth"]) for row in spec.series} == {0, 1}
    assert any("Pump house" in row["name"] for row in spec.series)
    assert page.select("Hierarchy treemap")
    spec = page.selected_spec()
    assert spec.kind == "treemap"
    assert spec.scope["name"] == "Intake"
    assert {row["name"] for row in spec.series} >= {"Pump house"}
    assert all(row["name"] != "Cooling" for row in spec.series)
    page.depth_box.setCurrentIndex(page.depth_box.findData(1))   # 1 level down
    assert page.select("Hierarchy Pareto")
    names = {row["name"] for row in page.selected_spec().series}
    assert "Pump house" in names and "Cooling" not in names
    page.depth_box.setCurrentIndex(page.depth_box.findData(0))   # every level
    names = {row["name"] for row in page.selected_spec().series}
    assert names == {"Pump", "Sluice"}


def _deep_hierarchy_inputs():
    """Intake > Pump house, plus a separate Cooling branch, with two trials."""
    per_run, attribution, importance = _inputs()
    attribution = dict(attribution)
    attribution["hierarchies"] = [
        {"kind": "hier", "id": 100, "name": "Intake", "path": ["Intake"],
         "depth": 1, "parent": 0,
         "members": [{"kind": "block", "id": 1}, {"kind": "block", "id": 2}],
         "direct_members": [{"kind": "block", "id": 2}],
         "member_count": 2, "starter_count": 2, "elements": 1,
         "dtci": {"mean": 0.8, "sem": 0.02},
         "causes": {"repair": 1.0}, "main_cause": "repair",
         "phase_shares": {"Cruise": 0.75, "Launch": 0.25},
         "trials": [{"red_time": 5.0, "dtci": 0.8},
                    {"red_time": 4.0, "dtci": 0.7}]},
        {"kind": "hier", "id": 101, "name": "Pump house",
         "path": ["Intake", "Pump house"], "depth": 2, "parent": 100,
         "members": [{"kind": "block", "id": 1}],
         "direct_members": [{"kind": "block", "id": 1}],
         "member_count": 1, "starter_count": 1, "elements": 1,
         "dtci": {"mean": 0.5, "sem": 0.01},
         "causes": {"repair": 1.0}, "main_cause": "repair",
         "phase_shares": {"Cruise": 0.4, "Launch": 0.6},
         "trials": [{"red_time": 3.0, "dtci": 0.5},
                    {"red_time": 2.0, "dtci": 0.4}]},
        {"kind": "hier", "id": 200, "name": "Cooling", "path": ["Cooling"],
         "depth": 1, "parent": 0,
         "members": [{"kind": "block", "id": 3}],
         "direct_members": [{"kind": "block", "id": 3}],
         "member_count": 1, "starter_count": 1, "elements": 1,
         "dtci": {"mean": 0.2, "sem": 0.01},
         "causes": {"wait_spare": 1.0}, "main_cause": "wait_spare",
         "phase_shares": {"Cruise": 0.5, "Launch": 0.5},
         "trials": [{"red_time": 1.0, "dtci": 0.2},
                    {"red_time": 1.0, "dtci": 0.2}]},
    ]
    attribution["elements"] = [
        {"kind": "block", "id": 1, "name": "Pump",
         "dtci": {"mean": 0.5, "sem": 0.01}, "causes": {"repair": 1.0},
         "main_cause": "repair"},
        {"kind": "block", "id": 2, "name": "Sluice",
         "dtci": {"mean": 0.3, "sem": 0.01}, "causes": {"repair": 1.0},
         "main_cause": "repair"},
        {"kind": "block", "id": 3, "name": "Cooler",
         "dtci": {"mean": 0.2, "sem": 0.01}, "causes": {"wait_spare": 1.0},
         "main_cause": "wait_spare"},
    ]
    attribution["phases"] = [
        {"name": "Cruise", "mission": True, "share_of_downtime": 0.6,
         "availability": 0.95, "drivers": [{"name": "Pump", "share": 1.0}]},
        {"name": "Launch", "mission": True, "share_of_downtime": 0.4,
         "availability": 0.9, "drivers": [{"name": "Cooler", "share": 1.0}]},
    ]
    attribution["outages"] = {"runs": [
        {"start": 0, "end": 10, "red": [2, 4],
         "hier": {"100": [2, 4], "101": [3, 4]}},
        {"start": 0, "end": 10, "red": [], "hier": {}},
    ]}
    return per_run, attribution, importance


def test_depth_control_offers_all_levels_and_each_level(qapp):
    page = ChartsPage()
    per_run, attribution, importance = _deep_hierarchy_inputs()
    page.set_inputs(per_run=per_run, attribution=attribution,
                    importance=importance, units="hours", run_id="r_1")
    assert [page.depth_box.itemText(i) for i in range(page.depth_box.count())] \
        == ["All levels", "Top level", "Level 2"]
    assert page.depth_box.currentText() == "All levels"

    assert page.select("Hierarchy sunburst")
    all_depths = {int(row["depth"]) for row in page.selected_spec().series}
    assert all_depths == {0, 1, 2}

    page.depth_box.setCurrentIndex(page.depth_box.findData(2))
    assert {int(row["depth"]) for row in page.selected_spec().series} == {0, 1}

    page.depth_box.setCurrentIndex(page.depth_box.findData(1))
    assert {int(row["depth"]) for row in page.selected_spec().series} == {0}

    assert page.select("Hierarchy scorecard")
    assert {row["name"] for row in page.selected_spec().series} \
        == {"Intake", "Cooling"}
    page.depth_box.setCurrentIndex(page.depth_box.findData(2))
    assert {row["name"] for row in page.selected_spec().series} == {"Pump house"}


def test_nested_scope_sunburst_and_treemap_draw_inside_one_hierarchy(qapp):
    page = ChartsPage()
    per_run, attribution, importance = _deep_hierarchy_inputs()
    page.set_inputs(per_run=per_run, attribution=attribution,
                    importance=importance, units="hours", run_id="r_1")
    page.follow_hierarchy(100)
    assert page.scope_button.text() == "Scope: Intake"
    assert page.select("Hierarchy sunburst")
    spec = page.selected_spec()
    assert spec.title == "Inside Intake"
    assert spec.scope["id"] == 100
    assert {row["name"] for row in spec.series} >= {"Intake"}
    assert not page.chart.grab().toImage().isNull()

    assert page.select("Hierarchy treemap")
    spec = page.selected_spec()
    assert spec.kind == "treemap"
    assert spec.scope["id"] == 100
    root = next(row for row in spec.series if row.get("is_group"))
    assert root["name"] == "Intake"
    assert any(row["name"] == "Pump house" for row in spec.series)


def test_scope_picker_lists_nested_hierarchies_under_their_parent(qapp):
    page = ChartsPage()
    per_run, attribution, importance = _deep_hierarchy_inputs()
    page.set_inputs(per_run=per_run, attribution=attribution,
                    importance=importance, units="hours", run_id="r_1")
    tree = page.picker("scope").tree
    top = [tree.topLevelItem(i).text(0) for i in range(tree.topLevelItemCount())]
    assert top[0] == "System"
    assert "Intake" in top
    intake = next(tree.topLevelItem(i) for i in range(tree.topLevelItemCount())
                  if tree.topLevelItem(i).text(0) == "Intake")
    assert [intake.child(i).text(0) for i in range(intake.childCount())] == [
        "Pump house"]


def test_a_theme_change_keeps_the_chart_and_repaints_the_list(qapp):
    page = ChartsPage()
    per_run, attribution, importance = _deep_hierarchy_inputs()
    page.set_inputs(per_run=per_run, attribution=attribution,
                    importance=importance, units="hours", run_id="r_1")
    assert page.select("Hierarchy sunburst")
    page._on_theme_changed(True)
    assert page.selected_key() == "hierarchy-map"
    assert page.selected_spec() is not None
    assert not page.chart.grab().toImage().isNull()


def test_a_model_without_hierarchies_has_no_hierarchy_group_or_controls(qapp):
    page = ChartsPage()
    per_run, attribution, importance = _inputs()
    page.set_inputs(per_run=per_run, attribution=attribution,
                    importance=importance, units="hours", run_id="r_1")
    groups = [page.nav.topLevelItem(i).text(0)
              for i in range(page.nav.topLevelItemCount())]
    assert "BY HIERARCHY" not in groups
    for widget in (page.scope_button, page.follow_button,
                   page.compare_button, page.baseline_button):
        assert widget.isHidden()
    assert page.depth_box.isHidden()
    assert "Where the downtime sits" not in page.titles(available=False)


def test_hierarchy_phase_heatmap_follows_scope_and_depth(qapp):
    page = ChartsPage()
    per_run, attribution, importance = _deep_hierarchy_inputs()
    page.set_inputs(per_run=per_run, attribution=attribution,
                    importance=importance, units="hours", run_id="r_1")
    assert page.select("Hierarchy by phase")
    spec = page.selected_spec()
    assert [row["name"] for row in spec.series] == ["Intake", "Cooling"]
    assert page.depth_box.currentText() == "All levels"
    page.depth_box.setCurrentIndex(page.depth_box.findData(2))
    assert [row["name"] for row in page.selected_spec().series] == ["Pump house"]
    page.depth_box.setCurrentIndex(page.depth_box.findData(0))
    page.set_scope(100)
    spec = page.selected_spec()
    assert spec.title == "Phases inside Intake"
    assert [row["name"] for row in spec.series] == ["Intake"]
    assert spec.scope["name"] == "Intake"


def test_the_nested_view_switches_layout_without_moving_the_chart(qapp):
    page = ChartsPage()
    per_run, attribution, importance = _deep_hierarchy_inputs()
    page.set_inputs(per_run=per_run, attribution=attribution,
                    importance=importance, units="hours", run_id="r_1")
    assert page.select("Where the downtime sits")
    assert page.selected_spec().kind == "treemap"
    page.layout_box.setCurrentIndex(page.layout_box.findData("sunburst"))
    assert page.selected_key() == "hierarchy-map"
    assert page.selected_spec().kind == "sunburst"
    page.layout_box.setCurrentIndex(page.layout_box.findData("treemap"))
    assert page.selected_spec().kind == "treemap"
    assert page.depth_box.isVisibleTo(page)


def test_scope_keeps_overlay_and_small_multiples_inside_it(qapp):
    page = ChartsPage()
    per_run, attribution, importance = _deep_hierarchy_inputs()
    page.set_inputs(per_run=per_run, attribution=attribution,
                    importance=importance, units="hours", run_id="r_1")
    page.set_scope(100)
    assert page.select("Is the hierarchy ranking stable?")
    assert [facet.title for facet in page.selected_spec().facets] == ["Pump house"]
    assert page.selected_spec().scope["path"] == ["Pump house"]


def test_hierarchy_without_phases_drops_phase_rows_and_the_flat_treemap(qapp):
    page = ChartsPage()
    per_run, attribution, importance = _hierarchy_inputs()
    attribution = dict(attribution)
    attribution.pop("phases", None)
    page.set_inputs(per_run=per_run, attribution=attribution,
                    importance=importance, units="hours", run_id="r_1")
    titles = page.titles(available=False)
    assert "Downtime treemap" not in titles
    assert "Hierarchy by phase" not in titles
    assert "Downtime by phase" not in titles


def test_capacity_target_is_set_on_the_capacity_year_chart(qapp):
    page = ChartsPage()
    per_run, attribution, importance = _inputs()
    periods = {"runs": 2, "periods_per_run": 2, "threshold": 0.95,
               "capacity": {"by_index": [
                   {"index": 1, "n": 2, "p50": 0.90},
                   {"index": 2, "n": 2, "p50": 0.97}]}}
    page.set_inputs(per_run=per_run, attribution=attribution,
                    importance=importance, periods=periods)
    assert page.select("Capacity delivered year by year")
    assert page.capacity_target_button.isVisibleTo(page)
    page.set_capacity_target(0.99)
    assert any(mark.get("kind") == "target" and mark.get("value") == 0.99
               for mark in page.selected_spec().marks)


def test_no_event_log_still_shows_hierarchy_and_phase_questions(qapp):
    page = ChartsPage()
    page.set_inputs(per_run=[], attribution=None, drivers="unavailable",
                    model_hierarchies=True, model_phases=True)
    assert "Hierarchy scorecard" in page.titles(available=False)
    assert "Downtime by phase" in page.titles(available=False)
    assert page.select("Hierarchy scorecard")
    assert "event log" in page.missing_text.text()
    assert page.missing_action.isVisibleTo(page)


def test_scorecard_and_overlay_paint_with_baseline_and_end_labels(qapp):
    page = ChartsPage()
    per_run, attribution, importance = _hierarchy_inputs()
    page.set_inputs(per_run=per_run, attribution=attribution,
                    importance=importance, units="hours", run_id="r_1")
    page.resize(900, 700)
    page.set_baseline(list(attribution["hierarchies"][:1]))
    page._rebuild()
    assert page.select("Hierarchy scorecard")
    rows = {r["name"]: r for r in page.selected_spec().series}
    assert rows["Intake"]["baseline"]["status"] == "same"
    assert rows["Filtration"]["baseline"]["status"] == "new"
    assert rows["Intake"]["dots"]
    assert not page.chart.grab().isNull()
    page.set_compare([100])
    assert page.select("When did each hierarchy's downtime build up?")
    assert page.selected_spec().provenance["end_labels"]
    assert not page.chart.grab().isNull()


def test_the_capacity_target_button_shows_on_every_chart_that_uses_it(qapp):
    from raptor.gui.ui.charts_page import CAPACITY_TARGET_CHARTS
    page = ChartsPage()
    per_run, attribution, importance = _hierarchy_inputs()
    attribution = dict(attribution)
    attribution["capacity"] = {
        "rule": "proportional", "generated": 100.0,
        "delivered_energy": {"mean": 80.0}, "lost_energy": {"mean": 20.0},
        "elements": [{"kind": "block", "id": 1, "name": "Pump",
                      "lost_energy": {"mean": 20.0}, "share": {"mean": 1.0},
                      "causes": {"repair": 1.0}, "main_cause": "repair"}],
        "causes": {"repair": {"mean": 20.0}}}
    page.set_inputs(per_run=per_run, attribution=attribution,
                    importance=importance, units="hours", generated=100.0)
    page.set_series(*_series())
    for key in CAPACITY_TARGET_CHARTS:
        if key == "annual-capacity-fan":
            continue            # needs a year-long run: covered above
        page.select(key)
        assert page.capacity_target_button.isVisibleTo(page), key
    for key in ("availability-band", "lost-capacity-pareto", "trial-outcome"):
        page.select(key)
        assert not page.capacity_target_button.isVisibleTo(page), key


def test_changing_the_capacity_target_rebuilds_the_chart_on_show(qapp):
    page = ChartsPage()
    per_run, attribution, importance = _hierarchy_inputs()
    attribution = dict(attribution)
    attribution["capacity"] = {
        "rule": "proportional", "generated": 100.0,
        "delivered_energy": {"mean": 80.0}, "lost_energy": {"mean": 20.0},
        "elements": [{"kind": "block", "id": 1, "name": "Pump",
                      "lost_energy": {"mean": 20.0}, "share": {"mean": 1.0},
                      "causes": {"repair": 1.0}, "main_cause": "repair"}],
        "causes": {"repair": {"mean": 20.0}}}
    page.set_inputs(per_run=per_run, attribution=attribution,
                    importance=importance, units="hours", generated=100.0)
    page.set_series(*_series())
    page.select("capacity-band")
    assert not page.selected_spec().marks
    page.set_capacity_target(0.9)
    spec = page.selected_spec()
    assert spec.marks[0]["share"] == 0.9
    assert "capacity target" in spec.caption
    assert page.capacity_target_button.text() == "Capacity target: 90 %"
    page.select("capacity-waterfall")
    assert "missed the capacity target 90 % by 10 points" in page.selected_spec().caption
    page.set_capacity_target(None)
    assert "capacity target" not in page.selected_spec().caption
    page.select("capacity-band")
    assert not page.selected_spec().marks


def test_a_builder_value_error_is_shown_not_logged_as_an_error(qapp, caplog):
    import logging

    from raptor.gui.ui.charts_page import _Entry
    page = ChartsPage()

    def entry(build):
        return _Entry("x-chart", "X chart", "Why it goes down", build)

    def refuse():
        raise ValueError("Nothing to show, here is why.")

    def bug():
        raise RuntimeError("boom")
    with caplog.at_level(logging.DEBUG):
        assert page._safe(entry(refuse)) is None
    assert page._build_errors["x-chart"] == "Nothing to show, here is why."
    assert not [r for r in caplog.records if r.levelno >= logging.ERROR]
    caplog.clear()
    with caplog.at_level(logging.DEBUG):
        assert page._safe(entry(bug)) is None
    assert any(r.levelno == logging.ERROR for r in caplog.records)


def test_the_lost_capacity_sankey_is_greyed_out_when_nothing_was_lost(qapp):
    page = ChartsPage()
    per_run, attribution, importance = _hierarchy_inputs()
    attribution = dict(attribution)
    attribution["capacity"] = {
        "rule": "proportional", "generated": 100.0,
        "delivered_energy": {"mean": 100.0}, "lost_energy": {"mean": 0.0},
        "elements": [], "causes": {}}
    page.set_inputs(per_run=per_run, attribution=attribution,
                    importance=importance, units="hours")
    entry = next(e for e in page._catalogue()
                 if e.key == "capacity-sankey")
    assert "No capacity was lost" in entry.missing
