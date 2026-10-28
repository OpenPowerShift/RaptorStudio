# WeakAnalysisProbe — WEAK_LINK_ANALYSIS `EVENT_RESULTS` + `HIERARCHY_RESULTS`

Built 2026-09-19 via an internal build script (clones
`Phasing1`, sets `SYSTEM_DATA.weakAnalysis=1`).

## What it pins

Source's `DoEndOfRunUpdate` weak-link section
writes four sub-tables when `weakAnalysis` is on:

1. `NODE_RESULTS`
2. `BLOCK_RESULTS`
3. `EVENT_RESULTS` — `Ao Do R %Success %Armed %Failure`
4. `HIERARCHY_RESULTS` 

The port's `_build_weak_section` (`reports.py:746-799`) writes only (1)
and (2); (3) and (4) are absent (ER3-22). `Phasing1` is the natural probe:
it is the only family with BOTH `HIERARCHY_DATA` rows and `EVENT_DATA`
rows, and it has phasing/missions.

## Status: AWAITING REAL CAPTURE

Port (2026-09-19) emits NODE_RESULTS + BLOCK_RESULTS but **no**
`EVENT_RESULTS` and **no** `HIERARCHY_RESULTS`.

## To capture

Load `input.rbd` in Raptor7Student, **Launch ▸ Simulation…**, run the
fixture's own `NumRuns`, tick **End of run** (the WEAK_LINK_ANALYSIS
section lives in the End-of-Run report) plus **Detailed event log**, OK.
Save into `legacy/` as:

- `rpt_end_of_run_WeakAnalysisProbe.txt`  (the key file)
- `rpt_events_WeakAnalysisProbe.txt`
- `rpt_final_results_WeakAnalysisProbe.txt`
