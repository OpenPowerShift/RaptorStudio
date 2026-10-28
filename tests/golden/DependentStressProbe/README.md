# DependentStressProbe — dependent block under non-unit phase stress

Built 2026-09-19 via an internal build script (clones
`NodeStandbyBackflowProbe`, adds a one-phase stress column to `Attitude`).

## What it pins

The dependent-block lifecycle `_dependent_block_process`
(`orchestrator.py`) hardcodes `phase_stress=1.0` and never computes
`combined_stress`, so a `dep_type=RBDBlock` block under a non-unit phase
stress is simulated at 1.0 (ER3-06).

Construction: `Attitude` depends on `LowGain` (`DepType=RBDBlock`,
`DepNum=6`, resource cleared) and is given a one-phase stress of **0.5**
(`Phasing=1`, `P 001 = "A:   0.500"`), with a long PHASES section.

## Expected real behaviour

`Attitude` should report `CombStress=0.500000` / `DependenceStress=0.500000`
throughout its phase-1 life. The port currently shows
`InitializeStress=0.500000` at t=0 but then **`DependenceStress=1.000000`**
on every subsequent recovery — i.e. the dependent path drops the phase
stress after the initial transition.

## Status: AWAITING REAL CAPTURE

Port (2026-09-19) shows the inconsistency above.

## To capture

Load `input.rbd` in Raptor7Student, **Launch ▸ Simulation…**, run the
fixture's own `NumRuns`, tick **Detailed event log**, OK. Save into
`legacy/` as `rpt_events_DependentStressProbe.txt` (+ end-of-run/final).
