"""The charts over time (the old Graphs tab) as Qt-free ChartSpecs."""
from __future__ import annotations

import json

import pytest

from raptor.api import build
from raptor.api import run as run_api
from raptor.api.insight import charts, timecharts
from raptor.api.series import parse_ao_runs, parse_capacity_runs
from raptor.api.workspace import Workspace
from raptor.cli import exitcodes as X
from raptor.cli.main import main
from raptor.core.fileio.rbds import save_rbds
from raptor.render.charts_svg import render

_MARK = ["", "*" * 30 + "End_of_Run_{n}" + "*" * 30, ""]
AO = (["SimulationTime Ao Green Yellow Red Interval",
       "10 0.9 0.8 0.1 0.1 0.9", "20 0.85 0.7 0.15 0.15 0.8", "30 0.8 0.6 0.2 0.2 0.7",
       "30 0.8 0.6 0.2 0.2"]
      + [m.format(n=1) for m in _MARK]
      + ["10 1 1 0 0 1", "20 0.95 0.9 0.05 0.05 0.9", "30 0.9 0.85 0.05 0.1 0.8",
         "30 0.9 0.85 0.05 0.1"]
      + [m.format(n=2) for m in _MARK])
CAP = (["SimulationTime Flow Capacity", "0 100 100", "5 40 40", "8 100 100"]
       + [m.format(n=1) for m in _MARK]
       + ["0 100 100", "12 0 0", "15 100 100"] + [m.format(n=2) for m in _MARK])


def _ao():
    return parse_ao_runs("\n".join(AO))


def _cap():
    return parse_capacity_runs("\n".join(CAP))


def test_the_bands_of_one_run_stack_to_the_whole_time():
    spec = timecharts.availability_through_run(_ao(), 1, units="hours")
    up, degraded, down, line = spec.series
    for i in range(len(up["t"])):
        assert up["lo"][i] == 0 and up["hi"][i] == degraded["lo"][i]
        assert degraded["hi"][i] == down["lo"][i] == line["y"][i]
        assert down["hi"][i] == pytest.approx(1.0)
    # the top of the yellow band is the run's availability (green + yellow)
    assert line["y"][-1] == pytest.approx(0.8)
    assert spec.caption.startswith("Run 1: up 80 % of the time by 30 hours")
    assert spec.provenance["y_range"] == [0.0, 1.0]


def test_each_interval_is_a_step_and_the_worst_one_is_named():
    spec = timecharts.interval_availability(_ao(), 1, units="hours", target=0.85)
    steps = spec.series[0]
    assert steps["t"] == [0.0, 10.0, 10.0, 20.0, 20.0, 30.0]
    assert steps["y"] == [0.9, 0.9, 0.8, 0.8, 0.7, 0.7]
    assert "the worst was 70 %, ending at 30 hours" in spec.caption
    assert "2 of them were below the target" in spec.caption


def test_a_long_run_keeps_the_worst_interval_of_each_group():
    rows = ["SimulationTime Ao Green Yellow Red Interval"]
    for i in range(1, 6001):
        value = 0.25 if i == 4321 else 1.0
        rows.append(f"{i} 1 1 0 0 {value}")
    runs = parse_ao_runs("\n".join(rows))
    spec = timecharts.interval_availability(runs, 1)
    steps = spec.series[0]
    assert len(steps["t"]) <= 2 * timecharts.MAX_POINTS
    assert min(steps["y"]) == 0.25            # the dip survives the grouping
    assert spec.provenance["grouped"] == 4
    assert "worst of 4 intervals" in spec.caption


def test_the_across_runs_bands_hold_the_runs():
    spec = timecharts.availability_band(_ao(), points=4)
    every, most, mean = spec.series
    end = {name: layer["y" if "y" in layer else "hi"][-1] for name, layer in
           (("mean", mean), ("max", every))}
    assert every["lo"][-1] == pytest.approx(0.8) and end["max"] == pytest.approx(0.9)
    assert end["mean"] == pytest.approx(0.85)
    for i in range(4):
        assert every["lo"][i] <= most["lo"][i] <= mean["y"][i] <= most["hi"][i] <= every["hi"][i]
    with pytest.raises(ValueError, match="at least 2 runs"):
        timecharts.availability_band(_ao()[:1])


def test_capacity_holds_its_last_value_to_the_end_of_the_run():
    # without the run's end, run 1 stops at its last change (t = 8)
    short = timecharts.capacity_through_run(_cap(), 1, generated=100.0)
    assert short.provenance["delivered"] == pytest.approx((5 * 100 + 3 * 40) / 800)
    full = timecharts.capacity_through_run(_cap(), 1, generated=100.0, ends=[20.0, 20.0])
    assert full.series[0]["t"][-1] == 20.0
    assert full.provenance["delivered"] == pytest.approx((5 * 100 + 3 * 40 + 12 * 100) / 2000)
    assert "lowest flow 40 of 100" in full.caption
    assert full.series[-1]["name"] == "flow generated"


def test_without_the_model_full_capacity_is_the_highest_recorded():
    spec = timecharts.capacity_through_run(_cap(), 2)
    assert spec.series[-1]["name"] == "full capacity (highest recorded)"
    assert spec.provenance["full"] == 100.0


def test_the_capacity_band_and_the_lost_capacity_share_agree():
    spec = timecharts.capacity_band(_cap(), generated=100.0, ends=[20.0, 20.0])
    lost = (3 * 60) + (3 * 100)
    assert spec.provenance["delivered"] == pytest.approx(1 - lost / 4000)


def test_chart_named_builds_them_and_says_what_is_missing():
    spec = charts.chart_named("availability-through-run", ao_runs=_ao(), run=2,
                              units="hours")
    assert spec.title == "Availability through run 2"
    with pytest.raises(ValueError, match="availability plot"):
        charts.chart_named("availability-band")
    with pytest.raises(ValueError, match="run 3 is not in this result"):
        charts.chart_named("interval-availability", ao_runs=_ao(), run=3)
    with pytest.raises(ValueError, match="capacity plot"):
        charts.chart_named("capacity-band")
    for name in timecharts.TIME_CHARTS:
        assert name in charts.CHARTS and charts.CHARTS[name].__doc__


@pytest.mark.parametrize("name", timecharts.TIME_CHARTS)
def test_every_chart_over_time_renders_to_svg(name):
    spec = timecharts.time_chart(name, ao_runs=_ao(), cap_runs=_cap(), units="hours")
    svg = render(spec)
    assert svg.startswith("<svg") and "<polyline" in svg
    for layer in spec.series:
        assert layer["name"] in svg          # the key names every layer


SPEC = {
    "blocks": [{"name": "Pump", "fail_dist": "Exponential", "fail_params": [300, 0, 0],
                "repair_dist": "Lognormal", "repair_params": [10, 3, 0]}],
    "nodes": [{"name": "Start", "type": 1}, {"name": "End", "type": 3}],
    "links": [{"from": "Start", "to": "Pump"}, {"from": "Pump", "to": "End"}],
    "experiment": {"trials": 5, "termination_type": "TimeSim", "term_condition": 2000},
}


def test_explain_records_the_plot_an_availability_chart_needs(capsys, tmp_path):
    path = tmp_path / "m.rbds"
    save_rbds(build.build_from_spec(SPEC), str(path))
    svg = tmp_path / "band.svg"
    assert main(["explain", str(path), "--seed", "5", "--chart", "availability-band",
                 "--chart-svg", str(svg)]) == X.OK
    out = capsys.readouterr().out
    assert "Availability across all runs — 5 runs end with 8 in 10" in out
    assert "mean availability:" in out
    assert svg.read_text().startswith("<svg")
    assert main(["explain", str(path), "--seed", "5", "--chart", "downtime-pareto",
                 "--chart-svg", str(svg), "--chart-page", "a4",
                 "--chart-min-share", "0.01"]) == X.OK
    capsys.readouterr()
    assert "width='297mm'" in svg.read_text()
    assert main(["explain", str(path), "--seed", "5", "--chart",
                 "availability-through-run", "--chart-run", "3", "-f", "json"]) == X.OK
    data = json.loads(capsys.readouterr().out)
    assert data["chart"]["title"] == "Availability through run 3"
    assert data["chart"]["kind"] == "timeline"


def test_the_stored_run_feeds_the_charts(tmp_path):
    ws = Workspace(tmp_path)
    mid = ws.register_model(build.build_from_spec(SPEC), name="m")["model_id"]
    result = run_api.run(ws, mid, seed=5, availability_plot=True)
    inputs = timecharts.stored_inputs(run_api.run_dir(ws, result.run_id))
    assert len(inputs["ao_runs"]) == 5 and inputs["units"] == "hours"
    assert inputs["cap_runs"] == []
    assert all(row["ending_sim_time"] == pytest.approx(2000.0) for row in result.per_run)


def test_raptors_unlimited_capacity_is_not_drawn_as_a_billion():
    # no link or block capacities: up -> 999,999,999 ("no limit"), down -> 0
    text = "\n".join(["SimulationTime Flow Capacity", "0 1 999999999", "5 0 0",
                      "8 1 999999999"] + [m.format(n=1) for m in _MARK])
    spec = timecharts.capacity_through_run(parse_capacity_runs(text), 1)
    assert spec.provenance["y_range"][1] < 10
    assert "capacity available" not in [layer["name"] for layer in spec.series]
    assert "unlimited" in spec.caption
