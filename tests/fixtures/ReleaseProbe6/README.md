# ReleaseProbe6 — deferred-PM pickup + PM-begin cascade probe (bisect step 3)

Extends ReleaseProbe5 with the last distinguishing feature of Capacity1
run 5's instant: the PM-begin cascade's mid-instant dependency dispatch.
BlockD (node-dep on the shared join, `DepNothing=50/DepIdle=50` — a
VISIBLE uniform dep draw) is Running when C's deferred-PM pickup grants
at t=25: C's PM-begin transitions the join → NodePM, whose Down dispatch
draws D's dependency-action draw mid-instant (mirroring Capacity1's S650
PM-begin drawing NiH/Bus_QR at 62.715126).

## Status

NOTE (2026-09-01): real Raptor fails to SIMULATE this fixture (opens
fine) — cause unknown; the user suspects a block with multiple inputs
(RAPTOR's exact error message needed). Our port runs it: B's Site-1
park at 5 (`Idle change_block_state`), C's deferred-PM pickup + PM-begin
at 25. The /sd readout: `[C PM-draw, B redraw]` — hopped.

**Root cause of the crash FOUND, 2026-09-03 (same session as
`ReleaseProbe12`'s own crash diagnosis)**: the "user suspects multiple
inputs" hunch was right, more precisely than expected — `join`'s own
`NODE_DATA` row declared `n=2` while THREE block links actually feed
it (`B`, `C`, and `D`, all wired to node 5). That mismatch between the
node's declared input count and its real wiring is the same crash
CLASS as `ReleaseProbe12`'s (`MODSIM Runtime error: Access Violation`
there too, most likely) -- an internal per-path array sized to `n`
gets a write/read at an index `n` was never told to expect. Fixed
`n: 2 -> 3` and also removed `BlockD`'s own redundant `pmTriggered`
config (it was ALSO independently subscribed to `ProbeTrig`, alongside
B -- not what this probe's own stated design describes; D is meant to
be a passive, node-dependent bystander only).

**BUT fixing the crash breaks this probe's own premise.** With `n`
corrected, `BlockB` no longer parks at t=5 at all -- it self-corrects
immediately (`PM_complete` -> `Good`, own run confirmed both with AND
without D's redundant PM removed) -- so there's no later recovery left
for anything to compete against at t=25. Tried bumping `join`'s `k`
from 1 to 2 (testing whether the "direct-Idle" threshold is `n-k`
rather than a flat `>1`) -- did NOT restore parking either. **This is
a genuine, NOT-YET-UNDERSTOOD divergence in our port's own downNum
behavior specifically for a 3-incoming-path node** (probes 3/5/7/8/9
+ Capacity1's own NiCd, all real-answered and all matching our port,
are all 2-path nodes) -- worth its own dedicated investigation later,
NOT patched around here. Left as-is (crash fixed, D's redundant PM
removed) rather than further reverse-engineered under this probe's own
now-invalidated premise.

**This probe's original goal — a third same-instant competitor that
ISN'T another trigger subscriber — is now pursued cleanly by
`ReleaseProbe14` instead**, which keeps `join` byte-identical to the
already-proven `ReleaseProbe3` (avoiding this exact 3-path complication
entirely) and adds the third competitor on its own fully separate
branch. Capture `ReleaseProbe14`, not this file, for that question.

## Readout (t=25 /sd order) — SUPERSEDED, see `ReleaseProbe14`

- `[D dep-draw, C PM-draw, B redraw]` (our port's ORIGINAL prediction,
  before the crash fix changed B's own behavior) → HOPPED.
- `[B redraw, D dep-draw, C PM-draw]` → SYNC.

These no longer apply as stated since B doesn't park in the fixed
file at all -- kept for the historical record of the original design
intent only.
