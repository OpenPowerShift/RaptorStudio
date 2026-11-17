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

---

## 2. Compatibility screening of all 27 examples

"Screened" means the model was exported and the conversion report was
read. Models marked **exact** were also simulated in both programs.
Models marked with a loss can still be exported, but the AWB run would be
a *reduced* model, so a difference could not be attributed to the engine.

| Example | AWB compatibility |
|---|---|
| AirlinerMission | rate definitions (6); formula-linked fields (12); mission phases (5); phased element schedules (7) |
| Bakery1 | repair distribution simplified to a fixed duration (1); node standby/priority settings (1); link switching and priority (6) |
| CarCare0 | repair distribution simplified to a fixed duration (1) |
| CarCare1 | repair distribution simplified to a fixed duration (1); preventive maintenance (1) |
| CarWash1 | mission phases (3); phased element schedules (1) |
| DC1 | repair distribution simplified to a fixed duration (1); spare pools (1); node standby/priority settings (1); link switching and priority (5) |
| DC2 | repair distribution simplified to a fixed duration (1); spare pools (1); preventive maintenance (1); node standby/priority settings (1); link switching and priority (5) |
| Drone1 | **exact** |
| Espresso1 | **exact** |
| EspressoDep1 | block dependencies (1) |
| FinaleShow1 | **not exportable**: event-only model, no RBD blocks |
| Flight1 | failure events (1); mission phases (4); capacity analysis (2); block dependencies (1); phased element schedules (2); node standby/priority settings (1); link switching and priority (3) |
| HospitalPower | repair distribution simplified to a fixed duration (8); rate definitions (7); formula-linked fields (17); spare pools (1); preventive maintenance (3); block cost data (9) |
| Lantern1 | **exact** |
| Laundromat1 | repair distribution simplified to a fixed duration (1) |
| ProdLine0 | repair distribution simplified to a fixed duration (12); block cost data (2) |
| ProdLine1 | repair distribution simplified to a fixed duration (12); preventive maintenance (1); block cost data (3) |
| TacoTruck1 | repair distribution simplified to a fixed duration (1) |
| Toaster_Expo | **exact** |
| Toaster_Fixed | **exact** |
| Toaster_Weibull | **exact** |
| Van1 | repair distribution simplified to a fixed duration (2) |
| VanFlat | repair distribution simplified to a fixed duration (2) |
| Vendo1 | repair distribution simplified to a fixed duration (1) |
| Water1 | capacity analysis (3); link switching and priority (2) |
| Water2 | capacity analysis (3); block cost data (3); link switching and priority (2) |
| WaterTreatment | repair distribution simplified to a fixed duration (6); rate definitions (6); formula-linked fields (15); spare pools (1); preventive maintenance (6); block cost data (10) |

### Reading the table

* **exact** — no reported loss; the comparison above applies.
* **repair distribution simplified** — RAPTOR has a repair *distribution*,
  AWB's generated task is a fixed duration with the same mean. The models
  can be compared if both sides use the fixed mean, but not as shipped.
* **spare pools / PM / dependencies / phases / capacity / standby /
  link switching** — AWB has features in these areas, but this converter
  does not populate them, so the AWB run is a reduced model.
* **rates/formulas/costs** — metadata or cost data; availability is still
  driven by the same numbers, but the conversion is lossy.
* **FinaleShow1** — a fault-tree/event model with no RBD blocks; AWB RBD
  export needs at least one block.

---

## 3. Conclusion

For the part of the model AWB and RAPTOR both represent, the two engines
agree on these examples within Monte Carlo error. The larger question for
the other examples is feature coverage, not engine disagreement: the AWB
converter deliberately refuses to invent capacity networks, PM schedules,
spares, dependencies or phase rules it cannot populate faithfully.

The full mapping and feature matrix are in
[`AWB_FORMAT.md`](AWB_FORMAT.md).
