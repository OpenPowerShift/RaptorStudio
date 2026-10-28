# Fixture: SharedStreamTie — same-instant shared-stream draw order

Captured 2026-08-15 from Raptor 7 Student Edition under Wine. Same
two-block `join(1-of-2)->end` topology as `TwoBlockRedundancy`, one
change: both blocks' `FStream`/`RStream` set to `203` (the shared-stream
sentinel, `GetStream`'s `usesStream>200` branch — see `orchestrator.py`'s
`_make_rng`) instead of their own dedicated streams, with `Uniform(0,1)`
fail/repair (already CONFIRMED bit-exact, `tests/golden/UniformProbe`) so
any divergence could only be about DRAW ORDER, not distribution-transform
correctness. `NumRuns=5`.

## What this is for

Isolates `core/scheduler.py`'s long-flagged "same-instant event tie-
breaking is UNVERIFIED" gap (its own module docstring, since the
scheduler was first built) from `tests/golden/Cascade`'s conflated
result: `Cascade.rbd` (a real bundled Student Edition example, 13 blocks
all sharing stream 203) diverges from real captured output partway
through, and it wasn't obvious whether that was a tie-breaking bug or the
ALSO-unverified `Normal`/`LogNormal` RNG transform (`Cascade`'s blocks
repair with `Lognormal(10,2)`, and `rng.py`'s `_normal`/`log_normal` are
explicitly marked NOT VERIFIED — the same gap `NormalProbe`'s
investigation parked early in this project).

This fixture removes the Lognormal variable entirely. Result: **exact
match, zero structural mismatches, 9074 lines across 5 runs** —
`core.scheduler.Scheduler`'s existing FIFO/insertion-order tie-break for
two processes scheduled at the identical instant (`t=0.0`, both blocks'
`TELL...Run IN 0.0`) is CONFIRMED CORRECT, not just a placeholder. This
narrows `Cascade.rbd`'s remaining divergence down to the `Normal`/
`LogNormal` transform specifically — see `tests/golden/Cascade/README.md`.

**Scope note**: this only confirms the 2-simultaneous-processes case at
the very start of a run. It does NOT confirm tie-breaking for 3+
simultaneous processes, or for ties arising later mid-run (e.g. two
blocks whose independently-drawn next-event times happen to coincide
exactly, which never occurs with continuous distributions in practice —
the only realistic source of true ties is several blocks' `Initialize`
calls all landing at `SimTime=0.0` together, which is exactly what this
fixture tests).

## Files

- `input.rbd` — the model.
- `legacy/rpt_events_sharedstreamtie.txt` — event log, 5 runs, 9096 lines.

Verified via `tests/unit/test_orchestrator.py`.
