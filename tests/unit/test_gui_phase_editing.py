"""The Phases dialog's Insert / Add / Delete / Clear: one undo step, files stay consistent, and equal real's saves."""
from __future__ import annotations

import os
from pathlib import Path

import pytest

pytest.importorskip("PySide6")
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication

from raptor.core.fileio.phase_edit import phase_problems
from raptor.core.fileio.rbd import load_rbd
from raptor.gui.ui.main_window import MainWindow
from raptor.gui.ui.phasing_dialog import PhasingDialog

DATA = Path(__file__).resolve().parents[1] / "data" / "phase_editing"
SECTIONS = ("PHASES", "BLOCK_DATA", "EVENT_DATA", "NODE_DATA")


@pytest.fixture(scope="module")
def qapp():
    return QApplication.instance() or QApplication([])


@pytest.fixture
def win(qapp):
    w = MainWindow()
    w._load_into_app(load_rbd(str(DATA / "input.rbd")), None)
    return w


def _snap(rbd):
    return {n: ([*rbd.sections[n].columns], [dict(r) for r in rbd.sections[n].rows]) for n in SECTIONS}


def test_insert_matches_real_and_undo_restores_everything(win):
    view = win._diagram_dock.view
    before = _snap(win._rbd)
    dlg = PhasingDialog(win)
    dlg._define.selectRow(0)
    dlg._insert_phase()
    assert phase_problems(win._rbd) == [] and win._rbd.counts["Phases"] == 6
    real = load_rbd(str(DATA / "real_ins1.rbd"))
    for n in ("BLOCK_DATA", "EVENT_DATA", "NODE_DATA"):
        assert win._rbd.sections[n].rows == real.sections[n].rows, n
    assert dlg._define.rowCount() == 6
    view._stack.undo()
    assert _snap(win._rbd) == before and win._rbd.counts["Phases"] == 5
    view._stack.redo()
    assert win._rbd.counts["Phases"] == 6 and phase_problems(win._rbd) == []


def test_add_delete_clear_through_the_dialog(win):
    dlg = PhasingDialog(win)
    dlg._add_phase()
    real_add = load_rbd(str(DATA / "real_add.rbd"))
    assert win._rbd.sections["NODE_DATA"].rows == real_add.sections["NODE_DATA"].rows
    dlg._define.selectRow(5)
    dlg._delete_phase()
    assert win._rbd.counts["Phases"] == 5 and phase_problems(win._rbd) == []
    dlg._clear_phases()
    assert win._rbd.counts["Phases"] == 0 and phase_problems(win._rbd) == []
    assert dlg._phases == []
    # an Apply with no phases writes no placeholder token
    dlg._apply()
    assert phase_problems(win._rbd) == []
    assert not [c for c in win._rbd.sections["BLOCK_DATA"].columns if c.startswith("P ")]


def test_hierarchy_cells_are_the_out_nodes_tokens(win):
    dlg = PhasingDialog(win)
    t = dlg._tables["hier"]
    assert [t.item(0, c).data(Qt_UserRole()) for c in range(2, 7)] == ["C", "A", "A", "A", "L"]
    # change phase 2 to Cut through the grid and Apply
    from raptor.gui.ui.phasing_dialog import _MODE_ROLE, _VALUE_ROLE
    it = t.item(0, 3)
    it.setData(_MODE_ROLE, "C")
    it.setData(_VALUE_ROLE, 0.0)
    dlg._apply()
    out = next(r for r in win._rbd.sections["NODE_DATA"].rows if r["Name"] == "out" and r["Phases"] == "1")
    assert [out[f"P{i:03d}"] for i in range(1, 6)] == ["C", "C", "1", "1", "L"]
    assert phase_problems(win._rbd) == []


def Qt_UserRole():
    from raptor.gui.ui.phasing_dialog import _MODE_ROLE
    return _MODE_ROLE
