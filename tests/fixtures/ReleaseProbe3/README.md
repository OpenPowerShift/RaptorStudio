# ReleaseProbe3 — node-dep recovery wake-order probe (series A→B)

The probe that finally isolates the node-dep recovery's sync-vs-hop rule
(the Capacity1 runs 5-9 question, §37-§38). BlockB is node-dep
(`DepNum=-1`, `DepType=None`, `DepIdle=100` — deterministic GoIdle, no dep
draw) and reaches the **Site-1 park via the downNum>1 direct-Idle branch**: B's triggered PM completes at t=5 while A's
failure holds the join's shared-path counters at fail=1 + B's own dp=1 →
downNum=2 > 1 → `Idle change_block_state` with **no redraw** and
FTLeft=0 preserved — the exact event signature of Capacity1's
BatteriesNiCd at t=30.269696 (`PM_complete` → `Idle change_block_state`,
no intermediate `Good` line), reproduced in isolation.

Key structural piece: A and B are chained by a BLOCK-TO-BLOCK link
(`A→B`), so RunEFPA's pass-1 walk gives BOTH blocks the same EFPA link
(B→join) and the join's counters accumulate the whole chain's state —
this is what makes the downNum>1 branch reachable (per the §38 addendum's
correction, the counters are per-EFPA-link and the EFPA walk is already
chain-aware in our port).

## Timeline

- t=3: A fails (FIXED 3), repairs 22h (FIXED) holding the pool's only
  `Techs` unit. Join degrades.
- t=3.5: C fails (FIXED); repair needs `Techs` → `RepHold
  Waiting_for_resource(s)`. Join goes Bad → B `Idle Dependence`
  (node-dep GoIdle).
- t=4: ProbeTrig (FIXED 4, once) → B `PM_is_due` → PM 1h (FIXED, invisible).
- t=5: B `PM_complete` → downNum=2>1 → `Idle change_block_state` → Site 1
  park (FTLeft=0, no redraw).
- t=25: A repairs → join recovers → node-level AllUp → B's recovery
  (ZeroWait release) + C's pool-grant pickup. Both produce VISIBLE /sd
  draws: B's recovery redraw (exp(1e6)) and C's repair draw (exp(10)).

## Readout

Run 3 trials with `/sd` enabled. The t=25.000000 lines:

- `BlockB` redraw BEFORE `BlockC` repair draw → the node-dep recovery
  redraw is SYNCHRONOUS (Capacity1 run 5 generalizes): our port's
  post-idle-loop hop is wrong for this path and the fix is scoped there.
- `BlockC` repair draw BEFORE `BlockB` redraw → HOPPED (our port's
  current behavior): run 5's leading redraw has a different mechanism
  and the search continues.

Our port's own run: `[BlockC 1.357908, BlockB 842292.018741]` (hopped),
with B's `Idle change_block_state` at t=5 and no B draw there.
