# Fixture: ComplexModel — everything at once, driven through the orchestrator

Captured 2026-08-15 from Raptor 7 Student Edition under Wine. Hand-built
(new topology, not adapted from an existing fixture's data): three blocks
feeding a 2-of-3 `join` — block A, block B (100% dependent on A, same
`DepNum`/`DepType`/`DepIdle` config as `BlockDependency`), and block D
living inside a hierarchy `H` (`in -> D -> out`, same shape as
`SingleHierarchy`) whose external `Hier -> Node(join)` link is `join`'s
third input. `start -> {A, B, H} -> join(k=2,n=3) -> end`. `NumRuns=5`.

## What this is for

The final verification step for Phase 4 v1's general orchestration layer
(`raptor/core/engine/orchestrator.py`): every other fixture so far
exercises ONE ported mechanism at a time. This one combines K-out-of-N,
block dependency, AND hierarchy in a single model to prove the
orchestrator generalizes, not just reproduces the 5 fixtures it was
built against. My own engine ran it cleanly (no crashes, no
`NotImplementedError`) before this was ever run for real — the real
capture then found one genuine new bug (below), which no single-mechanism
fixture could have surfaced.

## The bug this fixture found

`_Cascade`'s system-status check has **two independent triggers**, and
getting the interaction between them wrong is easy to miss with only
2-input topologies:

1. The ordinary `top_level`-gated check, once per OUTERMOST (scheduler-
   driven) block transition — fires even when that transition's own
   cascade never reaches the End node at all (a plain node-level
   `Degraded`, in the up-like bucket, produces zero further-cascade
   deltas and stops at the intermediate node).
2. An immediate check the instant ANY cascade — top-level OR a
   dependency-triggered NESTED one — reaches and changes the End node's
   status, **but only when that change is DOWN** (`system_up: True ->
   False`). An UP transition (`False -> True`) does NOT get an immediate
   check; it waits for the outer `top_level` check once the whole
   triggering cascade (including the depended-upon block's own further
   updates) has settled.

`tests/golden/BlockDependency` (1-of-2 join) never distinguishes these:
a dependent block's own nested cascade there only ever reaches the
intermediate `join` node (same up-like bucket, zero further deltas) —
it never reaches End by itself, so only trigger (1) is ever exercised.
This fixture's 2-of-3 join is exactly what makes a dependent block's own
nested cascade able to flip End's status *by itself*, on both the Down
and the AllUp branch of `BackFlowDependencies` — surfacing the DOWN-vs-
UP asymmetry directly. Confirmed real order for the DOWN case:
`Node join -> Node end -> System State_change` all within the DEPENDENT
block's own nested cascade, BEFORE the depended-upon block's own
subsequent (same-bucket, silent) reconfigure. Confirmed real order for
the UP case: the dependent's own nested cascade reaches End silently
(no line), and the System line only appears AFTER the depended-upon
block's own later top-level update.

Source mechanism not located (same kind of gap as `red_string`'s reset
and the run-boundary reset, see those modules' docstrings) — this is the
accumulated empirical evidence from comparing both fixtures side by side.

## Files

- `input.rbd` — the model.
- `legacy/rpt_events_complex.txt` — event log, 5 runs, 15034 lines.

Verified via `tests/unit/test_orchestrator.py` (parametrized alongside
every other fixture) — zero structural mismatches across all 5 runs.
