# BlockDepDownPerc

`input.rbd` lives in `tests/fixtures/BlockDepDownPerc/` (resolved by `tools/golden_fixtures.py`).

## Captures (real Raptor 7 Student)
- `legacy/simDetails_BlockDepDownPerc.txt` -- per-draw RNG values (2026-08-29).
- `legacy/rpt_events_BlockDepDownPerc.txt` -- detailed event log, 20 runs, captured 2026-09-20 (`events15`).
  **Untraced**: the scheduler-trace attach failed (gdb script error, "The program is not being run", because it
  was attached while Raptor was still starting), so Raptor ran normally and there is NO scheduler trace for this
  capture. The event log itself is a genuine real run.

## Status
Byte-exact on draws (5513) and events (21643 rows) since 2026-09-20. The old residual ("real fires BlockB's PM
trigger at t=15, the port doesn't") was a symptom: the dependent lifecycle crossed fewer TELL boundaries than
an ordinary block -- see `tests/unit/test_dep_pm_events.py`.
