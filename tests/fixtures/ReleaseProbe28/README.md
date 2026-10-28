# ReleaseProbe28 — does a benign mid-wait interrupt (dependency Idle) landing during a frequency-based PM's capped wait cause a wasted extra fail-time draw once that PM later defers?

The internal investigation log §70 flagged a NEW residual in `tests/golden/
Phasing3` (surfacing only after §68/§69's fixes): `Attitude` draws a
fresh, unexplained fail-time value at the exact instant its own
frequency-based (`Scheduled_PM`, not trigger-based) PM becomes due and
immediately defers (`pmReqDefer`, resource pool exhausted) — real draws
nothing there at all. This probe isolates the precise mechanism found by
direct instrumentation of this port's own execution (see below), in a
minimal, fully deterministic shape.

## The mechanism this port's own code appears to have (traced directly, not guessed)

`block_lifecycle.py`'s frequency-based PM race (`_pm_race`, itself
explicitly Tier C — "not verified against real line-by-line captured
output") works like this: at kickoff, a block's own life draw
(`raw_fail_draw`) races against `PMFreq`. If PM wins, the block's own
`fail_time_left` gets capped to `PMFreq` (the wait it will actually run),
`pm_due=True` is decided, and the ORIGINAL raw draw is parked in a
separate `race_raw_life` variable so it can be restored later if this PM
attempt ends up deferred (`ObtainPMprereqs`'s own model: a deferred PM
resumes ordinary running with `FailTimeLeft` — i.e. the ORIGINAL,
pre-PM-cap life — left untouched, not reset).

But if a BENIGN interrupt (a dependency-driven `Idle`/recovery
round-trip, `Phase_Change`, etc.) lands WHILE that PM-capped wait is
still in progress, the code correctly preserves `pm_due` across the
round-trip (`benign_continue_pending`, a documented, deliberate
mechanism — see that flag's own docstring) — but `race_raw_life` is
reset to `None` on EVERY loop iteration UNCONDITIONALLY, with no
equivalent `benign_continue_pending` exception. So by the time the
PM-capped wait later naturally completes and the PM turns out to be
deferred (pool exhausted), the "restore the parked life" step
(`if race_raw_life is not None: fail_time_left = race_raw_life - ...`)
silently does nothing — `race_raw_life` is already `None` — leaving
`fail_time_left` at `None` too. The very next loop iteration then hits
the top-of-loop redraw gate (`if fail_time_left is None:`) and burns a
completely fresh, wasted `raw_fail_draw` — exactly the extra draw seen
in `Phasing3`.

Confirmed directly: this port's OWN execution on the fixture below shows
precisely this pattern — a second, different-valued draw for both PM
subscribers at the exact `PM_is_due`/`pmReqDefer` instant, matching
`Attitude`'s own shape in `Phasing3`.

## What this probe builds

Clone of `ReleaseProbe10`'s own three-block chain (`start→BlockS(3)→
BlockN(4)→BlockD(6)→j1→end`, `BlockS`/`BlockN` each with the SAME
already-proven `DepNum=-1`/`defDepStateIdle=1`/`DepIdle=100.00`
dependency wiring `ReleaseProbe22`'s own probes reuse), with:

1. **`BlockS`/`BlockN` converted from trigger-based to frequency-based
   PM** (`pmTriggered: 1→0`, `pmTrig: "T1"→"none"`) — `PMFreq=100.0` was
   already set in the template, unused until now.
2. **Both given a shared, resource** (`NumRes=1`, `#Res1pmRep=1`,
   `Resource1Name=Pool1`) they need to borrow for their own PM.
3. **A new block `Holder`** (id 7) that fails almost instantly
   (`FIXED(0.001)`) and repairs for 10,000,000 units (effectively
   forever, permanently occupying `Pool1`'s only unit) — wired as a
   fully separate parallel branch (`start→Holder→end`) that never
   touches the `BlockS`/`BlockN`/`BlockD` chain at all. This guarantees
   `BlockS`/`BlockN`'s own PM ALWAYS defers once due — the resource is
   exhausted from `t=0.001` onward, for the entire mission.
4. **`BlockD`'s own fail time** (`FParam1: 1.0→40.0`) — its repeating
   `Fail(40.0)`/`Repair(3.5)` cycle gives `BlockS`/`BlockN` TWO clean,
   isolated dependency `Idle`→`Good` round-trips (`t=40-43.5`,
   `t=83.5-87`) entirely WHILE their own 100-unit PM-capped wait is
   still in progress (`t=0` to `t≈107`) — deliberately offset (`40`,
   not `50`) so `BlockD`'s own SECOND cycle does NOT coincidentally land
   back on the PM-due instant itself (an earlier draft of this fixture
   had exactly that coincidence, at `t=50`/`FParam1=50.0`, which made the
   read ambiguous — moved to `40.0` for a clean, single-mechanism test).
5. **Mission length** (`TermCondition: 30.0→150.0`) — room for the
   `t≈107` PM-due instant plus its own aftermath.

`validate()`: clean (no warnings at all — the base template's own
pre-existing `start`-node `Out` mismatch is resolved by `Holder`'s own
new outgoing link, which happens to fill the gap that warning flagged).

## The question

At `t≈107` (`PMFreq=100` plus the two dependency pauses, `3.5+3.5=7`),
both `BlockS`/`BlockN` reach `PM_is_due`/`pmReqDefer` (resource
permanently exhausted). Does real draw ANYTHING for either block at
that exact instant?

- **No draw at all** (real proceeds silently from `pmReqDefer` straight
  back to ordinary running, matching this fixture's own two EARLIER
  dependency round-trips, which show no draws either) → confirms the
  hypothesis above precisely: `race_raw_life`'s own unconditional
  per-iteration reset (missing the `benign_continue_pending` exception
  `pm_due` already gets) is the root cause, and the fix belongs exactly
  there — give `race_raw_life` the SAME survival rule `pm_due` already
  has.
- **A draw appears, but matching this port's OWN kickoff value**
  (`603247.678897`/`1661685.615667` for run 1, etc. — i.e. real
  correctly reuses the ORIGINAL parked life) → same conclusion, just
  confirms the SPECIFIC missing piece is `race_raw_life`'s survival, not
  some entirely different, not-yet-considered mechanism.
- **A draw appears, but DIFFERENT from both this port's own kickoff AND
  its own (wrong) extra-draw value** → the hypothesis above is
  incomplete or wrong in some other way; would need the real event log's
  own exact line order at `t≈107` to say more.

## Predicted (this port)

All 3 runs, identical event-log SHAPE (only the drawn life values
differ):
```
0.000000    Block  BlockS  Good        InitializeStress=1.000000
0.000000    Block  BlockN  Good        InitializeStress=1.000000
40.000000   Block  BlockS  Idle        Dependence
40.000000   Block  BlockN  Idle        Dependence
43.500000   Block  BlockS  Good        DependenceStress=1.000000
43.500000   Block  BlockN  Good        DependenceStress=1.000000
83.500000   Block  BlockS  Idle        Dependence
83.500000   Block  BlockN  Idle        Dependence
87.000000   Block  BlockS  Good        DependenceStress=1.000000
87.000000   Block  BlockN  Good        DependenceStress=1.000000
107.000000  Block  BlockS  PM_is_due   Scheduled_PM
107.000000  Block  BlockN  PM_is_due   Scheduled_PM
107.000000  Block  BlockS  pmReqDefer  No_resource_in_stock-Pool1
107.000000  Block  BlockN  pmReqDefer  No_resource_in_stock-Pool1
127.000000  Block  BlockS  Idle        Dependence
127.000000  Block  BlockN  Idle        Dependence
130.500000  Block  BlockS  Good        DependenceStress=1.000000
130.500000  Block  BlockN  Good        DependenceStress=1.000000
150.000000  Block  BlockS  Done        End_sim
150.000000  Block  BlockN  Done        End_sim
```
`/sd` (run 1): kickoff draws `BlockS 603247.678897` / `BlockN
1661685.615667` at `t=0.0` — then a SECOND, DIFFERENT draw for BOTH at
`t=107.000000`: `BlockS 602040.895197` / `BlockN 1472151.338000`. This
port's own execution confirms the extra-draw pattern precisely, at the
exact `PM_is_due`/`pmReqDefer` instant, with no ambiguity from any other
same-instant activity.

## How to capture it

Same procedure as the other `ReleaseProbeN` fixtures — launch Raptor7
Student with `/sd`, open this file, Simulate (3 runs), read the event
log and `/sd` output for `BlockS`/`BlockN` at `t≈107` specifically (and
confirm the two earlier dependency round-trips at `t≈40-43.5`/`83.5-87`
show no draws either, matching this port's own prediction — if THEY
diverge too, that would point somewhere else entirely). Real-Raptor
report outputs land in `~/Documents/Raptor7/`
(`rpt_events_*` etc.); the `/sd` copy goes to this repo's root as
`simDetails_ReleaseProbe28.txt`.

## REAL ANSWER (2026-09-04 capture, `simDetails_ReleaseProbe28.txt` + `~/Documents/Raptor7/rpt_events_ReleaseProbe28.txt`)

**Neither of the three predicted branches — the real answer is more
surprising and more informative.** Real's own `/sd` output, both runs
identical:
```
0.000000  BlockS  603247.679362
0.000000  BlockN  1661685.616133
0.000000  BlockS  602040.895663
0.000000  BlockN  1472151.338466
0.000000  BlockS  333217.671133
0.000000  BlockN  2448859.910701
```
Real draws BOTH `BlockS`'s and `BlockN`'s "extra" value — and it's the
EXACT SAME VALUE this port's own (wrong) execution draws at `t=107`
(`602040.895663` vs this port's `602040.895197`, `1472151.338466` vs
this port's `1472151.338000` — identical modulo the usual fp32 noise
floor) — but real logs it at **`t=0.000000`**, not `t=107`. Real also
shows a THIRD value per block (`333217.671133`/`2448859.910701`) at
`t=0.000000` too — this port's own execution never draws this third one
in the same run at all (a SEPARATE divergence, see below).

**This settles the CENTRAL question precisely**: this port's own
`race_raw_life`/redraw-gate mechanism (§70.3's hypothesis) draws the
CORRECT VALUE — the RNG stream position/value itself is right — the
mechanism just consumes it at the WRONG SIMULATED TIME. Real appears to
draw a whole BATCH of values for a frequency-based PM block UP FRONT,
at kickoff (`t=0`), rather than lazily drawing each one only once it's
actually needed. This port's own lazy, on-demand drawing shifts the
SAME value to a much later simulated instant (`t=107`, whenever the
deferred-PM's own restore-attempt actually runs) — which is exactly
what desynchronizes every OTHER block's own shared-stream draws
in between (the actual, downstream symptom `sd_diff` was originally
catching in `Phasing3`).

**Consequence for §70's own Phasing3 divergence**: `Attitude`'s own
real capture shows only ONE draw at `t=0` (not two) — `Phasing3`'s own
shape doesn't reproduce this exact "two draws at kickoff" pattern, so
the eager-batch-draw hypothesis needs to explain WHY `Attitude`'s own
second draw is deferred all the way to `t=1508.21` while `BlockS`/
`BlockN`'s own second draw here is NOT deferred at all — a genuine,
still-open follow-up question (worth checking whether the NUMBER of
dependency round-trips before the PM-due instant matters, since this
fixture has TWO and `Attitude` has only ONE, or whether something about
`Attitude`'s own specific PM/resource configuration differs).

**Also open**: the THIRD draw real shows per block (`333217.671133`/
`2448859.910701`) that this port never draws in this run at all —
matches, in fact, this port's own `run 3` seed value for `BlockS`
(`333217.670667`, from an earlier probe run this port executed) —
plausibly real's own eager-batch-draw model pre-computes even MORE
values than this port anticipated (perhaps one per POSSIBLE outcome of
some LATER decision point this fixture's own mission doesn't reach).
Not yet chased further.

**Not fixed yet** — the eager-batch-draw mechanism needs to be
understood precisely (what triggers it, how many values it pre-draws,
in what order) before attempting a real fix; `docs/engine-gap-
analysis.md` §70 updated with this real capture's own findings and the
new open sub-question.

## RETRACTION + RESOLUTION (2026-09-05) — the "real answer" above was a 3-run append misread; §70's ORIGINAL hypothesis was right

The entire "eager batch" reading above is WITHDRAWN. Re-checked against
the raw capture files: the event log contains **THREE**
`Simulation_Terminate` groups (3 runs — the "both runs identical" claim
above was wrong), and the `/sd` dump's 6 `t=0` lines are those 3 runs'
KICKOFF PAIRS on one continuous stream, not one run's batch:

- lines 1-2 (`603247`/`1661685`) = run 1's kickoff pair (= this port's
  run-1 kickoff pair exactly),
- lines 3-4 (`602040`/`1472151`) = run 2's kickoff pair — and
  coincidentally (because the port's own run-1 wasted `t=107` redraws
  consumed exactly those stream positions) the same VALUES this port's
  run-1 `t=107` extras drew,
- lines 5-6 (`333217`/`2448859`) = run 3's kickoff pair (the "matches
  run 3's seed" observation above — of course it does; it IS run 3).

(The dump also carries a leftover different-model `BlockA/BlockB`
section at the top and a byte-identical repeat of the whole R28 section
— a second launch appending.)

**Real makes exactly ONE kickoff draw per frequency-PM block per run
and draws NOTHING at the `PM_is_due`/`pmReqDefer` instant** — which is
precisely what the ORIGINAL mechanism section above predicted, and
what this port does once `race_raw_life` (the restore point a benign
mid-wait interrupt was wrongly wiping) gets the same
`benign_continue_pending` survival `pm_due` already had. Fix landed
2026-09-05: this port's output is now byte-exact with the real capture
(6 draws, fp32 noise only), `Phasing3`'s first divergence moved
7521→13285 with draw counts now equal, and its mean
Availability/Dependability now match real's own final-results report.
Full trail: the internal investigation log §70.4.

**Standing lesson (added to the internal engine ledger B.4)**:
check the run count in the EVENT LOG, never infer it from the /sd
dump's apparent line grouping, before reading a multi-line
same-timestamp /sd block as one run's behavior.
