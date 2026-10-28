# ReleaseProbe40 — does the most-recently-active subscriber wake FIRST at a tied trigger fire (§45.3 candidate (a), LIFO by calendar-entry recency)?

`Phasing1`'s own real capture (run 9, `t=311.466386`) flips the trigger
subscriber order: `BatteriesNiCd` (row-second, ID 2) dispatches and
commits its PM BEFORE `SolarP_500W` (row-first, ID 1) — contradicting
both row order and `ChooseNext`'s own lowest-block-ID rule (§48.4).
`ReleaseProbe22` (clean fire) and `ReleaseProbe23` (fire tied with a
same-node third block's independent failure, subscribers inactive)
both confirmed row order — so the tie alone does not flip. §45.3's
surviving candidate (a) was never tested: **at a tied instant, the
woken subscribers' resume order might follow their existing calendar
entries LIFO-by-recency, not registration order** — and in `Phasing1`
run 9 the row-second subscriber's last pre-fire calendar entry (269.47)
IS newer than row-first's (254.37).

This probe makes that variable deterministic and isolates it: the two
subscribers get staggered pre-tie fail/repair histories (all `FIXED`,
so no RNG draws and no other mechanism changes) such that the
ROW-SECOND subscriber is the most-recently-active one at the fire.
(Investigation-matrix position: R22 = clean fire, R23 = tie + inactive
subscribers, R27 = Phasing3-wholesale phase context without the tie
and without any discriminating activity stagger — the tie + staggered
recency cell tested here is the one none of them covered.)

## What's new here vs `ReleaseProbe23`

Only the two subscribers' fail/repair distributions changed (both were
`EXP(1000000)` "effectively never fails" before — which also means
`ReleaseProbe22`/`23`'s subscribers had NO pre-fire activity at all,
making any recency rule indeterminate: every calendar entry dated from
kickoff):

- `ZLast` (row-first, ID 3): `FParam1 1000000→1.0` (fails `t=1.0`),
  `RParam1 1000000→3.2` (Good again `t=4.2`; next fail `t=5.2`, AFTER
  the tie — mid-fail-wait at the fire, like both `Phasing1` subscribers).
- `AFirst` (row-second, ID 4): `FParam1 1000000→1.5` (fails `t=1.5`),
  `RParam1 1000000→3.0` (Good again `t=4.5`; next fail `t=6.0`).

**`AFirst`'s last pre-tie transition (4.5) is NEWER than `ZLast`'s
(4.2)** — and so is the fail-wait entry each Run process most recently
scheduled (created at the Good transition). Everything else — T1's
`FIXED(5.0)` fire, `BlockD`'s `FIXED(1.0)`/`FIXED(3.0)` cycle (fails
again exactly at `t=5.0`, tying the fire, no dependency links to the
subscribers, no resources), subscribers' PM durations `EXP(1.0)`/
`EXP(2.0)` on stream 203, `DepNum=0` on both, no phasing — is
byte-identical to `ReleaseProbe23`.

One deliberate side effect: with FIXED lives the subscribers consume
NO kickoff uniforms (R22/23's `EXP(1000000)` lives consumed two), so
the tie's PM-duration draws sit at the stream's first positions —
values differ from R22/23's, which is expected and harmless (the
readout is ORDER, not values). Port run 2's values (`0.602041`/
`2.944303`) equal R22/23 run 1's — a built-in continuity check on the
shared stream's position arithmetic.

## The question

At the `t=5.0` three-way collision (`T1` fires, `BlockD` fails,
`ZLast`/`AFirst` both PM-due), which subscriber dispatches
(`PM_is_due`) and commits (`beginning_PM`) FIRST?

- **`AFirst` first** (flip, matching `Phasing1`'s direction) → §45.3
  candidate (a) CONFIRMED: tied-instant wake order follows the woken
  processes' existing calendar entries LIFO-by-recency, not row/
  registration order. The port's fix surface is then concrete: its
  `on_fire` dispatch (and/or the scheduler's same-instant ordering for
  the woken Run processes) needs the recency term — with R22/23
  (no pre-fire activity) reducing to row order correctly, since all
  entries then date from kickoff in row order.
- **`ZLast` first** (row order holds) → candidate (a) is DEAD in this
  cleanest-possible configuration: recency of the subscribers' own
  recent activity does not govern tied wake order even when it is the
  only thing distinguishing them. `Phasing1`'s flip must then come from
  its phasing/mission mechanics (the documented next suspect) or
  A.1's general `ChooseNext`/`ActivityGroup` composition — and note
  that "earliest-expiry-first" (`ZLast`'s fail-wait expires 5.2 vs
  `AFirst`'s 6.0) would remain confounded with row order in this
  outcome; a follow-up probe inverting the stagger while keeping
  expiries ordered would separate those if it ever matters.
- **Absorption shape** (one `PM_is_due` line missing, G.3-style) →
  would contradict R23's own clean result at the same tie shape;
  capture the full `t=5.0` log and re-read G.3's rule against it.

## Predicted (this port)

Row order — this port's `on_fire` iterates `pm_trigger_subscribers` in
registration order and has no recency term. All 3 runs, identical
shape (FIXED distributions, single-fire trigger):

```
1.000000   Block  ZLast   Failed            Operating_time=1.000000
1.000000   Block  BlockD  Failed            Operating_time=1.000000
1.500000   Block  AFirst  Failed            Operating_time=1.500000
4.000000   Block  BlockD  Repaired          MaintTime=3.000000
4.200000   Block  ZLast   Repaired          MaintTime=3.200000
4.500000   Block  AFirst  Repaired          MaintTime=3.000000
5.000000   Trigger T1     Fired             System_event
5.000000   Block  BlockD  Failed            Operating_time=1.000000
5.000000   Block  ZLast   PM_is_due         Triggered_PM_Due
5.000000   Block  AFirst  PM_is_due         Triggered_PM_Due
5.000000   Block  ZLast   PM                beginning_PM
5.000000   Block  AFirst  PM                beginning_PM
```

`/sd` (3 runs, 2 draws each — the tie's PM-duration draws only):
`ZLast 0.603248`/`AFirst 3.323371`, then `0.602041`/`2.944303`, then
`0.333218`/`4.897720`.

## Readout (t=5.0 event log + /sd)

Order the `PM_is_due` lines and the `beginning_PM` lines separately
(they can disagree — `Phasing1`'s own flip shows in BOTH), and note
`BlockD`'s `Failed` line position relative to them (R23's real put it
BEFORE the two `PM_is_due` lines; `Phasing1`'s real put `Bus_UR Failed`
AFTER the `beginning_PM` lines — that placement difference is itself
unexplained data). Capture 3 runs, same procedure as the other
`ReleaseProbeN` fixtures (`/sd`, Raptor7 Student under Wine): event
log + reports land in `~/Documents/Raptor7/`, the
`/sd` copy at this repo's root as `simDetails_ReleaseProbe40.txt`.
**Run-count check (the §70.4 lesson): read the run count from the
event log's `Simulation_Terminate` groups, never from the /sd dump's
line grouping.**

## REAL ANSWER (2026-09-05 capture, `simDetails_ReleaseProbe40.txt` + `~/Documents/Raptor7/rpt_events_ReleaseProbe40.txt`)

**`ZLast` first, `AFirst` second — dispatch (`PM_is_due`) AND commit
(`beginning_PM`) order — in all 3 runs. Row order holds with the
row-second subscriber most-recently-active: §45.3 candidate (a)
(LIFO-by-recency of the woken processes' calendar entries) is DEAD.**
The 6 `/sd` draws are byte-identical to this port's own prediction
(`0.603248`/`3.323371`, `0.602041`/`2.944303`, `0.333218`/`4.897720`)
— zero RNG divergence anywhere; the 11 differing event-log lines per
run (of 141) are all pure same-instant ORDER differences, no missing
or extra lines, no value differences:

1. **t=1.0 fail-fail tie (`ZLast`'s FIXED(1.0) life expires the same
   instant `BlockD`'s does — an accidental second tie this probe's
   stagger created): real processes `BlockD` (ID 6) FIRST, `ZLast`
   (ID 3) second** — the system-check reason codes confirm it
   (real `_d6`, port `_d3`). This CONTRADICTS `ChooseNext`'s decoded
   lowest-block-ID rule (§48.4) for two same-instant natural fail
   expirations: real woke the HIGHER ID first. Neither row order nor
   lowest-ID nor (indeterminately) LIFO explains it — one data point,
   now on record for the A.1/`ActivityGroup` thread.
2. **t=5.0: real completes the failing block's ENTIRE sequence
   (`BlockD Failed → BlockD Repairing → j1/end Bad cascade →
   State_change Red`) BEFORE delivering the trigger subscribers' PM
   wakes; the port delivers the wakes right after the Failed
   transition, BEFORE the repair-start/cascade.** Consistent with the
   Interrupt-restamp-behind-existing-entries reading of §48.2's
   micro-semantic #2 (the ctl wake lands on the Run processes' NEW
   same-instant entries, which sort behind `BlockD`'s already-queued
   expiry entry). No RNG consequence here (the subscribers are
   `DepNum=0`), but `Phasing1`'s subscribers ARE `DepNum=-1` — if a
   tied fail's cascade ever dep-Idles a subscriber BEFORE its wake
   delivers, this ordering is exactly what decides absorption vs
   dispatch (G.3 territory).

`Phasing1`'s own swap remains unexplained: recency (this probe), the
same-node collision (R23), and phase proximity (R27) are all now
refuted. What's left: the subscribers' `DepNum=-1` wiring + PMRefresh
asymmetry (NiCd=1, SolarP=0) inside the full three-way collision, or
A.1's general `ChooseNext`/`ActivityGroup` composition (§48.5) — with
this probe's two new ordering facts as constraints on any candidate
mechanism.
