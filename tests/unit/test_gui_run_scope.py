"""Run-panel hierarchy scope: selection, extract-only run model, labels."""
from __future__ import annotations

from pathlib import Path

import pytest

from raptor.core.fileio.rbd import load_rbd

HIER = (Path(__file__).resolve().parents[1]
        / "golden" / "HierarchyExample" / "input.rbd")


@pytest.fixture(scope="module")
def qapp():
    from PySide6.QtWidgets import QApplication
    return QApplication.instance() or QApplication([])


@pytest.fixture
def win(qapp):
    from raptor.gui.ui.main_window import MainWindow
    w = MainWindow()
    w.resize(1400, 900)
    w.show()
    w._load_into_app(load_rbd(str(HIER)), str(HIER))
    w._dirty = False
    qapp.processEvents()
    yield w
    w._dirty = False
    w.close()


def _hierarchy(win, name="650path"):
    return next(h for h, hr in win._diagram_dock.view.hiers.items()
                if hr.name == name)


def test_hierarchy_names_are_escaped_in_warning_and_banner(win):
    from raptor.api import isolate
    source = win._rbd
    hid = _hierarchy(win)
    _extracted, record = isolate.extract(source, hid, {})
    record.hierarchy = "<b>not markup</b>"
    win._results._manifest = {"isolation": record.to_dict()}
    win._results._refresh_scope_banner()
    text = win._results._scope_banner.text()
    assert "<b>not markup</b>" not in text
    assert "&lt;b&gt;not markup&lt;/b&gt;" in text


def test_selecting_a_hierarchy_shows_the_warning_and_switches_level(win):
    hid = _hierarchy(win)
    panel = win._run_panel
    assert panel._scope.findData(hid) >= 0
    panel._scope.setCurrentIndex(panel._scope.findData(hid))
    assert panel.hierarchy_scope() == hid
    assert panel._scope_warning.isVisibleTo(panel)
    assert panel._scope_options.isVisibleTo(panel)
    assert "Importance measures" in panel._scope_warning.text()
    panel._scope_deps.setCurrentIndex(panel._scope_deps.findData("system"))
    assert panel.scope_treatments()["deps"] == "system"
    assert win._diagram_dock.view._current_parent == hid

    panel._scope.setCurrentIndex(0)
    assert win._diagram_dock.view._current_parent == 0
    assert not panel._scope_warning.isVisibleTo(panel)


def test_scoped_run_model_is_an_extract_and_source_is_untouched(win):
    hid = _hierarchy(win)
    source = win._rbd
    panel = win._run_panel
    panel._scope.setCurrentIndex(panel._scope.findData(hid))
    model, record = win._prepare_run_model()
    assert model is not None and model is not source
    assert record is not None
    assert record.hierarchy == "650path"
    assert win._rbd is source


def test_deleting_the_selected_hierarchy_falls_back_to_whole_model(win):
    hid = _hierarchy(win)
    panel = win._run_panel
    panel._scope.setCurrentIndex(panel._scope.findData(hid))
    win._diagram_dock.view.hiers.pop(hid)
    win._refresh_run_scope_options()
    assert panel.hierarchy_scope() == 0
    assert win._diagram_dock.view._current_parent == 0


def test_switching_scope_clears_results_from_the_other_scope(win):
    hid = _hierarchy(win)
    panel = win._run_panel
    panel._scope.setCurrentIndex(panel._scope.findData(hid))
    win._results._report_data = [{"Availability": 0.9}]
    assert win._results.has_results()
    panel._scope.setCurrentIndex(0)
    assert not win._results.has_results()


def test_isolation_error_cancels_the_run_with_a_message(win, monkeypatch):
    from raptor.api import isolate
    from raptor.gui.ui import msgbox
    hid = _hierarchy(win)
    win._run_panel._scope.setCurrentIndex(
        win._run_panel._scope.findData(hid))

    def _bad(*args, **kwargs):
        raise isolate.IsolationError("missing In/Out door")

    monkeypatch.setattr(isolate, "extract", _bad)
    told = []
    monkeypatch.setattr(msgbox, "warning",
                        lambda *args, **kwargs: told.append(args))
    model, record = win._prepare_run_model()
    assert model is None and record is None
    assert told


def test_refuse_treatment_cancels_the_run_with_a_message(win, monkeypatch):
    from raptor.gui.ui import msgbox
    hid = _hierarchy(win)
    panel = win._run_panel
    panel._scope.setCurrentIndex(panel._scope.findData(hid))
    panel._scope_pools.setCurrentIndex(
        panel._scope_pools.findData("refuse"))
    told = []
    monkeypatch.setattr(msgbox, "warning",
                        lambda *args, **kwargs: told.append(args))
    model, record = win._prepare_run_model()
    assert model is None and record is None
    assert told


def test_a_scoped_run_uses_the_extract_and_labels_its_results(win):
    from raptor.api import isolate
    from raptor.core import run_manifest
    hid = _hierarchy(win)
    source = win._rbd
    panel = win._run_panel
    panel._scope.setCurrentIndex(panel._scope.findData(hid))
    panel._trials.setValue(1)
    panel._trials.editingFinished.emit()    # commit 1 trial, not the model's 10
    panel._cores.setValue(1)
    # Changing a run setting marks the model dirty; this test is about the
    # scoped run not dirtying it, so reset that test setup change.
    win._dirty = False
    assert win._current_experiment()["trials"] == 1
    win._live_sim_action.setChecked(True)   # synchronous one-core run
    win._on_run()
    iso = (win._results._manifest or {}).get("isolation") or {}
    assert iso.get("hierarchy") == "650path"
    # The manifest hash proves the numbers came from the extract, not the
    # whole source model, with the treatments the panel showed.
    extract, _record = isolate.extract(source, hid, panel.scope_treatments())
    assert win._results._manifest["model_hash"] == \
        run_manifest.model_content_hash(extract)
    assert win._results._manifest["model_hash"] != \
        run_manifest.model_content_hash(source)
    assert win._rbd is source
    assert win._current_file == str(HIER)
    assert win._dirty is False
    assert win._results._scope_banner.isVisibleTo(win._results)
    assert "650path" in win._results._scope_banner.text()


def test_results_banner_names_the_isolated_hierarchy(win):
    from raptor.api import isolate
    source = win._rbd
    hid = _hierarchy(win)
    _extracted, record = isolate.extract(source, hid, {})
    win._results._manifest = {"isolation": record.to_dict()}
    win._results._refresh_scope_banner()
    banner = win._results._scope_banner
    assert banner.isVisibleTo(win._results)
    assert "650path" in banner.text()
    assert "not a whole-model result" in banner.text()
    win._results.clear()
    assert not banner.isVisibleTo(win._results)
