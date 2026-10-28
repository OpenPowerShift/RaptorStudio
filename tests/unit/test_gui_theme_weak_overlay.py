"""A light/dark flip must not repaint the weak-link bands with the run's cached end-of-run block states.

After a run the diagram keeps the last live colours cached; `_retheme_current` re-applied them AFTER the result
overlay, so flipping the theme turned every block red (and flipping back left them red)."""
from __future__ import annotations

import os
from pathlib import Path

import pytest

pytest.importorskip("PySide6")
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication

from raptor.core.fileio.rbds import load_rbds
from raptor.gui.resources.design_tokens import ThemeManager
from raptor.gui.ui.main_window import MainWindow

pytestmark = pytest.mark.gui
ROOT = Path(__file__).resolve().parents[2]


@pytest.fixture(scope="module")
def qapp():
    return QApplication.instance() or QApplication([])


def test_weak_link_bands_survive_a_theme_flip(qapp):
    w = MainWindow()
    path = ROOT / "new_docs" / "models" / "ProdLine1.rbds"
    w._load_into_app(load_rbds(str(path)), str(path))
    w._run_panel._analysis_checks["weak"].setChecked(True)
    w._run_count_spin.setValue(2)
    w._dirty = False          # ticking the analysis boxes edits the model; do not ask about saving
    w._confirm_long_batch = lambda _n: True
    w._run_simulation()
    qapp.processEvents()
    view = w._diagram_dock.view
    assert not view._weak_colors_on
    w._toggle_weak_colors()          # runs no longer auto-enable overlays
    assert view._weak_colors_on
    item = view._block_items[5]
    try:
        light = item.brush().color().name()
        ThemeManager.instance().set_dark(True)
        qapp.processEvents()
        dark = item.brush().color().name()
        assert dark == ThemeManager.instance().colour["result_green"].lower()
        ThemeManager.instance().set_dark(False)
        qapp.processEvents()
        assert item.brush().color().name() == light
    finally:
        ThemeManager.instance().set_dark(False)
        w._dirty = False
