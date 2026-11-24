"""Opening an `.rbdz` puts the saved result set back on the Results dock."""
from __future__ import annotations

import json
import os
from datetime import datetime

import pytest

pytest.importorskip("PySide6")

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication

from raptor.core.engine.runner import run_simulation_with_reports
from raptor.core.fileio import rbdz
from raptor.core.fileio.rbd import load_rbd
from raptor.core.run_manifest import manifest_digest
from raptor.gui.ui.file_io_mixin import FileIoMixin, _suffix_for_filter
from raptor.gui.ui.results import ResultsDock

REPO = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
FIXTURE = os.path.join(REPO, "tests/golden/TwoBlockRedundancy/input.rbd")


@pytest.fixture(scope="module")
def qapp():
    return QApplication.instance() or QApplication([])


@pytest.fixture(scope="module")
def two_runs(qapp):
    """A live two-run result set, plus the model that produced it."""
    rbd = load_rbd(FIXTURE)
    report_data: list = []
    logs, eor, fin = run_simulation_with_reports(
        rbd, num_runs=2, date="fixed", report_sink=report_data)
    live = ResultsDock()
    live.set_results(logs, eor, fin, report_data=report_data, units="hours",
                     rbd=rbd)
    return rbd, live


def test_archive_round_trip_restores_the_overview_and_runs(
        qapp, two_runs, tmp_path):
    rbd, live = two_runs
    payload = live.archive_payload()
    assert payload is not None and "summary.json" in payload
    archive = tmp_path / "two.rbdz"
    rbdz.write_archive(archive, "model placeholder", results=payload)

    # The archive's model entry is irrelevant to this test; read the result
    # entries back exactly as the GUI does.
    files, _ = rbdz.extract_results(archive, tmp_path / "extracted")
    summary = json.loads(files["summary.json"].read_text())
    saved = ResultsDock()
    assert saved.set_archive_results(files, summary, rbd=rbd)
    assert saved._report_data and saved.run_count() == 2
    assert saved._cards["Availability"]._value.text() == \
        live._cards["Availability"]._value.text()
    assert len(saved._event_logs) == 2
    assert saved._final_lines
    # A reopened archive can be saved again without losing its runs.
    again = saved.archive_payload()
    assert again is not None
    assert json.loads(again["summary.json"])["num_runs"] == 2


def test_archive_summary_names_the_run_and_keeps_its_time(
        qapp, two_runs, tmp_path):
    rbd, live = two_runs
    # A run manifest is what makes a result set self-describing; give this
    # one the fields the archive's summary should carry.
    manifest = {"model_hash": "abc123", "model_name": "two.rbd",
                "experiment": {"start_stats": 0}}
    live.set_results(live._event_logs, live._eor_lines, live._final_lines,
                     report_data=live._report_data, units="hours",
                     manifest=manifest, rbd=rbd)
    payload = live.archive_payload()
    summary = json.loads(payload["summary.json"])
    assert summary["format"] == rbdz.RESULTS_FORMAT_ID
    assert summary["version"] == rbdz.RESULTS_VERSION
    assert summary["run_id"] == manifest_digest(manifest)
    assert summary["model_hash"] == "abc123"
    assert summary["num_runs"] == 2
    assert summary["run_at"]
    # Events travel as lines, not as one giant joined string.
    assert isinstance(payload["events.txt"], list)

    archive = tmp_path / "two.rbdz"
    rbdz.write_archive(archive, "model placeholder", results=payload)
    files, _ = rbdz.extract_results(archive, tmp_path / "extracted")
    saved = ResultsDock()
    assert saved.set_archive_results(files, summary, rbd=rbd)
    assert saved._run_at == datetime.fromisoformat(summary["run_at"])
    assert saved._manifest["model_hash"] == "abc123"


def test_save_dialog_filter_chooses_its_own_extension():
    assert _suffix_for_filter(
        "RAPTOR Studio archives (*.rbdz)", ".rbds") == ".rbdz"
    assert _suffix_for_filter(
        "RAPTOR Studio models (*.rbds)", ".rbdz") == ".rbds"
    assert _suffix_for_filter("Classic RAPTOR (*.rbd)", ".rbds") == ".rbd"
    assert _suffix_for_filter("", ".rbds") == ".rbds"


def test_save_message_says_what_the_archive_contains(qapp, two_runs):
    _rbd, live = two_runs

    class _Host:
        _results = live

    message, timeout = FileIoMixin._save_message(_Host(), "plant.rbdz")
    assert "2 runs of results included" in message
    assert timeout > 0
    assert FileIoMixin._save_message(_Host(), "plant.rbds")[0] == \
        "Saved: plant.rbds"

    empty = ResultsDock()
    _Host._results = empty
    assert "model only" in FileIoMixin._save_message(_Host(), "plant.rbdz")[0]


@pytest.fixture(scope="module")
def analysed_runs(qapp):
    """A run with capacity, cost and weak-link data to archive."""
    rbd = load_rbd(os.path.join(REPO, "tests/golden/Capacity3/input.rbd"))
    rbd.sections["SYSTEM_DATA"].rows[0]["weakAnalysis"] = "TRUE"
    report_data: list = []
    caps: list = []
    logs, eor, fin = run_simulation_with_reports(
        rbd, num_runs=2, date="fixed", report_sink=report_data,
        capacity_sink=caps)
    live = ResultsDock()
    live.set_results(logs, eor, fin, report_data=report_data, units="hours",
                     cap_lines=caps, rbd=rbd)
    return rbd, live


def test_an_archive_restores_the_structured_analyses(
        qapp, analysed_runs, tmp_path):
    """Capacity, Cost and Weak Link read objects, not report text; the
    archive carries them so reopening shows the pages, not a 'no analysis'
    note."""
    from PySide6.QtWidgets import QTableWidget
    rbd, live = analysed_runs
    first = live._report_data[0]
    assert first.capacity is not None, "fixture has no capacity analysis"
    assert first.cost is not None, "fixture has no cost analysis"
    assert first.weak_stats, "fixture has no weak-link analysis"

    payload = live.archive_payload()
    assert payload is not None
    archive = tmp_path / "analysed.rbdz"
    rbdz.write_archive(archive, "model placeholder", results=payload)
    files, _ = rbdz.extract_results(archive, tmp_path / "extracted")
    summary = json.loads(files["summary.json"].read_text())

    saved = ResultsDock()
    assert saved.set_archive_results(files, summary, rbd=rbd)
    restored = saved._report_data[0]
    assert restored.capacity is not None
    assert restored.capacity.nodes[restored.capacity.system_node_id].ave_flow \
        == first.capacity.nodes[first.capacity.system_node_id].ave_flow
    assert restored.cost is not None
    assert restored.cost.grand_total == first.cost.grand_total
    assert restored.weak_stats
    assert restored.weak_nodes
    # the pages build their tables instead of the explanatory note
    assert saved._build_capacity_page().findChildren(QTableWidget)
    assert saved._build_cost_page().findChildren(QTableWidget)
    assert saved._build_weak_page().findChildren(QTableWidget)
    # the weak-link denominator survives, so Reliability is not 100%
    assert restored.missions_attempted == first.missions_attempted
    # Save As after opening must be able to serialise the restored objects
    again = saved.archive_payload()
    assert again is not None



def test_saving_after_a_new_run_does_not_keep_the_old_runs_files(
        qapp, two_runs, tmp_path):
    """Re-saving a reopened archive keeps its entries; after a NEW run the
    old run's event log (and drivers, plots...) must not be carried into
    the archive beside the new run's figures."""
    from raptor.gui.ui import settings
    from raptor.gui.ui.main_window import MainWindow
    rbd, live = two_runs
    archive = tmp_path / "old.rbdz"
    rbdz.write_archive(archive, "model placeholder",
                       results=live.archive_payload())
    settings.set_value("startup/show_start_page", False)
    win = MainWindow()
    try:
        win._load_into_app(load_rbd(FIXTURE), None)
        assert win._restore_archive_results(str(archive))
        assert "events.txt" in win._archive_files
        resaved = tmp_path / "resaved.rbdz"
        assert win._write_model(str(resaved))
        assert "events.txt" in rbdz.read_results(resaved).results

        # A new run with the event log off replaces the result set.
        report_data: list = []
        _logs, eor, fin = run_simulation_with_reports(
            load_rbd(FIXTURE), num_runs=1, date="fixed",
            report_sink=report_data)
        win._results.set_results([], eor, fin, report_data=report_data,
                                 units="hours", rbd=rbd)
        fresh = tmp_path / "fresh.rbdz"
        assert win._write_model(str(fresh))
        results = rbdz.read_results(fresh).results
        assert "events.txt" not in results
        summary = json.loads(results["summary.json"])
        assert summary["num_runs"] == 1
    finally:
        win._dirty = False
        win.close()


# -- an open archive: Save records versions; a new run makes it unsaved ----
@pytest.fixture
def archive_window(qapp, two_runs, tmp_path, monkeypatch):
    """A window with a temp .rbdz opened through the real open path, and a
    history store that is not the user's."""
    import raptor.api.history as api_history
    from raptor.core.fileio import rbds as rbds_io
    from raptor.core.history import HistoryStore
    from raptor.gui.ui import settings
    from raptor.gui.ui.main_window import MainWindow
    store = HistoryStore(tmp_path / "history")
    monkeypatch.setattr(api_history, "default_store", lambda: store)
    rbd, live = two_runs
    archive = tmp_path / "plant.rbdz"
    rbdz.write_archive(archive, rbds_io.render_for_save(load_rbd(FIXTURE)),
                       results=live.archive_payload())
    settings.set_value("startup/show_start_page", False)
    win = MainWindow()
    win._open_path(str(archive))
    yield win, archive, store, rbd
    win._dirty = False
    win.close()


def _new_run(win, rbd, runs=1):
    report_data: list = []
    _logs, eor, fin = run_simulation_with_reports(
        load_rbd(FIXTURE), num_runs=runs, date="fixed",
        report_sink=report_data)
    win._results.set_results([], eor, fin, report_data=report_data,
                             units="hours", rbd=rbd)


def test_viewing_a_freshly_opened_archive_is_not_unsaved(archive_window):
    win, archive, store, _rbd = archive_window
    assert win._results._archive_source
    assert win._dirty is False
    assert "*" not in win.windowTitle()
    assert [s.reason for s in store.snapshots(archive)] == ["opened"]


def test_saving_an_archive_records_a_version(archive_window):
    win, archive, store, _rbd = archive_window
    assert win._write_model(str(archive))
    assert [s.reason for s in store.snapshots(archive)] == ["save", "opened"]


def test_a_new_run_makes_an_open_archive_unsaved(archive_window):
    win, _archive, _store, rbd = archive_window
    _new_run(win, rbd)
    assert win._dirty is True
    assert "*" in win.windowTitle()


def test_clearing_the_results_of_an_open_archive_makes_it_unsaved(
        archive_window):
    win, _archive, _store, _rbd = archive_window
    win._results.clear()
    assert win._dirty is True


def test_save_version_now_after_a_new_run_captures_the_new_results(
        archive_window, monkeypatch):
    """open -> Save version now "A" -> run again -> "B" used to rename A and
    record nothing; the new results never reached the history."""
    from raptor.gui.ui import history_dialog as history_ui
    win, archive, store, rbd = archive_window
    dialog = history_ui.HistoryDialog(
        win, str(archive), store, current_model=win._rbd,
        save_current=win._save_for_history)
    answers = iter(["A", "B"])
    monkeypatch.setattr(history_ui.QInputDialog, "getText",
                        staticmethod(lambda *a, **k: (next(answers), True)))
    dialog._snapshot_now()
    dialog._desc.cancel()
    _new_run(win, rbd, runs=1)
    dialog._snapshot_now()
    shots = store.snapshots(archive)
    labels = [s.label for s in shots]
    assert labels[:2] == ["B", "A"], labels
    old = json.loads(rbdz.read_results(shots[1].path).results["summary.json"])
    new = json.loads(rbdz.read_results(shots[0].path).results["summary.json"])
    assert old["num_runs"] == 2 and new["num_runs"] == 1
    assert win._dirty is False


def test_plain_models_are_unaffected_by_results(qapp, tmp_path, two_runs):
    from raptor.gui.ui import settings
    from raptor.gui.ui.main_window import MainWindow
    rbd, _live = two_runs
    settings.set_value("startup/show_start_page", False)
    win = MainWindow()
    try:
        win._load_into_app(load_rbd(FIXTURE), None)
        _new_run(win, rbd)
        assert win._dirty is False
    finally:
        win._dirty = False
        win.close()
