"""Qt-free name/data planning and the canvas duplicate gestures (WP-1.5)."""
from __future__ import annotations

import os
from pathlib import Path

import pytest

pytest.importorskip("PySide6")
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication

from raptor.api.run import model_hash
from raptor.core.fileio.rbd import load_rbd
from raptor.core.edit.clipboard_plan import (
    DATA_GROUPS, next_free_name, plan_paste_data,
)
from raptor.gui.ui.main_window import MainWindow

GOLD = Path(__file__).resolve().parents[1] / "golden"


def test_next_free_name_moves_letters_digits_and_plain_names():
    assert next_free_name({"Pump_A"}, "Pump_A") == "Pump_B"
    assert next_free_name({"Pump_A", "Pump_B"}, "Pump_A") == "Pump_C"
    assert next_free_name({"Pump_9"}, "Pump_9") == "Pump_10"
    assert next_free_name({"Pump"}, "Pump") == "Pump_2"
    assert next_free_name({"Pump_2"}, "Pump") == "Pump_3"
    long = "A" * 20
    assert len(next_free_name({long}, long)) <= 20


def test_plan_paste_data_touches_only_the_chosen_groups():
    src = {"ID": "1", "blockName": "A", "FDist": "15", "FParam1": "2",
           "FParam2": "100", "RDist": "4", "RParam1": "9",
           "PMFreq": "500", "xPos": "0"}
    dst = {"ID": "2", "blockName": "B", "FDist": "4", "FParam1": "50",
           "FParam2": "0", "RDist": "7", "RParam1": "3",
           "PMFreq": "100", "xPos": "8"}
    changes = plan_paste_data(src, [dst], ["Failure"])
    columns = {change["column"] for change in changes}
    assert columns <= set(DATA_GROUPS["Failure"])
    assert "RDist" not in columns and "blockName" not in columns
    assert "xPos" not in columns


@pytest.fixture(scope="module")
def qapp():
    return QApplication.instance() or QApplication([])


@pytest.fixture()
def win(qapp):
    window = MainWindow()
    window._load_into_app(load_rbd(str(GOLD / "SpaceStation/input.rbd")), "x")
    return window


def _block(window, index=0):
    return [row for row in window._rbd.section("BLOCK_DATA").rows
            if row.get("ID") != "0"][index]


def test_duplicate_adds_a_suffixed_copy_in_one_undo_step(win):
    row = _block(win)
    before_hash = model_hash(win._rbd)
    view = win._diagram_dock.view
    view._block_items[int(row["ID"])].setSelected(True)
    depth = win._undo_stack.count()
    made = view.duplicate_selection()
    assert made == 1
    names = [r.get("blockName") for r in win._rbd.section("BLOCK_DATA").rows]
    assert "Attitude_2" in names
    assert win._undo_stack.count() == depth + 1
    win._undo_stack.undo()
    assert model_hash(win._rbd) == before_hash


def test_array_adds_copies_in_a_row(win):
    row = _block(win)
    view = win._diagram_dock.view
    view._block_items[int(row["ID"])].setSelected(True)
    made = view.array_selection(2, "right", 1)
    assert made == 2
    names = [r.get("blockName") for r in win._rbd.section("BLOCK_DATA").rows]
    assert "Attitude_2" in names and "Attitude_3" in names


def test_paste_data_changes_only_the_selected_group(win):
    source = dict(_block(win))
    target = _block(win, 1)
    source["FParam1"] = "999.0"
    view = win._diagram_dock.view
    QApplication.clipboard().clear()
    view._paste_buffer = [("block", source)]
    view._block_items[int(target["ID"])].setSelected(True)
    old_name, old_pos = target["blockName"], target["xPos"]
    made = view.paste_data(("Failure",))
    assert made == 1
    assert target["FParam1"] == "999.0"
    assert target["blockName"] == old_name and target["xPos"] == old_pos
    assert target["RDist"] == _block(win, 1)["RDist"]
    win._undo_stack.undo()
    assert target["FParam1"] != "999.0"


def test_the_api_duplicates_and_copies_data_without_qt(tmp_path):
    from raptor.api import edit
    from raptor.api.workspace import Workspace
    ws = Workspace(tmp_path)
    mid = ws.register_model(load_rbd(str(GOLD / "SpaceStation/input.rbd")),
                            name="m")["model_id"]
    result = edit.duplicate_elements(ws, mid, ["Attitude"], copies=2)
    assert result["copies"] == 2 and result["count"] == 2
    names = [row.get("blockName")
             for row in ws.get(mid).rbd.section("BLOCK_DATA").rows]
    assert "Attitude_2" in names and "Attitude_3" in names

    rows = [row for row in ws.get(mid).rbd.section("BLOCK_DATA").rows
            if row.get("ID") != "0"]
    source = rows[0]
    source["FParam1"] = "777.0"
    changed = edit.copy_element_data(ws, mid, "Attitude", ["SolarP_500W"],
                                     ["Failure"])
    assert changed["count"] >= 1
    assert next(row for row in ws.get(mid).rbd.section("BLOCK_DATA").rows
                if row.get("blockName") == "SolarP_500W")["FParam1"] == "777.0"


def test_duplicate_in_parallel_adds_copies_between_the_neighbours(win):
    view = win._diagram_dock.view
    bid = next(iter(view._block_items))
    view._block_items[bid].setSelected(True)
    before = len(view._block_items)
    ids = view.duplicate_parallel(2, 3)
    assert len(ids) == 2
    assert len(view._block_items) == before + 2
    assert view.blocks[bid].name != view.blocks[ids[0]].name
    win._undo_stack.undo()
    assert len(view._block_items) == before
