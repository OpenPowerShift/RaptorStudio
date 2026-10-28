# ReleaseProbe42 — WHICH recovery cascades wake a cold-standby block's parked fail-wait? (E.9 bug #2's discriminator, fully deterministic)

## The law this probe tests (decoded 2026-09-05 from the EXISTING CS_Probe/CS_SwitchFail captures — no new instrumentation)

E.9 bug #2 (the toggle-heavy countdown consuming life ~5.8x too fast;
`ReleaseProbe34`: real 49 fails vs port 7) finally cracked via an
Operating_time ACCRUAL DECOMPOSITION of the real captures. The
`Operating_time` at each real LowGain fail = the sum of the WALL
durations of the fail-wait waits that actually ENDED (each arm sets
TimeHack; each interrupt/natural end accrues `SimTime-TimeHack`).
Subset-summing the real state-segment walls against that total:

- **CS_SwitchFail run 1** (draw 44.047023 at t=2.0, fail 446.146564,
  `Operating_time=58.898959`): the post-Standby accrual 43.265343
  decomposes UNIQUELY as the Good walls of 263.420082→302.911996
  (39.491914) + 442.373135→446.146564 (3.773429). Consumption check:
  15.633617×0.05 (Standby) + 39.491914×1.0 + 3.773429×1.0 =
  **44.047024 = the draw, EXACT.**
- **CS_Probe run 1** (draw 7.486155 at t=2.0, fail 185.273503,
  `Operating_time=24.274384`): the post-Standby accrual 6.602564
  decomposes UNIQUELY as the FINAL Good segment 178.670939→185.273503.
  17.671820×0.05 + 6.602564×1.0 = 7.486155 EXACT.

**THE LAW**: the fail-wait consumes ONLY while the Run process is
actually in its armed accumulating wait — at sbStress during Standby,
at 1:1 (CombStress=1.0, confirmed by the real `StessLevel` lines:
only the t=0/t=2.0 Standby arms log ≠1.0) during armed Good segments
— and consumes NOTHING (no accrual either) while PARKED, including
through Good windows of the Idle/Good dep-flapping. The port's current
model (re-arm + consume 1:1 at EVERY recovery) is wrong in both
directions.

**THE WAKE RULE** (the open half): the parked process re-armed only at
recovery cascades originating from a block OUTSIDE the cold-standby
pair — CS_Probe: Attitude's repair @178.670939; CS_SwitchFail:
Bus_QR's repair @263.420082 and Attitude's postLDT @442.373135 — and
NEVER at the pair-mate's own repair cascades (HighGain End_Post-LDT →
LowGain Good, the 4.0-Idle round-trips: 7 windows in CS_Probe, ~15 in
CS_SwitchFail, zero consumption each). Source text says both routes
use the identical gate (`IF FailWait: Interrupt ELSIF ZeroWait:
ASK BlockTrigger[seqNum] TO Release`, /,
and BlockTrigger[seqNum] IS created per block) — so the binary diverges from its own source here
(consistent with the known `{cmc}` patching, cf. the system-status
appendix). This probe pins the rule deterministically.

## What this probe builds

`tests/golden/CS_Probe/input.rbd` byte-patched, three fields changed
(everything else — the failing switch, the Astronauts pool, the dep
wiring, the tied-triple bounces — is CS_Probe's own proven-real
structure):

1. `HighGain`: fail → `FIXED(20.0)` — fails at 20, 53, 86, ... (the
   Attitude cascades stretch the cycle to a deterministic 33-period
   after the first external event; the port's own run below is the
   exact baseline).
2. `LowGain`: fail → `FIXED(100.0)` — a raw life of 100, consumed at
   0.05 during Standby, so the fail instant directly encodes the arm
   schedule (FIXED dists consume no RNG draws).
3. `Attitude`: fail → `FIXED(30.0)`, repair → `FIXED(6.0)` (LDT 2.0 +
   1.0 kept) — recovers at 39, 78, 117, 156, 195, ... a deterministic
   external-wake source landing at known instants relative to
   LowGain's Idle/Good windows.

## The question — LowGain's first post-kickoff fail instant

Raw budget: 100.0, of which 20×0.05 = 1.0 consumed by the t=20
Standby exit. 99.0 remain. Each ARMED Good window consumes 1:1.

- **FAIL ≈ t=139** (port's own per-recovery law: Good windows [24,44],
  [48,68], [72,92], [96,116] consume 20 each, then 19 more into
  [120,140]) → real re-arms at EVERY recovery including the pair-mate's
  own — i.e. the source text is right and the CS captures' arm
  schedule came from something else entirely (would re-open §18hh).
- **NO FAIL** (or a fail decode showing only-external consumption
  totaling well under 99) → pair-mate recoveries genuinely never wake
  the parked wait; the wake rule keys on the cascade's ORIGIN (external
  route vs pair-mate route) — implement exactly that in the port.
- **A THIRD INSTANT** → decode it: the deterministic schedule makes the
  consumed-set recoverable from the fail instant alone (each armed
  window contributes its full wall; work backwards through the known
  Idle/Good windows). This outcome would pin the precise wake rule
  (e.g. "external wakes only when the block is currently Good" — note
  Attitude's 117 recovery lands INSIDE a LowGain-Idle window, so this
  probe also tests whether a wake arriving while Idle re-parks
  silently or arms late).

## Predicted (this port, current shipped code)

**No fail.** The t=20 switch failure sets `cold_path_done`
(E.9 bug #1's park) and LowGain's Run process freezes forever — 1
kickoff fail only, all runs. (This is the KNOWN masking; removing the
park is gated on this probe's law being implemented first.)

## Capture

Same procedure as every probe (`/sd`, Raptor7 Student under Wine, 10
runs — the file's own NumRuns). Event log + reports land in
`~/Documents/Raptor7/`, the `/sd` copy at this repo's
root as `simDetails_ReleaseProbe42.txt`. Readout: every
`LowGain ... Failed Operating_time=...` line per run (the kickoff
0.000020 fail is expected baseline). Run-count check from the event
log's `Simulation_Terminate` groups (the §70.4 lesson).

## REAL ANSWER (2026-09-06 capture, 10 runs) — all three predicted laws REFUTED; the structure it reveals

LowGain's first post-kickoff fail: run 1 at **358.100001**
(`Operating_time=117.099981`), run 3 at **300.100001** (same
Operating_time), run 2 at **470.025001** — none of {≈139, never,
external-decodable}. But the capture is a goldmine:

1. **Run 2 (and 4-10): the switch WORKS.** The 20.0 switch roll
   SUCCEEDS (`Good Prior=StandbyStress=1.000000` at 20.5 — LowGain
   PROMOTED), and LowGain then cycles Standby↔Good with the pair-mate
   (`Standby Too_many_on` demotions at 24/57/90/117...,
   `StessLevel CombStress=0.050000` at every STANDBY arm, silent 1.0
   arms at every Good arm) — **fully source-modelable**: Standby
   consumes at sbStress, Good at 1:1, Idle frozen, armed at every
   state transition. The AProb roll per attempt decides which shape a
   run takes (run 1/3: first roll FAILS at 20.0 → the link goes
   permanently dead → the CS-fixture shape; run 2/4-10: first roll
   SUCCEEDS → the promotion/demotion shape).
2. **Run 1/3 (switch dead): the parked-wake anomaly, now MEASURED.**
   After 20.0's dep-idle, the Idle→Good recoveries do NOT re-arm the
   process for MOST windows — but the raw budget (99.1 post-Standby)
   IS consumed exactly, and the accrual equals the consumption (both
   99.099981 ≈ 99.1): the consumed set is {Good windows starting
   78, 117, 209, 242, 273, 312, 330, 355} (8+2+16+20+20+14+16+3.1 =
   99.1 EXACT), starting at BOTH Attitude-recovery (78/117/273/312)
   AND HighGain-recovery (209/242/330) instants — so "external-only
   wakes" (the CS-fixture reading) is WRONG as a universal rule: in
   this fixture HighGain recoveries wake it too, and in CS_Probe/
   CS_SwitchFail they (and everything else) did NOT.
3. The difference between run 1's waking/non-waking recoveries does
   NOT correlate with the cascade's origin — the discriminating
   variable is still open. What IS now pinned: the consumption
   accounting (armed-vs-parked, the accrual=consumption identity),
   the Standby-rate law (0.05, logged), the Good-rate law (1:1), and
   the fact that the switch-live shape needs NO new mechanism at all.

**Next probe idea (for the wake rule):** R42's run-1 shape with the
Attitude route REMOVED entirely (delete the external block) — if the
consumed set then empties (no fail), the wake source is exclusively
external-route; if some HighGain-recovery windows still consume, the
rule depends on the process's own state (e.g. which wait site it
parked at — the Idle-park vs the accumulating-wait's own
interrupt-resume path) rather than the cascade's origin.

## Addendum (2026-09-06, post-R43): the accumulator arithmetic corrected + the probe question ANSWERED

Two corrections/completions from R43 (`ReleaseProbe43` — this probe's
run-1 shape with the Attitude route removed AND the switch made
deterministically dead):

1. **The accumulator arithmetic**: `Operating_time` carries the FULL
   WALL duration of every armed segment (source — the stress
   only scales the countdown consumption, not the accumulator). The
   fail's 117.099981 = the Standby arm [2.0→20.0]'s **full 18-wall**
   + the post-park armed wall **99.099981** (= exactly the raw
   remaining at the 20.0 park: the draw 100 minus 18×0.05=0.9). The
   "consumed window set" identification above survives; the
   Standby-rate conflation in the arithmetic is corrected.
2. **The probe question IS ANSWERED by R43**: with the external route
   removed, the consumed set EMPTIES (8/10 runs: zero consumption
   after the kickoff fail) — **the wake source is exclusively
   external-route**. The "HighGain-recovery windows" in this
   fixture's run-1/3 consumed sets were misattributed by me: those
   instants coincide with Attitude-cascade windows (the draws' /
   segments' timing was approximate); R43's cleaner run (no external
   route at all) shows the internal cascades NEVER wake the parked
   wait. The remaining fine structure (the exact wake sub-step inside
   the external cascade, and the 2.0-unit offset from the visible
   recovery in R43's runs-7/8) is recorded in R43's addendum.
