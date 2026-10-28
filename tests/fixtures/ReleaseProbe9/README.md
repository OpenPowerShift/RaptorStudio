# ReleaseProbe9 — UPSTREAM chain-last FAIL at B's PM completion (the NiCd/Bus_UR dimension)

The one downNum dimension no earlier probe covers. Chain
start→A→B→D→join(k1,**n1**)→end (B middle, D chain-last), D fails t=3.5
repairs 22h (FIXED, no resource), A never fails (isolation), B's
triggered PM completes t=5. Mirrors Capacity1's NiCd instant: NiCd
(middle) completes PM while Bus_UR (chain-last, own out-link = 500path's
in-link) is failed — real gave **Idle** (downNum=2: Bus_UR's df + NiCd's
dp).

## Readout (B's /sd draw time)

- B redraw at **t=25.5** → Idle: the upstream chain-last block's fail
  COUNTS toward the middle block's downNum (matches real NiCd; our
  port's current behavior).
- B redraw at **t=5** → Running: it doesn't (then Capacity1 run 5's
  NiCd Idle needs a different mechanism).

Our port: B redraw **t=25.5** (Idle — downNum=2, D's df counted).

## Companion note

probe-5's README "Our port: [C 18.197383, B 842292.018741] (hopped)" is
STALE — post-36dd580 the port matches real on probe-5 (B redraw t=5,
Running). §40's "our port's downNum=2" premise for probe-5 is outdated;
the open question is only this fixture's dimension.

## REAL ANSWER (2026-09-02 capture, legacy/)

B redraw at **t=25.5** — Idle. All 6 draws match our port exactly
(zero divergence). The upstream chain-last block's fail COUNTS toward
the middle block's downNum — real's NiCd answer reproduced in
isolation, and our port's rule (own dp + df of blocks whose own
out-link feeds the EFPA node directly) now has real confirmation on
all seven data points: probes 3/5/7/8/9 + Capacity1's NiCd/Bus_UR.
