"""Ch.10 FEV interactive log (the Failure Effects text, appended to a file)."""

from __future__ import annotations

import os
import pathlib
import tempfile

import pytest
from raptor.gui.ui import filedialogs as _fdg


REPO = pathlib.Path(__file__).resolve().parents[2]
GOLD = os.path.join(REPO, "tests", "golden")
# Resolve the log path under the platform temp dir rather than a fixed
# /tmp path, which is not portable across machines (see tests/conftest.py).
LOG = os.path.join(tempfile.gettempdir(), "ilog_test.txt")


@pytest.fixture()
def win(qtbot_stub):
    from raptor.gui.ui.main_window import MainWindow
    from raptor.core.fileio.rbd import load_rbd
    w = MainWindow()
    w._load_into_app(load_rbd(os.path.join(
        GOLD, "TwoBlockRedundancy", "input.rbd")), "x")
    return w


@pytest.fixture()
def qtbot_stub(qapp, monkeypatch):
    monkeypatch.setattr(_fdg, "getSaveFileName",
                        staticmethod(lambda *a, **k: (LOG, "")))
    from PySide6.QtWidgets import QApplication
    return QApplication.instance() or qapp


def test_write_and_autoclose(win):
    """The log is the Failure Effects text; leaving the view closes it."""
    if os.path.exists(LOG):
        os.remove(LOG)
    assert not win._fev_log_btn.isEnabled()          # needs Failure Effects View
    win._fev_view_action.setChecked(True)
    assert win._fev_log_btn.isEnabled()
    win._on_fev_log_open()
    assert win._fev_log and win._fev_log["path"] == LOG
    win._on_fev_log_write()
    win._fev_view_action.setChecked(False)           # leaving must auto-close
    assert win._fev_log is None
    assert not win._fev_log_write_action.isEnabled()
    txt = open(LOG, newline="").read()
    assert txt.startswith("\r\nFAILURES\r\n")
    assert "System Status = Green" in txt
    assert txt.rstrip("\r\n").endswith("*" * 65)


def test_open_requires_the_view(win, qtbot_stub):
    win._on_fev_log_open()
    assert win._fev_log is None


@pytest.fixture(scope="module")
def qapp():
    from PySide6.QtWidgets import QApplication
    return QApplication.instance() or QApplication([])
