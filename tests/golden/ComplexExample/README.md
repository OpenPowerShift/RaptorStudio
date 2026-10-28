# Fixture: ComplexExample — the real bundled Student Edition `Complex.rbd`

`input.rbd` copied verbatim from the Student Edition's own
`Examples/Complex.rbd` (5 blocks: A/B/C/D/E, 10 nodes, 18 links, no
hierarchy, no events, no sparing — all `Infinite`). `NumRuns=10`,
`TermCondition=50.0` (`TimeSim`). NOT hand-built, and NOT the same
fixture as `tests/golden/ComplexModel` (a hand-built 3-block model from
Phase 4's own verification pass) — this is the vendor's own example,
distinguished by name to avoid confusion.

## Status: SOLVED 2026-08-15 — all 10 runs bit-exact

`legacy/rpt_events_complex.txt` — real capture, 10 runs, 245 lines. All
10 runs match exactly (fp32 noise aside): 198 matching / 5 fp32-noise-only
/ 0 structural mismatches. Confirms three separate pieces of code ported
this session, all at once:

- **`No_repair`** (`RDist=18`) — every block in this fixture fails once
  and never repairs.
- **Multi-hop `Permanently_Down`** (`cascade.reconfigure_system`) —
  node `n5` fans out to `n6`/`n7`, so a later failure elsewhere has to
  recurse through the graph before reaching End.
- **Node-triggered dependency propagation** (`sysDepend`/`DepNum=-2` —
  every block here depends on the model's own End node). This was the
  hardest of the three: getting it bit-exact required TWO fixes beyond
  the dependency dispatch logic itself (`dependency.
  node_dependency_trigger`/`back_flow_dependencies_from_end_node`):

  1. `block_run_process` (the ordinary block driver -- what A-E actually
     run through, since their `dep_type` is `"None"`, only the
     dependency TARGET is special) had to become interrupt-aware at its
     RUNNING fail-wait, mirroring `orchestrator._dependent_block_process`'s
     existing `FailTimeLeft`-preserving pattern (pause the failure clock,
     wait in Idle for an external recovery signal, resume with the
     REMAINING time rather than redrawing).

  2. A block whose CURRENT fail-time draw already overshoots
     `run_length` takes a SEPARATE, non-obviously-related wait
     (`yield run_length - scheduler.time`, so its forced `"Done"/
     "End_sim"` line lands at exactly `run_length`) -- this ALSO needs
     to be interrupt-aware, or an interrupt arriving during that window
     silently falls through the bare `yield` and wrongly fires an
     immediate spurious `Done`/`End_sim` at the interrupt's own
     timestamp instead of pausing. Found via runs 2 and 5 (`Block B`/`C`
     both already past their last real failure for the run, mid-wait for
     `run_length`, right when the `sysDepend` trigger fired) -- exactly
     the scenario the ORIGINAL code's own docstring had flagged as
     "no fixture exercises this" before this fixture did.

## Files

- `input.rbd` — copied verbatim from the Student Edition's own
  `Examples/Complex.rbd`.
- `legacy/rpt_events_complex.txt` — real capture, 10 runs, 245 lines.

## What this is for

A passing regression fixture in `test_orchestrator.py`, same status as
every other verified fixture. The real proof that node-triggered
`sysDepend` dependency propagation (a mechanism no other fixture
exercises) is correct end-to-end, including its interaction with the
run-length boundary.
