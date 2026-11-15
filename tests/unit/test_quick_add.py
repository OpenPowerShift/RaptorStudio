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


def test_focus_mode_dims_only_the_unselected(win):
    view = win._diagram_dock.view
    ids = list(view._block_items)
    assert len(ids) >= 2
    view._block_items[ids[0]].setSelected(True)
    view.set_focus_mode(True)
    assert view._block_items[ids[0]].opacity() == 1.0
    assert view._block_items[ids[1]].opacity() == 0.25
    view.set_focus_mode(False)
    assert view._block_items[ids[1]].opacity() == 1.0


def test_series_and_parallel_patterns_are_valid_and_undoable(win):
    view = win._diagram_dock.view
    before_blocks = _blocks(win)
    before_nodes = len(view.nodes)
    view.add_series(3, 300.0, 200.0)
    assert _blocks(win) == before_blocks + 3
    assert len(view.nodes) == before_nodes + 2

    view.add_parallel(3, 2, 400.0, 300.0)
    assert len(view.nodes) == before_nodes + 4
    assert _blocks(win) == before_blocks + 6
    win._undo_stack.undo()
    assert _blocks(win) == before_blocks + 3


def test_parallel_units_stack_and_never_reuse_a_name(win):
    """User testing: the parallel pattern laid its blocks out in a row (it
    read as a series chain and ran off-screen) and named one Block_1 beside
    an existing Block_1 ("Block 1" was checked before it became Block_1)."""
    view = win._diagram_dock.view
    view.add_block("Block_1", 50.0, 50.0)
    ids = view.add_parallel(3, 1, 400.0, 300.0)
    xs = {view.blocks[b].x_position for b in ids}
    ys = sorted(view.blocks[b].y_position for b in ids)
    assert len(xs) == 1 and ys[0] < ys[1] < ys[2]
    rows = [row for row in win._rbd.section("BLOCK_DATA").rows if row.get("ID") != "0"]
    new = [str(r.get("blockName")) for r in rows if int(r["ID"]) in ids]
    old = [str(r.get("blockName")) for r in rows if int(r["ID"]) not in ids]
    assert len(set(new)) == 3 and not set(new) & set(old), (new, old)


def test_quick_add_offers_blocks_from_every_level(qapp):
    """G16: only the blocks drawn at the current level could be copied."""
    window = MainWindow()
    window._load_into_app(load_rbd(str(GOLD / "HierarchyExample/input.rbd")), "x")
    view = window._diagram_dock.view
    every = {str(r["ID"]) for r in window._rbd.section("BLOCK_DATA").rows
             if str(r.get("ID")) != "0"}
    here = {str(b.id) for b in view.blocks.values()}
    assert every - here, "the fixture should have blocks inside a hierarchy"
    from raptor.gui.ui.quick_add import QuickAdd
    popup = QuickAdd(view)
    _cands, data = popup._candidates()
    offered = {k.split(":", 1)[1] for k in data if k.startswith("block:")}
    assert offered == every
    deep = next(iter(every - here))
    popup._model_xy = (10.0, 10.0)
    popup._data = data
    from PySide6.QtWidgets import QListWidgetItem
    item = QListWidgetItem("copy")
    item.setData(Qt.ItemDataRole.UserRole, f"block:{deep}")
    popup.list.addItem(item)
    popup.list.setCurrentItem(item)
    before = _blocks(window)
    popup._choose()
    assert _blocks(window) == before + 1
