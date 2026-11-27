"""Arrange ▸ Lock Positions (#352): nothing moves until unlocked, and the
lock is saved with the model."""
from __future__ import annotations

import pytest
from PySide6.QtCore import QPointF, Qt
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication

from raptor.core.fileio.rbds import load_rbds, save_rbds
from tests.unit.test_gui_multidrag import _drag, _spots, _view


@pytest.fixture(scope="module")
def qapp():
    return QApplication.instance() or QApplication([])


def test_a_locked_canvas_refuses_drags_and_arrange_commands(qapp):
    v = _view()
    v.auto_layout(direction="LR")
    before = _spots(v)
    v.set_positions_locked(True)
    b = v._block_items[sorted(v._block_items)[1]]
    _drag(qapp, v, v.mapFromScene(b.sceneBoundingRect().center()),
          v.mapFromScene(b.sceneBoundingRect().center() + QPointF(120, 80)))
    assert b.isSelected()                     # the click still selects
    assert _spots(v) == before
    for item in v._block_items.values():
        item.setSelected(True)
    v.setFocus()
    QTest.keyClick(v, Qt.Key.Key_Right)
    v.align_selected("top")
    v.distribute_selected("h")
    assert v.flip_selected("h") is False
    v.auto_layout(direction="TB")
    qapp.processEvents()
    assert _spots(v) == before
    v.set_positions_locked(False)
    QTest.keyClick(v, Qt.Key.Key_Right)
    assert _spots(v) != before


def test_the_lock_is_saved_only_when_on(qapp, tmp_path):
    from raptor.api.build import build_from_spec
    from raptor.gui.ui.diagram import DiagramView
    rbd = build_from_spec({
        "name": "Lock", "blocks": [{"name": "A"}],
        "nodes": [{"id": 1, "name": "Start", "type": "start"},
                  {"id": 2, "name": "End", "type": "end"}],
        "links": [{"from": "Start", "to": "A"}, {"from": "A", "to": "End"}],
        "system": {"term_condition": 100, "num_runs": 1}})
    v = DiagramView()
    v.load_rbd(rbd)
    assert "positions_locked" not in v.view_options()
    v.set_positions_locked(True)
    v.write_back_to_rbd(rbd)
    path = tmp_path / "locked.rbds"
    save_rbds(rbd, str(path))
    again = load_rbds(str(path))
    assert again.view.get("positions_locked") == "1"
    fresh = DiagramView()
    fresh.apply_view_options(again.view)
    assert fresh.positions_locked
    fresh.apply_view_options({})              # another model, not locked
    assert not fresh.positions_locked
