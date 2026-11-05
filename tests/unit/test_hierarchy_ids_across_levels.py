"""A new hierarchy's ids are unique across EVERY level, and the canvas, the
API and MCP build it the same way.

The canvas allocated the new hierarchy and its In/Out markers by stepping
past the ids on the level on screen only. In a model whose levels' ids
interleave (`hierarchy_ids_three_levels.rbds`: Hierarchy_1's markers are
nodes 5 and 6), a collapse at the deepest level landed the new markers on
5 and 6 and was refused as invalid ("Node 6 'in': You cannot connect...");
Add Hierarchy there wrote duplicate node ids.
"""
from __future__ import annotations

import collections
import os
from pathlib import Path

import pytest

pytest.importorskip("PySide6")
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication  # noqa: E402

from raptor.api import build  # noqa: E402
from raptor.api.validate import issues  # noqa: E402
from raptor.api.workspace import Workspace  # noqa: E402
from raptor.core.fileio.model_file import load_model_file  # noqa: E402
from raptor.gui.ui.diagram import DiagramView  # noqa: E402

pytestmark = pytest.mark.gui
MODEL = Path(__file__).parent / "data" / "hierarchy_ids_three_levels.rbds"
#: Hierarchy_2 (id 1) inside Hierarchy_1 (id 4): In -> node_3 -> Switch
#: (block 2) -> LED_Panel (block 3) -> node_4 -> Out.
DEEPEST = (4, 1)
COLS = {"HIERARCHY_DATA": ("ID", "Parent", "in", "out", "level"),
        "NODE_DATA": ("ID", "Type", "parent", "k", "n", "Out"),
        "BLOCK_DATA": ("ID", "ParentId"),
        "LINK_DATA": ("ID", "FromType", "FromID", "ToType", "ToID", "pID")}


@pytest.fixture(scope="module")
def qapp():
    return QApplication.instance() or QApplication([])


def _deepest_view():
    v = DiagramView()
    v.load_rbd(load_model_file(str(MODEL)))
    for hid in DEEPEST:
        v.enter_level(hid)
    return v


def _duplicates(rbd) -> list[int]:
    ids = collections.Counter(int(float(r["ID"]))
                              for r in rbd.sections["NODE_DATA"].rows)
    return [i for i, n in ids.items() if n > 1]


def _shape(rbd) -> dict:
    def cell(row, col):
        value = row.get(col, "0")
        return value if col.endswith("Type") else str(int(float(value)))
    return {sec: sorted(tuple(cell(r, c) for c in cols)
                        for r in rbd.sections[sec].rows
                        if str(r.get("ID")) != "0")
            for sec, cols in COLS.items()}


@pytest.mark.parametrize("picked", [("block", 2, "block", 3),
                                    ("block", 3, "node", 2),
                                    ("node", 1, "block", 2)])
def test_a_collapse_at_the_deepest_level_is_accepted(qapp, picked):
    v = _deepest_view()
    items = {"block": v._block_items, "node": v._node_items}
    v._scene.clearSelection()
    items[picked[0]][picked[1]].setSelected(True)
    items[picked[2]][picked[3]].setSelected(True)
    assert v.collapse_selection() is not None
    v.write_back_to_rbd(v._rbd_ref)
    assert _duplicates(v._rbd_ref) == []
    assert [i for i in issues(v._rbd_ref) if i.severity == "error"] == []


def test_add_hierarchy_at_the_deepest_level_has_unique_ids(qapp):
    v = _deepest_view()
    v.add_hierarchy("Deep", 40.0, 40.0)
    assert _duplicates(v._rbd_ref) == []


def test_the_api_collapses_exactly_as_the_canvas_does(qapp, tmp_path):
    """One builder and one collapse plan: the API's result is the canvas's,
    row for row."""
    ws = Workspace(tmp_path)
    mid = ws.open_model(str(MODEL))["model_id"]
    res = build.collapse_selection(
        ws, mid, ["Block:Switch", "Block:LED_Panel"], name="Inner", parent=1)
    api = ws.get(mid).rbd
    assert [i for i in issues(api) if i.severity == "error"] == []

    v = _deepest_view()
    v._scene.clearSelection()
    v._block_items[2].setSelected(True)
    v._block_items[3].setSelected(True)
    hid = v.collapse_selection(name="Inner")
    v.write_back_to_rbd(v._rbd_ref)
    assert hid == int(res["ID"])
    assert _shape(api) == _shape(v._rbd_ref)

    deep = build.add_hierarchy(ws, mid, "Deeper", parent=hid)
    assert _duplicates(ws.get(mid).rbd) == []
    assert int(deep["Parent"]) == hid
