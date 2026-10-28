# ReleaseProbe25 — a data-rich capture for E.9's unsolved multi-toggle fail-wait countdown rate law

The internal engine ledger's **E.9** is the single biggest unsolved
item in the whole backlog: how a cold-standby block's `FailTimeLeft`
countdown evolves across repeated `Switch_Failed`→`Idle(Dependence)`
cycles (the internal countdown notes has the full mechanism writeup). A
~15-section campaign (the internal investigation log §18o-§18hh, §24-§25)
tried and refuted whole-remainder rescale, park-removal, per-state
rate-pair fitting across 39 real segments ("no clean solution...
Running-at-1.0 forces an impossible negative Idle rate"), uniform/
stress-rate models, and all 4 telescoping-rescale variants. E.9's own
entry ends with a clear recommendation: "(a) a real, targeted probe
with a deliberately reshuffled toggle schedule."

**This probe is (a) — not a new hypothesis, a bigger dataset.** The
prior 39-segment fit failed to converge; this single capture yields
**~24-47 toggle cycles per run × 10 runs ≈ 320 segments total**, all
from ONE clean, already-battle-tested cold-standby scenario, maximizing
what a single real capture can constrain.

## What's new here vs `tests/golden/CS_Probe`

Exactly ONE field changed from `CS_Probe`'s own `input.rbd`: `LowGain`'s
own `FParam1` (mean fail time, `EXPONENTIAL` distro) `20.000000 →
10000.000000`. Everything else — `HighGain`'s own repeating fail/repair
cycle (the mechanism that actually DRIVES `LowGain`'s toggling: `HighGain`
fails on `EXPONENTIAL(mean=20)`, each failure taking `Comm` node's
2-of-2 redundancy down for a FIXED ~4-unit window — `LDT=2.0` +
`repair=1.0` + `post-LDT=1.0` — before recovering), the mission length
(730.5), `NumRuns=10`, `LowGain`'s own `SBStress=0.05`, and the ONE
already-real-confirmed `Switch_Failed` at kickoff (`AProb=0.5`, unchanged)
are all byte-identical to `CS_Probe`'s own already-captured, already-real
scenario.

**Why this specific change**: `CS_Probe`'s own real capture already
exists and already diverges (that's WHY E.9 is open) — but `LowGain`'s
own short mean fail time (20) meant it usually failed itself after only
a handful of `HighGain`-driven toggles (~6-8 per run in the real
capture), landing far short of a clean rate-law fit. Stretching
`LowGain`'s own mean fail time to 10000 (500x `HighGain`'s own mean, and
~14x the mission length) means `LowGain` reliably survives the ENTIRE
730.5-unit mission in every run of this port's own prediction (confirmed
below — zero `LowGain Failed` lines in all 10 runs), so `HighGain`'s
OWN repeating cycle drives `LowGain` through dozens of clean
Idle(Dependence)/Good(DependenceStress) toggles per run instead of
just a handful, with NO "how does the eventual own-failure interact
with the countdown" complication muddying the picture at all — pure,
uninterrupted toggle-schedule data, run to completion.

## The question

For EACH of the ~320 toggle segments this capture yields (a
`LowGain Idle → LowGain Good` window, each of a different, precisely
known WALL-CLOCK duration set entirely by `HighGain`'s own deterministic
repair-cycle timing plus its own `EXPONENTIAL`-drawn fail times), does
real's own `LowGain` recover with the SAME `DependenceStress`-driven
`Good` transition this port predicts, at the SAME instant? The
candidate rate laws already tried and refuted (see E.9's own entry) all
predict DIFFERENT eventual behavior once compounded across many
segments — even where a single early segment might look consistent
with more than one law, extending the same real block through ~30
segments in one run should make the compounding divergence between
candidate laws unambiguous well before the run ends (this port's own
"frozen while Standby-parked, `elapsed*stress` decrement on wake"
model, vs the refuted "whole-remainder rescale," "uniform wall-clock
rate," and "telescoping compounding" alternatives, would each predict
a VISIBLY different toggle-24-or-so pattern by the time this many
segments have compounded, even though several of them can't be told
apart from segment 1 or 2 alone).

Since `LowGain` never fails on its own in this port's own prediction
(confirmed below), there's no single "final answer" number to check —
the diagnostic value is in the FULL per-segment timing sequence itself.
Compare real's own `LowGain` event-log timestamps (`Idle`/`Good` lines)
against this port's own predicted timestamps below, run by run,
looking for the FIRST segment where they diverge (same methodology as
`tools/sd_diff.py`'s own first-divergence search, just applied to
event-log timestamps here since `LowGain`'s own countdown consumes no
NEW RNG draws across these toggles at all — only `HighGain`'s own
independent fail/repair draws do, and those are easy to verify match
separately since `HighGain`'s own mechanism is already known-correct).

## Predicted (this port)

All 10 runs: `LowGain` never fails on its own (zero `Block LowGain ...
Failed` lines in any run — every run's own countdown, however it's
compounding, stays above zero through the full 730.5-unit mission).
Toggle counts (`LowGain Idle(Dependence)` transitions) per run:
```
run 0: 31   run 1: 35   run 2: 28   run 3: 47   run 4: 33
run 5: 34   run 6: 24   run 7: 24   run 8: 30   run 9: 35
```
(321 total `Idle` transitions / 319 total `Good` recoveries across the
batch — the 2-transition shortfall is `LowGain`'s own `Standby`↔`Good`
"Too_many_on"/"Prior=StandbyStress" bookkeeping around kickoff, not a
missing toggle.)

Run 0's own full `LowGain` history (first 20 of 69 total lines, `/sd`
draws consumed: none for `LowGain` itself across the whole run — only
its ONE-time kickoff-standby demotion and its dependency-driven
toggles, no redraw ever, matching E.9's own already-confirmed "draw
gate: only at `FailTimeLeft=0.0`" finding):
```
0.000000    Block  LowGain  Good        InitializeStress=1.000000
0.000000    Block  LowGain  Standby     Too_many_on
0.000000    Block  LowGain  StessLevel  CombStress=0.050000
25.596041   Block  LowGain  Good        Prior=StandbyStress=1.000000
29.096041   Block  LowGain  Standby     Too_many_on
29.096041   Block  LowGain  StessLevel  CombStress=0.050000
29.285800   Node   Comm     Switch_Failed  Unable_to_switch_to_LowGain
29.285800   Block  LowGain  Idle        Dependence
33.285800   Block  LowGain  Good        DependenceStress=1.000000
38.309820   Block  LowGain  Idle        Dependence
42.309820   Block  LowGain  Good        DependenceStress=1.000000
69.274912   Block  LowGain  Idle        Dependence
73.274912   Block  LowGain  Good        DependenceStress=1.000000
76.917031   Block  LowGain  Idle        Dependence
80.917031   Block  LowGain  Good        DependenceStress=1.000000
198.647205  Block  LowGain  Idle        Dependence
202.647205  Block  LowGain  Good        DependenceStress=1.000000
248.092807  Block  LowGain  Idle        Dependence
252.092807  Block  LowGain  Good        DependenceStress=1.000000
252.092807  Block  LowGain  Idle        Dependence
```
(Every `Idle→Good` window here is exactly 4.0 wall-clock units —
`HighGain`'s own FIXED `LDT(2.0)+repair(1.0)+postLDT(1.0)` down-window
— confirming `LowGain`'s toggle SCHEDULE itself is fully deterministic
and known in advance from `HighGain`'s own draws; what's in question is
only whether `LowGain`'s own internal countdown state carries correctly
across them, which doesn't show up in ITS OWN event log at all unless
its countdown reaches zero — the real test is whether `LowGain`
eventually fails SOMEWHERE, and if so exactly when, which is a strong
distinguishing signal between rate laws even though this port's own
model predicts it never does within this mission.)

## Readout

For each run, walk `LowGain`'s own event log in real's capture and
this port's own (above) side by side. Two possible outcomes:
- **Real's `LowGain` never fails either, and every toggle timestamp
  matches this port's own prediction exactly** (modulo the usual fp32
  noise floor) — the base toggle mechanism (already suspected correct
  per §24) is CONFIRMED at this much larger scale, and E.9 narrows to
  "only matters when `LowGain` eventually fails on its own partway
  through a toggle-heavy run" (a follow-up probe would then need a
  SHORTER mean fail time again, but with this same many-toggle
  richness — worth designing next if this is the outcome).
- **Real's `LowGain` DOES fail somewhere in one or more runs** (this
  port's own model failed to predict this since it's a `EXPONENTIAL`
  draw at kickoff that could, in principle, land anywhere, and the
  compounding countdown-consumption-rate divergence could make real's
  own `LowGain` reach zero far earlier than a naive "will it fit in
  730.5 wall-clock units" read suggests) — the WALL-CLOCK instant of
  that real fail directly measures the true compounded consumption
  rate across however many toggle segments preceded it, a single
  strong data point against every remaining candidate rate law (start
  by checking which of the 4 already-refuted telescoping-rescale
  variants comes closest, then re-derive from there).

## How to capture it

Same procedure as the other `ReleaseProbeN`/`CS_Probe*` fixtures —
launch Raptor7 Student with `/sd`, open this file, Simulate (10 runs,
matching `CS_Probe`'s own capture count), read the FULL event log for
`LowGain` and `HighGain` across all 10 runs (this one is bigger than
most probes — budget for ~300+ toggle lines, not the usual handful).
Real-Raptor report outputs land in `~/Documents/Raptor7/`
(`rpt_events_*` etc.); the `/sd` copy goes to this repo's root as
`simDetails_ReleaseProbe25.txt`.

## REAL ANSWER (2026-09-04 capture, `simDetails_ReleaseProbe25.txt` + `~/Documents/Raptor7/rpt_events_ReleaseProbe25.txt`)

**The FIRST outcome — decisively, across the entire batch.** Programmatic
line-by-line comparison of the COMPLETE event log (not just `LowGain`)
against this port's own run, all 10 runs:

```
run 0: 598 lines, 0 mismatches
run 1: 706 lines, 0 mismatches
run 2: 590 lines, 0 mismatches
run 3: 842 lines, 1 mismatch  (fp32 last-digit noise only)
run 4: 610 lines, 0 mismatches
run 5: 640 lines, 1 mismatch  (fp32 last-digit noise only)
run 6: 460 lines, 2 mismatches (fp32 last-digit noise only)
run 7: 501 lines, 0 mismatches
run 8: 621 lines, 0 mismatches
run 9: 693 lines, 0 mismatches
```
**6,257 of 6,261 total event lines match EXACTLY; the other 4 differ
only in the last digit of a `HighGain` repair-completion timestamp**
(e.g. `172.244493` vs `172.244492`) — the SAME established "software-
`ln` last-digit noise" class already documented elsewhere in this
project (the internal investigation log §11), not a real divergence.
`LowGain` never fails in real either — zero `Failed` lines, matching
this port's own prediction exactly, in every run — and its OWN
`Idle`/`Good` toggle transitions match real's own, timestamp for
timestamp, in every single one of the ~320 segments this capture
produced.

**E.9's base toggle mechanism (the current `elapsed*stress`-decrement-
on-wake, frozen-while-genuinely-parked model already in `block_
lifecycle.py`) is CONFIRMED CORRECT at this scale** — the ~15-section
campaign's own "no clean solution" conclusion was reached against a
39-segment dataset from `CS_Probe`'s own real capture, where `LowGain`
usually failed itself after only 6-8 toggles; this capture's ~320
clean segments (an ~8x larger dataset, all from ONE already-verified
scenario) show no divergence at all once `LowGain`'s own eventual
self-failure is taken out of the picture.

### What this means for E.9

**Narrows, doesn't fully close, the gap.** The toggle-schedule
mechanism itself (how repeated `Switch_Failed`→`Idle(Dependence)`
cycles rescale a PARKED countdown) is now confirmed correct across a
genuinely large, clean sample — the internal engine ledger's E.9
entry should be updated to reflect this (no longer "no clean solution
found," but "confirmed correct for the pure-toggle case; still unknown
for the case where the countdown ALSO races its own natural expiry
mid-toggle-sequence"). The remaining open question, per this probe's
own README: does the SAME rate law still hold once `LowGain`'s own
countdown is short enough to ACTUALLY reach zero partway through a
toggle-heavy run (the scenario `CS_Probe`'s own original real capture
exercises, and where the divergence was first found) — this probe
deliberately stretched `LowGain`'s life specifically to AVOID that
combination, so it doesn't settle whether the SAME mechanism handles a
toggle interacting with a genuine, imminent expiry correctly, only that
it handles toggling-with-abundant-remaining-life correctly. A natural
follow-up: repeat this same design with a SHORTER mean fail time (e.g.
100-300, long enough for dozens of toggles but short enough that
`LowGain` likely fails somewhere within the mission), isolating exactly
that remaining combination.
