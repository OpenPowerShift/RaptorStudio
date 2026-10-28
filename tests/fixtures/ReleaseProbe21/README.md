# ReleaseProbe21 — does the WAIT-SITE (dep-idle park vs ordinary running fail-wait) determine a deferred-PM grant's wake position?

`ReleaseProbe20` (§60.3) pinned the pickup TYPE as the D.3 discriminator:
real orders a repair-Give-resume AHEAD of the recovery cascade and a
deferred-PM grant BEHIND it (`[C, B, D]`). Implementing that split
directly in `_Cascade.apply` (§61.1) matched probe-20 exactly but
**regressed Capacity1 runs 0-4** — reverted. Root cause: Capacity1 run 4
needs the OPPOSITE ordering for its own deferred-PM pickup (S500, which
is BOTH dep-idled AND granted at the same instant) vs run 5's S650/NiH
(which is granted while sitting in an ORDINARY running fail-wait, never
dep-idled). §61.1's refined hypothesis: it isn't the grant TYPE alone —
it's the **wait-site**. A grant landing on a block parked in the
dep-idle park keeps the pre-cascade (recovery-wave) position; a grant
landing on a block in its ordinary running fail-wait queues behind the
cascade. Probe-20's `BlockD` was the latter shape (never dep-idled).
This probe builds the former.

## What's new here vs `ReleaseProbe20`

**One topology addition, `BlockD`'s dependency, and one field tweak.**
Everything else — `start→BlockA(3)→BlockB(4)→join(5)→end(2)`, `start→
BlockC(6)→join(5)` (C: fails FIXED 3.5, `RepHold`, repair EXP(10) stream
6), `Techs` pool `NumIn=2` held by A (`#Res1Rep=2`) 3→25 (FIXED 22
repair), `ProbeTrig` PM-parking B at t=4/park at 5 — is byte-identical
to probe-19/20.

1. **New node `Cpath` (ID 3, `Type=2`, `k=-1`, `n=1`, `Out=1`)** spliced
   between `BlockC` and `join`: link 5 (`BlockC→join`) now points
   `BlockC→Cpath`, and a new link 9 carries `Cpath→join`. `join`'s own
   `n` is unaffected (still 2 incoming links; the topology is
   unchanged, just one hop indirect). This gives `Cpath` a status that
   tracks `BlockC` alone — a single-purpose dependency anchor, the same
   pattern `tests/data/models/Phasing1.rbd`'s real, already-verified `900path` node
   uses (`SolarP_900W`/`BatteriesNaS` depend on it, both with
   `usesPM=1`/`#Res1pmRep=1` — direct precedent that `DepType=RBDNode`
   + PM-needing-a-resource is a real, already-ported combination).
2. **`BlockD` gets `DepType=RBDNode`, `DepNum=3`** (Cpath), `DepIdle`
   stays 100 (already was) — `BlockD` goes `Idle Dependence` the instant
   `Cpath` goes Bad, i.e. the instant `BlockC` fails.
   - Tried `DepType=RBDBlock`/`DepNum=6` (directly on `BlockC`) FIRST —
     **hits an existing, already-documented `NotImplementedError`**:
     `orchestrator.py:2842-2849`'s `_dependent_block_process` explicitly
     doesn't support PM needing a resource combined with a
     block-to-block dependency ("no fixture exercises that
     combination"). `DepType=RBDNode` routes through the ORDINARY
     `block_run_process` instead (`orchestrator.py:3997` only special-
     cases `RBDBlock`), which already supports PM+resource — confirmed
     both by `Phasing1`'s own real fixture and by this probe running
     clean. This is itself a useful, minor byproduct finding: the
     `RBDBlock`-dependency gap is real and narrow (no node-dependency
     equivalent), not something this probe needed to route around by
     accident.
3. **`BlockD`'s `PMFreq` lowered from 10.0 to 3.2.** At `PMFreq=10.0`
   (probe-20's value), `BlockD` never accumulates 10 units of OPERATING
   time in this timeline at all — its PM clock freezes for the ~23-unit
   dependency-idle stretch (confirmed empirically: PM never fires with
   the original value, see "Confirmed via this port's own run" below),
   so the grant race this probe exists to test never happens. `3.2` sits
   between `BlockA`'s own failure at `t=3` (which empties the `Techs`
   pool — `BlockA Repairing 2Techs`) and `BlockC`'s failure at `t=3.5`
   (which dep-idles `BlockD`): `BlockD`'s PM comes due at `t=3.2` with
   the pool ALREADY empty (deferred, `pmReqDefer`), THEN `BlockD`
   dep-idles at `3.5` with that deferred request still outstanding, and
   stays dep-idled straight through `t=25` (`BlockC` doesn't fully
   recover until `t≈26.36`/`28.58`/`27.87` across the 3 runs — well past
   the grant instant). This is exactly the target shape: **`BlockD` is
   BOTH dep-idled AND has a pending grant at `t=25`**, mirroring
   `Capacity1`'s own `SolarP_500W`/`BatteriesNiCd` (run 1/4) rather than
   probe-20's `BlockD` (which mirrored `Capacity1`'s `SolarP_650W`/
   `BatteriesNiH`, run 5).

## Confirmed via this port's own run

At `PMFreq=10.0` (unmodified probe-20 value): `BlockD`'s ONLY events in
the whole run are `Good`(t=0) → `Idle Dependence`(3.5) → `Good
DependenceStress`(~26.36, when C finally recovers) → `Idle
Dependence`(~29.86, C fails again) → `Done End_sim`(30) — **no PM
activity at all**, confirming the freeze. At `PMFreq=3.2` (this probe's
actual value), run 0's `t=25.000000` block:
```
25.000000  Block  BlockA  Repaired         MaintTime=22.000000
25.000000  Block  BlockA  Good             Stress=1.000000
25.000000  Node   join    Idle             0-1/2_di3_df9
25.000000  Node   end     Idle             0-1/2_di6_di8
25.000000  Block  BlockB  Good             DependenceStress=1.000000
25.000000  Node   join    Degraded         1-1/2_ur3_df9
25.000000  Node   end     Degraded         1-1/2_ur6_di8
25.000000  Block  BlockD  Resources        Obtained_1_Resource(s)
25.000000  Block  BlockD  PM               beginning_PM
25.000000  Node   end     Degraded         1-1/2_ur6_dp8
25.000000  Block  BlockC  Sparing          Obtained_1_Resource(s)
25.000000  Block  BlockC  Repairing        1Techs
25.000000  System State_change  Yellow    _d6
```
`BlockD`'s deferred-PM grant (`Obtained_1_Resource(s)`→`beginning_PM`)
logs BEFORE `BlockC`'s repair pickup in THIS port's event-log order —
already a qualitatively different shape than probe-20 (where the log
order was `D Obtained` → `C Sparing/Repairing` → `D beginning_PM`, i.e.
D's own two lines were split around C's). At `t=3.2`: `BlockD PM_is_due
Scheduled_PM` + `pmReqDefer No_resource_in_stock-Techs` — the standard
deferral signature, no state change. At `t=3.5`: `BlockD Idle
Dependence` (via `Cpath` going Bad from `BlockC`'s failure).

## The question

At `t=25.000000`, where does `BlockD`'s deferred-PM draw land relative
to `BlockC`'s repair draw and `BlockB`'s recovery redraw?

- **`BlockD` is NOT last** (`[D, C, B]`, `[C, D, B]`, or any order with
  `B` last) → **CONFIRMS the wait-site hypothesis**: a deferred-PM grant
  landing on a block that's ALSO dep-idled keeps a pre-cascade
  (recovery-wave) position, unlike probe-20's ordinary-running-fail-wait
  `BlockD` which landed last. This pins Capacity1 runs 5-9's fix surface
  as "route a granted block's wake by its CURRENT park (dep-idle vs
  ordinary fail-wait), not by request type alone" — a genuinely
  different (and more surgical) split than the reverted §61.1 attempt,
  worth a fresh implementation attempt.
- **`[C, B, D]`** (`BlockD` last, same shape as probe-20's real answer)
  → **REFUTES the wait-site hypothesis too**. Neither grant TYPE alone
  (probe-20) nor wait-site alone (this probe) is sufficient — Capacity1
  run 4 vs run 5's contradiction needs a THIRD factor not yet isolated
  (§61.1's own hedge: "hypothesis, not verified"). Worth revisiting
  whether Capacity1 run 4's own S500 pickup has some OTHER structural
  difference from run 5's S650/NiH beyond dep-idle-vs-running (e.g. the
  PM-begin cascade's own mid-instant dependency re-dispatch, §39's third,
  never-isolated feature).

## Our port's own prediction (NOT the readout)

`sim_details_path` trace, all 3 runs:
```
 0.000000  BlockB  792225.529423
25.000000  BlockD  8.484571
25.000000  BlockC  1.357908
25.000000  BlockB  842292.018741
 0.000000  BlockB  416902.749341
25.000000  BlockD  25.665643
25.000000  BlockC  3.582951
25.000000  BlockB  661497.624323
 0.000000  BlockB  3144207.888279
25.000000  BlockD  2.871198
25.000000  BlockC  13.185966
25.000000  BlockB  831393.179051
```
`[D, C, B]` — this port's own wake-loop ordering happens to put D FIRST
here (not `[C, D, B]` like probe-20's identical-queuing prediction),
because `BlockD`'s dep-idle-park pickup goes through a different branch
in `block_lifecycle.py` than probe-20's ordinary-fail-wait pickup. This
prediction is NOT the subject of the probe — under EITHER hypothesis
outcome above, this port's own order is expected to need a fix; only
real Raptor's answer distinguishes "not last" from "last."
`BlockD`'s draw values (8.484571/25.665643/2.871198) are stream 8's
first draws — identical to probe-19/20's D, the same built-in
cross-capture continuity check.

## Readout (t=25.0 /sd order)

Order the three `SimTime=25.000000` /sd lines across the 3 runs (C, D,
B) and read per the two outcomes above. Sanity-check the event log
around t=25 for `Repaired 22` (A), `BlockD Resources Obtained_1_
Resource(s)`/`PM beginning_PM`, `BlockC Sparing Obtained_1_Resource(s)`/
`Repairing 1Techs`, `BlockB Good DependenceStress` — and confirm
`BlockD`'s own state at t=25 reads `Idle` (dep-idled), NOT `Running`,
in whatever status/state fields the real event log or GUI exposes, to
confirm the dep-idle-and-granted shape actually landed as designed
before trusting the draw-order readout. At t=3.2 confirm `BlockD PM_is_
due Scheduled_PM` + `pmReqDefer No_resource_in_stock-Techs`; at t=3.5
confirm `BlockD Idle Dependence`. If real's deferral/dep-idle timing
differs from the predicted t=3.2/t=3.5 split, note the full event-log
context — the draw order is only interpretable against the event
structure that produced it.

## How to capture it

Same procedure as the other `ReleaseProbe*` fixtures — launch Raptor7
Student with `/sd`, open this file, Simulate (3 runs matches probe-19/
20's capture count). Real-Raptor report outputs land in
`~/Documents/Raptor7/` (`rpt_events_*` etc.); the `/sd`
copy goes to this repo's root as `simDetails_ReleaseProbe21.txt`.

## REAL ANSWER (2026-09-04 capture, `simDetails_ReleaseProbe21.txt` + `~/Documents/Raptor7/rpt_events_ReleaseProbe21.txt`)

**`[C, B, D]` in all 3 runs — `BlockD` lands LAST, exactly like
`ReleaseProbe20`'s ordinary-running case.** `/sd` order:
```
 0.000000  BlockB  792225.529889
25.000000  BlockC  1.357908
25.000000  BlockB  842292.019207
25.000000  BlockD  8.484571
```
(runs 2/3 identical shape; all values match this port's own predicted
draws exactly except `BlockB`'s two exp(1e6)-scale redraws, which show
the already-documented software-`ln`-noise signature — same class,
same rough magnitude, as probe-19's `842292.019207` vs `...018741`.)

**This REFUTES the wait-site hypothesis as tested.** `BlockD` here is
confirmed dep-idled AND granted at t=25 (event log: `PM_is_due
Scheduled_PM`/`pmReqDefer No_resource_in_stock-Techs` at t=3.200000
exactly as predicted; `Idle Dependence` at t=3.500000 exactly as
predicted; no intervening state change before t=25 — genuinely parked
in the dep-idle park, not Running, when granted) — yet it draws in the
SAME relative position (last) as probe-20's `BlockD`, which was never
dep-idled at all. The log-LINE order is also the identical shape to
probe-20's (`BlockD Resources Obtained_1_Resource(s)` splits around
`BlockC`'s two lines before `BlockD PM beginning_PM`), not the
predicted-different `[D-obtained, C-obtained, C-repairing, D-beginPM]`
this port's own dep-idle-park code path produces (this port predicted
`[D, C, B]` — WRONG on both counts, order AND which line splits where).

**Consequence**: neither pickup TYPE (probe-19, ruled out — count
doesn't matter) nor wait-site alone (this probe, ruled out) explains
Capacity1 run 4 vs run 5's contradiction — and §35 (2026-09-01, BEFORE
this session's wait-site work) already ruled out every simple
before/after-cascade comparator rule by direct instrumentation on
Capacity1 itself: "the run-4/run-5 contradiction shows the discriminator
must distinguish a trigger-woken PM-begin continuation from a
pool-granted one AT ORDERING TIME." §35's own closing "next step" (not
yet attempted by this probe, nor by the §61.1/§62 implementation
attempt) names a DIFFERENT, more specific mechanism: run 4's NiCd
receives TWO separate interrupts on the SAME scheduler handle at the
same instant (an `AllUp` interrupt from Bus_UR's turn, and a `GoIdle`
interrupt from S500's own later PM-begin turn) — real delivers BOTH,
producing "the redraw despite the re-idle"; this port's `Scheduler.
interrupt` generation-staling keeps only the LAST, silently dropping
the earlier one. This is a genuine scheduler-semantics gap (multiple
pending interrupts per handle, delivered in order), not a wake-ordering
classification question — `ReleaseProbe21`'s own topology never
produces two interrupts racing on ONE handle, so it couldn't have
caught this even if it existed here. Flagged by §35's own text as "the
most regression-prone" surface in the whole investigation — approach
with the same care (full sweep before trusting) as every attempt so
far. See the internal investigation log §35-36 for the full mechanism
trace.
