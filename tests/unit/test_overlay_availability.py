"""Show on Canvas layers: unusable ones are disabled, italic and explained."""
from __future__ import annotations

import os
from pathlib import Path
from types import SimpleNamespace

import pytest

pytest.importorskip("PySide6")
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from raptor.gui.overlay_layers import LAYERS, layer_availability

IDS = [layer.id for layer in LAYERS]
RICH = SimpleNamespace(weak_stats={1: {}}, capacity=object(), cost=object())
BARE = SimpleNamespace(weak_stats={}, capacity=None, cost=None)
ALL_FLAGS = dict(has_event_log=True, has_importance=True,
                 has_element_findings=True)


@pytest.mark.parametrize("layer_id", IDS)
def test_every_layer_has_a_rule_and_a_reason_before_any_run(layer_id):
    reason = layer_availability(layer_id, [])
    assert isinstance(reason, str) and reason


@pytest.mark.parametrize("layer_id", IDS)
def test_every_layer_is_usable_with_everything_present(layer_id):
    assert layer_availability(layer_id, [RICH], **ALL_FLAGS) is None


@pytest.mark.parametrize("layer_id", IDS)
def test_every_layer_is_refused_for_bare_results(layer_id):
    assert layer_availability(layer_id, [BARE]) is not None


@pytest.mark.parametrize("layer_id, flag", [
    ("downtime", "has_event_log"), ("lost_capacity", "has_event_log"),
    ("importance", "has_importance"), ("advisor", "has_element_findings")])
def test_flag_layers_need_their_flag(layer_id, flag):
    flags = {**ALL_FLAGS, flag: False}
    assert layer_availability(layer_id, [RICH], **flags) is not None


@pytest.mark.parametrize("layer_id, field", [
    ("weak_link", "weak_stats"), ("spares", "weak_stats"),
    ("capacity", "capacity"), ("lost_capacity", "capacity"),
    ("cost", "cost")])
def test_data_layers_need_their_field(layer_id, field):
    run = SimpleNamespace(**{**vars(RICH),
                             field: {} if field == "weak_stats" else None})
    assert layer_availability(layer_id, [run], **ALL_FLAGS) is not None


def test_cost_reason_names_the_cause():
    assert "No costs" in layer_availability("cost", [BARE])


def test_unknown_layer_raises():
    with pytest.raises(KeyError):
        layer_availability("nope", [RICH])


# --- GUI ------------------------------------------------------------------
from PySide6.QtWidgets import QApplication

from raptor.core.fileio.rbd import load_rbd
from raptor.gui.ui.main_window import MainWindow

GOLD = Path(__file__).resolve().parents[1] / "golden"


@pytest.fixture(scope="module")
def qapp():
    return QApplication.instance() or QApplication([])


def _run(win, qapp, name):
    win._load_into_app(load_rbd(str(GOLD / name / "input.rbd")), name)
    win._run_count_spin.setValue(1)
    win._run_simulation()
    qapp.processEvents()


def _item(win, label):
    combo = win._overlay_combo
    index = combo.findText(label)
    return combo.model().item(index), index


def test_cost_layer_disabled_italic_with_reason_then_enabled(qapp):
    win = MainWindow()
    _run(win, qapp, "TwoBlockRedundancy")
    item, _ = _item(win, "Cost")
    assert not item.isEnabled()
    assert item.font().italic()
    assert "No costs" in item.toolTip()
    assert item.statusTip() == item.toolTip()
    _run(win, qapp, "Capacity3")  # a costed model
    item, _ = _item(win, "Cost")
    assert item.isEnabled()
    assert not item.font().italic()
    assert item.toolTip() == ""


def test_selected_layer_falls_back_when_results_go(qapp):
    win = MainWindow()
    _run(win, qapp, "Capacity3")
    win._cost_overlay_action.setChecked(True)
    assert win._overlay_combo.currentText() == "Cost"
    _run(win, qapp, "TwoBlockRedundancy")
    assert not win._cost_overlay_action.isChecked()
    assert win._overlay_combo.currentIndex() == 0


def test_greyed_layers_follow_a_theme_flip(qapp):
    """The stylesheet hides the palette's disabled colour, so the combo
    greys unusable layers itself, and must re-grey them for a new theme."""
    from PySide6.QtCore import Qt
    from raptor.gui.resources.design_tokens import ThemeManager
    win = MainWindow()
    _run(win, qapp, "TwoBlockRedundancy")
    theme = ThemeManager.instance()
    try:
        for dark in (True, False):
            theme.set_dark(dark)
            qapp.processEvents()
            cost, _ = _item(win, "Cost")
            assert cost.foreground().color().name().lower() == \
                theme.colour["muted"].lower()
            usable, _ = _item(win, "Share of downtime")
            assert usable.data(Qt.ItemDataRole.ForegroundRole) is None
    finally:
        theme.set_dark(False)
        win._dirty = False
