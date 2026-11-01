"""GUI: the analytical dialog and the age fields in the Inspector."""
from __future__ import annotations

import os

import pytest

pytest.importorskip("PySide6")

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication, QWidget

from raptor.core.fileio.rbds import load_rbds, save_rbds
from raptor.core.fileio.typed import block_from_row
from raptor.gui.ui.analytic_dialog import AnalyticDialog
from raptor.gui.ui.diagram import DiagramView
from raptor.gui.ui.derived_dialog import DerivedResultsDialog

REPO = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
MODELS = os.path.join(REPO, "new_docs", "models")


@pytest.fixture(scope="module")
def qapp():
    return QApplication.instance() or QApplication([])


def _dialog(qapp, name, *, current_file=None):
    class Win(QWidget):
        _rbd = load_rbds(os.path.join(MODELS, f"{name}.rbds"))
        _current_file = current_file

    win = Win()
    dialog = AnalyticDialog(win)
    dialog.wait_for_result()
    return win, dialog


def _card_texts(dialog):
    from PySide6.QtWidgets import QLabel
    return [label.text() for label in dialog._summary_page.findChildren(QLabel)
            if label.objectName() == "MetricValue"]


def test_analytic_dialog_computes_reliability(qapp):
    _win, dialog = _dialog(qapp, "Lantern1")
    assert dialog._pages.currentIndex() == 1
    assert dialog._result["reliability"] == pytest.approx(0.38460369, rel=1e-6)
    assert any(text.startswith("0.384604") for text in _card_texts(dialog))
    # Per-element table: every element, sorted by criticality, N/A last.
    table = dialog._elements
    assert table.rowCount() == 3
    assert table.item(0, 0).text() == "Battery_Pack"
    assert dialog._tabs.isTabVisible(dialog._tab_curve)
    assert not dialog._tabs.isTabVisible(dialog._tab_equation)
    assert dialog._copy.isEnabled()
    dialog.close()


def test_analytic_dialog_switches_to_availability(qapp):
    _win, dialog = _dialog(qapp, "Vendo1")
    dialog._mode.setCurrentIndex(1)
    dialog.compute()
    dialog.wait_for_result()
    assert dialog._result["mode"] == "availability"
    assert dialog._result["availability"] == pytest.approx(0.9706853, rel=1e-6)
    assert any(text.startswith("0.970685") for text in _card_texts(dialog))
    # Reliability-only settings are hidden, and so is the curve tab.
    assert dialog._times.isHidden() or not dialog._times.isVisibleTo(dialog)
    assert not dialog._tabs.isTabVisible(dialog._tab_curve)
    assert dialog._elements.horizontalHeaderItem(3).text() == "MTTR"
    dialog.close()


def test_analytic_dialog_refuses_phases_without_a_traceback(qapp):
    _win, dialog = _dialog(qapp, "AirlinerMission")
    assert dialog._pages.currentIndex() == 0
    assert "outside the static solver" in dialog._notice_title.text()
    assert "hased models" in dialog._notice_body.text()
    assert not dialog._copy.isEnabled()
    dialog.close()


def test_analytic_dialog_explains_a_bad_setting(qapp):
    _win, dialog = _dialog(qapp, "Lantern1")
    dialog._bx.setText("ten")
    dialog.compute()
    dialog.wait_for_result()
    assert dialog._pages.currentIndex() == 0
    assert "numbers" in dialog._notice_body.text()
    dialog._bx.setText("120")
    dialog.compute()
    dialog.wait_for_result()
    assert "BX percentage" in dialog._notice_body.text()
    dialog.close()


def test_analytic_dialog_flags_stale_settings(qapp):
    _win, dialog = _dialog(qapp, "Lantern1")
    dialog._bx.setText("5")
    assert "press Compute" in dialog._banner.text()
    dialog.compute()
    dialog.wait_for_result()
    assert list(dialog._result["bx"]) == ["5"]
    assert "press Compute" not in dialog._banner.text()
    dialog.close()


def test_analytic_dialog_equation_tab_and_copy(qapp):
    _win, dialog = _dialog(qapp, "Lantern1")
    dialog._equation.setChecked(True)
    dialog.compute()
    dialog.wait_for_result()
    assert dialog._tabs.isTabVisible(dialog._tab_equation)
    assert "R_Switch" in dialog._equation_view.toPlainText()
    dialog._copy_result()
    assert "Battery_Pack" in QApplication.clipboard().text()
    dialog.close()


def test_analytic_dialog_non_time_terminated_asks_for_a_mission_time(qapp):
    class Win(QWidget):
        _rbd = load_rbds(os.path.join(MODELS, "Lantern1.rbds"))

    Win._rbd.sections["SYSTEM_DATA"].rows[0]["TerminationType"] = "FailSim"
    dialog = AnalyticDialog(Win())
    dialog.wait_for_result()
    assert dialog._pages.currentIndex() == 0
    assert "mission time" in dialog._notice_title.text().lower()
    dialog._at.setValue(48.0)
    dialog.compute()
    dialog.wait_for_result()
    assert dialog._pages.currentIndex() == 1
    dialog.close()


def test_analytic_dialog_does_not_change_the_model(qapp):
    win, dialog = _dialog(qapp, "Vendo1")
    from raptor.core.fileio.rbds import render_rbds
    before = render_rbds(win._rbd)
    dialog.compute()
    dialog.wait_for_result()
    assert render_rbds(win._rbd) == before
    dialog.close()


def test_age_fields_survive_undo_and_rbds_round_trip(qapp):
    view = DiagramView()
    view.load_rbd(load_rbds(os.path.join(MODELS, "Lantern1.rbds")))
    bid = sorted(view.blocks)[0]
    view.set_block_duty_cycle(bid, 0.4)
    view.set_block_start_age(bid, 12.5)
    view.set_block_restoration_factor(bid, 0.75)
    view.set_block_restoration_type(bid, 2)
    block = view.blocks[bid]
    assert block.duty_cycle == pytest.approx(0.4)
    assert block.start_age == pytest.approx(12.5)
    assert block.restoration_factor == pytest.approx(0.75)
    assert block.restoration_type == 2

    view._stack.undo()
    assert view.blocks[bid].restoration_type == 1
    view._stack.redo()
    assert view.blocks[bid].restoration_type == 2

    view.write_back_to_rbd(view._rbd_ref)
    path = os.path.join(os.path.dirname(__file__), "age_roundtrip.rbds")
    try:
        save_rbds(view._rbd_ref, path)
        again = load_rbds(path)
        row = next(r for r in again.sections["BLOCK_DATA"].rows
                   if int(float(r["ID"])) == bid)
        reloaded = block_from_row(row)
        assert reloaded.duty_cycle == pytest.approx(0.4)
        assert reloaded.start_age == pytest.approx(12.5)
        assert reloaded.restoration_factor == pytest.approx(0.75)
        assert reloaded.restoration_type == 2
    finally:
        if os.path.exists(path):
            os.unlink(path)


def test_derived_results_dialog_shows_classifications_and_criticality(qapp):
    from types import SimpleNamespace

    report = SimpleNamespace(
        availability=0.97, inherent_availability=0.98,
        achieved_availability=0.975, mttff=100.0,
        mtbf_total=200.0, mtbe_total=250.0,
        num_component_cms=3, num_component_pms=1,
        red_corrective_time=5.0, red_pm_time=1.0,
        red_logistics_time=0.0, red_done_time=0.0, red_other_time=0.0,
        criticality={1: {"name": "Pump", "failure": 0.6,
                         "downtime": 0.4, "cost": 0.2,
                         "downing_event": None}})

    class Results:
        _report_data = [report]

    class Win(QWidget):
        _results = Results()

    win = Win()
    dialog = DerivedResultsDialog(win, [report])
    from PySide6.QtWidgets import QTabWidget
    tabs = dialog.findChild(QTabWidget)
    assert tabs is not None
    assert tabs.count() == 2
    dialog.close()
