# Fixture: PreventMxNoDefer — PM verification, phasing-free, pm_req_defer=False

Built 2026-08-21 from the textbook corpus's `legacy/rbd/PreventMx1_Completed.rbd`
(Chapter 6, Fig 6.15 — same satellite/block roster and PM configuration
as `Phasing1`, but with zero phasing, isolating PM from all the
phase-change complexity). Two edits from the source file, via
`raptor.core.fileio.rbd`'s load/edit/save API:

- `PMReqDefer` set to `0` (False) on every block. The original file has
  `PMReqDefer=1` on every block, which `block_run_process` currently
  can't simulate (`ObtainPMprereqs`'s defer-instead-of-hold branch, is genuinely unported — see
  `tests/golden/PreventMxDefer/README.md`, the sibling fixture built
  specifically to capture THAT behavior). Turning it off here exercises
  the ALREADY-PORTED "hold and wait for the resource" path instead, on
  a model that otherwise wasn't reachable at all.
- `Attitude`'s `PMFreq` lowered from `1500.0` to `100.0`. At `1500.0`
  (exceeding `run_length=730.5`), `Attitude`'s frequency-mode PM
  countdown (`pm_triggered=False`) never reaches zero within a single
  run, so it never actually fires — meaning NO existing fixture,
  including `Phasing1`, has ever exercised `block_lifecycle._pm_race`'s
  actual racing arithmetic against a real capture; it's still Tier C
  (see that function's own docstring). `100.0` matches the scale every
  OTHER PM block in this roster already uses, and produces real repeat
  PM cycles within the run (confirmed via this port's own trial run:
  15-21 `Attitude` PM-related lines per run across a 10-run batch).

`Attitude` is also the only block in this roster with
`pm_spare_needed=True` (Infinite sparing) — another currently-untested
code path this fixture exercises for the first time.

## What this is for

Ground truth for THREE currently-unverified things at once, once a real
Wine capture is in hand:
1. `_pm_race`'s frequency-mode PM-vs-failure race (`Attitude`, now
   actually fires).
2. The PM resource-pool hold-and-wait path under real contention
   (multiple trigger-PM blocks sharing the `Astronauts` pool).
3. `pm_spare_needed=True` with Infinite sparing.

This port already runs the file end-to-end with zero errors across all
10 runs (confirmed via `run_simulation` before any capture exists) —
the point of capturing it is verifying the OUTPUT is right, not just
that it runs.

## Files

- `input.rbd` — the model (see edits above).
- `legacy/` — not yet populated. Needs a real Wine capture: open
  `input.rbd` in Raptor7Student, Simulate, save the Event Log output.
