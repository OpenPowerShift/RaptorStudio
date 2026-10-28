"""Non-blocking model warnings are the Issues list's, not the Run panel's.

A dependency cycle is allowed (real Raptor runs it), so the panel stays
*ready*; the warning is in the Issues list (the ribbon's Issues button and
the status-bar badge), which is the one place model findings are shown.
"""
from __future__ import annotations

import os

import pytest

pytest.importorskip("PySide6")
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication

from raptor.core.fileio.rbd import load_rbd
from raptor.gui.ui.main_window import MainWindow

REPO = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


@pytest.fixture(scope="module")
def qapp():
    return QApplication.instance() or QApplication([])


def _window(qapp, name):
    w = MainWindow()
    w._load_into_app(load_rbd(os.path.join(REPO, "tests", "golden", name,
                                           "input.rbd")), None)
    w._update_readiness()
    return w


def test_a_cycle_is_a_warning_and_the_panel_stays_ready(qapp):
    w = _window(qapp, "DepCycleIdleProbe")
    label = w._run_panel._readiness
    assert label.text().startswith("Ready."), label.text()
    assert "Model warning" not in label.text()
    w._validate_model(quiet=True)
    assert any("circular dependency" in m for m in w._validation_warnings())
    assert "warning" in w._validation_badge.text()
    assert "circular dependency" in w._validation_badge.toolTip()
    w.close()


def test_a_clean_model_shows_no_warning(qapp):
    w = _window(qapp, "SPFProbe")
    w._validate_model(quiet=True)
    assert w._validation_warnings() == []
    assert w._validation_badge.text() == "✓ Model OK"
    w.close()


def test_a_new_model_has_no_header_count_warning(qapp):
    """REGRESSION: a new one-block model showed "header Links=0 but
    LINK_DATA actually has 2 rows" -- a file detail that save re-syncs."""
    w = MainWindow()
    w._new_file()
    w._validate_model(quiet=True)
    assert not any("header" in m for m in w._validation_warnings())
    w._dirty = False
    w.close()
