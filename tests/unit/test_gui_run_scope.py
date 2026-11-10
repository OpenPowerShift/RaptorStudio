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
    assert panel._scope_button.isVisibleTo(panel)
    assert "Importance measures" in panel._scope_warning.text()
    panel._scope_deps.setCurrentIndex(panel._scope_deps.findData("system"))
    assert panel.scope_treatments()["deps"] == "system"
    assert win._diagram_dock.view._current_parent == hid

    panel._scope.setCurrentIndex(0)
    assert win._diagram_dock.view._current_parent == 0
    assert not panel._scope_button.isVisibleTo(panel)


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


def test_stepping_a_hierarchy_scope_steps_the_extract(win):
    """Step honours the Run-panel scope: it steps the isolated extract, the
    scope stays selected, and the extract's ids still match the canvas."""
    hid = _hierarchy(win)
    panel = win._run_panel
    panel._scope.setCurrentIndex(panel._scope.findData(hid))
    try:
        session = win._ensure_step_session()
        assert session is not None
        assert panel.hierarchy_scope() == hid
        assert win._step_isolation is not None
        blocks = {b.id for b in session.blocks}
        canvas = set(win._diagram_dock.view.blocks)
        assert blocks and blocks <= canvas
        assert len(blocks) < len(win._rbd.sections["BLOCK_DATA"].rows) - 1
        win._on_step()
        assert win._step_session is session
        win._on_step_finish()
        iso = (win._results._manifest or {}).get("isolation") or {}
        assert iso.get("hierarchy") == "650path"
    finally:
        win._end_step_session()


def test_stepping_whole_model_has_no_isolation(win):
    try:
        session = win._ensure_step_session()
        assert session is not None
        assert win._step_isolation is None
    finally:
        win._end_step_session()


def test_refused_isolation_cancels_stepping_cleanly(win, monkeypatch):
    from raptor.api import isolate
    from raptor.gui.ui import msgbox
    hid = _hierarchy(win)
    panel = win._run_panel
    panel._scope.setCurrentIndex(panel._scope.findData(hid))
    monkeypatch.setattr(msgbox, "warning", lambda *a, **k: None)

    def refuse(*a, **k):
        raise isolate.IsolationError("nope")
    monkeypatch.setattr(isolate, "extract", refuse)
    assert win._ensure_step_session() is None
    assert win._step_session is None
    win._on_step()                     # must not raise
    assert win._advance_until(max_steps=1) is False


def test_stepping_a_hierarchy_is_labelled_in_live_log_and_status(win):
    hid = _hierarchy(win)
    panel = win._run_panel
    panel._scope.setCurrentIndex(panel._scope.findData(hid))
    try:
        win._on_step()
        assert "650path" in win._live_log_panel.caption.text()
        assert "isolated" in win._live_log_panel.caption.text()
    finally:
        win._end_step_session()
        win._reset_live_log()


def test_whole_model_tools_say_so_under_a_hierarchy_scope(win):
    hid = _hierarchy(win)
    panel = win._run_panel
    win._statusbar.clearMessage()
    win._note_whole_model_tool("Failure Effects")
    assert win._statusbar.currentMessage() == ""
    panel._scope.setCurrentIndex(panel._scope.findData(hid))
    win._statusbar.clearMessage()
    win._note_whole_model_tool("Failure Effects")
    assert "whole model" in win._statusbar.currentMessage()


def test_duplicate_names_prefer_the_element_on_the_current_level(win):
    """A log row names an element, not an id. With the same name at Home and
    inside a hierarchy, the level on screen wins."""
    v = win._diagram_dock.view
    rbd = win._rbd
    inner = next(r for r in rbd.sections["BLOCK_DATA"].rows
                 if str(r.get("ID")) not in ("0", "")
                 and int(float(r.get("ParentId", 0) or 0)) != 0)
    inner_parent = int(float(inner["ParentId"]))
    name = inner["blockName"]
    home = next(r for r in rbd.sections["BLOCK_DATA"].rows
                if str(r.get("ID")) not in ("0", "")
                and int(float(r.get("ParentId", 0) or 0)) == 0)
    home["blockName"] = name                       # duplicate the name
    v._set_level(inner_parent)
    assert v.locate_log_element("Block", name)
    assert v._current_parent == inner_parent       # stayed on this level
    v._set_level(0)
    assert v.locate_log_element("Block", name)
    assert v._current_parent == 0
