"""The GUI's Failure Effects View shows the delivered flow, as `fev --capacity`
and the MCP failure-effects tools report it."""
from __future__ import annotations

from pathlib import Path

import pytest

from raptor.core.fileio.rbd import load_rbd

GOLD = Path(__file__).resolve().parents[1] / "golden"


@pytest.fixture(scope="module")
def qapp():
    from PySide6.QtWidgets import QApplication
    return QApplication.instance() or QApplication([])


def _window(qapp, fixture):
    from raptor.gui.ui.main_window import MainWindow
    w = MainWindow()
    w.resize(1400, 800)
    w.show()
    qapp.processEvents()
    path = str(GOLD / fixture / "input.rbd")
    w._load_into_app(load_rbd(path), path)
    w._dirty = False
    qapp.processEvents()
    return w


def _close(w):
    w._leave_fev()
    w._dirty = False
    w.close()


def test_a_failure_shows_the_delivered_flow(qapp):
    w = _window(qapp, "Capacity2")
    try:
        w._fev_view_action.setChecked(True)
        assert "Flow 1500 of 1500 (100.0%)" in w._statusbar.currentMessage()
        b = w._fev.block_by_name("SolarP_900W")
        w._fev_apply("fail", "block", b.id, b.name)
        assert "Flow 1150 of 1500 (76.7%)" in w._statusbar.currentMessage()
        text = w._fev_text()
        assert "CAPACITY" in text and "Delivered Flow:     1150 of 1500" in text
    finally:
        _close(w)


def test_a_model_without_capacity_analysis_shows_no_flow(qapp):
    w = _window(qapp, "HierarchyExample")
    try:
        w._fev_view_action.setChecked(True)
        assert "Flow" not in w._statusbar.currentMessage()
        assert "CAPACITY" not in w._fev_text()
    finally:
        _close(w)
