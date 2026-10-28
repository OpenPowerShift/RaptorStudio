# ReleaseProbe — minimal probe for TriggerObj.Release wake-position semantics

Answers the question left open by the internal investigation log §35/§36: when a
dependency-idled block (Site-1 parked: `Idle` + FailTimeLeft=0) is recovered by
an AllUp dispatch whose wake path is the `ASK BlockTrigger[seqNum] TO Release`
branch (— NOT `Interrupt()`), does the block's own
recovery fail-redraw happen SYNCHRONOUSLY on the wake, or one `WAIT DURATION
0.0` scheduler round-trip later?

Capacity1's real captures show BOTH behaviors at structurally identical
instants (run 5: redraw leads the pool-grant continuations; run 4: redraw
trails them, delivered by a same-instant re-idle interrupt riding the hop) —
unresolvable from Raptor 7's source because `TriggerObj` is a
MODSIM II runtime built-in.

## Structure

- `BlockA`: fails at t=3 (FIXED), repairs 20h (FIXED) holding the pool's only
  `Techs` unit; post-repair redraw is FIXED (invisible — deliberately).
- `BlockB`: depends on BlockA (`DepType=RBDBlock`, `DepFail=100` — deterministic
  induced failure, no dep draw). A's failure at t=3 induces B's failure; B
  repairs 5h (FIXED); at t=8?? (t=10) B's maintenance-completed hits the
  downNum>1 branch → `Idle change_block_state` → **Site 1 park** (FTLeft=0).
  B's fail distro is exp(1e6) so the recovery redraw is VISIBLE and B can
  never fail naturally.
- `BlockC`: fails at t=12 (FIXED); its repair needs the `Techs` unit A holds →
  `RepHold Waiting_for_resource(s)`; A's t=25 release grants it → pickup →
  hop → **repair-time draw** (exp(10) — VISIBLE).
- t=25: A's repair completion produces, at ONE instant: A's Good, B's AllUp
  recovery (ZeroWait release), and C's pool-grant pickup.

## Readout

Run 3 trials with `/sd` (Simulation Details) enabled. The t=25.000000 lines:

- `BlockB` redraw BEFORE `BlockC` repair draw → the release wake redraws
  SYNCHRONOUSLY (no hop). Our port (main AND the multipending branch) already
  produces this order; Capacity1 run 5's structure is then the default rule,
  and run 4's hopped variant must be explained by its same-instant re-idle.
- `BlockC` repair draw BEFORE `BlockB` redraw → the release wake pays the hop
  (back of queue). Our port is WRONG here in general, and Capacity1 run 5's
  leading redraw needs a different explanation.

Timeline sanity (our port's own run): A Failed/Repairing 5.0 → B Failed
(induced, Operating_time=0) 5.0 → B Repaired/Idle change_block_state 10.0 →
C Failed/RepHold 12.0 → A Repaired/Good + B Good DependenceStress + C
Sparing Obtained/Repairing 25.0.
