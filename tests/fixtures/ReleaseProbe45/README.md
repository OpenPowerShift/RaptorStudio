# ReleaseProbe45 — A.4's residual, isolated (genuine re-failure while switching, zero flicker)

## What this tests

`ReleaseProbe44`'s own real capture confirmed A.4's flicker-triggered
interrupt bug and the fix for it (the internal engine ledger A.4) —
but that same probe's own remaining 2 spurious draws (t=2.005020,
t=4.010020) come from a DIFFERENT, unexplained cause: `LowGain` itself
fails again (a genuine, unrelated event) WHILE its own just-spawned
cold-standby switch is still mid-flight (not yet at its own natural
`switch_time` completion). By the fix's own rule (and by literal source
reading of `ReconfigureNode`'s own interrupt condition, unconditional on
any link-status change while `switchStatus` is in-progress), this SHOULD
interrupt — yet real showed no draw there either in `ReleaseProbe44`'s
own capture. That probe's own topology couldn't rule out the flicker
still being a hidden contributor (both sub-cases are present in the same
6-hour run), so this probe removes it entirely.

## Construction

Cloned from `HierarchyExample` (same base as `ReleaseProbe44`). Identical
to `ReleaseProbe44` in every respect EXCEPT: `LowGain`'s own `DepNum`
changed `-2` -> **`0`** (`DepType` stays `None`) — removes LowGain's own
`sysDepend` on the `end` node ENTIRELY, so its repair completion can
NEVER self-correct through Idle (the flicker `ReleaseProbe44` isolated
and this project's own A.4 fix already handles). `Thermal` (this SAME
base fixture) already has `DepNum=0`/`DepType=None`, confirming this is
an already-exercised, valid value — not hand-invented.

Everything else (both blocks' `FDist=19`/`FParam1`, repair timing,
`SYSTEM_DATA`'s `NumRuns=1`/`TermCondition=6.0`) is IDENTICAL to
`ReleaseProbe44`.

Verified: diffed field-by-field against `HierarchyExample`'s own clone —
only `LowGain`'s `FDist`/`FParam1`/`RDist`/`RParam1`/`DepNum` and
`HighGain`'s `FDist`/`FParam1`/`RDist`/`RParam1` changed (one MORE field
than `ReleaseProbe44`'s own diff: `DepNum`), `SYSTEM_DATA`'s
`NumRuns`/`TermCondition`; `rbd_builder.validate()` clean; this port's
own run confirms the flicker is GONE (`LowGain` goes straight
`RepHold->postLDT->Good` at `t=2.000020`/`t=4.005020`, no `Idle`
self-correct line at all, unlike `ReleaseProbe44`'s own event log).

## This port's own predicted output

Event log (`LowGain`/`HighGain`/`Comm` lines) is IDENTICAL in shape to
`ReleaseProbe44`'s own, MINUS the two `Idle`/re-`Good` self-correct
lines at `t=2.000020`/`t=4.005020` (confirming the flicker's own
removal, nothing else changed). `LowGain` still fails again at
`t=2.005020`/`t=4.010020` exactly as before (its own FIXED(0.005) fail
time, unrelated to the dependency removal).

`/sd` draws:
```
0.000000   link 8   0.025303      <- kickoff SetSwitchTime (auto, <0.98)
2.005020   link 8   0.822104      <- THE QUESTION: does real draw here?
4.010020   link 8   0.310979      <- THE QUESTION: does real draw here too?
```
(no draw at `t=2.000020`/`t=4.005020` this time — there's no flicker
left to trigger one; no draw at `t=5.000000`, `HighGain`'s own genuine
recovery, same reasoning as `ReleaseProbe44`)

## The question

Does real's own `simDetails.TXT` show `link 8` draws at `t=2.005020`
and/or `t=4.010020` (matching this port's own prediction — i.e. a
GENUINE re-failure while switching DOES interrupt+redraw), or does the
FIRST `link 8` draw after kickoff (`0.025303`) land somewhere else
entirely (or nowhere at all, matching `ReleaseProbe44`'s own real
capture)?

### What each answer means

- **Real matches this port's prediction** (draws at `2.005020` AND
  `4.010020`): would mean the FIX landed for A.4's flicker case is
  correct as far as it goes, and the REMAINING divergence in
  `ReleaseProbe44` is a DIFFERENT bug entirely — NOT the same "genuine
  re-failure while switching" mechanism, but something specific to that
  probe's own flicker-adjacent topology (worth re-examining what's
  actually different between the two runs at that point, e.g. the
  duplicate `"Node Comm Bad"` log line's own side effects).
- **Real draws at NEITHER instant** (matching `ReleaseProbe44`'s own
  zero-draw pattern exactly): CONFIRMS a genuine re-failure while
  switching does NOT interrupt/redraw in real MODSIM, cleanly isolated
  from the flicker case — a real, second sub-case of A.4, needing its
  own fix (or an extension covering both mechanisms with one rule) —
  and rules out any lingering flicker-contamination theory.
- **Mixed** (draws at one but not the other, or something else
  entirely): a new, unpredicted finding — re-derive from the actual
  real event log.

## REAL ANSWER (captured 2026-09-10, this same session — self-captured via
kwin-mcp desktop automation, not a user round-trip)

**Matches this port's own prediction exactly, at both instants.** Real's
`simDetails.TXT` (`tests/golden/ReleaseProbe45/legacy/simDetails_ReleaseProbe45.txt`) shows exactly:
```
0.000000  link 8  0.025303
1.000000  Attitude  0.779210
2.005020  link 8  0.822104
4.010020  link 8  0.310979
6.000000  Attitude  0.603136
```
Every predicted draw present, in order, at the exact predicted instants —
`LowGain`'s own genuine re-failure while its switch is still mid-flight
DOES interrupt and redraw normally in real MODSIM. (No separate real
event log was archived for this probe — with zero flicker present in
this fixture's own topology, the `/sd` draw sequence alone is fully
decisive for the question this probe was built to isolate; contrast
`ReleaseProbe44`, where the event log was needed to disambiguate a
flicker from a genuine change.)

This resolves the open question definitively: A.4's own mechanism is
**not** "any interrupt attempt against an in-flight switch is a no-op" —
it's narrower. Combined with `ReleaseProbe44`'s own real capture (zero
draws at either the flicker instants OR the two genuine-re-failure
instants, since in that fixture the SAME switch cycle sees the flicker
FIRST), the two captures together pin down the actual rule: **only the
first interrupt attempt against a given switch's in-flight lifetime can
ever have any effect** (redraw if the settled status differs from the
switch's own spawn-time snapshot, or a no-op if it doesn't) — every
LATER attempt against that SAME cycle is unconditionally a no-op,
regardless of whether ITS OWN trigger is a same-turn flicker or a
genuinely separate later event. This matches SIMSCRIPT II.5's own
literal `interrupt` statement semantics (pure pending-list removal, no
forced redelivery, no automatic resume — see the internal engine ledger
A.4's own entry for the full citation) once `_switch_handles`'s own
naturally single-use `.pop()` is read as the mechanism enforcing
"first attempt only," rather than trying to re-derive the rule from
each attempt's own trigger type.

**Fix landed same day** in `perform_switch_process`
(`raptor/core/engine/cold_standby.py`) — see the internal engine ledger
A.4's own entry for the final design, the two earlier designs it
superseded (and the real-capture evidence that ruled each of them out),
and the residual this fix's own `ignore_dep`-clearing correction leaves
open in richer, longer-running fixtures (`CS_Probe`, `CS_SwitchFail`).
Verified byte-exact (0 mismatches) against both this probe and
`ReleaseProbe44`.
