"""Large-result guards: the Raw view previews, the full report exports.

The End-of-Run report is O(runs x elements). Joining all of it and handing
it to QPlainTextEdit was the crash path on a large batch.
"""
from __future__ import annotations

import os

import pytest
from PySide6.QtWidgets import QApplication

from raptor.core.fileio.model_file import load_model_file
from raptor.core.engine.runner import run_simulation_with_reports
from raptor.gui.ui import filedialogs as fdg
from raptor.gui.ui.results import ResultsDock
from raptor.gui.ui.result_streams import ResultStream

FIXTURE = "tests/golden/TwoBlockRedundancy/input.rbd"


@pytest.fixture(scope="module")
def qapp():
    return QApplication.instance() or QApplication([])


def test_raw_view_previews_a_huge_report_and_saves_it_all(qapp, monkeypatch,
                                                          tmp_path):
    dock = ResultsDock()
    total = dock._RAW_PREVIEW_LINES + 1_000
    eor = [f"line {i}" for i in range(total)]
    dock.set_results([], eor, ["final"])
    shown = dock._raw_views["End of Run"].toPlainText()
    assert f"first {dock._RAW_PREVIEW_LINES:,} of {total:,}" in shown
    assert "line 0" in shown
    assert f"line {total - 1}" not in shown

    path = str(tmp_path / "end_of_run.txt")
    monkeypatch.setattr(fdg, "getSaveFileName",
                        staticmethod(lambda *a, **kw: (path, "")))
    idx = dock._raw_tabs.indexOf(dock._raw_views["End of Run"])
    dock._raw_tabs.setCurrentIndex(idx)
    dock._save_raw_report()
    assert os.path.exists(path)
    with open(path, encoding="utf-8") as f:
        saved = f.read().splitlines()
    assert len(saved) == total
    assert saved[0] == "line 0"
    assert saved[-1] == f"line {total - 1}"


def test_set_results_reports_population_stages(qapp):
    dock = ResultsDock()
    seen: list[tuple[str, int, int]] = []
    dock.set_results([], ["eor"], ["fin"],
                     progress=lambda label, done, total:
                     seen.append((label, done, total)))
    assert seen[0][0] == "overview"
    assert seen[-1][0] == "event log"
    assert seen[-1][1:] == (6, 6)


def test_result_stream_preview_is_bounded_and_exports_full(tmp_path):
    stream = ResultStream("test", directory=str(tmp_path))
    try:
        stream.extend([f"row {i}" for i in range(50)])
        preview = stream.read_preview(10, 1_000_000)
        assert preview == [f"row {i}" for i in range(10)]
        # The full stream is still there for Export.
        assert len(stream.read_all_lines()) == 50
    finally:
        stream.close()


def test_report_stage_progress_reports_each_run(qapp):
    rbd = load_model_file(FIXTURE)
    sink: list[str] = []
    stages: list[tuple[int, int, str]] = []

    def progress(done, total, label):
        stages.append((done, total, label))

    logs, returned, final = run_simulation_with_reports(
        rbd, num_runs=2, date="fixed", end_of_run_sink=sink,
        report_progress=progress)
    assert len(logs) == 2
    assert returned == []
    assert sink and sink[0].startswith("End_Of_Run Report for")
    assert [s[0] for s in stages] == [1, 2, 2]
    assert stages[-1][2] == "Final results report"
    assert final and final[0].startswith("Final_Results_Report for")
