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


def _select_one_block(view):
    view._scene.clearSelection()
    bid = next(b for b in view._block_items if b != 0)
    view._block_items[bid].setSelected(True)
    return bid


def _pick(popup, key):
    for row in range(popup.list.count()):
        if popup.list.item(row).data(Qt.ItemDataRole.UserRole) == key:
            popup.list.setCurrentRow(row)
            return
    raise AssertionError(f"{key} not offered")


def test_space_opens_quick_add_and_enter_adds_a_copy(win):
    view = win._diagram_dock.view
    view.setFocus()
    QTest.keyClick(view, Qt.Key.Key_Space)
    popup = view._quick_add
    assert popup.isVisible()
    popup.query.setText("copy")
    before = _blocks(win)
    popup._choose()
    assert _blocks(win) == before + 1


def test_no_element_kinds_the_ribbon_already_has(win):
    """User testing: Block, Node, Event and Hierarchy rows repeated the
    ribbon's Add buttons."""
    from raptor.gui.ui.quick_add import QuickAdd
    popup = QuickAdd(win._diagram_dock.view)
    _cands, data = popup._candidates()
    assert not [k for k in data if k.startswith("kind:")]


def test_without_a_selection_the_patterns_ask_for_one(win):
    from raptor.gui.ui.quick_add import HINT, QuickAdd
    view = win._diagram_dock.view
    view._scene.clearSelection()
    popup = QuickAdd(view)
    popup._fill("")
    assert popup.list.item(0).data(Qt.ItemDataRole.UserRole) == HINT
    assert not popup.list.item(0).flags() & Qt.ItemFlag.ItemIsEnabled
    assert popup.list.currentRow() != 0
    _cands, data = popup._candidates()
    assert not [k for k in data if k.startswith(("series:", "parallel:"))]


def test_copies_are_named_after_the_block(win):
    from raptor.gui.ui.quick_add import QuickAdd
    popup = QuickAdd(win._diagram_dock.view)
    cands, _data = popup._candidates()
    names = {str(r["blockName"]) for r in win._rbd.section("BLOCK_DATA").rows
             if str(r.get("ID")) != "0"}
    texts = {c.text for c in cands if str(c.data).startswith("block:")}
    assert texts == {f"Copy of {name}" for name in names}


@pytest.mark.parametrize(("key", "added", "k"), [
    ("series:2", 1, None), ("series:3", 2, None),
    ("parallel:1:2", 1, 1), ("parallel:2:3", 2, 2), ("parallel:1:3", 2, 1)])
def test_patterns_repeat_the_selected_block_wired_in(win, key, added, k):
    """User testing: the patterns asked for a count, then dropped blank
    blocks unconnected at the cursor. Now they copy the selected block
    where it is, in one undo step."""
    from raptor.gui.ui.quick_add import QuickAdd
    view = win._diagram_dock.view
    bid = _select_one_block(view)
    template = view.blocks[bid]
    before = _blocks(win)
    popup = QuickAdd(view)
    popup._fill("")
    _pick(popup, key)
    popup._choose()
    assert _blocks(win) == before + added
    new = [b for b in view.blocks.values() if b.id not in
           {bid} and b.fail_distro == template.fail_distro][-added:]
    assert len(new) == added
    linked = {(r["FromType"], r["FromID"]) for r in
              win._rbd.section("LINK_DATA").rows} | {
              (r["ToType"], r["ToID"]) for r in
              win._rbd.section("LINK_DATA").rows}
    for block in new:
        assert ("RBDBlock", str(block.id)) in linked or \
            ("Block", str(block.id)) in linked, linked
    if k is not None:
        assert k in {n.good_paths for n in view.nodes.values()
                     if n.type_node == 2}
    win._undo_stack.undo()
    assert _blocks(win) == before


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
