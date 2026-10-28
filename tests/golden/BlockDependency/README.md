# Fixture: BlockDependency — block-depends-on-block

Captured 2026-08-15 from Raptor 7 Student Edition under Wine. Same
two-block `join(1-of-2)->end` topology as `TwoBlockRedundancy` (built by
loading that fixture's `input.rbd` and overwriting just block B's (id=4)
`BLOCK_DATA` columns — same technique as `FiniteSparePool`), with one
change: block B depends 100% on block A (id=3) —
`DepNum=3, DepType=RBDBlock, DepIdle=100.00`. `NumRuns=5`.

## What it shows

Two genuinely different dependency mechanisms, both ported into
`dependency.py`:

**1. `BackFlowDependencies`** — fires when the DEPENDED-UPON block (A)
itself changes state while Running/Standby-vs-Down. `ReconfigureBlock`
calls it BEFORE its own `ReconfigureNode` call, so a dependent's own full
node cascade is logged FIRST, and only then does the depended-upon
block's own link update — a real, observable two-step `"Node"`/`"join"`
pair at the same `SimTime`:

```
0.924530   Block   unnamed   Failed       Operating_time=0.924530   {A fails}
0.924530   Block   unnamed   Repairing    No_resources_used
0.924530   Block   unnamed   Idle         Dependence                {B, via BackFlowDependencies}
0.924530   Node    join      Degraded     1-1/2_ur2_di4              {B's link update only -- A's link2 still "ur"}
0.924530   Node    join      Idle         0-1/2_df2_di4              {A's OWN reconfigure, now both links down}
0.924530   Node    end       Idle         0-1/1_di5
0.924530   System  State_change  Red      _d3
```

**2. `repair_completion_status`** (`DetermineNewState("Maintenance
Completed")`) — a SEPARATE mechanism on the
DEPENDENT block's (B) own repair-completion path: finishing repair while
the depended-upon block is *still* down goes straight to `Idle` with an
EMPTY comment (not `"Dependence"`), landing on `change_block_state`'s own
`"change_block_state"` fallback string:

```
1.963613   Block   unnamed   Repaired     MaintTime=0.733333   {B finishes repairing}
1.963613   Block   unnamed   Idle         change_block_state   {A is still Repairing -> straight to Idle, not Running}
1.963613   Node    join      Idle         0-1/2_df2_di4
1.963613   Node    end       Idle         0-1/1_di5
1.963613   System  Red_change  Red        _r3
```

Also surfaced two real bugs, both fixed as part of this fixture (not
dependency-specific — either could have bitten any fixture with enough
Red/Green churn):

- **`SystemState.red_string` wasn't reset by ordinary color changes.**
  Raptor 7's `ChangeStatus` reuses the same `redString` global
  as both `red_change_line`'s dedup memory AND its own `"State_change"`
  comment scratch buffer — every actual color change resets it as a side
  effect, not just run boundaries. Without this, a `Red_change` comment
  computed long before a Green excursion and a later fresh Red re-entry
  could wrongly compare equal to a freshly-recomputed identical-looking
  comment and get deduplicated away. Fixed in `system_status.py`'s
  `change_system_status`.
- **A dependency-forced Idle needed real pause/resume, not just a state
  flip.** The dependent block's own scheduler process has to stop
  counting down its failure clock while forced Idle, and resume with the
  exact remaining time once `AllUp` fires — Modsim's real `Interrupt`/`ON
  INTERRUPT` mechanism (around `Interrupt(tempBlock,
  "Run")`). Added `Scheduler.interrupt`/`ProcessHandle` to `core/
  scheduler.py` to support this generically (lazy-deletion heap
  invalidation), used by `tests/unit/test_dependency.py`'s driver.

One more real behavior confirmed, not a bug: every run's final two
`"Block"`/`"Done"`/`"End_sim"` lines land at exactly `SimTime=100.000000`
for BOTH blocks, regardless of each block's own in-progress wait state at
that moment — a run-boundary force-termination step independent of each
block's own countdown, not each one's own "about to end" check landing
there by coincidence.

## Files

- `input.rbd` — the model.
- `legacy/rpt_blockdependency_events.txt` — event log, 5 runs, 9151 lines,
  704 `"Dependence"` occurrences.

## What this is for

Verifies `dependency.py` (`back_flow_dependency_trigger`,
`back_flow_dependencies`, `repair_completion_status`) against all 5 real
runs — see `tests/unit/test_dependency.py`. Node-level dependency
targets, mixed-percentage (`DepNothing`/`DepPM`/`DepFail`) outcomes, and
`NodeStandby` propagation remain out of scope (no fixture), and raise
`NotImplementedError` if hit.
