# ReleaseProbe20 — does the pickup TYPE (deferred-PM vs repair) flip B's redraw from hopped to sync? (the last §39 feature, after probe-19 killed the count)

`ReleaseProbe3`/`19` (§37/§39/§59-60): a Site-1-parked node-dependent
block's fresh recovery redraw racing pool-grant pickup continuations is
**HOPPED** — real's `[pickups..., B's redraw]`, matching this port, for
ONE competitor (probe-3) and TWO (probe-19, `[C, D, B]`). Candidate (b)
— pickup count — is dead.

`Capacity1` run 5 (`t=62.715126`) remains the one real instant where the
analogous recovery redraw runs **SYNC** (`NiCd`'s redraw at /sd position
2, BEFORE the pickups). With count and the second non-drawing recoverer
(probe-18) both ruled out, the remaining structural difference between
probe-3/19's pickups and run 5's is their **TYPE**:

- probe-3/19's competitors are **repair-resource waiters** (`RepHold` →
  pool Give-resume → repair draw). Real's `[pickups → B-redraw]` order
  matches source's repair-completion order exactly (TakeBack's
  Give-resumes `:5749` precede `DetermineNewState`'s cascade
  `:5250-5266`).
- run 5's competitors are **deferred-PM waiters** (`pmReqDefer` →
  granted at the release → `PM beginning_PM` → PM draws) — and real's
  run-5 order `[recovery-cascade → NiCd-redraw → PM pickups]` cannot
  follow the Give-resumes-first source order unless the deferred-PM
  grant reaches its block by a DIFFERENT (later-processed) path than a
  repair waiter's Give-resume.

This port queues BOTH grant kinds identically (`wake_ids`
`Pool_Available` interrupts ahead of the cascade), which reproduces
probe-3/19 by construction but cannot reproduce run 5.

## What's new here vs `ReleaseProbe19`

**One field-cluster change: `BlockD`'s pickup type.** Everything else is
byte-identical to probe-19 (A→B chain into `join`, C parallel and
unchanged from probe-3 — fails FIXED 3.5, `RepHold`, repair EXP(10)
stream 6, `#Res1Rep=1`; `Techs` pool `NumIn=2`; A `#Res1Rep=2` grabbing
both units 3→25; D on its own `start→D→end` branch, links 7/8, `end`
n=2, `start` Out=3; trigger PM at 4; B's Site-1 park at 5). D's row:

- probe-19's D: a repair-waiter twin of C (fails FIXED 3.6, repair
  EXP(10) stream 8) — drew its REPAIR time at the t=25 pickup.
- probe-20's D: **never fails** (`FDist=19 FIXED 1000000`) and instead
  carries a **frequency PM**: `usesPM=1`, `NumRes=1`, `#Res1Rep=0`,
  `#Res1pmRep=1`, `Techs`, `PMReqDefer=1`, `PMFreq=10.0` (PM due at
  operating time 10 — D runs clean 0→25), PM duration EXP(10) on stream
  8 (`Dist=4`, Param1=10). At t=10 the pool is empty (A holds both
  units) → `PM_is_due Scheduled_PM` → `pmReqDefer
  No_resource_in_stock-Techs`, no state change — the exact deferral
  signature of `Capacity1`'s `SolarP_650W`/`BatteriesNiH` at 48.788281
  (run 5). At t=25 A's release grants BOTH waiters (pool FIFO: C queued
  3.5, D queued 10) → C's repair pickup + D's deferred-PM pickup + B's
  recovery redraw, all one instant. `pmTriggered=0` (no trigger
  subscription), `DepNum=0` (no dependency), so D touches nothing but
  the pool queue and its own stream 8.

D's PM draw value is predictable before running anything: stream 8's
first draw through EXP(10) is the SAME value probe-19's D produced for
its repair draw (8.484571 / 25.665643 / 2.871198 across the 3 runs) —
a built-in cross-capture continuity check (this port's PM draws come
from the repair stream, the mapping `Capacity1` runs 0-4 already
verified against real).

**Confirmed via this port's own run** (structural sanity, not the
readout): `Repairing 2Techs` at 3; C `RepHold` at 3.5 (join Bad → B
`Idle Dependence`); B `PM_complete → Idle change_block_state` at 5; D
`PM_is_due Scheduled_PM` + `pmReqDefer No_resource_in_stock-Techs` at
10 with no state change; at 25 `Repaired` → B `Good DependenceStress` →
`BlockD Resources Obtained_1_Resource(s)` → `BlockC Sparing
Obtained_1_Resource(s)/Repairing 1Techs` → `BlockD PM beginning_PM`.
(C stays a repair pickup as an INTERNAL CONTROL: whatever real does to
D relative to B, C's position anchors the comparison against probe-19's
already-answered `[C, D, B]`.)

## The question

At `t=25.000000`, where do C's repair draw and D's PM draw land
relative to B's recovery redraw?

- **`[BlockC, BlockD, BlockB]`** (same as probe-19) → the deferred-PM
  pickup orders like a repair pickup: type ruled out too. EVERY §39
  feature is then dead, and run 5's sync must come from something none
  of them captured — the remaining candidates being the PM-begin
  cascades' mid-instant dependency dispatches (§39's third feature,
  never probed: run 5's S650 PM-begin dp-cascade re-dispatched NiH/
  Bus_QR dependency draws mid-instant) and/or the repairing block's own
  redraw (Bus_UR's exp redraw led run 5's instant; A's is FIXED here).
- **`[BlockC, BlockB, BlockD]`** → THE DISCRIMINATOR, cleanly: C (repair)
  ahead, B, D (deferred-PM) behind — real orders deferred-PM grant wakes
  BEHIND the recovery cascade while repair Give-resumes stay ahead.
  Capacity1 runs 5-9 get their fix surface: split the two grant wake
  paths' ordering (repair = Give-resume semantics, deferred-PM = later
  notification), not a scheduler-comparator change.
- **`[BlockD, BlockC, BlockB]`** or any other permution → record the
  exact order; D ahead of C would additionally say the pool's grant
  itself treats the two requester kinds differently, not just the
  continuation order.

## Our port's own prediction (NOT the readout — this port queues both grant kinds identically)

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
`[C, D, B]` — identical to probe-19's own port output including D's
values (stream 8's first draws, now spent on the PM draw instead of the
repair draw), as expected from the identical wake-loop queuing. This
prediction is NOT the subject of the probe; only real Raptor's own
answer distinguishes the hypotheses above.

## Readout (t=25.0 /sd order)

Order the three `SimTime=25.000000` /sd lines across the 3 runs (C,
D, B) and read per the three outcomes above. Sanity-check the event log
around t=25 for the expected lines (`Repaired 22`, D `Obtained_1_
Resource(s)`, C `Sparing Obtained_1_Resource(s)/Repairing 1Techs`,
D `PM beginning_PM`, B `Good DependenceStress`) and at t=10 for D's
`PM_is_due Scheduled_PM` + `pmReqDefer No_resource_in_stock-Techs`
deferral signature — if real's deferral or grant structure differs from
the above, note the full event-log context: the draw order is only
interpretable against the event structure that produced it.

## How to capture it

Same procedure as the other `ReleaseProbe*` fixtures — launch Raptor7
Student with `/sd`, open this file, Simulate (3 runs matches
`ReleaseProbe3`/`19`'s capture count). Real-Raptor report outputs land
in `~/Documents/Raptor7/` (`rpt_events_*` etc.); the
`/sd` copy goes to this repo's root as `simDetails_ReleaseProbe20.txt`.

## REAL ANSWER (2026-09-03 capture, `simDetails_ReleaseProbe20.txt` + `~/Documents/Raptor7/rpt_events_ReleaseProbe20.txt`)

**`[BlockC, BlockB, BlockD]` — THE DISCRIMINATOR, cleanly.** All 3 runs:
```
 0.000000  BlockB  792225.529889
25.000000  BlockC  1.357908      <- repair pickup: AHEAD (as in probe-3/19)
25.000000  BlockB  842292.019207 <- B's recovery redraw: MIDDLE
25.000000  BlockD  8.484571      <- deferred-PM pickup: BEHIND B
...
```
Values: C = stream 6's draws exactly (1.357908/3.582951/13.185966); D =
stream 8's first draws exactly (8.484571/25.665643/2.871198 — the
predicted cross-capture continuity values, byte-identical to probe-19's
D); B = the usual exp(1e6) ln-noise class vs this port. The real event
log is LINE-FOR-LINE identical to this port's — including the log-line
order at t=25 (`D Resources Obtained_1_Resource(s)` BEFORE `C Sparing`,
then `C Repairing`, then `D PM beginning_PM`) and D's t=10 deferral
signature (`PM_is_due Scheduled_PM` + `pmReqDefer
No_resource_in_stock-Techs`, no state change). Only the /sd DRAW order
differs: ours `[C, D, B]`, real `[C, B, D]`.

**The pinned mechanism** (entry-order model, fully consistent with both
engines' event logs): at the releasing turn, real's scheduler-entry
order is **[C's repair Give-resume, B's ZeroWait release, D's
deferred-PM grant]** — the repair waiters' resumes queue at TakeBack
(`:5749`, before the cascade), the cascade runs synchronously (B's
`Good DependenceStress` line), and the deferred-PM grant wake queues
AFTER the cascade. Each entry's continuation (C's `:5695` hop, B's
post-idle-loop hop, D's ObtainPMprereqs→Run hand-back) then pops in
that entry order: C draws, B redraws, D draws. The log lines land
where they do because C's `Sparing` and D's `PM beginning_PM` log at
their SECOND entries while D's `Obtained` logs at its first — exactly
reproducing real's line order. This port queues the deferred-PM grant
wake in the SAME wake loop as repair grants (BEFORE the cascade), so
its entry order is [C, D, B] and D's draw lands ahead of B's — the
exact Capacity1 run-5 defect.

**Consequence**: the pickup TYPE is the D.3 discriminator. Repair-grant
wakes and deferred-PM (`pmReqDefer`) grant wakes take DIFFERENT wake
paths in real: the former ahead of the node cascade, the latter behind
it. Capacity1 runs 5-9's fix surface is a wake-path split in the
releasing turn's grant delivery — NOT a scheduler-comparator change.
(Note this port's own 2026-08-21/22 reading of source — "the pmReqDefer
path STILL runs `WAIT FOR ... TO Give` unconditionally", i.e. D is a
Give-waiter like C — is now DOUBTFUL as the sole mechanism: it cannot
explain D's wake landing behind the cascade's release. Per §35,
Give/TakeBack resume semantics are runtime built-ins not recoverable
from source; the observable split above is the port-relevant truth.)
