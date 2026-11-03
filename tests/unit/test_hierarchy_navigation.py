"""Hierarchy traversal parses one level, not the whole model."""
from __future__ import annotations

import time

import pytest
from PySide6.QtCore import QPointF
from PySide6.QtWidgets import QApplication

from raptor.api.build import build_from_spec
from raptor.gui.ui import diagram as diagram_mod
from raptor.gui.ui.diagram import DiagramView
from raptor.gui.ui.diagram_items import BLOCK_W, _LinkItem


@pytest.fixture(scope="module")
def qapp():
    return QApplication.instance() or QApplication([])


N = 2_000
HID = 100


def _model():
    blocks = ([{"name": "HomeBlock", "id": 1}]
              + [{"name": f"B{i}", "id": 10 + i, "parent": HID}
                 for i in range(N)])
    nodes = [
        {"id": 2, "name": "Start", "type": "start"},
        {"id": 3, "name": "End", "type": "end"},
        {"id": 4, "name": "HIn", "type": "in", "parent": HID},
        {"id": 5, "name": "HOut", "type": "out", "parent": HID},
    ]
    links = [
        {"from": "Start", "to": "HomeBlock"},
        {"from": "HomeBlock", "to": "HIn"},
        {"from": "HOut", "to": "End"},
        {"from": "HIn", "to": "B0"},
    ]
    links += [{"from": f"B{i}", "to": f"B{i + 1}"}
              for i in range(N - 1)]
    links += [{"from": f"B{N - 1}", "to": "HOut"}]
    return build_from_spec({
        "name": "BigHierarchy",
        "blocks": blocks,
        "nodes": nodes,
        "links": links,
        "hierarchies": [{"id": HID, "name": "Big",
                         "in_id": 4, "out_id": 5}],
        "system": {"term_condition": 100, "num_runs": 1},
    }, auto_layout=False)


def test_returning_home_parses_only_home_rows(qapp, monkeypatch):
    view = DiagramView()
    view.load_rbd(_model())
    view.enter_level(HID)
    original = diagram_mod.block_from_row
    seen: list[int] = []

    def counting(row):
        seen.append(1)
        return original(row)

    monkeypatch.setattr(diagram_mod, "block_from_row", counting)
    view.go_home()
    # The home level has one real block plus the ID=0 template row.
    assert len(seen) <= 2, f"parsed {len(seen)} block rows for a one-block level"


def test_enter_and_exit_stay_within_a_time_budget(qapp):
    view = DiagramView()
    view.load_rbd(_model())
    started = time.perf_counter()
    view.enter_level(HID)
    enter = time.perf_counter() - started
    started = time.perf_counter()
    view.go_home()
    leave = time.perf_counter() - started
    # Generous bounds: this is a regression guard against O(whole model)
    # parsing creeping back, not a benchmark of the CI machine.
    assert enter < 12.0, f"entering {N} elements took {enter:.1f}s"
    assert leave < 3.0, f"leaving took {leave:.1f}s"


def test_links_on_a_level_have_both_ends_visible(qapp):
    view = DiagramView()
    view.load_rbd(_model())
    view.enter_level(HID)
    visible = {"Block": set(view.blocks), "Node": set(view.nodes),
               "Event": set(view.events), "Hier": set(view.hiers)}
    for link in view.links:
        for ref, ident in ((link.connect_f_ref, link.connect_from_id),
                           (link.connect_t_ref, link.connect_to_id)):
            kind = ref.replace("RBD", "", 1)
            assert ident in visible[kind], (
                f"link {link.object_num} has hidden endpoint {kind}:{ident}")
    assert len(view.links) == N + 1


# ---------------------------------------------------------------------------
# Link slicing: boundary links, id collisions and malformed rows
# ---------------------------------------------------------------------------

DUP_HID = 50


def _dup_model():
    """Two levels where Block and Node ids collide (both kind 1 and kind 2
    exist on each side), plus a hierarchy boundary link."""
    return build_from_spec({
        "name": "DupIds",
        "blocks": [{"name": "HomeB", "id": 1},
                   {"name": "ChildB", "id": 2, "parent": DUP_HID}],
        "nodes": [
            {"id": 1, "name": "Start", "type": "start"},
            {"id": 2, "name": "End", "type": "end"},
            {"id": 3, "name": "HIn", "type": "in", "parent": DUP_HID},
            {"id": 4, "name": "HOut", "type": "out", "parent": DUP_HID},
            {"id": 5, "name": "N5", "type": "regular"},
            {"id": 6, "name": "N6", "type": "regular"},
        ],
        # No HOut -> End link: the hidden-level links keep the HIGHER ids,
        # so allocating a new home link from `view.links` reused one.
        "links": [
            {"from": "Start", "to": "HomeB"},
            {"from": "HomeB", "to": ["Hier", DUP_HID]},
            {"from": "HIn", "to": "ChildB"},
            {"from": "ChildB", "to": "HOut"},
        ],
        "hierarchies": [{"id": DUP_HID, "name": "Child",
                         "in_id": 3, "out_id": 4}],
        "system": {"term_condition": 100, "num_runs": 1},
    }, auto_layout=False)


def _link_keys(view):
    return {(link.connect_f_ref.replace("RBD", "", 1),
             link.connect_from_id,
             link.connect_t_ref.replace("RBD", "", 1),
             link.connect_to_id) for link in view.links}


def test_home_level_links_include_the_hierarchy_boundary(qapp):
    view = DiagramView()
    view.load_rbd(_dup_model())
    assert _link_keys(view) == {
        ("Node", 1, "Block", 1),      # Start -> HomeB
        ("Block", 1, "Hier", DUP_HID),  # boundary link to the oval
    }
    boundary = next(l for l in view._links if l._link_num is not None
                    and l._to is view._hier_items[DUP_HID])
    assert boundary._from is view._block_items[1]


def test_hidden_level_links_are_not_drawn_or_listed(qapp):
    view = DiagramView()
    view.load_rbd(_dup_model())
    home_nums = {link.object_num for link in view.links}
    view.enter_level(DUP_HID)
    assert _link_keys(view) == {
        ("Node", 3, "Block", 2),     # HIn -> ChildB
        ("Block", 2, "Node", 4),     # ChildB -> HOut
    }
    assert not (home_nums & {link.object_num for link in view.links})
    # The same-numbered ids resolve to the right KIND on each level.
    inner = next(li for li in view._links
                 if li._link_num is not None
                 and li._from is view._block_items[2])
    assert inner._to is view._node_items[4]


def test_same_id_block_and_node_resolve_to_their_own_items(qapp):
    view = DiagramView()
    view.load_rbd(_dup_model())
    # Start (Node 1) -> HomeB (Block 1): both ids are 1, both are visible.
    li = next(li for li in view._links if li._link_num is not None
              and li._from is view._node_items[1])
    assert li._to is view._block_items[1]
    assert li._from is not li._to


def test_a_cross_level_link_is_drawn_on_neither_level(qapp):
    rbd = _dup_model()
    rows = rbd.sections["LINK_DATA"].rows
    cross = dict(rows[0])
    cross.update({"ID": "900", "FromType": "Node", "FromID": "4",
                  "ToType": "Node", "ToID": "2", "pID": "0"})
    rows.append(cross)
    view = DiagramView()
    view.load_rbd(rbd)
    assert 900 not in {link.object_num for link in view.links}
    view.enter_level(DUP_HID)
    # HOut (4) is visible here but End (2) is not.
    assert 900 not in {link.object_num for link in view.links}
    assert all(li._link_num != 900 for li in view._links)


def test_malformed_link_rows_are_skipped_without_a_crash(qapp):
    rbd = _dup_model()
    rows = rbd.sections["LINK_DATA"].rows
    for lid, from_type, from_id, to_type, to_id in (
            ("901", "Block", "inf", "Node", "1"),
            ("902", "", "1", "Block", "1"),
            ("903", "Block", "1", "Node", ""),
    ):
        row = dict(rows[0])
        row.update({"ID": lid, "FromType": from_type, "FromID": from_id,
                    "ToType": to_type, "ToID": to_id})
        rows.append(row)
    view = DiagramView()
    view.load_rbd(rbd)      # must not raise
    nums = {link.object_num for link in view.links}
    assert not ({"901", "902", "903"} & {str(n) for n in nums})
    view.enter_level(DUP_HID)
    assert not ({"901", "902", "903"} &
                {str(n) for n in {link.object_num for link in view.links}})


def test_malformed_parent_cells_do_not_hide_or_crash_rows(qapp):
    rbd = _dup_model()
    template = dict(rbd.sections["BLOCK_DATA"].rows[0])
    floating = dict(template, ID="997", blockName="FloatParent",
                    ParentId="50.0", xPos="0", yPos="0")
    junk = dict(template, ID="998", blockName="JunkParent",
                ParentId="inf", xPos="0", yPos="0")
    rbd.sections["BLOCK_DATA"].rows.extend([floating, junk])
    view = DiagramView()
    view.load_rbd(rbd)
    # "inf" is unreadable as an id, so the row is a home-level element --
    # the same place `block_from_row` (which also defaults to 0) puts it.
    assert 998 in view.blocks
    assert 997 not in view.blocks
    view.enter_level(DUP_HID)
    assert 997 in view.blocks and view.blocks[997].name == "FloatParent"


def test_home_parses_only_home_node_rows(qapp, monkeypatch):
    view = DiagramView()
    view.load_rbd(_model())
    view.enter_level(HID)
    original = diagram_mod.node_from_row
    seen: list[int] = []

    def counting(row):
        seen.append(1)
        return original(row)

    monkeypatch.setattr(diagram_mod, "node_from_row", counting)
    view.go_home()
    assert len(seen) <= 3, f"parsed {len(seen)} node rows at a 2-node level"


def test_new_link_id_does_not_reuse_a_hidden_links_id(qapp):
    view = DiagramView()
    view.load_rbd(_dup_model())
    rows = view._rbd_ref.sections["LINK_DATA"].rows
    hidden_ids = {int(float(r["ID"])) for r in rows
                  if r.get("FromID") in ("3", "4") or r.get("ToID") in ("3", "4")}
    visible_ids = {link.object_num for link in view.links}
    assert hidden_ids and not (hidden_ids & visible_ids)
    highest = max(int(float(r["ID"])) for r in rows)
    created = view.connect_elements("Node", 5, "Node", 6)
    assert created == highest + 1
    assert created not in hidden_ids
    all_ids = [int(float(r["ID"])) for r in rows]
    assert len(all_ids) == len(set(all_ids)), "duplicate LINK_DATA id"
    # The new link is visible, the hidden rows are untouched.
    assert created in {link.object_num for link in view.links}
    assert hidden_ids & {link.object_num for link in view.links} == set()
    # Undo/redo go through `_remove_ids` / `_insert_link`, which must keep
    # the same id and rebuild the canvas item and its route.
    view._stack.undo()
    assert created not in {int(float(r["ID"])) for r in rows}
    assert created not in {link.object_num for link in view.links}
    view._stack.redo()
    assert created in {int(float(r["ID"])) for r in rows}
    li = next((x for x in view._links if x._link_num == created), None)
    assert li is not None
    assert li._from is view._node_items[5]
    assert li._to is view._node_items[6]
    assert li.path().elementCount() == 2


# ---------------------------------------------------------------------------
# Lazily built whole-model name index for dependency badges
# ---------------------------------------------------------------------------

def test_dependency_added_live_resolves_its_cross_level_target(qapp):
    view = DiagramView()
    view.load_rbd(_dup_model())
    # Home has no dependencies, so the whole-model scan is skipped.
    assert view._global_name_index is None
    # Block 1 now depends on ChildB, which lives on the hidden level.
    view.set_block_dep_master(1, 2, "RBDBlock")
    badge = view._block_items[1]._dep_badge
    assert badge is not None
    assert badge["target_name"] == "ChildB", badge["tooltip"]
    assert badge["target_item"] is None
    assert "different hierarchy level" in badge["tooltip"]
    view._stack.undo()
    assert view._block_items[1]._dep_badge is None


def test_dependency_click_still_navigates_after_a_live_add(qapp):
    view = DiagramView()
    view.load_rbd(_dup_model())
    view.set_block_dep_master(1, 2, "RBDBlock")
    view._on_dep_click(view._block_items[1])
    QApplication.processEvents()
    assert view._current_parent == DUP_HID
    assert 2 in view._block_items


# ---------------------------------------------------------------------------
# Route caches: moves and label-driven resizes must not leave stale ports
# ---------------------------------------------------------------------------

def test_moving_a_block_reroutes_its_links(qapp):
    view = DiagramView()
    view.load_rbd(_dup_model())
    block_item = view._block_items[1]
    link = next(li for li in view._links
                if li._link_num is not None and li._from is block_item)
    before = link._p0
    block_item.setPos(block_item.pos() + QPointF(140, 0))
    centre = _LinkItem._true_centre(block_item)
    assert centre == block_item.mapToScene(block_item.rect().center())
    assert link._p0 != before, "a move left the route on the old port"
    # The cached centre is fresh, and the port sits on the box's CURRENT
    # outline -- not the pre-move one.
    local = block_item.mapFromScene(link._p0)
    assert not block_item.rect().adjusted(2, 2, -2, -2).contains(local)
    assert block_item.rect().adjusted(-1, -1, 1, 1).contains(local)


def test_a_wider_label_reroutes_the_links_off_the_old_edge(qapp, monkeypatch):
    view = DiagramView()
    view.load_rbd(_dup_model())
    original = diagram_mod.block_labels.describe

    def wide_label(block, **kwargs):
        _lines, tip = original(block, **kwargs)
        return ["MTTF " + "9" * 40], tip      # definitely wider than one block

    monkeypatch.setattr(diagram_mod.block_labels, "describe", wide_label)
    view.refresh_block_labels()
    item = view._block_items[1]
    assert item.rect().width() > BLOCK_W
    for link in view._links:
        ports = ((link._p0,) if link._from is item else ()) + \
                ((link._p3,) if link._to is item else ())
        for port in ports:
            local = item.mapFromScene(port)
            # On the outline or outside it; never buried inside the box.
            assert not item.rect().adjusted(2, 2, -2, -2).contains(local)


# ---------------------------------------------------------------------------
# The wait cursor is an exception-safe, balanced pair
# ---------------------------------------------------------------------------

def test_set_level_restores_the_cursor_when_a_rebuild_fails(qapp, monkeypatch):
    view = DiagramView()
    view.load_rbd(_dup_model())
    messages: list[str] = []
    view.status_message_sink = messages.append
    assert QApplication.overrideCursor() is None

    def boom():
        assert QApplication.overrideCursor() is not None, \
            "the wait cursor is not up during the rebuild"
        raise RuntimeError("synthetic rebuild failure")

    monkeypatch.setattr(view, "_rebuild_scene", boom)
    with pytest.raises(RuntimeError, match="synthetic"):
        view._set_level(DUP_HID)
    assert QApplication.overrideCursor() is None
    assert view._loading is False
    assert view._current_parent == 0, "a failed rebuild left the level moved"
    assert messages and "Opening Child" in messages[-1]


def test_missing_data_sections_still_navigate(qapp):
    rbd = build_from_spec({
        "name": "NoSections",
        "nodes": [{"id": 1, "name": "S", "type": "start"},
                  {"id": 2, "name": "E", "type": "end"}],
        "links": [{"from": "S", "to": "E"}],
        "system": {"term_condition": 100, "num_runs": 1},
    }, auto_layout=False)
    for section in ("BLOCK_DATA", "NODE_DATA", "EVENT_DATA",
                    "LINK_DATA", "HIERARCHY_DATA"):
        rbd.sections.pop(section, None)
    view = DiagramView()
    view.load_rbd(rbd)      # must not raise
    assert view.blocks == {} and view.links == []
    view.go_home()          # a rebuild over no rows is a no-op
    view.enter_level(DUP_HID)   # unknown hierarchy: refused, no crash
    assert view._current_parent == 0
