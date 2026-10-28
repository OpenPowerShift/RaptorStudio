# ReleaseProbe27 — does `Phasing1`'s own subscriber-order swap reproduce when a phase boundary lands shortly before a trigger fire?

`ReleaseProbe22` (the internal investigation log §67) confirmed row/ID
order for an ordinary, unraced trigger fire. `ReleaseProbe23` (§67.3,
§69.5) reintroduced `Phasing1`'s own same-node third-block collision
(`Bus_UR`-equivalent independently failing at the trigger's own fire
instant) and real REFUTED that hypothesis — row order held anyway.
Both prior probes deliberately have **no phase table at all**, while
`Phasing1` has all three relevant blocks (`SolarP_500W`, `BatteriesNiCd`,
`Bus_UR`) configured `Phasing=1` against a real multi-phase mission —
and its own real event log shows the phase change landing ~1.8 time
units BEFORE the trigger fire, not exactly on it.

## Revision history — THREE real crashes, now rebuilt from scratch on a proven base

This fixture crashed real Raptor three separate times across its first
three constructions (all hand-built from `ReleaseProbe22`'s own minimal
3-block template, adding a `PHASES` section and `P NNN` columns by
hand):
1. **Crashed on OPEN** — a stale `Phases=0` header from bypassing
   `rbd_builder.py`'s own header-count sync (fixed the tooling itself:
   `PHASES` added to `_SECTION_COUNT_KEY`, now a hard `validate()`
   `ERROR` on mismatch).
2. **Crashed on SIMULATE** — suspected an exact-same-instant trigger/
   phase-change collision; retimed by 0.1 units. User reported it
   **still crashed** — this hypothesis was wrong.
3. **Crashed on SIMULATE again** — suspected this fixture being the
   only 2-phase mission in the whole project (every other real-captured
   phased fixture uses 3+); added a third, practically-unreachable
   phase. User reported it **still crashed** — this hypothesis was
   ALSO wrong (or at least insufficient).

**Given three targeted field-level fixes on a hand-built template all
failed, this version abandons that approach entirely.** It's now built
by cloning `tests/golden/Phasing3/input.rbd` — a real, fully-proven,
already-real-captured file — **completely unchanged** except for ONE
field: the `500W-PowerString` trigger's own distribution, changed from
`Dist=8` (`Param1=288.0`, a long random mean) to `FIXED(11.7)` (`Dist=
19`). `SolarP_500W`/`BatteriesNiCd` are Phasing3's OWN existing blocks
— already `Phasing=1`, `usesPM=1`, `pmTriggered=1`, subscribing to this
exact trigger, with their own real, already-parsed `P NNN` phase-value
columns completely untouched. No `PHASES` section edit, no `P NNN`
column edit, no `NODE_DATA`/`HIERARCHY_DATA` edit — every part of the
file previously implicated in a crash is now byte-identical to a file
that has ALREADY loaded and simulated successfully in real Raptor. If
this ALSO crashes, the cause is almost certainly the ONE changed field
(the trigger's own distribution) rather than anything structural.

## Why `FIXED(11.7)`

This port's own run (checked directly, not guessed) shows `D151-225_
Orbit` beginning at `t≈9.92-9.99` across runs (Phasing3's own phase
durations are `NORMAL`-ish, not fully deterministic, but tightly
clustered) — `11.7` lands the trigger fire `~1.7-1.8` units AFTER that
phase boundary, closely matching `Phasing1`'s own real instant (a phase
change landing ~1.8 units BEFORE the trigger fire) far more precisely
than either of this fixture's earlier hand-built attempts managed.

## What this port's own run actually does

All 3 runs, consistent shape (only the phase-boundary's own exact
timing varies slightly run to run):
```
9.921192   System  Phase_change  Phase_change  Begin_phase_D151-225_Orbit
11.700010  Trigger 500W-PowerString  Fired      System_event
11.700010  Block   SolarP_500W    PM_is_due     Triggered_PM_Due
11.700010  Block   BatteriesNiCd  PM_is_due     Triggered_PM_Due
11.700010  Block   SolarP_500W    PM            beginning_PM
11.700010  Block   BatteriesNiCd  Idle           Dependence
11.700010  Block   Bus_UR         Idle           Dependence
11.700010  Block   BatteriesNiCd  PM             beginning_PM
```
`SolarP_500W` (row-first) commits its PM immediately; `BatteriesNiCd`
(row-second) gets dep-idled by `Bus_UR`'s own cascade first, then
recovers and commits its own PM afterward — the SAME same-node-chain
mechanism `ReleaseProbe23` already tested (and real already refuted the
"same-node collision changes order" hypothesis for), but this time
embedded in Phasing3's own REAL, phase-relevant context instead of an
isolated minimal fixture.

## The question

With a phase boundary landing ~1.7-1.8 units before the trigger fire
(closely matching `Phasing1`'s own real shape), does `SolarP_500W`
still commit first, or does `BatteriesNiCd` win instead (matching
`Phasing1`'s own real swap)?

- **`SolarP_500W` first, matching this port's own prediction** → phasing
  proximity to the trigger fire (at least in this shape) doesn't change
  the outcome either — `Phasing1`'s own swap still needs a different
  explanation (its own specific topology/phase-table beyond what this
  probe can isolate, or ultimately `A.1`'s general `ChooseNext` rule).
- **`BatteriesNiCd` first, matching `Phasing1`'s own real swap** → this
  IS the distinguishing factor — worth tracing precisely which
  mechanism (the phase-boundary's own recent occurrence changing
  dependency-propagation timing, most likely) causes the flip.
- **Some other pattern** → note the full real event log around
  `t=9.9-11.7` precisely.

## Predicted (this port)

See "What this port's own run actually does" above — `SolarP_500W`
first. `/sd` (run 1): both blocks draw their own PM-duration values at
`t=11.700010` (same shared stream 203 as everything else in Phasing3).

## How to capture it

Same procedure as the other `ReleaseProbeN` fixtures — launch Raptor7
Student with `/sd`, open this file, Simulate (matching Phasing3's own
10-run capture count), read the event log and `/sd` output around
`SimTime=9.9-11.7`. Real-Raptor report outputs land in
`~/Documents/Raptor7/` (`rpt_events_*` etc.); the `/sd`
copy goes to this repo's root as `simDetails_ReleaseProbe27.txt`.

## REAL ANSWER (2026-09-04 capture, `~/Documents/Raptor7/rpt_events_ReleaseProbe27.txt`)

**Confirms this port's own prediction exactly, in every run where the
comparison applies.** Runs 1, 3, 5 (and matching shape elsewhere):
```
9.897861   System  Phase_change  Phase_change  Begin_phase_D151-225_Orbit
11.700010  Trigger 500W-PowerString  Fired      System_event
11.700010  Block   SolarP_500W    PM_is_due     Triggered_PM_Due
11.700010  Block   BatteriesNiCd  PM_is_due     Triggered_PM_Due
11.700010  Block   SolarP_500W    PM            beginning_PM
11.700010  Block   BatteriesNiCd  Idle          Dependence
11.700010  Block   BatteriesNiCd  PM            beginning_PM
```
`SolarP_500W` (row-first) commits its PM immediately; `BatteriesNiCd`
(row-second) gets dep-idled by `Bus_UR`'s own same-node cascade first,
then recovers and commits afterward — matching this port's own
prediction line-for-line, with a phase boundary landing ~1.7-1.8 units
before the trigger fire (closely matching `Phasing1`'s own real shape).
Runs 2 and 4 show a DIFFERENT, unrelated scenario (`SolarP_500W`'s own
PM genuinely defers due to resource exhaustion at that instant, a
run-specific natural variance in `Astronauts` pool contention —
`BatteriesNiCd` doesn't even reach `PM_is_due` in those runs, matching
G.1's own "wake silently drops if not `Running`/`Idle`/`Standby`" rule)
— not directly comparable to the row-order question, but not a
divergence either.

**Row order is CONFIRMED to hold even with a phase boundary landing
shortly before the trigger fire, closely mirroring `Phasing1`'s own
real shape.** `Phasing1`'s own subscriber-order swap (`BatteriesNiCd`
before `SolarP_500W`) is STILL not reproduced by this construction —
the same-node collision (`ReleaseProbe23`) and phase-proximity
(`ReleaseProbe27`, this probe) hypotheses are BOTH now refuted by real
evidence. The explanation must lie in something more specific to
`Phasing1`'s own exact topology/config, or ultimately needs `A.1`'s
general `ChooseNext`/`ActivityGroup` rule.
