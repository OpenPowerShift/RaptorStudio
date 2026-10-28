# ReleaseProbe23 — does a same-node THIRD block's independent same-instant failure flip PM-trigger subscriber wake order?

`ReleaseProbe22`'s real capture (the internal investigation log §67)
confirmed row/ID/declaration order is the true rule for an ordinary,
unraced trigger fire. But `Phasing1`'s own real capture (`t=311.466386`)
shows `BatteriesNiCd` committing PM BEFORE `SolarP_500W` — the opposite
of row order — at an instant where `SolarP_500W`, `BatteriesNiCd`, AND
a THIRD block, `Bus_UR`, are all chain-linked via EFPA to the SAME node
(`500path`), and `Bus_UR` independently FAILS at the exact same
`SimTime` the trigger fires. `ReleaseProbe22` deliberately removed this
exact collision to isolate the ordinary case first (see its own README,
"What's new here vs `ReleaseProbe10`", point 3 — `BlockD`'s own fail
time was shifted specifically to AVOID landing on `T1`'s own `5.0`
fire).

**This probe reintroduces that collision, and nothing else.**

## What's new here vs `ReleaseProbe22`

Exactly ONE field changed: `BlockD`'s own `FParam1` (fail time)
restored `0.400000 → 1.000000` — `ReleaseProbe10`'s ORIGINAL value,
which `ReleaseProbe22` deliberately moved away from. Everything else —
`ZLast`/`AFirst`'s own dependencies (removed, `DepNum=0` on both, no
G.3-style absorption race in play), `T1`'s `FIXED(5.0)` fire, both
subscribers' PM distributions/streams (`EXP(1.0)`/`EXP(2.0)`, stream
203) — is byte-identical to `ReleaseProbe22`.

With `FParam1=1.0`, `RParam1=3.0` (repair, unchanged, `Fixed(3.0)`),
`NumRes=0` (no resource, no LDT delay — `BlockD`'s own fail/repair
cycle is pure, uninvolved with D.3's own resource-grant territory):
`BlockD` fails at `t=1.0`, repairs at `t=4.0` (`1.0+3.0`), fails AGAIN
at `t=5.0` (`4.0+1.0`) — landing EXACTLY on `T1`'s own fire. `BlockD`
sits chain-linked downstream of both `ZLast`/`AFirst` on the SAME EFPA
node (`start→ZLast(3)→AFirst(4)→BlockD(6)→j1(5)→end`, unchanged from
`ReleaseProbe10`/`22`) — the SAME "third block, same node, independent
same-instant failure" shape as `Phasing1`'s own `Bus_UR`. `BlockD` has
`DepNum=0` (no dependency of its own either) — its failure is genuinely
independent, matching `Bus_UR`'s own real capture (an ordinary `Failed`
line, not a dependency-cascade effect).

Confirmed via this port's own run: `BlockD Failed` lands at
`t=5.000000`, the SAME instant as `T1 Fired`, in all 3 runs — the
collision reproduces exactly as designed.

## The question

At the `t=5.0` three-way collision (`T1` fires, `ZLast`/`AFirst` both
become PM-due, `BlockD` independently fails), which subscriber commits
its own PM FIRST?

- **`ZLast` first** (row order holds, matching `ReleaseProbe22`'s clean
  case) → `Bus_UR`'s own same-instant failure is NOT what causes
  `Phasing1`'s swap — the same-node-collision hypothesis is REFUTED.
  The real explanation lies elsewhere in `Phasing1`'s own specific
  topology/config (worth checking `Phasing1`'s own phasing/mission
  mechanics next, or ultimately `A.1`'s general `ChooseNext` rule) —
  this simpler probe deliberately has NO phasing at all, so if row
  order holds here, phasing itself becomes a live suspect.
- **`AFirst` first** (row order FLIPS, matching `Phasing1`'s own swap
  direction) → CONFIRMS the same-node-collision hypothesis: a
  same-node third block's own independent same-instant failure changes
  which trigger subscriber commits first. A real, reproducible
  mechanism — the natural next step would be tracing exactly HOW
  (analogous in spirit to D.3's own same-node discriminator, §64-65,
  though the underlying primitive is different — trigger fan-out, not
  a resource-pool grant).
- **Some other pattern** (e.g. both PM_is_due lines land but only ONE
  commits, absorption-style — genuinely G.3 territory) → note the full
  real event log at `t=5.0`; would mean this collision shape triggers
  G.3's OWN mechanism rather than a pure ordering question, worth
  re-reading G.3's own absorption rule (the internal engine ledger)
  against this exact log before concluding anything new.

## Predicted (this port)

All 3 runs, `t=5.0` (identical shape every run — `BlockD`'s own cycle
is fully deterministic, `FIXED` distributions):
```
4.000000  Block  BlockD   Repaired   MaintTime=3.000000
4.000000  Block  BlockD   Good       Stress=1.000000
...
5.000000  Trigger  T1      Fired            System_event
5.000000  Block    BlockD  Failed           Operating_time=1.000000
5.000000  Block    ZLast   PM_is_due        Triggered_PM_Due
5.000000  Block    AFirst  PM_is_due        Triggered_PM_Due
5.000000  Block    BlockD  Repairing        No_resources_used
5.000000  Block    ZLast   PM               beginning_PM
5.000000  Block    AFirst  PM               beginning_PM
```
`ZLast` first, `AFirst` second — this port's `BlockD`'s own independent
failure does NOT disturb row order at all currently (no dependency
links either subscriber to `BlockD`, so its cascade never touches
them). `/sd` draws (run 1): `ZLast 0.602041`, `AFirst 2.944303` —
identical to `ReleaseProbe22`'s own draws (a built-in continuity check;
`BlockD`'s `FIXED` distributions consume no RNG draws at all).

## Readout (t=5.0 event log + /sd)

Order the `PM_is_due`/`"beginning_PM"` line pairs and the two
`SimTime=5.000000` `/sd` lines for `ZLast`/`AFirst`. Also note `BlockD`'s
own exact line position relative to them (does it log `"Failed"` before,
between, or after the two subscribers' own lines?) — real's own
placement of `Bus_UR Failed` at `t=311.466386` (per `Phasing1`'s
capture, sandwiched between the two subscribers' `PM_is_due` lines and
their `beginning_PM` lines) is itself informative, not just "who's
first."

## How to capture it

Same procedure as the other `ReleaseProbe*` fixtures — launch Raptor7
Student with `/sd`, open this file, Simulate (3 runs, matching
`ReleaseProbe10`/`22`'s own capture count), read the event log and
`/sd` output around `SimTime=5.0`. Real-Raptor report outputs land in
`~/Documents/Raptor7/` (`rpt_events_*` etc.); the `/sd`
copy goes to this repo's root as `simDetails_ReleaseProbe23.txt`.

## REAL ANSWER (2026-09-04 capture, `~/Documents/Raptor7/rpt_events_ReleaseProbe23.txt`)

**`ZLast` first, `AFirst` second — in all 3 runs**, identical shape to
`ReleaseProbe22`'s own clean case:
```
5.000000  Trigger  T1      Fired            System_event
5.000000  Block    ZLast   PM_is_due        Triggered_PM_Due
5.000000  Block    AFirst  PM_is_due        Triggered_PM_Due
5.000000  Block    ZLast   PM               beginning_PM
5.000000  Block    AFirst  PM               beginning_PM
```
`BlockD`'s own independent failure lands at the exact same instant in
all 3 runs (`5.000000 Block BlockD Failed Operating_time=1.000000`,
sandwiched between the trigger fire and the two `PM_is_due` lines) —
the collision reproduces exactly as designed, and row order STILL
holds regardless.

**The same-node-collision hypothesis is REFUTED.** `BlockD`'s own
same-instant, same-node independent failure does NOT flip subscriber
wake order — this port's own prediction was correct. `Phasing1`'s own
real divergence (`BatteriesNiCd` before `SolarP_500W`, `t=311.466386`,
§67) is NOT explained by `Bus_UR`'s own same-instant failure after all.
Per this probe's own README, the next suspect is `Phasing1`'s own
phasing/mission mechanics specifically (this probe deliberately has NO
phasing at all, unlike `Phasing1`) — or, failing that, `A.1`'s general
`ChooseNext`/`ActivityGroup` rule. The internal engine ledger's G.2
entry updated to record this second refutation.
