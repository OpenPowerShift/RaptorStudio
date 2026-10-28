# NodeStandbyBackflowProbe — block goes Standby → dependents follow

Built 2026-09-19 via an internal build script
(clones `CS_HigherCold`, repoints `Attitude` to depend on `LowGain`).

## What it pins

When a block goes to `Standby` (a cold-standby switch-off), source
`ChangeBlockState` fires `BackFlowDependencies(NodeStandby)`, which transitions each dependent Running/Idle
block to `Standby` ("Dependence", reason `Dependence_Standby`) and
wait-gates the wake. The port's `back_flow_dependencies`
(`dependency.py:360-366`) treats `NodeStandby` as a NO-OP (ER3-07).

Construction: `CS_HigherCold`'s `LowGain` repeatedly goes
`Standby Too_many_on` via the cold switch. `Attitude` is repointed to
depend on `LowGain` as an `RBDBlock` (`DepType=RBDBlock`, `DepNum=6`,
`DepIdle=100`, `defDepStateIdle=1`) and its resource cleared
(`NumRes=0`) so the dependent-block path accepts it.

## Expected real behaviour

Whenever real logs `LowGain Standby Too_many_on`, `Attitude` should
**also** transition to `Standby` (reason `Dependence`/`Dependence_Standby`)
in the same instant. The port currently leaves `Attitude` alone (it stays
`Good`/`Idle`), so the event logs diverge at the first such switch-off.

## Status: AWAITING REAL CAPTURE

Port (2026-09-19) runs cleanly and shows the gap: e.g. at t=2.000020
`LowGain Standby Too_many_on` with **no** `Attitude` transition.

## To capture

Load `input.rbd` in Raptor7Student, **Launch ▸ Simulation…**, set the
run count as-is (the fixture's own `NumRuns`), tick **Detailed event
log**, OK. Save into `legacy/` as
`rpt_events_NodeStandbyBackflowProbe.txt` (+ end-of-run/final-results).
