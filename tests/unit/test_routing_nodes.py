"""GUI-only routing grips: drag points on a link, not model nodes.

A grip changes only the drawn dog-leg. It has no `NODE_DATA` row, no
properties and must be invisible to EFPA, capacity flow and dependencies.
"""
from __future__ import annotations

import pathlib

import pytest
from PySide6.QtCore import QPointF
from PySide6.QtWidgets import QApplication, QGraphicsItem

from raptor.core.engine.runner import run_simulation
from raptor.core.fileio.rbd import load_rbd, save_rbd
from raptor.core.fileio.rbds import load_rbds, save_rbds
from raptor.core.fileio.model_file import load_model_file
from raptor.gui.ui.diagram import DiagramView

REPO = pathlib.Path(__file__).resolve().parents[2]
FIXTURE = REPO / "tests/golden/TwoBlockRedundancy/input.rbd"
CAPACITY_FIXTURE = REPO / "tests/golden/Capacity1/input.rbd"


@pytest.fixture(scope="module")
def qapp():
    return QApplication.instance() or QApplication([])


def _view(path=FIXTURE):
    v = DiagramView()
    v.load_rbd(load_model_file(str(path)))
    return v


def _open_shown_view(qapp, path=FIXTURE):
    v = _view(path)
    v.resize(700, 400)
    v.show()
    qapp.processEvents()
    return v


def _link_rows(view):
    return view._rbd_ref.sections["LINK_DATA"].rows


def _node_rows(view):
    return view._rbd_ref.sections["NODE_DATA"].rows


def _right_click_menu(v, scene_pos, chosen_prefix=None):
    vp = v.mapFromScene(scene_pos)

    class _Event:
        def position(self):
            return QPointF(vp)

        def accept(self):
            pass

    event = _Event()
    captured = []

    def fake_exec(menu, _event):
        captured.extend((a.text(), a) for a in menu.actions())
        if chosen_prefix is None:
            return None
        return next((a for text, a in captured
                     if text.startswith(chosen_prefix)), None)

    v._exec_menu = fake_exec
    v._show_context_menu(event)
    return captured


def test_context_menu_adds_a_grip_without_touching_the_model(qapp):
    v = _open_shown_view(qapp)
    link = v._links[0]
    link_id = link._link_num
    links_before = [dict(r) for r in _link_rows(v)]
    nodes_before = [dict(r) for r in _node_rows(v)]
    status = []
    v.status_message_sink = status.append

    _right_click_menu(v, link.line().center(),
                      chosen_prefix="Add routing point")
    assert status == ["Routing point added"]
    assert _link_rows(v) == links_before
    assert _node_rows(v) == nodes_before
    assert link_id in v._route_grips
    grip = v._route_grips[link_id][0]
    assert grip._link is link
    assert grip.parentItem() is link
    assert v._rbd_ref.link_grips[-1]["link"] == link_id


def test_new_grip_starts_exactly_on_the_drawn_link(qapp):
    v = _open_shown_view(qapp)
    link = v._links[0]
    away = link.line().center() + QPointF(18.0, 25.0)
    mx, my = v._vt.to_model(away.x(), away.y())
    grip = v.insert_routing_node(link._link_num, mx, my)
    assert grip is not None
    nearest = link.nearest_point(grip.scenePos())
    assert (grip.scenePos() - nearest).manhattanLength() == pytest.approx(0.0)


def test_adding_a_grip_preserves_the_routes_in_every_link_style(qapp):
    from raptor.gui.ui.diagram_items import set_link_routing

    def points(link):
        return [(link.path().elementAt(i).x, link.path().elementAt(i).y)
                for i in range(link.path().elementCount())]

    try:
        for mode in ("straight", "angled", "orthogonal"):
            set_link_routing(mode)
            v = _view()
            v.reroute_links()
            link = v._links[0]
            before = points(link)
            scene = link.line().center() + QPointF(20.0, 15.0)
            mx, my = v._vt.to_model(scene.x(), scene.y())
            assert v.insert_routing_node(link._link_num, mx, my)
            assert points(link) == before, mode
    finally:
        set_link_routing("straight")


def test_moving_a_grip_snaps_to_the_canvas_grid(qapp):
    v = _open_shown_view(qapp)
    link = v._links[0]
    grip = v.insert_routing_node(link._link_num, 1.25, 92.5)
    v._snap_state["x"], v._snap_state["y"] = 20.0, 10.0
    grip.setPos(QPointF(23.0, 47.0))
    assert (grip.pos().x(), grip.pos().y()) == (20.0, 50.0)
    v._snap_state["x"] = v._snap_state["y"] = 0.0
    grip.setPos(QPointF(23.0, 47.0))
    assert (grip.pos().x(), grip.pos().y()) == (23.0, 47.0)


def test_menu_style_model_coordinates_place_the_grip_on_the_link(qapp):
    v = _open_shown_view(qapp)
    link = v._links[0]
    scene = link.line().center()
    mx, my = v._vt.to_model(scene.x(), scene.y())
    grip = v.insert_routing_node(link._link_num, mx, my)
    assert grip is not None
    assert grip.scenePos().x() == pytest.approx(scene.x())
    assert grip.scenePos().y() == pytest.approx(scene.y())
    assert v.viewport().rect().contains(
        v.mapFromScene(grip.scenePos()))


def test_new_grip_leaves_the_link_path_unchanged_until_dragged(qapp):
    v = _view()
    link = v._links[0]
    before = [(link.path().elementAt(i).x, link.path().elementAt(i).y)
              for i in range(link.path().elementCount())]
    grip = v.insert_routing_node(link._link_num, 5.0, 50.0)
    assert grip is not None
    assert grip._active is False
    after = [(link.path().elementAt(i).x, link.path().elementAt(i).y)
             for i in range(link.path().elementCount())]
    assert after == before
    grip.setPos(grip.pos() + QPointF(20.0, 10.0))
    qapp.processEvents()
    assert grip._active is True
    assert link.path().elementCount() > len(before)
    assert v._rbd_ref.link_grips[0]["active"] is True


def test_auto_layout_removes_routing_points_and_undo_restores_them(qapp):
    v = _view()
    link = v._links[0]
    grip = v.insert_routing_node(link._link_num, 5.0, 50.0)
    grip.setPos(grip.pos() + QPointF(20.0, 10.0))
    assert v._rbd_ref.link_grips
    v.auto_layout(direction="LR")
    assert not v._rbd_ref.link_grips
    assert not v._route_grips
    v._stack.undo()
    assert len(v._rbd_ref.link_grips) == 1
    assert v._route_grips


def test_grip_is_selectable_movable_and_bends_the_link(qapp):
    v = _view()
    link = v._links[0]
    grip = v.insert_routing_node(link._link_num, 5.0, 50.0)
    assert grip is not None
    assert grip.flags() & QGraphicsItem.GraphicsItemFlag.ItemIsMovable
    assert grip.flags() & QGraphicsItem.GraphicsItemFlag.ItemIsSelectable
    grip.setSelected(True)
    assert grip in v._scene.selectedItems()
    count_before = link.path().elementCount()
    grip.setPos(grip.pos() + QPointF(30.0, 20.0))
    qapp.processEvents()
    assert link.path().elementCount() >= count_before
    entry = v._rbd_ref.link_grips[0]
    assert (entry["x"], entry["y"]) == (round(grip.pos().x(), 2),
                                        round(grip.pos().y(), 2))


def test_grip_add_and_remove_are_one_undo_step_each(qapp):
    v = _view()
    link = v._links[0]
    grip = v.insert_routing_node(link._link_num, 5.0, 50.0)
    assert grip is not None
    assert link.grip_items() == [grip]
    v._stack.undo()
    assert link.grip_items() == []
    assert not v._rbd_ref.link_grips
    v._stack.redo()
    assert len(link.grip_items()) == 1
    assert len(v._rbd_ref.link_grips) == 1


def test_click_selects_a_grip_and_delete_keeps_the_link(qapp):
    from PySide6.QtCore import Qt
    from PySide6.QtTest import QTest

    v = _open_shown_view(qapp)
    link = v._links[0]
    links_before = [dict(r) for r in _link_rows(v)]
    nodes_before = [dict(r) for r in _node_rows(v)]
    grip = v.insert_routing_node(link._link_num, 5.0, 50.0)
    QTest.mouseClick(v.viewport(), Qt.MouseButton.LeftButton,
                     Qt.KeyboardModifier.NoModifier,
                     v.mapFromScene(grip.scenePos()))
    qapp.processEvents()
    assert grip.isSelected()
    v.delete_selected()
    qapp.processEvents()
    assert link.grip_items() == []
    assert link in v._links
    assert _link_rows(v) == links_before
    assert _node_rows(v) == nodes_before


def test_right_click_grip_offers_delete_and_keeps_the_link(qapp):
    v = _open_shown_view(qapp)
    link = v._links[0]
    links_before = [dict(r) for r in _link_rows(v)]
    grip = v.insert_routing_node(link._link_num, 5.0, 50.0)
    status = []
    v.status_message_sink = status.append
    texts = [text for text, _ in _right_click_menu(
        v, grip.scenePos(), chosen_prefix="Delete routing point")]
    assert "Delete routing point" in texts
    assert link.grip_items() == []
    assert link in v._links
    assert _link_rows(v) == links_before
    assert status == ["Routing point removed"]


def test_delete_selected_removes_a_grip_and_can_be_undone(qapp):
    v = _view()
    link = v._links[0]
    grip = v.insert_routing_node(link._link_num, 5.0, 50.0)
    grip.setSelected(True)
    v.delete_selected()
    assert link.grip_items() == []
    assert not v._rbd_ref.link_grips
    v._stack.undo()
    assert len(link.grip_items()) == 1


def test_many_grips_route_in_order(qapp):
    v = _view()
    link = v._links[0]
    first = v.insert_routing_node(link._link_num, 3.0, 40.0)
    second = v.insert_routing_node(link._link_num, 6.0, 70.0)
    first.setPos(first.pos() + QPointF(1.0, 1.0))
    second.setPos(second.pos() + QPointF(1.0, 1.0))
    assert link.grip_items() == [first, second]
    elements = [(link.path().elementAt(i).x, link.path().elementAt(i).y)
                for i in range(link.path().elementCount())]
    assert (first.pos().x(), first.pos().y()) in elements
    assert (second.pos().x(), second.pos().y()) in elements


def test_grip_does_not_change_simulation_metrics(qapp):
    def metrics(rbd):
        sink = []
        run_simulation(rbd, num_runs=3, _report_sink=sink,
                       collect_logs=False, seed=12345, num_workers=1)
        return [(round(r.availability, 12), round(r.reliability, 12),
                 r.num_system_failures, r.num_component_failures)
                for r in sink]

    base = metrics(_view()._rbd_ref)
    v = _view()
    assert v.insert_routing_node(v._links[0]._link_num, 5.0, 50.0)
    assert metrics(v._rbd_ref) == base


def test_capacity_model_allows_a_grip_and_results_are_identical(qapp):
    def metrics(rbd):
        sink = []
        run_simulation(rbd, num_runs=3, _report_sink=sink,
                       collect_logs=False, seed=12345, num_workers=1)
        return [(round(r.availability, 12), round(r.reliability, 12),
                 r.num_system_failures, r.num_component_failures)
                for r in sink]

    base = metrics(_view(CAPACITY_FIXTURE)._rbd_ref)
    v = _view(CAPACITY_FIXTURE)
    assert v.insert_routing_node(v._links[0]._link_num, 5.0, 50.0)
    assert metrics(v._rbd_ref) == base
    assert not [r for r in _node_rows(v) if str(r.get("Name", "")).startswith("route_")]


def test_grip_round_trips_through_rbds(qapp, tmp_path):
    v = _view()
    link = v._links[0]
    grip = v.insert_routing_node(link._link_num, 5.0, 50.0)
    point = (round(grip.pos().x(), 2), round(grip.pos().y(), 2))
    path = tmp_path / "grip.rbds"
    save_rbds(v._rbd_ref, str(path))
    again = _view()
    again.load_rbd(load_rbds(str(path)))
    assert link._link_num in again._route_grips
    reloaded = again._route_grips[link._link_num][0]
    assert (round(reloaded.pos().x(), 2), round(reloaded.pos().y(), 2)) == point


def test_legacy_rbd_drops_grips_but_keeps_the_link(qapp, tmp_path):
    v = _view()
    link = v._links[0]
    v.insert_routing_node(link._link_num, 5.0, 50.0)
    path = tmp_path / "grip.rbd"
    save_rbd(v._rbd_ref, str(path))
    again = load_rbd(str(path))
    assert _link_rows(v) == again.sections["LINK_DATA"].rows
    assert _node_rows(v) == again.sections["NODE_DATA"].rows
    assert not getattr(again, "link_grips", [])


def test_grip_keeps_its_on_screen_size_when_zoomed_out(qapp):
    """A fixed scene-unit radius made the handle 2 px across at 25% zoom on
    a large model, too small to grab. It now ignores the view zoom."""
    v = _open_shown_view(qapp)
    link = v._links[0]
    c = link.line().center()
    grip = v.insert_routing_node(link._link_num, *v._vt.to_model(c.x(), c.y()))
    sizes = []
    for zoom in (1.0, 0.5, 0.25, 3.0):
        v.resetTransform()
        v.scale(zoom, zoom)
        qapp.processEvents()
        device = grip.deviceTransform(v.viewportTransform())
        sizes.append(device.mapRect(grip.rect()).width())
    assert all(s == pytest.approx(sizes[0]) for s in sizes), sizes
    assert sizes[0] >= 9.0
    v.resetTransform()
    v.scale(0.25, 0.25)
    qapp.processEvents()
    centre = v.mapFromScene(grip.scenePos())
    near = centre + QPointF(5.0, 0.0).toPoint()
    assert v.itemAt(near) is grip, "a click near the dot still hits the handle"
