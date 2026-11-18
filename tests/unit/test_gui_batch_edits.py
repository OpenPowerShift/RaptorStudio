"""Batch edits on the canvas cost one refresh, not one per row (round-2
GUI F1, F3, F7; round-3 GUI F5). Pinned by behaviour: the signal counts
and row order a user sees."""
from __future__ import annotations

import itertools
import os

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")


@pytest.fixture()
def view():
    from PySide6.QtWidgets import QApplication
    QApplication.instance() or QApplication([])
    from raptor.core.edit.new_model import build_new_model
    from raptor.gui.ui.diagram import DiagramView
    view = DiagramView()
    view.load_rbd(build_new_model())
    return view


def _count(signal) -> list:
    seen: list = []
    signal.connect(lambda *a: seen.append(a))
    return seen


def test_a_macro_says_model_changed_once(view):
    ids = [view.add_block(f"B{i}", 50.0 * i, 0) for i in range(5)]
    seen = _count(view.model_changed)
    stack = view._stack
    stack.beginMacro("Rename all")
    for i, bid in enumerate(ids):
        view.rename_block(bid, f"R{i}")
    stack.endMacro()
    assert len(seen) == 1
    assert [view.blocks[b].name for b in ids] == [f"R{i}" for i in range(5)]
    seen.clear()
    stack.undo()
    assert len(seen) == 1
    assert [view.blocks[b].name for b in ids] == [f"B{i}" for i in range(5)]
    seen.clear()
    stack.redo()
    assert len(seen) == 1


def test_a_single_edit_still_says_model_changed_at_once(view):
    bid = view.add_block("One", 0, 0)
    seen = _count(view.model_changed)
    view.rename_block(bid, "Two")
    assert len(seen) == 1


def _block_order(view) -> list[str]:
    return [r["ID"] for r in view._rbd_ref.sections["BLOCK_DATA"].rows]


def test_undo_of_a_delete_puts_rows_back_where_they_were(view):
    ids = [view.add_block(f"B{i}", 50.0 * i, 0) for i in range(6)]
    for a, b in itertools.pairwise(ids):
        view.connect_elements("Block", a, "Block", b)
    order = _block_order(view)
    links = [r["ID"] for r in view._rbd_ref.sections["LINK_DATA"].rows]
    view.scene().clearSelection()
    for bid in (ids[1], ids[3]):
        view._block_items[bid].setSelected(True)
    view.delete_selected()
    assert str(ids[1]) not in _block_order(view)
    view._stack.undo()
    assert _block_order(view) == order
    assert [r["ID"] for r in view._rbd_ref.sections["LINK_DATA"].rows] == links
    assert len(view._links) == len(view.links) == len(links)


def test_deleting_many_selected_items_says_selection_changed_once(view):
    ids = [view.add_block(f"B{i}", 50.0 * i, 0) for i in range(8)]
    view.scene().clearSelection()
    for bid in ids:
        view._block_items[bid].setSelected(True)
    seen = _count(view.scene().selectionChanged)
    view.delete_selected()
    assert not view.blocks.keys() & set(ids)
    assert len(seen) == 1


def test_undo_of_a_multi_block_add_removes_them_all(view):
    from raptor.core.fileio.rbdlib import make_block_row
    from raptor.gui.ui.diagram_commands import AddCmd
    before = set(view.blocks)
    rows = [make_block_row(900 + i, f"P{i}", 10.0 * i, 0) for i in range(4)]
    view._stack.push(AddCmd(view, "block", rows))
    assert {900, 901, 902, 903} <= set(view.blocks)
    view._stack.undo()
    assert set(view.blocks) == before
