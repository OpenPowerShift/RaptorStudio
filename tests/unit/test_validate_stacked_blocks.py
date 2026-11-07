"""Validation warns about blocks drawn on top of each other."""
from __future__ import annotations

from raptor.api.validate import issues
from raptor.core.fileio.rbd import load_rbd


def _set(rbd, name, x, y):
    for r in rbd.sections["BLOCK_DATA"].rows:
        if r.get("blockName") == name:
            r["xPos"], r["yPos"] = str(x), str(y)


def _stacked(rbd):
    return [i for i in issues(rbd) if "drawn on top of" in i.message]


def test_blocks_at_one_spot_warn_and_name_the_top_one():
    rbd = load_rbd("tests/golden/Phasing3/input.rbd")
    names = [r["blockName"] for r in rbd.sections["BLOCK_DATA"].rows
             if r.get("ID") not in ("0", None)]
    a, b = names[0], names[1]
    _set(rbd, a, 50, 50)
    _set(rbd, b, 50, 50)
    found = _stacked(rbd)
    assert len(found) == 1
    w = found[0]
    assert w.severity == "warning"
    assert b in w.element and f"'{a}'" in w.message     # later row is on top


def test_blocks_apart_do_not_warn():
    rbd = load_rbd("tests/golden/Phasing3/input.rbd")
    assert _stacked(rbd) == []


def _node(rbd):
    return next(r for r in rbd.sections["NODE_DATA"].rows
                if r.get("ID") not in ("0", None))


def test_a_node_on_a_block_is_named_as_the_top_element():
    rbd = load_rbd("tests/golden/Phasing3/input.rbd")
    blk = next(r for r in rbd.sections["BLOCK_DATA"].rows
               if r.get("ID") not in ("0", None))
    node = _node(rbd)
    node["parent"] = blk["ParentId"]
    node["xPos"], node["yPos"] = blk["xPos"], blk["yPos"]
    hits = [i for i in _stacked(rbd) if i.element.startswith("Node")]
    assert len(hits) == 1 and hits[0].severity == "warning"
    assert f"Block '{blk['blockName']}'" in hits[0].message
    # and the block is not reported as covering the node (nodes draw later)
    assert not [i for i in _stacked(rbd)
                if i.element.startswith(f"Block {blk['ID']} ")]


def test_two_nodes_at_one_spot_name_the_later_one():
    rbd = load_rbd("tests/golden/Phasing3/input.rbd")
    nodes = [r for r in rbd.sections["NODE_DATA"].rows
             if r.get("ID") not in ("0", None)]
    a, b = nodes[0], nodes[1]
    b["parent"] = a["parent"]
    b["xPos"], b["yPos"] = a["xPos"], a["yPos"]
    hits = [i for i in _stacked(rbd) if i.element.startswith("Node")]
    assert len(hits) == 1
    assert b["Name"] in hits[0].element and f"Node '{a['Name']}'" in hits[0].message


def test_elements_on_different_levels_do_not_warn():
    rbd = load_rbd("tests/golden/Phasing3/input.rbd")
    blk = next(r for r in rbd.sections["BLOCK_DATA"].rows
               if r.get("ID") not in ("0", None))
    node = _node(rbd)
    node["xPos"], node["yPos"] = blk["xPos"], blk["yPos"]
    node["parent"] = str(int(blk["ParentId"]) + 99)
    assert _stacked(rbd) == []
