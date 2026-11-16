"""Standalone capacity contributions at the plan's 400 / 4,000 element sizes.

The Failure Effects view computes one element's own capacity contribution.  A
full node-status pass per element was quadratic on a parallel bank (4000
elements took 75 s); the one-link capacity path brings 4000 to a few seconds.
The budgets are generous absolute times so a slow CI machine still passes.
"""
from __future__ import annotations

import threading
import time

import pytest

from raptor.api import build
from raptor.api.insight.capacity_attribution import (
    capacity_runs, standalone_capacity,
)
from raptor.api.progress import Cancelled

pytestmark = pytest.mark.long

#: The plan's WP-2.8 budgets.
BUDGET_400_S = 1.0
BUDGET_4000_S = 30.0


def _spec(count: int) -> dict:
    blocks = [
        {"name": f"B{i}", "id": i + 1, "fail_dist": "Fixed",
         "fail_params": [10.0, 0.0, 0.0], "repair_dist": "Fixed",
         "repair_params": [5.0, 0.0, 0.0]}
        for i in range(count)
    ]
    nodes = [{"name": "Start", "type": 1}, {"name": "S", "type": 2, "k": 1},
             {"name": "End", "type": 3, "full_flow": 0}]
    links = [{"from": "Start", "to": "S"}]
    for i in range(count):
        links += [{"from": "S", "to": f"B{i}"},
                  {"from": f"B{i}", "to": "End", "nom_flow": 1, "max_flow": 1}]
    return {"blocks": blocks, "nodes": nodes, "links": links,
            "system": {"capacity": True, "flow_generated": count},
            "experiment": {"trials": 1, "termination_type": "TimeSim",
                           "term_condition": 100.0}}


@pytest.mark.parametrize("count,budget", [(400, BUDGET_400_S),
                                          (4000, BUDGET_4000_S)])
def test_standalone_capacity_is_within_budget(count, budget):
    rbd = build.build_from_spec(_spec(count), auto_layout=False)
    started = time.perf_counter()
    values = standalone_capacity(rbd)
    elapsed = time.perf_counter() - started
    assert len(values) == count
    assert all(value > 0 for value in values.values())
    assert elapsed < budget, (count, elapsed, budget)


def test_a_cancelled_capacity_run_leaves_nothing_half_done():
    rbd = build.build_from_spec(_spec(20), auto_layout=False)
    stop = threading.Event()
    stop.set()
    with pytest.raises(Cancelled):
        standalone_capacity(rbd, abort=stop)
    stop2 = threading.Event()
    stop2.set()
    lines = ["SimulationTime        Flow       Capacity", "0.000000              20         20",
             "*" * 30 + "End_of_Run_1" + "*" * 30]
    with pytest.raises(Cancelled):
        capacity_runs(rbd, lines, abort=stop2)
