"""A run can be cancelled, or time out, while the model is still being set up.

Set-up can take minutes on a large model (#15). Abort and the timeout were
only checked between replications, so Cancel did nothing until set-up ended.
"""
from __future__ import annotations

import threading
import time

import pytest

from raptor.api.run import RunCancelledError, RunTimeoutError, run
from raptor.api.workspace import Workspace
from raptor.mcp import async_runs


def _model(tmp_path, n=300):
    ws = Workspace(str(tmp_path))
    return ws, ws.create_model("c", template="series", n=n)["model_id"]


def test_an_abort_during_set_up_cancels_the_run(tmp_path):
    ws, mid = _model(tmp_path)
    abort = threading.Event()
    seen = []

    def setup(stage, done, total):
        seen.append(done)
        abort.set()                       # the user presses Cancel mid set-up

    with pytest.raises(RunCancelledError, match="set-up"):
        run(ws, mid, num_runs=1, run_length=10.0, confirm_long_run=True,
            abort=abort, setup_progress=setup)
    assert len(seen) == 1                 # the very next report stopped it
    assert not (ws.root / "runs").exists() or not list((ws.root / "runs").iterdir())


def test_the_timeout_applies_to_set_up(tmp_path):
    ws, mid = _model(tmp_path)

    def slow(stage, done, total):
        time.sleep(0.2)

    with pytest.raises(RunTimeoutError, match="setting up"):
        run(ws, mid, num_runs=1, run_length=10.0, confirm_long_run=True,
            timeout_s=0.05, setup_progress=slow)


def test_a_run_that_is_not_aborted_still_reports_and_finishes(tmp_path):
    ws, mid = _model(tmp_path, n=40)
    seen = []
    result = run(ws, mid, num_runs=1, run_length=10.0, confirm_long_run=True,
                 setup_progress=lambda *a: seen.append(a))
    assert seen and result.num_runs == 1


def test_cancelling_a_background_job_during_set_up_ends_it_cancelled(tmp_path):
    ws, mid = _model(tmp_path)
    in_setup = threading.Event()

    def work(abort, progress, setup_progress):
        def noisy(stage, done, total):
            in_setup.set()
            time.sleep(0.01)
            setup_progress(stage, done, total)
        return run(ws, mid, num_runs=1, run_length=10.0, confirm_long_run=True,
                   abort=abort, progress=progress, setup_progress=noisy)

    job = async_runs.start(work, model_id=mid, total=1)
    assert in_setup.wait(10)
    async_runs.cancel(job.job_id)
    for _ in range(400):
        if job.state not in ("queued", "running"):
            break
        time.sleep(0.02)
    assert job.state == "cancelled", (job.state, job.error)
