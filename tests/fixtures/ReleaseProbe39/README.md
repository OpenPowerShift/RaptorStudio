# ReleaseProbe39 — is D.1's ESO grace a genuine "first-ever pool exhaustion" rule, or specifically a KICKOFF-TIME special case?

Two fix attempts for D.1 (the internal engine ledger) were implemented,
matched `ReleaseProbe30`/`33`/`38` exactly, and were then falsified by
broader real data:

1. **"Pool's first-ever exhaustion instant is free"** (any count of
   simultaneous discoverers, no further suppression after) — falsified
   by `Capacity1`: `Bus_QR`'s own THIRD failure is the `Electronics`
   pool's genuinely first-ever exhaustion, yet real orders on it
   IMMEDIATELY.
2. **"A block's own first-ever failure is free, but only when the pool
   has no RSO/SLO (ESO is its sole mechanism)"** — this looked
   confirmed at first (fixed `HierarchyExample`'s own `BatteriesNaS`
   regression) but rested on a MISCOUNTED run boundary: re-checking
   `Capacity1`'s own `Bus_UR` failure correctly (its `t=683.967284`
   failure is genuinely `Bus_UR`'s OWN first-ever failure in that run,
   not its second as originally miscounted from the wrong `Begin_Run`
   block) shows real orders on it IMMEDIATELY too, on an ESO-only pool
   (`Electronics`, `RSO=0`/`SLO=0`) — directly refuting hypothesis 2 as
   well. **Lesson for future investigations**: always double-check
   which `Begin_Run_N` block a real capture line falls inside before
   counting a block's own failure history — a real capture spanning 10
   runs is easy to miscount by one block if you're not careful about
   exactly where each `Begin_Run` boundary falls.

## The pattern that survives all 5 known data points

| Fixture | Failure(s) | Sim-time | System's own first-ever failure? | Real orders? |
|---|---|---|---|---|
| `ReleaseProbe30` | `BlockB` (solo) | `t=1.0` | YES | NO (silent) |
| `ReleaseProbe33` | `BlockB`+`BlockC` (simultaneous) | `t=1.0` | YES | NO (silent) |
| `ReleaseProbe38` | `BlockC`+`BlockD` (simultaneous) | `t=1.0` | YES | NO (silent) |
| `Capacity1` `Bus_UR` | solo | `t=683.97` (mission ~730) | NO (system's own first failure was `t=134.46`, a different block) | YES (orders) |
| `HierarchyExample` `BatteriesNaS` | solo | `t=634.04` (mission ~730) | NO (other blocks fail earlier) | YES (orders) |

Every SILENT case is BOTH kickoff-adjacent (`t=1.0`) AND the system's
own first-ever failure. Every ORDERING case is BOTH deep into the
mission AND NOT the system's own first-ever failure. These two
candidate explanations (kickoff-adjacency vs. "is this the system's
very first failure, regardless of when it happens") are STILL
numerically indistinguishable across all 5 existing data points,
because a system's first-ever failure has only ever been OBSERVED at
kickoff so far. This probe deliberately breaks that coincidence.

## What this probe does

Clone of `ReleaseProbe33`'s own `input.rbd` (`Stock=1`, `RSO=0`,
`SLO=0`, `ESO=1` — the same already-tested ESO-only `Cells` pool) via
`tools/rbd_builder.py`, with exactly two fields changed:
- **`BlockA`/`BlockB`/`BlockC`'s own `FParam1`** (fail time):
  `FIXED(1.0)` → `FIXED(500.0)` — the SAME simultaneous 3-way failure
  `ReleaseProbe33` already tests, just moved from kickoff+1 to deep
  into the mission. **No other block anywhere in this model fails
  before this** (all three are the ONLY blocks, all start fresh at
  `t=0`), so this failure is UNAMBIGUOUSLY the system's own first-ever
  failure, exactly like `ReleaseProbe33`'s own `t=1.0` triple — the
  ONLY thing that changed is absolute sim-time.
- **`SYSTEM_DATA` `TermCondition`**: `300.0` → `700.0` (room to see the
  aftermath after the late first exhaustion).

`validate()`: zero warnings or errors (identical structure to
`ReleaseProbe33`, already proven safe).

## The question

At `t=500.0`, does real's own `"Spare_ordered"` line appear
IMMEDIATELY (matching `Capacity1`/`HierarchyExample`'s own "deep
mission, no grace" pattern) or is it ABSENT (matching `ReleaseProbe33`'s
own kickoff-time silence, generalizing "system's first-ever failure is
always free" to late-mission timing too)?

- **Real orders immediately at `t=500.0`** → confirms grace is a
  genuine KICKOFF-TIME special case, not a general "system's first
  failure" rule — the correct fix needs to gate on absolute sim-time
  being (near) zero, not on any failure-count/exhaustion-count logic
  at all. This would also mean the CURRENT, unconditional per-block
  order code is ALREADY CORRECT everywhere except a narrow kickoff
  window — a much smaller, more surgical fix than any attempted so
  far.
- **Real stays silent at `t=500.0`, ordering only at the blocks' own
  SECOND failure** (matching `ReleaseProbe33`'s own exact shape,
  shifted in time) → confirms "system's own first-ever failure is
  always free, regardless of when it happens" as the true general
  rule — `Capacity1`/`HierarchyExample`'s own non-grace cases are then
  correctly explained by NOT being the system's first failure (other
  blocks failed earlier in both), with no kickoff-specific carve-out
  needed at all.
- **Some other pattern** — note precisely what real shows at `t=500.0`
  and the following repair/reorder cycle; would point to a mechanism
  neither hypothesis anticipated.

## Predicted (this port)

This port's CURRENT (unconditional, no grace at all) behavior — orders
IMMEDIATELY at `t=500.0` for BOTH `BlockB` and `BlockC` (two concurrent
orders, same shape as `ReleaseProbe38`'s own `t=4.0` pattern):
```
0.000000    Block  BlockA  Good      InitializeStress=1.000000
0.000000    Block  BlockB  Good      InitializeStress=1.000000
0.000000    Block  BlockC  Good      InitializeStress=1.000000
500.000000  Block  BlockA  Failed    Operating_time=500.000000
500.000000  Block  BlockB  Failed    Operating_time=500.000000
500.000000  Block  BlockC  Failed    Operating_time=500.000000
500.000000  Block  BlockA  Repairing No_resources_used
500.000000  Block  BlockB  Sparing   No_spare_in_stock-Cells
500.000000  Pool   Cells   Sparing   Spare_ordered_(stock=0)   <- order #1
500.000000  Block  BlockB  RepHold   Waiting_for_Spare
500.000000  Block  BlockC  Sparing   No_spare_in_stock-Cells
500.000000  Pool   Cells   Sparing   Spare_ordered_(stock=0)   <- order #2, CONCURRENT
500.000000  Block  BlockC  RepHold   Waiting_for_Spare
502.000000  Block  BlockA  Repaired  MaintTime=2.000000
502.000000  Block  BlockA  Good      Stress=1.000000
```
This is deliberately NOT a confident prediction of what real does — it's
this port's own CURRENT code, unchanged since the last two fix attempts
were reverted. If real confirms the "kickoff-time special case"
hypothesis, this prediction is CORRECT (no fix needed for this specific
scenario). If real confirms "system's first failure is always free,"
this prediction is WRONG (real would show silence here instead). Either
way, this single real capture is decisive between the two remaining
hypotheses. All distributions `FIXED` — no RNG draws, purely an
event-order/timing question, same as `ReleaseProbe30`/`33`/`38`.

## How to capture it

Same procedure as the other `ReleaseProbeN` fixtures — launch Raptor7
Student with `/sd`, open this file, Simulate (3 runs — deterministic,
all runs identical). Read the event log for `BlockA`/`BlockB`/`BlockC`/
`Cells` across the whole 700-unit mission, paying specific attention to
whether `"Spare_ordered"` appears at `t=500.0` or is delayed to the
blocks' own second failure. Real-Raptor report outputs land in
`~/Documents/Raptor7/` (`rpt_events_*` etc.); the `/sd`
copy goes to this repo's root as `simDetails_ReleaseProbe39.txt`
(expect it empty — all blocks are `FIXED`, no RNG draws involved).

## REAL ANSWER (2026-09-05 capture, `~/Documents/
Raptor7/rpt_events_ReleaseProbe39.txt`) — D.1 SOLVED

**Decisive: real stays completely SILENT through the whole 700-unit
mission.** At `t=500.0`, `BlockB`/`BlockC` both go straight to
`"RepHold Waiting_for_Spare"` — **no `"Spare_ordered"` line anywhere**,
in either of the 2 real runs (both identical, all `FIXED`). `BlockA`
repairs normally at `t=502.0`, but with no order ever placed, `BlockB`/
`BlockC` simply stay parked for the REST of the mission — nothing else
happens at all until `Done`/`End_sim` at `t=700.0`:
```
500.000000  Block  BlockA  Failed    Operating_time=500.000000
500.000000  Block  BlockB  Failed    Operating_time=500.000000
500.000000  Block  BlockC  Failed    Operating_time=500.000000
500.000000  Block  BlockA  Repairing No_resources_used
500.000000  Block  BlockB  RepHold   Waiting_for_Spare
500.000000  Block  BlockC  RepHold   Waiting_for_Spare
502.000000  Block  BlockA  Repaired  MaintTime=2.000000
502.000000  Block  BlockA  Good      Stress=1.000000
700.000000  Block  BlockA  Done      End_sim
700.000000  Block  BlockB  Done      End_sim
700.000000  Block  BlockC  Done      End_sim
```
This **refutes the kickoff-time-special-case hypothesis** — the exact
same simultaneous first-ever failure that was silent at `t=1.0` in
`ReleaseProbe33` is STILL silent at `t=500.0` here, with nothing about
absolute sim-time changed except the number itself. **Confirms**: grace
covers the SYSTEM's own very first-ever failure this run, wherever and
whenever it happens to occur — not a pool-level condition, not a
per-block condition, not tied to kickoff.

**Fix implemented and verified.** Added `SystemState.first_failure_
time` (`raptor/core/engine/system_status.py`) — set once, the very
first time `record_component_failure` runs each run (already called at
every block's own "Failed" line, natural or induced alike). `block_
lifecycle.py`'s ESO-order branch now suppresses the order only when
`sim_time == system_state.first_failure_time`. Verified against ALL
FIVE real data points at once: `ReleaseProbe30`/`33`/`38`/`39` all
match exactly (including this probe's own decisive silence), and the
two real counter-examples that falsified every earlier attempt
(`Capacity1`'s `Bus_QR`/`Bus_UR`, `HierarchyExample`'s `BatteriesNaS`)
now correctly order, since none of their own failures are the system's
first. Full suite: 789/791 passed (the 2 `Events`-fixture failures are
pre-existing full-suite-ordering flakiness, confirmed present and
absent identically with and without this fix — unrelated). `Capacity1-
3`: `sd_diff` shows 970/970 draws matching, zero divergence.

**D.1 is SOLVED.** Full trail of all five fix attempts (three
falsified, the fourth revealing a self-caught run-boundary miscount in
this investigation's own method, the fifth confirmed): the internal
engine ledger's D.1, `tests/fixtures/ReleaseProbe30/38/README.md`.
