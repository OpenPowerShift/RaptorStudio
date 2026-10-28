# Fixture: FiniteSparePool — first finite-spares capture

Captured 2026-08-15 from Raptor 7 Student Edition under Wine. Same
single-block model as `UniformProbe` (Uniform Real(0,1) fail/repair,
independent streams 1/2) with exactly one change: `SpType=Custom`,
`Stock=3` instead of `SpType=Infinite` (see `tests/golden/build_next_
fixtures.py`-style edit — built by loading `UniformProbe/input.rbd` and
overwriting just those two `BLOCK_DATA` columns, same technique as
`TwoBlockRedundancy`). `NumRuns=5`.

## What it shows

Once the block's spare stock is exhausted, repair does not happen at
all — the block goes permanently `Done`, and the system permanently
`Down`, for the rest of that run:

```
0.924530   Block   unnamed   Failed      Operating_time=0.924530
0.924530   Block   unnamed   Repairing   No_resources_used
...
1.109184   Block   unnamed   Repaired    MaintTime=0.184654
...                                          (one successful repair)
3.566975   Block   unnamed   Failed        Operating_time=0.976369
3.566975   Block   unnamed   Sparing       No_spare_in_stock-Custom
3.566975   Block   unnamed   Done          No_spares
3.566975   Node    end       Bad           0-1/1_df2
3.566975   System  State_change  Red       _d3
3.566975   System  Permanently_Down  Out_of_spares  System_change
100.000000 Block   unnamed   Done          End_sim
100.000000 System  Phase_change  Mission_failure  Finished_a_mission
100.000000 System  Simulation_Terminate  --  --
```

Notable new content, unseen in any prior fixture:
- `"Block"`/`"Sparing"`/`"No_spare_in_stock-Custom"` — a status not yet
  in `BlockStatus` (`model.py`) at all.
- `"Block"`/`"Done"`/`"No_spares"` — `Done` reached via a DIFFERENT path
  than the run-boundary `"End_sim"` already ported (`block_lifecycle.py`
  only handles the latter).
- `"System"`/`"Permanently_Down"`/`"Out_of_spares"`/`"System_change"` —
  this is `ReconfigureSystem`'s `typeNode=3` branch,
  read once during earlier cascade work but explicitly
  deferred (`nodes.py`/`cascade.py` don't port `ReconfigureSystem` at
  all yet — that's the whole "Start-node reachability"/`NumActivePaths`
  mechanism, separate from the `AnalyzeSystem` Green/Yellow/Red color
  already ported in `system_status.py`).
- Exactly **one successful repair happens before exhaustion** with
  `Stock=3` — the stock-to-repair-count relationship isn't 1:1 as a
  naive reading of "3 spares" might suggest; don't guess at the
  bookkeeping rule, read Raptor 7's actual spare-pool logic
  (`GetSpare`/similar, not yet located) before porting.

## Files

- `input.rbd` — the model.
- `legacy/rpt_finite_spare_pool.txt.gz` — event log, 5 runs (small, only
  1383 bytes gzipped — most runs exhaust spares almost immediately and
  then sit idle to `End_sim`).

## What this is for

The next real fixture for porting finite/custom sparing (`SpType=Custom`
handling in `RBDBlockObj.Run`/`Repair`, not yet ported — see
`block_lifecycle.py`'s module docstring, "infinite sparing... only" scope
note). It was then the first item of remaining work.
