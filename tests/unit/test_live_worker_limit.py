"""The number of busy workers can change while a batch runs (#11).

Workers take a "run permit" around every run; the parent holds some permits
back to lower the number of cores in use and returns them to raise it again.
Only how many runs execute at once changes -- every chunk still runs start to
finish in its own random-number stream -- so the numbers must not move.
"""
from __future__ import annotations

import multiprocessing as mp
import pathlib

import pytest

from raptor.core.engine import runner
from raptor.core.engine.runner import _set_concurrency, run_simulation
from raptor.core.fileio.rbd import load_rbd

GOLD = pathlib.Path(__file__).resolve().parents[1] / "golden"
CTX = mp.get_context(runner.MP_CONTEXT)


def _free_permits(sem) -> int:
    """How many permits the semaphore will give right now."""
    got = 0
    while sem.acquire(False):
        got += 1
    for _ in range(got):
        sem.release()
    return got


def test_lowering_the_limit_holds_permits_back():
    sem = CTX.Semaphore(4)
    held = _set_concurrency(sem, 0, 4, 2)
    assert held == 2 and _free_permits(sem) == 2


def test_raising_the_limit_returns_them():
    sem = CTX.Semaphore(4)
    held = _set_concurrency(sem, 0, 4, 1)
    assert held == 3
    held = _set_concurrency(sem, held, 4, 3)
    assert held == 1 and _free_permits(sem) == 3
    held = _set_concurrency(sem, held, 4, 4)
    assert held == 0 and _free_permits(sem) == 4


@pytest.mark.parametrize("wanted, busy", [(0, 1), (-5, 1), (1, 1), (4, 4), (99, 4)])
def test_the_limit_is_kept_between_one_and_the_pool(wanted, busy):
    sem = CTX.Semaphore(4)
    _set_concurrency(sem, 0, 4, wanted)
    assert _free_permits(sem) == busy


def test_a_permit_that_is_not_free_yet_is_taken_when_it_is():
    """Every permit is in use: the reduction is partial now, complete later."""
    sem = CTX.Semaphore(2)
    assert sem.acquire(False) and sem.acquire(False)          # workers mid-run
    held = _set_concurrency(sem, 0, 2, 1)
    assert held == 0                                          # nothing free yet
    sem.release()                                             # one run finishes
    held = _set_concurrency(sem, held, 2, 1)
    assert held == 1


class _Flag:
    def __init__(self, on): self.on = on
    def is_set(self): return self.on


class _Refusing:
    def acquire(self, timeout=None): return False
    def release(self): raise AssertionError("released a permit it never took")


def test_a_worker_without_permits_runs_as_before(monkeypatch):
    monkeypatch.setattr(runner, "_PERMITS", None)
    assert runner._take_run_permit() is True
    runner._give_run_permit()                                 # no error


def test_a_worker_waiting_for_a_permit_gives_up_on_stop(monkeypatch):
    monkeypatch.setattr(runner, "_PERMITS", _Refusing())
    monkeypatch.setattr(runner, "_STOP_FLAG", _Flag(True))
    assert runner._take_run_permit() is False


@pytest.fixture(scope="module")
def motor():
    return load_rbd(str(GOLD / "MotorDemo" / "input.rbd"))


def test_changing_the_limit_mid_batch_does_not_change_the_numbers(motor):
    steady = run_simulation(motor, num_runs=24, num_workers=4)
    asked = []

    def swinging():
        """Counted in polls, not seconds: a small model finishes too fast for
        a clock to swing the limit during the batch."""
        n = len(asked)
        asked.append(n)
        return 4 if n < 2 else (1 if n < 6 else 2)

    steered = run_simulation(motor, num_runs=24, num_workers=4, worker_limit=swinging)
    assert len(asked) > 6, "the batch ended before the limit swung"
    assert steered == steady


def test_a_limit_of_one_still_finishes_the_batch(motor):
    out = run_simulation(motor, num_runs=12, num_workers=3, worker_limit=lambda: 1)
    assert len(out) == 12


def test_stop_still_works_while_the_limit_is_low(motor):
    from raptor.core.engine.runner import RunAborted
    seen = []

    def progress(done, total):
        seen.append(done)
        if done >= 3:
            raise RunAborted

    out = run_simulation(motor, num_runs=200, num_workers=3,
                         worker_limit=lambda: 1, progress=progress)
    assert 1 <= len(out) < 200
