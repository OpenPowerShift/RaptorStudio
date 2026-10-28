# ReleaseProbe34 — does the toggle rate law hold when the countdown ALSO races its own natural expiry?

`ReleaseProbe25`'s own real capture SOLVED E.9's pure-toggle case:
6,257 of 6,261 total event-log lines matched this port's own run
exactly across all 10 runs, confirming the current `elapsed*stress`-
decrement-on-wake, frozen-while-genuinely-parked countdown model —
but that probe deliberately stretched `LowGain`'s own mean fail time
to `10000.0` (far past the 730.5-unit mission) specifically so it
would NEVER actually fail, isolating pure toggling from the countdown
ever reaching zero. Its own README named the exact remaining gap:
"does the SAME rate law still hold once the countdown is ALSO racing
its own natural expiry mid-toggle-sequence... repeat this same design
with a SHORTER mean fail time (~100-300, long enough for many toggles,
short enough to likely fail within the mission)."

**This probe is that follow-up.**

## What's new here vs `ReleaseProbe25`

Two fields changed from `tests/golden/CS_Probe/input.rbd` (the SAME
base `ReleaseProbe25` used), both on `LowGain`:
1. **`FParam1`** (mean fail time, `EXPONENTIAL` distro): `20.0 →
   15.0` — NOT stretched upward like `ReleaseProbe25`, shortened
   instead, specifically to let the countdown actually reach zero
   within the mission.
2. **`AmtExhausted`**: `2000.0 → 0.0` — `CS_Probe`'s own original
   value (`2000.0`) was tuned for a mean of `20.0`; combined with a
   SHORT mean, `GetStartCond`'s own retry-until-exceeds-`AmtExhausted`
   loop would force the FIRST accepted draw deep into the
   distribution's own tail (order of several thousand, nowhere near
   the nominal mean) — confirmed directly: leaving `AmtExhausted`
   unchanged at a short mean produced ZERO fails in 10 runs even at
   `FParam1=100`, because the retry loop was silently defeating the
   whole point. Zeroing it removes that confound (matches this
   project's own established "`AmtExhausted=0.0` is a no-op for the
   retry gate" finding from elsewhere in this codebase).

Everything else — `HighGain`'s own repeating fail/repair cycle (still
what DRIVES `LowGain`'s toggling), the mission length (730.5),
`NumRuns=10`, `LowGain`'s own `SBStress=0.05` — stays byte-identical to
`CS_Probe`'s own already-captured, already-real scenario, same as
`ReleaseProbe25`.

**Why `15.0` specifically**: tried `100`, `200`, `300` first (closest
to the recommended range) — ALL three produced ZERO fails across 10
runs (the block spends most of the mission Standby-PARKED, where the
countdown is frozen, so its own accumulated ACTIVE exposure across the
whole 730.5-unit mission stays well under even a fairly short mean).
Swept down through `90`, `70`, `50`, `30`, `25`, `20`, `15`, `10`:
`15.0` gives the richest mix — 4 of 10 runs show at least one genuine
`LowGain Failed` event (one run shows THREE separate fail/repair
cycles), while EVERY run still accumulates 31-41 clean toggles (healthy
pure-toggle data, consistent with — and an independent second
confirmation of — `ReleaseProbe25`'s own already-solved case). `10.0`
gave slightly more frequent fails but several landed before the FIRST
toggle ever happened (no accumulated toggle history to test against,
less diagnostic for the compounding question specifically).

## The question

For the 4 runs (2, 3, 7, 8) where this port's own prediction shows
`LowGain` genuinely failing: does real ALSO fail at the SAME wall-clock
instant, in the SAME run? This is the single most direct test possible
of the compounding countdown-consumption rate — every already-refuted
candidate law (whole-remainder rescale, uniform wall-clock rate, the 4
telescoping variants) predicts a DIFFERENT accumulated life-consumption
total by the time several toggles have compounded, so a genuine fail
instant is a hard, unambiguous number to check against every one of
them at once (unlike `ReleaseProbe25`'s own "never fails" case, which
could only confirm the model, never actively falsify a competing one).

Two independent readings from the SAME capture:
1. **Do the SAME 4 runs fail, at the SAME instants?** — the current
   model (already confirmed for pure toggling) is ALSO correct when
   the countdown races its own expiry; E.9 is then fully closed.
2. **Do OTHER runs fail that this port predicts survive (or vice
   versa), or do the SAME runs fail but at DIFFERENT instants?** — the
   compounding rate itself is still wrong even though the pure-toggle
   case checks out; the specific WRONG instant (early or late relative
   to this port's own prediction) directly measures which direction
   the true rate law diverges, a strong new constraint for re-deriving
   it.

## Predicted (this port)

All 10 runs, fail instants and toggle counts:
```
run 0: no fail, 31 toggles
run 1: no fail, 35 toggles
run 2: fails at t=20.401777, 37 toggles (up to that point + after repair)
run 3: fails at t=4.888061, then AGAIN at t=18.506320, 41 toggles
run 4: no fail, 32 toggles
run 5: no fail, 34 toggles
run 6: no fail, 31 toggles
run 7: fails at t=12.859625, t=41.459447, t=73.745922 (THREE cycles), 32 toggles
run 8: fails at t=12.278013, 39 toggles
run 9: no fail, 39 toggles
```
Run 7's own full pattern (richest — 3 fail/repair cycles, then settles
into pure toggling for the rest of the mission) is illustrative:
```
0.000000    LowGain  Good     InitializeStress=1.000000
0.000000    LowGain  Standby  Too_many_on
2.284348    LowGain  Good     Prior=StandbyStress=1.000000
5.784348    LowGain  Standby  Too_many_on
12.859625   LowGain  Failed   Operating_time=12.859625      <- 1st fail
12.859625   LowGain  RepHold  LDT
...
14.859625   LowGain  Good     Stress=1.000000
16.922035   LowGain  Idle     Dependence                    <- toggling resumes
20.422035   LowGain  Good     DependenceStress=1.000000
...
41.459447   LowGain  Failed   Operating_time=16.061779      <- 2nd fail
...
73.745922   LowGain  Failed   Operating_time=30.286476      <- 3rd fail
...
76.684220   LowGain  Standby  Too_many_on
86.299055   LowGain  Idle     Dependence                    <- from here: pure toggling,
...                                                             ~30 more toggles, never fails again
730.500000  LowGain  Done     End_sim
```

## How to capture it

Same procedure as `ReleaseProbe25`/other `ReleaseProbeN` fixtures —
launch Raptor7 Student with `/sd`, open this file, Simulate (10 runs),
read the FULL event log for `LowGain` and `HighGain` across all 10
runs (budget for ~700 lines per run in the fail-rich runs, similar
scale to `ReleaseProbe25`). Real-Raptor report outputs land in
`~/Documents/Raptor7/` (`rpt_events_*` etc.); the `/sd`
copy goes to this repo's root as `simDetails_ReleaseProbe34.txt`.

## REAL ANSWER (2026-09-04 capture, `~/Documents/Raptor7/rpt_events_ReleaseProbe34.txt`)

**Reading 2 (the falsifying one) — the compounding rate law is WRONG.**
Real fails MUCH more often than this port predicts: 49 genuine
`LowGain Failed` events across the 10 runs (only run 5 has zero),
vs this port's own 7 events across 4 runs:
```
run   real fails   this port's fails
 0        3              0
 1        2              0
 2        1              1  (different instant: t=32.85 vs t=20.40)
 3        8              2  (different instants entirely)
 4        3              0
 5        0              0  (only run where both agree)
 6        8              0
 7        8              3  (different instants entirely)
 8        9              1  (different instant: t=92.69 vs t=12.28)
 9        7              0
```
Real fails ~7x more total instants than this port predicts, spread
across the WHOLE `730.5`-unit mission (not clustered early); this
port's own predicted fails are all early (`t<75`) since its own
`FParam1=15.0`-mean exponential draws rarely survive that long once
toggling is added on top. Line-for-line comparison confirms the
divergence starts exactly at each run's OWN first fail instant, not
before — every toggle event up to that point matches real byte-for-
byte (confirmed for run 0 up through `t=261.807039`, the last shared
event before real's own first `LowGain Failed` at `t=293.750648`;
this port's own run 0 shows NO fail at all in that window, diverging
immediately once real's countdown reaches zero and this port's own
internal countdown hasn't).

**This falsifies the current `elapsed*stress`-decrement-on-wake model
for the toggle-races-expiry case**, even though `ReleaseProbe25`'s own
real capture already confirmed it EXACTLY for the pure-toggle case
(life never reaching zero). The two cases must be exercising different
code paths or a different edge of the same mechanism — the pure-toggle
case never needs the model to get the ABSOLUTE remaining-life value
right (only relative pause/resume), while this case does, and gets it
wrong.

**Root cause found the same day — TWO bugs stacked.** Bug #1:
`block_run_process`'s own `cold_path_done` permanent park (triggered
by `Node ... Switch_Failed`, which this fixture's own `LowGain` hits
at `t=29.2858` in every run) is unconditionally wrong — its own
justifying comment claimed real freezes a block's life forever once
its cold-standby switch fails, but directly re-reading `tests/golden/
CS_SwitchFail`'s own real capture REFUTES that: `LowGain` there fails
again at `t=446.146564`, consuming the SAME raw draw across ~216 more
wall-clock units of heavily-diluted toggling, then 27 more times after
that. Removing this park entirely closes MOST of this probe's own gap
(7→28 of 49 real fails reproduced) but exposes bug #2. Fixing bug #1
alone is net-negative (regresses `tests/golden/CS_Probe_AProb099`) —
reverted; both bugs need fixing together.

**Bug #2 diagnosed further — NOT a dilution-rate/stress bug, NOT
cold-standby-specific.** `LowGain`'s own logged `Operating_time` at
failure is IDENTICAL between real and this port (same raw draw, same
accumulated running+standby total) — the divergence is entirely in how
much genuinely-Idle time occurs in between: this port shows `LowGain`
running straight through an uninterrupted 34-unit stretch with no
toggle at all, while real interrupts it into `Idle` repeatedly across
that same stretch. Confirmed on `Attitude` itself (unrelated block,
same `DepNum=-2` pattern, no cold-standby link): real shows 86
Idle/Good toggles before its own first failure, this port shows only 3
— same shape of divergence on a completely different block.

**Two hypotheses tested, BOTH REFUTED.** (1) `end` node's own cascade
simply stopping propagation — checked directly, `end`'s own Bad/Good
status keeps flipping correctly for the whole mission, tracking
`Comm`'s aggregate exactly. (2) The C.7 `downNum` mechanism
(`repair_completion_status`) incorrectly parking `HighGain` via a
shared EFPA-link slot with `LowGain` (permanently `Switch_Failed`) —
instrumented directly, REFUTED: `Comm`'s own node has genuinely
SEPARATE per-path array slots, and the down_num≤1 self-correcting path
fires correctly every time. **Instead, a genuinely puzzling correlation**:
real shows `HighGain`'s own recovery from `Idle` landing at the EXACT
SAME instant as `Attitude`'s own repair completion, repeatedly, across
multiple runs — despite `Attitude`'s own RBD branch having no
traceable structural connection to `Comm`/`end`/`HighGain`. Both DO
share the `Astronauts` resource pool — a resource-release-triggered
`resend_back_flow` timing effect is the leading untested hypothesis.

**E.9 is only PARTIALLY solved — reopened for the toggle-races-expiry
case.** Next step for bug #2: instrument `resend_back_flow`/pool
`release()` directly to check whether `Attitude`'s own repair-
completion (releasing 3 `Astronauts` units) is what triggers the
re-check that promotes `HighGain` out of its post-repair `Idle` park in
real, and whether this port's own `release()` wires that up the same
way. Full diagnosis: the internal engine ledger E.9. Deliberately
NOT attempted further this session — Tier-A cascade/resource code,
matching this project's own "trace precisely before blind-fixing"
discipline.
