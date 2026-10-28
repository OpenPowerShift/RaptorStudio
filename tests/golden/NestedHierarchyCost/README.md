# NestedHierarchyCost

Synthetic fixture, derived from `HierarchyExample/input.rbd`, purpose-built
to exercise nested-hierarchy cost rollup (a hierarchy nested inside another
hierarchy, `HIERARCHY_DATA.Parent` pointing at another hierarchy's ID rather
than 0). No other fixture in the repo nests hierarchies.

Changes from `HierarchyExample`:
- `SYSTEM_DATA.costAnalysis = 1` (cost analysis enabled).
- Hierarchy 26 ("650path") re-parented under hierarchy 23 ("500path"):
  `Parent: 0 -> 23`, `level: 1 -> 2`.

This was built to verify `orchestrator._rollup_nested_hier_costs` (see
The internal investigation log §22), ported from Raptor 7.
Verified two ways so far:
1. Synthetic unit tests (`tests/unit/test_hier_cost_rollup.py`).
2. Running this fixture through the Python engine (10 runs): hierarchy 23's
   total (1106322.5) correctly includes hierarchy 26's total (534475.0)
   folded in; hierarchy 29 ("900path", unrelated sibling) is unaffected
   (484382.5).

Not yet captured against real Raptor 7 — there's no `legacy/` subdirectory
here. If you want ground-truth verification, open this file in real Raptor,
run 10 runs, and export the Life-Cycle Cost report (the Hierarchy cost
table specifically); send the export back and it'll get dropped into
`legacy/` alongside a golden-log comparison test, same shape as the other
fixtures in this directory. The port is already implemented and tested by
the two methods above, so this step is optional confirmation rather than a
blocker.
