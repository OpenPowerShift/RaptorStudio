"""A hierarchy built nested gets the fields real writes: `level` = depth, and the header's `HierDepths` is 1 once any
hierarchy exists (every real file with hierarchies has 1, nested or not). Found building `LadL8A`."""
from __future__ import annotations

from raptor.api.build import build_from_spec, collapse_selection
from raptor.core.fileio.builder import sync_counts

SPEC = {
    "name": "Nested",
    "blocks": [{"name": n, "fail_dist": 4, "fail_params": [300.0], "repair_dist": 4, "repair_params": [10.0]}
               for n in "AB"],
    # start -> S -> A/B -> J -> end: a Start marker and a hierarchy each take one link out (Raptor 7).
    "nodes": [{"name": "start", "type": 1, "n": 0, "out": 1}, {"name": "S", "type": 2, "n": 1, "k": 1, "out": 2},
              {"name": "J", "type": 2, "n": 2, "k": 1, "out": 1}, {"name": "end", "type": 3, "n": 1, "out": 0}],
    "links": [{"from": "start", "to": "S"}, {"from": "S", "to": "A"}, {"from": "S", "to": "B"},
              {"from": "A", "to": "J"}, {"from": "B", "to": "J"}, {"from": "J", "to": "end"}],
}


class _Handle:
    def __init__(self, rbd):
        self.rbd = rbd


class _Workspace:
    def __init__(self, rbd):
        self._h = _Handle(rbd)

    def get(self, _):
        return self._h

    def save(self, _):
        pass


def test_no_hierarchy_means_hier_depths_zero():
    rbd = build_from_spec(SPEC)
    sync_counts(rbd)
    assert rbd.counts["HierDepths"] == 0


def test_nested_hierarchy_level_and_header():
    rbd = build_from_spec(SPEC)
    ws = _Workspace(rbd)
    outer = collapse_selection(ws, "m", ["S", "A", "B", "J"], "Outer")
    inner = collapse_selection(ws, "m", ["A"], "Inner", parent=int(outer["ID"]))
    sync_counts(rbd)
    levels = {h["hierarchyName"]: h["level"] for h in rbd.section("HIERARCHY_DATA").rows}
    assert levels == {"Outer": "1", "Inner": "2"}
    assert rbd.counts["HierDepths"] == 1
    assert int(inner["out"]) != int(outer["out"])
