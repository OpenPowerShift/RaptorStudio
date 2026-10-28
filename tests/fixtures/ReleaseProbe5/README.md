# ReleaseProbe5 — deferred-PM pickup probe (bisect step 2)

Bisects the Capacity1 run-5 anomaly (§39): probe-3 proved the recovery
redraw trails a REPAIR-resource pickup; run 5's real capture has it
LEADING the DEFERRED-PM pickups. This probe converts the competitor to
the deferred-PM kind: BlockC's triggered PM (ProbeTrig2, t=3.6) is
deferred (`pmReqDefer`, Techs held by A) and granted at t=25 — the
pickup wake pays the ObtainPMprereqs TELL-boundary hop before C's
PM-begin and its VISIBLE PM-duration draw (exp(10)).

Structure: start→A→B→join→end (B node-dep, Site-1 park at t=5 via
downNum>1 — verified), start→C→join2→end (C's deferred-PM pickup).

## Readout (t=25 /sd order)

- `BlockC` PM draw BEFORE `BlockB` redraw → HOPPED (our port's current
  behavior) — the deferred-PM pickup structure is NOT the mechanism;
  the bisect moves to the PM-begin cascade (probe-6).
- `BlockB` redraw BEFORE `BlockC` PM draw → SYNC — the deferred-PM
  pickup's TELL-boundary hop IS the mechanism.

Our port: `[C 18.197383, B 842292.018741]` (hopped).

## RAPTOR compatibility note

The first version of this fixture failed to SIMULATE in real Raptor
(opened fine). Fixed by matching Capacity1 S650's resource config:
`#Res1Rep=1` (not 0) alongside `#Res1pmRep=1`. The trigger IDnum
duplicate (ProbeTrig/ProbeTrig2 both 0) and the join2 n=1 were ruled
out: ReleaseProbe4 has both and simulated fine.
