# ReleaseProbe12 — THREE pmTrig subscribers under the tie (the wave rule's shape)

ReleaseProbe10 with a third subscriber: chain `start→S→N→E→D→j1→end`
(BlockE Id 7, PM dist EXP(3.0), same node-dep + pmTrig=T1 config,
appended LAST in row/registration order). Same tie: D's second FIXED
fail lands on T1's 5.0 fire; the df cascade dep-Idles ALL THREE
subscribers before their queued wakes deliver. All blocks share
SysStream 203.

## The question

Probe-10 (two subscribers): real ABSORBED S's (row-first) PM wake and
dispatched N's; our port dispatched both. With three subscribers the
dispatch pattern pins the rule:

- S parks, N dispatches, E dispatches → "first-wave-absorbs, the rest
  dispatch" (a wave-position rule).
- ONLY E dispatches (S, N park) → one-waiter-per-fire (LIFO
  semaphore) — the last registrant wins.
- S, N, E all park → the df-dep-Idle park absorbs every queued wake
  (probe-10's N dispatch would need another cause).
- S, N, E all dispatch → the absorption needs exactly two (unlikely)
  or the tie's shape matters differently.

## Readout (t=5.0 real event log + /sd)

Count the `PM_is_due Triggered_PM_Due` lines and the /sd PM draws at
t=5.0. Our port: all three dispatch (`S 1.472151, N 0.666435,
E 7.346580` at t=5.0; PM_completes at +1.47/+0.67/+7.35).

## REAL RAPTOR CRASHES ON THIS FILE (2026-09-03) — not a probe bug, a genuine reference-implementation limitation

`input.rbd` loads and displays fine, but **Simulate crashes real Raptor
with `MODSIM Runtime error: Access Violation`** — a native memory
fault inside Raptor's own binary, not a file-format rejection.
Confirmed the crash is specifically about THREE blocks sharing one
trigger and firing at the exact same instant (not just having a 4th
block in the model): `input_nosub.rbd` (identical file, `BlockE` no
longer a `pmTrig` subscriber — an ordinary block in the same chain)
**runs clean**. So real Raptor's own `ControlPMtriggers`/`BlockTrigger`
machinery (§48.1's per-subscriber `TELL METHOD` process family, a
MODSIM runtime built-in with no source body for its
release/queueing internals) appears to have an actual capacity/
robustness limit around 3+ simultaneous same-trigger releases — this
is a bug or undocumented limit in the REFERENCE implementation, not
something fixable by editing the `.rbd`.

**Consequence for this whole investigation thread**: there is no valid
real answer to compare against for a genuine 3-way simultaneous tie —
real crashes instead of producing one. `ReleaseProbe12`'s own design
(a true 3-way tie) is UNRUNNABLE, not just unrun. Chasing "does our
port match real's 3-way tie behavior" is a dead end: real doesn't have
one. The `ChooseNext`/wake-order investigation (§45-48, `engine-gap-
analysis.md`) needs to stay within what real Raptor can actually
survive — 2-subscriber ties (`ReleaseProbe10`/`11`, both successfully
captured) are apparently the practical ceiling for this exact
mechanism family. A future probe wanting a THIRD same-instant
competitor should use a DIFFERENT kind of entry (e.g. a third block on
an unrelated wake, not a third `pmTrig` subscriber on the SAME
trigger) to avoid retriggering this crash.
