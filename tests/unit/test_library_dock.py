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
