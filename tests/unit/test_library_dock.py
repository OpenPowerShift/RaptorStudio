"""The Part Library dock: visible beside the Explorer, apply is undoable."""
from __future__ import annotations

import os
from pathlib import Path

import pytest

pytest.importorskip("PySide6")
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication, QInputDialog

from raptor.api.library.links import (
    apply_part_to_rbd, link_record, local_parts,
)
from raptor.api.library.model import part_from_block
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


def _first_block(rbd):
    return next(row for row in rbd.section("BLOCK_DATA").rows
                if row.get("ID") != "0")


def test_the_library_dock_is_tabbed_with_the_explorer(win):
    assert win._library_dock_widget is not None
    assert win._library_dock.widget() is win._library_dock_widget


def test_a_saved_part_appears_and_applies_to_a_selected_block(win):
    rbd = win._rbd
    row = _first_block(rbd)
    part = part_from_block(row, name="Redundant unit")
    apply_part_to_rbd(rbd, row["ID"], part)
    dock = win._library_dock_widget
    dock.refresh()
    assert dock.list.count() >= 1
    assert "follow" in dock.follows.text()

    view = win._diagram_dock.view
    view._block_items[int(row["ID"])].setSelected(True)
    dock.list.setCurrentRow(0)
    dock.apply_selected()
    assert link_record(win._rbd, row["ID"]) is not None
    assert win._dirty is True


def test_save_selection_creates_a_local_part_and_one_undo_step(win, monkeypatch):
    rbd = win._rbd
    row = _first_block(rbd)
    view = win._diagram_dock.view
    view._block_items[int(row["ID"])].setSelected(True)
    monkeypatch.setattr(QInputDialog, "getText",
                        lambda *args, **kwargs: ("Pump, centrifugal", True))
    dock = win._library_dock_widget
    before = len(local_parts(rbd))
    depth = win._undo_stack.count()
    dock.save_selection()
    assert len(local_parts(win._rbd)) == before + 1
    assert win._undo_stack.count() == depth + 1
    win._undo_stack.undo()
    assert len(local_parts(win._rbd)) == before


def test_dock_saves_and_applies_a_node_part(win, monkeypatch):
    from raptor.api.library.links import element_record, local_parts
    view = win._diagram_dock.view
    view.add_node("J2", 50.0, 50.0)
    node_ids = [nid for nid, node in view.nodes.items()
                if getattr(node, "type_node", 0) == 2]
    assert len(node_ids) >= 2
    view._node_items[node_ids[0]].setSelected(True)
    monkeypatch.setattr(QInputDialog, "getText",
                        lambda *args, **kwargs: ("Junction", True))
    dock = win._library_dock_widget
    dock.save_selection()
    parts = local_parts(win._rbd)
    node_part = next(part for part in parts.values() if part.kind == "node")
    assert element_record(win._rbd, "node", node_ids[0]) is not None

    view._scene.clearSelection()
    view._node_items[node_ids[1]].setSelected(True)
    dock.refresh()
    row = next(i for i in range(dock.list.count())
               if dock.list.item(i).data(256)[1] == node_part.id)
    dock.list.setCurrentRow(row)
    dock.apply_selected()
    assert element_record(win._rbd, "node", node_ids[1]) is not None


def test_an_annotation_part_creates_a_note_and_undoes(win):
    from raptor.api.library.model import part_from_element
    view = win._diagram_dock.view
    part = part_from_element("annotation", {
        "kind": "text", "text": "Inspect this", "w": 140, "h": 40})
    # put the part in the model through a temporary annotation link
    aid = view.add_annotation_from_part(part)
    assert aid is not None
    assert any(entry.get("text") == "Inspect this"
               for entry in view.annotations)
    win._undo_stack.undo()
    assert not any(entry.get("text") == "Inspect this"
                   for entry in view.annotations)
