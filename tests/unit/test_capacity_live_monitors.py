"""`touch_capacity_monitors` skips the nodes outside the capacity group.

Capacity analysis assigns `NodeFlow`/`NodeCapacity` only for the capacity
node group and the start and end nodes; every other node holds 0 all run.
Assigning 0 again to a monitor that holds 0 adds exactly +0.0 to its integral,
so once each monitor has had its first assignment after a reset, only the live
nodes are touched. These tests pin that the reports are unchanged.
"""
from __future__ import annotations

import glob
import os
import pathlib

import pytest
from types import SimpleNamespace

from raptor.core.engine import capacity, kickoff
from raptor.core.engine.runner import run_simulation_with_reports
from raptor.core.engine.stats import update_many
from raptor.core.fileio.rbd import load_rbd

GOLD = pathlib.Path(__file__).resolve().parents[1] / "golden"


def _capacity_fixtures():
    out = []
    for p in sorted(glob.glob(str(GOLD / "*" / "input.rbd"))):
        rows = load_rbd(p).sections["SYSTEM_DATA"].rows
        if rows and str(rows[0].get("capacityAnalysis", "0")).strip() in ("1", "1.0"):
            out.append(os.path.basename(os.path.dirname(p)))
    return out


FIXTURES = _capacity_fixtures()


def _touch_every_monitor(state, sim_time):
    update_many(state.flow_stats, state.node_flow, sim_time)
    update_many(state.cap_stats, state.node_capacity, sim_time)


def _run(path):
    capacity_lines: list[str] = []
    logs, end_of_run, final = run_simulation_with_reports(
        load_rbd(path), num_runs=2, date="fixed", capacity_sink=capacity_lines)
    return logs, end_of_run, final, capacity_lines


def test_there_are_capacity_fixtures():
    assert len(FIXTURES) > 10


@pytest.mark.parametrize("fixture", FIXTURES)
def test_reports_are_the_same_as_touching_every_monitor(fixture, monkeypatch):
    path = str(GOLD / fixture / "input.rbd")
    live = _run(path)
    monkeypatch.setattr(capacity, "touch_capacity_monitors", _touch_every_monitor)
    monkeypatch.setattr(kickoff, "touch_capacity_monitors", _touch_every_monitor)
    every = _run(path)
    assert live == every


def test_only_live_nodes_are_touched_after_the_first_pass():
    state = capacity.CapacityState()
    nodes = {i: SimpleNamespace(id=i) for i in range(1, 7)}
    capacity.reset_for_new_run(state, nodes, 0.0)
    state.live_ids = [1, 2]
    capacity.touch_capacity_monitors(state, 1.0)      # first pass: every monitor
    assert all(m._epoch == 1.0 for m in state.flow_stats.values())
    assert all(m.minimum == 0 == m.maximum for m in state.flow_stats.values())
    state.node_flow[1] = 5
    capacity.touch_capacity_monitors(state, 2.0)
    assert state.flow_stats[1]._epoch == 2.0 and state.flow_stats[1].value == 5
    assert state.flow_stats[6]._epoch == 1.0          # left alone: holds 0
    assert state.flow_stats[6].mean_at(3.0) == 0.0
    capacity.reset_for_new_run(state, nodes, 3.0)
    assert state.monitors_fresh
