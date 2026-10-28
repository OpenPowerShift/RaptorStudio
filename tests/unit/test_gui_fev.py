"""Failure Effects View in the main window: entering it without a run, clicking
elements, the phase arrows and erasers, hatching, and the Interactive Log
(which must write the same bytes as a real session)."""
from __future__ import annotations

from pathlib import Path

import pytest

from raptor.core.fileio.rbd import load_rbd

GOLD = Path(__file__).resolve().parents[1] / "golden"
PHASED = GOLD / "FevPhasing3"


@pytest.fixture(scope="module")
def qapp():
    from PySide6.QtWidgets import QApplication
    return QApplication.instance() or QApplication([])


@pytest.fixture
def win(qapp):
    from raptor.gui.ui.main_window import MainWindow
    w = MainWindow()
    w.resize(1600, 900)
    w.show()
    qapp.processEvents()
    path = str(PHASED / "input.rbd")
    w._load_into_app(load_rbd(path), path)
    w._dirty = False
    qapp.processEvents()
    yield w
    w._leave_fev()
    w._dirty = False
    w.close()


def _id(win, name):
    return win._fev.block_by_name(name).id


def test_it_opens_without_a_run_and_closes(win):
    assert win._fev is None
    win._fev_view_action.setChecked(True)
    assert win._fev is not None and win._fev.phase_number == 1
    assert win._fev_erase_all_action.isEnabled()
    assert win._fev_phase_next_action.isEnabled()
    # A new run remains available from FEV; MainWindow leaves FEV first.
    assert win._run_action.isEnabled()
    assert "Failure Effects View is open" not in win._run_action.toolTip()
    win._fev_view_action.setChecked(False)
    assert win._fev is None
    assert win._run_action.isEnabled()


def test_a_click_fails_a_block_and_the_canvas_follows(win):
    win._fev_view_action.setChecked(True)
    win._on_fev_phase_next()
    assert win._fev.phase_number == 2
    view = win._diagram_dock.view
    # The phase-1 Cut blocks come back hatched (induced) in phase 2.
    hatched = {b.name for b in win._fev.fev_dep_group}
    assert {"SolarP_500W", "BatteriesNiCd", "Bus_UR"} <= hatched
    win._on_fev_target_clicked(_id(win, "Thermal"), "click")
    assert win._fev.system_color.value == "Red"
    # Attitude is induced to fail by the system going Red: drawn hatched.
    assert _id(win, "Attitude") in view._block_items
    assert view._block_items[_id(win, "Attitude")]._fev_hatch
    assert not view._block_items[_id(win, "Thermal")]._fev_hatch
    assert "System Red" in win._statusbar.currentMessage()
    win._on_fev_erase_phase()
    assert win._fev.system_color.value == "Green"
    assert win._fev.phase_number == 2
    win._on_fev_erase_all()
    assert win._fev.phase_number == 1


def test_fail_and_repair_buttons_open_the_view_and_arm_the_click(win):
    win._on_fev_fail()
    assert win._fev is not None
    assert win._diagram_dock.view._fev_armed_mode == "fail"
    win._on_fev_target_clicked(_id(win, "Thermal"), "fail")
    assert win._fev.block_by_name("Thermal").op_status.value == "Repairing"
    assert win._diagram_dock.view._fev_armed_mode == "click"     # back to plain clicking
    win._on_fev_target_clicked(_id(win, "Thermal"), "repair")
    assert win._fev.block_by_name("Thermal").op_status.value == "Running"


def test_editing_the_model_leaves_the_view(win):
    win._fev_view_action.setChecked(True)
    win._invalidate_step_session()           # what any edit does
    assert win._fev is None
    assert not win._fev_view_action.isChecked()


def test_the_interactive_log_matches_the_real_one(win, tmp_path, monkeypatch):
    from raptor.gui.ui import filedialogs as fdg
    log = tmp_path / "log.txt"
    monkeypatch.setattr(fdg, "getSaveFileName", lambda *a, **k: (str(log), ""))
    win._fev_view_action.setChecked(True)
    win._on_fev_log_open()
    assert win._fev_log_write_action.isEnabled()
    win._on_fev_log_write()                                   # W1
    win._on_fev_phase_next(); win._on_fev_log_write()         # W2
    win._on_fev_target_clicked(_id(win, "Thermal"), "click"); win._on_fev_log_write()   # W3
    win._on_fev_erase_phase(); win._on_fev_log_write()        # W4
    win._on_fev_phase_next(); win._on_fev_log_write()         # W5
    win._on_fev_phase_next(); win._on_fev_log_write()         # W6
    win._on_fev_phase_prev(); win._on_fev_log_write()         # W7
    win._on_fev_erase_all(); win._on_fev_log_write()          # W8
    win._on_fev_log_close()
    real = PHASED / "legacy" / "fevlog_FevPhasing3.txt"
    assert log.read_bytes() == real.read_bytes()
    assert not win._fev_log_write_action.isEnabled()


def test_a_model_fev_cannot_start_is_reported_not_crashed(win, monkeypatch):
    from raptor.core.engine.errors import UnrunnableModel
    from raptor.core.engine.model_checks import ModelProblem
    from PySide6.QtWidgets import QMessageBox
    shown = []
    monkeypatch.setattr(QMessageBox, "warning", lambda *a, **k: shown.append(a[2]))

    def boom(_rbd):
        raise UnrunnableModel([ModelProblem("model", "nope")])
    monkeypatch.setattr("raptor.core.engine.fev.FevSession", boom)
    win._fev_view_action.setChecked(True)
    assert win._fev is None and shown
    assert not win._fev_view_action.isChecked()


def test_go_to_phase_lists_every_phase_and_goes_straight_there(win, monkeypatch):
    win._fev_view_action.setChecked(True)
    seen = {}

    def choose(labels, current):
        seen["labels"], seen["current"] = labels, current
        return 4
    monkeypatch.setattr(win, "_choose_fev_phase", choose)
    assert win._fev_goto_phase_action.isEnabled()
    win._fev_goto_phase_action.trigger()
    assert win._fev.phase_number == 4 and seen["current"] == 1
    assert len(seen["labels"]) == len(win._fev.model.phases)
    assert seen["labels"][0].startswith("001  ")
    # cancelling changes nothing; going to the phase it is already in is allowed
    monkeypatch.setattr(win, "_choose_fev_phase", lambda labels, current: None)
    win._fev_goto_phase_action.trigger()
    assert win._fev.phase_number == 4
    monkeypatch.setattr(win, "_choose_fev_phase", lambda labels, current: 1)
    win._fev_goto_phase_action.trigger()
    assert win._fev.phase_number == 1


def test_right_click_in_failure_effects_view_shows_the_originals_menu(win, monkeypatch):
    shown = []
    monkeypatch.setattr(win, "_exec_fev_menu", lambda menu, pos: shown.append(menu))
    view = win._diagram_dock.view
    # not in FEV: the ordinary canvas menu, not this one
    win._fev_view_action.setChecked(True)
    view.fev_menu_requested.emit(None)
    assert len(shown) == 1
    menu = shown[0]
    items = {a.text(): a.isEnabled() for a in menu.actions()}
    assert list(items) == ["Write to Log", "Display Failure Effects…", "Refresh Phase", "Refresh All", "Home", "Up",
                           "Center-zoom"]
    # no log open, a phased model, at the top level
    assert items["Write to Log"] is False and items["Refresh Phase"] is True
    assert items["Home"] is False and items["Up"] is False
    # an entry does what the ribbon's does
    win._on_fev_phase_next()
    assert win._fev.phase_number == 2
    next(a for a in menu.actions() if a.text() == "Refresh All").trigger()
    assert win._fev.phase_number == 1


def test_the_canvas_hands_a_right_click_to_the_fev_menu_only_while_it_is_open(win, monkeypatch):
    monkeypatch.setattr(win, "_exec_fev_menu", lambda menu, pos: None)   # never a real QMenu.exec
    from PySide6.QtCore import QPointF

    class _Ev:
        accepted = False

        def position(self):
            return QPointF(10, 10)

        def accept(self):
            self.accepted = True
    view = win._diagram_dock.view
    got = []
    view.fev_menu_requested.connect(got.append)
    win._fev_view_action.setChecked(True)
    ev = _Ev()
    view._show_context_menu(ev)
    assert len(got) == 1 and ev.accepted
