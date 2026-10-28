# ReleaseProbe7 — parallel-sibling UP at B's PM completion (§40 addendum pair, leg 1)

Probe-3's exact structure (start→A→B→join(k1,n2: B,C)→end, A fails t=3
repairs 22h, B's triggered PM completes t=5) with ONE change: BlockC
never fails (FIXED 1e6). At B's PM completion the join holds
A df + B dp + C **Running**.

Isolates whether the parallel sibling's mere presence/state (up) affects
B's maintenance-completed downNum branch — probe-3 (C **failed**) gave
real Idle; probe-5 (C on the join but up-with-deferred-PM — its file has
no join2, C feeds the SAME join) gave real Running.

## Readout (B's /sd draw time)

- B redraw at **t=5** → Running (self-correcting): C's up doesn't count.
- B redraw at **t=25** → Idle (direct, parked): sibling presence counts.

Our port: B redraw **t=5** (Running — downNum=1; A's df not counted, C's
up not counted).

## REAL ANSWER (2026-09-02 capture, legacy/)

B redraw at **t=5** — Running. All 6 draws match our port exactly
(zero divergence). C's up does NOT count toward B's downNum.
