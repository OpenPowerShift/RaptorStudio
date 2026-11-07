"""Cut and Copy are greyed with nothing selected; Paste with nothing to paste."""
from __future__ import annotations

import pytest
from PySide6.QtWidgets import QApplication

from raptor.core.fileio.rbd import load_rbd

pytestmark = pytest.mark.gui

MODEL = "tests/golden/Phasing3/input.rbd"


@pytest.fixture
def win():
    from raptor.gui.ui import settings
    from raptor.gui.ui.main_window import MainWindow
    QApplication.instance() or QApplication([])
    settings.set_value("startup/show_start_page", False)
    w = MainWindow()
    w._load_into_app(load_rbd(MODEL), MODEL)
    QApplication.processEvents()
    yield w
    w._dirty = False
    w.close()


def _states(w):
    return (w._copy_action.isEnabled(), w._cut_action.isEnabled(),
            w._paste_action.isEnabled(), w._delete_tool.isEnabled())


def test_nothing_selected_greys_cut_copy_delete_and_paste(win):
    view = win._diagram_dock.view
    view._scene.clearSelection()
    view._paste_buffer = []
    win._on_clipboard_changed()
    assert _states(win) == (False, False, False, False)


def test_selecting_enables_copy_cut_delete_live(win):
    view = win._diagram_dock.view
    view._scene.clearSelection()
    next(iter(view._block_items.values())).setSelected(True)
    QApplication.processEvents()
    copy, cut, _paste, delete = _states(win)
    assert copy and cut and delete
    view._scene.clearSelection()
    QApplication.processEvents()
    assert _states(win)[:2] == (False, False)


def test_copying_enables_paste(win):
    view = win._diagram_dock.view
    view._scene.clearSelection()
    view._paste_buffer = []
    win._on_clipboard_changed()
    assert not win._paste_action.isEnabled()
    next(iter(view._block_items.values())).setSelected(True)
    assert view.copy_selection() == 1
    QApplication.processEvents()
    assert win._paste_action.isEnabled()


def test_copy_with_nothing_selected_keeps_the_earlier_copy(win):
    view = win._diagram_dock.view
    view._scene.clearSelection()
    next(iter(view._block_items.values())).setSelected(True)
    view.copy_selection()
    kept = list(view._paste_buffer)
    view._scene.clearSelection()
    assert view.copy_selection() == 0 and view.cut_selection() == 0
    assert view._paste_buffer == kept


def test_an_annotation_alone_enables_copy_cut_and_delete(win):
    view = win._diagram_dock.view
    view._scene.clearSelection()
    from PySide6.QtCore import QPointF
    view._begin_annotation("text", QPointF(10.0, 10.0))
    next(iter(view._anno_items.values())).setSelected(True)
    QApplication.processEvents()
    copy, cut, _p, delete = _states(win)
    assert copy and cut and delete
