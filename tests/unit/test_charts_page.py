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


def test_the_list_is_grouped_by_question(qapp):
    page = ChartsPage()
    page.set_inputs(per_run=_inputs()[0])
    groups = [page.nav.topLevelItem(i).text(0)
              for i in range(page.nav.topLevelItemCount())]
    assert groups[:3] == ["THE ANSWER", "OVER TIME", "WHY IT GOES DOWN"]
    # a heading cannot be chosen
    assert not page.nav.topLevelItem(0).flags() & Qt.ItemFlag.ItemIsSelectable


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
        assert header.startswith("layer," if spec.kind == "timeline" else "series,")


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
                 "Availability year by year", "A bad year, as a frequency"):
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
    page.fold_box.setCurrentIndex(page.fold_box.findData(0.05))
    assert page.fold_share == 0.05
    names = [row["name"] for row in page.chart.spec().series]
    assert names == ["Element_0", "Element_1", "6 others below 5 %"]
    path = tmp_path / "folded.svg"
    page.save_svg(str(path))
    assert "6 others below 5 %" in path.read_text(encoding="utf-8")
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
