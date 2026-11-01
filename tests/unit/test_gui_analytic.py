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


def test_analytic_dialog_computes_reliability(qapp):
    class Win(QWidget):
        _rbd = load_rbds(os.path.join(MODELS, "Lantern1.rbds"))

    dialog = AnalyticDialog(Win())
    text = dialog._output.toPlainText()
    assert "reliability:" in text
    assert "MTTF:" in text
    assert "Importance:" in text
    assert "Not applicable" not in text
    dialog.close()


def test_analytic_dialog_switches_to_availability(qapp):
    class Win(QWidget):
        _rbd = load_rbds(os.path.join(MODELS, "Vendo1.rbds"))

    dialog = AnalyticDialog(Win())
    dialog._mode.setCurrentIndex(1)
    dialog.compute()
    text = dialog._output.toPlainText()
    assert "availability:" in text
    assert "0.970" in text
    dialog.close()


def test_analytic_dialog_refuses_phases_without_a_traceback(qapp):
    class Win(QWidget):
        _rbd = load_rbds(os.path.join(MODELS, "AirlinerMission.rbds"))

    dialog = AnalyticDialog(Win())
    text = dialog._output.toPlainText()
    assert "Not applicable" in text
    assert "phased" in text
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
