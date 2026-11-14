"""Quick Add at the cursor (WP-1.6)."""
from __future__ import annotations

import os
from pathlib import Path

import pytest

pytest.importorskip("PySide6")
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import Qt
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication

from raptor.core.fileio.rbd import load_rbd
from raptor.gui.ui.main_window import MainWindow

GOLD = Path(__file__).resolve().parents[1] / "golden"


@pytest.fixture(scope="module")
def qapp():
    return QApplication.instance() or QApplication([])


@pytest.fixture()
def win(qapp):
    window = MainWindow()
    window._load_into_app(load_rbd(str(GOLD / "TwoBlockRedundancy/input.rbd")),
                          "x")
    return window


def _blocks(win):
    return len([row for row in win._rbd.section("BLOCK_DATA").rows
                if row.get("ID") != "0"])


def test_space_opens_quick_add_and_enter_adds_a_block(win):
    view = win._diagram_dock.view
    view.setFocus()
    QTest.keyClick(view, Qt.Key.Key_Space)
    popup = view._quick_add
    assert popup.isVisible()
    popup.query.setText("Block")
    popup.list.setCurrentRow(0)
    before = _blocks(win)
    popup._choose()
    assert _blocks(win) == before + 1


def test_escape_leaves_the_model_unchanged(win):
    view = win._diagram_dock.view
    before = _blocks(win)
    view.show_quick_add()
    QTest.keyClick(view._quick_add.query, Qt.Key.Key_Escape)
    assert _blocks(win) == before
