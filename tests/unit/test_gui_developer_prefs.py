"""Preferences > Developer: the original's hidden switches as ordinary options."""
from __future__ import annotations

import os

import pytest
from raptor.gui.ui import filedialogs as _fdg

pytest.importorskip("PySide6")
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QSettings
from PySide6.QtWidgets import (QApplication, QCheckBox, QDialog, QSpinBox,
                               QDoubleSpinBox)

from raptor.core import dev_options as d
from raptor.core.fileio.rbd import load_rbd
from raptor.gui import dev_settings

REPO = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


@pytest.fixture(scope="module")
def qapp():
    return QApplication.instance() or QApplication([])


@pytest.fixture(autouse=True)
def clean_dev_settings():
    dev_settings.clear()
    yield
    dev_settings.clear()


def _open_prefs(qapp, monkeypatch, mutate):
    from raptor.gui.ui.main_window import MainWindow
    w = MainWindow()
    w._load_into_app(load_rbd(os.path.join(
        REPO, "tests", "golden", "TwoBlockRedundancy", "input.rbd")), "x")

    def fake_exec(self):
        QApplication.processEvents()
        mutate(self)
        return QDialog.Accepted
    monkeypatch.setattr(QDialog, "exec", fake_exec)
    w._show_preferences()
    return w


def test_defaults_are_all_off(qapp):
    assert dev_settings.current_options() == d.DeveloperOptions()


def test_the_dialog_has_a_developer_tab_with_every_option(qapp, monkeypatch):
    seen = {}

    def mutate(dlg):
        from raptor.gui.ui.dialogs import _SectionPages
        tabs = dlg.findChild(_SectionPages)
        seen["tabs"] = [tabs.tabText(i) for i in range(tabs.count())]
        page = tabs.widget(tabs.count() - 1)
        seen["bools"] = len(page.findChildren(QCheckBox))
        seen["ints"] = len(page.findChildren(QSpinBox))
        seen["floats"] = len(page.findChildren(QDoubleSpinBox))
    _open_prefs(qapp, monkeypatch, mutate)
    assert seen["tabs"][-1] == "Developer"
    kinds = [f.kind for f in d.DEV_FEATURES]
    # bool rows + the 4 category boxes of the 'set' row
    assert seen["bools"] == kinds.count("bool") + len(d.EVENT_CATEGORIES)
    assert seen["ints"] == kinds.count("int")
    assert seen["floats"] == kinds.count("float")


def test_ticking_options_stores_them_and_changes_the_options(
        qapp, monkeypatch):
    def mutate(dlg):
        from raptor.gui.ui.dialogs import _SectionPages
        tabs = dlg.findChild(_SectionPages)
        page = tabs.widget(tabs.count() - 1)
        for cb in page.findChildren(QCheckBox):
            if cb.text() in ("Random draw count in Final Results",
                             "Block", "System"):
                cb.setChecked(True)
        page.findChildren(QSpinBox)[0].setValue(5)
    _open_prefs(qapp, monkeypatch, mutate)
    o = dev_settings.current_options()
    assert o.random_draw_count is True
    assert o.event_log_filter == frozenset({"block", "system"})
    assert o.cumulative_by_trial == 5
    assert o.domain_tree is False
    assert QSettings().value("dev/random_draw_count", False, type=bool) is True


def test_a_second_visit_shows_what_was_stored(qapp, monkeypatch):
    dev_settings.store({"show_criticality": True,
                        "event_log_filter": "node"})
    seen = {}

    def mutate(dlg):
        from raptor.gui.ui.dialogs import _SectionPages
        tabs = dlg.findChild(_SectionPages)
        page = tabs.widget(tabs.count() - 1)
        seen["on"] = sorted(cb.text() for cb in page.findChildren(QCheckBox)
                            if cb.isChecked())
    _open_prefs(qapp, monkeypatch, mutate)
    assert seen["on"] == ["Node", "Show criticality"]


def test_environment_overrides_the_stored_value(qapp):
    dev_settings.store({"random_draw_count": False})
    o = dev_settings.current_options(
        {"RAPTOR_DEV_FEATURES": "random_draw_count,cumulative_by_trial=3"})
    assert o.random_draw_count is True and o.cumulative_by_trial == 3


def test_a_corrupt_stored_value_is_ignored_not_fatal(qapp):
    s = QSettings()
    s.setValue("dev/cumulative_by_trial", "not-a-number")
    s.setValue("dev/event_log_filter", "block,wrong")
    s.setValue("dev/domain_tree", "true")
    o = dev_settings.current_options()
    assert o.domain_tree is True
    assert o.cumulative_by_trial == 0 and o.event_log_filter == frozenset()


def test_event_log_filter_option_reaches_the_results_view(qapp, monkeypatch):
    from raptor.gui.ui.main_window import MainWindow
    monkeypatch.delenv("RAPTOR_DEV_FEATURES", raising=False)
    from raptor.core.engine.eventlog import format_event_log_line as f
    logs = [[f(0.0, "Block", "alpha", "Good", "x"), f(1.0, "Node", "n1", "Good", "y"),
             f(2.0, "System", "Phase_change", "Phase_change", "z")]]
    assert MainWindow._dev_filtered_event_logs(logs) is logs
    dev_settings.store({"event_log_filter": "node,phase"})
    out = MainWindow._dev_filtered_event_logs(logs)
    assert [l.type for l in out[0]] == ["Node", "System"]
    dev_settings.store({"event_log_filter": "", "event_log_component": "alpha"})
    out = MainWindow._dev_filtered_event_logs(logs)
    assert [l.name for l in out[0]] == ["alpha"]


def test_show_criticality_option_reaches_fev_and_spf(qapp, monkeypatch):
    from raptor.gui.ui.fev_mixin import FevMixin
    monkeypatch.delenv("RAPTOR_DEV_FEATURES", raising=False)
    assert FevMixin._fev_show_criticality() is False
    dev_settings.store({"show_criticality": True})
    assert FevMixin._fev_show_criticality() is True


def test_sim_details_path_is_beside_the_model_only_when_on(qapp, monkeypatch, tmp_path):
    from raptor.gui.ui.main_window import MainWindow
    monkeypatch.delenv("RAPTOR_DEV_FEATURES", raising=False)
    w = MainWindow()
    w._current_file = str(tmp_path / "m.rbd")
    assert w._sim_details_path() is None
    dev_settings.store({"sim_details": True})
    assert w._sim_details_path() == str(tmp_path / "simDetails.TXT")
    w._current_file = None
    assert w._sim_details_path() is None


def test_domain_tree_options_write_files_beside_the_model(qapp, monkeypatch, tmp_path):
    import shutil
    from raptor.gui.ui.main_window import MainWindow
    monkeypatch.delenv("RAPTOR_DEV_FEATURES", raising=False)
    model = tmp_path / "m.rbd"
    shutil.copy(os.path.join(REPO, "tests", "golden", "MotorDemo", "input.rbd"), model)
    w = MainWindow()
    w._load_into_app(load_rbd(str(model)), str(model))
    w._write_dev_trees()
    assert not (tmp_path / "reports").exists()
    dev_settings.store({"domain_tree": True, "core_file": True})
    w._write_dev_trees()
    text = (tmp_path / "reports" / "DomainTree.TXT").read_bytes().decode("latin-1")
    assert text.startswith("Raptor day month year = ") and "\r\n" in text and "nd end" in text
    assert (tmp_path / "reports" / "CoreFile.TXT").is_file()


def test_mission_file_option_asks_for_a_file(qapp, monkeypatch, tmp_path):
    from raptor.gui.ui.main_window import MainWindow
    monkeypatch.delenv("RAPTOR_DEV_FEATURES", raising=False)
    asked = []
    monkeypatch.setattr(_fdg, "getSaveFileName",
                        staticmethod(lambda *a, **k: (asked.append(a) or str(tmp_path / "m.txt"), "")))
    w = MainWindow()
    w._current_file = str(tmp_path / "model.rbd")
    assert w._ask_mission_path() is None and not asked
    dev_settings.store({"mission_file": True})
    assert w._ask_mission_path() == str(tmp_path / "m.txt")
    assert asked and asked[0][2].endswith("model_mission.txt")
    monkeypatch.setattr(_fdg, "getSaveFileName", staticmethod(lambda *a, **k: ("", "")))
    assert w._ask_mission_path() is None      # Cancel: no mission file this run


def test_inspector_ids_option_hooks_the_canvas(qapp, monkeypatch):
    from raptor.gui.ui.main_window import MainWindow
    monkeypatch.delenv("RAPTOR_DEV_FEATURES", raising=False)
    w = MainWindow()
    w._load_into_app(load_rbd(os.path.join(
        REPO, "tests", "golden", "MotorDemo", "input.rbd")), "x")
    assert w._diagram_dock.view.hover_inspector is None
    dev_settings.store({"inspector_ids": True})
    w._refresh_dev_hooks()
    hook = w._diagram_dock.view.hover_inspector
    assert hook is not None and "parentID = 0" in hook("block", 1)


def test_diagnostics_option_appends_beside_the_model(qapp, monkeypatch, tmp_path):
    from raptor.gui.ui.main_window import MainWindow
    monkeypatch.delenv("RAPTOR_DEV_FEATURES", raising=False)
    w = MainWindow()
    w._current_file = str(tmp_path / "m.rbd")
    w._diag("StartSimulation  x")
    assert not list(tmp_path.glob("*.TXT"))
    dev_settings.store({"diagnostics_log": True})
    w._diag("StartSimulation  x")
    w._diag("EndSimulation  y")
    (f,) = tmp_path.glob("*.TXT")
    text = f.read_bytes().decode()
    assert text.startswith("System today String =  ") and text.count("fileToOpen") == 1
    assert text.endswith("StartSimulation  x\r\nEndSimulation  y\r\n")


def test_negative_time_warning_dialog_sets_the_models_ignore_flag(qapp, monkeypatch):
    """The original's NegExpBox: shown after a batch that drew negative times, with its
    "Don't show this message again" box; ticking it sets `ShowNegErrMsg` (1 = ignore) in the model."""
    from PySide6.QtWidgets import QMessageBox
    from raptor.gui.ui.main_window import MainWindow
    shown = []

    def fake_exec(self):
        shown.append((self.windowTitle(), self.text()))
        self.checkBox().setChecked(True)
        return 0
    monkeypatch.setattr(QMessageBox, "exec", fake_exec)
    w = MainWindow()
    w._load_into_app(load_rbd(os.path.join(REPO, "tests", "golden", "NegDrawProbe", "input.rbd")), "x")
    notes: list[str] = []
    w._notify_negative_times([0, 0, 0], notes)
    assert not shown and not notes                    # nothing drawn: silent
    w._notify_negative_times([3, 0, 2], notes)
    assert shown and shown[0][0].startswith("WARNING:  Negative Repair or Failure Times")
    assert notes[0].startswith("Note: 5 negative time(s)")
    assert w._rbd.section("SYSTEM_DATA").rows[0]["ShowNegErrMsg"] == "1"
    shown.clear()
    w._notify_negative_times([3, 0, 2], notes)        # now ignored: no second dialog
    assert not shown
