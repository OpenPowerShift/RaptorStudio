# Fixture: PMGrantVsRepairSpareProbe — resource grant vs. repair-spare wait

Built 2026-09-17 via an internal build script
(loads `TwoBlockRedundancy`, adds a 1-unit `RESOURCE_DATA` pool and an
empty emergency-ordered `SPARE_POOL_DATA` pool, and rewrites the two
blocks with **Fixed(19)** fail/repair/PM distributions).

## Status: captured 2026-09-17

`legacy/` holds the real Wine capture. **The grant-identity fix is
confirmed by real**: real logs no `Spare_Arrived` at t=25 and shows it at
t=45, exactly as the fixed port does.

Real also exposed a SECOND gap, since fixed: real discards the stale
deferred PM RESOURCE request at FAILURE time (t=15), so the t=25
`Techs` release grants NOBODY and `ResHolder` reacquires at t=30
(`Repairing 1Techs`). The port used to keep the request queued, let it
eat the t=25 release, and starve `ResHolder` for the rest of the run.
Fixed by the same §69 withdrawal applied at the repair-spare entry.

Known residual (FIDELITY.toml `report_fidelity.PMGrantVsRepairSpareProbe`):
at t=55 both logs show ResHolder failing first, but real then completes
PmDeferRepair before ResHolder enters Repairing; the port enters RepHold
first and adds a zero-duration system outage, so System_Failures is 5 vs
real 4 and MTBDE/MDT differ. Same-instant dispatch, not resolved.

## What it exercises

A `"Pool_Available"` interrupt whose grant belongs to one outstanding
request arriving while the block is suspended on a DIFFERENT one — and in
particular the case that crashed this port:

- block B has an outstanding **deferred PM resource** request (`Techs`
  pool empty, `PMReqDefer=1` → logs `pmReqDefer`),
- then B **fails** and queues a repair **spare** on the empty `Spares`
  pool, suspending at `WAIT FOR PooledSpares[...] TO Give`,
- while B is suspended there, block A's **repair completion releases the
  `Techs` unit**, granting B's PM resource and sending B a
  `"Pool_Available"` interrupt.

Source's `WAIT FOR PooledSpares[pool] TO Give` is
bound to the SPARE pool object, so the resource-pool `Give` must NOT
resume it. This port used one generic `"Pool_Available"` reason with no
identity; the spare wait consumed the resource grant, and the REAL spare
grant later landed at a running fail-wait that cannot reconcile an
ordinary repair grant — `AssertionError ('interrupt', 'Pool_Available')`
(observed on PhasingDemo at 300 runs / 4 parallel workers).

## Why Fixed distributions

Every distribution is `Fixed` (code 19, draws `Param1` and consumes no
RNG), so the timeline is identical in this port and in real RAPTOR and
does not depend on the RNG stream. That is what makes a real capture line
up run-for-run with `ours/`.

## Topology

```
start --1--> ResHolder(id3) -----> join --5--> end
      --3--> PmDeferRepair(id4) --^
```

- `ResHolder`: `SpType=Infinite`, `NumRes=1`, `#Res1Rep=1`,
  `Resource1Name=Techs`; `Fixed(5)` fail, `Fixed(20)` repair.
- `PmDeferRepair`: `SpType=Pooled`, `SparePoolName=Spares`, `Stock=0`,
  `NumRes=1`, `#Res1pmRep=1`, `Resource1Name=Techs`, `usesPM=1`,
  `PMFreq=10`, `PMStaggerTime=0`, `PMReqDefer=1`, `PMSpare=0`;
  `Fixed(15)` fail, `Fixed(5)` repair, `Fixed(5)` PM.
- `RESOURCE_DATA Techs`: `NumIn=1`.
- `SPARE_POOL_DATA Spares`: `Stock=0`, `ESO=1`, `EmerTime=30`.
- `SYSTEM_DATA`: `NumRuns=1`, `TerminationType=TimeSim`, `TermCondition=100`.

## Timeline (one run)

| t  | event |
|----|-------|
| 5  | A fails, borrows the single `Techs` unit, repairs |
| 10 | B's PM is due; `Techs` empty → `pmReqDefer No_resource_in_stock-Techs` |
| 15 | B fails; `Spares` empty → `No_spare_in_stock-Spares`, ESO order, `RepHold Waiting_for_Spare` |
| 25 | A repaired, releases `Techs`; B's stale PM request has been withdrawn |
| 30 | A fails again and reacquires `Techs` |
| 45 | `Ordered_spare_arrives_(stock=0)` → B's repair spare granted (`Spare_Arrived`) |

## How the two behaviours differ (already measured)

This port, **before** the fix (`Scheduler.interrupt` gained the grant
`token`), wrongly resumed B's spare wait at t=25:

```
25.000000  Block  PmDeferRepair  Sparing   Spare_Arrived      <- WRONG (no spare yet)
45.000000  Block  PmDeferRepair  Sparing   Obtained_1_Resource(s)
```

After the fix, B correctly keeps waiting for its spare:

```
25.000000  Block  ResHolder      Repaired  MaintTime=20.000000
45.000000  Block  PmDeferRepair  Sparing   Spare_Arrived      <- correct
```

The real capture arbitrates: it should show `Spare_Arrived` at t=45, not
t=25 (source's `Give` is pool-object-specific).

## To capture

Load `input.rbd` in Raptor7Student, **Launch ▸ Simulation…**, set
**Number of runs = 1**, and in **Files & Reports** tick **Detailed event
log** (plus **End of run** and **Final results**), OK. Save the files into
`legacy/` as:

- `rpt_events_PMGrantVsRepairSpareProbe.txt`
- `rpt_end_of_run_PMGrantVsRepairSpareProbe.txt`
- `rpt_final_results_PMGrantVsRepairSpareProbe.txt`

(Real RAPTOR is single-process: there is no worker/thread setting. The
batch is just `Number of runs`, which is why the probe uses Fixed timing
to be reproducible from the canonical stream.)
