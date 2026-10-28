# DepHierProbe — awaiting real capture

Built 2026-09-16 via `tools/rbd_builder.py` from `HierarchyExample`.
**No real capture yet.** The ONLY change is:

    Thermal:  DepNum 0 -> 25,  DepType 'None' -> 'RBDHier'

**2026-09-19 FIX:** was `DepNum=23` (the HIERARCHY id), which crashed real
Raptor on simulate -- `CheckForDeps` resolves an `RBDHier` dependency as
`Child("RBDNode", simDependencyNum)`, so `DepNum`
must be the hierarchy's OUT NODE id. `500path`(hier 23) has `out=25`.

`Thermal` was chosen because it is the one block in that fixture with NO
dependency at all (`DepNum=0`), so this ADDS a dependency rather than
replacing one that existing captures already verify. Hierarchy 23 is
`500path`, a different branch of the model, so the dependency is
observable.

## What it is for

`dep:RBDHier` — a block depending on a HIERARCHY rather than on a node or
another block — is in the internal fixture-coverage notes's gap list with no
fixture anywhere in the corpus setting it.

## It found a latent bug before it was ever captured

`dependency.py`'s dispatch recognises exactly two dep_types:
`is_node_dependent` tests `dep_type == "RBDNode"` and the
block-to-block path tests `dep_type == "RBDBlock"`. `"RBDHier"`
matches NEITHER — so before 2026-09-16 this port ran such a model happily
and simulated the block as having NO DEPENDENCY AT ALL. Silently wrong,
not loudly unported, and invisible precisely because no fixture set it.

`orchestrator.py`'s per-block spawn loop now raises for any
`dependency_num > 0` whose `dep_type` is neither of the two ported kinds.
So **this fixture currently RAISES in this port by design** — that is the
correct state until the feature is ported, and the capture is what the
port will be written against.

## To capture

Load `input.rbd`, Launch > Simulation..., Files & Reports > tick
"Detailed event log" + "End of run", OK. Save into `legacy/`. The thing
to look for is whether `Thermal` goes Idle/Failed in response to
`500path` going down, and with what event-log wording.
