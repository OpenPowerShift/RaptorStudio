"""Copy and paste as file-row plans -- no Qt."""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path

from raptor.core.fileio.rbd import load_rbd
from raptor.core.fileio.typed import hier_from_row
from raptor.gui.clipboard_plan import count_elements, plan_copy, plan_paste

GOLD = Path(__file__).resolve().parents[1] / "golden"


def _model(name):
    rbd = load_rbd(str(GOLD / name / "input.rbd"))
    hiers = {}
    for r in rbd.sections.get("HIERARCHY_DATA", type("S", (), {"rows": []})).rows:
        try:
            h = hier_from_row(r)
        except (ValueError, KeyError):
            continue
        if h.id:
            hiers[h.id] = h
    return rbd, hiers


def _ids(rbd, section):
    return {int(float(r["ID"])) for r in rbd.sections[section].rows}


def _live(rbd, hiers):
    return {"block": _ids(rbd, "BLOCK_DATA"), "node": _ids(rbd, "NODE_DATA"),
            "event": _ids(rbd, "EVENT_DATA"), "hier": set(hiers)}


def test_modules_need_no_qt():
    code = ("import sys, raptor.gui.clipboard_plan;"
            "sys.exit(any(m.startswith('PySide') for m in sys.modules))")
    assert subprocess.run([sys.executable, "-c", code]).returncode == 0


def test_only_links_wholly_inside_the_copy_travel():
    rbd, hiers = _model("TwoBlockRedundancy")
    blocks = sorted(_ids(rbd, "BLOCK_DATA") - {0})
    one = plan_copy(rbd, hiers, blocks=blocks[:1], nodes=[], events=[],
                    sel_hiers=[], parent=0)
    assert not [k for k, _ in one if k == "link"]     # boundary links stay
    assert count_elements(one) == 1
    both = plan_copy(rbd, hiers, blocks=blocks, nodes=[], events=[],
                     sel_hiers=[], parent=0)
    assert count_elements(both) == len(blocks)


def test_a_hierarchy_copy_takes_its_markers_and_its_contents():
    rbd, hiers = _model("NestedHierarchyCost")
    outer = next(i for i, h in hiers.items() if not h.parent_id)
    buf = plan_copy(rbd, hiers, blocks=[], nodes=[], events=[],
                    sel_hiers=[outer], parent=0)
    kinds = [k for k, _ in buf]
    assert "hier" in kinds and "node" in kinds and "block" in kinds
    node_ids = {int(float(r["ID"])) for k, r in buf if k == "node"}
    assert {hiers[outer].in_id, hiers[outer].out_id} <= node_ids


def test_planning_a_copy_or_paste_leaves_the_file_alone():
    rbd, hiers = _model("ComplexModel")
    before = {n: [dict(r) for r in s.rows] for n, s in rbd.sections.items()}
    buf = plan_copy(rbd, hiers, blocks=sorted(_ids(rbd, "BLOCK_DATA") - {0}),
                    nodes=[], events=[], sel_hiers=list(hiers), parent=0)
    plan_paste(rbd, buf, _live(rbd, hiers), dx=1.0, dy=2.0, parent=0)
    assert before == {n: [dict(r) for r in s.rows]
                      for n, s in rbd.sections.items()}


def test_pasted_ids_are_fresh_and_positions_move_by_the_delta():
    rbd, hiers = _model("TwoBlockRedundancy")
    blocks = sorted(_ids(rbd, "BLOCK_DATA") - {0})
    buf = plan_copy(rbd, hiers, blocks=blocks, nodes=[], events=[],
                    sel_hiers=[], parent=0)
    plan = plan_paste(rbd, buf, _live(rbd, hiers), dx=10.0, dy=-5.0, parent=0)
    new = [int(r["ID"]) for r in plan.new_rows["block"]]
    assert len(new) == len(blocks) and not set(new) & _ids(rbd, "BLOCK_DATA")
    src = {int(float(r["ID"])): r for r in rbd.sections["BLOCK_DATA"].rows}
    for (_k, old), nr in zip([b for b in buf if b[0] == "block"],
                             plan.new_rows["block"]):
        assert float(nr["xPos"]) == float(old["xPos"]) + 10.0
        assert float(nr["yPos"]) == float(old["yPos"]) - 5.0
    assert src


def test_pasted_links_join_the_pasted_elements_at_the_pasted_level():
    rbd, hiers = _model("TwoBlockRedundancy")
    ids = sorted(_ids(rbd, "BLOCK_DATA") - {0})
    nodes = [n for n in _ids(rbd, "NODE_DATA") if n]
    buf = plan_copy(rbd, hiers, blocks=ids, nodes=nodes, events=[],
                    sel_hiers=[], parent=0)
    plan = plan_paste(rbd, buf, _live(rbd, hiers), dx=0.0, dy=0.0, parent=7)
    new_blocks = {int(r["ID"]) for r in plan.new_rows["block"]}
    new_nodes = {int(r["ID"]) for r in plan.new_rows["node"]}
    assert plan.link_rows
    for r in plan.link_rows:
        assert r["pID"] == "7"
        for side in ("From", "To"):
            pool = {"Block": new_blocks, "Node": new_nodes}[r[side + "Type"]]
            assert int(r[side + "ID"]) in pool
    lids = [int(r["ID"]) for r in plan.link_rows]
    assert len(lids) == len(set(lids))
    assert not set(lids) & _ids(rbd, "LINK_DATA")


def test_a_copy_includes_hierarchies_nested_in_it_and_their_links():
    from raptor.gui.clipboard_plan import subtree_hiers
    rbd, hiers = _model("NestedHierarchyCost")
    outer = next(i for i, h in hiers.items() if not h.parent_id)
    inner = {i for i, h in hiers.items() if h.parent_id == outer}
    assert inner
    assert subtree_hiers(rbd, [outer]) == {outer} | inner
    buf = plan_copy(rbd, hiers, blocks=[], nodes=[], events=[],
                    sel_hiers=[outer], parent=0)
    assert {int(float(r["ID"])) for k, r in buf if k == "hier"} == \
        {outer} | inner


def test_a_pasted_hierarchy_is_reparented_and_points_at_its_new_markers():
    rbd, hiers = _model("NestedHierarchyCost")
    outer = next(i for i, h in hiers.items() if not h.parent_id)
    buf = plan_copy(rbd, hiers, blocks=[], nodes=[], events=[],
                    sel_hiers=[outer], parent=0)
    plan = plan_paste(rbd, buf, _live(rbd, hiers), dx=0, dy=0, parent=5,
                      parent_level=3)
    new_h = {int(r["ID"]): r for r in plan.new_rows["hier"]}
    assert not set(new_h) & set(hiers)
    new_nodes = {int(r["ID"]) for r in plan.new_rows["node"]}
    top = next(r for r in new_h.values() if r["Parent"] == "5")
    assert top["level"] == "4"
    assert int(top["in"]) in new_nodes and int(top["out"]) in new_nodes
    nested = [r for r in new_h.values() if r is not top]
    assert nested and all(int(r["Parent"]) in new_h for r in nested)
    assert all(int(r["level"]) > 4 for r in nested)
    # nothing still hangs under an ORIGINAL hierarchy
    for r in plan.new_rows["block"]:
        assert int(r["ParentId"]) in new_h
    for r in plan.new_rows["node"]:
        assert int(r["parent"]) in new_h


def test_loose_elements_belong_to_the_level_pasted_into():
    rbd, hiers = _model("NestedHierarchyCost")
    blocks = sorted(_ids(rbd, "BLOCK_DATA") - {0})[:1]
    buf = plan_copy(rbd, hiers, blocks=blocks, nodes=[], events=[],
                    sel_hiers=[], parent=0)
    plan = plan_paste(rbd, buf, _live(rbd, hiers), dx=0, dy=0, parent=26)
    assert {r["ParentId"] for r in plan.new_rows["block"]} == {"26"}
