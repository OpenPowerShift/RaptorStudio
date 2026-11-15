"""P6: one core and several give different (equally valid) numbers, and the
GUI, CLI and MCP do not all default to the same, so every result says which
random streams it used and how to get the same numbers again."""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from raptor.api.run import run as api_run, streams_note
from raptor.api.workspace import Workspace

MODEL = Path(__file__).resolve().parents[2] / "new_docs" / "models" / "HospitalPower.rbds"


@pytest.mark.parametrize(("workers", "mode"), [(1, "sequential"), (2, "parallel")])
def test_a_run_records_its_streams_and_seed(tmp_path, workers, mode):
    ws = Workspace(tmp_path)
    mid = ws.open_model(MODEL)["model_id"]
    result = api_run(ws, mid, num_runs=4, workers=workers, seed=7,
                     confirm_long_run=True)
    assert (result.streams, result.seed) == (mode, 7)
    summary = json.loads((ws.root / "runs" / result.run_id / "summary.json").read_text())
    assert (summary["streams"], summary["seed"]) == (mode, 7)


def test_one_run_is_sequential_whatever_the_workers(tmp_path):
    ws = Workspace(tmp_path)
    mid = ws.open_model(MODEL)["model_id"]
    assert api_run(ws, mid, num_runs=1, workers=4, confirm_long_run=True).streams == "sequential"


def test_the_note_says_how_to_repeat_the_numbers():
    parallel = streams_note({"streams": "parallel", "seed": 3})
    assert "-j 2 --seed 3" in parallel and "workers=2" in parallel
    sequential = streams_note({"streams": "sequential"})
    assert "-j 1`" in sequential and "Raptor 7" in sequential


def test_the_cli_header_names_the_streams():
    from raptor.cli.cmd_run import summary_text
    doc = {"model": {"name": "M"}, "options": {"runs": 3, "streams": "parallel"},
           "wall_seconds": 0.1, "summary": {}, "warnings": []}
    assert summary_text(doc, None).splitlines()[0] == "M: 3 run(s), 0.1s, parallel streams"


def test_the_gui_manifest_line_names_the_streams():
    pytest.importorskip("PySide6")
    from PySide6.QtWidgets import QApplication

    from raptor.core.run_manifest import manifest_with_streams
    from raptor.gui.ui.results import ResultsDock

    QApplication.instance() or QApplication([])
    dock = ResultsDock()
    dock._manifest = manifest_with_streams(
        {"manifest": {"trials": 10, "model_name": "M"}, "streams": "parallel"})
    dock._refresh_manifest_line()
    assert "parallel streams" in dock._manifest_label.text()
    assert "-j 2" in dock._manifest_label.toolTip()
    assert manifest_with_streams({}) is None
