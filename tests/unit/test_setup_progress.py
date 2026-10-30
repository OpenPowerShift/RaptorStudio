"""Set-up progress: a large model is quiet for minutes before its first run.

The engine reports `(stage, done, total)` while the path pass runs; the CLI
prints it under `--progress json` and `run_status` shows it, so a client can
tell a slow set-up from a hang.
"""
from __future__ import annotations

import json
import pathlib
import threading
import time

from raptor.api.run import run
from raptor.api.workspace import Workspace
from raptor.cli.main import main
from raptor.core.engine.load import load_model
from raptor.mcp import async_runs

GOLD = pathlib.Path(__file__).resolve().parents[1] / "golden"
MOTOR = str(GOLD / "MotorDemo" / "input.rbd")


def _chain(tmp_path, n=60):
    ws = Workspace(str(tmp_path))
    return ws, ws.create_model("chain", template="series", n=n)["model_id"]


def test_load_model_reports_the_path_pass(tmp_path):
    ws, mid = _chain(tmp_path)
    seen = []
    load_model(ws.get(mid).rbd, progress=lambda *a: seen.append(a))
    assert seen[0][0] == "Analysing paths" and seen[0][1] == 0
    assert seen[-1][1] == seen[-1][2] > 0
    assert [s[1] for s in seen] == sorted(s[1] for s in seen)


def test_load_model_without_a_callback_is_unchanged(tmp_path):
    ws, mid = _chain(tmp_path)
    assert len(load_model(ws.get(mid).rbd).nodes_by_id) == len(
        load_model(ws.get(mid).rbd, progress=lambda *a: None).nodes_by_id)


def test_run_forwards_set_up_progress(tmp_path):
    ws, mid = _chain(tmp_path)
    seen = []
    run(ws, mid, num_runs=1, run_length=10.0, confirm_long_run=True,
        setup_progress=lambda *a: seen.append(a))
    assert seen and seen[-1][1] == seen[-1][2]


def test_cli_prints_set_up_progress_as_json(capsys):
    assert main(["run", MOTOR, "-n", "1", "-f", "json",
                 "--progress", "json"]) == 0
    events = [json.loads(line) for line in capsys.readouterr().err.splitlines()
              if line.startswith("{")]
    setup = [e for e in events if e.get("event") == "setup"]
    assert setup and setup[-1]["done"] == setup[-1]["total"]


def _wait(job):
    for _ in range(400):
        if job.state not in ("queued", "running"):
            return
        time.sleep(0.01)
    raise AssertionError(job.snapshot())


def test_a_job_reports_its_phase_and_set_up_progress():
    reported, release = threading.Event(), threading.Event()

    def work(abort, progress, setup_progress):
        setup_progress("Analysing paths", 3, 10)
        reported.set()
        assert release.wait(10)
        setup_progress("Analysing paths", 10, 10)
        return None

    job = async_runs.start(work, model_id="m", total=1)
    assert reported.wait(10)
    mid_setup = job.snapshot()
    release.set()
    _wait(job)
    assert mid_setup["phase"] == "setting_up"
    assert mid_setup["setup"] == {"stage": "Analysing paths", "done": 3,
                                  "total": 10, "percent": 30.0}
    assert job.snapshot()["setup"]["percent"] == 100.0
    assert job.snapshot()["phase"] == "done"


def test_a_job_whose_work_takes_two_arguments_still_runs():
    job = async_runs.start(lambda abort, progress: None, model_id="m", total=1)
    _wait(job)
    assert job.state == "done" and job.snapshot()["setup"] is None
