# ReleaseProbe29 — does a cold-standby link's reentrant switch-interrupt collision really produce duplicate log lines/extra RNG draws in real, or only in this port?

The internal engine ledger A.4: when a spawned cold-standby switch is
immediately interrupted by a reentrant same-instant toggle, this port's own
cold-logic path (`_run_cold_logic_now`/`_Cascade.apply_cold_logic`) re-runs
as a genuinely separate top-level pass 2-3 times where real produces only
1 — confirmed via the internal investigation log §25/§27 (direct
instrumentation showing 3 separate `apply_cold_logic(Comm)` calls at one
instant; a real-capture RNG-draw-position comparison against
`CS_NoAutosFirst`'s own real capture confirming 2 genuine extra `Uniform(0,1)`
draws land at the SAME desynchronizing instant). Two narrow fixes were tried
and had zero effect; the entry's own conclusion is this needs a broader
architecture change that risks regressing the whole cold-standby family, so
nothing has been attempted since. The entry's own recommended next step:
**`AProb` strictly between 0 and 1** (not the deterministic 0/1 extremes
already exhausted), isolating a single clean episode instead of continuing
to read `CS_Probe_AProb1`'s own noisy, already-exhausted capture.

## Why this is a SAFE construction, given this session's own crash history

Two probes crashed real Raptor this session (`ReleaseProbe26` on Simulate,
`ReleaseProbe27` on Open/Simulate) — both from unfamiliar file-format/
extreme-parameter territory this project hadn't exercised in a
real-captured fixture before. **This probe takes the opposite approach**:
it is a single-field clone of `tests/fixtures/CS_NoAutosFirst/input.rbd`
— a file that has ALREADY been real-captured successfully in this project
(`tests/golden/CS_NoAutosFirst/legacy/`), with `AProb=0.5` ALREADY present
on the exact link this question is about (link 8, `LowGain`→`Comm`, `ColdPri
=0.98`, `AutoTime=3.0` — all pre-existing, already-proven-safe values, not
new ones). **No topology, node, hierarchy, or link structure is touched at
all** — only `LowGain`'s and `HighGain`'s own fail-time distributions
(already `EXPONENTIAL` in the base file) are shortened, purely to make the
target collision likely EARLY and OFTEN within a short mission, instead of
relying on chance (the base file's own real capture only showed one clean
instance, in run 3, at kickoff — per §27).

## What's new here vs `CS_NoAutosFirst`

Two changes, both to existing `EXPONENTIAL` fields (no new columns, no
structural edits):
1. **`LowGain`'s own `FDist`/`FParam1`**: `EXPONENTIAL(mean=20)` →
   `FIXED(6.0)`.
2. **`HighGain`'s own `FDist`/`FParam1`**: `EXPONENTIAL(mean=20)` →
   `FIXED(4.0)`.
3. **Mission length** (`SYSTEM_DATA` `TermCondition`): `730.5` → `100.0`
   (shorter, since the target collision now happens repeatedly within the
   first ~20 units — no need for the original fixture's own much longer
   mission).

Both blocks' own OTHER fields (repair distros, `SBStress=0.05` for
`LowGain`, dependency wiring, the `Astronauts` resource pool, `Comm`'s own
`Cold=1`/`k=1,n=2` node config) are completely untouched from the
already-proven base file. `validate()`: zero warnings (clean).

## Confirmed via direct instrumentation: this port's own execution reliably reproduces the target oscillation

Patched `_Cascade.apply_cold_logic` directly to count calls per
`(run, sim_time)` across 5 runs — repeated calls at a SINGLE instant
(the exact "3 calls where real makes 1" signature A.4 describes) appear
in **every one of the 5 runs**, typically several times per run (e.g. run 1:
2 calls at `t=12.5`, 4 calls at `t=16.0`; run 3: 3 calls at `t=48.0`, 6 calls
at `t=48.58`). The event log itself shows the exact symptom directly — run
1's own `t=16.0` burst logs `"LowGain Good DependenceStress=1.000000"` /
`"LowGain Standby Too_many_on"` / `"LowGain Idle Dependence"` and `"Node
Comm Good"`/`"Degraded"`/`"Idle"` TWICE each, back to back, at the identical
timestamp — a duplicated cascade, not a single clean settle.

## The question

For EACH of the several same-instant collision episodes this capture
produces (there are many — walk the FULL event log for repeated same-
timestamp `Comm`/`LowGain` line bursts, not just one), does real show the
SAME duplicated burst this port predicts, or does real settle in ONE clean
pass?

- **Real shows the SAME duplicated bursts, same line count, at the same
  instants** → this port's own oscillation is CORRECT after all (surprising
  given A.4's own prior finding from `CS_NoAutosFirst`'s own real capture —
  worth re-checking that finding's own methodology if this is the result).
- **Real shows ONE clean pass at every episode this port duplicates**
  (matching A.4's own existing finding) → confirms the bug generally,
  across MANY fresh episodes in one clean capture instead of the one
  instance previously found — directly supports the "queued interrupt
  synchronicity" hypothesis (§25's own candidate explanation: source's own
  `Interrupt(link,"PerformSwitch")` may resolve SYNCHRONOUSLY, before a
  same-instant Idle sub-transition's own link-status recompute runs, while
  this port's queued `scheduler.interrupt` semantics let that recompute see
  a stale, not-yet-resolved value).
- **Some episodes match, others diverge** → informative on its own: would
  suggest the bug is condition-dependent (e.g. only fires for a SPECIFIC
  kind of reentrant toggle, not every one) — note precisely WHICH episodes
  diverge and what's different about them.

## Predicted (this port)

See "Confirmed via direct instrumentation" above for the mechanism; full
run 1 event log around `t=12.0-16.0` (one representative episode):
```
12.000000  Block  HighGain  Failed    Operating_time=4.000000
12.000000  Block  HighGain  RepHold   LDT
12.000000  Node   Comm      Bad       0/2_df15_dsw8
12.500000  Block  LowGain   Good      Prior=StandbyStress=1.000000
12.500000  Node   Comm      Degraded  1/2_df15_up8
12.500000  Block  LowGain   Idle      Dependence
12.500000  Node   Comm      Idle      0/2_df15_di8
14.000000  Block  HighGain  End_Pre-LDT  Time=2.000000
14.000000  Block  HighGain  Repairing    1Astronauts
15.000000  Block  HighGain  Repaired     MaintTime=1.000000
15.000000  Block  HighGain  RepHold      postLDT
16.000000  Block  HighGain  End_Post-LDT  Time=1.000000
16.000000  Block  HighGain  Good      Stress=1.000000
16.000000  Block  LowGain   Good      DependenceStress=1.000000
16.000000  Block  LowGain   Standby   Too_many_on
16.000000  Node   Comm      Good      1/2_up15_s8
16.000000  Block  LowGain   Idle      Dependence
16.000000  Node   Comm      Degraded  1/2_up15_di8
16.000000  Block  HighGain  Idle      change_block_state
16.000000  Block  HighGain  Good      DependenceStress=1.000000
16.000000  Block  LowGain   Good      DependenceStress=1.000000    <- duplicate burst starts
16.000000  Block  LowGain   Standby   Too_many_on
16.000000  Node   Comm      Good      1/2_up15_s8
16.000000  Block  LowGain   Idle      Dependence
16.000000  Block  HighGain  Idle      Dependence
16.000000  Node   Comm      Idle      0/2_di15_di8
```
This exact "settle, then re-settle a second (or third) time at the SAME
instant" pattern recurs at multiple points across all 5 runs — see the
instrumentation counts above for the full list of `(run, t)` collision
instants to check.

## How to capture it

Same procedure as the other `ReleaseProbeN` fixtures — launch Raptor7
Student with `/sd`, open this file, Simulate (5 runs), read the FULL event
log for `Comm`/`LowGain`/`HighGain` (this mission is short, ~100 units, but
has MANY collision episodes to check — don't stop after the first one).
Real-Raptor report outputs land in `~/Documents/Raptor7/`
(`rpt_events_*` etc.); the `/sd` copy goes to this repo's root as
`simDetails_ReleaseProbe29.txt`.

## REAL ANSWER (2026-09-04 capture, `simDetails_ReleaseProbe29.txt` + `~/Documents/Raptor7/rpt_events_ReleaseProbe29.txt`)

**Mostly a strong confirmation — 4 of 5 runs match this port's own
prediction essentially perfectly** (programmatic line-by-line diff,
matching this session's own established methodology):
```
run 0: 379/379 lines, 0 mismatches
run 1: 387/387 lines, 0 mismatches
run 2: 180/180 lines, 1 mismatch (fp32 last-digit noise only, 98.381591 vs .590)
run 3: 426 real / 433 ours lines -- a REAL divergence, see below
run 4: 399/399 lines, 0 mismatches
```

### Run 3's divergence — refines A.4's own characterization

At `t=48.579125`, **real itself already shows the `LowGain`/`Comm`/
`HighGain` settle-sequence duplicated once** (the exact same 4-line
cluster — `LowGain Standby`/`Comm Good`/`HighGain Idle Dependence`/
`Comm Idle` — appears twice back-to-back in the REAL capture too, not
just in this port's own run). **This port then produces a spurious
THIRD occurrence of the same cluster that real does not have** —
confirmed via direct side-by-side inspection of the full window
(`t=48.0-49.1`), not just the line-count delta.

This REFINES A.4's own documented framing (the internal engine ledger:
"this port produces 3 top-level `apply_cold_logic` calls where real
produces 1") — real is NOT perfectly clean here either; it genuinely
duplicates once. The actual gap is narrower than previously stated:
**this port produces one MORE duplication than real's own genuine
double**, not three-where-real-has-one. This is still a real,
confirmed divergence (this port's own extra pass is spurious and
should be eliminated), but the target behavior to match is "real's own
double," not "a single, non-duplicated call" — worth correcting the
mental model before attempting any fix, since aiming for zero
duplication would itself be wrong.

The other 4 runs (0, 1, 2, 4) never hit this exact collision shape at
all (or hit it in a form both this port and real already handle
identically) — consistent with A.4's own note that the bug is specific
to a reentrant same-instant toggle, not a general cold-standby issue.
The internal investigation log/the internal engine ledger's A.4
entry should be updated to reflect this narrower, more precise
characterization before any fix is attempted.
