"""Auto Layout with a selection lays out just that group around its centre."""
from __future__ import annotations

import pytest
from PySide6.QtWidgets import QApplication

from raptor.api.build import build_from_spec
from raptor.gui.ui.diagram import DiagramView
from raptor.gui.ui.diagram_items import _LinkItem, set_flow_direction


@pytest.fixture(scope="module")
def qapp():
    return QApplication.instance() or QApplication([])


def _view():
    rbd = build_from_spec({
        "name": "Sel",
        "blocks": [{"name": f"B{i}"} for i in range(5)],
        "nodes": [{"id": 1, "name": "Start", "type": "start"},
                  {"id": 2, "name": "End", "type": "end"}],
        "links": [{"from": "Start", "to": "B0"}, {"from": "B0", "to": "B1"},
                  {"from": "B1", "to": "B2"}, {"from": "B2", "to": "B3"},
                  {"from": "B3", "to": "B4"}, {"from": "B4", "to": "End"}],
        "system": {"term_condition": 100, "num_runs": 1},
    }, auto_layout=False)
    view = DiagramView()
    view.load_rbd(rbd)
    return view


def test_selection_layout_keeps_centre_and_neighbours(qapp):
    v = _view()
    v.auto_layout(direction="LR")
    blocks = list(v._block_items.values())
    neighbours = [blocks[0], blocks[-1]]
    selected = [blocks[1], blocks[2]]
    for item in selected:
        item.setSelected(True)
    centres = [_LinkItem._true_centre(it) for it in selected]
    old_cx = (centres[0].x() + centres[1].x()) / 2.0
    old_cy = (centres[0].y() + centres[1].y()) / 2.0
    neighbour_pos = [(it.scenePos().x(), it.scenePos().y())
                     for it in neighbours]
    v.auto_layout(direction="TB")
    new_centres = [_LinkItem._true_centre(it) for it in selected]
    assert (new_centres[0].x() + new_centres[1].x()) / 2.0 == pytest.approx(old_cx)
    assert (new_centres[0].y() + new_centres[1].y()) / 2.0 == pytest.approx(old_cy)
    assert [(it.scenePos().x(), it.scenePos().y())
            for it in neighbours] == neighbour_pos
    set_flow_direction("auto")
