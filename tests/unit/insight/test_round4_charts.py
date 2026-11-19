"""Fourth round of new-user testing: the charts with perfect, one-run,
very short and very large results, and what they say about them."""
from __future__ import annotations

import os

import pytest

from raptor.api.insight import charts, timecharts
from raptor.api.insight.axes import log_ticks, nice_range, nice_ticks
from raptor.render.charts_svg import _key, render

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")


def _element(name, dtci, causes=None):
    return {"name": name, "dtci": {"mean": dtci, "sem": 0.01}, "deci": {"mean": 0.0},
            "red_time": {"mean": 0.0}, "causes": causes or {"repair": 1.0},
            "outages": {"mean": 1.0}}


@pytest.mark.parametrize("low, high", [(1.0, 1.0), (0.99999994, 1.0), (0.2, 0.2000001)])
def test_a_share_axis_stays_in_0_to_1_and_spans_a_point(low, high):
    lo, hi = nice_range(low, high, 5, share=True)
    assert 0.0 <= lo <= low and high <= hi <= 1.0
    assert hi - lo >= 0.01 - 1e-12
    # round ticks, not eight decimals
    assert all(abs(t * 1e4 - round(t * 1e4)) < 1e-6 for t in nice_ticks(lo, hi, 5))


def test_log_ticks_are_round_and_inside():
    ticks = log_ticks(0.7, 3900)
    assert ticks[0] >= 0.7 and ticks[-1] <= 3900 and 1 in ticks and 1000 in ticks


def test_one_run_and_perfect_runs_are_said_plainly():
    one = charts.ecdf([{"availability": 0.94179}])
    assert "the one run reached" in one.caption.lower() and "9 runs in 10" not in one.caption
    every = charts.ecdf([{"availability": 1.0}] * 5)
    assert "Every run reached" in every.caption
    assert "1 runs" not in charts.trial_outcome([{"availability": 0.9}]).caption


def test_nothing_down_is_one_plain_sentence():
    attribution = {"runs": 10, "elements": [], "causes": {}, "sequences": []}
    for build in (charts.downtime_pareto, charts.downtime_treemap,
                  charts.outage_anatomy):
        assert build(attribution).caption.startswith(
            "The system never went down in 10 runs"), build.__name__


def test_a_cut_ranking_says_so():
    attribution = {"runs": 3, "elements": [_element(f"E{i}", 1 / 30) for i in range(30)]}
    spec = charts.downtime_pareto(attribution)
    assert len(spec.series) < 30 and "of 30" in spec.subtitle


def test_a_phase_with_no_downtime_is_not_the_weakest():
    spec = charts.phase_downtime({"runs": 4, "phases": [
        {"name": "Climb", "share_of_downtime": 0.0, "availability": 1.0, "drivers": []}]})
    assert "weakest" not in spec.caption and spec.series == []


def test_a_single_sample_run_has_no_time_chart_and_says_why():
    ao = [{"t": [2.0], "values": [1.0], "green": [1.0], "yellow": [0.0], "red": [0.0],
           "interval": [None]}]
    with pytest.raises(ValueError, match="shorter than two recording intervals"):
        timecharts.availability_through_run(ao, 1, units="hours")
    with pytest.raises(ValueError, match="shorter than two recording intervals"):
        timecharts.interval_availability(ao, 1, units="hours")


def test_unavailable_charts_are_an_error_not_a_blank_figure():
    attribution = {"runs": 2, "elements": [], "causes": {}}
    for name in ("lost-capacity-pareto", "capacity-waterfall", "phase-downtime"):
        with pytest.raises(ValueError):
            charts.chart_named(name, attribution=attribution)


def test_the_text_gives_numbers_never_blank_rows():
    spec = charts.convergence([{"availability": v} for v in (0.9, 0.95, 0.97)],
                              "availability")
    lines = charts.text_lines(spec)
    assert any("after 3 runs" in line for line in lines)
    hist = charts.outage_histogram({"runs": 2, "units": "hours", "outage_durations": {
        "hist": [{"lo": 1.0, "hi": 10.0, "count": 4.0}], "p50": 2.0, "p90": 8.0}})
    lines = charts.text_lines(hist)
    assert lines[1].startswith("  1 to 10 hours: 4")
    assert not any(line.startswith("  :") for line in lines)


def test_the_csv_names_its_columns_and_keeps_errors_and_causes():
    spec = charts.downtime_pareto({"runs": 3, "elements": [
        _element("Pump", 0.3, {"repair": 0.7, "logistics": 0.3})]})
    header, rows = charts.csv_table(spec)
    assert any(h.startswith("share of system downtime (fraction") for h in header)
    assert any(h.startswith("standard error") for h in header)
    assert any(h.startswith("cause: ") for h in header)
    assert rows[0][header.index("name")] == "Pump"


def test_the_saved_key_tells_the_bands_apart_and_explains_the_whiskers():
    runs = [{"t": [10.0, 20.0, 30.0], "values": [0.9 + 0.01 * i, 0.92, 0.93]}
            for i in range(4)]
    band = timecharts.availability_band(runs, units="hours")
    fills = [fill for _label, fill in _key(band)]
    assert len(set(fills)) == len(fills)
    pareto = charts.downtime_pareto({"runs": 3, "elements": [_element("Pump", 0.3)]})
    assert ("± 2 standard errors", "whisker") in _key(pareto)
    assert "<svg" in render(band) and "<svg" in render(pareto)


def test_a_wide_raw_spread_gets_a_log_axis():
    rows = [{"block": f"B{i}", "raw": raw, "rrw": 1.1}
            for i, raw in enumerate((1.2, 3.0, 2600.0))]
    spec = charts.importance_quadrant({"metrics": {"Availability": {"rows": rows}}})
    assert spec.provenance["x_log"] is True
    assert "log scale" in render(spec)


def test_the_year_charts_say_year():
    spec = charts.annual_fan({"by_index": [{"index": 1, "n": 3, "p50": 0.99}]})
    assert spec.x_label == "year" and "P5" not in spec.subtitle


@pytest.fixture()
def qapp():
    from PySide6.QtWidgets import QApplication
    return QApplication.instance() or QApplication([])


@pytest.mark.parametrize("height", [240, 500])
def test_a_short_chart_panel_still_draws(qapp, height):
    from raptor.gui.ui.chart_widget import ChartWidget
    widget = ChartWidget()
    widget.resize(900, height)
    widget.set_spec(charts.downtime_pareto(
        {"runs": 3, "elements": [_element(f"E{i}", 0.05) for i in range(12)]}))
    assert not widget.grab().toImage().isNull()
