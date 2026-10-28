# Fixture: TwoBlockRedundancy — first multi-block/K-out-of-N node fixture

Captured 2026-08-15 from Raptor 7 Student Edition under Wine. Unlike every
prior fixture (`UniformProbe`/`NormalProbe` GUI-drawn, `Hierarchy1-7`
copied verbatim from Raptor 7), `input.rbd` here was
**hand-built by our own `.rbd` writer** (`raptor/core/fileio/rbd.py`'s
`save_rbd`, starting from a loaded copy of `UniformProbe/input.rbd` and
edited via `raptor.core.fileio.rbd`'s section/row API — see
`/tmp/.../scratchpad/build_two_block_rbd.py` in that session, not
committed here since it's a one-off script, not reusable tooling yet).

## Topology

```
start --1--> block(id3) --2--> join(k=1,n=2) --5--> end
      --3--> block(id4) --4-------^
```

Two blocks in parallel (both `unnamed`, both Uniform Real(0,1) fail/repair
like `UniformProbe`, but independent streams 1/2 for block 3 and 3/4 for
block 4), feeding a `join` node (`Type=2`, `k=1`, `n=2` — a 1-of-2
redundancy gate, the same field pattern confirmed real via
`Hierarchy1-7.rbd`'s `n5`-`n12` nodes), which feeds
`end` (`Type=3`, `n=1`). `SYSTEM_DATA.NumRuns=20` (vs `UniformProbe`'s 500
— this fixture is for structural/topology verification, not RNG-draw-count
scale).

## First real crash — and a genuine writer bug found

The first attempt at this file crashed the real engine outright (process
exit, `mserr.txt` = `"MODSIM Runtime error: Unknown"`), with no link
lines ever drawn on the canvas — objects rendered, links didn't. Isolated
via a sequence of targeted diagnostics (round-trip the *unchanged*
`UniformProbe` content through our writer; then surgically patch just one
section's divider or row spacing at a time) down to: **`render_rbd` never
emitted a bare `"/"` line terminating an empty `COMMENTS` section.**

`COMMENTS` has no `EndOf...Data` sentinel (see `_END_MARKERS`'
docstring in `rbd.py`); real files terminate an empty one with a bare
`"/"` line instead. Our *reader* already tolerated this (skips it as
noise, per `parse_rbd`'s `candidate.strip() == "/"` branch) but never
stored it anywhere, so the *writer* silently dropped it on any
render. Invisible to a `parse -> render -> reparse` self-consistency
round trip (both sides agree with or without the line) but fatal to the
real engine: without it, the `LINK_DATA` section marker and header get
consumed as bogus `COMMENTS` data, corrupting every read after it —
exactly matching the observed symptom (objects fine, links never drawn).
Fixed in `rbd.py`; regression test in `test_fileio_rbd.py::
test_empty_comments_section_emits_bare_slash_terminator`. This was the
first time any `render_rbd`-written file had ever been fed back to the
real engine — every previous fixture avoided the writer's blind spot by
construction.

## Files

- `input.rbd` — the model (see Topology above).
- `legacy/rpt_input_two_event.txt.gz` — full event log, 20 runs, 36,576
  lines (gzipped like `UniformProbe`'s).
- `legacy/rpt_input_two_end.txt`, `legacy/rpt_input_two_results.txt` —
  the other two output files (per-run summary, aggregate summary).

## What this is for

The `join` node's `Node`/`Degraded`/`Bad`/`Good` lines here are the
**first real confirmation of the `Degraded` status branch** in
`determine_node_status` (`nodes.py`) — every prior fixture only ever
exercised the 1-of-1 `Bad`/`Good` case. Sample from the real log
(`Begin_Run_1`):

```
0.452836   Block   unnamed   Failed      Operating_time=0.452836
0.452836   Block   unnamed   Repairing   No_resources_used
0.452836   Node    join      Degraded    1-1/2_ur2_df4
0.452836   System  State_change   Yellow   _d4
...
1.045626   Block   unnamed   Failed      Operating_time=0.430722
1.045626   Block   unnamed   Repairing   No_resources_used
1.045626   Node    join      Bad         0-1/2_df2_df4
1.045626   Node    end       Bad         0-1/1_df5
1.045626   System  State_change   Red    _d3_d4
```

Also the first fixture with a **two-hop node cascade** (block -> join ->
end, `end` only changes when `join`'s own status crosses a boundary) and
genuine **two-object concurrency** (two independently-scheduled blocks on
one calendar) — the two things the migration had flagged as
"infrastructure exists, not exercised yet". Use this fixture to verify
the scheduler-driven multi-process port once it's written (see
`core/scheduler.py`, currently only exercised with one process at a
time in `test_block_lifecycle.py`).
