# Fixture: SingleHierarchy — hierarchy-internal-exit-node log line

Captured 2026-08-15 from Raptor 7 Student Edition under Wine. Hand-built
(not adapted from an existing fixture's data — the topology itself is
new): `start -> Hier(H) -> end` at the outer level, with `H` containing
one Uniform(0,1) fail/repair block internally: `in -> block -> out`
(`in`=node type 4, `out`=node type 5 — Student's own bundled
`Examples/Hierarchy.rbd` used as the field-mapping template for the
hierarchy/node rows). `NumRuns=5`.

## What it shows

`ReconfigureNode`'s `typeNode=5` branch: a
hierarchy's internal exit node logs `"Hier"`/`hier.name` instead of
`"Node"`/`node.node_name`, otherwise identical K-out-of-N aggregation —
and that log line's status change cascades onward through the
hierarchy's own external link (`Hier -> Node(end)`) exactly like an
ordinary node-to-node connection, no extra mechanism needed:

```
0.924530   Block   unnamed   Failed      Operating_time=0.924530
0.924530   Block   unnamed   Repairing   No_resources_used
0.924530   Hier    H         Bad         0-1/1_df4
0.924530   Node    end       Bad         0-1/1_df2
0.924530   System  State_change  Red     _d3
```

`RBDHierObj.ChangeStatus`/`ChangeDSIStatus` (GUI coloring +
`NumCurrentlyFailed`/`DSIsignal` rollup to a parent hierarchy) are NOT
exercised here in any observable way — read both methods in full before
building this fixture and confirmed neither ever calls `WriteEvents`, so
they're invisible in any event log regardless of topology. Not ported;
see `nodes.py`'s module docstring.

## A real fixture-building bug found (not a hierarchy issue)

The first attempt at this fixture crashed Raptor7Student.exe on open
(`mserr.txt`: `"Array allocation: high bound is less than low bound"`).
Cause: `BLOCK_DATA` needs a mandatory `ID=0` sentinel/template row
(`ParentId=0, In=0, Out=0`) present in EVERY fixture's `BLOCK_DATA`
section (confirmed present in `TwoBlockRedundancy`, `FiniteSparePool`,
`PrePostLDT`, and the real `Examples/Hierarchy.rbd`) — dropped by
mistake while trimming a template down to one real block. The `Blocks=`
header count itself EXCLUDES this sentinel row (e.g. `TwoBlockRedundancy`
has 2 real blocks + 1 sentinel = 3 `BLOCK_DATA` rows but `Blocks=2`).
Fixed by re-adding the sentinel row and correcting the header counts;
worked first try after that. No other section needs an equivalent
sentinel (confirmed against the real `Hierarchy.rbd`'s `NODE_DATA`/
`HIERARCHY_DATA`/`EVENT_DATA` — none have a stray `ID=0` row).

## Files

- `input.rbd` — the model.
- `legacy/rpt_events_singlehierarchy.txt` — event log, 5 runs, 5037
  lines, 1001 `"Hier"` occurrences.

## What this is for

Verifies `nodes.determine_node_status`'s `hier_name`/`typeNode=5`
handling and `cascade.py`'s `hier_name`/`hier_names_by_node_id`
threading — see `tests/unit/test_hierarchy.py` (single scheduler
process, block -> hier-exit-node -> end -> system, all 5 real runs, zero
structural mismatches, matched on the first attempt after the sentinel-
row fix).
