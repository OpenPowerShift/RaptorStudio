# ReleaseProbe24 — does reconciling a stale, abandoned PM resource request cost real an extra RNG draw (and when)?

The internal investigation log §68 fixed a genuine resource-pool capacity
leak in `tests/golden/Phasing3`: a PM resource request that gets
deferred/abandoned (`PMReqDefer`, `pmReqDefer` config flag — the block
moves on with ordinary running instead of blocking) is never withdrawn
from `ResourceQueue`'s own FIFO queue, so it can be granted LATER by an
unrelated release while the block is busy doing something else
entirely. This port now returns those phantom-granted units to the pool
when it discovers this (§68's own fix). But real's own `simDetails.TXT`
shows an EXTRA RNG draw for the reconciling block, at the reconciliation
instant, that this port doesn't produce at all (§68.4, still open) —
this probe isolates that mechanism directly, in a minimal two-block
shape, instead of Phasing3's own five-way collision.

## What this probe actually builds (and what it turned out to test)

Two blocks sharing one 1-unit `RESOURCE_DATA` pool (`Pool1`):
- **`B`** (id=4): ordinary `FIXED(1.0)` fail / `FIXED(10.0)` repair,
  borrows 1 unit of `Pool1` for its own repair, no PM.
- **`A`** (id=3): `FIXED(6.0)` fail / `FIXED(20.0)` repair, PM-triggered
  on `T1` (`FIXED(5.0)`, no repeat), `EXP(1.0)` PM duration, `PMReqDefer`
  set, borrows 1 unit of `Pool1` for BOTH its own ordinary repair
  (`#Res1Rep=1`) and its PM (`#Res1pmRep=1`).
- **`BlockD`** (id=6): inert distractor, `FIXED(1000000)` fail — never
  fires, just keeps the same chain shape as the other `ReleaseProbeN`
  fixtures (`start→A(3)→B(4)→BlockD(6)→j1→end`).

Intended timeline: `B` fails at `t=1`, borrows `Pool1`'s only unit,
repairs until `t=11`. `T1` fires at `t=5` — `A` becomes PM-due but
`Pool1` is fully held, so `A`'s PM request is deferred (`pmReqDefer`) —
per `ResourceQueue.request()`'s own contract, the request stays queued
even though `A` doesn't block on it. `A` then independently fails at
`t=6` (its own unrelated `FIXED(6.0)` countdown) and needs `Pool1` too
for its OWN ordinary repair — a SECOND queue entry for `A`, behind its
own still-queued stale PM one. `B` releases its unit at `t=11`.

**This port's own run** (below) shows this is a CLEAN, minimal
reproduction of the exact queue-order ambiguity behind §68's own bug,
just reached via a different route than Phasing3's own five-way
collision: `Pool1`'s capacity-1, strict-FIFO queue has `A`'s own TWO
entries adjacent (`pm` then `repair`), so `B`'s single release can only
satisfy the FRONT one — `A`'s stale `pm` entry, not the `repair` entry
`A`'s own coroutine is actually suspended waiting on
(`block_lifecycle.py`'s ordinary resource-wait site, ~line 3278, has NO
kind-check at all — it treats ANY `Pool_Available` interrupt as "my own
queued request landed," unconditionally). `A` proceeds into `Repairing`
believing this is its own grant; its ACTUAL `repair`-kind entry sits
forgotten in the queue until `A`'s OWN repair-completion release (at
`t=31`) self-satisfies it — only THEN does `A`'s code recognize (via the
existing `pm_resource_pending` ground-truth check at the PM-obtain
site) that its ORIGINAL PM is still due, and performs it immediately
after finishing repair, consuming exactly one PM-duration RNG draw at
`t=31.0` and nothing at `t=6.0` or `t=11.0`.

## The question

Real's own `Phasing3` capture shows the analogous reconciliation
(`BatteriesNaS`'s own stale PM entry landing while it's already
mid-repair from an unrelated failure, §68.1) draws an EXTRA RNG value
at the reconciliation instant that isn't explained by any visible event
-- this probe asks WHERE, in a clean two-block case, that extra draw
(if any) actually lands:

- **No extra draw anywhere; `A`'s only draw is the `t=31.0` PM-duration
  value, same as this port** → the "extra draw" `Phasing3` shows isn't
  a general reconciliation-mechanism artifact at all; something specific
  to `Phasing3`'s own five-way collision (worth re-reading that capture
  for a coincidental unrelated event at the same instant before
  concluding anything about reconciliation itself).
- **An extra draw appears at `t=6.0`** (when `A`'s NEW independent
  failure supersedes/abandons the stale PM) → real formally RETIRES the
  abandoned PM cycle right there and immediately schedules a fresh one
  (a new PM-interval redraw), rather than leaving it queued/ambiguous —
  this would mean real WITHDRAWS the stale request from the resource
  queue outright at abandonment time (matching what `docs/engine-
  gap-analysis.md` §68.1 already found in `Phasing3`'s own event log:
  `BatteriesNaS` never shows a double "Obtained" line) and this port's
  entire deferred-reconciliation design (`pm_resource_pending`) is
  structurally the wrong shape — the fix belongs at the ABANDON site
  (`block_lifecycle.py`'s `pm_req_defer` branch, ~line 2740), not the
  reconciliation site (~line 3355).
- **An extra draw appears at `t=11.0`** (when `B`'s release actually
  lands) → the redraw is tied to the GRANT event itself, not the
  abandonment — closer to this port's own reconciliation-time fix
  location, just missing a draw there.
- **An extra draw appears at `t=31.0`, alongside (not instead of) the
  PM-duration draw** → real's own PM-duration draw is preceded by some
  OTHER draw at the same instant this port doesn't model (e.g. a
  redundant/wasted "attempt" draw) — would need the real event log's
  own line order at that exact instant to say more.

## Predicted (this port)

All 3 runs, `A`'s only recorded draw (via this port's own `/sd`
equivalent): a single PM-duration value at `t=31.000000` (run 1:
`0.603248`) — nothing at `t=6.0` or `t=11.0`. Full event log (run 1):
```
1.000000   Block  B  Failed      Operating_time=1.000000
1.000000   Block  B  Repairing   1Pool1
5.000000   Trigger T1 Fired      System_event
5.000000   Block  A  PM_is_due   Triggered_PM_Due
5.000000   Block  A  pmReqDefer  No_resource_in_stock-Pool1
6.000000   Block  A  Failed      Operating_time=6.000000
6.000000   Block  A  RepHold     Waiting_for_resource(s)
11.000000  Block  B  Repaired    MaintTime=10.000000
11.000000  Block  B  Good        Stress=1.000000
11.000000  Block  A  Sparing     Obtained_1_Resource(s)
11.000000  Block  A  Repairing   1Pool1
12.000000  Block  B  Failed      Operating_time=1.000000
12.000000  Block  B  RepHold     Waiting_for_resource(s)
31.000000  Block  A  Repaired    MaintTime=20.000000
31.000000  Block  A  Good        Stress=1.000000
31.000000  Block  A  Resources   Obtained_1_Resource(s)
31.000000  Block  A  PM          beginning_PM
31.603248  Block  A  PM_complete MaintTime=0.603248
```
(`B` keeps failing/repairing every 11 units for the rest of the 50-unit
mission, immaterial to `A`'s own thread.)

## Readout

Compare real's own event log + `/sd` around `t=6.0`/`t=11.0`/`t=31.0`
for `A` specifically: does `A`'s PM ever separately appear as
`beginning_PM` right after `t=11` (this port's own current queue-order
mixup never lets that happen — `A` goes straight to ordinary
`Repairing` at `t=11`, PM only surfaces at `t=31`), and where (if
anywhere) an extra draw for `A` appears relative to this port's single
`t=31.0` value.

## How to capture it

Same procedure as the other `ReleaseProbeN` fixtures — launch Raptor7
Student with `/sd`, open this file, Simulate (3 runs), read the event
log and `/sd` output for block `A` across the whole 50-unit mission.
Real-Raptor report outputs land in `~/Documents/Raptor7/`
(`rpt_events_*` etc.); the `/sd` copy goes to this repo's root as
`simDetails_ReleaseProbe24.txt`.

## REAL ANSWER (2026-09-04 capture, `~/Documents/Raptor7/rpt_events_ReleaseProbe24.txt`)

**Decisive, and cleaner than any of the four candidate branches above —
`A`'s PM never happens AT ALL.** Real's own event log for `A`, all 3
runs identical shape:
```
5.000000   Block  A  PM_is_due   Triggered_PM_Due
5.000000   Block  A  pmReqDefer  No_resource_in_stock-Pool1
6.000000   Block  A  Failed      Operating_time=6.000000
6.000000   Block  A  RepHold     Waiting_for_resource(s)
11.000000  Block  A  Sparing     Obtained_1_Resource(s)
11.000000  Block  A  Repairing   1Pool1
31.000000  Block  A  Repaired    MaintTime=20.000000
31.000000  Block  A  Good        Stress=1.000000
37.000000  Block  A  Failed      Operating_time=6.000000    <- NEXT ordinary cycle, no PM in between
```
Through `t=11.0` this is BYTE-IDENTICAL to this port's own prediction
(same event text, same timestamps: `B`'s release grants `A`'s ordinary
`repair`-kind need cleanly). **The divergence starts exactly at
`t=31.0`**: this port's own prediction has `A` immediately pick back up
its stale PM request there (`"Resources Obtained_1_Resource(s)"` →
`"PM beginning_PM"` → a `t=31.6` `PM_complete`, consuming one
`EXP(1.0)` draw) — real shows NONE of that. `A` just goes `Repaired`→
`Good` and returns to ordinary running; its next event is a fresh
ordinary `Failed` at `t=37.0` (`31+6`, its own normal fail-time cycle),
with no PM activity anywhere in between. **Real draws ZERO values for
`A` in the entire capture** — not even the `t=31.0` PM-duration draw
this port's own (wrong) prediction consumes.

### What this settles for §68.4

None of the four candidate branches were quite right — there's no
"extra draw," because there's no revived PM cycle for the draw to
belong to. The abandoned PM request is discarded OUTRIGHT and
PERMANENTLY the instant a genuinely new, independent failure supersedes
it — not left in the queue to be reconciled/consumed later at all. This
also settles the mechanism question §68.1 could only infer indirectly
from `Phasing3`'s own five-way collision: real doesn't "silently
absorb" a stale grant after the fact (this port's §68 fix's own
approach) — it prevents the stale request from ever being live enough
to consume a resource unit again in the first place.

### Consequence for this port

This port's own current behavior (confirmed via direct instrumentation,
§68's own docs) satisfies the pool's STRICT FIFO in queued order: when
`A`'s new ordinary-repair request is queued BEHIND its own still-live
stale PM request, `B`'s `t=11` release grants whichever is FRONT (the
stale PM one) — `block_lifecycle.py`'s ordinary resource-wait site
(~line 3278, `if not granted: resume = yield _FAR_FUTURE`) has NO
ground-truth check at all (unlike the OTHER four reconciliation sites
in this file, the internal investigation log §68/§69) — it treats ANY
`Pool_Available` interrupt as "my own request landed," so it wrongly
believes it now holds the resource. The pool's own unit accounting
stays balanced (the SAME 1 unit is genuinely borrowed and later
returned), so this doesn't leak or crash — but `A`'s own real repair-
kind entry sits forgotten in the queue until `A`'s own eventual repair-
completion release self-satisfies it, reviving the abandoned PM ~20
units later than it should ever happen at all. Fixed in
The internal investigation log §69 (same session, same-day follow-up to
§68) — the ordinary resource-wait site now withdraws any stale,
same-block queue entry of a DIFFERENT kind before making its own fresh
request, matching real's own outright-discard behavior exactly. `A`
now shows zero PM activity after `t=6.0`, byte-identical to real.
