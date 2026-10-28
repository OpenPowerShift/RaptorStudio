# ReleaseProbe19 — do TWO pool-grant pickup competitors flip B's redraw from hopped to sync? (candidate (b), §39/§58)

`ReleaseProbe3` (the internal investigation log §37, §39) established the
general rule this port's recovery-redraw hop implements: a Site-1-parked,
node-dependent block's fresh recovery redraw, racing ONE already-queued
pool-grant resource pickup at the same instant, is **HOPPED** (the
pickup's continuation draws first) — real's `[C's pickup, B's redraw]`,
matching our port exactly.

`Capacity1` run 5 (`t=62.715126`) is the ONE known real instant where the
analogous situation — `BatteriesNiCd`'s Site-1-park recovery redraw racing
already-queued pool-grant pickups — instead runs **SYNC**: `NiCd`'s
redraw lands in /sd position 2, BEFORE the pickups' continuations. No
probe has yet reproduced the flip.

§58 ruled out the first of §39's two candidate distinguishing features
via `ReleaseProbe18`: a second, simultaneous, NON-drawing same-node
recoverer (`SolarP_500W`'s role) has zero effect — `BlockB`'s redraw
stayed hopped, byte-identical to probe-3. **Candidate (b) — TWO
pool-grant competitors instead of one — is the sole remaining lead.**

## What's new here vs `ReleaseProbe3`

Byte-identical to `ReleaseProbe3` (`BlockA`→`BlockB` chain into `join`,
`BlockC` parallel, `join`→`end`, `BlockB`'s trigger-driven PM at t=4
causing its own `downNum>1` direct-Idle Site-1 park at t=5, single
`Techs` pool, A holding the pool 3→25) with exactly TWO changes, both
serving one goal — a SECOND pickup granted at t=25 alongside `BlockC`:

1. **New block `BlockD`** (id 8), a byte-for-byte twin of `BlockC`
   except identity/position/streams: fails FIXED at **3.6** (after C's
   3.5, so the RepHold waiter-queue order is unambiguous — C first, D
   second), repair EXP(10) on its own **stream 8** (C's is stream 6). No
   PM, no dependency, no trigger subscription — a pure repair-resource
   waiter, the same mechanism as C, so the ONLY structural variable vs
   probe-3 is the pickup COUNT. Wired on its own physically-separate
   branch `start→BlockD→end` (links 7, 8) — the exact pattern
   `ReleaseProbe18`'s `BlockE` already proved safe in real Raptor
   (`end`'s `n` bumped `1→2`, `start`'s `Out` bumped `2→3`); D never
   touches `join`, so the downNum counting that parks B at t=5 is
   unaffected.
2. **`Techs` pool `NumIn: 1→2` and `BlockA`'s repair grab `#Res1Rep:
   1→2`** — A now holds BOTH units 3→25 (mirroring `Capacity1`'s own
   `Bus_UR` `Repairing 5Astronauts` multi-unit grab), so the pool is
   empty while C and D wait, and A's t=25 repair completion releases
   BOTH units simultaneously → **two** `Obtained_1_Resource(s)` grants +
   two repair draws racing B's recovery redraw. (With `NumIn=1` the
   second waiter could never be granted at t=25 — it would still be
   waiting — which is why the multi-unit grab is required to build the
   two-competitor shape at all.)

Deliberately NOT changed: C's mechanism (repair pickup, not a deferred-PM
pickup), B, the trigger, the timeline, the stream layout (A: F1/R2 FIXED,
B: F3/R4, C: F5/R6, D: F7/R8 — all FIXED fails, so the only /sd draws in
the whole run are B's kickoff+recovery redraws and C/D's pickup repair
draws).

**Confirmed via this port's own run** (structural sanity check, not the
readout): `BlockA Repairing 2Techs` at t=3; C and D both
`RepHold Waiting_for_resource(s)` at 3.5/3.6; B's `PM_complete → Idle
change_block_state` at t=5 (Site-1 park, no Good line, no redraw); at
t=25 `BlockA Repaired` → B `Good DependenceStress` → `BlockC Sparing
Obtained_1_Resource(s) / Repairing 1Techs` → `BlockD Sparing
Obtained_1_Resource(s) / Repairing 1Techs` — the three-way collision
`Capacity1` run 5 has. C's pickup draw values (1.357908 / 3.582951 /
13.185966 across the 3 runs) are identical to `ReleaseProbe3`'s REAL
capture (same per-block stream seeding — continuity check passed), as
are B's redraw values to within the usual ~1e-6 software-ln noise.

## The question

At `t=25.000000`, where does `BlockB`'s own recovery redraw land relative
to the TWO pickup draws?

- **`[BlockC, BlockD, BlockB]`** (B trails both) → still HOPPED: the
  pickup count alone is NOT the discriminator — candidate (b) ruled out
  too. The remaining confound between probe-3 and `Capacity1` run 5 is
  then the pickup TYPE (probe-3/19's repair-resource pickups vs run 5's
  deferred-PM `pmReqDefer` pickups, which in this port pay a different
  hop — `pm_resource_pending` TELL-boundary vs the repair-path
  `WAIT DURATION 0.0`): next probe swaps the competitors' type, not
  count.
- **`[BlockB, ...]`** (B leads both) → SYNC: the two-competitor shape
  reproduces `Capacity1` run 5's flip, and the port's hop rule needs a
  count-dependent (or otherwise competitor-aware) refinement.
- **`[BlockC, BlockB, BlockD]`** or any interleaved order → also a flip
  (B no longer trails everything); capture the exact order, it
  discriminates finer than the binary sync/hop reading.

## Our port's own prediction (NOT the readout — our port doesn't implement a variable sync-vs-hop rule)

`sim_details_path` trace from our own `run_simulation`, all 3 runs:
```
 0.000000  BlockB  792225.529423
25.000000  BlockC  1.357908
25.000000  BlockD  8.484571
25.000000  BlockB  842292.018741
 0.000000  BlockB  416902.749341
25.000000  BlockC  3.582951
25.000000  BlockD  25.665643
25.000000  BlockB  661497.624323
 0.000000  BlockB  3144207.888279
25.000000  BlockC  13.185966
25.000000  BlockD  2.871198
25.000000  BlockB  831393.179051
```
`[C, D, B]` (hopped) — the grants queue during A's repair-completion
turn ahead of B's wake, and each pickup's continuation pops before B's
hop continuation, exactly as in probe-3 with one competitor. This
prediction is NOT the subject of the probe; only real Raptor's own
answer distinguishes the hypotheses above.

## Readout (t=25.0 /sd order)

Count/order the `/sd` lines at `SimTime=25.000000` across the 3 runs:
which of `BlockC`/`BlockD`/`BlockB` draws first, second, third. B before
both pickups = SYNC (flip); B after both = HOPPED (no flip); between =
interleaved (record exact order). Also sanity-check the event log around
t=25 for the expected lines (`Repaired 22` / two `Obtained_1_Resource(s)`
/ B `Good DependenceStress`) — if real's event structure differs from
the above (e.g. a different grant order or an extra same-instant actor),
note it: the draw order is only interpretable against the event
structure that produced it.

## How to capture it

Same procedure as the other `ReleaseProbe*` fixtures — launch Raptor7
Student with `/sd`, open this file, Simulate (3 runs matches
`ReleaseProbe3`'s own capture count), read the event log and `/sd`
output around SimTime=25.0. Real-Raptor report outputs always land in
`~/Documents/Raptor7/` (`rpt_events_*` etc.); the `/sd`
copy is dropped at this repo's root as `simDetails_ReleaseProbe19.txt`.

## REAL ANSWER (2026-09-03 capture, `simDetails_ReleaseProbe19.txt` + `~/Documents/Raptor7/rpt_events_ReleaseProbe19.txt`)

**`BlockB`'s redraw STAYS HOPPED** — the order is `[BlockC, BlockD,
BlockB]` in all 3 runs:
```
 0.000000  BlockB  792225.529889
25.000000  BlockC  1.357908
25.000000  BlockD  8.484571
25.000000  BlockB  842292.019207
...
```
and the values match this port's own prediction EXACTLY: C's pickup
draws (1.357908/3.582951/13.185966, stream 6) and D's pickup draws
(8.484571/25.665643/2.871198, stream 8) are byte-identical real vs
ours — including the brand-new stream 8, an end-to-end RNG-seeding
confirmation — and B's redraw differs only by the known exp(1e6)
software-`ln` noise class (real ...019207 vs ours ...018741, same as
probe-3/18). The real event log (run 1) is line-for-line identical to
this port's: `Repairing 2Techs` at 3, dual `RepHold` at 3.5/3.6, B
`PM_complete → Idle change_block_state` at 5, at 25 `Repaired` → B
`Good DependenceStress` → dual `Sparing Obtained_1_Resource(s)/
Repairing 1Techs` → Yellow. (The capture file carries TWO RunEFPA
dumps, byte-identical same-model re-appends — readout unaffected;
per §43.1/§44 the dump count was checked before comparing.)

**Consequence**: candidate (b) — two pool-grant competitors instead of
one — is RULED OUT. The pickup count alone does not flip B's redraw to
sync. Combined with §58 (second non-drawing recoverer: ruled out), BOTH
of §39's candidate features are now dead. The remaining structural
difference between probe-3/19's hopped answer and `Capacity1` run 5's
sync one is the **pickup TYPE**: probe-3/19's competitors are
repair-resource waiters (`RepHold` → `WAIT FOR Techs TO Give`-style
resumes, source `:5749` Give-resumes preceding the
`DetermineNewState`→cascade at `:5250-5266` — probe-3/19's
`[pickups → B-redraw]` order matches that source order exactly), while
run 5's competitors are **deferred-PM** waiters (`pmReqDefer` — NOT in
the pool's Give-wait queue; run 5's real order `[recovery-cascade →
NiCd-redraw → PM pickups]` cannot follow the Give-resumes-first source
order unless their grant arrives as a QUEUED notification processed
after the releasing turn, not a synchronous Give-resume). This port
queues BOTH grant kinds identically (`wake_ids` `Pool_Available`
interrupts ahead of the cascade), which reproduces probe-3/19 by
construction but cannot reproduce run 5. **Next probe
(`ReleaseProbe20`)**: swap the pickup TYPE at count 1 — one
deferred-PM competitor (`pmReqDefer`, granted at t=25, PM-begin draw)
instead of a repair one, `BlockC` kept as a resource-free bystander so
B's baseline sequence (dep-Idle 3.5 → PM 4 → park 5) is preserved
byte-for-byte. `[B, PM-draw]` pins the mechanism and hands Capacity1
runs 5-9 their fix surface; `[PM-draw, B]` rules type out too and
promotes §39's remaining feature (the PM-begin cascade's mid-instant
dependency dispatches).
