"""A new model's first block, and finding a block by name from the results.

Two regressions: a new model drew its block below the Start-End line, so
both links ran diagonally; and clicking a block called "unnamed" in the
event log reported "Couldn't find block 'unnamed'" because the file's
ID-0 template row -- also called "unnamed" -- matched first.
"""
from __future__ import annotations

import pytest
from PySide6.QtWidgets import QApplication

pytestmark = pytest.mark.gui

MODEL = "tests/golden/TwoBlockRedundancy/input.rbd"


@pytest.fixture
def win():
    from raptor.gui.ui.main_window import MainWindow
    QApplication.instance() or QApplication([])
    w = MainWindow()
    yield w
    w._dirty = False
    w.close()


def test_a_new_models_block_sits_on_the_start_end_line(win):
    win._new_file()
    v = win._diagram_dock.view
    (block,) = v._block_items.values()
    start, end = (v._node_items[i] for i in sorted(v._node_items))
    y = block.sceneBoundingRect().center().y()
    for node in (start, end):
        assert abs(node.pos().y() - y) < 6, (node.pos().y(), y)
    assert next(iter(v.blocks.values())).name == "Block_1"


def test_a_block_named_unnamed_is_found(win):
    from raptor.core.fileio.rbd import load_rbd
    win._load_into_app(load_rbd(MODEL), MODEL)
    v = win._diagram_dock.view
    assert any(b.name == "unnamed" for b in v.blocks.values())
    assert v.locate_element("block", "unnamed")
    assert v._scene.selectedItems()


def test_deselecting_clears_the_inspector(win):
    from PySide6.QtTest import QTest

    from raptor.core.fileio.rbd import load_rbd
    from raptor.gui.ui.main_window import INSPECTOR_SELECT_DEBOUNCE_MS

    win._load_into_app(load_rbd(MODEL), MODEL)
    v = win._diagram_dock.view
    nid = next(iter(v._node_items))
    v._node_items[nid].setSelected(True)
    # The Inspector rebuild is debounced so the click stays cheap.
    QTest.qWait(INSPECTOR_SELECT_DEBOUNCE_MS * 2)
    assert win._inspector.current_target() == ("node", nid)
    v._scene.clearSelection()
    QApplication.processEvents()
    assert win._inspector.current_target() is None


def test_the_connect_cursor_stays_over_an_element(win):
    """Items set a pointing-hand cursor once, when built, which overrode
    the Connect tool's cursor just where a link starts or ends."""
    from raptor.core.fileio.rbd import load_rbd
    win._load_into_app(load_rbd(MODEL), MODEL)
    v = win._diagram_dock.view
    v.set_tool_mode("connect") if hasattr(v, "set_tool_mode") else \
        setattr(v, "tool_mode", "connect")
    v._apply_tool_cursor()
    block = next(iter(v._block_items.values()))
    assert block.cursor().shape() == v.viewport().cursor().shape()
    assert block.cursor().pixmap().cacheKey() == \
        v.viewport().cursor().pixmap().cacheKey()
    v.tool_mode = "select"
    v._apply_tool_cursor()
    from PySide6.QtCore import Qt
    assert block.cursor().shape() == Qt.CursorShape.PointingHandCursor
