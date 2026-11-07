"""Dependencies survive a copy when both ends are copied, and only then."""
from __future__ import annotations

from raptor.core.fileio.rbd import load_rbd
from raptor.gui.clipboard_plan import plan_paste, remap_dependencies

MODEL = "tests/golden/Phasing3/input.rbd"


def _plan(buffer, *, keep_external):
    rbd = load_rbd(MODEL)
    live = {"block": {1, 2, 3}, "node": {1}, "event": set(), "hier": set()}
    plan = plan_paste(rbd, buffer, live, dx=0.0, dy=0.0, parent=0)
    dropped = remap_dependencies(plan, keep_external=keep_external)
    return plan, dropped


def _blk(i, dep="0", typ="None"):
    return ("block", {"ID": str(i), "blockName": f"B{i}", "xPos": "0",
                      "yPos": "0", "ParentId": "0", "DepNum": dep,
                      "DepType": typ})


def _node(i, dep="0", typ="None"):
    return ("node", {"Name": f"N{i}", "ID": str(i), "xPos": "0", "yPos": "0",
                     "parent": "0", "Type": "2", "depNum": dep,
                     "depType": typ})


def test_block_on_a_copied_block_follows_the_copy():
    plan, dropped = _plan([_blk(20), _blk(21, "20", "RBDBlock")],
                          keep_external=False)
    a, b = plan.new_rows["block"]
    assert dropped == 0 and b["DepNum"] == a["ID"] and b["DepType"] == "RBDBlock"
    assert a["ID"] != "20"


def test_block_on_a_copied_node_and_node_on_a_copied_node():
    plan, _ = _plan([_node(30), _node(31, "30", "RBDNode"),
                     _blk(22, "30", "RBDNode")], keep_external=False)
    n0, n1 = plan.new_rows["node"]
    (b,) = plan.new_rows["block"]
    assert n1["depNum"] == n0["ID"]
    assert b["DepNum"] == n0["ID"] and b["DepType"] == "RBDNode"


def test_block_on_a_copied_hierarchy_and_event():
    hier = ("hier", {"hierarchyName": "H", "ID": "9", "xPos": "0",
                     "yPos": "0", "Parent": "0", "in": "0", "out": "0",
                     "level": "1"})
    ev = ("event", {"eventName": "E", "ID": "4", "xPos": "0", "yPos": "0",
                    "ParentId": "0"})
    plan, dropped = _plan([hier, ev, _blk(23, "9", "RBDHier"),
                           _blk(24, "4", "RBDEvent")], keep_external=False)
    new_h, new_e = plan.new_rows["hier"][0]["ID"], plan.new_rows["event"][0]["ID"]
    b1, b2 = plan.new_rows["block"]
    assert dropped == 0
    assert (b1["DepNum"], b2["DepNum"]) == (new_h, new_e)


def test_dependency_on_something_left_behind_is_dropped_across_models():
    plan, dropped = _plan([_blk(25, "3", "RBDBlock")], keep_external=False)
    (b,) = plan.new_rows["block"]
    assert dropped == 1 and b["DepNum"] == "0" and b["DepType"] == "None"


def test_dependency_on_something_left_behind_is_kept_in_the_same_model():
    plan, dropped = _plan([_blk(26, "3", "RBDBlock")], keep_external=True)
    assert dropped == 0 and plan.new_rows["block"][0]["DepNum"] == "3"


def test_own_node_and_whole_system_are_never_touched():
    plan, dropped = _plan([_blk(27, "-1", "None"), _blk(28, "-2", "None")],
                          keep_external=False)
    assert dropped == 0
    assert [r["DepNum"] for r in plan.new_rows["block"]] == ["-1", "-2"]


def test_a_type_must_match_the_kind_of_the_id():
    # id 20 is a copied BLOCK; a node-typed dependency on 20 is not that block
    plan, dropped = _plan([_blk(20), _blk(21, "20", "RBDNode")],
                          keep_external=False)
    assert dropped == 1 and plan.new_rows["block"][1]["DepNum"] == "0"
