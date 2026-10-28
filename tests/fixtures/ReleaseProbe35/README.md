# ReleaseProbe35 — does `Phasing1`'s own subscriber-order swap reproduce when the same-node collision AND phasing are BOTH present together?

`ReleaseProbe23` (same-node third-block collision alone, no phasing)
and `ReleaseProbe27` (phase boundary shortly before the trigger fire,
no same-node collision) each isolated ONE of `Phasing1`'s own two
distinguishing conditions and real REFUTED both individually — row
order held in both cases. `Phasing1`'s own real instant
(`t=311.466386`) has BOTH conditions present SIMULTANEOUSLY: `Bus_UR`
independently fails at the same general moment as phasing is active,
chain-linked via the same EFPA node (`500path`) to both PM-trigger
subscribers. **Neither prior probe tested the combination.**

## Critical constraint followed

Built by cloning `tests/golden/Phasing3/input.rbd` wholesale (the SAME
base `ReleaseProbe27`/`31` used) — no `PHASES` section edit, no `P NNN`
column edit, no `NODE_DATA`/`HIERARCHY_DATA` edit. Only two
already-exercised scalar fields changed, both via `rbd_builder.py`'s
own API (never `load_rbd`/`save_rbd` directly): the `500W-PowerString`
trigger's own distribution (`Dist=8`→`FIXED(11.7)`, the SAME value
`ReleaseProbe27` already validated as landing shortly after a phase
boundary), and `Bus_UR`'s own fail distribution (`Dist=4`
`EXPONENTIAL(850.0)` → `FIXED(6.7)`, `FDist=19`).

## Why `Bus_UR FIXED(6.7)`, and an honest caveat about timing precision

`Bus_UR` starts `Cut` (dependency-driven) at kickoff and doesn't begin
accumulating its own operating life until it first transitions to
`Good`/active — which happens at a run-varying instant (`t≈4.9-5.1`
across runs, checked directly), NOT `t=0`. `6.7` was chosen so
`4.9-5.1 + 6.7 ≈ 11.6-11.8`, landing close to the trigger's own
`t=11.7`. **This is NOT a precise, guaranteed collision like
`ReleaseProbe23`'s own `FIXED` fail-time-on-a-simple-chain technique
achieved** — `Bus_UR` in `Phasing3`'s own real topology is ITSELF
subject to repeated phase-driven `Cut`/`Idle`/`Good` toggling BEFORE
its first failure (the same class of complexity `E.9`'s own
countdown-consumption-rate investigation grapples with), so its own
active-time accumulation is interrupted unpredictably run to run. Of 8
runs checked, only 3 (runs 0, 3, 5 — 0-indexed) land `Bus_UR`'s own
failure within ~0.1 units of the trigger fire (`t≈11.6` vs `11.7`); the
other 5 land much later (`t≈19-198`), effectively NOT testing the
combined scenario at all in those runs. **This fixture cannot
guarantee the collision the way earlier probes could** — a genuine
limitation, not a design defect; `Bus_UR`'s own timing in a real,
complex phased topology isn't fully controllable via a single `FIXED`
field the way an isolated minimal-chain block's is.

## What this port's own run actually does (runs 0, 3, 5 — the aligned ones)

Consistent across all three:
```
11.604747-11.640641  Block  Bus_UR         Failed  Operating_time=6.700000
[same instant]         Block  SolarP_500W    Idle    Dependence
[same instant]         Block  BatteriesNiCd  Idle    Dependence
11.700010              Trigger 500W-PowerString  Fired  System_event
11.700010               Block  SolarP_500W    PM_is_due  Triggered_PM_Due
11.700010               Block  BatteriesNiCd  PM_is_due  Triggered_PM_Due
11.700010               Block  SolarP_500W    PM         beginning_PM
11.700010               Block  BatteriesNiCd  PM         beginning_PM
```
**A THIRD, genuinely different pattern from either prior probe.**
`Bus_UR`'s own failure lands ~0.06-0.1 units BEFORE the trigger (not
simultaneous with it, given the timing imprecision above) — dep-idling
BOTH subscribers TOGETHER, before either one has a chance to commit its
own PM first. By the time the trigger fires 0.06-0.1 units later, BOTH
subscribers commit their PM in THE SAME instant, with NO differential
ordering at all (unlike `ReleaseProbe23`'s own "one wins outright,
other gets dep-idled AFTER" pattern, and unlike `ReleaseProbe27`'s own
identical "one wins outright" pattern too) — the SEQUENCE that produces
`ReleaseProbe23`/`27`'s own asymmetric split (one subscriber committing
before the OTHER's own dependency cascade even starts) doesn't occur
here, because `Bus_UR`'s OWN failure preempts the trigger, pre-emptively
idling both together first. `SolarP_500W`'s own line still appears
textually FIRST (row order, per this session's own established
line-order-is-authoritative methodology, §67.1) even in this
simultaneous-looking pair.

## The question

With BOTH conditions present (a same-node third-block failure landing
shortly BEFORE the trigger, plus active phasing), does real show this
SAME "both dep-idled together, then both commit simultaneously, row
order preserved textually" pattern, or does it show `Phasing1`'s own
ACTUAL asymmetric swap (`BatteriesNiCd` committing meaningfully BEFORE
`SolarP_500W`, not simultaneously)?

- **Matches this port's own prediction** (simultaneous commit, row
  order preserved) → the combined scenario still doesn't reproduce
  `Phasing1`'s own swap — worth checking whether `Phasing1`'s own REAL
  instant has `Bus_UR`'s failure landing AFTER the trigger fire instead
  of before (a genuinely different collision shape this probe's own
  timing imprecision couldn't target), or ultimately needs `A.1`'s
  general `ChooseNext` rule.
- **`BatteriesNiCd` commits meaningfully first** → the combination IS
  the distinguishing factor (neither condition alone is sufficient) —
  a genuine, novel, actionable finding.
- **Some other pattern** → note the full real event log around
  `t=11.5-11.8` precisely, and which of the 10 runs (if any) show
  `Bus_UR`'s own failure landing near the trigger fire at all (given
  this fixture's own timing imprecision, most runs may show NO
  meaningful collision — check `Bus_UR`'s own failure timestamps
  across all 10 runs first before reading anything into a run where
  they don't land close together).

## Predicted (this port)

See "What this port's own run actually does" above. Runs where `Bus_UR`
doesn't fail near `t=11.7` (5 of 8 checked) show `Phasing1`'s own clean
`ReleaseProbe27`-style pattern instead (`SolarP_500W` wins outright, no
`Bus_UR` involvement at that instant) — not a distinguishing data point
for this probe's own question, but recorded for completeness.

## How to capture it

Same procedure as the other `ReleaseProbeN` fixtures — launch Raptor7
Student with `/sd`, open this file, Simulate (10 runs, matching
`Phasing3`'s own count — more runs improve the odds of `Bus_UR`'s own
timing landing close to the trigger in at least a few). Read the event
log and `/sd` output around `SimTime=11.5-12.0` in EVERY run, and note
`Bus_UR`'s own failure timestamp specifically (compare against
`t=11.700010`, the trigger's own fixed fire time) to identify which
runs actually test the combined scenario. Real-Raptor report outputs
land in `~/Documents/Raptor7/` (`rpt_events_*` etc.);
the `/sd` copy goes to this repo's root as
`simDetails_ReleaseProbe35.txt`.

## REAL ANSWER (2026-09-04 capture, `~/Documents/Raptor7/rpt_events_ReleaseProbe35.txt`, 10 runs)

**The predicted alignment did NOT occur in any of the 3 predicted runs
(0, 3, 5) — `Bus_UR` never fails near `t≈11.6-11.7` in any of them.**
Checked all 10 runs' own early window (`t<15`): `Bus_UR` stays
`Good`/toggling normally through the trigger fire in runs 0-5, 7-9;
the ONLY same-instant collision across the entire batch is in **run 6**
(not predicted), where `Bus_UR Failed` lands at the EXACT trigger
instant (`t=11.700010`) — but with `Operating_time=4.148754`, well
short of the `FIXED(6.7)` parameter used to construct this probe.
Every LATER `Bus_UR` failure in the same run (checked across the full
`t≈0-2400` mission — Phasing3's own true `CycleSim` length, per
`ReleaseProbe32`'s own note) shows the FULL, exact
`Operating_time=6.700000` — the shortfall is specific to `Bus_UR`'s
own FIRST-ever failure of a run, before it has completed one full
uninterrupted operating cycle.

**Even in run 6's genuine collision, the outcome does NOT match this
probe's own predicted "combined" pattern** — it matches the SIMPLER,
already-established `ReleaseProbe27` row-order pattern instead:
```
11.700010  Block  SolarP_500W    PM_is_due  Triggered_PM_Due
11.700010  Block  BatteriesNiCd  PM_is_due  Triggered_PM_Due
11.700010  Block  SolarP_500W    PM         beginning_PM        <- row-first commits immediately
11.700010  Block  BatteriesNiCd  Idle       Dependence
11.700010  Block  BatteriesNiCd  PM         beginning_PM        <- row-second dep-idles then commits
11.700010  Block  Bus_UR         Failed     Operating_time=4.148754  <- same instant, doesn't change the order
```
No "both dep-idle together" pattern anywhere in this batch.

**Root cause of the missed alignment**: `Bus_UR`'s own fail-time
clock is NOT a simple uninterrupted wall-clock countdown from when it
first exits its initial `Cut` state, as this probe's own construction
assumed. `Bus_UR` toggles (`Cut`→`Idle`→`Good`, `DependenceStress=
0.5`) during the same early window `SolarP_500W`/`BatteriesNiCd` do,
driven by the identical shared-node dependency mechanics — and its own
`FIXED(6.7)` countdown appears to lose ground across those toggles the
same way `ReleaseProbe34`'s own real capture just showed `LowGain`'s
countdown does (E.9, reopened same day) — pushing its genuine first
failure LATER than a naive uninterrupted-countdown estimate in most
runs, landing exactly on the trigger only by chance in run 6, and even
then with a partial (not full-6.7) accumulated value. **This is likely
the SAME underlying mechanism as E.9's newly-reopened toggle-races-
expiry divergence**, not a separate bug — worth revisiting once that
item is diagnosed, rather than re-tuning this probe's own timing
further.

**For the original A.1/Phasing1 question**: this is now the THIRD
probe (`ReleaseProbe23` same-node, `ReleaseProbe27` phase-proximity,
this one's combined attempt) to fail to reproduce `Phasing1`'s own
subscriber-order swap — even a genuine accidental collision (run 6)
didn't produce it. This further strengthens the case that the swap
needs `A.1`'s own general `ChooseNext`/`ActivityGroup` rule, not a
probe-specific collision condition.
