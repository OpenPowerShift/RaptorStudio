# ReleaseProbe2 — node-dep recovery probe (downNum branch + wake order)

Companion to `../ReleaseProbe` (§37): same three-block shape, but BlockB's
dependency is the NODE-level kind (`DepNum=-1`, `DepType=None`, `DepIdle=100`
— deterministic GoIdle, no dep draw) mirroring Capacity1's BatteriesNiCd, and
B's Site-1 park is reached via its own triggered PM completing while the join
node is down (the same `Idle change_block_state` downNum>1 branch NiCd took at
Capacity1 t=30.269696).

## Timeline (deterministic)

- t=3: A fails (FIXED 3), repairs 22h holding the pool's only `Techs` unit.
  Join degrades → B GoIdle (node-dep dispatch, no draw).
- t=3.5: C fails (FIXED), repair needs `Techs` → `RepHold
  Waiting_for_resource(s)`.
- t=4: ProbeTrig fires (FIXED 4, once) → B `PM_is_due` → PM 1h (FIXED, no
  draw).
- t=5: B `PM_complete` (PMRefresh → FailTimeLeft=0). THE PROBE'S FIRST
  READOUT: the join is down (A df + C df + B dp → node-total downNum=3 > 1),
  so source's `GetDownNum` special case →
  `Idle change_block_state` → **Site 1 park**. Our port instead went
  `Good Stress=1.000000` (Running) — its `GetDownNum` port reads the block's
  OWN per-path counters (down_num=1) rather than the node's total.
- t=12-25: B parked; C waiting.
- t=25: A repairs → join recovers → node-level AllUp → B's recovery
  (ZeroWait release) + C's pool-grant pickup. THE SECOND READOUT: the /sd
  order of B's recovery redraw (exp(1e6)) vs C's repair draw (exp(10)).

## Readout

1. Real's events at t=5: `BlockB Idle change_block_state` → GetDownNum is
   the NODE's total down count (our port's per-index reading is a bug to
   fix); `BlockB Good Stress=...` → per-index (our port is right).
2. If Idle: the t=25 /sd order — `BlockB` before `BlockC` = the node-dep
   recovery redraw is SYNC (Capacity1 run 5 generalizes; our port's
   post-idle-loop hop is wrong for this path); `BlockC` before `BlockB` =
   HOPPED (run 5's leading redraw has another mechanism).

Run 3 trials with `/sd` enabled; send back `simDetails.TXT`.
