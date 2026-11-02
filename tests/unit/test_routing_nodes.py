"""Dog-leg routing nodes: split a link, undo/redo, save and reload.

A routing node is a plain type-2 pass-through (`k=1`, full flow) inserted
into an existing link; it must be invisible to the reliability model and
robust against malformed/hostile rows and names.
"""
from __future__ import annotations

import pathlib

import pytest
from PySide6.QtCore import QPointF
from PySide6.QtWidgets import QApplication, QGraphicsItem

from raptor.api.analytical import analyse_rbd
from raptor.core.engine.runner import run_simulation
from raptor.core.fileio.builder import validate
from raptor.core.fileio.model_file import load_model_file
from raptor.core.fileio.rbd import load_rbd, save_rbd
from raptor.core.fileio.rbds import load_rbds, save_rbds
from raptor.gui.ui.diagram import DiagramView

REPO = pathlib.Path(__file__).resolve().parents[2]
FIXTURE = REPO / "tests/golden/TwoBlockRedundancy/input.rbd"
HIER_FIXTURE = REPO / "tests/golden/CSColdPmProbe/input.rbd"
OUT_OF_HIER_FIXTURE = REPO / "tests/golden/StartupFailuresProbe/input.rbd"
CAPACITY_FIXTURE = REPO / "tests/golden/Capacity1/input.rbd"


@pytest.fixture(scope="module")
def qapp():
    return QApplication.instance() or QApplication([])


def _view(path=FIXTURE):
    v = DiagramView()
    v.load_rbd(load_model_file(str(path)))
    return v


def _link_rows(view):
    return view._rbd_ref.sections["LINK_DATA"].rows


def _node_row(view, nid):
    return next(r for r in view._rbd_ref.sections["NODE_DATA"].rows
                if str(r.get("ID")) == str(nid))


def _non_header_issues(rbd):
    return [issue for issue in validate(rbd)
            if not issue.startswith("WARN: header ")]


def _save_and_reload(view, path, *, legacy: bool):
    if legacy:
        save_rbd(view._rbd_ref, str(path))
        return load_rbd(str(path))
    save_rbds(view._rbd_ref, str(path))
    return load_rbds(str(path))


def test_insert_splits_one_link_into_two(qapp):
    v = _view()
    link = next(iter(v._links))
    old_id = link._link_num
    row = next(r for r in _link_rows(v) if str(r.get("ID")) == str(old_id))
    old_target = (row["ToType"], row["ToID"])
    nid = v.insert_routing_node(old_id, 3.75, 92.5)
    assert nid in v.nodes
    node = v.nodes[nid]
    assert node.type_node == 2
    assert node.node_name.startswith("route_")
    # The original link now enters the node; the new link leaves it.
    row = next(r for r in _link_rows(v) if str(r.get("ID")) == str(old_id))
    assert (row["ToType"], row["ToID"]) == ("Node", str(nid))
    new_row = next(r for r in _link_rows(v)
                   if r.get("FromType") == "Node"
                   and str(r.get("FromID")) == str(nid))
    assert (new_row["ToType"], new_row["ToID"]) == old_target


def test_split_preserves_link_counts_and_validation(qapp):
    v = _view()
    before = {(r["ID"], r.get("FromType"), r.get("FromID"),
               r.get("ToType"), r.get("ToID"))
              for r in _link_rows(v)}
    nid = v.insert_routing_node(next(iter(v._links))._link_num, 3.75, 92.5)
    node = _node_row(v, nid)
    assert (node["Type"], node["k"], node["n"], node["Out"], node["parent"]) \
        == ("2", "1", "1", "1", "0")
    # One row in, one row out, and every other element's counts unchanged.
    after = {(r["ID"], r.get("FromType"), r.get("FromID"),
              r.get("ToType"), r.get("ToID"))
             for r in _link_rows(v)}
    assert len(after) == len(before) + 1
    assert _non_header_issues(v._rbd_ref) == []


@pytest.mark.parametrize("legacy", [False, True], ids=["rbds", "rbd"])
def test_routing_node_round_trips_through_both_formats(qapp, tmp_path, legacy):
    v = _view()
    nid = v.insert_routing_node(next(iter(v._links))._link_num, 3.75, 92.5)
    path = tmp_path / ("routing.rbd" if legacy else "routing.rbds")
    loaded = _save_and_reload(v, path, legacy=legacy)
    nodes = [r for r in loaded.sections["NODE_DATA"].rows
             if str(r.get("ID")) == str(nid)]
    assert nodes
    assert nodes[0]["Type"] == "2"
    assert nodes[0]["Name"].startswith("route_")
    assert len(loaded.sections["LINK_DATA"].rows) == len(_link_rows(v))
    assert _non_header_issues(loaded) == []


def test_undo_and_redo_restore_the_original_link(qapp):
    v = _view()
    before_nodes, before_links = len(v.nodes), len(v.links)
    link = next(iter(v._links))
    v.insert_routing_node(link._link_num, 3.75, 92.5)
    assert (len(v.nodes), len(v.links)) == (before_nodes + 1,
                                            before_links + 1)
    v._stack.undo()
    assert (len(v.nodes), len(v.links)) == (before_nodes, before_links)
    v._stack.redo()
    assert (len(v.nodes), len(v.links)) == (before_nodes + 1,
                                            before_links + 1)


def test_undo_redo_repeatedly_keeps_rows_identical(qapp):
    v = _view()
    original = [dict(r) for r in _link_rows(v)]
    nid = v.insert_routing_node(next(iter(v._links))._link_num, 3.75, 92.5)
    inserted = [dict(r) for r in _link_rows(v)]
    for _ in range(8):
        v._stack.undo()
        assert _link_rows(v) == original
        assert all(str(r.get("ID")) != str(nid)
                   for r in v._rbd_ref.sections["NODE_DATA"].rows)
        v._stack.redo()
        assert _link_rows(v) == inserted
        assert _node_row(v, nid)
    assert _non_header_issues(v._rbd_ref) == []


def test_insert_marks_the_model_dirty_and_failure_does_not(qapp):
    v = _view()
    emitted = []
    v.model_changed.connect(lambda: emitted.append(True))
    assert v.insert_routing_node(next(iter(v._links))._link_num, 3.75, 92.5)
    assert emitted, "a successful split must mark the document dirty"
    before = len(emitted)
    assert v.insert_routing_node(9999, 3.75, 92.5) is None
    assert len(emitted) == before, "a refused split must not dirty the model"


def test_selection_is_not_left_on_the_removed_link(qapp):
    v = _view()
    v.resize(700, 400)
    v.show()
    qapp.processEvents()
    link = v._links[0]
    v._scene.clearSelection()
    link.setSelected(True)
    assert v._scene.selectedItems()
    v.insert_routing_node(link._link_num, 3.75, 92.5)
    # The split recreates the link items; the old, removed item must not
    # linger in the scene's selection. The new node is deliberately not
    # auto-selected, matching add_block/add_node.
    assert v._scene.selectedItems() == []


# ---------------------------------------------------------------------
# Reliability semantics
# ---------------------------------------------------------------------

def test_pass_through_node_does_not_change_analytical_results(qapp):
    base = analyse_rbd(_view()._rbd_ref, mode="availability")
    for link_id in (1, 2, 3, 4, 5):
        v = _view()
        assert v.insert_routing_node(link_id, 5.0, 50.0)
        after = analyse_rbd(v._rbd_ref, mode="availability")
        assert after["availability"] == pytest.approx(
            base["availability"], abs=1e-12)
        assert after["unavailability"] == pytest.approx(
            base["unavailability"], abs=1e-12)


def test_cold_standby_links_are_refused_without_mutation(qapp):
    """Cold-standby switchover keys off the link's source block; a link
    from a routing node is not equivalent (HierDegradeWeakProbe's cold
    link changes the simulated availability)."""
    v = _view(HIER_FIXTURE)
    cold_ids = {str(r.get("ID")) for r in v._rbd_ref.sections["NODE_DATA"].rows
                if str(r.get("Cold", "0")).strip() not in ("", "0")}
    cold = next(r for r in _link_rows(v)
                if r.get("ToType") == "Node"
                and str(r.get("ToID")) in cold_ids)
    assert v._link_targets_cold_node(v._rbd_ref, cold)
    before_links = [dict(r) for r in _link_rows(v)]
    before_nodes = [dict(r) for r in v._rbd_ref.sections["NODE_DATA"].rows]
    assert v.insert_routing_node(int(cold["ID"]), 5, 50) is None
    assert _link_rows(v) == before_links
    assert v._rbd_ref.sections["NODE_DATA"].rows == before_nodes
    # An ordinary link in the same model still splits.
    assert v.insert_routing_node(1, 5, 50)


def test_capacity_model_routing_node_is_selectable_and_movable(qapp):
    v = _view(CAPACITY_FIXTURE)
    nid = v.insert_routing_node(4, 5, 50)
    item = v._node_items[nid]
    assert item.flags() & QGraphicsItem.GraphicsItemFlag.ItemIsMovable
    assert item.flags() & QGraphicsItem.GraphicsItemFlag.ItemIsSelectable
    item.setSelected(True)
    assert item in v._scene.selectedItems()
    before = (item.pos().x(), item.pos().y())
    item.setPos(before[0] + 25.0, before[1] + 15.0)
    v.write_back_to_rbd(v._rbd_ref)
    row = _node_row(v, nid)
    assert (float(row["xPos"]), float(row["yPos"])) != before


def test_capacity_analysis_models_allow_a_transparent_routing_node(qapp):
    """A routing junction is GUI-only: the run model contracts it back to
    the original link, so capacity results must be bit-identical."""
    def metrics(rbd):
        sink = []
        run_simulation(rbd, num_runs=3, _report_sink=sink,
                       collect_logs=False, seed=12345, num_workers=1)
        return [(round(r.availability, 12), round(r.reliability, 12),
                 r.num_system_failures, r.num_component_failures)
                for r in sink]

    base = metrics(_view(CAPACITY_FIXTURE)._rbd_ref)
    v = _view(CAPACITY_FIXTURE)
    assert v._capacity_analysis_enabled(v._rbd_ref)
    nid = v.insert_routing_node(4, 5, 50)
    assert nid is not None
    assert nid in v.nodes
    # The row is saved, but the computation model has neither the junction
    # nor its second link: EFPA sees the original graph.
    from raptor.core.engine.load import load_model
    run_model = load_model(v._rbd_ref)
    assert nid not in run_model.nodes_by_id
    assert metrics(v._rbd_ref) == base


def test_pass_through_node_does_not_change_simulation_metrics(qapp):
    def metrics(view):
        sink = []
        run_simulation(view._rbd_ref, num_runs=3, _report_sink=sink,
                       collect_logs=False, seed=12345, num_workers=1)
        return [(round(r.availability, 12), round(r.reliability, 12),
                 r.num_system_failures, r.num_component_failures)
                for r in sink]

    base = metrics(_view())
    for link_id in (1, 5):
        v = _view()
        assert v.insert_routing_node(link_id, 5.0, 50.0)
        assert metrics(v) == base


# ---------------------------------------------------------------------
# Names, ids and malformed rows
# ---------------------------------------------------------------------

def test_names_are_unique_bounded_and_not_injectable(qapp):
    v = _view()
    first = v.insert_routing_node(1, 5, 50, "route_3")
    second = v.insert_routing_node(2, 8, 50, "route_3")
    hostile = v.insert_routing_node(
        3, 11, 50, "evil\n/___SYSTEM_DATA___\nID 1")
    reserved = v.insert_routing_node(4, 14, 50, "start")
    numeric = v.insert_routing_node(5, 17, 50, 123)
    rows = v._rbd_ref.sections["NODE_DATA"].rows
    names = [r["Name"] for r in rows]
    assert names.count("route_3") == 1
    assert _node_row(v, first)["Name"] == "route_3"
    assert _node_row(v, second)["Name"] == "route_3_1"
    for nid in (first, second, hostile, reserved, numeric):
        name = _node_row(v, nid)["Name"]
        assert name and not any(ch.isspace() for ch in name)
        assert not name.startswith("/")
        assert len(name) <= 20
        assert name.lower() not in ("start", "end")
    assert len({n.lower() for n in names}) == len(names)


def test_links_are_read_from_the_model_not_the_name(qapp, tmp_path):
    v = _view()
    v.insert_routing_node(1, 5, 50, "evil\n/___SYSTEM_DATA___\nID 1")
    path = tmp_path / "inject.rbds"
    save_rbds(v._rbd_ref, str(path))
    reloaded = load_rbds(str(path))
    # The hostile name did not add a section or a row to the saved model.
    assert "SYSTEM_DATA" in reloaded.sections
    assert len(reloaded.sections["NODE_DATA"].rows) == 4
    assert _non_header_issues(reloaded) == []


@pytest.mark.parametrize("hostile", [
    "/___LINK_DATA___",
    "EndOfNodeData",
    "evil\n/___SYSTEM_DATA___\nID 1",
])
def test_hostile_names_do_not_truncate_the_legacy_file(qapp, tmp_path,
                                                        hostile):
    v = _view()
    nid = v.insert_routing_node(1, 5, 50, hostile)
    path = tmp_path / "hostile.rbd"
    save_rbd(v._rbd_ref, str(path))
    reloaded = load_rbd(str(path))
    assert _node_row(v, nid)
    assert {str(r.get("ID")) for r in reloaded.sections["NODE_DATA"].rows} \
        >= {str(r.get("ID")) for r in v._rbd_ref.sections["NODE_DATA"].rows}
    assert len(reloaded.sections["LINK_DATA"].rows) == \
        len(_link_rows(v))
    assert _non_header_issues(reloaded) == []


def test_malformed_link_rows_are_refused_without_mutation(qapp):
    v = _view()
    section = v._rbd_ref.sections["LINK_DATA"]
    section.rows.extend([
        {"ID": "bogus", "FromType": "Node", "FromID": "1",
         "ToType": "Block", "ToID": "3"},
        {"ID": "99", "FromType": "", "FromID": "",
         "ToType": "", "ToID": ""},
    ])
    before = [dict(r) for r in section.rows]
    for bad in ("bogus", 99, -1, 0, 1.5, None, True, 10 ** 9):
        assert v.insert_routing_node(bad, 5, 50) is None, bad
    assert section.rows == before
    assert len(v.nodes) == 3


def test_unrelated_malformed_link_row_does_not_break_the_edit(qapp):
    v = _view()
    v._rbd_ref.sections["LINK_DATA"].rows.append({"ID": "99"})
    nid = v.insert_routing_node(next(iter(v._links))._link_num, 5, 50)
    assert nid in v.nodes
    v._stack.undo()
    v._stack.redo()
    assert nid in v.nodes


def test_non_finite_positions_are_refused(qapp):
    v = _view()
    before = [dict(r) for r in v._rbd_ref.sections["NODE_DATA"].rows]
    for bad in (float("nan"), float("inf"), -float("inf"), None, "x"):
        assert v.insert_routing_node(1, bad, 50) is None, bad
        assert v.insert_routing_node(1, 50, bad) is None, bad
    assert v._rbd_ref.sections["NODE_DATA"].rows == before


def test_missing_sections_and_linkless_models_are_refused(qapp):
    v = _view()
    v._rbd_ref.sections["LINK_DATA"].rows.clear()
    assert v.insert_routing_node(1, 5, 50) is None

    v = _view()
    del v._rbd_ref.sections["NODE_DATA"]
    assert v.insert_routing_node(1, 5, 50) is None

    v = _view()
    v._rbd_ref = None
    assert v.insert_routing_node(1, 5, 50) is None


def test_split_link_cmd_rejects_invalid_ids(qapp):
    from raptor.gui.ui.diagram_commands import SplitLinkCmd

    v = _view()
    for bad in (None, "x", 0, -1, 1.5, True):
        with pytest.raises(ValueError):
            SplitLinkCmd(v, bad, 1, {"ID": "2"})


# ---------------------------------------------------------------------
# Hierarchy levels
# ---------------------------------------------------------------------

@pytest.mark.parametrize(
    "fixture, side",
    [(HIER_FIXTURE, "To"), (OUT_OF_HIER_FIXTURE, "From")],
    ids=["into-hierarchy", "out-of-hierarchy"])
def test_insert_on_a_hierarchy_boundary_link(qapp, tmp_path, fixture, side):
    v = _view(fixture)
    row = next(r for r in _link_rows(v) if r.get(f"{side}Type") == "Hier")
    nid = v.insert_routing_node(int(row["ID"]), 8, 92)
    node = _node_row(v, nid)
    assert node["parent"] == "0"          # the oval's own level
    assert _non_header_issues(v._rbd_ref) == []
    loaded = _save_and_reload(v, tmp_path / "boundary.rbds", legacy=False)
    assert _non_header_issues(loaded) == []


def test_insert_on_a_link_from_another_level_uses_that_level(qapp):
    """The link's own endpoints decide the node's level, not the level
    currently displayed (the API can split a link it found by id)."""
    v = _view(HIER_FIXTURE)
    before_items = len(v._links)
    row = next(r for r in _link_rows(v) if str(r.get("pID")) == "23")
    nid = v.insert_routing_node(int(row["ID"]), 10, 90)
    node = _node_row(v, nid)
    assert node["parent"] == "23"
    # No phantom link appears on the home level for a foreign row, and no
    # foreign node is registered in the level-scoped view model.
    assert nid not in v.nodes
    assert len(v._links) == before_items
    assert _non_header_issues(v._rbd_ref) == []
    v._stack.undo()
    v._stack.redo()
    assert _node_row(v, nid)["parent"] == "23"
    assert _non_header_issues(v._rbd_ref) == []
    # Entering that level shows the node and its outgoing link.
    v.enter_level(23)
    assert nid in v.nodes
    assert any(item._from is v._node_items[nid] for item in v._links)


def test_node_on_an_unknown_level_is_refused(qapp):
    v = _view()
    row = next(r for r in _link_rows(v) if r["ID"] == "1")
    row["FromType"] = "Mystery"
    assert v.insert_routing_node(1, 5, 50) is None


# ---------------------------------------------------------------------
# Deleting afterwards
# ---------------------------------------------------------------------

def test_deleting_the_node_or_new_link_undoes_cleanly(qapp):
    v = _view()
    original = {(r["ID"], r["FromType"], r["FromID"],
                 r["ToType"], r["ToID"]) for r in _link_rows(v)}
    nid = v.insert_routing_node(next(iter(v._links))._link_num, 3.75, 92.5)
    inserted = {(r["ID"], r["FromType"], r["FromID"],
                 r["ToType"], r["ToID"]) for r in _link_rows(v)}

    # Delete the routing node: its two incident links go with it, so the
    # original A->B row is not reconnected automatically (Raptor 7 leaves
    # the gap for the user to rewire).
    v._scene.clearSelection()
    v._node_items[nid].setSelected(True)
    v.delete_selected()
    assert len(_link_rows(v)) == len(original) - 1

    # Undo deletion restores both links + node; undoing the split again
    # restores the exact original topology.
    v._stack.undo()
    assert {(r["ID"], r["FromType"], r["FromID"],
             r["ToType"], r["ToID"]) for r in _link_rows(v)} == inserted
    v._stack.undo()
    assert {(r["ID"], r["FromType"], r["FromID"],
             r["ToType"], r["ToID"]) for r in _link_rows(v)} == original
    v._stack.redo()
    v._stack.redo()
    assert len(_link_rows(v)) == len(original) - 1
    assert _non_header_issues(v._rbd_ref) == []


def test_deleted_link_is_not_resurrected_or_removed_twice(qapp):
    v = _view()
    nid = v.insert_routing_node(next(iter(v._links))._link_num, 3.75, 92.5)
    new_link = next(item for item in v._links
                    if item._from is v._node_items[nid])
    new_id = new_link._link_num
    v._scene.clearSelection()
    new_link.setSelected(True)
    v.delete_selected()
    assert not [r for r in _link_rows(v) if str(r.get("ID")) == str(new_id)]

    # Delete (undoable) -> undo it -> undo the split: removing the split's
    # own row must not touch any other row, and redoing must add it once.
    v._stack.undo()
    assert len([r for r in _link_rows(v)
                if str(r.get("ID")) == str(new_id)]) == 1
    v._stack.undo()
    assert not [r for r in _link_rows(v) if str(r.get("ID")) == str(new_id)]
    v._stack.redo()
    assert len([r for r in _link_rows(v)
                if str(r.get("ID")) == str(new_id)]) == 1
    assert _non_header_issues(v._rbd_ref) == []


# ---------------------------------------------------------------------
# Context menu wiring
# ---------------------------------------------------------------------

def _open_shown_view(qapp, path=FIXTURE):
    v = _view(path)
    v.resize(700, 400)
    v.show()
    qapp.processEvents()
    return v


def _right_click_menu(v, scene_pos, chosen_prefix=None):
    from PySide6.QtCore import QPointF, Qt
    from PySide6.QtGui import QMouseEvent

    vp = v.mapFromScene(scene_pos)
    event = QMouseEvent(
        QMouseEvent.MouseButtonPress, QPointF(vp), QPointF(vp),
        Qt.MouseButton.RightButton, Qt.MouseButton.RightButton,
        Qt.KeyboardModifier.NoModifier)
    captured = []

    def capture(menu, _event):
        actions = [(a.text(), a) for a in menu.actions()
                   if not a.isSeparator()]
        captured.append(actions)
        if chosen_prefix is None:
            return None
        return next((a for text, a in actions
                     if text.startswith(chosen_prefix)), None)

    v._exec_menu = capture
    v._show_context_menu(event)
    return captured[-1]


def test_context_menu_offers_routing_only_on_a_link(qapp):
    v = _open_shown_view(qapp)
    link = v._links[0]
    texts = [text for text, _ in _right_click_menu(v, link.line().center())]
    assert any(text.startswith("Insert routing node") for text in texts)

    block = next(iter(v._block_items.values()))
    block_pos = block.mapToScene(block.boundingRect().center())
    texts = [text for text, _ in _right_click_menu(v, block_pos)]
    assert not any(text.startswith("Insert routing node") for text in texts)


def test_dismissing_the_menu_changes_nothing(qapp):
    v = _open_shown_view(qapp)
    status = []
    v.status_message_sink = status.append
    nodes = len(v.nodes)
    block = next(iter(v._block_items.values()))
    block_pos = block.mapToScene(block.boundingRect().center())
    _right_click_menu(v, block_pos)          # returns None: dismissed
    assert len(v.nodes) == nodes
    assert status == []
    assert _non_header_issues(v._rbd_ref) == []


def test_context_menu_action_inserts_and_reports_status(qapp):
    v = _open_shown_view(qapp)
    status = []
    v.status_message_sink = status.append
    before = len(v.nodes)
    _right_click_menu(v, v._links[0].line().center(),
                      chosen_prefix="Insert routing node")
    assert len(v.nodes) == before + 1
    assert status == ["Routing node inserted"]


def test_capacity_model_allows_a_routing_node(qapp):
    v = _open_shown_view(qapp, CAPACITY_FIXTURE)
    status = []
    v.status_message_sink = status.append
    nodes = len(v.nodes)
    _right_click_menu(v, v._links[0].line().center(),
                      chosen_prefix="Insert routing node")
    assert len(v.nodes) == nodes + 1
    assert status == ["Routing node inserted"]
    new = next(n for n in v.nodes.values() if n.node_name.startswith("route_"))
    assert new.full_flow == 1 and new.any_path == 1
    assert new.route_only


def test_context_menu_explains_a_cold_standby_refusal(qapp):
    v = _open_shown_view(qapp, HIER_FIXTURE)
    status = []
    v.status_message_sink = status.append
    cold_item = next(item for item in v._links
                     if v._link_targets_cold_node(
                         v._rbd_ref,
                         next(r for r in _link_rows(v)
                              if str(r.get("ID")) == str(item._link_num))))
    nodes = len(v.nodes)
    _right_click_menu(v, cold_item.line().center(),
                      chosen_prefix="Insert routing node")
    assert len(v.nodes) == nodes
    assert status and "cold-standby" in status[0]


def test_context_menu_without_a_link_id_does_not_crash(qapp):
    v = _open_shown_view(qapp)
    link = v._links[0]
    link._link_num = None
    texts = [text for text, _
             in _right_click_menu(v, link.line().center())]
    assert not any(text.startswith("Insert routing node") for text in texts)
