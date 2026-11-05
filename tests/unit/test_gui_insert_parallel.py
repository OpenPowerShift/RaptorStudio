"""Headless GUI tests for adding an element in parallel with another."""
from __future__ import annotations

import os

import pytest

pytest.importorskip("PySide6")

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QEvent, QPointF, Qt
from PySide6.QtGui import QMouseEvent
from PySide6.QtWidgets import QApplication

from raptor.api.build import build_from_spec
from raptor.gui.ui.diagram import DiagramView, _LinkItem


@pytest.fixture(scope="module")
def qapp():
    return QApplication.instance() or QApplication([])


@pytest.fixture(autouse=True)
def _reset_flow():
    from raptor.gui.ui.diagram_items import set_flow_direction
    set_flow_direction("auto")
    yield
    set_flow_direction("auto")


def _chain_spec() -> dict:
    return {
        "name": "ParallelInsert",
        "blocks": [{"name": "A"}, {"name": "B"}],
        "nodes": [{"id": 1, "name": "Start", "type": "start"},
                  {"id": 2, "name": "End", "type": "end"}],
        "links": [{"from": "Start", "to": "A"},
                  {"from": "A", "to": "B"},
                  {"from": "B", "to": "End"}],
        "system": {"term_condition": 100, "num_runs": 1},
    }


def _view(rbd) -> DiagramView:
    view = DiagramView()
    view.load_rbd(rbd)
    view.resize(1100, 800)
    view.show()
    return view


def _block_item(view: DiagramView, name: str):
    bid = next(i for i, block in view.blocks.items() if block.name == name)
    return view._block_items[bid]


def _links(view: DiagramView) -> set[tuple[str, str, str, str]]:
    return {(r["FromType"], r["FromID"], r["ToType"], r["ToID"])
            for r in view._rbd_ref.sections["LINK_DATA"].rows}


def _centre(item) -> QPointF:
    return _LinkItem._true_centre(item)


def _node_row(view: DiagramView, nid: int) -> dict:
    return next(r for r in view._rbd_ref.sections["NODE_DATA"].rows
                if int(r["ID"]) == nid)


def _snapshot(view: DiagramView):
    view.sync_model_positions()
    return ({k: (round(_centre(it).x(), 3), round(_centre(it).y(), 3))
             for k, it in view._level_items().items()}, _links(view))


def test_chain_gets_a_split_and_a_join_node(qapp):
    """Start and a block are not ordinary nodes, so both nodes are new."""
    view = _view(build_from_spec(_chain_spec(), auto_layout=True))
    nodes_before = set(view.nodes)

    new_id = view.insert_element_in_parallel("block", _block_item(view, "A"))

    assert new_id is not None
    added = set(view.nodes) - nodes_before
    assert len(added) == 2
    split, join = sorted(added)
    links = _links(view)
    assert ("Node", "1", "Node", str(split)) in links       # Start -> split
    assert ("Node", str(split), "Block", "1") in links      # split -> A
    assert ("Node", str(split), "Block", str(new_id)) in links
    assert ("Block", "1", "Node", str(join)) in links       # A -> join
    assert ("Block", str(new_id), "Node", str(join)) in links
    assert ("Node", str(join), "Block", "2") in links       # join -> B
    assert ("Block", "1", "Block", "2") not in links
    assert view.nodes[join].good_paths == 1                 # 1 of 2
    assert _node_row(view, join)["n"] == "2"
    assert view._block_items[new_id].isSelected()


def test_a_branch_of_a_group_gets_one_more_branch(qapp):
    """Ordinary nodes on both sides are reused: no new nodes, and the new
    element joins the group as another branch."""
    view = _view(build_from_spec(_chain_spec(), auto_layout=True))
    a = _block_item(view, "A")
    view.insert_element_in_parallel("block", a)
    nodes_before = set(view.nodes)

    third = view.insert_element_in_parallel("event", _block_item(view, "A"))

    assert third is not None
    assert set(view.nodes) == nodes_before
    split, join = sorted(nodes_before - {1, 2})
    links = _links(view)
    assert ("Node", str(split), "Event", str(third)) in links
    assert ("Event", str(third), "Node", str(join)) in links
    assert _node_row(view, join)["n"] == "3"


def test_a_reused_join_node_keeps_its_k(qapp):
    """Adding one more in parallel is the intent, even if it changes the
    join node's voting: its k is left alone."""
    view = _view(build_from_spec(_chain_spec(), auto_layout=True))
    view.insert_element_in_parallel("block", _block_item(view, "A"))
    join = max(view.nodes)
    view.nodes[join].good_paths = 2
    _node_row(view, join)["k"] = "2"

    view.insert_element_in_parallel("block", _block_item(view, "A"))

    assert view.nodes[join].good_paths == 2
    assert _node_row(view, join)["n"] == "3"


def test_downstream_moves_and_upstream_stays(qapp):
    view = _view(build_from_spec(_chain_spec(), auto_layout=True))
    start = view._node_items[1]
    start_before = _centre(start)
    b_before = _centre(_block_item(view, "B")).x()
    end_before = _centre(view._node_items[2]).x()

    view.insert_element_in_parallel("block", _block_item(view, "A"))

    assert _centre(view._node_items[1]) == start_before
    b_shift = _centre(_block_item(view, "B")).x() - b_before
    end_shift = _centre(view._node_items[2]).x() - end_before
    assert b_shift > 0
    assert end_shift == pytest.approx(b_shift, abs=0.5)
    # The branches sit either side of the line through the split node.
    a_y = _centre(_block_item(view, "A")).y()
    new_y = _centre(view._block_items[max(view.blocks)]).y()
    assert (a_y - start_before.y()) * (new_y - start_before.y()) < 0


def test_one_undo_restores_everything_and_redo_reapplies(qapp):
    view = _view(build_from_spec(_chain_spec(), auto_layout=True))
    before = _snapshot(view)

    view.insert_element_in_parallel("block", _block_item(view, "A"))
    after = _snapshot(view)
    view._stack.undo()
    assert _snapshot(view) == before
    view._stack.redo()
    assert _snapshot(view) == after


@pytest.mark.parametrize("kind,table", [("block", "blocks"),
                                        ("event", "events"),
                                        ("hier", "hiers")])
def test_blocks_events_and_hierarchies_can_go_in_parallel(qapp, kind, table):
    view = _view(build_from_spec(_chain_spec(), auto_layout=True))
    new_id = view.insert_element_in_parallel(kind, _block_item(view, "A"))
    assert new_id is not None
    assert new_id in getattr(view, table)


def test_a_node_and_an_unconnected_target_are_refused(qapp):
    view = _view(build_from_spec(_chain_spec(), auto_layout=True))
    before = _links(view)
    assert view.insert_element_in_parallel(
        "node", _block_item(view, "A")) is None
    loose = view.add_block("Loose", 0.0, 300.0)
    assert view.insert_element_in_parallel(
        "block", view._block_items[loose]) is None
    assert _links(view) == before


def _mouse(view: DiagramView, kind, scene_pos: QPointF, button):
    point = QPointF(view.mapFromScene(scene_pos))
    return QMouseEvent(kind, point, point, button, button,
                       Qt.KeyboardModifier.NoModifier)


def test_hover_highlights_the_element_and_click_adds_in_parallel(qapp):
    view = _view(build_from_spec(_chain_spec(), auto_layout=True))
    view.tool_mode = "add_block"
    a = _block_item(view, "A")
    pos = _centre(a)
    view.mouseMoveEvent(_mouse(view, QEvent.Type.MouseMove, pos,
                               Qt.MouseButton.NoButton))
    assert view._insert_hover_link is a
    assert view._insert_hover_state == "allowed"
    assert view._insert_halo is not None and view._insert_halo.isVisible()
    blocks_before = set(view.blocks)

    view.mousePressEvent(_mouse(view, QEvent.Type.MouseButtonPress, pos,
                                Qt.MouseButton.LeftButton))

    assert len(set(view.blocks) - blocks_before) == 1
    assert view._insert_halo is None


def test_hover_on_an_unconnected_element_is_blocked_and_click_refused(qapp):
    view = _view(build_from_spec(_chain_spec(), auto_layout=True))
    loose = view._block_items[view.add_block("Loose", 0.0, 300.0)]
    view.tool_mode = "add_block"
    pos = _centre(loose)
    view.mouseMoveEvent(_mouse(view, QEvent.Type.MouseMove, pos,
                               Qt.MouseButton.NoButton))
    assert view._insert_hover_link is loose
    assert view._insert_hover_state == "blocked"
    blocks_before = set(view.blocks)

    view.mousePressEvent(_mouse(view, QEvent.Type.MouseButtonPress, pos,
                                Qt.MouseButton.LeftButton))

    assert set(view.blocks) == blocks_before


def _hover_status(view: DiagramView, item) -> list[str]:
    messages: list[str] = []
    view.status_message_sink = messages.append
    view.mouseMoveEvent(_mouse(view, QEvent.Type.MouseMove, _centre(item),
                               Qt.MouseButton.NoButton))
    return messages


def test_hover_in_a_chain_outlines_only_the_target(qapp):
    view = _view(build_from_spec(_chain_spec(), auto_layout=True))
    view.tool_mode = "add_block"
    messages = _hover_status(view, _block_item(view, "A"))
    assert len(view._insert_halos) == 1
    assert messages[-1] == (
        "Add Block in parallel with A with a new split node and a new join "
        "node — click to place")


def test_hover_beside_a_group_outlines_the_group_it_will_join(qapp):
    view = _view(build_from_spec(_chain_spec(), auto_layout=True))
    view.insert_element_in_parallel("block", _block_item(view, "A"))
    split, join = sorted(set(view.nodes) - {1, 2})
    view.tool_mode = "add_block"
    view._scene.clearSelection()

    messages = _hover_status(view, _block_item(view, "A"))

    # The target, the two reused nodes and the other branch.
    assert len(view._insert_halos) == 4
    assert view._insert_halo.opacity() > view._insert_halos[-1].opacity()
    split_name = view._endpoint_text(view._node_items[split])
    join_name = view._endpoint_text(view._node_items[join])
    # The canvas names, not "Node 3".
    assert split_name == view.nodes[split].node_name
    assert join_name == view.nodes[join].node_name
    assert messages[-1] == (
        f"Add Block in parallel with A as branch 3 between {split_name} and "
        f"{join_name} ({join_name} becomes 1/3) — click to place")
    # Moving within the same target does not rebuild the outlines.
    first = list(view._insert_halos)
    _hover_status(view, _block_item(view, "A"))
    assert view._insert_halos == first


def test_moving_off_the_target_removes_every_outline(qapp):
    view = _view(build_from_spec(_chain_spec(), auto_layout=True))
    view.insert_element_in_parallel("block", _block_item(view, "A"))
    view.tool_mode = "add_block"
    _hover_status(view, _block_item(view, "A"))
    halos = list(view._insert_halos)
    assert halos
    far = QPointF(_centre(_block_item(view, "A")).x(), 5000.0)
    view.mouseMoveEvent(_mouse(view, QEvent.Type.MouseMove, far,
                               Qt.MouseButton.NoButton))
    assert view._insert_halos == []
    assert all(h.scene() is None for h in halos)
