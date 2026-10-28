# PMCustomSLOResourceDeferProbe

Deterministic probe for **PM resource deferral + Custom sparing + SLO**,
captured from real Raptor 2026-09-18.

Built by an internal build script.

## What it pins

- `PmCustom` needs the `Techs` resource for PM; `ResHolder` holds it
  t=5..25. The t=10 PM logs `pmReqDefer No_resource_in_stock-Techs` and
  also consumes its last Custom spare, placing an SLO order due t=50.
- t=25: Techs released, `Resources Obtained_1_Resource(s)`, PM begins.
- t=36: PM due again, Custom stock still 0 → `pmReqDefer
  No_spare_in_stock-Custom`.
- t=50: the SLO order arrives, `Spare_Arrived`, re-order, PM begins.

## Port status

- EOR + final-results reports: **byte-identical** to real.
- Event log: **t=10 matches real as of 2026-09-20** (`pmReqDefer` precedes `ordered`): `OrderStock` starts as its
  own selection and `PerformPM` is a TELL boundary (see the internal engine ledger).
- **One divergence remains, at t=50**: real runs `ResHolder`'s repair completion (releasing `Techs`, inserted
  t=30) before `PmCustom`'s SLO `arrived` line (inserted t=10) — the only in-run FIFO violation seen in a
  live scheduler trace; unexplained. Pinned by a strict xfail in `tests/unit/test_pm_custom_slo_probes.py`
  (`ChooseNext`/`ActivityGroup` class, ER3-29).

## Capture hygiene

All distributions are `Fixed` → this model draws **no** RNG, so there is
no legitimate `/sd` axis. The first `simDetails` capture was
append-contaminated (it contained draws for `SolarP_900W`, `BatteriesNiCd`,
`Bus_FR`, ... — blocks from a different model) and was discarded. A real
`/sd` capture would require closing any prior Raptor first. The event log is
compared byte for byte by `tests/unit/test_event_log_fidelity.py`.
