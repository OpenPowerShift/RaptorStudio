"""Ao availability plots (manual Ch.9): parser and widget tests.

The engine's `_ao_sink` rows feed the GUI's Graphs tab (Per-Trial /
Interval / Multi-run Ao). Covers the text format parsing (including the
run-separator subtlety: `***End_of_Run_N***` is both a "*"-prefixed line
and the run boundary), the multi-run envelope aggregation, and that the
real engine's sink output parses round-trip.
"""
from __future__ import annotations

import pathlib

import pytest

pytest.importorskip("PySide6")

import os
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication

from raptor.core.engine.runner import run_simulation_with_reports
from raptor.core.fileio.rbd import load_rbd
from raptor.gui.ui.plots import (AoPlotWidget, multi_run_envelope,
                                 parse_ao_runs, parse_cap_runs)

REPO = pathlib.Path(__file__).parent.parent.parent
GOLDEN = REPO / "tests" / "golden"


@pytest.fixture(scope="module")
def qapp():
    return QApplication.instance() or QApplication([])


def test_parse_splits_runs_on_separator():
    lines = [
        "SimulationTime        Ao        Green     Yellow    Red       Interval",
        "10.000000             0.900000  0.600000  0.300000  0.100000  0.900000",
        "***End_of_Run_1***",
        "10.000000             0.800000  0.500000  0.300000  0.200000  0.800000",
        "100.000000            0.850000  0.550000  0.300000  0.150000",
    ]
    runs = parse_ao_runs(lines)
    assert len(runs) == 2, "End_of_Run marker must split runs"
    assert runs[0]["ao"] == [0.9]
    # Run 2: one interval row + one 5-column run-end row.
    assert runs[1]["ao"] == [0.8, 0.85]
    assert runs[1]["iv"] == [0.8, None]
    assert runs[1]["t"][-1] == pytest.approx(100.0)


def test_parse_real_engine_sink_round_trip():
    """The real engine's own sink output must parse into >=2 samples with
    G+Y+R == 1 on every row."""
    fixture = GOLDEN / "TwoBlockRedundancy" / "input.rbd"
    ao_lines: list[str] = []
    run_simulation_with_reports(load_rbd(str(fixture)), num_runs=2,
                                date="fixed", ao_sink=ao_lines)
    runs = parse_ao_runs(ao_lines)
    assert len(runs) == 2
    for run in runs:
        assert len(run["t"]) > 3, "interval process must sample repeatedly"
        for g, y, r in zip(run["g"], run["y"], run["r"]):
            # 6-decimal rounding: up to ~1.5e-6 drift across three columns
            assert g + y + r == pytest.approx(1.0, abs=2e-6)


def test_multi_run_envelope_brackets_average():
    fixture = GOLDEN / "TwoBlockRedundancy" / "input.rbd"
    ao_lines: list[str] = []
    run_simulation_with_reports(load_rbd(str(fixture)), num_runs=4,
                                date="fixed", ao_sink=ao_lines)
    times, mins, avgs, maxs = multi_run_envelope(parse_ao_runs(ao_lines))
    assert len(times) > 10 and len(times) == len(mins) == len(avgs) == len(maxs)
    for mn, av, mx in zip(mins, avgs, maxs):
        assert mn <= av <= mx


def test_widget_paints_all_modes(qapp):
    fixture = GOLDEN / "TwoBlockRedundancy" / "input.rbd"
    ao_lines: list[str] = []
    run_simulation_with_reports(load_rbd(str(fixture)), num_runs=2,
                                date="fixed", ao_sink=ao_lines)
    w = AoPlotWidget()
    w.set_data(parse_ao_runs(ao_lines))
    w.resize(400, 260)
    for mode in (AoPlotWidget.PER_TRIAL, AoPlotWidget.INTERVAL,
                 AoPlotWidget.MULTI_RUN):
        w.set_mode(mode)
        assert not w.grab().isNull()


def test_widget_empty_state(qapp):
    w = AoPlotWidget()
    w.resize(400, 260)
    assert not w.grab().isNull()  # paints the "no data" message, no crash


# ---------------------------------------------------------------------
# Capacity plot (manual Ch.14)
# ---------------------------------------------------------------------

def test_parse_cap_runs():
    lines = [
        "0.000000                   80         80",
        "25.000000                    0         80",
        "***End_of_Run_1***",
        "0.000000                   80         80",
        "30.000000                   55        80",
    ]
    runs = parse_cap_runs(lines)
    assert len(runs) == 2
    assert runs[0]["flow"] == [80.0, 0.0]
    assert runs[1]["cap"] == [80.0, 80.0]
    assert runs[1]["t"] == [0.0, 30.0]


def test_capacity_mode_real_engine(qapp):
    fixture = GOLDEN / "Capacity1" / "input.rbd"
    cap_lines: list[str] = []
    ao_lines: list[str] = []
    run_simulation_with_reports(load_rbd(str(fixture)), num_runs=2,
                                date="fixed", capacity_sink=cap_lines,
                                ao_sink=ao_lines)
    runs = parse_cap_runs(cap_lines)
    assert len(runs) == 2, "run markers must separate capacity series"
    for run in runs:
        assert len(run["t"]) > 3
        # Flow never exceeds achievable capacity.
        for fl, cp in zip(run["flow"], run["cap"]):
            assert fl <= cp + 1e-9
    from raptor.gui.ui.results import ResultsDock
    dock = ResultsDock()
    dock.set_results([["x"]], [], [], report_data=[],
                     ao_lines=ao_lines, cap_lines=cap_lines)
    w = dock._ao_plot
    w.set_run(0)
    w.set_mode(AoPlotWidget.CAPACITY)
    w.resize(400, 260)
    assert not w.grab().isNull()
    w.set_mode(AoPlotWidget.MULTI_CAPACITY)
    env = w._cap_envelope()
    times, fmin, favg, fmax, cmin, cavg, cmax = env
    assert times and len(times) == len(fmin) == len(favg) == len(fmax)
    assert all(a <= b <= c for a, b, c in zip(fmin, favg, fmax))
    assert all(a <= b <= c for a, b, c in zip(cmin, cavg, cmax))
    assert not w.grab().isNull()


# ---------------------------------------------------------------------
# Availability Convergence plot: cumulative mean versus number of runs,
# distinct from Multi-run Ao's convergence over simulation time within a
# fixed run count.
# ---------------------------------------------------------------------

def test_convergence_widget_paints_and_empty_state(qapp):
    w = AoPlotWidget()
    w.resize(400, 260)
    w.set_mode(AoPlotWidget.CONVERGENCE)
    assert not w.grab().isNull()   # "need at least 2 runs" message, no crash

    w.set_convergence_data([(1.0, 0.9), (2.0, 0.85), (3.0, 0.87)])
    assert not w.grab().isNull()


def test_update_convergence_data_is_cumulative_mean_in_run_order(qapp):
    from raptor.core.engine.reporting import RunReportData
    from raptor.core.engine.stats import RunningStat
    from raptor.gui.ui.results import ResultsDock

    def mk(run, avail):
        return RunReportData(
            rcond_assigned=False, stats_start_time=0.0,
            run_number=run, ending_sim_time=100.0, availability=avail,
            mtbde=None, mdt=None, reliability=0.0, conditional_reliability=0.0,
            num_system_failures=1, num_component_failures=2, mtbm=None,
            mtbmu=None, mtbms=None, mmt=None, mmtu=None, mmts=None,
            dependability=None, has_phases=False,
            green_percent=None, yellow_percent=None, red_percent=None,
            mission_green_percent=None, mission_yellow_percent=None,
            mission_red_percent=None, sys_fail_stat=RunningStat(),
            red_stat=RunningStat(), comp_fail_stat=RunningStat(),
            comp_repair_stat=RunningStat(), block_spares={},
        )

    dock = ResultsDock()
    dock.set_results([[], [], []], ["eor"], ["fin"],
                     report_data=[mk(1, 1.0), mk(2, 0.5), mk(3, 0.5)])
    pts = dock._ao_plot._convergence_pts
    assert pts == [(1.0, 1.0), (2.0, 0.75), (3.0, pytest.approx(2.0 / 3))]
    assert dock._ao_plot._convergence_metric_name == "Availability"


def test_convergence_uses_dependability_when_model_configured_for_it(qapp):
    """Must agree with the Overview card's own metric choice
    (`_weak_anal_type`) -- both reading `nodeAnalType`, falling back to
    Availability when Dependability isn't computed for this model."""
    from raptor.core.engine.reporting import RunReportData
    from raptor.core.engine.stats import RunningStat
    from raptor.gui.ui.results import ResultsDock

    def mk(run, avail, dep):
        return RunReportData(
            rcond_assigned=False, stats_start_time=0.0,
            run_number=run, ending_sim_time=100.0, availability=avail,
            mtbde=None, mdt=None, reliability=0.0, conditional_reliability=0.0,
            num_system_failures=1, num_component_failures=2, mtbm=None,
            mtbmu=None, mtbms=None, mmt=None, mmtu=None, mmts=None,
            dependability=dep, has_phases=dep is not None,
            green_percent=None, yellow_percent=None, red_percent=None,
            mission_green_percent=None, mission_yellow_percent=None,
            mission_red_percent=None, sys_fail_stat=RunningStat(),
            red_stat=RunningStat(), comp_fail_stat=RunningStat(),
            comp_repair_stat=RunningStat(), block_spares={},
        )

    dock = ResultsDock()
    dock._weak_anal_type = "Dependability"
    dock.set_results([[], []], ["eor"], ["fin"],
                     report_data=[mk(1, 0.9, 0.8), mk(2, 0.9, 0.6)])
    assert dock._ao_plot._convergence_metric_name == "Dependability"
    assert dock._ao_plot._convergence_pts == [(1.0, 0.8), (2.0, 0.7)]


def test_graph_mode_combo_disables_run_selector_for_convergence(qapp):
    from raptor.gui.ui.results import ResultsDock
    dock = ResultsDock()
    idx = dock._graph_mode.findText("Availability Convergence")
    assert idx >= 0
    dock._graph_mode.setCurrentIndex(idx)
    assert dock._ao_plot._mode == AoPlotWidget.CONVERGENCE
    assert not dock._graph_run.isEnabled()
