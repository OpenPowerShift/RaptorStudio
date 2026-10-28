# ReleaseProbe30 — does a `SPARE_POOL_DATA` pool's own `Give()`/queue/wake behavior match the already-confirmed `RESOURCE_DATA` model exactly?

The internal engine ledger D.1: `WAIT FOR PooledSpares[x] TO Give
(...)` is a MODSIM II RUNTIME BUILT-IN with **no method-body definition
anywhere in Raptor 7** — queue position, hop behavior, and
whether a grant's own wake round-trips through `Interrupt(SELF,"Run")`
are not recoverable from Raptor 7's source at all, only from real captures.
This port's own `ResourceQueue` (`raptor/core/engine/pool.py`) uses the
SAME strict-FIFO, 0-duration-hop model for BOTH `RESOURCE_DATA` pools
(fixed capacity, borrow-then-release) AND `SPARE_POOL_DATA` pools
(Give-only, never released by the block) — but only the FORMER has been
extensively real-verified this session (`ReleaseProbe19`-`24`, D.3's
own resolution the internal investigation log §64-65, the resource-pool-
leak and stale-PM-request fixes §68-69). The SPARE-pool-specific
question — does `Give()`'s own queue/wake model match `RESOURCE_DATA`'s
own confirmed behavior, or differ — has never been directly tested.

## What this probe builds

Two-block clone of `tests/fixtures/SLOTest2/input.rbd` (a real,
already-structurally-simple single-block spare-pool fixture — chosen
specifically as a SAFE base after this session's own two real-Raptor
crashes on other probes; every field left untouched here except the
ones below):

1. **`BlockA`/`BlockB`**: `SLOTest2`'s own single block (`unnamed`,
   `SpareType=Pooled`, pool `Cells`), cloned into two, both renamed and
   both given `FDist=19 (FIXED)`, `FParam1=1.0` — fully deterministic,
   both fail at the EXACT same instant every cycle (`SLOTest2`'s own
   original `EXPONENTIAL(0.1)` mean was far too fast/random for a
   controlled tie test). Repair distribution (`FIXED(2.0)`) left
   unchanged from the template.
2. **`Cells` pool** (`SPARE_POOL_DATA`): `Stock: 2→1` (forces
   contention — with 2 initial spares, both blocks would get an
   immediate grant and there'd be nothing to queue), `ESO: 0→1`,
   `EmerTime: 24.0→5.0` (enables emergency spare ordering with a SHORT,
   deterministic lead time — small changes from the template's own
   already-real-adjacent defaults, not extreme values), `SLO: 1→0`,
   `Level: 1→0` (disabled, to avoid a SECOND replenishment mechanism
   confounding the read — `SLOTest2`'s own real capture already
   exercises `SLO` specifically, so this probe deliberately isolates
   `ESO` instead).
3. Wired `BlockB` as a new parallel branch (`start→BlockB→end`,
   mirroring `BlockA`'s own existing link shape) — `NODE_DATA`'s `end`
   node's own `n` bumped `1→2` (`validate()` caught this as a genuine
   crash-class error before it was fixed — a real Access Violation
   class this session already hit twice with other probes).

`validate()`: zero warnings or errors.

## This port's own prediction

Both `BlockA`/`BlockB` fail simultaneously at `t=1.0` every cycle
(`FIXED(1.0)` from each `Good` state). This port's own run shows a
clean, deterministic, REPEATING pattern across the whole mission (event
log excerpt, run 1, first cycle):
```
0.000000   Block  BlockA  Good        InitializeStress=1.000000
0.000000   Block  BlockB  Good        InitializeStress=1.000000
1.000000   Block  BlockA  Failed      Operating_time=1.000000
1.000000   Block  BlockB  Failed      Operating_time=1.000000
1.000000   Block  BlockA  Repairing   No_resources_used        <- row-first WINS the immediate grant
1.000000   Block  BlockB  Sparing     No_spare_in_stock-Cells
1.000000   Pool   Cells   Sparing     Spare_ordered_(stock=0)  <- BlockB triggers an emergency order
1.000000   Block  BlockB  RepHold     Waiting_for_Spare
3.000000   Block  BlockA  Repaired    MaintTime=2.000000
3.000000   Block  BlockA  Good        Stress=1.000000
4.000000   Block  BlockA  Failed      Operating_time=1.000000
4.000000   Block  BlockA  Sparing     No_spare_in_stock-Cells
4.000000   Pool   Cells   Sparing     Spare_ordered_(stock=0)  <- a SECOND emergency order, concurrent with the first
4.000000   Block  BlockA  RepHold     Waiting_for_Spare
6.000000   Pool   Cells   Sparing     Ordered_spare_arrives_(stock=0)
6.000000   Block  BlockB  Sparing     Spare_Arrived            <- FIFO: BlockB (queued FIRST, at t=1) gets the FIRST arriving spare
6.000000   Block  BlockB  Repairing   No_resources_used
8.000000   Block  BlockB  Repaired    MaintTime=2.000000
8.000000   Block  BlockB  Good        Stress=1.000000
9.000000   Pool   Cells   Sparing     Ordered_spare_arrives_(stock=0)
9.000000   Block  BlockA  Sparing     Spare_Arrived            <- the SECOND order (from t=4) arrives, grants BlockA (the only one still queued)
9.000000   Block  BlockA  Repairing   No_resources_used
9.000000   Block  BlockB  Failed      Operating_time=1.000000  <- BlockB's own next cycle starts right as this resolves
```
This cycle then repeats with the SAME shape (row-order wins the tie,
FIFO grants the delayed replenishments in queue order, TWO concurrent
emergency orders can be in flight at once) for the rest of the mission.
No RNG draws at all for either block (both `FIXED`) — this is purely an
event-ORDER/TIMING question, not a value question.

## The question

Three independent, complementary readings from this ONE deterministic,
repeating cycle:

1. **The `t=1.0` tie**: does real ALSO grant `BlockA` (row/ID-first) the
   pool's only immediate spare, leaving `BlockB` to queue? (Matches
   `ReleaseProbe19`/`20`-style row-order questions, but for `Give()`
   instead of ordinary `RESOURCE_DATA` release — if real REVERSES this,
   `Give()`'s own tie-break differs from the already-confirmed
   `RESOURCE_DATA` model.)
2. **FIFO across the two concurrent emergency orders**: does real grant
   the `t=6.0` arrival to `BlockB` (queued first, at `t=1.0`) rather
   than `BlockA` (queued second, at `t=4.0`) — confirming strict FIFO
   holds for spare-pool queueing too, not just ordinary resource pools?
3. **The wake's own timing**: does the grant land in the SAME instant
   the emergency spare "arrives" (`t=6.0`/`t=9.0`, this port's own
   0-duration-hop model), or does real insert a real scheduler
   round-trip (a later instant) between the spare arriving and the
   waiting block's own wake?

## How to capture it

Same procedure as the other `ReleaseProbeN` fixtures — launch Raptor7
Student with `/sd`, open this file, Simulate (3 runs — the cycle
repeats identically every ~9 units, so even a short mission yields many
confirming cycles), read the event log for `BlockA`/`BlockB`/`Cells`
across the whole run. Real-Raptor report outputs land in
`~/Documents/Raptor7/` (`rpt_events_*` etc.); the `/sd`
copy goes to this repo's root as `simDetails_ReleaseProbe30.txt` (though
since both blocks are `FIXED`, expect it to be empty or near-empty —
not a capture failure, just confirms no RNG draws are involved).

## REAL ANSWER (2026-09-04 capture, `simDetails_ReleaseProbe30.txt` + `~/Documents/Raptor7/rpt_events_ReleaseProbe30.txt`)

**Two of three questions confirmed exactly as predicted; the third
reveals a real, precisely-characterized divergence.**

1. **The `t=1.0` tie**: CONFIRMED — real grants `BlockA` (row-first) the
   pool's only immediate spare; `BlockB` queues. Matches this port's own
   row-order model exactly.
2. **FIFO ordering**: CONFIRMED, though not via the SAME mechanism this
   port predicted (see divergence below) — whoever queues earliest is
   served first: `BlockB` (queued `t=1.0`) is served by the FIRST
   emergency order (arrives `t=9.0`); `BlockA` (queued `t=4.0`) is
   served by the SECOND (arrives `t=17.0`).
3. **Wake timing**: CONFIRMED — the grant lands in the SAME instant the
   emergency spare "arrives" (`"Ordered_spare_arrives"` and
   `"Spare_Arrived"` on the identical timestamp), matching this port's
   own 0-duration-hop model exactly.

### The real divergence: this port spawns a NEW emergency order every time ANY block finds the pool empty; real spawns only ONE at a time

This port's own prediction has TWO concurrent emergency orders in
flight (`t=1.0` and `t=4.0`, arriving `t=6.0` and `t=9.0`). **Real shows
only ONE order at a time, ever** — the full repeating cycle:
```
1.000000   BlockB RepHold Waiting_for_Spare          <- queues, but NO order placed here
4.000000   BlockA Sparing No_spare_in_stock-Cells
4.000000   Cells  Sparing Spare_ordered_(stock=0)     <- FIRST (and only) order of this cycle
9.000000   Cells  Sparing Ordered_spare_arrives_(stock=0)   <- exactly EmerTime=5.0 after t=4
9.000000   BlockB Sparing Spare_Arrived               <- goes to BlockB (queued longest), not BlockA (who triggered it)
11.000000  BlockB Repaired / Good
12.000000  BlockB Failed / Sparing No_spare_in_stock-Cells
12.000000  Cells  Sparing Spare_ordered_(stock=0)      <- NEXT order, again exactly 5.0 units before arrival
17.000000  Cells  Sparing Ordered_spare_arrives_(stock=0)
17.000000  BlockA Sparing Spare_Arrived
...repeats identically (20→25, 28→33, 36→41, ...) for the rest of the mission
```
`BlockB`'s own `t=1.0` exhaustion does NOT trigger an order at all —
only `BlockA`'s SECOND exhaustion (`t=4.0`, while `BlockB` is still
queued) does. Every subsequent order is triggered the same way: only
when a NEW block queues while the pool is STILL exhausted from an
EARLIER, already-served cycle — never two orders overlapping in time.
`EmerTime=5.0` itself is confirmed exactly right (every single
order→arrival gap in the real capture is precisely `5.0`) — the bug is
purely about WHEN a new order gets triggered, not the lead time itself.

### Root cause (traced directly in this port's own code)

`block_lifecycle.py`'s `resources_was_zero` branch (~line 3047-3058)
unconditionally calls `spawn_order_a_spare()`/increments `spare_pool.
num_emer_ordered` every time ANY block finds the pool at `resources==0`
— with no check for whether an order is ALREADY outstanding. This
exactly matches the observed bug: this port's own run DOES log
`"No_spare_in_stock-Cells"`/`"Spare_ordered_(stock=0)"` for `BlockB` at
`t=1.0` (confirmed directly), which real never shows.

**Not yet fixed** — the naive candidate fix (gate the spawn on `spare_
pool.num_emer_ordered == 0`) does NOT actually match real: it would
STILL spawn at `t=1.0` (the pool's very first exhaustion, `num_emer_
ordered` genuinely being `0` at that point) — real doesn't order even
then, only on the block's OWN second/later exhaustion event. The true
trigger condition is still unconfirmed — possibly requires the
QUEUE/backlog to reach a specific size (not just "pool is empty"), or
some other condition this single capture's own repeating cycle doesn't
fully disambiguate (every cycle in this capture happens to follow the
identical "first exhaustion silent, second triggers" shape, so it can't
distinguish "always skip the very first exhaustion of a given empty
spell" from "only order when 2+ are backlogged" from other candidate
rules). A follow-up probe with a DIFFERENT backlog-depth pattern (e.g.
three blocks queuing in sequence before any order fires) would help
pin down the exact rule before attempting a fix.

## SOLVED, 2026-09-05 (`ReleaseProbe33`/`38`/`39`'s own real captures)

Five fix attempts and four real-capture rounds later, the true rule:
grace applies ONCE per run, to whichever failure(s) constitute the
SYSTEM's own very first-ever failure (not this pool's, not this
block's) — `BlockB`'s own `t=1.0` failure here happens to BE the
system's first-ever failure (nothing else has failed yet), so it's
silent; every LATER failure orders unconditionally, matching this
probe's own `t=4.0`/`t=12.0`/... cycle exactly. Confirmed independent
of `Stock`, `RSO`/`SLO`, and absolute sim-time (`ReleaseProbe39` shows
the SAME grace surviving all the way to `t=500.0` when nothing has
failed before it). Implemented via `SystemState.first_failure_time`
(`raptor/core/engine/system_status.py`) + a check in `block_lifecycle.
py`'s ESO branch. Full trail: the internal engine ledger D.1,
`tests/fixtures/ReleaseProbe39/README.md`.
