# SparingNoneProbe — `SparingType.NONE` exhausts, does not repair

Captured from real Raptor 2026-09-19 (`legacy/`); the `.rbd` predates
this session (no builder recorded here).

## What it pins

`CheckForSpare`'s `sparingType=None` branch
sets `sparesOut:=TRUE; spareAvail:=FALSE`, so
`DetermineNewState("Block Has Failed")` routes straight to
`ChangeBlockState(Done,...,"No_spares")` with **no repair**. Before the
ER3-05 fix the port fell through to the repair path, so a NONE-sparing
block repaired instead of exhausting.

Both blocks (`BlockA`, `BlockB`) fail once and go `Done "No_spares"` at
their first failure, then a `System Permanently_Down Out_of_spares`
line, then both `Done End_sim` at run end.

## Status: CONFIRMED 2026-09-19

Real capture (`NumRuns=5`, 18 lines/run) matches the port **byte-for-byte
across all 5 runs**; registered in `test_orchestrator.py`. Covered also
by `test_block_lifecycle.py::test_none_sparing_exhausts_to_done_no_spares`.

## Files

- `input.rbd` — 2 blocks, `SpType=None`, all-`Fixed`, `NumRuns=5`.
- `legacy/rpt_events_SparingNoneProbe.txt` etc. — the real capture.
