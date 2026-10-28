# ReleaseProbe33 — does real's emergency-order trigger fire once ANY block hits its OWN second exhaustion, or once the queue BACKLOG reaches a specific depth?

`ReleaseProbe30`'s own real capture found a genuine, precisely
characterized bug: this port's `block_lifecycle.py` (`resources_was_
zero` branch, ~line 3047-3058) spawns a NEW emergency spare order every
time ANY block finds the pool empty, with no check for an order
already outstanding. Real places only ONE order at a time — but
`ReleaseProbe30`'s own 2-block capture can't distinguish WHY: its own
repeating cycle always has the shape "first exhaustion after the pool
empties is silent; the SAME block's own second exhaustion (while
already backlogged) triggers the order" — which is consistent with
(at least) two different candidate rules that a 2-block backlog can
never tell apart:

- **Rule A ("own second exhaustion")**: an order fires the first time
  ANY SPECIFIC block hits its OWN second consecutive exhaustion while
  the pool is still empty from an earlier spell — regardless of how
  many OTHER blocks are already queued at that moment.
- **Rule B ("backlog depth")**: an order fires once the QUEUE reaches
  some threshold depth (e.g. 2 blocks waiting), regardless of whether
  it's the SAME block repeating or a DIFFERENT block joining the queue.

In `ReleaseProbe30`'s own 2-block cycle, `BlockA`'s own second
exhaustion (`t=4.0`) happens to ALSO be the moment the backlog reaches
depth 2 (`BlockB` already queued since `t=1.0`, `BlockA` now joining)
— so `ReleaseProbe30`'s own capture is consistent with BOTH rules
simultaneously and can't distinguish them.

## What's new here vs `ReleaseProbe30`

**Adds a third block.** Cloned `ReleaseProbe30`'s own `input.rbd`
directly via `rbd_builder.py` (`add_row(..., clone_id=3, ...)` — same
`FIXED(1.0)` fail / `FIXED(2.0)` repair shape as `BlockA`/`BlockB`),
added `BlockC` as a third parallel branch (`start→BlockC→end`, mirroring
`BlockA`'s own link shape), bumped `end` node's own `n` (`2→3`, matching
the SAME crash-class fix `ReleaseProbe30`'s own construction needed for
its own second block). `validate()`: only the pre-existing harmless
`start`-node `Out` mismatch — no new warnings or errors.

`BlockA`/`BlockB`/`BlockC` all fail SIMULTANEOUSLY at `t=1.0` (all
`FIXED(1.0)`) — `Stock=1`, so only `BlockA` (row-first) gets the
immediate grant; `BlockB` and `BlockC` BOTH queue at `t=1.0`, bringing
the backlog straight to depth 2 immediately — WITHOUT needing `BlockA`
to fail a second time first. This is the key structural difference
from `ReleaseProbe30`'s own cycle: here, "backlog reaches depth 2" and
"a block's own second exhaustion" are DECOUPLED into two separate
instants (`t=1.0` for the former, `t=4.0` — `BlockA`'s own next
failure, after its `t=1→3` repair — for the latter), so a real capture
can tell Rule A from Rule B directly.

## The question

Does real's own FIRST emergency order of the mission fire at `t=1.0`
(when `BlockC` becomes the SECOND block to queue, backlog depth 2 —
confirms Rule B) or wait until `t=4.0` (`BlockA`'s own second
exhaustion, still the only block to have failed twice — confirms Rule
A, matching `ReleaseProbe30`'s own reading exactly)?

- **Order fires at `t=1.0`** → Rule B (backlog-depth threshold, ≥2)
  — `ReleaseProbe30`'s own "own second exhaustion" reading was a
  coincidence of its own 2-block shape, not the true rule. The fix
  should gate on `len(pool._queue) >= threshold` (need to confirm the
  exact threshold — possibly depth 2, possibly something else this
  probe's own 3-block depth still can't fully pin down if it turns out
  to be ≥3).
- **Order fires at `t=4.0`** → Rule A confirmed independent of backlog
  depth — matches `ReleaseProbe30`'s own reading exactly, and rules out
  ANY simple depth-threshold explanation. The correct fix gates on
  something block-specific (e.g. "has this exact block already been
  granted-then-re-exhausted the pool since it last emptied," closer to
  `num_emer_ordered`'s own existing semantics but checked BEFORE
  incrementing, not after).
- **Some other pattern** (e.g. order fires at neither instant, or
  fires more than once by `t=4.0`) → note the full real event log
  across `t=1.0-9.0` precisely; would point to a third mechanism
  neither candidate anticipated.

## Predicted (this port)

This port's own CURRENT (already known-buggy, unconditional-spawn)
behavior — NOT a confident prediction of what real does, since the
whole point of this probe is that this port's own model is wrong here.
Recorded for reference/comparison only:
```
0.000000   Block  BlockA  Good        InitializeStress=1.000000
0.000000   Block  BlockB  Good        InitializeStress=1.000000
0.000000   Block  BlockC  Good        InitializeStress=1.000000
1.000000   Block  BlockA  Failed      Operating_time=1.000000
1.000000   Block  BlockB  Failed      Operating_time=1.000000
1.000000   Block  BlockC  Failed      Operating_time=1.000000
1.000000   Block  BlockA  Repairing   No_resources_used
1.000000   Block  BlockB  Sparing     No_spare_in_stock-Cells
1.000000   Pool   Cells   Sparing     Spare_ordered_(stock=0)   <- order #1 (for BlockB)
1.000000   Block  BlockB  RepHold     Waiting_for_Spare
1.000000   Block  BlockC  Sparing     No_spare_in_stock-Cells
1.000000   Pool   Cells   Sparing     Spare_ordered_(stock=0)   <- order #2 (for BlockC), CONCURRENT with #1
1.000000   Block  BlockC  RepHold     Waiting_for_Spare
3.000000   Block  BlockA  Repaired    MaintTime=2.000000
3.000000   Block  BlockA  Good        Stress=1.000000
4.000000   Block  BlockA  Failed      Operating_time=1.000000
4.000000   Block  BlockA  Sparing     No_spare_in_stock-Cells
4.000000   Pool   Cells   Sparing     Spare_ordered_(stock=0)   <- order #3 (for BlockA), a THIRD concurrent order
4.000000   Block  BlockA  RepHold     Waiting_for_Spare
6.000000   Pool   Cells   Sparing     Ordered_spare_arrives_(stock=0)  <- orders #1 and #2 both arrive together (both placed at t=1.0)
6.000000   Pool   Cells   Sparing     Ordered_spare_arrives_(stock=0)
6.000000   Block  BlockB  Sparing     Spare_Arrived
6.000000   Block  BlockB  Repairing   No_resources_used
6.000000   Block  BlockC  Sparing     Spare_Arrived
6.000000   Block  BlockC  Repairing   No_resources_used
8.000000   Block  BlockB  Repaired    MaintTime=2.000000
8.000000   Block  BlockB  Good        Stress=1.000000
8.000000   Block  BlockC  Repaired    MaintTime=2.000000
8.000000   Block  BlockC  Good        Stress=1.000000
9.000000   Pool   Cells   Sparing     Ordered_spare_arrives_(stock=0)  <- order #3 arrives
9.000000   Block  BlockA  Sparing     Spare_Arrived
9.000000   Block  BlockA  Repairing   No_resources_used
9.000000   Block  BlockB  Failed      Operating_time=1.000000
9.000000   Block  BlockC  Failed      Operating_time=1.000000
```
This port places THREE separate orders (`t=1.0` twice, `t=4.0` once) —
confirming the same unconditional-spawn bug `ReleaseProbe30` already
found, now against a 3-block backlog. No RNG draws at all (all blocks
`FIXED`) — purely an event-order/timing question.

## How to capture it

Same procedure as the other `ReleaseProbeN` fixtures — launch Raptor7
Student with `/sd`, open this file, Simulate (3 runs — the cycle
repeats identically, so even a short mission yields many confirming
cycles), read the event log for `BlockA`/`BlockB`/`BlockC`/`Cells`
across the whole run, paying specific attention to whether an order
appears at `t=1.0` or only at `t=4.0`. Real-Raptor report outputs land
in `~/Documents/Raptor7/` (`rpt_events_*` etc.); the
`/sd` copy goes to this repo's root as `simDetails_ReleaseProbe33.txt`
(expect it empty/near-empty, same as `ReleaseProbe30` — all blocks are
`FIXED`, no RNG draws involved).

## Crash fix (2026-09-04) — `start` node's own `Out` was UNDER-declared

User reported: opens fine, crashes on SIMULATE. Root cause: `start`'s
own `NODE_DATA` row declared `Out=2` (an unrelated, uniformly-wrong
template default this whole project's fixtures inherit — every OTHER
`start` node in this repo declares `Out=2` too, regardless of its own
real link count) while THIS fixture's own `add_row` for `BlockC` gave
`start` a THIRD real outgoing link, making the true count 3. This is
the SAME Access-Violation crash class as a node `n` (incoming path
count) mismatch — just on a source node's OUTGOING side instead of a
sink node's incoming side: an internal array sized to the DECLARED
count gets written past its bound. `rbd_builder.py`'s own `validate()`
previously treated ANY `Out` mismatch as a harmless WARN (correct for
every prior fixture, which all happen to be OVER-declared — `Out=2`,
1 real link — a direction that never overflows anything); this is the
FIRST fixture in the project to ever go the other way (UNDER-declared,
2 declared, 3 real). Fixed both the fixture (`Out: 2→3`, matching the
real 3 outgoing links, the same fix `end`'s own `n: 2→3` already
needed) and the tooling itself (`validate()` now makes UNDER-declared
`Out` a hard `ERROR`, keeping OVER-declared as the existing harmless
`WARN`). Re-running `validate()` against every fixture in the repo
confirms only two OTHER, already-suspect files hit the new `ERROR`:
`ReleaseProbe4` (pre-existing corruption, never real-captured — see
its own note in persistent memory) and `CS_DepOnColdBlock` (no README,
never real-captured either) — no previously-proven-good fixture
regresses.

## REAL ANSWER (2026-09-04 capture, `~/Documents/Raptor7/rpt_events_ReleaseProbe33.txt`)

**Decisive — Rule B (backlog depth) is REFUTED, Rule A confirmed, in a
cleaner form than either candidate.** At `t=1.0`, `BlockB` and `BlockC`
BOTH discover the pool empty (backlog reaches depth 2 immediately) —
**no order at `t=1.0` at all.** The first (and, in that instant, only)
`"Spare_ordered"` line is at `t=4.0`, exactly `BlockA`'s own later,
separate re-encounter:
```
1.000000  Block  BlockB  RepHold  Waiting_for_Spare   <- backlog depth 2 reached HERE, no order
1.000000  Block  BlockC  RepHold  Waiting_for_Spare
4.000000  Block  BlockA  Sparing  No_spare_in_stock-Cells
4.000000  Pool   Cells   Sparing  Spare_ordered_(stock=0)   <- first order, NOT at t=1.0
```
**True rule (refined from Rule A)**: the pool's OWN first-ever
exhaustion is silent for EVERY block that discovers it at that SAME
instant (not just the first one) — it's a per-INSTANT grace, not a
per-block "own second exhaustion" counter. Every later DISTINCT
instant the pool is still found empty places EXACTLY ONE order,
regardless of how many blocks discover it then. Confirmed repeating
identically every 8 units for the rest of the capture (`t=4,12,20,28,
36,...`), one order each time.

**Attempted a fix, REVERTED — the rule above only holds for `Stock=1`
pools.** Implemented the "first-instant-ever silent, every later
distinct instant orders once" rule directly (`ResourceQueue` gained
`_eso_first_exhaustion_time`/`_eso_last_order_time`; `block_lifecycle.
py`'s `resources_was_zero` branch consulted them). Matched `Release
Probe30`/`33` EXACTLY. **But the full test suite (780 tests) showed 15
regressions** — every golden fixture with a multi-unit spare pool
(`Stock=2` or `3`: `Capacity1-3`, `Phasing1`, `Phasing3`,
`PmFailResetOff`, `PreventMxDefer`, `SpaceStation`, `HierarchyExample`)
diverged from its own previously-EXACT real-matching baseline. Cleanly
reverted (`git diff` confirms zero change to either file) rather than
ship a fix that trades a probe-scoped confirmation for known real
regressions — matches this project's own "measure blast radius with
the full sweep before trusting a scheduler/PM-adjacent fix" discipline.

**Conclusion: the ESO trigger condition is narrowed but NOT solved.**
The "first-ever-instant grace, then one order per later distinct
instant" rule is CONFIRMED correct specifically for `Stock=1` pools
(both `ReleaseProbe30` and this fixture use `Stock=1`) — every
regressed fixture uses `Stock=2` or `3`, so the TRUE rule must depend
on stock depth in some way this probe's own construction can't
isolate (e.g. "grace applies to the first `Stock` exhaustions,
not just the first instant" is one candidate, untested). This port's
own ORIGINAL unconditional-per-block-order bug is left in place
(NOT fixed) pending a follow-up probe built with `Stock=2` specifically
to test that candidate. The internal engine ledger D.1 updated to
reflect the narrowed-but-not-solved status.

## SOLVED, 2026-09-05 (`ReleaseProbe38`/`39`'s own real captures)

The "grace applies to the first `Stock` exhaustions" candidate above
was ALSO falsified (`ReleaseProbe38`, `Stock=2`: the pool's SECOND
distinct exhaustion instant already orders, twice concurrently, no
further grace). The true rule turned out to depend on neither `Stock`
nor the pool at all: grace applies ONCE per run, to whichever
failure(s) constitute the SYSTEM's own very first-ever failure —
`BlockB`/`BlockC`'s own simultaneous `t=1.0` failure here IS that
system-first failure (this fixture's own three blocks are the whole
model), so both are silent; `BlockA`'s later `t=4.0` failure orders
because by then the system has already failed once before. Confirmed
independent of `Stock`/`RSO`/`SLO`/absolute sim-time via `ReleaseProbe
38`/`39` and the real `Capacity1`/`HierarchyExample` counter-examples
(whose OWN first-ever-pool-exhaustion failures order immediately
because they are NOT that run's system-first failure). Implemented via
`SystemState.first_failure_time` + a check in `block_lifecycle.py`'s
ESO branch. Full trail: the internal engine ledger D.1, `legacy/rbd/
test_fixtures/ReleaseProbe39/README.md`.
