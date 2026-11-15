"""U23: study windows' element lists can be searched and scoped to a hierarchy."""
from __future__ import annotations

import os

import pytest

pytest.importorskip("PySide6")
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from pathlib import Path

from PySide6.QtWidgets import QApplication, QComboBox

from raptor.core.fileio.rbd import load_rbd
from raptor.gui.ui.element_picker import ScopeBox, hierarchy_paths, parent_of, searchable

GOLD = Path(__file__).resolve().parents[1] / "golden"


@pytest.fixture(scope="module")
def rbd():
    QApplication.instance() or QApplication([])
    return load_rbd(str(GOLD / "HierarchyExample" / "input.rbd"))


def _blocks(rbd):
    rows = [r for r in rbd.sections["BLOCK_DATA"].rows if int(float(r.get("ID", 0) or 0))]
    return rows, [int(float(r.get("ParentId") or 0)) for r in rows]


def test_scope_shows_only_what_is_inside_a_hierarchy(rbd):
    rows, homes = _blocks(rbd)
    combo = searchable(QComboBox())
    combo.addItems([str(r["blockName"]) for r in rows])
    scope = ScopeBox(rbd, combo, homes)
    assert scope.count() > 2 and scope.itemText(0) == "Whole model"
    assert scope.visible_rows() == list(range(len(rows)))
    scope.setCurrentIndex(1)                                   # top level only
    top = [i for i, h in enumerate(homes) if h == 0]
    assert scope.visible_rows() == top
    hid = next(h for h in homes if h)
    scope.setCurrentIndex(scope.findData(hid))
    shown = scope.visible_rows()
    assert shown and all(homes[i] == hid or homes[i] in _below(rbd, hid) for i in shown)
    assert combo.currentIndex() in shown                       # the choice moved inside


def _below(rbd, hid):
    paths = hierarchy_paths(rbd)
    return {h for h in paths if h != hid and paths[h][0].startswith(paths[hid][0] + " ▸ ")}


def test_typing_finds_an_entry_and_nonsense_is_put_back(rbd):
    combo = searchable(QComboBox())
    combo.addItems(["Pump_A", "Valve_B", "Pump_C"])
    combo.setCurrentIndex(1)
    completer = combo.completer()
    completer.setCompletionPrefix("ump_c")
    assert completer.currentCompletion() == "Pump_C"             # anywhere, any case
    combo.setEditText("no such block")
    combo.lineEdit().editingFinished.emit()
    assert combo.currentText() == "Valve_B"


def test_a_sweep_choice_knows_its_hierarchy(rbd):
    rows, homes = _blocks(rbd)
    bid = int(float(rows[homes.index(next(h for h in homes if h))]["ID"]))
    assert parent_of(rbd, "block", bid) != 0
    from raptor.gui.ui.batch_dialogs import SweepTarget, _home
    assert _home(rbd, [SweepTarget("x", f"block:{bid}.fail_params[0]", 1.0)]) == parent_of(rbd, "block", bid)
    assert _home(rbd, [SweepTarget("x", "rate:@r", 1.0)]) is None


def test_hierarchies_are_listed_by_name(rbd):
    names = {str(r["hierarchyName"]) for r in rbd.sections["HIERARCHY_DATA"].rows
             if int(float(r.get("ID", 0) or 0))}
    assert {path.split(" ▸ ")[-1] for path, _p in hierarchy_paths(rbd).values()} == names
