"""Headless GUI tests for inserting an element in series on a link."""
from __future__ import annotations

import os

import pytest

pytest.importorskip("PySide6")

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QEvent, QPointF, Qt
from PySide6.QtGui import QMouseEvent
from PySide6.QtWidgets import QApplication

from raptor.api.build import build_from_spec
from raptor.gui.ui.diagram import DiagramView, _BlockItem, _LinkItem


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
        "name": "InsertOnLink",
        "blocks": [{"name": "A"}, {"name": "B"}],
        "nodes": [{"id": 1, "name": "Start", "type": "start"},
                  {"id": 2, "name": "End", "type": "end"}],
        "links": [{"from": "Start", "to": "A"},
                  {"from": "A", "to": "B"},
                  {"from": "B", "to": "End"}],
        "system": {"term_condition": 100, "num_runs": 1},
    }


def _view(rbd, *, show: bool = True) -> DiagramView:
    view = DiagramView()
    view.load_rbd(rbd)
    view.resize(1100, 800)
    if show:
        view.show()
    return view


def _block_id(view: DiagramView, name: str) -> int:
    return next(i for i, block in view.blocks.items() if block.name == name)


def _link_between(view: DiagramView, from_name: str,
                  to_name: str) -> _LinkItem:
    from_id = _block_id(view, from_name)
    to_id = _block_id(view, to_name)
    return next(
        link for link in view._links
        if isinstance(link._from, _BlockItem)
        and isinstance(link._to, _BlockItem)
        and link._from._block_id == from_id
        and link._to._block_id == to_id
    )


def _mouse_event(view: DiagramView, kind, scene_pos: QPointF,
                 button: Qt.MouseButton) -> QMouseEvent:
    point = QPointF(view.mapFromScene(scene_pos))
    buttons = button
    return QMouseEvent(kind, point, point, button, buttons,
                       Qt.KeyboardModifier.NoModifier)


def _hover(view: DiagramView, scene_pos: QPointF) -> None:
    view.mouseMoveEvent(_mouse_event(
        view, QEvent.Type.MouseMove, scene_pos, Qt.MouseButton.NoButton))


def _click(view: DiagramView, scene_pos: QPointF) -> None:
    view.mousePressEvent(_mouse_event(
        view, QEvent.Type.MouseButtonPress, scene_pos,
        Qt.MouseButton.LeftButton))


def test_hover_allows_and_click_inserts_block(qapp):
    rbd = build_from_spec(_chain_spec(), auto_layout=True)
    view = _view(rbd)
    view.tool_mode = "add_block"
    link = _link_between(view, "A", "B")
    point = link.path().pointAtPercent(0.5)

    _hover(view, point)
    assert view._insert_hover_link is link
    assert view._insert_hover_state == "allowed"
    assert link._insert_highlight == "allowed"

    moved_before = view._block_items[_block_id(view, "B")].scenePos()
    _click(view, point)

    assert len(view._block_items) == 3
    assert link._link_num not in [item._link_num for item in view._links]
    assert len(view._links) == 4
    assert view.tool_mode == "add_block"
    assert view._block_items[_block_id(view, "B")].scenePos() != moved_before
    assert any(item.isSelected() for item in view._block_items.values())

    view._stack.undo()
    assert len(view._block_items) == 2
    assert sorted(link._link_num for link in view._links) == [3, 4, 5]
    assert view._block_items[_block_id(view, "B")].scenePos() == moved_before


def test_ambiguous_overlapping_links_block_the_click(qapp):
    rbd = build_from_spec(_chain_spec(), auto_layout=True)
    section = rbd.sections["LINK_DATA"]
    original = next(row for row in section.rows
                    if row.get("FromType") == "Block"
                    and row.get("FromID") == "1"
                    and row.get("ToType") == "Block"
                    and row.get("ToID") == "2")
    duplicate = dict(original)
    duplicate["ID"] = "99"
    section.rows.append(duplicate)
    view = _view(rbd)
    view.tool_mode = "add_block"
    link = next(item for item in view._links if item._link_num == 4)
    point = link.path().pointAtPercent(0.5)

    _hover(view, point)
    assert view._insert_hover_state == "blocked"
    assert view._insert_hover_link._link_num in {4, 99}
    assert view._insert_hover_link._insert_highlight == "blocked"
    before = len(view._block_items)
    _click(view, point)
    assert len(view._block_items) == before


def test_insertion_leaves_more_than_the_bare_minimum_gap(qapp):
    from raptor.gui.ui.diagram_common import MIN_ELEMENT_GAP

    rbd = build_from_spec(_chain_spec(), auto_layout=True)
    view = _view(rbd, show=False)
    link = _link_between(view, "A", "B")
    a_item = link._from
    b_item = link._to

    oid = view.insert_element_on_link("block", link._link_num)
    new_item = view._block_items[oid]

    a_span = view._span_of(view._element_shape_rect(a_item), "x")
    new_span = view._span_of(view._element_shape_rect(new_item), "x")
    b_span = view._span_of(view._element_shape_rect(b_item), "x")
    assert new_span[0] - a_span[1] > MIN_ELEMENT_GAP
    assert b_span[0] - new_span[1] > MIN_ELEMENT_GAP


def test_only_the_downstream_subgraph_moves(qapp):
    spec = {
        "name": "InsertBranch",
        "blocks": [{"name": "A"}, {"name": "B"}, {"name": "C"}],
        "nodes": [{"id": 1, "name": "Start", "type": "start"},
                  {"id": 2, "name": "End", "type": "end"}],
        "links": [{"from": "Start", "to": "A"},
                  {"from": "A", "to": "B"},
                  {"from": "A", "to": "C"},
                  {"from": "B", "to": "End"},
                  {"from": "C", "to": "End"}],
        "system": {"term_condition": 100, "num_runs": 1},
    }
    rbd = build_from_spec(spec, auto_layout=True)
    view = _view(rbd)
    c_before = view._block_items[_block_id(view, "C")].scenePos()
    b_before = view._block_items[_block_id(view, "B")].scenePos()
    link = _link_between(view, "A", "B")

    assert view.insert_element_on_link("block", link._link_num) is not None

    assert view._block_items[_block_id(view, "C")].scenePos() == c_before
    assert view._block_items[_block_id(view, "B")].scenePos() != b_before


@pytest.mark.parametrize("kind,table_name", [
    ("block", "_block_items"),
    ("node", "_node_items"),
    ("event", "_event_items"),
    ("hier", "_hier_items"),
])
def test_all_four_element_kinds_can_be_inserted(qapp, kind, table_name):
    rbd = build_from_spec(_chain_spec(), auto_layout=True)
    view = _view(rbd, show=False)
    link = _link_between(view, "A", "B")

    oid = view.insert_element_on_link(kind, link._link_num)

    assert oid is not None
    table = getattr(view, table_name)
    assert oid in table
    assert len(view._links) == 4
    noun = {"block": "Block", "node": "Node", "event": "Event",
            "hier": "Hierarchy"}[kind]
    assert view._stack.undoText() == f"Insert {noun} on link"
    view._stack.undo()
    assert oid not in getattr(view, table_name)
    assert sorted(item._link_num for item in view._links) == [3, 4, 5]


def test_dog_legs_are_split_and_undo_restores_them(qapp):
    rbd = build_from_spec(_chain_spec(), auto_layout=True)
    view = _view(rbd)
    view._snap_state["x"] = view._snap_state["y"] = 0.0
    link = _link_between(view, "A", "B")
    for point in (QPointF(220.0, 30.0), QPointF(260.0, -30.0)):
        mx, my = view.scene_to_model(point)
        grip = view.insert_routing_node(link._link_num, mx, my)
        grip._active = True
        for entry in rbd.link_grips:
            if entry["id"] == grip._entry_id:
                entry["active"] = True
    grips = link._grips
    grips[0].setPos(220.0, 30.0)
    grips[1].setPos(260.0, -30.0)
    link._update_route()
    old_entries = [dict(entry) for entry in rbd.link_grips]

    oid = view.insert_element_on_link("block", link._link_num)
    assert oid is not None

    new_links = [item for item in view._links
                 if item._link_num not in {3, 5}]
    upstream = next(item for item in new_links
                    if isinstance(item._from, _BlockItem)
                    and isinstance(item._to, _BlockItem)
                    and item._to._block_id == oid)
    downstream = next(item for item in new_links
                      if isinstance(item._from, _BlockItem)
                      and isinstance(item._to, _BlockItem)
                      and item._from._block_id == oid)
    moved = {
        entry["id"]: entry
        for entry in rbd.link_grips
    }
    assert moved[old_entries[0]["id"]]["link"] == upstream._link_num
    assert moved[old_entries[0]["id"]]["x"] == pytest.approx(220.0)
    assert moved[old_entries[1]["id"]]["link"] == downstream._link_num
    assert moved[old_entries[1]["id"]]["x"] > 260.0
    assert set(view._route_grips) == {
        upstream._link_num, downstream._link_num}

    view._stack.undo()
    restored = {entry["id"]: entry for entry in rbd.link_grips}
    assert restored == {entry["id"]: entry for entry in old_entries}


def test_new_links_keep_the_split_links_settings(qapp):
    """Link columns are simulation inputs (cold-standby priority and
    switching, capacity priority and flows). Splitting must keep the clicked
    link's values, not reset them to the first LINK_DATA row's."""
    rbd = build_from_spec(_chain_spec(), auto_layout=True)
    section = rbd.sections["LINK_DATA"]
    target = next(row for row in section.rows
                  if row.get("FromType") == "Block"
                  and row.get("FromID") == "1"
                  and row.get("ToType") == "Block"
                  and row.get("ToID") == "2")
    assert section.rows[0] is not target
    # The first row (the Connect template) gets different values, so the
    # test fails if the halves are cloned from it.
    section.rows[0].update({
        "ColdPri": "50", "AProb": "1.000000", "AutoTime": "0.000000",
        "ManTime": "0.000000", "CapPri": "50", "nomFlow": "-2",
        "MaxFlow": "-1"})
    target.update({
        "ColdPri": "7", "AProb": "0.250000", "AutoTime": "1.500000",
        "ManTime": "2.500000", "CapPri": "3", "nomFlow": "123",
        "MaxFlow": "456", "pID": "42"})
    view = _view(rbd, show=False)
    link = _link_between(view, "A", "B")
    old_id = link._link_num
    before_ids = {row["ID"] for row in section.rows}

    assert view.insert_element_on_link("block", link._link_num) is not None

    created = [row for row in section.rows if row["ID"] not in before_ids]
    assert len(created) == 2
    for row in created:
        assert row["ID"] != str(old_id)
        assert row["ColdPri"] == "7"
        assert row["AProb"] == "0.250000"
        assert row["AutoTime"] == "1.500000"
        assert row["ManTime"] == "2.500000"
        assert row["CapPri"] == "3"
        assert row["nomFlow"] == "123"
        assert row["MaxFlow"] == "456"
        assert row["pID"] == "42"


@pytest.mark.parametrize("direction", ["RL", "TB", "BT"])
def test_direction_follows_the_page_flow(qapp, direction):
    rbd = build_from_spec(_chain_spec(), auto_layout=True)
    view = _view(rbd, show=False)
    view.auto_layout(direction=direction)
    link = _link_between(view, "A", "B")
    b_before = view._block_items[_block_id(view, "B")].scenePos()

    assert view.insert_element_on_link("block", link._link_num) is not None

    b_after = view._block_items[_block_id(view, "B")].scenePos()
    if direction == "RL":
        assert b_after.x() < b_before.x()
    elif direction == "TB":
        assert b_after.y() > b_before.y()
    else:
        assert b_after.y() < b_before.y()


def test_no_downstream_move_when_the_link_has_enough_room(qapp):
    rbd = build_from_spec(_chain_spec(), auto_layout=True)
    view = _view(rbd, show=False)
    view._snap_state["x"] = view._snap_state["y"] = 0.0
    b_item = view._block_items[_block_id(view, "B")]
    end_item = view._node_items[next(
        oid for oid, node in view.nodes.items() if node.type_node == 3)]
    b_item.setPos(b_item.scenePos() + QPointF(300.0, 0.0))
    end_item.setPos(end_item.scenePos() + QPointF(300.0, 0.0))
    view.sync_model_positions()
    b_before = b_item.scenePos()
    link = _link_between(view, "A", "B")

    oid = view.insert_element_on_link("block", link._link_num)

    assert oid is not None
    assert b_item.scenePos() == b_before
    new_item = view._block_items[oid]
    assert new_item.scenePos().x() < b_before.x()


def test_click_in_place_without_a_prior_mouse_move(qapp):
    rbd = build_from_spec(_chain_spec(), auto_layout=True)
    view = _view(rbd, show=False)
    view.tool_mode = "add_node"
    link = _link_between(view, "A", "B")

    _click(view, link.path().pointAtPercent(0.5))

    assert len(view._node_items) == 3
    assert len(view._links) == 4
