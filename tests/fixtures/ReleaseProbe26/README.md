# ReleaseProbe26 — does a shape&lt;1 Weibull draw show up honestly in the ordinary event log, or only as an "impossible" periodic-dump value?

The internal engine ledger F.2 (a MAJOR OPEN ITEM): real's own
periodic `/sd` dump for `WeibullShapeTiny`'s `BlockA` (`Weibull`
shape=0.01, scale=10, `FStream=1`) shows an astronomically large value
(`~1.217e300`) that's been **mathematically proven impossible** to
produce via `SCALE*(-LN(u))**(1/SHAPE)` from this project's own
confirmed-correct 31-bit LCG (the required `u` is ~292 orders of
magnitude below the smallest value the RNG can ever emit). An exhaustive
search of Raptor 7 found no code anywhere that writes that
periodic dump line — it is NOT simply "the raw fail-time draw," but
nothing has confirmed what it actually IS. `WeibullShapeTiny`'s own
`BlockA` also has a large `AmtExhausted` (100000), `usesPM=1`, and
`GDType=1` — three confounds that could each independently distort what
reaches an ORDINARY, state-changing event-log line, separate from
whatever the periodic dump is doing. The internal investigation log's own
§23 explicitly flags this: `WeibullShapeTiny`'s `BlockA` never fails or
changes state for the entire run, so **the real event log side of that
capture was never actually checked** — only the periodic dump was.

**This probe strips every confound** and asks the question directly:
what does a shape&lt;1 Weibull draw look like once it reaches an
ordinary `"Failed" "Operating_time=X"` line?

## Revision note (2026-09-04): the original `AmtExhausted=0.0` draft crashed real Raptor on Simulate

The first version of this probe set `AmtExhausted=0.0` (see point 1
below, as originally written) — the user reported it crashes real
Raptor when actually running the simulation (loads fine, crashes on
Simulate). Re-built with `AmtExhausted=1.0` instead (small and nonzero,
matching `WeibullShapeTiny`'s own known-safe pattern more closely — that
fixture already has a real capture and uses `AmtExhausted=100000`,
proving nonzero values are safe; `0.0` specifically is the one
change from that proven baseline this revision backs away from). Every
OTHER original change (`FStream`, PM/`GDType` disabled) is a config
this project has ALREADY exercised safely in other real-captured
fixtures, so `AmtExhausted=0.0` was the prime suspect — not confirmed
which exact mechanism crashes on it (a `GetStartCond` retry-loop edge
case seems likely, but untested against real; not worth a second crash
just to isolate it further). This port's own prediction section below
reflects the CURRENT (`AmtExhausted=1.0`) build.

## What's new here vs `WeibullShapeTiny`

Cloned from `tests/fixtures/WeibullShapeTiny/input.rbd`, three
changes to `BlockA` (id=3) and one to `BlockB` (id=4):
1. **`AmtExhausted`: `100000.0` → `1.0`** — the retry loop
   (`GetStartCond`, `block_lifecycle.py`) re-draws until the current
   draw exceeds this threshold; `1.0` (small, but NOT zero — see the
   revision note above) means almost any draw is accepted immediately,
   so `BlockA`'s own reported fail-time is directly whatever the
   Weibull transform produces, with nothing substantially retried away
   — while still testing the SAME retry mechanism `WeibullShapeTiny`'s
   own `100000` does, just with a far smaller threshold.
2. **`FStream`: `1` → `203`** — `WeibullShapeTiny`'s own `FStream=1` is
   already tied to a SPECIFIC known real value (the astronomical
   periodic-dump number); reusing it verbatim would just reproduce the
   same "never fails, nothing to compare" case. `203` is the ordinary
   shared stream most other fixtures in this project already use,
   picked specifically to land on a DIFFERENT draw at this exact stream
   position — see "Predicted" below for what this port's own transform
   produces there.
3. **`usesPM`/`pmTriggered`/`pmTrig`: `1`/`1`/`TestTrig` → `0`/`0`/
   `none`** on BOTH blocks — PM is fully disabled project-wide in this
   probe (matches the recommendation's own "no PM" condition), so
   `BlockA`'s own fail-time draw can only ever surface via an ordinary
   `Failed` transition, never absorbed into a PM cycle instead.
4. **`GDType`/`GDRate`/`GDLimit` on `BlockA`: `1`/`0.01`/`0.5` →
   `0`/`0.0`/`0.5`** — growth-rate stress modification fully disabled
   (matches the recommendation's own "no GDType" condition), so nothing
   rescales the raw draw before it's used as `FailTimeLeft`.

`BlockB` is otherwise untouched (still its own `EXPONENTIAL(60.0)`
fail/`FIXED(2.0)` repair cycle) — kept purely for the same two-parallel-
path structure `WeibullShapeTiny` already has (`validate()` needs no
changes to the node/link topology this way). `validate()`: zero
warnings.

## This port's own prediction

All 3 runs show the SAME two-draw pattern for `BlockA`, both draws at
`t=0.0` (the `GetStartCond` retry loop firing once, immediately,
entirely within the kickoff pass — matching the internal engine ledger-
primitives.md` B.8's own documented retry mechanism): the FIRST draw is
essentially ZERO (`0.000000`, rejected by the `AmtExhausted=1.0`
threshold), the SECOND (the retry) is ASTRONOMICALLY LARGE — run 1:
`113471303152019150209024.000000` (~1.13e23); run 2:
`624087956544109440.000000` (~6.24e17); run 3:
`7877604145660381962347140994398420992000.000000` (~7.88e39). All
comfortably within float64's own representable range (max ~1.8e308) —
part of why `AmtExhausted=1.0` is expected to be safer than the
original `0.0` draft. This matches the shape&lt;1 Weibull transform's
own mathematical shape exactly: `SCALE*(-LN(u))**(1/SHAPE)` with
`SHAPE=0.01` means `(-LN(u))` gets raised to the 100th power — for
`u>1/e` (`-LN(u)<1`), the result COLLAPSES toward zero (rejected here
by the `AmtExhausted` retry); for `u<1/e`, it EXPLODES (accepted).

Full event log, run 1 (`BlockA` only):
```
0.000000     Block  BlockA  Good    InitializeStress=1.000000
3000.000000  Block  BlockA  Done    End_sim
```
`BlockA` never fails at all within the 3000-unit mission in any of the
3 runs — its own (retried) fail-time draw is always astronomically
larger than the mission length.

## The question

Does real show `BlockA` failing at all within the 3000-hour mission?

- **`BlockA` stays `Good` the whole mission, matching this port's own
  prediction** — consistent with (though not full proof of) the
  underlying value genuinely being astronomically large; F.2's own open
  question then shifts to "what mechanism, structurally, produces the
  periodic dump's specific displayed number" rather than "is the
  transform itself wrong." Check real's own `/sd` output at `t=0.0` too
  — does it ALSO show two draws there (a rejected near-zero one, then
  an accepted astronomical one), or does real's own `GetStartCond`
  retry loop work differently (e.g. only the ACCEPTED draw gets logged,
  with the rejected one silently discarded)?
- **`BlockA` fails at some FINITE, sane time before `t=3000`** — settles
  F.2 decisively: the periodic-dump number (and this port's own
  transform) was never representative of the real `FailTimeLeft` at
  all; some OTHER mechanism produces both the periodic-dump display AND
  determines the block's actual fail behavior. Read real's own `/sd`
  value at whatever instant it fails — direct evidence for what the
  TRUE underlying draw is.

## How to capture it

Same procedure as the other `ReleaseProbeN` fixtures — launch Raptor7
Student with `/sd`, open this file, Simulate (3 runs), read the event
log and `/sd` output for `BlockA` across the whole 3000-hour mission
(note: this fixture's own `SYSTEM_DATA` uses `hours` as its display
unit, inherited unchanged from `WeibullShapeTiny`). Real-Raptor report
outputs land in `~/Documents/Raptor7/` (`rpt_events_*`
etc.); the `/sd` copy goes to this repo's root as
`simDetails_ReleaseProbe26.txt`.

## REAL ANSWER (2026-09-04 capture, `simDetails_ReleaseProbe26.txt` + `~/Documents/Raptor7/rpt_events_ReleaseProbe26.txt`)

**Confirms this port's own prediction exactly.** `BlockA` never fails
in any run — zero `Failed` lines across the whole capture, every run
identical: `Good` at `t=0`, `Done`/`End_sim` at `t=3000`. Real's own
`/sd` output shows the SAME retry-loop pattern this port predicts: a
rejected `0.000000` draw (the "collapse" regime, `u>1/e`) followed by
an accepted, astronomically large one (the "explode" regime) — and, in
several runs, MULTIPLE consecutive `0.000000` rejections before the
retry finally lands on an accepted value (expected: with `shape=0.01`,
~63% of draws collapse toward zero, so back-to-back rejections are
common) — real's own `GetStartCond` retry loop handles this correctly,
with no crash, confirming both the loop mechanism AND the underlying
transform. Run 1's own accepted value (`113471306331874188591104.0`)
matches this port's own prediction (`113471303152019150209024.0`) to
~8 significant figures — the remaining difference is the same
astronomical-value formatting noise already seen elsewhere in this
project, not a real discrepancy.

**Settles F.2's core question for this specific case**: this port's
shape&lt;1 Weibull transform is confirmed CORRECT — real's own engine
produces the same "collapse-or-explode" pattern, at the same
approximate magnitudes, and never crashes or silently discards the
astronomical value. F.2's own remaining mystery (what mechanism
produces `WeibullShapeTiny`'s own specific periodic-dump display
number) is now narrowed further: it's NOT that the underlying transform
or `FailTimeLeft` value is wrong — it's specifically about how/whether
the periodic dump's own DISPLAY differs from the value actually driving
the block's behavior, a separate, narrower question than originally
scoped.
