"""System flow/cost values live in the Run panel, not Project Settings.

The panel is still a view: it emits the SYSTEM_DATA edit and MainWindow
writes the row through its one undoable path. These tests pin both the
visibility rules (capacity gates flow, cost gates the lost-flow price)
and the removal from the Inspector's Project Settings page.
"""
from __future__ import annotations

import pytest
from PySide6.QtWidgets import QApplication, QGroupBox, QLabel

pytestmark = pytest.mark.gui

MODEL = "tests/golden/TwoBlockRedundancy/input.rbd"


@pytest.fixture
def win():
    from raptor.core.fileio.rbd import load_rbd
    from raptor.gui.ui.main_window import MainWindow

    QApplication.instance() or QApplication([])
    w = MainWindow()
    w._load_into_app(load_rbd(MODEL), MODEL)
    yield w
    w._dirty = False
    w.close()


def _row(win):
    return win._rbd.sections["SYSTEM_DATA"].rows[0]


def test_loading_a_model_fills_the_panel_system_fields(win):
    from raptor.core.fileio.typed import system_config_from_row

    cfg = system_config_from_row(_row(win))
    panel = win._run_panel
    assert panel._flow_gen.value() == cfg.flow_generated
    assert panel._system_red_cost_field.value() == pytest.approx(
        cfg.system_red_cost)
    assert panel._lost_flow_cost_field.value() == pytest.approx(
        cfg.lost_flow_unit_cost)


def test_flow_generated_edit_writes_system_data(win):
    panel = win._run_panel
    panel._analysis_checks["capacity"].setChecked(True)
    assert panel._system_form.isRowVisible(0)

    panel._flow_gen.setValue(250)
    panel._flow_gen.editingFinished.emit()

    assert _row(win)["flowGenerated"] == "250"
    assert panel._flow_gen.value() == 250


def test_cost_edits_write_system_data(win):
    panel = win._run_panel
    panel._analysis_checks["capacity"].setChecked(True)
    panel._analysis_checks["cost"].setChecked(True)
    assert panel._system_form.isRowVisible(2)

    panel._system_red_cost_field.setValue(12.5)
    panel._system_red_cost_field.editingFinished.emit()
    panel._lost_flow_cost_field.setValue(3.75)
    panel._lost_flow_cost_field.editingFinished.emit()

    assert float(_row(win)["systemRedCost"]) == pytest.approx(12.5)
    assert float(_row(win)["sysLostCost"]) == pytest.approx(3.75)


def test_project_settings_no_longer_show_the_moved_values(win):
    win._inspector.show_system_config()
    labels = [label.text() for label in
              win._inspector.findChildren(QLabel)]
    groups = [box.title() for box in
              win._inspector.findChildren(QGroupBox)]
    assert not any("Flow generated" in text for text in labels)
    assert not any("System red cost" in text for text in labels)
    assert not any("Cost of lost flow" in text for text in labels)
    assert "System Costs" not in groups
