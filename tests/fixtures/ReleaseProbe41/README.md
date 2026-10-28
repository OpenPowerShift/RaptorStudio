# ReleaseProbe41 — does real's LDT + Custom-sparing sequence (order placement, arrival, stock bookkeeping, LDT interaction) match the port's newly-ported code, which NO capture has ever exercised?

`legacy/rbd/demo/MotorDemo.rbd`'s own port (2026-09-04, see
The internal engine ledger D.5) was the first fixture combining
`dep_type=RBDBlock` + real LDT (`Pre-LDTDist`/`Post-LDTDist`) + finite
Custom sparing — ported by factoring `block_run_process`'s LDT/
Custom-sparing sequence into a shared sub-generator. It matched real
524/524 event lines across all 25 runs... but **its own default config
never exercises that sequence**: the mission is 36 hours while
`Armature` (Weibull(1.75, 174), median ≈84 h) and `Brushes`
(Weibull(2.3, 112), median ≈95 h) both outlast it, so neither ever
fails, orders a spare, or suspends on LDT inside the capture. The
new code path is verified only in the sense that its FIXTURE matched.

This probe changes exactly ONE field — mission `TermCondition`
`36.0 → 300.0` hours — so the pre-`t=36` portion of every run must
byte-match the existing golden capture (a built-in continuity check),
and everything after it is the first REAL comparison the LDT/
Custom-sparing code has ever faced.

## What the port's own 300-hour runs do (predicted shape, 3 runs spot-checked)

- `PowerSupply` (Custom sparing, stock starts 0, orders replenish):
  fails ~4x per 300 h; each failure runs the full sequence
  `Failed → Sparing No_spare_in_stock-Custom → Sparing
  Spare_ordered_(stock=0) → RepHold LDT → End_Pre-LDT (0.25)` → spare
  arrives 24 h after ordering (`Ordered_spare_arrives_` /
  `Spare_Arrived`) → `Repaired MaintTime=...` → `RepHold postLDT` →
  `End_Post-LDT (0.5)`.
- `Brushes` (Custom sparing, stock 0, ESO emergency, `EmerTime=100`):
  fails ~t=190 (`Operating_time=132.507613` in run 1), orders, sits in
  `RepHold LDT` 16.6 h, ESO spare arrives 100 h after ordering, and in
  run 1 the repair does NOT finish before `t=300` — the mission ends
  mid-repair (a real-terminatable shape worth confirming).
- `Armature` (Custom sparing, stock STARTS at 2, `NewSp=1` per 720 h):
  fails in ~90% of 300-h runs (its stock-DECREMENT-then-reorder branch
  differs from `PowerSupply`'s always-empty branch — run 1 happens not
  to exercise it; across 25 runs it usually fires).

No run hit the "permanently out of spares" terminal in the port's
spot check; if real ends any run early via `Done No_spares`, that is
itself decisive data for the D.5-flagged PM/`SLO`-only sibling check.

## The question

Do real's post-`t=36` event lines match this port's line-for-line —
specifically the order/arrival instants (`Spare_ordered_` at failure;
arrival exactly 24 h later for `PowerSupply`, `EmerTime=100` for
`Brushes`), the stock bookkeeping texts (`stock=0`), and the
LDT-suspend/resume interaction (`RepHold LDT` BEFORE the spare
arrives, `MaintTime` measured only after arrival, `postLDT` after)?

- **Byte-exact (modulo the fp32 noise class)** → the ported LDT/
  Custom-sparing sequence graduates from "fixture-matched but
  unexercised" to genuinely real-verified (Tier C → verified); D.5's
  MotorDemo entry closes its own caveat.
- **Divergence in ordering/arrival timing** → the sequence's
  scheduler-level shape (which wait the block parks in while the
  order is outstanding, and what the spare's arrival interrupts) is
  wrong somewhere the 36-hour fixture could never see; the first
  divergent line names the branch.
- **Real terminates early (`No_spares`) where the port continues**
  (or vice versa) → the D.5-flagged "permanently out" check difference
  is real, and the PM/`SLO`-only sibling question reopens with data.

## Capture

Same procedure as every probe (`/sd`, Raptor7 Student under Wine, 25
runs — the file's own `NumRuns`, unchanged): event log + reports land
in `~/Documents/Raptor7/`, the `/sd` copy at this
repo's root as `simDetails_ReleaseProbe41.txt`. FIRST check (before
anything else): each run's first `PowerSupply Failed` line must be
`35.623681` with the golden capture's own pre-`36` lines unchanged —
if not, the file didn't load as the same model. Read the run count
from the event log's `Simulation_Terminate` groups (the §70.4 lesson).

## REAL ANSWER (2026-09-05 capture, `simDetails_ReleaseProbe41.txt` + `~/Documents/Raptor7/rpt_events_ReleaseProbe41.txt`, 25 runs)

**The LDT + Custom-sparing sequence is REAL-VERIFIED** — after one
port fix. First comparison: every order/arrival/stock/LDT interaction
matched, but 3 of 25 runs (1, 12, 25) showed real logging an extra
line the port never emitted, and a fourth (run 22) had been hiding a
misalignment:

```
300.000000  Block  Brushes      Partial_Repair  RepairTime=9.265032
300.000000  Block  Brushes      Done            End_sim
```

**Real logs `Partial_Repair RepairTime=<time spent Repairing>` at
EndSimulation's Done sweep for any block still mid-repair** — `Brushes`
x3 (mid-ESO-emergency-repair; its repair-entry is the SILENT path, no
"Repairing" line) and `PowerSupply` x1 (run 22, mid ordinary repair).
Each `RepairTime` is exactly `SimTime - stateStart` of the Repairing
state. Spare-waiters and postLDT blocks get NO such line (real runs
5/24) — `RepWait` is exactly the right gate.

**Root cause of the miss: the DEPENDENT-block repair wait never set
`RepWait`.** `blocks.change_block_state`'s cost-analysis site already emits the line (verified against
`Phasing1`'s costAnalysis=1 capture), and MotorDemo IS costAnalysis=1
— but the ordinary `block_run_process` repair wait sets
`block.rep_wait = True` (`block_lifecycle.py`, `RepWait:=TRUE` port)
while the dependent-block generator's own copy of the repair wait
(`orchestrator.py`'s `_repair_with_ldt_and_sparing`, the D.5 refactor)
did not. `Brushes`/`PowerSupply` are `dep_type=RBDBlock` dependents —
that generator's exclusive territory. Fixed: `rep_wait` set at the
repair-wait entry, cleared at its natural completion before the
"Repaired" line, same relative order as the ordinary copy.

**Post-fix: all 25 runs match real with ZERO structural differences**
— line counts identical every run, all 214 remaining differing lines
classified as the established fp32/software-`ln` noise class (±1e-5
timestamps; this fixture's large Weibull-valued `Operating_time`/
`MaintTime` fields amplify ~1e-6 relative differences past the usual
ABS tolerance — the PreventMxDefer signature, not a new class). Full
15-fixture sweep + full test suite: zero regressions. D.5's MotorDemo
caveat ("fixture matched but sequence unexercised") is CLOSED: the
order→LDT→arrival→repair→postLDT sequence, the stock bookkeeping
texts, the ESO emergency path, and the termination-time `Partial_
Repair` behavior are all real-verified now.

Note: no run hit the "permanently out of spares" terminal (`No_spares`),
so the D.5-flagged PM/`SLO`-only sibling check remains unexercised —
a longer-mission variant (or smaller stock) would be its probe.
