# ReleaseProbe10 — trigger-fire TIED with a third block's failure (§45.3: the Phasing1 wake-order flip variable)

Phasing1's 500W-string in miniature. Chain `start→S→N→D→j1→end`:
BlockS and BlockN are the TWO `pmTrig=T1` subscribers (both node-dep
`DepNum=-1, DepIdle=100`, PM dists EXP(1.0)/EXP(2.0)), BlockD is the
chain's third block with NO PM (Bus_UR's role). All three share
SysStream 203 (Phasing1's own scheme — wake order = value assignment).

Timeline (deterministic): D fails t=1.0 (FIXED), repairs 3.0 (until
4.0, S/N dep-Idle then re-Good at 4.0), trigger T1 FIXED 5.0 — and D's
second fail (FIXED 1.0, entry pushed at its 4.0 recovery, AFTER the
trigger's kickoff entry) lands EXACTLY on the 5.0 fire: the calendar
tie. Pop order: trigger first (entry pushed at kickoff), then D's fail —
matching Phasing1 run 9's own tie (trigger entry from t≈23.34 older than
Bus_UR's fail entry from 309.306139).

## The question

Real Phasing1 run 9 (t=311.466386) woke BatteriesNiCd (row-second)
FIRST, while at both untied same-structure instants real followed row
order (SolarP_500W first). Is the TIE the flip variable?

## Readout (t=5.0 /sd, shared stream 203)

- `BlockS` PM value first → row order (no flip; the tie is NOT the
  variable — Phasing1's flip needs something in its longer history).
- `BlockN` PM value first → the flip REPRODUCES minimally: real's
  ControlPMtriggers wake order inverts when the fire ties another
  event.

Our port (fixed row order): `BlockS 0.602041, BlockN 2.944303` (S
first; S's PM consumes the earlier shared-stream U).

Event-log shape (our port, run 1): `T1 Fired → D Failed → S PM_is_due →
N PM_is_due → j1/end Bad → S Idle Dependence → N Idle Dependence → S
beginning_PM → N beginning_PM`.

## REAL ANSWER (2026-09-02 capture, legacy/)

**The tie reproduces a real divergence — but not the one hypothesized.**
Real run 1 event log at t=5.0: `T1 Fired → D Failed → nodes Bad → S Idle
Dependence → N Idle Dependence → N PM_is_due → N beginning_PM` — the
row-FIRST subscriber's (S's) PM wake is ABSORBED (no PM_is_due line, no
PM, no draw — all 3 runs; 12 draws = 3×[S@0, N@0, N@5, N@8]), while the
row-second subscriber's (N's) wake dispatches normally. Our port
dispatches BOTH blocks' PMs from the dep-Idle park (18 draws = 3×6).

Shared-stream proof S consumed nothing: our S's PM drew U→0.602041
(EXP mean 1.0); real's N's PM = 1.204082 = −2·ln(U) — the SAME U with
N's mean 2.0. Real's first post-kickoff stream consumer = N's PM.

So the waker's subscriber order is NOT pinned by this probe (both
delivery orders are consistent with S absorbing silently), but a REAL
engine difference is: **our port dispatches a Triggered_PM_Due wake at
a block dep-Idled by the tie's df cascade; real absorbs it** — while
Phasing1 run 9 shows real DISPATCHING the dep-Idled block's PM (SolarP
PM'd after its dp-cascade dep-Idle). Candidate discriminator: the
dep-Idle CAUSE (df-cascade park absorbs the wake; dp-cascade park
dispatches it) — untested. A ReleaseProbe11 variant with D failing
UNtied (t=4.5, fire 5.0) would separate "tie" from "df-dep-Idle-then-
wake" as the absorption variable.
