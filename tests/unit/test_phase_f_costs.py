"""Costs tab: all 14 block cost fields are editable via a single Apply (one
undoable ParamsCmd "costs"), with immediate BLOCK_DATA sync and a
save/reload round-trip.
"""
from __future__ import annotations

import os

import pytest

pytest.importorskip("PySide6")

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication

from raptor.core.fileio.rbd import load_rbd, save_rbd
from raptor.gui.ui.diagram import DiagramView
from raptor.gui.ui.main_window import MainWindow

REPO = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
app = None


@pytest.fixture(scope="module")
def qapp():
    global app
    app = QApplication.instance() or QApplication([])
    return app


def test_costs_edit_roundtrip(qapp):
    w = MainWindow()
    w._load_into_app(load_rbd(
        os.path.join(REPO, "tests/golden/Capacity1/input.rbd")), None)
    v = w._diagram_dock.view
    bid = next(iter(v.blocks))
    blk = v.blocks[bid]

    vals = [7.5] + [2.25] * 13
    v.set_block_costs(bid, vals)

    assert blk.initial_cost == pytest.approx(7.5)
    row = next(r for r in v._rbd_ref.sections["BLOCK_DATA"].rows
               if int(float(r["ID"])) == bid)
    for col, want in (("initial", 7.5), ("Running", 2.25),
                      ("perSpare", 2.25), ("emerShipping", 2.25)):
        assert float(row[col]) == pytest.approx(want), col

    path = os.path.join(REPO, "..", "tmp_cost_rt.rbd")
    save_rbd(v._rbd_ref, path)
    from raptor.core.fileio.rbd import load_rbd as lr
    row2 = next(r for r in lr(path).sections["BLOCK_DATA"].rows
                if int(float(r["ID"])) == bid)
    assert float(row2["initial"]) == pytest.approx(7.5)

    # a single ungrouped command: undo restores all 14 at once
    w._undo_stack.undo()
    assert blk.initial_cost != pytest.approx(7.5)
    assert float(row["initial"]) != pytest.approx(7.5)


def test_costs_noop_skips_command(qapp):
    w = MainWindow()
    w._load_into_app(load_rbd(
        os.path.join(REPO, "tests/golden/Capacity1/input.rbd")), None)
    v = w._diagram_dock.view
    bid = next(iter(v.blocks))
    depth = v._stack.count()
    current = [getattr(v.blocks[bid], attr)
               for attr, _col in DiagramView.BLOCK_COST_FIELDS]
    v.set_block_costs(bid, current)
    assert v._stack.count() == depth
