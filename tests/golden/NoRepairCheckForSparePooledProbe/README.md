# NoRepairCheckForSparePooledProbe — CheckForSpare runs before the No_repair route, Pooled sparing too

Built 2026-09-23 via an internal build script
(two all-`Fixed` blocks cloned from `TwoBlockRedundancy`, same recipe as
`NoRepairCheckForSpareProbe`, but `SpType=Pooled` instead of `Custom`).

## What it pins

`NoRepairCheckForSpareProbe` (2026-09-19) already real-captured this
ordering for **Custom** sparing. ER3-05's open half was whether
**Pooled** sparing gets the same treatment, since read-only source
inspection found `repairDistro==18` checked BEFORE the SparePool
branch's own `CheckForSpare`-equivalent in the port -- an asymmetry
with the Custom branch.

**Real Raptor treats Pooled exactly like Custom** — confirmed by this
capture:

```
5.000000  Block  BlockA  Failed    Operating_time=5.000000
5.000000  Block  BlockA  Sparing   No_spare_in_stock-TestPool
5.000000  Block  BlockA  Done      No_repair
```

`CheckForSpare`'s side effect (`No_spare_in_stock-<pool>`) fires BEFORE
`Done No_repair`, same as the Custom capture. The port (as of
2026-09-23, before the ER3-05 fix) skips this branch entirely for a
Pooled `RDist=18` block -- run `NoRepairCheckForSparePooledProbe`
through the port and it goes straight `Failed` -> `Done No_repair`
with no `Sparing` line at all, confirming ER3-05's asymmetry is a real,
reachable port bug, not just an unverified reading.

**Also note** (not this fixture's main finding, but visible in the
capture): the spare-order/arrival lines for a Pooled block are logged
as `Pool <name> Sparing ...` (pool-level), not `Block <name> Sparing
Spare_ordered_...` (block-level) the way Custom sparing logs them --
and the pool-level lines appear once **per ordering block**
(`Spare_ordered_(stock=0)` logged twice, once for BlockA's order and
once for BlockB's, both against the shared pool) while `Ordered_spare_
arrives_(stock=1)` / `(stock=2)` also fire once per arrival. Worth
checking the port's own Pool-vs-Block event-kind handling separately
if not already covered elsewhere.

## Status: CAPTURED (2026-09-23)

## To recapture

Load `input.rbd` in Raptor7Student, **Launch ▸ Simulation…**,
**Number of runs = 1**, tick **Detailed event log**, OK. Save into
`legacy/` as `rpt_events_NoRepairCheckForSparePooledProbe.txt`.

## History

`RepairNoneSpareOrderProbe` (`tests/fixtures/`, 2026-09-23) tried to
pin this same question on a cloned 10-block satellite model first and
**crashed real Raptor on open**, reproduced twice; a `HierarchyExample`
control launch in the same session opened cleanly, proving it was
fixture-specific, not an environment/wine issue. Root cause not
chased down (validate() found nothing, header counts matched,
distribution codes -- including `RDist=18` itself, 156 real occurrences
across the golden corpus -- were all legitimate). Abandoned in favour
of this much simpler, same-recipe-as-a-proven-safe-fixture rebuild
instead of debugging the crash. `RepairNoneSpareOrderProbe` itself is
left in `tests/fixtures/` unused/untraced -- do not launch it against
real Raptor without first finding what actually crashes it.
