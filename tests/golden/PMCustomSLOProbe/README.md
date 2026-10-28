# PMCustomSLOProbe

Deterministic probe for **PM + Custom sparing + SLO** (stock-level
ordering), captured from real Raptor 2026-09-18.

Built by an internal build script.

## What it pins

- SLO-only (RSO=ESO off) must NOT be treated as "no spare will ever
  come": at t=21 the second PM logs `pmReqDefer No_spare_in_stock-Custom`
  and waits for the SLO order, rather than skipping PM
  ('s `NOT stockLevelOrdering` conjunct).
- `CustomSpares.OrderStock` replenishes the block's **private** queue,
 and the deferred custom-PM pickup consumes,
  re-orders and logs `Spare_Arrived` at t=50.
- The deferred pickup must not re-log `PM_is_due` (source resumes the
  suspended `ObtainPMprereqs` past that log).

## Port status

- EOR + final-results reports: **byte-identical** to real.
- Event log: **byte-identical** to real (2026-09-18). Fixed by making
  the immediate PM-spare `OrderStock` path synchronous (`tell`) and
  tagging it block-owned rather than `front_tier` (it is a TELL METHOD on
  the RBDBlockObj) — so `1_Spare(s)_ordered_` lands
  before `beginning_PM` at t=10. Registered in `test_orchestrator.py`.

All distributions are `Fixed`, so there are no RNG draws and no `/sd`
sweep axis.

