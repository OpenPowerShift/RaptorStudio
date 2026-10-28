"""Tests for Ao / capacity series parsing and decimation.

Covers `parse_ao_text` / `parse_capacity_text` on the real engine formats,
`parse_ao_runs` splitting on the `End_of_Run_N` markers, min/mean/max
envelopes, and LTTB decimation keeping the first and last samples without
exceeding the requested point count. Uses the checked-in MotorDemo
availability plot under `tests/golden/`.
"""
from __future__ import annotations

from pathlib import Path

import pytest

from raptor.api.series import (
    capacity_envelope, decimate, mean_envelope, parse_and_decimate,
    parse_ao_runs, parse_ao_text, parse_capacity_runs, parse_capacity_text,
)

REPO = Path(__file__).resolve().parent.parent.parent
GOLDEN = REPO / "tests" / "golden"
MOTOR_AO = (
    GOLDEN / "MotorDemo" / "legacy"
    / "rpt_availability_plot_MotorDemo.txt"
)

AO_TEXT = (
    "SimulationTime        Ao        Green     Yellow    Red       Interval\n"
    "10.000000             0.900000  0.600000  0.300000  0.100000  0.900000\n"
    "100.000000            0.850000  0.550000  0.300000  0.150000\n"
    "******************************End_of_Run_1******************************\n"
    "10.000000             0.800000  0.500000  0.300000  0.200000  0.800000\n"
    "100.000000            0.700000  0.500000  0.200000  0.100000\n"
)


class TestParseAoTEXT:
    def test_real_format_header_and_columns(self):
        data = parse_ao_text(AO_TEXT)
        assert data["t"] == [10.0, 100.0, 10.0, 100.0]
        assert data["values"] == [0.9, 0.85, 0.8, 0.7]

    def test_motor_demo_plot(self):
        data = parse_ao_text(MOTOR_AO.read_text())
        assert len(data["t"]) == 100
        assert data["t"][0] == 10.0
        assert data["t"][-1] == 36.0


class TestParseAoRuns:
    def test_splits_on_end_of_run_marker(self):
        runs = parse_ao_runs(AO_TEXT)
        assert len(runs) == 2
        assert runs[0]["t"] == [10.0, 100.0]
        assert runs[1]["values"] == [0.8, 0.7]

    def test_motor_demo_has_25_runs_of_four(self):
        runs = parse_ao_runs(MOTOR_AO.read_text())
        assert len(runs) == 25
        assert all(len(run["t"]) == 4 for run in runs)
        # Every run shares the sampling cadence.
        assert all(run["t"] == [10.0, 20.0, 30.0, 36.0] for run in runs)


class TestMeanEnvelope:
    def test_brackets_mean(self):
        runs = parse_ao_runs(AO_TEXT)
        env = mean_envelope(runs)
        assert len(env["t"]) == len(env["mean"]) == len(env["min"]) \
            == len(env["max"])
        for low, mean, high in zip(env["min"], env["mean"], env["max"]):
            assert low <= mean <= high

    def test_motor_demo_envelope(self):
        env = mean_envelope(parse_ao_runs(MOTOR_AO.read_text()))
        assert env["t"][0] == 0.0
        assert env["t"][-1] == 36.0
        assert 0.0 <= env["min"][-1] <= env["max"][-1] <= 1.0


class TestDecimate:
    def test_noop_when_short_enough(self):
        data = {"t": [0.0, 1.0, 2.0], "values": [0.0, 1.0, 0.5]}
        out = decimate(data, 5)
        assert out == data

    def test_keeps_first_and_last_and_caps_length(self):
        data = {"t": [float(i) for i in range(1000)],
                "values": [float(i % 7) for i in range(1000)]}
        out = decimate(data, 100)
        assert len(out["t"]) <= 100
        assert out["t"][0] == data["t"][0]
        assert out["t"][-1] == data["t"][-1]
        assert out["values"][0] == data["values"][0]
        assert out["values"][-1] == data["values"][-1]

    def test_small_n_never_exceeds_request(self):
        data = {"t": [float(i) for i in range(10)],
                "values": [float(i) for i in range(10)]}
        for n in (1, 2, 3):
            out = decimate(data, n)
            assert len(out["t"]) <= n
            assert out["t"][0] == 0.0
            if n >= 2:
                assert out["t"][-1] == 9.0

    def test_motor_demo_to_100_points(self):
        """Decimating the MotorDemo plot to 100 points keeps both ends."""
        data = parse_ao_text(MOTOR_AO.read_text())
        out = decimate(data, 100)
        assert len(out["t"]) <= 100
        assert out["t"][0] == 10.0
        assert out["t"][-1] == 36.0

    def test_keeps_change_points_of_a_steppy_series(self):
        """A long, mostly-flat series keeps every step when they fit."""
        t = [float(i) for i in range(1000)]
        v = [1.0] * 1000
        for i in range(300, 700):
            v[i] = 2.0
        for i in range(700, 1000):
            v[i] = 3.0
        out = decimate({"t": t, "values": v}, 10)
        assert len(out["t"]) <= 10
        assert 300.0 in out["t"]
        assert 700.0 in out["t"]

    def test_capacity_envelope_then_decimate_keeps_steps(self):
        """The capacity mean path must not flatten the change-points."""
        runs = [
            {"t": [0.0, 100.0, 200.0], "values": [4.0, 2.0, 4.0]},
            {"t": [0.0, 100.0, 200.0], "values": [4.0, 2.0, 4.0]},
        ]
        env = capacity_envelope(runs)
        out = decimate({"t": env["t"], "values": env["mean"]}, 10)
        assert 100.0 in out["t"]
        assert len(out["values"]) == len(out["t"])


class TestParseCapacityText:
    def test_basic(self):
        text = (
            "0.000000                   80         80\n"
            "25.000000                    0         80\n"
        )
        data = parse_capacity_text(text)
        assert data["t"] == [0.0, 25.0]
        assert data["values"] == [80.0, 80.0]

    def test_ignores_headers_and_markers(self):
        text = (
            "SimulationTime   flow   capacity\n"
            "0.000000  80  80\n"
            "***End_of_Run_1***\n"
            "0.000000  80  80\n"
        )
        data = parse_capacity_text(text)
        assert data["values"] == [80.0, 80.0]


class TestParseCapacityRuns:
    """Capacity parser keeps per-run time, flow AND capacity."""

    def test_splits_runs_and_columns(self):
        text = (
            "SimulationTime        Flow       Capacity\n"
            "0.000000              2050       2050\n"
            "13.901797             1550       1550\n"
            "******************************End_of_Run_1******************************\n"
            "0.000000              4          999999999\n"
            "******************************End_of_Run_2******************************\n"
        )
        runs = parse_capacity_runs(text)
        assert len(runs) == 2
        assert runs[0]["t"] == [0.0, 13.901797]
        assert runs[0]["flow"] == [2050.0, 1550.0]
        assert runs[0]["capacity"] == [2050.0, 1550.0]
        assert runs[1]["t"] == [0.0]
        assert runs[1]["flow"] == [4.0]
        assert runs[1]["capacity"] == [999999999.0]

    def test_ignores_header_and_blank_lines(self):
        text = (
            "SimulationTime   flow   capacity\n"
            "\n"
            "0.0  4  4\n"
            "1.0  3  3\n"
        )
        runs = parse_capacity_runs(text)
        assert len(runs) == 1
        assert runs[0]["t"] == [0.0, 1.0]


class TestCapacityEnvelope:
    """Step-like capacity runs keep their trajectory across runs.

    The envelope horizon is the maximum last sample across runs, and a run
    that ends early holds its final value; a run with a single sample at
    t=0 therefore does not collapse the envelope to one point.
    """

    def test_single_sample_run_does_not_collapse(self):
        runs = [
            {"t": [0.0], "values": [4.0]},
            {"t": [0.0, 10.0, 20.0], "values": [4.0, 2.0, 4.0]},
        ]
        env = capacity_envelope(runs)
        assert env["t"] == [0.0, 10.0, 20.0]
        # t=10: run 1 holds its last value (4), run 2 is 2 -> mean 3.
        assert env["mean"][1] == 3.0
        assert env["min"][1] == 2.0
        assert env["max"][1] == 4.0

    def test_empty(self):
        env = capacity_envelope([])
        assert env == {"t": [], "mean": [], "min": [], "max": []}


class TestParseAndDecimate:
    def test_ao(self):
        out = parse_and_decimate(AO_TEXT, n=2, series_type="ao")
        assert out["t"] == [10.0, 100.0]

    def test_capacity(self):
        text = "0.0 10 20\n1.0 5 20\n"
        out = parse_and_decimate(text, n=10, series_type="capacity")
        assert out["values"] == [20.0, 20.0]


@pytest.mark.parametrize("n", [1, 2, 3, 50, 100])
def test_decimate_length_invariant(n):
    data = parse_ao_text(MOTOR_AO.read_text())
    out = decimate(data, n)
    assert len(out["t"]) <= n
    assert out["t"][0] == data["t"][0]
    if n >= 2:
        assert out["t"][-1] == data["t"][-1]