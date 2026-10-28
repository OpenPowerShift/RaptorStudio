# Fixture: Capacity1 — manual Ch.14 capacity analysis (flowGenerated=2050)

Real Wine capture (`Raptor7Student.exe`, all output files enabled), 10 runs,
TimeSim 730.5 h. Model: the satellite corpus's `Cost1.rbd` plus
`capacityAnalysis=1`, `costAnalysis=1`, `flowGenerated=2050`; `Power` node
unrestricted-flow OFF; hard flow limits 500/650/900 W on the three string
feed links; `n1` with anyPath=0 and outbound CapPri 50/30/10.

## What this verifies in this port

- **Capacity graph build** (`engine/capacity.py`): boundary detection
  (start/n1/Power/end = CapNodes=4, CapLinks=5) matches the real engine's
  own `simDetails.TXT` "Capacity Algorithm Results" section line for line,
  including n1's extended priorities 50/30/10 (anyPath=0 → positional
  reassignment skipped).
- **Capacity plot** (`legacy/rpt_capacity_plot_capacity1.txt`): **all 10
  runs are an exact value match** (every Flow/Capacity pair; only ±1e-6 fp32
  timestamp noise) as of 2026-09-04's D.3 fix (the internal investigation log
  §64-65) -- runs 5-9 used to diverge on the event-ordering gap this
  fixture originally exposed (below), the fixture that opened the whole
  D.3/G.3 investigation arc.
- **End_Of_Run `***CAPACITY***` section**: run 1 byte-identical against the
  real capture (UnitsProduced 1286316.11, AveFlow/Cap 1760.87, SystemCapacity
  row incl. trailing spaces, NodeCapacity table with case-insensitive alpha
  order and the fullFlow N/A variant for `end`/`n1`). COST sections are NOT
  compared (not ported).

## Resolved: the multi-block same-instant resource-grant ordering gap

**Fixed 2026-09-04** (the internal investigation log §64-65, `raptor/core/
engine/orchestrator.py`'s `_Cascade._apply_cascade` wake_ids loop +
`raptor/core/scheduler.py`'s `Scheduler.interrupt` `late` parameter).
Multi-block same-instant interleavings (deferred-PM resource grants +
dependency cascades) needed a three-case tie-break: an ordinary repair
grant always sorts ahead of the releasing block's own node cascade; a
PM-prereq grant sorts ahead too if it shares the releasing block's own
node (a genuinely coupled recovery, e.g. `SolarP_500W`) or if the target
is authentically `PM_HOLD`-parked (a real waiter); otherwise it sorts
behind the cascade. Confirmed via all three Capacity fixtures (970/970
draws exact) plus `ReleaseProbe19`/`20`/`21`'s real captures, with zero
regressions across the full 15-fixture sweep.
