# ReleaseProbe22 — is PM-trigger subscriber wake order row/ID order, or alphabetical by block NAME?

`Phasing1`'s own real capture (run 8, `t=311.466386`): the `500W-
PowerString` trigger fires with two subscribers, `SolarP_500W` (block
ID=1, `.rbd` row 29) and `BatteriesNiCd` (block ID=2, row 30). This
port's current implementation (`orchestrator.py`'s `on_fire`, built from
`model.blocks_by_id.values()` iteration order = file row order = block
ID order) treats `SolarP_500W` as "row-first" — dispatched first. But
the real capture shows `BatteriesNiCd` committing its own PM FIRST
(`"PM beginning_PM"`), with the resulting node cascade dep-idling
`SolarP_500W`, which only begins its own PM afterward — the OPPOSITE of
row/ID order.

Checked against `WakeOrderDependent` (the fixture that originally
established "row/config order, not reversed" — G.2's own evidence): its
two subscribers are `BlockC` (ID=3) and `BlockDep` (ID=4) — row order
AND alphabetical-by-name order agree there (C before D), so that
fixture's real capture never actually distinguished the two hypotheses.
`Phasing1`'s pair does: row/ID order says `SolarP_500W` first,
alphabetical order says `BatteriesNiCd` first (B < S) — and the real
capture matches ALPHABETICAL.

**This probe isolates that question directly**, with no dependency race
at all (so G.3's absorption mechanism can't confound the reading).

## What's new here vs `ReleaseProbe10`

Same chain shape (`start→[row-first block]→[row-second block]→BlockD→
j1→end`, both subscribing to trigger `T1`), with two changes:

1. **Both subscribers' dependencies removed** (`DepNum: -1→0,
   defDepStateIdle: 1→0` on both `BlockS`/`BlockN`, matching
   `ReleaseProbe16`'s own already-proven-safe single-field edit,
   applied to BOTH blocks this time instead of just one) — neither
   block is touched by `BlockD`'s own fail/repair cycle at all, so
   there's no dependency-driven race on either block's own
   `InterruptReason` field. Pure ordinary (untied) trigger-fire
   ordering, nothing else in play.
2. **Renamed to make row/ID order and alphabetical order predict
   OPPOSITE winners**: `BlockS` (row-first, ID=3) → `ZLast`; `BlockN`
   (row-second, ID=4) → `AFirst`. Row/ID order predicts `ZLast` wins;
   alphabetical-by-name order predicts `AFirst` wins.
3. **`BlockD`'s fail time shifted `FIXED(1.0)→FIXED(0.4)`** so its own
   second fail (repair completes at `3.4`, next fail at `3.8`) no
   longer coincidentally ties with `T1`'s own `5.0` fire the way
   `ReleaseProbe10`'s original `FIXED(1.0)` did — `BlockD` is now
   fully inert to this probe's own question (nothing depends on it,
   and it no longer creates a coincidental same-instant bystander
   event either). Everything else — `T1`'s `FIXED(5.0)` fire, both
   subscribers' PM distributions/streams (`EXP(1.0)`/`EXP(2.0)`, both
   `SysStream 203`) — is byte-identical to `ReleaseProbe10`.

## The question

At `T1`'s ordinary (untied, unraced) fire at `t=5.0`, which block gets
its `PM_is_due`/`"beginning_PM"` lines FIRST?

- **`ZLast` first** → confirms row/ID/declaration order as the true
  rule. `Phasing1`'s own flip (§ analysis below) would need a DIFFERENT
  explanation — e.g. something specific to the tie/dependency-race
  shape in that fixture, not the baseline ordering rule itself.
- **`AFirst` first** → confirms alphabetical-by-block-name order (or at
  minimum, REFUTES row/ID order) — a genuinely new primitive this
  project hasn't ported at all yet. Would directly explain `Phasing1`'s
  own real capture (`BatteriesNiCd` < `SolarP_500W` alphabetically) and
  redirect the fix target for that fixture's own divergence toward
  rebuilding `pm_trigger_subscribers` in alphabetical-by-name order
  instead of `blocks_by_id` iteration order.
- **Some other order** (e.g. shortest name first, or an order matching
  neither) → note the exact real event log around `t=5.0` in full;
  would point to a third variable neither hypothesis anticipated.

## Our port's own prediction (implements row/ID order only, doesn't know about alphabetical order at all)

All 3 runs, `t=5.0`:
```
Trigger T1 Fired
Block ZLast   PM_is_due  Triggered_PM_Due
Block AFirst  PM_is_due  Triggered_PM_Due
Block ZLast   PM         beginning_PM
Block AFirst  PM         beginning_PM
```
`/sd` draws (run 1): `ZLast 0.602041`, `AFirst 2.944303` — identical
values to `ReleaseProbe10`'s own `S`/`N` draws (same seeds/streams,
only names/dependencies/`BlockD`'s timing changed), a built-in
continuity check for the real capture too.

## Readout (t=5.0 /sd + event log)

Order the two `SimTime=5.000000` `/sd` lines and the `PM_is_due`/
`"beginning_PM"` event-log line pairs. No tie, no dependency race — a
clean, unambiguous "which one first" reading, nothing else to check.

## How to capture it

Same procedure as the other `ReleaseProbe*` fixtures — launch Raptor7
Student with `/sd`, open this file, Simulate (3 runs, matching
`ReleaseProbe10`/`16`'s own capture count), read the event log and
`/sd` output around `SimTime=5.0`. Real-Raptor report outputs land in
`~/Documents/Raptor7/` (`rpt_events_*` etc.); the `/sd`
copy goes to this repo's root as `simDetails_ReleaseProbe22.txt`.

## REAL ANSWER (2026-09-04 capture, `simDetails_ReleaseProbe22.txt` + `~/Documents/Raptor7/rpt_events_ReleaseProbe22.txt`)

**`ZLast` first, `AFirst` second — in all 3 runs, byte-for-byte matching
this port's own prediction.** `/sd`:
```
5.000000  ZLast   0.602041
5.000000  AFirst  2.944303
```
Event log (all 3 runs identical shape):
```
Trigger  T1      Fired
Block    ZLast   PM_is_due  Triggered_PM_Due
Block    AFirst  PM_is_due  Triggered_PM_Due
Block    ZLast   PM         beginning_PM
Block    AFirst  PM         beginning_PM
```

**Row/ID/declaration order is CONFIRMED as the true rule for an
ordinary (untied, unraced) trigger fire — the alphabetical-by-name
hypothesis is REFUTED.** This port's existing `pm_trigger_subscribers`
implementation (`model.blocks_by_id` iteration order) was already
correct for this case; no fix needed here.

**Consequence for `Phasing1`'s own real divergence** (the finding that
motivated this probe): the swap observed there (`BatteriesNiCd`
committing PM before `SolarP_500W`, opposite of row/ID order) is NOT
explained by a general subscriber-ordering rule change — this probe
rules that out cleanly with a second independent real data point (on
top of `WakeOrderDependent`). The real explanation must be something
SPECIFIC to `Phasing1`'s own instant: at `t=311.466386`, `Bus_UR` ALSO
independently fails at the exact same `SimTime` (`"Bus_UR Failed
Operating_time=2.160247"`, in the SAME log burst as the trigger fire)
— a genuine THREE-WAY same-instant collision this probe deliberately
doesn't have (by design, to isolate the ordinary case first). The next
probe for this thread should reintroduce a same-instant THIRD event
racing the trigger fire — closer to `G.3`'s own territory (a
dependency-cascade or unrelated-failure race) than a plain subscriber-
order question. The internal engine ledger's G.1/G.2 entries and
the `Phasing1` investigation notes in the internal investigation log
have been corrected to remove the alphabetical-order hypothesis as a
live lead.
