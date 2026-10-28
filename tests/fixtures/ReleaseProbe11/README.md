# ReleaseProbe11 — the df-dep-Idle park WITHOUT the tie (§46.2's discriminator)

ReleaseProbe10 with BlockD's fail dist FIXED 0.5 instead of 1.0:
D fails 0.5, repairs to 3.5, fails again 4.0, repairs to 7.0 — the
second fail (and the S/N dep-Idle it causes) lands a FULL SECOND
before trigger T1's 5.0 fire. No calendar tie. Everything else
identical to probe-10 (two pmTrig subscribers S/N on SysStream 203,
both node-dep).

## The question

Probe-10's tie: real ABSORBED S's (row-first) PM wake and dispatched
N's; our port dispatched both. Is the absorption about the TIE, or
about waking a df-dep-Idle-parked block at all?

## Readout (t=5.0, real event log + /sd)

- BOTH `PM_is_due` lines + both PM-begins (like our port: S 0.602041,
  N 2.944303 first) → the tie IS the variable; absorption is
  tie-specific mechanics.
- N-only (S absorbed, like probe-10) → the absorption follows the
  df-dep-Idle park itself (S-vs-N asymmetry structural) — fixable in
  the park/wake path directly.
- NEITHER → waking a df-parked block absorbs all PM wakes (our port's
  dispatch is wrong for this whole class).

Our port: both dispatch (`S 0.602041, N 2.944303` at t=5.0; PM_completes
5.602041 / 7.944303).

## REAL ANSWER (2026-09-02 capture, legacy/)

**NO DIVERGENCE — all 18 draws match our port.** Real dispatches BOTH
PMs untied (`S PM_is_due, N PM_is_due, S beginning_PM, N beginning_PM`
at t=5.0, both PM_completes) — identical to our port. The absorption
in probe-10 is **tie-specific** (§46.2 candidate (c)): the df-dep-Idle
park alone does NOT absorb PM wakes. The tied-instant mechanism (why
probe-10's tie absorbed S's wake but dispatched N's — the S/N
asymmetry is still unexplained by any state rule) needs the real
source: `ControlPMtriggers` + the dep-idle
park delivery + MODSIM Interrupt's replace semantics, read side by
side with probe-10's captured log.
