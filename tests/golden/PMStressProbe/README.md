# PMStressProbe — PM/failure race under non-unit `CombinedStress`

Built 2026-09-19 via an internal build script (clones
`TwoBlockRedundancy`, adds a one-phase PHASES section and a per-phase
stress column).

## What it pins

Source divides `FailTimeLeft` by `CombinedStress` BEFORE the PM race and never scales `PMTimeLeft`. The port kept the
fail clock raw and compared it unscaled (ER3-01 "wrong stress units").

Model: `PmStress` block, phase-1 **Active** with stress **0.5**
(`P 001 = "A:   0.500"`), `fail = Fixed(100)`, `PMFreq = 150`,
`PM = Fixed(2)`, all-Fixed.

Under stress 0.5 the fail clock is worth **200** real-time units, so:

```
150.000000  Block  PmStress  PM_is_due    Scheduled_PM     <- PM wins
150.000000  Block  PmStress  PM           beginning_PM
152.000000  Block  PmStress  PM_complete  MaintTime=2.000000
```

If real instead compares `PMTimeLeft(150)` against the RAW fail (100),
the failure wins and there is **no PM** — the block fails at t=200. The
two outcomes are unambiguous and RNG-free.

## Port status (2026-09-19, after the ER3-01 stress fix)

Port shows `PM_is_due` at **t=150** (PM wins), matching the source-scaled
expectation. Covered by
`test_block_lifecycle.py::test_pm_race_uses_stress_scaled_fail_clock`.

## Status: AWAITING REAL CAPTURE

If real logs `PM_is_due` at **t=150**, the stress scaling is confirmed;
if it fails at **t=200** with no PM, the port's fix is wrong.

## To capture

Load `input.rbd` in Raptor7Student, **Launch ▸ Simulation…**,
**Number of runs = 1**, tick **Detailed event log**, OK. Save into
`legacy/` as `rpt_events_PMStressProbe.txt` (+ end-of-run/final-results).
Note: PM needs an edition that enables PM activities (Student greys it).
