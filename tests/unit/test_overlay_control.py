"""One "Show on canvas" control drives the overlay actions (7.6.1)."""
from __future__ import annotations

import os

import pytest

pytest.importorskip("PySide6")
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication

from pathlib import Path

from raptor.core.fileio.rbd import load_rbd
from raptor.gui.ui.main_window import MainWindow

GOLD = Path(__file__).resolve().parents[1] / "golden"


@pytest.fixture(scope="module")
def qapp():
    return QApplication.instance() or QApplication([])


def test_one_combo_lists_every_overlay_and_none(qapp):
    win = MainWindow()
    combo = win._overlay_combo
    assert combo.count() == 6
    assert combo.itemText(0) == "No canvas overlay"
    assert {combo.itemText(i) for i in range(1, combo.count())} == {
        "Weak link (own availability)", "Capacity", "Cost", "Spares",
        "Importance"}


def test_choosing_an_overlay_checks_only_that_action(qapp):
    win = MainWindow()
    win._load_into_app(load_rbd(str(GOLD / "TwoBlockRedundancy/input.rbd")),
                       "x")
    win._run_count_spin.setValue(1)
    win._run_simulation()
    qapp.processEvents()
    combo = win._overlay_combo
    win._weak_color_action.setEnabled(True)
    win._weak_color_action.setChecked(True)
    assert combo.currentIndex() == 1
    combo.setCurrentIndex(0)
    assert not win._weak_color_action.isChecked()
