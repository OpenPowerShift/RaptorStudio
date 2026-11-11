"""Regression tests for issue #72: `Model > Collapse Selection` must rewire
BOTH boundary sides, leave a valid model, and be one undoable command.

The failure reported on `DataCentre_8Train.rbds` was a collapse of a
mid-chain block (`XferBus -> A-Util -> A-Xfmr`) that left `A-Xfmr` with no
input and could not be undone. These tests use a tiny series model with a
block on each side of the selection so the outgoing half of the split is
exercised on its own, plus the atomic-refusal and one-sided selection
cases the rewire has to keep.
"""
from __future__ import annotations

import os
from itertools import pairwise

import pytest

pytest.importorskip("PySide6")

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication

from raptor.api.build import build_from_spec
from raptor.core.edit.hierarchy_plan import plan_collapse
from raptor.core.input_rules import check
from tests.unit.diagram_patch import swap
from raptor.gui.ui.diagram import DiagramView


@pytest.fixture(scope="module")
def qapp():
    return QApplication.instance() or QApplication([])


def _chain(names: list[str], *, start: bool = True, end: bool = True):
    """Start -> names[0] -> ... -> names[-1] -> End with DIRECT block links
    (no intermediate nodes), the shape that exposed #72.

    `start=False` / `end=False` drop that terminator afterwards, giving the
    one-sided (already dangling) selection the collapse has to keep working
    on. `build_from_spec` itself insists on a Start and an End."""
    spec = {
        "blocks": [{"name": n} for n in names],
        "nodes": [{"name": "Start", "type": 1, "n": 0, "out": 1},
                  {"name": "End", "type": 3, "n": 1, "out": 0}],
        "links": ([{"from": "Start", "to": names[0]}]
                  + [{"from": a, "to": b} for a, b in pairwise(names)]
                  + [{"from": names[-1], "to": "End"}]),
    }
    rbd = build_from_spec(spec)
    for wanted, node_type in ((start, "1"), (end, "3")):
        if wanted:
            continue
        node = next(r for r in rbd.sections["NODE_DATA"].rows
                    if r["Type"] == node_type)
        nid = node["ID"]
        rbd.sections["NODE_DATA"].rows.remove(node)
        rbd.sections["LINK_DATA"].rows[:] = [
            r for r in rbd.sections["LINK_DATA"].rows
            if not ((r["FromType"] == "Node" and r["FromID"] == nid)
                    or (r["ToType"] == "Node" and r["ToID"] == nid))]
    return rbd


def _view(rbd) -> DiagramView:
    v = DiagramView()
    v.load_rbd(rbd)
    return v


def _select(v: DiagramView, *names: str) -> None:
    v._scene.clearSelection()
    by_name = {b.name: bid for bid, b in v.blocks.items()}
    for name in names:
        v._block_items[by_name[name]].setSelected(True)


def _id(v: DiagramView, name: str) -> int:
    return next(bid for bid, b in v.blocks.items() if b.name == name)


def _links(rbd) -> list[tuple[str, str, str, str, str, str]]:
    return [(r["ID"], r["FromType"], r["FromID"], r["ToType"], r["ToID"],
             r.get("pID", "0")) for r in rbd.sections["LINK_DATA"].rows]


def _violation_keys(rbd) -> set[tuple[str, str]]:
    return {v.key for v in check(rbd)}


def _hier_row(rbd, hid: int) -> dict:
    return next(r for r in rbd.sections["HIERARCHY_DATA"].rows
                if int(float(r["ID"])) == hid)


# ---------------------------------------------------------------------------
# The rewire itself
# ---------------------------------------------------------------------------

def test_collapse_mid_chain_rewires_both_boundary_sides(qapp):
    """The reported case: the incoming AND outgoing halves must both exist,
    and the block after the new hierarchy must keep its input."""
    rbd = _chain(["Xfer", "Util", "Xfmr", "Tail"])
    v = _view(rbd)
    before = _violation_keys(rbd)
    xfer, util, xfmr = _id(v, "Xfer"), _id(v, "Util"), _id(v, "Xfmr")
    _select(v, "Util")
    hid = v.collapse_selection("Wrapped")
    assert hid is not None
    assert hid in v.hiers and hid in v._hier_items
    assert len(rbd.sections["HIERARCHY_DATA"].rows) == 1

    hier = _hier_row(rbd, hid)
    in_id, out_id = int(float(hier["in"])), int(float(hier["out"]))
    links = _links(rbd)
    level = [r for r in links if r[5] == "0"]

    # Incoming: Xfer -> Hier (the oval keeps the outside half's id).
    assert any(f == "Block" and fi == str(xfer)
               and t == "Hier" and ti == str(hid) for _, f, fi, t, ti, _ in level)
    # Outgoing: Hier -> Xfmr -- exactly the half the issue lost.
    assert any(f == "Hier" and fi == str(hid)
               and t == "Block" and ti == str(xfmr) for _, f, fi, t, ti, _ in level)
    # Inside halves on the markers, filed under the hierarchy.
    inside = [r for r in links if r[5] == str(hid)]
    assert any(f == "Node" and fi == str(in_id) and t == "Block"
               and ti == str(util) for _, f, fi, t, ti, _ in inside)
    assert any(f == "Block" and fi == str(util) and t == "Node"
               and ti == str(out_id) for _, f, fi, t, ti, _ in inside)

    # The moved block is inside; nothing after it was orphaned.
    assert next(r for r in rbd.sections["BLOCK_DATA"].rows
                if r["ID"] == str(util))["ParentId"] == str(hid)
    assert _violation_keys(rbd) == before, "collapse must not add violations"


def test_collapse_undo_restores_rows_exactly_and_redo_reapplies(qapp):
    rbd = _chain(["Xfer", "Util", "Xfmr"])
    v = _view(rbd)
    snapshot = {name: [dict(r) for r in sec.rows]
                for name, sec in rbd.sections.items()}
    _select(v, "Util")
    v.collapse_selection("Wrapped")
    assert v._stack.count() == 1, "a collapse is ONE undo step"
    v._stack.undo()
    restored = {name: [dict(r) for r in sec.rows]
                for name, sec in rbd.sections.items()}
    assert restored == snapshot, "undo must restore the pre-collapse rows"
    assert not v.hiers and not v._hier_items
    assert v._stack.index() == 0
    # Redo is the same edit again, not a broken half-state.
    v._stack.redo()
    assert len(rbd.sections["HIERARCHY_DATA"].rows) == 1
    assert _violation_keys(rbd) == set()
    v._stack.undo()
    assert {name: [dict(r) for r in sec.rows]
            for name, sec in rbd.sections.items()} == snapshot


def test_collapse_multi_element_selection_still_works(qapp):
    rbd = _chain(["Xfer", "Util", "Xfmr", "Tail"])
    v = _view(rbd)
    before = _violation_keys(rbd)
    util, xfmr, tail = _id(v, "Util"), _id(v, "Xfmr"), _id(v, "Tail")
    _select(v, "Util", "Xfmr")
    hid = v.collapse_selection("Pair")
    assert hid is not None
    links = _links(rbd)
    # The link between the two selected blocks moves down a level.
    assert any(f == "Block" and fi == str(util) and t == "Block"
               and ti == str(xfmr) and p == str(hid)
               for _, f, fi, t, ti, p in links)
    # Only the outer boundary touches the oval on the parent level: one
    # link in, one link out.
    level = [r for r in links if r[5] == "0"]
    assert len([r for r in level if "Hier" in (r[1], r[3])]) == 2
    assert any(t == "Hier" and ti == str(hid) for _, _, _, t, ti, _ in level)
    assert any(f == "Hier" and fi == str(hid) and ti == str(tail)
               for _, f, fi, _, ti, _ in level)
    assert _violation_keys(rbd) == before


# ---------------------------------------------------------------------------
# One boundary side (start / end selections)
# ---------------------------------------------------------------------------

def test_collapse_at_the_end_of_the_chain_keeps_working(qapp):
    """A model that already ends at a block (no End node): the selection has
    only an incoming boundary. The new hierarchy inherits the dangling side
    instead of the collapse being refused."""
    rbd = _chain(["A", "B"], end=False)
    v = _view(rbd)
    before = {v.key for v in check(rbd)}
    assert any(v_.rule == "run.block_to" for v_ in check(rbd))
    _select(v, "B")
    hid = v.collapse_selection("TailHier")
    assert hid is not None, "a one-sided (end) selection must still collapse"
    links = _links(rbd)
    assert any(t == "Hier" and ti == str(hid) for _, _, _, t, ti, _ in links)
    assert not any(f == "Hier" and fi == str(hid) for _, f, fi, _, _, _ in links)
    # The only new violations are the hierarchy and its Out marker
    # inheriting B's missing output -- nothing outside was orphaned.
    hier = _hier_row(rbd, hid)
    out_id = int(float(hier["out"]))
    new = {v_.key for v_ in check(rbd)} - before
    assert new <= {("run.hier_to", f"Hier {hid}"),
                   ("run.node_from", f"Node {out_id}")}


def test_collapse_at_the_start_of_the_chain_keeps_working(qapp):
    """The mirror case: a model that starts at a block (no Start node)."""
    rbd = _chain(["A", "B"], start=False)
    v = _view(rbd)
    before = {v_.key for v_ in check(rbd)}
    assert any(v_.rule == "run.block_from" for v_ in check(rbd))
    _select(v, "A")
    hid = v.collapse_selection("HeadHier")
    assert hid is not None, "a one-sided (start) selection must still collapse"
    links = _links(rbd)
    assert any(f == "Hier" and fi == str(hid) for _, f, fi, _, _, _ in links)
    assert not any(t == "Hier" and ti == str(hid) for _, _, _, t, ti, _ in links)
    hier = _hier_row(rbd, hid)
    in_id = int(float(hier["in"]))
    new = {v_.key for v_ in check(rbd)} - before
    assert new <= {("run.hier_from", f"Hier {hid}"),
                   ("run.node_to", f"Node {in_id}")}


# ---------------------------------------------------------------------------
# Atomic refusal
# ---------------------------------------------------------------------------

def test_collapse_refuses_atomically_when_the_outgoing_half_is_lost(
        qapp, monkeypatch, request):
    """If the plan cannot rebuild a boundary half, the model must be left
    EXACTLY as it was and the user told why -- issue #72's invalid model
    must never be committed."""
    rbd = _chain(["Xfer", "Util", "Xfmr"])
    v = _view(rbd)
    rbd.annotations.append({"kind": "text", "id": 1, "x": 5.0, "y": 5.0,
                            "text": "note", "parent": 0})
    v._rebuild_scene()
    snapshot = {name: [dict(r) for r in sec.rows]
                for name, sec in rbd.sections.items()}
    assert "HIERARCHY_DATA" not in rbd.sections or not \
        rbd.sections["HIERARCHY_DATA"].rows

    real = plan_collapse

    def broken(*args, **kwargs):
        plan = real(*args, **kwargs)
        # Reproduce the reported failure: the outgoing outside half is
        # dropped, so the block after the selection loses its input.
        plan.link_ops = [
            op for op in plan.link_ops
            if not (op[0] == "add" and op[1].get("FromType") == "Hier")]
        return plan

    restore_plan = swap("plan_collapse", broken)
    request.addfinalizer(restore_plan)

    shown: list[str] = []
    from raptor.gui.ui import msgbox
    monkeypatch.setattr(msgbox, "warning",
                        lambda *a, **k: shown.append(a[2] if len(a) > 2 else ""))

    _select(v, "Util")
    v._anno_items[1].setSelected(True)
    hid = v.collapse_selection("Wrapped")
    assert hid is None
    # The selected annotation must NOT have been re-parented by a refused
    # collapse (the old code wrote it after the command, unconditionally).
    assert shown and "not applied" in shown[-1]
    assert "Xfmr" in shown[-1]
    # Atomic: no hierarchy rows (the section is not even created), no
    # re-parented block, no link edits, no moved annotation.
    assert "HIERARCHY_DATA" not in rbd.sections or not \
        rbd.sections["HIERARCHY_DATA"].rows
    assert {name: [dict(r) for r in sec.rows]
            for name, sec in rbd.sections.items()} == snapshot
    assert rbd.annotations[0]["parent"] == 0
    assert not v._stack.canUndo()


def test_collapse_refuses_a_link_filed_on_another_level(qapp, monkeypatch):
    """A boundary link the plan cannot reach (pID != current level) is
    refused before anything is written, rather than stranding half the
    edge inside the new hierarchy."""
    rbd = _chain(["Xfer", "Util", "Xfmr"])
    v = _view(rbd)
    # File Util -> Xfmr under a different level.
    outgoing = next(r for r in rbd.sections["LINK_DATA"].rows
                    if r["FromType"] == "Block" and r["FromID"] == str(_id(v, "Util")))
    outgoing["pID"] = "77"
    snapshot = {name: [dict(r) for r in sec.rows]
                for name, sec in rbd.sections.items()}
    shown: list[str] = []
    from raptor.gui.ui import msgbox
    monkeypatch.setattr(msgbox, "warning",
                        lambda *a, **k: shown.append(a[2] if len(a) > 2 else ""))
    _select(v, "Util")
    assert v.collapse_selection("Wrapped") is None
    assert shown and "another hierarchy level" in shown[-1]
    assert {name: [dict(r) for r in sec.rows]
            for name, sec in rbd.sections.items()} == snapshot
    assert not v._stack.canUndo()


def test_api_collapse_refuses_a_link_on_another_level(tmp_path):
    """The API counterpart refuses the same unrebuildable boundary without
    committing anything to the workspace."""
    from raptor.api.build import BuildError, collapse_selection as api_collapse
    from raptor.api.workspace import Workspace

    rbd = _chain(["Xfer", "Util", "Xfmr"])
    util = next(r["ID"] for r in rbd.sections["BLOCK_DATA"].rows
                if r["blockName"] == "Util")
    ws = Workspace(tmp_path)
    model_id = ws.register_model(rbd, name="chain")["model_id"]
    handle = ws.get(model_id)
    outgoing = next(r for r in handle.rbd.sections["LINK_DATA"].rows
                    if r["FromType"] == "Block" and r["FromID"] == util)
    outgoing["pID"] = "77"

    with pytest.raises(BuildError, match="another hierarchy level"):
        api_collapse(ws, model_id, ["Util"], "Wrapped")

    assert not handle.rbd.sections.get("HIERARCHY_DATA", None) or \
        not handle.rbd.sections["HIERARCHY_DATA"].rows
    assert next(r for r in handle.rbd.sections["BLOCK_DATA"].rows
                if r["ID"] == util)["ParentId"] == "0"
    assert next(r for r in handle.rbd.sections["LINK_DATA"].rows
                if r["ID"] == outgoing["ID"])["pID"] == "77"


# ---------------------------------------------------------------------------
# Annotations follow -- and only in the committed edit
# ---------------------------------------------------------------------------

def test_collapse_moves_a_selected_annotation_and_undo_restores_it(qapp):
    rbd = _chain(["Xfer", "Util", "Xfmr"])
    v = _view(rbd)
    rbd.annotations.append({"kind": "text", "id": 1, "x": 5.0, "y": 5.0,
                            "text": "note", "parent": 0})
    v._rebuild_scene()
    _select(v, "Util")
    v._anno_items[1].setSelected(True)
    hid = v.collapse_selection("Wrapped")
    assert hid is not None
    assert rbd.annotations[0]["parent"] == hid
    v._stack.undo()
    assert rbd.annotations[0]["parent"] == 0


# ---------------------------------------------------------------------------
# View / Explorer / Inspector counts and the window's undo action
# ---------------------------------------------------------------------------

def _explorer_texts(explorer) -> list[str]:
    out: list[str] = []

    def walk(item):
        out.append(item.text(0))
        for i in range(item.childCount()):
            walk(item.child(i))

    root = explorer._tree.topLevelItem(0)
    if root is not None:
        walk(root)
    return out


def test_collapse_is_visible_to_view_explorer_and_inspector(qapp):
    from raptor.gui.ui.main_window import MainWindow

    rbd = _chain(["Xfer", "Util", "Xfmr"])
    before_links = [dict(r) for r in rbd.sections["LINK_DATA"].rows]
    w = MainWindow()
    try:
        w._load_into_app(rbd, None)
        qapp.processEvents()
        v = w._diagram_dock.view
        _select(v, "Util")
        w._on_collapse_selection()
        qapp.processEvents()

        assert len(v.hiers) == 1
        hid = next(iter(v.hiers))
        assert hid in v._hier_items
        assert any(v.hiers[hid].name in text
                   for text in _explorer_texts(w._explorer))

        w._inspector._refresh_empty_text()
        assert "1 hierarchies" in w._inspector._empty.text()

        assert w._undo_action.isEnabled()
        w._undo_action.trigger()
        qapp.processEvents()
        assert not v.hiers
        assert [dict(r) for r in rbd.sections["LINK_DATA"].rows] == before_links
    finally:
        w._dirty = False
        w.close()
