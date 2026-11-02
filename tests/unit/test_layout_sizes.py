"""Auto layout uses the element's drawn size, not a fixed block box."""
from __future__ import annotations

import pytest
from PySide6.QtWidgets import QApplication

from raptor.api.build import build_from_spec
from raptor.core.fileio import element_size
from raptor.core.fileio.legacy_layout import layout_container_positions
from raptor.gui.ui.diagram import DiagramView


@pytest.fixture(scope="module")
def qapp():
    return QApplication.instance() or QApplication([])


def _model(names):
    blocks = [{"name": n, "id": i + 10} for i, n in enumerate(names)]
    nodes = [{"id": 1, "name": "Start", "type": "start"},
             {"id": 2, "name": "End", "type": "end"}]
    links = [{"from": "Start", "to": names[0]}]
    links += [{"from": names[i], "to": names[i + 1]}
              for i in range(len(names) - 1)]
    links += [{"from": names[-1], "to": "End"}]
    return build_from_spec({"name": "Sizes", "blocks": blocks,
                            "nodes": nodes, "links": links,
                            "system": {"term_condition": 100,
                                       "num_runs": 1}},
                           auto_layout=False)


def _long(n):
    return "VeryLongBlockName" + str(n) * 3


def test_long_name_layout_keeps_adjacent_columns_clear(qapp):
    names = [_long(1), _long(2)]
    model = _model(names)
    view = DiagramView()
    view.load_rbd(model)
    view.auto_layout()
    items = sorted(view._block_items.values(),
                   key=lambda it: it.sceneBoundingRect().left())
    assert len(items) == 2
    left, right = items
    assert left.sceneBoundingRect().right() < right.sceneBoundingRect().left()


def test_hierarchy_ovals_in_a_series_keep_one_row():
    members = [("node", 1)] + [("hier", i) for i in range(2, 6)] + [("node", 6)]
    edges = ([ (("node", 1), ("hier", 2)) ]
             + [(("hier", i), ("hier", i + 1)) for i in range(2, 5)]
             + [(("hier", 5), ("node", 6))])
    sizes = {k: (element_size.model_half_width(
        "NODE_DATA" if k[0] == "node" else "HIERARCHY_DATA",
        "H" if k[0] == "hier" else "", 64.0), 0.5)
        for k in members}
    pos = layout_container_positions(members, edges, [("node", 1)],
                                     pins={("node", 6)}, sizes=sizes,
                                     scale=64.0)
    assert len({round(pos[k][1], 6) for k in members}) == 1


def test_long_name_scale_reserves_more_room_than_a_short_one():
    short = build_from_spec({"name": "S", "blocks": [{"name": "A", "id": 1}],
                             "nodes": [{"id": 2, "name": "Start",
                                        "type": "start"},
                                       {"id": 3, "name": "End",
                                        "type": "end"}],
                             "links": [{"from": "Start", "to": "A"},
                                       {"from": "A", "to": "End"}],
                             "system": {"term_condition": 100,
                                        "num_runs": 1}},
                            auto_layout=False)
    long = _model([_long(9)])
    from raptor.core.fileio import view_transform
    assert view_transform._column_pitch_scale(long) >= \
        view_transform._column_pitch_scale(short)
