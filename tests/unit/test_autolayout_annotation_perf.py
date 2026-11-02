"""Auto Layout must not resolve every element by linear row scan.

`follow_layout` builds an obstacle list for every drawn element. It used
to call `resolve_element` for each row, which scans that section again:
O(N^2). On a 5,000-block model a four-node hierarchy's Auto Layout took
about nine seconds before the row was passed directly.
"""
from __future__ import annotations

from raptor.api import annotations
import pytest

from raptor.api.build import build_from_spec


@pytest.fixture(scope="module")
def qapp():
    from PySide6.QtWidgets import QApplication
    return QApplication.instance() or QApplication([])


def _model(n: int = 500):
    return build_from_spec({
        "name": "Perf",
        "blocks": [{"name": f"B{i}", "id": 10 + i} for i in range(n)],
        "nodes": [{"id": 1, "name": "Start", "type": "start"},
                  {"id": 2, "name": "End", "type": "end"}],
        "links": ([{"from": "Start", "to": "B0"}]
                  + [{"from": f"B{i}", "to": f"B{i + 1}"}
                     for i in range(n - 1)]
                  + [{"from": f"B{n - 1}", "to": "End"}]),
        "system": {"term_condition": 100, "num_runs": 1},
    }, auto_layout=False)


def test_element_boxes_does_not_resolve_each_row(monkeypatch):
    rbd = _model()
    calls: list[object] = []

    def boom(*args, **kwargs):
        calls.append(args)
        raise AssertionError("resolve_element should not be used per row")

    monkeypatch.setattr(annotations, "resolve_element", boom)
    boxes = annotations._element_boxes(rbd)
    assert len(boxes) == 502          # 500 blocks + Start + End
    assert not calls


def test_auto_layout_on_a_small_level_is_fast_on_a_large_model(qapp):
    import time
    from raptor.gui.ui.diagram import DiagramView

    rbd = _model(1_500)
    hid = 900
    # Move a handful of elements into a child hierarchy.
    for i in range(4):
        rbd.sections["BLOCK_DATA"].rows.append({
            "ID": str(20000 + i), "blockName": f"S{i}",
            "ParentId": str(hid), "xPos": "0", "yPos": "0",
            "FDist": "1", "FParam1": "1000", "RDist": "1", "RParam1": "10",
        })
    rbd.sections["HIERARCHY_DATA"].rows.append({
        "ID": str(hid), "hierarchyName": "Small", "Parent": "0",
        "in": "4", "out": "5", "xPos": "0", "yPos": "0",
    })
    for nid in (4, 5):
        rbd.sections["NODE_DATA"].rows.append({
            "ID": str(nid), "Name": "SIn" if nid == 4 else "SOut",
            "Type": "4" if nid == 4 else "5", "parent": str(hid),
            "xPos": "0", "yPos": "0",
        })
    view = DiagramView()
    view.load_rbd(rbd)
    view.enter_level(hid)
    started = time.perf_counter()
    view.auto_layout()
    assert time.perf_counter() - started < 3.0
