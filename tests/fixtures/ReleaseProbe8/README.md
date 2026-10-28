# ReleaseProbe8 — parallel-sibling DEFERRED-PM at B's PM completion (§40 addendum pair, leg 2)

Probe-3's structure with C never failing but PM-triggered (ProbeTrig2,
t=3.6, pmReqDefer — Techs held by A's repair until t=25, PM dist
exp(10)). At B's PM completion (t=5) C is **up with a deferred PM**.

Separates "C up" (probe-7) from "C up + outstanding PM" — pins whether
the OTHER in-link's PM/deferred state enters B's downNum (the
"node-total MINUS other in-links' PMs" candidate rule, §40 addendum).

## Readout (B's /sd draw time)

- B redraw at **t=5** → Running: C's dp does NOT count.
- B redraw at **t=25** → Idle: C's dp counts.

Our port: B redraw **t=5** + C's PM draw at t=25 (Running — C's dp not
counted). Structurally this mirrors probe-5's real-answered
configuration on probe-3's frame.

## REAL ANSWER (2026-09-02 capture, legacy/)

B redraw at **t=5** — Running; C's PM draw at t=25. All 9 draws match
our port exactly (zero divergence). C's deferred PM does NOT count
toward B's downNum.
