# Fixture: Events.rbd (official vendor example)

Captured 2026-08-14 from Raptor 7 Student Edition running under Wine
(prefix: `raptor_play/.wine-raptor`, install: `Raptor7Student/Examples/Events.rbd`).

## Run configuration
- Model: `input.rbd` (== `Examples/Events.rbd`, unmodified vendor example)
- Sim Options: **defaults** except all "File" tab checkboxes enabled
  (Fail Times, Rep Times, End Times, Key Params, Avail Plot, Cap Plot,
  Results, EOR Out, Event Log, Cost Report)
- Truncation: "to 1 failure" (per rpt_final_results_events.txt: "Results
  from 1000 runs to 1 failure")
- NumRuns: 1000
- Random stream seeds: the default `SysStreamsArray` baked into `input.rbd`'s
  own `SYSTEM_DATA` header (`7 8 9 10 70 71 72 73 74 75 101`) -- turned out
  not to need any special control: `rng.fetch_raptor_seed` is fully
  deterministic given that array, same as every other fixture, so this
  file reproduces bit-exact with no extra capture needed (see "Verified"
  below, 2026-08-18) -- the "not yet controlled" caveat this note
  originally carried was just an early-session caution, not a real gap.
- Only 3 of the 10 possible output files appeared: `rpt_events.txt`
  (Event Log), `rpt_final_results_events.txt` (Results, batch summary),
  `rpt_end_of_run_events.txt` (EOR Out, per-run detail). This model
  apparently has no blocks (0 blocks per its header), so block-oriented
  files (fail/rep times, cost, capacity, avail-plot) were presumably
  empty/suppressed — confirm against `input.rbd`'s header counts.

## What this is good for
- Verifying the Python `.rbd` reader against a real, non-trivial file.
- Verifying event-log line formatting (`WriteEvents` in):
  fixed-width time field, tab-separated type/name/action/comment.
- Verifying the Results/EOR report formatting and field layout
  (`***SUMMARY***`/`***LOGISTICS***` sections, N/A handling for
  not-applicable stats, 9-decimal vs 6-decimal field widths).
- This model uses **Bernoulli** events only (see "P=0.91" etc. event
  names — literally the probability parameter baked into the object
  name) — good first target for Phase 2 (RNG) unit tests once base
  UniformReal is recovered, since
  Bernoulli(p) = UniformReal(0,1) <= p.

## Verified 2026-08-19 — RBDEvent orchestration, all 1000/1000 runs bit-exact

Drove this fixture through `orchestrator.run_simulation` (not a hand-
wired driver -- this file has 0 blocks, so it's the first fixture that
exercises the `RBDEvent`-only kickoff path end to end): `_init_event`
ports `RBDEventObj.SetToInitValues`'s unconditional `ChangeEventState
(Armed,Active,"Initialize")` + `ConductEvent` pair, and `_Cascade.
apply_event` ports `ReconfigureEvent` (`BackFlowDependencies` +
`ReconfigureNode` on the event's own EFPA node) plus the event-side
`NumCurrentlyFailed` delta (`system_status.event_fail_count_delta`, a
SEPARATE `incFails`/`decFails` computation from the block one --
without it `NumCurrentlyFailed` would never leave 0 with no blocks in
the model, and the real `"System State_change Yellow"` line would be
unreachable).

One real ordering bug found and fixed along the way: the system-status
check is NOT per-event like it is for blocks (`_Cascade.apply`'s own
`top_level` check, which lives in 's per-block Run/Repair
wrapper -- events never go through that, they only ever run via
's `SetToInitValues`). Each failing event's own NODE
cascade still fires immediately, per-event (confirmed via run 2's real
capture: `n6 Degraded` right after `P=0.72`'s `Failed Fired`, THEN
`P=0.53`'s own Armed/Fired/node lines, THEN — only once, at the very
end — the single `"System State_change"` line). Fixed by moving the
`_check_system_status` call out of `_Cascade.apply_event` entirely, into
`run_simulation`'s event-init loop, called ONCE after every event has
initialized.

**Verification**: ALL 1000 of the file's real runs, all lines compared,
0 structural mismatches (14,478 lines total, byte-exact -- Bernoulli
draws are discrete success/fail, no float-rounding noise to classify).
See `tests/unit/test_orchestrator.py`'s `Events` parametrize case
(`compare_runs` no longer capped, now that all 1000 pass).

## `CallEndSimulation` early termination, closed 2026-08-19

Runs 22 onward exercise a real early-termination path: `ReconfigureSystem`
's EVENT-side method (`RBDEventObj`'s own analog
of the block-side mechanism `cascade.reconfigure_system` already ports,
called from `ConductEvent`'s own `WHEN Armed: ChangeEventState(Failure,
...); IF AllowReconfigureSystem: ReconfigureSystem(0)`,  -- only on a FRESH Armed->Failure fire, i.e. `fail_change==1`)
can trigger a real `"System Permanently_Down Out_of_spares"` line when
the End node's own path-reachability is exhausted. For a `termination_
type="FailSim"` model (this fixture's own config -- `TermCondition=1.0`,
"stop after 1 system failure"), that ALSO calls `CallEndSimulation`, which resets EVERY node silently (`ChangeNode
State(AllUp,Active)` called directly, not via `ReconfigureNode` -- no
`WriteEvents` in that path) and logs `ChangeEventState(Armed,activeStatus,
"End_sim")` for EVERY event, unconditionally, in kickoff order --
including ones not yet reached.

**The one real bug found**: the real `TELL System TO CallEndSimulation
(...)` call has NO explicit `IN delay` clause, meaning it's a QUEUED
zero-delay event (matching this project's own established `Scheduler.
interrupt`/`tell` distinction, see `scheduler.py`'s module docstring) --
NOT a synchronous interrupt of the current flow of control. An early
version of this port BROKE the event-init loop as soon as `Permanently_
Down` fired, which desynced the RNG stream for every run after the first
one that hit it (confirmed via run 30 onward suddenly diverging on
Success-vs-Failed draws, a classic stream-desync signature). Real run 29's
capture proved the fix directly: `P=0.53` still fires COMPLETELY normally
(its own `Armed`/`Failed`/`Fired` lines AND its own full node cascade)
even though `P=0.72`, two events earlier, already pushed the End node to
Permanently_Down -- the loop must run every event to completion first,
and only check `cascade.run_ended` once it's done. Fixed by removing the
early `break` and moving the `run_ended` check to after the loop.

See `orchestrator.py`'s `_Cascade.apply_event` (the `reconfigure_system`
call + `run_ended` flag) and `run_simulation`'s event-init loop (the
finalization pass) for the actual port.
