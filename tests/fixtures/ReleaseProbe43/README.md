# ReleaseProbe43 — with NO external wake source and a deterministically dead switch, does real's parked LowGain EVER consume again? (the `cold_path_done` park on trial)

E.9's two-bug structure: bug #1 (the port's `cold_path_done` park — a
block whose cold-standby switch ever rolled `"none"` parks its Run
process forever) and bug #2 (the toggle-heavy consumption rate). The
park was retained because removing it alone regressed
`CS_Probe_AProb099` — bug #2's faster consumption surfacing. R42's
real answer reframed everything: the parked-vs-armed consumption is
binary (armed-only), and the open question became THE WAKE RULE —
which cascades re-arm a parked fail-wait.

R42's run-1/3 (switch dead) consumed windows started at BOTH
Attitude-recovery AND HighGain-recovery instants — so "external-only"
was refuted as stated, but the runs also contained external cascades.
This probe removes the confound: **a world with NO external cascades
at all.**

## What's new here vs `ReleaseProbe42`

Two fields, everything else byte-identical:

1. `Attitude`: fail → `FIXED(1000000.0)` — it NEVER fails, so no
   Attitude-fail cascade, no end-node flip from its route, no external
   recovery cascade. The external wake source is ELIMINATED without
   touching the topology (its branch still exists and simulates —
   just inert).
2. `link 8`: `AProb 0.5 → 0.0` (ManTime already `-1.0` in R42) —
   source `SetSwitchTime`'s `autoSwitchProb=0.0` branch
  sets `switchStatus:="none"`
   deterministically on EVERY attempt: the switch is dead from the
   first attempt in EVERY run (R42's roll-dependent per-run shapes
   are gone — every run is the run-1/3 dead-link shape).

Result: at t=20.0 HighGain fails, the switch fails
(`Switch_Failed Unable_to_switch_to_LowGain`), LowGain dep-idles,
recovers per the cascade, and — per the port — its Run process parks
via `cold_path_done` forever. **Nothing else can ever happen to
LowGain: no external cascades exist.**

## The question

Does real's LowGain fail AT ALL after its kickoff `0.000020` fail?

- **NO — zero further LowGain fails in all 10 runs** → with no
  external wake source, the parked process NEVER consumes again:
  real's wake rule is at minimum external-involving, and the port's
  `cold_path_done` park is CORRECT in this shape — E.9 bug #1
  dissolves (the park isn't wrong, it's the wake rule's no-internal-
  wake consequence), and the E.9 fix reduces to bug #2's rate law
  alone.
- **YES — fails appear at deterministic instants** → internal
  (pair-mate or self) wakes DO re-arm the parked process even in a
  no-external world: the park is WRONG even here, and the fail
  instants DECODE the internal wake set exactly (same arithmetic as
  R42: consumed windows from the Operating_time totals). The likely
  internal candidates: HighGain's own recoveries (24/57/90... from
  its FIXED(20)/4.0 cycle) and the Phase_change stress-refresh wake
  every ~4-5 units (PhasingDemo's D000-060 cycles) — wait, this
  fixture has NO phases (CS_Probe base) — so the candidates are
  HighGate's recovery cascades and the Inspection event's periodic
  fires.
- **Fails appear ONLY in some runs** → the per-run AProb-class draws
  (Inspection's Bernoulli, PowerSupply's Custom-sparing rolls)
  modulate the wake rule — decode which.

## Predicted (this port, current shipped code)

One LowGain fail per run (the kickoff `0.000020`), then the
`cold_path_done` park: **no further LowGain activity at all**. The
HighGate cycle (fails at 20/53/86... — wait, no: with the switch dead
at 20.0, HighGate's own cycle continues: fails 20, 53, 86, ... with
LDT/repair — those cascades bounce LowGain Idle/Good per the dep
wiring (the port's cascade applies the state changes) but the Run
process stays parked (no consumption).

## Capture

Same procedure (`/sd`, Raptor7 Student under Wine, 10 runs — the
file's NumRuns). Event log + reports land in
`~/Documents/Raptor7/`, the `/sd` copy at this repo's
root as `simDetails_ReleaseProbe43.txt`. Readout: every
`LowGain ... Failed` line per run (the kickoff `0.000020` expected),
plus the FIRST `Switch_Failed` instant per run (must be 20.000000 —
if not, the link patch didn't load as intended). Run-count check from
the event log's `Simulation_Terminate` groups.

## REAL ANSWER (2026-09-06 capture, 10 runs) — the park is CORRECT; the wake rule confirmed external-route; one residual fine-structure

**8 of 10 runs: exactly ONE LowGain fail (the kickoff `0.000020`) —
the parked process NEVER consumes again.** The port's prediction
matches byte-for-byte in those runs. **The `cold_path_done` park is
CORRECT in the no-external-wake world** — E.9 bug #1 dissolves: the
park isn't wrong, it IS the wake rule's no-internal-wake consequence.
Real's LowGain still FLAPS Idle/Good per the dep cascades (HighGain's
own fails at 20/53/86... → Comm Bad → end Bad → `LowGain Idle
Dependence` → recoveries), and those cascades do NOT re-arm the parked
fail-wait — confirming the CS-fixture no-accrual law in its purest
form (no external route exists here at all).

**Runs 7/8: ONE extra fail** at 524.100001 / 548.100001 with
`Operating_time=117.099981` — the IDENTICAL accumulator to R42's
run-1/3 first fails (the same armed-window arithmetic). The trigger:
run 7's Attitude dep-roll at t=44 (0.9889 → ×100 = 98.89 > 90 →
**InducedFailure**; run 8's t=20 roll = 92.0 → same) — the 10%
DepFail band landing. Attitude's induced failure flips `end` via its
OWN branch cascade → the EXTERNAL route exists again in exactly those
runs → the parked LowGain woke and consumed. The 8/10 vs 2/10 split is
fully explained by the per-run dep-roll stream.

**The wake rule, final form**: a parked fail-wait re-arms ONLY on
external-route cascades (end-node flips originating OUTSIDE the cold
subsystem — an Attitude-fail-class event); pair-mate/internal cascades
(HighGain's own fails → Comm settles → end flaps → LowGain's own
Dependence_Idle dispatches) NEVER re-arm it. The port's unconditional
`cold_path_done` park must become: parked, WAKABLE by external-route
dispatches, re-parking on internal ones.

**Residual fine-structure (open)**: the runs-7/8 re-arm instant
computes to t≈408.0 (524.1 − 116.1 armed wall) — and run 7's event log
has NO line at 407-409 (the next event after 112.2's boundary cluster
sits at 419). The wake's exact trigger within the Attitude-cascade
chain (which sub-step of the induced-failure cascade re-arms, and why
the arithmetic lands at an unlogged instant) is not yet decoded — the
armed-wall bookkeeping may include a subtlety (the Standby-rate arms,
or an invisible re-arm). Also open: WHY real's Operating_time
(117.099981) exactly equals R42's run-1/3 value — the same armed-window
set implies the same wake schedule modulo the fixture deltas, which
the current R43/R42 wake inventories don't yet reconcile line-for-line.

**Engine consequence**: the port's fix shape is now concrete — the
`cold_path_done` parked loop (`block_lifecycle.py`'s `while True:
yield _FAR_FUTURE`) must accept interrupt deliveries: wake + consume
on external-route dispatches, stay parked on internal ones. The
route discriminator is the open piece (the reason strings are
identical — "Dependence_Idle" — for both routes; the dispatcher's
identity (end vs Comm) or the cascade context must thread through).

## Addendum (same day): the accumulator arithmetic corrected — the wake lands at t=425.0

`Operating_time` at the runs-7/8 fail (117.099981) decomposes as the Σ
of FULL ARMED WALLS (the source's `OperatingTime:=SimTime-TimeHack+
OperatingTime` accumulates the wall duration of every armed segment,
regardless of stress — the stress only scales the COUNTDOWN consumption,
not the accumulator): the kickoff Standby arm [0→0.000020] = 0.00002 +
the post-recovery Standby arm [2.000020→20.0] = 18 wall + the post-park
armed wall = 99.099981 → total 117.100001 ✓ (fp-tolerant). The
post-park armed wall 99.099981 = EXACTLY the raw remaining at the
20.0 park (the draw 100 minus the Standby-rate consumption 18×0.05 =
0.9) — the process woke, consumed its remaining life 1:1, and failed:
**the wake landed at t=524.100001 − 99.099981 = 425.00002**.

425.0 sits 2.0 units after the visible 423.0 recovery
(`LowGain Good DependenceStress` — the end-Good backflow at HighGain's
own repair completion) and has NO log line. The same arithmetic corrects
R42's README (its decomposition used the stress-scaled Standby
consumption 0.9 where the accumulator carries the full 18-wall — the
99.1 "consumed window set" there is the post-park armed wall, and
18+99.1 = 117.1 ✓ — R42's and R43's accumulators agree because the
pre-park structure is identical).

The 2.0 offset from the 423.0 recovery is unexplained (LowGain's own
LDT is 0.5+0.5; HighGate's repair-completion cascade settles at 423.0).
The wake-rule HEADLINE is unchanged: no-external → 8/10 runs zero
consumption; the Attitude InducedFailure cascades (runs 7/8) open the
only wake windows. What remains open is the fine bookkeeping of the
wake instant inside the recovery cascade, not the rule itself.
