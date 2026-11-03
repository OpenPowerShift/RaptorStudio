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


# ----------------------------------------------------------------------
# The dock keeps the stream lazy; only the preview is materialised
# ----------------------------------------------------------------------

def test_set_results_does_not_materialise_the_end_of_run_stream(
        qapp, monkeypatch, tmp_path):
    """The whole point of the sink: the O(runs x elements) report stays on
    disk. `set_results` used to do `list(end_of_run_lines)`, which read
    every line straight back into RAM."""
    stream = ResultStream("end_of_run", directory=str(tmp_path))
    stream.extend([f"line {i}" for i in range(5)])
    try:
        def boom(*_a, **_kw):
            raise AssertionError("set_results materialised the full stream")
        monkeypatch.setattr(stream, "read_all_lines", boom)
        dock = ResultsDock()
        dock.set_results([], stream, ["final"])
        assert dock._eor_lines is stream
        assert "line 0" in dock._raw_views["End of Run"].toPlainText()
        # The full source still exports on demand.
        assert len(dock.raw_report_lines("End of Run")[0]) == 5
    finally:
        stream.close()


def test_empty_raw_input_shows_nothing_and_saves_nothing(qapp, monkeypatch,
                                                        tmp_path):
    dock = ResultsDock()
    dock.set_results([], [], [])
    assert dock._raw_views["End of Run"].toPlainText() == ""
    assert dock._raw_views["Final Results"].toPlainText() == ""

    called: list[tuple] = []
    monkeypatch.setattr(fdg, "getSaveFileName",
                        staticmethod(lambda *a, **kw: called.append(a)))
    idx = dock._raw_tabs.indexOf(dock._raw_views["End of Run"])
    dock._raw_tabs.setCurrentIndex(idx)
    dock._save_raw_report()
    assert called == []          # no dialog for an empty report


def test_clear_drops_the_raw_sources(qapp):
    dock = ResultsDock()
    dock.set_results([], ["eor"], ["fin"])
    assert dock._raw_sources
    dock.clear()
    assert dock._raw_sources == {}


def test_raw_preview_does_not_claim_truncation_when_all_lines_show(qapp):
    dock = ResultsDock()
    dock._RAW_PREVIEW_LINES = 3
    dock._RAW_PREVIEW_BYTES = 1_000
    text, total, shown = dock._raw_preview(["a", "b", "c"])
    assert (total, shown) == (3, 3)
    assert "Showing" not in text

    # A byte cap that lands exactly on the last line is not truncation.
    dock._RAW_PREVIEW_BYTES = 33          # 3 lines of 10 chars + newlines
    text, total, shown = dock._raw_preview(["x" * 10] * 3)
    assert (total, shown) == (3, 3)
    assert "Showing" not in text


def test_raw_preview_truncates_by_bytes_and_says_so(qapp):
    dock = ResultsDock()
    dock._RAW_PREVIEW_LINES = 100
    dock._RAW_PREVIEW_BYTES = 25          # room for three 10-char lines
    text, total, shown = dock._raw_preview(["x" * 10] * 5)
    assert (total, shown) == (5, 3)
    assert "first 3 of 5" in text


def test_raw_report_lines_reports_the_real_total(qapp, monkeypatch):
    dock = ResultsDock()
    dock.set_results([], [f"line {i}" for i in range(7)], ["fin"])
    monkeypatch.setattr(dock, "_RAW_PREVIEW_LINES", 3)
    lines, total = dock.raw_report_lines("End of Run")
    assert lines == ["line 0", "line 1", "line 2"]
    assert total == 7


def test_saving_a_closed_backing_stream_warns_and_writes_nothing(
        qapp, monkeypatch, tmp_path):
    """A new run closes the previous batch's stream while its preview is
    still on screen; Save must say so, not raise."""
    from raptor.gui.ui import results as results_mod

    stream = ResultStream("end_of_run", directory=str(tmp_path))
    stream.extend(["line"])
    dock = ResultsDock()
    dock.set_results([], stream, ["fin"])
    stream.close()

    warnings: list[tuple] = []
    monkeypatch.setattr(results_mod.msgbox, "warning",
                        lambda *a, **kw: warnings.append(a))
    out = str(tmp_path / "end_of_run.txt")
    monkeypatch.setattr(fdg, "getSaveFileName",
                        staticmethod(lambda *a, **kw: (out, "")))
    idx = dock._raw_tabs.indexOf(dock._raw_views["End of Run"])
    dock._raw_tabs.setCurrentIndex(idx)
    dock._save_raw_report()
    assert warnings
    assert not os.path.exists(out)


def test_the_printed_report_uses_the_real_total(monkeypatch):
    """A capped view is still reported with its true length (the PDF's
    'first N of M lines' note must not quote the dock's preview cap)."""
    from raptor.api.report_html import TextBlock, _raw_html
    from raptor.gui.ui.printing import collect_raw_report_sections

    class _View:
        def toPlainText(self):
            return "truncated preview"

    class _Dock:
        _raw_views = {"End of Run": _View()}

        def raw_report_lines(self, name, max_lines=None):
            return ["line 1", "line 2"], 2_500

    win = type("W", (), {"_results": _Dock()})()
    [(title, lines, total)] = collect_raw_report_sections(win)
    assert title == "Raw Report — End of Run"
    assert lines == ["line 1", "line 2"]
    assert total == 2_500
    html = _raw_html([TextBlock("End of Run", lines, "", total)])
    assert "The first 1,500 of 2,500 lines." in html


# ----------------------------------------------------------------------
# ResultStream: caps, closed/missing files, header rows
# ----------------------------------------------------------------------

def test_result_stream_preview_stops_at_the_byte_cap(tmp_path):
    stream = ResultStream("test", directory=str(tmp_path))
    try:
        stream.extend(["x" * 10] * 20)
        assert len(stream.read_preview(100, 25)) == 3
    finally:
        stream.close()


def test_result_stream_preview_reads_closed_and_missing_as_empty(tmp_path):
    stream = ResultStream("test", directory=str(tmp_path))
    stream.extend(["row"])
    stream.close()
    assert stream.read_preview(10, 1_000) == []

    gone = ResultStream("test", directory=str(tmp_path))
    try:
        gone.extend(["row"])
        os.remove(gone.path)          # reaped behind our back
        assert gone.read_preview(10, 1_000) == []
    finally:
        gone.close()


def test_result_stream_header_counts_as_a_row(tmp_path):
    """`len(stream)` must match what readers see, preview totals included."""
    stream = ResultStream("test", header="SimulationTime Flow",
                          directory=str(tmp_path))
    try:
        stream.extend(["1.0 5"])
        assert len(stream) == 2
        assert len(stream.read_all_lines()) == 2
        assert stream.read_preview(10, 1_000) == ["SimulationTime Flow",
                                                  "1.0 5"]
    finally:
        stream.close()


def test_result_stream_extend_writes_a_large_batch(tmp_path):
    stream = ResultStream("test", directory=str(tmp_path))
    try:
        stream.extend([f"row {i}" for i in range(50_000)])
        assert len(stream) == 50_000
        assert stream.read_preview(3, 1_000_000) == [
            "row 0", "row 1", "row 2"]
        assert len(stream.read_all_lines()) == 50_000
    finally:
        stream.close()


# ----------------------------------------------------------------------
# Runner: sink/list equivalence, zero runs, aborted batches
# ----------------------------------------------------------------------

def _expected_end_of_run(rbd, data, date):
    from raptor.core.engine.load import load_model
    from raptor.core.engine.reports import build_end_of_run_report
    model = load_model(rbd)
    lines = [f"End_Of_Run Report for {rbd.file_name}  {date}", ""]
    for run_data in data:
        lines.extend(build_end_of_run_report(run_data, model.blocks_by_id))
    return lines


def test_end_of_run_sink_writes_exactly_what_the_list_would_hold():
    rbd = load_model_file(FIXTURE)
    data: list = []
    sink: list[str] = []
    _logs, returned, _final = run_simulation_with_reports(
        rbd, num_runs=3, date="fixed", report_sink=data,
        end_of_run_sink=sink)
    assert returned == []
    assert sink == _expected_end_of_run(rbd, data, "fixed")


def test_no_sink_still_returns_the_report():
    rbd = load_model_file(FIXTURE)
    _logs, eor, _final = run_simulation_with_reports(
        rbd, num_runs=2, date="fixed")
    assert eor and eor[0].startswith("End_Of_Run Report for")


def test_a_zero_run_batch_streams_the_header_and_reports_once():
    rbd = load_model_file(FIXTURE)
    sink: list[str] = []
    stages: list[tuple[int, int, str]] = []
    _logs, returned, final = run_simulation_with_reports(
        rbd, num_runs=0, date="fixed", end_of_run_sink=sink,
        report_progress=lambda d, t, label: stages.append((d, t, label)))
    assert returned == []
    assert sink == [f"End_Of_Run Report for {rbd.file_name}  fixed", ""]
    assert final and final[0].startswith("Final_Results_Report for")
    # Nothing to count per run: only the final-results step.
    assert stages == [(1, 1, "Final results report")]


def test_an_aborted_batch_streams_only_completed_runs():
    from raptor.core.engine.errors import RunAborted
    rbd = load_model_file(FIXTURE)
    data: list = []
    sink: list[str] = []
    stages: list[tuple[int, int, str]] = []

    def stop_after_the_first_run(done, total):
        if done == 1:
            raise RunAborted()

    _logs, returned, _final = run_simulation_with_reports(
        rbd, num_runs=5, date="fixed", report_sink=data,
        end_of_run_sink=sink, progress=stop_after_the_first_run,
        report_progress=lambda d, t, label: stages.append((d, t, label)))
    assert len(data) == 1
    assert returned == []
    assert sink == _expected_end_of_run(rbd, data, "fixed")
    assert [s[0] for s in stages] == [1, 1]


# ----------------------------------------------------------------------
# MainWindow: population guard and stream cleanup
# ----------------------------------------------------------------------

def test_the_population_guard_blocks_a_nested_population(qapp):
    from raptor.gui.ui.main_window import MainWindow

    win = MainWindow()
    calls: list[str] = []

    class _FakeResults:
        def set_results(self, *args, **kwargs):
            assert win._results_populating is True
            calls.append("start")
            # A queued event landing inside set_results must not start a
            # second population (it used to clear the half-built dock).
            win._set_results_with_progress()
            kwargs["progress"]("stage", 1, 2)
            calls.append("end")

    win._results = _FakeResults()
    win._set_results_with_progress([])
    assert calls == ["start", "end"]
    assert win._results_populating is False


def test_a_run_while_results_are_populating_is_refused(qapp):
    from raptor.gui.ui.main_window import MainWindow

    win = MainWindow()
    win._results_populating = True

    def must_not_run():
        raise AssertionError("_run_simulation ran during results population")

    win._maybe_save_before_run = must_not_run
    win._run_simulation()
    assert "previous results" in win._statusbar.currentMessage()
    win._results_populating = False


def test_report_stage_progress_throttles_its_pumps(qapp, monkeypatch):
    from raptor.gui.ui import main_window as mw
    from raptor.gui.ui.main_window import MainWindow

    win = MainWindow()
    pumps: list[tuple] = []

    class _App:
        @staticmethod
        def processEvents(*args):
            pumps.append(args)

    monkeypatch.setattr(mw, "QApplication", _App)
    win._last_stage_pump = 0.0
    for done in range(1, 101):
        win._report_stage_progress(done, 100, "End-of-Run report")
    # 100 per-run callbacks must not mean 100 event-loop pumps; the final
    # step always paints.
    assert 1 <= len(pumps) <= 3
    assert pumps[-1][0] is mw.QEventLoop.ProcessEventsFlag.ExcludeUserInputEvents


def test_discarding_a_failed_batch_closes_its_temp_streams(tmp_path):
    from raptor.gui.ui.main_window import MainWindow

    a = ResultStream("end_of_run", directory=str(tmp_path))
    b = ResultStream("ao", directory=str(tmp_path))
    paths = [a.path, b.path]
    for p in paths:
        assert os.path.exists(p)
    MainWindow._discard_report_streams({"end_of_run": a, "ao": b,
                                        "capacity": None})
    for p in paths:
        assert not os.path.exists(p)
