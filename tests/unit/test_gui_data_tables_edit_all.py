"""Data Tables ▸ Edit all levels (#353): rows of levels that are not open
are edited through their file rows, one undo step each."""
from __future__ import annotations

import pytest
from PySide6.QtCore import Qt
from PySide6.QtWidgets import QApplication

from tests.unit.test_gui_data_tables_whole_model import MODEL, win  # noqa: F401

pytestmark = pytest.mark.gui
assert MODEL


def _row(view, section, oid):
    return next(r for r in view._rbd_ref.sections[section].rows
                if str(r.get("ID")) == str(oid))


def _edit_all(dt):
    dt._whole_box.setChecked(True)
    dt._edit_all_box.setChecked(True)
    dt._realize_all(dt._blocks_table)


def test_the_switch_needs_whole_model_and_unlocks_other_levels(win):  # noqa: F811
    dt = win._data_tables
    assert not dt._edit_all_box.isEnabled()
    _edit_all(dt)
    assert dt._edit_all_box.isEnabled()
    t = dt._blocks_table
    assert all(t.item(r, 0).flags() & Qt.ItemFlag.ItemIsEditable
               for r in range(t.rowCount()))
    dt._whole_box.setChecked(False)
    assert not dt._edit_all_box.isChecked()


def test_rename_on_a_closed_level_is_one_undo_step(win):  # noqa: F811
    dt = win._data_tables
    view = win._diagram_dock.view
    _edit_all(dt)
    t = dt._blocks_table
    bid = dt._row_key(t, 0)
    assert bid not in view.blocks                     # its level is closed
    old = _row(view, "BLOCK_DATA", bid)["blockName"]
    before = view._stack.count()
    t.item(0, 0).setText("Renamed here")
    QApplication.processEvents()
    assert _row(view, "BLOCK_DATA", bid)["blockName"] == "Renamed_here"
    assert view._stack.count() == before + 1
    view._stack.undo()
    assert _row(view, "BLOCK_DATA", bid)["blockName"] == old
    view._stack.redo()
    assert _row(view, "BLOCK_DATA", bid)["blockName"] == "Renamed_here"


def test_a_parameter_edit_uses_the_same_rules(win):  # noqa: F811
    dt = win._data_tables
    view = win._diagram_dock.view
    _edit_all(dt)
    t = dt._blocks_table
    bid = dt._row_key(t, 0)
    dt._tabs.setCurrentWidget(t)
    row = _row(view, "BLOCK_DATA", bid)
    old = dict(row)
    assert dt._set_param(bid, "F", 0, 1234.0)
    assert float(_row(view, "BLOCK_DATA", bid)["FParam1"]) == pytest.approx(1234.0)
    assert not dt._set_param(bid, "F", 0, -5.0)       # refused as on the canvas
    assert float(_row(view, "BLOCK_DATA", bid)["FParam1"]) == pytest.approx(1234.0)
    view._stack.undo()
    assert _row(view, "BLOCK_DATA", bid) == old


def test_undo_after_opening_that_level_updates_the_canvas(win):  # noqa: F811
    dt = win._data_tables
    view = win._diagram_dock.view
    _edit_all(dt)
    t = dt._blocks_table
    bid = dt._row_key(t, 0)
    old = _row(view, "BLOCK_DATA", bid)["blockName"]
    t.item(0, 0).setText("Moved_on")
    QApplication.processEvents()
    assert view.locate_id("block", bid)                  # open its level
    assert view.blocks[bid].name == "Moved_on"
    view._stack.undo()
    assert view.blocks[bid].name == old
    assert _row(view, "BLOCK_DATA", bid)["blockName"] == old


def test_several_rows_on_closed_levels_change_in_one_undo_step(win, monkeypatch):  # noqa: F811
    dt = win._data_tables
    view = win._diagram_dock.view
    _edit_all(dt)
    t = dt._blocks_table
    dt._tabs.setCurrentWidget(t)
    keys = [dt._row_key(t, r) for r in range(3)]
    monkeypatch.setattr(dt, "_targets", lambda _t, _oid: list(keys))
    monkeypatch.setattr(dt, "_confirm_multi", lambda _n: True)
    olds = {k: dict(_row(view, "BLOCK_DATA", k)) for k in keys}
    before = view._stack.count()
    assert dt._set_param(keys[0], "R", 0, 7.5)
    assert view._stack.count() == before + 1
    assert all(float(_row(view, "BLOCK_DATA", k)["RParam1"]) == pytest.approx(7.5)
               for k in keys)
    view._stack.undo()
    assert all(_row(view, "BLOCK_DATA", k) == olds[k] for k in keys)
