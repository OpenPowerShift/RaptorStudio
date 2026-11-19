# Availability Workbench vs RAPTOR: example validation

**Scope:** the 27 example models in `new_docs/models/`, tested against
Isograph Availability Workbench 5.0.0.0 under Wine.

The result is in two parts:

1. every example was **screened** for what AWB can represent;
2. the six examples that AWB can represent **exactly** were simulated in
   both programs and their availabilities compared.

AWB's demonstration mode was used. It opens and simulates projects but
blocks Save and Export; the `.awb` files were written by RAPTOR's own
exporter. No AWB licence or licence bypass was used.

---

## 1. Exact-compatible examples

Six examples use only features the converter carries without loss:
blocks, nodes, links, exponential/Weibull/fixed failure laws, no repair,
and no dependency/spares/PM/phase/capacity data.

| Example | RAPTOR mean availability | AWB mean availability | Difference (pp) | Difference / combined σ |
|---|---:|---:|---:|---:|
| `Drone1` | 0.85610 | 0.8545 | -0.160 | 0.48 |
| `Espresso1` | 0.71912 | 0.7138 | -0.532 | 1.28 |
| `Lantern1` | 0.69339 | 0.6933 | -0.009 | 0.02 |
| `Toaster_Expo` | 0.75374 | 0.7499 | -0.384 | 0.81 |
| `Toaster_Fixed` | 1.00000 | 1.0000 | 0.000 | 0.00 |
| `Toaster_Weibull` | 0.85782 | 0.8546 | -0.322 | 0.89 |

Every difference is within **0.6 percentage points** and within **1.3
combined standard errors**.  There is no systematic AWB bias in this
sample: the small differences are consistent with Monte Carlo noise.

### Method

For each model:

* 10,000 replications, the example's own lifetime and failure/repair data;
* RAPTOR: `raptor run MODEL -n 10000 -j 1 -f json`, reading
  `summary.Availability.mean` and `summary.Availability.sem`;
* export with `raptor convert MODEL MODEL.awb`, open in AWB, run
  **Simulation → Start**, then read **Simulation Results → Systems**:
  `Mean unavailability` and `Std total downtime`;
* AWB availability is `1 - mean unavailability`;
* the AWB standard error is estimated from the time-weighted downtime:
  `std total downtime / (sqrt(10000) * lifetime)`;
* the combined σ is the root sum of the RAPTOR and AWB standard errors.

AWB shows the mean unavailability to four decimal places, so the AWB
availability carries about ±0.00005 of display rounding on top of its
sampling error.

### What this checks

* series and parallel block logic, including start/end nodes;
* exponential, Weibull and fixed failure laws;
* no-repair blocks (`NonMaintainable` in AWB);
* lifetime and trial-count transfer;
* the direct `.awb` writer and reader.

`Toaster_Fixed` is a useful edge case: its fixed failure time is 1000 h
and the run is 600 h, so both programs correctly report no failures and
availability 1.0000.

### Phase check: CarWash1

`CarWash1` became format-exact once phase rules were mapped, but its
1000-trial result is not yet counted as validated:

| | RAPTOR | AWB |
|---|---:|---:|
| Availability | 0.6970 | 0.6914 |
| System failures | 69.06 | 69.01 |

The failure count matches; the 0.56 percentage-point availability
difference points at phase/mission semantics (the first phase is
non-mission in RAPTOR, and AWB has no equivalent mission flag).  It is
listed as **format exact, result under investigation**, not as a clean
comparison.

### Normalized comparison: CarCare0

`raptor convert ... --normalize-out` reduced CarCare0's lognormal repair
to its fixed mean and wrote both files.  At 1000 trials:

| Example | RAPTOR | AWB | Difference (pp) |
|---|---:|---:|---:|
| CarCare0 | 0.990345 | 0.9904 | 0.0055 |
| Van1 (2 hierarchies) | 0.986632 | 0.9869 | 0.026 |

Both are within display precision.  This validates the normalization
pathway for repair-law-only differences, including a model with nested
hierarchies.

### Feature smoke test (Stage 1/2)

A synthetic two-block model with one PM task, a custom spare, a resource
pool, two phases, a text label and one cut block was exported and opened in
AWB.  AWB simulated 10 replications and showed the **Phases**, **Spares** and
**Labor** tabs with non-zero labour/spare costs.  The same `.awb` imported
back to RAPTOR with 0 validation errors and preserved the PM, spare/resource
links, phase cut/linked token and cut block.  This is a mapping smoke test,
not a 10,000-trial engine comparison.

### Large-model check

`ProjectX_BESS_RAM.rbds` (8.4 MB) has 3,470 blocks, 900 nodes, 217
hierarchies, 5,184 links and capacity analysis.  It exported in 3.6 s to a
3.0 MB `.awb` plus a 3.4 MB sidecar; AWB 5.0 opened the `.awb` without
validation errors.  The `.awb` plus sidecar imported back to RAPTOR with 0
validation errors.  Capacity and random repair laws were the only reported
losses: capacity is preserved in the sidecar, and the repair law is
mean-matched for AWB.  AWB demonstration mode may still refuse to analyse a
project of this size; that is its object limit, not a file-format failure.

### Shipped AWB examples

Stored AWB results vs RAPTOR on the imported model (100 trials, AWB's own
count; same lifetime).  Measured 2026-11-20 with the junction-node fix from
`main` (#339) applied: without it every AWB fan-out is mis-wired.

| AWB project | AWB Am | RAPTOR A | Result |
|---|---:|---:|---|
| OilAndGasExample | 0.9732 | 0.9747 | agrees (+0.15 pp) |
| SapDemo | 0.6293 | 0.6522 | agrees within the approximations (+2.3 pp) |
| Example1 | 0.0883 | 0.0959 | agrees within the approximations (+0.8 pp) |
| MaximoDemo | 0.6118 | 0.3654 | AWB's stored results look stale (below) |

`ALTExample` and the `PumpExample` library example have no RBD blocks.

Three import defects made Example1/SapDemo/MaximoDemo look "not
comparable" (≈0.5 % available) before this:

* **Spare pools ran dry.**  An AWB spare with no stock came in as a RAPTOR
  pool with no ordering, so the first demand left the block `Done`
  (`No_spares`).  AWB level 3 is unlimited; pools now emergency-order after
  the level 1+2+3 delay.
* **Planned tasks waited for spares.**  AWB's spare logistic delays hold
  up corrective actions only; imported PM no longer draws on the pool.
* **Bi-Weibull picked a placeholder mode.**  The shortest-η rule included
  the unused third-mode columns (8760 h).  Multi-mode laws are now
  moment-matched to the competing-risk life.

The per-subsystem figures (top-level superblocks, each run on its own) agree
to within about 2 pp for Example1 and SapDemo.  The dormant `PO` block,
modelled with the inspection detection-delay approximation, gives 0.682 vs
AWB 0.655 (SapDemo) and 0.183 vs 0.175 (Example1, inspection disabled, never
repaired).  RAPTOR's outage counts run lower than AWB's `W` because AWB
counts zero-duration PM/inspection outages that RAPTOR does not.

**MaximoDemo.**  Its spares carry level-2/3 delays of 96 h and 480 h, like
Example1's, but AWB's stored per-block spare downtime is only about the
level-1 delay (for example 45 h over 1.5 demands for the drive shaft), and its
subsystem results are almost identical to SapDemo's, which has no level-2/3
delays.  RAPTOR's MaximoDemo subsystems instead resemble AWB's own Example1
results (ELEC 0.67 vs 0.73, MON 0.80 vs 0.80).  The stored results were most
likely computed before those delays were entered.  This has not been checked
by re-running AWB.

---|---:|---:|---|
| OilAndGasExample | 0.973217 | 0.974734 | Δ 0.152 pp; agrees |
| Example1 | 0.088322 | 0.006982 | not comparable |
| SapDemo | 0.629333 | 0.005037 | not comparable |
| MaximoDemo | 0.611809 | 0.005037 | not comparable |

`ALTExample` and the `PumpExample` library example have no RBD blocks.

The new inspection approximation improves the model but does not make
Example1/SapDemo/MaximoDemo comparable: Example1 has its inspection
disabled, while SapDemo/MaximoDemo still lose PM/inspection timing,
multi-mode Weibull shape and topology semantics. OilAndGasExample remains
the clean shipped-AWB comparison.

---

## 2. Compatibility screening of all 27 examples

"Screened" means the model was exported and the conversion report was
read.  The six models in the comparison above were simulated in both
programs; `CarWash1` also became exact once phases were mapped, but it is
not part of the 10,000-trial comparison yet.  Models marked with a loss can
still be exported, but the AWB run would be a *reduced* model, so a
difference could not be attributed to the engine.

| Example | AWB compatibility |
|---|---|
| AirlinerMission | rate definitions (6); formula-linked fields (12); node and hierarchy phase schedules (1) |
| Bakery1 | repair distribution simplified to a fixed duration (1); node standby/priority settings (1); link switching and priority (6) |
| CarCare0 | repair distribution simplified to a fixed duration (1) |
| CarCare1 | repair distribution simplified to a fixed duration (1) |
| CarWash1 | **exact** (phases now exported) |
| DC1 | repair distribution simplified to a fixed duration (1); node standby/priority settings (1); link switching and priority (5) |
| DC2 | repair distribution simplified to a fixed duration (1); node standby/priority settings (1); link switching and priority (5) |
| Drone1 | **exact** |
| Espresso1 | **exact** |
| EspressoDep1 | block dependencies (1) |
| FinaleShow1 | **not exportable**: event-only model, no RBD blocks |
| Flight1 | failure events (1); capacity analysis (2); block dependencies (1); node standby/priority settings (1); link switching and priority (3) |
| HospitalPower | repair distribution simplified to a fixed duration (8); rate definitions (7); formula-linked fields (17); block cost data (9); additional or non-fixed PM (2) |
| Lantern1 | **exact** |
| Laundromat1 | repair distribution simplified to a fixed duration (1) |
| ProdLine0 | repair distribution simplified to a fixed duration (12); block cost data (2) |
| ProdLine1 | repair distribution simplified to a fixed duration (12); block cost data (3) |
| TacoTruck1 | repair distribution simplified to a fixed duration (1) |
| Toaster_Expo | **exact** |
| Toaster_Fixed | **exact** |
| Toaster_Weibull | **exact** |
| Van1 | repair distribution simplified to a fixed duration (2) |
| VanFlat | repair distribution simplified to a fixed duration (2) |
| Vendo1 | repair distribution simplified to a fixed duration (1) |
| Water1 | capacity analysis (3); link switching and priority (2) |
| Water2 | capacity analysis (3); block cost data (3); link switching and priority (2) |
| WaterTreatment | repair distribution simplified to a fixed duration (6); rate definitions (6); formula-linked fields (15); block cost data (10); additional or non-fixed PM (5) |

### Reading the table

* **exact** — no reported loss; the comparison above applies.
* **repair distribution simplified** — RAPTOR has a repair *distribution*,
  AWB's generated task is a fixed duration with the same mean. The models
  can be compared if both sides use the fixed mean, but not as shipped.
* **PM / spares / labour / phases** — now partially mapped:
  one planned task per failure model, one spare pool and one resource pool
  per block, phase durations and block cut/linked rules. Inspections, extra
  costs, repair-shop recycling and capacity/load rules remain lossy.
* **capacity / dependencies / standby / link switching** — AWB has features
  in these areas, but this converter does not populate them, so the AWB run
  is a reduced model.
* **rates/formulas/costs** — metadata or cost data; availability is still
  driven by the same numbers, but the conversion is lossy.
* **FinaleShow1** — a fault-tree/event model with no RBD blocks; AWB RBD
  export needs at least one block.

---

## 3. Conclusion

For the part of the model AWB and RAPTOR both represent, the two engines
agree on these examples within Monte Carlo error. The larger question for
the other examples is feature coverage, not engine disagreement.  The
Stage 1 mappings now carry PM, spares, labour and phase rules; capacity,
consequences, RCM/fault trees and exact cost models remain outside the
converter.

The full mapping and feature matrix are in
[`AWB_FORMAT.md`](AWB_FORMAT.md).
