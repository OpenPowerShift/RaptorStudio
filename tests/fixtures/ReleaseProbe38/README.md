# ReleaseProbe38 — does the ESO grace window scale with `Stock`, or is it always exactly "the pool's first-ever exhaustion instant" regardless of pool size?

`ReleaseProbe33`'s own real capture SOLVED the ESO trigger rule for
`Stock=1` pools cleanly: the pool's first-ever exhaustion is silent for
EVERY block that discovers it at that SAME instant (however many), and
every subsequent DISTINCT instant the pool is still found empty places
exactly one order, regardless of how many blocks discover it then. But
implementing that rule literally (`ResourceQueue._eso_first_exhaustion_
time`/`_eso_last_order_time`, a pure per-instant flag with no `Stock`
dependence at all) regressed **15 already-real-matching tests**, every
one a `Stock=2`/`3` golden fixture (`Capacity1-3`, `Phasing1`,
`Phasing3`, `PmFailResetOff`, `PreventMxDefer`, `SpaceStation`,
`HierarchyExample`). The fix was reverted; the internal engine ledger
D.1 named the untested candidate directly: **"grace applies to the
first `Stock` exhaustions, not just the first instant."**

## Why `ReleaseProbe33`'s own data can't already answer this

Re-deriving `ReleaseProbe33`'s own real capture (`Stock=1`, 3 blocks,
`BlockB`/`BlockC` both discover emptiness at `t=1.0`, `BlockA`'s own
SECOND exhaustion at `t=4.0` is where the first real order lands) shows
it is ALSO fully consistent with a totally different candidate this
project's own docs never separately named: **"grace covers the first
`Stock+2` distinct instant-discoveries"** collapses, for `Stock=1`, to
the exact same prediction as "grace covers exactly the first distinct
instant" — the two hypotheses are numerically indistinguishable at
`Stock=1` because a 3-block probe can only ever produce 2 simultaneous
discoveries at the first instant, never enough to separate "per-instant
grace" from "grace scales with Stock." This probe is deliberately built
so the two predictions DIVERGE.

## What this probe does

Clone of `ReleaseProbe33`'s own already-crash-fixed `input.rbd`
(`start→{A,B,C}→end`, `Stock=1`) via `tools/rbd_builder.py`, extended
to `Stock=2` with a FOURTH parallel block:
- **`Cells` spare pool**: `Stock` `1→2` (all other pool fields —
  `ESO=1`, `EmerTime=5.0`, `ArriveEvery=720` — untouched, already
  proven safe).
- **`BlockD`**: added via `add_row(clone_id=2, ...)` — an EXACT clone
  of `BlockC`'s own row (same `FDist=19`/`FIXED(1.0)` fail,
  `RDist=19`/`FIXED(2.0)` repair, same `Cells` pool linkage). Two new
  links (`start→BlockD`, `BlockD→end`), `start.Out` `3→4` and
  `end.n` `3→4` (the SAME crash-class fix `ReleaseProbe30`/`33` already
  needed for their own added blocks). `validate()`: zero warnings or
  errors.

All 4 blocks (`A`/`B`/`C`/`D`) fail SIMULTANEOUSLY at `t=1.0`
(`FIXED(1.0)`). With `Stock=2`, the row-first TWO (`A`, `B`) get
immediate grants; `C` and `D` BOTH discover the pool empty at `t=1.0` —
exactly 2 simultaneous discoveries, matching `Stock` exactly. `A`/`B`
then repair (using their own already-granted spare, `FIXED(2.0)`) and
fail AGAIN, ALSO simultaneously, at `t=4.0` (`3.0` repair-complete +
`1.0` fail) — by then the pool is STILL at 0 (no order has replenished
it yet under either hypothesis being tested), so `A` and `B` become the
3rd and 4th blocks to ever discover the pool empty, at a SECOND
distinct instant.

## The question

Does real's own FIRST `"Spare_ordered"` line land at `t=1.0` (already
ruled out by `ReleaseProbe33`'s own general finding — not expected),
at `t=4.0`, or does the mission show NO order at all through its own
end (`TermCondition=300`, unchanged from `ReleaseProbe33`)?

- **Order fires at `t=4.0`** → confirms the CURRENTLY-DOCUMENTED rule
  (`ReleaseProbe33`'s own "per-instant grace, regardless of count")
  generalizes directly to `Stock=2` with NO stock-dependent widening —
  the grace window is always exactly "the pool's first-ever exhaustion
  instant," never more, regardless of `Stock` or how many blocks
  discover it then. This would mean the 15-fixture regression from the
  earlier fix attempt was caused by something ELSE (a bug in that
  specific implementation, not a wrong RULE) — worth re-examining the
  reverted diff with fresh eyes rather than assuming the rule itself
  needs Stock-awareness.
- **NO order anywhere in the mission** (system stays Red/deadlocked
  from `t=4.0` to `t=300.0`, all 4 blocks permanently `RepHold Waiting_
  for_Spare`) → confirms grace SCALES with `Stock` — the first `Stock`
  distinct-instant-discoveries (not just the first ONE) are silent,
  refuting `ReleaseProbe33`'s own "always exactly one instant" reading
  and directly explaining why that literal implementation regressed
  every `Stock=2`/`3` fixture (it stopped granting grace one instant
  too early for them). The correct rule needs a per-pool grace COUNTER
  gated on `Stock` (e.g. `Stock` distinct grace instants, or `Stock`
  cumulative discoveries — this probe's own data will only show
  "grace ≥ 2 instants for `Stock=2`," not the exact terminal instant,
  since the whole mission deadlocks the moment the SECOND instant is
  ALSO silent — a follow-up with `Stock=3` and enough blocks to reach a
  THIRD distinct instant would be the natural next step if this
  result comes back silent).
- **Order fires at some OTHER instant** (e.g. only ONE of `A`/`B`
  triggers it at `t=4.0`, not both simultaneously; or it fires between
  `t=1.0` and `t=4.0` from some mechanism neither hypothesis
  anticipated) → note the exact instant and which block(s) are
  involved precisely; would point to a third, more granular rule (e.g.
  counting per-BLOCK exhaustions rather than per-instant discoveries).

## Predicted (this port)

This port's own CURRENT (already-known-buggy, unconditional-spawn)
behavior — NOT a confident prediction, recorded for reference/diff
purposes only, matching `ReleaseProbe33`'s own established convention
for this class of probe:
```
0.000000   Block  BlockA  Good      InitializeStress=1.000000
0.000000   Block  BlockB  Good      InitializeStress=1.000000
0.000000   Block  BlockC  Good      InitializeStress=1.000000
0.000000   Block  BlockD  Good      InitializeStress=1.000000
1.000000   Block  BlockA  Failed    Operating_time=1.000000
1.000000   Block  BlockB  Failed    Operating_time=1.000000
1.000000   Block  BlockC  Failed    Operating_time=1.000000
1.000000   Block  BlockD  Failed    Operating_time=1.000000
1.000000   Block  BlockA  Repairing No_resources_used
1.000000   Block  BlockB  Repairing No_resources_used
1.000000   Block  BlockC  Sparing   No_spare_in_stock-Cells
1.000000   Pool   Cells   Sparing   Spare_ordered_(stock=0)   <- order #1 (for BlockC)
1.000000   Block  BlockC  RepHold   Waiting_for_Spare
1.000000   Block  BlockD  Sparing   No_spare_in_stock-Cells
1.000000   Pool   Cells   Sparing   Spare_ordered_(stock=0)   <- order #2 (for BlockD), CONCURRENT
1.000000   Block  BlockD  RepHold   Waiting_for_Spare
3.000000   Block  BlockA  Repaired  MaintTime=2.000000
3.000000   Block  BlockA  Good      Stress=1.000000
3.000000   Block  BlockB  Repaired  MaintTime=2.000000
3.000000   Block  BlockB  Good      Stress=1.000000
4.000000   Block  BlockA  Failed    Operating_time=1.000000
4.000000   Block  BlockB  Failed    Operating_time=1.000000
4.000000   Block  BlockA  Sparing   No_spare_in_stock-Cells
4.000000   Pool   Cells   Sparing   Spare_ordered_(stock=0)   <- order #3 (for BlockA)
4.000000   Block  BlockA  RepHold   Waiting_for_Spare
4.000000   Block  BlockB  Sparing   No_spare_in_stock-Cells
4.000000   Pool   Cells   Sparing   Spare_ordered_(stock=0)   <- order #4 (for BlockB), CONCURRENT
4.000000   Block  BlockB  RepHold   Waiting_for_Spare
6.000000   Pool   Cells   Sparing   Ordered_spare_arrives_(stock=0)  <- orders #1/#2 arrive (both t=1.0+5.0)
6.000000   Pool   Cells   Sparing   Ordered_spare_arrives_(stock=0)
6.000000   Block  BlockC  Sparing   Spare_Arrived
6.000000   Block  BlockC  Repairing No_resources_used
6.000000   Block  BlockD  Sparing   Spare_Arrived
6.000000   Block  BlockD  Repairing No_resources_used
8.000000   Block  BlockC  Repaired  MaintTime=2.000000
8.000000   Block  BlockC  Good      Stress=1.000000
8.000000   Block  BlockD  Repaired  MaintTime=2.000000
8.000000   Block  BlockD  Good      Stress=1.000000
9.000000   Pool   Cells   Sparing   Ordered_spare_arrives_(stock=0)  <- orders #3/#4 arrive
9.000000   Pool   Cells   Sparing   Ordered_spare_arrives_(stock=0)
9.000000   Block  BlockC  Failed    Operating_time=1.000000
9.000000   Block  BlockD  Failed    Operating_time=1.000000
9.000000   Block  BlockA  Sparing   Spare_Arrived
9.000000   Block  BlockA  Repairing No_resources_used
9.000000   Block  BlockB  Sparing   Spare_Arrived
9.000000   Block  BlockB  Repairing No_resources_used
```
This port places FOUR separate orders (`t=1.0` twice, `t=4.0` twice) —
the same unconditional-spawn bug `ReleaseProbe30`/`33` already found,
now against a `Stock=2` backlog. All blocks are `FIXED` — no RNG draws,
purely an event-order/timing question, same as `ReleaseProbe33`.

## How to capture it

Same procedure as the other `ReleaseProbeN` fixtures — launch Raptor7
Student with `/sd`, open this file, Simulate (3 runs — the cycle would
repeat identically if it repeats at all). Read the event log for
`BlockA`/`BlockB`/`BlockC`/`BlockD`/`Cells` across the WHOLE run
(`TermCondition=300.0`) — the critical question is whether ANYTHING
happens after `t=4.0` at all, so don't stop reading early even if it
looks like the run has gone quiet. Real-Raptor report outputs land in
`~/Documents/Raptor7/` (`rpt_events_*` etc.); the `/sd`
copy goes to this repo's root as `simDetails_ReleaseProbe38.txt`
(expect it empty/near-empty, same as `ReleaseProbe30`/`33` — all blocks
are `FIXED`, no RNG draws involved).

## REAL ANSWER (2026-09-05 capture, `~/Documents/
Raptor7/rpt_events_ReleaseProbe38.txt`)

**Order fires at `t=4.0` — but with a twist neither candidate predicted
cleanly: TWO separate, genuinely concurrent orders, one per
discoverer.** At `t=1.0`, `BlockC`+`BlockD` both discover the pool
empty — **silent, no order**, exactly as predicted (confirms grace
covers the pool's first-ever exhaustion regardless of `Stock`). At
`t=4.0`, `BlockA`+`BlockB` both discover the pool empty — real shows
**two separate `"Spare_ordered"` lines**, one right after each block's
own `"No_spare_in_stock"` line:
```
4.000000  Block  BlockA  Sparing  No_spare_in_stock-Cells
4.000000  Block  BlockB  Sparing  No_spare_in_stock-Cells
4.000000  Pool   Cells   Sparing  Spare_ordered_(stock=0)   <- order #1, for BlockA
4.000000  Block  BlockA  RepHold  Waiting_for_Spare
4.000000  Block  BlockB  RepHold  Waiting_for_Spare
4.000000  Pool   Cells   Sparing  Spare_ordered_(stock=0)   <- order #2, for BlockB
```
This pattern (silent first instant; every LATER discovery — even
multiple simultaneous ones at the same instant — places its own order,
independently) repeats identically at every 8-unit cycle for the
WHOLE 300-unit mission (152 total `"Spare_ordered"` lines across 3
runs, always in pairs from `t=4` onward) — confirmed, not a one-off.

**This refines D.1's own rule precisely**: grace covers ONLY the
pool's first-ever exhaustion INSTANT (any number of simultaneous
discoverers there are all silent) — there is NO further suppression
after that, ever, regardless of `Stock` or how many blocks discover
emptiness together at a later instant. This also retroactively
explains `ReleaseProbe30`'s own "real never shows two concurrent
orders" reading: that fixture's 2-block ALTERNATING cycle never
produced two SIMULTANEOUS new discoverers at any post-grace instant
(only staggered, one-at-a-time ones) — it was never actually tested,
not confirmed absent.

**A fix implementing exactly this rule (`ResourceQueue.eso_first_
exhaustion_time`, suppress only when `sim_time == that stored value`,
otherwise unconditional per-block ordering) was implemented and
matched `ReleaseProbe30`/`33`/`38` all three exactly — but REGRESSED
16 tests on the full suite** (`Capacity1-3`, `HierarchyExample`,
`SpaceStation`, `PreventMxDemo`, plus the `sd_diff` sweep baselines for
`Phasing1`/`Phasing3`/`PmFailResetOff`/`PreventMxDefer`). Direct
investigation of `Capacity1`'s own real capture found the falsifying
counter-example: `Bus_QR`'s own `Electronics` pool exhaustion at
`t=661.068651` is genuinely that pool's FIRST-EVER exhaustion in the
whole run (confirmed — no earlier `"Electronics"` line anywhere in run
1) — and **real orders on it IMMEDIATELY, no grace at all**:
```
661.068651  Block  Bus_QR       Sparing  No_spare_in_stock-Electronics
661.068651  Pool   Electronics  Sparing  Spare_ordered_(stock=0)
```
**This directly refutes "the pool's first-ever exhaustion is always
free" as a general `SPARE_POOL_DATA` rule.** It holds for `ReleaseProbe
30`/`33`/`38`'s own `Cells` pool (`Stock∈{1,2}`, `EmerTime=5.0`, every
exhaustion clustered right at kickoff) but NOT for `Capacity1`'s own
`Electronics` pool (`Stock=4`, `EmerTime=24.0`, exhaustion at
`t=661`, deep into the mission, an isolated solo discovery like
`ReleaseProbe30`'s own first exhaustion — so it isn't a simultaneous-
vs-solo distinction either, since `ReleaseProbe30`'s own solo first
exhaustion WAS silent). Fix reverted cleanly (`git diff` confirms zero
change to `block_lifecycle.py`/`pool.py`) — third fix attempt for
this item, third falsification on broader real data. **D.1 remains
narrowed but NOT solved.**

**What this leaves for the next attempt**: the TRUE discriminator
between "gets a free first-exhaustion pass" (`ReleaseProbe30`/`33`/
`38`) and "orders immediately even on the very first exhaustion"
(`Capacity1`'s `Electronics`) is still unidentified. Candidates not
yet tested: `Stock` size (1-2 vs 4, but this needs a probe that varies
ONLY `Stock` with everything else — `EmerTime`, mission timing — held
fixed to isolate it); `EmerTime` itself (5.0 vs 24.0); whether the
exhaustion happens near mission kickoff vs deep into a long mission;
or some property of the REQUESTING block/topology not yet considered
(number of DISTINCT blocks ever sharing the pool, `RSO`/`SLO`
co-configuration — though `Electronics` has `RSO=0`/`SLO=0`, matching
`ReleaseProbe30`/`33`/`38` exactly, so that's not it). A probe that
clones `Capacity1`'s own `Electronics` pool config exactly (`Stock=4`,
`EmerTime=24.0`) but forces its FIRST exhaustion to happen EARLY
(kickoff-adjacent, `Cells`-probe-style) would directly test the
"early vs late timing" hypothesis without changing `Stock`/`EmerTime`
at all — the cleanest next isolation.

## Follow-up, same day — a FOURTH fix attempt, then a self-caught
investigation error

Reframed as "grace on the DISCOVERING BLOCK's own first-ever failure"
(`block.failure_counter == 1`), gated on the pool having NO RSO/SLO
(ESO its sole mechanism). This matched `ReleaseProbe30`/`33`/`38`
exactly AND fixed `HierarchyExample`'s own regression (`BatteriesNaS`,
whose pool has `RSO=1`/`SLO=1` unlike this probe's own `Cells`). But
the full suite still showed `Capacity1` diverging — `Bus_UR`'s own
`t=683.967284` failure on the `Electronics` pool (`RSO=0`/`SLO=0`,
ESO-only, matching the "should get grace" shape) orders IMMEDIATELY in
real, contradicting the hypothesis.

Investigating this found the root cause was in THIS investigation's
OWN method, not the port: a run-boundary miscount. `Bus_UR`'s apparent
"earlier failure at `t=13.9`" (which made `t=683.97` look like its
SECOND failure, exempting it from the grace-if-first-failure rule) was
actually from a DIFFERENT physical run (`Begin_Run_1`) than the one
`t=683.97` itself falls inside (`Begin_Run_2`). Re-counted correctly
within `Begin_Run_2` alone, `t=683.967284` is genuinely `Bus_UR`'s own
first-ever failure — and real orders on it immediately, refuting the
per-block-first-failure hypothesis (RSO/SLO gate and all). Reverted
cleanly a second time.

**The pattern surviving all 5 correctly-counted data points**: every
silent case (`ReleaseProbe30`/`33`/`38`) is BOTH kickoff-adjacent
(`t=1.0`) AND the system's own first-ever failure; every ordering case
(`Capacity1`'s `Bus_QR`/`Bus_UR`, `HierarchyExample`'s `BatteriesNaS`)
is deep into its mission AND not the system's first failure. These two
readings — kickoff-time special case, vs. "system's first failure is
always free regardless of when" — are still numerically
indistinguishable, since a system's first-ever failure has only ever
been observed at kickoff. `ReleaseProbe39` (committed, awaiting real
capture) breaks that coincidence directly: the SAME 3-way simultaneous
first-ever failure this probe already tests, retimed from `t=1.0` to
`t=500.0`. See `tests/fixtures/ReleaseProbe39/README.md`.
