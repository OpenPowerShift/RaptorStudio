"""Three defects the clipboard characterisation exposed, fixed together.

1. Paste remapped a copied cluster's internal links and then never added
   them, so a pasted group of connected blocks arrived unconnected.
2. Copying a hierarchy left out the hierarchies nested inside it, but still
   copied their contents: the pasted blocks kept `ParentId` = the ORIGINAL
   inner hierarchy, so the copy landed inside the original and the copy's
   own outer hierarchy was empty of them.
3. Loose elements copied at one level kept that level's `ParentId` when
   pasted at another, so "Pasted 1" showed nothing at the level being
   viewed and the element appeared at the source level instead.
"""
from __future__ import annotations

from pathlib import Path

import pytest
from PySide6.QtWidgets import QApplication

from raptor.core.fileio.rbd import load_rbd
from raptor.gui.ui import diagram

GOLD = Path(__file__).resolve().parents[1] / "golden"


@pytest.fixture
def qapp():
    return QApplication.instance() or QApplication([])


def _view(name):
    v = diagram.DiagramView()
    v.load_rbd(load_rbd(str(GOLD / name / "input.rbd")))
    return v


def _select(v, *, blocks=(), nodes=(), hiers=()):
    v._scene.clearSelection()
    for b in blocks:
        v._block_items[b].setSelected(True)
    for n in nodes:
        v._node_items[n].setSelected(True)
    for h in hiers:
        v._hier_items[h].setSelected(True)


def _rows(v, section):
    return v._rbd_ref.sections[section].rows


def _n_links(v):
    return len(_rows(v, "LINK_DATA"))


@pytest.mark.gui
def test_pasted_internal_links_are_added_and_undo_together(qapp):
    v = _view("TwoBlockRedundancy")
    blocks = sorted(b for b in v.blocks if b)
    nodes = [n for n, o in v.nodes.items() if o.type_node == 2]
    _select(v, blocks=blocks, nodes=nodes)
    v.copy_selection()
    internal = [r for k, r in v._paste_buffer if k == "link"]
    assert internal, "fixture has no internal links to paste"
    before, n_blocks = _n_links(v), len(_rows(v, "BLOCK_DATA"))
    v.paste(at_model=(200.0, 50.0))
    assert _n_links(v) == before + len(internal)
    new_ids = {int(r["ID"]) for r in _rows(v, "BLOCK_DATA")[n_blocks:]}
    fresh = _rows(v, "LINK_DATA")[before:]
    assert all(r["FromType"] != "Block" or int(r["FromID"]) in new_ids
               for r in fresh)
    v._stack.undo()                       # one step removes the whole paste
    assert _n_links(v) == before
    assert len(_rows(v, "BLOCK_DATA")) == n_blocks


@pytest.mark.gui
def test_copying_a_hierarchy_copies_the_ones_nested_in_it(qapp):
    v = _view("NestedHierarchyCost")
    outer = next(i for i, h in v.hiers.items() if not h.parent_id)
    inner = next(i for i, h in v.hiers.items() if h.parent_id == outer)
    inner_children = {r["ID"] for r in _rows(v, "BLOCK_DATA")
                      if int(r["ParentId"]) == inner}
    n_inner_before = len([r for r in _rows(v, "BLOCK_DATA")
                          if int(r["ParentId"]) == inner])
    _select(v, hiers=[outer])
    v.copy_selection()
    assert {int(r["ID"]) for k, r in v._paste_buffer if k == "hier"} == \
        {outer, inner}
    v.paste(at_model=(100.0, 100.0))
    new = [i for i in v.hiers if i not in (outer, inner)
           and i > max(outer, inner) and v.hiers[i].id != 29]
    new_outer = next(i for i in new if not v.hiers[i].parent_id)
    new_inner = next(i for i in new if v.hiers[i].parent_id == new_outer)
    # the copy is self-contained: its blocks live in the copy's hierarchies
    pasted = [r for r in _rows(v, "BLOCK_DATA")
              if int(r["ID"]) not in {int(x) for x in
                                      [b for b in v.blocks]} - set()
              and int(r["ParentId"]) in (new_outer, new_inner)]
    assert {int(r["ParentId"]) for r in pasted} == {new_outer, new_inner}
    # ... and the ORIGINAL inner hierarchy gained nothing
    assert len([r for r in _rows(v, "BLOCK_DATA")
                if int(r["ParentId"]) == inner]) == n_inner_before
    assert inner_children
    # nesting and depth are preserved
    assert v.hiers[new_inner].level == v.hiers[new_outer].level + 1
    # the markers point at nodes that live in the copy
    node_parent = {int(r["ID"]): int(r["parent"])
                   for r in _rows(v, "NODE_DATA")}
    assert node_parent[v.hiers[new_inner].in_id] == new_inner
    assert node_parent[v.hiers[new_outer].in_id] == new_outer


@pytest.mark.gui
def test_a_nested_copy_undoes_in_one_step(qapp):
    v = _view("NestedHierarchyCost")
    outer = next(i for i, h in v.hiers.items() if not h.parent_id)
    before = {s: len(_rows(v, s)) for s in
              ("BLOCK_DATA", "NODE_DATA", "HIERARCHY_DATA", "LINK_DATA")}
    _select(v, hiers=[outer])
    v.copy_selection()
    v.paste(at_model=(100.0, 100.0))
    v._stack.undo()
    assert {s: len(_rows(v, s)) for s in before} == before


@pytest.mark.gui
def test_elements_pasted_at_another_level_land_at_that_level(qapp):
    v = _view("NestedHierarchyCost")
    b = sorted(v.blocks)[0]
    _select(v, blocks=[b])
    v.copy_selection()
    v.enter_level(26)
    shown = set(v.blocks)
    assert v.paste(at_model=(100.0, 100.0)) == 1
    assert len(v.blocks) == len(shown) + 1, "the paste is visible here"
    new = next(bid for bid in v.blocks if bid not in shown)
    row = next(r for r in _rows(v, "BLOCK_DATA") if int(r["ID"]) == new)
    assert int(row["ParentId"]) == 26


@pytest.mark.gui
def test_a_hierarchy_pasted_into_a_level_goes_under_it_one_deeper(qapp):
    v = _view("NestedHierarchyCost")
    top = next(i for i, h in v.hiers.items() if not h.parent_id)
    other = next(i for i, h in v.hiers.items()
                 if not h.parent_id and i != top)
    _select(v, hiers=[top])
    v.copy_selection()
    v.enter_level(other)
    v.paste(at_model=(100.0, 100.0))
    made = [h for i, h in v.hiers.items() if i not in (23, 26, 29)]
    new_top = next(h for h in made if h.parent_id == other)
    assert new_top.level == v.hiers[other].level + 1
    new_inner = next(h for h in made if h.parent_id == new_top.id)
    assert new_inner.level == new_top.level + 1
