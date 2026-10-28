# Fixture family: ResourceRepairOrderProbe — same-instant resource release vs repair request

Built 2026-09-18 via an internal build script
(clones `TwoBlockRedundancy`, adds a 1-unit `RESOURCE_DATA` pool, and
rewrites both blocks with **Fixed(19)** fail/repair distributions).

## Why this exists

`ResourceRepairOrderProbe` isolates — WITHOUT the PM/spare-pool
machinery that confounds `PMGrantVsRepairSpareProbe` — the same-instant
ordering question behind that fixture's unresolved t=55 residual
(`FIDELITY.toml` `report_fidelity.PMGrantVsRepairSpareProbe`,
ER3 outstanding item 1, since closed; the ER3 label key is the appendix of the internal engine ledger).

## Topology

```
start --1--> ResHolder(id3) -------> join --5--> end
      --3--> ResourceWaiter(id4) --^
```

- `ResHolder`: `SpType=Infinite`, `NumRes=1`, `#Res1Rep=1`,
  `Resource1Name=Techs`; `Fixed(5)` fail, repair set per variant.
- `ResourceWaiter`: same sparing/resource; `Fixed(25)` fail,
  `Fixed(5)` repair.
- `RESOURCE_DATA Techs`: `NumIn=1` (exactly one unit, shared).
- `SYSTEM_DATA`: `NumRuns=1`, `TerminationType=TimeSim`,
  `TermCondition=100`.

## The question

`ResHolder` holds the only `Techs` unit for the whole of its repair. The
waiter fails at the instant that repair completes (which releases the
unit). Does the waiter **log `RepHold Waiting_for_resource(s)`** and
obtain the unit a moment later (this port's behaviour), or go
**straight to `Repairing 1Techs` with no `RepHold`** because the
same-instant release is already visible?

Source:
- `DetermineNewState("Block Has Failed")` writes `Failed`, then calls
  `Repair;`.
- `Repair` is `TELL METHOD Repair;`.
- For `numDiffRes=1` it runs the `Resources<numRes1` →
  `ChangeBlockState(RepHold,...,"Waiting_for_resource(s)")` check
   and then an UNCONDITIONAL
  `WAIT FOR PooledSpares[resPoolNum] TO Give(SELF,numRes1)`.

Interpretation (a) (immediate `TELL`): `RepHold` MUST appear.
Interpretation (b) (deferred/queued `TELL`): it must NOT, and the waiter
repairs straight after the release.

## Variants

| fixture | `ResHolder` repair | waiter fails at | release at | role |
|---|---|---|---|---|
| `ResourceRepairOrderProbe`  | `Fixed(20)` | t=25 | t=25 | **discriminating** (coincident) |
| `ResourceRepairOrderProbe2` | `Fixed(21)` | t=25 | t=26 | **control** (genuine 1-unit wait — `RepHold` expected) |

Both are all-`Fixed`, so no RNG is drawn and the timeline is identical in
this port and in real RAPTOR run-for-run.

## Status: RESOLVED 2026-09-18 — release-due re-yield

Real `legacy/` captures are in place. **A same-instant sibling release is
observed by the failing block's repair-resource wait, so there is no
`RepHold`.**

`ResourceRepairOrderProbe` (coincident, release and failure both t=25):

```
25  Block  ResourceWaiter  Failed     Operating_time=25.000000
25  Block  ResHolder       Repaired   MaintTime=20.000000
25  Block  ResHolder       Good       Stress=1.000000
25  Node   join            Good       2-1/2_ur2_ur4
25  Block  ResourceWaiter  Repairing  1Techs          <- NO RepHold
25  Node   join            Degraded   1-1/2_ur2_df4
```

Note there is **no `RepHold Waiting_for_resource(s)`, no `Node Bad` and no
`System Red`** at t=25, exactly as `PMGrantVsRepairSpareProbe` t=55
showed.

`ResourceRepairOrderProbe2` (control, release at t=26) real DOES log
`RepHold Waiting_for_resource(s)` at t=25 and obtains the unit at t=26 —
so the probe correctly distinguishes a same-instant release from a
genuine one-unit wait.

### The fix

Source's `WAIT FOR PooledSpares[resPoolNum] TO Give`
yields even when the unit is about to be freed, so the port must not
decide "wait" until the current instant's releases have run. Implemented
as a targeted re-yield in `block_lifecycle.py`'s `numDiffRes=1` branch:

* `ResourceQueue.note_release_at` (`pool.py`) records when a borrowed
  unit is due back — set to `scheduler.time + repair_time` when the block
  transitions to `Repairing`;
* before the resource `request()`, if the pool is short AND
  `release_due_at(scheduler.time)`, the block `yield 0.0` once to let the
  releasing sibling run, then re-checks — no `RepHold` when the release
  lands in the same instant, unchanged `RepHold` for a genuine wait.

Both variants now match real **byte-for-byte**
(an internal comparison script), and the full
`test_sd_diff_sweep` stays green (61 passed / 5 xfailed). Regression
tests: `test_resource_wait_observes_same_instant_release` (this fixture)
and `test_resource_wait_control_logs_rep_hold` (the control) in
`tests/unit/test_block_lifecycle.py`.

### Path not taken (kept for the record)

A global snapshot/two-wave scheduler (`RAPTOR_WAVE_COMPOSITION=1`) also
reproduced both probes exactly, but regressed 11 `test_sd_diff_sweep`
fixtures; a narrower marked-defer variant was worse (15). The global rule
defers *every* same-instant zero-delay continuation, whereas the real
mechanism is the resource `Give` specifically. The targeted re-yield
above avoids the scheduler redesign entirely. (The wave experiment and
the `choose_next` selector spike remain documented in `scheduler.py` as
default-off investigation harnesses.)





## To capture

Load `input.rbd` in Raptor7Student, **Launch ▸ Simulation…**, set
**Number of runs = 1**, and in **Files & Reports** tick **Detailed event
log** (plus **End of run** if available), OK. Save as:

- `legacy/rpt_events_ResourceRepairOrderProbe.txt`
- `legacy/rpt_events_ResourceRepairOrderProbe2.txt`

(Real RAPTOR is single-process; the batch is just `Number of runs`, which
is why the probe uses Fixed timing to be reproducible from the canonical
stream.)

## Port-side comparison

```
.venv/bin/python an internal comparison script   # both variants
```
