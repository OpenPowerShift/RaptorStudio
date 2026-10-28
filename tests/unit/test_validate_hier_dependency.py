"""`validate()` rejects a hierarchy dependency whose DepNum is the hierarchy's own id.

A block that depends on a hierarchy names the hierarchy's OUT node (`DepHierProbe`: hierarchy 23, in 24, out 25,
`DepNum` 25). Giving the hierarchy id made the engine fail with "block has a node-based dependency but no dep_node"
(found building `LadL7HierFull`).
"""
from __future__ import annotations

from raptor.core.fileio.builder import validate
from raptor.core.fileio.rbd import load_rbd


def _rbd():
    return load_rbd("tests/golden/DepHierProbe/input.rbd")


def test_real_hierarchy_dependency_is_accepted():
    assert not [i for i in validate(_rbd()) if "RBDHier" in i]


def test_hierarchy_id_as_depnum_is_an_error():
    rbd = _rbd()
    hier_id = rbd.section("HIERARCHY_DATA").rows[0]["ID"]
    for row in rbd.section("BLOCK_DATA").rows:
        if row.get("DepType") == "RBDHier":
            row["DepNum"] = hier_id
    assert any("not a hierarchy's Out node id" in i for i in validate(rbd))
