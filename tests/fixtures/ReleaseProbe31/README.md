# ReleaseProbe31 — does entering a NonMission phase defer a due PM (`PMMisDefer`), or is it a silent no-op as this port's own engine currently treats it?

The internal engine ledger B.6: `ConductPhaseChange` (real MODSIM
source) adds a block to a `pmDeferGroup` when
it enters a "NonMission" phase (`PHASES`' own `Mission=0` column) mid-
mission while its own `PMMisDefer` config flag is set — releasing it
later via a `Start_Deferred_PM` interrupt when the block exits that
NonMission phase. **This port does not implement this at all.**
Corrected during this probe's own design pass: the doc entry previously
said this "still raises `NotImplementedError`" — WRONG, checked
directly. `block.defer_pm` IS checked as a guard that WOULD raise if
`True` (`orchestrator.py:2497`, `block_lifecycle.py:2457`) — but
nothing anywhere in the engine ever SETS it. `pm_mis_defer` (parsed
from the `.rbd`'s own `PMMisDefer` column) is stored on the model and
never read again. **`PMMisDefer=1` is a completely silent no-op.**

## Revision history — crashed real Raptor on SIMULATE, rebuilt from scratch on a proven base

The first construction (a hand-built minimal 3-block fixture cloned
from `ReleaseProbe27`'s own template, with a hand-added 3-phase
`PHASES` table) crashed real Raptor on simulate. `ReleaseProbe27`
itself hit the SAME crash class three separate times on a similarly
hand-built template (see that fixture's own README for the full
history) before being rebuilt from `tests/golden/Phasing3/input.rbd`
wholesale — a real, already-proven file — rather than another targeted
field patch on unproven ground. **This fixture uses that same
strategy.**

Built by cloning `Phasing3` **completely unchanged** except TWO
already-exercised scalar fields:
1. The `500W-PowerString` trigger's own distribution: `Dist=8` (a long
   random mean) → `FIXED(21.0)` (`Dist=19`) — this port's own run
   (checked directly) shows `D301-360_Orbit` (Phasing3's OWN one
   `Mission=0` phase) beginning at `t≈19.7-20.2` and lasting until
   `t≈23.6-24.3` across runs — `21.0` lands the trigger fire squarely
   inside that window in every run.
2. `SolarP_500W`'s own `PMMisDefer`: `0→1`.

No `PHASES` section edit, no `P NNN` column edit, no `NODE_DATA`/
`HIERARCHY_DATA` edit — every part of the file previously implicated in
a crash (in `ReleaseProbe27`'s own history) is byte-identical to a file
already proven to load and simulate successfully in real Raptor.

## What this port's own run actually does (confirms the silent no-op directly)

All 3 runs, consistent shape:
```
19.711478  System  Phase_change  Phase_change  Begin_phase_D301-360_Orbit
21.000010  Trigger 500W-PowerString  Fired      System_event
21.000010  Block   SolarP_500W    PM_is_due     Triggered_PM_Due
21.000010  Block   SolarP_500W    PM            beginning_PM
22.410160  Block   SolarP_500W    PM_complete   MaintTime=1.410150
22.410160  Block   SolarP_500W    Idle          change_block_state
23.619318  System  Phase_change  Phase_change  Begin_phase_D000-075_Orbit
```
`SolarP_500W` becomes PM-due at `t=21.0`, squarely inside the
NonMission phase (`t≈19.7-23.6`), and proceeds through `beginning_PM`→
`PM_complete` IMMEDIATELY — zero deferral, zero exception, zero log
trace of `PMMisDefer` ever mattering. (`BatteriesNiCd`, the OTHER
`500W-PowerString` subscriber, also fires normally at the same instant
— included here only as background noise from Phasing3's own existing
topology, not itself under test; `PMMisDefer` stays `0` for it.)

## The question

Does real actually defer `SolarP_500W`'s own PM here, or does it also
proceed immediately (matching this port's own current — very likely
WRONG — silent-no-op behavior)?

- **Real proceeds immediately too, matching this port** → surprising,
  but would mean `PMMisDefer` genuinely has no effect in this exact
  scenario (worth double-checking the `.rbd` field mapping/semantics
  are right before concluding the source's own `pmDeferGroup`
  mechanism just doesn't apply here) — reduces B.6 to a documentation-
  only gap, not a behavioral one.
- **Real DEFERS the PM** (expect something like a distinct log line —
  maybe silence with no `PM_is_due` at all until phase exit, or an
  explicit deferral message analogous to `pmReqDefer`'s own
  `"No_resource_in_stock-..."` line) **until the phase exits at
  `t≈23.6-24.3`, then resumes** → confirms B.6's own mechanism
  precisely; note EXACTLY what the deferral's own log line says (if
  any), whether the resumed PM reuses in-flight state or restarts from
  scratch (echoing this session's own `race_raw_life`-survival finding,
  the internal investigation log §69/§70), and whether release is
  immediate on phase exit or waits for some later check.
- **Some other pattern** (e.g. the PM_is_due itself gets suppressed
  entirely, never firing at all) → note the full real event log across
  `t≈19.7-24.3` precisely.

## Predicted (this port)

See "What this port's own run actually does" above — immediate PM,
zero deferral (this port's own current, unimplemented behavior, NOT a
confident prediction of what real does — the whole point of this probe
is to find out).

## How to capture it

Same procedure as the other `ReleaseProbeN` fixtures — launch Raptor7
Student with `/sd`, open this file, Simulate (matching Phasing3's own
10-run capture count), read the FULL event log for `SolarP_500W` across
`t≈19.7-24.3` in every run. Real-Raptor report outputs land in
`~/Documents/Raptor7/` (`rpt_events_*` etc.); the `/sd`
copy goes to this repo's root as `simDetails_ReleaseProbe31.txt`.

## REAL ANSWER (2026-09-04 capture, `simDetails_ReleaseProbe31.txt` + `~/Documents/Raptor7/rpt_events_ReleaseProbe31.txt`)

**Not the silent no-op this port currently implements — and not what
either candidate hypothesis in "## The question" anticipated either.**
Real's own behavior, consistent across all 10 runs (4 checked in
detail):
```
19.688146  System  Phase_change  Phase_change  Begin_phase_D301-360_Orbit
19.688146  Block   SolarP_500W   PM_is_due     Start_Deferred_PM
19.688146  Block   SolarP_500W   PM            beginning_PM
20.958162  Block   SolarP_500W   PM_complete   MaintTime=1.270015
20.958162  Block   SolarP_500W   Good          Stress=0.500000
21.000010  Trigger 500W-PowerString  Fired      System_event
21.000010  Block   SolarP_500W   PM_is_due     Triggered_PM_Due
21.000010  Block   SolarP_500W   PM            beginning_PM
22.818162  Block   SolarP_500W   PM_complete   MaintTime=1.818152
```

**`SolarP_500W` gets a `"PM_is_due" "Start_Deferred_PM"` line at the
EXACT instant it enters the NonMission phase** (`t=19.688146`,
identical timestamp to `Begin_phase_D301-360_Orbit`) — this is a
COMPLETELY SEPARATE PM-due event from the ordinary trigger-based one
(`"Triggered_PM_Due"`, which still fires normally and separately at
`t=21.000010` when the `500W-PowerString` trigger itself fires).
Confirmed the necessary condition: `BatteriesNiCd` (same trigger, same
phase, but `PMMisDefer=0`, unchanged) gets ONLY the ordinary
`"Triggered_PM_Due"` at `t=21.0` — no `Start_Deferred_PM` event at
`t=19.688146` at all. **`PMMisDefer=1` causes entering a NonMission
phase to trigger an ENTIRELY NEW, otherwise-unscheduled PM-due
opportunity for that block — it does not defer/suppress an already-
scheduled one.**

This new phase-triggered PM opportunity is itself subject to ORDINARY
resource availability, same as any other PM: run 1 shows it proceeding
immediately (resource free); **run 2 shows it genuinely deferring**
(`"pmReqDefer" "No_resource_in_stock-Astronauts"`, the SAME ordinary
mechanism this session's own §68/§69 fixes already handle for
trigger/frequency-based PM) — and, in that run, the deferred PM does
NOT appear to resolve again within the NonMission window at all (no
`beginning_PM`/`PM_complete` for it before the phase exits at
`t=24.158982`) — worth checking a wider window in a follow-up capture
to see if it EVER resolves, and whether `Start_Deferred_PM`'s own name
implies a SECOND, later interrupt (a genuine `pmDeferGroup`-style
release) that this narrow capture window didn't happen to show.

### What this means for implementing B.6

This port's own doc entry (and this probe's own original design)
assumed `pmMisDefer`/`PMMisDefer` modifies what happens to an
ALREADY-due PM when NonMission starts (hold it, release on exit). Real
instead appears to make ENTERING NonMission itself a NEW PM-due
trigger point for `PMMisDefer=1` blocks — independent of, and
IN ADDITION TO, whatever their own ordinary PM-due mechanism (trigger
or frequency) already does. The name `Start_Deferred_PM` is itself
worth taking literally: it may specifically mean "start a PM that
belongs to the deferred-tracking group" (a NEW group membership created
by the phase transition), not "resume a PM that was previously held."
This is different from — and requires more source/capture evidence
than — the mechanism this probe's own original design anticipated;
The internal investigation log/the internal engine ledger's B.6
entry needs a significant rewrite to reflect this, not just a status
flip. A follow-up probe with a WIDER post-phase-exit window (to see
whether/when run 2's own deferred PM eventually resolves) and a
NonMission phase entered a SECOND time in the same run (to see whether
`Start_Deferred_PM` fires again, or only once per mission) would help
pin down the full mechanism before attempting an implementation.
