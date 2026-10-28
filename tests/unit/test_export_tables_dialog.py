"""File > Export > Export Tables...: the dialog and the export it performs."""
from __future__ import annotations

from pathlib import Path

import pytest
from PySide6.QtCore import Qt
from PySide6.QtWidgets import QApplication, QMessageBox

from raptor.core.fileio.rbd import load_rbd

MODEL = "tests/golden/EventDepProbe/input.rbd"

pytestmark = pytest.mark.gui


@pytest.fixture
def qapp():
    return QApplication.instance() or QApplication([])


@pytest.fixture
def win(qapp):
    from raptor.gui.ui.main_window import MainWindow
    w = MainWindow()
    w.resize(1400, 900)
    w.show()
    w._load_into_app(load_rbd(MODEL), MODEL)
    qapp.processEvents()
    yield w
    w._dirty = False
    w.close()


@pytest.fixture
def ran(win, qapp):
    win._set_exp_field("trials", 3)
    win._dirty = False
    win._run_simulation()
    qapp.processEvents()
    assert win._results.has_results()
    return win


def _dialog(w):
    from raptor.gui.ui.tables_dialog import ExportTablesDialog, gather
    rbd, bundle, path, name = gather(w)
    return ExportTablesDialog(w, rbd, bundle, path, name)


def _item(dlg, key):
    return next(c for c in dlg._children() if c.data(0, Qt.UserRole) == key)


# -- the command ---------------------------------------------------------------

def test_the_command_is_in_the_export_group_and_needs_a_model(qapp):
    from raptor.gui.ui.main_window import MainWindow
    w = MainWindow()
    try:
        assert not w._export_tables_action.isEnabled()
        assert "Unavailable" in w._export_tables_action.toolTip()
        w._load_into_app(load_rbd(MODEL), MODEL)
        qapp.processEvents()
        assert w._export_tables_action.isEnabled()
        assert w._export_tables_action.text().startswith("Export Tables")
    finally:
        w._dirty = False
        w.close()


# -- choosing ----------------------------------------------------------------------

def test_before_a_run_only_inputs_can_be_chosen(win):
    dlg = _dialog(win)
    keys = dlg.selected_keys()
    assert keys and all(k.startswith("inputs.") for k in keys)
    res = _item(dlg, "results.summary")
    assert not (res.flags() & Qt.ItemIsUserCheckable)
    assert "run a simulation" in res.toolTip(0).lower()
    assert "inputs.blocks_all" not in keys           # long: off by default


def test_after_a_run_results_are_offered_and_long_ones_are_off(ran):
    dlg = _dialog(ran)
    keys = set(dlg.selected_keys())
    assert {"results.summary", "results.per_run", "inputs.blocks"} <= keys
    assert not keys & {"results.event_log", "results.end_of_run",
                       "results.interim_cost"}
    assert _item(dlg, "results.event_log").flags() & Qt.ItemIsUserCheckable


def test_select_all_none_defaults(ran):
    dlg = _dialog(ran)
    dlg.select_all()
    assert "results.event_log" in dlg.selected_keys()
    dlg.select_none()
    assert dlg.selected_keys() == []
    assert not dlg.export_button.isEnabled()
    assert "at least one table" in dlg.preview.text()
    dlg.select_defaults()
    assert "results.event_log" not in dlg.selected_keys()


def test_group_rows_show_all_some_or_none(ran):
    dlg = _dialog(ran)
    inputs = dlg.tree.topLevelItem(0)
    dlg.select_none()
    assert inputs.checkState(0) == Qt.Unchecked
    dlg.select_all()
    assert inputs.checkState(0) == Qt.Checked
    _item(dlg, "inputs.blocks").setCheckState(0, Qt.Unchecked)
    assert inputs.checkState(0) == Qt.PartiallyChecked


def test_the_export_button_needs_a_format_and_a_folder(win):
    dlg = _dialog(win)
    for cb in dlg.format_boxes.values():
        cb.setChecked(False)
    assert not dlg.export_button.isEnabled()
    assert "at least one format" in dlg.preview.text()
    dlg.format_boxes["csv"].setChecked(True)
    assert dlg.export_button.isEnabled()
    dlg.folder_edit.setText("  ")
    assert not dlg.export_button.isEnabled()


def test_the_preview_counts_files_and_warns_about_overwriting(win, tmp_path):
    dlg = _dialog(win)
    dlg.folder_edit.setText(str(tmp_path))
    for key, cb in dlg.format_boxes.items():
        cb.setChecked(key == "csv")
    n = len(dlg.selected_keys())
    assert f"{n} table(s) → {n} file(s)" in dlg.preview.text()
    assert "replaced" not in dlg.preview.text()
    (tmp_path / "input_inputs_blocks.csv").write_text("x")
    dlg._update_preview()
    assert "1 existing file(s) will be replaced" in dlg.preview.text()


def test_the_default_folder_is_tables_beside_the_model(win):
    dlg = _dialog(win)
    assert dlg.folder() == (Path(MODEL).resolve().parent / "Tables")


# -- exporting -------------------------------------------------------------------------

def test_export_writes_the_chosen_tables_in_the_chosen_formats(ran, tmp_path):
    from raptor.gui.ui.tables_dialog import perform_export
    dlg = _dialog(ran)
    dlg.folder_edit.setText(str(tmp_path / "Tables"))
    for key, cb in dlg.format_boxes.items():
        cb.setChecked(key in ("csv", "xlsx", "txt_report"))
    n = len(dlg.selected_keys())
    result = perform_export(ran, dlg)
    assert result is not None and result.tables == n
    names = sorted(f.name for f in result.files)
    assert "input_tables.xlsx" in names and "input_tables.txt" in names
    assert "input_results_summary.csv" in names
    assert len(names) == n + 2
    assert all(f.exists() for f in result.files)
    assert "Exported" in ran._statusbar.currentMessage()


def test_export_includes_the_current_unsaved_edits(win, tmp_path):
    from raptor.gui.ui.tables_dialog import perform_export
    v = win._diagram_dock.view
    bid = next(b for b in sorted(v.blocks) if b)
    v.rename_block(bid, "Renamed_By_User") if hasattr(v, "rename_block") \
        else v._apply_rename("block", bid, "Renamed_By_User")
    dlg = _dialog(win)
    dlg.folder_edit.setText(str(tmp_path))
    for key, cb in dlg.format_boxes.items():
        cb.setChecked(key == "csv")
    result = perform_export(win, dlg)
    blocks = next(f for f in result.files if f.name.endswith("inputs_blocks.csv"))
    assert "Renamed_By_User" in blocks.read_text(encoding="utf-8-sig")


def test_choices_are_remembered(win, tmp_path):
    from raptor.gui.ui import settings
    from raptor.gui.ui.tables_dialog import perform_export
    dlg = _dialog(win)
    dlg.folder_edit.setText(str(tmp_path))
    for key, cb in dlg.format_boxes.items():
        cb.setChecked(key in ("txt_files", "csv"))
    perform_export(win, dlg)
    assert settings.get_bool("tables/csv") and settings.get_bool(
        "tables/txt_files")
    assert not settings.get_bool("tables/xlsx")
    again = _dialog(win)
    assert again.format_boxes["csv"].isChecked()
    assert not again.format_boxes["xlsx"].isChecked()


def test_open_folder_is_only_done_when_asked(win, tmp_path, monkeypatch):
    from raptor.gui.ui import tables_dialog
    from raptor.gui.ui.tables_dialog import perform_export
    opened = []
    monkeypatch.setattr(tables_dialog.QDesktopServices, "openUrl",
                        staticmethod(lambda url: opened.append(url) or True))
    dlg = _dialog(win)
    dlg.folder_edit.setText(str(tmp_path))
    dlg.open_after.setChecked(False)
    perform_export(win, dlg)
    assert opened == []
    dlg.open_after.setChecked(True)
    perform_export(win, dlg)
    assert len(opened) == 1


def test_a_write_failure_is_reported_not_raised(win, tmp_path, monkeypatch):
    from raptor.gui.ui.tables_dialog import perform_export
    shown = []
    monkeypatch.setattr(QMessageBox, "critical",
                        staticmethod(lambda *a, **k: shown.append(a[2])))
    blocker = tmp_path / "afile"
    blocker.write_text("not a folder")
    dlg = _dialog(win)
    dlg.folder_edit.setText(str(blocker))          # a file, not a folder
    assert perform_export(win, dlg) is None
    assert shown and "Could not write the tables" in shown[0]
    assert "failed" in win._statusbar.currentMessage()


# -- the data it is given --------------------------------------------------------------

def test_the_results_bundle_matches_the_run_on_screen(ran):
    b = ran._results.results_bundle(model_name="m", units="hours")
    assert b.num_runs == 3 and len(b.per_run) == 3
    assert any("SUMMARY" in ln for ln in b.final_lines)
    assert len(b.event_logs) == 3
    assert b.end_of_run_lines and b.units == "hours"


def test_there_is_no_bundle_before_a_run(win):
    assert win._results.results_bundle() is None


# -- the buttons on the docks --------------------------------------------------------

def test_the_results_and_data_tables_docks_open_the_same_dialog(win,
                                                                monkeypatch):
    from raptor.gui.ui import tables_dialog
    opened = []
    monkeypatch.setattr(tables_dialog, "open_export_tables_dialog",
                        lambda w: opened.append(w))
    win._results.export_tables_requested.emit()
    win._data_tables.export_tables_requested.emit()
    assert opened == [win, win]


def test_the_docks_have_an_export_tables_button(win):
    from PySide6.QtWidgets import QPushButton
    for dock in (win._results, win._data_tables):
        texts = [b.text() for b in dock.findChildren(QPushButton)]
        assert "Export Tables…" in texts, type(dock).__name__
