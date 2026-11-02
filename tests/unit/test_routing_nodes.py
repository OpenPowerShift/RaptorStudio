"""Dog-leg routing nodes: split a link, undo/redo, save and reload."""
from __future__ import annotations

import pytest
from PySide6.QtWidgets import QApplication

from raptor.core.fileio.rbds import load_rbds, save_rbds
from raptor.core.fileio.model_file import load_model_file
from raptor.gui.ui.diagram import DiagramView

FIXTURE = "tests/golden/TwoBlockRedundancy/input.rbd"


@pytest.fixture(scope="module")
def qapp():
    return QApplication.instance() or QApplication([])


def _view():
    v = DiagramView()
    v.load_rbd(load_model_file(FIXTURE))
    return v


def test_insert_splits_one_link_into_two(qapp):
    v = _view()
    link = next(iter(v._links))
    old_id = link._link_num
    row = next(r for r in v._rbd_ref.sections["LINK_DATA"].rows
               if str(r.get("ID")) == str(old_id))
    old_target = (row["ToType"], row["ToID"])
    nid = v.insert_routing_node(old_id, 3.75, 92.5)
    assert nid in v.nodes
    node = v.nodes[nid]
    assert node.type_node == 2
    assert node.node_name.startswith("route_")
    # The original link now enters the node; the new link leaves it.
    row = next(r for r in v._rbd_ref.sections["LINK_DATA"].rows
               if str(r.get("ID")) == str(old_id))
    assert (row["ToType"], row["ToID"]) == ("Node", str(nid))
    new_row = next(r for r in v._rbd_ref.sections["LINK_DATA"].rows
                   if r.get("FromType") == "Node"
                   and str(r.get("FromID")) == str(nid))
    assert (new_row["ToType"], new_row["ToID"]) == old_target


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


def test_routing_node_round_trips_through_rbds(qapp, tmp_path):
    v = _view()
    link = next(iter(v._links))
    nid = v.insert_routing_node(link._link_num, 3.75, 92.5)
    path = str(tmp_path / "routing.rbds")
    save_rbds(v._rbd_ref, path)
    loaded = load_rbds(path)
    node_rows = [r for r in loaded.sections["NODE_DATA"].rows
                 if str(r.get("ID")) == str(nid)]
    assert node_rows
    assert node_rows[0]["Type"] == "2"
    assert node_rows[0]["Name"].startswith("route_")
    assert len(loaded.sections["LINK_DATA"].rows) == len(
        v._rbd_ref.sections["LINK_DATA"].rows)
