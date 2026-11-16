"""The Advisor and the attribution replay on a 1,000-block model.

Budgets are ratios and generous absolute times so they hold on a slow CI
machine; the point is to catch a return to work that grows with
(model size x log length), which made the replay take five times as long
as the run itself.
"""
from __future__ import annotations

import time

import pytest

from raptor.api import build
from raptor.api.advisor import ModelContext, list_rules
from raptor.api.insight.attribution import aggregate, attribute_trial
from raptor.api.progress import Cancelled
from raptor.core.engine.runner import run_simulation

pytestmark = pytest.mark.long

GROUPS, WIDTH = 250, 4


@pytest.fixture(scope="module")
def big():
    blocks, links, prev = [], [], "Start"
    nodes = [{"name": "Start", "type": 1}]
    for g in range(GROUPS):
        split, join = f"S{g}", f"J{g}"
        nodes += [{"name": split, "type": 2, "k": 1}, {"name": join, "type": 2, "k": 2}]
        links.append({"from": prev, "to": split})
        for w in range(WIDTH):
            name = f"B{g}_{w}"
            blocks.append({"name": name, "fail_dist": 4, "fail_params": [4000.0, 0.0],
                           "repair_dist": 7, "repair_params": [24.0, 6.0]})
            links += [{"from": split, "to": name}, {"from": name, "to": join}]
        prev = join
    nodes.append({"name": "End", "type": 3})
    links.append({"from": prev, "to": "End"})
    spec = {"blocks": blocks, "nodes": nodes, "links": links,
            "experiment": {"trials": 200, "termination_type": "TimeSim", "term_condition": 8760}}
    return build.build_from_spec(spec, auto_layout=False)


def test_advisor_rules_are_fast_on_a_thousand_blocks(big):
    ctx = ModelContext(big, validation=[])      # the validator has its own budget
    started = time.perf_counter()
    for info in list_rules("model"):
        list(info.func(ctx))
    assert time.perf_counter() - started < 1.0


def test_replay_costs_a_small_part_of_the_run(big):
    started = time.perf_counter()
    logs = run_simulation(big, 4)
    run_time = time.perf_counter() - started
    started = time.perf_counter()
    trials = [attribute_trial(lines) for lines in logs]
    replay_time = time.perf_counter() - started
    result = aggregate(trials)
    assert replay_time < 0.5 * run_time, (replay_time, run_time)
    assert result["runs"] == 4
    for trial in trials:
        assert sum(v.red_time for v in trial.initiated.values()) == pytest.approx(trial.red_time)


def test_replay_reports_progress_and_can_be_cancelled(big):
    import threading
    from raptor.api.insight.attribution import attribute_logs
    logs = run_simulation(big, 3)
    seen = []
    attribute_logs(logs, progress=seen.append)
    assert [(p.done, p.total) for p in seen] == [(1, 3), (2, 3), (3, 3)]
    assert seen[0].stage == "attribute" and "caused the downtime" in seen[0].text()
    stop = threading.Event()
    stop.set()
    with pytest.raises(Cancelled):
        attribute_logs(logs, abort=stop)
