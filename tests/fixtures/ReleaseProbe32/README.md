# ReleaseProbe32 — does a genuinely-deferred `Start_Deferred_PM` (B.6) ever resolve, and does it fire again on a SECOND NonMission entry?

Direct follow-up to `ReleaseProbe31`'s own real capture. Read that
probe's own README in full first — it found `PMMisDefer=1` makes
entering a NonMission phase (`Phasing3`'s own one `Mission=0` phase,
`D301-360_Orbit`) a NEW, otherwise-unscheduled PM-due trigger point
(`"PM_is_due" "Start_Deferred_PM"`) for the affected block, completely
separate from its own ordinary trigger/frequency PM. That capture's own
narrow window (`t≈18-25`) caught the mechanism only ONCE per run, and
one run's own genuine deferral (`pmReqDefer`, resource exhausted) never
resolved before the window ended. Two things are still unknown: (1)
does a deferred `Start_Deferred_PM` instance ever resolve, and (2) does
it fire again on a SECOND NonMission entry in the same run, or only
once ever.

## What's new here vs `ReleaseProbe31`

Same starting point (`tests/golden/Phasing3/input.rbd`, cloned
wholesale — **no `PHASES`/`P NNN` edits**, matching the hard-won lesson
from this project's own crash history: `ReleaseProbe27`/`31` both
crashed real Raptor repeatedly when built from a hand-constructed
`PHASES` section; cloning a real, already-proven phased fixture
wholesale and touching only scalar fields is the only construction
method that's worked so far). Three scalar changes:

1. **`SolarP_500W`'s own `PMMisDefer`: `0→1`** (same as `ReleaseProbe31`).
2. **The `500W-PowerString` trigger's own distribution: `Dist=8`
   (`Param1=288.0`, a long random mean) → `FIXED(21.0)`** (same
   technique as `ReleaseProbe31`, for visibility into `SolarP_500W`'s
   own state) — this port's own run confirms it keeps re-firing roughly
   every 21 units for the WHOLE mission (an existing, pre-`ReleaseProbe31`
   quirk of this specific trigger config, not something either probe
   introduced), giving repeated opportunities to observe `SolarP_500W`.
3. **`Attitude`'s own fail/repair distributions made deterministic and
   RETIMED to GUARANTEE resource contention across TWO consecutive
   NonMission entries**: `FDist: 15 (Weibull) → FIXED(105.0)`,
   `RDist: 7 → FIXED(40.0)`. This port's own run (checked directly)
   shows `D301-360_Orbit` naturally landing at `t≈115-116` and again at
   `t≈139-140` in this specific fixture — `Attitude` failing at `t=105`
   and holding its own borrowed 3 (of 5) `Astronauts` units for a full
   40 units (`t≈107-147`, after its own `LDT`) spans BOTH windows
   entirely, reliably starving `SolarP_500W`'s own 1-unit need across
   both — confirmed directly: 4 of 5 runs checked show `SolarP_500W`
   genuinely `pmReqDefer`-ing (`"No_resource_in_stock-Astronauts"`) at
   its own ordinary trigger-due instants inside this window.

`validate()`: zero warnings or errors.

## What this port's own run actually does

This port doesn't implement B.6 at all (confirmed in `ReleaseProbe31`),
so it shows NOTHING special at either NonMission-phase boundary itself
— `SolarP_500W` just continues its ordinary `FIXED(21.0)`-trigger-driven
cycle, sometimes landing inside the Attitude-driven exhaustion window
(genuinely `pmReqDefer`-deferring, same ordinary mechanism `§68/§69`
already fixed) and sometimes not, with no distinction at all between an
ordinary deferral and a phase-boundary-adjacent one. Run 1 excerpt
(`Astronauts` exhausted `t≈105-119`, spanning past the first NonMission
entry at `t≈116.5`):
```
105.000010  SolarP_500W  PM_is_due    Triggered_PM_Due
105.000010  SolarP_500W  pmReqDefer   No_resource_in_stock-Astronauts
115.061487  SolarP_500W  Resources    Obtained_1_Resource(s)
115.061487  SolarP_500W  PM           beginning_PM
116.471823  System       Phase_change Begin_phase_D301-360_Orbit        <- lands WHILE SolarP_500W is already mid-PM
116.589885  SolarP_500W  PM_complete  MaintTime=1.528398
...
126.000010  SolarP_500W  PM_is_due    Triggered_PM_Due
126.000010  SolarP_500W  pmReqDefer   No_resource_in_stock-Astronauts   <- deferred again, ordinary mechanism
140.116027  System       Phase_change Begin_phase_D301-360_Orbit        <- SECOND NonMission entry, SolarP_500W still deferred
147.000010  SolarP_500W  PM_is_due    Triggered_PM_Due
147.000010  SolarP_500W  pmReqDefer   No_resource_in_stock-Astronauts   <- still deferred (Attitude's own 40-unit repair not done yet)
149.065025  SolarP_500W  Good         DependenceStress=0.500000         <- Attitude's own repair frees the pool shortly after
```
Not a confident prediction of what real does — this port's own current
model has no concept of `Start_Deferred_PM` at all, same caveat
`ReleaseProbe31`'s own README carries.

## The question

For each of the TWO `D301-360_Orbit` entries in this window (`t≈115-116`
and `t≈139-140`), does real show a `"PM_is_due" "Start_Deferred_PM"`
line for `SolarP_500W`, and:

1. **Does it fire at BOTH entries, or only the first?** — settles
   whether the phase-triggered PM opportunity is a once-per-mission
   event or genuinely repeats every NonMission entry.
2. **When the `Astronauts` pool is exhausted at that exact instant
   (`Attitude`'s own 40-unit repair spans both windows in this design),
   does real's own `Start_Deferred_PM` genuinely defer** (an explicit
   log line, ideally analogous to `pmReqDefer`'s own `"No_resource_in_
   stock-..."` message) **and, if so, does it EVER resolve** — either
   immediately once `Astronauts` frees up (same as an ordinary deferred
   PM), or specifically on the block's own NEXT phase-relevant boundary
   (phase exit, or the NEXT NonMission entry), or never at all within
   this window?
3. Does the SECOND `Start_Deferred_PM` (if any) behave any differently
   than the first — e.g. does it need to wait for the FIRST one to
   fully resolve first, or are they independent?

## Predicted (this port)

See "What this port's own run actually does" above — no `Start_
Deferred_PM` concept exists in this port's own current model at all;
`SolarP_500W`'s own ordinary trigger-driven PM cycle continues
unaffected by phase boundaries, deferring/resolving purely based on
ordinary `Astronauts` pool availability.

## Note: the mission is much longer than the `t≈100-150` window this README focuses on

`Phasing3`'s own `SYSTEM_DATA` uses `CycleSim` termination
(`TerminationType=CycleSim`, `TermCondition=100.000000`) — that `100`
means 100 CYCLES of the 5-phase table, not 100 time units. This port's
own run confirms the mission actually runs to `t≈2400` and revisits
`D301-360_Orbit` (and the `Astronauts`-exhaustion/`pmReqDefer` pattern
described above) roughly 100 times total, not just the two entries
`t≈115-116`/`t≈139-140` this README's own "What this port's own run
actually does"/"The question" sections focus on for a first, manageable
read. **If the first two entries don't fully settle the question**
(e.g. real shows something unexpected, or the deferred instance still
hasn't resolved by `t≈150`), there is FAR more data available in the
same single capture — read further into the run rather than assuming
the window described above is all there is.

## How to capture it

Same procedure as the other `ReleaseProbeN` fixtures — launch Raptor7
Student with `/sd`, open this file, Simulate (matching `Phasing3`'s own
10-run capture count), read the FULL event log for `SolarP_500W`/
`Attitude` — at minimum across `t≈100-150` (this window contains BOTH
target `D301-360_Orbit` entries and the full `Attitude`-driven
exhaustion cycle described above), but see the note above: the full
capture has ~100x more repeats of this same pattern if a wider read is
ever needed. Real-Raptor report outputs land in
`~/Documents/Raptor7/` (`rpt_events_*` etc.); the `/sd`
copy goes to this repo's root as `simDetails_ReleaseProbe32.txt`.

## REAL ANSWER (2026-09-04 capture, `~/Documents/Raptor7/rpt_events_ReleaseProbe32.txt`)

**Confirms `ReleaseProbe31`'s own mechanism and answers all three
questions**, with one genuinely complex wrinkle on question 2. Run 1's
own first 4 `D301-360_Orbit` entries:

```
t=19.688146  Begin_phase_D301-360_Orbit
t=19.688146  SolarP_500W  PM_is_due  Start_Deferred_PM
t=19.688146  SolarP_500W  PM         beginning_PM        <- resources available, begins immediately
t=20.876258  SolarP_500W  PM_complete ...                <- completes normally

t=43.492542  Begin_phase_D301-360_Orbit
t=43.492542  SolarP_500W  PM_is_due  Start_Deferred_PM
t=43.492542  SolarP_500W  pmReqDefer No_resource_in_stock-Astronauts   <- genuinely defers

t=67.637995  Begin_phase_D301-360_Orbit                  <- NO new Start_Deferred_PM here
t=69.437804  SolarP_500W  Resources  Obtained_1_Resource(s)   <- the t=43.49 deferral resolves...
t=69.437804  SolarP_500W  Idle       Dependence               <- ...but gets swept into a same-node
                                                                  cascade (BatteriesNiCd/SolarP_650W/
                                                                  BatteriesNiH all grab resources +
                                                                  begin PM at the SAME instant,
                                                                  500path node goes NodePM, dep-idling
                                                                  SolarP_500W before ITS OWN PM can
                                                                  start)
t=71.853786  SolarP_500W  Cut        Phase_Change             <- a NEW phase change (Mission begins
                                                                  again) CUTS SolarP_500W before its
                                                                  own deferred PM ever actually starts
                                                                  -- the deferral is abandoned, not
                                                                  completed

t=92.088534  Begin_phase_D301-360_Orbit
t=92.088534  SolarP_500W  PM_is_due  Start_Deferred_PM
t=92.088534  SolarP_500W  pmReqDefer No_resource_in_stock-Astronauts
t=92.614375  SolarP_500W  Resources  Obtained_1_Resource(s)
t=92.614375  SolarP_500W  PM         beginning_PM             <- THIS one completes cleanly
t=94.000684  SolarP_500W  PM_complete ...
```

**Answers**:
1. **Fires at EVERY `D301-360_Orbit` entry — but ONLY when no instance
   is already outstanding.** The `t=67.64` entry does NOT spawn a new
   `Start_Deferred_PM` because the `t=43.49` one is still pending —
   confirms at most ONE `Start_Deferred_PM` instance per block at a
   time, not independent per-entry firings.
2. **A genuinely-deferred instance CAN resolve (`t=43.49`→`t=69.44`,
   ~26 units later, same ordinary `pmReqDefer`→`Resources Obtained`
   mechanism ordinary deferred PM already uses) — but resolving the
   RESOURCE wait doesn't guarantee the PM itself ever runs.** In this
   run, that specific instance gets swept into an unrelated same-node
   dependency cascade (`BatteriesNiCd`/`SolarP_650W`/`BatteriesNiH` all
   racing for the same resource pool at the same instant, dep-idling
   `SolarP_500W` via its own node going `NodePM`) and is then abandoned
   outright by the NEXT ordinary phase change before it ever reaches
   `"PM beginning_PM"` — genuinely more complex than a simple hold-and-
   release, and NOT something this port could predict (it has no
   `Start_Deferred_PM` concept at all). The `t=92.09` instance, by
   contrast, resolves its resource wait AND completes cleanly to
   `PM_complete` with no intervening collision.
3. **The second instance (`t=43.49`) behaves differently from a plain
   repeat** — not because deferred instances have special resolution
   rules, but because it happens to collide with an unrelated same-node
   race and an intervening phase change; the underlying deferral/
   resolution mechanism itself (steps 1-3 above) looks identical to
   ordinary `pmReqDefer` in every run examined.

**B.6 remains unimplemented in this port** (confirmed no-op, per
`ReleaseProbe31`) — this capture doesn't change that, but sharpens the
mechanism: `Start_Deferred_PM` is a genuine per-block, at-most-one-
outstanding-at-a-time trigger fired by NonMission entry, whose
resolution follows ordinary resource-wait rules and is therefore
subject to the SAME same-node collision / phase-cut interactions any
other PM request is — not a special-cased mechanism of its own beyond
the trigger condition itself. The internal engine ledger B.6
updated with this refinement.
