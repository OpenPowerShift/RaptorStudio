"""The Part Library dock: visible beside the Explorer, apply is undoable."""
from __future__ import annotations

import os
from pathlib import Path

import pytest

pytest.importorskip("PySide6")
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication, QDialog

from raptor.gui.ui import library_dock as L

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
    assert win._library_dock_widget is not None
    assert win._library_dock.widget() is win._library_dock_widget


def _entries(dock):
    """(kind group title, item) for every part in the tree."""
    out = []
    for i in range(dock.tree.topLevelItemCount()):
        head = dock.tree.topLevelItem(i)
        for j in range(head.childCount()):
            out.append((head.text(0), head.child(j)))
    return out


def _choose(dock, part_id):
    item = next(item for _head, item in _entries(dock)
                if item.data(0, L._ROLE)[1] == part_id)
    dock.tree.setCurrentItem(item)


def _accept(monkeypatch, name, *, as_type=None):
    def exec_(self):
        self.name.setText(name)
        if as_type is not None:
            self.as_type.setChecked(as_type)
            self.as_assembly.setChecked(not as_type)
        return QDialog.DialogCode.Accepted
    monkeypatch.setattr(L._SaveDialog, "exec", exec_)


def test_the_dock_lists_the_open_models_parts_on_load(win):
    rbd = win._rbd
    row = _first_block(rbd)
    apply_part_to_rbd(rbd, row["ID"], part_from_block(row, name="Unit"))
    dock = win._library_dock_widget
    dock.refresh()
    assert [head for head, _item in _entries(dock)] == ["Block types (1)"]
    assert "1 in use" in _entries(dock)[0][1].text(0)
    assert "Open or create" not in dock.status.text()


def test_a_part_type_applies_to_a_selected_block(win):
    rbd = win._rbd
    row = _first_block(rbd)
    part = part_from_block(row, name="Redundant unit")
    apply_part_to_rbd(rbd, row["ID"], part)
    dock = win._library_dock_widget
    dock.refresh()
    view = win._diagram_dock.view
    other = next(bid for bid in view._block_items if bid != int(row["ID"]))
    view._block_items[other].setSelected(True)
    _choose(dock, part.id)
    assert dock.use_button.text() == "Apply to selection"
    dock.use_selected()
    assert link_record(win._rbd, other) is not None
    assert win._dirty is True


def test_one_block_saves_a_linked_part_type_in_one_undo_step(win, monkeypatch):
    rbd = win._rbd
    row = _first_block(rbd)
    view = win._diagram_dock.view
    view._block_items[int(row["ID"])].setSelected(True)
    _accept(monkeypatch, "Pump, centrifugal", as_type=True)
    dock = win._library_dock_widget
    before = len(local_parts(rbd))
    depth = win._undo_stack.count()
    dock.save_selection()
    assert len(local_parts(win._rbd)) == before + 1
    assert link_record(win._rbd, row["ID"]) is not None
    assert win._undo_stack.count() == depth + 1
    win._undo_stack.undo()
    assert len(local_parts(win._rbd)) == before


def test_a_selection_saves_as_an_assembly_and_inserts_a_copy(win, monkeypatch):
    view = win._diagram_dock.view
    for item in view._block_items.values():
        item.setSelected(True)
    _accept(monkeypatch, "Pair")
    dock = win._library_dock_widget
    dock.save_selection()
    parts = [p for p in local_parts(win._rbd).values() if p.kind == "assembly"]
    assert [p.name for p in parts] == ["Pair"]
    dock.refresh()
    assert _entries(dock)[0][0] == "Assemblies (1)"
    before = [dict(r) for r in win._rbd.section("BLOCK_DATA").rows]
    blocks_before = len(before)
    _choose(dock, parts[0].id)
    assert dock.use_button.text() == "Insert"
    inserted = dock.use_part("model", parts[0].id, at_model=(500.0, 500.0))
    rows = win._rbd.section("BLOCK_DATA").rows
    assert inserted >= 2
    new = rows[blocks_before:]
    assert len(new) == sum(1 for k, _r in parts[0].fields["items"] if k == "block")
    old_names = {r.get("blockName") for r in before}
    # The copies got names the model did not use, like the command line's
    # insert (a plain canvas paste keeps the source names).
    assert not {r.get("blockName") for r in new} & old_names
    assert len({r.get("blockName") for r in new}) == len(new)
    win._undo_stack.undo()
    assert len(win._rbd.section("BLOCK_DATA").rows) == blocks_before


def test_a_part_dropped_on_the_canvas_is_inserted_there(win, monkeypatch):
    import json
    from PySide6.QtCore import QMimeData, QPoint, QPointF, Qt
    from PySide6.QtGui import QDropEvent
    view = win._diagram_dock.view
    for item in view._block_items.values():
        item.setSelected(True)
    _accept(monkeypatch, "Pair")
    dock = win._library_dock_widget
    dock.save_selection()
    part = next(p for p in local_parts(win._rbd).values() if p.kind == "assembly")
    dock.install_canvas_drop(view)
    mime = QMimeData()
    mime.setData(L.PART_MIME, json.dumps({"source": "model", "id": part.id}).encode())
    before = len(win._rbd.section("BLOCK_DATA").rows)
    event = QDropEvent(QPointF(QPoint(40, 40)), Qt.DropAction.CopyAction, mime,
                       Qt.MouseButton.NoButton, Qt.KeyboardModifier.NoModifier)
    handled = dock._drop_filters[id(view)].eventFilter(view.viewport(), event)
    assert handled
    assert len(win._rbd.section("BLOCK_DATA").rows) > before


def test_the_dock_does_not_floor_the_left_column(win):
    assert win._library_dock.minimumSizeHint().height() <= 220




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


def test_a_saved_part_type_keeps_where_its_numbers_came_from(win, monkeypatch):
    rbd = win._rbd
    row = _first_block(rbd)
    win._diagram_dock.view._block_items[int(row["ID"])].setSelected(True)

    def exec_(self):
        self.name.setText("Seal")
        self.as_type.setChecked(True)
        self.basis.setCurrentIndex(self.basis.findData("handbook"))
        self.source.setText("OREDA 2015")
        return QDialog.DialogCode.Accepted
    monkeypatch.setattr(L._SaveDialog, "exec", exec_)
    win._library_dock_widget.save_selection()
    (part,) = [p for p in local_parts(win._rbd).values() if p.name == "Seal"]
    assert part.basis == "handbook" and part.source == "OREDA 2015"


def test_the_basis_is_one_of_three():
    import pytest as _pytest
    from raptor.api.library.model import PartType
    assert PartType("p", "P", basis="judgement").basis == "experience"
    with _pytest.raises(ValueError, match="basis"):
        PartType("p", "P", basis="guesswork")


def test_the_empty_library_message_is_never_cut_off(win, qapp):
    dock = win._library_dock_widget
    dock.resize(260, 420)
    dock.show()
    dock.refresh()
    for _ in range(4):
        qapp.processEvents()
    assert "No parts yet" in dock.status.text()
    assert dock.status.height() >= dock.status.heightForWidth(dock.status.width())
