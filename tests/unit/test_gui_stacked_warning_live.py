"""Dropping or moving an element onto another shows the Model warning."""
from __future__ import annotations

import pytest
from PySide6.QtWidgets import QApplication

from raptor.core.fileio.rbd import load_rbd

pytestmark = pytest.mark.gui

MODEL = "tests/golden/Phasing3/input.rbd"
MARK = "is drawn on top of"


@pytest.fixture
def win():
    from raptor.gui.ui import settings
    from raptor.gui.ui.main_window import MainWindow
    QApplication.instance() or QApplication([])
    settings.set_value("startup/show_start_page", False)
    w = MainWindow()
    w._load_into_app(load_rbd(MODEL), MODEL)
    w._validate_model(quiet=True)
    QApplication.processEvents()
    yield w
    w._dirty = False
    w.close()


def _stacked(win) -> list[str]:
    return [w for w in win._validation_warnings() if MARK in w]


def _settle(win):
    timer = win._stack_timer
    assert timer.isActive()
    timer.stop()
    win._refresh_stack_warnings()


def test_moving_a_block_onto_another_warns_and_moving_it_away_clears(win):
    from raptor.gui.ui.diagram_commands import MoveCmd
    view = win._diagram_dock.view
    assert _stacked(win) == []
    a, b = sorted(view.blocks)[:2]
    ia, ib = view._block_items[a], view._block_items[b]
    old, target = (ib.pos().x(), ib.pos().y()), (ia.pos().x(), ia.pos().y())
    view._stack.push(MoveCmd(view, [("block", b, old, target)]))
    _settle(win)
    assert len(_stacked(win)) == 1
    assert "Warning:" in win._statusbar.currentMessage()
    assert "warning" in win._validation_badge.text()
    view._stack.undo()
    _settle(win)
    assert _stacked(win) == []


def test_placing_a_block_on_another_warns(win):
    view = win._diagram_dock.view
    a = sorted(view.blocks)[0]
    from raptor.gui.ui.diagram_common import BLOCK_H, BLOCK_W
    top_left = view._block_items[a].pos()
    mx, my = view.scene_to_model(
        top_left + type(top_left)(BLOCK_W / 2, BLOCK_H / 2))   # its centre
    view.add_block("Dup", mx, my)
    QApplication.processEvents()
    win._validate_model(quiet=True)
    assert len(_stacked(win)) == 1
