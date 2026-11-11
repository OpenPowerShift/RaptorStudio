"""The size-on-disk estimate, the disk-backed availability plot and the
formatter fast path behind it."""
from __future__ import annotations

import decimal
import random
from pathlib import Path

import pytest

from raptor.api.run import run as api_run, section_path
from raptor.api.workspace import Workspace
from raptor.core import run_experiment as rx
from raptor.core.engine.report_format import write_real
from raptor.core.result_streams import ResultStream
from raptor.core.storage_estimate import (AO_ROW_BYTES, cycles_per_time,
                                          estimate_storage, experiment_of,
                                          format_bytes)

REPO = Path(__file__).resolve().parent.parent.parent
GOLDEN = REPO / "tests" / "golden"


def _experiment(**over):
    experiment = rx.defaults()
    experiment.update({"trials": 100, "termination_type": "TimeSim",
                       "term_condition": 175200.0})
    experiment["recording"].update(over)
    return experiment


def test_ao_plot_size_follows_runs_length_and_interval():
    est = estimate_storage(_experiment(ao_plot=True, event_log=False))
    item = est.item("ao_plot")
    assert item is not None and item.bytes is not None
    # 17,520 samples a run, 100 runs, 71 bytes a row: about 124 MB.
    assert item.bytes == pytest.approx(100 * 17520 * AO_ROW_BYTES, rel=0.01)
    assert not item.approximate
    halved = estimate_storage(_experiment(ao_plot=True, event_log=False,
                                          ao_interval=20.0))
    assert halved.total_bytes == pytest.approx(est.total_bytes / 2, rel=0.01)


def test_nothing_ticked_writes_nothing():
    est = estimate_storage(_experiment(event_log=False))
    assert est.items == [] and est.total_bytes == 0
    assert "nothing" in est.summary().lower()


def test_a_run_without_a_fixed_length_has_no_figure():
    experiment = _experiment(ao_plot=True, event_log=False)
    experiment["termination_type"] = "FailSim"
    est = estimate_storage(experiment)
    assert est.unknown and est.item("ao_plot").bytes is None
    assert "depends" in est.summary()


def test_event_log_size_uses_the_blocks_failure_rate():
    slow = estimate_storage(_experiment(), cycle_rate=1 / 1000.0)
    fast = estimate_storage(_experiment(), cycle_rate=1 / 10.0)
    assert slow.item("event_log").approximate
    assert fast.total_bytes > 50 * slow.total_bytes
    assert slow.summary().startswith("About")


def test_cost_plot_needs_the_cost_analysis():
    experiment = _experiment(event_log=False, cost_plot=True)
    experiment["analyses"] = []
    assert estimate_storage(experiment, elements=10).items == []
    experiment["analyses"] = ["cost"]
    assert estimate_storage(experiment, elements=10).item("cost_plot")


def test_cycle_rate_reads_block_rows_without_loading_the_engine():
    from raptor.core.fileio.rbd import load_rbd
    rbd = load_rbd(str(GOLDEN / "MotorDemo" / "input.rbd"))
    rate, blocks = cycles_per_time(rbd)
    assert blocks > 0 and rate > 0.0


def test_format_bytes():
    assert format_bytes(532) == "532 bytes"
    assert format_bytes(14_300) == "14 KB"
    assert format_bytes(1_240_000_000) == "1.2 GB"


def test_estimate_matches_what_a_run_writes(tmp_path):
    ws = Workspace(tmp_path)
    mid = ws.open_model(GOLDEN / "MotorDemo" / "input.rbd")["model_id"]
    result = api_run(ws, mid, num_runs=3, confirm_long_run=True,
                     availability_plot=True)
    ao = section_path(ws.root / "runs" / result.run_id, "availability_plot")
    assert ao is not None and ao.is_file()
    experiment = experiment_of(ws.get(mid).rbd)
    experiment["trials"] = 3
    est = estimate_storage(experiment, recording={"ao_plot": True,
                                                  "event_log": False})
    assert est.item("ao_plot").bytes == pytest.approx(
        ao.stat().st_size, rel=0.05, abs=200)
    # The plot was spooled and moved, not left behind.
    assert not list(ws.root.glob(".spool-*"))
    lines = ao.read_text().splitlines()
    assert lines[0].startswith("SimulationTime")
    assert sum("End_of_Run_" in line for line in lines) == 3


def test_parallel_run_spools_worker_rows_to_disk(tmp_path):
    ws = Workspace(tmp_path)
    mid = ws.open_model(GOLDEN / "MotorDemo" / "input.rbd")["model_id"]
    result = api_run(ws, mid, num_runs=4, workers=2, confirm_long_run=True,
                     availability_plot=True)
    ao = section_path(ws.root / "runs" / result.run_id, "availability_plot")
    lines = ao.read_text().splitlines()
    assert lines[0].startswith("SimulationTime")
    assert sum(line.startswith("SimulationTime") for line in lines) == 1
    assert sum("End_of_Run_" in line for line in lines) == 4
    assert not list(ws.root.glob(".spool-*"))
    # Same rows as a one-core batch: a chunk used to lose its first sample.
    single = api_run(ws, mid, num_runs=4, workers=1, confirm_long_run=True,
                     availability_plot=True)
    one = section_path(ws.root / "runs" / single.run_id, "availability_plot")
    assert len(one.read_text().splitlines()) == len(lines)


def test_parallel_run_into_a_plain_list_keeps_every_row():
    from raptor.core.engine.runner import run_simulation_with_reports
    from raptor.core.fileio.rbd import load_rbd
    rbd = load_rbd(str(GOLDEN / "MotorDemo" / "input.rbd"))
    seq: list[str] = []
    par: list[str] = []
    run_simulation_with_reports(rbd, num_runs=4, ao_sink=seq)
    run_simulation_with_reports(rbd, num_runs=4, ao_sink=par, num_workers=2)
    assert par[0] == seq[0] and par[0].startswith("SimulationTime")
    assert len(par) == len(seq)


def test_result_stream_merges_a_file_and_becomes_an_artefact(tmp_path):
    part = ResultStream("part", directory=str(tmp_path))
    part.extend(["header", "a", "b"])
    path = part.detach()
    main = ResultStream("ao", directory=str(tmp_path))
    main.append("header")
    main.extend_from_file(path, skip=1)   # skip a part's own header
    assert len(main) == 3 and main.read_all_lines() == ["header", "a", "b"]
    assert main.size_bytes() == len("header\na\nb\n")
    target = tmp_path / "kept.txt"
    main.move_to(str(target))
    assert target.read_text() == "header\na\nb\n"
    main.append("dropped")              # closed: ignored, not an error
    assert target.read_text() == "header\na\nb\n"


def _reference(val: float, dec: int) -> str:
    q = decimal.Decimal(val).quantize(decimal.Decimal(1).scaleb(-dec),
                                      rounding=decimal.ROUND_HALF_UP)
    return f"{q:.{dec}f}"


def test_write_real_fast_path_matches_half_away_rounding():
    # Exact binary halves are where Python's own format rounds to even.
    assert write_real(0.1640625, 6) == "0.164063"
    assert write_real(0.5, 0) == "1" and write_real(2.5, 0) == "3"
    assert write_real(-0.5, 0) == "-1"
    rng = random.Random(7)
    values = [k / 128 for k in range(-300, 300)]
    values += [k / 1024 for k in range(-2000, 2000)]
    values += [rng.random() for _ in range(20000)]
    values += [rng.uniform(-1e6, 1e6) for _ in range(5000)]
    values += [round(rng.random(), 7) for _ in range(5000)]
    for value in values:
        for dec in (0, 2, 6, 9):
            assert write_real(value, dec) == _reference(value, dec), (value, dec)


def test_rough_run_time_follows_failures_not_run_hours():
    from raptor.core.storage_estimate import rough_run_seconds
    # 200 trials of a year, about 100 failures a trial: seconds.
    year = rx.defaults()
    year.update({"trials": 200, "termination_type": "TimeSim",
                 "term_condition": 8760.0})
    quick = rough_run_seconds(year, cycle_rate=100 / 8760.0, elements=40)
    assert quick is not None and quick < 30
    # The same run-hours with blocks that fail a hundred times as often.
    busy = rough_run_seconds(year, cycle_rate=10_000 / 8760.0, elements=40)
    assert busy is not None and busy > 10 * quick
    # More cores shorten it; a densely sampled plot lengthens it.
    assert rough_run_seconds(year, cycle_rate=1.0, cores=8) < \
        rough_run_seconds(year, cycle_rate=1.0, cores=1)
    plotted = dict(year, recording=dict(year["recording"], ao_plot=True,
                                        ao_interval=0.01))
    assert rough_run_seconds(plotted, cycle_rate=100 / 8760.0) > 5 * quick
    # No figure without model facts, or for a run with no fixed length.
    assert rough_run_seconds(year, cycle_rate=None) is None
    assert rough_run_seconds(dict(year, termination_type="FailSim"),
                             cycle_rate=1.0) is None


def test_a_run_records_plots_only_when_the_model_or_the_caller_asks(tmp_path):
    """The command line and MCP used to sample the availability plot and the
    interim cost report on every run; the GUI only when they were ticked.
    Each is a timer inside the simulation, so the two could order
    same-instant events differently, and every run paid for the sampling."""
    ws = Workspace(tmp_path)
    mid = ws.open_model(GOLDEN / "MotorDemo" / "input.rbd")["model_id"]
    plain = api_run(ws, mid, num_runs=2, confirm_long_run=True)
    run_dir = ws.root / "runs" / plain.run_id
    assert not section_path(run_dir, "availability_plot").exists()
    assert not section_path(run_dir, "cost").exists()
    asked = api_run(ws, mid, num_runs=2, confirm_long_run=True,
                    availability_plot=True, interim_cost=True)
    run_dir = ws.root / "runs" / asked.run_id
    assert section_path(run_dir, "availability_plot").is_file()
    assert section_path(run_dir, "cost").is_file()
    # The model's own Recording setting is what a plain run follows.
    rbd = ws.get(mid).rbd
    experiment = experiment_of(rbd)
    experiment["recording"]["ao_plot"] = True
    rbd.experiment = experiment
    ticked = api_run(ws, mid, num_runs=2, confirm_long_run=True)
    assert section_path(ws.root / "runs" / ticked.run_id,
                        "availability_plot").is_file()
    off = api_run(ws, mid, num_runs=2, confirm_long_run=True,
                  availability_plot=False)
    assert not section_path(ws.root / "runs" / off.run_id,
                            "availability_plot").exists()


def test_the_engine_run_is_the_same_from_the_api_as_without_sinks(tmp_path):
    """Parity: a plain API run is the engine run with no plot sinks, which
    is what the GUI does with nothing ticked."""
    from raptor.core.engine.runner import run_simulation_with_reports
    from raptor.core.fileio.rbd import load_rbd
    ws = Workspace(tmp_path)
    mid = ws.open_model(GOLDEN / "MotorDemo" / "input.rbd")["model_id"]
    api = api_run(ws, mid, num_runs=3, confirm_long_run=True)
    _logs, eor, _final = run_simulation_with_reports(
        load_rbd(str(GOLDEN / "MotorDemo" / "input.rbd")), num_runs=3,
        report_sink=[], collect_logs=False)
    assert api.end_of_run.splitlines()[2:] == "\n".join(eor).splitlines()[2:]
