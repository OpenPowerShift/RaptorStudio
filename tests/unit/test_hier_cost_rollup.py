"""Unit tests for `rollup_nested_hier_costs` (costs.py), the
nested-hierarchy cost rollup ported from Raptor 7 (`mamaHier`).

The rollup is exercised directly against synthetic `HierCostRow` data,
covering the arithmetic and the deepest-first ordering. Per-block/event
accumulation into each hierarchy's own row is unchanged.
"""
from __future__ import annotations

from types import SimpleNamespace

from raptor.core.engine.costs import rollup_nested_hier_costs
from raptor.core.engine.reporting import HierCostRow


def _row(hid: int, name: str, total: float) -> HierCostRow:
    # purchase/operating/sparing/disposal/events chosen so their sum
    # equals `total`, so summing `total` alone is enough to check.
    return HierCostRow(name=name, hier_id=hid, purchase=total, operating=0.0,
                       sparing=0.0, disposal=0.0, events=0.0, total=total)


def _model(parents: dict[int, int]) -> SimpleNamespace:
    """`parents` maps hier id -> its own parent id (0 = top-level)."""
    hiers_by_id = {hid: SimpleNamespace(parent_id=pid)
                   for hid, pid in parents.items()}
    return SimpleNamespace(hiers_by_id=hiers_by_id)


def test_two_level_rollup():
    # Root (0-parented) with one nested child.
    hiers = {1: _row(1, "Root", 100.0), 2: _row(2, "HCF12", 50.0)}
    model = _model({1: 0, 2: 1})
    rollup_nested_hier_costs(hiers, model)
    assert hiers[1].total == 150.0   # root gained the child's total
    assert hiers[2].total == 50.0    # child's own row is untouched


def test_three_level_rollup_regardless_of_dict_order():
    # A (root) <- B <- C, each with its own direct cost of 10/20/30.
    # C's total must reach A (10+20+30=60) whatever order the dict
    # iterates in, so build it parent-before-child (1, 2, 3) and
    # child-before-parent (3, 2, 1) and check both give the same result.
    for order in ((1, 2, 3), (3, 2, 1)):
        hiers = {hid: _row(hid, f"H{hid}", 10.0 * hid) for hid in order}
        model = _model({1: 0, 2: 1, 3: 2})
        rollup_nested_hier_costs(hiers, model)
        assert hiers[1].total == 60.0   # A: own(10) + B(20) + C(30)
        assert hiers[2].total == 50.0   # B: own(20) + C(30)
        assert hiers[3].total == 30.0   # C: own only, no children


def test_top_level_only_is_a_noop():
    # Hierarchies that are all top-level are unaffected by this pass.
    hiers = {1: _row(1, "A", 10.0), 2: _row(2, "B", 20.0)}
    model = _model({1: 0, 2: 0})
    rollup_nested_hier_costs(hiers, model)
    assert hiers[1].total == 10.0
    assert hiers[2].total == 20.0


def test_sibling_hierarchies_under_the_same_parent():
    # Two children of the same parent both roll up into it, and not
    # into each other.
    hiers = {1: _row(1, "Root", 100.0), 2: _row(2, "ChildA", 5.0),
             3: _row(3, "ChildB", 7.0)}
    model = _model({1: 0, 2: 1, 3: 1})
    rollup_nested_hier_costs(hiers, model)
    assert hiers[1].total == 112.0
    assert hiers[2].total == 5.0
    assert hiers[3].total == 7.0
