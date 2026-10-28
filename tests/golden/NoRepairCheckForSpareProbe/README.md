# NoRepairCheckForSpareProbe — CheckForSpare runs before the No_repair route

Built 2026-09-19 via an internal build script
(two all-`Fixed` blocks cloned from `TwoBlockRedundancy`).

## What it pins

Source `DetermineNewState("Block Has Failed")`
calls `CheckForSpare` FIRST, THEN tests
`IF ((repairDistro = 18) OR (sparesOut))` → `Done "No_repair"`. So a
`repairDistro=18` (NONE) block with **Custom** sparing and empty stock
still runs CheckForSpare's side effects before going Done:

```
5.000000  Block  BlockA  Failed    Operating_time=5.000000
5.000000  Block  BlockA  Sparing   No_spare_in_stock-Custom
5.000000  Block  BlockA  Sparing   Spare_ordered_(stock=0)
5.000000  Block  BlockA  Done      No_repair
```

The port (before the fix) checked `repairDistro == NONE` first and
returned, **skipping CheckForSpare entirely** — no
`No_spare_in_stock-Custom`, no ESO order (ER3-05). Both `BlockA` and
`BlockB` have `SpType=Custom`, `Stock=0`, `ESO=1`, `RDist=18`.

All distributions are `Fixed` → no RNG, so port and real line up
run-for-run.

## Status: AWAITING REAL CAPTURE

Port currently emits only `Failed` → `Done No_repair`. The real capture
arbitrates whether the `No_spare_in_stock-Custom` + `Spare_ordered`
lines appear first.

## To capture

Load `input.rbd` in Raptor7Student, **Launch ▸ Simulation…**,
**Number of runs = 1**, tick **Detailed event log**, OK. Save into
`legacy/` as `rpt_events_NoRepairCheckForSpareProbe.txt` (+ end-of-run /
final-results if available).
