"""`Scheduler.step` keeps an index of the instant instead of re-sorting it.

The old `step` popped every entry of the current instant, sorted them by
`(tier, owner-group rank, seq)`, processed the first and pushed the rest back:
O(k log k) per entry, O(k^2 log k) per instant. A batch fast path avoided that
for plain instants, but it fell back to `step` whenever an owner had several
records at the instant (a block with its own spares) or an interrupt had fired
earlier in the run (dependencies, pools, maintenance). On a 3,470-block model
with custom sparing the t=0 instant alone took about 35 s.

`_ReferenceScheduler` below is the old `step`, verbatim. These tests pin that
the indexed `step` processes the same entries in the same order, and reports
the same unfinished instants through `next_time`, on every golden fixture and
on a generated model built to stress the index.
"""
from __future__ import annotations

import copy
import glob
import heapq
import os
import pathlib

import pytest

from raptor.api.build import add_spare_pool, configure_maintenance, configure_sparing, configure_system, update_element
from raptor.api.workspace import Workspace
from raptor.core import scheduler as sched_mod
from raptor.core.engine import kickoff
from raptor.core.engine.runner import run_simulation
from raptor.core.fileio.rbd import load_rbd
from raptor.core.scheduler import Scheduler, _validate_time, choose_next

GOLD = pathlib.Path(__file__).resolve().parents[1] / "golden"
FIXTURES = sorted(os.path.basename(os.path.dirname(p))
                  for p in glob.glob(str(GOLD / "*" / "input.rbd")))


class _ReferenceScheduler(Scheduler):
    """The pre-index `step`: pop the instant, sort it, process one, push the rest back."""

    def step(self, end_time: float) -> bool:
        _validate_time(end_time, "step end_time")
        if not self._calendar or self._calendar[0][0] > end_time:
            return False
        tie_time = self._calendar[0][0]
        group = []
        while self._calendar and self._calendar[0][0] == tie_time:
            entry = heapq.heappop(self._calendar)[2]
            if self._generations.get(entry.handle_id) != entry.generation:
                continue
            group.append(entry)
        if not group:
            return True
        if self.cn_active_time == tie_time:
            group.sort(key=lambda e: (self._gc_rank(e), e.seq))
            chosen = choose_next(group, self._identity)
            entry = group.pop(next(i for i, e in enumerate(group) if e is chosen))
        else:
            tiers = sched_mod._TIER_CN_OFF if self.cn_off_time == tie_time else sched_mod._TIER
            group.sort(key=lambda e: (tiers.get(self._owners.get(e.handle_id), 1), self._gc_rank(e), e.seq))
            entry = group.pop(0)
        self._gc_select(entry)
        for e in group:
            heapq.heappush(self._calendar, (e.time, e.seq, e))
        self.time = tie_time
        self._process(entry)
        return True


def _both(monkeypatch, rbd_factory, num_runs):
    indexed = run_simulation(rbd_factory(), num_runs=num_runs)
    with monkeypatch.context() as m:
        m.setattr(kickoff, "Scheduler", _ReferenceScheduler)
        reference = run_simulation(rbd_factory(), num_runs=num_runs)
    return indexed, reference


@pytest.mark.parametrize("fixture", FIXTURES)
def test_every_golden_fixture_logs_the_same_as_the_full_sort(fixture, monkeypatch):
    path = str(GOLD / fixture / "input.rbd")
    indexed, reference = _both(monkeypatch, lambda: load_rbd(path), 2)
    assert indexed == reference


def _stress_model(tmp_path, n=60):
    """A series model where a third of the blocks hold their own spares (two
    records per owner at t=0), a quarter take maintenance and a fifth follow
    an earlier block (interrupts in most instants)."""
    ws = Workspace(str(tmp_path))
    mid = ws.create_model("m", template="series", n=n)["model_id"]
    add_spare_pool(ws, mid, "shared", stock=1, arrive_every=50.0, rso=1)
    for i in range(1, n + 1):
        sel = f"Block:{i}"
        update_element(ws, mid, sel, {"fail_params[0]": 60.0, "repair_params[0]": 3.0})
        if i % 3 == 0:
            configure_sparing(ws, mid, sel, mode="custom", stock=1, rso=1, new_spares=1, arrive=40.0)
        elif i % 3 == 1:
            configure_sparing(ws, mid, sel, mode="pool", pool_name="shared")
        if i % 4 == 0:
            configure_maintenance(ws, mid, sel, pm_freq=100.0, pm_duration_dist=19, pm_params=[2.0])
        if i % 5 == 0:
            update_element(ws, mid, sel, {"dep_type": "RBDBlock", "dep_num": i - 2})
    configure_system(ws, mid, run_length=400.0)
    return ws.get(mid).rbd


def test_a_model_with_own_spares_maintenance_and_dependencies_logs_the_same(tmp_path, monkeypatch):
    rbd = _stress_model(tmp_path)
    indexed, reference = _both(monkeypatch, lambda: copy.deepcopy(rbd), 3)
    assert indexed == reference
    assert any("Dep" in line or "PM" in line for line in indexed[0])


def test_an_instant_costs_a_bounded_number_of_key_computations(tmp_path, monkeypatch):
    """Each entry is keyed once on arrival and again only when its owner group
    changes, so key computations grow with the entries, not their square."""
    rbd = _stress_model(tmp_path, n=150)
    counts = {"keys": 0, "steps": 0}
    real_push, real_step = Scheduler._inst_push, Scheduler.step

    def push(self, *a, **k):
        counts["keys"] += 1
        return real_push(self, *a, **k)

    def step(self, *a, **k):
        counts["steps"] += 1
        return real_step(self, *a, **k)

    monkeypatch.setattr(Scheduler, "_inst_push", push)
    monkeypatch.setattr(Scheduler, "step", step)
    run_simulation(rbd, num_runs=1)
    assert counts["steps"] > 300
    # The full sort keyed every pending entry on every step: hundreds per step at t=0 here.
    assert counts["keys"] < 4 * counts["steps"], counts
