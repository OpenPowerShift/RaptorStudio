"""Interaction hot spots on a large reliability diagram."""
from __future__ import annotations

import time

import pytest
from PySide6.QtWidgets import QApplication

from raptor.api.build import build_from_spec
from raptor.gui.ui.diagram import DiagramView

N = 1_500


@pytest.fixture(scope="module")
def qapp():
    return QApplication.instance() or QApplication([])


def _model():
    blocks = [{"name": f"B{i}", "id": 10 + i} for i in range(N)]
    nodes = [{"id": 1, "name": "Start", "type": "start"},
             {"id": 2, "name": "End", "type": "end"}]
    links = [{"from": "Start", "to": "B0"}]
    links += [{"from": f"B{i}", "to": f"B{i + 1}"}
              for i in range(N - 1)]
    links += [{"from": f"B{N - 1}", "to": "End"}]
    return build_from_spec({
        "name": "Large", "blocks": blocks, "nodes": nodes, "links": links,
        "system": {"term_condition": 100, "num_runs": 1}},
        auto_layout=False)


def test_a_single_drag_syncs_only_the_moved_element(qapp, monkeypatch):
    view = DiagramView()
    view.load_rbd(_model())
    bid = next(iter(view._block_items))
    item = view._block_items[bid]
    seen = []
    original = view._write_positions_to_rows

    def capture(only=None):
        seen.append(only)
        original(only)

    monkeypatch.setattr(view, "_write_positions_to_rows", capture)
    item.setPos(item.pos().x() + 20.0, item.pos().y())
    view.flush_pending_moves()
    assert seen and seen[-1] == {("block", bid)}
    assert set(view._pos_overrides) == {("block", bid)}


def test_a_single_drag_is_not_proportional_to_level_size(qapp):
    view = DiagramView()
    view.load_rbd(_model())
    item = next(iter(view._block_items.values()))
    started = time.perf_counter()
    item.setPos(item.pos().x() + 20.0, item.pos().y())
    view.flush_pending_moves()
    elapsed = time.perf_counter() - started
    # Generous: guards against a return to walking every row/item per drag.
    assert elapsed < 0.5, f"one-block settle took {elapsed:.2f}s"
