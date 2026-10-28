# Fixture: ResourcePool — hand-built, verifies `RESOURCE_DATA` pool contention

Built from `tests/golden/TwoBlockRedundancy/input.rbd` (2 parallel
`Infinite`-sparing blocks feeding a 1-of-2 `join` -> `end`, `Uniform(0,1)`
fail AND repair — very frequent cycles) via `raptor.core.fileio.rbd`'s
load/edit/save API: both real blocks (`ID=3,4`) given `NumRes=1`,
`Resource1Name=Techs`, `#Res1Rep=1`; a new `RESOURCE_DATA` row added,
`Techs` pool with `NumIn=1` (deliberately smaller than the 2 blocks that
share it, so contention happens constantly), `NumRuns=5` reduced from
the original 20 (`Uniform(0,1)` cycles are short — 5 runs of
`TermCondition=100.0` already produces ~1700+ event lines each).

## Status: SOLVED 2026-08-15 — all 5 runs bit-exact

`legacy/rpt_events_resourcepool.txt` — real capture, 5 runs, 8679 lines.
All 5 runs match exactly (fp32 noise aside): 8435 matching / 222
fp32-noise-only / 0 structural mismatches — the first real verification
of `RESOURCE_DATA`/`SparingType.RESOURCE` (`ResourceQueue`, `raptor/
core/engine/pool.py`), the first piece of the larger "Pooled sparing"
undertaking, deliberately built and verified
BEFORE attempting `SpType=Pooled` spare-pool consumption (a separate,
harder mechanism with RSO/SLO/ESO replenishment), since `SpaceStation
.rbd` needs both together and can't isolate either.

**First capture attempt crashed the real Raptor7Student.exe on load** —
unrelated to the simulation logic: `render_rbd` wrote the header's
`ResPools=` count from a stale copy of the ORIGINAL file's count (0),
not the actual `RESOURCE_DATA` row count (1) after this script added a
pool. Fixed at the source (`render_rbd` now derives these counts from
each section's real row count) so this bug class can't recur.

**Getting the real capture to actually match took two more real fixes**,
both about ORDERING (every VALUE was already correct on the first real
run — the divergence was 100% about which line appears in what order):

1. `TakeBack` (`resource_pool.release()`) happens BEFORE the "Repaired"
   line is logged in the source — an earlier
   version of this code had it after, which didn't matter for the
   VALUES but did for the eventual ordering once combined with fix 2.

2. The bigger one: a resumed (previously-waiting) block's own lines
   (`"Sparing"/"Obtained_N_Resource(s)"`, `"Repairing"`) must appear
   AFTER the releasing block's own transition-and-cascade lines but
   BEFORE the system-status check that follows — a three-way ordering
   no single function owns outright. Solved by (a) `Scheduler.interrupt`
   resuming a `wait<=0.0` response synchronously instead of deferring it
   to the calendar (needed for the woken block to actually run through
   its `WAIT DURATION 0.0`, to its own next
   real wait), and (b) threading `wake_ids` through `block_run_process`'s
   `on_transition` callback so `orchestrator._Cascade.apply` can call
   that interrupt from the ONE correct point in its own pipeline: after
   its node cascade, before its system-status check. See `Scheduler.
   interrupt`'s and `_Cascade.apply`'s own docstrings for the full
   reasoning — two earlier attempts (synchronous-chaining alone; reorder-
   ing the release call alone) each got the order wrong in a different
   way before combining both pieces correctly.

## Files

- `input.rbd` — hand-built, see above.
- `legacy/rpt_events_resourcepool.txt` — real capture, 5 runs, 8679 lines.

## What this is for

A passing regression fixture in `test_orchestrator.py`. The real proof
that `ResourceQueue`'s FIFO give/take-back mechanics — including the
tricky cross-process ordering when a release resumes a waiter — are
correct end-to-end. Next: `SpType=Pooled` spare-pool consumption itself.
